"""
FPREN Agent Tool Registry
=========================
Clean Python functions wrapping MongoDB collections as callable tools for
LLM agents. Every function:
  - Returns a plain dict (JSON-serializable)
  - Never raises — returns {"error": "..."} on failure
  - Has a matching JSON schema entry in TOOL_SCHEMAS (OpenAI function-calling format)

Usage:
    from weather_rss.agent_tools import TOOL_SCHEMAS, TOOL_FUNCTIONS
    result = run_agent(system, TOOL_SCHEMAS, TOOL_FUNCTIONS, message)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import requests as _requests
from pymongo import MongoClient

log = logging.getLogger("agent_tools")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "weather_rss"


def _db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)[DB_NAME]


# ── Tool functions ─────────────────────────────────────────────────────────────

def get_active_alerts(county: str = "", severity: str = "", limit: int = 20) -> dict:
    """Get active NWS/IPAWS alerts, optionally filtered by county and severity."""
    try:
        db = _db()
        query: dict = {}
        if county:
            query["$or"] = [
                {"area_desc": {"$regex": county, "$options": "i"}},
                {"counties":  {"$regex": county, "$options": "i"}},
            ]
        if severity:
            query["severity"] = {"$regex": severity, "$options": "i"}
        docs = list(db["nws_alerts"].find(
            query,
            {"_id": 0, "event": 1, "headline": 1, "severity": 1, "urgency": 1,
             "area_desc": 1, "sent": 1, "expires": 1},
        ).limit(max(1, min(limit, 100))))
        return {"count": len(docs), "alerts": docs,
                "summary": f"{len(docs)} alert(s) matching filters"}
    except Exception as e:
        return {"error": str(e), "alerts": []}


def get_weather_obs(icao: str = "KGNV") -> dict:
    """Get the most recent METAR observation for an airport ICAO code."""
    try:
        db = _db()
        doc = db["airport_metar"].find_one({"icaoId": icao.upper()}, {"_id": 0})
        if not doc:
            return {"error": f"No METAR data for {icao}"}
        if doc.get("temp") is not None:
            doc["temp_f"] = round(doc["temp"] * 9 / 5 + 32, 1)
        return doc
    except Exception as e:
        return {"error": str(e)}


def get_weather_history(icao: str = "KGNV", hours: int = 24) -> dict:
    """Summarise weather history for an airport over the past N hours."""
    try:
        db = _db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        docs = list(db["weather_history"].find(
            {"icao": icao.upper(), "timestamp": {"$gte": cutoff}},
            {"_id": 0, "temp_f": 1, "wind_speed": 1, "flight_cat": 1, "timestamp": 1},
        ).sort("timestamp", -1).limit(200))
        if not docs:
            return {"icao": icao, "hours": hours, "count": 0,
                    "summary": "No weather history available"}
        max_wind  = max((d.get("wind_speed") or 0 for d in docs), default=0)
        ifr_count = sum(1 for d in docs if d.get("flight_cat") in ("IFR", "LIFR"))
        avg_temp  = round(sum(d.get("temp_f") or 0 for d in docs) / len(docs), 1)
        return {
            "icao": icao, "hours": hours, "observations": len(docs),
            "max_wind_mph": max_wind, "ifr_lifr_hours": ifr_count,
            "avg_temp_f": avg_temp,
            "summary": (f"{len(docs)} obs over {hours}h — max wind {max_wind} mph, "
                        f"{ifr_count} IFR/LIFR hrs, avg temp {avg_temp}°F"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_traffic_summary(county: str = "", limit: int = 20) -> dict:
    """Get current FL511 traffic incidents, optionally filtered by county."""
    try:
        db = _db()
        query: dict = {}
        if county:
            query["county"] = {"$regex": county, "$options": "i"}
        docs = list(db["fl_traffic"].find(
            query,
            {"_id": 0, "road": 1, "type": 1, "severity": 1,
             "is_full_closure": 1, "county": 1, "description": 1, "dot_district": 1},
        ).limit(max(1, min(limit, 100))))
        closures = sum(1 for d in docs if d.get("is_full_closure"))
        major    = sum(1 for d in docs if str(d.get("severity", "")).lower() == "major")
        return {
            "count": len(docs), "full_closures": closures, "major_incidents": major,
            "incidents": docs,
            "summary": (f"{len(docs)} FL511 incident(s) in {county or 'Florida'} — "
                        f"{closures} full closure(s), {major} major"),
        }
    except Exception as e:
        return {"error": str(e), "incidents": []}


def get_waze_summary(lat: float, lon: float, radius_km: float = 10.0) -> dict:
    """Get Waze traffic alerts and jams within radius_km of a coordinate."""
    try:
        db = _db()
        radius_m = radius_km * 1000
        cutoff   = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        geo_q = {"location": {"$nearSphere": {
            "$geometry": {"type": "Point", "coordinates": [lon, lat]},
            "$maxDistance": radius_m,
        }}}
        time_q = {"fetched_at": {"$gte": cutoff}}
        alerts = list(db["waze_alerts"].find(
            {**geo_q, **time_q},
            {"_id": 0, "type": 1, "subtype": 1, "street": 1, "city": 1, "reliability": 1},
        ).limit(50))
        jams = list(db["waze_jams"].find(
            {**geo_q, **time_q},
            {"_id": 0, "street": 1, "city": 1, "level": 1, "delay_sec": 1, "speed_kmh": 1},
        ).limit(25))
        avg_delay = round(sum(j.get("delay_sec") or 0 for j in jams) / max(len(jams), 1))
        max_level = max((j.get("level") or 0 for j in jams), default=0)
        return {
            "lat": lat, "lon": lon, "radius_km": radius_km,
            "n_alerts": len(alerts), "n_jams": len(jams),
            "max_jam_level": max_level, "avg_jam_delay_sec": avg_delay,
            "alerts": alerts[:10], "jams": jams[:10],
            "summary": (f"{len(alerts)} Waze alert(s), {len(jams)} jam(s) "
                        f"(max level {max_level}, avg delay {avg_delay}s) "
                        f"within {radius_km} km"),
        }
    except Exception as e:
        return {"error": str(e), "n_alerts": 0, "n_jams": 0}


def get_census(county: str) -> dict:
    """Get US Census demographic and vulnerability data for a Florida county."""
    try:
        db = _db()
        doc = db["fl_census"].find_one(
            {"county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0},
            sort=[("year", -1)],
        )
        if not doc:
            return {"error": f"No Census data for {county} county"}
        return doc
    except Exception as e:
        return {"error": str(e)}


def get_evacuation_zones(county: str) -> dict:
    """Get hurricane evacuation zones for a Florida county."""
    try:
        db = _db()
        docs = list(db["fl_evacuation_zones"].find(
            {"county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0, "zone": 1, "description": 1, "has_coastal": 1},
        ).sort("zone_order", 1))
        return {
            "county": county, "zones": docs,
            "highest_zone": docs[0]["zone"] if docs else None,
            "summary": (f"{len(docs)} zone(s); highest: "
                        f"{docs[0]['zone'] if docs else 'N/A'}"),
        }
    except Exception as e:
        return {"error": str(e), "zones": []}


def get_evacuation_routes(county: str) -> dict:
    """Get designated hurricane evacuation routes for a Florida county."""
    try:
        db = _db()
        docs = list(db["fl_evacuation_routes"].find(
            {"county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0, "name": 1, "road": 1, "direction": 1,
             "route_type": 1, "serves_zones": 1},
        ))
        return {"county": county, "count": len(docs), "routes": docs}
    except Exception as e:
        return {"error": str(e), "routes": []}


def get_zone_stream_status() -> dict:
    """Get the current live status of all FPREN Icecast zone streams."""
    try:
        r = _requests.get("http://localhost:8000/status-json.xsl", timeout=5)
        data = r.json()
        sources = data.get("icestats", {}).get("source", [])
        if isinstance(sources, dict):
            sources = [sources]
        streams = [
            {"mount": s.get("listenurl", ""), "listeners": s.get("listeners", 0),
             "name": s.get("server_name", "")}
            for s in sources
        ]
        return {
            "active_mounts": len(streams), "streams": streams,
            "summary": f"{len(streams)} active Icecast mount(s)",
        }
    except Exception as e:
        return {"error": str(e), "active_mounts": 0, "streams": []}


def get_situation_report() -> dict:
    """Get the most recent AI-generated situation report from MongoDB."""
    try:
        db = _db()
        doc = db["situation_reports"].find_one(
            {}, {"_id": 0}, sort=[("generated_at", -1)]
        )
        return doc if doc else {"error": "No situation reports found"}
    except Exception as e:
        return {"error": str(e)}


def write_situation_report(text: str, data_snapshot: dict = None) -> dict:
    """Save an AI-generated situation report to MongoDB (agent write tool)."""
    try:
        db = _db()
        db["situation_reports"].insert_one({
            "text":          text,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "data_snapshot": data_snapshot or {},
        })
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# ── Tool schemas (OpenAI function-calling format) ──────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_active_alerts",
            "description": (
                "Get active NWS/IPAWS weather alerts for Florida. "
                "Filter by county name and/or severity level."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "county":   {"type": "string",
                                 "description": "County name, e.g. 'Alachua', 'Miami-Dade'"},
                    "severity": {"type": "string",
                                 "description": "Severity: 'Extreme', 'Severe', 'Moderate', 'Minor'"},
                    "limit":    {"type": "integer",
                                 "description": "Max alerts to return (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_obs",
            "description": "Get the current METAR weather observation for a Florida airport.",
            "parameters": {
                "type": "object",
                "properties": {
                    "icao": {"type": "string",
                             "description": "Airport ICAO code, e.g. 'KGNV', 'KTPA', 'KMIA'"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_history",
            "description": "Get a summary of recent weather history for a Florida airport.",
            "parameters": {
                "type": "object",
                "properties": {
                    "icao":  {"type": "string",  "description": "Airport ICAO code"},
                    "hours": {"type": "integer", "description": "Hours of history to summarise (default 24)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_traffic_summary",
            "description": "Get current FL511 road incidents for Florida, optionally filtered by county.",
            "parameters": {
                "type": "object",
                "properties": {
                    "county": {"type": "string",  "description": "County name to filter"},
                    "limit":  {"type": "integer", "description": "Max incidents to return (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_waze_summary",
            "description": "Get Waze real-time traffic alerts and jams near a lat/lon coordinate.",
            "parameters": {
                "type": "object",
                "required": ["lat", "lon"],
                "properties": {
                    "lat":       {"type": "number", "description": "Latitude"},
                    "lon":       {"type": "number", "description": "Longitude"},
                    "radius_km": {"type": "number", "description": "Search radius in km (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_census",
            "description": (
                "Get US Census demographic data and vulnerability score for a Florida county. "
                "Includes population, % elderly, % poverty, % disability, vulnerability label."
            ),
            "parameters": {
                "type": "object",
                "required": ["county"],
                "properties": {
                    "county": {"type": "string", "description": "County name, e.g. 'Alachua'"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evacuation_zones",
            "description": "Get hurricane evacuation zones (A–E) for a Florida county.",
            "parameters": {
                "type": "object",
                "required": ["county"],
                "properties": {
                    "county": {"type": "string", "description": "County name"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evacuation_routes",
            "description": "Get designated hurricane evacuation routes for a Florida county.",
            "parameters": {
                "type": "object",
                "required": ["county"],
                "properties": {
                    "county": {"type": "string", "description": "County name"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_zone_stream_status",
            "description": "Get the live status of all FPREN Icecast broadcast zone streams.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_situation_report",
            "description": "Retrieve the most recent AI-generated FPREN situation report.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Read-only tool set (safe for operator assistant)
TOOL_SCHEMAS_READONLY = TOOL_SCHEMAS  # all above are read-only

# Write-enabled tool set (for situation awareness agent)
TOOL_SCHEMAS_WRITE = TOOL_SCHEMAS + [
    {
        "type": "function",
        "function": {
            "name": "write_situation_report",
            "description": "Save the completed situation report text to MongoDB.",
            "parameters": {
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string",
                             "description": "The full situation report text to save"},
                },
            },
        },
    },
]

# Map names → callables (used by run_agent)
TOOL_FUNCTIONS: dict = {
    "get_active_alerts":     get_active_alerts,
    "get_weather_obs":       get_weather_obs,
    "get_weather_history":   get_weather_history,
    "get_traffic_summary":   get_traffic_summary,
    "get_waze_summary":      get_waze_summary,
    "get_census":            get_census,
    "get_evacuation_zones":  get_evacuation_zones,
    "get_evacuation_routes": get_evacuation_routes,
    "get_zone_stream_status": get_zone_stream_status,
    "get_situation_report":  get_situation_report,
    "write_situation_report": write_situation_report,
}
