package httpapi

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"log/slog"
	"testing"
	"time"
)

// queueOnce makes one query wait for a pool capped at one connection: the
// connection is held, a second query is started, and the hold is released
// once the pool reports that query waiting.
func queueOnce(t *testing.T, db *sql.DB) {
	t.Helper()
	ctx := context.Background()
	held, err := db.Conn(ctx)
	if err != nil {
		t.Fatal(err)
	}

	done := make(chan error, 1)
	go func() {
		var one int
		done <- db.QueryRowContext(ctx, "SELECT 1").Scan(&one)
	}()

	deadline := time.Now().Add(5 * time.Second)
	for db.Stats().WaitCount == 0 {
		if time.Now().After(deadline) {
			t.Fatal("the second query never waited for the held connection")
		}
		time.Sleep(time.Millisecond)
	}
	held.Close()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func cappedPool(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	db.SetMaxOpenConns(1)
	return db
}

func TestLogQueuedSaysNothingUntilAQueryWaits(t *testing.T) {
	db := cappedPool(t)
	var logged bytes.Buffer
	s := &Server{
		pools: map[string]*sql.DB{"warehouse": db},
		log:   slog.New(slog.NewJSONHandler(&logged, nil)),
	}
	last := map[string]PoolStats{"warehouse": poolStatsOf(db)}

	// Queries that found a free connection are not queueing, however many.
	for range 3 {
		if _, err := db.Exec("SELECT 1"); err != nil {
			t.Fatal(err)
		}
	}
	s.logQueued(last)
	if logged.Len() != 0 {
		t.Fatalf("logged with nothing waiting: %s", logged.String())
	}

	queueOnce(t, db)
	s.logQueued(last)
	var entry map[string]any
	if err := json.Unmarshal(logged.Bytes(), &entry); err != nil {
		t.Fatalf("one log line expected, got %q: %v", logged.String(), err)
	}
	if entry["msg"] != "pool.queued" || entry["pool"] != "warehouse" || entry["waits"] != float64(1) {
		t.Fatalf("unexpected entry: %v", entry)
	}

	// The same wait is not reported twice.
	logged.Reset()
	s.logQueued(last)
	if logged.Len() != 0 {
		t.Fatalf("reported the same wait again: %s", logged.String())
	}
}

func TestPoolReportCarriesEveryWatchedPool(t *testing.T) {
	db := cappedPool(t)
	queueOnce(t, db)

	s := &Server{pools: map[string]*sql.DB{}}
	s.WatchPool("app", db)
	// An application database that is not configured is not a pool.
	s.WatchPool("missing", nil)

	report := s.poolReport()
	if len(report) != 1 {
		t.Fatalf("report = %+v", report)
	}
	if app := report["app"]; app.MaxOpen != 1 || app.Waits != 1 || app.InUse != 0 {
		t.Fatalf("app = %+v", app)
	}
}
