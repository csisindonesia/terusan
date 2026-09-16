# Dataset schemas

Field definitions for each dataset, one file per dataset, grouped by domain.
These are the authority for what a dataset's Parquet contains: name, type,
nullability, unit and description per field.

They are committed because schema evolution has to be reviewable
(program.md §41) — a column that changes type between versions should show up
in a diff, not in a failing query. The catalog mirrors them into
`dataset_schemas` at publish time.
