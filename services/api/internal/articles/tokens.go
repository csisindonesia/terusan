package articles

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Figure tokens: a number in an article's prose, written as the question that
// produces it rather than as the answer.
//
//	{{latest indicator=6yugbox8 geo=IDN commodity=rice-medium-grade-1}}
//	{{change indicator=6yugbox8 geo=ID-31 from=2023-07 to=2024-03}}
//	{{median indicator=j8pe1oe1 period=2023-08 geo_type=province}}
//	{{rank indicator=6yugbox8 geo=ID-75 period=2024-03 order=desc}}
//
// The set of measures is closed and every argument is checked here, before
// anything reaches the lake, so a token can only ever ask one of a handful of
// questions of one series. What cannot be said this way — a correlation across
// provinces, a drought ranked against forty years — is an analysis, and is
// written in code (see the portal's src/articles).

// Measures a token may name.
var Measures = map[string]string{
	"value":         "the mean of a series over one period",
	"latest":        "the series' most recent value",
	"latest-period": "the period the most recent value is for",
	"change":        "the percentage change between two periods",
	"mean":          "the mean across places, each place's mean over the period",
	"median":        "the median across places, each place's mean over the period",
	"min":           "the lowest place's mean over the period",
	"max":           "the highest place's mean over the period",
	"rank":          "a place's rank among places over the period",
}

// Token is one parsed figure token.
type Token struct {
	// Raw is the token as written, braces included: what the rendered prose
	// replaces.
	Raw       string
	Measure   string
	Indicator string
	Geo       string
	GeoType   string
	Commodity string
	// Period for value, the across-place measures and rank; From and To for
	// change.
	Period string
	From   string
	To     string
	// Descending ranks the highest first; the default.
	Descending bool
	// Digits after the decimal point; -1 leaves it to the measure.
	Digits int
}

var (
	tokenPattern  = regexp.MustCompile(`\{\{\s*([a-z-]+)((?:\s+[a-z_]+=[^\s{}]+)*)\s*\}\}`)
	argPattern    = regexp.MustCompile(`([a-z_]+)=([^\s{}]+)`)
	loosePattern  = regexp.MustCompile(`\{\{[^{}]*\}\}`)
	periodPattern = regexp.MustCompile(`^\d{4}(-\d{2}(-\d{2})?)?$`)
)

// Parsed is every token in a text, and the ones that could not be read.
type Parsed struct {
	Tokens []Token
	// Invalid maps a token as written to why it was refused.
	Invalid map[string]string
}

// Parse reads every figure token in text.
func Parse(text string) Parsed {
	out := Parsed{Invalid: map[string]string{}}
	seen := map[string]bool{}
	for _, raw := range loosePattern.FindAllString(text, -1) {
		if seen[raw] {
			continue
		}
		seen[raw] = true
		// A figure from a code-built article's own figures file, which the
		// portal holds and resolves; nothing here to evaluate.
		if IsBuiltFigure(raw) {
			continue
		}
		token, err := parseOne(raw)
		if err != nil {
			out.Invalid[raw] = err.Error()
			continue
		}
		out.Tokens = append(out.Tokens, token)
	}
	return out
}

// IsBuiltFigure is a token naming a figure of a code-built article:
// {{figure article=<slug> key=<path>}}.
func IsBuiltFigure(raw string) bool {
	return strings.HasPrefix(strings.TrimSpace(strings.TrimPrefix(raw, "{{")), "figure ")
}

func parseOne(raw string) (Token, error) {
	m := tokenPattern.FindStringSubmatch(raw)
	if m == nil || m[0] != raw {
		return Token{}, fmt.Errorf("write it as {{measure name=value …}}")
	}
	t := Token{Raw: raw, Measure: m[1], Descending: true, Digits: -1}
	if _, ok := Measures[t.Measure]; !ok {
		return Token{}, fmt.Errorf("%q is not a measure; use one of %s", t.Measure, measureList())
	}
	for _, arg := range argPattern.FindAllStringSubmatch(m[2], -1) {
		key, value := arg[1], arg[2]
		switch key {
		case "indicator", "geo", "geo_type", "commodity":
			if !identifier.MatchString(value) {
				return Token{}, fmt.Errorf("%s: %q is not a well-formed identifier", key, value)
			}
			switch key {
			case "indicator":
				t.Indicator = value
			case "geo":
				t.Geo = value
			case "geo_type":
				t.GeoType = value
			case "commodity":
				t.Commodity = value
			}
		case "period", "from", "to":
			if !periodPattern.MatchString(value) {
				return Token{}, fmt.Errorf("%s: %q is not a period written YYYY, YYYY-MM or YYYY-MM-DD", key, value)
			}
			switch key {
			case "period":
				t.Period = value
			case "from":
				t.From = value
			case "to":
				t.To = value
			}
		case "order":
			if value != "asc" && value != "desc" {
				return Token{}, fmt.Errorf("order: asc or desc")
			}
			t.Descending = value == "desc"
		case "digits":
			n, err := strconv.Atoi(value)
			if err != nil || n < 0 || n > 6 {
				return Token{}, fmt.Errorf("digits: a whole number from 0 to 6")
			}
			t.Digits = n
		default:
			return Token{}, fmt.Errorf("%q is not an argument a token takes", key)
		}
	}
	if t.Indicator == "" {
		return Token{}, fmt.Errorf("indicator: every figure is of one series")
	}
	switch t.Measure {
	case "value":
		if t.Period == "" {
			return Token{}, fmt.Errorf("period: which period's value")
		}
	case "change":
		if t.From == "" || t.To == "" {
			return Token{}, fmt.Errorf("from: and to: the two periods to compare")
		}
	case "mean", "median", "min", "max":
		if t.Period == "" {
			return Token{}, fmt.Errorf("period: the period to compare places over")
		}
	case "rank":
		if t.Period == "" || t.Geo == "" {
			return Token{}, fmt.Errorf("period: and geo: the place to rank, and when")
		}
	}
	return t, nil
}

func measureList() string {
	return "value, latest, latest-period, change, mean, median, min, max, rank"
}

// Span is the half-open date range a period written YYYY, YYYY-MM or
// YYYY-MM-DD covers.
func Span(p string) (time.Time, time.Time, error) {
	switch len(p) {
	case 4:
		start, err := time.Parse("2006", p)
		return start, start.AddDate(1, 0, 0), err
	case 7:
		start, err := time.Parse("2006-01", p)
		return start, start.AddDate(0, 1, 0), err
	case 10:
		start, err := time.Parse(time.DateOnly, p)
		return start, start.AddDate(0, 0, 1), err
	}
	return time.Time{}, time.Time{}, fmt.Errorf("%q is not a period", p)
}

// Replace swaps each token in text for what render returns for it.
func Replace(text string, render func(raw string) string) string {
	return loosePattern.ReplaceAllStringFunc(text, render)
}

// Format writes a number the way the portal writes figures: grouped
// thousands, and as many decimals as asked for, or as suits its size.
func Format(v float64, digits int) string {
	if digits < 0 {
		digits = 2
		if abs(v) >= 100 {
			digits = 0
		}
	}
	s := strconv.FormatFloat(v, 'f', digits, 64)
	sign := ""
	if strings.HasPrefix(s, "-") {
		sign, s = "−", s[1:]
	}
	whole, frac, _ := strings.Cut(s, ".")
	var b strings.Builder
	for i, r := range whole {
		if i > 0 && (len(whole)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(r)
	}
	if frac != "" {
		return sign + b.String() + "." + frac
	}
	return sign + b.String()
}

// Percent writes a signed change: +18.0%, −0.5%.
func Percent(v float64, digits int) string {
	if digits < 0 {
		digits = 1
	}
	s := Format(v, digits)
	if v > 0 {
		s = "+" + s
	}
	return s + "%"
}

// Ordinal writes 1st, 2nd, 3rd, 4th, 11th, 21st.
func Ordinal(n int) string {
	suffix := "th"
	if n%100 < 11 || n%100 > 13 {
		switch n % 10 {
		case 1:
			suffix = "st"
		case 2:
			suffix = "nd"
		case 3:
			suffix = "rd"
		}
	}
	return strconv.Itoa(n) + suffix
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}
