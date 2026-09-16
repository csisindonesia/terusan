// Package config assembles the serving layer's runtime settings.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Config is the fully resolved configuration for one API process.
type Config struct {
	Host     string
	Port     int
	Storage  *storage.Config
	Database string

	DuckDBMemoryLimit string
	DuckDBThreads     int

	// Origins allowed to call the API from a browser. An allow-list rather
	// than a wildcard: restricted datasets should not be readable from any
	// page a browser happens to load.
	CORSOrigins []string

	ReadTimeout     time.Duration
	WriteTimeout    time.Duration
	ShutdownTimeout time.Duration
}

// Load reads configuration from the environment. It fails rather than
// defaulting whenever a wrong guess would be worse than not starting.
func Load() (*Config, error) {
	storageCfg, err := storage.LoadConfig()
	if err != nil {
		return nil, fmt.Errorf("storage config: %w", err)
	}

	port, err := intEnv("API_PORT", 8080)
	if err != nil {
		return nil, err
	}
	threads, err := intEnv("DUCKDB_THREADS", 4)
	if err != nil {
		return nil, err
	}

	return &Config{
		Host:              envOr("API_HOST", "127.0.0.1"),
		Port:              port,
		Storage:           storageCfg,
		Database:          os.Getenv("DATABASE_URL"),
		DuckDBMemoryLimit: envOr("DUCKDB_MEMORY_LIMIT", "4GB"),
		DuckDBThreads:     threads,
		CORSOrigins: strings.Split(
			envOr("API_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"), ",",
		),
		ReadTimeout:     15 * time.Second,
		WriteTimeout:    120 * time.Second, // analytical queries are slow by nature
		ShutdownTimeout: 20 * time.Second,
	}, nil
}

// Addr is the listen address for the HTTP server.
func (c *Config) Addr() string { return fmt.Sprintf("%s:%d", c.Host, c.Port) }

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func intEnv(key string, fallback int) (int, error) {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be an integer, got %q", key, raw)
	}
	return v, nil
}
