"""Streams markdown into a GtkTextBuffer. No GTK imports: it only needs the buffer's methods.

A model's reply arrives in small chunks and markup can be split across chunks ("**bo" + "ld**"),
so we keep the raw text of the current segment and, every few milliseconds, replace everything
from a mark to the end of the buffer with a fresh rendering of the whole segment.

A segment ends when anything else is written to the buffer (a tool line, an error, "\\n"), because
that text sits after the mark and must not be replaced. See `close()`.
"""
from .markdown import parse


class MarkdownWriter:
    def __init__(self, buf, on_change=lambda: None, schedule=None, cancel=None, delay_ms=40):
        self.buf = buf
        self.on_change = on_change  # e.g. scroll to the bottom
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
        """Add text to the current segment (starting one at the end of the buffer if needed)."""
        if not text:
            return
        if self._mark is None:
            # left gravity: the mark stays put when we re-insert text right after it
            self._mark = self.buf.create_mark(None, self.buf.get_end_iter(), True)
            self._raw = ""
        self._raw += text
        if self._timer is None:
            self._timer = self._schedule(self.delay_ms, self._tick)

    def close(self):
        """Render the segment one last time and stop tracking it. Call before writing anything else."""
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
        return False  # one-shot timer

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
