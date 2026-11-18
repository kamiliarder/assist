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
    def __init__(self, enabled: bool, history_dir: str = ""):
        self.enabled = enabled
        self.dir = pathlib.Path(history_dir).expanduser() if history_dir else pathlib.Path.home() / ".config" / "desk-ai" / "history"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sessions: list[Session] = []
        self.current: Session | None = None

    # -- session lifecycle

    def new_session(self) -> None:
        name = time.strftime("%Y-%m-%d_%H-%M-%S")
        self.current = Session(name=name)
        self.sessions.append(self.current)
        if self.enabled:
            self.save()

    def switch_previous(self) -> dict | None:
        if not self.current or len(self.sessions) < 2:
            return None
        idx = self.sessions.index(self.current)
        prev = self.sessions[idx - 1]
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
        return list(self.current.messages) if self.current else []

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
