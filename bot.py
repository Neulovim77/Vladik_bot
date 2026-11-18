import os
import asyncio
import logging
import html
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
import aiosqlite
from google import genai

# ===== НАСТРОЙКИ ИЗ RENDER =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Считываем ключи через запятую (например: key1,key2,key3) или один ключ
keys_env = os.getenv("GEMINI_KEYS", os.getenv("GEMINI_API_KEY", ""))
API_KEYS = [k.strip() for k in keys_env.split(",") if k.strip()]

if not API_KEYS:
    raise ValueError("❌ Ошибка: Не найдены API-ключи Gemini в переменных окружения!")

current_key_index = 0

def get_current_gemini_client():
    """Возвращает клиент с текущим рабочим API-ключом"""
    return genai.Client(api_key=API_KEYS[current_key_index])

def rotate_key():
    """Переключается на следующий ключ в пуле"""
    global current_key_index
    if len(API_KEYS) > 1:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        logging.warning(f"🔄 Переключение на запасной API-ключ #{current_key_index + 1}")

# Впиши сюда свой Telegram ID цифрами (чтобы у тебя был безлимит)
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789")) 

# Ссылка на твой Telegram для связи с разработчиком
DEV_CONTACT = "@Ronisupport"

DB_FILE = "bot_memory.db"

# Индивидуальный лимит запросов на обычного пользователя в сутки
DAILY_LIMIT = 20
# ===============================

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Брендовая подпись с призывом написать разработчику
BRAND_FOOTER = (
    "\n\n"
    "────────────────────────\n"
    f"💡 <i>Если остались вопросы или бот работает неправильно, напишите разработчику: {DEV_CONTACT}</i>\n\n"
    "🤖 <b>Vortex AI Core</b>\n"
    f'🔗 <a href="https://t.me/{DEV_CONTACT.lstrip("@")}">Поддержка</a> | '
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
    """Проверяет лимит пользователя. Если это админ — всегда разрешает."""
    if user_id == ADMIN_ID:
        return True, "∞ (Админ)"

    now = datetime.now()
    
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT requests_count, reset_time FROM user_limits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
            if not row:
                reset_time = (now + timedelta(days=1)).isoformat()
                await db.execute(
                    "INSERT INTO user_limits (user_id, requests_count, reset_time) VALUES (?, 1, ?)",
                    (user_id, reset_time)
                )
                await db.commit()
                return True, DAILY_LIMIT - 1
            
            requests_count, reset_time_str = row
            reset_time = datetime.fromisoformat(reset_time_str)
            
            # Если прошли сутки — сбрасываем лимит
            if now >= reset_time:
                new_reset_time = (now + timedelta(days=1)).isoformat()
                await db.execute(
                    "UPDATE user_limits SET requests_count = 1, reset_time = ? WHERE user_id = ?",
                    (new_reset_time, user_id)
                )
                await db.commit()
                return True, DAILY_LIMIT - 1
            
            # Если лимит исчерпан
            if requests_count >= DAILY_LIMIT:
                diff = reset_time - now
                hours = int(diff.total_seconds() // 3600)
                minutes = int((diff.total_seconds() % 3600) // 60)
                time_str = f"{hours}ч {minutes}м"
                return False, time_str
            
            # Увеличиваем счетчик
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
    user_id = message.from_user.id
    limit_text = "∞ (Бесконечно)" if user_id == ADMIN_ID else f"{DAILY_LIMIT} запросов в сутки"
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я <b>Vortex</b> — твой ИИ-помощник.\n"
        f"Твой лимит: <b>{limit_text}</b>.\n"
        f"Система активна. Напиши мне любой вопрос.{BRAND_FOOTER}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        return
    
    user_id = message.from_user.id
    
    # Проверяем персональный лимит (админ идет без ограничений)
    allowed, data = await check_and_update_limit(user_id)
    
    if not allowed:
        warning_text = (
            "⚠️ <b>Твой лимит запросов на сегодня исчерпан!</b>\n\n"
            f"Каждому пользователю доступно <code>{DAILY_LIMIT}</code> запросов в сутки.\n"
            f"⏱ <b>Лимит обновится через:</b> <code>{data}</code>\n\n"
            f"Подожди указанное время или обратись к разработчику: {DEV_CONTACT}.{BRAND_FOOTER}"
        )
        await message.answer(warning_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return
    
    # Отправляем статус печати
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Пытаемся отправить запрос с автопереключением ключей при 429 ошибке
    max_attempts = len(API_KEYS)
    success = False
    answer = ""
    error_str = ""

    for attempt in range(max_attempts):
        try:
            client = get_current_gemini_client()
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=message.text,
            )
            answer = response.text
            success = True
            break
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                logging.warning(f"Ключ #{current_key_index + 1} исчерпал лимит. Пробуем следующий...")
                rotate_key()
            else:
                # Другие ошибки (не лимит) прерывают цикл
                break

    if success:
        safe_answer = html.escape(answer)
        
        if user_id == ADMIN_ID:
            footer_info = "\n\n💬 <i>Осталось попыток: ∞ [Администратор]</i>"
        else:
            footer_info = f"\n\n💬 <i>Осталось попыток на сегодня: {data} из {DAILY_LIMIT}</i>"
            
        final_text = safe_answer + footer_info + BRAND_FOOTER
        await message.answer(final_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        logging.error(f"Ошибка Gemini для юзера {user_id}: {error_str}")
        await message.answer(
            f"Произошла ошибка при обращении к ИИ: <code>{html.escape(error_str)}</code>"
            f"\n\n<i>Если ошибка повторяется, сообщи разработчику: {DEV_CONTACT}</i>{BRAND_FOOTER}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

async def main():
    await init_db()
    await start_web_server()
    
    logging.basicConfig(level=logging.INFO)
    print(f"Бот Vortex запущен! Загружено API-ключей в пул: {len(API_KEYS)}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    while True:
        try:
            print("Запуск polling...")
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            logging.error(f"Поллинг упал с ошибкой: {e}. Перезапуск через 5 секунд...")
            asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
