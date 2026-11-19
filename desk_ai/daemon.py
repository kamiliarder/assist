"""desk-ai core daemon: agent + toolbox + Unix socket API.

Runs as a separate process from the UI. The UI connects over a Unix socket
and sends user messages; the daemon streams agent output back. Tool
approvals go over the socket too: the daemon sends a confirm_request, the
UI answers with confirm_response.
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import threading

from . import config
from .history import HistoryStore
from .llm import Agent, CancelledError
from .tools import Toolbox

SOCK_PATH = pathlib.Path(
    os.environ.get("XDG_RUNTIME_DIR") or pathlib.Path("/tmp")
) / "desk-ai.sock"


class Core:
    """The assistant core: config, agent, toolbox, history. No GTK."""

    def __init__(self):
        self.cfg = config.load()
        self.key = config.load_api_key(self.cfg)
        self.store = HistoryStore(self.cfg.history_enabled, self.cfg.history_dir)
        self.store.new_session()
        self.ui_conn: socket.socket | None = None
        self._conn_lock = threading.Lock()
        self._confirm_lock = threading.Lock()
        self._confirm_waiters: dict[int, tuple[threading.Event, dict]] = {}
        self._confirm_counter = 0
        self._busy = False
        self._busy_lock = threading.Lock()
        self._cancel: threading.Event | None = None
        self._cancel_lock = threading.Lock()

    # -- socket I/O to the UI

    def send(self, msg: dict):
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._conn_lock:
            conn = self.ui_conn
        if conn is None:
            return
        try:
            conn.sendall(data)
        except (BrokenPipeError, OSError):
            with self._conn_lock:
                self.ui_conn = None

    def attach_ui(self, conn: socket.socket):
        with self._conn_lock:
            self.ui_conn = conn

    def detach_ui(self):
        with self._conn_lock:
            self.ui_conn = None

    # -- confirmation: called by Toolbox from the agent worker thread

    def confirm(self, text: str) -> bool:
        with self._confirm_lock:
            self._confirm_counter += 1
            cid = self._confirm_counter
            ev = threading.Event()
            result: dict = {"ok": False}
            self._confirm_waiters[cid] = (ev, result)
        self.send({"type": "confirm_request", "id": cid, "text": text})
        if not ev.wait(self.cfg.approval_timeout):
            with self._confirm_lock:
                self._confirm_waiters.pop(cid, None)
            return False
        with self._confirm_lock:
            waiter = self._confirm_waiters.pop(cid, None)
        return waiter[1]["ok"] if waiter else False

    def answer_confirm(self, cid: int, ok: bool):
        with self._confirm_lock:
            waiter = self._confirm_waiters.pop(cid, None)
        if waiter:
            ev, result = waiter
            result["ok"] = ok
            ev.set()

    # -- agent turn

    def _set_busy(self, value: bool):
        with self._busy_lock:
            self._busy = value

    def is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def run_turn(self, text: str):
        self._set_busy(True)
        cancel = threading.Event()
        with self._cancel_lock:
            self._cancel = cancel
        try:
            self.store.append({"role": "user", "content": text})
            msgs = self.store.messages()
            toolbox = Toolbox(self.confirm, self.cfg.command_timeout)
            agent = Agent(self.cfg, self.key, toolbox)

            def on_text(t):
                self.send({"type": "output", "text": t, "tag": "ai"})

            def on_status(s):
                self.send({"type": "output", "text": s, "tag": "tool"})

            def on_pause(rounds: int) -> bool:
                with self._confirm_lock:
                    self._confirm_counter += 1
                    cid = self._confirm_counter
                    ev = threading.Event()
                    result: dict = {"ok": False}
                    self._confirm_waiters[cid] = (ev, result)
                self.send({
                    "type": "confirm_request",
                    "id": cid,
                    "text": f"{rounds} tools have run this turn. Resume or stop?",
                })
                if not ev.wait(self.cfg.approval_timeout):
                    with self._confirm_lock:
                        self._confirm_waiters.pop(cid, None)
                    return False
                with self._confirm_lock:
                    waiter = self._confirm_waiters.pop(cid, None)
                return waiter[1]["ok"] if waiter else False

            try:
                agent.run_turn(msgs, cancel=cancel, on_text=on_text, on_status=on_status, on_pause=on_pause)
            except CancelledError:
                self.send({"type": "output", "text": "\n[cancelled]\n", "tag": "tool"})
            except Exception as e:  # noqa: BLE001
                self.send({"type": "output", "text": f"\n[error] {e}\n", "tag": "err"})
            finally:
                self.store.save()
                self.send({"type": "done"})
        finally:
            self._set_busy(False)


def _dispatch(core: Core, msg: dict):
    mtype = msg.get("type")
    if mtype == "message":
        text = msg.get("text", "").strip()
        if text and not core.is_busy():
            threading.Thread(target=core.run_turn, args=(text,), daemon=True).start()
    elif mtype == "confirm_response":
        core.answer_confirm(msg.get("id"), bool(msg.get("ok")))
    elif mtype == "toggle":
        core.send({"type": "toggle"})
    elif mtype == "status":
        core.send({"type": "status", "busy": core.is_busy()})
    elif mtype == "cancel":
        with core._cancel_lock:
            c = core._cancel
        if c is not None:
            c.set()


def _handle_ui(core: Core, conn: socket.socket):
    buf = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                _dispatch(core, msg)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        core.detach_ui()
        try:
            conn.close()
        except OSError:
            pass


def serve():
    """Listen on the Unix socket and handle UI connections."""
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK_PATH))
    os.chmod(str(SOCK_PATH), 0o600)
    srv.listen(1)
    core = Core()
    while True:
        conn, _ = srv.accept()
        core.attach_ui(conn)
        threading.Thread(target=_handle_ui, args=(core, conn), daemon=True).start()


if __name__ == "__main__":
    serve()
