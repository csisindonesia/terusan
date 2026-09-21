package cache

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// Namespace prefixes every key this process writes, so a Redis shared with
// something else stays separable and `Purge` can drop our keys alone.
const Namespace = "terusan:v1:"

// Timeout bounds a cache round trip.
//
// Short on purpose. The cache exists to make a slow answer fast; waiting on it
// longer than the query it is standing in for would make it a liability. A
// Redis that cannot answer in this long is treated as absent.
const Timeout = 250 * time.Millisecond

// Redis is a Cache backed by a Redis server.
type Redis struct {
	client *redis.Client
	log    *slog.Logger

	hits   atomic.Uint64
	misses atomic.Uint64
	errors atomic.Uint64
}

// Open connects to the Redis at url and verifies it answers.
//
// It pings rather than connecting lazily: a typo in REDIS_URL should be a
// refusal at startup, where somebody is looking, and not a silent miss on
// every request afterwards.
func Open(url string, log *slog.Logger) (*Redis, error) {
	options, err := redis.ParseURL(url)
	if err != nil {
		return nil, fmt.Errorf("parse REDIS_URL: %w", err)
	}
	// The pool is bounded by the same reasoning as the timeout: a serving
	// layer that queues on the cache is worse than one without it.
	options.ReadTimeout = Timeout
	options.WriteTimeout = Timeout
	options.DialTimeout = Timeout

	client := redis.NewClient(options)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		client.Close()
		return nil, fmt.Errorf("reach redis at %s: %w", options.Addr, err)
	}
	return &Redis{client: client, log: log}, nil
}

func (r *Redis) Get(ctx context.Context, key string) ([]byte, bool) {
	ctx, cancel := context.WithTimeout(ctx, Timeout)
	defer cancel()

	value, err := r.client.Get(ctx, Namespace+key).Bytes()
	switch {
	case err == nil:
		r.hits.Add(1)
		return value, true
	case errors.Is(err, redis.Nil):
		r.misses.Add(1)
		return nil, false
	default:
		// A miss, not a failure: the request has a warehouse behind it and
		// can answer without us.
		r.errors.Add(1)
		r.log.Warn("cache.get_failed", "key", key, "error", err)
		return nil, false
	}
}

func (r *Redis) Set(ctx context.Context, key string, value []byte, ttl time.Duration) {
	ctx, cancel := context.WithTimeout(ctx, Timeout)
	defer cancel()

	if err := r.client.Set(ctx, Namespace+key, value, ttl).Err(); err != nil {
		r.errors.Add(1)
		r.log.Warn("cache.set_failed", "key", key, "error", err)
	}
}

// Purge drops this namespace's keys.
//
// SCAN plus UNLINK rather than FLUSHDB: the Redis may not be ours alone, and
// dropping somebody else's keys to invalidate our own would be a rude way to
// find that out. UNLINK frees them off the main thread.
func (r *Redis) Purge(ctx context.Context) error {
	var cursor uint64
	for {
		keys, next, err := r.client.Scan(ctx, cursor, Namespace+"*", 256).Result()
		if err != nil {
			return fmt.Errorf("scan cache keys: %w", err)
		}
		if len(keys) > 0 {
			if err := r.client.Unlink(ctx, keys...).Err(); err != nil {
				return fmt.Errorf("drop cache keys: %w", err)
			}
		}
		if next == 0 {
			return nil
		}
		cursor = next
	}
}

func (r *Redis) Stats() Stats {
	return Stats{
		Enabled: true,
		Backend: "redis",
		Hits:    r.hits.Load(),
		Misses:  r.misses.Load(),
		Errors:  r.errors.Load(),
	}
}

func (r *Redis) Close() error { return r.client.Close() }
