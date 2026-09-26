package auth

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

// Managing accounts: what an admin does from the Users page, and the one
// thing a stranger can do, which is ask to be let in.

var (
	// ErrExists is an address that already has an account.
	ErrExists = errors.New("an account with this email already exists")
	// ErrNotPending is an approval of an account nobody is waiting on.
	ErrNotPending = errors.New("this account is not waiting for approval")
	// ErrTooManyPending is the registration queue at its limit.
	ErrTooManyPending = errors.New("too many registrations are waiting for approval")
	// ErrGuestNeedsEnd is a guest without an end date in the future.
	ErrGuestNeedsEnd = invalidError("a guest needs an access end date in the future")
	// ErrInvalid matches every refusal that is the caller's input rather than
	// a fault here, so a handler can answer 400 with the message as written.
	ErrInvalid = errors.New("invalid input")
)

// invalidError is a refusal written for the person who made the request.
type invalidError string

func (e invalidError) Error() string        { return string(e) }
func (e invalidError) Is(target error) bool { return target == ErrInvalid }

func invalidf(format string, args ...any) error {
	return invalidError(fmt.Sprintf(format, args...))
}

// How many unapproved registrations may wait at once. A queue nobody is
// reading should fill up and stop, not become a way to write rows into the
// database from the open internet.
const maxPending = 200

// NewUser is an account an admin creates.
type NewUser struct {
	Email      string
	Name       string
	Department string
	Role       string
	// Empty to have one generated, which is then returned once.
	Password string
	// Required for a guest, ignored otherwise.
	AccessExpiresAt *time.Time
}

// CreateUser makes an active account. When no password is given one is
// generated and returned — the only time it is readable — for the admin to
// hand over.
func (s *Service) CreateUser(ctx context.Context, n NewUser) (User, string, error) {
	address := NormalizeEmail(n.Email)
	if !ValidEmail(address) {
		return User{}, "", invalidf("%q is not an email address", n.Email)
	}
	role := n.Role
	if role == "" {
		role = RoleResearcher
	}
	expires, err := checkRole(role, n.AccessExpiresAt)
	if err != nil {
		return User{}, "", err
	}
	if _, err := s.byEmail(ctx, address); err == nil {
		return User{}, "", ErrExists
	} else if !errors.Is(err, ErrNotFound) {
		return User{}, "", err
	}

	password, generated := n.Password, ""
	if password == "" {
		password = GeneratePassword()
		generated = password
	}
	hash, err := s.HashPassword(password)
	if err != nil {
		return User{}, "", err
	}
	id := uuid.NewString()
	if err := s.insertUser(ctx, id, address, n.Name, n.Department, role,
		StatusActive, hash, expires); err != nil {
		return User{}, "", err
	}
	user, err := s.byID(ctx, id)
	return user, generated, err
}

// Register records a request for an account, to be approved by an admin.
//
// An address that already has an account is accepted and ignored rather than
// refused: telling a stranger which addresses are registered is telling them
// which ones are worth attacking, the same reason login has one error for
// both. The person who really owns that address can already sign in.
func (s *Service) Register(ctx context.Context, email, password, name, department string) error {
	address := NormalizeEmail(email)
	if !ValidEmail(address) {
		return invalidf("%q is not an email address", email)
	}
	if strings.TrimSpace(name) == "" {
		return invalidError("a name is required, so an administrator knows who is asking")
	}
	hash, err := s.HashPassword(password)
	if err != nil {
		return err
	}
	if _, err := s.byEmail(ctx, address); err == nil {
		return nil
	} else if !errors.Is(err, ErrNotFound) {
		return err
	}
	var pending int
	if err := s.db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM users WHERE status = 'pending'`).Scan(&pending); err != nil {
		return err
	}
	if pending >= maxPending {
		return ErrTooManyPending
	}
	return s.insertUser(ctx, uuid.NewString(), address, name, department,
		RoleResearcher, StatusPending, hash, nil)
}

func (s *Service) insertUser(
	ctx context.Context, id, address, name, department, role, status, hash string,
	expires *time.Time,
) error {
	now := time.Now().UTC()
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO users (user_id, email, name, role, password_hash, disabled,
		                   created_at, updated_at, department, status, access_expires_at)
		VALUES (?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?, ?)`,
		id, address, nullable(clip(name, 120)), role, hash, now, now,
		nullable(clip(department, 120)), status, expires)
	return err
}

// UserPatch is what an admin may change about an account. A nil field is
// left as it is.
type UserPatch struct {
	Name       *string
	Department *string
	Role       *string
	// Set SetExpiry to change the end date; a nil AccessExpiresAt then clears it.
	SetExpiry       bool
	AccessExpiresAt *time.Time
	Disabled        *bool
}

// UpdateUser applies an admin's changes to an account.
func (s *Service) UpdateUser(ctx context.Context, id string, patch UserPatch) (User, error) {
	user, err := s.byID(ctx, id)
	if err != nil {
		return User{}, err
	}
	if patch.Name != nil {
		user.Name = clip(*patch.Name, 120)
	}
	if patch.Department != nil {
		user.Department = clip(*patch.Department, 120)
	}
	if patch.Role != nil {
		user.Role = *patch.Role
	}
	if patch.SetExpiry {
		user.AccessExpiresAt = patch.AccessExpiresAt
	}
	// Checked as a whole, after every change: turning somebody into a guest
	// and giving them an end date is one edit, not two that must be ordered.
	expires, err := checkRole(user.Role, user.AccessExpiresAt)
	if err != nil {
		return User{}, err
	}
	if patch.Disabled != nil {
		user.Disabled = *patch.Disabled
	}

	if _, err := s.db.ExecContext(ctx, `
		UPDATE users SET name = ?, department = ?, role = ?, access_expires_at = ?,
		                 disabled = ?, updated_at = ?
		WHERE user_id = ?`,
		nullable(user.Name), nullable(user.Department), user.Role, expires,
		user.Disabled, time.Now().UTC(), id); err != nil {
		return User{}, err
	}
	if user.Disabled {
		if err := s.LogoutEverywhere(ctx, id); err != nil {
			return User{}, err
		}
	}
	return s.byID(ctx, id)
}

// Approve lets a pending registration in, as the role the admin chooses.
func (s *Service) Approve(ctx context.Context, id, role string, expires *time.Time) (User, error) {
	user, err := s.byID(ctx, id)
	if err != nil {
		return User{}, err
	}
	if user.Status != StatusPending {
		return User{}, ErrNotPending
	}
	if role == "" {
		role = RoleResearcher
	}
	end, err := checkRole(role, expires)
	if err != nil {
		return User{}, err
	}
	if _, err := s.db.ExecContext(ctx, `
		UPDATE users SET status = 'active', role = ?, access_expires_at = ?, updated_at = ?
		WHERE user_id = ?`, role, end, time.Now().UTC(), id); err != nil {
		return User{}, err
	}
	return s.byID(ctx, id)
}

// Reject removes a registration that was never approved. Only a pending one:
// an account that has been used keeps its row, and is disabled instead, so
// what it did stays attached to somebody.
func (s *Service) Reject(ctx context.Context, id string) error {
	user, err := s.byID(ctx, id)
	if err != nil {
		return err
	}
	if user.Status != StatusPending {
		return ErrNotPending
	}
	_, err = s.db.ExecContext(ctx, `DELETE FROM users WHERE user_id = ? AND status = 'pending'`, id)
	return err
}

// ResetPassword gives an account a new generated password, returned once,
// and ends every session it holds.
func (s *Service) ResetPassword(ctx context.Context, id string) (string, error) {
	if _, err := s.byID(ctx, id); err != nil {
		return "", err
	}
	password := GeneratePassword()
	hash, err := s.HashPassword(password)
	if err != nil {
		return "", err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?`,
		hash, time.Now().UTC(), id); err != nil {
		return "", err
	}
	return password, s.LogoutEverywhere(ctx, id)
}

// UserByID is one account, for the admin's detail page.
func (s *Service) UserByID(ctx context.Context, id string) (User, error) {
	return s.byID(ctx, id)
}

// checkRole validates a role and returns the end date to store with it: the
// given one for a guest, which must be in the future, and none otherwise.
func checkRole(role string, expires *time.Time) (*time.Time, error) {
	if !ValidRole(role) {
		return nil, invalidf("role must be %s, %s or %s", RoleAdmin, RoleResearcher, RoleGuest)
	}
	if role != RoleGuest {
		return nil, nil
	}
	if expires == nil || !expires.After(time.Now()) {
		return nil, ErrGuestNeedsEnd
	}
	end := expires.UTC()
	return &end, nil
}

// An alphabet without the characters people misread when a password is read
// out or copied from a screen: no 0/O, 1/l/I.
const passwordAlphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

// GeneratePassword returns a random 20-character password, about 116 bits.
func GeneratePassword() string {
	return randomFrom(passwordAlphabet, 20)
}

func randomFrom(alphabet string, n int) string {
	out := make([]byte, n)
	// Rejection sampling, so every character is equally likely rather than
	// the first few being favoured by a modulo.
	limit := byte(256 - 256%len(alphabet))
	buf := make([]byte, 1)
	for i := 0; i < n; {
		if _, err := rand.Read(buf); err != nil {
			panic(err)
		}
		if buf[0] >= limit {
			continue
		}
		out[i] = alphabet[int(buf[0])%len(alphabet)]
		i++
	}
	return string(out)
}
