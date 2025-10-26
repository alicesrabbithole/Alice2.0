# tools/preview_cache.py
import os
import hashlib
import glob
from typing import List

CACHE_DIR = os.path.join(os.getcwd(), "cache", "previews")


def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def preview_cache_path(puzzle_key: str, user_id: str, owned_ids: List[str]) -> str:
    """
    Compute a stable cache path for the given puzzle/user/owned set.
    Uses a short sha1 of the sorted owned ids for filename stability.
    """
    _ensure_cache_dir()
    key = f"{puzzle_key}__{user_id}__{','.join(sorted(map(str, owned_ids)))}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"{puzzle_key}__{user_id}__{digest}.png")


def get_cache_dir() -> str:
    _ensure_cache_dir()
    return CACHE_DIR


def invalidate_user_puzzle_cache(puzzle_key: str, user_id: str) -> int:
    """
    Remove all cached previews for the given puzzle_key and user_id.
    Returns number of files removed.
    """
    _ensure_cache_dir()
    pattern = os.path.join(CACHE_DIR, f"{puzzle_key}__{user_id}__*.png")
    removed = 0
    for fn in glob.glob(pattern):
        try:
            os.remove(fn)
            removed += 1
        except Exception:
            pass
    return removed


def cleanup_older_than(days: int = 7) -> int:
    """
    Remove cached previews older than `days`. Returns number removed.
    """
    import time

    _ensure_cache_dir()
    cutoff = time.time() - days * 86400
    removed = 0
    for fn in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, fn)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    return removed
