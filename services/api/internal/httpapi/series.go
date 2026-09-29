package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// A series as a chart needs it: a line per member, at a granularity the span
// can be drawn at.
//
// Bank Indonesia's daily food prices are 2.6 million figures across 34
// provinces and 31 commodities. Sending them to a browser to be plotted is
// several hundred megabytes for a picture a thousand pixels wide, and taking
// the first few thousand instead — which is what the portal did — draws a line
// through one week and calls it a decade. Aggregating where the data is turns
// the whole series into a few hundred points per line, which is the most a
// chart can show anyway.
//
// A mean is not a published figure, so every point says what it was made of:
// how many rows went into it, and how many of those had no value. The
// granularity is on the response rather than inferred by the reader, and a span
// short enough to draw as published is returned as published.

// SeriesPoint is one plotted point: a published figure where the bucket holds
// exactly one, and the mean of the bucket where it holds more.
type SeriesPoint struct {
	Period string `json:"period"`
	// A string for the same reason an observation's value is one — a decimal
	// through a JSON number comes back a float64. Null where every figure in
	// the bucket was absent.
	Value *string `json:"value"`
	/// How many figures the point stands for, and how many of those were absent.
	Count   int64 `json:"count"`
	Missing int64 `json:"missing"`
}

// SeriesLine is one member's line.
type SeriesLine struct {
	/// The label the portal knows the member by — `Aceh`, `Rice`, `Aceh · Rice`.
	Member string        `json:"member"`
	Count  int64         `json:"count"`
	Points []SeriesPoint `json:"points"`
}

// ObservationSeries is what a chart of one indicator needs.
type ObservationSeries struct {
	// native, month, quarter or year. `native` means the figures are as
	// published and nothing has been averaged.
	Granularity string `json:"granularity"`
	// geography, commodity, both, category or none — what the lines are split
	// along.
	Dimension string `json:"dimension"`
	// How many members there are in total, against however many lines are
	// returned: a chart that draws the eight largest has to be able to say so.
	Members int64        `json:"members"`
	Series  []SeriesLine `json:"series"`
}

// The member expressions, matching the labels the portal builds from a row. The
// em dash stands in for a member with no name, as it does there: a figure with
// no place is still a figure, and dropping it would hide a gap.
const (
	memberBoth = "coalesce(" + memberGeoExpr + ", '—') || ' · ' || coalesce(" +
		memberCommodityExpr + ", '—')"
)

// defaultPoints is how many points a line is worth drawing with.
//
// A chart is around a thousand pixels wide, and past a point per two or three
// pixels the line is drawing over itself. Four hundred is a decade of months,
// or thirteen months of days.
const defaultPoints = 400

func (s *Server) handleObservationSeries(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	filter, err := observationParams(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	points, err := intParam(r, "points", defaultPoints, 10, 2000)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	// Eight lines is where a categorical palette runs out; the cap is higher
	// for a caller drawing its own.
	members, err := intParam(r, "members", 8, 1, 50)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	// Which dimension to split the lines along, where the caller already knows.
	//
	// Derived from the filtered rows otherwise, which is not the same answer: a
	// reader who has narrowed to one province of a series that varies by place
	// and commodity is still reading a series about both, and a line relabelled
	// `Aceh` under a filter would no longer match the `Aceh · Rice` the legend
	// and the table know it by.
	dimension, err := stringParam(r, "dimension", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	switch dimension {
	case "", "geography", "commodity", "both", "category", "none":
	default:
		badRequest(w, "invalid parameter", "dimension: is not a dimension")
		return
	}

	empty := ObservationSeries{Granularity: "native", Dimension: "none", Series: []SeriesLine{}}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, empty, &Meta{Layer: "silver"})
		return
	}

	from_, err := s.observationSource(ctx, filter.Indicators)
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}
	where, args := observationFilters(filter)

	shape, err := s.seriesShape(ctx, from_, where, args, points, dimension)
	if err != nil {
		internalError(w, s.log, "measure series", err)
		return
	}
	if shape.rows == 0 {
		writeData(w, empty, &Meta{Layer: "silver"})
		return
	}

	member := memberGeoExpr
	switch shape.dimension {
	case "both":
		member = memberBoth
	case "commodity":
		member = memberCommodityExpr
	case "category":
		member = "o.category"
	case "none":
		member = "''"
	}

	// A member with no name at all is left out rather than drawn as a line
	// nobody can identify; its figures are still in the table.
	named := ""
	if shape.dimension != "none" {
		named = " WHERE member IS NOT NULL"
	}

	// One pass: bucket every row, find the largest members, and average within
	// each member's buckets. The `CASE` keeps a bucket of one row exactly as it
	// was published — a mean of one figure is that figure, but routed through a
	// double it is that figure rewritten.
	querySQL := fmt.Sprintf(`
		WITH src AS (
			SELECT %s AS member, %s AS bucket, o.value AS value
			FROM %s%s
		), largest AS (
			SELECT member, count(*) AS n FROM src%s
			GROUP BY 1 ORDER BY n DESC, member ASC LIMIT %d
		)
		SELECT src.member, largest.n, src.bucket,
		       CASE WHEN count(*) = 1 THEN CAST(any_value(src.value) AS VARCHAR)
		            ELSE CAST(round(avg(src.value), 6) AS VARCHAR) END AS value,
		       count(*) AS n,
		       count(*) FILTER (WHERE src.value IS NULL) AS absent
		FROM src JOIN largest ON largest.member IS NOT DISTINCT FROM src.member
		GROUP BY 1, 2, 3
		ORDER BY largest.n DESC, src.member ASC, src.bucket ASC`,
		member, bucketExpr(shape.granularity), from_, where, named, members)

	rows, err := s.warehouse.DB().QueryContext(ctx, querySQL, args...)
	if err != nil {
		internalError(w, s.log, "query series", err)
		return
	}
	defer rows.Close()

	series := make([]SeriesLine, 0, members)
	byMember := map[string]int{}
	for rows.Next() {
		var name string
		var count int64
		var point SeriesPoint
		if err := rows.Scan(
			&name, &count, &point.Period, &point.Value, &point.Count, &point.Missing,
		); err != nil {
			internalError(w, s.log, "scan series", err)
			return
		}
		at, seen := byMember[name]
		if !seen {
			at = len(series)
			byMember[name] = at
			series = append(series, SeriesLine{Member: name, Count: count})
		}
		series[at].Points = append(series[at].Points, point)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read series", err)
		return
	}

	writeData(w, ObservationSeries{
		Granularity: shape.granularity,
		Dimension:   shape.dimension,
		Members:     shape.members,
		Series:      series,
	}, &Meta{Total: shape.rows, Layer: "silver"})
}

// seriesShape is what one probe of the filtered rows says about how to draw
// them: which dimension they vary along, how many members there are, and the
// granularity their span can be drawn at.
type seriesShape struct {
	dimension   string
	members     int64
	granularity string
	rows        int64
}

func (s *Server) seriesShape(
	ctx context.Context, from, where string, args []any, points int, asked string,
) (seriesShape, error) {
	var shape seriesShape
	var places, commodities, pairs, categories, periods int64
	var first, last sql.NullTime

	// One query rather than five: each of them is a scan of the same rows.
	err := s.warehouse.DB().QueryRowContext(ctx, fmt.Sprintf(`
		SELECT count(*), count(DISTINCT %s), count(DISTINCT %s), count(DISTINCT %s),
		       count(DISTINCT o.category),
		       count(DISTINCT o.period), min(o.period_start), max(o.period_start)
		FROM %s%s`, memberGeoExpr, memberCommodityExpr, memberBoth, from, where), args...,
	).Scan(&shape.rows, &places, &commodities, &pairs, &categories, &periods, &first, &last)
	if err != nil {
		return shape, err
	}

	// The same judgement the portal makes from the facet counts, in the same
	// order: a series that varies along neither still keeps the one column that
	// says what its figures are about.
	shape.dimension = asked
	if shape.dimension == "" {
		switch {
		// Before the place: a table of age groups is national, so its one
		// place would otherwise claim it and draw every group as one line.
		case categories > 1 && places <= 1 && commodities <= 1:
			shape.dimension = "category"
		case places > 1 && commodities > 1:
			shape.dimension = "both"
		case places > 1:
			shape.dimension = "geography"
		case commodities > 1:
			shape.dimension = "commodity"
		case places == 1:
			shape.dimension = "geography"
		case commodities == 1:
			shape.dimension = "commodity"
		default:
			shape.dimension = "none"
		}
	}
	switch shape.dimension {
	case "both":
		shape.members = pairs
	case "commodity":
		shape.members = commodities
	case "geography":
		shape.members = places
	case "category":
		shape.members = categories
	}

	shape.granularity = granularityFor(periods, first, last, points)
	return shape, nil
}

// granularityFor picks the coarsest bucket the span does not need, which is the
// finest one it fits in.
//
// Off the number of distinct periods rather than the number of rows: 2.6
// million figures across 34 provinces and 31 commodities are three and a half
// thousand days, and it is the days that a line has to have room for.
func granularityFor(periods int64, first, last sql.NullTime, points int) string {
	if periods <= int64(points) {
		return "native"
	}
	if !first.Valid || !last.Valid {
		// No bounded dates to bucket by. The labels are all there is, and
		// there are more of them than fit — the chart says so.
		return "native"
	}

	months := monthsBetween(first.Time, last.Time)
	switch {
	case months <= int64(points):
		return "month"
	case months/3+1 <= int64(points):
		return "quarter"
	default:
		return "year"
	}
}

func monthsBetween(first, last time.Time) int64 {
	years := int64(last.Year() - first.Year())
	return years*12 + int64(last.Month()) - int64(first.Month()) + 1
}

// bucketExpr writes the canonical label for a bucket — `2026-01`, `2026-Q1`,
// `2026` — so a point reads the way a period does everywhere else.
func bucketExpr(granularity string) string {
	switch granularity {
	case "day":
		return "strftime(o.period_start, '%Y-%m-%d')"
	case "month":
		return "strftime(o.period_start, '%Y-%m')"
	case "quarter":
		return "strftime(o.period_start, '%Y') || '-Q' || CAST(quarter(o.period_start) AS VARCHAR)"
	case "year":
		return "strftime(o.period_start, '%Y')"
	default:
		return "o.period"
	}
}
