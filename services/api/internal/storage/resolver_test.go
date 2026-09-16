package storage

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func localConfig(t *testing.T) *Config {
	t.Helper()
	dir := t.TempDir()
	return &Config{
		Profile:    ProfileLocal,
		Backend:    BackendLocal,
		Root:       filepath.Join(dir, "data"),
		ScratchDir: filepath.Join(dir, "cache"),
	}
}

// ---- the §45.4 contract: one address, three backends ----------------------

func TestResolveAcrossBackends(t *testing.T) {
	cases := []struct {
		name string
		cfg  *Config
		want string
	}{
		{
			name: "nas",
			cfg:  &Config{Backend: BackendNAS, Root: "/Volumes/research/terusan"},
			want: "/Volumes/research/terusan/silver/observations/year=2026",
		},
		{
			name: "s3",
			cfg:  &Config{Backend: BackendS3, Root: "s3://terusan-warehouse"},
			want: "s3://terusan-warehouse/silver/observations/year=2026",
		},
		{
			name: "local, absolute",
			cfg:  &Config{Backend: BackendLocal, Root: "/srv/terusan"},
			want: "/srv/terusan/silver/observations/year=2026",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := NewResolver(tc.cfg).Resolve(LayerSilver, "observations", "year=2026")
			if err != nil {
				t.Fatalf("Resolve: %v", err)
			}
			if got != tc.want {
				t.Errorf("Resolve = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestResolveKeepsS3Scheme(t *testing.T) {
	// filepath.Join would collapse this to `s3:/terusan-warehouse`.
	cfg := &Config{Backend: BackendS3, Root: "s3://terusan-warehouse"}
	got, err := NewResolver(cfg).Resolve(LayerGold)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if !strings.HasPrefix(got, "s3://terusan-warehouse/") {
		t.Errorf("Resolve = %q, lost the s3:// scheme", got)
	}
}

func TestGlobTargetsParquetRecursively(t *testing.T) {
	got, err := NewResolver(localConfig(t)).Glob(LayerSilver, "observations")
	if err != nil {
		t.Fatalf("Glob: %v", err)
	}
	if !strings.HasSuffix(got, "/silver/observations/**/*.parquet") {
		t.Errorf("Glob = %q", got)
	}
}

// ---- configuration coherence ---------------------------------------------

func TestConfigValidation(t *testing.T) {
	cases := []struct {
		name    string
		cfg     Config
		wantErr string
	}{
		{"s3 without s3 root", Config{Backend: BackendS3, Root: "/mnt/x", S3AccessKey: "k", S3SecretKey: "s"}, "s3:// STORAGE_ROOT"},
		{"s3 without credentials", Config{Backend: BackendS3, Root: "s3://b"}, "S3_ACCESS_KEY_ID"},
		{"local with s3 root", Config{Backend: BackendLocal, Root: "s3://b"}, "cannot use an s3://"},
		{"nas with relative root", Config{Backend: BackendNAS, Root: "./mnt"}, "absolute mount path"},
		{"unknown backend", Config{Backend: "floppy", Root: "./x"}, "unknown STORAGE_BACKEND"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.cfg.validate()
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("validate() = %v, want error containing %q", err, tc.wantErr)
			}
		})
	}
}

func TestLoadConfigDefaultsToLocal(t *testing.T) {
	for _, k := range []string{
		"STORAGE_PROFILE", "STORAGE_BACKEND", "STORAGE_ROOT", "SCRATCH_DIR",
		"S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "STORAGE_ALLOW_SHARED_WRITES",
	} {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if cfg.Profile != ProfileLocal || cfg.Backend != BackendLocal {
		t.Errorf("unconfigured LoadConfig reached %s/%s, want local/local", cfg.Profile, cfg.Backend)
	}
}

// ---- write guards ---------------------------------------------------------

func TestSharedProfileIsReadOnlyByDefault(t *testing.T) {
	cfg := localConfig(t)
	cfg.Profile = ProfileProduction
	_, err := NewResolver(cfg).ResolveForWrite(LayerSilver, false, "observations")
	if !errors.Is(err, ErrWriteRefused) {
		t.Fatalf("ResolveForWrite error = %v, want ErrWriteRefused", err)
	}
}

func TestSharedProfileWriteNeedsOverride(t *testing.T) {
	cfg := localConfig(t)
	cfg.Profile = ProfileProduction
	cfg.AllowSharedWrites = true
	if _, err := NewResolver(cfg).ResolveForWrite(LayerSilver, false, "observations"); err != nil {
		t.Fatalf("ResolveForWrite: %v", err)
	}
}

func TestRawLayerRefusesCasualWrites(t *testing.T) {
	_, err := NewResolver(localConfig(t)).ResolveForWrite(LayerRaw, false, "regulations")
	if !errors.Is(err, ErrWriteRefused) {
		t.Fatalf("ResolveForWrite error = %v, want ErrWriteRefused", err)
	}
}

func TestRawLayerAcceptsDeliberateLanding(t *testing.T) {
	got, err := NewResolver(localConfig(t)).ResolveForWrite(LayerRaw, true, "regulations")
	if err != nil {
		t.Fatalf("ResolveForWrite: %v", err)
	}
	if !strings.HasSuffix(got, "/raw/regulations") {
		t.Errorf("ResolveForWrite = %q", got)
	}
}

func TestWriteCreatesDirectory(t *testing.T) {
	got, err := NewResolver(localConfig(t)).ResolveForWrite(LayerGold, false, "economics", "inflation")
	if err != nil {
		t.Fatalf("ResolveForWrite: %v", err)
	}
	if info, err := os.Stat(got); err != nil || !info.IsDir() {
		t.Errorf("ResolveForWrite did not create %q", got)
	}
}

// ---- scratch stays local --------------------------------------------------

func TestScratchIgnoresStorageRoot(t *testing.T) {
	cfg := localConfig(t)
	got, err := NewResolver(cfg).Scratch("compaction")
	if err != nil {
		t.Fatalf("Scratch: %v", err)
	}
	if strings.Contains(got, cfg.Root) {
		t.Errorf("Scratch = %q, must not sit under STORAGE_ROOT %q", got, cfg.Root)
	}
	if info, err := os.Stat(got); err != nil || !info.IsDir() {
		t.Errorf("Scratch did not create %q", got)
	}
}

// ---- path hygiene ---------------------------------------------------------

func TestSlugify(t *testing.T) {
	cases := map[string]string{
		"Peraturan Menteri ESDM": "peraturan-menteri-esdm",
		"PP No. 12/2026":         "pp-no.-12-2026",
		"Ekspor Nikel — Q1":      "ekspor-nikel-q1",
		"year=2026":              "year=2026",
		"a:b:c":                  "a-b-c",
	}
	for in, want := range cases {
		got, err := Slugify(in)
		if err != nil {
			t.Errorf("Slugify(%q): %v", in, err)
			continue
		}
		if got != want {
			t.Errorf("Slugify(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestSlugifyRejectsEmptyResult(t *testing.T) {
	if _, err := Slugify("///"); err == nil {
		t.Error("Slugify(\"///\") = nil error, want ErrUnsafeSegment")
	}
}

func TestResolveRejectsUnsafeSegments(t *testing.T) {
	r := NewResolver(localConfig(t))
	for _, segment := range []string{"..", ".", "", "a/b", "Uppercase", "with space"} {
		if _, err := r.Resolve(LayerSilver, segment); err == nil {
			t.Errorf("Resolve accepted unsafe segment %q", segment)
		}
	}
}

func TestEnsureLayoutCreatesEveryLayer(t *testing.T) {
	created, err := NewResolver(localConfig(t)).EnsureLayout()
	if err != nil {
		t.Fatalf("EnsureLayout: %v", err)
	}
	if len(created) != len(AllLayers) {
		t.Fatalf("EnsureLayout created %d dirs, want %d", len(created), len(AllLayers))
	}
	for _, p := range created {
		if info, err := os.Stat(p); err != nil || !info.IsDir() {
			t.Errorf("EnsureLayout did not create %q", p)
		}
	}
}

func TestEnsureLayoutSkipsObjectStorage(t *testing.T) {
	cfg := &Config{Backend: BackendS3, Root: "s3://terusan-warehouse"}
	created, err := NewResolver(cfg).EnsureLayout()
	if err != nil {
		t.Fatalf("EnsureLayout: %v", err)
	}
	if len(created) != 0 {
		t.Errorf("EnsureLayout created %v for object storage, want none", created)
	}
}

// ---- anchoring relative roots ---------------------------------------------

func TestRelativeRootAnchorsToTheProject(t *testing.T) {
	// Without this, a process started in a subdirectory writes a second lake
	// beside itself and the first one looks empty.
	root := t.TempDir()
	for _, marker := range rootMarkers {
		if err := os.MkdirAll(filepath.Join(root, marker), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv(RootEnvVar, root)
	resetRootCache()

	cfg := &Config{Backend: BackendLocal, Root: "./.data"}
	got, err := NewResolver(cfg).Resolve(LayerSilver, "observations")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	want := filepath.Join(root, ".data", "silver", "observations")
	if got != want {
		t.Errorf("Resolve = %q, want %q", got, want)
	}
}

func TestAbsoluteRootsArePassedThrough(t *testing.T) {
	t.Setenv(RootEnvVar, t.TempDir())
	resetRootCache()

	cfg := &Config{Backend: BackendNAS, Root: "/Volumes/research/terusan"}
	got, err := NewResolver(cfg).Resolve(LayerGold)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if got != "/Volumes/research/terusan/gold" {
		t.Errorf("Resolve = %q, want the NAS path unchanged", got)
	}
}

func TestObjectStorageRootsAreNotTreatedAsPaths(t *testing.T) {
	t.Setenv(RootEnvVar, t.TempDir())
	resetRootCache()

	cfg := &Config{Backend: BackendS3, Root: "s3://terusan-warehouse"}
	if got := NewResolver(cfg).Root(); got != "s3://terusan-warehouse" {
		t.Errorf("Root = %q, want the URI unchanged", got)
	}
}

func TestScratchAnchorsToo(t *testing.T) {
	root := t.TempDir()
	t.Setenv(RootEnvVar, root)
	resetRootCache()

	cfg := &Config{Backend: BackendLocal, Root: "./.data", ScratchDir: "./.cache"}
	got, err := NewResolver(cfg).Scratch("compaction")
	if err != nil {
		t.Fatalf("Scratch: %v", err)
	}
	if !strings.HasPrefix(got, root) {
		t.Errorf("Scratch = %q, want it under %q", got, root)
	}
}
