// Package config assembles the serving layer's runtime settings.
package config

import (
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// Config is the fully resolved configuration for one API process.
type Config struct {
	Host     string
	Port     int
	Storage  *storage.Config
	Database string

	DuckDBMemoryLimit string
	DuckDBThreads     int
	// How many queries run against the lake at once. Past it they queue:
	// every one shares the same memory limit and threads, and ten full scans
	// side by side each took four times as long as one alone.
	DuckDBMaxConnections int
	// How long a query against the lake may run before it is stopped.
	QueryTimeout time.Duration

	// Where to cache rendered responses. Empty disables caching entirely and
	// the API serves every request from Parquet, which is correct and slower
	// (see internal/cache).
	RedisURL string
	// How long a cached response is served before it is derived again. The
	// bound on how stale an answer can be after a pipeline run.
	CacheTTL time.Duration

	// Origins allowed to call the API from a browser. An allow-list rather
	// than a wildcard: restricted datasets should not be readable from any
	// page a browser happens to load.
	CORSOrigins []string
	// Where the portal is served, for the few answers that send a browser back
	// to it rather than answering in JSON — the end of a Microsoft sign-in.
	// PORTAL_ORIGIN, or else the first CORS origin, which is the portal on
	// every deployment this has had.
	PortalOrigin string

	ReadTimeout     time.Duration
	WriteTimeout    time.Duration
	ShutdownTimeout time.Duration

	// Whether this process may start a pipeline, and how.
	Pipelines Pipelines

	// Where the application database is: the shelf and the accounts.
	AppDB string
	// Whether the shelf accepts changes.
	Collections Collections
	// Accounts, sessions, and whether the data routes need one.
	Auth Auth
	// The chat that suggests what to read. Off without a Cloudflare account
	// and token.
	Assistant Assistant
}

// Assistant is the model behind `POST /v1/assistant/chat`.
//
// Workers AI, on the account the news classifier already bills to. Off unless
// both the account and a token are set: the portal asks the capability report
// and hides the chat rather than offering a box that answers with an error.
type Assistant struct {
	AccountID string
	Token     string
	// A Workers AI model id.
	Model string
	// Optional: route through a named AI Gateway, for its own meter.
	GatewayID string
	// The classifier that routes each question and guards the chat before
	// the writing model is called (see httpapi/assistant_guard.go). Empty
	// turns it off, and the keyword rule routes alone.
	RouterModel string
}

// Enabled reports whether the chat can answer.
func (a Assistant) Enabled() bool { return a.AccountID != "" && a.Token != "" }

// Auth is how people log in, and what a session is worth.
//
// Accounts exist wherever APP_DB does; `Required` is the separate question of
// whether the *data* routes answer to a stranger. They are separate because
// most deployments of this want both: a public warehouse that anybody can
// read, and named accounts for the people whose shelf and saved queries it
// keeps.
type Auth struct {
	// Whether every route but the probes, the capability report and the login
	// routes needs a session or an API token. On unless AUTH_REQUIRED=false:
	// a public lake is a choice somebody makes, not a default they inherit.
	Required bool
	// Force `Secure` on the session cookie. Set it wherever the portal is
	// served over TLS but the API sits behind a proxy that terminates it, so
	// `r.TLS` is nil and this process cannot tell.
	SecureCookies bool
	// How long a session lasts, and how long "remember for 30 days" lasts.
	TTL         time.Duration
	RememberTTL time.Duration
	// PBKDF2 iterations for new passwords. Raising it re-hashes each password
	// the next time it is used rather than invalidating anything.
	Iterations int
	// Whether the portal's "request access" form answers. Requests land as
	// pending accounts that an admin approves; nobody gets in by filling it.
	Registration bool
	// Whether to believe the client address and location a proxy in front
	// of this process reports (CF-Connecting-IP, CF-IPCountry,
	// X-Forwarded-For). Only for the access log: set it where Cloudflare or
	// another proxy is the only way in, since anyone who can reach the API
	// directly can write those headers.
	TrustProxy bool
	// Signing in with a Microsoft 365 work account. Off unless configured.
	Microsoft Microsoft
}

// Microsoft is single sign-on against one Microsoft Entra ID tenant — the
// directory behind an office's Microsoft 365 — through OpenID Connect.
//
// One tenant, never "common" or "organizations": those accept every work
// account in the world, and the point of this is that being in *this* office's
// directory is what vouches for a person.
type Microsoft struct {
	// The directory (tenant) ID of the app registration, a GUID.
	TenantID string
	// The application (client) ID and a client secret issued for it.
	ClientID     string
	ClientSecret string
	// Where Microsoft sends the browser back: this API's
	// /v1/auth/microsoft/callback, exactly as registered in Entra.
	RedirectURL string
	// Email domains allowed in, lowercase. Empty lets in anyone the tenant
	// vouches for, guests invited into it included.
	Domains []string
	// What a first sign-in from an address with no account gets: "pending"
	// (an admin approves it, the default), "active" (a researcher account at
	// once), or "refuse" (only addresses an admin already created).
	NewAccounts string
	// The login host. Only ever changed by tests and sovereign clouds.
	Authority string
}

// Enabled reports whether the portal can offer "Log in with Microsoft".
func (m Microsoft) Enabled() bool { return m.ClientID != "" }

// Endpoint is one of the tenant's OAuth endpoints: "authorize" or "token".
func (m Microsoft) Endpoint(name string) string {
	return m.Authority + "/" + m.TenantID + "/oauth2/v2.0/" + name
}

// Issuer is what this tenant's ID tokens name as their issuer.
func (m Microsoft) Issuer() string {
	return m.Authority + "/" + m.TenantID + "/v2.0"
}

// Allows reports whether an address is on one of the allowed domains, or
// whether any address the tenant vouches for will do.
func (m Microsoft) Allows(address string) bool {
	if len(m.Domains) == 0 {
		return true
	}
	_, domain, found := strings.Cut(address, "@")
	if !found {
		return false
	}
	for _, allowed := range m.Domains {
		if strings.EqualFold(domain, allowed) {
			return true
		}
	}
	return false
}

// Collections is whether the shelf accepts changes.
//
// The shelf itself lives wherever APP_DB does. Off entirely without one, and
// the portal then keeps collections in the browser instead — which works, and
// cannot be shared: a folder in localStorage has no URL anyone else can open.
type Collections struct {
	// Whether POST, PATCH and DELETE answer. A deployment reachable by people
	// who should not be editing the shelf leaves this off and serves it
	// read-only; a session identifies a reader but does not yet scope what
	// they may change (program.md §34).
	Write bool
}

// Pipelines is what the serving layer needs in order to run one.
//
// Off unless switched on, and it stays off in every deployment that does not
// have the pipelines checked out beside it. The portal's "run ingestion now"
// is for a maintainer's own machine and for an operations host, not for a
// public read replica — see internal/runner for why the exception exists at
// all and what bounds it.
type Pipelines struct {
	Enabled bool
	// The repository root. Commands run in <Root>/pipelines.
	Root string
	// The uv binary. A bare name is looked up on PATH.
	UV string
	// When a run is considered hung and is stopped.
	Timeout time.Duration
	// How many runs may go at once, across every source.
	MaxConcurrent int
	// How many lines of a run's output to keep for the browser.
	MaxOutputLines int
	// How many finished runs to remember.
	History int
}

// Load reads configuration from the environment. It fails rather than
// defaulting whenever a wrong guess would be worse than not starting.
func Load() (*Config, error) {
	storageCfg, err := storage.LoadConfig()
	if err != nil {
		return nil, fmt.Errorf("storage config: %w", err)
	}

	port, err := intEnv("API_PORT", 8080)
	if err != nil {
		return nil, err
	}
	threads, err := intEnv("DUCKDB_THREADS", 4)
	if err != nil {
		return nil, err
	}
	// Seconds rather than a duration string: this is set by whoever runs the
	// deployment, and "60" is harder to get wrong than "60s" or "1m".
	maxConnections, err := intEnv("DUCKDB_MAX_CONNECTIONS", 8)
	if err != nil {
		return nil, err
	}
	queryTimeout, err := intEnv("QUERY_TIMEOUT_SECONDS", 90)
	if err != nil {
		return nil, err
	}
	if maxConnections < 1 || queryTimeout < 1 {
		return nil, fmt.Errorf("DUCKDB_MAX_CONNECTIONS and QUERY_TIMEOUT_SECONDS must be at least 1")
	}
	cacheTTL, err := intEnv("CACHE_TTL_SECONDS", 60)
	if err != nil {
		return nil, err
	}
	if cacheTTL < 1 {
		return nil, fmt.Errorf("CACHE_TTL_SECONDS must be at least 1, got %d", cacheTTL)
	}

	pipelines, err := loadPipelines()
	if err != nil {
		return nil, err
	}

	// Writes are on by default *once a database is given*, because configuring
	// one is already the decision to keep a shelf here; a deployment that wants
	// it read-only says so.
	collectionsWrite, err := boolEnv("COLLECTIONS_WRITE", true)
	if err != nil {
		return nil, err
	}

	authCfg, err := loadAuth()
	if err != nil {
		return nil, err
	}

	corsOrigins := strings.Split(
		envOr("API_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"), ",")
	portalOrigin := strings.TrimRight(
		envOr("PORTAL_ORIGIN", strings.TrimSpace(corsOrigins[0])), "/")

	return &Config{
		Host:                 envOr("API_HOST", "127.0.0.1"),
		Port:                 port,
		Storage:              storageCfg,
		Database:             os.Getenv("DATABASE_URL"),
		DuckDBMemoryLimit:    envOr("DUCKDB_MEMORY_LIMIT", "4GB"),
		DuckDBThreads:        threads,
		DuckDBMaxConnections: maxConnections,
		QueryTimeout:         time.Duration(queryTimeout) * time.Second,
		RedisURL:             os.Getenv("REDIS_URL"),
		CacheTTL:             time.Duration(cacheTTL) * time.Second,
		CORSOrigins:          corsOrigins,
		PortalOrigin:         portalOrigin,
		ReadTimeout:          15 * time.Second,
		WriteTimeout:         120 * time.Second, // analytical queries are slow by nature
		ShutdownTimeout:      20 * time.Second,
		Pipelines:            pipelines,
		// COLLECTIONS_DB is the older name for the same file, from when the
		// shelf was the only thing in it. Still read, because a deployment
		// that set it should not lose its collections to a rename.
		AppDB:       envOr("APP_DB", os.Getenv("COLLECTIONS_DB")),
		Collections: Collections{Write: collectionsWrite},
		Auth:        authCfg,
		Assistant: Assistant{
			AccountID: os.Getenv("CF_ACCOUNT_ID"),
			// Its own token where one is issued; otherwise the classifier's,
			// which is already scoped to Workers AI on the same account.
			Token:     envOr("CF_AI_TOKEN", os.Getenv("JEV_CLOUDFLARE")),
			Model:     envOr("ASSISTANT_MODEL", "@cf/zai-org/glm-4.7-flash"),
			GatewayID: os.Getenv("AI_GATEWAY_ID"),
			// "off" rather than empty to switch it off, since an empty
			// variable in a compose file is indistinguishable from unset.
			RouterModel: routerModel(),
		},
	}, nil
}

// routerModel is the assistant's router: Jev unless switched off.
func routerModel() string {
	switch model := strings.TrimSpace(os.Getenv("ASSISTANT_ROUTER_MODEL")); model {
	case "":
		return "typesafe/jev"
	case "off", "none", "false":
		return ""
	default:
		return model
	}
}

// loadAuth reads how long a session lasts and whether one is required.
func loadAuth() (Auth, error) {
	required, err := boolEnv("AUTH_REQUIRED", true)
	if err != nil {
		return Auth{}, err
	}
	secure, err := boolEnv("AUTH_SECURE_COOKIES", false)
	if err != nil {
		return Auth{}, err
	}
	registration, err := boolEnv("AUTH_REGISTRATION", true)
	if err != nil {
		return Auth{}, err
	}
	trustProxy, err := boolEnv("AUTH_TRUST_PROXY", false)
	if err != nil {
		return Auth{}, err
	}
	// Hours rather than a duration string, for the same reason the cache TTL
	// is seconds: this is set by whoever runs the deployment.
	ttl, err := intEnv("AUTH_SESSION_HOURS", 12)
	if err != nil {
		return Auth{}, err
	}
	remember, err := intEnv("AUTH_REMEMBER_DAYS", 30)
	if err != nil {
		return Auth{}, err
	}
	iterations, err := intEnv("AUTH_PBKDF2_ITERATIONS", 600_000)
	if err != nil {
		return Auth{}, err
	}
	if ttl < 1 || remember < 1 {
		return Auth{}, fmt.Errorf(
			"AUTH_SESSION_HOURS and AUTH_REMEMBER_DAYS must be at least 1")
	}
	// Below OWASP's floor a stored password is weaker than this code implies,
	// and a deployment that set this by accident should be told at startup.
	if iterations < 100_000 {
		return Auth{}, fmt.Errorf(
			"AUTH_PBKDF2_ITERATIONS is %d; PBKDF2-HMAC-SHA256 needs at least 100000",
			iterations)
	}

	microsoft, err := loadMicrosoft()
	if err != nil {
		return Auth{}, err
	}

	return Auth{
		Required:      required,
		SecureCookies: secure,
		TTL:           time.Duration(ttl) * time.Hour,
		RememberTTL:   time.Duration(remember) * 24 * time.Hour,
		Iterations:    iterations,
		Registration:  registration,
		TrustProxy:    trustProxy,
		Microsoft:     microsoft,
	}, nil
}

// loadMicrosoft reads the Microsoft sign-in, which is all or nothing: half of
// it configured is a button that fails at the callback, so it is refused at
// startup instead.
func loadMicrosoft() (Microsoft, error) {
	m := Microsoft{
		TenantID:     strings.TrimSpace(os.Getenv("AUTH_MICROSOFT_TENANT_ID")),
		ClientID:     strings.TrimSpace(os.Getenv("AUTH_MICROSOFT_CLIENT_ID")),
		ClientSecret: os.Getenv("AUTH_MICROSOFT_CLIENT_SECRET"),
		RedirectURL:  strings.TrimSpace(os.Getenv("AUTH_MICROSOFT_REDIRECT_URL")),
		NewAccounts:  envOr("AUTH_MICROSOFT_NEW_ACCOUNTS", "pending"),
		Authority: strings.TrimRight(
			envOr("AUTH_MICROSOFT_AUTHORITY", "https://login.microsoftonline.com"), "/"),
	}
	for _, domain := range strings.Split(os.Getenv("AUTH_MICROSOFT_DOMAINS"), ",") {
		if domain = strings.ToLower(strings.TrimSpace(domain)); domain != "" {
			m.Domains = append(m.Domains, strings.TrimPrefix(domain, "@"))
		}
	}
	if m.ClientID == "" && m.TenantID == "" && m.ClientSecret == "" {
		return Microsoft{}, nil
	}
	if m.ClientID == "" || m.TenantID == "" || m.ClientSecret == "" || m.RedirectURL == "" {
		return Microsoft{}, fmt.Errorf("Microsoft sign-in needs all four of " +
			"AUTH_MICROSOFT_TENANT_ID, AUTH_MICROSOFT_CLIENT_ID, " +
			"AUTH_MICROSOFT_CLIENT_SECRET and AUTH_MICROSOFT_REDIRECT_URL")
	}
	switch strings.ToLower(m.TenantID) {
	case "common", "organizations", "consumers":
		return Microsoft{}, fmt.Errorf("AUTH_MICROSOFT_TENANT_ID is %q, which lets in "+
			"accounts from every tenant; set the office's directory (tenant) ID", m.TenantID)
	}
	if u, err := url.Parse(m.RedirectURL); err != nil || u.Scheme == "" || u.Host == "" {
		return Microsoft{}, fmt.Errorf(
			"AUTH_MICROSOFT_REDIRECT_URL must be an absolute URL, got %q", m.RedirectURL)
	}
	switch m.NewAccounts {
	case "pending", "active", "refuse":
	default:
		return Microsoft{}, fmt.Errorf(
			"AUTH_MICROSOFT_NEW_ACCOUNTS must be pending, active or refuse, got %q",
			m.NewAccounts)
	}
	return m, nil
}

// loadPipelines reads the runner's settings.
//
// Disabled is the default and needs no other setting. Enabled without a
// readable project root is a startup failure rather than a button that returns
// an error hours later: whoever set PIPELINES_ENABLED meant for it to work.
func loadPipelines() (Pipelines, error) {
	enabled, err := boolEnv("PIPELINES_ENABLED", false)
	if err != nil {
		return Pipelines{}, err
	}
	if !enabled {
		return Pipelines{}, nil
	}

	root := os.Getenv("PIPELINES_ROOT")
	if root == "" {
		// The repository root, found by walking up from the working directory
		// looking for the uv project. `make dev-api` runs in services/api and
		// a deployment runs wherever it was unpacked, so neither a relative
		// default nor an absolute one would be right.
		root, err = findProjectRoot()
		if err != nil {
			return Pipelines{}, fmt.Errorf(
				"PIPELINES_ENABLED is set but the pipelines could not be found: %w; "+
					"set PIPELINES_ROOT to the repository root", err)
		}
	}
	root, err = filepath.Abs(root)
	if err != nil {
		return Pipelines{}, fmt.Errorf("PIPELINES_ROOT: %w", err)
	}
	if _, err := os.Stat(filepath.Join(root, "pipelines", "pyproject.toml")); err != nil {
		return Pipelines{}, fmt.Errorf(
			"PIPELINES_ROOT=%q does not look like the repository root: %w", root, err)
	}

	uv := envOr("PIPELINES_UV", "uv")
	// Checked now rather than on the first click, for the same reason: a
	// server that cannot run what it advertises should say so at startup.
	if !strings.ContainsRune(uv, os.PathSeparator) {
		if resolved, err := exec.LookPath(uv); err == nil {
			uv = resolved
		} else {
			return Pipelines{}, fmt.Errorf("PIPELINES_UV=%q is not on PATH: %w", uv, err)
		}
	}

	timeout, err := intEnv("PIPELINES_TIMEOUT_SECONDS", 1800)
	if err != nil {
		return Pipelines{}, err
	}
	if timeout < 1 {
		return Pipelines{}, fmt.Errorf(
			"PIPELINES_TIMEOUT_SECONDS must be at least 1, got %d", timeout)
	}
	concurrent, err := intEnv("PIPELINES_MAX_CONCURRENT", 2)
	if err != nil {
		return Pipelines{}, err
	}
	if concurrent < 1 {
		return Pipelines{}, fmt.Errorf(
			"PIPELINES_MAX_CONCURRENT must be at least 1, got %d", concurrent)
	}

	return Pipelines{
		Enabled:        true,
		Root:           root,
		UV:             uv,
		Timeout:        time.Duration(timeout) * time.Second,
		MaxConcurrent:  concurrent,
		MaxOutputLines: 500,
		History:        50,
	}, nil
}

// findProjectRoot walks up from the working directory for the uv project.
func findProjectRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "pipelines", "pyproject.toml")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("no pipelines/pyproject.toml above the working directory")
		}
		dir = parent
	}
}

// Addr is the listen address for the HTTP server.
func (c *Config) Addr() string { return fmt.Sprintf("%s:%d", c.Host, c.Port) }

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// boolEnv reads a flag. Only the words a person would actually write.
func boolEnv(key string, fallback bool) (bool, error) {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.ParseBool(raw)
	if err != nil {
		return false, fmt.Errorf("%s must be true or false, got %q", key, raw)
	}
	return v, nil
}

func intEnv(key string, fallback int) (int, error) {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be an integer, got %q", key, raw)
	}
	return v, nil
}
