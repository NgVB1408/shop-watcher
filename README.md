# Shop Watcher

Bot Telegram **theo dõi shop Shopee**: mỗi khi shop up sản phẩm mới thì báo về Telegram liền.

- Poll định kỳ qua public API Shopee, lưu SQLite, restart không mất dữ liệu.
- Adapter pattern: dễ mở rộng sang Lazada / TikTok Shop sau này.
- Allowlist `chat_id` chống người lạ spam.
- Retry + rate-limit handling, bounded recursion an toàn.

## Kiến trúc

```
shop_watcher/
├── config.py          # load .env, logging
├── db.py              # SQLite (shops, seen_items) — thread-safe, WAL
├── scrapers/
│   ├── base.py        # ShopScraper interface + Product/ShopInfo dataclass
│   └── shopee.py      # httpx + tenacity retry, API v4
├── notifier.py        # format message + send_photo/send_message với fallback
├── poller.py          # iterate distinct shops, diff item_ids, notify
├── bot.py             # Telegram CommandHandlers
└── main.py            # ApplicationBuilder + JobQueue scheduler
```

## Cài đặt nhanh (Windows)

1. Lấy bot token từ [@BotFather](https://t.me/BotFather).
2. Trong thư mục này:
   ```cmd
   setup.bat
   ```
   Script sẽ tạo `.venv`, cài requirements, copy `.env.example` → `.env`.
3. Mở `.env`, điền `TELEGRAM_BOT_TOKEN=...`.
4. Khởi chạy:
   ```cmd
   run.bat
   ```
5. Trong Telegram, gửi `/start` cho bot. Log sẽ in `chat_id` của bạn — copy vào
   `TELEGRAM_ALLOWED_CHAT_IDS` trong `.env` rồi restart để chỉ mình bạn dùng được.

## Cài đặt thủ công (mọi nền tảng)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# hoặc:  source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env       # rồi điền token
python run.py
```

## Commands Telegram

| Lệnh | Tác dụng |
|---|---|
| `/add <url\|username>` | Thêm shop Shopee để theo dõi |
| `/add shopee <url\|username>` | Chỉ định platform |
| `/list` (`/ls`) | Liệt kê shop đang theo dõi |
| `/remove <username\|shop_id>` (`/rm`) | Bỏ theo dõi 1 shop |
| `/check` | Chạy ngay 1 lượt poll (không chờ scheduler) |
| `/status` | Thông tin runtime + chat_id |
| `/help` | Hiển thị hướng dẫn |

Cú pháp `/add` hỗ trợ: `shop_username`, `@shop_username`,
`https://shopee.vn/shop_username`, `https://shopee.vn/shop/12345678`,
`https://shopee.vn/{slug}-i.{shopid}.{itemid}`, hoặc shop_id thuần.

## Cấu hình `.env`

| Biến | Mặc định | Mô tả |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Bắt buộc**. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | rỗng | Danh sách `chat_id` cho phép (CSV). Rỗng = mở cho tất cả (KHÔNG khuyến nghị). |
| `POLL_INTERVAL_SECONDS` | `300` | Khoảng poll. Tối thiểu 60s để tránh rate-limit Shopee. |
| `ITEMS_PER_CHECK` | `30` | Số sản phẩm mới nhất lấy mỗi lần. Tăng nếu shop bạn theo dõi up nhiều mỗi lần. |
| `DB_PATH` | `data/shop_watcher.db` | Đường dẫn SQLite. |
| `LOG_LEVEL` | `INFO` | `DEBUG` để xem từng request. |
| `HTTP_PROXY` | rỗng | Ví dụ `http://127.0.0.1:1080`. Dùng khi IP của bạn bị Shopee block. |
| `SHOPEE_COOKIE` | rỗng | Cookie string lấy từ browser thật (xem mục **Xử lý Shopee anti-bot**). |
| `SHOPEE_AUTO_COOKIE` | `0` | Bật để tự refresh cookies bằng Playwright khi bị 403. Chỉ work khi IP sạch. |

## Xử lý Shopee anti-bot (error `90309999`)

Shopee chặn rất gắt các IP residential VN gọi vào public API `/api/v4/...`.
Bot dùng `curl_cffi` (impersonate Chrome TLS fingerprint) — đây là HTTP client
chống bot tốt nhất hiện tại — nhưng nếu IP đã bị flag, vẫn nhận `403 / error
90309999`. Có 3 cách xử lý theo thứ tự ưu tiên:

### 1. Paste cookies từ browser thật (khuyến nghị, work nhất)

```cmd
.venv\Scripts\python.exe -m shop_watcher.tools.shopee_login
```

Script sẽ mở Chromium thật. Bạn login Shopee, browse 1 trang shop bất kỳ rồi
đóng browser. Cookie string sẽ được in ra + ghi vào `data/shopee_cookie.txt`.
Paste vào `.env`:

```
SHOPEE_COOKIE=SPC_F=...; SPC_CLIENTID=...; SPC_R_T_ID=...; ...
```

Cookies Shopee sống khoảng vài ngày → vài tuần. Khi bot bắt đầu báo lỗi 403
liên tục, chạy lại tool để refresh.

### 2. Dùng HTTP proxy có IP sạch

```
HTTP_PROXY=http://user:pass@proxy.example.com:8080
```

Có thể dùng proxy datacenter (chậm), residential (ổn định), hoặc tunnel qua
VPS bạn có sẵn (SSH dynamic forward `ssh -D 1080 user@vps` → `HTTP_PROXY=
socks5://127.0.0.1:1080`).

### 3. Chạy bot luôn trên VPS có IP sạch

Coolify VPS / AWS / Hetzner thường có IP datacenter không bị Shopee flag. Đẩy
project lên đó, chạy `systemd` (xem dưới), không cần cookie/proxy.

## Cơ chế hoạt động

1. Khi `/add`, bot resolve username → `shop_id` ổn định qua `shop/get_shop_detail`.
2. Scheduler chạy `Poller.run_once()` mỗi `POLL_INTERVAL_SECONDS`.
3. Với mỗi `(platform, shop_id)` distinct trong DB:
   - Gọi `search_items?by=ctime&order=desc`
   - **Lần đầu**: chỉ lưu `seen_items` làm baseline, **không** gửi noti.
   - **Lần sau**: item nào chưa có trong `seen_items` → gửi tới tất cả chat đang theo dõi shop đó.
4. Mỗi noti gồm: tên sản phẩm, giá, đã bán, link, ảnh (caption nếu Telegram chấp nhận image URL, fallback text).
5. Mỗi batch tối đa 10 noti / chat, phần thừa → 1 dòng summary "… và N sản phẩm khác".

## Vận hành lâu dài

- **Windows**: dùng Task Scheduler để chạy `run.bat` khi đăng nhập (Trigger: At log on, Action: Start a program).
- **Linux/server**: tạo systemd unit:
  ```ini
  [Unit]
  Description=Shop Watcher
  After=network-online.target

  [Service]
  WorkingDirectory=/opt/shop-watcher
  EnvironmentFile=/opt/shop-watcher/.env
  ExecStart=/opt/shop-watcher/.venv/bin/python run.py
  Restart=on-failure
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```
- **Docker**: chưa kèm Dockerfile vì user yêu cầu local-first. Có thể add sau nếu deploy.

## Bảo mật

- Token & allowlist nằm trong `.env`, đã được `.gitignore`.
- Allowlist chặn người lạ /add lung tung khiến poll quá tải.
- Không có SQL injection (parameterized everywhere).
- HTTP có timeout + retry exponential, không hang khi Shopee chậm.

## Giới hạn đã biết

- Shopee chặn IP nếu poll quá dày — đừng đặt `POLL_INTERVAL_SECONDS` < 60.
- API v4 không phải public docs chính thức; nếu Shopee đổi schema, scraper cần update.
- Một số shop bật chế độ "private" thì `get_shop_detail` không trả `shopid` → bot báo lỗi.
- Bot chạy long-polling Telegram (không cần expose port); nếu muốn webhook thì sửa `main.py` dùng `run_webhook`.
