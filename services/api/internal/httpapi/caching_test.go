package httpapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/config"
)

// memory is a Cache that keeps entries in a map, so the middleware can be
// tested without a Redis.
type memory struct {
	mu      sync.Mutex
	entries map[string][]byte
	sets    int
}

func newMemory() *memory { return &memory{entries: map[string][]byte{}} }

func (m *memory) Get(_ context.Context, key string) ([]byte, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	value, ok := m.entries[key]
	return value, ok
}

func (m *memory) Set(_ context.Context, key string, value []byte, _ time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.entries[key] = value
	m.sets++
}

func (m *memory) Purge(context.Context) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	clear(m.entries)
	return nil
}

func (m *memory) Stats() cache.Stats { return cache.Stats{Enabled: true, Backend: "memory"} }
func (m *memory) Close() error       { return nil }

func serverWithCache(c cache.Cache) *Server {
	return &Server{cfg: &config.Config{CacheTTL: time.Minute}, cache: c}
}

// ---- keys -----------------------------------------------------------------

// Otherwise the portal's two ways of spelling one request fill two entries and
// each is a separate cold miss.
func TestReorderedParametersAreOneCacheEntry(t *testing.T) {
	a := httptest.NewRequest(http.MethodGet, "/v1/observations?limit=10&order=period", nil)
	b := httptest.NewRequest(http.MethodGet, "/v1/observations?order=period&limit=10", nil)
	if cacheKey(a) != cacheKey(b) {
		t.Errorf("reordered parameters gave different keys:\n %s\n %s", cacheKey(a), cacheKey(b))
	}
}

func TestDifferentParametersAreDifferentEntries(t *testing.T) {
	a := httptest.NewRequest(http.MethodGet, "/v1/observations?limit=10", nil)
	b := httptest.NewRequest(http.MethodGet, "/v1/observations?limit=11", nil)
	if cacheKey(a) == cacheKey(b) {
		t.Error("two different queries share one key")
	}
}

func TestDifferentPathsAreDifferentEntriesEvenUnqueried(t *testing.T) {
	a := httptest.NewRequest(http.MethodGet, "/v1/indicators", nil)
	b := httptest.NewRequest(http.MethodGet, "/v1/datasets", nil)
	if cacheKey(a) == cacheKey(b) {
		t.Error("two endpoints share one key")
	}
}

// A repeated parameter is a list, and its order is the caller's meaning.
func TestRepeatedParametersKeepTheirOrder(t *testing.T) {
	a := httptest.NewRequest(http.MethodGet, "/v1/observations?geo=ID-32&geo=ID-31", nil)
	b := httptest.NewRequest(http.MethodGet, "/v1/observations?geo=ID-31&geo=ID-32", nil)
	if cacheKey(a) == cacheKey(b) {
		t.Error("two different filters share one key")
	}
}

// ---- what is worth keeping ------------------------------------------------

func TestOnlySuccessfulResponsesAreCached(t *testing.T) {
	for _, c := range []struct {
		status int
		body   string
		want   bool
	}{
		{http.StatusOK, `{"data":[]}`, true},
		{http.StatusOK, "", false},
		{http.StatusNotFound, `{"error":{}}`, false},
		{http.StatusBadRequest, `{"error":{}}`, false},
		{http.StatusInternalServerError, `{"error":{}}`, false},
	} {
		if got := cacheable(c.status, []byte(c.body)); got != c.want {
			t.Errorf("cacheable(%d, %q) = %v, want %v", c.status, c.body, got, c.want)
		}
	}
}

// ---- the middleware -------------------------------------------------------

func TestASecondIdenticalRequestIsServedFromTheCache(t *testing.T) {
	store := newMemory()
	calls := 0
	handler := serverWithCache(store).withCache(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		writeData(w, []string{"a"}, nil)
	})

	first := httptest.NewRecorder()
	handler(first, httptest.NewRequest(http.MethodGet, "/v1/indicators", nil))
	second := httptest.NewRecorder()
	handler(second, httptest.NewRequest(http.MethodGet, "/v1/indicators", nil))

	if calls != 1 {
		t.Errorf("handler ran %d times, want 1", calls)
	}
	if first.Header().Get("X-Cache") != "miss" {
		t.Errorf("first response was %q, want miss", first.Header().Get("X-Cache"))
	}
	if second.Header().Get("X-Cache") != "hit" {
		t.Errorf("second response was %q, want hit", second.Header().Get("X-Cache"))
	}
	if first.Body.String() != second.Body.String() {
		t.Errorf("cached body differs:\n %q\n %q", first.Body.String(), second.Body.String())
	}
}

// A 500 from a warehouse that was briefly unreachable must not be served for
// the rest of the TTL: that turns a blip into an outage.
func TestFailuresAreNotCached(t *testing.T) {
	store := newMemory()
	calls := 0
	handler := serverWithCache(store).withCache(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		writeError(w, http.StatusInternalServerError, CodeInternal, "boom", "")
	})

	for range 2 {
		handler(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/v1/indicators", nil))
	}
	if calls != 2 {
		t.Errorf("handler ran %d times, want 2 — a failure was cached", calls)
	}
	if store.sets != 0 {
		t.Errorf("%d entries were stored for a failing handler", store.sets)
	}
}

func TestTheStatusAndHeadersOfAMissSurvive(t *testing.T) {
	handler := serverWithCache(newMemory()).withCache(func(w http.ResponseWriter, _ *http.Request) {
		notFound(w, "no such thing", "id")
	})
	recorder := httptest.NewRecorder()
	handler(recorder, httptest.NewRequest(http.MethodGet, "/v1/indicators/nope", nil))

	if recorder.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", recorder.Code)
	}
	if got := recorder.Header().Get("Content-Type"); got != "application/json; charset=utf-8" {
		t.Errorf("Content-Type = %q", got)
	}
}

// With no Redis the server must behave exactly as it did before the cache
// existed: every request reaches the handler.
func TestWithoutACacheEveryRequestReachesTheHandler(t *testing.T) {
	calls := 0
	handler := serverWithCache(cache.Nothing{}).withCache(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		writeData(w, []string{"a"}, nil)
	})
	for range 3 {
		handler(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/v1/indicators", nil))
	}
	if calls != 3 {
		t.Errorf("handler ran %d times, want 3", calls)
	}
}
