// Copyright 2023 Redpanda Data, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package redpanda_test

import (
	"github.com/rockwotj/redpanda/src/go/sdk"
)

// This example shows the basic usage of the package:
// This is a transform that does nothing but copies the same data to an new
// topic.
func Example_identityTransform() {
	// Make sure to register your callback and perform other setup in main
	redpanda.OnRecordWritten(identityTransform)
}

// This will be called for each record in the source topic.
//
// The output records returned will be written to the destination topic.
func identityTransform(e redpanda.WriteEvent) ([]redpanda.Record, error) {
	return []redpanda.Record{e.Record()}, nil
}