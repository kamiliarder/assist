"""Socket server for ordis-core.

The daemon owns the one Core (and so the one model connection). GUIs attach, receive the live
event stream, and send commands. Closing a GUI never touches the session.
"""

from __future__ import annotations

import itertools
import os
import pathlib
import queue
import socket
import struct
import threading
import time

from .protocol import Decoder, encode

_DEFERRED = object()
_TURN_LOG_MAX = 5000


class AlreadyRunning(RuntimeError):
    """Another ordis-core is already answering on the socket."""


class _Reject(Exception):
    pass


# -- listening socket ----------------------------------------------------------------------

def activation_socket() -> socket.socket | None:
    """The listening socket systemd passed us (socket activation), if any."""
    try:
        pid, count = int(os.environ.get("LISTEN_PID", "")), int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    if pid != os.getpid() or count < 1:
        return None
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)  # don't leak into tools we spawn
    return socket.socket(fileno=3)


def bind_socket(path: str) -> socket.socket:
    """Bind a private socket at `path`. Refuses to steal one that is still alive."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if p.exists() or p.is_symlink():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(str(p))
        except (ConnectionRefusedError, FileNotFoundError):
            p.unlink(missing_ok=True)  # stale: nobody is listening
        except OSError as e:
            raise AlreadyRunning(f"{path} is not answering cleanly ({e}); not removing it") from e
        else:
            raise AlreadyRunning(f"another ordis-core is already listening on {path}")
        finally:
            probe.close()
    old = os.umask(0o177)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(p))
    finally:
        os.umask(old)
    s.listen(8)
    return s


# -- one client connection -------------------------------------------------------------------

class _Conn:
    """Outbound messages go through a bounded queue so a stuck client can't stall the worker."""

    def __init__(self, sock: socket.socket, on_close):
        self.sock = sock
        self.attached = False
        self.alive = True
        self._out: queue.Queue = queue.Queue(maxsize=4096)
        self._on_close = on_close
        threading.Thread(target=self._write_loop, daemon=True).start()

    def send(self, obj: dict) -> bool:
        if not self.alive:
            return False
        try:
            self._out.put_nowait(encode(obj))
            return True
        except queue.Full:
            self.close()
            return False

    def _write_loop(self):
        while True:
            data = self._out.get()
            if data is None:
                return
            try:
                self.sock.sendall(data)
            except OSError:
                break
        self.close()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        for fn in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                fn()
            except OSError:
                pass
        try:
            self._out.put_nowait(None)
        except queue.Full:
            pass
        self._on_close(self)


class _Pending:
    def __init__(self, rid: int, kind: str, text: str, extra: dict):
        self.id, self.kind, self.text, self.extra = rid, kind, text, extra
        self.event = threading.Event()
        self.ok = False

    def as_event(self, timeout: float) -> dict:
        return {"event": "confirm_request", "id": self.id, "kind": self.kind,
                "text": self.text, "timeout": timeout, **self.extra}


class _Callbacks:
    """CoreCallbacks implementation: turns Core callbacks into broadcast events."""

    def __init__(self, server: "Server"):
        self.s = server

    def on_text(self, text: str) -> None:
        self.s.emit({"event": "text", "text": text})

    def on_status(self, status: str) -> None:
        self.s.emit({"event": "status", "text": status})

    def on_finish(self) -> None:
        self.s.finish_turn()

    def on_pause(self, rounds: int) -> bool:
        return self.s.ask("pause", f"{rounds} tools have run this turn. Resume or stop?", rounds=rounds)

    def confirm(self, text: str) -> bool:
        return self.s.ask("confirm", text)


# -- the server ----------------------------------------------------------------------------

class Server:
    """`core` is duck-typed: callbacks, busy, send_message, cancel, reload_config, set_model,
    switch_session, list_models, snapshot, session_view, cfg.approval_timeout."""

    def __init__(self, core, listener: socket.socket, path: str | None = None, owned: bool = False):
        self.core = core
        self.listener = listener
        self.path = path
        self.owned = owned  # we created the socket file, so we remove it
        self.on_shutdown = None  # set by the daemon: called when a client asks to shut down
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._conns: list[_Conn] = []
        self._turn_log: list[dict] = []
        self._turn_base: int | None = None
        self._pending: _Pending | None = None
        self._ids = itertools.count(1)
        self._started = time.time()
        self._stopped = False
        core.callbacks = _Callbacks(self)

    # lifecycle

    def start(self) -> None:
        threading.Thread(target=self._accept_loop, daemon=True, name="ordis-accept").start()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self.core.cancel()  # closes the model connection
        except Exception:
            pass
        self._deny_pending()
        try:
            self.listener.close()
        except OSError:
            pass
        for c in list(self._conns):
            c.close()
        if self.owned and self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def _accept_loop(self):
        while not self._stopped:
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            if not self._peer_ok(sock):
                sock.close()
                continue
            conn = _Conn(sock, self._drop)
            with self._lock:
                self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _peer_ok(sock) -> bool:
        try:
            _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return uid == os.getuid()
        except (OSError, AttributeError, struct.error):
            return True  # no SO_PEERCRED here; the 0600 socket mode still applies

    def _drop(self, conn: _Conn):
        with self._lock:
            if conn in self._conns:
                self._conns.remove(conn)
            if self._pending and not any(c.attached for c in self._conns):
                self._deny_pending()  # nobody left to answer

    def _serve(self, conn: _Conn):
        dec = Decoder()
        try:
            while conn.alive:
                data = conn.sock.recv(65536)
                if not data:
                    break
                for msg in dec.feed(data):
                    self._dispatch(conn, msg)
        except (OSError, ValueError):
            pass
        finally:
            conn.close()

    # events

    def emit(self, event: dict) -> None:
        with self._lock:
            if self._turn_base is not None:
                log = self._turn_log
                if event["event"] == "text" and log and log[-1]["event"] == "text":
                    log[-1] = {"event": "text", "text": log[-1]["text"] + event["text"]}
                else:
                    log.append(event)
                    if len(log) > _TURN_LOG_MAX:
                        del log[: len(log) - _TURN_LOG_MAX]
            for c in list(self._conns):
                if c.attached:
                    c.send(event)

    def finish_turn(self) -> None:
        with self._lock:
            self.emit({"event": "finish"})
            self._turn_log, self._turn_base = [], None

    # approvals

    def ask(self, kind: str, text: str, **extra) -> bool:
        """Block the worker until an attached client answers. No client / timeout / cancel -> deny."""
        timeout = float(getattr(self.core.cfg, "approval_timeout", 120))
        with self._lock:
            if not any(c.attached for c in self._conns):
                self.emit({"event": "status", "text": "\n(no UI attached; denied)\n"})
                return False
            req = _Pending(next(self._ids), kind, text, extra)
            self._pending = req
            self.emit(req.as_event(timeout))
        answered = req.event.wait(timeout)
        with self._lock:
            if self._pending is req:
                self._pending = None
            self.emit({"event": "confirm_done", "id": req.id})
        return req.ok if answered else False

    def _resolve(self, rid, ok: bool) -> bool:
        with self._lock:
            req = self._pending
            if req is None or req.id != rid:
                return False
            req.ok = bool(ok)
            req.event.set()
            return True

    def _deny_pending(self) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending.ok = False
                self._pending.event.set()

    # commands

    def _dispatch(self, conn: _Conn, msg: dict) -> None:
        rid = msg.get("id")

        def reply(body: dict):
            if rid is not None:
                body = {"id": rid, **body}
            conn.send(body)

        if msg.get("_invalid"):
            return reply({"error": "invalid JSON"})
        action = msg.get("action")
        handler = getattr(self, f"_cmd_{action}", None) if isinstance(action, str) and action.isidentifier() else None
        if handler is None:
            return reply({"error": f"unknown action: {action}"})
        try:
            result = handler(conn, msg, reply)
        except _Reject as e:
            return reply({"error": str(e)})
        except Exception as e:  # a bad command must not take the daemon down
            return reply({"error": f"{type(e).__name__}: {e}"})
        if result is not _DEFERRED:
            reply({"status": "ok", **(result or {})})

    def _require_idle(self):
        if self.core.busy:
            raise _Reject("busy: a turn is running (cancel it first)")

    def _cmd_attach(self, conn, msg, reply):
        with self._lock:
            session = self.core.session_view()
            msgs = session["messages"]
            if self._turn_base is not None:
                msgs = msgs[: self._turn_base]  # the running turn is replayed from the log
            reply({"status": "ok", "snapshot": self.core.snapshot()})
            conn.attached = True
            conn.send({"event": "history", "name": session["name"], "messages": msgs})
            for ev in self._turn_log:
                conn.send(ev)
            if self._pending is not None:
                conn.send(self._pending.as_event(float(self.core.cfg.approval_timeout)))
        return _DEFERRED

    def _cmd_detach(self, conn, msg, reply):
        conn.attached = False
        return {}

    def _cmd_send(self, conn, msg, reply):
        text = msg.get("text")
        images = msg.get("images") or None
        if not isinstance(text, str) or not text.strip():
            raise _Reject("text is required")
        if images is not None and not (isinstance(images, list) and all(isinstance(i, str) for i in images)):
            raise _Reject("images must be a list of file paths")
        with self._send_lock:
            self._require_idle()
            with self._lock:
                self._turn_base = len(self.core.session_view()["messages"])
                self._turn_log = []
                self.emit({"event": "user", "text": text, "images": images or []})
            self.core.send_message(text, images=images)
        return {}

    def _cmd_cancel(self, conn, msg, reply):
        self._deny_pending()
        self.core.cancel()  # sets the flag AND closes the HTTP stream
        return {"message": "cancelled; model connection closed"}

    _cmd_kill = _cmd_cancel

    def _cmd_confirm_reply(self, conn, msg, reply):
        if not self._resolve(msg.get("request"), msg.get("ok") is True):
            raise _Reject("no such pending request")
        return {}

    def _cmd_reload(self, conn, msg, reply):
        self._require_idle()
        self.core.reload_config()
        self.emit({"event": "snapshot", "snapshot": self.core.snapshot()})
        return {"message": "config reloaded"}

    def _cmd_set_model(self, conn, msg, reply):
        model = msg.get("model")
        if not isinstance(model, str) or not model:
            raise _Reject("model is required")
        self._require_idle()
        self.core.set_model(model)
        self.emit({"event": "snapshot", "snapshot": self.core.snapshot()})
        return {}

    def _cmd_list_models(self, conn, msg, reply):
        def work():
            try:
                reply({"status": "ok", "models": self.core.list_models()})
            except Exception as e:
                reply({"error": str(e)})
        threading.Thread(target=work, daemon=True).start()
        return _DEFERRED

    def _cmd_switch_session(self, conn, msg, reply):
        self._require_idle()
        prev = self.core.switch_session(msg.get("direction", "previous"))
        if prev is None:
            raise _Reject("no previous session")
        self.emit({"event": "history", "name": prev["name"], "messages": prev["messages"]})
        return {"session": prev["name"]}

    def _cmd_status(self, conn, msg, reply):
        return {"busy": self.core.busy, "model": self.core.snapshot()["model"],
                "uptime_sec": int(time.time() - self._started),
                "clients": sum(1 for c in self._conns if c.attached)}

    def _cmd_healthcheck(self, conn, msg, reply):
        return {"status": "healthy", "busy": self.core.busy}

    def _cmd_shutdown(self, conn, msg, reply):
        reply({"status": "ok", "message": "shutting down"})
        threading.Timer(0.05, self.on_shutdown or self.stop).start()
        return _DEFERRED
