import os
import re
import logging
import json
import time as _t
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from tools import lookup_product_nutrition

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
THREADS: dict[int, str] = {}  # {chat_id: thread_id}
USER_HISTORY: dict[int, list[str]] = {}  # {chat_id: ["dish1", "dish2"]}
USER_PREFERENCES: dict[int, str] = {}  # {chat_id: "без глютена, веган"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я NutriMind.\n"
        "Например: «Что приготовить на ужин без молочки?» или «Калорийность 100 г гречки?»\n\n"
        "Команды:\n"
        "/my_preferences - посмотреть текущие предпочтения\n"
        "/set_preferences <текст> - установить предпочтения"
    )

async def my_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    preferences = USER_PREFERENCES.get(chat_id, "пока не заданы")
    await update.message.reply_text(f"Ваши предпочтения: {preferences}")

async def set_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите ваши предпочтения после команды.\n"
                                      "Пример: /set_preferences без глютена, веган")
        return

    preferences = " ".join(context.args)
    USER_PREFERENCES[chat_id] = preferences
    await update.message.reply_text(f"Ваши предпочтения обновлены: {preferences}")

def get_or_create_thread_id(chat_id: int) -> str:
    """Создаём Thread один раз на чат и переиспользуем для контекста."""
    if chat_id in THREADS:
        return THREADS[chat_id]
    thread = client.beta.threads.create()
    THREADS[chat_id] = thread.id
    return thread.id

def run_and_wait(thread_id: str, assistant_id: str):
    """
    Запускает run и ждёт завершения, обрабатывая requires_action (tool-calls).
    Возвращает финальный объект run.
    """
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)

    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)

        # Терминальные статусы
        if run.status in ("completed", "failed", "cancelled", "expired"):
            if run.status == "failed":
                err = getattr(run, "last_error", None)
                if err:
                    log.error("Run failed: %s — %s", err.code, err.message)
            return run

        # Обработка инструментов (функций)
        if run.status == "requires_action" and run.required_action and run.required_action.type == "submit_tool_outputs":
            tool_outputs = []
            tcs = run.required_action.submit_tool_outputs.tool_calls
            log.info("Tool calls: %d", len(tcs))
            for tc in tcs:
                name = tc.function.name
                args_raw = tc.function.arguments or "{}"
                log.info("Tool call: %s args=%s", name, args_raw)

                try:
                    args = json.loads(args_raw)
                except Exception:
                    tool_outputs.append({
                        "tool_call_id": tc.id,
                        "output": json.dumps({"status": "error", "message": "invalid_json"}, ensure_ascii=False)
                    })
                    continue

                if name == "lookup_product_nutrition":
                    try:
                        product = str(args["product"]).strip()
                        payload = lookup_product_nutrition(product=product, per="100g")
                        tool_outputs.append({
                            "tool_call_id": tc.id,
                            "output": json.dumps(payload, ensure_ascii=False)
                        })
                    except Exception as e:
                        tool_outputs.append({
                            "tool_call_id": tc.id,
                            "output": json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
                        })
                else:
                    tool_outputs.append({
                        "tool_call_id": tc.id,
                        "output": json.dumps({"status": "error", "message": f"Unknown function: {name}"}, ensure_ascii=False)
                    })

            run = client.beta.threads.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=tool_outputs
            )
            continue

        # Небольшая пауза, чтобы не крутить цикл слишком быстро
        _t.sleep(0.35)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    status_msg = await update.message.reply_text("Обрабатываю запрос...")

    # 1) Получаем/создаём thread для этого чата
    thread_id = get_or_create_thread_id(chat_id)

    # Формируем сообщение с учётом истории и предпочтений
    history = USER_HISTORY.get(chat_id, [])
    preferences = USER_PREFERENCES.get(chat_id)

    extra_prompt = ""
    if preferences:
        extra_prompt += f"\n\n(Мои предпочтения: {preferences})"
    if history:
        extra_prompt += "\n(Не предлагай снова: " + ", ".join(history) + ")"

    # 2) Добавляем сообщение пользователя в Thread
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_text + extra_prompt
    )

    # 3) Запускаем Run ассистента и ждём завершения (с обработкой функций)
    run_and_wait(thread_id, ASSISTANT_ID)

    # 4) Достаём последние сообщения ассистента из Thread
    messages = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=10)
    reply_text = None
    for m in messages.data:
        if m.role == "assistant":
            parts = []
            for c in m.content:
                if c.type == "text":
                    parts.append(c.text.value)
            if parts:
                reply_text = "\n".join(parts)
                break

    if not reply_text:
        reply_text = "Извини, не удалось получить ответ. Попробуй ещё раз."
    else:
        # Простое извлечение названия блюда для истории (пока без сложных регэкспов)
        match = re.search(r"🍴\s*\*(.*?)\*", reply_text)
        if match:
            dish_name = match.group(1).strip()
            if chat_id not in USER_HISTORY:
                USER_HISTORY[chat_id] = []
            if dish_name not in USER_HISTORY[chat_id]:
                USER_HISTORY[chat_id].append(dish_name)
                # Ограничим историю, чтобы она не росла бесконечно
                if len(USER_HISTORY[chat_id]) > 10:
                    USER_HISTORY[chat_id].pop(0)

    # 5) Санитайз + ответ в Telegram с parse_mode=Markdown
    clean = sanitize_markdown(reply_text)
    await status_msg.edit_text(
        clean,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my_preferences", my_preferences))
    app.add_handler(CommandHandler("set_preferences", set_preferences))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
