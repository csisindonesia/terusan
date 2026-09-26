package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// Analysis: a question that asks to compare, relate or chart series is
// answered with a chart as well as words.
//
// The model chooses, the server measures. The writing model is shown the
// series the portal's own search found and asked for a plan — which one to
// three series answer the question, and which of a handful of chart kinds
// shows them best. The plan is checked against what was offered and against
// what each kind can draw, the figures are read from the warehouse here, and
// the chart goes to the portal as one event:
//
//	data: {"type":"chart","chart":{…}}      before the first delta
//
// The reply is then written with the chart's own summary in its prompt — the
// first and last figure of each series, its low and high, and the correlation
// where there are two — and with leave to state those figures and no others.
// A model that has not seen a number cannot misquote it; one that has seen
// only these can be checked against the chart beside its words.
//
// Nothing here is a dependency of the answer. A planner that fails or says
// something unusable falls back to a rule; series with too few figures are
// dropped; and where nothing is left to draw the turn is answered as before.

const (
	// How many series the planner chooses among, and how many per word of
	// the question, so "kurs terhadap inflasi" offers exchange rates as well
	// as the thirty inflation series that would otherwise fill the list.
	analysisCandidates = 24
	analysisPerTerm    = 6
	// How many series one chart may carry: past four, lines in different
	// units stop being comparable at a glance even rebased.
	analysisMaxSeries = 4
	// How many periods a chart is drawn with. Thirty years of months; a
	// longer run keeps its most recent part, which is what a comparison is
	// usually about.
	analysisMaxPeriods = 360
	// A comparison drawn over fewer common periods than this says nothing.
	analysisMinOverlap = 3
	// How long the planner and the figures may take together before the turn
	// goes ahead without a chart.
	analysisTimeout = 10 * time.Second
)

// The chart kinds the portal draws, and what each is for. Said to the
// planner in these words.
var chartKinds = map[string]string{
	"line":      "one series over time, or two or three in the same unit",
	"dual_axis": "exactly two series in different units over time, each on its own axis (e.g. an exchange rate in rupiah against inflation in percent)",
	"indexed":   "two to four series in different units where the question is about relative growth; each is rebased to 100 at the first common period",
	"scatter":   "exactly two series, when the question asks how one relates to, moves with or affects the other; one dot per period",
	"bar":       "one series with few periods (a dozen or two of annual or quarterly figures), comparing magnitudes",
}

// chartSpec is what the portal draws: the periods along the bottom, and each
// series' figure at each of them.
type chartSpec struct {
	Kind        string        `json:"kind"`
	Title       string        `json:"title"`
	Reason      string        `json:"reason,omitempty"`
	Granularity string        `json:"granularity"` // month, quarter or year
	Periods     []string      `json:"periods"`
	Series      []chartSeries `json:"series"`
	// Pearson's r over the periods where both of two series have a figure,
	// and how many those are.
	Correlation *float64 `json:"correlation,omitempty"`
	Overlap     int      `json:"overlap,omitempty"`
}

type chartSeries struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	Unit  string `json:"unit,omitempty"`
	// The place, or the commodity, the figures are for where the series has
	// several: "Indonesia", "DKI Jakarta", "Beras".
	Member string `json:"member,omitempty"`
	// Null where the period has no figure: the line breaks there.
	Values []*float64 `json:"values"`
}

// analysisWords are what a reader says when they want figures set against
// each other or drawn, rather than pointed at.
var analysisWords = func() map[string]bool {
	set := map[string]bool{}
	for _, word := range strings.Fields(`bandingkan banding perbandingan membandingkan dibandingkan
		bandingin komparasi korelasi berkorelasi hubungan pengaruh mempengaruhi memengaruhi dampak
		tren trend grafik grafiknya chart plot plotkan visualisasi visualisasikan gambarkan
		analisis analisa analisislah pergerakan perkembangan
		versus vs compare comparison correlation correlate relationship relation impact effect
		graph visualize visualise visualization analyse analyze analysis`) {
		set[word] = true
	}
	return set
}()

// Words among those that ask how two things relate, rather than how they
// moved: a reason to draw one against the other.
var relationWords = map[string]bool{
	"korelasi": true, "berkorelasi": true, "hubungan": true, "pengaruh": true,
	"mempengaruhi": true, "memengaruhi": true, "dampak": true, "correlation": true,
	"correlate": true, "relationship": true, "relation": true, "impact": true, "effect": true,
}

// wantsAnalysis reports whether the latest question asks for a comparison or
// a chart. A question that names a regulation is about the law, not a chart,
// whatever else it says.
func wantsAnalysis(question string) bool {
	asked := false
	for _, word := range questionWords(question) {
		if regulationWords[word] || instrumentWords[word] != "" {
			return false
		}
		if analysisWords[word] {
			asked = true
		}
	}
	return asked
}

func asksRelation(question string) bool {
	for _, word := range questionWords(question) {
		if relationWords[word] {
			return true
		}
	}
	return false
}

func questionWords(text string) []string {
	return strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	})
}

// analysisCandidateSeries is the series a chart may be drawn from: the best few
// for each word of the question, then the best for all of them, so that each
// side of a comparison is on the list.
func analysisCandidateSeries(catalogue assistantCatalogue, question string) ([]Indicator, [][]Indicator) {
	titles := make(map[string]string, len(catalogue.datasets))
	for _, d := range catalogue.datasets {
		titles[d.DatasetID] = datasetTitle(d)
	}
	var out []Indicator
	var groups [][]Indicator
	seen := map[string]bool{}
	add := func(series []Indicator, terms []string) []Indicator {
		var added []Indicator
		for _, i := range series {
			if len(out) >= analysisCandidates {
				break
			}
			// Only what can be drawn over time, and only what is named for
			// the question: a unit of "IDR/kg" is not a reason to chart rice
			// for a question about the rupiah.
			if i.Observations < 2 || !namedFor(i, titles, terms) {
				continue
			}
			added = append(added, i)
			if !seen[i.IndicatorID] {
				seen[i.IndicatorID] = true
				out = append(out, i)
			}
		}
		return added
	}
	for _, word := range questionWords(question) {
		if analysisWords[word] {
			continue
		}
		terms := searchTerms(word)
		if len(terms) == 0 {
			continue
		}
		ranked := rankSeries(catalogue.series, titles, terms)
		if len(ranked) > analysisPerTerm {
			ranked = ranked[:analysisPerTerm]
		}
		if group := add(ranked, terms); len(group) > 0 {
			groups = append(groups, group)
		}
	}
	all := searchTerms(question)
	add(rankSeries(catalogue.series, titles, all), all)
	return out, groups
}

// namedFor reports whether a series' name, tags or dataset carry one of the
// terms — the fields rankSeries weighs most, without its description and unit.
func namedFor(i Indicator, titles map[string]string, terms []string) bool {
	named := strings.ToLower(indicatorTitle(i)) + " " + lower(i.Slug, i.Code) + " " +
		strings.ToLower(strings.Join(i.Tags, " "))
	if i.DatasetID != nil {
		named += " " + strings.ToLower(titles[*i.DatasetID])
	}
	for _, term := range terms {
		if strings.Contains(named, term) {
			return true
		}
	}
	return false
}

// analysisPlan is what the planner chose.
type analysisPlan struct {
	Series []string `json:"series"`
	Chart  string   `json:"chart"`
	Title  string   `json:"title"`
	Reason string   `json:"reason"`
}

// analyse plans a chart for the question and reads its figures, or returns
// nil where there is nothing worth drawing.
func (s *Server) analyse(
	ctx context.Context, catalogue assistantCatalogue, question, language string,
) *chartSpec {
	candidates, groups := analysisCandidateSeries(catalogue, question)
	if len(candidates) == 0 {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, analysisTimeout)
	defer cancel()

	plan, err := s.planChart(ctx, candidates, question, language)
	if err != nil {
		s.log.Warn("assistant.plan_failed", "error", err)
		plan = defaultPlan(groups, candidates, question)
	}
	byID := make(map[string]Indicator, len(candidates))
	for _, i := range candidates {
		byID[i.IndicatorID] = i
	}
	chosen := checkPlan(&plan, byID)
	if len(chosen) == 0 {
		plan = defaultPlan(groups, candidates, question)
		if chosen = checkPlan(&plan, byID); len(chosen) == 0 {
			return nil
		}
	}

	chart, err := s.chartFigures(ctx, plan, chosen, searchTerms(question))
	if err != nil {
		s.log.Warn("assistant.chart_failed", "error", err)
		return nil
	}
	if chart != nil {
		s.log.Info("assistant.charted", "kind", chart.Kind, "series", len(chart.Series),
			"periods", len(chart.Periods))
	}
	return chart
}

// planChart asks the writing model, briefly and without streaming, which
// series and which chart answer the question.
func (s *Server) planChart(
	ctx context.Context, candidates []Indicator, question, language string,
) (analysisPlan, error) {
	var b strings.Builder
	b.WriteString("You choose the chart that best answers a reader's question about Indonesian data, " +
		"from the series listed below. Reply with one JSON object and nothing else:\n" +
		`{"series":["<id>", …],"chart":"<kind>","title":"<chart title>","reason":"<why this chart>"}` +
		"\n\nChart kinds:\n")
	kinds := make([]string, 0, len(chartKinds))
	for kind := range chartKinds {
		kinds = append(kinds, kind)
	}
	sort.Strings(kinds)
	for _, kind := range kinds {
		fmt.Fprintf(&b, "- %s: %s\n", kind, chartKinds[kind])
	}
	b.WriteString("\nChoose 1 to 3 series by their id, exactly as listed. Prefer national figures over " +
		"regional ones, series whose coverage overlaps the longest, the same frequency where there is a " +
		"choice, and the measure the question names: inflation as a rate rather than a price index, an " +
		"exchange rate against the US dollar for rupiah to dollar. Leave out anything only loosely related.\n")
	if language == "id" {
		b.WriteString("Write the title and the reason in Indonesian. ")
	} else {
		b.WriteString("Write the title and the reason in English. ")
	}
	b.WriteString("The title names what is compared in under ten words, without years or dates (the span is shown on the chart); the reason is one sentence " +
		"under twenty words on why this kind of chart suits the question.\n\nSeries (id | name | frequency | unit | coverage | places):\n")
	for _, i := range candidates {
		unit := "—"
		if i.Unit != nil && *i.Unit != "" {
			unit = *i.Unit
		}
		places := "national"
		if i.Geographies > 1 {
			places = fmt.Sprintf("%d places", i.Geographies)
		}
		fmt.Fprintf(&b, "- %s | %s | %s | %s | %s to %s | %s\n", i.IndicatorID,
			clip(indicatorTitle(i), 140), i.Resolution, clip(unit, 40), i.PeriodStart, i.PeriodEnd, places)
	}

	content, err := s.completeAssistantModel(ctx, []assistantMessage{
		{Role: "system", Content: b.String()},
		// Clipped, and set apart as the reader's words: it is data for the
		// planner, not instructions to it.
		{Role: "user", Content: "Question: " + clip(question, 600)},
	}, 300)
	if err != nil {
		return analysisPlan{}, err
	}
	return parsePlan(content)
}

// parsePlan reads the planner's JSON, which a small model wraps in prose or
// a code fence as often as not.
func parsePlan(content string) (analysisPlan, error) {
	start, end := strings.Index(content, "{"), strings.LastIndex(content, "}")
	if start < 0 || end <= start {
		return analysisPlan{}, fmt.Errorf("planner reply has no JSON: %s", clip(content, 200))
	}
	var plan analysisPlan
	if err := json.Unmarshal([]byte(content[start:end+1]), &plan); err != nil {
		return analysisPlan{}, fmt.Errorf("planner reply: %w", err)
	}
	return plan, nil
}

// checkPlan keeps the series that were offered and makes the chart one that
// can draw them, and returns the series in the order chosen.
func checkPlan(plan *analysisPlan, offered map[string]Indicator) []Indicator {
	var chosen []Indicator
	seen := map[string]bool{}
	for _, id := range plan.Series {
		i, ok := offered[strings.TrimSpace(id)]
		if !ok || seen[i.IndicatorID] || len(chosen) >= analysisMaxSeries {
			continue
		}
		seen[i.IndicatorID] = true
		chosen = append(chosen, i)
	}
	plan.Chart = chartFor(plan.Chart, chosen)
	plan.Title = clip(plan.Title, 90)
	plan.Reason = clip(plan.Reason, 200)
	if plan.Title == "" {
		names := make([]string, len(chosen))
		for n, i := range chosen {
			names[n] = clip(indicatorTitle(i), 40)
		}
		plan.Title = strings.Join(names, " · ")
	}
	return chosen
}

// chartFor is the kind asked for where it can draw the series, and the
// nearest one that can where it cannot.
func chartFor(kind string, series []Indicator) string {
	sameUnit := true
	for _, i := range series[min(1, len(series)):] {
		if unitOf(i) != unitOf(series[0]) {
			sameUnit = false
		}
	}
	switch n := len(series); {
	case n == 0:
		return ""
	case n == 1:
		if kind == "bar" {
			return "bar"
		}
		return "line"
	case kind == "scatter" && n == 2:
		return "scatter"
	case kind == "scatter":
		return "indexed"
	case kind == "indexed":
		return "indexed"
	case kind == "dual_axis" && n == 2 && !sameUnit:
		return "dual_axis"
	case sameUnit:
		return "line"
	case n == 2:
		return "dual_axis"
	default:
		return "indexed"
	}
}

func unitOf(i Indicator) string {
	if i.Unit == nil {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(*i.Unit))
}

// defaultPlan is the rule for when the planner cannot be asked: the best
// series for each of the first two things the question names.
func defaultPlan(groups [][]Indicator, candidates []Indicator, question string) analysisPlan {
	var plan analysisPlan
	seen := map[string]bool{}
	for _, group := range groups {
		for _, i := range group {
			if !seen[i.IndicatorID] {
				seen[i.IndicatorID] = true
				plan.Series = append(plan.Series, i.IndicatorID)
				break
			}
		}
		if len(plan.Series) == 2 {
			break
		}
	}
	if len(plan.Series) == 0 && len(candidates) > 0 {
		plan.Series = []string{candidates[0].IndicatorID}
	}
	if len(plan.Series) == 2 && asksRelation(question) {
		plan.Chart = "scatter"
	}
	return plan
}

// chartFigures reads each chosen series from the warehouse at one shared
// granularity and lines them up period by period.
func (s *Server) chartFigures(
	ctx context.Context, plan analysisPlan, chosen []Indicator, terms []string,
) (*chartSpec, error) {
	granularity := "month"
	for _, i := range chosen {
		if coarser(granularityOf(i.Resolution), granularity) {
			granularity = granularityOf(i.Resolution)
		}
	}

	type read struct {
		series  chartSeries
		figures map[string]float64
	}
	var reads []read
	for _, i := range chosen {
		member, figures, err := s.seriesFigures(ctx, i.IndicatorID, granularity, terms)
		if err != nil {
			return nil, err
		}
		if len(figures) < 2 {
			continue // too little to draw
		}
		unit := ""
		if i.Unit != nil {
			unit = *i.Unit
		}
		reads = append(reads, read{
			chartSeries{ID: i.IndicatorID, Label: indicatorTitle(i), Unit: unit, Member: member},
			figures,
		})
	}
	if len(reads) == 0 {
		return nil, nil
	}

	// Every period any series has, in order; then, for a comparison, only
	// the span they share, where they share enough of one.
	all := map[string]bool{}
	for _, r := range reads {
		for period := range r.figures {
			all[period] = true
		}
	}
	periods := make([]string, 0, len(all))
	for period := range all {
		periods = append(periods, period)
	}
	sort.Strings(periods)
	if len(reads) > 1 {
		first, last := "", ""
		for n, r := range reads {
			lo, hi := spanOf(r.figures)
			if n == 0 || lo > first {
				first = lo
			}
			if n == 0 || hi < last {
				last = hi
			}
		}
		var shared []string
		for _, period := range periods {
			if period >= first && period <= last {
				shared = append(shared, period)
			}
		}
		if len(shared) >= analysisMinOverlap {
			periods = shared
		}
	}
	if len(periods) > analysisMaxPeriods {
		periods = periods[len(periods)-analysisMaxPeriods:]
	}

	chart := &chartSpec{
		Title:       plan.Title,
		Reason:      plan.Reason,
		Granularity: granularity,
		Periods:     periods,
	}
	chosenKept := make([]Indicator, 0, len(reads))
	byID := map[string]Indicator{}
	for _, i := range chosen {
		byID[i.IndicatorID] = i
	}
	for _, r := range reads {
		r.series.Values = make([]*float64, len(periods))
		for n, period := range periods {
			if value, ok := r.figures[period]; ok {
				r.series.Values[n] = &value
			}
		}
		chart.Series = append(chart.Series, r.series)
		chosenKept = append(chosenKept, byID[r.series.ID])
	}
	// A series dropped for want of figures can leave a kind that no longer
	// fits what is left.
	chart.Kind = chartFor(plan.Chart, chosenKept)
	if chart.Kind == "bar" && len(periods) > 24 {
		chart.Kind = "line"
	}
	if len(chart.Series) == 2 {
		if r, n := correlation(chart.Series[0].Values, chart.Series[1].Values); n >= analysisMinOverlap {
			chart.Correlation, chart.Overlap = &r, n
		}
	}
	return chart, nil
}

// seriesFigures is one series' figures by period, for the one place or
// commodity it is drawn for: the one the question names, or else the
// national figure, or else the one with the most figures.
func (s *Server) seriesFigures(
	ctx context.Context, indicator, granularity string, terms []string,
) (string, map[string]float64, error) {
	from, err := s.observationSource(ctx, []string{indicator})
	if err != nil {
		return "", nil, err
	}
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT o.geo_id, %s AS place, %s AS commodity, count(*) AS n
		FROM %s WHERE o.indicator_id = ? AND o.value IS NOT NULL
		GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 2000`,
		memberGeoExpr, memberCommodityExpr, from), indicator)
	if err != nil {
		return "", nil, err
	}
	type member struct {
		geo, place, commodity *string
		n                     int64
		score                 int
	}
	var best *member
	for rows.Next() {
		var m member
		if err := rows.Scan(&m.geo, &m.place, &m.commodity, &m.n); err != nil {
			rows.Close()
			return "", nil, err
		}
		for _, term := range terms {
			if len([]rune(term)) >= 4 && (strings.Contains(lower(m.place), term) ||
				strings.Contains(lower(m.commodity), term)) {
				m.score += 4
			}
		}
		if m.geo != nil && *m.geo == "IDN" {
			m.score += 2
		}
		if m.geo == nil {
			m.score++
		}
		// The first of equals has the most figures: the rows come that way.
		if best == nil || m.score > best.score {
			held := m
			best = &held
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return "", nil, err
	}
	if best == nil {
		return "", nil, nil
	}

	var label []string
	if best.place != nil {
		label = append(label, *best.place)
	}
	if best.commodity != nil {
		label = append(label, *best.commodity)
	}

	figures := map[string]float64{}
	query := fmt.Sprintf(`
		SELECT %s AS bucket, avg(CAST(o.value AS DOUBLE))
		FROM %s
		WHERE o.indicator_id = ? AND o.value IS NOT NULL
		  AND o.geo_id IS NOT DISTINCT FROM ?
		  AND %s IS NOT DISTINCT FROM ?
		  AND %s IS NOT DISTINCT FROM ?
		GROUP BY 1 ORDER BY 1`,
		bucketExpr(granularity), from, memberGeoExpr, memberCommodityExpr)
	figureRows, err := s.warehouse.DB().QueryContext(ctx, query,
		indicator, best.geo, best.place, best.commodity)
	if err != nil {
		return "", nil, err
	}
	defer figureRows.Close()
	for figureRows.Next() {
		var period string
		var value *float64
		if err := figureRows.Scan(&period, &value); err != nil {
			return "", nil, err
		}
		if value != nil && !math.IsNaN(*value) && !math.IsInf(*value, 0) {
			figures[period] = *value
		}
	}
	return strings.Join(label, " · "), figures, figureRows.Err()
}

// granularityOf is the bucket a series' own resolution is drawn in. A daily
// series is drawn by month: a comparison over years has no room for days.
func granularityOf(resolution string) string {
	switch resolution {
	case "annual":
		return "year"
	case "quarterly":
		return "quarter"
	default:
		return "month"
	}
}

func coarser(a, b string) bool {
	rank := map[string]int{"month": 0, "quarter": 1, "year": 2}
	return rank[a] > rank[b]
}

func spanOf(figures map[string]float64) (string, string) {
	first, last := "", ""
	for period := range figures {
		if first == "" || period < first {
			first = period
		}
		if period > last {
			last = period
		}
	}
	return first, last
}

// correlation is Pearson's r over the periods where both have a figure.
func correlation(a, b []*float64) (float64, int) {
	var xs, ys []float64
	for n := range a {
		if n < len(b) && a[n] != nil && b[n] != nil {
			xs, ys = append(xs, *a[n]), append(ys, *b[n])
		}
	}
	if len(xs) < 2 {
		return 0, len(xs)
	}
	var mx, my float64
	for n := range xs {
		mx += xs[n]
		my += ys[n]
	}
	mx /= float64(len(xs))
	my /= float64(len(ys))
	var sxy, sxx, syy float64
	for n := range xs {
		dx, dy := xs[n]-mx, ys[n]-my
		sxy += dx * dy
		sxx += dx * dx
		syy += dy * dy
	}
	if sxx == 0 || syy == 0 {
		return 0, 0 // a flat series correlates with nothing
	}
	return math.Round(sxy/math.Sqrt(sxx*syy)*1000) / 1000, len(xs)
}

// chartPrompt is what the writing model is told about the chart the reader
// sees above its reply: the only figures it may state.
func chartPrompt(chart *chartSpec) string {
	var b strings.Builder
	fmt.Fprintf(&b, "\n## Chart shown above your reply\nThe reader sees a %s chart, %q, of %s figures from %s to %s.\n",
		strings.ReplaceAll(chart.Kind, "_", "-"), chart.Title, map[string]string{
			"month": "monthly", "quarter": "quarterly", "year": "annual",
		}[chart.Granularity], chart.Periods[0], chart.Periods[len(chart.Periods)-1])
	if chart.Kind == "indexed" {
		b.WriteString("Each line is rebased to 100 at the first period where all have a figure.\n")
	}
	for _, series := range chart.Series {
		fmt.Fprintf(&b, "- [%s](/indicators/%s)", mdText(series.Label), series.ID)
		var about []string
		if series.Member != "" {
			about = append(about, series.Member)
		}
		if series.Unit != "" {
			about = append(about, series.Unit)
		}
		if len(about) > 0 {
			fmt.Fprintf(&b, " (%s)", strings.Join(about, "; "))
		}
		first, last, low, high := -1, -1, -1, -1
		for n, value := range series.Values {
			if value == nil {
				continue
			}
			if first < 0 {
				first = n
			}
			last = n
			if low < 0 || *value < *series.Values[low] {
				low = n
			}
			if high < 0 || *value > *series.Values[high] {
				high = n
			}
		}
		if first < 0 {
			b.WriteString(": no figures in this span\n")
			continue
		}
		at := func(n int) string {
			return fmt.Sprintf("%s in %s", figure(*series.Values[n]), chart.Periods[n])
		}
		fmt.Fprintf(&b, ": first %s; last %s; low %s; high %s", at(first), at(last), at(low), at(high))
		// A rate moves in points: inflation from 1.6% to 3.2% is up 1.6
		// points, and "up 99%" would be read as a figure about prices.
		start, end := *series.Values[first], *series.Values[last]
		switch {
		case isRate(series.Unit):
			fmt.Fprintf(&b, "; change first to last %+.2f percentage points", end-start)
		case start != 0:
			fmt.Fprintf(&b, "; change first to last %+.1f%%", (end-start)/math.Abs(start)*100)
		}
		b.WriteString("\n")
	}
	if chart.Correlation != nil {
		fmt.Fprintf(&b, "Correlation (Pearson r) over the %d periods both have a figure: %.2f\n",
			chart.Overlap, *chart.Correlation)
	}
	b.WriteString("For this turn: in 3 to 5 short sentences or bullets, say what the chart shows — how each " +
		"series moved, and whether and when they moved together — using only the figures in this section, " +
		"rounded, and link each series as given. Where a correlation is given, say how strong it is and its " +
		"sign, and that it does not show that one causes the other. Do not describe the chart's colours or " +
		"repeat its title. Then, if other listed items would deepen the comparison, suggest at most three.\n")
	return b.String()
}

func isRate(unit string) bool {
	unit = strings.ToLower(unit)
	return strings.Contains(unit, "%") || strings.Contains(unit, "percent") || strings.Contains(unit, "persen")
}

// figure writes a number for the prompt the way a reader would say it.
func figure(value float64) string {
	switch magnitude := math.Abs(value); {
	case magnitude >= 1000:
		return strconv.FormatFloat(math.Round(value), 'f', 0, 64)
	case magnitude >= 10:
		return strconv.FormatFloat(value, 'f', 1, 64)
	default:
		return strconv.FormatFloat(value, 'f', 2, 64)
	}
}

// analysisInstructions is the base instructions with the rule against
// figures relaxed to the chart's own.
func analysisInstructions(prompt string) string {
	return strings.Replace(prompt,
		"Never state a figure: you see what a series is and its coverage, not its numbers.",
		"State no figure other than those in the chart section below: you see those, and nothing else of any series' numbers.",
		1)
}

// completeAssistantModel asks the writing model for a whole reply at once,
// for the planner, which reads it as JSON rather than showing it.
func (s *Server) completeAssistantModel(
	ctx context.Context, messages []assistantMessage, maxTokens int,
) (string, error) {
	cfg := s.cfg.Assistant
	payload, err := json.Marshal(map[string]any{
		"model":                cfg.Model,
		"messages":             messages,
		"max_tokens":           maxTokens,
		"temperature":          0,
		"chat_template_kwargs": map[string]any{"enable_thinking": false},
	})
	if err != nil {
		return "", err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, s.assistantEndpoint(), bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	request.Header.Set("Authorization", "Bearer "+cfg.Token)
	request.Header.Set("Content-Type", "application/json")

	response, err := s.assistantState().client.Do(request)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 64<<10))
	if err != nil {
		return "", err
	}
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("workers ai answered %d: %s", response.StatusCode,
			strings.TrimSpace(clip(string(body), 300)))
	}
	var reply struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(body, &reply); err != nil {
		return "", err
	}
	if len(reply.Choices) == 0 {
		return "", fmt.Errorf("workers ai reply has no choices: %s", clip(string(body), 200))
	}
	return reply.Choices[0].Message.Content, nil
}

// withChartSources adds the charted series to what the reply may link to: a
// series chosen from the per-word candidates may not be among the few the
// prompt listed.
func withChartSources(sources []assistantSource, chart *chartSpec) []assistantSource {
	listed := map[string]bool{}
	for _, source := range sources {
		listed[source.Kind+":"+source.ID] = true
	}
	for _, series := range chart.Series {
		if !listed["indicator:"+series.ID] {
			sources = append(sources, assistantSource{Kind: "indicator", ID: series.ID, Label: series.Label})
		}
	}
	return sources
}
