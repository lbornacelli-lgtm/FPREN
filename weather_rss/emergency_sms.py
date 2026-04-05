#!/usr/bin/env python3
"""
FPREN Emergency SMS blast helper.

Usage:
  python3 emergency_sms.py \\
      --phones "+13525551234,+13525559999" \\
      --role "Broadcast Engineer" \\
      --phase before \\
      --mongo-uri mongodb://localhost:27017/

Prints "OK: sent to N numbers" on success or "ERROR: <msg>" on failure.
Called from Shiny via system2().
"""

import sys
import json
import argparse
import textwrap
import urllib.request
import urllib.parse
import urllib.error
import datetime

try:
    from pymongo import MongoClient
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False

TWILIO_CFG  = "/home/ufuser/Fpren-main/stream_notify_config.json"
MONGO_DB    = "weather_rss"
COLLECTION  = "emergency_roles_config"
MAX_SMS_LEN = 1550   # leave headroom for multi-part


def _load_twilio():
    try:
        with open(TWILIO_CFG) as f:
            cfg = json.load(f)
        sid   = cfg.get("twilio_sid", "").strip()
        token = cfg.get("twilio_token", "").strip()
        frm   = cfg.get("twilio_from", "").strip()
        if not all([sid, token, frm]):
            raise ValueError("Twilio credentials incomplete in stream_notify_config.json")
        return sid, token, frm
    except Exception as e:
        raise RuntimeError(f"Twilio config error: {e}")


def _get_todos(role, phase, mongo_uri):
    if not HAS_MONGO:
        return []
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
        db = client[MONGO_DB]
        doc = db[COLLECTION].find_one({"role": role, "phase": phase})
        client.close()
        if doc and "todos" in doc:
            return list(doc["todos"])
        return []
    except Exception as e:
        sys.stderr.write(f"emergency_sms: MongoDB error: {e}\n")
        return []


def _format_sms(role, phase, todos):
    phase_label = {"before": "BEFORE EVENT", "during": "DURING EVENT",
                   "after": "AFTER EVENT"}.get(phase, phase.upper())
    lines = [f"FPREN EMERGENCY — {phase_label}", f"Actions for {role}:"]
    if todos:
        for i, item in enumerate(todos, start=1):
            lines.append(f"{i}. {item}")
    else:
        lines.append("(No checklist configured for this role and phase.)")
    lines.append("Reply STOP to opt out. —FPREN")
    body = "\n".join(lines)

    # Split into chunks if over limit
    if len(body) <= MAX_SMS_LEN:
        return [body]

    # Split at item 8 boundary
    header = f"FPREN EMERGENCY — {phase_label}\nActions for {role} (pt %d/%d):\n"
    chunks = []
    items_per_chunk = 7
    for start in range(0, max(len(todos), 1), items_per_chunk):
        chunk_todos = todos[start:start + items_per_chunk]
        idx = start // items_per_chunk + 1
        total = (len(todos) + items_per_chunk - 1) // items_per_chunk
        chunk = header % (idx, total)
        for j, item in enumerate(chunk_todos, start=start + 1):
            chunk += f"{j}. {item}\n"
        chunk += "—FPREN"
        chunks.append(chunk)
    return chunks if chunks else [body]


def _send_one(sid, token, frm, to, body):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({
        "From": frm,
        "To":   to,
        "Body": body,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    import base64
    creds = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.getcode()
            return status in (200, 201)
    except urllib.error.HTTPError as e:
        body_err = e.read().decode(errors="replace")
        sys.stderr.write(f"emergency_sms: Twilio HTTP {e.code} for {to}: {body_err}\n")
        return False
    except Exception as e:
        sys.stderr.write(f"emergency_sms: send error for {to}: {e}\n")
        return False


def main():
    parser = argparse.ArgumentParser(description="FPREN Emergency SMS blast")
    parser.add_argument("--phones",    required=True, help="Comma-separated E.164 phone numbers")
    parser.add_argument("--role",      required=True, help="Profession/role name")
    parser.add_argument("--phase",     required=True, choices=["before","during","after"])
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/",
                        dest="mongo_uri")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Print message preview without sending")
    args = parser.parse_args()

    phones = [p.strip() for p in args.phones.split(",") if p.strip()]
    if not phones:
        print("ERROR: No phone numbers provided")
        sys.exit(1)

    todos    = _get_todos(args.role, args.phase, args.mongo_uri)
    messages = _format_sms(args.role, args.phase, todos)

    if args.dry_run:
        print(f"=== DRY RUN — {len(phones)} recipient(s) ===")
        for msg in messages:
            print(f"--- SMS ({len(msg)} chars) ---")
            print(msg)
            print()
        sys.exit(0)

    try:
        sid, token, frm = _load_twilio()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    sent  = 0
    fails = 0
    for phone in phones:
        for msg in messages:
            ok = _send_one(sid, token, frm, phone, msg)
            if ok:
                sent += 1
            else:
                fails += 1

    if fails == 0:
        print(f"OK: sent {sent} message(s) to {len(phones)} recipient(s)")
    else:
        print(f"PARTIAL: {sent} sent, {fails} failed")
        sys.exit(2)


if __name__ == "__main__":
    main()
