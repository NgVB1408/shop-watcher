from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _parse_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            logging.getLogger(__name__).warning("Bỏ qua chat_id không hợp lệ: %s", chunk)
    return out


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_chat_ids: set[int] = field(default_factory=set)
    poll_interval_seconds: int = 300
    items_per_check: int = 30
    db_path: Path = PROJECT_ROOT / "data" / "shop_watcher.db"
    log_level: str = "INFO"
    http_proxy: str | None = None
    shopee_cookie: str | None = None
    shopee_cookies_json: str | None = None
    twocaptcha_api_key: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN không được set. Copy .env.example -> .env và điền token."
            )

        poll = max(15, int(os.getenv("POLL_INTERVAL_SECONDS", "60")))
        items = max(5, min(100, int(os.getenv("ITEMS_PER_CHECK", "30"))))

        db_path_str = os.getenv("DB_PATH", "data/shop_watcher.db").strip()
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path

        return cls(
            telegram_bot_token=token,
            allowed_chat_ids=_parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")),
            poll_interval_seconds=poll,
            items_per_check=items,
            db_path=db_path,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
            http_proxy=os.getenv("HTTP_PROXY", "").strip() or None,
            shopee_cookie=os.getenv("SHOPEE_COOKIE", "").strip() or None,
            shopee_cookies_json=os.getenv("SHOPEE_COOKIES_JSON", "").strip() or None,
            twocaptcha_api_key=os.getenv("TWOCAPTCHA_API_KEY", "").strip() or None,
        )


def configure_logging(level: str) -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "shop_watcher.log", encoding="utf-8"),
        ],
    )
    # Giảm noise từ httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
