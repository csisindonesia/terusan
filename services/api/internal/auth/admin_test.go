package auth

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestARegistrationWaitsForApproval(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})

	if err := accounts.Register(ctx, "new@example.org", goodPassword, "New", "Economics"); err != nil {
		t.Fatalf("Register: %v", err)
	}
	if _, _, err := accounts.Login(ctx, "new@example.org", goodPassword, "test", false); !errors.Is(err, ErrPending) {
		t.Fatalf("a pending account logged in: %v", err)
	}
	// A second request for the same address is accepted and changes nothing,
	// so the form cannot be used to learn who is registered.
	if err := accounts.Register(ctx, "new@example.org", goodPassword, "Someone else", ""); err != nil {
		t.Fatalf("second Register: %v", err)
	}

	user, err := accounts.UserByEmail(ctx, "new@example.org")
	if err != nil || user.Status != StatusPending || user.Department != "Economics" || user.Name != "New" {
		t.Fatalf("pending user = %+v, %v", user, err)
	}
	if _, err := accounts.Approve(ctx, user.ID, RoleResearcher, nil); err != nil {
		t.Fatalf("Approve: %v", err)
	}
	if _, _, err := accounts.Login(ctx, "new@example.org", goodPassword, "test", false); err != nil {
		t.Fatalf("an approved account could not log in: %v", err)
	}
	if _, err := accounts.Approve(ctx, user.ID, RoleResearcher, nil); !errors.Is(err, ErrNotPending) {
		t.Errorf("approved twice: %v", err)
	}
}

func TestARejectedRegistrationIsGone(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	_ = accounts.Register(ctx, "spam@example.org", goodPassword, "Spam", "")
	user, _ := accounts.UserByEmail(ctx, "spam@example.org")
	if err := accounts.Reject(ctx, user.ID); err != nil {
		t.Fatalf("Reject: %v", err)
	}
	if _, err := accounts.UserByID(ctx, user.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("a rejected registration is still there: %v", err)
	}
	// An active account is disabled, never deleted.
	active, _, _ := accounts.CreateUser(ctx, NewUser{Email: "keep@example.org"})
	if err := accounts.Reject(ctx, active.ID); !errors.Is(err, ErrNotPending) {
		t.Fatalf("an active account was rejected: %v", err)
	}
}

func TestAGuestStopsAtTheEndDate(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})

	if _, _, err := accounts.CreateUser(ctx, NewUser{
		Email: "guest@example.org", Role: RoleGuest,
	}); !errors.Is(err, ErrGuestNeedsEnd) {
		t.Fatalf("a guest without an end date was made: %v", err)
	}

	end := time.Now().Add(time.Hour)
	guest, password, err := accounts.CreateUser(ctx, NewUser{
		Email: "guest@example.org", Role: RoleGuest, AccessExpiresAt: &end,
	})
	if err != nil {
		t.Fatalf("CreateUser: %v", err)
	}
	if len(password) != 20 {
		t.Fatalf("generated password %q", password)
	}
	token, _, err := accounts.Login(ctx, guest.Email, password, "test", false)
	if err != nil {
		t.Fatalf("guest Login: %v", err)
	}
	secret, _, err := accounts.CreateToken(ctx, guest.ID, "script", 90)
	if err != nil {
		t.Fatalf("CreateToken: %v", err)
	}

	// The end date is moved into the past directly: UpdateUser, rightly,
	// refuses to set one there.
	if _, err := accounts.db.ExecContext(ctx,
		`UPDATE users SET access_expires_at = ? WHERE user_id = ?`,
		time.Now().Add(-time.Minute).UTC(), guest.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := accounts.Session(ctx, token); !errors.Is(err, ErrExpired) {
		t.Errorf("a lapsed guest's session still works: %v", err)
	}
	if _, err := accounts.TokenSession(ctx, secret); !errors.Is(err, ErrExpired) {
		t.Errorf("a lapsed guest's token still works: %v", err)
	}
	if _, _, err := accounts.Login(ctx, guest.Email, password, "test", false); !errors.Is(err, ErrExpired) {
		t.Errorf("a lapsed guest logged in: %v", err)
	}

	// Promoting them clears the end date and lets them back in.
	role := RoleResearcher
	promoted, err := accounts.UpdateUser(ctx, guest.ID, UserPatch{Role: &role})
	if err != nil || promoted.AccessExpiresAt != nil {
		t.Fatalf("UpdateUser = %+v, %v", promoted, err)
	}
	if _, err := accounts.Session(ctx, token); err != nil {
		t.Errorf("a promoted guest's session is still refused: %v", err)
	}
}

func TestTheAccessLogIsThrottled(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, _, _ := accounts.CreateUser(ctx, NewUser{Email: "dev@example.org"})

	client := Client{IP: "203.0.113.7", Country: "ID", City: "Jakarta", UserAgent: "test"}
	if err := accounts.RecordAccess(ctx, user.ID, AccessLogin, client); err != nil {
		t.Fatalf("RecordAccess: %v", err)
	}
	for range 5 {
		if err := accounts.Touch(ctx, user.ID, AccessWeb, client); err != nil {
			t.Fatalf("Touch: %v", err)
		}
	}
	// A new address is new information, and is written at once.
	other := client
	other.IP = "198.51.100.2"
	_ = accounts.Touch(ctx, user.ID, AccessWeb, other)

	log, err := accounts.AccessLog(ctx, user.ID, 0)
	if err != nil {
		t.Fatalf("AccessLog: %v", err)
	}
	if len(log) != 3 {
		t.Fatalf("access log has %d rows, want 3: %+v", len(log), log)
	}
	if log[len(log)-1].Kind != AccessLogin || log[len(log)-1].City != "Jakarta" {
		t.Errorf("oldest row = %+v", log[len(log)-1])
	}
	refreshed, _ := accounts.UserByID(ctx, user.ID)
	if refreshed.LastActiveAt == nil {
		t.Error("last_active_at was not set")
	}
}

func TestOldRolesBecomeResearchers(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, _ := accounts.PutUser(ctx, "old@example.org", goodPassword, "", "")
	if _, err := accounts.db.ExecContext(ctx,
		`UPDATE users SET role = 'member', status = NULL WHERE user_id = ?`, user.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := New(accounts.db, Config{Iterations: testIterations}); err != nil {
		t.Fatalf("New: %v", err)
	}
	migrated, _ := accounts.UserByID(ctx, user.ID)
	if migrated.Role != RoleResearcher || migrated.Status != StatusActive {
		t.Fatalf("migrated = %+v", migrated)
	}
}
