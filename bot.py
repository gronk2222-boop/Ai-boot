import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv
from aiohttp import web

# Загрузка переменных окружения
load_dotenv()

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")  # Ключ от Groq

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("Ошибка: Не найдены TELEGRAM_TOKEN или GROQ_API_KEY")

# Настройки Groq
# Используем быструю и бесплатную модель Qwen
MODEL_NAME = "qwen-2.5-coder-32b" 
API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ═══ ПРОМПТЫ АГЕНТОВ ═══
ASSISTANT_PROMPT = "Ты аналитик. Преврати запрос в краткое ТЗ (1-2 предложения). Только суть."
CODER_PROMPT = "Ты Senior Python Dev. Напиши чистый, безопасный код по ТЗ. Только код."
REVIEWER_PROMPT = "Ты ревьюер. Проверь код на соответствие ТЗ. Если ОК — пиши 'APPROVED'. Если ошибки — кратко опиши."

# ═══ ВЫЗОВ LLM (GROQ) ═══
async def call_llm(system_prompt, user_content):
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
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"Groq Error {response.status}: {error_text}")
                    return None
                
                data = await response.json()
                content = data['choices'][0]['message']['content'].strip()
                return content
    except Exception as e:
        print(f"Network error: {e}")
        return None

# ═══ ПАЙПЛАЙН ═══
async def run_pipeline(task_text, message, bot, status_msg_id):
    try:
        # 1. Аналитик
        task_spec = await call_llm(ASSISTANT_PROMPT, task_text)
        if not task_spec:
            raise Exception("Ошибка связи с AI (Аналитик)")
        
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Пишу код...")

        # 2. Кодер
        code_solution = await call_llm(CODER_PROMPT, task_spec)
        if not code_solution:
            raise Exception("Ошибка связи с AI (Кодер)")

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Проверяю код...")

        # 3. Ревьюер
        review_context = f"ЗАДАНИЕ:\n{task_spec}\n\nКОД:\n{code_solution}"
        review_result = await call_llm(REVIEWER_PROMPT, review_context)
        
        if not review_result:
            raise Exception("Ошибка связи с AI (Ревьюер)")

        # Финал
        is_approved = "APPROVED" in review_result.upper()
        final_text = f"✅ <b>Готово!</b>\n\n📝 <b>ТЗ:</b>\n{task_spec}\n\n💻 <b>Код:</b>\n<code>{code_solution}</code>"
        
        if not is_approved:
            final_text += f"\n\n⚠️ <b>Замечания:</b>\n{review_result}"

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=final_text, parse_mode="HTML")

    except Exception as e:
        print(f"Pipeline failed: {e}")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"❌ Ошибка: {str(e)}")

# ═══ TELEGRAM HANDLERS ═══
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ Использование: /task <описание задачи>")
        return

    user_task = message.text.split(maxsplit=1)[1]
    status_message = await message.answer("⏳ Агенты думают...")
    asyncio.create_task(run_pipeline(user_task, message, bot, status_message.message_id))

@dp.message()
async def echo_all(message: types.Message):
    await message.answer("Нажми /task и опиши задачу.")

# ═══ WEB SERVER ДЛЯ RAILWAY (HEALTHCHECK) ═══
async def handle_healthcheck(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("Web server started on port 8080")

# ═══ ЗАПУСК ═══
async def main():
    # Запускаем веб-сервер в фоне, чтобы Railway не убивал контейнер
    asyncio.create_task(start_web_server())
    
    print("Bot polling started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
