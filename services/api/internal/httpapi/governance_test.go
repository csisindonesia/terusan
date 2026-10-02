package httpapi

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// governanceLake holds two series in one dataset, one of them with a missing
// figure and a place that did not resolve, and a Bronze that has read one
// document twice, at two parser versions.
func governanceLake(t *testing.T) *Server {
	t.Helper()
	root := t.TempDir()
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	write := func(dir, statement string) {
		t.Helper()
		path := filepath.Join(root, dir)
		if err := os.MkdirAll(path, 0o755); err != nil {
			t.Fatal(err)
		}
		full := "COPY (" + statement + ") TO '" + filepath.Join(path, "part-0.parquet") + "' (FORMAT parquet)"
		if _, err := db.Exec(full); err != nil {
			t.Fatalf("%v\n%s", err, full)
		}
	}

	write("silver/observations", `SELECT indicator_id, 'ds1' AS dataset_id, 'bps' AS source_id,
			period, 'monthly' AS temporal_resolution, 'percent' AS unit, status,
			geo_id, geo_name_raw, NULL::VARCHAR AS commodity_id, NULL::VARCHAR AS commodity_name_raw,
			document_id, 'https://bps.go.id/' || document_id AS source_url,
			'raw/' || document_id AS raw_path, 'h-' || document_id AS content_hash,
			TIMESTAMPTZ '2026-09-01 00:00:00+00' AS retrieved_at,
			TIMESTAMPTZ '2026-09-02 00:00:00+00' AS processed_at
		FROM (VALUES
		('cpi', '2026-01', 'ok', 'IDN', 'Indonesia', 'doc1'),
		('cpi', '2026-02', 'missing', 'IDN', 'Indonesia', 'doc1'),
		('cpi', '2026-03', 'ok', NULL, 'Kota Antah', 'doc2'),
		('gdp', '2026-01', 'ok', 'IDN', 'Indonesia', 'doc2')
		) AS t(indicator_id, period, status, geo_id, geo_name_raw, document_id)`)

	write("silver/indicators", `SELECT * FROM (VALUES
		('cpi', 'Consumer prices', 'prices', 'monthly', ['inflation', 'prices']),
		('gdp', 'Gross domestic product', NULL, 'quarterly', []::VARCHAR[])
		) AS t(indicator_id, name, category, frequency, tags)`)

	write("silver/datasets", `SELECT 'ds1' AS dataset_id, 'national-accounts' AS slug`)

	write("bronze/records", `SELECT 'bps' AS source_id, document_id, parser_version,
			source_url, content_hash, TIMESTAMPTZ '2026-09-01 00:00:00+00' AS retrieved_at
		FROM (VALUES
		('doc1', '25', 'https://bps.go.id/doc1', 'h1'),
		('doc1', '26', 'https://bps.go.id/doc1', 'h1b'),
		('doc2', '25', 'https://bps.go.id/doc2', 'h2')
		) AS t(document_id, parser_version, source_url, content_hash)`)

	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	resolver := storage.NewResolver(cfg)
	warehouse, err := query.Open(resolver, "1GB", 1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { warehouse.Close() })
	return &Server{
		cfg:       &config.Config{Storage: cfg},
		storage:   resolver,
		warehouse: warehouse,
		log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

// serve calls a handler and decodes its `data`.
func serve[T any](t *testing.T, handler http.HandlerFunc, target string, pattern string) T {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("GET "+pattern, handler)
	recorder := httptest.NewRecorder()
	mux.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, target, nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("%s: status %d: %s", target, recorder.Code, recorder.Body.String())
	}
	var envelope struct {
		Data T `json:"data"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	return envelope.Data
}

func TestQualityCountsStatusesAndUnresolvedPlaces(t *testing.T) {
	s := governanceLake(t)
	// Once off the observations, once off the rollup: the two must agree.
	for _, rolled := range []bool{false, true} {
		if rolled {
			if err := s.buildRollup(context.Background()); err != nil {
				t.Fatal(err)
			}
		}
		series := serve[[]SeriesQuality](t, s.handleQuality, "/v1/quality", "/v1/quality")
		if len(series) != 2 {
			t.Fatalf("rolled=%v: series = %+v", rolled, series)
		}
		cpi := series[0]
		if cpi.IndicatorID != "cpi" || cpi.Figures != 3 || cpi.Statuses["missing"] != 1 ||
			cpi.Statuses["ok"] != 2 || cpi.Unplaced != 1 {
			t.Errorf("rolled=%v: cpi = %+v", rolled, cpi)
		}
		if cpi.Name == nil || *cpi.Name != "Consumer prices" {
			t.Errorf("rolled=%v: cpi name = %v", rolled, cpi.Name)
		}
	}
}

func TestClassificationsCountTermsAndTheUnclassified(t *testing.T) {
	s := governanceLake(t)
	schemes := serve[[]Classification](t, s.handleClassifications, "/v1/classifications", "/v1/classifications")
	byScheme := map[string]Classification{}
	for _, scheme := range schemes {
		byScheme[scheme.Scheme] = scheme
	}

	category := byScheme["indicator.category"]
	if len(category.Values) != 1 || category.Values[0].Value != "prices" || category.Unclassified != 1 {
		t.Errorf("category = %+v", category)
	}
	tags := byScheme["indicator.tags"]
	if len(tags.Values) != 2 || tags.Unclassified != 1 {
		t.Errorf("tags = %+v", tags)
	}
	status := byScheme["observation.status"]
	if len(status.Values) != 2 || status.Values[0].Value != "ok" || status.Values[0].Count != 3 {
		t.Errorf("status = %+v", status)
	}
	// A table the lake does not hold is left out rather than failing.
	if _, ok := byScheme["source.category"]; ok {
		t.Error("listed a vocabulary from a table that does not exist")
	}
}

func TestLineageTracesADatasetToItsDocuments(t *testing.T) {
	s := governanceLake(t)
	lineage := serve[Lineage](t, s.handleLineage, "/v1/lineage/ds1", "/v1/lineage/{id}")
	if lineage.SourceID == nil || *lineage.SourceID != "bps" {
		t.Fatalf("source = %v", lineage.SourceID)
	}
	if lineage.Slug == nil || *lineage.Slug != "national-accounts" {
		t.Errorf("slug = %v", lineage.Slug)
	}
	if lineage.DocumentsTotal != 2 || len(lineage.Documents) != 2 {
		t.Errorf("documents = %+v", lineage.Documents)
	}
	for _, document := range lineage.Documents {
		if document.DocumentID == "doc2" && !slices.Equal(document.Series, []string{"cpi", "gdp"}) {
			t.Errorf("doc2 feeds %v", document.Series)
		}
	}
	if len(lineage.Series) != 2 || lineage.Series[0].Name == nil || *lineage.Series[0].Name != "Consumer prices" {
		t.Errorf("series = %+v", lineage.Series)
	}
	if len(lineage.Extractions) != 2 || lineage.Extractions[0].ParserVersion != "26" {
		t.Errorf("extractions = %+v", lineage.Extractions)
	}
}

func TestLineageOfAnUnknownDatasetIsNotFound(t *testing.T) {
	s := governanceLake(t)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/lineage/{id}", s.handleLineage)
	recorder := httptest.NewRecorder()
	mux.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/v1/lineage/nope", nil))
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("status = %d", recorder.Code)
	}
}

func TestVersionsFindDocumentsReadByAnOlderParser(t *testing.T) {
	s := governanceLake(t)
	versions := serve[Versions](t, s.handleVersions, "/v1/versions", "/v1/versions")
	if versions.LatestParser == nil || *versions.LatestParser != "26" {
		t.Fatalf("latest = %v", versions.LatestParser)
	}
	if len(versions.Sources) != 1 {
		t.Fatalf("sources = %+v", versions.Sources)
	}
	bps := versions.Sources[0]
	// doc1 was re-read at 26, doc2 only ever at 25; doc1's reading at 25 is
	// the one row a newer extraction replaced.
	if bps.Documents != 2 || bps.Current != 1 || bps.Behind != 1 || bps.SupersededRows != 1 {
		t.Errorf("bps = %+v", bps)
	}
	if !slices.Equal(bps.Versions, []string{"26", "25"}) {
		t.Errorf("versions = %v", bps.Versions)
	}
	if versions.RepublishedTotal != 1 || versions.Republished[0].SourceURL != "https://bps.go.id/doc1" {
		t.Errorf("republished = %+v", versions.Republished)
	}
}

func TestSortVersionsIsNumericNewestFirst(t *testing.T) {
	got := sortVersions([]string{"9", "26", "beta", "3", "12"})
	if want := []string{"26", "12", "9", "3", "beta"}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}
