from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import anthropic

# ====== НАСТРОЙКИ ======
from dotenv import load_dotenv
import os

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Список разрешённых пользователей (ID)
# Оставь пустым [] чтобы разрешить всем
ALLOWED_USERS = []

# ====== РЕЖИМЫ БОТА ======
MODES = {
    "assistant": {
        "name": "🤖 Ассистент",
        "prompt": "Ты полезный AI ассистент. Отвечай чётко и по делу."
    },
    "translator": {
        "name": "🌍 Переводчик",
        "prompt": "Ты профессиональный переводчик. Определи язык текста и переведи его на русский. Если текст уже на русском — переведи на английский. Только перевод, без лишних слов."
    },
    "business": {
        "name": "👨‍💼 Бизнес",
        "prompt": "Ты опытный бизнес-консультант. Помогаешь с бизнес-планами, стратегиями, анализом рынка и деловыми вопросами. Отвечай профессионально и структурированно."
    },
    "fun": {
        "name": "😂 Развлечения",
        "prompt": "Ты весёлый и остроумный собеседник. Шутишь, рассказываешь анекдоты, поднимаешь настроение. Общаешься легко и непринуждённо."
    }
}

# ====== ХРАНИЛИЩЕ ДАННЫХ ======
conversations = {}
user_modes = {}

# ====== ПРОВЕРКА ДОСТУПА ======
def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

# ====== ГЛАВНОЕ МЕНЮ ======
def get_main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🤖 Ассистент", callback_data="mode_assistant"),
            InlineKeyboardButton("🌍 Переводчик", callback_data="mode_translator")
        ],
        [
            InlineKeyboardButton("👨‍💼 Бизнес", callback_data="mode_business"),
            InlineKeyboardButton("😂 Развлечения", callback_data="mode_fun")
        ],
        [
            InlineKeyboardButton("🗑 Очистить историю", callback_data="clear")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ====== КОМАНДА /start ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name

    if not is_allowed(user_id):
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту.")
        return

    conversations[user_id] = []
    user_modes[user_id] = "assistant"

    await update.message.reply_text(
        f"Привет, {user_name}! 👋\n\n"
        f"Я AI ассистент на базе Claude.\n"
        f"Выбери режим работы:",
        reply_markup=get_main_menu()
    )

# ====== КОМАНДА /menu ======
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту.")
        return

    current_mode = user_modes.get(user_id, "assistant")
    mode_name = MODES[current_mode]["name"]

    await update.message.reply_text(
        f"Текущий режим: {mode_name}\n\nВыбери режим:",
        reply_markup=get_main_menu()
    )

# ====== ОБРАБОТКА КНОПОК ======
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    await query.answer()

    if query.data == "clear":
        conversations[user_id] = []
        await query.edit_message_text(
            "🗑 История очищена!\n\nВыбери режим:",
            reply_markup=get_main_menu()
        )
        return

    if query.data.startswith("mode_"):
        mode = query.data.replace("mode_", "")
        user_modes[user_id] = mode
        mode_name = MODES[mode]["name"]
        conversations[user_id] = []

        await query.edit_message_text(
            f"✅ Режим: {mode_name}\n\nИстория очищена. Начинаем!\nНапиши мне что-нибудь 👇"
        )

# ====== ОБРАБОТКА СООБЩЕНИЙ ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту.")
        return

    user_text = update.message.text

    if user_id not in conversations:
        conversations[user_id] = []
    if user_id not in user_modes:
        user_modes[user_id] = "assistant"

    conversations[user_id].append({
        "role": "user",
        "content": user_text
    })

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        current_mode = user_modes[user_id]
        system_prompt = MODES[current_mode]["prompt"]

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=conversations[user_id]
        )

        assistant_reply = response.content[0].text

        conversations[user_id].append({
            "role": "assistant",
            "content": assistant_reply
        })

        if len(conversations[user_id]) > 20:
            conversations[user_id] = conversations[user_id][-20:]

        # Кнопка меню под каждым ответом
        keyboard = [[InlineKeyboardButton("☰ Меню", callback_data="show_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            assistant_reply,
            reply_markup=reply_markup
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

# ====== ПОКАЗ МЕНЮ ПО КНОПКЕ ======
async def show_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    current_mode = user_modes.get(user_id, "assistant")
    mode_name = MODES[current_mode]["name"]

    await query.answer()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"Текущий режим: {mode_name}\n\nВыбери режим:",
        reply_markup=get_main_menu()
    )

# ====== ЗАПУСК ======
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(show_menu_callback, pattern="^show_menu$"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()