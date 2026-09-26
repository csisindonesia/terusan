package httpapi

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// The news monitoring endpoints.
//
// News is neither an observation nor a document, and pressing it into either
// would be a disservice to both. An observation is one indicator in one period
// in one place; an article is a piece of writing by a newspaper. And the
// documents endpoint lists what ministries publish — a few thousand news links
// dropped into it would bury the PDFs it exists to serve.
//
// So these routes are their own, and they serve three things: the papers being
// read, the articles collected from them, and the incidents coded out of those
// articles.
//
// What they do not serve is the article text. The archive holds the full page
// because a coding has to be reproducible from it, but the words belong to the
// newspaper that wrote them: these responses carry a title, a lead, the coded
// fields and a link back to the publisher.

// NewsOutlet is one newspaper, with what the crawl has actually got from it.
//
// The registry half comes from the outlet dimension and the counts from the
// corpus, joined outwards: an outlet with no articles is still an outlet, and
// showing it with a zero is the only way a reader can see that a province's
// coverage has gone quiet. An outlet missing from the list entirely would look
// like a province nobody reports violence in.
type NewsOutlet struct {
	Host     string  `json:"host"`
	Outlet   string  `json:"outlet"`
	Province string  `json:"province"`
	GeoID    *string `json:"geo_id,omitempty"`
	BaseURL  string  `json:"base_url"`
	// Which search-page shape the crawl uses for this host. Operational, but
	// it is the first thing to look at when an outlet stops yielding.
	Adapter string `json:"adapter"`
	Active  bool   `json:"active"`
	// Why an outlet was corrected or retired — that its domain now redirects
	// into a network, or that the title no longer has a working site.
	Note *string `json:"note,omitempty"`

	// Kept: articles stored, because they are about an issue. A small
	// fraction of what the paper published.
	Articles int64 `json:"articles"`
	// Read at all — the denominator. The crawl reads everything an outlet
	// published in the window and keeps only what is about an issue, so
	// without this a rise in incidents cannot be told from a crawl that
	// reached further that week.
	Scanned int64 `json:"scanned"`
	// Matched the vocabulary, which is a candidate rather than a finding;
	// and recorded, which is the classifier agreeing.
	Matched     int64   `json:"matched"`
	Recorded    int64   `json:"recorded"`
	FirstSeen   *string `json:"first_seen,omitempty"`
	LastSeen    *string `json:"last_seen,omitempty"`
	Screenshots int64   `json:"screenshots"`
}

// NewsArticle is one piece collected from an outlet.
//
// Every article in the corpus matched an issue. The crawl reads everything a
// paper published and keeps only what an issue claimed, so there is no such
// thing here as an article about nothing — what the rest amounted to is in the
// daily tallies, as a number.
type NewsArticle struct {
	// The Bronze document id, which is what addresses this article's own page.
	DocumentID string  `json:"document_id"`
	URL        string  `json:"url"`
	Title      string  `json:"title"`
	Lead       string  `json:"lead"`
	Outlet     string  `json:"outlet"`
	Host       string  `json:"outlet_host"`
	Province   string  `json:"outlet_province"`
	Published  *string `json:"published_at,omitempty"`
	// Which issues claimed this article, and the lexicon terms that matched.
	Issues []string `json:"issues"`
	Terms  []string `json:"matched_terms"`
	// Whether a full-page image of this article was taken when it was
	// collected. The page as it looked that day, which is what a human
	// verifier reads and what survives a correction or a deletion.
	Screenshot bool `json:"screenshot"`
	// The coding, where one was made. Absent means the article matched nothing;
	// present with Accepted false means a classifier read it and said no.
	Coding *NewsCoding `json:"coding,omitempty"`
}

// NewsCoding is what the classifier made of one article.
type NewsCoding struct {
	Profile  string   `json:"profile"`
	Engine   string   `json:"engine"`
	Accepted *bool    `json:"accepted"`
	Gate     *float64 `json:"gate_probability,omitempty"`

	Province     *string `json:"province,omitempty"`
	District     *string `json:"district_city,omitempty"`
	Date         *string `json:"date,omitempty"`
	ViolenceForm *string `json:"violence_form,omitempty"`
	WeaponType   *string `json:"weapon_type,omitempty"`
	IssueType    *string `json:"issue_type,omitempty"`
	Actor1       *string `json:"actor1,omitempty"`
	Actor2       *string `json:"actor2,omitempty"`
	Intervene    *string `json:"intervene,omitempty"`
	Deaths       *int64  `json:"deaths,omitempty"`
	Injured      *int64  `json:"injured,omitempty"`
	// How far the incident got: tension, a limited beating, violence that
	// spread, a riot. Machine-coded only — VEWS has no such column, so this is
	// never comparable with a human coding and is labelled as its own thing
	// wherever it is shown.
	Escalation *string `json:"escalation,omitempty"`
}

// NewsEvent is one incident, after several papers' reports of it were collapsed.
type NewsEvent struct {
	EventID  string  `json:"event_id"`
	Profile  string  `json:"profile"`
	Date     *string `json:"date,omitempty"`
	Province *string `json:"province,omitempty"`
	District *string `json:"district_city,omitempty"`

	ViolenceForm *string `json:"violence_form,omitempty"`
	WeaponType   *string `json:"weapon_type,omitempty"`
	IssueType    *string `json:"issue_type,omitempty"`
	Actor1       *string `json:"actor1,omitempty"`
	Actor2       *string `json:"actor2,omitempty"`
	Intervene    *string `json:"intervene,omitempty"`
	Deaths       *int64  `json:"deaths,omitempty"`
	Injured      *int64  `json:"injured,omitempty"`
	// The worst escalation any of the reports described. Papers file at
	// different moments — one while a crowd was gathering, one after the
	// market burned — and the event is the worse of them, the same rule the
	// casualty figures follow.
	Escalation *string `json:"escalation,omitempty"`

	// How many separate reports were collapsed into this, and from whom. One
	// report is not weaker evidence than five, but five is a different kind of
	// fact and a reader should be able to see which they have.
	ReportCount int64    `json:"report_count"`
	Outlets     []string `json:"outlets"`
	Sources     []string `json:"sources"`
}

// currentParserVersion narrows a Bronze read to the newest extraction.
//
// Re-extracting under an improved parser appends rows rather than replacing
// them — it is what makes a partition traceable to the code that produced it —
// so a read without this serves every version of every row at once. The
// pipelines apply the same rule when they normalize and when they cluster.
//
// It also keeps the rows homogeneous, which matters more than it should: the
// DuckDB the API embeds resolves a MAP subscript inconsistently across files
// whose key sets differ, and an extractor that learns a new column makes them
// differ. Reading one version at a time means one key set at a time. Without
// it, sixty-seven newspapers came back as a hundred and thirteen, each with a
// share of its own figures.
func currentParserVersion(records, dataset string) string {
	return "(SELECT max(try_cast(parser_version AS INTEGER)) FROM " + records +
		" WHERE dataset = '" + dataset + "')"
}

const (
	newsArticlesDataset = "news-articles"
	newsShotsDataset    = "news-screenshots"
	newsTalliesDataset  = "news-daily-tallies"
	newsEventsDataset   = "news-violence-events"
	newsCodingsDataset  = "news-violence-codings"
)

var newsOutletOrder = map[string]string{
	"outlet":    "outlet ASC",
	"-outlet":   "outlet DESC",
	"province":  "province ASC, outlet ASC",
	"-province": "province DESC, outlet ASC",
	"articles":  "articles ASC, outlet ASC",
	"-articles": "articles DESC, outlet ASC",
	"scanned":   "scanned ASC, outlet ASC",
	"-scanned":  "scanned DESC, outlet ASC",
	"recorded":  "recorded ASC, outlet ASC",
	"-recorded": "recorded DESC, outlet ASC",
}

// handleNewsOutlets lists the newspapers being read.
func (s *Server) handleNewsOutlets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	province, err := stringParam(r, "province", searchPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	search, err := stringParam(r, "q", searchPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	activeOnly, err := boolParam(r, "active")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	order, err := sortOrder(r, newsOutletOrder, "province")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	// Before a dimension has ever been published this is empty rather than an
	// error: the honest answer for a warehouse where the pipeline has not run.
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "news_outlets") {
		writeData(w, []NewsOutlet{}, &Meta{Limit: limit, Layer: "silver"})
		return
	}
	outlets, err := s.source(storage.LayerSilver, "news_outlets")
	if err != nil {
		internalError(w, s.log, "resolve news outlets", err)
		return
	}

	from := outlets + " o LEFT JOIN (" + s.newsOutletStats(ctx) + ") c ON c.host = o.host"

	clauses := []string{"true"}
	args := []any{}
	if province != "" {
		clauses = append(clauses, "upper(o.province) = upper(?)")
		args = append(args, province)
	}
	if activeOnly {
		clauses = append(clauses, "o.active")
	}
	if search != "" {
		clauses = append(clauses, "(o.outlet ILIKE ? OR o.host ILIKE ? OR o.province ILIKE ?)")
		like := "%" + search + "%"
		args = append(args, like, like, like)
	}
	where := " WHERE " + strings.Join(clauses, " AND ")

	var total int64
	if err := s.warehouse.DB().QueryRowContext(
		ctx, "SELECT count(*) FROM "+from+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count news outlets", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx,
		"SELECT o.host, o.outlet, o.province, o.geo_id, o.base_url, o.adapter, o.active, o.note, "+
			"coalesce(c.articles, 0), coalesce(c.scanned, 0), coalesce(c.matched, 0), "+
			"coalesce(c.recorded, 0), coalesce(c.screenshots, 0), "+
			"c.first_seen, c.last_seen FROM "+from+where+
			" ORDER BY "+order+" LIMIT ? OFFSET ?",
		append(append([]any{}, args...), limit, offset)...,
	)
	if err != nil {
		internalError(w, s.log, "query news outlets", err)
		return
	}
	defer rows.Close()

	out := []NewsOutlet{}
	for rows.Next() {
		var item NewsOutlet
		if err := rows.Scan(
			&item.Host, &item.Outlet, &item.Province, &item.GeoID, &item.BaseURL,
			&item.Adapter, &item.Active, &item.Note,
			&item.Articles, &item.Scanned, &item.Matched, &item.Recorded,
			&item.Screenshots, &item.FirstSeen, &item.LastSeen,
		); err != nil {
			internalError(w, s.log, "scan news outlets", err)
			return
		}
		out = append(out, item)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read news outlets", err)
		return
	}

	writeData(w, out, &Meta{
		Total: total, Limit: limit, Offset: offset,
		HasMore: int64(offset+len(out)) < total, Layer: "silver",
		Source: "news-monitoring",
	})
}

// newsOutletStats is the per-outlet corpus summary, as a subquery.
//
// Returned as SQL text rather than run separately so the outlet list is one
// statement: the alternative is a query per outlet, which is sixty-nine round
// trips to render one page.
func (s *Server) newsOutletStats(ctx context.Context) string {
	empty := "SELECT CAST(NULL AS VARCHAR) AS host, 0 AS articles, 0 AS scanned, " +
		"0 AS matched, 0 AS recorded, 0 AS screenshots, " +
		"CAST(NULL AS VARCHAR) AS first_seen, CAST(NULL AS VARCHAR) AS last_seen WHERE false"
	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		return empty
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		return empty
	}

	// Two collections, counted separately and joined on the outlet. What was
	// kept is a count of stored articles; what was read is a sum of the daily
	// tallies, because the articles that were only counted were never stored
	// and there is nothing to count rows of.
	// The host is projected in an inner select and grouped by name, never by
	// position. `GROUP BY 1` reads differently in the DuckDB the API embeds
	// than in the one the pipelines use — there it split each outlet into a
	// group per parser version, so every re-extracted outlet appeared on the
	// page twice with a share of its own figures. Grouping by a named column
	// cannot mean two things.
	kept := "SELECT host, " +
		"count(*) FILTER (WHERE dataset = '" + newsArticlesDataset + "') AS articles, " +
		"count(*) FILTER (WHERE dataset = '" + newsShotsDataset + "') AS screenshots, " +
		"CAST(min(published_at) AS VARCHAR) AS first_seen, " +
		"CAST(max(published_at) AS VARCHAR) AS last_seen " +
		"FROM (SELECT columns['outlet_host'] AS host, dataset, published_at FROM " + records +
		" WHERE dataset IN ('" + newsArticlesDataset + "', '" + newsShotsDataset +
		"') AND columns['outlet_host'] IS NOT NULL AND try_cast(parser_version AS INTEGER) = " +
		currentParserVersion(records, newsArticlesDataset) + ") GROUP BY host"

	read := "SELECT host, " +
		"sum(try_cast(scanned AS BIGINT)) AS scanned, " +
		"sum(try_cast(matched AS BIGINT)) AS matched, " +
		"sum(try_cast(recorded AS BIGINT)) AS recorded, " +
		// When the crawl last had this outlet in front of it. Taken from the
		// tallies rather than from stored articles, because almost no outlet
		// stores any: on most days a paper publishes nothing that is
		// collective violence, and a column that went blank for all of them
		// would say the crawl had never run when it had run that morning.
		// `retrieved_at`, not the tally's own day key: that key is the date the
		// articles were *published*, and a paper that post-dates a piece would
		// then report having been collected next month.
		"CAST(max(retrieved_at) AS DATE) AS last_read " +
		"FROM (SELECT columns['outlet_host'] AS host, columns['scanned'] AS scanned, " +
		"columns['matched'] AS matched, columns['recorded'] AS recorded, retrieved_at FROM " +
		records + " WHERE dataset = '" + newsTalliesDataset +
		"' AND columns['outlet_host'] IS NOT NULL AND try_cast(parser_version AS INTEGER) = " +
		currentParserVersion(records, newsTalliesDataset) + ") GROUP BY host"

	// A full join: an outlet can have tallies and no kept articles — a paper
	// that published nothing about the issue that week, which is a real and
	// common state — and, before the tallies existed, kept articles and no
	// tallies.
	return "SELECT coalesce(k.host, t.host) AS host, " +
		"coalesce(k.articles, 0) AS articles, coalesce(t.scanned, 0) AS scanned, " +
		"coalesce(t.matched, 0) AS matched, coalesce(t.recorded, 0) AS recorded, " +
		"coalesce(k.screenshots, 0) AS screenshots, k.first_seen, " +
		// The later of the two: an outlet read this morning and one whose last
		// kept article is from March should both report this morning.
		"greatest(coalesce(CAST(t.last_read AS VARCHAR), ''), coalesce(k.last_seen, '')) AS last_seen " +
		"FROM (" + kept + ") k FULL OUTER JOIN (" + read + ") t ON t.host = k.host"
}

// handleNewsOutlet serves one newspaper.
func (s *Server) handleNewsOutlet(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	host, err := pathIdentifier(r, "host")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "news_outlets") {
		notFound(w, "the news outlet dimension has not been published",
			"run `terusan silver dimensions`")
		return
	}
	outlets, err := s.source(storage.LayerSilver, "news_outlets")
	if err != nil {
		internalError(w, s.log, "resolve news outlets", err)
		return
	}

	var item NewsOutlet
	err = s.warehouse.DB().QueryRowContext(ctx,
		"SELECT o.host, o.outlet, o.province, o.geo_id, o.base_url, o.adapter, o.active, o.note, "+
			"coalesce(c.articles, 0), coalesce(c.scanned, 0), coalesce(c.matched, 0), "+
			"coalesce(c.recorded, 0), coalesce(c.screenshots, 0), "+
			"c.first_seen, c.last_seen FROM "+outlets+" o LEFT JOIN ("+
			s.newsOutletStats(ctx)+") c ON c.host = o.host WHERE o.host = ?", host,
	).Scan(
		&item.Host, &item.Outlet, &item.Province, &item.GeoID, &item.BaseURL,
		&item.Adapter, &item.Active, &item.Note,
		&item.Articles, &item.Scanned, &item.Matched, &item.Recorded,
		&item.Screenshots, &item.FirstSeen, &item.LastSeen,
	)
	if err == sql.ErrNoRows {
		notFound(w, "no such news outlet", host)
		return
	}
	if err != nil {
		internalError(w, s.log, "query news outlet", err)
		return
	}
	writeData(w, item, &Meta{Layer: "silver", Source: "news-monitoring"})
}

// NewsTally is one day of crawling, from one newspaper's side of it.
//
// The crawl's own log, and the only place the articles it read and threw away
// are counted at all: an article that is not about a monitored issue is never
// stored, so `Scanned` minus `Recorded` is a number with no rows behind it by
// design. Without it a quiet week and a crawl that never ran look identical.
type NewsTally struct {
	// The day the crawl ran, not the day the articles were published. The
	// tally's own key is a publication date, and a paper that post-dates a
	// piece would otherwise file its own collection in the future.
	Date string `json:"date"`
	// Candidates discovery turned up, before the window and the gate. Zero
	// against a reachable site means the sitemap, the feed, the section page
	// and the search all came back empty — a fault in this repository's
	// adapters rather than a paper with nothing to say.
	Discovered int64 `json:"discovered"`
	// Read in full: fetched, dated and put to the dictionary. The denominator.
	Scanned int64 `json:"scanned"`
	// Carried the dictionary's terms — a candidate, not a finding.
	Matched int64 `json:"matched"`
	// Kept: the classifier agreed it was about the issue. Two out of a hundred
	// is the ordinary day.
	Recorded int64 `json:"recorded"`
	// How many times the crawl visited this outlet that day. The list is
	// sharded across the working day, so an outlet is normally visited once —
	// two visits and one of them reading nothing is a shard that was re-run.
	Runs int64 `json:"runs"`
}

// handleNewsOutletTallies serves one newspaper's crawl log, a row per day.
func (s *Server) handleNewsOutletTallies(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	host, err := pathIdentifier(r, "host")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		writeData(w, []NewsTally{}, &Meta{Limit: limit, Layer: "bronze"})
		return
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		internalError(w, s.log, "resolve records", err)
		return
	}

	// Three nested steps, and each one is load-bearing.
	//
	// `rows` takes the latest extraction of each tally row: re-extracting under
	// the same parser appends rather than replaces, and a run counted twice is
	// a paper that appears to have been read twice.
	//
	// `visits` folds one tally file — one visit to one outlet — back into a
	// single row. The file holds a line per publication date, each repeating
	// the visit's own `discovered` count, so the counts sum across those lines
	// and `discovered` must not.
	//
	// The outer select then sums the visits that fell on the same day.
	rows := "SELECT document_id, row_number, columns, retrieved_at, " +
		"row_number() OVER (PARTITION BY document_id, row_number ORDER BY processed_at DESC) " +
		"AS pick FROM " + records + " WHERE dataset = '" + newsTalliesDataset +
		"' AND columns['outlet_host'] = ? AND try_cast(parser_version AS INTEGER) = " +
		currentParserVersion(records, newsTalliesDataset)

	visits := "SELECT document_id, CAST(retrieved_at AS DATE) AS day, " +
		"max(try_cast(columns['discovered'] AS BIGINT)) AS discovered, " +
		"sum(try_cast(columns['scanned'] AS BIGINT)) AS scanned, " +
		"sum(try_cast(columns['matched'] AS BIGINT)) AS matched, " +
		"sum(try_cast(columns['recorded'] AS BIGINT)) AS recorded " +
		"FROM (" + rows + ") WHERE pick = 1 GROUP BY document_id, day"

	var total int64
	if err := s.warehouse.DB().QueryRowContext(ctx,
		"SELECT count(DISTINCT day) FROM ("+visits+")", host,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count news tallies", err)
		return
	}

	result, err := s.warehouse.DB().QueryContext(ctx,
		"SELECT CAST(day AS VARCHAR), coalesce(sum(discovered), 0), "+
			"coalesce(sum(scanned), 0), coalesce(sum(matched), 0), "+
			"coalesce(sum(recorded), 0), count(*) FROM ("+visits+") "+
			"GROUP BY day ORDER BY day DESC LIMIT ? OFFSET ?",
		host, limit, offset,
	)
	if err != nil {
		internalError(w, s.log, "query news tallies", err)
		return
	}
	defer result.Close()

	out := []NewsTally{}
	for result.Next() {
		var row NewsTally
		if err := result.Scan(
			&row.Date, &row.Discovered, &row.Scanned, &row.Matched, &row.Recorded, &row.Runs,
		); err != nil {
			internalError(w, s.log, "scan news tallies", err)
			return
		}
		out = append(out, row)
	}
	if err := result.Err(); err != nil {
		internalError(w, s.log, "read news tallies", err)
		return
	}

	writeData(w, out, &Meta{
		Total: total, Limit: limit, Offset: offset,
		HasMore: int64(offset+len(out)) < total,
		Layer:   "bronze", Source: "news-monitoring",
	})
}

// newsCorpus is the corpus as one query: every kept article, with the coding
// made of it and whether its page was photographed.
//
// Shared by the list, the facet counts beside it and anything else that has to
// mean the same thing by "the corpus". Spelled once because the qualifiers in
// it are load-bearing — the parser-version filter, the newest-coding join, the
// exclusion of articles a classifier rejected — and two copies are two chances
// to change one and not the other.
func newsCorpus(records string) string {
	// The corpus and the codings are separate collections keyed by URL, which
	// is what lets an article exist without a coding and a coding be redone
	// without rewriting the article.
	// Every field named rather than the map handed back whole: the map type
	// crosses the driver as an opaque value, and a column list is also the
	// only place that says what this endpoint promises to serve.
	// One row per article and one per coding, newest first. Re-extracting under
	// a new parser version appends rows rather than replacing them — that is
	// what makes a Bronze partition traceable to the code that produced it — so
	// without this an article read twice appears twice, which reads as two
	// articles rather than one re-read.
	return "SELECT a.document_id AS document_id, " +
		"a.columns['url'] AS url, a.columns['title'] AS title, " +
		"a.columns['lead'] AS lead, a.columns['outlet'] AS outlet, " +
		"a.columns['outlet_host'] AS outlet_host, " +
		"a.columns['outlet_province'] AS outlet_province, " +
		"a.columns['issues'] AS issues, a.columns['matched_terms'] AS matched_terms, " +
		"c.columns['profile'] AS profile, c.columns['engine'] AS engine, " +
		"c.columns['accepted'] AS accepted, c.columns['gate_probability'] AS gate, " +
		"c.columns['province'] AS cv_province, c.columns['district_city'] AS cv_district, " +
		"c.columns['date'] AS cv_date, c.columns['violence_form1'] AS cv_form, " +
		"c.columns['weapon_type1'] AS cv_weapon, c.columns['issue_type1'] AS cv_issue, " +
		"c.columns['actor1a'] AS cv_actor1, c.columns['actor2a'] AS cv_actor2, " +
		"c.columns['intervene'] AS cv_intervene, " +
		"c.columns['num_death'] AS cv_deaths, c.columns['num_injured'] AS cv_injured, " +
		"c.columns['escalation'] AS cv_escalation, " +
		// Cast to text: the column is a timestamp and a reader wants the day a
		// paper published, not a midnight that is an artefact of the type.
		"(s.url IS NOT NULL) AS screenshot, a.pub AS pub FROM " +
		// The published date comes off the article row, so it is projected
		// inside the deduplicating subquery rather than joined back in.
		"(SELECT document_id, columns, CAST(published_at AS VARCHAR) AS pub, " +
		"row_number() OVER (PARTITION BY columns['url'] ORDER BY processed_at DESC) " +
		"AS pick FROM " + records + " WHERE dataset = '" + newsArticlesDataset +
		// Issue-only. The crawl keeps nothing else, and rows from before that
		// was true are excluded rather than shown as articles about nothing.
		"' AND columns['issues'] NOT IN ('', '[]') AND try_cast(parser_version AS INTEGER) = " +
		currentParserVersion(records, newsArticlesDataset) + ") a " +
		// An accepted coding beats a rejected one written at the same moment.
		"LEFT JOIN " + latestCoding(records) +
		" c ON c.columns['url'] = a.columns['url'] " +
		"LEFT JOIN (SELECT DISTINCT columns['url'] AS url FROM " + records +
		" WHERE dataset = '" + newsShotsDataset + "' AND try_cast(parser_version AS INTEGER) = " +
		currentParserVersion(records, newsShotsDataset) + ") s ON s.url = a.columns['url'] " +
		// An article the classifier read and rejected is not about the issue,
		// whatever its vocabulary suggested, and the crawl no longer keeps
		// one. Rows landed before that was true are excluded here for the same
		// reason: the corpus is what is about an issue, not what mentioned a
		// word. Articles awaiting coding stay — nothing has judged them yet.
		"WHERE a.pick = 1 AND coalesce(c.columns['accepted'], 'true') <> 'false'"
}

// newsCorpusFilters is everything a caller may narrow the corpus by.
//
// One builder for the list and its facets: a facet counted under a different
// filter than the list offers a reader an option that returns nothing. The
// columns are the outer aliases of `newsCorpus`, so the join and the version
// filter have already happened by the time these apply.
func newsCorpusFilters(r *http.Request) (string, []any, error) {
	clauses := []string{}
	args := []any{}

	host, err := stringParam(r, "outlet", identifierPattern)
	if err != nil {
		return "", nil, err
	}
	if host == "" {
		// Also reachable as /v1/news/outlets/{host}/articles, which is how the
		// portal's outlet page asks.
		host = r.PathValue("host")
	}
	if host != "" {
		clauses = append(clauses, "outlet_host = ?")
		args = append(args, host)
	}

	issue, err := stringParam(r, "issue", identifierPattern)
	if err != nil {
		return "", nil, err
	}
	if issue != "" {
		clauses = append(clauses, "issues ILIKE ?")
		args = append(args, "%"+issue+"%")
	}

	province, err := stringParam(r, "province", searchPattern)
	if err != nil {
		return "", nil, err
	}
	if province != "" {
		clauses = append(clauses, "upper(outlet_province) = upper(?)")
		args = append(args, province)
	}

	search, err := stringParam(r, "q", searchPattern)
	if err != nil {
		return "", nil, err
	}
	if search != "" {
		clauses = append(clauses, "(title ILIKE ? OR lead ILIKE ?)")
		like := "%" + search + "%"
		args = append(args, like, like)
	}

	from, err := stringParam(r, "from", identifierPattern)
	if err != nil {
		return "", nil, err
	}
	if from != "" {
		clauses = append(clauses, "pub >= CAST(? AS DATE)")
		args = append(args, from)
	}

	to, err := stringParam(r, "to", identifierPattern)
	if err != nil {
		return "", nil, err
	}
	if to != "" {
		clauses = append(clauses, "pub <= CAST(? AS DATE)")
		args = append(args, to)
	}

	coded, err := boolParam(r, "coded")
	if err != nil {
		return "", nil, err
	}
	if coded {
		clauses = append(clauses, "accepted = 'true'")
	}

	// The coded labels, each a multiple choice: a reader narrowing to the
	// brawls is usually narrowing to two or three forms rather than one, and
	// filtering client-side would narrow the page rather than the corpus.
	coding, codingArgs, err := newsCodingFilters(r)
	if err != nil {
		return "", nil, err
	}
	clauses = append(clauses, coding...)
	args = append(args, codingArgs...)

	if len(clauses) == 0 {
		return "", args, nil
	}
	return " WHERE " + strings.Join(clauses, " AND "), args, nil
}

// newsCodingFilters narrows the corpus by what the classifier made of it.
//
// One function because two callers must agree: the list and the facet counts
// beside it. A facet computed under a different filter than the list offers a
// reader an option that returns nothing.
//
// The columns are the outer aliases of the corpus query, not Bronze keys — the
// join and the version filter have already happened by the time these apply.
func newsCodingFilters(r *http.Request) ([]string, []any, error) {
	clauses := []string{}
	args := []any{}
	for _, f := range newsCodedColumns {
		values, err := codeListParam(r, f.param)
		if err != nil {
			return nil, nil, err
		}
		if clause, bound := inClause(f.column, values); clause != "" {
			clauses = append(clauses, clause)
			args = append(args, bound...)
		}
	}
	return clauses, args, nil
}

// newsCodedColumns is the coded vocabulary a reader may filter and count by.
//
// Not every column: the secondary form, weapon and issue are mostly absent
// because there was only one of each, and a filter whose options are nine
// parts empty is a filter nobody can use.
var newsCodedColumns = []struct {
	param  string
	column string
	into   func(*NewsFacets) *[]Facet
}{
	{"form", "cv_form", func(f *NewsFacets) *[]Facet { return &f.Forms }},
	{"issue_type", "cv_issue", func(f *NewsFacets) *[]Facet { return &f.Issues }},
	{"weapon", "cv_weapon", func(f *NewsFacets) *[]Facet { return &f.Weapons }},
	{"escalation", "cv_escalation", func(f *NewsFacets) *[]Facet { return &f.Escalations }},
	// The incident's province, which is not the paper's: an outlet reports on
	// its neighbours, so this is named apart from the `province` filter that
	// means where the paper is.
	{"incident_province", "cv_province", func(f *NewsFacets) *[]Facet { return &f.Provinces }},
}

// NewsFacets is what the corpus can be narrowed by, counted under the filters
// already on.
//
// The place is the *incident's*, not the paper's: an outlet reports on its
// neighbours, and a reader filtering by province means where it happened.
type NewsFacets struct {
	Forms       []Facet `json:"forms"`
	Issues      []Facet `json:"issues"`
	Weapons     []Facet `json:"weapons"`
	Escalations []Facet `json:"escalations"`
	Provinces   []Facet `json:"provinces"`
}

// handleNewsArticleFacets serves the filter options for the corpus.
func (s *Server) handleNewsArticleFacets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	facets := NewsFacets{
		Forms: []Facet{}, Issues: []Facet{}, Weapons: []Facet{},
		Escalations: []Facet{}, Provinces: []Facet{},
	}
	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		writeData(w, facets, &Meta{Layer: "bronze"})
		return
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		internalError(w, s.log, "resolve records", err)
		return
	}

	where, args, err := newsCorpusFilters(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	corpus := "(" + newsCorpus(records) + ")" + where

	for _, f := range newsCodedColumns {
		// Each facet is counted under every filter *including its own*, as the
		// documents facets are: a reader who has picked two forms still sees
		// both ticked, and the counts beside the rest say what picking one
		// more would leave.
		filter := " WHERE " + f.column + " IS NOT NULL AND " + f.column + " <> '' " +
			"AND upper(" + f.column + ") <> '" + enumMissing + "'"
		rows, err := s.warehouse.DB().QueryContext(ctx,
			"SELECT "+f.column+" AS value, count(*) AS n FROM (SELECT * FROM "+corpus+")"+
				filter+" GROUP BY value ORDER BY n DESC, value LIMIT 60", args...,
		)
		if err != nil {
			internalError(w, s.log, "query news facets", err)
			return
		}
		values := []Facet{}
		for rows.Next() {
			var facet Facet
			if err := rows.Scan(&facet.Value, &facet.Count); err != nil {
				rows.Close()
				internalError(w, s.log, "scan news facet", err)
				return
			}
			values = append(values, facet)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			internalError(w, s.log, "read news facets", err)
			return
		}
		*f.into(&facets) = values
	}

	writeData(w, facets, &Meta{Layer: "bronze", Source: "news-monitoring"})
}

// handleNewsArticles serves the corpus.
func (s *Server) handleNewsArticles(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	where, args, err := newsCorpusFilters(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		writeData(w, []NewsArticle{}, &Meta{Limit: limit, Layer: "bronze"})
		return
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		internalError(w, s.log, "resolve records", err)
		return
	}

	base := newsCorpus(records)
	outer := "SELECT * FROM (" + base + ") " + where

	var total int64
	if err := s.warehouse.DB().QueryRowContext(ctx,
		"SELECT count(*) FROM ("+base+") "+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count news articles", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx,
		outer+" ORDER BY pub DESC NULLS LAST, title LIMIT ? OFFSET ?",
		append(append([]any{}, args...), limit, offset)...,
	)
	if err != nil {
		internalError(w, s.log, "query news articles", err)
		return
	}
	defer rows.Close()

	out := []NewsArticle{}
	for rows.Next() {
		var row newsArticleRow
		if err := rows.Scan(
			&row.DocumentID,
			&row.URL, &row.Title, &row.Lead, &row.Outlet, &row.Host, &row.Province,
			&row.Issues, &row.Terms,
			&row.Profile, &row.Engine, &row.Accepted, &row.Gate,
			&row.CvProvince, &row.CvDistrict, &row.CvDate, &row.CvForm,
			&row.CvWeapon, &row.CvIssue, &row.CvActor1, &row.CvActor2,
			&row.CvIntervene, &row.CvDeaths, &row.CvInjured, &row.CvEscalation,
			&row.Screenshot, &row.Published,
		); err != nil {
			internalError(w, s.log, "scan news articles", err)
			return
		}
		out = append(out, row.article())
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read news articles", err)
		return
	}

	writeData(w, out, &Meta{
		Total: total, Limit: limit, Offset: offset,
		HasMore: int64(offset+len(out)) < total,
		Layer:   "bronze", Source: "news-monitoring",
	})
}

// handleNewsEvents serves the incidents coded out of the corpus.
func (s *Server) handleNewsEvents(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	province, err := stringParam(r, "province", searchPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	from, err := stringParam(r, "from", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	to, err := stringParam(r, "to", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		writeData(w, []NewsEvent{}, &Meta{Limit: limit, Layer: "bronze"})
		return
	}
	events, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		internalError(w, s.log, "resolve news events", err)
		return
	}

	// Events sit in `records` beside every other tabular collection rather than
	// in a directory of their own, so the dataset name is the filter.
	clauses := []string{
		"dataset = '" + newsEventsDataset + "'",
		"try_cast(parser_version AS INTEGER) = " + currentParserVersion(events, newsEventsDataset),
	}
	args := []any{}
	if province != "" {
		clauses = append(clauses, "upper(columns['province']) = upper(?)")
		args = append(args, province)
	}
	if from != "" {
		clauses = append(clauses, "columns['date'] >= ?")
		args = append(args, from)
	}
	if to != "" {
		clauses = append(clauses, "columns['date'] <= ?")
		args = append(args, to)
	}
	where := " WHERE " + strings.Join(clauses, " AND ")

	var total int64
	if err := s.warehouse.DB().QueryRowContext(ctx,
		"SELECT count(*) FROM "+events+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count news events", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx,
		"SELECT columns['event_id'], columns['profile'], columns['date'], "+
			"columns['province'], columns['district_city'], columns['violence_form1'], "+
			"columns['weapon_type1'], columns['issue_type1'], columns['actor1a'], "+
			"columns['actor2a'], columns['intervene'], columns['num_death'], "+
			"columns['num_injured'], columns['escalation'], "+
			"columns['report_count'], columns['outlets'], "+
			"columns['sources'] FROM "+events+where+
			" ORDER BY columns['date'] DESC, columns['event_id'] LIMIT ? OFFSET ?",
		append(append([]any{}, args...), limit, offset)...,
	)
	if err != nil {
		internalError(w, s.log, "query news events", err)
		return
	}
	defer rows.Close()

	out := []NewsEvent{}
	for rows.Next() {
		var row newsEventRow
		if err := rows.Scan(
			&row.EventID, &row.Profile, &row.Date, &row.Province, &row.District,
			&row.Form, &row.Weapon, &row.Issue, &row.Actor1, &row.Actor2,
			&row.Intervene, &row.Deaths, &row.Injured, &row.Escalation,
			&row.ReportCount, &row.Outlets, &row.Sources,
		); err != nil {
			internalError(w, s.log, "scan news events", err)
			return
		}
		out = append(out, row.event())
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read news events", err)
		return
	}

	writeData(w, out, &Meta{
		Total: total, Limit: limit, Offset: offset,
		HasMore: int64(offset+len(out)) < total,
		Layer:   "bronze", Source: "news-monitoring",
	})
}

// Bronze holds every value as text, because deciding what a value means is
// Silver's job and not the extractor's. These two types are where that text
// becomes the response — a place a reader never sees, and the only place the
// conventions of the coding form are interpreted.
//
// Two of those conventions matter here. `TIDAK JELAS` means the reporting did
// not say, and is dropped rather than served: a field absent from the response
// says the same thing without asking every consumer to know the phrase. And
// `-99` is the missing marker for a number, which is emphatically not zero —
// an incident where nobody was hurt and an incident nobody counted are
// different facts, and serving -99 as a casualty count would put it on a chart.

// enumMissing is what a coder writes when the reporting did not say.
const enumMissing = "TIDAK JELAS"

// numMissing is the same, for a figure.
const numMissing = -99

type newsArticleRow struct {
	DocumentID             sql.NullString
	URL, Title, Lead       sql.NullString
	Outlet, Host, Province sql.NullString
	Issues, Terms          sql.NullString
	Profile, Engine        sql.NullString
	Accepted, Gate         sql.NullString
	CvProvince, CvDistrict sql.NullString
	CvDate, CvForm         sql.NullString
	CvWeapon, CvIssue      sql.NullString
	CvActor1, CvActor2     sql.NullString
	CvIntervene            sql.NullString
	CvDeaths, CvInjured    sql.NullString
	CvEscalation           sql.NullString
	Screenshot             sql.NullBool
	Published              sql.NullString
}

func (r newsArticleRow) article() NewsArticle {
	article := NewsArticle{
		DocumentID: r.DocumentID.String,
		Screenshot: r.Screenshot.Bool,
		URL:        r.URL.String,
		Title:      r.Title.String,
		Lead:       r.Lead.String,
		Outlet:     r.Outlet.String,
		Host:       r.Host.String,
		Province:   r.Province.String,
		Issues:     jsonStrings(r.Issues),
		Terms:      jsonStrings(r.Terms),
	}
	if r.Published.Valid && r.Published.String != "" {
		published := r.Published.String
		article.Published = &published
	}
	if !r.Profile.Valid {
		return article
	}
	coding := &NewsCoding{Profile: r.Profile.String, Engine: r.Engine.String}
	if r.Accepted.Valid && r.Accepted.String != "" {
		accepted := r.Accepted.String == "true"
		coding.Accepted = &accepted
	}
	coding.Gate = optFloat(r.Gate)
	coding.Province = optEnum(r.CvProvince)
	coding.District = optEnum(r.CvDistrict)
	coding.Date = optEnum(r.CvDate)
	coding.ViolenceForm = optEnum(r.CvForm)
	coding.WeaponType = optEnum(r.CvWeapon)
	coding.IssueType = optEnum(r.CvIssue)
	coding.Actor1 = optEnum(r.CvActor1)
	coding.Actor2 = optEnum(r.CvActor2)
	coding.Intervene = optEnum(r.CvIntervene)
	coding.Deaths = optCount(r.CvDeaths)
	coding.Injured = optCount(r.CvInjured)
	coding.Escalation = optEnum(r.CvEscalation)
	article.Coding = coding
	return article
}

type newsEventRow struct {
	EventID, Profile sql.NullString
	Date, Province   sql.NullString
	District, Form   sql.NullString
	Weapon, Issue    sql.NullString
	Actor1, Actor2   sql.NullString
	Intervene        sql.NullString
	Deaths, Injured  sql.NullString
	Escalation       sql.NullString
	ReportCount      sql.NullString
	Outlets, Sources sql.NullString
}

func (r newsEventRow) event() NewsEvent {
	event := NewsEvent{
		EventID:      r.EventID.String,
		Profile:      r.Profile.String,
		Date:         optEnum(r.Date),
		Province:     optEnum(r.Province),
		District:     optEnum(r.District),
		ViolenceForm: optEnum(r.Form),
		WeaponType:   optEnum(r.Weapon),
		IssueType:    optEnum(r.Issue),
		Actor1:       optEnum(r.Actor1),
		Actor2:       optEnum(r.Actor2),
		Intervene:    optEnum(r.Intervene),
		Deaths:       optCount(r.Deaths),
		Injured:      optCount(r.Injured),
		Escalation:   optEnum(r.Escalation),
		Outlets:      jsonStrings(r.Outlets),
		Sources:      jsonStrings(r.Sources),
	}
	if count := optCount(r.ReportCount); count != nil {
		event.ReportCount = *count
	}
	return event
}

// optEnum drops a coded value that says the reporting did not say.
func optEnum(value sql.NullString) *string {
	text := strings.TrimSpace(value.String)
	if !value.Valid || text == "" || strings.EqualFold(text, enumMissing) {
		return nil
	}
	return &text
}

// optCount reads a figure, dropping the missing marker.
//
// Dropped rather than served as -99: a consumer that charted it would draw a
// province ninety-nine deaths below zero, and one that summed it would report
// fewer deaths than actually happened.
func optCount(value sql.NullString) *int64 {
	if !value.Valid || strings.TrimSpace(value.String) == "" {
		return nil
	}
	var number int64
	if _, err := fmt.Sscanf(strings.TrimSpace(value.String), "%d", &number); err != nil {
		return nil
	}
	if number == numMissing {
		return nil
	}
	return &number
}

func optFloat(value sql.NullString) *float64 {
	if !value.Valid || strings.TrimSpace(value.String) == "" {
		return nil
	}
	var number float64
	if _, err := fmt.Sscanf(strings.TrimSpace(value.String), "%g", &number); err != nil {
		return nil
	}
	return &number
}

// jsonStrings reads a list the extractor wrote as JSON.
//
// Always a list, never null: a consumer rendering tags should not have to
// branch on whether an article matched nothing.
func jsonStrings(value sql.NullString) []string {
	out := []string{}
	if !value.Valid || value.String == "" {
		return out
	}
	if err := json.Unmarshal([]byte(value.String), &out); err != nil {
		return []string{}
	}
	return out
}

// NewsArticleDetail is one article's own page.
//
// Everything on it answers a version of "can I trust this coding": what the
// paper said, what the classifier decided and how sure it was, where the bytes
// are, and whether there is a picture of the page as it stood that day.
type NewsArticleDetail struct {
	NewsArticle

	// How it was found — a lexicon term, the sitemap, the section page. Worth
	// showing: an article found down a route that is about to break is a gap
	// in tomorrow's coverage.
	DiscoveredBy *string `json:"discovered_by,omitempty"`
	// How much text the coding was made from. A paywalled piece leaves a lead
	// and little else, and a coding read off two sentences deserves to be read
	// differently from one read off a whole report.
	BodyChars *int64 `json:"body_chars,omitempty"`

	// Provenance, the same facts every other document in the warehouse carries.
	ContentHash *string `json:"content_hash,omitempty"`
	RawPath     *string `json:"raw_path,omitempty"`
	MediaType   *string `json:"media_type,omitempty"`
	RetrievedAt *string `json:"retrieved_at,omitempty"`
	SourceID    *string `json:"source_id,omitempty"`

	// The classifier's probability per field, so a reader can see which answer
	// to doubt first. A field the model was unsure of is exactly what a human
	// verifier should look at.
	Confidence map[string]float64 `json:"confidence,omitempty"`
	// Which dictionary categories the article carried — the act, who was
	// involved, what it left behind, what it was about. The terms alone do not
	// say which rule admitted the article, and the dictionary is the part of
	// the pipeline a reader is most likely to want to argue with.
	MatchedCategories []string `json:"matched_categories,omitempty"`
	// Why the coding was put to a second, larger model, and which fields that
	// model supplied. Absent on the great majority: the cheap classifier was
	// sure and nothing was escalated. A reason with no fields beside it is a
	// coding that wanted a second reading and did not get one — the second
	// reader was unconfigured or unreachable.
	EscalationReason *string  `json:"escalation_reason,omitempty"`
	Deepened         []string `json:"deepened,omitempty"`
	// The event this article was clustered into, where clustering has run.
	EventID *string `json:"event_id,omitempty"`
	// The other papers that reported the same incident.
	AlsoReportedBy []string `json:"also_reported_by,omitempty"`
}

// handleNewsArticle serves one article.
func (s *Server) handleNewsArticle(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id, err := pathIdentifier(r, "id")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		notFound(w, "no articles have been collected yet", id)
		return
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		internalError(w, s.log, "resolve records", err)
		return
	}

	var row newsArticleRow
	var discovered, bodyChars, hash, rawPath, mediaType, retrieved, sourceID sql.NullString
	var confidence, categories, escalationReason, deepened sql.NullString
	err = s.warehouse.DB().QueryRowContext(ctx,
		"SELECT a.document_id, a.columns['url'], a.columns['title'], a.columns['lead'], "+
			"a.columns['outlet'], a.columns['outlet_host'], a.columns['outlet_province'], "+
			"a.columns['issues'], a.columns['matched_terms'], "+
			"c.columns['profile'], c.columns['engine'], c.columns['accepted'], "+
			"c.columns['gate_probability'], c.columns['province'], c.columns['district_city'], "+
			"c.columns['date'], c.columns['violence_form1'], c.columns['weapon_type1'], "+
			"c.columns['issue_type1'], c.columns['actor1a'], c.columns['actor2a'], "+
			"c.columns['intervene'], c.columns['num_death'], c.columns['num_injured'], "+
			"c.columns['escalation'], "+
			"(s.url IS NOT NULL), CAST(a.published_at AS VARCHAR), "+
			"a.columns['discovered_by'], a.columns['body_chars'], a.content_hash, a.raw_path, "+
			"a.media_type, CAST(a.retrieved_at AS VARCHAR), a.source_id, c.columns['confidence'], "+
			// The dictionary's categories and the second reader's trail. Both
			// are on the article's own page only: a list of a hundred articles
			// is not the place to answer why one of them was read twice.
			"a.columns['matched_categories'], c.columns['escalation_reason'], "+
			"c.columns['deepened'] "+
			"FROM (SELECT *, row_number() OVER (PARTITION BY document_id ORDER BY "+
			"processed_at DESC) AS pick FROM "+records+" WHERE dataset = '"+
			newsArticlesDataset+"' AND document_id = ? AND try_cast(parser_version AS INTEGER) = "+
			currentParserVersion(records, newsArticlesDataset)+" "+
			// The same filter the list applies. An article that matched no
			// issue, or that the classifier read and rejected, is not part of
			// the corpus — and a page for one reachable only by typing its id
			// would be a second, quieter answer to what the corpus contains.
			"AND columns['issues'] NOT IN ('', '[]')) a "+
			"LEFT JOIN "+latestCoding(records)+" c ON c.columns['url'] = a.columns['url'] "+
			"LEFT JOIN (SELECT DISTINCT columns['url'] AS url FROM "+records+
			" WHERE dataset = '"+newsShotsDataset+"' AND try_cast(parser_version AS INTEGER) = "+
			currentParserVersion(records, newsShotsDataset)+") s ON s.url = a.columns['url'] "+
			"WHERE a.pick = 1 AND coalesce(c.columns['accepted'], 'true') <> 'false'", id,
	).Scan(
		&row.DocumentID, &row.URL, &row.Title, &row.Lead,
		&row.Outlet, &row.Host, &row.Province, &row.Issues, &row.Terms,
		&row.Profile, &row.Engine, &row.Accepted, &row.Gate,
		&row.CvProvince, &row.CvDistrict, &row.CvDate, &row.CvForm,
		&row.CvWeapon, &row.CvIssue, &row.CvActor1, &row.CvActor2,
		&row.CvIntervene, &row.CvDeaths, &row.CvInjured, &row.CvEscalation,
		&row.Screenshot, &row.Published,
		&discovered, &bodyChars, &hash, &rawPath, &mediaType, &retrieved, &sourceID,
		&confidence, &categories, &escalationReason, &deepened,
	)
	if err == sql.ErrNoRows {
		notFound(w, "no such article", id)
		return
	}
	if err != nil {
		internalError(w, s.log, "query news article", err)
		return
	}

	detail := NewsArticleDetail{NewsArticle: row.article()}
	detail.DiscoveredBy = optEnum(discovered)
	detail.BodyChars = optCount(bodyChars)
	detail.ContentHash = optEnum(hash)
	detail.RawPath = optEnum(rawPath)
	detail.MediaType = optEnum(mediaType)
	detail.RetrievedAt = optEnum(retrieved)
	detail.SourceID = optEnum(sourceID)
	if confidence.Valid && confidence.String != "" {
		parsed := map[string]float64{}
		if json.Unmarshal([]byte(confidence.String), &parsed) == nil && len(parsed) > 0 {
			detail.Confidence = parsed
		}
	}
	detail.MatchedCategories = jsonStrings(categories)
	detail.EscalationReason = optEnum(escalationReason)
	detail.Deepened = jsonStrings(deepened)
	s.attachEvent(ctx, &detail)

	writeData(w, detail, &Meta{Layer: "bronze", Source: "news-monitoring"})
}

// latestCoding is the one coding per article, newest first.
//
// Spelled once: the list and the detail both need it, and two copies of a
// window function are two chances to change one and not the other.
func latestCoding(records string) string {
	return "(SELECT columns FROM (SELECT columns, row_number() OVER (" +
		"PARTITION BY columns['url'] ORDER BY processed_at DESC, " +
		"columns['accepted'] DESC) AS pick FROM " + records +
		" WHERE dataset = '" + newsCodingsDataset + "' AND try_cast(parser_version AS INTEGER) = " +
		currentParserVersion(records, newsCodingsDataset) + ") WHERE pick = 1)"
}

// attachEvent finds the incident this article was clustered into.
//
// Best-effort: clustering may not have run, and an article whose coding was
// rejected belongs to no event. Neither is an error — the page simply does not
// show the section.
func (s *Server) attachEvent(ctx context.Context, detail *NewsArticleDetail) {
	if detail.URL == "" || !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		return
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		return
	}
	var eventID, outlets sql.NullString
	err = s.warehouse.DB().QueryRowContext(ctx,
		"SELECT columns['event_id'], columns['outlets'] FROM "+records+
			" WHERE dataset = '"+newsEventsDataset+
			"' AND contains(columns['sources'], ?) LIMIT 1", detail.URL,
	).Scan(&eventID, &outlets)
	if err != nil {
		return
	}
	detail.EventID = optEnum(eventID)
	for _, host := range jsonStrings(outlets) {
		if host != detail.Host {
			detail.AlsoReportedBy = append(detail.AlsoReportedBy, host)
		}
	}
}

// handleNewsScreenshot serves the picture taken of an article when it was
// collected.
//
// Inline rather than as a download: it is evidence to look at, and a PNG
// cannot run anything. The bytes come from RAW through the same guarded path
// the preserved documents use.
func (s *Server) handleNewsScreenshot(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id, err := pathIdentifier(r, "id")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	if s.cfg.Storage.IsObjectStorage() {
		writeError(w, http.StatusNotImplemented, CodeUnavailable,
			"screenshots are not served from this deployment",
			"the lake is object storage")
		return
	}
	if !s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		notFound(w, "no articles have been collected yet", id)
		return
	}
	records, err := s.warehouse.Source(storage.LayerBronze, "records")
	if err != nil {
		internalError(w, s.log, "resolve records", err)
		return
	}

	// Found through the article's URL rather than by id: the screenshot is its
	// own landed artifact with its own document id, and what ties the two
	// together is the page they are both of.
	var rawPath sql.NullString
	err = s.warehouse.DB().QueryRowContext(ctx,
		"SELECT s.raw_path FROM "+records+" a JOIN "+records+" s "+
			"ON s.columns['url'] = a.columns['url'] "+
			"WHERE a.dataset = '"+newsArticlesDataset+"' AND a.document_id = ? "+
			"AND s.dataset = '"+newsShotsDataset+"' ORDER BY s.processed_at DESC LIMIT 1", id,
	).Scan(&rawPath)
	if err == sql.ErrNoRows || !rawPath.Valid || rawPath.String == "" {
		notFound(w, "no screenshot was taken of this article", id)
		return
	}
	if err != nil {
		internalError(w, s.log, "query news screenshot", err)
		return
	}

	path, err := s.rawFile(rawPath.String)
	if err != nil {
		internalError(w, s.log, "resolve raw file", err)
		return
	}
	file, err := os.Open(path)
	if err != nil {
		notFound(w, "the screenshot is not on this machine",
			"the catalogue was built against a lake this deployment cannot read")
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		internalError(w, s.log, "stat screenshot", err)
		return
	}
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Disposition", fmt.Sprintf("inline; filename=%q", id+".png"))
	http.ServeContent(w, r, id+".png", info.ModTime(), file)
}
