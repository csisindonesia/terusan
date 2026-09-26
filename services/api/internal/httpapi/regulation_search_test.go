package httpapi

import (
	"context"
	"database/sql"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

func TestRegulationQueryWordsKeepAnAcronymsRegulation(t *testing.T) {
	words := regulationQueryWords(readQuestion([]assistantMessage{
		{Role: "user", Content: "Apakah ada PKPU tentang syarat capres?"},
	}))
	for _, want := range []string{"pkpu", "komisi", "pemilihan", "umum", "syarat", "capres", "calon", "presiden"} {
		if words[want] == 0 {
			t.Errorf("%q is not asked for; got %v", want, words)
		}
	}
	for _, unwanted := range []string{"apakah", "ada", "tentang"} {
		if words[unwanted] != 0 {
			t.Errorf("%q is asked for, and matches everything", unwanted)
		}
	}
	// One acronym is one idea: its words together count for more than one
	// word, and each alone for less.
	if words["komisi"] >= words["syarat"] {
		t.Errorf("an acronym's word weighs %v, as much as a word of the question (%v)",
			words["komisi"], words["syarat"])
	}
}

func TestRegulationQueryWordsWeighAFollowUpsTopicLess(t *testing.T) {
	words := regulationQueryWords(readQuestion([]assistantMessage{
		{Role: "user", Content: "syarat capres"},
		{Role: "assistant", Content: "…"},
		{Role: "user", Content: "kalau wapres?"},
	}))
	if words["syarat"] == 0 || words["syarat"] >= words["wapres"] {
		t.Errorf("the earlier topic weighs %v against the new question's %v", words["syarat"], words["wapres"])
	}
}

func TestExcerptStartsNearTheMatch(t *testing.T) {
	text := strings.Repeat("kata pembuka ", 40) + "Syarat untuk menjadi calon Presiden adalah bertakwa"
	got := excerpt(text, []string{"syarat"}, 60)
	if !strings.Contains(got, "Syarat untuk") || !strings.HasPrefix(got, "…") {
		t.Errorf("excerpt %q does not show the match", got)
	}
	if short := excerpt("Syarat calon.", []string{"syarat"}, 60); short != "Syarat calon." {
		t.Errorf("a short article is quoted as %q", short)
	}
}

func TestRegulationsNotAskedForMustMatchTheWholeQuestion(t *testing.T) {
	hits := []regulationHit{
		{Key: "a", Coverage: 1, TitleCoverage: 0.5},
		{Key: "b", Coverage: 0.4, TitleCoverage: 0.4},
		// Every word in an article, none in the title: not about it.
		{Key: "c", Coverage: 1, TitleCoverage: 0},
	}
	if got := regulationsWorthShowing(hits, true, 1); len(got) != 3 {
		t.Errorf("asked for regulations, %d of 3 were shown", len(got))
	}
	got := regulationsWorthShowing(hits, false, 0.5)
	if len(got) != 1 || got[0].Key != "a" {
		t.Errorf("not asked, shown %v; want only the one matching the whole question", got)
	}
	// A question the figures answer as fully is a question about them.
	if got := regulationsWorthShowing(hits, false, 1); len(got) != 0 {
		t.Errorf("not asked, and the catalogue matches all of it, yet %d were shown", len(got))
	}
}

func TestTitlePhrasesTellActsApartByTheirWordsTogether(t *testing.T) {
	if got := titlePhrases("Undang-undang (UU) Nomor 11 Tahun 2020 tentang Cipta Kerja", "uu cipta kerja"); got != 1 {
		t.Errorf("the act named for the phrase holds %d of it", got)
	}
	if got := titlePhrases("Undang-undang (UU) Nomor 28 Tahun 2014 tentang Hak Cipta", "uu cipta kerja"); got != 0 {
		t.Errorf("an act sharing one word holds %d of the phrase", got)
	}
}

func TestCatalogueCoverageCountsTranslatedWords(t *testing.T) {
	name := "Exchange rate, rupiah per US dollar"
	catalogue := assistantCatalogue{series: []Indicator{{IndicatorID: "fx", Name: &name}}}
	q := readQuestion([]assistantMessage{{Role: "user", Content: "kurs rupiah terhadap dolar"}})
	if got := catalogueCoverage(catalogue, q); got < 0.7 {
		t.Errorf("a series named for every word of the question covers %.2f of it", got)
	}
	q = readQuestion([]assistantMessage{{Role: "user", Content: "syarat menjadi calon presiden"}})
	if got := catalogueCoverage(catalogue, q); got != 0 {
		t.Errorf("a series sharing no word covers %.2f", got)
	}
}

// A lake of three regulations, indexed the way scripts/index-regulations.sh
// indexes one, with the stems written out by hand.
func regulationLake(t *testing.T) *Server {
	t.Helper()
	root := t.TempDir()
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	index := filepath.Join(root, "gold", regulationIndex)
	sections := filepath.Join(root, "silver", "regulation_sections", "track=kementerian", "year=2023")
	for _, dir := range []string{"postings", "vocab", "docs", "stats"} {
		if err := os.MkdirAll(filepath.Join(index, dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(sections, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		// d1 is the candidacy rule, whose Pasal 13 lists the requirements;
		// d2 is about campaigning and names a president in passing; d3 is
		// a perda that says nothing of either.
		`COPY (FROM (VALUES
			('calon', 'title', 'd1', '', 1, 6), ('presiden', 'title', 'd1', '', 1, 6),
			('pilih', 'title', 'd1', '', 1, 6), ('komisi', 'title', 'd1', '', 1, 6),
			('syarat', 'pasal', 'd1', '13', 2, 20), ('calon', 'pasal', 'd1', '13', 2, 20),
			('presiden', 'pasal', 'd1', '13', 2, 20),
			('kampanye', 'title', 'd2', '', 1, 5), ('presiden', 'title', 'd2', '', 1, 5),
			('komisi', 'title', 'd2', '', 1, 5), ('pilih', 'title', 'd2', '', 1, 5),
			('pasar', 'title', 'd3', '', 1, 3), ('retribusi', 'title', 'd3', '', 1, 3)
		) AS t(term, field, key, pasal, tf, dl)) TO '` + filepath.Join(index, "postings", "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (FROM (VALUES
			('calon', 'calon'), ('capres', 'capres'), ('presiden', 'presiden'), ('syarat', 'syarat'),
			('persyaratan', 'syarat'), ('komisi', 'komisi'), ('pemilihan', 'pilih'), ('kampanye', 'kampanye'),
			('pasar', 'pasar'), ('retribusi', 'retribusi')
		) AS t(word, term)) TO '` + filepath.Join(index, "vocab", "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (FROM (VALUES
			('d1', 'kementerian', 2023, 'Perlembaga', 'Peraturan Komisi Pemilihan Umum Nomor 19 Tahun 2023 tentang Pencalonan Presiden', 'Komisi Pemilihan Umum', NULL::VARCHAR, NULL::VARCHAR),
			('d2', 'kementerian', 2023, 'Perlembaga', 'Peraturan Komisi Pemilihan Umum Nomor 15 Tahun 2023 tentang Kampanye Presiden', 'Komisi Pemilihan Umum', NULL::VARCHAR, NULL::VARCHAR),
			('d3', 'perda', 2020, 'Perda', 'Retribusi Pasar', 'Kota Bandung', NULL::VARCHAR, NULL::VARCHAR)
		) AS t(key, track, year, instrument, title, region_name, subject, status)) TO '` + filepath.Join(index, "docs", "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (FROM (VALUES ('title', 3, 4.7), ('pasal', 1, 20.0)) AS t(field, n_units, avg_dl))
			TO '` + filepath.Join(index, "stats", "part-0.parquet") + `' (FORMAT parquet)`,
		`COPY (FROM (VALUES
			('d1', 'pasal', '13', '(1) Syarat untuk menjadi calon Presiden dan calon Wakil Presiden adalah bertakwa')
		) AS t(key, kind, pasal, text)) TO '` + filepath.Join(sections, "part-0.parquet") + `' (FORMAT parquet)`,
	} {
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
		cfg:       &config.Config{Storage: cfg},
		storage:   resolver,
		warehouse: warehouse,
		log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

func TestRankRegulationsFindsTheArticleThatAnswers(t *testing.T) {
	s := regulationLake(t)
	hits, err := s.searchRegulations(context.Background(), readQuestion([]assistantMessage{
		{Role: "user", Content: "persyaratan capres"},
	}))
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) == 0 || hits[0].Key != "d1" {
		t.Fatalf("hits %+v; want the candidacy rule first", hits)
	}
	if hits[0].Pasal != "13" || !strings.Contains(hits[0].Excerpt, "Syarat untuk menjadi calon Presiden") {
		t.Errorf("best article %q quoted as %q", hits[0].Pasal, hits[0].Excerpt)
	}
	if hits[0].Coverage < regulationUnaskedCoverage {
		t.Errorf("the rule that answers the question covers %.2f of it", hits[0].Coverage)
	}
	for _, hit := range hits {
		if hit.Key == "d3" {
			t.Error("a regulation sharing no word with the question was returned")
		}
	}
}

func TestRankRegulationsKeepsToTheInstrumentAsked(t *testing.T) {
	s := regulationLake(t)
	hits, err := s.searchRegulations(context.Background(), readQuestion([]assistantMessage{
		{Role: "user", Content: "perda retribusi pasar presiden"},
	}))
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 1 || hits[0].Key != "d3" {
		t.Errorf("hits %+v; want only the perda", hits)
	}
}
