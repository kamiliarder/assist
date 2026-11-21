"""GTK4 Application entry point: thin wrapper over Core + Panel."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk

from ... import core
from .panel import Panel, CSS as PanelCSS


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.local.DeskAI")
        self.panel = None
        self.core_instance = None

    def do_activate(self):
        if self.panel is None:
            provider = Gtk.CssProvider()
            provider.load_from_data(PanelCSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            self.core_instance = core.Core(callbacks=None)
            self.panel = Panel(self, self.core_instance)
            self.panel.present()
            self.panel.entry.grab_focus()

        self.panel.present()
        self.panel.entry.grab_focus()

    def _do_reload(self):
        self.core_instance.reload_config()
        self.panel._initial_render()
        self.panel.entry.grab_focus()

        # Reload settings: <Ctrl>R or /reload in the entry.
        action = Gio.SimpleAction.new("reload", None)
        action.connect("activate", lambda _a, _p: self._do_reload())
        self.add_action(action)
        self.set_accels_for_action("app.reload", ["<Ctrl>R"])

        # Full process restart.
        restart_action = Gio.SimpleAction.new("restart", None)
        restart_action.connect("activate", lambda _a, _p: restart())
        self.add_action(restart_action)
        self.set_accels_for_action("app.restart", ["<Ctrl><Shift>R"])


def restart():
    entry_point = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "assistant.py")
    )
    os.execv(sys.executable, [sys.executable, entry_point])


def main():
    if "--print-config" in sys.argv:
        from ...core.config import load, describe, load_api_key
        cfg = load()
        print(describe(cfg, load_api_key(cfg)))
        return
    sys.exit(App().run(sys.argv))
