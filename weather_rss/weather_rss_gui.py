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
import os
import xml.etree.ElementTree as ET
from pymongo import MongoClient
from datetime import datetime, timezone
from tkinter import scrolledtext as _st
from PIL import Image, ImageTk

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
# SERIAL ALERT CONFIG
# -------------------------------
SERIAL_CFG_FILE = "/home/lh_admin/weather_station/config/serial_config.json"
SMTP_CFG_FILE   = "/home/lh_admin/weather_rss/config/smtp_config.json"

# -------------------------------
# PLAYLIST CONFIG
# -------------------------------
PLAYLISTS_DIR         = "/home/lh_admin/weather_station/playlists"
STREAM_PLAYLISTS_FILE = "/home/lh_admin/weather_station/config/stream_playlists.json"
PLAYLIST_STATE_FILE   = "/tmp/beacon_playlist_state.json"

# -------------------------------
# STREAM ZONE CONFIG
# -------------------------------
ZONE_OVERRIDES_FILE = "/home/lh_admin/weather_station/config/stream_zone_overrides.json"

AVAILABLE_ZONES = [
    "all_florida", "north_florida", "central_florida", "south_florida",
    "miami", "jacksonville", "orlando", "tampa",
]

STREAMS = [
    {"id": "stream_8000", "label": "All Florida",     "port": 8000, "mount": "/beacon",          "default_zone": "all_florida"},
    {"id": "stream_8001", "label": "North Florida",   "port": 8001, "mount": "/north-florida",   "default_zone": "north_florida"},
    {"id": "stream_8002", "label": "Central Florida", "port": 8002, "mount": "/central-florida", "default_zone": "central_florida"},
    {"id": "stream_8003", "label": "South Florida",   "port": 8003, "mount": "/south-florida",   "default_zone": "south_florida"},
    {"id": "stream_8004", "label": "Miami",           "port": 8004, "mount": "/miami",           "default_zone": "miami"},
]

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
        self.title("FPREN Alerts Monitor")
        self.geometry("1100x650")

        self.status_var = tk.StringVar(value="Unknown")
        # Tracks sort state per treeview: tree_id → (col, reverse)
        self._sort_state: dict = {}

        self.create_widgets()
        # Set window icon (taskbar + title bar)
        try:
            _ico = Image.open(self.LOGO_PATH)
            _ico.thumbnail((64, 64), Image.LANCZOS)
            self._app_icon = ImageTk.PhotoImage(_ico)
            self.iconphoto(True, self._app_icon)
        except Exception:
            pass
        self.refresh_status()
        self.auto_refresh()

    LOGO_PATH = "/home/lh_admin/weather_rss/web/static/fpren.png"

    def create_widgets(self):
        # Header bar with logo + title
        header_frame = tk.Frame(self, background="#111111")
        header_frame.pack(fill="x")

        # Logo
        try:
            img = Image.open(self.LOGO_PATH)
            img.thumbnail((180, 60), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(img)
            tk.Label(header_frame, image=self._logo_img,
                     background="#111111").pack(side="left", padx=12, pady=6)
        except Exception:
            pass  # logo missing — still show the text title

        title_frame = tk.Frame(header_frame, background="#111111")
        title_frame.pack(side="left", pady=6)
        tk.Label(title_frame, text="FPREN Alerts Monitor",
                 font=("Arial", 15, "bold"),
                 foreground="#ffffff", background="#111111").pack(anchor="w")
        tk.Label(title_frame, text="Weather  \u2022  Traffic  \u2022  Alerts  \u2022  Icecast",
                 font=("Arial", 8),
                 foreground="#aaaaaa", background="#111111").pack(anchor="w")

        # Service Status
        status_frame = ttk.Frame(self)
        status_frame.pack(pady=5)
        ttk.Label(status_frame, text="Service Status: ").pack(side="left")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var)
        self.status_label.pack(side="left")

        # Tabs
        tab_control = ttk.Notebook(self)
        self.tab_config       = ttk.Frame(tab_control)
        self.tab_rss          = ttk.Frame(tab_control)
        self.tab_nws          = ttk.Frame(tab_control)
        self.tab_traffic      = ttk.Frame(tab_control)
        self.tab_school       = ttk.Frame(tab_control)
        self.tab_airport      = ttk.Frame(tab_control)
        self.tab_icecast      = ttk.Frame(tab_control)
        self.tab_playlist     = ttk.Frame(tab_control)
        self.tab_weather      = ttk.Frame(tab_control)
        tab_control.add(self.tab_config,   text="Config")
        tab_control.add(self.tab_rss,      text="RSS Feeds")
        tab_control.add(self.tab_nws,      text="NWS Alerts")
        tab_control.add(self.tab_traffic,  text="Traffic")
        tab_control.add(self.tab_school,   text="School Closings")
        tab_control.add(self.tab_airport,  text="Airport Weather")
        tab_control.add(self.tab_icecast,  text="Icecast Listeners")
        tab_control.add(self.tab_playlist, text="Playlist")
        tab_control.add(self.tab_weather,  text="Weather")
        self.tab_control = tab_control
        tab_control.pack(expand=1, fill="both")

        self._build_config_tab()
        self._build_playlist_tab()
        self._build_weather_tab()

        # --- RSS Treeview ---
        self.rss_tree = ttk.Treeview(
            self.tab_rss,
            columns=("filename", "status", "last_fetch", "size_kb"),
            show="headings",
        )
        rss_col_labels = {
            "filename":   "Filename",
            "status":     "Status",
            "last_fetch": "Last Fetch ▲▼",
            "size_kb":    "Size KB",
        }
        # date columns that need chronological (not alphabetical) sorting
        rss_date_cols = {"last_fetch"}
        for col in self.rss_tree["columns"]:
            label = rss_col_labels.get(col, col.replace("_", " ").title())
            self.rss_tree.heading(
                col, text=label,
                command=self._make_sort_cmd(self.rss_tree, col, col in rss_date_cols),
            )
        rss_scroll = ttk.Scrollbar(self.tab_rss, orient="vertical", command=self.rss_tree.yview)
        self.rss_tree.configure(yscrollcommand=rss_scroll.set)
        self.rss_tree.pack(side="left", expand=1, fill="both")
        rss_scroll.pack(side="right", fill="y")

        # --- NWS Alerts Treeview ---
        self.nws_tree = ttk.Treeview(
            self.tab_nws,
            columns=("event", "severity", "area", "headline", "fetched_at"),
            show="headings",
        )
        nws_col_labels = {
            "event":      "Event",
            "severity":   "Severity",
            "area":       "Area",
            "headline":   "Headline",
            "fetched_at": "Fetched At ▲▼",
        }
        nws_date_cols = {"fetched_at"}
        for col in self.nws_tree["columns"]:
            label = nws_col_labels.get(col, col.replace("_", " ").title())
            self.nws_tree.heading(
                col, text=label,
                command=self._make_sort_cmd(self.nws_tree, col, col in nws_date_cols),
            )
        nws_scroll = ttk.Scrollbar(self.tab_nws, orient="vertical", command=self.nws_tree.yview)
        self.nws_tree.configure(yscrollcommand=nws_scroll.set)
        self.nws_tree.pack(side="left", expand=1, fill="both")
        nws_scroll.pack(side="right", fill="y")

        # --- Traffic Treeview ---
        self.traffic_tree = ttk.Treeview(
            self.tab_traffic,
            columns=("type", "road", "location", "county", "severity", "last_updated"),
            show="headings",
        )
        traffic_col_labels = {
            "type":         "Type",
            "road":         "Road",
            "location":     "Location",
            "county":       "County",
            "severity":     "Severity",
            "last_updated": "Last Updated ▲▼",
        }
        traffic_date_cols = {"last_updated"}
        col_widths = {"type": 160, "road": 200, "location": 220, "county": 100, "severity": 80, "last_updated": 140}
        for col in self.traffic_tree["columns"]:
            label = traffic_col_labels.get(col, col.replace("_", " ").title())
            self.traffic_tree.heading(
                col, text=label,
                command=self._make_sort_cmd(self.traffic_tree, col, col in traffic_date_cols),
            )
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
            columns=("icaoId", "name", "fltCat", "temp_f", "temp", "dewp_f", "dewp", "wdir", "wspd", "visib", "obsTime"),
            show="headings",
        )
        airport_col_cfg = {
            "icaoId":  ("ICAO",       60),
            "name":    ("Airport",   220),
            "fltCat":  ("Cat",        50),
            "temp_f":  ("Temp °F",    70),
            "temp":    ("Temp °C",    70),
            "dewp_f":  ("Dewp °F",    70),
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

        # Now Playing row
        icecast_np = ttk.Frame(self.tab_icecast)
        icecast_np.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(icecast_np, text="Now Playing:", font=("Arial", 10, "bold")).pack(side="left")
        self.now_playing_var = tk.StringVar(value="—")
        ttk.Label(icecast_np, textvariable=self.now_playing_var,
                  font=("Arial", 10)).pack(side="left", padx=6)

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
    # SORTABLE TREEVIEW HELPERS
    # ------------------------------------------------------------------

    # Date formats written by the refresh methods
    _DATE_FMTS = [
        "%Y-%m-%d %H:%M:%S UTC",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M UTC",
        "%Y/%m/%d %H:%M",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y, %I:%M %p",   # FL511 format: '3/4/26, 6:04 AM'
        "%m/%d/%y %I:%M %p",
    ]

    def _parse_dt(self, val: str):
        """Return a sortable key for a date/time string; unknown → empty string sorts last."""
        v = (val or "").strip()
        for fmt in self._DATE_FMTS:
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                pass
        return datetime.min  # unparseable → treat as oldest

    def _make_sort_cmd(self, tree: ttk.Treeview, col: str, is_date: bool = False):
        """Return a callback that sorts *tree* by *col* and toggles direction."""
        def _cmd():
            self._sort_tree(tree, col, is_date)
        return _cmd

    def _sort_tree(self, tree: ttk.Treeview, col: str, is_date: bool):
        tid = str(id(tree))
        prev_col, prev_rev = self._sort_state.get(tid, (None, False))
        reverse = not prev_rev if col == prev_col else False
        self._sort_state[tid] = (col, reverse)

        col_idx = list(tree["columns"]).index(col)

        # Collect all rows as (sort_key, iid)
        rows = []
        for iid in tree.get_children(""):
            val = tree.item(iid, "values")[col_idx]
            key = self._parse_dt(val) if is_date else (val or "").lower()
            rows.append((key, iid))

        rows.sort(key=lambda x: x[0], reverse=reverse)

        for pos, (_, iid) in enumerate(rows):
            tree.move(iid, "", pos)

        # Update heading arrows
        arrow = " ▼" if reverse else " ▲"
        for c in tree["columns"]:
            current_text = tree.heading(c, "text")
            # Strip any existing arrow
            clean = current_text.rstrip(" ▲▼")
            # Also strip the static ▲▼ hint on the active column label
            clean = clean.replace(" ▲▼", "")
            if c == col:
                tree.heading(c, text=clean + arrow)
            else:
                # Restore ▲▼ hint on date columns
                if is_date and c == col:
                    tree.heading(c, text=clean + " ▲▼")
                else:
                    tree.heading(c, text=clean)

    # ------------------------------------------------------------------
    # PLAYLIST TAB
    # ------------------------------------------------------------------
    def _build_playlist_tab(self):
        self._playlist_combos = {}  # stream_id → ttk.Combobox

        top_pane = ttk.Frame(self.tab_playlist)
        top_pane.pack(fill="both", expand=True, padx=8, pady=6)

        # ---- Left: Available Playlists listbox ----
        left_frame = ttk.LabelFrame(top_pane, text="Available Playlists", padding=6)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))

        self._playlist_listbox = tk.Listbox(left_frame, width=28, selectmode="single",
                                            font=("Courier", 10))
        lb_scroll = ttk.Scrollbar(left_frame, orient="vertical",
                                   command=self._playlist_listbox.yview)
        self._playlist_listbox.configure(yscrollcommand=lb_scroll.set)
        self._playlist_listbox.pack(side="left", fill="both", expand=True)
        lb_scroll.pack(side="right", fill="y")
        self._playlist_listbox.bind("<<ListboxSelect>>", self._on_playlist_select)

        # ---- Right: Playlist Slots treeview ----
        right_frame = ttk.LabelFrame(top_pane, text="Playlist Slots", padding=6)
        right_frame.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self._slot_tree = ttk.Treeview(
            right_frame,
            columns=("#", "label", "category"),
            show="headings",
        )
        self._slot_tree.heading("#",        text="#")
        self._slot_tree.heading("label",    text="Label")
        self._slot_tree.heading("category", text="Category")
        self._slot_tree.column("#",        width=35,  anchor="center")
        self._slot_tree.column("label",    width=160, anchor="w")
        self._slot_tree.column("category", width=180, anchor="w")
        slot_scroll = ttk.Scrollbar(right_frame, orient="vertical",
                                     command=self._slot_tree.yview)
        self._slot_tree.configure(yscrollcommand=slot_scroll.set)
        self._slot_tree.pack(side="left", fill="both", expand=True)
        slot_scroll.pack(side="right", fill="y")

        # ---- Stream Assignments ----
        assign_lf = ttk.LabelFrame(self.tab_playlist, text="Stream Assignments", padding=10)
        assign_lf.pack(fill="x", padx=8, pady=4)

        headers = ["Stream", "Port", "Assigned Playlist", ""]
        col_widths = [160, 70, 240, 80]
        for col, (hdr, w) in enumerate(zip(headers, col_widths)):
            ttk.Label(assign_lf, text=hdr, font=("Arial", 9, "bold")).grid(
                row=0, column=col, padx=6, pady=(0, 6), sticky="w")
            assign_lf.columnconfigure(col, minsize=w)

        ttk.Separator(assign_lf, orient="horizontal").grid(
            row=1, column=0, columnspan=4, sticky="ew", padx=4, pady=(0, 4))

        playlist_names = self._list_playlist_filenames()
        current_assignments = self._load_stream_playlists()

        for row_idx, stream in enumerate(STREAMS, start=2):
            sid = stream["id"]
            current_pl = current_assignments.get(sid, "")

            ttk.Label(assign_lf, text=stream["label"],
                      font=("Arial", 10, "bold")).grid(
                row=row_idx, column=0, padx=6, pady=4, sticky="w")
            ttk.Label(assign_lf, text=f":{stream['port']}",
                      font=("Courier", 10), foreground="#0077aa").grid(
                row=row_idx, column=1, padx=6, pady=4, sticky="w")

            combo = ttk.Combobox(assign_lf, values=[""] + playlist_names,
                                  state="readonly", width=28)
            combo.set(current_pl)
            combo.grid(row=row_idx, column=2, padx=6, pady=4, sticky="w")
            self._playlist_combos[sid] = combo

            ttk.Button(assign_lf, text="Assign",
                       command=lambda s=sid: self._save_playlist_assignment(s)).grid(
                row=row_idx, column=3, padx=6, pady=4)

        # ---- Now Playing Status ----
        np_lf = ttk.LabelFrame(self.tab_playlist, text="Now Playing Status", padding=8)
        np_lf.pack(fill="x", padx=8, pady=4)

        self._pl_now_playing_var = tk.StringVar(value="— (no playlist active)")
        self._pl_stream_var      = tk.StringVar(value="")
        ttk.Label(np_lf, textvariable=self._pl_now_playing_var,
                  font=("Arial", 11)).pack(anchor="w")
        ttk.Label(np_lf, textvariable=self._pl_stream_var,
                  font=("Arial", 9), foreground="gray").pack(anchor="w")

        # Populate the listbox on load
        self._populate_playlist_listbox()

    def _list_playlist_filenames(self) -> list[str]:
        try:
            return sorted(
                f for f in os.listdir(PLAYLISTS_DIR) if f.endswith(".json")
            )
        except OSError:
            return []

    def _populate_playlist_listbox(self):
        self._playlist_listbox.delete(0, "end")
        for fname in self._list_playlist_filenames():
            self._playlist_listbox.insert("end", fname)

    def _on_playlist_select(self, event):
        sel = self._playlist_listbox.curselection()
        if not sel:
            return
        fname = self._playlist_listbox.get(sel[0])
        path = os.path.join(PLAYLISTS_DIR, fname)
        try:
            with open(path) as f:
                pl = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        for row in self._slot_tree.get_children():
            self._slot_tree.delete(row)
        for idx, slot in enumerate(pl.get("slots", []), start=1):
            self._slot_tree.insert("", "end", values=(
                idx,
                slot.get("label", ""),
                slot.get("category", ""),
            ))

    def _load_stream_playlists(self) -> dict:
        try:
            with open(STREAM_PLAYLISTS_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_playlist_assignment(self, stream_id: str):
        combo = self._playlist_combos.get(stream_id)
        if not combo:
            return
        playlist_file = combo.get()
        try:
            data = self._load_stream_playlists()
            if playlist_file:
                data[stream_id] = playlist_file
            else:
                data.pop(stream_id, None)
            os.makedirs(os.path.dirname(STREAM_PLAYLISTS_FILE), exist_ok=True)
            with open(STREAM_PLAYLISTS_FILE, "w") as f:
                json.dump(data, f, indent=2)
            label = next(
                (s["label"] for s in STREAMS if s["id"] == stream_id), stream_id
            )
            if playlist_file:
                messagebox.showinfo("Saved", f"Playlist for {label}:\n{playlist_file}")
            else:
                messagebox.showinfo("Cleared", f"Playlist assignment cleared for {label}.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save assignment:\n{e}")

    def _refresh_playlist(self):
        # Refresh listbox filenames in case new playlists were added
        current_names = list(self._playlist_listbox.get(0, "end"))
        new_names = self._list_playlist_filenames()
        if current_names != new_names:
            self._populate_playlist_listbox()
            # Also update combobox values
            for combo in self._playlist_combos.values():
                combo["values"] = [""] + new_names

        # Read state file for Now Playing
        try:
            with open(PLAYLIST_STATE_FILE) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._pl_now_playing_var.set("— (no playlist active)")
            self._pl_stream_var.set("")
            return

        stream_state = state.get("stream_8000")
        if not stream_state:
            self._pl_now_playing_var.set("— (stream_8000 not in state)")
            self._pl_stream_var.set("")
            return

        label    = stream_state.get("current_label", "?")
        category = stream_state.get("current_category", "?")
        slot_idx = stream_state.get("current_slot", 0) + 1
        total    = stream_state.get("slot_count", "?")
        pl_name  = stream_state.get("playlist_name", "?")
        pl_file  = stream_state.get("playlist_file", "?")
        updated  = stream_state.get("updated_at", "")

        self._pl_now_playing_var.set(
            f"Now Playing: {label} ({category}) — Slot {slot_idx} of {total}"
        )
        self._pl_stream_var.set(
            f"on All Florida (:8000)  |  {pl_name} ({pl_file})"
            + (f"  |  updated {updated}" if updated else "")
        )

    # ------------------------------------------------------------------
    # CONFIG TAB
    # ------------------------------------------------------------------
    def _build_config_tab(self):
        self._zone_combos = {}  # stream_id → ttk.Combobox

        lf = ttk.LabelFrame(self.tab_config, text="Stream Zone Configuration", padding=12)
        lf.pack(fill="x", padx=16, pady=14)

        headers = ["Stream", "Port", "Mount", "Assigned Zone", ""]
        col_widths = [160, 70, 180, 200, 80]
        for col, (hdr, w) in enumerate(zip(headers, col_widths)):
            ttk.Label(lf, text=hdr, font=("Arial", 9, "bold")).grid(
                row=0, column=col, padx=6, pady=(0, 8), sticky="w")
            lf.columnconfigure(col, minsize=w)

        ttk.Separator(lf, orient="horizontal").grid(
            row=1, column=0, columnspan=5, sticky="ew", padx=4, pady=(0, 6))

        overrides = self._load_zone_overrides()

        for row_idx, stream in enumerate(STREAMS, start=2):
            sid          = stream["id"]
            current_zone = overrides.get(sid, stream["default_zone"])

            ttk.Label(lf, text=stream["label"], font=("Arial", 10, "bold")).grid(
                row=row_idx, column=0, padx=6, pady=5, sticky="w")

            ttk.Label(lf, text=f":{stream['port']}",
                      font=("Courier", 10), foreground="#0077aa").grid(
                row=row_idx, column=1, padx=6, pady=5, sticky="w")

            ttk.Label(lf, text=stream["mount"],
                      font=("Courier", 9), foreground="gray").grid(
                row=row_idx, column=2, padx=6, pady=5, sticky="w")

            combo = ttk.Combobox(lf, values=AVAILABLE_ZONES, state="readonly", width=22)
            combo.set(current_zone)
            combo.grid(row=row_idx, column=3, padx=6, pady=5, sticky="w")
            self._zone_combos[sid] = combo

            ttk.Button(lf, text="Save",
                       command=lambda s=sid: self._save_zone(s)).grid(
                row=row_idx, column=4, padx=6, pady=5)

        note = ttk.Label(self.tab_config,
                         text="Zone changes take effect immediately and persist across restarts.",
                         foreground="gray", font=("Arial", 8, "italic"))
        note.pack(anchor="w", padx=18, pady=(0, 8))

        self._build_serial_section()
        self._build_smtp_section()

    # ------------------------------------------------------------------
    # SERIAL ALERT SECTION (inside Config tab)
    # ------------------------------------------------------------------
    def _build_serial_section(self):
        import serial.tools.list_ports

        slf = ttk.LabelFrame(self.tab_config, text="Serial Alert Output (priority_1)", padding=12)
        slf.pack(fill="x", padx=16, pady=(0, 14))

        # Row 0 — headers
        for col, hdr in enumerate(["COM Port", "Baud Rate", "Prefix", "", ""]):
            ttk.Label(slf, text=hdr, font=("Arial", 9, "bold")).grid(
                row=0, column=col, padx=6, pady=(0, 6), sticky="w")
        ttk.Separator(slf, orient="horizontal").grid(
            row=1, column=0, columnspan=5, sticky="ew", padx=4, pady=(0, 6))

        # Populate port list from system + any saved port
        system_ports = [p.device for p in serial.tools.list_ports.comports()]
        cfg = self._load_serial_cfg()
        saved_port = cfg.get("port", "")
        if saved_port and saved_port not in system_ports:
            system_ports.insert(0, saved_port)
        port_values = [""] + sorted(system_ports)

        # Port dropdown
        self._serial_port_combo = ttk.Combobox(slf, values=port_values, width=18)
        self._serial_port_combo.set(saved_port)
        self._serial_port_combo.grid(row=2, column=0, padx=6, pady=5, sticky="w")

        # Baud rate dropdown
        baud_values = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
        self._serial_baud_combo = ttk.Combobox(slf, values=baud_values, state="readonly", width=10)
        self._serial_baud_combo.set(str(cfg.get("baud", 9600)))
        self._serial_baud_combo.grid(row=2, column=1, padx=6, pady=5, sticky="w")

        # Prefix label (read-only display)
        ttk.Label(slf, text="BEACON ALERT: …",
                  font=("Courier", 9), foreground="#0077aa").grid(
            row=2, column=2, padx=6, pady=5, sticky="w")

        # Save button
        ttk.Button(slf, text="Save",
                   command=self._save_serial_cfg).grid(
            row=2, column=3, padx=6, pady=5)

        # Test button
        ttk.Button(slf, text="Test",
                   command=self._test_serial).grid(
            row=2, column=4, padx=6, pady=5)

        # Status label
        self._serial_status_var = tk.StringVar(value="")
        ttk.Label(slf, textvariable=self._serial_status_var,
                  font=("Arial", 8, "italic"), foreground="gray").grid(
            row=3, column=0, columnspan=5, padx=6, pady=(0, 2), sticky="w")

        serial_note = ttk.Label(self.tab_config,
                                text="Serial output is sent by serial-alert.service whenever a priority_1 alert WAV is written.",
                                foreground="gray", font=("Arial", 8, "italic"))
        serial_note.pack(anchor="w", padx=18, pady=(0, 8))

    def _load_serial_cfg(self) -> dict:
        try:
            with open(SERIAL_CFG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"port": "", "baud": 9600}

    def _save_serial_cfg(self):
        port = self._serial_port_combo.get().strip()
        baud = self._serial_baud_combo.get().strip()
        try:
            cfg = {"port": port, "baud": int(baud)}
            os.makedirs(os.path.dirname(SERIAL_CFG_FILE), exist_ok=True)
            with open(SERIAL_CFG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
            self._serial_status_var.set(f"Saved — port: {port or '(none)'}  baud: {baud}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save serial config:\n{e}")

    def _test_serial(self):
        import serial as _serial
        port = self._serial_port_combo.get().strip()
        baud = self._serial_baud_combo.get().strip()
        if not port:
            messagebox.showwarning("No Port", "Select a COM port first.")
            return
        try:
            msg = "BEACON ALERT: TEST — Serial output is working.\r\n"
            with _serial.Serial(port, int(baud), timeout=2) as ser:
                ser.write(msg.encode("ascii", errors="replace"))
            self._serial_status_var.set(f"Test sent → {port} @ {baud} baud")
        except Exception as e:
            self._serial_status_var.set(f"Test failed: {e}")
            messagebox.showerror("Serial Error", f"Could not send test:\n{e}")

    # ------------------------------------------------------------------
    # SMTP SETTINGS SECTION (inside Config tab)
    # ------------------------------------------------------------------
    def _build_smtp_section(self):
        lf = ttk.LabelFrame(self.tab_config, text="Email / SMTP Settings", padding=12)
        lf.pack(fill="x", padx=16, pady=(0, 14))

        cfg = self._load_smtp_cfg()

        # Row headers
        for col, hdr in enumerate(["SMTP Host", "Port", "From Address", "To Address(es)"]):
            ttk.Label(lf, text=hdr, font=("Arial", 9, "bold")).grid(
                row=0, column=col, padx=6, pady=(0, 6), sticky="w")
        ttk.Separator(lf, orient="horizontal").grid(
            row=1, column=0, columnspan=4, sticky="ew", padx=4, pady=(0, 6))

        # Host
        self._smtp_host_var = tk.StringVar(value=cfg.get("smtp_host", ""))
        ttk.Entry(lf, textvariable=self._smtp_host_var, width=24).grid(
            row=2, column=0, padx=6, pady=5, sticky="w")

        # Port
        self._smtp_port_var = tk.StringVar(value=str(cfg.get("smtp_port", 587)))
        port_combo = ttk.Combobox(lf, textvariable=self._smtp_port_var,
                                   values=["25", "465", "587", "2525"], width=7)
        port_combo.grid(row=2, column=1, padx=6, pady=5, sticky="w")

        # From
        self._smtp_from_var = tk.StringVar(value=cfg.get("mail_from", ""))
        ttk.Entry(lf, textvariable=self._smtp_from_var, width=30).grid(
            row=2, column=2, padx=6, pady=5, sticky="w")

        # To
        self._smtp_to_var = tk.StringVar(value=cfg.get("mail_to", ""))
        ttk.Entry(lf, textvariable=self._smtp_to_var, width=30).grid(
            row=2, column=3, padx=6, pady=5, sticky="w")

        # Row 2 — auth/TLS row
        for col, hdr in enumerate(["Username", "Password", "", ""]):
            ttk.Label(lf, text=hdr, font=("Arial", 9, "bold")).grid(
                row=3, column=col, padx=6, pady=(8, 2), sticky="w")

        # Username
        self._smtp_user_var = tk.StringVar(value=cfg.get("smtp_user", ""))
        ttk.Entry(lf, textvariable=self._smtp_user_var, width=24).grid(
            row=4, column=0, padx=6, pady=5, sticky="w")

        # Password
        self._smtp_pass_var = tk.StringVar(value=cfg.get("smtp_pass", ""))
        self._smtp_pass_entry = ttk.Entry(lf, textvariable=self._smtp_pass_var,
                                           show="*", width=24)
        self._smtp_pass_entry.grid(row=4, column=1, padx=6, pady=5, sticky="w")

        # TLS checkbox
        self._smtp_tls_var = tk.BooleanVar(value=cfg.get("use_tls", True))
        ttk.Checkbutton(lf, text="Use STARTTLS", variable=self._smtp_tls_var).grid(
            row=4, column=2, padx=6, pady=5, sticky="w")

        # Auth checkbox
        self._smtp_auth_var = tk.BooleanVar(value=cfg.get("use_auth", True))
        ttk.Checkbutton(lf, text="Use Authentication", variable=self._smtp_auth_var).grid(
            row=4, column=3, padx=6, pady=5, sticky="w")

        # Button row
        btn_frame = ttk.Frame(lf)
        btn_frame.grid(row=5, column=0, columnspan=4, sticky="w", padx=4, pady=(8, 2))
        ttk.Button(btn_frame, text="Save", command=self._save_smtp_cfg).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Send Test Email", command=self._test_smtp).pack(side="left", padx=6)
        self._smtp_show_pass = tk.BooleanVar(value=False)
        ttk.Checkbutton(btn_frame, text="Show password",
                        variable=self._smtp_show_pass,
                        command=self._toggle_smtp_pass).pack(side="left", padx=12)

        # Status label
        self._smtp_status_var = tk.StringVar(value="")
        ttk.Label(lf, textvariable=self._smtp_status_var,
                  font=("Arial", 8, "italic"), foreground="gray").grid(
            row=6, column=0, columnspan=4, padx=6, pady=(0, 2), sticky="w")

    def _load_smtp_cfg(self) -> dict:
        try:
            with open(SMTP_CFG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "use_tls":   True,
                "use_auth":  True,
                "smtp_user": "",
                "smtp_pass": "",
                "mail_from": "",
                "mail_to":   "",
            }

    def _save_smtp_cfg(self):
        cfg = {
            "smtp_host": self._smtp_host_var.get().strip(),
            "smtp_port": int(self._smtp_port_var.get().strip() or 587),
            "use_tls":   self._smtp_tls_var.get(),
            "use_auth":  self._smtp_auth_var.get(),
            "smtp_user": self._smtp_user_var.get().strip(),
            "smtp_pass": self._smtp_pass_var.get(),
            "mail_from": self._smtp_from_var.get().strip(),
            "mail_to":   self._smtp_to_var.get().strip(),
        }
        try:
            os.makedirs(os.path.dirname(SMTP_CFG_FILE), exist_ok=True)
            with open(SMTP_CFG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
            self._smtp_status_var.set(
                f"Saved — {cfg['smtp_host']}:{cfg['smtp_port']}  →  {cfg['mail_to'] or '(no recipient)'}"
            )
        except Exception as exc:
            messagebox.showerror("Error", f"Could not save SMTP config:\n{exc}")

    def _test_smtp(self):
        self._save_smtp_cfg()
        cfg = self._load_smtp_cfg()
        host     = cfg.get("smtp_host", "")
        port     = int(cfg.get("smtp_port", 587))
        use_tls  = cfg.get("use_tls", False)
        use_auth = cfg.get("use_auth", False)
        user     = cfg.get("smtp_user", "")
        passwd   = cfg.get("smtp_pass", "")
        mail_from = cfg.get("mail_from") or user
        mail_to   = cfg.get("mail_to", "")

        if not host:
            messagebox.showwarning("No Host", "Enter an SMTP host first.")
            return
        if not mail_to:
            messagebox.showwarning("No Recipient", "Enter a To address first.")
            return

        import smtplib
        from email.message import EmailMessage

        def _send():
            try:
                msg = EmailMessage()
                msg["Subject"] = "FPREN Alerts Monitor — SMTP Test"
                msg["From"]    = mail_from
                msg["To"]      = mail_to
                msg.set_content(
                    "This is a test email from the FPREN Alerts Monitor.\n"
                    "If you received this, your SMTP settings are working correctly."
                )
                with smtplib.SMTP(host, port, timeout=10) as smtp:
                    smtp.ehlo()
                    if use_tls:
                        smtp.starttls()
                        smtp.ehlo()
                    if use_auth and user and passwd:
                        smtp.login(user, passwd)
                    smtp.send_message(msg)
                self.after(0, lambda: self._smtp_status_var.set(f"Test email sent to {mail_to}"))
                self.after(0, lambda: messagebox.showinfo("Email Sent", f"Test email sent to:\n{mail_to}"))
            except Exception as exc:
                self.after(0, lambda: self._smtp_status_var.set(f"Test failed: {exc}"))
                self.after(0, lambda: messagebox.showerror("SMTP Error", f"Could not send test email:\n{exc}"))

        self._smtp_status_var.set("Sending…")
        threading.Thread(target=_send, daemon=True).start()

    def _toggle_smtp_pass(self):
        self._smtp_pass_entry.config(show="" if self._smtp_show_pass.get() else "*")

    def _load_zone_overrides(self) -> dict:
        try:
            with open(ZONE_OVERRIDES_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_zone(self, stream_id: str):
        combo = self._zone_combos.get(stream_id)
        if not combo:
            return
        zone = combo.get()
        try:
            overrides = self._load_zone_overrides()
            overrides[stream_id] = zone
            import os
            os.makedirs(os.path.dirname(ZONE_OVERRIDES_FILE), exist_ok=True)
            with open(ZONE_OVERRIDES_FILE, "w") as f:
                json.dump(overrides, f, indent=2)
            messagebox.showinfo("Saved", f"Zone for {stream_id} set to:\n{zone}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save zone:\n{e}")

    # ------------------------------------------------------------------
    # REFRESH METHODS
    # ------------------------------------------------------------------
    def refresh_status(self):
        # Preserve current notebook tab across refresh
        try:
            _current_tab = self.tab_control.index(self.tab_control.select())
        except Exception:
            _current_tab = None

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
        self._refresh_playlist()
        self._refresh_weather()

        # Restore the tab that was active before the refresh
        if _current_tab is not None:
            try:
                self.tab_control.select(_current_tab)
            except Exception:
                pass

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

    @staticmethod
    def _to_f(c):
        try:
            return round(float(c) * 9 / 5 + 32, 1)
        except (TypeError, ValueError):
            return ""

    def _refresh_airport(self):
        for row in self.airport_tree.get_children():
            self.airport_tree.delete(row)
        # Color-code by flight category
        flt_colors = {"LIFR": "#ff6666", "IFR": "#ffaa44", "MVFR": "#6699ff", "VFR": "#44bb44"}
        for rec in airport_metar_col.find().sort("icaoId", 1):
            flt_cat = rec.get("fltCat", "")
            obs_time = rec.get("obsTime", "")
            temp_c = rec.get("temp", "")
            dewp_c = rec.get("dewp", "")
            iid = self.airport_tree.insert("", "end", values=(
                rec.get("icaoId", ""),
                rec.get("name", ""),
                flt_cat,
                self._to_f(temp_c),
                temp_c,
                self._to_f(dewp_c),
                dewp_c,
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

        # Now Playing — read from station engine's JSON file
        try:
            with open("/tmp/beacon_now_playing.json") as f:
                np = json.load(f)
            title    = np.get("title", "—")
            category = np.get("category", "")
            started  = np.get("started_at", "")
            self.now_playing_var.set(
                f"{title}  [{category}]  —  started {started}" if started else f"{title}  [{category}]"
            )
        except (FileNotFoundError, json.JSONDecodeError):
            self.now_playing_var.set("— (station engine not running)")

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

    # ------------------------------------------------------------------
    # WEATHER TAB
    # ------------------------------------------------------------------
    _WEATHER_CITIES = [
        {"name": "Gainesville",  "icao": "KGNV", "lat": 29.6917, "lon": -82.2760},
        {"name": "Jacksonville", "icao": "KJAX", "lat": 30.4941, "lon": -81.6879},
        {"name": "Miami",        "icao": "KMIA", "lat": 25.7959, "lon": -80.2870},
        {"name": "Orlando",      "icao": "KMCO", "lat": 28.4294, "lon": -81.3089},
        {"name": "Tampa",        "icao": "KTPA", "lat": 27.9755, "lon": -82.5332},
    ]
    _NWS_UA = "BeaconWeatherStation/1.0 (lh_admin@localhost)"

    def _build_weather_tab(self):
        btn_frame = ttk.Frame(self.tab_weather)
        btn_frame.pack(fill="x", padx=8, pady=6)
        ttk.Button(btn_frame, text="↻ Refresh Now",
                   command=lambda: self._fetch_weather_async(force=True)).pack(side="left")
        self._wx_status_var = tk.StringVar(value="")
        ttk.Label(btn_frame, textvariable=self._wx_status_var,
                  font=("Arial", 8), foreground="gray").pack(side="left", padx=10)

        self._wx_text = _st.ScrolledText(
            self.tab_weather, wrap="word", font=("Consolas", 9), state="disabled",
        )
        self._wx_text.pack(expand=1, fill="both", padx=8, pady=(0, 8))

        for tag, kw in [
            ("h1",      {"font": ("Arial", 12, "bold"), "foreground": "#0077aa"}),
            ("h2",      {"font": ("Arial", 10, "bold"), "foreground": "#444444"}),
            ("kv",      {"font": ("Consolas", 9),       "foreground": "#333333"}),
            ("VFR",     {"font": ("Consolas", 9, "bold"), "foreground": "#155724", "background": "#d4f8d4"}),
            ("MVFR",    {"font": ("Consolas", 9, "bold"), "foreground": "#004085", "background": "#cce5ff"}),
            ("IFR",     {"font": ("Consolas", 9, "bold"), "foreground": "#856404", "background": "#ffe5b4"}),
            ("LIFR",    {"font": ("Consolas", 9, "bold"), "foreground": "#721c24", "background": "#f8d7da"}),
            ("fc_name", {"font": ("Consolas", 9, "bold"), "foreground": "#1565c0"}),
            ("fc_txt",  {"font": ("Consolas", 9),         "foreground": "#444444"}),
            ("sep",     {"foreground": "#cccccc"}),
        ]:
            self._wx_text.tag_configure(tag, **kw)

        self._wx_cache: dict = {}
        self._wx_last_ts: float = 0.0
        self._wx_fetching: bool = False
        self._wx_grid_cache: dict = {}   # icao → nws forecast url

    def _refresh_weather(self):
        if not hasattr(self, "_wx_cache"):
            return
        now = time.monotonic()
        if self._wx_cache and (now - self._wx_last_ts) < 900:
            return   # cache is fresh
        if not getattr(self, "_wx_fetching", False):
            self._fetch_weather_async()

    def _fetch_weather_async(self, force: bool = False):
        if getattr(self, "_wx_fetching", False) and not force:
            return
        self._wx_fetching = True
        self._wx_status_var.set("Fetching weather…")
        threading.Thread(target=self._fetch_weather_bg, daemon=True).start()

    def _fetch_weather_bg(self):
        """Background thread: METAR from MongoDB + 7-day forecast from NWS."""
        result = []
        for city in self._WEATHER_CITIES:
            icao  = city["icao"]
            entry = {"name": city["name"], "icao": icao, "current": None, "forecast": []}

            # Current conditions from MongoDB
            try:
                metar = airport_metar_col.find_one({"icaoId": icao})
                if metar:
                    entry["current"] = {
                        "temp_f":   self._to_f(metar.get("temp")),
                        "wind_dir": metar.get("wdir"),
                        "wind_spd": metar.get("wspd"),
                        "visib":    metar.get("visib"),
                        "flt_cat":  metar.get("fltCat", ""),
                        "obs_time": metar.get("obsTime", ""),
                    }
            except Exception:
                pass

            # 7-day forecast from NWS API
            try:
                fc_url = self._wx_grid_cache.get(icao)
                if not fc_url:
                    req = urllib.request.Request(
                        f"https://api.weather.gov/points/{city['lat']},{city['lon']}",
                        headers={"User-Agent": self._NWS_UA, "Accept": "application/geo+json"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        pts = json.loads(r.read().decode())
                    fc_url = pts["properties"]["forecast"]
                    self._wx_grid_cache[icao] = fc_url

                req2 = urllib.request.Request(
                    fc_url,
                    headers={"User-Agent": self._NWS_UA, "Accept": "application/geo+json"},
                )
                with urllib.request.urlopen(req2, timeout=10) as r2:
                    fc_data = json.loads(r2.read().decode())
                entry["forecast"] = [
                    {
                        "name":           p.get("name", ""),
                        "temp":           p.get("temperature"),
                        "temp_unit":      p.get("temperatureUnit", "F"),
                        "short_forecast": p.get("shortForecast", ""),
                        "precip_pct":     (p.get("probabilityOfPrecipitation") or {}).get("value"),
                    }
                    for p in fc_data["properties"]["periods"][:7]
                ]
            except Exception:
                pass

            result.append(entry)

        new_cache = {c["icao"]: c for c in result}
        self.after(0, lambda: self._on_weather_fetched(new_cache))

    def _on_weather_fetched(self, data: dict):
        self._wx_cache    = data
        self._wx_last_ts  = time.monotonic()
        self._wx_fetching = False
        self._render_weather(list(data.values()))
        self._wx_status_var.set(f"Updated {datetime.now().strftime('%H:%M:%S')}")

    def _render_weather(self, cities: list):
        txt = self._wx_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for city in cities:
            cur = city.get("current") or {}
            fc  = city.get("forecast") or []
            flt = cur.get("flt_cat", "")

            txt.insert("end", f"\n  {city['name']}, FL  ({city['icao']})\n", "h1")
            txt.insert("end", "  " + "\u2500" * 58 + "\n", "sep")

            txt.insert("end", "  Current Conditions\n", "h2")
            temp_str = f"{cur['temp_f']}\u00b0F" if cur.get("temp_f") is not None else "\u2014"
            txt.insert("end", f"    Temperature :  {temp_str}\n", "kv")
            wdir, wspd = cur.get("wind_dir"), cur.get("wind_spd")
            wind = f"{wdir}\u00b0 at {wspd} kt" if wdir is not None and wspd is not None else "\u2014"
            txt.insert("end", f"    Wind        :  {wind}\n", "kv")
            txt.insert("end", f"    Visibility  :  {cur.get('visib', chr(8212))} mi\n", "kv")
            txt.insert("end", f"    Flight Cat  :  ", "kv")
            if flt:
                txt.insert("end", f" {flt} ", flt)
            obs_raw = cur.get("obs_time", "")
            if isinstance(obs_raw, str) and "T" in obs_raw:
                try:
                    obs_raw = datetime.fromisoformat(obs_raw).strftime("%m-%d %H:%MZ")
                except Exception:
                    pass
            txt.insert("end", f"   Obs: {obs_raw}\n", "kv")

            if fc:
                txt.insert("end", "  7-Day Forecast\n", "h2")
                for p in fc:
                    rain = f"  Rain: {p['precip_pct']}%" if p.get("precip_pct") is not None else ""
                    txt.insert("end", f"    {p.get('name', ''):<18}", "fc_name")
                    txt.insert("end",
                               f"{p.get('temp', chr(8212))}\u00b0{p.get('temp_unit','F')}  "
                               f"{p.get('short_forecast', '')}{rain}\n",
                               "fc_txt")
            txt.insert("end", "\n")
        txt.configure(state="disabled")

    def auto_refresh(self):
        self.refresh_status()
        self.after(REFRESH_INTERVAL * 1000, self.auto_refresh)

# -------------------------------
# RUN APPLICATION
# -------------------------------
if __name__ == "__main__":
    app = SystemdMonitor()
    app.mainloop()
