#!/usr/bin/env python3
"""Toggle the desk-ai assistant window.

Sends a `toggle` message to the core daemon over a Unix socket. The daemon
relays it to the UI, which hides the window. If no daemon is running, this
starts one (and the UI) via `python -m desk_ai.app`.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

from desk_ai.daemon import SOCK_PATH


def _send_toggle() -> bool:
    """Send a toggle message to the daemon. Returns True if delivered."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(SOCK_PATH))
            s.sendall((json.dumps({"type": "toggle"}) + "\n").encode("utf-8"))
        return True
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return False


def main():
    if "--print-config" in sys.argv:
        from desk_ai.app import print_config
        print_config()
        return

    if not _send_toggle():
        # No daemon listening: start the full app (which launches the daemon
        # as a separate process and opens the UI).
        entry_point = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "desk_ai", "app.py")
        )
        subprocess.Popen(
            [sys.executable, entry_point],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


if __name__ == "__main__":
    main()
