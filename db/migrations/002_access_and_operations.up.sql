-- 002_access_and_operations: API keys, permissions, audit, pipelines.
--
-- The same access policy must apply to portal, API, export, SQL and AI
-- (program.md §36) — hence one permissions table, not one per surface.

BEGIN;

-- ---------------------------------------------------------------------------
-- API keys (program.md §34)
-- ---------------------------------------------------------------------------

CREATE TABLE api_keys (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid REFERENCES users (id) ON DELETE CASCADE,
    organization_id  uuid REFERENCES organizations (id) ON DELETE CASCADE,

    name             text NOT NULL,
    -- Only the hash is stored; the key itself is shown once at creation.
    key_hash         bytea NOT NULL UNIQUE,
    -- Short non-secret prefix so a key can be identified in logs and UI
    -- without storing anything that grants access.
    key_prefix       text NOT NULL,

    scopes           text[] NOT NULL DEFAULT '{}',
    rate_limit_tier  text NOT NULL DEFAULT 'default',

    last_used_at     timestamptz,
    expires_at       timestamptz,
    revoked_at       timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT api_keys_have_an_owner
        CHECK (user_id IS NOT NULL OR organization_id IS NOT NULL)
);

CREATE INDEX api_keys_prefix_idx ON api_keys (key_prefix);
CREATE INDEX api_keys_live_idx ON api_keys (user_id) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- Permissions (program.md §35, §36)
-- ---------------------------------------------------------------------------

CREATE TYPE principal_kind AS ENUM ('user', 'organization', 'api_key', 'anonymous');
CREATE TYPE dataset_grant AS ENUM ('read', 'download', 'query', 'manage');

CREATE TABLE permissions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_kind  principal_kind NOT NULL,
    principal_id    uuid,
    dataset_id      uuid REFERENCES datasets (id) ON DELETE CASCADE,
    grant_type      dataset_grant NOT NULL,
    granted_by      uuid REFERENCES users (id) ON DELETE SET NULL,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT permissions_principal_id_presence
        CHECK ((principal_kind = 'anonymous') = (principal_id IS NULL))
);

CREATE UNIQUE INDEX permissions_unique_grant
    ON permissions (principal_kind, coalesce(principal_id, '00000000-0000-0000-0000-000000000000'::uuid), dataset_id, grant_type);

-- ---------------------------------------------------------------------------
-- Saved datasets (portal bookmarks)
-- ---------------------------------------------------------------------------

CREATE TABLE saved_datasets (
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    dataset_id  uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, dataset_id)
);

-- ---------------------------------------------------------------------------
-- Pipelines (program.md §38, §39)
-- ---------------------------------------------------------------------------

CREATE TABLE pipeline_definitions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          text NOT NULL UNIQUE,
    name          text NOT NULL,
    source_id     uuid REFERENCES sources (id) ON DELETE RESTRICT,
    target_layer  storage_layer NOT NULL,

    -- Declarative pipeline body, version-controlled under pipelines/.
    -- Stored here so a run can be reproduced against the definition that
    -- produced it, not the current one.
    definition    jsonb NOT NULL,
    schedule      text,
    enabled       boolean NOT NULL DEFAULT true,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TYPE run_status AS ENUM ('pending', 'running', 'succeeded', 'failed', 'cancelled');

CREATE TABLE pipeline_runs (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_definition_id  uuid NOT NULL REFERENCES pipeline_definitions (id) ON DELETE CASCADE,
    dataset_version_id      uuid REFERENCES dataset_versions (id) ON DELETE SET NULL,

    status                  run_status NOT NULL DEFAULT 'pending',
    trigger                 text NOT NULL DEFAULT 'manual',

    -- Recorded per run so an output can be traced back to the exact code
    -- that produced it (program.md §17, §44).
    pipeline_version        text,
    parser_version          text,

    records_in              bigint,
    records_out             bigint,
    bytes_written           bigint,

    error_message           text,
    log_path                text,

    started_at              timestamptz,
    finished_at             timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pipeline_runs_finish_after_start
        CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX pipeline_runs_definition_idx
    ON pipeline_runs (pipeline_definition_id, created_at DESC);
CREATE INDEX pipeline_runs_unfinished_idx
    ON pipeline_runs (status) WHERE status IN ('pending', 'running');

-- ---------------------------------------------------------------------------
-- Audit log (program.md §51)
-- ---------------------------------------------------------------------------

CREATE TABLE audit_logs (
    id              bigserial PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT now(),

    actor_kind      principal_kind NOT NULL,
    actor_id        uuid,
    api_key_id      uuid REFERENCES api_keys (id) ON DELETE SET NULL,

    action          text NOT NULL,
    resource_type   text NOT NULL,
    resource_id     text,

    surface         text,
    ip_address      inet,
    user_agent      text,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_logs_occurred_idx ON audit_logs (occurred_at DESC);
CREATE INDEX audit_logs_actor_idx ON audit_logs (actor_kind, actor_id, occurred_at DESC);
CREATE INDEX audit_logs_resource_idx ON audit_logs (resource_type, resource_id, occurred_at DESC);

COMMIT;
