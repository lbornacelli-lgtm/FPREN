#!/usr/bin/env python3
"""
FPREN Situation Awareness Agent
================================
Runs every 15 minutes (via systemd timer). Uses the LLM tool-calling loop to
synthesise all live FPREN data feeds into a plain-English situation report,
then saves it to MongoDB `situation_reports`.

The report is surfaced in the Flask admin dashboard (/api/agent/situation)
and can be added to scheduled broadcasts.

Usage:
    python3 situation_agent.py              # run once
    python3 situation_agent.py --dry-run    # print report, skip DB write
"""

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [situation_agent] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("situation_agent")

# Add project root to path so imports resolve from any working dir
_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from weather_rss.agent_tools import (
    TOOL_SCHEMAS_WRITE, TOOL_FUNCTIONS,
    get_active_alerts, get_weather_obs, get_traffic_summary,
    get_waze_summary, get_evacuation_zones, get_zone_stream_status,
)
from weather_station.services.ai_client import run_agent, is_configured

# Nine primary FL cities monitored by FPREN and their nearest airport + coords
_MONITORED = [
    {"city": "Gainesville",  "icao": "KGNV", "county": "Alachua",    "lat": 29.65, "lon": -82.33},
    {"city": "Jacksonville", "icao": "KJAX", "county": "Duval",       "lat": 30.33, "lon": -81.66},
    {"city": "Tallahassee",  "icao": "KTLH", "county": "Leon",        "lat": 30.44, "lon": -84.28},
    {"city": "Tampa",        "icao": "KTPA", "county": "Hillsborough", "lat": 27.96, "lon": -82.46},
    {"city": "Orlando",      "icao": "KMCO", "county": "Orange",      "lat": 28.54, "lon": -81.38},
    {"city": "Miami",        "icao": "KMIA", "county": "Miami-Dade",  "lat": 25.76, "lon": -80.19},
]

SYSTEM_PROMPT = """\
You are the FPREN Situation Awareness Agent — an AI embedded in the Florida Public \
Radio Emergency Network. Your job is to call the available tools to gather current \
data across Florida and produce a concise, professional situation report suitable for \
emergency managers and broadcast operators.

When writing the report:
- Lead with the most serious active threat, if any.
- Cover: active NWS alerts, weather conditions at key airports, road / traffic \
  conditions, and Icecast stream health.
- Use plain language — no bullet lists in the final output; write in clear sentences.
- Keep the total report under 250 words.
- End with a one-sentence overall risk level: LOW / MODERATE / HIGH and brief rationale.
- After writing the report text, call write_situation_report to save it.
"""

TASK_PROMPT = """\
Generate a current situation report for FPREN. Check active alerts statewide, \
weather at Gainesville (KGNV), Tampa (KTPA), Miami (KMIA), and Jacksonville (KJAX), \
traffic in Alachua and Hillsborough counties, Waze conditions near Tampa \
(lat 27.96, lon -82.46), and current Icecast stream status. \
Then write and save the situation report.
"""


def _build_data_snapshot() -> dict:
    """Pre-fetch key data for the snapshot field (for audit log)."""
    return {
        "alerts_statewide": get_active_alerts(limit=5).get("count", 0),
        "stream_status":    get_zone_stream_status().get("active_mounts", 0),
        "traffic_statewide": get_traffic_summary(limit=5).get("count", 0),
    }


def run(dry_run: bool = False) -> str:
    if not is_configured():
        log.error("UF_LITELLM_API_KEY not set — cannot run agent")
        sys.exit(1)

    log.info("Starting situation awareness agent run%s",
             " (dry-run)" if dry_run else "")

    # Swap out write_situation_report for a dry-run no-op if needed
    tool_fns = dict(TOOL_FUNCTIONS)
    if dry_run:
        tool_fns["write_situation_report"] = lambda text, **_: (
            {"ok": True, "dry_run": True}
        )

    result = run_agent(
        system_prompt   = SYSTEM_PROMPT,
        tools           = TOOL_SCHEMAS_WRITE,
        tool_functions  = tool_fns,
        initial_message = TASK_PROMPT,
        max_iterations  = 12,
        max_tokens      = 600,
    )

    log.info("Agent finished in %d tool-calling round(s)", result["iterations"])
    log.info("Tools called: %s", [t["tool"] for t in result["tool_calls"]])

    report_text = result["response"]
    if report_text:
        log.info("Situation report:\n%s", report_text)
    else:
        log.warning("Agent produced no final text response")

    return report_text


def main():
    parser = argparse.ArgumentParser(description="FPREN Situation Awareness Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run agent but skip writing to MongoDB")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
