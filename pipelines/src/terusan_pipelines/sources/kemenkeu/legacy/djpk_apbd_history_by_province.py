# Vendored from an earlier collection of standalone scrapers, with the entry
# point and the hardcoded output path removed — landing is the platform's job
# now (program.md §2.1), and these modules keep only what they are good at:
# knowing how to reach a source and how to read what it returns.
#
# The fetch functions are what the Source beside this directory calls. The parse
# functions are kept for the extractor that will read the landed bytes; nothing
# calls them yet.

"""
Pull DJPK APBD data for every (province x month x year) combination, July
2011 through the current month, for all 38 provinces. Reuses the fetch/parse/
extract logic from djpk_apbd.py (which already auto-detects the legacy
pre-2016 vs current 2016+ Akun naming scheme, see that module's docstring).

6,954 requests total (183 periods x 38 provinces) - large enough to warrant
modest concurrency (a small thread pool) rather than one request at a time,
while staying polite to a government server (bounded worker count + small
per-request delay, generous retries with backoff on failure).

Resumable: on each run, periods/provinces already present in the output CSV
are skipped, so a killed/interrupted run can just be re-invoked.
"""

import concurrent.futures
import csv
import datetime
import os
import threading
import time

from .djpk_apbd import PROVINCES, extract_indicators, fetch_raw, parse_rows

FIELDS = ["provinsi_code", "provinsi_name", "periode", "tahun", "indicator", "anggaran", "realisasi", "note"]
MAX_WORKERS = 6
PER_REQUEST_DELAY = 0.15  # small politeness delay applied per worker thread
MAX_RETRIES = 4


def month_range(start_year, start_month, end_year, end_month):
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def load_done(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add((int(row["tahun"]), int(row["periode"]), row["provinsi_code"]))
    return done


write_lock = threading.Lock()


def append_rows(path, rows, write_header):
    with write_lock:
        mode = "a" if os.path.exists(path) and not write_header else "w"
        with open(path, mode, newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(rows)


def fetch_one(tahun, periode, code, name):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(PER_REQUEST_DELAY)
            raw = fetch_raw(periode, tahun, provinsi=code)
            records = parse_rows(raw)
            indicators = extract_indicators(records)
            return (tahun, periode, code, name, indicators, None)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(1.0 * attempt)
    return (tahun, periode, code, name, None, last_exc)


def main():
    today = datetime.date.today()
    start_year, start_month = 2011, 7
    end_year, end_month = today.year, today.month

    months = list(month_range(start_year, start_month, end_year, end_month))
    provinces = list(PROVINCES.items())
    total = len(months) * len(provinces)
    print(f"Target: {len(months)} periods x {len(provinces)} provinces = {total} requests")

    done = load_done(OUT_PATH)
    print(f"Already done: {len(done)}")

    jobs = [
        (tahun, periode, code, name)
        for tahun, periode in months
        for code, name in provinces
        if (tahun, periode, code) not in done
    ]
    print(f"Remaining: {len(jobs)}")

    write_header = not os.path.exists(OUT_PATH)
    n_ok, n_fail = 0, 0
    failures = []
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_one, *job) for job in jobs]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            tahun, periode, code, name, indicators, error = fut.result()
            if indicators is None or len(indicators) == 0:
                n_fail += 1
                failures.append((tahun, periode, code, str(error)))
                print(f"  [{i}/{len(jobs)}] {tahun}-{periode:02d} {code} {name}: FAILED ({error})")
                continue
            rows = [
                {
                    "provinsi_code": code,
                    "provinsi_name": name,
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
            if i % 100 == 0 or i == len(jobs):
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(jobs) - i) / rate if rate > 0 else float("inf")
                print(f"  [{i}/{len(jobs)}] ok={n_ok} fail={n_fail} rate={rate:.1f}/s eta={eta/60:.1f}min")

    print()
    print(f"Done. ok={n_ok} failed={n_fail} (already had {len(done)})")
    if failures:
        print(f"{len(failures)} failures (first 20): {failures[:20]}")
    print(f"Output: {OUT_PATH}")


