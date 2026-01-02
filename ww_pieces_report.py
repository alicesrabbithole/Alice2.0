#!/usr/bin/env python3
"""
ww_pieces_report.py

Produce a report of which piece IDs (and filenames) each user has for a given puzzle,
show missing pieces, and list finishers.

Usage:
  python3 ww_pieces_report.py --puzzle winter_wonderland --out ~/Alice2.0/data/backups/ww_report.txt

By default prints to stdout if --out is not supplied.
"""
from __future__ import annotations
import json
from pathlib import Path
import argparse
from typing import Dict, List

def load_data(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", "-d", default=str(Path.home() / "Alice2.0" / "data" / "collected_pieces.json"))
    p.add_argument("--puzzle", "-p", default="winter_wonderland")
    p.add_argument("--out", "-o", help="Optional output file path")
    args = p.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise SystemExit(f"Data file not found: {data_path}")

    data = load_data(data_path)
    pkey = args.puzzle

    pieces_map = data.get("pieces", {}).get(pkey, {}) or {}
    # Normalize piece ids as strings, but report sorted numerically when possible
    all_piece_ids = sorted([str(k) for k in pieces_map.keys()], key=lambda x: int(x) if x.isdigit() else x)
    total_pieces = len(all_piece_ids)

    user_pieces = data.get("user_pieces", {}) or {}
    # Build user -> list-of-piece-ids for this puzzle
    users: Dict[str, List[str]] = {}
    for uid_str, puzzles in user_pieces.items():
        # puzzles may be dict of puzzle_key -> list
        pieces = puzzles.get(pkey, []) if isinstance(puzzles, dict) else []
        if pieces:
            users[uid_str] = [str(x) for x in pieces]

    # Finishers recorded (preserves order)
    finishers_raw = data.get("puzzle_finishers", {}).get(pkey, []) or []
    finishers: List[int] = []
    for f in finishers_raw:
        try:
            uid = int(f.get("user_id")) if isinstance(f, dict) else int(f)
            finishers.append(uid)
        except Exception:
            continue

    # Prepare lines
    out_lines: List[str] = []
    out_lines.append(f"Puzzle: {pkey}")
    out_lines.append(f"Total pieces expected: {total_pieces}")
    out_lines.append("")
    out_lines.append("Finishers (recorded order):")
    if finishers:
        for pos, uid in enumerate(finishers, start=1):
            cnt = len(users.get(str(uid), []))
            out_lines.append(f"  {pos}. {uid} — recorded pieces: {cnt}")
    else:
        out_lines.append("  (none)")

    out_lines.append("")
    out_lines.append("All users with any pieces (sorted by count desc):")
    # sort users by piece count desc, then uid asc
    sorted_users = sorted(users.items(), key=lambda kv: (-len(kv[1]), int(kv[0]) if kv[0].isdigit() else kv[0]))
    for uid_str, pieces in sorted_users:
        uid = int(uid_str) if uid_str.isdigit() else uid_str
        pieces_sorted = sorted([str(x) for x in pieces], key=lambda x: int(x) if x.isdigit() else x)
        missing = [pid for pid in all_piece_ids if pid not in pieces_sorted]
        # Map piece ids to filenames where available
        filenames = [pieces_map.get(pid, f"(no-file-for-{pid})") for pid in pieces_sorted]
        out_lines.append(f"User {uid}: count={len(pieces_sorted)}")
        out_lines.append(f"  pieces: {', '.join(pieces_sorted)}")
        out_lines.append(f"  files: {', '.join(filenames)}")
        if missing:
            out_lines.append(f"  missing ({len(missing)}): {', '.join(missing)}")
        else:
            out_lines.append("  missing (0): (complete)")
        out_lines.append("")

    # Also list users who are finishers but have 0 recorded pieces (if any)
    out_lines.append("Finishers with zero recorded pieces (if any):")
    zero_finishers = [uid for uid in finishers if len(users.get(str(uid), [])) == 0]
    if zero_finishers:
        for uid in zero_finishers:
            out_lines.append(f"  {uid}")
    else:
        out_lines.append("  (none)")

    # Output
    if args.out:
        outp = Path(args.out).expanduser().resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text("\n".join(out_lines))
        print(f"Wrote report to {outp}")
    else:
        print("\n".join(out_lines))

if __name__ == "__main__":
    main()