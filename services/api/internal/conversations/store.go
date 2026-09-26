// Package conversations keeps the assistant's chats, so one can be reopened
// from its URL and carried on.
//
// A chat is addressed by a random UUID and holds its turns in order. The
// server records them itself — the question as it arrives, the reply as it
// finished streaming — so a conversation is the same thing in every browser
// that opens it, and a reply cut off by a closed tab is still there, marked as
// cut off, rather than lost.
//
// Who may open one: a chat started while signed in belongs to that account,
// and to nobody else. A chat started without an account belongs to nobody,
// and its UUID is the key to it — 122 random bits cannot be guessed, and a
// link is exactly what a reader wants to be able to hand over. What these
// hold is questions about public statistics, which is what makes that trade
// reasonable here.
package conversations

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/csis/terusan/services/api/internal/appdb"
)

// ErrNotFound is a chat this deployment does not hold, or one the caller may
// not see — the same answer for both, so an id cannot be probed for.
var ErrNotFound = errors.New("no such conversation")

const (
	// How many turns one chat may hold. A conversation longer than this is
	// better started again; the assistant only reads the last few anyway.
	MaxMessages = 200
	// How many chats a listing returns.
	listLimit = 100
)

// Message is one turn.
type Message struct {
	Seq     int    `json:"seq"`
	Role    string `json:"role"`
	Content string `json:"content"`
	// Set on a reply that did not finish: the model failed, or the reader
	// stopped it.
	Error string `json:"error,omitempty"`
	// What the reply was allowed to link to, as `kind:id`.
	Sources []string `json:"sources,omitempty"`
	// The collection this reply's suggestions were saved into.
	CollectionID string    `json:"collection_id,omitempty"`
	CreatedAt    time.Time `json:"created_at"`
}

// Conversation is one chat, with its turns when read whole.
type Conversation struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
	Messages  []Message `json:"messages,omitempty"`

	owner string
}

type Store struct {
	db *appdb.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS assistant_conversations (
    conversation_id  TEXT PRIMARY KEY,
    -- The account that started it, or '' for one started signed out, which
    -- anybody holding the id may open.
    owner_id         TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL,
    created_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at       TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE TABLE IF NOT EXISTS assistant_messages (
    conversation_id  TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    error            TEXT,
    -- A JSON list of kind:id, which is all anything reads it as.
    sources          TEXT,
    collection_id    TEXT,
    created_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
`

func New(db *appdb.DB) (*Store, error) {
	if db == nil {
		return nil, errors.New("no database")
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, fmt.Errorf("conversations schema: %w", err)
	}
	return &Store{db: db}, nil
}

// Create starts a chat for owner ("" for a reader without an account).
func (s *Store) Create(ctx context.Context, owner, title string) (Conversation, error) {
	now := time.Now().UTC()
	c := Conversation{
		ID:        uuid.NewString(),
		Title:     titleFor(title),
		CreatedAt: now,
		UpdatedAt: now,
		owner:     owner,
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO assistant_conversations (conversation_id, owner_id, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?)`, c.ID, owner, c.Title, now, now)
	return c, err
}

// Get reads one chat whole, if viewer may see it.
func (s *Store) Get(ctx context.Context, id, viewer string) (Conversation, error) {
	if _, err := uuid.Parse(id); err != nil {
		return Conversation{}, ErrNotFound
	}
	var c Conversation
	err := s.db.QueryRowContext(ctx, `
		SELECT conversation_id, owner_id, title, created_at, updated_at
		FROM assistant_conversations WHERE conversation_id = ?`, id,
	).Scan(&c.ID, &c.owner, &c.Title, &c.CreatedAt, &c.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Conversation{}, ErrNotFound
	}
	if err != nil {
		return Conversation{}, err
	}
	if c.owner != "" && c.owner != viewer {
		return Conversation{}, ErrNotFound
	}

	rows, err := s.db.QueryContext(ctx, `
		SELECT seq, role, content, coalesce(error, ''), coalesce(sources, ''),
		       coalesce(collection_id, ''), created_at
		FROM assistant_messages WHERE conversation_id = ? ORDER BY seq`, id)
	if err != nil {
		return Conversation{}, err
	}
	defer rows.Close()
	c.Messages = []Message{}
	for rows.Next() {
		var m Message
		var sources string
		if err := rows.Scan(&m.Seq, &m.Role, &m.Content, &m.Error, &sources,
			&m.CollectionID, &m.CreatedAt); err != nil {
			return Conversation{}, err
		}
		if sources != "" {
			_ = json.Unmarshal([]byte(sources), &m.Sources)
		}
		c.Messages = append(c.Messages, m)
	}
	return c, rows.Err()
}

// List is owner's chats, the latest first, without their turns.
func (s *Store) List(ctx context.Context, owner string) ([]Conversation, error) {
	if owner == "" {
		// Chats without an account are listed by the browser that started
		// them, which knows their ids; there is no "everyone's" list.
		return []Conversation{}, nil
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT conversation_id, title, created_at, updated_at
		FROM assistant_conversations WHERE owner_id = ?
		ORDER BY updated_at DESC LIMIT ?`, owner, listLimit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Conversation{}
	for rows.Next() {
		var c Conversation
		if err := rows.Scan(&c.ID, &c.Title, &c.CreatedAt, &c.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// Append adds a turn to the end of a chat and returns it numbered.
func (s *Store) Append(ctx context.Context, id string, m Message) (Message, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Message{}, err
	}
	defer tx.Rollback()

	var next int
	if err := tx.QueryRowContext(ctx, `
		SELECT coalesce(max(seq) + 1, 0) FROM assistant_messages WHERE conversation_id = ?`, id,
	).Scan(&next); err != nil {
		return Message{}, err
	}
	if next >= MaxMessages {
		return Message{}, fmt.Errorf("this conversation has reached %d messages; start a new one", MaxMessages)
	}

	m.Seq = next
	m.CreatedAt = time.Now().UTC()
	var sources any
	if len(m.Sources) > 0 {
		encoded, _ := json.Marshal(m.Sources)
		sources = string(encoded)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO assistant_messages (conversation_id, seq, role, content, error, sources, collection_id, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		id, m.Seq, m.Role, m.Content, nullable(m.Error), sources, nullable(m.CollectionID), m.CreatedAt,
	); err != nil {
		return Message{}, err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE assistant_conversations SET updated_at = ? WHERE conversation_id = ?`,
		m.CreatedAt, id); err != nil {
		return Message{}, err
	}
	return m, tx.Commit()
}

// TruncateAfter drops every turn after seq, for a regenerated reply.
func (s *Store) TruncateAfter(ctx context.Context, id string, seq int) error {
	_, err := s.db.ExecContext(ctx, `
		DELETE FROM assistant_messages WHERE conversation_id = ? AND seq > ?`, id, seq)
	return err
}

// SetCollection records the collection a reply's suggestions were saved to.
func (s *Store) SetCollection(ctx context.Context, id, viewer string, seq int, collectionID string) error {
	if _, err := s.Get(ctx, id, viewer); err != nil {
		return err
	}
	result, err := s.db.ExecContext(ctx, `
		UPDATE assistant_messages SET collection_id = ?
		WHERE conversation_id = ? AND seq = ? AND role = 'assistant'`,
		nullable(collectionID), id, seq)
	if err != nil {
		return err
	}
	if n, _ := result.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// Delete removes a chat and its turns, if viewer may see it.
func (s *Store) Delete(ctx context.Context, id, viewer string) error {
	if _, err := s.Get(ctx, id, viewer); err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx,
		`DELETE FROM assistant_messages WHERE conversation_id = ?`, id); err != nil {
		return err
	}
	_, err := s.db.ExecContext(ctx,
		`DELETE FROM assistant_conversations WHERE conversation_id = ?`, id)
	return err
}

// titleFor is a chat's name: its first question, cut short.
func titleFor(question string) string {
	flat := strings.Join(strings.Fields(question), " ")
	if flat == "" {
		return "New chat"
	}
	if runes := []rune(flat); len(runes) > 60 {
		return string(runes[:57]) + "…"
	}
	return flat
}

func nullable(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
