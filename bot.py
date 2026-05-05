import os
import asyncio
import aiohttp
import json
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ═══ КОНФИГУРАЦИЯ ═══
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if TELEGRAM_TOKEN:
    TELEGRAM_TOKEN = TELEGRAM_TOKEN.strip()
if GROQ_API_KEY:
    GROQ_API_KEY = GROQ_API_KEY.strip()

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ Ошибка: Не найдены TELEGRAM_TOKEN или GROQ_API_KEY")

# Модели Groq
MODEL_TEXT = "llama-3.1-8b-instant"       # Для текста
MODEL_WHISPER = "whisper-large-v3-turbo"  # Для расшифровки голоса
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Файл для хранения задач (имитация базы данных/календаря)
TASKS_FILE = "user_tasks.txt"

# ═══ ПРОМПТЫ АГЕНТОВ ═══

ROUTER_PROMPT = """
Ты — Диспетчер. Определи тип запроса:
1. "CODE" — написание кода, отладка, IT вопросы.
2. "ASSISTANT" — планирование, заметки, идеи, анализ текста, личные дела.
3. "VOICE_TASK" — если текст пришел из голосового сообщения (пользователь что-то просит сделать/запомнить).

Ответь ТОЛЬКО JSON: {"type": "TYPE", "summary": "суть в 5 словах"}
"""

CODER_PROMPT = """
Ты Senior Python Dev. Пиши чистый, рабочий код. Без воды.
"""

ASSISTANT_PROMPT = """
Ты Личный Ассистент. Твоя цель — помогать пользователю организовывать дела.
Если пользователь просит что-то запомнить, добавить в план или расписать шаги — выполни это.
В конце ответа, если была задача на будущее, добавь фразу: [SAVE_TASK: краткая суть задачи], чтобы я мог сохранить её в календарь.
"""

# ═══ УТИЛИТЫ ═══

def save_task_to_file(task_description):
    """Сохраняет задачу в локальный файл (имитация календаря)"""
    try:
        with open(TASKS_FILE, "a", encoding="utf-8") as f:
            f.write(f"- {task_description}\n")
        logger.info(f"✅ Задача сохранена: {task_description}")
        return True
    except Exception as e:
        logger.error(f"Ошибка записи в файл: {e}")
        return False

def get_tasks_from_file():
    """Читает задачи из файла"""
    if not os.path.exists(TASKS_FILE):
        return "Список задач пуст."
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return content if content else "Список задач пуст."

# ═══ ИНТЕГРАЦИЯ С GROQ ═══

async def call_groq_text(system_prompt, user_content, response_format=None):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_TEXT,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.2,
        "max_tokens": 1500
    }
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GROQ_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.error(f"API Error: {await resp.text()}")
                    return None
                data = await resp.json()
                return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Network error: {e}")
        return None

async def transcribe_voice(file_path):
    """Отправляет аудиофайл в Groq Whisper для расшифровки"""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    
    # Формируем multipart/form-data запрос вручную для aiohttp
    form_data = aiohttp.FormData()
    form_data.add_field('file', open(file_path, 'rb'), filename='voice.ogg', content_type='audio/ogg')
    form_data.add_field('model', MODEL_WHISPER)

    try:
        async with aiohttp.ClientSession() as session:
            # Endpoint для транскрибации отличается от чата
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            async with session.post(url, data=form_data, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    logger.error(f"Whisper Error: {await resp.text()}")
                    return None
                data = await resp.json()
                return data.get('text', '').strip()
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        return None

# ═══ ЛОГИКА ПАЙПЛАЙНА ═══

async def process_code_task(summary, request, message, bot, status_msg_id):
    await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Кодер пишет решение...")
    result = await call_groq_text(CODER_PROMPT, f"Задача: {request}")
    if result:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"💻 <b>Код:</b>\n<code>{result}</code>", parse_mode="HTML")
    else:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="❌ Ошибка генерации кода")

async def process_assistant_task(summary, request, message, bot, status_msg_id):
    await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="⏳ Ассистент думает...")
    result = await call_groq_text(ASSISTANT_PROMPT, f"Запрос: {request}")
    
    if result:
        # Проверка, нужно ли сохранить задачу
        saved = False
        if "[SAVE_TASK:" in result:
            try:
                # Извлекаем текст между [SAVE_TASK: и ]
                task_text = result.split("[SAVE_TASK:")[1].split("]")[0].strip()
                if save_task_to_file(task_text):
                    saved = True
                    # Убираем техническую метку из ответа пользователю
                    result = result.replace(f"[SAVE_TASK: {task_text}]", "").replace(f"[SAVE_TASK:{task_text}]", "").strip()
            except Exception:
                pass
        
        final_text = f"🤖 <b>Ассистент:</b>\n\n{result}"
        if saved:
            final_text += "\n\n✅ <i>Задача добавлена в ваш список дел!</i>"
            
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=final_text, parse_mode="HTML")
    else:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text="❌ Ошибка ассистента")

async def run_pipeline(text_content, message, bot, status_msg_id, is_voice=False):
    """Единый пайплайн для текста и расшифрованного голоса"""
    try:
        prefix = "🎤 (Голосовое): " if is_voice else ""
        full_request = f"{prefix}{text_content}"
        
        # Роутинг
        router_json = await call_groq_text(ROUTER_PROMPT, full_request, response_format="json")
        
        if not router_json:
            raise Exception("Роутер молчит")
            
        clean_json = router_json.replace("```json", "").replace("```", "").strip()
        decision = json.loads(clean_json)
        
        task_type = decision.get("type", "ASSISTANT")
        summary = decision.get("summary", "")
        
        logger.info(f"🔀 Маршрут: {task_type}")

        if task_type == "CODE":
            await process_code_task(summary, full_request, message, bot, status_msg_id)
        else:
            # ASSISTANT или VOICE_TASK обрабатываются одинаково (ассистентом)
            await process_assistant_task(summary, full_request, message, bot, status_msg_id)

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg_id, text=f"❌ Ошибка: {str(e)}")

# ═══ БОТ ═══
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой умный ассистент с поддержкой голоса.\n\n"
        "🔹 <b>Что я умею:</b>\n"
        "• Писать код (просто попроси).\n"
        "• Планировать дела и сохранять их в память.\n"
        "• Расшифровывать голосовые сообщения и выполнять их.\n\n"
        "📝 Команды:\n"
        "/tasks — показать список дел\n"
        "/clear — очистить список дел"
    )

@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    tasks = get_tasks_from_file()
    await message.answer(f"📋 <b>Ваши задачи:</b>\n\n{tasks}", parse_mode="HTML")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    if os.path.exists(TASKS_FILE):
        os.remove(TASKS_FILE)
        await message.answer("🗑️ Список задач очищен.")
    else:
        await message.answer("Список и так пуст.")

# Обработка голосовых сообщений
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    status_msg = await message.answer("⏳ Слушаю и расшифровываю...")
    
    # Скачиваем файл
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = f"temp_{message.from_user.id}.ogg"
    await bot.download_file(file.file_path, file_path)
    
    try:
        # Транскрибация
        text = await transcribe_voice(file_path)
        if text:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text=f"🎤 Вы сказали: <i>{text}</i>\n\n⏳ Обрабатываю...", parse_mode="HTML")
            # Запускаем пайплайн с распознанным текстом
            await run_pipeline(text, message, bot, status_msg.message_id, is_voice=True)
        else:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text="❌ Не удалось разобрать речь.")
    finally:
        # Удаляем временный файл
        if os.path.exists(file_path):
            os.remove(file_path)

# Обработка обычного текста
@dp.message()
async def handle_text(message: types.Message):
    if message.text and message.text.startswith('/'):
        return
    
    status_msg = await message.answer("⏳ Думаю...")
    await run_pipeline(message.text, message, bot, status_msg.message_id)

# ═══ ЗАПУСК ═══
async def main():
    await bot.session.close()
    logger.info("🚀 Бот запущен (Кодер + Ассистент + Голос)")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
