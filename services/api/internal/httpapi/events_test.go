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
	"testing"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// eventLake is a lake holding Lebaran 2024 — its holiday, its cuti bersama and
// the Ramadan before it — and Christmas 2024.
func eventLake(t *testing.T) *Server {
	t.Helper()
	root := t.TempDir()
	events := filepath.Join(root, "silver", "events")
	if err := os.MkdirAll(events, 0o755); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	// The columns the fixture does not vary are added as Silver writes them:
	// typed, and null where there is nothing to say.
	statement := `COPY (SELECT *, NULL::VARCHAR AS name_en, NULL::VARCHAR AS name_printed,
			NULL::VARCHAR AS calendar, 'IDN' AS geo_id, NULL::VARCHAR AS basis,
			'menpan-hari-libur' AS source_id, NULL::VARCHAR AS source_url
		FROM (VALUES
		('e1', 'holiday', 'ramadan', 'ramadan', 'Ramadan', 'Islam', 2024,
		 DATE '2024-03-11', DATE '2024-04-09', [DATE '2024-03-11', DATE '2024-04-09'], true),
		('e2', 'holiday', 'libur_nasional', 'idul_fitri', 'Idul Fitri', 'Islam', 2024,
		 DATE '2024-04-10', DATE '2024-04-11', [DATE '2024-04-10', DATE '2024-04-11'], false),
		('e3', 'holiday', 'cuti_bersama', 'idul_fitri', 'Idul Fitri', 'Islam', 2024,
		 DATE '2024-04-08', DATE '2024-04-15', [DATE '2024-04-08', DATE '2024-04-15'], false),
		('e4', 'holiday', 'libur_nasional', 'natal', 'Hari Raya Natal', 'Kristen', 2024,
		 DATE '2024-12-25', DATE '2024-12-25', [DATE '2024-12-25'], false)
		) AS t(event_id, category, kind, key, name, religion, year, start_date, end_date, dates, approximate))
		TO '` + filepath.Join(events, "part-0.parquet") + `' (FORMAT parquet)`
	if _, err := db.Exec(statement); err != nil {
		t.Fatalf("%v\n%s", err, statement)
	}

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

func eventKeys(events []Event) []string {
	out := make([]string, len(events))
	for n, e := range events {
		out[n] = e.EventID
	}
	return out
}

func TestEventsAreListedOldestFirst(t *testing.T) {
	s := eventLake(t)
	events, err := s.events(context.Background(), eventFilter{})
	if err != nil {
		t.Fatal(err)
	}
	if got := eventKeys(events); len(got) != 4 || got[0] != "e1" || got[3] != "e4" {
		t.Fatalf("events %v; want Ramadan first and Christmas last", got)
	}
	if !events[0].Approximate || events[0].Dates[0] != "2024-03-11" {
		t.Errorf("Ramadan read as %+v", events[0])
	}
}

func TestEventsOverlappingTheSpanAreInIt(t *testing.T) {
	s := eventLake(t)
	// The cuti bersama began on the 8th and runs into the span; Ramadan ended
	// the day before it.
	events, err := s.events(context.Background(), eventFilter{From: "2024-04-10", To: "2024-04-30"})
	if err != nil {
		t.Fatal(err)
	}
	if got := eventKeys(events); len(got) != 2 || got[0] != "e3" || got[1] != "e2" {
		t.Fatalf("events %v; want the cuti bersama and the holiday", got)
	}
}

func TestEventsNarrowByKeyAndReligion(t *testing.T) {
	s := eventLake(t)
	events, err := s.events(context.Background(), eventFilter{Keys: []string{"idul_fitri"}, Kinds: []string{"libur_nasional"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := eventKeys(events); len(got) != 1 || got[0] != "e2" {
		t.Fatalf("events %v; want Idul Fitri's national holiday", got)
	}
	events, err = s.events(context.Background(), eventFilter{Religions: []string{"Kristen"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := eventKeys(events); len(got) != 1 || got[0] != "e4" {
		t.Fatalf("events %v; want Christmas", got)
	}
}

func TestEventsRefuseAMalformedDate(t *testing.T) {
	s := eventLake(t)
	w := httptest.NewRecorder()
	s.handleEvents(w, httptest.NewRequest(http.MethodGet, "/v1/events?from=2024-13-40", nil))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d; want 400", w.Code)
	}
}

func TestEventsEndpointAnswersInTheUsualEnvelope(t *testing.T) {
	s := eventLake(t)
	w := httptest.NewRecorder()
	s.handleEvents(w, httptest.NewRequest(http.MethodGet, "/v1/events?key=natal", nil))
	var body struct {
		Data []Event `json:"data"`
		Meta Meta    `json:"meta"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Meta.Total != 1 || body.Data[0].Name != "Hari Raya Natal" || body.Data[0].GeoID != "IDN" {
		t.Fatalf("answer %s", w.Body.String())
	}
}

func TestALakeWithoutEventsHasNone(t *testing.T) {
	s := regulationLake(t)
	events, err := s.events(context.Background(), eventFilter{})
	if err != nil || len(events) != 0 {
		t.Fatalf("events %v, err %v; want none", events, err)
	}
}
