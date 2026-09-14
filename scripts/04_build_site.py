"""Build the static data files for the ./site visualisation.

Reads ./data/processed/ifc_classified.csv and writes:
  ./site/data/projects.json   (rows with tier in T1/T2/T3/CONTESTED)
  ./site/data/summary.json    (aggregates the site's charts/cards use)
  ./site/data/projects.js     (same as projects.json, wrapped as `window.PROJECTS_DATA = ...`)
  ./site/data/summary.js      (same as summary.json, wrapped as `window.SUMMARY_DATA = ...`)

The .js copies exist because opening index.html via double-click (file://)
blocks fetch()/XHR of local JSON in Chrome and Safari (CORS applies to the
file: scheme). A same-origin <script src="data/projects.js"> is NOT subject
to that restriction, so index.html loads data via script tags instead of
fetch. The .json files are kept as the human-readable/debuggable source.

Usage:
    python scripts/04_build_site.py
"""
import csv
import json
import os
from collections import defaultdict

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
INPUT_CSV = os.path.join(BASE_DIR, "data", "processed", "ifc_classified.csv")
SITE_DATA_DIR = os.path.join(BASE_DIR, "site", "data")

UPF_TIERS = {"T1", "T2", "T3", "CONTESTED"}
HEADLINE_TIERS = {"T1", "T3"}  # what "UPF commitments" means for the headline/trend/top-table figures

# Baseline for the "company names only" comparison card -- a naive keyword
# search over company names (e.g. "Coca-Cola", "Nestle", "PepsiCo") run
# before this project built sector-rule + LLM classification. Not
# reproducible from ifc_classified.csv, so it's a fixed reference point.
KEYWORD_ONLY = {"projects": 38, "usd_musd": 2852}

# World Bank country-name variants -> ISO3. Only covers names that appear in
# the T1/T2/T3/CONTESTED subset. Regional aggregates ("X Region", "X
# Subregion") are intentionally excluded -- see is_regional() below.
COUNTRY_TO_ISO3 = {
    "India": "IND", "Brazil": "BRA", "Argentina": "ARG", "Indonesia": "IDN",
    "Turkiye": "TUR", "Ukraine": "UKR", "Russian Federation": "RUS",
    "Egypt, Arab Republic of": "EGY", "Bangladesh": "BGD", "China": "CHN",
    "Mali": "MLI", "Tanzania": "TZA", "Senegal": "SEN", "Pakistan": "PAK",
    "Viet Nam": "VNM", "Tajikistan": "TJK", "Mexico": "MEX", "Colombia": "COL",
    "Belarus": "BLR", "Peru": "PER", "Nigeria": "NGA", "Cote D'Ivoire": "CIV",
    "Ecuador": "ECU", "Bosnia and Herzegovina": "BIH", "Kazakhstan": "KAZ",
    "Sri Lanka": "LKA", "Azerbaijan": "AZE", "South Africa": "ZAF",
    "Kenya": "KEN", "Bulgaria": "BGR", "Mozambique": "MOZ", "Cameroon": "CMR",
    "Tunisia": "TUN", "Romania": "ROU", "Yemen, Republic of": "YEM",
    "Ethiopia": "ETH", "Georgia": "GEO", "Nicaragua": "NIC", "Serbia": "SRB",
    "Moldova": "MDA", "Guatemala": "GTM", "Angola": "AGO",
    "Kyrgyz Republic": "KGZ", "Philippines": "PHL", "Uzbekistan": "UZB",
    "Haiti": "HTI", "Sierra Leone": "SLE", "Ghana": "GHA", "Guinea": "GIN",
    "Fiji": "FJI", "Uganda": "UGA", "Lebanon": "LBN", "Uruguay": "URY",
    "Myanmar": "MMR", "Armenia": "ARM", "Croatia": "HRV", "Costa Rica": "CRI",
    "Zimbabwe": "ZWE", "Madagascar": "MDG", "Morocco": "MAR", "Gabon": "GAB",
    "Chile": "CHL", "Kosovo": "XKX", "Zambia": "ZMB", "Iraq": "IRQ",
    "Rwanda": "RWA", "Malawi": "MWI", "Nepal": "NPL", "Burkina Faso": "BFA",
    "Saudi Arabia": "SAU", "Honduras": "HND", "Montenegro": "MNE",
    "El Salvador": "SLV", "Cambodia": "KHM", "Jamaica": "JAM", "Latvia": "LVA",
    "Mauritania": "MRT", "Dominican Republic": "DOM", "Lithuania": "LTU",
    "Guyana": "GUY", "Hungary": "HUN", "Poland": "POL",
}

# ISO3 -> [lat, lon] geographic centroid, for the 82 countries above.
ISO3_CENTROID = {
    "IND": [20.59, 78.96], "BRA": [-14.24, -51.93], "ARG": [-38.42, -63.62],
    "IDN": [-0.79, 113.92], "TUR": [38.96, 35.24], "UKR": [48.38, 31.17],
    "RUS": [61.52, 105.32], "EGY": [26.82, 30.80], "BGD": [23.68, 90.36],
    "CHN": [35.86, 104.20], "MLI": [17.57, -3.996], "TZA": [-6.37, 34.89],
    "SEN": [14.50, -14.45], "PAK": [30.38, 69.35], "VNM": [14.06, 108.28],
    "TJK": [38.86, 71.28], "MEX": [23.63, -102.55], "COL": [4.57, -74.30],
    "BLR": [53.71, 27.95], "PER": [-9.19, -75.02], "NGA": [9.08, 8.68],
    "CIV": [7.54, -5.55], "ECU": [-1.83, -78.18], "BIH": [43.92, 17.68],
    "KAZ": [48.02, 66.92], "LKA": [7.87, 80.77], "AZE": [40.14, 47.58],
    "ZAF": [-30.56, 22.94], "KEN": [-0.02, 37.91], "BGR": [42.73, 25.49],
    "MOZ": [-18.67, 35.53], "CMR": [7.37, 12.35], "TUN": [33.89, 9.54],
    "ROU": [45.94, 24.97], "YEM": [15.55, 48.52], "ETH": [9.15, 40.49],
    "GEO": [42.32, 43.36], "NIC": [12.87, -85.21], "SRB": [44.02, 21.01],
    "MDA": [47.41, 28.37], "GTM": [15.78, -90.23], "AGO": [-11.20, 17.87],
    "KGZ": [41.20, 74.77], "PHL": [12.88, 121.77], "UZB": [41.38, 64.59],
    "HTI": [18.97, -72.29], "SLE": [8.46, -11.78], "GHA": [7.95, -1.02],
    "GIN": [9.95, -9.70], "FJI": [-17.71, 178.07], "UGA": [1.37, 32.29],
    "LBN": [33.85, 35.86], "URY": [-32.52, -55.77], "MMR": [21.91, 95.96],
    "ARM": [40.07, 45.04], "HRV": [45.10, 15.20], "CRI": [9.75, -83.75],
    "ZWE": [-19.02, 29.15], "MDG": [-18.77, 46.87], "MAR": [31.79, -7.09],
    "GAB": [-0.80, 11.61], "CHL": [-35.68, -71.54], "XKX": [42.60, 20.90],
    "ZMB": [-13.13, 27.85], "IRQ": [33.22, 43.68], "RWA": [-1.94, 29.87],
    "MWI": [-13.25, 34.30], "NPL": [28.39, 84.12], "BFA": [12.24, -1.56],
    "SAU": [23.89, 45.08], "HND": [15.20, -86.24], "MNE": [42.71, 19.37],
    "SLV": [13.79, -88.90], "KHM": [12.57, 104.99], "JAM": [18.11, -77.30],
    "LVA": [56.88, 24.60], "MRT": [21.01, -10.94], "DOM": [18.74, -70.16],
    "LTU": [55.17, 23.88], "GUY": [4.86, -58.93], "HUN": [47.16, 19.50],
    "POL": [51.92, 19.15],
}


def is_regional(country_name):
    return "Region" in country_name or "Subregion" in country_name


def parse_amount(row):
    raw = row.get("Total IFC investment as approved by Board(Million - USD)")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_year(row):
    raw = row.get("Date Disclosed")
    if not raw:
        return None
    try:
        return int(raw.split("/")[-1])
    except (ValueError, IndexError):
        return None


def parse_confidence(row):
    raw = row.get("confidence")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def five_year_bucket(year):
    if year is None:
        return None
    start = (year // 5) * 5
    return f"{start}-{start + 4}"


def main():
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    upf_rows = [r for r in all_rows if r.get("tier") in UPF_TIERS]
    print(f"Loaded {len(all_rows)} total rows, {len(upf_rows)} in tiers {sorted(UPF_TIERS)}")

    # --- unmapped-country check ---------------------------------------
    unmapped = set()
    for row in upf_rows:
        country = row.get("Country", "")
        if not country or is_regional(country) or country in COUNTRY_TO_ISO3:
            continue
        unmapped.add(country)

    print(f"\n=== Unmapped country values ({len(unmapped)}) ===")
    if unmapped:
        for c in sorted(unmapped):
            n = sum(1 for r in upf_rows if r.get("Country") == c)
            print(f"  {c!r}  ({n} rows)")
    else:
        print("  (none -- every non-regional country in the UPF subset has an ISO3 code)")

    # --- projects.json / .js -------------------------------------------
    projects = []
    for row in upf_rows:
        country = row.get("Country", "")
        regional = is_regional(country)
        iso3 = None if regional else COUNTRY_TO_ISO3.get(country)
        projects.append({
            "project_number": row.get("project_number"),
            "company": row.get("company_name"),
            "country": country,
            "iso3": iso3,
            "is_regional": regional,
            "year": parse_year(row),
            "amount_musd": parse_amount(row),
            "tier": row.get("tier"),
            "sector": row.get("sector"),
            "tier_source": row.get("tier_source"),
            "confidence": parse_confidence(row),
            "products": row.get("products") or None,
            "reason": row.get("reason") or None,
            "url": row.get("source_url"),
        })

    os.makedirs(SITE_DATA_DIR, exist_ok=True)
    projects_path_json = os.path.join(SITE_DATA_DIR, "projects.json")
    with open(projects_path_json, "w", encoding="utf-8") as f:
        json.dump(projects, f, indent=2, ensure_ascii=False)

    projects_path_js = os.path.join(SITE_DATA_DIR, "projects.js")
    with open(projects_path_js, "w", encoding="utf-8") as f:
        f.write("window.PROJECTS_DATA = ")
        json.dump(projects, f, ensure_ascii=False)
        f.write(";\n")

    with open(os.path.join(SITE_DATA_DIR, "centroids.js"), "w", encoding="utf-8") as f:
        f.write("window.ISO3_CENTROID = ")
        json.dump(ISO3_CENTROID, f, ensure_ascii=False)
        f.write(";\n")

    # --- summary.json / .js ---------------------------------------------
    def agg(rows):
        return {"projects": len(rows), "usd_musd": round(sum(parse_amount(r) or 0 for r in rows), 1)}

    totals_by_tier = {
        tier: agg([r for r in upf_rows if r.get("tier") == tier])
        for tier in sorted(UPF_TIERS)
    }
    totals_by_tier_source = {
        source: agg([r for r in upf_rows if r.get("tier_source") == source])
        for source in ("sector_rule", "llm")
    }

    headline_rows = [r for r in upf_rows if r.get("tier") in HEADLINE_TIERS]
    sector_rule_headline_rows = [r for r in headline_rows if r.get("tier_source") == "sector_rule"]
    method_comparison = {
        "keyword_only": KEYWORD_ONLY,
        "sector_rule": agg(sector_rule_headline_rows),
        "sector_plus_llm": agg(headline_rows),
    }

    periods = sorted({
        five_year_bucket(parse_year(r)) for r in all_rows if parse_year(r) is not None
    })
    by_period = []
    for period in periods:
        period_headline_rows = [
            r for r in headline_rows if five_year_bucket(parse_year(r)) == period
        ]
        period_all_rows = [
            r for r in all_rows if five_year_bucket(parse_year(r)) == period
        ]
        by_period.append({
            "period": period,
            "upf_usd_musd": round(sum(parse_amount(r) or 0 for r in period_headline_rows), 1),
            "upf_projects": len(period_headline_rows),
            "total_usd_musd_all_scraped": round(sum(parse_amount(r) or 0 for r in period_all_rows), 1),
            "total_projects_all_scraped": len(period_all_rows),
        })

    top20 = sorted(headline_rows, key=lambda r: parse_amount(r) or 0, reverse=True)[:20]
    top20_out = [{
        "project_number": r.get("project_number"),
        "company": r.get("company_name"),
        "country": r.get("Country"),
        "year": parse_year(r),
        "amount_musd": parse_amount(r),
        "tier": r.get("tier"),
        "sector": r.get("sector"),
        "tier_source": r.get("tier_source"),
        "url": r.get("source_url"),
    } for r in top20]

    summary = {
        "totals_by_tier": totals_by_tier,
        "totals_by_tier_source": totals_by_tier_source,
        "method_comparison": method_comparison,
        "by_period": by_period,
        "top20_headline": top20_out,
    }

    with open(os.path.join(SITE_DATA_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    with open(os.path.join(SITE_DATA_DIR, "summary.js"), "w", encoding="utf-8") as f:
        f.write("window.SUMMARY_DATA = ")
        json.dump(summary, f, ensure_ascii=False)
        f.write(";\n")

    print(f"\nWrote {len(projects)} projects to {projects_path_json} (+ .js)")
    print(f"Wrote summary to {os.path.join(SITE_DATA_DIR, 'summary.json')} (+ .js)")
    print(f"Wrote {len(ISO3_CENTROID)} centroids to {os.path.join(SITE_DATA_DIR, 'centroids.js')}")
    print("\nmethod_comparison:")
    print(json.dumps(method_comparison, indent=2))


if __name__ == "__main__":
    main()
