"""Parse cached IFC project disclosure HTML into structured records.

Reads only from the local cache (./data/cache/{project_number}.html) — never
touches the network. Writes ./data/processed/ifc_disclosures.csv and .json.

Usage:
    python scripts/02_parse.py             # full run
    python scripts/02_parse.py --limit 20  # first 20 projects only
    python scripts/02_parse.py --limit 20 --show 3   # also print N full records
"""
import argparse
import csv
import json
import os
import re
import sys
import datetime

from bs4 import BeautifulSoup

from common import CACHE_DIR, PROCESSED_DIR, cache_path, load_filtered_rows

TOP_BLOCK_LABELS = {
    "project number": "project_number",
    "company name": "company_name",
    "sector": "sector",
    "industry": "industry",
    "country": "country",
    "date spi disclosed": "date_spi_disclosed",
    "status": "status",
    "environmental category": "environmental_category",
}

ACCORDION_LABELS = {
    "project sponsor and major shareholders of project company": "sponsor_shareholders",
    "total project cost and amount and nature of ifc's investment": "total_cost_and_ifc_investment",
    "ifc's investment as approved by the board": "board_approved_amount",
    "board approved ifc investment": "board_approved_amount",
    "location of project and description of site": "location_description",
    "anticipated impact measurement & monitoring (aimm) assessment": "development_impact",
    "ifc's role and additionality": "additionality",
}

SCRAPED_FIELDS = [
    "project_number", "company_name", "sector", "industry", "country",
    "date_spi_disclosed", "status", "environmental_category",
    "project_description", "sponsor_shareholders",
    "total_cost_and_ifc_investment", "board_approved_amount",
    "investment_table", "location_description", "development_impact",
    "additionality",
]

CSV_PASSTHROUGH = {
    "Company Name": "Company Name",
    "Industry": "Industry",
    "Country": "Country",
    "Date Disclosed": "Date Disclosed",
    "Total IFC investment as approved by Board(Million - USD)":
        "Total IFC investment as approved by Board(Million - USD)",
}


FOOTNOTE_RE = re.compile(r"\*\s*These investment figures are indicative\.?", re.IGNORECASE)


def clean(text):
    if text is None:
        return None
    text = FOOTNOTE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def get_text(tag):
    return clean(tag.get_text(" ", strip=True)) if tag is not None else None


def extract_top_block(soup):
    result = {}
    for strong in soup.find_all("strong"):
        label = clean(strong.get_text())
        if not label:
            continue
        key = TOP_BLOCK_LABELS.get(label.lower())
        if not key or key in result:
            continue
        sib = strong.find_next_sibling()
        if sib is not None and sib.name == "p":
            result[key] = get_text(sib)
    return result


def extract_accordion_sections(soup):
    result = {}
    for span in soup.find_all("span", class_="cmp-accordion__title"):
        label = clean(span.get_text())
        if not label:
            continue
        key = ACCORDION_LABELS.get(label.lower())
        if not key or key in result:
            continue
        button = span.find_parent("button")
        item = button.find_parent(class_="cmp-accordion__item") if button else None
        panel = item.find(class_="cmp-accordion__panel") if item else None
        if panel is not None:
            result[key] = get_text(panel)
    return result


def extract_project_description(soup):
    h2 = soup.find("h2", string=lambda s: s and clean(s) and clean(s).lower() == "project description")
    if not h2:
        return None
    sib = h2.find_next_sibling("div")
    return get_text(sib) if sib else None


def extract_investment_table(soup):
    th = soup.find("th", string=lambda s: s and clean(s) and clean(s).lower() == "product line")
    if not th:
        return None
    table = th.find_parent("table")
    if not table:
        return None
    result = {}
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        label = get_text(cells[0])
        value = get_text(cells[1])
        if not label or not value:
            continue
        try:
            result[label] = float(value.replace(",", ""))
        except ValueError:
            result[label] = value
    return result or None


def parse_html(html):
    soup = BeautifulSoup(html, "lxml")
    # The investment table is nested inside the "board approved amount" accordion
    # panel on some pages. Extract it first, then strip all tables from the tree
    # so panel/description text extraction below doesn't swallow the table text.
    investment_table = extract_investment_table(soup)
    for table in soup.find_all("table"):
        table.decompose()

    top = extract_top_block(soup)
    accordion = extract_accordion_sections(soup)
    record = {field: None for field in SCRAPED_FIELDS}
    record.update(top)
    record.update(accordion)
    record["project_description"] = extract_project_description(soup)
    record["investment_table"] = investment_table
    return record


def build_record(row, html, scrape_date):
    record = parse_html(html)
    for csv_col, out_col in CSV_PASSTHROUGH.items():
        record[out_col] = row.get(csv_col)
    record["scrape_date"] = scrape_date
    record["source_url"] = row.get("Project Url")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N filtered projects")
    parser.add_argument("--show", type=int, default=0,
                         help="Print N full parsed records to stdout for review")
    args = parser.parse_args()

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    rows = load_filtered_rows(limit=args.limit)
    total = len(rows)
    scrape_date = datetime.datetime.now(datetime.timezone.utc).isoformat()

    records = []
    missing_cache = 0
    for row in rows:
        project_number = row["Project Number"]
        path = cache_path(project_number)
        if not os.path.exists(path):
            missing_cache += 1
            continue
        with open(path, encoding="utf-8") as f:
            html = f.read()
        records.append(build_record(row, html, scrape_date))

    if missing_cache:
        print(f"Warning: {missing_cache} projects have no cache file yet "
              f"(run 01_scrape.py first). Skipped.", file=sys.stderr)

    print(f"Parsed {len(records)}/{total} projects.")

    fieldnames = SCRAPED_FIELDS + list(CSV_PASSTHROUGH.values()) + ["scrape_date", "source_url"]

    csv_path = os.path.join(PROCESSED_DIR, "ifc_disclosures.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            row_out = dict(rec)
            row_out["investment_table"] = (
                json.dumps(rec["investment_table"]) if rec["investment_table"] else None
            )
            writer.writerow(row_out)

    json_path = os.path.join(PROCESSED_DIR, "ifc_disclosures.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")

    if args.show:
        print(f"\n=== Showing {min(args.show, len(records))} full records ===\n")
        for rec in records[: args.show]:
            print(json.dumps(rec, indent=2, ensure_ascii=False))
            print()


if __name__ == "__main__":
    main()
