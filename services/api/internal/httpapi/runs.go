package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Run is one pipeline run as the journal recorded it (program.md §39, §50).
//
// Served from the lake for the same reason the source registry is: the
// PostgreSQL catalog is optional, and "did the scraper run" is exactly the
// question someone asks on the day the catalog is down. The pipelines write
// every run into `silver/pipeline_runs` as it finishes — see
// terusan_pipelines.warehouse.runlog.
type Run struct {
	RunID string `json:"run_id"`
	// The pipeline that ran: `ingest-bps`, `extract-bronze`,
	// `normalize-bi-food-prices`.
	Pipeline string `json:"pipeline"`
	// ingest | extract | normalize, for a reader asking about a stage rather
	// than about one pipeline.
	Kind string `json:"kind"`
	// succeeded | failed. A run that died without recording anything writes no
	// row at all, which is why the gap between runs is worth reading too.
	Status  string `json:"status"`
	Trigger string `json:"trigger"`
	// What the run touched. Absent where the stage has no opinion: extraction
	// reads every source at once and names none of them.
	SourceID    *string `json:"source_id,omitempty"`
	Dataset     *string `json:"dataset,omitempty"`
	IndicatorID *string `json:"indicator_id,omitempty"`

	StartedAt  string  `json:"started_at"`
	FinishedAt string  `json:"finished_at"`
	Seconds    float64 `json:"duration_seconds"`

	// What came in and what went out. `records_out` is the number that says
	// whether anything actually happened — a run that succeeded and landed
	// nothing is the quiet failure this endpoint exists for.
	RecordsIn    int64 `json:"records_in"`
	RecordsOut   int64 `json:"records_out"`
	BytesWritten int64 `json:"bytes_written"`

	ErrorMessage *string `json:"error_message,omitempty"`
	// Stage-specific counters, as recorded: JSON text rather than a fixed
	// shape, because each stage counts different things.
	Detail *string `json:"detail,omitempty"`

	PipelineVersion *string `json:"pipeline_version,omitempty"`
	ParserVersion   *string `json:"parser_version,omitempty"`
	// A dry run lands nothing on purpose. Reported so its zero is not read as
	// an ingestion that stopped working.
	DryRun  bool    `json:"dry_run"`
	Command *string `json:"command,omitempty"`
	Host    *string `json:"host,omitempty"`
}

// runColumns is the select list, in the order Run is scanned.
//
// The instants are rendered in UTC rather than in whatever timezone the server
// happens to sit in: a run history read from two machines must not disagree
// about when a run happened.
const runColumns = `SELECT run_id, pipeline, kind, status, trigger,
	source_id, dataset, indicator_id,
	strftime(started_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
	strftime(finished_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
	duration_seconds, records_in, records_out, bytes_written,
	error_message, detail, pipeline_version, parser_version,
	dry_run, command, host`

// handleRuns serves the run history, newest first.
func (s *Server) handleRuns(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	sources, err := stringListParam(r, "source", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	pipelines, err := stringListParam(r, "pipeline", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	kinds, err := stringListParam(r, "kind", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	statuses, err := stringListParam(r, "status", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	indicator, err := stringParam(r, "indicator", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, err := intParam(r, "limit", DefaultLimit, 1, MaxLimit)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	offset, err := intParam(r, "offset", 0, 0, 1_000_000)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	var clauses []string
	var args []any
	// A slice rather than a map: the clause and its bound values have to stay
	// paired, and the query text should not change order between two requests
	// that asked the same thing.
	for _, filter := range []struct {
		column string
		values []string
	}{
		{"source_id", sources},
		{"pipeline", pipelines},
		{"kind", kinds},
		{"status", statuses},
		{"indicator_id", nonEmpty(indicator)},
	} {
		if clause, bound := inClause(filter.column, filter.values); clause != "" {
			clauses = append(clauses, clause)
			args = append(args, bound...)
		}
	}

	s.writeRuns(ctx, w, clauses, args, limit, offset)
}

// handleIndicatorRuns serves the runs behind one series.
//
// Which runs those are is not a column: a series is produced by a
// normalization that names it, and fed by whichever sources' ingestions landed
// the documents behind it. So the sources are read off the observations and
// the two are asked for together.
//
// Extraction runs are deliberately absent. Extraction reads the whole of RAW
// at once and belongs to no series, so listing it here would put the same rows
// on every indicator page; `/v1/runs?kind=extract` is where they are.
func (s *Server) handleIndicatorRuns(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id := r.PathValue("id")
	if !identifierPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed identifier")
		return
	}
	limit, err := intParam(r, "limit", DefaultLimit, 1, MaxLimit)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	offset, err := intParam(r, "offset", 0, 0, 1_000_000)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	sources, err := s.sourcesBehind(ctx, id)
	if err != nil {
		internalError(w, s.log, "resolve indicator sources", err)
		return
	}

	clauses := []string{"indicator_id = ?"}
	args := []any{id}
	if clause, bound := inClause("source_id", sources); clause != "" {
		clauses = []string{"(indicator_id = ? OR " + clause + ")"}
		args = append(args, bound...)
	}

	s.writeRuns(ctx, w, clauses, args, limit, offset)
}

// sourcesBehind names the sources whose ingestions feed one series.
func (s *Server) sourcesBehind(ctx context.Context, id string) ([]string, error) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return nil, nil
	}
	observations, err := s.warehouse.SourceIn(
		storage.LayerSilver, "observations", "indicator_id", []string{id},
	)
	if err != nil {
		return nil, err
	}

	rows, err := s.warehouse.DB().QueryContext(ctx,
		"SELECT DISTINCT source_id FROM "+observations+" WHERE indicator_id = ? "+
			"AND source_id IS NOT NULL LIMIT ?", id, MaxFilterValues)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var sources []string
	for rows.Next() {
		var source string
		if err := rows.Scan(&source); err != nil {
			return nil, err
		}
		sources = append(sources, source)
	}
	return sources, rows.Err()
}

// writeRuns runs the narrowed query and answers with it.
func (s *Server) writeRuns(
	ctx context.Context, w http.ResponseWriter, clauses []string, args []any, limit, offset int,
) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "pipeline_runs") {
		// A lake whose pipelines have not run since the journal existed. Not
		// an error, and worth saying plainly: the portal shows "no runs
		// recorded" rather than an empty table that looks like a failure.
		writeData(w, []Run{}, &Meta{Total: 0, Limit: limit, Offset: offset, Layer: "silver"})
		return
	}

	journal, err := s.source(storage.LayerSilver, "pipeline_runs")
	if err != nil {
		internalError(w, s.log, "resolve run journal", err)
		return
	}

	where := ""
	if len(clauses) > 0 {
		where = " WHERE " + strings.Join(clauses, " AND ")
	}

	var total int64
	if err := s.warehouse.DB().QueryRowContext(
		ctx, "SELECT count(*) FROM "+journal+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count runs", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
		"%s FROM %s%s ORDER BY started_at DESC LIMIT ? OFFSET ?", runColumns, journal, where),
		append(append([]any{}, args...), limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query runs", err)
		return
	}
	defer rows.Close()

	results := make([]Run, 0, limit)
	for rows.Next() {
		var run Run
		if err := rows.Scan(
			&run.RunID, &run.Pipeline, &run.Kind, &run.Status, &run.Trigger,
			&run.SourceID, &run.Dataset, &run.IndicatorID,
			&run.StartedAt, &run.FinishedAt, &run.Seconds,
			&run.RecordsIn, &run.RecordsOut, &run.BytesWritten,
			&run.ErrorMessage, &run.Detail, &run.PipelineVersion, &run.ParserVersion,
			&run.DryRun, &run.Command, &run.Host,
		); err != nil {
			internalError(w, s.log, "scan run", err)
			return
		}
		results = append(results, run)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read runs", err)
		return
	}

	writeData(w, results, &Meta{
		Total:   total,
		Limit:   limit,
		Offset:  offset,
		HasMore: int64(offset+len(results)) < total,
		Layer:   "silver",
	})
}

// nonEmpty turns an optional single value into the list inClause takes.
func nonEmpty(value string) []string {
	if value == "" {
		return nil
	}
	return []string{value}
}
