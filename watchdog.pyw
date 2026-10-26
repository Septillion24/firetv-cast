"""Restarts the Send to TV server when nothing is listening on its port.

Run once a minute by the "Send to TV watchdog" scheduled task (see install_watchdog.ps1).
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from firetv_cast.config import ROOT, Config

CREATE_NO_WINDOW: int = 0x08000000
DETACHED_PROCESS: int = 0x00000008


def server_is_up(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def main() -> None:
    port: int = Config.load().port
    if server_is_up(port):
        return
    pythonw: Path = Path(sys.executable)
    subprocess.Popen(
        [str(pythonw), str(ROOT / "run_server.pyw")],
        cwd=str(ROOT),
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
