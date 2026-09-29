package httpapi

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
)

// A page of a long series, taken by where the last one stopped rather than by
// how many rows precede it.
//
// `OFFSET 2_596_600` makes DuckDB produce and discard every row before the one
// asked for: over Bank Indonesia's daily food prices the last page of the
// figures table measured at 2.4s against 0.3s for the first. A cursor names the
// boundary instead — "everything sorting after this row" — which the engine can
// answer from the sort key alone, so page forty thousand costs what page one
// does.
//
// It does not replace the offset. The table offers numbered pages and a jump to
// the last one, and a cursor cannot say where page twelve begins; the portal
// carries a cursor while it pages one step at a time and falls back to the
// offset for a jump.

// observationSortKey is one column of a sort, with the direction it is read in.
type observationSortKey struct {
	expr string
	desc bool
}

// cursorKey names a sort column that can be carried in a cursor, and says how
// to read its value off a row that has already been serialised.
//
// A sort on the figure itself is deliberately absent: `value` is a decimal, and
// a boundary round-tripped through a cursor would have to compare equal to the
// stored decimal exactly or the page would skip a row or repeat one. Sorting by
// value is a question about the extremes of a series, which is a first page and
// not a walk through the middle of one.
var cursorKey = map[string]func(Observation) *string{
	"o.observation_id": func(o Observation) *string { return &o.ObservationID },
	"o.period":         func(o Observation) *string { return &o.Period },
	"o.geo_id":         func(o Observation) *string { return o.GeoID },
	"o.category":       func(o Observation) *string { return o.Category },
	memberGeoExpr:      func(o Observation) *string { return o.GeoName },
	memberCommodityExpr: func(o Observation) *string {
		return o.CommodityName
	},
}

// cursorable reports whether every column of a sort can be carried in a cursor.
func cursorable(keys []observationSortKey) bool {
	for _, key := range keys {
		if _, ok := cursorKey[key.expr]; !ok {
			return false
		}
	}
	return true
}

// cursorFor encodes the sort key of the last row on a page.
func cursorFor(keys []observationSortKey, row Observation) string {
	values := make([]*string, 0, len(keys))
	for _, key := range keys {
		values = append(values, cursorKey[key.expr](row))
	}
	encoded, err := json.Marshal(values)
	if err != nil {
		return ""
	}
	return base64.RawURLEncoding.EncodeToString(encoded)
}

// decodeCursor reads one back, refusing anything that does not belong to the
// sort it arrived with.
//
// A cursor is this server's own bookmark, not a filter a caller composes: one
// that does not decode, or that carries the wrong number of columns, is a
// cursor from another sort or another version and answering it would page
// through an order nobody asked for.
func decodeCursor(encoded string, keys []observationSortKey) ([]*string, error) {
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(encoded, "="))
	if err != nil {
		return nil, errors.New("is not a cursor this server issued")
	}
	var values []*string
	if err := json.Unmarshal(raw, &values); err != nil {
		return nil, errors.New("is not a cursor this server issued")
	}
	if len(values) != len(keys) {
		return nil, errors.New("belongs to a different order")
	}
	return values, nil
}

// keysetAfter is the WHERE clause for "everything sorting after this row".
//
// Written out rather than expressed as a row comparison — `(a, b) > (?, ?)` —
// because the columns do not share a direction: the figures table reads newest
// first and, within one day, by place ascending. Each branch pins the columns
// before it to the boundary row's values and steps past it on one column:
//
//	period < ?
//	OR (period = ? AND geo_id > ?)
//	OR (period = ? AND geo_id = ? AND observation_id > ?)
//
// Nulls sort last throughout, which is what `NULLS LAST` in the ORDER BY says,
// so a column that is null in the boundary row has nothing after it and
// contributes no branch of its own. `IS NOT DISTINCT FROM` rather than `=` for
// the columns being pinned, because a null there is a value to match and not an
// unknown to propagate.
func keysetAfter(keys []observationSortKey, values []*string) (string, []any) {
	var branches []string
	var args []any

	for i, key := range keys {
		if values[i] == nil {
			// Null sorts last: nothing follows it on this column.
			continue
		}
		var terms []string
		for j := 0; j < i; j++ {
			terms = append(terms, keys[j].expr+" IS NOT DISTINCT FROM ?")
			args = append(args, values[j])
		}
		step := ">"
		if key.desc {
			step = "<"
		}
		terms = append(terms, "("+key.expr+" "+step+" ? OR "+key.expr+" IS NULL)")
		args = append(args, *values[i])
		branches = append(branches, "("+strings.Join(terms, " AND ")+")")
	}

	if len(branches) == 0 {
		// Every column of the boundary row is null, which only the very last
		// row of a sort can be. Nothing follows it.
		return " AND false", nil
	}
	return " AND (" + strings.Join(branches, " OR ") + ")", args
}
