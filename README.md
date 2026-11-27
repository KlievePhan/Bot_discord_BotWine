# 🎶 Discord YouTube Music Bot (Python + `yt-dlp`)

Bot phát nhạc YouTube chất lượng cao trên Discord được triển khai đầy đủ theo lộ trình 4 giai đoạn + trọn bộ tính năng nâng cao:
- **Hybrid Commands**: Dùng được cả **Slash Commands** (`/play` có **Autocomplete** gợi ý bài hát khi đang gõ) và **Prefix Commands** (`!play`, `!p`, `!s`, `!q`...).
- **Rich Embed & Interactive Buttons**: Thẻ bài hát kèm ảnh Thumbnail, thanh tiến trình thời gian thực, các nút `[⏸️ Tạm dừng]` `[⏭️ Bài tiếp]` `[🔁 Chế độ lặp]` `[🔀 Trộn bài]` `[⏹️ Dừng]` và menu chọn **Audio Filter** trực tiếp.
- **Hỗ trợ Từ khóa, Link lẻ & Playlist YouTube**: Tải danh sách Playlist tốc độ cao, tự động làm mới Direct Audio Stream URL ngay trước lúc phát từng bài để không bao giờ hết hạn giữa chừng.
- **Bộ lọc âm thanh trực tiếp (Audio Filters)**: `Bassboost`, `Nightcore`, `Slowed + Reverb`, `8D Audio`, `Karaoke` (áp dụng trực tiếp ngay tại giây đang phát mà không cần phát lại từ đầu) + chỉnh âm lượng `1% - 200%`.
- **Tự động nhận `cookies.txt` & Node.js EJS Runtime**: Chống lỗi `403 Forbidden`, video giới hạn độ tuổi hoặc chặn IP trên VPS.
- **Auto-Leave thông minh**: Tự rời phòng khi không còn người nghe hoặc khi hết nhạc quá thời gian chờ để tiết kiệm RAM.

---

## 🚀 Cách chạy nhanh (Chỉ cần 2 bước)

### Bước 1: Kiểm tra quyền trên Discord Developer Portal
1. Truy cập [Discord Developer Portal](https://discord.com/developers/applications) -> chọn ứng dụng của bạn -> tab **Bot**:
   - Bật **Message Content Intent** trong mục *Privileged Gateway Intents* -> bấm **Save Changes**.
   - Sao chép **Token** của Bot.
2. Vào tab **OAuth2 -> URL Generator**:
   - Tích chọn Scopes: `bot` và `applications.commands`.
   - Tích chọn Bot Permissions: `View Channels`, `Send Messages`, `Embed Links`, `Connect`, `Speak`, `Use Voice Activity`.
   - Mở URL tạo được để mời Bot vào Server Discord của bạn.

### Bước 2: Điền Token & Khởi chạy
1. Mở file `.env` và thay thế giá trị `DISCORD_TOKEN`:
   ```env
   DISCORD_TOKEN=dan_token_bot_cua_ban_vao_day
   ```
2. Nhấp đúp vào file **`run.bat`** hoặc chạy lệnh trên Terminal:
   ```powershell
   .\.venv\Scripts\python.exe bot.py
   ```

---

## 🍪 Tùy chọn chống chặn IP / Video 18+ (`cookies.txt`)
Nếu đưa bot lên VPS hoặc cần mở các bài nhạc giới hạn độ tuổi:
1. Dùng tiện ích trình duyệt **Get cookies.txt LOCALLY** đăng nhập tài khoản YouTube phụ và xuất file định dạng Netscape.
2. Đặt file tên **`cookies.txt`** ngay trong thư mục `e:\bot_wine\`. Bot sẽ **tự động phát hiện và kích hoạt** mà không cần sửa code.

---

## 📋 Danh sách lệnh chính

Link YouTube Mix/Radio (`list=RD...`, kể cả `start_radio=1`) được nạp tối đa
50 bài không trùng trong một lần gọi `/play` hoặc `!p`. Đây là danh sách tại
thời điểm thêm, không tự bổ sung vô hạn. Playlist thông thường giới hạn 100 bài.

### Tự lấy GVS PO Token khi YouTube trả 403

Cần Node.js >= 22 và Git. Chạy một lần trong PowerShell tại thư mục dự án:

```powershell
.\setup_po_token.ps1
```

Khởi động lại bot. Bot tự khởi động PO Token HTTP provider trên `127.0.0.1:4416`
để giữ cache trong RAM; tự tắt provider do bot tạo khi đóng bot. Nếu không khởi
động được server, bot dùng script dự phòng trong `.local/`. Bot dùng client
`visionos` không đăng nhập cho video công khai; dự phòng `mweb` khi thiếu định dạng.
Chỉ nạp cookies và dùng `mweb` có đăng nhập khi YouTube yêu cầu xác nhận tuổi.
Không cần mở thêm server hoặc chép token thủ công.
Token/cache nằm trong `.local/cache/`, được bỏ qua bởi Git. PO Token không bảo đảm
khắc phục mọi lỗi chặn IP hoặc yêu cầu đăng nhập của YouTube.

| Lệnh Slash | Lệnh nhanh (Prefix) | Chức năng |
| :--- | :--- | :--- |
| `/play <từ khóa / URL>` | `!play`, `!p` | Phát nhạc hoặc thêm bài / Playlist vào hàng đợi |
| `/pause` / `/resume` | `!pause`, `!resume` | Tạm dừng / Tiếp tục phát |
| `/skip` | `!skip`, `!s` | Bỏ qua bài hát hiện tại |
| `/queue` | `!queue`, `!q` | Xem danh sách chờ (có phân trang) |
| `/nowplaying` | `!nowplaying`, `!np` | Hiện thẻ bài hát hiện tại + bảng nút điều khiển |
| `/volume <1-200>` | `!volume`, `!vol` | Chỉnh âm lượng trực tiếp (mặc định `80%`) |
| `/filter <preset>` | `!filter`, `!bassboost` | Bật bộ lọc `off`, `bassboost`, `nightcore`, `slowed`, `8d`, `karaoke` |
| `/loop [off\|track\|queue]` | `!loop` | Chuyển chế độ lặp lại |
| `/shuffle` | `!shuffle` | Xáo trộn ngẫu nhiên hàng đợi |
| `/remove <số thứ tự>` | `!remove`, `!rm` | Xóa 1 bài bất kỳ khỏi hàng đợi |
| `/clear` | `!clear` | Xóa sạch hàng đợi chờ |
| `/stop` | `!stop`, `!leave`, `!dc` | Xóa hàng đợi và rời phòng thoại |
| `/help` | `!help`, `!h` | Xem hướng dẫn lệnh trên Discord |
| *(Chỉ Prefix)* | `!sync` | Đồng bộ ngay lập tức Slash Commands vào Server hiện tại |
