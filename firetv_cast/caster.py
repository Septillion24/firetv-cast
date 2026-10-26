"""What is playing on the TV, and every action the remote page and the CLI can take."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from . import media, streams
from .config import ADB_PATH, HELPER_APK, STATE_PATH, VIDEO_EXTENSIONS, Config
from .firetv import (FAST_FORWARD, MUTE, PAUSE, PLAY, PLAY_PAUSE, REWIND, VLC_PACKAGE, VOLUME_DOWN, VOLUME_UP,
                     FireTV, Key, Playback, TVError, local_ip_toward)

log: logging.Logger = logging.getLogger(__name__)

STATUS_CACHE_SECONDS: float = 0.7
HEADPHONES_CACHE_SECONDS: float = 5.0
POSITION_SAVE_SECONDS: float = 10.0
VLC_SKIP_SECONDS: int = 10
"""How far VLC jumps for one fast-forward or rewind key press."""
LOADING_SECONDS: float = 3.0
"""Right after a video opens, VLC still reports the previous position for a moment."""
LOAD_TIMEOUT_SECONDS: float = 20.0
"""How long to show "loading" while waiting for VLC to report the video we sent before showing what it has."""
FAILED_AFTER_SECONDS: float = 6.0
"""A player that took our video but closed without playing it by now could not play it. Earlier, a closed
player may just be one VLC has not brought up yet."""


@dataclass
class NowPlaying:
    token: str
    path: Path | None
    """The file on this PC, or None for a video played from a website link."""
    duration: float | None
    convert: bool
    offset: float
    """Where the converted or linked stream starts, in seconds. VLC counts from 0 inside it."""
    position: float = 0.0
    """Last position seen on the TV, to pick up from when the player was closed (screensaver, Home)."""
    link: str | None = None
    """The website page the video comes from."""
    name: str | None = None
    """The page's video title, for a link."""

    @property
    def title(self) -> str:
        return self.name or (self.path.stem if self.path is not None else "")

    @property
    def reopens_to_seek(self) -> bool:
        """Converted and linked videos reach VLC as one-way streams it can't skip around in."""
        return self.convert or self.link is not None

    def to_json(self) -> dict[str, object]:
        return {"path": None if self.path is None else str(self.path), "link": self.link, "name": self.name,
                "duration": self.duration, "convert": self.convert, "offset": self.offset, "position": self.position}

    @classmethod
    def from_json(cls, data: dict[str, object]) -> NowPlaying:
        link: str | None = str(data["link"]) if data.get("link") else None
        path: Path | None = None if link else Path(str(data["path"]))
        duration: object = data.get("duration")
        return cls(streams.token_for(link) if link else media.token_for(path),  # type: ignore[arg-type]
                   path, float(duration) if isinstance(duration, (int, float)) else None,
                   bool(data.get("convert")), float(str(data.get("offset", 0))), float(str(data.get("position", 0))),
                   link, str(data["name"]) if data.get("name") else None)


class Caster:
    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.tv: FireTV = FireTV(ADB_PATH, config.tv_host)
        self.now: NowPlaying | None = None
        self._paths: dict[str, Path] = {}
        self._streams: dict[str, streams.Stream] = {}
        self._lock: threading.RLock = threading.RLock()
        self._status: dict[str, object] = {}
        self._status_time: float = 0.0
        self._vlc_checked: bool = False
        self._opened_at: float = -LOADING_SECONDS
        self._opened_start: float = 0.0
        self._loading: bool = False
        self._failed: bool = False
        self._headphones: bool = False
        self._headphones_time: float = -HEADPHONES_CACHE_SECONDS
        self._saved_at: float = 0.0
        self._player_open: bool = False
        self._restore()

    def _restore(self) -> None:
        """Picks up what was playing before the server restarted."""
        try:
            self.now = NowPlaying.from_json(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            return
        if self.now.path is not None:
            self.register(self.now.path)

    def _save(self) -> None:
        self._saved_at = time.monotonic()
        if self.now is None:
            STATE_PATH.unlink(missing_ok=True)
        else:
            STATE_PATH.write_text(json.dumps(self.now.to_json()), encoding="utf-8")

    def reload_config(self) -> None:
        with self._lock:
            self.config = Config.load()
            self.tv.host = self.config.tv_host

    def path_for(self, token: str) -> Path | None:
        return self._paths.get(token)

    def register(self, path: Path) -> str:
        token: str = media.token_for(path)
        self._paths[token] = path.resolve()
        return token

    def library(self) -> list[dict[str, object]]:
        entries: list[media.LibraryEntry] = media.scan_library(
            self.config.video_folders, self.config.library_depth, self.config.library_limit)
        for entry in entries:
            self._paths[entry.token] = entry.path
        return [{"id": e.token, "name": e.path.name, "folder": e.path.parent.name,
                 "size": e.size, "modified": e.modified} for e in entries]

    def play(self, path: Path, convert: bool | None = None, start: float = 0.0) -> None:
        if not path.is_file():
            raise TVError(f"Can't find the file: {path}")
        if path.stat().st_size == 0:
            raise TVError(f"This file is empty (0 bytes), so there is nothing to play: {path.name}")
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            log.info("Unlisted extension %s, trying anyway", path.suffix)
        with self._lock:
            self._check_vlc()
            info: media.VideoInfo = media.probe(path)
            use_conversion: bool = info.needs_conversion if convert is None else convert
            self.now = NowPlaying(self.register(path), path, info.duration, use_conversion, 0.0)
            log.info("Playing %s (convert=%s, codec=%s)", path, use_conversion, info.video_codec)
            self._open(start)

    def play_link(self, page_url: str, start: float = 0.0) -> None:
        """Plays the video on a website page (YouTube and the many other sites yt-dlp knows)."""
        page_url = page_url.strip()
        if not streams.is_link(page_url):
            raise TVError("That isn't a web link.")
        # Looking the page up takes a few seconds: outside the lock, so the remote keeps updating meanwhile.
        stream: streams.Stream = streams.resolve(page_url, self.config.stream_max_height)
        with self._lock:
            self._check_vlc()
            token: str = streams.token_for(page_url)
            self._streams[token] = stream
            self.now = NowPlaying(token, None, stream.duration, False, 0.0, link=page_url, name=stream.title)
            log.info("Playing %s (%s)", page_url, stream.title)
            self._open(start)

    def stream_for(self, token: str) -> streams.Stream | None:
        """The streams for a link, looked up again once their addresses have expired."""
        stream: streams.Stream | None = self._streams.get(token)
        if stream is not None and stream.expired:
            try:
                stream = self._streams[token] = streams.resolve(stream.page_url, self.config.stream_max_height)
            except TVError as exc:
                log.info("Couldn't refresh %s: %s", stream.page_url, exc)
        return stream

    def _check_vlc(self) -> None:
        if not self._vlc_checked:
            if not self.tv.is_installed(VLC_PACKAGE):
                raise TVError("Install 'VLC for Fire' from the Appstore on the TV first.")
            self._vlc_checked = True

    def _open(self, start: float) -> None:
        now: NowPlaying | None = self.now
        if now is None:
            return
        base: str = f"http://{local_ip_toward(self.tv.host)}:{self.config.port}"
        start = max(0.0, start)
        if now.duration is not None:
            start = min(start, max(0.0, now.duration - 1.0))
        variant: str = _fresh_link_variant()
        # An open player only switches links while it is playing (see FireTV.play_url). A new link while
        # it plays the same video (a seek) loads at once; anything else gets a fresh player.
        current: Playback = self.tv.playback()
        restart: bool = current.foreground and (
            current.state != "playing" or not _same_title(current.title, now.title))
        if now.link is not None:
            stream: streams.Stream | None = self.stream_for(now.token)
            if stream is None:  # Played before the server restarted.
                stream = self._streams[now.token] = streams.resolve(now.link, self.config.stream_max_height)
            now.offset = 0.0 if stream.is_live else streams.keyframe_before(stream, start)
            self.tv.play_url(f"{base}/stream/{now.token}.mkv?t={now.offset:.3f}&v={variant}", now.title,
                             restart=restart)
        elif now.convert:
            now.offset = start
            self.tv.play_url(f"{base}/live/{now.token}.ts?t={start:.2f}&v={variant}", now.title, restart=restart)
        else:
            assert now.path is not None
            name: str = urllib.parse.quote(now.path.name)
            self.tv.play_url(f"{base}/media/{now.token}/{name}?v={variant}", now.title, int(start * 1000),
                             restart=restart)
        now.position = start
        self._save()
        self._opened_at, self._opened_start, self._loading, self._failed = time.monotonic(), start, True, False
        self._status_time = 0.0

    def seek(self, position: float) -> None:
        with self._lock:
            if self.now is None:
                raise TVError("Nothing is playing.")
            self._open(position)

    def skip(self, delta: float) -> None:
        """Small skips use VLC's own skip keys, which jump at once without reloading the video."""
        with self._lock:
            if self.now is None:
                raise TVError("Nothing is playing.")
            status: dict[str, object] = self.status()
            position: float | None = (status.get("position") if status.get("title") == self.now.title  # type: ignore[assignment]
                                      else self.now.position)
            presses: int = round(abs(delta) / VLC_SKIP_SECONDS)
            if (self._player_open and not self._loading and not self.now.reopens_to_seek
                    and presses * VLC_SKIP_SECONDS == abs(delta)
                    and 0 < presses <= 30):
                self._key(*([FAST_FORWARD if delta > 0 else REWIND] * presses))
                return
            self.seek((position or 0.0) + delta)

    def pause(self) -> None:
        self._key(PAUSE)

    def resume(self) -> None:
        with self._lock:
            if not self._reopen_if_closed():
                self._key(PLAY)

    def toggle(self) -> None:
        with self._lock:
            if not self._reopen_if_closed():
                self._key(PLAY_PAUSE)

    def _reopen_if_closed(self) -> bool:
        """Brings the video back where it left off when the player was closed behind our back."""
        self.status()
        if self.now is None or self._player_open:
            return False
        self._open(self.now.position)
        return True

    def volume(self, action: str, steps: int = 1) -> None:
        codes: dict[str, Key] = {"up": VOLUME_UP, "down": VOLUME_DOWN, "mute": MUTE}
        if action not in codes:
            raise TVError(f"Unknown volume action: {action}")
        self._key(*([codes[action]] * (1 if action == "mute" else max(1, min(steps, 20)))))

    def headphones(self, on: bool) -> None:
        """Moves TV sound to the Bluetooth headphones (pairing them the first time) or back to the TV."""
        name: str = self.config.headphones_name
        if not name:
            raise TVError("Set \"headphones_name\" in config.json to your headphones' Bluetooth name first.")
        if on:
            answer: dict[str, object] = self._helper("connect", name)
            if answer.get("error") == "not_paired":
                answer = self._helper("pair", name)
        else:
            answer = self._helper("disconnect", name)
        self._headphones_time = -HEADPHONES_CACHE_SECONDS
        self._status_time = 0.0
        if not answer.get("ok"):
            log.info("Headphones %s failed: %s", "on" if on else "off", answer)
            raise TVError(_HEADPHONE_ERRORS.get(str(answer.get("error")), "The headphones didn't switch. Try again."))

    def _helper(self, command: str, name: str) -> dict[str, object]:
        answer: dict[str, object] | None = self.tv.helper(command, name)
        if answer is None:
            if not HELPER_APK.exists():
                raise TVError("The TV helper app isn't built yet. Run: python tv-helper/build.py")
            log.info("Installing the TV helper app")
            self.tv.install(HELPER_APK)
            answer = self.tv.helper(command, name)
        if answer is None:
            raise TVError("The helper app on the TV isn't answering.")
        return answer

    def stop(self) -> None:
        with self._lock:
            self.tv.stop()
            self.now = None
            self._loading = self._failed = False
            self._save()
            self._status_time = 0.0

    def _key(self, *keys: Key) -> None:
        with self._lock:
            self.tv.key(*keys)
            self._status_time = 0.0

    def status(self) -> dict[str, object]:
        with self._lock:
            if time.monotonic() - self._status_time < STATUS_CACHE_SECONDS:
                return self._status
            status: dict[str, object] = {"tv": self.tv.host}
            try:
                playback: Playback = self.tv.playback()
            except TVError as exc:
                status.update(state="offline", message=str(exc))
            else:
                now: NowPlaying | None = self.now
                self._player_open = playback.foreground
                state: str = playback.state if playback.foreground else "idle"
                ours: bool = now is not None and (playback.title is None or _same_title(playback.title, now.title))
                position: float | None = None if playback.position is None else playback.position + (
                    now.offset if now is not None and ours else 0.0)
                if self._loading:
                    loading_for: float = time.monotonic() - self._opened_at
                    self._failed = (now is not None and not playback.foreground and loading_for >= FAILED_AFTER_SECONDS
                                    and _same_title(playback.title, now.title) and playback.state in ("stopped", "error"))
                    self._loading = not self._failed and (loading_for < LOADING_SECONDS or (
                        loading_for < LOAD_TIMEOUT_SECONDS and not (ours and state in ("playing", "paused"))))
                if self._failed and playback.foreground and ours:
                    self._failed = False  # VLC came round after all.
                if now is not None and self._failed:
                    state, position = "error", now.position
                    status["message"] = "The TV couldn't play this video."
                elif now is not None and self._loading:
                    state, position = "buffering", self._opened_start
                elif now is not None and playback.foreground and not ours:
                    # The player shows some other video (or never switched): say so rather than claim ours.
                    now = None
                    status.update(title=playback.title, duration=None, converted=False, position=position)
                elif now is not None and state in ("playing", "paused") and position is not None:
                    now.position = position
                    if time.monotonic() - self._saved_at > POSITION_SAVE_SECONDS:
                        self._save()
                elif now is not None and not playback.foreground:
                    state, position = "paused", now.position
                status["state"] = state
                status["headphones"] = self._headphones_on()
                if now is not None and state != "idle":
                    status.update(title=now.title, duration=now.duration, converted=now.convert, position=position,
                                  link=now.link)
            self._status, self._status_time = status, time.monotonic()
            return status

    def _headphones_on(self) -> bool:
        if time.monotonic() - self._headphones_time >= HEADPHONES_CACHE_SECONDS:
            self._headphones = self.tv.bluetooth_audio_active()
            self._headphones_time = time.monotonic()
        return self._headphones


def _same_title(reported: str | None, title: str) -> bool:
    """Whether VLC's title is the one we sent. VLC percent-decodes the title it is given."""
    return reported is not None and reported in (title, urllib.parse.unquote(title))


def _fresh_link_variant() -> str:
    """A link VLC has never opened. VLC ignores a new start position for the link it is playing, and for
    a link it has played before it resumes from its own saved spot instead of the position we ask for."""
    return f"{time.time_ns():x}"


_HEADPHONE_ERRORS: dict[str, str] = {
    "not_found": "Couldn't find the headphones. Put them in pairing mode, then tap the headphones button again.",
    "no_answer": "The headphones didn't answer. Are they on? If they're connected to two other devices, "
                 "disconnect one of them.",
    "bluetooth_off": "Bluetooth is switched off on the TV.",
    "pair_failed": "Found the headphones, but pairing didn't finish. Put them in pairing mode and try again.",
}
