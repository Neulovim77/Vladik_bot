import os
import asyncio
import logging
import html
import re
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
    "🤖 <b>Vortex AI Core</b>\n"
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
        f"Привет, {message.from_user.first_name}! Я <b>Vortex</b> — твой ИИ-помощник.\n"
        f"Система активна. Напиши мне любой вопрос.{BRAND_FOOTER}",
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
        # Запрос к модели Gemini
        response = gemini_client.models.generate_content(
            model="gemini-3.8-flash",
            contents=message.text,
        )
        answer = response.text
        
        # Экранируем спецсимволы под HTML
        safe_answer = html.escape(answer)
        final_text = safe_answer + BRAND_FOOTER
        
        await message.answer(final_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        
    except Exception as e:
        error_str = str(e)
        logging.error(f"Ошибка Gemini: {error_str}")
        
        # Проверяем лимиты
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            # Пытаемся вытащить время ожидания из текста ошибки Google (например, "retry in 1h36m...")
            retry_match = re.search(r"retry in ([^,\'\"]+)", error_str, re.IGNORECASE)
            wait_time = retry_match.group(1) if retry_match else "некоторое время"
            
            warning_text = (
                "⚠️ <b>Лимит бесплатных запросов исчерпан!</b>\n\n"
                "Превышен суточный лимит Free Tier для модели <code>gemini-3.8-flash</code>.\n"
                f"⏱ <b>Время до сброса лимита:</b> <code>{wait_time}</code>\n\n"
                f"Подожди указанное время или смени API-ключ в настройках Render.{BRAND_FOOTER}"
            )
            await message.answer(warning_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else:
            await message.answer(f"Произошла ошибка при обращении к ИИ: <code>{html.escape(error_str)}</code>{BRAND_FOOTER}", parse_mode=ParseMode.HTML)

async def main():
    # Запускаем веб-сервер для прохождения проверки портов на Render
    await start_web_server()
    
    logging.basicConfig(level=logging.INFO)
    print("Бот Vortex успешно запущен и ожидает сообщения!")
    
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
