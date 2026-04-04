#!/bin/bash
# Install FPREN Census fetcher service and monthly refresh timer.
# Run ONCE manually first to seed the database, then the timer keeps it current.
set -e
SYSTEMD_DIR=/home/ufuser/Fpren-main/systemd
VENV=/home/ufuser/Fpren-main/venv

echo "=== FPREN Census Fetcher Install ==="

# 1. Check census_config.json has a real key
CFG=/home/ufuser/Fpren-main/weather_rss/config/census_config.json
KEY=$(python3 -c "import json; d=json.load(open('$CFG')); print(d.get('api_key',''))" 2>/dev/null || echo "")
if [[ -z "$KEY" || "$KEY" == "YOUR_CENSUS_API_KEY" ]]; then
  echo ""
  echo "ERROR: Set your Census API key in:"
  echo "  $CFG"
  echo "  (or export CENSUS_API_KEY=your-key-here)"
  echo ""
  echo "Get a free key at: https://api.census.gov/data/key_signup.html"
  exit 1
fi

# 2. Install systemd units
sudo cp "$SYSTEMD_DIR/beacon-census-fetcher.service" /etc/systemd/system/
sudo cp "$SYSTEMD_DIR/fpren-census-refresh.timer"    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fpren-census-refresh.timer

# 3. Run initial fetch now
echo "Running initial census data fetch (this takes ~10-15 seconds)..."
cd /home/ufuser/Fpren-main
source "$VENV/bin/activate"
python3 weather_rss/fl_census_fetcher.py

# 4. Start the monthly timer
sudo systemctl start fpren-census-refresh.timer

echo ""
echo "=== Done ==="
echo "Census data loaded into MongoDB weather_rss.fl_census"
echo "Monthly refresh timer active:"
systemctl list-timers fpren-census-refresh.timer --no-pager
