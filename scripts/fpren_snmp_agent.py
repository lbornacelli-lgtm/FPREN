#!/home/ufuser/Fpren-main/venv/bin/python3
"""
FPREN SNMP pass_persist agent.
Exposes FPREN service health under private enterprise OID 1.3.6.1.4.1.64533.
Run by snmpd via:  pass_persist .1.3.6.1.4.1.64533  <this script>

OID Tree:
  .1.3.6.1.4.1.64533.1      fprenSystem (scalars)
    .1.1.0   systemHealth        STRING  OK | DEGRADED | CRITICAL
    .1.2.0   activeAlertCount    INTEGER
    .1.3.0   extremeAlertCount   INTEGER
    .1.4.0   severeAlertCount    INTEGER
    .1.5.0   worstFlightCat      STRING  VFR | MVFR | IFR | LIFR | UNK
    .1.6.0   icecastListeners    INTEGER
    .1.7.0   mongodbStatus       STRING  UP | DOWN
    .1.8.0   totalUserAssets     INTEGER
    .1.9.0   lastCacheUpdate     STRING  ISO8601
    .1.10.0  activeServiceCount  INTEGER
  .1.3.6.1.4.1.64533.2.1    fprenServiceTable (N=1..11)
    .2.1.1.N  serviceIndex      INTEGER
    .2.1.2.N  serviceName       STRING
    .2.1.3.N  serviceStatus     STRING  active | inactive | failed | unknown
    .2.1.4.N  serviceActiveState STRING  raw systemctl value
  .1.3.6.1.4.1.64533.3      fprenAlerts
    .3.1.0   alertCount         INTEGER (same as .1.2.0 — for trap convenience)
    .3.2.0   extremeCount       INTEGER
    .3.3.0   severeCount        INTEGER
"""

import sys
import threading
import subprocess
import json
import datetime
import traceback

try:
    from pymongo import MongoClient
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False

try:
    import urllib.request
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

# ── Constants ────────────────────────────────────────────────────────────────
BASE_OID   = "1.3.6.1.4.1.64533"
MONGO_URI  = "mongodb://localhost:27017/"
MONGO_DB   = "weather_rss"
CACHE_SEC  = 30

SERVICES = [
    "beacon-web-dashboard",
    "beacon-station-engine",
    "beacon-ipaws-fetcher",
    "beacon-obs-fetcher",
    "beacon-extended-fetcher",
    "beacon-mongo-tts",
    "beacon-rivers-fetcher",
    "beacon-rivers-agent",
    "zone-alert-tts",
    "fpren-broadcast-generator",
    "fpren-multi-zone-streamer",
]

# ── Cache ────────────────────────────────────────────────────────────────────
_cache = {
    "system_health":       "UNKNOWN",
    "active_alert_count":  0,
    "extreme_alert_count": 0,
    "severe_alert_count":  0,
    "worst_flight_cat":    "UNK",
    "icecast_listeners":   0,
    "mongodb_status":      "DOWN",
    "total_user_assets":   0,
    "last_cache_update":   "",
    "active_service_count":0,
    "services":            [],
}
_cache_lock = threading.Lock()


def _check_service(name):
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3
        )
        state = r.stdout.strip()
        return state if state else "unknown"
    except Exception:
        return "unknown"


def _get_icecast_listeners():
    if not HAS_URLLIB:
        return 0
    try:
        url = "http://localhost:8000/status-json.xsl"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode())
        sources = data.get("icestats", {}).get("source", [])
        if isinstance(sources, dict):
            sources = [sources]
        return sum(int(s.get("listeners", 0)) for s in sources)
    except Exception:
        return 0


def _refresh_cache():
    global _cache
    new = dict(_cache)  # start from current values

    # ── MongoDB queries ──────────────────────────────────────────────────────
    mongo_ok = False
    if HAS_MONGO:
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            db = client[MONGO_DB]

            # Active alerts
            total  = db.nws_alerts.count_documents({})
            extreme = db.nws_alerts.count_documents({"severity": "Extreme"})
            severe  = db.nws_alerts.count_documents({"severity": "Severe"})
            new["active_alert_count"]  = total
            new["extreme_alert_count"] = extreme
            new["severe_alert_count"]  = severe

            # Worst flight category
            cat_order = {"LIFR": 3, "IFR": 2, "MVFR": 1, "VFR": 0}
            worst = "UNK"
            worst_score = -1
            for doc in db.airport_metar.find({}, {"fltCat": 1, "_id": 0}):
                cat = (doc.get("fltCat") or "").upper().strip()
                score = cat_order.get(cat, -1)
                if score > worst_score:
                    worst_score = score
                    worst = cat if cat else "UNK"
            new["worst_flight_cat"] = worst if worst else "UNK"

            # Total user assets
            total_assets = 0
            for u in db.users.find({}, {"assets": 1, "_id": 0}):
                total_assets += len(u.get("assets") or [])
            new["total_user_assets"] = total_assets

            client.close()
            mongo_ok = True
        except Exception as e:
            sys.stderr.write(f"FPREN SNMP: MongoDB error: {e}\n")

    new["mongodb_status"] = "UP" if mongo_ok else "DOWN"

    # ── Service checks ───────────────────────────────────────────────────────
    svc_rows = []
    active_count = 0
    for i, svc in enumerate(SERVICES, start=1):
        state = _check_service(svc)
        if state == "active":
            active_count += 1
        svc_rows.append({
            "index":        i,
            "name":         svc,
            "status":       state,
            "active_state": state,
            "oid":          f"{BASE_OID}.2.1.3.{i}",
        })
    new["services"]             = svc_rows
    new["active_service_count"] = active_count

    # ── Icecast listeners ────────────────────────────────────────────────────
    new["icecast_listeners"] = _get_icecast_listeners()

    # ── System health roll-up ────────────────────────────────────────────────
    if not mongo_ok or active_count < 6:
        new["system_health"] = "CRITICAL"
    elif active_count < 10 or new["extreme_alert_count"] > 0:
        new["system_health"] = "DEGRADED"
    else:
        new["system_health"] = "OK"

    new["last_cache_update"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Write snapshot to MongoDB ────────────────────────────────────────────
    if HAS_MONGO:
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
            db = client[MONGO_DB]
            db.fpren_snmp_status.update_one(
                {"_id": "singleton"},
                {"$set": {
                    "system_health":        new["system_health"],
                    "active_alert_count":   new["active_alert_count"],
                    "extreme_alert_count":  new["extreme_alert_count"],
                    "severe_alert_count":   new["severe_alert_count"],
                    "worst_flight_cat":     new["worst_flight_cat"],
                    "icecast_listeners":    new["icecast_listeners"],
                    "mongodb_status":       new["mongodb_status"],
                    "total_user_assets":    new["total_user_assets"],
                    "last_cache_update":    new["last_cache_update"],
                    "active_service_count": new["active_service_count"],
                    "services":             [
                        {"name": s["name"], "oid": s["oid"], "status": s["status"]}
                        for s in svc_rows
                    ],
                    "asset_oid_map": _build_asset_oid_map(),
                }},
                upsert=True
            )
            client.close()
        except Exception as e:
            sys.stderr.write(f"FPREN SNMP: cache write error: {e}\n")

    with _cache_lock:
        _cache = new

    # Schedule next refresh
    t = threading.Timer(CACHE_SEC, _refresh_cache)
    t.daemon = True
    t.start()


def _build_asset_oid_map():
    """Return list of {username, asset_name, oid} for Shiny asset OID table."""
    rows = []
    if not HAS_MONGO:
        return rows
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        db = client[MONGO_DB]
        user_idx = 0
        for u in db.users.find({}, {"username": 1, "assets": 1, "_id": 0}).sort("username", 1):
            user_idx += 1
            for asset_idx, asset in enumerate(u.get("assets") or [], start=1):
                oid = f"{BASE_OID}.4.{user_idx}.{asset_idx}"
                rows.append({
                    "username":   u.get("username", ""),
                    "asset_name": asset.get("asset_name", ""),
                    "asset_type": asset.get("asset_type", ""),
                    "oid":        oid,
                })
        client.close()
    except Exception:
        pass
    return rows


# ── OID tree builder ─────────────────────────────────────────────────────────
def _oid_to_tuple(oid_str):
    return tuple(int(x) for x in oid_str.split(".") if x)


def _build_oid_tree():
    with _cache_lock:
        c = dict(_cache)

    tree = []

    def add(oid, typ, val):
        tree.append((oid, typ, str(val)))

    # System scalars
    add(f"{BASE_OID}.1.1.0",  "STRING",  c["system_health"])
    add(f"{BASE_OID}.1.2.0",  "INTEGER", c["active_alert_count"])
    add(f"{BASE_OID}.1.3.0",  "INTEGER", c["extreme_alert_count"])
    add(f"{BASE_OID}.1.4.0",  "INTEGER", c["severe_alert_count"])
    add(f"{BASE_OID}.1.5.0",  "STRING",  c["worst_flight_cat"])
    add(f"{BASE_OID}.1.6.0",  "INTEGER", c["icecast_listeners"])
    add(f"{BASE_OID}.1.7.0",  "STRING",  c["mongodb_status"])
    add(f"{BASE_OID}.1.8.0",  "INTEGER", c["total_user_assets"])
    add(f"{BASE_OID}.1.9.0",  "STRING",  c["last_cache_update"])
    add(f"{BASE_OID}.1.10.0", "INTEGER", c["active_service_count"])

    # Service table — column-major order: all .2.1.1.N, then .2.1.2.N, etc.
    svcs = c["services"] or []
    for svc in svcs:
        add(f"{BASE_OID}.2.1.1.{svc['index']}", "INTEGER", svc["index"])
    for svc in svcs:
        add(f"{BASE_OID}.2.1.2.{svc['index']}", "STRING", svc["name"])
    for svc in svcs:
        add(f"{BASE_OID}.2.1.3.{svc['index']}", "STRING", svc["status"])
    for svc in svcs:
        add(f"{BASE_OID}.2.1.4.{svc['index']}", "STRING", svc["active_state"])

    # Alert summary (for trap use)
    add(f"{BASE_OID}.3.1.0", "INTEGER", c["active_alert_count"])
    add(f"{BASE_OID}.3.2.0", "INTEGER", c["extreme_alert_count"])
    add(f"{BASE_OID}.3.3.0", "INTEGER", c["severe_alert_count"])

    # Sort by OID numeric tuple
    tree.sort(key=lambda x: _oid_to_tuple(x[0]))
    return tree


# ── pass_persist protocol loop ────────────────────────────────────────────────
def _norm(oid):
    """Normalize OID — strip optional leading dot."""
    return oid.lstrip(".")


def _find_exact(tree, oid):
    oid = _norm(oid)
    for entry in tree:
        if entry[0] == oid:
            return entry
    return None


def _find_next(tree, oid):
    oid_t = _oid_to_tuple(_norm(oid))
    for entry in tree:
        if _oid_to_tuple(entry[0]) > oid_t:
            return entry
    return None


def main():
    sys.stderr.write("FPREN SNMP agent started\n")
    # Kick off initial cache load in background so PING responds immediately
    t = threading.Timer(0.1, _refresh_cache)
    t.daemon = True
    t.start()

    # Unbuffered stdout
    out = sys.stdout

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()

            if line == "PING":
                out.write("PONG\n")
                out.flush()

            elif line == "get":
                oid = sys.stdin.readline().strip()
                tree = _build_oid_tree()
                entry = _find_exact(tree, oid)
                if entry:
                    out.write(f"{entry[0]}\n{entry[1]}\n{entry[2]}\n")
                else:
                    out.write("NONE\n")
                out.flush()

            elif line == "getnext":
                oid = sys.stdin.readline().strip()
                tree = _build_oid_tree()
                entry = _find_next(tree, oid)
                if entry:
                    out.write(f"{entry[0]}\n{entry[1]}\n{entry[2]}\n")
                else:
                    out.write("NONE\n")
                out.flush()

            else:
                out.write("NONE\n")
                out.flush()

        except KeyboardInterrupt:
            break
        except Exception as e:
            sys.stderr.write(f"FPREN SNMP: loop error: {e}\n{traceback.format_exc()}\n")
            out.write("NONE\n")
            out.flush()


if __name__ == "__main__":
    main()
