"""Core orchestrator: business logic with zero UI widget dependencies.

The Core owns config, history, toolbox, agent, and the worker thread.
UIs (GTK, Slint, TUI, ...) implement CoreCallbacks and drive Core.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

from . import agent as agent_module
from .config import Config, config_dir, load, load_api_key
from .history import HistoryStore
from .prompt import clear_skill_cache
from .toolbox import Toolbox
from .types import CoreCallbacks


class Core:
    def __init__(self, callbacks: CoreCallbacks, marshal=None):
        """`marshal(fn)` schedules `fn` on the UI main thread. Defaults to calling directly."""
        self.callbacks = callbacks
        self._marshal = marshal or (lambda fn: fn())
        self.cfg: Config = load()
        self.key = load_api_key(self.cfg)
        self.history = HistoryStore(self.cfg.history_enabled, self.cfg.history_dir)
        self.toolbox = Toolbox(lambda text: self.callbacks.confirm(text), self.cfg.command_timeout)
        self.agent = agent_module.Agent(self.cfg, self.key, self.toolbox,
                                        register_stream=self._register_stream,
                                        unregister_stream=self._unregister_stream)

        self._busy = False
        self._cancel = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._socket_server: socket.socket | None = None
        self._socket_thread: threading.Thread | None = None
        self._socket_path = self.cfg.socket_path if hasattr(self.cfg, 'socket_path') and self.cfg.socket_path else None
        self._active_stream: object | None = None  # httpx stream response for kill switch
        self._stream_lock = threading.Lock()
        self._start_socket_server()
        self._start_time = time.time()

    def _start_socket_server(self):
        if not self._socket_path:
            return
        # Clean up any stale socket
        try:
            os.unlink(self._socket_path)
        except OSError:
            pass
        self._socket_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket_server.bind(self._socket_path)
        self._socket_server.listen(5)
        self._socket_thread = threading.Thread(target=self._socket_loop, daemon=True)
        self._socket_thread.start()

    def _socket_loop(self):
        while True:
            try:
                conn, _ = self._socket_server.accept()
                self._handle_connection(conn)
            except OSError:
                break  # socket closed

    def _handle_connection(self, conn):
        try:
            data = conn.recv(4096).decode().strip()
            if not data:
                return
            cmd = json.loads(data)
            response = self._handle_command(cmd)
            conn.sendall(json.dumps(response).encode())
        except json.JSONDecodeError:
            conn.sendall(json.dumps({"error": "invalid JSON"}).encode())
        except Exception as e:
            conn.sendall(json.dumps({"error": str(e)}).encode())
        finally:
            conn.close()

    def _handle_command(self, cmd: dict) -> dict:
        action = cmd.get("action")
        if action == "kill":
            self._kill_stream()
            return {"status": "ok", "message": "stream killed"}
        elif action == "status":
            return {
                "status": "ok",
                "busy": self._busy,
                "model": self.cfg.model,
                "uptime_sec": int(time.time() - self._start_time),
            }
        elif action == "healthcheck":
            return {"status": "healthy", "busy": self._busy}
        elif action == "reload":
            self.reload_config()
            return {"status": "ok", "message": "config reloaded"}
        else:
            return {"error": f"unknown action: {action}"}

    def shutdown(self):
        if self._socket_server:
            self._socket_server.close()
        if self._socket_path:
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass

    def _kill_stream(self) -> None:
        """Force-close the active HTTP stream to kill the AI connection immediately."""
        with self._stream_lock:
            stream = self._active_stream
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass  # stream may already be closed

    def _register_stream(self, stream: object) -> None:
        """Register the active HTTP stream so kill can close it."""
        with self._stream_lock:
            self._active_stream = stream

    def _unregister_stream(self) -> None:
        """Clear the active stream reference when the turn ends."""
        with self._stream_lock:
            self._active_stream = None

    # -- lifecycle ---------------------------------------------------------

    def send_message(self, text: str, images: list[str] | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel.clear()
        if images:
            self._marshal(lambda: self.callbacks.on_status(f"\n[attached {len(images)} image(s)]\n"))
        self._marshal(lambda: self.callbacks.on_text("\nai\n"))
        self.history.append({"role": "user", "content": text})
        self._worker_thread = threading.Thread(
            target=self._worker, args=(text, images), daemon=True)
        self._worker_thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def reload_config(self) -> None:
        new_cfg = load()
        new_key = load_api_key(new_cfg)
        self.cfg = new_cfg
        self.key = new_key
        self.toolbox = Toolbox(self.callbacks.confirm, new_cfg.command_timeout)
        self.agent = agent_module.Agent(new_cfg, new_key, self.toolbox,
                                        register_stream=self._register_stream,
                                        unregister_stream=self._unregister_stream)
        self.history = HistoryStore(new_cfg.history_enabled, new_cfg.history_dir)
        clear_skill_cache()

    def switch_session(self, direction: str) -> dict | None:
        if direction == "previous":
            return self.history.switch_previous()
        return None

    # -- worker thread -----------------------------------------------------

    def _worker(self, text: str, images: list[str] | None):
        msgs = self.history.messages()
        mark = len(msgs) - 1
        try:
            self.agent.run_turn(
                msgs, cancel=self._cancel,
                on_text=lambda t: self._marshal(lambda: self.callbacks.on_text(t)),
                on_status=lambda s: self._marshal(lambda: self.callbacks.on_status(s)),
                on_pause=lambda r: self.callbacks.on_pause(r),
                images=images,
            )
            self.history.save()
        except agent_module.CancelledError:
            self._marshal(lambda: self.callbacks.on_text("\n[cancelled]\n"))
        except Exception as e:
            self._marshal(lambda: self.callbacks.on_status(f"\n[error] {e}\n"))
            if len(msgs) == mark + 1:
                self.history.truncate_after(mark)
            else:
                self._marshal(lambda: self.callbacks.on_status(
                    "(a tool already ran this turn, so your message was kept)\n"))
        finally:
            self._marshal(lambda: self.callbacks.on_text("\n"))
            self.history.save()
            self._busy = False
            self._marshal(self.callbacks.on_finish)

    # -- approval flow -----------------------------------------------------

    def confirm(self, text: str) -> bool:
        return self.callbacks.confirm(text)

    # -- properties --------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def config(self) -> Config:
        return self.cfg
