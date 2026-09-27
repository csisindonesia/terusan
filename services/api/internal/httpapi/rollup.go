package httpapi

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// The rollup: every observation, folded to one row per series, place,
// commodity, source, collection and unit, with how many figures it stands for
// and the span they cover.
//
// Every summary the catalogue answers — each series' count and coverage, each
// collection's, each commodity's, the lake's total — is a sum, a min, a max or
// a distinct count over those columns, so it comes out of the rollup exactly
// as it would out of the observations. The difference is size: 15.6 million
// rows in 31,000 files against 590,000 rows in memory. It is built by one
// scan when the catalogue is built, in this process's DuckDB, and rebuilt
// when the lake changes.
//
// Built here rather than written by the pipelines as a Gold table so it is
// always the lake's own: a pipeline run that forgot to rebuild a summary would
// leave the portal quoting last week's counts, and this cannot fall behind
// the files it reads.

const rollupTable = "lake_rollup"

// buildRollup (re)creates the rollup from the observations. Readers of the
// old table go on reading it until the new one is committed.
func (s *Server) buildRollup(ctx context.Context) error {
	observations, err := s.source(storage.LayerSilver, "observations")
	if err != nil {
		return err
	}
	started := time.Now()
	if _, err := s.warehouse.DB().ExecContext(ctx, fmt.Sprintf(`
		CREATE OR REPLACE TABLE %s AS
		SELECT o.indicator_id, o.dataset_id, o.source_id, o.temporal_resolution, o.unit,
		       o.geo_id, o.commodity_id, o.commodity_name_raw,
		       count(*) AS n,
		       min(o.period) AS period_start,
		       max(o.period) AS period_end,
		       max(o.processed_at) AS processed_at
		FROM %s o
		GROUP BY ALL`, rollupTable, observations)); err != nil {
		return fmt.Errorf("build rollup: %w", err)
	}
	s.rollupReady.Store(true)
	s.log.Info("rollup.built", "ms", time.Since(started).Milliseconds())
	return nil
}

// rolled reports whether the catalogue's queries can read the rollup.
func (s *Server) rolled() bool { return s.rollupReady.Load() }

// observationsSignature is what changes when a pipeline writes observations:
// the directory's modification time, which a partition replaced or added
// moves. Empty on object storage, where the catalogue's age alone decides.
func (s *Server) observationsSignature() string {
	if s.storage == nil || s.storage.Config().IsObjectStorage() {
		return ""
	}
	path, err := s.storage.Resolve(storage.LayerSilver, "observations")
	if err != nil {
		return ""
	}
	info, err := os.Stat(path)
	if err != nil {
		return ""
	}
	return info.ModTime().UTC().Format(time.RFC3339Nano)
}

// aggregates is the SQL for the summaries, over the observations or over the
// rollup: the same answers, in the rollup's terms.
type aggregates struct {
	from, count, first, last, processed string
}

func (s *Server) aggregatesOver(observations string) aggregates {
	if s.rolled() {
		return aggregates{
			from:      rollupTable,
			count:     "CAST(sum(o.n) AS BIGINT)",
			first:     "min(o.period_start)",
			last:      "max(o.period_end)",
			processed: "max(o.processed_at)",
		}
	}
	return aggregates{
		from:      observations,
		count:     "count(*)",
		first:     "min(o.period)",
		last:      "max(o.period)",
		processed: "max(o.processed_at)",
	}
}
