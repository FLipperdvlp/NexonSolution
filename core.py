# core.py
import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiocryptopay import AioCryptoPay, Networks
from dotenv import load_dotenv

from db.database import Database

load_dotenv()  # обязательно до чтения os.getenv ниже

LOGS_DIR = os.getenv("LOGS_DIR", os.path.join(os.path.dirname(__file__), "logs"))
os.makedirs(LOGS_DIR, exist_ok=True)

BOT_TOKEN: str = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError(
        "❌ BOT_TOKEN не задан! Создай файл .env и укажи BOT_TOKEN=<твой токен>"
    )

CRYPTO_TOKEN: str = os.getenv("CRYPTO_TOKEN")
if not CRYPTO_TOKEN:
    raise ValueError(
        "❌ CRYPTO_TOKEN не задан! Укажи его в файле .env (CRYPTO_TOKEN=<токен из @CryptoBot>)"
    )

DB_PATH: str = os.getenv("DB_PATH", "reputation.db")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database(DB_PATH)
crypto = AioCryptoPay(token=CRYPTO_TOKEN, network=Networks.MAIN_NET)
logger = logging.getLogger(__name__)

class WithdrawState(StatesGroup):
    waiting_amount = State()
    waiting_wallet = State()