#!/usr/bin/env python3
"""
Backfill completed_at timestamps in data/stockings.json.

Behavior:
- For every user record in stockings.json:
  - For each buildable where "completed" is True but "completed_at" is missing,
    set "completed_at" to an ISO timestamp based on the stockings.json mtime,
    with a 1-second stagger per entry so ordering is deterministic.
- Writes the file back (overwrites).
"""

from pathlib import Path
import json
from datetime import datetime, timezone, timedelta
import sys

DATA_DIR = Path("data")
STOCKINGS_FILE = DATA_DIR / "stockings.json"

if not STOCKINGS_FILE.exists():
    print("stockings.json not found at", STOCKINGS_FILE)
    sys.exit(1)

mtime = STOCKINGS_FILE.stat().st_mtime
baseline = datetime.fromtimestamp(mtime, tz=timezone.utc)

with STOCKINGS_FILE.open("r", encoding="utf-8") as fh:
    try:
        data = json.load(fh)
    except Exception as e:
        print("Failed to load JSON:", e)
        sys.exit(1)

to_fill = []
for uid_str, rec in (data or {}).items():
    buildables = rec.get("buildables", {}) or {}
    for bkey, brec in buildables.items():
        if brec and brec.get("completed") and not brec.get("completed_at"):
            to_fill.append((uid_str, bkey))

if not to_fill:
    print("No missing completed_at entries found.")
    sys.exit(0)

print(f"Found {len(to_fill)} entries to backfill. Writing timestamps based on file mtime {baseline.isoformat()} (UTC).")

for i, (uid_str, bkey) in enumerate(sorted(to_fill)):
    ts = baseline - timedelta(seconds=(len(to_fill) - i))
    iso = ts.isoformat()
    try:
        data[uid_str]["buildables"][bkey]["completed_at"] = iso
    except Exception as e:
        print(f"Failed to set completed_at for {uid_str}/{bkey}: {e}")

try:
    with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print("Backfill complete — stockings.json updated.")
except Exception as e:
    print("Failed to write stockings.json:", e)
    sys.exit(1)