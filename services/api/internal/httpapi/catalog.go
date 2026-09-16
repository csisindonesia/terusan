package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Indicator summarises one series (program.md §10).
//
// Derived from the observations rather than read from an indicators table:
// there is no point advertising an indicator with nothing behind it, and the
// coverage range is the first thing anyone asks about a series.
type Indicator struct {
	IndicatorID  string  `json:"indicator_id"`
	Resolution   string  `json:"temporal_resolution"`
	Unit         *string `json:"unit,omitempty"`
	Observations int64   `json:"observations"`
	Geographies  int64   `json:"geographies"`
	PeriodStart  string  `json:"period_start"`
	PeriodEnd    string  `json:"period_end"`
	// The sources behind the series, named rather than counted: "bps" tells a
	// reader where a figure came from, "1" tells them nothing.
	Sources []string `json:"sources"`
	// When the pipeline last wrote these rows. Distinct from `period_end`,
	// which is how recent the *figures* are — a series can cover 2025 and have
	// been refreshed this morning, or last year.
	LastUpdated *string `json:"last_updated,omitempty"`
}

// handleIndicator returns one series.
//
// A separate route rather than a filter on the list, because a detail page asks
// a different question — this one thing, in full — and a 404 is the honest
// answer to an identifier that names nothing.
func (s *Server) handleIndicator(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id := r.PathValue("id")
	if !identifierPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed identifier")
		return
	}

	indicators, err := s.indicatorRows(ctx, w, id)
	if err != nil {
		return // already reported
	}
	if len(indicators) == 0 {
		notFound(w, "no such indicator", id)
		return
	}
	writeData(w, indicators[0], &Meta{Layer: "silver"})
}

func (s *Server) handleIndicators(w http.ResponseWriter, r *http.Request) {
	indicators, err := s.indicatorRows(r.Context(), w, "")
	if err != nil {
		return // already reported
	}
	writeData(w, indicators, &Meta{Total: int64(len(indicators)), Layer: "silver"})
}

// indicatorRows summarises the series, or one of them when `only` is set.
//
// Derived from the observations rather than read from an indicators table:
// there is no point advertising an indicator with nothing behind it, and the
// coverage range is the first thing anyone asks about a series.
func (s *Server) indicatorRows(
	ctx context.Context, w http.ResponseWriter, only string,
) ([]Indicator, error) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return nil, nil
	}

	observations, err := s.source(storage.LayerSilver, "observations")
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return nil, err
	}

	where, args := "", []any{}
	if only != "" {
		where = " WHERE indicator_id = ?"
		args = append(args, only)
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT indicator_id,
		       any_value(temporal_resolution),
		       any_value(unit),
		       count(*),
		       count(DISTINCT geo_id),
		       min(period),
		       max(period),
		       list_sort(list(DISTINCT source_id)),
		       strftime(max(processed_at), '%%Y-%%m-%%dT%%H:%%M:%%SZ')
		FROM %s%s
		GROUP BY indicator_id
		ORDER BY indicator_id`, observations, where), args...)
	if err != nil {
		internalError(w, s.log, "query indicators", err)
		return nil, err
	}
	defer rows.Close()

	indicators := make([]Indicator, 0, 16)
	for rows.Next() {
		var i Indicator
		var sources any
		if err := rows.Scan(
			&i.IndicatorID, &i.Resolution, &i.Unit, &i.Observations,
			&i.Geographies, &i.PeriodStart, &i.PeriodEnd, &sources, &i.LastUpdated,
		); err != nil {
			internalError(w, s.log, "scan indicator", err)
			return nil, err
		}
		i.Sources = asStrings(sources)
		indicators = append(indicators, i)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read indicators", err)
		return nil, err
	}

	return indicators, nil
}

// Geography is one member of the geography dimension (program.md §11).
type Geography struct {
	GeoID     string  `json:"geo_id"`
	Name      string  `json:"name"`
	GeoType   string  `json:"geo_type"`
	ParentID  *string `json:"parent_geo_id,omitempty"`
	BPSCode   *string `json:"bps_code,omitempty"`
	ISOCode   *string `json:"iso_code,omitempty"`
	ValidFrom *string `json:"valid_from,omitempty"`
}

func (s *Server) handleGeography(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	geoTypes, err := stringListParam(r, "geo_type", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	search, err := stringParam(r, "q", searchPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "geography") {
		notFound(w, "the geography dimension has not been published",
			"run `terusan silver dimensions`")
		return
	}

	geography, err := s.source(storage.LayerSilver, "geography")
	if err != nil {
		internalError(w, s.log, "resolve geography", err)
		return
	}

	var clauses []string
	args := []any{}

	if clause, values := inClause("geo_type", geoTypes); clause != "" {
		clauses = append(clauses, clause)
		args = append(args, values...)
	}
	if search != "" {
		// Name and identifier are what a reader recognises a place by; the
		// codes are what a dataset refers to it by. Both are worth matching.
		clauses = append(clauses,
			"(name ILIKE ? OR geo_id ILIKE ? OR coalesce(bps_code, '') ILIKE ?)")
		pattern := "%" + search + "%"
		args = append(args, pattern, pattern, pattern)
	}

	where := ""
	if len(clauses) > 0 {
		where = " WHERE " + strings.Join(clauses, " AND ")
	}

	var total int64
	if err := s.warehouse.DB().QueryRowContext(
		ctx, "SELECT count(*) FROM "+geography+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count geography", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT geo_id, name, geo_type, parent_geo_id, bps_code, iso_code,
		       CAST(valid_from AS VARCHAR)
		FROM %s%s ORDER BY geo_type, geo_id LIMIT ? OFFSET ?`, geography, where),
		append(args, limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query geography", err)
		return
	}
	defer rows.Close()

	results := make([]Geography, 0, limit)
	for rows.Next() {
		var g Geography
		if err := rows.Scan(
			&g.GeoID, &g.Name, &g.GeoType, &g.ParentID,
			&g.BPSCode, &g.ISOCode, &g.ValidFrom,
		); err != nil {
			internalError(w, s.log, "scan geography", err)
			return
		}
		results = append(results, g)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read geography", err)
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

// Dataset is one queryable table in the lake (program.md §19).
type Dataset struct {
	Slug  string `json:"slug"`
	Layer string `json:"layer"`
	Name  string `json:"name"`
	Rows  int64  `json:"rows"`
}

// handleDatasets lists what is actually in the lake.
//
// Read from storage rather than from the PostgreSQL catalog, so the endpoint
// answers on a deployment with no database — and so it cannot disagree with
// what is on disk, which a catalog can.
func (s *Server) handleDatasets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	known := []struct {
		layer   storage.Layer
		dataset string
		name    string
	}{
		{storage.LayerSilver, "observations", "Observations"},
		{storage.LayerSilver, "geography", "Geography dimension"},
		{storage.LayerSilver, "commodities", "Commodity dimension"},
		{storage.LayerBronze, "records", "Bronze records"},
		{storage.LayerBronze, "documents", "Bronze documents"},
	}

	datasets := make([]Dataset, 0, len(known))
	for _, entry := range known {
		if !s.warehouse.Exists(ctx, entry.layer, entry.dataset) {
			continue
		}
		expression, err := s.source(entry.layer, entry.dataset)
		if err != nil {
			continue
		}
		var rows int64
		if err := s.warehouse.DB().QueryRowContext(
			ctx, "SELECT count(*) FROM "+expression,
		).Scan(&rows); err != nil {
			s.log.Warn("datasets.count_failed", "dataset", entry.dataset, "error", err)
			continue
		}
		datasets = append(datasets, Dataset{
			Slug:  fmt.Sprintf("%s-%s", entry.layer, entry.dataset),
			Layer: entry.layer.String(),
			Name:  entry.name,
			Rows:  rows,
		})
	}

	writeData(w, datasets, &Meta{Total: int64(len(datasets))})
}

// asStrings reads a DuckDB list column into a slice.
//
// The driver hands a LIST back as []any of driver values, so each entry is
// converted rather than asserted: a nil element is a source that was never
// recorded, which is a gap in the data rather than a reason to fail the
// request.
func asStrings(value any) []string {
	items, ok := value.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if text, ok := item.(string); ok && text != "" {
			out = append(out, text)
		}
	}
	return out
}
