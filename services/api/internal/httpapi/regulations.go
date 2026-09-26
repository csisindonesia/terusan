package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Regulation is one regulation as BPK catalogues it, regional or central.
//
// The grain is the catalogue record, not the parsed text: BPK publishes
// entries whose PDF never converted, and serving only the parsed ones would
// make the corpus look complete when it is not. Those rows keep their title,
// region and link, and say through ParseStatus why there is nothing to read.
//
// Number is a string because a regulation's number is a label, not a
// quantity — "1", "01", "12A" and "5/PERDA/2019" all occur.
type Regulation struct {
	Key      string  `json:"key"`
	BPKID    *string `json:"bpk_id,omitempty"`
	SourceID string  `json:"source_id"`

	// perda or pkd for regional instruments, pusat or kementerian for central
	// ones. The corpus never spells pkd "perkada", and a filter that does
	// silently drops two thirds of the rows.
	Track string `json:"track"`
	// Perda, Perbup, UU, PP, Permen… — read off the document itself, so it
	// is absent wherever the text was never parsed.
	Instrument *string `json:"instrument,omitempty"`
	Scope      *string `json:"scope,omitempty"`

	Title  string  `json:"title"`
	Number *string `json:"number,omitempty"`
	Year   *int    `json:"year,omitempty"`

	RegionName *string `json:"region_name,omitempty"`
	RegionType *string `json:"region_type,omitempty"`
	RegionCode *string `json:"region_code,omitempty"`

	Category    *string `json:"category,omitempty"`
	Subject     *string `json:"subject,omitempty"`
	Status      *string `json:"status,omitempty"`
	LegalStatus *string `json:"legal_status,omitempty"`

	EnactedDate   *string `json:"enacted_date,omitempty"`
	PublishedDate *string `json:"published_date,omitempty"`

	DetailURL *string `json:"detail_url,omitempty"`
	PDFURL    *string `json:"pdf_url,omitempty"`

	// Why there is or is not text: ok, no_sections, unbounded, empty, or
	// absent where BPK catalogued a record whose PDF never converted.
	ParseStatus *string `json:"parse_status,omitempty"`
	// How much of the PDF the conversion kept. A regulation read from a
	// partial conversion is a different thing from one read whole, and the
	// reader is entitled to know which they have.
	MDCoverage *float64 `json:"md_coverage,omitempty"`

	Words     *int `json:"words,omitempty"`
	Bab       *int `json:"bab,omitempty"`
	Pasal     *int `json:"pasal,omitempty"`
	Ayat      *int `json:"ayat,omitempty"`
	Citations *int `json:"citations,omitempty"`
}

// RegulationDetail adds the parts of a regulation that are too long to list.
type RegulationDetail struct {
	Regulation
	// The opening formula, the "considering" and "recalling" recitals, and the
	// closing. Separate from the articles because they are the document's
	// frame rather than its content, and a reader scanning the articles should
	// not have to scroll past them.
	Preamble  *string `json:"preamble,omitempty"`
	Menimbang *string `json:"menimbang,omitempty"`
	Mengingat *string `json:"mengingat,omitempty"`
	Penutup   *string `json:"penutup,omitempty"`

	SourceName   *string `json:"source_name,omitempty"`
	Organization *string `json:"organization,omitempty"`
	License      *string `json:"license,omitempty"`
}

// Section is one structural unit of a regulation: a bab, bagian, pasal or ayat.
type Section struct {
	Seq      int     `json:"seq"`
	Kind     string  `json:"kind"`
	Bab      *string `json:"bab,omitempty"`
	BabNum   *int    `json:"bab_num,omitempty"`
	BabTitle *string `json:"bab_title,omitempty"`
	Bagian   *string `json:"bagian,omitempty"`
	Paragraf *string `json:"paragraf,omitempty"`
	Pasal    *string `json:"pasal,omitempty"`
	PasalNum *int    `json:"pasal_num,omitempty"`
	Ayat     *int    `json:"ayat,omitempty"`
	Text     string  `json:"text"`
	Words    *int    `json:"words,omitempty"`
}

// Citation is one instrument a regulation cites.
//
// Resolution is deliberately not attempted here. Four fifths of citations name
// no jurisdiction, so "Perda No 1 Tahun 2015" matches every Perda No 1 of 2015
// in the country; the corpus resolves those into 25x as many candidate edges as
// there are citations. Serving a candidate as a fact would be a lie, so this
// carries what the document actually said and the one key it resolved to, if
// any.
type Citation struct {
	Index      int     `json:"index"`
	Instrument *string `json:"instrument,omitempty"`
	Scope      *string `json:"scope,omitempty"`
	Number     *int    `json:"number,omitempty"`
	Year       *int    `json:"year,omitempty"`
	CitedTitle *string `json:"cited_title,omitempty"`
	FoundIn    *string `json:"found_in,omitempty"`
	// The corpus document this resolved to, where it resolved to exactly one.
	CiteKey *string `json:"cite_key,omitempty"`
}

// Facet is one value a filter can take, with how many documents carry it.
//
// Counted rather than listed: "Kabupaten Gowa (212)" tells a reader whether the
// filter is worth applying, and a bare list of 514 regions does not.
type Facet struct {
	Value string `json:"value"`
	Count int64  `json:"count"`
}

// Facets is every filter's options for the regulations list.
type Facets struct {
	Tracks      []Facet `json:"tracks"`
	Instruments []Facet `json:"instruments"`
	RegionTypes []Facet `json:"region_types"`
	Regions     []Facet `json:"regions"`
	Categories  []Facet `json:"categories"`
	Years       []Facet `json:"years"`
}

// NULLS LAST throughout. Three thousand records name no region, and a plain
// ASC sorts those to the top of every page — the first thing a reader sees
// would be the handful of rows with the least to say.
var regulationOrder = map[string]string{
	"year":    "year ASC NULLS LAST, region_name ASC NULLS LAST, number ASC",
	"-year":   "year DESC NULLS LAST, region_name ASC NULLS LAST, number ASC",
	"title":   "title ASC",
	"-title":  "title DESC",
	"region":  "region_name ASC NULLS LAST, year DESC",
	"-region": "region_name DESC NULLS LAST, year DESC",
}

// The corpus covers 1952 to the current drafting year. The bounds are wide
// rather than exact: a filter that rejects 2027 because the import is a month
// old is a bug report waiting to happen.
const (
	minRegulationYear = 1900
	maxRegulationYear = 2100
)

// regulationFilters builds the WHERE clause shared by the list, the count and
// the facets, so a facet count can never describe a different set of rows from
// the list beside it.
func regulationFilters(r *http.Request) (string, []any, error) {
	var clauses []string
	args := []any{}

	for _, f := range []struct {
		param  string
		column string
	}{
		{"track", "track"},
		{"instrument", "instrument"},
		{"region_type", "region_type"},
		{"region_code", "region_code"},
		{"category", "category"},
	} {
		values, err := stringListParam(r, f.param, identifierPattern)
		if err != nil {
			return "", nil, err
		}
		if clause, bound := inClause(f.column, values); clause != "" {
			clauses = append(clauses, clause)
			args = append(args, bound...)
		}
	}

	// Region by name as well as by code: the codes are BPS's and a reader
	// picking "Kabupaten Gowa" out of a list has the name, not the code.
	region, err := stringParam(r, "region", searchPattern)
	if err != nil {
		return "", nil, err
	}
	if region != "" {
		clauses = append(clauses, "region_name = ?")
		args = append(args, region)
	}

	year, err := intParam(r, "year", 0, minRegulationYear, maxRegulationYear)
	if err != nil {
		return "", nil, err
	}
	if year != 0 {
		clauses = append(clauses, "year = ?")
		args = append(args, year)
	}
	from, err := intParam(r, "year_from", 0, minRegulationYear, maxRegulationYear)
	if err != nil {
		return "", nil, err
	}
	if from != 0 {
		clauses = append(clauses, "year >= ?")
		args = append(args, from)
	}
	to, err := intParam(r, "year_to", 0, minRegulationYear, maxRegulationYear)
	if err != nil {
		return "", nil, err
	}
	if to != 0 {
		clauses = append(clauses, "year <= ?")
		args = append(args, to)
	}

	// Text or no text, because the two are different corpora for most
	// purposes: 245,804 regulations can be read, and the rest can only be
	// cited and linked to.
	switch r.URL.Query().Get("has_text") {
	case "":
	case "true":
		clauses = append(clauses, "parse_status = 'ok'")
	case "false":
		clauses = append(clauses, "(parse_status IS NULL OR parse_status <> 'ok')")
	default:
		return "", nil, &paramError{param: "has_text", reason: "must be true or false"}
	}

	search, err := stringParam(r, "q", searchPattern)
	if err != nil {
		return "", nil, err
	}
	if search != "" {
		// Title and subject: the title says what the regulation does, the
		// subject is BPK's own classification of it, and a reader searching
		// "retribusi" means either.
		clauses = append(clauses,
			"(title ILIKE ? OR coalesce(subject, '') ILIKE ?)")
		pattern := "%" + search + "%"
		args = append(args, pattern, pattern)
	}

	if len(clauses) == 0 {
		return "", args, nil
	}
	return " WHERE " + strings.Join(clauses, " AND "), args, nil
}

// regulationColumns is the list projection, shared so the row scan below
// cannot drift from it.
const regulationColumns = `
	key, bpk_id, source_id, track, instrument, scope,
	title, number, year, region_name, region_type, region_code,
	category, subject, status, legal_status,
	CAST(enacted_date AS VARCHAR), CAST(published_date AS VARCHAR),
	detail_url, pdf_url, parse_status, md_coverage,
	n_word, n_bab, n_pasal, n_ayat, n_citations`

func scanRegulation(rows *sql.Rows, out *Regulation) error {
	return rows.Scan(
		&out.Key, &out.BPKID, &out.SourceID, &out.Track, &out.Instrument, &out.Scope,
		&out.Title, &out.Number, &out.Year, &out.RegionName, &out.RegionType,
		&out.RegionCode, &out.Category, &out.Subject, &out.Status, &out.LegalStatus,
		&out.EnactedDate, &out.PublishedDate, &out.DetailURL, &out.PDFURL,
		&out.ParseStatus, &out.MDCoverage, &out.Words, &out.Bab, &out.Pasal,
		&out.Ayat, &out.Citations,
	)
}

// regulations resolves the silver dataset, or reports that it has not been
// imported. Returned as an error the caller writes, because every handler here
// needs the same answer.
//
// `silver/regulations`, not `silver/documents`: the corpus held the generic
// name until the document catalogue needed it, and a table of perda under
// `documents` made the lake claim a catalogue of source material it did not
// have (program.md §7 lists the two separately).
func (s *Server) regulations(ctx context.Context, w http.ResponseWriter) (string, bool) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "regulations") {
		notFound(w, "no regulations have been imported",
			"run `scripts/import-regulations.sh`")
		return "", false
	}
	expression, err := s.source(storage.LayerSilver, "regulations")
	if err != nil {
		internalError(w, s.log, "resolve regulations", err)
		return "", false
	}
	return expression, true
}

// handleRegulations lists regulations.
func (s *Server) handleRegulations(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	where, args, err := regulationFilters(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	order, err := sortOrder(r, regulationOrder, "-year")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	regulations, ok := s.regulations(ctx, w)
	if !ok {
		return
	}

	var total int64
	if err := s.warehouse.DB().QueryRowContext(
		ctx, "SELECT count(*) FROM "+regulations+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count regulations", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
		"SELECT %s FROM %s%s ORDER BY %s LIMIT ? OFFSET ?",
		regulationColumns, regulations, where, order,
	), append(args, limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query regulations", err)
		return
	}
	defer rows.Close()

	results := make([]Regulation, 0, limit)
	for rows.Next() {
		var regulation Regulation
		if err := scanRegulation(rows, &regulation); err != nil {
			internalError(w, s.log, "scan regulation", err)
			return
		}
		results = append(results, regulation)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read regulations", err)
		return
	}

	writeData(w, results, &Meta{
		Total:   total,
		Limit:   limit,
		Offset:  offset,
		HasMore: int64(offset+len(results)) < total,
		Layer:   "silver",
	})
}

// handleRegulation serves one regulation, recitals and all.
func (s *Server) handleRegulation(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	key, err := pathIdentifier(r, "key")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	regulations, ok := s.regulations(ctx, w)
	if !ok {
		return
	}

	// The registry may not be published, and a regulation whose publisher is
	// unknown is still a regulation — so the join is left, as it is for
	// datasets.
	sources := "(SELECT NULL AS source_id, NULL AS name, NULL AS organization, " +
		"NULL AS license) s ON false"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "sources") {
		registry, err := s.source(storage.LayerSilver, "sources")
		if err != nil {
			internalError(w, s.log, "resolve sources", err)
			return
		}
		sources = registry + " s ON s.source_id = d.source_id"
	}

	query := fmt.Sprintf(`
		SELECT d.key, d.bpk_id, d.source_id, d.track, d.instrument, d.scope,
		       d.title, d.number, d.year, d.region_name, d.region_type,
		       d.region_code, d.category, d.subject, d.status, d.legal_status,
		       CAST(d.enacted_date AS VARCHAR), CAST(d.published_date AS VARCHAR),
		       d.detail_url, d.pdf_url, d.parse_status, d.md_coverage,
		       d.n_word, d.n_bab, d.n_pasal, d.n_ayat, d.n_citations,
		       d.preamble, d.menimbang, d.mengingat, d.penutup,
		       s.name, s.organization, s.license
		FROM %s d LEFT JOIN %s WHERE d.key = ?`, regulations, sources)

	var out RegulationDetail
	err = s.warehouse.DB().QueryRowContext(ctx, query, key).Scan(
		&out.Key, &out.BPKID, &out.SourceID, &out.Track, &out.Instrument, &out.Scope,
		&out.Title, &out.Number, &out.Year, &out.RegionName, &out.RegionType,
		&out.RegionCode, &out.Category, &out.Subject, &out.Status, &out.LegalStatus,
		&out.EnactedDate, &out.PublishedDate, &out.DetailURL, &out.PDFURL,
		&out.ParseStatus, &out.MDCoverage, &out.Words, &out.Bab, &out.Pasal,
		&out.Ayat, &out.Citations,
		&out.Preamble, &out.Menimbang, &out.Mengingat, &out.Penutup,
		&out.SourceName, &out.Organization, &out.License,
	)
	if err == sql.ErrNoRows {
		notFound(w, "no regulation with that key", key)
		return
	}
	if err != nil {
		internalError(w, s.log, "query regulation", err)
		return
	}

	writeData(w, out, &Meta{Total: 1, Layer: "silver"})
}

// handleRegulationSections serves a regulation's articles in document order.
func (s *Server) handleRegulationSections(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	key, err := pathIdentifier(r, "key")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	// Generous by default: a regulation is read whole, and paging its articles
	// into four requests serves nobody. The cap still applies.
	limit, err := intParam(r, "limit", 2_000, 1, MaxLimit)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	offset, err := intParam(r, "offset", 0, 0, 1_000_000)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "regulation_sections") {
		notFound(w, "no regulations have been imported",
			"run `scripts/import-regulations.sh`")
		return
	}
	sections, err := s.source(storage.LayerSilver, "regulation_sections")
	if err != nil {
		internalError(w, s.log, "resolve sections", err)
		return
	}
	regulations, ok := s.regulations(ctx, w)
	if !ok {
		return
	}

	// The track and year are read first and then bound, because the sections
	// are partitioned on exactly those two: with them the query touches one
	// partition, and without them it scans a gigabyte to find a few dozen rows.
	var track sql.NullString
	var year sql.NullInt64
	err = s.warehouse.DB().QueryRowContext(
		ctx, "SELECT track, year FROM "+regulations+" WHERE key = ?", key,
	).Scan(&track, &year)
	if err == sql.ErrNoRows {
		notFound(w, "no regulation with that key", key)
		return
	}
	if err != nil {
		internalError(w, s.log, "resolve regulation partition", err)
		return
	}

	where := "WHERE key = ?"
	args := []any{key}
	if track.Valid {
		where += " AND track = ?"
		args = append(args, track.String)
	}
	if year.Valid {
		where += " AND year = ?"
		args = append(args, year.Int64)
	}

	var total int64
	if err := s.warehouse.DB().QueryRowContext(
		ctx, "SELECT count(*) FROM "+sections+" "+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count sections", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT seq, kind, bab, bab_num, bab_title, bagian, paragraf,
		       pasal, pasal_num, ayat, text, n_word
		FROM %s %s ORDER BY seq LIMIT ? OFFSET ?`, sections, where),
		append(args, limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query sections", err)
		return
	}
	defer rows.Close()

	results := make([]Section, 0, 256)
	for rows.Next() {
		var section Section
		if err := rows.Scan(
			&section.Seq, &section.Kind, &section.Bab, &section.BabNum,
			&section.BabTitle, &section.Bagian, &section.Paragraf,
			&section.Pasal, &section.PasalNum, &section.Ayat,
			&section.Text, &section.Words,
		); err != nil {
			internalError(w, s.log, "scan section", err)
			return
		}
		results = append(results, section)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read sections", err)
		return
	}

	writeData(w, results, &Meta{
		Total:   total,
		Limit:   limit,
		Offset:  offset,
		HasMore: int64(offset+len(results)) < total,
		Layer:   "silver",
	})
}

// handleRegulationCitations serves what a regulation cites.
func (s *Server) handleRegulationCitations(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	key, err := pathIdentifier(r, "key")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "regulation_citations") {
		notFound(w, "no regulations have been imported",
			"run `scripts/import-regulations.sh`")
		return
	}
	citations, err := s.source(storage.LayerSilver, "regulation_citations")
	if err != nil {
		internalError(w, s.log, "resolve citations", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, `
		SELECT idx, instrument, scope, number, year, cited_title, found_in, cite_key
		FROM `+citations+` WHERE key = ? ORDER BY idx`, key)
	if err != nil {
		internalError(w, s.log, "query citations", err)
		return
	}
	defer rows.Close()

	results := make([]Citation, 0, 32)
	for rows.Next() {
		var citation Citation
		if err := rows.Scan(
			&citation.Index, &citation.Instrument, &citation.Scope,
			&citation.Number, &citation.Year, &citation.CitedTitle,
			&citation.FoundIn, &citation.CiteKey,
		); err != nil {
			internalError(w, s.log, "scan citation", err)
			return
		}
		results = append(results, citation)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read citations", err)
		return
	}

	writeData(w, results, &Meta{Total: int64(len(results)), Layer: "silver"})
}

// handleRegulationFacets serves the filter options, counted under whatever
// filters are already applied.
//
// Under the current filters rather than over the whole corpus: a reader who has
// picked Sulawesi Selatan wants the years that province actually legislated in,
// and a year offering zero results is a dead end the server could have known
// about.
func (s *Server) handleRegulationFacets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	where, args, err := regulationFilters(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	regulations, ok := s.regulations(ctx, w)
	if !ok {
		return
	}

	facets := Facets{}
	for _, f := range []struct {
		column string
		into   *[]Facet
		// Regions run to five hundred and years to seventy-five; the rest are
		// short. A cap keeps one runaway column from filling the response.
		limit int
		order string
	}{
		{"track", &facets.Tracks, 10, "count(*) DESC"},
		{"instrument", &facets.Instruments, 30, "count(*) DESC"},
		{"region_type", &facets.RegionTypes, 10, "count(*) DESC"},
		{"region_name", &facets.Regions, 600, "count(*) DESC"},
		{"category", &facets.Categories, 60, "count(*) DESC"},
		{"year", &facets.Years, 200, "1 DESC"},
	} {
		// The caller's filters and this column's not-null test are one clause,
		// so a facet never counts rows the list would not show.
		present := fmt.Sprintf(
			"%s IS NOT NULL AND CAST(%s AS VARCHAR) <> ''", f.column, f.column)
		filter := " WHERE " + present
		if where != "" {
			filter = where + " AND " + present
		}

		rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
			SELECT CAST(%s AS VARCHAR) AS value, count(*) AS n
			FROM %s%s
			GROUP BY 1 ORDER BY %s LIMIT %d`,
			f.column, regulations, filter, f.order, f.limit,
		), args...)
		if err != nil {
			internalError(w, s.log, "query facets", err)
			return
		}

		values := make([]Facet, 0, 16)
		for rows.Next() {
			var facet Facet
			if err := rows.Scan(&facet.Value, &facet.Count); err != nil {
				rows.Close()
				internalError(w, s.log, "scan facet", err)
				return
			}
			values = append(values, facet)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			internalError(w, s.log, "read facets", err)
			return
		}
		rows.Close()
		*f.into = values
	}

	writeData(w, facets, &Meta{Layer: "silver"})
}
