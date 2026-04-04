#!/bin/bash
# Install and enable the FPREN 2PM comprehensive report timer
set -e
SYSTEMD_DIR=/home/ufuser/Fpren-main/systemd

sudo cp "$SYSTEMD_DIR/fpren-comprehensive-2pm.service" /etc/systemd/system/
sudo cp "$SYSTEMD_DIR/fpren-comprehensive-2pm.timer"   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fpren-comprehensive-2pm.timer
sudo systemctl start  fpren-comprehensive-2pm.timer
echo "Timer status:"
systemctl status fpren-comprehensive-2pm.timer --no-pager
echo ""
echo "Next run:"
systemctl list-timers fpren-comprehensive-2pm.timer --no-pager
