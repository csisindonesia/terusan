-- Postgres cannot drop a value from an enum. Reversing this means recreating
-- the type and rewriting every column that uses it, which is not worth
-- automating for an additive change — so the down migration is deliberately a
-- no-op and says so.
SELECT 1;
