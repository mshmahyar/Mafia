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
admins = set()  # لیست ادمین های گروه
moderator_id = None
selected_scenario = None
players = {}  # {user_id: full_name}

# ======================
# مدیریت سناریوها (با فایل)
# ======================
SCENARIOS_FILE = "scenarios.json"

def load_scenarios():
    if not os.path.exists(SCENARIOS_FILE):
        return {}
    with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_scenarios():
    with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)

scenarios = load_scenarios()
if not scenarios:  # اگر فایل خالی بود، مقدار پیش‌فرض بذار
    scenarios = {
        "سناریو کلاسیک": {
            "min_players": 5,
            "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند"]
        },
        "سناریو ویژه": {
            "min_players": 6,
            "roles": ["مافیا", "مافیا", "شهروند", "شهروند", "شهروند", "کارآگاه"]
        }
    }
    save_scenarios()

# ======================
# کیبوردها
# ======================
def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📝 انتخاب سناریو", callback_data="choose_scenario"))
    kb.add(InlineKeyboardButton("🎩 انتخاب گرداننده", callback_data="choose_moderator"))
    return kb

def join_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("✅ ورود به بازی", callback_data="join_game"))
    kb.add(InlineKeyboardButton("❌ انصراف از بازی", callback_data="leave_game"))
    return kb

# ======================
# شروع بازی توسط ادمین
# ======================
@dp.message_handler(commands=["startgame"])
async def start_game(message: types.Message):
    global group_chat_id, game_running, admins, lobby_message_id

    group_chat_id = message.chat.id
    game_running = True
    admins = {member.user.id for member in await bot.get_chat_administrators(group_chat_id)}
    msg = await message.reply("🎮 بازی مافیا فعال شد! لطفا سناریو و گرداننده را انتخاب کنید.", reply_markup=main_menu())
    lobby_message_id = msg.message_id

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
    await callback.message.edit_text(f"📝 سناریو انتخاب شد: {selected_scenario}\nحالا گرداننده را انتخاب کنید.", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "choose_moderator")
async def choose_moderator(callback: types.CallbackQuery):
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
    await callback.message.edit_text(f"🎩 گرداننده انتخاب شد: {(await bot.get_chat_member(group_chat_id, moderator_id)).user.full_name}\nحالا اعضا می‌توانند وارد بازی شوند یا انصراف دهند.", reply_markup=join_menu())
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
# افزودن سناریو جدید (پی‌وی ادمین)
# ======================
@dp.message_handler(commands=["addscenario"], chat_type=types.ChatType.PRIVATE)
async def add_scenario(message: types.Message):
    # بررسی اینکه یوزر ادمین گروه هست یا نه
    if not group_chat_id:
        await message.answer("⚠ ابتدا باید یک بازی در گروه شروع کنید.")
        return

    admins = await bot.get_chat_administrators(group_chat_id)
    admin_ids = [a.user.id for a in admins]
    if message.from_user.id not in admin_ids:
        await message.answer("❌ فقط ادمین‌ها می‌توانند سناریو اضافه کنند.")
        return

    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.answer("❌ فرمت درست نیست.\nمثال:\n`/addscenario سناریو تست | 5 | مافیا,مافیا,شهروند,شهروند,کارآگاه`", parse_mode="Markdown")
        return

    try:
        name, rest = parts[1], parts[2]
        min_players_str, roles_str = rest.split("|", 1)
        min_players = int(min_players_str.strip())
        roles = [r.strip() for r in roles_str.split(",") if r.strip()]
    except Exception:
        await message.answer("❌ خطا در پردازش ورودی. فرمت را دقیق وارد کنید.")
        return

    scenarios[name] = {"min_players": min_players, "roles": roles}
    save_scenarios()
    await message.answer(f"✅ سناریو '{name}' با موفقیت اضافه شد!")

# ======================
# حذف سناریو (گروه - دو مرحله‌ای)
# ======================
@dp.message_handler(commands=["removescenario"], chat_type=["group", "supergroup"])
async def remove_scenario_menu(message: types.Message):
    admins = await bot.get_chat_administrators(message.chat.id)
    admin_ids = [a.user.id for a in admins]

    if message.from_user.id not in admin_ids:
        await message.reply("❌ فقط ادمین‌ها می‌توانند سناریو حذف کنند.")
        return

    if not scenarios:
        await message.reply("⚠ هیچ سناریویی برای حذف وجود ندارد.")
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(f"🗑 {scen}", callback_data=f"delete_scenario_{scen}"))

    await message.reply("🗑 یک سناریو برای حذف انتخاب کنید:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("delete_scenario_"))
async def confirm_delete_scenario(callback: types.CallbackQuery):
    scenario_name = callback.data.replace("delete_scenario_", "")
    if scenario_name not in scenarios:
        await callback.answer("⚠ سناریو پیدا نشد.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ بله", callback_data=f"confirm_delete_{scenario_name}"),
        InlineKeyboardButton("❌ خیر", callback_data="cancel_delete")
    )
    await callback.message.edit_text(f"آیا مطمئن هستید که می‌خواهید سناریو '{scenario_name}' را حذف کنید؟", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_delete_"))
async def delete_scenario(callback: types.CallbackQuery):
    scenario_name = callback.data.replace("confirm_delete_", "")
    if scenario_name not in scenarios:
        await callback.answer("⚠ سناریو پیدا نشد.", show_alert=True)
        return

    scenarios.pop(scenario_name)
    save_scenarios()

    await callback.answer(f"✅ سناریو '{scenario_name}' با موفقیت حذف شد.", show_alert=True)

    if scenarios:
        kb = InlineKeyboardMarkup(row_width=1)
        for scen in scenarios:
            kb.add(InlineKeyboardButton(f"🗑 {scen}", callback_data=f"delete_scenario_{scen}"))
        await callback.message.edit_text("🗑 یک سناریو برای حذف انتخاب کنید:", reply_markup=kb)
    else:
        await callback.message.edit_text("⚠ هیچ سناریویی باقی نمانده است.")

@dp.callback_query_handler(lambda c: c.data == "cancel_delete")
async def cancel_delete(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    for scen in scenarios:
        kb.add(InlineKeyboardButton(f"🗑 {scen}", callback_data=f"delete_scenario_{scen}"))

    await callback.message.edit_text("🗑 انتخاب سناریو برای حذف:", reply_markup=kb)
    await callback.answer("❌ حذف لغو شد.", show_alert=True)

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
