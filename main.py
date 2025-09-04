import os
import json
import logging
import asyncio
import random
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise ValueError("API_TOKEN environment variable is not set!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# -----------------------
# داده‌های بازی
# -----------------------
game_running = False
lobby_message_id = None
group_chat_id = None
moderator_id = None
selected_scenario = None
players = {}  # {user_id: full_name}
turn_order = []
current_turn_index = 0
turn_task = None
turn_message_id = None
challenges = {}  # {user_id: {"before": [], "after": []}}

SCENARIOS_FILE = "scenarios.json"

# -----------------------
# بارگذاری و ذخیره سناریوها
# -----------------------
def load_scenarios():
    if not os.path.exists(SCENARIOS_FILE):
        with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=4)
    with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_scenarios(scenarios):
    with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=4)

scenarios = load_scenarios()

admins = set()

# -----------------------
# کیبوردها
# -----------------------
def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game"))
    kb.add(InlineKeyboardButton("📝 مدیریت سناریو", callback_data="manage_scenarios"))
    kb.add(InlineKeyboardButton("❓ راهنما", callback_data="help"))
    return kb

def game_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"))
    kb.add(InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"))
    return kb

def join_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"))
    kb.add(InlineKeyboardButton("❌ انصراف از بازی", callback_data="leave_game"))
    kb.add(InlineKeyboardButton("🔄 تغییر سناریو", callback_data="change_scenario"))
    kb.add(InlineKeyboardButton("🛑 لغو بازی", callback_data="cancel_game"))
    return kb

def turn_controls(player_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⏭ پایان نوبت", callback_data=f"end_turn_{player_id}"))
    kb.add(InlineKeyboardButton("⚔ چالش قبل صحبت", callback_data=f"challenge_before_{player_id}"))
    kb.add(InlineKeyboardButton("⚔ چالش بعد صحبت", callback_data=f"challenge_after_{player_id}"))
    return kb

# -----------------------
# منوی اصلی
# -----------------------
@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    global group_chat_id
    group_chat_id = message.chat.id
    admins.update({member.user.id for member in await bot.get_chat_administrators(group_chat_id)})
    await message.reply("👋 منوی اصلی:", reply_markup=main_menu())

@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text("👋 منوی اصلی:", reply_markup=main_menu())
    await callback.answer()

# -----------------------
# بازی جدید
# -----------------------
@dp.callback_query_handler(lambda c: c.data == "new_game")
async def new_game(callback: types.CallbackQuery):
    global game_running, players, selected_scenario, moderator_id, lobby_message_id
    game_running = True
    players.clear()
    selected_scenario = None
    moderator_id = None
    lobby_message_id = None
    await callback.message.edit_text("🎮 بازی جدید شروع شد. لطفاً سناریو و گرداننده را انتخاب کنید.", reply_markup=game_menu())
    await callback.answer()

# -----------------------
# انتخاب سناریو
# -----------------------
@dp.callback_query_handler(lambda c: c.data == "choose_scenario")
async def choose_scenario(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(scen, callback_data=f"scenario_{scen}"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="new_game"))
    await callback.message.edit_text("📝 یک سناریو انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("scenario_"))
async def scenario_selected(callback: types.CallbackQuery):
    global selected_scenario
    selected_scenario = callback.data.replace("scenario_", "")
    await callback.message.edit_text(f"📝 سناریو انتخاب شد: {selected_scenario}\nحالا گرداننده را انتخاب کنید.", reply_markup=game_menu())
    await callback.answer()

# -----------------------
# انتخاب گرداننده
# -----------------------
@dp.callback_query_handler(lambda c: c.data == "choose_moderator")
async def choose_moderator(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    for admin_id in admins:
        member = await bot.get_chat_member(group_chat_id, admin_id)
        kb.add(InlineKeyboardButton(member.user.full_name, callback_data=f"moderator_{admin_id}"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="new_game"))
    await callback.message.edit_text("🎩 یک گرداننده انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("moderator_"))
async def moderator_selected(callback: types.CallbackQuery):
    global moderator_id
    moderator_id = int(callback.data.replace("moderator_", ""))
    await callback.message.edit_text(f"🎩 گرداننده انتخاب شد: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name}\nاعضا می‌توانند وارد بازی شوند یا انصراف دهند.", reply_markup=join_menu())
    await callback.answer()

# -----------------------
# ورود و انصراف از بازی
# -----------------------
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

# -----------------------
# بروزرسانی لابی
# -----------------------
async def update_lobby():
    if not group_chat_id:
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
        kb.add(InlineKeyboardButton("▶ شروع بازی", callback_data="start_play"))
    if lobby_message_id:
        try:
            await bot.edit_message_text(text, chat_id=group_chat_id, message_id=lobby_message_id, reply_markup=kb, parse_mode="Markdown")
        except:
            pass
    else:
        msg = await bot.send_message(group_chat_id, text, reply_markup=kb, parse_mode="Markdown")
        global lobby_message_id
        lobby_message_id = msg.message_id

# -----------------------
# شروع بازی
# -----------------------
@dp.callback_query_handler(lambda c: c.data == "start_play")
async def start_play(callback: types.CallbackQuery):
    global turn_order, current_turn_index, challenges
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
        return

    roles = scenarios[selected_scenario]["roles"]
    if len(players) < len(roles):
        await callback.answer(f"❌ تعداد بازیکنان کافی نیست! حداقل {len(roles)} نفر نیاز است.", show_alert=True)
        return

    if len(players) > len(roles):
        await callback.answer(f"❌ تعداد بازیکنان بیشتر از ظرفیت سناریو است!", show_alert=True)
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

    turn_order = player_ids.copy()
    random.shuffle(turn_order)
    current_turn_index = 0
    challenges = {pid: {"before": [], "after": []} for pid in turn_order}
    await start_turn(turn_order[current_turn_index])

# -----------------------
# شروع نوبت
# -----------------------
async def start_turn(player_id):
    global turn_message_id
    text = f"⏱ نوبت: {players[player_id]}\nزمان: 02:00"
    msg = await bot.send_message(group_chat_id, text, reply_markup=turn_controls(player_id))
    turn_message_id = msg.message_id
    await bot.pin_chat_message(group_chat_id, turn_message_id)
    asyncio.create_task(run_timer(player_id, 120))  # ۲ دقیقه تایمر

async def run_timer(player_id, seconds):
    global current_turn_index, turn_order, turn_message_id
    remaining = seconds
    while remaining >= 0:
        try:
            await bot.edit_message_text(f"⏱ نوبت: {players[player_id]}\nزمان: {remaining//60:02d}:{remaining%60:02d}",
                                        chat_id=group_chat_id, message_id=turn_message_id,
                                        reply_markup=turn_controls(player_id))
        except:
            pass
        await asyncio.sleep(10)
        remaining -= 10
    await next_turn(player_id)

async def next_turn(player_id):
    global current_turn_index, turn_order
    if current_turn_index + 1 < len(turn_order):
        current_turn_index += 1
        await start_turn(turn_order[current_turn_index])
    else:
        await bot.send_message(group_chat_id, "✅ فاز روز تمام شد!")

# -----------------------
# کنترل دستی پایان نوبت
# -----------------------
@dp.callback_query_handler(lambda c: c.data.startswith("end_turn_"))
async def end_turn_callback(callback: types.CallbackQuery):
    pid = int(callback.data.replace("end_turn_", ""))
    if callback.from_user.id != pid and callback.from_user.id != moderator_id:
        await callback.answer("❌ شما نمی‌توانید این نوبت را پایان دهید.", show_alert=True)
        return
    await next_turn(pid)
    await callback.answer("⏭ نوبت به پایان رسید.")

# -----------------------
# استارتاپ امن
# -----------------------
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

# -----------------------
# اجرای ربات
# -----------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
