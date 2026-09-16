package storage

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The Go resolver must satisfy the same cross-language contract as
// pipelines/src/terusan_pipelines/storage. If these two disagree, the
// pipelines write a dataset to one path and the API reads another
// (program.md §45.4).

type contract struct {
	Resolve []struct {
		Backend  string   `json:"backend"`
		Root     string   `json:"root"`
		Layer    string   `json:"layer"`
		Segments []string `json:"segments"`
		Expected string   `json:"expected"`
	} `json:"resolve"`
	Slugify []struct {
		Input    string `json:"input"`
		Expected string `json:"expected"`
	} `json:"slugify"`
	UnsafeSegments []string `json:"unsafe_segments"`
}

func loadContract(t *testing.T) contract {
	t.Helper()
	path := filepath.Join("..", "..", "..", "..", "fixtures", "storage", "contract.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read contract fixture: %v", err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("parse contract fixture: %v", err)
	}
	return c
}

func TestResolveMatchesContract(t *testing.T) {
	for _, tc := range loadContract(t).Resolve {
		t.Run(tc.Expected, func(t *testing.T) {
			cfg := &Config{Backend: Backend(tc.Backend), Root: tc.Root}
			if cfg.Backend == BackendS3 {
				cfg.S3AccessKey, cfg.S3SecretKey = "key", "secret"
			}
			if err := cfg.validate(); err != nil {
				t.Fatalf("contract case is not a valid config: %v", err)
			}
			got, err := NewResolver(cfg).Resolve(Layer(tc.Layer), tc.Segments...)
			if err != nil {
				t.Fatalf("Resolve: %v", err)
			}
			if got != tc.Expected {
				t.Errorf("Resolve = %q, want %q", got, tc.Expected)
			}
		})
	}
}

func TestSlugifyMatchesContract(t *testing.T) {
	for _, tc := range loadContract(t).Slugify {
		t.Run(tc.Input, func(t *testing.T) {
			got, err := Slugify(tc.Input)
			if err != nil {
				t.Fatalf("Slugify(%q): %v", tc.Input, err)
			}
			if got != tc.Expected {
				t.Errorf("Slugify(%q) = %q, want %q", tc.Input, got, tc.Expected)
			}
		})
	}
}

func TestUnsafeSegmentsAreRejected(t *testing.T) {
	r := NewResolver(&Config{Backend: BackendLocal, Root: "./.data"})
	for _, segment := range loadContract(t).UnsafeSegments {
		if _, err := r.Resolve(LayerSilver, segment); err == nil {
			t.Errorf("Resolve accepted unsafe segment %q", segment)
		}
	}
}
