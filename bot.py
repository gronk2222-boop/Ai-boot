import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.exceptions import TelegramConflictError
from dotenv import load_dotenv

# Загрузка переменных
load_dotenv()

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ Ошибка: Не найдены TELEGRAM_TOKEN или GROQ_API_KEY в настройках Railway!")

# Настройки Groq
API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "qwen-2.5-coder-32b" # Самая мощная бесплатная модель

# Промпты агентов
ASSISTANT_PROMPT = "Ты аналитик. Преврати запрос в краткое ТЗ (1 предложение). Только суть."
CODER_PROMPT = "Ты разработчик. Напиши Python-код по ТЗ. Только код, без лишних слов."
REVIEWER_PROMPT = "Ты ревьюер. Если код ок, пиши 'APPROVED'. Если нет — кратко укажи ошибку."

# ═══ ФУНКЦИИ ═══

async def call_groq(system_prompt, user_content):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": 1500,
        "temperature": 0.2
    }

    try:
        # Увеличили таймаут до 15 сек (Groq быстрый, но сеть может тупить)
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"❌ Groq Error {resp.status}: {text}")
                    return None
                data = await resp.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"❌ Network Error: {e}")
        return None

async def run_pipeline(task_text, message, bot, status_msg_id):
    try:
        # 1. Аналитик
        task_spec = await call_groq(ASSISTANT_PROMPT, task_text)
        if not task_spec:
            raise Exception("Аналитик молчит (проблема с ключом или сетью)")
        
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Пишу код...")

        # 2. Кодер
        code = await call_groq(CODER_PROMPT, task_spec)
        if not code:
            raise Exception("Кодер молчит")

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Проверяю код...")

        # 3. Ревьюер
        review_input = f"ТЗ: {task_spec}\nКОД: {code}"
        review = await call_groq(REVIEWER_PROMPT, review_input)
        
        if not review:
            raise Exception("Ревьюер молчит")

        is_ok = "APPROVED" in review.upper()
        result_text = f"✅ <b>Готово!</b>\n\n📝 <b>Задача:</b> {task_spec}\n\n💻 <b>Код:</b>\n<code>{code}</code>"
        if not is_ok:
            result_text += f"\n\n⚠️ <b>Замечания:</b> {review}"

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=result_text, parse_mode="HTML")

    except Exception as e:
        print(f"❌ Pipeline failed: {e}")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"❌ Ошибка: {str(e)}")

# ═══ ЗАПУСК ═══

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("Пример: /task напиши калькулятор")
        return
    
    status = await message.answer("⏳ Агенты думают...")
    asyncio.create_task(run_pipeline(message.text.split(maxsplit=1)[1], message, bot, status.message_id))

async def main():
    print("🚀 Бот запускается...")
    # Обработка конфликта при старте
    while True:
        try:
            await dp.start_polling(bot)
            break
        except TelegramConflictError:
            print("⚠️ Конфликт: бот запущен в другом месте. Жду 5 сек...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
