// Package cache holds the serving layer's read-through cache.
//
// The warehouse is columnar files on disk, not a database with a buffer pool:
// every request re-opens Parquet and re-aggregates, and the aggregates the
// portal asks for — every series with its coverage, every dataset with its
// counts — cost hundreds of milliseconds each time to produce an answer that
// only changes when a pipeline runs.
//
// So the answers are cached, not the rows. Caching rows would mean keeping a
// second copy of the lake somewhere and deciding when it is wrong; caching a
// rendered response means keeping a string for a minute and being able to say
// exactly how stale it can be.
//
// Cache is optional by construction. With no REDIS_URL the API returns Nothing,
// every lookup misses, and the serving layer behaves exactly as it did before
// this package existed — a deployment without a Redis is a slower deployment,
// not a broken one.
package cache

import (
	"context"
	"time"
)

// Cache is a bytes-in, bytes-out store with an expiry.
//
// Deliberately not a typed cache: what is stored is an already-encoded JSON
// response, so the hit path does not decode and re-encode what it is about to
// write to a socket.
type Cache interface {
	// Get returns the cached value, and whether there was one. An error
	// reaching the cache is a miss: a request must not fail because an
	// optional accelerator is down.
	Get(ctx context.Context, key string) ([]byte, bool)

	// Set stores a value. Errors are dropped for the same reason, after
	// being counted — a cache that has quietly stopped accepting writes
	// looks exactly like a cache that is working, and the metrics are how
	// anyone finds out.
	Set(ctx context.Context, key string, value []byte, ttl time.Duration)

	// Purge drops every key this cache owns, for when a pipeline run has
	// made the answers wrong before their TTL is up.
	Purge(ctx context.Context) error

	// Stats reports hits, misses and errors since start.
	Stats() Stats

	Close() error
}

// Stats is what the readiness endpoint reports about the cache.
type Stats struct {
	Enabled bool   `json:"enabled"`
	Backend string `json:"backend"`
	Hits    uint64 `json:"hits"`
	Misses  uint64 `json:"misses"`
	// Failures reaching the cache. Non-zero with a healthy hit rate means a
	// flaky connection; non-zero with no hits means it is not working at all
	// and the API is quietly serving every request from Parquet.
	Errors uint64 `json:"errors"`
}

// Nothing is the cache used when none is configured. Every Get misses and
// every Set is dropped, so callers need no branch of their own.
type Nothing struct{}

func (Nothing) Get(context.Context, string) ([]byte, bool)         { return nil, false }
func (Nothing) Set(context.Context, string, []byte, time.Duration) {}
func (Nothing) Purge(context.Context) error                        { return nil }
func (Nothing) Stats() Stats                                       { return Stats{Backend: "none"} }
func (Nothing) Close() error                                       { return nil }
