"""What goes with a question: the context AskTim! gathers, and a list of it for the student.

Every piece passes through privacy.clean() (secrets, personal details, the user's name) and
anything that came out of a program passes through privacy.filter_output() (data lines).
The student is always told what was sent; `AskTim! show "question"` prints all of it
without sending anything.
"""

import os

from . import fileinfo, history, paths, privacy

EXIT_MEANING = {
    1: "a general error",
    2: "wrong use of the command, or a file or folder that does not exist",
    126: "the file is not executable (check its permissions)",
    127: "command not found (not installed, not on PATH, or misspelt)",
    130: "stopped with Ctrl+C",
    134: "the program aborted (often a failed internal check)",
    137: "killed (often: it ran out of memory)",
    139: "the program crashed (segmentation fault)",
    141: "a pipe closed early (usually harmless, e.g. with head)",
    143: "terminated by a signal",
}


class Gathered(object):
    def __init__(self):
        self.parts = []  # text for the AI
        self.sent = []  # what the student is told was sent
        self.redactions = 0
        self.withheld = 0

    def add(self, text, what):
        if text:
            self.parts.append(text)
            if what:
                self.sent.append(what)

    def text(self):
        if not self.parts:
            return ""
        return "[Context that AskTim! gathered on the student's computer. It holds no file contents and no data.]\n" + "\n\n".join(self.parts) + "\n\n"

    def summary(self):
        s = ", ".join(self.sent) if self.sent else "only your question"
        extra = []
        if self.redactions:
            extra.append("%d secret%s or personal detail%s removed" % (self.redactions, "" if self.redactions == 1 else "s", "" if self.redactions == 1 else "s"))
        if self.withheld:
            extra.append("%d line%s of data withheld" % (self.withheld, "" if self.withheld == 1 else "s"))
        return "Sent with your question: %s%s. No file contents." % (s, (" (" + "; ".join(extra) + ")") if extra else "")


def _clean(g, text, strict):
    text, n = privacy.clean(text, strict=strict)
    g.redactions += n
    return text


def terminal(cfg, files=(), stdin_text=None, send_history=None, history_n=None, send_files=None, cwd=None):
    """Context for a question asked at the command line."""
    strict = cfg.strict
    g = Gathered()
    cwd = cwd or os.getcwd()
    g.add("The student's current folder: %s" % _clean(g, paths.pretty(cwd), strict), None)
    if send_files if send_files is not None else cfg.get("send_file_names", True):
        listing = fileinfo.listing(cwd, strict=strict)
        g.add("Files in that folder (names and sizes only):\n" + _clean(g, listing, strict), "names of the files in this folder" if not strict else "the number of files here")
    base = cfg.get("history_lines")
    base = 20 if base is None else int(base)
    n = min(int(history_n or base), 100)
    if cfg.is_locked("history_lines"):
        n = min(n, base)
    if n > 0 and (send_history if send_history is not None else cfg.get("send_history", True)):
        cmds, source = history.recent(n)
        if cmds:
            lines = []
            for c in cmds:
                line = _clean(g, c.line, strict)
                st = "" if c.status is None else ("   [exit %d]" % c.status if c.status else "")
                lines.append("  $ %s%s" % (line.replace("\n", "\n    "), st))
            known = any(c.status is not None for c in cmds)
            g.add("The student's last %d shell commands (oldest first%s):\n%s" % (len(cmds), "; [exit N] marks a command that failed" if known else "; exit statuses are not known", "\n".join(lines)),
                  "your last %d commands" % len(cmds))
            last = history.last_failed(cmds[-3:])
            if last is not None:
                meaning = EXIT_MEANING.get(last.status, "")
                g.add("The most recent failure: `%s` ended with exit status %d%s." % (_clean(g, last.line, strict), last.status, (" (" + meaning + ")") if meaning else ""), None)
        elif source is None:
            g.add("(AskTim! could not find the student's shell history.)", None)
    for f in files or ():
        desc = fileinfo.describe(f, strict=strict)
        g.add("A file the student asked about: " + _clean(g, desc, strict), "a description of %s (its structure, not its contents)" % os.path.basename(f))
    if stdin_text:
        fr = privacy.filter_output(stdin_text, strict=strict)
        g.withheld += fr.withheld
        body = privacy.shorten(fr.text, 6000)
        g.add("Output the student piped into AskTim! (lines that look like data are withheld):\n```text\n%s\n```" % body,
              "the text you piped in (%s)" % fr.summary())
    return g


def describe_value(name, v, strict=False):
    """A variable of a notebook as a line: name, type, shape, columns - never its values."""
    t = type(v)
    mod = getattr(t, "__module__", "") or ""
    tname = t.__name__
    try:
        if mod.startswith("pandas") and tname == "DataFrame":
            cols = list(v.columns)
            out = "%s: pandas DataFrame, %d rows × %d columns" % (name, v.shape[0], v.shape[1])
            if not strict:
                dt = [str(d) for d in v.dtypes]
                shown = ", ".join("%s (%s)" % (str(c)[:40], d) for c, d in list(zip(cols, dt))[:40])
                out += "; columns: " + shown + (", …" if len(cols) > 40 else "")
                idx = v.index
                if getattr(idx, "name", None):
                    out += "; index: %s" % idx.name
            return out
        if mod.startswith("pandas") and tname == "Series":
            return "%s: pandas Series, length %d, dtype %s%s" % (name, len(v), v.dtype, ("" if strict or v.name is None else ", name %s" % str(v.name)[:40]))
        if mod.startswith("numpy") and tname == "ndarray":
            return "%s: numpy array, shape %s, dtype %s" % (name, tuple(v.shape), v.dtype)
        if tname == "AnnData":
            out = "%s: AnnData, %d observations × %d variables" % (name, v.n_obs, v.n_vars)
            if not strict:
                out += "; obs columns: %s; var columns: %s; layers: %s; obsm: %s" % (
                    ", ".join(map(str, list(v.obs.columns)[:20])) or "none", ", ".join(map(str, list(v.var.columns)[:20])) or "none",
                    ", ".join(map(str, list(v.layers.keys())[:10])) or "none", ", ".join(map(str, list(v.obsm.keys())[:10])) or "none")
            return out
        if mod.startswith("polars") and tname == "DataFrame":
            return "%s: polars DataFrame, %d rows × %d columns%s" % (name, v.height, v.width, "" if strict else "; columns: " + ", ".join(str(c)[:40] for c in v.columns[:40]))
        if mod.startswith("matplotlib"):
            return "%s: %s (a plot)" % (name, tname)
        if tname == "module":
            return None
        if callable(v) and not isinstance(v, type):
            return "%s: function" % name if tname in ("function", "builtin_function_or_method") else "%s: %s" % (name, tname)
        if isinstance(v, tuple) and 1 <= len(v) <= 4 and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            return "%s: tuple %s (a shape or a count)" % (name, v)
        if isinstance(v, (list, tuple, set, frozenset, dict)):
            return "%s: %s of %d items" % (name, tname, len(v))
        if isinstance(v, str):
            return "%s: str (%d characters)" % (name, len(v))
        if isinstance(v, (bytes, bytearray)):
            return "%s: bytes (%d)" % (name, len(v))
        if isinstance(v, (bool, int, float, complex)):
            return "%s: %s" % (name, tname)
        if hasattr(v, "shape"):
            return "%s: %s, shape %s" % (name, tname, tuple(getattr(v, "shape")))
        if hasattr(v, "__len__"):
            return "%s: %s of length %d" % (name, tname, len(v))
    except Exception:
        pass
    return "%s: %s" % (name, tname)
