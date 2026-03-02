import tkinter as tk
from tkinter import ttk
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
import subprocess
import datetime
import os
import time

REFRESH_INTERVAL = 60000
WATCHDOG_FILE = "/run/weather_rss.watchdog"


class WeatherControlCenter:
    def __init__(self, root):
        self.root = root
        self.root.title("Weather RSS FULL CONTROL CENTER")
        self.root.geometry("1100x750")
        self.root.configure(bg="#111111")

        self.create_layout()
        self.refresh()

    # ---------------------------
    # Layout
    # ---------------------------
    def create_layout(self):
        titl
