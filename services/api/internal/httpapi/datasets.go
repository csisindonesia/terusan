package httpapi

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Dataset is a collection as its publisher issues it (program.md §9).
//
// The unit between a source and an indicator: Bank Indonesia's consumer survey
// is one dataset holding four series, and a reader looking for "the consumer
// survey" is looking for this rather than for any one of them.
//
// Derived from the observations rather than stored, like Indicator — a dataset
// with nothing behind it is not worth advertising, and a coverage range read
// off the figures cannot disagree with them. What the figures cannot say (who
// publishes it, under what licence, on what schedule) is joined from the source
// registry.
type Dataset struct {
	DatasetID string `json:"dataset_id"`
	// What the collection is called and what it is found by, from the Silver
	// dataset catalogue. Absent until `terusan silver dimensions` has
	// published it — the figures alone cannot say any of this.
	Slug        *string  `json:"slug,omitempty"`
	Title       *string  `json:"title,omitempty"`
	Description *string  `json:"description,omitempty"`
	Tags        []string `json:"tags"`
	SourceID    string   `json:"source_id"`
	// From the registry, and absent until `terusan silver dimensions` has
	// published it into the lake.
	SourceName   *string `json:"source_name,omitempty"`
	Organization *string `json:"organization,omitempty"`
	License      *string `json:"license,omitempty"`
	Schedule     *string `json:"schedule,omitempty"`
	// Named rather than counted: a reader deciding whether to open a dataset
	// wants to know which series are inside it.
	Indicators   []string `json:"indicators"`
	Observations int64    `json:"observations"`
	PeriodStart  string   `json:"period_start"`
	PeriodEnd    string   `json:"period_end"`
	LastUpdated  *string  `json:"last_updated,omitempty"`
}

// datasetSelect groups the figures and hangs the registry record off them.
//
// A LEFT JOIN, and a registry that may not be published at all: a dataset whose
// source is missing from it is still a real collection of figures, and dropping
// it here would hide the gap rather than show it.
func (s *Server) datasetSelect(ctx context.Context) (string, error) {
	observations, err := s.source(storage.LayerSilver, "observations")
	if err != nil {
		return "", err
	}

	sources := "(SELECT NULL AS source_id, NULL AS name, NULL AS organization, " +
		"NULL AS license, NULL AS schedule) s ON false"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "sources") {
		registry, err := s.source(storage.LayerSilver, "sources")
		if err != nil {
			return "", err
		}
		sources = registry + " s ON s.source_id = o.source_id"
	}

	// The catalogue, on the same terms as the registry: a collection missing
	// from it is still a real collection of figures, and dropping it here
	// would hide the figures rather than the gap.
	catalogue := "(SELECT NULL AS dataset_id, NULL AS slug, NULL AS title, " +
		"NULL AS description, NULL AS tags) d ON false"
	if s.warehouse.Exists(ctx, storage.LayerSilver, "datasets") {
		published, err := s.source(storage.LayerSilver, "datasets")
		if err != nil {
			return "", err
		}
		catalogue = published + " d ON d.dataset_id = o.dataset_id"
	}

	return fmt.Sprintf(`
		SELECT o.dataset_id,
		       any_value(d.slug),
		       any_value(d.title),
		       any_value(d.description),
		       any_value(d.tags),
		       any_value(o.source_id),
		       any_value(s.name),
		       any_value(s.organization),
		       any_value(s.license),
		       any_value(s.schedule),
		       list_sort(list(DISTINCT o.indicator_id)),
		       count(*),
		       min(o.period),
		       max(o.period),
		       CAST(max(o.processed_at) AS VARCHAR)
		FROM %s o
		LEFT JOIN %s
		LEFT JOIN %s
		WHERE o.dataset_id IS NOT NULL
		GROUP BY o.dataset_id`, observations, sources, catalogue), nil
}

func (s *Server) handleDatasets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		// Nothing normalized yet. The collections are derived from the
		// observations, so there is nothing to derive them from — and
		// read_parquet over a directory holding no files is an error, not an
		// empty scan, which would answer a bare lake with a 500.
		writeData(w, []Dataset{}, &Meta{Total: 0, Layer: "silver"})
		return
	}

	datasets, err := s.datasetRows(ctx)
	if err != nil {
		internalError(w, s.log, "query datasets", err)
		return
	}

	writeData(w, datasets, &Meta{Total: int64(len(datasets)), Layer: "silver"})
}

// datasetRows returns every collection, by id.
//
// The caller has checked that the observations exist.
func (s *Server) datasetRows(ctx context.Context) ([]Dataset, error) {
	query, err := s.datasetSelect(ctx)
	if err != nil {
		return nil, err
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, query+" ORDER BY o.dataset_id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	datasets := make([]Dataset, 0, 16)
	for rows.Next() {
		d, err := scanDataset(rows)
		if err != nil {
			return nil, err
		}
		datasets = append(datasets, d)
	}
	return datasets, rows.Err()
}

// handleDataset returns one collection.
//
// A separate route rather than a filter on the list, for the same reason an
// indicator has one: a detail page asks a different question, and a 404 is the
// honest answer to an identifier that names nothing.
func (s *Server) handleDataset(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	id := r.PathValue("id")
	if !identifierPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed identifier")
		return
	}

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		notFound(w, "dataset not found", id)
		return
	}

	query, err := s.datasetSelect(ctx)
	if err != nil {
		internalError(w, s.log, "resolve datasets", err)
		return
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, query+" HAVING o.dataset_id = ?", id)
	if err != nil {
		internalError(w, s.log, "query dataset", err)
		return
	}
	defer rows.Close()

	if !rows.Next() {
		notFound(w, "dataset not found", id)
		return
	}

	d, err := scanDataset(rows)
	if err != nil {
		internalError(w, s.log, "scan dataset", err)
		return
	}

	writeData(w, d, &Meta{Total: 1, Layer: "silver"})
}

// scanDataset reads one row of `datasetSelect`.
//
// Shared by the list and the detail route so the two cannot drift: they select
// the same columns, and a column added to one and not the other is a panic at
// scan time rather than a compile error.
func scanDataset(rows *sql.Rows) (Dataset, error) {
	var d Dataset
	var indicators, tags any
	if err := rows.Scan(
		&d.DatasetID, &d.Slug, &d.Title, &d.Description, &tags, &d.SourceID,
		&d.SourceName, &d.Organization, &d.License, &d.Schedule, &indicators,
		&d.Observations, &d.PeriodStart, &d.PeriodEnd, &d.LastUpdated,
	); err != nil {
		return d, err
	}
	d.Indicators = asStrings(indicators)
	d.Tags = asStrings(tags)
	if d.Tags == nil {
		d.Tags = []string{}
	}
	return d, nil
}
