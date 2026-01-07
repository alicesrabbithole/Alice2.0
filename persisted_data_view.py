#!/usr/bin/env python3
r"""
persisted_data_view.py

Single-file tool to download the server's persisted_snapshot_latest.tar.gz,
extract JSON files under the data/ tree, and write pretty-printed JSON files
to a local readable folder.

Save this file (e.g. C:\Users\brian\Desktop\persisted_data_view.py) and run:

  python persisted_data_view.py --key "C:\Users\brian\.ssh\alice_snapshot_key"

Defaults are already set to use:
  host: 45.55.90.180
  user: alice
  remote path: /home/alice/Alice2.0/data/backups/persisted_snapshot_latest.tar.gz
  local dir: C:\Users\brian\Desktop\Collected_Pieces.bck

Arguments:
  --host        server host or IP
  --user        ssh user
  --key         path to private key (optional)
  --remote-path remote tarball path
  --local-dir   local base dir to store downloads & readable JSONs
  --force       re-download even if hashes match
  --open        open a specific extracted readable file after run (relative to readable dir)
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

def run_cmd(cmd, capture=True):
    try:
        if capture:
            p = subprocess.run(cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return p.returncode, p.stdout.strip(), p.stderr.strip()
        else:
            p = subprocess.run(cmd, shell=False)
            return p.returncode, "", ""
    except FileNotFoundError as e:
        return 127, "", str(e)

def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def remote_sha256(host, user, remote_path, key=None):
    ssh_cmd = ["ssh"]
    if key:
        ssh_cmd += ["-i", str(key)]
    # run sha256sum on remote; handle shells that require quoting
    ssh_cmd += [f"{user}@{host}", f"sha256sum \"{remote_path}\" || sha256sum {remote_path}"]
    rc, out, err = run_cmd(ssh_cmd)
    if rc != 0:
        return None, err or out
    parts = out.split()
    if len(parts) >= 1:
        return parts[0], None
    return None, out

def scp_download(host, user, remote_path, local_path, key=None):
    scp_cmd = ["scp"]
    if key:
        scp_cmd += ["-i", str(key)]
    scp_cmd += ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", f"{user}@{host}:{remote_path}", str(local_path)]
    return run_cmd(scp_cmd)

def extract_tar(tar_path, extract_to):
    if not tarfile.is_tarfile(tar_path):
        raise RuntimeError(f"{tar_path} is not a tar archive")
    with tarfile.open(tar_path, "r:*") as t:
        t.extractall(path=extract_to)

def pretty_print_jsons(extracted_root, readable_dir):
    extracted_root = Path(extracted_root)
    readable_dir = Path(readable_dir)
    # Try to find the 'data' root inside the extracted tree
    candidate = None
    if (extracted_root / "data").exists():
        candidate = extracted_root / "data"
    else:
        for p in extracted_root.iterdir():
            if (p / "data").exists():
                candidate = p / "data"
                break
    if candidate is None:
        candidate = extracted_root

    json_files = list(candidate.rglob("*.json"))
    if not json_files:
        return 0

    for src in json_files:
        rel = src.relative_to(candidate)
        dest = readable_dir.joinpath(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = src.read_text(encoding="utf-8")
            obj = json.loads(text)
            pretty = json.dumps(obj, indent=2, ensure_ascii=False)
            dest.write_text(pretty, encoding="utf-8")
        except Exception:
            # If JSON parse fails, copy raw file
            shutil.copy2(src, dest)
    return len(json_files)

def open_file_with_default_app(path):
    path = Path(path)
    if not path.exists():
        return False
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
        return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Download and extract persisted data snapshot into human-readable JSON files.")
    parser.add_argument("--host", default=os.environ.get("ALICE_HOST", "45.55.90.180"), help="server host or IP")
    parser.add_argument("--user", default=os.environ.get("ALICE_USER", "alice"), help="ssh user")
    default_key = os.environ.get("ALICE_KEY", str(Path.home() / ".ssh" / "id_rsa"))
    parser.add_argument("--key", default=default_key, help="path to private key (optional)")
    parser.add_argument("--remote-path", default=os.environ.get("ALICE_REMOTE_PATH", "/home/alice/Alice2.0/data/backups/persisted_snapshot_latest.tar.gz"), help="remote tarball path")
    parser.add_argument("--local-dir", default=r"C:\Users\brian\Desktop\Collected_Pieces.bck", help="local base dir to store downloads and extracted readable JSONs")
    parser.add_argument("--force", action="store_true", help="force re-download even if hashes match")
    parser.add_argument("--open", metavar="FILE", help="after extract, open this readable file (path relative to readable dir)")
    args = parser.parse_args()

    host = args.host
    user = args.user
    key = Path(args.key) if args.key else None
    remote_path = args.remote_path
    local_dir = Path(args.local_dir)
    tmp_dir = local_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_tar_tmp = tmp_dir / "persisted_snapshot_latest.tar.gz.tmp"
    local_tar = local_dir / "persisted_snapshot_latest.tar.gz"
    readable_dir = local_dir / "readable"
    prev_hash_file = local_dir / "persisted_snapshot_latest.sha256"

    print(f"Host: {host}, user: {user}")
    print(f"Remote path: {remote_path}")
    print(f"Local dir: {local_dir}")
    local_dir.mkdir(parents=True, exist_ok=True)
    readable_dir.mkdir(parents=True, exist_ok=True)

    remote_hash, err = remote_sha256(host, user, remote_path, key=key if key and key.exists() else None)
    if remote_hash:
        print(f"Remote SHA256: {remote_hash}")
    else:
        print(f"Could not get remote hash: {err}. Proceeding to download (fallback).")

    # Decide whether to download
    if local_tar.exists() and not args.force and remote_hash:
        local_hash = sha256_of_file(local_tar)
        if local_hash == remote_hash:
            print("Local tar matches remote. Skipping download.")
            need_download = False
        else:
            print("Local tar differs from remote. Will download new copy.")
            need_download = True
    else:
        need_download = True
        if args.force:
            print("Force requested; will re-download snapshot.")

    if need_download:
        print("Downloading snapshot (scp)...")
        rc, out, err = scp_download(host, user, remote_path, local_tar_tmp, key=key if key and key.exists() else None)
        if rc != 0:
            print("scp failed:", err or out)
            sys.exit(2)
        # atomically move into place
        local_tar_tmp.replace(local_tar)
        print(f"Downloaded to {local_tar}")

    local_hash = sha256_of_file(local_tar)
    print(f"Local SHA256: {local_hash}")
    if remote_hash and local_hash != remote_hash:
        print("Warning: local hash does not match remote hash!")

    # extract and pretty-print JSONs
    with tempfile.TemporaryDirectory(prefix="alice_extract_") as extract_root:
        extract_root_path = Path(extract_root)
        print("Extracting tar to", extract_root_path)
        try:
            extract_tar(str(local_tar), str(extract_root_path))
        except Exception as e:
            print("Extraction failed:", e)
            sys.exit(3)
        count = pretty_print_jsons(extract_root_path, readable_dir)
        print(f"Extracted and processed {count} JSON files into {readable_dir}")

        if args.open:
            target = readable_dir / args.open
            if target.exists():
                print("Opening", target)
                open_file_with_default_app(target)
            else:
                found = list(readable_dir.rglob(args.open))
                if found:
                    print("Opening", found[0])
                    open_file_with_default_app(found[0])
                else:
                    print(f"Requested file {args.open} not found in readable dir.")

    # Save remote hash locally for quick checks
    if remote_hash:
        try:
            prev_hash_file.write_text(remote_hash)
        except Exception:
            pass

    print("Done.")

if __name__ == "__main__":
    main()
