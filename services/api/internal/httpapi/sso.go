package httpapi

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/auth"
)

// Logging in with a Microsoft 365 work account.
//
// OpenID Connect's authorization-code flow against one Entra ID tenant, with
// PKCE, written against the standard library rather than an OAuth package:
// the flow is two redirects and one POST, and every check that matters is a
// few lines that are easier to read here than to find in a dependency.
//
//  1. /v1/auth/microsoft/start sends the browser to Microsoft, carrying a
//     random state, nonce and PKCE challenge. The secrets behind them ride in
//     a short-lived HttpOnly cookie, so nothing is held in this process and a
//     restart in the middle of somebody's sign-in costs them only a retry.
//  2. Microsoft authenticates the person — password, MFA, whatever the
//     office's conditional-access policy demands — and sends the browser back
//     to /v1/auth/microsoft/callback with a code.
//  3. The callback checks the state against the cookie, trades the code for
//     an ID token directly with Microsoft's token endpoint, checks the token
//     names this tenant, this application and this attempt, and starts an
//     ordinary session — the same cookie a password login gets.
//
// The ID token's signature is not verified against Microsoft's published
// keys. OpenID Connect Core §3.1.3.7 allows exactly this for a token received
// straight from the token endpoint over TLS, in exchange for this
// application's client secret: the TLS connection to login.microsoftonline.com
// already proves who issued it, and nobody else could have put it in that
// response. The claims are still checked one by one.
//
// Every failure ends back on the portal's login page with a short code in the
// URL, never a JSON error: the browser arrived here by following a redirect,
// and a page of JSON is a dead end for the person standing at it.

const (
	ssoCookie    = "terusan_sso"
	ssoPath      = "/v1/auth/microsoft"
	ssoTTL       = 10 * time.Minute
	ssoClockSkew = 5 * time.Minute
)

// ssoAttempt is what the cookie carries between leaving for Microsoft and
// coming back.
type ssoAttempt struct {
	State    string `json:"s"`
	Nonce    string `json:"n"`
	Verifier string `json:"v"`
	// Where in the portal to land once signed in. Only ever a path.
	Redirect string `json:"r"`
	Remember bool   `json:"m,omitempty"`
}

func (s *Server) microsoftReady(w http.ResponseWriter, r *http.Request) bool {
	if s.auth == nil || !s.cfg.Auth.Microsoft.Enabled() {
		s.ssoFail(w, r, "unavailable", "")
		return false
	}
	return true
}

// handleMicrosoftStart sends the browser to Microsoft's sign-in page.
func (s *Server) handleMicrosoftStart(w http.ResponseWriter, r *http.Request) {
	if !s.microsoftReady(w, r) {
		return
	}
	ms := s.cfg.Auth.Microsoft
	attempt := ssoAttempt{
		State:    randomToken(),
		Nonce:    randomToken(),
		Verifier: randomToken(),
		Redirect: portalPath(r.URL.Query().Get("redirect")),
		Remember: r.URL.Query().Get("remember") == "1" ||
			r.URL.Query().Get("remember") == "true",
	}
	raw, err := json.Marshal(attempt)
	if err != nil {
		s.ssoFail(w, r, "failed", "")
		return
	}
	http.SetCookie(w, s.ssoAttemptCookie(r,
		base64.RawURLEncoding.EncodeToString(raw), int(ssoTTL.Seconds())))

	challenge := sha256.Sum256([]byte(attempt.Verifier))
	query := url.Values{
		"client_id":             {ms.ClientID},
		"response_type":         {"code"},
		"redirect_uri":          {ms.RedirectURL},
		"response_mode":         {"query"},
		"scope":                 {"openid profile email"},
		"state":                 {attempt.State},
		"nonce":                 {attempt.Nonce},
		"code_challenge":        {base64.RawURLEncoding.EncodeToString(challenge[:])},
		"code_challenge_method": {"S256"},
		// Lets somebody signed in to two Microsoft accounts pick the work one
		// rather than being signed in silently as whichever is current.
		"prompt": {"select_account"},
	}
	http.Redirect(w, r, ms.Endpoint("authorize")+"?"+query.Encode(), http.StatusFound)
}

// handleMicrosoftCallback is where Microsoft sends the browser back.
func (s *Server) handleMicrosoftCallback(w http.ResponseWriter, r *http.Request) {
	if !s.microsoftReady(w, r) {
		return
	}
	// The attempt is spent whatever happens next: a state that can be
	// replayed is not a state.
	attempt, ok := readAttempt(r)
	http.SetCookie(w, s.ssoAttemptCookie(r, "", -1))

	query := r.URL.Query()
	if code := query.Get("error"); code != "" {
		// access_denied is the person pressing back or declining consent;
		// anything else is Microsoft refusing them, which is worth logging.
		if code == "access_denied" {
			s.ssoFail(w, r, "cancelled", attempt.Redirect)
			return
		}
		s.log.Warn("auth.microsoft_refused", "error", code,
			"description", clipText(query.Get("error_description"), 300))
		s.ssoFail(w, r, "refused", attempt.Redirect)
		return
	}
	// Compared in constant time, and only against a cookie this browser was
	// given: a callback that arrives without one was started somewhere else,
	// which is what a login-CSRF looks like.
	if !ok || query.Get("state") == "" ||
		subtle.ConstantTimeCompare([]byte(query.Get("state")), []byte(attempt.State)) != 1 {
		s.ssoFail(w, r, "expired", attempt.Redirect)
		return
	}

	claims, err := s.redeemMicrosoftCode(r.Context(), query.Get("code"), attempt)
	if err != nil {
		s.log.Warn("auth.microsoft_token_failed", "error", err)
		s.ssoFail(w, r, "failed", attempt.Redirect)
		return
	}

	email := claims.address()
	if !s.cfg.Auth.Microsoft.Allows(email) {
		s.log.Info("auth.microsoft_domain_refused", "email", email)
		s.ssoFail(w, r, "domain", attempt.Redirect)
		return
	}

	token, session, err := s.auth.LoginExternal(r.Context(), auth.ExternalIdentity{
		Subject: "microsoft:" + claims.TenantID + ":" + claims.ObjectID,
		Email:   email,
		Name:    claims.Name,
	}, s.cfg.Auth.Microsoft.NewAccounts, r.UserAgent(), attempt.Remember)
	switch {
	case errors.Is(err, auth.ErrPending):
		s.log.Info("auth.microsoft_pending", "email", email)
		s.ssoFail(w, r, "pending", attempt.Redirect)
		return
	case errors.Is(err, auth.ErrDisabled):
		s.ssoFail(w, r, "disabled", attempt.Redirect)
		return
	case errors.Is(err, auth.ErrExpired):
		s.ssoFail(w, r, "access_ended", attempt.Redirect)
		return
	case errors.Is(err, auth.ErrNotInvited):
		s.log.Info("auth.microsoft_not_invited", "email", email)
		s.ssoFail(w, r, "not_invited", attempt.Redirect)
		return
	case errors.Is(err, auth.ErrLinkedElsewhere):
		s.log.Warn("auth.microsoft_linked_elsewhere", "email", email)
		s.ssoFail(w, r, "conflict", attempt.Redirect)
		return
	case errors.Is(err, auth.ErrTooManyPending):
		s.ssoFail(w, r, "queue_full", attempt.Redirect)
		return
	case err != nil:
		s.log.Error("auth.microsoft_login_failed", "error", err)
		s.ssoFail(w, r, "failed", attempt.Redirect)
		return
	}

	http.SetCookie(w, s.sessionCookie(r, token, session.ExpiresAt))
	if err := s.auth.RecordAccess(r.Context(), session.User.ID,
		auth.AccessLogin, s.client(r)); err != nil {
		s.log.Warn("auth.access_log_failed", "error", err)
	}
	s.log.Info("auth.login", "user", session.User.Email, "via", "microsoft")
	http.Redirect(w, r, s.cfg.PortalOrigin+attempt.Redirect, http.StatusFound)
}

// ssoFail sends the browser back to the login page with a reason the page
// knows how to say. The reason is one of a fixed set of words, never text from
// Microsoft or the request, so nothing a stranger writes ends up in the page.
func (s *Server) ssoFail(w http.ResponseWriter, r *http.Request, reason, redirect string) {
	query := url.Values{"sso_error": {reason}}
	if redirect != "" && redirect != "/" {
		query.Set("redirect", redirect)
	}
	http.Redirect(w, r, s.cfg.PortalOrigin+"/login?"+query.Encode(), http.StatusFound)
}

func (s *Server) ssoAttemptCookie(r *http.Request, value string, maxAge int) *http.Cookie {
	return &http.Cookie{
		Name:     ssoCookie,
		Value:    value,
		Path:     ssoPath,
		MaxAge:   maxAge,
		HttpOnly: true,
		// Lax, not Strict: the callback is a top-level navigation arriving from
		// login.microsoftonline.com, and Strict would leave the cookie behind.
		SameSite: http.SameSiteLaxMode,
		Secure:   s.cfg.Auth.SecureCookies || r.TLS != nil,
	}
}

func readAttempt(r *http.Request) (ssoAttempt, bool) {
	cookie, err := r.Cookie(ssoCookie)
	if err != nil || cookie.Value == "" {
		return ssoAttempt{Redirect: "/"}, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(cookie.Value)
	if err != nil {
		return ssoAttempt{Redirect: "/"}, false
	}
	var attempt ssoAttempt
	if err := json.Unmarshal(raw, &attempt); err != nil || attempt.State == "" {
		return ssoAttempt{Redirect: "/"}, false
	}
	// Checked again on the way out, though this process wrote it: the cookie
	// came back from a browser.
	attempt.Redirect = portalPath(attempt.Redirect)
	return attempt, true
}

// microsoftClaims are the parts of an Entra ID token this reads.
type microsoftClaims struct {
	Issuer    string          `json:"iss"`
	Audience  json.RawMessage `json:"aud"`
	Expires   int64           `json:"exp"`
	NotBefore int64           `json:"nbf"`
	Nonce     string          `json:"nonce"`
	TenantID  string          `json:"tid"`
	// The person's object ID in the directory: stable for as long as their
	// entry exists, and never reused for anyone else.
	ObjectID          string `json:"oid"`
	Name              string `json:"name"`
	Email             string `json:"email"`
	PreferredUsername string `json:"preferred_username"`
	// Whether the domain of `email` is one the tenant has proved it owns,
	// sent only when the app registration asks for this optional claim.
	EmailDomainVerified flexBool `json:"xms_edov"`
}

// address is who this is, as an email.
//
// The sign-in name is the user's principal name, which only a directory
// admin can set and which has to be on one of the tenant's verified domains.
// The `email` claim is the mail attribute, which Microsoft documents as
// unverified and which can, depending on the tenant, be edited by people who
// are not directory admins. An account is matched by address the first time
// it signs in, so a person whose mail attribute was set to somebody else's
// address would be linked to that somebody's account here. The `email` claim
// is therefore only trusted where it says the same as the sign-in name, or
// where Microsoft says its domain is verified; otherwise the sign-in name is
// who this is.
func (c microsoftClaims) address() string {
	email := auth.NormalizeEmail(strings.TrimSpace(c.Email))
	upn := auth.NormalizeEmail(strings.TrimSpace(c.PreferredUsername))
	switch {
	case email == "":
		return upn
	case upn == "" || !auth.ValidEmail(upn) || email == upn || bool(c.EmailDomainVerified):
		return email
	default:
		return upn
	}
}

// flexBool reads a boolean claim that providers send as true, "true" or "1".
type flexBool bool

func (b *flexBool) UnmarshalJSON(raw []byte) error {
	switch strings.Trim(strings.ToLower(string(raw)), `"`) {
	case "true", "1":
		*b = true
	default:
		*b = false
	}
	return nil
}

// redeemMicrosoftCode trades the code for an ID token and checks it.
func (s *Server) redeemMicrosoftCode(
	ctx context.Context, code string, attempt ssoAttempt,
) (microsoftClaims, error) {
	ms := s.cfg.Auth.Microsoft
	if code == "" {
		return microsoftClaims{}, errors.New("no code in the callback")
	}
	form := url.Values{
		"client_id":     {ms.ClientID},
		"client_secret": {ms.ClientSecret},
		"grant_type":    {"authorization_code"},
		"code":          {code},
		"redirect_uri":  {ms.RedirectURL},
		"code_verifier": {attempt.Verifier},
		"scope":         {"openid profile email"},
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost,
		ms.Endpoint("token"), strings.NewReader(form.Encode()))
	if err != nil {
		return microsoftClaims{}, err
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return microsoftClaims{}, err
	}
	defer response.Body.Close()

	var body struct {
		IDToken     string `json:"id_token"`
		Error       string `json:"error"`
		Description string `json:"error_description"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&body); err != nil {
		return microsoftClaims{}, fmt.Errorf("token endpoint answered %d: %w",
			response.StatusCode, err)
	}
	if response.StatusCode != http.StatusOK || body.IDToken == "" {
		return microsoftClaims{}, fmt.Errorf("token endpoint answered %d: %s: %s",
			response.StatusCode, body.Error, clipText(body.Description, 300))
	}
	return checkMicrosoftToken(body.IDToken, ms.TenantID, ms.ClientID,
		ms.Issuer(), attempt.Nonce, time.Now())
}

// checkMicrosoftToken reads an ID token's claims and refuses one that was not
// issued by this tenant, for this application, for this attempt, just now.
func checkMicrosoftToken(
	idToken, tenantID, clientID, issuer, nonce string, now time.Time,
) (microsoftClaims, error) {
	parts := strings.Split(idToken, ".")
	if len(parts) != 3 {
		return microsoftClaims{}, errors.New("the ID token is not a JWT")
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return microsoftClaims{}, fmt.Errorf("ID token payload: %w", err)
	}
	var claims microsoftClaims
	if err := json.Unmarshal(payload, &claims); err != nil {
		return microsoftClaims{}, fmt.Errorf("ID token payload: %w", err)
	}

	// `aud` is a string in Entra's tokens, and a list in the general case.
	var audiences []string
	var single string
	if json.Unmarshal(claims.Audience, &single) == nil {
		audiences = []string{single}
	} else if json.Unmarshal(claims.Audience, &audiences) != nil {
		return microsoftClaims{}, errors.New("ID token audience is unreadable")
	}

	switch {
	case !strings.EqualFold(claims.TenantID, tenantID):
		return microsoftClaims{}, fmt.Errorf("ID token is from tenant %q", claims.TenantID)
	case claims.Issuer != issuer:
		return microsoftClaims{}, fmt.Errorf("ID token issuer is %q", claims.Issuer)
	case !slices.Contains(audiences, clientID):
		return microsoftClaims{}, errors.New("ID token was issued to another application")
	case claims.Nonce == "" ||
		subtle.ConstantTimeCompare([]byte(claims.Nonce), []byte(nonce)) != 1:
		return microsoftClaims{}, errors.New("ID token nonce does not match this attempt")
	case now.After(time.Unix(claims.Expires, 0).Add(ssoClockSkew)):
		return microsoftClaims{}, errors.New("ID token has expired")
	case claims.NotBefore != 0 && now.Add(ssoClockSkew).Before(time.Unix(claims.NotBefore, 0)):
		return microsoftClaims{}, errors.New("ID token is not valid yet")
	case claims.ObjectID == "":
		return microsoftClaims{}, errors.New("ID token names no directory object")
	case !auth.ValidEmail(claims.address()):
		return microsoftClaims{}, errors.New("ID token carries no email address")
	}
	return claims, nil
}

// portalPath is a redirect target that cannot leave the portal: a plain path,
// or "/" for anything else. The same rule the login page applies, held here
// too because this end is the one that actually sends the browser.
func portalPath(value string) string {
	if !strings.HasPrefix(value, "/") || strings.HasPrefix(value, "//") ||
		strings.ContainsAny(value, "\\\r\n") || strings.HasPrefix(value, "/v1/") {
		return "/"
	}
	return value
}

func randomToken() string {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(raw)
}

func clipText(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}
