import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") # Теперь используем ключ Groq

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ Ошибка: Не найдены переменные TELEGRAM_TOKEN или GROQ_API_KEY в настройках Railway!")

# Настройки Groq API
# Модель qwen-2.5-coder-32b бесплатна и очень быстрая на Groq
MODEL_NAME = "qwen-2.5-coder-32b" 
API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Промпты агентов (краткие)
ASSISTANT_PROMPT = "Ты аналитик. Преврати запрос в краткое ТЗ (1-2 предложения). Только суть."
CODER_PROMPT = "Ты Senior Python Dev. Напиши рабочий код по ТЗ. Без лишних объяснений, только код."
REVIEWER_PROMPT = "Ты ревьюер. Если код ок — пиши 'APPROVED'. Если нет — кратко укажи ошибку."

# ═══ ЛОГИКА ЗАПРОСОВ ═══

async def call_ai(system_prompt, user_content):
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
        "temperature": 0.2,
        "max_tokens": 1500
    }

    # Увеличиваем таймаут до 30 сек на случай медленного ответа
    timeout = aiohttp.ClientTimeout(total=30)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"⚠️ Groq API Error {resp.status}: {error_text}")
                    return None
                
                data = await resp.json()
                content = data['choices'][0]['message']['content'].strip()
                if not content:
                    return None
                return content
    except asyncio.TimeoutError:
        print("⏰ Timeout: Сервер Groq не ответил за 30 сек")
        return None
    except Exception as e:
        print(f"🔥 Network Error: {type(e).__name__}: {e}")
        return None

# ═══ ПАЙПЛАЙН ═══

async def run_pipeline(task_text, message, bot, status_msg_id):
    try:
        # 1. Аналитик
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Анализирую задачу...")
        task_spec = await call_ai(ASSISTANT_PROMPT, task_text)
        
        if not task_spec:
            raise Exception("Не удалось связаться с Аналитиком (проверь логи Railway)")

        # 2. Кодер
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Пишу код...")
        code = await call_ai(CODER_PROMPT, task_spec)
        
        if not code:
            raise Exception("Не удалось связаться с Кодером")

        # 3. Ревьюер
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Проверяю код...")
        review_input = f"ТЗ: {task_spec}\nКОД:\n{code}"
        review = await call_ai(REVIEWER_PROMPT, review_input)
        
        if not review:
            raise Exception("Не удалось связаться с Ревьюером")

        # Финал
        is_ok = "APPROVED" in review.upper()
        status_icon = "✅" if is_ok else "⚠️"
        review_text = "" if is_ok else f"\n\n<i>Замечания:</i> {review}"
        
        final_msg = (
            f"{status_icon} <b>Готово!</b>\n\n"
            f"<b>Задача:</b> {task_spec}\n\n"
            f"<b>Код:</b>\n<code>{code}</code>"
            f"{review_text}"
        )
        
        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text=final_msg, 
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"❌ Pipeline failed: {e}")
        await bot.edit_message_text(
            chat_id=message.chat.id, 
            message_id=status_msg_id, 
            text=f"❌ Ошибка: {str(e)}"
        )

# ═══ БОТ И СЕРВЕР ═══

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("Пример: /task напиши калькулятор")
        return
    
    task = message.text.split(maxsplit=1)[1]
    status_msg = await message.answer("⏳ Запускаю агентов...")
    
    # Запуск в фоне
    asyncio.create_task(run_pipeline(task, message, bot, status_msg.message_id))

@dp.message()
async def echo(message: types.Message):
    await message.answer("Нажми /task и опиши задачу.")

async def healthcheck(request):
    """Веб-сервер для жизни контейнера на Railway"""
    return web.Response(text="OK")

async def main():
    # Запуск веб-сервера в фоне (чтобы Railway не убивал бота)
    app = web.Application()
    app.router.add_get('/', healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("🌍 Web server started on port 8080")

    # Запуск бота
    print("🤖 Bot polling started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
