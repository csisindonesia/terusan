package httpapi

import (
	"context"
	"fmt"
	"math"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Full-text search over the regulations, for the assistant.
//
// The index is built by scripts/index-regulations.sh into
// gold/regulation_search: postings of stemmed terms over two fields — each
// regulation's title (with its subject and region), and the text of each
// article in the central and ministerial corpora — and the stemmer's answer
// for every word the corpus uses. A question is split the way the index
// split the corpus, its words looked up in that vocabulary, and every unit
// holding one of the resulting terms scored with BM25.
//
// What that replaces is ILIKE over titles through hand-kept tables of
// acronyms and synonyms, where a word the tables did not know was a miss that
// nobody saw. Stemming makes "persyaratan", "syarat" and "pencalonan" match
// "Syarat untuk menjadi calon Presiden" in PKPU 19/2023 Pasal 13; IDF makes a
// word as common as "tidak" weigh almost nothing without a list saying so.
// The acronym table is still read, for what no stemmer can know: that KPU is
// the Komisi Pemilihan Umum.

// regulationIndex is the gold dataset the index lives in.
const regulationIndex = "regulation_search"

const (
	// BM25's usual constants: term frequency saturates around a few
	// occurrences, and a title twice the average length has its matches
	// counted a little under two-thirds as much.
	bm25K1     = 1.2
	bm25BTitle = 0.75

	// A match in a title against one in an article. A title says what the
	// regulation is about; an article can name anything in passing.
	regulationTitleWeight = 2.0
	regulationPasalWeight = 1.0

	// The question before the last one keeps its topic in a follow-up, at
	// half the weight: "dan untuk wapres?" after "syarat capres".
	regulationPreviousWeight = 0.5

	// How much of the question a hit must match for regulations to be shown
	// when the reader did not ask for them, and by how much more than the
	// portal's figures match it.
	regulationUnaskedCoverage = 0.7
	regulationUnaskedMargin   = 0.1

	// How much of an article is quoted to the model.
	regulationExcerpt = 240

	// How many are ranked before the few shown are chosen from them, and
	// how many of those may be one body's one kind of instrument.
	regulationCandidates = 40
	regulationPerIssuer  = 3

	// How much more a regulation counts for each pair of the question's
	// words its title holds together.
	regulationPhraseBonus = 0.25

	// How much of the question a regulation not asked for must match in its
	// title: one whose articles cover a question its title says nothing of
	// is not about it — "penduduk miskin per provinsi" is covered, word for
	// word, by an article of the village fund regulation.
	regulationUnaskedTitle = 0.25
)

// Chosen against TestRegulationSearchAgainstTheLake, where the results sit
// on a plateau around these: b from 0.5 to 0.6 and recency from 0.2 to 0.3
// all rank the cases within a couple of points of MRR of each other, and no
// recency at all costs over ten.
const (
	// Articles are normalised for length less than titles: the one that
	// answers is often the long one, a list of every requirement.
	bm25BPasal = 0.5
	// How much more a regulation counts for being recent, reached by 2025:
	// an old rule on a subject has usually been replaced by a newer one.
	regulationRecency = 0.2
)

// instrumentPrior is how much more a match counts in a higher instrument.
// Asked for "the rule on X", a reader wants the act before the ministerial
// regulation that implements it, and a regional one last.
var instrumentPrior = []struct {
	instrument string
	weight     float64
}{
	{"UU", 1.3}, {"Perpu", 1.3}, {"PP", 1.2}, {"Perpres", 1.1},
}

// instrumentPriorSQL is instrumentPrior as the WHEN arms of a CASE.
func instrumentPriorSQL() string {
	var arms []string
	for _, p := range instrumentPrior {
		arms = append(arms, fmt.Sprintf("WHEN %s THEN %g", sqlString(p.instrument), p.weight))
	}
	return strings.Join(arms, " ")
}

// weightedTerm is one index term a question asks for, and how much it counts.
type weightedTerm struct {
	term   string
	weight float64
}

// regulationQueryWords is the words of the question the index is asked for,
// each with its weight.
//
// Words that only say "a regulation" — peraturan, aturan, perda — are left
// out: the instrument is a filter, and "peraturan" is in every title. An
// acronym brings its expansion with it, and the group shares its weight, so
// "kpu" counts as one idea rather than four words; likewise an English word
// and the Indonesian one the titles use.
func regulationQueryWords(q assistantQuery) map[string]float64 {
	words := map[string]float64{}
	add := func(text string, weight float64) {
		for _, word := range indexWords(text) {
			group := []string{word}
			switch phrase, acronym := acronyms[word]; {
			case acronym:
				// Before the filter below: "pkpu" says "a regulation", and
				// says whose.
				group = append(group, indexWords(phrase)...)
			// "data" is noise to the catalogue, where every title is data,
			// and a subject here: "pelindungan data pribadi".
			case stopWords[word] && word != "data", regulationWords[word], instrumentWords[word] != "":
				continue
			case indonesianFor[word] != "":
				group = append(group, indexWords(indonesianFor[word])...)
			}
			share := weight / math.Sqrt(float64(len(group)))
			for _, member := range group {
				words[member] = max(words[member], share)
			}
		}
	}
	add(q.latest, 1)
	add(q.previous, regulationPreviousWeight)
	return words
}

// indexWords splits text the way scripts/index-regulations.sh splits the
// corpus: lower-cased, on anything that is not an ASCII letter or digit.
func indexWords(text string) []string {
	return strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9')
	})
}

// rankRegulations is the regulations that best match the question, by BM25.
//
// Every hit carries its coverage — the share of the question's weight found
// in its title or its best article — so the caller can tell a regulation
// that answers the question from one that shares a word with it.
func (s *Server) rankRegulations(ctx context.Context, q assistantQuery) ([]regulationHit, error) {
	words := regulationQueryWords(q)
	if len(words) == 0 && len(q.instruments) == 0 {
		return nil, nil
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	terms, err := s.regulationTerms(ctx, words)
	if err != nil {
		return nil, err
	}
	if len(terms) == 0 {
		return nil, nil
	}

	postings, err := s.warehouse.Source(storage.LayerGold, regulationIndex, "postings")
	if err != nil {
		return nil, err
	}
	stats, err := s.warehouse.Source(storage.LayerGold, regulationIndex, "stats")
	if err != nil {
		return nil, err
	}
	docs, err := s.warehouse.Source(storage.LayerGold, regulationIndex, "docs")
	if err != nil {
		return nil, err
	}

	// The terms are written into the statement rather than bound: a literal
	// IN list is what DuckDB prunes the postings' row groups by, and every
	// term came out of the index's own vocabulary, which holds [a-z0-9] only.
	values := make([]string, len(terms))
	list := make([]string, len(terms))
	for i, t := range terms {
		values[i] = fmt.Sprintf("(%s, %g)", sqlString(t.term), t.weight)
		list[i] = sqlString(t.term)
	}
	var filter string
	var args []any
	if len(q.instruments) > 0 {
		clause, bound := inClause("docs.instrument", q.instruments)
		filter, args = "WHERE "+clause, bound
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		WITH q(term, w) AS (VALUES %[1]s),
		p AS (SELECT * FROM %[2]s WHERE term IN (%[3]s)),
		df AS (SELECT field, term, count(*) AS df FROM p GROUP BY ALL),
		units AS (
			SELECT p.field, p.key, p.pasal,
			       sum(q.w * ln(1 + (s.n_units - df.df + 0.5) / (df.df + 0.5))
			           * p.tf * %[4]g / (p.tf + %[5]g * (1 - lb.b + lb.b * p.dl / s.avg_dl))) AS bm25,
			       sum(q.w) AS covered
			FROM p JOIN q USING (term) JOIN df USING (field, term) JOIN %[7]s s USING (field)
			JOIN (VALUES ('title', %[6]s), ('pasal', %[14]g)) AS lb(field, b) USING (field)
			GROUP BY ALL
		),
		-- A regulation about the question has several articles on it, and
		-- one that names it in passing has one: its best three count, each
		-- half the one before.
		articles AS (
			SELECT key, pasal, bm25, row_number() OVER (PARTITION BY key ORDER BY bm25 DESC) AS n
			FROM units WHERE field = 'pasal'
		),
		evidence AS (
			SELECT key, sum(bm25 / 2 ^ (n - 1)) FILTER (WHERE n <= 3) AS pasal_score,
			       arg_max(pasal, bm25) AS pasal
			FROM articles GROUP BY key
		),
		scored AS (
			SELECT key,
			       coalesce(max(bm25) FILTER (WHERE field = 'title'), 0) AS title_score,
			       max(covered) AS covered,
			       coalesce(max(covered) FILTER (WHERE field = 'title'), 0) AS title_covered
			FROM units GROUP BY key
		),
		-- The weight a unit could have matched: only terms the index holds,
		-- since one left out as too common can match nothing.
		possible AS (SELECT sum(w) AS total FROM q WHERE term IN (SELECT term FROM df))
		SELECT scored.key, docs.title, coalesce(docs.region_name, ''), coalesce(docs.subject, ''),
		       coalesce(docs.status, ''), coalesce(docs.track, ''), docs.year, coalesce(evidence.pasal, ''),
		       (%[8]g * title_score + %[9]g * coalesce(pasal_score, 0))
		         * CASE docs.instrument %[13]s ELSE 1 END
		         * (1 + %[15]g * least(greatest((coalesce(docs.year, 2000) - 2000) / 25.0, 0), 1)) AS score,
		       coalesce(docs.instrument, ''), coalesce(docs.region_name, ''),
		       covered / possible.total, title_covered / possible.total
		FROM scored LEFT JOIN evidence USING (key) JOIN %[10]s docs USING (key) CROSS JOIN possible
		%[11]s
		ORDER BY score DESC, docs.year DESC NULLS LAST
		LIMIT %[12]d`,
		strings.Join(values, ", "), postings, strings.Join(list, ", "),
		bm25K1+1, bm25K1, fmt.Sprint(bm25BTitle), stats,
		regulationTitleWeight, regulationPasalWeight, docs, filter, regulationCandidates,
		instrumentPriorSQL(), bm25BPasal, regulationRecency),
		args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	type candidate struct {
		hit    regulationHit
		issuer string
	}
	var candidates []candidate
	for rows.Next() {
		var c candidate
		var instrument, region string
		if err := rows.Scan(&c.hit.Key, &c.hit.Title, &c.hit.Region, &c.hit.Subject, &c.hit.Status,
			&c.hit.track, &c.hit.year, &c.hit.Pasal, &c.hit.Score, &instrument, &region,
			&c.hit.Coverage, &c.hit.TitleCoverage); err != nil {
			return nil, err
		}
		c.issuer = instrument + "|" + region
		c.hit.Score *= 1 + regulationPhraseBonus*float64(titlePhrases(c.hit.Title, q.latest))
		candidates = append(candidates, c)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sort.SliceStable(candidates, func(a, b int) bool { return candidates[a].hit.Score > candidates[b].hit.Score })

	// The best few, at most regulationPerIssuer from any one body's one kind
	// of instrument: "aturan ibu kota nusantara" matched eight regulations
	// of the Otorita's head before the act that founded it, and eight of one
	// kind answer less than three of it and the act. Not where the reader
	// named the instrument: "uu cipta kerja" asks for acts, and every act
	// has the same issuer.
	var hits []regulationHit
	issued := map[string]int{}
	for _, c := range candidates {
		if len(hits) == assistantRegulationLimit {
			break
		}
		if len(q.instruments) == 0 && issued[c.issuer] >= regulationPerIssuer {
			continue
		}
		issued[c.issuer]++
		hits = append(hits, c.hit)
	}

	// The article is what says the regulation answers the question, so it is
	// quoted to the model. Answered without it where it cannot be read.
	if err := s.quoteArticles(ctx, hits, words); err != nil {
		s.log.Warn("assistant.regulation_excerpt_failed", "error", err)
	}
	return hits, nil
}

// titlePhrases counts the pairs of the question's consecutive words that the
// title holds side by side. Terms alone cannot tell "cipta kerja" from
// "hak cipta", and a title that names the question's phrase is about it.
func titlePhrases(title, question string) int {
	var words []string
	for _, word := range indexWords(question) {
		if !stopWords[word] && !regulationWords[word] && instrumentWords[word] == "" {
			words = append(words, word)
		}
	}
	title = " " + strings.Join(indexWords(title), " ") + " "
	found := 0
	for i := 1; i < len(words); i++ {
		if strings.Contains(title, " "+words[i-1]+" "+words[i]+" ") {
			found++
		}
	}
	return found
}

// regulationTerms is the index terms the question's words stem to, each at
// the weight of the heaviest word that led to it. A word the corpus never
// uses has no term and drops out.
func (s *Server) regulationTerms(ctx context.Context, words map[string]float64) ([]weightedTerm, error) {
	vocab, err := s.warehouse.Source(storage.LayerGold, regulationIndex, "vocab")
	if err != nil {
		return nil, err
	}
	asked := make([]string, 0, len(words))
	for word := range words {
		asked = append(asked, word)
	}
	sort.Strings(asked)
	clause, args := inClause("word", asked)
	rows, err := s.warehouse.DB().QueryContext(ctx,
		"SELECT word, term FROM "+vocab+" WHERE "+clause, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	weights := map[string]float64{}
	for rows.Next() {
		var word, term string
		if err := rows.Scan(&word, &term); err != nil {
			return nil, err
		}
		weights[term] = max(weights[term], words[word])
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	terms := make([]weightedTerm, 0, len(weights))
	for term, weight := range weights {
		terms = append(terms, weightedTerm{term, weight})
	}
	sort.Slice(terms, func(a, b int) bool { return terms[a].term < terms[b].term })
	return terms, nil
}

// quoteArticles fills in the excerpt of each hit's best article.
//
// The sections are partitioned by track and year, and both are known for
// every hit, so this reads a handful of partitions rather than the gigabyte.
func (s *Server) quoteArticles(ctx context.Context, hits []regulationHit, words map[string]float64) error {
	type unit struct{ key, pasal string }
	wanted := map[unit]int{}
	var keys, tracks []string
	var years []int64
	for i, hit := range hits {
		if hit.Pasal == "" || !hit.year.Valid {
			continue
		}
		wanted[unit{hit.Key, hit.Pasal}] = i
		keys = append(keys, hit.Key)
		tracks = append(tracks, hit.track)
		years = append(years, hit.year.Int64)
	}
	if len(wanted) == 0 || !s.warehouse.Exists(ctx, storage.LayerSilver, "regulation_sections") {
		return nil
	}
	sections, err := s.warehouse.Source(storage.LayerSilver, "regulation_sections")
	if err != nil {
		return err
	}
	keyClause, keyArgs := inClause("key", keys)
	trackClause, trackArgs := inClause("track", tracks)
	yearArgs := make([]any, len(years))
	marks := make([]string, len(years))
	for i, year := range years {
		yearArgs[i], marks[i] = year, "?"
	}
	args := append(append(keyArgs, trackArgs...), yearArgs...)
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
		"SELECT key, pasal, text FROM %s WHERE kind = 'pasal' AND %s AND %s AND year IN (%s)",
		sections, keyClause, trackClause, strings.Join(marks, ", ")), args...)
	if err != nil {
		return err
	}
	defer rows.Close()

	surface := make([]string, 0, len(words))
	for word := range words {
		if len(word) >= 3 {
			surface = append(surface, word)
		}
	}
	for rows.Next() {
		var key, pasal, text string
		if err := rows.Scan(&key, &pasal, &text); err != nil {
			return err
		}
		i, ok := wanted[unit{key, pasal}]
		if !ok || hits[i].Excerpt != "" {
			continue
		}
		hits[i].Excerpt = excerpt(text, surface, regulationExcerpt)
	}
	return rows.Err()
}

// excerpt is n characters of text, starting a little before the first of the
// words it contains, so the quote shows why the article matched.
func excerpt(text string, words []string, n int) string {
	text = strings.Join(strings.Fields(text), " ")
	lower := strings.ToLower(text)
	first := -1
	for _, word := range words {
		if at := strings.Index(lower, word); at >= 0 && (first < 0 || at < first) {
			first = at
		}
	}
	runes := []rune(text)
	start := 0
	if first > n/3 {
		// Back to a word boundary, a third of the quote before the match.
		start = len([]rune(text[:first])) - n/3
		for start > 0 && runes[start-1] != ' ' {
			start--
		}
	}
	end := min(start+n, len(runes))
	quote := string(runes[start:end])
	if start > 0 {
		quote = "…" + quote
	}
	if end < len(runes) {
		quote += "…"
	}
	return quote
}

// regulationsWorthShowing is what of the hits reaches the model.
//
// Asked about regulations, all of them. Not asked, only those that match
// nearly the whole question, and match more of it than the portal's figures
// do: "syarat calon presiden" never says "peraturan" and is answered by one,
// while "kurs rupiah terhadap dolar" is matched as fully by a Bank Indonesia
// regulation as by the exchange-rate series, and is a question about the
// series. How much of a question an article covers cannot tell those apart
// on its own — the articles name everything — but which of the two covers
// more can.
func regulationsWorthShowing(hits []regulationHit, asked bool, catalogue float64) []regulationHit {
	if asked {
		return hits
	}
	var kept []regulationHit
	for _, hit := range hits {
		if hit.Coverage >= regulationUnaskedCoverage && hit.Coverage >= catalogue+regulationUnaskedMargin &&
			hit.TitleCoverage >= regulationUnaskedTitle {
			kept = append(kept, hit)
		}
	}
	return kept
}

// catalogueCoverage is the share of the question's words that the
// best-matching dataset, series or commodity has, each word counted as found
// when it or one of its translations is in the item's names.
func catalogueCoverage(catalogue assistantCatalogue, q assistantQuery) float64 {
	var groups [][]string
	for _, word := range indexWords(q.latest) {
		if regulationWords[word] || instrumentWords[word] != "" {
			continue
		}
		if group := searchTerms(word); len(group) > 0 {
			groups = append(groups, group)
		}
	}
	if len(groups) == 0 {
		return 0
	}

	titles := make(map[string]string, len(catalogue.datasets))
	var texts []string
	for _, d := range catalogue.datasets {
		titles[d.DatasetID] = datasetTitle(d)
	}
	for _, d := range rankDatasets(catalogue.datasets, q.terms) {
		texts = append(texts, strings.ToLower(datasetTitle(d)+" "+strings.Join(d.Tags, " "))+" "+
			lower(d.Slug, d.Description))
	}
	for _, i := range rankSeries(catalogue.series, titles, q.terms) {
		texts = append(texts, strings.ToLower(indicatorTitle(i)+" "+strings.Join(i.Tags, " "))+" "+
			lower(i.Slug, i.Description, i.Unit))
	}
	for _, c := range rankCommodities(catalogue.commodities, q.terms) {
		texts = append(texts, lower(&c.Name, c.Category, c.Subcategory))
	}

	best := 0
	for _, text := range texts {
		matched := 0
		for _, group := range groups {
			if slices.ContainsFunc(group, func(term string) bool { return strings.Contains(text, term) }) {
				matched++
			}
		}
		best = max(best, matched)
	}
	return float64(best) / float64(len(groups))
}

// sqlString quotes a value as a SQL string literal.
func sqlString(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "''") + "'"
}
