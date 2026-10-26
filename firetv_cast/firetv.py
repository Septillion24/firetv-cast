"""Fire TV control over adb (network debugging) and VLC for Fire."""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import logging
import plistlib
import queue
import re
import shlex
import socket
import struct
import subprocess
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

log: logging.Logger = logging.getLogger(__name__)

CREATE_NO_WINDOW: int = 0x08000000
ADB_PORT: int = 5555

VLC_PACKAGE: str = "org.videolan.vlc"
VLC_PLAYER: str = f"{VLC_PACKAGE}/{VLC_PACKAGE}.gui.video.VideoPlayerActivity"
HELPER_PACKAGE: str = "com.firetvcast.helper"
"""Our own small TV app (tv-helper/) for what Fire OS has no shell command for: Bluetooth audio, and the
ad-free home screen that replaces Amazon's."""
SHELL_HELPER_PORT: int = 58421
"""Loopback port of the helper's ShellHelper, which lets that home screen open without Android's
5-second wait after the Home button. It has to be started from adb, and ends when the TV restarts."""

KEYBOARD_DEVICE_NAME: str = "amzkeyboard"
"""The Fire TV's built-in virtual keyboard. Writing key events to it is much faster than
`input keyevent`, which starts a Java program on the TV for every press."""
EV_SYN: int = 0
EV_KEY: int = 1


@dataclass(frozen=True)
class Key:
    android: int
    """Android key code, for the slow `input keyevent` fallback."""
    linux: int
    """Linux input code that the keyboard device's key layout maps to the same key."""


HOME: Key = Key(3, 172)
VOLUME_UP: Key = Key(24, 115)
VOLUME_DOWN: Key = Key(25, 114)
PLAY_PAUSE: Key = Key(85, 164)
FAST_FORWARD: Key = Key(90, 208)
"""VLC skips 10 seconds forward."""
REWIND: Key = Key(89, 168)
"""VLC skips 10 seconds back."""
PLAY: Key = Key(126, 200)
PAUSE: Key = Key(127, 201)
MUTE: Key = Key(164, 113)
WAKEUP: Key = Key(224, 143)

_STATE_NAMES: dict[int, str] = {
    0: "idle", 1: "stopped", 2: "paused", 3: "playing", 4: "playing", 5: "playing",
    6: "buffering", 7: "error", 8: "buffering",
}


class TVError(Exception):
    """A problem worth showing to the person, in plain words."""


@dataclass
class Playback:
    state: str
    """idle, stopped, paused, playing, buffering or error."""
    position: float | None
    """Seconds into the stream VLC is playing, when it reports one."""
    foreground: bool
    """Whether the VLC player is the app on screen."""
    title: str | None = None
    """Title of the video VLC has loaded, which tells whether it actually switched to the one we sent."""


class _ShellSession:
    """One `adb shell` kept open, so each command costs a few milliseconds instead of a new adb process."""

    def __init__(self, adb: Path, serial: str) -> None:
        self._process: subprocess.Popen[bytes] = subprocess.Popen(
            [str(adb), "-s", serial, "shell"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW,
        )
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self._process.stdout is not None
        for line in iter(self._process.stdout.readline, b""):
            self._lines.put(line)
        self._lines.put(None)

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def run(self, command: str, timeout: float) -> str | None:
        """The command's output, or None when the session broke and should be replaced."""
        marker: str = f"__FTVC_{uuid.uuid4().hex}__"
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(f"{command}\necho; echo {marker}\n".encode("utf-8"))
            self._process.stdin.flush()
        except OSError:
            return None
        deadline: float = time.monotonic() + timeout
        output: list[str] = []
        while True:
            try:
                line: bytes | None = self._lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise TVError("The TV is not answering. Is it switched on?") from None
            if line is None:
                return None
            text: str = line.decode("utf-8", "replace").rstrip("\r\n")
            if text == marker:
                return "\n".join(output[:-1] if output and not output[-1] else output)
            output.append(text)

    def close(self) -> None:
        self._process.kill()


class FireTV:
    def __init__(self, adb: Path, host: str) -> None:
        self.adb: Path = adb
        self.host: str = host
        self._lock: threading.Lock = threading.Lock()
        self._session: _ShellSession | None = None
        self._keyboard: str | None = None
        self._keyboard_checked: bool = False

    @property
    def serial(self) -> str:
        return f"{self.host}:{ADB_PORT}"

    def _run(self, args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [str(self.adb), *args], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise TVError("The TV is not answering. Is it switched on?") from exc

    def connect(self) -> None:
        if not self.host:
            raise TVError("No TV set up yet. Run: tv find")
        result: subprocess.CompletedProcess[str] = self._run(["connect", self.serial], timeout=10.0)
        output: str = (result.stdout + result.stderr).lower()
        if "authenticate" in output or "unauthorized" in output:
            raise TVError("Accept 'Allow USB debugging' on the TV (tick 'Always allow'), then try again.")
        if "connected" not in output or "failed" in output or "cannot" in output:
            raise TVError("Can't reach the TV. Is it on, and is ADB Debugging switched on?")
        state: str = self._run(["-s", self.serial, "get-state"], timeout=5.0).stdout.strip()
        if state != "device":
            if "unauthorized" in state or not state:
                raise TVError("Accept 'Allow USB debugging' on the TV (tick 'Always allow'), then try again.")
            raise TVError(f"The TV connection is not ready ({state}). Try again in a moment.")

    def shell(self, command: str, timeout: float = 15.0) -> str:
        with self._lock:
            for _ in range(2):
                if self._session is None or not self._session.alive:
                    self.connect()
                    self._session = _ShellSession(self.adb, self.serial)
                output: str | None = self._session.run(command, timeout)
                if output is not None:
                    return output
                self._session.close()
                self._session = None
            raise TVError("Lost the connection to the TV.")

    def warm_up(self) -> None:
        """Opens the connection and finds the keyboard device ahead of the first button press."""
        self._keyboard_device()

    def key(self, *keys: Key) -> None:
        device: str | None = self._keyboard_device()
        if device is None:
            self.shell("input keyevent " + " ".join(str(k.android) for k in keys))
            return
        data: bytes = b"".join(_input_event(EV_KEY, k.linux, value) + _input_event(EV_SYN, 0, 0)
                               for k in keys for value in (1, 0))
        self.shell(f"printf '{''.join(f'\\x{b:02x}' for b in data)}' > {device}")

    def _keyboard_device(self) -> str | None:
        if not self._keyboard_checked:
            listing: str = self.shell("getevent -pl 2>/dev/null | grep -E 'add device|name:'")
            device: str | None = None
            for line in listing.splitlines():
                found: re.Match[str] | None = re.search(r"add device \d+: (\S+)", line)
                if found:
                    device = found.group(1)
                elif f'"{KEYBOARD_DEVICE_NAME}"' in line and device:
                    self._keyboard = device
                    break
            self._keyboard_checked = True
            log.info("Key events go to %s", self._keyboard or "input keyevent (slow)")
        return self._keyboard

    def is_installed(self, package: str) -> bool:
        return f"package:{package}" in self.shell(f"pm list packages {shlex.quote(package)}").split()

    def play_url(self, url: str, title: str, position_ms: int = 0, restart: bool = False) -> None:
        """Opens the stream in VLC. A position of 0 always starts from the beginning.

        An open player only takes a new link while it is playing: paused, opening or buffering, it keeps
        the old video (VideoPlayerActivity.onNewIntent). `restart` closes VLC first so the link always loads.
        """
        args: list[str] = [
            "am", "start", *(["-S"] if restart else []), "-a", "android.intent.action.VIEW", "-n", VLC_PLAYER,
            "-d", url, "-t", "video/*", "--es", "title", title,
        ]
        if position_ms > 0:
            args += ["--ez", "from_start", "false", "--el", "position", str(position_ms)]
        else:
            args += ["--ez", "from_start", "true"]
        self.key(WAKEUP)
        output: str = self.shell(" ".join(shlex.quote(a) for a in args))
        if re.search(r"^Error", output, re.M):
            log.warning("am start said: %s", output.strip())
            raise TVError("VLC would not open. Is 'VLC for Fire' installed on the TV?")

    def install(self, apk: Path) -> None:
        result: subprocess.CompletedProcess[str] = self._run(["-s", self.serial, "install", "-r", str(apk)], 120.0)
        if "Success" not in result.stdout:
            log.warning("Installing %s failed: %s %s", apk.name, result.stdout, result.stderr)
            raise TVError("Couldn't install the helper app on the TV.")

    def helper(self, command: str, name: str = "", timeout: float = 70.0) -> dict[str, object] | None:
        """Runs a helper app command. None means the helper is not installed."""
        output: str = self.shell(
            f"am broadcast -n {HELPER_PACKAGE}/.Command -f 0x20 --es cmd {shlex.quote(command)} "
            f"--es name {shlex.quote(name)}", timeout)
        found: re.Match[str] | None = re.search(r'data="(.*)"\s*$', output, re.S)
        if not found:
            return None
        return json.loads(found.group(1))

    def ensure_shell_helper(self) -> None:
        """Starts the helper app's ShellHelper on the TV unless it is already listening."""
        # A subshell, so its `exit` doesn't end the kept-open adb shell.
        output: str = self.shell(
            f"(cat /proc/net/tcp /proc/net/tcp6 | grep -q ':{SHELL_HELPER_PORT:04X} [0-9A-F]*:0000 0A' && exit; "
            f"apk=$(pm path {HELPER_PACKAGE} | cut -d: -f2); [ -n \"$apk\" ] || exit; "
            f"uid=$(pm list packages -U {HELPER_PACKAGE} | grep -o 'uid:[0-9]*' | cut -d: -f2); "
            f"CLASSPATH=$apk setsid app_process /system/bin {HELPER_PACKAGE}.ShellHelper $uid "
            "</dev/null >/dev/null 2>&1 & echo started)")
        if "started" in output:
            log.info("Started the home screen's shell helper on the TV")

    def bluetooth_audio_active(self) -> bool:
        """Whether TV sound currently goes to Bluetooth headphones or speakers."""
        output: str = self.shell("dumpsys bluetooth_manager 2>/dev/null | grep -m 1 mActiveDevice")
        return re.search(r"mActiveDevice: ([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", output) is not None

    def stop(self) -> None:
        self.shell(f"am force-stop {VLC_PACKAGE}")
        self.key(HOME)

    def playback(self) -> Playback:
        output: str = self.shell(
            "dumpsys media_session; echo __UPTIME__; cat /proc/uptime; echo __TOP__; "
            "dumpsys activity activities | grep -m 1 -E 'mResumedActivity|topResumedActivity'"
        )
        sessions, _, rest = output.partition("__UPTIME__")
        uptime_text, _, top = rest.partition("__TOP__")
        foreground: bool = VLC_PLAYER.split("/")[1].split(".")[-1] in top and VLC_PACKAGE in top
        block: str = _session_block(sessions, VLC_PACKAGE)
        title: str | None = _session_title(block)
        match: re.Match[str] | None = re.search(
            r"state=PlaybackState \{state=(\d+), position=(-?\d+),.*?speed=(-?[\d.]+), updated=(\d+)", block)
        if not match:
            return Playback("idle", None, foreground, title)
        state: str = _STATE_NAMES.get(int(match.group(1)), "idle")
        position_ms: float = float(match.group(2))
        if state == "playing":
            now_ms: float = float(uptime_text.split()[0]) * 1000.0 if uptime_text.split() else 0.0
            elapsed: float = max(0.0, now_ms - float(match.group(4)))
            position_ms += elapsed * float(match.group(3))
        return Playback(state, max(0.0, position_ms / 1000.0) if position_ms >= 0 else None, foreground, title)


def _input_event(kind: int, code: int, value: int) -> bytes:
    """A Linux input_event as the TV's 32-bit kernel expects it. The kernel stamps the time itself."""
    return struct.pack("<IIHHi", 0, 0, kind, code, value)


def _session_block(dump: str, package: str) -> str:
    """The media_session dump lines that belong to one app's session."""
    lines: list[str] = dump.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"package={package}":
            end: int = i + 1
            while end < len(lines) and not re.match(r"\s{4}\S", lines[end]) and "package=" not in lines[end]:
                end += 1
            return "\n".join(lines[i:end])
    return ""


def _session_title(block: str) -> str | None:
    """The title in a session's metadata line: `description=<title>, <subtitle>, <description>`."""
    found: re.Match[str] | None = re.search(r"^\s*metadata:size=\d+, description=(.*)$", block, re.M)
    if not found:
        return None
    parts: list[str] = found.group(1).rsplit(", ", 2)
    title: str = parts[0] if len(parts) == 3 else found.group(1)
    return None if title in ("", "null") else title


def local_ip_toward(host: str) -> str:
    """This PC's address on the network the TV is on."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, ADB_PORT))
        return str(s.getsockname()[0])


def find_fire_tvs(subnet_hint: str | None = None) -> list[tuple[str, str]]:
    """Scans the local network for Amazon devices. Returns (address, name) pairs."""
    own: str = local_ip_toward(subnet_hint or "192.168.0.1")
    network: ipaddress.IPv4Network = ipaddress.ip_network(f"{own}/24", strict=False)

    def probe(address: str) -> tuple[str, str] | None:
        name: str | None = _airplay_name(address)
        if name is not None:
            return address, name
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex((address, ADB_PORT)) == 0:
                return address, "Android device with ADB on"
        return None

    with concurrent.futures.ThreadPoolExecutor(64) as pool:
        found = pool.map(probe, [str(h) for h in network.hosts() if str(h) != own])
        return [f for f in found if f is not None]


def _airplay_name(address: str) -> str | None:
    with socket.socket() as s:
        s.settimeout(0.4)
        if s.connect_ex((address, 7000)) != 0:
            return None
    try:
        with urllib.request.urlopen(f"http://{address}:7000/info", timeout=2) as response:
            info: dict[str, object] = plistlib.loads(response.read())
    except Exception:
        return None
    if str(info.get("manufacturer", "")).lower() != "amazon":
        return None
    return f"{info.get('name', 'Fire TV')} ({info.get('model', '?')})"
