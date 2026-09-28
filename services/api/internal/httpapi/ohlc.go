package httpapi

import (
	"regexp"
	"sort"
	"strings"
)

// One price, four series.
//
// Silver stores one figure per observation, so a daily bar is four series —
// `ihsg_open`, `ihsg_high`, `ihsg_low`, `ihsg_close` — and a list of series
// shows the index four times. A reader thinks of it as one thing with four
// readings, so the lists that ask fold the four into the close and name the
// others on it, and a series' own page offers them as a choice.
//
// Detected rather than declared, as the portal detects a candle: nothing in
// the warehouse marks a series as a price, and four series sharing a key and
// a collection under those suffixes are what one is made of.

// OHLC names the four series of one price.
type OHLC struct {
	Open  string `json:"open"`
	High  string `json:"high"`
	Low   string `json:"low"`
	Close string `json:"close"`
}

var ohlcFields = []string{"open", "high", "low", "close"}

// ohlcKey is the prefix and the field a series is one of, or false.
//
// The readable key rather than the identifier: a published identifier is a
// derived code and carries no suffix. Older lakes have no key, and there the
// identifier still is the readable one.
func ohlcKey(i Indicator) (string, string, bool) {
	key := i.IndicatorID
	if i.Slug != nil && *i.Slug != "" {
		key = *i.Slug
	}
	for _, field := range ohlcFields {
		if prefix, ok := strings.CutSuffix(key, "_"+field); ok && prefix != "" {
			dataset := ""
			if i.DatasetID != nil {
				dataset = *i.DatasetID
			}
			return dataset + "\x00" + prefix, field, true
		}
	}
	return "", "", false
}

// closeSuffix is how the publishers' names end on a close: "…, close",
// "… close", "… — close".
var closeSuffix = regexp.MustCompile(`(?i)[\s,—–-]*\bclose$`)

// foldedCount is how many series a folded list would show of these.
func foldedCount(ids []string, sets map[string]*OHLC) int {
	count := 0
	for _, id := range ids {
		if set, ok := sets[id]; !ok || set.Close == id {
			count++
		}
	}
	return count
}

// foldedIDs is the identifiers foldedCount counts, sorted: a set's close
// standing for all four of its series.
func foldedIDs(ids []string, sets map[string]*OHLC) []string {
	folded := make([]string, 0, len(ids))
	for _, id := range ids {
		if set, ok := sets[id]; !ok || set.Close == id {
			folded = append(folded, id)
		}
	}
	sort.Strings(folded)
	return folded
}

// ohlcSets finds every complete set, by the identifier of each member.
func ohlcSets(series []Indicator) map[string]*OHLC {
	groups := map[string]map[string]string{}
	for _, i := range series {
		group, field, ok := ohlcKey(i)
		if !ok {
			continue
		}
		if groups[group] == nil {
			groups[group] = map[string]string{}
		}
		groups[group][field] = i.IndicatorID
	}
	members := map[string]*OHLC{}
	for _, fields := range groups {
		if len(fields) != len(ohlcFields) {
			continue // three of four is not a price; list them as they are
		}
		set := &OHLC{Open: fields["open"], High: fields["high"], Low: fields["low"], Close: fields["close"]}
		for _, id := range fields {
			members[id] = set
		}
	}
	return members
}

// foldOHLC keeps the close of every complete set, carrying the set, and drops
// the other three. Everything else passes through in order.
func foldOHLC(series []Indicator) []Indicator {
	sets := ohlcSets(series)
	if len(sets) == 0 {
		return series
	}
	out := make([]Indicator, 0, len(series))
	for _, i := range series {
		set, ok := sets[i.IndicatorID]
		if !ok {
			out = append(out, i)
			continue
		}
		if set.Close == i.IndicatorID {
			i.OHLC = set
			// Listed as the price, not as its close: "Jakarta Composite
			// Index", with the four readings chosen on its page.
			if i.Name != nil {
				name := closeSuffix.ReplaceAllString(*i.Name, "")
				i.Name = &name
			}
			out = append(out, i)
		}
	}
	return out
}
