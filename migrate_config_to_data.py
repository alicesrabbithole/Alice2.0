# tools/migrate_config_to_data.py
import json
import shutil
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
cfg_path = os.path.join(ROOT, "config.json")
data_path = os.path.join(ROOT, "data", "collected_pieces.json")

def load_json(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        raise

def write_json(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

def backup(p):
    bak = p + ".bak"
    try:
        shutil.copyfile(p, bak)
        print("Backup created:", bak)
    except FileNotFoundError:
        print("No file to back up at", p)
    except Exception as e:
        print("Warning: could not write backup for", p, ":", e)

def main(dry_run=True, clear_from_config=False):
    print("Dry run:", dry_run, "Clear moved keys from config.json:", clear_from_config)
    cfg = load_json(cfg_path)
    data = load_json(data_path)

    # ensure data structure
    data.setdefault("puzzles", {})
    data.setdefault("pieces", {})
    data.setdefault("user_pieces", {})
    data.setdefault("staff", [])
    data.setdefault("drop_channels", {})

    moved = {}

    # keys to move if present in config.json
    for key in ("user_pieces", "staff", "drop_channels"):
        if key in cfg:
            moved[key] = cfg[key]

    if not moved:
        print("No moveable keys found in config.json; nothing to do.")
        return

    print("Found keys to merge:", list(moved.keys()))

    # merge staff (preserve unique strings)
    if "staff" in moved:
        existing = set(map(str, data.get("staff", [])))
        incoming = set(map(str, moved["staff"] or []))
        merged = sorted(existing.union(incoming))
        data["staff"] = merged

    # merge drop_channels: shallow merge, incoming wins for conflicts
    if "drop_channels" in moved:
        existing = data.get("drop_channels", {}) or {}
        incoming = moved["drop_channels"] or {}
        merged = dict(existing)
        merged.update(incoming)
        data["drop_channels"] = merged

    # merge user_pieces: merge per-user, per-puzzle lists uniquely
    if "user_pieces" in moved:
        existing = data.get("user_pieces", {}) or {}
        incoming = moved["user_pieces"] or {}
        for uid, puzzles in incoming.items():
            existing.setdefault(uid, {})
            for pkey, piece_list in puzzles.items():
                existing[uid].setdefault(pkey, [])
                for pid in piece_list:
                    if pid not in existing[uid][pkey]:
                        existing[uid][pkey].append(pid)
        data["user_pieces"] = existing

    # show summary
    print("Will merge into data/collected_pieces.json:")
    if "staff" in moved:
        print(" - staff entries:", len(data["staff"]))
    if "drop_channels" in moved:
        print(" - drop_channels keys:", len(data["drop_channels"]))
    if "user_pieces" in moved:
        print(" - user_pieces users:", len(data["user_pieces"]))

    if dry_run:
        print("Dry run complete. No files were changed. Rerun with dry_run=False to apply.")
        return

    # backups and write
    backup(cfg_path)
    backup(data_path)
    write_json(data_path, data)
    print("Wrote merged data to", data_path)

    if clear_from_config:
        for k in ("user_pieces", "staff", "drop_channels"):
            cfg.pop(k, None)
        write_json(cfg_path, cfg)
        print("Cleared moved keys from config.json")

if __name__ == "__main__":
    # default is safe: dry run
    # run with: python tools/migrate_config_to_data.py --apply to persist
    import sys
    apply_it = "--apply" in sys.argv
    clear_cfg = "--clear-config" in sys.argv
    main(dry_run=not apply_it, clear_from_config=clear_cfg)
