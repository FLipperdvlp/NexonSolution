import os
import re
from aiogram import types
from html import escape as html_escape

ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "6155527631, 8372409305").split(",") if x.strip()
]

TRC20_ADDRESS_PATTERN = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_private(message: types.Message) -> bool:
    return message.chat.type == "private"

def is_valid_trc20_address(wallet: str) -> bool:
    return bool(TRC20_ADDRESS_PATTERN.match((wallet or "").strip()))

def escape(text: str) -> str:
    return html_escape(str(text), quote=False)

def display_name(target_id: int, username: str = "") -> str:
    return f"@{escape(username)}" if username else f"ID {target_id}"