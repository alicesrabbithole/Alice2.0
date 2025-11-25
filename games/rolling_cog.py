import discord
from discord.ext import commands
import random
import os
import json
import asyncio
from datetime import datetime, timedelta
from utils.checks import STAFF_ROLE_ID

DB_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'roll_leaderboard.json')
MAX_ROLLS = 10

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
        self.game_end_time = game_end_time
        self.rolls = []
        self.finished = False
        # Add restart button, starts disabled; will enable on finish
        self.restart_btn = discord.ui.Button(
            label="Restart Game", style=discord.ButtonStyle.success, disabled=True
        )
        self.restart_btn.callback = self.restart_callback
        self.add_item(self.restart_btn)

    def build_panel_message(self, member):
        panel = f"{member.mention}'s Roll-{MAX_ROLLS}x Game!\n"
        if self.rolls:
            panel += f"Rolls: {' '.join(map(str, self.rolls))}\n"
            panel += f"Current total: **{sum(self.rolls)}**\n"
        else:
            panel += "Click 🎲 to start!\n"
        panel += f"Perfect score: {MAX_ROLLS * 10}."
        if self.game_end_time:
            panel += f"\n{format_remaining(self.game_end_time)}"
        if self.finished:
            score = sum(self.rolls)
            perfect = " 🎉 Perfect!" if score == MAX_ROLLS * 10 else ""
            panel += f"\n**DONE! Your total: {score}{perfect}**"
        return panel

    @discord.ui.button(label="Roll 1-10 🎲", style=discord.ButtonStyle.primary)
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        game = self.cog.active_games.get(guild_id)
        game_ended = game and game.get("end_time") and datetime.utcnow() > game["end_time"]
        game_active = game and game.get("active", False) and not game_ended

        if not game_active:
            await interaction.response.send_message("Game ended!", ephemeral=True)
            self.disable_all_items()
            await self.edit_panel(interaction)
            return

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game. Type 'start rolling' for your own!", ephemeral=True)
            return

        if self.finished:
            await interaction.response.send_message("Your game is done. Hit 'Restart Game' to play again.", ephemeral=True)
            return

        if len(self.rolls) >= MAX_ROLLS:
            await interaction.response.send_message("You've finished your rolls!", ephemeral=True)
            return

        roll = random.randint(1, 10)
        self.rolls.append(roll)

        if len(self.rolls) == MAX_ROLLS:
            self.finished = True
            score = sum(self.rolls)
            self.cog.update_leaderboard(self.user_id, score)
            # Edit panel: disable roll button, enable restart
            self.children[0].disabled = True  # Roll button
            self.children[0].style = discord.ButtonStyle.secondary  # gray
            self.restart_btn.disabled = False
            await self.edit_panel(interaction)
        else:
            await self.edit_panel(interaction)

    async def restart_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your panel.", ephemeral=True)
            return
        # Reset state, enable roll button, disable restart, blank rolls
        self.rolls = []
        self.finished = False
        self.children[0].disabled = False
        self.children[0].style = discord.ButtonStyle.primary
        self.restart_btn.disabled = True
        await self.edit_panel(interaction)

    async def edit_panel(self, interaction):
        await interaction.response.edit_message(
            content=self.build_panel_message(interaction.user), view=self
        )

class RollingCog(commands.Cog):
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

    @commands.hybrid_command(name="roll_start_game", description="Host: Start a new roll game (optional minutes)")
    async def roll_start_game(self, ctx, minutes: int = None):
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to start new games.")
            return
        server_id = ctx.guild.id
        self.active_games[server_id] = {"active": True}
        self.leaderboard = {}
        save_leaderboard(self.leaderboard)
        msg = f"**New roll game started!** Leaderboard reset. Type **start rolling** to play ({MAX_ROLLS} rolls)."
        end_time = None
        if minutes and minutes > 0:
            end_time = datetime.utcnow() + timedelta(minutes=minutes)
            self.active_games[server_id]["end_time"] = end_time
            msg += f"\nGame ends in {minutes} minutes."
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
            await ctx.send(f"Your best roll-{MAX_ROLLS}x score: **{score}**")

    @commands.hybrid_command(name="roll_leaderboard", description="Show roll game leaderboard")
    async def roll_leaderboard(self, ctx):
        if not self.leaderboard:
            await ctx.send("No scores yet!")
            return
        sorted_lb = sorted(self.leaderboard.items(), key=lambda kv: kv[1], reverse=True)
        text = "\n".join(f"<@{uid}>: {score}" for uid, score in sorted_lb)
        await ctx.send(f"**Leaderboard:**\n{text}")

    @commands.hybrid_command(name="roll_leaderboard_reset", description="Host: Reset the roll game leaderboard")
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
            await message.channel.send(
                "No active roll game in this server. Ask a host to use /roll_start_game!",
                delete_after=20
            )
            return
        user_id = message.author.id
        view = PersonalRollView(self, user_id, game_end_time=end_time)
        await message.channel.send(view.build_panel_message(message.author), view=view)

async def setup(bot):
    await bot.add_cog(RollingCog(bot))