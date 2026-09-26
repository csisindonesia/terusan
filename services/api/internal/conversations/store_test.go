package conversations

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/csis/terusan/services/api/internal/appdb"
)

func open(t *testing.T) *Store {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	store, err := New(db)
	if err != nil {
		t.Fatal(err)
	}
	return store
}

func TestAChatCanBeReopenedAndContinued(t *testing.T) {
	ctx := context.Background()
	store := open(t)

	c, err := store.Create(ctx, "user-1", "What do you have on rice prices?")
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range []Message{
		{Role: "user", Content: "What do you have on rice prices?"},
		{Role: "assistant", Content: "See [Strategic food prices](/datasets/x).", Sources: []string{"dataset:x"}},
		{Role: "user", Content: "and by province?"},
	} {
		if _, err := store.Append(ctx, c.ID, m); err != nil {
			t.Fatal(err)
		}
	}

	got, err := store.Get(ctx, c.ID, "user-1")
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Messages) != 3 || got.Messages[2].Seq != 2 || got.Messages[2].Content != "and by province?" {
		t.Fatalf("turns came back as %+v", got.Messages)
	}
	if len(got.Messages[1].Sources) != 1 || got.Messages[1].Sources[0] != "dataset:x" {
		t.Errorf("sources came back as %v", got.Messages[1].Sources)
	}
	if got.Title != "What do you have on rice prices?" {
		t.Errorf("title %q", got.Title)
	}
}

func TestAnAccountsChatIsItsOwn(t *testing.T) {
	ctx := context.Background()
	store := open(t)
	c, _ := store.Create(ctx, "user-1", "mine")

	if _, err := store.Get(ctx, c.ID, "user-2"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("another account opened it: %v", err)
	}
	if _, err := store.Get(ctx, c.ID, ""); !errors.Is(err, ErrNotFound) {
		t.Fatalf("a signed-out reader opened it: %v", err)
	}
	if err := store.Delete(ctx, c.ID, "user-2"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("another account deleted it: %v", err)
	}
	if list, _ := store.List(ctx, "user-2"); len(list) != 0 {
		t.Fatalf("it is listed for another account: %v", list)
	}
	if list, _ := store.List(ctx, "user-1"); len(list) != 1 {
		t.Fatalf("its owner's list holds %d", len(list))
	}
}

func TestAChatWithoutAnAccountOpensByItsID(t *testing.T) {
	ctx := context.Background()
	store := open(t)
	c, _ := store.Create(ctx, "", "anonymous")

	if _, err := store.Get(ctx, c.ID, ""); err != nil {
		t.Fatalf("the link did not open it: %v", err)
	}
	if list, _ := store.List(ctx, ""); len(list) != 0 {
		t.Fatal("signed-out chats were listed for everyone")
	}
	if _, err := store.Get(ctx, "not-a-uuid", ""); !errors.Is(err, ErrNotFound) {
		t.Fatal("a malformed id was looked up")
	}
}

func TestRegenerateDropsTheLaterTurns(t *testing.T) {
	ctx := context.Background()
	store := open(t)
	c, _ := store.Create(ctx, "", "q")
	store.Append(ctx, c.ID, Message{Role: "user", Content: "q"})
	store.Append(ctx, c.ID, Message{Role: "assistant", Content: "a"})

	if err := store.TruncateAfter(ctx, c.ID, 0); err != nil {
		t.Fatal(err)
	}
	got, _ := store.Get(ctx, c.ID, "")
	if len(got.Messages) != 1 {
		t.Fatalf("kept %d turns", len(got.Messages))
	}
	next, _ := store.Append(ctx, c.ID, Message{Role: "assistant", Content: "b"})
	if next.Seq != 1 {
		t.Errorf("the new reply is seq %d, want 1", next.Seq)
	}
}

func TestSetCollectionOnlyOnAReply(t *testing.T) {
	ctx := context.Background()
	store := open(t)
	c, _ := store.Create(ctx, "", "q")
	store.Append(ctx, c.ID, Message{Role: "user", Content: "q"})
	store.Append(ctx, c.ID, Message{Role: "assistant", Content: "a"})

	if err := store.SetCollection(ctx, c.ID, "", 1, "col-1"); err != nil {
		t.Fatal(err)
	}
	if err := store.SetCollection(ctx, c.ID, "", 0, "col-1"); !errors.Is(err, ErrNotFound) {
		t.Fatal("a question was given a collection")
	}
	got, _ := store.Get(ctx, c.ID, "")
	if got.Messages[1].CollectionID != "col-1" {
		t.Errorf("collection %q", got.Messages[1].CollectionID)
	}
}
