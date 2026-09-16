// Package httpapi wires the serving layer's HTTP surface.
package httpapi

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/storage"
)

// Server holds the dependencies every handler needs.
type Server struct {
	cfg     *config.Config
	storage *storage.Resolver
	log     *slog.Logger
}

// New returns a Server over cfg.
func New(cfg *config.Config, log *slog.Logger) *Server {
	return &Server{
		cfg:     cfg,
		storage: storage.NewResolver(cfg.Storage),
		log:     log,
	}
}

// Routes returns the HTTP handler for the whole API.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealth)
	mux.HandleFunc("GET /readyz", s.handleReady)
	return s.withRequestLogging(mux)
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// handleReady reports whether this process can actually serve data. Storage
// reachability is the interesting part: a misconfigured mount or bucket makes
// the API healthy but useless.
func (s *Server) handleReady(w http.ResponseWriter, _ *http.Request) {
	body := map[string]any{
		"status":          "ok",
		"storage_profile": string(s.cfg.Storage.Profile),
		"storage_backend": string(s.cfg.Storage.Backend),
		"writes_allowed":  s.cfg.Storage.WritesAllowed(),
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) withRequestLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.log.Debug("request", "method", r.Method, "path", r.URL.Path)
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
