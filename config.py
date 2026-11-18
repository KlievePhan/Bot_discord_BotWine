from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class FilterPreset:
    key: str
    label: str
    emoji: str
    description: str
    ffmpeg_af: str | None


AUDIO_FILTERS: dict[str, FilterPreset] = {
    "off": FilterPreset(
        key="off",
        label="Mặc định (Tắt Filter)",
        emoji="🎵",
        description="Âm thanh gốc nguyên bản từ YouTube",
        ffmpeg_af=None,
    ),
    "bassboost": FilterPreset(
        key="bassboost",
        label="Bassboost",
        emoji="🔥",
        description="Tăng âm trầm mạnh mẽ cho nhạc EDM / Remix",
        ffmpeg_af="bass=g=10:f=110:w=0.6",
    ),
    "nightcore": FilterPreset(
        key="nightcore",
        label="Nightcore",
        emoji="⚡",
        description="Tăng tốc độ và cao độ sôi động (x1.22)",
        ffmpeg_af="asetrate=48000*1.22,aresample=48000,bass=g=3",
    ),
    "slowed": FilterPreset(
        key="slowed",
        label="Slowed + Reverb",
        emoji="🌙",
        description="Làm chậm giai điệu kèm hiệu ứng vang nhẹ chill",
        ffmpeg_af="asetrate=48000*0.85,aresample=48000,aecho=0.8:0.88:60:0.25",
    ),
    "8d": FilterPreset(
        key="8d",
        label="8D Audio",
        emoji="🎧",
        description="Hiệu ứng âm thanh vòm xoay quanh tai nghe",
        ffmpeg_af="apulsator=hz=0.125",
    ),
    "karaoke": FilterPreset(
        key="karaoke",
        label="Karaoke (Giảm lời)",
        emoji="🎤",
        description="Làm nhỏ giọng hát chính ở kênh trung tâm",
        ffmpeg_af="stereotools=mlev=0.03",
    ),
}


def resolve_ffmpeg_path() -> str:
    """Tự động tìm đường dẫn thực thi của FFmpeg trên Windows/Linux/macOS."""
    env_path = os.getenv("FFMPEG_PATH", "").strip()
    if env_path and Path(env_path).is_file():
        return env_path

    # 1. Tìm trong biến môi trường PATH chuẩn
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return which_ffmpeg

    # 2. Tìm trong thư mục dự án cục bộ (nếu người dùng chép ffmpeg.exe vào đây)
    for rel in ("ffmpeg.exe", "bin/ffmpeg.exe", "ffmpeg"):
        candidate = BASE_DIR / rel
        if candidate.is_file():
            return str(candidate)

    # 3. Tìm tự động bản cài đặt từ Winget trên Windows (không cần khởi động lại Terminal)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = glob.glob(
            os.path.join(
                local_app_data,
                "Microsoft",
                "WinGet",
                "Packages",
                "*FFmpeg*",
                "**",
                "ffmpeg.exe",
            ),
            recursive=True,
        )
        if winget_pkgs:
            return winget_pkgs[0]

        winget_link = Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        if winget_link.exists():
            return str(winget_link)

    # Trả về mặc định để báo lỗi rõ ràng khi phát
    return "ffmpeg"


def get_cookies_path() -> str | None:
    """
    Trả về đường dẫn file cookies.txt nếu file tồn tại và có nội dung hợp lệ.
    Tự động nhận diện ngay cả khi thêm file trong lúc bot đang chạy.
    """
    raw_path = os.getenv("COOKIES_FILE", "cookies.txt").strip()
    if not raw_path:
        return None

    cookie_path = Path(raw_path)
    if not cookie_path.is_absolute():
        cookie_path = BASE_DIR / cookie_path

    if not cookie_path.is_file():
        return None

    try:
        for line in cookie_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return str(cookie_path)
    except OSError:
        return None

    return None


DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip()
COMMAND_PREFIX: str = os.getenv("COMMAND_PREFIX", "!").strip() or "!"
FFMPEG_EXECUTABLE: str = resolve_ffmpeg_path()

try:
    AUTO_LEAVE_IDLE_SECONDS: int = max(15, int(os.getenv("AUTO_LEAVE_IDLE_SECONDS", "180")))
except ValueError:
    AUTO_LEAVE_IDLE_SECONDS = 180

try:
    AUTO_LEAVE_EMPTY_SECONDS: int = max(10, int(os.getenv("AUTO_LEAVE_EMPTY_SECONDS", "60")))
except ValueError:
    AUTO_LEAVE_EMPTY_SECONDS = 60

try:
    _vol = int(os.getenv("DEFAULT_VOLUME", "80"))
    DEFAULT_VOLUME: float = max(1, min(200, _vol)) / 100.0
except ValueError:
    DEFAULT_VOLUME = 0.8
