package showcase

import (
	"context"
	"errors"
	"path/filepath"
	"slices"
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
	s, err := New(db)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return s
}

func entry(title, kind string, datasets ...string) Entry {
	return Entry{
		Title:          title,
		Kind:           kind,
		URL:            "https://example.org/" + kind,
		Publisher:      "CSIS",
		Datasets:       datasets,
		CreatedBy:      "user-1",
		CreatedByEmail: "dev@example.org",
	}
}

func TestCreateKeepsDatasetsInOrderWithoutRepeats(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	created, err := s.Create(ctx, entry("Fiscal monitor", "dashboard", "b", "a", " b ", ""))
	if err != nil {
		t.Fatal(err)
	}
	got, err := s.Get(ctx, created.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(got.Datasets, []string{"b", "a"}) {
		t.Errorf("datasets %v; want [b a]", got.Datasets)
	}
	if got.Publisher != "CSIS" || got.CreatedByEmail != "dev@example.org" {
		t.Errorf("read back %+v", got)
	}
}

func TestCreateRefusesWhatCannotBeListed(t *testing.T) {
	s := store(t)
	for name, bad := range map[string]Entry{
		"no title": entry(" ", "article"),
		"no url":   {Title: "x", Kind: "article"},
		"kind":     entry("x", "podcast"),
		"date":     func() Entry { e := entry("x", "article"); e.PublishedOn = "22/07/2026"; return e }(),
	} {
		if _, err := s.Create(context.Background(), bad); err == nil {
			t.Errorf("%s: created; want an error", name)
		}
	}
}

// The dataset page asks which entries use it; the Discover page asks for one
// kind. Both are the same listing, narrowed.
func TestListNarrowsByDatasetAndKind(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	for _, e := range []Entry{
		entry("Fiscal monitor", "dashboard", "urn5cu46", "other"),
		entry("Violence op-ed", "article", "urn5cu46"),
		entry("Trade brief", "report", "trade"),
	} {
		if _, err := s.Create(ctx, e); err != nil {
			t.Fatal(err)
		}
	}

	using, err := s.List(ctx, Filter{DatasetID: "urn5cu46"})
	if err != nil {
		t.Fatal(err)
	}
	if len(using) != 2 {
		t.Fatalf("%d entries use urn5cu46; want 2", len(using))
	}
	for _, e := range using {
		if e.Title == "Fiscal monitor" && !slices.Equal(e.Datasets, []string{"urn5cu46", "other"}) {
			t.Errorf("listed datasets %v; want the entry's whole list", e.Datasets)
		}
	}

	articles, err := s.List(ctx, Filter{Kind: "article"})
	if err != nil {
		t.Fatal(err)
	}
	if len(articles) != 1 || articles[0].Title != "Violence op-ed" {
		t.Errorf("articles %+v", articles)
	}
}

func TestUpdateReplacesDatasetsAndKeepsTheAuthor(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	created, err := s.Create(ctx, entry("Fiscal monitor", "dashboard", "a", "b"))
	if err != nil {
		t.Fatal(err)
	}
	changed := entry("Fiscal monitor v2", "dashboard", "c")
	changed.CreatedByEmail = "somebody-else@example.org"
	updated, err := s.Update(ctx, created.ID, changed)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Title != "Fiscal monitor v2" || !slices.Equal(updated.Datasets, []string{"c"}) {
		t.Errorf("updated %+v", updated)
	}
	if updated.CreatedByEmail != "dev@example.org" {
		t.Errorf("author became %q; an edit must not change who added it", updated.CreatedByEmail)
	}
	if _, err := s.Update(ctx, "missing", changed); !errors.Is(err, ErrNotFound) {
		t.Errorf("update of a missing entry: %v; want ErrNotFound", err)
	}
}

func TestDeleteRemovesTheDatasetLinks(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	created, err := s.Create(ctx, entry("Fiscal monitor", "dashboard", "a"))
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Delete(ctx, created.ID); err != nil {
		t.Fatal(err)
	}
	using, err := s.List(ctx, Filter{DatasetID: "a"})
	if err != nil || len(using) != 0 {
		t.Errorf("after delete: %v, %v; want nothing", using, err)
	}
	if err := s.Delete(ctx, created.ID); !errors.Is(err, ErrNotFound) {
		t.Errorf("second delete: %v; want ErrNotFound", err)
	}
}
