import json
import logging
import os
from datetime import datetime

import anthropic
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    create_listing,
    delete_listing,
    extend_listing,
    get_listing,
    get_user_listings,
    init_db,
    search_listings,
    update_listing_channel_msgs,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # например @autobazar_nl или -1001234567890

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ====== СОСТОЯНИЯ ДИАЛОГА: подача объявления ======
(
    ASK_MAKE, ASK_MODEL, ASK_YEAR, ASK_MILEAGE, ASK_PRICE,
    ASK_FUEL, ASK_TRANSMISSION, ASK_COLOR, ASK_CITY,
    ASK_DESCRIPTION, ASK_PHOTOS, CONFIRM,
) = range(12)

# ====== СОСТОЯНИЯ ДИАЛОГА: поиск ======
SEARCH_MAKE, SEARCH_MODEL, SEARCH_PRICE_MAX = range(20, 23)

FUEL_TYPES = ["Бензин", "Дизель", "Гибрид", "Электро", "Газ/Бензин"]
TRANSMISSION_TYPES = ["Автомат", "Механика", "Робот", "Вариатор"]


# ====== КЛАВИАТУРЫ ======

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Подать объявление", callback_data="new_listing")],
        [
            InlineKeyboardButton("🔍 Поиск авто", callback_data="search"),
            InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings"),
        ],
    ])


def fuel_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f, callback_data=f"fuel_{f}")] for f in FUEL_TYPES]
    )


def transmission_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=f"tr_{t}")] for t in TRANSMISSION_TYPES]
    )


def photos_keyboard(count: int):
    label = f"✅ Готово ({count} фото)" if count else "✅ Фото готовы"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="photos_done")]])


# ====== ФОРМАТИРОВАНИЕ ОБЪЯВЛЕНИЯ ======

def listing_caption(listing: dict) -> str:
    expires = datetime.fromisoformat(listing["expires_at"]).strftime("%d.%m.%Y")
    contact = f"@{listing['username']}" if listing.get("username") else "—"
    return (
        f"🚗 <b>{listing['make']} {listing['model']} {listing['year']}</b>\n"
        f"💶 <b>{listing['price']:,} {listing['currency']}</b>\n\n"
        f"📍 {listing['city']}\n"
        f"🛣 {listing['mileage']:,} км\n"
        f"⛽ {listing['fuel']}  |  ⚙️ {listing['transmission']}\n"
        f"🎨 {listing['color']}\n\n"
        f"📝 {listing['description']}\n\n"
        f"👤 Продавец: {contact}\n"
        f"📅 Активно до: {expires}\n"
        f"🆔 #авто{listing['id']}"
    )


def listing_preview(d: dict, photo_count: int, ai_improved: bool = False) -> str:
    tag = " <i>(AI улучшено)</i>" if ai_improved else ""
    return (
        f"📋 <b>Предпросмотр объявления</b>{tag}\n\n"
        f"🚗 {d['make']} {d['model']} {d['year']}\n"
        f"💶 {d['price']:,} EUR\n"
        f"📍 {d['city']}\n"
        f"🛣 {d['mileage']:,} км\n"
        f"⛽ {d['fuel']}  |  ⚙️ {d['transmission']}\n"
        f"🎨 {d['color']}\n"
        f"📸 Фото: {photo_count}\n\n"
        f"📝 {d['description']}"
    )


def confirm_keyboard(ai_done: bool = False):
    rows = []
    if not ai_done:
        rows.append([InlineKeyboardButton("✨ Улучшить описание AI", callback_data="improve_ai")])
    rows.append([InlineKeyboardButton("✅ Опубликовать", callback_data="publish")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_listing")])
    return InlineKeyboardMarkup(rows)


# ====== ПУБЛИКАЦИЯ В КАНАЛ ======

async def post_to_channel(context, listing: dict) -> list[int]:
    caption = listing_caption(listing)
    photos = json.loads(listing["photo_ids"])
    try:
        if len(photos) == 1:
            msg = await context.bot.send_photo(
                chat_id=CHANNEL_ID, photo=photos[0], caption=caption, parse_mode="HTML"
            )
            return [msg.message_id]
        else:
            media = [
                InputMediaPhoto(
                    media=pid,
                    caption=caption if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None,
                )
                for i, pid in enumerate(photos)
            ]
            msgs = await context.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
            return [m.message_id for m in msgs]
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        return []


async def delete_from_channel(context, message_ids: list):
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=mid)
        except Exception:
            pass


# ====== /start ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "Добро пожаловать в <b>AutoBazar</b> — маркетплейс автомобилей.\n\n"
        "Здесь вы можете:\n"
        "• 📝 Подать объявление о продаже\n"
        "• 🔍 Найти нужный автомобиль\n"
        "• 📋 Управлять своими объявлениями",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def go_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🏠 Главное меню:", reply_markup=main_menu_keyboard())


# ====== ПОДАЧА ОБЪЯВЛЕНИЯ ======

async def new_listing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["photos"] = []
    await query.edit_message_text(
        "📝 <b>Подача объявления</b>\n\n"
        "Шаг 1 из 10 — Марка автомобиля\n"
        "<i>Например: Toyota, BMW, Volkswagen</i>",
        parse_mode="HTML",
    )
    return ASK_MAKE


async def ask_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["make"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Марка: <b>{context.user_data['make']}</b>\n\n"
        "Шаг 2 из 10 — Модель\n"
        "<i>Например: Camry, X5, Golf</i>",
        parse_mode="HTML",
    )
    return ASK_MODEL


async def ask_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["model"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Модель: <b>{context.user_data['model']}</b>\n\n"
        "Шаг 3 из 10 — Год выпуска\n"
        "<i>Например: 2020</i>",
        parse_mode="HTML",
    )
    return ASK_YEAR


async def ask_mileage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (1970 <= int(text) <= datetime.now().year):
        await update.message.reply_text("⚠️ Введите корректный год, например: 2020")
        return ASK_YEAR
    context.user_data["year"] = int(text)
    await update.message.reply_text(
        f"✅ Год: <b>{context.user_data['year']}</b>\n\n"
        "Шаг 4 из 10 — Пробег (км)\n"
        "<i>Например: 85000</i>",
        parse_mode="HTML",
    )
    return ASK_MILEAGE


async def ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "").replace(",", "").replace(".", "")
    if not text.isdigit():
        await update.message.reply_text("⚠️ Введите пробег числом, например: 85000")
        return ASK_MILEAGE
    context.user_data["mileage"] = int(text)
    await update.message.reply_text(
        f"✅ Пробег: <b>{context.user_data['mileage']:,} км</b>\n\n"
        "Шаг 5 из 10 — Цена (EUR)\n"
        "<i>Например: 18500</i>",
        parse_mode="HTML",
    )
    return ASK_PRICE


async def ask_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "").replace(",", "").replace(".", "")
    if not text.isdigit():
        await update.message.reply_text("⚠️ Введите цену числом, например: 18500")
        return ASK_PRICE
    context.user_data["price"] = int(text)
    context.user_data["currency"] = "EUR"
    await update.message.reply_text(
        f"✅ Цена: <b>{context.user_data['price']:,} EUR</b>\n\n"
        "Шаг 6 из 10 — Тип топлива",
        parse_mode="HTML",
        reply_markup=fuel_keyboard(),
    )
    return ASK_FUEL


async def ask_transmission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["fuel"] = query.data.replace("fuel_", "")
    await query.edit_message_text(
        f"✅ Топливо: <b>{context.user_data['fuel']}</b>\n\n"
        "Шаг 7 из 10 — Коробка передач",
        parse_mode="HTML",
        reply_markup=transmission_keyboard(),
    )
    return ASK_TRANSMISSION


async def ask_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["transmission"] = query.data.replace("tr_", "")
    await query.edit_message_text(
        f"✅ КПП: <b>{context.user_data['transmission']}</b>\n\n"
        "Шаг 8 из 10 — Цвет\n"
        "<i>Например: Чёрный, Белый, Серебристый</i>",
        parse_mode="HTML",
    )
    return ASK_COLOR


async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["color"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Цвет: <b>{context.user_data['color']}</b>\n\n"
        "Шаг 9 из 10 — Город\n"
        "<i>Например: Amsterdam, Rotterdam, Utrecht</i>",
        parse_mode="HTML",
    )
    return ASK_CITY


async def ask_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Город: <b>{context.user_data['city']}</b>\n\n"
        "Шаг 10 из 10 — Описание\n"
        "Расскажите о состоянии, комплектации, особенностях.\n"
        "<i>Минимум 20 символов</i>",
        parse_mode="HTML",
    )
    return ASK_DESCRIPTION


async def ask_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if len(desc) < 20:
        await update.message.reply_text("⚠️ Описание слишком короткое. Напишите подробнее (минимум 20 символов).")
        return ASK_DESCRIPTION
    context.user_data["description"] = desc
    context.user_data["photos"] = []
    await update.message.reply_text(
        "📸 Загрузите фото автомобиля\n\n"
        "• Минимум 1, максимум 10 фотографий\n"
        "• Отправляйте по одному или сразу несколько\n"
        "• Когда закончите — нажмите кнопку",
        reply_markup=photos_keyboard(0),
    )
    return ASK_PHOTOS


async def collect_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data.setdefault("photos", [])
    if len(photos) >= 10:
        await update.message.reply_text(
            "⚠️ Максимум 10 фото. Нажмите кнопку ниже.",
            reply_markup=photos_keyboard(len(photos)),
        )
        return ASK_PHOTOS
    photos.append(update.message.photo[-1].file_id)
    await update.message.reply_text(
        f"📸 Добавлено {len(photos)}/10",
        reply_markup=photos_keyboard(len(photos)),
    )
    return ASK_PHOTOS


async def photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    photos = context.user_data.get("photos", [])
    if not photos:
        await query.edit_message_text(
            "⚠️ Добавьте хотя бы одно фото!",
            reply_markup=photos_keyboard(0),
        )
        return ASK_PHOTOS
    await query.edit_message_text(
        listing_preview(context.user_data, len(photos)),
        parse_mode="HTML",
        reply_markup=confirm_keyboard(),
    )
    return CONFIRM


async def improve_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✨ Улучшаю описание...")
    await query.edit_message_text("⏳ Генерирую улучшенное описание через Claude AI...")

    d = context.user_data
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": (
                    f"Напиши продающее описание для объявления о продаже автомобиля на русском языке.\n\n"
                    f"Авто: {d['make']} {d['model']} {d['year']} г.\n"
                    f"Пробег: {d['mileage']:,} км\n"
                    f"Топливо: {d['fuel']}\n"
                    f"КПП: {d['transmission']}\n"
                    f"Цвет: {d['color']}\n"
                    f"Город: {d['city']}\n"
                    f"Исходное описание продавца: {d['description']}\n\n"
                    "Напиши только текст описания (2–4 предложения). "
                    "Без заголовков, без цены, без контактов."
                ),
            }],
        )
        context.user_data["description"] = response.content[0].text.strip()
    except Exception as e:
        logger.error(f"AI error: {e}")

    await query.edit_message_text(
        listing_preview(context.user_data, len(d["photos"]), ai_improved=True),
        parse_mode="HTML",
        reply_markup=confirm_keyboard(ai_done=True),
    )
    return CONFIRM


async def publish_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Публикую объявление...")

    user = query.from_user
    d = context.user_data

    listing_id = await create_listing({
        "user_id": user.id,
        "username": user.username,
        "make": d["make"],
        "model": d["model"],
        "year": d["year"],
        "mileage": d["mileage"],
        "price": d["price"],
        "currency": d.get("currency", "EUR"),
        "fuel": d["fuel"],
        "transmission": d["transmission"],
        "color": d["color"],
        "city": d["city"],
        "description": d["description"],
        "photo_ids": d["photos"],
    })

    listing = await get_listing(listing_id)
    msg_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)

    expires = datetime.fromisoformat(listing["expires_at"]).strftime("%d.%m.%Y")
    await query.edit_message_text(
        f"✅ <b>Объявление #{listing_id} опубликовано!</b>\n\n"
        f"🚗 {d['make']} {d['model']} {d['year']}\n"
        f"📅 Активно до: {expires}\n\n"
        f"Продлить или удалить можно в «Мои объявления».",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ Подача объявления отменена.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ====== МОИ ОБЪЯВЛЕНИЯ ======

async def my_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        user_id = update.callback_query.from_user.id
        reply = update.callback_query.edit_message_text
    else:
        user_id = update.effective_user.id
        reply = update.message.reply_text

    items = await get_user_listings(user_id)

    if not items:
        await reply(
            "📋 У вас нет объявлений.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Подать объявление", callback_data="new_listing")],
                [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
            ]),
        )
        return

    now = datetime.now()
    text = "📋 <b>Ваши объявления:</b>\n\n"
    keyboard = []

    for l in items:
        expires = datetime.fromisoformat(l["expires_at"])
        days_left = (expires - now).days
        icon = "✅" if l["status"] == "active" and days_left >= 0 else "⏰"
        status_str = f"ещё {days_left} дн." if days_left >= 0 else "истекло"
        text += f"{icon} <b>#{l['id']}</b> — {l['make']} {l['model']} {l['year']} — {l['price']:,} EUR ({status_str})\n"
        keyboard.append([
            InlineKeyboardButton(f"🔄 Продлить #{l['id']}", callback_data=f"extend_{l['id']}"),
            InlineKeyboardButton(f"🗑 #{l['id']}", callback_data=f"del_{l['id']}"),
        ])

    keyboard.append([InlineKeyboardButton("🏠 Меню", callback_data="main_menu")])
    await reply(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def extend_listing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.split("_")[1])
    listing = await get_listing(listing_id)

    if not listing or listing["user_id"] != query.from_user.id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    await extend_listing(listing_id)
    listing = await get_listing(listing_id)

    old_ids = json.loads(listing.get("channel_message_ids") or "[]")
    await delete_from_channel(context, old_ids)
    msg_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)

    expires = datetime.fromisoformat(listing["expires_at"]).strftime("%d.%m.%Y")
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ Объявление #{listing_id} продлено до {expires} и переопубликовано в канал.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings")],
        ]),
    )


async def delete_listing_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.split("_")[1])
    listing = await get_listing(listing_id)

    if not listing or listing["user_id"] != query.from_user.id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    await query.edit_message_text(
        f"🗑 Удалить объявление <b>#{listing_id} — {listing['make']} {listing['model']} {listing['year']}</b>?\n\n"
        "Объявление будет снято с публикации.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_del_{listing_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="my_listings"),
            ]
        ]),
    )


async def delete_listing_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.split("_")[2])
    listing = await get_listing(listing_id)

    if not listing or listing["user_id"] != query.from_user.id:
        return

    old_ids = json.loads(listing.get("channel_message_ids") or "[]")
    await delete_from_channel(context, old_ids)
    await delete_listing(listing_id)

    await query.edit_message_text(
        f"✅ Объявление #{listing_id} удалено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings")],
            [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
        ]),
    )


# ====== ПОИСК ======

async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["search"] = {}
    await query.edit_message_text(
        "🔍 <b>Поиск автомобиля</b>\n\n"
        "Шаг 1 — Марка\n"
        "<i>Введите марку или «любая» для пропуска</i>",
        parse_mode="HTML",
    )
    return SEARCH_MAKE


async def search_got_make(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() not in ("любая", "любой", "-", "all", "any"):
        context.user_data["search"]["make"] = text
    await update.message.reply_text(
        "Шаг 2 — Модель\n<i>Введите модель или «любая»</i>",
        parse_mode="HTML",
    )
    return SEARCH_MODEL


async def search_got_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() not in ("любая", "любой", "-", "all", "any"):
        context.user_data["search"]["model"] = text
    await update.message.reply_text(
        "Шаг 3 — Максимальная цена (EUR)\n<i>Введите число или «любая»</i>",
        parse_mode="HTML",
    )
    return SEARCH_PRICE_MAX


async def search_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "").replace(",", "")
    if text.isdigit():
        context.user_data["search"]["price_max"] = int(text)

    s = context.user_data.get("search", {})
    results = await search_listings(
        make=s.get("make"),
        model=s.get("model"),
        price_max=s.get("price_max"),
    )

    if not results:
        await update.message.reply_text(
            "😔 По вашему запросу ничего не найдено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Новый поиск", callback_data="search")],
                [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
            ]),
        )
        return ConversationHandler.END

    await update.message.reply_text(f"🔍 Найдено: <b>{len(results)}</b>", parse_mode="HTML")

    for l in results[:10]:
        contact = f"@{l['username']}" if l.get("username") else "—"
        text = (
            f"🚗 <b>{l['make']} {l['model']} {l['year']}</b>\n"
            f"💶 <b>{l['price']:,} EUR</b>  |  🛣 {l['mileage']:,} км\n"
            f"⛽ {l['fuel']}  |  ⚙️ {l['transmission']}\n"
            f"🎨 {l['color']}  |  📍 {l['city']}\n"
            f"👤 {contact}  |  🆔 #авто{l['id']}"
        )
        photos = json.loads(l["photo_ids"])
        try:
            await update.message.reply_photo(photo=photos[0], caption=text, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text, parse_mode="HTML")

    await update.message.reply_text(
        "Хотите выполнить новый поиск?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Новый поиск", callback_data="search")],
            [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
        ]),
    )
    return ConversationHandler.END


async def search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Поиск отменён.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ====== ЗАПУСК ======

async def post_init(app: Application):
    await init_db()
    logger.info("База данных инициализирована.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    listing_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_listing_start, pattern="^new_listing$")],
        states={
            ASK_MAKE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_model)],
            ASK_MODEL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_year)],
            ASK_YEAR:         [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_mileage)],
            ASK_MILEAGE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_price)],
            ASK_PRICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fuel)],
            ASK_FUEL:         [CallbackQueryHandler(ask_transmission, pattern="^fuel_")],
            ASK_TRANSMISSION: [CallbackQueryHandler(ask_color, pattern="^tr_")],
            ASK_COLOR:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_city)],
            ASK_CITY:         [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_description)],
            ASK_DESCRIPTION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_photos)],
            ASK_PHOTOS: [
                MessageHandler(filters.PHOTO, collect_photo),
                CallbackQueryHandler(photos_done, pattern="^photos_done$"),
            ],
            CONFIRM: [
                CallbackQueryHandler(improve_with_ai, pattern="^improve_ai$"),
                CallbackQueryHandler(publish_listing, pattern="^publish$"),
                CallbackQueryHandler(cancel_listing, pattern="^cancel_listing$"),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )

    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_start, pattern="^search$")],
        states={
            SEARCH_MAKE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_make)],
            SEARCH_MODEL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_model)],
            SEARCH_PRICE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_execute)],
        },
        fallbacks=[CommandHandler("cancel", search_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mylistings", my_listings))
    app.add_handler(listing_conv)
    app.add_handler(search_conv)
    app.add_handler(CallbackQueryHandler(my_listings, pattern="^my_listings$"))
    app.add_handler(CallbackQueryHandler(extend_listing_handler, pattern=r"^extend_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_ask, pattern=r"^del_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_confirm, pattern=r"^confirm_del_\d+$"))
    app.add_handler(CallbackQueryHandler(go_main_menu, pattern="^main_menu$"))

    logger.info("✅ AutoBazar Bot запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
