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
# certificates every HTTPS fetch needs.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
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

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-extras

# Same reasoning as the API: spill and staging stay on the container's disk,
# never on the share.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin terusan \
    && mkdir -p /scratch && chown terusan:terusan /scratch
USER terusan

ENV SCRATCH_DIR=/scratch

ENTRYPOINT ["terusan"]
CMD ["--help"]
