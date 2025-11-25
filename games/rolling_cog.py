import discord
from discord.ext import commands
import random
import os
import json
import asyncio
from datetime import datetime, timedelta
from utils.checks import STAFF_ROLE_ID

DB_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'roll_leaderboard.json')


def load_leaderboard():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_leaderboard(lb):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(lb, f)


def format_remaining(end_time):
    if not end_time:
        return ""
    delta = end_time - datetime.utcnow()
    if delta.total_seconds() <= 0:
        return "Game ended!"
    minutes, seconds = divmod(int(delta.total_seconds()), 60)
    if minutes >= 60:
        return f"Time left: {minutes // 60}h {minutes % 60}m"
    return f"Time left: {minutes}m {seconds}s"


class PersonalRollView(discord.ui.View):
    def __init__(self, cog, user_id, game_end_time=None):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id
        self.rolls = []
        self.finished = False
        self.game_end_time = game_end_time

    @discord.ui.button(label="Roll 1-10 🎲", style=discord.ButtonStyle.primary)
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Game active check
        guild_id = interaction.guild.id
        game = self.cog.active_games.get(guild_id)
        game_ended = game and game.get("end_time") and datetime.utcnow() > game["end_time"]
        game_active = game and game.get("active", False) and not game_ended

        if not game_active:
            await interaction.response.send_message("Game ended!", ephemeral=True)
            self.disable_all_items()
            await interaction.message.edit(view=self)
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game. Type 'start rolling' for your own!",
                                                    ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Your game is done. Type 'start rolling' for a new game.",
                                                    ephemeral=True)
            return
        if len(self.rolls) >= 5:
            await interaction.response.send_message("You've finished your 5 rolls!", ephemeral=True)
            return
        roll = random.randint(1, 10)
        self.rolls.append(roll)
        if len(self.rolls) == 5:
            score = sum(self.rolls)
            self.finished = True
            self.cog.update_leaderboard(self.user_id, score)
            perfect = " 🎉 Perfect!" if score == 50 else ""
            await interaction.response.send_message(
                f"Roll #{len(self.rolls)}: {roll}\nAll done! Your total: **{score}**.{perfect}", ephemeral=True
            )
            self.disable_all_items()
            await interaction.message.edit(view=self)
        else:
            await interaction.response.send_message(
                f"Roll #{len(self.rolls)}: {roll}\nCurrent total: **{sum(self.rolls)}**", ephemeral=True
            )


class PersonalRollGameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.leaderboard = load_leaderboard()
        self.active_games = {}  # server_id : {"active": bool, "end_time": datetime|None}

    def update_leaderboard(self, user_id, score):
        self.leaderboard[str(user_id)] = score
        save_leaderboard(self.leaderboard)

    def is_staff(self, member):
        return (
                any(r.id == STAFF_ROLE_ID for r in getattr(member, "roles", []))
                or member.guild_permissions.manage_guild
                or member.guild_permissions.administrator
        )

    @commands.hybrid_command(name="roll_start_game",
                             description="Host: Start a new roll game (optionally set duration in minutes)")
    async def roll_start_game(self, ctx, minutes: int = None):
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to start new games.")
            return
        server_id = ctx.guild.id
        self.active_games[server_id] = {"active": True}
        # Reset leaderboard on new game (persistently)
        self.leaderboard = {}
        save_leaderboard(self.leaderboard)
        msg = "**New roll game started!** Leaderboard has been reset.\nType **start rolling** to play."
        end_time = None
        if minutes and minutes > 0:
            end_time = datetime.utcnow() + timedelta(minutes=minutes)
            self.active_games[server_id]["end_time"] = end_time
            msg += f"\nGame will end in {minutes} minutes."
            self.bot.loop.create_task(self.auto_end_game(server_id, end_time, ctx.channel))
        await ctx.send(msg)

    async def auto_end_game(self, server_id, end_time, channel):
        seconds = max(1, int((end_time - datetime.utcnow()).total_seconds()))
        await asyncio.sleep(seconds)
        game = self.active_games.get(server_id)
        if game and game.get("active", False):
            game["active"] = False
            await channel.send("⏰ Roll game ended (timer expired)! No new rolls can be started.")

    @commands.hybrid_command(name="roll_score", description="Check your best roll score")
    async def roll_score(self, ctx):
        score = self.leaderboard.get(str(ctx.author.id))
        if score is None:
            await ctx.send("You have no recorded score. Type **start rolling** and play!")
        else:
            await ctx.send(f"Your best roll-5x score: **{score}**")

    @commands.hybrid_command(name="roll_leaderboard", description="Show roll game leaderboard")
    async def roll_leaderboard(self, ctx):
        if not self.leaderboard:
            await ctx.send("No scores yet!")
            return
        sorted_lb = sorted(self.leaderboard.items(), key=lambda kv: kv[1], reverse=True)
        text = "\n".join(
            f"<@{uid}>: {score}" for uid, score in sorted_lb
        )
        await ctx.send(f"**Leaderboard:**\n{text}")

    @commands.hybrid_command(name="roll_leaderboard_reset",
                             description="Host: Manually reset the roll game leaderboard")
    async def roll_leaderboard_reset(self, ctx):
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to reset the leaderboard.")
            return
        self.leaderboard = {}
        save_leaderboard(self.leaderboard)
        await ctx.send("Roll game leaderboard has been reset.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        # Only allow start rolling if game is active for this server
        content = message.content.strip().lower()
        if content != "start rolling":
            return
        if not message.guild:
            await message.channel.send("Rolling games can only be started in servers.")
            return
        server_id = message.guild.id
        game = self.active_games.get(server_id)
        end_time = game.get("end_time") if game else None
        now = datetime.utcnow()
        if not game or not game.get("active", False) or (end_time and now > end_time):
            await message.channel.send("No active roll game in this server. Ask a host to use /roll_start_game!",
                                       delete_after=20)
            return

        user_id = message.author.id
        time_str = format_remaining(end_time)
        panel_msg = f"{message.author.mention} started a Roll-5x Game!\nClick your button to roll up to 5 times.\nPerfect score: 50."
        if time_str:
            panel_msg += f"\n{time_str}"
        view = PersonalRollView(self, user_id, game_end_time=end_time)
        await message.channel.send(panel_msg, view=view)


async def setup(bot):
    await bot.add_cog(PersonalRollGameCog(bot))