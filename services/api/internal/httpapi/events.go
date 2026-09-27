package httpapi

import (
	"context"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Events: dated happenings a series is read against — national holidays,
// cuti bersama, Ramadan — published into Silver by `terusan silver events`.
//
// An event has no figure, only days. What a reader asks of one is "what did
// prices do around it", so the portal and the assistant ask this for the
// holidays in a series' span and set them beside its figures.

// Event is one occurrence: Idul Fitri 2024's national holiday is one, the cuti
// bersama around it another, and Idul Fitri 2025 a third.
type Event struct {
	EventID  string `json:"event_id"`
	Category string `json:"category"`
	Kind     string `json:"kind"`
	// What recurs: `idul_fitri` across every year.
	Key         string   `json:"key"`
	Name        string   `json:"name"`
	NameEn      *string  `json:"name_en,omitempty"`
	NamePrinted *string  `json:"name_printed,omitempty"`
	Religion    *string  `json:"religion,omitempty"`
	Calendar    *string  `json:"calendar,omitempty"`
	Year        int      `json:"year"`
	StartDate   string   `json:"start_date"`
	EndDate     string   `json:"end_date"`
	Dates       []string `json:"dates"`
	GeoID       string   `json:"geo_id"`
	// True where the dates are derived rather than decreed: Ramadan.
	Approximate bool    `json:"approximate"`
	Basis       *string `json:"basis,omitempty"`
	SourceID    string  `json:"source_id"`
	SourceURL   *string `json:"source_url,omitempty"`
}

// eventFilter is what a caller narrows events by. Zero values select all.
type eventFilter struct {
	Categories []string
	Kinds      []string
	Keys       []string
	Religions  []string
	From, To   string // ISO dates, inclusive; an event overlapping the span is in it
}

var datePattern = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// handleEvents lists events, oldest first.
//
// Unpaged: a year holds a few dozen, and the span a reader asks about is
// rarely more than a decade.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	var filter eventFilter
	var err error
	for name, into := range map[string]*[]string{
		"category": &filter.Categories, "kind": &filter.Kinds,
		"key": &filter.Keys, "religion": &filter.Religions,
	} {
		if *into, err = stringListParam(r, name, identifierPattern); err != nil {
			badRequest(w, "invalid parameter", err.Error())
			return
		}
	}
	for name, into := range map[string]*string{"from": &filter.From, "to": &filter.To} {
		if *into, err = stringParam(r, name, datePattern); err != nil {
			badRequest(w, "invalid parameter", err.Error())
			return
		}
		if *into != "" {
			if _, err := time.Parse("2006-01-02", *into); err != nil {
				badRequest(w, "invalid parameter", name+": is not a date")
				return
			}
		}
	}

	events, err := s.events(r.Context(), filter)
	if err != nil {
		internalError(w, s.log, "query events", err)
		return
	}
	writeData(w, events, &Meta{Total: int64(len(events)), Layer: "silver"})
}

// events reads the calendar. A lake that has never published one has none,
// which is an empty list rather than an error.
func (s *Server) events(ctx context.Context, filter eventFilter) ([]Event, error) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "events") {
		return []Event{}, nil
	}
	expression, err := s.source(storage.LayerSilver, "events")
	if err != nil {
		return nil, err
	}

	var where []string
	var args []any
	for column, values := range map[string][]string{
		"category": filter.Categories, "kind": filter.Kinds,
		"key": filter.Keys, "religion": filter.Religions,
	} {
		if clause, params := inClause(column, values); clause != "" {
			where = append(where, clause)
			args = append(args, params...)
		}
	}
	if filter.From != "" {
		where = append(where, "end_date >= CAST(? AS DATE)")
		args = append(args, filter.From)
	}
	if filter.To != "" {
		where = append(where, "start_date <= CAST(? AS DATE)")
		args = append(args, filter.To)
	}
	query := `
		SELECT event_id, category, kind, key, name, name_en, name_printed,
		       religion, calendar, year,
		       strftime(start_date, '%Y-%m-%d'), strftime(end_date, '%Y-%m-%d'),
		       list_transform(dates, d -> strftime(d, '%Y-%m-%d')),
		       geo_id, approximate, basis, source_id, source_url
		FROM ` + expression
	if len(where) > 0 {
		query += " WHERE " + strings.Join(where, " AND ")
	}
	query += " ORDER BY start_date, kind, key"

	rows, err := s.warehouse.DB().QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	events := make([]Event, 0, 64)
	for rows.Next() {
		var e Event
		var dates any
		if err := rows.Scan(
			&e.EventID, &e.Category, &e.Kind, &e.Key, &e.Name, &e.NameEn, &e.NamePrinted,
			&e.Religion, &e.Calendar, &e.Year, &e.StartDate, &e.EndDate, &dates,
			&e.GeoID, &e.Approximate, &e.Basis, &e.SourceID, &e.SourceURL,
		); err != nil {
			return nil, err
		}
		e.Dates = asStrings(dates)
		if e.Dates == nil {
			e.Dates = []string{}
		}
		events = append(events, e)
	}
	return events, rows.Err()
}
