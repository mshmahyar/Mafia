import os
import logging
import asyncio
import json
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import random

# ======================
# توکن ربات
# ======================
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise ValueError("API_TOKEN environment variable is not set!")

# ======================
# لاگینگ و کانفیگ
# ======================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# ======================
# دیتاهای بازی
# ======================
game_running = False
lobby_message_id = None
group_chat_id = None

# سناریوها از فایل JSON خوانده می‌شوند
SCENARIOS_FILE = "scenarios.json"
if not os.path.exists(SCENARIOS_FILE):
    with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "سناریو کلاسیک": {"min_players": 5, "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند"]},
            "سناریو ویژه": {"min_players": 6, "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند", "کارآگاه"]}
        }, f, ensure_ascii=False, indent=4)

def load_scenarios():
    with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_scenario(name, min_players, roles):
    scenarios = load_scenarios()
    scenarios[name] = {"min_players": min_players, "roles": roles}
    with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=4)

scenarios = load_scenarios()

admins = set()  # لیست ادمین های گروه
moderator_id = None
selected_scenario = None
players = {}  # {user_id: full_name}

# ======================
# کیبوردها
# ======================
def main_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎮 بازی جدید", callback_data="menu_new_game"),
        InlineKeyboardButton("🗂 مدیریت سناریو", callback_data="menu_manage_scenarios"),
        InlineKeyboardButton("📖 راهنما", callback_data="menu_help")
    )
    return kb

def game_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")
    )
    return kb

def join_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"),
        InlineKeyboardButton("❌ انصراف از بازی", callback_data="leave_game")
    )
    return kb

def admin_lobby_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("▶ شروع بازی", callback_data="start_play"),
        InlineKeyboardButton("⚠ لغو بازی", callback_data="cancel_game"),
        InlineKeyboardButton("🔄 تغییر سناریو", callback_data="choose_scenario")
    )
    return kb

# ======================
# استارت و منوی اصلی
# ======================
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.reply("👋 خوش آمدید به ربات بازی مافیا!", reply_markup=main_menu())

@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text("👋 منوی اصلی:", reply_markup=main_menu())
    await callback.answer()

# ======================
# منوی بازی جدید
# ======================
@dp.callback_query_handler(lambda c: c.data == "menu_new_game")
async def menu_new_game(callback: types.CallbackQuery):
    global group_chat_id, game_running, admins, lobby_message_id
    group_chat_id = callback.message.chat.id
    game_running = True
    admins = {member.user.id for member in await bot.get_chat_administrators(group_chat_id)}
    msg = await callback.message.edit_text(
        "🎮 بازی جدید فعال شد! لطفا سناریو و گرداننده را انتخاب کنید.",
        reply_markup=game_menu()
    )
    lobby_message_id = msg.message_id
    await callback.answer()

# ======================
# انتخاب سناریو و گرداننده
# ======================
@dp.callback_query_handler(lambda c: c.data == "choose_scenario")
async def choose_scenario(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(scen, callback_data=f"scenario_{scen}"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="menu_new_game"))
    await callback.message.edit_text("📝 یک سناریو انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("scenario_"))
async def scenario_selected(callback: types.CallbackQuery):
    global selected_scenario
    selected_scenario = callback.data.replace("scenario_", "")
    # بررسی تعداد بازیکنان نسبت به سناریو
    max_players = len(scenarios[selected_scenario]["roles"])
    if len(players) > max_players:
        await callback.answer(
            f"❌ تعداد بازیکنان بیشتر از حداکثر سناریو ({max_players}) است!",
            show_alert=True
        )
        selected_scenario = None
        return
    await callback.message.edit_text(
        f"📝 سناریو انتخاب شد: {selected_scenario}\nحالا گرداننده را انتخاب کنید.",
        reply_markup=game_menu()
    )
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "choose_moderator")
async def choose_moderator(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    for admin_id in admins:
        member = await bot.get_chat_member(group_chat_id, admin_id)
        kb.add(InlineKeyboardButton(member.user.full_name, callback_data=f"moderator_{admin_id}"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="menu_new_game"))
    await callback.message.edit_text("🎩 یک گرداننده انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("moderator_"))
async def moderator_selected(callback: types.CallbackQuery):
    global moderator_id
    moderator_id = int(callback.data.replace("moderator_", ""))
    await callback.message.edit_text(
        f"🎩 گرداننده انتخاب شد: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name}\nحالا اعضا می‌توانند وارد بازی شوند یا انصراف دهند.",
        reply_markup=join_menu()
    )
    await callback.answer()

# ======================
# ورود و انصراف از بازی
# ======================
@dp.callback_query_handler(lambda c: c.data == "join_game")
async def join_game_callback(callback: types.CallbackQuery):
    user = callback.from_user
    if user.id in players:
        await callback.answer("❌ شما در لیست بازی هستید!", show_alert=True)
        return
    players[user.id] = user.full_name
    await update_lobby()
    await callback.answer("✅ شما به بازی اضافه شدید!")

@dp.callback_query_handler(lambda c: c.data == "leave_game")
async def leave_game_callback(callback: types.CallbackQuery):
    user = callback.from_user
    if user.id not in players:
        await callback.answer("❌ شما در لیست بازی نیستید!", show_alert=True)
        return
    players.pop(user.id)
    await update_lobby()
    await callback.answer("✅ شما از بازی خارج شدید!")

# ======================
# بروزرسانی لابی
# ======================
async def update_lobby():
    if not group_chat_id or not lobby_message_id:
        return
    text = f"📋 **لیست بازی:**\nسناریو: {selected_scenario or 'انتخاب نشده'}\nگرداننده: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name if moderator_id else 'انتخاب نشده'}\n\n"
    if players:
        for uid, name in players.items():
            text += f"- {name}\n"
    else:
        text += "هیچ بازیکنی وارد بازی نشده است.\n"

    # بررسی تعداد بازیکنان نسبت به سناریو
    kb = join_menu()
    if moderator_id and selected_scenario:
        max_players = len(scenarios[selected_scenario]["roles"])
        if len(players) > max_players:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⚠ لغو بازی", callback_data="cancel_game"))
        elif len(players) >= scenarios[selected_scenario]["min_players"]:
            kb = admin_lobby_menu()

    await bot.edit_message_text(text, chat_id=group_chat_id, message_id=lobby_message_id, reply_markup=kb, parse_mode="Markdown")

# ======================
# شروع واقعی بازی
# ======================
@dp.callback_query_handler(lambda c: c.data == "start_play")
async def start_play(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
        return

    roles = scenarios[selected_scenario]["roles"]
    if len(players) < len(roles):
        await callback.answer(f"❌ تعداد بازیکنان کافی نیست! حداقل {len(roles)} نفر نیاز است.", show_alert=True)
        return

    shuffled_roles = random.sample(roles, len(players))
    player_ids = list(players.keys())
    for pid, role in zip(player_ids, shuffled_roles):
        try:
            await bot.send_message(pid, f"🎭 نقش شما: {role}")
        except:
            await bot.send_message(moderator_id, f"⚠ نمی‌توانم نقش را به {players[pid]} ارسال کنم.")

    text = "📜 نقش‌ها برای بازیکنان:\n"
    for pid, role in zip(player_ids, shuffled_roles):
        text += f"{players[pid]} → {role}\n"
    await bot.send_message(moderator_id, text)
    await callback.answer("✅ بازی شروع شد!")

# ======================
# لغو بازی
# ======================
@dp.callback_query_handler(lambda c: c.data == "cancel_game")
async def cancel_game(callback: types.CallbackQuery):
    global game_running, players, selected_scenario, moderator_id
    if callback.from_user.id not in admins:
        await callback.answer("❌ فقط ادمین‌ها می‌توانند بازی را لغو کنند.", show_alert=True)
        return
    game_running = False
    players = {}
    selected_scenario = None
    moderator_id = None
    await callback.message.edit_text("❌ بازی لغو شد و آماده شروع بازی جدید هستیم.", reply_markup=main_menu())
    await callback.answer()

# ======================
# مدیریت سناریو توسط ادمین‌ها در پیام خصوصی
# ======================
@dp.message_handler(commands=["addscenario"])
async def add_scenario_cmd(message: types.Message):
    if message.from_user.id not in admins:
        await message.reply("❌ فقط ادمین‌ها می‌توانند سناریو اضافه کنند.")
        return
    await message.reply(
        "📥 برای افزودن سناریو جدید، متن را این فرمت ارسال کنید:\n"
        "`نام سناریو|حداقل بازیکن|نقش1,نقش2,نقش3,...`\n\n"
        "مثال:\n"
        "`سناریو جدید|5|مافیا,مافیا,شهروند,شهروند,کارآگاه`",
        parse_mode="Markdown"
    )

@dp.message_handler()
async def add_scenario_message(message: types.Message):
    if message.from_user.id not in admins:
        return
    if "|" not in message.text:
        return
    try:
        name, min_p, roles_str = message.text.split("|")
        min_p = int(min_p.strip())
        roles = [r.strip() for r in roles_str.split(",") if r.strip()]
        save_scenario(name.strip(), min_p, roles)
        global scenarios
        scenarios = load_scenarios()
        await message.reply(f"✅ سناریو '{name.strip()}' اضافه شد!")
    except Exception as e:
        await message.reply(f"❌ فرمت یا داده‌ها صحیح نیستند. خطا: {e}")

# ======================
# راهنما
# ======================
@dp.callback_query_handler(lambda c: c.data == "menu_help")
async def menu_help(callback: types.CallbackQuery):
    text = (
        "📖 راهنما ربات مافیا:\n\n"
        "- 🎮 بازی جدید: شروع یک بازی جدید و انتخاب سناریو و گرداننده\n"
        "- 🗂 مدیریت سناریو: افزودن سناریو جدید توسط ادمین‌ها\n"
        "- 📖 راهنما: نمایش این متن\n\n"
        "برای افزودن سناریو جدید در پیام خصوصی ربات، از دستور /addscenario استفاده کنید."
    )
    await callback.message.edit_text(text, reply_markup=main_menu())
    await callback.answer()

# ======================
# استارتاپ امن
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

# ======================
# اجرای ربات
# ======================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
