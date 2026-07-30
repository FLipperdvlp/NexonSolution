from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
def view_user_kb(target_id: int, username: str = "") -> InlineKeyboardMarkup:
    label = f"@{username}" if username else str(target_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔍 Открыть репутацию {label}",
                    callback_data=f"view_{target_id}",
                )
            ],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")],
        ]
    )