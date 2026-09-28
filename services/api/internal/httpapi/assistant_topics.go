package httpapi

import (
	"sort"
	"strings"
)

// Topics: what a question is about, read by the router rather than by its
// words.
//
// The keyword search matches the words a reader typed, and a reader asking
// about decarbonisation types none of the words the data is catalogued by:
// the carbon is in "Emisi GRK", "Tree cover loss" and "Burned area". Jev
// cannot write search terms — it only picks from declared options — so it
// picks one of these topics, and each topic carries the words its data is
// titled and tagged with, in both languages: BPS titles its tables in
// Indonesian, and the Earth Engine, FRED and market series are in English.
//
// The tags among the terms are the ones `terusan_pipelines.tagging` derives
// (emissions, forestry, farmers-terms-of-trade, …). A topic added here should
// have a tagging rule there, or it can only match titles.

// assistantTopic is one topic the router may pick.
type assistantTopic struct {
	// What Jev reads to decide, in English, which is what it was trained on.
	criterion string
	// What the search then looks for: tags and title words, both languages.
	terms []string
}

// assistantTopics is every topic, by the name Jev answers with.
var assistantTopics = map[string]assistantTopic{
	"prices": {"Prices and inflation: consumer prices, CPI, food and commodity prices",
		[]string{"prices", "inflation", "harga", "inflasi"}},
	"food-agriculture": {"Food crops, agriculture, farmers and their terms of trade, rice, livestock",
		[]string{"agriculture", "food", "pertanian", "pangan", "farmers-terms-of-trade", "petani", "padi"}},
	"plantations": {"Plantation crops: palm oil, rubber, coffee, cocoa",
		[]string{"palm-oil", "sawit", "coffee", "kopi", "cocoa", "kakao", "rubber", "karet", "perkebunan", "plantation"}},
	"fisheries": {"Fisheries, fish, aquaculture, fishers",
		[]string{"fisheries", "perikanan", "ikan", "nelayan"}},
	"forestry": {"Forests, forestry, timber, deforestation, tree cover, mangroves",
		[]string{"forestry", "forests", "deforestation", "hutan", "kehutanan", "mangrove", "tree cover"}},
	"land-cover": {"Land cover and land use: built-up land, cropland, vegetation",
		[]string{"land-cover", "land-use", "land cover", "lahan", "vegetation", "built-up", "cropland"}},
	"mining": {"Mining and minerals: nickel, coal mining, tin, copper",
		[]string{"mining", "pertambangan", "tambang", "mineral", "metals", "nickel", "nikel", "coal", "batubara"}},
	"energy": {"Energy and electricity: fuel, power generation, electrification, energy use, renewables",
		[]string{"energy", "energi", "electricity", "listrik", "renewable-energy", "electrification", "bahan bakar", "oil", "gas", "coal"}},
	// Decarbonisation is asked about as a whole and catalogued by its parts:
	// the emissions accounts, and the carbon held in forests and released by
	// fires and burned for energy.
	"emissions": {"Greenhouse gas emissions, carbon, decarbonisation, climate change mitigation, net zero",
		[]string{"emissions", "emisi", "grk", "carbon", "karbon", "climate", "forestry", "deforestation", "fire", "renewable-energy", "energy"}},
	"climate-weather": {"Weather and climate: rainfall, temperature, drought, soil moisture",
		[]string{"climate", "iklim", "rainfall", "hujan", "temperature", "suhu", "drought", "kekeringan", "weather", "cuaca", "soil-moisture"}},
	"disasters": {"Disasters: floods, earthquakes, landslides, forest fires, their victims and damage",
		[]string{"disasters", "bencana", "floods", "banjir", "fire", "kebakaran", "earthquake", "gempa", "landslide", "longsor"}},
	"water": {"Water: surface water, groundwater, drinking water, sanitation",
		[]string{"water", "air minum", "air bersih", "sanitation", "sanitasi", "hydrology", "surface water"}},
	"air-quality": {"Air quality and air pollution",
		[]string{"air-quality", "air quality", "pollution", "polusi", "pencemaran", "aerosol"}},
	"population": {"Population, demography, births, deaths, migration",
		[]string{"population", "penduduk", "demography", "births", "kelahiran", "migration", "migrasi"}},
	"labour": {"Labour, employment, unemployment, wages",
		[]string{"labour", "employment", "unemployment", "pengangguran", "wage", "upah", "tenaga kerja"}},
	"poverty": {"Poverty, inequality, welfare, social assistance",
		[]string{"poverty", "kemiskinan", "miskin", "welfare", "inequality", "gini"}},
	"human-development": {"Human development index, life expectancy, gender development",
		[]string{"human-development", "pembangunan manusia", "ipm", "harapan hidup", "life expectancy", "pembangunan gender"}},
	"education": {"Education, schools, students, literacy",
		[]string{"education", "pendidikan", "sekolah", "school", "siswa", "literacy"}},
	"health": {"Health, disease, nutrition, stunting, hospitals",
		[]string{"health", "kesehatan", "stunting", "gizi", "nutrition", "penyakit", "disease"}},
	"housing": {"Housing, settlements, villages, cities, urbanisation, buildings",
		[]string{"housing", "perumahan", "settlements", "urbanisation", "buildings", "built-up", "desa"}},
	"transport": {"Transport: vehicles, roads, ports, airports, passengers",
		[]string{"transport", "transportasi", "angkutan", "kendaraan", "vehicle", "penumpang", "pelabuhan", "bandara"}},
	"tourism": {"Tourism, hotels, foreign visitors",
		[]string{"tourism", "wisata", "wisatawan", "hotel", "visitor"}},
	"trade": {"Exports, imports, trade balance, tariffs",
		[]string{"trade", "export", "import", "ekspor", "impor", "perdagangan", "tariff"}},
	"industry": {"Industry and manufacturing output",
		[]string{"industry", "manufacturing", "industri"}},
	"gdp": {"GDP, economic growth, national and regional accounts",
		[]string{"gdp", "pdb", "pdrb", "national-accounts", "economic growth", "pertumbuhan ekonomi"}},
	"investment": {"Investment, FDI, capital formation",
		[]string{"investment", "investasi", "fdi", "capital formation", "pmtb"}},
	"fiscal": {"Government budget, APBN, APBD, taxes, debt, transfers to regions",
		[]string{"fiscal", "public-finance", "apbn", "apbd", "anggaran", "tax", "pajak", "debt", "utang"}},
	"monetary-banking": {"Money, banking, credit, interest rates, central bank",
		[]string{"monetary", "banking", "credit", "kredit", "interest-rates", "suku bunga", "bank"}},
	"exchange-rates": {"Exchange rates, rupiah, foreign reserves",
		[]string{"exchange-rate", "exchange rate", "kurs", "nilai tukar rupiah", "rupiah", "reserve", "devisa"}},
	"markets": {"Financial and commodity markets: stocks, bonds, metals, oil and coal prices",
		[]string{"markets", "equities", "bonds", "metals", "commodities", "saham", "obligasi"}},
	"consumption": {"Household consumption and spending, retail sales, consumer surveys",
		[]string{"consumption", "konsumsi", "pengeluaran", "retail", "sentiment", "surveys"}},
	"conflict-security": {"Conflict, violence, crime, security, defence",
		[]string{"conflict", "konflik", "violence", "kekerasan", "crime", "kejahatan", "security", "defence"}},
	"elections": {"Elections, voters, candidates, political parties",
		[]string{"election", "pemilu", "pemilihan", "candidate", "calon", "party", "partai", "suara"}},
	"digital": {"Internet, telecommunications, computers, digital economy",
		[]string{"digital", "internet", "telekomunikasi", "telecommunication", "komputer", "telepon seluler"}},
}

// noTopic is the option for a message about none of them.
const noTopic = "none"

const (
	// How likely a topic must be to join the search. Jev is decisive — the
	// topic it picks is usually above 0.9 — so the second one it gives any
	// real weight to is worth searching too: "transisi energi dan net zero"
	// is energy at 0.77 and emissions at 0.23.
	topicMinProbability = 0.2
	// Past three, the topics stop narrowing the search and start listing
	// the catalogue.
	topicMax = 3
)

// topicQuestion is the router question the topics are asked as.
func topicQuestion() map[string]any {
	criteria := make(map[string]string, len(assistantTopics)+1)
	for name, topic := range assistantTopics {
		criteria[name] = topic.criterion
	}
	criteria[noTopic] = "None of these, or not a question about data"
	return map[string]any{
		"type":         "choice",
		"instructions": "Which topic of data would answer the message?",
		"criteria":     criteria,
	}
}

// pickTopics is the topics worth searching, most likely first.
func pickTopics(probabilities map[string]float64) []string {
	var picked []string
	for name, p := range probabilities {
		if _, known := assistantTopics[name]; known && p >= topicMinProbability {
			picked = append(picked, name)
		}
	}
	sort.Slice(picked, func(a, b int) bool {
		if probabilities[picked[a]] != probabilities[picked[b]] {
			return probabilities[picked[a]] > probabilities[picked[b]]
		}
		return picked[a] < picked[b]
	})
	if len(picked) > topicMax {
		picked = picked[:topicMax]
	}
	return picked
}

// topicTerms is what the topics add to the search, without the words the
// question already has.
func topicTerms(topics []string, have []string) []string {
	seen := make(map[string]bool, len(have))
	for _, term := range have {
		seen[term] = true
	}
	var terms []string
	for _, name := range topics {
		for _, term := range assistantTopics[name].terms {
			term = strings.ToLower(term)
			if !seen[term] {
				seen[term] = true
				terms = append(terms, term)
			}
		}
	}
	return terms
}
