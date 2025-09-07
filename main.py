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


def turn_keyboard(player_id, is_challenge=False):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⏭ نکست", callback_data=f"next_{player_id}"))
    # در حالت چالش گزینه درخواست چالش نمایش داده نمی‌شود
    if not is_challenge:
        kb.add(InlineKeyboardButton("⚔ درخواست چالش", callback_data=f"challenge_request_{player_id}"))
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


# ======================
# شروع نوبت + تایمر
# ======================
async def start_turn(player_id, duration=DEFAULT_TURN_DURATION, is_challenge=False):
    """
    شروع نوبت (عادی یا چالش)
    - player_id: id بازیکنِ دارای نوبت (یا چالش)
    - duration: زمان به ثانیه
    - is_challenge: اگر True باشد، این نوبت یک نوبت چالش است (دیگر امکان درخواست چالش نیست)
    """
    global current_turn_message_id, turn_timer_task, challenge_mode

    # تنظیم حالت چالش
    challenge_mode = bool(is_challenge)

    # unpin پیام قبلی (اگر لازم)
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

    # لغو تایمر قبلی (اگر در حال اجرا بود)
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # راه‌اندازی تایمر زنده
    turn_timer_task = asyncio.create_task(countdown(player_id, duration, msg.message_id, is_challenge))


#تایمر چالش
async def countdown(player_id, duration, message_id, is_challenge=False):
    remaining = duration
    mention = f"<a href='tg://user?id={player_id}'>{players.get(player_id, 'بازیکن')}</a>"
    try:
        while remaining > 0:
            await asyncio.sleep(10)
            remaining -= 10
            new_text = f"⏳ {max(0, remaining)//60:02d}:{max(0, remaining)%60:02d}\n🎙 نوبت صحبت {mention} است. ({max(0, remaining)} ثانیه)"
            try:
                await bot.edit_message_text(new_text, chat_id=group_chat_id, message_id=message_id,
                                            parse_mode="HTML", reply_markup=turn_keyboard(player_id, is_challenge))
            except:
                pass
    except asyncio.CancelledError:
        # اگر تایمر از بیرون کنسل شد، ساکت باشیم
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
async def next_turn_callback(callback: types.CallbackQuery):
    global current_turn_index, challenge_mode, paused_main_player, paused_main_duration

    # id ای که داخل callback_data فرستاده شده
    user_id = int(callback.data.split("_", 1)[1])

    # اگر الان داشتیم یک نوبت چالش را می‌گذراندیم -> بعد از اتمام چالش، resume نوبت اصلی (اگر وجود داشته)
    if challenge_mode:
        challenge_mode = False
        await callback.answer("✅ نوبت چالش تمام شد. ادامه‌ی بازی انجام می‌شود.")
        if paused_main_player:
            # resume نوبت اصلی که قبل از چالش paused شده بود
            resume_id = paused_main_player
            resume_dur = paused_main_duration or DEFAULT_TURN_DURATION
            # پاک‌سازی متغیرهای paused
            paused_main_player = None
            paused_main_duration = None
            await start_turn(resume_id, duration=resume_dur, is_challenge=False)
            return
        else:
            # اگر هیچ paused_main نباشد، معمولاً next یعنی رفتن به نوبت بعدی؛ پایین ادامه بدهیم
            pass

    # اگر نوبت اصلی بود و برای این بازیکن pending "چالش بعد" ثبت شده است -> اول چالش اجرا شود
    if user_id in pending_challenges:
        challenger_id = pending_challenges.get(user_id)
        # حذف از pending
        try:
            del pending_challenges[user_id]
        except KeyError:
            pass
        await callback.answer("⚔ چالش ثبت‌شده اجرا می‌شود.", show_alert=True)
        # اجرای نوبت چالشِ چالش‌کننده
        await start_turn(challenger_id, duration=60, is_challenge=True)
        return

    # اگر نه، بریم سراغ بازیکن بعدی در turn_order
    current_turn_index += 1
    if current_turn_index >= len(turn_order):
        # پایان فاز روز (یا هر اکشن دلخواه)
        await bot.send_message(group_chat_id, "✅ همه بازیکنان صحبت کردند. فاز روز پایان یافت.")
        current_turn_index = 0
        return

    next_player_id = turn_order[current_turn_index]
    await start_turn(next_player_id, duration=DEFAULT_TURN_DURATION, is_challenge=False)
    await callback.answer()

#=======================
# درخواست چالش
#=======================

@dp.callback_query_handler(lambda c: c.data.startswith("challenge_request_"))
async def challenge_request(callback: types.CallbackQuery):
    if not game_running:
        await callback.answer("❌ بازی در جریان نیست.", show_alert=True)
        return

    challenger_id = callback.from_user.id
    # قالب: "challenge_request_{current_player_id}"
    current_player_id = int(callback.data.split("_", 2)[2])

    if challenger_id == current_player_id:
        await callback.answer("❌ نمی‌توانید به خودتان چالش بدهید.", show_alert=True)
        return

    if challenger_id not in players:
        await callback.answer("❌ فقط بازیکنان داخل بازی می‌توانند چالش بدهند.", show_alert=True)
        return

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(current_player_id, "بازیکن")

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⚔ چالش قبل صحبت", callback_data=f"challenge_before_{challenger_id}_{current_player_id}"))
    kb.add(InlineKeyboardButton("⚔ چالش بعد صحبت", callback_data=f"challenge_after_{challenger_id}_{current_player_id}"))
    kb.add(InlineKeyboardButton("🚫 چالش نمیدم", callback_data=f"challenge_none_{challenger_id}_{current_player_id}"))

    await bot.send_message(group_chat_id,
                           f"⚔ <b>{challenger_name}</b> درخواست چالش به <b>{target_name}</b> داده!\n\n"
                           "یک گزینه را انتخاب کنید:",
                           parse_mode="HTML",
                           reply_markup=kb)
    await callback.answer()

#===============
# انتخاب چالش
#===============

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


# ======================
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
