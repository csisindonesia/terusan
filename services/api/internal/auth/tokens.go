package auth

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

// API tokens: how a script, a notebook or another service reads the warehouse
// as a named account without a browser to hold a cookie.
//
// A token belongs to one account and is worth exactly what that account's
// session is worth — no scopes, because nothing else here has any either. It
// is shown once, when it is made, and the database keeps only its SHA-256, the
// same as a session: a leaked database yields nothing anybody can present.
//
// Tokens outlive a password change on purpose. A script that breaks because
// somebody rotated their password is the kind of surprise that gets tokens
// pasted into places they should not be; the account page lists every token,
// and ending one is a click. Disabling the account ends all of them at once,
// because every lookup joins through the account.

// TokenPrefix marks a string as a Terusan API token. It lets a secret scanner
// recognise one in a commit, and lets the API tell a token from a mistyped
// cookie before it touches the database.
const TokenPrefix = "trs_"

// Bounds on what a person can mint. A year, because a token that never
// expires is a token nobody remembers they issued; twenty, because more than
// that on one account is a sign they are being made instead of reused.
const (
	MaxTokenDays     = 365
	DefaultTokenDays = 90
	maxTokensPerUser = 20
)

var (
	// ErrTooManyTokens is returned when an account already holds the maximum.
	ErrTooManyTokens = errors.New("this account already has the maximum number of API tokens")
	// ErrNoToken is a token id this account does not hold.
	ErrNoToken = errors.New("no such API token")
)

// Token is one issued API token, as the account page lists it. The secret is
// never part of it: it exists only in the response that created it.
type Token struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	// The first few characters, so a person can match a row here to the value
	// in their script without the list ever showing a usable secret.
	Hint       string     `json:"hint"`
	CreatedAt  time.Time  `json:"created_at"`
	ExpiresAt  time.Time  `json:"expires_at"`
	LastUsedAt *time.Time `json:"last_used_at,omitempty"`
}

const tokenSchema = `
CREATE TABLE IF NOT EXISTS api_tokens (
    -- The SHA-256 of the token, never the token.
    token_hash    TEXT PRIMARY KEY,
    -- Safe to put in a URL, so a token can be revoked by id.
    token_id      TEXT NOT NULL UNIQUE,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    hint          TEXT NOT NULL,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    last_used_at  TIMESTAMP WITH TIME ZONE
);
`

// How stale last_used_at may get before a request refreshes it. A write per
// request would make every read a write; to the minute is what a person
// deciding whether a token is still in use needs.
const lastUsedResolution = time.Minute

// CreateToken issues a token for an account. The returned secret is the only
// copy there will ever be.
func (s *Service) CreateToken(
	ctx context.Context, userID, name string, days int,
) (secret string, token Token, err error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return "", Token{}, errors.New("a token needs a name, so it can be recognised later")
	}
	if len([]rune(name)) > 80 {
		return "", Token{}, errors.New("that name is too long")
	}
	if days == 0 {
		days = DefaultTokenDays
	}
	if days < 1 || days > MaxTokenDays {
		return "", Token{}, fmt.Errorf("expiry must be between 1 and %d days", MaxTokenDays)
	}

	var held int
	if err := s.db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM api_tokens WHERE user_id = ? AND expires_at > ?`,
		userID, time.Now().UTC()).Scan(&held); err != nil {
		return "", Token{}, err
	}
	if held >= maxTokensPerUser {
		return "", Token{}, ErrTooManyTokens
	}

	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", Token{}, err
	}
	secret = TokenPrefix + base64.RawURLEncoding.EncodeToString(raw)

	now := time.Now().UTC()
	token = Token{
		ID:        uuid.NewString(),
		Name:      name,
		Hint:      secret[:len(TokenPrefix)+6],
		CreatedAt: now,
		ExpiresAt: now.Add(time.Duration(days) * 24 * time.Hour),
	}
	if _, err := s.db.ExecContext(ctx, `
		INSERT INTO api_tokens (token_hash, token_id, user_id, name, hint, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		hashToken(secret), token.ID, userID, token.Name, token.Hint,
		token.CreatedAt, token.ExpiresAt); err != nil {
		return "", Token{}, err
	}
	// Swept here for the same reason sessions are swept at login: issuing is
	// the only thing that makes rows, so it is the right moment to clear them.
	if _, err := s.db.ExecContext(ctx,
		`DELETE FROM api_tokens WHERE expires_at < ?`, now); err != nil {
		return "", Token{}, err
	}
	return secret, token, nil
}

// TokenSession resolves an API token to the account holding it, in the same
// shape a cookie resolves to, so nothing past the middleware has to care
// which one a request carried.
func (s *Service) TokenSession(ctx context.Context, secret string) (Session, error) {
	if !strings.HasPrefix(secret, TokenPrefix) {
		return Session{}, ErrNoSession
	}
	var session Session
	var expires time.Time
	var lastUsed sql.NullTime

	hash := hashToken(secret)
	user, err := scanUser(s.db.QueryRowContext(ctx, `
		SELECT `+userColumns+`, t.token_id, t.expires_at, t.last_used_at
		FROM api_tokens t JOIN users u ON u.user_id = t.user_id
		WHERE t.token_hash = ?`, hash), &session.ID, &expires, &lastUsed)
	if errors.Is(err, sql.ErrNoRows) {
		return Session{}, ErrNoSession
	}
	if err != nil {
		return Session{}, err
	}

	now := time.Now().UTC()
	if now.After(expires) {
		_, _ = s.db.ExecContext(ctx, `DELETE FROM api_tokens WHERE token_hash = ?`, hash)
		return Session{}, ErrNoSession
	}
	// A guest's tokens end with the guest, whatever expiry they were made with.
	if err := user.usable(now); err != nil {
		return Session{}, err
	}
	if !lastUsed.Valid || now.Sub(lastUsed.Time) > lastUsedResolution {
		// Best effort: a request is not refused because its bookkeeping failed.
		_, _ = s.db.ExecContext(ctx,
			`UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?`, now, hash)
	}
	session.User = user
	session.ExpiresAt = expires.UTC()
	return session, nil
}

// Tokens lists an account's live tokens, newest first.
func (s *Service) Tokens(ctx context.Context, userID string) ([]Token, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT token_id, name, hint, created_at, expires_at, last_used_at
		FROM api_tokens
		WHERE user_id = ? AND expires_at > ?
		ORDER BY created_at DESC`, userID, time.Now().UTC())
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []Token{}
	for rows.Next() {
		var token Token
		var lastUsed sql.NullTime
		if err := rows.Scan(&token.ID, &token.Name, &token.Hint,
			&token.CreatedAt, &token.ExpiresAt, &lastUsed); err != nil {
			return nil, err
		}
		if lastUsed.Valid {
			at := lastUsed.Time.UTC()
			token.LastUsedAt = &at
		}
		out = append(out, token)
	}
	return out, rows.Err()
}

// RevokeToken ends one of an account's tokens. Scoped to the account in the
// query itself, for the same reason RevokeLogin is.
func (s *Service) RevokeToken(ctx context.Context, userID, tokenID string) error {
	result, err := s.db.ExecContext(ctx,
		`DELETE FROM api_tokens WHERE user_id = ? AND token_id = ?`, userID, tokenID)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return ErrNoToken
	}
	return nil
}

// UserByEmail is the account behind an address, for `authctl token`.
func (s *Service) UserByEmail(ctx context.Context, email string) (User, error) {
	return s.byEmail(ctx, NormalizeEmail(email))
}
