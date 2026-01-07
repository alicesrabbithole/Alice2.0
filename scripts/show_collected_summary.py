# show_collected_summary.py
import json
from pathlib import Path

p = Path(r"C:\Users\brian\Desktop\Alice2.0\data\collected_pieces.json")  # adjust path
data = json.loads(p.read_text(encoding="utf-8"))

puzzles = data.get("puzzles", {}) if isinstance(data, dict) else {}
pieces = data.get("pieces", {}) if isinstance(data, dict) else {}

print("Puzzles summary:")
for slug, meta in puzzles.items():
    display = meta.get("display_name") if isinstance(meta, dict) else None
    rows = meta.get("rows") if isinstance(meta, dict) else None
    cols = meta.get("cols") if isinstance(meta, dict) else None
    collected_for_puzzle = pieces.get(slug, {})
    collected_count = len(collected_for_puzzle) if isinstance(collected_for_puzzle, dict) else 0
    print(f" - {slug}  ({display or 'no display_name'})  rows={rows} cols={cols}  collected_count={collected_count}")

# If you want to inspect one puzzle's entries, change slug below
example = next(iter(puzzles.keys()), None)
if example:
    print("\nExample entries for:", example)
    sample = pieces.get(example) or {}
    # print up to 20 entries
    for i, (k, v) in enumerate(sample.items()):
        if i >= 20:
            print("  ...")
            break
        print(f"  {k}: {v}")