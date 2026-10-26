"""Sets up Send to TV for this Windows user: right-click menu, start with Windows, `tv` command.

Run `install.py` to set up, `install.py --uninstall` to remove everything it added.
"""
from __future__ import annotations

import ctypes
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import winreg
import zipfile
from pathlib import Path

from firetv_cast.config import ADB_PATH, ROOT, VIDEO_EXTENSIONS, YTDLP_PYTHON, pythonw_path

VERB_KEY: str = "FireTVCast"
MENU_TEXT: str = "Send to TV"
ICON_PATH: Path = ROOT / "tv.ico"
CMD_PATH: Path = ROOT / "tv.cmd"
STARTUP_LINK: Path = (Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu"
                      / "Programs" / "Startup" / "Send to TV server.lnk")
PLATFORM_TOOLS_URL: str = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
HWND_BROADCAST: int = 0xFFFF
WM_SETTINGCHANGE: int = 0x001A


def _verb_path(extension: str) -> str:
    return rf"Software\Classes\SystemFileAssociations\{extension}\shell\{VERB_KEY}"


def install_menu() -> None:
    command: str = f'"{pythonw_path()}" "{ROOT / "tv.py"}" play --gui "%1"'
    for extension in sorted(VIDEO_EXTENSIONS):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _verb_path(extension)) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, MENU_TEXT)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(ICON_PATH))
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Single")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _verb_path(extension) + r"\command") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)


def uninstall_menu() -> None:
    for extension in sorted(VIDEO_EXTENSIONS):
        for sub in (r"\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _verb_path(extension) + sub)
            except FileNotFoundError:
                pass


def install_startup() -> None:
    script: str = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); "
        "$s.TargetPath = $env:TARGET; $s.Arguments = $env:ARGS; $s.WorkingDirectory = $env:WORKDIR; "
        "$s.IconLocation = $env:ICON; $s.Description = 'Streams videos to the Fire TV'; $s.Save()"
    )
    env: dict[str, str] = {
        **os.environ, "LINK": str(STARTUP_LINK), "TARGET": str(pythonw_path()),
        "ARGS": f'"{ROOT / "run_server.pyw"}"', "WORKDIR": str(ROOT), "ICON": str(ICON_PATH),
    }
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)


def install_command() -> None:
    CMD_PATH.write_text(f'@"{sys.executable}" "%~dp0tv.py" %*\r\n', encoding="utf-8")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        parts: list[str] = [p for p in str(value).split(";") if p]
        if str(ROOT).lower() not in (p.lower().rstrip("\\") for p in parts):
            winreg.SetValueEx(key, "Path", 0, kind, ";".join([*parts, str(ROOT)]))
            _broadcast_environment_change()


def uninstall_command() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return
        parts: list[str] = [p for p in str(value).split(";") if p and p.lower().rstrip("\\") != str(ROOT).lower()]
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(parts))
    _broadcast_environment_change()


def install_adb() -> None:
    """Google's platform-tools (adb) under tools/, downloaded from Google when missing."""
    if ADB_PATH.exists():
        return
    print("Downloading adb from Google ...")
    with urllib.request.urlopen(PLATFORM_TOOLS_URL, timeout=120) as response:
        archive: bytes = response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        zipped.extractall(ADB_PATH.parent.parent)


def check_ffmpeg() -> None:
    missing: list[str] = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(f"Warning: {' and '.join(missing)} not found on PATH. Playing files needs them to read and convert "
              "videos, and links need ffmpeg. Install ffmpeg (for example: winget install Gyan.FFmpeg).")


def check_node() -> None:
    if shutil.which("node") is None:
        print("Note: Node.js not found on PATH. YouTube links need it (yt-dlp runs YouTube's scripts with it). "
              "Install it from https://nodejs.org if YouTube links fail.")


def install_ytdlp() -> None:
    """yt-dlp in its own environment under tools/, which the server keeps up to date."""
    if not YTDLP_PYTHON.exists():
        subprocess.run([sys.executable, "-m", "venv", str(YTDLP_PYTHON.parent.parent)], check=True)
    subprocess.run([str(YTDLP_PYTHON), "-m", "pip", "install", "-q", "-U", "--disable-pip-version-check",
                    "yt-dlp[default]"], check=True)


def make_icon() -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    size: int = 256
    image: Image.Image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw: ImageDraw.ImageDraw = ImageDraw.Draw(image)
    orange: tuple[int, int, int, int] = (255, 159, 67, 255)
    dark: tuple[int, int, int, int] = (16, 17, 20, 255)
    draw.rounded_rectangle((16, 36, 240, 196), radius=26, fill=orange)
    draw.polygon([(104, 76), (104, 156), (172, 116)], fill=dark)
    draw.rounded_rectangle((80, 212, 176, 228), radius=8, fill=orange)
    image.save(ICON_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


def _broadcast_environment_change() -> None:
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 2000,
                                             ctypes.byref(result))


def main() -> int:
    if "--uninstall" in sys.argv:
        uninstall_menu()
        STARTUP_LINK.unlink(missing_ok=True)
        uninstall_command()
        print("Removed the right-click menu, the startup entry and the tv command.")
        return 0
    make_icon()
    install_adb()
    install_menu()
    install_startup()
    install_command()
    install_ytdlp()
    check_ffmpeg()
    check_node()
    print(f"Installed: right-click a video > {MENU_TEXT}. Server starts with Windows. `tv` command added.")
    print("Next: switch on ADB debugging on the TV, then run `tv find` (open a new terminal first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
