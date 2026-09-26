package httpapi

import "strings"

// Indonesian builds words out of roots with affixes — bencana, kebencanaan,
// penanggulangan bencana — and a search that only matches the word as typed
// finds nothing for "kebencanaan" in a catalogue whose titles say "Disaster".
// indonesianRoots is a light stemmer for that: not a full one (the nasal
// changes of meN-/peN- are left alone), but enough to reach the root a
// reader's derived word is built on, which the translation table and the
// Indonesian titles of regulations can then match.

// Clitics and particles that ride on the end of a word and change nothing
// about its topic.
var particles = []string{"nya", "lah", "kah", "pun"}

// Affixes, longest first so "peng-" is tried before "pe-".
var (
	indonesianPrefixes = []string{
		"peng", "peny", "pem", "pen", "per", "pe", "ke", "ber", "be", "ter",
		"di", "meng", "meny", "mem", "men", "me", "se",
	}
	indonesianSuffixes = []string{"kan", "an", "i"}
	// The circumfixes that make an abstract noun of a root — ke-an, pe-an,
	// per-an — whose root is worth searching for even when the translation
	// table does not know it: "kebakaran" is about "bakar", "perikanan" about
	// "ikan".
	nounPrefixes = map[string]bool{"ke": true, "pe": true, "per": true}
)

// root is one candidate a word may be built on.
type root struct {
	word string
	// Whether it came from stripping a noun-forming circumfix, which makes it
	// safe to search on even when nothing else vouches for it.
	circumfix bool
}

// indonesianRoots returns the roots a word may be built on, shortest affix
// removal first. Every candidate is at least four letters: below that a
// substring matches half the catalogue.
func indonesianRoots(word string) []root {
	for _, particle := range particles {
		if strings.HasSuffix(word, particle) && len(word)-len(particle) >= 4 {
			word = strings.TrimSuffix(word, particle)
			break
		}
	}
	var out []root
	seen := map[string]bool{}
	add := func(candidate string, circumfix bool) {
		if len([]rune(candidate)) < 4 || seen[candidate] {
			return
		}
		seen[candidate] = true
		out = append(out, root{candidate, circumfix})
	}

	bases := []string{word}
	stripped := map[string]string{word: ""}
	for _, suffix := range indonesianSuffixes {
		if strings.HasSuffix(word, suffix) {
			base := strings.TrimSuffix(word, suffix)
			bases = append(bases, base)
			stripped[base] = suffix
		}
	}
	for _, base := range bases {
		if base != word {
			add(base, false)
		}
		for _, prefix := range indonesianPrefixes {
			if strings.HasPrefix(base, prefix) {
				add(strings.TrimPrefix(base, prefix),
					nounPrefixes[prefix] && stripped[base] == "an")
			}
		}
	}
	return out
}

// moreTranslations extends the Indonesian-to-English table with the topics
// researchers here ask about that the first list did not reach: hazards and
// their toll, the environment, health and education, security, elections,
// and the goods the commodity series name.
var moreTranslations = map[string]string{
	// Disasters, and what BNPB counts about them.
	"bencana": "disaster disasters hazard",
	"banjir":  "flood floods flooding", "gempa": "earthquake earthquakes seismic",
	"longsor": "landslide landslides", "tsunami": "tsunami", "kekeringan": "drought droughts",
	"kebakaran": "fire fires wildfire", "karhutla": "fire forest wildfire",
	"erupsi": "eruption volcanic volcano", "letusan": "eruption volcanic",
	"gunung": "volcano volcanic", "puting": "tornado whirlwind", "abrasi": "abrasion coastal",
	"cuaca": "weather", "iklim": "climate", "hujan": "rainfall rain precipitation",
	"suhu": "temperature", "korban": "victims deaths casualties", "meninggal": "deaths died",
	"tewas": "deaths killed", "luka": "injured injuries", "hilang": "missing",
	"pengungsi": "displaced evacuated refugees", "mengungsi": "displaced evacuated",
	"terdampak": "affected", "kerusakan": "damage damaged", "rusak": "damaged damage",
	// Security.
	"kekerasan": "violence violent", "konflik": "conflict", "kerusuhan": "riot unrest",
	"kejahatan": "crime", "kriminal": "crime criminal", "keamanan": "security",
	"pertahanan": "defence defense",
	// People.
	"kesehatan": "health", "penyakit": "disease", "gizi": "nutrition",
	"kematian": "death deaths mortality", "kelahiran": "birth births",
	"pendidikan": "education school", "sekolah": "school schools", "siswa": "students pupils",
	"guru": "teachers", "perumahan": "housing", "desa": "village villages",
	"kota": "city urban", "transportasi": "transport transportation",
	// Environment.
	"lingkungan": "environment environmental", "hutan": "forest forestry",
	"deforestasi": "deforestation", "emisi": "emission emissions",
	"polusi": "pollution", "pencemaran": "pollution", "udara": "air",
	// Elections.
	"pemilihan": "election electoral", "suara": "vote votes", "partai": "party parties",
	// Money.
	"keuangan": "finance financial fiscal",
	// Goods.
	"perikanan": "fisheries fishery fish", "ikan": "fish", "nelayan": "fishers fishermen",
	"perkebunan": "plantation estate crops", "kelapa": "coconut palm", "karet": "rubber",
	"kopi": "coffee", "kakao": "cocoa", "teh": "tea", "tembakau": "tobacco", "garam": "salt",
	"semen": "cement", "baja": "steel", "nikel": "nickel", "timah": "tin",
	"tembaga": "copper", "bauksit": "bauxite",
}

func init() {
	for word, english := range moreTranslations {
		if _, ok := translations[word]; !ok {
			translations[word] = english
		}
	}
	// indonesianFor was built from the table before init ran; rebuild it so
	// "disaster regulations" finds "bencana" too.
	indonesianFor = reverseTranslations()
}

// lastQuestion is the reader's most recent message.
func lastQuestion(messages []assistantMessage) string {
	for i := len(messages) - 1; i >= 0; i-- {
		if messages[i].Role == "user" {
			return messages[i].Content
		}
	}
	return ""
}

// yesNoQuestion is whether a message asks for a yes or a no: a tag question
// ("…bukan?", "…kan?"), one opened by apakah/adakah, or an English one opened
// by an auxiliary verb.
func yesNoQuestion(text string) bool {
	text = strings.ToLower(strings.TrimSpace(text))
	if text == "" {
		return false
	}
	words := strings.FieldsFunc(text, func(r rune) bool {
		return !(r >= 'a' && r <= 'z') && !(r >= '0' && r <= '9')
	})
	if len(words) == 0 {
		return false
	}
	switch words[0] {
	case "apakah", "adakah", "apa", "benarkah", "bisakah", "is", "are", "does", "do",
		"did", "can", "could", "will", "has", "have", "should":
		// "apa" opens "apa saja..." as often as "apa benar...": only a
		// question mark makes it a yes-or-no.
		if words[0] != "apa" || strings.HasSuffix(text, "?") {
			return words[0] != "apa" || len(words) > 1 && words[1] != "saja"
		}
	}
	last := words[len(words)-1]
	return strings.HasSuffix(text, "?") && (last == "bukan" || last == "kan" || last == "ya" || last == "tidak" || last == "belum")
}
