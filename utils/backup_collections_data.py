#!/usr/bin/env python3
"""
Safe backup script for collected_pieces.json.

- Uses ALICE_BACKUP_DIR env var or defaults to ../data/backups (relative to repo).
- Avoids writing to Windows-style paths on Linux.
- Writes a run marker so you can confirm which host executed the backup.
"""
import os
import shutil
import json
from datetime import datetime
from pathlib import Path
import re
import sys

# --- Configuration / safe defaults ---
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = (REPO_ROOT / "data").resolve()
DATA_FILE = "collected_pieces.json"

# prefer environment variable override
env_dir = os.environ.get("ALICE_BACKUP_DIR")
if env_dir:
    candidate = Path(env_dir)
else:
    candidate = (DATA_DIR / "backups")

# Detect obvious Windows-drive-style paths and refuse to use them on non-Windows hosts
_win_drive_re = re.compile(r"^[A-Za-z]:\\")
if os.name != "nt" and _win_drive_re.match(str(candidate)):
    # fallback to repo data/backups
    fallback = (DATA_DIR / "backups").resolve()
    print(f"WARNING: configured backup dir looks like a Windows path ({candidate}); using fallback {fallback}", file=sys.stderr)
    BACKUP_DIR = fallback
else:
    BACKUP_DIR = candidate.resolve()

# Ensure the backup directory exists
try:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"ERROR: could not create backup dir {BACKUP_DIR}: {e}", file=sys.stderr)
    # fallback to a local backups dir inside repo
    BACKUP_DIR = (DATA_DIR / "backups").resolve()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# marker log (attempt /var/log first, fall back to backups dir)
LOG_PATH = Path("/var/log/backup-run.log")
if not os.access(LOG_PATH.parent, os.W_OK):
    LOG_PATH = BACKUP_DIR / "backup-run.log"

def log_marker(msg: str):
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.utcnow().isoformat()} {msg}\n")
    except Exception:
        # last resort: print to stderr
        print(f"{datetime.utcnow().isoformat()} {msg}", file=sys.stderr)

# --- Run ---
src = (DATA_DIR / DATA_FILE)
if not src.exists():
    log_marker(f"ABORT: source file not found: {src}")
    sys.exit(1)

timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup_file = f"collected_pieces_{timestamp}.json"
hourly_backup = BACKUP_DIR / backup_file

try:
    shutil.copy2(src, hourly_backup)
    log_marker(f"BACKUP_OK: wrote {hourly_backup} (src={src}) host={os.uname().nodename} user={os.getlogin()}")
except Exception as e:
    log_marker(f"BACKUP_FAIL: could not copy {src} -> {hourly_backup}: {e}")
    sys.exit(1)

# Rotation logic (preserve one per day + interval snapshots)
def parse_dt(fn: str):
    try:
        ts = fn.replace("collected_pieces_", "").replace(".json", "")
        return datetime.strptime(ts, "%Y%m%d-%H%M%S")
    except Exception:
        return None

all_backups = [f for f in os.listdir(BACKUP_DIR) if f.startswith("collected_pieces_") and f.endswith(".json")]
backups = []
for f in all_backups:
    dt = parse_dt(f)
    if dt:
        backups.append((f, dt))

# preserve latest per day
daily_snapshots = {}
for fn, dt in backups:
    day = dt.date()
    if day not in daily_snapshots or dt > daily_snapshots[day][1]:
        daily_snapshots[day] = (fn, dt)

# preserve intervals (0,6,12,18) for today only
now = datetime.now()
intervals = [0, 6, 12, 18]
interval_files = []
for h in intervals:
    interval_time = now.replace(hour=h, minute=0, second=0, microsecond=0)
    closest = None
    for fn, dt in backups:
        # choose candidate for same day only (defensive)
        if dt.date() == now.date() and dt.hour >= h and dt.hour < h + 6:
            if closest is None or abs((dt - interval_time).total_seconds()) < abs((closest[1] - interval_time).total_seconds()):
                closest = (fn, dt)
    if closest:
        interval_files.append(closest[0])

preserve = set(fn for fn, dt in daily_snapshots.values())
preserve.update(interval_files)

# Delete others (robust: only remove files that match expected pattern)
removed = 0
for fn, dt in backups:
    if fn not in preserve:
        try:
            p = BACKUP_DIR / fn
            p.unlink()
            removed += 1
        except Exception:
            log_marker(f"ROTATION_FAIL: could not remove {fn}")

log_marker(f"ROTATION_DONE: removed={removed} keep={len(preserve)} backups dir={BACKUP_DIR}")