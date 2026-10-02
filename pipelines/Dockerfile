# The pipelines: acquisition, extraction and normalization.
#
# Not a server. This image holds the `terusan` CLI and is run a command at a
# time — `docker compose run --rm pipelines sources run bnpb-disaster` — or on
# a schedule by whatever runs the host.
#
# Built from the repository root, because `reference/` sits beside `pipelines/`
# and the dimension registries are read from there rather than from the
# package: geography and commodities are committed data, not code.
#
# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

# `curl` for the odd source that needs a redirect followed by hand, and the
# certificates every HTTPS fetch needs. Tesseract with its Indonesian model for
# the holiday decrees, which KemenPANRB publishes as scans with no text layer.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
        tesseract-ocr tesseract-ocr-ind \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app/pipelines

# `uv sync` installs the project itself editable, which is what keeps the
# dimension registries findable: `reference.py` locates them four directories
# above its own file, and that arithmetic only lands on /app/reference while
# the package is still at /app/pipelines/src.
#
# Dependencies before the source, so editing a scraper does not rebuild the
# environment. `--all-extras`: the agency sources need openpyxl and friends,
# and an image that cannot read a workbook is an image that cannot run half of
# what is registered.
COPY pipelines/pyproject.toml pipelines/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-extras --no-install-project

COPY pipelines/ ./
COPY reference/ /app/reference/

# The project root, said outright. `project_root()` finds a checkout by the
# Makefile beside `pipelines/` and `reference/`, and the image carries no
# Makefile, so it fell back to the working directory, /app/pipelines. Files
# found from the root were then looked for where they are not: the news
# keyword dictionary among them, which made every article extracted here
# carry no coding at all.
ENV TERUSAN_ROOT=/app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-extras

# The browser SP2KP's crosstab is downloaded through. The `browser` extra
# brings the Playwright package; the browser itself is a separate download,
# and without it the source failed every hour once the NAS kept a schedule.
# The headless shell only, which is all a download needs, and its system
# libraries with it. Put outside any home directory, since the uid that runs
# this is the host's choice.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

# The scheduled run and the normalize scripts it calls, so the container can
# keep its own schedule (scripts/scheduler.sh) rather than wait for a host
# that has none. They call `uv --project pipelines run ...`, as on a laptop;
# UV_NO_SYNC makes that run straight from /opt/venv instead of trying to sync
# an environment that belongs to root while this runs as someone else.
# /app/.cache, made writable below, holds daily.sh's lock and default logs.
COPY scripts/ /app/scripts/
ENV UV_NO_SYNC=1

# Same reasoning as the API: spill and staging stay on the container's disk,
# never on the share.
# 1777 for the reason services/api/Dockerfile gives: the uid this runs as is
# the host's business, not the image's.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin terusan \
    && mkdir -p /scratch /app/.cache && chmod 1777 /scratch /app/.cache
USER terusan

ENV SCRATCH_DIR=/scratch

ENTRYPOINT ["terusan"]
CMD ["--help"]
