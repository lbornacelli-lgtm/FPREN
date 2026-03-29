"""
FPREN Alerts Dashboard - Desktop App
Mirrors http://localhost:5000 exactly with 5 tabs:
Config | Weather | Playlist | Icecast | Alerts & Data
Syncs every 5 seconds via JSON API. Changes made here update the web dashboard and vice versa.
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import requests

API = "http://localhost:5000"
REFRESH_SEC = 60

# Shared session for authenticated requests
_session = requests.Session()

# ─────────────────────────────────────────────────────────── helpers

def api_login(username, password):
    """Log in to the web dashboard and store session cookie."""
    try:
        r = _session.post(f"{API}/login",
                          data={"username": username, "password": password},
                          timeout=8, allow_redirects=False)
        # Successful login redirects to /
        return r.status_code in (302, 200)
    except Exception:
        return False

def api_get(path):
    try:
        r = _session.get(f"{API}{path}", timeout=8)
        if r.status_code == 302 or "/login" in r.url:
            return {"_error": "Not authenticated"}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}

def api_post(path, data=None):
    try:
        r = _session.post(f"{API}{path}", json=data or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


# ─────────────────────────────────────────────────────────── main app

class FPRENApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FPREN Alerts Dashboard")
        self.geometry("1300x800")
        self.configure(bg="#212529")
        self._build_header()
        self._build_tabs()
        self._schedule_refresh()

    # ── Header ───────────────────────────────────────────────────────
    def _build_header(self):
        header = tk.Frame(self, bg="#212529", pady=10)
        header.pack(fill="x")

        # Logo area
        logo_frame = tk.Frame(header, bg="#1a1f24", padx=10, pady=6)
        logo_frame.pack(side="left", padx=(15, 0))
        tk.Label(logo_frame, text="Florida Public Radio\nFPREN\nEmergency Network",
                 font=("Arial", 7, "bold"), fg="white", bg="#1a1f24",
                 justify="center").pack()

        # Title
        title_frame = tk.Frame(header, bg="#212529", padx=12)
        title_frame.pack(side="left")
        tk.Label(title_frame, text="FPREN Alerts Dashboard",
                 font=("Arial", 16, "bold"), fg="white", bg="#212529").pack(anchor="w")
        tk.Label(title_frame, text="Weather • Traffic • Alerts • Icecast",
                 font=("Arial", 9), fg="#adb5bd", bg="#212529").pack(anchor="w")

        # Feedback button
        tk.Button(header, text="Feedback",
                  command=self._show_feedback,
                  bg="#6c757d", fg="white", font=("Arial", 9),
                  relief="flat", padx=10, pady=4).pack(side="right", padx=15)

        # Last updated
        self.updated_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self.updated_var,
                 font=("Arial", 8), fg="#adb5bd", bg="#212529").pack(side="right", padx=10)

    # ── Tabs ─────────────────────────────────────────────────────────
    def _build_tabs(self):
        style = ttk.Style()
        style.configure("TNotebook", background="#212529", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Arial", 10),
                        padding=[16, 8], background="#343a40", foreground="#adb5bd")
        style.map("TNotebook.Tab",
                  background=[("selected", "#212529")],
                  foreground=[("selected", "#0dcaf0")])

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        self.tab_config    = ttk.Frame(self.nb)
        self.tab_weather   = ttk.Frame(self.nb)
        self.tab_playlist  = ttk.Frame(self.nb)
        self.tab_icecast   = ttk.Frame(self.nb)
        self.tab_data      = ttk.Frame(self.nb)
        self.tab_ai        = ttk.Frame(self.nb)

        self.nb.add(self.tab_config,   text="Config")
        self.nb.add(self.tab_weather,  text="Weather")
        self.nb.add(self.tab_playlist, text="Playlist")
        self.nb.add(self.tab_icecast,  text="Icecast")
        self.nb.add(self.tab_data,     text="Alerts & Data")
        self.nb.add(self.tab_ai,       text="AI Broadcast")

        self._build_config_tab()
        self._build_weather_tab()
        self._build_playlist_tab()
        self._build_icecast_tab()
        self._build_data_tab()
        self._build_ai_tab()

    # ══════════════════════════════════════════ CONFIG TAB
    def _build_config_tab(self):
        f = self.tab_config
        f.configure(style="TFrame")

        canvas = tk.Canvas(f, bg="#f8f9fa", highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        inner = tk.Frame(canvas, bg="#f8f9fa")
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

        # SMTP Config
        smtp_card = tk.LabelFrame(inner, text="  SMTP Email Config  ",
                                   font=("Arial", 10, "bold"),
                                   bg="white", relief="solid", bd=1, padx=15, pady=10)
        smtp_card.pack(fill="x", padx=20, pady=(15, 0))

        self._smtp_vars = {}
        fields = [
            ("smtp_host",  "SMTP Host",     ""),
            ("smtp_port",  "SMTP Port",     "587"),
            ("smtp_user",  "SMTP User",     ""),
            ("smtp_pass",  "SMTP Password", ""),
            ("mail_from",  "From Address",  ""),
            ("mail_to",    "To Address",    ""),
        ]
        for i, (key, label, default) in enumerate(fields):
            tk.Label(smtp_card, text=label, font=("Arial", 9),
                     bg="white", width=16, anchor="e").grid(
                row=i, column=0, padx=(0, 8), pady=4, sticky="e")
            v = tk.StringVar(value=default)
            self._smtp_vars[key] = v
            show = "*" if key == "smtp_pass" else ""
            tk.Entry(smtp_card, textvariable=v, font=("Arial", 10),
                     width=35, show=show,
                     relief="solid", bd=1).grid(row=i, column=1, pady=4, sticky="w")

        # TLS / Auth checkboxes
        self._smtp_tls  = tk.BooleanVar(value=True)
        self._smtp_auth = tk.BooleanVar(value=True)
        tk.Checkbutton(smtp_card, text="Use TLS",
                       variable=self._smtp_tls, bg="white",
                       font=("Arial", 9)).grid(row=len(fields), column=1, sticky="w")
        tk.Checkbutton(smtp_card, text="Use Auth",
                       variable=self._smtp_auth, bg="white",
                       font=("Arial", 9)).grid(row=len(fields)+1, column=1, sticky="w")

        btn_row = tk.Frame(smtp_card, bg="white")
        btn_row.grid(row=len(fields)+2, column=0, columnspan=2, pady=8)

        self._smtp_status = tk.StringVar(value="")
        tk.Label(btn_row, textvariable=self._smtp_status,
                 font=("Arial", 9), fg="#198754", bg="white").pack(side="left", padx=10)

        tk.Button(btn_row, text="Test Email",
                  command=self._smtp_test,
                  bg="#6c757d", fg="white", font=("Arial", 9),
                  relief="flat", padx=10, pady=3).pack(side="right", padx=6)
        tk.Button(btn_row, text="Save SMTP",
                  command=self._smtp_save,
                  bg="#0d6efd", fg="white", font=("Arial", 9),
                  relief="flat", padx=10, pady=3).pack(side="right")

        # Stream Zone Config
        zone_card = tk.LabelFrame(inner, text="  Stream Zone Config  ",
                                   font=("Arial", 10, "bold"),
                                   bg="white", relief="solid", bd=1, padx=15, pady=10)
        zone_card.pack(fill="x", padx=20, pady=(15, 20))

        self._zone_vars = {}
        self._zone_status = tk.StringVar(value="")
        tk.Label(zone_card, textvariable=self._zone_status,
                 font=("Arial", 9), fg="#198754", bg="white").pack(anchor="w", pady=(0, 6))

        self._zone_frame = tk.Frame(zone_card, bg="white")
        self._zone_frame.pack(fill="x")

    def _populate_config(self, smtp, streams):
        # Only load SMTP fields on first load, never overwrite after that
        if not getattr(self, "_smtp_loaded_once", False):
            self._smtp_loaded_once = True
            for key, v in self._smtp_vars.items():
                v.set(str(smtp.get(key, "")))
            self._smtp_tls.set(bool(smtp.get("use_tls", True)))
            self._smtp_auth.set(bool(smtp.get("use_auth", True)))


    def _smtp_save(self):
        data = {k: v.get() for k, v in self._smtp_vars.items()}
        data["use_tls"]  = self._smtp_tls.get()
        data["use_auth"] = self._smtp_auth.get()
        res = api_post("/api/smtp", data)
        self._smtp_status.set(res.get("message", res.get("_error", "")))
        if res.get("ok"):
            self._smtp_loaded_once = False  # allow next refresh to reload from server

    def _smtp_test(self):
        data = {k: v.get() for k, v in self._smtp_vars.items()}
        data["use_tls"]  = self._smtp_tls.get()
        data["use_auth"] = self._smtp_auth.get()
        self._smtp_status.set("Sending test email...")
        def task():
            res = api_post("/api/smtp/test", data)
            self.after(0, lambda: self._smtp_status.set(
                res.get("message", res.get("_error", ""))))
        threading.Thread(target=task, daemon=True).start()

    def _zone_save(self, stream_id, zone):
        res = api_post(f"/api/streams/{stream_id}/zone", {"zone": zone})
        self._zone_status.set(res.get("message", res.get("_error", "")))

    # ══════════════════════════════════════════ WEATHER TAB
    def _build_weather_tab(self):
        f = self.tab_weather

        canvas = tk.Canvas(f, bg="#f8f9fa", highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        self._wx_inner = tk.Frame(canvas, bg="#f8f9fa")
        canvas.create_window((0, 0), window=self._wx_inner, anchor="nw")
        self._wx_inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

    def _populate_weather(self, cities):
        for w in self._wx_inner.winfo_children():
            w.destroy()

        for city in cities:
            card = tk.LabelFrame(self._wx_inner,
                                  text=f"  {city['name']} ({city['icao']})  ",
                                  font=("Arial", 10, "bold"),
                                  bg="white", relief="solid", bd=1,
                                  padx=12, pady=8)
            card.pack(fill="x", padx=20, pady=(10, 0))

            cur = city.get("current")
            if cur:
                cur_row = tk.Frame(card, bg="white")
                cur_row.pack(fill="x")
                info = (f"Temp: {cur.get('temp_f','—')}°F  |  "
                        f"Wind: {cur.get('wind_dir','—')}° @ {cur.get('wind_spd','—')}kts  |  "
                        f"Vis: {cur.get('visib','—')}sm  |  "
                        f"Cat: {cur.get('flt_cat','—')}  |  "
                        f"Obs: {cur.get('obs_time','—')}")
                tk.Label(cur_row, text=info, font=("Arial", 9),
                         bg="white", fg="#212529").pack(anchor="w")

            # Forecast
            fc = city.get("forecast", [])
            if fc:
                fc_frame = tk.Frame(card, bg="white")
                fc_frame.pack(fill="x", pady=(6, 0))
                for i, p in enumerate(fc):
                    col = tk.Frame(fc_frame, bg="#f8f9fa", relief="solid",
                                   bd=1, padx=8, pady=6)
                    col.grid(row=0, column=i, padx=3, sticky="n")
                    tk.Label(col, text=p.get("name", ""),
                             font=("Arial", 8, "bold"),
                             bg="#f8f9fa").pack()
                    tk.Label(col, text=f"{p.get('temp','—')}°{p.get('temp_unit','F')}",
                             font=("Arial", 11, "bold"),
                             fg="#0d6efd", bg="#f8f9fa").pack()
                    tk.Label(col, text=p.get("short_forecast", "")[:20],
                             font=("Arial", 7), bg="#f8f9fa",
                             wraplength=80).pack()
                    if p.get("precip_pct") is not None:
                        tk.Label(col, text=f"💧 {p['precip_pct']}%",
                                 font=("Arial", 7), bg="#f8f9fa").pack()

    # ══════════════════════════════════════════ PLAYLIST TAB
    def _build_playlist_tab(self):
        f = self.tab_playlist

        # Toolbar
        toolbar = tk.Frame(f, bg="#f8f9fa", pady=8, padx=15)
        toolbar.pack(fill="x")

        tk.Label(toolbar, text="Stream:",
                 font=("Arial", 9, "bold"), bg="#f8f9fa").pack(side="left")

        self._pl_stream_var = tk.StringVar()
        self._pl_stream_select = ttk.Combobox(toolbar, textvariable=self._pl_stream_var,
                                               state="readonly", width=22)
        self._pl_stream_select.pack(side="left", padx=4)
        self._pl_stream_select.bind("<<ComboboxSelected>>", self._on_stream_select)

        tk.Label(toolbar, text="Playlist:",
                 font=("Arial", 9, "bold"), bg="#f8f9fa").pack(side="left", padx=(8,0))

        self._pl_select_var = tk.StringVar()
        self._pl_select = ttk.Combobox(toolbar, textvariable=self._pl_select_var,
                                        state="readonly", width=20)
        self._pl_select.pack(side="left", padx=4)

        tk.Button(toolbar, text="Assign",
                  command=self._pl_assign_selected,
                  bg="#0d6efd", fg="white", font=("Arial", 9),
                  relief="flat", padx=8).pack(side="left", padx=4)

        self._mute_btn = tk.Button(toolbar, text="Mute",
                                    command=self._toggle_mute,
                                    bg="#c0392b", fg="white", font=("Arial", 9),
                                    relief="flat", padx=8)
        self._mute_btn.pack(side="left", padx=4)

        self._pl_status = tk.StringVar(value="")
        tk.Label(toolbar, textvariable=self._pl_status,
                 font=("Arial", 9), fg="#198754", bg="#f8f9fa").pack(side="left", padx=10)

        self._pl_stream_status_var = tk.StringVar(value="")
        tk.Label(f, textvariable=self._pl_stream_status_var,
                 font=("Arial", 9), fg="#555",
                 bg="#f8f9fa", pady=3, padx=10,
                 anchor="w").pack(fill="x", padx=15)

        # Now playing
        self._pl_now_var = tk.StringVar(value="")
        tk.Label(f, textvariable=self._pl_now_var,
                 font=("Arial", 9), fg="#0c5460",
                 bg="#d1ecf1", pady=5, padx=10,
                 anchor="w").pack(fill="x", padx=15, pady=(0, 5))

        # Slot editor frame
        self._pl_editor_frame = tk.Frame(f, bg="#f8f9fa")
        self._pl_editor_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self._pl_data = None
        self._pl_current_file = "normal.json"
        self._pl_current_slots = []
        self._pl_muted = False
        self._pl_current_stream = "stream_8000"
        self._pl_streams = []

    def _on_stream_select(self, event=None):
        """Called when user changes stream selection."""
        selected = self._pl_stream_var.get()
        # Extract stream_id from display string e.g. "All Florida (:8000) [stream_8000]"
        for s in self._pl_streams:
            label = f"{s['label']} (:{s['port']})"
            if label in selected or s['id'] in selected:
                self._pl_current_stream = s["id"]
                active = s["active"]
                muted  = s["muted"]
                # Update mute button
                if muted:
                    self._mute_btn.config(text="Unmute", bg="#198754")
                else:
                    self._mute_btn.config(text="Mute", bg="#c0392b")
                self._pl_muted = muted
                # Update stream status
                status = "MUTED" if muted else "LIVE"
                self._pl_stream_status_var.set(
                    f"Stream: {s['label']} | Status: {status} | Active playlist: {active}")
                # Update playlist selector
                self._pl_select_var.set(active)
                self._pl_current_file = active
                available = self._pl_data.get("available", []) if self._pl_data else []
                pl = next((p for p in available if p["file"] == active), None)
                if pl:
                    self._pl_current_slots = list(pl.get("slots", []))
                    self._render_slot_editor()
                break

    def _populate_playlist(self, data):
        self._pl_data = data
        available  = data.get("available", [])
        streams    = data.get("streams", [])
        now_pl     = data.get("now_playing")
        self._pl_streams = streams

        # Populate stream selector
        if streams:
            stream_labels = [f"{s['label']} (:{s['port']})" for s in streams]
            self._pl_stream_select["values"] = stream_labels
            # Keep current selection or default to first
            current_label = next(
                (f"{s['label']} (:{s['port']})" for s in streams
                 if s["id"] == self._pl_current_stream), stream_labels[0])
            self._pl_stream_var.set(current_label)

            # Get active stream info
            active_stream = next((s for s in streams if s["id"] == self._pl_current_stream),
                                  streams[0])
            active   = active_stream["active"]
            muted    = active_stream["muted"]
            self._pl_muted = muted

            # Update mute button
            if muted:
                self._mute_btn.config(text="Unmute", bg="#198754")
            else:
                self._mute_btn.config(text="Mute", bg="#c0392b")

            # Update stream status bar
            status = "MUTED" if muted else "LIVE"
            self._pl_stream_status_var.set(
                f"Stream: {active_stream['label']} | Status: {status} | Active playlist: {active}")
        else:
            active = data.get("active", "normal.json")

        # Now playing
        if now_pl:
            self._pl_now_var.set(
                f"Now Playing: {now_pl.get('title','—')}  |  Category: {now_pl.get('category','')}")
        else:
            self._pl_now_var.set("Nothing currently playing")

        # Playlist combobox
        files = [p["file"] for p in available]
        self._pl_select["values"] = files
        self._pl_select_var.set(active)
        self._pl_current_file = active

        # Load slots for active playlist
        pl = next((p for p in available if p["file"] == active), None)
        if pl:
            self._pl_current_slots = list(pl.get("slots", []))
            self._render_slot_editor()

    def _render_slot_editor(self):
        for w in self._pl_editor_frame.winfo_children():
            w.destroy()

        # Header
        hdr = tk.Frame(self._pl_editor_frame, bg="white",
                       relief="solid", bd=1)
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text=f"{self._pl_current_file}  —  {len(self._pl_current_slots)} slots",
                 font=("Arial", 10, "bold"), bg="white",
                 padx=12, pady=6).pack(side="left")
        tk.Button(hdr, text="💾 Save Changes",
                  command=self._pl_save_slots,
                  bg="#0d6efd", fg="white", font=("Arial", 9),
                  relief="flat", padx=10).pack(side="right", padx=8, pady=4)
        self._pl_save_status = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._pl_save_status,
                 font=("Arial", 9), fg="#198754", bg="white").pack(side="right")

        # Table
        cols = ("#", "Label", "Category", "Top of Hour", "Skip if Empty")
        tree_frame = tk.Frame(self._pl_editor_frame, bg="#f8f9fa")
        tree_frame.pack(fill="both", expand=True)

        self._slot_tree = ttk.Treeview(tree_frame, columns=cols,
                                        show="headings", selectmode="browse")
        widths = {"#": 35, "Label": 180, "Category": 180,
                  "Top of Hour": 100, "Skip if Empty": 110}
        for c in cols:
            self._slot_tree.heading(c, text=c)
            self._slot_tree.column(c, width=widths.get(c, 100), anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self._slot_tree.yview)
        self._slot_tree.configure(yscrollcommand=vsb.set)
        self._slot_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Double-click to edit a slot inline
        self._slot_tree.bind("<Double-1>", self._pl_edit_slot_dialog)

        self._refresh_slot_tree()

        # Add slot form
        add_frame = tk.LabelFrame(self._pl_editor_frame,
                                   text="  Add New Slot  ",
                                   font=("Arial", 9, "bold"),
                                   bg="white", relief="solid", bd=1,
                                   padx=10, pady=8)
        add_frame.pack(fill="x", pady=(6, 0))

        row = tk.Frame(add_frame, bg="white")
        row.pack(fill="x")

        tk.Label(row, text="Label:", font=("Arial", 9),
                 bg="white").pack(side="left")
        self._new_label = tk.Entry(row, font=("Arial", 9), width=20,
                                    relief="solid", bd=1)
        self._new_label.pack(side="left", padx=6)

        tk.Label(row, text="Category:", font=("Arial", 9),
                 bg="white").pack(side="left")
        self._new_cat = ttk.Combobox(row, values=[
            "top_of_hour","priority_1","educational","airport_weather",
            "weather","sweepers","traffic","generated_wav_files",
            "imaging","jingles","station_ids","other"
        ], state="readonly", width=18)
        self._new_cat.set("weather")
        self._new_cat.pack(side="left", padx=6)

        self._new_toh  = tk.BooleanVar()
        self._new_skip = tk.BooleanVar()
        tk.Checkbutton(row, text="Top of Hour",
                       variable=self._new_toh, bg="white",
                       font=("Arial", 9)).pack(side="left", padx=4)
        tk.Checkbutton(row, text="Skip if Empty",
                       variable=self._new_skip, bg="white",
                       font=("Arial", 9)).pack(side="left", padx=4)
        tk.Button(row, text="+ Add Slot",
                  command=self._pl_add_slot,
                  bg="#198754", fg="white", font=("Arial", 9),
                  relief="flat", padx=8).pack(side="left", padx=6)

        # Action buttons row
        btn_row = tk.Frame(self._pl_editor_frame, bg="#f8f9fa", pady=4)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="▲ Move Up",
                  command=lambda: self._pl_move(-1),
                  bg="#6c757d", fg="white", font=("Arial", 9),
                  relief="flat", padx=8).pack(side="left", padx=3)
        tk.Button(btn_row, text="▼ Move Down",
                  command=lambda: self._pl_move(1),
                  bg="#6c757d", fg="white", font=("Arial", 9),
                  relief="flat", padx=8).pack(side="left", padx=3)
        tk.Button(btn_row, text="✕ Remove Selected",
                  command=self._pl_remove_slot,
                  bg="#c0392b", fg="white", font=("Arial", 9),
                  relief="flat", padx=8).pack(side="left", padx=3)

    def _refresh_slot_tree(self):
        self._slot_tree.delete(*self._slot_tree.get_children())
        for i, s in enumerate(self._pl_current_slots):
            self._slot_tree.insert("", "end", iid=str(i), values=(
                i + 1,
                s.get("label", ""),
                s.get("category", ""),
                "●" if s.get("top_of_hour") else "",
                "●" if s.get("skip_if_empty") else "",
            ))

    def _pl_edit_slot_dialog(self, event=None):
        """Open a dialog to edit the selected slot."""
        idx = self._pl_selected_idx()
        if idx is None:
            return
        slot = self._pl_current_slots[idx]

        win = tk.Toplevel(self)
        win.title(f"Edit Slot {idx + 1}")
        win.geometry("420x280")
        win.configure(bg="white")
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text=f"Edit Slot {idx + 1}",
                 font=("Arial", 12, "bold"), bg="white").pack(
            anchor="w", padx=15, pady=(15, 0))

        form = tk.Frame(win, bg="white", padx=15, pady=10)
        form.pack(fill="both", expand=True)

        # Label
        tk.Label(form, text="Label:", font=("Arial", 9),
                 bg="white", anchor="w").grid(row=0, column=0, sticky="e", pady=6, padx=(0,8))
        label_var = tk.StringVar(value=slot.get("label", ""))
        tk.Entry(form, textvariable=label_var, font=("Arial", 10),
                 width=30, relief="solid", bd=1).grid(row=0, column=1, sticky="w")

        # Category
        tk.Label(form, text="Category:", font=("Arial", 9),
                 bg="white", anchor="w").grid(row=1, column=0, sticky="e", pady=6, padx=(0,8))
        cat_var = tk.StringVar(value=slot.get("category", ""))
        cat_cb = ttk.Combobox(form, textvariable=cat_var, width=28,
                               values=[
                                   "top_of_hour","priority_1","educational","airport_weather",
                                   "weather","sweepers","traffic","generated_wav_files",
                                   "imaging","jingles","station_ids","other"
                               ], state="readonly")
        cat_cb.grid(row=1, column=1, sticky="w")

        # Top of Hour
        toh_var = tk.BooleanVar(value=bool(slot.get("top_of_hour")))
        tk.Checkbutton(form, text="Top of Hour",
                       variable=toh_var, bg="white",
                       font=("Arial", 9)).grid(row=2, column=1, sticky="w", pady=4)

        # Skip if Empty
        skip_var = tk.BooleanVar(value=bool(slot.get("skip_if_empty")))
        tk.Checkbutton(form, text="Skip if Empty",
                       variable=skip_var, bg="white",
                       font=("Arial", 9)).grid(row=3, column=1, sticky="w", pady=4)

        # Buttons
        btn_row = tk.Frame(win, bg="white")
        btn_row.pack(fill="x", padx=15, pady=10)

        def save():
            self._pl_current_slots[idx] = {
                "label":         label_var.get().strip(),
                "category":      cat_var.get(),
                "top_of_hour":   toh_var.get(),
                "skip_if_empty": skip_var.get(),
            }
            self._refresh_slot_tree()
            win.destroy()

        tk.Button(btn_row, text="Cancel", command=win.destroy,
                  bg="#6c757d", fg="white", font=("Arial", 9),
                  relief="flat", padx=10).pack(side="right", padx=(6, 0))
        tk.Button(btn_row, text="Save", command=save,
                  bg="#0d6efd", fg="white", font=("Arial", 9),
                  relief="flat", padx=10).pack(side="right")

    def _pl_selected_idx(self):
        sel = self._slot_tree.selection()
        return int(sel[0]) if sel else None

    def _pl_add_slot(self):
        label = self._new_label.get().strip()
        if not label:
            messagebox.showwarning("Required", "Please enter a label.")
            return
        self._pl_current_slots.append({
            "label":         label,
            "category":      self._new_cat.get(),
            "top_of_hour":   self._new_toh.get(),
            "skip_if_empty": self._new_skip.get(),
        })
        self._new_label.delete(0, "end")
        self._refresh_slot_tree()

    def _pl_move(self, direction):
        idx = self._pl_selected_idx()
        if idx is None:
            messagebox.showinfo("No selection", "Select a slot first.")
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._pl_current_slots):
            return
        s = self._pl_current_slots
        s[idx], s[new_idx] = s[new_idx], s[idx]
        self._refresh_slot_tree()
        self._slot_tree.selection_set(str(new_idx))

    def _pl_remove_slot(self):
        idx = self._pl_selected_idx()
        if idx is None:
            messagebox.showinfo("No selection", "Select a slot first.")
            return
        label = self._pl_current_slots[idx].get("label", "")
        if messagebox.askyesno("Confirm", f"Remove slot '{label}'?"):
            self._pl_current_slots.pop(idx)
            self._refresh_slot_tree()

    def _pl_save_slots(self):
        self._pl_save_status.set("Saving…")
        def task():
            res = api_post(f"/api/playlist/{self._pl_current_file}/slots",
                           {"slots": self._pl_current_slots})
            self.after(0, lambda: self._pl_save_status.set(
                res.get("message", res.get("_error", ""))))
        threading.Thread(target=task, daemon=True).start()

    def _pl_assign_selected(self):
        fname = self._pl_select_var.get()
        if not fname:
            return
        stream_id = self._pl_current_stream
        def task():
            res = api_post("/api/playlist/assign",
                           {"stream_id": stream_id, "file": fname})
            self.after(0, lambda: self._pl_status.set(
                res.get("message", res.get("_error", ""))))
        threading.Thread(target=task, daemon=True).start()

    def _toggle_mute(self):
        mute = not self._pl_muted
        stream_id = self._pl_current_stream
        def task():
            res = api_post("/api/playlist/mute/toggle", {"stream_id": stream_id})
            def update():
                self._pl_status.set(res.get("message", res.get("_error", "")))
                if res.get("ok"):
                    self._pl_muted = res.get("muted", mute)
                    if self._pl_muted:
                        self._mute_btn.config(text="Unmute", bg="#198754")
                    else:
                        self._mute_btn.config(text="Mute", bg="#c0392b")
            self.after(0, update)
        threading.Thread(target=task, daemon=True).start()

    # ══════════════════════════════════════════ ICECAST TAB
    def _build_icecast_tab(self):
        f = self.tab_icecast

        tk.Label(f, text="Icecast Stream Status",
                 font=("Arial", 12, "bold"),
                 bg="#f8f9fa").pack(anchor="w", padx=20, pady=(15, 5))

        self._ice_frame = tk.Frame(f, bg="#f8f9fa")
        self._ice_frame.pack(fill="both", expand=True, padx=20)

    def _populate_icecast(self, streams):
        for w in self._ice_frame.winfo_children():
            w.destroy()

        cols = ("Label", "Port", "Mount", "Status", "Listeners", "Bitrate", "Format")
        tree = ttk.Treeview(self._ice_frame, columns=cols,
                             show="headings", height=len(streams)+1)
        widths = {"Label": 150, "Port": 70, "Mount": 160,
                  "Status": 90, "Listeners": 90,
                  "Bitrate": 80, "Format": 120}
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=widths.get(c, 100), anchor="w")

        for s in streams:
            status = "🟢 LIVE" if s.get("live") else "🔴 Offline"
            tree.insert("", "end", values=(
                s.get("label", ""),
                s.get("port", ""),
                s.get("mount", ""),
                status,
                s.get("listeners", 0),
                s.get("bitrate") or "—",
                s.get("format") or "—",
            ))

        vsb = ttk.Scrollbar(self._ice_frame, orient="vertical",
                             command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # ══════════════════════════════════════════ ALERTS & DATA TAB
    def _build_data_tab(self):
        f = self.tab_data

        canvas = tk.Canvas(f, bg="#f8f9fa", highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        self._data_inner = tk.Frame(canvas, bg="#f8f9fa")
        canvas.create_window((0, 0), window=self._data_inner, anchor="nw")
        self._data_inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

    def _make_table(self, parent, title, columns, widths, rows, row_colors=None):
        card = tk.LabelFrame(parent, text=f"  {title}  ",
                              font=("Arial", 10, "bold"),
                              bg="white", relief="solid", bd=1)
        card.pack(fill="x", padx=20, pady=(10, 0))

        tree = ttk.Treeview(card, columns=columns,
                             show="headings", height=min(len(rows)+1, 12))
        for c, w in zip(columns, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")

        style = ttk.Style()
        style.configure("Treeview", font=("Arial", 9), rowheight=24)
        style.configure("Treeview.Heading", font=("Arial", 9, "bold"))

        if row_colors:
            for tag, color in row_colors.items():
                tree.tag_configure(tag, background=color)

        for row in rows:
            tags = ()
            if row_colors and len(row) > len(columns):
                tags = (row[-1],)
                row  = row[:-1]
            tree.insert("", "end", values=row, tags=tags)

        vsb = ttk.Scrollbar(card, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return tree

    def _populate_data(self, data):
        for w in self._data_inner.winfo_children():
            w.destroy()

        # NWS Alerts
        alerts = data.get("alerts", [])
        sev_colors = {
            "Extreme":  "#f8d7da",
            "Severe":   "#fff3cd",
            "Moderate": "#fff9e6",
        }
        alert_rows = []
        for a in alerts:
            wav = "▶ WAV" if a.get("alert_id") else "Pending"
            alert_rows.append((
                a.get("event", "—"),
                (a.get("headline") or "—")[:60],
                a.get("severity", "—"),
                (a.get("area_desc") or "—")[:50],
                a.get("sender", "—"),
                a.get("sent", "—"),
                wav,
                a.get("severity", ""),
            ))
        self._make_table(
            self._data_inner,
            f"NWS Alerts ({len(alerts)} most recent)",
            ("Event", "Headline", "Severity", "Areas", "Sender", "Sent", "WAV"),
            [140, 250, 90, 200, 100, 130, 70],
            alert_rows,
            row_colors={**{k: v for k, v in sev_colors.items()}}
        )

        # Airport METAR
        airports = data.get("airports", [])
        flt_colors = {"VFR": "#d4edda", "MVFR": "#cce5ff",
                      "IFR": "#f8d7da", "LIFR": "#e2d9f3"}
        ap_rows = [
            (a["icaoId"], a.get("name","")[:25],
             a.get("fltCat",""), f"{a.get('temp_f','')}°F",
             f"{a.get('dewp_f','')}°F",
             f"{a.get('wdir','')}° @ {a.get('wspd','')}kts",
             a.get("visib",""), a.get("obsTime",""),
             a.get("fltCat",""))
            for a in airports
        ]
        self._make_table(
            self._data_inner,
            "Airport METAR Observations",
            ("ICAO", "Name", "Cat", "Temp", "Dewpoint", "Wind", "Vis", "Time"),
            [60, 160, 55, 70, 80, 130, 55, 110],
            ap_rows,
            row_colors=flt_colors
        )

        # Traffic
        traffic = data.get("traffic", [])
        if traffic:
            tr_rows = [
                (t.get("type",""), t.get("road",""),
                 t.get("location","")[:40], t.get("county",""),
                 t.get("severity",""), t.get("last_updated",""))
                for t in traffic
            ]
            self._make_table(
                self._data_inner, "FL Traffic",
                ("Type","Road","Location","County","Severity","Updated"),
                [100,80,200,120,80,130],
                tr_rows
            )

        # Feed Status
        feeds = data.get("feeds", [])
        feed_colors = {"OK": "#d4edda", "STALE": "#fff3cd", "ERROR": "#f8d7da"}
        feed_rows = [
            (f["filename"], f.get("last_success","—"),
             f"{f.get('age_min','—')} min",
             f"{f.get('file_size_kb','—')} KB",
             f["status"], f["row_class"])
            for f in feeds
        ]
        self._make_table(
            self._data_inner, "RSS Feed Status",
            ("Feed File","Last Success","Age","Size","Status"),
            [200,160,80,80,80],
            feed_rows,
            row_colors=feed_colors
        )

    # ══════════════════════════════════════════ FEEDBACK
    def _show_feedback(self):
        win = tk.Toplevel(self)
        win.title("Send Feedback")
        win.geometry("420x300")
        win.configure(bg="white")
        win.resizable(False, False)

        tk.Label(win, text="Send Feedback",
                 font=("Arial", 12, "bold"), bg="white").pack(
            anchor="w", padx=15, pady=(15, 0))

        tk.Label(win, text="Name (optional)",
                 font=("Arial", 9), bg="white").pack(
            anchor="w", padx=15, pady=(10, 2))
        name_entry = tk.Entry(win, font=("Arial", 10),
                               relief="solid", bd=1, width=45)
        name_entry.pack(padx=15)

        tk.Label(win, text="Message *",
                 font=("Arial", 9), bg="white").pack(
            anchor="w", padx=15, pady=(10, 2))
        msg_text = tk.Text(win, font=("Arial", 10),
                            height=5, relief="solid", bd=1, width=45)
        msg_text.pack(padx=15)

        def submit():
            msg = msg_text.get("1.0", "end").strip()
            if not msg:
                messagebox.showwarning("Required", "Message is required.", parent=win)
                return
            try:
                requests.post(f"{API}/feedback", data={
                    "name":    name_entry.get().strip(),
                    "message": msg
                }, timeout=10)
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        btn_row = tk.Frame(win, bg="white")
        btn_row.pack(fill="x", padx=15, pady=10)
        tk.Button(btn_row, text="Cancel", command=win.destroy,
                  bg="#6c757d", fg="white", font=("Arial", 9),
                  relief="flat", padx=10).pack(side="right", padx=(6, 0))
        tk.Button(btn_row, text="Submit", command=submit,
                  bg="#0d6efd", fg="white", font=("Arial", 9),
                  relief="flat", padx=10).pack(side="right")

    # ══════════════════════════════════════════ REFRESH LOOP
    def _refresh(self):
        def task():
            smtp     = api_get("/api/smtp")
            streams  = api_get("/api/streams")
            weather  = api_get("/api/weather")
            playlist = api_get("/api/playlist")
            icecast  = api_get("/api/icecast")
            data_tab = api_get("/api/data-tab")
            self.after(0, lambda: self._apply_refresh(
                smtp, streams, weather, playlist, icecast, data_tab))
        threading.Thread(target=task, daemon=True).start()

    def _apply_refresh(self, smtp, streams, weather, playlist, icecast, data_tab):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        self.updated_var.set(f"Updated {now}")

        if isinstance(smtp, dict) and not smtp.get("_error") and isinstance(streams, list):
            self._populate_config(smtp, streams)
        if isinstance(weather, list) and weather:
            self._populate_weather(weather)
        if isinstance(playlist, dict) and not playlist.get("_error"):
            self._populate_playlist(playlist)
        if isinstance(icecast, list) and icecast:
            self._populate_icecast(icecast)
        if isinstance(data_tab, dict) and not data_tab.get("_error"):
            self._populate_data(data_tab)

    def _schedule_refresh(self):
        self._refresh()
        self.after(REFRESH_SEC * 1000, self._schedule_refresh)


    # ══════════════════════════════════════════ AI BROADCAST TAB
    def _build_ai_tab(self):
        f = self.tab_ai
        f.configure(style="TFrame")

        tk.Label(f, text="AI Broadcast Tools",
                 font=("Arial", 13, "bold"),
                 bg="#f8f9fa").pack(anchor="w", padx=20, pady=(15, 2))
        tk.Label(f, text="Powered by UF LiteLLM  •  " + API + "/api/ai/…",
                 font=("Arial", 8), fg="#6c757d",
                 bg="#f8f9fa").pack(anchor="w", padx=20, pady=(0, 10))

        # ── Rewrite Alert ──────────────────────────────────────────────
        rw = tk.LabelFrame(f, text="  Rewrite NWS Alert  ",
                           font=("Arial", 10, "bold"),
                           bg="white", relief="solid", bd=1)
        rw.pack(fill="x", padx=20, pady=(0, 12))

        for label, attr, default in [
            ("Headline:",    "_ai_rw_headline", ""),
            ("Area:",        "_ai_rw_area",     ""),
            ("Description:", "_ai_rw_desc",     ""),
        ]:
            row = tk.Frame(rw, bg="white")
            row.pack(fill="x", padx=10, pady=3)
            tk.Label(row, text=label, font=("Arial", 9), bg="white",
                     width=12, anchor="e").pack(side="left")
            v = tk.StringVar()
            setattr(self, attr, v)
            tk.Entry(row, textvariable=v, font=("Arial", 10),
                     relief="solid", bd=1).pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._ai_rw_status = tk.StringVar(value="")
        tk.Label(rw, textvariable=self._ai_rw_status,
                 font=("Arial", 9), fg="#0077aa", bg="white").pack(anchor="w", padx=10)

        self._ai_rw_out = tk.Text(rw, height=5, font=("Arial", 10),
                                  wrap="word", relief="solid", bd=1, bg="#f8f9fa")
        self._ai_rw_out.pack(fill="x", padx=10, pady=(0, 6))

        tk.Button(rw, text="Rewrite Alert →", command=self._ai_rewrite_alert,
                  bg="#0d6efd", fg="white", font=("Arial", 9),
                  relief="flat", padx=12, pady=4).pack(anchor="e", padx=10, pady=(0, 8))

        # ── Generate Broadcast ─────────────────────────────────────────
        bc = tk.LabelFrame(f, text="  Generate Full Broadcast Script  ",
                           font=("Arial", 10, "bold"),
                           bg="white", relief="solid", bd=1)
        bc.pack(fill="x", padx=20, pady=(0, 12))

        tk.Label(bc, text="Pulls live alerts + METAR observations from the dashboard.",
                 font=("Arial", 9), fg="#6c757d", bg="white").pack(anchor="w", padx=10, pady=(6, 0))

        self._ai_bc_status = tk.StringVar(value="")
        tk.Label(bc, textvariable=self._ai_bc_status,
                 font=("Arial", 9), fg="#0077aa", bg="white").pack(anchor="w", padx=10)

        self._ai_bc_out = tk.Text(bc, height=8, font=("Arial", 10),
                                  wrap="word", relief="solid", bd=1, bg="#f8f9fa")
        self._ai_bc_out.pack(fill="x", padx=10, pady=(0, 6))

        tk.Button(bc, text="Generate Broadcast →", command=self._ai_gen_broadcast,
                  bg="#198754", fg="white", font=("Arial", 9),
                  relief="flat", padx=12, pady=4).pack(anchor="e", padx=10, pady=(0, 8))

    def _ai_rewrite_alert(self):
        self._ai_rw_status.set("Calling LiteLLM…")
        self._ai_rw_out.delete("1.0", "end")
        payload = {
            "headline":    self._ai_rw_headline.get().strip(),
            "area":        self._ai_rw_area.get().strip(),
            "description": self._ai_rw_desc.get().strip(),
        }
        def task():
            res = api_post("/api/ai/rewrite-alert", payload)
            def update():
                if res.get("ok"):
                    self._ai_rw_status.set("Done.")
                    self._ai_rw_out.insert("1.0", res.get("script", ""))
                else:
                    self._ai_rw_status.set(f"Error: {res.get('message', res.get('_error',''))}")
            self.after(0, update)
        threading.Thread(target=task, daemon=True).start()

    def _ai_gen_broadcast(self):
        self._ai_bc_status.set("Calling LiteLLM…")
        self._ai_bc_out.delete("1.0", "end")
        def task():
            res = api_post("/api/ai/broadcast", {})
            def update():
                if res.get("ok"):
                    self._ai_bc_status.set("Done.")
                    self._ai_bc_out.insert("1.0", res.get("script", ""))
                else:
                    self._ai_bc_status.set(f"Error: {res.get('message', res.get('_error',''))}")
            self.after(0, update)
        threading.Thread(target=task, daemon=True).start()


class LoginDialog(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FPREN Login")
        self.geometry("380x260")
        self.resizable(False, False)
        self.configure(bg="#111")
        self.result = False
        self._build()

    def _build(self):
        # Header
        tk.Frame(self, bg="#0077aa", height=4).pack(fill="x")
        hdr = tk.Frame(self, bg="#111", pady=20)
        hdr.pack(fill="x")
        tk.Label(hdr, text="FPREN Alerts Dashboard",
                 font=("Arial", 14, "bold"), fg="white", bg="#111").pack()
        tk.Label(hdr, text="Weather • Traffic • Alerts • Icecast",
                 font=("Arial", 9), fg="#aaa", bg="#111").pack()

        # Form
        form = tk.Frame(self, bg="white", padx=30, pady=20)
        form.pack(fill="both", expand=True)

        tk.Label(form, text="Username", font=("Arial", 9),
                 bg="white", anchor="w").pack(fill="x")
        self._user = tk.Entry(form, font=("Arial", 11),
                               relief="solid", bd=1)
        self._user.pack(fill="x", pady=(2, 10))
        self._user.insert(0, "admin")

        tk.Label(form, text="Password", font=("Arial", 9),
                 bg="white", anchor="w").pack(fill="x")
        self._pass = tk.Entry(form, font=("Arial", 11),
                               relief="solid", bd=1, show="*")
        self._pass.pack(fill="x", pady=(2, 10))
        self._pass.bind("<Return>", lambda e: self._login())

        self._err = tk.StringVar(value="")
        tk.Label(form, textvariable=self._err,
                 fg="#c0392b", bg="white", font=("Arial", 9)).pack()

        tk.Button(form, text="Sign In",
                  command=self._login,
                  bg="#0077aa", fg="white",
                  font=("Arial", 10), relief="flat",
                  pady=6).pack(fill="x", pady=(6, 0))

        self._user.focus()

    def _login(self):
        username = self._user.get().strip()
        password = self._pass.get()
        self._err.set("Signing in…")
        self.update()
        if api_login(username, password):
            self.result = True
            self.destroy()
        else:
            self._err.set("Invalid username or password")
            self._pass.delete(0, "end")


if __name__ == "__main__":
    # Show login first
    login = LoginDialog()
    login.mainloop()
    if login.result:
        app = FPRENApp()
        app.mainloop()
