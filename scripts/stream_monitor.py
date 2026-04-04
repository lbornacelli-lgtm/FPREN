#!/usr/bin/env python3
"""
stream_monitor.py  —  Continuously monitor Icecast stream on port 8000.

Checks every 60 seconds. On state change (online→offline or offline→online),
calls stream_notify.py to send configured notifications.
"""

import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

import requests

CONFIG_FILE   = os.path.join(os.path.dirname(__file__), "..", "stream_notify_config.json")
STATE_FILE    = "/home/ufuser/Fpren-main/logs/stream_state.txt"
LOG_FILE      = "/home/ufuser/Fpren-main/logs/stream_monitor.log"
NOTIFY_SCRIPT = os.path.join(os.path.dirname(__file__), "stream_notify.py")
CHECK_PORT    = 8000
CHECK_HOST    = "127.0.0.1"
INTERVAL      = 60  # seconds

# Zone mount points — must have a live source connected for the stream to work.
# The /fpren mount is the public All-Florida stream; others are zone-specific.
ICECAST_STATUS_URL = f"http://{CHECK_HOST}:{CHECK_PORT}/status-json.xsl"
EXPECTED_MOUNTS = [
    "/fpren",
    "/north-florida",
    "/central-florida",
    "/south-florida",
    "/tampa",
    "/miami",
    "/orlando",
    "/jacksonville",
    "/gainesville",
]

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def check_stream() -> bool:
    """
    Return True only if Icecast is up AND at least the primary /fpren mount
    has a live source connected.

    Checking just port 8000 tells us Icecast is running, but all 9 zone
    FFmpeg source processes could be dead while port 8000 stays open.
    This checks the actual mount source list from the Icecast status API.
    """
    try:
        resp = requests.get(ICECAST_STATUS_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logging.warning("Icecast status fetch failed: %s", exc)
        # Fall back to bare TCP check so we still catch total outages.
        try:
            with socket.create_connection((CHECK_HOST, CHECK_PORT), timeout=5):
                return False  # Port open but can't read status — treat as degraded
        except (ConnectionRefusedError, OSError, socket.timeout):
            return False

    # Icecast reports active sources under icestats.source (dict or list).
    icestats = data.get("icestats", {})
    sources  = icestats.get("source", [])
    if isinstance(sources, dict):
        sources = [sources]

    active_mounts = {s.get("listenurl", "").split(":8000", 1)[-1] for s in sources}

    # Require at least the primary All-Florida mount to be live.
    primary_up = "/fpren" in active_mounts
    if not primary_up:
        logging.warning(
            "Primary /fpren mount has no live source. Active mounts: %s",
            sorted(active_mounts) or "(none)",
        )
        return False

    # Log any zone mounts that are down (informational — don't fail the check
    # on zone mounts alone, since a zone restart is less critical than total outage).
    dead_zones = [m for m in EXPECTED_MOUNTS if m != "/fpren" and m not in active_mounts]
    if dead_zones:
        logging.warning("Zone mounts with no live source: %s", dead_zones)

    return True


def read_last_state() -> str:
    """Return 'online', 'offline', or 'unknown'."""
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


def write_state(state: str):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(state)


def send_notification(event: str):
    try:
        result = subprocess.run(
            [sys.executable, NOTIFY_SCRIPT, event],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logging.error("Notify script error: %s", result.stderr)
        else:
            logging.info("Notification sent for event: %s", event)
    except Exception as e:
        logging.error("Failed to run notify script: %s", e)


def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    logging.info("Stream monitor started — watching port %d", CHECK_PORT)
    last_state = read_last_state()

    while True:
        try:
            is_up       = check_stream()
            curr_state  = "online" if is_up else "offline"
            cfg         = load_config()

            if last_state != curr_state:
                logging.info("Stream state changed: %s -> %s", last_state, curr_state)
                write_state(curr_state)

                if curr_state == "offline" and cfg.get("notify_on_offline", True):
                    send_notification("offline")
                elif curr_state == "online" and last_state not in ("unknown",):
                    # Only notify restoration if we previously saw it go down
                    send_notification("online")

                last_state = curr_state
            else:
                logging.debug("Stream state unchanged: %s", curr_state)

        except Exception as e:
            logging.error("Monitor loop error: %s", e)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
