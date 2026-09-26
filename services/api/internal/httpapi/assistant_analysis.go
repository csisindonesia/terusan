package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"regexp"
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
	// How many series one chart may carry: an oil price against five
	// currencies is six, and past that the palette would repeat.
	analysisMaxSeries = 6
	// How many periods a chart is drawn with. Thirty years of months; a
	// longer run keeps its most recent part, which is what a comparison is
	// usually about.
	analysisMaxPeriods = 360
	// A comparison drawn over fewer common periods than this says nothing.
	analysisMinOverlap = 3
	// How long the planner, and then the figures, may take before the turn
	// goes ahead with the rule's plan or without a chart.
	plannerTimeout = 8 * time.Second
	figuresTimeout = 8 * time.Second
)

// The chart kinds the portal draws, and what each is for. Said to the
// planner in these words.
var chartKinds = map[string]string{
	"line":      "one series over time, or two or three in the same unit",
	"dual_axis": "exactly two series in different units over time, each on its own axis (e.g. an exchange rate in rupiah against inflation in percent)",
	"indexed":   "two to six series in different units where the question is about relative growth; each is rebased to 100 at the first common period",
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

	// The question it was planned for, which may be an earlier one than the
	// latest ("buatkan chartnya"): what the proposal explains each series by.
	asked string
}

type chartSeries struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	Unit  string `json:"unit,omitempty"`
	// The place, or the commodity, the figures are for where the series has
	// several: "Indonesia", "DKI Jakarta", "Beras".
	Member string `json:"member,omitempty"`
	// Who publishes the figures, for the chart's "Data source" line.
	Source string `json:"source,omitempty"`
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
		if isAnalysisWord(word) {
			asked = true
		}
	}
	return asked
}

// isAnalysisWord reads a word with its Indonesian suffix off as well:
// "buatkan chartnya" asks for a chart as plainly as "chart" does, and so do
// "grafikkan", "bandingkanlah" and "hubungannya".
func isAnalysisWord(word string) bool {
	return analysisWords[word] || analysisWords[withoutSuffix(word)]
}

func withoutSuffix(word string) string {
	for _, suffix := range []string{"nya", "lah", "kah", "kan", "in"} {
		if stem := strings.TrimSuffix(word, suffix); stem != word && len([]rune(stem)) >= 3 {
			return stem
		}
	}
	return word
}

func asksRelation(question string) bool {
	for _, word := range questionWords(question) {
		if relationWords[word] || relationWords[withoutSuffix(word)] {
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
			if i.Observations < 2 || !namedFor(i, titles, terms) || quoteVariant(i) {
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
		if isAnalysisWord(word) {
			continue
		}
		terms := searchTerms(word)
		if len(terms) == 0 {
			continue
		}
		// The closes before the cap: a currency is four quotes, and three of
		// them would take the places of other currencies.
		var ranked []Indicator
		for _, i := range rankSeries(catalogue.series, titles, terms) {
			if !quoteVariant(i) {
				ranked = append(ranked, i)
			}
		}
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
	// The place or commodity each series was proposed for, so a confirmed
	// chart draws the one the reader agreed to.
	Members map[string]string `json:"-"`
}

// analyse plans a chart for the question and reads its figures, or returns
// nil where there is nothing worth drawing.
//
// A question that names nothing to chart — "buatkan chartnya" — is about what
// came before it: the series the last reply linked, then the question before.
func (s *Server) analyse(
	ctx context.Context, catalogue assistantCatalogue, question, language string, earlier analysisContext,
) *chartSpec {
	candidates, groups := analysisCandidateSeries(catalogue, question)
	if len(groups) == 0 {
		var asked string
		candidates, groups, asked = earlier.candidates(catalogue)
		if asked != "" {
			question = asked + " — " + question
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	// The planner and the figures each have their own time: a slow planner
	// once spent the whole of a shared one, and the rule it fell back to had
	// nothing left to read the figures with.
	planCtx, cancelPlan := context.WithTimeout(ctx, plannerTimeout)
	defer cancelPlan()
	plan, err := s.planChart(planCtx, candidates, question, language)
	// Started once the plan is in, so a slow planner leaves it whole.
	ctx, cancel := context.WithTimeout(ctx, figuresTimeout)
	defer cancel()
	switch {
	case err != nil:
		s.log.Warn("assistant.plan_failed", "error", err)
		plan = defaultPlan(groups, candidates, question)
	case len(plan.Series) == 0:
		// Declined: nothing listed measures what was asked. The rule is not
		// asked instead, since it would chart the nearest thing regardless.
		s.log.Info("assistant.plan_declined")
		return nil
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

	sides := questionSides(catalogue, question)
	titles := catalogueTitles(catalogue)
	candidates = preferred(question, candidates, titles)
	chosen = preferredChoice(question, chosen, candidates, titles)
	plan.Chart = chartFor(plan.Chart, chosen)
	for _, i := range chosen {
		byID[i.IndicatorID] = i
	}
	// Once what each series was drawn for is known, a side of the question
	// nothing charted is gets the best candidate that is it: "harga minyak
	// dengan currency asean" drew six currencies and no oil. Decided after
	// the first read, because a food price series is chili only once its
	// commodity is known.
	repair := func(drawn []chartSeries) []Indicator {
		var extra []Indicator
		for _, sd := range sides {
			covered := false
			for _, series := range drawn {
				if sd.coveredBy(seriesText(byID[series.ID], series.Member, titles)) {
					covered = true
					break
				}
			}
			if covered {
				continue
			}
			for _, candidate := range candidates {
				if sd.coveredBy(seriesText(candidate, "", titles)) &&
					!containsSeries(chosen, candidate) && !containsSeries(extra, candidate) {
					extra = append(extra, candidate)
					byID[candidate.IndicatorID] = candidate
					break
				}
			}
		}
		return extra
	}
	// Once the place or commodity drawn is known, a series that is no side
	// of the question is dropped — the euro in a chart of ASEAN currencies,
	// the third copy of the dollar rate — before it narrows the span.
	keep := func(series chartSeries) bool {
		return len(sides) == 0 || coversAnySide(sides, seriesText(byID[series.ID], series.Member, titles))
	}
	chart, err := s.chartFigures(ctx, plan, chosen, searchTerms(question), keep, repair)
	if err != nil {
		s.log.Warn("assistant.chart_failed", "error", err)
		return nil
	}
	// The planner charts the nearest thing when nothing fits, whatever it is
	// told: "anggaran pendidikan dengan jumlah penduduk" drew the education
	// industry's GDP against the number of poor. Every side of what was asked
	// must be what one charted series is.
	if chart != nil {
		chart.asked = question
		if side := uncoveredSide(catalogue, chart, question); side != "" {
			charted := make([]string, len(chart.Series))
			for n, series := range chart.Series {
				charted[n] = series.Label
			}
			s.log.Info("assistant.plan_off_topic", "side", side, "series", strings.Join(charted, " | "))
			return nil
		}
	}
	if chart == nil {
		s.log.Info("assistant.chart_empty", "series", len(chosen))
	}
	if chart != nil {
		sourceOf := chartSourceNames(catalogue)
		for n := range chart.Series {
			chart.Series[n].Source = sourceOf(byID[chart.Series[n].ID])
		}
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
	b.WriteString("\nChoose 1 to 3 series by their id, exactly as listed — up to 6 where the question names that many " +
		"things, such as several currencies. Never choose two series that measure the same thing (two US " +
		"dollar to rupiah rates), and never leave out a thing the question names when a series for it is " +
		"listed. \"Minyak\" alone is crude oil (Brent); \"minyak sawit\" is palm oil. Where the question asks for more than is listed (five currencies " +
		"and three are listed), chart what is listed rather than nothing. Prefer national figures over " +
		"regional ones, series whose coverage overlaps the longest, the same frequency where there is a " +
		"choice, and the measure the question names: inflation as a rate rather than a price index, an " +
		"exchange rate against the US dollar for rupiah to dollar. Leave out anything only loosely related. " +
		"If no listed series measures what the question asks for — only something near it, such as an " +
		"industry's output for a budget — reply {\"series\":[]}: no chart is better than a chart of the wrong thing.\n")
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
	}, 700)
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
		// Cut off before it closed — the reason ran past the token limit —
		// the series and the chart are still there to be read.
		if ids := plannedSeries.FindStringSubmatch(content); ids != nil {
			plan := analysisPlan{}
			for _, id := range quotedID.FindAllStringSubmatch(ids[1], -1) {
				plan.Series = append(plan.Series, id[1])
			}
			if kind := plannedChart.FindStringSubmatch(content); kind != nil {
				plan.Chart = kind[1]
			}
			if title := plannedTitle.FindStringSubmatch(content); title != nil {
				plan.Title = title[1]
			}
			return plan, nil
		}
		return analysisPlan{}, fmt.Errorf("planner reply has no JSON: %s", clip(content, 200))
	}
	var plan analysisPlan
	if err := json.Unmarshal([]byte(content[start:end+1]), &plan); err != nil {
		return analysisPlan{}, fmt.Errorf("planner reply: %w", err)
	}
	return plan, nil
}

var (
	plannedSeries = regexp.MustCompile(`"series"\s*:\s*\[([^\]]*)\]`)
	quotedID      = regexp.MustCompile(`"([^"]+)"`)
	plannedChart  = regexp.MustCompile(`"chart"\s*:\s*"([a-z_]+)"`)
	plannedTitle  = regexp.MustCompile(`"title"\s*:\s*"([^"]+)"`)
)

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
	keep func(chartSeries) bool, repair func([]chartSeries) []Indicator,
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
		member, figures, err := s.seriesFigures(ctx, i.IndicatorID, granularity, terms, plan.Members[i.IndicatorID])
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
	if repair != nil {
		drawn := make([]chartSeries, len(reads))
		for n, r := range reads {
			drawn[n] = r.series
		}
		if extra := repair(drawn); len(extra) > 0 {
			// Read again with them: one may be coarser, and every series is
			// drawn at the coarsest.
			more := append(append([]Indicator{}, chosen...), extra...)
			plan.Chart = chartFor(plan.Chart, more)
			return s.chartFigures(ctx, plan, more, terms, keep, nil)
		}
	}
	if keep != nil {
		var kept []read
		for _, r := range reads {
			if keep(r.series) {
				kept = append(kept, r)
			}
		}
		if len(kept) > 0 {
			reads = kept
		}
	}
	if len(reads) > analysisMaxSeries {
		reads = reads[:analysisMaxSeries]
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

	title := plan.Title
	if title == "" || !sameSeries(plan.Series, reads, func(r read) string { return r.series.ID }) {
		// Repaired or pruned since the planner named it, and its title may
		// name what is no longer there ("… (USD, SGD, JPY, EUR, GBP)").
		names := make([]string, len(reads))
		for n, r := range reads {
			names[n] = shortLabel(r.series.Label)
		}
		title = clip(strings.Join(names, " · "), 90)
	}
	chart := &chartSpec{
		Title:       title,
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
	ctx context.Context, indicator, granularity string, terms []string, want string,
) (string, map[string]float64, error) {
	from, err := s.observationSource(ctx, []string{indicator})
	if err != nil {
		return "", nil, err
	}
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT o.geo_id, %s AS place, %s AS commodity, any_value(o.commodity_name_raw) AS printed,
		       count(*) AS n
		FROM %s WHERE o.indicator_id = ? AND o.value IS NOT NULL
		GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 2000`,
		memberGeoExpr, memberCommodityExpr, from), indicator)
	if err != nil {
		return "", nil, err
	}
	type member struct {
		geo, place, commodity, printed *string
		n                              int64
		score                          int
	}
	var best *member
	for rows.Next() {
		var m member
		if err := rows.Scan(&m.geo, &m.place, &m.commodity, &m.printed, &m.n); err != nil {
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
		// The one the reader confirmed, over anything the words suggest.
		if want != "" && memberName(m.place, m.commodity, m.printed) == want {
			m.score += 1000
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
	return memberName(best.place, best.commodity, best.printed), figures, figureRows.Err()
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
	fmt.Fprintf(&b, "\n## Chart shown above your reply\nA chart has been drawn and is on the page above your "+
		"reply: never write that a chart or its data is unavailable, missing or cannot be made. The reader sees a %s chart, %q, of %s figures from %s to %s.\n",
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
		"repeat its title. The chart shows these series and no others: name each only by its link text above, " +
		"whatever earlier replies named other series. Write each place and commodity exactly as given in its " +
		"brackets, untranslated (\"Bird's eye chili — green\", not a guess at its local name). " +
		"Begin with what the chart shows. Then, if other listed items would deepen the " +
		"comparison, suggest at most three.\n")
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
	return strings.NewReplacer(
		"Never state a figure: you see what a series is and its coverage, not its numbers.",
		"State no figure other than those in the chart section below: you see those, and nothing else of any series' numbers.",
		// A turn with a chart has something about the topic by definition;
		// left in, this rule is how "where is the chart?" got "there is none"
		// beneath the chart.
		`- If nothing listed is about the topic, say so plainly and suggest "Suggest data" (top bar). Do not offer items on a different topic in its place.`,
		"- The chart described below is directly above your reply; if the reader asks where it is, it is there.",
	).Replace(prompt)
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

// chartSourceNames names who publishes a series: its own publisher where the
// indicator says, otherwise the source its dataset was collected from.
func chartSourceNames(catalogue assistantCatalogue) func(Indicator) string {
	byDataset := map[string]string{}
	for _, d := range catalogue.datasets {
		switch {
		case d.Organization != nil && *d.Organization != "":
			byDataset[d.DatasetID] = *d.Organization
		case d.SourceName != nil && *d.SourceName != "":
			byDataset[d.DatasetID] = *d.SourceName
		}
	}
	return func(i Indicator) string {
		if i.Publisher != nil && *i.Publisher != "" {
			return *i.Publisher
		}
		if i.DatasetID != nil && byDataset[*i.DatasetID] != "" {
			return byDataset[*i.DatasetID]
		}
		return strings.Join(i.Sources, ", ")
	}
}

// analysisContext is what a follow-up asking for a chart refers to: the
// reader's earlier questions, the latest first, and the series and datasets
// the last reply that linked any of them linked.
type analysisContext struct {
	questions []string
	linked    []link
}

// link is a series or dataset link as a reply wrote it.
type link struct {
	id, text string
	dataset  bool
}

// How far back a follow-up looks for what it is about: "dimana chartnya"
// after "ya maksud saya itu" is still about the question before that.
const analysisLookBack = 3

// earlierContext reads the context off the conversation.
func earlierContext(messages []assistantMessage) analysisContext {
	var earlier analysisContext
	seenQuestion := false
	for i := len(messages) - 1; i >= 0 && len(earlier.questions) < analysisLookBack; i-- {
		switch messages[i].Role {
		case "user":
			if !seenQuestion {
				seenQuestion = true // the latest, which is being answered
				continue
			}
			earlier.questions = append(earlier.questions, messages[i].Content)
		case "assistant":
			if seenQuestion && earlier.linked == nil {
				for _, match := range recordLink.FindAllStringSubmatch(messages[i].Content, -1) {
					earlier.linked = append(earlier.linked,
						link{text: match[1], dataset: match[2] == "datasets", id: match[3]})
				}
			}
		}
	}
	return earlier
}

var (
	indicatorLink = regexp.MustCompile(`\[([^\]]+)\]\(/indicators/([A-Za-z0-9_-]+)\)`)
	recordLink    = regexp.MustCompile(`\[([^\]]+)\]\(/(indicators|datasets)/([A-Za-z0-9_-]+)\)`)
)

// quoteVariant reports whether a series is the open, high or low of a
// market quote, beside which its close is the one to chart: an exchange
// rate is four series, and three of them would crowd out other currencies.
func quoteVariant(i Indicator) bool {
	name := strings.ToLower(indicatorTitle(i))
	for _, variant := range []string{" open", " high", " low"} {
		if strings.HasSuffix(name, ","+variant) || strings.HasSuffix(name, " price"+variant) {
			return true
		}
	}
	return false
}

// candidates is what the earlier turns offer to chart — each linked series
// as a side of its own, each linked dataset's series as another, then what
// the latest earlier question that names anything finds — and that question.
func (a analysisContext) candidates(catalogue assistantCatalogue) ([]Indicator, [][]Indicator, string) {
	byID := make(map[string]Indicator, len(catalogue.series))
	byDataset := map[string][]Indicator{}
	for _, i := range catalogue.series {
		byID[i.IndicatorID] = i
		if i.DatasetID != nil && i.Observations >= 2 && !quoteVariant(i) {
			byDataset[*i.DatasetID] = append(byDataset[*i.DatasetID], i)
		}
	}
	datasetTitles := map[string]string{}
	for _, d := range catalogue.datasets {
		datasetTitles[d.DatasetID] = datasetTitle(d)
	}

	var out []Indicator
	var groups [][]Indicator
	seen := map[string]bool{}
	add := func(series []Indicator) []Indicator {
		var added []Indicator
		for _, i := range series {
			if !seen[i.IndicatorID] && len(out) < analysisCandidates {
				seen[i.IndicatorID] = true
				out = append(out, i)
				added = append(added, i)
			}
		}
		return added
	}
	for _, l := range a.linked {
		// A link whose words are not the record's title was made up by the
		// model — "Population, age 7-24" on the id of an inflation series —
		// and charting it would chart something the reader never asked for.
		if l.dataset {
			if title, ok := datasetTitles[l.id]; ok && sameTitle(l.text, title) {
				if group := add(byDataset[l.id]); len(group) > 0 {
					groups = append(groups, group)
				}
			}
			continue
		}
		if i, ok := byID[l.id]; ok && i.Observations >= 2 && sameTitle(l.text, indicatorTitle(i)) {
			if group := add([]Indicator{i}); len(group) > 0 {
				groups = append(groups, group)
			}
		}
	}
	question := ""
	for _, asked := range a.questions {
		more, moreGroups := analysisCandidateSeries(catalogue, asked)
		if len(moreGroups) == 0 {
			continue // "ya maksud saya itu" names nothing
		}
		question = asked
		add(more)
		groups = append(groups, moreGroups...)
		break
	}
	return out, groups, question
}

// noChartPrompt is what the reply is told when the reader asked for a chart
// and none could be drawn. Without it the model, seeing an earlier reply that
// described a chart, describes one too — with figures it made up.
const noChartPrompt = "\n## No chart this turn\nThe reader asked for a chart or a comparison, but no " +
	"series in this portal measures what they asked for closely enough to chart. Say exactly that in one " +
	"sentence, naming what is missing in the reader's words; give no other reason (not frequency, not " +
	"format) and do not name a series you do not see listed above. Then point to the listed items that " +
	"come closest, if any. Never describe a chart or write as if one is shown, and state no figures: the " +
	"figures in earlier replies belonged to their charts and are not known for this turn.\n"

// sameTitle compares a link's text with a title as the prompt printed it,
// without case, emphasis or spacing.
func sameTitle(text, title string) bool {
	flat := func(s string) string {
		return strings.Join(strings.Fields(strings.ToLower(strings.Trim(s, "*_` "))), " ")
	}
	return flat(text) == flat(mdText(title))
}

// sideWords split a question into the things it sets against each other:
// "anggaran pendidikan dengan jumlah penduduk" is two, "kurs rupiah dan
// inflasi" two, "idr ke usd terhadap inflasi" three.
var sideWords = map[string]bool{
	"dan": true, "dengan": true, "terhadap": true, "vs": true, "versus": true, "ke": true,
	"antara": true, "atau": true, "serta": true, "sama": true, "and": true, "with": true,
	"against": true, "or": true, "to": true, "between": true,
}

// coverageNoise is words that name no thing to chart, though series carry
// them: "Jumlah Penduduk Miskin" is not what "jumlah penduduk" asks for
// because of "jumlah", and every FRED series is "for Indonesia".
var coverageNoise = func() map[string]bool {
	set := map[string]bool{}
	for _, word := range strings.Fields(`jumlah nilai angka tingkat total besaran indonesia nasional
		national mata lagi cek coba sekarang berikan buatkan tampilkan lihat number amount level
		value rate negara country countries`) {
		set[word] = true
	}
	return set
}()

// side is one thing a question sets against the others: its words, and the
// search terms of those that name something in the catalogue.
type side struct {
	words  []string
	topics [][]string
}

// questionSides splits the question at its joining words and keeps the sides
// that name something ("saya ingin tau perbandingan" names nothing).
func questionSides(catalogue assistantCatalogue, question string) []side {
	titles := catalogueTitles(catalogue)
	// Whether anything in the catalogue is named for a word: one that nothing
	// is ("cek", "saya") is not a thing asked for.
	named := func(terms []string) bool {
		for _, i := range catalogue.series {
			if namedFor(i, titles, terms) {
				return true
			}
		}
		return false
	}
	var sides []side
	var words []string
	for _, word := range append(questionWords(question), "dan") {
		if !sideWords[word] {
			words = append(words, word)
			continue
		}
		var topics [][]string
		for _, w := range words {
			if isAnalysisWord(w) || coverageNoise[w] || stopWords[w] {
				continue
			}
			// A short word matches inside longer ones — "tau" in "status" —
			// so it is a topic only where the tables know it, as "idr".
			if len([]rune(w)) < 4 && translations[w] == "" && acronyms[w] == "" {
				continue
			}
			if terms := searchTerms(w); len(terms) > 0 && named(terms) {
				topics = append(topics, terms)
			}
		}
		if len(topics) > 0 {
			sides = append(sides, side{words: words, topics: topics})
		}
		words = nil
	}
	return sides
}

// coveredBy reports whether one series' text is this side: all of a short
// side — "anggaran pendidikan" is an education budget, and a budget or an
// education series alone is not one — and most of a long one, whose words
// are as often the reader's framing ("currency 5 negara asean") as the thing.
func (sd side) coveredBy(text string) bool {
	need := len(sd.topics)
	if need >= 3 {
		need = (need + 1) / 2
	}
	hits := 0
	for _, terms := range sd.topics {
		for _, term := range terms {
			if strings.Contains(text, term) {
				hits++
				break
			}
		}
	}
	return hits >= need
}

func catalogueTitles(catalogue assistantCatalogue) map[string]string {
	titles := make(map[string]string, len(catalogue.datasets))
	for _, d := range catalogue.datasets {
		titles[d.DatasetID] = datasetTitle(d)
	}
	return titles
}

// seriesText is what a series is called everywhere a reader would see it:
// its name, the place or commodity drawn, its tags and its dataset's title.
func seriesText(i Indicator, member string, titles map[string]string) string {
	text := strings.ToLower(indicatorTitle(i)+" "+member) + " " + lower(i.Slug, i.Code) + " " +
		strings.ToLower(strings.Join(i.Tags, " "))
	if i.DatasetID != nil {
		text += " " + strings.ToLower(titles[*i.DatasetID])
	}
	return text
}

func coversAnySide(sides []side, text string) bool {
	for _, sd := range sides {
		if sd.coveredBy(text) {
			return true
		}
	}
	return false
}

func containsSeries(series []Indicator, i Indicator) bool {
	for _, s := range series {
		if s.IndicatorID == i.IndicatorID {
			return true
		}
	}
	return false
}

// uncoveredSide is the first side of the question that no charted series
// is, or "" where each is.
func uncoveredSide(catalogue assistantCatalogue, chart *chartSpec, question string) string {
	titles := catalogueTitles(catalogue)
	byID := make(map[string]Indicator, len(catalogue.series))
	for _, i := range catalogue.series {
		byID[i.IndicatorID] = i
	}
	for _, sd := range questionSides(catalogue, question) {
		covered := false
		for _, series := range chart.Series {
			i, ok := byID[series.ID]
			if !ok {
				i = Indicator{IndicatorID: series.ID, Name: &series.Label}
			}
			if sd.coveredBy(seriesText(i, series.Member, titles)) {
				covered = true
				break
			}
		}
		if !covered {
			return strings.Join(sd.words, " ")
		}
	}
	return ""
}

// verifiedHistory marks the series links in earlier replies whose words are
// not the series' title. The model made those up, and shown them again it
// repeats them — a title invented on one turn becomes a "fact" on the next.
func verifiedHistory(messages []assistantMessage, catalogue assistantCatalogue) []assistantMessage {
	titles := make(map[string]string, len(catalogue.series))
	for _, i := range catalogue.series {
		titles[i.IndicatorID] = indicatorTitle(i)
	}
	out := make([]assistantMessage, len(messages))
	for n, message := range messages {
		if message.Role == "assistant" {
			message.Content = indicatorLink.ReplaceAllStringFunc(message.Content, func(written string) string {
				match := indicatorLink.FindStringSubmatch(written)
				if title, ok := titles[match[2]]; ok && sameTitle(match[1], title) {
					return written
				}
				return match[1] + " (not a series in this portal)"
			})
		}
		out[n] = message
	}
	return out
}

// repeating reports whether a reply has fallen into saying the same thing
// over and over, which a small model does once it loses its place: the same
// run of at least 20 characters, four times back to back at the end.
func repeating(reply string) bool {
	runes := []rune(reply)
	for size := 20; size <= 200 && size*4 <= len(runes); size++ {
		tail := string(runes[len(runes)-size:])
		loops := true
		for k := 2; k <= 4; k++ {
			if string(runes[len(runes)-k*size:len(runes)-(k-1)*size]) != tail {
				loops = false
				break
			}
		}
		if loops && strings.TrimSpace(tail) != "" {
			return true
		}
	}
	return false
}

// withoutEarlierReplies keeps the reader's questions and blanks the replies
// between them, for a turn that asked for a chart. What the chart shows is in
// the prompt; an earlier reply describing another chart, or one it invented,
// is what the model copies instead — word for word, figures and all.
func withoutEarlierReplies(messages []assistantMessage) []assistantMessage {
	out := make([]assistantMessage, len(messages))
	for n, message := range messages {
		if message.Role == "assistant" {
			message.Content = "(earlier reply omitted)"
		}
		out[n] = message
	}
	return out
}

// preference settles a word the catalogue reads two ways: "minyak" alone is
// crude oil, and the planner, told so, still charted palm oil.
type preference struct {
	word, prefer, over string
	unless             []string
}

var preferences = []preference{
	{word: "minyak", prefer: "brent", over: "palm", unless: []string{"sawit", "palm", "cpo", "goreng", "kelapa"}},
	{word: "oil", prefer: "brent", over: "palm", unless: []string{"palm", "cpo", "cooking", "sawit"}},
}

func activePreferences(question string) []preference {
	words := map[string]bool{}
	for _, word := range questionWords(question) {
		words[word] = true
	}
	var active []preference
	for _, p := range preferences {
		if !words[p.word] {
			continue
		}
		qualified := false
		for _, other := range p.unless {
			qualified = qualified || words[other]
		}
		if !qualified {
			active = append(active, p)
		}
	}
	return active
}

// preferred puts the candidates a preference favours first.
func preferred(question string, candidates []Indicator, titles map[string]string) []Indicator {
	active := activePreferences(question)
	if len(active) == 0 {
		return candidates
	}
	rank := func(i Indicator) int {
		text := seriesText(i, "", titles)
		for _, p := range active {
			if strings.Contains(text, p.prefer) {
				return 0
			}
		}
		return 1
	}
	out := append([]Indicator{}, candidates...)
	sort.SliceStable(out, func(a, b int) bool { return rank(out[a]) < rank(out[b]) })
	return out
}

// preferredChoice swaps a chosen series a preference disfavours for the
// best candidate it favours.
func preferredChoice(question string, chosen, candidates []Indicator, titles map[string]string) []Indicator {
	out := append([]Indicator{}, chosen...)
	for _, p := range activePreferences(question) {
		for n, i := range out {
			text := seriesText(i, "", titles)
			if !strings.Contains(text, p.over) || strings.Contains(text, p.prefer) {
				continue
			}
			for _, candidate := range candidates {
				if strings.Contains(seriesText(candidate, "", titles), p.prefer) && !containsSeries(out, candidate) {
					out[n] = candidate
					break
				}
			}
		}
	}
	return out
}

// sameSeries reports whether the planner's ids are exactly what was charted.
func sameSeries[T any](planned []string, charted []T, id func(T) string) bool {
	if len(planned) != len(charted) {
		return false
	}
	set := map[string]bool{}
	for _, p := range planned {
		set[strings.TrimSpace(p)] = true
	}
	for _, c := range charted {
		if !set[id(c)] {
			return false
		}
	}
	return true
}

// shortLabel is a series' name without the quote it is the close of, for a
// title built from names.
func shortLabel(label string) string {
	for _, suffix := range []string{", close", " close", " exchange rate"} {
		label = strings.TrimSuffix(label, suffix)
	}
	return label
}

// denial is a reply opening by saying there is no chart or no data, which a
// small model does beneath a chart that was drawn when the question was
// "dimana chartnya" — the page would contradict itself.
var denial = regexp.MustCompile(`(?i)(tidak (tersedia|dapat|bisa|ada (chart|grafik|data|seri))|belum (tersedia|ada)|` +
	`not available|unavailable|cannot (be )?(made|drawn|create)|can't (make|create|draw)|no (chart|data|series))`)

var aboutTheChart = regexp.MustCompile(`(?i)(chart|grafik|diagram|yang anda (minta|maksud)|you asked|requested)`)

// withoutDenial drops the opening sentences of a reply that deny the chart
// above it, and returns what is left and what was dropped.
func withoutDenial(text string) (string, string) {
	dropped := ""
	for {
		end := strings.IndexAny(text, ".!?\n")
		if end < 0 {
			return text, dropped
		}
		sentence := text[:end+1]
		// About the chart, not about one thing missing from it: "Tidak ada
		// data untuk Filipina" is worth keeping.
		if !denial.MatchString(sentence) || !aboutTheChart.MatchString(sentence) {
			return text, dropped
		}
		dropped += sentence
		text = strings.TrimLeft(text[end+1:], " \n")
	}
}

// verifiedReply unlinks the record links in a finished reply whose words
// are not the record's title — "[Gold price close]" on the id of the
// Singapore dollar rate. Applied to what is kept, which the portal reloads
// once the reply ends, so the link that would open the wrong series goes.
func verifiedReply(content string, catalogue assistantCatalogue) string {
	indicators := make(map[string]string, len(catalogue.series))
	for _, i := range catalogue.series {
		indicators[i.IndicatorID] = indicatorTitle(i)
	}
	datasets := catalogueTitles(catalogue)
	return recordLink.ReplaceAllStringFunc(content, func(written string) string {
		match := recordLink.FindStringSubmatch(written)
		titles := indicators
		if match[2] == "datasets" {
			titles = datasets
		}
		if title, ok := titles[match[3]]; ok && sameTitle(match[1], title) {
			return written
		}
		return match[1]
	})
}

// memberLabel is how a place and a commodity are named together.
func memberLabel(place, commodity *string) string {
	var label []string
	if place != nil {
		label = append(label, *place)
	}
	if commodity != nil {
		label = append(label, *commodity)
	}
	return strings.Join(label, " · ")
}

// ---- proposing before drawing ------------------------------------------------
//
// A chart is proposed before it is drawn: which series, for which part of the
// question, from where, over what span, and which kind of chart and why. The
// reader confirms — or unticks a series, or asks for something else — and
// only then is it drawn and described. The proposal's words are the server's,
// not the model's: it lists what was checked and nothing it could invent.

// chartProposal is what the reader is asked to confirm.
type chartProposal struct {
	Proposed    bool             `json:"proposed"`
	Kind        string           `json:"kind"`
	Title       string           `json:"title"`
	Reason      string           `json:"reason,omitempty"`
	Granularity string           `json:"granularity"`
	From        string           `json:"from"`
	To          string           `json:"to"`
	Series      []proposedSeries `json:"series"`
	// What the portal sends as the reader's message when they confirm, in
	// their language.
	ConfirmText string `json:"confirm_text"`
}

type proposedSeries struct {
	ID     string `json:"id"`
	Label  string `json:"label"`
	Unit   string `json:"unit,omitempty"`
	Member string `json:"member,omitempty"`
	Source string `json:"source,omitempty"`
	// The part of the question it stands for, in the reader's words.
	For  string `json:"for,omitempty"`
	From string `json:"from"`
	To   string `json:"to"`
}

// chartConfirm is the reader's answer to a proposal: the series they kept.
type chartConfirm struct {
	Series  []string          `json:"series"`
	Members map[string]string `json:"members,omitempty"`
	Kind    string            `json:"kind"`
	Title   string            `json:"title,omitempty"`
	Reason  string            `json:"reason,omitempty"`
}

// proposalFrom is the chart, drawn but not shown, as a proposal.
func proposalFrom(chart *chartSpec, catalogue assistantCatalogue, language string) chartProposal {
	sides := questionSides(catalogue, chart.asked)
	titles := catalogueTitles(catalogue)
	byID := make(map[string]Indicator, len(catalogue.series))
	for _, i := range catalogue.series {
		byID[i.IndicatorID] = i
	}
	p := chartProposal{
		Proposed: true, Kind: chart.Kind, Title: chart.Title, Reason: kindReason(chart.Kind, len(chart.Series), language),
		Granularity: chart.Granularity, From: chart.Periods[0], To: chart.Periods[len(chart.Periods)-1],
		ConfirmText: "Yes, draw the chart.",
	}
	if language == "id" {
		p.ConfirmText = "Ya, buat grafiknya."
	}
	for _, series := range chart.Series {
		first, last := -1, -1
		for n, value := range series.Values {
			if value != nil {
				if first < 0 {
					first = n
				}
				last = n
			}
		}
		proposed := proposedSeries{
			ID: series.ID, Label: series.Label, Unit: series.Unit, Member: series.Member, Source: series.Source,
		}
		if first >= 0 {
			proposed.From, proposed.To = chart.Periods[first], chart.Periods[last]
		}
		text := seriesText(byID[series.ID], series.Member, titles)
		for _, sd := range sides {
			if sd.coveredBy(text) {
				proposed.For = sideWordsShown(sd)
				break
			}
		}
		p.Series = append(p.Series, proposed)
	}
	return p
}

// sideWordsShown is a side as the reader wrote it, without the words that
// only framed it ("buatkan chart harga minyak" is "harga minyak").
func sideWordsShown(sd side) string {
	var shown []string
	for _, word := range sd.words {
		if isAnalysisWord(word) || stopWords[word] || coverageNoise[word] || isNumber(word) {
			continue
		}
		shown = append(shown, word)
	}
	return strings.Join(shown, " ")
}

var kindNames = map[string]map[string]string{
	"id": {
		"line": "garis", "dual_axis": "garis dua sumbu", "scatter": "sebar (scatter)", "bar": "batang",
		"indexed": "garis terindeks (periode awal = 100)",
	},
	"en": {
		"line": "line", "dual_axis": "two-axis line", "scatter": "scatter", "bar": "bar",
		"indexed": "indexed line (first period = 100)",
	},
}

var granularityNames = map[string]map[string]string{
	"id": {"month": "bulanan", "quarter": "kuartalan", "year": "tahunan"},
	"en": {"month": "monthly", "quarter": "quarterly", "year": "annual"},
}

// proposalText is the proposal as the reply the reader reads.
func proposalText(p chartProposal, language string) string {
	if language != "id" {
		language = "en"
	}
	id := language == "id"
	var b strings.Builder
	if id {
		b.WriteString("Sebelum membuat grafik, ini data yang akan saya gunakan:\n\n")
	} else {
		b.WriteString("Before drawing the chart, this is the data I would use:\n\n")
	}
	for _, series := range p.Series {
		fmt.Fprintf(&b, "- [%s](/indicators/%s)", mdText(series.Label), series.ID)
		var about []string
		if series.Member != "" {
			about = append(about, series.Member)
		}
		if series.Unit != "" {
			about = append(about, series.Unit)
		}
		if series.From != "" {
			about = append(about, series.From+"–"+series.To)
		}
		if series.Source != "" {
			if id {
				about = append(about, "sumber: "+series.Source)
			} else {
				about = append(about, "source: "+series.Source)
			}
		}
		if len(about) > 0 {
			fmt.Fprintf(&b, " — %s", strings.Join(about, "; "))
		}
		if series.For != "" {
			if id {
				fmt.Fprintf(&b, ". Untuk: *%s*", series.For)
			} else {
				fmt.Fprintf(&b, ". For: *%s*", series.For)
			}
		}
		b.WriteString("\n")
	}
	kind := kindNames[language][p.Kind]
	if id {
		fmt.Fprintf(&b, "\nJenis grafik: **%s**", kind)
	} else {
		fmt.Fprintf(&b, "\nChart: **%s**", kind)
	}
	if p.Reason != "" {
		fmt.Fprintf(&b, " — %s", strings.TrimSuffix(p.Reason, "."))
	}
	if id {
		fmt.Fprintf(&b, ".\nRentang: %s sampai %s, data %s.\n\n", p.From, p.To, granularityNames[language][p.Granularity])
		b.WriteString("Klik **Buat grafik** untuk melanjutkan, hapus centang seri yang tidak diperlukan, " +
			"atau tulis apa yang ingin diubah.")
	} else {
		fmt.Fprintf(&b, ".\nSpan: %s to %s, %s figures.\n\n", p.From, p.To, granularityNames[language][p.Granularity])
		b.WriteString("Click **Draw chart** to go ahead, untick any series you don't need, or say what to change.")
	}
	return b.String()
}

// confirmedChart draws what the reader confirmed: the series they kept, read
// again, for the place or commodity they were proposed for.
func (s *Server) confirmedChart(
	ctx context.Context, catalogue assistantCatalogue, c chartConfirm, earlier analysisContext,
) *chartSpec {
	byID := make(map[string]Indicator, len(catalogue.series))
	for _, i := range catalogue.series {
		byID[i.IndicatorID] = i
	}
	var chosen []Indicator
	var ids []string
	for _, id := range c.Series {
		i, ok := byID[id]
		if !ok || containsSeries(chosen, i) || len(chosen) >= analysisMaxSeries {
			continue // not a series here: the request is the browser's
		}
		chosen = append(chosen, i)
		ids = append(ids, id)
	}
	if len(chosen) == 0 {
		return nil
	}
	members := map[string]string{}
	for id, member := range c.Members {
		members[id] = clip(member, 200)
	}
	plan := analysisPlan{
		Series: ids, Chart: chartFor(c.Kind, chosen), Title: clip(c.Title, 90), Reason: clip(c.Reason, 200),
		Members: members,
	}
	// The words the proposal was made for pick the same place and commodity
	// where a member was not named.
	asked := strings.Join(earlier.questions, " ")
	ctx, cancel := context.WithTimeout(ctx, figuresTimeout)
	defer cancel()
	chart, err := s.chartFigures(ctx, plan, chosen, searchTerms(asked), nil, nil)
	if err != nil {
		s.log.Warn("assistant.chart_failed", "error", err)
		return nil
	}
	if chart != nil {
		sourceOf := chartSourceNames(catalogue)
		for n := range chart.Series {
			chart.Series[n].Source = sourceOf(byID[chart.Series[n].ID])
		}
		s.log.Info("assistant.charted", "kind", chart.Kind, "series", len(chart.Series), "confirmed", true)
	}
	return chart
}

func isNumber(word string) bool {
	_, err := strconv.Atoi(word)
	return err == nil
}

// kindReason says why a kind of chart suits the series, in the server's words
// rather than the planner's: the planner explained the series it chose, and
// after a repair or an untick those are not always the ones drawn.
func kindReason(kind string, series int, language string) string {
	id := language == "id"
	switch {
	case kind == "line" && series == 1 && id:
		return "satu seri dari waktu ke waktu"
	case kind == "line" && series == 1:
		return "one series over time"
	case kind == "line" && id:
		return "satuannya sama, jadi dibaca pada satu sumbu"
	case kind == "line":
		return "the same unit, so they share one axis"
	case kind == "dual_axis" && id:
		return "dua satuan berbeda, masing-masing pada sumbunya sendiri"
	case kind == "dual_axis":
		return "two different units, each on its own axis"
	case kind == "indexed" && id:
		return "satuannya berbeda, jadi setiap seri disamakan ke 100 pada periode awal untuk membandingkan pertumbuhannya"
	case kind == "indexed":
		return "different units, so each series is set to 100 at the first period to compare their growth"
	case kind == "scatter" && id:
		return "untuk melihat hubungan keduanya, satu titik per periode"
	case kind == "scatter":
		return "to see how the two relate, one dot per period"
	case kind == "bar" && id:
		return "periodenya sedikit, jadi besarnya dibandingkan per batang"
	default:
		return "few periods, so their size is compared bar by bar"
	}
}

// memberName is the member as the chart and the reply name it: with the
// commodity as its source printed it where the registry renamed it, since a
// model told "Bird's eye chili — green" writes "cabai merah", and told
// "Cabai Rawit Hijau" as well, writes that.
func memberName(place, commodity, printed *string) string {
	name := memberLabel(place, commodity)
	if commodity != nil && printed != nil && *printed != "" && !strings.EqualFold(*printed, *commodity) {
		name += " (" + *printed + ")"
	}
	return name
}
