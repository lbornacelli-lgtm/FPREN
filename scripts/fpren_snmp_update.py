#!/home/ufuser/Fpren-main/venv/bin/python3
"""
FPREN SNMP Status Updater — runs once, writes to MongoDB fpren_snmp_status.
Invoked by a systemd timer every 60 seconds.
snmpd uses `extend` directives to read individual values from MongoDB.
"""
import sys, subprocess, json, datetime

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

MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB  = "weather_rss"

SERVICES = [
    "beacon-web-dashboard", "beacon-station-engine", "beacon-ipaws-fetcher",
    "beacon-obs-fetcher", "beacon-extended-fetcher", "beacon-mongo-tts",
    "beacon-rivers-fetcher", "beacon-rivers-agent", "zone-alert-tts",
    "fpren-broadcast-generator", "fpren-multi-zone-streamer",
]

BASE_OID = "1.3.6.1.4.1.64533"

def check_service(name):
    try:
        r = subprocess.run(["systemctl","is-active",name], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"

def get_icecast():
    if not HAS_URLLIB: return 0
    try:
        with urllib.request.urlopen("http://localhost:8000/status-json.xsl", timeout=3) as r:
            d = json.loads(r.read())
        srcs = d.get("icestats",{}).get("source",[])
        if isinstance(srcs, dict): srcs = [srcs]
        return sum(int(s.get("listeners",0)) for s in srcs)
    except Exception:
        return 0

def main():
    if not HAS_MONGO:
        sys.exit(1)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    db = client[MONGO_DB]

    total  = db.nws_alerts.count_documents({})
    extreme = db.nws_alerts.count_documents({"severity":"Extreme"})
    severe  = db.nws_alerts.count_documents({"severity":"Severe"})

    cat_order = {"LIFR":3,"IFR":2,"MVFR":1,"VFR":0}
    worst, wscore = "UNK", -1
    for doc in db.airport_metar.find({},{"fltCat":1,"_id":0}):
        cat = (doc.get("fltCat") or "").upper().strip()
        s = cat_order.get(cat,-1)
        if s > wscore: wscore, worst = s, cat or "UNK"

    total_assets = sum(len(u.get("assets") or []) for u in db.users.find({},{"assets":1,"_id":0}))

    svc_rows, active = [], 0
    for i, svc in enumerate(SERVICES, 1):
        state = check_service(svc)
        if state == "active": active += 1
        svc_rows.append({"index":i,"name":svc,"status":state,"oid":f"{BASE_OID}.2.1.3.{i}"})

    listeners = get_icecast()

    health = ("CRITICAL" if active < 6 else
              "DEGRADED" if active < 10 or extreme > 0 else "OK")

    asset_oid_map = []
    for ui, u in enumerate(db.users.find({},{"username":1,"assets":1,"_id":0}).sort("username",1), 1):
        for ai, a in enumerate(u.get("assets") or [], 1):
            asset_oid_map.append({
                "username": u.get("username",""),
                "asset_name": a.get("asset_name",""),
                "asset_type": a.get("asset_type",""),
                "oid": f"{BASE_OID}.4.{ui}.{ai}",
            })

    db.fpren_snmp_status.update_one({"_id":"singleton"}, {"$set":{
        "system_health":       health,
        "active_alert_count":  total,
        "extreme_alert_count": extreme,
        "severe_alert_count":  severe,
        "worst_flight_cat":    worst,
        "icecast_listeners":   listeners,
        "mongodb_status":      "UP",
        "total_user_assets":   total_assets,
        "last_cache_update":   datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "active_service_count":active,
        "services":            [{"name":s["name"],"oid":s["oid"],"status":s["status"]} for s in svc_rows],
        "asset_oid_map":       asset_oid_map,
    }}, upsert=True)

    client.close()

if __name__ == "__main__":
    main()
