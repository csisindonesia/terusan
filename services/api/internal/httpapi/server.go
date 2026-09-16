// Package httpapi wires the serving layer's HTTP surface.
package httpapi

import (
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// Server holds the dependencies every handler needs.
type Server struct {
	cfg       *config.Config
	storage   *storage.Resolver
	warehouse *query.Warehouse
	log       *slog.Logger
}

// New returns a Server over cfg.
func New(cfg *config.Config, warehouse *query.Warehouse, log *slog.Logger) *Server {
	return &Server{
		cfg:       cfg,
		storage:   storage.NewResolver(cfg.Storage),
		warehouse: warehouse,
		log:       log,
	}
}

// Routes returns the HTTP handler for the whole API.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", s.handleHealth)
	mux.HandleFunc("GET /readyz", s.handleReady)

	mux.HandleFunc("GET /v1/observations", s.handleObservations)
	mux.HandleFunc("GET /v1/indicators", s.handleIndicators)
	mux.HandleFunc("GET /v1/geography", s.handleGeography)
	mux.HandleFunc("GET /v1/datasets", s.handleDatasets)

	return s.withCORS(s.withRequestLogging(mux))
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
			w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
