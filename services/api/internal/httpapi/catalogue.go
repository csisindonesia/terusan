package httpapi

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// The catalogue: every series, every collection and every commodity, as the
// lake summarises them.
//
// Each of the three is a scan of every observation, and five places want
// them — the series and collection lists, a collection's page, the home
// page's figures and the assistant, which used to rebuild all three inside a
// reader's turn every five minutes. They are built here, once, and shared:
// held in the process, rebuilt in the background once older than
// catalogueFresh or after a pipeline run, and built before the first reader
// when the server starts. Ten readers arriving at a cold server start one
// build between them.

// catalogueFresh is how long the catalogue is served without a rebuild.
// Past it, it is still served while the rebuild runs.
const catalogueFresh = 10 * time.Minute

// catalogueBuildTimeout bounds a build. Longer than a request's timeout: a
// reader who gives up should not waste the scan the next reader needs.
const catalogueBuildTimeout = 5 * time.Minute

type lakeCatalogue struct {
	datasets    []Dataset
	series      []Indicator
	commodities []Commodity
	at          time.Time
}

type catalogueStore struct {
	mu          sync.Mutex
	held        *lakeCatalogue
	refreshing  bool
	flight      chan struct{}
	staleBefore time.Time
}

// lakeCatalogue returns the catalogue, building it only if none is held.
func (s *Server) lakeCatalogue(ctx context.Context) (*lakeCatalogue, error) {
	store := &s.catalogue
	store.mu.Lock()
	if held := store.held; held != nil {
		stale := time.Since(held.at) > catalogueFresh || held.at.Before(store.staleBefore)
		if stale && !store.refreshing {
			store.refreshing = true
			go s.rebuildCatalogue()
		}
		store.mu.Unlock()
		return held, nil
	}
	if flight := store.flight; flight != nil {
		store.mu.Unlock()
		select {
		case <-flight:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
		store.mu.Lock()
		held := store.held
		store.mu.Unlock()
		if held != nil {
			return held, nil
		}
		return s.buildCatalogue(ctx) // the first build failed; try once more
	}
	flight := make(chan struct{})
	store.flight = flight
	store.mu.Unlock()

	built, err := s.buildCatalogueDetached()
	store.mu.Lock()
	if err == nil {
		store.held = built
	}
	store.flight = nil
	close(flight)
	store.mu.Unlock()
	return built, err
}

// buildCatalogueDetached builds under its own deadline rather than the
// reader's, so the build is not thrown away if they close the tab.
func (s *Server) buildCatalogueDetached() (*lakeCatalogue, error) {
	ctx, cancel := context.WithTimeout(context.Background(), catalogueBuildTimeout)
	defer cancel()
	return s.buildCatalogue(ctx)
}

func (s *Server) rebuildCatalogue() {
	started := time.Now()
	built, err := s.buildCatalogueDetached()
	store := &s.catalogue
	store.mu.Lock()
	defer store.mu.Unlock()
	store.refreshing = false
	if err != nil {
		s.log.Warn("catalogue.rebuild_failed", "error", err)
		return
	}
	store.held = built
	s.log.Info("catalogue.rebuilt", "series", len(built.series), "ms", time.Since(started).Milliseconds())
}

// buildCatalogue reads the three summaries side by side.
func (s *Server) buildCatalogue(ctx context.Context) (*lakeCatalogue, error) {
	built := &lakeCatalogue{
		datasets: []Dataset{}, series: []Indicator{}, commodities: []Commodity{}, at: time.Now(),
	}
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return built, nil
	}
	var wg sync.WaitGroup
	var datasetsErr, seriesErr, commoditiesErr error
	wg.Add(3)
	go func() {
		defer wg.Done()
		built.datasets, datasetsErr = s.datasetRows(ctx)
	}()
	go func() {
		defer wg.Done()
		built.series, seriesErr = s.indicatorRows(ctx, discardWriter{}, "", nil)
	}()
	go func() {
		defer wg.Done()
		built.commodities, commoditiesErr = s.commodityRows(ctx)
	}()
	wg.Wait()
	for _, err := range []error{datasetsErr, seriesErr, commoditiesErr} {
		if err != nil {
			return nil, err
		}
	}
	return built, nil
}

// staleCatalogue marks the held catalogue for a rebuild after a pipeline run.
func (s *Server) staleCatalogue() {
	s.catalogue.mu.Lock()
	s.catalogue.staleBefore = time.Now()
	s.catalogue.mu.Unlock()
}

// LakeStats is what the home page counts: the headline figures, without the
// eighteen megabytes of series it used to download to add them up.
type LakeStats struct {
	Series       int    `json:"series"`
	Observations int64  `json:"observations"`
	Datasets     int    `json:"datasets"`
	Commodities  int    `json:"commodities"`
	Sources      int    `json:"sources"`
	AsOf         string `json:"as_of"`
}

func (s *Server) handleStats(w http.ResponseWriter, r *http.Request) {
	catalogue, err := s.lakeCatalogue(r.Context())
	if err != nil {
		internalError(w, s.log, "build catalogue", err)
		return
	}
	stats := LakeStats{
		Series:      len(catalogue.series),
		Datasets:    len(catalogue.datasets),
		Commodities: len(catalogue.commodities),
		AsOf:        catalogue.at.UTC().Format(time.RFC3339),
	}
	sources := map[string]bool{}
	for _, series := range catalogue.series {
		stats.Observations += series.Observations
		for _, source := range series.Sources {
			sources[source] = true
		}
	}
	stats.Sources = len(sources)
	writeData(w, stats, &Meta{Layer: "silver"})
}
