BEGIN;

DROP TABLE IF EXISTS dataset_schemas;
DROP TABLE IF EXISTS dataset_versions;
DROP TABLE IF EXISTS datasets;
DROP TABLE IF EXISTS sources;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS organizations;

DROP TYPE IF EXISTS change_kind;
DROP TYPE IF EXISTS dataset_status;
DROP TYPE IF EXISTS access_level;
DROP TYPE IF EXISTS storage_layer;
DROP TYPE IF EXISTS update_frequency;
DROP TYPE IF EXISTS collection_method;
DROP TYPE IF EXISTS source_type;

COMMIT;
