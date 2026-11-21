"""Streams markdown into a GtkTextBuffer."""

from __future__ import annotations

from ...markdown import parse


class MarkdownWriter:
    def __init__(self, buf, on_change=lambda: None, schedule=None, cancel=None, delay_ms=40):
        self.buf = buf
        self.on_change = on_change
        self.delay_ms = delay_ms
        if schedule is None:
            from gi.repository import GLib
            schedule, cancel = GLib.timeout_add, GLib.source_remove
        self._schedule, self._cancel = schedule, cancel
        self._mark = None
        self._raw = ""
        self._timer = None

    @property
    def is_open(self):
        return self._mark is not None

    def write(self, text):
        if not text:
            return
        if self._mark is None:
            self._mark = self.buf.create_mark(None, self.buf.get_end_iter(), True)
            self._raw = ""
        self._raw += text
        if self._timer is None:
            self._timer = self._schedule(self.delay_ms, self._tick)

    def close(self):
        if self._mark is None:
            return
        if self._timer is not None:
            self._cancel(self._timer)
            self._timer = None
        self._render()
        self.buf.delete_mark(self._mark)
        self._mark, self._raw = None, ""

    def _tick(self):
        self._timer = None
        if self._mark is not None:
            self._render()
        return False

    def _render(self):
        self.buf.delete(self.buf.get_iter_at_mark(self._mark), self.buf.get_end_iter())
        for text, tags in parse(self._raw):
            start = self.buf.get_end_iter().get_offset()
            self.buf.insert(self.buf.get_end_iter(), text)
            if tags:
                a, b = self.buf.get_iter_at_offset(start), self.buf.get_end_iter()
                for tag in tags:
                    self.buf.apply_tag_by_name(tag, a, b)
        self.on_change()
