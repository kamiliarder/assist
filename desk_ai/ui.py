"""The chat window."""
import importlib
import pathlib
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk, Pango  # noqa: E402

from . import config  # noqa: E402
from .config import config_dir  # noqa: E402
from .history import HistoryStore  # noqa: E402
from .llm import Agent, CancelledError  # noqa: E402
from .mdview import MarkdownWriter  # noqa: E402
from .tools import Toolbox  # noqa: E402

CSS = b"""
textview, textview text { font-size: 14px; }
.confirm { background: alpha(@warning_color, 0.18); border-radius: 8px; padding: 8px; }
"""


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app, cfg, key, resume_session=None):
        super().__init__(application=app, title="desk-ai")
        self.cfg = cfg
        self.key = key
        self.busy = False
        self._pending = None
        self._cancel = threading.Event()
        self.store = HistoryStore(cfg.history_enabled, cfg.history_dir)
        if resume_session is not None:
            self.store.current = resume_session
            if resume_session not in self.store.sessions:
                self.store.sessions.append(resume_session)
        else:
            self.store.new_session()
        self.toolbox = Toolbox(self.confirm, cfg.command_timeout)
        self.agent = Agent(cfg, key, self.toolbox)

        self.set_default_size(cfg.window_width, cfg.window_height)
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

        # Image attachment row: button + chosen-file chips
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

        self._pending_images: list[str] = []

        # Cancel button (shown only while a turn is running)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.add_css_class("destructive-action")
        self.cancel_btn.set_visible(False)
        self.cancel_btn.connect("clicked", lambda _b: self._cancel.set())
        box.append(self.cancel_btn)

        self.append(f"model: {cfg.model} @ {cfg.base_url}\n", "tool")
        for w in cfg.warnings:
            self.append(f"config: {w}\n", "err")
        if not key and config.requires_api_key(cfg):
            self.append("No API key. Set NVIDIA_API_KEY or put it in ~/.config/desk-ai/api_key\n", "err")

        # Model list is not loaded at startup; click ⮌ to refresh manually.
        # The configured model (cfg.model) is used by the agent immediately.

    def _load_models(self):
        """Fetch available models from {base_url}/models and fill the dropdown."""
        key = self.key if config.requires_api_key(self.cfg) else None
        models = config.fetch_models(self.cfg.base_url, key=key)
        self._model_store.splice(0, self._model_store.get_n_items(), models)
        if models:
            # Select the currently configured model if it's in the list.
            # Do not auto-switch to index 0 just because the configured model
            # isn't available — that changes the effective model without asking.
            try:
                idx = models.index(self.cfg.model)
            except ValueError:
                idx = -1
            if idx >= 0:
                self.model_dropdown.handler_block(self._model_changed_handler)
                self.model_dropdown.set_selected(idx)
                self.model_dropdown.handler_unblock(self._model_changed_handler)
        self.append(f"\n[models] {len(models)} available from {self.cfg.base_url}\n", "tool")

    def _on_model_changed(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        if idx < 0:
            return
        model = self._model_store.get_string(idx)
        if model == self.cfg.model:
            return
        self.cfg.model = model
        self.agent = Agent(self.cfg, self.key, self.toolbox)
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

    # -- UI helpers (main thread only)

    def _create_markdown_tags(self):
        """Styles for desk_ai/markdown.py. Names must match; later tags win over earlier ones."""
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
        for level in range(4):  # list items hang their bullet; continuation lines just indent
            styles[f"li{level}"] = dict(left_margin=22 + 16 * level, indent=-16)
            styles[f"lc{level}"] = dict(left_margin=22 + 16 * level)
        for name, props in styles.items():
            self.buf.create_tag(name, **props)

    def _scroll_end(self):
        adj = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    def append(self, text, tag=None):
        self.md.close()  # anything else written to the buffer ends the markdown segment
        end = self.buf.get_end_iter()
        if tag:
            self.buf.insert_with_tags_by_name(end, text, tag)
        else:
            self.buf.insert(end, text)
        self._scroll_end()

    def append_md(self, text):
        """Model/user text: rendered as markdown (re-rendered as chunks arrive) unless disabled."""
        if self.cfg.render_markdown:
            self.md.write(text)
        else:
            self.append(text)

    def on_close(self, _win):
        self.set_visible(False)  # keep the process (and chat history) alive
        return True

    def on_submit(self, entry):
        text = entry.get_text().strip()
        if text == "/reload":
            entry.set_text("")
            self.reload_settings()
            return
        if text == "/restart":
            entry.set_text("")
            self.restart()
            return
        if text == "/prev-session":
            entry.set_text("")
            prev = self.store.switch_previous()
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
        if not text or self.busy or (not self.key and config.requires_api_key(self.cfg)):
            return
        images = list(self._pending_images)
        self._pending_images.clear()
        self._attach_label.set_text("")
        entry.set_text("")
        self.busy = True
        self._cancel.clear()
        self.cancel_btn.set_visible(True)
        self.append("\nyou\n", "you")
        self.append_md(text)
        if images:
            self.append(f"\n[attached {len(images)} image(s)]\n", "tool")
        self.append("\n")
        self.append("\nai\n", "ai")
        self.store.append({"role": "user", "content": text})
        threading.Thread(target=self.worker, args=(text, self._cancel, images), daemon=True).start()

    def reload_settings(self):
        """Re-read config.toml, hot-reload Python modules from disk, rebuild the agent, and refresh the UI.

        The chat session stays alive; history is preserved in memory.
        """
        new_cfg = config.load()
        new_key = config.load_api_key(new_cfg)
        self.cfg = new_cfg
        self.key = new_key

        # Best-effort hot-reload of every desk_ai module so code changes take effect
        # without killing the session. Modules are reloaded in dependency order where
        # possible; anything that fails is reported but doesn't abort the reload.
        reloaded, failed = self._hot_reload_modules()
        self.append(f"\n[modules reloaded: {', '.join(reloaded) or 'none'}]\n", "tool")
        for mod, err in failed:
            self.append(f"[reload failed: {mod} — {err}]\n", "err")

        # Re-import classes from reloaded modules so new code is actually used
        from .llm import Agent as NewAgent
        from .tools import Toolbox as NewToolbox
        self.toolbox = NewToolbox(self.confirm, new_cfg.command_timeout)
        self.agent = NewAgent(new_cfg, new_key, self.toolbox)
        self.append(f"\n[config reloaded] model: {new_cfg.model} @ {new_cfg.base_url}\n", "tool")
        for w in new_cfg.warnings:
            self.append(f"config: {w}\n", "err")
        if not new_key and config.requires_api_key(new_cfg):
            self.append("No API key. Set NVIDIA_API_KEY or put it in ~/.config/desk-ai/api_key\n", "err")
        self.entry.grab_focus()

    def _hot_reload_modules(self):
        """Reload all loaded desk_ai modules. Returns (reloaded_names, failed_list)."""
        import desk_ai
        package_dir = pathlib.Path(desk_ai.__file__).parent
        targets = sorted(
            m for m in sys.modules
            if m.startswith("desk_ai") and sys.modules[m] is not None
            and getattr(sys.modules[m], "__file__", None)
            and pathlib.Path(sys.modules[m].__file__).parent == package_dir
        )
        # Reverse dependency order: submodules first, then the package itself.
        targets = sorted(targets, key=lambda m: m.count("."), reverse=True)
        reloaded, failed = [], []
        for name in targets:
            mod = sys.modules[name]
            try:
                importlib.reload(mod)
                reloaded.append(name)
            except Exception as e:  # noqa: BLE001
                failed.append((name, str(e) or type(e).__name__))
        return reloaded, failed

    def restart(self):
        """Re-exec the process so every Python module is reloaded from disk.

        Unlike reload_settings(), this picks up code changes in llm.py, tools/,
        prompt.py, etc. The current session is saved to a marker file and
        restored on the next launch so the chat persists.
        """
        self.append("\n[restarting desk-ai...]\n", "tool")
        if self.store.current is not None:
            marker = config_dir() / ".resume_session"
            marker.write_text(self.store.current.name)
        from .app import restart
        restart()

    def worker(self, text, cancel, images=None):
        def ui(fn, *a):
            GLib.idle_add(lambda: fn(*a) or False)

        msgs = self.store.messages()
        mark = len(msgs) - 1  # index of this turn's user message
        try:
            self.agent.run_turn(msgs, cancel, images=images,
                                on_text=lambda t: ui(self.append_md, t),
                                on_status=lambda s: ui(self.append, s, "tool"),
                                on_pause=lambda r: self.confirm_pause(r))
            self.store.save()
        except CancelledError:
            ui(self.append, "\n[cancelled]\n", "tool")
        except Exception as e:  # noqa: BLE001
            ui(self.append, f"\n[error] {e}\n", "err")
            if len(msgs) == mark + 1:
                # Failed before anything happened: drop the dangling message and
                # put the text back so you can just press Enter again.
                self.store.truncate_after(mark)
                ui(self.entry.set_text, text)
            else:
                ui(self.append, "(a tool already ran this turn, so your message was kept)\n", "tool")
        finally:
            ui(self.append, "\n")
            self.store.save()
            ui(self.finish)

    def finish(self):
        self.busy = False
        self.cancel_btn.set_visible(False)
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
