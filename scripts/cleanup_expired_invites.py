#!/home/ufuser/Fpren-main/venv/bin/python3
"""
FPREN Invite Expiry Cleanup
Deletes user accounts whose invite_token was never used (last_login is null)
and whose invite_expires timestamp has passed.

Run daily via fpren-invite-cleanup.timer.
Logs each deletion to user_audit_log collection.
"""

import sys
import datetime

try:
    from pymongo import MongoClient
except ImportError:
    print("ERROR: pymongo not installed", file=sys.stderr)
    sys.exit(1)

MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB  = "weather_rss"


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db     = client[MONGO_DB]
    now    = datetime.datetime.utcnow()

    # Find users with an unexpired invite token who have never logged in
    # and whose invite_expires is in the past
    expired = list(db.users.find({
        "invite_token":  {"$ne": None, "$exists": True},
        "last_login":    None,
        "invite_expires": {"$lt": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    }, {"username": 1, "email": 1, "invite_expires": 1, "_id": 1}))

    if not expired:
        print(f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}] No expired uninvited accounts found.")
        client.close()
        return

    deleted = []
    for u in expired:
        uname = u.get("username", "unknown")
        email = u.get("email", "")
        exp   = u.get("invite_expires", "")
        try:
            db.users.delete_one({"_id": u["_id"]})
            db.user_audit_log.insert_one({
                "action":       "user_deleted",
                "target_user":  uname,
                "performed_by": "system",
                "timestamp":    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "details":      f"Invite expired at {exp} — account never activated. Email: {email}"
            })
            deleted.append(uname)
            print(f"DELETED: {uname} ({email}) — invite expired {exp}")
        except Exception as e:
            print(f"ERROR deleting {uname}: {e}", file=sys.stderr)

    print(f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}] Cleanup complete: {len(deleted)} account(s) removed.")
    client.close()


if __name__ == "__main__":
    main()
