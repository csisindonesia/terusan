-- 001_catalog: organizations, sources, datasets and versions.
--
-- PostgreSQL holds application and operational state only. The analytical
-- warehouse stays in Parquet and is not duplicated here (program.md §48).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------------
-- Organizations and users
-- ---------------------------------------------------------------------------

CREATE TABLE organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid REFERENCES organizations (id) ON DELETE SET NULL,
    email            citext,
    display_name     text NOT NULL,
    is_active        boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Source registry (program.md §16)
-- ---------------------------------------------------------------------------

CREATE TYPE source_type AS ENUM (
    'official_portal',
    'government_api',
    'scraping',
    'manual_upload',
    'news',
    'research_repository',
    'internal'
);

CREATE TYPE collection_method AS ENUM (
    'api',
    'scrape',
    'bulk_download',
    'manual_upload',
    'feed'
);

CREATE TYPE update_frequency AS ENUM (
    'realtime', 'daily', 'weekly', 'monthly', 'quarterly', 'annual', 'irregular'
);

CREATE TABLE sources (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug               text NOT NULL UNIQUE,
    name               text NOT NULL,
    organization       text,
    source_type        source_type NOT NULL,
    base_url           text,
    country            char(2),
    license            text,
    collection_method  collection_method,
    update_frequency   update_frequency,
    active             boolean NOT NULL DEFAULT true,
    notes              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sources_active_idx ON sources (active) WHERE active;
CREATE INDEX sources_type_idx ON sources (source_type);

-- ---------------------------------------------------------------------------
-- Datasets
-- ---------------------------------------------------------------------------

CREATE TYPE storage_layer AS ENUM (
    'raw', 'bronze', 'silver', 'gold', 'exports', 'temporary'
);

CREATE TYPE access_level AS ENUM (
    'public', 'registered', 'restricted', 'internal'
);

CREATE TYPE dataset_status AS ENUM (
    'draft', 'active', 'deprecated', 'retired'
);

CREATE TABLE datasets (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug             text NOT NULL UNIQUE,
    title            text NOT NULL,
    description      text,
    layer            storage_layer NOT NULL,

    -- Logical address within the layer, resolved to a physical path by the
    -- storage resolver (program.md §45.4). Never store a physical path here:
    -- it would pin the catalog to one backend.
    storage_path     text NOT NULL,

    access_level     access_level NOT NULL DEFAULT 'internal',
    status           dataset_status NOT NULL DEFAULT 'draft',

    source_id        uuid REFERENCES sources (id) ON DELETE RESTRICT,
    owner_id         uuid REFERENCES users (id) ON DELETE SET NULL,

    partition_keys   text[] NOT NULL DEFAULT '{}',
    tags             text[] NOT NULL DEFAULT '{}',

    row_count        bigint,
    size_bytes       bigint,
    temporal_start   date,
    temporal_end     date,

    license          text,
    citation         text,

    published_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT datasets_storage_path_relative
        CHECK (storage_path !~ '^([a-z0-9]+://|/)'),
    CONSTRAINT datasets_temporal_order
        CHECK (temporal_end IS NULL OR temporal_start IS NULL OR temporal_end >= temporal_start)
);

CREATE INDEX datasets_layer_idx ON datasets (layer);
CREATE INDEX datasets_access_idx ON datasets (access_level);
CREATE INDEX datasets_source_idx ON datasets (source_id);
CREATE INDEX datasets_tags_idx ON datasets USING gin (tags);

-- ---------------------------------------------------------------------------
-- Dataset versions (program.md §40)
-- ---------------------------------------------------------------------------

CREATE TYPE change_kind AS ENUM (
    'new_observations',
    'corrections',
    'source_revision',
    'schema_change',
    'methodology_change',
    'pipeline_change'
);

CREATE TABLE dataset_versions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id    uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    version       text NOT NULL,
    change_kinds  change_kind[] NOT NULL DEFAULT '{}',
    changelog     text,

    -- Content hash over the version's Parquet parts, for deduplication and
    -- cache invalidation (program.md §18, §49).
    content_hash  text,

    row_count     bigint,
    size_bytes    bigint,
    released_at   timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now(),

    UNIQUE (dataset_id, version)
);

CREATE INDEX dataset_versions_dataset_idx ON dataset_versions (dataset_id, released_at DESC);

-- ---------------------------------------------------------------------------
-- Schemas (program.md §41)
-- ---------------------------------------------------------------------------

CREATE TABLE dataset_schemas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id          uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    dataset_version_id  uuid REFERENCES dataset_versions (id) ON DELETE CASCADE,

    -- Arrow/Parquet field definitions: name, type, nullable, description, unit.
    fields              jsonb NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    UNIQUE (dataset_id, dataset_version_id)
);

COMMIT;
