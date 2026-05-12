"""CLI tool: import nhiều link Shopee (kể cả shortlink/affiliate) vào DB.

Mỗi link sẽ:
  1. Follow HTTP redirect → URL canonical (xử lý s.shopee.vn, vn.shp.ee).
  2. Parse shopid hoặc username.
  3. Gọi Shopee API `get_shop_detail` để lấy tên + shop_id chuẩn.
  4. INSERT vào bảng `shops` cho chat_id chỉ định.

Dùng khi muốn bulk-add nhiều shop mà không cần mở Telegram chat với bot.

Cú pháp:
    python -m shop_watcher.tools.import_links \\
        --chat-id 5240277805 \\
        https://s.shopee.vn/3LNbu0y0ZB \\
        https://vn.shp.ee/YZv3Ca3x

`--chat-id` có thể bỏ qua nếu `.env` đã set `TELEGRAM_ALLOWED_CHAT_IDS` —
script sẽ lấy chat_id đầu tiên.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Iterable

from curl_cffi import requests as curl_requests

from ..config import PROJECT_ROOT, configure_logging
from ..db import Database
from ..scrapers import ScraperError, ShopeeScraper

log = logging.getLogger("import_links")

SHORTLINK_HOSTS = ("s.shopee.vn", "vn.shp.ee", "shp.ee", "shope.ee")
IMPERSONATE = "chrome124"


def _is_shortlink(url: str) -> bool:
    lower = url.lower()
    return any(h in lower for h in SHORTLINK_HOSTS)


async def _expand_shortlink(
    url: str, proxy: str | None = None
) -> str:
    """Follow redirect chain, trả URL cuối."""
    kwargs = {"impersonate": IMPERSONATE, "timeout": 15}
    if proxy:
        kwargs["proxy"] = proxy
    async with curl_requests.AsyncSession(**kwargs) as s:
        try:
            r = await s.get(url, allow_redirects=True)
            return str(r.url)
        except curl_requests.RequestsError as exc:
            raise RuntimeError(f"Expand shortlink fail: {exc}") from exc


def _load_default_chat_id() -> int | None:
    """Lấy chat_id đầu tiên từ TELEGRAM_ALLOWED_CHAT_IDS trong .env."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    try:
        return int(first)
    except ValueError:
        return None


async def _import_one(
    scraper: ShopeeScraper,
    db: Database,
    chat_id: int,
    url: str,
    proxy: str | None,
) -> dict:
    """Trả dict result để in summary."""
    orig = url
    if _is_shortlink(url):
        try:
            url = await _expand_shortlink(url, proxy=proxy)
            log.info("Expand %s → %s", orig, url)
        except RuntimeError as exc:
            return {"orig": orig, "ok": False, "error": str(exc)}

    try:
        info = await scraper.resolve_shop(url)
    except ScraperError as exc:
        return {"orig": orig, "expanded": url, "ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("resolve fail")
        return {"orig": orig, "expanded": url, "ok": False, "error": repr(exc)}

    shop, created = db.add_shop(
        chat_id=chat_id,
        platform=info.platform,
        shop_handle=info.handle,
        shop_id=info.shop_id,
        shop_name=info.name,
    )
    return {
        "orig": orig,
        "expanded": url,
        "ok": True,
        "created": created,
        "shop_id": shop.shop_id,
        "handle": shop.shop_handle,
        "name": shop.shop_name,
    }


async def main_async(args: argparse.Namespace) -> int:
    configure_logging(args.log_level)

    chat_id = args.chat_id
    if chat_id is None:
        chat_id = _load_default_chat_id()
        if chat_id is None:
            log.error(
                "Không tìm thấy chat_id. Truyền --chat-id <id> hoặc set "
                "TELEGRAM_ALLOWED_CHAT_IDS trong .env"
            )
            return 2
        log.info("Dùng chat_id từ .env: %s", chat_id)

    db_path = args.db or (PROJECT_ROOT / "data" / "shop_watcher.db")
    db = Database(db_path)

    proxy = os.getenv("HTTP_PROXY", "").strip() or None
    cookie = os.getenv("SHOPEE_COOKIE", "").strip() or None
    auto_cookie = (os.getenv("SHOPEE_AUTO_COOKIE", "0").strip().lower() in
                   {"1", "true", "yes", "on"})
    scraper = ShopeeScraper(
        proxy=proxy, cookie_string=cookie, auto_cookie=auto_cookie
    )

    results: list[dict] = []
    try:
        for raw_url in args.urls:
            url = raw_url.strip()
            if not url:
                continue
            log.info("→ %s", url)
            res = await _import_one(scraper, db, chat_id, url, proxy)
            results.append(res)
    finally:
        await scraper.close()
        db.close()

    # Summary in cuối
    print("\n" + "=" * 70)
    print(f"IMPORT SUMMARY — chat_id={chat_id}")
    print("=" * 70)
    ok = sum(1 for r in results if r["ok"])
    created = sum(1 for r in results if r.get("created"))
    failed = len(results) - ok
    for i, r in enumerate(results, 1):
        if r["ok"]:
            status = "NEW" if r["created"] else "EXIST"
            print(
                f"[{i}] {status:5} {r['shop_id']:>12}  {r['handle']}  "
                f"— {r['name']}"
            )
            print(f"      from: {r['orig']}")
        else:
            print(f"[{i}] FAIL  {r['orig']}")
            print(f"      reason: {r['error']}")
    print("-" * 70)
    print(
        f"Tổng: {len(results)} link · OK: {ok} · "
        f"thêm mới: {created} · fail: {failed}"
    )
    return 0 if failed == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shop_watcher.tools.import_links",
        description="Bulk-import Shopee shop URL/shortlink vào DB shop-watcher.",
    )
    p.add_argument(
        "urls",
        nargs="+",
        help="Một hoặc nhiều URL/shortlink Shopee.",
    )
    p.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help=(
            "Telegram chat_id sẽ nhận noti. Bỏ qua = lấy ID đầu tiên trong "
            "TELEGRAM_ALLOWED_CHAT_IDS từ .env."
        ),
    )
    p.add_argument(
        "--db",
        type=str,
        default=None,
        help="Đường dẫn SQLite. Mặc định: data/shop_watcher.db",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.db:
        from pathlib import Path

        args.db = Path(args.db)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
