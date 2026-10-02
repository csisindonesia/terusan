package httpapi

import (
	"context"
	"errors"
	"net/http"

	"github.com/csis/terusan/services/api/internal/articles"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/showcase"
)

// The Portal's articles written in the portal: blocks of prose and charts,
// whose figure tokens are evaluated when the article is read.
//
// Reading follows the deployment, like every other read, except that a draft
// is shown to administrators only. Writing needs an administrator, by session
// or API token, as the showcase does, and publishing an article files it on
// the Portal as a showcase entry whose datasets are the ones its charts draw.

// ArticleResponse is an article as served: its blocks, and every figure token
// in them evaluated, keyed by the token as written.
type ArticleResponse struct {
	articles.Article
	Figures map[string]ArticleFigure `json:"figures"`
	// The datasets its charts draw, worked out from their series.
	Datasets []string `json:"datasets"`
}

func (s *Server) articlesReady(w http.ResponseWriter) bool {
	if s.articles == nil {
		writeError(w, http.StatusNotFound, CodeNotFound,
			"this serving layer keeps no articles", "set APP_DB where the API runs")
		return false
	}
	return true
}

// isAdmin is whether the request carries an administrator's session or token.
// A reader without one is not refused; they are shown less.
func (s *Server) isAdmin(r *http.Request) bool {
	if s.auth == nil {
		return false
	}
	session, err := s.session(r)
	return err == nil && session.User.Role == auth.RoleAdmin
}

// datasetsFor maps indicators to the datasets holding them, in order.
func (s *Server) datasetsFor(ctx context.Context, indicators []string) []string {
	out := []string{}
	if len(indicators) == 0 || s.warehouse == nil {
		return out
	}
	catalogue, err := s.lakeCatalogue(ctx)
	if err != nil {
		s.log.Warn("articles.catalogue", "error", err)
		return out
	}
	owner := map[string]string{}
	for _, d := range catalogue.datasets {
		for _, id := range d.Indicators {
			owner[id] = d.DatasetID
		}
	}
	seen := map[string]bool{}
	for _, id := range indicators {
		if d, ok := owner[id]; ok && !seen[d] {
			seen[d] = true
			out = append(out, d)
		}
	}
	return out
}

func (s *Server) respondArticle(w http.ResponseWriter, r *http.Request, status int, a articles.Article) {
	body := ArticleResponse{
		Article:  a,
		Figures:  s.articleFigures(r.Context(), a.Texts()),
		Datasets: s.datasetsFor(r.Context(), a.Indicators()),
	}
	writeJSON(w, status, Response[ArticleResponse]{Data: body})
}

func (s *Server) handleArticles(w http.ResponseWriter, r *http.Request) {
	if !s.articlesReady(w) {
		return
	}
	list, err := s.articles.List(r.Context(), s.isAdmin(r))
	if err != nil {
		internalError(w, s.log, "list articles", err)
		return
	}
	writeData(w, list, &Meta{Total: int64(len(list)), Limit: len(list)})
}

func (s *Server) handleArticle(w http.ResponseWriter, r *http.Request) {
	if !s.articlesReady(w) {
		return
	}
	a, err := s.articles.Get(r.Context(), r.PathValue("slug"))
	// A draft is answered as absent, not as forbidden: saying it exists is
	// already saying something about it.
	if errors.Is(err, articles.ErrNotFound) || (err == nil && a.Status != articles.Published && !s.isAdmin(r)) {
		notFound(w, "no such article", "")
		return
	}
	if err != nil {
		internalError(w, s.log, "read article", err)
		return
	}
	s.respondArticle(w, r, http.StatusOK, a)
}

func (s *Server) handleCreateArticle(w http.ResponseWriter, r *http.Request) {
	if !s.articlesReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "write articles")
	if !ok {
		return
	}
	var body articles.Article
	if !decodeBody(w, r, &body, 512<<10) {
		return
	}
	body.CreatedBy, body.CreatedByEmail = session.User.ID, session.User.Email
	created, err := s.articles.Create(r.Context(), body)
	if s.articleError(w, err) {
		return
	}
	if created, err = s.syncShowcase(r.Context(), created); err != nil {
		internalError(w, s.log, "file article on the portal", err)
		return
	}
	s.log.Info("article.created", "slug", created.Slug, "status", created.Status, "by", session.User.Email)
	s.respondArticle(w, r, http.StatusCreated, created)
}

func (s *Server) handleUpdateArticle(w http.ResponseWriter, r *http.Request) {
	if !s.articlesReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "write articles")
	if !ok {
		return
	}
	var body articles.Article
	if !decodeBody(w, r, &body, 512<<10) {
		return
	}
	updated, err := s.articles.Update(r.Context(), r.PathValue("slug"), body)
	if s.articleError(w, err) {
		return
	}
	if updated, err = s.syncShowcase(r.Context(), updated); err != nil {
		internalError(w, s.log, "file article on the portal", err)
		return
	}
	s.log.Info("article.updated", "slug", updated.Slug, "status", updated.Status, "by", session.User.Email)
	s.respondArticle(w, r, http.StatusOK, updated)
}

func (s *Server) handleDeleteArticle(w http.ResponseWriter, r *http.Request) {
	if !s.articlesReady(w) {
		return
	}
	session, ok := s.requireAdminOrToken(w, r, "write articles")
	if !ok {
		return
	}
	a, err := s.articles.Get(r.Context(), r.PathValue("slug"))
	if s.articleError(w, err) {
		return
	}
	if a.ShowcaseID != "" && s.showcase != nil {
		if err := s.showcase.Delete(r.Context(), a.ShowcaseID); err != nil &&
			!errors.Is(err, showcase.ErrNotFound) {
			internalError(w, s.log, "remove article from the portal", err)
			return
		}
	}
	if err := s.articles.Delete(r.Context(), a.Slug); s.articleError(w, err) {
		return
	}
	s.log.Info("article.deleted", "slug", a.Slug, "by", session.User.Email)
	writeData(w, map[string]bool{"deleted": true}, nil)
}

// handleArticleFigures evaluates the tokens in text an editor is writing, so
// the preview shows the numbers the published article will.
func (s *Server) handleArticleFigures(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireAdminOrToken(w, r, "write articles"); !ok {
		return
	}
	var body struct {
		Texts []string `json:"texts"`
	}
	if !decodeBody(w, r, &body, 256<<10) {
		return
	}
	writeData(w, s.articleFigures(r.Context(), body.Texts), nil)
}

// handleArticleMeasures lists what a figure token may ask, for the editor's
// help.
func (s *Server) handleArticleMeasures(w http.ResponseWriter, _ *http.Request) {
	writeData(w, articles.Measures, nil)
}

func (s *Server) articleError(w http.ResponseWriter, err error) bool {
	switch {
	case err == nil:
		return false
	case errors.Is(err, articles.ErrNotFound):
		notFound(w, "no such article", "")
	case errors.Is(err, articles.ErrExists):
		writeError(w, http.StatusConflict, CodeConflict, err.Error(), "choose another slug")
	default:
		badRequest(w, "invalid body", err.Error())
	}
	return true
}

// syncShowcase keeps an article's Portal entry in step with it: filed while it
// is published, with the datasets its charts draw, and withdrawn when it is
// not.
func (s *Server) syncShowcase(ctx context.Context, a articles.Article) (articles.Article, error) {
	if s.showcase == nil {
		return a, nil
	}
	if a.Status != articles.Published {
		if a.ShowcaseID == "" {
			return a, nil
		}
		if err := s.showcase.Delete(ctx, a.ShowcaseID); err != nil && !errors.Is(err, showcase.ErrNotFound) {
			return a, err
		}
		a.ShowcaseID = ""
		return a, s.articles.SetShowcase(ctx, a.ID, "")
	}

	entry := showcase.Entry{
		Title: a.Title, Kind: kindOf(a), URL: "/portal/" + a.Slug,
		Publisher: a.Publisher, PublishedOn: a.PublishedOn, Description: a.Dek,
		Datasets:  s.datasetsFor(ctx, a.Indicators()),
		CreatedBy: a.CreatedBy, CreatedByEmail: a.CreatedByEmail,
	}
	if a.ShowcaseID != "" {
		_, err := s.showcase.Update(ctx, a.ShowcaseID, entry)
		if err == nil {
			return a, nil
		}
		// Removed from the Portal by hand; filed again below.
		if !errors.Is(err, showcase.ErrNotFound) {
			return a, err
		}
	}
	created, err := s.showcase.Create(ctx, entry)
	if err != nil {
		return a, err
	}
	a.ShowcaseID = created.ID
	return a, s.articles.SetShowcase(ctx, a.ID, created.ID)
}

// kindOf is what an article is filed as on the Portal.
func kindOf(a articles.Article) string {
	if a.Layout == articles.LayoutDashboard {
		return "dashboard"
	}
	return "article"
}
