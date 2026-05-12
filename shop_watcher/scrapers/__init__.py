from __future__ import annotations

from typing import Iterable

from ..config import Settings
from .base import Product, ScraperError, ShopInfo, ShopScraper
from .shopee import ShopeeScraper


def get_scraper(platform: str, settings: Settings | None = None) -> ShopScraper:
    key = platform.lower()
    if key == "shopee":
        if settings is None:
            return ShopeeScraper()
        return ShopeeScraper(
            proxy=settings.http_proxy,
            cookies_json=settings.shopee_cookies_json,
            cookie_string=settings.shopee_cookie,
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
