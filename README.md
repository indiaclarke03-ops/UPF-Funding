# UPF-Funding

Public Money, Ultra-Processed Food — a data pipeline and visualization tracking
IFC (International Finance Corporation) investment commitments to companies
across the ultra-processed food value chain, 1995–2026. Built for a graduate
thesis on development finance and ultra-processed food.

**[View the live visualization →](https://indiaclarke03-ops.github.io/UPF-Funding/)**

## What's here

- `scripts/01_scrape.py` / `02_parse.py` — scrape and parse IFC project disclosure pages
- `scripts/03_classify.py` — classify financed companies against the Nova food
  classification system, using IFC's own sector labels plus LLM classification
  of disclosure text for ambiguous sectors
- `scripts/04_build_site.py` — build the static data files behind the visualization
- `config/sector_tiers.yaml` — the rule-based sector → Nova tier mapping
- `site/` — the self-contained, offline static visualization (no backend, no
  build step — open `site/index.html` directly, or use the live link above)
- `data/` — raw, processed, and classified project data

## Method

Company sectors are classified into: **T1** (makes ultra-processed food/drink),
**T2** (supplies UPF inputs or packaging), **T3** (distributes/retails UPF at
scale), **CONTESTED** (infant formula, fortified staples), or **N** (none of
the above). Most projects are classified directly from IFC's published sector
label; ambiguous catch-all sectors are classified per-project from disclosure
text via LLM. Research in progress.
