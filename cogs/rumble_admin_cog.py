# Discord-only admin commands to manage RumbleListener config and test awards
# - Commands call into the RumbleListenerCog loaded in the same bot
# - No external website required; this provides the quick admin workflow via Discord

from typing import Optional, Tuple, Dict, Any, List

import discord
from discord.ext import commands

def get_listener(bot: commands.Bot):
    return bot.get_cog("RumbleListenerCog")

class RumbleAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ephemeral_reply(self, ctx: commands.Context, content: str):
        try:
            if getattr(ctx, "interaction", None) is not None and getattr(ctx.interaction, "response", None) is not None:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(content, ephemeral=True)
                    return
        except Exception:
            pass
        await ctx.reply(content, mention_author=False)

    @commands.hybrid_command(name="rumble_list", description="(Owner) Return raw persisted RumbleListener config JSON")
    @commands.is_owner()
    async def rumble_list(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        snap = listener.get_config_snapshot()
        import json, io
        bio = io.BytesIO(json.dumps(snap, indent=2).encode("utf-8"))
        await ctx.reply(file=discord.File(bio, filename="rumble_config.json"), mention_author=False)

    @commands.hybrid_command(name="rumble_show_config", description="(Owner) Show pretty config")
    @commands.is_owner()
    async def rumble_show_config(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        snap = listener.get_config_snapshot()
        text = "Monitored bot IDs:\n"
        for bid in snap.get("rumble_bot_ids", []):
            text += f"  • {bid}\n"
        text += "\nChannel mappings:\n"
        cmap = snap.get("channel_part_map", {})
        if cmap:
            for k, v in cmap.items():
                text += f"  • {k}: {v[0]} -> {v[1]}\n"
        else:
            text += "  (none)\n"
        await self._ephemeral_reply(ctx, f"```\n{text}\n```")

    @commands.hybrid_command(name="rumble_add_bot", description="(Owner) Add a rumble bot id to monitor")
    @commands.is_owner()
    async def rumble_add_bot(self, ctx: commands.Context, bot_id: int):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        if bot_id in listener.rumble_bot_ids:
            await self._ephemeral_reply(ctx, f"{bot_id} is already monitored.")
            return
        listener.rumble_bot_ids.append(int(bot_id))
        listener._save_config_file()
        await self._ephemeral_reply(ctx, f"Added {bot_id} to monitored rumble bot list.")

    @commands.hybrid_command(name="rumble_remove_bot", description="(Owner) Remove a rumble bot id")
    @commands.is_owner()
    async def rumble_remove_bot(self, ctx: commands.Context, bot_id: int):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        if bot_id not in listener.rumble_bot_ids:
            await self._ephemeral_reply(ctx, f"{bot_id} not found.")
            return
        listener.rumble_bot_ids.remove(int(bot_id))
        listener._save_config_file()
        await self._ephemeral_reply(ctx, f"Removed {bot_id} from monitored list.")

    @commands.hybrid_command(name="rumble_set_channel_part", description="Set buildable part to award for this channel")
    @commands.has_permissions(manage_guild=True)
    async def rumble_set_channel_part(self, ctx: commands.Context, buildable_key: str, part_key: str):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        listener.channel_part_map[ctx.channel.id] = (buildable_key, part_key)
        listener._save_config_file()
        await self._ephemeral_reply(ctx, f"Channel {ctx.channel.name} will now award `{part_key}` for `{buildable_key}` on rumble wins.")

    @commands.hybrid_command(name="rumble_remove_channel", description="Remove channel mapping")
    @commands.has_permissions(manage_guild=True)
    async def rumble_remove_channel(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        if ctx.channel.id in listener.channel_part_map:
            del listener.channel_part_map[ctx.channel.id]
            listener._save_config_file()
            await self._ephemeral_reply(ctx, f"Removed mapping for {ctx.channel.name}.")
        else:
            await self._ephemeral_reply(ctx, "No mapping for this channel.")

    @commands.hybrid_command(name="rumble_preview", description="Post a styled embed preview of current channel->part mappings")
    @commands.has_permissions(manage_guild=True)
    async def rumble_preview(self, ctx: commands.Context):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        embed = discord.Embed(title="Rumble Listener — Channel → Part Map", color=0x2F3136)
        if listener.rumble_bot_ids:
            embed.add_field(name="Monitored Rumble Bot IDs", value="\n".join(str(x) for x in listener.rumble_bot_ids), inline=False)
        else:
            embed.add_field(name="Monitored Rumble Bot IDs", value="(monitoring all bot messages)", inline=False)
        if listener.channel_part_map:
            mapping_lines: List[str] = []
            for ch, (bkey, pkey) in listener.channel_part_map.items():
                emoji = getattr(listener, "PART_EMOJI", {}).get(pkey, "")
                line = f"<#{int(ch)}> — **{bkey}** → {pkey} {emoji}"
                mapping_lines.append(line)
            embed.add_field(name="Channel Mappings", value="\n".join(mapping_lines), inline=False)
        else:
            embed.add_field(name="Channel Mappings", value="No mappings configured", inline=False)
        embed.set_footer(text="Use /rumble_set_channel_part in a channel to set what it awards.")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="rumble_test_award", description="Simulate awarding a part to a user (for testing)")
    @commands.has_permissions(manage_guild=True)
    async def rumble_test_award(self, ctx: commands.Context, member: discord.Member, channel_id: Optional[int] = None):
        listener = get_listener(self.bot)
        if not listener:
            await self._ephemeral_reply(ctx, "RumbleListenerCog is not loaded.")
            return
        target_channel = ctx.channel
        if channel_id is not None:
            ch = self.bot.get_channel(int(channel_id))
            if ch is None:
                await self._ephemeral_reply(ctx, f"Channel id {channel_id} not found or bot cannot see it.")
                return
            target_channel = ch
        mapping = listener.channel_part_map.get(int(target_channel.id))
        if not mapping:
            await self._ephemeral_reply(ctx, f"No mapping configured for channel {target_channel.mention}.")
            return
        buildable_key, part_key = mapping
        stocking = self.bot.get_cog("StockingCog")
        if stocking is None:
            await self._ephemeral_reply(ctx, "StockingCog not loaded; can't test award.")
            return
        awarded = False
        if hasattr(stocking, "award_part"):
            awarded = await getattr(stocking, "award_part")(member.id, buildable_key, part_key, target_channel)
        elif hasattr(stocking, "award_sticker"):
            awarded = await getattr(stocking, "award_sticker")(member.id, part_key, target_channel)
        if not awarded:
            await self._ephemeral_reply(ctx, f"Failed to award {part_key} to {member.mention} (maybe already has it).")
            return
        emoji = getattr(listener, "PART_EMOJI", {}).get(part_key, "")
        color = getattr(listener, "PART_COLORS", {}).get(part_key, 0x2F3136)
        embed = discord.Embed(
            title=f"🎉 {member.display_name} found a {part_key}!",
            description=f"You've been awarded **{part_key}** for **{buildable_key}**. Use `/stocking show` to view your progress.",
            color=color,
        )
        embed.set_footer(text="Test award simulated by admin")
        try:
            part_file = listener.ASSETS_DIR / f"buildables/{buildable_key}/parts/{part_key}.png"
            if not part_file.exists():
                part_file = listener.ASSETS_DIR / f"stickers/{part_key}.png"
            if part_file.exists():
                f = discord.File(part_file, filename=part_file.name)
                embed.set_thumbnail(url=f"attachment://{part_file.name}")
                await ctx.reply(content=f"{emoji} {member.mention}", embed=embed, file=f, mention_author=False)
                return
        except Exception:
            pass
        await ctx.reply(content=f"{emoji} {member.mention}", embed=embed, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleAdminCog(bot))