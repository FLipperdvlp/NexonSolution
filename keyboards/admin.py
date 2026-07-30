from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def admin_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]]
    )

def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить отзыв", callback_data="admin_delreview")],
            [InlineKeyboardButton(text="🚫 Забанить", callback_data="admin_ban")],
            [InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")],
            [InlineKeyboardButton(text="📈 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")],
        ]
    )

def withdrawal_admin_kb(wd_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"wd_done_{wd_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd_reject_{wd_id}"),
            ]
        ]
    )