# Deploy Shop Watcher lên Coolify VPS

VPS Coolify có IP datacenter — Shopee không block. Bot chạy 24/7 không cần
cookie hay proxy.

## Phương án 1 — Deploy qua git (khuyến nghị)

Coolify tự pull code từ GitHub repo mỗi khi bạn push.

### Bước 1. Tạo GitHub repo

```cmd
:: trên máy bạn, trong thư mục shop-watcher
git init
git add .
git commit -m "Initial: Shop Watcher Telegram bot"
```

Tạo repo trên GitHub (private nếu muốn), rồi:

```cmd
git remote add origin git@github.com:<your_user>/shop-watcher.git
git branch -M main
git push -u origin main
```

⚠️ **File `.env` đã được `.gitignore`** — không push token lên GitHub. Sẽ set
env vars trong Coolify UI.

### Bước 2. Tạo resource trên Coolify

1. Vào Coolify dashboard.
2. **Projects** → chọn project (vd: `adtk-suite`) hoặc tạo project mới
   `personal-bots`.
3. **+ New** → **Resource** → **Public Repository** (nếu repo public) hoặc
   **Private Repository (GitHub App)** (nếu private — phải connect GitHub app trước).
4. Paste URL git repo, branch `main`.
5. **Build Pack**: chọn **Dockerfile**.
6. **Dockerfile location**: `Dockerfile` (default).

### Bước 3. Cấu hình Environment Variables

Tab **Environment Variables** → thêm:

| Key | Value | Secret? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `8459423110:AAGkEK3l08GYkWWqJ-QztIrcfgF9zK8Sa1I` | ✅ |
| `TELEGRAM_ALLOWED_CHAT_IDS` | `5240277805` | ⬜ |
| `POLL_INTERVAL_SECONDS` | `300` | ⬜ |
| `ITEMS_PER_CHECK` | `30` | ⬜ |
| `LOG_LEVEL` | `INFO` | ⬜ |
| `DB_PATH` | `/app/data/shop_watcher.db` | ⬜ |

Đánh dấu Secret cho `TELEGRAM_BOT_TOKEN`.

### Bước 4. Persistent Storage

Tab **Storages** → thêm:

| Source path (trong container) | Type |
|---|---|
| `/app/data` | **Volume** (named) |
| `/app/logs` | **Volume** (named) |

Không cần mount host path — Coolify tự quản lý volume.

### Bước 5. Deploy

Click **Deploy**. Coolify sẽ:
1. Clone git repo
2. Build Docker image từ `Dockerfile`
3. Start container
4. Log realtime hiện tại tab **Logs**

Khi log thấy:

```
[INFO] Shop Watcher khởi động · poll=300s · items=30
[INFO] DB ready: /app/data/shop_watcher.db
[INFO] Scheduler đã chạy, interval=300s
```

→ Bot đã online. Gửi `/start` cho bot trên Telegram để test.

### Bước 6. Update code sau này

Mỗi lần `git push` lên branch `main`, Coolify auto-rebuild (nếu enable webhook
trong tab **Webhooks**). Hoặc click **Redeploy** thủ công.

---

## Phương án 2 — Deploy không cần git (manual SSH)

Nếu không muốn dùng git, SCP zip lên VPS rồi chạy docker-compose:

### Bước 1. SCP code lên VPS

```cmd
:: nén project (đã exclude .venv/data/logs)
:: dùng zip có sẵn ở C:\Users\ngvan\shop-watcher.zip

scp C:\Users\ngvan\shop-watcher.zip user@your-vps:/opt/
ssh user@your-vps
```

Trên VPS:

```bash
cd /opt
unzip shop-watcher.zip
cd shop-watcher
```

### Bước 2. Tạo `.env` trên VPS

```bash
nano .env
```

Paste:

```
TELEGRAM_BOT_TOKEN=8459423110:AAGkEK3l08GYkWWqJ-QztIrcfgF9zK8Sa1I
TELEGRAM_ALLOWED_CHAT_IDS=5240277805
POLL_INTERVAL_SECONDS=300
ITEMS_PER_CHECK=30
DB_PATH=/app/data/shop_watcher.db
LOG_LEVEL=INFO
```

`Ctrl-O` → Enter → `Ctrl-X`.

### Bước 3. Chạy

```bash
docker compose up -d --build
docker compose logs -f
```

Bot sẽ chạy nền. Khi nào muốn dừng:

```bash
docker compose down
```

Khi update code:

```bash
docker compose pull
docker compose up -d --build
```

---

## Phương án 3 — Coolify Docker Compose Empty

Nếu Coolify không kết nối git được:

1. Coolify → **+ New** → **Service** → **Docker Compose Empty**.
2. Paste nội dung `docker-compose.yml` vào tab **Configuration**.
3. **Environment Variables** như Bước 3 Phương án 1.
4. Coolify sẽ cần build context — copy file `Dockerfile` + `requirements-vps.txt`
   + thư mục `shop_watcher/` + `run.py` lên container build path. Cách này khó
   maintain hơn, khuyên dùng Phương án 1.

---

## Kiểm tra sau khi deploy

1. **Log trên Coolify**: phải thấy `Scheduler đã chạy, interval=300s`.
2. **Telegram**: gõ `/start` cho bot → bot reply HELP.
3. **Test thật**: gửi link Shopee bất kỳ vào chat, vd:
   ```
   https://shopee.vn/shop/104274078
   ```
   Bot sẽ hiện 2 nút: **✅ Theo dõi shop này** và **❌ Bỏ qua**.
4. Click ✅ → bot resolve shop, lưu vào DB.
5. Gõ `/list` → thấy shop vừa thêm + nút 🗑 để bỏ.
6. Đợi 5 phút → bot tự poll. Nếu shop có sản phẩm mới, Telegram nhận noti.
7. Gõ `/check` để force poll ngay không chờ scheduler.

## Quirks Coolify đã biết (rút từ kinh nghiệm)

- **Không thêm `labels:` block trong docker-compose.yml** — Coolify auto-inject
  của riêng nó.
- **Branch deploy**: default Coolify dùng branch trên config. Nếu push branch
  khác phải set lại.
- **Build args**: chỉ truyền qua tab Build Arguments, không qua docker-compose.yml.
- **Volume persistence**: nếu xoá resource → volume cũng có thể bị xoá. **BACKUP
  `/app/data/shop_watcher.db` định kỳ** bằng cách SCP về máy:
  ```bash
  docker compose cp shop-watcher:/app/data/shop_watcher.db ./backup.db
  ```

## Bảo mật token

Sau khi đã chia sẻ token với mình để setup:

- Nếu muốn rotate, vào BotFather → `/mybots` → chọn bot → **API Token** → **Revoke
  current token**. BotFather sẽ cấp token mới.
- Update env var `TELEGRAM_BOT_TOKEN` trên Coolify → click **Redeploy**.
- Bot dùng token mới, token cũ không còn auth được.

Khuyên rotate token sau khi setup xong.
