import time
import unittest

from desk_ai.markdown import MAX_INLINE_LINE, parse

DOC = """# Title
Some **bold**, *italic*, ~~gone~~ and `code` with a [link](https://x.org).

- one
  - nested **b**
1. first
   continuation

> quoted *text*

---
| a | b |
|---|---|

```python
x = **not bold**

y = 2
```
done"""


def plain(md):
    return "".join(t for t, _ in parse(md))


def tags_of(md, piece):
    """Tags of the span that is exactly `piece`."""
    for text, tags in parse(md):
        if text == piece:
            return tags
    raise AssertionError(f"no span {piece!r} in {parse(md)}")


class InlineTests(unittest.TestCase):
    def test_plain_text_and_single_newlines_are_unchanged(self):
        self.assertEqual(plain("hello world\nsecond line"), "hello world\nsecond line")

    def test_bold_italic_strike_code(self):
        md = "a **b** c *i* d ~~s~~ e `c`"
        self.assertEqual(plain(md), "a b c i d s e c")
        self.assertEqual(tags_of(md, "b"), ("bold",))
        self.assertEqual(tags_of(md, "i"), ("italic",))
        self.assertEqual(tags_of(md, "s"), ("strike",))
        self.assertEqual(tags_of(md, "c"), ("code",))

    def test_underscore_variants(self):
        self.assertEqual(tags_of("__b__ and _i_", "b"), ("bold",))
        self.assertEqual(tags_of("__b__ and _i_", "i"), ("italic",))

    def test_nested_emphasis_accumulates_tags(self):
        self.assertEqual(tags_of("**bold *both* bold**", "both"), ("bold", "italic"))

    def test_code_protects_markup(self):
        self.assertEqual(plain("`**x**` and `a_b_c`"), "**x** and a_b_c")
        self.assertEqual(tags_of("`**x**`", "**x**"), ("code",))

    def test_double_backtick_code_may_contain_a_backtick(self):
        self.assertEqual(plain("``a ` b``"), "a ` b")

    def test_escapes_make_markers_literal(self):
        self.assertEqual(plain(r"\*not italic\* and \`tick"), "*not italic* and `tick")

    def test_unclosed_markers_stay_literal(self):
        for md in ("**bold", "*it", "~~x", "`code", "[text](url", "[text]"):
            self.assertEqual(plain(md), md)

    def test_things_that_look_like_markup_but_are_not(self):
        for md in ("snake_case_name", "2 * 3 * 4", "a * b", "x_y_z and a*b", "5*3 = 15 and 2*4"):
            tags = {t for _, ts in parse(md) for t in ts}
            self.assertNotIn("italic", tags, md)
        for md in ("2**3 and 4**5", "x**2 + y**2", "a**b**c"):
            tags = {t for _, ts in parse(md) for t in ts}
            self.assertNotIn("bold", tags, md)
            self.assertEqual(plain(md), md)

    def test_emphasis_still_works_next_to_punctuation(self):
        self.assertEqual(tags_of("(*it*) and **b**.", "it"), ("italic",))
        self.assertEqual(tags_of("see **this**, then *that*!", "this"), ("bold",))
        self.assertEqual(tags_of("see **this**, then *that*!", "that"), ("italic",))
        self.assertEqual(tags_of('"**quoted**"', "quoted"), ("bold",))

    def test_link_shows_url_unless_label_is_the_url(self):
        self.assertEqual(plain("[docs](https://a.b/c)"), "docs (https://a.b/c)")
        self.assertEqual(tags_of("[docs](https://a.b/c)", "docs"), ("link",))
        self.assertEqual(plain("[https://a.b](https://a.b)"), "https://a.b")

    def test_hash_without_space_is_not_a_heading(self):
        self.assertEqual(parse("#hashtag"), [("#hashtag", ())])


class BlockTests(unittest.TestCase):
    def test_heading_levels_and_cap(self):
        self.assertEqual(tags_of("# T", "T"), ("h1",))
        self.assertEqual(tags_of("## T", "T"), ("h2",))
        self.assertEqual(tags_of("#### T", "T"), ("h3",))
        self.assertEqual(plain("## T ##"), "T")

    def test_heading_with_inline_markup(self):
        self.assertEqual(tags_of("# a **b**", "b"), ("h1", "bold"))

    def test_bullets_levels_and_numbers(self):
        md = "- a\n  - b\n    - c\n* d\n+ e\n1. f\n2) g"
        self.assertEqual(plain(md), "• a\n◦ b\n▪ c\n• d\n• e\n1. f\n2) g")

    def test_list_item_tags(self):
        spans = dict(parse("- a\n  - b"))
        self.assertEqual(spans["• a\n"], ("li0",))
        self.assertEqual(spans["◦ b"], ("li1",))

    def test_bold_at_line_start_is_not_a_bullet(self):
        self.assertEqual(plain("**Note** this"), "Note this")

    def test_list_continuation_line(self):
        out = parse("- item\n  more text")
        self.assertEqual(out[-1], ("more text", ("lc0",)))

    def test_indented_text_after_a_paragraph_is_not_a_continuation(self):
        self.assertEqual(parse("para\n  indented")[-1][1], ())

    def test_blockquote(self):
        self.assertEqual(plain("> hi *x*"), "▎ hi x")
        self.assertEqual(tags_of("> hi *x*", "x"), ("quote", "italic"))

    def test_horizontal_rule(self):
        for rule in ("---", "***", "___", "- - -"):
            self.assertTrue(plain(rule).startswith("─"), rule)

    def test_table_rows_are_monospace(self):
        out = parse("| a | b |\n|---|---|")
        self.assertTrue(all("codeblock" in tags for _, tags in out))

    def test_blank_lines_collapse_and_leading_ones_are_dropped(self):
        self.assertEqual(plain("\n\n\na\n\n\n\nb"), "a\n\nb")

    def test_crlf(self):
        self.assertEqual(plain("a\r\nb"), "a\nb")

    def test_empty(self):
        self.assertEqual(parse(""), [])
        self.assertEqual(parse(None), [])


class CodeFenceTests(unittest.TestCase):
    def test_fence_lines_dropped_and_content_literal(self):
        md = "```python\nx = **y**\n```\nafter"
        self.assertEqual(plain(md), "x = **y**\nafter")
        self.assertEqual(tags_of(md, "x = **y**\n"), ("codeblock",))

    def test_blank_line_inside_code_is_kept(self):
        md = "```\na\n\nb\n```"
        self.assertEqual(plain(md), "a\n\nb")

    def test_unclosed_fence_runs_to_the_end(self):  # what streaming looks like mid-block
        md = "text\n```python\nprint(1)\nprint(2)"
        self.assertEqual(plain(md), "text\nprint(1)\nprint(2)")
        self.assertEqual(parse(md)[-1][1], ("codeblock",))

    def test_tilde_fence_and_longer_fence_containing_shorter(self):
        self.assertEqual(plain("~~~\na\n~~~"), "a")
        md = "````\n```\ninner\n```\n````\nout"
        self.assertEqual(plain(md), "```\ninner\n```\nout")

    def test_mismatched_closer_does_not_close(self):
        self.assertEqual(plain("```\na\n~~~\nb"), "a\n~~~\nb")


class StreamingTests(unittest.TestCase):
    def test_every_prefix_of_a_rich_document_parses_without_error(self):
        for n in range(len(DOC) + 1):
            out = parse(DOC[:n])
            self.assertTrue(all(isinstance(t, str) and isinstance(tags, tuple) for t, tags in out))
            self.assertTrue(all(t for t, _ in out))  # no empty spans

    def test_every_prefix_of_plain_text_renders_identically(self):
        text = "just words\nover two lines, with punctuation: yes? (ok)"
        for n in range(len(text) + 1):
            self.assertEqual(plain(text[:n]), text[:n])

    def test_rendered_text_never_gets_longer_than_the_source_plus_decoration(self):
        # markers are removed; only bullets, rules and url suffixes add characters
        for n in range(len(DOC) + 1):
            self.assertLessEqual(len(plain(DOC[:n])), len(DOC[:n]) + 80)


class PerformanceTests(unittest.TestCase):
    """Streaming re-parses the whole message every ~40 ms, so bad input must stay fast."""

    def timed(self, md, limit=1.0):
        t0 = time.perf_counter()
        parse(md)
        self.assertLess(time.perf_counter() - t0, limit, repr(md[:30]))

    def test_pathological_inline_input(self):
        n = MAX_INLINE_LINE - 1
        for unit in ("*", "**a ", "[", "`", "_a", "~~", "\\", "[a](", "*a "):
            self.timed(unit * (n // len(unit)))
            self.timed((unit * (n // len(unit)) + "\n") * 40, limit=3.0)

    def test_overlong_lines_skip_inline_parsing_and_are_fast(self):
        long_line = "**x** " * 20000
        self.assertEqual(plain(long_line), long_line)
        self.timed(long_line)

    def test_large_realistic_document(self):
        self.timed(DOC * 300)


if __name__ == "__main__":
    unittest.main()
