"""MarkdownWriter against a fake buffer that mimics GtkTextBuffer (marks, gravity, tag ranges)."""
import unittest

from ordis.mdview import MarkdownWriter


class FakeIter:
    def __init__(self, offset):
        self.offset = offset

    def get_offset(self):
        return self.offset


class FakeBuffer:
    """Just enough of GtkTextBuffer: text, marks with gravity, and tag ranges."""

    def __init__(self):
        self.text = ""
        self.marks = {}   # mark -> [offset, left_gravity]
        self.tags = []    # [start, end, name]

    def get_end_iter(self):
        return FakeIter(len(self.text))

    def get_iter_at_offset(self, n):
        return FakeIter(n)

    def create_mark(self, _name, it, left_gravity):
        mark = object()
        self.marks[mark] = [it.offset, left_gravity]
        return mark

    def get_iter_at_mark(self, mark):
        return FakeIter(self.marks[mark][0])

    def delete_mark(self, mark):
        del self.marks[mark]

    def insert(self, it, text):
        pos, n = it.offset, len(text)
        self.text = self.text[:pos] + text + self.text[pos:]
        for m in self.marks.values():
            if m[0] > pos or (m[0] == pos and not m[1]):  # right-gravity marks move past the text
                m[0] += n
        for t in self.tags:
            if t[0] >= pos:
                t[0] += n
            if t[1] > pos:
                t[1] += n

    def delete(self, a, b):
        lo, hi = a.offset, b.offset
        self.text = self.text[:lo] + self.text[hi:]
        for m in self.marks.values():
            m[0] = lo if m[0] >= lo and m[0] <= hi else (m[0] - (hi - lo) if m[0] > hi else m[0])
        kept = []
        for s, e, name in self.tags:
            s2 = s if s <= lo else max(lo, s - (hi - lo))
            e2 = e if e <= lo else max(lo, e - (hi - lo))
            if e2 > s2:
                kept.append([s2, e2, name])
        self.tags = kept

    def apply_tag_by_name(self, name, a, b):
        self.tags.append([a.offset, b.offset, name])

    # test helpers
    def tags_over(self, piece):
        i = self.text.index(piece)
        return sorted({n for s, e, n in self.tags if s <= i and e >= i + len(piece)})


class Scheduler:
    def __init__(self):
        self.pending, self.scheduled, self.cancelled, self._n = {}, 0, 0, 0

    def schedule(self, _delay, cb):
        self._n += 1
        self.scheduled += 1
        self.pending[self._n] = cb
        return self._n

    def cancel(self, handle):
        self.cancelled += 1
        self.pending.pop(handle, None)

    def fire(self):
        for handle, cb in list(self.pending.items()):
            del self.pending[handle]
            cb()


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.buf, self.sched, self.changes = FakeBuffer(), Scheduler(), []
        self.w = MarkdownWriter(self.buf, on_change=lambda: self.changes.append(1),
                                schedule=self.sched.schedule, cancel=self.sched.cancel)

    def test_markup_split_across_chunks_renders_once_complete(self):
        self.w.write("Hello **bo")
        self.sched.fire()
        self.assertEqual(self.buf.text, "Hello **bo")        # still literal: not closed yet
        self.w.write("ld** world")
        self.sched.fire()
        self.assertEqual(self.buf.text, "Hello bold world")
        self.assertEqual(self.buf.tags_over("bold"), ["bold"])
        self.assertEqual(self.buf.tags_over("Hello"), [])

    def test_text_before_the_segment_is_never_touched(self):
        self.buf.insert(self.buf.get_end_iter(), "you\nprevious message\n")
        self.w.write("a **b**")
        self.sched.fire()
        self.w.write(" and more")
        self.sched.fire()
        self.assertEqual(self.buf.text, "you\nprevious message\na b and more")

    def test_many_rerenders_never_duplicate_text_or_tags(self):
        self.w.write("x *y*")
        self.sched.fire()
        first_tags = [list(t) for t in self.buf.tags]
        for _ in range(5):
            self.w._render()
        self.assertEqual(self.buf.text, "x y")
        self.assertEqual([list(t) for t in self.buf.tags], first_tags)

    def test_close_renders_immediately_and_cancels_the_pending_timer(self):
        self.w.write("done **now**")          # timer scheduled but never fired
        self.w.close()
        self.assertEqual(self.buf.text, "done now")
        self.assertEqual(self.sched.cancelled, 1)
        self.assertFalse(self.w.is_open)
        self.assertEqual(self.buf.marks, {})  # mark released

    def test_text_written_between_segments_survives_and_new_segment_starts_after_it(self):
        self.w.write("first *a*")
        self.w.close()                                   # what append() does before a tool line
        self.buf.insert(self.buf.get_end_iter(), "\n⚙ tool\n")
        self.w.write("second *b*")
        self.sched.fire()
        self.w.close()
        self.assertEqual(self.buf.text, "first a\n⚙ tool\nsecond b")
        self.assertEqual(self.buf.tags_over("a"), ["italic"])
        self.assertEqual(self.buf.tags_over("b"), ["italic"])

    def test_writes_are_debounced_into_one_timer(self):
        for ch in "streaming text":
            self.w.write(ch)
        self.assertEqual(self.sched.scheduled, 1)
        self.sched.fire()
        self.assertEqual(self.buf.text, "streaming text")
        self.w.write("!")
        self.assertEqual(self.sched.scheduled, 2)         # a new timer after the first one fired

    def test_on_change_runs_after_each_render(self):
        self.w.write("a")
        self.sched.fire()
        self.w.close()
        self.assertEqual(len(self.changes), 2)

    def test_empty_write_and_idle_close_do_nothing(self):
        self.w.write("")
        self.w.close()
        self.assertFalse(self.w.is_open)
        self.assertEqual(self.sched.scheduled, 0)

    def test_code_block_streams_in_and_its_content_stays_literal(self):
        for chunk in ("Here:\n```py", "thon\nx = **1**\n", "y = 2\n``", "`\ndone"):
            self.w.write(chunk)
            self.sched.fire()
        self.w.close()
        self.assertEqual(self.buf.text, "Here:\nx = **1**\ny = 2\ndone")
        self.assertEqual(self.buf.tags_over("x = **1**"), ["codeblock"])
        self.assertEqual(self.buf.tags_over("done"), [])

    def test_a_late_timer_after_close_is_harmless(self):
        self.w.write("x")
        handle_cb = next(iter(self.sched.pending.values()))
        self.w.close()
        handle_cb()  # e.g. a timer that was already dispatched when close() ran
        self.assertEqual(self.buf.text, "x")


if __name__ == "__main__":
    unittest.main()
