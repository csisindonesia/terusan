# Transformations

DuckDB SQL that moves data between layers, one directory per target layer.

```text
bronze/   RAW → Bronze: extraction output, minimally typed
silver/   Bronze → Silver: normalization, deduplication, joins to dimensions
gold/     Silver → Gold: curation and pre-aggregation
```

Read and write paths are supplied by the storage resolver (program.md §45.4);
a query must not hardcode a physical path or bucket, or it stops working the
moment the lake moves between NAS and object storage.

Parameterize instead:

```sql
-- silver/observations.sql
CREATE OR REPLACE TABLE observations AS
SELECT * FROM read_parquet($bronze_statistics_glob);
```
