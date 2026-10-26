"""Videos from website links: yt-dlp finds the streams behind a page, ffmpeg joins them for VLC.

Sites like YouTube send picture and sound as separate streams. ffmpeg copies both into one Matroska stream
without re-encoding, so they keep the site's own timestamps and cannot drift apart.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass

from .config import COOKIES_PATH, YTDLP_PYTHON
from .firetv import CREATE_NO_WINDOW, TVError

log: logging.Logger = logging.getLogger(__name__)

URL_LIFETIME_SECONDS: float = 3 * 3600
"""Stream addresses from sites expire (YouTube's after about six hours); older ones are looked up again."""
UPDATE_EVERY_SECONDS: float = 24 * 3600
RETRY_UPDATE_AFTER_SECONDS: float = 3600
"""A failing link triggers a yt-dlp update at most this often: sites change, and yt-dlp follows."""
NOT_A_VIDEO: str = "That link isn't a video page this can play."
PROTOCOLS: str = "https,http,tls,tcp,crypto"
"""Network only: a page must never get ffmpeg to read files on this PC."""


@dataclass(frozen=True)
class StreamInput:
    url: str
    headers: dict[str, str]


@dataclass
class Stream:
    page_url: str
    title: str
    duration: float | None
    is_live: bool
    inputs: list[StreamInput]
    """One input with picture and sound, or picture then sound."""
    resolved_at: float

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.resolved_at > URL_LIFETIME_SECONDS


def is_link(text: str) -> bool:
    return urllib.parse.urlsplit(text.strip()).scheme in ("http", "https")


def token_for(page_url: str) -> str:
    return hashlib.sha1(page_url.encode("utf-8")).hexdigest()[:16]


def resolve(page_url: str, max_height: int) -> Stream:
    """The streams behind a page. Updates yt-dlp and tries again once when a site has changed under it."""
    try:
        return _resolve(page_url, max_height)
    except TVError as exc:
        if str(exc) == NOT_A_VIDEO or not update_ytdlp(retry=True):
            raise
        return _resolve(page_url, max_height)


def _format(max_height: int) -> str:
    # AV1 is left out (not every TV decodes it), and so are DASH fragment lists, which ffmpeg can't open.
    picture: str = f"[height<={max_height}][vcodec!^=av01][protocol!*=dash]"
    sound: str = "[protocol!*=dash]"
    return f"bv*{picture}+ba[acodec^=mp4a]{sound}/bv*{picture}+ba{sound}/b{picture}/b{sound}"


def _resolve(page_url: str, max_height: int) -> Stream:
    if not YTDLP_PYTHON.exists():
        raise TVError("Playing links needs yt-dlp. Run install.py once to set it up.")
    command: list[str] = [str(YTDLP_PYTHON), "-m", "yt_dlp", "--js-runtimes", "node", "--no-warnings",
                          "--no-playlist", "-f", _format(max_height), "-J"]
    if COOKIES_PATH.exists():
        command += ["--cookies", str(COOKIES_PATH)]
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            [*command, "--", page_url], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise TVError("The site took too long to answer. Try again.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        reason: str = _error_reason(result.stderr)
        log.info("yt-dlp failed for %s: %s", page_url, result.stderr.strip()[-500:])
        raise TVError(reason)
    info: dict[str, object] = json.loads(result.stdout)
    formats: list[dict[str, object]] = list(info.get("requested_formats") or [info])  # type: ignore[arg-type]
    inputs: list[StreamInput] = []
    for f in formats:
        url: str = str(f.get("url", ""))
        if not is_link(url):
            raise TVError("That site's video can't be streamed this way.")
        headers: dict[str, str] = {str(k): str(v) for k, v in dict(f.get("http_headers") or {}).items()}  # type: ignore[call-overload]
        inputs.append(StreamInput(url, headers))
    duration: object = info.get("duration")
    log.info("Resolved %s: %s", page_url, " + ".join(str(f.get("format")) for f in formats))
    return Stream(page_url, str(info.get("title") or page_url), float(duration) if isinstance(duration, (int, float))
                  else None, bool(info.get("is_live")), inputs, time.monotonic())


def _error_reason(stderr: str) -> str:
    errors: list[str] = [line for line in stderr.splitlines() if line.startswith("ERROR:")]
    detail: str = errors[-1].removeprefix("ERROR:").strip() if errors else "yt-dlp didn't say why"
    detail = re.sub(r"^\[[^\]]+\]\s*(?:[\w-]+:\s*)?", "", detail)  # "[youtube] abc123: Video unavailable"
    if "Unsupported URL" in detail:
        return NOT_A_VIDEO
    if any(word in detail.lower() for word in ("sign in", "login", "log in", "members", "private")):
        return f"That video needs a login: {detail}"
    return f"Couldn't get the video from that link: {detail}"


def keyframe_before(stream: Stream, position: float) -> float:
    """Where the picture can start at or before `position`. Starting the sound at the same spot keeps them
    together: copied video can only start on a keyframe, sound can start anywhere."""
    if position <= 0:
        return 0.0
    first: StreamInput = stream.inputs[0]
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["ffprobe", "-v", "error", *_input_options(first), "-read_intervals", f"{position:.3f}%+#1",
             "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", first.url],
            capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        found: float = float(result.stdout.split(",")[0].strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        log.info("No keyframe found near %.1f, starting there", position)
        return position
    return found if 0 <= found <= position else position


def remux_command(stream: Stream, start: float) -> list[str]:
    """ffmpeg arguments that write the stream as Matroska on stdout, from `start` seconds."""
    command: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    for item in stream.inputs:
        command += _input_options(item)
        if start > 0:
            command += ["-ss", f"{start:.3f}"]
        command += ["-i", item.url]
    if len(stream.inputs) > 1:
        command += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        command += ["-map", "0:v:0", "-map", "0:a:0?"]
    return [*command, "-c", "copy", "-f", "matroska", "-live", "1", "pipe:1"]


def _input_options(item: StreamInput) -> list[str]:
    options: list[str] = ["-protocol_whitelist", PROTOCOLS]
    if item.headers:
        options += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in item.headers.items())]
    return options


_update_lock: threading.Lock = threading.Lock()
_updated_at: float = -UPDATE_EVERY_SECONDS


def update_ytdlp(retry: bool = False) -> bool:
    """Updates yt-dlp once a day, or sooner when a link failed. Whether an update ran."""
    global _updated_at
    with _update_lock:
        since: float = time.monotonic() - _updated_at
        if not YTDLP_PYTHON.exists() or since < (RETRY_UPDATE_AFTER_SECONDS if retry else UPDATE_EVERY_SECONDS):
            return False
        _updated_at = time.monotonic()
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                [str(YTDLP_PYTHON), "-m", "pip", "install", "-q", "-U", "--disable-pip-version-check",
                 "yt-dlp[default]"], capture_output=True, text=True, timeout=300, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            log.warning("Updating yt-dlp failed to run")
            return False
        if result.returncode != 0:
            log.warning("Updating yt-dlp failed: %s", result.stderr.strip()[-300:])
            return False
        log.info("yt-dlp is up to date")
        return True


def keep_ytdlp_updated() -> None:
    while True:
        update_ytdlp()
        time.sleep(3600)
