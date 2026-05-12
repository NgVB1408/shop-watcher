from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    platform      TEXT    NOT NULL,
    shop_handle   TEXT    NOT NULL,
    shop_id       TEXT    NOT NULL,
    shop_name     TEXT,
    added_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_checked  TEXT,
    last_status   TEXT,
    UNIQUE(chat_id, platform, shop_id)
);

CREATE INDEX IF NOT EXISTS idx_shops_chat ON shops(chat_id);

CREATE TABLE IF NOT EXISTS seen_items (
    platform     TEXT    NOT NULL,
    shop_id      TEXT    NOT NULL,
    item_id      TEXT    NOT NULL,
    first_seen   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, shop_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_seen_shop ON seen_items(platform, shop_id);
"""


@dataclass
class ShopRow:
    id: int
    chat_id: int
    platform: str
    shop_handle: str
    shop_id: str
    shop_name: str | None
    last_checked: str | None
    last_status: str | None


class Database:
    """Thin SQLite wrapper, thread-safe (lock + check_same_thread=False)."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
        log.info("DB ready: %s", self.path)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ---------- Shops ----------

    def add_shop(
        self,
        chat_id: int,
        platform: str,
        shop_handle: str,
        shop_id: str,
        shop_name: str | None,
    ) -> tuple[ShopRow, bool]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM shops WHERE chat_id=? AND platform=? AND shop_id=?",
                (chat_id, platform, shop_id),
            )
            row = cur.fetchone()
            if row:
                return _row_to_shop(row), False

            cur.execute(
                """INSERT INTO shops (chat_id, platform, shop_handle, shop_id, shop_name)
                   VALUES (?, ?, ?, ?, ?)""",
                (chat_id, platform, shop_handle, shop_id, shop_name),
            )
            new_id = cur.lastrowid
            cur.execute("SELECT * FROM shops WHERE id=?", (new_id,))
            return _row_to_shop(cur.fetchone()), True

    def remove_shop(self, chat_id: int, handle_or_id: str) -> ShopRow | None:
        with self._cursor() as cur:
            cur.execute(
                """SELECT * FROM shops
                   WHERE chat_id=? AND (shop_handle=? OR shop_id=?)
                   ORDER BY added_at LIMIT 1""",
                (chat_id, handle_or_id, handle_or_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            shop = _row_to_shop(row)
            cur.execute("DELETE FROM shops WHERE id=?", (shop.id,))
            cur.execute(
                "SELECT COUNT(*) AS c FROM shops WHERE platform=? AND shop_id=?",
                (shop.platform, shop.shop_id),
            )
            if cur.fetchone()["c"] == 0:
                cur.execute(
                    "DELETE FROM seen_items WHERE platform=? AND shop_id=?",
                    (shop.platform, shop.shop_id),
                )
            return shop

    def list_shops(self, chat_id: int) -> list[ShopRow]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM shops WHERE chat_id=? ORDER BY added_at",
                (chat_id,),
            )
            return [_row_to_shop(r) for r in cur.fetchall()]

    def distinct_shop_keys(self) -> list[tuple[str, str]]:
        with self._cursor() as cur:
            cur.execute("SELECT DISTINCT platform, shop_id FROM shops")
            return [(r["platform"], r["shop_id"]) for r in cur.fetchall()]

    def chats_watching(self, platform: str, shop_id: str) -> list[ShopRow]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM shops WHERE platform=? AND shop_id=?",
                (platform, shop_id),
            )
            return [_row_to_shop(r) for r in cur.fetchall()]

    def update_check_status(self, platform: str, shop_id: str, status: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """UPDATE shops
                   SET last_checked = datetime('now'), last_status = ?
                   WHERE platform=? AND shop_id=?""",
                (status, platform, shop_id),
            )

    # ---------- Seen items ----------

    def filter_new_items(
        self, platform: str, shop_id: str, item_ids: list[str]
    ) -> list[str]:
        if not item_ids:
            return []
        with self._cursor() as cur:
            placeholders = ",".join("?" * len(item_ids))
            cur.execute(
                f"""SELECT item_id FROM seen_items
                    WHERE platform=? AND shop_id=? AND item_id IN ({placeholders})""",
                (platform, shop_id, *item_ids),
            )
            seen = {r["item_id"] for r in cur.fetchall()}
            return [i for i in item_ids if i not in seen]

    def mark_items_seen(
        self, platform: str, shop_id: str, item_ids: list[str]
    ) -> None:
        if not item_ids:
            return
        with self._cursor() as cur:
            cur.executemany(
                """INSERT OR IGNORE INTO seen_items (platform, shop_id, item_id)
                   VALUES (?, ?, ?)""",
                [(platform, shop_id, i) for i in item_ids],
            )

    def has_baseline(self, platform: str, shop_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM seen_items WHERE platform=? AND shop_id=? LIMIT 1",
                (platform, shop_id),
            )
            return cur.fetchone() is not None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_shop(row: sqlite3.Row) -> ShopRow:
    return ShopRow(
        id=row["id"],
        chat_id=row["chat_id"],
        platform=row["platform"],
        shop_handle=row["shop_handle"],
        shop_id=row["shop_id"],
        shop_name=row["shop_name"],
        last_checked=row["last_checked"],
        last_status=row["last_status"],
    )
