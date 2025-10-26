# NutriMind Telegram Bot — README

Ассистент по питанию для Telegram: подбирает блюда (завтрак/обед/ужин) из подключённых файлов и по запросу выдаёт калорийность продукта (на 100 г) через внешнее API.

---

## 🚀 Быстрый старт

### 1) Требования
- Python 3.10+
- Аккаунт OpenAI (API-ключ)
- Telegram Bot Token (через @BotFather)

### 2) Клонирование и окружение
```bash
git clone <repo-url> nutrimind-bot
cd nutrimind-bot
python -m venv .venv
# Windows:
.\.venv\Scriptsctivate
# macOS/Linux:
source .venv/bin/activate
```

### 3) Установка зависимостей
```bash
pip install -U pip
pip install openai python-telegram-bot==20.* python-dotenv requests
```

### 4) Конфигурация
Создайте файл `.env` в корне проекта:
```
OPENAI_API_KEY=sk-...ваш_ключ...
TELEGRAM_BOT_TOKEN=...токен_бота...
ASSISTANT_ID=asst_...ID_вашего_ассистента...
```

### 5) Запуск
```bash
python bot.py
```
В Telegram напишите боту, например:  
`Что приготовить на ужин без молочки?`  
`Калорийность 100 г гречки`

---

## 🧠 Что внутри

### Архитектура
```
project/
├─ bot.py                    # Telegram-бот + интеграция с OpenAI Assistants
├─ tools/
│  ├─ __init__.py
│  └─ nutrition_lookup.py    # lookup_product_nutrition: калорийность и БЖУ (Open Food Facts)
└─ .env
```

### Основные компоненты
- **Assistants API (OpenAI)**: диалог + File Search (ваше векторное хранилище).
- **Function call**: `lookup_product_nutrition(product)` — поиск калорийности/БЖУ на 100 г через Open Food Facts (без ключей).
- **Санитайзер Markdown**: убирает служебные метки `【…】`, имена файлов (`.json/.pdf`), URL и лишние пробелы — для корректного рендера в Telegram.
- **parse_mode**: используется `Markdown` (не V2), чтобы минимизировать экранирование.

---

## 🔧 Функции и файлы

### `tools/nutrition_lookup.py`
- `lookup_product_nutrition(product: str, per="100g") -> dict`  
  Ищет продукт в Open Food Facts и возвращает:
  ```json
  {
    "status": "ok",
    "name": "Chicken breast",
    "per": "100g",
    "kcal": 165.0,
    "protein_g": 31.0,
    "fat_g": 3.6,
    "carbs_g": 0.0,
    "fiber_g": null,
    "sugars_g": null,
    "salt_g": 0.12,
    "source": "openfoodfacts",
    "barcode": "...",
    "url": "..."
  }
  ```
  Возможные статусы: `ok`, `not_found`, `incomplete`, `unsupported_per`.

### `bot.py`
- `sanitize_markdown(text)` — чистит ответы перед отправкой в Telegram.
- `get_or_create_thread_id(chat_id)` — создаёт/кеширует Thread для контекста диалога.
- `run_and_wait(thread_id, assistant_id)` — запускает Run и обрабатывает `requires_action`:
  - перехватывает вызов `lookup_product_nutrition`,
  - вызывает локальную функцию из `tools`,
  - отправляет `submit_tool_outputs`,
  - ждёт `completed`.
- Telegram handlers:
  - `/start`
  - обработка текстовых сообщений (передаёт их ассистенту и возвращает отформатированный ответ).

---

## 🧾 Инструкции ассистента (кратко)

Рекомендуемая модель: **gpt-4.1-mini**  
Выберите **Text** в Output. Подключите File Search (ваше векторное хранилище).

**System Instructions (суть):**
- Подбирай блюда (завтрак/обед/ужин), будь кратким и дружелюбным.
- Десерты не предлагай как основное; по согласию — 1 десерт отдельно.
- При запросах калорийности продукта вызывай `lookup_product_nutrition`.
- **Не вставляй ссылки/URL/имена файлов, не показывай служебные цитаты `【…】`.**
- Формат ответа (Telegram Markdown), пример:
  ```
  🍴 *Название блюда*
  🥗 _Категория:_ завтрак / обед / ужин
  🔥 _Калорийность:_ ~NNN ккал (если есть)
  🧂 _Ингредиенты:_ ...
  👩‍🍳 _Шаги:_ 1–3 шага
  ```
- Разрешённые элементы: `*bold*`, `_italic_`, `[inline URL](http://...)`, `[inline mention](tg://user?id=...)`, `` `inline code` ``, тройные бэктики для блоков.

**Function schema (в UI → Tools → Function):**
```json
{
  "name": "lookup_product_nutrition",
  "description": "Найти калорийность и БЖУ по продукту (Open Food Facts)",
  "strict": true,
  "parameters": {
    "type": "object",
    "properties": {
      "product": {
        "type": "string",
        "description": "Название продукта, например: 'гречка', 'apple', 'куриная грудка'"
      }
    },
    "required": ["product"],
    "additionalProperties": false
  }
}
```

---

## 💬 Примеры запросов
- «Что приготовить на ужин без молочки за 20 минут?»  
- «Калорийность 100 г куриной грудки»  
- «БЖУ творог 5% (на 100 г)»  
- «Ещё варианты ужина, но без рыбы»

---

## 🩹 Траблшутинг
- **400: “Can't add messages … while a run is active.”**  
  Причина: в треде есть активный Run в состоянии `requires_action`.  
  Решение: используйте `run_and_wait(...)` (обрабатывайте tool-calls и отправляйте `submit_tool_outputs`) — в коде уже сделано.

- **Ответ сломан/видны `【…】` или `*.json`**  
  Отправляйте текст через:
  ```python
  clean = sanitize_markdown(reply_text)
  await message.reply_text(clean, parse_mode="Markdown", disable_web_page_preview=True)
  ```
  И в System Instructions запретите вывод ссылок/цитат.

- **Assistants API DeprecationWarning**  
  Предупреждение о миграции на Responses API. Для учебного проекта можно оставить, позже легко перенести.

---

## 🗺️ План улучшений (опционально)
- Фолбэк на Nutritionix/Edamam, если OFF даёт `not_found`.
- История «последних блюд» (не повторять рекомендации).
- Учёт ограничений пользователя (без глютена/молочки) в памяти/БД.
- Переход на Responses API.

---

## 📄 Лицензия
MIT 
