// Package collections keeps the reader's own shelf: named folders of records,
// and the queries worth re-running.
//
// One of the two things the serving layer stores rather than derives — the
// other is the accounts in internal/auth — and both live in the application
// database (internal/appdb), beside the lake rather than inside it. Everything
// else this service answers can be rebuilt by re-running a pipeline; a folder
// somebody assembled cannot.
//
// Nothing here is scoped to a person. Logging in identifies who is reading
// (internal/auth); it does not yet partition what they read, and a folder is
// shared by everyone on the deployment. One deployment is one shelf, and a
// deployment whose shelf should not be edited by whoever can reach it is
// started read-only. The handlers say so rather than implying a privacy this
// does not have.
package collections

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/appdb"
)

// ErrNotFound is returned for a collection or query this shelf does not hold.
var ErrNotFound = errors.New("not found")

// ErrReadOnly is returned when the deployment forbids writes.
var ErrReadOnly = errors.New("this shelf is read-only")

// Item is one record filed into a collection.
//
// A reference, never a copy: an identifier the API already answers for, plus
// the label it carried when it was filed so a folder reads without fetching
// six catalogues first. The label can go stale — a publisher renames a series
// — and that is the right trade, because the identifier is what resolves and
// the label is only what is printed until it does.
type Item struct {
	// indicator, dataset, document, regulation, commodity or topic.
	Kind string `json:"kind"`
	// The identifier that kind is addressed by: `indicator_id`, `dataset_id`,
	// `document_id`, a regulation key, a commodity's printed name, a tag.
	ID      string    `json:"id"`
	Label   string    `json:"label"`
	Note    string    `json:"note,omitempty"`
	AddedAt time.Time `json:"added_at"`
}

// Collection is a named folder of records.
type Collection struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	Items       []Item    `json:"items"`
}

// Query is a named set of filters, stored as the filters rather than as the
// rows they return — so it answers against the warehouse as it is when it is
// reopened.
type Query struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	// observations, documents, regulations, indicators, datasets,
	// commodities or search.
	Kind string `json:"kind"`
	// The portal route it reopens — `/observations`.
	Path string `json:"path"`
	// The search parameters, as the route validated them.
	Search json.RawMessage `json:"search"`
	// What the filters said when it was saved, for the card.
	Summary   string    `json:"summary,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

// Shelf is everything one deployment holds, which is what an import carries.
type Shelf struct {
	Collections []Collection `json:"collections"`
	Queries     []Query      `json:"queries"`
}

// Store is a shelf in the application database.
type Store struct {
	db       *appdb.DB
	writable bool
}

const schema = `
CREATE TABLE IF NOT EXISTS collections (
    collection_id  TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT,
    created_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at     TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    ref_id         TEXT NOT NULL,
    label          TEXT NOT NULL,
    note           TEXT,
    added_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    -- One record is in a folder or it is not. Filing it twice — from a search,
    -- then from its own page — is a thing readers do, and a folder holding it
    -- twice is a bug rather than a fact about the folder.
    PRIMARY KEY (collection_id, kind, ref_id)
);

CREATE TABLE IF NOT EXISTS saved_queries (
    query_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    -- Held as the text of a JSON object rather than as a typed column per
    -- filter: the filters differ per route and gain new ones, and a migration
    -- for every new facet would be a tax on the portal, not on this file.
    search      TEXT NOT NULL,
    summary     TEXT,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL
);
`

// New prepares the shelf's tables in the application database.
//
// `writable` is the deployment's answer to "may whoever can reach this API
// change it". It is a deployment setting rather than a permission: a session
// says who is reading, and every session sees the same shelf.
func New(db *appdb.DB, writable bool) (*Store, error) {
	if db == nil {
		return nil, errors.New("no database")
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, fmt.Errorf("collections schema: %w", err)
	}
	return &Store{db: db, writable: writable}, nil
}

// Writable reports whether this deployment accepts changes.
func (s *Store) Writable() bool { return s != nil && s.writable }

func (s *Store) guard() error {
	if s == nil {
		return ErrNotFound
	}
	if !s.writable {
		return ErrReadOnly
	}
	return nil
}

// List returns every collection, newest change first, with its items.
func (s *Store) List(ctx context.Context) ([]Collection, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT collection_id, name, COALESCE(description, ''), created_at, updated_at
		FROM collections
		ORDER BY updated_at DESC, name ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Collection
	index := map[string]int{}
	for rows.Next() {
		var c Collection
		if err := rows.Scan(&c.ID, &c.Name, &c.Description, &c.CreatedAt, &c.UpdatedAt); err != nil {
			return nil, err
		}
		c.Items = []Item{}
		index[c.ID] = len(out)
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return []Collection{}, nil
	}

	// Every item in one pass rather than a query per folder: eleven folders
	// would otherwise be twelve round trips to answer one list.
	items, err := s.db.QueryContext(ctx, `
		SELECT collection_id, kind, ref_id, label, COALESCE(note, ''), added_at
		FROM collection_items
		ORDER BY added_at ASC, label ASC`)
	if err != nil {
		return nil, err
	}
	defer items.Close()

	for items.Next() {
		var id string
		var item Item
		if err := items.Scan(&id, &item.Kind, &item.ID, &item.Label, &item.Note, &item.AddedAt); err != nil {
			return nil, err
		}
		if at, ok := index[id]; ok {
			out[at].Items = append(out[at].Items, item)
		}
	}
	return out, items.Err()
}

// Get returns one collection, or ErrNotFound.
func (s *Store) Get(ctx context.Context, id string) (Collection, error) {
	var c Collection
	err := s.db.QueryRowContext(ctx, `
		SELECT collection_id, name, COALESCE(description, ''), created_at, updated_at
		FROM collections WHERE collection_id = ?`, id,
	).Scan(&c.ID, &c.Name, &c.Description, &c.CreatedAt, &c.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Collection{}, ErrNotFound
	}
	if err != nil {
		return Collection{}, err
	}

	rows, err := s.db.QueryContext(ctx, `
		SELECT kind, ref_id, label, COALESCE(note, ''), added_at
		FROM collection_items WHERE collection_id = ?
		ORDER BY added_at ASC, label ASC`, id)
	if err != nil {
		return Collection{}, err
	}
	defer rows.Close()

	c.Items = []Item{}
	for rows.Next() {
		var item Item
		if err := rows.Scan(&item.Kind, &item.ID, &item.Label, &item.Note, &item.AddedAt); err != nil {
			return Collection{}, err
		}
		c.Items = append(c.Items, item)
	}
	return c, rows.Err()
}

// Create files a new collection. An id already in use is an error rather than
// a silent overwrite — see Put for the import case.
func (s *Store) Create(ctx context.Context, c Collection) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	if _, err := s.Get(ctx, c.ID); err == nil {
		return Collection{}, fmt.Errorf("collection %s already exists", c.ID)
	}
	return c, s.put(ctx, c)
}

// Put writes a collection whether or not it is already here, items and all.
// This is what an import does.
func (s *Store) Put(ctx context.Context, c Collection) error {
	if err := s.guard(); err != nil {
		return err
	}
	return s.put(ctx, c)
}

func (s *Store) put(ctx context.Context, c Collection) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx,
		`DELETE FROM collection_items WHERE collection_id = ?`, c.ID); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM collections WHERE collection_id = ?`, c.ID); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO collections (collection_id, name, description, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?)`,
		c.ID, c.Name, nullable(c.Description), c.CreatedAt, c.UpdatedAt); err != nil {
		return err
	}
	if err := insertItems(ctx, tx, c.ID, c.Items); err != nil {
		return err
	}
	return tx.Commit()
}

func insertItems(ctx context.Context, tx *sql.Tx, id string, items []Item) error {
	seen := map[string]bool{}
	for _, item := range items {
		key := item.Kind + "\x00" + item.ID
		if seen[key] {
			continue
		}
		seen[key] = true
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO collection_items (collection_id, kind, ref_id, label, note, added_at)
			VALUES (?, ?, ?, ?, ?, ?)`,
			id, item.Kind, item.ID, item.Label, nullable(item.Note), item.AddedAt,
		); err != nil {
			return err
		}
	}
	return nil
}

// Update renames a collection or changes what it says it is for.
//
// A nil field is "leave it alone" and an empty description is "remove it",
// which are different requests and would be the same one if this took plain
// strings.
func (s *Store) Update(ctx context.Context, id string, name, description *string) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	current, err := s.Get(ctx, id)
	if err != nil {
		return Collection{}, err
	}
	if name != nil && strings.TrimSpace(*name) != "" {
		current.Name = strings.TrimSpace(*name)
	}
	if description != nil {
		current.Description = strings.TrimSpace(*description)
	}
	current.UpdatedAt = time.Now().UTC()

	if _, err := s.db.ExecContext(ctx, `
		UPDATE collections SET name = ?, description = ?, updated_at = ?
		WHERE collection_id = ?`,
		current.Name, nullable(current.Description), current.UpdatedAt, id); err != nil {
		return Collection{}, err
	}
	return current, nil
}

// Delete removes a collection and everything filed in it.
func (s *Store) Delete(ctx context.Context, id string) error {
	if err := s.guard(); err != nil {
		return err
	}
	if _, err := s.Get(ctx, id); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx,
		`DELETE FROM collection_items WHERE collection_id = ?`, id); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM collections WHERE collection_id = ?`, id); err != nil {
		return err
	}
	return tx.Commit()
}

// AddItems files records, skipping any the collection already holds, and
// returns how many were new.
func (s *Store) AddItems(ctx context.Context, id string, items []Item) (Collection, int, error) {
	if err := s.guard(); err != nil {
		return Collection{}, 0, err
	}
	current, err := s.Get(ctx, id)
	if err != nil {
		return Collection{}, 0, err
	}

	held := map[string]bool{}
	for _, item := range current.Items {
		held[item.Kind+"\x00"+item.ID] = true
	}
	var fresh []Item
	now := time.Now().UTC()
	for _, item := range items {
		if held[item.Kind+"\x00"+item.ID] {
			continue
		}
		held[item.Kind+"\x00"+item.ID] = true
		if item.AddedAt.IsZero() {
			item.AddedAt = now
		}
		fresh = append(fresh, item)
	}
	if len(fresh) == 0 {
		return current, 0, nil
	}

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Collection{}, 0, err
	}
	defer tx.Rollback()

	if err := insertItems(ctx, tx, id, fresh); err != nil {
		return Collection{}, 0, err
	}
	if _, err := tx.ExecContext(ctx,
		`UPDATE collections SET updated_at = ? WHERE collection_id = ?`, now, id); err != nil {
		return Collection{}, 0, err
	}
	if err := tx.Commit(); err != nil {
		return Collection{}, 0, err
	}

	updated, err := s.Get(ctx, id)
	return updated, len(fresh), err
}

// RemoveItem takes one record out of a collection.
func (s *Store) RemoveItem(ctx context.Context, id, kind, ref string) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	if _, err := s.Get(ctx, id); err != nil {
		return Collection{}, err
	}
	if _, err := s.db.ExecContext(ctx, `
		DELETE FROM collection_items
		WHERE collection_id = ? AND kind = ? AND ref_id = ?`, id, kind, ref); err != nil {
		return Collection{}, err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE collections SET updated_at = ? WHERE collection_id = ?`,
		time.Now().UTC(), id); err != nil {
		return Collection{}, err
	}
	return s.Get(ctx, id)
}

// Queries returns every saved query, newest first.
func (s *Store) Queries(ctx context.Context) ([]Query, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT query_id, name, kind, path, search, COALESCE(summary, ''), created_at
		FROM saved_queries ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []Query{}
	for rows.Next() {
		var q Query
		var search string
		if err := rows.Scan(&q.ID, &q.Name, &q.Kind, &q.Path, &search, &q.Summary, &q.CreatedAt); err != nil {
			return nil, err
		}
		q.Search = json.RawMessage(search)
		out = append(out, q)
	}
	return out, rows.Err()
}

// GetQuery returns one saved query, or ErrNotFound.
func (s *Store) GetQuery(ctx context.Context, id string) (Query, error) {
	var q Query
	var search string
	err := s.db.QueryRowContext(ctx, `
		SELECT query_id, name, kind, path, search, COALESCE(summary, ''), created_at
		FROM saved_queries WHERE query_id = ?`, id,
	).Scan(&q.ID, &q.Name, &q.Kind, &q.Path, &search, &q.Summary, &q.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Query{}, ErrNotFound
	}
	if err != nil {
		return Query{}, err
	}
	q.Search = json.RawMessage(search)
	return q, nil
}

// PutQuery saves a query, replacing one of the same id.
func (s *Store) PutQuery(ctx context.Context, q Query) error {
	if err := s.guard(); err != nil {
		return err
	}
	search := string(q.Search)
	if search == "" {
		search = "{}"
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx,
		`DELETE FROM saved_queries WHERE query_id = ?`, q.ID); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO saved_queries (query_id, name, kind, path, search, summary, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		q.ID, q.Name, q.Kind, q.Path, search, nullable(q.Summary), q.CreatedAt); err != nil {
		return err
	}
	return tx.Commit()
}

// RenameQuery changes what a saved query is called, which is the only thing
// about one worth changing: its filters are what it *is*.
func (s *Store) RenameQuery(ctx context.Context, id, name string) (Query, error) {
	if err := s.guard(); err != nil {
		return Query{}, err
	}
	current, err := s.GetQuery(ctx, id)
	if err != nil {
		return Query{}, err
	}
	current.Name = strings.TrimSpace(name)
	if current.Name == "" {
		return Query{}, errors.New("a query needs a name")
	}
	_, err = s.db.ExecContext(ctx,
		`UPDATE saved_queries SET name = ? WHERE query_id = ?`, current.Name, id)
	return current, err
}

// DeleteQuery removes a saved query.
func (s *Store) DeleteQuery(ctx context.Context, id string) error {
	if err := s.guard(); err != nil {
		return err
	}
	if _, err := s.GetQuery(ctx, id); err != nil {
		return err
	}
	_, err := s.db.ExecContext(ctx, `DELETE FROM saved_queries WHERE query_id = ?`, id)
	return err
}

// Import merges a shelf in, leaving anything already here alone.
//
// Merge rather than replace: the usual case is carrying folders *to* a
// deployment that already has some, and an import that quietly replaced them
// would be a data loss nobody asked for. An id already present is therefore
// skipped, which also makes importing the same file twice a no-op.
func (s *Store) Import(ctx context.Context, shelf Shelf) (collections, queries int, err error) {
	if err := s.guard(); err != nil {
		return 0, 0, err
	}
	for _, c := range shelf.Collections {
		if _, err := s.Get(ctx, c.ID); err == nil {
			continue
		} else if !errors.Is(err, ErrNotFound) {
			return collections, queries, err
		}
		if err := s.put(ctx, c); err != nil {
			return collections, queries, err
		}
		collections++
	}
	for _, q := range shelf.Queries {
		if _, err := s.GetQuery(ctx, q.ID); err == nil {
			continue
		} else if !errors.Is(err, ErrNotFound) {
			return collections, queries, err
		}
		if err := s.PutQuery(ctx, q); err != nil {
			return collections, queries, err
		}
		queries++
	}
	return collections, queries, nil
}

// nullable keeps an absent string out of the column as NULL rather than as an
// empty string, so "no description" reads the same to SQL as it does here.
func nullable(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
