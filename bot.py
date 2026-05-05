import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# ═══ КОНФИГУРАЦИЯ И КЛЮЧИ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Очистка ключей от лишних символов (пробелы, переносы строк)
if TELEGRAM_TOKEN:
    TELEGRAM_TOKEN = TELEGRAM_TOKEN.strip()
if GROQ_API_KEY:
    GROQ_API_KEY = GROQ_API_KEY.strip()

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("Ошибка: Не найдены переменные окружения TELEGRAM_TOKEN или GROQ_API_KEY")

# Настройки модели Groq
# Используем актуальную модель qwen-3-32b
MODEL_NAME = "qwen-3-32b" 
API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ═══ ЛОГИКА АГЕНТОВ (ПРОМПТЫ) ═══
ASSISTANT_PROMPT = "Ты аналитик. Преврати запрос пользователя в краткое техническое задание (1-2 предложения). Только суть."
CODER_PROMPT = "Ты старший разработчик. Напиши чистый, безопасный Python-код по заданию. Только код."
REVIEWER_PROMPT = "Ты ревьюер. Проверь код на соответствие заданию. Если всё верно, напиши только 'APPROVED'. Если есть ошибки, напиши кратко, что исправить."

# ═══ ИНТЕГРАЦИЯ С GROQ ═══

async def call_groq(system_prompt, user_content):
    """Отправляет запрос к Groq API"""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL_NAME,  # Используем объявленную константу
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
                    print(f"❌ Groq API Error {response.status}: {error_text}")
                    return None
                
                data = await response.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"💥 Критическая ошибка сети при вызове Groq: {e}")
        return None

# ═══ ПАЙПЛАЙН АГЕНТОВ ═══

async def run_pipeline(task_text, message, bot, status_msg_id):
    """Запускает цепочку: Ассистент -> Кодер -> Ревьюер"""
    try:
        print(f"📝 Запрос пользователя: {task_text[:50]}...")

        # Шаг 1: Ассистент формулирует ТЗ
        task_spec = await call_groq(ASSISTANT_PROMPT, task_text)
        if not task_spec:
            raise Exception("Аналитик не ответил")
        
        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text="⏳ Агенты пишут код..."
        )

        # Шаг 2: Кодер пишет решение
        code_solution = await call_groq(CODER_PROMPT, task_spec)
        if not code_solution:
            raise Exception("Кодер не ответил")

        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text="⏳ Ревьюер проверяет код..."
        )

        # Шаг 3: Ревьюер проверяет результат
        review_context = f"ЗАДАНИЕ:\n{task_spec}\n\nКОД:\n{code_solution}"
        review_result = await call_groq(REVIEWER_PROMPT, review_context)
        
        if not review_result:
            raise Exception("Ревьюер не ответил")

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
        print(f"💥 Ошибка в пайплайне: {e}")
        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text=f"❌ Ошибка: {str(e)}"
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
    
    # Запуск в фоне
    asyncio.create_task(run_pipeline(user_task, message, bot, status_message.message_id))

@dp.message()
async def echo_all(message: types.Message):
    await message.answer("Нажмите /task и опишите задачу, которую нужно решить кодом.")

# ═══ WEB SERVER ДЛЯ RAILWAY (HEALTHCHECK) ═══
async def healthcheck(request):
    return web.Response(text="OK")

async def start_web_app():
    app = web.Application()
    app.router.add_get('/', healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("🌐 Web-server started on port 8080")

# ═══ ЗАПУСК ═══

async def main():
    await start_web_app()
    print("🚀 Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
