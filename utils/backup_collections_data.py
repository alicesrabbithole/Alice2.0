import os
import shutil
from datetime import datetime, timedelta

# CONFIGURATION
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))
DATA_FILE = "collected_pieces.json"
BACKUP_DIR = r"C:\Users\brian\Desktop\Alice2.0\backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

src = os.path.join(DATA_DIR, DATA_FILE)
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup_file = f"collected_pieces_{timestamp}.json"
hourly_backup = os.path.join(BACKUP_DIR, backup_file)
shutil.copy2(src, hourly_backup)

# List backups
all_backups = [f for f in os.listdir(BACKUP_DIR) if f.startswith("collected_pieces_") and f.endswith(".json")]

def parse_dt(fn):
    try:
        ts = fn.replace("collected_pieces_", "").replace(".json", "")
        return datetime.strptime(ts, "%Y%m%d-%H%M%S")
    except Exception:
        return None

backups = []
for f in all_backups:
    dt = parse_dt(f)
    if dt:
        backups.append((f, dt))

# --- Permanently preserve one backup per day ---
daily_snapshots = {}
for fn, dt in backups:
    day = dt.date()
    if day not in daily_snapshots or dt > daily_snapshots[day][1]:
        daily_snapshots[day] = (fn, dt)  # preserve latest per day

# --- Also preserve the latest 6am, 12pm, 6pm, midnight for up to 24h ---
now = datetime.now()
intervals = [0, 6, 12, 18]
interval_files = []

for h in intervals:
    interval_time = now.replace(hour=h, minute=0, second=0, microsecond=0)
    # Find the snapshot closest to but not after this interval (within 6 hours)
    closest = None
    for fn, dt in backups:
        if dt.date() == now.date() and dt.hour >= h and dt.hour < h + 6:
            if closest is None or abs((dt - interval_time).total_seconds()) < abs((closest[1] - interval_time).total_seconds()):
                closest = (fn, dt)
    if closest:
        interval_files.append(closest[0])

# Combine sets to preserve: daily + last 24h intervals
preserve = set(fn for fn, dt in daily_snapshots.values())
preserve.update(interval_files)

# Remove all other backups
for fn, dt in backups:
    if fn not in preserve:
        os.remove(os.path.join(BACKUP_DIR, fn))