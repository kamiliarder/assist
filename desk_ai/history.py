"""Persistent chat history with named sessions."""
from __future__ import annotations

import json
import pathlib
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    name: str
    messages: list = field(default_factory=list)


class HistoryStore:
    MAX_SESSIONS = 5

    def __init__(self, enabled: bool, history_dir: str = ""):
        self.enabled = enabled
        self.dir = pathlib.Path(history_dir).expanduser() if history_dir else pathlib.Path.home() / ".config" / "desk-ai" / "history"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sessions: list[Session] = []
        self.current: Session | None = None
        self.load_sessions()

    # -- session discovery

    def load_sessions(self) -> None:
        """Load all existing session files from the history directory into memory."""
        if not self.dir.exists():
            return
        paths = sorted(self.dir.glob("*.json"))
        for path in paths[:-self.MAX_SESSIONS]:
            try:
                path.unlink()
            except OSError:
                pass
        for path in paths[-self.MAX_SESSIONS:]:
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get("name") or path.stem
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                messages = []
            self.sessions.append(Session(name=name, messages=messages))
        if self.sessions and self.current is None:
            self.current = self.sessions[-1]

    # -- session lifecycle

    def new_session(self) -> None:
        name = time.strftime("%Y-%m-%d_%H-%M-%S")
        self.current = Session(name=name)
        self.sessions.append(self.current)
        if len(self.sessions) > self.MAX_SESSIONS:
            old = self.sessions.pop(0)
            old_path = self.dir / f"{old.name}.json"
            try:
                old_path.unlink()
            except OSError:
                pass
        if self.enabled:
            self.save()

    def switch_previous(self) -> dict | None:
        if not self.current or len(self.sessions) < 2:
            return None
        idx = self.sessions.index(self.current)
        if idx == 0:
            return None
        prev = self.sessions[idx - 1]
        self.sessions.pop(idx)
        self.sessions.append(prev)
        self.current = prev
        return {"name": prev.name, "messages": list(prev.messages)}

    # -- message access

    def append(self, message: dict) -> None:
        if self.current is None:
            self.new_session()
        self.current.messages.append(message)
        if self.enabled:
            self.save()

    def messages(self) -> list:
        """Return the actual message list (not a copy) so callers can mutate it."""
        return self.current.messages if self.current else []

    def truncate_after(self, mark: int) -> None:
        if self.current is None:
            return
        self.current.messages = self.current.messages[: mark + 1]
        if self.enabled:
            self.save()

    # -- persistence

    def save(self) -> None:
        if not self.enabled or self.current is None:
            return
        path = self.dir / f"{self.current.name}.json"
        data = {"name": self.current.name, "messages": self.current.messages}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
