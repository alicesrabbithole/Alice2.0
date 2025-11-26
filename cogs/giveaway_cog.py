import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import random

class Giveaway:
    def __init__(self, channel_id, message_id, prize, end_time, num_winners=1, image_url=None):
        self.channel_id = channel_id
        self.message_id = message_id
        self.prize = prize
        self.end_time = end_time
        self.num_winners = num_winners
        self.image_url = image_url
        self.entries = set()
        self.ended = False
        self.winners = []

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway):
        super().__init__(timeout=None)
        self.giveaway = giveaway

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.giveaway.ended or datetime.utcnow() > self.giveaway.end_time:
            await interaction.response.send_message("This giveaway has ended!", ephemeral=True)
            return
        if interaction.user.id in self.giveaway.entries:
            await interaction.response.send_message("You already joined!", ephemeral=True)
            return
        self.giveaway.entries.add(interaction.user.id)
        await interaction.response.send_message("You're entered!", ephemeral=True)

class GiveawayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.giveaways = {}  # message_id -> Giveaway

    @commands.hybrid_command(
        name="giveaway_create",
        description="Create a giveaway. Use duration (e.g. '10m') and attach an image if you want."
    )
    @commands.has_permissions(manage_guild=True)
    async def giveaway_create(self, ctx, duration: str, winners: int, prize: str):
        # Parse minutes from duration like '10m'
        try:
            mins = int(duration.replace("m", ""))
            end_time = datetime.utcnow() + timedelta(minutes=mins)
        except Exception:
            await ctx.send("Invalid duration! Use minutes, e.g. '10m'", ephemeral=True)
            return

        image_url = None
        if ctx.interaction and ctx.interaction.attachments:
            image_url = ctx.interaction.attachments[0].url

        embed = discord.Embed(
            title="🎉 GIVEAWAY 🎉",
            description=f"Prize: **{prize}**\nPress the button to enter!\nEnds <t:{int(end_time.timestamp())}:R>",
            color=0x00FF99
        )
        if image_url:
            embed.set_image(url=image_url)
        view = GiveawayView(None)
        msg = await ctx.send(embed=embed, view=view)
        giveaway = Giveaway(
            channel_id=ctx.channel.id,
            message_id=msg.id,
            prize=prize,
            end_time=end_time,
            num_winners=winners,
            image_url=image_url
        )
        view.giveaway = giveaway
        self.giveaways[msg.id] = giveaway

        async def auto_end():
            await asyncio.sleep(max(1, int((end_time - datetime.utcnow()).total_seconds())))
            entries = list(giveaway.entries)
            giveaway.ended = True
            if not entries:
                await ctx.channel.send(f"No entries for giveaway **{prize}**!")
                return
            winner_count = giveaway.num_winners
            actual_winners = random.sample(entries, k=min(winner_count, len(entries)))
            giveaway.winners = actual_winners
            mention_list = ' '.join(f"<@{uid}>" for uid in actual_winners)
            result_embed = discord.Embed(
                title="🎉 Giveaway Ended!",
                description=f"Prize: **{prize}**\nWinner(s): {mention_list}",
                color=0x00FF99
            )
            if image_url:
                result_embed.set_image(url=image_url)
            await ctx.channel.send(embed=result_embed)

        self.bot.loop.create_task(auto_end())

async def setup(bot):
    await bot.add_cog(GiveawayCog(bot))