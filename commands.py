from aiogram import types
from main import dp, bot, group_chat_id, player_slots

# دستورات فارسی و انگلیسی
COMMANDS = {
    "تگ همه": "tag_all",
    "تگ ادمین": "tag_admins",
    "تگ لیست": "tag_list",
    "tag all": "tag_all",
    "tag admins": "tag_admins",
    "tag list": "tag_list",
}


# تابع اجرای دستور
async def run_command(name, message: types.Message):
    if name == "tag_all":
        # همان تابع cmd_tag_all
        await cmd_tag_all(message)
    elif name == "tag_admins":
        await cmd_tag_admins(message)
    elif name == "tag_list":
        await cmd_tag_players(message)


# هندلر کلی برای متن آزاد و کامندها
@dp.message_handler(lambda m: m.text)
async def handle_text_commands(message: types.Message):
    text = message.text.strip().lower()

    # حذف / در ابتدای متن (کامندها)
    if text.startswith("/"):
        text = text[1:]

    # جایگزینی نیم‌فاصله با فاصله
    text = text.replace("‌", " ")
    text = " ".join(text.split())  # فشرده‌سازی فاصله‌های اضافه

    if text in COMMANDS:
        await run_command(COMMANDS[text], message)
