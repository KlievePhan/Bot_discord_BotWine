from __future__ import annotations

import asyncio
import logging
import random
import shlex
import time
from collections import deque
from enum import Enum
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    AUDIO_FILTERS,
    AUTO_LEAVE_EMPTY_SECONDS,
    AUTO_LEAVE_IDLE_SECONDS,
    COMMAND_PREFIX,
    DEFAULT_VOLUME,
    FFMPEG_EXECUTABLE,
    get_cookies_path,
)
from utils.ytdlp_helper import (
    Track,
    ensure_stream_url,
    extract_tracks,
    format_duration,
    search_autocomplete,
    translate_ytdlp_error,
)

log = logging.getLogger("music_bot.cog")


class LoopMode(str, Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"

    @property
    def label(self) -> str:
        if self is LoopMode.TRACK:
            return "Lặp 1 bài"
        if self is LoopMode.QUEUE:
            return "Lặp hàng đợi"
        return "Tắt"

    @property
    def emoji(self) -> str:
        if self is LoopMode.TRACK:
            return "🔂"
        return "🔁"

    def next_mode(self) -> LoopMode:
        order = [LoopMode.OFF, LoopMode.TRACK, LoopMode.QUEUE]
        idx = order.index(self)
        return order[(idx + 1) % len(order)]


def build_progress_bar(elapsed: float, duration: int | None, is_live: bool, length: int = 14) -> str:
    if is_live:
        return "🔴 **Đang phát trực tiếp (LIVE)**"
    if not duration or duration <= 0:
        return f"`{format_duration(elapsed)} / ??:??`"

    clamped = max(0.0, min(float(duration), elapsed))
    ratio = clamped / float(duration)
    knob_idx = min(length - 1, int(round(ratio * (length - 1))))
    bar = "━" * knob_idx + "🔘" + "─" * (length - 1 - knob_idx)
    return f"`{format_duration(clamped)}` {bar} `{format_duration(duration)}`"


def build_ffmpeg_source(
    track: Track,
    *,
    volume: float,
    audio_filter_key: str,
    seek_seconds: float = 0.0,
) -> discord.PCMVolumeTransformer:
    """Tạo luồng âm thanh FFmpeg kèm tự động kết nối lại, seek thời gian và bộ lọc âm thanh."""
    if not track.stream_url:
        raise ValueError("Track chưa có stream_url hợp lệ.")

    before_parts: list[str] = [
        "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-nostdin",
    ]

    user_agent = track.http_headers.get("User-Agent")
    if user_agent:
        before_parts.extend(["-user_agent", user_agent])

    if seek_seconds > 1.0 and not track.is_live:
        before_parts.extend(["-ss", f"{seek_seconds:.2f}"])

    option_parts: list[str] = ["-vn"]
    preset = AUDIO_FILTERS.get(audio_filter_key)
    if preset and preset.ffmpeg_af:
        option_parts.extend(["-af", preset.ffmpeg_af])

    before_options = " ".join(shlex.quote(part) for part in before_parts)
    options = " ".join(shlex.quote(part) for part in option_parts)

    # Keep native FFmpeg errors even when the Windows launcher has no stderr.
    # Popen gives the child its own handle, so the parent can close this file.
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    error_log = log_dir / "ffmpeg.log"
    with error_log.open("ab", buffering=0) as stderr_file:
        stderr_file.write(
            f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} filter={audio_filter_key} ---\n".encode()
        )
        pcm_audio = discord.FFmpegPCMAudio(
            track.stream_url,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_options,
            options=options,
            stderr=stderr_file,
        )
    log.info("FFmpeg diagnostics: %s", error_log)
    return discord.PCMVolumeTransformer(pcm_audio, volume=volume)


class GuildMusicState:
    """Lưu trữ độc lập trạng thái phát nhạc & hàng đợi cho từng Server (Guild)."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id: int = guild_id
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.loop_mode: LoopMode = LoopMode.OFF
        self.volume: float = DEFAULT_VOLUME
        self.audio_filter: str = "off"
        self.text_channel: discord.abc.Messageable | None = None
        self.now_playing_msg: discord.Message | None = None

        # Theo dõi thời gian phát để vẽ thanh tiến trình & đổi Filter mượt giữa bài
        self._started_at: float = 0.0
        self._paused_at: float | None = None
        self._seek_offset: float = 0.0

        # Cờ đồng bộ tránh xung đột khi chuyển nguồn phát hoặc dừng hẳn
        self.play_lock: asyncio.Lock = asyncio.Lock()
        self.suppress_after_callback: bool = False

        # Tác vụ hẹn giờ tự rời phòng
        self.idle_leave_task: asyncio.Task[None] | None = None
        self.empty_room_task: asyncio.Task[None] | None = None

    def mark_started(self, seek_offset: float = 0.0) -> None:
        self._started_at = time.monotonic()
        self._paused_at = None
        self._seek_offset = seek_offset

    def mark_paused(self) -> None:
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def mark_resumed(self) -> None:
        if self._paused_at is not None:
            paused_duration = time.monotonic() - self._paused_at
            self._started_at += paused_duration
            self._paused_at = None

    def get_elapsed(self) -> float:
        if not self.current or self._started_at <= 0:
            return 0.0
        now = self._paused_at if self._paused_at is not None else time.monotonic()
        elapsed = self._seek_offset + max(0.0, now - self._started_at)
        if self.current.duration:
            return min(float(self.current.duration), elapsed)
        return elapsed

    def cancel_idle_timer(self) -> None:
        if self.idle_leave_task and not self.idle_leave_task.done():
            self.idle_leave_task.cancel()
        self.idle_leave_task = None

    def cancel_empty_timer(self) -> None:
        if self.empty_room_task and not self.empty_room_task.done():
            self.empty_room_task.cancel()
        self.empty_room_task = None

    def reset(self) -> None:
        self.cancel_idle_timer()
        self.cancel_empty_timer()
        self.queue.clear()
        self.current = None
        self._started_at = 0.0
        self._paused_at = None
        self._seek_offset = 0.0


class FilterSelect(discord.ui.Select["PlayerControlView"]):
    """Menu thả xuống để chọn nhanh hiệu ứng âm thanh (Audio Filter) ngay trên bảng điều khiển."""

    def __init__(self, active_filter: str) -> None:
        options: list[discord.SelectOption] = []
        for key, preset in AUDIO_FILTERS.items():
            options.append(
                discord.SelectOption(
                    label=preset.label,
                    value=key,
                    emoji=preset.emoji,
                    description=preset.description[:100],
                    default=(key == active_filter),
                )
            )
        super().__init__(
            placeholder="🎛️ Chọn hiệu ứng âm thanh (Audio Filter)...",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        chosen = self.values[0]
        await self.view.cog.apply_filter_from_ui(interaction, chosen)


class PlayerControlView(discord.ui.View):
    """Bảng nút bấm tương tác trực tiếp dưới thẻ Now Playing."""

    def __init__(self, cog: MusicCog, guild_id: int) -> None:
        super().__init__(timeout=3600)
        self.cog = cog
        self.guild_id = guild_id
        state = cog.get_state(guild_id)
        self.filter_select = FilterSelect(state.audio_filter)
        self.add_item(self.filter_select)
        self.sync_visual_state(is_paused=False)

    def sync_visual_state(self, *, is_paused: bool) -> None:
        state = self.cog.get_state(self.guild_id)

        if is_paused:
            self.pause_resume_btn.label = "Tiếp tục"
            self.pause_resume_btn.emoji = "▶️"
            self.pause_resume_btn.style = discord.ButtonStyle.success
        else:
            self.pause_resume_btn.label = "Tạm dừng"
            self.pause_resume_btn.emoji = "⏸️"
            self.pause_resume_btn.style = discord.ButtonStyle.secondary

        self.loop_btn.emoji = state.loop_mode.emoji
        self.loop_btn.label = f"Lặp: {state.loop_mode.label}"
        if state.loop_mode is LoopMode.OFF:
            self.loop_btn.style = discord.ButtonStyle.secondary
        elif state.loop_mode is LoopMode.TRACK:
            self.loop_btn.style = discord.ButtonStyle.success
        else:
            self.loop_btn.style = discord.ButtonStyle.primary

        for opt in self.filter_select.options:
            opt.default = opt.value == state.audio_filter

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        vc = interaction.guild.voice_client
        if not vc or not vc.channel:
            await interaction.response.send_message(
                "⚠️ Bot hiện không còn ở trong kênh thoại.", ephemeral=True
            )
            return False
        if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
            await interaction.response.send_message(
                f"⚠️ Bạn cần vào kênh thoại **{vc.channel.name}** cùng với bot để dùng nút điều khiển!",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Tạm dừng", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PlayerControlView]
    ) -> None:
        assert interaction.guild is not None
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[assignment]
        state = self.cog.get_state(self.guild_id)

        if not vc or not state.current:
            await interaction.response.send_message("⚠️ Không có bài hát nào đang phát.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            state.mark_paused()
            self.sync_visual_state(is_paused=True)
            embed = self.cog.build_now_playing_embed(state, is_paused=True)
            await interaction.response.edit_message(embed=embed, view=self)
        elif vc.is_paused():
            vc.resume()
            state.mark_resumed()
            self.sync_visual_state(is_paused=False)
            embed = self.cog.build_now_playing_embed(state, is_paused=False)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("⚠️ Trạng thái phát không khả dụng.", ephemeral=True)

    @discord.ui.button(label="Bài tiếp", emoji="⏭️", style=discord.ButtonStyle.primary, row=0)
    async def skip_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PlayerControlView]
    ) -> None:
        assert interaction.guild is not None
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[assignment]
        state = self.cog.get_state(self.guild_id)

        if not vc or (not vc.is_playing() and not vc.is_paused()):
            await interaction.response.send_message("⚠️ Không có bài hát nào để bỏ qua.", ephemeral=True)
            return

        skipped_title = state.current.title if state.current else "bài hiện tại"
        # Nếu đang lặp 1 bài mà bấm Skip, vẫn chuyển sang bài kế tiếp trong Queue
        if state.loop_mode is LoopMode.TRACK and state.current and state.queue:
            state.loop_mode = LoopMode.OFF

        vc.stop()
        await interaction.response.send_message(
            f"⏭️ **{interaction.user.display_name}** đã bỏ qua: **{skipped_title}**"
        )

    @discord.ui.button(label="Lặp: Tắt", emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def loop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PlayerControlView]
    ) -> None:
        assert interaction.guild is not None
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[assignment]
        state = self.cog.get_state(self.guild_id)
        state.loop_mode = state.loop_mode.next_mode()

        is_paused = bool(vc and vc.is_paused())
        self.sync_visual_state(is_paused=is_paused)
        embed = self.cog.build_now_playing_embed(state, is_paused=is_paused)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Trộn bài", emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PlayerControlView]
    ) -> None:
        state = self.cog.get_state(self.guild_id)
        if len(state.queue) < 2:
            await interaction.response.send_message(
                "ℹ️ Hàng đợi cần có ít nhất 2 bài hát để xáo trộn.", ephemeral=True
            )
            return

        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)
        await interaction.response.send_message(
            f"🔀 **{interaction.user.display_name}** đã xáo trộn ngẫu nhiên **{len(items)}** bài trong hàng đợi!"
        )

    @discord.ui.button(label="Dừng", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PlayerControlView]
    ) -> None:
        assert interaction.guild is not None
        await self.cog.stop_and_cleanup(interaction.guild)
        self.disable_all()
        try:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                f"⏹️ **{interaction.user.display_name}** đã dừng nhạc và cho bot rời phòng thoại."
            )
        except discord.HTTPException:
            pass


class QueuePaginationView(discord.ui.View):
    """Phân trang danh sách hàng đợi khi có nhiều bài hát."""

    def __init__(self, cog: MusicCog, guild_id: int, per_page: int = 10) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.per_page = per_page
        self.page = 0
        self._update_buttons()

    def _total_pages(self) -> int:
        state = self.cog.get_state(self.guild_id)
        return max(1, (len(state.queue) + self.per_page - 1) // self.per_page)

    def _update_buttons(self) -> None:
        total = self._total_pages()
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= (total - 1)

    def build_embed(self) -> discord.Embed:
        state = self.cog.get_state(self.guild_id)
        total_pages = self._total_pages()
        self.page = max(0, min(self.page, total_pages - 1))
        self._update_buttons()

        embed = discord.Embed(
            title="📜 Danh Sách Hàng Đợi Phát Nhạc",
            color=discord.Color.blurple(),
        )

        if state.current:
            embed.add_field(
                name="🎶 Đang phát hiện tại",
                value=(
                    f"[{state.current.title}]({state.current.webpage_url}) "
                    f"(`{state.current.duration_str}`) — yêu cầu bởi **{state.current.requester_name}**"
                ),
                inline=False,
            )

        queue_list = list(state.queue)
        if not queue_list:
            embed.description = "*Hàng đợi tiếp theo hiện đang trống. Gõ `/play` hoặc `!play` để thêm bài!*"
        else:
            start = self.page * self.per_page
            end = start + self.per_page
            lines: list[str] = []
            for idx, track in enumerate(queue_list[start:end], start=start + 1):
                title_short = track.title if len(track.title) <= 58 else track.title[:55] + "..."
                lines.append(
                    f"`{idx:02d}.` [{title_short}]({track.webpage_url}) "
                    f"(`{track.duration_str}`) • *{track.requester_name}*"
                )
            embed.add_field(
                name=f"⏳ Danh sách chờ ({len(queue_list)} bài)",
                value="\n".join(lines),
                inline=False,
            )

            known_durations = [t.duration for t in queue_list if t.duration]
            total_dur = format_duration(sum(known_durations)) if known_durations else "??:??"
            preset = AUDIO_FILTERS[state.audio_filter]
            embed.set_footer(
                text=(
                    f"Trang {self.page + 1}/{total_pages} • Tổng thời lượng chờ: {total_dur} "
                    f"• Lặp: {state.loop_mode.label} • Filter: {preset.label}"
                )
            )

        return embed

    @discord.ui.button(label="Trang trước", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[QueuePaginationView]
    ) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Trang sau", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[QueuePaginationView]
    ) -> None:
        self.page = min(self._total_pages() - 1, self.page + 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class MusicCog(commands.Cog, name="Music"):
    """Cog xử lý toàn bộ tính năng phát nhạc YouTube, hàng đợi, nút bấm & bộ lọc âm thanh."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildMusicState(guild_id)
        return self.states[guild_id]

    async def cog_unload(self) -> None:
        for guild in self.bot.guilds:
            if guild.voice_client:
                await self.stop_and_cleanup(guild)

    # =========================================================================
    # TIỆN ÍCH HIỂN THỊ & QUẢN LÝ TRẠNG THÁI PHÁT
    # =========================================================================

    def build_now_playing_embed(self, state: GuildMusicState, *, is_paused: bool = False) -> discord.Embed:
        track = state.current
        assert track is not None

        status_icon = "⏸️ Đang tạm dừng" if is_paused else "🎶 Đang phát nhạc"
        color = discord.Color.orange() if is_paused else discord.Color.from_rgb(88, 101, 242)

        embed = discord.Embed(
            title=track.title,
            url=track.webpage_url,
            description=build_progress_bar(state.get_elapsed(), track.duration, track.is_live),
            color=color,
        )
        embed.set_author(name=status_icon)

        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)

        preset = AUDIO_FILTERS.get(state.audio_filter, AUDIO_FILTERS["off"])
        embed.add_field(name="📺 Kênh YouTube", value=track.uploader, inline=True)
        embed.add_field(name="⏱️ Thời lượng", value=f"`{track.duration_str}`", inline=True)
        embed.add_field(name="👤 Yêu cầu bởi", value=f"<@{track.requester_id}>", inline=True)
        embed.add_field(name="🔊 Âm lượng", value=f"`{int(round(state.volume * 100))}%`", inline=True)
        embed.add_field(name="🎛️ Bộ lọc", value=f"{preset.emoji} {preset.label}", inline=True)
        embed.add_field(
            name="🔁 Chế độ lặp / Chờ",
            value=f"{state.loop_mode.label} • `{len(state.queue)}` bài chờ",
            inline=True,
        )

        cookie_status = "🍪 Cookies: Bật" if get_cookies_path() else "🌐 Chế độ tiêu chuẩn"
        embed.set_footer(text=f"{cookie_status} • Dùng nút bên dưới hoặc gõ {COMMAND_PREFIX}help để điều khiển")
        return embed

    async def _disable_previous_np_message(self, state: GuildMusicState) -> None:
        old_msg = state.now_playing_msg
        state.now_playing_msg = None
        if not old_msg:
            return
        try:
            await old_msg.edit(view=None)
        except discord.HTTPException:
            pass

    async def stop_and_cleanup(self, guild: discord.Guild) -> None:
        state = self.get_state(guild.id)
        state.suppress_after_callback = True
        await self._disable_previous_np_message(state)
        state.reset()

        vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]
        if vc:
            try:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                await vc.disconnect(force=True)
            except Exception as exc:
                log.debug("Lỗi nhỏ khi ngắt kết nối voice tại guild %s: %s", guild.id, exc)
        state.suppress_after_callback = False

    def _schedule_idle_leave(self, guild: discord.Guild) -> None:
        state = self.get_state(guild.id)
        state.cancel_idle_timer()

        async def _idle_worker() -> None:
            try:
                await asyncio.sleep(AUTO_LEAVE_IDLE_SECONDS)
                vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]
                if vc and not vc.is_playing() and not vc.is_paused() and not state.queue:
                    channel = state.text_channel
                    await self.stop_and_cleanup(guild)
                    if channel:
                        mins = AUTO_LEAVE_IDLE_SECONDS // 60
                        time_txt = f"{mins} phút" if mins > 0 else f"{AUTO_LEAVE_IDLE_SECONDS} giây"
                        await channel.send(
                            f"💤 Không có bài hát nào mới sau **{time_txt}**, mình tự động rời phòng để giải phóng RAM nhé!"
                        )
            except asyncio.CancelledError:
                pass

        state.idle_leave_task = asyncio.create_task(_idle_worker())

    async def _play_next_in_queue(self, guild: discord.Guild, *, previous_failed: bool = False) -> None:
        state = self.get_state(guild.id)

        async with state.play_lock:
            vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]
            if not vc or not vc.is_connected():
                state.reset()
                return

            if vc.is_playing() or vc.is_paused():
                return

            await self._disable_previous_np_message(state)

            # Xử lý chế độ Lặp (chỉ lặp lại bài vừa phát nếu nó không bị lỗi tải)
            next_track: Track | None = None
            if not previous_failed and state.current:
                if state.loop_mode is LoopMode.TRACK:
                    next_track = state.current
                elif state.loop_mode is LoopMode.QUEUE:
                    state.queue.append(state.current)

            if next_track is None:
                if state.queue:
                    next_track = state.queue.popleft()
                else:
                    state.current = None
                    self._schedule_idle_leave(guild)
                    if state.text_channel:
                        try:
                            await state.text_channel.send(
                                "✅ **Đã phát hết danh sách nhạc trong hàng đợi!** "
                                f"Gõ `/play` hoặc `{COMMAND_PREFIX}play` để thêm bài mới nhé."
                            )
                        except discord.HTTPException:
                            pass
                    return

            state.cancel_idle_timer()
            state.current = next_track

            # Lấy Direct Stream URL mới nhất từ YouTube (nếu chưa có hoặc đã quá hạn)
            try:
                await ensure_stream_url(next_track)
                source = build_ffmpeg_source(
                    next_track,
                    volume=state.volume,
                    audio_filter_key=state.audio_filter,
                    seek_seconds=0.0,
                )
            except Exception as exc:
                friendly_err = translate_ytdlp_error(exc)
                log.warning("Bỏ qua bài lỗi '%s' ở guild %s: %s", next_track.title, guild.id, exc)
                if state.text_channel:
                    try:
                        await state.text_channel.send(
                            f"⚠️ Bỏ qua **{next_track.title}**: {friendly_err}"
                        )
                    except discord.HTTPException:
                        pass
                # Tự động thử phát bài tiếp theo trong hàng đợi mà không làm crash bot
                self.bot.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._play_next_in_queue(guild, previous_failed=True))
                )
                return

            def _after_playback(play_err: Exception | None) -> None:
                if play_err:
                    log.error("Lỗi FFmpeg/Voice khi phát tại guild %s: %s", guild.id, play_err)
                if state.suppress_after_callback:
                    return
                self.bot.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self._play_next_in_queue(guild, previous_failed=play_err is not None)
                    )
                )

            vc.play(source, after=_after_playback)
            state.mark_started(seek_offset=0.0)

            if state.text_channel:
                try:
                    view = PlayerControlView(self, guild.id)
                    embed = self.build_now_playing_embed(state, is_paused=False)
                    state.now_playing_msg = await state.text_channel.send(embed=embed, view=view)
                except discord.HTTPException as exc:
                    log.warning("Không thể gửi thẻ Now Playing ở guild %s: %s", guild.id, exc)

    async def hot_swap_filter(self, guild: discord.Guild, new_filter_key: str) -> bool:
        """Đổi bộ lọc âm thanh (Filter) ngay tại thời điểm đang phát mà không mất bài hát."""
        state = self.get_state(guild.id)
        state.audio_filter = new_filter_key

        vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]
        if not vc or not state.current or (not vc.is_playing() and not vc.is_paused()):
            return False

        async with state.play_lock:
            was_paused = vc.is_paused()
            elapsed = state.get_elapsed()
            await ensure_stream_url(state.current)
            new_source = build_ffmpeg_source(
                state.current,
                volume=state.volume,
                audio_filter_key=new_filter_key,
                seek_seconds=elapsed,
            )

            state.suppress_after_callback = True
            vc.stop()
            # Đợi ngắn để luồng audio cũ kết thúc callback trước khi gắn nguồn mới
            await asyncio.sleep(0.12)
            state.suppress_after_callback = False

            def _after_playback(play_err: Exception | None) -> None:
                if play_err:
                    log.error("Lỗi FFmpeg sau khi đổi filter ở guild %s: %s", guild.id, play_err)
                if state.suppress_after_callback:
                    return
                self.bot.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self._play_next_in_queue(guild, previous_failed=play_err is not None)
                    )
                )

            vc.play(new_source, after=_after_playback)
            state.mark_started(seek_offset=elapsed)
            if was_paused:
                vc.pause()
                state.mark_paused()
            return True

    async def apply_filter_from_ui(self, interaction: discord.Interaction, chosen_key: str) -> None:
        assert interaction.guild is not None
        if chosen_key not in AUDIO_FILTERS:
            await interaction.response.send_message("❌ Bộ lọc không hợp lệ.", ephemeral=True)
            return

        await interaction.response.defer()
        await self.hot_swap_filter(interaction.guild, chosen_key)

        state = self.get_state(interaction.guild.id)
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[assignment]
        is_paused = bool(vc and vc.is_paused())

        if state.current and interaction.message:
            view = PlayerControlView(self, interaction.guild.id)
            view.sync_visual_state(is_paused=is_paused)
            embed = self.build_now_playing_embed(state, is_paused=is_paused)
            try:
                await interaction.message.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass

        preset = AUDIO_FILTERS[chosen_key]
        await interaction.followup.send(
            f"{preset.emoji} **{interaction.user.display_name}** đã chuyển bộ lọc âm thanh sang **{preset.label}**!"
        )

    # =========================================================================
    # KIỂM TRA KẾT NỐI KÊNH THOẠI (GIAI ĐOẠN 1)
    # =========================================================================

    async def _ensure_user_and_bot_voice(
        self, ctx: commands.Context[commands.Bot], *, connect_if_needed: bool = False
    ) -> discord.VoiceClient | None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await ctx.send("❌ Lệnh này chỉ hoạt động bên trong Server Discord.")
            return None

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("⚠️ Bạn cần tham gia một **Kênh thoại (Voice Channel)** trước!")
            return None

        user_channel = ctx.author.voice.channel
        vc: discord.VoiceClient | None = ctx.guild.voice_client  # type: ignore[assignment]

        if vc is None:
            if not connect_if_needed:
                await ctx.send("⚠️ Bot hiện không phát nhạc trong kênh thoại nào.")
                return None

            perms = user_channel.permissions_for(ctx.guild.me)
            if not perms.connect or not perms.speak:
                await ctx.send(
                    f"❌ Bot thiếu quyền **Kết nối (Connect)** hoặc **Nói (Speak)** tại kênh **{user_channel.name}**!"
                )
                return None

            vc = await user_channel.connect(self_deaf=True)
            return vc

        if vc.channel != user_channel:
            # Nếu phòng cũ không còn người nghe nào, tự động chuyển sang phòng người gọi lệnh
            humans_in_old = [m for m in vc.channel.members if not m.bot] if vc.channel else []
            if connect_if_needed and not humans_in_old:
                await vc.move_to(user_channel)
                return vc

            await ctx.send(
                f"⚠️ Bot đang phục vụ tại kênh **{vc.channel.name}**. "
                "Hãy vào cùng kênh thoại với bot để điều khiển nhé!"
            )
            return None

        return vc

    # =========================================================================
    # SỰ KIỆN TỰ ĐỘNG RỜI PHÒNG KHI TRỐNG NGƯỜI (GIAI ĐOẠN 4)
    # =========================================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        guild = member.guild
        if guild.id not in self.states:
            return
        state = self.states[guild.id]

        # 1. Nếu chính Bot bị ngắt kết nối khỏi kênh thoại từ bên ngoài
        if self.bot.user and member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                await self._disable_previous_np_message(state)
                state.reset()
            return

        # 2. Kiểm tra số lượng người thật còn lại trong phòng thoại của Bot
        vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]
        if not vc or not vc.channel:
            return

        human_members = [m for m in vc.channel.members if not m.bot]
        if len(human_members) == 0:
            if state.empty_room_task is None or state.empty_room_task.done():
                async def _leave_when_empty() -> None:
                    try:
                        await asyncio.sleep(AUTO_LEAVE_EMPTY_SECONDS)
                        current_vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]
                        if current_vc and current_vc.channel:
                            still_humans = [m for m in current_vc.channel.members if not m.bot]
                            if len(still_humans) == 0:
                                notify_ch = state.text_channel
                                await self.stop_and_cleanup(guild)
                                if notify_ch:
                                    await notify_ch.send(
                                        "👋 Phòng thoại không còn ai nên mình tự động rời phòng và xóa hàng đợi nhé!"
                                    )
                    except asyncio.CancelledError:
                        pass

                state.empty_room_task = asyncio.create_task(_leave_when_empty())
        else:
            # Có người quay lại phòng trước khi hết giờ -> hủy bộ đếm rời phòng
            state.cancel_empty_timer()

    # =========================================================================
    # CÁC LỆNH ĐIỀU KHIỂN NHẠC (HYBRID: SLASH COMMAND + PREFIX COMMAND)
    # =========================================================================

    @commands.hybrid_command(
        name="play",
        aliases=["p"],
        description="Phát nhạc từ link YouTube, Playlist hoặc từ khóa tìm kiếm",
    )
    @app_commands.describe(query="Nhập tên bài hát, ca sĩ hoặc dán link YouTube / Playlist")
    async def play(self, ctx: commands.Context[commands.Bot], *, query: str) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx, connect_if_needed=True)
        if vc is None or ctx.guild is None:
            return

        await ctx.defer()
        state = self.get_state(ctx.guild.id)
        state.text_channel = ctx.channel
        state.cancel_idle_timer()
        state.cancel_empty_timer()

        try:
            tracks = await extract_tracks(query, requester=ctx.author)
        except Exception as exc:
            await ctx.send(f"❌ {translate_ytdlp_error(exc)}")
            if not vc.is_playing() and not vc.is_paused() and not state.queue:
                self._schedule_idle_leave(ctx.guild)
            return

        if not tracks:
            await ctx.send(f"🔍 Không tìm thấy kết quả phù hợp cho: **{query}**")
            if not vc.is_playing() and not vc.is_paused() and not state.queue:
                self._schedule_idle_leave(ctx.guild)
            return

        is_already_active = vc.is_playing() or vc.is_paused() or state.current is not None
        for t in tracks:
            state.queue.append(t)

        if len(tracks) > 1:
            embed = discord.Embed(
                title="📑 Đã thêm Playlist vào hàng đợi",
                description=(
                    f"Đã nạp thành công **{len(tracks)}** bài hát vào danh sách chờ!\n"
                    f"Bài đầu tiên: **[{tracks[0].title}]({tracks[0].webpage_url})**"
                ),
                color=discord.Color.green(),
            )
            if tracks[0].thumbnail:
                embed.set_thumbnail(url=tracks[0].thumbnail)
            await ctx.send(embed=embed)
        elif is_already_active:
            added = tracks[0]
            pos = len(state.queue)
            embed = discord.Embed(
                description=(
                    f"➕ Đã thêm vào hàng đợi (vị trí `#{pos}`):\n"
                    f"**[{added.title}]({added.webpage_url})** (`{added.duration_str}`)"
                ),
                color=discord.Color.teal(),
            )
            if added.thumbnail:
                embed.set_thumbnail(url=added.thumbnail)
            await ctx.send(embed=embed)
        else:
            # Nếu bắt đầu phát ngay lập tức từ Slash Command, gửi phản hồi xác nhận gọn gàng
            first = tracks[0]
            await ctx.send(f"🔎 Đã tìm thấy: **{first.title}** (`{first.duration_str}`) — đang khởi tạo âm thanh...")

        if not vc.is_playing() and not vc.is_paused():
            await self._play_next_in_queue(ctx.guild)

    @play.autocomplete("query")
    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        suggestions = await search_autocomplete(current, limit=5)
        choices: list[app_commands.Choice[str]] = []
        for item in suggestions:
            display = f"🎵 [{item['duration']}] {item['title']} — {item['channel']}"
            if len(display) > 100:
                display = display[:97] + "..."
            url = item["url"]
            if len(url) <= 100:
                choices.append(app_commands.Choice(name=display, value=url))
        return choices

    @commands.hybrid_command(name="pause", description="Tạm dừng bài hát đang phát")
    async def pause(self, ctx: commands.Context[commands.Bot]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        if vc.is_paused():
            await ctx.send("ℹ️ Nhạc vốn đang được tạm dừng rồi.")
            return
        if not vc.is_playing():
            await ctx.send("⚠️ Hiện không có bài hát nào đang phát.")
            return

        state = self.get_state(ctx.guild.id)
        vc.pause()
        state.mark_paused()
        await ctx.send(f"⏸️ Đã tạm dừng bài hát. Dùng `/resume` hoặc `{COMMAND_PREFIX}resume` để phát tiếp.")

    @commands.hybrid_command(name="resume", aliases=["unpause"], description="Tiếp tục phát bài hát đang tạm dừng")
    async def resume(self, ctx: commands.Context[commands.Bot]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        if not vc.is_paused():
            await ctx.send("ℹ️ Nhạc hiện không ở trạng thái tạm dừng.")
            return

        state = self.get_state(ctx.guild.id)
        vc.resume()
        state.mark_resumed()
        await ctx.send("▶️ Đã tiếp tục phát nhạc!")

    @commands.hybrid_command(name="skip", aliases=["s", "next"], description="Bỏ qua bài hát hiện tại")
    async def skip(self, ctx: commands.Context[commands.Bot]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        if not vc.is_playing() and not vc.is_paused():
            await ctx.send("⚠️ Không có bài hát nào đang phát để bỏ qua.")
            return

        state = self.get_state(ctx.guild.id)
        title = state.current.title if state.current else "bài hát hiện tại"
        if state.loop_mode is LoopMode.TRACK and state.queue:
            state.loop_mode = LoopMode.OFF

        vc.stop()
        await ctx.send(f"⏭️ Đã bỏ qua: **{title}**")

    @commands.hybrid_command(
        name="stop",
        aliases=["leave", "dc", "disconnect"],
        description="Xóa toàn bộ hàng đợi, dừng nhạc và rời phòng thoại",
    )
    async def stop(self, ctx: commands.Context[commands.Bot]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        await self.stop_and_cleanup(ctx.guild)
        await ctx.send("👋 Đã xóa hàng đợi, dừng phát nhạc và rời kênh thoại.")

    @commands.hybrid_command(name="queue", aliases=["q"], description="Xem danh sách các bài hát đang chờ")
    async def queue_cmd(self, ctx: commands.Context[commands.Bot]) -> None:
        if ctx.guild is None:
            return
        state = self.get_state(ctx.guild.id)
        if not state.current and not state.queue:
            await ctx.send(
                f"📭 Hàng đợi hiện đang trống. Gõ `/play` hoặc `{COMMAND_PREFIX}play <tên bài>` để nghe nhạc nhé!"
            )
            return

        view = QueuePaginationView(self, ctx.guild.id)
        embed = view.build_embed()
        if len(state.queue) > view.per_page:
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="nowplaying",
        aliases=["np"],
        description="Hiển thị thông tin chi tiết & bảng điều khiển của bài đang phát",
    )
    async def nowplaying(self, ctx: commands.Context[commands.Bot]) -> None:
        if ctx.guild is None:
            return
        state = self.get_state(ctx.guild.id)
        vc: discord.VoiceClient | None = ctx.guild.voice_client  # type: ignore[assignment]
        if not vc or not state.current:
            await ctx.send("📭 Hiện không có bài hát nào đang phát.")
            return

        await self._disable_previous_np_message(state)
        is_paused = vc.is_paused()
        view = PlayerControlView(self, ctx.guild.id)
        view.sync_visual_state(is_paused=is_paused)
        embed = self.build_now_playing_embed(state, is_paused=is_paused)
        state.now_playing_msg = await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="volume", aliases=["vol", "v"], description="Điều chỉnh âm lượng phát nhạc (1 - 200%)")
    @app_commands.describe(percent="Mức âm lượng từ 1 đến 200 (mặc định: 80)")
    async def volume(self, ctx: commands.Context[commands.Bot], percent: commands.Range[int, 1, 200]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        state = self.get_state(ctx.guild.id)
        state.volume = percent / 100.0
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume

        icon = "🔊" if percent >= 70 else ("🔉" if percent >= 25 else "🔈")
        await ctx.send(f"{icon} Đã đặt âm lượng ở mức **{percent}%**.")

    @commands.hybrid_command(name="loop", description="Chuyển chế độ lặp lại (Tắt / Lặp 1 bài / Lặp hàng đợi)")
    @app_commands.describe(mode="Chọn chế độ lặp mong muốn")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="🔁 Tắt lặp (Off)", value="off"),
            app_commands.Choice(name="🔂 Lặp 1 bài hiện tại (Track)", value="track"),
            app_commands.Choice(name="🔁 Lặp toàn bộ hàng đợi (Queue)", value="queue"),
        ]
    )
    async def loop_cmd(self, ctx: commands.Context[commands.Bot], mode: str | None = None) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        state = self.get_state(ctx.guild.id)
        if mode is None:
            state.loop_mode = state.loop_mode.next_mode()
        else:
            normalized = mode.strip().lower()
            if normalized not in ("off", "track", "queue"):
                await ctx.send("❌ Chế độ không hợp lệ! Hãy chọn một trong: `off`, `track`, `queue`.")
                return
            state.loop_mode = LoopMode(normalized)

        await ctx.send(f"{state.loop_mode.emoji} Chế độ lặp hiện tại: **{state.loop_mode.label}**.")

    @commands.hybrid_command(
        name="filter",
        aliases=["fx", "bassboost"],
        description="Áp dụng bộ lọc hiệu ứng âm thanh trực tiếp (Bassboost, Nightcore, 8D...)",
    )
    @app_commands.describe(preset="Chọn hiệu ứng âm thanh muốn áp dụng")
    @app_commands.choices(
        preset=[
            app_commands.Choice(name=f"{p.emoji} {p.label}", value=key)
            for key, p in AUDIO_FILTERS.items()
        ]
    )
    async def filter_cmd(self, ctx: commands.Context[commands.Bot], preset: str = "off") -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        key = preset.strip().lower()
        # Nếu người dùng gõ tắt !bassboost không kèm tham số thì tự bật bassboost
        if ctx.invoked_with == "bassboost" and preset == "off":
            key = "bassboost"

        if key not in AUDIO_FILTERS:
            valid = ", ".join(f"`{k}`" for k in AUDIO_FILTERS)
            await ctx.send(f"❌ Không tìm thấy bộ lọc `{preset}`. Các bộ lọc hỗ trợ: {valid}")
            return

        await ctx.defer()
        swapped = await self.hot_swap_filter(ctx.guild, key)
        info = AUDIO_FILTERS[key]
        if swapped:
            await ctx.send(f"{info.emoji} Đã áp dụng ngay bộ lọc **{info.label}** ({info.description})!")
        else:
            await ctx.send(f"{info.emoji} Đã lưu bộ lọc **{info.label}** cho bài hát tiếp theo!")

    @commands.hybrid_command(name="shuffle", description="Xáo trộn ngẫu nhiên thứ tự các bài trong hàng đợi")
    async def shuffle(self, ctx: commands.Context[commands.Bot]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        state = self.get_state(ctx.guild.id)
        if len(state.queue) < 2:
            await ctx.send("ℹ️ Cần có ít nhất 2 bài trong hàng đợi để xáo trộn.")
            return

        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)
        await ctx.send(f"🔀 Đã xáo trộn ngẫu nhiên **{len(items)}** bài hát trong hàng đợi!")

    @commands.hybrid_command(name="remove", aliases=["rm"], description="Xóa một bài hát khỏi hàng đợi theo số thứ tự")
    @app_commands.describe(index="Số thứ tự bài hát trong lệnh /queue (bắt đầu từ 1)")
    async def remove(self, ctx: commands.Context[commands.Bot], index: int) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        state = self.get_state(ctx.guild.id)
        if not state.queue:
            await ctx.send("📭 Hàng đợi hiện đang trống.")
            return
        if index < 1 or index > len(state.queue):
            await ctx.send(f"⚠️ Số thứ tự không hợp lệ! Vui lòng nhập từ `1` đến `{len(state.queue)}`.")
            return

        items = list(state.queue)
        removed = items.pop(index - 1)
        state.queue = deque(items)
        await ctx.send(f"🗑️ Đã xóa bài `#{index}` khỏi hàng đợi: **{removed.title}**")

    @commands.hybrid_command(name="clear", description="Xóa sạch danh sách chờ nhưng vẫn giữ bài đang phát")
    async def clear_queue(self, ctx: commands.Context[commands.Bot]) -> None:
        vc = await self._ensure_user_and_bot_voice(ctx)
        if vc is None or ctx.guild is None:
            return

        state = self.get_state(ctx.guild.id)
        count = len(state.queue)
        state.queue.clear()
        await ctx.send(f"🧹 Đã dọn sạch **{count}** bài hát trong danh sách chờ.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
