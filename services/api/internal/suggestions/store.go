// Package suggestions keeps what readers have asked the warehouse to collect.
//
// A source nobody has ingested yet is the most common thing a research portal
// is missing, and the person who notices is almost never the person who runs
// the pipelines. The portal's other forms hand the message to a mail client
// (see the portal's `lib/mailto.ts`) precisely because a form posting into a
// void is worse than no form; this one posts somewhere instead, because a
// request to ingest a source is a work item rather than a message — it wants a
// status, it wants to be visible to the next person about to ask for the same
// thing, and it wants to still be there next month.
//
// Stored beside the shelf and the accounts in the application database, and
// scoped to nobody: everyone signed in sees every suggestion. That is
// deliberate, and it is what makes the list useful as a queue.
package suggestions

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

// ErrNotFound is a suggestion this deployment does not hold.
var ErrNotFound = errors.New("no such suggestion")

// How often the source publishes, as the person asking understands it.
//
// A closed list, because the useful thing about cadence is comparing it to the
// schedule a pipeline would run on, and "whenever they feel like it" and
// "irregular" cannot be compared to anything if both are allowed.
var Cadences = []string{
	"daily",
	"weekly",
	"monthly",
	"quarterly",
	"annual",
	"irregular",
	"one-off",
	"unknown",
}

// Where a request has got to. Deliberately few: this is a queue somebody works
// through, not a project tracker.
var Statuses = []string{"open", "planned", "ingested", "declined"}

func valid(value string, allowed []string) bool {
	for _, entry := range allowed {
		if entry == value {
			return true
		}
	}
	return false
}

// Suggestion is one request to collect a source.
type Suggestion struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	// Where the data actually is. The single most valuable field: a title can
	// be searched for, a URL can be fetched.
	URL string `json:"url"`
	// One of Cadences.
	Cadence string `json:"cadence"`
	// What it covers, why it is worth having, and anything known about how it
	// is published — a login, a captcha, a PDF-only release.
	Description string `json:"description,omitempty"`
	// One of Statuses.
	Status string `json:"status"`
	/// What a maintainer said when they moved it — why it was declined, or
	/// which source it became.
	Note string `json:"note,omitempty"`
	// Who asked. Kept as the address as well as the id, so a suggestion still
	// names its author after the account is gone.
	RequestedBy      string    `json:"requested_by"`
	RequestedByEmail string    `json:"requested_by_email"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

type Store struct {
	db *appdb.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS suggestions (
    suggestion_id       TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL,
    cadence             TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    note                TEXT,
    requested_by        TEXT NOT NULL,
    -- The address as well as the account id: an account can be deleted, and a
    -- request whose author has become a UUID nobody recognises is a request
    -- nobody can follow up.
    requested_by_email  TEXT NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL
);
`

func New(db *appdb.DB) (*Store, error) {
	if db == nil {
		return nil, errors.New("no database")
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, fmt.Errorf("suggestions schema: %w", err)
	}
	return &Store{db: db}, nil
}

// Create files a request.
func (s *Store) Create(ctx context.Context, input Suggestion) (Suggestion, error) {
	input.Title = strings.TrimSpace(input.Title)
	input.URL = strings.TrimSpace(input.URL)
	input.Description = strings.TrimSpace(input.Description)

	if input.Title == "" {
		return Suggestion{}, errors.New("title: a suggestion needs a title")
	}
	if input.URL == "" {
		return Suggestion{}, errors.New("url: where the data is published")
	}
	if !valid(input.Cadence, Cadences) {
		return Suggestion{}, fmt.Errorf(
			"cadence: %q is not one of %s", input.Cadence, strings.Join(Cadences, ", "))
	}

	input.ID = uuid.NewString()
	input.Status = "open"
	now := time.Now().UTC()
	input.CreatedAt, input.UpdatedAt = now, now

	_, err := s.db.ExecContext(ctx, `
		INSERT INTO suggestions (suggestion_id, title, url, cadence, description,
		                         status, note, requested_by, requested_by_email,
		                         created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)`,
		input.ID, input.Title, input.URL, input.Cadence, nullable(input.Description),
		input.Status, input.RequestedBy, input.RequestedByEmail, now, now)
	return input, err
}

// List returns the queue, newest first. Open requests first, because that is
// what the list is read for.
func (s *Store) List(ctx context.Context, limit int) ([]Suggestion, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT suggestion_id, title, url, cadence, COALESCE(description, ''), status,
		       COALESCE(note, ''), requested_by, requested_by_email, created_at, updated_at
		FROM suggestions
		ORDER BY status = 'open' DESC, created_at DESC
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []Suggestion{}
	for rows.Next() {
		var entry Suggestion
		if err := rows.Scan(&entry.ID, &entry.Title, &entry.URL, &entry.Cadence,
			&entry.Description, &entry.Status, &entry.Note, &entry.RequestedBy,
			&entry.RequestedByEmail, &entry.CreatedAt, &entry.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, entry)
	}
	return out, rows.Err()
}

// SetStatus moves a request through the queue.
//
// Any signed-in person may move one, which matches what the rest of this
// service enforces: a session says who is asking and roles grant nothing yet
// (program.md §35). The note is where a maintainer says why.
func (s *Store) SetStatus(ctx context.Context, id, status, note string) (Suggestion, error) {
	if !valid(status, Statuses) {
		return Suggestion{}, fmt.Errorf(
			"status: %q is not one of %s", status, strings.Join(Statuses, ", "))
	}
	result, err := s.db.ExecContext(ctx, `
		UPDATE suggestions SET status = ?, note = ?, updated_at = ?
		WHERE suggestion_id = ?`,
		status, nullable(strings.TrimSpace(note)), time.Now().UTC(), id)
	if err != nil {
		return Suggestion{}, err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return Suggestion{}, ErrNotFound
	}
	return s.Get(ctx, id)
}

func (s *Store) Get(ctx context.Context, id string) (Suggestion, error) {
	var entry Suggestion
	err := s.db.QueryRowContext(ctx, `
		SELECT suggestion_id, title, url, cadence, COALESCE(description, ''), status,
		       COALESCE(note, ''), requested_by, requested_by_email, created_at, updated_at
		FROM suggestions WHERE suggestion_id = ?`, id,
	).Scan(&entry.ID, &entry.Title, &entry.URL, &entry.Cadence, &entry.Description,
		&entry.Status, &entry.Note, &entry.RequestedBy, &entry.RequestedByEmail,
		&entry.CreatedAt, &entry.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Suggestion{}, ErrNotFound
	}
	return entry, err
}

// Delete removes a request outright — a duplicate, or one filed by mistake.
func (s *Store) Delete(ctx context.Context, id string) error {
	result, err := s.db.ExecContext(ctx,
		`DELETE FROM suggestions WHERE suggestion_id = ?`, id)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return ErrNotFound
	}
	return nil
}

func nullable(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
