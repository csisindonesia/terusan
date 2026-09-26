package httpapi

import (
	"math"
	"strings"
	"testing"
)

func ptr(value float64) *float64 { return &value }

func indicator(id, name, unit, resolution string, observations int64) Indicator {
	return Indicator{
		IndicatorID: id, Name: &name, Unit: &unit, Resolution: resolution,
		Observations: observations, PeriodStart: "2000", PeriodEnd: "2025",
	}
}

func TestAComparisonOrAChartIsAnAnalysis(t *testing.T) {
	for question, want := range map[string]bool{
		"saya ingin tau perbandingan antara mata uang idr ke usd terhadap inflasi": true,
		"korelasi kurs dan inflasi":    true,
		"tampilkan grafik harga beras": true,
		"compare exports vs imports":   true,
		"data inflasi provinsi":        false,
		"cari dataset kebencanaan":     false,
		// About the law, however it is phrased.
		"perbandingan perda pajak daerah":      false,
		"analisis peraturan kpu soal kampanye": false,
	} {
		if got := wantsAnalysis(question); got != want {
			t.Errorf("wantsAnalysis(%q) = %v, want %v", question, got, want)
		}
	}
}

func TestTheCurrencyQuestionFindsBothSides(t *testing.T) {
	// "idr ke usd terhadap inflasi" must offer exchange rates as well as the
	// inflation series, which outnumber them.
	catalogue := assistantCatalogue{series: []Indicator{
		indicator("fx", "Exchange Rate to U.S. Dollar for Indonesia", "IDR per USD", "monthly", 700),
		indicator("cpi1", "Inflation, consumer prices for Indonesia", "%", "annual", 66),
		indicator("cpi2", "Inflasi Bulanan (M-to-M)", "%", "monthly", 572),
		indicator("cpi3", "Inflasi Tahunan (Y-on-Y) 150 Kabupaten/Kota", "%", "monthly", 900),
		indicator("rice", "Food price — rice", "IDR/kg", "daily", 5000),
	}}
	candidates, groups := analysisCandidateSeries(catalogue,
		"perbandingan antara mata uang idr ke usd terhadap inflasi")
	ids := map[string]bool{}
	for _, i := range candidates {
		ids[i.IndicatorID] = true
	}
	if !ids["fx"] || !ids["cpi1"] {
		t.Fatalf("candidates = %v, want the exchange rate and inflation", ids)
	}
	if ids["rice"] {
		t.Errorf("rice price offered for a currency question")
	}
	plan := defaultPlan(groups, candidates, "perbandingan idr ke usd terhadap inflasi")
	if len(plan.Series) != 2 {
		t.Fatalf("default plan = %v, want one series for each side", plan.Series)
	}
}

func TestThePlannersJSONIsFoundInsideProse(t *testing.T) {
	plan, err := parsePlan("Here is the plan:\n```json\n" +
		`{"series":["fx","cpi1"],"chart":"dual_axis","title":"Kurs vs inflasi","reason":"Dua satuan berbeda."}` +
		"\n```")
	if err != nil {
		t.Fatal(err)
	}
	if plan.Chart != "dual_axis" || len(plan.Series) != 2 || plan.Title != "Kurs vs inflasi" {
		t.Errorf("plan = %+v", plan)
	}
	if _, err := parsePlan("I cannot help with that."); err == nil {
		t.Error("a reply without JSON was read as a plan")
	}
}

func TestAPlanIsHeldToWhatWasOfferedAndWhatTheChartCanDraw(t *testing.T) {
	offered := map[string]Indicator{
		"fx":   indicator("fx", "Exchange rate", "IDR per USD", "monthly", 700),
		"cpi":  indicator("cpi", "Inflation", "%", "monthly", 500),
		"core": indicator("core", "Core inflation", "%", "monthly", 200),
		"gdp":  indicator("gdp", "GDP", "IDR bn", "annual", 60),
	}
	for _, c := range []struct {
		name   string
		plan   analysisPlan
		series int
		kind   string
	}{
		{"an invented id is dropped", analysisPlan{Series: []string{"fx", "made-up"}, Chart: "dual_axis"}, 1, "line"},
		{"two units on two axes", analysisPlan{Series: []string{"fx", "cpi"}, Chart: "dual_axis"}, 2, "dual_axis"},
		{"one unit shares an axis", analysisPlan{Series: []string{"cpi", "core"}, Chart: "dual_axis"}, 2, "line"},
		{"a line in two units is split", analysisPlan{Series: []string{"fx", "cpi"}, Chart: "line"}, 2, "dual_axis"},
		{"a line in one unit stays", analysisPlan{Series: []string{"cpi", "core"}, Chart: "line"}, 2, "line"},
		{"a scatter needs exactly two", analysisPlan{Series: []string{"fx", "cpi", "gdp"}, Chart: "scatter"}, 3, "indexed"},
		{"three units are rebased", analysisPlan{Series: []string{"fx", "cpi", "gdp"}, Chart: "line"}, 3, "indexed"},
		{"a bar of several is a line", analysisPlan{Series: []string{"cpi", "core"}, Chart: "bar"}, 2, "line"},
		{"an unknown kind is chosen for", analysisPlan{Series: []string{"gdp"}, Chart: "pie"}, 1, "line"},
		{"duplicates count once", analysisPlan{Series: []string{"fx", "fx"}, Chart: "scatter"}, 1, "line"},
	} {
		plan := c.plan
		chosen := checkPlan(&plan, offered)
		if len(chosen) != c.series || plan.Chart != c.kind {
			t.Errorf("%s: %d series as %q, want %d as %q", c.name, len(chosen), plan.Chart, c.series, c.kind)
		}
		if plan.Title == "" {
			t.Errorf("%s: no title", c.name)
		}
	}
}

func TestCorrelationIsReadOverTheSharedPeriodsOnly(t *testing.T) {
	a := []*float64{ptr(1), ptr(2), nil, ptr(4), ptr(5)}
	b := []*float64{ptr(2), ptr(4), ptr(100), ptr(8), nil}
	r, n := correlation(a, b)
	if n != 3 || math.Abs(r-1) > 1e-9 {
		t.Errorf("r = %v over %d, want 1 over 3", r, n)
	}
	inverse := []*float64{ptr(5), ptr(4), ptr(3)}
	if r, _ := correlation([]*float64{ptr(1), ptr(2), ptr(3)}, inverse); math.Abs(r+1) > 1e-9 {
		t.Errorf("r = %v, want -1", r)
	}
	if _, n := correlation([]*float64{ptr(1), ptr(1), ptr(1)}, inverse); n != 0 {
		t.Error("a flat series was given a correlation")
	}
}

func TestTheReplyIsGivenTheChartsFiguresAndLeaveToUseThem(t *testing.T) {
	r := 0.42
	chart := &chartSpec{
		Kind: "dual_axis", Title: "Kurs vs inflasi", Granularity: "year",
		Periods: []string{"2020", "2021", "2022"},
		Series: []chartSeries{
			{ID: "fx", Label: "Exchange rate", Unit: "IDR per USD", Member: "Indonesia",
				Values: []*float64{ptr(14582.2), ptr(14308.1), ptr(14849.9)}},
			{ID: "cpi", Label: "Inflation", Unit: "%", Values: []*float64{ptr(1.92), nil, ptr(4.21)}},
		},
		Correlation: &r, Overlap: 2,
	}
	prompt := chartPrompt(chart)
	for _, want := range []string{
		"[Exchange rate](/indicators/fx)", "first 14582 in 2020", "high 14850 in 2022",
		"last 4.21 in 2022", "+2.29 percentage points", "+1.8%", "Pearson r", "0.42", "does not show that one causes",
	} {
		if !strings.Contains(prompt, want) {
			t.Errorf("chart prompt lacks %q:\n%s", want, prompt)
		}
	}
	relaxed := analysisInstructions(assistantInstructions)
	if strings.Contains(relaxed, "Never state a figure") {
		t.Error("the rule against figures was left in an analysis turn")
	}
	if !strings.Contains(assistantInstructions, "Never state a figure") {
		t.Error("the base instructions lost the rule against figures")
	}
}

func TestAChartedSeriesCanBeLinked(t *testing.T) {
	sources := withChartSources([]assistantSource{{Kind: "indicator", ID: "fx"}},
		&chartSpec{Series: []chartSeries{{ID: "fx"}, {ID: "cpi", Label: "Inflation"}}})
	if len(sources) != 2 || sources[1].ID != "cpi" {
		t.Errorf("sources = %+v", sources)
	}
}
