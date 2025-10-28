def pretty_name(puzzles: dict, key: str) -> str:
    return puzzles.get(key, {}).get("display_name", key)
