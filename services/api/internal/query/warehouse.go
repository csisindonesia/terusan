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
	"sync"
	"time"

	_ "github.com/marcboeker/go-duckdb/v2"

	"github.com/csis/terusan/services/api/internal/storage"
)

// MetadataTTL is how long "does this dataset exist" and "does it carry this
// column" are believed without re-checking.
//
// These are asked several times per request and answer to the shape of the
// lake, which changes when a pipeline run finishes — minutes or hours apart,
// never mid-request. Re-deriving them per request cost more than the queries
// they guard: the existence probe over the observations measured at 176ms,
// because it expands a glob across sixteen hundred partition directories.
//
// Short enough that a pipeline run becomes visible without a restart, which is
// the behaviour `Source` goes out of its way to preserve.
const MetadataTTL = 30 * time.Second

// Warehouse is a DuckDB connection pointed at the lake.
type Warehouse struct {
	db       *sql.DB
	resolver *storage.Resolver

	// Memoised answers to the shape questions, keyed by what was asked.
	// Guarded by a plain mutex rather than sync.Map: the map is tiny, the
	// critical section is a lookup, and contention is a handful of
	// goroutines rather than a hot loop.
	metaMu sync.Mutex
	meta   map[string]metaAnswer
}

type metaAnswer struct {
	value bool
	at    time.Time
}

// remember runs `answer` unless a fresh one is already held.
//
// The query is deliberately made outside the lock. Holding it across a 176ms
// glob would serialise every request behind the first one — which is the
// stall this cache exists to remove, reintroduced.
func (w *Warehouse) remember(key string, answer func() bool) bool {
	w.metaMu.Lock()
	held, ok := w.meta[key]
	w.metaMu.Unlock()
	if ok && time.Since(held.at) < MetadataTTL {
		return held.value
	}

	value := answer()

	w.metaMu.Lock()
	w.meta[key] = metaAnswer{value: value, at: time.Now()}
	w.metaMu.Unlock()
	return value
}

// ForgetMetadata drops the memoised shape answers, so the next request sees
// the lake as it is now. For a process that has just been told a pipeline ran.
func (w *Warehouse) ForgetMetadata() {
	w.metaMu.Lock()
	clear(w.meta)
	w.metaMu.Unlock()
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

	return &Warehouse{db: db, resolver: resolver, meta: map[string]metaAnswer{}}, nil
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

// MaxNarrowedPartitions bounds how many partition globs are worth naming.
//
// Past this the list of patterns is itself a cost — DuckDB expands each one —
// and a query asking for fifty series is heading for a full scan anyway.
const MaxNarrowedPartitions = 32

// SourceIn returns a read_parquet expression narrowed to named partitions.
//
// The point is that DuckDB prunes partitions *after* expanding the glob, so a
// filter on the partition key does not save the listing. Over the observations
// — sixteen hundred directories, one per series — that listing is the whole
// cost of the query: counting one series through the full glob measured at
// 185ms, and through its own partition at 0.3ms.
//
// Narrowing is an optimisation and never a filter. The caller keeps its WHERE
// clause; this only spares the engine the directories that clause was going to
// discard. Anything that cannot be narrowed safely falls back to the whole
// dataset, which is slower and identical.
func (w *Warehouse) SourceIn(
	layer storage.Layer, dataset, key string, values []string,
) (string, error) {
	if len(values) == 0 || len(values) > MaxNarrowedPartitions {
		return w.Source(layer, dataset)
	}

	patterns := make([]string, 0, len(values))
	for _, value := range values {
		segment := key + "=" + value
		// A partition that is not there must not be named: read_parquet
		// raises "No files found that match the pattern" rather than
		// returning nothing, so a filter on a series this lake has never
		// held would turn an empty result into a 500.
		found, known := w.resolver.HasParquet(layer, dataset, segment)
		if !known {
			// Object storage, where existence is not free to check. The
			// full glob is correct; only the saving is lost.
			return w.Source(layer, dataset)
		}
		if !found {
			continue
		}
		pattern, err := w.resolver.Glob(layer, dataset, segment)
		if err != nil {
			return w.Source(layer, dataset)
		}
		patterns = append(patterns, "'"+pattern+"'")
	}

	// Every named partition is absent. The caller's WHERE returns nothing
	// either way, so the full glob is the correct answer and the only one
	// that does not raise.
	if len(patterns) == 0 {
		return w.Source(layer, dataset)
	}

	return fmt.Sprintf(
		"read_parquet([%s], union_by_name=true, hive_partitioning=true)",
		strings.Join(patterns, ", "),
	), nil
}

// Exists reports whether a dataset has any files behind it.
//
// Checked before querying so an empty lake answers "no rows" rather than a
// DuckDB IO error, which would read to a consumer as a server fault.
func (w *Warehouse) Exists(ctx context.Context, layer storage.Layer, dataset string) bool {
	return w.remember("exists:"+layer.String()+":"+dataset, func() bool {
		// The filesystem knows this without opening a Parquet file, and
		// knows it in a millisecond. Only a bucket, which has no cheap
		// equivalent, falls through to the engine.
		if found, known := w.resolver.HasParquet(layer, dataset); known {
			return found
		}
		expression, err := w.Source(layer, dataset)
		if err != nil {
			return false
		}
		var one int
		err = w.db.QueryRowContext(ctx, "SELECT 1 FROM "+expression+" LIMIT 1").Scan(&one)
		return err == nil || err == sql.ErrNoRows
	})
}

// HasColumn reports whether a dataset's files carry a column.
//
// Needed because the lake outlives a schema change: a column added to Silver
// this month is absent from every file written before it, and a query naming
// it fails to bind rather than returning nulls. Asked per request, like
// Exists, since a pipeline run can add the column under a running server.
func (w *Warehouse) HasColumn(
	ctx context.Context, layer storage.Layer, dataset, column string,
) bool {
	return w.remember("column:"+layer.String()+":"+dataset+":"+column, func() bool {
		expression, err := w.Source(layer, dataset)
		if err != nil {
			return false
		}
		rows, err := w.db.QueryContext(
			ctx, "SELECT "+column+" FROM "+expression+" LIMIT 0",
		)
		if err != nil {
			return false
		}
		defer rows.Close()
		return rows.Err() == nil
	})
}

// Ping verifies the connection is usable.
func (w *Warehouse) Ping(ctx context.Context) error { return w.db.PingContext(ctx) }
