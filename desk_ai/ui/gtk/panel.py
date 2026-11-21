"""The chat window: thin GTK4 UI layer over Core."""

from __future__ import annotations

import pathlib
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gio, Gtk, Pango

from ... import core
from ...core.config import config_dir
from .mdview import MarkdownWriter

CSS = b"""
textview, textview text { font-size: 14px; }
.confirm { background: alpha(@warning_color, 0.18); border-radius: 8px; padding: 8px; }
"""




class Panel(Gtk.ApplicationWindow):
    def __init__(self, app, core_instance: core.Core):
        super().__init__(application=app, title="desk-ai")
        self.app = app
        self.core = core_instance
        core_instance.callbacks = self  # register as the callback target
        core_instance._marshal = lambda fn: GLib.idle_add(lambda: fn() or False)

        self.busy = False
        self.store = core_instance.history
        self.toolbox = core_instance.toolbox
        self.agent = core_instance.agent

        self._cancel = core_instance._cancel
        self._pending = None
        self._pending_images: list[str] = []

        self.set_default_size(core_instance.cfg.window_width, core_instance.cfg.window_height)
        self.connect("close-request", self.on_close)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        self.set_child(box)

        # Model selector row
        self.model_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.model_label = Gtk.Label(label="Model:", xalign=0)
        self.model_label.set_hexpand(False)
        self.model_dropdown = Gtk.DropDown()
        self.model_dropdown.set_hexpand(True)
        self.model_refresh = Gtk.Button(label="↻")
        self.model_refresh.set_tooltip_text("Refresh model list from the API")
        self.model_refresh.connect("clicked", lambda _b: self._load_models())
        self.model_row.append(self.model_label)
        self.model_row.append(self.model_dropdown)
        self.model_row.append(self.model_refresh)
        box.append(self.model_row)

        self._model_store = Gtk.StringList()
        self.model_dropdown.set_model(self._model_store)
        self._model_changed_handler = self.model_dropdown.connect("notify::selected", self._on_model_changed)

        self.view = Gtk.TextView(editable=False, cursor_visible=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=6, right_margin=6)
        self.buf = self.view.get_buffer()
        self.buf.create_tag("you", weight=700, foreground="#7aa2ff")
        self.buf.create_tag("ai", weight=700, foreground="#b794f6")
        self.buf.create_tag("tool", style=Pango.Style.ITALIC, foreground="#8a8799")
        self.buf.create_tag("err", foreground="#ff6b6b")
        self._create_markdown_tags()
        self.md = MarkdownWriter(self.buf, on_change=self._scroll_end)
        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.scroller.set_child(self.view)
        box.append(self.scroller)

        # Approval bar
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

        # Image attachment row
        self.attach_btn = Gtk.Button(label="🖼️ Attach image")
        self.attach_btn.set_tooltip_text("Attach an image to your next message (vision models only)")
        self.attach_btn.connect("clicked", self._on_attach_clicked)
        self.attach_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.attach_row.set_hexpand(True)
        self.attach_row.append(self.attach_btn)
        self._attach_label = Gtk.Label(label="", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self._attach_label.set_hexpand(True)
        self.attach_row.append(self._attach_label)
        box.append(self.attach_row)

        self.entry = Gtk.Entry(placeholder_text="Ask anything…")
        self.entry.connect("activate", self.on_submit)
        box.append(self.entry)

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.add_css_class("destructive-action")
        self.cancel_btn.set_visible(False)
        self.cancel_btn.connect("clicked", lambda _b: self.core.cancel())
        box.append(self.cancel_btn)

        self._initial_render()

    def _initial_render(self):
        cfg = self.core.cfg
        key = self.core.key
        self.append(f"model: {cfg.model} @ {cfg.base_url}\n", "tool")
        for w in cfg.warnings:
            self.append(f"config: {w}\n", "err")
        if not key and self._requires_api_key(cfg):
            self.append("No API key. Set NVIDIA_API_KEY or put it in ~/.config/desk-ai/api_key\n", "err")

    def _requires_api_key(self, cfg):
        from ...core.config import requires_api_key
        return requires_api_key(cfg)

    # -- CoreCallbacks implementation -------------------------------------

    def on_text(self, text: str) -> None:
        self.append_md(text)

    def on_status(self, status: str) -> None:
        self.append(status, "tool")

    def on_finish(self) -> None:
        self.cancel_btn.set_visible(False)
        self.entry.grab_focus()

    def on_pause(self, rounds: int) -> bool:
        return self.confirm_pause(rounds)

    def confirm(self, text: str) -> bool:
        ev, box = threading.Event(), {"ok": False}

        def show():
            self.allow_btn.set_label("Allow")
            self.deny_btn.set_label("Deny")
            self.confirm_label.set_text(f"Run this command?\n\n{text}")
            self.confirm_box.set_visible(True)
            self._pending = (ev, box)
            self.present()
            return False

        GLib.idle_add(show)
        if not ev.wait(self.core.cfg.approval_timeout):
            GLib.idle_add(lambda: self._answer(False) or False)
            ev.wait(5)
        return box["ok"]

    # -- UI helpers (main thread only) ------------------------------------

    def _create_markdown_tags(self):
        styles = {
            "dim": dict(foreground="#8a8799"),
            "bold": dict(weight=700),
            "italic": dict(style=Pango.Style.ITALIC),
            "strike": dict(strikethrough=True),
            "link": dict(foreground="#7aa2ff", underline=Pango.Underline.SINGLE),
            "h1": dict(weight=700, scale=1.4, pixels_above_lines=6),
            "h2": dict(weight=700, scale=1.25, pixels_above_lines=4),
            "h3": dict(weight=700, scale=1.1, pixels_above_lines=2),
            "code": dict(family="monospace", background="#3a3a42", foreground="#f0c987"),
            "quote": dict(left_margin=16, foreground="#b4b1c4"),
            "codeblock": dict(family="monospace", paragraph_background="#26262c",
                              foreground="#d6d6e0", left_margin=10, right_margin=10),
        }
        for level in range(4):
            styles[f"li{level}"] = dict(left_margin=22 + 16 * level, indent=-16)
            styles[f"lc{level}"] = dict(left_margin=22 + 16 * level)
        for name, props in styles.items():
            self.buf.create_tag(name, **props)

    def _scroll_end(self):
        adj = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    def append(self, text, tag=None):
        self.md.close()
        end = self.buf.get_end_iter()
        if tag:
            self.buf.insert_with_tags_by_name(end, text, tag)
        else:
            self.buf.insert(end, text)
        self._scroll_end()

    def append_md(self, text):
        if self.core.cfg.render_markdown:
            self.md.write(text)
        else:
            self.append(text)

    def on_close(self, _win):
        self.set_visible(False)
        return True

    def on_submit(self, entry):
        text = entry.get_text().strip()
        if text == "/reload":
            entry.set_text("")
            self.core.reload_config()
            self._initial_render()
            self.entry.grab_focus()
            return
        if text == "/restart":
            entry.set_text("")
            self.restart()
            return
        if text == "/prev-session":
            entry.set_text("")
            prev = self.core.switch_session("previous")
            if prev is None:
                self.append("\n[no previous session]\n", "tool")
            else:
                self.append(f"\n[switched to session: {prev['name']}]\n", "tool")
                self.append(f"({len(prev['messages'])} messages)\n", "tool")
                for m in prev["messages"]:
                    role = m.get("role", "?")
                    content = m.get("content") or ""
                    if role == "user":
                        self.append(f"\nyou\n{content}\n", "you")
                    elif role == "assistant":
                        self.append(f"\nai\n{content}\n", "ai")
                    elif role == "tool":
                        self.append(f"\n[tool result] {content}\n", "tool")
                self.store.save()
            return
        if not text or self.core.busy or (not self.core.key and self._requires_api_key(self.core.cfg)):
            return
        images = list(self._pending_images)
        self._pending_images.clear()
        self._attach_label.set_text("")
        entry.set_text("")
        self.core.send_message(text, images=images)
        self.cancel_btn.set_visible(True)
    def _load_models(self):
        key = self.core.key if self._requires_api_key(self.core.cfg) else None
        from ...core.config import fetch_models
        models = fetch_models(self.core.cfg.base_url, key=key)
        self._model_store.splice(0, self._model_store.get_n_items(), models)
        if models:
            try:
                idx = models.index(self.core.cfg.model)
            except ValueError:
                idx = -1
            if idx >= 0:
                self.model_dropdown.handler_block(self._model_changed_handler)
                self.model_dropdown.set_selected(idx)
                self.model_dropdown.handler_unblock(self._model_changed_handler)
        self.append(f"\n[models] {len(models)} available from {self.core.cfg.base_url}\n", "tool")

    def _on_model_changed(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        if idx < 0:
            return
        model = self._model_store.get_string(idx)
        if model == self.core.cfg.model:
            return
        self.core.cfg.model = model
        self.core.agent = core.Agent(self.core.cfg, self.core.key, self.core.toolbox)
        self.append(f"\n[model switched to: {model}]\n", "tool")
        self.entry.grab_focus()

    def _on_attach_clicked(self, _btn):
        dialog = Gtk.FileChooserNative(
            title="Attach image",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        filter_img = Gtk.FileFilter()
        filter_img.set_name("Images")
        for mime in ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"):
            filter_img.add_mime_type(mime)
        dialog.add_filter(filter_img)
        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        def on_response(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = dlg.get_file()
                if file is not None:
                    path = file.get_path()
                    if path:
                        self._pending_images.append(path)
                        name = pathlib.Path(path).name
                        self._attach_label.set_text(
                            (self._attach_label.get_text() + " • " if self._attach_label.get_text() else "")
                            + name
                        )
            dlg.destroy()

        dialog.connect("response", on_response)
        dialog.show()

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
        if not ev.wait(self.core.cfg.approval_timeout):
            GLib.idle_add(lambda: self._answer(False) or False)
            ev.wait(5)
        GLib.idle_add(lambda: (self.allow_btn.set_label("Allow"),
                               self.deny_btn.set_label("Deny")) or False)
        return box["ok"]

    def restart(self):
        self.append("\n[restarting desk-ai...]\n", "tool")
        if self.store.current is not None:
            marker = config_dir() / ".resume_session"
            marker.write_text(self.store.current.name)
        from .app import restart
        restart()
