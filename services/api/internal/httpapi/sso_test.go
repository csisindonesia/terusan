package httpapi

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/csis/terusan/services/api/internal/appdb"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/config"
)

const (
	testTenant = "11111111-2222-3333-4444-555555555555"
	testClient = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
	portal     = "https://portal.example.org"
)

// fakeMicrosoft is the token endpoint, answering as the person it is told to
// be, and checking the PKCE verifier against the challenge the API sent.
type fakeMicrosoft struct {
	server    *httptest.Server
	oid       string
	email     string
	challenge string
	nonce     string
}

func newFakeMicrosoft(t *testing.T) *fakeMicrosoft {
	t.Helper()
	fake := &fakeMicrosoft{oid: "object-1", email: "Dev@CSIS.or.id"}
	fake.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/"+testTenant+"/oauth2/v2.0/token" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		if err := r.ParseForm(); err != nil {
			t.Errorf("token form: %v", err)
		}
		sum := sha256.Sum256([]byte(r.PostForm.Get("code_verifier")))
		if base64.RawURLEncoding.EncodeToString(sum[:]) != fake.challenge {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid_grant"})
			return
		}
		if r.PostForm.Get("client_secret") != "secret" || r.PostForm.Get("code") != "the-code" {
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid_client"})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"id_token": idToken(map[string]any{
				"iss":   fake.server.URL + "/" + testTenant + "/v2.0",
				"aud":   testClient,
				"exp":   time.Now().Add(time.Hour).Unix(),
				"nbf":   time.Now().Add(-time.Minute).Unix(),
				"nonce": fake.nonce,
				"tid":   testTenant,
				"oid":   fake.oid,
				"name":  "Dev Person",
				"email": fake.email,
			}),
		})
	}))
	t.Cleanup(fake.server.Close)
	return fake
}

func idToken(claims map[string]any) string {
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256","typ":"JWT"}`))
	payload, _ := json.Marshal(claims)
	return header + "." + base64.RawURLEncoding.EncodeToString(payload) + ".signature"
}

func microsoftServer(
	t *testing.T, fake *fakeMicrosoft, newAccounts string, domains ...string,
) (http.Handler, *auth.Service) {
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
	cfg := &config.Config{
		PortalOrigin: portal,
		Auth: config.Auth{Required: true, Registration: true, Microsoft: config.Microsoft{
			TenantID:     testTenant,
			ClientID:     testClient,
			ClientSecret: "secret",
			RedirectURL:  "https://api.example.org/v1/auth/microsoft/callback",
			Domains:      domains,
			NewAccounts:  newAccounts,
			Authority:    fake.server.URL,
		}},
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(cfg, nil, nil, nil, accounts, nil, nil, nil, nil, log).Routes(), accounts
}

// signIn walks the whole flow and returns where the browser ends up, and the
// session cookie if one was set.
func signIn(t *testing.T, h http.Handler, fake *fakeMicrosoft, redirect string) (string, string) {
	t.Helper()
	start := httptest.NewRecorder()
	h.ServeHTTP(start, httptest.NewRequest(http.MethodGet,
		"/v1/auth/microsoft/start?redirect="+url.QueryEscape(redirect), nil))
	if start.Code != http.StatusFound {
		t.Fatalf("start = %d: %s", start.Code, start.Body)
	}
	to, err := url.Parse(start.Header().Get("Location"))
	if err != nil || !strings.HasPrefix(to.String(), fake.server.URL+"/"+testTenant+"/oauth2/v2.0/authorize") {
		t.Fatalf("start redirected to %q", start.Header().Get("Location"))
	}
	fake.challenge = to.Query().Get("code_challenge")
	fake.nonce = to.Query().Get("nonce")

	callback := httptest.NewRequest(http.MethodGet, "/v1/auth/microsoft/callback?"+url.Values{
		"code": {"the-code"}, "state": {to.Query().Get("state")},
	}.Encode(), nil)
	for _, c := range start.Result().Cookies() {
		callback.AddCookie(c)
	}
	done := httptest.NewRecorder()
	h.ServeHTTP(done, callback)
	if done.Code != http.StatusFound {
		t.Fatalf("callback = %d: %s", done.Code, done.Body)
	}
	session := ""
	for _, c := range done.Result().Cookies() {
		if c.Name == SessionCookie && c.Value != "" {
			session = c.Value
		}
	}
	return done.Header().Get("Location"), session
}

func TestMicrosoftSignInStartsASession(t *testing.T) {
	fake := newFakeMicrosoft(t)
	h, _ := microsoftServer(t, fake, auth.NewAccountsActive, "csis.or.id")

	where, cookie := signIn(t, h, fake, "/datasets?page=2")
	if where != portal+"/datasets?page=2" || cookie == "" {
		t.Fatalf("ended at %q with session %q", where, cookie)
	}
	me := call(h, http.MethodGet, "/v1/auth/me", cookie, "")
	var got Response[auth.Session]
	if err := json.Unmarshal(me.Body.Bytes(), &got); err != nil ||
		got.Data.User.Email != "dev@csis.or.id" || got.Data.User.Name != "Dev Person" {
		t.Fatalf("GET /v1/auth/me = %d: %s", me.Code, me.Body)
	}

	// The second time finds the same account by its directory object, even
	// after the directory renamed the address.
	fake.email = "renamed@csis.or.id"
	if _, cookie := signIn(t, h, fake, "/"); cookie == "" {
		t.Fatal("a renamed address could not sign back in")
	}
	me = call(h, http.MethodGet, "/v1/auth/me", cookie, "")
	if !strings.Contains(me.Body.String(), "dev@csis.or.id") {
		t.Fatalf("the second sign-in reached another account: %s", me.Body)
	}
}

func TestMicrosoftSignInWaitsForApproval(t *testing.T) {
	fake := newFakeMicrosoft(t)
	h, accounts := microsoftServer(t, fake, auth.NewAccountsPending)

	where, cookie := signIn(t, h, fake, "/")
	if cookie != "" || where != portal+"/login?sso_error=pending" {
		t.Fatalf("ended at %q with session %q", where, cookie)
	}
	user, err := accounts.UserByEmail(context.Background(), "dev@csis.or.id")
	if err != nil || user.Status != auth.StatusPending {
		t.Fatalf("UserByEmail = %+v, %v; want a pending account", user, err)
	}
	// A pending account cannot be got into with a password either.
	if _, _, err := accounts.Login(context.Background(), user.Email, "", "test", false); err == nil {
		t.Fatal("an empty password signed a single-sign-on account in")
	}
}

func TestMicrosoftSignInRefusals(t *testing.T) {
	t.Run("other domain", func(t *testing.T) {
		fake := newFakeMicrosoft(t)
		fake.email = "someone@gmail.com"
		h, _ := microsoftServer(t, fake, auth.NewAccountsActive, "csis.or.id")
		if where, cookie := signIn(t, h, fake, "/"); cookie != "" ||
			where != portal+"/login?sso_error=domain" {
			t.Fatalf("ended at %q with session %q", where, cookie)
		}
	})
	t.Run("not invited", func(t *testing.T) {
		fake := newFakeMicrosoft(t)
		h, _ := microsoftServer(t, fake, auth.NewAccountsRefuse)
		if where, _ := signIn(t, h, fake, "/"); where != portal+"/login?sso_error=not_invited" {
			t.Fatalf("ended at %q", where)
		}
	})
	t.Run("address linked to someone else", func(t *testing.T) {
		fake := newFakeMicrosoft(t)
		h, accounts := microsoftServer(t, fake, auth.NewAccountsRefuse)
		if _, err := accounts.PutUser(context.Background(),
			"dev@csis.or.id", "a-long-enough-passphrase", "", ""); err != nil {
			t.Fatalf("PutUser: %v", err)
		}
		// The first directory identity links the existing account …
		if _, cookie := signIn(t, h, fake, "/"); cookie == "" {
			t.Fatal("an existing account was not linked")
		}
		// … and a different one arriving with the same address is refused.
		fake.oid = "object-2"
		if where, cookie := signIn(t, h, fake, "/"); cookie != "" ||
			where != portal+"/login?sso_error=conflict" {
			t.Fatalf("ended at %q with session %q", where, cookie)
		}
	})
	t.Run("callback without the attempt cookie", func(t *testing.T) {
		fake := newFakeMicrosoft(t)
		h, _ := microsoftServer(t, fake, auth.NewAccountsActive)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
			"/v1/auth/microsoft/callback?code=the-code&state=forged", nil))
		if w.Header().Get("Location") != portal+"/login?sso_error=expired" {
			t.Fatalf("ended at %q", w.Header().Get("Location"))
		}
	})
	t.Run("cancelled at Microsoft", func(t *testing.T) {
		fake := newFakeMicrosoft(t)
		h, _ := microsoftServer(t, fake, auth.NewAccountsActive)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
			"/v1/auth/microsoft/callback?error=access_denied", nil))
		if w.Header().Get("Location") != portal+"/login?sso_error=cancelled" {
			t.Fatalf("ended at %q", w.Header().Get("Location"))
		}
	})
}

func TestMicrosoftTokenChecks(t *testing.T) {
	now := time.Now()
	issuer := "https://login.microsoftonline.com/" + testTenant + "/v2.0"
	good := map[string]any{
		"iss": issuer, "aud": testClient, "exp": now.Add(time.Hour).Unix(),
		"nonce": "n", "tid": testTenant, "oid": "o", "email": "a@csis.or.id",
	}
	if _, err := checkMicrosoftToken(idToken(good), testTenant, testClient, issuer, "n", now); err != nil {
		t.Fatalf("a good token was refused: %v", err)
	}
	for name, change := range map[string]map[string]any{
		"other tenant":      {"tid": "99999999-0000-0000-0000-000000000000"},
		"other issuer":      {"iss": "https://evil.example/v2.0"},
		"other application": {"aud": "someone-else"},
		"other attempt":     {"nonce": "m"},
		"expired":           {"exp": now.Add(-time.Hour).Unix()},
		"no object":         {"oid": ""},
		"no address":        {"email": "", "preferred_username": ""},
	} {
		claims := map[string]any{}
		for k, v := range good {
			claims[k] = v
		}
		for k, v := range change {
			claims[k] = v
		}
		if _, err := checkMicrosoftToken(idToken(claims), testTenant, testClient, issuer, "n", now); err == nil {
			t.Errorf("%s: token was accepted", name)
		}
	}
}

func TestPortalPathStaysInThePortal(t *testing.T) {
	for in, want := range map[string]string{
		"/datasets?x=1":       "/datasets?x=1",
		"":                    "/",
		"https://evil.com":    "/",
		"//evil.com":          "/",
		"/\\evil.com":         "/",
		"/v1/auth/tokens":     "/",
		"/ok\r\nSet-Cookie:x": "/",
	} {
		if got := portalPath(in); got != want {
			t.Errorf("portalPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRegistrationSendsTheDirectorysDomainsToMicrosoft(t *testing.T) {
	fake := newFakeMicrosoft(t)
	h, _ := microsoftServer(t, fake, auth.NewAccountsActive, "csis.or.id")

	w := send(h, http.MethodPost, "/v1/auth/register", "",
		`{"email":"Budi@CSIS.or.id","password":"a-long-enough-passphrase","name":"Budi"}`)
	if w.Code != http.StatusForbidden {
		t.Fatalf("registering an address on the directory's domain = %d, want 403: %s", w.Code, w.Body)
	}
	// Somebody from outside still asks the ordinary way.
	w = send(h, http.MethodPost, "/v1/auth/register", "",
		`{"email":"visitor@example.org","password":"a-long-enough-passphrase","name":"Visitor"}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("registering an outside address = %d, want 202: %s", w.Code, w.Body)
	}
}

// Somebody registers a colleague's address with a password of their own
// before the colleague has ever signed in. When the colleague does, through
// Microsoft, the account becomes theirs, and the stranger's password must not
// open it.
func TestMicrosoftSignInEndsAStrangersRegistration(t *testing.T) {
	fake := newFakeMicrosoft(t)
	// No domain list, so the form does not turn the address away and the
	// sign-in is what has to catch it.
	h, _ := microsoftServer(t, fake, auth.NewAccountsActive)

	const stolen = `{"email":"dev@csis.or.id","password":"the-strangers-passphrase"}`
	w := send(h, http.MethodPost, "/v1/auth/register", "",
		`{"email":"dev@csis.or.id","password":"the-strangers-passphrase","name":"Dev"}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("registering = %d: %s", w.Code, w.Body)
	}

	if _, cookie := signIn(t, h, fake, "/"); cookie == "" {
		t.Fatal("the address's owner could not sign in with Microsoft")
	}
	if w := send(h, http.MethodPost, "/v1/auth/login", "", stolen); w.Code == http.StatusOK {
		t.Fatalf("the stranger's password still opens the account: %s", w.Body)
	}
}

// The mail attribute is not proof of anything, so it only names the account
// where it agrees with the sign-in name or Microsoft vouches for its domain.
func TestMicrosoftAddressTrustsTheSignInName(t *testing.T) {
	for _, c := range []struct {
		name   string
		claims string
		want   string
	}{
		{"the same", `{"email":"Dev@csis.or.id","preferred_username":"dev@csis.or.id"}`, "dev@csis.or.id"},
		{"mail set to somebody else's", `{"email":"boss@csis.or.id","preferred_username":"intern@csis.or.id"}`, "intern@csis.or.id"},
		{"verified domain", `{"email":"dev@csis.or.id","preferred_username":"dev@csis.onmicrosoft.com","xms_edov":true}`, "dev@csis.or.id"},
		{"verified as a string", `{"email":"dev@csis.or.id","preferred_username":"dev@csis.onmicrosoft.com","xms_edov":"1"}`, "dev@csis.or.id"},
		{"no mail", `{"preferred_username":"dev@csis.or.id"}`, "dev@csis.or.id"},
		{"sign-in name not an address", `{"email":"dev@csis.or.id","preferred_username":"dev"}`, "dev@csis.or.id"},
	} {
		var claims microsoftClaims
		if err := json.Unmarshal([]byte(c.claims), &claims); err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if got := claims.address(); got != c.want {
			t.Errorf("%s: address() = %q, want %q", c.name, got, c.want)
		}
	}
}
