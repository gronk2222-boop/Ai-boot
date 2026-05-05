import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# ═══ КОНФИГУРАЦИЯ И КЛЮЧИ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Ошибка: Не найдены переменные окружения TELEGRAM_TOKEN или OPENROUTER_API_KEY")

# Настройки модели
MODEL_NAME = "qwen/qwen2.5-coder:free"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# ═══ ЛОГИКА АГЕНТОВ (ПРОМПТЫ) ═══
# Краткие промпты для экономии токенов и скорости

ASSISTANT_PROMPT = "Ты аналитик. Преврати запрос пользователя в краткое техническое задание (1-2 предложения). Только суть, без лишних слов."
CODER_PROMPT = "Ты старший разработчик. Напиши чистый, безопасный Python-код по заданию. Только код, минимум комментариев."
REVIEWER_PROMPT = "Ты ревьюер. Проверь код на соответствие заданию. Если всё верно, напиши только 'APPROVED'. Если есть ошибки, напиши кратко, что исправить."

# ═══ ИНТЕГРАЦИЯ С OPENROUTER ═══

async def call_llm(system_prompt, user_content):
    """Отправляет запрос к OpenRouter и возвращает ответ."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://huggingface.co", # Требуется OpenRouter
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
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"API Error {response.status}: {error_text}")
                    return None
                
                data = await response.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"Network error: {e}")
        return None

# ═══ ПАЙПЛАЙН АГЕНТОВ ═══

async def run_pipeline(task_text, message, bot, status_msg_id):
    """Запускает цепочку: Ассистент -> Кодер -> Ревьюер"""
    try:
        # Шаг 1: Ассистент формулирует ТЗ
        task_spec = await call_llm(ASSISTANT_PROMPT, task_text)
        if not task_spec:
            raise Exception("Ошибка анализа задачи")
        
        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text="⏳ Агенты пишут код..."
        )

        # Шаг 2: Кодер пишет решение
        code_solution = await call_llm(CODER_PROMPT, task_spec)
        if not code_solution:
            raise Exception("Ошибка генерации кода")

        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text="⏳ Ревьюер проверяет код..."
        )

        # Шаг 3: Ревьюер проверяет результат
        review_context = f"ЗАДАНИЕ:\n{task_spec}\n\nКОД:\n{code_solution}"
        review_result = await call_llm(REVIEWER_PROMPT, review_context)
        
        if not review_result:
            raise Exception("Ошибка проверки кода")

        # Формирование финального ответа
        is_approved = "APPROVED" in review_result.upper()
        
        final_text = f"✅ <b>Готово!</b>\n\n📝 <b>Задача:</b>\n{task_spec}\n\n💻 <b>Код:</b>\n<code>{code_solution}</code>"
        
        if not is_approved:
            final_text += f"\n\n⚠️ <b>Замечания:</b>\n{review_result}"

        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text=final_text, 
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Pipeline failed: {e}")
        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text=f"❌ Произошла ошибка: {str(e)}"
        )

# ═══ TELEGRAM HANDLERS ═══

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ Использование: /task <ваше описание задачи>")
        return

    user_task = message.text.split(maxsplit=1)[1]
    
    # Мгновенная реакция
    status_message = await message.answer("⏳ Агенты обрабатывают запрос...")
    
    # Запуск в фоне, чтобы не блокировать бота
    asyncio.create_task(run_pipeline(user_task, message, bot, status_message.message_id))

@dp.message()
async def echo_all(message: types.Message):
    await message.answer("Нажмите /task и опишите задачу, которую нужно решить кодом.")

# ═══ ЗАПУСК ═══

async def main():
    print("Bot started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
