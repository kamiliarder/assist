"""Core orchestrator: business logic with zero UI widget dependencies.

The Core owns config, history, toolbox, agent, and the worker thread. It lives in exactly one
place, the ordis-core daemon; UIs reach it through ordis.ipc (see ordis/ipc/server.py).
"""

from __future__ import annotations

import threading
import time

from . import agent as agent_module
from .agent import Agent  # noqa: F401  (re-exported)
from .config import Config, fetch_models, load, load_api_key, requires_api_key
from .history import HistoryStore
from .prompt import clear_skill_cache
from .toolbox import Toolbox
from .types import CoreCallbacks


class Core:
    def __init__(self, callbacks: CoreCallbacks | None = None, marshal=None):
        """`marshal(fn)` schedules `fn` on a UI thread. Defaults to calling directly (daemon)."""
        self.callbacks = callbacks
        self._marshal = marshal or (lambda fn: fn())
        self.cfg: Config = load()
        self.key = load_api_key(self.cfg)
        self.history = HistoryStore(self.cfg.history_enabled, self.cfg.history_dir)
        # looked up lazily, so replacing `callbacks` later (the server does) keeps working
        self.toolbox = Toolbox(lambda text: self.callbacks.confirm(text), self.cfg.command_timeout)
        self.agent = self._make_agent()

        self._lock = threading.Lock()
        self._busy = False
        self._cancel = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._active_stream: object | None = None  # httpx response, so cancel can close it
        self._stream_lock = threading.Lock()
        self._start_time = time.time()

    def _make_agent(self) -> agent_module.Agent:
        return agent_module.Agent(self.cfg, self.key, self.toolbox,
                                  register_stream=self._register_stream,
                                  unregister_stream=self._unregister_stream)

    # -- stream registry (so cancel can drop the model connection) --------

    def _register_stream(self, stream: object) -> None:
        with self._stream_lock:
            self._active_stream = stream

    def _unregister_stream(self) -> None:
        with self._stream_lock:
            self._active_stream = None

    def _kill_stream(self) -> None:
        """Force-close the active HTTP stream so the provider connection really goes away."""
        with self._stream_lock:
            stream = self._active_stream
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass  # already closed

    # -- lifecycle ---------------------------------------------------------

    def send_message(self, text: str, images: list[str] | None = None) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        self._cancel.clear()
        if images:
            self._marshal(lambda: self.callbacks.on_status(f"\n[attached {len(images)} image(s)]\n"))
        self._marshal(lambda: self.callbacks.on_text("\nai\n"))
        self.history.append({"role": "user", "content": text})
        self._worker_thread = threading.Thread(
            target=self._worker, args=(text, images), daemon=True)
        self._worker_thread.start()
        return True

    def cancel(self) -> None:
        """Stop the running turn and drop the model connection now (not at the next chunk).

        The flag goes first so a retry loop that sees the dropped stream gives up instead of
        reconnecting.
        """
        self._cancel.set()
        self._kill_stream()

    kill = cancel

    def shutdown(self) -> None:
        self.cancel()
        t = self._worker_thread
        if t is not None and t.is_alive():
            t.join(timeout=3)
        self.history.save()

    def reload_config(self) -> None:
        old = self.cfg
        self.cfg = load()
        self.key = load_api_key(self.cfg)
        self.toolbox = Toolbox(lambda text: self.callbacks.confirm(text), self.cfg.command_timeout)
        self.agent = self._make_agent()
        if (self.cfg.history_enabled, self.cfg.history_dir) != (old.history_enabled, old.history_dir):
            self.history = HistoryStore(self.cfg.history_enabled, self.cfg.history_dir)
        clear_skill_cache()

    def set_model(self, model: str) -> None:
        self.cfg.model = model
        self.agent = self._make_agent()

    def list_models(self) -> list[str]:
        key = self.key if requires_api_key(self.cfg) else None
        return fetch_models(self.cfg.base_url, key=key)

    def switch_session(self, direction: str) -> dict | None:
        if direction == "previous":
            return self.history.switch_previous()
        return None
    def new_session(self) -> dict:
        self.history.new_session()
        return self.session_view()

    # -- read-only views for UIs -------------------------------------------

    def snapshot(self) -> dict:
        cfg = self.cfg
        cur = self.history.current
        return {
            "model": cfg.model,
            "base_url": cfg.base_url,
            "has_key": bool(self.key),
            "requires_key": requires_api_key(cfg),
            "warnings": list(cfg.warnings),
            "approval_timeout": cfg.approval_timeout,
            "busy": self._busy,
            "session": cur.name if cur else None,
            "uptime_sec": int(time.time() - self._start_time),
        }

    def session_view(self) -> dict:
        cur = self.history.current
        return {"name": cur.name if cur else None,
                "messages": list(cur.messages) if cur else []}

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
            with self._lock:
                self._busy = False
            self._marshal(self.callbacks.on_finish)

    # -- properties --------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def config(self) -> Config:
        return self.cfg
