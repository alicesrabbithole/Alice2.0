from cogs.db_utils import get_channel_puzzle_slug
import logging
import random

logger = logging.getLogger(__name__)

class DropConfig:
    def __init__(self, bot, channel_id: int, raw_cfg: dict):
        self.bot = bot
        self.channel_id = channel_id
        self.raw = raw_cfg or {}

        self.slug = get_channel_puzzle_slug(bot, self.raw)
        self.meta = bot.data.get("puzzles", {}).get(self.slug, {}) if self.slug else {}
        self.display = self.meta.get("display_name", self.slug.replace("_", " ").title()) if self.slug else "Unknown Puzzle"

        self.claims_range = self.raw.get("claims_range", [1, 3])
        self.mode = self.raw.get("mode", "timer")
        self.value = self.raw.get("value", 1)

        self.message_count = self.raw.get("message_count", 0)
        self.next_trigger = self.raw.get("next_trigger", 10)

    @property
    def pieces_map(self) -> dict:
        return self.bot.data.get("pieces", {}).get(self.slug, {}) or {}


    def roll_trigger(self) -> bool:
        chance = min(100, max(1, int(self.value)))
        roll = random.randint(1, 100)
        logger.debug("🎲 Roll for channel %s: %s <= %s?", self.channel_id, roll, chance)
        return roll <= chance

    def increment_message(self):
        self.message_count += 1
        self.raw["message_count"] = self.message_count

    def reset_trigger(self):
        low, high = self.value if isinstance(self.value, list) else [5, 15]
        delta = random.randint(low, high)
        self.next_trigger = self.message_count + delta
        self.raw["next_trigger"] = self.next_trigger
        logger.debug("🔁 Next trigger at %s messages", self.next_trigger)

def get_claim_limit(self) -> int:
    try:
        low, high = int(self.claims_range[0]), int(self.claims_range[1])
        if low > high:
            low, high = high, low
        return random.randint(low, high)
    except Exception:
        return random.randint(1, 3)
