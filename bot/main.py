# Запуск Telegram-бота: регистрация роутеров и фоновых задач.

import asyncio
from aiogram import Dispatcher

from bot.bot_init import bot
from bot.backup_utils import run_daily

from bot.handlers.private_guard import router as private_guard_router
from bot.handlers.commands import router as commands_router
from bot.handlers.echo import router as echo_router
from bot.handlers.moderation import router as moderation_router
from bot.handlers.audit import router as chat_member_router
from bot.handlers.chat_lock import router as chat_lock_router

from bot.handlers.echo import wisdom_loop
from bot.scheduled_messages import scheduled_messages_loop

dp = Dispatcher()


# Точка запуска и координации основных задач модуля.
async def main():
    dp.include_router(private_guard_router)
    dp.include_router(chat_lock_router)
    dp.include_router(chat_member_router)
    dp.include_router(moderation_router)
    dp.include_router(commands_router)
    dp.include_router(echo_router)

    daily_task = asyncio.create_task(run_daily(bot))
    wisdom_task = asyncio.create_task(wisdom_loop(bot))
    scheduled_task = asyncio.create_task(scheduled_messages_loop(bot))

    try:
        await dp.start_polling(
            bot,
            allowed_updates=["chat_member", "my_chat_member", "message"],
        )

    finally:
        for task in (daily_task, wisdom_task, scheduled_task):
            task.cancel()

        await asyncio.gather(daily_task, wisdom_task, scheduled_task, return_exceptions=True)
