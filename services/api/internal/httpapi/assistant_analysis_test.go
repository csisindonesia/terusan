package httpapi

import (
	"fmt"
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

func TestASuffixedWordStillAsksForAChart(t *testing.T) {
	for _, question := range []string{"buatkan chartnya", "grafiknya dong", "bandingkanlah keduanya",
		"apa hubungannya?"} {
		if !wantsAnalysis(question) {
			t.Errorf("wantsAnalysis(%q) = false", question)
		}
	}
	if !asksRelation("apa pengaruhnya") {
		t.Error("pengaruhnya is not read as a relation")
	}
}

func TestAChartFollowUpChartsWhatTheLastReplyLinked(t *testing.T) {
	messages := []assistantMessage{
		{Role: "user", Content: "kurs terhadap inflasi, buatkan chartnya"},
		{Role: "assistant", Content: "Chart … [Kurs](/indicators/fx)"},
		{Role: "user", Content: "sekarang, berikan saya anggaran pendidikan dan jumlah penduduk sekolah"},
		{Role: "assistant", Content: "- [APBD expenditure, budgeted](/indicators/apbd)\n- [Population, age 7-24, total](/indicators/pop)"},
		{Role: "user", Content: "buatkan chartnya"},
	}
	earlier := earlierContext(messages)
	if len(earlier.questions) == 0 || earlier.questions[0] != messages[2].Content {
		t.Errorf("earlier questions = %q", earlier.questions)
	}
	if len(earlier.linked) != 2 || earlier.linked[0].id != "apbd" || earlier.linked[1].id != "pop" {
		t.Errorf("linked = %v, want the series of the reply before the latest question", earlier.linked)
	}

	catalogue := assistantCatalogue{series: []Indicator{
		indicator("apbd", "APBD expenditure, budgeted", "IDR", "monthly", 180),
		indicator("pop", "Population, age 7-24, total", "persons", "annual", 30),
		indicator("fx", "Exchange rate", "IDR per USD", "monthly", 700),
	}}
	// The latest question names nothing to chart ...
	if _, groups := analysisCandidateSeries(catalogue, "buatkan chartnya"); len(groups) != 0 {
		t.Fatalf("groups = %v for a question naming nothing", groups)
	}
	// ... so the chart is of what was just listed, one side each.
	candidates, groups, _ := earlier.candidates(catalogue)
	plan := defaultPlan(groups, candidates, "buatkan chartnya")
	if strings.Join(plan.Series, ",") != "apbd,pop" {
		t.Errorf("plan = %v, want the two linked series", plan.Series)
	}
}

func TestAnAskedForChartThatCannotBeDrawnIsNotDescribed(t *testing.T) {
	for _, want := range []string{"Never describe a chart", "state no figures"} {
		if !strings.Contains(noChartPrompt, want) {
			t.Errorf("no-chart prompt lacks %q", want)
		}
	}
}

func TestALinkUnderAMadeUpTitleIsNotCharted(t *testing.T) {
	catalogue := assistantCatalogue{series: []Indicator{
		indicator("cpi", "Inflation CPI", "%", "monthly", 200),
		indicator("apbd", "APBD expenditure, budgeted", "IDR", "monthly", 180),
	}}
	earlier := analysisContext{linked: []link{
		{id: "cpi", text: "Population, age 7-24, total"}, // the id of something else
		{id: "apbd", text: "**APBD expenditure, budgeted**"},
	}}
	candidates, _, _ := earlier.candidates(catalogue)
	if len(candidates) != 1 || candidates[0].IndicatorID != "apbd" {
		t.Errorf("candidates = %v, want only the link whose words are its title", candidates)
	}
}

func TestAChartOfTheNearestThingIsDeclined(t *testing.T) {
	catalogue := assistantCatalogue{series: []Indicator{
		indicator("gdp-edu", "GDP at Current Price from EDUCATION Industry", "Rp Billion", "quarterly", 66),
		indicator("poor", "Jumlah Penduduk Miskin (Ribu Jiwa) Menurut Provinsi", "ribu jiwa", "annual", 30),
		indicator("apbd", "APBD expenditure, budgeted", "IDR", "monthly", 180),
		indicator("pop", "Population, total", "persons", "annual", 60),
		indicator("fx", "US dollar / rupiah exchange rate, close", "IDR", "daily", 5000),
		indicator("cpi", "Inflation CPI", "%", "monthly", 200),
		indicator("exp", "Exports", "USD Million", "monthly", 400),
		indicator("imp", "Imports", "USD Million", "monthly", 400),
		indicator("food", "Food price — traditional market", "IDR/kg", "daily", 9000),
		indicator("school", "Ratio of Female to Male Primary School Enrollment", "Ratio", "annual", 40),
	}}
	chart := func(series ...chartSeries) *chartSpec { return &chartSpec{Series: series} }
	for _, c := range []struct {
		name, question string
		chart          *chartSpec
		declined       bool
	}{
		{"education GDP is not an education budget",
			"cek lagi, apakah kamu bisa buatkan chart anggaran pendidikan dengan jumlah penduduk",
			chart(chartSeries{ID: "gdp-edu", Label: "GDP at Current Price from EDUCATION Industry"},
				chartSeries{ID: "poor", Label: "Jumlah Penduduk Miskin (Ribu Jiwa) Menurut Provinsi"}), true},
		{"a regional budget is not an education budget",
			"anggaran pendidikan dan jumlah penduduk sekolah",
			chart(chartSeries{ID: "apbd", Label: "APBD expenditure, budgeted"},
				chartSeries{ID: "pop", Label: "Population, total"}), true},
		{"the rupiah against inflation",
			"saya ingin tau perbandingan antara mata uang idr ke usd terhadap inflasi",
			chart(chartSeries{ID: "fx", Label: "US dollar / rupiah exchange rate, close"},
				chartSeries{ID: "cpi", Label: "Inflation CPI"}), false},
		{"exports and imports", "compare Indonesia exports and imports",
			chart(chartSeries{ID: "exp", Label: "Exports"}, chartSeries{ID: "imp", Label: "Imports"}), false},
		{"a commodity is found in what was drawn", "tampilkan tren harga cabai rawit di jakarta",
			chart(chartSeries{ID: "food", Label: "Food price — traditional market",
				Member: "DKI Jakarta · Bird's eye chili — green"}), false},
		{"a budget alone", "grafik anggaran daerah",
			chart(chartSeries{ID: "apbd", Label: "APBD expenditure, budgeted"}), false},
	} {
		side := uncoveredSide(catalogue, c.chart, c.question)
		if (side != "") != c.declined {
			t.Errorf("%s: uncovered side = %q, want declined %v", c.name, side, c.declined)
		}
	}
}

func TestAMadeUpLinkInHistoryIsMarked(t *testing.T) {
	catalogue := assistantCatalogue{series: []Indicator{indicator("cpi", "Inflation CPI", "%", "monthly", 200)}}
	got := verifiedHistory([]assistantMessage{
		{Role: "user", Content: "[Population, age 7-24, total](/indicators/cpi)"},
		{Role: "assistant", Content: "- [Population, age 7-24, total](/indicators/cpi)\n- [Inflation CPI](/indicators/cpi)"},
	}, catalogue)
	if got[0].Content != "[Population, age 7-24, total](/indicators/cpi)" {
		t.Error("a reader's message was rewritten")
	}
	want := "- Population, age 7-24, total (not a series in this portal)\n- [Inflation CPI](/indicators/cpi)"
	if got[1].Content != want {
		t.Errorf("history = %q, want %q", got[1].Content, want)
	}
}

func TestALoopingReplyIsCaught(t *testing.T) {
	loop := "Namun, saya akan memakai link itu. " + strings.Repeat("Saya akan menggunakan link tersebut. ", 4)
	if !repeating(loop) {
		t.Error("a reply saying one sentence four times was not caught")
	}
	for _, fine := range []string{
		"- [Exports](/indicators/a)\n- [Imports](/indicators/b)\n- [Balance](/indicators/c)",
		"Nilai tukar naik 24,6%; inflasi naik 1,6 poin. Korelasi -0,14.",
	} {
		if repeating(fine) {
			t.Errorf("repeating(%q) = true", fine)
		}
	}
}

func TestAChartTurnDoesNotSeeEarlierReplies(t *testing.T) {
	got := withoutEarlierReplies([]assistantMessage{
		{Role: "user", Content: "buatkan chartnya"},
		{Role: "assistant", Content: "Chart … naik 2,5 kali lipat"},
		{Role: "user", Content: "cek lagi"},
	})
	if got[0].Content != "buatkan chartnya" || got[2].Content != "cek lagi" ||
		strings.Contains(got[1].Content, "2,5") {
		t.Errorf("history = %+v", got)
	}
}

func currencyCatalogue() assistantCatalogue {
	fx := "fx"
	oil := "oil"
	series := func(id, name, dataset string, tags ...string) Indicator {
		i := indicator(id, name, "", "daily", 2000)
		i.DatasetID, i.Tags = &dataset, tags
		return i
	}
	var all []Indicator
	for _, quote := range []string{"open", "high", "low", "close"} {
		all = append(all,
			series("brent-"+quote, "Brent crude price "+quote, oil, "oil", "energy"),
			series("usd-"+quote, "US dollar / rupiah exchange rate, "+quote, fx, "exchange-rate"),
			series("sgd-"+quote, "Singapore dollar / rupiah exchange rate, "+quote, fx, "exchange-rate"),
			series("myr-"+quote, "Malaysian ringgit / rupiah exchange rate, "+quote, fx, "exchange-rate"),
			series("thb-"+quote, "Thai baht / rupiah exchange rate, "+quote, fx, "exchange-rate"),
			series("eur-"+quote, "Euro / rupiah exchange rate, "+quote, fx, "exchange-rate"),
		)
	}
	return assistantCatalogue{
		series: all,
		datasets: []Dataset{
			{DatasetID: "oil", Title: ptrString("Brent crude oil price")},
			{DatasetID: "fx", Title: ptrString("Exchange rates")},
		},
	}
}

func ptrString(value string) *string { return &value }

func TestOilAgainstASEANCurrenciesFindsTheirCloses(t *testing.T) {
	catalogue := currencyCatalogue()
	question := "buatkan chart harga minyak dengan currency 5 negara asean + indonesia"
	candidates, groups := analysisCandidateSeries(catalogue, question)
	ids := map[string]bool{}
	for _, i := range candidates {
		ids[i.IndicatorID] = true
		if quoteVariant(i) {
			t.Errorf("%s offered beside its close", i.IndicatorID)
		}
	}
	for _, want := range []string{"brent-close", "sgd-close", "myr-close", "thb-close"} {
		if !ids[want] {
			t.Errorf("%s not offered; candidates %v", want, ids)
		}
	}
	if len(groups) < 2 {
		t.Errorf("groups = %d, want oil and currencies apart", len(groups))
	}

	plan := analysisPlan{Series: []string{"brent-close", "sgd-close", "myr-close", "thb-close", "eur-close"},
		Chart: "indexed"}
	byID := map[string]Indicator{}
	for _, i := range candidates {
		byID[i.IndicatorID] = i
	}
	// The euro was not offered for an ASEAN question, and is dropped.
	if chosen := checkPlan(&plan, byID); len(chosen) != 4 || plan.Chart != "indexed" {
		t.Errorf("%d series as %q, want four rebased", len(chosen), plan.Chart)
	}
	chart := &chartSpec{Series: []chartSeries{
		{ID: "brent-close", Label: "Brent crude price close"},
		{ID: "sgd-close", Label: "Singapore dollar / rupiah exchange rate, close"},
		{ID: "myr-close", Label: "Malaysian ringgit / rupiah exchange rate, close"},
	}}
	if side := uncoveredSide(catalogue, chart, question); side != "" {
		t.Errorf("oil and ASEAN currencies declined at %q", side)
	}
}

func TestAFollowUpLooksPastAQuestionThatNamesNothing(t *testing.T) {
	catalogue := currencyCatalogue()
	earlier := earlierContext([]assistantMessage{
		{Role: "user", Content: "buatkan chart harga minyak dengan currency 5 negara asean + indonesia"},
		{Role: "assistant", Content: "Tidak ada."},
		{Role: "user", Content: "ya maksud saya itu"},
		{Role: "assistant", Content: "- [Brent crude oil price](/datasets/oil)\n- [Exchange rates](/datasets/fx)"},
		{Role: "user", Content: "dimana chartnya"},
	})
	candidates, groups, asked := earlier.candidates(catalogue)
	if !strings.Contains(asked, "minyak") {
		t.Errorf("asked = %q, want the question that named something", asked)
	}
	if len(groups) < 2 || len(candidates) == 0 {
		t.Fatalf("groups = %d, candidates = %d", len(groups), len(candidates))
	}
	for _, i := range candidates {
		if quoteVariant(i) {
			t.Errorf("%s offered from a linked dataset beside its close", i.IndicatorID)
		}
	}
}

func TestMinyakAloneIsCrudeOil(t *testing.T) {
	catalogue := currencyCatalogue()
	palm := indicator("palm", "Palm oil price close", "", "daily", 2000)
	catalogue.series = append(catalogue.series, palm)
	titles := catalogueTitles(catalogue)
	var brent Indicator
	for _, i := range catalogue.series {
		if i.IndicatorID == "brent-close" {
			brent = i
		}
	}
	candidates := []Indicator{palm, brent}
	got := preferredChoice("buatkan chart harga minyak dengan currency asean", []Indicator{palm}, candidates, titles)
	if got[0].IndicatorID != "brent-close" {
		t.Errorf("minyak charted as %s, want Brent", got[0].IndicatorID)
	}
	kept := preferredChoice("harga minyak sawit", []Indicator{palm}, candidates, titles)
	if kept[0].IndicatorID != "palm" {
		t.Errorf("minyak sawit charted as %s, want palm oil", kept[0].IndicatorID)
	}
}

func TestACutOffPlanIsStillRead(t *testing.T) {
	plan, err := parsePlan("```json {\"series\":[\"0kp69xys\",\"4u2jcars\"],\"chart\":\"indexed\"," +
		"\"title\":\"Nilai Tukar\",\"reason\":\"Menggambarkan perba")
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Series) != 2 || plan.Chart != "indexed" || plan.Title != "Nilai Tukar" {
		t.Errorf("plan = %+v", plan)
	}
}

func TestAChartTurnDropsTheRuleThatSaysThereIsNothing(t *testing.T) {
	relaxed := analysisInstructions(assistantInstructions)
	if strings.Contains(relaxed, "If nothing listed is about the topic") {
		t.Error("the refusal rule was left in a turn with a chart")
	}
	if !strings.Contains(relaxed, "directly above your reply") {
		t.Error("the reply is not told where the chart is")
	}
	if shortLabel("Singapore dollar / rupiah exchange rate, close") != "Singapore dollar / rupiah" {
		t.Errorf("short label = %q", shortLabel("Singapore dollar / rupiah exchange rate, close"))
	}
}

func TestADenialBeneathAChartIsDropped(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"Chart yang Anda minta tidak tersedia di portal ini. Data yang tersedia hanya untuk [SGD](/indicators/a).",
			"Data yang tersedia hanya untuk [SGD](/indicators/a)."},
		{"The chart is not available. It shows exports rising.", "It shows exports rising."},
		{"Chart menunjukkan kurs naik 24,6%. Inflasi naik 1,6 poin.",
			"Chart menunjukkan kurs naik 24,6%. Inflasi naik 1,6 poin."},
		{"Tidak ada data untuk Filipina dan Vietnam. Grafik memuat tiga mata uang.",
			"Tidak ada data untuk Filipina dan Vietnam. Grafik memuat tiga mata uang."},
	} {
		if got, _ := withoutDenial(c.in); got != c.want {
			t.Errorf("withoutDenial(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestALinkUnderAnotherSeriesTitleIsUnlinkedWhenKept(t *testing.T) {
	catalogue := currencyCatalogue()
	got := verifiedReply("Lihat [Gold price close](/indicators/sgd-close), "+
		"[Singapore dollar / rupiah exchange rate, close](/indicators/sgd-close) dan "+
		"[Exchange rates](/datasets/fx).", catalogue)
	want := "Lihat Gold price close, [Singapore dollar / rupiah exchange rate, close](/indicators/sgd-close) dan " +
		"[Exchange rates](/datasets/fx)."
	if got != want {
		t.Errorf("kept reply = %q, want %q", got, want)
	}
}

func TestAProposalSaysWhatEachSeriesIsFor(t *testing.T) {
	catalogue := currencyCatalogue()
	chart := &chartSpec{
		Kind: "indexed", Title: "Minyak dan kurs ASEAN", Reason: "Satuan berbeda, jadi dibandingkan pertumbuhannya.",
		Granularity: "month", Periods: []string{"2021-09", "2021-10", "2021-11"},
		Series: []chartSeries{
			{ID: "brent-close", Label: "Brent crude price close", Source: "Yahoo Finance",
				Values: []*float64{ptr(77), ptr(80), ptr(82)}},
			{ID: "sgd-close", Label: "Singapore dollar / rupiah exchange rate, close", Unit: "IDR",
				Values: []*float64{nil, ptr(10600), ptr(10700)}},
		},
		asked: "buatkan chart harga minyak dengan currency 5 negara asean + indonesia",
	}
	p := proposalFrom(chart, catalogue, "id")
	if !p.Proposed || p.From != "2021-09" || p.To != "2021-11" || p.ConfirmText != "Ya, buat grafiknya." {
		t.Errorf("proposal = %+v", p)
	}
	if p.Series[0].For != "harga minyak" {
		t.Errorf("oil is for %q, want the words it answers", p.Series[0].For)
	}
	if p.Series[1].From != "2021-10" || p.Series[1].For != "currency asean" {
		t.Errorf("currency = %+v", p.Series[1])
	}
	text := proposalText(p, "id")
	for _, want := range []string{
		"[Brent crude price close](/indicators/brent-close)", "sumber: Yahoo Finance", "Untuk: *harga minyak*",
		"garis terindeks", "disamakan ke 100", "2021-09 sampai 2021-11, data bulanan", "Buat grafik",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("proposal text lacks %q:\n%s", want, text)
		}
	}
	// Figures are not in a proposal: nothing is described before it is drawn.
	if strings.Contains(text, "10600") || strings.Contains(text, "77") {
		t.Errorf("proposal states a figure:\n%s", text)
	}
}

func TestEveryThingAskedAboutGetsCandidates(t *testing.T) {
	var series []Indicator
	// Plenty of exchange rates and noise, and one inflation series last.
	for n := 0; n < 40; n++ {
		series = append(series, indicator(fmt.Sprintf("fx%d", n), fmt.Sprintf("Exchange rate to US dollar %d", n), "IDR", "monthly", 100))
		series = append(series, indicator(fmt.Sprintf("st%d", n), fmt.Sprintf("Status pekerjaan mata pencaharian %d", n), "", "annual", 20))
	}
	series = append(series, indicator("cpi", "Inflation CPI", "%", "monthly", 200))
	candidates, _ := analysisCandidateSeries(assistantCatalogue{series: series},
		"saya ingin tau perbandingan antara mata uang idr ke usd terhadap inflasi")
	found := false
	for _, i := range candidates {
		found = found || i.IndicatorID == "cpi"
		if strings.HasPrefix(i.IndicatorID, "st") {
			t.Errorf("%s offered for the words tau and mata", i.IndicatorID)
		}
	}
	if !found {
		t.Error("inflation was crowded out of the candidates")
	}
}
