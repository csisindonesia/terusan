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
)

// shelved is the API with a writable shelf and three signed-in accounts,
// returned as their session cookies.
func shelved(t *testing.T) (http.Handler, map[string]string) {
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
	accounts, err := auth.New(db, auth.Config{Iterations: 1000})
	if err != nil {
		t.Fatalf("auth.New: %v", err)
	}
	ctx := context.Background()
	cookies := map[string]string{}
	for _, name := range []string{"ana", "budi", "citra"} {
		email := name + "@example.org"
		if _, err := accounts.PutUser(ctx, email, "a-long-enough-passphrase", name, ""); err != nil {
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
	return New(cfg, nil, nil, shelf, accounts, nil, nil, log).Routes(), cookies
}

func asViewer(h http.Handler, method, path, cookie, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.AddCookie(&http.Cookie{Name: SessionCookie, Value: cookie})
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestAMemberFilesIntoTheOwnersCollection(t *testing.T) {
	h, as := shelved(t)

	w := asViewer(h, http.MethodPost, "/v1/collections", as["ana"], `{"id":"col-1","name":"Reading"}`)
	if w.Code != http.StatusCreated {
		t.Fatalf("create = %d: %s", w.Code, w.Body)
	}
	if got := asViewer(h, http.MethodGet, "/v1/collections/col-1", as["budi"], "").Code; got != http.StatusNotFound {
		t.Fatalf("a stranger opening it = %d, want 404", got)
	}
	// Adding someone is the owner's, and a stranger cannot use it to learn
	// which addresses have accounts.
	if got := asViewer(h, http.MethodPost, "/v1/collections/col-1/members", as["budi"],
		`{"email":"citra@example.org"}`).Code; got != http.StatusNotFound {
		t.Fatalf("a stranger adding a member = %d, want 404", got)
	}
	if got := asViewer(h, http.MethodPost, "/v1/collections/col-1/members", as["ana"],
		`{"email":"nobody@example.org"}`).Code; got != http.StatusNotFound {
		t.Fatalf("adding an address with no account = %d, want 404", got)
	}

	w = asViewer(h, http.MethodPost, "/v1/collections/col-1/members", as["ana"], `{"email":"BUDI@example.org"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("add member = %d: %s", w.Code, w.Body)
	}
	var added Response[collectionView]
	json.Unmarshal(w.Body.Bytes(), &added)
	if len(added.Data.Members) != 1 || added.Data.Members[0].Email != "budi@example.org" ||
		added.Data.Owner == nil || added.Data.Owner.Email != "ana@example.org" || added.Data.Role != "owner" {
		t.Fatalf("after adding = %s", w.Body)
	}

	// What it is for: budi sends ana a record from the three-dots menu.
	w = asViewer(h, http.MethodPost, "/v1/collections/col-1/items", as["budi"],
		`{"items":[{"kind":"indicator","id":"73qf14je","label":"Road injuries"}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("member filing = %d: %s", w.Code, w.Body)
	}
	var list Response[[]collectionView]
	json.Unmarshal(asViewer(h, http.MethodGet, "/v1/collections", as["ana"], "").Body.Bytes(), &list)
	if len(list.Data) != 1 || len(list.Data[0].Items) != 1 {
		t.Fatalf("owner's list = %+v, want the folder with budi's item", list.Data)
	}
	json.Unmarshal(asViewer(h, http.MethodGet, "/v1/collections", as["budi"], "").Body.Bytes(), &list)
	if len(list.Data) != 1 || list.Data[0].Role != "member" {
		t.Fatalf("member's list = %+v, want the folder as member", list.Data)
	}

	if got := asViewer(h, http.MethodPatch, "/v1/collections/col-1", as["budi"], `{"name":"Mine"}`).Code; got != http.StatusForbidden {
		t.Errorf("member renaming = %d, want 403", got)
	}
	if got := asViewer(h, http.MethodDelete, "/v1/collections/col-1", as["budi"], "").Code; got != http.StatusForbidden {
		t.Errorf("member deleting = %d, want 403", got)
	}

	// Leaving.
	var me Response[collectionView]
	json.Unmarshal(w.Body.Bytes(), &me)
	budi := me.Data.Members[0].ID
	if got := asViewer(h, http.MethodDelete, "/v1/collections/col-1/members/"+budi, as["budi"], "").Code; got != http.StatusOK {
		t.Fatalf("leaving = %d", got)
	}
	if got := asViewer(h, http.MethodGet, "/v1/collections/col-1", as["budi"], "").Code; got != http.StatusNotFound {
		t.Errorf("after leaving = %d, want 404", got)
	}
}

func TestACollectionsAPIIsOffUntilTheOwnerTurnsItOn(t *testing.T) {
	h, as := shelved(t)
	asViewer(h, http.MethodPost, "/v1/collections", as["ana"],
		`{"id":"col-1","name":"Prices","items":[{"id":"cpi00001","label":"CPI"}]}`)
	asViewer(h, http.MethodPost, "/v1/collections/col-1/members", as["ana"], `{"email":"budi@example.org"}`)

	path := "/v1/collections/col-1/observations"
	if got := asViewer(h, http.MethodGet, path, as["ana"], "").Code; got != http.StatusForbidden {
		t.Fatalf("API while off = %d, want 403", got)
	}
	if got := asViewer(h, http.MethodPatch, "/v1/collections/col-1", as["budi"], `{"api":true}`).Code; got != http.StatusForbidden {
		t.Fatalf("a member turning it on = %d, want 403", got)
	}
	w := asViewer(h, http.MethodPatch, "/v1/collections/col-1", as["ana"], `{"api":true}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"api":true`) {
		t.Fatalf("owner turning it on = %d: %s", w.Code, w.Body)
	}

	// On, it is still the collection's people only — a stranger learns
	// nothing, not even that the API is on.
	if got := asViewer(h, http.MethodGet, path, as["citra"], "").Code; got != http.StatusNotFound {
		t.Errorf("a stranger = %d, want 404", got)
	}
	// Narrowing to a series the collection does not hold is refused rather
	// than answered with less than was asked for.
	if got := asViewer(h, http.MethodGet, path+"?indicator=other001", as["budi"], "").Code; got != http.StatusBadRequest {
		t.Errorf("a series outside the collection = %d, want 400", got)
	}
}

func TestAnEmptyCollectionServesNothingRatherThanEverything(t *testing.T) {
	h, as := shelved(t)
	asViewer(h, http.MethodPost, "/v1/collections", as["ana"], `{"id":"col-1","name":"Empty"}`)
	asViewer(h, http.MethodPatch, "/v1/collections/col-1", as["ana"], `{"api":true}`)
	for _, path := range []string{"/observations", "/observations/series", "/observations/facets", "/indicators"} {
		if got := asViewer(h, http.MethodGet, "/v1/collections/col-1"+path, as["ana"], "").Code; got != http.StatusNotFound {
			t.Errorf("GET %s on an empty collection = %d, want 404", path, got)
		}
	}
}

func TestCollectionsHoldIndicatorsOnly(t *testing.T) {
	h, as := shelved(t)
	asViewer(h, http.MethodPost, "/v1/collections", as["ana"], `{"id":"col-1","name":"Mixed"}`)
	w := asViewer(h, http.MethodPost, "/v1/collections/col-1/items", as["ana"],
		`{"items":[{"kind":"dataset","id":"118v9w17","label":"Survey"}]}`)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("filing a dataset = %d, want 400: %s", w.Code, w.Body)
	}
}
