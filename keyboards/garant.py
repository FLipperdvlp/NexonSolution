from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def garant_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧑‍💼 Живой гарант", callback_data="garant_live")],
            [InlineKeyboardButton(text="💰 Деп", callback_data="garant_dep")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")],
        ]
    )


def garant_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="garant_menu")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")],
        ]
    )
