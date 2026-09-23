# The data portal.
#
# Built from the repository root: the lockfile and the workspace manifest live
# there, and installing without them would resolve versions afresh and ship
# something nobody tested.
#
# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS build

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

# Manifests first. A change to a component should not reinstall the world.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/portal/package.json apps/portal/
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile

COPY apps/portal apps/portal
# The documentation the portal serves at /docs. It is read at build time — see
# apps/portal/src/lib/docs.ts — so the image needs the directory, not just the
# app.
COPY docs docs

# Read at build time and baked into the client bundle, which is why it is an
# argument here rather than an environment variable in compose: by the time the
# container starts, the browser's copy of this URL is already fixed.
ARG VITE_API_URL=http://localhost:8080
ENV VITE_API_URL=$VITE_API_URL
RUN pnpm --filter @terusan/portal build

FROM node:22-bookworm-slim AS runtime

# `vite build` emits a self-contained fetch handler — the server bundle imports
# nothing outside Node's own modules — so the runtime image carries the build
# output and the bridge that listens, and no node_modules at all.
WORKDIR /app
COPY --from=build /app/apps/portal/dist ./dist
COPY --from=build /app/apps/portal/server.mjs ./server.mjs

USER node

# server.mjs defaults to loopback, which inside a container serves nobody.
ENV HOST=0.0.0.0 \
    PORT=3000 \
    NODE_ENV=production

EXPOSE 3000

ENTRYPOINT ["node", "server.mjs"]
