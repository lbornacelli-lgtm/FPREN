#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import time
import urllib.request
import urllib.error
import base64
import json
import xml.etree.ElementTree as ET
from pymongo import MongoClient
from datetime import datetime, timezone

# -------------------------------
# CONFIGURATION
# -------------------------------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather_rss"
SERVICE_NAME = "weather-rss.service"
REFRESH_INTERVAL = 5  # seconds

# -------------------------------
# ICECAST CONFIGURATION
# -------------------------------
ICECAST_HOST       = "localhost"
ICECAST_PORT       = 8000
ICECAST_MOUNT      = "/beacon"
ICECAST_ADMIN_USER = "admin"
ICECAST_ADMIN_PASS = "1002LBorn1!"

# -------------------------------
# MONGODB CONNECTION
# -------------------------------
mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
rss_status_col = db.feed_status
nws_alerts_col = db.nws_alerts

# -------------------------------
# GUI APPLICATION
# -------------------------------
class SystemdMonitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Weather RSS Service Monitor")
        self.geometry("900x600")

        self.status_var = tk.StringVar(value="Unknown")

        self.create_widgets()
        self.refresh_status()
        self.auto_refresh()

    def create_widgets(self):
        # Header
        header = ttk.Label(self, text="Weather RSS & NWS Alerts Monitor",
                           font=("Arial", 16, "bold"))
        header.pack(pady=10)

        # Service Status
        status_frame = ttk.Frame(self)
        status_frame.pack(pady=5)
        ttk.Label(status_frame, text="Service Status: ").pack(side="left")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var)
        self.status_label.pack(side="left")

        # Tabs
        tab_control = ttk.Notebook(self)
        self.tab_rss = ttk.Frame(tab_control)
        self.tab_nws = ttk.Frame(tab_control)
        self.tab_icecast = ttk.Frame(tab_control)
        tab_control.add(self.tab_rss, text="RSS Feeds")
        tab_control.add(self.tab_nws, text="NWS Alerts")
        tab_control.add(self.tab_icecast, text="Icecast Listeners")
        tab_control.pack(expand=1, fill="both")

        # RSS Treeview
        self.rss_tree = ttk.Treeview(self.tab_rss, columns=("filename", "status", "last_fetch", "size_kb"), show="headings")
        for col in self.rss_tree["columns"]:
            self.rss_tree.heading(col, text=col.title())
        self.rss_tree.pack(expand=1, fill="both")

        # NWS Treeview
        self.nws_tree = ttk.Treeview(self.tab_nws, columns=("event", "severity", "area", "headline", "fetched_at"), show="headings")
        for col in self.nws_tree["columns"]:
            self.nws_tree.heading(col, text=col.replace("_", " ").title())
        self.nws_tree.pack(expand=1, fill="both")

        # Icecast Listeners tab
        self.icecast_summary_var = tk.StringVar(value="Mount: /beacon  |  Listeners: —  |  Stream started: —")
        ttk.Label(self.tab_icecast, textvariable=self.icecast_summary_var,
                  font=("Arial", 11)).pack(pady=8)
        self.icecast_tree = ttk.Treeview(
            self.tab_icecast,
            columns=("ip", "connected", "user_agent"),
            show="headings",
        )
        self.icecast_tree.heading("ip",         text="IP Address")
        self.icecast_tree.heading("connected",  text="Connected")
        self.icecast_tree.heading("user_agent", text="User Agent")
        self.icecast_tree.column("ip",         width=160, anchor="w")
        self.icecast_tree.column("connected",  width=120, anchor="center")
        self.icecast_tree.column("user_agent", width=500, anchor="w")
        self.icecast_tree.pack(expand=1, fill="both")

    def refresh_status(self):
        # Update systemd service status
        try:
            result = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                                    capture_output=True, text=True)
            self.status_var.set(result.stdout.strip())
        except Exception as e:
            self.status_var.set(f"Error: {e}")

        # Update RSS feed table
        for row in self.rss_tree.get_children():
            self.rss_tree.delete(row)
        for doc in rss_status_col.find():
            last_fetch = doc.get("last_fetch")
            if last_fetch:
                last_fetch = last_fetch.strftime("%Y-%m-%d %H:%M:%S UTC")
            self.rss_tree.insert("", "end", values=(
                doc.get("filename"),
                doc.get("status"),
                last_fetch,
                round(doc.get("file_size_bytes", 0)/1024, 2)
            ))

        # Update NWS alerts table
        for row in self.nws_tree.get_children():
            self.nws_tree.delete(row)
        for alert in nws_alerts_col.find().sort("fetched_at", -1):
            fetched_at = alert.get("fetched_at")
            if fetched_at:
                fetched_at = fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            self.nws_tree.insert("", "end", values=(
                alert.get("event"),
                alert.get("severity"),
                alert.get("area_desc"),
                alert.get("headline"),
                fetched_at
            ))

        # Update Icecast listeners table
        self._refresh_icecast()

    def _refresh_icecast(self):
        """Fetch listener data from the Icecast admin API and update the tab."""
        url = (
            f"http://{ICECAST_HOST}:{ICECAST_PORT}"
            f"/admin/listclients?mount={ICECAST_MOUNT}"
        )
        credentials = base64.b64encode(
            f"{ICECAST_ADMIN_USER}:{ICECAST_ADMIN_PASS}".encode()
        ).decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {credentials}"})

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                xml_data = resp.read()
        except Exception as exc:
            self.icecast_summary_var.set(f"Mount: {ICECAST_MOUNT}  |  Icecast unreachable: {exc}")
            for row in self.icecast_tree.get_children():
                self.icecast_tree.delete(row)
            return

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as exc:
            self.icecast_summary_var.set(f"Mount: {ICECAST_MOUNT}  |  XML parse error: {exc}")
            return

        source = root.find("source")
        if source is None:
            self.icecast_summary_var.set(
                f"Mount: {ICECAST_MOUNT}  |  Listeners: 0  |  Stream: offline"
            )
            for row in self.icecast_tree.get_children():
                self.icecast_tree.delete(row)
            return

        listener_count = source.findtext("Listeners", "0")
        stream_start   = source.findtext("StreamStart", "—")
        self.icecast_summary_var.set(
            f"Mount: {ICECAST_MOUNT}  |  Listeners: {listener_count}  |  Stream started: {stream_start}"
        )

        for row in self.icecast_tree.get_children():
            self.icecast_tree.delete(row)

        for listener in source.findall("listener"):
            ip        = listener.findtext("IP", "—")
            connected = listener.findtext("Connected", "—")
            agent     = listener.findtext("UserAgent", "—")
            # Format connected seconds as h:mm:ss
            try:
                secs = int(connected)
                connected = f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
            except (ValueError, TypeError):
                pass
            self.icecast_tree.insert("", "end", values=(ip, connected, agent))

    def auto_refresh(self):
        self.refresh_status()
        self.after(REFRESH_INTERVAL * 1000, self.auto_refresh)

# -------------------------------
# RUN APPLICATION
# -------------------------------
if __name__ == "__main__":
    app = SystemdMonitor()
    app.mainloop()
