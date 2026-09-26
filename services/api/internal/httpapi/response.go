package httpapi

import (
	"encoding/json"
	"log/slog"
	"net/http"
)

// The response envelope (program.md §53). Every endpoint answers in this shape,
// including errors, so a consumer writes one parser rather than one per route.

// Response wraps a successful payload.
type Response[T any] struct {
	Data T         `json:"data"`
	Meta *Meta     `json:"meta,omitempty"`
	Err  *APIError `json:"error,omitempty"`
}

// Meta carries pagination and provenance for a result set.
type Meta struct {
	Total   int64 `json:"total,omitempty"`
	Limit   int   `json:"limit,omitempty"`
	Offset  int   `json:"offset,omitempty"`
	HasMore bool  `json:"has_more"`
	// Where the next page starts, for a caller walking a series rather than
	// jumping about in it. Opaque on purpose: it is this server's bookmark and
	// not a filter to compose (see cursor.go).
	NextCursor string `json:"next_cursor,omitempty"`
	// Where the numbers came from, so a figure lifted out of an API response
	// can still be traced (program.md §2.2).
	Source string `json:"source,omitempty"`
	Layer  string `json:"layer,omitempty"`
}

// APIError is the error half of the envelope.
type APIError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	// Detail names the offending parameter where one is to blame, so a caller
	// can fix the request without guessing which of six it got wrong.
	Detail string `json:"detail,omitempty"`
}

const (
	CodeBadRequest  = "bad_request"
	CodeNotFound    = "not_found"
	CodeInternal    = "internal_error"
	CodeUnavailable = "unavailable"
	// The deployment will not do this, whoever is asking. Not an authorization
	// failure — there is nothing to authenticate as — but a capability this
	// serving layer was started without.
	CodeForbidden = "forbidden"
	// The request is fine and the moment is wrong: the same source is already
	// being ingested.
	CodeConflict = "conflict"
	// No session, or one that has expired. Distinct from forbidden: that is
	// "this deployment will not do this at all", this is "not as nobody".
	CodeUnauthorized = "unauthorized"
	// Slow down — today only a login that has been guessed at too often.
	CodeTooManyRequests = "too_many_requests"
)

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		slog.Default().Error("response.encode_failed", "error", err)
	}
}

func writeData[T any](w http.ResponseWriter, data T, meta *Meta) {
	writeJSON(w, http.StatusOK, Response[T]{Data: data, Meta: meta})
}

func writeError(w http.ResponseWriter, status int, code, message, detail string) {
	writeJSON(w, status, Response[any]{
		Err: &APIError{Code: code, Message: message, Detail: detail},
	})
}

func badRequest(w http.ResponseWriter, message, detail string) {
	writeError(w, http.StatusBadRequest, CodeBadRequest, message, detail)
}

func notFound(w http.ResponseWriter, message, detail string) {
	writeError(w, http.StatusNotFound, CodeNotFound, message, detail)
}

// internalError logs the cause and returns a generic message.
//
// The detail stays in the log: a query error can carry a storage path or a
// credential fragment, and neither belongs in a response body.
func internalError(w http.ResponseWriter, log *slog.Logger, context string, err error) {
	log.Error("request.failed", "context", context, "error", err)
	writeError(w, http.StatusInternalServerError, CodeInternal,
		"the request could not be completed", "")
}
