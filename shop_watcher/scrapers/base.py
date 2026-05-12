from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ScraperError(Exception):
    """Lỗi scraper: shop không tồn tại, bị block, parse fail, ..."""


@dataclass(frozen=True)
class ShopInfo:
    platform: str
    shop_id: str
    handle: str            # username hoặc identifier hiển thị
    name: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class Product:
    platform: str
    shop_id: str
    item_id: str
    name: str
    price: int | None       # giá tối thiểu, đơn vị VND
    price_max: int | None
    currency: str = "VND"
    url: str | None = None
    image_url: str | None = None
    stock: int | None = None
    sold: int | None = None
    ctime: int | None = None  # unix ts khi item được list lên

    @property
    def price_text(self) -> str:
        if self.price is None:
            return "N/A"
        if self.price_max and self.price_max != self.price:
            return f"{_fmt_money(self.price)}đ - {_fmt_money(self.price_max)}đ"
        return f"{_fmt_money(self.price)}đ"


def _fmt_money(n: int) -> str:
    return f"{n:,}".replace(",", ".")


class ShopScraper(ABC):
    """Interface chung cho mọi platform."""

    platform: str = "abstract"

    @abstractmethod
    async def resolve_shop(self, handle: str) -> ShopInfo:
        """Từ username/URL → ShopInfo có shop_id chuẩn."""

    @abstractmethod
    async def list_latest_items(
        self, shop_id: str, limit: int = 30
    ) -> list[Product]:
        """Lấy sản phẩm mới nhất, sắp xếp giảm dần theo thời gian list."""

    async def close(self) -> None:
        """Đóng tài nguyên (HTTP client, ...)."""
