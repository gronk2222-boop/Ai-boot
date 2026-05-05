import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv
from aiohttp import web

# Загрузка переменных окружения
load_dotenv()

# ═══ КОНФИГУРАЦИЯ И КЛЮЧИ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.getenv("PORT", 8000))  # Порт для Railway

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Ошибка: Не найдены переменные окружения TELEGRAM_TOKEN или OPENROUTER_API_KEY")

# Настройки модели
MODEL_NAME = "qwen/qwen2.5-coder:free"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# ═══ ЛОГИКА АГЕНТОВ (ПРОМПТЫ) ═══
ASSISTANT_PROMPT = "Ты аналитик. Преврати запрос пользователя в краткое техническое задание (1-2 предложения). Только суть."
CODER_PROMPT = "Ты старший разработчик. Напиши чистый, безопасный Python-код по заданию. Только код."
REVIEWER_PROMPT = "Ты ревьюер. Проверь код на соответствие заданию. Если всё верно, напиши только 'APPROVED'. Если ошибки — кратко опиши их."

# ═══ ИНТЕГРАЦИЯ С OPENROUTER ═══

async def call_llm(system_prompt, user_content):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://railway.app",
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
                    print(f"API Error {response.status}: {await response.text()}")
                    return None
                data = await response.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"LLM Network error: {e}")
        return None

# ═══ ПАЙПЛАЙН АГЕНТОВ ═══

async def run_pipeline(task_text, message, bot, status_msg_id):
    try:
        print(f"🚀 Start processing task: {task_text[:30]}...")
        
        # Шаг 1: Ассистент
        task_spec = await call_llm(ASSISTANT_PROMPT, task_text)
        if not task_spec: raise Exception("Ошибка анализа")
        
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Пишем код...")

        # Шаг 2: Кодер
        code_solution = await call_llm(CODER_PROMPT, task_spec)
        if not code_solution: raise Exception("Ошибка генерации")

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Проверяем код...")

        # Шаг 3: Ревьюер
        review_context = f"ЗАДАНИЕ:\n{task_spec}\n\nКОД:\n{code_solution}"
        review_result = await call_llm(REVIEWER_PROMPT, review_context)
        
        if not review_result: raise Exception("Ошибка проверки")

        is_approved = "APPROVED" in review_result.upper()
        final_text = f"✅ <b>Готово!</b>\n\n📝 <b>Задача:</b>\n{task_spec}\n\n💻 <b>Код:</b>\n<code>{code_solution}</code>"
        if not is_approved:
            final_text += f"\n\n⚠️ <b>Замечания:</b>\n{review_result}"

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=final_text, parse_mode="HTML")
        print("✅ Task completed successfully")

    except Exception as e:
        print(f"❌ Pipeline failed: {e}")
        try:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"❌ Ошибка: {str(e)}")
        except:
            pass

# ═══ TELEGRAM HANDLERS ═══

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ Использование: /task <описание задачи>")
        return

    user_task = message.text.split(maxsplit=1)[1]
    status_message = await message.answer("⏳ Агенты обрабатывают запрос...")
    
    # Запуск в фоне
    asyncio.create_task(run_pipeline(user_task, message, bot, status_message.message_id))

@dp.message()
async def echo_all(message: types.Message):
    await message.answer("Нажми /task и опиши задачу.")

# ═══ WEB SERVER ДЛЯ RAILWAY (HEALTHCHECK) ═══
# Этот сервер нужен только чтобы Railway не убивал контейнер

async def handle_health(request):
    return web.Response(text="OK - Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"🌍 Web server started on port {PORT} (Railway Healthcheck)")

# ═══ ЗАПУСК ═══

async def main():
    # Запускаем веб-сервер в фоне
    asyncio.create_task(start_web_server())
    
    print("🤖 Starting Telegram Bot polling...")
    # Увеличиваем таймаут, чтобы соединение было стабильнее
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), skip_updates=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
