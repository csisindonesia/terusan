package articles

import (
	"strings"
	"testing"
)

func TestParseReadsEachMeasureOnce(t *testing.T) {
	text := `Rice rose {{change indicator=6yugbox8 geo=IDN from=2023-07 to=2024-03}}, and
again {{change indicator=6yugbox8 geo=IDN from=2023-07 to=2024-03}}; the median province
had {{median indicator=j8pe1oe1 period=2023 geo_type=province digits=0}} mm.`
	got := Parse(text)
	if len(got.Invalid) != 0 {
		t.Fatalf("invalid: %v", got.Invalid)
	}
	if len(got.Tokens) != 2 {
		t.Fatalf("%d tokens; want the repeat read once", len(got.Tokens))
	}
	change := got.Tokens[0]
	if change.Measure != "change" || change.From != "2023-07" || change.To != "2024-03" || change.Geo != "IDN" {
		t.Errorf("change read as %+v", change)
	}
	if got.Tokens[1].Digits != 0 || got.Tokens[1].GeoType != "province" {
		t.Errorf("median read as %+v", got.Tokens[1])
	}
}

// A token is a question put to the lake, so everything in it is checked
// before it gets there.
func TestParseRefusesWhatCannotBeAsked(t *testing.T) {
	for raw, why := range map[string]string{
		"{{sum indicator=x period=2023}}":            "not a measure",
		"{{value period=2023}}":                      "indicator",
		"{{value indicator=x}}":                      "period",
		"{{change indicator=x from=2023-07}}":        "from: and to:",
		"{{value indicator=x' period=2023}}":         "identifier",
		"{{value indicator=x period=2023-Q1}}":       "not a period",
		"{{rank indicator=x period=2023}}":           "geo",
		"{{value indicator=x period=2023 where=1}}":  "not an argument",
		"{{value indicator=x period=2023 digits=9}}": "digits",
	} {
		got := Parse(raw)
		reason, ok := got.Invalid[raw]
		if !ok || !strings.Contains(reason, why) {
			t.Errorf("%s: refused %v with %q; want a reason naming %q", raw, ok, reason, why)
		}
	}
}

func TestBuiltFiguresAreLeftForThePortal(t *testing.T) {
	got := Parse("{{figure article=rice-after-the-dry-season key=y2023.rho}}")
	if len(got.Tokens) != 0 || len(got.Invalid) != 0 {
		t.Errorf("parsed %+v; want it passed over", got)
	}
}

func TestFormat(t *testing.T) {
	for _, c := range []struct {
		v      float64
		digits int
		want   string
	}{
		{16017.4, -1, "16,017"}, {12.345, -1, "12.35"}, {-1234.5, 1, "−1,234.5"}, {0.4, 0, "0"},
	} {
		if got := Format(c.v, c.digits); got != c.want {
			t.Errorf("Format(%v, %d) = %q, want %q", c.v, c.digits, got, c.want)
		}
	}
	if got := Percent(18.04, -1); got != "+18.0%" {
		t.Errorf("Percent = %q", got)
	}
	if got := Percent(-0.5, -1); got != "−0.5%" {
		t.Errorf("Percent = %q", got)
	}
	for n, want := range map[int]string{1: "1st", 2: "2nd", 3: "3rd", 11: "11th", 12: "12th", 22: "22nd", 34: "34th"} {
		if got := Ordinal(n); got != want {
			t.Errorf("Ordinal(%d) = %q, want %q", n, got, want)
		}
	}
}

func TestSpanCoversThePeriod(t *testing.T) {
	start, end, err := Span("2024-02")
	if err != nil || start.Format("2006-01-02") != "2024-02-01" || end.Format("2006-01-02") != "2024-03-01" {
		t.Errorf("Span(2024-02) = %v, %v, %v", start, end, err)
	}
}
