"""The chat window: a GTK client that talks to the desk-ai daemon over a Unix socket."""
from __future__ import annotations

import json
import socket
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import config  # noqa: E402
from .daemon import SOCK_PATH  # noqa: E402
from .history import HistoryStore  # noqa: E402
from .mdview import MarkdownWriter  # noqa: E402

CSS = b"""
textview, textview text { font-size: 14px; }
.confirm { background: alpha(@warning_color, 0.18); border-radius: 8px; padding: 8px; }
"""


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app, cfg, key):
        super().__init__(application=app, title="desk-ai")
        self.cfg = cfg
        self.key = key
        self.busy = False
        self._pending = None
        self._cancel = threading.Event()
        self.store = HistoryStore(cfg.history_enabled, cfg.history_dir)
        self.store.new_session()

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

        self.entry = Gtk.Entry(placeholder_text="Ask anything…")
        self.entry.connect("activate", self.on_submit)
        box.append(self.entry)

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

        # Socket connection to the daemon (started in a background thread)
        self._sock = None
        self._sock_lock = threading.Lock()
        threading.Thread(target=self._connect_socket, daemon=True).start()

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
        self.append(f"\n[model switched to: {model}]\n", "tool")
        self.entry.grab_focus()

    # -- socket

    def _connect_socket(self):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(SOCK_PATH))
            with self._sock_lock:
                self._sock = sock
            GLib.idle_add(self._announce_ready)
            self._read_loop(sock)
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            GLib.idle_add(lambda: self.append(
                "\n[daemon not reachable at "
                f"{SOCK_PATH}. Start it with: python -m desk_ai.daemon]\n", "err"))
        finally:
            with self._sock_lock:
                self._sock = None

    def _announce_ready(self):
        self.append(f"\n[connected to daemon at {SOCK_PATH}]\n", "tool")
        self.entry.grab_focus()
        return False

    def _read_loop(self, sock):
        buf = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    GLib.idle_add(self._on_daemon_msg, msg)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            GLib.idle_add(lambda: self.append("\n[daemon disconnected]\n", "err"))
            with self._sock_lock:
                self._sock = None

    def _send(self, msg: dict):
        with self._sock_lock:
            sock = self._sock
        if sock is None:
            return
        try:
            sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        except (BrokenPipeError, OSError):
            with self._sock_lock:
                self._sock = None

    def _on_daemon_msg(self, msg: dict):
        mtype = msg.get("type")
        if mtype == "output":
            tag = msg.get("tag")
            self.append(msg.get("text", ""), tag)
        elif mtype == "done":
            self.finish()
        elif mtype == "confirm_request":
            self.show_confirm(msg.get("id"), msg.get("text", ""))
        elif mtype == "toggle":
            self.request_toggle()
        elif mtype == "status":
            self.busy = msg.get("busy", False)
            self.cancel_btn.set_visible(self.busy)
        return False

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
        entry.set_text("")
        self.busy = True
        self._cancel.clear()
        self.cancel_btn.set_visible(True)
        self.append("\nyou\n", "you")
        self.append_md(text)
        self.append("\n")
        self.append("\nai\n", "ai")
        self.store.append({"role": "user", "content": text})
        self._send({"type": "message", "text": text})

    def reload_settings(self):
        """Re-read config.toml and the API key, rebuild the agent, and refresh the UI."""
        new_cfg = config.load()
        new_key = config.load_api_key(new_cfg)
        self.cfg = new_cfg
        self.key = new_key
        self.append(f"\n[config reloaded] model: {new_cfg.model} @ {new_cfg.base_url}\n", "tool")
        for w in new_cfg.warnings:
            self.append(f"config: {w}\n", "err")
        if not new_key and config.requires_api_key(new_cfg):
            self.append("No API key. Set NVIDIA_API_KEY or put it in ~/.config/desk-ai/api_key\n", "err")
        self.entry.grab_focus()

    def restart(self):
        """Re-exec the process so every Python module is reloaded from disk.

        Unlike reload_settings(), this picks up code changes in llm.py, tools/,
        prompt.py, etc. Chat history is flushed to disk before the process is
        replaced, so the current session persists across the restart.
        """
        self.append("\n[restarting desk-ai...]\n", "tool")
        self.store.save()
        from .app import restart
        restart()

    def finish(self):
        self.busy = False
        self.cancel_btn.set_visible(False)
        self.entry.grab_focus()

    def on_cancel(self, _btn):
        self._cancel.set()
        self._send({"type": "cancel"})

    # -- approval flow (called from the main thread via GLib.idle_add)

    def show_confirm(self, cid, text):
        self.allow_btn.set_label("Allow")
        self.deny_btn.set_label("Deny")
        self.confirm_label.set_text(text)
        self._pending = (cid,)
        self.confirm_box.set_visible(True)
        self.present()  # bring the window forward so the prompt is seen

    def _answer(self, ok):
        if self._pending is None:
            return
        cid = self._pending[0]
        self._pending = None
        self._send({"type": "confirm_response", "id": cid, "ok": ok})
        self.confirm_box.set_visible(False)

    def request_toggle(self):
        """The daemon asked us to toggle visibility (hide the window)."""
        self.set_visible(False)
