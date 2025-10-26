import os
import re
import logging
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# ----- утилиты форматирования/санитайза -----
CITATION_RE = re.compile(r"【[^】]*】")
FILES_RE = re.compile(r"\b[\w.-]+\.(json|pdf|csv|md)\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")
SPACE_RE = re.compile(r"[ \t]+\n")

def sanitize_markdown(text: str) -> str:
    """Удаляем служебные хвосты и приводим к аккуратному Telegram Markdown."""
    text = CITATION_RE.sub("", text)               # удалить 【...】
    text = FILES_RE.sub("", text)                  # убрать имена файлов
    text = URL_RE.sub("", text)                    # убрать URL
    text = SPACE_RE.sub("\n", text)                # хвостовые пробелы перед \n
    text = text.replace("\r\n", "\n").strip()
    # Иногда модель ставит лишние бэктики/скобки в конце — мягкая очистка:
    text = text.strip("` \n")
    return text

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

if not (OPENAI_API_KEY and TELEGRAM_BOT_TOKEN and ASSISTANT_ID):
    raise RuntimeError("Проверь .env: нужен OPENAI_API_KEY, TELEGRAM_BOT_TOKEN и ASSISTANT_ID")

# Настроим логи
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tg-assistant-NutriMind")

# Инициализируем OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

# Простое хранилище thread_id по chat_id (для демо в памяти процесса)
THREADS = {}  # {chat_id: thread_id}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-помощник.\n"
        "Отправь любое сообщение, и я передам его ассистенту OpenAI."
    )

def get_or_create_thread_id(chat_id: int) -> str:
    """Создаём Thread один раз на чат и переиспользуем для контекста."""
    if chat_id in THREADS:
        return THREADS[chat_id]
    thread = client.beta.threads.create()
    THREADS[chat_id] = thread.id
    return thread.id

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    status_msg = await update.message.reply_text("Обрабатываю запрос...")  # Сообщаем пользователю о процессе обработки

    # 1) Получаем/создаём thread для этого чата
    thread_id = get_or_create_thread_id(chat_id)

    # 2) Добавляем сообщение пользователя в Thread
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_text
    )

    # 3) Запускаем Run ассистента и ждём завершения (create_and_poll)
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
    )

    # 4) Достаём последние сообщения ассистента из Thread
    messages = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=5)
    # Ищем первый ответ ассистента
    reply_text = None
    for m in messages.data:
        if m.role == "assistant":
            # Собираем текстовые части
            parts = []
            for c in m.content:
                if c.type == "text":
                    parts.append(c.text.value)
            if parts:
                reply_text = "\n".join(parts)
                break

    if not reply_text:
        reply_text = "Извини, не удалось получить ответ. Попробуй ещё раз."

    # await update.message.reply_text(reply_text)
    clean = sanitize_markdown(reply_text)
    await update.message.reply_text(
        clean,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
