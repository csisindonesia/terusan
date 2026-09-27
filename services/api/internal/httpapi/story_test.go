package httpapi

import (
	"strings"
	"testing"
)

func currencyChart() *chartSpec {
	r := -0.14
	return &chartSpec{
		Kind: "dual_axis", Granularity: "month",
		Periods: []string{"2021-09", "2021-10", "2021-11", "2021-12", "2022-01"},
		Series: []chartSeries{
			{ID: "fx", Label: "US dollar / rupiah exchange rate, close", Unit: "IDR",
				Values: []*float64{ptr(14200), ptr(14300), ptr(15900), ptr(15800), ptr(17700)}},
			{ID: "cpi", Label: "Inflation CPI", Unit: "percent", Member: "Indonesia",
				Values: []*float64{ptr(1.6), ptr(1.7), ptr(2.1), ptr(1.9), ptr(3.2)}},
		},
		Correlation: &r, Overlap: 5,
	}
}

func TestTheHeadlineIsTheFindingWithItsNumber(t *testing.T) {
	story := tellStory(currencyChart(), "id")
	want := "US dollar / rupiah naik 24,6% sejak 2021-09, sementara Inflation CPI naik 1,6 poin; " +
		"tanpa hubungan yang jelas (r = −0,14)"
	if story.Headline != want {
		t.Errorf("headline = %q\nwant       %q", story.Headline, want)
	}
	english := tellStory(currencyChart(), "en").Headline
	if !strings.Contains(english, "rose 24.6% since 2021-09") || !strings.Contains(english, "rose 1.6 points") {
		t.Errorf("english headline = %q", english)
	}
}

func TestTheKeyFiguresAreTheLastValueAndTheChange(t *testing.T) {
	figures := tellStory(currencyChart(), "id").Figures
	if len(figures) != 2 || figures[0].Last != 17700 || figures[0].ChangeUnit != "percent" ||
		figures[1].ChangeUnit != "points" || figures[1].LastPeriod != "2022-01" {
		t.Errorf("figures = %+v", figures)
	}
}

func TestTheMarkedPointsArePeaksLowsAndTheSharpestMove(t *testing.T) {
	marks := tellStory(currencyChart(), "id").Annotations
	if len(marks) == 0 || len(marks) > maxAnnotations {
		t.Fatalf("%d marks", len(marks))
	}
	kinds := map[string]bool{}
	for n, mark := range marks {
		kinds[mark.Kind] = true
		if n > 0 && marks[n-1].Index > mark.Index {
			t.Error("marks are not in the order of the axis")
		}
	}
	if !kinds["peak"] || !kinds["low"] {
		t.Errorf("marks = %+v, want a peak and a low", marks)
	}
	scatter := currencyChart()
	scatter.Kind = "scatter"
	if marks := tellStory(scatter, "id").Annotations; len(marks) != 0 {
		t.Error("a scatter, with no time axis, was marked along one")
	}
}

func TestSeveralSeriesAreToldAsMostAgainstLeast(t *testing.T) {
	chart := currencyChart()
	chart.Kind = "indexed"
	chart.Correlation = nil
	chart.Series = append(chart.Series, chartSeries{ID: "sgd", Label: "Singapore dollar / rupiah exchange rate, close",
		Values: []*float64{ptr(10000), ptr(10100), ptr(10200), ptr(10300), ptr(10400)}})
	headline := tellStory(chart, "id").Headline
	if !strings.HasPrefix(headline, "US dollar / rupiah bergerak paling jauh (+24,6%)") ||
		!strings.Contains(headline, "Singapore dollar / rupiah paling sedikit (+4,0%)") {
		t.Errorf("headline = %q", headline)
	}
}

func TestNumbersAreWrittenTheReadersWay(t *testing.T) {
	if got := localNumber(17844.5, 1, true); got != "17.844,5" {
		t.Errorf("id = %q", got)
	}
	if got := localNumber(-1234567, 0, false); got != "−1,234,567" {
		t.Errorf("en = %q", got)
	}
}

func TestAHeadlineNamesASeriesShortly(t *testing.T) {
	for _, c := range []struct{ label, proposed, want string }{
		{"Rata-rata Harga Eceran Nasional Beberapa Jenis Barang — Cabai Rawit (kg)", "", "Cabai Rawit (kg)"},
		{"US dollar / rupiah exchange rate, close", "", "US dollar / rupiah"},
		{"Currency Conversions: US Dollar Exchange Rate: Average of Daily Rates", "Kurs dolar AS", "Kurs dolar AS"},
		{"Currency Conversions: US Dollar Exchange Rate: Average of Daily Rates", "", "Currency Conversions: US Dollar Exchange…"},
		// A proposed name carrying a number is not taken: numbers are the server's.
		{"Inflation CPI", "Inflasi 3,2%", "Inflation CPI"},
	} {
		if got := shortName(c.label, c.proposed); got != c.want {
			t.Errorf("shortName(%q, %q) = %q, want %q", c.label, c.proposed, got, c.want)
		}
	}
}
