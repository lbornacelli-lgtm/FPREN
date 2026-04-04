"""
FPREN Census AI Analyzer
========================
Uses the FPREN LiteLLM client to generate AI-powered analysis of Florida
county census data for emergency response, alert impact assessment, and
Business Continuity planning.

Connects census demographics (fl_census) with active NWS alerts (nws_alerts)
to produce population-aware impact narratives.

All AI calls use the existing ai_client module — same UF LiteLLM endpoint
(https://api.ai.it.ufl.edu, llama-3.3-70b-instruct).
"""

import logging
import os
import sys

from pymongo import MongoClient

# Add weather_station to path so we can import ai_client
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "weather_station"))

try:
    from services.ai_client import chat as ai_chat, is_configured as ai_ready
    _AI_AVAILABLE = True
except ImportError:
    _AI_AVAILABLE = False

log = logging.getLogger("census_ai")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "weather_rss"

# ── Vulnerability label ────────────────────────────────────────────────────

def vulnerability_label(score: float) -> str:
    if score >= 0.70: return "Critical"
    if score >= 0.50: return "High"
    if score >= 0.30: return "Moderate"
    return "Low"


def vulnerability_color(score: float) -> str:
    if score >= 0.70: return "danger"
    if score >= 0.50: return "warning"
    if score >= 0.30: return "info"
    return "success"


# ── MongoDB helpers ────────────────────────────────────────────────────────

def _get_col(collection: str):
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        return client[DB_NAME][collection], client
    except Exception:
        return None, None


def get_county_census(county: str) -> dict | None:
    """Return the most recent census record for a county, or None."""
    col, client = _get_col("fl_census")
    if col is None:
        return None
    try:
        rec = col.find_one(
            {"county": {"$regex": f"^{county}$", "$options": "i"}},
            sort=[("year", -1)],
        )
        client.close()
        return rec
    except Exception:
        client.close()
        return None


def get_all_counties_census() -> list[dict]:
    """Return all FL county census records sorted by vulnerability score desc."""
    col, client = _get_col("fl_census")
    if col is None:
        return []
    try:
        docs = list(col.find({}, {"_id": 0}).sort("vulnerability_score", -1))
        client.close()
        return docs
    except Exception:
        client.close()
        return []


def get_active_alerts_for_county(county: str) -> list[dict]:
    """Return active NWS alerts that mention a county."""
    col, client = _get_col("nws_alerts")
    if col is None:
        return []
    try:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        docs = list(col.find(
            {
                "$or": [
                    {"area_desc": {"$regex": county, "$options": "i"}},
                    {"counties":  {"$regex": county, "$options": "i"}},
                ],
                "expires": {"$gt": now_iso},
            },
            {"_id": 0, "event": 1, "severity": 1, "headline": 1,
             "area_desc": 1, "urgency": 1, "sent": 1},
            sort=[("severity", 1)],
        ))
        client.close()
        return docs
    except Exception:
        client.close()
        return []


# ── AI analysis functions ──────────────────────────────────────────────────

_SYSTEM_VULNERABILITY = """You are an emergency management analyst for FPREN
(Florida Public Radio Emergency Network). You analyze US Census demographic
data to assess community vulnerability for emergency planning. Be concise,
factual, and actionable. Focus on what emergency managers and broadcasters
need to know. Response should be 3-5 sentences maximum."""

_SYSTEM_IMPACT = """You are an emergency broadcast analyst for FPREN
(Florida Public Radio Emergency Network). You assess the population impact
of active NWS weather alerts using Census demographic data. Your analysis
helps prioritize emergency messaging and identify vulnerable populations who
need targeted outreach. Be specific, concise, and actionable. 2-4 sentences."""

_SYSTEM_BCP = """You are a business continuity specialist for FPREN
(Florida Public Radio Emergency Network). You analyze county demographics to
provide specific recommendations for maintaining broadcast operations and
serving vulnerable populations during emergencies. Focus on actionable
guidance for radio station operators. 3-5 sentences."""


def analyze_county_vulnerability(county: str, census: dict | None = None) -> str:
    """
    Generate an AI narrative about a county's emergency vulnerability.
    Returns plain text (2–5 sentences).
    Falls back to a rule-based summary if AI is unavailable.
    """
    if census is None:
        census = get_county_census(county)
    if census is None:
        return f"No census data available for {county} County."

    prompt = f"""Analyze the emergency vulnerability for {county} County, Florida
based on this Census data (ACS {census.get('year','2022')} 5-year estimates):

- Total population: {census.get('population_total', 0):,}
- Population 65+: {census.get('population_65plus', 0):,} ({census.get('pct_65plus', 0):.1f}%)
- Population under 18: {census.get('population_under18', 0):,} ({census.get('pct_under18', 0):.1f}%)
- Below poverty level: {census.get('population_in_poverty', 0):,} ({census.get('pct_poverty', 0):.1f}%)
- Limited English proficiency: {census.get('limited_english', 0):,} ({census.get('pct_limited_english', 0):.1f}%)
- With disability: {census.get('population_with_disability', 0):,} ({census.get('pct_disability', 0):.1f}%)
- Median household income: ${census.get('median_household_income', 0):,}
- Vulnerability score: {census.get('vulnerability_score', 0):.2f}/1.0 ({vulnerability_label(census.get('vulnerability_score', 0))})

Provide a concise vulnerability assessment for emergency management purposes,
highlighting which population groups require special consideration during
weather emergencies and what types of outreach FPREN should prioritize."""

    if not _AI_AVAILABLE or not ai_ready():
        return _fallback_vulnerability_summary(county, census)

    try:
        return ai_chat(prompt, system=_SYSTEM_VULNERABILITY, max_tokens=200)
    except Exception as e:
        log.warning("AI vulnerability analysis failed for %s: %s", county, e)
        return _fallback_vulnerability_summary(county, census)


def analyze_alert_impact(county: str, alerts: list[dict] | None = None,
                          census: dict | None = None) -> str:
    """
    Generate an AI assessment of how active alerts impact a county's population.
    Returns plain text (2–4 sentences).
    """
    if census is None:
        census = get_county_census(county)
    if alerts is None:
        alerts = get_active_alerts_for_county(county)

    if not alerts:
        return f"No active NWS alerts for {county} County at this time."
    if census is None:
        return f"{len(alerts)} active alert(s) for {county} County. Census data unavailable for population impact assessment."

    alert_lines = "\n".join(
        f"  - {a.get('event','?')} ({a.get('severity','?')} / {a.get('urgency','?')}): {a.get('headline','')[:80]}"
        for a in alerts[:5]
    )

    prompt = f"""Active NWS weather alerts for {county} County, Florida:
{alert_lines}

County demographics (ACS {census.get('year','2022')}):
- Total population: {census.get('population_total', 0):,}
- Elderly (65+): {census.get('pct_65plus', 0):.1f}% ({census.get('population_65plus', 0):,} people)
- In poverty: {census.get('pct_poverty', 0):.1f}% ({census.get('population_in_poverty', 0):,} people)
- Limited English: {census.get('pct_limited_english', 0):.1f}% ({census.get('limited_english', 0):,} people)
- Vulnerability score: {census.get('vulnerability_score', 0):.2f}/1.0

Assess the population impact of these alerts on {county} County, focusing on
which vulnerable groups are most at risk and what targeted messaging FPREN should broadcast."""

    if not _AI_AVAILABLE or not ai_ready():
        return _fallback_impact_summary(county, alerts, census)

    try:
        return ai_chat(prompt, system=_SYSTEM_IMPACT, max_tokens=180)
    except Exception as e:
        log.warning("AI alert impact analysis failed for %s: %s", county, e)
        return _fallback_impact_summary(county, alerts, census)


def analyze_bcp_demographics(county: str, asset_name: str = "",
                              census: dict | None = None) -> str:
    """
    Generate BCP-specific demographic recommendations for an asset's county.
    Used by business_continuity_report.Rmd.
    Returns plain text (3–5 sentences).
    """
    if census is None:
        census = get_county_census(county)
    if census is None:
        return f"No census data available for {county} County."

    asset_ctx = f" for the {asset_name} facility" if asset_name else ""

    prompt = f"""Business continuity planning{asset_ctx} in {county} County, Florida.

County demographics (ACS {census.get('year','2022')}):
- Total population served: {census.get('population_total', 0):,}
- Elderly (65+): {census.get('pct_65plus', 0):.1f}% — likely heavy radio listeners
- In poverty: {census.get('pct_poverty', 0):.1f}% — may lack internet/cell alternatives
- Limited English: {census.get('pct_limited_english', 0):.1f}% — need translated messaging
- With disability: {census.get('pct_disability', 0):.1f}% — require accessible alerts
- Median household income: ${census.get('median_household_income', 0):,}
- Vulnerability score: {census.get('vulnerability_score', 0):.2f}/1.0 ({vulnerability_label(census.get('vulnerability_score', 0))})

Provide specific business continuity recommendations for FPREN radio operations
in this county, focusing on maintaining broadcast coverage for vulnerable populations
and adjusting emergency messaging strategies based on the demographic profile."""

    if not _AI_AVAILABLE or not ai_ready():
        return _fallback_bcp_demographics(county, census)

    try:
        return ai_chat(prompt, system=_SYSTEM_BCP, max_tokens=220)
    except Exception as e:
        log.warning("AI BCP demographics failed for %s: %s", county, e)
        return _fallback_bcp_demographics(county, census)


# ── Rule-based fallbacks (no AI required) ─────────────────────────────────

def _fallback_vulnerability_summary(county: str, census: dict) -> str:
    score = census.get("vulnerability_score", 0)
    label = vulnerability_label(score)
    parts = []
    if census.get("pct_65plus", 0) > 20:
        parts.append(f"high elderly population ({census['pct_65plus']:.1f}% over 65)")
    if census.get("pct_poverty", 0) > 15:
        parts.append(f"elevated poverty rate ({census['pct_poverty']:.1f}%)")
    if census.get("pct_limited_english", 0) > 5:
        parts.append(f"significant limited-English population ({census['pct_limited_english']:.1f}%)")
    factors = ", ".join(parts) if parts else "mixed risk factors"
    pop = f"{census.get('population_total', 0):,}"
    return (
        f"{county} County (pop. {pop}) has a {label.lower()} vulnerability score "
        f"({score:.2f}/1.0) driven by {factors}. "
        f"Emergency broadcasts should prioritize accessibility and clear messaging "
        f"for elderly residents and those with limited English proficiency."
    )


def _fallback_impact_summary(county: str, alerts: list[dict], census: dict) -> str:
    n = len(alerts)
    pop = census.get("population_total", 0)
    sev = [a.get("severity", "Unknown") for a in alerts]
    worst = "Extreme" if "Extreme" in sev else "Severe" if "Severe" in sev else sev[0] if sev else "Unknown"
    return (
        f"{n} active alert(s) ({worst} severity) affecting {county} County "
        f"(population {pop:,}). "
        f"Approximately {int(pop * census.get('pct_65plus', 0) / 100):,} elderly residents "
        f"and {int(pop * census.get('pct_poverty', 0) / 100):,} residents below poverty "
        f"level are at elevated risk and should be prioritized in emergency broadcasts."
    )


def _fallback_bcp_demographics(county: str, census: dict) -> str:
    score  = census.get("vulnerability_score", 0)
    label  = vulnerability_label(score)
    pop    = census.get("population_total", 0)
    return (
        f"{county} County has a {label.lower()} vulnerability score ({score:.2f}/1.0) "
        f"with a population of {pop:,}. "
        f"Priority groups for FPREN broadcasting include the {census.get('pct_65plus',0):.1f}% elderly "
        f"and {census.get('pct_poverty',0):.1f}% below poverty who may rely solely on radio. "
        f"Ensure broadcast continuity through any power or infrastructure disruption to serve these audiences."
    )


# ── Convenience: enrich an alert dict with census + AI impact ─────────────

_FL_COUNTIES = {
    "Alachua", "Baker", "Bay", "Bradford", "Brevard", "Broward",
    "Calhoun", "Charlotte", "Citrus", "Clay", "Collier", "Columbia",
    "DeSoto", "Dixie", "Duval", "Escambia", "Flagler", "Franklin",
    "Gadsden", "Gilchrist", "Glades", "Gulf", "Hamilton", "Hardee",
    "Hendry", "Hernando", "Highlands", "Hillsborough", "Holmes",
    "Indian River", "Jackson", "Jefferson", "Lafayette", "Lake",
    "Lee", "Leon", "Levy", "Liberty", "Madison", "Manatee",
    "Marion", "Martin", "Miami-Dade", "Monroe", "Nassau", "Okaloosa",
    "Okeechobee", "Orange", "Osceola", "Palm Beach", "Pasco",
    "Pinellas", "Polk", "Putnam", "Santa Rosa", "Sarasota",
    "Seminole", "St. Johns", "St. Lucie", "Sumter", "Suwannee",
    "Taylor", "Union", "Volusia", "Wakulla", "Walton", "Washington",
}

# Build a normalized lowercase lookup: strip hyphens and periods for matching
_COUNTY_NORM = {
    c.lower().replace("-", " ").replace(".", "").strip(): c
    for c in _FL_COUNTIES
}

# Geographic qualifier words that prefix county names in NWS area_desc
_AREA_QUALIFIERS = {
    "coastal", "inland", "northern", "southern", "eastern", "western",
    "central", "mainland", "outer", "inner", "lower", "upper",
    "northeast", "northwest", "southeast", "southwest", "barrier",
    "islands", "offshore", "metro", "suburban",
}


def _extract_counties_from_area(area_desc: str, counties_list=None) -> set:
    """
    Robustly extract FL county names from an NWS area_desc string.
    Strips geographic qualifier prefixes (Coastal, Inland, Northern, etc.)
    and normalizes hyphen/period differences (Miami Dade → Miami-Dade).
    """
    import re
    mentioned = set()

    # Also accept pre-parsed counties list
    for name in (counties_list or []):
        if isinstance(name, str):
            clean = re.sub(r"\s*County\b.*", "", name, flags=re.IGNORECASE).strip()
            key = clean.lower().replace("-", " ").replace(".", "").strip()
            if key in _COUNTY_NORM:
                mentioned.add(_COUNTY_NORM[key])

    # Split area_desc on semicolons and commas, then process each segment
    for segment in re.split(r"[;,]", area_desc or ""):
        segment = segment.strip()
        # Drop " County" (and anything after) from the end
        segment = re.sub(r"\s+County\b.*", "", segment, flags=re.IGNORECASE).strip()
        if not segment:
            continue
        # Strip geographic qualifier words from the front
        words = segment.split()
        while words and words[0].lower() in _AREA_QUALIFIERS:
            words.pop(0)
        name = " ".join(words).strip()
        if not name:
            continue
        # Normalize and look up
        key = name.lower().replace("-", " ").replace(".", "").strip()
        if key in _COUNTY_NORM:
            mentioned.add(_COUNTY_NORM[key])

    return mentioned


def enrich_alert_with_census(alert: dict) -> dict:
    """
    Add census context and AI impact assessment to an NWS alert dict.
    Returns a copy with new keys: census, ai_impact.
    Used by Flask /api/census/impact/<alert_id>.
    """
    import copy
    out = copy.deepcopy(alert)

    area = alert.get("area_desc", "") or ""
    counties_list = alert.get("counties") or []
    if isinstance(counties_list, str):
        counties_list = [c.strip() for c in counties_list.split(";") if c.strip()]

    mentioned = _extract_counties_from_area(area, counties_list)

    census_data = {}
    ai_impacts  = []
    for county in list(mentioned)[:5]:  # cap at 5 counties per alert
        cdata = get_county_census(county)
        if cdata:
            census_data[county] = {
                "population_total":   cdata.get("population_total"),
                "pct_65plus":         cdata.get("pct_65plus"),
                "pct_poverty":        cdata.get("pct_poverty"),
                "vulnerability_score": cdata.get("vulnerability_score"),
                "vulnerability_label": vulnerability_label(cdata.get("vulnerability_score", 0)),
            }
            ai_impacts.append(f"{county}: {analyze_alert_impact(county, [alert], cdata)}")

    out["census_impact"] = {
        "counties_affected": list(mentioned),
        "total_population_at_risk": sum(
            v.get("population_total", 0) for v in census_data.values()
        ),
        "county_data": census_data,
        "ai_analysis": "\n\n".join(ai_impacts) if ai_impacts else "No census data available.",
    }
    return out


if __name__ == "__main__":
    # Quick test: analyze Alachua County
    logging.basicConfig(level=logging.INFO)
    county = sys.argv[1] if len(sys.argv) > 1 else "Alachua"
    print(f"\n=== Vulnerability: {county} ===")
    print(analyze_county_vulnerability(county))
    print(f"\n=== Alert Impact: {county} ===")
    print(analyze_alert_impact(county))
    print(f"\n=== BCP Demographics: {county} ===")
    print(analyze_bcp_demographics(county, "WUFT Studio"))
