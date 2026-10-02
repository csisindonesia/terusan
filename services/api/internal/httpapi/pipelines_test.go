package httpapi

import (
	"context"
	"database/sql"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// journalLake is a lake whose journal holds three pipelines: one healthy, one
// that broke after working, and one that has never worked.
func journalLake(t *testing.T) *Server {
	t.Helper()
	root := t.TempDir()
	journal := filepath.Join(root, "silver", "pipeline_runs")
	if err := os.MkdirAll(journal, 0o755); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	statement := `COPY (SELECT run_id, pipeline, kind, status, 'manual' AS trigger,
			source_id, NULL::VARCHAR AS dataset, NULL::VARCHAR AS indicator_id,
			started_at::TIMESTAMPTZ AS started_at, started_at::TIMESTAMPTZ AS finished_at,
			1.5::DOUBLE AS duration_seconds, 10::BIGINT AS records_in,
			records_out::BIGINT AS records_out, 0::BIGINT AS bytes_written,
			error_message, NULL::VARCHAR AS detail, NULL::VARCHAR AS pipeline_version,
			NULL::VARCHAR AS parser_version, dry_run, NULL::VARCHAR AS command,
			NULL::VARCHAR AS host
		FROM (VALUES
		('r1', 'ingest-bps', 'ingest', 'succeeded', 'bps', '2026-09-01 01:00:00+00', 5, NULL, false),
		('r2', 'ingest-bps', 'ingest', 'succeeded', 'bps', '2026-09-02 01:00:00+00', 7, NULL, false),
		('r3', 'ingest-bi', 'ingest', 'succeeded', 'bi', '2026-09-01 02:00:00+00', 3, NULL, false),
		('r4', 'ingest-bi', 'ingest', 'failed', 'bi', '2026-09-02 02:00:00+00', 0, 'timeout', false),
		('r5', 'ingest-bi', 'ingest', 'succeeded', 'bi', '2026-09-03 02:00:00+00', 0, NULL, true),
		('r6', 'ingest-bi', 'ingest', 'failed', 'bi', '2026-09-04 02:00:00+00', 0, 'refused', false),
		('r7', 'ingest-ojk', 'ingest', 'failed', 'ojk', '2026-08-30 02:00:00+00', 0, 'gated', false)
		) AS t(run_id, pipeline, kind, status, source_id, started_at, records_out, error_message, dry_run))
		TO '` + filepath.Join(journal, "part-0.parquet") + `' (FORMAT parquet)`
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

func TestPipelinesFoldTheJournalToOneRowEach(t *testing.T) {
	s := journalLake(t)
	pipelines, err := s.pipelines(context.Background(), nil, nil)
	if err != nil {
		t.Fatal(err)
	}

	byName := map[string]Pipeline{}
	order := make([]string, len(pipelines))
	for n, p := range pipelines {
		byName[p.Pipeline] = p
		order[n] = p.Pipeline
	}
	// Most recently run first.
	if want := []string{"ingest-bi", "ingest-bps", "ingest-ojk"}; !slices.Equal(order, want) {
		t.Fatalf("order = %v, want %v", order, want)
	}

	healthy := byName["ingest-bps"]
	if healthy.Runs != 2 || healthy.Failures != 0 || healthy.FailingStreak != 0 {
		t.Errorf("healthy = %+v", healthy)
	}
	if healthy.LastRecords != 7 || healthy.LastRunID != "r2" {
		t.Errorf("healthy newest run = %+v", healthy)
	}

	// The dry run in between proves nothing landed, so the streak runs back to
	// the last real success.
	broken := byName["ingest-bi"]
	if broken.LastStatus != "failed" || broken.FailingStreak != 2 || broken.Failures != 2 {
		t.Errorf("broken = %+v", broken)
	}
	if broken.LastSucceededAt == nil || *broken.LastSucceededAt != "2026-09-01T02:00:00Z" {
		t.Errorf("broken last success = %v", broken.LastSucceededAt)
	}
	if broken.LastError == nil || *broken.LastError != "refused" {
		t.Errorf("broken last error = %v", broken.LastError)
	}

	never := byName["ingest-ojk"]
	if never.LastSucceededAt != nil || never.FailingStreak != 1 {
		t.Errorf("never = %+v", never)
	}
}

func TestPipelinesNarrowBySource(t *testing.T) {
	s := journalLake(t)
	clause, args := inClause("source_id", []string{"bps"})
	pipelines, err := s.pipelines(context.Background(), []string{clause}, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(pipelines) != 1 || pipelines[0].Pipeline != "ingest-bps" {
		t.Fatalf("pipelines = %+v", pipelines)
	}
}
