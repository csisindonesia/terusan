package httpapi

import (
	"context"
	"database/sql"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"testing"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// tallyLake is a lake whose Bronze holds one newspaper's daily tallies.
//
// kompas.id is read twice on 1 September — a scheduled run and a manual one
// beside it — and the second run re-reads the articles the first only counted.
// It is read once on the 2nd, and once more under an older parser whose rows
// must not count at all.
func tallyLake(t *testing.T) *Server {
	t.Helper()
	root := t.TempDir()
	records := filepath.Join(root, "bronze", "records")
	if err := os.MkdirAll(records, 0o755); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	statement := `COPY (SELECT dataset, parser_version,
			MAP {'outlet_host': 'kompas.id', 'date': day, 'scanned': scanned,
			     'matched': matched, 'recorded': recorded} AS columns,
			retrieved_at, NULL::TIMESTAMP AS published_at
		FROM (VALUES
		('news-daily-tallies', '26', '2026-09-01', '40', '3', '2', TIMESTAMP '2026-09-01 06:00'),
		('news-daily-tallies', '26', '2026-09-01', '55', '4', '2', TIMESTAMP '2026-09-01 14:00'),
		('news-daily-tallies', '26', '2026-09-02', '30', '1', '1', TIMESTAMP '2026-09-03 06:00'),
		('news-daily-tallies', '25', '2026-09-02', '99', '9', '9', TIMESTAMP '2026-09-04 06:00')
		) AS t(dataset, parser_version, day, scanned, matched, recorded, retrieved_at))
		TO '` + filepath.Join(records, "part-0.parquet") + `' (FORMAT parquet)`
	if _, err := db.Exec(statement); err != nil {
		t.Fatalf("%v\n%s", err, statement)
	}

	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	resolver := storage.NewResolver(cfg)
	warehouse, err := query.Open(resolver, "1GB", 1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { warehouse.Close() })
	return &Server{
		cfg:       &config.Config{Storage: cfg},
		storage:   resolver,
		warehouse: warehouse,
		log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

// An outlet read twice in a day is counted once, by its fuller tally — the
// reduction the pipelines use for `news_articles_read`, so the outlet page and
// that series report the same figure.
func TestOutletStatsCountEachDayOnceByItsFullestTally(t *testing.T) {
	s := tallyLake(t)
	ctx := context.Background()
	var host, lastSeen string
	var scanned, matched, recorded int64
	if err := s.warehouse.DB().QueryRowContext(ctx,
		"SELECT host, scanned, matched, recorded, last_seen FROM ("+s.newsOutletStats(ctx)+")",
	).Scan(&host, &scanned, &matched, &recorded, &lastSeen); err != nil {
		t.Fatal(err)
	}
	if host != "kompas.id" || scanned != 55+30 || matched != 4+1 || recorded != 2+1 {
		t.Errorf("stats %s scanned=%d matched=%d recorded=%d; want kompas.id 85/5/3",
			host, scanned, matched, recorded)
	}
	// The last visit under the current parser, not the older one's, and not
	// the publication date of what it read.
	if lastSeen != "2026-09-03" {
		t.Errorf("last_seen %q; want 2026-09-03", lastSeen)
	}
}
