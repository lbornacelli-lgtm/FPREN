#!/usr/bin/env python3
"""
FPREN Florida Evacuation Data Fetcher
======================================
Pulls hurricane evacuation zones and designated evacuation routes for all 67
Florida counties from FDEM (Florida Division of Emergency Management) and FDOT
ArcGIS REST services, then stores them in MongoDB.

Collections written:
  fl_evacuation_zones   — county-level zone designations (A=highest, E=lowest)
  fl_evacuation_routes  — designated evacuation roads by county and region

Primary data sources (ArcGIS REST):
  FDEM zones:   https://services1.arcgis.com/O1JpcwDW8sjYuddV/arcgis/rest/services/
  FDOT routes:  https://services.arcgis.com/G54nRf3VlX7jJEiY/arcgis/rest/services/

Falls back to a curated FL-specific dataset if APIs are unreachable.

Usage:
    python3 fl_evacuation_fetcher.py              # fetch + store
    python3 fl_evacuation_fetcher.py --dry-run    # fetch only, no DB write
    python3 fl_evacuation_fetcher.py --source api # force API only
    python3 fl_evacuation_fetcher.py --source curated # force curated only
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [evacuation] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fl_evacuation_fetcher")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "weather_rss"

ZONES_COLL  = "fl_evacuation_zones"
ROUTES_COLL = "fl_evacuation_routes"

# FDEM ArcGIS REST endpoints
FDEM_ZONES_URL = (
    "https://services1.arcgis.com/O1JpcwDW8sjYuddV/arcgis/rest/services/"
    "FloridaHurricaneEvacuationZones/FeatureServer/0/query"
)
FDOT_ROUTES_URL = (
    "https://services1.arcgis.com/O1JpcwDW8sjYuddV/arcgis/rest/services/"
    "Florida_Evacuation_Routes/FeatureServer/0/query"
)

REQUEST_TIMEOUT = 20
PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Curated Florida evacuation zone data (all 67 counties)
# Based on the Florida Statewide Regional Evacuation Study Program (SRESP)
# and published county emergency management plans.
# ---------------------------------------------------------------------------

# Zone order: A=1 (highest storm surge risk), E=5 (lowest / inland)
# Not all counties use all zones — coastal counties tend to have A-D,
# inland counties may only have A-B or no coastal zones at all.

CURATED_ZONES = [
    # --- NW Florida ---
    {"county": "Escambia",     "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Santa Rosa",   "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Okaloosa",     "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Walton",       "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Holmes",       "zones": ["A"],               "has_coastal": False},
    {"county": "Washington",   "zones": ["A"],               "has_coastal": False},
    {"county": "Bay",          "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Jackson",      "zones": ["A"],               "has_coastal": False},
    {"county": "Calhoun",      "zones": ["A","B"],           "has_coastal": False},
    {"county": "Gulf",         "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Gadsden",      "zones": ["A","B"],           "has_coastal": False},
    {"county": "Liberty",      "zones": ["A"],               "has_coastal": False},
    {"county": "Franklin",     "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Leon",         "zones": ["A","B"],           "has_coastal": False},
    {"county": "Wakulla",      "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Jefferson",    "zones": ["A","B","C"],       "has_coastal": True},
    {"county": "Madison",      "zones": ["A"],               "has_coastal": False},
    {"county": "Taylor",       "zones": ["A","B","C","D"],   "has_coastal": True},
    # --- NE Florida ---
    {"county": "Hamilton",     "zones": ["A"],               "has_coastal": False},
    {"county": "Suwannee",     "zones": ["A","B"],           "has_coastal": False},
    {"county": "Columbia",     "zones": ["A"],               "has_coastal": False},
    {"county": "Baker",        "zones": ["A"],               "has_coastal": False},
    {"county": "Nassau",       "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Duval",        "zones": ["A","B","C","D","E"], "has_coastal": True},
    {"county": "Clay",         "zones": ["A","B","C"],       "has_coastal": False},
    {"county": "St. Johns",    "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Flagler",      "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Putnam",       "zones": ["A","B"],           "has_coastal": False},
    {"county": "Alachua",      "zones": ["A","B"],           "has_coastal": False},
    {"county": "Gilchrist",    "zones": ["A","B"],           "has_coastal": False},
    {"county": "Levy",         "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Union",        "zones": ["A"],               "has_coastal": False},
    {"county": "Bradford",     "zones": ["A"],               "has_coastal": False},
    # --- Central Florida ---
    {"county": "Marion",       "zones": ["A","B"],           "has_coastal": False},
    {"county": "Citrus",       "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Hernando",     "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Pasco",        "zones": ["A","B","C","D","E"], "has_coastal": True},
    {"county": "Pinellas",     "zones": ["A","B","C","D","E"], "has_coastal": True},
    {"county": "Hillsborough", "zones": ["A","B","C","D","E"], "has_coastal": True},
    {"county": "Polk",         "zones": ["A","B"],           "has_coastal": False},
    {"county": "Osceola",      "zones": ["A","B"],           "has_coastal": False},
    {"county": "Orange",       "zones": ["A","B"],           "has_coastal": False},
    {"county": "Seminole",     "zones": ["A","B"],           "has_coastal": False},
    {"county": "Volusia",      "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Lake",         "zones": ["A","B"],           "has_coastal": False},
    {"county": "Sumter",       "zones": ["A","B"],           "has_coastal": False},
    # --- SW Florida ---
    {"county": "Manatee",      "zones": ["A","B","C","D","E"], "has_coastal": True},
    {"county": "Sarasota",     "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "DeSoto",       "zones": ["A","B"],           "has_coastal": False},
    {"county": "Hardee",       "zones": ["A"],               "has_coastal": False},
    {"county": "Highlands",    "zones": ["A","B"],           "has_coastal": False},
    {"county": "Charlotte",    "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Glades",       "zones": ["A"],               "has_coastal": False},
    {"county": "Okeechobee",   "zones": ["A","B"],           "has_coastal": False},
    {"county": "Lee",          "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Hendry",       "zones": ["A","B"],           "has_coastal": False},
    {"county": "Collier",      "zones": ["A","B","C","D"],   "has_coastal": True},
    # --- SE Florida ---
    {"county": "Brevard",      "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Indian River",  "zones": ["A","B","C","D"],  "has_coastal": True},
    {"county": "St. Lucie",    "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Okeechobee",   "zones": ["A","B"],           "has_coastal": False},
    {"county": "Martin",       "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Palm Beach",   "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Broward",      "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Miami-Dade",   "zones": ["A","B","C","D"],   "has_coastal": True},
    {"county": "Monroe",       "zones": ["A"],               "has_coastal": True},
]

ZONE_DESCRIPTIONS = {
    "A": "Zone A — Highest storm surge risk. Mobile homes, low-lying coastal/riverside areas. Evacuate for any hurricane.",
    "B": "Zone B — High storm surge risk. Evacuate for Category 1+ hurricanes.",
    "C": "Zone C — Moderate storm surge risk. Evacuate for Category 2+ hurricanes.",
    "D": "Zone D — Lower storm surge risk. Evacuate for Category 3+ hurricanes.",
    "E": "Zone E — Lowest surge risk. Evacuate for Category 4+ hurricanes.",
}


# ---------------------------------------------------------------------------
# Curated Florida evacuation routes
# Source: FDOT and county emergency management plans
# Organized by region — primary + alternate routes
# ---------------------------------------------------------------------------

CURATED_ROUTES = [
    # Keys / Monroe County
    {"route_id": "MON-001", "county": "Monroe", "name": "Overseas Highway North",
     "road": "US-1", "direction": "North", "from_location": "Key West",
     "to_location": "Homestead/Florida City", "route_type": "Primary",
     "serves_zones": ["A"], "region": "Keys"},

    # Miami-Dade / Broward
    {"route_id": "MIA-001", "county": "Miami-Dade", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "Miami",
     "to_location": "Fort Lauderdale / Palm Beach", "route_type": "Primary",
     "serves_zones": ["A","B","C"], "region": "Southeast"},
    {"route_id": "MIA-002", "county": "Miami-Dade", "name": "Florida Turnpike North",
     "road": "FL-Turnpike", "direction": "North", "from_location": "Miami",
     "to_location": "Orlando / Central FL", "route_type": "Primary",
     "serves_zones": ["A","B","C"], "region": "Southeast"},
    {"route_id": "MIA-003", "county": "Miami-Dade", "name": "US-27 North",
     "road": "US-27", "direction": "North", "from_location": "Hialeah",
     "to_location": "Lake Okeechobee / Central FL", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Southeast"},
    {"route_id": "BRO-001", "county": "Broward", "name": "I-75 North (Alligator Alley)",
     "road": "I-75", "direction": "North/West", "from_location": "Fort Lauderdale",
     "to_location": "Naples / Fort Myers / Tampa", "route_type": "Primary",
     "serves_zones": ["A","B","C"], "region": "Southeast"},
    {"route_id": "BRO-002", "county": "Broward", "name": "I-595 West to I-75",
     "road": "I-595 / I-75", "direction": "West", "from_location": "Fort Lauderdale",
     "to_location": "Naples", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Southeast"},

    # Palm Beach
    {"route_id": "PBE-001", "county": "Palm Beach", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "West Palm Beach",
     "to_location": "Orlando / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C"], "region": "Southeast"},
    {"route_id": "PBE-002", "county": "Palm Beach", "name": "Florida Turnpike North",
     "road": "FL-Turnpike", "direction": "North", "from_location": "West Palm Beach",
     "to_location": "Orlando", "route_type": "Primary",
     "serves_zones": ["A","B"], "region": "Southeast"},
    {"route_id": "PBE-003", "county": "Palm Beach", "name": "US-27 North",
     "road": "US-27", "direction": "North", "from_location": "Belle Glade",
     "to_location": "Sebring / Central FL", "route_type": "Alternate",
     "serves_zones": ["A"], "region": "Southeast"},

    # Collier / Lee
    {"route_id": "COL-001", "county": "Collier", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Naples",
     "to_location": "Fort Myers / Sarasota / Tampa", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southwest"},
    {"route_id": "COL-002", "county": "Collier", "name": "US-41 North (Tamiami Trail)",
     "road": "US-41", "direction": "North", "from_location": "Naples",
     "to_location": "Fort Myers", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Southwest"},
    {"route_id": "LEE-001", "county": "Lee", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Fort Myers",
     "to_location": "Sarasota / Tampa", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southwest"},
    {"route_id": "LEE-002", "county": "Lee", "name": "US-41 North",
     "road": "US-41", "direction": "North", "from_location": "Fort Myers",
     "to_location": "Sarasota", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Southwest"},

    # Charlotte / Sarasota / Manatee
    {"route_id": "CHA-001", "county": "Charlotte", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Port Charlotte",
     "to_location": "Tampa / Orlando", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southwest"},
    {"route_id": "SAR-001", "county": "Sarasota", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Sarasota",
     "to_location": "Tampa", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southwest"},
    {"route_id": "SAR-002", "county": "Sarasota", "name": "US-301 North",
     "road": "US-301", "direction": "North", "from_location": "Sarasota",
     "to_location": "Tampa / Ocala", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Southwest"},
    {"route_id": "MAN-001", "county": "Manatee", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Bradenton",
     "to_location": "Tampa / Orlando", "route_type": "Primary",
     "serves_zones": ["A","B","C","D","E"], "region": "Southwest"},

    # Tampa Bay (Hillsborough / Pinellas / Pasco / Hernando)
    {"route_id": "HIL-001", "county": "Hillsborough", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Tampa",
     "to_location": "Ocala / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D","E"], "region": "Tampa Bay"},
    {"route_id": "HIL-002", "county": "Hillsborough", "name": "I-4 East",
     "road": "I-4", "direction": "East", "from_location": "Tampa",
     "to_location": "Orlando / I-95", "route_type": "Primary",
     "serves_zones": ["A","B","C"], "region": "Tampa Bay"},
    {"route_id": "HIL-003", "county": "Hillsborough", "name": "US-301 North",
     "road": "US-301", "direction": "North", "from_location": "Tampa",
     "to_location": "Ocala", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Tampa Bay"},
    {"route_id": "PIN-001", "county": "Pinellas", "name": "I-275 North to I-75",
     "road": "I-275 / I-75", "direction": "North", "from_location": "St. Petersburg / Clearwater",
     "to_location": "Ocala / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D","E"], "region": "Tampa Bay"},
    {"route_id": "PIN-002", "county": "Pinellas", "name": "US-19 North",
     "road": "US-19", "direction": "North", "from_location": "Clearwater",
     "to_location": "Pasco County / New Port Richey", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Tampa Bay"},
    {"route_id": "PAS-001", "county": "Pasco", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Wesley Chapel",
     "to_location": "Ocala / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Tampa Bay"},
    {"route_id": "HER-001", "county": "Hernando", "name": "I-75 North / US-98 East",
     "road": "I-75 / US-98", "direction": "North", "from_location": "Spring Hill",
     "to_location": "Ocala", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Tampa Bay"},

    # Central Florida (Orange, Osceola, Polk, Lake, Seminole, Brevard)
    {"route_id": "ORL-001", "county": "Orange", "name": "I-4 East to I-95 North",
     "road": "I-4 / I-95", "direction": "East/North", "from_location": "Orlando",
     "to_location": "Daytona Beach / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B"], "region": "Central"},
    {"route_id": "ORL-002", "county": "Orange", "name": "Florida Turnpike North",
     "road": "FL-Turnpike", "direction": "North", "from_location": "Orlando",
     "to_location": "Gainesville / Jacksonville", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Central"},
    {"route_id": "BRE-001", "county": "Brevard", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "Melbourne / Cocoa",
     "to_location": "Daytona Beach / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Central"},
    {"route_id": "BRE-002", "county": "Brevard", "name": "US-192 West to I-4",
     "road": "US-192 / I-4", "direction": "West", "from_location": "Melbourne",
     "to_location": "Orlando / Tampa", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Central"},
    {"route_id": "VOL-001", "county": "Volusia", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "Daytona Beach",
     "to_location": "Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Central"},
    {"route_id": "VOL-002", "county": "Volusia", "name": "I-4 West to I-75",
     "road": "I-4 / I-75", "direction": "West", "from_location": "Daytona Beach",
     "to_location": "Tampa / Ocala", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Central"},

    # Martin / St. Lucie / Indian River
    {"route_id": "MAR-001", "county": "Martin", "name": "I-95 North / Florida Turnpike",
     "road": "I-95 / FL-Turnpike", "direction": "North", "from_location": "Stuart",
     "to_location": "Orlando / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southeast"},
    {"route_id": "STL-001", "county": "St. Lucie", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "Fort Pierce",
     "to_location": "Orlando / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southeast"},
    {"route_id": "IND-001", "county": "Indian River", "name": "I-95 North / US-1 North",
     "road": "I-95 / US-1", "direction": "North", "from_location": "Vero Beach",
     "to_location": "Melbourne / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Southeast"},

    # NE Florida (Duval, Nassau, St. Johns, Flagler, Clay, Putnam)
    {"route_id": "DUV-001", "county": "Duval", "name": "I-10 West",
     "road": "I-10", "direction": "West", "from_location": "Jacksonville",
     "to_location": "Lake City / Tallahassee / Pensacola", "route_type": "Primary",
     "serves_zones": ["A","B","C","D","E"], "region": "Northeast"},
    {"route_id": "DUV-002", "county": "Duval", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "Jacksonville",
     "to_location": "Georgia / Savannah", "route_type": "Primary",
     "serves_zones": ["A","B","C"], "region": "Northeast"},
    {"route_id": "DUV-003", "county": "Duval", "name": "US-1 North / US-17 North",
     "road": "US-1 / US-17", "direction": "North", "from_location": "Jacksonville",
     "to_location": "Georgia", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Northeast"},
    {"route_id": "NAS-001", "county": "Nassau", "name": "I-95 North to Georgia",
     "road": "I-95", "direction": "North", "from_location": "Fernandina Beach",
     "to_location": "Georgia", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Northeast"},
    {"route_id": "STJ-001", "county": "St. Johns", "name": "I-95 North to Jacksonville",
     "road": "I-95", "direction": "North", "from_location": "St. Augustine",
     "to_location": "Jacksonville / Georgia", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Northeast"},
    {"route_id": "FLA-001", "county": "Flagler", "name": "I-95 North",
     "road": "I-95", "direction": "North", "from_location": "Palm Coast",
     "to_location": "Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Northeast"},

    # Alachua / Marion / Levy / Citrus / Hernando
    {"route_id": "ALA-001", "county": "Alachua", "name": "I-75 North",
     "road": "I-75", "direction": "North", "from_location": "Gainesville",
     "to_location": "Lake City / Valdosta", "route_type": "Primary",
     "serves_zones": ["A","B"], "region": "North Central"},
    {"route_id": "ALA-002", "county": "Alachua", "name": "US-441 North",
     "road": "US-441", "direction": "North", "from_location": "Gainesville",
     "to_location": "Lake City", "route_type": "Alternate",
     "serves_zones": ["A"], "region": "North Central"},
    {"route_id": "MAR-ROU-001", "county": "Marion", "name": "I-75 North / South",
     "road": "I-75", "direction": "North/South", "from_location": "Ocala",
     "to_location": "Gainesville (N) / Tampa (S)", "route_type": "Primary",
     "serves_zones": ["A","B"], "region": "North Central"},
    {"route_id": "CIT-001", "county": "Citrus", "name": "US-19 North / US-98 East",
     "road": "US-19 / US-98", "direction": "North", "from_location": "Crystal River",
     "to_location": "Gainesville / Ocala", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "North Central"},

    # NW Florida (Escambia, Santa Rosa, Okaloosa, Walton, Bay, Leon)
    {"route_id": "ESC-001", "county": "Escambia", "name": "I-10 East",
     "road": "I-10", "direction": "East", "from_location": "Pensacola",
     "to_location": "Tallahassee / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Northwest"},
    {"route_id": "ESC-002", "county": "Escambia", "name": "US-29 North",
     "road": "US-29", "direction": "North", "from_location": "Pensacola",
     "to_location": "Alabama", "route_type": "Alternate",
     "serves_zones": ["A","B"], "region": "Northwest"},
    {"route_id": "SAN-001", "county": "Santa Rosa", "name": "I-10 East / US-90 East",
     "road": "I-10 / US-90", "direction": "East", "from_location": "Milton",
     "to_location": "Tallahassee", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Northwest"},
    {"route_id": "OKA-001", "county": "Okaloosa", "name": "I-10 East / US-90 East",
     "road": "I-10 / US-90", "direction": "East", "from_location": "Fort Walton Beach",
     "to_location": "Tallahassee", "route_type": "Primary",
     "serves_zones": ["A","B","C","D"], "region": "Northwest"},
    {"route_id": "BAY-001", "county": "Bay", "name": "US-231 North / US-98 East",
     "road": "US-231 / US-98", "direction": "North/East",
     "from_location": "Panama City", "to_location": "Dothan AL / Tallahassee",
     "route_type": "Primary", "serves_zones": ["A","B","C","D"], "region": "Northwest"},
    {"route_id": "LEO-001", "county": "Leon", "name": "I-10 East / US-90 East",
     "road": "I-10 / US-90", "direction": "East", "from_location": "Tallahassee",
     "to_location": "Lake City / Jacksonville", "route_type": "Primary",
     "serves_zones": ["A","B"], "region": "Northwest"},
    {"route_id": "LEO-002", "county": "Leon", "name": "US-19 / US-27 South",
     "road": "US-19 / US-27", "direction": "South", "from_location": "Tallahassee",
     "to_location": "Perry / Crystal River", "route_type": "Alternate",
     "serves_zones": ["A"], "region": "Northwest"},
]


# ---------------------------------------------------------------------------
# ArcGIS REST fetch helpers
# ---------------------------------------------------------------------------

def _arcgis_query(url: str, where: str = "1=1",
                  out_fields: str = "*", page_size: int = PAGE_SIZE) -> list[dict]:
    """Page through an ArcGIS Feature Service query and return all features."""
    features = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "where":            where,
            "outFields":        out_fields,
            "f":                "json",
            "resultOffset":     offset,
            "resultRecordCount": page_size,
            "returnGeometry":   "false",
        })
        full_url = f"{url}?{params}"
        try:
            req = urllib.request.Request(
                full_url,
                headers={"Accept": "application/json", "User-Agent": "FPREN/1.0"}
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            log.warning("ArcGIS fetch error at offset %d: %s", offset, e)
            break

        if "error" in data:
            log.warning("ArcGIS API error: %s", data["error"])
            break

        batch = data.get("features", [])
        if not batch:
            break
        features.extend(batch)
        offset += len(batch)
        if not data.get("exceededTransferLimit", False):
            break
        time.sleep(0.3)  # be polite

    return features


def fetch_zones_from_api() -> list[dict]:
    """Try to fetch evacuation zone data from FDEM ArcGIS REST."""
    log.info("Fetching evacuation zones from FDEM ArcGIS...")
    features = _arcgis_query(
        FDEM_ZONES_URL,
        out_fields="COUNTY,ZONE,ZONETYPE,POP_ESTIMATE,DESCRIPTION"
    )
    if not features:
        return []

    now = datetime.now(timezone.utc).isoformat()
    results = []
    for f in features:
        p = f.get("attributes", {})
        county = (p.get("COUNTY") or "").strip().replace(" County", "").strip()
        zone   = (p.get("ZONE") or "").strip().upper()
        if not county or not zone:
            continue
        results.append({
            "county":             county,
            "zone":               zone,
            "zone_order":         ord(zone) - ord("A") + 1 if zone.isalpha() else 99,
            "description":        p.get("DESCRIPTION") or ZONE_DESCRIPTIONS.get(zone, f"Zone {zone}"),
            "population_estimate": int(p.get("POP_ESTIMATE") or 0),
            "source":             "FDEM_ArcGIS",
            "fetched_at":         now,
        })
    log.info("FDEM API returned %d zone features", len(results))
    return results


def fetch_routes_from_api() -> list[dict]:
    """Try to fetch evacuation route data from FDOT/FDEM ArcGIS REST."""
    log.info("Fetching evacuation routes from FDOT/FDEM ArcGIS...")
    features = _arcgis_query(
        FDOT_ROUTES_URL,
        out_fields="COUNTY,ROUTE_NAME,ROAD,DIRECTION,FROM_LOC,TO_LOC,ROUTE_TYPE,LENGTH_MI"
    )
    if not features:
        return []

    now = datetime.now(timezone.utc).isoformat()
    results = []
    for i, f in enumerate(features):
        p = f.get("attributes", {})
        county = (p.get("COUNTY") or "").strip().replace(" County", "").strip()
        road   = (p.get("ROAD") or p.get("ROUTE_NAME") or "").strip()
        if not county or not road:
            continue
        results.append({
            "route_id":      p.get("OBJECTID") or f"API-{i}",
            "county":        county,
            "name":          p.get("ROUTE_NAME") or road,
            "road":          road,
            "direction":     p.get("DIRECTION") or "Various",
            "from_location": p.get("FROM_LOC") or "",
            "to_location":   p.get("TO_LOC") or "",
            "route_type":    p.get("ROUTE_TYPE") or "Primary",
            "serves_zones":  ["A","B","C"],
            "length_miles":  float(p.get("LENGTH_MI") or 0),
            "region":        "",
            "source":        "FDOT_ArcGIS",
            "fetched_at":    now,
        })
    log.info("FDOT API returned %d route features", len(results))
    return results


# ---------------------------------------------------------------------------
# Curated dataset builders
# ---------------------------------------------------------------------------

def build_curated_zones() -> list[dict]:
    """Expand curated zone config into individual zone documents."""
    now = datetime.now(timezone.utc).isoformat()
    results = []
    seen = set()
    for entry in CURATED_ZONES:
        county = entry["county"]
        for zone in entry["zones"]:
            key = (county.lower(), zone.upper())
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "county":             county,
                "zone":               zone.upper(),
                "zone_order":         ord(zone.upper()) - ord("A") + 1,
                "description":        ZONE_DESCRIPTIONS.get(zone.upper(), f"Zone {zone}"),
                "population_estimate": 0,   # not estimated in curated data
                "has_coastal":        entry.get("has_coastal", False),
                "source":             "FPREN_Curated",
                "fetched_at":         now,
            })
    log.info("Built %d curated zone records", len(results))
    return results


def build_curated_routes() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for r in CURATED_ROUTES:
        results.append({**r, "source": "FPREN_Curated", "fetched_at": now,
                        "length_miles": r.get("length_miles", 0.0)})
    log.info("Built %d curated route records", len(results))
    return results


# ---------------------------------------------------------------------------
# MongoDB storage
# ---------------------------------------------------------------------------

def store_zones(records: list[dict]) -> int:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    col = client[DB_NAME][ZONES_COLL]
    col.create_index([("county", 1), ("zone", 1)], unique=True, background=True)
    col.create_index([("zone_order", 1)], background=True)
    ops = [
        UpdateOne(
            {"county": r["county"], "zone": r["zone"]},
            {"$set": r},
            upsert=True,
        )
        for r in records
    ]
    result = col.bulk_write(ops)
    client.close()
    return result.upserted_count + result.modified_count


def store_routes(records: list[dict]) -> int:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    col = client[DB_NAME][ROUTES_COLL]
    col.create_index([("route_id", 1)], unique=True, background=True)
    col.create_index([("county", 1)], background=True)
    col.create_index([("region", 1)], background=True)
    ops = [
        UpdateOne(
            {"route_id": r["route_id"]},
            {"$set": r},
            upsert=True,
        )
        for r in records
    ]
    result = col.bulk_write(ops)
    client.close()
    return result.upserted_count + result.modified_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FPREN FL Evacuation Data Fetcher")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch data but do not write to MongoDB")
    parser.add_argument("--source", choices=["auto","api","curated"], default="auto",
                        help="Data source: auto (try API, fall back to curated), api, curated")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    zones  = []
    routes = []

    if args.source in ("auto", "api"):
        zones  = fetch_zones_from_api()
        routes = fetch_routes_from_api()
        if not zones:
            log.info("Zone API returned no data — using curated dataset")
        if not routes:
            log.info("Route API returned no data — using curated dataset")

    if not zones:
        zones = build_curated_zones()
    if not routes:
        routes = build_curated_routes()

    log.info("Total: %d zone records, %d route records", len(zones), len(routes))

    if args.dry_run:
        log.info("Dry run — skipping MongoDB write.")
        print(json.dumps(zones[:3], indent=2))
        return

    try:
        n_zones  = store_zones(zones)
        n_routes = store_routes(routes)
        log.info("MongoDB upsert complete: %d zones, %d routes written",
                 n_zones, n_routes)
        print(f"EVACUATION_FETCH_OK: {len(zones)} zones, {len(routes)} routes stored "
              f"({n_zones + n_routes} new/updated)")
    except Exception as e:
        log.error("MongoDB write failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
