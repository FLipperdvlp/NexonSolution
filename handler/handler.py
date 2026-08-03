from aiogram.filters import Command
from logging.handlers import TimedRotatingFileHandler
from handler.wallet import *
from keyboards.admin import *
from handler.withdrawals import _withdrawals
from core import bot, dp, db, LOGS_DIR, WithdrawState, crypto

WITHDRAWALS_PATH = os.getenv("WITHDRAWALS_PATH", "withdrawals.json")

def withdrawal_notice_text(wd: dict) -> str:
    label = display_name(wd["user_id"], wd["username"])
    lines = [
        "💸 <b>Заявка на вывод</b>",
        "",
        f"🆔 Заявка: <code>{wd['id']}</code>",
        f"Пользователь: <code>{wd['user_id']}</code> ({label})",
        f"Сумма: <b>{wd['amount']:.2f} USDT</b>",
        "Кошелёк (TRC20):",
        f"<code>{escape(wd['wallet'])}</code>",
        "",
    ]
    if wd["status"] == "pending":
        lines.append("⏳ Статус: <b>ожидает обработки</b>")
    else:
        who = (
            f"@{escape(wd['handled_by_username'])}"
            if wd.get("handled_by_username")
            else (f"ID {wd['handled_by']}" if wd.get("handled_by") else "—")
        )
        if wd["status"] == "done":
            lines.append(f"✅ Статус: <b>выполнено</b> ({who})")
        else:
            lines.append(f"❌ Статус: <b>отклонено, деньги возвращены</b> ({who})")
    return "\n".join(lines)


@dp.message(Command("withdrawals"))
async def withdrawals_list_handler(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return

    pending = [wd for wd in _withdrawals.values() if wd["status"] == "pending"]
    if not pending:
        await message.answer("📭 Нет заявок на вывод, ожидающих обработки.")
        return

    pending.sort(key=lambda w: w["created_at"])
    await message.answer(f"📋 <b>Заявок в ожидании: {len(pending)}</b>")
    for wd in pending:
        await message.answer(withdrawal_notice_text(wd), reply_markup=withdrawal_admin_kb(wd["id"]))
