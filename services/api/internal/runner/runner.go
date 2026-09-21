// Package runner starts pipeline commands on behalf of the portal and keeps
// what they printed (program.md §39).
//
// The serving layer is a read-only view of the lake, and this is the one
// deliberate exception: it writes nothing itself, it asks the pipelines to.
// "Run ingestion now" is an operational button, not a data edit — the figures
// it produces still arrive the only way figures ever arrive here, by a
// pipeline landing them in RAW and the journal recording the run.
//
// Because it does start a process, it is off unless switched on:
//
//   - PIPELINES_ENABLED=true, which nothing sets by default.
//   - The program is fixed (`uv run terusan …`) and assembled as argv. No
//     shell is involved, so nothing a caller sends can become a command.
//   - The one caller-supplied word, the source slug, is checked against the
//     registry in the lake before it is passed. An unknown slug is a 404, not
//     an argument.
//   - One run per source at a time, and a small ceiling on runs at once, so a
//     repeated click cannot fork a hundred scrapers at a publisher.
//
// A job lives in this process's memory and dies with it. That is the right
// lifetime: the durable record of a run is the journal the pipeline itself
// writes, and this only exists to show progress while the pipeline is still
// deciding what to write.
package runner

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

// Status is where a job is in its life.
//
// The same words the journal uses for a finished run, so a reader is not asked
// to learn two vocabularies for one thing.
type Status string

const (
	StatusRunning   Status = "running"
	StatusSucceeded Status = "succeeded"
	StatusFailed    Status = "failed"
)

// Errors a caller has to tell apart, because each is a different answer to the
// browser: switched off, already going, too many at once, nothing to run.
var (
	ErrDisabled   = errors.New("running pipelines from the serving layer is switched off")
	ErrBusy       = errors.New("that source is already being ingested")
	ErrAtCapacity = errors.New("too many pipeline runs are already going")
	ErrNoSuchJob  = errors.New("no such job")
)

// Config is what the runner needs to start anything.
type Config struct {
	// Off unless explicitly switched on. Every other field is ignored while
	// this is false.
	Enabled bool
	// The repository root. Commands run in <Root>/pipelines, which is where
	// the uv project lives.
	Root string
	// The uv binary. A name is looked up on PATH; a path is used as given.
	UV string
	// How long a run may take before it is killed. A scraper that hangs on a
	// publisher's socket would otherwise hold a slot forever.
	Timeout time.Duration
	// How many runs may go at once, across every source.
	MaxConcurrent int
	// How many lines of a run's output to keep. The tail, not the whole of it:
	// this is a progress display, and the durable record is the journal.
	MaxOutputLines int
	// How many finished jobs to remember before the oldest is dropped.
	History int
}

// Job is one pipeline command this process started, as the API reports it.
type Job struct {
	JobID string `json:"job_id"`
	// `ingest` today. The same machinery carries extraction and normalization
	// the day either gets a button.
	Kind string `json:"kind"`
	// What it runs against — a source slug, for an ingestion.
	Target string `json:"target"`
	// The series whose page asked for the run. Carried so a page can find its
	// own job again after a reload, without the browser having stored anything.
	IndicatorID string `json:"indicator_id,omitempty"`

	Status Status `json:"status"`
	// The exact argv, joined for reading. What someone would paste into a
	// terminal to do the same thing by hand — which is what the menu beside
	// this button offers to copy.
	Command   string     `json:"command"`
	StartedAt time.Time  `json:"started_at"`
	EndedAt   *time.Time `json:"finished_at,omitempty"`
	// Wall-clock so far for a running job, and final for a finished one, so a
	// caller renders one field either way.
	Seconds  float64 `json:"duration_seconds"`
	ExitCode *int    `json:"exit_code,omitempty"`
	// Why it failed, in the terms this process knows: a non-zero exit, a
	// timeout, or a command that could not be started at all.
	Error string `json:"error,omitempty"`
	// The tail of what the command printed, oldest first. stdout and stderr
	// interleaved, because the pipelines log to stderr and print their summary
	// to stdout, and reading either alone tells half the story.
	Output []string `json:"output"`
}

// Options are the parts of a run a caller may choose.
type Options struct {
	// Fetch without landing anything. The journal records the run with a
	// `dry_run` flag, so its zero records are not read as a broken scraper.
	DryRun bool
	// Stop after this many artifacts. Zero means the source's own idea of a
	// full run.
	Limit int
	// The series whose page asked, remembered on the job.
	IndicatorID string
}

// Runner owns every job this process has started.
type Runner struct {
	cfg Config
	log *slog.Logger
	// Called once a run has finished, whatever it did. Whatever the API has
	// cached was computed before a pipeline touched the lake, and a reader who
	// just watched an ingestion finish should not then be served a minute of
	// pre-ingestion answers.
	onFinish func()

	mu      sync.Mutex
	jobs    map[string]*Job
	order   []string          // job ids, oldest first
	busy    map[string]string // target -> the job id currently running it
	running int
}

// New returns a runner over cfg. `onFinish` may be nil.
func New(cfg Config, log *slog.Logger, onFinish func()) *Runner {
	return &Runner{
		cfg:      cfg,
		log:      log,
		onFinish: onFinish,
		jobs:     make(map[string]*Job),
		busy:     make(map[string]string),
	}
}

// Enabled reports whether this runner will start anything.
func (r *Runner) Enabled() bool { return r != nil && r.cfg.Enabled }

// StartIngest runs one source and returns the job as it stands at that moment.
//
// It returns as soon as the process is running rather than when it finishes:
// an ingestion takes minutes, and a request held open for minutes is a request
// that dies to a proxy timeout with the run still going and nobody watching.
func (r *Runner) StartIngest(target string, opts Options) (Job, error) {
	if !r.Enabled() {
		return Job{}, ErrDisabled
	}

	args := []string{"run", "terusan", "sources", "run", target, "--trigger", "portal"}
	if opts.DryRun {
		args = append(args, "--dry-run")
	}
	if opts.Limit > 0 {
		args = append(args, "--limit", strconv.Itoa(opts.Limit))
	}

	return r.start("ingest", target, opts.IndicatorID, args)
}

// Get returns one job.
func (r *Runner) Get(id string) (Job, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	job, ok := r.jobs[id]
	if !ok {
		return Job{}, ErrNoSuchJob
	}
	return r.snapshot(job), nil
}

// List returns every job this process remembers, newest first.
//
// `target` narrows to one source, which is how an indicator page asks "is
// anything running for me" without having kept a job id across a reload.
func (r *Runner) List(target string) []Job {
	r.mu.Lock()
	defer r.mu.Unlock()

	jobs := make([]Job, 0, len(r.order))
	for i := len(r.order) - 1; i >= 0; i-- {
		job := r.jobs[r.order[i]]
		if job == nil || (target != "" && job.Target != target) {
			continue
		}
		jobs = append(jobs, r.snapshot(job))
	}
	return jobs
}

// start launches one command and records it.
func (r *Runner) start(kind, target, indicator string, args []string) (Job, error) {
	r.mu.Lock()
	if _, going := r.busy[target]; going {
		r.mu.Unlock()
		return Job{}, ErrBusy
	}
	if r.running >= r.cfg.MaxConcurrent {
		r.mu.Unlock()
		return Job{}, ErrAtCapacity
	}

	job := &Job{
		JobID:       uuid.NewString(),
		Kind:        kind,
		Target:      target,
		IndicatorID: indicator,
		Status:      StatusRunning,
		Command:     strings.Join(append([]string{r.cfg.UV}, args...), " "),
		StartedAt:   time.Now().UTC(),
		Output:      []string{},
	}
	r.jobs[job.JobID] = job
	r.order = append(r.order, job.JobID)
	r.busy[target] = job.JobID
	r.running++
	r.forget()
	started := r.snapshot(job)
	r.mu.Unlock()

	if err := r.spawn(job, args); err != nil {
		// Nothing is running, so the slot has to go back now rather than when
		// a wait that will never happen returns.
		r.finish(job, nil, err)
		return r.snapshot(job), nil
	}
	return started, nil
}

// spawn starts the process and the goroutine that waits for it.
func (r *Runner) spawn(job *Job, args []string) error {
	ctx, cancel := context.WithTimeout(context.Background(), r.cfg.Timeout)

	cmd := exec.CommandContext(ctx, r.cfg.UV, args...)
	cmd.Dir = filepath.Join(r.cfg.Root, "pipelines")
	// The API's own environment, unchanged. STORAGE_ROOT and the source
	// credentials are read from it by both sides, and a pipeline started here
	// must land in the same lake this process is serving — quietly writing to
	// a different one is the failure nobody would think to look for.
	cmd.Env = os.Environ()
	// Kill the whole process group on timeout. `uv run` execs Python as a
	// child, and killing only uv leaves the scraper running and holding the
	// publisher's socket.
	detach(cmd)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		cancel()
		return err
	}

	if err := cmd.Start(); err != nil {
		cancel()
		return fmt.Errorf("start %s: %w", r.cfg.UV, err)
	}
	r.log.Info("pipeline.started",
		"job", job.JobID, "kind", job.Kind, "target", job.Target, "command", job.Command)

	var streams sync.WaitGroup
	streams.Add(2)
	go func() { defer streams.Done(); r.drain(job, stdout) }()
	go func() { defer streams.Done(); r.drain(job, stderr) }()

	go func() {
		defer cancel()
		// Both pipes to EOF before the wait, or Wait closes them under the
		// readers and the last lines of a failing run — the ones worth having
		// — are lost.
		streams.Wait()
		waitErr := cmd.Wait()

		var code *int
		if cmd.ProcessState != nil {
			exit := cmd.ProcessState.ExitCode()
			code = &exit
		}
		if ctx.Err() != nil {
			waitErr = fmt.Errorf("the run was still going after %s and was stopped", r.cfg.Timeout)
		}
		r.finish(job, code, waitErr)
	}()

	return nil
}

// drain copies one stream into the job's output tail.
func (r *Runner) drain(job *Job, stream io.Reader) {
	scanner := bufio.NewScanner(stream)
	// A single log line from a scraper can carry a whole HTML fragment, and
	// the default 64KB would end the scan rather than truncate the line.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		r.append(job, scanner.Text())
	}
	if err := scanner.Err(); err != nil {
		r.append(job, "[output truncated: "+err.Error()+"]")
	}
}

// Past this a line is cut. A stack trace's frames are worth keeping; a
// base64-encoded PDF that reached the log is not.
const maxLineLength = 2000

func (r *Runner) append(job *Job, line string) {
	if len(line) > maxLineLength {
		line = line[:maxLineLength] + "…"
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	job.Output = append(job.Output, line)
	if over := len(job.Output) - r.cfg.MaxOutputLines; over > 0 {
		job.Output = append(job.Output[:0], job.Output[over:]...)
	}
}

// finish records how a run ended and frees its slot.
func (r *Runner) finish(job *Job, code *int, err error) {
	r.mu.Lock()
	ended := time.Now().UTC()
	job.EndedAt = &ended
	job.Seconds = ended.Sub(job.StartedAt).Seconds()
	job.ExitCode = code
	if err != nil {
		job.Status = StatusFailed
		job.Error = err.Error()
	} else {
		job.Status = StatusSucceeded
	}
	if r.busy[job.Target] == job.JobID {
		delete(r.busy, job.Target)
	}
	if r.running > 0 {
		r.running--
	}
	r.mu.Unlock()

	r.log.Info("pipeline.finished",
		"job", job.JobID, "target", job.Target,
		"status", string(job.Status), "seconds", job.Seconds, "error", job.Error)

	if r.onFinish != nil {
		r.onFinish()
	}
}

// forget drops the oldest finished jobs once there are more than History.
//
// Running jobs are never dropped: the browser is holding their ids.
// Called with the lock held.
func (r *Runner) forget() {
	for len(r.order) > r.cfg.History {
		var kept []string
		dropped := false
		for _, id := range r.order {
			if !dropped && r.jobs[id] != nil && r.jobs[id].Status != StatusRunning {
				delete(r.jobs, id)
				dropped = true
				continue
			}
			kept = append(kept, id)
		}
		r.order = kept
		if !dropped {
			return // everything remembered is still running
		}
	}
}

// snapshot copies a job for handing outside the lock.
//
// The output slice is copied too: the caller marshals it while the process it
// belongs to may still be appending. Called with the lock held.
func (r *Runner) snapshot(job *Job) Job {
	copied := *job
	copied.Output = append([]string{}, job.Output...)
	if job.Status == StatusRunning {
		copied.Seconds = time.Since(job.StartedAt).Seconds()
	}
	return copied
}
