#!/usr/bin/env python3
"""
scripts/seed_zone_definitions.py
---------------------------------
Seeds the zone_definitions collection in MongoDB with all Florida zones.
Run once:  python3 scripts/seed_zone_definitions.py
Re-running is safe — uses upsert on zone_id.
"""

import os
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "weather_rss"

NORTH_FL_COUNTIES = [
    "alachua", "baker", "bay", "bradford", "calhoun", "clay", "columbia",
    "dixie", "duval", "escambia", "flagler", "franklin", "gadsden",
    "gilchrist", "gulf", "hamilton", "holmes", "jackson", "jefferson",
    "lafayette", "leon", "levy", "liberty", "madison", "nassau", "okaloosa",
    "putnam", "santa rosa", "st. johns", "suwannee", "taylor",
    "union", "wakulla", "walton", "washington",
]

CENTRAL_FL_COUNTIES = [
    "marion", "citrus", "hernando", "pasco", "hillsborough", "pinellas",
    "polk", "osceola", "orange", "seminole", "lake", "sumter",
    "volusia", "st. lucie", "indian river", "okeechobee", "highlands",
    "hardee", "manatee", "sarasota", "charlotte", "desoto",
]

SOUTH_FL_COUNTIES = [
    "palm beach", "broward", "miami-dade", "monroe",
    "collier", "lee", "hendry", "glades",
]

TAMPA_COUNTIES        = ["hillsborough", "pinellas"]
MIAMI_COUNTIES        = ["miami-dade", "broward"]
ORLANDO_COUNTIES      = ["orange", "osceola", "seminole"]
JACKSONVILLE_COUNTIES = ["duval", "clay", "st. johns"]
GAINESVILLE_COUNTIES  = ["alachua"]

ALL_FLORIDA_EVENT_FILTER = [
    "severe thunderstorm warning", "severe thunderstorm watch",
    "hurricane warning", "hurricane watch",
    "tropical storm warning", "tropical storm watch",
    "storm surge warning", "storm surge watch",
    "hurricane local statement", "extreme wind warning",
    "hurricane force wind warning", "hurricane force wind watch",
    "flood warning", "flood watch", "flood advisory",
    "flash flood warning", "flash flood watch", "flash flood emergency",
    "coastal flood warning", "coastal flood watch", "coastal flood advisory",
]

ZONES = [
    {"zone_id": "all_florida",    "display_name": "All Florida",    "catch_all": True,  "event_filter": ALL_FLORIDA_EVENT_FILTER, "counties": [],                   "cleanup": {"max_files": 10, "max_age_hours": 24}},
    {"zone_id": "north_florida",  "display_name": "North Florida",  "catch_all": False, "event_filter": None, "counties": NORTH_FL_COUNTIES,        "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "central_florida","display_name": "Central Florida","catch_all": False, "event_filter": None, "counties": CENTRAL_FL_COUNTIES,      "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "south_florida",  "display_name": "South Florida",  "catch_all": False, "event_filter": None, "counties": SOUTH_FL_COUNTIES,        "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "tampa",          "display_name": "Tampa",          "catch_all": False, "event_filter": None, "counties": TAMPA_COUNTIES,           "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "miami",          "display_name": "Miami",          "catch_all": False, "event_filter": None, "counties": MIAMI_COUNTIES,           "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "orlando",        "display_name": "Orlando",        "catch_all": False, "event_filter": None, "counties": ORLANDO_COUNTIES,         "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "jacksonville",   "display_name": "Jacksonville",   "catch_all": False, "event_filter": None, "counties": JACKSONVILLE_COUNTIES,    "cleanup": {"max_files": None, "max_age_hours": 72}},
    {"zone_id": "gainesville",    "display_name": "Gainesville",    "catch_all": False, "event_filter": None, "counties": GAINESVILLE_COUNTIES,     "cleanup": {"max_files": None, "max_age_hours": 72}},
]

def seed():
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    col    = db["zone_definitions"]
    col.create_index("zone_id", unique=True)
    for zone in ZONES:
        col.update_one({"zone_id": zone["zone_id"]}, {"$set": zone}, upsert=True)
        print(f"  ✓ {zone['zone_id']:20s} — {zone['display_name']}")
    print(f"\nSeeded {len(ZONES)} zones into {DB_NAME}.zone_definitions")
    client.close()

if __name__ == "__main__":
    seed()
