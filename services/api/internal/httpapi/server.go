// Package httpapi wires the serving layer's HTTP surface.
package httpapi

import (
	"context"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/collections"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/conversations"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/runner"
	"github.com/csis/terusan/services/api/internal/storage"
	"github.com/csis/terusan/services/api/internal/suggestions"
)

// Server holds the dependencies every handler needs.
type Server struct {
	cfg       *config.Config
	storage   *storage.Resolver
	warehouse *query.Warehouse
	cache     cache.Cache
	runner    *runner.Runner
	// The two things this service stores rather than derives. Both nil where
	// the deployment has no application database, and their routes say so.
	shelf       *collections.Store
	auth        *auth.Service
	suggestions *suggestions.Store
	// The chat's catalogue and rate limit (see assistant.go), and where its
	// conversations are kept — nil without an application database, and the
	// portal then keeps them in the browser.
	assistant assistantState
	chats     *conversations.Store
	// Per-address limit on the "request access" form (see users.go).
	registrations registrationLimiter
	// The lake's summaries, held and refreshed in the process (summaries.go),
	// and the routes that serve them, for the warm-up at start.
	summaries summaryStore
	catalogue catalogueStore
	// Whether the rollup (rollup.go) is built and the summaries read it.
	rollupReady   atomic.Bool
	summaryRoutes map[string]http.HandlerFunc
	log           *slog.Logger
}

// New returns a Server over cfg.
//
// `c` may be nil, and is on any deployment without a Redis: the server then
// uses a cache that misses everything and serves each request from Parquet.
// `shelf` and `accounts` may be nil, and are wherever APP_DB is unset: the
// portal then keeps collections in the browser, which works and cannot be
// shared, and shows no login because there is nothing to log in to.
func New(
	cfg *config.Config, warehouse *query.Warehouse, c cache.Cache,
	shelf *collections.Store, accounts *auth.Service, asked *suggestions.Store,
	chats *conversations.Store, log *slog.Logger,
) *Server {
	if c == nil {
		c = cache.Nothing{}
	}
	s := &Server{
		cfg:         cfg,
		storage:     storage.NewResolver(cfg.Storage),
		warehouse:   warehouse,
		cache:       c,
		shelf:       shelf,
		auth:        accounts,
		suggestions: asked,
		chats:       chats,
		log:         log,
	}
	// Disabled unless the deployment asked for it, and then the only thing it
	// can do is start `uv run terusan …` against a source the registry names
	// (see internal/runner).
	s.runner = runner.New(runner.Config{
		Enabled:        cfg.Pipelines.Enabled,
		Root:           cfg.Pipelines.Root,
		UV:             cfg.Pipelines.UV,
		Timeout:        cfg.Pipelines.Timeout,
		MaxConcurrent:  cfg.Pipelines.MaxConcurrent,
		MaxOutputLines: cfg.Pipelines.MaxOutputLines,
		History:        cfg.Pipelines.History,
	}, log, s.invalidate)
	return s
}

// invalidate drops the response cache after a pipeline run.
//
// Every cached answer was derived before that run touched the lake, and a
// reader who has just watched an ingestion finish should not then be served a
// minute of answers from before it.
func (s *Server) invalidate() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := s.cache.Purge(ctx); err != nil {
		s.log.Warn("cache.purge_failed", "error", err)
	}
	s.staleSummaries()
	s.staleCatalogue()
}

// Routes returns the HTTP handler for the whole API.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", s.handleHealth)
	mux.HandleFunc("GET /readyz", s.handleReady)

	// Every route that reads the lake is cached: each is a pure function of
	// the lake's contents and the query string, and the lake changes when a
	// pipeline runs rather than between two requests. The document file is
	// the exception — it streams from disk with range support, and holding
	// that in Redis would trade a fast local read for a slow network one.
	mux.HandleFunc("GET /v1/observations", s.withCache(s.handleObservations))
	// Before the list route in spirit though not in matching: what the filters
	// on a series can offer, counted in the warehouse rather than off whatever
	// page of rows a browser happens to hold.
	mux.HandleFunc("GET /v1/observations/facets", s.withCache(s.handleObservationFacets))
	// The same figures as a chart needs them: a line per member, bucketed to a
	// granularity the span can be drawn at rather than sent row by row.
	mux.HandleFunc("GET /v1/observations/series", s.withCache(s.handleObservationSeries))
	// The four that read every observation are also held in the process
	// and refreshed behind the reader: see summaries.go.
	mux.HandleFunc("GET /v1/indicators", s.summary("/v1/indicators", s.withCache(s.handleIndicators)))
	// What the list can be filtered by, over every series.
	mux.HandleFunc("GET /v1/indicators/facets", s.handleIndicatorFacets)
	mux.HandleFunc("GET /v1/indicators/{id}", s.withCache(s.handleIndicator))
	// What the series was read out of, which is the question a reader looking
	// at a figure actually has.
	mux.HandleFunc("GET /v1/indicators/{id}/documents", s.withCache(s.handleIndicatorDocuments))
	// Whether the pipelines behind the series are still running, and what
	// happened last time they did (program.md §39). Read from the lake's
	// journal, so it answers with no catalog database up.
	mux.HandleFunc("GET /v1/indicators/{id}/runs", s.withCache(s.handleIndicatorRuns))
	mux.HandleFunc("GET /v1/geography", s.withCache(s.handleGeography))
	// The other dimension a figure can vary by. Derived from the observations
	// rather than the registry, which knows almost none of them yet.
	mux.HandleFunc("GET /v1/commodities", s.summary("/v1/commodities", s.withCache(s.handleCommodities)))
	mux.HandleFunc("GET /v1/datasets", s.summary("/v1/datasets", s.withCache(s.handleDatasets)))
	mux.HandleFunc("GET /v1/datasets/{id}", s.withCache(s.handleDataset))
	// The home page's headline figures, from the shared catalogue.
	mux.HandleFunc("GET /v1/stats", s.handleStats)
	mux.HandleFunc("GET /v1/storage", s.summary("/v1/storage", s.withCache(s.handleStorage)))
	mux.HandleFunc("GET /v1/sources", s.withCache(s.handleSources))
	mux.HandleFunc("GET /v1/runs", s.withCache(s.handleRuns))
	mux.HandleFunc("GET /v1/documents", s.withCache(s.handleDocuments))
	// Before the {id} route, which would otherwise swallow it.
	mux.HandleFunc("GET /v1/documents/facets", s.withCache(s.handleDocumentFacets))
	mux.HandleFunc("GET /v1/documents/{id}", s.withCache(s.handleDocument))
	mux.HandleFunc("GET /v1/documents/{id}/indicators", s.withCache(s.handleDocumentIndicators))
	// The preserved copy, which is the point of keeping originals at all
	// (program.md §2.1).
	mux.HandleFunc("GET /v1/documents/{id}/file", s.handleDocumentFile)
	// The same bytes under a trailing filename, which is cosmetic and worth
	// it: a browser titles its PDF viewer from the last path segment, so
	// without this a reader sits in front of a document called "file". The
	// segment is ignored — the response is built from the catalogue, never
	// from what the URL claims.
	mux.HandleFunc("GET /v1/documents/{id}/file/{name}", s.handleDocumentFile)
	mux.HandleFunc("GET /v1/regulations", s.withCache(s.handleRegulations))
	// Before the {key} route, which would otherwise swallow it.
	mux.HandleFunc("GET /v1/regulations/facets", s.withCache(s.handleRegulationFacets))
	mux.HandleFunc("GET /v1/regulations/{key}", s.withCache(s.handleRegulation))
	mux.HandleFunc("GET /v1/regulations/{key}/sections", s.withCache(s.handleRegulationSections))
	mux.HandleFunc("GET /v1/regulations/{key}/citations", s.withCache(s.handleRegulationCitations))

	// What this deployment will do beyond answering questions, so the portal
	// can show a button that works rather than one that explains itself.
	// News monitoring. Its own routes rather than folded into documents: an
	// article is not a ministry PDF, and a few thousand news links dropped into
	// that list would bury what it exists to serve.
	mux.HandleFunc("GET /v1/news/outlets", s.withCache(s.handleNewsOutlets))
	mux.HandleFunc("GET /v1/news/outlets/{host}", s.withCache(s.handleNewsOutlet))
	// The outlet page's own list, which is the same query narrowed to one host.
	mux.HandleFunc("GET /v1/news/outlets/{host}/articles", s.withCache(s.handleNewsArticles))
	// The crawl's own log for one paper, a row per day: what discovery turned
	// up, what was read, what matched and what was kept. The only place the
	// articles that were read and thrown away are counted — they are never
	// stored, so nothing else can count them.
	mux.HandleFunc("GET /v1/news/outlets/{host}/tallies", s.withCache(s.handleNewsOutletTallies))
	mux.HandleFunc("GET /v1/news/articles", s.withCache(s.handleNewsArticles))
	// What the corpus can be narrowed by, counted under the filters already
	// on. More specific than the article route below it, so the mux prefers it.
	mux.HandleFunc("GET /v1/news/articles/facets", s.withCache(s.handleNewsArticleFacets))
	mux.HandleFunc("GET /v1/news/articles/{id}", s.withCache(s.handleNewsArticle))
	// The page as it stood the day it was collected. Not cached through the
	// response cache: it is a file, served with range support.
	mux.HandleFunc("GET /v1/news/articles/{id}/screenshot", s.handleNewsScreenshot)
	mux.HandleFunc("GET /v1/news/events", s.withCache(s.handleNewsEvents))

	mux.HandleFunc("GET /v1/capabilities", s.handleCapabilities)

	// The one write surface, and it writes nothing itself: it asks a pipeline
	// to run, and the pipeline lands what it finds the way every figure here
	// arrives (see jobs.go). Off unless PIPELINES_ENABLED is set, in which
	// case these three answer; otherwise the POST is a 403 and the list is
	// empty. Uncached by nature — a job changes while it is being watched.
	mux.HandleFunc("POST /v1/sources/{id}/run", s.handleRunSource)
	mux.HandleFunc("GET /v1/jobs", s.handleJobs)
	mux.HandleFunc("GET /v1/jobs/{id}", s.handleJob)

	// The shelf. Uncached, unlike everything above: these answer from a
	// database this process writes, so a cached copy would be a copy of what
	// the reader changed a second ago. Off entirely without COLLECTIONS_DB,
	// and read-only without COLLECTIONS_WRITE (see collections.go).
	mux.HandleFunc("GET /v1/collections", s.handleCollections)
	mux.HandleFunc("POST /v1/collections", s.handleCreateCollection)
	// Before the {id} route, which would otherwise swallow it.
	mux.HandleFunc("POST /v1/collections/import", s.handleImportShelf)
	mux.HandleFunc("GET /v1/collections/{id}", s.handleCollection)
	mux.HandleFunc("PATCH /v1/collections/{id}", s.handleUpdateCollection)
	mux.HandleFunc("DELETE /v1/collections/{id}", s.handleDeleteCollection)
	mux.HandleFunc("POST /v1/collections/{id}/items", s.handleAddItems)
	mux.HandleFunc("DELETE /v1/collections/{id}/items/{kind}/{ref}", s.handleRemoveItem)

	// Who is asking. Identity, not authority: every route answers the same way
	// to everyone who gets past AUTH_REQUIRED, which is on unless the
	// deployment turned it off (see auth.go).
	// The first account on a deployment that has none, from the machine the
	// API runs on. After that it is a conflict and accounts come from authctl.
	mux.HandleFunc("POST /v1/auth/bootstrap", s.handleBootstrap)
	mux.HandleFunc("POST /v1/auth/login", s.handleLogin)
	mux.HandleFunc("POST /v1/auth/logout", s.handleLogout)
	mux.HandleFunc("GET /v1/auth/me", s.handleMe)
	// The account's own page: a name, a password, and everywhere it is signed
	// in. All scoped to the session asking — there is no route here that takes
	// somebody else's user id.
	mux.HandleFunc("PATCH /v1/auth/profile", s.handleUpdateProfile)
	mux.HandleFunc("POST /v1/auth/password", s.handleChangePassword)
	mux.HandleFunc("GET /v1/auth/sessions", s.handleLogins)
	// Before the {id} route, which would otherwise swallow it.
	mux.HandleFunc("DELETE /v1/auth/sessions/others", s.handleRevokeOtherLogins)
	mux.HandleFunc("DELETE /v1/auth/sessions/{id}", s.handleRevokeLogin)
	// API tokens, for callers without a browser. Managed only from a cookie
	// session: a token cannot list, mint or revoke tokens (see auth.go).
	mux.HandleFunc("GET /v1/auth/tokens", s.handleTokens)
	mux.HandleFunc("POST /v1/auth/tokens", s.handleCreateToken)
	mux.HandleFunc("DELETE /v1/auth/tokens/{id}", s.handleRevokeToken)
	// A stranger asking for an account. Lands as pending; an admin decides.
	mux.HandleFunc("POST /v1/auth/register", s.handleRegister)

	// The Users page. Admins only, on a cookie session (see users.go).
	mux.HandleFunc("GET /v1/admin/users", s.handleUsers)
	mux.HandleFunc("POST /v1/admin/users", s.handleCreateUser)
	mux.HandleFunc("GET /v1/admin/users/{id}", s.handleUser)
	mux.HandleFunc("PATCH /v1/admin/users/{id}", s.handleUpdateUser)
	mux.HandleFunc("DELETE /v1/admin/users/{id}", s.handleRejectUser)
	mux.HandleFunc("POST /v1/admin/users/{id}/approve", s.handleApproveUser)
	mux.HandleFunc("POST /v1/admin/users/{id}/reset-password", s.handleResetUserPassword)

	// What readers have asked the warehouse to collect. A queue rather than a
	// message: it keeps a status, and the next person about to ask for the
	// same source can see that somebody already did.
	mux.HandleFunc("GET /v1/suggestions", s.handleSuggestions)
	mux.HandleFunc("POST /v1/suggestions", s.handleCreateSuggestion)
	mux.HandleFunc("PATCH /v1/suggestions/{id}", s.handleUpdateSuggestion)
	mux.HandleFunc("DELETE /v1/suggestions/{id}", s.handleDeleteSuggestion)

	mux.HandleFunc("GET /v1/queries", s.handleSavedQueries)
	mux.HandleFunc("POST /v1/queries", s.handleSaveQuery)
	mux.HandleFunc("PATCH /v1/queries/{id}", s.handleRenameQuery)
	mux.HandleFunc("DELETE /v1/queries/{id}", s.handleDeleteQuery)

	// The chat that points a reader at what is here. Streamed, and so never
	// cached; off without a Workers AI account (see assistant.go).
	mux.HandleFunc("POST /v1/assistant/chat", s.handleAssistantChat)
	// Its conversations, by UUID (see assistant_conversations.go).
	mux.HandleFunc("GET /v1/assistant/conversations", s.handleConversations)
	mux.HandleFunc("GET /v1/assistant/conversations/{id}", s.handleConversation)
	mux.HandleFunc("DELETE /v1/assistant/conversations/{id}", s.handleDeleteConversation)
	mux.HandleFunc("PATCH /v1/assistant/conversations/{id}/messages/{seq}", s.handleSetMessageCollection)

	// Outermost first: CORS answers the browser's preflight before anything
	// else looks at the request, the log records what arrived, the session is
	// resolved once, and the gate decides whether it goes through.
	return s.withCORS(s.withCompression(s.withRequestLogging(s.withSession(s.withAuth(mux)))))
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeData(w, map[string]string{"status": "ok"}, nil)
}

// handleReady reports whether this process can actually serve data.
//
// Storage reachability is the interesting part: a misconfigured mount or bucket
// leaves the API healthy and useless.
func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	body := map[string]any{
		"status":          "ok",
		"storage_profile": string(s.cfg.Storage.Profile),
		"storage_backend": string(s.cfg.Storage.Backend),
		"storage_root":    s.storage.Root(),
		// Reported because a cache that has quietly stopped working looks
		// exactly like one that is working, only slower.
		"cache": s.cache.Stats(),
	}
	if err := s.warehouse.Ping(r.Context()); err != nil {
		body["status"] = "degraded"
		body["warehouse_error"] = err.Error()
		writeJSON(w, http.StatusServiceUnavailable, Response[any]{
			Data: body,
			Err: &APIError{
				Code:    CodeUnavailable,
				Message: "the analytical engine is not answering",
			},
		})
		return
	}
	writeData(w, body, nil)
}

func (s *Server) withRequestLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		next.ServeHTTP(w, r)
		s.log.Info("request",
			"method", r.Method,
			"path", r.URL.Path,
			"query", r.URL.RawQuery,
			"ms", time.Since(started).Milliseconds(),
		)
	})
}

// withCORS allows the portal's dev server to call the API.
//
// The allow-list comes from configuration rather than a wildcard: a data portal
// serving restricted datasets should not be readable from any page a browser
// happens to load (program.md §36).
func (s *Server) withCORS(next http.Handler) http.Handler {
	allowed := make(map[string]bool, len(s.cfg.CORSOrigins))
	for _, origin := range s.cfg.CORSOrigins {
		allowed[strings.TrimSpace(origin)] = true
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" && allowed[origin] {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
			w.Header().Set("Access-Control-Allow-Methods",
				"GET, POST, PATCH, DELETE, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
			// The session cookie only reaches the API from the portal's origin
			// if this says so — and it is safe to say because the origin is an
			// allow-list above rather than a wildcard, which the browser would
			// refuse to pair with credentials anyway.
			w.Header().Set("Access-Control-Allow-Credentials", "true")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
