import discord
from discord.ext import commands, tasks
import json
import os
import re
from datetime import datetime, timedelta
import pytz

DATA_DIR = "data"
REMINDERS_FILE = os.path.join(DATA_DIR, "utilities.json")
TIMEZONES_FILE = os.path.join(DATA_DIR, "user_timezones.json")

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def load_json(path, default):
    ensure_dir(os.path.dirname(path))
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump(default, f)
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        with open(path, 'w') as f:
            json.dump(default, f)
        return default

def save_json(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

class ReminderCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminders = load_json(REMINDERS_FILE, [])
        self.user_timezones = load_json(TIMEZONES_FILE, {})
        self.check_reminders.start()

    @commands.hybrid_command(name="settimezone", description="Set your timezone, e.g. /settimezone America/New_York")
    async def settimezone(self, ctx, timezone: str):
        try:
            pytz.timezone(timezone)
        except pytz.UnknownTimeZoneError:
            return await ctx.send("❌ Invalid timezone. Try `America/New_York`, `Europe/London`, `Asia/Tokyo`.", ephemeral=True)
        self.user_timezones[str(ctx.author.id)] = timezone
        save_json(TIMEZONES_FILE, self.user_timezones)
        await ctx.send(f"✅ Your timezone is set to {timezone}.", ephemeral=True)

    @commands.hybrid_command(
        name="remember",
        description="Set a reminder. E.g. /remember 10m Feed rabbit"
    )
    @discord.app_commands.describe(
        time="When? (10m, 1h, 1d, 1p, 2:30pm, 2025-11-12 15:00)",
        message="What to remember"
    )
    async def remember(self, ctx, time: str, *, message: str):
        user_id = str(ctx.author.id)
        tzname = self.user_timezones.get(user_id, "UTC")
        try:
            tz = pytz.timezone(tzname)
        except Exception:
            tz = pytz.UTC
        parsed = self.parse_time(time, tz)
        if not parsed:
            return await ctx.send(
                "❌ Invalid time! Try `10m`, `2h`, `1d`, `1p`, `2:30pm`, `15:45`, or `2025-11-12 15:45`.",
                ephemeral=True)
        remind_dt = parsed
        self.reminders.append({
            "user": user_id,
            "message": message,
            "remind_at_utc": remind_dt.astimezone(pytz.UTC).isoformat()
        })
        save_json(REMINDERS_FILE, self.reminders)
        await ctx.send(
            f"⏰ Reminder set for {remind_dt.strftime('%Y-%m-%d %H:%M:%S')} ({tzname}).",
            ephemeral=True
        )

    def parse_time(self, time_str, tz):
        now = datetime.now(tz)
        match = re.match(r'^(\d+)([mhd])$', time_str.lower())
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            delta = {"m": timedelta(minutes=num), "h": timedelta(hours=num), "d": timedelta(days=num)}[unit]
            return now + delta

        match = re.match(r'^(\d{1,2})(:(\d{2}))?\s*([ap]m?)$', time_str.lower())
        if match:
            hour = int(match.group(1))
            minute = int(match.group(3) or 0)
            ampm = match.group(4)
            if ampm.startswith("p") and hour != 12:
                hour += 12
            if ampm.startswith("a") and hour == 12:
                hour = 0
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            return dt

        match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
        if match:
            hour, minute = map(int, match.groups())
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            return dt

        match = re.match(r'^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$', time_str)
        if match:
            y, m, d, h, mi = map(int, match.groups())
            try:
                dt = tz.localize(datetime(y, m, d, h, mi))
            except Exception:
                dt = datetime(y, m, d, h, mi, tzinfo=tz)
            if dt <= now:
                return None
            return dt
        return None

    @tasks.loop(seconds=60.0)
    async def check_reminders(self):
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
        changed = False
        for rem in self.reminders[:]:
            try:
                remind_dt = datetime.fromisoformat(rem["remind_at_utc"])
            except Exception:
                continue
            if now_utc >= remind_dt:
                user = self.bot.get_user(int(rem["user"]))
                if user:
                    try:
                        await user.send(f"⏰ Reminder: {rem['message']}")
                    except Exception:
                        pass
                self.reminders.remove(rem)
                changed = True
        if changed:
            save_json(REMINDERS_FILE, self.reminders)

async def setup(bot):
    await bot.add_cog(ReminderCog(bot))