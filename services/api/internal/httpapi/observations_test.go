package httpapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

// observationRequest builds a request whose parameters may repeat, which the
// list filters take and `request` in params_test.go cannot express.
func observationRequest(pairs ...string) *http.Request {
	values := url.Values{}
	for i := 0; i+1 < len(pairs); i += 2 {
		values.Add(pairs[i], pairs[i+1])
	}
	return httptest.NewRequest(http.MethodGet, "/v1/observations?"+values.Encode(), nil)
}

func TestTheNewFiltersReachTheWhereClause(t *testing.T) {
	// Every one of these was applied in the browser until the portal began
	// paging in the warehouse, where filtering a page filters whatever
	// twenty-five rows happened to arrive.
	f, err := observationParams(observationRequest(
		"indicator", "6yugbox8",
		"geo_name", "Aceh",
		"status", "missing",
		"year", "2019",
		"year", "2024",
	))
	if err != nil {
		t.Fatalf("observationParams: %v", err)
	}

	where, args := observationFilters(f)
	for _, want := range []string{
		// One value compares with `=` and several with `IN`, so the column is
		// what this asserts on.
		"coalesce(g.name, o.geo_name_raw) =",
		"o.status =",
		"CAST(year(o.period_start) AS VARCHAR) IN (?, ?)",
	} {
		if !strings.Contains(where, want) {
			t.Errorf("where clause %q is missing %q", where, want)
		}
	}
	// Indicator, place, status and two years.
	if len(args) != 5 {
		t.Errorf("bound %d values, want 5: %v", len(args), args)
	}
}

func TestYearsAreASetAndNotTheRangeTheySpan(t *testing.T) {
	// "2019 and 2024" is two years. Folded into `period_start`/`period_end` it
	// would be six, which is what the portal used to send.
	f, err := observationParams(observationRequest("year", "2019", "year", "2024"))
	if err != nil {
		t.Fatalf("observationParams: %v", err)
	}
	if len(f.Years) != 2 || f.Years[0] != "2019" || f.Years[1] != "2024" {
		t.Fatalf("years = %v, want [2019 2024]", f.Years)
	}
	if f.From != "" || f.Until != "" {
		t.Errorf("years leaked into the period bounds: %q–%q", f.From, f.Until)
	}
}

func TestAMonthIsNotAYear(t *testing.T) {
	// `periodPattern` would admit it; a year filter compares against the year
	// alone and would match nothing.
	if _, err := observationParams(observationRequest("year", "2019-06")); err == nil {
		t.Error("observationParams accepted year=2019-06")
	}
}

func TestTheTableCanBeOrderedByWhatTheReaderSees(t *testing.T) {
	// The figures table sorts on the printed name, not the identifier:
	// ordering thirty-four provinces by `ID-11`, `ID-12` puts them in an order
	// nobody reading "Aceh, Bali, Banten" can predict.
	for _, key := range []string{
		"period", "-period", "value", "-value", "place", "-place", "commodity", "-commodity",
	} {
		clause, err := sortOrder(observationRequest("order", key), observationOrder, "period")
		if err != nil || clause == "" {
			t.Errorf("order=%s: %q, %v", key, clause, err)
		}
	}
	// Order cannot be bound, so a caller naming its own clause is refused
	// rather than interpolated.
	if _, err := sortOrder(
		observationRequest("order", "o.value; DROP TABLE"), observationOrder, "period",
	); err == nil {
		t.Error("sortOrder accepted a clause of its own")
	}
}

func TestFreeTextFindsAPeriod(t *testing.T) {
	// The portal's search box offers "Search periods and places", and searched
	// the periods in the browser. The API now has to answer the same question.
	f, err := observationParams(observationRequest("q", "2026-09"))
	if err != nil {
		t.Fatalf("observationParams: %v", err)
	}
	where, args := observationFilters(f)
	if !strings.Contains(where, "o.period ILIKE ?") {
		t.Errorf("where clause %q does not search the period label", where)
	}
	if len(args) != 5 {
		t.Errorf("bound %d patterns, want one per searched column: %v", len(args), args)
	}
}

// A collection's route carries its series in the context, past the fifty a
// query string may name.
func TestACollectionScopeReplacesTheIndicatorFilter(t *testing.T) {
	scope := make([]string, 120)
	for i := range scope {
		scope[i] = "s" + strings.Repeat("0", 3) + string(rune('a'+i%26)) + string(rune('a'+i/26))
	}
	r := observationRequest("indicator", "ignored1")
	r = r.WithContext(context.WithValue(r.Context(), scopeKey{}, scope))
	f, err := observationParams(r)
	if err != nil {
		t.Fatalf("observationParams: %v", err)
	}
	if len(f.Indicators) != len(scope) || f.Indicators[0] != scope[0] {
		t.Fatalf("Indicators = %d, want the %d in scope", len(f.Indicators), len(scope))
	}
}
