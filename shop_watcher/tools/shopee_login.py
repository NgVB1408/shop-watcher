"""One-shot helper: mở Chromium thật, để user login Shopee, rồi in cookie string
ra stdout (paste vào SHOPEE_COOKIE trong .env).

Cách dùng:
    python -m shop_watcher.tools.shopee_login
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Thiếu playwright. Cài: pip install playwright && playwright install chromium")
    sys.exit(1)


COOKIE_NAMES_NEEDED = {"SPC_F", "SPC_CLIENTID", "SPC_R_T_ID", "REC_T_ID", "SPC_EC", "SPC_U", "csrftoken"}


async def main() -> int:
    print("🌐 Đang mở Chromium. Hãy login Shopee (nếu cần) và browse 1 trang shop bất kỳ.")
    print("   Sau đó đóng browser, script sẽ in cookie string ra để paste vào .env\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            locale="vi-VN",
            viewport={"width": 1366, "height": 900},
        )
        page = await ctx.new_page()
        await page.goto("https://shopee.vn/")

        # Đợi user đóng browser hoặc nhấn Ctrl-C
        try:
            await page.wait_for_event("close", timeout=0)
        except Exception:  # noqa: BLE001
            pass

        cookies = await ctx.cookies("https://shopee.vn")
        await browser.close()

    if not cookies:
        print("\n❌ Không lấy được cookie nào.")
        return 1

    # Filter chỉ các cookie liên quan Shopee
    relevant = [
        c for c in cookies
        if c["name"] in COOKIE_NAMES_NEEDED or c["name"].startswith(("SPC_", "REC_"))
    ]
    if not relevant:
        relevant = cookies

    cookie_string = "; ".join(f"{c['name']}={c['value']}" for c in relevant)

    print("\n" + "=" * 60)
    print("✅ Cookie string (paste vào .env):")
    print("=" * 60)
    print(f'SHOPEE_COOKIE={cookie_string}')
    print("=" * 60)

    # Cũng ghi ra file để dễ copy
    out_path = Path(__file__).resolve().parent.parent.parent / "data" / "shopee_cookie.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(cookie_string, encoding="utf-8")
    print(f"\n📁 Đã ghi cookie ra: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
