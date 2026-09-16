BEGIN;

DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS pipeline_runs;
DROP TABLE IF EXISTS pipeline_definitions;
DROP TABLE IF EXISTS saved_datasets;
DROP TABLE IF EXISTS permissions;
DROP TABLE IF EXISTS api_keys;

DROP TYPE IF EXISTS run_status;
DROP TYPE IF EXISTS dataset_grant;
DROP TYPE IF EXISTS principal_kind;

COMMIT;
