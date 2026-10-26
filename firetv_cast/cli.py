"""`tv` command line. Talks to the background server, starting it when needed."""
from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ROOT, Config, pythonw_path
from .firetv import local_ip_toward
from .streams import is_link

DETACHED_PROCESS: int = 0x00000008
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
CREATE_NO_WINDOW: int = 0x08000000


class CliError(Exception):
    pass


def _request(config: Config, path: str, body: dict[str, object] | None = None, timeout: float = 90.0) -> dict[str, object]:
    url: str = f"http://127.0.0.1:{config.port}{path}"
    data: bytes | None = None if body is None else json.dumps(body).encode("utf-8")
    request: urllib.request.Request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            message: object = json.loads(exc.read() or b"{}").get("message", exc)
        except (json.JSONDecodeError, AttributeError):
            message = exc
        raise CliError(str(message)) from exc


def ensure_server(config: Config) -> None:
    try:
        _request(config, "/api/ping", timeout=1.5)
        return
    except (urllib.error.URLError, OSError, CliError):
        pass
    subprocess.Popen(
        [str(pythonw_path()), str(ROOT / "run_server.pyw")], cwd=str(ROOT), close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )
    deadline: float = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        time.sleep(0.25)
        try:
            _request(config, "/api/ping", timeout=1.0)
            return
        except (urllib.error.URLError, OSError, CliError):
            continue
    raise CliError("The TV server did not start. See logs\\server.log.")


def parse_time(text: str) -> float:
    """Seconds from "90", "1:30" or "1:02:03"."""
    total: float = 0.0
    for piece in text.split(":"):
        total = total * 60 + float(piece)
    return total


def format_time(seconds: object) -> str:
    if not isinstance(seconds, (int, float)):
        return "?"
    s: int = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def describe(status: dict[str, object]) -> str:
    state: str = str(status.get("state", "?"))
    if state == "offline":
        return f"TV offline: {status.get('message', '')}"
    if "title" not in status:
        return f"TV {state}, nothing of ours playing"
    return (f"{state}: {status['title']}  {format_time(status.get('position'))} / "
            f"{format_time(status.get('duration'))}" + ("  (converted)" if status.get("converted") else ""))


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="tv", description="Play videos from this PC on the Fire TV.")
    sub = parser.add_subparsers(dest="command", required=True)
    play = sub.add_parser("play", help="play a video file, or a video page link (YouTube and more), on the TV")
    play.add_argument("file", help="a video file or a web link")
    play.add_argument("--convert", action="store_true", help="convert on the fly (for files the TV can't play)")
    play.add_argument("--start", default="0", help="start time, like 1:30")
    play.add_argument("--gui", action="store_true", help="show errors in a popup (used by the right-click menu)")
    for name, text in (("pause", "pause"), ("resume", "resume"), ("toggle", "pause or resume"),
                       ("stop", "stop and go to the TV home screen"), ("status", "show what is playing"),
                       ("list", "list recent videos"), ("url", "print the remote page address"),
                       ("find", "search the network for the Fire TV and remember it"),
                       ("server", "run the server in this window (normally it runs in the background)")):
        sub.add_parser(name, help=text)
    seek = sub.add_parser("seek", help="jump to a time (1:30) or skip (+30, -10)")
    seek.add_argument("time")
    phones = sub.add_parser("headphones", help="send TV sound to the Bluetooth headphones, or back")
    phones.add_argument("action", choices=["on", "off"])
    vol = sub.add_parser("vol", help="TV volume")
    vol.add_argument("action", choices=["up", "down", "mute"])
    vol.add_argument("steps", nargs="?", type=int, default=1)
    folder = sub.add_parser("folder", help="folders shown on the remote page")
    folder.add_argument("action", choices=["list", "add", "remove"])
    folder.add_argument("path", nargs="?")
    return parser


def run(args: argparse.Namespace) -> str:
    config: Config = Config.load()
    command: str = args.command
    if command == "server":
        from .server import run as run_server
        run_server()
        return ""
    if command == "find":
        return _find(config)
    if command == "folder":
        return _folder(config, args.action, args.path)
    if command == "url":
        return f"http://{local_ip_toward(config.tv_host or '192.168.0.1')}:{config.port}/"
    ensure_server(config)
    if command == "status":
        return describe(_request(config, "/api/status"))
    if command == "list":
        items: list[dict[str, object]] = _request(config, "/api/library")["items"]  # type: ignore[assignment]
        return "\n".join(str(i["name"]) for i in items[:40]) or "No videos found."
    body: dict[str, object] = {}
    if command == "play":
        body = {"start": parse_time(args.start)}
        if is_link(args.file):
            body["url"] = args.file.strip()
        else:
            body["path"] = str(Path(args.file).resolve())
        if args.convert:
            body["convert"] = True
    elif command == "seek":
        text: str = args.time
        body = {"delta": float(text)} if text[0] in "+-" else {"position": parse_time(text)}
    elif command == "headphones":
        body = {"on": args.action == "on"}
    elif command == "vol":
        body = {"action": args.action, "steps": args.steps}
        command = "volume"
    result: dict[str, object] = _request(config, f"/api/{command}", body)
    return describe(result.get("status", {}))  # type: ignore[arg-type]


def _find(config: Config) -> str:
    from .firetv import find_fire_tvs
    found: list[tuple[str, str]] = find_fire_tvs()
    if not found:
        return "No Fire TV found. Is it switched on and on the same Wi-Fi?"
    config.tv_host = found[0][0]
    config.save()
    try:
        _request(config, "/api/reload", {}, timeout=3.0)
    except (urllib.error.URLError, OSError, CliError):
        pass
    return "\n".join(f"{address}  {name}" for address, name in found) + f"\nUsing {config.tv_host}"


def _folder(config: Config, action: str, path: str | None) -> str:
    if action != "list":
        if not path:
            raise CliError("Give a folder path.")
        resolved: str = str(Path(path).expanduser().resolve())
        if action == "add" and resolved not in config.video_folders:
            config.video_folders.append(resolved)
        elif action == "remove":
            config.video_folders = [f for f in config.video_folders
                                    if str(Path(f).expanduser().resolve()).lower() != resolved.lower()]
        config.save()
        try:
            _request(config, "/api/reload", {}, timeout=3.0)
        except (urllib.error.URLError, OSError, CliError):
            pass
    return "\n".join(config.video_folders) or "No folders."


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace = build_parser().parse_args(argv)
    try:
        output: str = run(args)
    except (CliError, OSError) as exc:
        message: str = str(exc) or exc.__class__.__name__
        if getattr(args, "gui", False):
            ctypes.windll.user32.MessageBoxW(None, message, "Send to TV", 0x30)
        elif sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1
    if output and sys.stdout is not None:
        print(output)
    return 0
