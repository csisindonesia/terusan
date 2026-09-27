package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func searchSeries() []Indicator {
	str := func(v string) *string { return &v }
	return []Indicator{
		{IndicatorID: "a1", Name: str("Inflation CPI"), Resolution: "monthly", Unit: str("%"),
			Sources: []string{"tradingeconomics"}, Tags: []string{"prices", "inflation"},
			PeriodEnd: "2026-08", LastUpdated: str("2026-09-01T00:00:00Z"), DatasetID: str("d1")},
		{IndicatorID: "b2", Name: str("Exports"), Code: str("XTEXVA01IDM"), Resolution: "monthly",
			Unit: str("USD Million"), Sources: []string{"fred"}, Tags: []string{"trade"},
			PeriodEnd: "2026-07", LastUpdated: str("2026-09-20T00:00:00Z"), DatasetID: str("d2")},
		{IndicatorID: "c3", Name: str("Population, total"), Resolution: "annual", Unit: str("persons"),
			Sources: []string{"worldbank"}, Tags: []string{"population"}, PeriodEnd: "2025"},
	}
}

func queryFor(t *testing.T, raw string) indicatorQuery {
	t.Helper()
	q, _, err := readIndicatorQuery(httptest.NewRequest(http.MethodGet, "/v1/indicators?"+raw, nil))
	if err != nil {
		t.Fatal(err)
	}
	return q
}

func ids(series []Indicator) []string {
	out := make([]string, len(series))
	for n, i := range series {
		out[n] = i.IndicatorID
	}
	return out
}

func TestTheSeriesFiltersAreThePortals(t *testing.T) {
	for raw, want := range map[string][]string{
		"q=inflation":              {"a1"},       // the name
		"q=xtexva":                 {"b2"},       // the publisher's code
		"q=trade":                  {"b2"},       // a tag
		"q=worldbank":              {"c3"},       // a source
		"frequency=monthly":        {"b2", "a1"}, // newest first by default
		"unit=%25":                 {"a1"},       // "%", as a unit is written
		"tag=prices&tag=inflation": {"a1"},       // every tag, not any
		"tag=prices&tag=trade":     {},
		"id=c3,a1":                 {"a1", "c3"}, // the one with no update goes last
		"dataset=d2":               {"b2"},
		"sort=indicator_id":        {"b2", "a1", "c3"},
		"sort=coverage&dir=desc":   {"a1", "b2", "c3"},
	} {
		got := ids(queryFor(t, raw).apply(searchSeries()))
		if len(got) != len(want) {
			t.Errorf("%s: %v, want %v", raw, got, want)
			continue
		}
		for n := range got {
			if got[n] != want[n] {
				t.Errorf("%s: %v, want %v", raw, got, want)
				break
			}
		}
	}
}

func TestAnUnaskedListIsEverySeries(t *testing.T) {
	if _, asked, _ := readIndicatorQuery(httptest.NewRequest(http.MethodGet, "/v1/indicators", nil)); asked {
		t.Error("a bare request was read as a search")
	}
	if _, _, err := readIndicatorQuery(httptest.NewRequest(http.MethodGet, "/v1/indicators?sort=drop", nil)); err == nil {
		t.Error("an unknown sort column was accepted")
	}
}
