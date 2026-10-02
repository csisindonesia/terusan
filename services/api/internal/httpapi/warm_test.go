package httpapi

import (
	"context"
	"testing"
)

func TestAStartDropsTheAnswersThePreviousBuildCached(t *testing.T) {
	// Written by the build before this one, in a shape this one may no longer
	// answer in. Warmed through, it would be held as the summary.
	held := newMemory()
	held.entries["terusan:v1:/v1/datasets"] = []byte(`{"data":[]}`)

	s := eventLake(t)
	s.cache = held
	s.WarmSummaries(context.Background())

	if len(held.entries) != 0 {
		t.Fatalf("the cache still holds %v", held.entries)
	}
}
