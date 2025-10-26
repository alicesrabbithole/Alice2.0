# tools/preview_sync.py
from cogs import db_utils
import json

def main():
    data = db_utils.sync_from_fs("puzzles")
    cp = len(data.get("puzzles", {}))
    pp = sum(len(v) for v in data.get("pieces", {}).values())
    print(f"Preview sync: {cp} puzzles, {pp} pieces")
    print("Puzzles (first 50):")
    for n in list(data.get("puzzles", {}).keys())[:50]:
        print(" -", n)

if __name__ == "__main__":
    main()
