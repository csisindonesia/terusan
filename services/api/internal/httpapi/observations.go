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
	// Not every series varies by place. Bank Indonesia's food prices are a
	// commodity by a date and name no place at all, and a reader given only
	// the geography columns sees thirty-one identical rows.
	CommodityID   *string `json:"commodity_id"`
	CommodityName *string `json:"commodity_name,omitempty"`
	SourceID      string  `json:"source_id"`
	SourceURL     *string `json:"source_url,omitempty"`
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

	indicators, err := stringListParam(r, "indicator", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	geos, err := stringListParam(r, "geo", identifierPattern)
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
	geoTypes, err := stringListParam(r, "geo_type", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	// Matched on the printed name rather than the identifier: most commodities
	// are not in the registry yet, so their identifier is null and the name the
	// source printed is all that distinguishes them. `searchPattern` because
	// those names carry spaces — `Cabai Merah Keriting`.
	commodities, err := stringListParam(r, "commodity", searchPattern)
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
	order, err := sortOrder(r, observationOrder, "period")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []Observation{}, &Meta{Limit: limit, Layer: "silver"})
		return
	}

	// Narrowed to the series asked for, where any were. The filter below is
	// unchanged and still does the filtering; this only spares DuckDB the
	// listing of sixteen hundred partition directories it was about to
	// discard, which is most of what an observations query costs.
	observations, err := s.warehouse.SourceIn(
		storage.LayerSilver, "observations", "indicator_id", indicators,
	)
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	// The geography join is a LEFT JOIN: an observation whose place could not be
	// resolved is still a real figure, and dropping it here would hide the gap
	// that Silver went to the trouble of recording.
	//
	// The name falls back to what the source printed for the same reason the
	// commodity name does. Seventeen of Bank Indonesia's eighteen survey cities
	// are not in the geography registry — it holds provinces — and selecting
	// `g.name` alone reported eighteen cities as one unnamed place.
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

	// Same treatment for the commodity dimension. The canonical name is used
	// where the registry knows the commodity and the name the source printed
	// where it does not — an unresolved commodity is still the row's identity,
	// and blanking it would leave the reader unable to tell rice from chilli.
	if s.warehouse.Exists(ctx, storage.LayerSilver, "commodities") {
		commodityDim, err := s.source(storage.LayerSilver, "commodities")
		if err != nil {
			internalError(w, s.log, "resolve commodities", err)
			return
		}
		from_ += " LEFT JOIN " + commodityDim + " c ON c.commodity_id = o.commodity_id"
	} else {
		from_ += " LEFT JOIN (SELECT NULL AS commodity_id, NULL AS canonical_name) c ON false"
	}

	where, args := observationFilters(observationFilter{
		Indicators:  indicators,
		Geos:        geos,
		GeoTypes:    geoTypes,
		Commodities: commodities,
		From:        from,
		Until:       until,
		Search:      search,
	})

	var total int64
	countSQL := "SELECT count(*) FROM " + from_ + where
	if err := s.warehouse.DB().QueryRowContext(ctx, countSQL, args...).Scan(&total); err != nil {
		internalError(w, s.log, "count observations", err)
		return
	}

	querySQL := fmt.Sprintf(`
		SELECT o.observation_id, o.indicator_id, o.period, o.period_start, o.period_end,
		       o.temporal_resolution, CAST(o.value AS VARCHAR), o.unit, o.status,
		       o.value_unambiguous, o.geo_id, coalesce(g.name, o.geo_name_raw), g.geo_type,
		       o.commodity_id, coalesce(c.canonical_name, o.commodity_name_raw),
		       o.source_id, o.source_url
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
			&o.CommodityID, &o.CommodityName,
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

// observationFilter is what a caller asked to narrow the result to. A struct
// rather than a positional list: the fields are all strings and string slices,
// and at seven of them a transposed pair is a silent wrong answer rather than a
// compile error.
type observationFilter struct {
	Indicators  []string
	Geos        []string
	GeoTypes    []string
	Commodities []string
	From        string
	Until       string
	Search      string
}

// observationFilters builds the WHERE clause with every value bound.
func observationFilters(f observationFilter) (string, []any) {
	var clauses []string
	var args []any

	add := func(clause string, value any) {
		clauses = append(clauses, clause)
		args = append(args, value)
	}
	addIn := func(column string, values []string) {
		clause, values2 := inClause(column, values)
		if clause == "" {
			return
		}
		clauses = append(clauses, clause)
		args = append(args, values2...)
	}

	addIn("o.indicator_id", f.Indicators)
	addIn("o.geo_id", f.Geos)
	addIn("g.geo_type", f.GeoTypes)
	addIn("coalesce(c.canonical_name, o.commodity_name_raw)", f.Commodities)
	// Compared as canonical labels rather than dates: `2026-Q1` and `2026-01`
	// both sort correctly as text, and a caller filtering on a label should not
	// have to know which resolution the series uses.
	if f.From != "" {
		add("o.period >= ?", f.From)
	}
	if f.Until != "" {
		add("o.period <= ?", f.Until)
	}

	// Free text searches the columns a reader would recognise a row by, rather
	// than every column: matching a hash or a URL fragment returns rows nobody
	// was looking for.
	if f.Search != "" {
		clauses = append(clauses, "(coalesce(g.name, o.geo_name_raw) ILIKE ? "+
			"OR o.geo_id ILIKE ? OR o.indicator_id ILIKE ? "+
			"OR coalesce(c.canonical_name, o.commodity_name_raw) ILIKE ?)")
		pattern := "%" + f.Search + "%"
		args = append(args, pattern, pattern, pattern, pattern)
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
