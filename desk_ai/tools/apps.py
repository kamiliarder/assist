"""Application launching tool."""
from gi.repository import Gio, GLib

from .env import LEAKY_ENV


def open_app(self, name):
    """Launch an installed app by its .desktop name (no arbitrary commands)."""
    query = name.lower().strip()
    apps = [a for a in Gio.AppInfo.get_all() if a.should_show()]
    exact = [a for a in apps if a.get_name().lower() == query]
    fuzzy = [a for a in apps if query in a.get_name().lower()]
    match = (exact or fuzzy or [None])[0]
    if not match:
        return f"error: no installed app matching '{name}'"
    ctx = Gio.AppLaunchContext()
    for var in LEAKY_ENV:
        ctx.unsetenv(var)
    try:
        ok = match.launch([], ctx)
    except GLib.Error as e:
        return f"error: failed to launch {match.get_name()}: {e.message}"
    return f"started {match.get_name()}" if ok else f"error: launching {match.get_name()} failed"
