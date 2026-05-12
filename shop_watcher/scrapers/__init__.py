from __future__ import annotations

from typing import Iterable

from ..config import Settings
from .base import Product, ScraperError, ShopInfo, ShopScraper
from .shopee import ShopeeScraper


def get_scraper(platform: str, settings: Settings | None = None) -> ShopScraper:
    """Factory: tạo scraper cho platform với cấu hình từ Settings (nếu có)."""
    key = platform.lower()
    if key == "shopee":
        if settings is None:
            return ShopeeScraper()
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
