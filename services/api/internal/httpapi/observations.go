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
	// The row of a table broken down by neither place nor commodity — an age
	// group, a sector — as the publisher labels it. Without it, BPS's table of
	// Indonesian speakers is sixteen identical-looking figures for one year.
	Category  *string `json:"category"`
	SourceID  string  `json:"source_id"`
	SourceURL *string `json:"source_url,omitempty"`
}

// The expressions behind the dimension column, which is a name and not an
// identifier: ordering thirty-four provinces by `ID-11`, `ID-12` puts them in an
// order nobody reading "Aceh, Bali, Banten" can predict.
const (
	categoryOrder       = "try_cast(regexp_extract(facet, '^[0-9]+') AS INTEGER) ASC NULLS LAST, facet ASC"
	memberGeoExpr       = "coalesce(g.name, o.geo_name_raw)"
	memberCommodityExpr = "coalesce(c.canonical_name, o.commodity_name_raw)"
)

// observationSorts maps a caller's `order` to the columns that define it.
//
// Every sort ends in the observation id, so that no two rows compare equal on
// every column. A sort that is not total has no stable page boundary: two rows
// alike on period and place can come back in either order, which returns one of
// them twice and skips the other when the page is taken by cursor.
var observationSorts = map[string][]observationSortKey{
	// The category after the place, so a table of age groups reads by group
	// within a year rather than in the order of its figures' hashes.
	"period":  {{"o.period", false}, {"o.geo_id", false}, {"o.category", false}, {"o.observation_id", false}},
	"-period": {{"o.period", true}, {"o.geo_id", false}, {"o.category", false}, {"o.observation_id", false}},
	"value":   {{"o.value", false}, {"o.observation_id", false}},
	"-value":  {{"o.value", true}, {"o.observation_id", false}},
	"geo":     {{"o.geo_id", false}, {"o.period", false}, {"o.observation_id", false}},
	// The dimension columns, by the name the reader sees. Period comes last in
	// both so one member's rows stay a series rather than a shuffle, and the
	// other dimension comes second so a table sorted by place still groups a
	// place's commodities together.
	"place": {
		{memberGeoExpr, false}, {memberCommodityExpr, false},
		{"o.period", false}, {"o.observation_id", false},
	},
	"-place": {
		{memberGeoExpr, true}, {memberCommodityExpr, false},
		{"o.period", false}, {"o.observation_id", false},
	},
	"commodity": {
		{memberCommodityExpr, false}, {memberGeoExpr, false},
		{"o.period", false}, {"o.observation_id", false},
	},
	"-commodity": {
		{memberCommodityExpr, true}, {memberGeoExpr, false},
		{"o.period", false}, {"o.observation_id", false},
	},
}

// observationOrder is the same set as SQL, which is what `sortOrder` hands back.
//
// Nulls last on every column, stated rather than left to the engine's default:
// the cursor's arithmetic depends on where they sort, and a default that
// changed under it would page past rows nobody saw.
var observationOrder = func() map[string]string {
	clauses := make(map[string]string, len(observationSorts))
	for name, keys := range observationSorts {
		parts := make([]string, 0, len(keys))
		for _, key := range keys {
			direction := " ASC"
			if key.desc {
				direction = " DESC"
			}
			parts = append(parts, key.expr+direction+" NULLS LAST")
		}
		clauses[name] = strings.Join(parts, ", ")
	}
	return clauses
}()

// observationParams reads the filter a caller asked for and nothing else.
//
// Separate from paging and ordering because the list and the facet counts
// narrow identically and differ only in what they do with the rows: a facet
// offered under one filter and counted under another is a number nobody can
// act on.
func observationParams(r *http.Request) (observationFilter, error) {
	var f observationFilter
	var err error

	if f.Indicators, err = stringListParam(r, "indicator", identifierPattern); err != nil {
		return f, err
	}
	// Under a collection's route, the series are the collection's, already
	// narrowed by `indicator` and not held to MaxFilterValues: a collection
	// may file more series than one query string may name.
	if scope, ok := indicatorScope(r.Context()); ok {
		f.Indicators = scope
	}
	if f.Geos, err = stringListParam(r, "geo", identifierPattern); err != nil {
		return f, err
	}
	// Places by the name they were published under, beside `geo`'s identifier.
	// Seventeen of Bank Indonesia's eighteen survey cities are not in the
	// geography registry and so have no identifier at all; a caller narrowing
	// to one of them has the printed name and nothing else.
	if f.GeoNames, err = stringListParam(r, "geo_name", searchPattern); err != nil {
		return f, err
	}
	if f.GeoTypes, err = stringListParam(r, "geo_type", identifierPattern); err != nil {
		return f, err
	}
	// Matched on the printed name rather than the identifier: most commodities
	// are not in the registry yet, so their identifier is null and the name the
	// source printed is all that distinguishes them. `searchPattern` because
	// those names carry spaces — `Cabai Merah Keriting`.
	if f.Commodities, err = stringListParam(r, "commodity", searchPattern); err != nil {
		return f, err
	}
	// As printed, like the commodity: categories have no registry. The
	// search pattern because they carry spaces and punctuation — `75+`,
	// `Tidak Mampu`.
	if f.Categories, err = stringListParam(r, "category", searchPattern); err != nil {
		return f, err
	}
	// Why a figure is absent, as Silver recorded it. A reader asking for the
	// suppressed rows is asking about this column and no other, and filtering
	// it in the browser means filtering whatever page happened to be loaded.
	if f.Statuses, err = stringListParam(r, "status", identifierPattern); err != nil {
		return f, err
	}
	// Calendar years as a set, which is not the range they span: "2019 and
	// 2024" is two years, and `period_start`/`period_end` across them is six.
	if f.Years, err = stringListParam(r, "year", yearPattern); err != nil {
		return f, err
	}
	if f.From, err = stringParam(r, "period_start", periodPattern); err != nil {
		return f, err
	}
	if f.Until, err = stringParam(r, "period_end", periodPattern); err != nil {
		return f, err
	}
	if f.Search, err = stringParam(r, "q", searchPattern); err != nil {
		return f, err
	}
	return f, nil
}

// observationSource is the observations table with both dimension tables
// joined, ready to be filtered.
//
// The joins are LEFT JOINs: an observation whose place could not be resolved is
// still a real figure, and dropping it here would hide the gap that Silver went
// to the trouble of recording. Each name falls back to what the source printed,
// because an unresolved commodity is still the row's identity and blanking it
// would leave the reader unable to tell rice from chilli — and because
// selecting `g.name` alone once reported eighteen survey cities as one unnamed
// place.
func (s *Server) observationSource(ctx context.Context, indicators []string) (string, error) {
	// Narrowed to the series asked for, where any were. The filter still does
	// the filtering; this only spares DuckDB the listing of sixteen hundred
	// partition directories it was about to discard, which is most of what an
	// observations query costs.
	observations, err := s.warehouse.SourceIn(
		storage.LayerSilver, "observations", "indicator_id", indicators,
	)
	if err != nil {
		return "", fmt.Errorf("resolve observations: %w", err)
	}

	from := observations + " o"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "geography") {
		geography, err := s.source(storage.LayerSilver, "geography")
		if err != nil {
			return "", fmt.Errorf("resolve geography: %w", err)
		}
		from += " LEFT JOIN " + geography + " g ON g.geo_id = o.geo_id"
	} else {
		from += " LEFT JOIN (SELECT NULL AS geo_id, NULL AS name, NULL AS geo_type) g ON false"
	}

	if s.warehouse.Exists(ctx, storage.LayerSilver, "commodities") {
		commodities, err := s.source(storage.LayerSilver, "commodities")
		if err != nil {
			return "", fmt.Errorf("resolve commodities: %w", err)
		}
		from += " LEFT JOIN " + commodities + " c ON c.commodity_id = o.commodity_id"
	} else {
		from += " LEFT JOIN (SELECT NULL AS commodity_id, NULL AS canonical_name) c ON false"
	}
	return from, nil
}

func (s *Server) handleObservations(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	filter, err := observationParams(r)
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
	orderKey := r.URL.Query().Get("order")
	if orderKey == "" {
		orderKey = "period"
	}
	keys := observationSorts[orderKey]
	after, err := stringParam(r, "after", cursorPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []Observation{}, &Meta{Limit: limit, Layer: "silver"})
		return
	}

	from_, err := s.observationSource(ctx, filter.Indicators)
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	where, args := observationFilters(filter)

	// Counted under the filters alone. The cursor says where this page starts,
	// not what the series holds, and a total that shrank as the reader paged
	// through it would renumber the pages under them.
	var total int64
	countSQL := "SELECT count(*) FROM " + from_ + where
	if err := s.warehouse.DB().QueryRowContext(ctx, countSQL, args...).Scan(&total); err != nil {
		internalError(w, s.log, "count observations", err)
		return
	}

	// A cursor names the last row of the page before this one, so the engine
	// can seek to it instead of producing and discarding everything ahead of
	// it. Where there is no cursor the offset stands: numbered pages and a jump
	// to the end are worth more than the milliseconds, and a jump has no
	// boundary row to name.
	pageWhere, pageArgs := where, append([]any{}, args...)
	if after != "" {
		if len(keys) == 0 || !cursorable(keys) {
			badRequest(w, "invalid parameter", "after: this order cannot be paged by cursor")
			return
		}
		values, err := decodeCursor(after, keys)
		if err != nil {
			badRequest(w, "invalid parameter", "after: "+err.Error())
			return
		}
		clause, bound := keysetAfter(keys, values)
		if pageWhere == "" {
			// `keysetAfter` writes a conjunct, which needs a clause to join.
			pageWhere = " WHERE true"
		}
		pageWhere += clause
		pageArgs = append(pageArgs, bound...)
		offset = 0
	}

	// One row more than asked for, which is how the response knows whether
	// another page exists without counting the rows after this one.
	querySQL := fmt.Sprintf(`
		SELECT o.observation_id, o.indicator_id, o.period, o.period_start, o.period_end,
		       o.temporal_resolution, CAST(o.value AS VARCHAR), o.unit, o.status,
		       o.value_unambiguous, o.geo_id, coalesce(g.name, o.geo_name_raw), g.geo_type,
		       o.commodity_id, coalesce(c.canonical_name, o.commodity_name_raw),
		       o.category, o.source_id, o.source_url
		FROM %s%s ORDER BY %s LIMIT ? OFFSET ?`, from_, pageWhere, order)

	rows, err := s.warehouse.DB().QueryContext(ctx, querySQL, append(pageArgs, limit+1, offset)...)
	if err != nil {
		internalError(w, s.log, "query observations", err)
		return
	}
	defer rows.Close()

	results := make([]Observation, 0, limit+1)
	for rows.Next() {
		var o Observation
		var start, end sql.NullTime
		if err := rows.Scan(
			&o.ObservationID, &o.IndicatorID, &o.Period, &start, &end,
			&o.Resolution, &o.Value, &o.Unit, &o.Status,
			&o.ValueUnambiguous, &o.GeoID, &o.GeoName, &o.GeoType,
			&o.CommodityID, &o.CommodityName,
			&o.Category, &o.SourceID, &o.SourceURL,
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

	hasMore := len(results) > limit
	if hasMore {
		results = results[:limit]
	}

	// The bookmark for the next page, where the sort can carry one. Absent for
	// a sort by value, and absent on the last page, which is how a caller
	// walking the series knows it has reached the end.
	var next string
	if hasMore && len(keys) > 0 && cursorable(keys) && len(results) > 0 {
		next = cursorFor(keys, results[len(results)-1])
	}

	writeData(w, results, &Meta{
		Total:      total,
		Limit:      limit,
		Offset:     offset,
		HasMore:    hasMore,
		NextCursor: next,
		Layer:      "silver",
	})
}

// ObservationFacets is every narrowing control's options for one series,
// counted rather than guessed.
//
// The portal used to read these off the rows it had already fetched, which
// works only for a series that fits in one request. Bank Indonesia's daily food
// prices are 2.6 million rows: the first page of them holds every province and
// one week of 2017, so a Year control built from it offered one year and hid
// the other nine.
type ObservationFacets struct {
	Places      []Facet `json:"places"`
	Commodities []Facet `json:"commodities"`
	Categories  []Facet `json:"categories"`
	Years       []Facet `json:"years"`
	Statuses    []Facet `json:"statuses"`
}

// handleObservationFacets counts each filter's options under the filters
// already applied.
func (s *Server) handleObservationFacets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	filter, err := observationParams(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	facets := ObservationFacets{
		Places:      []Facet{},
		Commodities: []Facet{},
		Categories:  []Facet{},
		Years:       []Facet{},
		Statuses:    []Facet{},
	}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, facets, &Meta{Layer: "silver"})
		return
	}

	from_, err := s.observationSource(ctx, filter.Indicators)
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}
	where, args := observationFilters(filter)

	for _, f := range []struct {
		column string
		into   *[]Facet
		order  string
		limit  int
	}{
		// Largest first, because the default view is the fullest one and a
		// reader narrowing to a place looks for the one with the figures in it.
		// A thousand rather than a few dozen: one of these lists the 514
		// regencies, and a control that quietly stopped at five hundred would
		// leave a reader unable to pick their own.
		{"coalesce(g.name, o.geo_name_raw)", &facets.Places, "n DESC, facet ASC", 1000},
		{"coalesce(c.canonical_name, o.commodity_name_raw)", &facets.Commodities, "n DESC, facet ASC", 1000},
		// By their leading number, then by name: age groups read 5-9, 10-14,
		// 75+ rather than the 10-14, 15-19, 5-9 of plain text order, and a
		// total such as `INDONESIA` follows them. Not by count, which for a
		// cross-section is the same for every row and orders nothing.
		{"o.category", &facets.Categories, categoryOrder, 1000},
		// Years descending by value, not by count: a reader picking a year
		// wants them in order.
		{"CAST(year(o.period_start) AS VARCHAR)", &facets.Years, "facet DESC", 200},
		{"o.status", &facets.Statuses, "n DESC, facet ASC", 20},
	} {
		// Counted under the filters that are on, like the documents page: an
		// option that would return no rows is an option not worth offering.
		// Nulls are excluded because "no commodity recorded" is not a commodity
		// anyone can filter by — a series that varies along one dimension only
		// therefore reports an empty list for the other, which is what tells
		// the portal which controls to show.
		filters := where
		if filters == "" {
			filters = " WHERE " + f.column + " IS NOT NULL"
		} else {
			filters += " AND " + f.column + " IS NOT NULL"
		}

		// Aliased `facet` rather than `value`, and grouped by position:
		// observations carry a column called `value`, and `GROUP BY value`
		// binds to that column rather than to the alias — which is a figure
		// per group and not a facet.
		rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
			"SELECT %s AS facet, count(*) AS n FROM %s%s GROUP BY 1 "+
				"ORDER BY %s LIMIT %d",
			f.column, from_, filters, f.order, f.limit,
		), args...)
		if err != nil {
			internalError(w, s.log, "query observation facets", err)
			return
		}
		values := make([]Facet, 0, f.limit)
		for rows.Next() {
			var facet Facet
			if err := rows.Scan(&facet.Value, &facet.Count); err != nil {
				rows.Close()
				internalError(w, s.log, "scan observation facet", err)
				return
			}
			values = append(values, facet)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			internalError(w, s.log, "read observation facets", err)
			return
		}
		*f.into = values
	}

	writeData(w, facets, &Meta{Layer: "silver"})
}

// observationFilter is what a caller asked to narrow the result to. A struct
// rather than a positional list: the fields are all strings and string slices,
// and at seven of them a transposed pair is a silent wrong answer rather than a
// compile error.
type observationFilter struct {
	Indicators  []string
	Geos        []string
	GeoNames    []string
	GeoTypes    []string
	Commodities []string
	Categories  []string
	Statuses    []string
	Years       []string
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
	addIn("coalesce(g.name, o.geo_name_raw)", f.GeoNames)
	addIn("g.geo_type", f.GeoTypes)
	addIn("coalesce(c.canonical_name, o.commodity_name_raw)", f.Commodities)
	addIn("o.category", f.Categories)
	addIn("o.status", f.Statuses)
	// The year a figure falls in, off the bounded start date rather than the
	// label: the label's shape depends on the resolution — `2011`, `2012-01`,
	// `2026-Q1` — and only the start date means the same thing across all of
	// them.
	addIn("CAST(year(o.period_start) AS VARCHAR)", f.Years)
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
			"OR o.geo_id ILIKE ? OR o.indicator_id ILIKE ? OR o.period ILIKE ? "+
			"OR coalesce(c.canonical_name, o.commodity_name_raw) ILIKE ? OR o.category ILIKE ?)")
		pattern := "%" + f.Search + "%"
		args = append(args, pattern, pattern, pattern, pattern, pattern, pattern)
	}

	if len(clauses) == 0 {
		return "", nil
	}
	return " WHERE " + strings.Join(clauses, " AND "), args
}

func (s *Server) source(layer storage.Layer, dataset string) (string, error) {
	return s.warehouse.Source(layer, dataset)
}
