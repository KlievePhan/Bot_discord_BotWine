# HƯỚNG DẪN TRIỂN KHAI BOT DISCORD PHÁT NHẠC YOUTUBE TRÊN MÁY CÁ NHÂN (LOCALHOST)

Chạy bot trực tiếp trên máy tính cá nhân (Windows/macOS) là phương án **hoàn toàn miễn phí (0đ)** và có một lợi thế cực lớn: sử dụng **IP mạng gia đình (Residential IP)** nên hầu như không bao giờ bị YouTube chặn hay bắt xác minh Robot như khi chạy trên VPS.

---

## Bước 1: Cài đặt Công cụ Hệ thống (Chỉ làm 1 lần)

Để bot có thể tải và giải mã âm thanh từ YouTube trên máy tính của bạn, máy cần có **Python** và **FFmpeg**.

### 1.1. Trên Windows (Khuyên dùng lệnh Winget cho nhanh)
Mở **PowerShell** hoặc **Command Prompt (CMD)** và chạy 2 lệnh sau:
```powershell
# 1. Cài đặt Python (nếu máy chưa có)
winget install Python.Python.3.11

# 2. Cài đặt FFmpeg (tự động thêm vào biến môi trường PATH)
winget install Gyan.FFmpeg
```
*(Lưu ý: Sau khi cài xong, hãy **tắt cửa sổ Terminal đi và mở lại** để hệ thống nhận diện lệnh `ffmpeg`).*

### 1.2. Trên macOS
Mở **Terminal** và cài đặt qua Homebrew:
```bash
brew install python ffmpeg
```

### 1.3. Kiểm tra cài đặt
Gõ thử lệnh sau vào Terminal, nếu hiện ra thông tin phiên bản là thành công:
```bash
python --version
ffmpeg -version
```

---

## Bước 2: Khởi tạo Thư mục Dự án & Môi trường ảo

Mở Terminal tại ổ đĩa bạn muốn lưu bot (ví dụ Desktop hoặc ổ D) và chạy lần lượt các lệnh:

```bash
# 1. Tạo thư mục dự án và di chuyển vào trong
mkdir discord-music-local
cd discord-music-local

# 2. Tạo môi trường ảo Python (giúp không làm rác máy tính)
python -m venv venv

# 3. Kích hoạt môi trường ảo:
# - Nếu dùng Windows (CMD):
venv\Scripts\activate
# - Nếu dùng Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# - Nếu dùng macOS/Linux:
source venv/bin/activate

# 4. Cài đặt các thư viện cần thiết (Có PyNaCl để phát Voice)
pip install -U discord.py[voice] yt-dlp python-dotenv
```

---

## Bước 3: Thiết lập Cấu hình và Mã nguồn Hoàn chỉnh

Trong thư mục `discord-music-local`, bạn tạo 2 file dưới đây:

### 3.1. File `.env` (Chứa Token bảo mật)
Tạo file tên là `.env` và dán Token lấy từ Discord Developer Portal vào:
```env
DISCORD_TOKEN=dan_token_bot_cua_ban_vao_day
```

### 3.2. File `bot.py` (Tích hợp sẵn Hàng đợi Queue & Tìm kiếm từ khóa)
Tạo file `bot.py` và dán đoạn code hoàn chỉnh sau vào. Bản code này hỗ trợ cả dán **Link YouTube** lẫn gõ **Tên bài hát**:

```python
import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Cấu hình quyền (Intents)
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Cấu hình yt-dlp tối ưu tốc độ
YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch', # Cho phép tìm kiếm nếu người dùng không nhập link
    'source_address': '0.0.0.0',
}

# Cấu hình FFmpeg tự kết nối lại nếu rớt gói tin mạng
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Hệ thống lưu trữ Hàng đợi (Queue) theo từng Server (Guild ID)
queues = {}

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

async def play_next(ctx):
    queue = get_queue(ctx.guild.id)
    if len(queue) > 0 and ctx.voice_client:
        song = queue.pop(0)
        source = discord.FFmpegOpusAudio(song['url'], **FFMPEG_OPTIONS)
        
        # Khi phát xong bài hiện tại, tự động gọi lại play_next
        ctx.voice_client.play(
            source, 
            after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        )
        await ctx.send(f"🎶 Đang phát: **{song['title']}**")
    else:
        await ctx.send("✅ Đã phát hết danh sách nhạc trong hàng đợi.")

@bot.event
async def on_ready():
    print(f'=========================================')
    print(f'🚀 Bot đã Online tại máy cá nhân: {bot.user}')
    print(f'=========================================')

@bot.command(name='play', aliases=['p'], help='Phát nhạc từ Link YouTube hoặc Tên bài hát')
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        return await ctx.send("⚠️ Bạn cần vào một kênh thoại (Voice Channel) trước!")

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    async with ctx.typing():
        try:
            loop = asyncio.get_event_loop()
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                if 'entries' in info:
                    info = info['entries'][0]
                
                song = {
                    'url': info['url'],
                    'title': info.get('title', 'Không rõ tiêu đề')
                }

            queue = get_queue(ctx.guild.id)
            
            # Nếu bot đang phát nhạc thì thêm vào hàng đợi
            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                queue.append(song)
                await ctx.send(f"📝 Đã thêm vào hàng đợi (#{len(queue)}): **{song['title']}**")
            else:
                queue.append(song)
                await play_next(ctx)

        except Exception as e:
            await ctx.send(f"❌ Lỗi khi tải âm thanh: `{str(e)}`")

@bot.command(name='skip', aliases=['s'], help='Bỏ qua bài hát hiện tại')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop() # Khi stop, hàm after trong play() sẽ tự kích hoạt play_next
        await ctx.send("⏭️ Đã bỏ qua bài hát hiện tại.")
    else:
        await ctx.send("⚠️ Không có bài hát nào đang phát.")

@bot.command(name='pause', help='Tạm dừng nhạc')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Đã tạm dừng.")

@bot.command(name='resume', aliases=['r'], help='Tiếp tục phát nhạc')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Tiếp tục phát nhạc.")

@bot.command(name='queue', aliases=['q'], help='Xem danh sách hàng đợi')
async def show_queue(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue:
        return await ctx.send("📭 Hàng đợi hiện đang trống.")
    
    msg = "**📋 Danh sách bài hát đang chờ:**\n"
    for idx, song in enumerate(queue, start=1):
        msg += f"`{idx}.` {song['title']}\n"
    await ctx.send(msg)

@bot.command(name='stop', aliases=['leave'], help='Xóa hàng đợi và rời phòng')
async def stop(ctx):
    if ctx.voice_client:
        queues[ctx.guild.id] = []
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Đã xóa hàng đợi và rời kênh thoại.")

bot.run(TOKEN)
```

---

## Bước 4: Khởi chạy & Tạo nút bật nhanh (1-Click)

### 4.1. Chạy thử nghiệm trong Terminal
Đảm bảo bạn đang ở trong thư mục `discord-music-local` và đã kích hoạt `venv`, gõ:
```bash
python bot.py
```
Vào kênh Discord của bạn, tham gia một kênh Voice và gõ thử:
* `!play https://www.youtube.com/watch?v=...` (hoặc `!p tên bài hát bất kỳ`)
* `!q` để xem danh sách chờ.
* `!s` để qua bài.
* `!stop` để cho bot nghỉ.

### 4.2. Tạo file chạy nhanh trên Windows (`start_bot.bat`)
Để mỗi lần mở máy tính không phải gõ lại lệnh Terminal, bạn hãy tạo một file tên là `start_bot.bat` nằm cùng thư mục với `bot.py`, bên trong dán nội dung sau:

```bat
@echo off
title Discord Music Bot - Localhost
echo Dang khoi dong Music Bot...
call venv\Scripts\activate
python bot.py
pause
```
Từ giờ, mỗi khi muốn bật bot để nghe nhạc cùng bạn bè, bạn chỉ cần **nháy đúp chuột vào file `start_bot.bat`** là xong!

---

## Bước 5: Xử lý các lỗi thường gặp trên Localhost

1. **Lỗi `discord.errors.ClientException: ffmpeg was not found.`:**
   * *Nguyên nhân:* Bạn vừa cài FFmpeg nhưng chưa khởi động lại cửa sổ Terminal/VS Code, hoặc FFmpeg chưa nằm trong `PATH`.
   * *Khắc phục:* Tắt hoàn toàn Terminal/VS Code đi bật lại. Nếu vẫn bị, tải file `ffmpeg.exe` bỏ trực tiếp vào cùng thư mục chứa file `bot.py`.
2. **Lỗi `PyNaCl is not installed`:**
   * *Nguyên nhân:* Thiếu thư viện mã hóa âm thanh của Discord.
   * *Khắc phục:* Chạy lệnh `pip install PyNaCl` trong môi trường ảo `venv`.
3. **Nhạc phát được vài tháng bỗng nhiên báo lỗi không lấy được link:**
   * *Nguyên nhân:* YouTube vừa cập nhật thuật toán mới.
   * *Khắc phục:* Bạn chỉ cần mở Terminal và cập nhật `yt-dlp` lên bản mới nhất bằng lệnh:
     ```bash
     pip install -U yt-dlp
     ```