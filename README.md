# 🚗 AutoBazar NL — Telegram Bot

Telegram-бот маркетплейс для покупки и продажи автомобилей в Нидерландах.

**Бот:** [@autobazar_nl_bot](https://t.me/autobazar_nl_bot)  
**Канал:** [@autobazar_nederland](https://t.me/autobazar_nederland)

---

## Возможности

### Для пользователей
- 📝 Подача объявления через Telegram Mini App (марка, модель, год, пробег, фото до 10 шт.)
- 🤖 AI-валидация фото — только реальные автомобили (Claude Haiku)
- ✨ AI-улучшение описания одной кнопкой
- 🖼 Автоматическая ватермарка `@autobazar_nederland` на фото
- 🔍 Поиск по марке, модели, году, цене, городу
- 📋 Управление своими объявлениями: скрыть / показать / продлить / удалить / изменить фото и описание
- ⏰ Напоминания об истечении за 1–4 дня, автоудаление через 30 дней

### Для модератора
- 🛡 Панель модератора через `/mod` — отдельный WebApp только для владельца
- 👁 Просмотр всех объявлений с фильтром по статусу (активные / скрытые / истёкшие)
- ⚙️ Полное управление любым объявлением: изменить текст/цену/фото, скрыть, продлить, удалить
- 👤 Информация о владельце (username, телефон, дата создания)

### Автоматика
- 📆 Ежедневный репост всех активных объявлений в канал
- 🧹 Автоочистка истёкших объявлений

---

## Структура

```
autobazar/
├── bot.py           # основной бот + веб-сервер (aiohttp)
├── database.py      # SQLite через aiosqlite
├── requirements.txt
├── railway.toml     # Railway деплой
├── .env.example     # шаблон переменных окружения
└── webapp/
    └── index.html   # Telegram Mini App (пользователь + модератор)
```

---

## Деплой на Railway

1. Создать проект на [railway.app](https://railway.app)
2. Root Directory: `autobazar`
3. Добавить переменные окружения (см. ниже)
4. Railway сам запустит `python bot.py`

## Переменные окружения

```env
TELEGRAM_TOKEN=       # токен от @BotFather
ANTHROPIC_API_KEY=    # ключ Anthropic API
CHANNEL_ID=           # @username или -100... канала
WEBAPP_URL=           # публичный URL сервиса (Railway URL)
ADMIN_ID=             # числовой Telegram ID владельца (для /mod)
```

> `ADMIN_ID` — опционально. Без него панель модератора недоступна.

---

## Локальный запуск

```bash
cd autobazar
pip install -r requirements.txt
cp .env.example .env  # заполнить значения
python bot.py
```

---

## Стек

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v22+
- [aiohttp](https://docs.aiohttp.org/) — веб-сервер для Mini App
- [aiosqlite](https://github.com/omnilib/aiosqlite) — асинхронная SQLite
- [Pillow](https://pillow.readthedocs.io/) — ватермарка на фото
- [Anthropic API](https://docs.anthropic.com/) — Claude Haiku для AI-функций
