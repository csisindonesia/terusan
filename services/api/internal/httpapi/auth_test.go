package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/appdb"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/config"
)

// gated is the whole API with sign-in required, one account, and a cookie
// session and an API token for it.
func gated(t *testing.T) (handler http.Handler, cookie, token string) {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	accounts, err := auth.New(db, auth.Config{Iterations: 1000})
	if err != nil {
		t.Fatalf("auth.New: %v", err)
	}
	ctx := context.Background()
	user, err := accounts.PutUser(ctx, "dev@example.org", "a-long-enough-passphrase", "", "")
	if err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	cookie, _, err = accounts.Login(ctx, user.Email, "a-long-enough-passphrase", "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	token, _, err = accounts.CreateToken(ctx, user.ID, "test", 1)
	if err != nil {
		t.Fatalf("CreateToken: %v", err)
	}

	cfg := &config.Config{Auth: config.Auth{Required: true}}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(cfg, nil, nil, nil, accounts, nil, nil, log).Routes(), cookie, token
}

func call(h http.Handler, method, path, cookie, bearer string) *httptest.ResponseRecorder {
	var body io.Reader
	if method == http.MethodPost {
		body = strings.NewReader(`{"name":"another"}`)
	}
	r := httptest.NewRequest(method, path, body)
	if cookie != "" {
		r.AddCookie(&http.Cookie{Name: SessionCookie, Value: cookie})
	}
	if bearer != "" {
		r.Header.Set("Authorization", "Bearer "+bearer)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestAStrangerCannotRead(t *testing.T) {
	h, _, _ := gated(t)
	for _, path := range []string{"/v1/indicators", "/v1/observations", "/v1/documents", "/v1/auth/tokens"} {
		if got := call(h, http.MethodGet, path, "", "").Code; got != http.StatusUnauthorized {
			t.Errorf("GET %s signed out = %d, want 401", path, got)
		}
	}
	// What the portal needs before it can know to show a login.
	for _, path := range []string{"/healthz", "/v1/capabilities"} {
		if got := call(h, http.MethodGet, path, "", "").Code; got != http.StatusOK {
			t.Errorf("GET %s signed out = %d, want 200", path, got)
		}
	}
}

func TestAnAPITokenSignsARequestIn(t *testing.T) {
	h, _, token := gated(t)
	w := call(h, http.MethodGet, "/v1/auth/me", "", token)
	if w.Code != http.StatusOK {
		t.Fatalf("GET /v1/auth/me with a token = %d: %s", w.Code, w.Body)
	}
	var got Response[auth.Session]
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil || got.Data.User.Email != "dev@example.org" {
		t.Fatalf("GET /v1/auth/me = %s (%v)", w.Body, err)
	}
	if got := call(h, http.MethodGet, "/v1/auth/me", "", auth.TokenPrefix+"forged").Code; got != http.StatusUnauthorized {
		t.Errorf("a forged token = %d, want 401", got)
	}
}

func TestATokenCannotManageItsAccount(t *testing.T) {
	h, cookie, token := gated(t)
	if got := call(h, http.MethodPost, "/v1/auth/tokens", "", token).Code; got != http.StatusForbidden {
		t.Errorf("a token minting a token = %d, want 403", got)
	}
	if got := call(h, http.MethodGet, "/v1/auth/sessions", "", token).Code; got != http.StatusForbidden {
		t.Errorf("a token listing logins = %d, want 403", got)
	}
	w := call(h, http.MethodPost, "/v1/auth/tokens", cookie, "")
	if w.Code != http.StatusCreated || !strings.Contains(w.Body.String(), `"token":"trs_`) {
		t.Fatalf("a cookie minting a token = %d: %s", w.Code, w.Body)
	}
}

func TestRequiredSignInWithoutAccountsRefusesEverything(t *testing.T) {
	cfg := &config.Config{Auth: config.Auth{Required: true}}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	h := New(cfg, nil, nil, nil, nil, nil, nil, log).Routes()
	if got := call(h, http.MethodGet, "/v1/indicators", "", "").Code; got != http.StatusServiceUnavailable {
		t.Errorf("GET /v1/indicators with no accounts = %d, want 503", got)
	}
}

// admin is the whole API with one admin and one researcher signed in.
func admin(t *testing.T) (h http.Handler, adminCookie, researcherCookie string) {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	accounts, err := auth.New(db, auth.Config{Iterations: 1000})
	if err != nil {
		t.Fatalf("auth.New: %v", err)
	}
	ctx := context.Background()
	const password = "a-long-enough-passphrase"
	for _, who := range []struct{ email, role string }{
		{"boss@example.org", auth.RoleAdmin}, {"reader@example.org", auth.RoleResearcher},
	} {
		if _, err := accounts.PutUser(ctx, who.email, password, "", who.role); err != nil {
			t.Fatalf("PutUser: %v", err)
		}
	}
	adminCookie, _, _ = accounts.Login(ctx, "boss@example.org", password, "test", false)
	researcherCookie, _, _ = accounts.Login(ctx, "reader@example.org", password, "test", false)

	cfg := &config.Config{Auth: config.Auth{Required: true, Registration: true}}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(cfg, nil, nil, nil, accounts, nil, nil, log).Routes(), adminCookie, researcherCookie
}

func send(h http.Handler, method, path, cookie, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if cookie != "" {
		r.AddCookie(&http.Cookie{Name: SessionCookie, Value: cookie})
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestOnlyAnAdminManagesUsers(t *testing.T) {
	h, boss, reader := admin(t)
	if got := send(h, http.MethodGet, "/v1/admin/users", reader, "").Code; got != http.StatusForbidden {
		t.Errorf("a researcher listing users = %d, want 403", got)
	}
	w := send(h, http.MethodGet, "/v1/admin/users", boss, "")
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "reader@example.org") {
		t.Fatalf("an admin listing users = %d: %s", w.Code, w.Body)
	}

	w = send(h, http.MethodPost, "/v1/admin/users", boss,
		`{"email":"guest@example.org","role":"guest","department":"Politics",
		  "access_expires_at":"2099-01-01T00:00:00Z"}`)
	if w.Code != http.StatusCreated || !strings.Contains(w.Body.String(), `"password":"`) {
		t.Fatalf("creating a guest = %d: %s", w.Code, w.Body)
	}
	if got := send(h, http.MethodPost, "/v1/admin/users", boss,
		`{"email":"guest2@example.org","role":"guest"}`).Code; got != http.StatusBadRequest {
		t.Errorf("a guest without an end date = %d, want 400", got)
	}
}

func TestAStrangerCanAskAndAnAdminApproves(t *testing.T) {
	h, boss, _ := admin(t)
	w := send(h, http.MethodPost, "/v1/auth/register", "",
		`{"email":"new@example.org","password":"a-long-enough-passphrase","name":"New","department":"Economics"}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("registering = %d: %s", w.Code, w.Body)
	}
	w = send(h, http.MethodPost, "/v1/auth/login", "",
		`{"email":"new@example.org","password":"a-long-enough-passphrase"}`)
	if w.Code != http.StatusForbidden {
		t.Fatalf("a pending login = %d, want 403: %s", w.Code, w.Body)
	}

	var listed Response[[]auth.User]
	_ = json.Unmarshal(send(h, http.MethodGet, "/v1/admin/users", boss, "").Body.Bytes(), &listed)
	var id string
	for _, user := range listed.Data {
		if user.Email == "new@example.org" {
			id = user.ID
		}
	}
	if id == "" {
		t.Fatal("the registration is not in the list")
	}
	if got := send(h, http.MethodPost, "/v1/admin/users/"+id+"/approve", boss,
		`{"role":"researcher"}`).Code; got != http.StatusOK {
		t.Fatalf("approving = %d", got)
	}
	w = send(h, http.MethodPost, "/v1/auth/login", "",
		`{"email":"new@example.org","password":"a-long-enough-passphrase"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("an approved login = %d: %s", w.Code, w.Body)
	}

	w = send(h, http.MethodGet, "/v1/admin/users/"+id, boss, "")
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"kind":"login"`) {
		t.Fatalf("the user's detail = %d: %s", w.Code, w.Body)
	}
}

func TestAnAdminCannotDemoteThemselves(t *testing.T) {
	h, boss, _ := admin(t)
	w := send(h, http.MethodGet, "/v1/auth/me", boss, "")
	var me Response[auth.Session]
	_ = json.Unmarshal(w.Body.Bytes(), &me)
	if got := send(h, http.MethodPatch, "/v1/admin/users/"+me.Data.User.ID, boss,
		`{"role":"researcher"}`).Code; got != http.StatusBadRequest {
		t.Errorf("self-demotion = %d, want 400", got)
	}
}
