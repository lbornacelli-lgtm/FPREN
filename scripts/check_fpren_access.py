#!/home/ufuser/Fpren-main/venv/bin/python3
"""
FPREN Comprehensive Connectivity & Accessibility Checker

Tests all FPREN-related services, external APIs, UF network endpoints,
and registered SNMP devices for reachability from this VM.
Outputs JSON to stdout for consumption by fpren_accessibility_report.Rmd.

Usage:  python3 check_fpren_access.py [--pretty]
"""

import sys
import json
import socket
import datetime
import argparse
import time

try:
    import urllib.request, urllib.error
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

try:
    from pymongo import MongoClient
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False

TIMEOUT = 4   # seconds per TCP probe

# ── Helpers ──────────────────────────────────────────────────────────────────

def tcp_check(host, port, timeout=TIMEOUT):
    """Return dict: status, latency_ms, error."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            lat = round((time.monotonic() - t0) * 1000, 1)
            return {"status": "reachable", "latency_ms": lat, "error": None}
    except ConnectionRefusedError:
        lat = round((time.monotonic() - t0) * 1000, 1)
        return {"status": "refused",
                "latency_ms": lat,
                "error": f"Port {port} actively refused on {host} — host up, service not listening"}
    except socket.timeout:
        return {"status": "timeout",
                "latency_ms": None,
                "error": f"Timed out — {host}:{port} may be firewalled or offline"}
    except OSError as e:
        return {"status": "error", "latency_ms": None, "error": str(e)}


def http_check(url, timeout=TIMEOUT):
    """Return dict: status, http_code, latency_ms, error."""
    if not HAS_URLLIB:
        return {"status": "skip", "http_code": None, "latency_ms": None, "error": "urllib unavailable"}
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FPREN-Check/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            lat  = round((time.monotonic() - t0) * 1000, 1)
            code = r.getcode()
            return {"status": "reachable", "http_code": code, "latency_ms": lat, "error": None}
    except urllib.error.HTTPError as e:
        lat = round((time.monotonic() - t0) * 1000, 1)
        # 4xx/5xx still means the host responded
        reachable = e.code < 500
        return {"status": "reachable" if reachable else "server_error",
                "http_code": e.code, "latency_ms": lat, "error": str(e)}
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "timed out" in reason.lower():
            return {"status": "timeout", "http_code": None, "latency_ms": None,
                    "error": f"Timed out — {url} may be firewalled or offline"}
        return {"status": "unreachable", "http_code": None, "latency_ms": None, "error": reason}
    except Exception as e:
        return {"status": "error", "http_code": None, "latency_ms": None, "error": str(e)}


def row(label, category, proto, host, port, result, note=""):
    return {
        "label":       label,
        "category":    category,
        "protocol":    proto,
        "host":        host,
        "port":        port,
        "status":      result.get("status", "error"),
        "latency_ms":  result.get("latency_ms"),
        "http_code":   result.get("http_code"),
        "error":       result.get("error"),
        "note":        note,
        "checked_at":  datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }


# ── Check groups ─────────────────────────────────────────────────────────────

def check_local_services():
    results = []
    checks = [
        ("Shiny Server",         "local", "TCP", "127.0.0.1",  3838, "FPREN primary dashboard"),
        ("Flask Admin (5000)",   "local", "TCP", "127.0.0.1",  5000, "FPREN Flask admin app"),
        ("Icecast2 (8000)",      "local", "TCP", "127.0.0.1",  8000, "Audio streaming server"),
        ("MongoDB (27017)",      "local", "TCP", "127.0.0.1", 27017, "Primary database"),
        ("RStudio Server (8787)","local", "TCP", "127.0.0.1",  8787, "R development environment"),
        ("SNMP Agent (UDP-161)", "local", "TCP", "127.0.0.1",   161, "SNMP — note: tests TCP not UDP"),
        ("Nginx port 80",        "local", "TCP", "127.0.0.1",    80, "Reverse proxy → Shiny"),
        ("Nginx port 443",       "local", "TCP", "127.0.0.1",   443, "HTTPS (pending UF IT cert)"),
    ]
    for label, cat, proto, host, port, note in checks:
        results.append(row(label, cat, proto, host, port, tcp_check(host, port), note))
    return results


def check_uf_network():
    results = []
    checks = [
        ("UF NTP Server",        "uf_network", "TCP", "128.227.30.254",  123, "UF Stratum-2 time server"),
        ("UF LiteLLM API",       "uf_network", "HTTP","api.ai.it.ufl.edu", 443, "UF AI endpoint (LiteLLM)"),
        ("UF DNS",               "uf_network", "TCP", "128.227.36.36",     53, "UF primary DNS"),
        ("UF DNS secondary",     "uf_network", "TCP", "128.227.100.100",   53, "UF secondary DNS"),
    ]
    for label, cat, proto, host, port, note in checks:
        if proto == "HTTP":
            r = http_check(f"https://{host}", timeout=5)
            results.append(row(label, cat, proto, host, port, r, note))
        else:
            results.append(row(label, cat, proto, host, port, tcp_check(host, port), note))
    return results


def check_external_apis():
    results = []
    api_checks = [
        ("NWS Alerts API",         "external_api", "HTTP",
         "https://api.weather.gov/alerts/active?area=FL&limit=1",
         "api.weather.gov", 443,
         "NWS/IPAWS alert feed — critical for FPREN"),
        ("NWS Points API",         "external_api", "HTTP",
         "https://api.weather.gov/points/29.65,-82.33",
         "api.weather.gov", 443,
         "NWS forecast points (ZIP/city forecasts)"),
        ("FAA ATCSCC API",         "external_api", "HTTP",
         "https://soa.smext.faa.gov/asws/api/airport/status/KGNV",
         "soa.smext.faa.gov", 443,
         "FAA airport delays — BLOCKED by UF IT (known issue)"),
        ("Twilio API",             "external_api", "HTTP",
         "https://api.twilio.com",
         "api.twilio.com", 443,
         "SMS delivery (invite + emergency alerts)"),
        ("ElevenLabs TTS",         "external_api", "HTTP",
         "https://api.elevenlabs.io",
         "api.elevenlabs.io", 443,
         "AI TTS for critical alerts (tornado/hurricane)"),
        ("US Census API",          "external_api", "HTTP",
         "https://api.census.gov/data/2022/acs/acs5?get=NAME&for=county:001&in=state:12",
         "api.census.gov", 443,
         "FL demographic data"),
        ("NHC RSS (Atlantic)",     "external_api", "HTTP",
         "https://www.nhc.noaa.gov/nhc_at1.xml",
         "www.nhc.noaa.gov", 443,
         "National Hurricane Center Atlantic feed"),
        ("Icecast public test",    "external_api", "TCP",
         "128.227.67.234", "128.227.67.234", 8000,
         "Icecast external reachability from self — firewalled by UF IT"),
        ("FPREN port 80 (self)",   "external_api", "TCP",
         "128.227.67.234", "128.227.67.234", 80,
         "Dashboard external port 80 — pending UF IT approval"),
        ("FPREN port 443 (self)",  "external_api", "TCP",
         "128.227.67.234", "128.227.67.234", 443,
         "Dashboard HTTPS external — pending UF IT approval"),
    ]
    for label, cat, proto, url_or_host, host, port, note in api_checks:
        if proto == "HTTP":
            r = http_check(url_or_host, timeout=5)
        else:
            r = tcp_check(host, port)
        results.append(row(label, cat, proto, host, port, r, note))
    return results


def check_snmp_devices():
    """Check all SNMP devices registered in user assets."""
    results = []
    if not HAS_MONGO:
        return results
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=3000)
        db     = client["weather_rss"]
        users  = list(db.users.find({}, {"username": 1, "assets": 1, "_id": 0}))
        client.close()
        for u in users:
            uname  = u.get("username", "?")
            assets = u.get("assets") or []
            if not isinstance(assets, list):
                continue
            for asset in assets:
                devices = asset.get("snmp_devices") or []
                if not isinstance(devices, list):
                    continue
                for dev in devices:
                    ip   = dev.get("ip", "")
                    port = int(dev.get("port") or 161)
                    lbl  = dev.get("label") or f"{ip}:{port}"
                    if not ip:
                        continue
                    r = tcp_check(ip, port)
                    note = f"Asset: {asset.get('asset_name','?')} — User: {uname}"
                    results.append(row(f"SNMP: {lbl}", "snmp_device", "TCP",
                                       ip, port, r, note))
    except Exception as e:
        results.append({"label": "SNMP device query", "category": "snmp_device",
                        "status": "error", "error": str(e)})
    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    all_results = (
        check_local_services() +
        check_uf_network() +
        check_external_apis() +
        check_snmp_devices()
    )

    output = {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total":        len(all_results),
        "reachable":    sum(1 for r in all_results if r["status"] == "reachable"),
        "blocked":      sum(1 for r in all_results if r["status"] in ("timeout", "unreachable")),
        "refused":      sum(1 for r in all_results if r["status"] == "refused"),
        "errors":       sum(1 for r in all_results if r["status"] == "error"),
        "results":      all_results
    }
    indent = 2 if args.pretty else None
    print(json.dumps(output, indent=indent))


if __name__ == "__main__":
    main()
