# The serving layer.
#
# Built from the repository root, not from this directory: `docker build
# services/api` would work today and break the first time the API reads
# anything the rest of the repo owns.
#
# syntax=docker/dockerfile:1

FROM golang:1.26-bookworm AS build

WORKDIR /src

# Dependencies first, so editing a handler does not re-download the module
# graph on every build.
COPY services/api/go.mod services/api/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download

COPY services/api/ ./

# DuckDB is a C library with Go bindings, so this cannot be a static build.
# The driver carries a prebuilt library per platform, which is why the runtime
# image below is glibc rather than distroless static.
ENV CGO_ENABLED=1
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go build -trimpath -o /out/api ./cmd/api

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Scratch is DuckDB's spill and staging area. It belongs on the container's own
# disk: pointed at a network share it degrades badly enough that the storage
# layer refuses to do it (program.md §45.6).
#
# 1777 rather than owned by that user: a deployment may have to run this image
# under an account the host recognises — a NAS that enforces share permissions
# by uid will not let 10001 near the lake — and a scratch directory only one
# uid can write turns that into a crash on the first spill.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin terusan \
    && mkdir -p /scratch && chmod 1777 /scratch

COPY --from=build /out/api /usr/local/bin/api

USER terusan
WORKDIR /home/terusan

# Bound to every interface rather than to loopback: inside a container loopback
# is reachable only from the container itself, which serves nobody.
ENV API_HOST=0.0.0.0 \
    API_PORT=8080 \
    SCRATCH_DIR=/scratch

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/api"]
