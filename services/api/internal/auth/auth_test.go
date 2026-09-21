package auth

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/csis/terusan/services/api/internal/appdb"
)

// Iterations are deliberately low here. The production floor is 600,000, and a
// test that pays for it twenty times over spends a minute proving arithmetic
// the standard library already tests.
const testIterations = 1000

func service(t *testing.T, cfg Config) *Service {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	cfg.Iterations = testIterations
	accounts, err := New(db, cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return accounts
}

const goodPassword = "a-long-enough-passphrase"

func TestPutUserAndLogin(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})

	// Mixed case going in, lowercase coming out: an address is one address
	// however it was typed.
	user, err := accounts.PutUser(ctx, "Dev@Example.Org", goodPassword, "Dev", "admin")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	if user.Email != "dev@example.org" || user.Role != "admin" {
		t.Fatalf("PutUser stored %+v", user)
	}

	token, session, err := accounts.Login(ctx, "  DEV@example.org ", goodPassword, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if token == "" || session.User.ID != user.ID {
		t.Fatalf("Login returned %q, %+v", token, session)
	}
	if session.User.LastLoginAt == nil {
		t.Error("Login did not record last_login_at")
	}

	held, err := accounts.Session(ctx, token)
	if err != nil {
		t.Fatalf("Session: %v", err)
	}
	if held.User.Email != "dev@example.org" {
		t.Fatalf("Session returned %+v", held.User)
	}
}

func TestLoginRejects(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	cases := map[string]struct{ email, password string }{
		"wrong password": {"dev@example.org", "not-the-passphrase"},
		"unknown email":  {"nobody@example.org", goodPassword},
		// Case matters for the password and not for the address.
		"password case": {"dev@example.org", strings.ToUpper(goodPassword)},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if _, _, err := accounts.Login(ctx, tc.email, tc.password, "test", false); !errors.Is(err, ErrInvalidCredentials) {
				t.Fatalf("Login = %v, want ErrInvalidCredentials", err)
			}
		})
	}
}

func TestPasswordIsNotStoredInTheClear(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	var stored string
	if err := accounts.db.QueryRowContext(ctx,
		`SELECT password_hash FROM users`).Scan(&stored); err != nil {
		t.Fatalf("read hash: %v", err)
	}
	if strings.Contains(stored, goodPassword) {
		t.Fatal("the password is in the database")
	}
	if !strings.HasPrefix(stored, "pbkdf2-sha256$") {
		t.Fatalf("unexpected encoding %q", stored)
	}
	// The parameters travel with the hash, so the work factor can be raised
	// later without invalidating anyone.
	if !strings.Contains(stored, "$"+"1000"+"$") {
		t.Errorf("iterations are not in the encoding: %q", stored)
	}
	// Two accounts with the same password must not share a hash.
	if _, err := accounts.PutUser(ctx, "other@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	var second string
	if err := accounts.db.QueryRowContext(ctx,
		`SELECT password_hash FROM users WHERE email = 'other@example.org'`).Scan(&second); err != nil {
		t.Fatalf("read hash: %v", err)
	}
	if stored == second {
		t.Fatal("two accounts with one password share a hash; the salt is not working")
	}
}

func TestSessionTokenIsNotStored(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	token, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}

	var held string
	if err := accounts.db.QueryRowContext(ctx, `SELECT token_hash FROM sessions`).Scan(&held); err != nil {
		t.Fatalf("read session: %v", err)
	}
	if held == token {
		t.Fatal("the session table holds the token itself")
	}
	if held != hashToken(token) {
		t.Fatal("the session table does not hold the token's hash either")
	}
}

func TestSessionExpires(t *testing.T) {
	ctx := context.Background()
	// A session that has already run out by the time it is presented.
	accounts := service(t, Config{TTL: time.Millisecond})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	token, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	time.Sleep(5 * time.Millisecond)

	if _, err := accounts.Session(ctx, token); !errors.Is(err, ErrNoSession) {
		t.Fatalf("Session = %v, want ErrNoSession", err)
	}
	// And the row is gone, not merely refused.
	var rows int
	if err := accounts.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM sessions`).Scan(&rows); err != nil {
		t.Fatalf("count sessions: %v", err)
	}
	if rows != 0 {
		t.Errorf("expired session left %d rows behind", rows)
	}
}

func TestRememberLastsLonger(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{TTL: time.Hour, RememberTTL: 30 * 24 * time.Hour})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	_, short, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	_, long, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", true)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if !long.ExpiresAt.After(short.ExpiresAt.Add(24 * time.Hour)) {
		t.Fatalf("remember me expires at %s, barely later than %s",
			long.ExpiresAt, short.ExpiresAt)
	}
}

func TestLogoutEndsTheSession(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	token, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if err := accounts.Logout(ctx, token); err != nil {
		t.Fatalf("Logout: %v", err)
	}
	if _, err := accounts.Session(ctx, token); !errors.Is(err, ErrNoSession) {
		t.Fatalf("Session after Logout = %v, want ErrNoSession", err)
	}
	// Logging out twice is not an error: the caller is asking to be logged
	// out, and it is.
	if err := accounts.Logout(ctx, token); err != nil {
		t.Fatalf("second Logout: %v", err)
	}
}

func TestDisabledAccount(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	token, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}

	if err := accounts.SetDisabled(ctx, "dev@example.org", true); err != nil {
		t.Fatalf("SetDisabled: %v", err)
	}
	// Disabling ends the sessions that are already open, which is the point of
	// disabling somebody.
	if _, err := accounts.Session(ctx, token); !errors.Is(err, ErrNoSession) {
		t.Fatalf("Session after disable = %v, want ErrNoSession", err)
	}
	if _, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "test", false); !errors.Is(err, ErrDisabled) {
		t.Fatalf("Login while disabled = %v, want ErrDisabled", err)
	}
	// A wrong password on a disabled account still reads as invalid
	// credentials, so the state of the account is not discoverable.
	if _, _, err := accounts.Login(ctx, "dev@example.org", "wrong-password-here", "test", false); !errors.Is(err, ErrInvalidCredentials) {
		t.Fatalf("wrong password while disabled = %v, want ErrInvalidCredentials", err)
	}
}

func TestRateLimit(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", ""); err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	for range maxAttempts {
		if _, _, err := accounts.Login(ctx, "dev@example.org", "wrong-password-here", "t", false); !errors.Is(err, ErrInvalidCredentials) {
			t.Fatalf("Login = %v, want ErrInvalidCredentials", err)
		}
	}
	// Past the limit, even the right password waits.
	if _, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "t", false); !errors.Is(err, ErrTooManyAttempts) {
		t.Fatalf("Login after %d failures = %v, want ErrTooManyAttempts", maxAttempts, err)
	}
	// Another address is unaffected: the lockout is per account, not global.
	if _, _, err := accounts.Login(ctx, "other@example.org", goodPassword, "t", false); !errors.Is(err, ErrInvalidCredentials) {
		t.Fatalf("other address = %v, want ErrInvalidCredentials", err)
	}
}

func TestShortPasswordRefused(t *testing.T) {
	_, err := service(t, Config{}).PutUser(context.Background(), "dev@example.org", "short", "", "")
	if !errors.Is(err, ErrWeakPassword) {
		t.Fatalf("PutUser = %v, want ErrWeakPassword", err)
	}
}

func TestPutUserResetsPassword(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	if _, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "Dev", "admin"); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	const replacement = "another-long-passphrase"
	user, err := accounts.PutUser(ctx, "dev@example.org", replacement, "", "admin")
	if err != nil {
		t.Fatalf("PutUser again: %v", err)
	}
	// The name is kept when the reset does not supply one: resetting a
	// password should not quietly blank the rest of the row.
	if user.Name != "Dev" {
		t.Errorf("name became %q", user.Name)
	}
	if _, _, err := accounts.Login(ctx, "dev@example.org", replacement, "t", false); err != nil {
		t.Fatalf("Login with the new password: %v", err)
	}
	if _, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "t", false); !errors.Is(err, ErrInvalidCredentials) {
		t.Fatal("the old password still works")
	}
}

func TestVerifyPasswordRejectsRubbish(t *testing.T) {
	for _, encoded := range []string{
		"", "plaintext", "pbkdf2-sha256$notanumber$c2FsdA$aGFzaA",
		"pbkdf2-sha256$1000$!!!$aGFzaA", "argon2id$1$c2FsdA$aGFzaA",
	} {
		if verifyPassword(encoded, goodPassword) {
			t.Errorf("verifyPassword accepted %q", encoded)
		}
	}
}

func TestRename(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "Dev", "admin")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	renamed, err := accounts.Rename(ctx, user.ID, "  Dev Nugroho  ")
	if err != nil {
		t.Fatalf("Rename: %v", err)
	}
	if renamed.Name != "Dev Nugroho" {
		t.Errorf("Rename stored %q", renamed.Name)
	}
	// The address is not touched by a rename: it is what the account is
	// addressed by, not a display preference.
	if renamed.Email != "dev@example.org" || renamed.Role != "admin" {
		t.Errorf("Rename changed more than the name: %+v", renamed)
	}
	if _, err := accounts.Rename(ctx, user.ID, strings.Repeat("a", 200)); err == nil {
		t.Error("Rename accepted a 200-character name")
	}
}

func TestChangePassword(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", "")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	here, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "this browser", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	elsewhere, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "the other laptop", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}

	const next = "a-completely-new-passphrase"
	if err := accounts.ChangePassword(ctx, user.ID, "wrong-old-password", next, here); !errors.Is(err, ErrInvalidCredentials) {
		t.Fatalf("ChangePassword with a wrong current = %v, want ErrInvalidCredentials", err)
	}
	if err := accounts.ChangePassword(ctx, user.ID, goodPassword, "short", here); !errors.Is(err, ErrWeakPassword) {
		t.Fatalf("ChangePassword to a short one = %v, want ErrWeakPassword", err)
	}
	if err := accounts.ChangePassword(ctx, user.ID, goodPassword, goodPassword, here); err == nil {
		t.Error("ChangePassword accepted the same password")
	}

	if err := accounts.ChangePassword(ctx, user.ID, goodPassword, next, here); err != nil {
		t.Fatalf("ChangePassword: %v", err)
	}
	if _, _, err := accounts.Login(ctx, "dev@example.org", next, "t", false); err != nil {
		t.Fatalf("Login with the new password: %v", err)
	}

	// The session that made the change survives; the one on the other machine
	// does not, which is the whole point of changing a password.
	if _, err := accounts.Session(ctx, here); err != nil {
		t.Errorf("the session that changed the password was ended: %v", err)
	}
	if _, err := accounts.Session(ctx, elsewhere); !errors.Is(err, ErrNoSession) {
		t.Errorf("the other session survived a password change: %v", err)
	}
}

func TestLoginsAndRevoke(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", "")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	here, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "this browser", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	_, other, err := accounts.Login(ctx, "dev@example.org", goodPassword, "the other laptop", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}

	logins, err := accounts.Logins(ctx, user.ID, here)
	if err != nil {
		t.Fatalf("Logins: %v", err)
	}
	if len(logins) != 2 {
		t.Fatalf("Logins = %d, want 2", len(logins))
	}
	var current, listed int
	for _, login := range logins {
		if login.Current {
			current++
		}
		if login.ID == "" {
			t.Error("a login has no id, so nothing can end it")
		}
		if login.UserAgent != "" {
			listed++
		}
	}
	if current != 1 {
		t.Errorf("%d logins marked as this browser, want 1", current)
	}
	if listed != 2 {
		t.Errorf("%d logins carry a user agent, want 2", listed)
	}

	if err := accounts.RevokeLogin(ctx, user.ID, other.ID); err != nil {
		t.Fatalf("RevokeLogin: %v", err)
	}
	if err := accounts.RevokeLogin(ctx, user.ID, other.ID); !errors.Is(err, ErrNoSession) {
		t.Fatalf("second RevokeLogin = %v, want ErrNoSession", err)
	}

	// Somebody else's session is not theirs to end, even knowing its id.
	intruder, err := accounts.PutUser(ctx, "other@example.org", goodPassword, "", "")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	_, mine, err := accounts.Login(ctx, "dev@example.org", goodPassword, "third", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if err := accounts.RevokeLogin(ctx, intruder.ID, mine.ID); !errors.Is(err, ErrNoSession) {
		t.Fatalf("revoking across accounts = %v, want ErrNoSession", err)
	}
	if _, err := accounts.Session(ctx, here); err != nil {
		t.Errorf("this browser's session was ended by somebody else: %v", err)
	}
}

func TestRevokeOthers(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", "")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	here, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "this browser", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	for range 3 {
		if _, _, err := accounts.Login(ctx, "dev@example.org", goodPassword, "elsewhere", false); err != nil {
			t.Fatalf("Login: %v", err)
		}
	}

	ended, err := accounts.RevokeOthers(ctx, user.ID, here)
	if err != nil {
		t.Fatalf("RevokeOthers: %v", err)
	}
	if ended != 3 {
		t.Errorf("RevokeOthers ended %d, want 3", ended)
	}
	if _, err := accounts.Session(ctx, here); err != nil {
		t.Errorf("RevokeOthers ended the session asking: %v", err)
	}
	logins, err := accounts.Logins(ctx, user.ID, here)
	if err != nil || len(logins) != 1 {
		t.Fatalf("Logins = %d, %v; want 1, nil", len(logins), err)
	}
}
