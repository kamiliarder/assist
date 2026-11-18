"""Application entry point."""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

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


def main():
    sys.exit(App().run(sys.argv))
