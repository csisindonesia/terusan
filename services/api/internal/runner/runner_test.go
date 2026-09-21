package runner

import (
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// newTestRunner returns a runner whose "uv" is `program`, over a directory
// shaped like the repository — the runner runs commands in <root>/pipelines,
// and a missing working directory fails the command rather than the test.
func newTestRunner(t *testing.T, program string) *Runner {
	t.Helper()

	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "pipelines"), 0o755); err != nil {
		t.Fatal(err)
	}

	return New(Config{
		Enabled:        true,
		Root:           root,
		UV:             program,
		Timeout:        10 * time.Second,
		MaxConcurrent:  2,
		MaxOutputLines: 100,
		History:        10,
	}, slog.New(slog.NewTextHandler(io.Discard, nil)), nil)
}

// script writes an executable shell script and returns its path.
func script(t *testing.T, body string) string {
	t.Helper()

	path := filepath.Join(t.TempDir(), "fake-uv")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// await polls until the job stops running, or the test times out.
func await(t *testing.T, r *Runner, id string) Job {
	t.Helper()

	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		job, err := r.Get(id)
		if err != nil {
			t.Fatal(err)
		}
		if job.Status != StatusRunning {
			return job
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("job %s was still running after 10s", id)
	return Job{}
}

func TestDisabledRunnerStartsNothing(t *testing.T) {
	r := New(Config{}, slog.New(slog.NewTextHandler(io.Discard, nil)), nil)

	if r.Enabled() {
		t.Fatal("a zero config must not be enabled")
	}
	if _, err := r.StartIngest("bps-inflation", Options{}); !errors.Is(err, ErrDisabled) {
		t.Fatalf("want ErrDisabled, got %v", err)
	}
}

func TestSuccessfulRunKeepsItsOutput(t *testing.T) {
	r := newTestRunner(t, script(t, `echo "landed 3"; echo "to stderr" >&2`))

	started, err := r.StartIngest("bps-inflation", Options{})
	if err != nil {
		t.Fatal(err)
	}

	finished := await(t, r, started.JobID)
	if finished.Status != StatusSucceeded {
		t.Fatalf("status = %q, error = %q", finished.Status, finished.Error)
	}
	if finished.ExitCode == nil || *finished.ExitCode != 0 {
		t.Fatalf("exit code = %v", finished.ExitCode)
	}

	// Both streams, because a scraper logs to stderr and prints its summary to
	// stdout, and either one alone tells half the story.
	printed := strings.Join(finished.Output, "\n")
	for _, want := range []string{"landed 3", "to stderr"} {
		if !strings.Contains(printed, want) {
			t.Errorf("output does not carry %q:\n%s", want, printed)
		}
	}
}

func TestFailedRunIsRecordedAsSuch(t *testing.T) {
	r := newTestRunner(t, script(t, `echo "the layout changed" >&2; exit 2`))

	started, err := r.StartIngest("bps-inflation", Options{})
	if err != nil {
		t.Fatal(err)
	}

	finished := await(t, r, started.JobID)
	if finished.Status != StatusFailed {
		t.Fatalf("status = %q, want failed", finished.Status)
	}
	if finished.ExitCode == nil || *finished.ExitCode != 2 {
		t.Fatalf("exit code = %v, want 2", finished.ExitCode)
	}
	if finished.Error == "" {
		t.Error("a failed run must say why")
	}
}

// One source, one run at a time. A repeated click must not fork two scrapers
// at the same publisher.
func TestSecondRunOfOneSourceIsRefused(t *testing.T) {
	r := newTestRunner(t, script(t, "sleep 5"))

	started, err := r.StartIngest("bps-inflation", Options{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.StartIngest("bps-inflation", Options{}); !errors.Is(err, ErrBusy) {
		t.Fatalf("want ErrBusy, got %v", err)
	}
	// A different source is fine: the bound is per source, not global.
	if _, err := r.StartIngest("worldbank-gdp", Options{}); err != nil {
		t.Fatalf("a second source should start: %v", err)
	}
	// And a third is not, because MaxConcurrent is 2.
	if _, err := r.StartIngest("sipri-milex", Options{}); !errors.Is(err, ErrAtCapacity) {
		t.Fatalf("want ErrAtCapacity, got %v", err)
	}

	if _, err := r.Get(started.JobID); err != nil {
		t.Fatal(err)
	}
}

func TestCommandCarriesTheOptions(t *testing.T) {
	r := newTestRunner(t, script(t, "true"))

	started, err := r.StartIngest("bps-inflation", Options{DryRun: true, Limit: 5})
	if err != nil {
		t.Fatal(err)
	}

	want := "run terusan sources run bps-inflation --trigger portal --dry-run --limit 5"
	if !strings.HasSuffix(started.Command, want) {
		t.Errorf("command = %q, want it to end in %q", started.Command, want)
	}
	await(t, r, started.JobID)
}

// A run that hangs is stopped rather than holding its slot forever.
func TestHungRunIsStopped(t *testing.T) {
	r := newTestRunner(t, script(t, "sleep 30"))
	r.cfg.Timeout = 200 * time.Millisecond

	started, err := r.StartIngest("bps-inflation", Options{})
	if err != nil {
		t.Fatal(err)
	}

	finished := await(t, r, started.JobID)
	if finished.Status != StatusFailed {
		t.Fatalf("status = %q, want failed", finished.Status)
	}
	if !strings.Contains(finished.Error, "still going") {
		t.Errorf("error = %q, want it to say the run was stopped", finished.Error)
	}
	// The slot is back: the next run of the same source starts.
	if _, err := r.StartIngest("bps-inflation", Options{}); err != nil {
		t.Fatalf("the slot was not released: %v", err)
	}
}

func TestListNarrowsToOneSource(t *testing.T) {
	r := newTestRunner(t, script(t, "true"))

	first, err := r.StartIngest("bps-inflation", Options{})
	if err != nil {
		t.Fatal(err)
	}
	await(t, r, first.JobID)
	second, err := r.StartIngest("worldbank-gdp", Options{})
	if err != nil {
		t.Fatal(err)
	}
	await(t, r, second.JobID)

	if all := r.List(""); len(all) != 2 {
		t.Fatalf("got %d jobs, want 2", len(all))
	}
	// Newest first, so a page reading `[0]` adopts the run that is going now
	// rather than one from an hour ago.
	one := r.List("bps-inflation")
	if len(one) != 1 || one[0].JobID != first.JobID {
		t.Fatalf("narrowing to one source returned %d jobs", len(one))
	}
	if r.List("")[0].JobID != second.JobID {
		t.Error("jobs are not newest first")
	}
}
