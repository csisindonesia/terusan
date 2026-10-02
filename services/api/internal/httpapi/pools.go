package httpapi

import (
	"context"
	"database/sql"
	"time"
)

// poolCheckInterval is how often the pools are looked at for queueing. A
// minute is short enough that a burst shows up near the requests it slowed,
// and long enough that a quiet API writes nothing at all.
const poolCheckInterval = time.Minute

// PoolStats is what the readiness endpoint reports about one connection pool.
//
// The question it answers is whether the cap on concurrent queries
// (DUCKDB_MAX_CONNECTIONS) is ever reached. A request that waited for a
// connection looks, from the request log, exactly like a slow query; only the
// pool knows the time went to queueing rather than to the scan.
type PoolStats struct {
	// The cap. Zero means unlimited.
	MaxOpen int `json:"max_open"`
	Open    int `json:"open"`
	InUse   int `json:"in_use"`
	Idle    int `json:"idle"`
	// Since start: how many queries had to wait for a connection, and how long
	// they waited between them. Both staying at zero under real traffic means
	// the cap is never what a reader is waiting on.
	Waits  int64 `json:"waits"`
	WaitMS int64 `json:"wait_ms"`
}

func poolStatsOf(db *sql.DB) PoolStats {
	stats := db.Stats()
	return PoolStats{
		MaxOpen: stats.MaxOpenConnections,
		Open:    stats.OpenConnections,
		InUse:   stats.InUse,
		Idle:    stats.Idle,
		Waits:   stats.WaitCount,
		WaitMS:  stats.WaitDuration.Milliseconds(),
	}
}

// WatchPool adds a pool to the readiness report and to the queueing log.
//
// The warehouse is watched from New; this is for the application database,
// which main opens and hands to the stores rather than to the server.
func (s *Server) WatchPool(name string, db *sql.DB) {
	if db == nil {
		return
	}
	s.pools[name] = db
}

func (s *Server) poolReport() map[string]PoolStats {
	report := make(map[string]PoolStats, len(s.pools))
	for name, db := range s.pools {
		report[name] = poolStatsOf(db)
	}
	return report
}

// MonitorPools logs a pool whenever queries had to wait for one of its
// connections since the last look, and says nothing otherwise.
//
// Logged rather than only reported on /readyz, because the readiness answer is
// a running total since start: it says the cap was reached at some point, not
// when, and the when is what lines a queue up against the requests it slowed.
func (s *Server) MonitorPools(ctx context.Context) {
	last := make(map[string]PoolStats, len(s.pools))
	for name, db := range s.pools {
		last[name] = poolStatsOf(db)
	}

	ticker := time.NewTicker(poolCheckInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.logQueued(last)
		}
	}
}

// logQueued logs each pool whose wait count has grown since last, and moves
// last on to the pools' current state.
func (s *Server) logQueued(last map[string]PoolStats) {
	for name, db := range s.pools {
		now := poolStatsOf(db)
		before := last[name]
		last[name] = now
		if now.Waits == before.Waits {
			continue
		}
		s.log.Warn("pool.queued",
			"pool", name,
			"waits", now.Waits-before.Waits,
			"wait_ms", now.WaitMS-before.WaitMS,
			"in_use", now.InUse,
			"max_open", now.MaxOpen,
		)
	}
}
