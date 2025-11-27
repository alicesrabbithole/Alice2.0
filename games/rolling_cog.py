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

def load_leaderboards():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_leaderboards(lb):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(lb, f)

def pretty_rolls(rolls):
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
        self.restart_btn = discord.ui.Button(
            label="Restart Game", style=discord.ButtonStyle.secondary, disabled=True
        )
        self.restart_btn.callback = self.restart_callback
        self.add_item(self.restart_btn)

    def build_panel_message(self, member):
        scores = self.cog.leaderboards.get(str(self.channel_id), {})
        score_to_beat = max(scores.values()) if scores else '-'
        panel = f"## __{member.mention}'s rolls:__\n"
        panel += pretty_rolls(self.rolls) + "\n"
        panel += "──────────────────────────\n"
        score_str = f"Current total: {sum(self.rolls) if self.rolls else 0}"
        beat_str = f"Score to beat: {score_to_beat}"
        panel += f"{score_str}   |   {beat_str}"
        return panel

    @discord.ui.button(label="Roll 1-10 🎲", style=discord.ButtonStyle.secondary)
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game. Type 'start rolling' in this channel for your own!", ephemeral=True)
            return

        game = self.cog.active_games.get(self.channel_id)
        now = datetime.utcnow()
        end_time = game.get("end_time") if game else None
        game_active = game and game.get("active", False) and (not end_time or now <= end_time)
        if not game_active:
            await interaction.response.send_message("Game ended!", ephemeral=True)
            self.disable_all_items()
            await self.edit_panel(interaction)
            self.cog.open_panels.pop((self.channel_id, self.user_id), None)
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
            self.children[0].disabled = True
            self.restart_btn.disabled = False
            await self.edit_panel(interaction)
            # Clean up panel tracking
            self.cog.open_panels.pop((self.channel_id, self.user_id), None)
        else:
            await self.edit_panel(interaction)

    async def restart_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your panel.", ephemeral=True)
            return
        self.rolls = []
        self.finished = False
        self.children[0].disabled = False
        self.restart_btn.disabled = True
        await self.edit_panel(interaction)
        # Re-track the panel ID
        self.cog.open_panels[(self.channel_id, self.user_id)] = interaction.message.id

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
        self.open_panels = {}   # (channel_id, user_id): message_id

    def update_leaderboard(self, channel_id, user_id, score):
        cid = str(channel_id)
        uid = str(user_id)
        if cid not in self.leaderboards:
            self.leaderboards[cid] = {}
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
        channel_id = ctx.channel.id
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to start new games.")
            return
        game = self.active_games.get(channel_id)
        now = datetime.utcnow()
        end_time = game.get("end_time") if game else None
        if game and game.get("active", False) and (not end_time or now <= end_time):
            await ctx.send("A rolling game is already running in this channel! Wait for it to finish before starting a new one.")
            return
        self.active_games[channel_id] = {"active": True}
        self.last_host[channel_id] = ctx.author.id
        self.leaderboards[str(channel_id)] = {}
        save_leaderboards(self.leaderboards)
        # Remove any stale open panels for this channel/game
        for panel_key in list(self.open_panels):
            if panel_key[0] == channel_id:
                self.open_panels.pop(panel_key, None)
        msg = "**A new game has started! Perfect score is 100.**\nType **start rolling** to play."
        if minutes and minutes > 0:
            end_time = now + timedelta(minutes=minutes)
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
            sorted_lb = sorted(self.leaderboards.get(str(channel_id), {}).items(), key=lambda kv: kv[1], reverse=True)
            leaderboard_text = "\n".join(f"<@{uid}>: {score}" for uid, score in sorted_lb) if sorted_lb else "No scores for this game!"
            host_tag = f"<@{host_id}>" if host_id else ""
            embed = discord.Embed(
                title="Game Ended!",
                description=f"__{host_tag} - Your game has ended.__\n\n**Final Leaderboard:**\n{leaderboard_text}",
                color=discord.Color.purple()
            )
            await channel.send(embed=embed)
            # Clean up open panels for this channel
            for panel_key in list(self.open_panels):
                if panel_key[0] == channel_id:
                    self.open_panels.pop(panel_key, None)

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
        now = datetime.utcnow()
        end_time = game.get("end_time") if game else None
        if not game or not game.get("active", False) or (end_time and now > end_time):
            await message.channel.send(
                "No active roll game in this channel. Ask a host to use /roll_start!",
                delete_after=20
            )
            return
        user_id = message.author.id
        panel_key = (channel_id, user_id)
        # Check for existing panel per user/channel/game
        if panel_key in self.open_panels:
            await message.channel.send(
                f"{message.author.mention} You already have a rolling panel open for this game in this channel!",
                delete_after=10
            )
            return
        view = PersonalRollView(self, user_id, channel_id, game_end_time=end_time)
        msg = await message.channel.send(view.build_panel_message(message.author), view=view)
        self.open_panels[panel_key] = msg.id

async def setup(bot):
    await bot.add_cog(RollingCog(bot))