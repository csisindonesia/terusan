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
	if _, err := asked.SetStatus(ctx, first.ID, "ingested", "landed as esdm-publications"); err != nil {
		t.Fatalf("SetStatus: %v", err)
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

func TestSetStatusRefusesRubbish(t *testing.T) {
	ctx := context.Background()
	asked := store(t)
	created, err := asked.Create(ctx, request("Something"))
	if err != nil {
		t.Fatalf("Create: %v", err)
	}

	if _, err := asked.SetStatus(ctx, created.ID, "maybe", ""); err == nil {
		t.Fatal("SetStatus accepted a status nobody defined")
	} else if !strings.Contains(err.Error(), "status") {
		t.Errorf("unhelpful error: %v", err)
	}
	if _, err := asked.SetStatus(ctx, "not-an-id", "planned", ""); !errors.Is(err, ErrNotFound) {
		t.Fatalf("SetStatus on a missing row = %v, want ErrNotFound", err)
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
