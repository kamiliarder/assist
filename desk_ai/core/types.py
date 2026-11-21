"""Interfaces between Core and any UI layer."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class CoreCallbacks(Protocol):
    """Called by Core to communicate with the UI.

    Some callbacks run on the worker thread (confirm, on_pause); UI layers must
    marshal to the main thread themselves (e.g. GLib.idle_add).
    """

    def on_text(self, text: str) -> None:
        """Streamed assistant text (markdown or plain)."""

    def on_status(self, status: str) -> None:
        """Non-streaming status lines (retry, fallback, tool results, errors)."""

    def on_pause(self, rounds: int) -> bool:
        """Called every N tool rounds; return True to resume, False to stop."""

    def confirm(self, text: str) -> bool:
        """Blocking confirmation dialog. Runs on the worker thread."""

    def on_finish(self) -> None:
        """Called when the worker thread finishes (success, error, or cancel)."""


@runtime_checkable
class CoreUI(Protocol):
    """UI layer that drives Core."""

    def send_message(self, text: str, images: list[str] | None = None) -> None: ...

    def cancel(self) -> None: ...

    def reload_config(self) -> None: ...

    def switch_session(self, direction: str) -> None: ...

    def present(self) -> None: ...
