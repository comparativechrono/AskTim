"""Printing to the terminal: answers as they stream in (light Markdown), notes, questions.

Code blocks are printed without any border, so that a command can be copied and pasted
straight from the answer. Colour is used when the output is a terminal (and NO_COLOR is not
set); piped output stays plain Markdown.
"""

import os
import re
import shutil
import sys

STYLES = {
    "bold": "1", "dim": "2", "italic": "3", "underline": "4", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "gray": "90", "bright": "97",
}


def _supports_color(stream, setting="auto"):
    if setting == "always":
        return True
    if setting == "never" or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        try:
            os.system("")  # turns on ANSI escape codes in the Windows console
        except Exception:
            return False
    return os.environ.get("TERM") != "dumb"


def _unicode_ok(stream):
    enc = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in enc


class Term(object):
    def __init__(self, color="auto", stream=None, err=None):
        self.out = stream or sys.stdout
        self.err = err or sys.stderr
        self.color = _supports_color(self.out, color)
        self.uni = _unicode_ok(self.out)

    # -- basics -------------------------------------------------------------------------------

    def s(self, text, *styles):
        if not self.color or not styles:
            return text
        codes = ";".join(STYLES[x] for x in styles if x in STYLES)
        return "\x1b[%sm%s\x1b[0m" % (codes, text)

    def write(self, text):
        try:
            self.out.write(text)
        except UnicodeEncodeError:
            self.out.write(text.encode("ascii", "replace").decode("ascii"))
        self.out.flush()

    def line(self, text=""):
        self.write(text + "\n")

    def width(self):
        try:
            return max(40, min(100, shutil.get_terminal_size((80, 24)).columns))
        except Exception:
            return 80

    def rule(self, title="", style="gray"):
        ch = "─" if self.uni else "-"
        w = self.width()
        if title:
            t = " %s " % title
            self.line(self.s(ch * 2 + t + ch * max(2, w - len(t) - 2), style))
        else:
            self.line(self.s(ch * w, style))

    def note(self, text):
        self.line(self.s(text, "gray"))

    def info(self, text):
        self.line(self.s(text, "cyan"))

    def good(self, text):
        self.line(self.s(text, "green"))

    def warn(self, text):
        self.line(self.s(text, "yellow"))

    def error(self, text):
        self.line(self.s(text, "red"))

    def bullet(self):
        return "•" if self.uni else "-"

    # -- asking ---------------------------------------------------------------------------------

    def interactive(self):
        try:
            return sys.stdin.isatty()
        except Exception:
            return False

    def ask(self, prompt, default=""):
        try:
            ans = input(self.s(prompt, "bold"))
        except EOFError:
            return None
        return ans.strip() if ans.strip() else default

    def edit(self, prompt, text):
        """Let the user change a line of text (pre-filled where readline allows)."""
        if "\n" not in text:
            try:
                import readline  # noqa: F401  (gives input() line editing)

                def hook():
                    readline.insert_text(text)
                    readline.redisplay()

                readline.set_startup_hook(hook)
                try:
                    return input(prompt)
                finally:
                    readline.set_startup_hook()
            except (ImportError, AttributeError):
                pass
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor and shutil.which(editor.split()[0]):
            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
                f.write(text)
                path = f.name
            try:
                subprocess.call(editor.split() + [path])
                with open(path) as f:
                    return f.read().rstrip("\n")
            finally:
                os.unlink(path)
        self.line(self.s("Type the new version (one line); press Enter alone to keep it as it is:", "gray"))
        ans = input(prompt)
        return ans if ans.strip() else text

    # -- streaming Markdown ---------------------------------------------------------------------

    def markdown(self):
        return MarkdownStream(self)


# C0/C1 control characters (but not tab and newline), and the invisible characters that can reorder
# or hide text on screen (bidirectional overrides, zero-width space, byte-order mark)
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _shown(ch):
    o = ord(ch)
    if o < 0x80:
        return "^" + chr((o + 64) % 128)
    return "\\x%02x" % o if o < 0x100 else "\\u%04x" % o


def visible(text):
    """Control characters (escape codes, carriage returns ...) written as ^X.

    Text that came from the AI goes through this before it reaches the terminal, so it cannot move the
    cursor, recolour or overwrite what is on the screen: what the user reads is what will run.
    """
    if not text:
        return text
    return _CTRL.sub(lambda m: _shown(m.group()), text)


class MarkdownStream(object):
    """Prints Markdown as it arrives, character by character, with light styling."""

    def __init__(self, term):
        self.t = term
        self.raw = []
        self.line_start = True
        self.pending = ""
        self.in_code = False
        self.code_lang = ""
        self.code_buf = []
        self.blocks = []  # (language, code) of every code block
        self.inline_code = False
        self.bold = False
        self.star = False
        self.heading = False
        self.styled = term.color
        self._segs = []

    def feed(self, text):
        if not text:
            return
        text = visible(text.replace("\r", ""))
        self.raw.append(text)
        if not self.styled:
            self.t.write(text)
            self._track_blocks_plain(text)
            return
        for ch in text:
            self._char(ch)
        self._flush()

    def _track_blocks_plain(self, text):
        # keep the code blocks also when printing plain text
        for ch in text:
            if self.line_start:
                self.pending += ch
                if ch == "\n" or len(self.pending) > 200:
                    ln = self.pending
                    self.pending = ""
                    self._plain_line(ln)
                    self.line_start = True
                continue

    def _plain_line(self, ln):
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            if self.in_code:
                self.blocks.append((self.code_lang, "".join(self.code_buf).rstrip("\n")))
                self.in_code = False
                self.code_buf = []
            else:
                self.in_code = True
                self.code_lang = s[3:].strip().split()[0].lower() if s[3:].strip() else ""
        elif self.in_code:
            self.code_buf.append(ln)

    def _out(self, text, *styles):
        if self._segs and self._segs[-1][1] == styles:
            self._segs[-1][0].append(text)
        else:
            self._segs.append(([text], styles))

    def _flush(self):
        if self._segs:
            self.t.write("".join(self.t.s("".join(parts), *st) if st else "".join(parts) for parts, st in self._segs))
            self._segs = []

    def _char(self, ch):
        if self.line_start:
            self.pending += ch
            p = self.pending
            stripped = p.lstrip(" ")
            if ch == "\n":
                self._decide_line(final=True)
                return
            # wait until the start of the line tells what it is
            if stripped in ("", "`", "``", "~", "~~", "#", "##", "###", "####", "-", "*", "+") or (stripped[:3] in ("```", "~~~")):
                if stripped[:3] in ("```", "~~~"):
                    return  # a fence line: wait for its end
                if len(p) < 8:
                    return
            self._decide_line()
            return
        self._body_char(ch)

    def _decide_line(self, final=False):
        p = self.pending
        self.pending = ""
        stripped = p.lstrip(" ")
        if stripped[:3] in ("```", "~~~"):
            if p.endswith("\n") or final:
                self._fence(stripped.strip())
                self.line_start = True
                return
        if self.in_code:
            self.line_start = False
            self.code_buf.append("")
            for ch in p:
                self._body_char(ch)
            return
        self.line_start = False
        m = re.match(r"^(#{1,4})\s+", stripped)
        if m:
            self.heading = True
            rest = stripped[m.end():]
            for ch in rest:
                self._body_char(ch)
            return
        m = re.match(r"^([-*+])\s", stripped)
        if m:
            indent = p[: len(p) - len(stripped)]
            self._out(indent + self.t.bullet() + " ")
            for ch in stripped[2:]:
                self._body_char(ch)
            return
        for ch in p:
            self._body_char(ch)

    def _fence(self, line):
        if not self.in_code:
            self.in_code = True
            self.code_lang = line[3:].strip().split()[0].lower() if line[3:].strip() else ""
            self.code_buf = []
            label = self.code_lang or "code"
            self._out(("┄┄ %s ┄┄" if self.t.uni else "-- %s --") % label, "gray")
            self._out("\n")
        else:
            self.in_code = False
            self.blocks.append((self.code_lang, "".join(self.code_buf).rstrip("\n")))
            self.code_buf = []
            self._out("┄┄" if self.t.uni else "--", "gray")
            self._out("\n")

    def _body_char(self, ch):
        if ch == "\n":
            if self.star:
                self._emit("*")
                self.star = False
            self._out("\n")
            if self.in_code:
                if self.code_buf:
                    self.code_buf[-1] += "\n"
            self.line_start = True
            self.heading = False
            self.inline_code = False
            self.bold = False
            return
        if self.in_code:
            if self.code_buf:
                self.code_buf[-1] += ch
            else:
                self.code_buf.append(ch)
            self._out(ch, "green")
            return
        if ch == "`":
            if self.star:
                self._emit("*")
                self.star = False
            self.inline_code = not self.inline_code
            return
        if self.inline_code:
            self._out(ch, "cyan")
            return
        if ch == "*":
            if self.star:
                self.star = False
                self.bold = not self.bold
                return
            self.star = True
            return
        if self.star:
            self.star = False
            self._emit("*")
        self._emit(ch)

    def _emit(self, ch):
        styles = []
        if self.bold or self.heading:
            styles.append("bold")
        self._out(ch, *styles)

    def close(self):
        if self.pending:
            if self.styled:
                self._decide_line(final=True)
            else:
                self._plain_line(self.pending)
            self.pending = ""
        if self.in_code:
            self.blocks.append((self.code_lang, "".join(self.code_buf).rstrip("\n")))
            self.in_code = False
            if self.styled:
                self._out("\n")
        self._flush()
        text = "".join(self.raw)
        if text and not text.endswith("\n"):
            self.t.write("\n")
        return text


def code_blocks(markdown):
    """(language, code) for each fenced block of a Markdown text."""
    out = []
    for m in re.finditer(r"^[ \t]*(```|~~~)[ \t]*([\w+#.\-]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", markdown or "", re.S | re.M):
        out.append((m.group(2).lower(), m.group(3).rstrip("\n")))
    return out


# -----------------------------------------------------------------------------------------------
# Markdown from the AI, made safe for a notebook
# -----------------------------------------------------------------------------------------------

_SAFE_SCHEMES = ("http", "https", "mailto")
_FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
# MathJax commands that can make links, load extensions or change styles
_MATHJAX = re.compile(r"\\(href|url|require|class|cssId|style|html|unicode|data|attr)(?![A-Za-z])")


def _escape_text(s):
    """A line of Markdown text with raw HTML made inert: & < " ' become entities, links that are not
    http(s) or mailto stop being links, and MathJax commands that could make links are broken up."""
    # quotes too: some Markdown parsers copy an image's text into its alt="..." unescaped
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;").replace("'", "&#39;")

    # every "](" is looked at (they may nest), and those whose destination is not http(s), mailto or
    # a relative path - or is on the next line, where it cannot be checked here - lose their "("
    out, i = [], 0
    while True:
        j = s.find("](", i)
        if j < 0:
            break
        d = re.sub(r"^\s*(?:&lt;)?\s*", "", s[j + 2:]).lower()
        sm = re.match(r"([a-z][a-z0-9+.\-]*):", d)
        out.append(s[i:j])
        out.append("]&#40;" if (not d or (sm and sm.group(1) not in _SAFE_SCHEMES)) else "](")
        i = j + 2
    out.append(s[i:])
    s = "".join(out)
    # link reference definitions ([id]: url), which may sit in a list or a quote and may have their
    # destination on the next line, are all turned into text (it looks the same: &#91; is "[")
    s = re.sub(r"\[([^\]]*)\]:", lambda m: "&#91;" + m.group(1) + "]:", s)
    return _MATHJAX.sub(lambda m: "\\ " + m.group(1), s)


_CODE_PUNCT = {c: "&#%d;" % ord(c) for c in "[]()!*_`~|\\:$"}


def _code_text(text):
    """The text of a code span or block, as HTML that shows it exactly and that no Markdown parser,
    nor MathJax, can turn into anything else: < & > and every character with a meaning in Markdown
    become character references (a parser may treat <code> as inline HTML and read on inside it)."""
    import html as _h

    t = _h.escape(text, quote=True)
    return "".join(_CODE_PUNCT.get(c, c) for c in t)


def _escape_line(line):
    """A line outside code blocks: inline code becomes <code> with its text escaped; the rest goes through _escape_text."""
    import html as _h

    out, i = [], 0
    for m in re.finditer(r"(?<!\\)(`+)(.+?)(?<!`)\1(?!`)", line):
        out.append(_escape_text(line[i:m.start()]))
        body = m.group(2)
        if body.startswith(" ") and body.endswith(" ") and body.strip():
            body = body[1:-1]
        out.append("<code>%s</code>" % _code_text(body))
        i = m.end()
    out.append(_escape_text(line[i:]))
    return "".join(out)


def _indent(line):
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))


def safe_markdown(md):
    """Markdown written by the AI, ready to be shown in a notebook.

    Jupyter renders the HTML inside Markdown that it trusts, and output made while a notebook runs is
    trusted, so an <img onerror=...> from the model would run as script in the notebook's page - with
    the power to run code in the kernel without the user's approval. So nothing the model writes
    reaches the page as HTML: code blocks and inline code are turned into <pre>/<code> with their text
    escaped, everything else has & and < escaped, and only http(s) and mailto links stay links. (The
    price: code in answers is not colour-highlighted in notebooks.)
    """
    import html as _h

    if not md:
        return md
    out = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _FENCE.match(line)
        if not m or (m.group(2)[0] == "`" and "`" in m.group(3)):
            out.append(_escape_line(line))
            i += 1
            continue
        lead, fence, info = m.group(1), m.group(2), m.group(3).strip()
        k = _indent(line)
        body = []
        i += 1
        while i < len(lines):
            ln = lines[i]
            if ln.strip() and _indent(ln) < k:
                break  # the list item (or quote) the block was in has ended
            if ln.lstrip().startswith(fence[0] * len(fence)):
                i += 1
                break
            body.append(ln)  # kept indented, so that a block in a list item stays in it
            i += 1
        if body:
            body[0] = body[0][len(lead):] if body[0].startswith(lead) else body[0].lstrip()
        lang = re.sub(r"[^\w+#.\-]", "", info.split()[0]) if info else ""
        cls = ' class="language-%s"' % lang if lang else ""
        out.append("%s<pre><code%s>%s</code></pre>" % (lead, cls, _code_text("\n".join(body))))
    return "\n".join(out)


def inline_markdown(text):
    """A short piece of the AI's text (a reason, a 'why') for one line of Markdown."""
    return safe_markdown(" ".join((text or "").split()))


def inline_code(text):
    """A path or a command for one line of Markdown, as <code> that shows it exactly."""
    return "<code>%s</code>" % _code_text(" ".join(str(text or "").split()))
