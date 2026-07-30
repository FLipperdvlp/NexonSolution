import sqlite3
from datetime import datetime, timedelta
from typing import Optional


class Database:
    def __init__(self, path: str = "reputation.db") -> None:
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_balance(self, telegram_id: int) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT balance FROM user_balances WHERE telegram_id=?",
                (telegram_id,)
            ).fetchone()
            return float(row["balance"]) if row else 0.0

    def get_user_by_username(self, username: str):
        username = username.lstrip("@").lower()
    
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT telegram_id AS user_id, username
                FROM users
                WHERE LOWER(username)=LOWER(?)
                """,
                (username,)
            ).fetchone()
    
        return dict(row) if row else None

    def add_balance(self, telegram_id: int, amount: float):
        with self._conn() as conn:

            conn.execute("""
                INSERT INTO user_balances (telegram_id, balance)
                VALUES (?, ?)
                ON CONFLICT(telegram_id)
                DO UPDATE SET balance = balance + excluded.balance
            """, (telegram_id, amount))

            conn.execute("""
                INSERT INTO balance_history
                (telegram_id, amount, operation)
                VALUES (?, ?, 'deposit')
            """, (telegram_id, amount))

    def remove_balance(self, telegram_id: int, amount: float) -> bool:

        balance = self.get_balance(telegram_id)

        if balance < amount:
            return False

        with self._conn() as conn:

            conn.execute("""
                UPDATE user_balances
                SET balance = balance - ?
                WHERE telegram_id=?
            """, (amount, telegram_id))

            conn.execute("""
                INSERT INTO balance_history
                (telegram_id, amount, operation)
                VALUES (?, ?, 'withdraw')
            """, (telegram_id, amount))

        return True

    def save_invoice(
        self,
        invoice_id: str,
        telegram_id: int,
        amount: float,
        asset: str
    ):

        with self._conn() as conn:

            conn.execute("""
                INSERT INTO invoices
                (invoice_id, telegram_id, amount, asset)
                VALUES (?, ?, ?, ?)
            """, (
                invoice_id,
                telegram_id,
                amount,
                asset
            ))

    def mark_invoice_paid(self, invoice_id: str):
    
        with self._conn() as conn:
        
            conn.execute("""
                UPDATE invoices
                SET
                    status='paid',
                    paid_at=CURRENT_TIMESTAMP
                WHERE invoice_id=?
            """, (invoice_id,))

    def get_invoice(self, invoice_id: str):

        with self._conn() as conn:

            row = conn.execute("""
                SELECT *
                FROM invoices
                WHERE invoice_id=?
            """, (invoice_id,)).fetchone()

            return dict(row) if row else None

    def is_invoice_paid(self, invoice_id: str) -> bool:
    
        with self._conn() as conn:
        
            row = conn.execute(
                """
                SELECT status 
                FROM invoices
                WHERE invoice_id=?
                """,
                (invoice_id,)
            ).fetchone()
    
            return row and row["status"] == "paid"










    def init(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username    TEXT,
                    first_name  TEXT,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_users_username
                    ON users(username);

                CREATE TABLE IF NOT EXISTS reviews (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id         INTEGER NOT NULL,
                    target_username   TEXT,
                    reviewer_id       INTEGER NOT NULL,
                    reviewer_username TEXT,
                    reviewer_name     TEXT,
                    sign              TEXT NOT NULL,
                    description       TEXT,
                    photo_file_id     TEXT,
                    chat_id           INTEGER,
                    chat_title        TEXT,
                    message_id        INTEGER,
                    source            TEXT,
                    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_reviews_target
                    ON reviews(target_id);
                CREATE INDEX IF NOT EXISTS idx_reviews_reviewer_target
                    ON reviews(reviewer_id, target_id);

                CREATE TABLE IF NOT EXISTS banned_users (
                    target_id  INTEGER PRIMARY KEY,
                    reason     TEXT,
                    banned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS channels (
                    chat_id    INTEGER PRIMARY KEY,
                    chat_title TEXT
                );

                CREATE TABLE IF NOT EXISTS user_balances (
                    telegram_id INTEGER PRIMARY KEY,
                    balance REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    asset TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    paid_at TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    operation TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );
                """
            )



    
    def get_reviews_by_username(self, username: str):
        username = username.lstrip("@").lower()
    
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT *
                FROM reviews
                WHERE target_username = ?
                ORDER BY created_at DESC
            """, (username,)).fetchall()
    
        return [dict(r) for r in rows]
    
    
    def get_review_by_number(self, username: str, number: int):
        reviews = self.get_reviews_by_username(username)
    
        if number < 1 or number > len(reviews):
            return None
    
        return reviews[number - 1]

    # ------------------------------------------------------------------
    # Пользователи / резолвинг username -> id
    # ------------------------------------------------------------------
    def upsert_user(
        self, telegram_id: int, username: str = "", first_name: str = ""
    ) -> None:
        username = (username or "").lstrip("@").lower()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, username, first_name),
            )

    def get_user_id_by_username(self, username: str) -> Optional[int]:
        username = username.lstrip("@").lower()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT telegram_id FROM users WHERE username = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (username,),
            ).fetchone()
            return row["telegram_id"] if row else None

    def get_username_for_id(self, target_id: int) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE telegram_id = ?",
                (target_id,),
            ).fetchone()
            return row["username"] if row and row["username"] else None

    # ------------------------------------------------------------------
    # Баны (по target_id)
    # ------------------------------------------------------------------
    def is_banned(self, target_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM banned_users WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            return row is not None

    def ban_user(self, target_id: int, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO banned_users (target_id, reason, banned_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(target_id) DO UPDATE SET
                    reason = excluded.reason,
                    banned_at = CURRENT_TIMESTAMP
                """,
                (target_id, reason),
            )

    def unban_user(self, target_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM banned_users WHERE target_id = ?", (target_id,)
            )

    # ------------------------------------------------------------------
    # Отзывы
    # ------------------------------------------------------------------
    def add_review(
        self,
        target_id: int,
        reviewer_id: int,
        sign: str,
        description: str,
        photo_file_id: str = "",
        target_username: str = "",
        reviewer_username: str = "",
        reviewer_name: str = "",
        chat_id: int = 0,
        chat_title: str = "",
        message_id: int = 0,
        source: str = "chat",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO reviews (
                    target_id, target_username, reviewer_id,
                    reviewer_username, reviewer_name, sign, description,
                    photo_file_id, chat_id, chat_title, message_id, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    (target_username or "").lstrip("@").lower(),
                    reviewer_id,
                    (reviewer_username or "").lstrip("@").lower(),
                    reviewer_name,
                    sign,
                    description,
                    photo_file_id,
                    chat_id,
                    chat_title,
                    message_id,
                    source,
                ),
            )
            return cur.lastrowid

    def delete_review(self, review_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            return cur.rowcount > 0

    def has_recent_review(
        self, reviewer_id: int, target_id: int, hours: int = 24
    ) -> bool:
        threshold = datetime.utcnow() - timedelta(hours=hours)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM reviews
                WHERE reviewer_id = ? AND target_id = ? AND created_at >= ?
                LIMIT 1
                """,
                (reviewer_id, target_id, threshold),
            ).fetchone()
            return row is not None

    def get_user_stats(self, target_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN sign = '+' THEN 1 ELSE 0 END) AS positive,
                    SUM(CASE WHEN sign = '-' THEN 1 ELSE 0 END) AS negative
                FROM reviews WHERE target_id = ?
                """,
                (target_id,),
            ).fetchone()

            if not row or not row["total"]:
                return None

            total = row["total"]
            positive = row["positive"] or 0
            negative = row["negative"] or 0
            score = round(((positive - negative) / total) * 100) if total else 0

            return {
                "total": total,
                "positive": positive,
                "negative": negative,
                "score": score,
            }

    def get_user_reviews(self, target_id: int, limit: int = 5) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reviews
                WHERE target_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (target_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_users(self, limit: int = 10, min_reviews: int = 2) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.target_id AS target_id,
                    COALESCE(
                        (SELECT username FROM users
                        WHERE telegram_id = r.target_id),
                        r.target_username
                    ) AS username,
                    COUNT(*) AS total,
                    SUM(CASE WHEN sign = '+' THEN 1 ELSE 0 END) AS positive,
                    SUM(CASE WHEN sign = '-' THEN 1 ELSE 0 END) AS negative
                FROM reviews r
                GROUP BY r.target_id
                HAVING total >= ?
                ORDER BY (positive - negative) DESC, total DESC
                LIMIT ?
                """,
                (min_reviews, limit),
            ).fetchall()

            result = []
            for row in rows:
                total = row["total"]
                positive = row["positive"] or 0
                negative = row["negative"] or 0
                score = round(((positive - negative) / total) * 100) if total else 0
                result.append(
                    {
                        "target_id": row["target_id"],
                        "username": row["username"] or str(row["target_id"]),
                        "total": total,
                        "positive": positive,
                        "negative": negative,
                        "score": score,
                    }
                )
            return result

    def get_global_stats(self) -> dict:
        with self._conn() as conn:
            users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            total_reviews = conn.execute(
                "SELECT COUNT(*) c FROM reviews"
            ).fetchone()["c"]
            positive = conn.execute(
                "SELECT COUNT(*) c FROM reviews WHERE sign = '+'"
            ).fetchone()["c"]
            negative = conn.execute(
                "SELECT COUNT(*) c FROM reviews WHERE sign = '-'"
            ).fetchone()["c"]
            chats = conn.execute(
                "SELECT COUNT(DISTINCT chat_id) c FROM reviews WHERE source = 'chat'"
            ).fetchone()["c"]
            channels = conn.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]

            return {
                "users": users,
                "total_reviews": total_reviews,
                "positive": positive,
                "negative": negative,
                "chats": chats,
                "channels": channels,
            }

    # ------------------------------------------------------------------
    # Каналы
    # ------------------------------------------------------------------
    def add_monitored_channel(self, chat_id: int, chat_title: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO channels (chat_id, chat_title)
                VALUES (?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET chat_title = excluded.chat_title
                """,
                (chat_id, chat_title),
            )

    def list_monitored_channels(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM channels").fetchall()
            return [dict(r) for r in rows]