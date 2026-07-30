from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❓ Как пользоваться", callback_data="help"),
                InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu"),
            ]
        ]
    )