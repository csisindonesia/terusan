package storage

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// Backend names the physical storage provider behind STORAGE_ROOT.
type Backend string

const (
	BackendLocal Backend = "local"
	BackendNAS   Backend = "nas"
	BackendS3    Backend = "s3"
)

// Profile names the deployment environment. It decides whether writes are
// permitted, independently of the backend.
type Profile string

const (
	ProfileLocal      Profile = "local"
	ProfileSharedDev  Profile = "shared-dev"
	ProfileStaging    Profile = "staging"
	ProfileProduction Profile = "production"
)

// ErrWriteRefused reports a write the active profile disallows.
var ErrWriteRefused = errors.New("storage write refused")

// Config holds resolved storage settings. Build it with LoadConfig; this is
// the only place storage environment variables are read.
type Config struct {
	Profile     Profile
	Backend     Backend
	Root        string
	ScratchDir  string
	S3Endpoint  string
	S3Region    string
	S3AccessKey string
	S3SecretKey string
	S3UseSSL    bool

	// AllowSharedWrites must be set explicitly before a non-local profile
	// will accept writes, so an inherited production credential in a
	// developer shell cannot quietly mutate the shared lake.
	AllowSharedWrites bool
}

// LoadConfig reads storage settings from the environment, defaulting to the
// local profile. Absent configuration must never resolve to a shared backend
// (program.md §45.9).
func LoadConfig() (*Config, error) {
	c := &Config{
		Profile:           Profile(envOr("STORAGE_PROFILE", string(ProfileLocal))),
		Backend:           Backend(envOr("STORAGE_BACKEND", string(BackendLocal))),
		Root:              envOr("STORAGE_ROOT", "./.data"),
		ScratchDir:        envOr("SCRATCH_DIR", "./.cache"),
		S3Endpoint:        os.Getenv("S3_ENDPOINT"),
		S3Region:          envOr("S3_REGION", "auto"),
		S3AccessKey:       os.Getenv("S3_ACCESS_KEY_ID"),
		S3SecretKey:       os.Getenv("S3_SECRET_ACCESS_KEY"),
		S3UseSSL:          envOr("S3_USE_SSL", "true") == "true",
		AllowSharedWrites: os.Getenv("STORAGE_ALLOW_SHARED_WRITES") == "true",
	}
	if err := c.validate(); err != nil {
		return nil, err
	}
	return c, nil
}

func (c *Config) validate() error {
	isS3Root := strings.HasPrefix(c.Root, "s3://")
	switch c.Backend {
	case BackendS3:
		if !isS3Root {
			return fmt.Errorf("backend=s3 requires an s3:// STORAGE_ROOT, got %q", c.Root)
		}
		if c.S3AccessKey == "" || c.S3SecretKey == "" {
			return errors.New("backend=s3 requires S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
		}
	case BackendNAS:
		if isS3Root {
			return fmt.Errorf("backend=nas cannot use an s3:// STORAGE_ROOT")
		}
		if !filepath.IsAbs(c.Root) {
			return fmt.Errorf("backend=nas requires an absolute mount path, got %q; "+
				"session automounts are not stable enough for serving use", c.Root)
		}
	case BackendLocal:
		if isS3Root {
			return fmt.Errorf("backend=local cannot use an s3:// STORAGE_ROOT")
		}
	default:
		return fmt.Errorf("unknown STORAGE_BACKEND %q", c.Backend)
	}
	return nil
}

// IsObjectStorage reports whether the lake lives in a bucket.
func (c *Config) IsObjectStorage() bool { return c.Backend == BackendS3 }

// WritesAllowed reports whether this process may write to the lake.
func (c *Config) WritesAllowed() bool {
	return c.Profile == ProfileLocal || c.AllowSharedWrites
}

// Resolver turns logical addresses into physical paths or URIs.
type Resolver struct{ cfg *Config }

// NewResolver returns a Resolver over cfg.
func NewResolver(cfg *Config) *Resolver { return &Resolver{cfg: cfg} }

// Config exposes the settings this Resolver was built with.
func (r *Resolver) Config() *Config { return r.cfg }

// Root is the lake root: absolute for a filesystem, a URI for a bucket.
// Everything below resolves from here, so a relative STORAGE_ROOT cannot mean
// two different directories depending on where the process was started.
func (r *Resolver) Root() string {
	if r.cfg.IsObjectStorage() {
		return r.cfg.Root
	}
	return ResolvePath(r.cfg.Root)
}

// Resolve returns the physical location of a logical address.
//
// Segments are validated, not rewritten: a path that silently differs from
// what the catalog recorded is worse than a loud failure. Callers holding
// untrusted text should call Slugify first.
func (r *Resolver) Resolve(layer Layer, segments ...string) (string, error) {
	for _, s := range segments {
		if err := CheckSegment(s); err != nil {
			return "", err
		}
	}
	return join(append([]string{r.Root(), layer.String()}, segments...)...), nil
}

// Glob returns a recursive pattern suitable for DuckDB read_parquet.
func (r *Resolver) Glob(layer Layer, segments ...string) (string, error) {
	base, err := r.Resolve(layer, segments...)
	if err != nil {
		return "", err
	}
	return join(base, "**/*.parquet"), nil
}

// HasParquet reports whether a dataset has any files behind it, by looking at
// the filesystem rather than by asking the query engine.
//
// The engine's answer costs a glob expansion: `SELECT 1 FROM read_parquet(...)
// LIMIT 1` over the observations enumerates sixteen hundred partition
// directories before it can return the one row, which measured at 176ms — paid
// on nearly every request, before the real query starts.
//
// This walks until it finds the first Parquet file and stops. Object storage
// has no cheap equivalent, so it reports false there and the caller falls back
// to asking the engine.
func (r *Resolver) HasParquet(layer Layer, segments ...string) (found bool, known bool) {
	if r.cfg.IsObjectStorage() {
		return false, false
	}
	root, err := r.Resolve(layer, segments...)
	if err != nil {
		return false, false
	}

	stop := errors.New("found")
	err = filepath.WalkDir(root, func(_ string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".parquet") {
			found = true
			return stop
		}
		return nil
	})
	switch {
	case errors.Is(err, stop):
		return true, true
	case errors.Is(err, fs.ErrNotExist):
		// A dataset that was never written. A definite no, and the common
		// case for a lake that holds only some of what the API can serve.
		return false, true
	case err != nil:
		// An unreadable directory is not an answer; let the engine try.
		return false, false
	}
	return found, true
}

// Scratch returns a local-disk scratch path, creating the directory. It never
// resolves against STORAGE_ROOT: spill and staging stay on local SSD however
// the lake is stored (program.md §45.6).
func (r *Resolver) Scratch(segments ...string) (string, error) {
	for _, s := range segments {
		if err := CheckSegment(s); err != nil {
			return "", err
		}
	}
	path := filepath.Join(append([]string{ResolvePath(r.cfg.ScratchDir)}, segments...)...)
	if err := os.MkdirAll(path, 0o755); err != nil {
		return "", fmt.Errorf("create scratch dir: %w", err)
	}
	return path, nil
}

// ResolveForWrite is Resolve with the profile and immutability guards applied.
//
// The two guards fail for different reasons: a shared profile is a
// wrong-machine mistake, while writing to RAW is a wrong-layer mistake that
// would destroy the one non-reproducible copy of a source document.
func (r *Resolver) ResolveForWrite(layer Layer, overwriteImmutable bool, segments ...string) (string, error) {
	if !r.cfg.WritesAllowed() {
		return "", fmt.Errorf("%w: profile %s is read-only for this process; "+
			"set STORAGE_ALLOW_SHARED_WRITES=true to override", ErrWriteRefused, r.cfg.Profile)
	}
	if layer.Immutable() && !overwriteImmutable {
		return "", fmt.Errorf("%w: layer %s is immutable", ErrWriteRefused, layer)
	}
	target, err := r.Resolve(layer, segments...)
	if err != nil {
		return "", err
	}
	if !r.cfg.IsObjectStorage() {
		if err := os.MkdirAll(target, 0o755); err != nil {
			return "", fmt.Errorf("create %s: %w", target, err)
		}
	}
	return target, nil
}

// EnsureLayout creates every layer directory. Object storage has no
// directories to create, so it returns nothing.
func (r *Resolver) EnsureLayout() ([]string, error) {
	if r.cfg.IsObjectStorage() {
		return nil, nil
	}
	created := make([]string, 0, len(AllLayers))
	for _, layer := range AllLayers {
		path := join(r.Root(), layer.String())
		if err := os.MkdirAll(path, 0o755); err != nil {
			return nil, fmt.Errorf("create %s: %w", path, err)
		}
		created = append(created, path)
	}
	return created, nil
}

// join concatenates path parts. filepath.Join collapses `s3://` to `s3:/`,
// so string joining is deliberate here.
func join(parts ...string) string {
	if len(parts) == 0 {
		return ""
	}
	stem := strings.TrimRight(parts[0], "/")
	for _, p := range parts[1:] {
		stem += "/" + strings.Trim(p, "/")
	}
	return stem
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
