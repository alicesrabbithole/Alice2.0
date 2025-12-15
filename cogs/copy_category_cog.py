"""
Updated CopyCategory cog with:
- Slash autocomplete for category names
- Option to hide the copied category from a "verified" role (either passed or auto-detected)
- Robust handling for both slash and prefix invocation (hybrid command)
- Safer replies and clearer error handling
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


# Autocomplete helper used by the app command option. Kept at module level so it can be
# referenced by the decorator before the class is created.
async def _category_autocomplete(interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    categories = interaction.guild.categories or []
    choices = [
        discord.app_commands.Choice(name=cat.name, value=cat.name)
        for cat in categories
        if current.lower() in cat.name.lower()
    ]
    return choices[:25]


class CopyCategory(commands.Cog):
    """Cog to copy a category and its channels/permissions."""

    def __init__(self, bot: commands.Bot, success_emoji: Optional[str] = None):
        self.bot = bot
        # Allow passing emoji when adding the cog, or read bot.success_emoji if set,
        # otherwise fall back to the green checkmark.
        self.success_emoji = success_emoji or getattr(bot, "success_emoji", "✅")

    # Dual-purpose hybrid command: works as slash (with autocomplete) and prefix command.
    @commands.hybrid_command(
        name="copycategory",
        description="Copy a category with its channels and permissions (slash supports autocomplete).",
    )
    @discord.app_commands.describe(
        category_name="Category to copy (autocomplete available)",
        hide_from_verified="If true the copied category will be hidden from the verified role until you decide to reveal it",
        verified_role="Optional: role to consider as 'verified' (if omitted the cog will try to find a role named 'Verified')",
    )
    # Wire the autocomplete for the slash option (uses module-level function defined above)
    @discord.app_commands.autocomplete(category_name=_category_autocomplete)
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def copycategory(
        self,
        ctx: commands.Context,
        category_name: str,
        hide_from_verified: bool = False,
        verified_role: Optional[discord.Role] = None,
    ):
        """
        Copy a category and its channels.

        Usage examples:
          /copycategory "Announcements" False
          (slash UI will autocomplete category name)

        Parameters:
        - category_name: the name of the category to copy (slash autocomplete provided)
        - hide_from_verified: if True, the created category will have a deny overwrite for the verified role
        - verified_role: optional role object to treat as verified; if omitted we try to find a role named 'Verified'
        """
        # If invoked as an interaction (slash), defer so Discord doesn't show "This interaction failed".
        interaction: Optional[discord.Interaction] = getattr(ctx, "interaction", None)
        if interaction:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except Exception:
                # best-effort; continue even if defer fails
                logger.debug("Could not defer interaction for copycategory", exc_info=True)

        guild = ctx.guild
        if guild is None:
            # Shouldn't happen because of @guild_only, but be safe.
            if interaction:
                try:
                    await interaction.followup.send("This command must be used in a server.", ephemeral=True)
                except Exception:
                    pass
            else:
                await ctx.reply("This command must be used in a server.", mention_author=False)
            return

        # Find source category (by name)
        src_cat = discord.utils.get(guild.categories, name=category_name)
        if not src_cat:
            msg = f"❌ Category '{category_name}' not found."
            if interaction:
                try:
                    await interaction.followup.send(msg, ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(msg, ephemeral=True)
                    except Exception:
                        pass
            else:
                await ctx.reply(msg, mention_author=False)
            return

        # Determine "verified" role if requested
        verified_role_obj: Optional[discord.Role] = None
        if hide_from_verified:
            if verified_role:
                verified_role_obj = verified_role
            else:
                # try to find common role names
                candidates = ["Verified", "verified", "Member (Verified)", "Members (Verified)"]
                for rn in candidates:
                    r = discord.utils.find(lambda ro: (ro.name or "").lower() == (rn or "").lower(), guild.roles)
                    if r:
                        verified_role_obj = r
                        break
                # last resort: try role named "member" with some heuristics
                if not verified_role_obj:
                    # do not fail if none found; we'll just not add a verified overwrite
                    logger.debug("No verified role auto-detected for hide_from_verified option")

        # Build overwrites for the new category starting from the source category overwrites
        try:
            # category.overwrites returns a Mapping[abc.Snowflake, PermissionOverwrite]
            base_overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = dict(src_cat.overwrites)
        except Exception:
            base_overwrites = {}

        if verified_role_obj:
            # Add deny for view_channel (and read_messages) to hide from verified role
            base_overwrites[verified_role_obj] = discord.PermissionOverwrite(view_channel=False, read_messages=False)

        # Create a unique new category name
        new_name_base = f"{src_cat.name} (Copy)"
        unique_new_name = new_name_base
        attempts = 0
        while discord.utils.get(guild.categories, name=unique_new_name) and attempts < 20:
            attempts += 1
            unique_new_name = f"{new_name_base} #{attempts}"

        # Create the category with composed overwrites
        try:
            new_category = await guild.create_category(name=unique_new_name, overwrites=base_overwrites)
        except Exception as exc:
            msg = f"❌ Failed to create category: {exc}"
            if interaction:
                try:
                    await interaction.followup.send(msg, ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(msg, ephemeral=True)
                    except Exception:
                        pass
            else:
                await ctx.reply(msg, mention_author=False)
            return

        created_channels = []
        try:
            # Duplicate channels inside the category. Channels will still respect the category-level overwrites;
            # we preserve each channel's own overwrites as in the source.
            for channel in src_cat.channels:
                # copy the channel overwrites mapping if present
                try:
                    ch_overwrites = dict(channel.overwrites)
                except Exception:
                    ch_overwrites = {}

                # If hide_from_verified is requested and role exists, ensure channel-level overwrites also include deny
                if hide_from_verified and verified_role_obj and verified_role_obj not in ch_overwrites:
                    # don't override channel-level explicit allows/denies, only add if missing
                    ch_overwrites[verified_role_obj] = discord.PermissionOverwrite(view_channel=False, read_messages=False)

                if isinstance(channel, discord.TextChannel):
                    ch = await guild.create_text_channel(
                        name=channel.name,
                        category=new_category,
                        overwrites=ch_overwrites,
                        topic=channel.topic,
                        slowmode_delay=getattr(channel, "slowmode_delay", 0),
                        nsfw=getattr(channel, "is_nsfw", lambda: False)(),
                    )
                    created_channels.append(ch)

                elif isinstance(channel, discord.VoiceChannel):
                    ch = await guild.create_voice_channel(
                        name=channel.name,
                        category=new_category,
                        overwrites=ch_overwrites,
                        user_limit=getattr(channel, "user_limit", 0),
                        bitrate=getattr(channel, "bitrate", None),
                    )
                    created_channels.append(ch)

                elif isinstance(channel, discord.StageChannel):
                    ch = await guild.create_stage_channel(
                        name=channel.name,
                        category=new_category,
                        overwrites=ch_overwrites,
                        topic=getattr(channel, "topic", None),
                    )
                    created_channels.append(ch)

                else:
                    # Fallback: try create a text channel replica for unknown types
                    try:
                        ch = await guild.create_text_channel(
                            name=channel.name,
                            category=new_category,
                            overwrites=ch_overwrites,
                        )
                        created_channels.append(ch)
                    except Exception:
                        # ignore channels we can't recreate
                        logger.debug("Could not recreate channel %r of type %r", getattr(channel, "name", None), type(channel), exc_info=True)
                        continue

        except Exception as exc:
            # Cleanup what we created to avoid leaving a half-copied category around
            cleanup_errors = []
            for c in created_channels:
                try:
                    await c.delete()
                except Exception as e:
                    cleanup_errors.append(str(e))
            try:
                await new_category.delete()
            except Exception:
                pass

            msg = f"❌ Error while copying channels: {exc}"
            if cleanup_errors:
                msg += f" (cleanup errors: {', '.join(cleanup_errors)})"
            if interaction:
                try:
                    await interaction.followup.send(msg, ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(msg, ephemeral=True)
                    except Exception:
                        pass
            else:
                await ctx.reply(msg, mention_author=False)
            return

            # --- audit/log the creation ---
            try:
                logger.info(
                    "copycategory: user=%s created category=%s from=%s in guild=%s channels_created=%d hide_from_verified=%s verified_role=%s",
                    getattr(ctx.author, "id", None),
                    unique_new_name,
                    getattr(src_cat, "id", None),
                    getattr(guild, "id", None),
                    len(created_channels),
                    hide_from_verified,
                    getattr(verified_role_obj, "id", None) if verified_role_obj else None,
                )
            except Exception:
                logger.exception("copycategory: logger.info failed for category creation")

            # Optional: send a short audit message to a configured channel id on the bot object
            audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
            if audit_id:
                try:
                    audit_ch = self.bot.get_channel(int(audit_id))
                    if audit_ch and isinstance(audit_ch, discord.abc.Messageable):
                        await audit_ch.send(
                            f"Category copied: `{unique_new_name}` (from `{src_cat.name}`) by {getattr(ctx.author, 'mention', str(getattr(ctx.author, 'id', 'unknown')))} in **{guild.name}** (`{guild.id}`)"
                        )
                except Exception:
                    logger.exception("copycategory: failed to send audit message to channel %r", audit_id)

        # Success reply
        success_msg = f"{self.success_emoji} Category `{src_cat.name}` copied successfully as `{unique_new_name}`!"
        if hide_from_verified and verified_role_obj:
            success_msg += f" (hidden from role `{verified_role_obj.name}`)"
        elif hide_from_verified:
            success_msg += " (hide_from_verified requested but no verified role was detected)"

        if interaction:
            try:
                await interaction.followup.send(success_msg, ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(success_msg, ephemeral=True)
                except Exception:
                    # as a last fallback, try a public reply
                    await ctx.reply(success_msg, mention_author=False)
        else:
            await ctx.reply(success_msg, mention_author=False)


async def setup(bot: commands.Bot):
    # When adding the cog you can optionally pass a success emoji:
    # await bot.add_cog(CopyCategory(bot, success_emoji=":youremoji:"))
    await bot.add_cog(CopyCategory(bot))