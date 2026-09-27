package httpapi

import (
	"context"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Prices around holidays: "analisis harga beras dengan hari besar keagamaan".
//
// A holiday is not a series, so the ordinary chart — two series over the same
// months — has nothing to put beside the price. What answers the question is
// an event study: the price's daily figures from a month before each holiday
// to two weeks after, set to 100 on the week the window opens, and averaged
// across the years the event calendar holds. A rise that shows in the average
// is the holiday's; one year's spike may be anything.
//
// Lebaran moves about eleven days earlier each year, which is what makes this
// work: a harvest-season effect stays put in the calendar and averages out of
// the window, and the holiday's does not.
//
// The holidays come from Silver's event calendar (events.go), which is the
// SKB 3 Menteri as JDIH KemenPANRB publishes it, 2020 onwards. As everywhere
// in the assistant, the model chooses nothing here and states no figure it was
// not given: the window is computed from the warehouse, and the reply is told
// its numbers.

const (
	// The window: days before the holiday and after it.
	eventDaysBefore = 30
	eventDaysAfter  = 14
	// The first days of the window, averaged, are its baseline: one day's
	// figure is noise.
	eventBaselineDays = 7
	// A year whose window is thinner than this is left out of the average.
	eventMinDays = 15
	// Years drawn beside the average for one holiday.
	eventMaxYears = 5
)

// The holidays that "hari besar keagamaan" means, in the order they are
// charted: Lebaran first, since it is the one prices are known to move for.
var religiousHolidays = []string{
	"idul_fitri", "idul_adha", "natal", "tahun_baru_imlek", "waisak", "nyepi",
}

// eventWords are the words that name a holiday, and the holidays they name.
var eventWords = map[string][]string{
	"lebaran": {"idul_fitri"}, "fitri": {"idul_fitri"}, "idulfitri": {"idul_fitri"},
	"mudik": {"idul_fitri"}, "eid": {"idul_fitri"},
	"adha": {"idul_adha"}, "iduladha": {"idul_adha"}, "kurban": {"idul_adha"}, "qurban": {"idul_adha"},
	"natal": {"natal"}, "christmas": {"natal"}, "nataru": {"natal"},
	"imlek":  {"tahun_baru_imlek"},
	"nyepi":  {"nyepi"},
	"waisak": {"waisak"}, "vesak": {"waisak"},
	"ramadan": {"ramadan"}, "ramadhan": {"ramadan"}, "puasa": {"ramadan"},
	"paskah": {"paskah"}, "easter": {"paskah"},
	"keagamaan": religiousHolidays, "religious": religiousHolidays, "religius": religiousHolidays,
	"holiday": religiousHolidays, "holidays": religiousHolidays,
}

// eventPairs are two-word names: "hari raya", "cuti bersama".
var eventPairs = map[string][]string{
	"hari raya": religiousHolidays, "hari besar": religiousHolidays, "hari libur": religiousHolidays,
	"libur nasional": religiousHolidays, "cuti bersama": religiousHolidays,
}

// eventFraming is what surrounds a holiday's name in a question and names no
// series: kept out of the series search, so "harga beras menjelang hari raya"
// searches for "harga beras".
var eventFraming = map[string]bool{
	"hari": true, "raya": true, "besar": true, "libur": true, "nasional": true, "cuti": true,
	"bersama": true, "idul": true, "menjelang": true, "jelang": true, "saat": true, "selama": true,
	"sekitar": true, "sebelum": true, "sesudah": true, "setelah": true, "musim": true,
	"around": true, "before": true, "after": true, "during": true,
	// What asks rather than names: "lakukan analisis harga beras dengan …".
	"lakukan": true, "tolong": true, "coba": true, "buat": true, "buatkan": true, "bagaimana": true,
	"apakah": true, "dengan": true, "terhadap": true, "antara": true, "pada": true, "ketika": true,
}

// eventsAsked is the holidays a question names, in chart order.
//
// A holiday named outright is the one asked about: "hari raya idul fitri" is
// Lebaran, not every hari raya. The general words — "hari besar keagamaan",
// "hari raya" alone — mean all the religious holidays, and so does naming
// one alongside a word that asks about all ("lebaran dan hari besar
// keagamaan lainnya").
func eventsAsked(question string) []string {
	words := questionWords(question)
	var specific []string
	general, allOfThem := false, false
	for n, word := range words {
		if n+1 < len(words) && eventPairs[word+" "+words[n+1]] != nil {
			general = true
		}
		found := eventWords[word]
		switch {
		case len(found) == 1:
			if !containsString(specific, found[0]) {
				specific = append(specific, found[0])
			}
		case len(found) > 1:
			general, allOfThem = true, true
		}
	}
	switch {
	case len(specific) > 0 && !allOfThem:
		return specific
	case general:
		keys := append([]string{}, specific...)
		for _, key := range religiousHolidays {
			if !containsString(keys, key) {
				keys = append(keys, key)
			}
		}
		return keys
	default:
		return nil
	}
}

// withoutEventWords is the question with the holiday and its framing taken
// out, for finding the series it is about.
func withoutEventWords(question string) string {
	var kept []string
	for _, word := range questionWords(question) {
		if eventWords[word] != nil || eventFraming[word] {
			continue
		}
		kept = append(kept, word)
	}
	return strings.Join(kept, " ")
}

// asksAboutPricesAroundEvents reports whether a question with no word asking
// for analysis still asks for one: "harga beras menjelang lebaran".
func asksAboutPricesAroundEvents(question string) bool {
	if len(eventsAsked(question)) == 0 {
		return false
	}
	for _, word := range questionWords(question) {
		switch withoutSuffix(word) {
		case "harga", "price", "prices", "inflasi", "inflation":
			return true
		}
	}
	return false
}

func containsString(list []string, value string) bool {
	for _, item := range list {
		if item == value {
			return true
		}
	}
	return false
}

// eventWindow is what an event chart was computed from, kept on the chart so
// the proposal, the story and the reply's prompt read the same facts.
type eventWindow struct {
	Indicator   string            `json:"indicator"`
	Label       string            `json:"label"`
	Unit        string            `json:"unit,omitempty"`
	Member      string            `json:"member,omitempty"`
	Keys        []string          `json:"keys"`
	Before      int               `json:"before"`
	After       int               `json:"after"`
	Baseline    int               `json:"baseline_days"`
	Occurrences []eventOccurrence `json:"occurrences"`
}

// eventOccurrence is one holiday in one year, and what the price did.
type eventOccurrence struct {
	Key  string `json:"key"`
	Name string `json:"name"`
	Year int    `json:"year"`
	Date string `json:"date"`
	// The index on the day (H) and two weeks after, against the baseline of
	// 100; nil where the price has no figure near that day.
	AtDay *float64 `json:"at_day,omitempty"`
	After *float64 `json:"after,omitempty"`
	// The price itself at the baseline, in the series' unit.
	BaselinePrice float64 `json:"baseline_price"`
}

// eventLabels are the window's days as the chart labels them.
func eventLabels() []string {
	labels := make([]string, 0, eventDaysBefore+eventDaysAfter+1)
	for day := -eventDaysBefore; day <= eventDaysAfter; day++ {
		switch {
		case day < 0:
			labels = append(labels, fmt.Sprintf("H%d", day))
		case day == 0:
			labels = append(labels, "H")
		default:
			labels = append(labels, fmt.Sprintf("H+%d", day))
		}
	}
	return labels
}

// eventAnchors are the days each holiday fell on: its first national holiday,
// or for Ramadan its first day.
func (s *Server) eventAnchors(ctx context.Context, keys []string) ([]Event, error) {
	events, err := s.events(ctx, eventFilter{Keys: keys, Kinds: []string{"libur_nasional", "ramadan"}})
	if err != nil {
		return nil, err
	}
	var anchors []Event
	for _, e := range events {
		if (e.Key == "ramadan") == (e.Kind == "ramadan") {
			anchors = append(anchors, e)
		}
	}
	return anchors, nil
}

// analyseEvents plans an event chart for a question naming holidays: the
// daily price series the rest of the question is about, read around each.
func (s *Server) analyseEvents(
	ctx context.Context, catalogue assistantCatalogue, question string, keys []string, earlier analysisContext,
) *chartSpec {
	about := withoutEventWords(question)
	candidates, _ := analysisCandidateSeries(catalogue, about)
	if len(candidates) == 0 {
		var asked string
		candidates, _, asked = earlier.candidates(catalogue)
		about = withoutEventWords(asked)
	}
	terms := searchTerms(about)
	// A food price is a commodity of a series named for its market — "Food
	// price — traditional market" — so the words find the commodity first,
	// and the daily series that carries it most.
	var chosen *Indicator
	if found, commodity := s.dailySeriesFor(ctx, catalogue, terms); found != nil {
		chosen, terms = found, []string{strings.ToLower(commodity)}
	}
	for n := range candidates {
		if chosen == nil && candidates[n].Resolution == "daily" {
			chosen = &candidates[n]
		}
	}
	if chosen == nil {
		s.log.Info("assistant.event_no_daily_series", "candidates", len(candidates))
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, figuresTimeout)
	defer cancel()
	chart, err := s.eventChart(ctx, *chosen, "", keys, terms)
	if err != nil {
		s.log.Warn("assistant.event_chart_failed", "error", err)
		return nil
	}
	if chart != nil {
		chart.asked = question
		chart.Series[0].Source = chartSourceNames(catalogue)(*chosen)
		for n := range chart.Series {
			chart.Series[n].Source = chart.Series[0].Source
		}
		s.log.Info("assistant.charted", "kind", "event", "series", len(chart.Series),
			"occurrences", len(chart.Event.Occurrences))
	}
	return chart
}

// eventChart reads a daily series around each holiday and lines the windows
// up day by day.
func (s *Server) eventChart(
	ctx context.Context, indicator Indicator, want string, keys []string, terms []string,
) (*chartSpec, error) {
	anchors, err := s.eventAnchors(ctx, keys)
	if err != nil || len(anchors) == 0 {
		return nil, err
	}
	member, figures, err := s.seriesFigures(ctx, indicator.IndicatorID, "day", terms, want)
	if err != nil || len(figures) == 0 {
		return nil, err
	}
	// On a national holiday or a cuti bersama few markets report, and the
	// national average is a handful of them: PIHPS's rice jumped 15% on
	// Lebaran's cuti bersama in 2022 and fell back the next working day.
	// Those days are left out, and the last market day's price stands.
	closed, err := s.events(ctx, eventFilter{Kinds: []string{"libur_nasional", "cuti_bersama"}})
	if err != nil {
		return nil, err
	}
	for _, e := range closed {
		for _, day := range e.Dates {
			delete(figures, day)
		}
	}
	unit := ""
	if indicator.Unit != nil {
		unit = *indicator.Unit
	}
	window := &eventWindow{
		Indicator: indicator.IndicatorID, Label: indicatorTitle(indicator), Unit: unit, Member: member,
		Keys: keys, Before: eventDaysBefore, After: eventDaysAfter, Baseline: eventBaselineDays,
	}

	days := eventDaysBefore + eventDaysAfter + 1
	type year struct {
		occurrence eventOccurrence
		index      []*float64
	}
	byKey := map[string][]year{}
	for _, anchor := range anchors {
		day, err := time.Parse("2006-01-02", anchor.StartDate)
		if err != nil {
			continue
		}
		index, baseline, ok := windowIndex(figures, day, days)
		if !ok {
			continue
		}
		occurrence := eventOccurrence{
			Key: anchor.Key, Name: anchor.Name, Year: anchor.Year, Date: anchor.StartDate,
			AtDay: near(index, eventDaysBefore), After: near(index, days-1), BaselinePrice: baseline,
		}
		window.Occurrences = append(window.Occurrences, occurrence)
		byKey[anchor.Key] = append(byKey[anchor.Key], year{occurrence, index})
	}
	if len(window.Occurrences) == 0 {
		return nil, nil
	}

	chart := &chartSpec{Kind: "event", Granularity: "day", Periods: eventLabels(), Event: window}
	line := func(label string, values []*float64) chartSeries {
		return chartSeries{
			ID: indicator.IndicatorID, Label: label, Short: label, Unit: eventUnit, Member: member, Values: values,
		}
	}
	var present []string
	for _, key := range keys {
		if len(byKey[key]) > 0 {
			present = append(present, key)
		}
	}
	if len(present) == 1 {
		// One holiday: its average across the years, and the latest years
		// beside it so a reader sees how much they differ.
		years := byKey[present[0]]
		indices := make([][]*float64, len(years))
		for n, y := range years {
			indices[n] = y.index
		}
		name := years[0].occurrence.Name
		chart.Series = append(chart.Series, line(fmt.Sprintf("%s — rata-rata %d tahun", name, len(years)),
			averageIndex(indices, days)))
		sort.Slice(years, func(a, b int) bool { return years[a].occurrence.Year > years[b].occurrence.Year })
		for n, y := range years {
			if n >= eventMaxYears {
				break
			}
			chart.Series = append(chart.Series, line(fmt.Sprintf("%s %d", name, y.occurrence.Year), y.index))
		}
	} else {
		// Several: each holiday's average, one line apiece.
		for _, key := range present {
			if len(chart.Series) >= analysisMaxSeries {
				break
			}
			years := byKey[key]
			indices := make([][]*float64, len(years))
			for n, y := range years {
				indices[n] = y.index
			}
			chart.Series = append(chart.Series, line(
				fmt.Sprintf("%s (%d tahun)", years[0].occurrence.Name, len(years)), averageIndex(indices, days)))
		}
	}
	chart.Title = eventTitle(window)
	return chart, nil
}

// eventUnit is what an event chart's figures are.
const eventUnit = "indeks"

// windowIndex is one holiday's window of figures as an index on its first
// days, and the price those days averaged; false where the series is too
// thin around it to say anything.
func windowIndex(figures map[string]float64, anchor time.Time, days int) ([]*float64, float64, bool) {
	raw := make([]*float64, days)
	count := 0
	for n := 0; n < days; n++ {
		day := anchor.AddDate(0, 0, n-eventDaysBefore).Format("2006-01-02")
		if value, ok := figures[day]; ok && value > 0 {
			v := value
			raw[n] = &v
			count++
		}
	}
	sum, seen := 0.0, 0
	for n := 0; n < eventBaselineDays; n++ {
		if raw[n] != nil {
			sum += *raw[n]
			seen++
		}
	}
	if seen < 3 || count < eventMinDays {
		return nil, 0, false
	}
	baseline := sum / float64(seen)
	// Every day of the window has a figure, so an average across years is of
	// the same years every day: the last market day's price carries over a
	// weekend or a holiday, and the first one back to the window's start.
	index := make([]*float64, days)
	var last *float64
	for n, value := range raw {
		if value != nil {
			v := math.Round(*value/baseline*10000) / 100
			last = &v
		}
		index[n] = last
	}
	for n := range index {
		if index[n] != nil {
			for m := 0; m < n; m++ {
				index[m] = index[n]
			}
			break
		}
	}
	return index, baseline, true
}

// near is the index on a day, or on the nearest day within three that has a
// figure: markets close on the holiday itself.
func near(index []*float64, at int) *float64 {
	for offset := 0; offset <= 3; offset++ {
		for _, n := range []int{at - offset, at + offset} {
			if n >= 0 && n < len(index) && index[n] != nil {
				return index[n]
			}
		}
	}
	return nil
}

// averageIndex is the day-by-day mean of several windows, where at least one
// has a figure that day.
func averageIndex(indices [][]*float64, days int) []*float64 {
	out := make([]*float64, days)
	for n := 0; n < days; n++ {
		sum, seen := 0.0, 0
		for _, index := range indices {
			if index[n] != nil {
				sum += *index[n]
				seen++
			}
		}
		if seen > 0 {
			v := math.Round(sum/float64(seen)*100) / 100
			out[n] = &v
		}
	}
	return out
}

// eventSubject is what an event chart is the price of, as a headline says
// it: "Harga beras (traditional market)" for the rice member of "Food price —
// traditional market", rather than the market's name with its member.
func eventSubject(w *eventWindow) string {
	commodity := ""
	if open := strings.LastIndex(w.Member, "("); open >= 0 && strings.HasSuffix(w.Member, ")") {
		// The name the source printed: "Indonesia · Rice (Beras)".
		commodity = w.Member[open+1 : len(w.Member)-1]
	} else if at := strings.LastIndex(w.Member, " · "); at >= 0 {
		commodity = w.Member[at+len(" · "):]
	}
	if commodity == "" {
		subject := shortName(w.Label, "")
		if w.Member != "" && w.Member != "Indonesia" {
			subject += " (" + w.Member + ")"
		}
		return subject
	}
	subject := "Harga " + strings.ToLower(commodity)
	if at := strings.LastIndex(w.Label, " — "); at >= 0 {
		subject += " (" + w.Label[at+len(" — "):] + ")"
	}
	return subject
}

func eventTitle(w *eventWindow) string {
	subject := eventSubject(w)
	names := eventNames(w)
	if len(names) > 3 {
		names = append(names[:3], "…")
	}
	return clip(subject+" di sekitar "+strings.Join(names, ", "), 90)
}

// eventNames is each holiday charted, by its name, in chart order.
func eventNames(w *eventWindow) []string {
	var names []string
	for _, key := range w.Keys {
		for _, o := range w.Occurrences {
			if o.Key == key {
				names = append(names, o.Name)
				break
			}
		}
	}
	return names
}

// eventYears is the span of years a holiday's occurrences cover: "2020–2026".
func eventYears(w *eventWindow, key string) (string, int) {
	first, last, count := 0, 0, 0
	for _, o := range w.Occurrences {
		if o.Key != key {
			continue
		}
		count++
		if first == 0 || o.Year < first {
			first = o.Year
		}
		if o.Year > last {
			last = o.Year
		}
	}
	if first == last {
		return fmt.Sprint(first), count
	}
	return fmt.Sprintf("%d–%d", first, last), count
}

// tellEventStory is an event chart's headline, key figures and marks: how far
// the price rose into the holiday, on average, and where it rose most.
func tellEventStory(chart *chartSpec, language string) *chartStory {
	id := language == "id"
	w := chart.Event
	if w == nil {
		return nil
	}
	story := &chartStory{Language: pick(id, "id", "en")}
	day := eventDaysBefore
	for n, series := range chart.Series {
		at := near(series.Values, day)
		if at == nil {
			continue
		}
		figure := storyFigure{
			Series: n, Last: *at, LastPeriod: "H", From: fmt.Sprintf("H-%d", eventDaysBefore),
			Change: *at - 100, ChangeUnit: "percent",
		}
		figure.LastText = localNumber(*at, 1, id)
		figure.ChangeText = signed(figure, id)
		story.Figures = append(story.Figures, figure)
	}
	if len(story.Figures) == 0 {
		return nil
	}
	subject := eventSubject(w)
	names := eventNames(w)
	if len(names) == 1 {
		years, count := eventYears(w, w.Keys[0])
		first := story.Figures[0]
		story.Headline = fmt.Sprintf(pick(id,
			"%s rata-rata %s dari sebulan sebelum %s sampai hari H (%d kali, %s)",
			"%s on average %s from a month before %s to the day (%d times, %s)"),
			subject, changeWords(first, id), names[0], count, years)
		if biggest := largestRise(w); biggest != nil {
			story.Headline += fmt.Sprintf(pick(id, "; terbesar %d (%s)", "; most in %d (%s)"),
				biggest.Year, signedChange(*biggest.AtDay-100, id))
		}
	} else {
		ranked := append([]storyFigure(nil), story.Figures...)
		sort.SliceStable(ranked, func(a, b int) bool { return ranked[a].Change > ranked[b].Change })
		top, bottom := ranked[0], ranked[len(ranked)-1]
		story.Headline = fmt.Sprintf(pick(id,
			"%s paling naik menjelang %s (%s dari sebulan sebelumnya); paling sedikit menjelang %s (%s)",
			"%s rose most into %s (%s from a month before); least into %s (%s)"),
			subject, eventSeriesName(chart.Series[top.Series]), top.ChangeText,
			eventSeriesName(chart.Series[bottom.Series]), bottom.ChangeText)
	}
	story.Headline = clip(story.Headline, 200)

	// The day itself, and the first line's peak within the window.
	first := chart.Series[0]
	if near(first.Values, day) != nil {
		story.Annotations = append(story.Annotations, storyAnnotation{
			Series: 0, Index: day, Kind: "event", Label: pick(id, "Hari H", "The day"),
		})
		if first.Values[day] == nil {
			// Drawn where the line has a point.
			for offset := 1; offset <= 3; offset++ {
				if day-offset >= 0 && first.Values[day-offset] != nil {
					story.Annotations[0].Index = day - offset
					break
				}
			}
		}
	}
	if high, _ := extremes(first.Values); high >= 0 && high != day {
		story.Annotations = append(story.Annotations, storyAnnotation{
			Series: 0, Index: high, Kind: "peak",
			Label: pick(id, "Puncak ", "Peak ") + localNumber(*first.Values[high], 1, id) + " (" + chart.Periods[high] + ")",
		})
	}
	sort.SliceStable(story.Annotations, func(a, b int) bool { return story.Annotations[a].Index < story.Annotations[b].Index })
	return story
}

// eventSeriesName is a line of an event chart without its count of years.
func eventSeriesName(series chartSeries) string {
	name := series.Label
	if at := strings.Index(name, " ("); at > 0 {
		name = name[:at]
	}
	if at := strings.Index(name, " — "); at > 0 {
		name = name[:at]
	}
	return name
}

func largestRise(w *eventWindow) *eventOccurrence {
	var best *eventOccurrence
	for n := range w.Occurrences {
		o := &w.Occurrences[n]
		if o.AtDay != nil && (best == nil || *o.AtDay > *best.AtDay) {
			best = o
		}
	}
	return best
}

func signedChange(change float64, id bool) string {
	return signed(storyFigure{Change: change, ChangeUnit: "percent"}, id)
}

// eventChartPrompt is what the reply is told about an event chart: the only
// figures it may state.
func eventChartPrompt(chart *chartSpec) string {
	w := chart.Event
	var b strings.Builder
	fmt.Fprintf(&b, "\n## Chart shown above your reply\nA chart has been drawn and is on the page above your "+
		"reply: never write that a chart or its data is unavailable, missing or cannot be made. It is an event "+
		"chart, %q: the daily price of [%s](/indicators/%s)", chart.Title, mdText(w.Label), w.Indicator)
	var about []string
	if w.Member != "" {
		about = append(about, w.Member)
	}
	if w.Unit != "" {
		about = append(about, w.Unit)
	}
	if len(about) > 0 {
		fmt.Fprintf(&b, " (%s)", strings.Join(about, "; "))
	}
	fmt.Fprintf(&b, " from %d days before each holiday (H-%d) to %d days after (H+%d). Each window is an index: "+
		"the average of its first %d days is 100, so 102 means 2%% above the price a month before. National "+
		"holidays and cuti bersama are left out, since few markets report on them, and the last market day's "+
		"price stands for them — so the figure \"on the day\" is the price just before the holiday. Holiday dates "+
		"are the national holidays of the SKB 3 Menteri (joint decree of the Ministers of Religious Affairs, "+
		"Manpower and State Apparatus), 2020 onwards.\n", w.Before, w.Before, w.After, w.After, w.Baseline)
	if containsString(w.Keys, "ramadan") {
		b.WriteString("Ramadan's first day is derived as thirty days before Idul Fitri and may be a day off.\n")
	}
	b.WriteString("Lines:\n")
	for _, series := range chart.Series {
		at, after := near(series.Values, w.Before), near(series.Values, len(series.Values)-1)
		fmt.Fprintf(&b, "- %s:", series.Label)
		if at != nil {
			fmt.Fprintf(&b, " %s on the day (%+.1f%%)", figure(*at), *at-100)
		}
		if after != nil {
			fmt.Fprintf(&b, "; %s at H+%d (%+.1f%%)", figure(*after), w.After, *after-100)
		}
		if high, low := extremes(series.Values); high >= 0 {
			fmt.Fprintf(&b, "; high %s at %s; low %s at %s", figure(*series.Values[high]), chart.Periods[high],
				figure(*series.Values[low]), chart.Periods[low])
		}
		b.WriteString("\n")
	}
	b.WriteString("Each holiday, year by year (index on the day; price at the baseline):\n")
	for _, o := range w.Occurrences {
		fmt.Fprintf(&b, "- %s %d (%s):", o.Name, o.Year, o.Date)
		if o.AtDay != nil {
			fmt.Fprintf(&b, " %s (%+.1f%%)", figure(*o.AtDay), *o.AtDay-100)
		} else {
			b.WriteString(" no figure near the day")
		}
		fmt.Fprintf(&b, "; baseline price %s\n", figure(o.BaselinePrice))
	}
	if story := chart.Story; story != nil {
		fmt.Fprintf(&b, "The chart's headline, worked out from its figures: %q\n", story.Headline)
	}
	b.WriteString("For this turn, tell what the chart shows as a short story, in this order, using only the " +
		"figures in this section, rounded:\n" +
		"1. The finding, first, in one sentence: the headline above, in your own words.\n" +
		"2. How it was measured, in one sentence: the price a month before each holiday set to 100, averaged " +
		"over the years listed, linking the series as given.\n" +
		"3. At most three points: the years that rose most or least, whether prices kept rising after the day " +
		"or fell back, and — where several holidays are drawn — which moved prices and which did not.\n" +
		"4. The limits, in one or two sentences: how few years there are; that other things move prices in the " +
		"same weeks (harvests, policy such as the HET, imports) and this does not separate them; that the figures " +
		"are for one place or grade as named.\n" +
		"5. Where to look next: one follow-up question the reader could ask.\n" +
		"No advice or recommendations on policy or business. Do not describe the chart's colours or repeat its " +
		"title. Write the commodity and place exactly as given in brackets. Keep it under 180 words.\n")
	return b.String()
}

// eventCalendarPrompt is the holiday calendar handed to an ordinary turn that
// names a holiday — "kapan cuti bersama lebaran 2026?" — so the reply can
// state the decreed dates rather than guess them.
func (s *Server) eventCalendarPrompt(ctx context.Context, keys []string) string {
	events, err := s.events(ctx, eventFilter{Keys: keys})
	if err != nil || len(events) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString("\n## Holiday calendar\nThe national holidays and cuti bersama the question names, as the SKB 3 " +
		"Menteri fixes them (JDIH KemenPANRB), 2020 onwards. You may state these dates; they are the only dates " +
		"you know. Ramadan's are derived and may be a day off.\n")
	for _, e := range events {
		kind := map[string]string{
			"libur_nasional": "national holiday", "cuti_bersama": "cuti bersama", "ramadan": "Ramadan (approx.)",
		}[e.Kind]
		span := e.StartDate
		if e.EndDate != e.StartDate {
			span += " to " + e.EndDate
		}
		fmt.Fprintf(&b, "- %s %d, %s: %s (%s)\n", e.Name, e.Year, kind, strings.Join(e.Dates, ", "), span)
	}
	return b.String()
}

// eventNameOf is a holiday's name as the calendar gives it.
func eventNameOf(w *eventWindow, key string) string {
	for _, o := range w.Occurrences {
		if o.Key == key {
			return o.Name
		}
	}
	return ""
}

// confirmedEventChart draws the event chart the reader confirmed: the series
// they kept, around the holidays proposed.
func (s *Server) confirmedEventChart(
	ctx context.Context, catalogue assistantCatalogue, indicator Indicator, c chartConfirm, earlier analysisContext,
) *chartSpec {
	var keys []string
	for _, key := range c.Events {
		// Only keys the calendar could hold: the request is the browser's.
		if identifierPattern.MatchString(key) && !containsString(keys, key) && len(keys) < analysisMaxSeries {
			keys = append(keys, key)
		}
	}
	if len(keys) == 0 {
		keys = eventsAsked(strings.Join(earlier.questions, " "))
	}
	if len(keys) == 0 {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, figuresTimeout)
	defer cancel()
	asked := withoutEventWords(strings.Join(earlier.questions, " "))
	chart, err := s.eventChart(ctx, indicator, clip(c.Members[indicator.IndicatorID], 200), keys, searchTerms(asked))
	if err != nil {
		s.log.Warn("assistant.event_chart_failed", "error", err)
		return nil
	}
	if chart != nil {
		source := chartSourceNames(catalogue)(indicator)
		for n := range chart.Series {
			chart.Series[n].Source = source
		}
		s.log.Info("assistant.charted", "kind", "event", "series", len(chart.Series), "confirmed", true)
	}
	return chart
}

// dailySeriesFor is the daily series carrying the commodity the words name
// most figures, and that commodity's name; nil where none does.
func (s *Server) dailySeriesFor(
	ctx context.Context, catalogue assistantCatalogue, terms []string,
) (*Indicator, string) {
	commodities := rankCommodities(catalogue.commodities, terms)
	if len(commodities) == 0 || commodities[0].CommodityID == nil {
		return nil, ""
	}
	commodity := commodities[0]
	from, figures := rollupTable, "sum(o.n)"
	if !s.rolled() {
		figures = "count(*)"
		observations, err := s.source(storage.LayerSilver, "observations")
		if err != nil {
			return nil, ""
		}
		from = observations
	}
	rows, err := s.warehouse.DB().QueryContext(ctx, `
		SELECT indicator_id FROM `+from+` o
		WHERE o.temporal_resolution = 'daily' AND o.commodity_id = ?
		GROUP BY 1 ORDER BY `+figures+` DESC, 1`, *commodity.CommodityID)
	if err != nil {
		s.log.Warn("assistant.daily_series_failed", "error", err)
		return nil, ""
	}
	defer rows.Close()
	byID := make(map[string]int, len(catalogue.series))
	for n, i := range catalogue.series {
		byID[i.IndicatorID] = n
	}
	for rows.Next() {
		var id string
		if rows.Scan(&id) != nil {
			return nil, ""
		}
		if n, ok := byID[id]; ok {
			return &catalogue.series[n], commodity.Name
		}
	}
	return nil, ""
}
