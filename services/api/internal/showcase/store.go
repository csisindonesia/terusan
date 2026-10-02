// Package showcase keeps the dashboards and articles built on this warehouse's
// data — what the portal lists under Discover → Portal.
//
// A dataset page says what a collection holds; it cannot say what anybody has
// done with it, and that is the question a funder, a new researcher or the
// agency that published the figures asks first. Nothing in the lake can answer
// it: a dashboard on somebody else's server leaves no trace in a Parquet file.
// So it is kept here, beside the shelf and the accounts, as one of the few
// things this service stores rather than derives.
//
// Written by administrators only. Every entry is a public claim that a piece
// of work stands on this data, and a list anyone could add to would be a list
// nobody could cite.
package showcase

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/csis/terusan/services/api/internal/appdb"
)

// ErrNotFound is an entry this deployment does not hold.
var ErrNotFound = errors.New("no such entry")

// What an entry is. A closed list, because the page filters by it and a filter
// over free text splits "Dashboard" from "dashboard" from "dashboards".
var Kinds = []string{"dashboard", "article", "report", "other"}

// MaxDatasets bounds how many datasets one entry may name. A piece of work
// citing more than this is citing the warehouse, and says so in its
// description instead.
const MaxDatasets = 50

func valid(value string, allowed []string) bool {
	for _, entry := range allowed {
		if entry == value {
			return true
		}
	}
	return false
}

// Entry is one dashboard, article or report that uses the warehouse's data.
type Entry struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	// One of Kinds.
	Kind string `json:"kind"`
	// Where the work itself is. Required: an entry nobody can open is a
	// rumour, not a showcase.
	URL string `json:"url"`
	// Who made it — a person, a team, a newspaper.
	Publisher string `json:"publisher,omitempty"`
	// When it was published, as YYYY-MM-DD. Optional, because a dashboard
	// that is updated every week has no single date worth claiming.
	PublishedOn string `json:"published_on,omitempty"`
	Description string `json:"description,omitempty"`
	// The datasets it draws on, by id, in the order they were given.
	Datasets []string `json:"datasets"`
	// Who added it. The address as well as the id, so an entry still names
	// who vouched for it after the account is gone.
	CreatedBy      string    `json:"created_by"`
	CreatedByEmail string    `json:"created_by_email"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

// Filter narrows a listing. Empty fields do not narrow.
type Filter struct {
	Kind      string
	DatasetID string
}

type Store struct {
	db *appdb.DB
}

// The datasets sit in a table of their own rather than a list column, so the
// dataset page's "used in" question is an indexed lookup and not a scan of
// every entry's list.
const schema = `
CREATE TABLE IF NOT EXISTS showcase (
    showcase_id       TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    kind              TEXT NOT NULL,
    url               TEXT NOT NULL,
    publisher         TEXT,
    published_on      DATE,
    description       TEXT,
    created_by        TEXT NOT NULL,
    created_by_email  TEXT NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE TABLE IF NOT EXISTS showcase_datasets (
    showcase_id  TEXT NOT NULL,
    dataset_id   TEXT NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (showcase_id, dataset_id)
);
`

func New(db *appdb.DB) (*Store, error) {
	if db == nil {
		return nil, errors.New("no database")
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, fmt.Errorf("showcase schema: %w", err)
	}
	return &Store{db: db}, nil
}

// clean trims an entry and says what is wrong with it, if anything.
func clean(input Entry) (Entry, error) {
	input.Title = strings.TrimSpace(input.Title)
	input.Kind = strings.TrimSpace(input.Kind)
	input.URL = strings.TrimSpace(input.URL)
	input.Publisher = strings.TrimSpace(input.Publisher)
	input.PublishedOn = strings.TrimSpace(input.PublishedOn)
	input.Description = strings.TrimSpace(input.Description)

	if input.Title == "" {
		return Entry{}, errors.New("title: an entry needs a title")
	}
	if input.URL == "" {
		return Entry{}, errors.New("url: where the work can be opened")
	}
	if !valid(input.Kind, Kinds) {
		return Entry{}, fmt.Errorf("kind: %q is not one of %s", input.Kind, strings.Join(Kinds, ", "))
	}
	if input.PublishedOn != "" {
		if _, err := time.Parse(time.DateOnly, input.PublishedOn); err != nil {
			return Entry{}, errors.New("published_on: is not a date written YYYY-MM-DD")
		}
	}

	// Deduplicated in order: the first mention decides the position, so an
	// editor who pastes a list twice gets the list they meant.
	seen := map[string]bool{}
	datasets := []string{}
	for _, id := range input.Datasets {
		id = strings.TrimSpace(id)
		if id == "" || seen[id] {
			continue
		}
		seen[id] = true
		datasets = append(datasets, id)
	}
	if len(datasets) > MaxDatasets {
		return Entry{}, fmt.Errorf("datasets: at most %d", MaxDatasets)
	}
	input.Datasets = datasets
	return input, nil
}

// Create adds an entry.
func (s *Store) Create(ctx context.Context, input Entry) (Entry, error) {
	input, err := clean(input)
	if err != nil {
		return Entry{}, err
	}
	input.ID = uuid.NewString()
	now := time.Now().UTC()
	input.CreatedAt, input.UpdatedAt = now, now

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Entry{}, err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO showcase (showcase_id, title, kind, url, publisher, published_on,
		                      description, created_by, created_by_email, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		input.ID, input.Title, input.Kind, input.URL, nullable(input.Publisher),
		nullable(input.PublishedOn), nullable(input.Description),
		input.CreatedBy, input.CreatedByEmail, now, now); err != nil {
		return Entry{}, err
	}
	if err := writeDatasets(ctx, tx, input.ID, input.Datasets); err != nil {
		return Entry{}, err
	}
	if err := tx.Commit(); err != nil {
		return Entry{}, err
	}
	return input, nil
}

// Update replaces an entry's fields and its datasets. Who added it, and when,
// stay as they were.
func (s *Store) Update(ctx context.Context, id string, input Entry) (Entry, error) {
	input, err := clean(input)
	if err != nil {
		return Entry{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Entry{}, err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `
		UPDATE showcase SET title = ?, kind = ?, url = ?, publisher = ?, published_on = ?,
		                    description = ?, updated_at = ?
		WHERE showcase_id = ?`,
		input.Title, input.Kind, input.URL, nullable(input.Publisher),
		nullable(input.PublishedOn), nullable(input.Description), time.Now().UTC(), id)
	if err != nil {
		return Entry{}, err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return Entry{}, ErrNotFound
	}
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM showcase_datasets WHERE showcase_id = ?`, id); err != nil {
		return Entry{}, err
	}
	if err := writeDatasets(ctx, tx, id, input.Datasets); err != nil {
		return Entry{}, err
	}
	if err := tx.Commit(); err != nil {
		return Entry{}, err
	}
	return s.Get(ctx, id)
}

func writeDatasets(ctx context.Context, tx *sql.Tx, id string, datasets []string) error {
	for position, dataset := range datasets {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO showcase_datasets (showcase_id, dataset_id, position)
			VALUES (?, ?, ?)`, id, dataset, position); err != nil {
			return err
		}
	}
	return nil
}

const selectEntry = `
	SELECT e.showcase_id, e.title, e.kind, e.url, COALESCE(e.publisher, ''),
	       COALESCE(CAST(e.published_on AS VARCHAR), ''), COALESCE(e.description, ''),
	       e.created_by, e.created_by_email, e.created_at, e.updated_at
	FROM showcase e`

func scan(rows interface{ Scan(...any) error }) (Entry, error) {
	var entry Entry
	err := rows.Scan(&entry.ID, &entry.Title, &entry.Kind, &entry.URL, &entry.Publisher,
		&entry.PublishedOn, &entry.Description, &entry.CreatedBy, &entry.CreatedByEmail,
		&entry.CreatedAt, &entry.UpdatedAt)
	entry.Datasets = []string{}
	return entry, err
}

// List returns the entries, newest publication first. An entry with no date
// sorts by when it was added, so a fresh dashboard is not buried under every
// dated article.
func (s *Store) List(ctx context.Context, filter Filter) ([]Entry, error) {
	clauses := []string{"true"}
	args := []any{}
	if filter.Kind != "" {
		clauses = append(clauses, "e.kind = ?")
		args = append(args, filter.Kind)
	}
	if filter.DatasetID != "" {
		clauses = append(clauses, `e.showcase_id IN
			(SELECT showcase_id FROM showcase_datasets WHERE dataset_id = ?)`)
		args = append(args, filter.DatasetID)
	}
	rows, err := s.db.QueryContext(ctx, selectEntry+
		" WHERE "+strings.Join(clauses, " AND ")+
		" ORDER BY coalesce(e.published_on, CAST(e.created_at AS DATE)) DESC, e.created_at DESC",
		args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []Entry{}
	index := map[string]int{}
	for rows.Next() {
		entry, err := scan(rows)
		if err != nil {
			return nil, err
		}
		index[entry.ID] = len(out)
		out = append(out, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return out, nil
	}

	// All the datasets in one query rather than one per entry. The table is a
	// few hundred rows at most, so reading it whole is cheaper than binding a
	// list of ids.
	links, err := s.db.QueryContext(ctx,
		`SELECT showcase_id, dataset_id FROM showcase_datasets ORDER BY showcase_id, position`)
	if err != nil {
		return nil, err
	}
	defer links.Close()
	for links.Next() {
		var id, dataset string
		if err := links.Scan(&id, &dataset); err != nil {
			return nil, err
		}
		if n, ok := index[id]; ok {
			out[n].Datasets = append(out[n].Datasets, dataset)
		}
	}
	return out, links.Err()
}

func (s *Store) Get(ctx context.Context, id string) (Entry, error) {
	entry, err := scan(s.db.QueryRowContext(ctx, selectEntry+" WHERE e.showcase_id = ?", id))
	if errors.Is(err, sql.ErrNoRows) {
		return Entry{}, ErrNotFound
	}
	if err != nil {
		return Entry{}, err
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT dataset_id FROM showcase_datasets WHERE showcase_id = ? ORDER BY position`, id)
	if err != nil {
		return Entry{}, err
	}
	defer rows.Close()
	for rows.Next() {
		var dataset string
		if err := rows.Scan(&dataset); err != nil {
			return Entry{}, err
		}
		entry.Datasets = append(entry.Datasets, dataset)
	}
	return entry, rows.Err()
}

// Delete removes an entry and its dataset links.
func (s *Store) Delete(ctx context.Context, id string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `DELETE FROM showcase WHERE showcase_id = ?`, id)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return ErrNotFound
	}
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM showcase_datasets WHERE showcase_id = ?`, id); err != nil {
		return err
	}
	return tx.Commit()
}

func nullable(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
