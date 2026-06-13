import json
import logging
import os
import sys
from datetime import datetime, time as dt_time
from urllib.parse import quote

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
    get_expired_listings,
    get_listing,
    get_user_listings,
    init_db,
    search_listings,
    update_listing_channel_msgs,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_missing = [v for v in ("TELEGRAM_TOKEN", "ANTHROPIC_API_KEY", "CHANNEL_ID") if not os.getenv(v)]
if _missing:
    logger.critical(f"Отсутствуют переменные окружения: {', '.join(_missing)}")
    sys.exit(1)

# ====== СОСТОЯНИЯ ДИАЛОГА: подача объявления ======
(
    ASK_MAKE, ASK_MODEL, ASK_YEAR, ASK_MILEAGE, ASK_PRICE,
    ASK_FUEL, ASK_TRANSMISSION, ASK_ENGINE, ASK_TURBO, ASK_CITY, ASK_PHONE,
    ASK_DESCRIPTION, ASK_PHOTOS, CONFIRM,
) = range(14)

# ====== СОСТОЯНИЯ ДИАЛОГА: поиск ======
SEARCH_MAKE, SEARCH_MODEL, SEARCH_PRICE_MAX = range(20, 23)

FUEL_TYPES = ["Бензин", "Дизель", "Электро", "Газ/Бензин"]
TRANSMISSION_TYPES = ["Автомат", "Механика"]

TURBO_LABELS = {
    "Toyota":      {"Бензин": "Turbo",          "Дизель": "D-4D Turbo",    "Газ/Бензин": "Turbo"},
    "BMW":         {"Бензин": "TwinPower Turbo", "Дизель": "TwinPower Turbo","Газ/Бензин": "TwinPower Turbo"},
    "Mercedes":    {"Бензин": "AMG Turbo",       "Дизель": "CDI",           "Газ/Бензин": "AMG Turbo"},
    "Volkswagen":  {"Бензин": "TSI",             "Дизель": "TDI",           "Газ/Бензин": "TSI"},
    "Audi":        {"Бензин": "TFSI",            "Дизель": "TDI",           "Газ/Бензин": "TFSI"},
    "Ford":        {"Бензин": "EcoBoost",        "Дизель": "EcoBlue",       "Газ/Бензин": "EcoBoost"},
    "Škoda":       {"Бензин": "TSI",             "Дизель": "TDI",           "Газ/Бензин": "TSI"},
    "Opel":        {"Бензин": "Turbo",           "Дизель": "CDTI",          "Газ/Бензин": "Turbo"},
    "Hyundai":     {"Бензин": "T-GDi",           "Дизель": "CRDi",          "Газ/Бензин": "T-GDi"},
    "Kia":         {"Бензин": "T-GDi",           "Дизель": "CRDi",          "Газ/Бензин": "T-GDi"},
    "Volvo":       {"Бензин": "T-series",        "Дизель": "D-series",      "Газ/Бензин": "T-series"},
    "Renault":     {"Бензин": "TCe",             "Дизель": "dCi",           "Газ/Бензин": "TCe"},
    "Peugeot":     {"Бензин": "THP",             "Дизель": "HDi",           "Газ/Бензин": "THP"},
    "Honda":       {"Бензин": "VTEC Turbo",      "Дизель": "i-DTEC",        "Газ/Бензин": "VTEC Turbo"},
    "Nissan":      {"Бензин": "DIG-T",           "Дизель": "dCi",           "Газ/Бензин": "DIG-T"},
    "Mazda":       {"Бензин": "Skyactiv-T",      "Дизель": "Skyactiv-D",    "Газ/Бензин": "Skyactiv-T"},
}

POPULAR_MAKES = [
    "Toyota", "BMW", "Mercedes", "Volkswagen",
    "Audi", "Ford", "Škoda", "Opel",
    "Hyundai", "Kia", "Volvo", "Renault",
    "Peugeot", "Honda", "Nissan", "Mazda",
]
NL_CITIES = [
    "Amsterdam", "Rotterdam", "Utrecht", "Den Haag",
    "Eindhoven", "Tilburg", "Groningen", "Almere",
    "Breda", "Nijmegen", "Arnhem", "Haarlem",
    "Zaandam", "Amersfoort", "Apeldoorn", "Hoofddorp",
]
PRICE_PRESETS = [5000, 10000, 15000, 20000, 30000, 50000]


# ====== КЛАВИАТУРЫ ======

# === НОВОЕ: make_keyboard ===
def make_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(POPULAR_MAKES), 2):
        pair = POPULAR_MAKES[i:i + 2]
        rows.append([
            InlineKeyboardButton(brand, callback_data=f"make_{brand}")
            for brand in pair
        ])
    rows.append([InlineKeyboardButton("✏️ Другая марка", callback_data="make_other")])
    return InlineKeyboardMarkup(rows)


# === НОВОЕ: year_keyboard ===
def year_keyboard() -> InlineKeyboardMarkup:
    current_year = datetime.now().year
    years = list(range(current_year, 2009, -1))
    rows = []
    for i in range(0, len(years), 3):
        chunk = years[i:i + 3]
        rows.append([
            InlineKeyboardButton(str(y), callback_data=f"year_{y}")
            for y in chunk
        ])
    rows.append([InlineKeyboardButton("✏️ Другой год", callback_data="year_other")])
    return InlineKeyboardMarkup(rows)


# === НОВОЕ: city_keyboard ===
ENGINE_VOLUMES = ["1.0", "1.2", "1.4", "1.6", "1.8", "2.0", "2.5", "3.0", "3.5", "4.0"]


def engine_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(ENGINE_VOLUMES), 3):
        chunk = ENGINE_VOLUMES[i:i + 3]
        rows.append([InlineKeyboardButton(f"{v}L", callback_data=f"eng_{v}") for v in chunk])
    rows.append([InlineKeyboardButton("✏️ Другой объём", callback_data="eng_other")])
    return InlineKeyboardMarkup(rows)


def city_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(NL_CITIES), 2):
        pair = NL_CITIES[i:i + 2]
        rows.append([
            InlineKeyboardButton(city, callback_data=f"city_{city}")
            for city in pair
        ])
    rows.append([InlineKeyboardButton("✏️ Другой город", callback_data="city_other")])
    return InlineKeyboardMarkup(rows)


def search_make_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(POPULAR_MAKES), 2):
        pair = POPULAR_MAKES[i:i + 2]
        rows.append([InlineKeyboardButton(b, callback_data=f"smake_{b}") for b in pair])
    rows.append([InlineKeyboardButton("🔍 Любая марка", callback_data="smake_any")])
    rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data="smake_other")])
    return InlineKeyboardMarkup(rows)


def search_skip_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data=f"sskip_{field}")]])


def search_price_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(PRICE_PRESETS), 2):
        pair = PRICE_PRESETS[i:i + 2]
        rows.append([
            InlineKeyboardButton(f"< {p:,} €".replace(",", " "), callback_data=f"sprice_{p}")
            for p in pair
        ])
    rows.append([InlineKeyboardButton("💶 Любая цена", callback_data="sprice_any")])
    rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data="sprice_other")])
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Разместить объявление", callback_data="new_listing")],
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
    rows = [[InlineKeyboardButton(t, callback_data=f"tr_{t}")] for t in TRANSMISSION_TYPES]
    rows.append([InlineKeyboardButton("✏️ Другая", callback_data="tr_other")])
    return InlineKeyboardMarkup(rows)


def turbo_keyboard(make: str, fuel: str) -> InlineKeyboardMarkup:
    turbo_label = TURBO_LABELS.get(make, {}).get(fuel, "Turbo")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ Турбо ({turbo_label})", callback_data=f"turbo_{turbo_label}")],
        [InlineKeyboardButton("🌀 Атмосферный", callback_data="turbo_no")],
    ])


def photos_keyboard(count: int):
    label = f"✅ Готово ({count} фото)" if count else "✅ Фото готовы"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="photos_done")]])


# ====== ВАЛИДАЦИЯ ======

async def _validate_car(make: str, model: str) -> bool:
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content":
                f"Is '{model}' a plausible car model name for brand '{make}'? "
                f"The name may be in Russian, transliterated, abbreviated, or contain typos — be lenient. "
                f"Answer NO only if it is obviously not a car model (e.g. a random word, food, animal). "
                f"If unsure, answer YES. Answer only YES or NO."}],
        )
        return resp.content[0].text.strip().upper().startswith("Y")
    except Exception:
        return True


async def _validate_photo_is_car(bot, file_id: str) -> bool:
    import base64
    try:
        tg_file = await bot.get_file(file_id)
        img_bytes = bytes(await tg_file.download_as_bytearray())
        img_b64 = base64.b64encode(img_bytes).decode()
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": "Is a car (automobile) visible in this image? Answer only YES or NO."},
            ]}],
        )
        return resp.content[0].text.strip().upper().startswith("Y")
    except Exception:
        return True


# ====== ФОРМАТИРОВАНИЕ ОБЪЯВЛЕНИЯ ======

def _contact(listing: dict) -> str:
    if listing.get("username"):
        return f"@{listing['username']}"
    return listing.get("phone") or "—"


def listing_caption(listing: dict) -> str:
    engine_parts = []
    if listing.get("engine"):
        engine_parts.append(listing["engine"])
    if listing.get("turbo") and listing["turbo"] != "Атмосферный":
        engine_parts.append(listing["turbo"])
    engine_str = " ".join(engine_parts)

    lines = [
        f"🚘 <b>{listing['make']} {listing['model']} · {listing['year']} г.</b>",
        "",
        f"💶 <b>{listing['price']:,} €</b>",
        "",
        f"📍 <a href=\"https://maps.google.com/maps?q={quote(listing['city'] + ', Netherlands')}\">{listing['city']}</a>",
        f"🛣 Пробег: {listing['mileage']:,} км",
        f"⛽ Топливо: {listing['fuel']}",
        f"⚙️ КПП: {listing['transmission']}",
    ]
    if engine_str:
        lines.append(f"🔧 Двигатель: {engine_str}")

    lines += [
        "",
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        "",
        f"{listing['description']}",
        "",
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        "",
        f"📲 Контакт: {_contact(listing)}",
    ]
    return "\n".join(lines)


def listing_preview(d: dict, photo_count: int, ai_improved: bool = False) -> str:
    tag = " <i>(AI улучшено)</i>" if ai_improved else ""
    contact = f"@{d['username']}" if d.get("username") else d.get("phone") or "—"
    return (
        f"📋 <b>Предпросмотр объявления</b>{tag}\n\n"
        f"🚗 {d['make']} {d['model']} {d['year']}\n"
        f"💶 {d['price']:,} EUR\n"
        f"📍 {d['city']}\n"
        f"🛣 {d['mileage']:,} км\n"
        f"⛽ {d['fuel']}  |  ⚙️ {d['transmission']}"
        + (f"  |  🔧 {d.get('engine', '')} {d.get('turbo', '')}".rstrip() if d.get('engine') else "")
        + "\n"
        f"📸 Фото: {photo_count}\n\n"
        f"📞 Контакт: {contact}\n\n"
        f"📝 {d['description']}"
    )


def confirm_keyboard(ai_done: bool = False):
    rows = []
    if not ai_done:
        rows.append([InlineKeyboardButton("✨ Улучшить описание AI", callback_data="improve_ai")])
    else:
        rows.append([InlineKeyboardButton("↩️ Вернуть оригинал", callback_data="revert_ai")])
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
        "Добро пожаловать в <b>AutoBazar NL</b> — маркетплейс автомобилей.\n\n"
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

# === ИЗМЕНЕНО: new_listing_start ===
async def new_listing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["photos"] = []
    context.user_data["username"] = query.from_user.username
    await query.edit_message_text(
        "📝 <b>Подача объявления</b>\n\n"
        "Шаг 1 из 10 — Марка автомобиля",
        parse_mode="HTML",
        reply_markup=make_keyboard(),
    )
    return ASK_MAKE


# === НОВОЕ: got_make_button ===
async def got_make_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "make_other":
        await query.message.reply_text("✏️ Введите марку:")
        return ASK_MAKE
    brand = query.data.replace("make_", "", 1)
    context.user_data["make"] = brand
    await query.message.reply_text(
        f"✅ Марка: <b>{brand}</b>\n\n"
        "Шаг 2 из 10 — Модель\n"
        "<i>Например: Camry, X5, Golf</i>",
        parse_mode="HTML",
    )
    return ASK_MODEL


async def ask_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["make"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Марка: <b>{context.user_data['make']}</b>\n\n"
        "Шаг 2 из 10 — Модель\n"
        "<i>Например: Camry, X5, Golf</i>",
        parse_mode="HTML",
    )
    return ASK_MODEL


# === ИЗМЕНЕНО: ask_year ===
async def ask_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    make = context.user_data.get("make", "")
    msg = await update.message.reply_text("⏳ Проверяю марку и модель...")
    valid = await _validate_car(make, model)
    if not valid:
        await msg.edit_text(
            f"⚠️ Не могу найти автомобиль <b>{make} {model}</b>.\n"
            "Проверьте написание и попробуйте снова.",
            parse_mode="HTML",
        )
        return ASK_MODEL
    await msg.edit_text(
        f"✅ Модель: <b>{model}</b>\n\nШаг 3 из 13 — Год выпуска",
        parse_mode="HTML",
        reply_markup=year_keyboard(),
    )
    context.user_data["model"] = model
    return ASK_YEAR


# === НОВОЕ: got_year_button ===
async def got_year_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "year_other":
        await query.message.reply_text("✏️ Введите год:")
        return ASK_YEAR
    year = int(query.data.replace("year_", "", 1))
    context.user_data["year"] = year
    await query.message.reply_text(
        f"✅ Год: <b>{year}</b>\n\n"
        "Шаг 4 из 10 — Пробег (км)\n"
        "<i>Например: 85000</i>",
        parse_mode="HTML",
    )
    return ASK_MILEAGE


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


async def ask_engine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "tr_other":
        await query.message.reply_text("✏️ Введите тип коробки передач:")
        return ASK_TRANSMISSION
    context.user_data["transmission"] = query.data.replace("tr_", "")
    if context.user_data.get("fuel") == "Электро":
        context.user_data["engine"] = ""
        context.user_data["turbo"] = ""
        await query.edit_message_text(
            f"✅ КПП: <b>{context.user_data['transmission']}</b>\n\nШаг 9 из 13 — Город",
            parse_mode="HTML",
            reply_markup=city_keyboard(),
        )
        return ASK_CITY
    await query.edit_message_text(
        f"✅ КПП: <b>{context.user_data['transmission']}</b>\n\nШаг 8 из 13 — Объём двигателя",
        parse_mode="HTML",
        reply_markup=engine_keyboard(),
    )
    return ASK_ENGINE


async def ask_engine_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["transmission"] = update.message.text.strip()
    if context.user_data.get("fuel") == "Электро":
        context.user_data["engine"] = ""
        context.user_data["turbo"] = ""
        await update.message.reply_text(
            f"✅ КПП: <b>{context.user_data['transmission']}</b>\n\nШаг 9 из 13 — Город",
            parse_mode="HTML",
            reply_markup=city_keyboard(),
        )
        return ASK_CITY
    await update.message.reply_text(
        f"✅ КПП: <b>{context.user_data['transmission']}</b>\n\nШаг 8 из 13 — Объём двигателя",
        parse_mode="HTML",
        reply_markup=engine_keyboard(),
    )
    return ASK_ENGINE


async def got_engine_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "eng_other":
        await query.message.reply_text("✏️ Введите объём (например: 1.8):")
        return ASK_ENGINE
    engine = query.data.replace("eng_", "") + "L"
    context.user_data["engine"] = engine
    make = context.user_data.get("make", "")
    fuel = context.user_data.get("fuel", "")
    await query.message.reply_text(
        f"✅ Двигатель: <b>{engine}</b>\n\nШаг 9 из 13 — Тип двигателя",
        parse_mode="HTML",
        reply_markup=turbo_keyboard(make, fuel),
    )
    return ASK_TURBO


async def ask_engine_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["engine"] = text
    make = context.user_data.get("make", "")
    fuel = context.user_data.get("fuel", "")
    await update.message.reply_text(
        f"✅ Двигатель: <b>{text}</b>\n\nШаг 9 из 13 — Тип двигателя",
        parse_mode="HTML",
        reply_markup=turbo_keyboard(make, fuel),
    )
    return ASK_TURBO


async def got_turbo_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "turbo_no":
        context.user_data["turbo"] = "Атмосферный"
    else:
        context.user_data["turbo"] = query.data.replace("turbo_", "")
    await query.message.reply_text(
        f"✅ Тип: <b>{context.user_data['turbo']}</b>\n\nШаг 10 из 13 — Город",
        parse_mode="HTML",
        reply_markup=city_keyboard(),
    )
    return ASK_CITY


# === ИЗМЕНЕНО: ask_city ===
async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["transmission"] = query.data.replace("tr_", "")
    await query.edit_message_text(
        f"✅ КПП: <b>{context.user_data['transmission']}</b>\n\n"
        "Шаг 8 из 12 — Город",
        parse_mode="HTML",
        reply_markup=city_keyboard(),
    )
    return ASK_CITY


# === НОВОЕ: got_city_button ===
async def got_city_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "city_other":
        await query.message.reply_text("✏️ Введите город:")
        return ASK_CITY
    city = query.data.replace("city_", "", 1)
    context.user_data["city"] = city
    await query.message.reply_text(
        f"✅ Город: <b>{city}</b>\n\n"
        "Шаг 9 из 10 — Номер телефона\n"
        "<i>Например: +31612345678</i>\n"
        "<i>Нужен для связи, если ваш аккаунт приватный</i>",
        parse_mode="HTML",
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Город: <b>{context.user_data['city']}</b>\n\n"
        "Шаг 9 из 10 — Номер телефона\n"
        "<i>Например: +31612345678</i>\n"
        "<i>Нужен для связи, если ваш аккаунт приватный</i>",
        parse_mode="HTML",
    )
    return ASK_PHONE


async def ask_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Телефон: <b>{context.user_data['phone']}</b>\n\n"
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


async def _send_photo_ack(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_data = context.application.user_data.get(data["user_id"], {})
    count = len(user_data.get("photos", []))
    rejected = user_data.get("rejected_photos", 0)
    user_data["rejected_photos"] = 0
    text = f"📸 Добавлено: {count}/10"
    if rejected:
        text += f"\n⚠️ Отклонено: {rejected} фото — авто не распознано"
    await context.bot.send_message(
        chat_id=data["chat_id"],
        text=text,
        reply_markup=photos_keyboard(count),
    )


async def collect_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data.setdefault("photos", [])
    if len(photos) >= 10:
        await update.message.reply_text(
            "⚠️ Максимум 10 фото. Нажмите кнопку ниже.",
            reply_markup=photos_keyboard(len(photos)),
        )
        return ASK_PHOTOS
    file_id = update.message.photo[-1].file_id
    is_car = await _validate_photo_is_car(context.bot, file_id)
    if is_car:
        photos.append(file_id)
    else:
        context.user_data["rejected_photos"] = context.user_data.get("rejected_photos", 0) + 1
    # Always debounce — one summary after all photos processed
    for job in context.job_queue.get_jobs_by_name(f"photo_ack_{update.effective_user.id}"):
        job.schedule_removal()
    context.job_queue.run_once(
        _send_photo_ack,
        when=2.0,
        data={"chat_id": update.effective_chat.id, "user_id": update.effective_user.id},
        name=f"photo_ack_{update.effective_user.id}",
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
    d["original_description"] = d.get("description", "")
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
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


async def revert_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["description"] = context.user_data.get("original_description", context.user_data["description"])
    await query.edit_message_text(
        listing_preview(context.user_data, len(context.user_data["photos"])),
        parse_mode="HTML",
        reply_markup=confirm_keyboard(ai_done=False),
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
        "phone": d.get("phone", ""),
        "make": d["make"],
        "model": d["model"],
        "year": d["year"],
        "mileage": d["mileage"],
        "price": d["price"],
        "currency": d.get("currency", "EUR"),
        "fuel": d["fuel"],
        "transmission": d["transmission"],
        "engine": d.get("engine", ""),
        "turbo": d.get("turbo", ""),
        "city": d["city"],
        "description": d["description"],
        "photo_ids": d["photos"],
    })

    listing = await get_listing(listing_id)
    msg_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)

    expires = datetime.fromisoformat(listing["expires_at"]).strftime("%d.%m.%Y")
    await query.edit_message_text(
        f"✅ <b>Объявление опубликовано!</b>\n\n"
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
                [InlineKeyboardButton("🚗 Разместить объявление", callback_data="new_listing")],
                [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
            ]),
        )
        return

    now = datetime.now()
    text = "📋 <b>Ваши объявления:</b>\n\n"
    keyboard = []

    for item in items:
        expires = datetime.fromisoformat(item["expires_at"])
        days_left = (expires - now).days
        icon = "✅" if item["status"] == "active" and days_left >= 0 else "⏰"
        status_str = f"ещё {days_left} дн." if days_left >= 0 else "истекло"
        text += f"{icon} <b>#{item['id']}</b> — {item['make']} {item['model']} {item['year']} — {item['price']:,} EUR ({status_str})\n"
        keyboard.append([
            InlineKeyboardButton(f"🔄 Продлить #{item['id']}", callback_data=f"extend_{item['id']}"),
            InlineKeyboardButton(f"🗑 #{item['id']}", callback_data=f"del_{item['id']}"),
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
        "🔍 <b>Поиск автомобиля</b>\n\nШаг 1 — Марка",
        parse_mode="HTML",
        reply_markup=search_make_keyboard(),
    )
    return SEARCH_MAKE


async def search_make_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "smake_any":
        context.user_data["search"] = {}
    elif data == "smake_other":
        await query.edit_message_text("✏️ Введите марку:")
        return SEARCH_MAKE
    else:
        context.user_data["search"]["make"] = data.removeprefix("smake_")
    brand = context.user_data["search"].get("make", "")
    prefix = f"✅ Марка: <b>{brand}</b>\n\n" if brand else ""
    await query.edit_message_text(
        f"{prefix}Шаг 2 — Модель\n<i>Или пропустите</i>",
        parse_mode="HTML",
        reply_markup=search_skip_keyboard("model"),
    )
    return SEARCH_MODEL


async def search_got_make(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() not in ("любая", "любой", "-", "all", "any"):
        context.user_data["search"]["make"] = text
    await update.message.reply_text(
        "Шаг 2 — Модель\n<i>Или пропустите</i>",
        parse_mode="HTML",
        reply_markup=search_skip_keyboard("model"),
    )
    return SEARCH_MODEL


async def search_skip_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "sskip_model":
        await query.edit_message_text(
            "Шаг 3 — Максимальная цена (EUR)",
            parse_mode="HTML",
            reply_markup=search_price_keyboard(),
        )
        return SEARCH_PRICE_MAX
    return ConversationHandler.END


async def search_got_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() not in ("любая", "любой", "-", "all", "any"):
        context.user_data["search"]["model"] = text
    await update.message.reply_text(
        "Шаг 3 — Максимальная цена (EUR)",
        parse_mode="HTML",
        reply_markup=search_price_keyboard(),
    )
    return SEARCH_PRICE_MAX


async def search_price_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "sprice_other":
        await query.edit_message_text("✏️ Введите максимальную цену (EUR):")
        return SEARCH_PRICE_MAX
    if data != "sprice_any":
        context.user_data["search"]["price_max"] = int(data.removeprefix("sprice_"))
    await query.edit_message_text("⏳ Ищу...")
    return await _run_search(update, context)


async def search_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "").replace(",", "")
    if text.isdigit():
        context.user_data["search"]["price_max"] = int(text)
    return await _run_search(update, context)


async def _run_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        reply = update.callback_query.message.reply_text
        reply_photo = update.callback_query.message.reply_photo
    else:
        reply = update.message.reply_text
        reply_photo = update.message.reply_photo

    s = context.user_data.get("search", {})
    results = await search_listings(
        make=s.get("make"), model=s.get("model"), price_max=s.get("price_max"),
    )
    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Новый поиск", callback_data="search")],
        [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
    ])
    if not results:
        await reply("😔 По вашему запросу ничего не найдено.", reply_markup=nav)
        return ConversationHandler.END

    await reply(f"🔍 Найдено: <b>{len(results)}</b>", parse_mode="HTML")
    for item in results[:10]:
        text = (
            f"🚗 <b>{item['make']} {item['model']} {item['year']}</b>\n"
            f"💶 <b>{item['price']:,} EUR</b>  |  🛣 {item['mileage']:,} км\n"
            f"⛽ {item['fuel']}  |  ⚙️ {item['transmission']}\n"
            f"📍 {item['city']}\n"
            f"📞 {_contact(item)}"
        )
        photos = json.loads(item["photo_ids"])
        try:
            await reply_photo(photo=photos[0], caption=text, parse_mode="HTML")
        except Exception:
            await reply(text, parse_mode="HTML")

    await reply("Хотите выполнить новый поиск?", reply_markup=nav)
    return ConversationHandler.END


async def search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Поиск отменён.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ====== AI FALLBACK ======

async def ai_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"Пользователь Telegram-бота написал: «{user_text}»\n\n"
                    "Определи намерение и ответь ТОЛЬКО одним словом из списка:\n"
                    "MENU — хочет главное меню / отменить / вернуться назад\n"
                    "LISTINGS — хочет свои объявления\n"
                    "SEARCH — хочет поиск\n"
                    "OTHER — другое (тогда добавь через пробел короткий ответ на русском, до 100 символов)"
                ),
            }],
        )
        result = response.content[0].text.strip()
    except Exception:
        result = "OTHER Не понял запрос. Используйте меню ниже."

    if result.startswith("MENU"):
        context.user_data.clear()
        await update.message.reply_text("🏠 Главное меню:", reply_markup=main_menu_keyboard())
    elif result.startswith("LISTINGS"):
        await my_listings(update, context)
    elif result.startswith("SEARCH"):
        await update.message.reply_text(
            "🔍 Поиск:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Начать поиск", callback_data="search")]]),
        )
    else:
        reply_text = result.replace("OTHER", "").strip() or "Используйте меню ниже."
        await update.message.reply_text(reply_text, reply_markup=main_menu_keyboard())


# ====== АДМИНИСТРАТИВНЫЕ КОМАНДЫ ======

async def post_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("❌ Только администраторы канала могут это делать.")
            return
    except Exception:
        pass

    text = (
        "🚗 <b>AutoBazar NL — авто в Нидерландах!</b>\n\n"
        "Покупайте и продавайте автомобили без посредников.\n\n"
        "✅ Бесплатные объявления\n"
        "📸 До 10 фото на объявление\n"
        "🤖 AI улучшение описания\n"
        "⏱ Подача за 2 минуты\n\n"
        "👇 Нажмите кнопку чтобы подать объявление:"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📝 Подать объявление", url="https://t.me/autobazar_nl_bot")
    ]])
    msg = await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    try:
        await context.bot.pin_chat_message(
            chat_id=CHANNEL_ID,
            message_id=msg.message_id,
            disable_notification=True,
        )
    except Exception as e:
        logger.warning(f"Не удалось закрепить пост: {e}")
    await update.message.reply_text("✅ Приветственный пост опубликован и закреплён в канале!")


# ====== ФОНОВЫЕ ЗАДАЧИ ======

async def daily_repost(context: ContextTypes.DEFAULT_TYPE):
    listings = await search_listings()
    count = 0
    for listing in listings:
        old_ids = json.loads(listing.get("channel_message_ids") or "[]")
        await delete_from_channel(context, old_ids)
        msg_ids = await post_to_channel(context, listing)
        if msg_ids:
            await update_listing_channel_msgs(listing["id"], msg_ids)
            count += 1
    logger.info(f"Ежедневный репост: {count} объявлений переопубликовано")


async def cleanup_expired(context: ContextTypes.DEFAULT_TYPE):
    expired = await get_expired_listings()
    for listing in expired:
        old_ids = json.loads(listing.get("channel_message_ids") or "[]")
        await delete_from_channel(context, old_ids)
        await delete_listing(listing["id"])
    if expired:
        logger.info(f"Очистка: {len(expired)} истёкших объявлений удалено")


# ====== ЗАПУСК ======

async def post_init(app: Application):
    await init_db()
    # 09:00 UTC = 11:00 Amsterdam (летнее время)
    app.job_queue.run_daily(daily_repost, time=dt_time(9, 0, 0))
    # Очистка истёкших в 08:00 UTC
    app.job_queue.run_daily(cleanup_expired, time=dt_time(8, 0, 0))
    logger.info("База данных инициализирована. Фоновые задачи запущены.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    listing_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_listing_start, pattern="^new_listing$")],
        # === ИЗМЕНЕНО: states listing_conv ===
        states={
            ASK_MAKE: [
                CallbackQueryHandler(got_make_button, pattern="^make_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_model),
            ],
            ASK_MODEL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_year)],
            ASK_YEAR: [
                CallbackQueryHandler(got_year_button, pattern="^year_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_mileage),
            ],
            ASK_MILEAGE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_price)],
            ASK_PRICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fuel)],
            ASK_FUEL:         [CallbackQueryHandler(ask_transmission, pattern="^fuel_")],
            ASK_TRANSMISSION: [
                CallbackQueryHandler(ask_engine, pattern="^tr_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_engine_from_text),
            ],
            ASK_ENGINE: [
                CallbackQueryHandler(got_engine_button, pattern="^eng_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_engine_text),
            ],
            ASK_TURBO: [
                CallbackQueryHandler(got_turbo_button, pattern="^turbo_"),
            ],
            ASK_CITY: [
                CallbackQueryHandler(got_city_button, pattern="^city_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone),
            ],
            ASK_PHONE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_description)],
            ASK_DESCRIPTION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_photos)],
            ASK_PHOTOS: [
                MessageHandler(filters.PHOTO, collect_photo),
                CallbackQueryHandler(photos_done, pattern="^photos_done$"),
            ],
            CONFIRM: [
                CallbackQueryHandler(improve_with_ai, pattern="^improve_ai$"),
                CallbackQueryHandler(revert_ai, pattern="^revert_ai$"),
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
            SEARCH_MAKE: [
                CallbackQueryHandler(search_make_btn, pattern="^smake_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_make),
            ],
            SEARCH_MODEL: [
                CallbackQueryHandler(search_skip_btn, pattern="^sskip_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_model),
            ],
            SEARCH_PRICE_MAX: [
                CallbackQueryHandler(search_price_btn, pattern="^sprice_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_execute),
            ],
        },
        fallbacks=[CommandHandler("cancel", search_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mylistings", my_listings))
    app.add_handler(CommandHandler("welcome", post_welcome))
    app.add_handler(listing_conv)
    app.add_handler(search_conv)
    app.add_handler(CallbackQueryHandler(my_listings, pattern="^my_listings$"))
    app.add_handler(CallbackQueryHandler(extend_listing_handler, pattern=r"^extend_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_ask, pattern=r"^del_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_confirm, pattern=r"^confirm_del_\d+$"))
    app.add_handler(CallbackQueryHandler(go_main_menu, pattern="^main_menu$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_fallback))

    logger.info("✅ AutoBazar Bot запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
