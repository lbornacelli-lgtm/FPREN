#!/usr/bin/env python3
"""
FPREN Waze for Cities (Connected Citizens Program) Fetcher
===========================================================
Pulls real-time traffic alerts, jams, and irregularities from the Waze CCP
feed and stores them in MongoDB with proper geospatial indexes so RStudio can
run distance calculations against asset locations.

Collections written:
  waze_alerts          — point incidents (accidents, hazards, closures)
  waze_jams            — polyline jams with speed/delay data

Feed URL format (from Waze CCP partner portal):
  https://www.waze.com/row-partnerhub-api/partners/{partner_id}/waze-feeds/{token}?format=1

Config:  weather_rss/config/waze_config.json
  { "feed_url": "https://www.waze.com/row-partnerhub-api/partners/..." }

  Or set env var: WAZE_FEED_URL

Usage:
    python3 waze_fetcher.py               # fetch + store once
    python3 waze_fetcher.py --dry-run     # fetch only, print counts
    python3 waze_fetcher.py --loop        # run forever (2-min interval)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests
from pymongo import MongoClient, UpdateOne, GEOSPHERE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [waze] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("waze_fetcher")

MONGO_URI   = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME     = "weather_rss"
ALERTS_COLL = "waze_alerts"
JAMS_COLL   = "waze_jams"
POLL_INTERVAL = 120  # seconds — matches Waze's own refresh cadence

CFG_PATH = os.path.join(os.path.dirname(__file__), "config", "waze_config.json")


def load_feed_url() -> str:
    """Load Waze CCP feed URL from config file or env var."""
    env_url = os.environ.get("WAZE_FEED_URL", "").strip()
    if env_url:
        return env_url
    try:
        with open(CFG_PATH) as f:
            cfg = json.load(f)
        url = cfg.get("feed_url", "").strip()
        if url and url != "YOUR_WAZE_FEED_URL":
            return url
    except FileNotFoundError:
        pass
    raise RuntimeError(
        f"Waze feed URL not configured.\n"
        f"Set WAZE_FEED_URL env var, or add it to {CFG_PATH}:\n"
        f'  {{"feed_url": "https://www.waze.com/row-partnerhub-api/partners/NNNNN/waze-feeds/TOKEN?format=1"}}'
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_waze_feed(url: str) -> dict:
    """Pull the Waze CCP JSON feed. Returns parsed dict."""
    try:
        resp = requests.get(url, timeout=30,
                            headers={"Accept": "application/json",
                                     "User-Agent": "FPREN/1.0"})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Waze feed HTTP error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Waze feed request failed: {e}") from e
    except ValueError as e:
        raise RuntimeError(f"Waze feed JSON parse error: {e}") from e


# ---------------------------------------------------------------------------
# Parse + normalise
# ---------------------------------------------------------------------------

def _ts(millis) -> str | None:
    """Convert epoch milliseconds to ISO 8601 UTC string."""
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def parse_alerts(raw_alerts: list) -> list[dict]:
    """
    Normalise Waze alert objects.
    Coordinates stored as GeoJSON Point for MongoDB 2dsphere indexing.
    """
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for a in raw_alerts:
        loc = a.get("location") or {}
        lon = loc.get("x")
        lat = loc.get("y")
        if lon is None or lat is None:
            continue  # skip alerts without coordinates

        results.append({
            "uuid":         a.get("uuid"),
            "source":       "waze",
            "type":         a.get("type"),
            "subtype":      a.get("subtype"),
            "street":       a.get("street"),
            "city":         a.get("city"),
            "country":      a.get("country", "US"),
            "reliability":  a.get("reliability"),
            "confidence":   a.get("confidence"),
            "report_rating": a.get("reportRating"),
            "thumbs_up":    a.get("nThumbsUp", 0),
            "description":  a.get("reportDescription"),
            "pub_millis":   a.get("pubMillis"),
            "pub_time":     _ts(a.get("pubMillis")),
            # GeoJSON Point — enables $near / $geoWithin queries and R sf
            "location": {
                "type":        "Point",
                "coordinates": [float(lon), float(lat)],
            },
            # Flat copies for easy R / pandas access without unpacking GeoJSON
            "lat":  float(lat),
            "lon":  float(lon),
            "fetched_at": now,
        })
    return results


def parse_jams(raw_jams: list) -> list[dict]:
    """
    Normalise Waze jam objects.
    Polyline stored as GeoJSON LineString for $geoIntersects / st_distance in R.
    """
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for j in raw_jams:
        line_pts = j.get("line") or []
        coords = [
            [float(pt["x"]), float(pt["y"])]
            for pt in line_pts
            if "x" in pt and "y" in pt
        ]
        # Centroid of the jam for simple distance lookups
        if coords:
            centroid_lon = sum(c[0] for c in coords) / len(coords)
            centroid_lat = sum(c[1] for c in coords) / len(coords)
        else:
            centroid_lon = centroid_lat = None

        results.append({
            "uuid":         j.get("uuid"),
            "source":       "waze",
            "street":       j.get("street"),
            "city":         j.get("city"),
            "country":      j.get("country", "US"),
            "start_node":   j.get("startNode"),
            "end_node":     j.get("endNode"),
            "road_type":    j.get("roadType"),
            "speed_ms":     j.get("speed"),          # metres/second
            "speed_kmh":    j.get("speedKMH"),
            "delay_sec":    j.get("delay"),          # delay vs free-flow, seconds
            "length_m":     j.get("length"),         # metres
            "level":        j.get("level"),          # 0-5
            "pub_millis":   j.get("pubMillis"),
            "pub_time":     _ts(j.get("pubMillis")),
            # GeoJSON LineString — enables $geoIntersects
            "line": {
                "type":        "LineString",
                "coordinates": coords,
            } if len(coords) >= 2 else None,
            # Centroid for $near queries
            "location": {
                "type":        "Point",
                "coordinates": [centroid_lon, centroid_lat],
            } if centroid_lon is not None else None,
            "lat":  centroid_lat,
            "lon":  centroid_lon,
            "fetched_at": now,
        })
    return results


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _ensure_indexes(col_alerts, col_jams):
    """Create geospatial and dedup indexes (idempotent)."""
    col_alerts.create_index([("uuid", 1)], unique=True, background=True)
    col_alerts.create_index([("location", GEOSPHERE)], background=True)
    col_alerts.create_index([("type", 1)], background=True)
    col_alerts.create_index([("pub_millis", -1)], background=True)
    col_alerts.create_index([("city", 1)], background=True)

    col_jams.create_index([("uuid", 1)], unique=True, background=True)
    col_jams.create_index([("location", GEOSPHERE)], background=True)
    col_jams.create_index([("level", -1)], background=True)
    col_jams.create_index([("pub_millis", -1)], background=True)


def store(alerts: list[dict], jams: list[dict]) -> tuple[int, int]:
    """Upsert alerts and jams. Returns (n_alerts_written, n_jams_written)."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    col_a = client[DB_NAME][ALERTS_COLL]
    col_j = client[DB_NAME][JAMS_COLL]
    _ensure_indexes(col_a, col_j)

    def _upsert(col, records, key="uuid"):
        if not records:
            return 0
        ops = [
            UpdateOne(
                {key: r[key]},
                {"$set": r},
                upsert=True,
            )
            for r in records if r.get(key)
        ]
        if not ops:
            return 0
        result = col.bulk_write(ops, ordered=False)
        return result.upserted_count + result.modified_count

    n_a = _upsert(col_a, alerts)
    n_j = _upsert(col_j, jams)
    client.close()
    return n_a, n_j


# ---------------------------------------------------------------------------
# Summary log
# ---------------------------------------------------------------------------

def _summarise(alerts: list[dict], jams: list[dict]):
    from collections import Counter
    type_counts = Counter(a.get("type") for a in alerts)
    cities      = Counter(a.get("city") for a in alerts if a.get("city"))
    log.info(
        "Alerts: %d total | %s",
        len(alerts),
        " | ".join(f"{k}:{v}" for k, v in type_counts.most_common(5)),
    )
    log.info(
        "Jams: %d total | avg delay %.0f s | top cities: %s",
        len(jams),
        sum(j.get("delay_sec") or 0 for j in jams) / max(len(jams), 1),
        ", ".join(c for c, _ in cities.most_common(5)),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_once(feed_url: str, dry_run: bool = False) -> tuple[int, int]:
    log.info("Fetching Waze feed...")
    raw = fetch_waze_feed(feed_url)

    alerts = parse_alerts(raw.get("alerts") or [])
    jams   = parse_jams(raw.get("jams") or [])

    log.info("Parsed: %d alerts, %d jams from feed", len(alerts), len(jams))
    _summarise(alerts, jams)

    if dry_run:
        log.info("Dry run — skipping MongoDB write")
        return len(alerts), len(jams)

    n_a, n_j = store(alerts, jams)
    log.info("Stored: %d alert records, %d jam records (new/updated)", n_a, n_j)
    return n_a, n_j


def main():
    parser = argparse.ArgumentParser(description="FPREN Waze for Cities Fetcher")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch only, do not write to MongoDB")
    parser.add_argument("--loop", action="store_true",
                        help=f"Run continuously, polling every {POLL_INTERVAL}s")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        feed_url = load_feed_url()
    except RuntimeError as e:
        log.error("%s", e)
        sys.exit(1)

    log.info("Waze feed URL configured (token hidden)")

    if args.loop:
        log.info("Starting continuous poll loop (interval: %ds)", POLL_INTERVAL)
        while True:
            try:
                run_once(feed_url, dry_run=args.dry_run)
            except RuntimeError as e:
                log.error("Fetch failed: %s — will retry in %ds", e, POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
    else:
        try:
            n_a, n_j = run_once(feed_url, dry_run=args.dry_run)
            print(f"WAZE_FETCH_OK: {n_a} alerts, {n_j} jams stored")
        except RuntimeError as e:
            log.error("Fetch failed: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
