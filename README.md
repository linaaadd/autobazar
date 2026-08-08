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
├── bot.py             # main bot + web server (aiohttp)
├── database.py        # SQLite via aiosqlite
├── requirements.txt
├── Dockerfile         # runtime image
├── docker-compose.yml # bot + one of two HTTPS front ends
├── Caddyfile          # reverse proxy, automatic Let's Encrypt
├── deploy.sh          # one-shot deploy on a fresh VM
├── oci-bootstrap.sh   # Oracle Cloud: network, instance, reserved IP
├── DEPLOY.md          # full deployment walkthrough
├── .env.example       # environment variables template
└── webapp/
    └── index.html     # Telegram Mini App (user + moderator)
```

---

## Deployment

The bot needs three things at once: a process that never sleeps (long polling),
a persistent disk for the SQLite database, and a public HTTPS URL with a valid
certificate — Telegram Mini Apps reject anything else. It runs on an Oracle
Cloud Always Free VM under Docker Compose.

```bash
git clone https://github.com/linaaadd/autobazar.git
cd autobazar/autobazar
cp .env.example .env    # fill in the values
./deploy.sh caddy
```

`deploy.sh` installs Docker, opens the firewall, derives the public hostname
from the machine's IP, builds and starts everything, and waits for the health
check. Two HTTPS options: `caddy` needs no domain (it serves over
`<ip-with-dashes>.sslip.io`), `tunnel` routes through a Cloudflare Tunnel and
opens no inbound ports at all.

See [DEPLOY.md](autobazar/DEPLOY.md) for provisioning the machine itself.

## Environment Variables

```env
TELEGRAM_TOKEN=       # token from @BotFather
ANTHROPIC_API_KEY=    # Anthropic API key
CHANNEL_ID=           # @username or -100... of the channel
WEBAPP_URL=           # public HTTPS URL (deploy.sh fills this in)
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
├── bot.py             # основной бот + веб-сервер (aiohttp)
├── database.py        # SQLite через aiosqlite
├── requirements.txt
├── Dockerfile         # образ для запуска
├── docker-compose.yml # бот + один из двух вариантов HTTPS
├── Caddyfile          # реверс-прокси, Let's Encrypt автоматом
├── deploy.sh          # разворачивание на чистой VM одной командой
├── oci-bootstrap.sh   # Oracle Cloud: сеть, инстанс, резервный IP
├── DEPLOY.md          # полная инструкция по деплою
├── .env.example       # шаблон переменных окружения
└── webapp/
    └── index.html     # Telegram Mini App (пользователь + модератор)
```

---

## Деплой

Боту нужны три вещи одновременно: процесс, который не засыпает (long polling),
постоянный диск под SQLite и публичный HTTPS с валидным сертификатом —
Telegram Mini App не принимает ничего другого. Работает на Oracle Cloud
Always Free под Docker Compose.

```bash
git clone https://github.com/linaaadd/autobazar.git
cd autobazar/autobazar
cp .env.example .env    # заполнить значения
./deploy.sh caddy
```

`deploy.sh` ставит Docker, открывает порты, вычисляет публичный адрес машины,
собирает и запускает всё и дожидается healthcheck. Два варианта HTTPS: `caddy`
не требует домена (отдаёт на `<ip-через-дефисы>.sslip.io`), `tunnel` работает
через Cloudflare Tunnel и не открывает наружу ни одного порта.

Как поднять саму машину — [DEPLOY.md](autobazar/DEPLOY.md).

## Переменные окружения

```env
TELEGRAM_TOKEN=       # токен от @BotFather
ANTHROPIC_API_KEY=    # ключ Anthropic API
CHANNEL_ID=           # @username или -100... канала
WEBAPP_URL=           # публичный HTTPS-адрес (заполняет deploy.sh)
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
