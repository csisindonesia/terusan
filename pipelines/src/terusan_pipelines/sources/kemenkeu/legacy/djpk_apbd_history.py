# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Pull the full DJPK APBD national-aggregate time series, July 2011 through the
current month, using the same endpoint/parsing as djpk_apbd.py.

Notes:
- "periode" is cumulative realization within a fiscal year (Jan..periode), not
  a standalone monthly figure. This matches the catalogue's stated "Monthly"
  frequency for this data.
- Site's own year filter only goes back to 2011; before July 2011 the portal
  doesn't offer the year at all in some earlier drafts, but 2011 is listed in
  the dropdown range (2011-2027) so we start there per user request (July
  2011).
- Writes progress to stdout and saves incrementally so a partial run can be
  resumed (skips periods already present in the output CSV).
"""

import csv
import datetime
import os
import time

import requests

from .djpk_apbd import extract_indicators, fetch_raw, parse_rows

FIELDS = ["periode", "tahun", "indicator", "anggaran", "realisasi", "note"]
DELAY = 0.4
MAX_RETRIES = 3


def month_range(start_year, start_month, end_year, end_month):
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def load_done_periods(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add((int(row["tahun"]), int(row["periode"])))
    return done


def append_rows(path, rows, write_header):
    mode = "a" if os.path.exists(path) and not write_header else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(rows)


def main():
    today = datetime.date.today()
    start_year, start_month = 2011, 7
    end_year, end_month = today.year, today.month

    months = list(month_range(start_year, start_month, end_year, end_month))
    print(f"Target range: {start_year}-{start_month:02d} .. {end_year}-{end_month:02d} ({len(months)} periods)")

    done = load_done_periods(OUT_PATH)
    if done:
        print(f"Resuming: {len(done)} periods already saved in {OUT_PATH}")

    write_header = not os.path.exists(OUT_PATH)
    n_ok, n_fail, n_skip = 0, 0, 0
    failures = []

    for tahun, periode in months:
        if (tahun, periode) in done:
            n_skip += 1
            continue

        indicators = None
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = fetch_raw(periode, tahun)
                records = parse_rows(raw)
                indicators = extract_indicators(records)
                break
            except (requests.exceptions.RequestException, Exception) as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(1.0 * attempt)

        if indicators is None or len(indicators) == 0:
            n_fail += 1
            failures.append((tahun, periode, str(last_exc)))
            print(f"  [{tahun}-{periode:02d}] FAILED: {last_exc}")
            time.sleep(DELAY)
            continue

        rows = [
            {
                "periode": periode,
                "tahun": tahun,
                "indicator": label,
                "anggaran": vals["anggaran"],
                "realisasi": vals["realisasi"],
                "note": vals.get("note", ""),
            }
            for label, vals in indicators.items()
        ]
        append_rows(OUT_PATH, rows, write_header)
        write_header = False
        n_ok += 1
        print(f"  [{tahun}-{periode:02d}] ok ({len(indicators)} indicators)")
        time.sleep(DELAY)

    print()
    print(f"Done. ok={n_ok} failed={n_fail} skipped(already had)={n_skip}")
    if failures:
        print("Failed periods:", failures)
    print(f"Output: {OUT_PATH}")


