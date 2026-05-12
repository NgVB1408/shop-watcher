"""Shopee scraper.

Strategy:
1. Dùng curl_cffi (TLS fingerprint giả Chrome) - bypass Shopee anti-bot tốt nhất
   trong các HTTP client.
2. Hỗ trợ cookie injection qua Settings.shopee_cookie để pass qua bot check khi
   IP residential bị Shopee flag (error 90309999).
3. Optional: tự refresh cookie bằng Playwright headless khi enable
   SHOPEE_AUTO_COOKIE=1.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

from curl_cffi import requests as curl_requests
from curl_cffi.requests import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import Product, ScraperError, ShopInfo, ShopScraper

log = logging.getLogger(__name__)


BASE = "https://shopee.vn"
IMPERSONATE = "chrome124"

# Error codes Shopee
_ERR_ANTIBOT = 90309999
_ERR_INVALID_USERNAME = 2003013


def _build_headers(referer: str | None = None) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Referer": referer or BASE,
        "X-Requested-With": "XMLHttpRequest",
        "X-API-SOURCE": "pc",
        "X-Shopee-Language": "vi",
    }


def _parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse 'k1=v1; k2=v2; ...' → dict."""
    out: dict[str, str] = {}
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class ShopeeScraper(ShopScraper):
    platform = "shopee"

    def __init__(
        self,
        proxy: str | None = None,
        cookie_string: str | None = None,
        auto_cookie: bool = False,
    ):
        self._proxy = proxy
        self._cookie_string = cookie_string
        self._auto_cookie = auto_cookie
        self._session: AsyncSession | None = None
        self._warmed_up = False
        self._cookie_refresh_lock = asyncio.Lock()

    async def _ensure_session(self) -> AsyncSession:
        if self._session is None:
            kwargs: dict[str, Any] = {"impersonate": IMPERSONATE}
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._session = AsyncSession(**kwargs)
            if self._cookie_string:
                for k, v in _parse_cookie_string(self._cookie_string).items():
                    self._session.cookies.set(k, v, domain=".shopee.vn")
                log.debug("Loaded %d cookies từ config", len(self._cookie_string.split(";")))
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _warm_up(self) -> None:
        if self._warmed_up:
            return
        s = await self._ensure_session()
        try:
            await s.get(BASE, headers={"Referer": "https://www.google.com/"})
            self._warmed_up = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Warm-up Shopee fail (vẫn tiếp tục): %s", exc)
            self._warmed_up = True

    # ---------- public API ----------

    async def resolve_shop(self, handle: str) -> ShopInfo:
        username, shop_id_hint = _parse_handle(handle)

        if shop_id_hint and not username:
            data = await self._get_shop_detail(shopid=shop_id_hint)
            return self._build_shop_info(data, fallback_handle=shop_id_hint)

        if username:
            data = await self._get_shop_detail(username=username)
            return self._build_shop_info(data, fallback_handle=username)

        raise ScraperError(f"Không parse được handle: {handle!r}")

    async def list_latest_items(
        self, shop_id: str, limit: int = 30
    ) -> list[Product]:
        url = f"{BASE}/api/v4/search/search_items"
        params = {
            "by": "ctime",
            "fe_categoryids": "",
            "order": "desc",
            "page_type": "shop",
            "scenario": "PAGE_SHOP_SEARCH",
            "version": 2,
            "shop_id": shop_id,
            "limit": max(1, min(100, limit)),
            "newest": 0,
        }
        data = await self._get_json(
            url,
            params=params,
            referer=f"{BASE}/shop/{shop_id}",
        )

        items = data.get("items") or []
        out: list[Product] = []
        for raw in items:
            basic = raw.get("item_basic") or raw
            try:
                out.append(self._build_product(basic, shop_id=shop_id))
            except Exception as exc:  # noqa: BLE001
                log.debug("Bỏ qua item parse fail: %s", exc)
        return out

    # ---------- internals ----------

    @retry(
        reraise=True,
        retry=retry_if_exception_type((curl_requests.RequestsError, ScraperError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    async def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
    ) -> dict[str, Any]:
        await self._warm_up()
        s = await self._ensure_session()
        try:
            resp = await s.get(url, params=params, headers=_build_headers(referer))
        except curl_requests.RequestsError as exc:
            log.warning("HTTP error calling %s: %s", url, exc)
            raise

        status = resp.status_code
        text = resp.text or ""

        if status == 403:
            err_code = _extract_error_code(text)
            if err_code == _ERR_ANTIBOT and self._auto_cookie:
                log.warning("Shopee anti-bot 403, thử refresh cookies bằng Playwright")
                refreshed = await self._refresh_cookies_via_browser()
                if refreshed:
                    raise ScraperError("Đã refresh cookies, retry…")  # tenacity sẽ retry
            raise ScraperError(
                "Shopee chặn request (403). Nếu IP bạn bị flag, set SHOPEE_COOKIE "
                "(paste cookies từ browser thường đã login) hoặc HTTP_PROXY. "
                f"tracking_id={_extract_tracking_id(text)}"
            )
        if status == 404:
            raise ScraperError("Shop không tồn tại (404).")
        if status >= 400:
            raise ScraperError(f"Shopee API trả lỗi {status}: {text[:200]}")

        try:
            data = resp.json()
        except (ValueError, TypeError) as exc:
            raise ScraperError(f"Phản hồi không phải JSON: {exc}") from exc

        err_code = data.get("error")
        if err_code not in (None, 0):
            msg = data.get("error_msg") or data.get("message") or "unknown"
            raise ScraperError(f"Shopee báo lỗi {err_code}: {msg}")
        return data

    async def _refresh_cookies_via_browser(self) -> bool:
        """Mở Chromium headless, vào homepage Shopee, copy cookies vào session.

        Chỉ work nếu IP không bị block ở mức captcha. Trả True nếu lấy được
        ít nhất 1 cookie SPC_*.
        """
        async with self._cookie_refresh_lock:
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                log.error(
                    "SHOPEE_AUTO_COOKIE=1 nhưng playwright chưa cài. "
                    "Chạy: pip install playwright && playwright install chromium"
                )
                return False

            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    ctx_opts: dict[str, Any] = {
                        "locale": "vi-VN",
                        "viewport": {"width": 1366, "height": 900},
                    }
                    if self._proxy:
                        # Playwright proxy format khác curl_cffi
                        ctx_opts["proxy"] = {"server": self._proxy}
                    ctx = await browser.new_context(**ctx_opts)
                    page = await ctx.new_page()
                    await page.goto(
                        BASE, wait_until="domcontentloaded", timeout=30000
                    )
                    await page.wait_for_timeout(2500)
                    cookies = await ctx.cookies("https://shopee.vn")
                    await browser.close()
            except Exception as exc:  # noqa: BLE001
                log.error("Playwright cookie refresh fail: %s", exc)
                return False

            if not cookies:
                log.warning("Browser không lấy được cookie nào (có thể bị captcha)")
                return False

            s = await self._ensure_session()
            count = 0
            for c in cookies:
                if c.get("name", "").startswith(("SPC_", "REC_", "_dd_")):
                    s.cookies.set(
                        c["name"],
                        c.get("value", ""),
                        domain=c.get("domain", ".shopee.vn"),
                    )
                    count += 1
            log.info("Refreshed %d cookies từ browser", count)
            self._warmed_up = True
            return count > 0

    async def _get_shop_detail(
        self,
        username: str | None = None,
        shopid: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if username:
            params["username"] = username
        if shopid:
            params["shopid"] = shopid
        if not params:
            raise ScraperError("get_shop_detail thiếu username/shopid")

        url = f"{BASE}/api/v4/shop/get_shop_detail"
        data = await self._get_json(url, params=params, referer=BASE)
        shop_data = data.get("data")
        if not shop_data or not shop_data.get("shopid"):
            raise ScraperError(
                "Không tìm thấy shop. Kiểm tra username hoặc shop_id."
            )
        return shop_data

    def _build_shop_info(
        self, data: dict[str, Any], fallback_handle: str
    ) -> ShopInfo:
        shop_id = str(data["shopid"])
        username = data.get("account", {}).get("username") or fallback_handle
        name = data.get("name") or username
        return ShopInfo(
            platform=self.platform,
            shop_id=shop_id,
            handle=username,
            name=name,
            url=f"{BASE}/{username}" if username else f"{BASE}/shop/{shop_id}",
        )

    def _build_product(
        self, basic: dict[str, Any], shop_id: str
    ) -> Product:
        item_id = str(basic["itemid"])
        name = basic.get("name", "")

        price_raw = basic.get("price")
        price_max_raw = basic.get("price_max")
        price = price_raw // 100_000 if isinstance(price_raw, int) else None
        price_max = (
            price_max_raw // 100_000 if isinstance(price_max_raw, int) else None
        )

        image_hash = basic.get("image")
        image_url = (
            f"https://down-vn.img.susercontent.com/file/{image_hash}"
            if image_hash
            else None
        )

        slug = _slugify(name) if name else "i"
        url = f"{BASE}/{slug}-i.{shop_id}.{item_id}"

        return Product(
            platform=self.platform,
            shop_id=shop_id,
            item_id=item_id,
            name=name,
            price=price,
            price_max=price_max,
            url=url,
            image_url=image_url,
            stock=basic.get("stock"),
            sold=basic.get("historical_sold") or basic.get("sold"),
            ctime=basic.get("ctime"),
        )


# ---------- helpers ----------

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
_SHOP_URL_RE = re.compile(r"shopee\.vn/shop/(\d+)", re.IGNORECASE)
_ITEM_URL_RE = re.compile(r"-i\.(\d+)\.(\d+)", re.IGNORECASE)
_TRACKING_RE = re.compile(r'"tracking_id"\s*:\s*"([^"]+)"')
_ERROR_RE = re.compile(r'"error"\s*:\s*(\d+)')


def _extract_error_code(text: str) -> int | None:
    m = _ERROR_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_tracking_id(text: str) -> str:
    m = _TRACKING_RE.search(text)
    return m.group(1) if m else "n/a"


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", text.strip())
    s = s.strip("-")[:80]
    return s or "i"


def _parse_handle(raw: str) -> tuple[str | None, str | None]:
    s = raw.strip()
    if not s:
        raise ScraperError("Handle rỗng")

    if s.startswith(("http://", "https://", "shopee.vn/")):
        s = s if s.startswith("http") else "https://" + s

        m = _ITEM_URL_RE.search(s)
        if m:
            return None, m.group(1)

        m = _SHOP_URL_RE.search(s)
        if m:
            return None, m.group(1)

        parsed = urlparse(s)
        path = unquote(parsed.path).strip("/")
        username = path.split("/")[0] if path else ""
        if username:
            return username, None
        raise ScraperError(f"Không parse được Shopee URL: {raw!r}")

    if s.isdigit() and len(s) >= 4:
        return None, s

    return s.lstrip("@"), None
