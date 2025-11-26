import discord
from discord.ext import commands
import json
from datetime import datetime

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
                since = entry.get("since")
                reason = entry.get("reason")
                mentioned_afks.append(f"{user.mention} is AFK: {reason} (since {since})")
        # Notify in the channel if any AFK users are mentioned
        if mentioned_afks and not message.mention_everyone:
            await message.channel.send("\n".join(mentioned_afks))

async def setup(bot):
    await bot.add_cog(AFKCog(bot))