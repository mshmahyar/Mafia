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
game_running = False
lobby_active = False
turn_order = []             # ترتیب نوبت‌ها
current_turn_index = 0      # اندیس نوبت فعلی
current_turn_message_id = None  # پیام پین شده برای نوبت
turn_timer_task = None      # تسک تایمر نوبت
player_slots = {}  # {slot_number: user_id}
# وضعیت پخش نقش و نگهداری نقش‌های اختصاص‌یافته
roles_distributed = False       # آیا نقش‌ها پخش شده‌اند (با کلید «پخش نقش»)
assigned_roles = {}             # { user_id: role }  — نقش اختصاص‌یافته به هر بازیکن


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


def turn_keyboard(player_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⏭ نکست", callback_data=f"next_turn_{player_id}"))
    return kb

# ======================
# دستورات اصلی
# ======================
@dp.callback_query_handler(lambda c: c.data == "new_game")
async def start_game(callback: types.CallbackQuery):
    global group_chat_id, lobby_active, admins, lobby_message_id
    group_chat_id = callback.message.chat.id
    lobby_active = True    # فقط لابی فعال، بازی هنوز شروع نشده
    admins = {member.user.id for member in await bot.get_chat_administrators(group_chat_id)}
    msg = await callback.message.reply(
        "🎮 لابی بازی مافیا فعال شد!\nلطفا سناریو و گرداننده را انتخاب کنید:",
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
    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(scen, callback_data=f"scenario_{scen}"))
    await callback.message.edit_text("📝 یک سناریو انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("scenario_"))
async def scenario_selected(callback: types.CallbackQuery):
    global selected_scenario
    selected_scenario = callback.data.replace("scenario_", "")
    await callback.answer(f"📝 سناریو «{selected_scenario}» انتخاب شد.")

   
    #اگر لابی فعال است و پیام لابی وجود دارد → آن را بروزرسانی کن تا صندلی‌ها/ورود نمایش داده شود
    global lobby_active
    lobby_active = True
    await update_lobby()



async def moderator_selected(callback: types.CallbackQuery):
    global moderator_id, lobby_active
    moderator_id = int(callback.data.replace("moderator_", ""))
    lobby_active = True   # بعد از انتخاب گرداننده هم لابی فعال میشه

    await callback.message.edit_text(
        f"🎩 گرداننده انتخاب شد: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name}\n"
        f"حالا اعضا می‌توانند وارد بازی شوند یا انصراف دهند.",
        reply_markup=join_menu()
    )
    await callback.answer()
    await update_lobby()

# ======================
# ورود و انصراف
# ======================
@dp.callback_query_handler(lambda c: c.data == "join_game")
async def join_game_callback(callback: types.CallbackQuery):
    user = callback.from_user

    # ❌ جلوگیری از ورود گرداننده
    #if user.id == moderator_id:
    #    await callback.answer("❌ گرداننده نمی‌تواند وارد بازی شود.", show_alert=True)
    #    return

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
    global lobby_message_id
    if not group_chat_id or not lobby_message_id:
        return

    # ساخت متن با قالب ساده (HTML safe)
    if selected_scenario:
        scenario_text = selected_scenario
    else:
        scenario_text = "انتخاب نشده"

    if moderator_id:
        try:
            mod = await bot.get_chat_member(group_chat_id, moderator_id)
            mod_name = mod.user.full_name
        except:
            mod_name = "❓"
    else:
        mod_name = "انتخاب نشده"

    text = f"📋 لیست بازی:\nسناریو: {scenario_text}\nگرداننده: {mod_name}\n\n"

    if players:
        for uid, name in players.items():
            text += f"- {name}\n"
    else:
        text += "هیچ بازیکنی وارد بازی نشده است.\n"

    kb = InlineKeyboardMarkup(row_width=5)

    # دکمه‌های انتخاب صندلی فقط اگر سناریو انتخاب شده باشد
    if selected_scenario:
        max_players = len(scenarios[selected_scenario]["roles"])
        for i in range(1, max_players + 1):
            if i in player_slots:
                player_name = players.get(player_slots[i], "❓")
                kb.insert(InlineKeyboardButton(f"{i} ({player_name})", callback_data=f"slot_{i}"))
            else:
                kb.insert(InlineKeyboardButton(str(i), callback_data=f"slot_{i}"))

    # دکمه ورود/خروج
    kb.row(
        InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"),
        InlineKeyboardButton("❌ خروج از بازی", callback_data="leave_game"),
    )

    # دکمه لغو بازی فقط برای مدیران (گرداننده)
    if moderator_id and moderator_id in admins:
        kb.add(InlineKeyboardButton("🚫 لغو بازی", callback_data="cancel_game"))

    # ✅ دکمه پخش نقش یا شروع بازی بسته به وضعیت پخش نقش
    if selected_scenario and moderator_id:
        scenario_data = scenarios[selected_scenario]
        min_players = scenario_data["min_players"]
        max_players = len(scenario_data["roles"])
        if min_players <= len(players) <= max_players:
            if not roles_distributed:
                # دکمه "پخش نقش" وقتی نقش‌ها هنوز پخش نشده‌اند
                kb.add(InlineKeyboardButton("🎭 پخش نقش", callback_data="distribute_roles"))
            else:
                # بعد از پخش نقش، دکمه "شروع بازی"
                kb.add(InlineKeyboardButton("🚀 شروع بازی", callback_data="start_play"))
        elif len(players) > max_players:
            text += "\n⚠️ تعداد بازیکنان بیش از ظرفیت این سناریو است."
            
        player_ids = list(players.keys())
        turn_order = player_ids.copy()
        random.shuffle(turn_order)
        current_turn_index = 0
        game_running = True    
    if len(players) < min_players:
        await callback.answer("❌ تعداد بازیکنان کافی نیست.", show_alert=True)
        return
    elif len(players) > max_players:
        await callback.answer("❌ تعداد بازیکنان بیش از ظرفیت این سناریو است.", show_alert=True)
        return


    # بروزرسانی پیام لابی (استفاده از HTML برای parse mode چون bot با HTML مقداردهی شده)
    try:
        await bot.edit_message_text(
            text,
            chat_id=group_chat_id,
            message_id=lobby_message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        # در صورت خطا، لاگ کن اما برنامه قطع نشود
        logging.error(f"خطا هنگام بروزرسانی پیام لابی: {e}")
        


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
    # پاک‌سازی وضعیت مربوط به نقش‌ها
    roles_distributed = False
    assigned_roles.clear()
    game_running = False
    selected_scenario = None
    moderator_id = None
    lobby_message_id = None

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
@dp.callback_query_handler(lambda c: c.data == "distribute_roles")
async def distribute_roles(callback: types.CallbackQuery):
    global roles_distributed, assigned_roles

    # فقط گرداننده اجازه دارد
    if callback.from_user.id != moderator_id:
        await callback.answer("❌ فقط گرداننده می‌تواند نقش‌ها را پخش کند.", show_alert=True)
        return

    if not selected_scenario:
        await callback.answer("❌ هنوز سناریویی انتخاب نشده.", show_alert=True)
        return

    scenario_data = scenarios[selected_scenario]
    min_players = scenario_data["min_players"]
    max_players = len(scenario_data["roles"])

    # تعداد بازیکنان چک شود
    if len(players) < min_players:
        await callback.answer("❌ تعداد بازیکنان برای این سناریو کافی نیست.", show_alert=True)
        return
    if len(players) > max_players:
        await callback.answer("❌ تعداد بازیکنان بیش از ظرفیت این سناریو است.", show_alert=True)
        return

    if roles_distributed:
        await callback.answer("⚠ نقش‌ها قبلاً پخش شده‌اند.", show_alert=True)
        return

    # ساختن لیست نقش‌ها بر اساس سناریو و تعداد بازیکنان
    roles_pool = scenario_data["roles"][:len(players)]
    random.shuffle(roles_pool)

    # نگاشت نقش‌ها به بازیکنان (از ترتیب players استفاده می‌کنیم)
    assigned_roles.clear()
    player_ids = list(players.keys())
    role_list_for_moderator = f"📜 لیست نقش‌ها (سناریو: {selected_scenario}):\n\n"

    for uid, role in zip(player_ids, roles_pool):
        assigned_roles[uid] = role
        try:
            await bot.send_message(uid, f"🎭 نقش شما: {role}\n\nبرای مشاهدهٔ نقش حتماً پیوی ربات را چک کنید.")
        except Exception:
            # اگر نتوانستیم در پیوی ارسال کنیم، به گرداننده اطلاع می‌دهیم بعداً
            pass
        # نام بازیکن برای گرداننده
        role_list_for_moderator += f"{players.get(uid, uid)}: {role}\n"

    # ارسال لیست کامل نقش‌ها به پیوی گرداننده
    try:
        await bot.send_message(moderator_id, role_list_for_moderator)
    except Exception:
        pass

    # علامت‌گذاری که نقش‌ها پخش شدند
    roles_distributed = True

    # بروزرسانی لابی (اکنون دکمه شروع بازی نمایش داده خواهد شد)
    await update_lobby()

    # اطلاع به گروه / ویرایش متن پیام لابی فعلی
    try:
        await callback.message.edit_text(
            "🚀 نقش‌ها پخش شدند!\n"
            "🎭 نقش‌ها به پیوی بازیکنان ارسال شدند.\n"
            "📌 گرداننده لیست نقش‌ها را در پیوی دریافت کرده است."
        )
    except:
        pass

    await callback.answer("✅ نقش‌ها پخش شدند.")


    @dp.callback_query_handler(lambda c: c.data == "start_play")
    async def start_play(callback: types.CallbackQuery):
        global turn_order, current_turn_index, game_running

        if callback.from_user.id != moderator_id:
            await callback.answer("❌ فقط گرداننده می‌تواند بازی را شروع کند.", show_alert=True)
            return

        if not selected_scenario:
            await callback.answer("❌ ابتدا یک سناریو انتخاب کنید.", show_alert=True)
            return

        # مطمئن شو نقش‌ها قبلاً پخش شده‌اند
        if not roles_distributed:
            await callback.answer("❌ ابتدا روی «پخش نقش» کلیک کنید.", show_alert=True)
            return

        # بررسی تعداد بازیکنان نسبت به سناریو (ایمنی)
        scenario_roles = scenarios[selected_scenario]["roles"]
        if len(players) < len(scenario_roles[:len(players)]):
            await callback.answer("❌ تعداد بازیکنان کافی نیست.", show_alert=True)
            return

        # آماده‌سازی ترتیب نوبت‌ها و شروع بازی
        player_ids = list(players.keys())
        turn_order = player_ids.copy()
        random.shuffle(turn_order)
        current_turn_index = 0
        game_running = True

        # ارسال لیست نقش‌ها به گرداننده دوباره (اختیاری) — اگر می‌خواهی نیاور، خط زیر را پاک کن
        try:
            role_list_for_moderator = "📜 نقش‌ها (تکرار برای گرداننده):\n\n"
            for uid in player_ids:
                role_list_for_moderator += f"{players.get(uid,'؟')}: {assigned_roles.get(uid,'(نامشخص)')}\n"
            await bot.send_message(moderator_id, role_list_for_moderator)
        except:
            pass
            
        await callback.answer("✅ بازی آغاز شد!")

        # شروع نوبت اول (اگر لیست نوبت وجود دارد)
        if turn_order:
            await start_turn(turn_order[0])


# ======================
# شروع نوبت + تایمر
# ======================
async def start_turn(player_id, duration=120):
    global current_turn_message_id, turn_timer_task
    mention = f"<a href='tg://user?id={player_id}'>{players[player_id]}</a>"
    text = f"⏳ 00:{duration:02d}\n🎙 نوبت صحبت {mention} است. ({duration} ثانیه)"
    msg = await bot.send_message(group_chat_id, text, reply_markup=turn_keyboard(player_id))
    try:
        await bot.pin_chat_message(group_chat_id, msg.message_id, disable_notification=True)
    except:
        pass
    current_turn_message_id = msg.message_id

    async def countdown():
        nonlocal msg
        remaining = duration
        while remaining > 0:
            await asyncio.sleep(10)
            remaining -= 10
            new_text = f"⏳ 00:{remaining:02d}\n🎙 نوبت صحبت {mention} است. ({remaining} ثانیه)"
            try:
                await bot.edit_message_text(new_text, chat_id=group_chat_id,
                                            message_id=current_turn_message_id,
                                            reply_markup=turn_keyboard(player_id))
            except:
                pass

    turn_timer_task = asyncio.create_task(countdown())

# ======================
# نکست
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
        await bot.send_message(group_chat_id, "✅ همه بازیکنان صحبت کردند. فاز روز پایان یافت.")
    await callback.answer()

# ======================
# استارتاپ
# ======================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted and ready for polling.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)

