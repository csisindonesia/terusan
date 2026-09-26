package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/csis/terusan/services/api/internal/auth"
)

// The Users page, and the form a stranger fills in to ask for an account.
//
// Everything under /v1/admin/users needs an admin, on a cookie session: an API
// token cannot manage accounts any more than it can manage its own.

// requireAdmin answers the request itself unless an admin is asking.
func (s *Server) requireAdmin(w http.ResponseWriter, r *http.Request) (auth.Session, bool) {
	if !s.authReady(w) {
		return auth.Session{}, false
	}
	session, ok := s.requireBrowserSession(w, r)
	if !ok {
		return auth.Session{}, false
	}
	if session.User.Role != auth.RoleAdmin {
		writeError(w, http.StatusForbidden, CodeForbidden,
			"only an administrator can manage accounts", "")
		return auth.Session{}, false
	}
	return session, true
}

// accountError maps what the accounts service refuses to an HTTP answer.
func (s *Server) accountError(w http.ResponseWriter, what string, err error) {
	switch {
	case errors.Is(err, auth.ErrNotFound):
		notFound(w, "no such account", "")
	case errors.Is(err, auth.ErrExists):
		writeError(w, http.StatusConflict, CodeConflict, err.Error(), "")
	case errors.Is(err, auth.ErrNotPending):
		writeError(w, http.StatusConflict, CodeConflict, err.Error(), "")
	case errors.Is(err, auth.ErrWeakPassword):
		badRequest(w, "invalid body",
			fmt.Sprintf("password: must be at least %d characters", auth.MinPasswordLength))
	case errors.Is(err, auth.ErrInvalid):
		badRequest(w, "invalid body", err.Error())
	default:
		internalError(w, s.log, what, err)
	}
}

// nullableT is a timestamp field where absent and null mean different things:
// absent leaves the stored value alone, null clears it.
type nullableT struct {
	Set   bool
	Value *time.Time
}

func (n *nullableT) UnmarshalJSON(raw []byte) error {
	n.Set = true
	if string(raw) == "null" {
		n.Value = nil
		return nil
	}
	var at time.Time
	if err := json.Unmarshal(raw, &at); err != nil {
		return err
	}
	n.Value = &at
	return nil
}

// ---- registration ---------------------------------------------------------

// How many requests one address may send in an hour. The queue has its own
// cap (auth.maxPending); this keeps one visitor from filling it alone.
const registrationsPerHour = 5

type registrationLimiter struct {
	mu   sync.Mutex
	seen map[string][]time.Time
}

func (l *registrationLimiter) allow(ip string, now time.Time) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.seen == nil || len(l.seen) > 10_000 {
		l.seen = map[string][]time.Time{}
	}
	recent := l.seen[ip][:0]
	for _, at := range l.seen[ip] {
		if now.Sub(at) < time.Hour {
			recent = append(recent, at)
		}
	}
	if len(recent) >= registrationsPerHour {
		l.seen[ip] = recent
		return false
	}
	l.seen[ip] = append(recent, now)
	return true
}

// handleRegister records a request for an account. The answer is the same
// whether or not the address already had one (see auth.Register).
func (s *Server) handleRegister(w http.ResponseWriter, r *http.Request) {
	if !s.authReady(w) {
		return
	}
	if !s.cfg.Auth.Registration {
		writeError(w, http.StatusForbidden, CodeForbidden,
			"this deployment does not take registrations",
			"ask whoever administers it for an account")
		return
	}
	if !s.registrations.allow(s.client(r).IP, time.Now()) {
		writeError(w, http.StatusTooManyRequests, CodeTooManyRequests,
			"too many requests from this address", "try again in an hour")
		return
	}
	var body struct {
		Email      string `json:"email"`
		Password   string `json:"password"`
		Name       string `json:"name"`
		Department string `json:"department"`
	}
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}
	err := s.auth.Register(r.Context(), body.Email, body.Password, body.Name, body.Department)
	if errors.Is(err, auth.ErrTooManyPending) {
		writeError(w, http.StatusServiceUnavailable, CodeUnavailable, err.Error(),
			"try again once an administrator has worked through the queue")
		return
	}
	if err != nil {
		s.accountError(w, "register", err)
		return
	}
	s.log.Info("auth.registration", "email", auth.NormalizeEmail(body.Email))
	writeJSON(w, http.StatusAccepted, Response[map[string]bool]{
		Data: map[string]bool{"requested": true},
	})
}

// ---- the admin's routes ---------------------------------------------------

func (s *Server) handleUsers(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireAdmin(w, r); !ok {
		return
	}
	users, err := s.auth.Users(r.Context())
	if err != nil {
		internalError(w, s.log, "list users", err)
		return
	}
	writeData(w, users, &Meta{Total: int64(len(users))})
}

// userDetail is one account and what it has been doing.
type userDetail struct {
	User   auth.User          `json:"user"`
	Access []auth.AccessEvent `json:"access"`
}

func (s *Server) handleUser(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireAdmin(w, r); !ok {
		return
	}
	user, err := s.auth.UserByID(r.Context(), r.PathValue("id"))
	if err != nil {
		s.accountError(w, "get user", err)
		return
	}
	access, err := s.auth.AccessLog(r.Context(), user.ID, 200)
	if err != nil {
		internalError(w, s.log, "access log", err)
		return
	}
	writeData(w, userDetail{User: user, Access: access}, nil)
}

// createdUser carries the generated password, when there was one. This is
// the only response it appears in.
type createdUser struct {
	auth.User
	Password string `json:"password,omitempty"`
}

func (s *Server) handleCreateUser(w http.ResponseWriter, r *http.Request) {
	admin, ok := s.requireAdmin(w, r)
	if !ok {
		return
	}
	var body struct {
		Email           string     `json:"email"`
		Name            string     `json:"name"`
		Department      string     `json:"department"`
		Role            string     `json:"role"`
		Password        string     `json:"password"`
		AccessExpiresAt *time.Time `json:"access_expires_at"`
	}
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}
	user, password, err := s.auth.CreateUser(r.Context(), auth.NewUser{
		Email: body.Email, Name: body.Name, Department: body.Department,
		Role: body.Role, Password: body.Password, AccessExpiresAt: body.AccessExpiresAt,
	})
	if err != nil {
		s.accountError(w, "create user", err)
		return
	}
	s.log.Info("auth.user_created", "by", admin.User.Email, "user", user.Email, "role", user.Role)
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusCreated, Response[createdUser]{
		Data: createdUser{User: user, Password: password},
	})
}

func (s *Server) handleUpdateUser(w http.ResponseWriter, r *http.Request) {
	admin, ok := s.requireAdmin(w, r)
	if !ok {
		return
	}
	id := r.PathValue("id")
	// Pointers, so a field left out is a field left alone. The expiry is a
	// raw message because "absent" and "null" mean different things for it:
	// absent keeps the date, null clears it.
	var body struct {
		Name            *string   `json:"name"`
		Department      *string   `json:"department"`
		Role            *string   `json:"role"`
		Disabled        *bool     `json:"disabled"`
		AccessExpiresAt nullableT `json:"access_expires_at"`
	}
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}
	// An admin cannot demote or lock out themselves: the last admin doing so
	// would leave a deployment nobody can manage from the portal.
	if id == admin.User.ID {
		if body.Role != nil && *body.Role != auth.RoleAdmin {
			badRequest(w, "invalid body", "role: you cannot change your own role")
			return
		}
		if body.Disabled != nil && *body.Disabled {
			badRequest(w, "invalid body", "disabled: you cannot disable your own account")
			return
		}
	}
	user, err := s.auth.UpdateUser(r.Context(), id, auth.UserPatch{
		Name: body.Name, Department: body.Department, Role: body.Role,
		Disabled: body.Disabled, SetExpiry: body.AccessExpiresAt.Set,
		AccessExpiresAt: body.AccessExpiresAt.Value,
	})
	if err != nil {
		s.accountError(w, "update user", err)
		return
	}
	s.log.Info("auth.user_updated", "by", admin.User.Email, "user", user.Email)
	writeData(w, user, nil)
}

func (s *Server) handleApproveUser(w http.ResponseWriter, r *http.Request) {
	admin, ok := s.requireAdmin(w, r)
	if !ok {
		return
	}
	var body struct {
		Role            string     `json:"role"`
		AccessExpiresAt *time.Time `json:"access_expires_at"`
	}
	if !decodeBody(w, r, &body, 4<<10) {
		return
	}
	user, err := s.auth.Approve(r.Context(), r.PathValue("id"), body.Role, body.AccessExpiresAt)
	if err != nil {
		s.accountError(w, "approve user", err)
		return
	}
	s.log.Info("auth.user_approved", "by", admin.User.Email, "user", user.Email, "role", user.Role)
	writeData(w, user, nil)
}

func (s *Server) handleRejectUser(w http.ResponseWriter, r *http.Request) {
	admin, ok := s.requireAdmin(w, r)
	if !ok {
		return
	}
	id := r.PathValue("id")
	if err := s.auth.Reject(r.Context(), id); err != nil {
		s.accountError(w, "reject user", err)
		return
	}
	s.log.Info("auth.user_rejected", "by", admin.User.Email, "user", id)
	writeData(w, map[string]string{"rejected": id}, nil)
}

func (s *Server) handleResetUserPassword(w http.ResponseWriter, r *http.Request) {
	admin, ok := s.requireAdmin(w, r)
	if !ok {
		return
	}
	id := r.PathValue("id")
	password, err := s.auth.ResetPassword(r.Context(), id)
	if err != nil {
		s.accountError(w, "reset password", err)
		return
	}
	s.log.Info("auth.password_reset", "by", admin.User.Email, "user", id)
	w.Header().Set("Cache-Control", "no-store")
	writeData(w, map[string]string{"password": password}, nil)
}
