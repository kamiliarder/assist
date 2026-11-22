"""The chat window: a GTK4 view over ordis-core (via ordis.ipc). Holds no session state."""

from __future__ import annotations

import os
import pathlib

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gio, Gtk, Pango

from ...core.config import load as load_config
from ...ipc.client import ClientCallbacks, CoreClient
from .mdview import MarkdownWriter

CSS = b"""
textview, textview text { font-size: 14px; }
.confirm { background: alpha(@warning_color, 0.18); border-radius: 8px; padding: 8px; }
"""


def _load_user_css(css_file: str) -> Gtk.CssProvider | None:
    """Load user CSS file if configured. Returns provider or None."""
    if not css_file:
        return None
    path = pathlib.Path(css_file).expanduser()
    if not path.exists():
        return None
    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(str(path))
        return provider
    except Exception:
        return None


class Panel(Gtk.ApplicationWindow, ClientCallbacks):
    def __init__(self, app, client: CoreClient):
        super().__init__(application=app, title="ordis")
        self.app = app
        self.client = client
        client.callbacks = self
        self.cfg = load_config()      # window/markdown/css settings are the GUI's own
        self.snap: dict = {}          # what the daemon told us: model, key state, warnings
        self._pending_request = None  # id of the approval the daemon is waiting on
        self._pending_images: list[str] = []
        self._ever_connected = False

        self.set_default_size(self.cfg.window_width, self.cfg.window_height)
        self.connect("close-request", self.on_close)

        # Load built-in CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Load user CSS if configured (higher priority so it can override)
        self._user_css_provider = _load_user_css(self.cfg.css_file)
        if self._user_css_provider:
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._user_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

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
        self.cancel_btn.connect("clicked", lambda _b: self.client.cancel())
        box.append(self.cancel_btn)

        # Connection indicator: are we attached to ordis-core?
        self.health_label = Gtk.Label(label="●", xalign=1)
        self.health_label.add_css_class("dim")
        self.health_label.set_tooltip_text("ordis-core connection")
        box.append(self.health_label)

        self._set_health_indicator(False)
        client.start()

    def _initial_render(self):
        snap = self.snap
        if not snap:
            return
        self.append(f"model: {snap.get('model')} @ {snap.get('base_url')}\n", "tool")
        for w in snap.get("warnings", []):
            self.append(f"config: {w}\n", "err")
        if not snap.get("has_key") and snap.get("requires_key"):
            self.append("No API key in ordis-core. Set ORDIS_API_KEY (or the agenix key file) "
                        "or put it in ~/.config/ordis/api_key, then /reload\n", "err")

    def _reload_user_css(self):
        """Reload user CSS from config file."""
        if self._user_css_provider:
            Gtk.StyleContext.remove_provider_for_display(
                Gdk.Display.get_default(), self._user_css_provider)
        self._user_css_provider = _load_user_css(self.cfg.css_file)
        if self._user_css_provider:
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._user_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def reload(self):
        """GUI settings are re-read here; the daemon re-reads its own."""
        self.cfg = load_config()
        self._reload_user_css()
        self.client.command("reload", on_done=lambda r: self.append(
            f"\n[reload failed: {r['error']}]\n", "err") if "error" in r else None)
        self.entry.grab_focus()

    # -- ClientCallbacks (all on the GTK main thread) ---------------------

    def on_connection(self, connected, snapshot):
        self._set_health_indicator(connected)
        if connected:
            self._ever_connected = True
            self._apply_snapshot(snapshot)
        elif self._ever_connected:
            self.append("\n[lost connection to ordis-core; reconnecting…]\n", "err")
            self.cancel_btn.set_visible(False)
            self._hide_confirm()
        else:
            self.append("\n[ordis-core isn't answering. Start it with: "
                        "systemctl --user start ordis-core  (retrying…)]\n", "err")

    def on_snapshot(self, snapshot):
        self._apply_snapshot(snapshot)
        self._initial_render()

    def on_history(self, name, messages):
        self.md.close()
        self.buf.set_text("")
        self._initial_render()
        self.append(f"[session: {name}, {len(messages)} messages]\n", "tool")
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content") or ""
            if role == "user":
                self.append("\nyou\n", "you")
                self.append_md(content if isinstance(content, str) else str(content))
            elif role == "assistant" and content:
                self.append(f"\nai\n{content}\n", "ai")
            elif role == "tool":
                first = content.splitlines()[0] if content else ""
                self.append(f"\n→ {first[:200]}\n", "tool")
        self.append("")

    def on_user(self, text, images):
        self.append("\nyou\n", "you")
        self.append_md(text)
        self.cancel_btn.set_visible(True)

    def on_text(self, text: str) -> None:
        if text == "\nai\n":
            self.append(text, "ai")
        else:
            self.append_md(text)

    def on_status(self, status: str) -> None:
        self.append(status, "tool")

    def on_finish(self) -> None:
        self.cancel_btn.set_visible(False)
        self._hide_confirm()
        self.entry.grab_focus()

    def on_confirm(self, request_id, kind, text, extra):
        pause = kind == "pause"
        self.allow_btn.set_label("Resume" if pause else "Allow")
        self.deny_btn.set_label("Stop" if pause else "Deny")
        self.confirm_label.set_text(text if pause else f"Run this command?\n\n{text}")
        self.confirm_box.set_visible(True)
        self._pending_request = request_id
        self.present()

    def on_confirm_done(self, request_id):
        if request_id == self._pending_request:
            self._hide_confirm()

    def on_error(self, text):
        self.append(f"\n[{text}]\n", "err")

    def _apply_snapshot(self, snap):
        self.snap = snap or {}
        model = self.snap.get("model")
        for i in range(self._model_store.get_n_items()):
            if self._model_store.get_string(i) == model:
                self.model_dropdown.handler_block(self._model_changed_handler)
                self.model_dropdown.set_selected(i)
                self.model_dropdown.handler_unblock(self._model_changed_handler)
                break

    def _hide_confirm(self):
        self._pending_request = None
        self.confirm_box.set_visible(False)

    # -- UI helpers (main thread only) ------------------------------------

    def _create_markdown_tags(self):
        """Styles for ordis/markdown.py. Names must match; later tags win over earlier ones.
        Colors use GTK theme color names (e.g., @view_fg_color, @accent_bg_color) so they
        adapt to user themes like Noctalia. Fallback hex values are used if theme colors
        aren't available (e.g., when user CSS isn't loaded)."""
        styles = {
            "dim": dict(foreground="@theme_unfocused_fg_color"),
            "bold": dict(weight=700),
            "italic": dict(style=Pango.Style.ITALIC),
            "strike": dict(strikethrough=True),
            "link": dict(foreground="@accent_color", underline=Pango.Underline.SINGLE),
            "h1": dict(weight=700, scale=1.4, pixels_above_lines=6, foreground="@view_fg_color"),
            "h2": dict(weight=700, scale=1.25, pixels_above_lines=4, foreground="@view_fg_color"),
            "h3": dict(weight=700, scale=1.1, pixels_above_lines=2, foreground="@view_fg_color"),
            "code": dict(family="monospace", background="@card_bg_color", foreground="@accent_color"),
            "quote": dict(left_margin=16, foreground="@theme_unfocused_fg_color"),
            "codeblock": dict(family="monospace", paragraph_background="@card_bg_color",
                              foreground="@view_fg_color", left_margin=10, right_margin=10),
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
        if self.cfg.render_markdown:
            self.md.write(text)
        else:
            self.append(text)

    def on_close(self, _win):
        # The session lives in ordis-core, so closing the window can really end this process.
        self.client.close()
        self.app.quit()
        return False

    def on_submit(self, entry):
        text = entry.get_text().strip()
        if text == "/reload":
            entry.set_text("")
            self.reload()
            return
        if text == "/restart":
            entry.set_text("")
            self.restart()
            return
        if text == "/prev-session":
            entry.set_text("")
            self.client.command("switch_session", direction="previous", on_done=lambda r: self.append(
                f"\n[{r['error']}]\n", "tool") if "error" in r else None)
            return
        if text == "/new-session":
            entry.set_text("")
            self.client.command("new_session", on_done=lambda r: self.append(
                f"\n[{r['error']}]\n", "tool") if "error" in r else None)
            return
        snap = self.snap
        if not text or self.client.busy or (not snap.get("has_key") and snap.get("requires_key")):
            return
        images = list(self._pending_images)
        self._pending_images.clear()
        self._attach_label.set_text("")
        entry.set_text("")
        self.client.send_message(text, images=images)  # the daemon echoes it back as an event

    def _load_models(self):
        def done(reply):
            models = reply.get("models") or []
            self._model_store.splice(0, self._model_store.get_n_items(), models)
            self._apply_snapshot(self.snap)
            self.append(f"\n[models] {len(models)} available from {self.snap.get('base_url')}\n", "tool")
        self.client.command("list_models", on_done=done)

    def _on_model_changed(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        if idx < 0:
            return
        model = self._model_store.get_string(idx)
        if model == self.snap.get("model"):
            return
        self.client.command("set_model", model=model, on_done=lambda r: self.append(
            f"\n[{r['error']}]\n", "err") if "error" in r else self.append(
            f"\n[model switched to: {model}]\n", "tool"))
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
        if self._pending_request is None:
            return
        self.client.answer(self._pending_request, ok)
        self._hide_confirm()

    def restart(self):
        self.append("\n[restarting the GUI; your session stays in ordis-core]\n", "tool")
        self.client.close()
        from .app import restart
        restart()

    def _set_health_indicator(self, healthy: bool):
        if healthy:
            self.health_label.set_markup('<span color="green">●</span>')
            self.health_label.set_tooltip_text("attached to ordis-core")
        else:
            self.health_label.set_markup('<span color="red">●</span>')
            self.health_label.set_tooltip_text("ordis-core unavailable")
