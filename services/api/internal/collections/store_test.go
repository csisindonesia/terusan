package collections

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/csis/terusan/services/api/internal/appdb"
)

func open(t *testing.T, writable bool) *Store {
	t.Helper()
	return openAt(t, filepath.Join(t.TempDir(), "shelf.duckdb"), writable)
}

func openAt(t *testing.T, path string, writable bool) *Store {
	t.Helper()
	db, err := appdb.Open(path)
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	store, err := New(db, writable)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return store
}

func folder(id, name string, items ...Item) Collection {
	now := time.Now().UTC()
	return Collection{ID: id, Name: name, CreatedAt: now, UpdatedAt: now, Items: items}
}

func item(kind, id, label string) Item {
	return Item{Kind: kind, ID: id, Label: label, AddedAt: time.Now().UTC()}
}

func TestCreateAndGet(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)

	want := folder("col-1", "Inflation",
		item("indicator", "6mxpn8s3", "Composite CPI of 150 Cities"),
		item("commodity", "Beras", "Beras"))
	if _, err := store.Create(ctx, want); err != nil {
		t.Fatalf("Create: %v", err)
	}

	got, err := store.Get(ctx, "col-1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.Name != "Inflation" || len(got.Items) != 2 {
		t.Fatalf("Get = %q with %d items, want Inflation with 2", got.Name, len(got.Items))
	}

	// A second create under the same id is a conflict, not an overwrite: the
	// caller supplied the id, and silently replacing a folder is data loss.
	if _, err := store.Create(ctx, want); err == nil {
		t.Error("Create with a used id returned no error")
	}
}

func TestGetMissing(t *testing.T) {
	_, err := open(t, true).Get(context.Background(), "col-nothing")
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("Get = %v, want ErrNotFound", err)
	}
}

func TestAddItemsSkipsWhatIsHeld(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)
	if _, err := store.Create(ctx, folder("col-1", "Trade",
		item("indicator", "aur11223", "Exports of Goods"))); err != nil {
		t.Fatalf("Create: %v", err)
	}

	updated, added, err := store.AddItems(ctx, "col-1", []Item{
		item("indicator", "aur11223", "Exports of Goods"), // already filed
		item("indicator", "vuj0ojxd", "Imports of Goods"),
	})
	if err != nil {
		t.Fatalf("AddItems: %v", err)
	}
	if added != 1 {
		t.Errorf("added = %d, want 1", added)
	}
	if len(updated.Items) != 2 {
		t.Errorf("items = %d, want 2", len(updated.Items))
	}

	// The same record under a different kind is a different record.
	_, added, err = store.AddItems(ctx, "col-1", []Item{item("dataset", "aur11223", "Something else")})
	if err != nil || added != 1 {
		t.Errorf("AddItems across kinds = %d, %v; want 1, nil", added, err)
	}
}

func TestPutReplacesItems(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)
	if err := store.Put(ctx, folder("col-1", "One", item("topic", "energy", "Energy"))); err != nil {
		t.Fatalf("Put: %v", err)
	}
	if err := store.Put(ctx, folder("col-1", "Two", item("topic", "trade", "Trade"))); err != nil {
		t.Fatalf("Put again: %v", err)
	}
	got, err := store.Get(ctx, "col-1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.Name != "Two" || len(got.Items) != 1 || got.Items[0].ID != "trade" {
		t.Fatalf("Put did not replace: %+v", got)
	}
}

func TestUpdateDistinguishesAbsentFromEmpty(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)
	start := folder("col-1", "Energy")
	start.Description = "What the handbook says"
	if _, err := store.Create(ctx, start); err != nil {
		t.Fatalf("Create: %v", err)
	}

	name := "Energy statistics"
	updated, err := store.Update(ctx, "col-1", &name, nil)
	if err != nil {
		t.Fatalf("Update: %v", err)
	}
	if updated.Name != name || updated.Description != "What the handbook says" {
		t.Fatalf("a nil description changed it: %+v", updated)
	}

	empty := ""
	updated, err = store.Update(ctx, "col-1", nil, &empty)
	if err != nil {
		t.Fatalf("Update: %v", err)
	}
	if updated.Description != "" {
		t.Fatalf("an empty description did not clear it: %q", updated.Description)
	}
}

func TestRemoveItemAndDelete(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)
	if _, err := store.Create(ctx, folder("col-1", "Markets",
		item("indicator", "e8gvmhci", "Brent crude price close"),
		item("commodity", "Gold", "Gold"))); err != nil {
		t.Fatalf("Create: %v", err)
	}

	got, err := store.RemoveItem(ctx, "col-1", "commodity", "Gold")
	if err != nil {
		t.Fatalf("RemoveItem: %v", err)
	}
	if len(got.Items) != 1 || got.Items[0].Kind != "indicator" {
		t.Fatalf("RemoveItem left %+v", got.Items)
	}

	if err := store.Delete(ctx, "col-1"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := store.Get(ctx, "col-1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Get after Delete = %v, want ErrNotFound", err)
	}
	if err := store.Delete(ctx, "col-1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("second Delete = %v, want ErrNotFound", err)
	}
}

func TestQueries(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)

	query := Query{
		ID:        "qry-1",
		Name:      "Brent, daily close",
		Kind:      "observations",
		Path:      "/observations",
		Search:    json.RawMessage(`{"indicator":["e8gvmhci"]}`),
		CreatedAt: time.Now().UTC(),
	}
	if err := store.PutQuery(ctx, query); err != nil {
		t.Fatalf("PutQuery: %v", err)
	}

	list, err := store.Queries(ctx)
	if err != nil || len(list) != 1 {
		t.Fatalf("Queries = %d, %v; want 1, nil", len(list), err)
	}
	// The filters have to survive the round trip intact — they are the whole
	// content of a saved query.
	var search map[string][]string
	if err := json.Unmarshal(list[0].Search, &search); err != nil {
		t.Fatalf("stored search is not JSON: %v", err)
	}
	if len(search["indicator"]) != 1 || search["indicator"][0] != "e8gvmhci" {
		t.Errorf("search round-tripped as %v", search)
	}

	if _, err := store.RenameQuery(ctx, "qry-1", "Brent crude"); err != nil {
		t.Fatalf("RenameQuery: %v", err)
	}
	renamed, err := store.GetQuery(ctx, "qry-1")
	if err != nil || renamed.Name != "Brent crude" {
		t.Fatalf("GetQuery = %+v, %v", renamed, err)
	}

	if err := store.DeleteQuery(ctx, "qry-1"); err != nil {
		t.Fatalf("DeleteQuery: %v", err)
	}
	if _, err := store.GetQuery(ctx, "qry-1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("GetQuery after delete = %v, want ErrNotFound", err)
	}
}

func TestImportMerges(t *testing.T) {
	ctx := context.Background()
	store := open(t, true)
	if _, err := store.Create(ctx, folder("col-1", "Already here")); err != nil {
		t.Fatalf("Create: %v", err)
	}

	shelf := Shelf{
		Collections: []Collection{
			folder("col-1", "Renamed by the import"), // skipped: id is held
			folder("col-2", "New"),
		},
		Queries: []Query{{
			ID: "qry-1", Name: "Q", Kind: "observations", Path: "/observations",
			Search: json.RawMessage(`{}`), CreatedAt: time.Now().UTC(),
		}},
	}
	added, queries, err := store.Import(ctx, shelf)
	if err != nil {
		t.Fatalf("Import: %v", err)
	}
	if added != 1 || queries != 1 {
		t.Fatalf("Import = %d collections, %d queries; want 1, 1", added, queries)
	}

	held, err := store.Get(ctx, "col-1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if held.Name != "Already here" {
		t.Errorf("import overwrote a folder that was already here: %q", held.Name)
	}

	// Importing the same shelf twice changes nothing.
	added, queries, err = store.Import(ctx, shelf)
	if err != nil || added != 0 || queries != 0 {
		t.Fatalf("re-import = %d, %d, %v; want 0, 0, nil", added, queries, err)
	}
}

func TestReadOnlyRefusesWrites(t *testing.T) {
	ctx := context.Background()
	store := open(t, false)

	if _, err := store.Create(ctx, folder("col-1", "Nope")); !errors.Is(err, ErrReadOnly) {
		t.Fatalf("Create = %v, want ErrReadOnly", err)
	}
	if err := store.Delete(ctx, "col-1"); !errors.Is(err, ErrReadOnly) {
		t.Fatalf("Delete = %v, want ErrReadOnly", err)
	}
	// Reading still works: a read-only shelf is for serving a curated one.
	list, err := store.List(ctx)
	if err != nil || len(list) != 0 {
		t.Fatalf("List = %d, %v; want 0, nil", len(list), err)
	}
}

func TestReopenKeepsTheShelf(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "shelf.duckdb")

	db, err := appdb.Open(path)
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	store, err := New(db, true)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if _, err := store.Create(ctx, folder("col-1", "Kept", item("dataset", "118v9w17", "Consumer survey"))); err != nil {
		t.Fatalf("Create: %v", err)
	}
	// Closed rather than left open: DuckDB holds the file exclusively, so this
	// also checks that a restart is all it takes to pick the shelf back up.
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	reopened := openAt(t, path, true)
	got, err := reopened.Get(ctx, "col-1")
	if err != nil {
		t.Fatalf("Get after reopen: %v", err)
	}
	if len(got.Items) != 1 || got.Items[0].Label != "Consumer survey" {
		t.Fatalf("reopened shelf lost its items: %+v", got)
	}
}
