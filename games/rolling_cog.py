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

def format_timedelta(dt):
    if not dt or dt.total_seconds() < 0:
        return "ended"
    mins, secs = divmod(int(dt.total_seconds()), 60)
    hours, mins = divmod(mins, 60)
    s = []
    if hours:
        s.append(f"{hours}h")
    if mins:
        s.append(f"{mins}m")
    if secs or not s:
        s.append(f"{secs}s")
    return " ".join(s)

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
        lb = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        user_score = sum(self.rolls) if self.rolls else 0
        pos = None
        for idx, (uid, score) in enumerate(lb):
            if int(uid) == member.id:
                pos = idx + 1
                break
        # Time left
        if self.game_end_time:
            time_left = self.game_end_time - datetime.utcnow()
            time_str = format_timedelta(time_left)
        else:
            time_str = "∞"
        panel = f"## __{member.mention}'s rolls:__\n"
        panel += pretty_rolls(self.rolls) + "\n"
        panel += "──────────────────────────\n"
        score_str = f"Current total: {user_score}   |   Score to beat: {score_to_beat}"
        time_str = f"Time left: {time_str}"
        panel += f"{score_str}\n{time_str}\n"
        if pos is not None:
            panel += f"Your leaderboard position: **#{pos}**\n"
        return panel

    @discord.ui.button(label="Roll 1-10 🎲", style=discord.ButtonStyle.secondary)
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return

        game = self.cog.active_games.get(self.channel_id)
        now = datetime.utcnow()
        end_time = game.get("end_time") if game else None
        game_active = game and game.get("active", False) and (not end_time or now <= end_time)
        if not game_active:
            await interaction.response.send_message("Game ended!", ephemeral=True)
            self.disable_all_items()
            await self.edit_panel(interaction)
            self.cog.active_panels[(self.channel_id, self.user_id)]["active"] = False
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
            self.cog.active_panels[(self.channel_id, self.user_id)]["active"] = False
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
        self.cog.active_panels[(self.channel_id, self.user_id)]["active"] = True
        await self.edit_panel(interaction)

    async def edit_panel(self, interaction):
        await interaction.response.edit_message(
            content=self.build_panel_message(interaction.user), view=self
        )

class JoinGameView(discord.ui.View):
    def __init__(self, cog, channel_id, game_end_time=None):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.game_end_time = game_end_time

    @discord.ui.button(label="🐇 Hop In!", style=discord.ButtonStyle.secondary)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        panel_key = (self.channel_id, user_id)
        view = PersonalRollView(self.cog, user_id, self.channel_id, game_end_time=self.game_end_time)
        await interaction.response.send_message(
            view.build_panel_message(interaction.user),
            view=view,
            ephemeral=True
        )
        self.cog.active_panels[panel_key] = {"active": True}

class RollingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.leaderboards = load_leaderboards()  # channel_id: {user_id: score}
        self.active_games = {}  # channel_id : {"active": bool, "end_time": datetime|None}
        self.last_host = {}     # channel_id : host_id
        self.active_panels = {} # (channel_id, user_id): {"active": bool}

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

    @commands.hybrid_command(name="roll_start", description="Host: Start a new roll game (optional minutes).")
    async def roll_start_game(self, ctx, minutes: int = None):
        channel_id = ctx.channel.id
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to start new games.")
            return
        game = self.active_games.get(channel_id)
        now = datetime.utcnow()
        end_time = game.get("end_time") if game else None
        # End and cleanup any active game first
        if game and game.get("active", False):
            await self.force_end_game(channel_id, ctx.channel)

        # Always start a fresh game (reset leaderboard)
        self.active_games[channel_id] = {"active": True}
        self.last_host[channel_id] = ctx.author.id
        self.leaderboards[str(channel_id)] = {}
        save_leaderboards(self.leaderboards)
        # Cleanup all panels for this channel
        for panel_key in list(self.active_panels):
            if panel_key[0] == channel_id:
                self.active_panels.pop(panel_key, None)
        # Alice theme
        embed = discord.Embed(
            title="A New Wonderland Rolling Game Has Begun!",
            description=(
                "Hello, dreamers! The perfect score is **100**.\n"
                "Ready for adventure? Click below for your private panel <a:whiterabbit_gif:1328740902432276500>"
            ),
            color=discord.Color.purple()
        )
        if ctx.author.avatar:
            embed.set_thumbnail(
                url="https://cdn.discordapp.com/attachments/1309962373846532159/1443432386732757123/Aiwdice.png?ex=69290caa&is=6927bb2a&hm=dded477a1d04745957dd25bbe5b0c84b8faff9cbe54e8851655caaf15d2202b0&")
            embed.set_footer(text="Good luck!")
            if minutes and minutes > 0:
                end_time = now + timedelta(minutes=minutes)
                self.active_games[channel_id]["end_time"] = end_time
                embed.add_field(name="Game Ends", value=f"{minutes} minutes", inline=False)
                self.bot.loop.create_task(self.auto_end_game(channel_id, end_time, ctx.channel))
            view = JoinGameView(self, channel_id, game_end_time=end_time)
            await ctx.send(embed=embed, view=view)

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
            # Mark all panels in this channel inactive
            for panel_key in list(self.active_panels):
                if panel_key[0] == channel_id:
                    self.active_panels[panel_key]["active"] = False

    async def force_end_game(self, channel_id, channel):
        host_id = self.last_host.get(channel_id)
        self.active_games[channel_id] = {"active": False}
        sorted_lb = sorted(self.leaderboards.get(str(channel_id), {}).items(), key=lambda kv: kv[1], reverse=True)
        leaderboard_text = "\n".join(
            f"<@{uid}>: {score}" for uid, score in sorted_lb) if sorted_lb else "No scores for this game!"
        host_tag = f"<@{host_id}>" if host_id else ""
        embed = discord.Embed(
            title="Game Ended!",
            description=f"__Final Leaderboard:__\n{leaderboard_text}\n\nGame ended {host_tag}",
            color=discord.Color.purple()
        )
        await channel.send(embed=embed)
        # Mark all panels in this channel inactive
        for panel_key in list(self.active_panels):
            if panel_key[0] == channel_id:
                self.active_panels[panel_key]["active"] = False

    @commands.hybrid_command(name="roll_leaderboard", description="Show roll game leaderboard.")
    async def roll_leaderboard(self, ctx):
        if not self.is_staff(ctx.author):  # restrict to staff only
            await ctx.send("You do not have permission to view the leaderboard.", ephemeral=True)
            return
        scores = self.leaderboards.get(str(ctx.channel.id), {})
        if not scores:
            await ctx.send("No scores yet in this channel!")
            return
        sorted_lb = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        lines = []
        for idx, (uid, score) in enumerate(sorted_lb, 1):
            member = ctx.guild.get_member(int(uid))
            if member:
                entry = f"**#{idx}** {member.mention}: {score}"
            else:
                entry = f"**#{idx}** <@{uid}>: {score}"
            lines.append(entry)
        embed = discord.Embed(
            title="__Top Rolling Scores:__",
            description="\n".join(lines),
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roll_leaderboard_reset", description="Host: Reset the roll game leaderboard for this channel")
    async def roll_leaderboard_reset(self, ctx):
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to reset the leaderboard.")
            return
        self.leaderboards[str(ctx.channel.id)] = {}
        save_leaderboards(self.leaderboards)
        await ctx.send("Roll game leaderboard has been reset for this channel.")

    @commands.hybrid_command(name="roll_reset",
                             description="Host: Immediately end any rolling game and clear all panels and scores for this channel.")
    async def roll_reset(self, ctx):
        channel_id = ctx.channel.id
        if not self.is_staff(ctx.author):
            await ctx.send("You do not have permission to reset the game in this channel.")
            return
        await self.force_end_game(channel_id, ctx.channel)
        self.leaderboards[str(channel_id)] = {}
        save_leaderboards(self.leaderboards)
        await ctx.send("The rolling game and leaderboard have been fully reset in this channel.")

async def setup(bot):
    await bot.add_cog(RollingCog(bot))