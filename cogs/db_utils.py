import os
import json
import inspect
import discord
from discord import app_commands
from discord import Embed
from PIL import Image
import re
from typing import Optional
from pathlib import Path
from cogs.constants import BASE_DIR
import unicodedata
from tools.utils import pretty_name
import random
import logging
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
    os.makedirs("temp", exist_ok=True)
    uid_part = f"_{user_id}" if user_id else ""
    filename = f"temp_progress_{slugify_key(puzzle_key)}{uid_part}.png"
    output_path = os.path.join("temp", filename)

    try:
        base.save(output_path)
    except Exception:
        logger.exception("❌ Failed to save preview to %s", output_path)
        raise

    logger.info("🖼️ Preview image saved to %s", output_path)
    return output_path


# === Constants ===
DB_PATH = os.path.join("data", "collected_pieces.json")

DEFAULT_DATA = {
    "puzzles": {},
    "pieces": {},
    "staff": [],
    "drop_channels": {},
    "user_pieces": {},
    "render_flags": {}
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
    aliases = (getattr(bot, "data", {}) or {}).get("aliases", {}) or {}
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
        display = pretty_name(puzzles, key)
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
    { "puzzles": {...}, "pieces": {...}, "user_pieces": {...} }
    This function should NOT depend on `bot` and can be called from CLI or tests.
    """
    from cogs.db_utils import slugify_key  # local import to avoid circulars

    puzzles = {}
    pieces = {}
    user_pieces = {}

    if not os.path.isdir(puzzle_root):
        return {"puzzles": puzzles, "pieces": pieces, "user_pieces": user_pieces}

    for entry in sorted(os.listdir(puzzle_root)):
        path = os.path.join(puzzle_root, entry)
        if not os.path.isdir(path):
            continue

        display_name = entry
        key = slugify_key(display_name)
        meta = {
            "display_name": display_name,
            "rows": 4,
            "cols": 4,
            "enabled": True
        }

        full_img = os.path.join(path, f"{display_name}_full.png")
        if os.path.exists(full_img):
            meta["full_image"] = full_img.replace("\\", "/")

        puzzles[key] = meta

        pieces_dir = os.path.join(path, "pieces")
        if os.path.isdir(pieces_dir):
            piece_files = sorted(
                f for f in os.listdir(pieces_dir)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))
            )
            pieces[key] = {
                str(i): os.path.join(pieces_dir, fname).replace("\\", "/")
                for i, fname in enumerate(piece_files, start=1)
            }
        else:
            pieces[key] = {}

    return {
        "puzzles": puzzles,
        "pieces": pieces,
        "user_pieces": user_pieces
    }

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
    bot.data = data
    return canonical

def load_data():
    if not os.path.exists(DB_PATH):
        save_data(DEFAULT_DATA)

    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError:
        logger.warning("⚠️ Data file is empty or corrupted — loading fallback")
        raw = DEFAULT_DATA.copy()
        save_data(raw)

    if not isinstance(raw, dict):
        raise TypeError("Loaded data is not a dictionary")

    from cogs.db_utils import slugify_key  # local import to avoid circulars
    raw["pieces"] = {slugify_key(k): v for k, v in raw.get("pieces", {}).items()}
    raw["puzzles"] = {slugify_key(k): v for k, v in raw.get("puzzles", {}).items()}
    return raw

def save_data(data: dict) -> None:
    if not isinstance(data, dict):
        raise TypeError(f"save_data expected dict, got {type(data).__name__}")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info("✅ Saved data with keys: %s", list(data.keys()))


def load_data():
    if not os.path.exists(DB_PATH):
        save_data(DEFAULT_DATA)

    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError:
        logger.warning("⚠️ Data file is empty or corrupted — loading fallback")
        raw = DEFAULT_DATA.copy()
        save_data(raw)

    # Optional normalization or fallback logic
    raw.setdefault("puzzles", {})
    raw.setdefault("pieces", {})
    raw.setdefault("render_flags", {})  # ✅ Add this if needed

    return raw

# === Piece Management ===

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
    return bot.data.get("drop_channels", {})

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

def resolve_puzzle_folder(slug: str, display_name: str) -> str:
    base = os.path.join(os.getcwd(), "puzzles")
    slug_path = os.path.join(base, slug)
    display_path = os.path.join(base, display_name)

    logger.info("🔍 resolve_puzzle_folder called with slug='%s', display='%s'", slug, display_name)
    logger.info("Checking slug path: %s", slug_path)
    logger.info("Checking display path: %s", display_path)

    if os.path.isdir(slug_path):
        logger.info("✅ Found folder via slug: %s", slug_path)
        return slug_path
    if os.path.isdir(display_path):
        logger.info("✅ Found folder via display name: %s", display_path)
        return display_path

    logger.warning("❌ No folder found for slug='%s' or display='%s'", slug, display_name)
    raise FileNotFoundError(f"No folder found for puzzle: {slug} or {display_name}")

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
    )

def sync_puzzle_data(bot, puzzle_root="puzzles"):
    puzzles = {}
    pieces = {}
    names = []
    missing_summary = {}

    for folder in os.listdir(puzzle_root):
        puzzle_path = os.path.join(puzzle_root, folder)
        if not os.path.isdir(puzzle_path):
            continue

        base_path, full_path = get_puzzle_image_paths(puzzle_path, folder)

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
            missing_summary[folder] = missing

        puzzles[folder] = {
            "display_name": folder.replace("_", " ").title(),
            "full_image": full_path or base_path,
            "rows": rows,
            "cols": cols,
            "enabled": True,
        }

        pieces[folder] = {
            f.split("_")[1].split(".")[0]: os.path.join(piece_folder, f).replace("\\", "/")
            for f in piece_files
        }

        names.append(folder)

    bot.data["puzzles"] = puzzles
    bot.data["pieces"] = pieces
    save_data(bot.data)

    return puzzles, pieces, missing_summary

def sync_puzzle_images(bot, puzzle_root="puzzles"):
    puzzles, pieces, missing_summary = sync_puzzle_data(bot, puzzle_root)
    total_pieces = sum(len(p) for p in pieces.values())

    embed = Embed(
        title="🧩 Puzzle Sync Summary",
        description=f"Synced `{len(puzzles)}` puzzles with `{total_pieces}` pieces.",
        color=discord.Color.purple()
    )

    if missing_summary:
        for name, missing in missing_summary.items():
            embed.add_field(
                name=f"{name} (missing {len(missing)})",
                value=", ".join(str(i) for i in missing[:10]) + ("..." if len(missing) > 10 else ""),
                inline=False
            )
    else:
        embed.add_field(name="✅ All puzzles complete", value="No missing pieces detected.", inline=False)

    return embed


# --- patched add_piece_to_user (slugify incoming puzzle name) ---
def add_piece_to_user(user_id: int, puzzle_name: str, piece_id: str) -> bool:
    try:
        from cogs.db_utils import slugify_key  # local import to avoid circulars
    except Exception:
        from .db_utils import slugify_key  # type: ignore

    puzzle_slug = slugify_key(puzzle_name)
    data = load_data()
    uid = str(user_id)
    data.setdefault('user_pieces', {})
    data['user_pieces'].setdefault(uid, {})
    data['user_pieces'][uid].setdefault(puzzle_slug, [])

    if piece_id not in data['user_pieces'][uid][puzzle_slug]:
        data['user_pieces'][uid][puzzle_slug].append(piece_id)
        save_data(data)
        return True
    return False

def get_channel_puzzle_slug(bot, cfg: dict) -> str | None:
    requested = cfg.get("puzzle")
    if not requested:
        logger.warning("Drop config missing puzzle key")
        return None

    # Try resolving display name to slug
    from cogs.db_utils import resolve_puzzle_key
    slug = resolve_puzzle_key(bot, requested)
    logger.info("Resolved puzzle slug '%s' from requested key '%s'", slug, requested)

    # Handle "All Puzzles" fallback
    if not slug and requested.lower() == "all puzzles":
        all_slugs = list(bot.data.get("puzzles", {}).keys())
        if not all_slugs:
            logger.warning("No puzzles available for 'All Puzzles' fallback")
            return None
        slug = random.choice(all_slugs)
        logger.info("Fallback triggered — randomly selected puzzle: %s", slug)

    if not slug:
        logger.warning("Could not resolve puzzle key '%s'", requested)
        return None

    return slug

def validate_puzzle_config(data: dict):
    for key, meta in data.get("puzzles", {}).items():
        if "base_image" not in meta and "full_image" not in meta:
            logger.warning("⚠️ Puzzle %s missing base/full image", key)
        if "rows" not in meta or "cols" not in meta:
            logger.warning("⚠️ Puzzle %s missing grid dimensions", key)
        if key not in data.get("pieces", {}):
            logger.warning("⚠️ Puzzle %s has no pieces configured", key)
