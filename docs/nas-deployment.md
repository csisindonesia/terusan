# Running it on the NAS

The deployment: the whole stack on a UGREEN NASync (UGOS Pro), published at
<https://terusan.csis.or.id> through a Cloudflare Tunnel. The box holds both
halves of the thing — the lake on its share, the services in containers beside
it — so the figures and the server that reads them are never a network apart.

```
                     Cloudflare edge
                            │  (the only way in; no port is forwarded)
                      cloudflared
                            │  docker network
              ┌─────────────┴─────────────┐
           portal:3000                 api:8080 ── postgres:5432
                                          │
                                   /volume2/terusan/lake   (bind mount)
```

## What lives where

```
/volume2/terusan/
  app/           the repository, copied from a laptop by scripts/nas-deploy.sh
  stack/         what the UGOS Docker project runs:
  stack/docker-compose.yml   compose.yaml + the NAS overlay, merged and resolved
  stack/.env     written on the first deploy, never overwritten after
  lake/          raw/ bronze/ silver/ gold/ exports/ temporary/, and app.duckdb
  cloudflared/   config.yml and credentials.json, mounted read-only
```

The project directory is separate from the repository because the UGOS UI is
given a path and runs the `docker-compose.yml` it finds there; a rendered file
dropped into the repository would sit next to the one it was rendered from.
Build contexts inside it are absolute paths under `app/`, so the two stay
connected.

The lake is a bind mount rather than a Docker volume for the reason
compose.yaml gives: the figures are meant to be readable over the share by
people and by other machines, not sealed inside Docker's storage. Scratch is
the opposite — a container-local volume, because DuckDB spills there and a
spill over a share is slow enough that the storage layer refuses it
(program.md §45.6).

## Once

On the NAS, in UGOS:

1. **Control Panel → Terminal → SSH.** Enable it. The deploy copies files and
   renders the compose file over SSH; nothing below works without it.
2. **App Center → Docker.** Installed and started.
3. From a clone: `ssh-copy-id dev@192.168.1.212`, so the deploy does not ask
   for a password per step.

Then:

```bash
make nas-tunnel-install   # create the tunnel, hand its credentials to the NAS
make nas-deploy           # copy the repository, render stack/docker-compose.yml
```

and in UGOS, **Docker → Project → Create**:

```
Name          terusan
Path          /volume2/terusan/stack
Compose file  the docker-compose.yml already there — do not paste over it
```

then **Build and start**. The first build pulls a Go, a Node and a Python
base and takes a while; after that only what changed is rebuilt.

`nas-tunnel-install` refuses to replace a hostname that already resolves
unless it is asked by name (`OVERWRITE_DNS=1`), because doing so takes the
name away from whatever answers there now — the laptop's own `terusan` tunnel,
if that is what has been serving it.

## Why the UGOS UI and not `docker compose up`

On UGOS the login user is not in the `docker` group and `sudo` wants a
password, so the socket is out of reach over SSH. Two ways round it were
available and both were declined for this box: adding the user to the `docker`
group, and a sudoers line for the docker binary — each of which is
root-equivalent on a machine that also holds the files.

What survives that restriction is enough. `docker compose config` only reads
files, so the merge of `compose.yaml` and the NAS overlay happens on the box
with every path and variable resolved, and the UI starts the result. The
script still starts the stack itself where the socket *is* reachable, so
nothing has to change if the box is set up differently later.

One consequence worth knowing: the profile markers are stripped out of the
rendered file. `config` keeps them, and a project started the plain way would
bring up the API and the portal and silently skip the catalog and the
connector.

The other: `docker compose run --rm pipelines ...` is not available, so the
pipelines container runs idle (`sleep infinity`) and a pipeline is started
inside it instead:

```bash
docker exec terusan-pipelines-1 terusan sources run bnpb-disaster
```

from the container's terminal in the UGOS UI, or from a UGOS scheduled task —
both of which run as root. That is also how the daily ingestion should be
scheduled here: **Control Panel → Task Scheduler**, rather than the launchd
agent `make schedule-install` writes on a laptop.

## Afterwards

```bash
make nas-deploy                      # after a commit: copy, re-render
                                     # then: UGOS → project terusan → Rebuild
make nas-status                      # the box, the lake, the hostname
make nas-ps / nas-logs               # over SSH where the socket allows it,
                                     # otherwise the UI
make nas-ingest CMD="sources list"
make nas-shell                       # a shell in /volume2/terusan/app
```

Every target takes `NAS_HOST`, `NAS_USER`, `NAS_PORT` and `NAS_DIR`, so a
second box needs no edit:

```bash
make nas-deploy NAS_HOST=192.168.1.50 NAS_DIR=/volume1/terusan
```

## What the overlay changes

`infra/nas/compose.nas.yaml` is an overlay on `compose.yaml`, not a second
copy of it. Five differences:

- **The connector.** `cloudflared` runs beside the services and reaches them
  by container name, so nothing has to be published on the LAN for the
  hostname to work. Its ingress rules are `infra/cloudflare/config.nas.yml`,
  rendered into `/volume2/terusan/cloudflared/config.yml` — a locally-managed
  configuration rather than a dashboard token, so what is published is
  reviewable in the repository and moves with it.
- **One origin, split by path.** `^/(v1|healthz|readyz)(/|$)` reaches the API
  and everything else reaches the portal, which is what keeps the session
  cookie same-site and CORS out of the picture. `API_CORS_ORIGINS` is the
  https hostname, an allow-list of one.
- **`AUTH_SECURE_COOKIES=true`.** TLS is terminated at Cloudflare's edge; the
  API only ever sees plain http and cannot work out on its own that the portal
  is served over https.
- **The catalog is not optional.** PostgreSQL runs under the `catalog` profile
  and the pipelines container is given its URL, because a scheduled ingestion
  that cannot record its history is how a pipeline stops for three weeks
  without anyone noticing.
- **The pipelines container waits instead of running.** `sleep infinity` and a
  restart policy, because `compose run` needs a socket nobody here has; see
  above.

Logs are capped at 10 MB × 5 per container. A container left running for a
year on an 8 GB box should not be able to fill the volume with its own output.

## The ports UGOS already owns

80, 443, 5000, 5001, 9443 and 9999 belong to the NAS itself — 9999 is the UGOS
desktop. The stack takes 3000, 8080 and 5432 on the LAN for debugging from the
house; the tunnel uses none of them. Change them in `app/.env` on the box if
something else claims one.

## Secrets

`app/.env` holds the catalog password, generated on the first deploy and
written 0600. It is not synced from the laptop and not in git — a redeploy
leaves it alone, because PostgreSQL keeps the password it was initialised with
and rolling one side of that pair breaks the API's catalog connection in a way
that says nothing about passwords.

`cloudflared/credentials.json` is a bearer secret for the hostname: 0600, and
the directory is mounted read-only so a compromised connector cannot rewrite
its own ingress.

## When the hostname is down

```bash
make nas-status     # containers here, hostname there
make nas-logs
```

- Cloudflare says 1033 and the containers are up → the connector cannot reach
  the edge, or it never started: check `credentials.json` exists and that
  `docker compose ps` shows `cloudflared` running rather than restarting.
- The hostname answers but every page is a 502 → the connector is up and the
  service behind the matching ingress rule is not. `nas-ps` shows which.
- `/v1/...` returns the portal's HTML → the path rule is not matching; the
  API rule has to come first in `config.nas.yml`.
