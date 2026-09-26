package httpapi

import (
	"context"
	"errors"
	"net/http"
	"regexp"

	"github.com/csis/terusan/services/api/internal/runner"
	"github.com/csis/terusan/services/api/internal/storage"
)

// The serving layer's one write surface: asking a pipeline to run.
//
// It writes nothing itself. A figure still arrives the only way a figure ever
// arrives here — a scraper lands it in RAW, extraction reads it into Bronze, a
// normalization publishes it into Silver — and the durable record of the run
// is the journal the pipeline writes, which `/v1/runs` already serves. What
// this adds is the ability to start one, and something to watch while it goes.
//
// Everything that makes that safe is in internal/runner. The one thing that
// belongs here is the allow-list: the slug in the path is checked against the
// source registry in the lake before it reaches a command line.

// A source slug, as the registry writes them. Narrower than identifierPattern,
// which allows the dots and colons an indicator id carries: this word becomes
// an argument to a program, and the smallest alphabet that fits is the right
// one.
var slugPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,80}$`)

// Capabilities is what this deployment will do, beyond answering questions.
//
// Served so the portal can offer a working button instead of a disabled one
// with an explanation, or the other way round, without guessing from a 403.
type Capabilities struct {
	// Whether `POST /v1/sources/{id}/run` will start anything.
	RunPipelines bool `json:"run_pipelines"`
	// Whether this deployment keeps collections and saved queries, which is
	// what decides whether a folder has a URL other people can open or lives
	// in one browser.
	Collections bool `json:"collections"`
	// Whether that shelf accepts changes. False on a deployment serving a
	// curated shelf read-only — a session says who is reading but does not yet
	// scope what they may change (program.md §34), so "who may edit" is a
	// deployment setting rather than a permission.
	CollectionsWrite bool `json:"collections_write"`
	// Whether this deployment has accounts at all, which is what tells the
	// portal to show a login page rather than going straight in.
	Auth bool `json:"auth"`
	// Whether a session is needed to read anything. False on the public
	// warehouse this is usually deployed as.
	AuthRequired bool `json:"auth_required"`
	// Whether the login page can offer "request access".
	Registration bool `json:"registration"`
	// Whether readers can ask for a source to be collected, which needs
	// somewhere to keep the queue.
	Suggestions bool `json:"suggestions"`
	// Whether `POST /v1/assistant/chat` has a model behind it.
	Assistant bool `json:"assistant"`
	// Whether this deployment keeps the assistant's conversations, so a chat
	// has a URL that opens anywhere. Without it they live in the browser.
	AssistantHistory bool `json:"assistant_history"`
}

func (s *Server) handleCapabilities(w http.ResponseWriter, _ *http.Request) {
	writeData(w, Capabilities{
		RunPipelines:     s.runner.Enabled(),
		Collections:      s.shelf != nil,
		CollectionsWrite: s.shelf.Writable(),
		Auth:             s.auth != nil,
		AuthRequired:     s.auth != nil && s.cfg.Auth.Required,
		Registration:     s.auth != nil && s.cfg.Auth.Registration,
		Suggestions:      s.suggestions != nil,
		Assistant:        s.cfg.Assistant.Enabled(),
		AssistantHistory: s.cfg.Assistant.Enabled() && s.chats != nil,
	}, nil)
}

// handleRunSource starts an ingestion of one source.
//
// Answers 202 with the job rather than waiting: an ingestion takes minutes, and
// a request held open that long dies to a proxy timeout with the run still
// going and nobody watching it.
func (s *Server) handleRunSource(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	if !s.runner.Enabled() {
		writeError(w, http.StatusForbidden, CodeForbidden,
			"this serving layer does not run pipelines",
			"set PIPELINES_ENABLED=true where the API runs, on a host that has the "+
				"pipelines checked out; until then run it from a terminal")
		return
	}

	id := r.PathValue("id")
	if !slugPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed source slug")
		return
	}
	// The allow-list. A slug the registry does not carry never reaches a
	// command line — and a caller who mistyped one gets told so rather than
	// watching a run fail three minutes later.
	known, err := s.knowsSource(ctx, id)
	if err != nil {
		internalError(w, s.log, "resolve source", err)
		return
	}
	if !known {
		notFound(w, "no such source", "id: "+id+" is not in the registry")
		return
	}

	dryRun, err := boolParam(r, "dry_run")
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	limit, err := intParam(r, "limit", 0, 0, 100_000)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}
	// Optional, and only ever recorded on the job: it is what lets an
	// indicator page find the run it started after a reload.
	indicator, err := stringParam(r, "indicator", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	job, err := s.runner.StartIngest(id, runner.Options{
		DryRun:      dryRun,
		Limit:       limit,
		IndicatorID: indicator,
	})
	switch {
	case errors.Is(err, runner.ErrBusy):
		writeError(w, http.StatusConflict, CodeConflict,
			"that source is already being ingested",
			"wait for the run in progress to finish")
		return
	case errors.Is(err, runner.ErrAtCapacity):
		writeError(w, http.StatusServiceUnavailable, CodeUnavailable,
			"too many pipeline runs are already going",
			"wait for one to finish, or raise PIPELINES_MAX_CONCURRENT")
		return
	case err != nil:
		internalError(w, s.log, "start ingestion", err)
		return
	}

	writeJSON(w, http.StatusAccepted, Response[runner.Job]{Data: job})
}

// handleJobs lists what this process has started, newest first.
//
// This process, not this lake: a job is a live view of a running command and
// dies with the server that started it. The history that outlives a restart is
// the journal, under `/v1/runs`.
func (s *Server) handleJobs(w http.ResponseWriter, r *http.Request) {
	if !s.runner.Enabled() {
		writeData(w, []runner.Job{}, &Meta{Total: 0})
		return
	}

	target, err := stringParam(r, "source", slugPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return
	}

	jobs := s.runner.List(target)
	// Without their output. Fifty remembered jobs carrying five hundred lines
	// each is a megabyte of scraper logging in answer to "is anything running",
	// and the one job a caller then watches is fetched by id anyway.
	for i := range jobs {
		jobs[i].Output = []string{}
	}
	writeData(w, jobs, &Meta{Total: int64(len(jobs))})
}

// handleJob serves one job, output and all. What the portal polls.
func (s *Server) handleJob(w http.ResponseWriter, r *http.Request) {
	job, err := s.runner.Get(r.PathValue("id"))
	if errors.Is(err, runner.ErrNoSuchJob) {
		notFound(w, "no such job",
			"a job lives only as long as the server that started it; "+
				"the run itself is in /v1/runs once it finishes")
		return
	}
	if err != nil {
		internalError(w, s.log, "read job", err)
		return
	}
	writeData(w, job, nil)
}

// knowsSource reports whether the lake has heard of this slug.
//
// The registry first, which is the authority: `terusan silver dimensions`
// publishes it and it names every source whether or not one has ever run.
// Where it has not been published, the sources named on the observations do
// instead — a narrower set, and still a closed one read out of the lake, which
// is the property that matters. What must never happen is falling through to
// "no list, so allow it": that is how a word in a URL becomes an argument to a
// program.
func (s *Server) knowsSource(ctx context.Context, id string) (bool, error) {
	for _, candidate := range []struct {
		dataset string
		column  string
	}{
		{"sources", "source_id"},
		{"observations", "source_id"},
	} {
		if !s.warehouse.Exists(ctx, storage.LayerSilver, candidate.dataset) {
			continue
		}

		expression, err := s.source(storage.LayerSilver, candidate.dataset)
		if err != nil {
			return false, err
		}

		var count int64
		if err := s.warehouse.DB().QueryRowContext(ctx,
			"SELECT count(*) FROM "+expression+" WHERE "+candidate.column+" = ? LIMIT 1", id,
		).Scan(&count); err != nil {
			return false, err
		}
		if count > 0 {
			return true, nil
		}
	}
	return false, nil
}
