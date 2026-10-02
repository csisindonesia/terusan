package httpapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/auth"
)

// A page on another host of the same site sends the signed-in admin's cookie
// along with a request to create an account. SameSite=Lax lets the cookie
// through, so the API has to tell the portal's pages from everybody else's.
func TestAChangeFromAnotherSitesPageIsRefused(t *testing.T) {
	fake := newFakeMicrosoft(t)
	h, accounts := microsoftServer(t, fake, auth.NewAccountsActive)
	ctx := context.Background()
	const password = "a-long-enough-passphrase"
	if _, err := accounts.PutUser(ctx, "boss@example.org", password, "", auth.RoleAdmin); err != nil {
		t.Fatalf("PutUser: %v", err)
	}
	boss, _, err := accounts.Login(ctx, "boss@example.org", password, "test", false)
	if err != nil {
		t.Fatalf("Login: %v", err)
	}

	create := func(email string, headers map[string]string) int {
		r := httptest.NewRequest(http.MethodPost, "/v1/admin/users", strings.NewReader(
			`{"email":"`+email+`","password":"`+password+`","role":"admin"}`))
		r.Header.Set("Content-Type", "text/plain")
		r.AddCookie(&http.Cookie{Name: SessionCookie, Value: boss})
		for name, value := range headers {
			r.Header.Set(name, value)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w.Code
	}

	for name, headers := range map[string]map[string]string{
		"same-site page":  {"Sec-Fetch-Site": "same-site", "Origin": "https://blog.example.org"},
		"cross-site page": {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example.com"},
		// An older browser that sends Origin and no Sec-Fetch-Site.
		"origin only": {"Origin": "https://blog.example.org"},
	} {
		if got := create("planted-"+strings.ReplaceAll(name, " ", "-")+"@example.org", headers); got != http.StatusForbidden {
			t.Errorf("%s: creating an account = %d, want 403", name, got)
		}
	}
	if _, err := accounts.UserByEmail(ctx, "planted-same-site-page@example.org"); err == nil {
		t.Error("the same-site page's account was created")
	}

	// The portal itself, and a client that is not a browser, still get in.
	if got := create("from-portal@example.org",
		map[string]string{"Sec-Fetch-Site": "same-site", "Origin": portal}); got != http.StatusCreated && got != http.StatusOK {
		t.Errorf("the portal creating an account = %d, want success", got)
	}
	if got := create("from-script@example.org", nil); got != http.StatusCreated && got != http.StatusOK {
		t.Errorf("a script creating an account = %d, want success", got)
	}
}
