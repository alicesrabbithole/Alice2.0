# RumbleListenerCog with persistent config and embedded announcements
# - Persists RUMBLE_BOT_IDS and CHANNEL_PART_MAP to data/rumble_listener_config.json
# - Loads config on startup; admin commands update + persist config
# - Awards buildable parts via StockingCog.award_part(...)
# - Sends a styled embed announcement in-channel (colors per part) and attaches the part image if available

import asyncio
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord.ext import commands

DATA_DIR = Path("data")
CONFIG_FILE = DATA_DIR / "rumble_listener_config.json"
ASSETS_DIR = DATA_DIR / "stocking_assets"
SEARCH_STARTER_BACK_MESSAGES = 40

WINNER_TITLE_RE = re.compile(r"\bWINNER\b|WON!?", re.IGNORECASE)
STARTED_BY_RE = re.compile(r"Started by\s*[:\-]?\s*(?:<@!?(\d+)>|@?([A-Za-z0-9_`~\-\s]+))", re.IGNORECASE)

PART_EMOJI: Dict[str, str] = {
    "carrot": "🥕",
    "hat": "🎩",
    "scarf": "🧣",
}
PART_COLORS: Dict[str, int] = {
    "carrot": 0xFFA500,
    "hat": 0x000000,
    "scarf": 0x8B0000,
}
DEFAULT_COLOR = 0x2F3136


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, initial_config: Optional[Dict[str, Any]] = None):
        self.bot = bot
        self._lock = asyncio.Lock()
        ensure_data_dir()
        self.rumble_bot_ids: List[int] = []
        self.channel_part_map: Dict[int, Tuple[str, str]] = {}
        if initial_config:
            self._load_from_dict(initial_config)
        self._load_config_file()

    # config persistence
    def _load_from_dict(self, data: Dict[str, Any]) -> None:
        rids = data.get("rumble_bot_ids", [])
        self.rumble_bot_ids = [int(x) for x in rids] if rids else []
        cmap = data.get("channel_part_map", {})
        new_map: Dict[int, Tuple[str, str]] = {}
        for chk, val in cmap.items():
            try:
                ch = int(chk)
            except Exception:
                continue
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                new_map[ch] = (str(val[0]), str(val[1]))
        self.channel_part_map = new_map

    def _load_config_file(self) -> None:
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._load_from_dict(data)
        except Exception:
            return

    def _save_config_file(self) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            out = {
                "rumble_bot_ids": self.rumble_bot_ids,
                "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
            }
            with CONFIG_FILE.open("w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # extraction helpers
    @staticmethod
    def _extract_winner_ids(message: discord.Message) -> List[int]:
        if message.mentions:
            return [m.id for m in message.mentions]
        ids: List[int] = []
        if message.content:
            raw_ids = re.findall(r"<@!?(?P<id>\d+)>", message.content)
            for r in raw_ids:
                try:
                    ids.append(int(r))
                except Exception:
                    pass
        for emb in message.embeds:
            text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
            raw_ids = re.findall(r"<@!?(?P<id>\d+)>", text)
            for r in raw_ids:
                try:
                    ids.append(int(r))
                except Exception:
                    pass
        if not ids and message.guild and message.content:
            m = re.search(r"@([A-Za-z0-9_\-]{2,32})", message.content)
            if m:
                name = m.group(1)
                mem = discord.utils.find(lambda u: u.name == name or u.display_name == name, message.guild.members)
                if mem:
                    ids.append(mem.id)
        return ids

    async def _find_starter_id(self, message: discord.Message) -> Optional[int]:
        for emb in message.embeds:
            for f in (emb.fields or []):
                if "started by" in (f.name or "").lower() or "started by" in (f.value or "").lower():
                    m = re.search(r"<@!?(?P<id>\d+)>", f.value or "")
                    if m:
                        return int(m.group("id"))
                    m2 = STARTED_BY_RE.search(f.value or "")
                    if m2:
                        if m2.group(1):
                            return int(m2.group(1))
                        elif message.guild and m2.group(2):
                            name = m2.group(2).strip()
                            mem = discord.utils.find(lambda u: u.name == name or u.display_name == name, message.guild.members)
                            if mem:
                                return mem.id
        try:
            async for prev in message.channel.history(limit=SEARCH_STARTER_BACK_MESSAGES, before=message.created_at, oldest_first=False):
                content = prev.content or ""
                if "started by" in content.lower() or "started a new rumble" in content.lower():
                    if prev.mentions:
                        return prev.mentions[0].id
                    m = STARTED_BY_RE.search(content)
                    if m:
                        if m.group(1):
                            return int(m.group(1))
                        elif message.guild and m.group(2):
                            name = m.group(2).strip()
                            mem = discord.utils.find(lambda u: u.name == name or u.display_name == name, message.guild.members)
                            if mem:
                                return mem.id
                for emb in prev.embeds:
                    text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                    if "started by" in text.lower():
                        m = re.search(r"<@!?(?P<id>\d+)>", text)
                        if m:
                            return int(m.group("id"))
                        m2 = STARTED_BY_RE.search(text)
                        if m2:
                            if m2.group(1):
                                return int(m2.group(1))
                            elif message.guild and m2.group(2):
                                name = m2.group(2).strip()
                                mem = discord.utils.find(lambda u: u.name == name or u.display_name == name, message.guild.members)
                                if mem:
                                    return mem.id
        except Exception:
            return None
        return None

    # listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        try:
            author_id = int(message.author.id)
        except Exception:
            return
        if self.rumble_bot_ids and author_id not in self.rumble_bot_ids:
            return
        found = False
        if message.content and WINNER_TITLE_RE.search(message.content):
            found = True
        for emb in message.embeds:
            if (emb.title and WINNER_TITLE_RE.search(emb.title)) or (emb.description and WINNER_TITLE_RE.search(emb.description)):
                found = True
                break
            for f in (emb.fields or []):
                if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or ""):
                    found = True
                    break
            if found:
                break
        if not found:
            return
        winner_ids = self._extract_winner_ids(message)
        if not winner_ids:
            return
        mapping = self.channel_part_map.get(message.channel.id)
        if not mapping:
            return
        buildable_key, part_key = mapping
        stocking_cog = self.bot.get_cog("StockingCog")
        if stocking_cog is None:
            return
        async with self._lock:
            for wid in winner_ids:
                try:
                    awarded = False
                    if hasattr(stocking_cog, "award_part"):
                        awarded = await getattr(stocking_cog, "award_part")(int(wid), buildable_key, part_key, message.channel)
                    elif hasattr(stocking_cog, "award_sticker"):
                        awarded = await getattr(stocking_cog, "award_sticker")(int(wid), part_key, message.channel)
                    if awarded:
                        member = message.guild.get_member(int(wid))
                        mention = member.mention if member else f"<@{wid}>"
                        emoji = PART_EMOJI.get(part_key, "")
                        color = PART_COLORS.get(part_key, DEFAULT_COLOR)
                        embed = discord.Embed(
                            title=f"🎉 {member.display_name if member else mention} found a {part_key}!",
                            description=f"You've been awarded **{part_key}** for **{buildable_key}**. Use `/stocking show` to view your progress.",
                            color=color,
                        )
                        embed.set_footer(text=f"Congratulations {member.display_name if member else mention}!")
                        try:
                            part_img = ASSETS_DIR / f"buildables/{buildable_key}/parts/{part_key}.png"
                            if not part_img.exists():
                                part_img = ASSETS_DIR / f"stickers/{part_key}.png"
                            if part_img.exists():
                                attached = discord.File(part_img, filename=part_img.name)
                                embed.set_thumbnail(url=f"attachment://{part_img.name}")
                                await message.channel.send(content=f"{emoji} {mention}", embed=embed, file=attached)
                            else:
                                await message.channel.send(content=f"{emoji} {mention}", embed=embed)
                        except Exception:
                            try:
                                await message.channel.send(content=f"{emoji} {mention}", embed=embed)
                            except Exception:
                                pass
                except Exception:
                    continue

    # config snapshot helper
    def get_config_snapshot(self) -> Dict[str, Any]:
        return {
            "rumble_bot_ids": self.rumble_bot_ids,
            "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
        }


async def setup(bot: commands.Bot):
    listener = RumbleListenerCog(bot)
    await bot.add_cog(listener)