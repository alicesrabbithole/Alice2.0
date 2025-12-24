# Cog to log command usage to a configured log channel.
# - Logs when commands are invoked and when they complete
# - Attempts to capture the bot's immediate reply (searches for messages by the bot in the channel
#   that were created after the invocation timestamp, within a short window).
# - Works for prefix/hybrid commands; provides a fallback on_interaction for pure app commands.
from typing import Optional, Any
import logging
from datetime import timedelta, datetime, timezone

import discord
from discord.ext import commands

# Prefer to import send_log from utils.discord_logging; fall back to a local implementation
try:
    from utils.discord_logging import send_log, setup_discord_logging  # type: ignore
except Exception:
    send_log = None  # will use fallback defined below
    setup_discord_logging = None

import config  # your project config that may contain LOG_CHANNEL_ID, USAGE_* settings

logger = logging.getLogger(__name__)

# utcnow fallback (try to use discord.utils.utcnow if available)
try:
    from discord.utils import utcnow  # type: ignore
except Exception:
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)

# Local fallback send_log if the utils helper isn't available
async def _fallback_send_log(bot: commands.Bot, message: str, embed: Optional[discord.Embed] = None):
    try:
        if not getattr(config, "LOG_CHANNEL_ID", None):
            logger.debug("LOG_CHANNEL_ID not set; skipping send_log")
            return
        cid = int(config.LOG_CHANNEL_ID)
    except Exception:
        logger.debug("LOG_CHANNEL_ID invalid or missing; skipping send_log")
        return
    try:
        ch = bot.get_channel(cid)
        if ch is None:
            # try fetch as a last resort
            try:
                ch = await bot.fetch_channel(cid)
            except Exception:
                ch = None
        if ch:
            await ch.send(content=message, embed=embed)
        else:
            logger.warning("send_log: could not resolve channel %s", cid)
    except Exception:
        logger.exception("send_log: failed to send log to channel %s", cid)

# Decide which send_log to use
if send_log is None:
    _send_log = _fallback_send_log
else:
    _send_log = send_log  # type: ignore

# Configuration overrides from config.py
SKIP_ALL_PREFIX = getattr(config, "USAGE_IGNORE_ALL_PREFIX_INVOCATIONS", False)
SKIP_COMMANDS = set(getattr(config, "USAGE_IGNORED_PREFIX_COMMANDS", ["wordle", "21questions"]))

# New: commands for which we only want a minimal acknowledgement in logs (no args, no embed contents).
# Example defaults include leaderboard/gallery/mysnowman/summary variants.
QUIET_COMMANDS = set(n.lower() for n in getattr(config, "USAGE_QUIET_COMMANDS",
                                                  ["leaderboard", "gallery", "mysnowman", "summary21q", "sum21", "sled", "rumble_builds_leaderboard"]))

class UsageLoggerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._response_search_window = timedelta(seconds=5)
        # Register global before/after invoke hooks
        bot.before_invoke(self._before_any_command)
        bot.after_invoke(self._after_any_command)
        # Optionally tune discord logging
        try:
            if setup_discord_logging:
                setup_discord_logging()  # type: ignore
        except Exception:
            pass

    async def _before_any_command(self, ctx: commands.Context):
        try:
            ctx._usage_invoke_time = utcnow()
            # determine prefix vs interaction
            is_prefix = getattr(ctx, "interaction", None) is None and getattr(ctx, "message", None) is not None

            # determine command name
            cmd_name = None
            if getattr(ctx, "command", None):
                try:
                    cmd_name = ctx.command.qualified_name
                except Exception:
                    cmd_name = getattr(ctx, "invoked_with", None)
            else:
                cmd_name = getattr(ctx, "invoked_with", None)

            ctx._usage_skip = False
            if is_prefix:
                if SKIP_ALL_PREFIX:
                    ctx._usage_skip = True
                    return
                if cmd_name:
                    root = cmd_name.split()[0] if isinstance(cmd_name, str) else str(cmd_name)
                    if root in SKIP_COMMANDS:
                        ctx._usage_skip = True
                        return

            # capture readable arg string
            ctx._usage_argstr = ""
            try:
                if getattr(ctx, "message", None) and isinstance(ctx.message, discord.Message):
                    ctx._usage_argstr = ctx.message.content or ""
                else:
                    args = ctx.args[1:] if getattr(ctx, "args", None) and len(ctx.args) > 1 else ()
                    kwargs = getattr(ctx, "kwargs", {}) or {}
                    parts = []
                    if args:
                        parts.append(" ".join(str(a) for a in args))
                    if kwargs:
                        parts.append(" ".join(f"{k}={v!s}" for k, v in kwargs.items()))
                    ctx._usage_argstr = " ".join(parts)
            except Exception:
                ctx._usage_argstr = ""
        except Exception:
            logger.exception("before_invoke failure in UsageLoggerCog")

    def _is_quiet_command(self, cmdname: str) -> bool:
        """
        Determine whether the given command should be quieted in logs.
        Accepts either qualified command names or simple invoked names.
        """
        try:
            if not cmdname:
                return False
            root = str(cmdname).split()[0].lower()
            return root in QUIET_COMMANDS
        except Exception:
            return False

    async def _after_any_command(self, ctx: commands.Context):
        try:
            if getattr(ctx, "_usage_skip", False):
                return

            invoke_time = getattr(ctx, "_usage_invoke_time", utcnow())
            argstr = getattr(ctx, "_usage_argstr", "")
            user = ctx.author
            channel = getattr(ctx, "channel", None)
            cmdname = ctx.command.qualified_name if getattr(ctx, "command", None) else getattr(ctx, "invoked_with", "unknown")

            # If this command is in the quiet list, only post a minimal acknowledgement.
            if self._is_quiet_command(cmdname):
                chan_repr = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else (f"DM with {user}" if channel is None else str(channel))
                cmd_display = f"/{cmdname}" if getattr(ctx, "interaction", None) else f"{cmdname}"
                log_text = f"<@{user.id}> used {cmd_display} in {chan_repr}"
                try:
                    await _send_log(self.bot, log_text)
                except Exception:
                    logger.exception("Failed to send usage log to discord; falling back to logger")
                    logger.info(log_text)
                return

            bot_reply_text = None
            try:
                if isinstance(channel, (discord.TextChannel, discord.abc.Messageable)):
                    async for m in channel.history(limit=6, after=invoke_time, oldest_first=True):
                        if m.author and m.author.id == self.bot.user.id:
                            if m.content:
                                bot_reply_text = m.content
                                break
                            if m.embeds:
                                e = m.embeds[0]
                                bot_reply_text = f"[embed] {e.title or ''} {e.description or ''}".strip()
                                break
                            if m.attachments:
                                bot_reply_text = f"[attachment] {', '.join(a.filename for a in m.attachments)}"
                                break
            except Exception:
                logger.debug("Could not search channel history for bot reply (safe to ignore).", exc_info=True)

            chan_repr = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else (f"DM with {user}" if channel is None else str(channel))
            cmd_display = f"/{cmdname}" if getattr(ctx, "interaction", None) else f"{cmdname}"
            arg_display = f' "{argstr}"' if argstr else ""
            reply_display = f' "{bot_reply_text}"' if bot_reply_text else " (no bot reply captured)"

            log_text = f"<@{user.id}> used {cmd_display}{arg_display} in {chan_repr}{reply_display}"

            try:
                await _send_log(self.bot, log_text)
            except Exception:
                logger.exception("Failed to send usage log to discord; falling back to logger")
                logger.info(log_text)
        except Exception:
            logger.exception("after_invoke failure in UsageLoggerCog")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        try:
            if interaction.type is not discord.InteractionType.application_command:
                return
            user = interaction.user
            data = interaction.data or {}
            name = data.get("name", "unknown")

            # If this slash command is quiet, only send a minimal acknowledgement
            if self._is_quiet_command(name):
                chan = interaction.channel
                chan_repr = f"#{chan.name}" if isinstance(chan, discord.TextChannel) else str(chan)
                log_text = f"<@{user.id}> used /{name} in {chan_repr}"
                try:
                    await _send_log(self.bot, log_text)
                except Exception:
                    logger.exception("Failed to send interaction usage log")
                return

            args_display = ""
            try:
                opts = data.get("options") or []
                parts = []
                for o in opts:
                    if "value" in o:
                        parts.append(f"{o.get('name')}={o.get('value')}")
                    elif "options" in o:
                        for sub in o.get("options", []):
                            if "value" in sub:
                                parts.append(f"{sub.get('name')}={sub.get('value')}")
                args_display = " ".join(parts)
            except Exception:
                args_display = ""

            chan = interaction.channel
            chan_repr = f"#{chan.name}" if isinstance(chan, discord.TextChannel) else str(chan)
            arg_display = f' "{args_display}"' if args_display else ""
            log_text = f"<@{user.id}> used /{name}{arg_display} in {chan_repr}"

            try:
                await _send_log(self.bot, log_text)
            except Exception:
                logger.exception("Failed to send interaction usage log")
        except Exception:
            logger.exception("on_interaction failure in UsageLoggerCog")


async def setup(bot: commands.Bot):
    await bot.add_cog(UsageLoggerCog(bot))