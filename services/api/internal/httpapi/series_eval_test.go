package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// The series search, measured against the real catalogue.
//
// Every case in testdata/series_eval.json is a question a reader asked and
// was answered wrongly, with what the model should have been shown. The
// question is read and ranked the way the assistant does, without the
// router, which is what a turn gets when the router is down. It skips where
// the lake has no Silver observations, CI included:
//
//	make eval-assistant

type seriesEvalCase struct {
	Ask    []string `json:"ask"`
	Want   []string `json:"want"`
	First  []string `json:"first"`
	Never  []string `json:"never"`
	Prompt []string `json:"prompt"`
}

func TestSeriesSearchAgainstTheLake(t *testing.T) {
	root := os.Getenv("STORAGE_ROOT")
	if root == "" {
		root = filepath.Join(storage.ProjectRoot(), ".data")
	}
	if _, err := os.Stat(filepath.Join(root, "silver", "observations")); err != nil {
		t.Skipf("no Silver observations under %s", root)
	}
	raw, err := os.ReadFile("testdata/series_eval.json")
	if err != nil {
		t.Fatal(err)
	}
	var suite struct {
		Cases []seriesEvalCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &suite); err != nil {
		t.Fatal(err)
	}

	cfg := &storage.Config{Profile: storage.ProfileLocal, Backend: storage.BackendLocal, Root: root, ScratchDir: t.TempDir()}
	resolver := storage.NewResolver(cfg)
	warehouse, err := query.Open(resolver, "4GB", 4)
	if err != nil {
		t.Fatal(err)
	}
	defer warehouse.Close()
	s := &Server{
		cfg:       &config.Config{Storage: cfg},
		storage:   resolver,
		warehouse: warehouse,
		cache:     cache.Nothing{},
		log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	catalogue, err := s.assistantCatalogue(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	titles := make(map[string]string, len(catalogue.datasets))
	for _, d := range catalogue.datasets {
		titles[d.DatasetID] = datasetTitle(d)
	}

	for _, c := range suite.Cases {
		var messages []assistantMessage
		for _, ask := range c.Ask {
			messages = append(messages, assistantMessage{Role: "user", Content: ask})
		}
		asked := strings.Join(c.Ask, " → ")
		q := readQuestion(messages)
		shown := withoutQuoteVariants(rankSeries(catalogue.series, titles, q.terms, q.topicTerms...))
		for n, i := range shown {
			t.Logf("%s #%d %s (%d places, %s to %s)", clip(asked, 40), n+1, indicatorTitle(i), i.Geographies, i.PeriodStart, i.PeriodEnd)
		}
		datasets := rankDatasets(catalogue.datasets, q.terms, q.topicTerms...)
		var named []string
		for _, i := range shown {
			named = append(named, indicatorTitle(i))
		}
		for _, d := range datasets {
			named = append(named, datasetTitle(d))
		}
		// A commodity is shown with the datasets it is priced in, which is
		// how a question about rice reaches the food price collections.
		bySource := map[string][]string{}
		for _, d := range catalogue.datasets {
			bySource[d.SourceID] = append(bySource[d.SourceID], datasetTitle(d))
		}
		for _, c := range rankCommodities(catalogue.commodities, q.terms, q.topicTerms...) {
			named = append(named, c.Name)
			for _, source := range c.Sources {
				named = append(named, bySource[source]...)
			}
		}
		for _, want := range c.Want {
			found := false
			for _, title := range named {
				if containsAny(title, []string{want}) {
					found = true
					break
				}
			}
			if !found {
				t.Errorf("%q: nothing shown is titled %q", asked, want)
			}
		}
		if len(shown) == 0 {
			t.Errorf("%q: no series shown", asked)
			continue
		}
		if len(c.First) > 0 && !containsAny(indicatorTitle(shown[0]), c.First) {
			t.Errorf("%q: first is %q, want one titled with any of %v", asked, indicatorTitle(shown[0]), c.First)
		}
		for _, i := range shown {
			if containsAny(indicatorTitle(i), c.Never) {
				t.Errorf("%q: shown %q", asked, indicatorTitle(i))
			}
		}
		prompt, _ := assistantPrompt(catalogue, nil, q)
		for _, want := range c.Prompt {
			if !strings.Contains(prompt, want) {
				t.Errorf("%q: the prompt lacks %q", asked, want)
			}
		}
	}
}

// containsAny is whether text holds any of the words, ignoring case.
func containsAny(text string, words []string) bool {
	text = strings.ToLower(text)
	for _, word := range words {
		if strings.Contains(text, strings.ToLower(word)) {
			return true
		}
	}
	return false
}
