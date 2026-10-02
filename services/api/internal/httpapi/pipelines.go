package httpapi

import (
	"context"
	"net/http"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Pipeline is the run journal folded to one row per pipeline: how it last went
// and how it has been going.
//
// `/v1/runs` answers "what happened", a row per run, and is the right shape for
// reading one pipeline's history. It is the wrong shape for the question an
// operator brings to the administration pages — "which of my several hundred
// pipelines is broken" — because answering it from the run list means paging
// through thousands of rows to find the newest of each. The journal already
// holds the answer; this groups it where the data is.
type Pipeline struct {
	Pipeline string `json:"pipeline"`
	Kind     string `json:"kind"`
	// What the newest run touched. A pipeline is named after its target, so
	// these do not change between runs in practice; the newest is taken so a
	// renamed dataset shows its current name.
	SourceID    *string `json:"source_id,omitempty"`
	Dataset     *string `json:"dataset,omitempty"`
	IndicatorID *string `json:"indicator_id,omitempty"`

	Runs     int64 `json:"runs"`
	Failures int64 `json:"failures"`
	// Failures since the last run that succeeded and was not a dry run. Zero
	// for a healthy pipeline; for a broken one, how long it has been broken —
	// which a single "last status" cannot say.
	FailingStreak int64 `json:"failing_streak"`

	LastRunID     string  `json:"last_run_id"`
	LastStatus    string  `json:"last_status"`
	LastTrigger   string  `json:"last_trigger"`
	LastDryRun    bool    `json:"last_dry_run"`
	LastStartedAt string  `json:"last_started_at"`
	LastSeconds   float64 `json:"last_duration_seconds"`
	LastRecords   int64   `json:"last_records_out"`
	// Absent where the pipeline has never landed anything for real.
	LastSucceededAt *string `json:"last_succeeded_at,omitempty"`
	// The message the newest failure recorded, whether or not the pipeline
	// has recovered since: a pipeline that fails one night in three is worth
	// reading about even on a night it worked.
	LastError *string `json:"last_error,omitempty"`
}

// handlePipelines serves the journal grouped by pipeline, most recently run
// first.
//
// Unpaged: there are a few hundred pipelines, one per source and one per
// normalized series, and the page filters them in place.
func (s *Server) handlePipelines(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	kinds, err := stringListParam(r, "kind", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	sources, err := stringListParam(r, "source", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	var clauses []string
	var args []any
	for _, filter := range []struct {
		column string
		values []string
	}{
		{"kind", kinds},
		{"source_id", sources},
	} {
		if clause, bound := inClause(filter.column, filter.values); clause != "" {
			clauses = append(clauses, clause)
			args = append(args, bound...)
		}
	}

	pipelines, err := s.pipelines(ctx, clauses, args)
	if err != nil {
		internalError(w, s.log, "summarise pipelines", err)
		return
	}
	writeData(w, pipelines, &Meta{Total: int64(len(pipelines)), Layer: "silver"})
}

// pipelines groups the journal, narrowed by the clauses given.
func (s *Server) pipelines(ctx context.Context, clauses []string, args []any) ([]Pipeline, error) {
	results := make([]Pipeline, 0, 256)
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "pipeline_runs") {
		// The same reading as an empty run list: nothing recorded yet.
		return results, nil
	}

	journal, err := s.source(storage.LayerSilver, "pipeline_runs")
	if err != nil {
		return nil, err
	}

	where := ""
	if len(clauses) > 0 {
		where = " WHERE " + strings.Join(clauses, " AND ")
	}

	// The last good run is found first and joined back, because the streak is
	// "failures after it", and an aggregate cannot refer to another aggregate
	// in the same grouping. The instants are rendered in UTC for the reason
	// runColumns gives.
	rows, err := s.warehouse.DB().QueryContext(ctx, `
		WITH j AS (SELECT * FROM `+journal+where+`),
		good AS (
			SELECT pipeline, kind,
			       max(started_at) FILTER (WHERE status = 'succeeded' AND NOT dry_run) AS good_at
			FROM j GROUP BY pipeline, kind
		)
		SELECT pipeline, kind,
		       arg_max(source_id, started_at), arg_max(dataset, started_at),
		       arg_max(indicator_id, started_at),
		       count(*),
		       count(*) FILTER (WHERE status = 'failed'),
		       count(*) FILTER (WHERE status = 'failed'
		                        AND (good.good_at IS NULL OR j.started_at > good.good_at)),
		       arg_max(run_id, started_at), arg_max(status, started_at),
		       arg_max(trigger, started_at), arg_max(dry_run, started_at),
		       strftime(max(started_at) AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
		       arg_max(duration_seconds, started_at), arg_max(records_out, started_at),
		       strftime(any_value(good.good_at) AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
		       arg_max(error_message, started_at) FILTER (WHERE status = 'failed')
		FROM j JOIN good USING (pipeline, kind)
		GROUP BY pipeline, kind
		ORDER BY max(started_at) DESC, pipeline`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	for rows.Next() {
		var p Pipeline
		if err := rows.Scan(
			&p.Pipeline, &p.Kind, &p.SourceID, &p.Dataset, &p.IndicatorID,
			&p.Runs, &p.Failures, &p.FailingStreak,
			&p.LastRunID, &p.LastStatus, &p.LastTrigger, &p.LastDryRun,
			&p.LastStartedAt, &p.LastSeconds, &p.LastRecords,
			&p.LastSucceededAt, &p.LastError,
		); err != nil {
			return nil, err
		}
		results = append(results, p)
	}
	return results, rows.Err()
}
