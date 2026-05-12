"""Shopee scraper qua Playwright headless + stealth.

Approach của bot reference:
1. Mở trang shop /shop/{id}/search → Shopee tự gọi internal API
   (search_items hoặc rcmd_items) trong browser
2. Hook page.on('response', ...) → bắt response JSON
3. Browser session có cookies đầy đủ → bypass anti-bot
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .base import Product, ScraperError, ShopInfo, ShopScraper

log = logging.getLogger(__name__)


BASE = "https://shopee.vn"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Endpoint Shopee gọi từ browser khi vào /shop/{id}/search
_API_PATTERNS = ("search_items", "rcmd_items", "recommend")


class ShopeeScraper(ShopScraper):
    """Mở trang shop, intercept API response do Shopee tự gọi."""

    platform = "shopee"

    # Cookie persistence (lưu vào /app/data/ để survive restart)
    COOKIE_CACHE_FILE = Path(
        os.getenv("DB_PATH", "data/shop_watcher.db")
    ).parent / "shopee_cookies_live.json"

    def __init__(
        self,
        proxy: str | None = None,
        cookie_string: str | None = None,
        cookies_json: str | None = None,
    ):
        self._proxy = proxy
        self._cookie_string = cookie_string
        self._cookies_json = cookies_json
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._init_lock = asyncio.Lock()
        self._last_cookie_save = 0.0

    async def _ensure_context(self) -> BrowserContext:
        async with self._init_lock:
            if self._context is not None:
                return self._context
            self._pw = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            }
            proxy_cfg = _parse_proxy(self._proxy)
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
                log.info("Playwright dùng proxy: %s", proxy_cfg.get("server"))

            self._browser = await self._pw.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale="vi-VN",
                timezone_id="Asia/Bangkok",
            )

            cookies = self._load_cookies()
            if cookies:
                await self._context.add_cookies(cookies)
                log.info("Playwright context: nạp %d cookies", len(cookies))
            else:
                log.warning("Playwright context: KHÔNG có cookie nào — dễ bị Shopee chặn")

            # Stealth (optional): chỉ apply nếu lib có sẵn
            try:
                from playwright_stealth import Stealth  # type: ignore
                self._stealth = Stealth()
                log.info("playwright-stealth enabled")
            except ImportError:
                self._stealth = None
                log.debug("playwright-stealth không cài, dùng default fingerprint")

            return self._context

    def _load_cookies(self) -> list[dict[str, Any]]:
        """Trả về list cookies. Ưu tiên file cache (đã refresh runtime) > env.

        Cache file được update sau mỗi poll → cookies luôn fresh nhất có thể.
        Khi restart container, cache (trong volume /app/data) vẫn còn → không
        rớt session.
        """
        # Cache file: cookies đã được Playwright refresh trong runtime trước
        if self.COOKIE_CACHE_FILE.exists():
            try:
                age = time.time() - self.COOKIE_CACHE_FILE.stat().st_mtime
                cached_raw = json.loads(self.COOKIE_CACHE_FILE.read_text(encoding="utf-8"))
                # Cache có giá trị nếu < 7 ngày (cookie Shopee thường sống ~14 ngày)
                if cached_raw and age < 7 * 24 * 3600:
                    log.info(
                        "Load %d cookies từ cache (refresh %dm trước)",
                        len(cached_raw), int(age / 60),
                    )
                    return cached_raw
                log.info("Cookie cache quá cũ (%dh) — dùng env", age / 3600)
            except Exception as exc:  # noqa: BLE001
                log.warning("Đọc cookie cache fail: %s — fallback env", exc)

        out: list[dict[str, Any]] = []

        # Ưu tiên SHOPEE_COOKIES_JSON (full format từ Chrome extension export)
        if self._cookies_json:
            try:
                raw = json.loads(self._cookies_json)
                for c in raw:
                    cookie = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".shopee.vn"),
                        "path": c.get("path", "/"),
                    }
                    if "expirationDate" in c:
                        cookie["expires"] = int(c["expirationDate"])
                    if c.get("secure"):
                        cookie["secure"] = True
                    if c.get("httpOnly"):
                        cookie["httpOnly"] = True
                    ss = (c.get("sameSite") or "unspecified").capitalize()
                    cookie["sameSite"] = ss if ss in ("Strict", "Lax", "None") else "None"
                    out.append(cookie)
                return out
            except Exception as exc:  # noqa: BLE001
                log.warning("Parse SHOPEE_COOKIES_JSON fail: %s", exc)

        # Fallback: SHOPEE_COOKIE string format `name=value;`
        if self._cookie_string:
            for chunk in self._cookie_string.split(";"):
                chunk = chunk.strip()
                if not chunk or "=" not in chunk:
                    continue
                k, v = chunk.split("=", 1)
                out.append({
                    "name": k.strip(),
                    "value": v.strip(),
                    "domain": ".shopee.vn",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                })
        return out

    async def _persist_cookies(self, force: bool = False) -> None:
        """Lấy cookies hiện tại từ browser context, lưu vào file cache.

        Throttle: chỉ ghi mỗi 60s (trừ khi force=True). Browser context
        tự refresh cookies khi navigate → cookies trong context luôn mới
        nhất so với env input.
        """
        if not self._context:
            return
        now = time.time()
        if not force and (now - self._last_cookie_save) < 60:
            return
        try:
            cookies = await self._context.cookies("https://shopee.vn")
            if not cookies:
                return
            self.COOKIE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.COOKIE_CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.COOKIE_CACHE_FILE)
            self._last_cookie_save = now
            log.debug("Persisted %d cookies → %s", len(cookies), self.COOKIE_CACHE_FILE)
        except Exception as exc:  # noqa: BLE001
            log.debug("Persist cookies fail: %s", exc)

    async def close(self) -> None:
        # Save trước khi đóng để không mất state
        await self._persist_cookies(force=True)
        async with self._init_lock:
            try:
                if self._context:
                    await self._context.close()
                if self._browser:
                    await self._browser.close()
                if self._pw:
                    await self._pw.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("Playwright close error: %s", exc)
            self._context = None
            self._browser = None
            self._pw = None

    # ---------- public API ----------

    async def resolve_shop(self, handle: str) -> ShopInfo:
        """Mở trang /shop/{id} hoặc /{username}, đọc title + URL final."""
        username, shop_id_hint = _parse_handle(handle)

        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            if shop_id_hint:
                target_url = f"{BASE}/shop/{shop_id_hint}"
            else:
                target_url = f"{BASE}/{username}"

            shop_id: str | None = shop_id_hint
            captured_shop_id: list[str] = []

            async def on_response(resp):
                # Bắt /api/v4/shop/get_shop_detail để confirm shop_id
                if "shop/get_shop_detail" in resp.url:
                    try:
                        d = await resp.json()
                        sd = d.get("data") or {}
                        sid = str(sd.get("shopid") or "")
                        if sid:
                            captured_shop_id.append(sid)
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)

            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:  # noqa: BLE001
                raise ScraperError(f"Không load được trang shop: {exc}") from exc

            # Đợi tí cho Shopee gọi xong shop_detail
            await asyncio.sleep(3)

            if captured_shop_id:
                shop_id = captured_shop_id[0]

            # Nếu vẫn chưa có shop_id (chỉ có username) → parse URL hiện tại
            if not shop_id:
                cur = page.url
                m = re.search(r"/shop/(\d+)", cur)
                if m:
                    shop_id = m.group(1)

            if not shop_id:
                raise ScraperError("Không xác định được shop_id từ URL hoặc API response")

            # Lấy shop name từ title hoặc query selector
            try:
                name = await page.title()
                # Title dạng "{Name} | Shopee Việt Nam" → lấy phần trước "|"
                if "|" in name:
                    name = name.split("|")[0].strip()
            except Exception:  # noqa: BLE001
                name = username or shop_id

            return ShopInfo(
                platform=self.platform,
                shop_id=shop_id,
                handle=username or shop_id,
                name=name,
                url=f"{BASE}/shop/{shop_id}",
            )
        finally:
            await page.close()

    async def list_latest_items(
        self, shop_id: str, limit: int = 30
    ) -> list[Product]:
        ctx = await self._ensure_context()
        page = await ctx.new_page()
        if self._stealth:
            try:
                await self._stealth.apply_stealth_async(page)
            except Exception as exc:  # noqa: BLE001
                log.debug("apply stealth fail: %s", exc)

        items_raw: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_api_urls: list[str] = []
        data_event = asyncio.Event()

        async def on_response(resp):
            url = resp.url
            # Log mọi API call Shopee để debug
            if "/api/" in url and "shopee" in url:
                short = url.split("?")[0].split("/api/")[-1][:80]
                seen_api_urls.append(short)

            if not any(p in url for p in _API_PATTERNS):
                return
            try:
                data = await resp.json()
            except Exception:  # noqa: BLE001
                return

            found = data.get("items") or []

            if not found:
                d = data.get("data") or {}
                for sec in d.get("sections", []) or []:
                    sd = sec.get("data") or {}
                    found.extend(sd.get("item") or sd.get("items") or sd.get("item_cards") or [])
                if not found:
                    cic = (d.get("centralize_item_card") or {})
                    found = cic.get("item_cards") or []

            for it in found:
                info = it.get("item_basic") or it
                iid = info.get("itemid")
                if iid is None:
                    iid = (info.get("item") or {}).get("itemid")
                if iid is None:
                    continue
                sid = str(iid)
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                items_raw.append(info)

            if items_raw:
                data_event.set()

        page.on("response", on_response)

        try:
            await page.goto(
                f"{BASE}/shop/{shop_id}/search",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as exc:  # noqa: BLE001
            await page.close()
            raise ScraperError(f"Không load được trang shop search: {exc}") from exc

        try:
            await asyncio.wait_for(data_event.wait(), timeout=20)
        except asyncio.TimeoutError:
            log.warning(
                "[shop %s] Playwright không bắt được API response trong 20s. "
                "Shopee API URLs đã thấy (%d): %s",
                shop_id,
                len(seen_api_urls),
                seen_api_urls[:15] if seen_api_urls else "(none)",
            )
            # Check xem trang có captcha không
            try:
                content = await page.content()
                if "captcha" in content.lower() or "verify" in content.lower():
                    log.warning("[shop %s] Trang có CAPTCHA — cookies hết hạn?", shop_id)
            except Exception:  # noqa: BLE001
                pass

        await asyncio.sleep(3)
        await page.close()

        # Persist cookies sau mỗi scrape — auto-refresh khỏi env-locked state
        await self._persist_cookies()

        # Build Product
        out: list[Product] = []
        for info in items_raw[:limit]:
            try:
                out.append(self._build_product(info, shop_id=shop_id))
            except Exception as exc:  # noqa: BLE001
                log.debug("Parse item fail: %s", exc)

        out.sort(key=lambda p: p.ctime or 0, reverse=True)
        return out

    def _build_product(self, info: dict[str, Any], shop_id: str) -> Product:
        # Hỗ trợ cả format cũ (search_items) lẫn format mới (rcmd item_card)
        item_id = info.get("itemid") or (info.get("item") or {}).get("itemid")
        item_id = str(item_id)
        name = (
            info.get("name")
            or (info.get("item_card_displayed_asset") or {}).get("name")
            or ""
        )

        # Price: search_items có price (raw * 100000). rcmd item_card có
        # item_card_display_price.display_price.price (cũng raw * 100000)
        price = None
        price_max = None
        if isinstance(info.get("price"), int):
            price = info["price"] // 100_000
        elif "item_card_display_price" in info:
            dp = info["item_card_display_price"].get("display_price") or {}
            raw = dp.get("price")
            if isinstance(raw, int):
                price = raw // 100_000

        if isinstance(info.get("price_max"), int):
            price_max = info["price_max"] // 100_000

        image_hash = info.get("image") or (
            info.get("item_card_displayed_asset") or {}
        ).get("image")
        image_url = (
            f"https://down-vn.img.susercontent.com/file/{image_hash}"
            if image_hash else None
        )

        sold = (
            info.get("historical_sold")
            or info.get("sold")
            or (info.get("item_card_display_sold_count") or {}).get("historical_sold_count")
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
            stock=info.get("stock"),
            sold=sold,
            ctime=info.get("ctime"),
        )


# ---------- helpers (copy từ shopee.py để không phụ thuộc) ----------

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
_SHOP_URL_RE = re.compile(r"shopee\.vn/shop/(\d+)", re.IGNORECASE)
_ITEM_URL_RE = re.compile(r"-i\.(\d+)\.(\d+)", re.IGNORECASE)


def _parse_proxy(raw: str | None) -> dict[str, Any] | None:
    """Parse 'http://user:pass@host:port' → Playwright proxy config."""
    if not raw:
        return None
    from urllib.parse import urlparse
    p = urlparse(raw if "://" in raw else f"http://{raw}")
    if not p.hostname:
        return None
    scheme = p.scheme or "http"
    port = p.port or (1080 if scheme.startswith("socks") else 8080)
    cfg: dict[str, Any] = {"server": f"{scheme}://{p.hostname}:{port}"}
    if p.username:
        cfg["username"] = p.username
    if p.password:
        cfg["password"] = p.password
    return cfg


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", text.strip())
    return s.strip("-")[:80] or "i"


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
