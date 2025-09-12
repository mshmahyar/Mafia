from aiogram import types
from loader import dp, bot

# دستورات فارسی و انگلیسی
COMMANDS = {
    "تگ همه": "tag_all",
    "تگ ادمین": "tag_admins",
    "تگ لیست": "tag_list",
    "tag all": "tag_all",
    "tag admins": "tag_admins",
    "tag list": "tag_list",
}


async def cmd_tag_all(message: types.Message):
    await message.reply("🔔 تگ همه: " + ", ".join(["@user1", "@user2"]))


async def cmd_tag_admins(message: types.Message):
    await message.reply("🛡 تگ ادمین‌ها")


async def cmd_tag_players(message: types.Message):
    await message.reply("🎭 تگ بازیکنان حاضر")


# اجرای دستور
async def run_command(name, message: types.Message):
    if name == "tag_all":
        await cmd_tag_all(message)
    elif name == "tag_admins":
        await cmd_tag_admins(message)
    elif name == "tag_list":
        await cmd_tag_players(message)


# هندلر متنی
@dp.message_handler(lambda m: m.text)
async def handle_text_commands(message: types.Message):
    text = message.text.strip().lower()

    if text.startswith("/"):
        text = text[1:]

    text = text.replace("‌", " ")
    text = " ".join(text.split())

    if text in COMMANDS:
        await run_command(COMMANDS[text], message)
