"""Builds tv-helper.apk, the small Fire TV app for Bluetooth headphones and the ad-free home screen.

Needs a JDK 17+ (found through JAVA_HOME or PATH). The build kit in tools/buildkit is downloaded on first
use: Google's r8, aapt2 and apksig from Google Maven, the Android 4.1 API and org.json stubs from Maven
Central, and framework-res.apk, which is pulled from the TV over adb (so the TV must be on).
Newer TV classes the 4.1 API lacks (TvView and friends) come from compile-only stubs in stubs/.
Run: python tv-helper/build.py
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from firetv_cast.config import ADB_PATH, Config  # noqa: E402
from firetv_cast.firetv import ADB_PORT  # noqa: E402

KIT: Path = HERE.parent / "tools" / "buildkit"
MAVEN_CENTRAL: str = "https://repo1.maven.org/maven2"
GOOGLE_MAVEN: str = "https://maven.google.com"
OUT: Path = HERE / "build"
APK: Path = HERE / "tv-helper.apk"
KEYSTORE: Path = HERE / "keystore.p12"
PASSWORD: str = "firetvcast"
"""Local signing key, made on the first build and never published. It only has to stay the same so the TV
accepts updates of the helper."""

ANDROID_JAR: Path = KIT / "android-4.1.1.4.jar"
JSON_JAR: Path = KIT / "json-20090211.jar"
"""org.json stubs for compiling only; the TV has its own copy."""
FRAMEWORK: Path = KIT / "framework-res.apk"
R8: Path = KIT / "r8-8.5.35.jar"
APKSIG: Path = KIT / "apksig-8.7.3.jar"
AAPT2: Path = KIT / "aapt2" / "aapt2.exe"

DOWNLOADS: dict[Path, str] = {
    ANDROID_JAR: f"{MAVEN_CENTRAL}/com/google/android/android/4.1.1.4/android-4.1.1.4.jar",
    JSON_JAR: f"{MAVEN_CENTRAL}/org/json/json/20090211/json-20090211.jar",
    R8: f"{GOOGLE_MAVEN}/com/android/tools/r8/8.5.35/r8-8.5.35.jar",
    APKSIG: f"{GOOGLE_MAVEN}/com/android/tools/build/apksig/8.7.3/apksig-8.7.3.jar",
}
AAPT2_ARCHIVE: str = f"{GOOGLE_MAVEN}/com/android/tools/build/aapt2/8.7.3-12006047/aapt2-8.7.3-12006047-windows.jar"
"""A zip with aapt2.exe in it."""


def find_jdk() -> Path:
    """The bin folder of the JDK from JAVA_HOME, or else the one `javac` is on PATH from."""
    home: str | None = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "javac.exe").exists():
        return Path(home) / "bin"
    javac: str | None = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent
    raise SystemExit("No JDK found. Install JDK 17 or newer (for example: winget install EclipseAdoptium.Temurin.21.JDK) "
                     "and set JAVA_HOME, or put its bin folder on PATH.")


def download(url: str) -> bytes:
    print(f"Downloading {url.rsplit('/', 1)[1]} ...")
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_build_kit() -> None:
    """Downloads whatever of the build kit is missing."""
    KIT.mkdir(parents=True, exist_ok=True)
    for target, url in DOWNLOADS.items():
        if not target.exists():
            target.write_bytes(download(url))
    if not AAPT2.exists():
        with zipfile.ZipFile(io.BytesIO(download(AAPT2_ARCHIVE))) as archive:
            archive.extractall(AAPT2.parent)
    if not FRAMEWORK.exists():
        host: str = Config.load().tv_host
        if not host:
            raise SystemExit("framework-res.apk is pulled from your TV: run `tv find` first so it is known.")
        serial: str = f"{host}:{ADB_PORT}"
        print(f"Pulling framework-res.apk from the TV at {serial} ...")
        run(ADB_PATH, "connect", serial)
        run(ADB_PATH, "-s", serial, "pull", "/system/framework/framework-res.apk", FRAMEWORK)


def run(*args: str | Path) -> None:
    subprocess.run([str(a) for a in args], check=True)


def main() -> int:
    JDK: Path = find_jdk()
    fetch_build_kit()
    shutil.rmtree(OUT, ignore_errors=True)
    classes: Path = OUT / "classes"
    classes.mkdir(parents=True)
    stubs: Path = OUT / "stubs"
    run(JDK / "javac.exe", "--release", "8", "-nowarn", "-cp", ANDROID_JAR, "-d", stubs,
        *sorted((HERE / "stubs").rglob("*.java")))
    sources: list[Path] = sorted((HERE / "src").rglob("*.java"))
    run(JDK / "javac.exe", "--release", "8", "-nowarn",
        "-cp", f"{ANDROID_JAR};{JSON_JAR};{stubs}", "-d", classes, *sources)
    stub_jar: Path = OUT / "stubs.jar"
    run(JDK / "jar.exe", "cf", stub_jar, "-C", stubs, ".")
    run(JDK / "java.exe", "-cp", R8, "com.android.tools.r8.D8", "--release", "--min-api", "22",
        "--lib", ANDROID_JAR, "--lib", JDK.parent, "--classpath", JSON_JAR, "--classpath", stub_jar,
        "--output", OUT, *sorted(classes.rglob("*.class")))
    resources: Path = OUT / "res.zip"
    run(AAPT2, "compile", "--dir", HERE / "res", "-o", resources)
    unsigned: Path = OUT / "unsigned.apk"
    run(AAPT2, "link", "-I", FRAMEWORK, "--manifest", HERE / "AndroidManifest.xml", "-R", resources,
        "-o", unsigned)
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as apk:
        apk.write(OUT / "classes.dex", "classes.dex")
    if not KEYSTORE.exists():
        run(JDK / "keytool.exe", "-genkeypair", "-keystore", KEYSTORE, "-storetype", "PKCS12",
            "-storepass", PASSWORD, "-keypass", PASSWORD, "-alias", "helper", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "36500", "-dname", "CN=Send to TV helper")
    signer: Path = OUT / "signer"
    signer.mkdir()
    run(JDK / "javac.exe", "-cp", APKSIG, "-d", signer, HERE / "Sign.java")
    run(JDK / "java.exe", "-cp", f"{signer};{APKSIG}", "Sign", KEYSTORE, PASSWORD, unsigned, APK)
    print(f"Built {APK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
