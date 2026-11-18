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

# Считываем пул ключей через запятую (переменная GEMINI_KEYS на Render)
keys_env = os.getenv("GEMINI_KEYS", os.getenv("GEMINI_API_KEY", ""))
API_KEYS = [k.strip() for k in keys_env.split(",") if k.strip()]

if not API_KEYS:
    raise ValueError("❌ Ошибка: Не найдены API-ключи Gemini в переменных окружения GEMINI_KEYS!")

current_key_index = 0

def get_current_gemini_client():
    """Возвращает клиент с текущим рабочим API-ключом"""
    return genai.Client(api_key=API_KEYS[current_key_index])

def rotate_key():
    """Переключается на следующий ключ в пуле при исчерпании лимита"""
    global current_key_index
    if len(API_KEYS) > 1:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        logging.warning(f"🔄 Переключение на запасной API-ключ #{current_key_index + 1}")

# Список моделей для каскадного переключения при ошибках квот
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-3.8-flash"
]

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
    """Проверяет персональный лимит пользователя. Администратору — безлимит."""
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

# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH CHECK / UPTIMEROBOT) =====
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
