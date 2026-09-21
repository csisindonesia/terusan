// Command api runs the Terusan serving layer.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/csis/terusan/services/api/internal/appdb"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/collections"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/httpapi"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
	"github.com/csis/terusan/services/api/internal/suggestions"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

	if err := run(log); err != nil {
		log.Error("fatal", "error", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	resolver := storage.NewResolver(cfg.Storage)
	warehouse, err := query.Open(resolver, cfg.DuckDBMemoryLimit, cfg.DuckDBThreads)
	if err != nil {
		return err
	}
	defer warehouse.Close()

	// The cache is optional and its absence is not an error: a deployment
	// with no REDIS_URL serves every request from Parquet, which is correct
	// and slower. A configured Redis that cannot be reached *is* an error,
	// though — it means somebody expected caching and is not getting it, and
	// finding that out from a latency graph is worse than not starting.
	var responses cache.Cache = cache.Nothing{}
	if cfg.RedisURL != "" {
		redis, err := cache.Open(cfg.RedisURL, log)
		if err != nil {
			return err
		}
		defer redis.Close()
		responses = redis
		log.Info("cache.ready", "backend", "redis", "ttl", cfg.CacheTTL.String())
	} else {
		log.Info("cache.disabled", "reason", "REDIS_URL is not set")
	}

	// The application database is optional in the same way the cache is, and
	// for the opposite reason: without it nothing is lost from what the API can
	// answer, only the ability to address a folder by URL and to say who is
	// asking. A configured path that cannot be opened is an error — somebody
	// meant to keep this here.
	var shelf *collections.Store
	var accounts *auth.Service
	var asked *suggestions.Store
	if cfg.AppDB != "" {
		db, err := appdb.Open(cfg.AppDB)
		if err != nil {
			return err
		}
		defer db.Close()

		shelf, err = collections.New(db, cfg.Collections.Write)
		if err != nil {
			return err
		}
		accounts, err = auth.New(db, auth.Config{
			TTL:         cfg.Auth.TTL,
			RememberTTL: cfg.Auth.RememberTTL,
			Iterations:  cfg.Auth.Iterations,
		})
		if err != nil {
			return err
		}
		asked, err = suggestions.New(db)
		if err != nil {
			return err
		}
		users, err := accounts.Count(context.Background())
		if err != nil {
			return err
		}
		log.Info("appdb.ready",
			"path", db.Path(),
			"collections_writable", shelf.Writable(),
			"accounts", users,
			"auth_required", cfg.Auth.Required)
		if users == 0 {
			// A deployment with no accounts cannot be logged into, and the
			// portal would show a login page nobody can pass. Said at startup
			// rather than discovered at the form.
			log.Warn("auth.no_accounts",
				"fix", "cd services/api && go run ./cmd/authctl create -email you@example.org")
		}
	} else {
		log.Info("appdb.disabled", "reason", "APP_DB is not set")
	}

	srv := &http.Server{
		Addr: cfg.Addr(),
		Handler: httpapi.New(
			cfg, warehouse, responses, shelf, accounts, asked, log,
		).Routes(),
		ReadTimeout:  cfg.ReadTimeout,
		WriteTimeout: cfg.WriteTimeout,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	errs := make(chan error, 1)
	go func() {
		log.Info("serving",
			"addr", cfg.Addr(),
			"storage_profile", string(cfg.Storage.Profile),
			"storage_backend", string(cfg.Storage.Backend),
			"storage_root", resolver.Root(),
		)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
	}()

	select {
	case err := <-errs:
		return err
	case <-ctx.Done():
		log.Info("shutting down")
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	return srv.Shutdown(shutdownCtx)
}
