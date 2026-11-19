"""A small chat-oriented markdown parser. No GTK in here.

parse(text) -> [(text, tags), ...]: plain text spans, each with a tuple of style-tag names that the
UI maps to GtkTextTags. Joined, the spans are the text to display (markers like ** are removed).

Like `_`, a `*` or `**` cannot open inside a word or close right before one, so math and code such
as 2*3*4 or x**2 + y**2 stay literal. (Globs like src/*/test_*.py outside backticks can still be
mistaken for emphasis.)

Built for streaming: it is called again on the whole message every few milliseconds, so it never
raises, and half-finished markup (an unclosed ``` or **) degrades gracefully instead of breaking.

Supported: # headings, **bold**, *italic*, ~~strike~~, `code`, ``` fences, - * + and 1. lists
(nested by 2-space indent), > quotes, --- rules, [text](url) links (shown as "text (url)"),
backslash escapes. Tables are shown as monospace text. Single newlines are kept as line breaks,
which is how chat models mostly write. Not supported: HTML, images, setext headings, reference
links, nested block quotes, bare-URL detection.
"""
import re

# Lines longer than this are shown as plain text: inline matching is quadratic in line length.
MAX_INLINE_LINE = 1500

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*[^\s`]*\s*$")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
CONTINUATION = re.compile(r"^\s{2,}\S")

INLINE = re.compile(
    r"""
      (?P<code>(?P<tick>`+)(?P<code_body>.+?)(?P=tick))
    | \\(?P<esc>[\\`*_{}\[\]()#+\-.!|~>])
    | \[(?P<link_text>[^\]]+)\]\((?P<url>[^)\s]+)\)
    | (?<![\w*])\*\*(?=\S)(?P<bold_body>.+?)(?<=\S)\*\*(?![\w*])
    | (?<!\w)__(?=\S)(?P<bold2_body>.+?)(?<=\S)__(?!\w)
    | ~~(?=\S)(?P<strike_body>.+?)(?<=\S)~~
    | (?<![\w*])\*(?=[^\s*])(?P<it_body>.+?)(?<=[^\s*])\*(?![\w*])
    | (?<!\w)_(?=[^\s_])(?P<it2_body>.+?)(?<=[^\s_])_(?!\w)
    """,
    re.VERBOSE,
)

BULLETS = ("•", "◦", "▪", "▪")


def inline(text, base=()):
    """Inline markup -> spans. `base` tags are added to every span."""
    if len(text) > MAX_INLINE_LINE:
        return [(text, base)] if text else []
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], base))
        pos = m.end()
        if m.group("code") is not None:
            body = m.group("code_body")
            if len(body) > 2 and body.startswith(" ") and body.endswith(" "):
                body = body[1:-1]
            out.append((body, base + ("code",)))
        elif m.group("esc") is not None:
            out.append((m.group("esc"), base))
        elif m.group("link_text") is not None:
            label, url = m.group("link_text"), m.group("url")
            out.extend(inline(label, base + ("link",)))
            if label != url:
                out.append((f" ({url})", base + ("dim",)))
        elif m.group("bold_body") is not None or m.group("bold2_body") is not None:
            out.extend(inline(m.group("bold_body") or m.group("bold2_body"), base + ("bold",)))
        elif m.group("strike_body") is not None:
            out.extend(inline(m.group("strike_body"), base + ("strike",)))
        else:
            out.extend(inline(m.group("it_body") or m.group("it2_body"), base + ("italic",)))
    if pos < len(text):
        out.append((text[pos:], base))
    return out


def _blocks(md):
    """Yields rows (spans, tags_for_the_newline_after_the_row)."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rows = []
    item_level = None  # nesting level of the list item we are inside, for continuation lines
    i = 0
    while i < len(lines):
        line = lines[i]

        m = FENCE.match(line)
        if m:
            fence = m.group(1)
            closer = re.compile(rf"^\s{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$")
            i += 1
            while i < len(lines):  # an unclosed fence (still streaming) runs to the end
                if closer.match(lines[i]):
                    i += 1
                    break
                rows.append(([(lines[i], ("codeblock",))], ("codeblock",)))
                i += 1
            item_level = None
            continue

        i += 1
        if not line.strip():
            item_level = None
            previous_is_blank = not rows or (not rows[-1][0] and not rows[-1][1])
            if not previous_is_blank:  # at most one blank row in a row, and none at the start
                rows.append(([], ()))
            continue

        m = HEADING.match(line)
        if m:
            tag = (f"h{min(len(m.group(1)), 3)}",)
            rows.append((inline(m.group(2), tag), tag))
            item_level = None
            continue

        if HR.match(line):
            rows.append(([("─" * 28, ("dim",))], ()))
            item_level = None
            continue

        m = QUOTE.match(line)
        if m:
            rows.append(([("▎ ", ("quote", "dim"))] + inline(m.group(1), ("quote",)), ("quote",)))
            item_level = None
            continue

        m = ITEM.match(line)
        if m:
            indent, marker, body = m.groups()
            level = min(len(indent.replace("\t", "    ")) // 2, 3)
            bullet = marker if marker[0].isdigit() else BULLETS[level]
            tag = (f"li{level}",)
            rows.append(([(bullet + " ", tag)] + inline(body, tag), tag))
            item_level = level
            continue

        if item_level is not None and CONTINUATION.match(line):
            tag = (f"lc{item_level}",)
            rows.append((inline(line.strip(), tag), tag))
            continue

        if line.lstrip().startswith("|"):  # table row: keep the columns aligned
            rows.append(([(line, ("codeblock",))], ("codeblock",)))
            item_level = None
            continue

        rows.append((inline(line), ()))
        item_level = None
    return rows


def parse(md):
    """Markdown -> [(text, tags)]. Adjacent spans with the same tags are merged."""
    spans = []
    rows = _blocks(md or "")
    for n, (row, newline_tags) in enumerate(rows):
        spans.extend(row)
        if n < len(rows) - 1:
            spans.append(("\n", newline_tags))
    merged = []
    for text, tags in spans:
        if not text:
            continue
        if merged and merged[-1][1] == tags:
            merged[-1] = (merged[-1][0] + text, tags)
        else:
            merged.append((text, tags))
    return merged
