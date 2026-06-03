# Модерация контента и права пользователей: фильтры, лимиты и предупреждения.

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from aiogram import Router, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.utils.formatting import html_decoration as hd

from bot.database import db
from bot.message_queue import bot_answer, bot_send_message, bot_send_photo
from bot.handlers.badword_detector import detect_badword_details
from bot.handlers.emoji_detector import emoji_count
from bot.utils import save_timed_message, get_full_name, safe_delete
from env_config import require_int_env

import time

router = Router()
LOG_CHANNEL_ID = require_int_env("LOG_CHANNEL_ID")
SOURCE_CHAT_ID = require_int_env("SOURCE_CHAT_ID")


# Формирует человекочитаемый тег чата для логов.
def tag_chat(chat_id: int) -> str:
    return f"#c{abs(int(chat_id))}"


# Формирует человекочитаемый тег пользователя для логов.
def tag_user(user_id: int) -> str:
    return f"#u{int(user_id)}"


# Отправляет лог удаления сообщения за мат в лог-канал.
async def send_badword_deleted_log(
    message: Message,
    trigger_word: str,
    canonical_word: str,
    trigger_type: str,
    message_text: str,
) -> None:
    if not LOG_CHANNEL_ID:
        return
    if message.chat.id != SOURCE_CHAT_ID:
        return
    if not message.from_user:
        return

    try:
        user = message.from_user
        chat_title = hd.quote(message.chat.title or str(message.chat.id))
        full_name = hd.quote(await get_full_name(user))
        trigger = hd.quote(trigger_word.strip()[:120] or "-")
        canonical = hd.quote(canonical_word.strip()[:120] or "-")
        trigger_type_label = "префикс" if trigger_type == "prefix" else "точное слово"

        source_text = (message_text or "").strip()
        if not source_text:
            source_text = "<без текста>"
        if len(source_text) > 3500:
            source_text = source_text[:3500] + "..."

        quoted_text = hd.quote(source_text)
        event_time = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime())

        blocks = [
            f"{chat_title}\n{tag_chat(message.chat.id)}",
            f'<a href="tg://user?id={user.id}">👤 {full_name}</a>\n{tag_user(user.id)}',
            "Удалено сообщение за мат",
            f"Время: {event_time}",
            f"Триггер: <code>{trigger}</code> (<code>{canonical}</code>)",
            f"Тип триггера: {trigger_type_label}",
            f"<blockquote expandable>{quoted_text}</blockquote>",
            "#badword_deleted",
        ]

        await bot_send_message(
            message.bot,
            LOG_CHANNEL_ID,
            "\n\n".join(blocks),
            wait=True,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        print(f"Ошибка отправки badword-лога: {e}")


# Проверяет наличие ссылок в entities/caption_entities сообщения.
async def message_has_link(message: Message) -> bool:
    """
    Проверяет, есть ли в сообщении ссылка, по данным Telegram:
      - entities / caption_entities с type in ("url", "text_link").

    Без регэкспов — только то, что распарсил сам Телеграм.
    Работает и для текста, и для подписи к медиа.
    """
    entities = message.entities or []
    caption_entities = getattr(message, "caption_entities", None) or []

    for ent in list(entities) + list(caption_entities):
        if ent.type in ("url", "text_link"):
            return True

    return False


# Возвращает permission_level пользователя в чате (0/1/2) для правил модерации.
async def get_permission_level(chat_id: int, user_id: int) -> int:
    """
    Уровень прав (permission_level) в chat_users:
      0 — всё запрещено (мат, ссылки, медиа)
      1 — разрешены: фото, видео, гиф, документы, аудио
      2 — то же, + мат, + ссылки, + видео-сообщения
    """
    async with db() as cur:
        await cur.execute(
            """
            SELECT permission_level
            FROM chat_users
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0


# Устанавливает permission_level пользователю (создает запись в chat_users при отсутствии).
async def set_permission_level(chat_id: int, user_id: int, level: int) -> None:
    """
    Устанавливает permission_level (0/1/2).
    Если юзера нет в chat_users — создаёт с нулями.
    """
    async with db() as cur:
        await cur.execute(
            """
            SELECT 1 FROM chat_users
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()

        if row is None:
            await cur.execute(
                """
                INSERT INTO chat_users (chat_id, user_id, score, level, permission_level)
                VALUES (?, ?, 0, 0, ?)
                """,
                (chat_id, user_id, level),
            )
        else:
            await cur.execute(
                """
                UPDATE chat_users
                SET permission_level = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (level, chat_id, user_id),
            )


# Проверяет право пользователя использовать /view (поле can_view_forms).
async def has_view_permission(chat_id: int, user_id: int) -> bool:
    """
    Есть ли у пользователя право смотреть анкеты (/view).
    Хранится в chat_users.can_view_forms (0/1).
    """
    async with db() as cur:
        await cur.execute(
            """
            SELECT can_view_forms
            FROM chat_users
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()
        return bool(row and row[0])


# Выдает/снимает право пользователя на просмотр анкет (/view).
async def set_view_permission(chat_id: int, user_id: int, allowed: bool = True) -> None:
    """
    Устанавливает право смотреть анкеты для пользователя.
    """
    value = 1 if allowed else 0
    async with db() as cur:
        await cur.execute(
            """
            SELECT 1 FROM chat_users
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()

        if row is None:
            await cur.execute(
                """
                INSERT INTO chat_users (chat_id, user_id, can_view_forms)
                VALUES (?, ?, ?)
                """,
                (chat_id, user_id, value),
            )
        else:
            await cur.execute(
                """
                UPDATE chat_users
                SET can_view_forms = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (value, chat_id, user_id),
            )


# Проверяет, действует ли у пользователя разрешение на голосовые сообщения.
async def has_voice_permission(chat_id: int, user_id: int) -> bool:
    """
    Проверка наличия прав на голосовые:
    valid_until > now в таблице voice_permissions.
    """
    now = int(time.time())
    async with db() as cur:
        await cur.execute(
            """
            SELECT valid_until
            FROM voice_permissions
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        return int(row[0]) > now


# Проверяет и обновляет суточный лимит голосовых (20/день).
async def check_and_update_voice_limit(chat_id: int, user_id: int, add_count: int = 1) -> bool:
    """
    Суточный лимит голосовых: не более 20 в день.
    Работает по таблице voice_permissions (used_today, used_date).
    Возвращает:
      True  — если можно пропустить (и счётчик обновлён),
      False — если лимит превышен.
    """
    now = int(time.time())
    today_str = time.strftime("%Y-%m-%d", time.localtime(now))

    async with db() as cur:
        await cur.execute(
            """
            SELECT used_today, used_date
            FROM voice_permissions
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()

        if row is None:
            return False

        used_today, used_date = row
        used_today = int(used_today or 0)
        used_date = used_date or ""

        if used_date != today_str:
            used_today = 0

        if used_today + add_count > 20:
            return False

        used_today += add_count
        await cur.execute(
            """
            UPDATE voice_permissions
            SET used_today = ?, used_date = ?
            WHERE chat_id = ? AND user_id = ?
            """,
            (used_today, today_str, chat_id, user_id),
        )
        return True


# Проверяет, действует ли у пользователя разрешение на эмодзи.
async def has_emoji_permission(chat_id: int, user_id: int) -> bool:
    """
    Проверка наличия прав на эмодзи (valid_until > now).
    """
    now = int(time.time())
    async with db() as cur:
        await cur.execute(
            """
            SELECT valid_until
            FROM emoji_permissions
            WHERE chat_id = ? AND user_id = ? AND valid_until > ?
            """,
            (chat_id, user_id, now),
        )
        row = await cur.fetchone()
        return row is not None


# Проверяет и обновляет суточный лимит эмодзи (50/день).
async def check_and_update_emoji_limit(chat_id: int, user_id: int, emojis_count: int) -> bool:
    now = int(time.time())
    today_str = time.strftime("%Y-%m-%d", time.localtime(now))

    async with db() as cur:
        await cur.execute(
            """
            UPDATE emoji_permissions
            SET used_today = 0, used_date = ?
            WHERE chat_id = ? AND user_id = ? AND used_date != ?
            """,
            (today_str, chat_id, user_id, today_str),
        )

        await cur.execute(
            """
            UPDATE emoji_permissions
            SET used_today = used_today + ?
            WHERE chat_id = ?
              AND user_id = ?
              AND used_today + ? <= 50
            """,
            (emojis_count, chat_id, user_id, emojis_count),
        )

        return cur.rowcount > 0


# Проверяет активный мут пользователя и чистит просроченный мут.
async def is_user_muted(chat_id: int, user_id: int) -> bool:
    """
    True  — если пользователь сейчас в муте.
    False — если не в муте (или мут истёк; запись удаляется).
    """
    async with db() as cur:
        await cur.execute(
            """
            SELECT until, CAST(strftime('%s','now') AS INTEGER) AS now
            FROM mutes
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = await cur.fetchone()

        if not row:
            return False

        mute_until = int(row[0])
        now = int(row[1])

        if mute_until <= now:
            try:
                await cur.execute(
                    """
                    DELETE FROM mutes
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, user_id),
                )
            except Exception as e:
                print(f"Ошибка при очистке просроченного мута: {e}")
            return False

        return True


# Загружает шаблон предупреждения для конкретного типа ограничения (link/badword и т.д.).
async def get_permission_settings(permission_type: str):
    """
    Читает из permission_types запись:
      message, image_path, button_text, button_url
    для указанного типа.
    """
    async with db() as cur:
        await cur.execute(
            """
            SELECT message, image_path, button_text, button_url
            FROM permission_types
            WHERE media_type = ?
            """,
            (permission_type,),
        )
        row = await cur.fetchone()

        if not row:
            return None

        return {
            "message": row[0],
            "image_path": row[1],
            "button_text": row[2],
            "button_url": row[3],
        }


# Сохраняет данные в базе или кэше.
async def save_permission_message(chat_id: int, message_id: int) -> None:
    """
    Запоминаем последнее предупреждающее сообщение в permission_messages.
    send_time пишем Unix-временем через SQLite.
    """
    async with db() as cur:
        await cur.execute(
            """
            INSERT INTO permission_messages (chat_id, message_id, send_time)
            VALUES (?, ?, CAST(strftime('%s','now') AS INTEGER))
            ON CONFLICT(chat_id) DO UPDATE SET
                message_id = excluded.message_id,
                send_time  = excluded.send_time
            """,
            (chat_id, message_id),
        )


# Возвращает id последнего warning-сообщения в чате для последующего удаления.
async def get_old_permission_message(chat_id: int):
    """
    Берём id старого предупреждающего сообщения, если есть.
    """
    async with db() as cur:
        await cur.execute(
            """
            SELECT message_id
            FROM permission_messages
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None


# Отправляет warning по ограничению и заменяет предыдущее warning-сообщение в чате.
async def send_restriction_warning(message: Message, permission_type: str) -> bool:
    # 1) Загружаем шаблон предупреждения для текущего типа ограничения.
    settings = await get_permission_settings(permission_type)
    if not settings:
        return False

    chat_id = message.chat.id

    # 2) Удаляем предыдущее warning-сообщение, чтобы в чате оставалось только актуальное.
    old_id = await get_old_permission_message(chat_id)
    if old_id:
        try:
            await message.bot.delete_message(chat_id, old_id)
        except Exception:
            pass

    user = message.from_user
    full_name = await get_full_name(user)

    raw_msg = settings["message"] or ""

    user_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'

    caption = raw_msg
    caption = caption.replace("{user}", user_link)
    caption = caption.replace("{user_id}", str(user.id))
    caption = caption.replace("{full_name}", full_name)

    button_text = settings["button_text"]
    button_url = settings["button_url"]
    keyboard = (
    InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=button_url)]])
    if button_text and button_url
    else None
    )

    # 3) Для emoji warning отправляем текстом, для остальных — картинкой.
    if permission_type == "emoji":
        sent = await bot_answer(
            message,
            caption,
            wait=True,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        if sent:
            await save_permission_message(chat_id, sent.message_id)

        return True

    sent = None
    try:
        photo = FSInputFile(f"bot/images/{settings['image_path']}")
        sent = await bot_send_photo(
            message,
            photo,
            wait=True,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        print(f"Ошибка отправки изображения в send_restriction_warning, permission_type={permission_type}: {e}")
    
    # 4) Запоминаем id отправленного warning для следующей замены.
    if sent:
        await save_permission_message(chat_id, sent.message_id)

    return True


# Выдает/снимает разрешение на медиа по reply-команде.
@router.message(Command("media"))
async def media_permission_handler(message: Message, bot: Bot):
    """
    /media <0|1|2> — только в ответ на сообщение.
    Устанавливает permission_level пользователю.
    """
    try:
        await safe_delete(message)

        chat_id = message.chat.id

        if await is_user_muted(chat_id, message.from_user.id):
            return

        if not message.reply_to_message:
            return

        member = await bot.get_chat_member(chat_id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return

        # Ожидаем формат команды: /media <0|1|2>.
        args = message.text.split()
        if len(args) != 2 or args[1] not in ("0", "1", "2"):
            return

        level = int(args[1])
        target = message.reply_to_message.from_user

        await set_permission_level(chat_id, target.id, level)

        full_name = await get_full_name(target)

        await bot_answer(
            message,
            f"Пользователю <b>{full_name}</b> выданы права на отправку медиа.\n\nУровень: <b>{level}</b>.",
            parse_mode="HTML"
        )

    except Exception as e:
        print("Ошибка /media:", e)


# Выдает/продлевает доступ к голосовым сообщениям.
@router.message(Command("voice"))
async def voice_allow_handler(message: Message, bot: Bot):
    """
    /voice <дней> — только в ответ на сообщение.
    Выдаёт/продлевает права на голосовые сообщения.
    Права + лимит 20/день.
    """
    try:
        await safe_delete(message)

        chat_id = message.chat.id

        if await is_user_muted(chat_id, message.from_user.id):
            return

        if not message.reply_to_message:
            return

        member = await bot.get_chat_member(chat_id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return

        # Ожидаем формат: /voice <days>.
        args = message.text.split()
        if len(args) != 2 or not args[1].isdigit():
            return

        days = int(args[1])
        if days <= 0:
            return

        target_user = message.reply_to_message.from_user

        now = int(time.time())
        additional_time = days * 86400  # дни → секунды

        # Если запись уже есть — продлеваем право от текущего valid_until, иначе создаем новую.
        async with db() as cur:
            await cur.execute(
                """
                SELECT valid_until
                FROM voice_permissions
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, target_user.id),
            )
            row = await cur.fetchone()

            if row is None:
                new_valid_until = now + additional_time
                await cur.execute(
                    """
                    INSERT INTO voice_permissions (chat_id, user_id, valid_until, used_today, used_date)
                    VALUES (?, ?, ?, 0, '')
                    """,
                    (chat_id, target_user.id, new_valid_until),
                )
            else:
                current_valid_until = int(row[0])
                base_time = current_valid_until if current_valid_until > now else now
                new_valid_until = base_time + additional_time
                await cur.execute(
                    """
                    UPDATE voice_permissions
                    SET valid_until = ?
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (new_valid_until, chat_id, target_user.id),
                )

        full_name = await get_full_name(target_user)
        formatted_date = time.strftime("%d.%m.%Y", time.localtime(new_valid_until))

        await bot_answer(
            message,
            f"Пользователю {full_name} выдано разрешение на голосовые сообщения "
            f"на {days} дней (до {formatted_date}).",
        )

    except Exception as e:
        print("Ошибка /voice:", e)


# Выдает/продлевает доступ к эмодзи.
@router.message(Command("emoji"))
async def emoji_allow_handler(message: Message, bot: Bot):
    """
    /emoji <дней> — только в ответ на сообщение.
    Выдаёт/продлевает права на эмодзи.
    Права + лимит 50/день.
    """
    try:
        await safe_delete(message)

        chat_id = message.chat.id

        if await is_user_muted(chat_id, message.from_user.id):
            return

        from_user_id = message.from_user.id

        if not message.reply_to_message:
            return

        member = await bot.get_chat_member(chat_id, from_user_id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return

        # Ожидаем формат: /emoji <days>.
        args = message.text.split()
        if len(args) != 2 or not args[1].isdigit():
            return

        days = int(args[1])
        if days <= 0:
            return

        target_user = message.reply_to_message.from_user

        now = int(time.time())
        additional_time = days * 86400

        # Если запись уже есть — продлеваем право от текущего valid_until, иначе создаем новую.
        async with db() as cur:
            await cur.execute(
                """
                SELECT valid_until
                FROM emoji_permissions
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, target_user.id),
            )
            row = await cur.fetchone()

            if row is None:
                new_valid_until = now + additional_time
                await cur.execute(
                    """
                    INSERT INTO emoji_permissions (chat_id, user_id, valid_until, used_today, used_date)
                    VALUES (?, ?, ?, 0, '')
                    """,
                    (chat_id, target_user.id, new_valid_until),
                )
            else:
                current_valid_until = int(row[0])
                base_time = current_valid_until if current_valid_until > now else now
                new_valid_until = base_time + additional_time
                await cur.execute(
                    """
                    UPDATE emoji_permissions
                    SET valid_until = ?
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (new_valid_until, chat_id, target_user.id),
                )

        full_name = await get_full_name(target_user)
        formatted_date = time.strftime("%d.%m.%Y", time.localtime(new_valid_until))

        await bot_answer(
            message,
            f"Пользователю {full_name} выдано разрешение на эмодзи на {days} дней (до {formatted_date}).",
        )

    except Exception as e:
        print("Ошибка /emoji:", e)


# Выдает право смотреть анкеты через /view.
@router.message(Command("canview"))
async def canview_allow_handler(message: Message, bot):
    try:
        await safe_delete(message)

        chat_id = message.chat.id

        if not message.reply_to_message:
            return

        chat_member = await bot.get_chat_member(chat_id, message.from_user.id)
        if chat_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return

        target = message.reply_to_message.from_user

        await set_view_permission(chat_id, target.id, True)

        full_name = await get_full_name(target)

        await bot_answer(
            message,
            f"Пользователю <b>{full_name}</b> выдано право просматривать анкеты.",
            parse_mode="HTML",
        )

    except Exception as e:
        print("Ошибка /canview:", e)


# Проводит текстовую модерацию: мут, мат, ссылки и лимиты эмодзи.
async def moderation_handle_text(message: Message, level: int) -> bool:
    """
    Общая текстовая модерация:
      - МУТ (таблица mutes)
      - мат (badword_handler)
      - ссылки (message_has_link)
      - эмодзи (право + лимит)

    Работает и для "голого" текста, и для подписи к медиа.
    Использует text = message.text или message.caption.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id

    if await is_user_muted(chat_id, user_id):
        await safe_delete(message)
        return True

    text = message.text or message.caption or ""

    full_name = await get_full_name(message.from_user)

    if text:
        badword_details = await detect_badword_details(text)
    else:
        badword_details = None

    if badword_details:
        if level < 2:
            trigger_word, canonical_word, trigger_type = badword_details
            await safe_delete(message)
            await send_badword_deleted_log(
                message,
                trigger_word,
                canonical_word,
                trigger_type,
                text,
            )
            await send_restriction_warning(message, "badword")
            return True

    if text and await message_has_link(message):
        if level < 2:
            await safe_delete(message)
            await send_restriction_warning(message, "link")
            return True

    emojis_count = await emoji_count(text)

    if emojis_count > 0:
        if not await has_emoji_permission(chat_id, user_id):
            await safe_delete(message)

            await send_restriction_warning(message, "emoji")
            return True

        if not await check_and_update_emoji_limit(chat_id, user_id, emojis_count):
            await safe_delete(message)

            sent = await bot_answer(
                message,
                f"{full_name}, превышен дневной лимит эмодзи (50). Сообщение удалено.",
                wait=True
            )
            await save_timed_message(chat_id, sent.message_id)
            return True

    return False


# Главный роутер модерации по типам контента сообщения.
async def moderation_handle_message(message: Message) -> bool:
    """
    Возвращает True, если модерация всё обработала
    (сообщение удалено, предупреждение отправлено)
    и дальше обрабатывать НЕ нужно.
    False — если всё ок, можно продолжать.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    content_type = message.content_type
    level = await get_permission_level(chat_id, user_id)

    # Сначала единая текстовая проверка (мат/ссылки/эмодзи/мут).
    if await moderation_handle_text(message, level):
        return True

    # Ниже — проверки по типам вложений и медиа.
    if content_type == "sticker":
        await safe_delete(message)
        return True

    if content_type == "audio":
        if level == 0:
            await safe_delete(message)
            await send_restriction_warning(message, "audio")
            return True
        return False  # 1 и 2 уровни — можно

    if content_type == "voice":
        if not await has_voice_permission(chat_id, user_id):
            await safe_delete(message)
            await send_restriction_warning(message, "voice")
            return True

        full_name = await get_full_name(message.from_user)

        if not await check_and_update_voice_limit(chat_id, user_id, 1):
            await safe_delete(message)
            sent = await bot_answer(
                message,
                f"{full_name}, превышен дневной лимит голосовых сообщений (20). Сообщение удалено.",
                wait=True
            )
            await save_timed_message(chat_id, sent.message_id)
            return True

        return False  # всё ок

    if content_type == "video_note":
        if level < 2:
            await safe_delete(message)
            await send_restriction_warning(message, "video_note")
            return True
        return False

    allowed_level0 = {"text"}
    if level == 0 and content_type not in allowed_level0:
        await safe_delete(message)
        await send_restriction_warning(message, content_type)
        return True

    allowed_level1 = {"text", "photo", "video", "animation", "document", "audio"}
    if level == 1 and content_type not in allowed_level1:
        await safe_delete(message)
        await send_restriction_warning(message, content_type)
        return True

    return False
