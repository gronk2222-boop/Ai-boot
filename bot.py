import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import URLInputFile
from dotenv import load_dotenv
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Очистка ключей от случайных пробелов и переносов
if TELEGRAM_TOKEN:
    TELEGRAM_TOKEN = TELEGRAM_TOKEN.strip()
if GROQ_API_KEY:
    GROQ_API_KEY = GROQ_API_KEY.strip()

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ Ошибка: Не найдены TELEGRAM_TOKEN или GROQ_API_KEY в переменных окружения!")

# Настройки Groq (Llama 3.1 - быстрая и надежная)
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ═══ ПРОМПТЫ АГЕНТОВ ═══
PROMPT_ANALYST = "Ты аналитик. Преврати запрос в краткое ТЗ (1 предложение). Только суть."
PROMPT_CODER = "Ты Senior Python Dev. Пиши чистый, рабочий код по ТЗ. Без лишних слов, только код и минимальные комментарии."
PROMPT_REVIEWER = "Ты Ревьюер. Проверь код на ошибки. Если всё ок — пиши 'APPROVED'. Если есть ошибки — напиши кратко, что исправить."

# ═══ ИНТЕГРАЦИЯ С GROQ ═══
async def call_groq(system_prompt, user_content):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.2,
        "max_tokens": 1500
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GROQ_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Groq API Error {resp.status}: {text}")
                    return None
                data = await resp.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Network error calling Groq: {e}")
        return None

# ═══ ПАЙПЛАЙН ═══
async def run_pipeline(task_text, message, bot, status_msg_id):
    try:
        # 1. Аналитик
        logger.info(f"📝 Запрос: {task_text[:50]}...")
        task_spec = await call_groq(PROMPT_ANALYST, task_text)
        if not task_spec:
            raise Exception("Аналитик не ответил")
        
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Пишу код...")

        # 2. Кодер
        code = await call_groq(PROMPT_CODER, task_spec)
        if not code:
            raise Exception("Кодер не ответил")

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Проверяю код...")

        # 3. Ревьюер
        review_context = f"ТЗ: {task_spec}\nКОД:\n{code}"
        review = await call_groq(PROMPT_REVIEWER, review_context)
        
        is_approved = review and "APPROVED" in review.upper()
        
        result_text = f"✅ <b>Готово!</b>\n\n📋 <b>Задача:</b>\n{task_spec}\n\n💻 <b>Код:</b>\n<code>{code}</code>"
        if not is_approved:
            result_text += f"\n\n⚠️ <b>Замечания:</b>\n{review}"

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=result_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"❌ Ошибка: {str(e)}")

# ═══ БОТ ═══
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ Пример: /task напиши калькулятор")
        return
    
    task = message.text.split(maxsplit=1)[1]
    status_msg = await message.answer("⏳ Агенты думают...")
    
    # Запуск в фоне
    asyncio.create_task(run_pipeline(task, message, bot, status_msg.message_id))

@dp.message()
async def echo(message: types.Message):
    await message.answer("Нажми /task и опиши задачу.")

# ═══ ЗАПУСК С ЗАЩИТОЙ ОТ КОНФЛИКТОВ ═══
async def main():
    # Принудительно закрываем старые сессии перед стартом (решает проблему Conflict)
    await bot.session.close()
    
    logger.info("🚀 Бот запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
