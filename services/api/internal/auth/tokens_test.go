package auth

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestATokenResolvesToItsAccount(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, err := accounts.PutUser(ctx, "dev@example.org", goodPassword, "Dev", "")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}

	secret, token, err := accounts.CreateToken(ctx, user.ID, "notebook", 0)
	if err != nil {
		t.Fatalf("CreateToken: %v", err)
	}
	if !strings.HasPrefix(secret, TokenPrefix) || !strings.HasPrefix(secret, token.Hint) {
		t.Fatalf("secret %q does not carry the prefix or hint %q", secret, token.Hint)
	}

	session, err := accounts.TokenSession(ctx, secret)
	if err != nil {
		t.Fatalf("TokenSession: %v", err)
	}
	if session.User.ID != user.ID || session.ID != token.ID {
		t.Fatalf("TokenSession returned %+v", session)
	}

	// The secret is not a cookie, and a cookie is not a token.
	if _, err := accounts.Session(ctx, secret); !errors.Is(err, ErrNoSession) {
		t.Errorf("a token worked as a cookie: %v", err)
	}
	if _, err := accounts.TokenSession(ctx, TokenPrefix+"nonsense"); !errors.Is(err, ErrNoSession) {
		t.Errorf("an unknown token resolved: %v", err)
	}

	listed, err := accounts.Tokens(ctx, user.ID)
	if err != nil || len(listed) != 1 || listed[0].ID != token.ID {
		t.Fatalf("Tokens = %+v, %v", listed, err)
	}
}

func TestARevokedTokenStopsWorking(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	owner, _ := accounts.PutUser(ctx, "owner@example.org", goodPassword, "", "")
	other, _ := accounts.PutUser(ctx, "other@example.org", goodPassword, "", "")

	secret, token, err := accounts.CreateToken(ctx, owner.ID, "script", 30)
	if err != nil {
		t.Fatalf("CreateToken: %v", err)
	}
	// Scoped to the account: somebody else cannot revoke it by id.
	if err := accounts.RevokeToken(ctx, other.ID, token.ID); !errors.Is(err, ErrNoToken) {
		t.Fatalf("another account revoked the token: %v", err)
	}
	if err := accounts.RevokeToken(ctx, owner.ID, token.ID); err != nil {
		t.Fatalf("RevokeToken: %v", err)
	}
	if _, err := accounts.TokenSession(ctx, secret); !errors.Is(err, ErrNoSession) {
		t.Fatalf("a revoked token still resolves: %v", err)
	}
}

func TestADisabledAccountsTokensAreRefused(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, _ := accounts.PutUser(ctx, "gone@example.org", goodPassword, "", "")
	secret, _, err := accounts.CreateToken(ctx, user.ID, "script", 30)
	if err != nil {
		t.Fatalf("CreateToken: %v", err)
	}
	if err := accounts.SetDisabled(ctx, user.Email, true); err != nil {
		t.Fatalf("SetDisabled: %v", err)
	}
	if _, err := accounts.TokenSession(ctx, secret); !errors.Is(err, ErrDisabled) {
		t.Fatalf("a disabled account's token resolved: %v", err)
	}
}

func TestTokenBounds(t *testing.T) {
	ctx := context.Background()
	accounts := service(t, Config{})
	user, _ := accounts.PutUser(ctx, "dev@example.org", goodPassword, "", "")

	if _, _, err := accounts.CreateToken(ctx, user.ID, "  ", 30); err == nil {
		t.Error("a nameless token was issued")
	}
	if _, _, err := accounts.CreateToken(ctx, user.ID, "forever", MaxTokenDays+1); err == nil {
		t.Error("a token past the maximum expiry was issued")
	}
	for i := 0; i < maxTokensPerUser; i++ {
		if _, _, err := accounts.CreateToken(ctx, user.ID, "t", 1); err != nil {
			t.Fatalf("CreateToken %d: %v", i, err)
		}
	}
	if _, _, err := accounts.CreateToken(ctx, user.ID, "one more", 1); !errors.Is(err, ErrTooManyTokens) {
		t.Fatalf("the limit was not enforced: %v", err)
	}
}
