package auth

import (
	"context"
	"errors"
	"testing"
)

func TestAnOpenDirectoryApprovesWhatWasWaiting(t *testing.T) {
	ctx := context.Background()
	s := service(t, Config{Iterations: 1000})
	id := ExternalIdentity{Subject: "microsoft:t:o", Email: "dev@csis.or.id", Name: "Dev"}

	// First under the stricter policy: a request, and no session.
	if _, _, err := s.LoginExternal(ctx, id, NewAccountsPending, "test", false); !errors.Is(err, ErrPending) {
		t.Fatalf("LoginExternal(pending) = %v, want ErrPending", err)
	}
	// The deployment opens up, and the same person is let straight in.
	_, session, err := s.LoginExternal(ctx, id, NewAccountsActive, "test", false)
	if err != nil {
		t.Fatalf("LoginExternal(active) = %v", err)
	}
	if session.User.Status != StatusActive || session.User.Role != RoleResearcher {
		t.Fatalf("signed in as %s %s, want an active researcher",
			session.User.Status, session.User.Role)
	}

	// What an admin decides afterwards stands: a switched-off account stays
	// off, whatever the policy.
	if err := s.SetDisabled(ctx, id.Email, true); err != nil {
		t.Fatalf("SetDisabled: %v", err)
	}
	if _, _, err := s.LoginExternal(ctx, id, NewAccountsActive, "test", false); !errors.Is(err, ErrDisabled) {
		t.Fatalf("LoginExternal(disabled) = %v, want ErrDisabled", err)
	}
}

func TestAnOpenDirectoryKeepsTheRoleAnAdminChose(t *testing.T) {
	ctx := context.Background()
	s := service(t, Config{Iterations: 1000})
	id := ExternalIdentity{Subject: "microsoft:t:o", Email: "dev@csis.or.id"}
	if _, _, err := s.LoginExternal(ctx, id, NewAccountsActive, "test", false); err != nil {
		t.Fatalf("LoginExternal = %v", err)
	}
	user, err := s.UserByEmail(ctx, id.Email)
	if err != nil {
		t.Fatalf("UserByEmail: %v", err)
	}
	admin := RoleAdmin
	if _, err := s.UpdateUser(ctx, user.ID, UserPatch{Role: &admin}); err != nil {
		t.Fatalf("UpdateUser: %v", err)
	}
	_, session, err := s.LoginExternal(ctx, id, NewAccountsActive, "test", false)
	if err != nil || session.User.Role != RoleAdmin {
		t.Fatalf("LoginExternal = %v as %q, want the admin role kept", err, session.User.Role)
	}
}
