package httpapi

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/appdb"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// odataLake is a lake with one dataset of rice prices — three places over two
// days, one of them missing — behind an API that requires sign-in, and an
// API token for it.
func odataLake(t *testing.T) (http.Handler, string) {
	t.Helper()
	root := t.TempDir()
	obs := filepath.Join(root, "silver", "observations", "indicator_id=rice")
	geo := filepath.Join(root, "silver", "geography")
	datasets := filepath.Join(root, "silver", "datasets")
	indicators := filepath.Join(root, "silver", "indicators")
	for _, dir := range []string{obs, geo, datasets, indicators} {
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
			CAST(p AS DATE) AS period_start, CAST(p AS DATE) AS period_end, 'daily' AS temporal_resolution,
			'IDR/kg' AS unit, s AS status, true AS value_unambiguous, g AS geo_id,
			NULL::VARCHAR AS geo_name_raw, NULL::VARCHAR AS commodity_id,
			NULL::VARCHAR AS commodity_name_raw, NULL::VARCHAR AS category,
			'pihps' AS source_id, NULL::VARCHAR AS source_url, 'ds1' AS dataset_id,
			now() AS processed_at, 'x' || row_number() OVER () AS observation_id
		FROM (VALUES
			('ID-31', 9000.5, '2023-07-03', 'ok'), ('ID-32', 8000, '2023-07-03', 'ok'),
			('IDN', NULL, '2023-07-03', 'missing'),
			('ID-31', 11000, '2023-07-04', 'ok'), ('ID-32', 8100, '2023-07-04', 'ok'),
			('IDN', 9900, '2023-07-04', 'ok')
		) AS t(g, v, p, s)) TO '` + filepath.Join(obs, "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (SELECT * FROM (VALUES ('ID-31', 'DKI Jakarta', 'province'),
			('ID-32', 'Jawa Barat', 'province'), ('IDN', 'Indonesia', 'country'))
			AS t(geo_id, name, geo_type)) TO '` + filepath.Join(geo, "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (SELECT 'ds1' AS dataset_id, 'harga-pangan' AS slug, 'Harga pangan' AS title,
			NULL::VARCHAR AS description, ['prices'] AS tags)
			TO '` + filepath.Join(datasets, "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (SELECT 'rice' AS indicator_id, 'Beras' AS name, NULL::VARCHAR AS code,
			NULL::VARCHAR AS description, NULL::VARCHAR AS publisher, NULL::VARCHAR AS release,
			'beras' AS slug, 'ds1' AS dataset_id, []::VARCHAR[] AS tags)
			TO '` + filepath.Join(indicators, "part-0.parquet") + `' (FORMAT parquet)`,
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
	ctx := context.Background()
	user, err := accounts.PutUser(ctx, "reader@example.org", "a-long-enough-passphrase", "", auth.RoleResearcher)
	if err != nil {
		t.Fatal(err)
	}
	secret, _, err := accounts.CreateToken(ctx, user.ID, "excel", 0)
	if err != nil {
		t.Fatal(err)
	}

	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	api := New(&config.Config{Storage: cfg, Auth: config.Auth{Required: true}}, warehouse, nil,
		nil, accounts, nil, nil, nil, nil, log)
	return api.Routes(), secret
}

func odataGet(h http.Handler, target, token string, header ...string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodGet, target, nil)
	if token != "" {
		r.SetBasicAuth("anyone", token)
	}
	for i := 0; i+1 < len(header); i += 2 {
		r.Header.Set(header[i], header[i+1])
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

type odataPage struct {
	Context  string           `json:"@odata.context"`
	Count    *int64           `json:"@odata.count"`
	NextLink string           `json:"@odata.nextLink"`
	Value    []map[string]any `json:"value"`
}

func readPage(t *testing.T, w *httptest.ResponseRecorder) odataPage {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	var page odataPage
	decoder := json.NewDecoder(w.Body)
	decoder.UseNumber()
	if err := decoder.Decode(&page); err != nil {
		t.Fatal(err)
	}
	return page
}

func TestExcelIsAskedForCredentialsAndAcceptsATokenAsThePassword(t *testing.T) {
	h, token := odataLake(t)

	// Without credentials Excel has to be told which scheme to offer, or it
	// reports a failed connection instead of asking.
	w := odataGet(h, "/v1/odata/", "")
	if w.Code != http.StatusUnauthorized || !strings.HasPrefix(w.Header().Get("WWW-Authenticate"), "Basic") {
		t.Fatalf("anonymous = %d, challenge %q", w.Code, w.Header().Get("WWW-Authenticate"))
	}
	if w := odataGet(h, "/v1/odata/", "a-password-not-a-token"); w.Code != http.StatusUnauthorized {
		t.Fatalf("a password = %d, want 401", w.Code)
	}
	// Basic is the feed's only: anywhere else it would be a second way in.
	if w := odataGet(h, "/v1/datasets", token); w.Code != http.StatusUnauthorized {
		t.Fatalf("Basic on /v1/datasets = %d, want 401", w.Code)
	}

	page := readPage(t, odataGet(h, "/v1/odata/", token))
	names := map[string]bool{}
	for _, set := range page.Value {
		names[set["name"].(string)] = true
	}
	if !names["Datasets"] || !names["harga_pangan"] {
		t.Fatalf("service document lists %v", names)
	}
	if page.Context != "http://example.com/v1/odata/$metadata" {
		t.Errorf("context %q", page.Context)
	}
}

func TestTheMetadataTypesTheFigures(t *testing.T) {
	h, token := odataLake(t)
	w := odataGet(h, "/v1/odata/$metadata", token)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	body := w.Body.String()
	for _, want := range []string{
		`<EntitySet Name="harga_pangan" EntityType="Terusan.Observation"/>`,
		`<Property Name="Value" Type="Edm.Decimal" Scale="variable"/>`,
		`<Property Name="PeriodStart" Type="Edm.Date"/>`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metadata is missing %s", want)
		}
	}
	if w.Header().Get("OData-Version") != "4.0" {
		t.Errorf("OData-Version %q", w.Header().Get("OData-Version"))
	}
}

func TestADatasetIsServedAsTypedRowsWithMissingFiguresKeptMissing(t *testing.T) {
	h, token := odataLake(t)
	w := odataGet(h, "/v1/odata/harga_pangan?$count=true", token)
	raw := w.Body.String()
	page := readPage(t, w)
	if page.Count == nil || *page.Count != 6 || len(page.Value) != 6 {
		t.Fatalf("count %v, rows %d", page.Count, len(page.Value))
	}
	first := page.Value[0]
	// The decimal as digits, without the fixed scale's zeros, and a number
	// rather than a string so Excel reads it as one.
	if first["Value"] != json.Number("9000.5") || first["GeoName"] != "DKI Jakarta" ||
		first["PeriodStart"] != "2023-07-03" || first["IndicatorName"] != "Beras" {
		t.Fatalf("first row %v", first)
	}
	if !strings.Contains(raw, `"Value":9000.5`) {
		t.Errorf("value not written as a number: %s", raw)
	}
	var missing map[string]any
	for _, row := range page.Value {
		if row["Status"] == "missing" {
			missing = row
		}
	}
	if missing == nil || missing["Value"] != nil {
		t.Fatalf("missing figure read as %v", missing)
	}

	// IEEE754Compatible asks for the decimals as strings.
	page = readPage(t, odataGet(h, "/v1/odata/harga_pangan?$top=1", token,
		"Accept", "application/json;odata.metadata=minimal;IEEE754Compatible=true"))
	if page.Value[0]["Value"] != "9000.5" {
		t.Errorf("IEEE754Compatible value %#v", page.Value[0]["Value"])
	}
}

func TestPowerQueryFiltersAreAnswered(t *testing.T) {
	h, token := odataLake(t)
	for filter, want := range map[string]int{
		"GeoName eq 'Jawa Barat'":                            2,
		"Value gt 9000":                                      3,
		"Value eq 9000.5":                                    1,
		"Value eq null":                                      1,
		"PeriodStart ge 2023-07-04":                          3,
		"PeriodStart lt 2023-07-04T00:00:00Z":                3,
		"year(PeriodStart) eq 2023 and GeoType eq 'country'": 2,
		"contains(GeoName,'Jawa') or GeoID eq 'IDN'":         4,
		"not (GeoName eq 'Indonesia')":                       4,
		"GeoID in ('ID-31','ID-32')":                         4,
		"startswith(tolower(GeoName),'dki') eq true":         2,
		"Status ne 'ok'":                                     1,
	} {
		page := readPage(t, odataGet(h, "/v1/odata/harga_pangan?"+url.Values{"$filter": {filter}}.Encode(), token))
		if len(page.Value) != want {
			t.Errorf("%s: %d rows, want %d", filter, len(page.Value), want)
		}
	}

	page := readPage(t, odataGet(h, "/v1/odata/harga_pangan?$select=GeoName,Value&$orderby=Value%20desc&$top=2", token))
	if len(page.Value) != 2 || page.Value[0]["Value"] != json.Number("11000") || len(page.Value[0]) != 2 {
		t.Fatalf("selected and ordered %v", page.Value)
	}
	if !strings.HasSuffix(page.Context, "#harga_pangan(GeoName,Value)") {
		t.Errorf("context %q", page.Context)
	}

	if w := odataGet(h, "/v1/odata/harga_pangan?"+url.Values{"$filter": {"Value add 1 gt 2"}}.Encode(), token); w.Code != http.StatusNotImplemented {
		t.Errorf("arithmetic = %d, want 501", w.Code)
	}
	if w := odataGet(h, "/v1/odata/harga_pangan?$expand=Geo", token); w.Code != http.StatusNotImplemented {
		t.Errorf("$expand = %d, want 501", w.Code)
	}
	if w := odataGet(h, "/v1/odata/harga_pangan?"+url.Values{"$filter": {"Nope eq 1"}}.Encode(), token); w.Code != http.StatusBadRequest {
		t.Errorf("unknown property = %d, want 400", w.Code)
	}
}

func TestEveryRowArrivesOnceAcrossPages(t *testing.T) {
	h, token := odataLake(t)
	// Followed as Power Query follows them: blind, from link to link.
	for _, target := range []string{
		"/v1/odata/harga_pangan",
		"/v1/odata/harga_pangan?$orderby=GeoName",
		"/v1/odata/harga_pangan?$skip=1&$top=4",
	} {
		seen := map[string]int{}
		link := target
		for pages := 0; link != ""; pages++ {
			if pages > 10 {
				t.Fatalf("%s: pages never end", target)
			}
			page := readPage(t, odataGet(h, link, token, "Prefer", "odata.maxpagesize=2"))
			for _, row := range page.Value {
				seen[row["ObservationID"].(string)]++
			}
			link = strings.TrimPrefix(page.NextLink, "http://example.com")
		}
		want := 6
		if strings.Contains(target, "$top=4") {
			want = 4
		}
		if len(seen) != want {
			t.Errorf("%s: %d distinct rows, want %d", target, len(seen), want)
		}
		for id, n := range seen {
			if n != 1 {
				t.Errorf("%s: %s arrived %d times", target, id, n)
			}
		}
	}
}

func TestTheCatalogueNamesEachFeed(t *testing.T) {
	h, token := odataLake(t)
	page := readPage(t, odataGet(h, "/v1/odata/Datasets?"+url.Values{"$filter": {"Observations gt 5"}}.Encode(), token))
	if len(page.Value) != 1 {
		t.Fatalf("catalogue %v", page.Value)
	}
	row := page.Value[0]
	if row["Title"] != "Harga pangan" || row["EntitySet"] != "harga_pangan" ||
		row["FeedURL"] != "http://example.com/v1/odata/harga_pangan" || row["Observations"] != json.Number("6") {
		t.Fatalf("catalogue row %v", row)
	}
	if w := odataGet(h, "/v1/odata/harga_pangan/$count", token); w.Body.String() != "6" {
		t.Errorf("$count = %q", w.Body.String())
	}
}

func TestODataFiltersBindEveryValue(t *testing.T) {
	where, args, err := compileFilter("GeoName eq 'x'' OR 1=1 --'", odataObservationProps)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(where, "OR 1=1") || len(args) != 1 || args[0] != "x' OR 1=1 --" {
		t.Fatalf("where %q args %v", where, args)
	}
	for _, bad := range []string{
		"GeoName eq", "(GeoName eq 'a'", "GeoName", "GeoName eq 1", "contains(Value,'1')",
		strings.Repeat("(", 100) + "true" + strings.Repeat(")", 100),
	} {
		if _, _, err := compileFilter(bad, odataObservationProps); err == nil {
			t.Errorf("%q compiled", bad)
		}
	}
	var unsupported odataUnsupported
	if _, _, err := compileFilter("substringof('a',GeoName)", odataObservationProps); !errors.As(err, &unsupported) {
		t.Errorf("an unknown function should be unsupported, got %v", err)
	}
}

func TestEntitySetNamesAreIdentifiersAndStable(t *testing.T) {
	slug := func(s string) *string { return &s }
	datasets := []Dataset{
		{DatasetID: "a1", Slug: slug("adb-key-indicators")},
		{DatasetID: "b2", Slug: slug("adb_key.indicators")},
		{DatasetID: "c3", Slug: slug("2024-census")},
		{DatasetID: "d4"},
	}
	assignODataSets(datasets)
	want := []string{"adb_key_indicators", "adb_key_indicators_b2", "d_2024_census", "dataset_d4"}
	for i, d := range datasets {
		if d.ODataSet != want[i] {
			t.Errorf("%s named %q, want %q", d.DatasetID, d.ODataSet, want[i])
		}
	}
}
