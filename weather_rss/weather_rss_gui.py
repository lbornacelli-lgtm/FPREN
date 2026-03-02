#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import time
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
        tab_control.add(self.tab_rss, text="RSS Feeds")
        tab_control.add(self.tab_nws, text="NWS Alerts")
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

    def auto_refresh(self):
        self.refresh_status()
        self.after(REFRESH_INTERVAL * 1000, self.auto_refresh)

# -------------------------------
# RUN APPLICATION
# -------------------------------
if __name__ == "__main__":
    app = SystemdMonitor()
    app.mainloop()
