import asyncio
import json
import logging
import os
import re
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Tuple
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiohttp import web
from dotenv import load_dotenv
from aiocryptopay import AioCryptoPay, Networks

from keyboards.main_menu import *
from keyboards.admin     import *
from keyboards.back      import *
from keyboards.garant    import *
from keyboards.help      import *
from keyboards.review    import *
from keyboards.view_user import *
from keyboards.wallet    import *
from checks.checks       import *
from handler.wallet      import *
from handler.handler     import *
from classes.class_      import *
from handler.withdrawals import _withdrawals

load_dotenv()

if not BOT_TOKEN:
    raise ValueError(
        "❌ BOT_TOKEN не задан! Создай файл .env и укажи BOT_TOKEN=<твой токен>"
    )

CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN", "604330:AAdwUH5U4qdjhITyvkkkL26BEC9Kxh4Bfwr")

crypto = AioCryptoPay(
    token=CRYPTO_TOKEN,
    network=Networks.MAIN_NET
)

GARANT_USERNAME: str = os.getenv("GARANT_USERNAME", "gavrilovit")

# Логирование
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
formatter = logging.Formatter(LOG_FORMAT)

bot_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOGS_DIR, "bot.log"),
    when="midnight",
    backupCount=14,
    encoding="utf-8",
)
bot_file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[console_handler, bot_file_handler],
)

@dp.message.outer_middleware()
async def register_user_middleware(handler, event: types.Message, data: dict):
    user = event.from_user
    if user is not None and not user.is_bot:
        try:
            db.upsert_user(
                telegram_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
            )
        except Exception as e:
            logger.warning(f"Не удалось зарегистрировать пользователя {user.id}: {e}")
    return await handler(event, data)


REP_PATTERN = re.compile(
    r"^\s*(?:@?(\w+)\s*([+\-])\s*реп\s*(.*)|([+\-])\s*реп\s+@?(\w+)\s*(.*))$",
    re.IGNORECASE | re.DOTALL,
)


def parse_rep_message(text: str) -> Optional[Tuple[str, str, str]]:

    match = REP_PATTERN.match(text.strip())

    if not match:
        return None

    # @user +реп текст
    if match.group(1):
        identifier = match.group(1)
        sign = match.group(2)
        description = match.group(3).strip()

    # +реп @user текст
    else:
        sign = match.group(4)
        identifier = match.group(5)
        description = match.group(6).strip()

    return sign, identifier, description

def _save_withdrawals() -> None:
    try:
        with open(WITHDRAWALS_PATH, "w", encoding="utf-8") as f:
            json.dump(_withdrawals, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error(f"Не удалось сохранить {WITHDRAWALS_PATH}: {e}")


def create_withdrawal_request(
    user_id: int, username: str, amount: float, wallet: str, source: str
) -> dict:
    wd_id = str(int(datetime.utcnow().timestamp() * 1000))
    wd = {
        "id": wd_id,
        "user_id": user_id,
        "username": username or "",
        "amount": amount,
        "wallet": wallet,
        "status": "pending",
        "source": source,  # "bot" или "webapp"
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "handled_by": None,
        "handled_by_username": None,
        "handled_at": None,
        "notified": {},
    }
    _withdrawals[wd_id] = wd
    _save_withdrawals()
    return wd

async def notify_admins_withdrawal(wd_id: str) -> None:
    wd = _withdrawals.get(wd_id)
    if wd is None:
        return
    text = withdrawal_notice_text(wd)
    notified: dict[str, int] = {}
    for admin_id in ADMIN_IDS:
        try:
            msg = await bot.send_message(admin_id, text, reply_markup=withdrawal_admin_kb(wd_id))
            notified[str(admin_id)] = msg.message_id
        except TelegramAPIError as e:
            logger.warning(f"Не удалось уведомить админа {admin_id} о заявке {wd_id}: {e}")
    wd["notified"] = notified
    _save_withdrawals()


async def _refresh_admin_notifications(wd: dict) -> None:
    text = withdrawal_notice_text(wd)
    kb = withdrawal_admin_kb(wd["id"]) if wd["status"] == "pending" else None
    for admin_id_str, message_id in (wd.get("notified") or {}).items():
        try:
            await bot.edit_message_text(
                text, chat_id=int(admin_id_str), message_id=message_id, reply_markup=kb
            )
        except TelegramAPIError:
            pass


@dp.callback_query(F.data.startswith("wd_done_"))
async def withdrawal_done_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён", show_alert=True)
        return

    wd_id = callback.data.split("_", 2)[2]
    wd = _withdrawals.get(wd_id)
    if wd is None:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if wd["status"] != "pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    wd["status"] = "done"
    wd["handled_by"] = callback.from_user.id
    wd["handled_by_username"] = callback.from_user.username or ""
    wd["handled_at"] = datetime.utcnow().isoformat(timespec="seconds")
    _save_withdrawals()

    await _refresh_admin_notifications(wd)

    try:
        await bot.send_message(
            wd["user_id"],
            f"✅ <b>Вывод выполнен</b>\n\n"
            f"Сумма <b>{wd['amount']:.2f} USDT</b> отправлена на указанный кошелёк.",
        )
    except TelegramAPIError:
        pass

    label = display_name(wd["user_id"], wd["username"])
    log_action(
        callback.from_user.id,
        callback.from_user.username,
        "ВЫВОД (подтверждён админом)",
        details=f"заявка={wd_id} цель={label}(id={wd['user_id']}) сумма={wd['amount']:.2f} USDT",
    )
    await callback.answer("✅ Отмечено как выполнено")


@dp.callback_query(F.data.startswith("wd_reject_"))
async def withdrawal_reject_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён", show_alert=True)
        return

    wd_id = callback.data.split("_", 2)[2]
    wd = _withdrawals.get(wd_id)
    if wd is None:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if wd["status"] != "pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    wd["status"] = "rejected"
    wd["handled_by"] = callback.from_user.id
    wd["handled_by_username"] = callback.from_user.username or ""
    wd["handled_at"] = datetime.utcnow().isoformat(timespec="seconds")
    _save_withdrawals()

    db.add_balance(wd["user_id"], wd["amount"])

    await _refresh_admin_notifications(wd)

    try:
        await bot.send_message(
            wd["user_id"],
            f"❌ <b>Заявка на вывод отклонена</b>\n\n"
            f"Сумма <b>{wd['amount']:.2f} USDT</b> возвращена на ваш баланс в боте.",
        )
    except TelegramAPIError:
        pass

    label = display_name(wd["user_id"], wd["username"])
    log_action(
        callback.from_user.id,
        callback.from_user.username,
        "ВЫВОД (отклонён админом)",
        details=(
            f"заявка={wd_id} цель={label}(id={wd['user_id']}) сумма={wd['amount']:.2f} USDT "
            f"баланс_после={db.get_balance(wd['user_id']):.2f}"
        ),
    )
    await callback.answer("❌ Отклонено, деньги возвращены")

album_buffers: dict[str, list[types.Message]] = {}
album_confirm_in_progress: set[str] = set()

@dp.message.outer_middleware()
async def album_collector_middleware(handler, event: types.Message, data: dict):
    if event.media_group_id:
        album_buffers.setdefault(event.media_group_id, []).append(event)
    return await handler(event, data)


@dp.channel_post.outer_middleware()
async def album_collector_channel_middleware(handler, event: types.Message, data: dict):
    if event.media_group_id:
        album_buffers.setdefault(event.media_group_id, []).append(event)
    return await handler(event, data)


def get_single_photo(message: types.Message) -> Optional[str]:
    if message.photo:
        return message.photo[-1].file_id
    if message.reply_to_message and message.reply_to_message.photo:
        return message.reply_to_message.photo[-1].file_id
    return None


async def collect_review_photos(
    message: types.Message, delete_after: bool = False
) -> list[str]:
    if message.media_group_id:
        gid = message.media_group_id
        await asyncio.sleep(1.5)
        messages = album_buffers.pop(gid, [message])
        messages = sorted(messages, key=lambda m: m.message_id)

        photo_ids: list[str] = []
        seen: set[str] = set()
        for m in messages:
            if m.photo:
                fid = m.photo[-1].file_id
                if fid not in seen:
                    seen.add(fid)
                    photo_ids.append(fid)
        if photo_ids:
            if delete_after:
                for m in messages:
                    if m.photo:
                        try:
                            await m.delete()
                        except TelegramAPIError:
                            pass
            return photo_ids

    single = get_single_photo(message)
    if single and delete_after and message.photo:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
    return [single] if single else []


def get_target_text(message: types.Message) -> str:
    return message.text or message.caption or ""


def trust_bar(score: int) -> str:
    score = max(-100, min(100, score))

    if score >= 80:
        level = "Проверенный"
        color = "🟢"
    elif score >= 40:
        level = "Надёжный"
        color = "🟡"
    elif score >= -20:
        level = "Сомнительный"
        color = "🟠"
    else:
        level = "Мошенник"
        color = "🔴"

    filled = max(0, min(10, (score + 100) // 20))
    bar = color * filled + "⚫" * (10 - filled)
    return f"[{bar}] {score}% — {level}"


def truncate(text: str, max_len: int = 200) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"

def get_review_photo_ids(rev) -> list[str]:
    try:
        value = rev["photo_file_id"]
    except (KeyError, IndexError, TypeError):
        return []
    if not value:
        return []
    return [pid for pid in str(value).split(",") if pid]

def get_review_id(rev) -> Optional[int]:
    for key in ("id", "review_id"):
        try:
            value = rev[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None

def deal_button_label(rev, index: int) -> str:
    sign_emoji = "✅" if rev["sign"] == "+" else "❌"
    date_str = str(rev["created_at"])[:10]
    n_photos = len(get_review_photo_ids(rev))
    return f"{index}. {sign_emoji} {date_str} · {n_photos} фото"


async def resolve_target(identifier: str) -> Tuple[Optional[int], str]:
    raw = identifier.strip().lstrip("@")

    if raw.isdigit():
        target_id = int(raw)
        username = db.get_username_for_id(target_id) or ""
        return target_id, username

    username = raw.lower()

    target_id = db.get_user_id_by_username(username)
    if target_id:
        return target_id, username

    try:
        chat = await bot.get_chat(f"@{username}")
        target_id = chat.id
        db.upsert_user(
            telegram_id=target_id,
            username=chat.username or username,
            first_name=getattr(chat, "first_name", "") or "",
        )
        return target_id, chat.username or username
    except TelegramAPIError:
        return None, username


def resolve_target_from_reply(message: types.Message) -> Optional[Tuple[int, str]]:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return None
    user = reply.from_user
    if user.is_bot:
        return None
    username = (user.username or "").lower()
    db.upsert_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    return user.id, username


async def resolve_target_smart(
    identifier: Optional[str], message: types.Message
) -> Tuple[Optional[int], str]:
    from_reply = resolve_target_from_reply(message)
    if from_reply:
        return from_reply
    if identifier:
        return await resolve_target(identifier)
    return None, ""

@dp.message.outer_middleware()
async def block_channel_commands(handler, event: types.Message, data: dict):
    if event.chat.type in ("group", "supergroup"):

        text = event.text or ""

        if text.startswith("/"):
            user = event.from_user
            if user is None:
                return await handler(event, data)
            if not is_admin(user.id):
                try:
                    await event.delete()
                except TelegramAPIError:
                    pass
                return

    return await handler(event, data)

async def send_reputation_card( target: types.Message | types.CallbackQuery, identifier: Optional[str] = None, source_message: Optional[types.Message] = None, ) -> None:
    if isinstance(target, CallbackQuery):
        msg = target.message
        is_callback = True
        requester = target.from_user
    else:
        msg = target
        is_callback = False
        requester = target.from_user

    if source_message is not None:
        target_id, username = await resolve_target_smart(identifier, source_message)
    else:
        target_id, username = await resolve_target(identifier or "")

    if target_id is None:
        not_found_text = (
            f"❌ <b>Не удалось найти пользователя @{escape(username)}</b>\n\n"
            f"Telegram не даёт боту искать людей по username, если они "
            f"ни разу не писали этому боту и не состоят с ним в общем чате "
            f"— это ограничение самого Telegram, а не бота.\n\n"
            f"✅ <b>Как найти гарантированно:</b>\n"
            f"• Ответь (reply) на любое сообщение этого человека и повтори команду\n"
            f"• Либо укажи числовой Telegram ID, например: <code>/check 123456789</code>\n"
            f"• Либо попроси его один раз написать /start этому боту"
        )
        if is_callback:
            await msg.edit_text(not_found_text, reply_markup=back_kb())
        else:
            await msg.answer(not_found_text, reply_markup=back_kb())
        return

    label = display_name(target_id, username)

    log_action(
        requester.id if requester else None,
        requester.username if requester else None,
        "ПРОВЕРКА РЕПУТАЦИИ",
        details=f"цель={label}(id={target_id})",
        chat=msg.chat if msg else None,
    )

    if db.is_banned(target_id):
        ban_text = (
            f"🚫 <b>Пользователь {label} заблокирован</b>\n\n"
            f"Данный продавец находится в чёрном списке 🍓 Клубничного бота.\n"
            f"Рекомендуем <b>не совершать</b> сделки с ним."
        )
        if is_callback:
            await msg.edit_text(ban_text, reply_markup=back_kb())
        else:
            await msg.answer(ban_text, reply_markup=back_kb())
        return

    stats = db.get_user_stats(target_id)
    reviews = db.get_user_reviews(target_id, limit=5)

    if stats is None:
        no_reviews_text = (
            f"❌ <b>Отзывов о {label} не найдено</b>\n\n"
            f"Этот продавец ещё не появлялся в нашей базе.\n\n"
            f"⚠️ <i>Будьте осторожны при работе с незнакомыми продавцами!</i>\n"
            f"Попросите отзывы напрямую или поищите в других источниках."
        )
        if is_callback:
            await msg.edit_text(no_reviews_text, reply_markup=back_kb())
        else:
            await msg.answer(no_reviews_text, reply_markup=back_kb())
        return

    bar = trust_bar(stats["score"])
    lines = [
        f"🍓 <b>Репутация продавца {label}</b>",
        "",
        f"<b>Рейтинг:</b> {bar}",
        f"<b>Отзывов:</b> {stats['total']} "
        f"(✅ {stats['positive']} / ❌ {stats['negative']})",
        "",
        "📋 <b>Последние отзывы:</b>",
    ]

    medals = ["🥇", "🥈", "🥉", "🏅", "🏅"]
    is_admin_viewer = is_admin(requester.id) if requester else False
    for i, rev in enumerate(reviews):
        medal = medals[i] if i < len(medals) else "▫️"
        sign_emoji = "✅" if rev["sign"] == "+" else "❌"
        reviewer_tag = (
            f"@{escape(rev['reviewer_username'])}"
            if rev["reviewer_username"]
            else escape(rev["reviewer_name"] or "Аноним")
        )
        source_tag = (
            f" <i>[канал: {escape(rev['chat_title'])}]</i>"
            if rev["source"] == "channel" and rev["chat_title"]
            else ""
        )

        date_str = str(rev["created_at"])[:10]
        desc = truncate(rev["description"] or "", 200)
        rev_id = get_review_id(rev)
        id_tag = f" <code>#{rev_id}</code>" if is_admin_viewer and rev_id is not None else ""

        lines.append(
            f"\n{medal} {sign_emoji}"
            f"<i>({date_str})</i>{id_tag}"
        )
        if desc:
            lines.append(f"   <blockquote>{escape(desc)}</blockquote>")

    check_arg = f"@{username}" if username else str(target_id)
    lines += [
        "",
        f"🔎 Используй /check @{escape(check_arg)} для обновления",
    ]

    card_text = "\n".join(lines)
    has_photos = any(get_review_photo_ids(rev) for rev in reviews)
    kb = card_kb(target_id, has_photos)

    try:
        if is_callback:
            await msg.edit_text(card_text, reply_markup=kb)
        else:
            await msg.answer(card_text, reply_markup=kb)
    except TelegramAPIError as e:
        if "message is not modified" not in str(e):
            logger.error(f"Ошибка отправки карточки: {e}")


@dp.message(CommandStart())
async def start_handler(message: types.Message) -> None:
    user = message.from_user
    db.upsert_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    log_action(user.id, user.username, "СТАРТ БОТА", chat=message.chat)

    welcome_text = (
        "🍓 <b>Добро пожаловать в Nexon бот репутации!</b>\n\n"
        "Powered by Nexon Group Solution \n"
        "Этот бот помогает покупателям и продавцам клубники "
        "безопасно работать друг с другом.\n\n"
        "🌟 <b>Возможности бота:</b>\n"
        "  ✍️ <b>Оставить отзыв</b> — прямо в меню, без команд\n"
        "  🔍 <b>Искать репутацию</b> — быстрая проверка любого продавца\n"
        "  📊 <b>Топ продавцов</b> — рейтинг лучших по отзывам\n"
        "  📡 <b>Мониторинг каналов</b> — автосбор отзывов из каналов\n"
        "  🛡️ <b>Защита от накрутки</b> — антиспам и верификация\n\n"
        "💡 Быстрый вызов мастера отзыва — команда /rep\n\n"
        "Выбери действие в меню 👇"
    )

    await message.answer(
        welcome_text,
        reply_markup=main_menu_kb(admin=is_admin(user.id), private=is_private(message)),
    )

@dp.message(Command("help"))
async def help_handler(message: types.Message) -> None:
    help_text = (
        "❓ <b>Как пользоваться Nexon ботом</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "✍️ <b>Как оставить отзыв</b>\n"
        "👉 Самый простой способ — кнопка <b>«✍️ Оставить отзыв»</b> в меню "
        "или команда /rep: бот сам спросит тип отзыва, продавца, описание "
        "и скриншоты по шагам.\n\n"
        "Также можно по-старому текстом в группе:\n"
        "<code>+реп @username описание сделки</code>\n"
        "<code>-реп @username описание проблемы</code>\n"
        "Можно указать числовой Telegram ID вместо @username, "
        "или ответить (reply) на сообщение продавца.\n"
        "и <b>прикрепи скриншот</b> сделки!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>Как проверить продавца</b>\n"
        "• Команда: <code>/check @username</code>\n"
        "• Ответь (reply) на его сообщение командой <code>/check</code>\n"
        "• Или нажми кнопку <b>🔍 Проверить продавца</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Правила</b>\n"
        "📸 Скриншот сделки обязателен\n"
        "📝 Описание минимум 2 символов\n"
        "🚫 Нельзя оставлять отзыв самому себе\n"
        "⏰ Один отзыв об одном человеке в сутки\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎨 <b>Уровни доверия</b>\n"
        "🟢 <b>Проверенный</b> — рейтинг 80–100%\n"
        "🟡 <b>Надёжный</b> — рейтинг 40–79%\n"
        "🟠 <b>Сомнительный</b> — рейтинг −20–39%\n"
        "🔴 <b>Мошенник</b> — рейтинг ниже −20%"
    )

    if hasattr(message, "edit_text"):
        try:
            await message.edit_text(help_text, reply_markup=back_kb())
        except TelegramAPIError:
            await message.answer(help_text, reply_markup=back_kb())
    else:
        await message.answer(help_text, reply_markup=back_kb())

@dp.message(Command("check"))
async def check_command(message: types.Message, command: CommandObject) -> None:
    identifier: Optional[str] = None

    if command.args:
        identifier = command.args.split()[0].lstrip("@")
        if not re.match(r"^\w+$", identifier):
            await message.answer(
                "❌ Некорректный запрос. "
                "Используй @username или числовой Telegram ID.",
                reply_markup=back_kb(),
            )
            return

    if identifier is None and message.reply_to_message is None:
        await message.answer(
            "🔍 <b>Укажи имя пользователя или ID</b>\n\n"
            "Пример: <code>/check @username</code> или <code>/check 123456789</code>\n"
            "Либо ответь (reply) на сообщение продавца и напиши просто <code>/check</code>.",
            reply_markup=back_kb(),
        )
        return

    await send_reputation_card(message, identifier, source_message=message)


@dp.message(F.text.regexp(r"^\s*(?:@?\w+\s*[+\-]\s*реп|[+\-]\s*реп\s+@?\w+)"))
@dp.message(F.caption.regexp(r"^\s*(?:@?\w+\s*[+\-]\s*реп|[+\-]\s*реп\s+@?\w+)"))
async def reputation_handler(message: types.Message) -> None:
    text = get_target_text(message)
    parsed = parse_rep_message(text)

    if parsed is None:
        return

    sign, identifier, description = parsed
    reviewer = message.from_user

    db.upsert_user(
        telegram_id=reviewer.id,
        username=reviewer.username or "",
        first_name=reviewer.first_name or "",
    )

    if db.is_banned(reviewer.id):
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await message.answer(
            "🚫 <b>Ваш аккаунт заблокирован</b>\n"
            "Вы не можете оставлять отзывы в этом боте."
        )
        return

    target_id, target_username = await resolve_target_smart(identifier, message)
    if target_id is None:
        await message.answer(
            f"❌ <b>Не удалось найти пользователя @{escape(target_username)}</b>\n\n"
            f"Telegram не позволяет боту искать людей по username, если они "
            f"ни разу не писали этому боту.\n\n"
            f"✅ Ответь (reply) на сообщение продавца и повтори "
            f"<code>+реп описание</code> — тогда бот найдёт его точно, "
            f"либо укажи числовой Telegram ID."
        )
        return

    if target_id == reviewer.id:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await message.answer(
            "🚫 <b>Нельзя оставить отзыв самому себе!</b>\n"
            "Попроси другого пользователя оставить отзыв."
        )
        return

    delete_photo_msg = message.chat.type in ("group", "supergroup")
    photos = await collect_review_photos(message, delete_after=delete_photo_msg)
    if not photos:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await message.answer(
            "📸 <b>Прикрепи скриншот сделки!</b>\n\n"
            "Без подтверждения отзыв не принимается.\n"
            "Отправь одно или несколько фото вместе с командой "
            "<code>+реп @username описание</code>",
        )
        return

    if len(description) < 2:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await message.answer(
            "📝 <b>Добавь описание сделки!</b>\n\n"
            "Описание должно содержать <b>минимум 2 символов</b>.\n"
            "Расскажи подробнее о сделке: что купил, когда, впечатления.",
        )
        return

    if db.has_recent_review(reviewer.id, target_id, hours=0.03):
        await message.answer(
            f"⏰ <b>Подождите 2 минуты</b>\n\n"
            f"Вы уже оставляли отзыв о {display_name(target_id, target_username)} сегодня.\n"
            f"Повторный отзыв можно будет оставить через 2 минуты.",
        )
        return


    chat = message.chat
    review_id = db.add_review(
        target_id=target_id,
        target_username=target_username,
        reviewer_id=reviewer.id,
        reviewer_username=reviewer.username or "",
        reviewer_name=reviewer.first_name or "",
        sign=sign,
        description=description,
        photo_file_id=",".join(photos),
        chat_id=chat.id,
        chat_title=chat.title or "",
        message_id=message.message_id,
        source="chat",
    )

    sign_emoji = "✅" if sign == "+" else "❌"
    sign_word = "положительный" if sign == "+" else "отрицательный"
    desc_preview = truncate(description, 300)
    label = display_name(target_id, target_username)
    photos_line = f"📸 <b>Скриншотов:</b> {len(photos)}\n" if len(photos) > 1 else ""

    confirmation = (
        f"🍓 <b>Отзыв сохранён!</b>\n\n"
        f"{sign_emoji} <b>Тип:</b> {sign_word}\n"
        f"👤 <b>Продавец:</b> {label}\n"
        f"{photos_line}"
        f"📝 <b>Описание:</b>\n"
        f"<blockquote>{escape(desc_preview)}</blockquote>\n\n"
        f"🔍 Проверить репутацию: /check {escape(target_username or target_id)}\n"
        f"🆔 ID отзыва: <code>{review_id}</code>"
    )

    await message.answer(confirmation)
    logger.info(
        f"Новый отзыв #{review_id}: {sign} реп target_id={target_id} "
    )
    log_action(
        reviewer.id,
        reviewer.username,
        f"НОВЫЙ ОТЗЫВ ({sign_word})",
        details=f"review_id={review_id} цель={label}(id={target_id}) фото={len(photos)} "
                f"описание='{description[:120]}'",
        chat=chat,
    )


@dp.channel_post(F.text.regexp(r"^\s*(?:@?\w+\s*[+\-]\s*реп|[+\-]\s*реп\s+@?\w+)"))
@dp.channel_post(F.caption.regexp(r"^\s*(?:@?\w+\s*[+\-]\s*реп|[+\-]\s*реп\s+@?\w+)"))
async def channel_reputation_handler(message: types.Message) -> None:
    text = get_target_text(message)
    parsed = parse_rep_message(text)
    if not parsed:
        return

    sign, identifier, description = parsed

    if len(description) < 2:
        return

    photos = await collect_review_photos(message)
    if not photos:
        return

    target_id, target_username = await resolve_target(identifier)
    if target_id is None:
        logger.info(f"Не удалось резолвить цель '{identifier}' в канальном отзыве")
        return

    chat = message.chat

    db.add_monitored_channel(
        chat_id=chat.id,
        chat_title=chat.title or str(chat.id),
    )

    review_id = db.add_review(
        target_id=target_id,
        target_username=target_username,
        reviewer_id=0,
        reviewer_username="",
        reviewer_name=chat.title or "",
        sign=sign,
        description=description,
        photo_file_id=",".join(photos),
        chat_id=chat.id,
        chat_title=chat.title or "",
        message_id=message.message_id,
        source="channel",
    )

    logger.info(
        f"Канальный отзыв #{review_id}: {sign} реп @{target_id} "
        f"из канала '{chat.title}' (id={chat.id})"
    )
    log_action(
        None,
        None,
        "НОВЫЙ ОТЗЫВ (канал)",
        details=f"review_id={review_id} цель=id={target_id} фото={len(photos)}",
        chat=chat,
    )


@dp.callback_query(F.data == "check")
async def check_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CheckState.waiting_for_username)
    await callback.message.edit_text(
        "🔍 <b>Введи @username или ID продавца</b>\n\n"
        "Напиши имя пользователя (с @ или без) либо числовой Telegram ID "
        "для проверки репутации.\n\n"
        "💡 Либо просто перешли/ответь (reply) на сообщение продавца в чате "
        "командой <code>/check</code> — так надёжнее.",
        reply_markup=back_kb(),
    )
    await callback.answer()


@dp.message(CheckState.waiting_for_username)
async def process_check_username(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().lstrip("@")

    if not re.match(r"^\w+$", raw):
        await message.answer(
            "❌ <b>Некорректное имя пользователя</b>\n\n"
            "Используй только буквы, цифры и подчёркивание (_).\n"
            "Попробуй ещё раз:",
            reply_markup=back_kb(),
        )
        return

    await state.clear()
    await send_reputation_card(message, raw, source_message=message)


async def start_review_wizard(msg: types.Message, is_edit: bool) -> None:
    text = (
        "✍️ <b>Оставляем отзыв о продавце</b>\n\n"
        "Шаг 1 из 3 — выбери тип отзыва:"
    )
    if is_edit:
        await msg.edit_text(text, reply_markup=review_sign_kb())
    else:
        await msg.answer(text, reply_markup=review_sign_kb())


@dp.message(Command("rep"))
async def rep_command(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ReviewState.choosing_sign)
    await start_review_wizard(message, is_edit=False)


@dp.callback_query(F.data == "leave_review")
async def leave_review_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ReviewState.choosing_sign)
    await start_review_wizard(callback.message, is_edit=True)
    await callback.answer()


@dp.callback_query(F.data.startswith("revsign_"), ReviewState.choosing_sign)
async def review_sign_callback(callback: CallbackQuery, state: FSMContext) -> None:
    sign = callback.data.split("_", 1)[1]
    await state.update_data(sign=sign)
    await state.set_state(ReviewState.waiting_target)
    sign_label = "✅ Положительный" if sign == "+" else "❌ Отрицательный"
    await callback.message.edit_text(
        f"✍️ <b>{sign_label} отзыв</b>\n\n"
        f"Шаг 2 из 3 — укажи продавца:\n\n"
        f"• Напиши его <code>@username</code> или числовой Telegram ID\n"
        f"• Либо просто <b>перешли (forward)</b> сюда любое его сообщение — "
        f"так бот найдёт его гарантированно, даже если username закрыт",
        reply_markup=review_cancel_kb(),
    )
    await callback.answer()


@dp.message(ReviewState.waiting_target)
async def review_target_handler(message: types.Message, state: FSMContext) -> None:
    reviewer = message.from_user
    if reviewer is None:
        await state.clear()
        await message.answer(
            "❌ Не удалось определить отправителя (анонимное сообщение). "
            "Отключи анонимность и начни оставление отзыва заново."
        )
        return
    target_id: Optional[int] = None
    username = ""

    if message.forward_from:
        fu = message.forward_from
        if fu.is_bot:
            await message.answer(
                "❌ Это бот, его нельзя выбрать продавцом. Попробуй ещё раз.",
                reply_markup=review_cancel_kb(),
            )
            return
        target_id = fu.id
        username = fu.username or ""
        db.upsert_user(telegram_id=target_id, username=username, first_name=fu.first_name or "")
    else:
        raw = (message.text or "").strip()
        if not raw:
            await message.answer(
                "❌ Отправь @username, числовой ID, либо перешли сообщение продавца.",
                reply_markup=review_cancel_kb(),
            )
            return
        target_id, username = await resolve_target(raw)

    if target_id is None:
        await message.answer(
            f"❌ <b>Не удалось найти @{escape(username)}</b>\n\n"
            f"Telegram не даёт искать по username людей, которые ни разу "
            f"не писали этому боту. Попробуй числовой ID или перешли "
            f"(forward) его сообщение сюда.",
            reply_markup=review_cancel_kb(),
        )
        return

    if target_id == reviewer.id:
        await message.answer(
            "🚫 <b>Нельзя оставить отзыв самому себе!</b>\nУкажи другого продавца.",
            reply_markup=review_cancel_kb(),
        )
        return

    if db.is_banned(reviewer.id):
        await state.clear()
        await message.answer(
            "🚫 <b>Ваш аккаунт заблокирован</b>\nВы не можете оставлять отзывы.",
            reply_markup=main_menu_kb(admin=is_admin(reviewer.id), private=is_private(message)),
        )
        return

    await state.update_data(target_id=target_id, target_username=username)
    await state.set_state(ReviewState.waiting_description)
    await message.answer(
        f"✍️ <b>Продавец: {display_name(target_id, username)}</b>\n\n"
        f"Шаг 3 из 3 — опиши сделку (минимум 3 символов): "
        f"что купил(а), когда, впечатления.",
        reply_markup=review_cancel_kb(),
    )


@dp.message(ReviewState.waiting_description)
async def review_description_handler(message: types.Message, state: FSMContext) -> None:
    text = (message.text or message.caption or "").strip()
    if len(text) < 3:
        await message.answer(
            "📝 Описание должно содержать <b>минимум 3 символов</b>. Попробуй ещё раз:",
            reply_markup=review_cancel_kb(),
        )
        return

    await state.update_data(description=text, photos=[])
    await state.set_state(ReviewState.waiting_photos)
    await message.answer(
        "📸 <b>Пришли скриншот(ы) сделки</b>\n\n"
        "Можно одно фото, несколько по очереди или сразу альбомом.\n"
        "Когда закончишь — жми «Готово».",
        reply_markup=review_photos_kb(0),
    )


@dp.message(ReviewState.waiting_photos, F.photo)
async def review_photo_handler(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = list(data.get("photos", []))

    if message.media_group_id:
        gid = message.media_group_id
        if gid in album_confirm_in_progress:
            return
        album_confirm_in_progress.add(gid)
        try:
            new_photos = await collect_review_photos(message)
        finally:
            album_confirm_in_progress.discard(gid)
    else:
        single = get_single_photo(message)
        new_photos = [single] if single else []

    for p in new_photos:
        if p not in photos:
            photos.append(p)

    await state.update_data(photos=photos)
    await message.answer(
        f"✅ Добавлено. Всего скриншотов: <b>{len(photos)}</b>.\n"
        f"Пришли ещё или нажми «Готово».",
        reply_markup=review_photos_kb(len(photos)),
    )


@dp.message(ReviewState.waiting_photos)
async def review_photos_wrong_input(message: types.Message) -> None:
    await message.answer(
        "📸 Жду именно фото (скриншот сделки). Пришли фото или нажми «Готово», "
        "если уже отправил(а) все скриншоты.",
        reply_markup=review_photos_kb(0),
    )


@dp.callback_query(F.data == "review_done", ReviewState.waiting_photos)
async def review_done_callback(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = list(data.get("photos", []))

    if not photos:
        await callback.answer("📸 Сначала пришли хотя бы один скриншот", show_alert=True)
        return

    sign = data["sign"]
    target_id = data["target_id"]
    target_username = data.get("target_username", "")
    description = data["description"]
    reviewer = callback.from_user

    if db.is_banned(reviewer.id):
        await state.clear()
        await callback.message.edit_text(
            "🚫 <b>Ваш аккаунт заблокирован</b>\nВы не можете оставлять отзывы.",
            reply_markup=main_menu_kb(
                admin=is_admin(reviewer.id), private=is_private(callback.message)
            ),
        )
        await callback.answer()
        return

    chat = callback.message.chat
    review_id = db.add_review(
        target_id=target_id,
        target_username=target_username,
        reviewer_id=reviewer.id,
        reviewer_username=reviewer.username or "",
        reviewer_name=reviewer.first_name or "",
        sign=sign,
        description=description,
        photo_file_id=",".join(photos),
        chat_id=chat.id,
        chat_title=chat.title or "",
        message_id=callback.message.message_id,
        source="menu",
    )

    label = display_name(target_id, target_username)
    sign_emoji = "✅" if sign == "+" else "❌"
    sign_word = "положительный" if sign == "+" else "отрицательный"
    desc_preview = truncate(description, 300)

    confirmation = (
        f"🍓 <b>Отзыв сохранён!</b>\n\n"
        f"{sign_emoji} <b>Тип:</b> {sign_word}\n"
        f"👤 <b>Продавец:</b> {label}\n"
        f"📸 <b>Скриншотов:</b> {len(photos)}\n"
        f"📝 <b>Описание:</b>\n"
        f"<blockquote>{escape(desc_preview)}</blockquote>\n\n"
        f"🆔 ID отзыва: <code>{review_id}</code>"
    )

    await state.clear()
    await callback.message.edit_text(confirmation, reply_markup=view_user_kb(target_id, target_username))
    await callback.answer("Сохранено! 🍓")
    logger.info(
        f"Новый отзыв #{review_id} (через меню): {sign} target_id={target_id} "
    )
    log_action(
        reviewer.id,
        reviewer.username,
        f"НОВЫЙ ОТЗЫВ через меню ({sign_word})",
        details=f"review_id={review_id} цель={label}(id={target_id}) фото={len(photos)} "
                f"описание='{description[:120]}'",
        chat=chat,
    )


@dp.callback_query(F.data == "review_cancel")
async def review_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Оставление отзыва отменено</b>",
        reply_markup=main_menu_kb(
            admin=is_admin(callback.from_user.id), private=is_private(callback.message)
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("view_"))
async def view_callback(callback: CallbackQuery) -> None:
    identifier = callback.data.split("_", 1)[1]
    await send_reputation_card(callback.message, identifier)
    await callback.answer()


@dp.callback_query(F.data.startswith("photos_"))
async def photos_menu_callback(callback: CallbackQuery) -> None:
    try:
        target_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    username = db.get_username_for_id(target_id) or ""
    label = display_name(target_id, username)

    reviews = db.get_user_reviews(target_id, limit=20)
    reviews_with_photos = [rev for rev in reviews if get_review_photo_ids(rev)]

    if not reviews_with_photos:
        await callback.answer("😔 Скриншотов пока нет", show_alert=True)
        return

    rows: list[list[InlineKeyboardButton]] = []
    for i, rev in enumerate(reviews_with_photos, start=1):
        review_id = get_review_id(rev)
        ref = str(review_id) if review_id is not None else f"idx{i - 1}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=deal_button_label(rev, i),
                    callback_data=f"rphotos_{target_id}_{ref}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад к репутации", callback_data=f"view_{target_id}")])

    await callback.message.answer(
        f"📸 <b>Скриншоты сделок — {label}</b>\n\n"
        f"Выбери сделку, чтобы посмотреть её скриншоты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("rphotos_"))
async def review_photos_callback(callback: CallbackQuery) -> None:
    try:
        _, target_id_str, ref = callback.data.split("_", 2)
        target_id = int(target_id_str)
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    reviews = db.get_user_reviews(target_id, limit=20)
    reviews_with_photos = [rev for rev in reviews if get_review_photo_ids(rev)]

    rev = None
    if ref.startswith("idx"):
        try:
            idx = int(ref[3:])
            rev = reviews_with_photos[idx]
        except (ValueError, IndexError):
            rev = None
    else:
        try:
            wanted_id = int(ref)
        except ValueError:
            wanted_id = None
        if wanted_id is not None:
            for r in reviews_with_photos:
                if get_review_id(r) == wanted_id:
                    rev = r
                    break

    if rev is None:
        await callback.answer("❌ Сделка не найдена (возможно, отзыв удалён)", show_alert=True)
        return

    photo_ids = get_review_photo_ids(rev)
    sign_emoji = "✅" if rev["sign"] == "+" else "❌"
    sign_word = "положительный" if rev["sign"] == "+" else "отрицательный"
    date_str = str(rev["created_at"])[:10]
    reviewer_tag = (
        f"@{escape(rev['reviewer_username'])}"
        if rev["reviewer_username"]
        else escape(rev["reviewer_name"] or "Аноним")
    )
    desc = truncate(rev["description"] or "", 500)

    caption_lines = [
        f"{sign_emoji} <b>{sign_word.capitalize()} отзыв</b> ({date_str})",
    ]
    if desc:
        caption_lines.append(f"📝 {escape(desc)}")
    caption = "\n".join(caption_lines)

    media: list[types.InputMediaPhoto] = []
    for i, photo_id in enumerate(photo_ids[:10]):
        media.append(
            types.InputMediaPhoto(
                media=photo_id,
                caption=caption if i == 0 else None,
            )
        )

    try:
        if len(media) == 1:
            await callback.message.answer_photo(media[0].media, caption=caption)
        else:
            await callback.message.answer_media_group(media)
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки скриншотов сделки: {e}")
        await callback.answer("❌ Не удалось отправить скриншоты", show_alert=True)
        return

    await callback.message.answer(
        "⬆️ Скриншоты этой сделки",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ К списку сделок", callback_data=f"photos_{target_id}")],
                [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")],
            ]
        ),
    )
    await callback.answer()


@dp.callback_query(F.data == "top")
async def top_callback(callback: CallbackQuery) -> None:
    top_users = db.get_top_users(limit=10, min_reviews=2)

    if not top_users:
        await callback.message.edit_text(
            "📊 <b>Топ продавцов</b>\n\n"
            "😔 Пока никто не попал в топ.\n"
            "Нужно минимум <b>2 отзыва</b> для попадания в рейтинг.",
            reply_markup=back_kb(),
        )
        await callback.answer()
        return

    medals = ["🥇", "🥈", "🥉", "🏅", "🏅", "🏅", "🏅", "🏅", "🏅", "🏅"]
    lines = ["📊 <b>Топ продавцов клубники</b>\n"]

    for i, user in enumerate(top_users):
        medal = medals[i] if i < len(medals) else "▫️"
        bar = trust_bar(user["score"])
        lines.append(
            f"{medal} <b>@{escape(user['username'])}</b>\n"
            f"   {bar}\n"
            f"   ✅ {user['positive']} / ❌ {user['negative']} "
            f"(всего: {user['total']})\n"
        )

    lines.append("\n🍓 <i>Топ формируется по количеству и качеству отзывов</i>")

    await callback.message.edit_text("\n".join(lines), reply_markup=back_kb())
    await callback.answer()


@dp.callback_query(F.data == "stats")
async def stats_callback(callback: CallbackQuery) -> None:
    s = db.get_global_stats()

    stats_text = (
        "📈 <b>Статистика nexon бота</b>\n\n"
        f"👥 <b>Пользователей:</b> {s['users']}\n"
        f"📝 <b>Всего отзывов:</b> {s['total_reviews']}\n"
        f"   ✅ Положительных: {s['positive']}\n"
        f"   ❌ Отрицательных: {s['negative']}\n"
        f"💬 <b>Активных чатов:</b> {s['chats']}\n"
        f"📡 <b>Мониторируемых каналов:</b> {s['channels']}\n\n"
        f"🍓 <i>Помогаем делать бизнес честным!</i>"
    )

    kb = admin_panel_kb() if is_admin(callback.from_user.id) else back_kb()
    await callback.message.edit_text(stats_text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    await help_handler(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "menu")
async def menu_callback(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.clear()

    welcome_text = (
        "<b>Бот репутации</b>\n\n"
        "Выбери действие в меню 👇"
    )

    try:
        await callback.message.edit_text(
            welcome_text,
            reply_markup=main_menu_kb(
                admin=is_admin(callback.from_user.id),
                private=is_private(callback.message),
            ),
        )
    except TelegramAPIError as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Ошибка при возврате в меню: {e}")

    await callback.answer()


@dp.callback_query(F.data == "garant_menu")
async def garant_menu_callback(callback: CallbackQuery) -> None:
    text = (
        "🛡️ <b>Гарант сделок</b>\n\n"
        "Выбери, каким способом хочешь провести безопасную сделку:\n\n"
        "🧑‍💼 <b>Живой гарант</b> — сделка через реального человека-гаранта\n"
        "💰 <b>Деп</b> — автоматический депозит-гарант"
    )
    try:
        await callback.message.edit_text(text, reply_markup=garant_menu_kb())
    except TelegramAPIError as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Ошибка открытия меню гаранта: {e}")
    await callback.answer()


@dp.callback_query(F.data == "garant_live")
async def garant_live_callback(callback: CallbackQuery) -> None:
    text = (
        "🧑‍💼 <b>Живой гарант</b>\n\n"
        f"👤 @{escape(GARANT_USERNAME)}\n\n"
        f"Это <b>гарант</b> — обращайся к нему для проведения безопасной "
        f"сделки между покупателем и продавцом."
    )
    await callback.message.edit_text(text, reply_markup=garant_back_kb())
    await callback.answer()


@dp.callback_query(F.data == "garant_dep")
async def garant_dep_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    balance = db.get_balance(callback.from_user.id)

    await state.set_state(WithdrawState.waiting_amount)

    await callback.message.edit_text(
        f"""
💰 <b>Депозит-гарант</b>

Переведите средства в депозит.

Ваш баланс:
<b>{balance:.2f} USDT</b>

Введите сумму депозита:
""",
        reply_markup=back_kb()
    )

    await callback.answer()

@dp.callback_query(F.data=="balance")
async def balance_callback(callback:CallbackQuery):

    balance = db.get_balance(
        callback.from_user.id
    )

    text = (
        "💳 <b>Ваш баланс</b>\n\n"
        f"💰 {balance:.2f} USDT"
    )

    await callback.message.edit_text(
        text,
        reply_markup=balance_kb()
    )
    await callback.answer()

@dp.callback_query(F.data=="deposit")
async def deposit_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.set_state(
        DepositState.waiting_amount
    )
    await callback.message.edit_text(
        """
            💰 <b>Пополнение баланса</b>
            Пополнение — только через <b>CryptoBot</b>, USDT в сети <b>TRC20</b>.
            Введите сумму в USDT:
            Минимальная сумма:
            <b>1 USDT</b>
        """,
        reply_markup=back_kb()
    )

    await callback.answer()


@dp.callback_query(F.data == "transfer")
async def transfer_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.set_state(TransferState.waiting_user)

    await callback.message.edit_text(
        """
🔄 <b>Передача баланса</b>

Введите <b>@username</b> получателя:

        """,
        reply_markup=back_kb()
    )

    await callback.answer()

@dp.message(TransferState.waiting_user)
async def transfer_user(
    message: types.Message,
    state: FSMContext
):
    username = (message.text or "").strip().lstrip("@")

    user = db.get_user_by_username(username)

    if user is None:
        await message.answer(
            "❌ Пользователь не найден.\n\n"
            "Он должен вступить в группу и хотя бы один раз запустить бота."
        )
        return

    await state.update_data(
        target=user["user_id"],
        username=username
    )

    await state.set_state(
        TransferState.waiting_amount
    )

    await message.answer(
        f"👤 Получатель: @{username}\n\n"
        "💰 Введите сумму перевода:",
        reply_markup=back_kb()
    )


@dp.message(TransferState.waiting_amount)
async def transfer_amount(
    message:types.Message,
    state:FSMContext
):

    data=await state.get_data()
    target=data["target"]
    amount=float(message.text)
    sender=message.from_user.id
    if db.get_balance(sender)<amount:
        await message.answer(
            "❌ Недостаточно средств"
        )
        return

    db.remove_balance(
        sender,
        amount
    )

    db.add_balance(
        target,
        amount
    )

    log_action(
        sender,
        message.from_user.username,
        "ПЕРЕВОД",
        details=(
            f"получатель={target} сумма={amount} USDT "
            f"баланс_отправителя_после={db.get_balance(sender):.2f} "
            f"баланс_получателя_после={db.get_balance(target):.2f}"
        ),
        chat=message.chat,
    )

    await message.answer(
        f"""
            ✅ <b>Перевод выполнен</b>

            👤 Получатель:
            <code>{target}</code>

            💰 Сумма:
            <b>{amount} USDT</b>
        """
    )

    await state.clear()


async def check_crypto_payments():
    invoices = await crypto.get_invoices(
        status="paid"
    )
    if not invoices:
        return

    for inv in invoices:
        invoice_id=str(inv.invoice_id)
        data=db.get_invoice(invoice_id)

        if not data:
            continue

        if data["status"]=="paid":
            continue

        db.add_balance(
            data["telegram_id"],
            float(data["amount"])
        )

        log_action(
            data["telegram_id"],
            db.get_username_for_id(data["telegram_id"]),
            "ПОПОЛНЕНИЕ (подтверждено CryptoBot)",
            details=(
                f"сумма={float(data['amount']):.2f} USDT "
                f"invoice_id={invoice_id} "
                f"баланс_после={db.get_balance(data['telegram_id']):.2f}"
            ),
        )
        db.mark_invoice_paid(invoice_id)


@dp.message(DepositState.waiting_amount)
async def process_deposit_amount(
    message: types.Message,
    state: FSMContext
):
    try:
        amount = float(
            message.text.replace(",", ".")
        )
    except ValueError:
        await message.answer(
            "❌ Введите только число\n\nНапример: 10"
        )
        return
    if amount < 1:
        await message.answer(
            "❌ Минимальное пополнение 1 USDT"
        )
        return
    invoice = await crypto.create_invoice(
        asset="USDT",
        amount=amount,
        payload=str(message.from_user.id)
    )
    db.save_invoice(
        invoice_id=str(invoice.invoice_id),
        telegram_id=message.from_user.id,
        amount=amount,
        asset="USDT"
    )
    log_action(
        message.from_user.id,
        message.from_user.username,
        "ПОПОЛНЕНИЕ (счёт создан)",
        details=f"сумма={amount:.2f} USDT invoice_id={invoice.invoice_id}",
        chat=message.chat,
    )
    await message.answer(
        f"""
            💰 <b>Счёт создан</b>
            Сумма:
            <b>{amount:.2f} USDT</b>
            Оплатить:
            {invoice.bot_invoice_url}

            ⚠️ Пополняйте только через <b>CryptoBot</b>, сеть <b>TRC20</b>.
            После оплаты баланс будет автоматически пополнен.
        """,
        reply_markup=back_kb()
    )

    await state.clear()


def admin_panel_text(user_id: int) -> str:
    return (
        "🛡️ <b>Панель администратора</b>\n\n"
        f"👮 <b>Ваш ID:</b> <code>{user_id}</code>\n\n"
        "Выбери действие ниже 👇"
    )


@dp.message(Command("admin"))
async def admin_handler(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.clear()
    log_action(message.from_user.id, message.from_user.username, "ОТКРЫТА АДМИН-ПАНЕЛЬ", chat=message.chat)
    await message.answer(
        admin_panel_text(message.from_user.id),
        reply_markup=admin_panel_kb(),
    )


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён", show_alert=True)
        return

    await state.clear()
    try:
        await callback.message.edit_text(
            admin_panel_text(callback.from_user.id), reply_markup=admin_panel_kb()
        )
    except TelegramAPIError as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Ошибка открытия админ-панели: {e}")
    await callback.answer()


@dp.callback_query(F.data == "admin_delreview")
async def admin_delreview_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminState.waiting_delreview_id)
    await callback.message.edit_text(
        "🗑️ <b>Удаление отзыва</b>\n\n"
        "Пришли числовой <b>ID отзыва</b>, который нужно удалить "
        "(его видно в карточке отзыва как «ID отзыва»).",
        reply_markup=admin_cancel_kb(),
    )
    await callback.answer()


@dp.message(AdminState.waiting_delreview_id)
async def admin_delreview_input(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    raw = (message.text or "").strip()
    try:
        review_id = int(raw)
    except ValueError:
        await message.answer(
            "❌ ID должен быть числом. Попробуй ещё раз:",
            reply_markup=admin_cancel_kb(),
        )
        return

    success = db.delete_review(review_id)
    await state.clear()
    if success:
        await message.answer(
            f"✅ Отзыв <code>#{review_id}</code> успешно удалён.",
            reply_markup=admin_panel_kb(),
        )
        logger.info(f"Админ {message.from_user.id} удалил отзыв #{review_id}")
        log_action(
            message.from_user.id,
            message.from_user.username,
            "УДАЛЕНИЕ ОТЗЫВА",
            details=f"review_id={review_id} результат=успешно",
            chat=message.chat,
        )
    else:
        await message.answer(
            f"❌ Отзыв <code>#{review_id}</code> не найден.",
            reply_markup=admin_panel_kb(),
        )
        log_action(
            message.from_user.id,
            message.from_user.username,
            "УДАЛЕНИЕ ОТЗЫВА",
            details=f"review_id={review_id} результат=не_найден",
            chat=message.chat,
        )


@dp.callback_query(F.data == "admin_ban")
async def admin_ban_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminState.waiting_ban_target)
    await callback.message.edit_text(
        "🚫 <b>Блокировка пользователя</b>\n\n"
        "Пришли <code>@username причина</code> или <code>ID причина</code>.\n"
        "Например: <code>@username накрутка отзывов</code>\n\n"
        "Причину можно не указывать — тогда будет «Не указана».",
        reply_markup=admin_cancel_kb(),
    )
    await callback.answer()

@dp.callback_query(F.data == "stats")
async def stats_callback(callback: CallbackQuery) -> None:
    s = db.get_global_stats()

    stats_text = (
        "📈 <b>Статистика nexon бота</b>\n\n"
        f"👥 <b>Пользователей:</b> {s['users']}\n"
        f"📝 <b>Всего отзывов:</b> {s['total_reviews']}\n"
        f"   ✅ Положительных: {s['positive']}\n"
        f"   ❌ Отрицательных: {s['negative']}\n"
        f"💬 <b>Активных чатов:</b> {s['chats']}\n"
        f"📡 <b>Мониторируемых каналов:</b> {s['channels']}\n\n"
        f"🍓 <i>Помогаем делать бизнес честным!</i>"
    )

    kb = admin_panel_kb() if is_admin(callback.from_user.id) else back_kb()
    await callback.message.edit_text(stats_text, reply_markup=kb)
    await callback.answer()




@dp.message(AdminState.waiting_ban_target)
async def admin_ban_input(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer(
            "❌ Отправь @username или ID и причину.",
            reply_markup=admin_cancel_kb(),
        )
        return

    parts = raw.split(None, 1)
    identifier = parts[0].lstrip("@").lower()
    reason = parts[1] if len(parts) > 1 else "Не указана"

    target_id, target_username = await resolve_target_smart(identifier, message)
    if target_id is None:
        await message.answer(
            f"❌ Не удалось найти пользователя @{escape(target_username)}.\n"
            f"Попробуй ещё раз или укажи числовой ID:",
            reply_markup=admin_cancel_kb(),
        )
        return

    db.ban_user(target_id, reason)
    await state.clear()
    label = display_name(target_id, target_username)
    await message.answer(
        f"🚫 Пользователь {label} заблокирован.\n📋 Причина: {escape(reason)}",
        reply_markup=admin_panel_kb(),
    )
    logger.info(
        f"Админ {message.from_user.id} забанил target_id={target_id} (причина: {reason})"
    )
    log_action(
        message.from_user.id,
        message.from_user.username,
        "БАН ПОЛЬЗОВАТЕЛЯ",
        details=f"цель={label}(id={target_id}) причина='{reason}'",
        chat=message.chat,
    )


@dp.callback_query(F.data == "admin_unban")
async def admin_unban_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminState.waiting_unban_target)
    await callback.message.edit_text(
        "✅ <b>Разблокировка пользователя</b>\n\n"
        "Пришли <code>@username</code> или числовой Telegram ID.",
        reply_markup=admin_cancel_kb(),
    )
    await callback.answer()


@dp.message(AdminState.waiting_unban_target)
async def admin_unban_input(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    identifier = (message.text or "").strip().lstrip("@")
    if not identifier:
        await message.answer(
            "❌ Отправь @username или числовой ID.",
            reply_markup=admin_cancel_kb(),
        )
        return

    target_id, target_username = await resolve_target_smart(identifier, message)
    if target_id is None:
        await message.answer(
            f"❌ Не удалось найти пользователя @{escape(target_username)}.",
            reply_markup=admin_cancel_kb(),
        )
        return

    db.unban_user(target_id)
    await state.clear()
    label = display_name(target_id, target_username)
    await message.answer(
        f"✅ Пользователь {label} разблокирован.",
        reply_markup=admin_panel_kb(),
    )
    logger.info(f"Админ {message.from_user.id} разбанил target_id={target_id}")
    log_action(
        message.from_user.id,
        message.from_user.username,
        "РАЗБАН ПОЛЬЗОВАТЕЛЯ",
        details=f"цель={label}(id={target_id})",
        chat=message.chat,
    )

@dp.message(Command("delreview"))
async def delreview_handler(message: types.Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer(
            "Пример:\n"
            "<code>/delreview @username 2</code>"
        )
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer(
            "Используй:\n"
            "<code>/delreview @username 2</code>"
        )
        return
    username = parts[0]
    try:
        review_number = int(parts[1])
    except ValueError:
        await message.answer("Номер отзыва должен быть числом.")
        return
    review = db.get_review_by_number(username, review_number)
    if review is None:
        await message.answer("Такого отзыва нет.")
        return
    success = db.delete_review(review["id"])
    if not success:
        await message.answer("❌ Не удалось удалить отзыв (уже удалён?).")
        return
    await message.answer(
        f"✅ Удалён отзыв №{review_number} (id={review['id']}) пользователя {username}"
    )
    log_action(
        message.from_user.id,
        message.from_user.username,
        "УДАЛЕНИЕ ОТЗЫВА (текстовая команда)",
        details=f"цель={username} номер={review_number} review_id={review['id']}",
        chat=message.chat,
    )

@dp.message(Command("banuser"))
async def banuser_handler(message: types.Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer(
            "❌ Укажи username и причину.\n"
            "Пример: <code>/banuser @username накрутка отзывов</code>"
        )
        return

    parts = command.args.strip().split(None, 1)
    identifier = parts[0].lstrip("@").lower()
    reason = parts[1] if len(parts) > 1 else "Не указана"

    target_id, target_username = await resolve_target_smart(identifier, message)
    if target_id is None:
        await message.answer(
            f"❌ Не удалось найти пользователя @{escape(target_username)}."
        )
        return

    db.ban_user(target_id, reason)
    label = display_name(target_id, target_username)
    await message.answer(
        f"🚫 Пользователь {label} заблокирован.\n"
        f"📋 Причина: {escape(reason)}"
    )
    logger.info(
        f"Админ {message.from_user.id} забанил target_id={target_id} (причина: {reason})"
    )
    log_action(
        message.from_user.id,
        message.from_user.username,
        "БАН ПОЛЬЗОВАТЕЛЯ (текстовая команда)",
        details=f"цель={label}(id={target_id}) причина='{reason}'",
        chat=message.chat,
    )


@dp.message(Command("unban"))
async def unban_handler(message: types.Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer(
            "❌ Укажи username или ID.\nПример: <code>/unban @username</code>"
        )
        return

    identifier = command.args.strip().split()[0].lstrip("@")
    target_id, target_username = await resolve_target_smart(identifier, message)
    if target_id is None:
        await message.answer(
            f"❌ Не удалось найти пользователя @{escape(target_username)}."
        )
        return

    db.unban_user(target_id)
    label = display_name(target_id, target_username)
    await message.answer(f"✅ Пользователь{label} разблокирован.")
    logger.info(f"Админ {message.from_user.id} разбанил target_id{target_id}")
    log_action(
        message.from_user.id,
        message.from_user.username,
        "РАЗБАН ПОЛЬЗОВАТЕЛЯ (текстовая команда)",
        details=f"цель={label}(id={target_id})",
        chat=message.chat,
    )

async def crypto_payments_loop() -> None:
    while True:
        try:
            await check_crypto_payments()
        except Exception as e:
            logger.error(f"Ошибка при проверке платежей CryptoPay: {e}")
        await asyncio.sleep(15)

async def on_startup() -> None:
    db.init()

    await bot.set_my_commands(
        [
            types.BotCommand(command="start", description="🍓 Главное меню"),
            types.BotCommand(command="rep",   description="✍️ Оставить отзыв"),
            types.BotCommand(command="check", description="🔍 Проверить продавца"),
            types.BotCommand(command="help",  description="❓ Как пользоваться"),
        ]
    )

    admin_commands = [
        types.BotCommand(command="start",    description="🍓 Главное меню"),
        types.BotCommand(command="admin",    description="🛡️ Админ-панель"),
        types.BotCommand(command="balance",  description="💰 Баланс"),
        types.BotCommand(command="rep",      description="✍️ Оставить отзыв"),
        types.BotCommand(command="check",    description="🔍 Проверить продавца"),
        types.BotCommand(command="help",     description="❓ Как пользоваться"),
    ]
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands, scope=types.BotCommandScopeChat(chat_id=admin_id))
        except TelegramAPIError as e:
            logger.warning(f"Не удалось задать команды для админа {admin_id}: {e}")

    me = await bot.get_me()
    logger.info(
        f"🍓 Бот @{me.username} запущен! "
        f"Администраторы: {ADMIN_IDS if ADMIN_IDS else 'не назначены'}"
    )
    logger.info(f"Логи пишутся в папку: {os.path.abspath(LOGS_DIR)}")


async def shutdown() -> None:
    try:
        await bot.session.close()
    except Exception:
        pass
    try:
        await crypto.close()
    except Exception:
        pass

async def main() -> None:
    db.init()
    dp.startup.register(on_startup)
    logger.info("🍓 Nexon Reputation Bot стартует...")
    asyncio.create_task(crypto_payments_loop())
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown()

if __name__ == "__main__":
    asyncio.run(main())