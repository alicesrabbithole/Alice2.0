import logging

async def log(bot, message: str):
    log_channel = bot.get_channel(1411859714144468992)
    if log_channel:
        try:
            await log_channel.send(message)
        except Exception:
            logging.exception("Failed to send log message")

async def log_exception(bot, context: str, error: Exception):
    log_channel = bot.get_channel(1411859714144468992)
    if log_channel:
        try:
            await log_channel.send(f"❌ Error in {context}: `{type(error).__name__}` — {error}")
        except Exception:
            logging.exception("Failed to send exception log")
