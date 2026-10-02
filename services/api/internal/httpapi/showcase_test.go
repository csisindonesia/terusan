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
	"github.com/csis/terusan/services/api/internal/showcase"
)

// showcaseAPI is the whole API with a showcase, one admin and one researcher.
func showcaseAPI(t *testing.T) (h http.Handler, adminCookie, researcherCookie string) {
	h, adminCookie, researcherCookie, _, _ = showcaseAPIWithTokens(t)
	return h, adminCookie, researcherCookie
}

// showcaseAPIWithTokens also issues each of the two accounts an API token.
func showcaseAPIWithTokens(t *testing.T) (h http.Handler, adminCookie, researcherCookie, adminToken, researcherToken string) {
	t.Helper()
	db, err := appdb.Open(filepath.Join(t.TempDir(), "app.duckdb"))
	if err != nil {
		t.Fatalf("appdb.Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	accounts, err := auth.New(db, auth.Config{Iterations: 1000})
	if err != nil {
		t.Fatal(err)
	}
	shown, err := showcase.New(db)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	const password = "a-long-enough-passphrase"
	tokens := map[string]string{}
	for _, who := range []struct{ email, role string }{
		{"boss@example.org", auth.RoleAdmin}, {"reader@example.org", auth.RoleResearcher},
	} {
		user, err := accounts.PutUser(ctx, who.email, password, "", who.role)
		if err != nil {
			t.Fatal(err)
		}
		secret, _, err := accounts.CreateToken(ctx, user.ID, "script", 30)
		if err != nil {
			t.Fatal(err)
		}
		tokens[who.role] = secret
	}
	adminCookie, _, _ = accounts.Login(ctx, "boss@example.org", password, "test", false)
	researcherCookie, _, _ = accounts.Login(ctx, "reader@example.org", password, "test", false)

	cfg := &config.Config{Auth: config.Auth{Required: false}}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(cfg, nil, nil, nil, accounts, nil, shown, nil, nil, log).Routes(),
		adminCookie, researcherCookie, tokens[auth.RoleAdmin], tokens[auth.RoleResearcher]
}

const dashboard = `{"title":"Fiscal monitoring","kind":"dashboard",
	"url":"https://example.org/fiscal","publisher":"CSIS","published_on":"2026-07-22",
	"datasets":["urn5cu46"]}`

// An entry is the warehouse vouching for a piece of work, so a session is not
// enough to add one — but anyone may read the list.
func TestOnlyAnAdminChangesTheShowcase(t *testing.T) {
	h, boss, reader := showcaseAPI(t)

	if got := send(h, http.MethodPost, "/v1/showcase", "", dashboard).Code; got != http.StatusUnauthorized {
		t.Errorf("anonymous create = %d, want 401", got)
	}
	w := send(h, http.MethodPost, "/v1/showcase", reader, dashboard)
	if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "change the showcase") {
		t.Errorf("researcher create = %d %s, want 403 naming the showcase", w.Code, w.Body)
	}

	w = send(h, http.MethodPost, "/v1/showcase", boss, dashboard)
	if w.Code != http.StatusCreated {
		t.Fatalf("admin create = %d: %s", w.Code, w.Body)
	}
	var created struct{ Data ShowcaseEntry }
	if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if created.Data.CreatedByEmail != "boss@example.org" ||
		len(created.Data.Datasets) != 1 || created.Data.Datasets[0].DatasetID != "urn5cu46" {
		t.Errorf("created %+v", created.Data)
	}

	// Read without a session, narrowed to the dataset it uses.
	w = send(h, http.MethodGet, "/v1/showcase?dataset=urn5cu46", "", "")
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "Fiscal monitoring") {
		t.Fatalf("list by dataset = %d: %s", w.Code, w.Body)
	}

	path := "/v1/showcase/" + created.Data.ID
	if got := send(h, http.MethodDelete, path, reader, "").Code; got != http.StatusForbidden {
		t.Errorf("researcher delete = %d, want 403", got)
	}
	if got := send(h, http.MethodDelete, path, boss, "").Code; got != http.StatusOK {
		t.Errorf("admin delete = %d, want 200", got)
	}
}

func TestTheShowcaseRefusesALinkThatIsNotAWebPage(t *testing.T) {
	h, boss, _ := showcaseAPI(t)
	w := send(h, http.MethodPost, "/v1/showcase", boss,
		`{"title":"x","kind":"article","url":"javascript:alert(1)"}`)
	if w.Code != http.StatusBadRequest {
		t.Errorf("javascript: URL = %d, want 400", w.Code)
	}
}

// Unlike account management, the showcase can be curated from a script: an
// administrator's API token may write it. A researcher's still may not.
func TestAnAdminTokenMayChangeTheShowcase(t *testing.T) {
	h, _, _, boss, reader := showcaseAPIWithTokens(t)
	post := func(token string) int {
		r := httptest.NewRequest(http.MethodPost, "/v1/showcase", strings.NewReader(dashboard))
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w.Code
	}
	if got := post(reader); got != http.StatusForbidden {
		t.Errorf("researcher token create = %d, want 403", got)
	}
	if got := post(boss); got != http.StatusCreated {
		t.Errorf("admin token create = %d, want 201", got)
	}
}

// The Portal's own articles are filed by their path, which the portal opens in
// place. A path that is anything else is refused, and so is a `//host`.
func TestTheShowcaseTakesAPortalArticleByItsPath(t *testing.T) {
	h, boss, _ := showcaseAPI(t)
	for url, want := range map[string]int{
		"/portal/rice-after-the-dry-season": http.StatusCreated,
		"//evil.example/portal/x":           http.StatusBadRequest,
		"/datasets/uv7fqbgj":                http.StatusBadRequest,
		"/portal/../admin":                  http.StatusBadRequest,
	} {
		w := send(h, http.MethodPost, "/v1/showcase", boss,
			`{"title":"x","kind":"article","url":"`+url+`"}`)
		if w.Code != want {
			t.Errorf("url %q = %d, want %d: %s", url, w.Code, want, w.Body)
		}
	}
}
