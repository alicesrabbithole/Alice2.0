import discord
from discord.ext import commands
import json
from datetime import datetime, timedelta

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

def get_message_counts():
    data = load_utilities()
    return data.setdefault("message_counts", {})

def save_message_counts(msg_counts):
    data = load_utilities()
    data["message_counts"] = msg_counts
    save_utilities(data)

def global_leaderboard(msg_counts, period_days=1):
    today = datetime.utcnow().date()
    leaderboard = []
    for user_id, counts in msg_counts.items():
        msg_count = 0
        for i in range(period_days):
            d = (today - timedelta(days=i)).isoformat()
            msg_count += counts.get(d, 0)
        if msg_count > 0:
            leaderboard.append((user_id, msg_count))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    return leaderboard

def leaderboard_page(leaderboard, page, page_size):
    start = page * page_size
    end = start + page_size
    return leaderboard[start:end]

class GlobalLeaderboardView(discord.ui.View):
    def __init__(self, bot, leaderboard, period, period_type, invoker, page_size=10):
        super().__init__(timeout=120)
        self.bot = bot
        self.leaderboard = leaderboard
        self.period = period
        self.period_type = period_type
        self.page_size = page_size
        self.page = 0
        self.invoker = invoker

    async def show_page(self, interaction):
        max_page = (len(self.leaderboard) - 1) // self.page_size
        if self.page < 0 or self.page > max_page:
            await interaction.response.send_message(
                f"Error: That leaderboard page doesn't exist.",
                ephemeral=True, delete_after=3
            )
            return

        page_data = leaderboard_page(self.leaderboard, self.page, self.page_size)
        if not page_data:
            desc = "_No messages recorded for the selected period._"
        else:
            lines = []
            for i, (user_id, count) in enumerate(page_data):
                uid = int(user_id)
                member = None
                for guild in self.bot.guilds:
                    m = guild.get_member(uid)
                    if m:
                        member = m
                        break
                user = member if member else self.bot.get_user(uid)
                name = member.nick if member and member.nick else (user.name if user else f"User {uid}")
                lines.append(f"**#{self.page * self.page_size + i + 1}** [{name}](https://discord.com/users/{uid}) — `{count}` messages")
            desc = "\n".join(lines)

        embed = discord.Embed(
            title=f"🌐 Global Leaderboard: Past {self.period} {self.period_type.capitalize()}{'s' if self.period != 1 else ''} (Page {self.page + 1})",
            description=desc,
            color=0x9b59b6
        )
        if page_data:
            uid = int(page_data[0][0])
            member = None
            for guild in self.bot.guilds:
                m = guild.get_member(uid)
                if m:
                    member = m
                    break
            user = member if member else self.bot.get_user(uid)
            avatar_url = user.avatar.url if user and user.avatar else (member.avatar.url if member and member.avatar else None)
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⬅ Prev", style=discord.ButtonStyle.primary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.invoker:
            await interaction.response.send_message(
                "You can only paginate your own leaderboard view.", ephemeral=True, delete_after=4
            )
            return
        self.page -= 1
        await self.show_page(interaction)

    @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.invoker:
            await interaction.response.send_message(
                "You can only paginate your own leaderboard view.", ephemeral=True, delete_after=4
            )
            return
        self.page += 1
        await self.show_page(interaction)

class MessageCounterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        msg_counts = get_message_counts()
        user_id = str(message.author.id)
        today_str = datetime.utcnow().date().isoformat()
        user_data = msg_counts.setdefault(user_id, {})
        user_data[today_str] = user_data.get(today_str, 0) + 1
        save_message_counts(msg_counts)

    @commands.hybrid_command(name="globalleaderboard", description="🌐 Top message senders, paginated, past X days (across all servers).")
    async def globalleaderboard(self, ctx, days: int = 1):
        msg_counts = get_message_counts()
        leaderboard = global_leaderboard(msg_counts, period_days=days)
        view = GlobalLeaderboardView(self.bot, leaderboard, days, "day", invoker=ctx.author)
        page_data = leaderboard_page(leaderboard, 0, 10)
        if not page_data:
            desc = "_No messages recorded for the selected period._"
        else:
            lines = []
            for i, (user_id, count) in enumerate(page_data):
                uid = int(user_id)
                member = None
                for guild in self.bot.guilds:
                    m = guild.get_member(uid)
                    if m:
                        member = m
                        break
                user = member if member else self.bot.get_user(uid)
                name = member.nick if member and member.nick else (user.name if user else f"User {uid}")
                lines.append(f"**#{i + 1}** [{name}](https://discord.com/users/{uid}) — `{count}` messages")
            desc = "\n".join(lines)
        embed = discord.Embed(
            title=f"🌐 Global Leaderboard: Past {days} Day{'s' if days != 1 else ''} (Page 1)",
            description=desc,
            color=0x9b59b6
        )
        if page_data:
            uid = int(page_data[0][0])
            member = None
            for guild in self.bot.guilds:
                m = guild.get_member(uid)
                if m:
                    member = m
                    break
            user = member if member else self.bot.get_user(uid)
            avatar_url = user.avatar.url if user and user.avatar else (member.avatar.url if member and member.avatar else None)
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="globalweekleaderboard", description="🌐 Top message senders, paginated, past X weeks (across all servers).")
    async def globalweekleaderboard(self, ctx, weeks: int = 1):
        msg_counts = get_message_counts()
        leaderboard = global_leaderboard(msg_counts, period_days=weeks * 7)
        view = GlobalLeaderboardView(self.bot, leaderboard, weeks, "week", invoker=ctx.author)
        page_data = leaderboard_page(leaderboard, 0, 10)
        if not page_data:
            desc = "_No messages recorded for the selected period._"
        else:
            lines = []
            for i, (user_id, count) in enumerate(page_data):
                uid = int(user_id)
                member = None
                for guild in self.bot.guilds:
                    m = guild.get_member(uid)
                    if m:
                        member = m
                        break
                user = member if member else self.bot.get_user(uid)
                name = member.nick if member and member.nick else (user.name if user else f"User {uid}")
                lines.append(f"**#{i + 1}** [{name}](https://discord.com/users/{uid}) — `{count}` messages")
            desc = "\n".join(lines)
        embed = discord.Embed(
            title=f"🌐 Global Leaderboard: Past {weeks} Week{'s' if weeks != 1 else ''} (Page 1)",
            description=desc,
            color=0x9b59b6
        )
        if page_data:
            uid = int(page_data[0][0])
            member = None
            for guild in self.bot.guilds:
                m = guild.get_member(uid)
                if m:
                    member = m
                    break
            user = member if member else self.bot.get_user(uid)
            avatar_url = user.avatar.url if user and user.avatar else (member.avatar.url if member and member.avatar else None)
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
        await ctx.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(MessageCounterCog(bot))