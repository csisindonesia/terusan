package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Indicator summarises one series (program.md §10).
//
// Derived from the observations rather than read from an indicators table:
// there is no point advertising an indicator with nothing behind it, and the
// coverage range is the first thing anyone asks about a series.
type Indicator struct {
	IndicatorID string `json:"indicator_id"`
	// What the series is called, from the Silver indicators table, and the
	// publisher's own identifier for it. Both are absent until a source
	// publishes them — most series here carry a readable identifier and need
	// neither, but a bulk source cannot: FRED's identifiers are eight-character
	// codes, and without the name there is nothing to print but the code.
	Name *string `json:"name,omitempty"`
	Code *string `json:"code,omitempty"`
	// The readable key the series was declared under. The identifier is a
	// derived code, so this is what a reader recognises and what a search
	// matches; it is deliberately not what the URL carries (program.md §10).
	Slug *string `json:"slug,omitempty"`
	// What the series counts, in the publisher's own words, and who publishes
	// it: FRED redistributes the Treasury's and the OECD's figures, so the
	// source we collected from is not who produced them.
	Description  *string `json:"description,omitempty"`
	Publisher    *string `json:"publisher,omitempty"`
	Release      *string `json:"release,omitempty"`
	Resolution   string  `json:"temporal_resolution"`
	Unit         *string `json:"unit,omitempty"`
	Observations int64   `json:"observations"`
	Geographies  int64   `json:"geographies"`
	PeriodStart  string  `json:"period_start"`
	PeriodEnd    string  `json:"period_end"`
	// The sources behind the series, named rather than counted: "bps" tells a
	// reader where a figure came from, "1" tells them nothing.
	Sources []string `json:"sources"`
	// The collection this series belongs to, and the words it is found by.
	// Both come from the indicators table, where they are derived when the
	// series is published rather than typed by anyone.
	DatasetID *string  `json:"dataset_id,omitempty"`
	Tags      []string `json:"tags"`
	// When the pipeline last wrote these rows. Distinct from `period_end`,
	// which is how recent the *figures* are — a series can cover 2025 and have
	// been refreshed this morning, or last year.
	LastUpdated *string `json:"last_updated,omitempty"`
	// Where this is one of a price's four series, all four. On the close of
	// a folded list, and on any of the four when asked for by itself.
	OHLC *OHLC `json:"ohlc,omitempty"`
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

	indicators, err := s.indicatorRows(ctx, w, "WHERE o.indicator_id = ?", []any{id}, id)
	if err != nil {
		return // already reported
	}
	if len(indicators) == 0 {
		notFound(w, "no such indicator", id)
		return
	}
	// The set is found in the catalogue rather than queried: a series' own
	// page asks one partition, and its siblings live in three others. A cold
	// catalogue is not worth waiting on for a dropdown, so it is left out.
	indicator := indicators[0]
	if catalogue := s.heldCatalogue(); catalogue != nil {
		indicator.OHLC = ohlcSets(catalogue.series)[id]
	}
	writeData(w, indicator, &Meta{Layer: "silver"})
}

func (s *Server) handleIndicators(w http.ResponseWriter, r *http.Request) {
	query, asked, err := readIndicatorQuery(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	catalogue, err := s.lakeCatalogue(r.Context())
	if err != nil {
		internalError(w, s.log, "build catalogue", err)
		return
	}
	series := catalogue.series
	if query.fold {
		series = foldOHLC(series)
	}
	if !asked {
		// Every series, for the pages that summarise the whole catalogue.
		writeData(w, series, &Meta{Total: int64(len(series)), Layer: "silver"})
		return
	}
	matched := query.apply(series)
	page := matched[min(offset, len(matched)):min(offset+limit, len(matched))]
	writeData(w, page, &Meta{
		Total: int64(len(matched)), Limit: limit, Offset: offset,
		HasMore: offset+len(page) < len(matched), Layer: "silver",
	})
}

// indicatorRows summarises the series, narrowed by `where` against the
// observations aliased `o` — "" for all of them.
//
// Derived from the observations rather than read from an indicators table:
// there is no point advertising an indicator with nothing behind it, and the
// coverage range is the first thing anyone asks about a series.
//
// The filter is a clause the caller supplies rather than an id, because the
// interesting questions about a series are not all "which one": the document
// endpoints ask which series a source document produced, and that is the same
// summary over a different slice of the same table.
func (s *Server) indicatorRows(
	ctx context.Context, w http.ResponseWriter, where string, args []any,
	only ...string,
) ([]Indicator, error) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		// Empty rather than nil. This is marshalled straight into `data`, and
		// a nil slice reaches the browser as `null` — which is not an empty
		// list to anything that maps over it, so a lake with nothing
		// normalized yet takes the portal down instead of showing no rows.
		return []Indicator{}, nil
	}

	// `only` narrows the scan to one series' own partition where the caller
	// knows which one it wants. The WHERE still does the filtering — this
	// spares the listing, which is what the query actually costs.
	observations, err := s.warehouse.SourceIn(
		storage.LayerSilver, "observations", "indicator_id", only,
	)
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return nil, err
	}

	// A LEFT JOIN onto the names, and a table that may not be published at all:
	// an indicator with no name row is still a real series, and dropping it
	// here would hide figures rather than a missing label.
	named := "(SELECT NULL AS indicator_id, NULL AS name, NULL AS code, " +
		"NULL AS description, NULL AS publisher, NULL AS release, " +
		"NULL AS slug, NULL AS dataset_id, NULL AS tags) n ON false"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "indicators") {
		catalogue, err := s.source(storage.LayerSilver, "indicators")
		if err != nil {
			internalError(w, s.log, "resolve indicator names", err)
			return nil, err
		}
		// The slug, the dataset and the tags arrived after the first lakes
		// were written. Each is selected where the files carry it and read as
		// null where they do not, so an older lake serves names without them
		// rather than failing to bind and serving nothing.
		columns := []string{"indicator_id", "name", "code", "description", "publisher", "release"}
		for _, column := range []string{"slug", "dataset_id", "tags"} {
			if s.warehouse.HasColumn(ctx, storage.LayerSilver, "indicators", column) {
				columns = append(columns, column)
			} else {
				columns = append(columns, "NULL AS "+column)
			}
		}
		named = "(SELECT " + strings.Join(columns, ", ") + " FROM " + catalogue +
			") n ON n.indicator_id = o.indicator_id"
	}

	// Every series, unfiltered, is the catalogue's question and comes out
	// of the rollup; a filtered one reads its own partitions.
	//
	// min() rather than any_value() for what a series should hold one of:
	// 26 APBD series carry rows under two collections, and any_value named
	// either, differently from one query to the next.
	agg := aggregates{from: observations, count: "count(*)", first: "min(o.period)",
		last: "max(o.period)", processed: "max(o.processed_at)"}
	if where == "" && len(only) == 0 {
		agg = s.aggregatesOver(observations)
	}
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT o.indicator_id,
		       any_value(n.name),
		       any_value(n.code),
		       any_value(n.slug),
		       any_value(n.description),
		       any_value(n.publisher),
		       any_value(n.release),
		       min(o.temporal_resolution),
		       min(o.unit),
		       %s,
		       count(DISTINCT o.geo_id),
		       %s,
		       %s,
		       list_sort(list(DISTINCT o.source_id)),
		       strftime(%s, '%%Y-%%m-%%dT%%H:%%M:%%SZ'),
		       min(o.dataset_id),
		       any_value(n.tags)
		FROM %s o
		LEFT JOIN %s
		%s
		GROUP BY o.indicator_id
		ORDER BY o.indicator_id`, agg.count, agg.first, agg.last, agg.processed, agg.from, named, where), args...)
	if err != nil {
		internalError(w, s.log, "query indicators", err)
		return nil, err
	}
	defer rows.Close()

	indicators := make([]Indicator, 0, 16)
	for rows.Next() {
		var i Indicator
		var sources, tags any
		if err := rows.Scan(
			&i.IndicatorID, &i.Name, &i.Code, &i.Slug, &i.Description, &i.Publisher,
			&i.Release, &i.Resolution, &i.Unit, &i.Observations,
			&i.Geographies, &i.PeriodStart, &i.PeriodEnd, &sources, &i.LastUpdated,
			&i.DatasetID, &tags,
		); err != nil {
			internalError(w, s.log, "scan indicator", err)
			return nil, err
		}
		i.Sources = asStrings(sources)
		// Never null in the response: a reader filtering on tags should get an
		// empty list from a series published before tagging, not a null they
		// have to guard every access with.
		i.Tags = asStrings(tags)
		if i.Tags == nil {
			i.Tags = []string{}
		}
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
// LakeTable is one physical table in the lake.
//
// An operational view, not a research one: these rows are the same in every
// warehouse this code runs, and say nothing about what has been collected. What
// a reader means by "dataset" is in datasets.go.
type LakeTable struct {
	Slug  string `json:"slug"`
	Layer string `json:"layer"`
	Name  string `json:"name"`
	Rows  int64  `json:"rows"`
}

// handleStorage lists what is actually in the lake.
//
// Read from storage rather than from the PostgreSQL catalog, so the endpoint
// answers on a deployment with no database — and so it cannot disagree with
// what is on disk, which a catalog can.
func (s *Server) handleStorage(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	known := []struct {
		layer   storage.Layer
		dataset string
		name    string
	}{
		{storage.LayerSilver, "observations", "Observations"},
		{storage.LayerSilver, "geography", "Geography dimension"},
		{storage.LayerSilver, "commodities", "Commodity dimension"},
		{storage.LayerSilver, "sources", "Source registry"},
		{storage.LayerSilver, "events", "Event calendar"},
		{storage.LayerSilver, "documents", "Document catalogue"},
		{storage.LayerSilver, "regulations", "Regulations"},
		{storage.LayerSilver, "regulation_sections", "Regulation sections"},
		{storage.LayerSilver, "regulation_citations", "Regulation citations"},
		{storage.LayerSilver, "regulation_tags", "Regulation tags"},
		{storage.LayerBronze, "records", "Bronze records"},
		{storage.LayerBronze, "documents", "Bronze documents"},
	}

	// Counted side by side: each is a separate scan, and one after another
	// the answer took as long as all of them together.
	counted := make([]*LakeTable, len(known))
	var wg sync.WaitGroup
	for n, entry := range known {
		if !s.warehouse.Exists(ctx, entry.layer, entry.dataset) {
			continue
		}
		expression, err := s.source(entry.layer, entry.dataset)
		if err != nil {
			continue
		}
		counting := "SELECT count(*) FROM " + expression
		if entry.dataset == "observations" && s.rolled() {
			// The one count that opened every file, and the rollup holds it.
			counting = "SELECT CAST(sum(n) AS BIGINT) FROM " + rollupTable
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			var rows int64
			if err := s.warehouse.DB().QueryRowContext(ctx, counting).Scan(&rows); err != nil {
				s.log.Warn("storage.count_failed", "dataset", entry.dataset, "error", err)
				return
			}
			counted[n] = &LakeTable{
				Slug:  fmt.Sprintf("%s-%s", entry.layer, entry.dataset),
				Layer: entry.layer.String(),
				Name:  entry.name,
				Rows:  rows,
			}
		}()
	}
	wg.Wait()
	tables := make([]LakeTable, 0, len(known))
	for _, table := range counted {
		if table != nil {
			tables = append(tables, *table)
		}
	}

	writeData(w, tables, &Meta{Total: int64(len(tables))})
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
