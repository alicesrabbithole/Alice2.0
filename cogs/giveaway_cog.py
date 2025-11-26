import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import random
import re

def parse_duration(duration_str: str) -> timedelta:
    """
    Parse a duration string like "1d4h30m" into a timedelta.
    Accepts 'd' (days), 'h' (hours), 'm' (minutes) in any order.
    Example: "2h12m", "1d6h", "45m", etc.
    """
    pattern = r'(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?'
    match = re.fullmatch(pattern, duration_str)
    if not match:
        return None
    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes)

class Giveaway:
    def __init__(self, channel_id, message_id, prize, host_id, end_time, num_winners=1, image_url=None):
        self.channel_id = channel_id
        self.message_id = message_id
        self.prize = prize
        self.host_id = host_id
        self.end_time = end_time
        self.num_winners = num_winners
        self.image_url = image_url
        self.entries = set()
        self.ended = False
        self.winners = []

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway, cog):
        super().__init__(timeout=None)
        self.giveaway = giveaway
        self.cog = cog

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.primary)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.giveaway.ended or datetime.utcnow() > self.giveaway.end_time:
            await interaction.response.send_message("Giveaway ended!", ephemeral=True)
            return
        if interaction.user.id in self.giveaway.entries:
            await interaction.response.send_message("You already entered!", ephemeral=True)
            return
        self.giveaway.entries.add(interaction.user.id)
        await interaction.response.send_message("You're entered!", ephemeral=True)

class GiveawayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.giveaways = {}  # message_id -> Giveaway

    @commands.hybrid_command(
        name="giveaway_create",
        description="Create a giveaway. Use duration like '1d2h30m'. Winners, prize, optionally attach an image file."
    )
    @commands.has_permissions(manage_guild=True)
    async def giveaway_create(self, ctx, duration: str, winners: int, prize: str):
        """
        Create a giveaway. Optionally, ATTACH an image file in the command UI.
        Usage: /giveaway_create duration:"1d4h30m" winners:2 prize:"Nitro" [attach file]
        """
        delta = parse_duration(duration)
        if not delta or delta.total_seconds() < 60:
            await ctx.send("Invalid duration! Use format like '2d4h30m'. Must be at least 1 minute.", ephemeral=True)
            return
        end_time = datetime.utcnow() + delta
        host_id = ctx.author.id
        embed = discord.Embed(
            title="🎉 GIVEAWAY 🎉",
            description=f"Prize: **{prize}**\nClick the button below to enter!\nEnds <t:{int(end_time.timestamp())}:R>\nHost: <@{host_id}>",
            color=0x00ff99
        )
        image_url = None
        # If the user has attached a file, use it as the giveaway image!
        if ctx.interaction and ctx.interaction.attachments:
            image_url = ctx.interaction.attachments[0].url
            embed.set_image(url=image_url)
        view = GiveawayView(None, self)
        msg = await ctx.send(embed=embed, view=view)
        giveaway = Giveaway(
            channel_id=ctx.channel.id,
            message_id=msg.id,
            prize=prize,
            host_id=host_id,
            end_time=end_time,
            num_winners=winners,
            image_url=image_url
        )
        view.giveaway = giveaway
        self.giveaways[msg.id] = giveaway
        self.bot.loop.create_task(self.giveaway_auto_end(msg.id, ctx.channel, giveaway))

    async def giveaway_auto_end(self, message_id, channel, giveaway: Giveaway):
        seconds = max(1, int((giveaway.end_time - datetime.utcnow()).total_seconds()))
        await asyncio.sleep(seconds)
        await self.giveaway_end_core(message_id, channel, giveaway)

    @commands.hybrid_command(name="giveaway_end", description="Manually end a giveaway")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_end(self, ctx, message_id: int):
        giveaway = self.giveaways.get(message_id)
        if not giveaway or giveaway.ended:
            await ctx.send("No active giveaway with that message ID.", ephemeral=True)
            return
        channel = self.bot.get_channel(giveaway.channel_id)
        await self.giveaway_end_core(message_id, channel, giveaway)

    async def giveaway_end_core(self, message_id, channel, giveaway: Giveaway):
        if giveaway.ended:
            return
        giveaway.ended = True
        entries = list(giveaway.entries)
        if not entries:
            await channel.send(f"No entries for giveaway **{giveaway.prize}**! 😢")
            return
        winners = random.sample(entries, k=min(giveaway.num_winners, len(entries)))
        giveaway.winners = winners
        win_mentions = " ".join(f"<@{uid}>" for uid in winners)
        embed = discord.Embed(
            title="🎉 Giveaway Ended!",
            description=f"Prize: **{giveaway.prize}**\nWinner(s): {win_mentions}",
            color=0x00ff99
        )
        if giveaway.image_url:
            embed.set_image(url=giveaway.image_url)
        await channel.send(embed=embed)

    @commands.hybrid_command(name="giveaway_reroll", description="Reroll for a new winner")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_reroll(self, ctx, message_id: int):
        giveaway = self.giveaways.get(message_id)
        if not giveaway or not giveaway.ended or not giveaway.entries:
            await ctx.send("No ended giveaway or no entries.", ephemeral=True)
            return
        non_winners = list(set(giveaway.entries) - set(giveaway.winners))
        if not non_winners:
            await ctx.send("No one left to reroll!", ephemeral=True)
            return
        new_winners = random.sample(non_winners, k=min(giveaway.num_winners, len(non_winners)))
        giveaway.winners = new_winners
        win_mentions = " ".join(f"<@{uid}>" for uid in new_winners)
        embed = discord.Embed(
            title="🎉 Giveaway Rerolled!",
            description=f"Prize: **{giveaway.prize}**\nNEW Winner(s): {win_mentions}",
            color=0x00ff99
        )
        if giveaway.image_url:
            embed.set_image(url=giveaway.image_url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="ping", description="Test if bot is alive.")
    async def ping(self, ctx):
        await ctx.send("Pong!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(GiveawayCog(bot))