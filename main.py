import os
import json
import random
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

# ======================
# تنظیمات ربات
# ======================
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise ValueError("API_TOKEN environment variable is not set!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# ======================
# متغیرهای سراسری
# ======================
players = {}                # بازیکنان: {user_id: name}
moderator_id = None         # آیدی گرداننده
selected_scenario = None    # سناریوی انتخابی
scenarios = {}              # لیست سناریوها
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
pending_challenges = {}
challenge_mode = False      # آیا الان در حالت نوبت چالش هستیم؟
paused_main_player = None   # اگر چالش "قبل" ثبت شد، اینجا id نوبت اصلی ذخیره می‌شود تا بعد از چالش resume شود
paused_main_duration = None # (اختیاری) مدت زمان نوبت اصلی برای resume — معمولا 120
DEFAULT_TURN_DURATION = 120  # مقدار پیش‌فرض نوبت اصلی (در صورت تمایل تغییر بده)
challenges = {}  # {player_id: {"type": "before"/"after", "challenger": user_id}}
challenge_active = False
game_running = False
roles = {}  # نقش‌ها به هر بازیکن
leader_id = None  #سر صحبت
turn_order = []  #لنتخاب سر صحبت
challenge_disabled = False
challenge_disabled_permanent = False
selected_head = None  # بازیکن سر صحبت
talk_order = []       # ترتیب نوبت‌ها
current_turn_index = 0  # ایندکس نفر فعلی در talk_order
spoken_players = set()
turn_start_time = None
game_phase = "day"  # یا "night"



# ======================
# لود سناریوها
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

def lobby_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎭 پخش نقش‌ها", callback_data="distribute_roles"),
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator"),
    )
    kb.add(
        InlineKeyboardButton("🚪 خروج", callback_data="leave_game"),
        InlineKeyboardButton("❌ لغو بازی", callback_data="cancel_game"),
    )
    return kb


# ======================
# انتخاب / لغو انتخاب صندلی
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("slot_"))
async def handle_slot(callback: types.CallbackQuery):
    global player_slots
    if not selected_scenario:
        await callback.answer("❌ هنوز سناریویی انتخاب نشده.", show_alert=True)
        return
    
    slot_num = int(callback.data.replace("slot_", ""))
    user_id = callback.from_user.id

    # اگه همون بازیکن دوباره بزنه → لغو انتخاب
    if slot_num in player_slots and player_slots[slot_num] == user_id:
        del player_slots[slot_num]
        await callback.answer(f"جایگاه {slot_num} آزاد شد ✅")
    else:
        # اگه جایگاه پر باشه
        if slot_num in player_slots:
            await callback.answer("❌ این جایگاه قبلاً انتخاب شده.", show_alert=True)
            return
        # اگه بازیکن قبلاً جای دیگه نشسته → اون رو آزاد کن
        for s, uid in list(player_slots.items()):
            if uid == user_id:
                del player_slots[s]
        player_slots[slot_num] = user_id
        await callback.answer(f"شما جایگاه {slot_num} را انتخاب کردید ✅")

    await update_lobby()


def turn_keyboard(player_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⏭ نکست", callback_data=f"next_{player_id}"))
    kb.add(InlineKeyboardButton("⚔ درخواست چالش", callback_data=f"challenge_request_{player_id}"))
    return kb

def game_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"),
        InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator")
    )
    kb.add(
        InlineKeyboardButton("🎮 شروع بازی", callback_data="start_round")
    )
    return kb


# ======================
# دستورات اصلی
# ======================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.reply("🏠 منوی اصلی:", reply_markup=main_menu_keyboard())

@dp.callback_query_handler(lambda c: c.data == "new_game")
async def start_game(callback: types.CallbackQuery):
    global group_chat_id, lobby_active, admins, lobby_message_id
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
async def set_moderator(callback: types.CallbackQuery):
    global moderator_id
    moderator_id = int(callback.data.split("_")[1])
    
    # پیام به‌روزرسانی می‌شود تا انتخاب مشخص شود
    member = await bot.get_chat_member(group_chat_id, moderator_id)
    await callback.message.edit_text(
        f"🎩 گرداننده انتخاب شد: {member.user.full_name}"
    )
    await callback.answer("✅ گرداننده تنظیم شد!")



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

#===============
# پخش نقش
#===============

@dp.callback_query_handler(lambda c: c.data == "distribute_roles")
async def distribute_roles(callback: types.CallbackQuery):
    global game_running, roles, players, moderator_id

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند نقش‌ها را پخش کند.", show_alert=True)
        return

    if game_running:
        await callback.answer("❌ نقش‌ها قبلاً پخش شده‌اند.", show_alert=True)
        return

    if not players:
        await callback.answer("❌ هیچ بازیکنی در بازی وجود ندارد.", show_alert=True)
        return

    # فعال کردن وضعیت بازی
    game_running = True
    pending_challenges.clear()
    paused_main_player = None
    paused_main_challenger = None

    # پخش نقش‌ها (اینجا منطق واقعی نقش‌پخشیت باید بیاد)
    for uid, name in players.items():
        role = "🔑 نقش تستی"  # TODO: منطق واقعی نقش‌ها
        try:
            await bot.send_message(uid, f"🎭 نقش شما: {role}")
        except:
            pass  # اگر استارت نکرده بود

    # منوی بعد از پخش نقش‌ها
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎯 انتخاب سر صحبت", callback_data="choose_leader"),
        InlineKeyboardButton("⚔ چالش آف", callback_data="challenge_off"),
        InlineKeyboardButton("🛡 تک‌چالش آف", callback_data="single_challenge_off"),
    )
    kb.add(InlineKeyboardButton("▶ شروع دور", callback_data="start_round"))

    await callback.message.edit_text(
        "🚀 نقش‌ها پخش شدند!\n\n"
        "🎭 نقش‌ها در پیوی بازیکنان ارسال شدند.\n"
        "برای دیدن نقش خودتون به پیوی ربات برید.\n\n"
        "📌 هرکی نقششو گرفت لایک کنه.\n"
        "❗ اگر نقشتون نیومد احتمالاً ربات رو استارت نکردید.",
        reply_markup=kb
    )

    await callback.answer()


# انتخاب سر صحبت
@dp.callback_query_handler(lambda c: c.data == "choose_leader")
async def choose_leader(callback: types.CallbackQuery):
    global players

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند سر صحبت را انتخاب کند.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🎲 انتخاب تصادفی", callback_data="random_leader"))

    for uid, name in players.items():
        kb.add(InlineKeyboardButton(name, callback_data=f"set_leader_{uid}"))

    await callback.message.edit_text("👑 یک نفر را به عنوان سر صحبت انتخاب کنید:", reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("set_leader_"))
async def set_leader(callback: types.CallbackQuery):
    global leader_id, turn_order, players

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند سر صحبت را انتخاب کند.", show_alert=True)
        return

    leader_id = int(callback.data.split("_")[2])

    # ترتیب نوبت‌ها از سر صحبت تا آخر + ادامه لیست
    all_players = list(players.keys())
    idx = all_players.index(leader_id)
    turn_order = all_players[idx:] + all_players[:idx]

    await callback.message.edit_text(
        f"👑 <b>{players[leader_id]}</b> به عنوان سر صحبت انتخاب شد.\n"
        "✅ ترتیب نوبت‌ها بر اساس انتخاب مشخص گردید.",
        reply_markup=start_round_keyboard()
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "random_leader")
async def random_leader(callback: types.CallbackQuery):
    global leader_id, turn_order, players

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند سر صحبت را انتخاب کند.", show_alert=True)
        return

    leader_id = random.choice(list(players.keys()))

    all_players = list(players.keys())
    idx = all_players.index(leader_id)
    turn_order = all_players[idx:] + all_players[:idx]

    await callback.message.edit_text(
        f"🎲 به صورت تصادفی 👑 <b>{players[leader_id]}</b> سر صحبت شد.\n"
        "✅ ترتیب نوبت‌ها مشخص گردید.",
        reply_markup=start_round_keyboard()
    )
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "challenge_off")
async def challenge_off(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند این گزینه را تغییر دهد.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🔕 برای این دور", callback_data="off_this_round"),
        InlineKeyboardButton("🔕 برای کل بازی", callback_data="off_all_game"),
    )
    await callback.message.edit_text("⚔ چالش را برای کدام حالت غیرفعال می‌کنید؟", reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data in ["off_this_round", "off_all_game"])
async def confirm_challenge_off(callback: types.CallbackQuery):
    global challenge_disabled, challenge_disabled_permanent

    if callback.data == "off_this_round":
        challenge_disabled = True
        challenge_disabled_permanent = False
        msg = "🔕 چالش برای این دور غیرفعال شد."
    else:
        challenge_disabled = True
        challenge_disabled_permanent = True
        msg = "🔕 چالش برای کل بازی غیرفعال شد."

    await update_main_game_menu(callback.message, msg)
    await callback.answer()
    
# ======================
# تک چالش آف
# ======================
@dp.callback_query_handler(lambda c: c.data == "single_challenge_off")
async def single_challenge_off(callback: types.CallbackQuery):
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند این گزینه را تغییر دهد.", show_alert=True)
        return

    global unlimited_challenges
    unlimited_challenges = not unlimited_challenges  # سوییچ بین فعال/غیرفعال

    if unlimited_challenges:
        msg = "♾ حالت «تک چالش آف» فعال شد.\nبازیکنان می‌توانند بی‌نهایت چالش داشته باشند."
    else:
        msg = "✅ حالت «تک چالش آف» غیرفعال شد.\nهر بازیکن فقط یک چالش دارد."

    await update_main_game_menu(callback.message, msg)
    await callback.answer()


# ======================
# شروع دور صحبت
# ======================
@dp.callback_query_handler(lambda c: c.data == "start_round")
async def start_round(callback: types.CallbackQuery):
    global talk_order, current_turn_index

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند دور را شروع کند.", show_alert=True)
        return

    if not selected_head:
        await callback.answer("⚠️ ابتدا سر صحبت را انتخاب کنید.", show_alert=True)
        return

    # ساخت ترتیب نوبت‌ها
    player_ids = list(players.keys())
    start_index = player_ids.index(selected_head)
    talk_order = player_ids[start_index:] + player_ids[:start_index]
    current_turn_index = 0

    await callback.message.edit_text("▶️ دور صحبت‌ها شروع شد!\n"
                                     f"🎙 اولین نفر: {players[selected_head]}")

# ======================
# نکست → رفتن به نوبت بعدی
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("next_"))
async def next_turn(callback: types.CallbackQuery):
    global current_turn_index, turn_timer_task

    player_id = int(callback.data.split("_")[1])

    # فقط گرداننده یا خود بازیکن مجاز هستند
    if callback.from_user.id not in [moderator_id, player_id]:
        await callback.answer("❌ فقط گرداننده یا بازیکن نوبت‌دار می‌توانند نکست بزنند.", show_alert=True)
        return

    # توقف تایمر فعلی
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    current_turn_index += 1

    if current_turn_index >= len(talk_order):
        # همه صحبت کردند → پایان دور
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🌙 پایان روز", callback_data="end_day"))
        await callback.message.edit_text("✅ دور صحبت‌ها به پایان رسید.", reply_markup=kb)
        await callback.answer()
        return


    # شروع نوبت بعدی
    next_player = talk_order[current_turn_index]
    await start_turn(next_player, duration=120)

    await callback.answer()



# ======================
# ورود و انصراف
# ======================
@dp.callback_query_handler(lambda c: c.data == "join_game")
async def join_game_callback(callback: types.CallbackQuery):
    user = callback.from_user

    # جلوگیری از ورود در حین بازی
    if game_running:
        await callback.answer("❌ بازی در جریان است. نمی‌توانید وارد شوید.", show_alert=True)
        return

    # جلوگیری از ورود گرداننده
    if user.id == moderator_id:
        await callback.answer("❌ گرداننده نمی‌تواند وارد بازی شود.", show_alert=True)
        return

    # جلوگیری از ورود دوباره بازیکن
    if user.id in players:
        await callback.answer("❌ شما در لیست بازی هستید!", show_alert=True)
        return

    players[user.id] = user.full_name
    await update_lobby()
    await callback.answer("✅ شما به بازی اضافه شدید!")


@dp.callback_query_handler(lambda c: c.data == "leave_game")
async def leave_game_callback(callback: types.CallbackQuery):
    user = callback.from_user

    # جلوگیری از خروج در حین بازی
    if game_running:
        await callback.answer("❌ بازی در جریان است. نمی‌توانید خارج شوید.", show_alert=True)
        return

    if user.id not in players:
        await callback.answer("❌ شما در لیست بازی نیستید!", show_alert=True)
        return

    players.pop(user.id)

    # آزاد کردن صندلی اگر انتخاب کرده بود
    for slot, uid in list(player_slots.items()):
        if uid == user.id:
            del player_slots[slot]

    await update_lobby()
    await callback.answer("✅ شما از بازی خارج شدید!")


# ======================
# بروزرسانی لابی
# ======================
async def update_lobby():
    global lobby_message_id
    if not group_chat_id or not lobby_message_id:
        return

    text = f"📋 **لیست بازی:**\n"
    text += f"سناریو: {selected_scenario or 'انتخاب نشده'}\n"
    text += f"گرداننده: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name if moderator_id else 'انتخاب نشده'}\n\n"

    if players:
        for uid, name in players.items():
            text += f"- {name}\n"
    else:
        text += "هیچ بازیکنی وارد بازی نشده است.\n"

    kb = InlineKeyboardMarkup(row_width=5)

    # ✅ دکمه‌های انتخاب صندلی
    if selected_scenario:
        max_players = len(scenarios[selected_scenario]["roles"])
        for i in range(1, max_players + 1):
            if i in player_slots:
                # اگه صندلی پر باشه → نمایش نام بازیکن
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
            kb.add(InlineKeyboardButton("▶ شروع بازی", callback_data="start_play"))
        elif len(players) > max_players:
            text += "\n⚠️ تعداد بازیکنان بیش از ظرفیت این سناریو است."

    # 🔄 بروزرسانی پیام لابی
    await bot.edit_message_text(
        text,
        chat_id=group_chat_id,
        message_id=lobby_message_id,
        reply_markup=kb,
        parse_mode="Markdown"
    )


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
    paused_main_challenger = None
    spoken_players.clear()
    turn_start_time = None

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



# ======================
# شروع بازی و نوبت اول
# ======================
@dp.callback_query_handler(lambda c: c.data == "start_play")
async def start_play(callback: types.CallbackQuery):
    global game_running, lobby_active, turn_order, current_turn_index, group_chat_id

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
        return

    if not group_chat_id:
        group_chat_id = callback.message.chat.id

    roles = scenarios[selected_scenario]["roles"]
    if len(players) < len(roles):
        await callback.answer(f"❌ تعداد بازیکنان کافی نیست! حداقل {len(roles)} نفر نیاز است.", show_alert=True)
        return

    # بازی واقعاً شروع شد
    game_running = True
    lobby_active = False

    shuffled_roles = random.sample(roles, len(players))
    player_ids = list(players.keys())
    turn_order = player_ids.copy()
    random.shuffle(turn_order)
    current_turn_index = 0
    paused_main_player = None
    paused_main_challenger = None
    spoken_players.clear()
    turn_start_time = None

    # ارسال نقش‌ها به بازیکنان
    for pid, role in zip(player_ids, shuffled_roles):
        try:
            await bot.send_message(pid, f"🎭 نقش شما: {role}")
        except:
            if moderator_id:
                await bot.send_message(moderator_id, f"⚠ نمی‌توانم نقش را به {players[pid]} ارسال کنم.")

    if moderator_id:
        text = "📜 نقش‌ها برای بازیکنان:\n"
        for pid, role in zip(player_ids, shuffled_roles):
            text += f"{players[pid]} → {role}\n"
        await bot.send_message(moderator_id, text)

    await callback.answer("✅ بازی شروع شد!")

    if turn_order:
        await start_turn(turn_order[0])


# ======================
# نکست نوبت
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("next_turn_"))
async def next_turn_callback(callback: types.CallbackQuery):
    global current_turn_index, turn_order, turn_timer_task

    if turn_timer_task:
        turn_timer_task.cancel()

    player_id = int(callback.data.replace("next_turn_", ""))

    if callback.from_user.id != moderator_id and callback.from_user.id != player_id:
        await callback.answer("❌ فقط بازیکن یا گرداننده می‌تواند نوبت را پایان دهد.", show_alert=True)
        return

    current_turn_index += 1
    if current_turn_index < len(turn_order):
        await start_turn(turn_order[current_turn_index])
    else:
        if not group_chat_id:
            await callback.answer("⚠ شناسه گروه پیدا نشد.", show_alert=True)
            return
        await bot.send_message(group_chat_id, "✅ همه بازیکنان صحبت کردند. فاز روز پایان یافت.")

    await callback.answer()

def turn_keyboard(player_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⏭ نکست", callback_data=f"next_{player_id}"))
    kb.add(InlineKeyboardButton("⚔ درخواست چالش", callback_data=f"challenge_request_{player_id}"))
    return kb

def start_round_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("▶ شروع دور", callback_data="start_round")
    )
    return kb


# ======================
# شروع نوبت + تایمر
# ======================
async def start_turn(player_id, duration=DEFAULT_TURN_DURATION, is_challenge=False):
    """
    شروع نوبت (عادی یا چالش)
    - is_challenge=True => دکمه درخواست چالش نمایش داده نمی‌شود
    """
    global current_turn_message_id, turn_timer_task, paused_main_player, paused_main_challenger, turn_start_time, spoken_players
    
    # اگر این نوبت یک نوبت عادی است، spoken_players را پاک کن و زمان شروع را ثبت کن
    if not is_challenge:
        spoken_players.clear()
        # ثبت زمان شروع نوبت (به ثانیه از loop.time())
        turn_start_time = asyncio.get_event_loop().time()
    else:
        
        # در نوبت چالش اجازه‌ی درخواست چالش ندهیم — زمان را None می‌کنیم
        turn_start_time = None
        
    # اگر پیام قبلی پین شده بود، آنپین کن (اختیاری)
    if current_turn_message_id:
        try:
            await bot.unpin_chat_message(group_chat_id, current_turn_message_id)
        except:
            pass

    mention = f"<a href='tg://user?id={player_id}'>{players.get(player_id, 'بازیکن')}</a>"
    text = f"⏳ {duration//60:02d}:{duration%60:02d}\n🎙 نوبت صحبت {mention} است. ({duration} ثانیه)"
    msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=turn_keyboard(player_id, is_challenge))

    try:
        await bot.pin_chat_message(group_chat_id, msg.message_id, disable_notification=True)
    except:
        pass

    current_turn_message_id = msg.message_id

    # لغو تایمر قبلی اگر وجود داشت
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # راه‌اندازی تایمر زنده
    turn_timer_task = asyncio.create_task(countdown(player_id, duration, msg.message_id, is_challenge))



#تایمر چالش
async def countdown(player_id, duration, message_id, is_challenge=False):
    remaining = duration
    mention = f"<a href='tg://user?id={player_id}'>{players.get(player_id,'بازیکن')}</a>"
    try:
        while remaining > 0:
            await asyncio.sleep(10)
            remaining -= 10
            new_text = f"⏳ {max(0,remaining)//60:02d}:{max(0,remaining)%60:02d}\n🎙 نوبت صحبت {mention} است. ({max(0,remaining)} ثانیه)"
            try:
                await bot.edit_message_text(new_text, chat_id=group_chat_id, message_id=message_id,
                                            parse_mode="HTML", reply_markup=turn_keyboard(player_id, is_challenge))
            except:
                pass
    except asyncio.CancelledError:
        return

# ======================
# شروع بازی و نوبت اول
# ======================
@dp.callback_query_handler(lambda c: c.data == "start_play")
async def start_play(callback: types.CallbackQuery):
    global turn_order, current_turn_index, group_chat_id

    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
        return

    if not group_chat_id:
        group_chat_id = callback.message.chat.id

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
            if moderator_id:
                await bot.send_message(moderator_id, f"⚠ نمی‌توانم نقش را به {players[pid]} ارسال کنم.")

    if moderator_id:
        text = "📜 نقش‌ها برای بازیکنان:\n"
        for pid, role in zip(player_ids, shuffled_roles):
            text += f"{players[pid]} → {role}\n"
        await bot.send_message(moderator_id, text)

    await callback.answer("✅ بازی شروع شد!")

    if turn_order:
        await start_turn(turn_order[0])


# ======================
# نکست نوبت
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("next_"))
async def next_turn(callback: types.CallbackQuery):
    global current_turn_index, turn_timer_task, paused_main_player, paused_main_challenger, pending_challenges

    player_id = int(callback.data.split("_", 1)[1])

    # فقط گرداننده یا خود بازیکن مجازند
    if callback.from_user.id not in [moderator_id, player_id]:
        await callback.answer("❌ فقط گرداننده یا بازیکن نوبت‌دار می‌توانند نکست بزنند.", show_alert=True)
        return

    # توقف تایمر فعلی
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # 1) اگر قبلاً یک "چالش قبل" اتفاق افتاده و حالا چالش‌کننده نوبتش رو تمام کرده => resume نوبت اصلی
    if paused_main_player is not None:
        # فقط چالش‌کننده یا گرداننده می‌توانند پایان چالش را تایید کنند
        if callback.from_user.id == paused_main_challenger or callback.from_user.id == moderator_id:
            target_to_resume = paused_main_player
            paused_main_player = None
            paused_main_challenger = None
            await callback.answer("✅ چالش تمام شد — نوبت اصلی ادامه می‌یابد.")
            await start_turn(target_to_resume, duration=DEFAULT_TURN_DURATION, is_challenge=False)
            return
        else:
            await callback.answer("❌ فقط گرداننده یا چالش‌کننده می‌توانند چالش را پایان دهند.", show_alert=True)
            return

    # 2) اگر برای این بازیکن یک چالش 'بعد' ثبت شده است => اجرا کن
    if player_id in pending_challenges:
        challenger = pending_challenges.pop(player_id)
        await callback.answer("⚔ نوبت چالش اجرا می‌شود.", show_alert=True)
        await start_turn(challenger, duration=60, is_challenge=True)
        return

    # 3) در غیر این صورت، بریم سراغ نفر بعدی در talk_order (یا خودِ لیست نوبتت)
    # فرض: تو از متغیر talk_order یا turn_order برای ترتیب استفاده می‌کنی
    if player_id in talk_order:
        idx = talk_order.index(player_id)
        next_index = idx + 1
    else:
        # fallback به current_turn_index
        next_index = current_turn_index + 1

    if next_index >= len(talk_order):
        await bot.send_message(group_chat_id, "✅ دور صحبت‌ها به پایان رسید.")
        current_turn_index = 0
        await callback.answer()
        return

    current_turn_index = next_index
    next_player = talk_order[current_turn_index]
    await start_turn(next_player, duration=DEFAULT_TURN_DURATION, is_challenge=False)
    await callback.answer()


#=======================
# درخواست چالش
#=======================
@dp.callback_query_handler(lambda c: c.data.startswith("challenge_request_"))
async def challenge_request(callback: types.CallbackQuery):
    # گرفتن id صاحب نوبت از callback data
    target_id = int(callback.data.split("_", 2)[2])
    challenger_id = callback.from_user.id
    # شرایط اولیه
    if challenger_id == target_id:
        await callback.answer("❌ نمی‌توانید به خودتان چالش دهید.", show_alert=True)
        return
    if challenger_id not in players:
        await callback.answer("❌ فقط بازیکنان داخل بازی می‌توانند چالش دهند.", show_alert=True)
        return
    if not game_running:
        await callback.answer("❌ بازی در جریان نیست.", show_alert=True)
        return
        # شرط ۲: مهلت ۶۰ ثانیه از شروع نوبت (اگر بیش از 60s گذشته باشد، پذیرش درخواست ممنوع)
    if turn_start_time is not None:
        now = asyncio.get_event_loop().time()
        if now - turn_start_time > 60:
            await callback.answer("⏳ مهلت درخواست چالش (۶۰ ثانیه) به پایان رسیده است.", show_alert=True)
            return    
    # (اگر از قبل چالش‌ها غیرفعال کرده باشی، میتونی چک کنی؛ در غیر این صورت خط را حذف کن)
    if 'challenge_disabled' in globals() and challenge_disabled:
        await callback.answer("❌ در این دور چالش غیرفعال است.", show_alert=True)
        return

    challenger_id = callback.from_user.id
    # callback_data: "challenge_request_{target_id}"
    target_id = int(callback.data.split("_", 2)[2])

    if challenger_id == target_id:
        await callback.answer("❌ نمی‌توانید به خودتان چالش بدهید.", show_alert=True)
        return
    if challenger_id not in players:
        await callback.answer("❌ فقط بازیکنان داخل بازی می‌توانند چالش دهند.", show_alert=True)
        return

    
    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    kb = InlineKeyboardMarkup(row_width=1)
    # فقط اگر صاحب نوبت هنوز حرف نزده باشد، گزینه "چالش قبل" را نمایش بده
    if target_id not in spoken_players: 
        kb.add(InlineKeyboardButton("⚔ چالش قبل صحبت", callback_data=f"challenge_before_{challenger_id}_{target_id}"))
   
    kb.add(InlineKeyboardButton("⚔ چالش بعد صحبت", callback_data=f"challenge_after_{challenger_id}_{target_id}"))
    kb.add(InlineKeyboardButton("🚫 چالش نمیدم", callback_data=f"challenge_none_{challenger_id}_{target_id}"))

    await bot.send_message(group_chat_id,
                           f"⚔ <b>{challenger_name}</b> درخواست چالش به <b>{target_name}</b> داده!\n\n"
                           "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
                           parse_mode="HTML",
                           reply_markup=kb)
    await callback.answer()



#======================
# انتخاب نوع چالش
#======================

@dp.callback_query_handler(lambda c: c.data.startswith("challenge_"))
async def challenge_choice(callback: types.CallbackQuery):
    global pending_challenges, paused_main_player, paused_main_challenger, turn_timer_task

    parts = callback.data.split("_")
    # قالب: challenge_before_{challenger}_{target}  یا challenge_after_{challenger}_{target} یا challenge_none_{challenger}_{target}
    action = parts[1]
    challenger_id = int(parts[2])
    target_id = int(parts[3])

    challenger_name = players.get(challenger_id, "چالش‌کننده")
    target_name = players.get(target_id, "بازیکن")
    
    if callback.from_user.id != target_id:
        await callback.answer("❌ فقط بازیکن نوبت‌دار می‌تواند انتخاب کند.", show_alert=True)
        return
        
    if action == "before":
        # pause نوبت اصلی و اجرای فوری چالشِ یک دقیقه‌ای توسط challenger
        paused_main_player = target_id
        paused_main_challenger = challenger_id

        # لغو تایمر فعلی (تا نوبت اصلی متوقف شود)
        if turn_timer_task and not turn_timer_task.done():
            turn_timer_task.cancel()

        await bot.send_message(group_chat_id, f"⚔ چالش قبل: <b>{challenger_name}</b> یک دقیقه صحبت می‌کند.", parse_mode="HTML")
        await start_turn(challenger_id, duration=60, is_challenge=True)
        await callback.answer("✅ چالش قبل اجرا شد.", show_alert=True)

    elif action == "after":
        # ثبت در pending_challenges تا وقتی نوبت main تمام شد اجرا شود
        pending_challenges[target_id] = challenger_id
        await callback.message.edit_text(f"⚔ {players[target_id]} چالش بعد را به {players[challenger_id]} ثبت کرد.")
        await callback.answer()

    elif action == "none":
        await callback.message.edit_text(f"✅ {players[target_id]} تصمیم گرفت هیچ چالشی انجام نشود.")
        await callback.answer()


#========================
# انتخاب چالش
#========================

@dp.callback_query_handler(lambda c: c.data.startswith("challenge_"))
async def challenge_choice(callback: types.CallbackQuery):
    global paused_main_player, paused_main_duration

    parts = callback.data.split("_")
    # parts = ["challenge", "before"/"after"/"none", challenger_id, target_id]
    action = parts[1]
    challenger_id = int(parts[2])
    target_id = int(parts[3])

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    if action == "before":
        # اگر الان یک نوبت اصلی در حال اجراست، آن را pause می‌کنیم و چالش‌کننده یک دقیقه صحبت می‌کند
        # ذخیره‌ی نوبت اصلی برای resume بعد از چالش
        paused_main_player = target_id
        paused_main_duration = DEFAULT_TURN_DURATION

        # لغو تایمر فعلی (تا نوبت اصلی متوقف شود)
        if turn_timer_task and not turn_timer_task.done():
            turn_timer_task.cancel()

        await bot.send_message(group_chat_id, f"⚔ چالش قبل: <b>{challenger_name}</b> یک دقیقه صحبت می‌کند.", parse_mode="HTML")
        await start_turn(challenger_id, duration=60, is_challenge=True)

    elif action == "after":
        # ثبت برای اجرا بعد از پایان نوبت اصلی
        pending_challenges[target_id] = challenger_id
        await bot.send_message(group_chat_id, f"⚔ چالش بعد برای <b>{target_name}</b> ثبت شد (چالش‌کننده: {challenger_name}).", parse_mode="HTML")

    elif action == "none":
        await bot.send_message(group_chat_id, f"🚫 {challenger_name} از ارسال چالش منصرف شد.", parse_mode="HTML")

    await callback.answer()

@dp.message_handler()  # این handler عمومی است؛ اگر handlerهای دقیق‌تر داری، این را بعد از آنها قرار بده
async def detect_speaking(message: types.Message):
    # فقط پیام‌های داخل گروه بازی را بررسی کن
    try:
        if message.chat.id != group_chat_id:
            return
    except:
        return

    # اگر بازی در جریان است و talk_order/ current_turn_index منطقی است:
    if not game_running:
        return

    # ایندکس و نفر فعلی را بدست آور
    if not talk_order:
        return
    if current_turn_index < 0 or current_turn_index >= len(talk_order):
        return

    current_player = talk_order[current_turn_index]
    # اگر صاحب نوبت پیام فرستاد -> به مجموعه spoken_players اضافه کن
    if message.from_user.id == current_player:
        spoken_players.add(current_player)


@dp.callback_query_handler(lambda c: c.data == "end_day")
async def end_day(callback: types.CallbackQuery):
    global game_phase
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند روز را تمام کند.", show_alert=True)
        return

    game_phase = "night"

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("☀ اعلام روز", callback_data="start_day"))

    msg = (
        "🌙 روز تموم شد\n"
        "🌌 شب میشه\n\n"
        "🕵 گروه مافیا به گروهشون برن\n"
        "🎭 نقش‌دارها به پیوی گرداننده برن\n\n"
        "🚫 در فاز شب در گروه تایپ نکنید\n"
        "❌ در صورت تایپ اخطار و کیک خواهید شد."
    )

    # قفل کردن گروه (گرفتن دسترسی نوشتن از همه)
    try:
        await bot.set_chat_permissions(
            group_chat_id,
            types.ChatPermissions(can_send_messages=False)
        )
    except Exception as e:
        print("خطا در قفل گروه:", e)

    await callback.message.edit_text(msg, reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "start_day")
async def start_day(callback: types.CallbackQuery):
    global game_phase
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند روز را اعلام کند.", show_alert=True)
        return

    game_phase = "day"

    # باز کردن گروه
    try:
        await bot.set_chat_permissions(
            group_chat_id,
            types.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
    except Exception as e:
        print("خطا در باز کردن گروه:", e)

    await callback.message.edit_text("☀ روز جدید آغاز شد. همه می‌توانند صحبت کنند.")
    await callback.answer()


# ======================
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
