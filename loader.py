from aiogram import Bot, Dispatcher
import os

API_TOKEN = os.getenv("API_TOKEN")

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
