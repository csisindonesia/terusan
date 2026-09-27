package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// The rollup must answer exactly what the observations answer: every series,
// collection and commodity, with the same counts, spans and sources. Run
// against the local lake, which is the only place the two can be compared at
// a realistic size; skipped where there is none.
func TestTheRollupAnswersAsTheObservationsDo(t *testing.T) {
	if os.Getenv("LAKE_TESTS") == "" {
		t.Skip("scans the whole local lake; LAKE_TESTS=1 (make test-lake) runs it")
	}
	root := os.Getenv("STORAGE_ROOT")
	if root == "" {
		root = filepath.Join(storage.ProjectRoot(), ".data")
	}
	if _, err := os.Stat(filepath.Join(root, "silver", "observations")); err != nil {
		t.Skipf("no observations under %s", root)
	}
	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	resolver := storage.NewResolver(cfg)
	warehouse, err := query.Open(resolver, "4GB", 4)
	if err != nil {
		t.Fatal(err)
	}
	defer warehouse.Close()
	s := &Server{
		cfg: &config.Config{Storage: cfg}, storage: resolver, warehouse: warehouse,
		cache: cache.Nothing{}, log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	ctx := context.Background()

	commodities := func() string {
		recorder := httptest.NewRecorder()
		s.handleCommodities(recorder, httptest.NewRequest(http.MethodGet,
			"/v1/commodities?limit=10000&sort=-observations", nil))
		return recorder.Body.String()
	}

	// From the observations.
	series, err := s.indicatorRows(ctx, discardWriter{}, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	datasets, err := s.datasetRows(ctx)
	if err != nil {
		t.Fatal(err)
	}
	commodityList := commodities()

	// From the rollup.
	if err := s.buildRollup(ctx); err != nil {
		t.Fatal(err)
	}
	rolledSeries, err := s.indicatorRows(ctx, discardWriter{}, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	rolledDatasets, err := s.datasetRows(ctx)
	if err != nil {
		t.Fatal(err)
	}

	if len(series) != len(rolledSeries) {
		t.Fatalf("%d series from the observations, %d from the rollup", len(series), len(rolledSeries))
	}
	differ := 0
	for n := range series {
		a, _ := json.Marshal(series[n])
		b, _ := json.Marshal(rolledSeries[n])
		if string(a) != string(b) {
			if differ < 3 {
				t.Errorf("series differs:\n observations %s\n rollup       %s", a, b)
			}
			differ++
		}
	}
	if differ > 0 {
		t.Errorf("%d of %d series differ", differ, len(series))
	}
	if !reflect.DeepEqual(datasets, rolledDatasets) {
		t.Error("the collections differ between the observations and the rollup")
	}
	if rolled := commodities(); rolled != commodityList {
		t.Errorf("the commodities differ:\n observations %.300s\n rollup       %.300s", commodityList, rolled)
	}
}
