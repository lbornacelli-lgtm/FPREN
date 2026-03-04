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
rss_status_col      = db.feed_status
nws_alerts_col      = db.nws_alerts
airport_metar_col   = db.airport_metar
fl_traffic_col      = db.fl_traffic
school_closings_col = db.school_closings

# -------------------------------
# GUI APPLICATION
# -------------------------------
class SystemdMonitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Beacon Alerts Monitor")
        self.geometry("1100x650")

        self.status_var = tk.StringVar(value="Unknown")

        self.create_widgets()
        self.refresh_status()
        self.auto_refresh()

    def create_widgets(self):
        # Header
        header = ttk.Label(self, text="Beacon Alerts Monitor",
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
        self.tab_rss          = ttk.Frame(tab_control)
        self.tab_nws          = ttk.Frame(tab_control)
        self.tab_traffic      = ttk.Frame(tab_control)
        self.tab_school       = ttk.Frame(tab_control)
        self.tab_airport      = ttk.Frame(tab_control)
        self.tab_icecast      = ttk.Frame(tab_control)
        tab_control.add(self.tab_rss,     text="RSS Feeds")
        tab_control.add(self.tab_nws,     text="NWS Alerts")
        tab_control.add(self.tab_traffic, text="Traffic")
        tab_control.add(self.tab_school,  text="School Closings")
        tab_control.add(self.tab_airport, text="Airport Weather")
        tab_control.add(self.tab_icecast, text="Icecast Listeners")
        tab_control.pack(expand=1, fill="both")

        # --- RSS Treeview ---
        self.rss_tree = ttk.Treeview(
            self.tab_rss,
            columns=("filename", "status", "last_fetch", "size_kb"),
            show="headings",
        )
        for col in self.rss_tree["columns"]:
            self.rss_tree.heading(col, text=col.title())
        self.rss_tree.pack(expand=1, fill="both")

        # --- NWS Alerts Treeview ---
        self.nws_tree = ttk.Treeview(
            self.tab_nws,
            columns=("event", "severity", "area", "headline", "fetched_at"),
            show="headings",
        )
        for col in self.nws_tree["columns"]:
            self.nws_tree.heading(col, text=col.replace("_", " ").title())
        self.nws_tree.pack(expand=1, fill="both")

        # --- Traffic Treeview ---
        self.traffic_tree = ttk.Treeview(
            self.tab_traffic,
            columns=("type", "road", "location", "county", "severity", "last_updated"),
            show="headings",
        )
        col_widths = {"type": 160, "road": 200, "location": 220, "county": 100, "severity": 80, "last_updated": 140}
        for col in self.traffic_tree["columns"]:
            self.traffic_tree.heading(col, text=col.replace("_", " ").title())
            self.traffic_tree.column(col, width=col_widths.get(col, 120), anchor="w")
        traffic_scroll = ttk.Scrollbar(self.tab_traffic, orient="vertical", command=self.traffic_tree.yview)
        self.traffic_tree.configure(yscrollcommand=traffic_scroll.set)
        self.traffic_tree.pack(side="left", expand=1, fill="both")
        traffic_scroll.pack(side="right", fill="y")

        # --- School Closings Treeview ---
        self.school_tree = ttk.Treeview(
            self.tab_school,
            columns=("title", "closure_type", "published_date", "fetched_at"),
            show="headings",
        )
        school_col_widths = {"title": 400, "closure_type": 120, "published_date": 180, "fetched_at": 180}
        for col in self.school_tree["columns"]:
            self.school_tree.heading(col, text=col.replace("_", " ").title())
            self.school_tree.column(col, width=school_col_widths.get(col, 150), anchor="w")
        self.school_no_data = ttk.Label(self.tab_school, text="No active school closings or delays.",
                                         font=("Arial", 11), foreground="gray")
        self.school_tree.pack(expand=1, fill="both")

        # --- Airport Weather Treeview ---
        self.airport_tree = ttk.Treeview(
            self.tab_airport,
            columns=("icaoId", "name", "fltCat", "temp", "dewp", "wdir", "wspd", "visib", "obsTime"),
            show="headings",
        )
        airport_col_cfg = {
            "icaoId":  ("ICAO",       60),
            "name":    ("Airport",   260),
            "fltCat":  ("Cat",        50),
            "temp":    ("Temp °C",    70),
            "dewp":    ("Dewp °C",    70),
            "wdir":    ("Wdir",       60),
            "wspd":    ("Wspd kt",    65),
            "visib":   ("Vis",        55),
            "obsTime": ("Obs Time",  160),
        }
        for col, (label, width) in airport_col_cfg.items():
            self.airport_tree.heading(col, text=label)
            self.airport_tree.column(col, width=width, anchor="center")
        airport_scroll = ttk.Scrollbar(self.tab_airport, orient="vertical", command=self.airport_tree.yview)
        self.airport_tree.configure(yscrollcommand=airport_scroll.set)
        self.airport_tree.pack(side="left", expand=1, fill="both")
        airport_scroll.pack(side="right", fill="y")

        # --- Icecast Listeners tab ---
        icecast_top = ttk.Frame(self.tab_icecast)
        icecast_top.pack(fill="x", padx=8, pady=6)

        self.icecast_summary_var = tk.StringVar(
            value="Mount: /beacon  |  Listeners: —  |  Stream started: —"
        )
        ttk.Label(icecast_top, textvariable=self.icecast_summary_var,
                  font=("Arial", 11)).pack(side="left")

        restart_btn = ttk.Button(
            icecast_top,
            text="Restart Stream",
            command=self._restart_stream,
        )
        restart_btn.pack(side="right", padx=6)

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

    # ------------------------------------------------------------------
    # REFRESH METHODS
    # ------------------------------------------------------------------
    def refresh_status(self):
        try:
            result = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                                    capture_output=True, text=True)
            self.status_var.set(result.stdout.strip())
        except Exception as e:
            self.status_var.set(f"Error: {e}")

        self._refresh_rss()
        self._refresh_nws()
        self._refresh_traffic()
        self._refresh_school()
        self._refresh_airport()
        self._refresh_icecast()

    def _refresh_rss(self):
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
                round(doc.get("file_size_bytes", 0) / 1024, 2),
            ))

    def _refresh_nws(self):
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
                fetched_at,
            ))

    def _refresh_traffic(self):
        for row in self.traffic_tree.get_children():
            self.traffic_tree.delete(row)
        for inc in fl_traffic_col.find().sort("severity", 1):
            self.traffic_tree.insert("", "end", values=(
                inc.get("type", ""),
                inc.get("road", ""),
                inc.get("location", ""),
                inc.get("county", ""),
                inc.get("severity", ""),
                inc.get("last_updated", ""),
            ))

    def _refresh_school(self):
        for row in self.school_tree.get_children():
            self.school_tree.delete(row)
        docs = list(school_closings_col.find().sort("fetched_at", -1))
        for doc in docs:
            fetched_at = doc.get("fetched_at")
            if fetched_at and hasattr(fetched_at, "strftime"):
                fetched_at = fetched_at.strftime("%Y-%m-%d %H:%M UTC")
            self.school_tree.insert("", "end", values=(
                doc.get("title", ""),
                doc.get("closure_type", ""),
                doc.get("published_date", ""),
                fetched_at,
            ))
        if not docs:
            self.school_no_data.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.school_no_data.place_forget()

    def _refresh_airport(self):
        for row in self.airport_tree.get_children():
            self.airport_tree.delete(row)
        # Color-code by flight category
        flt_colors = {"LIFR": "#ff6666", "IFR": "#ffaa44", "MVFR": "#6699ff", "VFR": "#44bb44"}
        for rec in airport_metar_col.find().sort("icaoId", 1):
            flt_cat = rec.get("fltCat", "")
            obs_time = rec.get("obsTime", "")
            iid = self.airport_tree.insert("", "end", values=(
                rec.get("icaoId", ""),
                rec.get("name", ""),
                flt_cat,
                rec.get("temp", ""),
                rec.get("dewp", ""),
                rec.get("wdir", ""),
                rec.get("wspd", ""),
                rec.get("visib", ""),
                obs_time,
            ))
            color = flt_colors.get(flt_cat)
            if color:
                self.airport_tree.tag_configure(flt_cat, foreground=color)
                self.airport_tree.item(iid, tags=(flt_cat,))

    def _refresh_icecast(self):
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
            self.icecast_summary_var.set(
                f"Mount: {ICECAST_MOUNT}  |  Icecast unreachable: {exc}"
            )
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
            try:
                secs = int(connected)
                connected = f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
            except (ValueError, TypeError):
                pass
            self.icecast_tree.insert("", "end", values=(ip, connected, agent))

    def _restart_stream(self):
        if not messagebox.askyesno("Restart Stream",
                                   "Kill the Icecast source on /beacon?\nThis will briefly disconnect listeners."):
            return
        url = (
            f"http://{ICECAST_HOST}:{ICECAST_PORT}"
            f"/admin/killsource?mount={ICECAST_MOUNT}"
        )
        credentials = base64.b64encode(
            f"{ICECAST_ADMIN_USER}:{ICECAST_ADMIN_PASS}".encode()
        ).decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {credentials}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            messagebox.showinfo("Stream Restarted", "Source killed — DarkIce will reconnect automatically.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not restart stream:\n{e}")

    def auto_refresh(self):
        self.refresh_status()
        self.after(REFRESH_INTERVAL * 1000, self.auto_refresh)

# -------------------------------
# RUN APPLICATION
# -------------------------------
if __name__ == "__main__":
    app = SystemdMonitor()
    app.mainloop()
