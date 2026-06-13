import asyncio
import base64
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
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update,
    WebAppInfo, KeyboardButton, ReplyKeyboardMarkup,
)
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
    update_listing_photo_ids,
    update_listing_watermarked_photos,
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

# ====== СОСТОЯНИЯ ДИАЛОГА: редактирование ======
EDIT_CHOOSE_FIELD, EDIT_PRICE, EDIT_DESCRIPTION, EDIT_AI_CONFIRM, EDIT_DESC_CONFIRM = range(30, 35)

# ====== КЛАВИАТУРЫ ======


def webapp_keyboard(user_id=None):
    """ReplyKeyboard с WebApp-кнопками — sendData работает только отсюда."""
    if not WEBAPP_URL:
        return None
    uid = f"&user_id={user_id}" if user_id else ""
    return ReplyKeyboardMarkup(
        [[
            KeyboardButton("🚗 Подать объявление", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp?tab=publish{uid}")),
            KeyboardButton("🔍 Поиск авто",         web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp?tab=search{uid}")),
            KeyboardButton("📋 Мои авто",            web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp?tab=my{uid}")),
        ]],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


# ====== ВАЛИДАЦИЯ ======


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

    footer = f"\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n📲 Контакт: {_contact(listing)}"
    header = "\n".join(lines) + "\n\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    max_desc = 1024 - len(header) - len(footer)
    desc = listing['description']
    if len(desc) > max_desc:
        desc = desc[:max_desc - 1] + "…"
    return header + desc + footer


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


# ====== ПУБЛИКАЦИЯ В КАНАЛ ======

async def post_to_channel(context, listing: dict) -> tuple[list[int], list[str]]:
    """Returns (message_ids, watermarked_file_ids)."""
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
            return [msg.message_id], [msg.photo[-1].file_id]
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
            return [m.message_id for m in msgs], [m.photo[-1].file_id for m in msgs]
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        return [], []


async def delete_from_channel(context, message_ids: list):
    flat = []
    for item in message_ids:
        if isinstance(item, int):
            flat.append(item)
        elif isinstance(item, list):
            flat.extend(x for x in item if isinstance(x, int))
    for mid in flat:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=mid)
        except Exception as e:
            logger.error(f"delete_message {mid} failed: {e}")


# ====== /start ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    name = update.effective_user.first_name
    kb = webapp_keyboard(update.effective_user.id)
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "Добро пожаловать в <b>AutoBazar NL</b> — маркетплейс автомобилей.\n\n"
        "Здесь вы можете:\n"
        "• 🚗 Подать объявление о продаже\n"
        "• 🔍 Найти нужный автомобиль\n"
        "• 📋 Управлять своими объявлениями",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def go_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    try:
        await query.delete_message()
    except Exception:
        pass
    kb = webapp_keyboard(query.from_user.id)
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🏠 Главное меню",
        reply_markup=kb,
    )


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
        send = update.callback_query.message.reply_text
    else:
        user_id = update.effective_user.id
        send = update.message.reply_text

    if WEBAPP_URL:
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton(
                "📋 Открыть Мои авто",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp?tab=my&user_id={user_id}"),
            )]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await send("👇 Нажмите кнопку ниже, чтобы открыть ваши объявления:", reply_markup=kb)
    else:
        items = await get_user_listings(user_id)
        text, markup = _build_listings_view(items)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        else:
            await send(text, parse_mode="HTML", reply_markup=markup)


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
    msg_ids, wm_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)
    if wm_ids:
        await update_listing_watermarked_photos(listing_id, wm_ids)

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

    kb = webapp_keyboard(update.effective_user.id)
    if result.startswith("MENU"):
        context.user_data.clear()
        await update.message.reply_text("🏠 Главное меню:", reply_markup=kb)
    elif result.startswith("LISTINGS"):
        await my_listings(update, context)
    elif result.startswith("SEARCH"):
        await update.message.reply_text("🔍 Используйте кнопку Поиск авто:", reply_markup=kb)
    else:
        reply_text = result.replace("OTHER", "").strip() or "Используйте меню ниже."
        await update.message.reply_text(reply_text, reply_markup=kb)


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
    msg_ids, wm_ids = await post_to_channel(context, listing)
    await update_listing_channel_msgs(listing_id, msg_ids)
    if wm_ids:
        await update_listing_watermarked_photos(listing_id, wm_ids)
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
        msg_ids, wm_ids = await post_to_channel(context, listing)
        await update_listing_channel_msgs(listing_id, msg_ids)
        if wm_ids:
            await update_listing_watermarked_photos(listing_id, wm_ids)
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
    await update.message.reply_text("Редактирование отменено.", reply_markup=webapp_keyboard(update.effective_user.id))
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
    cached = json.loads(listing.get("watermarked_photo_ids") or "[]")
    caption = listing_caption(listing)
    if cached:
        try:
            if len(cached) == 1:
                msg = await context.bot.send_photo(
                    chat_id=CHANNEL_ID, photo=cached[0],
                    caption=caption, parse_mode="HTML",
                )
                msg_ids = [msg.message_id]
            else:
                media = [
                    InputMediaPhoto(media=fid, caption=caption if i == 0 else None, parse_mode="HTML" if i == 0 else None)
                    for i, fid in enumerate(cached)
                ]
                msgs = await context.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
                msg_ids = [m.message_id for m in msgs]
            await update_listing_channel_msgs(listing_id, msg_ids)
        except Exception as e:
            logger.warning(f"Fast unhide failed, falling back: {e}")
            msg_ids, wm_ids = await post_to_channel(context, listing)
            await update_listing_channel_msgs(listing_id, msg_ids)
            if wm_ids:
                await update_listing_watermarked_photos(listing_id, wm_ids)
    else:
        msg_ids, wm_ids = await post_to_channel(context, listing)
        await update_listing_channel_msgs(listing_id, msg_ids)
        if wm_ids:
            await update_listing_watermarked_photos(listing_id, wm_ids)
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
        msg_ids, wm_ids = await post_to_channel(context, listing)
        if msg_ids:
            await update_listing_channel_msgs(listing["id"], msg_ids)
            if wm_ids:
                await update_listing_watermarked_photos(listing["id"], wm_ids)
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


def _cors(response: aio_web.Response) -> aio_web.Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


async def _api_listings(request: aio_web.Request) -> aio_web.Response:
    """GET /api/listings?user_id=123  — объявления пользователя для Mini App."""
    if request.method == "OPTIONS":
        return _cors(aio_web.Response(text=""))
    try:
        user_id = int(request.rel_url.query.get("user_id", 0))
    except ValueError:
        return _cors(aio_web.Response(status=400, text="bad user_id"))
    if not user_id:
        return _cors(aio_web.Response(status=400, text="user_id required"))

    rows = await get_user_listings(user_id)
    listings = []
    for r in rows:
        from datetime import date
        expires = r.get("expires_at", "")
        days_left = 0
        if expires:
            try:
                exp_date = date.fromisoformat(str(expires)[:10])
                days_left = max(0, (exp_date - date.today()).days)
            except Exception:
                pass
        photo_ids = json.loads(r.get("photo_ids") or "[]")
        listings.append({
            "id":          r["id"],
            "make":        r.get("make", ""),
            "model":       r.get("model", ""),
            "year":        r.get("year", ""),
            "price":       r.get("price", 0),
            "mileage":     r.get("mileage", 0),
            "city":        r.get("city", ""),
            "status":      r.get("status", "active"),
            "days_left":   days_left,
            "description": r.get("description", ""),
            "photo_count": len(photo_ids),
        })
    return _cors(aio_web.Response(
        content_type="application/json",
        text=json.dumps(listings, ensure_ascii=False),
    ))


async def _api_ai_improve(request: aio_web.Request) -> aio_web.Response:
    """POST /api/ai_improve  — улучшить описание через Claude."""
    if request.method == "OPTIONS":
        return _cors(aio_web.Response(text=""))
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
    except Exception:
        return _cors(aio_web.Response(status=400, text="bad json"))
    if len(text) < 10:
        return _cors(aio_web.Response(status=400, text="text too short"))

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content":
                f"Улучши это объявление о продаже автомобиля: исправь грамматику, "
                f"сделай текст чётким и привлекательным для покупателя. "
                f"Верни ТОЛЬКО улучшенный текст без объяснений. "
                f"Максимум 700 символов.\n\n{text}"}],
        )
        improved = resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"AI improve error: {e}")
        return _cors(aio_web.Response(status=500, text="ai error"))

    return _cors(aio_web.Response(
        content_type="application/json",
        text=json.dumps({"improved": improved}, ensure_ascii=False),
    ))


# ====== TELEGRAM MINI APP HANDLERS ======

_WEBAPP_AWAIT_PHOTOS = "webapp_await_photos"


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.message.web_app_data.data
    try:
        data = json.loads(raw)
    except Exception:
        await update.message.reply_text("⚠️ Ошибка обработки данных.")
        return

    action = data.get("action")
    kb = webapp_keyboard(update.effective_user.id)

    # ── ПОИСК ──
    if action == "search":
        brands = data.get("brands") or ([data.get("brand")] if data.get("brand") else [])
        results = await search_listings(city=data.get("city") or None)
        if brands:
            brands_lower = [b.lower() for b in brands]
            results = [r for r in results if r.get("make", "").lower() in brands_lower]
        if data.get("model"):
            m = data["model"].lower()
            results = [r for r in results if m in r.get("model", "").lower()]
        if data.get("fuel"):
            results = [r for r in results if r.get("fuel") == data["fuel"]]
        if data.get("transmission"):
            results = [r for r in results if r.get("transmission") == data["transmission"]]
        if data.get("year_from"):
            results = [r for r in results if (r.get("year") or 0) >= int(data["year_from"])]
        if data.get("year_to"):
            results = [r for r in results if (r.get("year") or 9999) <= int(data["year_to"])]
        if data.get("price_from"):
            results = [r for r in results if (r.get("price") or 0) >= int(data["price_from"])]
        if data.get("price_to"):
            results = [r for r in results if (r.get("price") or 0) <= int(data["price_to"])]

        if not results:
            await update.message.reply_text(
                "🔍 По вашим фильтрам ничего не найдено.\nПопробуйте расширить критерии.",
                reply_markup=kb,
            )
            return

        total = len(results)
        await update.message.reply_text(
            f"🔍 Найдено: <b>{total}</b> объявлений\nПоказываю первые {min(5, total)}:",
            parse_mode="HTML",
        )
        for listing in results[:5]:
            caption = listing_caption(listing)
            photos = json.loads(listing.get("photo_ids") or "[]")
            if photos:
                wm = await _watermark_photo(context.bot, photos[0])
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=io.BytesIO(wm),
                    caption=caption, parse_mode="HTML",
                )
            else:
                await update.message.reply_text(caption, parse_mode="HTML")
        if total > 5:
            await update.message.reply_text(f"...и ещё {total - 5}. Уточните фильтры.", reply_markup=kb)
        else:
            await update.message.reply_text("✅ Все результаты показаны.", reply_markup=kb)

    # ── ПУБЛИКАЦИЯ ──
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

    # ── ФОТО: ДОБАВИТЬ / ИЗМЕНИТЬ ──
    elif action in ("photo_add", "photo_replace"):
        listing_id = data.get("id")
        listing = await get_listing(listing_id) if listing_id else None
        if not listing or listing.get("user_id") != update.effective_user.id:
            await update.message.reply_text("⚠️ Объявление не найдено.", reply_markup=kb)
            return
        existing = json.loads(listing.get("photo_ids") or "[]")
        is_add = action == "photo_add"
        max_new = 10 - len(existing) if is_add else 10
        context.user_data["webapp_photos"] = []
        context.user_data[_WEBAPP_AWAIT_PHOTOS] = True
        context.user_data["webapp_photo_listing_id"] = listing_id
        context.user_data["webapp_photo_mode"] = "add" if is_add else "replace"
        if is_add:
            msg = (f"➕ <b>Добавление фото</b>\n\n"
                   f"🚗 {listing['make']} {listing['model']} {listing['year']}\n"
                   f"Сейчас фото: {len(existing)} шт. Можно добавить ещё {max_new}.\n\n"
                   f"Отправьте новые фото и нажмите кнопку ниже.")
        else:
            msg = (f"✏️ <b>Замена фото</b>\n\n"
                   f"🚗 {listing['make']} {listing['model']} {listing['year']}\n"
                   f"Текущие {len(existing)} фото будут заменены.\n\n"
                   f"Отправьте новые фото (1–10) и нажмите кнопку ниже.")
        await update.message.reply_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Фото готовы", callback_data="webapp_photos_done"),
                InlineKeyboardButton("❌ Отмена", callback_data="webapp_photos_cancel"),
            ]]),
        )

    # ── ДЕЙСТВИЯ С ОБЪЯВЛЕНИЕМ ──
    elif action in ("hide", "unhide", "extend", "delete", "edit"):
        listing_id = data.get("id")
        if not listing_id:
            await update.message.reply_text("⚠️ ID объявления не указан.")
            return
        listing = await get_listing(listing_id)
        if not listing or listing.get("user_id") != update.effective_user.id:
            await update.message.reply_text("⚠️ Объявление не найдено.", reply_markup=kb)
            return

        if action == "hide":
            msg_ids = json.loads(listing.get("channel_message_ids") or "[]")
            logger.info(f"Hide #{listing_id}, channel_message_ids={msg_ids}")
            await delete_from_channel(context, msg_ids)
            await hide_listing(listing_id)
            await update_listing_channel_msgs(listing_id, [])
            await update.message.reply_text("🙈 Объявление скрыто.", reply_markup=kb)

        elif action == "unhide":
            await unhide_listing(listing_id)
            listing["status"] = "active"
            cached = json.loads(listing.get("watermarked_photo_ids") or "[]")
            if cached:
                await update.message.reply_text("⏳ Публикую в канал…", reply_markup=kb)
                async def _republish_cached():
                    try:
                        caption = listing_caption(listing)
                        if len(cached) == 1:
                            msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=cached[0], caption=caption, parse_mode="HTML")
                            msg_ids = [msg.message_id]
                        else:
                            media = [InputMediaPhoto(cached[0], caption=caption, parse_mode="HTML")] + [InputMediaPhoto(f) for f in cached[1:]]
                            msgs = await context.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
                            msg_ids = [m.message_id for m in msgs]
                        await update_listing_channel_msgs(listing_id, msg_ids)
                        await context.bot.send_message(update.effective_chat.id, "👁 Объявление опубликовано в канал.")
                    except Exception as e:
                        logger.error(f"republish cached error: {e}")
                asyncio.create_task(_republish_cached())
            else:
                await update.message.reply_text("⏳ Публикую в канал…", reply_markup=kb)
                async def _republish():
                    try:
                        msg_ids, wm_ids = await post_to_channel(context, listing)
                        await update_listing_channel_msgs(listing_id, msg_ids)
                        if wm_ids:
                            await update_listing_watermarked_photos(listing_id, wm_ids)
                        await context.bot.send_message(update.effective_chat.id, "👁 Объявление опубликовано в канал.")
                    except Exception as e:
                        logger.error(f"republish error: {e}")
                asyncio.create_task(_republish())

        elif action == "extend":
            await extend_listing(listing_id)
            listing = await get_listing(listing_id)
            old_ids = json.loads(listing.get("channel_message_ids") or "[]")
            await delete_from_channel(context, old_ids)
            msg_ids, wm_ids = await post_to_channel(context, listing)
            await update_listing_channel_msgs(listing_id, msg_ids)
            if wm_ids:
                await update_listing_watermarked_photos(listing_id, wm_ids)
            await update.message.reply_text("🔄 Объявление продлено на 30 дней и переопубликовано.", reply_markup=kb)

        elif action == "delete":
            msg_ids = json.loads(listing.get("channel_message_ids") or "[]")
            logger.info(f"Удаление объявления #{listing_id}, channel_message_ids={msg_ids}")
            await delete_from_channel(context, msg_ids)
            await delete_listing(listing_id)
            await update.message.reply_text("🗑 Объявление удалено.", reply_markup=kb)

        elif action == "edit":
            pass  # handled in webapp inline edit panel

    elif action == "edit_save":
        listing_id = data.get("id")
        listing = await get_listing(listing_id) if listing_id else None
        if not listing or listing.get("user_id") != update.effective_user.id:
            await update.message.reply_text("⚠️ Объявление не найдено.", reply_markup=kb)
            return
        new_price = data.get("price")
        new_desc = data.get("description")
        if new_price:
            await update_listing_field(listing_id, "price", int(new_price))
        if new_desc:
            await update_listing_field(listing_id, "description", new_desc)
        await update.message.reply_text("✅ Объявление обновлено.", reply_markup=kb)

    else:
        await update.message.reply_text("⚠️ Неизвестное действие.", reply_markup=kb)


async def _webapp_photo_ack(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    user_data = context.application.user_data.get(data["user_id"], {})
    photos = user_data.get("webapp_photos", [])
    rejected = user_data.pop("webapp_rejected", 0)
    text = f"📸 Добавлено: {len(photos)}/10"
    if rejected:
        text += f"\n⚠️ Отклонено: {rejected} — на фото нет автомобиля"
    await context.bot.send_message(
        chat_id=data["chat_id"],
        text=text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Фото готовы ({len(photos)} шт)", callback_data="webapp_photos_done"),
        ]]),
    )


async def collect_webapp_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get(_WEBAPP_AWAIT_PHOTOS):
        return
    photos = context.user_data.setdefault("webapp_photos", [])
    if len(photos) >= 10:
        await update.message.reply_text("⚠️ Максимум 10 фотографий. Нажмите «Фото готовы».")
        return
    file_id = update.message.photo[-1].file_id
    try:
        tg_file = await context.bot.get_file(file_id)
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
        is_car = resp.content[0].text.strip().upper().startswith("Y")
    except Exception:
        is_car = True
    if is_car:
        photos.append(file_id)
    else:
        context.user_data["webapp_rejected"] = context.user_data.get("webapp_rejected", 0) + 1
    # Дебаунс — одно уведомление после всей группы фото
    for job in context.job_queue.get_jobs_by_name(f"webapp_photo_ack_{update.effective_user.id}"):
        job.schedule_removal()
    context.job_queue.run_once(
        _webapp_photo_ack,
        when=2.0,
        data={"chat_id": update.effective_chat.id, "user_id": update.effective_user.id},
        name=f"webapp_photo_ack_{update.effective_user.id}",
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
    photo_mode = context.user_data.pop("webapp_photo_mode", None)
    photo_listing_id = context.user_data.pop("webapp_photo_listing_id", None)

    # ── Обновление фото существующего объявления ──
    if photo_mode in ("add", "replace") and photo_listing_id:
        listing = await get_listing(photo_listing_id)
        if not listing or listing.get("user_id") != update.effective_user.id:
            await query.edit_message_text("⚠️ Объявление не найдено.")
            return
        if photo_mode == "add":
            existing = json.loads(listing.get("photo_ids") or "[]")
            new_ids = (existing + photos)[:10]
        else:
            new_ids = photos
        await query.edit_message_text("⏳ Обновляю фото…")
        try:
            await update_listing_photo_ids(photo_listing_id, new_ids)
            listing = await get_listing(photo_listing_id)
            old_ids = json.loads(listing.get("channel_message_ids") or "[]")
            await delete_from_channel(context, old_ids)
            msg_ids, wm_ids = await post_to_channel(context, listing)
            await update_listing_channel_msgs(photo_listing_id, msg_ids)
            if wm_ids:
                await update_listing_watermarked_photos(photo_listing_id, wm_ids)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🎉 <b>Фото обновлены!</b>\nОбъявление переопубликовано в канал. Фото: {len(new_ids)} шт.",
                parse_mode="HTML",
                reply_markup=webapp_keyboard(update.effective_user.id),
            )
        except Exception as exc:
            logger.error(f"photo update error: {exc}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Ошибка при обновлении фото.",
                reply_markup=webapp_keyboard(update.effective_user.id),
            )
        return

    # ── Новое объявление ──
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
        msg_ids, wm_ids = await post_to_channel(context, listing)
        await update_listing_channel_msgs(listing_id, msg_ids)
        if wm_ids:
            await update_listing_watermarked_photos(listing_id, wm_ids)
        kb = webapp_keyboard(update.effective_user.id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"🎉 <b>Объявление опубликовано!</b>\n\n"
                f"🚗 {full_data['make']} {full_data['model']} {full_data['year']}\n"
                f"💶 €{full_data['price']:,}\n"
                f"📍 {full_data['city']}"
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.error(f"webapp_photos_done publish error: {exc}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Ошибка при публикации. Попробуйте ещё раз.",
            reply_markup=webapp_keyboard(update.effective_user.id),
        )


async def webapp_photos_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data[_WEBAPP_AWAIT_PHOTOS] = False
    context.user_data.pop("webapp_photos", None)
    context.user_data.pop("webapp_photo_listing_id", None)
    context.user_data.pop("webapp_photo_mode", None)
    await query.edit_message_text("❌ Отменено.", reply_markup=None)


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
    web_app.router.add_get("/api/listings", _api_listings)
    web_app.router.add_post("/api/ai_improve", _api_ai_improve)
    web_app.router.add_route("OPTIONS", "/api/listings", _api_listings)
    web_app.router.add_route("OPTIONS", "/api/ai_improve", _api_ai_improve)
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
    app.add_handler(CallbackQueryHandler(webapp_photos_cancel, pattern="^webapp_photos_cancel$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_fallback))

    logger.info("✅ AutoBazar Bot запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
