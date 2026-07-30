from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
def main_menu_kb(admin: bool = False, private: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data="leave_review")],
        [InlineKeyboardButton(text="🔍 Проверить продавца", callback_data="check")],
        [
            InlineKeyboardButton(text="📊 Топ продавцов", callback_data="top"),
        ],
        [
            InlineKeyboardButton(
                text="💳 Мой баланс",
                callback_data="balance"
            )
        ],
        [InlineKeyboardButton(text="❓ Как пользоваться", callback_data="help")],
    ]
    if private:
        rows.append([InlineKeyboardButton(text="🛡️ Гарант", callback_data="garant_menu")])
    if admin:
        rows.append([InlineKeyboardButton(text="🛡️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)