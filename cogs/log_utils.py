# cogs/log_utils.py
import discord
import traceback

async def log(bot: discord.Client, message: str = None, embed: discord.Embed = None):
    channel = getattr(bot, "log_channel", None)
    if channel:
        try:
            await channel.send(content=message, embed=embed)
        except Exception:
            pass  # Avoid crashing on log failure

async def log_exception(bot: discord.Client, context: str, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    msg = f"⚠️ Exception in {context}:\n```{tb[-1800:]}```"
    await log(bot, msg)