"""Shared constants and helpers for the IFC disclosure scraper/parser."""
import csv
import os

RAW_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "raw",
    "ifc_investment_services_projects_09-14-2026.csv",
)
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
FAILED_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "failed_urls.csv")

TARGET_INDUSTRIES = {
    "Agribusiness and Forestry",
    "Manufacturing",
    "Tourism, Retail and Property",
    "other",
}

USER_AGENT = "NYU-CGA-research/1.0 (graduate thesis; indiaclarke03@gmail.com)"

CSV_PASSTHROUGH_COLUMNS = [
    "Project Number",
    "Project Url",
    "Company Name",
    "Project Name",
    "Country",
    "Industry",
    "Date Disclosed",
    "Total IFC investment as approved by Board(Million - USD)",
]


def load_filtered_rows(limit=None):
    """Read the raw CSV, filter to target industries, and dedupe by Project Number
    (keeping the first occurrence). Returns a list of dict rows."""
    with open(RAW_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    seen = set()
    kept = []
    for row in rows:
        if row.get("Industry") not in TARGET_INDUSTRIES:
            continue
        pn = row.get("Project Number")
        if not pn or pn in seen:
            continue
        seen.add(pn)
        kept.append(row)
        if limit is not None and len(kept) >= limit:
            break
    return kept


def cache_path(project_number):
    return os.path.join(CACHE_DIR, f"{project_number}.html")
