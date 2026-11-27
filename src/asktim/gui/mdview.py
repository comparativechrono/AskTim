"""The AI's Markdown, shown in a Tk Text widget: headings, paragraphs, lists, quotes, tables,
inline code, bold and italic, links (http, https and mailto only), and code blocks - under which
the window puts its buttons. Nothing in the text can run: a Text widget shows text."""

import re

FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([\w+#.\-]*)[^\n]*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
ITEM = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")

# `code`, **bold**, __bold__, *italic*, _italic_, [text](link)
INLINE = re.compile(
    r"(?P<code>`+)(?P<c>.+?)(?P=code)"
    r"|\*\*(?P<b>(?:(?!\*\*).)+?)\*\*"
    r"|(?<![\w])__(?P<b2>(?:(?!__).)+?)__(?![\w])"
    r"|(?<![\w*])\*(?![\s*])(?P<i>[^*\n]+?)(?<!\s)\*(?![\w*])"
    r"|(?<![\w])_(?![\s_])(?P<i2>[^_\n]+?)(?<!\s)_(?![\w])"
    r"|\[(?P<lt>[^\]\n]+)\]\((?P<lu>[^)\s]+)\)"
)
SAFE_LINK = re.compile(r"^(https?://|mailto:)", re.I)


def parse(md):
    """The blocks of a Markdown text, as tuples:
    ("h", level, text) ("p", text) ("code", lang, code, closed) ("li", depth, marker, text)
    ("quote", text) ("hr",) ("table", rows)"""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    blocks, para = [], []

    def flush():
        if para:
            blocks.append(("p", " ".join(x.strip() for x in para)))
            del para[:]

    i = 0
    while i < len(lines):
        ln = lines[i]
        m = FENCE.match(ln)
        if m:
            flush()
            fence, lang = m.group(2), m.group(3).lower()
            body, i, closed = [], i + 1, False
            while i < len(lines):
                end = re.match(r"^[ \t]*(%s{%d,})[ \t]*$" % (re.escape(fence[0]), len(fence)), lines[i])
                if end:
                    closed = True
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            blocks.append(("code", lang, "\n".join(body), closed))
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        m = HEADING.match(ln)
        if m:
            flush()
            blocks.append(("h", len(m.group(1)), m.group(2)))
            i += 1
            continue
        if RULE.match(ln) and not para:
            blocks.append(("hr",))
            i += 1
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            flush()
            rows = [_cells(ln)]
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            blocks.append(("table", rows))
            continue
        m = ITEM.match(ln)
        if m:
            flush()
            depth = len(m.group(1).replace("\t", "    ")) // 2
            text = m.group(3)
            i += 1
            # lines that continue the item (indented, not a new item or block)
            while i < len(lines) and lines[i].strip() and lines[i].startswith((" ", "\t")) and not ITEM.match(lines[i]) and not FENCE.match(lines[i]):
                text += " " + lines[i].strip()
                i += 1
            blocks.append(("li", min(depth, 4), m.group(2), text))
            continue
        m = QUOTE.match(ln)
        if m:
            flush()
            quoted = [m.group(1)]
            i += 1
            while i < len(lines) and QUOTE.match(lines[i]):
                quoted.append(QUOTE.match(lines[i]).group(1))
                i += 1
            blocks.append(("quote", " ".join(q.strip() for q in quoted)))
            continue
        para.append(ln)
        i += 1
    flush()
    return blocks


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def inline(text):
    """[(text, tags, link or None)] for a line of Markdown text."""
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], (), None))
        if m.group("c") is not None:
            out.append((m.group("c"), ("icode",), None))
        elif m.group("b") is not None or m.group("b2") is not None:
            for t, tags, link in inline(m.group("b") or m.group("b2")):
                out.append((t, tags + ("bold",), link))
        elif m.group("i") is not None or m.group("i2") is not None:
            out.append((m.group("i") or m.group("i2"), ("italic",), None))
        else:
            url = m.group("lu")
            if SAFE_LINK.match(url):
                out.append((m.group("lt"), ("link",), url))
            else:
                out.append((m.group("lt"), (), None))  # (other kinds of link are shown as text)
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], (), None))
    return out


class Renderer(object):
    """Puts blocks into a Text widget at a mark (whose gravity is right, so that each insert goes after the last)."""

    def __init__(self, text, mark, base=(), on_code=None, on_link=None):
        self.t = text
        self.mark = mark
        self.base = tuple(base)
        self.on_code = on_code  # on_code(lang, code, closed) -> a widget to put under the block, or None
        self.on_link = on_link
        self.links = 0

    def put(self, s, tags=()):
        if s:
            self.t.insert(self.mark, s, self.base + tuple(tags))

    def put_inline(self, text, tags=()):
        for s, extra, link in inline(text):
            if link and self.on_link is not None:
                self.links += 1
                name = "link-%d-%d" % (id(self), self.links)
                self.t.tag_bind(name, "<Button-1>", lambda e, u=link: self.on_link(u))
                self.t.tag_bind(name, "<Enter>", lambda e: self.t.config(cursor="hand2"))
                self.t.tag_bind(name, "<Leave>", lambda e: self.t.config(cursor=""))
                self.put(s, tuple(tags) + extra + (name,))
            else:
                self.put(s, tuple(tags) + extra)

    def render(self, blocks):
        first = True
        for b in blocks:
            kind = b[0]
            if not first and kind not in ("li",):
                self.put("\n", ("gap",))
            first = False
            if kind == "h":
                self.put_inline(b[2], ("h%d" % min(b[1], 3),))
                self.put("\n")
            elif kind == "p":
                self.put_inline(b[1])
                self.put("\n")
            elif kind == "li":
                depth, marker, text = b[1], b[2], b[3]
                bullet = "•" if marker in "-*+" else marker
                self.put("%s " % bullet, ("li%d" % depth,))
                self.put_inline(text, ("li%d" % depth,))
                self.put("\n")
            elif kind == "quote":
                self.put_inline(b[1], ("quote",))
                self.put("\n")
            elif kind == "hr":
                self.put("─" * 30 + "\n", ("muted",))
            elif kind == "table":
                rows = b[1]
                width = max(len(r) for r in rows)
                cols = [max(len(r[c]) if c < len(r) else 0 for r in rows) for c in range(width)]
                for n, r in enumerate(rows):
                    cells = [(r[c] if c < len(r) else "").ljust(cols[c]) for c in range(width)]
                    self.put("  ".join(cells).rstrip() + "\n", ("pre", "bold") if n == 0 else ("pre",))
            elif kind == "code":
                lang, code, closed = b[1], b[2], b[3]
                self.put(code.rstrip("\n") + "\n", ("pre",))
                if self.on_code is not None:
                    w = self.on_code(lang, code, closed)
                    if w is not None:
                        self.t.window_create(self.mark, window=w, padx=14, pady=2)
                        self.put("\n")
