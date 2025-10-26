#!/usr/bin/env python3
"""
Safe normalization and optional persist for puzzles + pieces data.

Usage (dry run, no writes):
  python tools/fix_sync_and_normalize.py

Apply changes (write backed-up collected_pieces.json):
  python tools/fix_sync_and_normalize.py --apply

This script:
- loads runtime data (prefer bot-like sync if available)
- canonicalizes puzzle keys to slug form
- merges duplicate puzzle entries (non-destructive merge)
- moves pieces and per-user progress into the canonical slug
- ensures a display_name exists
- writes a backup and persists only when --apply is provided
"""
import argparse
import json
import os
import re
import shutil
import sys
from importlib import reload

# adjust import path if script is executed from tools/ with project root as CWD
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cogs.db_utils as dbu  # type: ignore

# reload to pick up local edits during iterative dev
reload(dbu)


def slugify_key(key: str) -> str:
    """Lightweight slugifier that matches db_utils.slugify_key intent."""
    # Prefer dbu.slugify_key if available
    slug_fn = getattr(dbu, "slugify_key", None)
    if callable(slug_fn):
        return slug_fn(key)
    slug = (key or "").lower().replace(" ", "_")
    slug = re.sub(r"[^\w_]", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "puzzle"


def try_sync(bot_like):
    """Try calling known sync entrypoints; return data dict or None."""
    # prefer a synchronous return value from sync functions, but be defensive
    funcs = [
        getattr(dbu, "sync_puzzle_images", None),
        getattr(dbu, "sync_from_fs", None),
        getattr(dbu, "sync", None),
    ]
    for fn in funcs:
        if not callable(fn):
            continue
        try:
            # many variants exist: fn(bot_like), fn(bot_like, puzzle_root="puzzles"), fn()
            try:
                res = fn(bot_like)
            except TypeError:
                try:
                    res = fn(bot_like, puzzle_root="puzzles")
                except TypeError:
                    res = fn()
            # expect dict-like return (puzzles, pieces, etc.)
            if isinstance(res, dict):
                return res
        except Exception:
            # ignore and try the next candidate
            continue
    return None


def load_preferred_data():
    """Load data preferring a bot-like sync result, otherwise fallback to persistent file."""
    # create a minimal stub bot that some sync functions expect
    class StubBot:
        def __init__(self):
            self.data = dbu.load_data() if hasattr(dbu, "load_data") else {}

    bot = StubBot()
    synced = try_sync(bot)
    if synced:
        # normalize shape: if sync returns puzzles/pieces subkeys, use them merged over file
        base = dbu.load_data() if hasattr(dbu, "load_data") else {}
        for k in ("puzzles", "pieces", "user_pieces", "staff", "drop_channels"):
            base.setdefault(k, {})
        # overlay keys from synced
        if "puzzles" in synced:
            base["puzzles"].update(synced.get("puzzles", {}))
        if "pieces" in synced:
            # merge pieces dicts
            for pk, pv in synced.get("pieces", {}).items():
                base.setdefault("pieces", {}).setdefault(pk, {})
                base["pieces"][pk].update(pv)
        # accept user_pieces if provided
        if "user_pieces" in synced:
            base.setdefault("user_pieces", {}).update(synced.get("user_pieces", {}))
        return base
    # fallback to disk
    if hasattr(dbu, "load_data"):
        return dbu.load_data()
    # last resort: read data/collected_pieces.json directly
    data_path = os.path.join(ROOT, "data", "collected_pieces.json")
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"puzzles": {}, "pieces": {}, "user_pieces": {}, "staff": [], "drop_channels": {}}


def backup_file(path: str):
    bak = path + ".bak"
    try:
        shutil.copyfile(path, bak)
        print("Backup written:", bak)
    except FileNotFoundError:
        print("No existing file to back up at", path)


def normalize_data(data: dict):
    """
    Normalize puzzles/pieces/user_pieces in place and return a summary dict.
    Rules:
      - canonicalize puzzle keys to slug form
      - merge non-conflicting fields where duplicates exist
      - move pieces and user_pieces into canonical slug
      - ensure display_name exists
    """
    puzzles = data.setdefault("puzzles", {})
    pieces = data.setdefault("pieces", {})
    user_pieces = data.setdefault("user_pieces", {})

    changes = {"renamed": {}, "merged": [], "errors": []}

    # operate on a snapshot of keys to allow mutation
    for orig_key in list(puzzles.keys()):
        slug = slugify_key(orig_key)
        if slug == orig_key:
            # ensure meta is a dict
            if not isinstance(puzzles[slug], dict):
                changes["errors"].append(f"meta-not-dict:{slug}")
                puzzles[slug] = {}
            continue

        src_meta = puzzles.pop(orig_key)
        if not isinstance(src_meta, dict):
            # if malformed, skip but record
            changes["errors"].append(f"malformed-meta:{orig_key}")
            src_meta = {}

        if slug in puzzles:
            # merge non-conflicting fields (existing wins)
            for k, v in src_meta.items():
                if k not in puzzles[slug]:
                    puzzles[slug][k] = v
            changes["merged"].append((orig_key, slug))
        else:
            puzzles[slug] = src_meta
            changes["renamed"][orig_key] = slug

        # move pieces safely
        pieces.setdefault(slug, {})
        old_pieces = pieces.pop(orig_key, {})
        # ensure both piece dicts are dictionaries
        if isinstance(old_pieces, dict):
            for pid, pval in old_pieces.items():
                if pid not in pieces[slug]:
                    pieces[slug][pid] = pval
        else:
            # handle list or other shapes by appending under numeric keys
            if isinstance(old_pieces, list):
                next_idx = max((int(k) for k in pieces[slug].keys() if str(k).isdigit()), default=0) + 1
                for item in old_pieces:
                    pieces[slug][str(next_idx)] = item
                    next_idx += 1
            else:
                changes["errors"].append(f"unexpected-pieces-shape:{orig_key}")

        # move user_pieces progress
        for uid, pm in list(user_pieces.items()):
            if not isinstance(pm, dict):
                continue
            if orig_key in pm:
                pm.setdefault(slug, [])
                for pid in pm[orig_key]:
                    if pid not in pm[slug]:
                        pm[slug].append(pid)
                del pm[orig_key]

    # ensure display_name
    for slug, meta in puzzles.items():
        if not isinstance(meta, dict):
            puzzles[slug] = {}
            meta = puzzles[slug]
        if "display_name" not in meta:
            meta.setdefault("display_name", meta.get("title") or slug.replace("_", " ").title())

    return changes


def persist_data(data: dict):
    """Persist using dbu.save_data if available, otherwise write to data/collected_pieces.json"""
    if hasattr(dbu, "save_data"):
        dbu.save_data(data)
        return True
    # fallback: write file with backup
    data_path = os.path.join(ROOT, "data", "collected_pieces.json")
    backup_file(data_path)
    os.makedirs(os.path.dirname(data_path), exist_ok=True)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True


def main():
    parser = argparse.ArgumentParser(description="Normalize puzzle data safely")
    parser.add_argument("--apply", action="store_true", help="Persist changes (writes backed-up data/collected_pieces.json)")
    args = parser.parse_args()

    data = load_preferred_data()
    print("Loaded data keys:", ", ".join(sorted(k for k in data.keys())))
    puzzles = data.get("puzzles", {})
    pieces = data.get("pieces", {})
    print("Pre-normalize: puzzles:", len(puzzles), "pieces groups:", len(pieces))

    changes = normalize_data(data)
    print("Normalization summary:")
    print(" - renamed:", len(changes.get("renamed", {})))
    print(" - merged:", len(changes.get("merged", [])))
    if changes.get("errors"):
        print(" - errors:", changes["errors"])

    print("Post-normalize: puzzles:", len(data.get("puzzles", {})),
          "total pieces:", sum(len(v) for v in data.get("pieces", {}).values()))

    if args.apply:
        # persist
        data_path = os.path.join(ROOT, "data", "collected_pieces.json")
        if os.path.exists(data_path):
            backup_file(data_path)
        persist_data(data)
        print("Persisted normalized data to data/collected_pieces.json")
    else:
        print("Dry run complete. No files changed. Re-run with --apply to persist (creates a backup).")


if __name__ == "__main__":
    main()
