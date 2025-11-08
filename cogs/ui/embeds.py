import discord
import io
import logging
from utils.db_utils import get_user_pieces  # Correct import

def build_progress_embed(
        puzzle_meta, bot_data, user_id, puzzle_key,
        total_pieces, image_bytes
    ):
    logger = logging.getLogger(__name__)
    logger.info("[DEBUG] build_progress_embed called with:")
    logger.info(f"[DEBUG] puzzle_meta: {puzzle_meta}")
    logger.info(f"[DEBUG] total_pieces: {total_pieces}")
    logger.info(f"[DEBUG] image_bytes length: {len(image_bytes) if image_bytes else 'None'}")
    logger.info(f"[DEBUG] user_id: {user_id}, puzzle_key: {puzzle_key}")

    user_pieces = get_user_pieces(bot_data, user_id, puzzle_key)
    logger.info(f"[DEBUG] user_pieces: {user_pieces}")

    embed = discord.Embed(
        title=f"Progress for {puzzle_meta['display_name']}",
        description=f"Collected {len(user_pieces)} / {total_pieces} pieces",
        color=discord.Color.blurple()
    )

    if user_pieces:
        embed.add_field(
            name="Collected IDs",
            value=", ".join(user_pieces),
            inline=False
        )
    else:
        embed.add_field(name="Collected IDs", value="None yet!", inline=False)

    filename = f"{puzzle_meta['display_name'].replace(' ', '_').lower()}_progress.png"
    file = discord.File(io.BytesIO(image_bytes), filename=filename)
    embed.set_image(url=f"attachment://{filename}")

    return embed, file