#!/usr/bin/env python3
"""
Beacon network interface monitor.

Polls enp0s31f6 (ethernet) and wlp0s20f3 (wireless) every 30 seconds.
Sends an email via local Postfix relay when either interface changes state.
"""
import os
import time
import subprocess
import logging
from datetime import datetime, timezone

LOG_FILE   = "/home/lh_admin/weather_station/logs/network_monitor.log"
RECIPIENT  = "lbornacelli@gmail.com"
INTERFACES = {
    "enp0s31f6": "Ethernet (ETH)",
    "wlp0s20f3": "Wireless (WiFi)",
}
POLL_INTERVAL = 30  # seconds

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [NetworkMonitor] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("NetworkMonitor")


def get_interface_state(iface: str) -> str:
    """Return 'UP' or 'DOWN' for the given interface."""
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            state = f.read().strip().upper()
        # operstate values: up, down, unknown, dormant, etc.
        return "UP" if state == "UP" else "DOWN"
    except FileNotFoundError:
        return "DOWN"


def send_alert(iface: str, label: str, old_state: str, new_state: str):
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = f"Beacon Network Alert — {label} is {new_state}"
    body    = (
        f"Network Interface State Change\n"
        f"{'=' * 40}\n"
        f"Interface : {iface} ({label})\n"
        f"Change    : {old_state} → {new_state}\n"
        f"Time      : {now}\n"
        f"Host      : lighthouseserver\n"
        f"\n"
        f"{'Action required: check network connection.' if new_state == 'DOWN' else 'Interface is back online.'}\n"
        f"\nBeacon Weather Station | Gainesville, FL"
    )
    raw = (
        f"To: {RECIPIENT}\n"
        f"From: lh_admin@lighthouseserver.local\n"
        f"Subject: {subject}\n"
        f"Content-Type: text/plain\n"
        f"\n"
        f"{body}"
    )
    try:
        proc = subprocess.run(
            ["sendmail", RECIPIENT],
            input=raw.encode(),
            capture_output=True,
        )
        if proc.returncode == 0:
            logger.info(f"Alert sent: {iface} {old_state} → {new_state}")
        else:
            logger.error(f"sendmail failed: {proc.stderr.decode()}")
    except Exception as e:
        logger.error(f"Email error: {e}")


def main():
    # Initialise state
    states = {iface: get_interface_state(iface) for iface in INTERFACES}
    logger.info("Network monitor started.")
    for iface, label in INTERFACES.items():
        logger.info(f"  {iface} ({label}): {states[iface]}")

    while True:
        time.sleep(POLL_INTERVAL)
        for iface, label in INTERFACES.items():
            new_state = get_interface_state(iface)
            if new_state != states[iface]:
                logger.warning(f"{iface} ({label}): {states[iface]} → {new_state}")
                states[iface] = new_state


if __name__ == "__main__":
    main()
