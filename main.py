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
from aiogram.utils.exceptions import ChatAdminRequired
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils.exceptions import MessageNotModified, MessageToEditNotFound, MessageCantBeEdited
import jdatetime
class AddScenario(StatesGroup):
    waiting_for_name = State()
    waiting_for_roles = State()
    waiting_for_min_players = State()




# ======================
# تنظیمات ربات
# ======================
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise ValueError("API_TOKEN environment variable is not set!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# فقط این گروه اجازه اجرای بازی داره
#تست
#ALLOWED_GROUP_ID = -1003080272814
#اصلی
ALLOWED_GROUP_ID = -1001760002160
#چکنویس
#ALLOWED_GROUP_ID = -1002356353761

# ======================
# متغیرهای سراسری
# ======================
players = {}                # بازیکنان: {user_id: name}
moderator_id = None         # آیدی گرداننده
selected_scenario = None    # سناریوی انتخابی
scenarios = {}              # لیست سناریوها
game_message_id = None
lobby_message_id = None     # پیام لابی
group_chat_id = None
admins = set()
game_running = False     # وقتی بازی واقعاً شروع شده است (نقش‌ها ارسال شدند)
lobby_active = False     # وقتی لابی فعال است (انتخاب سناریو و گرداننده)
turn_order = []             # ترتیب نوبت‌ها
current_turn_index = 0      # اندیس نوبت فعلی
current_turn_message_id = None  # پیام پین شده برای نوبت
turn_timer_task = None      # تسک تایمر نوبت
player_slots = {}  # {slot_number: user_id}
challenge_requests = {}  
pending_challenges = {}
active_challenger_seats = set()
challenge_mode = False      # آیا الان در حالت نوبت چالش هستیم؟
paused_main_player = None   # اگر چالش "قبل" ثبت شد، اینجا id نوبت اصلی ذخیره می‌شود تا بعد از چالش resume شود
paused_main_duration = None # (اختیاری) مدت زمان نوبت اصلی برای resume — معمولا 120
DEFAULT_TURN_DURATION = 120  # مقدار پیش‌فرض نوبت اصلی (در صورت تمایل تغییر بده)
challenges = {}  # {player_id: {"type": "before"/"after", "challenger": user_id}}
challenge_active = True
post_challenge_advance = False   # وقتی اجرای چالش 'بعد' باشه، بعد از چالش به نوبت بعدی می‌رویم
substitute_list = {}  # group_id: {user_id: {"name": name}}
players_in_game = {}  # group_id: {seat_number: {"id": user_id, "name": name, "role": role}}
removed_players = {}  # group_id: {seat_number: {"id": user_id, "name": name, "roles": []}}
MAX_SEATS = 0        # تعداد صندلی‌ها، بعد از انتخاب سناریو مقداردهی میشه
waiting_message_id = None
waiting_list = []     # لیست انتظار جایگزین
substitute_list = {}  # لیست جایگزین‌ها بر اساس گروه
extra_turns = []  # لیست بازیکن‌هایی که باید بعد از پایان دور یک ترن اضافه بگیرن

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
    try:
        with open("scenarios.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "سناریو کلاسیک": {"min_players": 5, "max_players": 10, "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند"]},
            "سناریو ویژه": {"min_players": 6, "max_players": 12, "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند", "کارآگاه"]}
        }

def save_scenarios():
    with open("scenarios.json", "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)

scenarios = load_scenarios()

# ------------------------------
# انتخاب سناریو → تنظیم MAX_SEATS
# ------------------------------
def set_max_seats_from_scenario(scenario_name: str):
    global MAX_SEATS
    roles = scenarios.get(scenario_name, [])
    MAX_SEATS = len(roles)

# ================================
# تابع تقویم
# ================================
def get_jalali_today():
    today = jdatetime.date.today()
    return today.strftime("%Y/%m/%d")

# ======================
# مدیریت سناریو
# ======================
@dp.callback_query_handler(lambda c: c.data == "manage_scenarios")
async def manage_scenarios(callback: types.CallbackQuery):
    # گرفتن لیست ادمین‌ها از گروه اصلی
    if not group_chat_id:
        await callback.answer("❌ هنوز گروهی ثبت نشده.", show_alert=True)
        return

    admins_chat = await bot.get_chat_administrators(group_chat_id)
    admin_ids = [a.user.id for a in admins_chat]

    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ فقط ادمین‌های گروه می‌توانند مدیریت سناریو کنند.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("➕ افزودن سناریو", callback_data="add_scenario"),
        InlineKeyboardButton("➖ حذف سناریو", callback_data="remove_scenario"),
        InlineKeyboardButton("⬅ بازگشت", callback_data="back_main")
    )
    await callback.message.edit_text("⚙ مدیریت سناریو:", reply_markup=kb)


# شروع افزودن سناریو
@dp.callback_query_handler(lambda c: c.data == "add_scenario")
async def add_scenario_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 نام سناریو را وارد کنید:")
    await state.set_state(AddScenario.waiting_for_name)


# مرحله ۱: دریافت نام
@dp.message_handler(state=AddScenario.waiting_for_name)
async def add_scenario_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("👥 نقش‌های سناریو را با کاما (,) جدا کنید:")
    await state.set_state(AddScenario.waiting_for_roles)


# مرحله ۲: دریافت نقش‌ها
@dp.message_handler(state=AddScenario.waiting_for_roles)
async def add_scenario_roles(message: types.Message, state: FSMContext):
    roles = [r.strip() for r in message.text.split(",") if r.strip()]
    await state.update_data(roles=roles)

    await message.answer("🔢 حداقل تعداد بازیکنان را وارد کنید:")
    await state.set_state(AddScenario.waiting_for_min_players)

# مرحله ۳: دریافت حداقل بازیکنان و ذخیره نهایی
@dp.message_handler(state=AddScenario.waiting_for_min_players)
async def add_scenario_min_players(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفا یک عدد معتبر وارد کنید.")
        return

    min_players = int(message.text)
    data = await state.get_data()

    name = data["name"]
    roles = data["roles"]
    max_players = len(roles)  # حداکثر تعداد بازیکن = تعداد نقش‌ها

    # ذخیره در دیکشنری scenarios
    scenarios[name] = {
        "roles": roles,
        "min_players": min_players,
        "max_players": max_players
    }

    # ذخیره در فایل
    save_scenarios()

    await message.answer(
        f"✅ سناریو <b>{name}</b> با موفقیت ذخیره شد!\n\n"
        f"👥 نقش‌ها: {', '.join(roles)}\n"
        f"🔢 بازیکنان: {min_players} تا {max_players}",
        parse_mode="HTML"
    )

    await state.finish()



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

# ======================
# 🎮 مدیریت بازی در پیوی
# ======================
@dp.callback_query_handler(lambda c: c.data == "manage_game")
async def manage_game_handler(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer("⚠️ این گزینه فقط در پیوی کار می‌کند.", show_alert=True)
        return

    user_id = callback.from_user.id
    # 🔴 قبلاً: if not reserved_god or (user_id != reserved_god.get("id") and user_id not in admins):
    if not moderator_id or (user_id != moderator_id and user_id not in admins):
        await callback.answer("⛔ فقط گرداننده یا مدیران گروه می‌تونن به منوی مدیریت دسترسی داشته باشن!", show_alert=True)
        return

    if not group_chat_id:
        await callback.answer("🚫 هنوز هیچ بازی فعالی شروع نشده.", show_alert=True)
        return

    kb = manage_game_keyboard(group_chat_id)
    await callback.message.edit_text("🎮 منوی مدیریت بازی:", reply_markup=kb)
    await callback.answer()

# ==============================
# لیست بعد از انتخاب سر صحبت
# ==============================
async def send_turn_order_list():
    if not turn_order:
        return

    text = "👥 لیست بازیکنان (بر اساس نوبت صحبت):\n"
    text += "◤◢◣◥◤◢◣◥◤◢◣◥\n\n"

    for i, seat in enumerate(turn_order, start=1):
        uid = player_slots.get(seat)
        if not uid:
            continue
        name = players.get(uid, "❓")
        mention = f"<a href='tg://user?id={uid}'><b>{html.escape(name)}</b></a>"
        text += f"\u200F{i:02d} {mention}\n"

    text += "\n◤◢◣◥◤◢◣◥◤◢◣◥"
    await bot.send_message(group_chat_id, text, parse_mode="HTML")


# -----------------------------
# اضافه شدن به لیست جایگزین
# -----------------------------
@dp.message_handler(lambda m: m.text and "جایگزین" in m.text)
async def add_to_substitute_list(message: types.Message):
    global substitute_list, group_chat_id

    if not group_chat_id:
        await message.reply("⚠️ هنوز هیچ بازی فعالی شروع نشده.")
        return

    user_id = message.from_user.id
    user_name = message.from_user.full_name

    # مطمئن میشیم substitute_list ساختار درست داشته باشه
    if group_chat_id not in substitute_list:
        substitute_list[group_chat_id] = {}

    # جلوگیری از تکرار
    if user_id in substitute_list[group_chat_id]:
        await message.reply("ℹ️ شما قبلاً در لیست جایگزین هستید.")
        return

    # ذخیره با ساختار درست
    substitute_list[group_chat_id][user_id] = {
        "id": user_id,
        "name": user_name
    }

    await message.reply(f"✅ شما به لیست جایگزین اضافه شدید: {user_name}")


# =========================
# صندلی من
# =========================
@dp.message_handler(lambda m: m.text and m.text.strip() == "صندلی من")
async def my_seat_handler(message: types.Message):
    global player_slots, group_chat_id

    uid = message.from_user.id
    # پیدا کردن صندلی از player_slots (seat -> uid)
    seat = next((s for s, u in (player_slots or {}).items() if u == uid), None)

    if seat is None:
        await message.reply("⚠️ شما در بازی ثبت نشده‌اید یا هنوز صندلی به شما اختصاص نیافته.")
    else:
        await message.reply(f"🔹 شما در صندلی شماره {seat} قرار دارید.")


# =========================
# لیست صندلی
# =========================
@dp.message_handler(lambda m: m.text and m.text.strip() == "لیست صندلی")
async def seats_list_handler(message: types.Message):
    global player_slots, players, reserved_list, group_chat_id

    # اگر بازی در حال اجراست از player_slots و players استفاده کن، در غیر اینصورت از reserved_list
    text_lines = []
    if player_slots:
        for seat in sorted(player_slots.keys()):
            uid = player_slots.get(seat)
            name = players.get(uid, "❓") if uid else "---"
            text_lines.append(f"{seat:02d}. {html.escape(name)}")
    elif reserved_list:
        for item in reserved_list:
            name = item.get("player", {}).get("name") if item.get("player") else "---"
            text_lines.append(f"{item['seat']:02d}. {html.escape(name if name else '---')}")
    else:
        await message.reply("🚫 هیچ لیست صندلی فعالی وجود ندارد.")
        return

    text = "📋 لیست صندلی‌ها:\n\n" + "\n".join(text_lines)
    await message.reply(text)


# =========================
# نقش من (فقط در پیوی)
# =========================
@dp.message_handler(lambda m: m.text and m.text.strip() == "نقش من")
async def my_role_handler(message: types.Message):
    global last_role_map, group_chat_id

    if message.chat.type != "private":
        await message.reply("ℹ️ برای دریافت نقش، لطفاً در پیوی این پیام را ارسال کنید: «نقش من»")
        return

    uid = message.from_user.id
    # نقش‌ها ممکنه در last_role_map یا player_roles ذخیره شده باشه
    role = None
    if globals().get("last_role_map"):
        role = last_role_map.get(uid)
    if not role and globals().get("player_roles"):
        role = globals().get("player_roles").get(uid)

    if role:
        # نقش خصوصی به کاربر در پیوی ارسال می‌شود
        await message.reply(f"🔐 نقش شما: {html.escape(str(role))}")
    else:
        await message.reply("⚠️ هنوز نقشی برای شما اختصاص داده نشده یا بازی شروع نشده.")


# =========================
# لیست بازیکنان (فقط گرداننده یا مدیران)
# =========================
@dp.message_handler(lambda m: m.text and m.text.strip() == "لیست بازیکنان")
async def show_players_handler(message: types.Message):
    global group_chat_id, reserved_god, players, player_slots, group_admins, bot

    # در گروه: بررسی اینکه فرستنده ادمین هست یا نه
    is_allowed = False
    uid = message.from_user.id

    # اگر فرستنده گرداننده باشه اجازه بده
    if reserved_god and uid == reserved_god.get("id"):
        is_allowed = True
    else:
        # اگر پیام در گروه باشه، چک کن او ادمین است
        if message.chat.type in ["group", "supergroup"]:
            member = await bot.get_chat_member(message.chat.id, uid)
            if member.status in ["creator", "administrator"]:
                is_allowed = True
        else:
            # اگر در پیویه، سعی کن group_admins رو آپدیت کنی و چک کن
            await ensure_group_admins()
            if uid in (group_admins or []):
                is_allowed = True

    if not is_allowed:
        await message.reply("⛔ فقط گرداننده یا مدیران گروه می‌توانند لیست بازیکنان را مشاهده کنند.")
        return

    # ساخت متن لیست بازیکنان
    if player_slots:
        lines = []
        for seat in sorted(player_slots.keys()):
            uid = player_slots.get(seat)
            name = players.get(uid, "❓") if uid else "---"
            lines.append(f"{seat:02d}. {html.escape(name)}")
        text = "📜 لیست بازیکنان:\n\n" + "\n".join(lines)
    else:
        text = "🚫 هیچ بازیکنی در بازی ثبت نشده است."

    await message.reply(text)


# =========================
# وضعیت بازی
# =========================
@dp.message_handler(lambda m: m.text and m.text.strip() == "وضعیت بازی")
async def game_status_handler(message: types.Message):
    global group_chat_id, players, player_slots, reserved_list, reserved_scenario, round_active, turn_order

    num_players = len(players) if globals().get("players") else 0
    seats_total = None
    if globals().get("reserved_list"):
        seats_total = len(reserved_list)
    else:
        try:
            if reserved_scenario:
                seats_total = len(scenarios[reserved_scenario]["roles"])
        except Exception:
            seats_total = None

    text = "🔎 وضعیت بازی:\n\n"
    text += f"تعداد بازیکنان ثبت‌شده: {num_players}\n"
    text += f"مجموع صندلی‌ها: {seats_total if seats_total is not None else '---'}\n"
    text += f"سناریو: {reserved_scenario or '---'}\n"
    text += f"وضعیت دور: {'فعال' if round_active else 'غیرفعال'}\n"
    text += f"ترتیب نوبت: {len(turn_order) if globals().get('turn_order') else 0}\n"

    await message.reply(text)


# =============================
# خروج بازیکن (فقط در لابی)
# =============================
@dp.message_handler(lambda m: m.chat.type in ["group", "supergroup"] and m.text and m.text.strip() == "خروج")
async def leave_game(message: types.Message):
    global round_active

    group_id = message.chat.id
    user_id = message.from_user.id

    # بررسی اینکه هنوز دور شروع نشده (لابی فعال باشه)
    if round_active:
        await message.reply("⚠️ بعد از شروع بازی امکان خروج وجود ندارد.")
        return

    # بررسی اینکه بازیکن داخل بازی هست یا نه
    if user_id not in players:
        await message.reply("⚠️ شما در حال حاضر داخل بازی نیستید.")
        return

    # پیدا کردن شماره صندلی بازیکن
    seat_to_remove = None
    for seat, uid in player_slots.items():
        if uid == user_id:
            seat_to_remove = seat
            break

    # حذف بازیکن از players و player_slots
    name = players.pop(user_id, "❓")
    if seat_to_remove:
        player_slots.pop(seat_to_remove, None)

    removed_players[user_id] = name  # برای ثبت در لیست حذف‌شده‌ها

    await message.reply(f"🚪 بازیکن {html.escape(name)} از بازی خارج شد (صندلی {seat_to_remove}).")



# =========================
# راهنما / help (عمومی)
# =========================
@dp.message_handler(lambda m: m.text and m.text.strip() in ["راهنما", "/help"])
async def help_handler(message: types.Message):
    help_text = (
        "📚 راهنمای دستورات ربات:\n\n"
        "برای همه:\n"
        " - صندلی من : نمایش شماره صندلی شما\n"
        " - لیست صندلی : نمایش لیست صندلی‌ها و اسامی\n"
        " - نقش من : (در پیوی) دریافت نقش شما\n"
        " - وضعیت بازی : اطلاعات کلی درباره بازی\n\n"
        "برای گرداننده / مدیران:\n"
        " - شروع دور : شروع نوبت (فقط گرداننده)\n"
        " - لیست بازیکنان : نمایش نام و صندلی‌ها (مدیر یا گرداننده)\n\n"
        "ثبت / حذف جایگزین‌ها و خروج و دیگر دستورات:\n"
        " - جایگزین / لغو جایگزین / لیست جایگزین (همانطور که قبلاً اضافه شده‌اند)\n"
    )
    await message.reply(help_text)


# ======================
# لیست بازیکنان
# ======================
@dp.callback_query_handler(lambda c: c.data == "list_players")
async def list_players_handler(callback: types.CallbackQuery):
    # فقط پیوی
    if callback.message.chat.type != "private":
        await callback.answer()
        return

    # چک کن که group_chat_id ست شده باشه
    if not group_chat_id:
        await callback.message.answer("🚫 هنوز لابی/گروهی ست نشده است.")
        await callback.answer()
        return

    # استفاده از player_slots برای ترتیب صندلی‌ها
    seats = sorted(player_slots.items())  # [(seat, user_id), ...]
    if not seats:
        # اگر هیچ صندلی‌ای ثبت نشده، fallback به players (اگر players دیکشنریه)
        if isinstance(players, dict) and players:
            text = "👥 لیست بازیکنان (بدون صندلی):\n"
            for i, (uid, name) in enumerate(players.items(), start=1):
                text += f"{i}. <a href='tg://user?id={uid}'>{html.escape(name)}</a>\n"
        else:
            await callback.message.answer("👥 هیچ بازیکنی ثبت نشده است.")
            await callback.answer()
            return
    else:
        text = "👥 لیست بازیکنان (بر اساس شماره صندلی):\n"
        for seat, uid in seats:
            name = players.get(uid, "❓")
            text += f"{seat}. <a href='tg://user?id={uid}'>{html.escape(name)}</a>\n"

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()



# ======================
# کیبوردها
# ======================
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game")
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
# ======================
# کیبورد پنل پیوی
# ======================
def main_panel_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎮 مدیریت بازی", callback_data="manage_game"))
    kb.add(InlineKeyboardButton("📜 مدیریت سناریو", callback_data="manage_scenarios"))
    kb.add(InlineKeyboardButton("❓ راهنما", callback_data="help"))
    return kb

# -----------------------------
# منوی مدیریت بازی
# -----------------------------
def manage_game_keyboard(group_id: int):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👥 لیست بازیکنان", callback_data="list_players"))
    kb.add(InlineKeyboardButton("📤 ارسال نقش", callback_data="resend_roles"))
    kb.add(InlineKeyboardButton("🗑 حذف بازیکن", callback_data="remove_player"))
    kb.add(InlineKeyboardButton("🔄 جایگزین بازیکن", callback_data="replace_player"))
    kb.add(InlineKeyboardButton("🎂 تولد بازیکن", callback_data="player_birthday"))
    kb.add(InlineKeyboardButton("➕ ترن اضافه", callback_data="extra_turn"))   # ➕ ترن
    kb.add(InlineKeyboardButton("🔇 سکوت بازیکن", callback_data="mute_player"))     # ➕ سکوت
    kb.add(InlineKeyboardButton("🔊 حذف سکوت", callback_data="unmute_player"))     # ➕ حذف سکوت
    kb.add(InlineKeyboardButton("⚔ وضعیت چالش", callback_data="challenge_status"))
    kb.add(InlineKeyboardButton("🚫 لغو بازی", callback_data=f"cancel_{group_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb


# =========================
# توابع کمکی
# =========================
# پیام موقتی
async def send_temp_message(chat_id, text, delay=5, **kwargs):
    msg = await bot.send_message(chat_id, text, **kwargs)
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, msg.message_id)
    except:
        pass

# ========================
# لیست مدیران
# ========================
async def update_group_admins(bot, chat_id):
    """به‌روزرسانی لیست مدیران گروه"""
    global group_admins
    admins = await bot.get_chat_administrators(chat_id)
    group_admins = [admin.user.id for admin in admins]
    

# ======================
# مدیریت بازی در پیوی
# ======================
async def manage_game_handler(callback: types.CallbackQuery):
    # فقط در پیوی کار کنه
    if callback.message.chat.type != "private":
        return

    group_id = group_chat_id  # یا اگر چند گروه داری باید با تابع پیدا کنی
    await callback.message.edit_text(
        "🎮 مدیریت بازی:",
        reply_markup=manage_game_keyboard(group_id)
    )
    await callback.answer()



# -------------------------
# اضافه شدن به لیست رزرو (دکمه)
# -------------------------
@dp.callback_query_handler(lambda c: c.data == "reserve_waiting")
async def reserve_waiting(callback: types.CallbackQuery):
    global waiting_list

    user_id = callback.from_user.id
    user_name = callback.from_user.full_name

    # 1) اگر بازیکن در لیست اصلی است → اجازه نده
    if user_id in players:
        await callback.answer("⚠️ شما در حال حاضر در لیست اصلی بازی هستید و نمی‌توانید در لیست رزرو باشید.", show_alert=True)
        return

    # 2) جلوگیری از اضافه شدن تکراری
    if any(w.get("id") == user_id for w in waiting_list):
        await callback.answer("ℹ️ شما قبلاً در لیست رزرو هستید.", show_alert=True)
        # اما اگر پیام لیست رزرو ناقص است، آن را آپدیت کن
        await update_waiting_list_message()
        return

    # 3) ثبت با ساختار ثابت (dict)
    waiting_list.append({"id": user_id, "name": user_name})

    await callback.answer("✅ شما به لیست رزرو اضافه شدید.")
    # به‌روزرسانی پیام لیست رزرو و لابی (در صورت نیاز)
    await update_waiting_list_message()
    await update_lobby()
# =========================
# کنسل رزرو
# =========================
@dp.callback_query_handler(lambda c: c.data == "cancel_seat")
async def cancel_seat(callback: types.CallbackQuery):
    global reserved_list, waiting_list
    user_id = callback.from_user.id

    seat_info = next((s for s in reserved_list if s["player"] and s["player"]["id"] == user_id), None)
    if seat_info:
        seat_info["player"] = None
        await callback.answer("❌ رزرو شما لغو شد")
        if waiting_list:
            next_user = waiting_list.pop(0)
            seat_info["player"] = next_user

        await update_reserved_message(callback.message)
    else:
        await callback.answer("⚠️ شما صندلی رزرو نکرده‌اید", show_alert=True)

#=======================
# لیست بازیکنان
#=======================
@dp.callback_query_handler(lambda c: c.data == "list_players")
async def list_players_handler(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer()
        return

    global players  # فرض: players لیستی از دیکشنری بازیکنان فعلیه
    if not players:
        await callback.message.answer("👥 هیچ بازیکنی در بازی نیست.")
        await callback.answer()
        return

    text = "👥 لیست بازیکنان:\n\n"
    for p in players:
        text += f"{p['seat']} - <a href='tg://user?id={p['id']}'>{p['name']}</a>\n"

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

# ===================================
# لیست بازیکنان و نقش ها
# ===================================
async def show_roles_list(user_id: int):
    """
    ارسال لیست نقش‌ها و بازیکنان برای گرداننده در پیوی
    """
    if not selected_scenario:
        return

    # 📆 تاریخ روز شمسی
    today = JalaliDate.today().strftime("%Y/%m/%d")

    max_players = len(scenarios[selected_scenario]["roles"])
    current_players = len(players)

    # 📝 هدر لیست
    text = (
        "༄\n"
        "    Mafia Nights\n\n"
        f"⏱ Time : 21:00\n"
        f"📆 Date : {today}\n"
        f"🗓 Scenario : {selected_scenario}\n"
        f"👮‍♂ God : {players.get(moderator_id, '---')}\n\n"
        f"👥 Players : {current_players}/{max_players}\n\n"
        " ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ \n"
        "        لیست بازیکنان\n"
        "◤◢◣◥◤◢◣◥◤◢◣◥\n\n"
    )

    # 📋 لیست بازیکنان بر اساس شماره صندلی
    for seat in sorted(player_slots.keys()):
        uid = player_slots[seat]
        name = players.get(uid, "❓")
        mention = f"<b><a href='tg://user?id={uid}'>{html.escape(name)}</a></b>"
        text += f"{seat:02d} {mention}\n"

    text += "\n◤◢◣◥◤◢◣◥◤◢◣◥\n\n༄"

    # 📤 ارسال پیام به پیوی گرداننده
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        logging.exception("⚠️ خطا در ارسال لیست نقش‌ها به گرداننده")


#=======================
# ارسال نقش ها
#=======================
@dp.callback_query_handler(lambda c: c.data == "resend_roles")
async def resend_roles_handler(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer()
        return

    if not group_chat_id:
        await callback.message.answer("🚫 هنوز هیچ بازی فعالی وجود ندارد.")
        await callback.answer()
        return

    # بررسی وجود نقش‌های قبلی
    global last_role_map
    if not last_role_map:
        await callback.message.answer("⚠️ نقش‌ها هنوز پخش نشده‌اند؛ ابتدا «پخش نقش» در گروه را بزنید.")
        await callback.answer()
        return

    # ارسال نقش به هر بازیکن
    sent = 0
    if player_slots:
        for seat in sorted(player_slots.keys()):
            uid = player_slots[seat]
            role = last_role_map.get(uid, "❓")
            try:
                await bot.send_message(uid, f"🎭 نقش شما: {html.escape(str(role))}")
                sent += 1
            except Exception as e:
                logging.warning("⚠️ ارسال نقش به %s خطا: %s", uid, e)
    else:
        # fallback
        for uid in players.keys():
            role = last_role_map.get(uid, "❓")
            try:
                await bot.send_message(uid, f"🎭 نقش شما: {html.escape(str(role))}")
                sent += 1
            except Exception as e:
                logging.warning("⚠️ ارسال نقش به %s خطا: %s", uid, e)

    if sent == 0:
        await callback.message.answer("⚠️ هیچ پیامی ارسال نشد (شاید بازیکنانی پیویشان بسته است).")
        await callback.answer()
        return

    # 📜 ساخت متن لیست نقش‌ها برای گرداننده
    fancy_text = "༄\n    Mafia Nights\n\n"
    fancy_text += "⏱ Time : 21:00\n"
    fancy_text += f"📆 Date : {get_jalali_today()}\n"
    fancy_text += f"🗓 Scenario : {selected_scenario}\n"
    fancy_text += f"👮‍♂ God : {players.get(moderator_id, '❓')}\n\n"
    fancy_text += " ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ \n"
    fancy_text += "          لیست نقش‌ها\n"
    fancy_text += "◤◢◣◥◤◢◣◥◤◢◣◥\n\n"

    for seat in sorted(player_slots.keys()):
        uid = player_slots[seat]
        role = last_role_map.get(uid, "❓")
        name = players.get(uid, "❓")
        mention = f"<a href='tg://user?id={uid}'><b>{html.escape(name)}</b></a>"
        fancy_text += f"\u200E{seat:02d} {mention} — {html.escape(role)}\n"

    fancy_text += "\n◤◢◣◥◤◢◣◥◤◢◣◥\n\n༄"

    # ارسال لیست به گرداننده
    try:
        await bot.send_message(moderator_id, fancy_text, parse_mode="HTML")
    except Exception as e:
        logging.warning("⚠️ ارسال لیست نقش‌ها به گرداننده شکست خورد: %s", e)

    await callback.answer(f"✅ نقش‌ها به {sent} بازیکن ارسال شدند.")



# -----------------------------
# جایگزینی بازیکن - نمایش لیست جایگزین‌ها
# -----------------------------
@dp.callback_query_handler(lambda c: c.data == "replace_player")
async def replace_player_list_handler(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer()
        return

    subs = substitute_list.get(group_chat_id, {})
    if not subs:
        await callback.message.answer("🚫 لیست جایگزین‌ها خالی است.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for uid, info in subs.items():
        name = info.get("name") or "❓"
        kb.add(InlineKeyboardButton(html.escape(name), callback_data=f"choose_sub_{uid}"))

    await callback.message.answer("👥 لیست جایگزین‌ها:", reply_markup=kb)
    await callback.answer()


# -----------------------------
# انتخاب بازیکن اصلی برای جایگزینی
# -----------------------------
@dp.callback_query_handler(lambda c: c.data.startswith("choose_sub_"))
async def choose_substitute_for_replace(callback: types.CallbackQuery):
    uid_sub = int(callback.data.replace("choose_sub_", ""))

    # بازیکنان فعلی
    current = {seat: players.get(uid, "❓") for seat, uid in player_slots.items()}
    if not current:
        await callback.message.answer("🚫 هیچ بازیکنی در بازی نیست.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for seat, name in sorted(current.items()):
        label = f"{seat}. {html.escape(name)}"
        kb.add(InlineKeyboardButton(label, callback_data=f"do_replace_{uid_sub}_{seat}"))

    await callback.message.answer("👤 بازیکن جایگزین، بازیکن فعلی را انتخاب کنید:", reply_markup=kb)
    await callback.answer()


# -----------------------------
# انجام جایگزینی
# -----------------------------
@dp.callback_query_handler(lambda c: c.data.startswith("do_replace_"))
async def do_replace_handler(callback: types.CallbackQuery):
    try:
        _, _, uid_sub_str, seat_str = callback.data.split("_")
        uid_sub = int(uid_sub_str)
        seat = int(seat_str)
    except Exception:
        await callback.answer("⚠️ داده جایگزینی نامعتبر است.", show_alert=True)
        return

    subs = substitute_list.get(group_chat_id, {})
    sub_info = subs.pop(uid_sub, None)
    if not sub_info:
        await callback.message.answer("⚠️ جایگزینی پیدا نشد.")
        await callback.answer()
        return

    # بازیکن قدیمی
    old_uid = player_slots.get(seat)
    old_name = players.pop(old_uid, "❓") if old_uid in players else "❓"

    # جایگزین جدید
    players[uid_sub] = sub_info.get("name", f"User{uid_sub}")
    player_slots[seat] = uid_sub

    # انتقال نقش در صورت وجود
    global last_role_map
    if old_uid and last_role_map and old_uid in last_role_map:
        last_role_map[uid_sub] = last_role_map.pop(old_uid)

    await callback.message.answer(
        f"✅ بازیکن {html.escape(old_name)} با {html.escape(players[uid_sub])} جایگزین شد (صندلی {seat})."
    )
    await callback.answer()

#=======================
# حذف بازیکن
#=======================
@dp.callback_query_handler(lambda c: c.data == "remove_player")
async def remove_player_handler(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer()
        return

    if not group_chat_id:
        await callback.message.answer("🚫 هنوز هیچ بازی فعالی وجود ندارد.")
        await callback.answer()
        return

    # اگر player_slots پر است: لیست بر اساس صندلی
    if player_slots:
        kb = InlineKeyboardMarkup(row_width=1)
        for seat in sorted(player_slots.keys()):
            uid = player_slots[seat]
            name = players.get(uid, "❓")
            kb.add(InlineKeyboardButton(f"{seat}. {html.escape(name)}", callback_data=f"confirm_remove_{seat}"))
        await callback.message.answer("🗑 لطفاً بازیکنی که می‌خواهید حذف شود را انتخاب کنید:", reply_markup=kb)
        await callback.answer()
        return

    # fallback: اگر فقط players دیکشنری است
    if isinstance(players, dict) and players:
        kb = InlineKeyboardMarkup(row_width=1)
        for uid, name in players.items():
            kb.add(InlineKeyboardButton(html.escape(name), callback_data=f"confirm_remove_uid_{uid}"))
        await callback.message.answer("🗑 بازیکنی را انتخاب کنید:", reply_markup=kb)
        await callback.answer()
        return

    await callback.message.answer("🚫 بازیکنی برای حذف وجود ندارد.")
    await callback.answer()


# پردازش تایید حذف بر اساس صندلی
@dp.callback_query_handler(lambda c: c.data.startswith("confirm_remove_"))
async def remove_player_confirm(callback: types.CallbackQuery):
    data = callback.data
    # دو حالت: confirm_remove_{seat} یا confirm_remove_uid_{uid}
    if data.startswith("confirm_remove_uid_"):
        uid = int(data.replace("confirm_remove_uid_", ""))
        # جستجو برای صندلی (اگر وجود داشته باشه)
        seat = next((s for s, u in player_slots.items() if u == uid), None)
    else:
        seat = int(data.replace("confirm_remove_", ""))
        uid = player_slots.get(seat)

    if uid is None:
        await callback.message.answer("⚠️ بازیکن پیدا نشد.")
        await callback.answer()
        return

    # حذف از player_slots و players؛ و اضافه شدن به removed_players[group]
    removed_players.setdefault(group_chat_id, {})[seat] = {"id": uid, "name": players.get(uid, "❓")}
    # حذف از players dict اگر موجوده
    try:
        if uid in players:
            del players[uid]
    except Exception:
        pass

    if seat in player_slots:
        del player_slots[seat]

    await callback.message.answer(f"✅ بازیکن با آی‌دی {uid} حذف شد و به لیست خارج‌شده‌ها منتقل شد.")
    await callback.answer()



#=======================
# تولد بازیکن
#=======================
@dp.callback_query_handler(lambda c: c.data == "player_birthday")
async def birthday_player_handler(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer()
        return

    if not group_chat_id:
        await callback.message.answer("🚫 هنوز هیچ بازی فعالی وجود ندارد.")
        await callback.answer()
        return

    removed = removed_players.get(group_chat_id, {})
    if not removed:
        await callback.message.answer("🚫 لیست بازیکنان خارج‌شده خالی است.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for seat, info in sorted(removed.items()):
        kb.add(InlineKeyboardButton(f"{seat}. {html.escape(info.get('name','❓'))}", callback_data=f"confirm_revive_{seat}"))

    await callback.message.answer("🎂 بازیکنی را که می‌خواهید بازگردانید انتخاب کنید:", reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("confirm_revive_"))
async def birthday_player_confirm(callback: types.CallbackQuery):
    seat = int(callback.data.replace("confirm_revive_", ""))
    info = removed_players.get(group_chat_id, {}).pop(seat, None)
    if not info:
        await callback.message.answer("⚠️ موردی برای بازگرداندن پیدا نشد.")
        await callback.answer()
        return

    uid = info["id"]
    name = info.get("name", "❓")
    # بازگرداندن به players و player_slots
    players[uid] = name
    player_slots[seat] = uid

    await callback.message.answer(f"✅ بازیکن {html.escape(name)} با صندلی {seat} بازگردانده شد.")
    await callback.answer()



#=======================
# لغو بازی
#=======================
@dp.callback_query_handler(lambda c: c.data.startswith("cancel_"))
async def cancel_game_handler(callback: types.CallbackQuery):
    global players, removed_players, substitute_list

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    # گرفتن لیست ادمین‌های گروه
    admins = await callback.bot.get_chat_administrators(chat_id)
    admin_ids = [admin.user.id for admin in admins]

    # شرط دسترسی
    if not reserved_god or (user_id != reserved_god.get("id") and user_id not in admin_ids):
        await callback.answer("⛔ فقط گرداننده یا مدیران گروه می‌تونن بازی رو لغو کنن!", show_alert=True)
        return

    players.clear()
    removed_players.clear()
    substitute_list.clear()

    await callback.message.answer("🚫 بازی لغو شد.")
    await callback.answer()


#========================
# 
#========================
def register_game_panel_handlers(dp: Dispatcher):
    dp.register_callback_query_handler(manage_game_handler, lambda c: c.data == "manage_game")
    dp.register_callback_query_handler(lambda c: send_roles_panel(c, dp.bot), lambda c: c.data == "resend_roles")
    dp.register_callback_query_handler(list_players_pv, lambda c: c.data == "list_players")
    dp.register_callback_query_handler(show_substitute_list, lambda c: c.data == "replace_player")
    dp.register_callback_query_handler(choose_substitute, lambda c: c.data.startswith("choose_sub_"))
    dp.register_callback_query_handler(replace_player, lambda c: c.data.startswith("replace_"))
    dp.register_callback_query_handler(challenge_status_pv, lambda c: c.data == "challenge_status")
    dp.register_message_handler(add_substitute, lambda m: m.text.strip() == "جایگزین")
    dp.register_callback_query_handler(remove_player_handler, lambda c: c.data == "remove_player")
    dp.register_callback_query_handler(remove_player_confirm, lambda c: c.data.startswith("remove_"))
    dp.register_callback_query_handler(birthday_player_handler, lambda c: c.data == "player_birthday")
    dp.register_callback_query_handler(birthday_player_confirm, lambda c: c.data.startswith("revive_"))
    




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


#======================
# تابع کمکی برای پخش نقش‌ها
#======================
@dp.callback_query_handler(lambda c: c.data == "distribute_roles")
async def distribute_roles_callback(callback: types.CallbackQuery):
    global game_message_id, lobby_message_id, game_running, group_chat_id, last_role_map

    # فقط گرداننده اجازه دارد
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند نقش‌ها را پخش کند.", show_alert=True)
        return

    if not selected_scenario:
        await callback.answer("❌ سناریو انتخاب نشده.", show_alert=True)
        return

    try:
        mapping = await distribute_roles()
        last_role_map = mapping
    except Exception as e:
        logging.exception("⚠️ مشکل در پخش نقش‌ها: %s", e)
        await callback.answer("❌ خطا در پخش نقش‌ها.", show_alert=True)
        return

    # نمایش لیست بازیکنان در گروه
    seats = {seat: (uid, players.get(uid, "❓")) for seat, uid in player_slots.items()}
    players_list = "\n".join([
        f"{seat:02d}. <a href='tg://user?id={uid}'>{html.escape(name)}</a>"
        for seat, (uid, name) in sorted(seats.items())
    ])

    text = (
        "🎭 نقش‌ها پخش شد!\n\n"
        f"👥 لیست بازیکنان:\n{players_list}\n\n"
        "ℹ️ برای دیدن نقش به پیوی ربات بروید.\n"
        "👑 گرداننده سر صحبت را انتخاب کند تا بازی شروع شود."
    )

    # ساخت کیبورد مدیریت دور
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👑 انتخاب سر صحبت", callback_data="choose_head"))
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))
    kb.add(InlineKeyboardButton("⚔ چالش روشن" if challenge_active else "⚔ چالش خاموش",
                                callback_data="challenge_toggle"))

    # ویرایش یا ارسال پیام بازی
    try:
        if lobby_message_id:
            msg = await bot.edit_message_text(
                text, chat_id=group_chat_id, message_id=lobby_message_id,
                parse_mode="HTML", reply_markup=kb
            )
            game_message_id = msg.message_id
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

    # آماده‌سازی لیست نقش‌ها مطابق تعداد بازیکنان
    roles = list(roles_template)  # کپی
    if len(player_ids) > len(roles):
        # اگر نیاز به نقش بیشتر هست، بقیه را "شهروند" قرار می‌دهیم
        roles += ["شهروند"] * (len(player_ids) - len(roles))
    roles = roles[:len(player_ids)]  # اگر نقش بیشتر از بازیکن بود کوتاه می‌کنیم

    random.shuffle(roles)

    mapping = {}
    for pid, role in zip(player_ids, roles):
        mapping[pid] = role
        try:
            await bot.send_message(pid, f"🎭 نقش شما: <b>{html.escape(str(role))}</b>", parse_mode="HTML")
        except Exception as e:
            # به گرداننده اطلاع بده که ارسال به یکی از بازیکنان شکست خورد
            logging.warning("⚠️ ارسال نقش به %s شکست خورد: %s", pid, e)
            if moderator_id:
                try:
                    await bot.send_message(moderator_id, f"⚠️ نمی‌توانم نقش را به {players.get(pid, pid)} ارسال کنم.")
                except:
                    pass

    return mapping

#==================

# =========================
# هندلرهای دستورات متنی گروه
# =========================
@dp.message_handler(lambda m: m.chat.type in ["group", "supergroup"] and not m.text.startswith("/"))
async def text_commands_handler(message: types.Message):
    text = message.text.strip().lower()
    group_id = message.chat.id

    # helper: تعیین لیست uidهای بازیکنان برای گروه جاری با چند fallback
    def get_group_player_ids(gid):
        # 1) players[group_id] اگر ساختار گروهی داشته باشی (لیست)
        try:
            val = players.get(gid)
            if isinstance(val, list) and val:
                return val
        except Exception:
            pass

        # 2) player_slots (صندلی -> uid) اگر پر است، از اون استفاده کن
        try:
            if player_slots:
                # بازگرداندن فقط uidها (به ترتیب صندلی)
                return [uid for seat, uid in sorted(player_slots.items())]
        except Exception:
            pass

        # 3) players به شکل {uid: name} → کل uidها
        try:
            if isinstance(players, dict) and players:
                # اگر values ها اسامی باشن (str) فرض می‌کنیم کلیدها uid هستند
                sample_val = next(iter(players.values()))
                if isinstance(sample_val, str) or isinstance(sample_val, (str,)):
                    return list(players.keys())
        except Exception:
            pass

        return []


    # -------------------
    # دستور "تگ لیست" → فقط بازیکنان حاضر در بازی
    # -------------------
    if text == "تگ لیست":
        # ترجیحاً از player_slots استفاده کن چون صندلی‌ها نشان‌دهندهٔ حاضر بودنن
        uids = []
        try:
            if player_slots:
                uids = [uid for seat, uid in sorted(player_slots.items())]
        except Exception:
            uids = []

        # اگر خالی بود، fallback به همان تابع بالا
        if not uids:
            uids = get_group_player_ids(group_id)

        if not uids:
            await message.reply("👥 هیچ بازیکنی در بازی نیست.")
            return

        parts = []
        for uid in uids:
            name = players.get(uid) if isinstance(players, dict) else None
            if name:
                parts.append(f"<a href='tg://user?id={uid}'>{html.escape(name)}</a>")
            else:
                parts.append(f"<a href='tg://user?id={uid}'>🎮</a>")

        await message.reply("📢 تگ بازیکنان حاضر:\n" + " ".join(parts), parse_mode="HTML")
        return

    # -------------------
    # دستور "تگ ادمین" → فقط مدیران گروه
    # -------------------
    if text == "تگ ادمین":
        try:
            admins = await bot.get_chat_administrators(group_id)
        except Exception as e:
            await message.reply("⚠️ خطا در دریافت مدیران گروه.")
            return

        if not admins:
            await message.reply("ℹ️ هیچ مدیری در این گروه یافت نشد.")
            return

        parts = []
        for admin in admins:
            uid = admin.user.id
            full = admin.user.full_name or str(uid)
            parts.append(f"<a href='tg://user?id={uid}'>{html.escape(full)}</a>")

        await message.reply("📢 تگ مدیران گروه:\n" + " ".join(parts), parse_mode="HTML")
        return

    # بقیه پیام‌ها — نادیده بگیر
    return

# ======================
# کیبوردها
# ======================
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎮 بازی جدید", callback_data="new_game"),
        InlineKeyboardButton("📋 لیست جدید", callback_data="new_list"),
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
# ======================
# کیبورد پنل پیوی
# ======================
def main_panel_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎮 مدیریت بازی", callback_data="manage_game"))
    kb.add(InlineKeyboardButton("📜 مدیریت سناریو", callback_data="manage_scenarios"))
    kb.add(InlineKeyboardButton("❓ راهنما", callback_data="help"))
    return kb

# -----------------------------
# منوی مدیریت بازی
# -----------------------------
def manage_game_keyboard(group_id: int):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👥 لیست بازیکنان", callback_data="list_players"))
    kb.add(InlineKeyboardButton("📤 ارسال نقش", callback_data="resend_roles"))
    kb.add(InlineKeyboardButton("🗑 حذف بازیکن", callback_data="remove_player"))
    kb.add(InlineKeyboardButton("🔄 جایگزین بازیکن", callback_data="replace_player"))
    kb.add(InlineKeyboardButton("🎂 تولد بازیکن", callback_data="player_birthday"))
    kb.add(InlineKeyboardButton("⚔ وضعیت چالش", callback_data="challenge_status"))
    kb.add(InlineKeyboardButton("⚙️ تنظیم گرداننده", callback_data="manage_moderator"))
    kb.add(InlineKeyboardButton("🚫 لغو بازی", callback_data=f"cancel_{group_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb

# =========================
# توابع کمکی
# =========================
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

# =======================
# تنظیم گرداننده
# =======================
@dp.callback_query_handler(lambda c: c.data == "manage_moderator")
async def manage_moderator_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👤 گرداننده فعلی", callback_data="show_current_mod"))
    kb.add(InlineKeyboardButton("🔄 تغییر گرداننده", callback_data="change_mod"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="back_manage_game"))

    await callback.message.edit_text("⚙️ تنظیمات گرداننده:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "show_current_mod")
async def show_current_moderator(callback: types.CallbackQuery):
    if not moderator_id:
        await callback.answer("⛔ گرداننده هنوز تنظیم نشده.", show_alert=True)
        return
    mod_name = players.get(moderator_id, "❓")
    await callback.answer(f"👤 گرداننده فعلی: {mod_name}", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "change_mod")
async def change_moderator(callback: types.CallbackQuery):
    admins = await bot.get_chat_administrators(group_chat_id)
    kb = InlineKeyboardMarkup(row_width=1)
    for admin in admins:
        kb.add(InlineKeyboardButton(admin.user.full_name, callback_data=f"set_mod_{admin.user.id}"))

    await callback.message.edit_text("🔄 انتخاب گرداننده جدید:", reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("set_mod_"))
async def set_new_moderator(callback: types.CallbackQuery):
    global moderator_id
    new_id = int(callback.data.split("set_mod_")[1])
    moderator_id = new_id
    new_name = callback.from_user.full_name if callback.from_user.id == new_id else players.get(new_id, "❓")

    await callback.message.edit_text(f"✅ گرداننده جدید تنظیم شد: <b>{new_name}</b>", parse_mode="HTML")
    await callback.answer()

# =======================
# وضعیت چالش
# =======================
@dp.callback_query_handler(lambda c: c.data == "challenge_status")
async def challenge_status_pv(callback: types.CallbackQuery):
    # فقط برای پیوی
    if callback.message.chat.type != "private":
        return  # اگر در گروه است، هندلر قبلی لابی اجرا شود
    group_id = ...  # آیدی گروه مربوطه را از دیتابیس یا سیستم خود بگیرید
    # پیام جدید در پیوی
    await callback.message.answer(
        "⚔ وضعیت چالش:",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("⚔ آف کردن چالش", callback_data=f"turn_off_challenge_{group_id}")
        )
    )
    await callback.answer()
#=======================
# لیست بازیکنان
#=======================
async def list_players_pv(callback: types.CallbackQuery):
    # اگر در گروه زده شد، هندلر اصلی لابی کار کند
    if callback.message.chat.type != "private":
        return

    # گرفتن لیست بازیکنان از دیتابیس یا متغیر سراسری
    # فرضا players = {seat_number: {"id": user_id, "name": name}}
    players = get_players_for_group(callback.from_user.id)  # باید تابع خودت داشته باشی
    if not players:
        await callback.message.answer("⚠️ هیچ بازیکنی ثبت نشده است.")
        await callback.answer()
        return

    # مرتب سازی بر اساس شماره صندلی
    sorted_players = sorted(players.items(), key=lambda x: x[0])

    # ساخت متن پیام با منشن
    text = "👥 لیست بازیکنان:\n"
    for seat, player in sorted_players:
        text += f"{seat}. [{player['name']}](tg://user?id={player['id']})\n"

    # ارسال پیام در پیوی
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# ثبت هندلر در دیسپچر
def register_player_list_handler(dp: Dispatcher):
    dp.register_callback_query_handler(list_players_pv, lambda c: c.data == "list_players")
#=======================
# ارسال نقش ها
#=======================
async def send_roles_panel(callback: types.CallbackQuery, bot):
    group_id = get_group_for_admin(callback.from_user.id)  # تابع خودت برای پیدا کردن گروه
    players = players_in_game.get(group_id, {})

    if not players:
        await callback.message.answer("⚠️ هیچ بازیکنی ثبت نشده است.")
        await callback.answer()
        return

    # 1️⃣ ارسال نقش به هر بازیکن در پیوی
    for seat, player in players.items():
        roles = ", ".join(player.get("roles", []))
        text = f"🎭 نقش شما:\n{roles}\n\nشماره صندلی شما: {seat}"
        try:
            await bot.send_message(player["id"], text)
        except Exception as e:
            print(f"⚠️ خطا در ارسال نقش به {player['name']}: {e}")

    # 2️⃣ ارسال لیست بازیکنان همراه نقش‌ها برای گرداننده
    sorted_players = sorted(players.items(), key=lambda x: x[0])
    text = "🎭 لیست بازیکنان و نقش‌ها:\n"
    for seat, p in sorted_players:
        roles = ", ".join(p.get("roles", []))
        text += f"{seat}. [{p['name']}](tg://user?id={p['id']}) — {roles}\n"

    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer("✅ نقش‌ها ارسال شدند و لیست برای شما نمایش داده شد.")

# ثبت هندلر
def register_send_roles_handler(dp):
    dp.register_callback_query_handler(
        lambda c: send_roles_panel(c, dp.bot), 
        lambda c: c.data == "resend_roles"
    )




#=======================
# حذف بازیکن
#=======================
async def remove_player_handler(callback: types.CallbackQuery):
    group_id = get_group_for_admin(callback.from_user.id)
    current_players = players_in_game.get(group_id, {})
    if not current_players:
        await callback.message.answer("⚠️ هیچ بازیکنی در بازی نیست.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for seat, p in current_players.items():
        kb.add(InlineKeyboardButton(f"{seat}. {p['name']}", callback_data=f"remove_{seat}_{group_id}"))

    await callback.message.answer("🗑 بازیکنی که می‌خواهید حذف کنید را انتخاب کنید:", reply_markup=kb)

async def remove_player_confirm(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    seat = int(parts[1])
    group_id = int(parts[2])

    player = players_in_game[group_id].pop(seat, None)
    if not player:
        await callback.message.answer("⚠️ بازیکن پیدا نشد.")
        return

    removed_players.setdefault(group_id, {})[seat] = player
    await callback.message.answer(f"✅ بازیکن {player['name']} حذف شد و به لیست خارج شده‌ها منتقل شد.")
#=======================
# تولد بازیکن
#=======================
async def birthday_player_handler(callback: types.CallbackQuery):
    group_id = get_group_for_admin(callback.from_user.id)
    removed = removed_players.get(group_id, {})
    if not removed:
        await callback.message.answer("⚠️ هیچ بازیکنی در لیست خارج شده‌ها نیست.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for seat, p in removed.items():
        kb.add(InlineKeyboardButton(f"{seat}. {p['name']}", callback_data=f"revive_{seat}_{group_id}"))

    await callback.message.answer("🎂 بازیکنی که می‌خواهید بازگردانید را انتخاب کنید:", reply_markup=kb)

async def birthday_player_confirm(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    seat = int(parts[1])
    group_id = int(parts[2])

    player = removed_players[group_id].pop(seat, None)
    if not player:
        await callback.message.answer("⚠️ بازیکن پیدا نشد.")
        return

    players_in_game.setdefault(group_id, {})[seat] = player
    await callback.message.answer(f"✅ بازیکن {player['name']} با همان شماره صندلی و نقش بازگردانده شد.")

    
# ======================
# دستورات اصلی
# ======================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    if message.chat.type == "private":
        # منوی پیوی ربات
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🛠 مدیریت بازی", callback_data="manage_game"))
        kb.add(InlineKeyboardButton("⚙ مدیریت سناریو", callback_data="manage_scenarios"))
        
        
        # فقط مدیر ربات این دو دکمه را می‌بیند
        if message.from_user.id == moderator_id:
            kb.add(InlineKeyboardButton("🛠 مدیریت بازی", callback_data="manage_game"))
            kb.add(InlineKeyboardButton("⚙ مدیریت سناریو", callback_data="manage_scenarios"))

        kb.add(InlineKeyboardButton("📚 راهنما", callback_data="help"))

        await message.reply("📋 منوی ربات:", reply_markup=kb)

    else:
        # منوی گروه همان منوی اصلی گروه
        kb = main_menu_keyboard()  # همان منوی قبلی گروه
        await message.reply("🏠 منوی اصلی گروه:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "new_game")
async def start_game(callback: types.CallbackQuery):
    # محدودیت به گروه خاص
    if callback.message.chat.id != ALLOWED_GROUP_ID:
        await callback.answer("❌ این ربات فقط در گروه اصلی کار می‌کند.", show_alert=True)
        return

    global group_chat_id, lobby_active, admins, lobby_message_id

    # فقط در گروه: شروع لابی
    if callback.message.chat.type != "private":
        group_chat_id = callback.message.chat.id
        lobby_active = True    # فقط لابی فعال، بازی هنوز شروع نشده
        admins = {member.user.id for member in await bot.get_chat_administrators(group_chat_id)}

        msg = await callback.message.reply(
            "🎮 بازی مافیا فعال شد!\nلطفا سناریو و گرداننده را انتخاب کنید:",
            reply_markup=game_menu_keyboard()
        )
        lobby_message_id = msg.message_id

    await callback.answer()



# ======================
# انتخاب سناریو و گرداننده
# ======================
@dp.callback_query_handler(lambda c: c.data == "choose_scenario")
async def choose_scenario(callback: types.CallbackQuery):
    global lobby_active

    if not lobby_active:
        await callback.answer("❌ هیچ بازی فعالی برای انتخاب سناریو وجود ندارد.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(scen, callback_data=f"scenario_{scen}"))
    await callback.message.edit_text("📝 یک سناریو انتخاب کنید:", reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("scenario_"))
async def scenario_selected(callback: types.CallbackQuery):
    global selected_scenario
    selected_scenario = callback.data.replace("scenario_", "")
    await callback.message.edit_text(
        f"📝 سناریو انتخاب شد: {selected_scenario}\nحالا گرداننده را انتخاب کنید.",
        reply_markup=game_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "choose_moderator")
async def choose_moderator(callback: types.CallbackQuery):
    global lobby_active

    if not lobby_active:
        await callback.answer("❌ هیچ بازی فعالی برای انتخاب گرداننده وجود ندارد.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for admin_id in admins:
        member = await bot.get_chat_member(group_chat_id, admin_id)
        kb.add(InlineKeyboardButton(member.user.full_name, callback_data=f"moderator_{admin_id}"))
    await callback.message.edit_text("🎩 یک گرداننده انتخاب کنید:", reply_markup=kb)
    await callback.answer()




@dp.callback_query_handler(lambda c: c.data.startswith("moderator_"))
async def moderator_selected(callback: types.CallbackQuery):
    global moderator_id
    moderator_id = int(callback.data.replace("moderator_", ""))
    await callback.message.edit_text(
        f"🎩 گرداننده انتخاب شد: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name}\n"
        f"حالا اعضا می‌توانند وارد بازی شوند یا انصراف دهند.",
        reply_markup=join_menu()
    )
    await callback.answer()

# ======================
# ورود و انصراف
# ======================
@dp.callback_query_handler(lambda c: c.data == "join_game")
async def join_game_callback(callback: types.CallbackQuery):
    global players, player_slots

    user = callback.from_user

    # جلوگیری از ورود در حین بازی
    if game_running:
        await callback.answer("❌ بازی در جریان است. نمی‌توانید وارد شوید.", show_alert=True)
        return

    # جلوگیری از ورود دوباره بازیکن
    if user.id in players:
        await callback.answer("⚠️ شما از قبل در لیست هستید.", show_alert=True)
        return

    # ظرفیت سناریو
    if not selected_scenario:
        await callback.answer("⚠️ لطفاً اول سناریو انتخاب کنید.", show_alert=True)
        return

    max_players = len(scenarios[selected_scenario]["roles"])
    if len(player_slots) >= max_players:
        # اضافه به لیست رزرو
        if not any(w["id"] == user.id for w in waiting_list):
            waiting_list.append({"id": user.id, "name": user.full_name})
            await callback.answer("✅ شما به لیست رزرو اضافه شدید.")
        else:
            await callback.answer("⚠️ شما در لیست رزرو هستید.", show_alert=True)
    else:
        # ثبت در لیست اصلی
        players[user.id] = user.full_name
        # پیدا کردن اولین صندلی خالی
        for i in range(1, max_players + 1):
            if i not in player_slots:
                player_slots[i] = user.id
                break
        await callback.answer("✅ شما وارد بازی شدید.")

    await update_lobby()


# ===============================
# خروج از بازی
#================================
@dp.callback_query_handler(lambda c: c.data == "leave_game")
async def leave_game_callback(callback: types.CallbackQuery):
    global players, player_slots, waiting_list, waiting_message_id

    user_id = callback.from_user.id

    # جلوگیری از خروج در حین بازی
    if game_running:
        await callback.answer("❌ بازی در جریان است. نمی‌توانید خارج شوید.", show_alert=True)
        return

    # پیدا کردن صندلی بازیکن
    seat = next((s for s, uid in player_slots.items() if uid == user_id), None)
    if seat is None:
        await callback.answer("⚠️ شما در لیست اصلی نیستید.", show_alert=True)
        return

    # حذف بازیکن
    player_slots.pop(seat, None)
    players.pop(user_id, None)
    await callback.answer("❌ شما از بازی خارج شدید.")
    await update_lobby()

    # اگر لیست رزرو خالی نبود → جایگزین کن
    if waiting_list:
        sub = waiting_list.pop(0)
        player_slots[seat] = sub["id"]
        players[sub["id"]] = sub["name"]

        await bot.send_message(group_chat_id, f"♻️ {sub['name']} جایگزین شد (صندلی {seat}).")
        await update_lobby()

        # اگه لیست رزرو خالی شد → پیام رزرو رو حذف کن
        if not waiting_list and waiting_message_id:
            try:
                await bot.delete_message(group_chat_id, waiting_message_id)
            except:
                pass
            waiting_message_id = None

# ======================
# بروزرسانی لابی
# ======================
async def update_lobby():
    global lobby_message_id

    if not group_chat_id:
        return

    text = f"📋 <b>لیست بازی:</b>\n"
    text += f"سناریو: {selected_scenario or 'انتخاب نشده'}\n\n"

    # 👤 گرداننده
    if moderator_id:
        try:
            moderator = await bot.get_chat_member(group_chat_id, moderator_id)
            text += f"👤 گرداننده: {html.escape(moderator.user.full_name)}\n\n"
        except:
            text += "👤 گرداننده: انتخاب نشده\n\n"
    else:
        text += "👤 گرداننده: انتخاب نشده\n\n"

    # 👥 بازیکنان اصلی
    if players:
        for uid, name in players.items():
            seat = next((s for s, u in player_slots.items() if u == uid), None)
            seat_str = f" (صندلی {seat})" if seat else ""
            text += f"- <a href='tg://user?id={uid}'>{html.escape(name)}</a>{seat_str}\n"
    else:
        text += "هیچ بازیکنی وارد بازی نشده است.\n"

    kb = InlineKeyboardMarkup(row_width=5)

    if selected_scenario:
        max_players = len(scenarios[selected_scenario]["roles"])

        # 🎯 دکمه‌های صندلی
        for i in range(1, max_players + 1):
            if i in player_slots:
                player_name = players.get(player_slots[i], "❓")
                kb.insert(InlineKeyboardButton(f"{i} ({player_name})", callback_data=f"slot_{i}"))
            else:
                kb.insert(InlineKeyboardButton(str(i), callback_data=f"slot_{i}"))

        # 🎯 ورود/خروج یا غیرفعال شدن
        if len(player_slots) >= max_players:
            kb.row(
                InlineKeyboardButton("🚫 لیست پر شده", callback_data="full_list"),
                InlineKeyboardButton("❌ خروج از بازی", callback_data="leave_game"),
            )    
            
            # لیست رزرو
            if waiting_list:
                text += "\n\n📌 <b>لیست رزرو:</b>\n"
                for w in waiting_list:
                    text += f"- <a href='tg://user?id={w['id']}'>{html.escape(w['name'])}</a>\n"
            else:
                text += "\n\n📌 لیست رزرو خالی است."

            kb.row(
                InlineKeyboardButton("📝 رزرو", callback_data="join_waiting"),
                InlineKeyboardButton("❌ کنسل", callback_data="leave_waiting"),
            )
        else:
            kb.row(
                InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"),
                InlineKeyboardButton("❌ خروج از بازی", callback_data="leave_game"),
            )

        # ✅ نمایش لیست رزرو
    if waiting_list:
        text += "\n\n📋 لیست رزرو:\n"
        for i, w in enumerate(waiting_list, start=1):
            text += f"{i}. {w['name']}\n"
            
    # 🎭 پخش نقش
    if selected_scenario and moderator_id:
        min_players = scenarios[selected_scenario]["min_players"]
        max_players = len(scenarios[selected_scenario]["roles"])
        if min_players <= len(players) <= max_players:
            kb.add(InlineKeyboardButton("🎭 پخش نقش", callback_data="distribute_roles"))
         # 🎭 پخش نقش


    # 🚫 لغو بازی
    if moderator_id and moderator_id in admins:
        kb.add(InlineKeyboardButton("🚫 لغو بازی", callback_data="cancel_game"))

    # 🔄 بروزرسانی پیام
    try:
        await bot.edit_message_text(
            text, chat_id=group_chat_id, message_id=lobby_message_id,
            reply_markup=kb, parse_mode="HTML"
        )
    except (MessageNotModified, MessageCantBeEdited):
        # متن تغییری نکرده یا قابل ویرایش نیست → کاری نکن
        pass
    except MessageToEditNotFound:
        # پیام پاک شده یا پیدا نشد → پیام جدید بساز
        msg = await bot.send_message(group_chat_id, text, reply_markup=kb, parse_mode="HTML")
        lobby_message_id = msg.message_id


# ======================================
# ایجاد لیست رزرو
# ======================================
async def update_waiting_list_message():
    """
    پیام لیست رزرو را ایجاد یا آپدیت می‌کند.
    اگر لیست رزرو خالی شود، پیام حذف می‌شود.
    """
    global waiting_message_id, waiting_list, group_chat_id

    # اگر لیست رزرو خالی است → پیام را پاک کن (اگه وجود دارد) و تمام
    if not waiting_list:
        if waiting_message_id:
            try:
                await bot.delete_message(group_chat_id, waiting_message_id)
            except:
                pass
            waiting_message_id = None
        return

    # ساخت متن لیست رزرو
    text = "📢 <b>لیست رزرو</b>\n\n"
    for idx, item in enumerate(waiting_list, start=1):
        name = item.get("name", "❓")
        text += f"{idx}. {html.escape(name)}\n"

    text += "\nاگر می‌خواید جایگزین شوید، روی «💺 رزرو» بزنید.\nبرای انصراف از رزرو «❌ کنسل»."

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💺 رزرو", callback_data="reserve_waiting"),
        InlineKeyboardButton("❌ کنسل", callback_data="cancel_waiting")
    )

    # اگر قبلاً پیام وجود داشت → ویرایشش کن، در غیر این صورت ارسال جدید
    if waiting_message_id:
        try:
            await bot.edit_message_text(text, chat_id=group_chat_id, message_id=waiting_message_id,
                                        parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            # اگر ویرایش موفق نبود (مثلاً پیام پاک شده)، پیام جدید ارسال کن
            try:
                msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
                waiting_message_id = msg.message_id
                return
            except Exception:
                return
    else:
        try:
            msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=kb)
            waiting_message_id = msg.message_id
        except Exception:
            return

#==========================
# ورود به رزرو
#==========================
@dp.callback_query_handler(lambda c: c.data == "join_waiting")
async def join_waiting_handler(callback: types.CallbackQuery):
    user = callback.from_user

    # ✅ اگر داخل بازی هست → اجازه نداره بره رزرو
    if user.id in players:
        await callback.answer("❌ شما در لیست اصلی هستید و نمی‌توانید وارد رزرو شوید.", show_alert=True)
        return

    # ✅ اگر از قبل در رزرو هست → تکراری نره
    if any(w["id"] == user.id for w in waiting_list):
        await callback.answer("⚠️ شما قبلاً در لیست رزرو هستید.", show_alert=True)
        return

    # ✅ اضافه به رزرو
    waiting_list.append({"id": user.id, "name": user.full_name})
    await callback.answer("✅ شما به لیست رزرو اضافه شدید.", show_alert=True)

    await update_lobby()

# -------------------------
# کنسل رزرو (دکمه)
# -------------------------
@dp.callback_query_handler(lambda c: c.data == "leave_waiting")
async def leave_waiting_handler(callback: types.CallbackQuery):
    user = callback.from_user

    # ✅ بررسی وجود در رزرو
    before = len(waiting_list)
    waiting_list[:] = [w for w in waiting_list if w["id"] != user.id]

    if len(waiting_list) < before:
        await callback.answer("✅ شما از لیست رزرو خارج شدید.", show_alert=True)
    else:
        await callback.answer("⚠️ شما در لیست رزرو نبودید.", show_alert=True)

    await update_lobby()
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
        await show_roles_list(moderator_id)
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

    # نمایش لیست بازیکنان بر اساس نوبت صحبت
    await send_turn_order_list()

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
async def head_set_handler(callback: types.CallbackQuery):
    global turn_order, current_turn_index

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند سر صحبت را تعیین کند.", show_alert=True)
        return

    # صندلی انتخاب شده
    seat = int(callback.data.split("head_set_")[1])

    if seat not in player_slots:
        await callback.answer("⚠ این صندلی خالی است.", show_alert=True)
        return

    # ساخت ترتیب نوبت: بازیکن انتخاب‌شده اول، بقیه به ترتیب صندلی‌ها
    all_seats = sorted(player_slots.keys())
    start_index = all_seats.index(seat)
    turn_order = all_seats[start_index:] + all_seats[:start_index]

    current_turn_index = 0

    await callback.answer("✅ سر صحبت انتخاب شد!")

    # نمایش لیست بازیکنان به ترتیب نوبت صحبت
    await send_turn_order_list()

    # نمایش منوی شروع دور و چالش
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))
    if challenge_active:
        kb.add(InlineKeyboardButton("⚔ چالش روشن", callback_data="challenge_toggle"))
    else:
        kb.add(InlineKeyboardButton("⚔ چالش خاموش", callback_data="challenge_toggle"))

    await bot.send_message(group_chat_id, "🔧 حالا می‌توانید دور را شروع کنید:", reply_markup=kb)


# ======================
# شروع بازی و نوبت اول
# ======================
async def start_turn(seat, duration=DEFAULT_TURN_DURATION, is_challenge=False):
    """
    شروع نوبت برای یک seat (صندلی). این تابع:
    - پیام نوبت را در گروه می‌فرستد و پین می‌کند
    - کیبورد مناسب را می‌سازد
    - تایمر زنده را با countdown ایجاد می‌کند
    """
    global current_turn_message_id, turn_timer_task, challenge_mode

    if not group_chat_id:
        return

    # seat باید در player_slots باشد
    if seat not in player_slots:
        await bot.send_message(group_chat_id, f"⚠️ صندلی {seat} بازیکنی ندارد.")
        return

    user_id = player_slots[seat]
    player_name = players.get(user_id, "بازیکن")
    mention = f"<a href='tg://user?id={user_id}'>{html.escape(str(player_name))}</a>"

    # حالت چالش را تنظیم کن
    challenge_mode = bool(is_challenge)

    # unpin پیام قبلی اگر لازم
    #if current_turn_message_id:
        #try:
            #await bot.unpin_chat_message(group_chat_id, current_turn_message_id)
        #except:
            #pass

    text = f"⏳ {duration//60:02d}:{duration%60:02d}\n🎙 نوبت صحبت {mention} است. ({duration} ثانیه)"
    msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=turn_keyboard(seat, is_challenge))

    # تلاش برای پین کردن پیام جدید (اختیاری)
    #try:
        #await bot.pin_chat_message(group_chat_id, msg.message_id, disable_notification=True)
    #except:
        #pass

    #current_turn_message_id = msg.message_id

    # لغو تایمر قبلی
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # راه‌اندازی تایمر (task)
    turn_timer_task = asyncio.create_task(countdown(seat, duration, msg.message_id, is_challenge))

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


# ======================
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
