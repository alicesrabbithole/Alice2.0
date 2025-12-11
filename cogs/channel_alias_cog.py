"""
Channel alias admin + category listing commands.

Provides two safe commands to avoid app_commands union-type issues:
 - /channel_ids category:<pick category>           (accepts a CategoryChannel via picker)
 - /channel_ids_str category:<string>              (accepts an arbitrary string: id/alias/name)

Also includes /channel_alias group (set/remove/list) and /list_channels utility.
"""
from typing import Optional, Dict, Any
import logging

import discord
from discord.ext import commands

from utils.channel_utils import (
    resolve_channel,
    resolve_category,
    set_alias,
    remove_alias,
    list_aliases,
    _normalize,
)

logger = logging.getLogger(__name__)


class ChannelAliasCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply(self, ctx: commands.Context, content: str):
        try:
            if getattr(ctx, "interaction", None) and getattr(ctx.interaction, "response", None) and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, ephemeral=True)
                return
        except Exception:
            pass
        await ctx.reply(content, mention_author=False)

    @commands.hybrid_group(name="channel_alias", description="Manage channel aliases")
    @commands.is_owner()
    async def channel_alias(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._reply(ctx, "Subcommands: set, remove, list")

    @channel_alias.command(name="set", description="Set an alias for a channel (alias -> channel_id)")
    async def channel_alias_set(self, ctx: commands.Context, alias: str, channel: discord.TextChannel):
        try:
            set_alias(alias, channel.id)
        except RuntimeError as exc:
            await self._reply(ctx, f"Cannot set alias: {exc}")
            return
        await self._reply(ctx, f"Alias `{alias}` -> {channel.mention} saved.")

    @channel_alias.command(name="remove", description="Remove an alias")
    async def channel_alias_remove(self, ctx: commands.Context, alias: str):
        try:
            ok = remove_alias(alias)
        except RuntimeError as exc:
            await self._reply(ctx, f"Cannot remove alias: {exc}")
            return
        if ok:
            await self._reply(ctx, f"Removed alias `{alias}`.")
        else:
            await self._reply(ctx, f"Alias `{alias}` not found.")

    @channel_alias.command(name="list", description="List configured aliases")
    async def channel_alias_list(self, ctx: commands.Context):
        aliases = list_aliases()
        if not aliases:
            await self._reply(ctx, "No aliases configured.")
            return
        lines = []
        for a, cid in aliases.items():
            ch = self.bot.get_channel(int(cid))
            chrepr = ch.mention if ch else f"`{cid}`"
            lines.append(f"{a} → {chrepr}")
        text = "Aliases:\n" + "\n".join(lines)
        await self._reply(ctx, f"```\n{text}\n```")

    @commands.hybrid_command(name="list_channels", description="List all text channels in this guild with their IDs")
    @commands.guild_only()
    async def list_channels(self, ctx: commands.Context):
        g = ctx.guild
        if not g:
            await self._reply(ctx, "This command must be used in a guild.")
            return
        lines = []
        for ch in g.text_channels:
            lines.append(f"{ch.mention} — {ch.name} — `{ch.id}`")
        await self._reply(ctx, "Channels:\n```\n" + "\n".join(lines) + "\n```")

    # Option A — accepts a CategoryChannel (slash UI picker; required)
    @commands.hybrid_command(name="channel_ids", description="List all text channel names and IDs in a category (use category picker).")
    @commands.guild_only()
    async def channel_ids(self, ctx: commands.Context, category: discord.CategoryChannel):
        """
        Usage:
          /channel_ids category:<choose category>
        For prefix usage you can pass the category id: !channel_ids 123456789012345678
        """
        if category is None:
            await self._reply(ctx, "You must provide a category (use the category picker in slash commands).")
            return

        lines = []
        for ch in category.text_channels:
            lines.append(f"{ch.mention} — {ch.name} — `{ch.id}`")
        if not lines:
            await self._reply(ctx, f"No text channels found in category {category.name}.")
            return

        text = f"Channels in category **{category.name}**:\n```\n" + "\n".join(lines) + "\n```"
        await self._reply(ctx, text)

    # Option B — accepts a string and resolves by category name/alias (handy for typing)
    @commands.hybrid_command(name="channel_ids_str", description="List channels by category name or alias (string).")
    @commands.guild_only()
    async def channel_ids_str(self, ctx: commands.Context, category: str):
        """
        Usage:
          /channel_ids_str category:"Events"
          Prefix: !channel_ids_str "Events"
        This resolves category by normalized name (exact then startswith).
        """
        if not category:
            await self._reply(ctx, "You must provide a category name or alias.")
            return

        guild = ctx.guild
        cat_obj: Optional[discord.CategoryChannel] = None
        try:
            if category.isdigit():
                cat_obj = guild.get_channel(int(category)) if guild else None
                if not isinstance(cat_obj, discord.CategoryChannel):
                    cat_obj = None
        except Exception:
            cat_obj = None

        if cat_obj is None:
            try:
                cat_obj = await resolve_category(self.bot, guild, category)
            except Exception:
                cat_obj = None

        if cat_obj is None:
            if guild:
                norm = _normalize(category)
                for c in guild.categories:
                    if _normalize(c.name) == norm:
                        cat_obj = c
                        break
                if cat_obj is None:
                    for c in guild.categories:
                        if _normalize(c.name).startswith(norm):
                            cat_obj = c
                            break

        if cat_obj is None:
            await self._reply(ctx, f"Could not find a category matching `{category}`.")
            return

        lines = []
        for ch in cat_obj.text_channels:
            lines.append(f"{ch.mention} — {ch.name} — `{ch.id}`")
        if not lines:
            await self._reply(ctx, f"No text channels found in category {cat_obj.name}.")
            return

        text = f"Channels in category **{cat_obj.name}**:\n```\n" + "\n".join(lines) + "\n```"
        await self._reply(ctx, text)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelAliasCog(bot))