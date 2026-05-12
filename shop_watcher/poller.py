from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from telegram import Bot
from telegram.ext import Application

from .config import Settings
from .db import Database
from .notifier import send_batch
from .scrapers import ScraperError, get_scraper
from .scrapers.base import Product

log = logging.getLogger(__name__)


@dataclass
class PollStats:
    shops_checked: int = 0
    new_items: int = 0
    errors: int = 0


class Poller:
    """Iterate qua tất cả (platform, shop_id) unique trong DB, diff & notify."""

    def __init__(self, settings: Settings, db: Database, app: Application):
        self.settings = settings
        self.db = db
        self.app = app
        self._lock = asyncio.Lock()
        self._scraper_cache: dict[str, object] = {}

    async def shutdown(self) -> None:
        for sc in self._scraper_cache.values():
            close = getattr(sc, "close", None)
            if close:
                try:
                    await close()
                except Exception as exc:  # noqa: BLE001
                    log.debug("Scraper close error: %s", exc)
        self._scraper_cache.clear()

    def _get_scraper(self, platform: str):
        sc = self._scraper_cache.get(platform)
        if sc is None:
            sc = get_scraper(platform, self.settings)
            self._scraper_cache[platform] = sc
        return sc

    async def run_once(self) -> PollStats:
        if self._lock.locked():
            log.info("Lượt poll trước chưa xong, bỏ qua tick này")
            return PollStats()

        async with self._lock:
            stats = PollStats()
            keys = self.db.distinct_shop_keys()
            log.info("Poll bắt đầu, %d shop cần check", len(keys))

            for platform, shop_id in keys:
                stats.shops_checked += 1
                try:
                    new_count = await self._check_shop(platform, shop_id)
                    stats.new_items += new_count
                    self.db.update_check_status(platform, shop_id, "ok")
                except ScraperError as exc:
                    log.warning("[%s/%s] scraper error: %s", platform, shop_id, exc)
                    self.db.update_check_status(platform, shop_id, f"err: {exc}")
                    stats.errors += 1
                except Exception as exc:  # noqa: BLE001
                    log.exception("[%s/%s] unexpected error", platform, shop_id)
                    self.db.update_check_status(
                        platform, shop_id, f"err: {type(exc).__name__}"
                    )
                    stats.errors += 1
                await asyncio.sleep(1.5)

            log.info(
                "Poll xong: %d shop, %d sản phẩm mới, %d lỗi",
                stats.shops_checked,
                stats.new_items,
                stats.errors,
            )
            return stats

    async def _check_shop(self, platform: str, shop_id: str) -> int:
        scraper = self._get_scraper(platform)
        products: list[Product] = await scraper.list_latest_items(
            shop_id, limit=self.settings.items_per_check
        )
        if not products:
            log.debug("[%s/%s] không có sản phẩm nào trả về", platform, shop_id)
            return 0

        item_ids = [p.item_id for p in products]
        baseline_existed = self.db.has_baseline(platform, shop_id)

        if not baseline_existed:
            self.db.mark_items_seen(platform, shop_id, item_ids)
            log.info(
                "[%s/%s] baseline %d items, skip notify lần đầu",
                platform,
                shop_id,
                len(item_ids),
            )
            return 0

        new_ids = self.db.filter_new_items(platform, shop_id, item_ids)
        if not new_ids:
            return 0

        new_set = set(new_ids)
        new_products = [p for p in products if p.item_id in new_set]
        new_products.sort(key=lambda p: p.ctime or 0, reverse=True)

        await self._notify_subscribers(platform, shop_id, new_products)
        self.db.mark_items_seen(platform, shop_id, new_ids)
        log.info("[%s/%s] %d sản phẩm mới", platform, shop_id, len(new_ids))
        return len(new_ids)

    async def _notify_subscribers(
        self, platform: str, shop_id: str, products: list[Product]
    ) -> None:
        if not products:
            return
        bot: Bot = self.app.bot
        subscribers = self.db.chats_watching(platform, shop_id)
        for sub in subscribers:
            try:
                await send_batch(bot, sub.chat_id, products, sub.shop_name)
            except Exception:  # noqa: BLE001
                log.exception("Notify chat %s fail", sub.chat_id)
