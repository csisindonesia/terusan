package httpapi

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strings"

	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/storage"
	"github.com/csis/terusan/services/api/internal/suggestions"
)

// "Collect this too" — the queue of sources readers have asked for.
//
// Writing needs a session, like every other write here, and for a second
// reason: a suggestion is only worth acting on if somebody can be asked what
// they meant by it, and an anonymous queue is a queue of dead ends.

const (
	maxSuggestionTitle = 200
	maxSuggestionURL   = 2000
	maxSuggestionText  = 4000
)

func (s *Server) suggestionsReady(w http.ResponseWriter) bool {
	if s.suggestions == nil {
		writeError(w, http.StatusNotFound, CodeNotFound,
			"this serving layer keeps no suggestions",
			"set APP_DB where the API runs; until then write to the address on the "+
				"contact page instead")
		return false
	}
	return true
}

func (s *Server) handleSuggestions(w http.ResponseWriter, r *http.Request) {
	if !s.suggestionsReady(w) {
		return
	}
	limit, err := intParam(r, "limit", 50, 1, 200)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	list, err := s.suggestions.List(r.Context(), limit)
	if err != nil {
		internalError(w, s.log, "list suggestions", err)
		return
	}
	writeData(w, list, &Meta{Total: int64(len(list)), Limit: limit})
}

func (s *Server) handleCreateSuggestion(w http.ResponseWriter, r *http.Request) {
	if !s.suggestionsReady(w) {
		return
	}
	// Named, not anonymous: somebody has to be askable about what they meant.
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}

	var body struct {
		Title       string `json:"title"`
		URL         string `json:"url"`
		Cadence     string `json:"cadence"`
		Description string `json:"description"`
	}
	if !decodeBody(w, r, &body, 32<<10) {
		return
	}

	if len(body.Title) > maxSuggestionTitle {
		badRequest(w, "invalid body", "title: is too long")
		return
	}
	if len(body.URL) > maxSuggestionURL {
		badRequest(w, "invalid body", "url: is too long")
		return
	}
	if len(body.Description) > maxSuggestionText {
		badRequest(w, "invalid body", "description: is too long")
		return
	}
	// A URL somebody can open, not a note about where the data might be. The
	// scheme is checked because this address is shown as a link, and a
	// `javascript:` in a list every reader hovers is somebody else's problem
	// to discover.
	if detail := badURL(body.URL); detail != "" {
		badRequest(w, "invalid body", detail)
		return
	}

	created, err := s.suggestions.Create(r.Context(), suggestions.Suggestion{
		Title:            body.Title,
		URL:              body.URL,
		Cadence:          strings.TrimSpace(body.Cadence),
		Description:      body.Description,
		RequestedBy:      session.User.ID,
		RequestedByEmail: session.User.Email,
	})
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	s.log.Info("suggestion.created", "title", created.Title, "by", session.User.Email)
	writeJSON(w, http.StatusCreated, Response[suggestions.Suggestion]{Data: created})
}

// handleUpdateSuggestion is an administrator triaging a suggestion: whether
// the source will be added is theirs to say, not whoever is signed in.
func (s *Server) handleUpdateSuggestion(w http.ResponseWriter, r *http.Request) {
	if !s.suggestionsReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "triage suggestions")
	if !ok {
		return
	}
	var body struct {
		Status    string `json:"status"`
		Reason    string `json:"reason"`
		DatasetID string `json:"dataset_id"`
		Note      string `json:"note"`
	}
	if !decodeBody(w, r, &body, 8<<10) {
		return
	}
	if len(body.Note) > maxSuggestionText {
		badRequest(w, "invalid body", "note: is too long")
		return
	}
	// The dataset is shown to the asker as a link to its page, so it has to
	// be one: a typo here would answer "we already have it" with a 404.
	if dataset := strings.TrimSpace(body.DatasetID); dataset != "" {
		if !identifierPattern.MatchString(dataset) {
			badRequest(w, "invalid body", "dataset_id: is not a well-formed identifier")
			return
		}
		known, err := s.knowsDataset(r.Context(), dataset)
		if err != nil {
			internalError(w, s.log, "look up dataset", err)
			return
		}
		if !known {
			badRequest(w, "invalid body", "dataset_id: the catalogue holds no "+dataset)
			return
		}
	}

	updated, err := s.suggestions.Decide(r.Context(), r.PathValue("id"), suggestions.Decision{
		Status:          strings.TrimSpace(body.Status),
		Reason:          body.Reason,
		DatasetID:       body.DatasetID,
		Note:            body.Note,
		ReviewedByEmail: session.User.Email,
	})
	if errors.Is(err, suggestions.ErrNotFound) {
		notFound(w, "no such suggestion", "")
		return
	}
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	s.log.Info("suggestion.updated",
		"id", updated.ID, "status", updated.Status, "reason", updated.Reason,
		"by", session.User.Email)
	writeData(w, updated, nil)
}

// knowsDataset reports whether the catalogue lists this dataset.
//
// A deployment serving no lake has no catalogue to check against, and then the
// identifier is taken as given: it only ever becomes a link, never an argument
// to anything.
func (s *Server) knowsDataset(ctx context.Context, id string) (bool, error) {
	if s.warehouse == nil {
		return true, nil
	}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return false, nil
	}
	catalogue, err := s.lakeCatalogue(ctx)
	if err != nil {
		return false, err
	}
	for _, d := range catalogue.datasets {
		if d.DatasetID == id {
			return true, nil
		}
	}
	return false, nil
}

// handleDeleteSuggestion withdraws a suggestion: the person who made it, or
// an administrator. Anybody else gets the same answer as for one that does
// not exist.
func (s *Server) handleDeleteSuggestion(w http.ResponseWriter, r *http.Request) {
	if !s.suggestionsReady(w) {
		return
	}
	session, ok := s.requireSession(w, r)
	if !ok {
		return
	}
	id := r.PathValue("id")
	found, err := s.suggestions.Get(r.Context(), id)
	if errors.Is(err, suggestions.ErrNotFound) ||
		(err == nil && session.User.Role != auth.RoleAdmin && found.RequestedBy != session.User.ID) {
		notFound(w, "no such suggestion", "")
		return
	}
	if err != nil {
		internalError(w, s.log, "read suggestion", err)
		return
	}
	if err := s.suggestions.Delete(r.Context(), id); err != nil {
		if errors.Is(err, suggestions.ErrNotFound) {
			notFound(w, "no such suggestion", "")
			return
		}
		internalError(w, s.log, "delete suggestion", err)
		return
	}
	writeData(w, map[string]bool{"deleted": true}, nil)
}

// badURL returns why an address is unusable, or "" when it is fine.
func badURL(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "url: where the data is published"
	}
	parsed, err := url.Parse(value)
	if err != nil {
		return "url: is not a URL"
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return "url: must start with http:// or https://"
	}
	if parsed.Host == "" {
		return "url: names no host"
	}
	return ""
}
