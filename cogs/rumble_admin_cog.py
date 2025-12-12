#!/usr/bin/env python3
"""
Discord admin commands to manage RumbleListener config and test awards.

Provides:
- /rumble_list, /rumble_show_config
- /rumble_set_channel_part, /rumble_remove_channel
- /rumble_preview
- /rumble_test_award (accepts optional channel id/mention as string)
- sync_guild_commands (owner only)

This version auto-generates PART_EMOJI and PART_COLORS from data/buildables.json so
all parts defined there (e.g., your 7 snowman pieces) will have sensible defaults.
"""
from __future__ import annotations

from typing import Optional, Tuple, Dict, Any, List
import json
import re
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands

# shared theme generator
from utils.snowman_theme import DEFAULT_COLOR, generate_part_maps_from_buildables

DATA_DIR = Path("data")
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"
ASSETS_DIR = DATA_DIR / "stocking_assets"

# Generated maps used throughout this cog
PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()


def _load_buildables() -> Dict[str, Any]:
    try:
        if BUILDABLES_DEF_FILE.exists():
            with BUILDABLES_DEF_FILE.open("r", encoding="utf-8") as fh:
                return json.load(fh) or {}
    except Exception:
        pass
    return {}


async def _autocomplete_buildable_part(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    buildables = _load_buildables()
    current_raw = (current or "").strip()
    current_lower = current_raw.lower()

    suggestions: List[str] = []

    if ":" in current_raw:
        bfrag, pfrag = current_raw.split(":", 1)
        bfrag = bfrag.strip().lower()
        pfrag = pfrag.strip().lower()
        if bfrag:
            for bkey in sorted(buildables.keys()):
                if bkey.lower().startswith(bfrag):
                    for pkey in sorted((buildables[bkey].get("parts") or {}).keys()):
                        if not pfrag or pkey.lower().startswith(pfrag):
                            suggestions.append(f"{bkey}:{pkey}")
                            if len(suggestions) >= 25:
                                return [app_commands.Choice(name=s, value=s) for s in suggestions]
        for bkey in sorted(buildables.keys()):
            for pkey in sorted((buildables[bkey].get("parts") or {}).keys()):
                if pkey.lower().startswith(pfrag):
                    suggestions.append(f"{bkey}:{pkey}")
                    if len(suggestions) >= 25:
                        return [app_commands.Choice(name=s, value=s) for s in suggestions]
    else:
        matching_buildables = [bk for bk in sorted(buildables.keys()) if bk.lower().startswith(current_lower)] if current_lower else []
        if matching_buildables:
            for bkey in matching_buildables:
                for pkey in sorted((buildables[bkey].get("parts") or {}).keys()):
                    suggestions.append(f"{bkey}:{pkey}")
                    if len(suggestions) >= 25:
                        return [app_commands.Choice(name=s, value=s) for s in suggestions]
        else:
            for bkey in sorted(buildables.keys()):
                for pkey in sorted((buildables[bkey].get("parts") or {}).keys()):
                    candidate = f"{bkey}:{pkey}"
                    if not current_lower or candidate.lower().startswith(current_lower):
                        suggestions.append(candidate)
                        if len(suggestions) >= 25:
                            return [app_commands.Choice(name=s, value=s) for s in suggestions]

    return [app_commands.Choice(name=s, value=s) for s in suggestions]


def get_listener(bot: commands.Bot):
    return bot.get_cog("RumbleListenerCog")


class RumbleAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ephemeral_reply(self, ctx: commands.Context, content: str, *, mention_author: bool = False):
        """
        Prefer ephemeral interaction responses when available for admin commands.
        Falls back to ctx.reply or ctx.send for prefix invocations.
        """
        try:
            if getattr(ctx, "interaction", None) and getattr(ctx.interaction, "response", None) and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, ephemeral=True)
                return
        except Exception:
            pass
        try:
            await ctx.reply(content, mention_author=mention_author)
        except Exception:
            try:
                await ctx.send(content)
            except Exception:
                pass

    def _parse_snowflake(self, raw: str) -> Optional[int]:
        """Tolerant snowflake parsing from mention or pasted text. Returns int or None."""
        if not raw:
            return None
        s = str(raw).strip()
        m = re.search(r"(\d{16,22})", s)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        digits = re.sub(r"\D", "", s)
        try:
            return int(digits) if digits else None
        except Exception:
            return None

    @commands.hybrid_command(name="rumble_list", description="(Owner) Return raw persisted RumbleListener config JSON")
    @commands.is_owner()
    async def rumble_list(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        if hasattr(listener, "get_config_snapshot"):
            snap = listener.get_config_snapshot()
        else:
            snap = {}
        import io
        bio = io.BytesIO(json.dumps(snap, indent=2).encode("utf-8"))
        await ctx.reply(file=discord.File(bio, filename="rumble_config.json"), mention_author=False)

    @commands.hybrid_command(name="rumble_show_config", description="(Owner) Show pretty config")
    @commands.is_owner()
    async def rumble_show_config(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return

        stored_id = None
        try:
            stored_id = getattr(listener, "rumble_bot_id", None)
        except Exception:
            stored_id = None
        if stored_id is None:
            try:
                lst = getattr(listener, "rumble_bot_ids", None)
                if isinstance(lst, (list, tuple)) and lst:
                    stored_id = lst[0]
            except Exception:
                stored_id = None

        snap = listener.get_config_snapshot() if hasattr(listener, "get_config_snapshot") else {}
        text = "Monitored rumble bot id:\n"
        text += f"  • {stored_id}\n" if stored_id else "  (none)\n"

        text += "\nChannel mappings:\n"
        cmap = snap.get("channel_part_map", {}) if isinstance(snap, dict) else {}
        if cmap:
            for k, v in cmap.items():
                emoji = PART_EMOJI.get(v[1].lower(), "")
                line = f"  • {k}: {v[0]} -> {v[1]} {emoji}"
                text += f"{line}\n"
        else:
            text += "  (none)\n"
        await self._ephemeral_reply(ctx, f"```\n{text}\n```")

    @commands.hybrid_command(name="rumble_remove_bot", description="(Owner) Clear the configured rumble bot (or remove only if matches provided id).")
    @commands.is_owner()
    async def rumble_remove_bot(self, ctx: commands.Context, bot_id: Optional[str] = None):
        """
        Remove the configured rumble bot. If bot_id is provided, only removes if it matches the stored id.
        If no bot_id is provided, clears any configured rumble bot.
        """
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return

        stored_id = None
        # read stored id defensively from multiple possible attributes
        try:
            stored_id = int(getattr(listener, "rumble_bot_id"))
        except Exception:
            try:
                lst = getattr(listener, "rumble_bot_ids", None)
                if isinstance(lst, (list, tuple)) and lst:
                    stored_id = int(lst[0])
            except Exception:
                stored_id = None

        if bot_id:
            bid_int = self._parse_snowflake(bot_id)
            if not bid_int:
                await self._ephemeral_reply(ctx, "Please provide a valid bot id to remove.")
                return
            if stored_id is None:
                await self._ephemeral_reply(ctx, f"No rumble bot is configured (nothing to remove).")
                return
            if bid_int != stored_id:
                await self._ephemeral_reply(ctx, f"Configured rumble bot ({stored_id}) does not match the provided id ({bid_int}); no changes made.")
                return

        # Clear stored id
        try:
            if hasattr(listener, "rumble_bot_id"):
                setattr(listener, "rumble_bot_id", None)
        except Exception:
            pass
        try:
            listener.rumble_bot_ids = []
        except Exception:
            try:
                if hasattr(listener, "rumble_bot_ids"):
                    listener.rumble_bot_ids.clear()
            except Exception:
                pass

        try:
            if hasattr(listener, "_save_config_file"):
                listener._save_config_file()
        except Exception:
            pass

        await self._ephemeral_reply(ctx, f"Cleared configured rumble bot (was {stored_id})." if stored_id else "Cleared configured rumble bot.")

    @commands.hybrid_command(name="rumble_set_channel_part", description="Set buildable:part to award for a channel")
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(selection=_autocomplete_buildable_part)
    async def rumble_set_channel_part(self, ctx: commands.Context, selection: str, channel: Optional[discord.TextChannel] = None):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return

        if not selection or ":" not in selection:
            await self._ephemeral_reply(ctx, "Please provide a value of the form `buildable:part` (e.g. `snowman:carrot`). Use autocomplete for help.")
            return

        buildable_key, part_key = selection.split(":", 1)
        buildable_key = buildable_key.strip()
        part_key = part_key.strip()
        if not buildable_key or not part_key:
            await self._ephemeral_reply(ctx, "Invalid selection. Use the form `buildable:part` (e.g. `snowman:carrot`).")
            return

        buildables = _load_buildables()
        bdef = buildables.get(buildable_key)
        if not bdef or part_key not in (bdef.get("parts") or {}):
            await self._ephemeral_reply(ctx, f"Unknown buildable or part: `{selection}`. Check your definitions in data/buildables.json.")
            return

        target = channel or ctx.channel
        if not hasattr(listener, "channel_part_map"):
            listener.channel_part_map = {}
        # Overwrite any existing mapping for this channel (keep only latest)
        listener.channel_part_map[int(target.id)] = (buildable_key, part_key)
        if hasattr(listener, "_save_config_file"):
            listener._save_config_file()
        await self._ephemeral_reply(ctx, f"Channel {target.mention} will now award `{part_key}` for `{buildable_key}` on rumble wins.")

    @commands.hybrid_command(name="rumble_remove_channel", description="Remove channel mapping")
    @commands.has_permissions(manage_guild=True)
    async def rumble_remove_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        target = channel or ctx.channel
        if int(target.id) in getattr(listener, "channel_part_map", {}):
            del listener.channel_part_map[int(target.id)]
            if hasattr(listener, "_save_config_file"):
                listener._save_config_file()
            await self._ephemeral_reply(ctx, f"Removed mapping for {target.mention}.")
        else:
            await self._ephemeral_reply(ctx, "No mapping for that channel.")

    @commands.hybrid_command(name="rumble_preview", description="Post a styled embed preview of current channel->part mappings")
    @commands.has_permissions(manage_guild=True)
    async def rumble_preview(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return

        did_defer = False
        try:
            if getattr(ctx, "interaction", None) and getattr(ctx.interaction, "response", None) and not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=False)
                did_defer = True
        except Exception:
            did_defer = False

        embed = discord.Embed(title="Rumble Listener — Channel → Part Map", color=DEFAULT_COLOR)
        try:
            if getattr(listener, "rumble_bot_ids", None):
                embed.add_field(name="Monitored Rumble Bot IDs", value="\n".join(str(x) for x in getattr(listener, "rumble_bot_ids", [])), inline=False)
            else:
                embed.add_field(name="Monitored Rumble Bot IDs", value="(monitoring all bot messages)", inline=False)
            if getattr(listener, "channel_part_map", None):
                mapping_lines: List[str] = []
                for ch, (bkey, pkey) in listener.channel_part_map.items():
                    emoji = PART_EMOJI.get(pkey.lower(), "")
                    line = f"<#{int(ch)}> — **{bkey}** → {pkey} {emoji}"
                    mapping_lines.append(line)
                embed.add_field(name="Channel Mappings", value="\n".join(mapping_lines), inline=False)
            else:
                embed.add_field(name="Channel Mappings", value="No mappings configured", inline=False)
            embed.set_footer(text="Use /rumble_set_channel_part with a channel argument to configure a channel remotely.")
        except Exception:
            if did_defer:
                try:
                    await ctx.interaction.followup.send("Failed to build preview (see logs).", ephemeral=True)
                except Exception:
                    pass
            else:
                await self._ephemeral_reply(ctx, "Failed to build preview (see logs).")
            return

        try:
            if did_defer:
                await ctx.interaction.followup.send(embed=embed)
            else:
                await ctx.reply(embed=embed, mention_author=False)
        except Exception:
            await self._ephemeral_reply(ctx, "Failed to send preview (see logs).")

    @commands.hybrid_command(name="rumble_test_award", description="Simulate awarding a part to a user (for testing)")
    @commands.has_permissions(manage_guild=True)
    async def rumble_test_award(self, ctx: commands.Context, member: discord.Member, channel_id: Optional[str] = None):
        """
        Simulate awarding a part to a user in the provided (or current) channel.
        channel_id may be a mention or id string.
        """
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return

        target_channel = ctx.channel
        if channel_id is not None:
            cid = self._parse_snowflake(channel_id)
            if cid is None:
                await self._ephemeral_reply(ctx, f"Channel id {channel_id} not found or bot cannot see it.")
                return
            ch = self.bot.get_channel(int(cid))
            if ch is None or not isinstance(ch, discord.TextChannel):
                await self._ephemeral_reply(ctx, f"Channel id {channel_id} not found or bot cannot see it.")
                return
            target_channel = ch

        mapping = getattr(listener, "channel_part_map", {}).get(int(target_channel.id))
        if not mapping:
            await self._ephemeral_reply(ctx, f"No mapping configured for channel {target_channel.mention}.")
            return
        buildable_key, part_key = mapping
        stocking = self.bot.get_cog("StockingCog")
        if stocking is None:
            await self._ephemeral_reply(ctx, "StockingCog not loaded; can't test award.")
            return

        # Persist + render composite, but do NOT let StockingCog announce it (announce=False).
        awarded = False
        if hasattr(stocking, "award_part"):
            awarded = await getattr(stocking, "award_part")(member.id, buildable_key, part_key, None, announce=False)
        elif hasattr(stocking, "award_sticker"):
            awarded = await getattr(stocking, "award_sticker")(member.id, part_key, None, announce=False)
        if not awarded:
            await self._ephemeral_reply(ctx, f"Failed to award {part_key} to {member.mention} (maybe already has it).")
            return

        emoji = PART_EMOJI.get(part_key.lower(), "")
        color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
        color = discord.Color(color_int)
        embed = discord.Embed(
            title=f"🎉 {member.display_name} found a {part_key}!",
            description=f"You've been awarded **{part_key}** for **{buildable_key}**. Use `/mysnowman` or `/stocking show` to view your assembled snowman.",
            color=color,
        )
        embed.set_footer(text="Test award simulated by admin")
        try:
            part_file = ASSETS_DIR / f"buildables/{buildable_key}/parts/{part_key}.png"
            if not part_file.exists():
                part_file = ASSETS_DIR / f"stickers/{part_key}.png"
            if part_file.exists():
                f = discord.File(part_file, filename=part_file.name)
                embed.set_thumbnail(url=f"attachment://{part_file.name}")
                await ctx.reply(content=f"{emoji} {member.mention}", embed=embed, file=f, mention_author=False)
                return
        except Exception:
            pass
        await ctx.reply(content=f"{emoji} {member.mention}", embed=embed, mention_author=False)

    @commands.command(name="sync_guild_commands")
    @commands.is_owner()
    async def sync_guild_commands(self, ctx: commands.Context):
        """Force a guild-only slash command sync (immediate)."""
        try:
            await self.bot.tree.sync(guild=ctx.guild)
            await ctx.reply("Synced commands to this guild.", mention_author=False)
        except Exception as exc:
            await ctx.reply(f"Sync failed: {exc}", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleAdminCog(bot))