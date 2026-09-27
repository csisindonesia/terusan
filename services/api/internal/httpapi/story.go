package httpapi

import (
	"fmt"
	"math"
	"sort"
	"strings"
)

// The story a chart tells, worked out from its figures.
//
// A chart titled "Kurs IDR ke USD vs Inflasi" names what is drawn; one titled
// "Rupiah melemah 24,6% terhadap dolar AS sejak 2021-09, sementara inflasi
// naik 1,6 poin" says what it shows, which is what the reader came for. So
// each chart carries, beside its figures:
//
//   - a headline: the finding, with its number, in the reader's language;
//   - the key figures: each series' latest value and its change over the span;
//   - annotations: the few points worth pointing at — a series' peak, its low,
//     and its sharpest move from one period to the next.
//
// All of it is computed here, from the same figures the chart draws, and
// none of it by the model: a headline is the most-read sentence on the page
// and the last place for an invented number. The model is then handed the
// same story to tell in order — the finding, its baseline, at most three
// points, the limits, and where to look next.

type chartStory struct {
	// The reader's language, which the headline is written in and the
	// portal writes the key figures' numbers in: 17.844 or 17,844.
	Language    string            `json:"language"`
	Headline    string            `json:"headline"`
	Figures     []storyFigure     `json:"figures"`
	Annotations []storyAnnotation `json:"annotations,omitempty"`
}

// storyFigure is one series' key figure: where it ended, and how far it
// moved from where it began.
type storyFigure struct {
	Series     int     `json:"series"`
	Last       float64 `json:"last"`
	LastPeriod string  `json:"last_period"`
	// The change from the first figure to the last: a percentage, or for a
	// rate, percentage points.
	Change     float64 `json:"change"`
	ChangeUnit string  `json:"change_unit"` // "percent" or "points"
	From       string  `json:"from"`
	// The two as the headline writes them, so the key figures and the
	// headline cannot round the same number two ways.
	LastText   string `json:"last_text"`
	ChangeText string `json:"change_text"`
}

// storyAnnotation marks one point on the chart.
type storyAnnotation struct {
	Series int    `json:"series"`
	Index  int    `json:"index"` // into the chart's periods
	Kind   string `json:"kind"`  // peak, low, jump, drop
	Label  string `json:"label"`
}

// maxAnnotations keeps the chart legible: past a handful, marks and their
// labels crowd the lines they are meant to explain.
const maxAnnotations = 4

// tellStory works out the chart's headline, key figures and annotations.
func tellStory(chart *chartSpec, language string) *chartStory {
	id := language == "id"
	story := &chartStory{Language: pick(id, "id", "en")}
	type moved struct {
		index  int
		figure storyFigure
	}
	var movements []moved
	for n, series := range chart.Series {
		first, last := firstLast(series.Values)
		if first < 0 || first == last {
			continue
		}
		start, end := *series.Values[first], *series.Values[last]
		figure := storyFigure{
			Series: n, Last: end, LastPeriod: chart.Periods[last], From: chart.Periods[first],
		}
		if isRate(series.Unit) {
			figure.Change, figure.ChangeUnit = end-start, "points"
		} else if start != 0 {
			figure.Change, figure.ChangeUnit = (end-start)/math.Abs(start)*100, "percent"
		} else {
			continue
		}
		figure.LastText = figureWords(end, series.Unit, id)
		figure.ChangeText = signed(figure, id)
		story.Figures = append(story.Figures, figure)
		movements = append(movements, moved{n, figure})
	}
	if len(movements) == 0 {
		return nil
	}

	name := func(n int) string { return storyName(chart.Series[n]) }
	switch {
	case len(movements) == 1 || chart.Kind == "bar":
		m := movements[0]
		story.Headline = fmt.Sprintf(pick(id, "%s %s sejak %s, ke %s pada %s", "%s %s since %s, to %s in %s"),
			name(m.index), changeWords(m.figure, id), m.figure.From,
			figureWords(m.figure.Last, chart.Series[m.index].Unit, id), m.figure.LastPeriod)
	case len(movements) == 2:
		a, b := movements[0], movements[1]
		story.Headline = fmt.Sprintf(pick(id, "%s %s sejak %s, sementara %s %s", "%s %s since %s, while %s %s"),
			name(a.index), changeWords(a.figure, id), a.figure.From, name(b.index), changeWords(b.figure, id))
		if chart.Correlation != nil {
			story.Headline += correlationWords(*chart.Correlation, id)
		}
	default:
		// Several series: the one that moved most against the one that
		// moved least, which is the comparison a rebased chart is for.
		// Only changes of one kind are ranked against each other: a rate's
		// 1.6 points and a price's 24.6% are not larger and smaller.
		comparable := movements[:0:0]
		for _, m := range movements {
			if m.figure.ChangeUnit == "percent" {
				comparable = append(comparable, m)
			}
		}
		if len(comparable) < 2 {
			comparable = movements
		}
		sorted := append([]moved(nil), comparable...)
		sort.SliceStable(sorted, func(x, y int) bool { return sorted[x].figure.Change > sorted[y].figure.Change })
		top, bottom := sorted[0], sorted[len(sorted)-1]
		story.Headline = fmt.Sprintf(pick(id, "%s bergerak paling jauh (%s) sejak %s; %s paling sedikit (%s)",
			"%s moved most (%s) since %s; %s least (%s)"),
			name(top.index), signed(top.figure, id), top.figure.From, name(bottom.index), signed(bottom.figure, id))
	}
	story.Headline = clip(story.Headline, 180)
	story.Annotations = annotate(chart, id)
	return story
}

// annotate picks the points worth marking: each series' peak and low and its
// sharpest move, for the first two series, and no more than maxAnnotations.
// A scatter has no time axis to mark along, so it has none.
func annotate(chart *chartSpec, id bool) []storyAnnotation {
	if chart.Kind == "scatter" {
		return nil
	}
	var marks []storyAnnotation
	seen := map[[2]int]bool{}
	add := func(series, index int, kind, label string) {
		key := [2]int{series, index}
		if index < 0 || seen[key] || len(marks) >= maxAnnotations {
			return
		}
		seen[key] = true
		marks = append(marks, storyAnnotation{Series: series, Index: index, Kind: kind, Label: label})
	}
	for n, series := range chart.Series {
		if n >= 2 {
			break
		}
		high, low := extremes(series.Values)
		if high >= 0 {
			add(n, high, "peak", pick(id, "Puncak ", "Peak ")+figureWords(*series.Values[high], series.Unit, id))
		}
		if low >= 0 && low != high {
			add(n, low, "low", pick(id, "Terendah ", "Low ")+figureWords(*series.Values[low], series.Unit, id))
		}
	}
	// The sharpest move, if it is not already a peak or a low.
	if index, series, rise := sharpestMove(chart); index >= 0 {
		value := *chart.Series[series].Values[index]
		kind, word := "jump", pick(id, "Lonjakan ke ", "Jump to ")
		if !rise {
			kind, word = "drop", pick(id, "Anjlok ke ", "Drop to ")
		}
		add(series, index, kind, word+figureWords(value, chart.Series[series].Unit, id))
	}
	sort.SliceStable(marks, func(a, b int) bool { return marks[a].Index < marks[b].Index })
	return marks
}

// sharpestMove is the largest change from one period to the next, relative
// to the series' own range, across the first two series.
func sharpestMove(chart *chartSpec) (index, series int, rise bool) {
	best, index, series := 0.0, -1, -1
	for n, s := range chart.Series {
		if n >= 2 {
			break
		}
		high, low := extremes(s.Values)
		if high < 0 || low < 0 {
			continue
		}
		span := *s.Values[high] - *s.Values[low]
		if span == 0 {
			continue
		}
		previous := -1
		for i, v := range s.Values {
			if v == nil {
				continue
			}
			if previous >= 0 {
				move := (*v - *s.Values[previous]) / span
				if math.Abs(move) > math.Abs(best) {
					best, index, series = move, i, n
				}
			}
			previous = i
		}
	}
	// A move of less than a quarter of the range is not worth pointing at.
	if math.Abs(best) < 0.25 {
		return -1, -1, false
	}
	return index, series, best > 0
}

func firstLast(values []*float64) (int, int) {
	first, last := -1, -1
	for n, v := range values {
		if v != nil {
			if first < 0 {
				first = n
			}
			last = n
		}
	}
	return first, last
}

func extremes(values []*float64) (int, int) {
	high, low := -1, -1
	for n, v := range values {
		if v == nil {
			continue
		}
		if high < 0 || *v > *values[high] {
			high = n
		}
		if low < 0 || *v < *values[low] {
			low = n
		}
	}
	return high, low
}

// storyName is a series as a headline names it: without the quote it is the
// close of, and with the place or commodity it was drawn for.
func storyName(series chartSeries) string {
	name := series.Short
	if name == "" {
		name = shortName(series.Label, "")
	}
	if series.Member != "" && series.Member != "Indonesia" {
		name += " (" + series.Member + ")"
	}
	return name
}

func pick(id bool, indonesian, english string) string {
	if id {
		return indonesian
	}
	return english
}

// changeWords is a change as a headline says it: "naik 24,6%", "turun 1,6 poin".
func changeWords(f storyFigure, id bool) string {
	amount := math.Abs(f.Change)
	var number string
	if f.ChangeUnit == "points" {
		number = localNumber(amount, 1, id) + pick(id, " poin", " points")
	} else {
		number = localNumber(amount, 1, id) + "%"
	}
	switch {
	case amount < 0.05:
		return pick(id, "hampir tidak berubah", "barely changed")
	case f.Change > 0:
		return pick(id, "naik ", "rose ") + number
	default:
		return pick(id, "turun ", "fell ") + number
	}
}

func signed(f storyFigure, id bool) string {
	sign := "+"
	if f.Change < 0 {
		sign = "−"
	}
	if f.ChangeUnit == "points" {
		return sign + localNumber(math.Abs(f.Change), 1, id) + pick(id, " poin", " pts")
	}
	return sign + localNumber(math.Abs(f.Change), 1, id) + "%"
}

// correlationWords says what the correlation is, in words, and that it is no
// more than that.
func correlationWords(r float64, id bool) string {
	value := localNumber(r, 2, id)
	switch {
	case r >= 0.7:
		return pick(id, "; keduanya bergerak searah (r = ", "; the two moved together (r = ") + value + ")"
	case r <= -0.7:
		return pick(id, "; keduanya bergerak berlawanan (r = ", "; the two moved in opposite directions (r = ") + value + ")"
	case math.Abs(r) < 0.3:
		return pick(id, "; tanpa hubungan yang jelas (r = ", "; with no clear relationship (r = ") + value + ")"
	default:
		return pick(id, "; hubungannya lemah (r = ", "; weakly related (r = ") + value + ")"
	}
}

// figureWords is a value with its unit, as a reader would say it.
func figureWords(value float64, unit string, id bool) string {
	digits := 0
	switch magnitude := math.Abs(value); {
	case magnitude < 10:
		digits = 2
	case magnitude < 1000:
		digits = 1
	}
	text := localNumber(value, digits, id)
	switch {
	case isRate(unit):
		return text + "%"
	case unit != "":
		return text + " " + unit
	default:
		return text
	}
}

// localNumber writes a number with the reader's separators: 17.844,5 in
// Indonesian, 17,844.5 in English.
func localNumber(value float64, digits int, id bool) string {
	text := fmt.Sprintf("%.*f", digits, value)
	negative := strings.HasPrefix(text, "-")
	text = strings.TrimPrefix(text, "-")
	whole, fraction, _ := strings.Cut(text, ".")
	var grouped strings.Builder
	for n, r := range whole {
		if n > 0 && (len(whole)-n)%3 == 0 {
			grouped.WriteString(pick(id, ".", ","))
		}
		grouped.WriteRune(r)
	}
	out := grouped.String()
	if fraction != "" {
		out += pick(id, ",", ".") + fraction
	}
	if negative {
		out = "−" + out
	}
	return out
}

// shortName is a series' name for a headline: the planner's short name where
// it gave a usable one, and otherwise the catalogue's, cut to its point — the
// part after the last dash of a BPS table title ("… — Cabai Rawit (kg)"), the
// close's name without the quote, or its first five words.
func shortName(label, proposed string) string {
	proposed = strings.Join(strings.Fields(proposed), " ")
	if proposed != "" && len([]rune(proposed)) <= 40 && !strings.ContainsAny(proposed, "0123456789") {
		return proposed
	}
	name := shortLabel(label)
	if at := strings.LastIndex(name, " — "); at >= 0 {
		name = name[at+len(" — "):]
	}
	name = strings.TrimSuffix(name, " for Indonesia")
	if words := strings.Fields(name); len(words) > 5 {
		name = strings.Join(words[:5], " ") + "…"
	}
	return name
}
