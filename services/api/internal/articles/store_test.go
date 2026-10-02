package articles

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/csis/terusan/services/api/internal/appdb"
)

func store(t *testing.T) *Store {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	s, err := New(db)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func draft(slug string) Article {
	return Article{
		Slug: slug, Title: "Rice prices", CreatedBy: "u1", CreatedByEmail: "dev@example.org",
		Blocks: []Block{
			{Type: "text", Markdown: "Rice rose {{change indicator=a from=2023-07 to=2024-03}}."},
			{Type: "chart", Title: "Rice", Query: &Query{Indicator: []string{"a", "b"}, Geo: []string{"IDN"}}},
			{Type: "chart", Query: &Query{Indicator: []string{"b", "c"}}},
		},
	}
}

func TestCreateKeepsTheBlocksAndDefaultsToDraft(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	if _, err := s.Create(ctx, draft("rice")); err != nil {
		t.Fatal(err)
	}
	got, err := s.Get(ctx, "rice")
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != Draft || len(got.Blocks) != 3 || got.Blocks[1].Query.Geo[0] != "IDN" {
		t.Errorf("read back %+v", got)
	}
	if ids := got.Indicators(); len(ids) != 3 || ids[0] != "a" || ids[2] != "c" {
		t.Errorf("indicators %v; want a, b, c in order", ids)
	}
	if _, err := s.Create(ctx, draft("rice")); !errors.Is(err, ErrExists) {
		t.Errorf("second create: %v; want ErrExists", err)
	}
}

func TestDraftsAreListedOnlyWhenAsked(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	a := draft("one")
	b := draft("two")
	b.Status = Published
	for _, x := range []Article{a, b} {
		if _, err := s.Create(ctx, x); err != nil {
			t.Fatal(err)
		}
	}
	public, _ := s.List(ctx, false)
	all, _ := s.List(ctx, true)
	if len(public) != 1 || public[0].Slug != "two" || len(all) != 2 {
		t.Errorf("public %d, all %d", len(public), len(all))
	}
}

func TestUpdateCanRenameButNotOntoAnother(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	for _, slug := range []string{"one", "two"} {
		if _, err := s.Create(ctx, draft(slug)); err != nil {
			t.Fatal(err)
		}
	}
	moved := draft("three")
	if _, err := s.Update(ctx, "one", moved); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Get(ctx, "one"); !errors.Is(err, ErrNotFound) {
		t.Errorf("old slug still answers: %v", err)
	}
	if _, err := s.Update(ctx, "three", draft("two")); !errors.Is(err, ErrExists) {
		t.Errorf("rename onto an existing slug: %v; want ErrExists", err)
	}
}

func TestCleanRefusesBadBlocks(t *testing.T) {
	s := store(t)
	for name, blocks := range map[string][]Block{
		"empty text": {{Type: "text"}},
		"no series":  {{Type: "chart", Query: &Query{}}},
		"bad geo":    {{Type: "chart", Query: &Query{Indicator: []string{"a"}, Geo: []string{"x y"}}}},
		"bad period": {{Type: "chart", Query: &Query{Indicator: []string{"a"}, PeriodStart: "last year"}}},
		"bad embed":  {{Type: "embed", Article: "Rice!", Chart: "scatter"}},
		"unknown":    {{Type: "video"}},
		"many lines": {{Type: "chart", Query: &Query{Indicator: []string{"a"}, Members: 20}}},
	} {
		a := draft("x")
		a.Blocks = blocks
		if _, err := s.Create(context.Background(), a); err == nil {
			t.Errorf("%s: created; want an error", name)
		}
	}
	for _, slug := range []string{"new", "Rice", "rice--x", ""} {
		if _, err := s.Create(context.Background(), draft(slug)); err == nil {
			t.Errorf("slug %q: created; want an error", slug)
		}
	}
}

// Every kind of block keeps what it needs and drops what it does not, so a
// block turned from one type into another carries nothing of the old one.
func TestEachBlockTypeKeepsOnlyItsFields(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	a := draft("kinds")
	a.Blocks = []Block{
		{Type: "heading", Markdown: "Where the rain failed", Level: 7, Key: "stale"},
		{Type: "quote", Markdown: "Rice is politics.", Note: "A minister"},
		{Type: "callout", Markdown: "Figures are provisional."},
		{Type: "divider", Markdown: "left over"},
		{Type: "table", Header: true, Rows: [][]string{{"Year", "Change"}, {"2023", "{{change indicator=a from=2023-07 to=2024-03}}"}}},
		{Type: "footnote", Markdown: "PIHPS, traditional markets."},
		{Type: "regulation", Key: "perpres-2024-1", Markdown: "stale"},
		{Type: "dataset", Dataset: "uv7fqbgj"},
	}
	if _, err := s.Create(ctx, a); err != nil {
		t.Fatal(err)
	}
	got, _ := s.Get(ctx, "kinds")
	b := got.Blocks
	if b[0].Level != 2 || b[0].Key != "" {
		t.Errorf("heading kept %+v; want level 2 and no key", b[0])
	}
	if b[3].Markdown != "" || b[6].Markdown != "" {
		t.Errorf("divider or regulation kept markdown: %+v %+v", b[3], b[6])
	}
	texts := got.Texts()
	found := false
	for _, text := range texts {
		if text == "{{change indicator=a from=2023-07 to=2024-03}}" {
			found = true
		}
	}
	if !found {
		t.Errorf("a token in a table cell is not among the texts evaluated: %v", texts)
	}
}

func TestADashboardKeepsItsTiles(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	a := draft("monitor")
	a.Layout = LayoutDashboard
	a.Blocks = []Block{
		{Type: "stat", Title: "Rice, Jakarta", Markdown: "Rp{{latest indicator=a geo=ID-31}}", Note: "{{change indicator=a from=2023-07 to=2024-03}} since July 2023", Width: 1, Height: 1},
		{Type: "chart", Query: &Query{Indicator: []string{"a"}}, Width: 3, Height: 2},
	}
	if _, err := s.Create(ctx, a); err != nil {
		t.Fatal(err)
	}
	got, _ := s.Get(ctx, "monitor")
	if got.Layout != LayoutDashboard || got.Blocks[1].Width != 3 || got.Blocks[1].Height != 2 {
		t.Errorf("read back %+v", got)
	}
	// The stat's note is prose too, and its tokens are evaluated.
	if texts := got.Texts(); len(texts) < 3 {
		t.Errorf("texts %v; want the stat's figure, title and note", texts)
	}
	a.Blocks[0].Width = 5
	if _, err := s.Update(ctx, "monitor", a); err == nil {
		t.Error("a tile wider than the grid was saved")
	}
	a.Layout = "poster"
	a.Blocks[0].Width = 1
	if _, err := s.Update(ctx, "monitor", a); err == nil {
		t.Error("an unknown layout was saved")
	}
}

func TestAnArticleFromBeforeDashboardsIsAnArticle(t *testing.T) {
	ctx := context.Background()
	s := store(t)
	if _, err := s.Create(ctx, draft("old")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.db.ExecContext(ctx, `UPDATE articles SET layout = NULL`); err != nil {
		t.Fatal(err)
	}
	got, err := s.Get(ctx, "old")
	if err != nil || got.Layout != LayoutArticle {
		t.Errorf("layout %q, err %v; want article", got.Layout, err)
	}
}
