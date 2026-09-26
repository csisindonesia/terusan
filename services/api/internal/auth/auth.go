// Package auth is who is asking.
//
// An email address, a password, and a session cookie. Three roles, and they
// grant little on purpose:
//
//   - admin       manages accounts — creates them, approves the requests that
//     come in through the registration form, changes roles.
//   - researcher  the ordinary account: reads the warehouse, keeps a shelf.
//   - guest       a researcher with an end date. After access_expires_at the
//     account stops signing in, and every session and API token
//     it holds stops working with it.
//
// There are no organisations and no per-record permissions: everyone who can
// sign in sees the same warehouse. The schema in db/migrations has the shape
// the fuller model takes when there is something to scope (program.md §35,
// §36).
//
// Passwords are stored as PBKDF2-SHA256 with a per-password salt, from the
// standard library. Not because PBKDF2 is the best of the modern choices —
// Argon2id is — but because it is in `crypto/pbkdf2` as of Go 1.24, and a
// dependency-free hash that is configured correctly beats a better one that a
// deployment has to remember to vendor. The encoding carries its parameters,
// so raising the work factor later re-hashes on next login rather than
// invalidating every account.
//
// Sessions are opaque random tokens. What the database holds is the SHA-256 of
// the token, never the token itself: a leaked database then yields no cookie
// anybody can present, which is the same reason the password is not in there
// either. API tokens (tokens.go) are the same idea for callers without a
// browser.
package auth

import (
	"context"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/mail"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/csis/terusan/services/api/internal/appdb"
)

var (
	// ErrInvalidCredentials is returned for both a wrong password and an email
	// nobody has. One error for both on purpose: telling them apart is telling
	// an attacker which addresses are worth attacking.
	ErrInvalidCredentials = errors.New("invalid email or password")
	// ErrDisabled is an account that exists and may not be used.
	ErrDisabled = errors.New("this account is disabled")
	// ErrNoSession is a missing, unknown or expired session token.
	ErrNoSession = errors.New("no session")
	// ErrTooManyAttempts is returned while a login is rate-limited.
	ErrTooManyAttempts = errors.New("too many attempts")
	// ErrWeakPassword is returned when a password is too short to store.
	ErrWeakPassword = errors.New("password is too short")
	// ErrNotFound is an account this deployment does not have.
	ErrNotFound = errors.New("no such account")
	// ErrPending is an account that asked to join and has not been approved.
	ErrPending = errors.New("this account is waiting for an administrator to approve it")
	// ErrExpired is a guest whose access period has ended.
	ErrExpired = errors.New("this account's access period has ended")
)

// The roles an account can hold.
const (
	RoleAdmin      = "admin"
	RoleResearcher = "researcher"
	RoleGuest      = "guest"
)

// ValidRole reports whether this is one of the three roles.
func ValidRole(role string) bool {
	return role == RoleAdmin || role == RoleResearcher || role == RoleGuest
}

// Account states. Pending is a registration nobody has approved yet; it can
// hold no session.
const (
	StatusActive  = "active"
	StatusPending = "pending"
)

// User is an account, as every surface above this sees it. The password hash
// never leaves this package.
type User struct {
	ID    string `json:"id"`
	Email string `json:"email"`
	Name  string `json:"name,omitempty"`
	// admin, researcher or guest (see the package comment).
	Role        string     `json:"role"`
	Department  string     `json:"department,omitempty"`
	Status      string     `json:"status"`
	CreatedAt   time.Time  `json:"created_at"`
	LastLoginAt *time.Time `json:"last_login_at,omitempty"`
	// The last time any request arrived as this person, to the few minutes
	// the access log is kept at (see access.go).
	LastActiveAt *time.Time `json:"last_active_at,omitempty"`
	// When a guest's access ends. Nil for every other role.
	AccessExpiresAt *time.Time `json:"access_expires_at,omitempty"`
	Disabled        bool       `json:"disabled,omitempty"`
}

// usable is whether this account may hold a session right now, and if not,
// why not.
func (u User) usable(now time.Time) error {
	switch {
	case u.Disabled:
		return ErrDisabled
	case u.Status == StatusPending:
		return ErrPending
	case u.Role == RoleGuest && u.AccessExpiresAt != nil && !now.Before(*u.AccessExpiresAt):
		return ErrExpired
	}
	return nil
}

// Session is a live login.
type Session struct {
	User User `json:"user"`
	// Which of this person's logins this is, so the account page can mark the
	// row a reader is reading it from as "this browser".
	ID        string    `json:"id"`
	ExpiresAt time.Time `json:"expires_at"`
}

// Login is one row in "where am I signed in".
type LoginRecord struct {
	ID string `json:"id"`
	// What the browser called itself. Kept raw rather than parsed into "Chrome
	// on macOS": the parsing is guesswork, and a reader recognises their own
	// user agent well enough to tell one machine from another.
	UserAgent string    `json:"user_agent,omitempty"`
	CreatedAt time.Time `json:"created_at"`
	ExpiresAt time.Time `json:"expires_at"`
	// Whether this is the session asking.
	Current bool `json:"current"`
}

// Config is how long a login lasts and how hard a password is to check.
type Config struct {
	// How long a session lasts by default — one working day, so a browser left
	// open overnight in a shared room is not still logged in at 09:00.
	TTL time.Duration
	// How long "remember me" lasts instead.
	RememberTTL time.Duration
	// PBKDF2 iterations for new passwords. OWASP's floor for PBKDF2-HMAC-SHA256
	// was 600,000 at the time of writing; it goes up, never down.
	Iterations int
}

// Defaults fills in whatever a caller left at zero.
func (c Config) withDefaults() Config {
	if c.TTL <= 0 {
		c.TTL = 12 * time.Hour
	}
	if c.RememberTTL <= 0 {
		c.RememberTTL = 30 * 24 * time.Hour
	}
	if c.Iterations <= 0 {
		c.Iterations = 600_000
	}
	return c
}

// Service is the accounts and sessions in the application database.
type Service struct {
	db  *appdb.DB
	cfg Config

	// Failed attempts, in memory. Deliberately not in the database: this is a
	// speed bump against guessing, it resets when the process does, and a
	// write per failed password would hand an attacker a way to make the
	// database do work.
	mu       sync.Mutex
	failures map[string]*attempts
	// When each (account, address, kind) was last written to the access log,
	// so a busy page is one row per few minutes rather than one per request.
	touched map[string]time.Time
}

type attempts struct {
	count int
	until time.Time
}

const schema = `
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    -- Stored lowercase, because an address is one address however it was
    -- typed, and "somebody logged in as Dev@ and cannot see what dev@ saved"
    -- is a support ticket nobody enjoys.
    email          TEXT NOT NULL UNIQUE,
    name           TEXT,
    role           TEXT NOT NULL DEFAULT 'member',
    -- pbkdf2-sha256$<iterations>$<salt>$<key>, base64, parameters included so
    -- the work factor can be raised without invalidating anyone.
    password_hash  TEXT NOT NULL,
    disabled       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    last_login_at  TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS sessions (
    -- The SHA-256 of the token, never the token. A database that leaks hands
    -- over nothing anybody can present as a cookie.
    token_hash   TEXT PRIMARY KEY,
    -- A name for the session that is safe to put in a URL, so a person can end
    -- one from the list of their logins without the token — or its hash — ever
    -- appearing in a page, a log or a browser history.
    session_id   TEXT,
    user_id      TEXT NOT NULL,
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    -- What asked for it, so a person can be told where they are logged in.
    user_agent   TEXT
);
`

// Columns added after the table first shipped. DuckDB has no migration
// runner here and this is a handful of rows, so each is added if it is
// missing and the error is ignored where the engine reports it differently.
var additions = []string{
	`ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_id TEXT`,
	`ALTER TABLE users ADD COLUMN IF NOT EXISTS department TEXT`,
	`ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT`,
	`ALTER TABLE users ADD COLUMN IF NOT EXISTS access_expires_at TIMESTAMP WITH TIME ZONE`,
	`ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP WITH TIME ZONE`,
	// Every account that existed before approval did was an approved one.
	`UPDATE users SET status = 'active' WHERE status IS NULL`,
	// "member" was the only non-admin role before there were three.
	`UPDATE users SET role = 'researcher' WHERE role NOT IN ('admin', 'researcher', 'guest')`,
}

// New prepares the accounts tables in the application database.
func New(db *appdb.DB, cfg Config) (*Service, error) {
	if db == nil {
		return nil, errors.New("no database")
	}
	if _, err := db.Exec(schema + tokenSchema + accessSchema); err != nil {
		return nil, fmt.Errorf("auth schema: %w", err)
	}
	for _, statement := range additions {
		if _, err := db.Exec(statement); err != nil {
			return nil, fmt.Errorf("auth schema: %w", err)
		}
	}
	// Sessions written before session_id existed get one now, so a person who
	// was already logged in can still see and end that login.
	if _, err := db.Exec(
		`UPDATE sessions SET session_id = md5(token_hash) WHERE session_id IS NULL`,
	); err != nil {
		return nil, fmt.Errorf("auth schema: %w", err)
	}
	return &Service{
		db: db, cfg: cfg.withDefaults(),
		failures: map[string]*attempts{}, touched: map[string]time.Time{},
	}, nil
}

// Count is how many accounts exist, which is what tells a fresh deployment
// from one somebody has set up.
func (s *Service) Count(ctx context.Context) (int, error) {
	var count int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM users`).Scan(&count)
	return count, err
}

// NormalizeEmail is the stored form of an address: trimmed and lowercased.
func NormalizeEmail(email string) string {
	return strings.ToLower(strings.TrimSpace(email))
}

// ValidEmail reports whether this is an address at all. Deliberately lax —
// the only authority on whether an address works is sending to it — but it
// keeps a typo out of the accounts table.
func ValidEmail(email string) bool {
	address, err := mail.ParseAddress(email)
	return err == nil && address.Address == email
}

// ---- passwords ------------------------------------------------------------

// MinPasswordLength is the shortest password this will store.
//
// Length rather than a composition rule: "at least one symbol" produces
// Password1! and nothing else, where a long passphrase is both easier to
// remember and harder to guess (NIST SP 800-63B).
const MinPasswordLength = 12

// HashPassword returns the stored encoding of a password.
func (s *Service) HashPassword(password string) (string, error) {
	if len([]rune(password)) < MinPasswordLength {
		return "", ErrWeakPassword
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key, err := pbkdf2.Key(sha256.New, password, salt, s.cfg.Iterations, 32)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("pbkdf2-sha256$%d$%s$%s",
		s.cfg.Iterations,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key),
	), nil
}

// verifyPassword checks a password against a stored encoding.
//
// The comparison is constant-time, and so is the failure path as far as it can
// be: an unparseable encoding still costs a hash, so a row written by an older
// or broken version cannot be told from a wrong password by timing it.
func verifyPassword(encoded, password string) bool {
	parts := strings.Split(encoded, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2-sha256" {
		return false
	}
	iterations, err := strconv.Atoi(parts[1])
	if err != nil || iterations <= 0 {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[2])
	if err != nil {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[3])
	if err != nil {
		return false
	}
	got, err := pbkdf2.Key(sha256.New, password, salt, iterations, len(want))
	if err != nil {
		return false
	}
	return subtle.ConstantTimeCompare(got, want) == 1
}

// ---- accounts -------------------------------------------------------------

// PutUser creates an account or resets an existing one's password.
//
// Used by `authctl` and the bootstrap route. The account is active at once:
// whoever runs these is already trusted with the database. Accounts from the
// portal come in through CreateUser (an admin) or Register (a request an admin
// approves), both in admin.go.
func (s *Service) PutUser(ctx context.Context, email, password, name, role string) (User, error) {
	address := NormalizeEmail(email)
	if !ValidEmail(address) {
		return User{}, fmt.Errorf("%q is not an email address", email)
	}
	hash, err := s.HashPassword(password)
	if err != nil {
		return User{}, err
	}
	if role == "" {
		role = RoleResearcher
	}
	if !ValidRole(role) || role == RoleGuest {
		// A guest needs an end date, which this path has no way to take.
		return User{}, fmt.Errorf("role must be %s or %s here; make guests from the portal",
			RoleAdmin, RoleResearcher)
	}

	now := time.Now().UTC()
	existing, err := s.byEmail(ctx, address)
	switch {
	case err == nil:
		if name == "" {
			name = existing.Name
		}
		if _, err := s.db.ExecContext(ctx, `
			UPDATE users SET password_hash = ?, name = ?, role = ?, status = 'active',
			    access_expires_at = NULL, updated_at = ?
			WHERE user_id = ?`, hash, nullable(name), role, now, existing.ID); err != nil {
			return User{}, err
		}
		existing.Name = name
		existing.Role = role
		existing.Status = StatusActive
		existing.AccessExpiresAt = nil
		return existing, nil

	case errors.Is(err, ErrNotFound):
		user := User{
			ID:        uuid.NewString(),
			Email:     address,
			Name:      name,
			Role:      role,
			Status:    StatusActive,
			CreatedAt: now,
		}
		if _, err := s.db.ExecContext(ctx, `
			INSERT INTO users (user_id, email, name, role, password_hash, disabled, created_at, updated_at, status)
			VALUES (?, ?, ?, ?, ?, FALSE, ?, ?, 'active')`,
			user.ID, user.Email, nullable(user.Name), user.Role, hash, now, now); err != nil {
			return User{}, err
		}
		return user, nil

	default:
		return User{}, err
	}
}

// SetDisabled switches an account off without deleting it, which is what
// "somebody left" usually means — their sessions end and the audit trail of
// what they did stays attached to a row that still exists.
func (s *Service) SetDisabled(ctx context.Context, email string, disabled bool) error {
	user, err := s.byEmail(ctx, NormalizeEmail(email))
	if err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET disabled = ?, updated_at = ? WHERE user_id = ?`,
		disabled, time.Now().UTC(), user.ID); err != nil {
		return err
	}
	if disabled {
		_, err = s.db.ExecContext(ctx, `DELETE FROM sessions WHERE user_id = ?`, user.ID)
	}
	return err
}

// Users lists the accounts, for `authctl list`.
func (s *Service) Users(ctx context.Context) ([]User, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+userColumns+` FROM users u ORDER BY u.email`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []User{}
	for rows.Next() {
		user, err := scanUser(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, user)
	}
	return out, rows.Err()
}

type scanner interface {
	Scan(dest ...any) error
}

// userColumns is what scanUser reads, in its order, off a `users u`.
const userColumns = `u.user_id, u.email, COALESCE(u.name, ''), u.role, u.disabled,
	u.created_at, u.last_login_at, COALESCE(u.department, ''),
	COALESCE(u.status, 'active'), u.access_expires_at, u.last_active_at`

// scanUser reads userColumns, then whatever extra destinations the query
// selected after them.
func scanUser(row scanner, extra ...any) (User, error) {
	var user User
	var lastLogin, expires, lastActive sql.NullTime
	dest := append([]any{&user.ID, &user.Email, &user.Name, &user.Role,
		&user.Disabled, &user.CreatedAt, &lastLogin, &user.Department,
		&user.Status, &expires, &lastActive}, extra...)
	if err := row.Scan(dest...); err != nil {
		return User{}, err
	}
	user.CreatedAt = user.CreatedAt.UTC()
	user.LastLoginAt = utcPointer(lastLogin)
	user.AccessExpiresAt = utcPointer(expires)
	user.LastActiveAt = utcPointer(lastActive)
	return user, nil
}

func utcPointer(value sql.NullTime) *time.Time {
	if !value.Valid {
		return nil
	}
	at := value.Time.UTC()
	return &at
}

func (s *Service) byEmail(ctx context.Context, address string) (User, error) {
	row := s.db.QueryRowContext(ctx, `SELECT `+userColumns+` FROM users u WHERE u.email = ?`, address)
	user, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return user, err
}

// ---- logging in -----------------------------------------------------------

// Login checks a password and starts a session.
//
// Returns the token to put in a cookie. It is returned once and never stored,
// so this is the only moment anything can read it.
func (s *Service) Login(
	ctx context.Context, email, password, userAgent string, remember bool,
) (token string, session Session, err error) {
	address := NormalizeEmail(email)
	if blocked, wait := s.blocked(address); blocked {
		return "", Session{}, fmt.Errorf("%w: try again in %s",
			ErrTooManyAttempts, wait.Round(time.Second))
	}

	var hash string
	row := s.db.QueryRowContext(ctx,
		`SELECT `+userColumns+`, u.password_hash FROM users u WHERE u.email = ?`, address)
	user, err := scanUser(row, &hash)
	if errors.Is(err, sql.ErrNoRows) {
		// Hashed anyway, against a throwaway encoding, so that "no such
		// account" and "wrong password" take the same time to answer.
		verifyPassword(decoyHash, password)
		s.failed(address)
		return "", Session{}, ErrInvalidCredentials
	}
	if err != nil {
		return "", Session{}, err
	}

	if !verifyPassword(hash, password) {
		s.failed(address)
		return "", Session{}, ErrInvalidCredentials
	}
	// Checked after the password, so a wrong password on a disabled, pending
	// or lapsed account does not reveal that the account exists.
	if err := user.usable(time.Now()); err != nil {
		return "", Session{}, err
	}
	s.succeeded(address)

	token, id, expires, err := s.startSession(ctx, user.ID, userAgent, remember)
	if err != nil {
		return "", Session{}, err
	}
	session.ID = id

	now := time.Now().UTC()
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET last_login_at = ? WHERE user_id = ?`, now, user.ID); err != nil {
		return "", Session{}, err
	}
	user.LastLoginAt = &now

	session.User = user
	session.ExpiresAt = expires
	return token, session, nil
}

// A valid encoding of a password nobody has, hashed on the "no such account"
// path so that probing for addresses cannot be done with a stopwatch.
const decoyHash = "pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$" +
	"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

func (s *Service) startSession(
	ctx context.Context, userID, userAgent string, remember bool,
) (token string, id string, expires time.Time, err error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", "", time.Time{}, err
	}
	token = base64.RawURLEncoding.EncodeToString(raw)
	id = uuid.NewString()

	ttl := s.cfg.TTL
	if remember {
		ttl = s.cfg.RememberTTL
	}
	now := time.Now().UTC()
	expires = now.Add(ttl)

	if len(userAgent) > 400 {
		userAgent = userAgent[:400]
	}
	if _, err := s.db.ExecContext(ctx, `
		INSERT INTO sessions (token_hash, session_id, user_id, created_at, expires_at, user_agent)
		VALUES (?, ?, ?, ?, ?, ?)`,
		hashToken(token), id, userID, now, expires, nullable(userAgent)); err != nil {
		return "", "", time.Time{}, err
	}

	// Expired rows are swept here rather than by a timer: logins are the only
	// thing that makes them, so they are also the right moment to clear them,
	// and a process with no logins has no sessions to sweep.
	if _, err := s.db.ExecContext(ctx,
		`DELETE FROM sessions WHERE expires_at < ?`, now); err != nil {
		return "", "", time.Time{}, err
	}
	return token, id, expires, nil
}

// Session resolves a token to whoever holds it.
func (s *Service) Session(ctx context.Context, token string) (Session, error) {
	if token == "" {
		return Session{}, ErrNoSession
	}
	var session Session
	var expires time.Time
	var id sql.NullString

	user, err := scanUser(s.db.QueryRowContext(ctx, `
		SELECT `+userColumns+`, s.expires_at, s.session_id
		FROM sessions s JOIN users u ON u.user_id = s.user_id
		WHERE s.token_hash = ?`, hashToken(token)), &expires, &id)
	if errors.Is(err, sql.ErrNoRows) {
		return Session{}, ErrNoSession
	}
	if err != nil {
		return Session{}, err
	}

	now := time.Now().UTC()
	if now.After(expires) {
		// Cleared on the way past rather than left to a sweep: an expired
		// session that is still being presented is exactly the row worth
		// removing now.
		_, _ = s.db.ExecContext(ctx, `DELETE FROM sessions WHERE token_hash = ?`, hashToken(token))
		return Session{}, ErrNoSession
	}
	if err := user.usable(now); err != nil {
		return Session{}, err
	}
	session.User = user
	session.ID = id.String
	session.ExpiresAt = expires.UTC()
	return session, nil
}

// Logout ends one session. Unknown tokens are not an error: the caller is
// asking to be logged out, and it already is.
func (s *Service) Logout(ctx context.Context, token string) error {
	if token == "" {
		return nil
	}
	_, err := s.db.ExecContext(ctx, `DELETE FROM sessions WHERE token_hash = ?`, hashToken(token))
	return err
}

// LogoutEverywhere ends every session a person holds, which is what a stolen
// laptop calls for.
func (s *Service) LogoutEverywhere(ctx context.Context, userID string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM sessions WHERE user_id = ?`, userID)
	return err
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

// ---- the account's own settings -------------------------------------------

// Rename changes what a person is called. The address is not editable here:
// it is the identity the account is addressed by, and changing it is an
// administrative act (`authctl`), not a preference.
func (s *Service) Rename(ctx context.Context, userID, name string) (User, error) {
	name = strings.TrimSpace(name)
	if len([]rune(name)) > 120 {
		return User{}, errors.New("that name is too long")
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET name = ?, updated_at = ? WHERE user_id = ?`,
		nullable(name), time.Now().UTC(), userID); err != nil {
		return User{}, err
	}
	return s.byID(ctx, userID)
}

func (s *Service) byID(ctx context.Context, userID string) (User, error) {
	row := s.db.QueryRowContext(ctx, `SELECT `+userColumns+` FROM users u WHERE u.user_id = ?`, userID)
	user, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return user, err
}

// ChangePassword replaces a password, given the current one.
//
// Every other session is ended by it. The reason to change a password is
// usually that somebody else might know the old one, and leaving their login
// alive would make the change cosmetic; the session doing the changing is kept
// so the person is not thrown out of the page they are standing on.
func (s *Service) ChangePassword(
	ctx context.Context, userID, current, next, keepToken string,
) error {
	var hash string
	err := s.db.QueryRowContext(ctx,
		`SELECT password_hash FROM users WHERE user_id = ?`, userID).Scan(&hash)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if !verifyPassword(hash, current) {
		return ErrInvalidCredentials
	}
	if current == next {
		return errors.New("the new password is the old one")
	}

	encoded, err := s.HashPassword(next)
	if err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?`,
		encoded, time.Now().UTC(), userID); err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx,
		`DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?`,
		userID, hashToken(keepToken))
	return err
}

// Logins is everywhere this person is currently signed in.
func (s *Service) Logins(ctx context.Context, userID, currentToken string) ([]LoginRecord, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT COALESCE(session_id, ''), COALESCE(user_agent, ''), created_at, expires_at,
		       token_hash = ? AS current
		FROM sessions
		WHERE user_id = ? AND expires_at > ?
		ORDER BY created_at DESC`,
		hashToken(currentToken), userID, time.Now().UTC())
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []LoginRecord{}
	for rows.Next() {
		var record LoginRecord
		if err := rows.Scan(&record.ID, &record.UserAgent,
			&record.CreatedAt, &record.ExpiresAt, &record.Current); err != nil {
			return nil, err
		}
		out = append(out, record)
	}
	return out, rows.Err()
}

// RevokeLogin ends one of this person's sessions.
//
// Scoped to the user on purpose: the id comes out of a URL, and without the
// scope anybody with a session could end anybody else's by guessing one.
func (s *Service) RevokeLogin(ctx context.Context, userID, sessionID string) error {
	result, err := s.db.ExecContext(ctx,
		`DELETE FROM sessions WHERE user_id = ? AND session_id = ?`, userID, sessionID)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return ErrNoSession
	}
	return nil
}

// RevokeOthers ends every session but the one asking — the "I left myself
// logged in somewhere" button.
func (s *Service) RevokeOthers(ctx context.Context, userID, keepToken string) (int64, error) {
	result, err := s.db.ExecContext(ctx,
		`DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?`,
		userID, hashToken(keepToken))
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

// ---- rate limiting --------------------------------------------------------

// How many wrong passwords before an address is made to wait, and for how
// long. Per address rather than per connection: an attacker changes address
// far less easily than they change IP.
const (
	maxAttempts = 8
	lockout     = 2 * time.Minute
)

func (s *Service) blocked(address string) (bool, time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.failures[address]
	if entry == nil || entry.until.IsZero() {
		return false, 0
	}
	if wait := time.Until(entry.until); wait > 0 {
		return true, wait
	}
	delete(s.failures, address)
	return false, 0
}

func (s *Service) failed(address string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.failures[address]
	if entry == nil {
		entry = &attempts{}
		s.failures[address] = entry
	}
	entry.count++
	if entry.count >= maxAttempts {
		entry.until = time.Now().Add(lockout)
		entry.count = 0
	}
}

func (s *Service) succeeded(address string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.failures, address)
}

func nullable(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
