package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAPricesFourSeriesFoldIntoItsClose(t *testing.T) {
	str := func(v string) *string { return &v }
	series := []Indicator{
		{IndicatorID: "a", Slug: str("ihsg_open"), DatasetID: str("d")},
		{IndicatorID: "b", Slug: str("ihsg_high"), DatasetID: str("d")},
		{IndicatorID: "cpi", Slug: str("cpi")},
		{IndicatorID: "c", Slug: str("ihsg_low"), DatasetID: str("d")},
		{IndicatorID: "e", Slug: str("ihsg_close"), DatasetID: str("d")},
		// Three of four is not a price, and stays as it is.
		{IndicatorID: "x", Slug: str("lq45_open"), DatasetID: str("d")},
		{IndicatorID: "y", Slug: str("lq45_high"), DatasetID: str("d")},
		{IndicatorID: "z", Slug: str("lq45_close"), DatasetID: str("d")},
	}
	folded := foldOHLC(series)
	var ids []string
	for _, i := range folded {
		ids = append(ids, i.IndicatorID)
	}
	want := []string{"cpi", "e", "x", "y", "z"}
	if len(ids) != len(want) {
		t.Fatalf("folded to %v, want %v", ids, want)
	}
	for at := range want {
		if ids[at] != want[at] {
			t.Fatalf("folded to %v, want %v", ids, want)
		}
	}
	set := folded[1].OHLC
	if set == nil || *set != (OHLC{Open: "a", High: "b", Low: "c", Close: "e"}) {
		t.Fatalf("close carries %+v", set)
	}
	if folded[0].OHLC != nil || folded[2].OHLC != nil {
		t.Fatal("a series outside a set carries one")
	}
	if high := ohlcSets(series)["b"]; high == nil || *high != *set {
		t.Fatal("the high does not know its set")
	}
}

func TestFoldIsAskedFor(t *testing.T) {
	if queryFor(t, "q=ihsg").fold {
		t.Fatal("folded without being asked")
	}
	q, asked, err := readIndicatorQuery(httptest.NewRequest(http.MethodGet, "/v1/indicators?fold=ohlc", nil))
	if err != nil || !q.fold {
		t.Fatal("fold=ohlc did not fold")
	}
	if asked {
		t.Fatal("folding alone paged the catalogue")
	}
}

func TestAFoldedPriceIsNamedAndFoundAsThePrice(t *testing.T) {
	str := func(v string) *string { return &v }
	var series []Indicator
	for _, field := range ohlcFields {
		series = append(series, Indicator{
			IndicatorID: field, Slug: str("ihsg_" + field), DatasetID: str("d"),
			Name: str("Jakarta Composite Index, " + field),
		})
	}
	folded := foldOHLC(series)
	if len(folded) != 1 || *folded[0].Name != "Jakarta Composite Index" {
		t.Fatalf("folded to %+v", folded)
	}
	for _, name := range []string{"Coking coal (DCE) price — close", "Gold price close"} {
		if got := closeSuffix.ReplaceAllString(name, ""); strings.HasSuffix(got, "close") || strings.HasSuffix(got, " ") {
			t.Errorf("%q named %q", name, got)
		}
	}
	if !queryFor(t, "q=ihsg+open").matches(folded[0]) {
		t.Fatal("a folded price does not answer to its open")
	}
	if n := foldedCount([]string{"open", "high", "low", "close", "cpi"}, ohlcSets(series)); n != 2 {
		t.Fatalf("counted %d series, want 2", n)
	}
	if ids := foldedIDs([]string{"open", "high", "low", "close", "cpi"}, ohlcSets(series)); strings.Join(ids, ",") != "close,cpi" {
		t.Fatalf("folded to %v, want close and cpi", ids)
	}
}
