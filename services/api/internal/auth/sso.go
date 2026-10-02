package auth

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

// Signing in on somebody else's word.
//
// An identity provider — the office's Microsoft 365 directory — vouches that
// the person at the browser is a particular member of it. What it vouches for
// is a subject: an identifier that belongs to that person for as long as their
// directory entry exists, and is never reissued to somebody else. The address
// is only how that subject is first matched to an account here, because an
// address in a directory can be renamed and, once its owner leaves, handed on.
//
// So the first sign-in links an account to the subject, and every later one
// finds the account by the subject alone. An account linked to one subject is
// never quietly re-linked to another that turns up with the same address.

var (
	// ErrNotInvited is a first sign-in on a deployment that only lets in
	// addresses an admin has already created.
	ErrNotInvited = errors.New("no account has been made for this address")
	// ErrLinkedElsewhere is an address whose account is already linked to a
	// different person in the directory — usually a mailbox that was handed
	// on after its first owner left.
	ErrLinkedElsewhere = errors.New("this account is linked to a different directory identity")
)

// What a first sign-in from an address with no account gets.
const (
	NewAccountsPending = "pending"
	NewAccountsActive  = "active"
	NewAccountsRefuse  = "refuse"
)

// ExternalIdentity is a person as an identity provider vouches for them.
type ExternalIdentity struct {
	// Provider-qualified and stable, e.g. "microsoft:<tenant>:<object id>".
	Subject string
	Email   string
	Name    string
}

// LoginExternal starts a session for a person an identity provider has
// already authenticated. newAccounts is one of the NewAccounts constants.
//
// No password is checked and no attempt is rate-limited here: the provider
// did both, and a caller can only reach this with what it vouched for.
func (s *Service) LoginExternal(
	ctx context.Context, id ExternalIdentity, newAccounts, userAgent string, remember bool,
) (token string, session Session, err error) {
	if id.Subject == "" {
		return "", Session{}, errors.New("an external identity needs a subject")
	}
	address := NormalizeEmail(id.Email)
	if !ValidEmail(address) {
		return "", Session{}, invalidf("%q is not an email address", id.Email)
	}

	user, err := s.bySubject(ctx, id.Subject)
	switch {
	case err == nil:
	case errors.Is(err, ErrNotFound):
		user, err = s.linkOrCreate(ctx, id, address, newAccounts)
		if err != nil {
			return "", Session{}, err
		}
	default:
		return "", Session{}, err
	}

	// Where the deployment lets the directory's word stand in for an admin's,
	// a request still waiting for approval — made at /register, or by an
	// earlier sign-in under the stricter policy — is approved by this one. Only
	// the waiting: a role an admin chose, or an account they switched off,
	// stays exactly as they left it.
	if newAccounts == NewAccountsActive && user.Status == StatusPending {
		if _, err := s.db.ExecContext(ctx,
			`UPDATE users SET status = 'active', updated_at = ? WHERE user_id = ?`,
			time.Now().UTC(), user.ID); err != nil {
			return "", Session{}, err
		}
		user.Status = StatusActive
	}

	if err := user.usable(time.Now()); err != nil {
		return "", Session{}, err
	}

	token, sessionID, expires, err := s.startSession(ctx, user.ID, userAgent, remember)
	if err != nil {
		return "", Session{}, err
	}
	now := time.Now().UTC()
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET last_login_at = ? WHERE user_id = ?`, now, user.ID); err != nil {
		return "", Session{}, err
	}
	user.LastLoginAt = &now
	return token, Session{User: user, ID: sessionID, ExpiresAt: expires}, nil
}

// linkOrCreate is a subject seen for the first time: the account its address
// already has, linked to it, or a new one where the deployment allows that.
func (s *Service) linkOrCreate(
	ctx context.Context, id ExternalIdentity, address, newAccounts string,
) (User, error) {
	var linked sql.NullString
	row := s.db.QueryRowContext(ctx,
		`SELECT `+userColumns+`, u.sso_subject FROM users u WHERE u.email = ?`, address)
	user, err := scanUser(row, &linked)
	switch {
	case err == nil:
		if linked.Valid && linked.String != "" && linked.String != id.Subject {
			return User{}, ErrLinkedElsewhere
		}
		if _, err := s.db.ExecContext(ctx,
			`UPDATE users SET sso_subject = ?, updated_at = ? WHERE user_id = ?`,
			id.Subject, time.Now().UTC(), user.ID); err != nil {
			return User{}, err
		}
		// A request still waiting for approval was made at /register, where
		// nobody checks that the person typing owns the address. Its password
		// is therefore a stranger's until proven otherwise, and the directory
		// is about to vouch for the account it protects: left in place, it
		// would let whoever registered the address first sign in as its real
		// owner. So it is replaced with one nobody is shown, and anything
		// that password could have started is ended with it.
		if user.Status == StatusPending {
			if err := s.forgetPassword(ctx, user.ID); err != nil {
				return User{}, err
			}
		}
		return user, nil
	case !errors.Is(err, sql.ErrNoRows):
		return User{}, err
	}

	status := StatusPending
	switch newAccounts {
	case NewAccountsRefuse:
		return User{}, ErrNotInvited
	case NewAccountsActive:
		status = StatusActive
	case NewAccountsPending:
		var pending int
		if err := s.db.QueryRowContext(ctx,
			`SELECT COUNT(*) FROM users WHERE status = 'pending'`).Scan(&pending); err != nil {
			return User{}, err
		}
		if pending >= maxPending {
			return User{}, ErrTooManyPending
		}
	default:
		return User{}, fmt.Errorf("unknown new-account policy %q", newAccounts)
	}

	// The column is NOT NULL and the password form must refuse this account,
	// so it holds the hash of a password nobody is ever shown. An admin who
	// resets it from the Users page gives the person a password as well.
	hash, err := s.HashPassword(GeneratePassword())
	if err != nil {
		return User{}, err
	}
	userID := uuid.NewString()
	name := strings.TrimSpace(id.Name)
	if err := s.insertUser(ctx, userID, address, name, "", RoleResearcher,
		status, hash, nil); err != nil {
		return User{}, err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET sso_subject = ? WHERE user_id = ?`, id.Subject, userID); err != nil {
		return User{}, err
	}
	return s.byID(ctx, userID)
}

// forgetPassword replaces an account's password with one nobody is shown and
// ends every session and API token it has.
func (s *Service) forgetPassword(ctx context.Context, userID string) error {
	hash, err := s.HashPassword(GeneratePassword())
	if err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?`,
		hash, time.Now().UTC(), userID); err != nil {
		return err
	}
	if err := s.LogoutEverywhere(ctx, userID); err != nil {
		return err
	}
	return s.revokeTokens(ctx, userID)
}

func (s *Service) bySubject(ctx context.Context, subject string) (User, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT `+userColumns+` FROM users u WHERE u.sso_subject = ?`, subject)
	user, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return user, err
}
