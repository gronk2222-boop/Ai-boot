import os
import asyncio
import aiohttp
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardRemove
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Проверка ключей
if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ Ошибка запуска: Не найдены TELEGRAM_TOKEN или GROQ_API_KEY в переменных окружения!")

# Настройки Groq (ВНИМАНИЕ: Имя модели должно быть точным)
GROQ_MODEL = "qwen-2.5-coder-32b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Промпты агентов
PROMPTS = {
    "analyst": "Ты аналитик. Сформулируй задачу пользователя в одно четкое техническое предложение. Без лишних слов.",
    "coder": "Ты Senior Python Developer. Напиши рабочий, безопасный код по задаче. Только код, без длинных объяснений.",
    "reviewer": "Ты ревьюер. Проверь код. Если всё ок, напиши 'APPROVED'. Если есть ошибки, напиши кратко, что исправить."
}

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ═══ ЛОГИКА GROQ ═══
async def call_groq(system_prompt, user_text):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        "temperature": 0.2,
        "max_tokens": 1500
    }

    try:
        # Добавляем таймаут, чтобы не висеть вечно
        timeout = aiohttp.ClientTimeout(total=15) 
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(GROQ_URL, json=payload, headers=headers) as resp:
                text_response = await resp.text()
                
                if resp.status != 200:
                    logger.error(f"❌ Groq API Error {resp.status}: {text_response}")
                    return None
                
                data = await resp.json()
                content = data['choices'][0]['message']['content']
                return content.strip()
                
    except asyncio.TimeoutError:
        logger.error("⏳ Таймаут соединения с Groq (сервер не ответил за 15 сек)")
        return None
    except Exception as e:
        logger.error(f"💥 Критическая ошибка сети при вызове Groq: {type(e).__name__} - {e}")
        return None

# ═══ ПАЙПЛАЙН ═══
async def run_pipeline(task_text, message, bot, status_msg_id):
    try:
        # 1. Аналитик
        logger.info(f"📝 Запрос пользователя: {task_text[:50]}...")
        task_spec = await call_groq(PROMPTS["analyst"], task_text)
        
        if not task_spec:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="❌ Ошибка: Аналитик не ответил (проверь логи Railway)")
            return

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Пишем код...")

        # 2. Кодер
        code = await call_groq(PROMPTS["coder"], task_spec)
        if not code:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="❌ Ошибка: Кодер молчит")
            return

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Ревью проверяет...")

        # 3. Ревьюер
        review_input = f"ЗАДАЧА: {task_spec}\nКОД:\n{code}"
        review = await call_groq(PROMPTS["reviewer"], review_input)
        
        is_approved = review and "APPROVED" in review.upper()
        
        result_text = f"✅ <b>Готово!</b>\n\n📝 <b>Суть:</b>\n{task_spec}\n\n💻 <b>Код:</b>\n<code>{code}</code>"
        if not is_approved and review:
            result_text += f"\n\n⚠️ <b>Замечания:</b>\n{review}"

        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=result_text, parse_mode="HTML")

    except Exception as e:
        logger.exception(f"💥 Ошибка в пайплайне: {e}")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"❌ Внутренняя ошибка: {e}")

# ═══ HANDLERS ═══
@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("Пример: /task напиши калькулятор")
        return

    task = message.text.split(maxsplit=1)[1]
    status_msg = await message.answer("⏳ Агенты думают...")
    
    # Запуск в фоне
    asyncio.create_task(run_pipeline(task, message, bot, status_msg.message_id))

@dp.message()
async def echo(message: types.Message):
    await message.answer("Нажми /task и опиши задачу.")

# ═══ ЗАПУСК + HEALTHCHECK ═══
async def on_startup(app):
    logger.info("🤖 Бот запущен и готов к работе!")

async def healthcheck(request):
    return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get('/', healthcheck)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("🌐 Web-server started on port 8080")

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
