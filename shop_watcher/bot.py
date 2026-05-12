from __future__ import annotations

import logging
import re
from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings
from .db import Database
from .poller import Poller
from .scrapers import ScraperError, get_scraper, supported_platforms

log = logging.getLogger(__name__)


HELP_TEXT = (
    "<b>Shop Watcher</b> — báo sản phẩm mới từ shop bạn theo dõi.\n\n"
    "<b>Cách dùng nhanh</b>:\n"
    "• Gửi <b>link Shopee</b> (URL shop hoặc URL sản phẩm) → bot hiện nút "
    "<i>Theo dõi shop này</i>.\n"
    "• Gõ <code>/list</code> → mỗi shop có nút <i>Bỏ</i> để gỡ.\n\n"
    "<b>Commands</b>:\n"
    "/add &lt;url|username&gt; — thêm shop\n"
    "/list — danh sách shop + nút bỏ\n"
    "/remove &lt;handle&gt; — bỏ shop (text)\n"
    "/check — chạy poll ngay\n"
    "/status — runtime info\n"
    "/help — hướng dẫn\n\n"
    "Ví dụ paste vào chat:\n"
    "<code>https://shopee.vn/shop/12345678</code>\n"
    "<code>https://shopee.vn/shop_username</code>"
)


# Regex detect Shopee URL trong message text
SHOPEE_URL_RE = re.compile(
    r"https?://(?:www\.)?shopee\.vn/\S+", re.IGNORECASE
)

# Callback data prefixes
CB_ADD = "add:"
CB_RM = "rm:"
CB_RM_CONFIRM = "rmok:"
CB_CANCEL = "cancel"


def _is_allowed(settings: Settings, chat_id: int) -> bool:
    if not settings.allowed_chat_ids:
        return True
    return chat_id in settings.allowed_chat_ids


async def _guard(update: Update, settings: Settings) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if _is_allowed(settings, chat.id):
        return True
    log.warning("Chat %s không nằm trong allowlist", chat.id)
    if update.effective_message:
        await update.effective_message.reply_text(
            f"Chat ID của bạn ({chat.id}) chưa được cấp phép. "
            "Thêm vào TELEGRAM_ALLOWED_CHAT_IDS trong .env."
        )
    return False


def _parse_add_args(args: list[str]) -> tuple[str, str]:
    if not args:
        raise ValueError(
            "Thiếu tham số. Dùng: /add <url|username> hoặc /add shopee <url|username>"
        )
    platforms = set(supported_platforms())
    if args[0].lower() in platforms:
        if len(args) < 2:
            raise ValueError("Thiếu URL/username sau tên platform.")
        return args[0].lower(), " ".join(args[1:])
    return "shopee", " ".join(args)


# ============================================================
# CORE LOGIC (share giữa /add command và button callback)
# ============================================================


async def _do_add_shop(
    settings: Settings,
    db: Database,
    chat_id: int,
    platform: str,
    handle: str,
) -> tuple[bool, str]:
    """Trả (success, html_message). Không gửi message — caller tự gửi."""
    scraper = get_scraper(platform, settings)
    try:
        try:
            info = await scraper.resolve_shop(handle)
        except ScraperError as exc:
            return False, f"❌ {escape(str(exc))}"
        except Exception as exc:  # noqa: BLE001
            log.exception("resolve_shop fail")
            return False, f"❌ Lỗi không xác định: {escape(str(exc))}"
    finally:
        await scraper.close()

    shop, created = db.add_shop(
        chat_id=chat_id,
        platform=info.platform,
        shop_handle=info.handle,
        shop_id=info.shop_id,
        shop_name=info.name,
    )
    if not created:
        return False, (
            f"ℹ️ Đang theo dõi shop này rồi: "
            f"<b>{escape(shop.shop_name or shop.shop_handle)}</b>"
        )
    return True, (
        f"✅ Đã thêm: <b>{escape(info.name or info.handle)}</b>\n"
        f"Shop ID: <code>{info.shop_id}</code>\n"
        f"Handle: <code>{escape(info.handle)}</code>\n\n"
        "Lượt poll đầu tiên sẽ tạo baseline (không gửi noti) để tránh spam. "
        "Sau đó mọi sản phẩm mới sẽ được báo ngay."
    )


# ============================================================
# COMMANDS
# ============================================================


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    chat_id = update.effective_chat.id
    log.info("/start từ chat_id=%s user=%s", chat_id, update.effective_user.username)
    msg = HELP_TEXT
    if not _is_allowed(settings, chat_id):
        msg += (
            f"\n\n⚠️ Chat ID của bạn là <code>{chat_id}</code>. "
            "Hiện chưa được cấp phép — thêm vào TELEGRAM_ALLOWED_CHAT_IDS."
        )
    await update.effective_message.reply_html(msg, disable_web_page_preview=True)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, ctx.application.bot_data["settings"]):
        return
    await update.effective_message.reply_html(HELP_TEXT, disable_web_page_preview=True)


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    db: Database = ctx.application.bot_data["db"]
    if not await _guard(update, settings):
        return

    try:
        platform, handle = _parse_add_args(ctx.args or [])
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    msg = await update.effective_message.reply_text(
        f"⏳ Đang resolve {platform}:{handle} …"
    )
    ok, text = await _do_add_shop(settings, db, update.effective_chat.id, platform, handle)
    await msg.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    db: Database = ctx.application.bot_data["db"]
    if not await _guard(update, settings):
        return

    if not ctx.args:
        await update.effective_message.reply_text("Dùng: /remove <username|shop_id>")
        return

    chat_id = update.effective_chat.id
    handle = " ".join(ctx.args)
    shop = db.remove_shop(chat_id, handle)
    if not shop:
        await update.effective_message.reply_text(
            f"Không tìm thấy shop {handle!r} trong danh sách của bạn."
        )
        return
    await update.effective_message.reply_html(
        f"🗑 Đã bỏ theo dõi: <b>{escape(shop.shop_name or shop.shop_handle)}</b>"
    )


def _build_list_keyboard(shops, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Inline keyboard: mỗi shop 1 hàng với nút 🗑 Bỏ."""
    start = page * per_page
    end = start + per_page
    rows = []
    for s in shops[start:end]:
        label = (s.shop_name or s.shop_handle)[:30]
        rows.append(
            [InlineKeyboardButton(f"🗑 {label}", callback_data=f"{CB_RM}{s.id}")]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Trước", callback_data=f"page:{page - 1}"))
    if end < len(shops):
        nav.append(InlineKeyboardButton("Sau ▶️", callback_data=f"page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _format_list_text(shops) -> str:
    lines = [f"<b>Đang theo dõi {len(shops)} shop</b>:"]
    for s in shops:
        last = s.last_checked or "chưa check"
        status = s.last_status or "—"
        lines.append(
            f"• <b>{escape(s.shop_name or s.shop_handle)}</b> "
            f"(<code>{s.platform}:{s.shop_id}</code>)\n"
            f"   last: {escape(last)} · status: {escape(status[:40])}"
        )
    return "\n".join(lines)


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    db: Database = ctx.application.bot_data["db"]
    if not await _guard(update, settings):
        return

    chat_id = update.effective_chat.id
    shops = db.list_shops(chat_id)
    if not shops:
        await update.effective_message.reply_text(
            "Bạn chưa theo dõi shop nào.\n"
            "Gửi link Shopee vào chat (vd: https://shopee.vn/shop/12345678) "
            "để nhận nút theo dõi."
        )
        return

    await update.effective_message.reply_html(
        _format_list_text(shops),
        reply_markup=_build_list_keyboard(shops),
        disable_web_page_preview=True,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    db: Database = ctx.application.bot_data["db"]
    if not await _guard(update, settings):
        return

    chat_id = update.effective_chat.id
    my_count = len(db.list_shops(chat_id))
    total_keys = len(db.distinct_shop_keys())
    await update.effective_message.reply_html(
        f"<b>Shop Watcher</b>\n"
        f"• Poll mỗi: <code>{settings.poll_interval_seconds}s</code>\n"
        f"• Items/check: <code>{settings.items_per_check}</code>\n"
        f"• Shops bạn theo dõi: <code>{my_count}</code>\n"
        f"• Tổng unique shop trong DB: <code>{total_keys}</code>\n"
        f"• Chat ID của bạn: <code>{chat_id}</code>"
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    poller: Poller = ctx.application.bot_data["poller"]
    if not await _guard(update, settings):
        return

    msg = await update.effective_message.reply_text("⏳ Đang chạy lượt check…")
    stats = await poller.run_once()
    await msg.edit_text(
        f"✅ Xong: check {stats.shops_checked} shop, "
        f"{stats.new_items} sản phẩm mới, {stats.errors} lỗi."
    )


# ============================================================
# MESSAGE HANDLER: detect Shopee URL trong text bất kỳ
# ============================================================


async def on_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    if not await _guard(update, settings):
        return

    text = update.effective_message.text or ""
    urls = SHOPEE_URL_RE.findall(text)
    if not urls:
        return  # không phải link Shopee → bỏ qua, không reply

    url = urls[0]
    # Truncate cho callback_data (Telegram limit 64 bytes)
    # → lưu URL vào ctx.user_data và pass key
    pending = ctx.application.bot_data.setdefault("pending_adds", {})
    key = str(abs(hash(url)))[:12]
    pending[key] = url

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Theo dõi shop này", callback_data=f"{CB_ADD}{key}"),
            InlineKeyboardButton("❌ Bỏ qua", callback_data=CB_CANCEL),
        ]
    ])
    await update.effective_message.reply_html(
        f"🔗 Phát hiện link Shopee:\n<code>{escape(url)}</code>\n\nThêm vào theo dõi?",
        reply_markup=kb,
        disable_web_page_preview=True,
    )


# ============================================================
# CALLBACK QUERY (nút bấm)
# ============================================================


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    db: Database = ctx.application.bot_data["db"]

    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()  # tắt loading spinner trên nút

    chat = q.message.chat if q.message else None
    if not chat or not _is_allowed(settings, chat.id):
        try:
            await q.edit_message_text("⛔ Không có quyền.")
        except Exception:  # noqa: BLE001
            pass
        return

    data = q.data
    chat_id = chat.id

    # Cancel
    if data == CB_CANCEL:
        try:
            await q.edit_message_text("❌ Đã huỷ.")
        except Exception:  # noqa: BLE001
            pass
        return

    # Pagination /list
    if data.startswith("page:"):
        page = int(data.split(":", 1)[1])
        shops = db.list_shops(chat_id)
        try:
            await q.edit_message_text(
                _format_list_text(shops),
                parse_mode=ParseMode.HTML,
                reply_markup=_build_list_keyboard(shops, page=page),
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("edit page fail: %s", exc)
        return

    # Add từ URL detect
    if data.startswith(CB_ADD):
        key = data[len(CB_ADD):]
        pending = ctx.application.bot_data.get("pending_adds", {})
        url = pending.pop(key, None)
        if not url:
            await q.edit_message_text("⚠️ Phiên đã hết hạn, gửi lại link nhé.")
            return
        await q.edit_message_text(f"⏳ Đang resolve <code>{escape(url)}</code> …", parse_mode=ParseMode.HTML)
        _, text = await _do_add_shop(settings, db, chat_id, "shopee", url)
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    # Remove (yêu cầu confirm)
    if data.startswith(CB_RM) and not data.startswith(CB_RM_CONFIRM):
        shop_db_id = data[len(CB_RM):]
        shops = {str(s.id): s for s in db.list_shops(chat_id)}
        s = shops.get(shop_db_id)
        if not s:
            await q.edit_message_text("⚠️ Shop không còn trong danh sách.")
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Xác nhận bỏ", callback_data=f"{CB_RM_CONFIRM}{shop_db_id}"
                ),
                InlineKeyboardButton("↩️ Huỷ", callback_data="back_to_list"),
            ]
        ])
        await q.edit_message_text(
            f"🗑 Bỏ theo dõi <b>{escape(s.shop_name or s.shop_handle)}</b>?\n"
            f"<code>{s.platform}:{s.shop_id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    # Confirm remove
    if data.startswith(CB_RM_CONFIRM):
        shop_db_id = data[len(CB_RM_CONFIRM):]
        shops = {str(s.id): s for s in db.list_shops(chat_id)}
        s = shops.get(shop_db_id)
        if not s:
            await q.edit_message_text("⚠️ Shop đã bị bỏ trước đó.")
            return
        removed = db.remove_shop(chat_id, s.shop_id)
        if removed:
            await q.edit_message_text(
                f"🗑 Đã bỏ theo dõi: <b>{escape(removed.shop_name or removed.shop_handle)}</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await q.edit_message_text("❌ Bỏ thất bại (không tìm thấy).")
        return

    # Back to list
    if data == "back_to_list":
        shops = db.list_shops(chat_id)
        if not shops:
            await q.edit_message_text("Danh sách rỗng.")
            return
        await q.edit_message_text(
            _format_list_text(shops),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_list_keyboard(shops),
            disable_web_page_preview=True,
        )
        return

    log.warning("Unknown callback_data: %s", data)


# ============================================================
# ERROR / UNKNOWN
# ============================================================


async def on_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Không hiểu lệnh. Gõ /help để xem hướng dẫn."
    )


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Telegram handler error", exc_info=ctx.error)


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("rm", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("ls", cmd_list))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(on_callback))

    # Detect link Shopee trong text bất kỳ (KHÔNG match command vì command bắt đầu bằng '/')
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message)
    )

    # Unknown command
    app.add_handler(MessageHandler(filters.COMMAND, on_unknown))
    app.add_error_handler(on_error)
