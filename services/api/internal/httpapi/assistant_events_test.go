package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

func TestHolidaysAreReadOffTheQuestion(t *testing.T) {
	for question, want := range map[string][]string{
		"lakukan analisis harga beras dengan hari besar keagamaan": religiousHolidays,
		"harga beras menjelang lebaran":                            {"idul_fitri"},
		// Named outright, "hari raya" is that holiday and not all of them.
		"harga cabai saat hari raya idul fitri":            {"idul_fitri"},
		"bandingkan harga daging saat natal dan idul adha": {"natal", "idul_adha"},
		"inflasi selama puasa":                             {"ramadan"},
		// Named beside a word that asks about all: that one first, then the rest.
		"harga beras saat lebaran dan hari besar keagamaan lain": religiousHolidays,
		"harga beras bulan ini":                                  nil,
	} {
		if got := eventsAsked(question); !reflect.DeepEqual(got, want) {
			t.Errorf("%q names %v; want %v", question, got, want)
		}
	}
}

func TestTheSeriesIsSearchedForWithoutTheHoliday(t *testing.T) {
	if got := withoutEventWords("lakukan analisis harga beras dengan hari besar keagamaan"); got != "analisis harga beras" {
		t.Errorf("searched for %q", got)
	}
	if got := withoutEventWords("harga cabai menjelang hari raya idul fitri"); got != "harga cabai" {
		t.Errorf("searched for %q", got)
	}
}

func TestAPriceAroundAHolidayIsAnAnalysisWithoutTheWord(t *testing.T) {
	if !asksAboutPricesAroundEvents("harga beras menjelang lebaran") {
		t.Error("prices around Lebaran not read as an analysis")
	}
	if asksAboutPricesAroundEvents("kapan cuti bersama lebaran 2026") {
		t.Error("a date question read as an analysis")
	}
}

func TestAWindowIsAnIndexOnItsFirstWeekWithGapsCarried(t *testing.T) {
	anchor := time.Date(2024, 4, 10, 0, 0, 0, 0, time.UTC)
	figures := map[string]float64{}
	for day := -30; day <= 14; day++ {
		date := anchor.AddDate(0, 0, day)
		if date.Weekday() == time.Saturday || date.Weekday() == time.Sunday {
			continue // markets closed
		}
		price := 10000.0
		if day >= -10 {
			price = 10500 // up 5% in the last ten days
		}
		figures[date.Format("2006-01-02")] = price
	}
	index, baseline, ok := windowIndex(figures, anchor, eventDaysBefore+eventDaysAfter+1)
	if !ok || baseline != 10000 {
		t.Fatalf("baseline %v, ok %v", baseline, ok)
	}
	for n, v := range index {
		if v == nil {
			t.Fatalf("day %d has no figure: gaps are carried", n-eventDaysBefore)
		}
	}
	if at := *index[eventDaysBefore]; at != 105 {
		t.Errorf("index on the day %v; want 105", at)
	}
	if first := *index[0]; first != 100 {
		t.Errorf("index at H-30 %v; want 100", first)
	}
	if _, _, ok := windowIndex(map[string]float64{}, anchor, 45); ok {
		t.Error("an empty window was read")
	}
}

func TestTheAverageIsOfEveryYearEveryDay(t *testing.T) {
	a := []*float64{ptr(100), ptr(102)}
	b := []*float64{ptr(100), ptr(104)}
	if got := averageIndex([][]*float64{a, b}, 2); *got[1] != 103 {
		t.Errorf("average %v; want 103", *got[1])
	}
}

// holidayLake is a lake with one daily rice price, which rises 5% into two
// Lebarans and jumps 20% on a cuti bersama when few markets report — and the
// calendar that says when those were.
func holidayLake(t *testing.T) *Server {
	t.Helper()
	root := t.TempDir()
	for _, dir := range []string{"observations", "events"} {
		if err := os.MkdirAll(filepath.Join(root, "silver", dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var values []string
	for _, lebaran := range []time.Time{
		time.Date(2023, 4, 22, 0, 0, 0, 0, time.UTC), time.Date(2024, 4, 10, 0, 0, 0, 0, time.UTC),
	} {
		for day := -40; day <= 20; day++ {
			date := lebaran.AddDate(0, 0, day)
			price := 10000.0
			if day >= -10 {
				price = 10500
			}
			if day == -2 {
				price = 12600 // the cuti bersama's thin sample
			}
			values = append(values, fmt.Sprintf("('rice-daily', 'IDN', 'Indonesia', 'rice', 'Beras', %.0f, DATE '%s', '%s', 'daily')",
				price, date.Format("2006-01-02"), date.Format("2006-01-02")))
		}
	}
	statements := []string{
		`COPY (SELECT *, NULL::VARCHAR AS category FROM (VALUES ` + strings.Join(values, ", ") + `) AS t(indicator_id, geo_id, geo_name_raw, commodity_id,
			commodity_name_raw, value, period_start, period, temporal_resolution))
			TO '` + filepath.Join(root, "silver", "observations", "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (SELECT *, NULL::VARCHAR AS name_en, NULL::VARCHAR AS name_printed, 'hijri' AS calendar,
			'IDN' AS geo_id, NULL::VARCHAR AS basis, 'menpan-hari-libur' AS source_id, NULL::VARCHAR AS source_url
		FROM (VALUES
			('f23', 'holiday', 'libur_nasional', 'idul_fitri', 'Idul Fitri', 'Islam', 2023,
			 DATE '2023-04-22', DATE '2023-04-23', [DATE '2023-04-22', DATE '2023-04-23'], false),
			('c23', 'holiday', 'cuti_bersama', 'idul_fitri', 'Idul Fitri', 'Islam', 2023,
			 DATE '2023-04-20', DATE '2023-04-20', [DATE '2023-04-20'], false),
			('f24', 'holiday', 'libur_nasional', 'idul_fitri', 'Idul Fitri', 'Islam', 2024,
			 DATE '2024-04-10', DATE '2024-04-11', [DATE '2024-04-10', DATE '2024-04-11'], false),
			('c24', 'holiday', 'cuti_bersama', 'idul_fitri', 'Idul Fitri', 'Islam', 2024,
			 DATE '2024-04-08', DATE '2024-04-08', [DATE '2024-04-08'], false)
		) AS t(event_id, category, kind, key, name, religion, year, start_date, end_date, dates, approximate))
		TO '` + filepath.Join(root, "silver", "events", "part-0.parquet") + `' (FORMAT parquet)`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			t.Fatalf("%v\n%s", err, statement)
		}
	}
	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	resolver := storage.NewResolver(cfg)
	warehouse, err := query.Open(resolver, "1GB", 1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { warehouse.Close() })
	return &Server{
		cfg: &config.Config{Storage: cfg}, storage: resolver, warehouse: warehouse, cache: cache.Nothing{},
		log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

func TestPricesAreChartedAroundEachLebaran(t *testing.T) {
	s := holidayLake(t)
	rice := indicator("rice-daily", "Food price — traditional market", "IDR/kg", "daily", 120)
	chart, err := s.eventChart(context.Background(), rice, "", []string{"idul_fitri"}, []string{"rice"})
	if err != nil || chart == nil {
		t.Fatalf("chart %v, err %v", chart, err)
	}
	if chart.Kind != "event" || len(chart.Periods) != eventDaysBefore+eventDaysAfter+1 || chart.Periods[eventDaysBefore] != "H" {
		t.Fatalf("periods %v", chart.Periods)
	}
	// The average, then each year.
	if len(chart.Series) != 3 || !strings.Contains(chart.Series[0].Label, "rata-rata 2 tahun") {
		t.Fatalf("lines %+v", chart.Series)
	}
	// The cuti bersama's spike is left out: the day before stands at 105.
	for _, o := range chart.Event.Occurrences {
		if o.AtDay == nil || *o.AtDay != 105 {
			t.Errorf("%d on the day: %v; want 105, without the cuti bersama's 126", o.Year, o.AtDay)
		}
	}
	story := tellStory(chart, "id")
	if story == nil || !strings.Contains(story.Headline, "naik 5,0%") || !strings.Contains(story.Headline, "Idul Fitri") {
		t.Fatalf("headline %+v", story)
	}
	if story.Annotations[0].Kind != "event" && story.Annotations[len(story.Annotations)-1].Kind != "event" {
		t.Errorf("the day is not marked: %+v", story.Annotations)
	}

	prompt := eventChartPrompt(chart)
	for _, want := range []string{"[Food price — traditional market](/indicators/rice-daily)", "Idul Fitri 2023",
		"105.0 (+5.0%)", "cuti bersama are left out"} {
		if !strings.Contains(prompt, want) {
			t.Errorf("prompt lacks %q:\n%s", want, prompt)
		}
	}

	chart.asked = "harga beras menjelang lebaran"
	p := proposalFrom(chart, assistantCatalogue{series: []Indicator{rice}}, "id")
	if len(p.Series) != 1 || p.Series[0].ID != "rice-daily" || len(p.Events) != 1 || p.Events[0].Count != 2 {
		t.Fatalf("proposal %+v", p)
	}
	text := proposalText(p, "id")
	for _, want := range []string{"Hari raya: **Idul Fitri** — 2 kali, 2023–2024", "jendela hari raya", "data harian"} {
		if !strings.Contains(text, want) {
			t.Errorf("proposal lacks %q:\n%s", want, text)
		}
	}
	// Linked once, as the series, not once per year.
	if sources := withChartSources(nil, chart); len(sources) != 1 || sources[0].Label != indicatorTitle(rice) {
		t.Errorf("sources %+v", sources)
	}
}

func TestTheConfirmedEventChartIsTheOneProposed(t *testing.T) {
	s := holidayLake(t)
	catalogue := assistantCatalogue{series: []Indicator{
		indicator("rice-daily", "Food price — traditional market", "IDR/kg", "daily", 120),
	}}
	chart := s.confirmedChart(context.Background(), catalogue, chartConfirm{
		Series: []string{"rice-daily"}, Kind: "event", Events: []string{"idul_fitri", "not a key!"},
	}, analysisContext{questions: []string{"harga beras menjelang lebaran"}})
	if chart == nil || chart.Event == nil || !reflect.DeepEqual(chart.Event.Keys, []string{"idul_fitri"}) {
		t.Fatalf("chart %+v", chart)
	}
}

func TestTheCalendarIsHandedToAQuestionAboutDates(t *testing.T) {
	s := holidayLake(t)
	prompt := s.eventCalendarPrompt(context.Background(), []string{"idul_fitri"})
	for _, want := range []string{"## Holiday calendar", "Idul Fitri 2024, cuti bersama: 2024-04-08",
		"Idul Fitri 2024, national holiday: 2024-04-10, 2024-04-11"} {
		if !strings.Contains(prompt, want) {
			t.Errorf("calendar lacks %q:\n%s", want, prompt)
		}
	}
}

// Against the local lake: the question this was built for is charted.
func TestTheRiceQuestionIsChartedFromTheLake(t *testing.T) {
	if os.Getenv("LAKE_TESTS") == "" {
		t.Skip("reads the local lake; LAKE_TESTS=1 (make test-lake) runs it")
	}
	root := filepath.Join(storage.ProjectRoot(), ".data")
	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	resolver := storage.NewResolver(cfg)
	warehouse, err := query.Open(resolver, "4GB", 4)
	if err != nil {
		t.Fatal(err)
	}
	defer warehouse.Close()
	s := &Server{cfg: &config.Config{Storage: cfg}, storage: resolver, warehouse: warehouse,
		cache: cache.Nothing{}, log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	ctx := context.Background()
	catalogue, err := s.assistantCatalogue(ctx)
	if err != nil {
		t.Fatal(err)
	}
	chart := s.analyse(ctx, catalogue, "lakukan analisis harga beras dengan hari besar keagamaan", "id", analysisContext{})
	if chart == nil || chart.Event == nil || len(chart.Event.Occurrences) < 20 {
		t.Fatalf("chart %+v", chart)
	}
	t.Log(tellStory(chart, "id").Headline)
}
