from __future__ import annotations

import logging
import shutil
import subprocess
import sys

import discord
from discord.ext import commands

from config import (
    AUDIO_FILTERS,
    COMMAND_PREFIX,
    DISCORD_TOKEN,
    FFMPEG_EXECUTABLE,
    get_cookies_path,
)

# Cấu hình Logging rõ ràng ra màn hình Console (UTF-8)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("music_bot")


def verify_ffmpeg() -> tuple[bool, str]:
    """Kiểm tra FFmpeg có sẵn sàng hoạt động trước khi khởi chạy Bot."""
    try:
        out = subprocess.check_output(
            [FFMPEG_EXECUTABLE, "-version"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
        first_line = out.splitlines()[0] if out else FFMPEG_EXECUTABLE
        return True, first_line
    except Exception as exc:
        return False, str(exc)


class WineMusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True      # Bắt buộc để nhận diện kênh thoại
        intents.message_content = True   # Bắt buộc cho lệnh Prefix (!play, !p...)

        super().__init__(
            command_prefix=commands.when_mentioned_or(COMMAND_PREFIX),
            intents=intents,
            help_command=None,  # Sử dụng lệnh /help Rich Embed tùy chỉnh bên dưới
        )

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.music")
        log.info("Đã nạp module âm nhạc (cogs.music).")

        try:
            synced = await self.tree.sync()
            log.info("Đã đồng bộ %d Slash Commands lên toàn bộ Server.", len(synced))
        except Exception as exc:
            log.warning("Không thể đồng bộ Slash Commands tự động: %s", exc)

    async def on_ready(self) -> None:
        assert self.user is not None
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name=f"/play | {COMMAND_PREFIX}help",
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

        cookies = get_cookies_path()
        log.info("==========================================================")
        log.info("🎶 BOT ĐÃ SẴN SÀNG: %s (ID: %s)", self.user, self.user.id)
        log.info("📡 Đang tham gia: %d Server", len(self.guilds))
        log.info("🎞️ FFmpeg: %s", FFMPEG_EXECUTABLE)
        log.info("🟢 Node.js EJS Runtime: %s", "Đã bật" if shutil.which("node") else "Không tìm thấy")
        log.info("🍪 YouTube Cookies: %s", cookies if cookies else "Chưa đặt file cookies.txt (tùy chọn)")
        log.info("==========================================================")

    async def on_command_error(
        self, ctx: commands.Context[commands.Bot], error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"⚠️ Thiếu tham số `{error.param.name}`! Gõ `{COMMAND_PREFIX}help` để xem cách dùng chi tiết."
            )
            return
        if isinstance(error, commands.RangeError):
            await ctx.send(f"⚠️ Giá trị phải nằm trong khoảng từ `{error.minimum}` đến `{error.maximum}`.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"⚠️ Tham số không hợp lệ: {error}")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Bạn không có đủ quyền hạn để dùng lệnh này.")
            return

        log.exception("Lỗi không mong muốn khi xử lý lệnh %s:", ctx.command, exc_info=error)
        await ctx.send(f"❌ Đã xảy ra lỗi khi thực thi lệnh: `{error}`")


bot = WineMusicBot()


@bot.hybrid_command(name="help", aliases=["h", "commands"], description="Xem bảng hướng dẫn toàn bộ lệnh của Music Bot")
async def help_command(ctx: commands.Context[commands.Bot]) -> None:
    p = COMMAND_PREFIX
    embed = discord.Embed(
        title="🎧 Hướng Dẫn Sử Dụng Discord Music Bot",
        description=(
            f"Bot hỗ trợ **cả hai kiểu gõ lệnh**: Slash Command (`/play`) có gợi ý tìm kiếm tự động "
            f"và lệnh nhanh bằng tiền tố (`{p}play`, `{p}p`)."
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="🎶 Phát Nhạc & Hàng Đợi",
        value=(
            f"• `/play <từ khóa / link>` (`{p}p`) — Phát bài hát hoặc thêm cả Playlist YouTube\n"
            f"• `/queue` (`{p}q`) — Xem danh sách bài hát đang chờ\n"
            f"• `/nowplaying` (`{p}np`) — Hiện lại thẻ bài đang phát & nút điều khiển\n"
            f"• `/shuffle` — Xáo trộn ngẫu nhiên hàng đợi\n"
            f"• `/remove <số thứ tự>` (`{p}rm`) — Xóa 1 bài khỏi hàng đợi\n"
            f"• `/clear` — Xóa toàn bộ danh sách chờ"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎛️ Điều Khiển & Hiệu Ứng Âm Thanh",
        value=(
            f"• `/pause` & `/resume` — Tạm dừng / Tiếp tục phát\n"
            f"• `/skip` (`{p}s`) — Bỏ qua bài hát hiện tại\n"
            f"• `/loop [off|track|queue]` — Đổi chế độ lặp lại\n"
            f"• `/volume <1-200>` (`{p}vol`) — Chỉnh âm lượng trực tiếp\n"
            f"• `/filter <hiệu ứng>` (`{p}bassboost`) — Đổi hiệu ứng âm thanh ngay giữa bài\n"
            f"• `/stop` (`{p}leave`, `{p}dc`) — Dừng nhạc và rời kênh thoại"
        ),
        inline=False,
    )

    filter_list = " • ".join(f"`{k}` ({v.emoji} {v.label})" for k, v in AUDIO_FILTERS.items())
    embed.add_field(name="✨ Các Bộ Lọc (Filters) Hỗ Trợ", value=filter_list, inline=False)
    embed.set_footer(text=f"Mẹo: Gõ {p}sync (quyền Quản trị viên) nếu server mới chưa hiện lệnh gạch chéo /")
    await ctx.send(embed=embed)


@bot.command(name="sync", help="Đồng bộ nhanh Slash Commands ngay lập tức cho Server hiện tại")
@commands.has_permissions(manage_guild=True)
async def sync_guild_commands(ctx: commands.Context[commands.Bot]) -> None:
    if ctx.guild is None:
        return
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ Đã đồng bộ tức thì **{len(synced)}** Slash Commands cho server **{ctx.guild.name}**!")


def main() -> None:
    ok_ffmpeg, ffmpeg_info = verify_ffmpeg()
    if ok_ffmpeg:
        log.info("Đã tìm thấy FFmpeg: %s", ffmpeg_info)
    else:
        log.error(
            "❌ Không tìm thấy FFmpeg (%s)! Hãy chạy 'winget install Gyan.FFmpeg' hoặc cấu hình FFMPEG_PATH trong .env",
            ffmpeg_info,
        )
        sys.exit(1)

    if not DISCORD_TOKEN or DISCORD_TOKEN == "dan_token_bot_cua_ban_vao_day":
        log.error("===================================================================")
        log.error("⚠️ CHƯA CẤU HÌNH DISCORD_TOKEN!")
        log.error("Hãy mở file e:\\bot_wine\\.env và dán Token Bot của bạn vào dòng:")
        log.error("DISCORD_TOKEN=token_that_cua_ban")
        log.error("===================================================================")
        sys.exit(1)

    try:
        bot.run(DISCORD_TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        log.error("===================================================================")
        log.error("❌ THIẾU QUYỀN PRIVILEGED INTENTS TRÊN DISCORD DEVELOPER PORTAL!")
        log.error("Vào https://discord.com/developers/applications -> Chọn Bot -> mục Bot:")
        log.error("Bật công tắc 'MESSAGE CONTENT INTENT', nhấn Save Changes rồi chạy lại.")
        log.error("===================================================================")
        sys.exit(1)
    except discord.LoginFailure:
        log.error("❌ DISCORD_TOKEN không hợp lệ! Vui lòng kiểm tra lại Token trong file .env.")
        sys.exit(1)


if __name__ == "__main__":
    main()
