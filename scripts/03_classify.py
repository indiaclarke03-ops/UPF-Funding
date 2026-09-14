"""Classify IFC-financed companies against the Nova ultra-processed-food (UPF)
classification system.

Two paths:
  - Rows whose CSV "sector" is one of the LLM_SECTORS (ambiguous catch-all/
    ambiguous labels) are classified per-project by the Anthropic API.
  - Every other row gets a tier looked up from config/sector_tiers.yaml,
    a rule-based mapping from sector name alone.

Reads:  ./data/processed/ifc_disclosures.csv
Caches: ./data/cache/classify/{project_number}.json  (LLM responses only)
Writes: ./data/processed/ifc_classified.csv, ./data/processed/ifc_classified.json
Logs:   ./data/classify_errors.log  (unparseable LLM responses)

Usage:
    python scripts/03_classify.py --limit 20   # random sample of 20 rows (seed=42), print results
    python scripts/03_classify.py              # full run
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import yaml
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
INPUT_CSV = os.path.join(BASE_DIR, "data", "processed", "ifc_disclosures.csv")
SECTOR_TIERS_YAML = os.path.join(BASE_DIR, "config", "sector_tiers.yaml")
CLASSIFY_CACHE_DIR = os.path.join(BASE_DIR, "data", "cache", "classify")
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")
ERROR_LOG = os.path.join(BASE_DIR, "data", "classify_errors.log")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1000
CONCURRENCY = 5
MAX_RETRIES = 5

# $/token, standard Claude Sonnet 5 pricing (no active intro discount as of this run)
INPUT_PRICE_PER_TOKEN = 3.00 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 15.00 / 1_000_000

LLM_SECTORS = {
    "other",
    "Other",
    "Other Food",
    "Dairy Products",
    "Animal Slaughtering and Processing",
    "Fruit and Vegetable Preservation or Processing (Canning, Freezing, Drying, Jams, etc.)",
    "Glass and Glass Products (Including Glass and Mineral Wool)",
    "Other Plastic and Rubber Products (Including Polypropylene Bags, Housing Components, Containers, etc.)",
}

SYSTEM_PROMPT = """You classify companies financed by development banks against the Nova
food classification system, for academic research.

Assign ONE tier:
T1 - makes ultra-processed food or drink (Nova group 4): soft drinks,
     juices from concentrate, confectionery, biscuits, snacks, instant
     noodles, packaged bakery, breakfast cereal, flavoured/sweetened
     dairy, reconstituted meat products, ready meals, sauces.
T2 - supplies inputs or packaging predominantly used by UPF makers:
     refined sugar and sweeteners, industrial starches, refined
     vegetable oils, emulsifiers and additives, beverage cans, PET,
     beverage glass, food-grade flexible packaging.
T3 - distributes or retails UPF at scale: supermarkets, hypermarkets,
     convenience chains, food wholesale, quick-service restaurants.
CONTESTED - infant formula, follow-on formula, fortified staples,
     therapeutic foods.
N  - none of the above. This includes primary agriculture, raw
     commodity trading, plain dairy, fresh meat and fish, non-food
     manufacturing, and any non-food business.

Rules:
- Classify on what the company ACTUALLY PRODUCES OR SELLS, never on
  marketing language. A firm describing itself as a "healthy beverage"
  or "nutrition" company that sells sweetened drinks is T1.
- Judge only from the text given. If the description does not say what
  the company makes, return N with confidence below 0.4.
- Do not infer from country, sector label, or company name alone.

Return ONLY valid JSON, no preamble, no markdown fences:
{"tier":"T1|T2|T3|CONTESTED|N","confidence":0.0-1.0,
 "products":"<what they actually make, max 12 words>",
 "reason":"<one sentence>"}"""

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_cost_lock = threading.Lock()
_cost = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cache_hits": 0}


def load_sector_tiers():
    with open(SECTOR_TIERS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["sector_tiers"]


def cache_path(project_number):
    return os.path.join(CLASSIFY_CACHE_DIR, f"{project_number}.json")


def log_parse_error(project_number, raw_text):
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"=== {project_number} ===\n{raw_text}\n\n")


def parse_llm_json(raw_text):
    cleaned = FENCE_RE.sub("", raw_text.strip()).strip()
    return json.loads(cleaned)


def call_with_retry(client, **kwargs):
    delay = 2.0
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
            else:
                raise
        if attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
    raise last_exc


def classify_project(client, row):
    """Returns (tier, confidence, products, reason) for one LLM-eligible row.
    Uses the on-disk cache if present; otherwise calls the API and caches
    the result before returning."""
    project_number = row["project_number"]
    path = cache_path(project_number)

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        with _cost_lock:
            _cost["cache_hits"] += 1
        return cached["tier"], cached["confidence"], cached["products"], cached["reason"]

    user_content = (
        f"company_name: {row.get('company_name') or ''}\n"
        f"sector: {row.get('sector') or ''}\n"
        f"project_description: {row.get('project_description') or ''}"
    )

    response = call_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    with _cost_lock:
        _cost["input_tokens"] += response.usage.input_tokens
        _cost["output_tokens"] += response.usage.output_tokens
        _cost["calls"] += 1
        calls = _cost["calls"]
        running_cost = (
            _cost["input_tokens"] * INPUT_PRICE_PER_TOKEN
            + _cost["output_tokens"] * OUTPUT_PRICE_PER_TOKEN
        )
        if calls % 10 == 0:
            print(f"  [{calls} LLM calls] running cost estimate: ${running_cost:.4f}")

    raw_text = next((b.text for b in response.content if b.type == "text"), "")

    try:
        parsed = parse_llm_json(raw_text)
        tier = parsed.get("tier")
        confidence = parsed.get("confidence")
        products = parsed.get("products")
        reason = parsed.get("reason")
    except (json.JSONDecodeError, AttributeError):
        log_parse_error(project_number, raw_text)
        tier = confidence = products = reason = None

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"tier": tier, "confidence": confidence, "products": products, "reason": reason},
            f, indent=2,
        )

    return tier, confidence, products, reason


def parse_usd(row):
    raw = row.get("Total IFC investment as approved by Board(Million - USD)")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def parse_year(row):
    raw = row.get("Date Disclosed")
    if not raw:
        return None
    try:
        return int(raw.split("/")[-1])
    except (ValueError, IndexError):
        return None


def five_year_bucket(year):
    if year is None:
        return "unknown"
    start = (year // 5) * 5
    return f"{start}-{start + 4}"


def print_summary(records):
    from collections import defaultdict

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print("\n--- Project count and total USD by tier, split by tier_source ---")
    grouped = defaultdict(lambda: {"count": 0, "usd": 0.0})
    for r in records:
        key = (r.get("tier") or "null", r.get("tier_source") or "null")
        grouped[key]["count"] += 1
        grouped[key]["usd"] += parse_usd(r)
    for (tier, source), agg in sorted(grouped.items()):
        print(f"  {tier:12s} {source:12s} n={agg['count']:5d}  ${agg['usd']:,.1f}M")

    print("\n--- Project count and total USD by tier, by 5-year period ---")
    period_grouped = defaultdict(lambda: {"count": 0, "usd": 0.0})
    for r in records:
        bucket = five_year_bucket(parse_year(r))
        key = (bucket, r.get("tier") or "null")
        period_grouped[key]["count"] += 1
        period_grouped[key]["usd"] += parse_usd(r)
    for (bucket, tier), agg in sorted(period_grouped.items()):
        print(f"  {bucket:12s} {tier:12s} n={agg['count']:5d}  ${agg['usd']:,.1f}M")

    headline = sum(parse_usd(r) for r in records if r.get("tier") in ("T1", "T3"))
    print(f"\n--- HEADLINE: total USD for tier in (T1, T3) ---")
    print(f"  ${headline:,.1f}M")

    print("\n--- 25 largest T1 projects ---")
    t1 = sorted(
        (r for r in records if r.get("tier") == "T1"),
        key=parse_usd, reverse=True,
    )[:25]
    for r in t1:
        print(f"  ${parse_usd(r):>10,.1f}M  {r.get('company_name','')[:45]:45s} "
              f"{r.get('Country',''):20s} {r.get('Date Disclosed','')}")

    print("\n--- LLM classifications with confidence < 0.6 (flagged for review) ---")
    low_conf = [
        r for r in records
        if r.get("tier_source") == "llm"
        and r.get("confidence") not in (None, "")
        and float(r["confidence"]) < 0.6
    ]
    for r in low_conf:
        print(f"  {r.get('project_number','')}  {r.get('company_name','')[:35]:35s} "
              f"sector={r.get('sector',''):30s} tier={r.get('tier')} "
              f"conf={r.get('confidence')}  reason={r.get('reason','')}")
    if not low_conf:
        print("  (none)")


def check_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or key == "placeholder":
        raise RuntimeError(
            "ANTHROPIC_API_KEY is missing or still set to \"placeholder\". "
            "Edit .env in the project root and set it to your real Anthropic API key."
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process a random sample of N rows (seed=42), spanning the full date range")
    args = parser.parse_args()

    check_api_key()

    os.makedirs(CLASSIFY_CACHE_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    sector_tiers = load_sector_tiers()

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.limit is not None and args.limit < len(rows):
        rows = random.Random(42).sample(rows, args.limit)
        rows.sort(key=lambda r: (parse_year(r) is None, parse_year(r) or 0))
    total = len(rows)
    print(f"Loaded {total} rows from {INPUT_CSV}"
          + (f" (random sample, seed=42)" if args.limit is not None else ""))

    llm_rows = [r for r in rows if r.get("sector") in LLM_SECTORS]
    rule_rows = [r for r in rows if r.get("sector") not in LLM_SECTORS]
    print(f"  {len(llm_rows)} rows -> LLM classification")
    print(f"  {len(rule_rows)} rows -> rule-based (config/sector_tiers.yaml)")

    results = {}  # project_number -> (tier, tier_source, confidence, products, reason)

    for row in rule_rows:
        sector = row.get("sector")
        tier = sector_tiers.get(sector)
        if tier is None:
            print(f"  WARNING: sector {sector!r} (project {row.get('project_number')}) "
                  f"not found in sector_tiers.yaml", file=sys.stderr)
        results[row["project_number"]] = (tier, "sector_rule", None, None, None)

    if llm_rows:
        client = anthropic.Anthropic()
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
            futures = {
                executor.submit(classify_project, client, row): row
                for row in llm_rows
            }
            done = 0
            for future in as_completed(futures):
                row = futures[future]
                try:
                    tier, confidence, products, reason = future.result()
                except Exception as exc:
                    print(f"  FAILED {row.get('project_number')}: {exc}", file=sys.stderr)
                    tier = confidence = products = reason = None
                results[row["project_number"]] = (tier, "llm", confidence, products, reason)
                done += 1
                if done % 10 == 0 or done == len(llm_rows):
                    print(f"  [{done}/{len(llm_rows)}] LLM classifications complete")

    final_cost = (
        _cost["input_tokens"] * INPUT_PRICE_PER_TOKEN
        + _cost["output_tokens"] * OUTPUT_PRICE_PER_TOKEN
    )
    print(f"\nLLM calls made: {_cost['calls']}  (cache hits: {_cost['cache_hits']})")
    print(f"Tokens: {_cost['input_tokens']} in / {_cost['output_tokens']} out")
    print(f"Final cost estimate: ${final_cost:.4f}")

    records = []
    for row in rows:
        tier, tier_source, confidence, products, reason = results[row["project_number"]]
        rec = dict(row)
        rec["tier"] = tier
        rec["tier_source"] = tier_source
        rec["confidence"] = confidence
        rec["products"] = products
        rec["reason"] = reason
        records.append(rec)

    fieldnames = list(rows[0].keys()) + ["tier", "tier_source", "confidence", "products", "reason"]
    csv_path = os.path.join(PROCESSED_DIR, "ifc_classified.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    json_path = os.path.join(PROCESSED_DIR, "ifc_classified.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {csv_path}")
    print(f"Wrote {json_path}")

    if args.limit is not None:
        print(f"\n=== {len(records)} test records ===\n")
        for r in records:
            print(f"company_name: {r.get('company_name')}")
            print(f"sector:       {r.get('sector')}")
            print(f"tier:         {r.get('tier')}  (source: {r.get('tier_source')})")
            print(f"confidence:   {r.get('confidence')}")
            print(f"products:     {r.get('products')}")
            print(f"reason:       {r.get('reason')}")
            print()

    print_summary(records)


if __name__ == "__main__":
    main()
