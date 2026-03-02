#!/bin/bash

UNIT_NAME="$1"
HOSTNAME="$(hostname)"
DATE="$(date)"

LOG_FILE="/home/lh_admin/weather_rss/weather_service.log"

DOC_DIR="/home/lh_admin/weather_rss/docs"
PDF1="$DOC_DIR/tp.pdf"
PDF2="$DOC_DIR/Workflow.pdf"
PDF3="$DOC_DIR/schematic.pdf"

TO_EMAIL="lbornacelli@gmail.com"
SUBJECT="🚨 Weather RSS Service FAILED: $UNIT_NAME on $HOSTNAME"

BODY=$(cat <<EOF
The systemd service below has FAILED.

Service: $UNIT_NAME
Host: $HOSTNAME
Time: $DATE

--- Last 20 lines of weather_service.log ---
$(tail -n 20 "$LOG_FILE")

Attached documents:
• Test Point Explanation
• System Workflow
• System Schematic
EOF
)

echo "$BODY" | mail \
  -s "$SUBJECT" \
  -A "$PDF1" \
  -A "$PDF2" \
  -A "$PDF3" \
  "$TO_EMAIL"
