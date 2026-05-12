from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from telegram.ext import Application, ApplicationBuilder

from .bot import register_handlers
from .config import Settings, configure_logging
from .db import Database
from .poller import Poller

log = logging.getLogger(__name__)


def _heartbeat_path(settings: Settings) -> Path:
    return settings.db_path.parent / ".heartbeat"


def _write_heartbeat(settings: Settings) -> None:
    try:
        p = _heartbeat_path(settings)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except Exception as exc:  # noqa: BLE001
        log.debug("Heartbeat write fail: %s", exc)


async def _on_startup(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    poller: Poller = app.bot_data["poller"]

    _write_heartbeat(settings)

    # Lên lịch poll định kỳ. first=10s để có baseline ngay sau khi start.
    app.job_queue.run_repeating(
        callback=_poll_job,
        interval=settings.poll_interval_seconds,
        first=10,
        name="shop_poll",
    )
    # Heartbeat tick mỗi 30s — để healthcheck phân biệt bot sống vs treo
    app.job_queue.run_repeating(
        callback=_heartbeat_job,
        interval=30,
        first=5,
        name="heartbeat",
    )
    log.info(
        "Scheduler đã chạy, interval=%ss",
        settings.poll_interval_seconds,
    )
    # Báo cho admin (nếu allowlist có)
    for chat_id in settings.allowed_chat_ids:
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🤖 Shop Watcher đã online.\n"
                    f"Poll mỗi {settings.poll_interval_seconds}s. "
                    "Gõ /help để xem commands."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Notify startup tới %s fail: %s", chat_id, exc)


async def _on_shutdown(app: Application) -> None:
    log.info("Shutting down…")
    poller: Poller | None = app.bot_data.get("poller")
    db: Database | None = app.bot_data.get("db")
    if poller:
        await poller.shutdown()
    if db:
        db.close()


async def _poll_job(ctx) -> None:
    poller: Poller = ctx.application.bot_data["poller"]
    settings: Settings = ctx.application.bot_data["settings"]
    try:
        await poller.run_once()
    except Exception:  # noqa: BLE001
        log.exception("Poll job crashed")
    _write_heartbeat(settings)


async def _heartbeat_job(ctx) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    _write_heartbeat(settings)


def build_app(settings: Settings) -> Application:
    app: Application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )

    db = Database(settings.db_path)
    poller = Poller(settings=settings, db=db, app=app)

    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["poller"] = poller

    register_handlers(app)
    return app


def main() -> None:
    settings = Settings.load()
    configure_logging(settings.log_level)
    log.info(
        "Shop Watcher khởi động · poll=%ss · items=%d · db=%s",
        settings.poll_interval_seconds,
        settings.items_per_check,
        settings.db_path,
    )
    app = build_app(settings)
    # run_polling tự handle SIGINT/SIGTERM trên Linux; Windows cũng OK với Ctrl-C
    app.run_polling(
        stop_signals=(signal.SIGINT, signal.SIGTERM)
        if hasattr(signal, "SIGTERM")
        else (signal.SIGINT,),
        allowed_updates=None,
    )


if __name__ == "__main__":
    main()
