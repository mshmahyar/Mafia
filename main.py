import os
import logging
import asyncio
import json
import random
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from aiogram.utils import executor

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
moderator_id = None
selected_scenario = None
players = {}  # {user_id: full_name}
scenarios = {}  # سناریوها از فایل load خواهند شد
current_turn_index = 0
turn_order = []
turn_task = None
challenge_queue = []

SCENARIO_FILE = "scenarios.json"

# ======================
# بارگذاری سناریوها
# ======================
def load_scenarios():
    if not os.path.exists(SCENARIO_FILE):
        with open(SCENARIO_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "سناریو کلاسیک": {"min_players": 5, "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند"]},
                "سناریو ویژه": {"min_players": 6, "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند", "کارآگاه"]}
            }, f, ensure_ascii=False, indent=2)
    with open(SCENARIO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_scenarios():
    with open(SCENARIO_FILE, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)

scenarios = load_scenarios()

# ======================
# کیبوردها
# ======================
def main_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game"),
        InlineKeyboardButton("📝 مدیریت سناریو", callback_data="manage_scenarios"),
        InlineKeyboardButton("ℹ راهنما", callback_data="help")
    )
    return kb

def game_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"))
    return kb

def join_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"),
        InlineKeyboardButton("❌ انصراف از بازی", callback_data="leave_game")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_game"))
    return kb

# ======================
# منوی اصلی
# ======================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.reply("🎲 خوش آمدید! منوی اصلی:", reply_markup=main_menu())

# ======================
# مدیریت منوها
# ======================
@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_main(callback: types.CallbackQuery):
    await callback.message.edit_text("🎲 منوی اصلی:", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "back_game")
async def back_game(callback: types.CallbackQuery):
    await callback.message.edit_text("🎮 منوی بازی:", reply_markup=game_menu())
    await callback.answer()

# ======================
# شروع بازی جدید
# ======================
@dp.callback_query_handler(lambda c: c.data == "new_game")
async def new_game(callback: types.CallbackQuery):
    global game_running, lobby_message_id, group_chat_id, moderator_id, selected_scenario, players
    group_chat_id = callback.message.chat.id
    game_running = True
    moderator_id = None
    selected_scenario = None
    players = {}
    msg = await callback.message.edit_text("🎮 بازی جدید فعال شد! لطفا سناریو و گرداننده را انتخاب کنید.", reply_markup=game_menu())
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
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_game"))
    await callback.message.edit_text("📝 یک سناریو انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("scenario_"))
async def scenario_selected(callback: types.CallbackQuery):
    global selected_scenario
    selected_scenario = callback.data.replace("scenario_", "")
    await callback.message.edit_text(f"📝 سناریو انتخاب شد: {selected_scenario}\nحالا گرداننده را انتخاب کنید.", reply_markup=game_menu())
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "choose_moderator")
async def choose_moderator(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    admins = {member.user.id for member in await bot.get_chat_administrators(group_chat_id)}
    for admin_id in admins:
        member = await bot.get_chat_member(group_chat_id, admin_id)
        kb.add(InlineKeyboardButton(member.user.full_name, callback_data=f"moderator_{admin_id}"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_game"))
    await callback.message.edit_text("🎩 یک گرداننده انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("moderator_"))
async def moderator_selected(callback: types.CallbackQuery):
    global moderator_id
    moderator_id = int(callback.data.replace("moderator_", ""))
    member = await bot.get_chat_member(group_chat_id, moderator_id)
    await callback.message.edit_text(f"🎩 گرداننده انتخاب شد: {member.user.full_name}\nحالا اعضا می‌توانند وارد بازی شوند یا انصراف دهند.", reply_markup=join_menu())
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
    text = f"📋 **لیست بازی:**\nسناریو: {selected_scenario}\nگرداننده: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name if moderator_id else 'انتخاب نشده'}\n\n"
    if players:
        for uid, name in players.items():
            text += f"- {name}\n"
    else:
        text += "هیچ بازیکنی وارد بازی نشده است.\n"

    min_players = scenarios[selected_scenario]["min_players"] if selected_scenario else 0
    kb = join_menu()
    if len(players) >= min_players and moderator_id:
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("▶ شروع بازی", callback_data="start_play"))
    await bot.edit_message_text(text, chat_id=group_chat_id, message_id=lobby_message_id, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ======================
# شروع بازی و پخش نقش‌ها
# ======================
@dp.callback_query_handler(lambda c: c.data == "start_play")
async def start_play(callback: types.CallbackQuery):
    global turn_order, current_turn_index
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
        return

    roles = scenarios[selected_scenario]["roles"]
    if len(players) < len(roles):
        await callback.answer(f"❌ تعداد بازیکنان کافی نیست! حداقل {len(roles)} نفر نیاز است.", show_alert=True)
        return

    shuffled_roles = random.sample(roles, len(players))
    player_ids = list(players.keys())
    turn_order = player_ids.copy()
    random.shuffle(turn_order)
    current_turn_index = 0

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
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

# ======================
# اجرای ربات
# ======================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
