import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardRemove
from dotenv import load_dotenv

# Загрузка переменных
load_dotenv()

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Очистка ключей от случайных пробелов
if TELEGRAM_TOKEN: TELEGRAM_TOKEN = TELEGRAM_TOKEN.strip()
if GROQ_API_KEY: GROQ_API_KEY = GROQ_API_KEY.strip()

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ Ошибка: Не найдены TELEGRAM_TOKEN или GROQ_API_KEY в переменных окружения!")

# Настройки Groq
# Используем стабильную модель qwen-2.5-coder-32b
MODEL_NAME = "qwen-2.5-coder-32b" 
API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ═══ ПРОМПТЫ ═══
PROMPT_ANALYST = "Ты аналитик. Преврати запрос в краткое ТЗ (1 предложение). Только суть."
PROMPT_CODER = "Ты программист. Напиши код по ТЗ. Только код, без лишних слов."
PROMPT_REVIEWER = "Ты ревьюер. Если код ок, пиши 'APPROVED'. Если нет — кратко напиши ошибку."

# ═══ ЛОГИКА ═══

async def call_groq(system_prompt, user_text):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        "temperature": 0.2,
        "max_tokens": 1500
    }

    try:
        # Увеличиваем таймаут до 15 сек (Groq быстрый, но сеть может тупить)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"❌ Groq Error {resp.status}: {text}")
                    return None
                data = await resp.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"❌ Network Error: {e}")
        return None

async def pipeline(task, message, bot, status_id):
    try:
        # 1. Аналитик
        tz = await call_groq(PROMPT_ANALYST, task)
        if not tz: raise Exception("Аналитик не ответил")
        
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_id, text="⏳ Пишу код...")

        # 2. Кодер
        code = await call_groq(PROMPT_CODER, tz)
        if not code: raise Exception("Кодер не ответил")

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_id, text="⏳ Проверяю...")

        # 3. Ревьюер
        review_text = f"ТЗ: {tz}\nКод: {code}"
        review = await call_groq(PROMPT_REVIEWER, review_text)
        
        status = "✅ Готово!" if review and "APPROVED" in review.upper() else "⚠️ Есть замечания"
        
        result = f"{status}\n\n📝 <b>Задача:</b>\n{tz}\n\n💻 <b>Код:</b>\n<code>{code}</code>"
        if review and "APPROVED" not in review.upper():
            result += f"\n\n🔍 <b>Комментарий:</b>\n{review}"

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_id, text=result, parse_mode="HTML")

    except Exception as e:
        print(f"Pipeline crash: {e}")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_id, text=f"❌ Ошибка: {e}")

# ═══ ЗАПУСК ═══
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_start(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("Пример: /task напиши калькулятор")
        return
    
    task = message.text.split(maxsplit=1)[1]
    status_msg = await message.answer("⏳ Агенты думают...")
    
    # Запускаем в фоне
    asyncio.create_task(pipeline(task, message, bot, status_msg.message_id))

@dp.message()
async def echo(message: types.Message):
    await message.answer("Нажми /task и напиши задачу.")

async def main():
    print("🚀 Бот запускается...")
    # Удаляем обновления, чтобы избежать конфликтов при рестарте
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
