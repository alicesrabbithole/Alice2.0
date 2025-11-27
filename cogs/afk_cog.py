import discord
from discord.ext import commands
import json
from datetime import datetime, timezone

UTILITIES_PATH = "utilities.json"

def load_utilities():
    try:
        with open(UTILITIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_utilities(data):
    with open(UTILITIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def how_long_ago(iso_str):
    """Return a string such as '2 hr 5 min' or '7 min' for an UTC iso timestamp."""
    try:
        then = datetime.fromisoformat(iso_str)
        now = datetime.utcnow()
        delta = now - then
        mins = delta.total_seconds() // 60
        hrs = int(mins // 60)
        mins = int(mins % 60)
        if hrs > 0:
            return f"{hrs} hr{'s' if hrs > 1 else ''} {mins} min"
        else:
            return f"{mins} min"
    except Exception:
        return "unknown"

class AFKCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="afk", description="Set yourself as AFK with a reason")
    async def afk(self, ctx, *, reason: str = "AFK"):
        data = load_utilities()
        if "afk" not in data:
            data["afk"] = {}
        data["afk"][str(ctx.author.id)] = {
            "reason": reason,
            "since": datetime.utcnow().isoformat(timespec="seconds")
        }
        save_utilities(data)
        await ctx.send(f"You're now AFK: {reason}", ephemeral=True)

    @commands.hybrid_command(name="back", description="Remove your AFK status")
    async def back(self, ctx):
        data = load_utilities()
        if "afk" in data and str(ctx.author.id) in data["afk"]:
            del data["afk"][str(ctx.author.id)]
            save_utilities(data)
            await ctx.send("Welcome back! Your AFK status was removed.", ephemeral=True)
        else:
            await ctx.send("You are not marked as AFK.", ephemeral=True)

    @commands.hybrid_command(name="afklist", description="List all currently AFK members in this server.")
    async def afklist(self, ctx):
        data = load_utilities()
        afk_data = data.get("afk", {})
        members = [(ctx.guild.get_member(int(uid)), entry) for uid, entry in afk_data.items()]
        # Only show members still in the server
        active_afks = [(m, entry) for m, entry in members if m]
        if not active_afks:
            await ctx.send("No one is AFK right now.", ephemeral=True)
            return
        lines = []
        for member, entry in active_afks:
            ago = how_long_ago(entry.get("since", ""))
            reason = entry.get("reason", "AFK")
            lines.append(f"{member.mention} — **{reason}** (*{ago} ago*)")
        embed = discord.Embed(
            title="Currently AFK members",
            description="\n".join(lines),
            color=0x58d68d
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        data = load_utilities()
        afk_data = data.get("afk", {})
        mentioned_afks = []
        for user in message.mentions:
            entry = afk_data.get(str(user.id))
            if entry:
                ago = how_long_ago(entry.get("since", ""))
                reason = entry.get("reason")
                mentioned_afks.append(f"{user.mention} is AFK: {reason} (*{ago} ago*)")
        if mentioned_afks and not message.mention_everyone:
            await message.channel.send("\n".join(mentioned_afks))

async def setup(bot):
    await bot.add_cog(AFKCog(bot))