package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/cache"
	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/query"
	"github.com/csis/terusan/services/api/internal/storage"
)

// The regulation search, measured against the real lake.
//
// Every case in testdata/regulation_eval.json is a question and the
// regulations that answer it. The test asks each question the way the
// assistant does — read, searched, filtered to what the model would be shown
// — and counts a case as found when any of its regulations is among those.
// The router is not asked: this is the keyword rule's view, which is what a
// turn gets when the router is down, and the harder of the two.
//
// It needs a lake with the index in it, so it skips anywhere without one —
// CI included. Run it after changing the search or rebuilding the index:
//
//	make eval-assistant
//
// It fails below regulationEvalFloor. Raise the floor as the search improves;
// never lower it to make a change pass.

// regulationEvalFloor is the share of cases that must be found.
const regulationEvalFloor = 0.9

type regulationEvalCase struct {
	Ask  []string `json:"ask"`
	Want []string `json:"want"`
	None bool     `json:"none"`
}

func TestRegulationSearchAgainstTheLake(t *testing.T) {
	root := os.Getenv("STORAGE_ROOT")
	if root == "" {
		root = filepath.Join(storage.ProjectRoot(), ".data")
	}
	if _, err := os.Stat(filepath.Join(root, "gold", regulationIndex, "postings")); err != nil {
		t.Skipf("no regulation index under %s; run scripts/index-regulations.sh", root)
	}

	raw, err := os.ReadFile("testdata/regulation_eval.json")
	if err != nil {
		t.Fatal(err)
	}
	var suite struct {
		Cases []regulationEvalCase `json:"cases"`
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

	ctx := context.Background()
	// The portal's figures, against which a question that never says
	// "peraturan" is weighed.
	catalogue, err := s.assistantCatalogue(ctx)
	if err != nil {
		t.Fatal(err)
	}
	found, foundBefore, questions := 0, 0, 0
	reciprocal := 0.0
	for _, c := range suite.Cases {
		var messages []assistantMessage
		for _, ask := range c.Ask {
			messages = append(messages, assistantMessage{Role: "user", Content: ask})
		}
		q := readQuestion(messages)
		asked := strings.Join(c.Ask, " → ")

		hits, err := s.rankRegulations(ctx, q)
		if err != nil {
			t.Fatalf("%s: %v", asked, err)
		}
		figures := catalogueCoverage(catalogue, q)
		shown := regulationsWorthShowing(hits, q.aboutRegulation, figures)
		asked += fmt.Sprintf(" [figures %.2f]", figures)
		before, err := s.matchRegulationTitles(ctx, q)
		if err != nil {
			t.Fatalf("%s (titles): %v", asked, err)
		}

		if c.None {
			// A question about figures: nothing shown, and it is not
			// read as a question about regulations.
			questions++
			if len(shown) == 0 && !q.aboutRegulation {
				found++
				t.Logf("ok   none %q, best unshown %s", asked, describeHit(hits))
			} else {
				t.Logf("MISS %q brought %d regulations, first %s", asked, len(shown), describeHit(shown))
			}
			if len(before) == 0 {
				foundBefore++
			}
			continue
		}

		questions++
		rank := slices.IndexFunc(shown, func(h regulationHit) bool { return slices.Contains(c.Want, h.Key) })
		if rank >= 0 {
			found++
			reciprocal += 1 / float64(rank+1)
			t.Logf("ok   #%d %q → %s", rank+1, asked, describeHit(shown[rank:]))
		} else {
			t.Logf("MISS %q: want %v, shown %d of %d hits, first %s",
				asked, c.Want, len(shown), len(hits), describeHit(hits))
		}
		if slices.ContainsFunc(before, func(h regulationHit) bool { return slices.Contains(c.Want, h.Key) }) {
			foundBefore++
		}
	}

	recall := float64(found) / float64(questions)
	t.Logf("found %d of %d (%.0f%%), MRR %.2f; title matching alone found %d (%.0f%%)",
		found, questions, 100*recall, reciprocal/float64(questions),
		foundBefore, 100*float64(foundBefore)/float64(questions))
	if recall < regulationEvalFloor {
		t.Fatalf("found %.0f%% of cases, below the floor of %.0f%%", 100*recall, 100*regulationEvalFloor)
	}
}

func describeHit(hits []regulationHit) string {
	if len(hits) == 0 {
		return "(none)"
	}
	h := hits[0]
	quote := ""
	if h.Pasal != "" {
		quote = " Pasal " + h.Pasal
	}
	return fmt.Sprintf("%s %s%s (covers %.2f, title %.2f)",
		h.Key, clip(h.Title, 70), quote, h.Coverage, h.TitleCoverage)
}
