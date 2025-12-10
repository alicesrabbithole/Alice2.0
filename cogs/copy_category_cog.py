# Updated CopyCategory cog for your project
# - Uses a configurable success emoji (bot.success_emoji if set, otherwise "✅")
# - Simpler, robust hybrid command compatible with both prefix and slash invocation
# - Safer replies (uses ctx.reply) and clearer error messages
# - Removed fragile autocomplete wiring (add it back later if you want a slash autocomplete)

import discord
from discord.ext import commands
from typing import Optional


class CopyCategory(commands.Cog):
    """Cog to copy a category and its channels/permissions."""

    def __init__(self, bot: commands.Bot, success_emoji: Optional[str] = None):
        self.bot = bot
        # Allow passing emoji when adding the cog, or read bot.success_emoji if set,
        # otherwise fall back to the green checkmark.
        self.success_emoji = success_emoji or getattr(bot, "success_emoji", "✅")

    # Optional autocomplete helper left as a method in case you want to wire it later
    async def category_autocomplete(self, interaction: discord.Interaction, current: str):
        categories = interaction.guild.categories if interaction.guild else []
        choices = [
            discord.app_commands.Choice(name=cat.name, value=cat.name)
            for cat in categories
            if current.lower() in cat.name.lower()
        ]
        return choices[:25]

    @commands.hybrid_command(
        name="copycategory",
        with_app_command=True,
        description="Copy a category with its channels and permissions"
    )
    @commands.guild_only()
    @discord.app_commands.describe(category_name="Select the category you want to copy")
    @commands.has_permissions(manage_channels=True)
    async def copycategory(self, ctx: commands.Context, *, category_name: str):
        """Copies a category with all its channels + permissions."""
        guild = ctx.guild
        if guild is None:
            await ctx.reply("❌ This command can only be used in a server.", mention_author=False)
            return

        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            await ctx.reply(f"❌ Category '{category_name}' not found.", mention_author=False)
            return

        # Build the name for the new category (keep uniqueness in mind)
        new_name = f"{category.name} (Copy)"
        # If name already exists, append a short suffix until unique
        attempts = 0
        unique_new_name = new_name
        while discord.utils.get(guild.categories, name=unique_new_name) and attempts < 10:
            attempts += 1
            unique_new_name = f"{new_name} #{attempts}"

        try:
            # Duplicate category (copy permission overwrites)
            new_category = await guild.create_category(
                name=unique_new_name,
                overwrites=category.overwrites
            )
        except Exception as exc:
            await ctx.reply(f"❌ Failed to create category: {exc}", mention_author=False)
            return

        created = []
        try:
            # Duplicate channels inside the category
            for channel in category.channels:
                overwrites = channel.overwrites

                if isinstance(channel, discord.TextChannel):
                    ch = await guild.create_text_channel(
                        name=channel.name,
                        category=new_category,
                        overwrites=overwrites,
                        topic=channel.topic,
                        slowmode_delay=channel.slowmode_delay,
                        nsfw=channel.is_nsfw()
                    )
                    created.append(ch)

                elif isinstance(channel, discord.VoiceChannel):
                    ch = await guild.create_voice_channel(
                        name=channel.name,
                        category=new_category,
                        overwrites=overwrites,
                        user_limit=channel.user_limit,
                        bitrate=channel.bitrate
                    )
                    created.append(ch)

                elif isinstance(channel, discord.StageChannel):
                    ch = await guild.create_stage_channel(
                        name=channel.name,
                        category=new_category,
                        overwrites=overwrites,
                        topic=getattr(channel, "topic", None)
                    )
                    created.append(ch)

                else:
                    # Fallback: attempt a generic channel creation (rare)
                    try:
                        ch = await guild.create_text_channel(
                            name=channel.name,
                            category=new_category,
                            overwrites=overwrites
                        )
                        created.append(ch)
                    except Exception:
                        # ignore channels we can't recreate
                        continue

        except Exception as exc:
            # If something fails while creating channels, attempt to clean up what we created
            cleanup_errors = []
            for c in created:
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
            await ctx.reply(msg, mention_author=False)
            return

        # Success reply using your configured emoji
        await ctx.reply(f"{self.success_emoji} Category `{category.name}` copied successfully as `{unique_new_name}`!", mention_author=False)


async def setup(bot: commands.Bot):
    # When adding the cog you can optionally pass a success emoji:
    # await bot.add_cog(CopyCategory(bot, success_emoji=":youremoji:"))
    await bot.add_cog(CopyCategory(bot))