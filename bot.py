import asyncio
import html
import json
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite
from openai import AsyncOpenAI

import os

# ===== НАСТРОЙКИ ИЗ RENDER =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_FILE = "bot_memory.db"
# ===============================

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Брендовая подпись
BRAND_FOOTER = (
    "\n\n───────────────────\n✨ <b>Создано при помощи</b>\n📱 Telegram: <a"
    ' href="https://t.me/Ronisupport">@Ronisupport</a>\n🎬 TikTok: <a'
    ' href="https://www.tiktok.com/@dez1x_x">@dez1x_x</a>'
)


async def init_db():
  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute("""
            CREATE TABLE IF NOT EXISTS dialogs (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                history TEXT
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS style_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                example_text TEXT
            )
        """)
    await db.commit()


async def get_style_examples() -> str:
  async with aiosqlite.connect(DB_FILE) as db:
    async with db.execute(
        "SELECT example_text FROM style_examples ORDER BY id DESC LIMIT 15"
    ) as cursor:
      rows = await cursor.fetchall()
      if rows:
        examples = "\n".join([f"- {row[0]}" for row in rows])
        return (
            "\n\nВот примеры того, как общается сам владелец (копируй этот"
            f" стиль, сленг и манеру):\n{examples}"
        )
  return ""


async def add_style_example(text: str):
  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute(
        "INSERT INTO style_examples (example_text) VALUES (?)", (text,)
    )
    await db.commit()


async def get_user_history(user_id: int) -> list:
  async with aiosqlite.connect(DB_FILE) as db:
    async with db.execute(
        "SELECT history FROM dialogs WHERE user_id = ?", (user_id,)
    ) as cursor:
      row = await cursor.fetchone()
      if row and row[0]:
        return json.loads(row[0])
  return []


async def save_user_history(user_id: int, user_name: str, history: list):
  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute(
        """
            INSERT INTO dialogs (user_id, user_name, history) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET history = excluded.history, user_name = excluded.user_name
        """,
        (user_id, user_name, json.dumps(history, ensure_ascii=False)),
    )
    await db.commit()


async def send_admin_report(
    user: types.User, chat_type: str, chat_title: str, query: str, answer: str
):
  if user.id == ADMIN_ID:
    return

  u_name = html.escape(user.full_name)
  u_username = f"@{user.username}" if user.username else "нет"
  c_info = (
      "💬 Личные сообщения (ЛС)"
      if chat_type == "private"
      else f"👥 Группа: <b>{html.escape(chat_title)}</b>"
  )

  report = (
      f"📊 <b>ОТЧЕТ О ПЕРЕПИСКЕ</b>\n\n"
      f"📍 <b>Тип:</b> {c_info}\n"
      f"👤 <b>Кто писал:</b> {u_name} ({u_username}) | ID: <code>{user.id}</code>\n\n"
      f"📥 <b>Запрос:</b>\n<blockquote>{html.escape(query)}</blockquote>\n\n"
      f"📤 <b>Ответ бота:</b>\n<blockquote>{html.escape(answer)}</blockquote>"
      "\n\n💡 <i>Хочешь научить бота отвечать иначе? Сделай реплай на это"
      " сообщение с текстом правильного ответа!</i>"
      f"{BRAND_FOOTER}"
  )

  try:
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=report,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
  except Exception as e:
    logging.error(f"Ошибка отчета: {e}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  builder = InlineKeyboardBuilder()
  builder.button(text="💬 Telegram автора", url="https://t.me/Ronisupport")
  builder.button(text="🎬 TikTok автора", url="https://www.tiktok.com/@dez1x_x")
  builder.adjust(1)

  welcome_text = (
      "👋 Привет! Я интеллектуальный ассистент-заместитель.\n\n"
      "Я веду диалоги от лица владельца, запоминаю контекст и обучаюсь"
      " стилю.\n\n"
      f"Задай мне вопрос!{BRAND_FOOTER}"
  )
  await message.answer(
      welcome_text,
      parse_mode=ParseMode.HTML,
      reply_markup=builder.as_markup(),
      disable_web_page_preview=True,
  )


@dp.message(F.from_user.id == ADMIN_ID)
async def handle_admin_actions(message: types.Message):
  if message.reply_to_message:
    correction_text = message.text
    await add_style_example(correction_text)
    await message.answer(
        "🧠 <b>Стиль успешно обновлен!</b> Я запомнил эту фразу и буду"
        " использовать её в похожих ситуациях.",
        parse_mode=ParseMode.HTML,
    )
    return


@dp.message(F.chat.type == ChatType.PRIVATE & F.text)
async def handle_private(message: types.Message):
  user = message.from_user
  await bot.send_chat_action(chat_id=message.chat.id, action="typing")

  style_instructions = await get_style_examples()
  SYSTEM_PROMPT = (
      "Ты — цифровой заместитель и аватар владельца. Твоя задача — отвечать в"
      " Telegram от его лица. Общайся естественно, перенимай его лексикон,"
      " сленг и манеру речи на основе примеров ниже. Не будь роботом."
      f"{style_instructions}"
  )

  history = await get_user_history(user.id)
  history.append({"role": "user", "content": message.text})
  trimmed_history = history[-10:]

  try:
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}]
        + trimmed_history,
    )
    answer = response.choices[0].message.content

    history.append({"role": "assistant", "content": answer})
    await save_user_history(user.id, user.full_name, history)

    final_message = f"{answer}{BRAND_FOOTER}"
    await message.answer(
        final_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await send_admin_report(user, "private", "", message.text, answer)

  except Exception as e:
    await message.answer(f"⚠️ Ошибка: {e}")


@dp.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
    & (F.text | F.caption)
)
async def handle_group(message: types.Message):
  bot_user = await bot.get_me()
  is_mentioned = f"@{bot_user.username}" in (message.text or "")
  is_reply = (
      message.reply_to_message
      and message.reply_to_message.from_user.id == bot_user.id
  )

  if is_mentioned or is_reply:
    user = message.from_user
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    clean_text = (
        message.text.replace(f"@{bot_user.username}", "").strip()
        if message.text
        else ""
    )

    style_instructions = await get_style_examples()
    SYSTEM_PROMPT = (
        "Ты — цифровой заместитель владельца в групповом чате. Отвечай от его"
        " лица, используя его манеру общения и сленг из примеров ниже."
        f"{style_instructions}"
    )

    history = await get_user_history(user.id)
    history.append({"role": "user", "content": clean_text})
    trimmed_history = history[-10:]

    try:
      response = await openai_client.chat.completions.create(
          model="gpt-4o-mini",
          messages=[{"role": "system", "content": SYSTEM_PROMPT}]
          + trimmed_history,
      )
      answer = response.choices[0].message.content

      history.append({"role": "assistant", "content": answer})
      await save_user_history(user.id, user.full_name, history)

      final_message = f"{answer}{BRAND_FOOTER}"
      await message.answer(
          final_message,
          reply_to_message_id=message.message_id,
          parse_mode=ParseMode.HTML,
          disable_web_page_preview=True,
      )

      await send_admin_report(
          user, str(message.chat.id), message.chat.title, clean_text, answer
      )

    except Exception as e:
      await message.answer(f"⚠️ Ошибка: {e}")


async def main():
  await init_db()
  logging.basicConfig(level=logging.INFO)
  print("🚀 Бот с самообучением стиля запущен!")
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
