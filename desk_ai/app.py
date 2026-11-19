"""Application entry point."""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from . import config  # noqa: E402
from .ui import CSS, Panel  # noqa: E402


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.local.DeskAI")
        self.panel = None

    def do_activate(self):
        if self.panel is None:
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            cfg = config.load()
            self.panel = Panel(self, cfg, config.load_api_key(cfg))
        self.panel.present()
        self.panel.entry.grab_focus()

        # Reload settings without restarting: <Ctrl>R or /reload in the entry.
        action = Gio.SimpleAction.new("reload", None)
        action.connect("activate", lambda _a, _p: self.panel.reload_settings())
        self.add_action(action)
        self.set_accels_for_action("app.reload", ["<Ctrl>R"])

        # Full process restart: reloads every Python module from disk.
        restart_action = Gio.SimpleAction.new("restart", None)
        restart_action.connect("activate", lambda _a, _p: restart())
        self.add_action(restart_action)
        self.set_accels_for_action("app.restart", ["<Ctrl><Shift>R"])


def print_config():
    """`python assistant.py --print-config`: show effective settings and the exact system prompt."""
    from . import prompt
    from .tools import Toolbox

    cfg = config.load()
    print(config.describe(cfg, config.load_api_key(cfg)))
    text, warning = prompt.build(cfg, Toolbox(lambda _t: False, cfg.command_timeout).names())
    if warning:
        print(f"warning: {warning}")
    print("\n--- system prompt (as sent to the model) ---")
    print(text)


def restart():
    """Re-exec the current process so all Python modules are reloaded from disk."""
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


def main():
    if "--print-config" in sys.argv:
        print_config()
        return
    sys.exit(App().run(sys.argv))
