import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
from pathlib import Path
from datetime import datetime
import time

SERVICE = "weather-rss.service"
FEEDS_DIR = Path("/weather_rss/feeds")
REFRESH_SEC = 5
RESTART_THRESHOLD = 5   # restarts in last 10 minutes

def run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        return e.output

class WeatherRSSMonitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Weather RSS Service Monitor")
        self.geometry("1000x600")

        self.status_var = tk.StringVar()
        self.uptime_var = tk.StringVar()

        self.create_ui()
        self.refresh_all()

    # ---------------- UI ---------------- #

    def create_ui(self):
        ttk.Label(self, text="Weather RSS Service Monitor",
                  font=("Arial", 18, "bold")).pack(pady=10)

        top = ttk.Frame(self)
        top.pack(pady=5)

        ttk.Label(top, text="Service Status:").pack(side="left")
        self.status_label = ttk.Label(top, textvariable=self.status_var,
                                      font=("Arial", 12, "bold"))
        self.status_label.pack(side="left", padx=5)

        ttk.Label(top, text="Uptime:").pack(side="left", padx=(20, 0))
        ttk.Label(top, textvariable=self.uptime_var).pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(pady=10)

        ttk.Button(btns, text="Start", command=self.start).pack(side="left", padx=5)
        ttk.Button(btns, text="Stop", command=self.stop).pack(side="left", padx=5)
        ttk.Button(btns, text="Restart", command=self.restart).pack(side="left", padx=5)
        ttk.Button(btns, text="Refresh", command=self.refresh_all).pack(side="left", padx=5)

        # Feed health table
        ttk.Label(self, text="Feed Health", font=("Arial", 14)).pack(pady=(20, 5))

        cols = ("feed", "last_updated", "age_min", "size_kb", "status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=8)

        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, anchor="center")

        self.tree.pack(fill="x", padx=10)

        # Logs
        ttk.Label(self, text="Recent Service Logs", font=("Arial", 14)).pack(pady=(20, 5))
        self.log_box = tk.Text(self, height=10, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---------------- Service Control ---------------- #

    def start(self):
        run(["systemctl", "start", SERVICE])
        self.refresh_all()

    def stop(self):
        run(["systemctl", "stop", SERVICE])
        self.refresh_all()

    def restart(self):
        run(["systemctl", "restart", SERVICE])
        self.refresh_all()

    # ---------------- Monitoring ---------------- #

    def refresh_all(self):
        self.refresh_service_status()
        self.refresh_uptime()
        self.refresh_feed_health()
        self.refresh_logs()
        self.after(REFRESH_SEC * 1000, self.refresh_all)

    def refresh_service_status(self):
        status = run(["systemctl", "is-active", SERVICE]).strip()
        self.status_var.set(status.upper())

        color = "green" if status == "active" else "red"
        if status == "activating":
            color = "orange"

        self.status_label.config(foreground=color)

        if status == "failed":
            self.alert("Service FAILED")

        self.detect_restart_loop()

    def refresh_uptime(self):
        out = run(["systemctl", "show", SERVICE, "-p", "ActiveEnterTimestamp"])
        if "=" in out:
            ts = out.strip().split("=", 1)[1]
            self.uptime_var.set(ts if ts else "N/A")

    def detect_restart_loop(self):
        logs = run([
            "journalctl", "-u", SERVICE,
            "--since", "10 min ago",
            "--grep", "Starting"
        ])
        restarts = logs.count("Starting")
        if restarts >= RESTART_THRESHOLD:
            self.alert(f"Restart loop detected ({restarts} restarts)")

    # ---------------- Feed Health ---------------- #

    def refresh_feed_health(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        now = time.time()

        for xml in FEEDS_DIR.glob("*.xml"):
            stat = xml.stat()
            age_min = round((now - stat.st_mtime) / 60, 1)
            size_kb = round(stat.st_size / 1024, 2)

            status = "OK"
            if age_min > 30:
                status = "STALE"
            if stat.st_size < 1:
                status = "EMPTY"

            self.tree.insert(
                "", "end",
                values=(
                    xml.name,
                    datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    age_min,
                    size_kb,
                    status
                )
            )

    # ---------------- Logs ---------------- #

    def refresh_logs(self):
        logs = run(["journalctl", "-u", SERVICE, "-n", "30", "--no-pager"])
        self.log_box.delete("1.0", tk.END)
        self.log_box.insert(tk.END, logs)

    # ---------------- Alerts ---------------- #

    def alert(self, msg):
        self.bell()
        messagebox.showwarning("Weather RSS Alert", msg)

# ---------------- Run ---------------- #

if __name__ == "__main__":
    WeatherRSSMonitor().mainloop()
