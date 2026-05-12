from __future__ import annotations

import logging
from typing import Iterable

from ..config import Settings
from .base import Product, ScraperError, ShopInfo, ShopScraper
from .shopee import ShopeeScraper

log = logging.getLogger(__name__)


def get_scraper(platform: str, settings: Settings | None = None) -> ShopScraper:
    """Factory: tạo scraper cho platform với cấu hình từ Settings (nếu có).

    Khi `SHOPEE_USE_BROWSER=1`, dùng ShopeePlaywrightScraper (browser thật,
    bypass anti-bot tốt hơn HTTP client).
    """
    key = platform.lower()
    if key == "shopee":
        if settings is None:
            return ShopeeScraper()

        if settings.shopee_use_browser:
            try:
                from .shopee_playwright import ShopeePlaywrightScraper
                log.info("Sử dụng ShopeePlaywrightScraper (browser mode)")
                return ShopeePlaywrightScraper(
                    proxy=settings.http_proxy,
                    cookie_string=settings.shopee_cookie,
                    cookies_json=settings.shopee_cookies_json,
                )
            except ImportError as exc:
                log.error(
                    "SHOPEE_USE_BROWSER=1 nhưng playwright chưa cài: %s. "
                    "Fallback sang HTTP scraper.",
                    exc,
                )

        return ShopeeScraper(
            proxy=settings.http_proxy,
            cookie_string=settings.shopee_cookie,
            auto_cookie=settings.shopee_auto_cookie,
        )
    raise ScraperError(f"Platform không hỗ trợ: {platform}")


def supported_platforms() -> Iterable[str]:
    return ("shopee",)


__all__ = [
    "Product",
    "ScraperError",
    "ShopInfo",
    "ShopScraper",
    "ShopeeScraper",
    "get_scraper",
    "supported_platforms",
]
