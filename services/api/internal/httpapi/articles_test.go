package httpapi

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/appdb"
	"github.com/csis/terusan/services/api/internal/articles"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/showcase"
	"github.com/csis/terusan/services/api/internal/storage"
)

// articleLake is a lake with one rice series across two provinces and the
// national row, and an API with an admin signed in.
//
// ID-31 averages 10,000 in July 2023 (two days, 9,000 and 11,000) and 12,000
// in March 2024. ID-32 goes 8,000 to 8,800. The national row goes 9,000 to
// 10,400 and must not count as a province.
func articleLake(t *testing.T) (http.Handler, string) {
	t.Helper()
	root := t.TempDir()
	obs := filepath.Join(root, "silver", "observations", "indicator_id=rice")
	geo := filepath.Join(root, "silver", "geography")
	for _, dir := range []string{obs, geo} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, statement := range []string{
		`COPY (SELECT 'rice' AS indicator_id, CAST(v AS DECIMAL(38,9)) AS value, p AS period,
			CAST(d AS DATE) AS period_start, CAST(d AS DATE) AS period_end, 'daily' AS temporal_resolution,
			'IDR/kg' AS unit, 'ok' AS status, true AS value_unambiguous, g AS geo_id,
			NULL::VARCHAR AS geo_name_raw, NULL::VARCHAR AS commodity_id, NULL::VARCHAR AS category,
			'pihps' AS source_id, NULL::VARCHAR AS source_url, 'ds1' AS dataset_id,
			'x' || row_number() OVER () AS observation_id
		FROM (VALUES
			('ID-31', 9000, '2023-07-03', '2023-07-03'), ('ID-31', 11000, '2023-07-04', '2023-07-04'),
			('ID-31', 12000, '2024-03-01', '2024-03-01'),
			('ID-32', 8000, '2023-07-03', '2023-07-03'), ('ID-32', 8800, '2024-03-01', '2024-03-01'),
			('IDN', 9000, '2023-07-03', '2023-07-03'), ('IDN', 10400, '2024-03-01', '2024-03-01')
		) AS t(g, v, p, d)) TO '` + filepath.Join(obs, "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (SELECT * FROM (VALUES ('ID-31', 'DKI Jakarta', 'province'),
			('ID-32', 'Jawa Barat', 'province'), ('IDN', 'Indonesia', 'country'))
			AS t(geo_id, name, geo_type)) TO '` + filepath.Join(geo, "part-0.parquet") + `' (FORMAT parquet)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			t.Fatalf("%v\n%s", err, statement)
		}
	}

	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	warehouse, err := query.Open(storage.NewResolver(cfg), "1GB", 1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { warehouse.Close() })

	app, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { app.Close() })
	accounts, _ := auth.New(app, auth.Config{Iterations: 1000})
	shown, _ := showcase.New(app)
	written, _ := articles.New(app)
	ctx := context.Background()
	if _, err := accounts.PutUser(ctx, "boss@example.org", "a-long-enough-passphrase", "", auth.RoleAdmin); err != nil {
		t.Fatal(err)
	}
	cookie, _, _ := accounts.Login(ctx, "boss@example.org", "a-long-enough-passphrase", "test", false)

	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	api := New(&config.Config{Storage: cfg, Auth: config.Auth{Required: false}}, warehouse, nil,
		nil, accounts, nil, shown, written, nil, log)
	return api.Routes(), cookie
}

func figuresFor(t *testing.T, h http.Handler, cookie string, text string) map[string]ArticleFigure {
	t.Helper()
	body, _ := json.Marshal(map[string][]string{"texts": {text}})
	w := send(h, http.MethodPost, "/v1/articles/figures", cookie, string(body))
	if w.Code != http.StatusOK {
		t.Fatalf("figures = %d: %s", w.Code, w.Body)
	}
	var out struct{ Data map[string]ArticleFigure }
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out.Data
}

// Each measure, against figures worked out by hand above.
func TestFigureTokensAreEvaluatedAgainstTheLake(t *testing.T) {
	h, cookie := articleLake(t)
	for token, want := range map[string]string{
		"{{change indicator=rice geo=ID-31 from=2023-07 to=2024-03}}": "+20.0%",
		"{{value indicator=rice geo=ID-31 period=2023-07}}":           "10,000",
		"{{value indicator=rice geo=ID-32 period=2023-07 digits=1}}":  "8,000.0",
		"{{median indicator=rice period=2024-03}}":                    "10,400",
		"{{max indicator=rice period=2023-07}}":                       "10,000",
		"{{mean indicator=rice period=2024 geo_type=country}}":        "10,400",
		"{{rank indicator=rice geo=ID-32 period=2024-03}}":            "2nd of 2",
		"{{rank indicator=rice geo=ID-32 period=2024-03 order=asc}}":  "1st of 2",
		"{{latest indicator=rice geo=IDN}}":                           "10,400",
	} {
		got := figuresFor(t, h, cookie, "x "+token)[token]
		if got.Display != want || got.Error != "" {
			t.Errorf("%s = %q (error %q), want %q", token, got.Display, got.Error, want)
		}
	}
}

// A figure that cannot be given says why, instead of reading as zero.
func TestAFigureWithoutDataSaysSo(t *testing.T) {
	h, cookie := articleLake(t)
	for _, token := range []string{
		"{{value indicator=rice geo=ID-31 period=2020}}",
		"{{value indicator=absent period=2023}}",
		"{{rank indicator=rice geo=ID-99 period=2024-03}}",
		"{{sum indicator=rice period=2024}}",
	} {
		got := figuresFor(t, h, cookie, token)[token]
		if got.Error == "" || got.Display != "" {
			t.Errorf("%s = %+v; want an error and no figure", token, got)
		}
	}
}

func TestPublishingFilesTheArticleOnThePortal(t *testing.T) {
	h, cookie := articleLake(t)
	article := `{"slug":"rice-note","title":"Rice note","dek":"Short.","status":"draft",
		"blocks":[{"type":"text","markdown":"Up {{change indicator=rice geo=ID-31 from=2023-07 to=2024-03}}."},
		          {"type":"chart","title":"Rice","query":{"indicator":["rice"],"geo":["ID-31"]}}]}`
	if w := send(h, http.MethodPost, "/v1/articles", cookie, article); w.Code != http.StatusCreated {
		t.Fatalf("create = %d: %s", w.Code, w.Body)
	}

	// A draft is not there for a reader, and is not on the Portal.
	if got := send(h, http.MethodGet, "/v1/articles/rice-note", "", "").Code; got != http.StatusNotFound {
		t.Errorf("anonymous read of a draft = %d, want 404", got)
	}
	if w := send(h, http.MethodGet, "/v1/showcase", "", ""); strings.Contains(w.Body.String(), "rice-note") {
		t.Errorf("a draft was filed on the portal: %s", w.Body)
	}

	published := strings.Replace(article, `"status":"draft"`, `"status":"published"`, 1)
	w := send(h, http.MethodPatch, "/v1/articles/rice-note", cookie, published)
	if w.Code != http.StatusOK {
		t.Fatalf("publish = %d: %s", w.Code, w.Body)
	}
	var read struct{ Data ArticleResponse }
	w = send(h, http.MethodGet, "/v1/articles/rice-note", "", "")
	if err := json.Unmarshal(w.Body.Bytes(), &read); err != nil || w.Code != http.StatusOK {
		t.Fatalf("read = %d: %s", w.Code, w.Body)
	}
	if got := read.Data.Figures["{{change indicator=rice geo=ID-31 from=2023-07 to=2024-03}}"].Display; got != "+20.0%" {
		t.Errorf("figure in the published article = %q", got)
	}
	if w := send(h, http.MethodGet, "/v1/showcase", "", ""); !strings.Contains(w.Body.String(), `"/portal/rice-note"`) {
		t.Errorf("published article not on the portal: %s", w.Body)
	}

	// Taken back to draft, it leaves the Portal; deleted, it is gone.
	if w := send(h, http.MethodPatch, "/v1/articles/rice-note", cookie, article); w.Code != http.StatusOK {
		t.Fatalf("unpublish = %d: %s", w.Code, w.Body)
	}
	if w := send(h, http.MethodGet, "/v1/showcase", "", ""); strings.Contains(w.Body.String(), "rice-note") {
		t.Errorf("unpublished article still on the portal: %s", w.Body)
	}
	if got := send(h, http.MethodDelete, "/v1/articles/rice-note", cookie, "").Code; got != http.StatusOK {
		t.Errorf("delete = %d", got)
	}
}
