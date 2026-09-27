package httpapi

import (
	"net/http"
	"slices"
	"sort"
	"strings"
)

// Finding series on the server.
//
// The portal used to download every series — 31,000 of them, eighteen
// megabytes — and filter them in the browser: for the search page, the ⌘K
// palette on every page, the explorer's filter, a collection's page. Each of
// those now asks for the few it needs. The filters are the portal's own,
// moved here unchanged, and they run over the catalogue in memory, so a search
// costs a pass over a slice rather than a scan of the lake.
//
// A request with no parameters still answers with every series, for the pages
// that summarise the whole catalogue.

// indicatorQuery is what a request asks of the series list.
type indicatorQuery struct {
	q           string
	frequencies []string
	units       []string
	sources     []string
	tags        []string
	ids         []string
	dataset     string
	sort        string
	descending  bool
}

// Bounds on what a request may ask, since these values are matched in Go
// rather than bound into SQL: enough for any real filter, not a way to make
// the server compare a megabyte of text per series.
const (
	maxIndicatorFilterValues = 200
	maxIndicatorFilterLength = 200
)

func readIndicatorQuery(r *http.Request) (indicatorQuery, bool, error) {
	values := r.URL.Query()
	list := func(name string) ([]string, error) {
		var out []string
		for _, raw := range values[name] {
			for _, value := range strings.Split(raw, ",") {
				value = strings.TrimSpace(value)
				if value == "" {
					continue
				}
				if len(value) > maxIndicatorFilterLength {
					return nil, paramErr(name, "is too long")
				}
				out = append(out, value)
			}
		}
		if len(out) > maxIndicatorFilterValues {
			return nil, paramErr(name, "names too many values")
		}
		return out, nil
	}
	var q indicatorQuery
	var err error
	if q.frequencies, err = list("frequency"); err != nil {
		return q, false, err
	}
	if q.units, err = list("unit"); err != nil {
		return q, false, err
	}
	if q.sources, err = list("source"); err != nil {
		return q, false, err
	}
	if q.tags, err = list("tag"); err != nil {
		return q, false, err
	}
	if q.ids, err = list("id"); err != nil {
		return q, false, err
	}
	q.q = strings.ToLower(strings.TrimSpace(values.Get("q")))
	q.dataset = strings.TrimSpace(values.Get("dataset"))
	if len(q.q) > maxIndicatorFilterLength || len(q.dataset) > maxIndicatorFilterLength {
		return q, false, paramErr("q", "is too long")
	}
	q.sort = values.Get("sort")
	switch q.sort {
	case "", "indicator_id", "temporal_resolution", "unit", "source", "coverage", "last_updated":
	default:
		return q, false, paramErr("sort", "is not a column")
	}
	switch values.Get("dir") {
	case "", "asc":
	case "desc":
		q.descending = true
	default:
		return q, false, paramErr("dir", "is asc or desc")
	}
	// The newest first unless asked otherwise, as the series page orders.
	if q.sort == "" {
		q.sort, q.descending = "last_updated", values.Get("dir") != "asc"
	}
	asked := len(values) > 0
	return q, asked, nil
}

// matches is the portal's filter, as it ran in the browser.
func (q indicatorQuery) matches(i Indicator) bool {
	if len(q.frequencies) > 0 && !slices.Contains(q.frequencies, i.Resolution) {
		return false
	}
	if len(q.units) > 0 && (i.Unit == nil || !slices.Contains(q.units, *i.Unit)) {
		return false
	}
	if len(q.sources) > 0 && !slices.ContainsFunc(i.Sources, func(s string) bool {
		return slices.Contains(q.sources, s)
	}) {
		return false
	}
	// Every tag, not any: a second tag narrows.
	for _, tag := range q.tags {
		if !slices.Contains(i.Tags, tag) {
			return false
		}
	}
	if len(q.ids) > 0 && !slices.Contains(q.ids, i.IndicatorID) {
		return false
	}
	if q.dataset != "" && (i.DatasetID == nil || *i.DatasetID != q.dataset) {
		return false
	}
	// Every word, each anywhere: "bi rate" finds "BI 7-Day Reverse Repo
	// Rate", as the search page and the palette did in the browser, which
	// also looked in the description and the publisher.
	for _, word := range strings.Fields(q.q) {
		if !seriesMentions(i, word) {
			return false
		}
	}
	return true
}

func seriesMentions(i Indicator, word string) bool {
	contains := func(value *string) bool {
		return value != nil && strings.Contains(strings.ToLower(*value), word)
	}
	return strings.Contains(strings.ToLower(i.IndicatorID), word) ||
		contains(i.Slug) ||
		slices.ContainsFunc(i.Tags, func(tag string) bool { return strings.Contains(tag, word) }) ||
		strings.Contains(strings.ToLower(indicatorTitle(i)), word) ||
		contains(i.Code) ||
		contains(i.Description) ||
		contains(i.Publisher) ||
		slices.ContainsFunc(i.Sources, func(s string) bool { return strings.Contains(strings.ToLower(s), word) })
}

// sortKey is the portal's, column for column.
func (q indicatorQuery) sortKey(i Indicator) string {
	switch q.sort {
	case "indicator_id":
		return strings.ToLower(indicatorTitle(i))
	case "temporal_resolution":
		return i.Resolution
	case "unit":
		return deref(i.Unit)
	case "source":
		if len(i.Sources) > 0 {
			return i.Sources[0]
		}
		return ""
	case "coverage":
		return i.PeriodEnd
	default:
		return deref(i.LastUpdated)
	}
}

func (q indicatorQuery) apply(series []Indicator) []Indicator {
	matched := make([]Indicator, 0, 64)
	for _, i := range series {
		if q.matches(i) {
			matched = append(matched, i)
		}
	}
	sort.SliceStable(matched, func(a, b int) bool {
		left, right := q.sortKey(matched[a]), q.sortKey(matched[b])
		// A series with nothing in the column goes last either way.
		if (left == "") != (right == "") {
			return left != ""
		}
		if q.descending {
			return left > right
		}
		return left < right
	})
	return matched
}

func deref(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// IndicatorFacets is what the series list can be filtered by.
type IndicatorFacets struct {
	Frequencies []string `json:"frequencies"`
	Units       []string `json:"units"`
	Sources     []string `json:"sources"`
	Tags        []string `json:"tags"`
}

func (s *Server) handleIndicatorFacets(w http.ResponseWriter, r *http.Request) {
	catalogue, err := s.lakeCatalogue(r.Context())
	if err != nil {
		internalError(w, s.log, "build catalogue", err)
		return
	}
	sets := [4]map[string]bool{{}, {}, {}, {}}
	for _, i := range catalogue.series {
		sets[0][i.Resolution] = true
		if i.Unit != nil && *i.Unit != "" {
			sets[1][*i.Unit] = true
		}
		for _, source := range i.Sources {
			sets[2][source] = true
		}
		for _, tag := range i.Tags {
			sets[3][tag] = true
		}
	}
	sorted := func(set map[string]bool) []string {
		out := make([]string, 0, len(set))
		for value := range set {
			out = append(out, value)
		}
		sort.Strings(out)
		return out
	}
	writeData(w, IndicatorFacets{
		Frequencies: sorted(sets[0]), Units: sorted(sets[1]),
		Sources: sorted(sets[2]), Tags: sorted(sets[3]),
	}, &Meta{Layer: "silver"})
}

func paramErr(param, reason string) error { return &paramError{param: param, reason: reason} }
