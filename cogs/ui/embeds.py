import discord
import io
import logging

def build_progress_embed(puzzle_meta, collected_piece_ids, total_pieces, image_bytes):
    logger = logging.getLogger(__name__)
    logger.info(f"[DEBUG] build_progress_embed called with:")
    logger.info(f"[DEBUG] puzzle_meta: {puzzle_meta}")
    logger.info(f"[DEBUG] collected_piece_ids: {collected_piece_ids}")
    logger.info(f"[DEBUG] total_pieces: {total_pieces}")
    logger.info(f"[DEBUG] image_bytes length: {len(image_bytes) if image_bytes else 'None'}")

    embed = discord.Embed(
        title=f"Progress for {puzzle_meta['display_name']}",
        description=f"Collected {len(collected_piece_ids)} / {total_pieces} pieces",
        color=discord.Color.blurple()
    )

    if collected_piece_ids:
        embed.add_field(
            name="Collected IDs",
            value=", ".join(collected_piece_ids),
            inline=False
        )
    else:
        embed.add_field(name="Collected IDs", value="None yet!", inline=False)

    file = discord.File(io.BytesIO(image_bytes), filename=f"{puzzle_meta['display_name']}_progress.png")
    embed.set_image(url=f"attachment://{puzzle_meta['display_name']}_progress.png")

    return embed, file