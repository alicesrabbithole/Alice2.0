#!/usr/bin/env python3
"""
extract_jsons_from_snapshot.py

Extract only JSON files from a (possibly nested) tar.gz snapshot into a readable folder.
It will:
 - extract .json files encountered directly in the outer tar,
 - for nested tar/tar.gz/tgz files it will stream them to a temp file and attempt to extract JSON files from them,
 - pretty-print JSON when parseable, otherwise copy raw bytes,
 - skip files that fail to open and continue.

Usage:
  python extract_jsons_from_snapshot.py <snapshot.tar.gz> <output_dir>
"""
import sys
import tarfile
import tempfile
import shutil
import json
import io
import os
from pathlib import Path

def safe_path_join(base: Path, member_name: str) -> Path:
    # Prevent path traversal
    member_name = member_name.lstrip("/\\")
    target = (base / member_name).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise RuntimeError("Unsafe path in archive: " + member_name)
    return target

def write_json_pretty(dest_path: Path, data_bytes: bytes):
    try:
        text = data_bytes.decode("utf-8")
        obj = json.loads(text)
        pretty = json.dumps(obj, indent=2, ensure_ascii=False)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(pretty, encoding="utf-8")
        return True
    except Exception:
        # fallback: write raw bytes
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data_bytes)
        return False

def extract_jsons_from_tarfileobj(tar_path: Path, tarobj: tarfile.TarFile, out_base: Path, stats: dict):
    for member in tarobj.getmembers():
        if member.isdir():
            continue
        name = member.name
        lower = name.lower()
        try:
            if lower.endswith(".json"):
                try:
                    f = tarobj.extractfile(member)
                    if f is None:
                        stats['skipped'] += 1
                        continue
                    data = f.read()
                    out = safe_path_join(out_base, name)
                    write_ok = write_json_pretty(out, data)
                    stats['extracted'] += 1
                    stats['pretty'] += 1 if write_ok else 0
                except Exception:
                    stats['errors'] += 1
                    continue
            elif lower.endswith((".tar", ".tar.gz", ".tgz")):
                # stream nested tar to a temp file and try to open it
                try:
                    f = tarobj.extractfile(member)
                    if f is None:
                        stats['skipped'] += 1
                        continue
                    with tempfile.NamedTemporaryFile(delete=False) as tf:
                        shutil.copyfileobj(f, tf)
                        tmpname = tf.name
                    try:
                        with tarfile.open(tmpname, mode='r:*') as nested:
                            extract_jsons_from_tarfileobj(tar_path, nested, out_base, stats)
                    except Exception:
                        stats['nested_errors'] += 1
                    finally:
                        try:
                            os.remove(tmpname)
                        except Exception:
                            pass
                except Exception:
                    stats['errors'] += 1
                    continue
            else:
                # ignore other file types
                continue
        except RuntimeError:
            # unsafe path -> skip
            stats['skipped'] += 1
            continue

def main():
    if len(sys.argv) < 3:
        print("Usage: python extract_jsons_from_snapshot.py <snapshot.tar.gz> <output_dir>")
        sys.exit(2)
    src = Path(sys.argv[1]).expanduser()
    out = Path(sys.argv[2]).expanduser()
    if not src.exists():
        print("Source file not found:", src)
        sys.exit(2)
    out.mkdir(parents=True, exist_ok=True)

    stats = {'extracted':0, 'pretty':0, 'errors':0, 'nested_errors':0, 'skipped':0}
    try:
        with tarfile.open(src, mode='r:*') as t:
            extract_jsons_from_tarfileobj(src, t, out, stats)
    except Exception as e:
        print("Failed to open outer tar:", e)
        sys.exit(1)

    print("Done.")
    print(f"JSON extracted: {stats['extracted']}")
    print(f"Pretty-printed: {stats['pretty']}")
    print(f"Errors: {stats['errors']}, nested_errors: {stats['nested_errors']}, skipped: {stats['skipped']}")
    print("Output dir:", out)

if __name__ == '__main__':
    main()