from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError

from .scrapers.base import Product

log = logging.getLogger(__name__)

MAX_NOTIFICATIONS_PER_BATCH = 10
MAX_RETRY_AFTER_ATTEMPTS = 3
MAX_RETRY_AFTER_WAIT = 60


def format_product_message(product: Product, shop_name: str | None) -> str:
    title = escape(product.name or "(không tên)")
    shop = escape(shop_name or product.shop_id)
    price = escape(product.price_text)
    sold = f" · đã bán {product.sold}" if product.sold else ""
    stock = f" · còn {product.stock}" if isinstance(product.stock, int) else ""

    lines = [
        f"🆕 <b>Sản phẩm mới</b> từ <b>{shop}</b>",
        f"📦 {title}",
        f"💰 {price}{sold}{stock}",
    ]
    if product.url:
        lines.append(f'🔗 <a href="{escape(product.url)}">Xem trên Shopee</a>')
    return "\n".join(lines)


async def _send_single(
    bot: Bot,
    chat_id: int,
    text: str,
    image_url: str | None,
    attempt: int = 0,
) -> bool:
    """Gửi 1 message; trả True nếu thành công. Bounded retry trên RetryAfter."""
    try:
        if image_url:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                )
                return True
            except RetryAfter:
                raise
            except TelegramError as exc:
                log.debug("send_photo fail (%s), fallback text", exc)

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        return True
    except Forbidden:
        log.warning("Chat %s đã block bot hoặc bot không có quyền, skip", chat_id)
        return False
    except RetryAfter as exc:
        if attempt >= MAX_RETRY_AFTER_ATTEMPTS:
            log.error(
                "Telegram rate-limit > %d lần, bỏ message tới %s",
                MAX_RETRY_AFTER_ATTEMPTS,
                chat_id,
            )
            return False
        wait = min(int(exc.retry_after) + 1, MAX_RETRY_AFTER_WAIT)
        log.warning(
            "Telegram rate-limit, chờ %ss (attempt %d/%d)",
            wait,
            attempt + 1,
            MAX_RETRY_AFTER_ATTEMPTS,
        )
        await asyncio.sleep(wait)
        return await _send_single(bot, chat_id, text, image_url, attempt + 1)
    except TelegramError as exc:
        log.error("Gửi tới %s fail: %s", chat_id, exc)
        return False


async def send_product_notification(
    bot: Bot, chat_id: int, product: Product, shop_name: str | None
) -> bool:
    text = format_product_message(product, shop_name)
    return await _send_single(bot, chat_id, text, product.image_url)


async def send_batch(
    bot: Bot, chat_id: int, products: list[Product], shop_name: str | None
) -> int:
    sent = 0
    to_send = products[:MAX_NOTIFICATIONS_PER_BATCH]
    remaining = len(products) - len(to_send)

    for p in to_send:
        ok = await send_product_notification(bot, chat_id, p, shop_name)
        if ok:
            sent += 1
        await asyncio.sleep(0.5)

    if remaining > 0:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"… và {remaining} sản phẩm mới khác. Dùng /list để xem shop.",
            )
        except TelegramError:
            pass

    return sent
