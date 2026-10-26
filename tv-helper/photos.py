"""Downloads the home screen's nature photos (photos.json) and copies them to the TV.

They are featured pictures from Wikimedia Commons under free licenses; the home screen shows each one's
caption, photographer and license, as the licenses ask. To change the set, edit photos.json (the "file" is
the Commons file name) and run this again.
Run: python tv-helper/photos.py   (needs Pillow; the TV must be reachable over adb)
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from firetv_cast.config import ADB_PATH, Config  # noqa: E402
from firetv_cast.firetv import ADB_PORT, HELPER_PACKAGE  # noqa: E402

LIST: Path = HERE / "photos.json"
OUT: Path = HERE / "photos"
TV_DIR: str = f"/sdcard/Android/data/{HELPER_PACKAGE}/files/photos"
"""The helper app's own storage on the TV, which it can read without asking for permission."""
API: str = "https://commons.wikimedia.org/w/api.php"
HEADERS: dict[str, str] = {"User-Agent": "firetv-cast-home-photos/1.0 (personal TV wallpaper)"}
SIZE: tuple[int, int] = (1920, 1080)
FETCH_WIDTH: int = 2400
"""Wide enough that even the widest photos (about 2:1) are 1080 pixels tall."""


def fetch(url: str) -> bytes:
    """Gets a URL politely: Wikimedia answers 429 when asked too quickly."""
    for attempt in range(6):
        time.sleep(1.0)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Wikimedia keeps refusing: {url}")


def thumbnail_urls(files: list[str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for start in range(0, len(files), 50):
        query: dict[str, str] = {
            "action": "query", "format": "json", "formatversion": "2", "prop": "imageinfo",
            "iiprop": "url", "iiurlwidth": str(FETCH_WIDTH),
            "titles": "|".join("File:" + name for name in files[start:start + 50]),
        }
        pages = json.loads(fetch(API + "?" + urllib.parse.urlencode(query)))["query"]["pages"]
        for page in pages:
            urls[page["title"][len("File:"):]] = page["imageinfo"][0]["thumburl"]
    return urls


def fill(image: Image.Image) -> Image.Image:
    """Scales and center-crops to fill the TV screen."""
    scale: float = max(SIZE[0] / image.width, SIZE[1] / image.height)
    resized: Image.Image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left: int = (resized.width - SIZE[0]) // 2
    top: int = (resized.height - SIZE[1]) // 2
    return resized.crop((left, top, left + SIZE[0], top + SIZE[1]))


def main() -> int:
    photos: list[dict[str, str]] = json.loads(LIST.read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    names: list[str] = [f"{number:02d}.jpg" for number in range(len(photos))]
    missing: list[int] = [i for i, name in enumerate(names) if not (OUT / name).exists()]
    if missing:
        urls: dict[str, str] = thumbnail_urls([photos[i]["file"] for i in missing])
        for i in missing:
            print(f"Downloading {photos[i]['caption']}")
            image: Image.Image = Image.open(io.BytesIO(fetch(urls[photos[i]["file"]]))).convert("RGB")
            fill(image).save(OUT / names[i], quality=88, optimize=True, progressive=True)
    for stale in OUT.glob("*.jpg"):
        if stale.name not in names:
            stale.unlink()
    credits: str = "".join(f"{name}\t{photo['caption']}\t{photo['artist']}\t{photo['license']}\n"
                           for name, photo in zip(names, photos))
    (OUT / "credits.tsv").write_text(credits, encoding="utf-8")

    serial: str = f"{Config.load().tv_host}:{ADB_PORT}"
    subprocess.run([str(ADB_PATH), "connect", serial], check=True, capture_output=True)
    subprocess.run([str(ADB_PATH), "-s", serial, "shell", f"rm -rf {TV_DIR} && mkdir -p {TV_DIR}"], check=True)
    subprocess.run([str(ADB_PATH), "-s", serial, "push", *[str(OUT / name) for name in names],
                    str(OUT / "credits.tsv"), TV_DIR + "/"], check=True)
    print(f"Copied {len(names)} photos to the TV")
    return 0


if __name__ == "__main__":
    sys.exit(main())
