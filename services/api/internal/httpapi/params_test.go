package httpapi

import (
	"net/http"
	"net/http/httptest"
	"net/url"
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
	where, args := observationFilters("GDP", "IDN", "country", "2020", "2024")
	if got := countPlaceholders(where); got != len(args) {
		t.Errorf("%d placeholders for %d arguments: %q", got, len(args), where)
	}
	if len(args) != 5 {
		t.Errorf("args = %v, want five bound values", args)
	}
}

func TestNoFiltersMeansNoWhereClause(t *testing.T) {
	where, args := observationFilters("", "", "", "", "")
	if where != "" || args != nil {
		t.Errorf("observationFilters = %q, %v; want empty", where, args)
	}
}

func TestFilterValuesNeverReachTheSQLText(t *testing.T) {
	where, _ := observationFilters("GDP'; DROP TABLE x--", "", "", "", "")
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
