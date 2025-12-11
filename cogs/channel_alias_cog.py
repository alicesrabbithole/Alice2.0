"""
Channel alias admin + category listing command.
Single command `channel_ids` accepts either:
 - a CategoryChannel (slash picker) OR
 - a string (category id or name/alias)
and lists text channels with their names and IDs.

Also includes /channel_alias group (set/remove/list) and /list_channels utility.
"""
from typing import Optional, Union, Dict, Any
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

    @commands.hybrid_command(name="channel_ids", description="List text channels in a category. Accepts Category or string (id/name/alias).")
    @commands.guild_only()
    async def channel_ids(self, ctx: commands.Context, category: Optional[Union[discord.CategoryChannel, str]]):
        """
        /channel_ids category:<pick category>
        or /channel_ids category:"events"  (string alias/name/id)
        Prefix: !channel_ids 123456789012345678 or !channel_ids "Events"
        """
        if category is None:
            await self._reply(ctx, "You must provide a category (picker or string).")
            return

        cat_obj: Optional[discord.CategoryChannel] = None
        # If a CategoryChannel object was provided by the slash picker, use it.
        if isinstance(category, discord.CategoryChannel):
            cat_obj = category
        else:
            # category was provided as a string (or id); try to resolve
            try:
                cat_obj = await resolve_category(self.bot, ctx.guild, category)
            except Exception:
                cat_obj = None

        if not cat_obj:
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