#!/bin/bash
LOGFILE=/home/lh_admin/weather_rss/logs/gui.log
echo "Starting Weather RSS GUI at $(date)" >> "$LOGFILE"

export DISPLAY=:0
export XAUTHORITY=/home/lh_admin/.Xauthority

/home/lh_admin/weather_rss/venv/bin/python /home/lh_admin/weather_rss/weather_rss_gui.py >> "$LOGFILE" 2>&1
