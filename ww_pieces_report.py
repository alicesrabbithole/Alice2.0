#!/usr/bin/env python3
"""
ww_pieces_report.py

Report which piece IDs each user has for a given puzzle, show missing pieces, and list finishers.
Resolves user IDs to display names optionally (map file / Discord API).

Change from previous version:
- Does NOT list filenames anymore. It shows collected piece IDs only (e.g. "collected: 1, 2, 24").
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

# Try to use requests if available; otherwise fall back to urllib
try:
    import requests  # type: ignore
    HAS_REQUESTS = True
except Exception:
    import urllib.request as _urllib_request  # type: ignore
    HAS_REQUESTS = False

def load_data(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))

def load_map_file(path: Path) -> Dict[str, str]:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    text = path.read_text(encoding="utf-8")
    try:
        j = json.loads(text)
        return {str(k): str(v) for k, v in j.items()}
    except Exception:
        out = {}
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            for row in reader:
                if not row:
                    continue
                if len(row) >= 2:
                    out[str(row[0]).strip()] = row[1].strip()
        return out

def discord_get_member(display_cache: Dict[str, str], token: str, guild_id: str, user_id: str, use_requests: bool) -> Optional[str]:
    if user_id in display_cache:
        return display_cache[user_id]
    url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}"
    headers = {"Authorization": f"Bot {token}", "User-Agent": "ww-pieces-report/1.0"}
    try:
        if use_requests:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                m = r.json()
            else:
                return None
        else:
            req = _urllib_request.Request(url, headers=headers)
            with _urllib_request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                m = json.loads(raw)
        user = m.get("user", {})
        nick = m.get("nick")
        username = user.get("username") or user.get("id")
        discrim = user.get("discriminator")
        if nick:
            disp = f"{nick} ({username}#{discrim})" if discrim else f"{nick} ({username})"
        else:
            disp = f"{username}#{discrim}" if discrim else f"{username}"
        display_cache[user_id] = disp
        return disp
    except Exception:
        return None

def resolve_display_names(user_ids: Set[str], map_file: Optional[Path], token: Optional[str], guild_id: Optional[str], cache_file: Optional[Path]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    cache: Dict[str, str] = {}
    if cache_file:
        try:
            if cache_file.exists():
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if map_file:
        try:
            mf = load_map_file(map_file)
            result.update(mf)
        except Exception as e:
            print(f"[warn] Unable to read map file {map_file}: {e}", file=sys.stderr)
    for uid, name in cache.items():
        if uid not in result:
            result[uid] = name
    remaining = [uid for uid in user_ids if uid not in result]
    if token and guild_id and remaining:
        use_requests = HAS_REQUESTS
        for uid in remaining:
            name = discord_get_member(cache, token, guild_id, uid, use_requests)
            if name:
                result[uid] = name
            time.sleep(0.25)
    if cache_file:
        try:
            merged = dict(cache)
            merged.update({k: v for k, v in result.items() if k not in merged})
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        except Exception:
            pass
    return result

def main():
    parser = argparse.ArgumentParser(description="Produce puzzle pieces report with optional user id -> display name resolution")
    parser.add_argument("--data", "-d", default=str(Path.home() / "Alice2.0" / "data" / "collected_pieces.json"))
    parser.add_argument("--puzzle", "-p", default="winter_wonderland")
    parser.add_argument("--out", "-o", help="Optional output file path")
    parser.add_argument("--map-file", "-m", help="Optional local map file (JSON or CSV) mapping user_id -> display name")
    parser.add_argument("--discord-token", "-t", help="Optional Bot token to resolve member names via Discord API (requires --guild-id)")
    parser.add_argument("--guild-id", "-g", help="Guild ID for Discord member lookups")
    parser.add_argument("--cache", "-c", help="Cache file path for Discord lookups (JSON). Default: data/backups/user_lookup_cache.json")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise SystemExit(f"Data file not found: {data_path}")

    data = load_data(data_path)
    pkey = args.puzzle

    pieces_map = data.get("pieces", {}).get(pkey, {}) or {}
    all_piece_ids = sorted([str(k) for k in pieces_map.keys()], key=lambda x: int(x) if x.isdigit() else x)
    total_pieces = len(all_piece_ids)

    user_pieces = data.get("user_pieces", {}) or {}
    users: Dict[str, List[str]] = {}
    for uid_str, puzzles in user_pieces.items():
        pieces = puzzles.get(pkey, []) if isinstance(puzzles, dict) else []
        if pieces:
            users[uid_str] = [str(x) for x in pieces]

    finishers_raw = data.get("puzzle_finishers", {}).get(pkey, []) or []
    finishers: List[str] = []
    for f in finishers_raw:
        try:
            uid = str(f.get("user_id")) if isinstance(f, dict) else str(f)
            finishers.append(uid)
        except Exception:
            continue

    user_ids: Set[str] = set(users.keys()) | set(finishers)
    map_file = Path(args.map_file).expanduser().resolve() if args.map_file else None
    cache_file = Path(args.cache).expanduser().resolve() if args.cache else Path.home() / "Alice2.0" / "data" / "backups" / "user_lookup_cache.json"

    name_map = resolve_display_names(user_ids, map_file, args.discord_token, args.guild_id, cache_file)

    def pretty(uid: str) -> str:
        return f"{name_map.get(uid, '')} <{uid}>" if uid in name_map else f"{uid}"

    out_lines: List[str] = []
    out_lines.append(f"Puzzle: {pkey}")
    out_lines.append(f"Total pieces expected: {total_pieces}")
    out_lines.append("")
    out_lines.append("Finishers (recorded order):")
    if finishers:
        for pos, uid in enumerate(finishers, start=1):
            cnt = len(users.get(uid, []))
            out_lines.append(f"  {pos}. {pretty(uid)} — recorded pieces: {cnt}")
    else:
        out_lines.append("  (none)")

    out_lines.append("")
    out_lines.append("All users with any pieces (sorted by count desc):")
    sorted_users = sorted(users.items(), key=lambda kv: (-len(kv[1]), int(kv[0]) if kv[0].isdigit() else kv[0]))
    for uid_str, pieces in sorted_users:
        uid = uid_str
        pieces_sorted = sorted([str(x) for x in pieces], key=lambda x: int(x) if x.isdigit() else x)
        missing = [pid for pid in all_piece_ids if pid not in pieces_sorted]
        # Show collected piece IDs only (no filenames)
        collected_display = ", ".join(pieces_sorted)
        out_lines.append(f"User {pretty(uid)}: count={len(pieces_sorted)}")
        out_lines.append(f"  collected: {collected_display}")
        if missing:
            out_lines.append(f"  missing ({len(missing)}): {', '.join(missing)}")
        else:
            out_lines.append("  missing (0): (complete)")
        out_lines.append("")

    out_lines.append("Finishers with zero recorded pieces (if any):")
    zero_finishers = [uid for uid in finishers if len(users.get(uid, [])) == 0]
    if zero_finishers:
        for uid in zero_finishers:
            out_lines.append(f"  {pretty(uid)}")
    else:
        out_lines.append("  (none)")

    if args.out:
        outp = Path(args.out).expanduser().resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text("\n".join(out_lines), encoding="utf-8")
        print(f"Wrote report to {outp}")
    else:
        print("\n".join(out_lines))

if __name__ == "__main__":
    main()