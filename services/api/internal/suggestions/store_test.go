package suggestions

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/appdb"
)

func store(t *testing.T) *Store {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	asked, err := New(db)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return asked
}

func request(title string) Suggestion {
	return Suggestion{
		Title:            title,
		URL:              "https://www.esdm.go.id/id/publikasi/statistik",
		Cadence:          "monthly",
		Description:      "Monthly realisation by fuel type.",
		RequestedBy:      "user-1",
		RequestedByEmail: "dev@example.org",
	}
}

func TestCreate(t *testing.T) {
	ctx := context.Background()
	asked := store(t)

	created, err := asked.Create(ctx, request("  Fuel subsidy realisation  "))
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if created.Title != "Fuel subsidy realisation" {
		t.Errorf("Create stored %q — it was not trimmed", created.Title)
	}
	// Everything arrives open: the status is the queue's, not the asker's.
	if created.Status != "open" || created.ID == "" {
		t.Fatalf("Create returned %+v", created)
	}
	if created.RequestedByEmail != "dev@example.org" {
		t.Error("Create lost who asked, which is the one thing a follow-up needs")
	}
}

func TestCreateRefuses(t *testing.T) {
	ctx := context.Background()
	asked := store(t)

	cases := map[string]func(Suggestion) Suggestion{
		"no title":      func(s Suggestion) Suggestion { s.Title = "   "; return s },
		"no url":        func(s Suggestion) Suggestion { s.URL = ""; return s },
		"stray cadence": func(s Suggestion) Suggestion { s.Cadence = "fortnightly"; return s },
	}
	for name, mangle := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := asked.Create(ctx, mangle(request("Something"))); err == nil {
				t.Fatal("Create accepted it")
			}
		})
	}
}

func TestListPutsOpenFirst(t *testing.T) {
	ctx := context.Background()
	asked := store(t)

	first, err := asked.Create(ctx, request("Asked first"))
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if _, err := asked.Create(ctx, request("Asked second")); err != nil {
		t.Fatalf("Create: %v", err)
	}
	// The older one is dealt with; the list is read to find what is still
	// outstanding, so that is what comes first.
	if _, err := asked.Decide(ctx, first.ID, Decision{
		Status: "ingested", Note: "landed as esdm-publications",
	}); err != nil {
		t.Fatalf("Decide: %v", err)
	}

	list, err := asked.List(ctx, 0)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(list) != 2 {
		t.Fatalf("List = %d, want 2", len(list))
	}
	if list[0].Title != "Asked second" || list[0].Status != "open" {
		t.Fatalf("List put %q (%s) first", list[0].Title, list[0].Status)
	}
	if list[1].Note != "landed as esdm-publications" {
		t.Errorf("the note did not survive: %q", list[1].Note)
	}
}

func TestDecideRefusesRubbish(t *testing.T) {
	ctx := context.Background()
	asked := store(t)
	created, err := asked.Create(ctx, request("Something"))
	if err != nil {
		t.Fatalf("Create: %v", err)
	}

	cases := map[string]struct {
		decision Decision
		field    string
	}{
		"a status nobody defined":   {Decision{Status: "maybe"}, "status"},
		"declined without a reason": {Decision{Status: "declined"}, "reason"},
		"a reason nobody defined":   {Decision{Status: "declined", Reason: "meh"}, "reason"},
		"already collected, but where": {
			Decision{Status: "declined", Reason: "already-collected"}, "dataset_id",
		},
		"a reason for accepting": {Decision{Status: "planned", Reason: "other"}, "reason"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := asked.Decide(ctx, created.ID, c.decision)
			if err == nil {
				t.Fatal("Decide accepted it")
			}
			if !strings.Contains(err.Error(), c.field) {
				t.Errorf("unhelpful error: %v", err)
			}
		})
	}
	if _, err := asked.Decide(ctx, "not-an-id", Decision{Status: "planned"}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Decide on a missing row = %v, want ErrNotFound", err)
	}
}

func TestDeclinedAsAlreadyCollectedPointsAtTheData(t *testing.T) {
	ctx := context.Background()
	asked := store(t)
	created, err := asked.Create(ctx, request("Harga beras"))
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if created.ReviewedAt != nil {
		t.Fatal("a request nobody has looked at says it was reviewed")
	}

	declined, err := asked.Decide(ctx, created.ID, Decision{
		Status: "declined", Reason: "already-collected", DatasetID: "pihps-prices",
		Note: "Daily, by province.", ReviewedByEmail: "boss@example.org",
	})
	if err != nil {
		t.Fatalf("Decide: %v", err)
	}
	got, err := asked.Get(ctx, created.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.Reason != "already-collected" || got.DatasetID != "pihps-prices" {
		t.Fatalf("the answer did not survive: %+v", got)
	}
	if got.ReviewedByEmail != "boss@example.org" || got.ReviewedAt == nil {
		t.Errorf("who decided it was lost: %+v", got)
	}
	if declined.Note != "Daily, by province." {
		t.Errorf("note = %q", declined.Note)
	}

	// Reopened, it says nothing the last decision said: a stale "already
	// collected" beside an "open" would answer a question nobody has yet.
	reopened, err := asked.Decide(ctx, created.ID, Decision{Status: "open"})
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	if reopened.Reason != "" || reopened.DatasetID != "" || reopened.Note != "" {
		t.Errorf("reopening kept the last decision: %+v", reopened)
	}

	// A reason other than "already collected" carries no dataset.
	other, err := asked.Decide(ctx, created.ID, Decision{
		Status: "declined", Reason: "out-of-scope", DatasetID: "pihps-prices",
	})
	if err != nil {
		t.Fatalf("Decide: %v", err)
	}
	if other.DatasetID != "" {
		t.Errorf("out of scope, yet points at %q", other.DatasetID)
	}
}

func TestDelete(t *testing.T) {
	ctx := context.Background()
	asked := store(t)
	created, err := asked.Create(ctx, request("A duplicate"))
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if err := asked.Delete(ctx, created.ID); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := asked.Get(ctx, created.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Get after Delete = %v, want ErrNotFound", err)
	}
	if err := asked.Delete(ctx, created.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("second Delete = %v, want ErrNotFound", err)
	}
}
