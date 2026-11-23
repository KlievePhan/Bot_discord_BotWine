from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import discord
import yt_dlp

from config import BASE_DIR, get_cookies_path

# ThreadPool riêng để tác vụ mạng của yt-dlp không làm nghẽn vòng lặp sự kiện Discord
_YTDLP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ytdlp_worker")

# Cache gợi ý tìm kiếm Autocomplete trong 60 giây để phản hồi cực nhanh
_AUTOCOMPLETE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS = 60.0
_STREAM_URL_TTL_SECONDS = 600.0  # Lấy lại link stream nếu chờ quá 10 phút trong Queue

_URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)
_HAS_NODE_RUNTIME = shutil.which("node") is not None


def format_duration(seconds: int | float | None, is_live: bool = False) -> str:
    """Định dạng thời lượng giây sang chuỗi MM:SS hoặc HH:MM:SS."""
    if is_live:
        return "🔴 LIVE"
    if seconds is None or seconds <= 0:
        return "??:??"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class Track:
    title: str
    webpage_url: str
    requester_id: int
    requester_name: str
    duration: int | None = None
    is_live: bool = False
    thumbnail: str | None = None
    uploader: str = "YouTube"
    stream_url: str | None = None
    http_headers: dict[str, str] = field(default_factory=dict)
    fetched_at: float = 0.0

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration, self.is_live)

    def has_fresh_stream(self) -> bool:
        if not self.stream_url:
            return False
        return (time.monotonic() - self.fetched_at) < _STREAM_URL_TTL_SECONDS


def _is_youtube_mix_url(url: str) -> bool:
    """
    Kiểm tra link YouTube Radio/Mix tự động (list=RD...).
    Với link Mix đính kèm video đang xem, chỉ nên phát video đó thay vì tải 50 bài ngẫu nhiên.
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        list_ids = qs.get("list", [])
        has_video = "v" in qs or "youtu.be" in parsed.netloc.lower()
        return bool(has_video and list_ids and list_ids[0].startswith("RD"))
    except Exception:
        return False


def _build_ydl_options(
    *,
    noplaylist: bool = True,
    extract_flat: bool | str = False,
    playlistend: int | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
        "noplaylist": noplaylist,
        "extract_flat": extract_flat,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",  # Tránh lỗi chặn dải IPv6
        "geo_bypass": True,
        "nocheckcertificate": True,
        "socket_timeout": 15,
    }

    if playlistend is not None:
        opts["playlistend"] = playlistend

    # Tận dụng Node.js trên máy để giải mã chữ ký YouTube (EJS) ổn định nhất
    if _HAS_NODE_RUNTIME:
        opts["js_runtimes"] = {"node": {}}

    provider_home = BASE_DIR / ".local" / "bgutil-ytdlp-pot-provider" / "server"
    if _HAS_NODE_RUNTIME and (provider_home / "build" / "generate_once.js").is_file():
        os.environ.setdefault("XDG_CACHE_HOME", str(BASE_DIR / ".local" / "cache"))
        opts["extractor_args"] = {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilscript": {"server_home": [str(provider_home)]},
        }

    # Tự động nạp cookies.txt nếu người dùng có đặt file trong thư mục dự án
    cookies_path = get_cookies_path()
    if cookies_path:
        opts["cookiefile"] = cookies_path

    return opts


def _extract_info(ydl: yt_dlp.YoutubeDL, query: str) -> dict[str, Any] | None:
    """Try the faster token-enabled client before broader compatibility clients."""
    try:
        return ydl.extract_info(query, download=False)
    except yt_dlp.utils.DownloadError as exc:
        args = ydl.params.get("extractor_args", {})
        if args.get("youtube", {}).get("player_client") != ["mweb"]:
            raise
        # Do not repeat unrelated login, unavailable-video, or network errors.
        if "requested format" not in str(exc).lower():
            raise
        fallback = dict(ydl.params)
        fallback["extractor_args"] = {
            **args, "youtube": {"player_client": ["tv", "web_safari"]}
        }
        with yt_dlp.YoutubeDL(fallback) as alternate:
            return alternate.extract_info(query, download=False)


def _pick_thumbnail(entry: dict[str, Any]) -> str | None:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        for item in reversed(thumbs):
            if isinstance(item, dict) and item.get("url"):
                return item["url"]
    vid_id = entry.get("id")
    if vid_id and isinstance(vid_id, str) and len(vid_id) == 11:
        return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    return None


def _entry_to_track(entry: dict[str, Any], requester: discord.abc.User, is_flat: bool) -> Track | None:
    vid_id = entry.get("id")
    webpage_url = entry.get("webpage_url") or entry.get("original_url")
    entry_url = entry.get("url")

    if is_flat:
        if not webpage_url:
            if entry_url and _URL_REGEX.match(str(entry_url)):
                webpage_url = str(entry_url)
            elif vid_id:
                webpage_url = f"https://www.youtube.com/watch?v={vid_id}"
        stream_url = None
        headers: dict[str, str] = {}
        fetched_at = 0.0
    else:
        stream_url = entry_url
        if not webpage_url:
            if vid_id:
                webpage_url = f"https://www.youtube.com/watch?v={vid_id}"
            else:
                webpage_url = stream_url
        headers = dict(entry.get("http_headers") or {})
        fetched_at = time.monotonic() if stream_url else 0.0

    if not webpage_url:
        return None

    title = (entry.get("title") or "").strip()
    if not title or title in ("[Deleted video]", "[Private video]"):
        return None

    is_live = bool(entry.get("is_live")) or entry.get("live_status") == "is_live"
    raw_duration = entry.get("duration")
    duration = int(raw_duration) if isinstance(raw_duration, (int, float)) and raw_duration > 0 else None

    uploader = (
        entry.get("uploader")
        or entry.get("channel")
        or entry.get("artist")
        or "YouTube"
    )

    return Track(
        title=title,
        webpage_url=webpage_url,
        requester_id=requester.id,
        requester_name=requester.display_name,
        duration=duration,
        is_live=is_live,
        thumbnail=_pick_thumbnail(entry),
        uploader=str(uploader),
        stream_url=stream_url,
        http_headers=headers,
        fetched_at=fetched_at,
    )


def _sync_extract_tracks(query: str, requester: discord.abc.User, max_playlist_items: int) -> list[Track]:
    cleaned = query.strip()
    is_url = bool(_URL_REGEX.match(cleaned))

    if not is_url:
        # Tìm kiếm theo từ khóa: lấy ngay kết quả phù hợp nhất kèm stream URL
        opts = _build_ydl_options(noplaylist=True, extract_flat=False)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _extract_info(ydl, f"ytsearch1:{cleaned}")
        entries = (info or {}).get("entries") or []
        for entry in entries:
            if entry:
                track = _entry_to_track(entry, requester, is_flat=False)
                if track:
                    return [track]
        return []

    # Nếu là URL: hỗ trợ cả Video lẻ lẫn Playlist (trừ YouTube Radio Mix tự động)
    allow_playlist = not _is_youtube_mix_url(cleaned)
    opts = _build_ydl_options(
        noplaylist=not allow_playlist,
        extract_flat="in_playlist" if allow_playlist else False,
        playlistend=max_playlist_items if allow_playlist else None,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _extract_info(ydl, cleaned)

    if not info:
        return []

    if "entries" in info:
        tracks: list[Track] = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            # Với extract_flat='in_playlist', các mục trong playlist chưa có direct stream URL
            is_flat_entry = entry.get("_type") in ("url", "url_transparent") or "formats" not in entry
            track = _entry_to_track(entry, requester, is_flat=is_flat_entry)
            if track:
                tracks.append(track)
        return tracks

    single = _entry_to_track(info, requester, is_flat=False)
    return [single] if single else []


def _sync_populate_stream(track: Track) -> Track:
    opts = _build_ydl_options(noplaylist=True, extract_flat=False)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _extract_info(ydl, track.webpage_url)

    if info and "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else None

    if not info or not info.get("url"):
        raise yt_dlp.utils.DownloadError("Không thể trích xuất luồng âm thanh cho video này.")

    track.stream_url = info["url"]
    track.http_headers = dict(info.get("http_headers") or {})
    track.fetched_at = time.monotonic()

    if info.get("title"):
        track.title = info["title"]
    if info.get("duration") and not track.duration:
        track.duration = int(info["duration"])
    track.is_live = bool(info.get("is_live")) or info.get("live_status") == "is_live"
    thumb = _pick_thumbnail(info)
    if thumb:
        track.thumbnail = thumb
    uploader = info.get("uploader") or info.get("channel")
    if uploader:
        track.uploader = str(uploader)

    return track


def _sync_autocomplete_search(query: str, limit: int) -> list[dict[str, Any]]:
    opts = _build_ydl_options(noplaylist=True, extract_flat=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _extract_info(ydl, f"ytsearch{limit}:{query}")

    results: list[dict[str, Any]] = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        vid_id = entry.get("id")
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else None)
        if not url:
            continue
        if not _URL_REGEX.match(str(url)) and vid_id:
            url = f"https://www.youtube.com/watch?v={vid_id}"

        duration_str = format_duration(
            entry.get("duration"),
            is_live=bool(entry.get("is_live")) or entry.get("live_status") == "is_live",
        )
        channel = entry.get("channel") or entry.get("uploader") or "YouTube"
        results.append(
            {
                "title": title,
                "url": str(url),
                "duration": duration_str,
                "channel": str(channel),
            }
        )
    return results


async def extract_tracks(
    query: str,
    requester: discord.abc.User,
    max_playlist_items: int = 100,
) -> list[Track]:
    """Trích xuất danh sách bài hát (1 bài hoặc cả Playlist) bất đồng bộ."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _YTDLP_EXECUTOR,
        _sync_extract_tracks,
        query,
        requester,
        max_playlist_items,
    )


async def ensure_stream_url(track: Track) -> Track:
    """Đảm bảo Track luôn có Direct Audio Stream URL còn hạn trước khi đưa vào FFmpeg."""
    if track.has_fresh_stream():
        return track
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_YTDLP_EXECUTOR, _sync_populate_stream, track)


async def search_autocomplete(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Tìm nhanh top 5 gợi ý bài hát cho Slash Command Autocomplete."""
    cleaned = query.strip()
    if len(cleaned) < 2 or _URL_REGEX.match(cleaned):
        return []

    cache_key = cleaned.lower()
    now = time.monotonic()
    cached = _AUTOCOMPLETE_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    loop = asyncio.get_running_loop()
    try:
        results = await asyncio.wait_for(
            loop.run_in_executor(_YTDLP_EXECUTOR, _sync_autocomplete_search, cleaned, limit),
            timeout=2.4,  # Đảm bảo kịp giới hạn 3 giây của Discord Autocomplete
        )
    except Exception:
        return []

    # Dọn bớt cache nếu quá 200 từ khóa
    if len(_AUTOCOMPLETE_CACHE) > 200:
        expired = [k for k, (ts, _) in _AUTOCOMPLETE_CACHE.items() if (now - ts) >= _CACHE_TTL_SECONDS]
        for k in expired:
            _AUTOCOMPLETE_CACHE.pop(k, None)

    _AUTOCOMPLETE_CACHE[cache_key] = (now, results)
    return results


def translate_ytdlp_error(exc: Exception) -> str:
    """Chuyển thông báo lỗi kỹ thuật của yt-dlp sang tiếng Việt dễ hiểu."""
    msg = str(exc)
    lower = msg.lower()

    if "sign in to confirm your age" in lower or "age-restricted" in lower or "inappropriate for some users" in lower:
        return (
            "Video này bị **giới hạn độ tuổi (18+)** trên YouTube. "
            "Bạn có thể xuất file `cookies.txt` từ trình duyệt và đặt vào thư mục bot để phát được video này."
        )
    if "sign in to confirm you’re not a bot" in lower or "not a bot" in lower:
        return (
            "YouTube đang yêu cầu xác minh chống bot trên địa chỉ IP hiện tại. "
            "Hãy thêm file `cookies.txt` vào thư mục bot để vượt qua ngay lập tức."
        )
    if "private video" in lower:
        return "Đây là **video riêng tư (Private)** nên không thể truy cập."
    if "members-only" in lower or "join this channel" in lower:
        return "Video này chỉ dành riêng cho **Hội viên kênh (Members-only)**."
    if "video unavailable" in lower or "has been removed" in lower:
        return "Video không tồn tại, đã bị xóa hoặc bị chặn tại quốc gia hiện tại."
    if "premieres in" in lower or "this live event will begin" in lower:
        return "Video/Livestream này chưa tới giờ công chiếu."

    # Rút gọn thông báo lỗi gốc nếu quá dài
    short = msg.splitlines()[-1] if msg.splitlines() else msg
    if len(short) > 220:
        short = short[:217] + "..."
    return f"Không thể tải luồng âm thanh: `{short}`"
