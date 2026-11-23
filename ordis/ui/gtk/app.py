"""GTK4 Application entry point: a thin client of ordis-core.

The session lives in the daemon, so closing the window really closes this process and the
conversation is still there next time.
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gio, Gtk

from ...core.config import load
from ...ipc.client import CoreClient
from ...ipc.protocol import resolve_socket_path
from .panel import Panel, CSS as PanelCSS


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.local.Ordis")
        self.panel = None
        self.client = None

    def do_startup(self):
        Gtk.Application.do_startup(self)

        # Reload settings: <Ctrl>R or /reload in the entry.
        reload_action = Gio.SimpleAction.new("reload", None)
        reload_action.connect("activate", lambda _a, _p: self.panel and self.panel.reload())
        self.add_action(reload_action)
        self.set_accels_for_action("app.reload", ["<Ctrl>R"])

        # Restart this GUI process (the session is untouched: it lives in ordis-core).
        restart_action = Gio.SimpleAction.new("restart", None)
        restart_action.connect("activate", lambda _a, _p: restart())
        self.add_action(restart_action)
        self.set_accels_for_action("app.restart", ["<Ctrl><Shift>R"])

        # Push-to-talk: <Ctrl>Space toggles the mic button in the panel.
        ptt_action = Gio.SimpleAction.new("ptt", None)
        ptt_action.connect("activate", lambda _a, _p: self.panel and self.panel._ptt_activate())
        self.add_action(ptt_action)
        self.set_accels_for_action("app.ptt", ["<Ctrl>space"])

    def do_activate(self):
        if self.panel is None:
            provider = Gtk.CssProvider()
            provider.load_from_data(PanelCSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            self.client = CoreClient(
                resolve_socket_path(load().socket_path),
                marshal=lambda fn: GLib.idle_add(lambda: fn() or False))
            self.panel = Panel(self, self.client)

        self.panel.present()
        self.panel.entry.grab_focus()


def restart():
    entry_point = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "assistant.py")
    )
    os.execv(sys.executable, [sys.executable, entry_point])


def main():
    if "--print-config" in sys.argv:
        from ...core.config import load, describe, load_api_key
        cfg = load()
        print(describe(cfg, load_api_key(cfg)))
        return
    sys.exit(App().run(sys.argv))
