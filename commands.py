from aiogram import types
from main import dp, bot, group_chat_id, player_slots

# ======================
# دستور: تگ همه
# ======================
async def cmd_tag_all(message: types.Message):
    if not group_chat_id:
        await message.reply("❌ گروه ثبت نشده.")
        return

    members = []
    try:
        async for member in bot.iter_chat_members(group_chat_id):
            if not member.user.is_bot:
                members.append(member.user)
    except Exception as e:
        await message.reply("⚠️ خطا در دریافت اعضا.")
        return

    if not members:
        await message.reply("❌ عضوی یافت نشد.")
        return

    tags = " ".join([f"[{m.first_name}](tg://user?id={m.id})" for m in members])
    await message.reply(f"📢 تگ همه:\n{tags}", parse_mode="Markdown")


# ======================
# دستور: تگ ادمین
# ======================
async def cmd_tag_admins(message: types.Message):
    if not group_chat_id:
        await message.reply("❌ گروه ثبت نشده.")
        return

    admins = []
    try:
        admins = await bot.get_chat_administrators(group_chat_id)
    except Exception:
        await message.reply("⚠️ خطا در دریافت ادمین‌ها.")
        return

    if not admins:
        await message.reply("❌ ادمینی یافت نشد.")
        return

    tags = " ".join([f"[{a.user.first_name}](tg://user?id={a.user.id})" for a in admins if not a.user.is_bot])
    await message.reply(f"👮 تگ ادمین‌ها:\n{tags}", parse_mode="Markdown")


# ======================
# دستور: تگ لیست بازیکنان
# ======================
async def cmd_tag_players(message: types.Message):
    if not player_slots:
        await message.reply("❌ لیست بازیکنان خالی است.")
        return

    tags = " ".join([f"[{uid}](tg://user?id={uid})" for uid in player_slots.values()])
    await message.reply(f"🎮 بازیکنان حاضر:\n{tags}", parse_mode="Markdown")


# ======================
# دیکشنری دستورات
# ======================
COMMANDS = {
    "تگ همه": cmd_tag_all,
    "تگ ادمین": cmd_tag_admins,
    "تگ لیست": cmd_tag_players,

    # انگلیسی
    "tag all": cmd_tag_all,
    "tag admins": cmd_tag_admins,
    "tag list": cmd_tag_players,
}


# ======================
# هندلر کلی برای همه دستورات
# ======================
@dp.message_handler(lambda m: m.text and m.chat.type in ["group", "supergroup"])
async def handle_text_commands(message: types.Message):
    print("📩 متن دریافت شد:", message.text)  # دیباگ
    text = message.text.strip().lower()
    if text in COMMANDS:
        await COMMANDS[text](message)

