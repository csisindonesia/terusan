package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/url"
	"sort"
	"strings"
)

// Read-through caching for the handlers whose answers are expensive to derive
// and change only when a pipeline runs.
//
// Applied as a wrapper around a handler rather than inside each one, so a
// handler stays a function from a request to rows and does not grow a cache
// branch, a key, and a second way of writing its response.

// cacheable is a response worth keeping: a 200 with a body.
//
// Errors are never cached. A 500 from a warehouse that was briefly unreachable
// would otherwise be served for the rest of the TTL, turning a blip into an
// outage; and a 400 is cheap to produce again anyway.
func cacheable(status int, body []byte) bool {
	return status == http.StatusOK && len(body) > 0
}

// recorder captures a handler's response so it can be cached before it is
// sent. Nothing is streamed through: these are JSON documents of at most a few
// megabytes, and the file endpoint — the one response that must stream — is
// not cached.
type recorder struct {
	header http.Header
	status int
	body   bytes.Buffer
}

func (r *recorder) Header() http.Header { return r.header }

func (r *recorder) WriteHeader(status int) {
	if r.status == 0 {
		r.status = status
	}
}

func (r *recorder) Write(p []byte) (int, error) {
	if r.status == 0 {
		r.status = http.StatusOK
	}
	return r.body.Write(p)
}

// cacheKey identifies a response by what was asked for.
//
// The query is canonicalised — parameters sorted, repeats kept in order — so
// `?limit=10&order=title` and `?order=title&limit=10` are one entry rather
// than two. Hashed because a regulations query with six filters makes a key
// longer than the value it points at, and Redis keys are held in memory.
func cacheKey(r *http.Request) string {
	values := r.URL.Query()
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	sort.Strings(names)

	canonical := make(url.Values, len(values))
	for _, name := range names {
		canonical[name] = values[name]
	}

	sum := sha256.Sum256([]byte(canonical.Encode()))
	// The path stays readable in the key so that a human looking at Redis can
	// see what is cached, and so a future purge can narrow by endpoint.
	return strings.TrimPrefix(r.URL.Path, "/") + ":" + hex.EncodeToString(sum[:8])
}

// withCache serves `next` from the cache when it can, and fills the cache when
// it cannot.
//
// A miss runs the handler in full and stores what it produced. There is
// deliberately no single-flight: two identical requests arriving during a cold
// miss both run the query, which wastes one query and keeps this
// straightforward. Adding coalescing would matter under a thundering herd this
// API does not have.
func (s *Server) withCache(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key := cacheKey(r)

		if body, ok := s.cache.Get(r.Context(), key); ok {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			// Said in a header rather than only in the logs: when a figure
			// looks stale, the first question is whether it came from the
			// cache, and this answers it from the browser's network tab.
			w.Header().Set("X-Cache", "hit")
			w.WriteHeader(http.StatusOK)
			w.Write(body)
			return
		}

		recorded := &recorder{header: w.Header().Clone()}
		next(recorded, r)

		body := recorded.body.Bytes()
		if cacheable(recorded.status, body) {
			s.cache.Set(r.Context(), key, body, s.cfg.CacheTTL)
		}

		for name, values := range recorded.header {
			w.Header()[name] = values
		}
		w.Header().Set("X-Cache", "miss")
		if recorded.status == 0 {
			recorded.status = http.StatusOK
		}
		w.WriteHeader(recorded.status)
		w.Write(body)
	}
}
