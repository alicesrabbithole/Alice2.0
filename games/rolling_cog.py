import discord
from discord.ext import commands
import random
import os
import json
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from utils.checks import STAFF_ROLE_ID

DB_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'roll_leaderboard.json')
MAX_ROLLS = 10

def load_leaderboards():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_leaderboards(lb):
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

def pretty_rolls(rolls):
    # Show dice emoji, big bold numbers, separated by -
    return " - ".join(f"**{x}**" for x in rolls) if rolls else ""

class PersonalRollView(discord.ui.View):
    def __init__(self, cog, user_id, channel_id, game_end_time=None):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.game_end_time = game_end_time
        self.rolls = []
        self.finished = False
        # Add restart button, starts disabled; will enable on finish
        self.restart_btn = discord.ui.Button(
            label="Restart Game", style=discord.ButtonStyle.secondary, disabled=True
        )
        self.restart_btn.callback = self.restart_callback
        self.add_item(self.restart_btn)

    def build_panel_message(self, member):
        # Get only this user's best score in this channel
        scores = self.cog.leaderboards.get(str(self.channel_id), {})
        user_best = scores.get(str(self.user_id), '-')
        panel = f"__{member.mention}'s rolls:__\n"
        # Page break line for clarity
        panel += pretty_rolls(self.rolls) + "\n"
        # Page break line for clarity
        panel += "──────────────────────────\n"
        score_str = f"Current total: {sum(self.rolls) if self.rolls else 0}"
        best_str = f"Best score: {user_best}"
        panel += f"{score_str}   |   {best_str}"
        return panel

    @discord.ui.button(label="Roll 1-10 🎲", style=discord.ButtonStyle.secondary)
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel_id = interaction.channel.id
        game = self.cog.active_games.get(channel_id)
        game_ended = game and game.get("end_time") and datetime.utcnow() > game["end_time"]
        game_active = game and game.get("active", False) and not game_ended

        if not game_active:
            await interaction.response.send_message("Game ended!", ephemeral=True)
            self.disable_all_items()
            await self.edit_panel(interaction)
            return

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game. Type 'start rolling' in this channel for your own!", ephemeral=True)
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
            self.cog.update_leaderboard(self.channel_id, self.user_id, score)
            # Edit panel: disable roll button, enable restart
            self.children[0].disabled = True  # Roll button
            self.children[0].style = discord.ButtonStyle.secondary
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
        self.children[0].style = discord.ButtonStyle.secondary
        self.restart_btn.disabled = True
        await self.edit_panel(interaction)

    async def edit_panel(self, interaction):
        await interaction.response.edit_message(
            content=self.build_panel_message(interaction.user), view=self
        )

class RollingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.leaderboards = load_leaderboards()  # channel_id: {user_id: score}
        self.active_games = {}  # channel_id : {"active": bool, "end_time": datetime|None}
        self.last_host = {}     # channel_id : host_id

    def update_leaderboard(self, channel_id, user_id, score):
        cid = str(channel_id)
        uid = str(user_id)
        if cid not in self.leaderboards:
            self.leaderboards[cid] = {}
        # Only update if it's the user's best score
        old_best = self.leaderboards[cid].get(uid, 0)
        if score > old_best:
            self.leaderboards[cid][uid] = score
            save_leaderboards(self.leaderboards)

    def is_staff(self, member):
        return (
            any(r.id == STAFF_ROLE_ID for r in getattr(member, "roles", []))
            or member.guild_permissions.manage_guild
            or member.guild_permissions.administrator
        )

    @commands.hybrid_command(name="roll_start", description="Host: Start a new roll game (optional minutes). Channel-specific.")
    async def roll_start_game(self, ctx, minutes: int = None):
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to start new games.")
            return
        channel_id = ctx.channel.id
        self.active_games[channel_id] = {"active": True}
        self.last_host[channel_id] = ctx.author.id
        self.leaderboards[str(channel_id)] = {}
        save_leaderboards(self.leaderboards)
        msg = (f"**A new game has started! Perfect score is 100. \n"f"** Type **start rolling** to play.")
        end_time = None
        if minutes and minutes > 0:
            end_time = datetime.utcnow() + timedelta(minutes=minutes)
            self.active_games[channel_id]["end_time"] = end_time
            msg += f"\nGame ends in {minutes} minutes."
            self.bot.loop.create_task(self.auto_end_game(channel_id, end_time, ctx.channel))
        await ctx.send(msg)

    async def auto_end_game(self, channel_id, end_time, channel):
        seconds = max(1, int((end_time - datetime.utcnow()).total_seconds()))
        await asyncio.sleep(seconds)
        game = self.active_games.get(channel_id)
        host_id = self.last_host.get(channel_id)
        if game and game.get("active", False):
            game["active"] = False
            leaderboard_msg = await self.post_leaderboard(channel_id, channel)
            host_tag = f"<@{host_id}>" if host_id else ""
            await channel.send(f"⏰ {host_tag}, your game has ended! {host_tag}")

    async def post_leaderboard(self, channel_id, channel):
        scores = self.leaderboards.get(str(channel_id), {})
        if not scores:
            await channel.send("No scores for this game!")
            return None
        sorted_lb = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        text = "\n".join(f"<@{uid}>: {score}" for uid, score in sorted_lb)
        msg = await channel.send(f"**Final Leaderboard:**\n{text}")
        return msg

    @commands.hybrid_command(name="roll_leaderboard", description="Show roll game leaderboard. Channel-specific.")
    async def roll_leaderboard(self, ctx):
        scores = self.leaderboards.get(str(ctx.channel.id), {})
        if not scores:
            await ctx.send("No scores yet in this channel!")
            return
        sorted_lb = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        text = "\n".join(f"<@{uid}>: {score}" for uid, score in sorted_lb)
        await ctx.send(f"**Leaderboard for this channel:**\n{text}")

    @commands.hybrid_command(name="roll_leaderboard_reset", description="Host: Reset the roll game leaderboard for this channel")
    async def roll_leaderboard_reset(self, ctx):
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to reset the leaderboard.")
            return
        self.leaderboards[str(ctx.channel.id)] = {}
        save_leaderboards(self.leaderboards)
        await ctx.send("Roll game leaderboard has been reset for this channel.")

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
        channel_id = message.channel.id
        game = self.active_games.get(channel_id)
        end_time = game.get("end_time") if game else None
        now = datetime.utcnow()
        if not game or not game.get("active", False) or (end_time and now > end_time):
            await message.channel.send(
                "No active roll game in this channel. Ask a host to use /roll_start!",
                delete_after=20
            )
            return
        user_id = message.author.id
        view = PersonalRollView(self, user_id, channel_id, game_end_time=end_time)
        await message.channel.send(view.build_panel_message(message.author), view=view)

async def setup(bot):
    await bot.add_cog(RollingCog(bot))