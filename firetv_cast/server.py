"""Local web server: streams videos to the TV and serves the remote page and its API."""
from __future__ import annotations

import json
import logging
import logging.handlers
import mimetypes
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import media, streams
from .caster import Caster
from .config import LOG_DIR, WEB_DIR, Config
from .firetv import CREATE_NO_WINDOW, TVError

log: logging.Logger = logging.getLogger("firetv_cast")

CHUNK: int = 1024 * 1024
LOCAL_ADDRESSES: frozenset[str] = frozenset({"127.0.0.1", "::1"})
DISCONNECT_ERRORS: tuple[type[BaseException], ...] = (ConnectionError, BrokenPipeError, TimeoutError)

mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("video/mp2t", ".ts")
mimetypes.add_type("video/webm", ".webm")


class CastServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, caster: Caster) -> None:
        self.caster: Caster = caster
        super().__init__(("0.0.0.0", caster.config.port), Handler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server: CastServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        log.debug("%s %s", self.client_address[0], format % args)

    @property
    def caster(self) -> Caster:
        return self.server.caster

    def do_HEAD(self) -> None:
        self._get(head=True)

    def do_GET(self) -> None:
        self._get(head=False)

    def _get(self, head: bool) -> None:
        url: urllib.parse.SplitResult = urllib.parse.urlsplit(self.path)
        parts: list[str] = [p for p in url.path.split("/") if p]
        try:
            if not parts or parts == ["index.html"]:
                self._send_file(WEB_DIR / "index.html", head)
            elif parts[0] == "media" and len(parts) >= 2:
                self._send_media(parts[1], head)
            elif parts[0] in ("live", "stream") and len(parts) == 2:
                query: dict[str, list[str]] = urllib.parse.parse_qs(url.query)
                start: float = float(query.get("t", ["0"])[0])
                if parts[0] == "live":
                    self._send_live(parts[1].removesuffix(".ts"), start, head)
                else:
                    self._send_stream(parts[1].removesuffix(".mkv"), start, head)
            elif parts == ["api", "status"]:
                self._json(self.caster.status())
            elif parts == ["api", "library"]:
                self._json({"items": self.caster.library()})
            elif parts == ["api", "ping"]:
                self._json({"ok": True})
            else:
                self.send_error(404)
        except DISCONNECT_ERRORS:
            self.close_connection = True

    def do_POST(self) -> None:
        parts: list[str] = [p for p in urllib.parse.urlsplit(self.path).path.split("/") if p]
        length: int = int(self.headers.get("Content-Length") or 0)
        try:
            body: dict[str, object] = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            self._json({"ok": False, "message": "Bad request"}, 400)
            return
        if len(parts) != 2 or parts[0] != "api" or parts[1] not in self._actions():
            self._json({"ok": False, "message": "Unknown action"}, 404)
            return
        try:
            self._actions()[parts[1]](body)
        except TVError as exc:
            log.info("Action %s failed: %s", parts[1], exc)
            self._json({"ok": False, "message": str(exc)}, 409)
            return
        except Exception:
            log.exception("Action %s crashed", parts[1])
            self._json({"ok": False, "message": "Something went wrong. See logs\\server.log."}, 500)
            return
        self._json({"ok": True, "status": self.caster.status()})

    def _actions(self) -> dict[str, Callable[[dict[str, object]], None]]:
        c: Caster = self.caster
        return {
            "play": self._play,
            "pause": lambda b: c.pause(),
            "resume": lambda b: c.resume(),
            "toggle": lambda b: c.toggle(),
            "stop": lambda b: c.stop(),
            "seek": lambda b: c.seek(float(str(b["position"]))) if "position" in b
            else c.skip(float(str(b.get("delta", 0)))),
            "volume": lambda b: c.volume(str(b.get("action", "")), int(str(b.get("steps", 1)))),
            "headphones": lambda b: c.headphones(bool(b.get("on", True))),
            "reload": lambda b: c.reload_config(),
        }

    def _play(self, body: dict[str, object]) -> None:
        convert: bool | None = None if body.get("convert") is None else bool(body["convert"])
        start: float = float(str(body.get("start", 0)))
        if "url" in body:
            self.caster.play_link(str(body["url"]), start)
            return
        if "id" in body:
            path: Path | None = self.caster.path_for(str(body["id"]))
            if path is None:
                self.caster.library()  # The page may be older than this server: its ids come from a scan.
                path = self.caster.path_for(str(body["id"]))
            if path is None:
                raise TVError("That video is no longer in the list. Refresh the page.")
        elif "path" in body:
            if self.client_address[0] not in LOCAL_ADDRESSES:
                raise TVError("Playing by file path only works from this PC.")
            path = Path(str(body["path"]))
        else:
            raise TVError("Nothing to play.")
        self.caster.play(path, convert, start)

    def _json(self, data: dict[str, object], code: int = 200) -> None:
        payload: bytes = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path, head: bool) -> None:
        payload: bytes = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not head:
            self.wfile.write(payload)

    def _send_media(self, token: str, head: bool) -> None:
        path: Path | None = self.caster.path_for(token)
        if path is None or not path.is_file():
            self.send_error(404)
            return
        size: int = path.stat().st_size
        start, end = 0, size - 1
        partial: bool = False
        range_header: str | None = self.headers.get("Range")
        if range_header and range_header.startswith("bytes=") and "," not in range_header:
            first, _, last = range_header[6:].strip().partition("-")
            try:
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                partial = True
            except ValueError:
                partial = False
            if partial and (start > end or start >= size):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with path.open("rb") as file:
            file.seek(start)
            remaining: int = end - start + 1
            while remaining > 0:
                chunk: bytes = file.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _send_live(self, token: str, start: float, head: bool) -> None:
        path: Path | None = self.caster.path_for(token)
        if path is None or not path.is_file():
            self.send_error(404)
            return
        self._send_ffmpeg(media.conversion_command(path, start, self.caster.config.convert_quality_crf),
                          "video/mp2t", head)

    def _send_stream(self, token: str, start: float, head: bool) -> None:
        stream: streams.Stream | None = self.caster.stream_for(token)
        if stream is None:
            self.send_error(404)
            return
        self._send_ffmpeg(streams.remux_command(stream, start), "video/x-matroska", head)

    def _send_ffmpeg(self, command: list[str], content_type: str, head: bool) -> None:
        """Streams ffmpeg's output until it ends or the TV hangs up."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if head:
            return
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=_log_stderr, args=(process,), daemon=True).start()
        try:
            assert process.stdout is not None
            shutil.copyfileobj(process.stdout, self.wfile, 256 * 1024)
        finally:
            process.kill()
            process.wait()


def _log_stderr(process: subprocess.Popen[bytes]) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        log.warning("ffmpeg: %s", line.decode("utf-8", "replace").rstrip())


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    handler: logging.Handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "server.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root: logging.Logger = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())


def run() -> None:
    setup_logging()
    caster: Caster = Caster(Config.load())
    try:
        server: CastServer = CastServer(caster)
    except OSError:
        log.info("Port %d is busy: the server is probably already running.", caster.config.port)
        return
    threading.Thread(target=_warm_up, args=(caster,), daemon=True).start()
    threading.Thread(target=_keep_shell_helper, args=(caster,), daemon=True).start()
    threading.Thread(target=streams.keep_ytdlp_updated, daemon=True).start()
    log.info("Serving on port %d for TV %s", caster.config.port, caster.config.tv_host)
    server.serve_forever()


def _warm_up(caster: Caster) -> None:
    try:
        caster.tv.warm_up()
    except TVError as exc:
        log.info("TV not reachable yet: %s", exc)


SHELL_HELPER_CHECK_SECONDS: float = 120.0


def _keep_shell_helper(caster: Caster) -> None:
    """Keeps the TV home screen's shell helper running; it ends whenever the TV restarts."""
    while True:
        try:
            caster.tv.ensure_shell_helper()
        except TVError:
            pass
        time.sleep(SHELL_HELPER_CHECK_SECONDS)
