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
// through, not a project tracker. "planned" is a request an administrator has
// accepted, and "declined" one they have turned down.
var Statuses = []string{"open", "planned", "ingested", "declined"}

// Why a request was declined. A refusal without a reason reads as a door shut
// in the asker's face, and the commonest reason — the warehouse already holds
// it — is the one that most deserves an answer the asker can act on, which is
// the dataset itself.
var Reasons = []string{
	// Already in the warehouse, under a name the asker did not find. Comes
	// with the dataset it is in.
	"already-collected",
	// Not something this warehouse is for.
	"out-of-scope",
	// Behind a login, a captcha or a wall nobody here can get past.
	"unreachable",
	"other",
}

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
	// One of Reasons, when the status is "declined".
	Reason string `json:"reason,omitempty"`
	// The dataset that answers the request: the one it was already in, for a
	// request declined as already collected, or the one it became, for one
	// that has been ingested.
	DatasetID string `json:"dataset_id,omitempty"`
	// What a maintainer said when they moved it — why it was declined, or
	// which source it became.
	Note string `json:"note,omitempty"`
	// Who asked. Kept as the address as well as the id, so a suggestion still
	// names its author after the account is gone.
	RequestedBy      string `json:"requested_by"`
	RequestedByEmail string `json:"requested_by_email"`
	// Which administrator last moved it, and when. Absent while it has never
	// been looked at, which is what "open" alone cannot tell apart from
	// "reopened".
	ReviewedByEmail string     `json:"reviewed_by_email,omitempty"`
	ReviewedAt      *time.Time `json:"reviewed_at,omitempty"`
	CreatedAt       time.Time  `json:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at"`
}

// Decision is an administrator moving a request through the queue.
type Decision struct {
	Status    string
	Reason    string
	DatasetID string
	Note      string
	// The administrator's address, kept for the same reason the asker's is.
	ReviewedByEmail string
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
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS dataset_id TEXT;
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS reviewed_by_email TEXT;
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE;
`

// columns is what every read selects, in the order scan expects.
const columns = `
	suggestion_id, title, url, cadence, COALESCE(description, ''), status,
	COALESCE(reason, ''), COALESCE(dataset_id, ''), COALESCE(note, ''),
	requested_by, requested_by_email, COALESCE(reviewed_by_email, ''), reviewed_at,
	created_at, updated_at`

type scanner interface {
	Scan(dest ...any) error
}

func scan(row scanner) (Suggestion, error) {
	var entry Suggestion
	var reviewedAt sql.NullTime
	err := row.Scan(&entry.ID, &entry.Title, &entry.URL, &entry.Cadence,
		&entry.Description, &entry.Status, &entry.Reason, &entry.DatasetID, &entry.Note,
		&entry.RequestedBy, &entry.RequestedByEmail, &entry.ReviewedByEmail, &reviewedAt,
		&entry.CreatedAt, &entry.UpdatedAt)
	if reviewedAt.Valid {
		at := reviewedAt.Time.UTC()
		entry.ReviewedAt = &at
	}
	return entry, err
}

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
		SELECT `+columns+`
		FROM suggestions
		ORDER BY status = 'open' DESC, created_at DESC
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []Suggestion{}
	for rows.Next() {
		entry, err := scan(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, entry)
	}
	return out, rows.Err()
}

// Decide moves a request through the queue.
//
// Only an administrator may (the route checks), and what they may say is
// constrained so the queue can be read without asking anyone: a declined
// request always says why, one declined because the warehouse already holds
// it always says where, and reopening a request clears what the last decision
// said rather than leaving a stale reason beside an "open".
func (s *Store) Decide(ctx context.Context, id string, decision Decision) (Suggestion, error) {
	decision.Reason = strings.TrimSpace(decision.Reason)
	decision.DatasetID = strings.TrimSpace(decision.DatasetID)
	decision.Note = strings.TrimSpace(decision.Note)

	if !valid(decision.Status, Statuses) {
		return Suggestion{}, fmt.Errorf(
			"status: %q is not one of %s", decision.Status, strings.Join(Statuses, ", "))
	}
	switch decision.Status {
	case "declined":
		if !valid(decision.Reason, Reasons) {
			return Suggestion{}, fmt.Errorf(
				"reason: a declined request says why, one of %s", strings.Join(Reasons, ", "))
		}
		if decision.Reason == "already-collected" && decision.DatasetID == "" {
			return Suggestion{}, errors.New(
				"dataset_id: name the dataset that already holds it, so the asker can open it")
		}
		if decision.Reason != "already-collected" {
			decision.DatasetID = ""
		}
	case "ingested":
		decision.Reason = ""
	default:
		if decision.Reason != "" {
			return Suggestion{}, errors.New("reason: only a declined request has one")
		}
		decision.DatasetID = ""
	}
	if decision.Status == "open" {
		decision.Note = ""
	}

	now := time.Now().UTC()
	result, err := s.db.ExecContext(ctx, `
		UPDATE suggestions
		SET status = ?, reason = ?, dataset_id = ?, note = ?,
		    reviewed_by_email = ?, reviewed_at = ?, updated_at = ?
		WHERE suggestion_id = ?`,
		decision.Status, nullable(decision.Reason), nullable(decision.DatasetID),
		nullable(decision.Note), nullable(decision.ReviewedByEmail), now, now, id)
	if err != nil {
		return Suggestion{}, err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return Suggestion{}, ErrNotFound
	}
	return s.Get(ctx, id)
}

func (s *Store) Get(ctx context.Context, id string) (Suggestion, error) {
	entry, err := scan(s.db.QueryRowContext(ctx,
		`SELECT `+columns+` FROM suggestions WHERE suggestion_id = ?`, id))
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
