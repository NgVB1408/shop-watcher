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


async def _send_demo_product(app: Application) -> None:
    """Sau seed: pick 1 shop random, scrape 1 sản phẩm random, gửi cho admin.

    Mục đích: chứng minh end-to-end pipeline (scraper + Telegram noti) hoạt động.
    Chỉ chạy 1 lần khi startup nếu env `SEND_DEMO_ON_STARTUP=1`.
    """
    import os, random
    if not os.getenv("SEND_DEMO_ON_STARTUP", "").strip().lower() in {"1", "true", "yes"}:
        return

    settings: Settings = app.bot_data["settings"]
    db = app.bot_data["db"]

    if not settings.allowed_chat_ids:
        return

    admin = next(iter(settings.allowed_chat_ids))
    all_shops = db.list_shops(admin)
    if not all_shops:
        log.warning("DEMO: chưa có shop nào trong DB")
        return

    shop = random.choice(all_shops)
    log.info("DEMO: quét shop random %s (%s)", shop.shop_id, shop.shop_name)

    from .scrapers import get_scraper
    from .notifier import send_product_notification

    scraper = get_scraper("shopee", settings)
    try:
        items = await scraper.list_latest_items(shop.shop_id, limit=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("DEMO scrape fail: %s", exc)
        items = []
    finally:
        await scraper.close()

    if not items:
        try:
            await app.bot.send_message(
                chat_id=admin,
                text=f"⚠️ Demo: scrape shop <b>{shop.shop_name}</b> không lấy được item nào.",
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
        return

    # Pick 1-2 sản phẩm ngẫu nhiên
    sample = random.sample(items, min(2, len(items)))

    try:
        await app.bot.send_message(
            chat_id=admin,
            text=(
                f"🔬 <b>Demo scrape</b> · shop random: <b>{shop.shop_name}</b>\n"
                f"Scraper bắt {len(items)} items. Gửi {len(sample)} mẫu:"
            ),
            parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("DEMO header send fail: %s", exc)
        return

    for p in sample:
        try:
            ok = await send_product_notification(app.bot, admin, p, shop.shop_name)
            log.info("DEMO sent item %s: %s", p.item_id, ok)
        except Exception as exc:  # noqa: BLE001
            log.warning("DEMO send item %s fail: %s", p.item_id, exc)


async def _seed_shops_from_env(app: Application) -> None:
    """Auto-add shop_ids từ env SEED_SHOPS=104274078,123456,... cho mỗi allowed chat.

    Chỉ chạy 1 lần khi shop chưa có trong DB của chat đó.
    """
    import os
    raw = os.getenv("SEED_SHOPS", "").strip()
    if not raw:
        return
    settings: Settings = app.bot_data["settings"]
    db = app.bot_data["db"]
    if not settings.allowed_chat_ids:
        log.warning("SEED_SHOPS yêu cầu TELEGRAM_ALLOWED_CHAT_IDS để biết add cho ai")
        return

    from .scrapers import get_scraper
    scraper = get_scraper("shopee", settings)
    try:
        for shop_part in raw.split(","):
            shop_part = shop_part.strip()
            if not shop_part:
                continue
            for chat_id in settings.allowed_chat_ids:
                try:
                    info = await scraper.resolve_shop(shop_part)
                    _, created = db.add_shop(
                        chat_id=chat_id,
                        platform=info.platform,
                        shop_handle=info.handle,
                        shop_id=info.shop_id,
                        shop_name=info.name,
                    )
                    if created:
                        log.info(
                            "SEED: added shop %s (%s) cho chat %s",
                            info.shop_id, info.name, chat_id,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("SEED fail cho %s: %s", shop_part, exc)
    finally:
        await scraper.close()


async def _on_startup(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    poller: Poller = app.bot_data["poller"]

    _write_heartbeat(settings)

    # Auto-seed shops từ env (skip nếu shop đã tồn tại)
    await _seed_shops_from_env(app)

    # Demo: scrape + gửi 1 sản phẩm random cho admin (nếu SEND_DEMO_ON_STARTUP=1)
    try:
        await _send_demo_product(app)
    except Exception:  # noqa: BLE001
        log.exception("Demo send fail")

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
