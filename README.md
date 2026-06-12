# 🚗 AutoBazar NL — Telegram Bot

Telegram-бот для покупки и продажи автомобилей в Нидерландах. Публикует объявления в канал [@autobazar_nederland](https://t.me/autobazar_nederland) с AI-улучшением описания.

**Бот:** [@autobazar_nl_bot](https://t.me/autobazar_nl_bot)

---

## Возможности

- **Подача объявления** — пошаговая форма: марка, модель, год, пробег, цена, топливо, КПП, цвет, город, описание, до 10 фото
- **AI-улучшение** — Claude Haiku автоматически улучшает описание объявления
- **Публикация в канал** — объявление с фотогалереей уходит в @autobazar_nederland
- **Управление объявлениями** — просмотр, продление (30 дней), удаление
- **Поиск** — поиск по марке, модели и максимальной цене

## Стек

| Компонент | Технология |
|-----------|------------|
| Бот | python-telegram-bot 22.x (async) |
| База данных | SQLite + aiosqlite |
| AI | Anthropic Claude Haiku |
| Деплой | Railway |
| Python | 3.9+ |

## Структура

```
My First Bot/
├── bot.py           # Основная логика бота
├── database.py      # Работа с SQLite
├── requirements.txt # Зависимости
└── railway.toml     # Конфиг деплоя
```

## Локальный запуск

```bash
# 1. Клонировать репозиторий
git clone https://github.com/linaaadd/My-First-Bot.git
cd My-First-Bot

# 2. Создать виртуальное окружение
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac

# 3. Установить зависимости
pip install -r "My First Bot/requirements.txt"

# 4. Создать .env файл
cp "My First Bot/.env.example" "My First Bot/.env"
# Заполнить переменные в .env

# 5. Запустить
cd "My First Bot"
python bot.py
```

## Переменные окружения

Создай файл `.env` в папке `My First Bot/`:

```env
TELEGRAM_TOKEN=your_bot_token_here
ANTHROPIC_API_KEY=your_anthropic_key_here
CHANNEL_ID=@autobazar_nederland
```

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_TOKEN` | Токен бота от @BotFather |
| `ANTHROPIC_API_KEY` | API ключ Anthropic (Claude) |
| `CHANNEL_ID` | Username канала для публикаций |

## Деплой на Railway

Репозиторий настроен для автодеплоя на Railway:
- Root Directory: `My First Bot`
- Build: nixpacks
- Start: `python bot.py`

После форка добавь переменные окружения в Railway Dashboard → Variables.

## Команды бота

| Команда | Действие |
|---------|----------|
| `/start` | Главное меню |
| `Подать объявление` | Начать форму подачи |
| `Мои объявления` | Управление своими объявлениями |
| `Поиск авто` | Поиск по базе |

---

Канал: [@autobazar_nederland](https://t.me/autobazar_nederland)
