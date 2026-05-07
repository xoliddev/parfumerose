from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from dotenv import load_dotenv

from config import BOT_TOKEN, REDIS_DB, REDIS_HOST, REDIS_PORT

load_dotenv()

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")

try:
    if REDIS_HOST:
        storage = RedisStorage2(host=REDIS_HOST, port=int(REDIS_PORT), db=int(REDIS_DB))
    else:
        storage = MemoryStorage()
except Exception:
    storage = MemoryStorage()

dp = Dispatcher(bot, storage=storage)
