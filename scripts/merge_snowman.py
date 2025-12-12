#!/usr/bin/env python3
import json
from pathlib import Path
import sys

repo = Path.cwd()
remote = Path('/tmp/remote_buildables.json')
entry = repo / 'data' / 'my_snowman.json'
out = repo / 'data' / 'buildables.json'

def load_json(p):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"ERROR: could not parse JSON from {p}: {e}", file=sys.stderr)
        return None

# Load remote as base (if available)
base = {}
if remote.exists() and remote.stat().st_size > 0:
    parsed = load_json(remote)
    if parsed is None:
        print("Continuing with empty base (remote parse failed).")
    else:
        base = parsed

# Ensure uploaded file exists
if not entry.exists():
    print(f"ERROR: expected uploaded file {entry} not found.", file=sys.stderr)
    sys.exit(1)

uploaded = load_json(entry)
if uploaded is None:
    print("ERROR: uploaded JSON is invalid. Fix data/my_snowman.json and retry.", file=sys.stderr)
    sys.exit(1)

# If uploaded contains full buildables.json, extract snowman; otherwise, use it directly
if 'snowman' in uploaded and isinstance(uploaded['snowman'], dict):
    base['snowman'] = uploaded['snowman']
else:
    # If the uploaded file is a full buildables.json, but doesn't have snowman key,
    # try to use it as the snowman entry if its top-level keys only include snowman
    if set(uploaded.keys()) == {'snowman'}:
        base['snowman'] = uploaded['snowman']
    else:
        # Fallback: set snowman to the whole uploaded file
        base['snowman'] = uploaded

# Write output
try:
    out.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Wrote merged buildables.json to {out}")
except Exception as e:
    print(f"ERROR: could not write {out}: {e}", file=sys.stderr)
    sys.exit(1)