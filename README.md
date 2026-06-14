# 🚗 AutoBazar NL — Telegram Bot

> Telegram marketplace bot for buying and selling cars in the Netherlands.

**Bot:** [@autobazar_nl_bot](https://t.me/autobazar_nl_bot)  
**Channel:** [@autobazar_nederland](https://t.me/autobazar_nederland)

---

## Features

### For users
- 📝 Post listings via Telegram Mini App (make, model, year, mileage, up to 10 photos)
- 🤖 AI photo validation — only real cars accepted (Claude Haiku)
- ✨ One-click AI description improvement
- 🖼 Automatic `@autobazar_nederland` watermark on photos
- 🔍 Search by make, model, year, price, city
- 📋 Manage your listings: hide / show / extend / delete / edit photos & description
- ⏰ Expiry reminders 1–4 days before, auto-removal after 30 days

### For moderators
- 🛡 Moderator panel via `/mod` — private WebApp for the owner only
- 👁 View all listings with status filter (active / hidden / expired)
- ⚙️ Full control over any listing: edit text/price/photos, hide, extend, delete
- 👤 Owner info displayed (username, phone, created date)

### Automation
- 📆 Daily repost of all active listings to channel
- 🧹 Auto-cleanup of expired listings

---

## Tech Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v22+
- [aiohttp](https://docs.aiohttp.org/) — web server for Mini App
- [aiosqlite](https://github.com/omnilib/aiosqlite) — async SQLite
- [Pillow](https://pillow.readthedocs.io/) — photo watermarking
- [Anthropic API](https://docs.anthropic.com/) — Claude Haiku for AI features

---

## Project Structure

```
autobazar/
├── bot.py           # main bot + web server (aiohttp)
├── database.py      # SQLite via aiosqlite
├── requirements.txt
├── railway.toml     # Railway deployment config
├── .env.example     # environment variables template
└── webapp/
    └── index.html   # Telegram Mini App (user + moderator)
```

---

## Deploy on Railway

1. Create a project on [railway.app](https://railway.app)
2. Set Root Directory: `autobazar`
3. Add environment variables (see below)
4. Railway runs `python bot.py` automatically

## Environment Variables

```env
TELEGRAM_TOKEN=       # token from @BotFather
ANTHROPIC_API_KEY=    # Anthropic API key
CHANNEL_ID=           # @username or -100... of the channel
WEBAPP_URL=           # public Railway service URL
ADMIN_ID=             # owner's numeric Telegram ID (for /mod panel)
```

> `ADMIN_ID` is optional. Without it, the moderator panel is disabled.

---

## Local Development

```bash
cd autobazar
pip install -r requirements.txt
cp .env.example .env  # fill in values
python bot.py
```

---

---

# 🚗 AutoBazar NL — Telegram Бот

> Telegram-бот маркетплейс для покупки и продажи автомобилей в Нидерландах.

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
- 📋 Управление объявлениями: скрыть / показать / продлить / удалить / изменить фото и описание
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

## Стек

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v22+
- [aiohttp](https://docs.aiohttp.org/) — веб-сервер для Mini App
- [aiosqlite](https://github.com/omnilib/aiosqlite) — асинхронная SQLite
- [Pillow](https://pillow.readthedocs.io/) — ватермарка на фото
- [Anthropic API](https://docs.anthropic.com/) — Claude Haiku для AI-функций

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
