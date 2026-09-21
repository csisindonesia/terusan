// Package appdb opens the one database the serving layer writes to.
//
// Everything else this service answers is derived from the lake and can be
// rebuilt by re-running a pipeline. Two things cannot: the shelf somebody
// assembled (internal/collections) and the accounts people log in with
// (internal/auth). Both live here, in one DuckDB file beside the lake rather
// than inside it — the lake is pipeline-written and immutable (program.md
// §45.3), and a re-ingestion must not be able to walk over a password.
//
// One file and one handle, shared by both packages, because DuckDB holds a
// database file exclusively per process: opening it twice from the same binary
// is a lock error rather than two connections.
//
// DuckDB because the process already links it for the warehouse, and this is a
// few hundred rows. It is not a claim that DuckDB is where a multi-tenant
// portal keeps its users — when there are organisations to scope accounts to,
// this is the layer that moves to PostgreSQL, and the packages above it are
// written so that move is a rewrite of their SQL and nothing else.
package appdb

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "github.com/marcboeker/go-duckdb/v2"
)

// DB is the application database: a DuckDB file and the one handle onto it.
type DB struct {
	*sql.DB
	path string
}

// Open prepares the database at path, creating the file and its directory if
// they are not there yet.
func Open(path string) (*DB, error) {
	if path == "" {
		return nil, fmt.Errorf("no path")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("app database path: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return nil, fmt.Errorf("app database directory: %w", err)
	}

	db, err := sql.Open("duckdb", abs)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", abs, err)
	}
	// One connection. DuckDB holds the file exclusively per process anyway,
	// and what lives here is a few hundred rows read in single-digit
	// milliseconds — serialising them costs nothing and removes every write
	// conflict between the shelf and a login happening at the same moment.
	db.SetMaxOpenConns(1)

	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("open %s: %w", abs, err)
	}
	return &DB{DB: db, path: abs}, nil
}

// Path is where the database is kept, for the startup log and the readiness
// report.
func (d *DB) Path() string {
	if d == nil {
		return ""
	}
	return d.path
}
