"""Fetch IFC project disclosure pages and cache the raw HTML.

Cache-only: does not parse anything. Safe to interrupt and re-run — any
project that already has a cache file is skipped. Reads the raw CSV, filters
to the target industries, dedupes by Project Number, and fetches whatever
isn't already cached.

Usage:
    python scripts/01_scrape.py            # full run
    python scripts/01_scrape.py --limit 20  # first 20 projects only
"""
import argparse
import csv
import os
import sys
import time

import requests

from common import (
    CACHE_DIR,
    FAILED_CSV,
    USER_AGENT,
    cache_path,
    load_filtered_rows,
)

RATE_LIMIT_SECONDS = 1.0
MAX_RETRIES = 3
TIMEOUT = 30
PROGRESS_EVERY = 50


def fetch_with_retries(session, url):
    """Return (html, status_code) on success, or (None, status_code) on
    permanent failure after retries are exhausted."""
    delay = 2.0
    last_status = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.text, resp.status_code
            if resp.status_code == 404:
                return None, resp.status_code
        except requests.RequestException as exc:
            last_status = f"error: {exc}"

        if attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
    return None, last_status


def append_failure(project_number, url, status):
    is_new = not os.path.exists(FAILED_CSV)
    with open(FAILED_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["project_number", "url", "status"])
        writer.writerow([project_number, url, status])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N filtered projects")
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)

    rows = load_filtered_rows(limit=args.limit)
    total = len(rows)
    print(f"Loaded {total} projects to process (limit={args.limit})")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    done = 0
    failed = 0
    skipped = 0
    start = time.time()

    for i, row in enumerate(rows, start=1):
        project_number = row["Project Number"]
        url = row["Project Url"]
        dest = cache_path(project_number)

        if os.path.exists(dest):
            skipped += 1
            done += 1
        else:
            html, status = fetch_with_retries(session, url)
            if html is not None:
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(html)
                done += 1
            else:
                failed += 1
                append_failure(project_number, url, status)
                print(f"  FAILED {project_number} ({status}): {url}", file=sys.stderr)
            time.sleep(RATE_LIMIT_SECONDS)

        if i % PROGRESS_EVERY == 0 or i == total:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = total - i
            eta = remaining / rate if rate > 0 else float("inf")
            print(
                f"[{i}/{total}] done={done} failed={failed} skipped_cached={skipped} "
                f"elapsed={elapsed:.0f}s eta={eta:.0f}s"
            )

    print(f"\nFinished. done={done} failed={failed} (skipped {skipped} already cached)")
    if failed:
        print(f"See {FAILED_CSV} for permanent failures.")


if __name__ == "__main__":
    main()
