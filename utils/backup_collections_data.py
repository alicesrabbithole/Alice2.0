import os
import shutil
from datetime import datetime

DATA_DIR = "/home/alice/Alice2.0/data"
DATA_FILE = "../data/collected_pieces.json"
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# Create timestamped backup for this run (hourly)
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
src = os.path.join(DATA_DIR, DATA_FILE)
hourly_backup = os.path.join(BACKUP_DIR, f"{DATA_FILE}.{timestamp}")
shutil.copy2(src, hourly_backup)

# List all backups
all_backups = [f for f in os.listdir(BACKUP_DIR) if f.startswith(DATA_FILE)]
# Parse backups into (file, dt) tuples
def parse_dt(fn):
    try:
        ts = fn.replace(DATA_FILE+'.', '').split('.')[0]
        return datetime.strptime(ts, "%Y%m%d-%H%M%S")
    except Exception:
        return None
backups = []
for f in all_backups:
    dt = parse_dt(f)
    if dt:
        backups.append((f, dt))

# Split backups into hourly and daily
now = datetime.now()
last_7_days = [(now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)).date() for i in range(7)]
daily_snapshots = {}  # date => filename

for fn, dt in backups:
    day = dt.date()
    if day in last_7_days:
        # for daily: keep the last (most recent) file for each day
        if day not in daily_snapshots or dt > daily_snapshots[day][1]:
            daily_snapshots[day] = (fn, dt)

# Prepare sets to preserve
preserve = set(fn for (fn, dt) in daily_snapshots.values())

# Add most recent 4 (hourly) backups (by datetime)
latest_hourly = sorted(backups, key=lambda x: x[1], reverse=True)[:4]
for fn, _ in latest_hourly:
    preserve.add(fn)

# Remove all other backups not in preserve set
for fn, dt in backups:
    if fn not in preserve:
        os.remove(os.path.join(BACKUP_DIR, fn))