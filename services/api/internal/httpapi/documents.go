package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Document is one publication the warehouse collected — a handbook, a report,
// a paper (program.md §13).
//
// Documents only. A scraper lands far more than this: spreadsheets, CSVs, the
// pages crawled to find a series. Those stay in RAW and their provenance
// travels on the observation row itself — `document_id`, `content_hash`,
// `source_url`, `raw_path` — rather than through this table, which exists so a
// reader can find something to read.
//
// The grain is the retrieval, not the publication. Two editions of one
// handbook are two documents, because they are two files with two content
// hashes — and an observation points at one of them and not the other.
//
// Distinct from Regulation, which is a legal instrument with articles and
// citations and its own model (program.md §14). A regulation is a document in
// the ordinary sense of the word and not in the sense of this table.
type Document struct {
	DocumentID string `json:"document_id"`
	// regulation, report, publication, web_page, data_file. Never empty: an
	// artifact nobody has classified is a data_file, which is what an unread
	// download is.
	DocumentType string `json:"document_type"`
	Title        string `json:"title"`
	// What tells two artifacts of one collection apart — "edition 2025". A
	// handbook's fifteen editions carry near-identical filenames.
	Subtitle *string `json:"subtitle,omitempty"`
	Language *string `json:"language,omitempty"`
	Author   *string `json:"author,omitempty"`
	// Who issued it, which is not who we collected it from.
	Publisher   *string `json:"publisher,omitempty"`
	PublishedAt *string `json:"published_at,omitempty"`

	// Where the publisher put it. The preserved copy is served from
	// /v1/documents/{id}/file; this is the address that breaks when an agency
	// reorganises its site, which is why the copy exists (program.md §2.1).
	SourceURL        *string `json:"source_url,omitempty"`
	OriginalFilename *string `json:"original_filename,omitempty"`
	MediaType        *string `json:"media_type,omitempty"`
	SizeBytes        *int64  `json:"size_bytes,omitempty"`

	// Known only for what an extractor has actually read, so absent for most.
	PageCount *int `json:"page_count,omitempty"`
	WordCount *int `json:"word_count,omitempty"`

	// What rests on it, counted from the observations' own provenance. A zero
	// is a real answer: plenty of what the warehouse collects is a listing
	// page or an index that no figure was read out of.
	IndicatorCount   int   `json:"indicator_count"`
	ObservationCount int64 `json:"observation_count"`

	DatasetID   *string  `json:"dataset_id,omitempty"`
	DatasetSlug *string  `json:"dataset_slug,omitempty"`
	Partition   []string `json:"partition"`

	SourceID    string  `json:"source_id"`
	ContentHash *string `json:"content_hash,omitempty"`
	RetrievedAt *string `json:"retrieved_at,omitempty"`
}

// DocumentDetail adds what a reader needs once they have chosen a document.
type DocumentDetail struct {
	Document
	SourceName   *string `json:"source_name,omitempty"`
	Organization *string `json:"organization,omitempty"`
	// What the material may be used for. Absent rather than guessed: a
	// document served without its licence is a document nobody can safely
	// republish (program.md §16).
	License *string `json:"license,omitempty"`
	// Whether the preserved copy can actually be served from this deployment.
	// False where the lake is a bucket, and the reader should follow
	// SourceURL instead.
	FileAvailable bool `json:"file_available"`
}

// DocumentFacets is every filter's options for the documents list.
type DocumentFacets struct {
	Types      []Facet `json:"types"`
	Sources    []Facet `json:"sources"`
	MediaTypes []Facet `json:"media_types"`
	Publishers []Facet `json:"publishers"`
	// Keyed by dataset_id, which is what the filter takes and what a link from
	// a dataset page carries. The reader sees the collection's title, which
	// the portal looks up — a facet carries a value and a count and has
	// nowhere to put a label.
	Datasets []Facet `json:"datasets"`
	Years    []Facet `json:"years"`
}

// NULLS LAST throughout, as for regulations: most of what lands states no
// publication date, and a plain DESC sorts those to the top of every page.
var documentOrder = map[string]string{
	"published":  "published_at ASC NULLS LAST, title ASC",
	"-published": "published_at DESC NULLS LAST, title ASC",
	"retrieved":  "retrieved_at ASC NULLS LAST, title ASC",
	"-retrieved": "retrieved_at DESC NULLS LAST, title ASC",
	"title":      "title ASC",
	"-title":     "title DESC",
	"size":       "size_bytes ASC NULLS LAST, title ASC",
	"-size":      "size_bytes DESC NULLS LAST, title ASC",
	// What the document actually contributed. The default, because a
	// catalogue of fifteen hundred downloads is most usefully entered at the
	// end that the figures were read from.
	"observations":  "observation_count ASC, title ASC",
	"-observations": "observation_count DESC, title ASC",
}

const (
	minDocumentYear = 1800
	maxDocumentYear = 2100
)

// mediaTypePattern is looser than identifierPattern, which forbids the slash
// every media type contains.
var mediaTypePattern = regexp.MustCompile(
	`^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}$`)

// documentFilters builds the WHERE clause shared by the list, the count and
// the facets, so a facet count can never describe a different set of rows from
// the list beside it.
func documentFilters(r *http.Request) (string, []any, error) {
	var clauses []string
	args := []any{}

	for _, f := range []struct {
		param   string
		column  string
		pattern *regexp.Regexp
	}{
		{"type", "document_type", identifierPattern},
		{"source_id", "source_id", identifierPattern},
		{"dataset_id", "dataset_id", identifierPattern},
		{"media_type", "media_type", mediaTypePattern},
	} {
		values, err := stringListParam(r, f.param, f.pattern)
		if err != nil {
			return "", nil, err
		}
		if clause, bound := inClause(f.column, values); clause != "" {
			clauses = append(clauses, clause)
			args = append(args, bound...)
		}
	}

	// Publisher by name: the reader picking one out of a facet list has the
	// name, and there is no identifier for a publisher to offer them.
	publisher, err := stringParam(r, "publisher", searchPattern)
	if err != nil {
		return "", nil, err
	}
	if publisher != "" {
		clauses = append(clauses, "publisher = ?")
		args = append(args, publisher)
	}

	year, err := intParam(r, "year", 0, minDocumentYear, maxDocumentYear)
	if err != nil {
		return "", nil, err
	}
	if year != 0 {
		clauses = append(clauses, "year(published_at) = ?")
		args = append(args, year)
	}

	// Backs figures, or does not. The two are different corpora for most
	// purposes: half of what lands is a listing page that produced nothing,
	// and a reader looking for sources wants the other half.
	switch r.URL.Query().Get("has_data") {
	case "":
	case "true":
		clauses = append(clauses, "observation_count > 0")
	case "false":
		clauses = append(clauses, "observation_count = 0")
	default:
		return "", nil, &paramError{param: "has_data", reason: "must be true or false"}
	}

	search, err := stringParam(r, "q", searchPattern)
	if err != nil {
		return "", nil, err
	}
	if search != "" {
		// Title, publisher and filename: a reader searching "handbook" means
		// the title, one searching "ESDM" means the publisher, and one who
		// pasted a filename out of a URL means neither.
		clauses = append(clauses,
			"(title ILIKE ? OR coalesce(publisher, '') ILIKE ? "+
				"OR coalesce(original_filename, '') ILIKE ?)")
		pattern := "%" + search + "%"
		args = append(args, pattern, pattern, pattern)
	}

	if len(clauses) == 0 {
		return "", args, nil
	}
	return " WHERE " + strings.Join(clauses, " AND "), args, nil
}

// documentColumns is the list projection, shared so the row scan below cannot
// drift from it.
const documentColumns = `
	document_id, document_type, title, subtitle, language, author, publisher,
	CAST(published_at AS VARCHAR), source_url, original_filename, media_type,
	size_bytes, page_count, word_count, indicator_count, observation_count,
	dataset_id, dataset_slug, partition, source_id, content_hash,
	strftime(retrieved_at, '%Y-%m-%dT%H:%M:%SZ')`

// documentColumnsQualified is the same projection for the detail join, where
// `source_id` exists in both tables and a bare name would not bind.
const documentColumnsQualified = `
	d.document_id, d.document_type, d.title, d.subtitle, d.language, d.author,
	d.publisher, CAST(d.published_at AS VARCHAR), d.source_url,
	d.original_filename, d.media_type, d.size_bytes, d.page_count, d.word_count,
	d.indicator_count, d.observation_count, d.dataset_id, d.dataset_slug,
	d.partition, d.source_id, d.content_hash,
	strftime(d.retrieved_at, '%Y-%m-%dT%H:%M:%SZ')`

// scanner is what both Query rows and a QueryRow satisfy, so one scan serves
// the list and the detail.
type scanner interface{ Scan(...any) error }

func scanDocument(rows scanner, out *Document) error {
	var partition any
	if err := rows.Scan(
		&out.DocumentID, &out.DocumentType, &out.Title, &out.Subtitle, &out.Language,
		&out.Author, &out.Publisher, &out.PublishedAt, &out.SourceURL,
		&out.OriginalFilename, &out.MediaType, &out.SizeBytes, &out.PageCount,
		&out.WordCount, &out.IndicatorCount, &out.ObservationCount, &out.DatasetID,
		&out.DatasetSlug, &partition, &out.SourceID, &out.ContentHash, &out.RetrievedAt,
	); err != nil {
		return err
	}
	// Never null in the response: a reader reading the partition should get an
	// empty list for an unpartitioned document, not a null to guard.
	out.Partition = asStrings(partition)
	if out.Partition == nil {
		out.Partition = []string{}
	}
	return nil
}

func scanDocumentDetail(row scanner, out *DocumentDetail) error {
	var partition any
	if err := row.Scan(
		&out.DocumentID, &out.DocumentType, &out.Title, &out.Subtitle, &out.Language,
		&out.Author, &out.Publisher, &out.PublishedAt, &out.SourceURL,
		&out.OriginalFilename, &out.MediaType, &out.SizeBytes, &out.PageCount,
		&out.WordCount, &out.IndicatorCount, &out.ObservationCount, &out.DatasetID,
		&out.DatasetSlug, &partition, &out.SourceID, &out.ContentHash, &out.RetrievedAt,
		&out.SourceName, &out.Organization, &out.License,
	); err != nil {
		return err
	}
	out.Partition = asStrings(partition)
	if out.Partition == nil {
		out.Partition = []string{}
	}
	return nil
}

// documents resolves the silver dataset, or reports that it has not been
// built. Returned as an error the caller writes, because every handler here
// needs the same answer.
func (s *Server) documents(ctx context.Context, w http.ResponseWriter) (string, bool) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "documents") {
		notFound(w, "the document catalogue has not been built",
			"run `terusan silver documents`")
		return "", false
	}
	expression, err := s.source(storage.LayerSilver, "documents")
	if err != nil {
		internalError(w, s.log, "resolve documents", err)
		return "", false
	}
	return expression, true
}

// handleDocuments lists the collected material.
func (s *Server) handleDocuments(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	where, args, err := documentFilters(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	order, err := sortOrder(r, documentOrder, "-observations")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, offset, err := pagination(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	documents, ok := s.documents(ctx, w)
	if !ok {
		return
	}

	var total int64
	if err := s.warehouse.DB().QueryRowContext(
		ctx, "SELECT count(*) FROM "+documents+where, args...,
	).Scan(&total); err != nil {
		internalError(w, s.log, "count documents", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
		"SELECT %s FROM %s%s ORDER BY %s LIMIT ? OFFSET ?",
		documentColumns, documents, where, order,
	), append(args, limit, offset)...)
	if err != nil {
		internalError(w, s.log, "query documents", err)
		return
	}
	defer rows.Close()

	results := make([]Document, 0, limit)
	for rows.Next() {
		var document Document
		if err := scanDocument(rows, &document); err != nil {
			internalError(w, s.log, "scan document", err)
			return
		}
		results = append(results, document)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read documents", err)
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

// handleDocument serves one document, with its source registry record.
func (s *Server) handleDocument(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id, err := pathIdentifier(r, "id")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	documents, ok := s.documents(ctx, w)
	if !ok {
		return
	}

	// The registry may not be published, and a document whose collector is
	// unknown is still a document — so the join is left, as it is for
	// regulations and datasets.
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

	query := fmt.Sprintf(
		"SELECT %s, s.name, s.organization, s.license FROM %s d LEFT JOIN %s "+
			"WHERE d.document_id = ?",
		documentColumnsQualified, documents, sources,
	)

	var detail DocumentDetail
	row := s.warehouse.DB().QueryRowContext(ctx, query, id)
	if err := scanDocumentDetail(row, &detail); err != nil {
		if err == sql.ErrNoRows {
			notFound(w, "no such document", id)
			return
		}
		internalError(w, s.log, "query document", err)
		return
	}

	detail.FileAvailable = s.fileServable(&detail.Document)
	writeData(w, detail, &Meta{Layer: "silver"})
}

// handleDocumentIndicators lists the series a document produced.
//
// This is the link the catalogue exists for. Every observation carries the
// document it was read out of, so "what came out of the 2025 handbook" is a
// question the figures can answer about themselves — and answering it from the
// figures means the catalogue can never claim a series it did not produce.
func (s *Server) handleDocumentIndicators(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id, err := pathIdentifier(r, "id")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	// Not narrowed: the partition key is the series, and which series came
	// out of a document is the question being asked.
	indicators, err := s.indicatorRows(ctx, w, "WHERE o.document_id = ?", []any{id})
	if err != nil {
		return // already reported
	}
	writeData(w, indicators, &Meta{Total: int64(len(indicators)), Layer: "silver"})
}

// handleIndicatorDocuments lists the material one series was read out of.
//
// The reverse of handleDocumentIndicators, and the half a reader actually
// reaches for: they are looking at a figure and want to know what it came
// from. Almost every series here has exactly one such document, and the few
// with more are collections republished in instalments.
//
// Driven by the observations rather than by the catalogue, so a series can
// only ever name the documents its own figures name.
func (s *Server) handleIndicatorDocuments(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id, err := pathIdentifier(r, "id")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	documents, ok := s.documents(ctx, w)
	if !ok {
		return
	}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []Document{}, &Meta{Total: 0, Layer: "silver"})
		return
	}
	// The one series' partition, not the lake: the id is known.
	observations, err := s.warehouse.SourceIn(storage.LayerSilver, "observations", "indicator_id", []string{id})
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	// A semi-join through a subquery rather than a join onto the observations:
	// a series has a quarter of a million rows and one document, and joining
	// would produce that many duplicate document rows to fold back down.
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
		"SELECT %s FROM %s WHERE document_id IN ("+
			"SELECT DISTINCT document_id FROM %s WHERE indicator_id = ? "+
			"AND document_id IS NOT NULL) ORDER BY published_at DESC NULLS LAST, title",
		documentColumns, documents, observations,
	), id)
	if err != nil {
		internalError(w, s.log, "query indicator documents", err)
		return
	}
	defer rows.Close()

	results := make([]Document, 0, 4)
	for rows.Next() {
		var document Document
		if err := scanDocument(rows, &document); err != nil {
			internalError(w, s.log, "scan document", err)
			return
		}
		results = append(results, document)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read indicator documents", err)
		return
	}

	writeData(w, results, &Meta{Total: int64(len(results)), Layer: "silver"})
}

// handleDocumentFacets serves the filter options for the documents list.
func (s *Server) handleDocumentFacets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	where, args, err := documentFilters(r)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	documents, ok := s.documents(ctx, w)
	if !ok {
		return
	}

	facets := DocumentFacets{}
	for _, f := range []struct {
		column string
		into   *[]Facet
		order  string
		limit  int
	}{
		{"document_type", &facets.Types, "count(*) DESC", 20},
		{"source_id", &facets.Sources, "count(*) DESC", 100},
		{"media_type", &facets.MediaTypes, "count(*) DESC", 40},
		{"publisher", &facets.Publishers, "count(*) DESC", 100},
		{"dataset_id", &facets.Datasets, "count(*) DESC", 100},
		// Years descending by value, not by count: a reader picking a year
		// wants them in order, and "2025 (1)" above "2019 (400)" is the
		// ordering they expect from a date.
		{"CAST(year(published_at) AS VARCHAR)", &facets.Years, "value DESC", 120},
	} {
		// The filter is the list's own, minus nothing: a facet that ignored
		// the active filters would offer a reader options that return zero
		// rows. Null values are excluded because "no publisher stated" is not
		// a publisher anyone can filter by.
		filter := where
		if filter == "" {
			filter = " WHERE " + f.column + " IS NOT NULL"
		} else {
			filter += " AND " + f.column + " IS NOT NULL"
		}

		rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(
			"SELECT %s AS value, count(*) AS n FROM %s%s GROUP BY value "+
				"ORDER BY %s LIMIT %d",
			f.column, documents, filter, f.order, f.limit,
		), args...)
		if err != nil {
			internalError(w, s.log, "query document facets", err)
			return
		}
		values := make([]Facet, 0, f.limit)
		for rows.Next() {
			var facet Facet
			if err := rows.Scan(&facet.Value, &facet.Count); err != nil {
				rows.Close()
				internalError(w, s.log, "scan document facet", err)
				return
			}
			values = append(values, facet)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			internalError(w, s.log, "read document facets", err)
			return
		}
		*f.into = values
	}

	writeData(w, facets, &Meta{Layer: "silver"})
}

// handleDocumentFile serves the preserved copy out of RAW.
//
// The point of keeping originals (program.md §2.1) is that they remain
// readable after the agency moves the file, so the catalogue has to be able to
// hand one back. The source URL is served alongside and stays the citation;
// this is the copy.
//
// Object storage is refused rather than proxied: streaming a bucket through
// the API would make the serving layer a bandwidth bottleneck for something
// the storage layer can sign a URL for, and doing it badly is worse than
// saying it is not available here.
func (s *Server) handleDocumentFile(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id, err := pathIdentifier(r, "id")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	documents, ok := s.documents(ctx, w)
	if !ok {
		return
	}

	var rawPath, filename, mediaType *string
	if err := s.warehouse.DB().QueryRowContext(ctx,
		"SELECT raw_path, original_filename, media_type FROM "+documents+
			" WHERE document_id = ?", id,
	).Scan(&rawPath, &filename, &mediaType); err != nil {
		if err == sql.ErrNoRows {
			notFound(w, "no such document", id)
			return
		}
		internalError(w, s.log, "query document file", err)
		return
	}

	if s.cfg.Storage.IsObjectStorage() {
		writeError(w, http.StatusNotImplemented, CodeUnavailable,
			"the preserved copy is not served from this deployment",
			"the lake is object storage; follow the document's source_url")
		return
	}
	if rawPath == nil || *rawPath == "" {
		notFound(w, "this document has no preserved copy recorded", id)
		return
	}

	path, err := s.rawFile(*rawPath)
	if err != nil {
		// A catalogue row pointing outside RAW is a corrupt catalogue, not a
		// caller's mistake, so it is logged rather than explained.
		internalError(w, s.log, "resolve raw file", err)
		return
	}

	file, err := os.Open(path)
	if err != nil {
		notFound(w, "the preserved copy is not on this machine",
			"the catalogue was built against a lake this deployment cannot read")
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		internalError(w, s.log, "stat raw file", err)
		return
	}

	served := "application/octet-stream"
	if mediaType != nil && *mediaType != "" {
		served = *mediaType
	}
	w.Header().Set("Content-Type", served)
	w.Header().Set("X-Content-Type-Options", "nosniff")

	inline := servesInline(r, served)
	disposition := "attachment"
	if inline {
		disposition = "inline"
		// Belt and braces around a PDF from a government CMS: a sandboxed
		// response cannot navigate the opener, submit a form or run script,
		// and the browser's own viewer needs none of those to render a page.
		w.Header().Set("Content-Security-Policy", "sandbox")
	}
	w.Header().Set("Content-Disposition",
		fmt.Sprintf("%s; filename=%q", disposition, downloadName(filename, id)))

	// ServeContent rather than io.Copy: it handles range requests and
	// conditional gets, which a ten-megabyte handbook wants and a browser
	// asks for without being told.
	http.ServeContent(w, r, downloadName(filename, id), info.ModTime(), file)
}

// servesInline reports whether the preserved copy may render in the browser
// rather than download.
//
// Attachment is the default, because these are bytes fetched from third-party
// sites: a landed HTML page rendered inline in the API's own origin would run
// whatever script it arrived with, and half of what this warehouse collects is
// a scraped page.
//
// `?inline=1` asks for the opposite so the portal can frame a document and let
// somebody read it. Granted for PDFs alone, and judged by the media type the
// catalogue recorded rather than by anything the request says — the caller
// chooses the disposition, never the type.
func servesInline(r *http.Request, mediaType string) bool {
	return r.URL.Query().Get("inline") == "1" && mediaType == "application/pdf"
}

// fileServable reports whether this deployment can hand back the preserved
// copy, so the detail response can say so before a reader clicks.
func (s *Server) fileServable(document *Document) bool {
	if s.cfg.Storage.IsObjectStorage() {
		return false
	}
	return document.OriginalFilename != nil
}

// rawFile turns a catalogued RAW path into an absolute one, refusing anything
// that escapes the layer.
//
// The path comes from our own Parquet rather than from the caller, so this
// guards against a corrupt catalogue rather than an attacker — but the
// document id that selects the row does come from the caller, and a check that
// only holds while every writer behaves is not a check.
func (s *Server) rawFile(relative string) (string, error) {
	root, err := s.storage.Resolve(storage.LayerRaw)
	if err != nil {
		return "", err
	}
	// An absolute path is refused rather than joined. filepath.Join would
	// quietly reinterpret "/etc/passwd" as "<root>/raw/etc/passwd" — inside
	// RAW, so the containment check below would pass, and the server would
	// serve a file at a path the catalogue never recorded. The catalogue
	// stores paths relative to the layer, so an absolute one is corrupt.
	local := filepath.FromSlash(relative)
	if filepath.IsAbs(local) || strings.HasPrefix(relative, "/") {
		return "", fmt.Errorf("catalogued path %q is absolute; it must be relative to RAW", relative)
	}

	root = filepath.Clean(root)
	path := filepath.Clean(filepath.Join(root, local))
	if path != root && !strings.HasPrefix(path, root+string(filepath.Separator)) {
		return "", fmt.Errorf("catalogued path %q resolves outside %s", relative, root)
	}
	return path, nil
}

// downloadName is what the browser should save the file as.
func downloadName(filename *string, fallback string) string {
	if filename != nil && *filename != "" {
		// Quoted into a header, so the one character that could end the quoted
		// string early is removed. The names are slugified at landing and
		// never contain it; this is here so that stays true.
		return strings.NewReplacer(`"`, "", `\`, "", "\r", "", "\n", "").Replace(*filename)
	}
	return fallback
}
