import discord
from discord.ext import commands
import json

from utils.checks import is_staff
from utils.theme import Colors, Emojis
import config

STICKY_DATA_FILE = config.DATA_DIR / "stickies.json"
MESSAGE_THRESHOLD = 5


class StickyCog(commands.Cog, name="Sticky Messages"):
    """Manages sticky messages that repost after a set number of messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stickies = {}
        self.message_counts = {}
        self.load_stickies()

    def load_stickies(self):
        try:
            with open(STICKY_DATA_FILE, "r", encoding="utf-8") as f:
                self.stickies = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.stickies = {}

    def save_stickies(self):
        STICKY_DATA_FILE.parent.mkdir(exist_ok=True)
        with open(STICKY_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.stickies, f, indent=4)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        channel_id = str(message.channel.id)
        if channel_id in self.stickies:
            self.message_counts[channel_id] = self.message_counts.get(channel_id, 0) + 1
            if self.message_counts[channel_id] >= MESSAGE_THRESHOLD:
                self.message_counts[channel_id] = 0
                await self.repost_sticky(message.channel)

    async def repost_sticky(self, channel: discord.TextChannel):
        channel_id = str(channel.id)
        sticky_info = self.stickies.get(channel_id)
        if not sticky_info: return

        last_message_id = sticky_info.get("last_message_id")
        if last_message_id:
            try:
                old_message = await channel.fetch_message(last_message_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        embed = discord.Embed(description=sticky_info["message"], color=Colors.PRIMARY)
        try:
            new_message = await channel.send(embed=embed)
            self.stickies[channel_id]["last_message_id"] = new_message.id
            self.save_stickies()
        except discord.Forbidden:
            del self.stickies[channel_id]
            self.save_stickies()

    # --- User Commands ---
    @commands.hybrid_command(name="stickplz", description="Add or update a sticky.")
    @commands.guild_only()
    @is_staff()
    async def sticky_set(self, ctx: commands.Context, *, message: str):
        """Sets the sticky message. Use `*` to capture multi-word messages with prefixes."""
        channel_id = str(ctx.channel.id)
        self.stickies[channel_id] = {"message": message, "last_message_id": None}
        self.message_counts[channel_id] = 0
        self.save_stickies()

        await ctx.send(f"{Emojis.SUCCESS} Sticky message has been set. Posting it now...", ephemeral=True)
        await self.repost_sticky(ctx.channel)

    @commands.hybrid_command(name="byesticky", description="Remove the sticky.")
    @commands.guild_only()
    @is_staff()
    async def sticky_remove(self, ctx: commands.Context):
        """Removes the sticky message from the current channel."""
        channel_id = str(ctx.channel.id)
        if channel_id in self.stickies:
            last_message_id = self.stickies[channel_id].get("last_message_id")
            if last_message_id:
                try:
                    old_message = await ctx.channel.fetch_message(last_message_id)
                    await old_message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

            del self.stickies[channel_id]
            if channel_id in self.message_counts: del self.message_counts[channel_id]
            self.save_stickies()
            await ctx.send(f"{Emojis.SUCCESS} Sticky message has been removed.", ephemeral=True)
        else:
            await ctx.send(f"{Emojis.FAILURE} There is no sticky message set for this channel.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StickyCog(bot))