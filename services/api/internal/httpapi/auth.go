package httpapi

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/auth"
)

// Logging in, staying logged in, and logging out.
//
// A session cookie rather than a token the page has to hold: a token in
// JavaScript is a token any script on the page can read, and the portal has no
// need to see it. HttpOnly puts it out of reach of the document entirely, and
// SameSite keeps it off cross-site requests.
//
// What a session buys is identity, not authority. Every data route answers the
// same way to everyone; `AUTH_REQUIRED` decides whether they answer to a
// stranger at all. That is the whole model today, and it is stated rather than
// dressed up (program.md §34–36).

// SessionCookie is the name the portal's session rides under.
const SessionCookie = "terusan_session"

func (s *Server) authReady(w http.ResponseWriter) bool {
	if s.auth == nil {
		writeError(w, http.StatusNotFound, CodeNotFound,
			"this serving layer has no accounts",
			"set APP_DB where the API runs, and create an account with authctl")
		return false
	}
	return true
}

type loginBody struct {
	Email    string `json:"email"`
	Password string `json:"password"`
	// Thirty days instead of a working day. The reader's choice, because the
	// right answer differs between a laptop on a desk and a shared terminal.
	Remember bool `json:"remember"`
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	var body loginBody
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}
	if strings.TrimSpace(body.Email) == "" || body.Password == "" {
		badRequest(w, "invalid body", "email and password are both required")
		return
	}

	token, session, err := s.auth.Login(
		r.Context(), body.Email, body.Password, r.UserAgent(), body.Remember)
	switch {
	case errors.Is(err, auth.ErrInvalidCredentials):
		// One message for a wrong password and for an address nobody has.
		// Telling them apart tells an attacker which addresses are worth
		// attacking.
		writeError(w, http.StatusUnauthorized, CodeUnauthorized,
			"invalid email or password", "")
		return
	case errors.Is(err, auth.ErrDisabled):
		writeError(w, http.StatusForbidden, CodeForbidden,
			"this account is disabled", "ask whoever administers this deployment")
		return
	case errors.Is(err, auth.ErrTooManyAttempts):
		writeError(w, http.StatusTooManyRequests, CodeTooManyRequests,
			"too many attempts", err.Error())
		return
	case err != nil:
		internalError(w, s.log, "login", err)
		return
	}

	http.SetCookie(w, s.sessionCookie(r, token, session.ExpiresAt))
	s.log.Info("auth.login", "user", session.User.Email)
	writeData(w, session, nil)
}

// handleBootstrap creates the first account on a deployment that has none.
//
// It exists because of how the application database is held: DuckDB locks the
// file to one process, so `authctl` cannot write to it while the API is
// running, and telling somebody to stop their server to create their first
// account is a poor first five minutes.
//
// Three things bound it, and all three are needed. It answers only while the
// accounts table is empty — the second call is a conflict, whoever makes it.
// It answers only to loopback, so an instance exposed to a network cannot be
// claimed by a stranger who found it before its owner did. And it grants
// exactly what `authctl create` grants, which is an account, on a deployment
// where an account by itself opens nothing that was closed.
func (s *Server) handleBootstrap(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	if !isLoopback(r) {
		writeError(w, http.StatusForbidden, CodeForbidden,
			"the first account can only be created from the machine the API runs on",
			"use authctl with the API stopped, or call this from localhost")
		return
	}

	count, err := s.auth.Count(r.Context())
	if err != nil {
		internalError(w, s.log, "count accounts", err)
		return
	}
	if count > 0 {
		writeError(w, http.StatusConflict, CodeConflict,
			"this deployment already has accounts",
			"create further accounts with authctl, which needs the API stopped")
		return
	}

	var body struct {
		Email    string `json:"email"`
		Password string `json:"password"`
		Name     string `json:"name"`
	}
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}

	user, err := s.auth.PutUser(r.Context(), body.Email, body.Password, body.Name, "admin")
	if errors.Is(err, auth.ErrWeakPassword) {
		badRequest(w, "invalid body",
			fmt.Sprintf("password: must be at least %d characters", auth.MinPasswordLength))
		return
	}
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	s.log.Info("auth.bootstrap", "user", user.Email)
	writeJSON(w, http.StatusCreated, Response[auth.User]{Data: user})
}

// isLoopback reports whether the request came from this machine.
//
// Read off the connection, never off a header: X-Forwarded-For is whatever the
// client wrote there, and trusting it here would make the loopback check a
// suggestion.
func isLoopback(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	if cookie, err := r.Cookie(SessionCookie); err == nil {
		if err := s.auth.Logout(r.Context(), cookie.Value); err != nil {
			internalError(w, s.log, "logout", err)
			return
		}
	}
	// Cleared whether or not there was a session to end: the caller is asking
	// to be logged out, and after this it is.
	http.SetCookie(w, s.sessionCookie(r, "", time.Unix(0, 0)))
	writeData(w, map[string]bool{"logged_out": true}, nil)
}

// handleMe is what the portal asks on every load: is there a session, and
// whose.
func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	session, err := s.session(r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, CodeUnauthorized, "not signed in", "")
		return
	}
	writeData(w, session, nil)
}

// The resolved session travels on the request context, so a handler that needs
// to know who is asking does not resolve the cookie a second time — and so
// there is exactly one place where a token becomes a person.
type contextKey int

const sessionContextKey contextKey = iota

type signedIn struct {
	session auth.Session
	// The raw token, needed by the operations that act on *other* sessions:
	// "sign out everywhere else" has to know which one to keep.
	token string
}

// withSession resolves the cookie once per request and puts the answer on the
// context. It refuses nothing — that is withAuth's job — so a signed-out
// request passes through with nothing attached.
func (s *Server) withSession(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s.auth == nil {
			next.ServeHTTP(w, r)
			return
		}
		cookie, err := r.Cookie(SessionCookie)
		if err != nil || cookie.Value == "" {
			next.ServeHTTP(w, r)
			return
		}
		session, err := s.auth.Session(r.Context(), cookie.Value)
		if err != nil {
			// An expired or unknown cookie is the same as none: the request
			// carries on as a stranger and whatever gate is in front of it
			// decides what that means.
			next.ServeHTTP(w, r)
			return
		}
		ctx := context.WithValue(r.Context(),
			sessionContextKey, signedIn{session: session, token: cookie.Value})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// session is who is asking, if anybody.
func (s *Server) session(r *http.Request) (auth.Session, error) {
	held, ok := r.Context().Value(sessionContextKey).(signedIn)
	if !ok {
		return auth.Session{}, auth.ErrNoSession
	}
	return held.session, nil
}

// sessionToken is the cookie this request arrived with, for the operations
// that have to tell this login from the others.
func (s *Server) sessionToken(r *http.Request) string {
	held, ok := r.Context().Value(sessionContextKey).(signedIn)
	if !ok {
		return ""
	}
	return held.token
}

// requireSession answers the request itself when nobody is signed in.
func (s *Server) requireSession(w http.ResponseWriter, r *http.Request) (auth.Session, bool) {
	session, err := s.session(r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, CodeUnauthorized,
			"not signed in", "log in through the portal, or POST /v1/auth/login")
		return auth.Session{}, false
	}
	return session, true
}

// sessionCookie builds the cookie, including the one that clears it.
func (s *Server) sessionCookie(r *http.Request, token string, expires time.Time) *http.Cookie {
	cookie := &http.Cookie{
		Name:  SessionCookie,
		Value: token,
		Path:  "/",
		// Out of reach of any script on the page: the portal never needs to
		// read this, and a token JavaScript can read is a token an injected
		// script can steal.
		HttpOnly: true,
		// Lax rather than Strict: the portal and the API are different origins
		// in development (`:3000` and `:8080`) and usually different
		// subdomains in a deployment, which is same-site either way, and Strict
		// would drop the cookie on a link followed in from elsewhere.
		SameSite: http.SameSiteLaxMode,
		// Only over TLS wherever there is TLS. Not unconditional, because a
		// Secure cookie on plain http://localhost is a cookie the browser
		// throws away and a login that silently never sticks.
		Secure:  s.cfg.Auth.SecureCookies || r.TLS != nil,
		Expires: expires,
	}
	if token == "" {
		cookie.MaxAge = -1
		return cookie
	}
	cookie.MaxAge = int(time.Until(expires).Seconds())
	return cookie
}

// ---- the account's own page -----------------------------------------------

// handleUpdateProfile changes what the signed-in person is called.
//
// The address is not editable: it is what the account is addressed by, and
// changing it is an administrative act (`authctl`) rather than a preference.
func (s *Server) handleUpdateProfile(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}
	var body struct {
		Name string `json:"name"`
	}
	if !decodeBody(w, r, &body, 4<<10) {
		return
	}
	user, err := s.auth.Rename(r.Context(), session.User.ID, body.Name)
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	writeData(w, user, nil)
}

// handleChangePassword replaces a password, given the current one.
func (s *Server) handleChangePassword(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}
	var body struct {
		Current string `json:"current_password"`
		Next    string `json:"new_password"`
	}
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}

	err := s.auth.ChangePassword(
		r.Context(), session.User.ID, body.Current, body.Next, s.sessionToken(r))
	switch {
	case errors.Is(err, auth.ErrInvalidCredentials):
		// The current password, not the new one. Said precisely, because this
		// is a person proving who they are rather than an attacker probing.
		writeError(w, http.StatusUnauthorized, CodeUnauthorized,
			"that is not your current password", "")
		return
	case errors.Is(err, auth.ErrWeakPassword):
		badRequest(w, "invalid body",
			fmt.Sprintf("new_password: must be at least %d characters",
				auth.MinPasswordLength))
		return
	case err != nil:
		badRequest(w, "invalid body", err.Error())
		return
	}
	s.log.Info("auth.password_changed", "user", session.User.Email)
	// Every other login was ended by the change; the one asking was kept, so
	// nobody is thrown out of the page they are standing on.
	writeData(w, map[string]bool{"changed": true}, nil)
}

// handleLogins lists everywhere this person is signed in.
func (s *Server) handleLogins(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}
	logins, err := s.auth.Logins(r.Context(), session.User.ID, s.sessionToken(r))
	if err != nil {
		internalError(w, s.log, "list logins", err)
		return
	}
	writeData(w, logins, &Meta{Total: int64(len(logins))})
}

// handleRevokeLogin ends one of this person's other sessions.
func (s *Server) handleRevokeLogin(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}
	id := r.PathValue("id")
	// Scoped to the signed-in person in the query itself: the id comes out of
	// a URL, and without the scope anybody could end anybody else's session by
	// guessing one.
	if err := s.auth.RevokeLogin(r.Context(), session.User.ID, id); err != nil {
		if errors.Is(err, auth.ErrNoSession) {
			notFound(w, "no such session", "it may already have ended")
			return
		}
		internalError(w, s.log, "revoke login", err)
		return
	}
	// Ending the session doing the asking is a sign-out, cookie and all.
	if id == session.ID {
		http.SetCookie(w, s.sessionCookie(r, "", time.Unix(0, 0)))
	}
	writeData(w, map[string]string{"ended": id}, nil)
}

// handleRevokeOtherLogins ends every session but this one.
func (s *Server) handleRevokeOtherLogins(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}
	ended, err := s.auth.RevokeOthers(r.Context(), session.User.ID, s.sessionToken(r))
	if err != nil {
		internalError(w, s.log, "revoke logins", err)
		return
	}
	s.log.Info("auth.revoked_others", "user", session.User.Email, "ended", ended)
	writeData(w, map[string]int64{"ended": ended}, nil)
}

// Paths that answer without a session even where one is required: the probes
// an orchestrator calls, the capability report the portal reads before it can
// know to log in, and the login routes themselves.
var openPaths = map[string]bool{
	"/healthz":           true,
	"/readyz":            true,
	"/v1/capabilities":   true,
	"/v1/auth/login":     true,
	"/v1/auth/logout":    true,
	"/v1/auth/me":        true,
	"/v1/auth/bootstrap": true,
}

// withAuth refuses requests that carry no session, where the deployment asked
// for that.
//
// Off by default, and that default is the honest one: this API serves a
// research warehouse whose whole point is that figures can be traced, and most
// deployments want them readable. Where it is on, everything but the paths
// above needs a session — the read routes included, because "this data is not
// public" is the only reason to switch it on.
//
// A middleware over the whole mux rather than a wrapper per route: a gate that
// has to be remembered at each of thirty registrations is a gate somebody will
// forget to put on the thirty-first.
func (s *Server) withAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s.auth == nil || openPaths[r.URL.Path] || r.Method == http.MethodOptions {
			next.ServeHTTP(w, r)
			return
		}

		// Two different questions, and only one of them is a setting.
		//
		// Reading is gated by AUTH_REQUIRED, because whether the figures are
		// public is a decision about the deployment. Changing anything is
		// gated as soon as the deployment has accounts at all: on a portal
		// where people sign in, an anonymous request that renames somebody's
		// collection or starts an ingestion is not a feature.
		write := r.Method != http.MethodGet && r.Method != http.MethodHead
		if !s.cfg.Auth.Required && !write {
			next.ServeHTTP(w, r)
			return
		}

		if _, err := s.session(r); err != nil {
			detail := "log in through the portal, or POST /v1/auth/login"
			if write && !s.cfg.Auth.Required {
				detail = "this deployment keeps accounts, so changing anything " +
					"needs a session; reading does not"
			}
			writeError(w, http.StatusUnauthorized, CodeUnauthorized,
				"not signed in", detail)
			return
		}
		next.ServeHTTP(w, r)
	})
}
