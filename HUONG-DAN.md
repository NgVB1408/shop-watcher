# Hướng Dẫn Chạy Shop Watcher

Bot Telegram theo dõi shop Shopee, báo về Telegram mỗi khi shop up sản phẩm mới.

## 1. Yêu cầu trước khi chạy

- **Windows 10/11** (hoặc Linux/macOS, lệnh tương đương).
- **Python 3.10+** đã cài (kiểm tra: mở Terminal/CMD gõ `python --version`).
- **Telegram Bot Token** — xem mục 2.
- **IP của máy chạy** không bị Shopee block. Nếu bạn ở VN và bị `403/error 90309999`, xem mục 6 (Bypass anti-bot).

## 2. Tạo Bot Telegram & lấy Token

1. Mở Telegram, tìm **@BotFather** (chính chủ, có dấu tick xanh).
2. Gõ `/newbot` → đặt tên hiển thị (ví dụ "Shop Watcher của tôi") → đặt username phải kết thúc bằng `bot` (ví dụ `my_shop_watcher_bot`).
3. BotFather trả về **token** dạng `1234567890:ABCdefGHIjklMNOpqrSTUvwxyz`. Copy giữ kín.

## 3. Cài đặt project

### Cách 1 — Tự động (khuyến nghị cho Windows)

```cmd
cd path\to\shop-watcher
setup.bat
```

Script sẽ:
- Tạo môi trường ảo `.venv`
- Cài tất cả dependencies (`python-telegram-bot`, `curl_cffi`, `playwright`, …)
- Cài Chromium cho Playwright (dùng cho tool lấy cookie nếu cần)
- Copy `.env.example` → `.env` để bạn điền config

### Cách 2 — Thủ công

```cmd
cd path\to\shop-watcher
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
```

Trên Linux/macOS thay `.venv\Scripts\` thành `.venv/bin/`.

## 4. Cấu hình `.env`

Mở file `.env` vừa tạo bằng Notepad (hoặc bất kỳ editor nào) và điền:

```
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxyz
TELEGRAM_ALLOWED_CHAT_IDS=
POLL_INTERVAL_SECONDS=300
ITEMS_PER_CHECK=30
DB_PATH=data/shop_watcher.db
LOG_LEVEL=INFO
HTTP_PROXY=
SHOPEE_COOKIE=
SHOPEE_AUTO_COOKIE=0
```

Lúc đầu chỉ bắt buộc `TELEGRAM_BOT_TOKEN`. Các trường khác chỉ điền nếu cần.

## 5. Khởi chạy bot

```cmd
run.bat
```

Hoặc thủ công:

```cmd
.venv\Scripts\python.exe run.py
```

Bạn sẽ thấy log:

```
[INFO] Shop Watcher khởi động · poll=300s · items=30 · db=...\shop_watcher.db
[INFO] DB ready: ...\data\shop_watcher.db
[INFO] Scheduler đã chạy, interval=300s
```

Bot đang chạy → đừng đóng cửa sổ Terminal/CMD.

## 6. Sử dụng bot trên Telegram

Mở Telegram, tìm bot bạn vừa tạo (theo username `@my_shop_watcher_bot`).

### 6.1. Bước đầu — set chat_id của bạn vào allowlist

Gõ `/start`. Bot trả về help và in `chat_id` của bạn (ví dụ `123456789`).

**Quan trọng**: nếu bạn để `TELEGRAM_ALLOWED_CHAT_IDS=` rỗng trong `.env`, **ai biết tên bot cũng add được shop**. Để an toàn:

1. Mở `.env`, thêm chat_id vào:
   ```
   TELEGRAM_ALLOWED_CHAT_IDS=123456789
   ```
   (nhiều chat_id cách nhau bằng dấu phẩy)
2. Tắt bot (Ctrl-C trong CMD), chạy lại `run.bat`.

### 6.2. Thêm shop để theo dõi

Trên Telegram, gõ một trong các dạng sau:

```
/add https://shopee.vn/shop/88201679
/add https://shopee.vn/apple_flagship_store
/add apple_flagship_store
/add @apple_flagship_store
/add 88201679
```

Bot sẽ trả:
- ✅ Đã thêm → shop được lưu vào DB.
- ❌ Lỗi (Shopee chặn / shop không tồn tại) → xem mục 8 (Troubleshooting).

**Lưu ý**: lượt poll đầu tiên (sau ~10 giây) chỉ tạo *baseline*, **không** gửi noti cho 30 sản phẩm cũ. Từ lượt thứ 2 trở đi, mọi sản phẩm mới shop up lên sẽ được báo ngay.

### 6.3. Các lệnh khác

```
/list             — danh sách shop đang theo dõi
/remove <handle>  — bỏ theo dõi 1 shop  (vd: /remove apple_flagship_store)
/check            — chạy ngay 1 lượt poll, không chờ scheduler
/status           — xem cấu hình + số shop
/help             — danh sách commands
```

### 6.4. Nhận thông báo

Khi shop up sản phẩm mới, bot tự động gửi vào chat của bạn:

```
🆕 Sản phẩm mới từ Apple Flagship Store
📦 iPhone 15 Pro Max 256GB
💰 28.990.000đ · đã bán 12 · còn 5
🔗 Xem trên Shopee
```

Có ảnh sản phẩm kèm theo (nếu Shopee public ảnh).

## 7. Bypass Shopee anti-bot (nếu bot báo lỗi `403/90309999`)

Nếu khi `/add` bot trả `❌ Shopee chặn request (403)`, có nghĩa IP của bạn bị Shopee đánh dấu là bot. Đây là vấn đề phổ biến với IP residential VN.

**Cách 1 — Paste cookie từ browser thật (khuyến nghị, ổn định nhất)**:

```cmd
.venv\Scripts\python.exe -m shop_watcher.tools.shopee_login
```

Một cửa sổ Chromium sẽ mở. Bạn:
1. Login Shopee bằng tài khoản của mình.
2. Browse 1 trang shop bất kỳ (để Shopee set đủ cookie).
3. **Đóng cửa sổ Chromium** (nút X).
4. Script in cookie string ra Terminal + ghi vào `data/shopee_cookie.txt`.
5. Mở `.env`, paste cookie string vào dòng `SHOPEE_COOKIE=`:
   ```
   SHOPEE_COOKIE=SPC_F=abc123; SPC_CLIENTID=xyz...; SPC_R_T_ID=...; ...
   ```
6. Restart bot (`run.bat`).

Cookie sống vài ngày → vài tuần. Khi bot báo 403 lại, chạy lại bước trên.

**Cách 2 — HTTP proxy IP sạch**:

```
HTTP_PROXY=socks5://127.0.0.1:1080
```

Có thể tunnel qua VPS bằng SSH: `ssh -D 1080 user@your-vps` rồi giữ session SSH mở.

**Cách 3 — Chạy bot trên VPS**:

Deploy lên VPS (Hetzner, AWS, Coolify, …). IP datacenter thường không bị Shopee chặn. Không cần cookie hay proxy.

## 8. Troubleshooting

| Triệu chứng | Nguyên nhân & xử lý |
|---|---|
| Bot không reply gì cả | Sai token, hoặc Python chưa chạy. Kiểm tra log trong `logs/shop_watcher.log`. |
| `❌ Shopee chặn request (403)` | IP bị block. Xem mục 7. |
| `❌ Shop không tồn tại (404)` | URL/username sai. Thử mở link đó trên browser, copy lại đúng. |
| `❌ Không tìm thấy shop` | Username không tồn tại trên Shopee. Dùng URL `/shop/{id}` thay vì username. |
| `Chat ID của bạn chưa được cấp phép` | `TELEGRAM_ALLOWED_CHAT_IDS` không khớp. Cập nhật `.env` và restart. |
| Bot báo sản phẩm cũ lúc mới add | Sai logic — lượt đầu chỉ baseline, KHÔNG gửi noti. Nếu thấy noti cho item cũ là bug, báo lại. |
| Quá nhiều noti mỗi check | Shop up hàng loạt. Bot tự giới hạn 10 noti/batch, phần còn lại gửi 1 dòng summary. |
| Bot crash, không tự restart | Dùng Task Scheduler (mục 9) để auto-restart. |

Xem log chi tiết tại `logs/shop_watcher.log` (rotate mặc định không bật, file sẽ to dần — cân nhắc xoá định kỳ).

## 9. Chạy bot lâu dài (auto-start, auto-restart)

### Windows — Task Scheduler

1. Mở **Task Scheduler** (Win + R → `taskschd.msc`).
2. **Create Task** → đặt tên `Shop Watcher`.
3. Tab **Triggers**: New → **At log on**.
4. Tab **Actions**: New → Program: `C:\Users\ngvan\workspaces\shop-watcher\run.bat`. Start in: `C:\Users\ngvan\workspaces\shop-watcher`.
5. Tab **Settings**: tick "If the task fails, restart every: 1 minute".
6. OK. Task sẽ tự chạy mỗi khi bạn đăng nhập Windows.

### Linux/macOS — systemd

Tạo `/etc/systemd/system/shop-watcher.service`:

```ini
[Unit]
Description=Shop Watcher Telegram Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/shop-watcher
EnvironmentFile=/opt/shop-watcher/.env
ExecStart=/opt/shop-watcher/.venv/bin/python run.py
Restart=on-failure
RestartSec=10
User=ubuntu

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now shop-watcher
sudo systemctl status shop-watcher
sudo journalctl -u shop-watcher -f      # xem log realtime
```

## 10. Cấu trúc thư mục

```
shop-watcher/
├── .env                    ← config (đừng commit)
├── .env.example            ← template config
├── README.md               ← mô tả tổng quan
├── HUONG-DAN.md            ← file này
├── requirements.txt        ← Python dependencies
├── setup.bat / run.bat     ← Windows launcher
├── run.py                  ← entrypoint
├── data/                   ← SQLite DB (đừng xoá → mất hết shop đã add)
├── logs/                   ← log file
└── shop_watcher/
    ├── main.py             ← khởi tạo bot + scheduler
    ├── bot.py              ← Telegram command handlers
    ├── config.py           ← đọc .env
    ├── db.py               ← SQLite layer
    ├── notifier.py         ← format & gửi message Telegram
    ├── poller.py           ← scheduler polling logic
    ├── scrapers/
    │   ├── base.py         ← interface ShopScraper
    │   └── shopee.py       ← Shopee API client
    └── tools/
        └── shopee_login.py ← tool lấy cookie Shopee
```

## 11. Cập nhật bot

Khi sửa code:

```cmd
:: Tắt bot (Ctrl-C trong CMD đang chạy)
:: Sửa code
run.bat        :: chạy lại
```

Khi cập nhật dependencies:

```cmd
.venv\Scripts\python.exe -m pip install -r requirements.txt --upgrade
```

DB tự migrate tương thích — không mất dữ liệu khi update code.

## 12. Bảo mật

- **Đừng share file `.env`** — chứa token & cookie. Đã được `.gitignore`.
- **TELEGRAM_ALLOWED_CHAT_IDS** = best practice: chỉ chat_id của chính bạn được dùng bot.
- **SHOPEE_COOKIE** = session login Shopee của bạn. Ai có cookie này = login được Shopee bằng account bạn. Đừng paste lên Discord/Telegram public.
- File DB `data/shop_watcher.db` chỉ chứa shop_id + item_id (không nhạy cảm).

---

Có thắc mắc thì gõ `/help` trên Telegram để xem nhanh hoặc đọc `README.md`.
