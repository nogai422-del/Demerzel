# Детектор эмодзи для модерации: извлечение, фильтрация флагов и подсчет.

import emoji
from typing import List


# Проверяет пару символов regional indicator как флаг.
async def _is_regional_indicator_flag(e: str) -> bool:
    return (
        len(e) == 2
        and all("\U0001F1E6" <= c <= "\U0001F1FF" for c in e)
    )


# Определяет, является ли символ emoji-флагом (включая regional indicator пары).
async def is_flag_emoji(emoji_char: str) -> bool:
    emoji_data = emoji.EMOJI_DATA.get(emoji_char)
    if not emoji_data:
        return await _is_regional_indicator_flag(emoji_char)

    en = (emoji_data.get("en") or "").lower()
    aliases_list = (
        emoji_data.get("alias")
        or emoji_data.get("aliases")
        or []
    )
    aliases = " ".join(aliases_list).lower()

    if "flag" in en or "flag" in aliases:
        return True

    return await _is_regional_indicator_flag(emoji_char)


# Извлекает эмодзи из текста с учетом флагов-суррогатов.
async def extract_emojis(text: str | None) -> List[str]:
    if not text:
        return []

    return [
        m["emoji"]
        for m in emoji.emoji_list(text)
        if not (await is_flag_emoji(m["emoji"]))
    ]


# Считает количество эмодзи в строке.
async def emoji_count(text: str | None) -> int:
    return len(await extract_emojis(text))


# Проверяет сообщение на превышение лимита эмодзи.
async def emoji_handler(text: str | None) -> bool:
    return (await emoji_count(text)) > 0
