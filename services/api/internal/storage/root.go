package storage

import (
	"os"
	"path/filepath"
	"sync"
)

// A relative STORAGE_ROOT has to name the same directory whichever one the
// process was started in. Without anchoring, a command run from a subdirectory
// writes a second lake beside itself and the first one looks empty.
//
// This mirrors pipelines/src/terusan_pipelines/storage/root.py.

// RootEnvVar overrides the search, for a deployment whose layout differs.
const RootEnvVar = "TERUSAN_ROOT"

// rootMarkers name a directory as the project root only together. Any one
// alone is too weak: a Makefile matches plenty of directories that are not
// this one.
var rootMarkers = []string{"pipelines", "reference", "Makefile"}

var (
	rootOnce  sync.Once
	rootCache string
)

// ProjectRoot returns the directory holding the project, or the working
// directory. It falls back rather than failing: a binary run outside a checkout
// still works, it simply anchors to wherever it was started.
func ProjectRoot() string {
	rootOnce.Do(func() {
		if override := os.Getenv(RootEnvVar); override != "" {
			if abs, err := filepath.Abs(override); err == nil {
				rootCache = abs
				return
			}
		}

		cwd, err := os.Getwd()
		if err != nil {
			rootCache = "."
			return
		}

		for dir := cwd; ; dir = filepath.Dir(dir) {
			if hasAllMarkers(dir) {
				rootCache = dir
				return
			}
			if parent := filepath.Dir(dir); parent == dir {
				break
			}
		}
		rootCache = cwd
	})
	return rootCache
}

func hasAllMarkers(dir string) bool {
	for _, marker := range rootMarkers {
		if _, err := os.Stat(filepath.Join(dir, marker)); err != nil {
			return false
		}
	}
	return true
}

// ResolvePath anchors a relative path to the project root. Absolute paths pass
// through unchanged.
func ResolvePath(path string) string {
	if path == "" || filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(ProjectRoot(), path)
}

// resetRootCache clears the memoised project root. Tests that change
// TERUSAN_ROOT need it; nothing else should.
func resetRootCache() {
	rootOnce = sync.Once{}
	rootCache = ""
}
