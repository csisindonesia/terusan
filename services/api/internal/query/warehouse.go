// Package query reads the analytical layers through DuckDB.
//
// The serving layer holds no data of its own: it opens Parquet through DuckDB
// over whichever backend the storage resolver points at, so the same binary
// serves a local lake, a NAS mount or a bucket without knowing which
// (program.md §24, §45.4).
package query

import (
	"context"
	"database/sql"
	"fmt"
	"strings"

	_ "github.com/marcboeker/go-duckdb/v2"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Warehouse is a DuckDB connection pointed at the lake.
type Warehouse struct {
	db       *sql.DB
	resolver *storage.Resolver
}

// Open configures a DuckDB connection for the given storage backend.
func Open(resolver *storage.Resolver, memoryLimit string, threads int) (*Warehouse, error) {
	db, err := sql.Open("duckdb", "")
	if err != nil {
		return nil, fmt.Errorf("open duckdb: %w", err)
	}

	cfg := resolver.Config()
	settings := []string{
		fmt.Sprintf("SET memory_limit='%s'", memoryLimit),
		fmt.Sprintf("SET threads=%d", threads),
		// Spill stays on local disk however the lake is stored: a NAS-backed
		// temp directory turns a large join into a network-bound crawl.
		fmt.Sprintf("SET temp_directory='%s'", storage.ResolvePath(cfg.ScratchDir)),
	}
	for _, statement := range settings {
		if _, err := db.Exec(statement); err != nil {
			db.Close()
			return nil, fmt.Errorf("configure duckdb (%s): %w", statement, err)
		}
	}

	if cfg.IsObjectStorage() {
		if err := attachBucket(db, cfg); err != nil {
			db.Close()
			return nil, err
		}
	}

	return &Warehouse{db: db, resolver: resolver}, nil
}

func attachBucket(db *sql.DB, cfg *storage.Config) error {
	for _, statement := range []string{"INSTALL httpfs", "LOAD httpfs"} {
		if _, err := db.Exec(statement); err != nil {
			return fmt.Errorf("%s: %w", statement, err)
		}
	}

	parts := []string{
		"TYPE S3",
		fmt.Sprintf("KEY_ID '%s'", cfg.S3AccessKey),
		fmt.Sprintf("SECRET '%s'", cfg.S3SecretKey),
		fmt.Sprintf("REGION '%s'", cfg.S3Region),
		fmt.Sprintf("USE_SSL %t", cfg.S3UseSSL),
	}
	if cfg.S3Endpoint != "" {
		endpoint := strings.TrimPrefix(strings.TrimPrefix(cfg.S3Endpoint, "https://"), "http://")
		parts = append(parts, fmt.Sprintf("ENDPOINT '%s'", endpoint), "URL_STYLE 'path'")
	}

	statement := fmt.Sprintf("CREATE OR REPLACE SECRET lake (%s)", strings.Join(parts, ", "))
	if _, err := db.Exec(statement); err != nil {
		return fmt.Errorf("attach bucket credentials: %w", err)
	}
	return nil
}

// Close releases the connection pool.
func (w *Warehouse) Close() error { return w.db.Close() }

// DB exposes the connection for callers needing their own statements.
func (w *Warehouse) DB() *sql.DB { return w.db }

// Source returns a read_parquet expression for one dataset.
//
// Returned as SQL text rather than a registered view because the lake changes
// under a long-lived process: a pipeline run adds files, and a view pinned at
// startup would keep reading the old set.
func (w *Warehouse) Source(layer storage.Layer, dataset string, partition ...string) (string, error) {
	pattern, err := w.resolver.Glob(layer, append([]string{dataset}, partition...)...)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf(
		"read_parquet('%s', union_by_name=true, hive_partitioning=true)", pattern,
	), nil
}

// Exists reports whether a dataset has any files behind it.
//
// Checked before querying so an empty lake answers "no rows" rather than a
// DuckDB IO error, which would read to a consumer as a server fault.
func (w *Warehouse) Exists(ctx context.Context, layer storage.Layer, dataset string) bool {
	expression, err := w.Source(layer, dataset)
	if err != nil {
		return false
	}
	var one int
	err = w.db.QueryRowContext(ctx, "SELECT 1 FROM "+expression+" LIMIT 1").Scan(&one)
	return err == nil || err == sql.ErrNoRows
}

// Ping verifies the connection is usable.
func (w *Warehouse) Ping(ctx context.Context) error { return w.db.PingContext(ctx) }
