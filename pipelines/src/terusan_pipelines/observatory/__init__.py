"""The Government Content Restriction Observatory.

Measures what governments asked platforms to restrict in Indonesia, and what
Komdigi says it handled, without adding the two together
(docs/government_content_restriction_observatory.md). It is built on the
warehouse rather than beside it:

- **discover and fetch** are the sources in `sources/transparency/` and
  `sources/komdigi/`, which land each report in RAW, content-addressed, with
  its provenance;
- **parse** is `extract/transparency.py`, which reads each report's own shape
  into Bronze records and names the publisher's label on every one;
- **normalize and validate** are the adapters here, which turn those records
  into `restriction_observations` — one figure, one metric, one unit, one
  period, traceable to the report it came from — and quarantine what they
  cannot vouch for rather than coercing it.

The as-published country series also reach Silver's ordinary observations
through `scripts/normalize-transparency.sh`, so they appear in the catalogue
like any other dataset. The tables here are the observatory's own: they carry
the requester, the scope and the comparability group the ordinary schema has
no room for.
"""
