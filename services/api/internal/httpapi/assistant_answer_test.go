package httpapi

import (
	"strings"
	"testing"
)

func answerFixture() map[string]answerItem {
	return map[string]answerItem{
		"/indicators/dvilfazc": {Kind: "indicator", ID: "dvilfazc", Ref: "/indicators/dvilfazc",
			Title:      "Commercial bank credit to micro, small and medium enterprises by project location (SPI)",
			Resolution: "monthly", Start: "2015-06", End: "2025-06", Places: 32},
		"/indicators/svidv63f": {Kind: "indicator", ID: "svidv63f", Ref: "/indicators/svidv63f",
			Title: "Deaths from disasters: floods", Resolution: "annual", Start: "2024", End: "2025", Places: 38},
		"/indicators/imij7qm6": {Kind: "indicator", ID: "imij7qm6", Ref: "/indicators/imij7qm6",
			Title: "Area flooded during the year (Global Flood Database)", Resolution: "annual",
			Start: "2000", End: "2018", Places: 38},
		"/indicators/7lylxdyu": {Kind: "indicator", ID: "7lylxdyu", Ref: "/indicators/7lylxdyu",
			Title:      "Persentase Penduduk Miskin (P0) Menurut Provinsi dan Daerah — Perkotaan",
			Resolution: "2 a year (March and September only)", Start: "2012-03", End: "2026-03", Places: 39},
		"/datasets/uv7fqbgj": {Kind: "dataset", ID: "uv7fqbgj", Ref: "/datasets/uv7fqbgj",
			Title: "Strategic food prices", Start: "2017-03-01", End: "2026-10-01"},
	}
}

func TestAnAnswerPlanIsReadOutOfItsFence(t *testing.T) {
	plan, err := parseAnswerPlan("Here you go:\n```json\n{\"parts\":[{\"asked\":\"kredit UMKM\",\"status\":\"held\"," +
		"\"items\":[{\"ref\":\"/indicators/dvilfazc\",\"fit\":\"kredit UMKM per provinsi\"}]}]}\n```")
	if err != nil || len(plan.Parts) != 1 || plan.Parts[0].Items[0].Ref != "/indicators/dvilfazc" {
		t.Fatalf("plan %+v, err %v", plan, err)
	}
	if _, err := parseAnswerPlan("Portal ini tidak memiliki data."); err == nil {
		t.Error("prose was taken for a plan")
	}
}

func TestAHeldPartIsCheckedAgainstTheYearsAsked(t *testing.T) {
	// The flood question: the model called the area flooded, mapped to 2018,
	// held for 2020.
	plan := checkAnswer(answerPlan{Parts: []answerPart{
		{Asked: "korban meninggal banjir", Status: "held", Items: []answerPick{{Ref: "/indicators/svidv63f"}}},
		{Asked: "luas genangan", Status: "held", Items: []answerPick{{Ref: "[Area](/indicators/imij7qm6)"}}},
		{Asked: "korban luka", Status: "held", Items: []answerPick{{Ref: "/indicators/madeup"}}},
	}}, answerFixture(), "2020", "2020", true)
	for n, want := range []string{"outside", "outside", "missing"} {
		if plan.Parts[n].Status != want {
			t.Errorf("part %q is %q, want %q", plan.Parts[n].Asked, plan.Parts[n].Status, want)
		}
	}
	if len(plan.Parts[1].Items) != 1 || plan.Parts[1].Items[0].Ref != "/indicators/imij7qm6" {
		t.Errorf("a link written whole was not read as its path: %+v", plan.Parts[1].Items)
	}
	if len(plan.Parts[2].Items) != 0 {
		t.Error("an item that was never listed was kept")
	}

	// Held for some of the years asked is close.
	partial := checkAnswer(answerPlan{Parts: []answerPart{
		{Asked: "kredit UMKM", Status: "held", Items: []answerPick{{Ref: "/indicators/dvilfazc"}}},
	}}, answerFixture(), "2010", "2024", true)
	if partial.Parts[0].Status != "close" {
		t.Errorf("2015 to 2025 for 2010 to 2024 is %q, want close", partial.Parts[0].Status)
	}
}

func TestTheAnswerSaysWhatTheCatalogueSays(t *testing.T) {
	items := answerFixture()
	plan := checkAnswer(answerPlan{Parts: []answerPart{
		{Asked: "kredit UMKM Jawa Timur bulanan", Status: "held",
			Items: []answerPick{{Ref: "/indicators/dvilfazc", Fit: "Kredit UMKM per lokasi proyek."}}},
		{Asked: "persentase penduduk miskin kota", Status: "held",
			Items: []answerPick{{Ref: "/indicators/7lylxdyu", Fit: "P0 perkotaan"}}},
		{Asked: "harga beras", Status: "close", Items: []answerPick{{Ref: "/datasets/uv7fqbgj", Fit: "harga per provinsi"}}},
	}}, items, "2018", "2024", true)
	text, keys := renderAnswer(plan, items, "2018", "2024", "id")
	for _, want := range []string{
		"**kredit UMKM Jawa Timur bulanan**: tersedia.",
		"[Commercial bank credit to micro, small and medium enterprises by project location (SPI)](/indicators/dvilfazc) — Kredit UMKM per lokasi proyek. Bulanan; 2015-06 – 2025-06 (mencakup semua tahun yang diminta); 32 wilayah, per provinsi",
		"2 kali setahun (Maret dan September)",
		"**harga beras**: tidak tersedia persis; yang paling mendekati:",
		"[Strategic food prices](/datasets/uv7fqbgj) — harga per provinsi. 2017-03-01 – 2026-10-01 (mencakup semua tahun yang diminta)",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("the answer lacks %q:\n%s", want, text)
		}
	}
	if len(keys) != 3 || keys[0] != "indicator:dvilfazc" || keys[2] != "dataset:uv7fqbgj" {
		t.Errorf("keys %v", keys)
	}
	if strings.Contains(text, "Suggest data") {
		t.Error("a held answer pointed to Suggest data")
	}

	none := checkAnswer(answerPlan{Parts: []answerPart{{Asked: "tamatan S1 per provinsi", Status: "missing"}}},
		items, "2014", "2024", true)
	if text, _ := renderAnswer(none, items, "2014", "2024", "id"); !strings.Contains(text, "tidak tersedia di portal.") ||
		!strings.Contains(text, "Suggest data") {
		t.Errorf("a missing answer reads:\n%s", text)
	}
}

func TestARegionalQuestionIsNotHeldByNationalFigures(t *testing.T) {
	items := map[string]answerItem{
		"/indicators/umkm": {Kind: "indicator", ID: "umkm", Ref: "/indicators/umkm", Start: "2017", End: "2023", Places: 1},
	}
	part := []answerPart{{Asked: "kredit UMKM", Status: "held", Items: []answerPick{{Ref: "/indicators/umkm"}}}}
	if got := checkAnswer(answerPlan{Parts: part}, items, "", "", true).Parts[0].Status; got != "national" {
		t.Errorf("national figures for a regional question are %q", got)
	}
	if got := checkAnswer(answerPlan{Parts: part}, items, "", "", false).Parts[0].Status; got != "held" {
		t.Errorf("national figures for a national question are %q", got)
	}
	if !asksRegion("kredit UMKM di Jawa Timur") || asksRegion("kurs rupiah terhadap dolar") {
		t.Error("asksRegion misread a question")
	}
}

func TestAMissingPartShowsWhatItsWordsName(t *testing.T) {
	// The flood question, as the model planned it: both parts missing, no
	// items, above a list that held both for other years.
	items := answerFixture()
	items["/indicators/hph"] = answerItem{Kind: "indicator", ID: "hph", Ref: "/indicators/hph",
		Title: "Luas Areal Perusahaan Hak Pengusahaan Hutan per Provinsi", Start: "2004", End: "2017", Places: 35}
	plan := checkAnswer(answerPlan{Parts: []answerPart{
		{Asked: "korban meninggal akibat banjir di Jawa Barat tahun 2020", Status: "missing"},
		{Asked: "luas genangannya di Jawa Barat tahun 2020", Status: "missing"},
		{Asked: "korban gempa di Mars", Status: "missing"},
	}}, items, "2020", "2020", true)
	for n, want := range []string{"/indicators/svidv63f", "/indicators/imij7qm6"} {
		part := plan.Parts[n]
		if part.Status != "outside" || len(part.Items) != 1 || part.Items[0].Ref != want {
			t.Errorf("part %q: %s %+v, want outside with %s", part.Asked, part.Status, part.Items, want)
		}
	}
	if plan.Parts[2].Status != "missing" || len(plan.Parts[2].Items) != 0 {
		t.Errorf("a part nothing names got %+v", plan.Parts[2])
	}
	text, _ := renderAnswer(plan, items, "2020", "2020", "id")
	if !strings.Contains(text, "**korban meninggal akibat banjir di Jawa Barat tahun 2020**: tidak tersedia untuk 2020; yang ada hanya untuk tahun lain:") {
		t.Errorf("the answer reads:\n%s", text)
	}
}

func TestAFitDoesNotRepeatTheYears(t *testing.T) {
	got := withoutYears("Harga harian 17 bahan pokok di 513 kabupaten/kota, termasuk beras medium, tapi hanya mencakup tahun 2026")
	if got != "Harga harian 17 bahan pokok di 513 kabupaten/kota, termasuk beras medium" {
		t.Errorf("withoutYears = %q", got)
	}
	plan := checkAnswer(answerPlan{Parts: []answerPart{
		{Asked: "luas genangan", Status: "close", Items: []answerPick{{Ref: "/indicators/imij7qm6"}}},
	}}, answerFixture(), "2020", "2020", true)
	if plan.Parts[0].Status != "outside" {
		t.Errorf("close for none of the years asked is %q, want outside", plan.Parts[0].Status)
	}
}

func TestAOneYearSpanIsWrittenOnce(t *testing.T) {
	item := answerItem{Kind: "indicator", Resolution: "annual", Start: "2025", End: "2025", Places: 38}
	if got := itemFacts(item, "2020", "2020", true); got != "Tahunan; 2025 (tidak mencakup tahun yang diminta); 38 wilayah, per provinsi" {
		t.Errorf("itemFacts = %q", got)
	}
}
