package httpapi

import (
	"fmt"
	"net/http"

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
	Sources      int64   `json:"sources"`
}

func (s *Server) handleIndicators(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []Indicator{}, &Meta{Layer: "silver"})
		return
	}

	observations, err := s.source(storage.LayerSilver, "observations")
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT indicator_id,
		       any_value(temporal_resolution),
		       any_value(unit),
		       count(*),
		       count(DISTINCT geo_id),
		       min(period),
		       max(period),
		       count(DISTINCT source_id)
		FROM %s
		GROUP BY indicator_id
		ORDER BY indicator_id`, observations))
	if err != nil {
		internalError(w, s.log, "query indicators", err)
		return
	}
	defer rows.Close()

	indicators := make([]Indicator, 0, 16)
	for rows.Next() {
		var i Indicator
		if err := rows.Scan(
			&i.IndicatorID, &i.Resolution, &i.Unit, &i.Observations,
			&i.Geographies, &i.PeriodStart, &i.PeriodEnd, &i.Sources,
		); err != nil {
			internalError(w, s.log, "scan indicator", err)
			return
		}
		indicators = append(indicators, i)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read indicators", err)
		return
	}

	writeData(w, indicators, &Meta{Total: int64(len(indicators)), Layer: "silver"})
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

	geoType, err := stringParam(r, "geo_type", identifierPattern)
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

	where, args := "", []any{}
	if geoType != "" {
		where = " WHERE geo_type = ?"
		args = append(args, geoType)
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
