#!/usr/bin/env python3
"""
FPREN Florida Census Data Fetcher
==================================
Pulls ACS 5-year estimates for all 67 Florida counties from the US Census
Bureau API and stores them in MongoDB collection `fl_census`.

Config: weather_rss/config/census_config.json
    { "api_key": "YOUR_CENSUS_API_KEY" }

Usage:
    python3 fl_census_fetcher.py               # fetch + store
    python3 fl_census_fetcher.py --dry-run     # fetch only, no DB write
    python3 fl_census_fetcher.py --year 2022   # override ACS year

Census Bureau API docs: https://www.census.gov/data/developers/data-sets.html
ACS 5-Year variables:   https://api.census.gov/data/2022/acs/acs5/variables.html
"""

import argparse
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne

# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [census] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fl_census_fetcher")

CENSUS_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "config", "census_config.json"
)
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "weather_rss"
COLL_NAME = "fl_census"

# Florida FIPS state code
FL_STATE_FIPS = "12"

# ACS 5-year variables to request
# Format: { local_field_name: (census_variable, friendly_description) }
ACS_VARIABLES = {
    "population_total":        ("B01003_001E", "Total population"),
    "population_male":         ("B01001_002E", "Male population"),
    "population_female":       ("B01001_026E", "Female population"),
    # Under 18 — sum of male+female age buckets 0-17
    "population_under5_m":     ("B01001_003E", "Male under 5"),
    "population_5to9_m":       ("B01001_004E", "Male 5-9"),
    "population_10to14_m":     ("B01001_005E", "Male 10-14"),
    "population_15to17_m":     ("B01001_006E", "Male 15-17"),
    "population_under5_f":     ("B01001_027E", "Female under 5"),
    "population_5to9_f":       ("B01001_028E", "Female 5-9"),
    "population_10to14_f":     ("B01001_029E", "Female 10-14"),
    "population_15to17_f":     ("B01001_030E", "Female 15-17"),
    # 65+ — male buckets 65-66, 67-69, 70-74, 75-79, 80-84, 85+
    "population_65to66_m":     ("B01001_020E", "Male 65-66"),
    "population_67to69_m":     ("B01001_021E", "Male 67-69"),
    "population_70to74_m":     ("B01001_022E", "Male 70-74"),
    "population_75to79_m":     ("B01001_023E", "Male 75-79"),
    "population_80to84_m":     ("B01001_024E", "Male 80-84"),
    "population_85plus_m":     ("B01001_025E", "Male 85+"),
    "population_65to66_f":     ("B01001_044E", "Female 65-66"),
    "population_67to69_f":     ("B01001_045E", "Female 67-69"),
    "population_70to74_f":     ("B01001_046E", "Female 70-74"),
    "population_75to79_f":     ("B01001_047E", "Female 75-79"),
    "population_80to84_f":     ("B01001_048E", "Female 80-84"),
    "population_85plus_f":     ("B01001_049E", "Female 85+"),
    # Housing
    "housing_units":           ("B25001_001E", "Total housing units"),
    "housing_occupied":        ("B25002_002E", "Occupied housing units"),
    # Income & poverty
    "median_household_income": ("B19013_001E", "Median household income"),
    "population_in_poverty":   ("B17001_002E", "Population below poverty level"),
    # Limited English proficiency (speak English less than very well)
    "limited_english":         ("B16004_067E", "Speak English less than very well (all ages)"),
    # Disability
    "population_with_disability": ("B18101_004E", "With disability, civilian noninstitutionalized"),
    # Land area (from NAME, handled separately via TIGER)
}

# Map Census county NAME → clean county name used in FPREN
def _clean_county_name(name: str) -> str:
    """'Alachua County, Florida' → 'Alachua'"""
    return name.replace(" County, Florida", "").replace(" County", "").strip()


def load_census_key() -> str:
    """Load Census API key from census_config.json or CENSUS_API_KEY env var."""
    env_key = os.environ.get("CENSUS_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        with open(CENSUS_CFG_PATH) as f:
            cfg = json.load(f)
        key = cfg.get("api_key", "").strip()
        if key and key != "YOUR_CENSUS_API_KEY":
            return key
    except FileNotFoundError:
        pass
    raise RuntimeError(
        f"Census API key not found. Set CENSUS_API_KEY env var or add it to "
        f"{CENSUS_CFG_PATH}:\n"
        f'  {{"api_key": "your-key-here"}}'
    )


def fetch_acs_data(api_key: str, year: int = 2022) -> list[dict]:
    """
    Fetch ACS 5-year estimates for all FL counties.
    Returns list of county dicts with computed fields.
    """
    variables = list(ACS_VARIABLES.keys())
    census_vars = [ACS_VARIABLES[v][0] for v in variables]
    get_param = "NAME," + ",".join(census_vars)

    url = (
        f"https://api.census.gov/data/{year}/acs/acs5"
        f"?get={urllib.parse.quote(get_param)}"
        f"&for=county:*"
        f"&in=state:{FL_STATE_FIPS}"
        f"&key={api_key}"
    )

    log.info("Fetching ACS %d data for all FL counties...", year)
    log.debug("URL: %s", url.replace(api_key, "***"))

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Census API request failed: {e}") from e

    if not raw or len(raw) < 2:
        raise RuntimeError("Census API returned empty response")

    headers = raw[0]  # first row = column names
    rows    = raw[1:]  # remaining rows = data
    log.info("Received %d county rows from Census API", len(rows))

    results = []
    for row in rows:
        rec = dict(zip(headers, row))

        county_full = rec.get("NAME", "Unknown")
        county      = _clean_county_name(county_full)
        fips_county = rec.get("county", "")

        def _int(field: str) -> int:
            try:
                v = int(rec.get(ACS_VARIABLES[field][0], -1) or -1)
                return max(v, 0)
            except (ValueError, TypeError):
                return 0

        def _float(field: str) -> float:
            try:
                v = float(rec.get(ACS_VARIABLES[field][0], -1) or -1)
                return max(v, 0.0)
            except (ValueError, TypeError):
                return 0.0

        pop_total = _int("population_total")

        # Aggregate under-18
        pop_under18 = sum(_int(f) for f in [
            "population_under5_m", "population_5to9_m",
            "population_10to14_m", "population_15to17_m",
            "population_under5_f", "population_5to9_f",
            "population_10to14_f", "population_15to17_f",
        ])

        # Aggregate 65+
        pop_65plus = sum(_int(f) for f in [
            "population_65to66_m", "population_67to69_m",
            "population_70to74_m", "population_75to79_m",
            "population_80to84_m", "population_85plus_m",
            "population_65to66_f", "population_67to69_f",
            "population_70to74_f", "population_75to79_f",
            "population_80to84_f", "population_85plus_f",
        ])

        pop_in_poverty      = _int("population_in_poverty")
        limited_english     = _int("limited_english")
        pop_with_disability = _int("population_with_disability")
        housing_units       = _int("housing_units")
        housing_occupied    = _int("housing_occupied")
        median_income       = _int("median_household_income")

        # Derived percentages (safe division)
        def pct(numerator: int, denominator: int) -> float:
            return round(numerator / denominator * 100, 2) if denominator > 0 else 0.0

        pct_65plus      = pct(pop_65plus, pop_total)
        pct_under18     = pct(pop_under18, pop_total)
        pct_poverty     = pct(pop_in_poverty, pop_total)
        pct_lep         = pct(limited_english, pop_total)
        pct_disability  = pct(pop_with_disability, pop_total)
        housing_vacancy = pct(housing_units - housing_occupied, housing_units)

        # Vulnerability score (0.0–1.0)
        # Weights: elderly 35%, poverty 30%, LEP 15%, disability 20%
        # Normalized against approximate FL averages / max values
        score = min(1.0, (
            (pct_65plus      / 30.0) * 0.35 +
            (pct_poverty     / 25.0) * 0.30 +
            (pct_lep         / 15.0) * 0.15 +
            (pct_disability  / 20.0) * 0.20
        ))

        results.append({
            "fips_state":            FL_STATE_FIPS,
            "fips_county":           fips_county,
            "county_name":           county_full,
            "county":                county,
            "year":                  year,
            "dataset":               f"ACS 5-Year {year}",
            "population_total":      pop_total,
            "population_under18":    pop_under18,
            "population_65plus":     pop_65plus,
            "population_in_poverty": pop_in_poverty,
            "limited_english":       limited_english,
            "population_with_disability": pop_with_disability,
            "housing_units":         housing_units,
            "housing_occupied":      housing_occupied,
            "median_household_income": median_income,
            "pct_65plus":            pct_65plus,
            "pct_under18":           pct_under18,
            "pct_poverty":           pct_poverty,
            "pct_limited_english":   pct_lep,
            "pct_disability":        pct_disability,
            "housing_vacancy_rate":  housing_vacancy,
            "vulnerability_score":   round(score, 4),
            "fetched_at":            datetime.now(timezone.utc).isoformat(),
        })

    results.sort(key=lambda r: r["county"])
    return results


def store_to_mongodb(records: list[dict]) -> int:
    """Upsert census records into MongoDB. Returns count of upserted docs."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    col = client[DB_NAME][COLL_NAME]

    # Ensure index on county + year
    col.create_index([("county", 1), ("year", -1)], unique=True, background=True)
    col.create_index([("vulnerability_score", -1)], background=True)

    ops = [
        UpdateOne(
            {"county": r["county"], "year": r["year"]},
            {"$set": r},
            upsert=True,
        )
        for r in records
    ]
    result = col.bulk_write(ops)
    client.close()
    return result.upserted_count + result.modified_count


def main():
    parser = argparse.ArgumentParser(description="FPREN FL Census Data Fetcher")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch from API but do not write to MongoDB")
    parser.add_argument("--year", type=int, default=2022,
                        help="ACS year to fetch (default: 2022)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        api_key = load_census_key()
    except RuntimeError as e:
        log.error("%s", e)
        sys.exit(1)

    try:
        records = fetch_acs_data(api_key, year=args.year)
    except RuntimeError as e:
        log.error("Fetch failed: %s", e)
        sys.exit(1)

    log.info("Fetched %d county records", len(records))

    # Log top-5 most vulnerable counties
    top5 = sorted(records, key=lambda r: r["vulnerability_score"], reverse=True)[:5]
    log.info("Top 5 most vulnerable counties:")
    for r in top5:
        log.info("  %-20s  score=%.3f  65+=%5.1f%%  poverty=%5.1f%%  LEP=%4.1f%%",
                 r["county"], r["vulnerability_score"],
                 r["pct_65plus"], r["pct_poverty"], r["pct_limited_english"])

    if args.dry_run:
        log.info("Dry run — skipping MongoDB write.")
        print(json.dumps(records[:2], indent=2))
        return

    try:
        n = store_to_mongodb(records)
        log.info("MongoDB upsert complete: %d records written to %s.%s",
                 n, DB_NAME, COLL_NAME)
        print(f"CENSUS_FETCH_OK: {len(records)} counties stored ({n} new/updated)")
    except Exception as e:
        log.error("MongoDB write failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
