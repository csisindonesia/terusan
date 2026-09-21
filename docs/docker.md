# Running it in Docker

Three images — the API, the portal, the pipelines — and one lake on a shared
folder. Everything below assumes Docker Desktop or an engine with Compose v2.

## Once

```bash
cp .env.example .env
```

Then set one line in `.env`, the host path to the lake:

```bash
DATA_DIR=/Volumes/Shared Folder/terusan     # macOS, a mounted share
DATA_DIR=D:/Shared Folder/terusan           # Windows, forward slashes
DATA_DIR=/srv/shared/terusan                # Linux
```

Spaces need no quotes — Compose reads the rest of the line. There is no
default: a lake that quietly lands somewhere nobody meant is worse than a
container that refuses to start, so Compose refuses.

The share has to be one Docker is allowed to mount. On Docker Desktop that
means adding it under **Settings → Resources → File sharing**; a path outside
that list mounts as an empty directory rather than failing, which is how an
ingestion run ends up writing into nothing.

```bash
make docker-up          # build and run the portal and the API
make docker-ps          # what is running, and whether the API is healthy
make docker-logs        # follow them
make docker-down        # stop, keeping the lake and the database
```

The portal is then on <http://localhost:3000> and the API on
<http://localhost:8080>.

## The lake, and what stays off it

`DATA_DIR` is bind-mounted at `/data` in every container that touches figures,
and the storage layer is configured for it:

```
STORAGE_BACKEND=nas     a mount something else owns; an absolute path is required
STORAGE_PROFILE=shared-dev
STORAGE_ROOT=/data
SCRATCH_DIR=/scratch    a container-local volume, never the share
```

Scratch is separate on purpose. DuckDB spills there during a large query or a
compaction, and a spill over SMB or NFS is slow enough that the storage layer
refuses the arrangement outright (program.md §45.6). Losing it costs nothing —
it is rebuilt on the next run.

Writes are guarded rather than assumed. A shared profile refuses writes unless
a service says it means them, and only the pipelines container does
(`STORAGE_ALLOW_SHARED_WRITES=true`). An API container cannot mutate the lake
even if a handler one day tries.

Two things do live on the share beside the figures: `app.duckdb`, which holds
the shelf and the accounts, because a collection somebody saved is the only
copy of something a person wrote here.

## Running a pipeline

The pipelines image is a CLI, not a server, so it runs a command at a time:

```bash
make docker-ingest CMD="sources list"
make docker-ingest CMD="sources run bnpb-disaster --limit 5 --dry-run"
make docker-ingest CMD="warehouse extract"
make docker-ingest CMD="silver dimensions"
```

Or directly, which is the same thing:

```bash
docker compose run --rm pipelines sources run bnpb-disaster
```

For a scheduled run, point the host's scheduler at that command — the image
holds no cron of its own, because a container that schedules itself is a
container nobody can see the schedule of.

The portal's "run ingestion now" button stays off here. That endpoint starts
scrapers on the host and has nothing in front of it (program.md §34), and the
person clicking a button on a shared deployment is not necessarily the person
who owns the machine.

## PostgreSQL and Redis

Both are optional and both sit behind a Compose profile, so the default stack
is the three services and nothing else.

```bash
docker compose --profile catalog up -d postgres   # the catalog
docker compose --profile cache up -d redis        # rendered-response cache
```

The migrations apply themselves the first time PostgreSQL initialises its data
directory — only the `*.up.sql` half, via
[`infra/docker/postgres-init.sh`](../infra/docker/postgres-init.sh). Mounting
`db/migrations` straight into the init directory would run
`001_catalog.down.sql` immediately after `001_catalog.up.sql` and leave an
empty schema with no error anywhere.

Point the other services at them by setting `DATABASE_URL` and `REDIS_URL` in
`.env`, using the service names as hosts:

```bash
DATABASE_URL=postgres://terusan:terusan@postgres:5432/terusan?sslmode=disable
REDIS_URL=redis://redis:6379/0
```

## Publishing it

`PUBLIC_API_URL` is baked into the portal's client bundle **at build time**,
because by the time a browser runs it the URL is already fixed. Behind a
hostname, set it before building rather than after:

```bash
PUBLIC_API_URL=https://terusan.example.org API_CORS_ORIGINS=https://terusan.example.org \
  docker compose up -d --build
```

`http://api:8080` is never the right value for it: that name resolves between
containers, not in anybody's browser.

## What the images are

| | base | why |
|---|---|---|
| API | `debian:bookworm-slim` | DuckDB is a C library, so the binary is not static and the image needs glibc |
| portal | `node:22-bookworm-slim` | `vite build` emits a self-contained handler, so the runtime carries `dist/` and `server.mjs` and no `node_modules` |
| pipelines | `python:3.12-slim` | `uv sync --frozen`, the project installed editable so the reference CSVs stay findable beside it |

All three build from the repository root. The pipelines image needs
`reference/` — the geography and commodity registries are committed data read
from beside the package, not from inside it — and the portal needs the
lockfile and the workspace manifest, which live at the root.

Each runs as an unprivileged user with a fixed uid. On a share that enforces
ownership, that uid is what needs write access to `DATA_DIR`.
