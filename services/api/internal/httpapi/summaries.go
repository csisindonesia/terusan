package httpapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"time"
)

// The lake's summaries, kept in the process and refreshed behind the reader.
//
// A few routes answer by reading every observation: the series list with its
// coverage, the datasets with their counts, the lake's row counts, the
// commodities. With the BPS catalogue landed that is 31,000 partitions, one
// file each, and every one of those routes takes 15 to 30 seconds to open
// them all — the home page waited on four of them.
//
// The response cache does not help enough. It is Redis, which a laptop does
// not run, and its minute-long TTL means some reader pays the full wait every
// minute. These answers change when a pipeline runs, a few times a day, so
// they are kept here instead: served at once while a copy is held, recomputed
// in the background once it is older than summaryFresh, and computed ahead of
// the first reader when the server starts. A reader sees an answer up to one
// refresh old, never a thirty-second spinner.

const (
	// How long a summary is served without being recomputed. Past this it is
	// still served, and recomputed in the background.
	summaryFresh = 10 * time.Minute
	// How many distinct query strings are held. Summaries are read with few
	// shapes (the portal asks for the whole list); a search box typing into
	// one of these routes would otherwise grow the map without end.
	summaryMaxEntries = 128
	// How long a background recompute may take.
	summaryRefreshTimeout = 3 * time.Minute
)

// summaryPaths are computed when the server starts, as the portal asks for
// them: the home page's four, and the commodity list the filters load.
var summaryPaths = []string{
	"/v1/storage",
	"/v1/indicators",
	"/v1/datasets",
	"/v1/commodities?limit=1000",
}

type summaryEntry struct {
	status     int
	header     http.Header
	body       []byte
	at         time.Time
	refreshing bool
}

type summaryStore struct {
	mu      sync.Mutex
	entries map[string]*summaryEntry
	// First computations in progress, so ten readers arriving at a cold
	// route start one scan rather than ten.
	flights map[string]chan struct{}
	// Whatever was computed before this is stale, however young.
	staleBefore time.Time
}

// withSummary serves a route from the process's copy of its answer.
func (s *Server) withSummary(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key := cacheKey(r)
		store := &s.summaries

		store.mu.Lock()
		if store.entries == nil {
			store.entries = map[string]*summaryEntry{}
			store.flights = map[string]chan struct{}{}
		}
		if entry := store.entries[key]; entry != nil {
			stale := time.Since(entry.at) > summaryFresh || entry.at.Before(store.staleBefore)
			if stale && !entry.refreshing {
				entry.refreshing = true
				go s.refreshSummary(key, r.URL.RequestURI(), next)
			}
			held := *entry
			store.mu.Unlock()
			writeSummary(w, &held, stale)
			return
		}
		if flight, ok := store.flights[key]; ok {
			store.mu.Unlock()
			select {
			case <-flight:
			case <-r.Context().Done():
				return
			}
			store.mu.Lock()
			entry := store.entries[key]
			store.mu.Unlock()
			if entry != nil {
				writeSummary(w, entry, false)
				return
			}
			// The first computation failed; this reader tries for itself.
			next(w, r)
			return
		}
		flight := make(chan struct{})
		store.flights[key] = flight
		store.mu.Unlock()

		entry := s.computeSummary(r.Context(), r.URL.RequestURI(), next)
		store.mu.Lock()
		if entry != nil {
			s.keepSummary(key, entry)
		}
		delete(store.flights, key)
		close(flight)
		store.mu.Unlock()

		if entry == nil {
			// Not kept (an error, or the reader went away): answered as the
			// route would have answered.
			next(w, r)
			return
		}
		writeSummary(w, entry, false)
	}
}

// computeSummary runs the route and returns its answer if it is one to keep.
func (s *Server) computeSummary(ctx context.Context, uri string, next http.HandlerFunc) *summaryEntry {
	request := httptest.NewRequestWithContext(ctx, http.MethodGet, uri, nil)
	recorded := &recorder{header: http.Header{}}
	next(recorded, request)
	status := recorded.status
	if status == 0 {
		status = http.StatusOK
	}
	if !cacheable(status, recorded.body.Bytes()) {
		return nil
	}
	return &summaryEntry{
		status: status,
		header: recorded.header,
		body:   append([]byte(nil), recorded.body.Bytes()...),
		at:     time.Now(),
	}
}

// refreshSummary recomputes a held answer in the background.
func (s *Server) refreshSummary(key, uri string, next http.HandlerFunc) {
	ctx, cancel := context.WithTimeout(context.Background(), summaryRefreshTimeout)
	defer cancel()
	started := time.Now()
	entry := s.computeSummary(ctx, uri, next)

	store := &s.summaries
	store.mu.Lock()
	defer store.mu.Unlock()
	if entry == nil {
		// Kept as it was, and tried again on the next read.
		if held := store.entries[key]; held != nil {
			held.refreshing = false
		}
		s.log.Warn("summary.refresh_failed", "route", uri)
		return
	}
	s.keepSummary(key, entry)
	s.log.Info("summary.refreshed", "route", uri, "ms", time.Since(started).Milliseconds())
}

// keepSummary stores an answer, dropping the oldest past the limit. Called
// with the store locked.
func (s *Server) keepSummary(key string, entry *summaryEntry) {
	store := &s.summaries
	store.entries[key] = entry
	for len(store.entries) > summaryMaxEntries {
		oldest := ""
		for k, e := range store.entries {
			if oldest == "" || e.at.Before(store.entries[oldest].at) {
				oldest = k
			}
		}
		delete(store.entries, oldest)
	}
}

func writeSummary(w http.ResponseWriter, entry *summaryEntry, stale bool) {
	for name, values := range entry.header {
		w.Header()[name] = values
	}
	if stale {
		w.Header().Set("X-Summary", "stale")
	} else {
		w.Header().Set("X-Summary", "held")
	}
	w.WriteHeader(entry.status)
	_, _ = w.Write(entry.body)
}

// staleSummaries marks every held summary as needing a recompute, for when a
// pipeline run has changed the lake. They are still served until the
// recompute finishes: a minute of the previous answer beats thirty seconds of
// none.
func (s *Server) staleSummaries() {
	s.summaries.mu.Lock()
	s.summaries.staleBefore = time.Now()
	s.summaries.mu.Unlock()
}

// WarmSummaries computes the summaries the portal asks for first, so the
// first reader after a start does not wait for them. Run once, in the
// background, after the server is built.
//
// The route's own handler is called, so the key is the one a reader's request
// makes, and the sign-in gate — which a warm-up has no session for — is not
// in the way.
func (s *Server) WarmSummaries(ctx context.Context) {
	for _, path := range summaryPaths {
		if ctx.Err() != nil {
			return
		}
		request := httptest.NewRequestWithContext(ctx, http.MethodGet, path, nil)
		route, ok := s.summaryRoutes[request.URL.Path]
		if !ok {
			continue
		}
		started := time.Now()
		route(discardWriter{}, request)
		s.log.Info("summary.warmed", "route", path, "ms", time.Since(started).Milliseconds())
	}
}

// summary wraps a route in withSummary and remembers it for the warm-up.
func (s *Server) summary(path string, next http.HandlerFunc) http.HandlerFunc {
	wrapped := s.withSummary(next)
	if s.summaryRoutes == nil {
		s.summaryRoutes = map[string]http.HandlerFunc{}
	}
	s.summaryRoutes[path] = wrapped
	return wrapped
}
