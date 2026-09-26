package httpapi

import (
	"database/sql"
	"strings"
	"testing"
	"time"
)

func date(value string) sql.NullTime {
	at, err := time.Parse("2006-01-02", value)
	if err != nil {
		panic(err)
	}
	return sql.NullTime{Time: at, Valid: true}
}

func TestAChartIsDrawnAtTheFinestGranularityItFitsIn(t *testing.T) {
	for _, c := range []struct {
		name    string
		periods int64
		first   string
		last    string
		want    string
	}{
		// Twenty annual figures are twenty points. Nothing to average.
		{"an annual series", 20, "2005-01-01", "2024-01-01", "native"},
		// A decade of daily prices is 3,500 days and 115 months.
		{"a decade of daily prices", 3500, "2017-03-01", "2026-09-21", "month"},
		// Sixty years of them is 720 months but 240 quarters.
		{"sixty years of daily prices", 21900, "1966-01-01", "2026-01-01", "quarter"},
		// Four hundred years is 1,600 quarters and 400 years.
		{"four centuries", 146000, "1626-01-01", "2025-12-31", "year"},
	} {
		got := granularityFor(c.periods, date(c.first), date(c.last), defaultPoints)
		if got != c.want {
			t.Errorf("%s: granularity = %q, want %q", c.name, got, c.want)
		}
	}
}

func TestASeriesWithNoBoundedDatesIsLeftAsPublished(t *testing.T) {
	// Without start dates there is nothing to bucket by, and inventing one
	// would misdate every point. The chart says it cannot draw them all.
	if got := granularityFor(9000, sql.NullTime{}, sql.NullTime{}, 400); got != "native" {
		t.Errorf("granularity = %q, want native", got)
	}
}

func TestEveryBucketWritesACanonicalPeriodLabel(t *testing.T) {
	// A point has to read the way a period does everywhere else, because the
	// chart's axis and the table's column are the same labels to a reader.
	for granularity, want := range map[string]string{
		"month":   "%Y-%m",
		"quarter": "-Q",
		"year":    "%Y",
		"native":  "o.period",
	} {
		if expr := bucketExpr(granularity); !strings.Contains(expr, want) {
			t.Errorf("%s buckets as %q", granularity, expr)
		}
	}
}
