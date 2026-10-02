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
	"github.com/csis/terusan/services/api/internal/collections"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/suggestions"
)

// staffed is the API with a shelf, a suggestion queue, an administrator and
// two researchers, returned as their session cookies, and the database
// behind them for setting up what no route can (a folder from before owners).
func staffed(t *testing.T) (http.Handler, map[string]string, *appdb.DB) {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	shelf, err := collections.New(db, true)
	if err != nil {
		t.Fatalf("collections.New: %v", err)
	}
	asked, err := suggestions.New(db)
	if err != nil {
		t.Fatalf("suggestions.New: %v", err)
	}
	accounts, err := auth.New(db, auth.Config{Iterations: 1000})
	if err != nil {
		t.Fatalf("auth.New: %v", err)
	}
	ctx := context.Background()
	cookies := map[string]string{}
	for name, role := range map[string]string{
		"boss": auth.RoleAdmin, "ana": auth.RoleResearcher, "budi": auth.RoleResearcher,
	} {
		email := name + "@example.org"
		if _, err := accounts.PutUser(ctx, email, "a-long-enough-passphrase", name, role); err != nil {
			t.Fatalf("PutUser: %v", err)
		}
		cookie, _, err := accounts.Login(ctx, email, "a-long-enough-passphrase", "test", false)
		if err != nil {
			t.Fatalf("Login: %v", err)
		}
		cookies[name] = cookie
	}
	cfg := &config.Config{Auth: config.Auth{Required: true}}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(cfg, nil, nil, shelf, accounts, asked, nil, nil, nil, log).Routes(), cookies, db
}

func TestOnlyAnAdministratorStartsAPipeline(t *testing.T) {
	h, as, _ := staffed(t)
	for _, path := range []string{"/v1/sources/bps/run"} {
		w := asViewer(h, http.MethodPost, path, as["ana"], "")
		if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "administrator") {
			t.Errorf("a researcher's POST %s = %d: %s", path, w.Code, w.Body)
		}
		// The administrator gets past the role and on to the deployment's
		// own answer, which here is that it runs no pipelines.
		w = asViewer(h, http.MethodPost, path, as["boss"], "")
		if strings.Contains(w.Body.String(), "administrator") {
			t.Errorf("the administrator's POST %s was refused for the role: %s", path, w.Body)
		}
	}
}

func TestASuggestionIsTriagedByAnAdministrator(t *testing.T) {
	h, as, _ := staffed(t)
	w := asViewer(h, http.MethodPost, "/v1/suggestions", as["ana"],
		`{"title":"Harga beras","url":"https://example.org/beras","cadence":"monthly"}`)
	if w.Code != http.StatusCreated {
		t.Fatalf("suggesting = %d: %s", w.Code, w.Body)
	}
	var created Response[suggestions.Suggestion]
	if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
		t.Fatalf("decode: %v", err)
	}
	path := "/v1/suggestions/" + created.Data.ID

	if got := asViewer(h, http.MethodPatch, path, as["budi"], `{"status":"declined"}`).Code; got != http.StatusForbidden {
		t.Errorf("another researcher triaging = %d, want 403", got)
	}
	if got := asViewer(h, http.MethodDelete, path, as["budi"], "").Code; got != http.StatusNotFound {
		t.Errorf("another researcher deleting = %d, want 404", got)
	}
	if got := asViewer(h, http.MethodPatch, path, as["boss"], `{"status":"planned"}`).Code; got != http.StatusOK {
		t.Errorf("the administrator triaging = %d, want 200", got)
	}
	w = asViewer(h, http.MethodPatch, path, as["boss"],
		`{"status":"declined","reason":"already-collected","dataset_id":"pihps-prices"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("declining as already collected = %d: %s", w.Code, w.Body)
	}
	var declined Response[suggestions.Suggestion]
	if err := json.Unmarshal(w.Body.Bytes(), &declined); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if declined.Data.DatasetID != "pihps-prices" || declined.Data.ReviewedByEmail != "boss@example.org" {
		t.Errorf("the decision lost its dataset or its author: %+v", declined.Data)
	}
	if got := asViewer(h, http.MethodPatch, path, as["boss"],
		`{"status":"declined","reason":"already-collected","dataset_id":"no such/thing"}`).Code; got != http.StatusBadRequest {
		t.Errorf("a dataset id that is not one = %d, want 400", got)
	}
	if got := asViewer(h, http.MethodDelete, path, as["ana"], "").Code; got != http.StatusOK {
		t.Errorf("the person who suggested it withdrawing it = %d, want 200", got)
	}
}

func TestAFolderEveryoneSharesIsAnAdministratorsToChange(t *testing.T) {
	h, as, db := staffed(t)
	if w := asViewer(h, http.MethodPost, "/v1/collections", as["ana"],
		`{"id":"col-old","name":"From before owners"}`); w.Code != http.StatusCreated {
		t.Fatalf("create = %d: %s", w.Code, w.Body)
	}
	if _, err := db.Exec(`UPDATE collections SET owner_id = '' WHERE collection_id = 'col-old'`); err != nil {
		t.Fatalf("making it shared: %v", err)
	}

	// Everyone still reads it.
	if got := asViewer(h, http.MethodGet, "/v1/collections/col-old", as["budi"], "").Code; got != http.StatusOK {
		t.Fatalf("reading a shared folder = %d, want 200", got)
	}
	for _, c := range []struct{ method, path, body string }{
		{http.MethodPost, "/v1/collections/col-old/claim", ""},
		{http.MethodPatch, "/v1/collections/col-old", `{"visibility":"public"}`},
		{http.MethodDelete, "/v1/collections/col-old", ""},
	} {
		if got := asViewer(h, c.method, c.path, as["budi"], c.body).Code; got != http.StatusForbidden {
			t.Errorf("a researcher's %s %s = %d, want 403", c.method, c.path, got)
		}
	}
	if got := asViewer(h, http.MethodPost, "/v1/collections/col-old/claim", as["boss"], "").Code; got != http.StatusOK {
		t.Errorf("the administrator claiming it = %d, want 200", got)
	}
}

// A tunnel on the same machine connects from loopback on behalf of whoever is
// at the other end, so the first account cannot be made through one.
func TestTheFirstAccountIsNotMadeThroughATunnel(t *testing.T) {
	fake := newFakeMicrosoft(t)
	h, _ := microsoftServer(t, fake, auth.NewAccountsPending)
	bootstrap := func(headers map[string]string) int {
		r := httptest.NewRequest(http.MethodPost, "/v1/auth/bootstrap", strings.NewReader(
			`{"email":"first@example.org","password":"a-long-enough-passphrase","name":"First"}`))
		r.RemoteAddr = "127.0.0.1:52000"
		for name, value := range headers {
			r.Header.Set(name, value)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w.Code
	}
	if got := bootstrap(map[string]string{"Cf-Connecting-Ip": "203.0.113.9"}); got == http.StatusCreated {
		t.Fatal("an account was bootstrapped through a tunnel")
	}
	if got := bootstrap(nil); got != http.StatusCreated {
		t.Fatalf("bootstrapping from this machine = %d, want 201", got)
	}
}

func TestEveryResponseRefusesToBeFramed(t *testing.T) {
	fake := newFakeMicrosoft(t)
	h, _ := microsoftServer(t, fake, auth.NewAccountsPending)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if w.Header().Get("X-Frame-Options") != "DENY" || w.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Errorf("/healthz headers = %v", w.Header())
	}
	for path, want := range map[string]bool{
		"/v1/documents/doc-1/file":          true,
		"/v1/documents/doc-1/file/page.pdf": true,
		"/v1/documents/doc-1":               false,
		"/v1/documents/doc-1/indicators":    false,
		"/v1/documents/file":                false,
	} {
		if got := isDocumentFile(path); got != want {
			t.Errorf("isDocumentFile(%q) = %v, want %v", path, got, want)
		}
	}
}

func TestTheSignInCallbackIsNotLoggedWithItsCode(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/v1/auth/microsoft/callback?code=secret&state=s", nil)
	if got := loggedQuery(r); strings.Contains(got, "secret") {
		t.Errorf("loggedQuery = %q", got)
	}
	r = httptest.NewRequest(http.MethodGet, "/v1/observations?indicator=x", nil)
	if got := loggedQuery(r); got != "indicator=x" {
		t.Errorf("loggedQuery = %q, want the filters kept", got)
	}
}
