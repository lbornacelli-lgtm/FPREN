#!/home/ufuser/Fpren-main/venv/bin/python3
"""Query fpren_snmp_status and print one field. Usage: snmp_query.py <field>"""
import sys
try:
    from pymongo import MongoClient
    c = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=3000)
    d = c.weather_rss.fpren_snmp_status.find_one({"_id":"singleton"}) or {}
    c.close()
    field = sys.argv[1] if len(sys.argv) > 1 else "system_health"
    print(d.get(field, "UNKNOWN"))
except Exception as e:
    print(f"ERROR: {e}")
