"""Client side: what a UI uses to talk to ordis-core. No GTK in here."""

from __future__ import annotations

import itertools
import socket
import threading
import time

from .protocol import Decoder, encode


class ClientCallbacks:
    """Override what you need. Every method runs through `marshal` (the UI thread for GTK)."""

    def on_connection(self, connected: bool, snapshot: dict | None) -> None: ...
    def on_snapshot(self, snapshot: dict) -> None: ...
    def on_history(self, name: str, messages: list) -> None: ...
    def on_user(self, text: str, images: list) -> None: ...
    def on_text(self, text: str) -> None: ...
    def on_status(self, text: str) -> None: ...
    def on_finish(self) -> None: ...
    def on_confirm(self, request_id: int, kind: str, text: str, extra: dict) -> None: ...
    def on_confirm_done(self, request_id: int) -> None: ...
    def on_error(self, text: str) -> None: ...


class CoreClient:
    def __init__(self, path: str, callbacks: ClientCallbacks | None = None, marshal=None):
        self.path = path
        self.callbacks = callbacks or ClientCallbacks()
        self._marshal = marshal or (lambda fn: fn())
        self.snapshot: dict = {}
        self.connected = False
        self.busy = False
        self._sock: socket.socket | None = None
        self._wlock = threading.Lock()
        self._ids = itertools.count(1)
        self._waiters: dict[int, tuple] = {}  # id -> (Event, box, on_done)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._attach_id = 0

    # lifecycle

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="ordis-client")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        s = self._sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # commands

    def command(self, action: str, on_done=None, **params) -> int | None:
        """Fire a command without blocking. `on_done(reply)` runs through marshal."""
        rid = next(self._ids)
        self._waiters[rid] = (None, None, on_done)
        if not self._send({"id": rid, "action": action, **params}):
            self._waiters.pop(rid, None)
            self._emit(self.callbacks.on_error, "not connected to ordis-core")
            return None
        return rid

    def request(self, action: str, timeout: float = 10.0, **params) -> dict:
        """Blocking round trip. Don't call this on a UI thread."""
        rid = next(self._ids)
        ev, box = threading.Event(), {}
        self._waiters[rid] = (ev, box, None)
        if not self._send({"id": rid, "action": action, **params}):
            self._waiters.pop(rid, None)
            return {"error": "not connected to ordis-core"}
        if not ev.wait(timeout):
            self._waiters.pop(rid, None)
            return {"error": "timed out"}
        return box["reply"]

    def send_message(self, text: str, images: list[str] | None = None) -> None:
        self.command("send", text=text, images=images or [], on_done=self._report)

    def cancel(self) -> None:
        self.command("cancel", on_done=self._report)

    def answer(self, request_id: int, ok: bool) -> None:
        self.command("confirm_reply", request=request_id, ok=bool(ok), on_done=self._report)

    def _report(self, reply: dict) -> None:
        if "error" in reply:
            self.callbacks.on_error(reply["error"])

    # plumbing

    def _send(self, obj: dict) -> bool:
        s = self._sock
        if s is None or not self.connected and obj.get("action") != "attach":
            return False
        try:
            with self._wlock:
                s.sendall(encode(obj))
            return True
        except OSError:
            return False

    def _emit(self, fn, *args):
        self._marshal(lambda: fn(*args))

    def _run(self):
        delay, announced = 0.3, False
        while not self._stop.is_set():
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(3)
                s.connect(self.path)
                s.settimeout(None)
            except OSError:
                s.close()
                if not announced:
                    announced = True
                    self._emit(self.callbacks.on_connection, False, None)
                self._stop.wait(delay)
                delay = min(delay * 1.7, 3.0)
                continue
            delay = 0.3
            self._sock = s
            self._attach_id = next(self._ids)
            try:
                s.sendall(encode({"id": self._attach_id, "action": "attach"}))
                self._read(s)
            except OSError:
                pass
            finally:
                self.connected = False
                self._sock = None
                s.close()
                for rid, (ev, box, _cb) in list(self._waiters.items()):
                    if ev is not None:
                        box["reply"] = {"error": "connection lost"}
                        ev.set()
                self._waiters.clear()
            if not self._stop.is_set():
                announced = True
                self._emit(self.callbacks.on_connection, False, None)
                self._stop.wait(delay)

    def _read(self, s: socket.socket):
        dec = Decoder()
        while True:
            data = s.recv(65536)
            if not data:
                return
            for msg in dec.feed(data):
                if "event" in msg:
                    self._on_event(msg)
                else:
                    self._on_reply(msg)

    def _on_reply(self, msg: dict):
        rid = msg.get("id")
        if rid == self._attach_id:
            if "error" in msg:
                self._emit(self.callbacks.on_error, msg["error"])
                return
            self.snapshot = msg.get("snapshot", {})
            self.busy = bool(self.snapshot.get("busy"))
            self.connected = True
            self._emit(self.callbacks.on_connection, True, self.snapshot)
            return
        waiter = self._waiters.pop(rid, None)
        if waiter is None:
            return
        ev, box, on_done = waiter
        if ev is not None:
            box["reply"] = msg
            ev.set()
        if on_done is not None:
            self._emit(on_done, msg)

    def _on_event(self, msg: dict):
        e, cb = msg["event"], self.callbacks
        if e == "text":
            self._emit(cb.on_text, msg.get("text", ""))
        elif e == "status":
            self._emit(cb.on_status, msg.get("text", ""))
        elif e == "user":
            self.busy = True
            self._emit(cb.on_user, msg.get("text", ""), msg.get("images", []))
        elif e == "finish":
            self.busy = False
            self._emit(cb.on_finish)
        elif e == "history":
            self._emit(cb.on_history, msg.get("name", ""), msg.get("messages", []))
        elif e == "snapshot":
            self.snapshot = msg.get("snapshot", {})
            self._emit(cb.on_snapshot, self.snapshot)
        elif e == "confirm_request":
            extra = {k: v for k, v in msg.items() if k not in ("event", "id", "kind", "text")}
            self._emit(cb.on_confirm, msg["id"], msg.get("kind", "confirm"), msg.get("text", ""), extra)
        elif e == "confirm_done":
            self._emit(cb.on_confirm_done, msg.get("id"))


def oneshot(path: str, action: str, timeout: float = 5.0, **params) -> dict:
    """Send one command and return the reply (for `ordis --ctl ...`). Doesn't attach."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(encode({"id": 1, "action": action, **params}))
        dec = Decoder()
        while True:
            data = s.recv(65536)
            if not data:
                return {"error": "connection closed without a reply"}
            for msg in dec.feed(data):
                if msg.get("id") == 1:
                    return msg
    except OSError as e:
        return {"error": f"cannot reach ordis-core at {path}: {e}"}
    finally:
        s.close()
