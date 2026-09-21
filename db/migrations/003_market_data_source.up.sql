-- A commercial market-data API is not a government one.
--
-- IHSG arrives through Yahoo Finance, which aggregates the exchange rather than
-- publishing on its behalf. Filing it under `government_api` would make the
-- registry claim a provenance it does not have, and provenance is the one thing
-- this warehouse exists to keep straight (program.md §16).
ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'market_data';
