package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"sort"
	"strconv"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Data governance: what the lake holds, described rather than served.
//
// The rest of the API answers questions about figures. These endpoints answer
// questions about the warehouse itself — what each table looks like, how clean
// each series is, which vocabularies classify it, where a dataset came from,
// and which version of the code produced each layer. All of it is read off the
// lake as it stands, for the same reason the run journal is: the catalog
// database is optional, and an account of the data that could disagree with
// the data is worse than none.

// ---------------------------------------------------------------- schemas

// TableSchema is one physical table and its columns, as the files declare
// them.
type TableSchema struct {
	Slug    string         `json:"slug"`
	Layer   string         `json:"layer"`
	Dataset string         `json:"dataset"`
	Name    string         `json:"name"`
	Columns []SchemaColumn `json:"columns"`
}

type SchemaColumn struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

// handleSchemas describes every table in the lake.
//
// Read over all of a table's files at once, so a column that only newer files
// carry is still listed — what a reader of the schema needs to know is
// everything they may find, not what the oldest file happened to hold.
func (s *Server) handleSchemas(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	schemas := make([]TableSchema, 0, len(lakeTables))
	for _, table := range lakeTables {
		if !s.warehouse.Exists(ctx, table.layer, table.dataset) {
			continue
		}
		expression, err := s.source(table.layer, table.dataset)
		if err != nil {
			internalError(w, s.log, "resolve "+table.dataset, err)
			return
		}
		columns, err := s.describe(ctx, expression)
		if err != nil {
			// One unreadable table should not hide the others; it is logged
			// and left out, as /v1/storage does with a table it cannot count.
			s.log.Warn("schemas.describe_failed", "dataset", table.dataset, "error", err)
			continue
		}
		schemas = append(schemas, TableSchema{
			Slug:    fmt.Sprintf("%s-%s", table.layer, table.dataset),
			Layer:   table.layer.String(),
			Dataset: table.dataset,
			Name:    table.name,
			Columns: columns,
		})
	}
	writeData(w, schemas, &Meta{Total: int64(len(schemas))})
}

func (s *Server) describe(ctx context.Context, expression string) ([]SchemaColumn, error) {
	rows, err := s.warehouse.DB().QueryContext(ctx, "DESCRIBE SELECT * FROM "+expression)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var columns []SchemaColumn
	for rows.Next() {
		var column SchemaColumn
		var null, key, def, extra sql.NullString
		if err := rows.Scan(&column.Name, &column.Type, &null, &key, &def, &extra); err != nil {
			return nil, err
		}
		columns = append(columns, column)
	}
	return columns, rows.Err()
}

// ---------------------------------------------------------------- quality

// SeriesQuality is one series' figures, counted by what normalization made of
// them.
//
// Normalization refuses to guess: a missing marker stays a status rather than
// becoming zero, and a place it cannot resolve stays unresolved with the text
// it was printed as. That is what makes those figures countable here — each
// one is a decision someone may want to revisit.
type SeriesQuality struct {
	IndicatorID string  `json:"indicator_id"`
	Name        *string `json:"name,omitempty"`
	DatasetID   *string `json:"dataset_id,omitempty"`
	SourceID    *string `json:"source_id,omitempty"`
	Figures     int64   `json:"figures"`
	// Figures by status: `ok`, and every marker the source printed instead
	// of a number — `missing`, `unparseable`, and whatever else it uses.
	Statuses map[string]int64 `json:"statuses"`
	// Figures whose place was printed but did not resolve to the registry.
	Unplaced int64 `json:"unplaced"`
	// Where another series says the same thing better, the one that
	// supersedes it.
	SupersededBy     *string  `json:"superseded_by,omitempty"`
	SupersededByName *string  `json:"superseded_by_name,omitempty"`
	MatchShare       *float64 `json:"match_share,omitempty"`
}

// handleQuality serves every series' quality counts, in indicator order.
//
// Unpaged: a few thousand rows of counts, which the page filters in place.
func (s *Server) handleQuality(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		writeData(w, []SeriesQuality{}, &Meta{Total: 0, Layer: "silver"})
		return
	}

	from, figures, unplaced := rollupTable, "sum(o.n)", "sum(o.unplaced)"
	if !s.rolled() {
		observations, err := s.source(storage.LayerSilver, "observations")
		if err != nil {
			internalError(w, s.log, "resolve observations", err)
			return
		}
		from, figures = observations, "count(*)"
		unplaced = "count(*) FILTER (WHERE o.geo_id IS NULL AND o.geo_name_raw IS NOT NULL)"
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, `
		SELECT o.indicator_id, any_value(o.dataset_id), any_value(o.source_id),
		       coalesce(o.status, 'ok'), CAST(`+figures+` AS BIGINT), CAST(`+unplaced+` AS BIGINT)
		FROM `+from+` o
		GROUP BY o.indicator_id, coalesce(o.status, 'ok')
		ORDER BY o.indicator_id`)
	if err != nil {
		internalError(w, s.log, "count quality", err)
		return
	}
	defer rows.Close()

	byID := map[string]*SeriesQuality{}
	var order []string
	for rows.Next() {
		var id, status string
		var dataset, source *string
		var count, misplaced int64
		if err := rows.Scan(&id, &dataset, &source, &status, &count, &misplaced); err != nil {
			internalError(w, s.log, "scan quality", err)
			return
		}
		series, ok := byID[id]
		if !ok {
			series = &SeriesQuality{
				IndicatorID: id, DatasetID: dataset, SourceID: source,
				Statuses: map[string]int64{},
			}
			byID[id] = series
			order = append(order, id)
		}
		series.Figures += count
		series.Statuses[status] += count
		series.Unplaced += misplaced
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read quality", err)
		return
	}

	// The names and the duplicates are joined after rather than in the
	// query: either table may be absent, and a series is still worth
	// counting without them.
	if s.warehouse.Exists(ctx, storage.LayerSilver, "indicators") {
		if err := s.eachRow(ctx, storage.LayerSilver, "indicators",
			"SELECT indicator_id, name FROM %s", func(scan func(...any) error) error {
				var id string
				var name *string
				if err := scan(&id, &name); err != nil {
					return err
				}
				if series, ok := byID[id]; ok {
					series.Name = name
				}
				return nil
			}); err != nil {
			internalError(w, s.log, "name series", err)
			return
		}
	}
	if s.warehouse.Exists(ctx, storage.LayerSilver, "indicator_duplicates") {
		if err := s.eachRow(ctx, storage.LayerSilver, "indicator_duplicates",
			"SELECT indicator_id, superseded_by, superseded_by_name, match_share FROM %s",
			func(scan func(...any) error) error {
				var id string
				var by, byName *string
				var share *float64
				if err := scan(&id, &by, &byName, &share); err != nil {
					return err
				}
				if series, ok := byID[id]; ok {
					series.SupersededBy, series.SupersededByName, series.MatchShare = by, byName, share
				}
				return nil
			}); err != nil {
			internalError(w, s.log, "read duplicates", err)
			return
		}
	}

	results := make([]SeriesQuality, 0, len(order))
	for _, id := range order {
		results = append(results, *byID[id])
	}
	writeData(w, results, &Meta{Total: int64(len(results)), Layer: "silver"})
}

// eachRow runs a query over one table, `%s` standing for its expression, and
// hands each row to fn.
func (s *Server) eachRow(
	ctx context.Context, layer storage.Layer, dataset, query string,
	fn func(scan func(...any) error) error, args ...any,
) error {
	expression, err := s.source(layer, dataset)
	if err != nil {
		return err
	}
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(query, expression), args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		if err := fn(rows.Scan); err != nil {
			return err
		}
	}
	return rows.Err()
}

// ---------------------------------------------------------- classifications

// Classification is one controlled vocabulary and how much of the lake each of
// its terms classifies.
type Classification struct {
	Scheme string `json:"scheme"`
	Label  string `json:"label"`
	// Where the terms are read from: the table and column a reader would
	// query to apply one.
	Table  string `json:"table"`
	Column string `json:"column"`
	// What `count` counts: indicators, figures, places, sources.
	Unit   string            `json:"unit"`
	Values []ClassifiedValue `json:"values"`
	// How many carry no term at all. Not an error in every scheme — a
	// national series has no subcategory — but always worth seeing.
	Unclassified int64 `json:"unclassified"`
}

type ClassifiedValue struct {
	Value string `json:"value"`
	Count int64  `json:"count"`
}

// classificationScheme is where one vocabulary lives.
type classificationScheme struct {
	scheme, label     string
	layer             storage.Layer
	dataset, column   string
	unit              string
	list              bool // the column holds a list of terms, like tags
	figuresFromRollup bool
}

// maxClassifiedValues bounds one vocabulary. Units run to a few hundred; past
// that a list is a column of free text rather than a vocabulary.
const maxClassifiedValues = 500

var classificationSchemes = []classificationScheme{
	{scheme: "indicator.category", label: "Indicator category", layer: storage.LayerSilver, dataset: "indicators", column: "category", unit: "series"},
	{scheme: "indicator.subcategory", label: "Indicator subcategory", layer: storage.LayerSilver, dataset: "indicators", column: "subcategory", unit: "series"},
	{scheme: "indicator.frequency", label: "Frequency", layer: storage.LayerSilver, dataset: "indicators", column: "frequency", unit: "series"},
	{scheme: "indicator.tags", label: "Indicator tags", layer: storage.LayerSilver, dataset: "indicators", column: "tags", unit: "series", list: true},
	{scheme: "observation.status", label: "Figure status", layer: storage.LayerSilver, dataset: "observations", column: "status", unit: "figures", figuresFromRollup: true},
	{scheme: "observation.temporal_resolution", label: "Temporal resolution", layer: storage.LayerSilver, dataset: "observations", column: "temporal_resolution", unit: "figures", figuresFromRollup: true},
	{scheme: "observation.unit", label: "Unit", layer: storage.LayerSilver, dataset: "observations", column: "unit", unit: "figures", figuresFromRollup: true},
	{scheme: "geography.geo_type", label: "Place type", layer: storage.LayerSilver, dataset: "geography", column: "geo_type", unit: "places"},
	{scheme: "commodity.category", label: "Commodity category", layer: storage.LayerSilver, dataset: "commodities", column: "category", unit: "commodities"},
	{scheme: "document.document_type", label: "Document type", layer: storage.LayerSilver, dataset: "documents", column: "document_type", unit: "documents"},
	{scheme: "source.category", label: "Source category", layer: storage.LayerSilver, dataset: "sources", column: "category", unit: "sources"},
	{scheme: "source.source_type", label: "Source type", layer: storage.LayerSilver, dataset: "sources", column: "source_type", unit: "sources"},
	{scheme: "source.collection_method", label: "Collection method", layer: storage.LayerSilver, dataset: "sources", column: "collection_method", unit: "sources"},
	{scheme: "source.update_frequency", label: "Update frequency", layer: storage.LayerSilver, dataset: "sources", column: "update_frequency", unit: "sources"},
}

// handleClassifications lists every vocabulary with its terms, most used
// first.
func (s *Server) handleClassifications(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	results := make([]Classification, 0, len(classificationSchemes))
	for _, scheme := range classificationSchemes {
		if !s.warehouse.Exists(ctx, scheme.layer, scheme.dataset) ||
			!s.warehouse.HasColumn(ctx, scheme.layer, scheme.dataset, scheme.column) {
			continue
		}
		classification, err := s.classify(ctx, scheme)
		if err != nil {
			s.log.Warn("classifications.failed", "scheme", scheme.scheme, "error", err)
			continue
		}
		results = append(results, classification)
	}
	writeData(w, results, &Meta{Total: int64(len(results))})
}

func (s *Server) classify(ctx context.Context, scheme classificationScheme) (Classification, error) {
	out := Classification{
		Scheme: scheme.scheme, Label: scheme.label,
		Table:  fmt.Sprintf("%s.%s", scheme.layer, scheme.dataset),
		Column: scheme.column, Unit: scheme.unit, Values: []ClassifiedValue{},
	}

	from, count := "", "count(*)"
	if scheme.figuresFromRollup && s.rolled() {
		from, count = rollupTable, "sum(n)"
	} else {
		expression, err := s.source(scheme.layer, scheme.dataset)
		if err != nil {
			return out, err
		}
		from = expression
	}

	// The column names come from the constant table above, never from the
	// request, so they are safe to place in the text.
	term := "CAST(" + scheme.column + " AS VARCHAR)"
	values := fmt.Sprintf(`SELECT %s AS term, CAST(%s AS BIGINT) FROM %s
		WHERE %s IS NOT NULL AND %s <> '' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %d`,
		term, count, from, scheme.column, term, maxClassifiedValues)
	missing := fmt.Sprintf(`SELECT CAST(coalesce(%s, 0) AS BIGINT) FROM %s
		WHERE %s IS NULL OR %s = ''`, count, from, scheme.column, term)
	if scheme.list {
		values = fmt.Sprintf(`SELECT term, count(*) FROM
			(SELECT unnest(%s) AS term FROM %s) WHERE term IS NOT NULL AND term <> ''
			GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %d`, scheme.column, from, maxClassifiedValues)
		missing = fmt.Sprintf(`SELECT count(*) FROM %s
			WHERE %s IS NULL OR len(%s) = 0`, from, scheme.column, scheme.column)
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, values)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var value ClassifiedValue
		if err := rows.Scan(&value.Value, &value.Count); err != nil {
			return out, err
		}
		out.Values = append(out.Values, value)
	}
	if err := rows.Err(); err != nil {
		return out, err
	}
	if err := s.warehouse.DB().QueryRowContext(ctx, missing).Scan(&out.Unclassified); err != nil {
		return out, err
	}
	return out, nil
}

// ---------------------------------------------------------------- lineage

// Lineage is the path one dataset's figures took: the source that fetched
// them, the RAW documents they were read out of, the parser versions that
// read them into Bronze, the series normalization published, and the
// pipelines that did each step.
type Lineage struct {
	DatasetID string  `json:"dataset_id"`
	Slug      *string `json:"slug,omitempty"`
	SourceID  *string `json:"source_id,omitempty"`
	// The documents behind the figures, most recently retrieved first, and
	// how many there are in all — the list is bounded, the count is not.
	Documents      []LineageDocument `json:"documents"`
	DocumentsTotal int64             `json:"documents_total"`
	// What extraction made of the source's documents, by parser version.
	Extractions []LineageExtraction `json:"extractions"`
	Series      []LineageSeries     `json:"series"`
	Pipelines   []Pipeline          `json:"pipelines"`
}

type LineageDocument struct {
	DocumentID  string   `json:"document_id"`
	SourceURL   *string  `json:"source_url,omitempty"`
	RawPath     *string  `json:"raw_path,omitempty"`
	ContentHash *string  `json:"content_hash,omitempty"`
	RetrievedAt *string  `json:"retrieved_at,omitempty"`
	Figures     int64    `json:"figures"`
	Series      []string `json:"series"`
}

type LineageExtraction struct {
	ParserVersion string `json:"parser_version"`
	Documents     int64  `json:"documents"`
	Records       int64  `json:"records"`
}

type LineageSeries struct {
	IndicatorID string  `json:"indicator_id"`
	Name        *string `json:"name,omitempty"`
	Figures     int64   `json:"figures"`
}

// maxLineageDocuments bounds the document list. A daily price series is read
// out of thousands of daily files, and the newest few hundred say everything
// the page has room for.
const maxLineageDocuments = 200

// handleLineage traces one dataset back to its source.
func (s *Server) handleLineage(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := r.PathValue("id")
	if !identifierPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed identifier")
		return
	}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		notFound(w, "no such dataset", "the lake holds no observations yet")
		return
	}
	observations, err := s.source(storage.LayerSilver, "observations")
	if err != nil {
		internalError(w, s.log, "resolve observations", err)
		return
	}

	lineage := Lineage{
		DatasetID: id, Documents: []LineageDocument{}, Extractions: []LineageExtraction{},
		Series: []LineageSeries{}, Pipelines: []Pipeline{},
	}

	// The series first, off the rollup where there is one: it is also what
	// says whether the dataset exists at all.
	from, figures := rollupTable, "sum(o.n)"
	if !s.rolled() {
		from, figures = observations, "count(*)"
	}
	rows, err := s.warehouse.DB().QueryContext(ctx, `
		SELECT o.indicator_id, any_value(o.source_id), CAST(`+figures+` AS BIGINT)
		FROM `+from+` o WHERE o.dataset_id = ?
		GROUP BY o.indicator_id ORDER BY 3 DESC, 1`, id)
	if err != nil {
		internalError(w, s.log, "trace series", err)
		return
	}
	for rows.Next() {
		var series LineageSeries
		var source *string
		if err := rows.Scan(&series.IndicatorID, &source, &series.Figures); err != nil {
			rows.Close()
			internalError(w, s.log, "scan series", err)
			return
		}
		if lineage.SourceID == nil {
			lineage.SourceID = source
		}
		lineage.Series = append(lineage.Series, series)
	}
	rows.Close()
	if len(lineage.Series) == 0 {
		notFound(w, "no such dataset", "id: "+id+" has no figures in Silver")
		return
	}

	// Named where the catalogue names them: a bulk source's identifiers are
	// derived codes, and a list of those says nothing about what is inside.
	if s.warehouse.Exists(ctx, storage.LayerSilver, "indicators") {
		index := make(map[string]int, len(lineage.Series))
		for n, series := range lineage.Series {
			index[series.IndicatorID] = n
		}
		if err := s.eachRow(ctx, storage.LayerSilver, "indicators",
			"SELECT indicator_id, name FROM %s",
			func(scan func(...any) error) error {
				var id string
				var name *string
				if err := scan(&id, &name); err != nil {
					return err
				}
				if n, ok := index[id]; ok {
					lineage.Series[n].Name = name
				}
				return nil
			}); err != nil {
			internalError(w, s.log, "name series", err)
			return
		}
	}

	if s.warehouse.Exists(ctx, storage.LayerSilver, "datasets") {
		if err := s.eachRow(ctx, storage.LayerSilver, "datasets",
			"SELECT slug FROM %s WHERE dataset_id = ? LIMIT 1",
			func(scan func(...any) error) error { return scan(&lineage.Slug) }, id); err != nil {
			internalError(w, s.log, "read dataset slug", err)
			return
		}
	}

	documents, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT document_id, any_value(source_url), any_value(raw_path), any_value(content_hash),
		       strftime(max(retrieved_at) AT TIME ZONE 'UTC', '%%Y-%%m-%%dT%%H:%%M:%%SZ'),
		       count(*), list(DISTINCT indicator_id ORDER BY indicator_id),
		       count(*) OVER ()
		FROM %s WHERE dataset_id = ? AND document_id IS NOT NULL
		GROUP BY document_id
		ORDER BY max(retrieved_at) DESC NULLS LAST, document_id
		LIMIT %d`, observations, maxLineageDocuments), id)
	if err != nil {
		internalError(w, s.log, "trace documents", err)
		return
	}
	for documents.Next() {
		var document LineageDocument
		var series any
		if err := documents.Scan(
			&document.DocumentID, &document.SourceURL, &document.RawPath, &document.ContentHash,
			&document.RetrievedAt, &document.Figures, &series, &lineage.DocumentsTotal,
		); err != nil {
			documents.Close()
			internalError(w, s.log, "scan document", err)
			return
		}
		document.Series = asStrings(series)
		if document.Series == nil {
			document.Series = []string{}
		}
		lineage.Documents = append(lineage.Documents, document)
	}
	documents.Close()

	if lineage.SourceID != nil {
		source := *lineage.SourceID
		if s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
			if err := s.eachRow(ctx, storage.LayerBronze, "records", `
				SELECT parser_version, count(DISTINCT document_id), count(*) FROM %s
				WHERE source_id = ? AND parser_version IS NOT NULL
				GROUP BY 1 ORDER BY TRY_CAST(parser_version AS INTEGER) DESC NULLS LAST, 1 DESC`,
				func(scan func(...any) error) error {
					var extraction LineageExtraction
					if err := scan(&extraction.ParserVersion, &extraction.Documents, &extraction.Records); err != nil {
						return err
					}
					lineage.Extractions = append(lineage.Extractions, extraction)
					return nil
				}, source); err != nil {
				internalError(w, s.log, "trace extraction", err)
				return
			}
		}

		// Extraction reads the whole of RAW and names no source, so its
		// pipeline is asked for by stage rather than by source.
		pipelines, err := s.pipelines(ctx,
			[]string{"(source_id = ? OR kind = 'extract')"}, []any{source})
		if err != nil {
			internalError(w, s.log, "trace pipelines", err)
			return
		}
		// A source can feed several datasets; only the normalizations that
		// write this one belong to its lineage.
		for _, pipeline := range pipelines {
			if pipeline.Kind == "normalize" && !s.normalizes(pipeline, lineage) {
				continue
			}
			lineage.Pipelines = append(lineage.Pipelines, pipeline)
		}
	}

	writeData(w, lineage, &Meta{Layer: "silver"})
}

// normalizes reports whether a normalization pipeline writes into this
// dataset: by the dataset slug it recorded, or by a series it produced.
func (s *Server) normalizes(pipeline Pipeline, lineage Lineage) bool {
	if pipeline.Dataset != nil && lineage.Slug != nil && *pipeline.Dataset == *lineage.Slug {
		return true
	}
	if pipeline.IndicatorID != nil {
		for _, series := range lineage.Series {
			if series.IndicatorID == *pipeline.IndicatorID {
				return true
			}
		}
	}
	return false
}

// ---------------------------------------------------------------- versions

// Versions is which code produced what is in the lake.
//
// Every Bronze row records the parser version that wrote it and every Silver
// row the pipeline version, so a change to either can be traced to the rows it
// touched (program.md §17). What a maintainer asks of that is "how much of the
// lake is still at the old version", and "which documents did the agency
// republish under the same address".
type Versions struct {
	// The newest parser version found in Bronze. The code's own constant is
	// not visible from here; the newest version the lake holds is what the
	// last extraction ran at, which is the same thing once it has run.
	LatestParser *string `json:"latest_parser,omitempty"`
	// Row counts per recorded version, per table.
	Tables []VersionedTable `json:"tables"`
	// Per source: how many of its documents have been read at the newest
	// parser version, and how many are still only at an older one.
	Sources []SourceVersions `json:"sources"`
	// Addresses that have served more than one content, newest change first.
	Republished      []RepublishedDocument `json:"republished"`
	RepublishedTotal int64                 `json:"republished_total"`
}

type VersionedTable struct {
	Layer   string  `json:"layer"`
	Dataset string  `json:"dataset"`
	Column  string  `json:"column"`
	Version *string `json:"version,omitempty"`
	Rows    int64   `json:"rows"`
}

type SourceVersions struct {
	SourceID  string `json:"source_id"`
	Documents int64  `json:"documents"`
	// Documents whose newest extraction is at the newest parser version, and
	// those last read by an older one.
	Current  int64    `json:"current"`
	Behind   int64    `json:"behind"`
	Versions []string `json:"versions"`
	Rows     int64    `json:"rows"`
	// Rows a newer extraction of the same document has replaced. Extraction
	// appends rather than overwrites, so these stay in Bronze until it is
	// compacted — kept on purpose, so an old reading can be compared with a
	// new one, and counted so the cost of keeping them is visible.
	SupersededRows int64 `json:"superseded_rows"`
}

type RepublishedDocument struct {
	SourceID       *string `json:"source_id,omitempty"`
	SourceURL      string  `json:"source_url"`
	Versions       int64   `json:"versions"`
	FirstRetrieved string  `json:"first_retrieved"`
	LastRetrieved  string  `json:"last_retrieved"`
}

const maxRepublished = 200

// handleVersions accounts for the versions in the lake.
func (s *Server) handleVersions(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	versions := Versions{
		Tables: []VersionedTable{}, Sources: []SourceVersions{}, Republished: []RepublishedDocument{},
	}

	for _, table := range []struct {
		layer   storage.Layer
		dataset string
		column  string
	}{
		{storage.LayerBronze, "records", "parser_version"},
		{storage.LayerBronze, "documents", "parser_version"},
		{storage.LayerBronze, "records", "pipeline_version"},
		{storage.LayerSilver, "observations", "pipeline_version"},
		{storage.LayerSilver, "documents", "pipeline_version"},
	} {
		if !s.warehouse.Exists(ctx, table.layer, table.dataset) ||
			!s.warehouse.HasColumn(ctx, table.layer, table.dataset, table.column) {
			continue
		}
		if err := s.eachRow(ctx, table.layer, table.dataset, `
			SELECT `+table.column+`, count(*) FROM %s GROUP BY 1
			ORDER BY TRY_CAST(`+table.column+` AS INTEGER) DESC NULLS LAST, 1 DESC`,
			func(scan func(...any) error) error {
				entry := VersionedTable{
					Layer: table.layer.String(), Dataset: table.dataset, Column: table.column,
				}
				if err := scan(&entry.Version, &entry.Rows); err != nil {
					return err
				}
				versions.Tables = append(versions.Tables, entry)
				return nil
			}); err != nil {
			internalError(w, s.log, "count versions", err)
			return
		}
	}

	if s.warehouse.Exists(ctx, storage.LayerBronze, "records") {
		// Versions compare as numbers, which is how they are bumped; one
		// that is not a number is listed but never counted as newest.
		if err := s.eachRow(ctx, storage.LayerBronze, "records", `
			WITH g AS (
				SELECT source_id, document_id, parser_version AS raw,
				       TRY_CAST(parser_version AS INTEGER) AS v, count(*) AS n
				FROM %s WHERE source_id IS NOT NULL GROUP BY ALL
			), d AS (
				SELECT *, max(v) OVER (PARTITION BY source_id, document_id) AS newest FROM g
			), latest AS (SELECT max(v) AS v FROM g)
			SELECT d.source_id, count(DISTINCT d.document_id),
			       count(DISTINCT d.document_id) FILTER (WHERE d.newest = latest.v),
			       count(DISTINCT d.document_id) FILTER (WHERE d.newest IS NULL OR d.newest < latest.v),
			       list_sort(list(DISTINCT d.raw) FILTER (WHERE d.raw IS NOT NULL)),
			       CAST(sum(d.n) AS BIGINT),
			       CAST(coalesce(sum(d.n) FILTER (WHERE d.v < d.newest), 0) AS BIGINT),
			       CAST(any_value(latest.v) AS VARCHAR)
			FROM d, latest GROUP BY d.source_id
			ORDER BY 4 DESC, 1`,
			func(scan func(...any) error) error {
				var entry SourceVersions
				var seen any
				var latest *string
				if err := scan(&entry.SourceID, &entry.Documents, &entry.Current,
					&entry.Behind, &seen, &entry.Rows, &entry.SupersededRows, &latest); err != nil {
					return err
				}
				entry.Versions = sortVersions(asStrings(seen))
				versions.LatestParser = latest
				versions.Sources = append(versions.Sources, entry)
				return nil
			}); err != nil {
			internalError(w, s.log, "compare parser versions", err)
			return
		}

		if err := s.eachRow(ctx, storage.LayerBronze, "records", fmt.Sprintf(`
			SELECT any_value(source_id), source_url, count(DISTINCT content_hash),
			       strftime(min(retrieved_at) AT TIME ZONE 'UTC', '%%%%Y-%%%%m-%%%%dT%%%%H:%%%%M:%%%%SZ'),
			       strftime(max(retrieved_at) AT TIME ZONE 'UTC', '%%%%Y-%%%%m-%%%%dT%%%%H:%%%%M:%%%%SZ'),
			       count(*) OVER ()
			FROM %%s WHERE source_url IS NOT NULL
			GROUP BY source_url HAVING count(DISTINCT content_hash) > 1
			ORDER BY max(retrieved_at) DESC, source_url LIMIT %d`, maxRepublished),
			func(scan func(...any) error) error {
				var entry RepublishedDocument
				if err := scan(&entry.SourceID, &entry.SourceURL, &entry.Versions,
					&entry.FirstRetrieved, &entry.LastRetrieved, &versions.RepublishedTotal); err != nil {
					return err
				}
				versions.Republished = append(versions.Republished, entry)
				return nil
			}); err != nil {
			internalError(w, s.log, "find republished documents", err)
			return
		}
	}

	writeData(w, versions, nil)
}

// sortVersions orders versions newest first, numerically where they are
// numbers.
func sortVersions(values []string) []string {
	if values == nil {
		return []string{}
	}
	sort.SliceStable(values, func(i, j int) bool {
		a, errA := strconv.Atoi(values[i])
		b, errB := strconv.Atoi(values[j])
		if errA == nil && errB == nil {
			return a > b
		}
		if (errA == nil) != (errB == nil) {
			return errA == nil
		}
		return values[i] > values[j]
	})
	return values
}
