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
#pragma once

#include "model/transform.h"
#include "wasm/transform_probe.h"

#include <absl/container/flat_hash_map.h>

namespace transform {

struct processor_state_change {
    using state = model::transform_report::processor::state;

    std::optional<state> from;
    std::optional<state> to;
};

/** A per transform probe. */
class probe : public wasm::transform_probe {
public:
    void setup_metrics(ss::sstring);

    void increment_read_bytes(uint64_t bytes);
    void increment_write_bytes(uint64_t bytes);
    void increment_failure();
    void state_change(processor_state_change);
    void report_lag(int64_t delta);

private:
    uint64_t _read_bytes = 0;
    uint64_t _write_bytes = 0;
    uint64_t _failures = 0;
    uint64_t _lag = 0;
    absl::flat_hash_map<model::transform_report::processor::state, uint64_t>
      _processor_state;
};

} // namespace transform
