package httpapi

import (
	"database/sql"
	"net/http"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Source is the registry record for a provider: the settings its figures are
// collected under (program.md §16).
//
// Served from the lake rather than from the catalog database, because the
// catalog is optional and a reader asking "when does this refresh, and what may
// I do with it" should not depend on a Postgres being up.
type Source struct {
	SourceID         string  `json:"source_id"`
	Name             string  `json:"name"`
	Organization     *string `json:"organization,omitempty"`
	Category         string  `json:"category"`
	SourceType       string  `json:"source_type"`
	CollectionMethod string  `json:"collection_method"`
	BaseURL          *string `json:"base_url,omitempty"`
	Country          *string `json:"country,omitempty"`
	License          *string `json:"license,omitempty"`
	UpdateFrequency  string  `json:"update_frequency"`
	// Cron, or absent where the source is run by hand only.
	Schedule *string `json:"schedule,omitempty"`
	Active   bool    `json:"active"`
	// The source's own ceiling. The runner also limits per host, so several
	// sources sharing one server cannot add up to a hammering.
	MaxRequestsPerSecond float64 `json:"max_requests_per_second"`
	Notes                *string `json:"notes,omitempty"`
	// What a reader finds the provider by: derived from this record when it is
	// published, so it says the same things the columns above do, in the words
	// someone would search with.
	Tags []string `json:"tags"`
}

// handleSources lists the registry.
//
// Unfiltered and unpaged: there are tens of these, not thousands, and a reader
// wanting one picks it out of the list.
func (s *Server) handleSources(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	if !s.warehouse.Exists(ctx, storage.LayerSilver, "sources") {
		// Not an error: a lake that has never run `silver dimensions` has no
		// registry published yet, and an empty list says that plainly.
		writeData(w, []Source{}, &Meta{Total: 0, Layer: "silver"})
		return
	}

	expression, err := s.source(storage.LayerSilver, "sources")
	if err != nil {
		internalError(w, s.log, "resolve sources", err)
		return
	}

	// Tags arrived after the first lakes were written, so a file that predates
	// them still has to serve: the column is selected where it exists and read
	// as empty where it does not.
	tagged := "[]::VARCHAR[] AS tags"
	if s.warehouse.HasColumn(ctx, storage.LayerSilver, "sources", "tags") {
		tagged = "COALESCE(tags, []::VARCHAR[]) AS tags"
	}

	rows, err := s.warehouse.DB().QueryContext(ctx, `
		SELECT source_id, name, organization, category, source_type,
		       collection_method, base_url, country, license, update_frequency,
		       schedule, active, max_requests_per_second, notes,
		       `+tagged+`
		FROM `+expression+` ORDER BY source_id`)
	if err != nil {
		internalError(w, s.log, "query sources", err)
		return
	}
	defer rows.Close()

	results := make([]Source, 0, 32)
	for rows.Next() {
		var source Source
		var tags any
		if err := rows.Scan(
			&source.SourceID, &source.Name, &source.Organization, &source.Category,
			&source.SourceType, &source.CollectionMethod, &source.BaseURL,
			&source.Country, &source.License, &source.UpdateFrequency,
			&source.Schedule, &source.Active, &source.MaxRequestsPerSecond,
			&source.Notes, &tags,
		); err != nil {
			internalError(w, s.log, "scan source", err)
			return
		}
		source.Tags = asStrings(tags)
		if source.Tags == nil {
			source.Tags = []string{}
		}
		results = append(results, source)
	}
	if err := rows.Err(); err != nil {
		internalError(w, s.log, "read sources", err)
		return
	}

	writeData(w, results, &Meta{Total: int64(len(results)), Layer: "silver"})
}

var _ = sql.ErrNoRows
