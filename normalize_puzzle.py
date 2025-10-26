# normalize_puzzles.py
import json
import os
from cogs.db_utils import load_data, save_data, slugify_key

DATA_PATH = os.path.join("data", "collected_pieces.json")

data = load_data()
puzzles = data.get("puzzles", {})
pieces = data.get("pieces", {})
user_pieces = data.get("user_pieces", {})

mappings = {}
# build mapping: slug -> canonical key (prefer existing slug-key if present)
for key, meta in list(puzzles.items()):
    slug = slugify_key(key)
    # if there's an existing exact slug key, prefer it; otherwise map slug to current key
    if slug in puzzles and slug != key:
        mappings[key] = slug
    else:
        # rename non-slug keys to slug if different
        if slug != key:
            mappings[key] = slug

# apply mappings
for old_key, new_key in mappings.items():
    # merge puzzles entry
    old_meta = puzzles.pop(old_key, None)
    if not old_meta:
        continue
    if new_key in puzzles:
        # merge missing fields into the existing new_key entry without overwriting
        for k, v in (old_meta or {}).items():
            puzzles[new_key].setdefault(k, v)
    else:
        puzzles[new_key] = old_meta
    # merge pieces
    old_pieces = pieces.pop(old_key, {})
    pieces.setdefault(new_key, {})
    pieces[new_key].update(old_pieces)
    # update user_pieces: move any per-user lists
    for uid, puzzles_map in user_pieces.items():
        if old_key in puzzles_map:
            puzzles_map.setdefault(new_key, [])
            # append unique entries
            for pid in puzzles_map[old_key]:
                if pid not in puzzles_map[new_key]:
                    puzzles_map[new_key].append(pid)
            del puzzles_map[old_key]

data["puzzles"] = puzzles
data["pieces"] = pieces
data["user_pieces"] = user_pieces

save_data(data)
print("Normalization complete. Current puzzles:", list(puzzles.keys()))
