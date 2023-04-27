/*
 * Copyright 2023 Redpanda Data, Inc.
 *
 * Use of this software is governed by the Business Source License
 * included in the file licenses/BSL.md
 *
 * As of the Change Date specified in that file, in accordance with
 * the Business Source License, use of this software will be governed
 * by the Apache License, Version 2.0
 */

#include "resource_mgmt/memory_sampling.h"

#include "resource_mgmt/available_memory.h"
#include "ssx/future-util.h"
#include "vlog.h"

#include <seastar/core/condition-variable.hh>
#include <seastar/core/future.hh>
#include <seastar/core/memory.hh>
#include <seastar/core/sstring.hh>
#include <seastar/util/memory_diagnostics.hh>

#include <fmt/core.h>

constexpr std::string_view diagnostics_header() {
    return "Top-N alloc sites - size count stack:";
}

constexpr std::string_view allocation_site_format_str() { return "{} {} {}\n"; }

/// Put `top_n` allocation sites into the front of `allocation_sites`
static void top_n_allocation_sites(
  std::vector<ss::memory::allocation_site>& allocation_sites, size_t top_n) {
    std::partial_sort(
      allocation_sites.begin(),
      allocation_sites.begin() + top_n,
      allocation_sites.end(),
      [](const auto& lhs, const auto& rhs) { return lhs.size > rhs.size; });
}

ss::sstring memory_sampling::format_allocation_site(
  const ss::memory::allocation_site& alloc_site) {
    return fmt::format(
      allocation_site_format_str(),
      alloc_site.size,
      alloc_site.count,
      alloc_site.backtrace);
}

void memory_sampling::notify_of_reclaim() { _low_watermark_cond.signal(); }

ss::future<> memory_sampling::start_low_available_memory_logging() {
    // We want some periodic logging "on the way" to OOM. At the same time we
    // don't want to spam the logs. Hence, we periodically look at the available
    // memory low watermark (this is without the batch cache). If we see that we
    // have crossed the 10% and 20% marks we log the allocation sites. We stop
    // afterwards.

    size_t first_log_limit = _first_log_limit_fraction
                             * seastar::memory::stats().total_memory();
    size_t second_log_limit = _second_log_limit_fraction
                              * seastar::memory::stats().total_memory();
    size_t next_log_limit = first_log_limit;

    while (true) {
        try {
            co_await _low_watermark_cond.wait([&next_log_limit]() {
                auto current_low_water_mark
                  = resources::available_memory::local()
                      .available_low_water_mark();

                return current_low_water_mark <= next_log_limit;
            });
        } catch (const ss::broken_condition_variable&) {
            co_return;
        }

        auto allocation_sites = ss::memory::sampled_memory_profile();
        const size_t top_n = std::min(size_t(5), allocation_sites.size());
        top_n_allocation_sites(allocation_sites, top_n);

        vlog(
          _logger.info,
          "{} {}",
          diagnostics_header(),
          fmt::join(
            allocation_sites.begin(), allocation_sites.begin() + top_n, "|"));

        if (next_log_limit == first_log_limit) {
            next_log_limit = second_log_limit;
        } else {
            co_return;
        }
    }
}

memory_sampling::memory_sampling(ss::logger& logger)
  : _logger(logger)
  , _first_log_limit_fraction(0.2)
  , _second_log_limit_fraction(0.1) {}

memory_sampling::memory_sampling(
  ss::logger& logger,
  double first_log_limit_fraction,
  double second_log_limit_fraction)
  : _logger(logger)
  , _first_log_limit_fraction(first_log_limit_fraction)
  , _second_log_limit_fraction(second_log_limit_fraction) {}

void memory_sampling::start() {
    // We chose a sampling rate of ~3MB. From testing this has a very low
    // overhead of something like ~1%. We could still get away with something
    // smaller like 1MB and have acceptable overhead (~3%) but 3MB should be a
    // safer default for the initial rollout.
    ss::memory::set_heap_profiling_sampling_rate(3000037);

    ssx::spawn_with_gate(_low_watermark_gate, [this]() {
        return start_low_available_memory_logging();
    });
}

ss::future<> memory_sampling::stop() {
    _low_watermark_cond.broken();

    co_await _low_watermark_gate.close();
}
