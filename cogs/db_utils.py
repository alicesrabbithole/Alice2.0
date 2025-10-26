import os
import json
import inspect
import logging
import discord
from discord import app_commands
from PIL import Image
import re
from typing import Optional
from pathlib import Path
from cogs.constants import BASE_DIR
import unicodedata

logger = logging.getLogger(__name__)

def slugify_key(key: str) -> str:
    key = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode("ascii")
    slug = key.lower().replace(" ", "_")
    slug = re.sub(r"[^\w_]", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "puzzle"

def get_puzzle(data: dict, key_or_name: str):
    """
    Resolve a puzzle key or human name to (meta, slug).
    Returns (meta_dict or None, slug).
    """
    slug = slugify_key(key_or_name)
    meta = data.get("puzzles", {}).get(slug)
    return meta, slug

def write_preview(puzzle_key: str, base: Image.Image, user_id: Optional[str] = None) -> str:
    uid_part = f"_{user_id}" if user_id else ""
    cache_dir = os.path.join(os.getcwd(), "cache", "previews")
    os.makedirs(cache_dir, exist_ok=True)
    filename = f"temp_progress_{slugify_key(puzzle_key)}{uid_part}.png"
    output_path = os.path.join(cache_dir, filename)
    try:
        base.save(output_path)
    except Exception:
        logger.exception("Failed to save preview to %s", output_path)
        raise
    logger.info("Wrote preview cache: %s", output_path)
    return output_path

# === Constants ===
DB_PATH = os.path.join("data", "collected_pieces.json")

DEFAULT_DATA = {
    "puzzles": {},
    "pieces": {},
    "staff": [],
    "drop_channels": {},
    "user_pieces": {}
}

# === Core Persistence ===
async def puzzle_autocomplete_proxy(interaction: discord.Interaction, current: str):
    cog = interaction.client.get_cog("DropRuntime")
    return await cog.puzzle_autocomplete(interaction, current) if cog else []

async def puzzle_autocomplete_shared(interaction: "discord.Interaction", current: str):
    """
    Module-level autocomplete that returns choices of form:
    Display Name (key) => value is canonical key.
    """
    puzzles = getattr(interaction.client, "data", {}).get("puzzles", {}) or {}
    # use puzzles dict (slug -> meta)
    choices = []
    for slug, meta in puzzles.items():
        display = (meta or {}).get("display_name") or slug.replace("_", " ").title()
        if current.lower() in slug.lower() or current.lower() in display.lower():
            choices.append(app_commands.Choice(name=f"{display}", value=slug))
    # limit to 25 if needed
    return choices[:25]

# optional debug at import to confirm it's a coroutine
logger.info("puzzle_autocomplete_shared is coroutine=%s", inspect.iscoroutinefunction(puzzle_autocomplete_shared))

def resolve_puzzle_key(bot, requested: str) -> str | None:
    if not requested:
        return None
    requested_norm = requested.strip().lower()
    puzzles = (getattr(bot, "data", {}) or {}).get("puzzles", {}) or {}

    # 1) exact key
    if requested in puzzles:
        return requested

    # 2) case-insensitive key
    for key in puzzles:
        if key.lower() == requested_norm:
            return key

    # 3) match display_name (case-insensitive)
    for key, meta in puzzles.items():
        display = (meta.get("display_name") or "").strip()
        if display and display.lower() == requested_norm:
            return key

    # 4) global aliases stored in bot.collected["aliases"]
    aliases = (getattr(bot, "collected", {}) or {}).get("aliases", {}) or {}
    for k, v in aliases.items():
        if k.strip().lower() == requested_norm:
            return v

    # 5) per-puzzle aliases
    for key, meta in puzzles.items():
        for alias in meta.get("aliases", []):
            if alias.strip().lower() == requested_norm:
                return key

    return None

def normalize_all_puzzle_keys(bot):
    data = getattr(bot, "data", {})
    puzzles = data.get("puzzles", {})
    pieces = data.get("pieces", {})
    user_pieces = data.get("user_pieces", {})
    drop_channels = data.get("drop_channels", {})
    aliases = data.get("aliases", {})  # optional

    new_puzzles = {}
    new_pieces = {}
    key_map = {}  # maps old keys and display names to slugs

    # Normalize puzzle keys
    for key in list(puzzles.keys()):
        display = puzzles[key].get("display_name", key)
        slug = slugify_key(display)
        key_map[key] = slug
        key_map[display] = slug
        new_puzzles[slug] = puzzles[key]
        if key in pieces:
            new_pieces[slug] = pieces[key]

    # Normalize drop_channels
    for ch_id, cfg in drop_channels.items():
        puzzle = cfg.get("puzzle")
        if puzzle and puzzle not in new_puzzles:
            resolved = key_map.get(puzzle)
            if resolved:
                cfg["puzzle"] = resolved

    # Normalize user_pieces
    new_user_pieces = {}
    for user_id, puzzle_map in user_pieces.items():
        new_user_pieces[user_id] = {}
        for puzzle_key, piece_list in puzzle_map.items():
            resolved = key_map.get(puzzle_key)
            if resolved:
                new_user_pieces[user_id][resolved] = piece_list

    # Normalize aliases
    new_aliases = {}
    for alias, target in aliases.items():
        resolved = key_map.get(target)
        if resolved:
            new_aliases[alias] = resolved

    # Replace in bot.data
    data["puzzles"] = new_puzzles
    data["pieces"] = new_pieces
    data["drop_channels"] = drop_channels
    data["user_pieces"] = new_user_pieces
    data["aliases"] = new_aliases

    logger.info("✅ Normalized puzzle keys: %s", list(new_puzzles.keys()))

# Pure filesystem sync: reads puzzles from disk and returns a plain data dict
def sync_from_fs(puzzle_root: str = "puzzles") -> dict:
    """
    Pure, side-effect-free sync that builds and returns the full data dict:
    { "puzzles": {...}, "pieces": {...}, "user_pieces": {...}, ... }
    This function should NOT depend on `bot` and can be called from CLI or tests.
    """
    puzzles = {}
    pieces = {}
    user_pieces = {}
    # minimal safe scanning: look for directories under puzzle_root
    if not os.path.isdir(puzzle_root):
        return {"puzzles": puzzles, "pieces": pieces, "user_pieces": user_pieces}

    for entry in sorted(os.listdir(puzzle_root)):
        path = os.path.join(puzzle_root, entry)
        if not os.path.isdir(path):
            continue
        # treat directory name as display name; canonical key is slugify_key(display_name)
        display_name = entry
        key = slugify_key(display_name)
        meta = {}
        meta["display_name"] = display_name
        # attempt to locate full image and thumbnail with conventional names
        full_img = os.path.join(path, f"{display_name}_full.png")
        thumb_img = os.path.join(path, f"{display_name}_thumbnail.png")
        if os.path.exists(full_img):
            meta["full_image"] = full_img.replace("\\", "/")
        if os.path.exists(thumb_img):
            meta["thumbnail"] = thumb_img.replace("\\", "/")
        # optional rows/cols: infer from a manifest file or default 4x4
        meta["rows"] = meta.get("rows", 4)
        meta["cols"] = meta.get("cols", 4)
        meta["enabled"] = True
        puzzles[key] = meta

        # collect piece images under path/pieces
        pieces_dir = os.path.join(path, "pieces")
        if os.path.isdir(pieces_dir):
            piece_files = sorted(f for f in os.listdir(pieces_dir) if f.lower().endswith((".png", ".jpg", ".jpeg")))
            pieces[key] = {}
            for i, fname in enumerate(piece_files, start=1):
                pieces[key][str(i)] = os.path.join(pieces_dir, fname).replace("\\", "/")
        else:
            pieces.setdefault(key, {})

    data = {
        "puzzles": puzzles,
        "pieces": pieces,
        "user_pieces": user_pieces,
    }
    return data

# Wrapper to keep existing API for callers that expect a (count, pieces, list) tuple
def sync_puzzle_images(bot_like=None, puzzle_root: str = "puzzles"):
    """
    Backwards-compatible wrapper:
    - If called with a bot-like object, update bot_like.data and persist via save_data.
    - Always return (count_puzzles, count_pieces, list_of_keys) so callers never get None.
    """
    try:
        data = sync_from_fs(puzzle_root)
    except Exception:
        # Fall back to existing load_data if sync_from_fs fails for any reason
        logger.exception("sync_from_fs failed; falling back to load_data")
        data = load_data()

    # If a bot-like object was provided, attach data and persist
    if bot_like is not None and hasattr(bot_like, "data"):
        bot_like.data = data
        normalize_all_puzzle_keys(bot_like)
        try:
            save_data(data)
        except Exception:
            logger.exception("Failed to save data after sync")

    puzzles = data.get("puzzles", {})
    pieces = data.get("pieces", {})
    count_puzzles = len(puzzles)
    count_pieces = sum(len(v) for v in pieces.values())
    return count_puzzles, count_pieces, list(puzzles.keys())

def normalize_puzzle_identifier(bot, raw: str) -> str | None:
    """
    Return the canonical puzzle key for the provided identifier (key, display_name, or alias).
    Returns None if no match found.
    """
    if not raw:
        return None
    from cogs.db_utils import resolve_puzzle_key  # local import to avoid circulars in some layouts
    return resolve_puzzle_key(bot, raw)

def build_drop_embed(bot, puzzle_key: str, piece_file: str) -> tuple[discord.Embed, discord.File | None]:
    display = bot.data.get("puzzles", {}).get(puzzle_key, {}).get("display_name", puzzle_key.replace("_", " ").title())
    embed = discord.Embed(
        title=f"🧩 {display}",
        description=f"Collect a piece of **{display}**",
        color=discord.Color.purple()
    )

    file = None
    if piece_file:
        piece_path = Path(piece_file)
        if not piece_path.is_absolute():
            piece_path = BASE_DIR.joinpath(piece_file)
        if piece_path.exists():
            filename = f"{puzzle_key}_preview.png"
            file = discord.File(str(piece_path), filename=filename)
            embed.set_image(url=f"attachment://{filename}")

    return embed, file

def set_drop_channel_normalized(bot, channel_id: int, raw_puzzle: str, mode: str, value: int, claims_range=None) -> str:
    """
    Store drop_channels[channel_id]['puzzle'] as the canonical key where possible.
    Returns the value actually saved (canonical or raw if not resolvable).
    """
    data = load_data()
    data.setdefault("drop_channels", {})
    canonical = normalize_puzzle_identifier(bot, raw_puzzle) or raw_puzzle
    entry = {"puzzle": canonical, "mode": mode, "value": value}
    if claims_range is not None:
        entry["claims_range"] = claims_range
    data["drop_channels"][str(channel_id)] = entry
    save_data(data)
    # refresh runtime state
    bot.collected = data
    return canonical

def load_data():
    if not os.path.exists(DB_PATH):
        save_data(DEFAULT_DATA)
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def init_db():
    if not os.path.exists(DB_PATH):
        save_data(DEFAULT_DATA)

# === Piece Management ===
def add_piece_to_user(user_id: int, puzzle_name: str, piece_id: str) -> bool:
    data = load_data()
    uid = str(user_id)
    data.setdefault("user_pieces", {})
    data["user_pieces"].setdefault(uid, {})
    data["user_pieces"][uid].setdefault(puzzle_name, [])

    if piece_id not in data["user_pieces"][uid][puzzle_name]:
        data["user_pieces"][uid][puzzle_name].append(piece_id)
        save_data(data)
        return True
    return False

def get_user_puzzle_progress(user_id: int, puzzle_name: str):
    data = load_data()
    uid = str(user_id)
    all_pieces = set(data.get("pieces", {}).get(puzzle_name, {}).keys())
    owned = set(data.get("user_pieces", {}).get(uid, {}).get(puzzle_name, []))
    return {
        "owned": sorted(owned, key=int),
        "missing": sorted(all_pieces - owned, key=int),
        "total": len(all_pieces),
        "collected": len(owned)
    }


import shutil
import datetime

def backup_data():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    src = os.path.join("data", "collected_pieces.json")
    dst = os.path.join("data", f"backup_{timestamp}.json")
    shutil.copy2(src, dst)
    print(f"🗂️ Backup saved to {dst}")

# === Staff Management ===
def is_staff(user_id: int) -> bool:
    data = load_data()
    return str(user_id) in data.get("staff", [])

def add_staff(user_id: int):
    data = load_data()
    staff = set(data.get("staff", []))
    staff.add(str(user_id))
    data["staff"] = list(staff)
    save_data(data)

def remove_staff(user_id: int):
    data = load_data()
    staff = set(data.get("staff", []))
    staff.discard(str(user_id))
    data["staff"] = list(staff)
    save_data(data)

def list_staff_ids():
    data = load_data()
    return data.get("staff", [])

# === Drop Channel Helpers ===
def get_drop_channels(bot):
    return bot.collected.get("drop_channels", {})

def set_drop_channel(channel_id: int, puzzle: str, mode: str, value: int):
    data = load_data()
    data.setdefault("drop_channels", {})
    data["drop_channels"][str(channel_id)] = {
        "puzzle": puzzle,
        "mode": mode,
        "value": value
    }
    save_data(data)

def remove_drop_channel(channel_id: int):
    data = load_data()
    if str(channel_id) in data.get("drop_channels", {}):
        del data["drop_channels"][str(channel_id)]
        save_data(data)

# === Puzzle Stats ===
def get_puzzle_stats(puzzle_name: str):
    data = load_data()
    pieces = data.get("pieces", {}).get(puzzle_name, {})
    users = data.get("user_pieces", {})
    collected = sum(1 for uid in users if puzzle_name in users[uid])
    return {
        "total_pieces": len(pieces),
        "users_with_progress": collected
    }

def get_all_puzzle_names():
    data = load_data()
    return list(data.get("puzzles", {}).keys())

def get_all_piece_ids(puzzle_name: str):
    data = load_data()
    return list(data.get("pieces", {}).get(puzzle_name, {}).keys())

# === Leaderboard Scaffolding ===
def get_leaderboard(puzzle_name: str):
    data = load_data()
    leaderboard = []
    for uid, puzzles in data.get("user_pieces", {}).items():
        if puzzle_name in puzzles:
            count = len(puzzles[puzzle_name])
            leaderboard.append((uid, count))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    return leaderboard

# === Sync Puzzle Images ===

def get_puzzle_image_paths(puzzle_path, folder):
    def safe(path): return path if os.path.exists(path) else None
    return (
        safe(os.path.join(puzzle_path, f"{folder}_base.png")),
        safe(os.path.join(puzzle_path, f"{folder}_full.png")),
        safe(os.path.join(puzzle_path, f"{folder}_thumbnail.png"))
    )

def sync_puzzle_images(bot, puzzle_root="puzzles"):
    puzzles = {}
    pieces = {}
    names = []

    for folder in os.listdir(puzzle_root):
        puzzle_path = os.path.join(puzzle_root, folder)
        if not os.path.isdir(puzzle_path):
            continue

        base_path, full_path, thumb_path = get_puzzle_image_paths(puzzle_path, folder)

        if not base_path:
            print(f"❌ Skipping '{folder}': no base image found.")
            continue

        try:
            img = Image.open(base_path)
        except Exception as e:
            print(f"❌ Failed to open base image for '{folder}': {e}")
            continue

        piece_folder = os.path.join(puzzle_path, "pieces")
        if not os.path.exists(piece_folder):
            print(f"❌ Skipping '{folder}': no pieces folder found.")
            continue

        piece_files = [f for f in os.listdir(piece_folder) if f.startswith("p1_") and f.endswith(".png")]
        total_pieces = len(piece_files)
        grid_size = int(total_pieces ** 0.5)
        rows = cols = grid_size
        expected_total = rows * cols

        if total_pieces != expected_total:
            print(f"⚠️ Puzzle '{folder}' expected {expected_total} pieces but found {total_pieces}.")

        found_indices = {int(f.split("_")[1].split(".")[0]) for f in piece_files if "_" in f}
        missing = [i for i in range(1, expected_total + 1) if i not in found_indices]
        if missing:
            print(f"🧩 Missing pieces in '{folder}': {missing}")

        # Generate thumbnail if missing
        if not thumb_path:
            thumb_path = os.path.join(puzzle_path, f"{folder}_thumbnail.png")
            try:
                thumb = img.resize((256, 256), Image.Resampling.LANCZOS)
                thumb.save(thumb_path)
            except Exception as e:
                print(f"❌ Failed to generate thumbnail for '{folder}': {e}")
                continue

        # Build puzzle entry
        puzzles[folder] = {
            "display_name": folder.replace("_", " ").title(),
            "full_image": full_path or base_path,
            "rows": rows,
            "cols": cols,
            "enabled": True,
            "thumbnail": thumb_path.replace("\\", "/")
        }

        # Build piece map
        pieces[folder] = {
            f.split("_")[1].split(".")[0]: os.path.join(piece_folder, f).replace("\\", "/")
            for f in piece_files
        }

        names.append(folder)

    bot.data["puzzles"] = puzzles
    bot.data["pieces"] = pieces

    print(f"✅ Synced {len(puzzles)} puzzles with {sum(len(p) for p in pieces.values())} pieces.")




