import asyncio
import logging
import os
import re
from html import escape as html_escape
from typing import Optional, Tuple
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from dotenv import load_dotenv

from database import Database

# ---------------------------------------------------------------------------
# Загрузка переменных окружения
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError(
        "❌ BOT_TOKEN не задан! Создай файл .env и укажи BOT_TOKEN=<твой токен>"
    )

ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]
DB_PATH: str = os.getenv("DB_PATH", "reputation.db")

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

class CheckState(StatesGroup):
    waiting_for_username = State()



# ---------------------------------------------------------------------------
# Глобальные объекты
# ---------------------------------------------------------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database(DB_PATH)


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Проверить продавца", callback_data="check")],
            [
                InlineKeyboardButton(text="📊 Топ продавцов", callback_data="top"),
                InlineKeyboardButton(text="📈 Статистика", callback_data="stats"),
            ],
            [InlineKeyboardButton(text="❓ Как пользоваться", callback_data="help")],
        ]
    )


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu")]]
    )


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


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❓ Как пользоваться", callback_data="help"),
                InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Паттерн распознавания команды репутации
# ---------------------------------------------------------------------------
REP_PATTERN = re.compile(
    r"^\s*([+\-])\s*реп\s+@?(\w+)\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def parse_rep_message(
    text: str,
) -> Optional[Tuple[str, str, str]]:
    """
    Парсит команду репутации из текста.
    Возвращает (знак, identifier, описание) или None.
    """
    match = REP_PATTERN.match(text)
    if not match:
        return None
    sign = match.group(1)
    identifier = match.group(2)
    description = match.group(3).strip()
    return sign, identifier, description


# Буфер для сборки альбомов (media group). Telegram присылает каждое фото
# альбома отдельным апдейтом с одинаковым media_group_id, а подпись
# ("+реп ...") прикрепляется только к одному из них — остальные приходят
# без текста. Собираем их сюда через outer middleware ниже.
album_buffers: dict[str, list[types.Message]] = {}


@dp.message.outer_middleware()
async def album_collector_middleware(handler, event: types.Message, data: dict):
    """Складывает все сообщения одного альбома в общий буфер по media_group_id."""
    if event.media_group_id:
        album_buffers.setdefault(event.media_group_id, []).append(event)
    return await handler(event, data)


@dp.channel_post.outer_middleware()
async def album_collector_channel_middleware(handler, event: types.Message, data: dict):
    """То же самое, но для постов в каналах (dp.channel_post — отдельный поток апдейтов)."""
    if event.media_group_id:
        album_buffers.setdefault(event.media_group_id, []).append(event)
    return await handler(event, data)


def get_single_photo(message: types.Message) -> Optional[str]:
    """Одно фото — из самого сообщения либо из reply-сообщения."""
    if message.photo:
        return message.photo[-1].file_id
    if message.reply_to_message and message.reply_to_message.photo:
        return message.reply_to_message.photo[-1].file_id
    return None


async def collect_review_photos(message: types.Message) -> list[str]:
    """
    Собирает ВСЕ фото сделки:
    • если пользователь прислал альбом (несколько фото одним сообщением) —
      ждём немного, пока все фото альбома долетят, и берём их все;
    • иначе — одно фото из самого сообщения или из reply.
    """
    if message.media_group_id:
        gid = message.media_group_id
        # Небольшая пауза, чтобы все фото альбома успели попасть в буфер
        await asyncio.sleep(1.5)
        messages = album_buffers.pop(gid, [message])
        # Сохраняем порядок отправки пользователем
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
            return photo_ids

    single = get_single_photo(message)
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


def escape(text: str) -> str:
    return html_escape(str(text), quote=False)


def truncate(text: str, max_len: int = 200) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def display_name(target_id: int, username: str = "") -> str:
    return f"@{escape(username)}" if username else f"ID {target_id}"


def get_review_photo_ids(rev) -> list[str]:
    """
    Безопасно достаёт список photo_file_id из строки отзыва (dict или sqlite3.Row).
    Несколько фото хранятся в одном текстовом поле через запятую (в file_id
    Telegram запятых не бывает, так что разделитель безопасен).
    """
    try:
        value = rev["photo_file_id"]
    except (KeyError, IndexError, TypeError):
        return []
    if not value:
        return []
    return [pid for pid in str(value).split(",") if pid]


def get_review_id(rev) -> Optional[int]:
    """Безопасно достаёт первичный ключ отзыва (id / review_id) из строки БД."""
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
    """Короткая подпись сделки для кнопки в меню скриншотов."""
    sign_emoji = "✅" if rev["sign"] == "+" else "❌"
    date_str = str(rev["created_at"])[:10]
    n_photos = len(get_review_photo_ids(rev))
    return f"{index}. {sign_emoji} {date_str} · {n_photos} фото"


# ---------------------------------------------------------------------------
# Резолвинг идентификатора (username ИЛИ numeric id) -> реальный telegram_id
# ---------------------------------------------------------------------------
async def resolve_target(identifier: str) -> Tuple[Optional[int], str]:
    raw = identifier.strip().lstrip("@")

    # Числовой telegram_id введён напрямую
    if raw.isdigit():
        target_id = int(raw)
        username = db.get_username_for_id(target_id) or ""
        return target_id, username

    username = raw.lower()

    # 1) ищем среди тех, кто уже взаимодействовал с ботом
    target_id = db.get_user_id_by_username(username)
    if target_id:
        return target_id, username

    # 2) пробуем резолвить через Telegram API. Это работает только для
    #    публичных каналов/групп либо пользователей, которые уже писали
    #    этому боту / состоят с ним в общем чате — обычные приватные
    #    аккаунты, никогда не видевшие бота, Telegram резолвить не даст.
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


# ---------------------------------------------------------------------------
# Резолвинг цели через reply (самый надёжный способ — Telegram сам
# присылает объект from_user того, на чьё сообщение отвечают, поэтому
# username вообще не нужен и никакие ограничения API не действуют)
# ---------------------------------------------------------------------------
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
    """
    Единая точка резолвинга цели.
    Приоритет: reply на сообщение пользователя > текстовый identifier.
    Так, даже если у человека закрытый/незнакомый боту username,
    достаточно ответить (reply) на его сообщение — и бот найдёт его
    гарантированно, без обращения к Telegram API поиска по username.
    """
    from_reply = resolve_target_from_reply(message)
    if from_reply:
        return from_reply
    if identifier:
        return await resolve_target(identifier)
    return None, ""

# ---------------------------------------------------------------------------
# Карточка репутации (общая для Message и CallbackQuery)
# ---------------------------------------------------------------------------
async def send_reputation_card(
    target: types.Message | types.CallbackQuery,
    identifier: Optional[str] = None,
    source_message: Optional[types.Message] = None,
) -> None:

    if isinstance(target, CallbackQuery):
        msg = target.message
        is_callback = True
    else:
        msg = target
        is_callback = False

    # source_message — сообщение, из которого нужно смотреть reply
    # (для callback это не имеет смысла, там всегда только identifier)
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

    # Проверяем бан
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

    # Получаем статистику и отзывы
    stats = db.get_user_stats(target_id)
    reviews = db.get_user_reviews(target_id, limit=5)

    # Нет отзывов
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

    # Формируем карточку
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

        lines.append(
            f"\n{medal} {sign_emoji} от {reviewer_tag}{source_tag} "
            f"<i>({date_str})</i>"
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


# ---------------------------------------------------------------------------
# ХЕНДЛЕР: /start
# ---------------------------------------------------------------------------
@dp.message(CommandStart())
async def start_handler(message: types.Message) -> None:
    user = message.from_user
    db.upsert_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    welcome_text = (
        "🍓 <b>Добро пожаловать в Клубничный бот репутации!</b>\n\n"
        "Powered by Nexon Group Solution "
        "Этот бот помогает покупателям и продавцам клубники "
        "безопасно работать друг с другом.\n\n"
        "🌟 <b>Возможности бота:</b>\n"
        "  🍓 <b>Принимать отзывы</b> — с подтверждением скриншотом\n"
        "  🔍 <b>Искать репутацию</b> — быстрая проверка любого продавца\n"
        "  📊 <b>Топ продавцов</b> — рейтинг лучших по отзывам\n"
        "  📡 <b>Мониторинг каналов</b> — автосбор отзывов из каналов\n"
        "  🛡️ <b>Защита от накрутки</b> — антиспам и верификация\n\n"
        "Выбери действие в меню 👇"
    )

    await message.answer(welcome_text, reply_markup=main_menu_kb())


# ---------------------------------------------------------------------------
# ХЕНДЛЕР: /help
# ---------------------------------------------------------------------------
@dp.message(Command("help"))
async def help_handler(message: types.Message) -> None:
    """Справка по боту на русском языке."""
    help_text = (
        "❓ <b>Как пользоваться Клубничным ботом</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 <b>Как оставить отзыв</b>\n"
        "Напиши в группе:\n"
        "<code>+реп @username описание сделки</code>\n"
        "<code>-реп @username описание проблемы</code>\n"
        "Можно указать и числовой Telegram ID вместо @username.\n"
        "👉 <b>Самый надёжный способ</b> — ответить (reply) на сообщение "
        "продавца и написать <code>+реп good</code> без username вообще.\n"
        "и <b>прикрепи скриншот</b> сделки!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>Как проверить продавца</b>\n"
        "• Команда: <code>/check @username</code>\n"
        "• Ответь (reply) на его сообщение командой <code>/check</code>\n"
        "• Или нажми кнопку <b>🔍 Проверить продавца</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Правила</b>\n"
        "📸 Скриншот сделки обязателен\n"
        "📝 Описание минимум 10 символов\n"
        "🚫 Нельзя оставлять отзыв самому себе\n"
        "⏰ Один отзыв об одном человеке в сутки\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎨 <b>Уровни доверия</b>\n"
        "🟢 <b>Проверенный</b> — рейтинг 80–100%\n"
        "🟡 <b>Надёжный</b> — рейтинг 40–79%\n"
        "🟠 <b>Сомнительный</b> — рейтинг −20–39%\n"
        "🔴 <b>Мошенник</b> — рейтинг ниже −20%"
    )

    # Определяем способ отправки: редактируем или отвечаем
    if hasattr(message, "edit_text"):
        try:
            await message.edit_text(help_text, reply_markup=back_kb())
        except TelegramAPIError:
            await message.answer(help_text, reply_markup=back_kb())
    else:
        await message.answer(help_text, reply_markup=back_kb())


# ---------------------------------------------------------------------------
# ХЕНДЛЕР: /check @username или /check <id>, либо /check в reply
# ---------------------------------------------------------------------------
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

    # Если это reply — можно вообще без identifier, бот возьмёт автора
    # сообщения, на которое ответили
    if identifier is None and message.reply_to_message is None:
        await message.answer(
            "🔍 <b>Укажи имя пользователя или ID</b>\n\n"
            "Пример: <code>/check @username</code> или <code>/check 123456789</code>\n"
            "Либо ответь (reply) на сообщение продавца и напиши просто <code>/check</code>.",
            reply_markup=back_kb(),
        )
        return

    await send_reputation_card(message, identifier, source_message=message)


# ---------------------------------------------------------------------------
# ХЕНДЛЕР: +реп / -реп в группах и личных сообщениях
# ---------------------------------------------------------------------------
@dp.message(F.text.regexp(r"^[+\-]\s*реп\s+(@?\w+|\S+)"))
@dp.message(F.caption.regexp(r"^[+\-]\s*реп\s+(@?\w+|\S+)"))
async def reputation_handler(message: types.Message) -> None:
    text = get_target_text(message)
    parsed = parse_rep_message(text)
    if not parsed:
        return

    sign, identifier, description = parsed
    reviewer = message.from_user

    # Регистрируем того, кто оставляет отзыв
    db.upsert_user(
        telegram_id=reviewer.id,
        username=reviewer.username or "",
        first_name=reviewer.first_name or "",
    )

    # --- Проверка: автор не в бане ---
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

    # Резолвим цель: приоритет — reply на сообщение продавца, иначе username/ID
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

    # --- Проверка: нельзя оставить отзыв самому себе ---
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

    # --- Проверка: скриншот обязателен (поддержка альбомов из нескольких фото) ---
    photos = await collect_review_photos(message)
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
            reply_markup=help_kb(),
        )
        return

    # --- Проверка: описание достаточно подробное ---
    if len(description) < 10:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await message.answer(
            "📝 <b>Добавь описание сделки!</b>\n\n"
            "Описание должно содержать <b>минимум 10 символов</b>.\n"
            "Расскажи подробнее о сделке: что купил, когда, впечатления.",
            reply_markup=help_kb(),
        )
        return

    # --- Проверка: не более одного отзыва об одном человеке в сутки ---
    #if db.has_recent_review(reviewer.id, target_id, hours=24):
    #    await message.answer(
    #        f"⏰ <b>Подождите 24 часа</b>\n\n"
    #        f"Вы уже оставляли отзыв о {display_name(target_id, target_username)} сегодня.\n"
    #        f"Повторный отзыв можно будет оставить через 24 часа.",
    #        reply_markup=view_user_kb(target_id, target_username),
    #    )
    #    return

    # --- Сохраняем отзыв ---
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

    await message.answer(confirmation, reply_markup=view_user_kb(target_id, target_username))
    logger.info(
        f"Новый отзыв #{review_id}: {sign} реп target_id={target_id} "
        f"от {reviewer.id} (@{reviewer.username})"
    )


# ---------------------------------------------------------------------------
# ХЕНДЛЕР: +реп / -реп в каналах (мониторинг)
# ---------------------------------------------------------------------------
@dp.channel_post(F.text.regexp(r"^[+\-]\s*реп\s+@?\w+"))
@dp.channel_post(F.caption.regexp(r"^[+\-]\s*реп\s+@?\w+"))
async def channel_reputation_handler(message: types.Message) -> None:
    text = get_target_text(message)
    parsed = parse_rep_message(text)
    if not parsed:
        return

    sign, identifier, description = parsed

    # В каналах требуем описание, иначе игнорируем сразу (без ожидания альбома)
    if len(description) < 10:
        return

    photos = await collect_review_photos(message)
    if not photos:
        return

    target_id, target_username = await resolve_target(identifier)
    if target_id is None:
        # В канале некому ответить об ошибке — просто пропускаем запись
        logger.info(f"Не удалось резолвить цель '{identifier}' в канальном отзыве")
        return

    chat = message.chat

    # Регистрируем канал как мониторируемый
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


# ---------------------------------------------------------------------------
# CALLBACK: кнопка «Проверить продавца» → запрос username через FSM
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# FSM: обработка введённого username после нажатия кнопки
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CALLBACK: view_{target_id} — прямой просмотр репутации
# ---------------------------------------------------------------------------
@dp.callback_query(F.data.startswith("view_"))
async def view_callback(callback: CallbackQuery) -> None:
    identifier = callback.data.split("_", 1)[1]
    await send_reputation_card(callback.message, identifier)
    await callback.answer()


# ---------------------------------------------------------------------------
# CALLBACK: photos_{target_id} — список сделок со скриншотами (меню выбора)
# ---------------------------------------------------------------------------
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
        # Если в БД нет колонки id — используем позицию в этом же списке
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


# ---------------------------------------------------------------------------
# CALLBACK: rphotos_{target_id}_{review_id} — скриншоты конкретной сделки
# ---------------------------------------------------------------------------
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
        f"👤 От: {reviewer_tag}",
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


# ---------------------------------------------------------------------------
# CALLBACK: топ продавцов
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CALLBACK: глобальная статистика
# ---------------------------------------------------------------------------
@dp.callback_query(F.data == "stats")
async def stats_callback(callback: CallbackQuery) -> None:
    s = db.get_global_stats()

    stats_text = (
        "📈 <b>Статистика Клубничного бота</b>\n\n"
        f"👥 <b>Пользователей:</b> {s['users']}\n"
        f"📝 <b>Всего отзывов:</b> {s['total_reviews']}\n"
        f"   ✅ Положительных: {s['positive']}\n"
        f"   ❌ Отрицательных: {s['negative']}\n"
        f"💬 <b>Активных чатов:</b> {s['chats']}\n"
        f"📡 <b>Мониторируемых каналов:</b> {s['channels']}\n\n"
        f"🍓 <i>Помогаем делать клубничный бизнес честным!</i>"
    )

    await callback.message.edit_text(stats_text, reply_markup=back_kb())
    await callback.answer()


# ---------------------------------------------------------------------------
# CALLBACK: помощь
# ---------------------------------------------------------------------------
@dp.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    await help_handler(callback.message)
    await callback.answer()


# ---------------------------------------------------------------------------
# CALLBACK: возврат в главное меню
# ---------------------------------------------------------------------------
@dp.callback_query(F.data == "menu")
async def menu_callback(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.clear()

    welcome_text = (
        "🍓 <b>Клубничный бот репутации</b>\n\n"
        "Выбери действие в меню 👇"
    )

    try:
        await callback.message.edit_text(welcome_text, reply_markup=main_menu_kb())
    except TelegramAPIError as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Ошибка при возврате в меню: {e}")

    await callback.answer()


# ---------------------------------------------------------------------------
# ADMIN: /admin — панель администратора
# ---------------------------------------------------------------------------
@dp.message(Command("admin"))
async def admin_handler(message: types.Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return

    admin_text = (
        "🛡️ <b>Панель администратора</b>\n\n"
        "📋 <b>Доступные команды:</b>\n\n"
        "🗑️ <code>/delreview [ID]</code>\n"
        "   — Удалить отзыв по ID\n\n"
        "🚫 <code>/banuser @username причина</code>\n"
        "   — Заблокировать пользователя\n\n"
        "✅ <code>/unban @username</code>\n"
        "   — Разблокировать пользователя\n\n"
        "📡 <code>/channels</code>\n"
        "   — Список мониторируемых каналов\n\n"
        f"👮 <b>Ваш ID:</b> <code>{message.from_user.id}</code>"
    )

    await message.answer(admin_text)


# ---------------------------------------------------------------------------
# ADMIN: /delreview — удаление отзыва
# ---------------------------------------------------------------------------
@dp.message(Command("delreview"))
async def delreview_handler(message: types.Message, command: CommandObject) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return

    if not command.args:
        await message.answer(
            "❌ Укажи ID отзыва.\nПример: <code>/delreview 42</code>"
        )
        return

    try:
        review_id = int(command.args.strip().split()[0])
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    success = db.delete_review(review_id)
    if success:
        await message.answer(f"✅ Отзыв <code>#{review_id}</code> успешно удалён.")
        logger.info(f"Админ {message.from_user.id} удалил отзыв #{review_id}")
    else:
        await message.answer(f"❌ Отзыв <code>#{review_id}</code> не найден.")


# ---------------------------------------------------------------------------
# ADMIN: /banuser — бан пользователя
# ---------------------------------------------------------------------------
@dp.message(Command("banuser"))
async def banuser_handler(message: types.Message, command: CommandObject) -> None:
    if message.from_user.id not in ADMIN_IDS:
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


# ---------------------------------------------------------------------------
# ADMIN: /unban — разбан пользователя
# ---------------------------------------------------------------------------
@dp.message(Command("unban"))
async def unban_handler(message: types.Message, command: CommandObject) -> None:
    if message.from_user.id not in ADMIN_IDS:
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


# ---------------------------------------------------------------------------
# ADMIN: /channels — список мониторируемых каналов
# ---------------------------------------------------------------------------
@dp.message(Command("channels"))
async def channels_handler(message: types.Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return

    channels = db.list_monitored_channels()

    if not channels:
        await message.answer(
            "📡 <b>Мониторируемые каналы</b>\n\n"
            "Пока ни один канал не добавлен.\n"
            "Добавь бота в канал как администратора."
        )
        return

    lines = ["📡 <b>Мониторируемые каналы:</b>\n"]
    for ch in channels:
        title = escape(ch["chat_title"] or "Без названия")
        lines.append(f"• <b>{title}</b> (<code>{ch['chat_id']}</code>)")

    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# Запуск бота
# ---------------------------------------------------------------------------
async def on_startup() -> None:
    """Инициализация при запуске."""
    db.init()
    me = await bot.get_me()
    logger.info(
        f"🍓 Бот @{me.username} запущен! "
        f"Администраторы: {ADMIN_IDS if ADMIN_IDS else 'не назначены'}"
    )


async def main() -> None:
    db.init()
    dp.startup.register(on_startup)
    logger.info("🍓 Strawberry Reputation Bot стартует...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
