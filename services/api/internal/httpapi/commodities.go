package httpapi

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Commodity is one member of the commodity dimension (program.md §12).
//
// Derived from the observations rather than read from the registry, for the
// same reason the indicator list is: most commodities here are not in the
// registry at all — Bank Indonesia prints `Cabai Merah Keriting` and nothing
// resolves it — so a page built from the registry alone would be empty while
// the warehouse holds a hundred thousand price figures. What the registry does
// know is joined on; what it does not is still a commodity, named as the
// source named it.
//
// CommodityID is therefore nullable and the name is the identity: it is the
// grain the figures are grouped at, and what the observations filter matches.
type Commodity struct {
	// What the registry calls it, or what the source printed. Never empty —
	// rows naming no commodity are not commodities and are excluded.
	Name        string  `json:"name"`
	CommodityID *string `json:"commodity_id"`
	// Registry metadata, absent for a commodity nothing has resolved yet.
	Category    *string `json:"category,omitempty"`
	Subcategory *string `json:"subcategory,omitempty"`
	HSCode      *string `json:"hs_code,omitempty"`
	// What the figures are measured in, read off the figures rather than off
	// the registry: Yahoo quotes coffee in US cents and gold in dollars, and
	// printing the registry's `USc/lb` over a column of `USX` would describe
	// the commodity correctly and the data wrongly. Several where a commodity
	// is published both as a price and as a tonnage, which is worth seeing
	// rather than resolving to whichever one sorted first. Falls back to the
	// registry's default unit only where the figures carry none.
	Units []string `json:"units"`
	// What rests on it. Counted over the rows this request's filters left, so
	// a commodity narrowed to one source reports that source's figures.
	Observations int64 `json:"observations"`
	Indicators   int64 `json:"indicators"`
	// Zero for most: a commodity series names a date and a commodity and no
	// place at all, which is why this dimension exists separately.
	Geographies int64    `json:"geographies"`
	PeriodStart string   `json:"period_start"`
	PeriodEnd   string   `json:"period_end"`
	Sources     []string `json:"sources"`
	// When the pipeline last wrote these rows, not how recent the figures are.
	LastUpdated *string `json:"last_updated,omitempty"`
}

// The name is the grain, so it is what every clause below groups and filters
// on. Spelled once rather than at each of its five uses.
const commodityName = "coalesce(c.canonical_name, o.commodity_name_raw)"

var commodityOrder = map[string]string{
	"name":          "name ASC",
	"-name":         "name DESC",
	"observations":  "observations ASC, name ASC",
	"-observations": "observations DESC, name ASC",
	"period":        "period_start ASC, name ASC",
	"-period":       "period_end DESC, name ASC",
}

func (s *Server) handleCommodities(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	indicators, err := stringListParam(r, "indicator", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	sources, err := stringListParam(r, "source", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	// `searchPattern` rather than the identifier one: a category is a readable
	// label — "Food & beverages" — not one of our codes.
	categories, err := stringListParam(r, "category", searchPattern)
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
	order, err := sortOrder(r, commodityOrder, "name")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	// An empty lake answers "no commodities" rather than raising: the same
	// answer the observations endpoint gives, and the honest one before a
	// pipeline has ever run.
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []Commodity{}, &Meta{Limit: limit, Layer: "silver"})
		return
	}

	observations, err := s.warehouse.SourceIn(
		storage.LayerSilver, "observations", "indicator_id", indicators,
	)
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	// A LEFT JOIN onto a registry that may not be published at all. An
	// unresolved commodity is still a commodity, and an inner join here would
	// empty the page in every warehouse that has no commodities.csv.
	from := observations + " o"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "commodities") {
		registry, err := s.source(storage.LayerSilver, "commodities")
		if err != nil {
			internalError(w, s.log, "resolve commodities", err)
			return
		}
		from += " LEFT JOIN " + registry + " c ON c.commodity_id = o.commodity_id"
	} else {
		// Cast, so the columns bind as text rather than as DuckDB's default
		// integer for an untyped NULL — the scan reads them into strings.
		from += " LEFT JOIN (SELECT CAST(NULL AS VARCHAR) AS commodity_id, " +
			"CAST(NULL AS VARCHAR) AS canonical_name, " +
			"CAST(NULL AS VARCHAR) AS category, " +
			"CAST(NULL AS VARCHAR) AS subcategory, " +
			"CAST(NULL AS VARCHAR) AS hs_code, " +
			"CAST(NULL AS VARCHAR) AS unit_default) c ON false"
	}

	// Rows naming no commodity are most of the warehouse — every series that
	// varies by place instead — and they are not an unnamed commodity.
	clauses := []string{commodityName + " IS NOT NULL"}
	args := []any{}

	addIn := func(column string, values []string) {
		clause, bound := inClause(column, values)
		if clause == "" {
			return
		}
		clauses = append(clauses, clause)
		args = append(args, bound...)
	}
	addIn("o.indicator_id", indicators)
	addIn("o.source_id", sources)
	addIn("c.category", categories)

	if search != "" {
		// The name is what a reader types; the identifier and the HS code are
		// what a dataset refers to the commodity by.
		clauses = append(clauses, "("+commodityName+" ILIKE ? "+
			"OR coalesce(o.commodity_id, '') ILIKE ? "+
			"OR coalesce(c.hs_code, '') ILIKE ?)")
		pattern := "%" + search + "%"
		args = append(args, pattern, pattern, pattern)
	}

	where := " WHERE " + strings.Join(clauses, " AND ")

	// Distinct names rather than rows: the page counts commodities, and the
	// pagination below is over commodities too.
	var total int64
	if err := s.warehouse.DB().QueryRowContext(ctx, fmt.Sprintf(
		"SELECT count(DISTINCT %s) FROM %s%s", commodityName, from, where,
	), args...).Scan(&total); err != nil {
		internalError(w, s.log, "count commodities", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT %s AS name,
		       any_value(o.commodity_id),
		       any_value(c.category),
		       any_value(c.subcategory),
		       any_value(c.hs_code),
		       CASE WHEN len(list(DISTINCT o.unit)) > 0
		            THEN list_sort(list(DISTINCT o.unit))
		            ELSE [any_value(c.unit_default)] END,
		       count(*) AS observations,
		       count(DISTINCT o.indicator_id),
		       count(DISTINCT o.geo_id),
		       min(o.period) AS period_start,
		       max(o.period) AS period_end,
		       list_sort(list(DISTINCT o.source_id)),
		       strftime(max(o.processed_at), '%%Y-%%m-%%dT%%H:%%M:%%SZ')
		FROM %s%s
		GROUP BY 1
		ORDER BY %s
		LIMIT ? OFFSET ?`, commodityName, from, where, order),
		append(args, limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query commodities", err)
		return
	}
	defer rows.Close()

	results := make([]Commodity, 0, limit)
	for rows.Next() {
		var commodity Commodity
		var units, sourceIDs any
		if err := rows.Scan(
			&commodity.Name, &commodity.CommodityID, &commodity.Category,
			&commodity.Subcategory, &commodity.HSCode, &units,
			&commodity.Observations, &commodity.Indicators, &commodity.Geographies,
			&commodity.PeriodStart, &commodity.PeriodEnd, &sourceIDs,
			&commodity.LastUpdated,
		); err != nil {
			internalError(w, s.log, "scan commodity", err)
			return
		}
		// Never null in the response: a consumer listing units should get an
		// empty list from a commodity whose figures carry none, not a null it
		// has to guard every access with.
		commodity.Units = asStrings(units)
		if commodity.Units == nil {
			commodity.Units = []string{}
		}
		commodity.Sources = asStrings(sourceIDs)
		if commodity.Sources == nil {
			commodity.Sources = []string{}
		}
		results = append(results, commodity)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read commodities", err)
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
