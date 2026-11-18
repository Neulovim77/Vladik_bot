import os
import asyncio
import logging
import html
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
# ===============================

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Инициализация клиента Google GenAI
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Брендовая подпись
BRAND_FOOTER = (
    "\n\n"
    "────────────────────────\n"
    "🤖 <b>Создано с помощью ИИ (Gemini)</b>\n"
    '🔗 <a href="https://t.me/Ronisupport">@Ronisupport</a> | '
    '🌐 <a href="https://www.tiktok.com/@dez1x_">TikTok</a>'
)

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
        f"Привет, {message.from_user.first_name}! Я твой бесплатный ИИ-помощник на базе Gemini.\n"
        f"Напиши мне любой вопрос, и я отвечу!{BRAND_FOOTER}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        return
    
    # Отправляем статус печати
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        # Запрос к бесплатной модели Gemini
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=message.text,
        )
        answer = response.text
        
        # Экранируем спецсимволы под HTML
        safe_answer = html.escape(answer)
        final_text = safe_answer + BRAND_FOOTER
        
        await message.answer(final_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Ошибка Gemini: {e}")
        await message.answer(f"Произошла ошибка при обращении к ИИ: {e}")

async def main():
    # Запускаем веб-сервер для прохождения проверки портов на Render
    await start_web_server()
    
    logging.basicConfig(level=logging.INFO)
    print("Бот успешно запущен и ожидает сообщения!")
    
    # Сбрасываем зависшие старые апдейты на стороне Telegram
    await bot.delete_webhook(drop_pending_updates=True)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
