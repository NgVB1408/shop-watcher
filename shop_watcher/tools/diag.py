"""Diagnostic: kiểm tra cấu hình + test scraper Playwright.

Dùng:
    .venv\\Scripts\\python.exe -m shop_watcher.tools.diag
    .venv\\Scripts\\python.exe -m shop_watcher.tools.diag <shop_id_hoặc_url>
"""

from __future__ import annotations

import asyncio
import json
import sys

from shop_watcher.config import Settings, configure_logging
from shop_watcher.scrapers import get_scraper


def _mask(s: str | None, keep: int = 6) -> str:
    if not s:
        return "(empty)"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "…" + s[-keep:]


async def run(target: str) -> int:
    try:
        settings = Settings.load()
    except RuntimeError as exc:
        print(f"❌ Config error: {exc}")
        return 1

    configure_logging("INFO")

    print("=" * 60)
    print("CẤU HÌNH")
    print("=" * 60)
    print(f"TELEGRAM_BOT_TOKEN  : {_mask(settings.telegram_bot_token, 4)}")
    print(f"ALLOWED_CHAT_IDS    : {sorted(settings.allowed_chat_ids) or '(open)'}")
    print(f"POLL_INTERVAL       : {settings.poll_interval_seconds}s")
    print(f"ITEMS_PER_CHECK     : {settings.items_per_check}")
    print(f"HTTP_PROXY          : {settings.http_proxy or '(none)'}")

    cookie_count = 0
    if settings.shopee_cookies_json:
        try:
            cookies = json.loads(settings.shopee_cookies_json)
            cookie_count = len(cookies)
            print(f"SHOPEE_COOKIES_JSON : {cookie_count} cookies (JSON format)")
            critical = {"SPC_F", "SPC_CLIENTID", "SPC_R_T_ID", "SPC_T_ID", "SPC_EC", "SPC_U", "csrftoken"}
            names = {c.get("name", "") for c in cookies}
            present = sorted(names & critical)
            missing = sorted(critical - names)
            print(f"  Critical có        : {present or '(none)'}")
            print(f"  Critical thiếu     : {missing or '(none)'}")
        except Exception as exc:  # noqa: BLE001
            print(f"SHOPEE_COOKIES_JSON : ❌ JSON parse fail: {exc}")
    elif settings.shopee_cookie:
        names = [c.split("=", 1)[0].strip() for c in settings.shopee_cookie.split(";") if "=" in c]
        cookie_count = len(names)
        print(f"SHOPEE_COOKIE       : {cookie_count} cookies (string format)")
        print(f"  Names              : {names}")
    else:
        print("⚠️ Chưa có cookie nào — Shopee có thể chặn request.")
    print()

    print("=" * 60)
    print(f"TEST scraper với target: {target}")
    print("=" * 60)

    scraper = get_scraper("shopee", settings)

    try:
        print("\n[1/2] resolve_shop …")
        try:
            info = await scraper.resolve_shop(target)
            print(f"   ✓ Shop tìm thấy:")
            print(f"     shop_id : {info.shop_id}")
            print(f"     handle  : {info.handle}")
            print(f"     name    : {info.name}")
            print(f"     url     : {info.url}")
        except Exception as exc:
            print(f"   ✗ {type(exc).__name__}: {exc}")
            return 2

        shop_id = info.shop_id

        print(f"\n[2/2] list_latest_items(shop_id={shop_id}, limit=5) …")
        try:
            items = await scraper.list_latest_items(shop_id, limit=5)
            print(f"   ✓ Lấy được {len(items)} sản phẩm:")
            for i in items[:5]:
                print(f"     - {i.item_id} | {i.name[:60]}")
                print(f"       {i.price_text} | sold={i.sold} | ctime={i.ctime}")
            if not items:
                print("   ⚠️ Không lấy được item nào — có thể browser chưa load xong "
                      "hoặc Shopee chặn hoàn toàn.")
                return 3
        except Exception as exc:
            print(f"   ✗ {type(exc).__name__}: {exc}")
            return 3

        print()
        print("=" * 60)
        print("✅ TẤT CẢ OK — bot sẽ chạy được.")
        print("=" * 60)
        return 0
    finally:
        await scraper.close()


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "https://shopee.vn/shop/104274078"
    return asyncio.run(run(target))


if __name__ == "__main__":
    raise SystemExit(main())
