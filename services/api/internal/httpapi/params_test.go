package httpapi

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
)

// request builds a request with properly encoded parameters. Concatenating raw
// values would make the test fail on the space in `1=1 --` rather than on the
// validation it is meant to exercise.
func request(pairs ...string) *http.Request {
	values := url.Values{}
	for i := 0; i+1 < len(pairs); i += 2 {
		values.Set(pairs[i], pairs[i+1])
	}
	return httptest.NewRequest(http.MethodGet, "/v1/observations?"+values.Encode(), nil)
}

// ---- identifiers ----------------------------------------------------------

func TestWellFormedIdentifiersPass(t *testing.T) {
	for _, value := range []string{"IDN", "ID-32", "GDP_CURRENT_USD", "gdp.current", "a+b"} {
		got, err := stringParam(request("geo", value), "geo", identifierPattern)
		if err != nil || got != value {
			t.Errorf("stringParam(%q) = %q, %v", value, got, err)
		}
	}
}

func TestInjectionShapedInputIsRefused(t *testing.T) {
	// Every value reaching SQL is bound, so this is defence in depth rather
	// than the only defence. It still belongs here: a rejected request is a
	// clearer signal than a query that binds nonsense and returns nothing.
	for _, value := range []string{
		"IDN' OR 1=1--",
		"IDN; DROP TABLE observations",
		"../../etc/passwd",
		"IDN OR 1=1",
		"<script>",
	} {
		if _, err := stringParam(request("geo", value), "geo", identifierPattern); err == nil {
			t.Errorf("stringParam accepted %q", value)
		}
	}
}

func TestAnAbsentParameterIsNotAnError(t *testing.T) {
	got, err := stringParam(request(), "geo", identifierPattern)
	if err != nil || got != "" {
		t.Errorf("stringParam = %q, %v; want empty and no error", got, err)
	}
}

// ---- periods --------------------------------------------------------------

func TestCanonicalPeriodLabelsPass(t *testing.T) {
	for _, value := range []string{"2026", "2026-01", "2026-Q1", "2026-S2", "2026-01-15"} {
		if _, err := stringParam(request("period_start", value), "period_start", periodPattern); err != nil {
			t.Errorf("period %q rejected: %v", value, err)
		}
	}
}

func TestMalformedPeriodsAreRefused(t *testing.T) {
	for _, value := range []string{"Januari 2026", "26-01", "2026-13-45x", "'; --"} {
		if _, err := stringParam(request("period_start", value), "period_start", periodPattern); err == nil {
			t.Errorf("period %q accepted", value)
		}
	}
}

// ---- pagination -----------------------------------------------------------

func TestPaginationDefaults(t *testing.T) {
	limit, offset, err := pagination(request())
	if err != nil || limit != DefaultLimit || offset != 0 {
		t.Errorf("pagination = %d, %d, %v", limit, offset, err)
	}
}

func TestLimitIsCapped(t *testing.T) {
	// An unbounded limit is a legitimate need, but it belongs in the export or
	// SQL surface, not in a JSON response nobody can stream.
	if _, _, err := pagination(request("limit", "999999")); err == nil {
		t.Error("pagination accepted a limit above the cap")
	}
	if _, _, err := pagination(request("limit", "0")); err == nil {
		t.Error("pagination accepted a zero limit")
	}
}

func TestNonNumericPaginationIsRefused(t *testing.T) {
	if _, _, err := pagination(request("limit", "all")); err == nil {
		t.Error("pagination accepted a non-numeric limit")
	}
}

// ---- sort order -----------------------------------------------------------

func TestSortOrderComesFromAnAllowList(t *testing.T) {
	// ORDER BY cannot be a bound parameter, so the caller picks a key and the
	// server supplies the clause. Nothing from the request reaches the SQL.
	clause, err := sortOrder(request("order", "-value"), observationOrder, "period")
	if err != nil {
		t.Fatalf("sortOrder: %v", err)
	}
	if clause != observationOrder["-value"] {
		t.Errorf("sortOrder = %q", clause)
	}
}

func TestAnUnknownSortKeyIsRefused(t *testing.T) {
	for _, value := range []string{"value; DROP TABLE x", "rand()", "unknown_column"} {
		if _, err := sortOrder(request("order", value), observationOrder, "period"); err == nil {
			t.Errorf("sortOrder accepted %q", value)
		}
	}
}

func TestSortOrderFallsBackWhenAbsent(t *testing.T) {
	clause, err := sortOrder(request(), observationOrder, "period")
	if err != nil || clause != observationOrder["period"] {
		t.Errorf("sortOrder = %q, %v", clause, err)
	}
}

// ---- filter construction --------------------------------------------------

func TestFiltersBindEveryValue(t *testing.T) {
	where, args := observationFilters(
		[]string{"GDP"}, []string{"IDN"}, []string{"country"}, "2020", "2024", "",
	)
	if got := countPlaceholders(where); got != len(args) {
		t.Errorf("%d placeholders for %d arguments: %q", got, len(args), where)
	}
	if len(args) != 5 {
		t.Errorf("args = %v, want five bound values", args)
	}
}

func TestNoFiltersMeansNoWhereClause(t *testing.T) {
	where, args := observationFilters(nil, nil, nil, "", "", "")
	if where != "" || args != nil {
		t.Errorf("observationFilters = %q, %v; want empty", where, args)
	}
}

func TestFilterValuesNeverReachTheSQLText(t *testing.T) {
	where, _ := observationFilters([]string{"GDP'; DROP TABLE x--"}, nil, nil, "", "", "")
	if contains(where, "DROP") {
		t.Errorf("filter value leaked into SQL: %q", where)
	}
}

func countPlaceholders(s string) int {
	n := 0
	for _, r := range s {
		if r == '?' {
			n++
		}
	}
	return n
}

func contains(haystack, needle string) bool {
	return strings.Contains(haystack, needle)
}

// ---- multi-value filters --------------------------------------------------

func TestListParamAcceptsRepeatedAndCommaSeparated(t *testing.T) {
	// Both are in the wild: repeats are what a form produces, commas are what
	// a hand-written URL looks like.
	repeated := httptest.NewRequest("GET", "/v1/observations?geo=IDN&geo=MYS", nil)
	commas := httptest.NewRequest("GET", "/v1/observations?geo=IDN,MYS", nil)

	for _, r := range []*http.Request{repeated, commas} {
		values, err := stringListParam(r, "geo", identifierPattern)
		if err != nil {
			t.Fatalf("stringListParam: %v", err)
		}
		if len(values) != 2 || values[0] != "IDN" || values[1] != "MYS" {
			t.Errorf("stringListParam = %v", values)
		}
	}
}

func TestListParamDropsDuplicatesAndBlanks(t *testing.T) {
	r := httptest.NewRequest("GET", "/v1/observations?geo=IDN,,IDN,MYS", nil)
	values, err := stringListParam(r, "geo", identifierPattern)
	if err != nil {
		t.Fatalf("stringListParam: %v", err)
	}
	if len(values) != 2 {
		t.Errorf("stringListParam = %v, want two distinct values", values)
	}
}

func TestOneBadValueFailsTheWholeList(t *testing.T) {
	// Quietly dropping it would answer a different question than the one asked.
	r := request("geo", "IDN,DROP TABLE x")
	if _, err := stringListParam(r, "geo", identifierPattern); err == nil {
		t.Error("stringListParam accepted a malformed value")
	}
}

func TestListParamIsBounded(t *testing.T) {
	many := make([]string, MaxFilterValues+1)
	for i := range many {
		many[i] = "IDN"
	}
	// Distinct, so deduplication does not hide the size.
	for i := range many {
		many[i] = "ID-" + strings.Repeat("A", i%3+1) + strconv.Itoa(i)
	}
	r := request("geo", strings.Join(many, ","))
	if _, err := stringListParam(r, "geo", identifierPattern); err == nil {
		t.Error("stringListParam accepted an unbounded list")
	}
}

func TestInClauseBindsOnePlaceholderPerValue(t *testing.T) {
	clause, args := inClause("g.geo_type", []string{"country", "province"})
	if countPlaceholders(clause) != len(args) {
		t.Errorf("%q binds %d args", clause, len(args))
	}
	if !strings.Contains(clause, "IN (") {
		t.Errorf("clause = %q", clause)
	}
}

func TestInClauseWithOneValueUsesEquality(t *testing.T) {
	clause, args := inClause("g.geo_type", []string{"country"})
	if clause != "g.geo_type = ?" || len(args) != 1 {
		t.Errorf("inClause = %q, %v", clause, args)
	}
}

func TestInClauseWithNothingProducesNothing(t *testing.T) {
	clause, args := inClause("g.geo_type", nil)
	if clause != "" || args != nil {
		t.Errorf("inClause = %q, %v", clause, args)
	}
}

func TestMultipleValuesStillBindEveryArgument(t *testing.T) {
	where, args := observationFilters(
		[]string{"a", "b"}, []string{"IDN", "MYS", "THA"}, []string{"country"}, "", "", "",
	)
	if countPlaceholders(where) != len(args) {
		t.Errorf("%q binds %d args", where, len(args))
	}
}

// ---- free-text search -----------------------------------------------------

func TestSearchAcceptsOrdinaryText(t *testing.T) {
	for _, value := range []string{"Jawa Barat", "IDN", "gdp_current_usd", "Aceh"} {
		if _, err := stringParam(request("q", value), "q", searchPattern); err != nil {
			t.Errorf("search %q rejected: %v", value, err)
		}
	}
}

func TestSearchRefusesPunctuationOnlyQueries(t *testing.T) {
	// A query of wildcards matches everything and costs a scan to find out.
	for _, value := range []string{"%%%", "';--", "<script>", "*"} {
		if _, err := stringParam(request("q", value), "q", searchPattern); err == nil {
			t.Errorf("search accepted %q", value)
		}
	}
}

func TestSearchIsBound(t *testing.T) {
	where, args := observationFilters(nil, nil, nil, "", "", "Jawa")
	if countPlaceholders(where) != len(args) {
		t.Errorf("%q binds %d args", where, len(args))
	}
	if strings.Contains(where, "Jawa") {
		t.Errorf("search text reached the SQL: %q", where)
	}
}
