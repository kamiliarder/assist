"""The chat window."""
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .llm import Agent  # noqa: E402
from .tools import Toolbox  # noqa: E402

CSS = b"""
textview, textview text { font-size: 14px; }
.confirm { background: alpha(@warning_color, 0.18); border-radius: 8px; padding: 8px; }
"""


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app, cfg, key):
        super().__init__(application=app, title="desk-ai")
        self.cfg = cfg
        self.key = key
        self.history = []
        self.busy = False
        self._pending = None
        self.toolbox = Toolbox(self.confirm, cfg.command_timeout)
        self.agent = Agent(cfg, key, self.toolbox)

        self.set_default_size(cfg.window_width, cfg.window_height)
        self.connect("close-request", self.on_close)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        self.set_child(box)

        self.view = Gtk.TextView(editable=False, cursor_visible=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=6, right_margin=6)
        self.buf = self.view.get_buffer()
        self.buf.create_tag("you", weight=700, foreground="#7aa2ff")
        self.buf.create_tag("ai", weight=700, foreground="#b794f6")
        self.buf.create_tag("tool", style=Pango.Style.ITALIC, foreground="#8a8799")
        self.buf.create_tag("err", foreground="#ff6b6b")
        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.scroller.set_child(self.view)
        box.append(self.scroller)

        # Approval bar for run_command (hidden until a command needs approval)
        self.confirm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.confirm_box.add_css_class("confirm")
        self.confirm_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        row = Gtk.Box(spacing=6)
        self.allow_btn = Gtk.Button(label="Allow")
        self.allow_btn.add_css_class("suggested-action")
        self.deny_btn = Gtk.Button(label="Deny")
        self.allow_btn.connect("clicked", lambda _b: self._answer(True))
        self.deny_btn.connect("clicked", lambda _b: self._answer(False))
        row.append(self.allow_btn)
        row.append(self.deny_btn)
        self.confirm_box.append(self.confirm_label)
        self.confirm_box.append(row)
        self.confirm_box.set_visible(False)
        box.append(self.confirm_box)

        self.entry = Gtk.Entry(placeholder_text="Ask anything…")
        self.entry.connect("activate", self.on_submit)
        box.append(self.entry)

        self.append(f"model: {cfg.model}\n", "tool")
        for w in cfg.warnings:
            self.append(f"config: {w}\n", "err")
        if not key:
            self.append("No API key. Set NVIDIA_API_KEY or put it in ~/.config/desk-ai/api_key\n", "err")

    # -- UI helpers (main thread only)

    def append(self, text, tag=None):
        end = self.buf.get_end_iter()
        if tag:
            self.buf.insert_with_tags_by_name(end, text, tag)
        else:
            self.buf.insert(end, text)
        adj = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    def on_close(self, _win):
        self.set_visible(False)  # keep the process (and chat history) alive
        return True

    def on_submit(self, entry):
        text = entry.get_text().strip()
        if not text or self.busy or not self.key:
            return
        entry.set_text("")
        self.busy = True
        self.append("\nyou\n", "you")
        self.append(f"{text}\n")
        self.append("\nai\n", "ai")
        self.history.append({"role": "user", "content": text})
        threading.Thread(target=self.worker, args=(text,), daemon=True).start()

    def worker(self, text):
        def ui(fn, *a):
            GLib.idle_add(lambda: fn(*a) or False)

        mark = len(self.history) - 1  # index of this turn's user message
        try:
            self.agent.run_turn(self.history,
                                on_text=lambda t: ui(self.append, t),
                                on_status=lambda s: ui(self.append, s, "tool"),
                                on_pause=lambda r: self.confirm_pause(r))
        except Exception as e:  # noqa: BLE001
            ui(self.append, f"\n[error] {e}\n", "err")
            if len(self.history) == mark + 1:
                # Failed before anything happened: drop the dangling message and
                # put the text back so you can just press Enter again.
                del self.history[mark:]
                ui(self.entry.set_text, text)
            else:
                ui(self.append, "(a tool already ran this turn, so your message was kept)\n", "tool")
        finally:
            ui(self.append, "\n")
            ui(self.finish)

    def finish(self):
        self.busy = False
        self.entry.grab_focus()

    # -- approval flow (called from the worker thread)

    def confirm(self, text):
        ev, box = threading.Event(), {"ok": False}

        def show():
            self.allow_btn.set_label("Allow")
            self.deny_btn.set_label("Deny")
            self.confirm_label.set_text(f"Run this command?\n\n{text}")
            self.confirm_box.set_visible(True)
            self._pending = (ev, box)
            self.present()  # bring the window forward so the prompt is seen
            return False

        GLib.idle_add(show)
        if not ev.wait(self.cfg.approval_timeout):
            GLib.idle_add(lambda: self._answer(False) or False)
            ev.wait(5)
        return box["ok"]

    def _answer(self, ok):
        if self._pending is None:
            return
        ev, box = self._pending
        self._pending = None
        box["ok"] = ok
        self.confirm_box.set_visible(False)
        ev.set()

    def confirm_pause(self, rounds):
        ev, box = threading.Event(), {"ok": False}

        def show():
            self.allow_btn.set_label("Resume")
            self.deny_btn.set_label("Stop")
            self.confirm_label.set_text(
                f"{rounds} tools have run this turn. Resume or stop?")
            self.confirm_box.set_visible(True)
            self._pending = (ev, box)
            self.present()
            return False

        GLib.idle_add(show)
        if not ev.wait(self.cfg.approval_timeout):
            GLib.idle_add(lambda: self._answer(False) or False)
            ev.wait(5)
        GLib.idle_add(lambda: (self.allow_btn.set_label("Allow"),
                               self.deny_btn.set_label("Deny")) or False)
        return box["ok"]
