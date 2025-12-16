from pathlib import Path

p = Path('cogs/rumble_listener_cog.py')
text = p.read_text(encoding='utf-8')

marker = "ids = [int(x) for x in dict.fromkeys(ids)]\n"
if marker not in text:
    print("Marker not found. Aborting. (file may differ from expected)")
else:
    diagnostic = (
        marker
        + "\n"
        + "    # TEMP DIAGNOSTIC: always log what we saw so we can debug missed winners\n"
        + "    logger.info(\"rumble:_extract_winner_ids: explicit_ids=%r\", ids)\n"
        + "    logger.info(\"rumble:_extract_winner_ids: message.mentions=%r\", [getattr(m, \"id\", None) for m in (message.mentions or [])])\n"
        + "    logger.info(\"rumble:_extract_winner_ids: content=%r\", message.content)\n"
        + "    try:\n"
        + "        emb_texts = []\n"
        + "        for emb in (message.embeds or []):\n"
        + "            emb_texts.append({\"title\": emb.title, \"description\": emb.description, \"fields\": [(f.name, f.value) for f in (emb.fields or [])]})\n"
        + "        logger.info(\"rumble:_extract_winner_ids: embeds=%r\", emb_texts)\n"
        + "    except Exception:\n"
        + "        logger.exception(\"rumble:_extract_winner_ids: failed to dump embeds for diagnostic\")\n\n"
        + "    if ids:\n"
        + "        logger.debug(\"rumble_listener: found explicit id tokens -> %r\", ids)\n"
        + "        return ids\n"
    )
    new_text = text.replace(marker, diagnostic, 1)
    p.write_text(new_text, encoding='utf-8')
    print("Patched", p)