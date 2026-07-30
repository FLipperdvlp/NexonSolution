import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional
from aiogram.exceptions import TelegramAPIError
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from bot import LOGS_DIR, WithdrawState
from checks.checks import *
from keyboards.back import *
from aiogram.fsm.context import FSMContext
from db.database import Database

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8578283530:AAEUajtwik66P-ReEfPA_j8ge36zClfoN-M")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

DB_PATH: str = os.getenv("DB_PATH", "reputation.db")

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database(DB_PATH)
logger = logging.getLogger(__name__)



action_formatter = logging.Formatter("%(asctime)s | %(message)s")
action_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOGS_DIR, "actions.log"),
    when="midnight",
    backupCount=14,
    encoding="utf-8",
)

action_file_handler.setFormatter(action_formatter)
action_logger = logging.getLogger("actions")
action_logger.setLevel(logging.INFO)
action_logger.addHandler(action_file_handler)
action_console_handler = logging.StreamHandler()
action_console_handler.setFormatter(action_formatter)
action_logger.addHandler(action_console_handler)
action_logger.propagate = False

_ACTION_EMOJI: list[tuple[str, str]] = [
    ("ПЕРЕВОД", "🔄"),
    ("ПОПОЛНЕНИЕ", "💰"),
    ("ВЫВОД", "💸"),
    ("БАН", "🚫"),
    ("РАЗБАН", "✅"),
    ("УДАЛЕНИЕ", "🗑️"),
    ("ОТЗЫВ", "✍️"),
    ("ПРОВЕРКА", "🔍"),
    ("СТАРТ", "🚀"),
    ("АДМИН", "🛡️"),
]

def _action_emoji(action: str) -> str:
    upper = action.upper()
    for keyword, emoji in _ACTION_EMOJI:
        if keyword in upper:
            return emoji
    return "•"

def log_action(
    user_id: Optional[int],
    username: Optional[str],
    action: str,
    details: str = "",
    chat: Optional[types.Chat] = None,
) -> None:
    uname = f"@{username}" if username else "-"
    user_part = f"{user_id if user_id is not None else '-'} ({uname})"

    chat_part = "-"
    if chat is not None:
        chat_label = chat.title or chat.type
        chat_part = f"{escape(chat_label)}({chat.id})"

    emoji = _action_emoji(action)
    line = (
        f"{emoji} {action:<28} | user={user_part:<28} | чат={chat_part:<22}"
    )
    if details:
        line += f" | {details}"

    action_logger.info(line)



@dp.callback_query(F.data=="withdraw")
async def withdraw_callback( callback: CallbackQuery, state: FSMContext ):
    balance = db.get_balance(
        callback.from_user.id
    )
    await state.set_state(
        WithdrawState.waiting_amount
    )
    await callback.message.edit_text(
        f"""
💸 <b>Вывод средств</b>

Вывод — только на кошелёк USDT в сети <b>TRC20</b>.

Ваш баланс:
<b>{balance:.2f} USDT</b>


Введите сумму вывода:
        """,
        reply_markup=back_kb()
    )
    await callback.answer()

@dp.message(WithdrawState.waiting_amount)
async def withdraw_amount(
    message: types.Message,
    state:FSMContext
):
    try:
        amount=float(message.text)
    except:
        await message.answer(
            "❌ Введите число"
        )
        return
    balance=db.get_balance(
        message.from_user.id
    )
    if amount > balance:
        await message.answer(
            "❌ Недостаточно средств"
        )
        return
    await state.update_data(
        amount=amount
    )
    await state.set_state(
        WithdrawState.waiting_wallet
    )
    await message.answer(
        """
            💳 Отправьте ваш USDT-кошелёк в сети <b>TRC20</b> (адрес TRON, начинается с «T»):

            Например:
            TXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        """
    )

@dp.message(WithdrawState.waiting_wallet)
async def withdraw_wallet(
    message:types.Message,
    state:FSMContext
):
    data=await state.get_data()
    amount=data["amount"]
    wallet=(message.text or "").strip()

    if not is_valid_trc20_address(wallet):
        await message.answer(
            "❌ <b>Это не похоже на адрес TRC20</b>\n\n"
            "Адрес TRON начинается с «T» и состоит из 34 символов.\n"
            "Отправьте корректный TRC20-адрес:"
        )
        return

    success=db.remove_balance(
        message.from_user.id,
        amount
    )
    if not success:
        await message.answer(
            "❌ Ошибка"
        )
        return

    log_action(
        message.from_user.id,
        message.from_user.username,
        "ВЫВОД (заявка создана)",
        details=(
            f"сумма={amount} USDT кошелёк(TRC20)={wallet} "
            f"баланс_после={db.get_balance(message.from_user.id):.2f}"
        ),
        chat=message.chat,
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "💸 <b>Заявка на вывод</b>\n\n"
                f"Пользователь: <code>{message.from_user.id}</code>"
                f" (@{escape(message.from_user.username or '-')})\n"
                f"Сумма: <b>{amount} USDT</b>\n"
                f"Кошелёк (TRC20):\n<code>{escape(wallet)}</code>",
            )
        except TelegramAPIError:
            pass

    await message.answer(
        f"""
            ✅ <b>Заявка создана</b>

            Сумма:
            <b>{amount} USDT</b>

            Кошелек (TRC20):
            <code>{wallet}</code>

            Администратор обработает вывод.
        """
    )
    await state.clear()
