"""Settings stored in config.json next to the project root."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_PATH: Path = ROOT / "config.json"
LOG_DIR: Path = ROOT / "logs"
STATE_PATH: Path = ROOT / "state.json"
ADB_PATH: Path = ROOT / "tools" / "platform-tools" / "adb.exe"
WEB_DIR: Path = Path(__file__).resolve().parent / "web"
HELPER_APK: Path = ROOT / "tv-helper" / "tv-helper.apk"
YTDLP_PYTHON: Path = ROOT / "tools" / "ytdlp" / "Scripts" / "python.exe"
"""The interpreter of the private environment yt-dlp is installed (and kept up to date) in."""
COOKIES_PATH: Path = ROOT / "cookies.txt"
"""Website logins for yt-dlp in Netscape cookies.txt format, used when present."""

VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".3gp", ".ogv", ".vob",
})


def _default_folders() -> list[str]:
    return [str(Path(os.path.expanduser("~")) / "Downloads")]


@dataclass
class Config:
    tv_host: str = ""
    port: int = 8765
    video_folders: list[str] = field(default_factory=_default_folders)
    """Folders listed on the remote page, newest videos first."""
    library_depth: int = 3
    """How many subfolder levels below each video folder are searched."""
    library_limit: int = 300
    convert_quality_crf: int = 20
    """Lower is better quality and more CPU when a video has to be converted."""
    headphones_name: str = ""
    """Part of the Bluetooth name of the headphones the headphones button connects to the TV. Empty leaves the
    headphones button off."""
    stream_max_height: int = 1080
    """Highest picture height picked for videos played from a website link. Raise it to 2160 if your TV decodes
    4K VP9 or HEVC in hardware and your Wi-Fi keeps up."""

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_PATH.exists():
            config = cls()
            config.save()
            return config
        data: dict[str, object] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known: set[str] = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")


def pythonw_path() -> Path:
    """The windowless interpreter next to the one running now."""
    exe: Path = Path(sys.executable)
    candidate: Path = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe
