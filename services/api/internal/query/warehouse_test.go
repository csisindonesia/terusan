package query

import (
	"fmt"
	"testing"
)

// The pipelines hash with the same function (pipelines/tests/
// test_observation_buckets.py holds these vectors too): if the two ever
// disagree the API reads the wrong bucket and a series goes missing.
func TestBucketsAreThePipelinesBuckets(t *testing.T) {
	for value, bucket := range map[string]int{"0kp69xys": 3, "39q7qs8c": 52, "a": 44, "": 197, "bps-inflasi": 69} {
		if got, want := BucketSegment(value), fmt.Sprintf("bucket=%03d", bucket); got != want {
			t.Errorf("BucketSegment(%q) = %s, want %s", value, got, want)
		}
	}
}

func TestOnlyTheObservationsIgnoreTheirPaths(t *testing.T) {
	if hivePartitioned("observations") || !hivePartitioned("regulation_sections") {
		t.Error("hive partitioning is off for the observations alone")
	}
}
