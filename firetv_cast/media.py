"""Video files: library listing, probing, and on-the-fly conversion for the TV."""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import VIDEO_EXTENSIONS
from .firetv import CREATE_NO_WINDOW

log: logging.Logger = logging.getLogger(__name__)

PLAYABLE_VIDEO_CODECS: frozenset[str] = frozenset({
    "h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "mpeg1video",
    "vc1", "wmv3", "msmpeg4v3", "mjpeg", "theora", "h263",
})
"""Codecs VLC for Fire plays directly. Anything else is converted on the way to the TV."""


@dataclass(frozen=True)
class VideoInfo:
    duration: float | None
    video_codec: str | None
    audio_codec: str | None

    @property
    def needs_conversion(self) -> bool:
        return self.video_codec is not None and self.video_codec not in PLAYABLE_VIDEO_CODECS


def token_for(path: Path) -> str:
    return hashlib.sha1(os.path.normcase(str(path.resolve())).encode("utf-8")).hexdigest()[:16]


def probe(path: Path) -> VideoInfo:
    stat: os.stat_result = path.stat()
    return _probe(path, stat.st_size, stat.st_mtime)


@functools.lru_cache(maxsize=256)
def _probe(path: Path, size: int, modified: float) -> VideoInfo:
    """Cached per file version: ffprobe on a large file takes most of a second."""
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        data: dict[str, object] = json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        log.exception("ffprobe failed for %s", path)
        return VideoInfo(None, None, None)
    streams: list[dict[str, object]] = list(data.get("streams", []))  # type: ignore[arg-type]
    video: list[str] = [str(s.get("codec_name")) for s in streams
                        if s.get("codec_type") == "video" and not _is_cover_art(s)]
    audio: list[str] = [str(s.get("codec_name")) for s in streams if s.get("codec_type") == "audio"]
    fmt: dict[str, object] = data.get("format", {})  # type: ignore[assignment]
    duration: float | None = None
    try:
        duration = float(str(fmt.get("duration")))
    except ValueError:
        pass
    return VideoInfo(duration, video[0] if video else None, audio[0] if audio else None)


def _is_cover_art(stream: dict[str, object]) -> bool:
    disposition: dict[str, int] = stream.get("disposition", {})  # type: ignore[assignment]
    return bool(disposition.get("attached_pic"))


@functools.cache
def _hardware_encoder() -> str | None:
    """A GPU H.264 encoder ffmpeg can use on this PC, if any."""
    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=black:s=256x256:d=0.2", "-c:v", encoder, "-f", "null", "-"],
                capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            log.info("Using %s for conversion", encoder)
            return encoder
    return None


def conversion_command(path: Path, start: float, crf: int) -> list[str]:
    """ffmpeg arguments that stream the file as H.264/AAC MPEG-TS on stdout, from `start` seconds."""
    encoder: str | None = _hardware_encoder()
    video: list[str]
    if encoder == "h264_nvenc":
        video = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf + 3), "-b:v", "0"]
    elif encoder is not None:
        video = ["-c:v", encoder, "-b:v", "12M"]
    else:
        video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf)]
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-vf", "scale='min(1920,iw)':-2,format=yuv420p", *video,
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-f", "mpegts", "pipe:1",
    ]


@dataclass(frozen=True)
class LibraryEntry:
    token: str
    path: Path
    size: int
    modified: float


def scan_library(folders: list[str], depth: int, limit: int) -> list[LibraryEntry]:
    entries: list[LibraryEntry] = []
    for folder in folders:
        root: Path = Path(os.path.expandvars(os.path.expanduser(folder)))
        if root.is_dir():
            _walk(root, depth, entries)
    entries.sort(key=lambda e: e.modified, reverse=True)
    return entries[:limit]


def _walk(folder: Path, depth: int, out: list[LibraryEntry]) -> None:
    try:
        children: list[os.DirEntry[str]] = list(os.scandir(folder))
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir(follow_symlinks=False):
                if depth > 0 and not child.name.startswith("."):
                    _walk(Path(child.path), depth - 1, out)
            elif Path(child.name).suffix.lower() in VIDEO_EXTENSIONS:
                stat: os.stat_result = child.stat()
                if stat.st_size == 0:  # Placeholders left by downloads: nothing to play.
                    continue
                path: Path = Path(child.path)
                out.append(LibraryEntry(token_for(path), path, stat.st_size, stat.st_mtime))
        except OSError:
            continue
