"""Media playback control tool."""

from .env import _run


def media(self, action):
    allowed = {"play-pause", "next", "previous", "status", "now-playing"}
    if action not in allowed:
        return f"error: action must be one of {sorted(allowed)}"
    if action == "now-playing":
        return _run(["playerctl", "metadata", "--format", "{{artist}} - {{title}}"]) or "nothing playing"
    return _run(["playerctl", action]) or "ok"
