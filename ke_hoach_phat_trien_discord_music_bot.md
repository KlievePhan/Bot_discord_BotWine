# KẾ HOẠCH PHÁT TRIỂN BOT DISCORD PHÁT NHẠC YOUTUBE (MUSIC BOT)

Tài liệu này cung cấp lộ trình chi tiết, giải pháp kỹ thuật (tránh lỗi bị YouTube chặn IP/Stream) và các gợi ý nâng cấp để xây dựng một Bot phát âm thanh từ link YouTube ổn định nhất.

---

## 1. Phân tích Kỹ thuật & Lựa chọn Công nghệ (Tech Stack)

Việc lấy luồng âm thanh (audio stream) từ YouTube hiện tại khá nhạy cảm vì YouTube thường xuyên cập nhật thuật toán chặn bot. Do đó, việc chọn đúng thư viện là yếu tố quyết định.

### Phương án A: Node.js (Khuyên dùng cho Bot quy mô vừa & lớn)
* **Thư viện lõi:** `discord.js` (v14) + `@discordjs/voice`
* **Framework xử lý nhạc:** **`discord-player`** (kèm extractor `discord-player-youtubei`) hoặc **`DisTube`** (kèm `@distube/yt-dlp`).
  * *Lý do:* Các thư viện cũ như `ytdl-core` thường xuyên bị lỗi `403 Forbidden`. Sử dụng `youtubei` hoặc `yt-dlp` giúp giả lập client chuẩn, hạn chế tối đa việc bị đứt kết nối giữa chừng.
* **Công cụ hệ thống bắt buộc:** **FFmpeg** (để giải mã và chuyển đổi âm thanh sang chuẩn Opus của Discord).

### Phương án B: Python (Khuyên dùng vì `yt-dlp` cực kỳ mạnh mẽ trên Python)
* **Thư viện lõi:** `discord.py` + `PyNaCl` (thư viện xử lý kết nối Voice).
* **Thư viện trích xuất YouTube:** **`yt-dlp`** (Bản nâng cấp hoàn hảo của `youtube-dl`, được cập nhật hàng tuần để vượt rào cản của YouTube).
* **Công cụ hệ thống bắt buộc:** **FFmpeg**.

---

## 2. Chuẩn bị Môi trường & Cấp quyền

### 2.1. Cấp quyền bổ sung trên Discord Developer Portal
Để bot vào được kênh thoại (Voice Channel) và phát nhạc, bạn cần đảm bảo:
1. **Gateway Intents:** Bật bắt buộc `GuildVoiceStates` (để bot biết người dùng đang ở kênh thoại nào) cùng với `Guilds`, `GuildMessages`, `MessageContent`.
2. **Bot Permissions (OAuth2):** Cấp thêm quyền `Connect` (Kết nối) và `Speak` (Nói).

### 2.2. Cài đặt FFmpeg trên máy
Bot không thể phát nhạc nếu thiếu **FFmpeg**:
* **Windows:** Tải qua Winget bằng lệnh `winget install Gyan.FFmpeg` trên Terminal (hoặc tải file zip từ trang chủ FFmpeg và thêm vào biến môi trường `PATH`).
* **Linux (Ubuntu/Debian - khi đưa lên VPS):** `sudo apt update && sudo apt install ffmpeg -y`
* **MacOS:** `brew install ffmpeg`

---

## 3. Lộ trình Lập trình Chi tiết (4 Giai đoạn)

### Giai đoạn 1: Kết nối Kênh thoại (Voice Connection)
* Kiểm tra xem người gõ lệnh có đang ở trong một Voice Channel hay không. Nếu không, báo lỗi yêu cầu họ vào phòng trước.
* Cho bot join vào đúng Voice Channel của người dùng đó.

### Giai đoạn 2: Trích xuất & Phát luồng âm thanh (Core Feature)
* Nhận URL YouTube từ tin nhắn hoặc Slash Command (`/play <url>`).
* Kiểm tra tính hợp lệ của link (Link video đơn lẻ, link rút gọn `youtu.be`, hoặc link Playlist).
* Dùng `yt-dlp` hoặc `discord-player` lấy **Direct Audio Stream URL** (chỉ lấy luồng âm thanh `bestaudio`, bỏ qua luồng hình ảnh để tiết kiệm băng thông và RAM).
* Đẩy luồng âm thanh qua **FFmpeg** và phát lên kênh thoại.

### Giai đoạn 3: Xây dựng Hệ thống Hàng đợi (Queue System)
Nếu đang phát bài A mà người dùng gửi thêm link bài B, bot không được ngắt bài A mà phải đưa bài B vào danh sách chờ:
* Thiết lập cấu trúc dữ liệu mảng (`Queue`) riêng cho từng Server (`Guild ID`).
* Bắt sự kiện khi bài hát hiện tại kết thúc (`idle` / `finish`) để tự động lấy bài tiếp theo trong mảng ra phát.
* Các lệnh điều khiển cơ bản:
  * `/play <link hoặc từ khóa>`: Phát hoặc thêm vào hàng đợi.
  * `/skip`: Bỏ qua bài hiện tại.
  * `/pause` & `/resume`: Tạm dừng và tiếp tục.
  * `/stop` / `/leave`: Xóa hàng đợi và rời phòng.
  * `/queue`: Xem danh sách các bài đang chờ.

### Giai đoạn 4: Tối ưu hóa & Xử lý lỗi (Error Handling)
* **Tự động rời phòng (Auto-Leave):** Nếu phòng thoại không còn ai (hoặc bot phát hết nhạc sau 3-5 phút), tự động ngắt kết nối để giải phóng RAM cho server.
* **Bắt lỗi Video:** Xử lý trường hợp video bị giới hạn độ tuổi (Age-restricted), video riêng tư, hoặc livestream mà không làm sập (crash) toàn bộ bot.

---

## 4. Mã nguồn Khởi đầu Nhanh (Quickstart Code)

Dưới đây là bản mẫu dùng **Python (`discord.py` + `yt-dlp`)** — phương án gọn nhẹ và ít bị YouTube chặn nhất hiện nay:

### Cài đặt thư viện:
```bash
pip install discord.py[voice] yt-dlp python-dotenv
```

### Cấu trúc code mẫu (`bot.py`):
```python
import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Cấu hình Intents (Bắt buộc có voice_states)
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Cấu hình yt-dlp tối ưu cho lấy Audio
YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0', # Tránh lỗi IPv6 bị chặn
}

# Cấu hình FFmpeg tự động kết nối lại nếu mạng chập chờn
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn', # Không xử lý video
}

@bot.event
async def on_ready():
    print(f'Music Bot đã sẵn sàng: {bot.user.name}')

@bot.command(name='play', help='Phát nhạc từ link YouTube')
async def play(ctx, *, url: str):
    # 1. Kiểm tra người dùng có trong phòng Voice không
    if not ctx.author.voice:
        return await ctx.send("Bạn cần vào một kênh thoại (Voice Channel) trước!")

    channel = ctx.author.voice.channel

    # 2. Kết nối hoặc di chuyển bot tới phòng của người dùng
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    async with ctx.typing():
        try:
            # 3. Trích xuất luồng âm thanh bằng yt-dlp
            loop = asyncio.get_event_loop()
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                if 'entries' in info:
                    info = info['entries'][0]
                stream_url = info['url']
                title = info.get('title', 'Không rõ tiêu đề')

            # 4. Phát âm thanh qua FFmpeg
            source = discord.FFmpegOpusAudio(stream_url, **FFMPEG_OPTIONS)
            if ctx.voice_client.is_playing():
                ctx.voice_client.stop()
                
            ctx.voice_client.play(source)
            await ctx.send(f"🎶 Đang phát: **{title}**")

        except Exception as e:
            await ctx.send(f"❌ Có lỗi xảy ra khi tải âm thanh: {str(e)}")

@bot.command(name='stop', help='Dừng nhạc và rời phòng')
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Đã dừng nhạc và rời kênh thoại.")

bot.run(TOKEN)
```

---

## 5. Gợi ý Tính năng Nâng cao (Suggestions để Bot xịn hơn)

Sau khi chạy thành công bản cơ bản ở trên, bạn nên nâng cấp theo các gợi ý sau để trải nghiệm người dùng chuyên nghiệp hơn:

### 5.1. Giao diện Điều khiển bằng Nút bấm (Interactive Buttons)
Thay vì bắt người dùng gõ `!pause`, `!skip`, hãy gửi một **Rich Embed** hiển thị:
* Ảnh Thumbnail của video YouTube.
* Thời lượng bài hát, tên kênh YouTube, người yêu cầu bài hát.
* Hàng nút bấm (`discord.ui.View`) bên dưới tin nhắn gồm: `[⏯️ Tạm dừng]` `[⏭️ Bài tiếp]` `[🔁 Lặp lại]` `[⏹️ Dừng]`.

### 5.2. Tìm kiếm bằng Từ khóa & Autocomplete
Người dùng thường lười ra YouTube copy link. Hãy cho phép họ gõ `/play Sơn Tùng MTP`, bot sẽ dùng `ytsearch:` của `yt-dlp` để tự lấy kết quả đầu tiên hoặc hiển thị danh sách 5 bài gợi ý ngay khi người dùng đang gõ lệnh (Slash Command Autocomplete).

### 5.3. Bộ lọc Âm thanh (Audio Filters)
Thêm các tham số vào `FFMPEG_OPTIONS` để tạo hiệu ứng âm thanh trực tiếp:
* **Bassboost:** Tăng âm trầm cho nhạc Remix/EDM (`-af bass=g=10`).
* **Nightcore:** Tăng tốc độ và cao độ (`-af asetrate=48000*1.25,aresample=48000`).
* **8D Audio:** Hiệu ứng âm thanh vòm xoay quanh tai nghe.

### 5.4. Giải pháp chống YouTube chặn IP khi đưa lên VPS/Cloud
Khi bạn chạy ở máy cá nhân (mạng nhà), YouTube hiếm khi chặn. Nhưng khi đưa lên VPS (DigitalOcean, AWS, Render...), dải IP của trung tâm dữ liệu rất dễ bị YouTube yêu cầu xác minh "Bạn không phải là robot":
* **Giải pháp 1 (Cookies):** Xuất file `cookies.txt` từ một tài khoản YouTube phụ của bạn và cấu hình vào `yt-dlp` (`'cookiefile': 'cookies.txt'`).
* **Giải pháp 2 (Lavalink - Chuẩn công nghiệp):** Nếu muốn làm bot phục vụ hàng chục/trăm server cùng lúc mà không bị giật lag, hãy chạy một máy chủ âm thanh độc lập bằng Java tên là **Lavalink** kèm plugin `youtube-source`. Toàn bộ các Music Bot lớn hiện nay đều dùng kiến trúc này.