import os
import json
import random
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import html

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
pending_challenges = {}
challenge_mode = False      # آیا الان در حالت نوبت چالش هستیم؟
paused_main_player = None   # اگر چالش "قبل" ثبت شد، اینجا id نوبت اصلی ذخیره می‌شود تا بعد از چالش resume شود
paused_main_duration = None # (اختیاری) مدت زمان نوبت اصلی برای resume — معمولا 120
DEFAULT_TURN_DURATION = 120  # مقدار پیش‌فرض نوبت اصلی (در صورت تمایل تغییر بده)
challenges = {}  # {player_id: {"type": "before"/"after", "challenger": user_id}}
challenge_active = False
post_challenge_advance = False   # وقتی اجرای چالش 'بعد' باشه، بعد از چالش به نوبت بعدی می‌رویم


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
        player_id = player_slots.get(seat)
        if player_id:
            kb.add(InlineKeyboardButton("⚔ درخواست چالش", callback_data=f"challenge_request_{seat}"))
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

    await bot.edit_message_reply_markup(
        chat_id=group_chat_id,
        message_id=game_message_id,
        reply_markup=kb
    )


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
    if current_turn_message_id:
        try:
            await bot.unpin_chat_message(group_chat_id, current_turn_message_id)
        except:
            pass

    text = f"⏳ {duration//60:02d}:{duration%60:02d}\n🎙 نوبت صحبت {mention} است. ({duration} ثانیه)"
    msg = await bot.send_message(group_chat_id, text, parse_mode="HTML", reply_markup=turn_keyboard(seat, is_challenge))

    # تلاش برای پین کردن پیام جدید (اختیاری)
    try:
        await bot.pin_chat_message(group_chat_id, msg.message_id, disable_notification=True)
    except:
        pass

    current_turn_message_id = msg.message_id

    # لغو تایمر قبلی
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # راه‌اندازی تایمر (task)
    turn_timer_task = asyncio.create_task(countdown(seat, duration, msg.message_id, is_challenge))
    
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
            await asyncio.sleep(5)   # هر 5 ثانیه بروزرسانی کن
            remaining -= 5
            new_text = f"⏳ {max(0, remaining)//60:02d}:{max(0, remaining)%60:02d}\n🎙 نوبت صحبت {mention} است. ({max(0, remaining)} ثانیه)"
            try:
                await bot.edit_message_text(new_text, chat_id=group_chat_id, message_id=message_id,
                                            parse_mode="HTML", reply_markup=turn_keyboard(seat, is_challenge))
            except:
                pass
        # زمان به پایان رسید -> اطلاع بده (می‌تونی اینجا خودکار next بزنی یا منتظر دکمه بمانی)
        try:
            await bot.send_message(group_chat_id, f"⏳ زمان {mention} به پایان رسید.")
        except:
            pass
    except asyncio.CancelledError:
        # اگر از بیرون کنسل شد بی‌صدا بازمی‌گردیم
        return

# ======================
# نکست نوبت
# ======================
@dp.callback_query_handler(lambda c: c.data.startswith("next_"))
async def next_turn_callback(callback: types.CallbackQuery):
    global current_turn_index, turn_order, turn_timer_task, challenge_mode, paused_main_player, paused_main_duration, post_challenge_advance

    try:
        seat = int(callback.data.split("_",1)[1])
    except Exception:
        await callback.answer("⚠️ دادهٔ نادرست برای نکست.", show_alert=True)
        return

    # فقط گرداننده یا خود بازیکن مربوطه می‌تواند نوبت را پایان دهد
    player_uid = player_slots.get(seat)
    if callback.from_user.id != moderator_id and callback.from_user.id != player_uid:
        await callback.answer("❌ فقط بازیکن مربوطه یا گرداننده می‌تواند نوبت را پایان دهد.", show_alert=True)
        return

    # قبل از هر چیز، لغو تایمر فعلی
    if turn_timer_task and not turn_timer_task.done():
        turn_timer_task.cancel()

    # اگر الان در حالت چالش بودیم -> این Next مربوط به پایان نوبت چالش است
    if challenge_mode:
        challenge_mode = False
        await callback.answer("✅نوبت چالش تموم شد.")
        # اگر paused_main_player ست شده باشد یعنی این چالش از نوع "before" بوده -> resume نوبت اصلی
        if paused_main_player:
            resume_seat = paused_main_player
            resume_dur = paused_main_duration or DEFAULT_TURN_DURATION
            paused_main_player = None
            paused_main_duration = None
            await start_turn(resume_seat, duration=resume_dur, is_challenge=False)
            return
        # اگر قرار بود بعد از چالش به نوبت بعدی بریم (post_challenge_advance) -> advance کن
        if post_challenge_advance:
            post_challenge_advance = False
            current_turn_index += 1
            if current_turn_index >= len(turn_order):
                await bot.send_message(group_chat_id, "✅ همه بازیکنا صحبت کردن. فاز روز تموم شد.")
                current_turn_index = 0
                return
            next_seat = turn_order[current_turn_index]
            await start_turn(next_seat, duration=DEFAULT_TURN_DURATION, is_challenge=False)
            return
        # در غیر اینصورت فقط ادامه عادی (بدون resume/advance)
        return

    # اگر برای این seat چالش بعد ثبت شده است -> ابتدا چالش اجرا شود
    if seat in pending_challenges:
        challenger_uid = pending_challenges.pop(seat, None)
        if challenger_uid:
            challenger_seat = next((s for s,u in player_slots.items() if u == challenger_uid), None)
            if challenger_seat is None:
                await bot.send_message(group_chat_id, "⚠️ چالش‌کننده صندلی ندارد؛ چالش نادیده گرفته شد.")
            else:
                # اجرای نوبتِ چالش‌کننده (نوع after) — بعد از چالش به نوبت بعدی می‌رویم
                challenge_mode = True
                post_challenge_advance = True
                await callback.answer("⚔ چالش ثبت‌شده اجرا می‌شود.", show_alert=True)
                await start_turn(challenger_seat, duration=60, is_challenge=True)
                return

    # اگر نه، بریم سراغ نوبت بعدی عادی
    # افزایش ایندکس و اجرای نفر بعد
    current_turn_index += 1
    if current_turn_index >= len(turn_order):
        await bot.send_message(group_chat_id, "✅ همه بازیکنان صحبت کردند. فاز روز پایان یافت.")
        current_turn_index = 0
        return

    next_seat = turn_order[current_turn_index]
    await callback.answer()  # بستن لودر
    await start_turn(next_seat, duration=DEFAULT_TURN_DURATION, is_challenge=False)

#===============
# درخواست چالش
#===============
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
        await callback.answer("⚠️ این صندلی بازیکن ندارد.", show_alert=True)
        return
    if challenger_id == target_id:
        await callback.answer("❌ نمی‌تونی به خودت چالش بدی.", show_alert=True)
        return

    # ساخت دکمه‌ها برای انتخاب نوع چالش
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("⚔ چالش قبل", callback_data=f"challenge_before_{challenger_id}_{target_id}"),
        InlineKeyboardButton("⚔ چالش بعد", callback_data=f"challenge_after_{challenger_id}_{target_id}"),
        InlineKeyboardButton("🚫 نمیدم چشت درآد", callback_data=f"challenge_none_{challenger_id}_{target_id}")
    )

    await callback.message.reply("لطفاً نوع چالشو انتخاب کن:", reply_markup=kb)
    await callback.answer()


#===============
# نوع چالش
#===============
@dp.callback_query_handler(lambda c: c.data.startswith("challenge_"))
async def challenge_choice(callback: types.CallbackQuery):
    global paused_main_player, paused_main_duration, challenge_mode

    parts = callback.data.split("_")
    # parts = ["challenge", "before"/"after"/"none", challenger_id, target_user]
    if len(parts) < 4:
        await callback.answer("⚠️ دادهٔ چالش ناقص است.", show_alert=True)
        return

    action = parts[1]
    challenger_id = int(parts[2])
    target_id = int(parts[3])

    challenger_name = players.get(challenger_id, "بازیکن")
    target_name = players.get(target_id, "بازیکن")

    if action == "before":
        paused_main_player = target_id
        paused_main_duration = DEFAULT_TURN_DURATION
        if turn_timer_task and not turn_timer_task.done():
            turn_timer_task.cancel()

        # پیدا کردن seat چالش‌کننده
        challenger_seat = next((s for s, u in player_slots.items() if u == challenger_id), None)
        target_seat = next((s for s,u in player_slots.items() if u == target_id), None)
        
        

#===============
# انتخاب چالش
#===============
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

    # فقط خود چالش‌کننده یا گرداننده اجازه دارند
    if callback.from_user.id not in [challenger_id, moderator_id]:
        await callback.answer("❌ فقط چالش‌دهنده یا گرداننده می‌تواند این گزینه را انتخاب کند.", show_alert=True)
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
            await bot.send_message(group_chat_id, f"⚔ چالش قبل صحبت برای {challenger_name} از {target_name} اجرا شد.")
            await start_turn(challenger_seat, duration=60, is_challenge=True)

    elif action == "after":
        target_seat = next((s for s,u in player_slots.items() if u == target_id), None)
        if target_seat is None:
            await bot.send_message(group_chat_id, "⚠️ هدف چالش صندلی ندارد؛ نمی‌توان چالش را ثبت کرد.")
        else:
            pending_challenges[target_seat] = challenger_id
            await bot.send_message(group_chat_id, f"⚔ چالش بعد صحبت برای {target_name} ثبت شد (: {challenger_name}).")

    elif action == "none":
        await bot.send_message(group_chat_id, f"🚫 {challenger_name}   چالش نداد.")

    await callback.answer()


# ======================
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
