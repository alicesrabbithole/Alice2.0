# Cog to log command usage to the configured log channel.
# - Logs when commands are invoked and when they complete
# - Attempts to capture the bot's immediate reply (searches for messages by the bot in the channel
#   that were created after the invocation timestamp, within a short window).
# - Works for text (prefix) and hybrid commands. For purely app_commands there is a fallback via on_interaction.
#
# Place this file at cogs/usage_logger_cog.py and ensure utils/discord_logging.send_log is available.

from typing import Optional
import logging
import discord
from discord.ext import commands
from typing import Optional
import config
from datetime import timedelta, datetime, timezone
try:
    # Prefer discord.utils.utcnow when available (keeps parity with discord.py behavior)
    from discord.utils import utcnow  # type: ignore
except Exception:
    # Fallback implementation used when discord.utils.utcnow is unavailable to the analyzer.
    def utcnow() -> datetime:
        """Return timezone-aware UTC datetime similar to discord.utils.utcnow()."""
        return datetime.now(timezone.utc)

logger = logging.getLogger(__name__)

def setup_discord_logging():
    """Call after root logging is configured to reduce noisy discord internals."""
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.INFO)

async def send_log(bot: commands.Bot, message: str, embed: Optional[discord.Embed] = None):
    """Send a message or embed to the configured log channel. Safe: logs exceptions and returns."""
    if not getattr(config, "LOG_CHANNEL_ID", None):
        logger.warning("LOG_CHANNEL_ID not set in config.py; skipping send_log()")
        return
    try:
        ch = bot.get_channel(int(config.LOG_CHANNEL_ID))
        if ch is None:
            # fallback to fetch (may be needed during startup)
            ch = await bot.fetch_channel(int(config.LOG_CHANNEL_ID))
        if ch:
            await ch.send(content=message, embed=embed)
        else:
            logger.warning("Could not resolve log channel %s", config.LOG_CHANNEL_ID)
    except (discord.Forbidden, discord.NotFound):
        logger.exception("No permission or log channel not found for %s", config.LOG_CHANNEL_ID)
    except Exception:
        logger.exception("Failed to send log message to channel %s", config.LOG_CHANNEL_ID)

# Configuration: read overrides from config.py (optional)
# If True, skip logging for every prefix-invoked command.
SKIP_ALL_PREFIX = getattr(config, "USAGE_IGNORE_ALL_PREFIX_INVOCATIONS", False)
# Otherwise, SKIP_COMMANDS lists command names (as registered) to ignore when invoked via prefix.
# Example: ["wordle", "21questions"]
SKIP_COMMANDS = set(getattr(config, "USAGE_IGNORED_PREFIX_COMMANDS", ["wordle", "21questions"]))


class UsageLoggerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # how far back to search for the bot response (seconds)
        self._response_search_window = timedelta(seconds=5)

        # register global before/after invoke handlers if running under a Bot instance
        # these decorators are also applied when the cog is loaded, but ensure idempotence
        bot.before_invoke(self._before_any_command)
        bot.after_invoke(self._after_any_command)

    async def _before_any_command(self, ctx: commands.Context):
        """
        Called before any command (prefix/hybrid) runs. Store invocation time and args for later logging.
        Also marks prefix commands to skip logging based on configuration.
        """
        try:
            # mark invocation time
            ctx._usage_invoke_time = utcnow()

            # Detect prefix invocation (no interaction attached) vs slash/hybrid (interaction present)
            is_prefix = getattr(ctx, "interaction", None) is None and getattr(ctx, "message", None) is not None

            # Determine the effective command name for lookup
            cmd_name = None
            try:
                if getattr(ctx, "command", None):
                    cmd_name = ctx.command.qualified_name
                else:
                    cmd_name = getattr(ctx, "invoked_with", None)
            except Exception:
                cmd_name = getattr(ctx, "invoked_with", None)

            # Decide whether to skip logging for this invocation
            ctx._usage_skip = False
            if is_prefix:
                if SKIP_ALL_PREFIX:
                    ctx._usage_skip = True
                    return
                # Normalize and check against configured skip list
                if cmd_name:
                    # command names can be "group sub" for nested commands; compare root name
                    root = cmd_name.split()[0] if isinstance(cmd_name, str) else str(cmd_name)
                    if root in SKIP_COMMANDS:
                        ctx._usage_skip = True
                        return

            # capture a readable arg summary (skip internal ctx attributes)
            try:
                ctx._usage_argstr = ""
                if getattr(ctx, "command", None):
                    # prefer the raw invoked string when available (prefix commands)
                    if getattr(ctx, "message", None) and isinstance(ctx.message, discord.Message):
                        # for prefix commands, this is the raw message content
                        ctx._usage_argstr = ctx.message.content or ""
                    else:
                        # fall back to arguments tuple representation
                        try:
                            args = ctx.args[1:] if ctx.args and len(ctx.args) > 1 else ()
                            kwargs = ctx.kwargs or {}
                            argparts = []
                            if args:
                                argparts.append(" ".join(str(a) for a in args))
                            if kwargs:
                                argparts.append(" ".join(f"{k}={v!s}" for k, v in kwargs.items()))
                            ctx._usage_argstr = " ".join(argparts)
                        except Exception:
                            ctx._usage_argstr = ""
            except Exception:
                ctx._usage_argstr = ""
        except Exception:
            logger.exception("before_invoke failure in UsageLoggerCog")

    async def _after_any_command(self, ctx: commands.Context):
        """
        Called after any command completes. Attempt to find the bot's reply in the channel right
        after invocation and send a formatted log to the configured log channel.
        """
        try:
            # Respect skip marker set in before_invoke
            if getattr(ctx, "_usage_skip", False):
                return

            invoke_time = getattr(ctx, "_usage_invoke_time", utcnow())
            argstr = getattr(ctx, "_usage_argstr", "")
            user = ctx.author
            channel = ctx.channel if getattr(ctx, "channel", None) else None
            cmdname = ctx.command.qualified_name if getattr(ctx, "command", None) else getattr(ctx, "invoked_with", "unknown")

            # Try to find the bot's response messages in the channel that happened after invoke_time.
            bot_reply_text = None
            try:
                if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.abc.Messageable):
                    search_after = invoke_time
                    async for m in channel.history(limit=6, after=search_after, oldest_first=True):
                        if m.author and m.author.id == self.bot.user.id:
                            if m.content:
                                bot_reply_text = m.content
                                break
                            elif m.embeds:
                                e = m.embeds[0]
                                bot_reply_text = f"[embed] {e.title or ''} {e.description or ''}".strip()
                                break
                            elif m.attachments:
                                bot_reply_text = f"[attachment] {', '.join(a.filename for a in m.attachments)}"
                                break
            except Exception:
                logger.debug("Could not search channel history for bot reply (safe to ignore).", exc_info=True)

            # Build the log message. Keep it compact and readable.
            chan_repr = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else (f"DM with {user}" if channel is None else str(channel))
            cmd_display = f"/{cmdname}" if isinstance(ctx, discord.ApplicationContext) or getattr(ctx, "interaction", None) else f"{cmdname}"
            arg_display = f' "{argstr}"' if argstr else ""
            reply_display = f' "{bot_reply_text}"' if bot_reply_text else " (no bot reply captured)"

            log_text = f"<@{user.id}> used {cmd_display}{arg_display} in {chan_repr}{reply_display}"

            # send to configured log channel; use send_log helper (async). If send_log fails, log locally.
            try:
                await send_log(self.bot, log_text)
            except Exception:
                logger.exception("Failed to send usage log to discord; falling back to logger")
                logger.info(log_text)
        except Exception:
            logger.exception("after_invoke failure in UsageLoggerCog")

    # Fallback for pure application command interactions that may not trigger before_invoke/after_invoke hooks:
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        When someone uses a slash/app command, this event fires. We log the invocation here as well.
        Note: this may duplicate logs for hybrid commands (prefix+slash) — the after_invoke path will also log.
        If you see duplicates, you can choose to disable the interaction logging or add dedup logic.
        """
        try:
            # Optionally skip interaction logging for commands listed in SKIP_COMMANDS if desired.
            name = (interaction.data or {}).get("name", None)
            if name and name in SKIP_COMMANDS and SKIP_ALL_PREFIX:
                # If SKIP_ALL_PREFIX=True we probably don't want to log those commands at all,
                # but by default SKIP_ALL_PREFIX only applies to prefix invocations. Keep this conservative.
                pass

            if interaction.type is not discord.InteractionType.application_command:
                return
            user = interaction.user
            data = interaction.data or {}
            name = data.get("name", "unknown")
            # build a readable argument string if present in options
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
            cmd_display = f"/{name}"
            arg_display = f' "{args_display}"' if args_display else ""
            log_text = f"<@{user.id}> used {cmd_display}{arg_display} in {chan_repr}"

            # send log (don't try to capture output here)
            try:
                await send_log(self.bot, log_text)
            except Exception:
                logger.exception("Failed to send interaction usage log")
        except Exception:
            logger.exception("on_interaction failure in UsageLoggerCog")


async def setup(bot: commands.Bot):
    await bot.add_cog(UsageLoggerCog(bot))