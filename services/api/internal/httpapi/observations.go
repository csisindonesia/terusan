package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Observation is one statistical figure (program.md §10).
//
// Value is a string, not a float. Silver stores decimal128 precisely because
// published statistics are decimal quantities; serialising through a JSON
// number would hand every consumer a float64 and reintroduce exactly the drift
// the decimal storage exists to avoid. A consumer that wants a float can ask
// for one; one that wants the exact figure can still have it.
type Observation struct {
	ObservationID string  `json:"observation_id"`
	IndicatorID   string  `json:"indicator_id"`
	Period        string  `json:"period"`
	PeriodStart   string  `json:"period_start"`
	PeriodEnd     string  `json:"period_end"`
	Resolution    string  `json:"temporal_resolution"`
	Value         *string `json:"value"`
	Unit          *string `json:"unit,omitempty"`
	// Why a value is absent: missing, suppressed, not_applicable, provisional.
	// A null with no status cannot tell "not collected" from "collected and
	// zero", and they are different facts.
	Status string `json:"status"`
	// False where the figure was read under an assumption that could have gone
	// the other way — see the number parsing in normalize.values.
	ValueUnambiguous bool    `json:"value_unambiguous"`
	GeoID            *string `json:"geo_id"`
	GeoName          *string `json:"geo_name,omitempty"`
	GeoType          *string `json:"geo_type,omitempty"`
	SourceID         string  `json:"source_id"`
	SourceURL        *string `json:"source_url,omitempty"`
}

var observationOrder = map[string]string{
	"period":  "o.period ASC, o.geo_id ASC",
	"-period": "o.period DESC, o.geo_id ASC",
	"value":   "o.value ASC NULLS LAST",
	"-value":  "o.value DESC NULLS LAST",
	"geo":     "o.geo_id ASC, o.period ASC",
}

func (s *Server) handleObservations(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	indicator, err := stringParam(r, "indicator", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	geo, err := stringParam(r, "geo", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	from, err := stringParam(r, "period_start", periodPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	until, err := stringParam(r, "period_end", periodPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
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
	order, err := sortOrder(r, observationOrder, "period")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []Observation{}, &Meta{Limit: limit, Layer: "silver"})
		return
	}

	observations, err := s.source(storage.LayerSilver, "observations")
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	// The geography join is a LEFT JOIN: an observation whose place could not be
	// resolved is still a real figure, and dropping it here would hide the gap
	// that Silver went to the trouble of recording.
	from_ := observations + " o"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "geography") {
		geography, err := s.source(storage.LayerSilver, "geography")
		if err != nil {
			internalError(w, s.log, "resolve geography", err)
			return
		}
		from_ += " LEFT JOIN " + geography + " g ON g.geo_id = o.geo_id"
	} else {
		from_ += " LEFT JOIN (SELECT NULL AS geo_id, NULL AS name, NULL AS geo_type) g ON false"
	}

	where, args := observationFilters(indicator, geo, geoType, from, until)

	var total int64
	countSQL := "SELECT count(*) FROM " + from_ + where
	if err := s.warehouse.DB().QueryRowContext(ctx, countSQL, args...).Scan(&total); err != nil {
		internalError(w, s.log, "count observations", err)
		return
	}

	querySQL := fmt.Sprintf(`
		SELECT o.observation_id, o.indicator_id, o.period, o.period_start, o.period_end,
		       o.temporal_resolution, CAST(o.value AS VARCHAR), o.unit, o.status,
		       o.value_unambiguous, o.geo_id, g.name, g.geo_type, o.source_id, o.source_url
		FROM %s%s ORDER BY %s LIMIT ? OFFSET ?`, from_, where, order)

	rows, err := s.warehouse.DB().QueryContext(ctx, querySQL, append(args, limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query observations", err)
		return
	}
	defer rows.Close()

	results := make([]Observation, 0, limit)
	for rows.Next() {
		var o Observation
		var start, end sql.NullTime
		if err := rows.Scan(
			&o.ObservationID, &o.IndicatorID, &o.Period, &start, &end,
			&o.Resolution, &o.Value, &o.Unit, &o.Status,
			&o.ValueUnambiguous, &o.GeoID, &o.GeoName, &o.GeoType,
			&o.SourceID, &o.SourceURL,
		); err != nil {
			internalError(w, s.log, "scan observation", err)
			return
		}
		if start.Valid {
			o.PeriodStart = start.Time.Format("2006-01-02")
		}
		if end.Valid {
			o.PeriodEnd = end.Time.Format("2006-01-02")
		}
		results = append(results, o)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read observations", err)
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

// observationFilters builds the WHERE clause with every value bound.
func observationFilters(indicator, geo, geoType, from, until string) (string, []any) {
	var clauses []string
	var args []any

	add := func(clause string, value any) {
		clauses = append(clauses, clause)
		args = append(args, value)
	}

	if indicator != "" {
		add("o.indicator_id = ?", indicator)
	}
	if geo != "" {
		add("o.geo_id = ?", geo)
	}
	if geoType != "" {
		add("g.geo_type = ?", geoType)
	}
	// Compared as canonical labels rather than dates: `2026-Q1` and `2026-01`
	// both sort correctly as text, and a caller filtering on a label should not
	// have to know which resolution the series uses.
	if from != "" {
		add("o.period >= ?", from)
	}
	if until != "" {
		add("o.period <= ?", until)
	}

	if len(clauses) == 0 {
		return "", nil
	}
	return " WHERE " + strings.Join(clauses, " AND "), args
}

func (s *Server) source(layer storage.Layer, dataset string) (string, error) {
	return s.warehouse.Source(layer, dataset)
}

var _ = context.Background
