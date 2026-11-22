"""Wire format: JSON objects over a Unix stream socket, one per line.

Client -> daemon:  {"id": 1, "action": "send", "text": "hi"}
Daemon -> client:
  reply  (answers a request):  {"id": 1, "status": "ok", ...}  or  {"id": 1, "error": "..."}
  event  (pushed, attached clients only):  {"event": "text", "text": "..."}

Newlines between objects are optional on input, so `echo '{"action":"status"}' | socat - UNIX:...`
and older one-shot clients keep working.
"""

from __future__ import annotations

import codecs
import json
import os
import pathlib

MAX_BUFFER = 64 * 1024 * 1024


def default_socket_path() -> str:
    """Same answer in the daemon and in every client, whatever environment they run in.

    Matches the systemd socket unit's `%t/ordis/control.sock`.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return str(pathlib.Path(runtime) / "ordis" / "control.sock")
    base = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return str(pathlib.Path(base) / "ordis" / "control.sock")


def resolve_socket_path(configured: str = "") -> str:
    return os.path.expanduser(configured) if configured else default_socket_path()


def encode(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class Decoder:
    """Incremental decoder. feed() returns the complete objects received so far.

    A malformed line comes back as {"_invalid": True} so the caller can answer it.
    """

    def __init__(self):
        self._text = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buf = ""
        self._json = json.JSONDecoder()

    def feed(self, data: bytes) -> list[dict]:
        self._buf += self._text.decode(data)
        if len(self._buf) > MAX_BUFFER:
            raise ValueError("message too large")
        out: list[dict] = []
        while True:
            self._buf = self._buf.lstrip()
            if not self._buf:
                break
            try:
                obj, end = self._json.raw_decode(self._buf)
            except json.JSONDecodeError:
                nl = self._buf.find("\n")
                if nl < 0:
                    break  # incomplete; wait for more
                self._buf = self._buf[nl + 1:]
                out.append({"_invalid": True})
                continue
            self._buf = self._buf[end:]
            out.append(obj if isinstance(obj, dict) else {"_invalid": True})
        return out
