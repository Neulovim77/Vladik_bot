import os
import asyncio
import logging
import html
import re
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
import aiosqlite
from google import genai

# ===== НАСТРОЙКИ ИЗ RENDER =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_FILE = "bot_memory.db"

# Индивидуальный лимит запросов на одного пользователя в сутки
DAILY_LIMIT = 20
# ===============================

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Инициализация клиента Google GenAI
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Брендовая подпись
BRAND_FOOTER = (
    "\n\n"
    "────────────────────────\n"
    "🤖 <b>Vortex AI Core</b>\n"
    '🔗 <a href="https://t.me/Ronisupport">@Ronisupport</a> | '
    '🌐 <a href="https://www.tiktok.com/@dez1x_">TikTok</a>'
)

# ===== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ДЛЯ ЛИМИТОВ =====
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_limits (
                user_id INTEGER PRIMARY KEY,
                requests_count INTEGER DEFAULT 0,
                reset_time TEXT
            )
        """)
        await db.commit()

async def check_and_update_limit(user_id: int):
    """Проверяет лимит пользователя. Возвращает (True, осталось_попыток) или (False, часов_до_сброса)"""
    now = datetime.now()
    
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT requests_count, reset_time FROM user_limits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
            if not row:
                # Новий користувач — створюємо запис, даємо лімит
                reset_time = (now + timedelta(days=1)).isoformat()
                await db.execute(
                    "INSERT INTO user_limits (user_id, requests_count, reset_time) VALUES (?, 1, ?)",
                    (user_id, reset_time)
                )
                await db.commit()
                return True, DAILY_LIMIT - 1
            
            requests_count, reset_time_str = row
            reset_time = datetime.fromisoformat(reset_time_str)
            
            # Якщо минула доба — сброс ліміту
            if now >= reset_time:
                new_reset_time = (now + timedelta(days=1)).isoformat()
                await db.execute(
                    "UPDATE user_limits SET requests_count = 1, reset_time = ? WHERE user_id = ?",
                    (new_reset_time, user_id)
                )
                await db.commit()
                return True, DAILY_LIMIT - 1
            
            # Якщо ліміт вичерпано
            if requests_count >= DAILY_LIMIT:
                # Рахуємо скільки залишилось годин/хвилин до сбросу
                diff = reset_time - now
                hours = int(diff.total_seconds() // 3600)
                minutes = int((diff.total_seconds() % 3600) // 60)
                time_str = f"{hours}ч {minutes}м"
                return False, time_str
            
            # Збільшуємо лічильник на 1
            await db.execute(
                "UPDATE user_limits SET requests_count = requests_count + 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
            return True, DAILY_LIMIT - (requests_count + 1)

# ===== ФИКТИВНЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH CHECK) =====
async def handle(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")
# ==========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я <b>Vortex</b> — твой ИИ-помощник.\n"
        f"У тебя есть <b>{DAILY_LIMIT} бесплатных запросов</b> в сутки.\n"
        f"Система активна. Напиши мне любой вопрос.{BRAND_FOOTER}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        return
    
    user_id = message.from_user.id
    
    # Проверяем индивидуальный лимит пользователя
    allowed, data = await check_and_update_limit(user_id)
    
    if not allowed:
        # Лимит исчерпан именно у этого юзера
        warning_text = (
            "⚠️ <b>Твой лимит запросов на сегодня исчерпан!</b>\n\n"
            f"Каждому пользователю доступно <code>{DAILY_LIMIT}</code> запросов в сутки.\n"
            f"⏱ <b>Лимит обновится через:</b> <code>{data}</code>\n\n"
            f"Подожди указанное время или обратись к администратору.{BRAND_FOOTER}"
        )
        await message.answer(warning_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return
    
    # Отправляем статус печати
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        # Запрос к модели Gemini
        response = gemini_client.models.generate_content(
            model="gemini-3.8-flash",
            contents=message.text,
        )
        answer = response.text
        
        # Экранируем спецсимволы под HTML
        safe_answer = html.escape(answer)
        final_text = safe_answer + f"\n\n💬 <i>Осталось попыток на сегодня: {data}</i>" + BRAND_FOOTER
        
        await message.answer(final_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        
    except Exception as e:
        error_str = str(e)
        logging.error(f"Ошибка Gemini для юзера {user_id}: {error_str}")
        
        # Если вдруг уперлись в глобальный лимит самого API ключа Google
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            retry_match = re.search(r"retry in ([^,\'\"]+)", error_str, re.IGNORECASE)
            wait_time = retry_match.group(1) if retry_match else "некоторое время"
            
            warning_text = (
                "⚠️ <b>Техническая пауза сервера!</b>\n\n"
                "Общий ресурс нейросети временно исчерпан. Попробуй отправить запрос чуть позже."
                f"{BRAND_FOOTER}"
            )
            await message.answer(warning_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else:
            await message.answer(f"Произошла ошибка при обращении к ИИ: <code>{html.escape(error_str)}</code>{BRAND_FOOTER}", parse_mode=ParseMode.HTML)

async def main():
    # Инициализируем базу данных
    await init_db()
    
    # Запускаем веб-сервер для прохождения проверки портов на Render
    await start_web_server()
    
    logging.basicConfig(level=logging.INFO)
    print("Бот Vortex успешно запущен с системой персональных лимитов!")
    
    # Сбрасываем зависшие старые апдейты на стороне Telegram
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Бесконечный цикл с автопереподключением при обрывах связи
    while True:
        try:
            print("Запуск polling...")
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            logging.error(f"Поллинг упал с ошибкой: {e}. Перезапуск через 5 секунд...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
