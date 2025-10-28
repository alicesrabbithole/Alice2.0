# tools/run_sync.py
import shutil
from cogs import db_utils

def main():
    data = db_utils.sync_from_fs("puzzles")

    try:
        shutil.copyfile("../data/collected_pieces.json", "data/collected_pieces.json.bak")
        print("Backup written to data/collected_pieces.json.bak")
    except FileNotFoundError:
        print("No existing data file to back up; a new one will be created.")
    except Exception as e:
        print("Warning: could not write backup:", e)

    db_utils.save_data(data)
    cp = len(data.get("puzzles", {}))
    pp = sum(len(v) for v in data.get("pieces", {}).values())
    print(f"✅ Synced {cp} puzzles with {pp} pieces.")
    print("Puzzles:", list(data.get("puzzles", {}).keys()))

if __name__ == "__main__":
    main()

