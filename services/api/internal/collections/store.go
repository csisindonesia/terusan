// Package collections keeps the reader's own shelf: named folders of records,
// and the queries worth re-running.
//
// One of the two things the serving layer stores rather than derives — the
// other is the accounts in internal/auth — and both live in the application
// database (internal/appdb), beside the lake rather than inside it. Everything
// else this service answers can be rebuilt by re-running a pipeline; a folder
// somebody assembled cannot.
//
// A collection belongs to whoever made it, and to the members they add. The
// owner renames it, deletes it and decides who else is in; a member files
// records into it and takes them out, which is what "send me that link" needs
// and no more. Everybody else does not see it at all — not a 403, a 404, so a
// folder's name is not something a stranger can probe for.
//
// Folders made before there were owners belong to nobody, and stay what they
// were: visible to and editable by everyone on the deployment. Anyone may
// claim one, which makes it theirs and private, and is the only way one gets
// members. Nothing is quietly re-homed by a migration, because the person a
// migration would pick is a guess.
//
// A deployment without accounts has no viewers to tell apart: every request is
// "", every folder is made unowned, and the shelf is shared exactly as it was.
//
// A collection holds indicators and nothing else. It is a set of series
// somebody is asking one question of, and its API (see API on Collection)
// serves their figures — which a folder of documents or regulations has none
// of. Folders from before this held other kinds too; those rows were dropped
// on upgrade.
//
// Saved queries are not scoped yet. They are filters, not somebody's records,
// and the shelf they are on is still the deployment's.
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

// ErrForbidden is returned when the viewer can see a collection but their role
// in it does not allow the change — a member renaming it, say.
var ErrForbidden = errors.New("not allowed for your role in this collection")

// ErrInvalid is returned for a request that cannot mean anything, such as
// adding a collection's owner as its member.
var ErrInvalid = errors.New("invalid request")

// A viewer's part in one collection.
const (
	// Made it: everything, including who else is in.
	RoleOwner = "owner"
	// Added by the owner: files records and takes them out.
	RoleMember = "member"
	// An unowned folder from before there were owners, which everyone on the
	// deployment may do anything with, as they always could.
	RoleShared = "shared"
)

// Member is an account added to a collection by its owner.
type Member struct {
	UserID  string    `json:"user_id"`
	AddedAt time.Time `json:"added_at"`
}

// ItemKind is the one kind of record a collection holds.
const ItemKind = "indicator"

// Item is one record filed into a collection.
//
// A reference, never a copy: an identifier the API already answers for, plus
// the label it carried when it was filed so a folder reads without fetching
// six catalogues first. The label can go stale — a publisher renames a series
// — and that is the right trade, because the identifier is what resolves and
// the label is only what is printed until it does.
type Item struct {
	// Always ItemKind. Kept on the wire so an item still says what it is, and
	// so a later kind is an addition rather than a new shape.
	Kind string `json:"kind"`
	// The series' `indicator_id`.
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
	// Whether the collection's figures are served under
	// /v1/collections/{id}/…, to its owner and members with their own
	// sessions or API tokens. Off until the owner turns it on: a folder is
	// somebody's reading list first, and an endpoint that other programs
	// depend on is a promise about it.
	API bool `json:"api"`

	// The account that made it, or "" for a folder from before there were
	// owners. Not serialized: who someone is, as opposed to their id, is the
	// accounts package's to say, and the handler adds it.
	OwnerID string   `json:"-"`
	Members []Member `json:"-"`
}

// RoleOf is the viewer's part in the collection, or "" when they have none and
// should not see it.
func (c Collection) RoleOf(viewer string) string {
	if c.OwnerID == "" {
		return RoleShared
	}
	if viewer == "" {
		return ""
	}
	if c.OwnerID == viewer {
		return RoleOwner
	}
	for _, m := range c.Members {
		if m.UserID == viewer {
			return RoleMember
		}
	}
	return ""
}

// manages reports whether the role may rename or delete the collection.
func manages(role string) bool { return role == RoleOwner || role == RoleShared }

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
    updated_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    -- '' for a folder nobody owns, which everyone on the deployment shares.
    owner_id       TEXT DEFAULT '',
    api_enabled    BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS collection_members (
    collection_id  TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    added_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (collection_id, user_id)
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

// Changes to a shelf made before them, each safe to run on every start.
var migrations = []string{
	`ALTER TABLE collections ADD COLUMN IF NOT EXISTS owner_id TEXT DEFAULT ''`,
	// Every folder that existed before owners did was everybody's.
	`UPDATE collections SET owner_id = '' WHERE owner_id IS NULL`,
	`ALTER TABLE collections ADD COLUMN IF NOT EXISTS api_enabled BOOLEAN DEFAULT FALSE`,
	`UPDATE collections SET api_enabled = FALSE WHERE api_enabled IS NULL`,
	// Collections became indicators-only. Anything else filed before then is
	// removed rather than kept out of sight: a row nothing shows and nothing
	// can remove is a row that confuses the next person to read this table.
	`DELETE FROM collection_items WHERE kind <> 'indicator'`,
}

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
	for _, step := range migrations {
		if _, err := db.Exec(step); err != nil {
			return nil, fmt.Errorf("collections migration: %w", err)
		}
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

// List returns every collection the viewer has a part in, newest change first,
// with its items and members.
func (s *Store) List(ctx context.Context, viewer string) ([]Collection, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT collection_id, name, COALESCE(description, ''), created_at, updated_at,
		       COALESCE(owner_id, ''), COALESCE(api_enabled, FALSE)
		FROM collections
		WHERE COALESCE(owner_id, '') = ''
		   OR (? <> '' AND owner_id = ?)
		   OR collection_id IN (
		       SELECT collection_id FROM collection_members WHERE user_id = ?)
		ORDER BY updated_at DESC, name ASC`, viewer, viewer, viewer)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Collection
	index := map[string]int{}
	for rows.Next() {
		var c Collection
		if err := rows.Scan(&c.ID, &c.Name, &c.Description, &c.CreatedAt, &c.UpdatedAt,
			&c.OwnerID, &c.API); err != nil {
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
	if err := items.Err(); err != nil {
		return nil, err
	}

	members, err := s.db.QueryContext(ctx, `
		SELECT collection_id, user_id, added_at
		FROM collection_members ORDER BY added_at ASC`)
	if err != nil {
		return nil, err
	}
	defer members.Close()
	for members.Next() {
		var id string
		var m Member
		if err := members.Scan(&id, &m.UserID, &m.AddedAt); err != nil {
			return nil, err
		}
		if at, ok := index[id]; ok {
			out[at].Members = append(out[at].Members, m)
		}
	}
	return out, members.Err()
}

// Get returns one collection the viewer has a part in, or ErrNotFound — also
// when it exists and they have none, so its existence is not an answer.
func (s *Store) Get(ctx context.Context, viewer, id string) (Collection, error) {
	c, err := s.load(ctx, id)
	if err != nil {
		return Collection{}, err
	}
	if c.RoleOf(viewer) == "" {
		return Collection{}, ErrNotFound
	}
	return c, nil
}

// access loads a collection and checks the viewer's role against what the
// change needs.
func (s *Store) access(ctx context.Context, viewer, id string, allowed func(string) bool) (Collection, string, error) {
	c, err := s.Get(ctx, viewer, id)
	if err != nil {
		return Collection{}, "", err
	}
	role := c.RoleOf(viewer)
	if !allowed(role) {
		return Collection{}, role, ErrForbidden
	}
	return c, role, nil
}

func anyRole(string) bool { return true }

// load is one collection whoever is asking.
func (s *Store) load(ctx context.Context, id string) (Collection, error) {
	var c Collection
	err := s.db.QueryRowContext(ctx, `
		SELECT collection_id, name, COALESCE(description, ''), created_at, updated_at,
		       COALESCE(owner_id, ''), COALESCE(api_enabled, FALSE)
		FROM collections WHERE collection_id = ?`, id,
	).Scan(&c.ID, &c.Name, &c.Description, &c.CreatedAt, &c.UpdatedAt, &c.OwnerID, &c.API)
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
	if err := rows.Err(); err != nil {
		return Collection{}, err
	}

	members, err := s.db.QueryContext(ctx, `
		SELECT user_id, added_at FROM collection_members
		WHERE collection_id = ? ORDER BY added_at ASC`, id)
	if err != nil {
		return Collection{}, err
	}
	defer members.Close()
	for members.Next() {
		var m Member
		if err := members.Scan(&m.UserID, &m.AddedAt); err != nil {
			return Collection{}, err
		}
		c.Members = append(c.Members, m)
	}
	return c, members.Err()
}

// Create files a new collection, owned by c.OwnerID. An id already in use is an
// error rather than a silent overwrite — see Put for the import case.
func (s *Store) Create(ctx context.Context, c Collection) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	if _, err := s.load(ctx, c.ID); err == nil {
		return Collection{}, fmt.Errorf("collection %s already exists", c.ID)
	}
	return c, s.put(ctx, c)
}

// Put writes a collection whether or not it is already here, items and all,
// with the owner it carries. Its members, if it had any, stay.
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
		INSERT INTO collections (collection_id, name, description, created_at, updated_at,
		                         owner_id, api_enabled)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		c.ID, c.Name, nullable(c.Description), c.CreatedAt, c.UpdatedAt,
		c.OwnerID, c.API); err != nil {
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
		// The handlers refuse other kinds; this is the second lock, so an
		// import or a test cannot put back what the migration took out.
		if seen[key] || item.Kind != ItemKind {
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

// Changes is what Update may change about a collection. A nil field is "leave
// it alone" and an empty description is "remove it", which are different
// requests and would be the same one if this took plain strings.
type Changes struct {
	Name        *string
	Description *string
	API         *bool
}

// Update renames a collection, changes what it says it is for, or turns its
// API on or off.
func (s *Store) Update(ctx context.Context, viewer, id string, change Changes) (Collection, error) {
	name, description := change.Name, change.Description
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	current, _, err := s.access(ctx, viewer, id, manages)
	if err != nil {
		return Collection{}, err
	}
	if name != nil && strings.TrimSpace(*name) != "" {
		current.Name = strings.TrimSpace(*name)
	}
	if description != nil {
		current.Description = strings.TrimSpace(*description)
	}
	if change.API != nil {
		current.API = *change.API
	}
	current.UpdatedAt = time.Now().UTC()

	if _, err := s.db.ExecContext(ctx, `
		UPDATE collections SET name = ?, description = ?, api_enabled = ?, updated_at = ?
		WHERE collection_id = ?`,
		current.Name, nullable(current.Description), current.API, current.UpdatedAt, id); err != nil {
		return Collection{}, err
	}
	return current, nil
}

// Delete removes a collection, everything filed in it, and its members.
func (s *Store) Delete(ctx context.Context, viewer, id string) error {
	if err := s.guard(); err != nil {
		return err
	}
	if _, _, err := s.access(ctx, viewer, id, manages); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	for _, table := range []string{"collection_items", "collection_members", "collections"} {
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM `+table+` WHERE collection_id = ?`, id); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// AddItems files records, skipping any the collection already holds, and
// returns how many were new.
func (s *Store) AddItems(ctx context.Context, viewer, id string, items []Item) (Collection, int, error) {
	if err := s.guard(); err != nil {
		return Collection{}, 0, err
	}
	current, _, err := s.access(ctx, viewer, id, anyRole)
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
		if held[item.Kind+"\x00"+item.ID] || item.Kind != ItemKind {
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

	updated, err := s.load(ctx, id)
	return updated, len(fresh), err
}

// RemoveItem takes one record out of a collection.
func (s *Store) RemoveItem(ctx context.Context, viewer, id, kind, ref string) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	if _, _, err := s.access(ctx, viewer, id, anyRole); err != nil {
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
	return s.load(ctx, id)
}

// Claim makes an unowned folder the viewer's, which also makes it private to
// them until they add members.
func (s *Store) Claim(ctx context.Context, viewer, id string) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	if viewer == "" {
		return Collection{}, ErrForbidden
	}
	if _, _, err := s.access(ctx, viewer, id,
		func(role string) bool { return role == RoleShared }); err != nil {
		return Collection{}, err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE collections SET owner_id = ?, updated_at = ? WHERE collection_id = ?`,
		viewer, time.Now().UTC(), id); err != nil {
		return Collection{}, err
	}
	return s.load(ctx, id)
}

// AddMember lets another account into the viewer's collection. Adding someone
// already in is not an error; adding the owner is, because it would mean
// nothing.
func (s *Store) AddMember(ctx context.Context, viewer, id, user string) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	current, _, err := s.access(ctx, viewer, id,
		func(role string) bool { return role == RoleOwner })
	if err != nil {
		return Collection{}, err
	}
	if user == "" || user == current.OwnerID {
		return Collection{}, ErrInvalid
	}
	if current.RoleOf(user) == RoleMember {
		return current, nil
	}
	if _, err := s.db.ExecContext(ctx, `
		INSERT INTO collection_members (collection_id, user_id, added_at)
		VALUES (?, ?, ?)`, id, user, time.Now().UTC()); err != nil {
		return Collection{}, err
	}
	return s.load(ctx, id)
}

// RemoveMember takes an account out of a collection: the owner removing
// anyone, or a member leaving.
func (s *Store) RemoveMember(ctx context.Context, viewer, id, user string) (Collection, error) {
	if err := s.guard(); err != nil {
		return Collection{}, err
	}
	if _, _, err := s.access(ctx, viewer, id, func(role string) bool {
		return role == RoleOwner || (role == RoleMember && viewer == user)
	}); err != nil {
		return Collection{}, err
	}
	if _, err := s.db.ExecContext(ctx,
		`DELETE FROM collection_members WHERE collection_id = ? AND user_id = ?`,
		id, user); err != nil {
		return Collection{}, err
	}
	return s.load(ctx, id)
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
//
// Imported folders are the viewer's, whoever made them where they came from.
func (s *Store) Import(ctx context.Context, viewer string, shelf Shelf) (collections, queries int, err error) {
	if err := s.guard(); err != nil {
		return 0, 0, err
	}
	for _, c := range shelf.Collections {
		c.OwnerID = viewer
		if _, err := s.load(ctx, c.ID); err == nil {
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
