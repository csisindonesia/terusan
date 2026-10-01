package httpapi

import (
	"slices"
	"strings"
	"testing"
)

func TestPlacesAreReadFromTheQuestion(t *testing.T) {
	known := []placeName{
		{"ID-32", "Jawa Barat", "province"}, {"ID-31", "DKI Jakarta", "province"},
		{"ID-51", "Bali", "province"}, {"ID-34", "DI Yogyakarta", "province"},
		{"ID-32.04", "Bandung", "regency"}, {"ID-32.73", "Kota Bandung", "regency"},
		{"ID-32.17", "Bandung Barat", "regency"}, {"ID-35.07", "Malang", "regency"},
		{"ID-31.71", "Kota Administrasi Jakarta Pusat", "regency"},
	}
	ids := func(question string) []string {
		var out []string
		for _, p := range matchPlaces(question, known) {
			out = append(out, p.ID)
		}
		return out
	}
	for question, want := range map[string][]string{
		"belanja pegawai APBD Kabupaten Bandung 2015-2023": {"ID-32.04"},
		"belanja pegawai Kota Bandung":                     {"ID-32.73"},
		"PM2.5 di DKI Jakarta":                             {"ID-31"},
		"PM2.5 di Jakarta Pusat, kota":                     {"ID-31.71"},
		"korban banjir di Jawa Barat":                      {"ID-32"},
		"wisman lewat Ngurah Rai Bali":                     {"ID-51"},
		"harga beras di Jogja":                             {"ID-34"},
		"nasib malang petani":                              nil,
		"kredit UMKM nasional":                             nil,
		"kabupaten Bandung Barat dan Kabupaten Bandung":    {"ID-32.17", "ID-32.04"},
	} {
		if got := ids(question); !slices.Equal(got, want) {
			t.Errorf("%q: places %v, want %v", question, got, want)
		}
	}
}

func TestASeriesIsJudgedByTheYearsAtThePlaceAsked(t *testing.T) {
	// APBD personnel spending: 2011 to 2026 across governments, December
	// 2011 and 2026 for Kabupaten Bandung.
	items := map[string]answerItem{
		"/indicators/ze3t0z5b": {Kind: "indicator", ID: "ze3t0z5b", Ref: "/indicators/ze3t0z5b",
			Title: "APBD personnel, realised, year to date — one regional government", Resolution: "monthly",
			Start: "2011-12", End: "2026-09", Places: 466,
			AtPlaces: []placeYears{{Name: "Bandung", Years: []int{2011, 2026}}}},
	}
	// The collection beside it spans every government's years, and is not
	// what Bandung's are judged by.
	items["/datasets/wj6jw4dy"] = answerItem{Kind: "dataset", ID: "wj6jw4dy", Ref: "/datasets/wj6jw4dy",
		Title: "APBD by regional government", Start: "2011-12", End: "2026-09"}
	plan := checkAnswer(answerPlan{Parts: []answerPart{{Asked: "belanja pegawai Kabupaten Bandung", Status: "held",
		Items: []answerPick{{Ref: "/indicators/ze3t0z5b"}, {Ref: "/datasets/wj6jw4dy"}}}}}, items, "2015", "2023", true)
	if plan.Parts[0].Status != "outside" {
		t.Fatalf("status %q, want outside", plan.Parts[0].Status)
	}
	text, _ := renderAnswer(plan, items, "2015", "2023", "id")
	for _, want := range []string{"tidak tersedia untuk 2015–2023", "untuk Bandung hanya 2011, 2026, tidak mencakup tahun yang diminta"} {
		if !strings.Contains(text, want) {
			t.Errorf("the answer lacks %q:\n%s", want, text)
		}
	}
	if strings.Contains(strings.Split(text, "\n")[1], "mencakup semua tahun") {
		t.Errorf("the series' span was taken for Bandung's:\n%s", text)
	}
}

func TestYearRuns(t *testing.T) {
	if got := yearRuns([]int{2011, 2015, 2016, 2017, 2026}); got != "2011, 2015–2017, 2026" {
		t.Errorf("yearRuns = %q", got)
	}
	if any, all := placeCoverage([]int{2014, 2015, 2016}, "2015", "2016"); !any || !all {
		t.Error("2014 to 2016 does not cover 2015 to 2016")
	}
	if any, _ := placeCoverage([]int{2011, 2026}, "2015", "2023"); any {
		t.Error("2011 and 2026 were taken for 2015 to 2023")
	}
}
