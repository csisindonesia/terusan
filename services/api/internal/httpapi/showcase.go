package httpapi

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"strings"

	"github.com/csis/terusan/services/api/internal/showcase"
	"github.com/csis/terusan/services/api/internal/storage"
)

// The work built on this warehouse's data: dashboards, articles and reports,
// listed on the portal's Discover → Portal page and under each dataset they
// use.
//
// Reading follows the deployment, like every other read. Writing needs an
// administrator, not merely a session: an entry is the warehouse vouching that
// a piece of work stands on its data. The administrator's API token will do,
// so the list can be curated from a script; account management stays
// browser-only (see requireBrowserSession).

const (
	maxShowcaseTitle = 300
	maxShowcaseText  = 4000
	maxShowcaseName  = 200
)

// ShowcaseDataset is one dataset an entry uses, with the title the catalogue
// gives it. `title` is absent for a dataset the lake no longer holds, which
// the portal shows as such rather than dropping the link.
type ShowcaseDataset struct {
	DatasetID string  `json:"dataset_id"`
	Title     *string `json:"title,omitempty"`
}

// ShowcaseEntry is an entry as served: the stored fields, with its datasets
// named.
type ShowcaseEntry struct {
	ID             string            `json:"id"`
	Title          string            `json:"title"`
	Kind           string            `json:"kind"`
	URL            string            `json:"url"`
	Publisher      string            `json:"publisher,omitempty"`
	PublishedOn    string            `json:"published_on,omitempty"`
	Description    string            `json:"description,omitempty"`
	Datasets       []ShowcaseDataset `json:"datasets"`
	CreatedByEmail string            `json:"created_by_email"`
	CreatedAt      string            `json:"created_at"`
	UpdatedAt      string            `json:"updated_at"`
}

func (s *Server) showcaseReady(w http.ResponseWriter) bool {
	if s.showcase == nil {
		writeError(w, http.StatusNotFound, CodeNotFound,
			"this serving layer keeps no showcase",
			"set APP_DB where the API runs")
		return false
	}
	return true
}

// datasetTitles maps every dataset the lake holds to its title, or nil when
// the lake has no catalogue to ask. A nil map means "cannot tell", which is
// different from an empty one.
func (s *Server) datasetTitles(ctx context.Context) map[string]*string {
	if s.warehouse == nil || !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return nil
	}
	catalogue, err := s.lakeCatalogue(ctx)
	if err != nil {
		s.log.Warn("showcase.catalogue", "error", err)
		return nil
	}
	titles := make(map[string]*string, len(catalogue.datasets))
	for _, d := range catalogue.datasets {
		titles[d.DatasetID] = d.Title
	}
	return titles
}

func served(entry showcase.Entry, titles map[string]*string) ShowcaseEntry {
	datasets := make([]ShowcaseDataset, len(entry.Datasets))
	for n, id := range entry.Datasets {
		datasets[n] = ShowcaseDataset{DatasetID: id, Title: titles[id]}
	}
	return ShowcaseEntry{
		ID: entry.ID, Title: entry.Title, Kind: entry.Kind, URL: entry.URL,
		Publisher: entry.Publisher, PublishedOn: entry.PublishedOn,
		Description: entry.Description, Datasets: datasets,
		CreatedByEmail: entry.CreatedByEmail,
		CreatedAt:      entry.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
		UpdatedAt:      entry.UpdatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	}
}

func (s *Server) handleShowcase(w http.ResponseWriter, r *http.Request) {
	if !s.showcaseReady(w) {
		return
	}
	kind, err := stringParam(r, "kind", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	dataset, err := stringParam(r, "dataset", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	list, err := s.showcase.List(r.Context(), showcase.Filter{Kind: kind, DatasetID: dataset})
	if err != nil {
		internalError(w, s.log, "list showcase", err)
		return
	}
	titles := s.datasetTitles(r.Context())
	out := make([]ShowcaseEntry, len(list))
	for n, entry := range list {
		out[n] = served(entry, titles)
	}
	writeData(w, out, &Meta{Total: int64(len(out)), Limit: len(out)})
}

func (s *Server) handleShowcaseEntry(w http.ResponseWriter, r *http.Request) {
	if !s.showcaseReady(w) {
		return
	}
	entry, err := s.showcase.Get(r.Context(), r.PathValue("id"))
	if errors.Is(err, showcase.ErrNotFound) {
		notFound(w, "no such entry", "")
		return
	}
	if err != nil {
		internalError(w, s.log, "read showcase entry", err)
		return
	}
	writeData(w, served(entry, s.datasetTitles(r.Context())), nil)
}

type showcaseBody struct {
	Title       string   `json:"title"`
	Kind        string   `json:"kind"`
	URL         string   `json:"url"`
	Publisher   string   `json:"publisher"`
	PublishedOn string   `json:"published_on"`
	Description string   `json:"description"`
	Datasets    []string `json:"datasets"`
}

// check says what is wrong with a body, or "" when it can be stored.
//
// `kept` are datasets the entry already names. They pass even when the lake
// no longer holds them, so an entry whose dataset was retired can still be
// edited without first being made to forget it.
func (s *Server) checkShowcase(ctx context.Context, body showcaseBody, kept []string) string {
	switch {
	case len(body.Title) > maxShowcaseTitle:
		return "title: is too long"
	case len(body.Publisher) > maxShowcaseName:
		return "publisher: is too long"
	case len(body.Description) > maxShowcaseText:
		return "description: is too long"
	case len(body.Datasets) > showcase.MaxDatasets:
		return fmt.Sprintf("datasets: at most %d", showcase.MaxDatasets)
	}
	// Shown as a link on a page every reader visits, so the scheme matters.
	if detail := showcaseURL(body.URL); detail != "" {
		return detail
	}
	titles := s.datasetTitles(ctx)
	for _, id := range body.Datasets {
		id = strings.TrimSpace(id)
		if id == "" {
			continue
		}
		if !identifierPattern.MatchString(id) {
			return fmt.Sprintf("datasets: %q is not a well-formed identifier", id)
		}
		// A lake with no catalogue cannot say, and is not a reason to refuse.
		if _, known := titles[id]; titles != nil && !known && !containsString(kept, id) {
			return fmt.Sprintf("datasets: %q is not a dataset this warehouse holds", id)
		}
	}
	return ""
}

// showcaseURL is badURL, plus the Portal's own articles: a path under
// /portal/ on this deployment, which the portal opens in place. Only that
// prefix, and never a `//host` a browser would read as another site.
func showcaseURL(value string) string {
	value = strings.TrimSpace(value)
	if strings.HasPrefix(value, "/") {
		if !internalArticle.MatchString(value) {
			return "url: a path here must be /portal/<article>"
		}
		return ""
	}
	return badURL(value)
}

var internalArticle = regexp.MustCompile(`^/portal/[a-z0-9]+(?:-[a-z0-9]+)*$`)

func (body showcaseBody) entry() showcase.Entry {
	return showcase.Entry{
		Title: body.Title, Kind: body.Kind, URL: body.URL, Publisher: body.Publisher,
		PublishedOn: body.PublishedOn, Description: body.Description, Datasets: body.Datasets,
	}
}

func (s *Server) handleCreateShowcase(w http.ResponseWriter, r *http.Request) {
	if !s.showcaseReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "change the showcase")
	if !ok {
		return
	}
	var body showcaseBody
	if !decodeBody(w, r, &body, 64<<10) {
		return
	}
	if detail := s.checkShowcase(r.Context(), body, nil); detail != "" {
		badRequest(w, "invalid body", detail)
		return
	}
	input := body.entry()
	input.CreatedBy, input.CreatedByEmail = session.User.ID, session.User.Email
	created, err := s.showcase.Create(r.Context(), input)
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	s.log.Info("showcase.created", "id", created.ID, "title", created.Title, "by", session.User.Email)
	writeJSON(w, http.StatusCreated,
		Response[ShowcaseEntry]{Data: served(created, s.datasetTitles(r.Context()))})
}

func (s *Server) handleUpdateShowcase(w http.ResponseWriter, r *http.Request) {
	if !s.showcaseReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "change the showcase")
	if !ok {
		return
	}
	id := r.PathValue("id")
	current, err := s.showcase.Get(r.Context(), id)
	if errors.Is(err, showcase.ErrNotFound) {
		notFound(w, "no such entry", "")
		return
	}
	if err != nil {
		internalError(w, s.log, "read showcase entry", err)
		return
	}
	var body showcaseBody
	if !decodeBody(w, r, &body, 64<<10) {
		return
	}
	if detail := s.checkShowcase(r.Context(), body, current.Datasets); detail != "" {
		badRequest(w, "invalid body", detail)
		return
	}
	updated, err := s.showcase.Update(r.Context(), id, body.entry())
	if errors.Is(err, showcase.ErrNotFound) {
		notFound(w, "no such entry", "")
		return
	}
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	s.log.Info("showcase.updated", "id", id, "by", session.User.Email)
	writeData(w, served(updated, s.datasetTitles(r.Context())), nil)
}

func (s *Server) handleDeleteShowcase(w http.ResponseWriter, r *http.Request) {
	if !s.showcaseReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "change the showcase")
	if !ok {
		return
	}
	if err := s.showcase.Delete(r.Context(), r.PathValue("id")); err != nil {
		if errors.Is(err, showcase.ErrNotFound) {
			notFound(w, "no such entry", "")
			return
		}
		internalError(w, s.log, "delete showcase entry", err)
		return
	}
	s.log.Info("showcase.deleted", "id", r.PathValue("id"), "by", session.User.Email)
	writeData(w, map[string]bool{"deleted": true}, nil)
}
