# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Terusan is a research data warehouse, catalog and serving layer. Full spec: [program.md](program.md) (section numbers like "§45" in comments and docs refer to it). Longer walkthroughs: [docs/running-it.md](docs/running-it.md), [docs/adding-a-source.md](docs/adding-a-source.md), [docs/design.md](docs/design.md) (portal page rules).

## Layout

```text
Sources → RAW → Bronze → Silver → Gold → DuckDB over Parquet → Go API → portal / BI tools
```

| Part | Where | Stack |
|---|---|---|
| Pipelines (ingest, extract, normalize, CLI `terusan`) | `pipelines/` | Python 3.12+, uv, typer, DuckDB, pyarrow |
| Serving API | `services/api/` | Go 1.26, `net/http` mux in `internal/httpapi/server.go`, DuckDB |
| Portal | `apps/portal/` | TanStack Start, React 19, shadcn/ui, Tailwind 4; vite proxies `/v1` to the API on :8080 |
| App catalog DB | `db/migrations/` | PostgreSQL 18 (optional for pipelines) |
| Reference data | `reference/` | countries, aggregates, 38 Indonesian provinces with BPS codes and aliases |

pnpm workspace (`apps/*`, `packages/*`), Node 22+ via corepack.

## Commands

```bash
make setup                 # toolchains + ./.data layers
make dev                   # API :8080 + portal :3000, both hot-reloading
make test                  # Go + Python
make lint                  # gofmt -l, go vet, ruff check, ruff format --check, tsc, prettier --check
make fmt                   # gofmt -w, ruff format/fix, prettier
make smoke                 # does a running `make dev` stack actually render rows

# single tests
cd pipelines && uv run pytest tests/test_adb.py -q
cd pipelines && uv run pytest tests/test_adb.py::test_name -q
cd services/api && go test ./internal/httpapi/ -run TestName -count=1 -v
pnpm --filter @terusan/portal typecheck

# pipeline CLI (run from pipelines/, or `uv --project pipelines run terusan ...` from root)
uv run terusan sources list
uv run terusan sources run <slug> --dry-run --limit 1     # → RAW
uv run terusan warehouse extract [statistics <slug>]      # RAW → Bronze
uv run terusan silver normalize <INDICATOR> ...           # Bronze → Silver (one series)
uv run terusan silver normalize-each --by <col> ...       # one Silver series per distinct value
uv run terusan silver dimensions | documents              # geography/commodity dims, document catalogue
uv run terusan warehouse query "SELECT ... FROM silver_observations LIMIT 5"
```

Per-source end-to-end targets exist (`make adb-silver`, `bps-silver`, `pihps-silver`, ...): typically `sources run` → `warehouse extract` → `scripts/normalize-<source>.sh`. The normalize scripts hold the declared column mapping for each source.

`make test-lake` / `make eval-assistant` run Go tests against the real local lake (slow, need data).

## Architecture rules the code relies on

- **All physical paths come from the storage resolver** — Go `services/api/internal/storage`, Python `terusan_pipelines.storage`. Never build lake paths by hand; SQL in `sql/` takes paths as parameters. Both resolvers are tested against the same fixture, `fixtures/storage/contract.json`; change one, change both.
- **`STORAGE_ROOT` absent → `local` (`./.data`).** Non-local profiles refuse writes unless `STORAGE_ALLOW_SHARED_WRITES=true`.
- **RAW is immutable and content-addressed.** Landing checks magic bytes (an HTML error page named `.xls` is refused). Re-running unchanged sources writes nothing; extraction skips documents already in Bronze at the current parser version.
- **A source fetches, it does not parse.** Sources live in `pipelines/src/terusan_pipelines/sources/<pkg>/`, yield `Artifact`s, and self-register by subclassing `Source` (registry imports every submodule). Prefer the engines in `sources/portals.py` (`ApiSource`, `FileIndexSource`, `PageSource`, `GatedSource`) over hand-written `collect`. Unreachable portals are still registered as `GatedSource(active=False)` with an actionable `access` sentence. Use `wants(ctx, "full")` for boolean params — CLI params arrive as strings.
- **Extractors** are in `extract/`, ordered in `DEFAULT_EXTRACTORS` (`extract/runner.py`): first claimer wins, so source-specific extractors must come before the generic format readers.
- **Bronze is all text; Silver decides types.** Silver values are `decimal128`, never float. Normalization refuses to guess: number format (`--number-format id|en`; `1.234` is ambiguous), place names (unresolved stays unresolved with raw text), and missing markers (`-`, `x`, `...` are distinct statuses, never zero). Column mapping is declared, not inferred.
- **Silver observations are partitioned by `indicator_id`**, so a normalize run replaces one series idempotently.
- **Dataset titles/descriptions** are declared in `pipelines/src/terusan_pipelines/datasets.py`; undeclared datasets still work with fallback names.
- **Pipeline catalog is optional.** Without `DATABASE_URL`, runs record nothing to Postgres; scheduled runs use `--require-catalog` / `REQUIRE_CATALOG=1`.
- **API:** every response uses the `data` + `meta`|`error` envelope (program.md §53). Values are serialized as **strings**. Every SQL value is bound; non-bindable parts (e.g. `order`) are chosen server-side from a whitelisted key. Responses optionally cached in Redis (`REDIS_URL`); failures are never cached.
- **Portal** (see docs/design.md): never render a missing value as blank/zero — show its status; keep figures as strings and format via `lib/format.ts`; the URL is the state (parse with `lib/search-params.ts`, params typed `string | number`); display names derive from keys via `lib/labels.ts`. Typecheck does not catch layout or value bugs — run `make smoke` and look at the page.
- Vendored agency scrapers are excluded from ruff restyling on purpose; don't reformat them.

## Conventions

- Commits: Conventional Commits with a scope, e.g. `feat(portal): ...`, `fix(bps): ...`.
- Comments and docs explain *why* in full prose sentences; match that style.
