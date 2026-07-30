from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def review_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data="review_cancel")]]
    )

def review_photos_kb(count: int) -> InlineKeyboardMarkup:
    done_text = f"✅ Готово ({count} 📸) — сохранить" if count else "✅ Готово — сохранить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=done_text, callback_data="review_done")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="review_cancel")],
        ]
    )

def review_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data="review_cancel")]]
    )

def review_sign_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Положительный", callback_data="revsign_+")],
            [InlineKeyboardButton(text="❌ Отрицательный", callback_data="revsign_-")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="menu")],
        ]
    )