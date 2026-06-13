import asyncio
import io
import json
import logging
import os
import sys
from datetime import datetime, time as dt_time
from urllib.parse import quote

import anthropic
from dotenv import load_dotenv
from aiohttp import web as aio_web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update, WebAppInfo
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
    get_listings_expiring_soon,
    get_user_listings,
    hide_listing,
    init_db,
    mark_warning_sent,
    search_listings,
    unhide_listing,
    update_listing_channel_msgs,
    update_listing_field,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").rstrip("/")
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")

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

# ====== СОСТОЯНИЯ ДИАЛОГА: редактирование ======
EDIT_CHOOSE_FIELD, EDIT_PRICE, EDIT_DESCRIPTION, EDIT_AI_CONFIRM, EDIT_DESC_CONFIRM = range(30, 35)

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
    if WEBAPP_URL:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🚗 Разместить объявление",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp?tab=publish"),
            )],
            [
                InlineKeyboardButton(
                    "🔍 Поиск авто",
                    web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp?tab=search"),
                ),
                InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings"),
            ],
        ])
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


# ====== ВАТЕРМАРК ======

def _add_watermark(img_bytes: bytes) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    text = "@autobazar_nederland"
    font_size = max(14, w // 40)
    font = None
    for fp in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            pass
    if font is None:
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = 14, h - th - 14

    # Subtle dark outline instead of a box
    for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0)]:
        draw.text((x + dx, y + dy), text, fill=(0, 0, 0, 110), font=font)
    draw.text((x, y), text, fill=(255, 255, 255, 190), font=font)

    result = Image.alpha_composite(img, overlay).convert("RGB")
    out = io.BytesIO()
    result.save(out, format="JPEG", quality=90)
    return out.getvalue()


async def _watermark_photo(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    img_bytes = bytes(await tg_file.download_as_bytearray())
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _add_watermark, img_bytes)
    except Exception as e:
        logger.warning(f"Watermark failed: {e}")
        return img_bytes


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
        watermarked = await asyncio.gather(*[_watermark_photo(context.bot, pid) for pid in photos])
        if len(watermarked) == 1:
            msg = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=io.BytesIO(watermarked[0]),
                caption=caption,
                parse_mode="HTML",
            )
            return [msg.message_id]
        else:
            media = [
                InputMediaPhoto(
                    media=io.BytesIO(wm),
                    caption=caption if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None,
                )
                for i, wm in enumerate(watermarked)
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
    name = query.from_user.first_name
    await query.edit_message_text(
        f"👋 Привет, {name}!\n\n"
        "Добро пожаловать в <b>AutoBazar NL</b> — маркетплейс автомобилей.\n\n"
        "Здесь вы можете:\n"
        "• 📝 Подать объявление о продаже\n"
        "• 🔍 Найти нужный автомобиль\n"
        "• 📋 Управлять своими объявлениями",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


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

def _build_listings_view(items: list) -> tuple[str, InlineKeyboardMarkup]:
    if not items:
        return "📋 У вас нет объявлений.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🚗 Разместить объявление", callback_data="new_listing")],
            [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
        ])
    now = datetime.now()
    text = "📋 <b>Ваши объявления:</b>\n\n"
    keyboard = []
    for item in items:
        expires = datetime.fromisoformat(item["expires_at"])
        days_left = (expires - now).days
        if item["status"] == "hidden":
            icon, status_str = "🙈", "скрыто"
        elif days_left >= 0:
            icon, status_str = "✅", f"ещё {days_left} дн."
        else:
            icon, status_str = "⏰", "истекло"
        text += f"{icon} <b>#{item['id']}</b> — {item['make']} {item['model']} {item['year']} — {item['price']:,} EUR ({status_str})\n"
        hide_btn = (
            InlineKeyboardButton(f"👁 Показать #{item['id']}", callback_data=f"unhide_{item['id']}")
            if item["status"] == "hidden"
            else InlineKeyboardButton(f"🙈 Скрыть #{item['id']}", callback_data=f"hide_{item['id']}")
        )
        keyboard.append([InlineKeyboardButton(f"✏️ Изменить #{item['id']}", callback_data=f"edit_{item['id']}"), hide_btn])
        keyboard.append([
            InlineKeyboardButton(f"🔄 Продлить #{item['id']}", callback_data=f"extend_{item['id']}"),
            InlineKeyboardButton(f"🗑 #{item['id']}", callback_data=f"del_{item['id']}"),
        ])
    keyboard.append([InlineKeyboardButton("🏠 Меню", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


async def my_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        user_id = update.callback_query.from_user.id
        reply = update.callback_query.edit_message_text
    else:
        user_id = update.effective_user.id
        reply = update.message.reply_text
    items = await get_user_listings(user_id)
    text, markup = _build_listings_view(items)
    await reply(text, parse_mode="HTML", reply_markup=markup)


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


# ====== РЕДАКТИРОВАНИЕ ОБЪЯВЛЕНИЯ ======

async def edit_listing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.split("_")[1])
    listing = await get_listing(listing_id)
    if not listing or listing["user_id"] != query.from_user.id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return ConversationHandler.END
    context.user_data["editing_listing_id"] = listing_id
    await query.edit_message_text(
        f"✏️ <b>Редактирование #{listing_id}</b>\n"
        f"{listing['make']} {listing['model']} {listing['year']}\n\n"
        "Что изменить?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💶 Цену", callback_data="editf_price")],
            [InlineKeyboardButton("📝 Описание", callback_data="editf_desc")],
            [InlineKeyboardButton("❌ Отмена", callback_data="my_listings")],
        ]),
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "editf_price":
        listing_id = context.user_data.get("editing_listing_id")
        listing = await get_listing(listing_id)
        await query.edit_message_text(
            f"💶 Текущая цена: <b>{listing['price']:,} EUR</b>\n\nВведите новую цену:",
            parse_mode="HTML",
        )
        return EDIT_PRICE
    else:
        listing_id = context.user_data.get("editing_listing_id")
        listing = await get_listing(listing_id)
        await query.edit_message_text(
            f"📝 Текущее описание:\n<i>{listing['description']}</i>\n\nВведите новое описание:",
            parse_mode="HTML",
        )
        return EDIT_DESCRIPTION


async def edit_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "").replace(",", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ Введите корректную цену числом, например: 12000")
        return EDIT_PRICE
    listing_id = context.user_data.get("editing_listing_id")
    new_price = int(text)
    await update_listing_field(listing_id, "price", new_price)
    listing = await get_listing(listing_id)
    old_ids = json.loads(listing.get("channel_message_ids") or "[]")
    await delete_from_channel(context, old_ids)
    msg_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)
    await update.message.reply_text(
        f"✅ Цена обновлена: <b>{new_price:,} EUR</b>. Объявление переопубликовано.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings")]]),
    )
    return ConversationHandler.END


def _edit_desc_keyboard(ai_done: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✅ Опубликовать", callback_data="edit_desc_confirm")],
        [InlineKeyboardButton("✨ Улучшить описание AI", callback_data="edit_desc_ai")],
    ]
    if ai_done:
        rows.append([InlineKeyboardButton("↩️ Вернуть оригинал", callback_data="edit_desc_revert_ai")])
    rows.append([InlineKeyboardButton("✏️ Изменить заново", callback_data="edit_desc_retry")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="edit_done")])
    return InlineKeyboardMarkup(rows)


async def edit_description_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 20:
        await update.message.reply_text("⚠️ Описание слишком короткое (минимум 20 символов).")
        return EDIT_DESCRIPTION
    context.user_data["edit_new_desc"] = text
    context.user_data["edit_original_desc"] = text
    await update.message.reply_text(
        "📝 <b>Новое описание:</b>\n\n"
        f"<i>{text}</i>",
        parse_mode="HTML",
        reply_markup=_edit_desc_keyboard(ai_done=False),
    )
    return EDIT_DESC_CONFIRM


async def edit_desc_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✨ Улучшаю...")
    await query.edit_message_text("⏳ Генерирую улучшенное описание...")
    listing_id = context.user_data.get("editing_listing_id")
    listing = await get_listing(listing_id)
    current_desc = context.user_data.get("edit_new_desc", listing["description"])
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": (
                f"Напиши продающее описание для объявления о продаже автомобиля на русском языке.\n\n"
                f"Авто: {listing['make']} {listing['model']} {listing['year']} г.\n"
                f"Пробег: {listing['mileage']:,} км\n"
                f"Топливо: {listing['fuel']}\n"
                f"КПП: {listing['transmission']}\n"
                f"Город: {listing['city']}\n"
                f"Исходное описание: {current_desc}\n\n"
                "Напиши только текст описания (2–4 предложения). Без заголовков, без цены, без контактов."
            )}],
        )
        improved = response.content[0].text.strip()
        context.user_data["edit_new_desc"] = improved
    except Exception as e:
        logger.error(f"AI edit error: {e}")
        improved = current_desc
    await query.edit_message_text(
        "✨ <b>AI улучшило описание:</b>\n\n"
        f"<i>{improved}</i>",
        parse_mode="HTML",
        reply_markup=_edit_desc_keyboard(ai_done=True),
    )
    return EDIT_DESC_CONFIRM


async def edit_desc_revert_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    original = context.user_data.get("edit_original_desc", "")
    context.user_data["edit_new_desc"] = original
    await query.edit_message_text(
        "↩️ <b>Оригинальное описание:</b>\n\n"
        f"<i>{original}</i>",
        parse_mode="HTML",
        reply_markup=_edit_desc_keyboard(ai_done=False),
    )
    return EDIT_DESC_CONFIRM


async def edit_desc_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Публикую обновлённое объявление...")
    listing_id = context.user_data.get("editing_listing_id")
    text = context.user_data.get("edit_new_desc", "")
    try:
        await update_listing_field(listing_id, "description", text)
        listing = await get_listing(listing_id)
        old_ids = json.loads(listing.get("channel_message_ids") or "[]")
        await delete_from_channel(context, old_ids)
        msg_ids = await post_to_channel(context, listing)
        await update_listing_channel_msgs(listing_id, msg_ids)
        result_text = "✅ Описание обновлено и объявление переопубликовано."
    except Exception as e:
        logger.error(f"edit_desc_confirm error: {e}")
        result_text = "⚠️ Ошибка при публикации. Описание сохранено в базе."
    await query.edit_message_text(
        result_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings")]]),
    )
    return ConversationHandler.END


async def edit_desc_retry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Введите новое описание:")
    return EDIT_DESCRIPTION


async def edit_ai_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "❌ Изменение отменено.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Мои объявления", callback_data="my_listings")]]),
    )
    return ConversationHandler.END


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Редактирование отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ====== СКРЫТИЕ ОБЪЯВЛЕНИЯ ======

async def hide_listing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    listing_id = int(query.data.split("_")[1])
    listing = await get_listing(listing_id)
    if not listing or listing["user_id"] != query.from_user.id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return
    old_ids = json.loads(listing.get("channel_message_ids") or "[]")
    await delete_from_channel(context, old_ids)
    await update_listing_channel_msgs(listing_id, [])
    await hide_listing(listing_id)
    await query.answer(f"🙈 Объявление #{listing_id} скрыто из канала", show_alert=True)
    items = await get_user_listings(query.from_user.id)
    text, markup = _build_listings_view(items)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def unhide_listing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    listing_id = int(query.data.split("_")[1])
    listing = await get_listing(listing_id)
    if not listing or listing["user_id"] != query.from_user.id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return
    await unhide_listing(listing_id)
    listing = await get_listing(listing_id)
    msg_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)
    await query.answer(f"👁 Объявление #{listing_id} снова опубликовано", show_alert=True)
    items = await get_user_listings(query.from_user.id)
    text, markup = _build_listings_view(items)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ====== ФОНОВЫЕ ЗАДАЧИ ======

async def check_expiry_warnings(context: ContextTypes.DEFAULT_TYPE):
    listings = await get_listings_expiring_soon()
    for listing in listings:
        expires = datetime.fromisoformat(listing["expires_at"])
        days_left = (expires - datetime.now()).days + 1
        try:
            await context.bot.send_message(
                chat_id=listing["user_id"],
                text=(
                    f"⏰ <b>Объявление #{listing['id']} истекает через {days_left} дн.</b>\n"
                    f"🚗 {listing['make']} {listing['model']} {listing['year']}\n\n"
                    "Продлить объявление ещё на 30 дней?"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"🔄 Продлить #{listing['id']}", callback_data=f"extend_{listing['id']}"),
                ]]),
            )
            await mark_warning_sent(listing["id"])
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание пользователю {listing['user_id']}: {e}")


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


# ====== WEB SERVER ======

async def _serve_webapp(request: aio_web.Request) -> aio_web.Response:
    filepath = os.path.join(WEBAPP_DIR, "index.html")
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return aio_web.Response(content_type="text/html", text=fh.read())
    except FileNotFoundError:
        return aio_web.Response(status=404, text="Not found")


async def _healthcheck(request: aio_web.Request) -> aio_web.Response:
    return aio_web.Response(text="OK")


# ====== TELEGRAM MINI APP HANDLERS ======

_WEBAPP_AWAIT_PHOTOS = "webapp_await_photos"


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.message.web_app_data.data
    try:
        data = json.loads(raw)
    except Exception:
        await update.message.reply_text("⚠️ Ошибка обработки данных. Попробуйте ещё раз.")
        return

    action = data.get("action")

    if action == "search":
        price_max = data.get("price_max")
        results = await search_listings(
            make=data.get("brand") or None,
            price_max=int(price_max) if price_max else None,
            city=data.get("city") or None,
        )
        # In-memory filters for fields not in search_listings
        if data.get("fuel"):
            results = [r for r in results if r.get("fuel") == data["fuel"]]
        if data.get("transmission"):
            results = [r for r in results if r.get("transmission") == data["transmission"]]
        if data.get("year_from"):
            results = [r for r in results if (r.get("year") or 0) >= int(data["year_from"])]
        if data.get("year_to"):
            results = [r for r in results if (r.get("year") or 9999) <= int(data["year_to"])]

        if not results:
            await update.message.reply_text(
                "🔍 По вашим фильтрам объявлений не найдено.\n\n"
                "Попробуйте расширить критерии поиска.",
                reply_markup=main_menu_keyboard(),
            )
            return

        total = len(results)
        await update.message.reply_text(
            f"🔍 Найдено: <b>{total}</b> объявлений\n\n"
            f"Показываю первые {min(5, total)}:",
            parse_mode="HTML",
        )
        for listing in results[:5]:
            caption = listing_caption(listing)
            photos = json.loads(listing.get("photo_ids") or "[]")
            if photos:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photos[0],
                    caption=caption,
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(caption, parse_mode="HTML")
        if total > 5:
            await update.message.reply_text(
                f"...и ещё {total - 5} объявлений. Уточните фильтры для лучших результатов.",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_text("↩️ Главное меню:", reply_markup=main_menu_keyboard())

    elif action == "publish":
        context.user_data["webapp_listing"] = data
        context.user_data["webapp_photos"] = []
        context.user_data[_WEBAPP_AWAIT_PHOTOS] = True
        make = data.get("make", "")
        model = data.get("model", "")
        year = data.get("year", "")
        price = int(data.get("price", 0))
        await update.message.reply_text(
            f"✅ <b>Данные получены!</b>\n\n"
            f"🚗 {make} {model} {year}\n"
            f"💶 €{price:,}\n\n"
            f"Теперь отправьте <b>фотографии</b> (от 1 до 10).\n"
            f"Когда загрузите все — нажмите кнопку ниже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Фото готовы", callback_data="webapp_photos_done"),
            ]]),
        )
    else:
        await update.message.reply_text("⚠️ Неизвестное действие.", reply_markup=main_menu_keyboard())


async def collect_webapp_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get(_WEBAPP_AWAIT_PHOTOS):
        return
    photos = context.user_data.get("webapp_photos", [])
    if len(photos) >= 10:
        await update.message.reply_text(
            "⚠️ Максимум 10 фотографий. Нажмите «Фото готовы».",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"✅ Фото готовы ({len(photos)} шт)", callback_data="webapp_photos_done"),
            ]]),
        )
        return
    photos.append(update.message.photo[-1].file_id)
    context.user_data["webapp_photos"] = photos
    await update.message.reply_text(
        f"📸 Фото {len(photos)}/10 добавлено.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Фото готовы ({len(photos)} шт)", callback_data="webapp_photos_done"),
        ]]),
    )


async def webapp_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not context.user_data.get(_WEBAPP_AWAIT_PHOTOS):
        return

    photos = context.user_data.get("webapp_photos", [])
    if not photos:
        await query.edit_message_text(
            "⚠️ Нет фотографий. Пожалуйста, отправьте хотя бы одно фото.",
        )
        return

    context.user_data[_WEBAPP_AWAIT_PHOTOS] = False
    listing_data = context.user_data.get("webapp_listing", {})
    user = update.effective_user

    full_data = {
        "user_id":      user.id,
        "username":     user.username,
        "make":         listing_data.get("make", ""),
        "model":        listing_data.get("model", ""),
        "year":         int(listing_data.get("year") or 0),
        "mileage":      int(listing_data.get("mileage") or 0),
        "price":        int(listing_data.get("price") or 0),
        "currency":     "EUR",
        "fuel":         listing_data.get("fuel", ""),
        "transmission": listing_data.get("transmission", ""),
        "engine":       listing_data.get("engine", ""),
        "turbo":        "",
        "city":         listing_data.get("city", ""),
        "phone":        listing_data.get("phone", ""),
        "description":  listing_data.get("description", ""),
        "photo_ids":    photos,
    }

    await query.edit_message_text("⏳ Публикую объявление…")
    try:
        listing_id = await create_listing(full_data)
        listing = await get_listing(listing_id)
        msg_ids = await post_to_channel(context, listing)
        await update_listing_channel_msgs(listing_id, msg_ids)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"🎉 <b>Объявление опубликовано!</b>\n\n"
                f"🚗 {full_data['make']} {full_data['model']} {full_data['year']}\n"
                f"💶 €{full_data['price']:,}\n"
                f"📍 {full_data['city']}"
            ),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error(f"webapp_photos_done publish error: {exc}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при публикации. Попробуйте ещё раз.",
            reply_markup=main_menu_keyboard(),
        )


# ====== ЗАПУСК ======

async def post_init(app: Application):
    await init_db()
    # 09:00 UTC = 11:00 Amsterdam (летнее время)
    app.job_queue.run_daily(daily_repost, time=dt_time(9, 0, 0))
    app.job_queue.run_daily(cleanup_expired, time=dt_time(8, 0, 0))
    app.job_queue.run_daily(check_expiry_warnings, time=dt_time(10, 0, 0))
    logger.info("База данных инициализирована. Фоновые задачи запущены.")

    # Start aiohttp web server for Telegram Mini App
    port = int(os.getenv("PORT", 8080))
    web_app = aio_web.Application()
    web_app.router.add_get("/", _healthcheck)
    web_app.router.add_get("/health", _healthcheck)
    web_app.router.add_get("/webapp", _serve_webapp)
    web_app.router.add_get("/webapp/", _serve_webapp)
    runner = aio_web.AppRunner(web_app)
    await runner.setup()
    await aio_web.TCPSite(runner, "0.0.0.0", port).start()
    app.bot_data["web_runner"] = runner
    logger.info(f"✅ Web server запущен на порту {port}")


async def post_shutdown(app: Application):
    runner = app.bot_data.get("web_runner")
    if runner:
        await runner.cleanup()
        logger.info("Web server остановлен.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    listing_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_listing_start, pattern="^new_listing$")],
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

    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_listing_start, pattern=r"^edit_\d+$")],
        states={
            EDIT_CHOOSE_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^editf_")],
            EDIT_PRICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_price_handler)],
            EDIT_DESCRIPTION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_description_handler)],
            EDIT_DESC_CONFIRM: [
                CallbackQueryHandler(edit_desc_confirm_handler, pattern="^edit_desc_confirm$"),
                CallbackQueryHandler(edit_desc_ai_handler, pattern="^edit_desc_ai$"),
                CallbackQueryHandler(edit_desc_revert_ai_handler, pattern="^edit_desc_revert_ai$"),
                CallbackQueryHandler(edit_desc_retry_handler, pattern="^edit_desc_retry$"),
                CallbackQueryHandler(edit_ai_done, pattern="^edit_done$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mylistings", my_listings))
    app.add_handler(CommandHandler("welcome", post_welcome))
    app.add_handler(listing_conv)
    app.add_handler(search_conv)
    app.add_handler(edit_conv)
    app.add_handler(CallbackQueryHandler(my_listings, pattern="^my_listings$"))
    app.add_handler(CallbackQueryHandler(extend_listing_handler, pattern=r"^extend_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_ask, pattern=r"^del_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_listing_confirm, pattern=r"^confirm_del_\d+$"))
    app.add_handler(CallbackQueryHandler(hide_listing_handler, pattern=r"^hide_\d+$"))
    app.add_handler(CallbackQueryHandler(unhide_listing_handler, pattern=r"^unhide_\d+$"))
    app.add_handler(CallbackQueryHandler(go_main_menu, pattern="^main_menu$"))
    # Mini App handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.add_handler(MessageHandler(filters.PHOTO, collect_webapp_photo))
    app.add_handler(CallbackQueryHandler(webapp_photos_done, pattern="^webapp_photos_done$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_fallback))

    logger.info("✅ AutoBazar Bot запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
