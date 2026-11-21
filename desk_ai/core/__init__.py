"""Core orchestrator: business logic with zero UI widget dependencies.

The Core owns config, history, toolbox, agent, and the worker thread.
UIs (GTK, Slint, TUI, ...) implement CoreCallbacks and drive Core.
"""

from __future__ import annotations

import threading

from . import agent as agent_module
from .config import Config, config_dir, load, load_api_key
from .history import HistoryStore
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
        self.agent = agent_module.Agent(self.cfg, self.key, self.toolbox)

        self._busy = False
        self._cancel = threading.Event()
        self._worker_thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def send_message(self, text: str, images: list[str] | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel.clear()
        self._marshal(lambda: self.callbacks.on_text("\nyou\n"))
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
        self.agent = agent_module.Agent(new_cfg, new_key, self.toolbox)
        self.history = HistoryStore(new_cfg.history_enabled, new_cfg.history_dir)

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
