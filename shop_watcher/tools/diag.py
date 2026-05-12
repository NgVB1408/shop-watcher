"""Diagnostic: kiểm tra cấu hình + gọi thật Shopee API.

Dùng:
    .venv\\Scripts\\python.exe -m shop_watcher.tools.diag
    .venv\\Scripts\\python.exe -m shop_watcher.tools.diag <shop_id_hoặc_url>
"""

from __future__ import annotations

import asyncio
import sys

from shop_watcher.config import Settings, configure_logging
from shop_watcher.scrapers.shopee import ShopeeScraper, _parse_cookie_string


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
    print(f"SHOPEE_AUTO_COOKIE  : {settings.shopee_auto_cookie}")
    print()

    cookies = _parse_cookie_string(settings.shopee_cookie or "")
    print(f"SHOPEE_COOKIE       : {len(cookies)} cookie(s)")
    if cookies:
        critical = {"SPC_F", "SPC_CLIENTID", "SPC_R_T_ID", "SPC_T_ID", "SPC_EC", "SPC_U", "csrftoken"}
        present = sorted(set(cookies.keys()) & critical)
        missing = sorted(critical - set(cookies.keys()))
        print(f"  Critical có        : {present or '(none)'}")
        print(f"  Critical thiếu     : {missing or '(none)'}")
        print(f"  Tất cả cookie names: {sorted(cookies.keys())}")
        for k, v in list(cookies.items())[:3]:
            print(f"    - {k} = {_mask(v, 4)}")
    else:
        print("  ⚠️ Chưa set cookie — nếu IP bị Shopee flag sẽ 403.")
    print()

    print("=" * 60)
    print(f"TEST API với target: {target}")
    print("=" * 60)

    scraper = ShopeeScraper(
        proxy=settings.http_proxy,
        cookie_string=settings.shopee_cookie,
        auto_cookie=settings.shopee_auto_cookie,
    )

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
            print()
            print("   → Nếu là 403/error 90309999: IP bị Shopee block.")
            print("     • Copy cookies từ Chrome (DevTools > Application > Cookies > shopee.vn)")
            print("     • Hoặc set HTTP_PROXY tới VPS sạch")
            print("     • Hoặc deploy bot lên VPS")
            return 2

        shop_id = info.shop_id

        print(f"\n[2/2] list_latest_items(shop_id={shop_id}, limit=5) …")
        try:
            items = await scraper.list_latest_items(shop_id, limit=5)
            print(f"   ✓ Lấy được {len(items)} sản phẩm:")
            for i in items[:5]:
                print(f"     - {i.item_id} | {i.name[:60]}")
                print(f"       {i.price_text} | sold={i.sold} | ctime={i.ctime}")
        except Exception as exc:
            print(f"   ✗ {type(exc).__name__}: {exc}")
            return 3

        print()
        print("=" * 60)
        print("✅ TẤT CẢ OK — bot sẽ chạy được. Nếu bot vẫn báo lỗi:")
        print("   1. Đảm bảo đã RESTART bot sau khi sửa .env")
        print("   2. Gõ /check trên Telegram để force poll ngay")
        print("=" * 60)
        return 0
    finally:
        await scraper.close()


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "https://shopee.vn/shop/104274078"
    return asyncio.run(run(target))


if __name__ == "__main__":
    raise SystemExit(main())
