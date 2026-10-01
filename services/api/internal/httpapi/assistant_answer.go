package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// The answer, chosen by the model and written by the server.
//
// The writing model judged the items well and described them badly: it
// picked the right series and then opened "the portal has no monthly data
// for East Java" above a series of monthly figures for 32 provinces, called a
// series from 2017 one that "does not cover 2020", and a flood area mapped to
// 2018 one for 2020. What an item measures is a judgement only a model can
// make; how often, where and when it is measured is in the catalogue. So the
// model is asked for the judgement alone — for each thing the question asks
// for, whether the portal holds it and which listed items show it — and the
// server writes the reply: its opening from that judgement, checked against
// the years each item covers, and every fact beside each item from the
// catalogue rather than from the model.
//
// A turn the model cannot plan — a reply that is not the JSON asked for, or
// names nothing listed — is written by the model as before.

// answerPlan is the model's judgement.
type answerPlan struct {
	Parts []answerPart `json:"parts"`
}

// answerPart is one thing the question asks for.
type answerPart struct {
	Asked  string       `json:"asked"`
	Status string       `json:"status"` // held, close or missing
	Items  []answerPick `json:"items"`
}

type answerPick struct {
	Ref string `json:"ref"`
	Fit string `json:"fit"`
}

// answerItem is a listed item, with the facts the server writes beside it.
type answerItem struct {
	Kind, ID, Title, Ref string
	Resolution           string
	Start, End           string
	Places               int64
	// The years it has figures for at each place the question names, where
	// it names any; see assistant_places.go.
	AtPlaces []placeYears
}

// reach is whether an item covers any, and all, of the years asked: at the
// places asked where there are any, else over its whole span.
func (item answerItem) reach(from, to string) (any, all bool) {
	if len(item.AtPlaces) > 0 {
		all = true
		for _, p := range item.AtPlaces {
			a, b := placeCoverage(p.Years, from, to)
			any = any || a
			all = all && b
		}
		return any, all
	}
	switch coverageNote(item.Start, item.End, from, to) {
	case "", "covers all the years asked":
		return true, true
	case "none of the years asked":
		return false, false
	}
	return true, false
}

const (
	answerMaxParts    = 4
	answerItemsPerAll = 6
	answerFitLength   = 140
	// Long: on a comparison the chart planner has already been asked, and a
	// plan that runs out of time is answered by the free writer that this
	// is here to replace.
	answerTimeout = 45 * time.Second
)

// answerFormat is what the model is told this turn, after the lists.
const answerFormat = `
## How to answer this turn
This turn's format replaces the format rules above. Reply with one JSON object and nothing else:
{"parts":[{"asked":"<one thing the question asks for, in the reader's words>","status":"held|close|missing","items":[{"ref":"<the item's link exactly as listed, e.g. /indicators/abc123>","fit":"<what it measures, and how it differs from what was asked>"}]}]}
- One part per thing the question asks for, each named in a few words: "credit to small businesses in East Java, and its bad loans" is two parts ("credit to small businesses", "bad loans"), "deaths in floods and the area flooded" two, "poverty by province, urban and rural" one, "PM2.5 and NO2" two. Judge each part on its own: one part missing does not make another close. At most 4.
- status "held": a listed item measures that very thing, whatever its years or places — the portal weighs those itself and says where they fall short, so never lower a status for them. "close": only something near it is listed (another measure, a broader total) — including a series that counts something else broken down by what was asked: internet use by level of schooling, or workers by level of schooling, is close for "people with a degree", never held. "missing": nothing listed is about it at all.
- items: the listed items that show the part, best first, at most 3 per part. Never one that is not listed. An item about the very thing for other years or places is still listed under that part. None only for a part that is missing.
- fit: under 15 words, in the reader's language. Say what the item measures, and for "close" how it differs. Never write years, dates, numbers of places, or how often it is measured: the portal writes those beside each item, and writing them twice reads as an error.
`

// planAnswer asks the model for its judgement of the listed items.
func (s *Server) planAnswer(ctx context.Context, prompt string, history []assistantMessage) (answerPlan, error) {
	ctx, cancel := context.WithTimeout(ctx, answerTimeout)
	defer cancel()
	messages := append([]assistantMessage{{Role: "system", Content: prompt + answerFormat}}, history...)
	content, err := s.completeAssistantModel(ctx, messages, 900)
	if err != nil {
		return answerPlan{}, err
	}
	plan, err := parseAnswerPlan(content)
	if err != nil {
		// Asked once more: a small model writes broken JSON now and then,
		// and the free writer it would fall back to is what this replaces.
		s.log.Info("assistant.answer_retried", "error", err)
		if content, err = s.completeAssistantModel(ctx, messages, 900); err != nil {
			return answerPlan{}, err
		}
		plan, err = parseAnswerPlan(content)
	}
	return plan, err
}

// withoutQuoteVariants drops a market series' open, high and low, which
// filled three of the places a question about the ringgit had with one rate
// and left out its close.
func withoutQuoteVariants(series []Indicator) []Indicator {
	out := series[:0:0]
	for _, i := range series {
		if !quoteVariant(i) {
			out = append(out, i)
		}
	}
	return out
}

// parseAnswerPlan reads the model's JSON, which it wraps in a code fence as
// often as not.
func parseAnswerPlan(content string) (answerPlan, error) {
	start, end := strings.Index(content, "{"), strings.LastIndex(content, "}")
	if start < 0 || end <= start {
		return answerPlan{}, fmt.Errorf("answer plan has no JSON: %s", clip(content, 200))
	}
	var plan answerPlan
	if err := json.Unmarshal([]byte(content[start:end+1]), &plan); err != nil {
		return answerPlan{}, fmt.Errorf("answer plan: %w", err)
	}
	if len(plan.Parts) == 0 {
		return answerPlan{}, fmt.Errorf("answer plan has no parts")
	}
	return plan, nil
}

// answerItems is every item the prompt listed, by the link it was listed
// under: the same searches assistantPrompt runs, in the same order.
func answerItems(catalogue assistantCatalogue, q assistantQuery) map[string]answerItem {
	titles := catalogueTitles(catalogue)
	items := map[string]answerItem{}
	for _, d := range rankDatasets(catalogue.datasets, q.terms, q.topicTerms...) {
		ref := "/datasets/" + d.DatasetID
		items[ref] = answerItem{Kind: "dataset", ID: d.DatasetID, Title: datasetTitle(d), Ref: ref,
			Start: d.PeriodStart, End: d.PeriodEnd}
	}
	for _, i := range withoutQuoteVariants(rankSeries(catalogue.series, titles, q.terms, q.topicTerms...)) {
		ref := "/indicators/" + i.IndicatorID
		items[ref] = answerItem{Kind: "indicator", ID: i.IndicatorID, Title: indicatorTitle(i), Ref: ref,
			Resolution: resolutionText(i), Start: i.PeriodStart, End: i.PeriodEnd, Places: i.Geographies}
	}
	for _, c := range rankCommodities(catalogue.commodities, q.terms, q.topicTerms...) {
		ref := "/commodities?q=" + url.QueryEscape(c.Name)
		items[ref] = answerItem{Kind: "commodity", ID: c.Name, Title: c.Name, Ref: ref,
			Start: c.PeriodStart, End: c.PeriodEnd, Places: c.Geographies}
	}
	return items
}

// refPath reads a ref the way the model may write it: a whole link, a path,
// or a path with the portal's host before it.
var refPath = regexp.MustCompile(`/(indicators|datasets)/[A-Za-z0-9_-]+|/commodities\?q=[^)\s"]+`)

// checkAnswer keeps what was listed and makes each status one the catalogue
// bears out: a part "held" by items none of which reaches the years asked is
// missing for those years, and one held by items that reach only some of
// them is close.
//
// A question that names a region is not answered by national figures alone:
// a part held only by national series is one the portal holds nationally.
func checkAnswer(plan answerPlan, items map[string]answerItem, from, to string, regional bool) answerPlan {
	var out answerPlan
	used := 0
	for _, part := range plan.Parts {
		if len(out.Parts) == answerMaxParts {
			break
		}
		part.Asked = strings.TrimSpace(clip(part.Asked, 120))
		if part.Asked == "" {
			continue
		}
		// A series the part's own words name is preferred to one they do
		// not: asked for the ringgit, the model chose the Singapore dollar
		// from a list that held the ringgit first.
		topics := partTopics(part.Asked, items)
		named := map[string]bool{}
		for _, ref := range relatedItems(part.Asked, items) {
			if items[ref].Kind == "indicator" {
				named[ref] = true
			}
		}
		var kept []answerPick
		taken := map[string]bool{}
		for _, pick := range part.Items {
			ref := refPath.FindString(pick.Ref)
			if _, ok := items[ref]; !ok || len(kept) == 3 || used == answerItemsPerAll || taken[ref] {
				continue
			}
			if items[ref].Kind == "indicator" && len(named) > 0 && !named[ref] {
				swapped := ""
				// Only for one that names more of the part than the pick:
				// a title that shares one word with it is no better.
				for _, candidate := range sortedRefs(named, items) {
					if !taken[candidate] && !pickedAmong(part.Items, candidate) &&
						topicHits(topics, items[candidate]) > topicHits(topics, items[ref]) {
						swapped = candidate
						break
					}
				}
				if swapped == "" {
					continue
				}
				ref, pick.Fit = swapped, ""
			}
			taken[ref] = true
			kept = append(kept, answerPick{Ref: ref, Fit: withoutYears(strings.TrimSpace(clip(pick.Fit, answerFitLength)))})
			used++
		}
		if len(kept) == 0 && used < answerItemsPerAll {
			// The model called "deaths in floods in 2020" missing above a
			// list holding deaths in floods for 2024 and 2025: an item the
			// part's own words name is shown, and the years asked judge it.
			for _, ref := range relatedItems(part.Asked, items) {
				if len(kept) == 2 || used == answerItemsPerAll {
					break
				}
				kept = append(kept, answerPick{Ref: ref})
				used++
			}
			if len(kept) > 0 {
				part.Status = "related"
			}
		}
		part.Items = kept
		switch part.Status {
		case "held", "close", "missing":
		case "related":
			part.Status = "missing"
			if (from != "" || placed(kept, items)) && !reachesYears(kept, items, from, to) {
				part.Status = "outside"
			}
		default:
			part.Status = "close"
		}
		if len(kept) == 0 {
			part.Status = "missing"
		}
		if part.Status == "held" && (from != "" || placed(kept, items)) {
			all, any := false, false
			for _, pick := range judged(kept, items) {
				a, b := items[pick.Ref].reach(from, to)
				any, all = any || a, all || b
			}
			switch {
			case !any:
				part.Status = "outside"
			case !all:
				part.Status = "close"
			}
		}
		// Close to the thing, but for none of the years asked, is the
		// thing for other years.
		if part.Status == "close" && (from != "" || placed(kept, items)) && !reachesYears(kept, items, from, to) {
			part.Status = "outside"
		}
		if part.Status == "held" && regional {
			national := true
			for _, pick := range part.Items {
				if item := items[pick.Ref]; item.Kind == "dataset" || item.Places > 1 {
					national = false
				}
			}
			if national {
				part.Status = "national"
			}
		}
		out.Parts = append(out.Parts, part)
	}
	return out
}

// fitYears is a clause of a fit that names a year.
var fitYears = regexp.MustCompile(`\b(19|20)\d{2}\b`)

// withoutYears drops the clauses of a fit that give years: the model writes
// "but only 2024 to 2026" however it is told not to, beside the server's own
// span, and the two read as a contradiction when they differ.
func withoutYears(fit string) string {
	var kept []string
	for _, clause := range strings.Split(fit, ", ") {
		if !fitYears.MatchString(clause) {
			kept = append(kept, clause)
		}
	}
	return strings.Join(kept, ", ")
}

// reachesYears reports whether any of the items covers any of the years
// asked.
func reachesYears(picks []answerPick, items map[string]answerItem, from, to string) bool {
	for _, pick := range judged(picks, items) {
		if any, _ := items[pick.Ref].reach(from, to); any {
			return true
		}
	}
	return false
}

// sortedRefs is a set of refs, widest first as relatedItems orders them.
func sortedRefs(set map[string]bool, items map[string]answerItem) []string {
	refs := make([]string, 0, len(set))
	for ref := range set {
		refs = append(refs, ref)
	}
	sort.Slice(refs, func(a, b int) bool {
		if items[refs[a]].Places != items[refs[b]].Places {
			return items[refs[a]].Places > items[refs[b]].Places
		}
		return refs[a] < refs[b]
	})
	return refs
}

// pickedAmong reports whether the model chose a ref itself, by any spelling.
func pickedAmong(picks []answerPick, ref string) bool {
	for _, pick := range picks {
		if refPath.FindString(pick.Ref) == ref {
			return true
		}
	}
	return false
}

// judged is the items a part's years are judged by: those read at the places
// asked, where any were. A collection's span is every place's, and it held
// "Kabupaten Bandung, 2015 to 2023" beside a series with nothing for Bandung
// in those years.
func judged(picks []answerPick, items map[string]answerItem) []answerPick {
	if !placed(picks, items) {
		return picks
	}
	var out []answerPick
	for _, pick := range picks {
		if len(items[pick.Ref].AtPlaces) > 0 {
			out = append(out, pick)
		}
	}
	return out
}

// placed reports whether any of the items was read at the places asked.
func placed(picks []answerPick, items map[string]answerItem) bool {
	for _, pick := range picks {
		if len(items[pick.Ref].AtPlaces) > 0 {
			return true
		}
	}
	return false
}

// frequencyWords say how often, which is the catalogue's to say: "bulanan"
// is in every monthly inflation title, and a question about monthly PM2.5
// was answered with them.
var frequencyWords = map[string]bool{
	"harian": true, "mingguan": true, "bulanan": true, "triwulanan": true, "kuartalan": true,
	"semesteran": true, "tahunan": true, "daily": true, "weekly": true, "monthly": true,
	"quarterly": true, "annual": true, "yearly": true,
}

// partTopics is what a part asks for, as the chart's sides are read: each
// word the tables translate, each that a listed title carries, and each that
// mixes letters and digits ("pm2", "no2"), which is a name however short.
func partTopics(asked string, items map[string]answerItem) [][]string {
	var topics [][]string
	for _, word := range questionWords(asked) {
		if coverageNoise[word] || stopWords[word] || regionWords[word] || frequencyWords[word] ||
			isNumber(word) || isAnalysisWord(word) {
			continue
		}
		coded := strings.ContainsAny(word, "0123456789")
		if len([]rune(word)) < 4 && translations[word] == "" && !coded {
			continue
		}
		// A word the tables translate is a topic whether or not anything
		// listed is named for it: "gempa" names earthquakes, and a list
		// without them holds nothing for "korban gempa". Any other word is
		// a topic only where something listed carries it.
		terms := searchTerms(word)
		if coded {
			terms = []string{word}
		}
		known := len(terms) > 1 || coded
		for _, item := range items {
			if known || score(terms, field{strings.ToLower(item.Title), 1}) > 0 {
				topics = append(topics, terms)
				break
			}
		}
	}
	return topics
}

// topicHits is how many of a part's topics an item's title carries.
func topicHits(topics [][]string, item answerItem) int {
	title := strings.ToLower(item.Title)
	hits := 0
	for _, terms := range topics {
		if score(terms, field{title, 1}) > 0 {
			hits++
		}
	}
	return hits
}

// enough is whether an item carries enough of a part's topics to be about
// it: every one of a short part, most of a long one.
func enough(hits, topics int) bool {
	return hits == topics || topics >= 3 && 2*hits >= topics+1
}

// relatedItems is the listed items whose titles name what a part asks for.
// Widest first.
func relatedItems(asked string, items map[string]answerItem) []string {
	topics := partTopics(asked, items)
	if len(topics) == 0 {
		return nil
	}
	var refs []string
	for ref, item := range items {
		if enough(topicHits(topics, item), len(topics)) {
			refs = append(refs, ref)
		}
	}
	sort.Slice(refs, func(a, b int) bool {
		if items[refs[a]].Places != items[refs[b]].Places {
			return items[refs[a]].Places > items[refs[b]].Places
		}
		return refs[a] < refs[b]
	})
	return refs
}

// regionWords are the words that name a place inside Indonesia: the levels,
// and the words the 38 provinces are named with.
var regionWords = func() map[string]bool {
	set := map[string]bool{}
	for _, word := range strings.Fields(`provinsi kabupaten kota kab daerah wilayah regional province
		provinces provincial regency regencies city district aceh sumatera sumatra riau jambi bengkulu
		lampung bangka belitung jakarta jawa banten yogyakarta diy bali nusa ntb ntt kalimantan
		sulawesi gorontalo maluku papua`) {
		set[word] = true
	}
	return set
}()

// asksRegion reports whether a question names a place below the nation.
func asksRegion(question string) bool {
	for _, word := range questionWords(question) {
		if regionWords[word] {
			return true
		}
	}
	return false
}

// renderAnswer writes the reply from the checked plan.
func renderAnswer(plan answerPlan, items map[string]answerItem, from, to, language string) (string, []string) {
	id := language == "id"
	var b strings.Builder
	var keys []string
	missingAll := true
	for n, part := range plan.Parts {
		if n > 0 {
			b.WriteString("\n")
		}
		fmt.Fprintf(&b, "**%s**: %s\n", mdText(part.Asked), statusText(part.Status, from, to, len(part.Items) > 0, id))
		if part.Status != "missing" {
			missingAll = false
		}
		for _, pick := range part.Items {
			item := items[pick.Ref]
			fmt.Fprintf(&b, "- [%s](%s)", mdText(item.Title), item.Ref)
			if pick.Fit != "" {
				fmt.Fprintf(&b, " — %s", mdText(strings.TrimSuffix(pick.Fit, ".")))
			}
			if facts := itemFacts(item, from, to, id); facts != "" {
				fmt.Fprintf(&b, ". %s", facts)
			}
			b.WriteString("\n")
			// A commodity is a dimension, not a record the conversation
			// keeps a link to.
			if item.Kind != "commodity" {
				keys = append(keys, item.Kind+":"+item.ID)
			}
		}
	}
	if missingAll {
		if id {
			b.WriteString("\nUsulkan data ini lewat **Suggest data** di bilah atas.\n")
		} else {
			b.WriteString("\nYou can ask for it with **Suggest data** in the top bar.\n")
		}
	}
	return b.String(), keys
}

func statusText(status, from, to string, hasItems, id bool) string {
	years := from
	if to != from {
		years = from + "–" + to
	}
	switch {
	case status == "held" && id:
		return "tersedia."
	case status == "held":
		return "held in the portal."
	case status == "close" && id:
		return "tidak tersedia persis; yang paling mendekati:"
	case status == "close":
		return "not held exactly; the closest:"
	case status == "national" && id:
		return "tidak tersedia per wilayah; yang ada hanya angka nasional:"
	case status == "national":
		return "not held by region; only national figures:"
	case status == "outside" && from == "" && id:
		return "tidak ada data untuk tempat yang diminta; yang ada hanya untuk tempat lain:"
	case status == "outside" && from == "":
		return "no figures for the place asked; only for other places:"
	case status == "outside" && id:
		return fmt.Sprintf("tidak tersedia untuk %s; yang ada hanya untuk tahun atau tempat lain:", years)
	case status == "outside":
		return fmt.Sprintf("not held for %s; only for other years or places:", years)
	case hasItems && id:
		return "tidak tersedia di portal; yang terkait:"
	case hasItems:
		return "not held in the portal; related:"
	case id:
		return "tidak tersedia di portal."
	default:
		return "not held in the portal."
	}
}

// itemFacts is what the catalogue says of an item: how often, when and how
// that sits against the years asked, and where.
func itemFacts(item answerItem, from, to string, id bool) string {
	var parts []string
	if item.Resolution != "" {
		parts = append(parts, resolutionWords(item.Resolution, id))
	}
	if item.Start != "" && item.End != "" {
		span := item.Start
		if item.End != item.Start {
			span += " – " + item.End
		}
		// At the places asked, the series' own span says nothing of them.
		if note := coverageNote(item.Start, item.End, from, to); note != "" && len(item.AtPlaces) == 0 {
			span += " (" + coverageWords(note, id) + ")"
		}
		parts = append(parts, span)
	}
	if item.Kind != "dataset" {
		parts = append(parts, placesWords(item.Places, id))
	}
	if facts := placeFacts(item, from, to, id); facts != "" {
		parts = append(parts, facts)
	}
	text := strings.Join(parts, "; ")
	if text != "" {
		text = strings.ToUpper(text[:1]) + text[1:]
	}
	return text
}

var resolutionsID = map[string]string{
	"annual": "tahunan", "monthly": "bulanan", "daily": "harian", "quarterly": "triwulanan",
	"weekly": "mingguan", "semiannual": "semesteran", "decadal": "per dasawarsa",
}

var monthsID = []string{"", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
	"Agustus", "September", "Oktober", "November", "Desember"}

// fewMonthsText reads resolutionText's "2 a year (March and September only)".
var fewMonthsText = regexp.MustCompile(`^(\d+) a year \((.+) only\)$`)

func resolutionWords(resolution string, id bool) string {
	if !id {
		return resolution
	}
	if match := fewMonthsText.FindStringSubmatch(resolution); match != nil {
		var names []string
		for _, name := range strings.Split(match[2], " and ") {
			for n := 1; n <= 12; n++ {
				if time.Month(n).String() == name {
					name = monthsID[n]
				}
			}
			names = append(names, name)
		}
		return fmt.Sprintf("%s kali setahun (%s)", match[1], strings.Join(names, " dan "))
	}
	if words, ok := resolutionsID[resolution]; ok {
		return words
	}
	return resolution
}

var spanNote = regexp.MustCompile(`^covers only (\d{4})(?: to (\d{4}))? of the years asked$`)

func coverageWords(note string, id bool) string {
	if !id {
		return note
	}
	switch note {
	case "covers all the years asked":
		return "mencakup semua tahun yang diminta"
	case "none of the years asked":
		return "tidak mencakup tahun yang diminta"
	}
	if match := spanNote.FindStringSubmatch(note); match != nil {
		if match[2] == "" {
			return "hanya " + match[1] + " dari tahun yang diminta"
		}
		return "hanya " + match[1] + "–" + match[2] + " dari tahun yang diminta"
	}
	return note
}

// placesWords is where an item is measured: nationally, by province, or by
// regency, by how many places it has figures for.
func placesWords(places int64, id bool) string {
	switch {
	case places <= 1 && id:
		return "nasional saja"
	case places <= 1:
		return "national only"
	case places >= 400 && id:
		return strconv.FormatInt(places, 10) + " wilayah, per kabupaten/kota"
	case places >= 400:
		return strconv.FormatInt(places, 10) + " places, by regency"
	case places >= 30 && id:
		return strconv.FormatInt(places, 10) + " wilayah, per provinsi"
	case places >= 30:
		return strconv.FormatInt(places, 10) + " places, by province"
	case id:
		return strconv.FormatInt(places, 10) + " wilayah"
	default:
		return strconv.FormatInt(places, 10) + " places"
	}
}
