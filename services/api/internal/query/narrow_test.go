package query

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/storage"
)

// lake builds a throwaway lake with `partitions` observation partitions, each
// holding one (empty) Parquet file — the shape the normalizer produces, which
// is one directory per series.
func lake(t *testing.T, partitions int) *Warehouse {
	t.Helper()
	root := t.TempDir()
	for i := range partitions {
		dir := filepath.Join(root, "silver", "observations",
			"indicator_id=ind"+strconv.Itoa(i), "temporal_resolution=annual")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "part-0.parquet"), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cfg := &storage.Config{
		Profile: storage.ProfileLocal,
		Backend: storage.BackendLocal,
		Root:    root,
	}
	return &Warehouse{resolver: storage.NewResolver(cfg), meta: map[string]metaAnswer{}}
}

func TestNamingOnePartitionGlobsOnlyThatPartition(t *testing.T) {
	w := lake(t, 3)
	got, err := w.SourceIn(storage.LayerSilver, "observations", "indicator_id", []string{"ind1"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "indicator_id=ind1") {
		t.Errorf("did not narrow: %s", got)
	}
	for _, other := range []string{"ind0", "ind2"} {
		if strings.Contains(got, "indicator_id="+other) {
			t.Errorf("narrowed expression still names %s: %s", other, got)
		}
	}
}

func TestNamingSeveralPartitionsGlobsEachOfThem(t *testing.T) {
	w := lake(t, 4)
	got, err := w.SourceIn(storage.LayerSilver, "observations", "indicator_id",
		[]string{"ind0", "ind3"})
	if err != nil {
		t.Fatal(err)
	}
	for _, wanted := range []string{"indicator_id=ind0", "indicator_id=ind3"} {
		if !strings.Contains(got, wanted) {
			t.Errorf("expression is missing %s: %s", wanted, got)
		}
	}
	if strings.Contains(got, "indicator_id=ind1") {
		t.Errorf("expression names a partition nobody asked for: %s", got)
	}
}

// Narrowing is an optimisation, never a filter: with nothing named, the whole
// dataset is the correct source.
func TestNamingNothingReadsTheWholeDataset(t *testing.T) {
	w := lake(t, 3)
	got, err := w.SourceIn(storage.LayerSilver, "observations", "indicator_id", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(strings.Split(got, "'")[1], "observations/**/*.parquet") {
		t.Errorf("did not fall back to the whole dataset: %s", got)
	}
}

// read_parquet raises "No files found that match the pattern" for a directory
// that is not there, so a filter on a series this lake has never held would
// turn an empty result into a 500.
func TestAPartitionThatIsNotThereIsNeverNamed(t *testing.T) {
	w := lake(t, 2)
	got, err := w.SourceIn(storage.LayerSilver, "observations", "indicator_id",
		[]string{"ind0", "never-collected"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(got, "never-collected") {
		t.Errorf("named a partition that does not exist: %s", got)
	}
	if !strings.Contains(got, "indicator_id=ind0") {
		t.Errorf("dropped the partition that does exist: %s", got)
	}
}

// Every named partition absent. The caller's WHERE returns nothing either way,
// and the full glob is the only source that does not raise.
func TestAllPartitionsMissingFallsBackRatherThanRaising(t *testing.T) {
	w := lake(t, 2)
	got, err := w.SourceIn(storage.LayerSilver, "observations", "indicator_id",
		[]string{"nope", "also-nope"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(got, "nope") {
		t.Errorf("named a missing partition: %s", got)
	}
	if !strings.Contains(got, "observations/**/*.parquet") {
		t.Errorf("did not fall back to the whole dataset: %s", got)
	}
}

// Past the ceiling the list of patterns is its own cost, and a query naming
// that many series is heading for a full scan anyway.
func TestTooManyPartitionsReadsTheWholeDataset(t *testing.T) {
	w := lake(t, MaxNarrowedPartitions+5)
	values := make([]string, 0, MaxNarrowedPartitions+1)
	for i := range MaxNarrowedPartitions + 1 {
		values = append(values, "ind"+strconv.Itoa(i))
	}
	got, err := w.SourceIn(storage.LayerSilver, "observations", "indicator_id", values)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "observations/**/*.parquet") {
		t.Errorf("narrowed past the ceiling: %s", got)
	}
}

// ---- the existence fast path ---------------------------------------------

func TestHasParquetFindsAndMissesWithoutTheEngine(t *testing.T) {
	w := lake(t, 2)
	r := w.resolver

	if found, known := r.HasParquet(storage.LayerSilver, "observations"); !found || !known {
		t.Errorf("observations: found=%v known=%v, want true true", found, known)
	}
	if found, known := r.HasParquet(storage.LayerSilver, "commodities"); found || !known {
		t.Errorf("a dataset never written: found=%v known=%v, want false true", found, known)
	}
	if found, known := r.HasParquet(
		storage.LayerSilver, "observations", "indicator_id=ind0",
	); !found || !known {
		t.Errorf("a partition that exists: found=%v known=%v, want true true", found, known)
	}
}

// A bucket has no cheap equivalent, so the caller must fall through to asking
// the engine rather than believing a "no".
func TestObjectStorageReportsTheAnswerIsUnknown(t *testing.T) {
	cfg := &storage.Config{
		Profile:     storage.ProfileProduction,
		Backend:     storage.BackendS3,
		Root:        "s3://lake",
		S3AccessKey: "k",
		S3SecretKey: "s",
	}
	r := storage.NewResolver(cfg)
	if found, known := r.HasParquet(storage.LayerSilver, "observations"); found || known {
		t.Errorf("object storage: found=%v known=%v, want false false", found, known)
	}
}

// ---- the metadata memo ----------------------------------------------------

func TestTheShapeOfTheLakeIsRememberedRatherThanReAsked(t *testing.T) {
	w := lake(t, 1)
	calls := 0
	answer := func() bool { calls++; return true }

	for range 5 {
		if !w.remember("k", answer) {
			t.Fatal("remember returned false")
		}
	}
	if calls != 1 {
		t.Errorf("the answer was derived %d times, want 1", calls)
	}
}

func TestForgettingMakesTheNextRequestLookAgain(t *testing.T) {
	w := lake(t, 1)
	calls := 0
	answer := func() bool { calls++; return true }

	w.remember("k", answer)
	w.ForgetMetadata()
	w.remember("k", answer)

	if calls != 2 {
		t.Errorf("the answer was derived %d times, want 2", calls)
	}
}
