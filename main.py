import os
import json
import random
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import html
import commands

# ======================
# تنظیمات ربات
# ======================
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise ValueError("API_TOKEN environment variable is not set!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# =========================
# مدیریت چند بازی (Global)
# =========================
# نگهداری اطلاعات بازی‌ها به ازای هر گروه
games = {}  
# { group_id: { "players": {}, "player_slots": {}, "reserves": {}, "eliminated": {}, ... } }

def ensure_game_entry(group_id):
    """ایجاد یا برگشت ورودی بازی برای یک گروه"""
    if group_id not in games:
        games[group_id] = {
            "players": {},              # {user_id: name}
            "player_slots": {},         # {seat: user_id}
            "reserves": {},             # {user_id: name}
            "eliminated": {},           # {user_id: name}
            "moderator": None,          # user_id گرداننده (اگر تعیین شده)
            "admins": set(),            # set of admin ids in that group
            "lobby_message_id": None,
            "game_running": False,

            # مدیریت سناریو
            "selected_scenario": None,  # سناریوی انتخابی
            "scenarios": {},            # لیست سناریوها
            "scenarios": load_scenarios(),   # 🔹 بارگذاری سناریوها اینجا

            # پیام‌ها
            "game_message_id": None,
            "group_chat_id": None,
            "lobby_active": False,      # وقتی لابی فعال است (انتخاب سناریو و گرداننده)

            # نوبت‌ها
            "turn_order": [],           # ترتیب نوبت‌ها
            "current_turn_index": 0,    # اندیس نوبت فعلی
            "current_turn_message_id": None,  
            "turn_timer_task": None,    

            # چالش‌ها
            "challenge_requests": {},  
            "pending_challenges": {},
            "active_challenger_seats": set(),
            "challenge_mode": False,    

            # توقف و ادامه
            "paused_main_player": None, 
            "paused_main_duration": None, 
            "DEFAULT_TURN_DURATION": 120,  

            # وضعیت چالش
            "challenges": {},  
            "challenge_active": True,
            "post_challenge_advance": False   
        }
    return games[group_id]


    
def extract_group_id_from_callback(callback):
    """
    الگوها:
      - در پیوی: callback.data ممکنه 'action_{group_id}' یا 'action_{group_id}_{other}'
      - در گروه: callback.data ممکنه فقط 'action' و گروه را از callback.message.chat.id می‌گیریم
    """
    data = callback.data or ""
    parts = data.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        try:
            return int(parts[1])
        except:
            pass
    # fallback: گروه از محل پیام (اگر از گروه اومده باشه)
    return callback.message.chat.id    

def sync_game_to_globals(group_id):
    global players, player_slots, reserves, eliminated
    global moderator, lobby_message_id, game_running
    global selected_scenario, scenarios

    game = ensure_game_entry(group_id)
    players = game["players"].copy()
    player_slots = game["player_slots"].copy()
    reserves = game["reserves"].copy()
    eliminated = game["eliminated"].copy()
    moderator = game["moderator"]
    lobby_message_id = game["lobby_message_id"]
    game_running = game["game_running"]
    selected_scenario = game["selected_scenario"]
    scenarios = game["scenarios"].copy()

def sync_globals_from_game(group_id):
    """games[group_id] → globals"""
    global players, player_slots, reserves, eliminated
    global moderator, lobby_message_id, game_running
    global selected_scenario, scenarios

    game = ensure_game_entry(group_id)
    players = game["players"].copy()
    player_slots = game["player_slots"].copy()
    reserves = game["reserves"].copy()
    eliminated = game["eliminated"].copy()
    moderator = game["moderator"]
    lobby_message_id = game["lobby_message_id"]
    game_running = game["game_running"]
    selected_scenario = game["selected_scenario"]
    scenarios = game["scenarios"].copy()


def sync_game_from_globals(group_id):
    """globals → games[group_id]"""
    game = ensure_game_entry(group_id)
    game["players"] = players.copy()
    game["player_slots"] = player_slots.copy()
    game["reserves"] = reserves.copy()
    game["eliminated"] = eliminated.copy()
    game["moderator"] = moderator
    game["lobby_message_id"] = lobby_message_id
    game["game_running"] = game_running
    game["selected_scenario"] = selected_scenario
    game["scenarios"] = scenarios.copy()



#=======================
# داده های ریست در شروع روز
#=======================
def reset_round_data():
    global current_turn_index, turn_order, challenge_requests, active_challenger_seats
    global paused_main_player, paused_main_duration, post_challenge_advance, pending_challenges

    current_turn_index = 0
    turn_order = []
    challenge_requests = {}
    active_challenger_seats = set()
    paused_main_player = None
    paused_main_duration = None
    post_challenge_advance = False
    pending_challenges = {}

# ======================
#  لود سناریوها
# ======================
def load_scenarios():
    path = os.path.join(os.path.dirname(__file__), "scenarios.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # خروجی dict یا list بر اساس ساختار فایل

# ======================
# کیبوردها
# ======================
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game"),
        InlineKeyboardButton("⚙ مدیریت سناریو", callback_data="manage_scenarios"),
        InlineKeyboardButton("📖 راهنما", callback_data="help")
    )
    return kb

def game_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator")
    )
    return kb

def join_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"),
        InlineKeyboardButton("❌ انصراف", callback_data="leave_game")
    )
    return kb
#=========================
# توابع کمکی
#=========================
# پیام موقتی
async def send_temp_message(chat_id, text, delay=5, **kwargs):
    msg = await bot.send_message(chat_id, text, **kwargs)
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, msg.message_id)
    except:
        pass

# ======================
# انتخاب / لغو انتخاب صندلی
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("slot_"))
async def handle_slot(callback: types.CallbackQuery):
    global player_slots, player_slots
    user = callback.from_user
    seat_number = int(callback.data.split("_")[1])
    
    if not selected_scenario:
        await callback.answer("❌ هنوز سناریویی انتخاب نشده.", show_alert=True)
        return
    try:
        seat_number = int(callback.data.split("_", 1)[1])
    except Exception:
        await callback.answer("⚠ شماره صندلی نامعتبر است.", show_alert=True)
        return
        
    if user.id not in players:
        await callback.answer("❌ ابتدا وارد بازی شوید.", show_alert=True)
        return   
        
        
    slot_num = int(callback.data.replace("slot_", ""))
    user_id = callback.from_user.id

    # اگه همون بازیکن دوباره بزنه → لغو انتخاب
    if slot_num in player_slots and player_slots[slot_num] == user_id:
        del player_slots[slot_num]
        await callback.answer(f"جایگاه {slot_num} آزاد شد ✅")
        await update_lobby()
        return
        
    else:
        # اگه جایگاه پر باشه
        if seat_number in player_slots and player_slots[seat_number] != user.id:
            await callback.answer("❌ این صندلی قبلاً رزرو شده است.", show_alert=True)
            return
        # اگه بازیکن قبلاً جای دیگه نشسته → اون رو آزاد کن
    for seat, uid in list(player_slots.items()):
        if uid == user.id:
            del player_slots[seat]
            
    player_slots[seat_number] = user.id
    await callback.answer(f"✅ صندلی {seat_number} برای شما رزرو شد.")        
    await update_lobby()
    
def turn_keyboard(seat, is_challenge=False):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("⏭ نکست", callback_data=f"next_{seat}"))

    if not is_challenge:
        if not challenge_active:
            return kb
        player_id = player_slots.get(seat)
        if player_id:
            # فقط اگر این بازیکن قبلاً چالش داده (accept کرده) → دکمه حذف بشه
            if seat in active_challenger_seats:
                return kb

            # فقط اگر هنوز درخواست pending داره → دکمه غیرفعال بشه
            already_challenged = any(
                reqs.get(player_id) == "pending"
                for reqs in challenge_requests.values()
            )
            if not already_challenged:
                kb.add(InlineKeyboardButton("⚔ درخواست چالش", callback_data=f"challenge_request_{seat}"))

    return kb

# ======================
# دستورات اصلی
# ======================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    if message.chat.type == "private":
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game"))
        # دکمه مدیریت بازی فقط برای مدیران (ادمین‌های ثبت‌شده) و گرداننده‌ها نمایش می‌یابد.
        # اینجا نمایش فقط به خودِ کاربر بستگی داره؛ واقعاً بررسی گروه‌ها در manage_game انجام میشه.
        kb.add(InlineKeyboardButton("🛠 مدیریت بازی", callback_data="manage_game"))
        kb.add(InlineKeyboardButton("⚙ مدیریت سناریو", callback_data="manage_scenario"))
        kb.add(InlineKeyboardButton("📚 راهنما", callback_data="help"))
        await message.reply("📋 منوی ربات:", reply_markup=kb)
    else:
        # برای گروه همان منوی گروه قبلی را نمایش بده (بدون تغییر)
        kb = main_menu_keyboard()  # فرض بر این است تو این تابع منوی گروه را می‌سازی
        await message.reply("🏠 منوی اصلی گروه:", reply_markup=kb)


    group_chat_id = callback.message.chat.id
    admins = {member.user.id for member in await bot.get_chat_administrators(group_chat_id)}

    games[group_chat_id] = {
        "players": [],      # بازیکنان حاضر
        "reserves": [],     # بازیکنان رزرو
        "eliminated": [],   # بازیکنان حذف‌شده
        "moderator": callback.from_user.id,  # فعلاً کسی که بازی رو شروع کرده
        "admins": admins
    }

    msg = await callback.message.reply(
        "🎮 بازی مافیا فعال شد!\nلطفا سناریو و گرداننده را انتخاب کنید:",
        reply_markup=game_menu_keyboard()
    )
    lobby_message_id = msg.message_id

    await callback.answer()

def lobby_menu_keyboard(group_id):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator")
    )
    kb.add(InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_gameplay_{group_id}"))
    return kb


@dp.callback_query_handler(lambda c: c.data.startswith("start_gameplay_"))
async def start_gameplay(callback: types.CallbackQuery):
    _, group_id = callback.data.split("_", 1)
    group_id = int(group_id)

    game = games.get(group_id)
    if not game:
        await callback.answer("❌ بازی پیدا نشد.", show_alert=True)
        return

    if not game["selected_scenario"] or not game["moderator"]:
        await callback.answer("⚠️ ابتدا سناریو و گرداننده را انتخاب کنید.", show_alert=True)
        return

    game["game_running"] = True
    game["lobby_active"] = False

    await callback.message.edit_text("🔥 بازی شروع شد! موفق باشید 🎭")


#=============================
# شروع بازی
#=============================
@dp.callback_query_handler(lambda c: c.data == "new_game")
async def start_game(callback: types.CallbackQuery):
    group_id = callback.message.chat.id

    game = ensure_game_entry(group_id)
    game["group_chat_id"] = group_id
    game["lobby_active"] = True
    game["game_running"] = False
    game["admins"] = {m.user.id for m in await bot.get_chat_administrators(group_id)}

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator")
    )

    msg = await callback.message.reply(
        "🎮 بازی مافیا فعال شد!\nلطفاً سناریو و گرداننده را انتخاب کنید:",
        reply_markup=kb
    )
    game["lobby_message_id"] = msg.message_id

    await callback.answer()


#=============================
# ای پی آی داخلی
#=============================
def get_game(group_id):
    return games.get(group_id)

def add_player_to_game(group_id, user_id, name, seat=None):
    g = ensure_game_entry(group_id)
    g["players"][user_id] = name
    if seat is not None:
        g["player_slots"][seat] = user_id

def remove_player_from_game(group_id, user_id):
    g = ensure_game_entry(group_id)
    # حذف از players و player_slots
    g["players"].pop(user_id, None)
    # remove from slots
    for s,u in list(g["player_slots"].items()):
        if u == user_id:
            del g["player_slots"][s]
    # اضافه به eliminated
    g["eliminated"][user_id] = "نام_قبلی"  # یا name اگر داری

#=============================
# حذف بازیکن
#=============================
@dp.callback_query_handler(lambda c: c.data.startswith("remove"))
async def remove_player_handler(callback: types.CallbackQuery):
    # استخراج group_id: اگر callback.data الگو 'remove_{group_id}' باشد از آن استفاده کن
    data = callback.data
    if "_" in data and data.split("_",1)[1].isdigit():
        group_id = int(data.split("_",1)[1])
    else:
        group_id = callback.message.chat.id

    g = get_game(group_id)
    if not g:
        await callback.answer("❌ بازی فعالی برای این گروه یافت نشد.", show_alert=True)
        return

    # نمایش لیست بازیکنان حاضر (keyboard) برای انتخاب حذف
    kb = InlineKeyboardMarkup(row_width=2)
    for uid, name in g["players"].items():
        kb.add(InlineKeyboardButton(name, callback_data=f"do_remove_{group_id}_{uid}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="manage_game"))
    await callback.message.edit_text("❌ یک بازیکن را برای حذف انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("do_remove_"))
async def do_remove_player(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    # do_remove_{group_id}_{user_id}
    if len(parts) < 3:
        await callback.answer("❌ دادهٔ نامعتبر.", show_alert=True)
        return
    group_id = int(parts[1])
    user_id = int(parts[2])

    g = get_game(group_id)
    if not g:
        await callback.answer("❌ بازی پیدا نشد.", show_alert=True)
        return

    # فقط گرداننده اجازه حذف دارد
    if callback.from_user.id != g.get("moderator"):
        await callback.answer("❌ فقط گرداننده می‌تواند حذف کند.", show_alert=True)
        return

    # انجام حذف (استفاده از helper)
    name = g["players"].get(user_id)
    remove_player_from_game(group_id, user_id)
    g["eliminated"][user_id] = name or "نام‌ناشناخته"

    await callback.message.edit_text(f"❌ بازیکن {name} حذف شد.", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ بازگشت", callback_data=f"manage_{group_id}")))
    await callback.answer()
#=============================
# جایگزین
#=============================
# نمایش رزروها
@dp.callback_query_handler(lambda c: c.data.startswith("replace"))
async def start_replace(callback: types.CallbackQuery):
    group_id = extract_group_id_from_callback(callback)
    g = get_game(group_id)
    kb = InlineKeyboardMarkup()
    for uid, name in g["reserves"].items():
        kb.add(InlineKeyboardButton(name, callback_data=f"select_reserve_{group_id}_{uid}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data=f"manage_{group_id}"))
    await callback.message.edit_text("🔄 یک بازیکن از رزرو انتخاب کن:", reply_markup=kb)
    await callback.answer()

# انتخاب رزرو -> نمایش بازیکنان حاضر
@dp.callback_query_handler(lambda c: c.data.startswith("select_reserve_"))
async def select_reserve(callback: types.CallbackQuery):
    _, group_str, reserve_uid_str = callback.data.split("_", 2)
    group_id = int(group_str); reserve_uid = int(reserve_uid_str)
    g = get_game(group_id)
    kb = InlineKeyboardMarkup()
    for uid, name in g["players"].items():
        kb.add(InlineKeyboardButton(name, callback_data=f"do_replace_{group_id}_{reserve_uid}_{uid}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data=f"replace_{group_id}"))
    await callback.message.edit_text("🔄 به چه بازیکنی می‌خواهید جایگزین کنید؟", reply_markup=kb)
    await callback.answer()

# انجام جایگزینی
@dp.callback_query_handler(lambda c: c.data.startswith("do_replace_"))
async def do_replace(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    # do_replace_{group_id}_{reserve_uid}_{target_uid}
    group_id, reserve_uid, target_uid = int(parts[1]), int(parts[2]), int(parts[3])
    g = get_game(group_id)
    # حرکت‌ها: reserve -> players, target -> eliminated
    reserve_name = g["reserves"].pop(reserve_uid, None)
    if not reserve_name:
        await callback.answer("❌ بازیکن رزرو موجود نیست.", show_alert=True); return
    # پیدا کردن صندلی هدف و جابجایی
    for seat, uid in list(g["player_slots"].items()):
        if uid == target_uid:
            g["player_slots"][seat] = reserve_uid
            break
    g["players"][reserve_uid] = reserve_name
    removed_name = g["players"].pop(target_uid, None)
    if removed_name:
        g["eliminated"][target_uid] = removed_name

    await callback.message.edit_text(f"🔄 جایگزینی انجام شد: {reserve_name} جایگزین {removed_name} شد.", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ بازگشت", callback_data=f"manage_{group_id}")))
    await callback.answer()


#=============================
# ورود به مدیریت بازی از پیوی
#=============================
@dp.callback_query_handler(lambda c: c.data == "manage_game")
async def manage_game(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    # پیدا کردن گروه‌هایی که این کاربر مدیر/گردان هست
    user_games = [
        gid for gid, g in games.items()
        if (g.get("moderator") == user_id) or (user_id in g.get("admins", set()))
    ]

    if not user_games:
        await callback.message.answer("❌ شما مدیر یا گردانندهٔ هیچ بازی فعالی نیستید.")
        await callback.answer()
        return

    if len(user_games) == 1:
        # فقط یک گروه: مستقیم نمایش منوی مدیریت همان گروه
        await show_manage_menu_private(callback, user_games[0])
    else:
        # چند گروه: نمایش لیست برای انتخاب
        kb = InlineKeyboardMarkup()
        for gid in user_games:
            kb.add(InlineKeyboardButton(f"🎲 گروه {gid}", callback_data=f"select_group_{gid}"))
        kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="back_to_menu"))
        await callback.message.edit_text("📋 گروه مورد نظر را انتخاب کنید:", reply_markup=kb)
    await callback.answer()


#=======================
# هندلر انتخاب گروه
#=======================
@dp.callback_query_handler(lambda c: c.data.startswith("select_group_"))
async def select_group(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    if group_id not in games:
        await callback.message.reply("❌ این گروه دیگر بازی فعالی ندارد.")
        return

    await show_manage_menu(callback.message, group_id, user_id)


#=======================
# تابع مدیریت گروه
#=======================
async def show_manage_menu_private(callback_or_message, group_id):
    """
    callback_or_message: می‌تواند یک CallbackQuery (معمولاً callback) یا Message باشد
    این تابع منوی مدیریت را در پیوی کاربر نمایش می‌دهد (edit_text اگر callback باشد، و reply اگر message باشد).
    """
    # تشخیص کاربر و داده‌ها
    if isinstance(callback_or_message, types.CallbackQuery):
        user_id = callback_or_message.from_user.id
        target = callback_or_message.message
    else:
        user_id = callback_or_message.from_user.id
        target = callback_or_message

    g = games.get(group_id)
    if not g:
        await target.reply("❌ بازی‌ای برای این گروه فعال نیست.")
        return

    # ساخت کیبورد بر اساس نقش (گرداننده یا مدیر)
    kb = InlineKeyboardMarkup(row_width=2)
    # گزینه‌هایی که همهٔ مدیرها باید ببینند
    kb.add(InlineKeyboardButton("🔄 جایگزین", callback_data=f"replace_{group_id}"))
    kb.add(InlineKeyboardButton("🛑 لغو بازی", callback_data=f"cancel_{group_id}"))

    # فقط گرداننده گزینه‌های تکمیلی را می‌بیند
    if user_id == g.get("moderator"):
        kb.add(InlineKeyboardButton("❌ حذف بازیکن", callback_data=f"remove_{group_id}"))
        kb.add(InlineKeyboardButton("🎂 تولد بازیکن", callback_data=f"revive_{group_id}"))
        kb.add(InlineKeyboardButton("🔇 سکوت بازیکن", callback_data=f"mute_{group_id}"))
        kb.add(InlineKeyboardButton("🔊 حذف سکوت", callback_data=f"unmute_{group_id}"))
        kb.add(InlineKeyboardButton("⚔ وضعیت چالش", callback_data=f"challenge_{group_id}"))
        kb.add(InlineKeyboardButton("📜 لیست نقش‌ها", callback_data=f"roles_{group_id}"))
        kb.add(InlineKeyboardButton("📩 ارسال دوباره نقش‌ها", callback_data=f"resend_roles_{group_id}"))

    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="back_to_menu"))

    # نمایش یا ویرایش در پیوی
    if isinstance(callback_or_message, types.CallbackQuery):
        await callback_or_message.message.edit_text("🛠 منوی مدیریت بازی:", reply_markup=kb)
    else:
        await callback_or_message.reply("🛠 منوی مدیریت بازی:", reply_markup=kb)

#=======================
# تابع بازگشت به منو
#=======================
@dp.callback_query_handler(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    # منوی پیوی اصلی
    await callback.message.edit_text("📋 منوی ربات:", reply_markup=main_menu_keyboard_private(callback.from_user.id))
    await callback.answer()
    
#============================
# تایع ساخت منو پیوی
#============================
def main_menu_keyboard_private(user_id: int):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game"))
    kb.add(InlineKeyboardButton("🛠 مدیریت بازی", callback_data="manage_game"))
    kb.add(InlineKeyboardButton("⚙ مدیریت سناریو", callback_data="manage_scenario"))
    kb.add(InlineKeyboardButton("📚 راهنما", callback_data="help"))
    return kb

#============================
# تست حذف
#============================
@dp.callback_query_handler(lambda c: c.data.startswith("do_remove_"))
async def do_remove_player(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    # do_remove_{group_id}_{user_id}
    if len(parts) < 3:
        await callback.answer("❌ دادهٔ نامعتبر.", show_alert=True)
        return
    group_id = int(parts[1])
    user_id = int(parts[2])

    g = get_game(group_id)
    if not g:
        await callback.answer("❌ بازی پیدا نشد.", show_alert=True)
        return

    # فقط گرداننده اجازه حذف دارد
    if callback.from_user.id != g.get("moderator"):
        await callback.answer("❌ فقط گرداننده می‌تواند حذف کند.", show_alert=True)
        return

    # انجام حذف (استفاده از helper)
    name = g["players"].get(user_id)
    remove_player_from_game(group_id, user_id)
    g["eliminated"][user_id] = name or "نام‌ناشناخته"

    await callback.message.edit_text(f"❌ بازیکن {name} حذف شد.", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ بازگشت", callback_data=f"manage_{group_id}")))
    await callback.answer()


#=======================
# لغو بازی
#=======================
@dp.callback_query_handler(lambda c: c.data.startswith("cancel_"))
async def cancel_game(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_", 1)[1])
    if group_id in games:
        # فقط مدیران/گرداننده بتوانند لغو کنند
        user_id = callback.from_user.id
        if (user_id != games[group_id].get("moderator")) and (user_id not in games[group_id].get("admins", set())):
            await callback.answer("❌ شما اجازه لغو این بازی را ندارید.", show_alert=True)
            return

        # پاکسازی داده‌ها (یا هر عملیات تکمیلی که نیاز داری)
        del games[group_id]
        await callback.message.edit_text("🗑 بازی لغو شد و اطلاعات پاک گردید.")
    else:
        await callback.answer("❌ بازی‌ای یافت نشد.", show_alert=True)
    await callback.answer()


# ======================
# مدیریت سناریو
# ======================
@dp.callback_query_handler(lambda c: c.data == "manage_scenarios")
async def manage_scenarios(callback: types.CallbackQuery):
    if callback.from_user.id not in admins:
        await callback.answer("❌ فقط ادمین‌ها می‌توانند مدیریت سناریو کنند.", show_alert=True)
        return
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("➕ افزودن سناریو", callback_data="add_scenario"),
        InlineKeyboardButton("➖ حذف سناریو", callback_data="remove_scenario"),
        InlineKeyboardButton("⬅ بازگشت", callback_data="back_main")
    )
    await callback.message.edit_text("⚙ مدیریت سناریو:", reply_markup=kb)

# افزودن سناریو
@dp.callback_query_handler(lambda c: c.data == "add_scenario")
async def add_scenario(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "➕ برای افزودن سناریو جدید، فایل <b>scenarios.json</b> را ویرایش کنید و ربات را ری‌استارت کنید.",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅ بازگشت", callback_data="manage_scenarios"))
    )
    await callback.answer()

# حذف سناریو
@dp.callback_query_handler(lambda c: c.data == "remove_scenario")
async def remove_scenario(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(f"❌ {scen}", callback_data=f"delete_scen_{scen}"))
    kb.add(InlineKeyboardButton("⬅ بازگشت", callback_data="manage_scenarios"))
    await callback.message.edit_text("یک سناریو را برای حذف انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("delete_scen_"))
async def delete_scenario(callback: types.CallbackQuery):
    scen = callback.data.replace("delete_scen_", "")
    if scen in scenarios:
        scenarios.pop(scen)
        save_scenarios()
        await callback.message.edit_text(f"✅ سناریو «{scen}» حذف شد.", reply_markup=main_menu_keyboard())
    else:
        await callback.answer("⚠ این سناریو وجود ندارد.", show_alert=True)


@dp.callback_query_handler(lambda c: c.data == "help")
async def show_help(callback: types.CallbackQuery):
    try:
        with open("help.txt", "r", encoding="utf-8") as f:
            help_text = f.read()
    except FileNotFoundError:
        help_text = "⚠ فایل help.txt پیدا نشد."
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅ بازگشت", callback_data="back_main"))
    await callback.message.edit_text(help_text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_main(callback: types.CallbackQuery):
    await callback.message.edit_text("🏠 منوی اصلی:", reply_markup=main_menu_keyboard())

# ======================
# انتخاب سناریو و گرداننده
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("choose_scenario"))
async def choose_scenario(callback: types.CallbackQuery):
    group_id = callback.message.chat.id if callback.message.chat.type in ["group", "supergroup"] else None
    if not group_id:
        await callback.answer("❌ این گزینه فقط در گروه فعال است.", show_alert=True)
        return

    game = ensure_game_entry(group_id)
    if not game["lobby_active"]:
        await callback.answer("⚠️ هیچ بازی فعالی برای انتخاب سناریو وجود ندارد.", show_alert=True)
        return

    # اگر هنوز هیچ سناریویی ثبت نشده
    if not game["scenarios"]:
        await callback.answer("⚠️ هیچ سناریویی تعریف نشده است.", show_alert=True)
        return

    # ساخت دکمه‌ها
    kb = InlineKeyboardMarkup()
    for scen_id, scen_name in game["scenarios"].items():
        kb.add(InlineKeyboardButton(scen_name, callback_data=f"set_scenario_{group_id}_{scen_id}"))

    await callback.message.edit_text("📜 یک سناریو انتخاب کنید:", reply_markup=kb)
    
@dp.callback_query_handler(lambda c: c.data.startswith("select_scenario_"))
async def select_scenario(callback: types.CallbackQuery):
    group_id = callback.message.chat.id
    game = ensure_game_entry(group_id)

    scenario_key = callback.data.split("select_scenario_")[1]
    scenario = game["scenarios"].get(scenario_key)

    if not scenario:
        await callback.answer("⚠️ سناریو نامعتبر است.", show_alert=True)
        return

    game["selected_scenario"] = scenario_key

    await callback.message.edit_text(f"✅ سناریوی انتخابی: {scenario['name']}")
    await callback.answer()

#====================================================
@dp.callback_query_handler(lambda c: c.data.startswith("choose_moderator"))
async def choose_moderator(callback: types.CallbackQuery):
    group_id = callback.message.chat.id if callback.message.chat.type in ["group", "supergroup"] else None
    if not group_id:
        await callback.answer("❌ این گزینه فقط در گروه فعال است.", show_alert=True)
        return

    game = ensure_game_entry(group_id)
    if not game["lobby_active"]:
        await callback.answer("⚠️ هیچ بازی فعالی برای انتخاب گرداننده وجود ندارد.", show_alert=True)
        return

    # گرفتن ادمین‌ها
    admins = {member.user.id: member.user.first_name for member in await bot.get_chat_administrators(group_id)}
    game["admins"] = set(admins.keys())

    kb = InlineKeyboardMarkup()
    for uid, name in admins.items():
        kb.add(InlineKeyboardButton(name, callback_data=f"set_moderator_{group_id}_{uid}"))

    await callback.message.edit_text("👤 یک گرداننده انتخاب کنید:", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data.startswith("set_moderator_"))
async def set_moderator(callback: types.CallbackQuery):
    parts = callback.data.split("_", 3)  # set_moderator_groupid_userid
    if len(parts) < 4:
        await callback.answer("❌ داده نامعتبر.", show_alert=True)
        return

    group_id = int(parts[2])
    user_id = int(parts[3])

    game = ensure_game_entry(group_id)

    if user_id not in game["admins"]:
        await callback.answer("❌ این کاربر مدیر گروه نیست.", show_alert=True)
        return
        
    member = await bot.get_chat_member(group_id, user_id)
    moderator_name = member.user.full_name
    # ذخیره در games
    game["moderator"] = user_id
    # sync به globals
    sync_globals_from_game(group_id)

    await callback.answer("✅ گرداننده انتخاب شد.", show_alert=True)
    await callback.message.edit_text(f"👤 گرداننده بازی: {moderator_name}")




# ======================
# ورود و انصراف
# ======================
@dp.callback_query_handler(lambda c: c.data == "join_game")
async def join_game_callback(callback: types.CallbackQuery):
    user = callback.from_user
    print("✅ ورود به بازی کلیک شد!")

    # جلوگیری از ورود در حین بازی
    if game_running:
        await callback.answer("❌ بازی در جریان است. نمی‌توانید وارد شوید.", show_alert=True)
        return

    # جلوگیری از ورود گرداننده
    #if user.id == moderator_id:
        #await callback.answer("❌ گرداننده نمی‌تواند وارد بازی شود.", show_alert=True)
        #return

    # جلوگیری از ورود دوباره بازیکن
    if user.id in players:
        await callback.answer("❌ شما در لیست بازی هستید!", show_alert=True)
        return

    players[user.id] = user.full_name
    await callback.answer("✅ شما به بازی اضافه شدید!")
    await update_lobby()

@dp.callback_query_handler(lambda c: c.data == "leave_game")
async def leave_game_callback(callback: types.CallbackQuery):
    global players, player_slots
    user = callback.from_user

    # جلوگیری از خروج در حین بازی
    if game_running:
        await callback.answer("❌ بازی در جریان است. نمی‌توانید خارج شوید.", show_alert=True)
        return

    if user.id not in players:
        await callback.answer("❌ شما در لیست بازی نیستید!", show_alert=True)
        return
    del players[user.id]
    players.pop(user.id)

    # آزاد کردن صندلی اگر انتخاب کرده بود
    for slot, uid in list(player_slots.items()):
        if uid == user.id:
            del player_slots[slot]

    await callback.answer("✅ شما از بازی خارج شدید!")
    await update_lobby()

# ======================
# بروزرسانی لابی
# ======================
async def update_lobby():
    global lobby_message_id
    if not group_chat_id or not lobby_message_id:
        return


    # ساخت متن لابی
    text = f"📋 **لیست بازی:**\n"
    text += f"سناریو: {selected_scenario or 'انتخاب نشده'}\n"
    text += f"گرداننده: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name if moderator_id else 'انتخاب نشده'}\n\n"

    if moderator_id:
        try:
            moderator = await bot.get_chat_member(group_chat_id, moderator_id)
            text += f"گرداننده: {html.escape(moderator.user.full_name)}\n\n"
        except Exception:
            text += "گرداننده: انتخاب نشده\n\n"
    else:
        text += "گرداننده: انتخاب نشده\n\n"
        
    if players:
        for uid, name in players.items():
            seat = next((s for s, u in player_slots.items() if u == uid), None)
            seat_str = f" (صندلی {seat})" if seat else ""
            text += f"- <a href='tg://user?id={uid}'>{html.escape(name)}</a>{seat_str}\n"
    else:
        text += "هیچ بازیکنی وارد بازی نشده است.\n"        


    kb = InlineKeyboardMarkup(row_width=5)

    # ✅ دکمه‌های انتخاب صندلی
    if selected_scenario:
        max_players = len(scenarios[selected_scenario]["roles"])
        for i in range(1, max_players + 1):
            if i in player_slots:
                player_name = players.get(player_slots[i], "❓")
                kb.insert(InlineKeyboardButton(f"{i} ({player_name})", callback_data=f"slot_{i}"))
            else:
                kb.insert(InlineKeyboardButton(str(i), callback_data=f"slot_{i}"))

    # ✅ دکمه ورود/خروج
    kb.row(
        InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"),
        InlineKeyboardButton("❌ خروج از بازی", callback_data="leave_game"),
    )

    # ✅ دکمه لغو بازی فقط برای مدیران
    if moderator_id and moderator_id in admins:
        kb.add(InlineKeyboardButton("🚫 لغو بازی", callback_data="cancel_game"))
        

    # ✅ دکمه شروع بازی در صورت کافی بودن بازیکنان
    if selected_scenario and moderator_id:
        min_players = scenarios[selected_scenario]["min_players"]
        max_players = len(scenarios[selected_scenario]["roles"])
        if min_players <= len(players) <= max_players:
            kb.add(InlineKeyboardButton("🎭 پخش نقش", callback_data="distribute_roles"))
        elif len(players) > max_players:
            text += "\n⚠️ تعداد بازیکنان بیش از ظرفیت این سناریو است."
    

    # 🔄 بروزرسانی پیام لابی
    if lobby_message_id:
        try:
            await bot.edit_message_text(text, chat_id=group_chat_id, message_id=lobby_message_id, reply_markup=kb, parse_mode="HTML")
        except Exception:
            # اگر ویرایش نشد، یک پیام جدید ارسال شود
            msg = await bot.send_message(group_chat_id, text, reply_markup=kb, parse_mode="HTML")
            lobby_message_id = msg.message_id
    else:
        try:
            msg = await bot.send_message(group_chat_id, text, reply_markup=kb, parse_mode="HTML")
            lobby_message_id = msg.message_id

        except Exception as e:
            logging.exception("⚠️ Failed to edit lobby, sending new message")
            msg = await bot.send_message(group_chat_id, text, reply_markup=kb, parse_mode="HTML")
            lobby_message_id = msg.message_id


# ======================
# لغو بازی توسط مدیران
# ======================
@dp.callback_query_handler(lambda c: c.data == "cancel_game")
async def cancel_game(callback: types.CallbackQuery):
    if callback.from_user.id not in admins:
        await callback.answer("❌ فقط مدیران می‌توانند بازی را لغو کنند.", show_alert=True)
        return
    
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ تایید", callback_data="confirm_cancel"),
        InlineKeyboardButton("↩ بازگشت", callback_data="back_to_lobby"),
    )
    await callback.message.edit_text("آیا مطمئنید که می‌خواهید بازی را لغو کنید؟", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "confirm_cancel")
async def confirm_cancel(callback: types.CallbackQuery):
    global players, player_slots, game_running, selected_scenario, moderator_id, lobby_message_id
    players.clear()
    player_slots.clear()
    game_running = False
    selected_scenario = None
    moderator_id = None
    lobby_message_id = None
    # ریست کردن متغیرهای چالش
    pending_challenges.clear()
    challenge_mode = False
    paused_main_player = None
    paused_main_duration = None

    # یک بار ویرایش کن
    msg = await callback.message.edit_text("🚫 بازی لغو شد.")
    await callback.answer()

    # بعد ۵ ثانیه پاکش کن
    await asyncio.sleep(5)
    try:
        await bot.delete_message(callback.message.chat.id, msg.message_id)
    except:
        pass



@dp.callback_query_handler(lambda c: c.data == "back_to_lobby")
async def back_to_lobby(callback: types.CallbackQuery):
    await update_lobby()
    await callback.answer()

#======================
# تابع کمکی برای پخش نقش‌ها
#======================
@dp.callback_query_handler(lambda c: c.data == "distribute_roles")
async def distribute_roles_callback(callback: types.CallbackQuery):
    global game_message_id, lobby_message_id, game_running

    # فقط گرداننده اجازه دارد
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند نقش‌ها را پخش کند.", show_alert=True)
        return

    if not selected_scenario:
        await callback.answer("❌ سناریو انتخاب نشده.", show_alert=True)
        return

    try:
        mapping = await distribute_roles()
    except Exception as e:
        logging.exception("⚠️ مشکل در پخش نقش‌ها: %s", e)
        await callback.answer("❌ خطا در پخش نقش‌ها.", show_alert=True)
        return

    # نمایش خلاصه در گروه و تبديل پیام لابی به پیام بازی (game_message_id)
    seats = {seat: (uid, players.get(uid, "❓")) for seat, uid in player_slots.items()}
    players_list = "\n".join([f"{seat}. <a href='tg://user?id={uid}'>{html.escape(name)}</a>" for seat, (uid, name) in sorted(seats.items())])

    text = (
        "🎭 نقش‌ها پخش شد!\n\n"
        f"👥 لیست بازیکنان:\n{players_list}\n\n"
        "ℹ️ برای دیدن نقش به پیوی ربات بروید.\n"
        "👑 گرداننده سر صحبت را انتخاب کند تا بازی شروع شود."
    )

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👑 انتخاب سر صحبت", callback_data="choose_head"))
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))
    
    if challenge_active:
        kb.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        kb.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))

    try:
        if lobby_message_id:
            msg = await bot.edit_message_text(text, chat_id=group_chat_id, message_id=lobby_message_id, parse_mode="HTML", reply_markup=kb)
            game_message_id = msg.message_id
            # اگر می‌خواهی بعد از پخش نقش پیام لابی را نداشته باشی می‌توانی lobby_message_id = None کنی
        else:
            msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
            game_message_id = msg.message_id
    except Exception as e:
        logging.warning("⚠️ distribute_roles: edit failed, sending new message: %s", e)
        msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
        game_message_id = msg.message_id

    game_running = True
    await callback.answer("✅ نقش‌ها پخش شد!")



async def distribute_roles():
    """
    نقش‌ها را به پیوی بازیکنان می‌فرستد و mapping از user_id -> role برمی‌گرداند.
    ترتیب اختصاص نقش: اگر صندلی رزرو شده باشد بر اساس شماره صندلی، در غیر اینصورت بر اساس insertion-order players.
    """
    if not selected_scenario:
        raise ValueError("سناریو انتخاب نشده")

    roles_template = scenarios[selected_scenario]["roles"]
    # ترتیب بازیکنان: بر اساس صندلی اگر موجود باشد، وگرنه بر اساس players.keys()
    if player_slots:
        player_ids = [player_slots[s] for s in sorted(player_slots.keys())]
    else:
        player_ids = list(players.keys())

    # آماده سازی لیست نقش‌ها مطابق تعداد بازیکنان
    roles = list(roles_template)  # کپی
    if len(player_ids) > len(roles):
        # اگر نیاز به نقش بیشتر هست، بقیه را "شهروند" قرار می‌دهیم
        roles += ["شهروند"] * (len(player_ids) - len(roles))
    # اگر نقش‌ها بیشتر از بازیکنان بود، کافی است کوتاهش کنیم
    roles = roles[:len(player_ids)]

    random.shuffle(roles)

    mapping = {}
    for pid, role in zip(player_ids, roles):
        mapping[pid] = role
        try:
            await bot.send_message(pid, f"🎭 نقش شما: {html.escape(str(role))}")
        except Exception as e:
            # به گرداننده اطلاع بده که ارسال به یکی از بازیکنان شکست خورد
            logging.warning("⚠️ ارسال نقش به %s شکست خورد: %s", pid, e)
            if moderator_id:
                try:
                    await bot.send_message(moderator_id, f"⚠ نمی‌توانم نقش را به {players.get(pid, pid)} ارسال کنم.")
                except:
                    pass

    # ارسال لیست نقش‌ها به گرداننده (اگر وجود داشته باشد)
    if moderator_id:
        text = "📜 لیست نقش‌ها:\n"
        for pid, role in mapping.items():
            text += f"{players.get(pid,'❓')} → {role}\n"
        try:
            await bot.send_message(moderator_id, text)
        except Exception:
            pass

    return mapping
#==================
# شروع راند
#==================
@dp.callback_query_handler(lambda c: c.data == "start_round")
async def start_round_handler(callback: types.CallbackQuery):
    global turn_order, current_turn_index, round_active

    if not turn_order:
        seats_list = sorted(player_slots.keys())
        if not seats_list:
            await callback.answer("⚠️ هیچ بازیکنی در بازی نیست.", show_alert=True)
            return
        turn_order = seats_list[:]  # همه بازیکن‌ها به ترتیب صندلی

    round_active = True
    current_turn_index = 0  # شروع از سر صحبت

    first_seat = turn_order[current_turn_index]  # صندلی یا آی‌دی بازیکن اول
    await start_turn(first_seat, duration=DEFAULT_TURN_DURATION, is_challenge=False)
    await callback.answer()

#======================
# تابع کمکی برای ساخت / بروزرسانی پیام گروه (پیام «بازی شروع شد»
#======================

async def render_game_message(edit=True):
    """
    نمایش یا ویرایش پیام 'بازی شروع شد' در گروه بر اساس player_slots (صندلی‌ها).
    اگر edit==True سعی می‌کنیم پیام قبلی را ویرایش کنیم، در غیر اینصورت پیام جدید می‌فرستیم.
    """
    global game_message_id

    if not group_chat_id:
        return

    # لیست بازیکنان بر اساس صندلی مرتب
    max_players = len(scenarios[selected_scenario]["roles"])
    lines = []
    for seat in range(1, max_players+1):
        if seat in player_slots:
            uid = player_slots[seat]
            name = players.get(uid, "❓")
            lines.append(f"{seat}. <a href='tg://user?id={uid}'>{html.escape(name)}</a>")
    players_list = "\n".join(lines) if lines else "هیچ بازیکنی ثبت نشده است."

    head_text = ""
    if current_head_seat:
        head_uid = player_slots.get(current_head_seat)
        head_name = players.get(head_uid, "❓")
        head_text = f"\n\nسر صحبت: صندلی {current_head_seat} - <a href='tg://user?id={head_uid}'>{html.escape(head_name)}</a>"

    text = (
        "🎮 بازی شروع شد!\n"
        "📩 نقش‌ها در پیوی ارسال شدند.\n\n"
        f"لیست بازیکنان حاضر (بر اساس صندلی):\n{players_list}\n\n"
        "ℹ️ برای دیدن نقش به پیوی ربات برید\n"
        "📜 لیست نقش‌ها برای گرداننده ارسال شد"
        f"{head_text}\n\n"
        "🎤 گرداننده باید «سر صحبت» را انتخاب کند و سپس «شروع دور» را بزند."
    )

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎯 انتخاب سر صحبت", callback_data="choose_head"))
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))
    
    if challenge_active:
        kb.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        kb.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))
    

    try:
        if edit and game_message_id:
            await bot.edit_message_text(text, chat_id=group_chat_id, message_id=game_message_id,
                                        parse_mode="HTML", reply_markup=kb)
        else:
            msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
            game_message_id = msg.message_id
    except Exception:
        # اگر ویرایش شکست خورد، پیام جدید بفرست و id را ذخیره کن
        msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
        game_message_id = msg.message_id


# ======================
# شروع بازی و نوبت اول
# ======================

@dp.callback_query_handler(lambda c: c.data == "start_play")
async def start_play(callback: types.CallbackQuery):
    global game_running, lobby_active, turn_order, current_turn_index, game_message_id

    # فقط گرداننده می‌تواند شروع کند
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
        return

    if not selected_scenario:
        await callback.answer("❌ سناریو انتخاب نشده.", show_alert=True)
        return

    max_players = len(scenarios[selected_scenario]["roles"])
    # اطمینان از اینکه صندلی‌ها حداقل به اندازه حداقل بازیکنان پر شده‌اند
    occupied_seats = [s for s in range(1, max_players+1) if s in player_slots]
    if len(occupied_seats) < scenarios[selected_scenario]["min_players"]:
        await callback.answer(f"❌ تعداد بازیکنان کافی نیست. حداقل {scenarios[selected_scenario]['min_players']} صندلی باید انتخاب شود.", show_alert=True)
        return

    # یا اگر خواستی می‌تونی اصرار کنی که همهٔ بازیکنان صندلی انتخاب کنند:
    if len(occupied_seats) != len(players):
        await callback.answer("❌ لطفا همه بازیکنان ابتدا صندلی انتخاب کنند تا لیست مرتب بر اساس صندلی ساخته شود.", show_alert=True)
        return

    game_running = True
    lobby_active = False

    # پخش نقش‌ها
    await distribute_roles()
    
        # ✅ اضافه شده
    # ساخت متن لیست بازیکنان بر اساس صندلی‌ها
    seats = {seat: (uid, players[uid]) for seat, uid in player_slots.items()}
    players_list = "\n".join(
        [f"{seat}. <a href='tg://user?id={uid}'>{name}</a>" for seat, (uid, name) in seats.items()]
    )

    text = (
        "🎮 بازی شروع شد!\n"
        "📩 نقش‌ها در پیوی ارسال شدند.\n\n"
        f"👥 لیست بازیکنان حاضر در بازی:\n{players_list}\n\n"
        "ℹ️ برای دیدن نقش به پیوی ربات بروید.\n"
        "📜 لیست نقش‌ها به گرداننده ارسال شد.\n\n"
        "👑 گرداننده سر صحبت را انتخاب کند و شروع دور را بزند."
    )

    # کیبورد جدید (انتخاب سر صحبت + شروع دور)
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("👑 انتخاب سر صحبت", callback_data="choose_head"),
        InlineKeyboardButton("▶ شروع دور", callback_data="start_round")
    )
    if challenge_active:
        kb.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        kb.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))
    
    # ویرایش پیام لابی به پیام شروع بازی
    try:
        if lobby_message_id:
            await bot.edit_message_text(
                chat_id=group_chat_id,
                message_id=lobby_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
            lobby_message_id = msg.message_id
    except Exception as e:
        print("❌ خطا در ویرایش پیام لابی:", e)
        
    await callback.answer("✅ بازی شروع شد و نقش‌ها پخش شد!")
#==================================
#منو انتخاب سر صحبت (نمایش گزینه خودکار/دستی)
#==================================
@dp.callback_query_handler(lambda c: c.data == "choose_head")
async def choose_head(callback: types.CallbackQuery):
    global game_message_id

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند این کار را انجام دهد.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎲 انتخاب خودکار", callback_data="speaker_auto"),
        InlineKeyboardButton("✋ انتخاب دستی", callback_data="speaker_manual")
    )

    text = "🔧 روش انتخاب سر صحبت را انتخاب کنید:"

    try:
        # تلاش برای ویرایش پیام قبلی
        await bot.edit_message_text(
            text,
            chat_id=group_chat_id,
            message_id=game_message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.warning(f"⚠️ خطا در نمایش منو: {e}")
        # اگر پیام قبلی قابل ویرایش نبود → پیام جدید بفرست
        msg = await bot.send_message(group_chat_id, text, reply_markup=kb)
        game_message_id = msg.message_id  # بروزرسانی آیدی پیام جدید

    await callback.answer()

#=======================================
# انتخاب خودکار → نمایش لیست صندلی‌ها با دکمه برای انتخاب
#=======================================

@dp.callback_query_handler(lambda c: c.data == "speaker_auto")
async def speaker_auto(callback: types.CallbackQuery):
    import random
    global current_speaker, turn_order, current_turn_index

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند انتخاب کند.", show_alert=True)
        return

    if not player_slots:
        await callback.answer("⚠ هیچ صندلی ثبت نشده.", show_alert=True)
        return

    seats_list = sorted(player_slots.keys())
    current_speaker = random.choice(seats_list)
    current_turn_index = seats_list.index(current_speaker)

    # درست‌کردن ترتیب نوبت‌ها: همه از سر صحبت شروع بشن
    turn_order = seats_list[current_turn_index:] + seats_list[:current_turn_index]

    # اطمینان از اینکه سر صحبت در اول لیست هست
    if current_speaker in turn_order:
        turn_order.remove(current_speaker)
    turn_order.insert(0, current_speaker)

    await callback.answer(f"✅ صندلی {current_speaker} به صورت رندوم سر صحبت شد.")

    # بازگرداندن منوی بازی (انتخاب سر صحبت + شروع دور)
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👑 انتخاب سر صحبت", callback_data="choose_head"))
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))
    
    if challenge_active:
        kb.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        kb.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))

    try:
        await bot.edit_message_reply_markup(
            chat_id=group_chat_id,
            message_id=game_message_id,
            reply_markup=kb
        )
    except Exception:
        pass





#=======================================
# انتخاب دستی → نمایش لیست صندلی‌ها با دکمه برای انتخاب
#=======================================

@dp.callback_query_handler(lambda c: c.data == "speaker_manual")
async def speaker_manual(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند انتخاب کند.", show_alert=True)
        return

    if not player_slots:
        await callback.answer("⚠ هیچ صندلی ثبت نشده.", show_alert=True)
        return

    seats = {seat: (uid, players.get(uid, "❓")) for seat, uid in player_slots.items()}
    kb = InlineKeyboardMarkup(row_width=2)
    for seat, (uid, name) in sorted(seats.items()):
        kb.add(InlineKeyboardButton(f"{seat}. {html.escape(name)}", callback_data=f"head_set_{seat}"))

    try:
        await bot.edit_message_reply_markup(chat_id=group_chat_id, message_id=game_message_id, reply_markup=kb)
    except Exception:
        # اگر اصلا ویرایش نشد، ارسال پیام جدید با همین کیبورد
        try:
            msg = await bot.send_message(group_chat_id, "✋ یکی از بازیکنان را انتخاب کنید:", reply_markup=kb)
            game_message_id = msg.message_id
        except:
            pass

    await callback.answer()

#==========================
# هد ست
#==========================

@dp.callback_query_handler(lambda c: c.data.startswith("head_set_"))
async def head_set(callback: types.CallbackQuery):
    global current_speaker, turn_order, current_turn_index

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند انتخاب کند.", show_alert=True)
        return

    try:
        seat = int(callback.data.split("_")[2])
    except (IndexError, ValueError):
        await callback.answer("⚠ خطای داده صندلی.", show_alert=True)
        return

    if seat not in player_slots:
        await callback.answer("⚠ این صندلی رزرو نشده است.", show_alert=True)
        return

    # تنظیم سر صحبت
    current_speaker = seat
    seats_list = sorted(player_slots.keys())
    current_turn_index = seats_list.index(seat)
    turn_order = seats_list[current_turn_index:] + seats_list[:current_turn_index]

    await callback.answer(f"✅ صندلی {seat} به عنوان سر صحبت انتخاب شد.")

    # جاگذاری سر صحبت در اول لیست نوبت‌ها
    if seat in turn_order:
        turn_order.remove(seat)
    turn_order.insert(0, seat)

    # بازگشت به منوی اصلی
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👑 انتخاب سر صحبت", callback_data="choose_head"))
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))

    if challenge_active:
        kb.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        kb.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))

    await bot.edit_message_reply_markup(
        chat_id=group_chat_id,
        message_id=game_message_id,
        reply_markup=kb
    )


# ======================
# شروع بازی و نوبت اول
# ======================
async def start_turn(group_id, seat, duration=None, is_challenge=False):
    """
    شروع نوبت یک بازیکن در بازی مشخص (group_id)
    seat = شماره صندلی بازیکن
    duration = مدت نوبت (در صورت None شدن از مقدار پیش‌فرض گرفته می‌شود)
    is_challenge = آیا این نوبت به عنوان چالش اجرا می‌شود؟
    """
    game = games[group_id]  # دسترسی به داده‌های بازی

    # اطمینان از وجود بازیکن در صندلی
    if seat not in game["player_slots"]:
        logging.warning(f"Seat {seat} در بازی {group_id} بازیکن ندارد.")
        return

    player_id = game["player_slots"][seat]
    player_name = game["players"].get(player_id, "ناشناس")

    # مدت زمان نوبت
    if duration is None:
        duration = game["DEFAULT_TURN_DURATION"]

    # اگر تسک تایمر قبلی هنوز فعاله، متوقفش کن
    if game["turn_timer_task"]:
        game["turn_timer_task"].cancel()

    # ساخت پیام نوبت
    text = f"🎙 نوبت {player_name} (صندلی {seat})"
    if is_challenge:
        text += "\n⚔ این نوبت در حالت چالش است!"

    try:
        # اگر پیام نوبت قبلی وجود داره، ویرایشش کن
        if game["current_turn_message_id"]:
            await bot.edit_message_text(
                chat_id=group_id,
                message_id=game["current_turn_message_id"],
                text=text
            )
        else:
            # در غیر این صورت پیام جدید بفرست
            msg = await bot.send_message(group_id, text)
            game["current_turn_message_id"] = msg.message_id
    except Exception as e:
        logging.exception(f"خطا در ارسال پیام نوبت در گروه {group_id}: {e}")
        msg = await bot.send_message(group_id, text)
        game["current_turn_message_id"] = msg.message_id

    # راه‌اندازی تایمر برای پایان نوبت
    async def turn_timer():
        try:
            await asyncio.sleep(duration)
            # بعد از پایان زمان، نوبت بعدی رو شروع کن
            await advance_turn(group_id)
        except asyncio.CancelledError:
            pass

    game["turn_timer_task"] = asyncio.create_task(turn_timer())


# ======================
# هندلر دکمه شروع دور
# ======================
@dp.callback_query_handler(lambda c: c.data == "start_turn")
async def handle_start_turn(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند دور را شروع کند.", show_alert=True)
        return

    global current_turn_index
    if not turn_order:
        await callback.answer("⚠️ ترتیب نوبت‌ها مشخص نشده.", show_alert=True)
        return

    current_turn_index = 0
    first_seat = turn_order[current_turn_index]
    await start_turn(first_seat)

    await callback.answer()

#================
# چالش آف
#================
@dp.callback_query_handler(lambda c: c.data == "challenge_off")
async def challenge_off_handler(callback: types.CallbackQuery):
    global challenge_active
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند چالش را غیرفعال کند.", show_alert=True)
        return

    if not challenge_active:
        await callback.answer("⚔ چالش از قبل غیرفعال است.", show_alert=True)
        return

@dp.callback_query_handler(lambda c: c.data == "challenge_toggle")
async def challenge_toggle_handler(callback: types.CallbackQuery):
    global challenge_active

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند وضعیت چالش را تغییر دهد.", show_alert=True)
        return

    # اینجا: تغییر وضعیت
    challenge_active = not challenge_active
#=============================
# تایمر زندهٔ نوبت (ویرایش پیام هر N ثانیه)
#=============================
async def countdown(seat, duration, message_id, is_challenge=False):
    remaining = duration
    user_id = player_slots.get(seat)
    player_name = players.get(user_id, "بازیکن")
    mention = f"<a href='tg://user?id={user_id}'>{html.escape(str(player_name))}</a>"

    try:
        while remaining > 0:
            await asyncio.sleep(5)
            remaining -= 5
            new_text = f"⏳ {max(0, remaining)//60:02d}:{max(0, remaining)%60:02d}\n🎙 نوبت صحبت {mention} است. ({max(0, remaining)} ثانیه)"
            try:
                await bot.edit_message_text(new_text, chat_id=group_chat_id, message_id=message_id,
                                            parse_mode="HTML", reply_markup=turn_keyboard(seat, is_challenge))
            except:
                pass
        # پایان زمان → پیام موقتی
        await send_temp_message(group_chat_id, f"⏳ زمان {mention} به پایان رسید.", delay=5)
    except asyncio.CancelledError:
        return


# ======================
# نکست نوبت
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("next_"))
async def next_turn(callback: types.CallbackQuery):
    global current_turn_index, challenge_mode
    global paused_main_player, paused_main_duration, post_challenge_advance

    try:
        seat = int(callback.data.split("_", 1)[1])
    except Exception:
        await bot.send_message(group_chat_id, "⚠️ دادهٔ نادرست برای نکست.")
        return

    player_uid = player_slots.get(seat)
    if callback.from_user.id != moderator_id and callback.from_user.id != player_uid:
        await callback.answer("❌ فقط بازیکن مربوطه یا گرداننده می‌تواند نوبت را پایان دهد.", show_alert=True)
        return

    # لغو تایمر
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # =========================
    #  حالت "چالش"
    # =========================
    if challenge_mode:
        challenge_mode = False

        if paused_main_player is not None:
            if post_challenge_advance:
                # بعد از چالش → برو نفر بعد از main
                post_challenge_advance = False
                current_turn_index += 1

            # پاکسازی وضعیت
            paused_main_player = None
            paused_main_duration = None

    # =========================
    #  حالت "نوبت عادی"
    # =========================
    else:
        # بررسی کنیم آیا برای این بازیکن چالش رزرو شده؟
        if seat in pending_challenges:
            challenger_id = pending_challenges.pop(seat)
            challenger_seat = next((s for s, u in player_slots.items() if u == challenger_id), None)
            if challenger_seat:
                # ذخیره نوبت اصلی
                paused_main_player = seat
                paused_main_duration = 120  # یا زمان واقعی نوبت اصلی
                post_challenge_advance = True
                challenge_mode = True

                # شروع چالش
                await start_turn(challenger_seat, duration=60, is_challenge=True)
                return

        # اگر چالشی نبود → برو نفر بعدی
        current_turn_index += 1

    # =========================
    #  پایان روز یا ادامه نوبت
    # =========================
    if current_turn_index >= len(turn_order):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🌙 شروع فاز شب", callback_data="start_night"))
        await bot.send_message(group_chat_id, "✅ همه بازیکنان صحبت کردند. فاز روز پایان یافت.", reply_markup=kb)
    else:
        next_seat = turn_order[current_turn_index]
        await start_turn(next_seat)


#========================
# شب کردن
#========================
@dp.callback_query_handler(lambda c: c.data == "start_night")
async def start_night(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند فاز شب را شروع کند.", show_alert=True)
        return

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🌞 شروع روز جدید", callback_data="start_new_day"))

    await bot.send_message(group_chat_id, "🌙 فاز شب شروع شد. بازیکنان ساکت باشند...", reply_markup=kb)
    await callback.answer()


#===========================
# روز کردن و ریست دور قبل
#===========================
@dp.callback_query_handler(lambda c: c.data == "start_new_day")
async def start_new_day(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند روز جدید را شروع کند.", show_alert=True)
        return

    # ریست تمام داده‌های دور قبلی
    reset_round_data()

    # دکمه‌ها
    keyboard = InlineKeyboardMarkup()

    keyboard.add(
        InlineKeyboardButton("🗣 انتخاب سر صحبت", callback_data="choose_head"),
    )

    # دکمه وضعیت چالش
    if challenge_active:
        keyboard.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        keyboard.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))

    keyboard.add(
        InlineKeyboardButton("▶️ شروع دور", callback_data="start_turn")
    )


    # ویرایش پیام فعلی
    await callback.message.edit_text("🌞 روز جدید شروع شد! سر صحبت را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()




#=======================
# درخواست چالش
#=======================
@dp.callback_query_handler(lambda c: c.data.startswith(("challenge_before_", "challenge_after_", "challenge_none_")))
async def challenge_choice(callback: types.CallbackQuery):
    global paused_main_player, paused_main_duration

    parts = callback.data.split("_")
    action = parts[1]     # before / after / none
    challenger_id = int(parts[2])
    target_id = int(parts[3])

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    if callback.from_user.id not in [challenger_id, moderator_id]:
        await callback.answer("❌ فقط چالش‌کننده یا گرداننده می‌تواند این گزینه را انتخاب کند.", show_alert=True)
        return

    if action == "before":
        paused_main_player = target_seat
        paused_main_duration = DEFAULT_TURN_DURATION

        if turn_timer_task and not turn_timer_task.done():
            turn_timer_task.cancel()

        challenger_seat = next((s for s,u in player_slots.items() if u == challenger_id), None)
        if challenger_seat is None:
            await bot.send_message(group_chat_id, "⚠️ چالش‌کننده صندلی ندارد؛ نمی‌توان چالش را اجرا کرد.")
        else:
            await bot.send_message(group_chat_id, f"⚔ چالش قبل صحبت برای {target_name} توسط {challenger_name} اجرا شد.")
            await start_turn(challenger_seat, duration=60, is_challenge=True)

    elif action == "after":
        target_seat = next((s for s,u in player_slots.items() if u == target_id), None)
        if target_seat is None:
            await bot.send_message(group_chat_id, "⚠️ هدف چالش صندلی ندارد؛ نمی‌توان چالش را ثبت کرد.")
        else:
            pending_challenges[target_seat] = challenger_id
            await bot.send_message(group_chat_id, f"⚔ چالش بعد صحبت برای {target_name} ثبت شد (چالش‌کننده: {challenger_name}).")

    elif action == "none":
        await bot.send_message(group_chat_id, f"🚫 {challenger_name} از ارسال چالش منصرف شد.")

    await callback.answer()
    
# ======================
# درخواست چالش (باز کردن منوی انتخاب قبل/بعد/انصراف)
# ======================
challenge_requests = {}

@dp.callback_query_handler(lambda c: c.data.startswith("challenge_request_"))
async def challenge_request(callback: types.CallbackQuery):
    challenger_id = callback.from_user.id
    try:
        target_seat = int(callback.data.split("_", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("⚠️ خطا در داده چالش.", show_alert=True)
        return

    target_id = player_slots.get(target_seat)
    if not target_id:
        await callback.answer("⚠️ بازیکن یافت نشد.", show_alert=True)
        return

    # نمی‌تونی به خودت درخواست بدی
    if challenger_id == target_id:
        await callback.answer("❌ نمی‌توانی به خودت درخواست چالش بدهی.", show_alert=True)
        return

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    # ثبت درخواست جدید
    if target_seat not in challenge_requests:
        challenge_requests[target_seat] = {}
    if challenger_id in challenge_requests[target_seat]:
        await callback.answer("❌ در این نوبت قبلاً درخواست داده‌ای.", show_alert=True)
        return

    challenge_requests[target_seat][challenger_id] = "pending"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ قبول (قبل)", callback_data=f"accept_before_{challenger_id}_{target_id}"),
        InlineKeyboardButton("✅ قبول (بعد)", callback_data=f"accept_after_{challenger_id}_{target_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"reject_{challenger_id}_{target_id}")
    )

    await bot.send_message(group_chat_id, f"⚔ {challenger_name} از {target_name} درخواست چالش کرد.", reply_markup=kb)
    await callback.answer("⏳ درخواست ارسال شد.", show_alert=True)


#=======================
# پذیرش/رد چالش
#=======================
@dp.callback_query_handler(lambda c: c.data.startswith(("accept_before_", "accept_after_", "reject_")))
async def handle_challenge_response(callback: types.CallbackQuery):
    global paused_main_player, paused_main_duration, challenge_mode, post_challenge_advance

    parts = callback.data.split("_")
    action = parts[0]      # accept / reject
    timing = parts[1] if action == "accept" else None
    challenger_id = int(parts[2])
    target_id = int(parts[3])

    target_seat = next((s for s, u in player_slots.items() if u == target_id), None)
    challenger_seat = next((s for s, u in player_slots.items() if u == challenger_id), None)

    if not target_seat or not challenger_seat:
        await callback.answer("⚠️ صندلی نامعتبر.", show_alert=True)
        return

    if callback.from_user.id not in [target_id, moderator_id]:
        await callback.answer("❌ فقط صاحب نوبت یا گرداننده می‌تواند تصمیم بگیرد.", show_alert=True)
        return

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    # درخواست‌های بازیکن مربوطه رو از لیست پاک می‌کنیم
    if target_seat in challenge_requests:
        challenge_requests[target_seat].pop(challenger_id, None)

    if action == "reject":
        challenge_requests[target_seat] = {}
        await callback.message.edit_reply_markup(reply_markup=None)  # ❌ حذف دکمه‌ها
        await bot.send_message(group_chat_id, f"🚫 {target_name} درخواست چالش {challenger_name} را رد کرد.")
        await callback.answer()
        return

    if action == "accept":
        # همه درخواست‌های مربوط به target پاک بشن
        challenge_requests[target_seat] = {}
        # فقط target به active_challenger_seats اضافه میشه
        active_challenger_seats.add(target_seat)

        await callback.message.edit_reply_markup(reply_markup=None)  # ❌ حذف دکمه‌ها

    # ✅ فقط target (صاحب نوبت) به لیست چالش‌دهنده‌ها اضافه میشه
    active_challenger_seats.add(target_seat)

    if timing == "before":
        paused_main_player = target_seat
        paused_main_duration = DEFAULT_TURN_DURATION
        challenge_mode = True

        await bot.send_message(
            group_chat_id,
            f"⚔ {target_name} درخواست چالش {challenger_name} را قبول کرد (قبل از صحبت)."
        )
        await start_turn(challenger_seat, duration=60, is_challenge=True)

    elif timing == "after":
        pending_challenges[target_seat] = challenger_id

        await bot.send_message(
            group_chat_id,
            f"⚔ {target_name} درخواست چالش {challenger_name} را قبول کرد (بعد از صحبت)."
        )

    await callback.answer()

# ======================
# انتخاب نوع چالش (قبل / بعد / انصراف)
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("challenge_"))
async def challenge_choice(callback: types.CallbackQuery):
    global paused_main_player, paused_main_duration

    parts = callback.data.split("_")
    # مثال: challenge_before_12345_67890
    action = parts[1]     # before / after / none
    challenger_id = int(parts[2])
    target_id = int(parts[3])

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    # فقط چالش‌کننده یا گرداننده اجازه دارند
    if callback.from_user.id not in [challenger_id, moderator_id]:
        await callback.answer("❌ فقط صاحب ترن یا گرداننده می‌تواند این گزینه را انتخاب کند.", show_alert=True)
        return

    if action == "before":
        paused_main_player = target_id
        paused_main_duration = DEFAULT_TURN_DURATION

        if turn_timer_task and not turn_timer_task.done():
            turn_timer_task.cancel()

        challenger_seat = next((s for s,u in player_slots.items() if u == challenger_id), None)
        if challenger_seat is None:
            await bot.send_message(group_chat_id, "⚠️ چالش‌کننده صندلی ندارد؛ نمی‌توان چالش را اجرا کرد.")
        else:
            await bot.send_message(group_chat_id, f"⚔ چالش قبل صحب برای {challenger_name} از {target_name} اجرا شد.")
            await start_turn(challenger_seat, duration=60, is_challenge=True)

    elif action == "after":
        target_seat = next((s for s,u in player_slots.items() if u == target_id), None)
        if target_seat is None:
            await bot.send_message(group_chat_id, "⚠️ هدف چالش صندلی ندارد؛ نمی‌توان چالش را ثبت کرد.")
        else:
            pending_challenges[target_seat] = challenger_id
            await bot.send_message(group_chat_id, f"⚔ چالش بعد صحبت برای {target_name} ثبت شد (: {challenger_name}).")

    elif action == "none":
        await bot.send_message(group_chat_id, f"🚫 {challenger_name}   چالش نداد .")

    await callback.answer()



#===============
# نوع چالش
#===============

#===============
# انتخاب چالش
#===============

# ======================
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
