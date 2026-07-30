from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def card_kb(target_id: int, has_photos: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_photos:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📸 Скриншоты сделок",
                    callback_data=f"photos_{target_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def balance_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Пополнить",
                    callback_data="deposit"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💸 Вывести",
                    callback_data="withdraw"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Передать",
                    callback_data="transfer"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="menu"
                )
            ]
        ]
    )