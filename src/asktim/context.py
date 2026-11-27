"""What goes with a question: the context AskTim! gathers, and a list of it for the user.

Every piece passes through privacy.clean() (secrets, personal details, the user's name) and
anything that came out of a program passes through privacy.filter_output() (data lines) -
unless the user has turned the data filter off (AskTim! privacy off), when data go too.
`AskTim! show "question"` prints all of it without sending anything; -v lists it with each answer.
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
    def __init__(self, share_data=False):
        self.share_data = share_data  # the user has turned the data filter off
        self.parts = []  # text for the AI
        self.sent = []  # what the user is told was sent
        self.redactions = 0
        self.withheld = 0
        self.withheld_described = 0  # of those, the lines a part of the summary already counts (the piped text)
        self.history_gap = None  # history.unseen_terminal(): the commands typed in this terminal cannot be seen

    def add(self, text, what):
        if text:
            self.parts.append(text)
            if what:
                self.sent.append(what)

    def text(self):
        if not self.parts:
            return ""
        if self.share_data:
            head = "[Context that AskTim! gathered on the user's computer. The user has turned AskTim!'s data filter off, so it can hold their data.]\n"
        else:
            head = "[Context that AskTim! gathered on the user's computer. It holds no file contents and no data.]\n"
        return head + "\n\n".join(self.parts) + "\n\n"

    def summary(self):
        s = ", ".join(self.sent) if self.sent else "only your question"
        extra = []
        if self.redactions:
            extra.append("%d removed: secrets, personal details or sequences" % self.redactions)
        rest = self.withheld - self.withheld_described
        if rest > 0:
            extra.append("%d piece%s of data withheld" % (rest, "" if rest == 1 else "s"))
        tail = "The data filter is off (AskTim! privacy on turns it on)." if self.share_data else "No file contents."
        return "Sent with your question: %s%s. %s" % (s, (" (" + "; ".join(extra) + ")") if extra else "", tail)


def _clean(g, text, strict):
    text, n = privacy.clean(text, strict=strict, share_data=g.share_data)
    g.redactions += n
    return text


def terminal(cfg, files=(), stdin_text=None, send_history=None, history_n=None, send_files=None, cwd=None,
             window_runs=None, output_of=None):
    """Context for a question asked at the command line (or in AskTim!'s window).
    window_runs: the commands run from AskTim!'s window ({"command", "status"}, oldest first);
    output_of: (command, exit status) when stdin_text is what that command printed in the window."""
    strict = cfg.strict
    g = Gathered(share_data=cfg.share_data)
    cwd = cwd or os.getcwd()
    g.add("The user's current folder: %s" % _clean(g, paths.pretty(cwd), strict), None)
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
        gap = g.history_gap = history.unseen_terminal()
        if cmds:
            lines = []
            for c in cmds:
                line = _clean(g, c.line, strict)
                st = "" if c.status is None else ("   [exit %d]" % c.status if c.status else "")
                lines.append("  $ %s%s" % (line.replace("\n", "\n    "), st))
            known = any(c.status is not None for c in cmds)
            one = len(cmds) == 1
            how = ([] if one else ["oldest first"]) + ["[exit N] marks a command that failed" if known else
                                                       "its exit status is not known" if one else "exit statuses are not known"]
            # (with a gap, these are other terminals' commands - from the hook's log - or those of terminals that have closed)
            other = source == history.HOOK_SOURCE
            elsewhere = "" if not gap else " from other terminals" if other else " from earlier terminal sessions"
            g.add("The user's last %s%s (%s):\n%s" % ("shell command" if one else "%d shell commands" % len(cmds), elsewhere, "; ".join(how), "\n".join(lines)),
                  ("your last command" if one else "your last %d commands" % len(cmds))
                  + ("" if not gap else " (from other terminals)" if other else " (from earlier sessions)"))
            last = history.last_failed(cmds[-3:])
            if last is not None and not gap:
                meaning = EXIT_MEANING.get(last.status, "")
                g.add("The most recent failure: `%s` ended with exit status %d%s." % (_clean(g, last.line, strict), last.status, (" (" + meaning + ")") if meaning else ""), None)
        elif source is None and not gap:
            g.add("(AskTim! could not find the user's shell history.)", None)
        if gap:
            why = ("AskTim!'s shell hook is installed but not loaded in it" if gap["installed"] else
                   ("fish has not saved them yet" if gap["shell"] == "fish" else "%s keeps them in memory until the terminal closes" % gap["shell"])
                   + ", and AskTim!'s shell hook is not set up")
            g.add("(AskTim! cannot see the commands typed in the terminal it was started from: %s. If the question is about a command, ask "
                  "the user to paste the command and its error message - not their data - or to run `%ssource %s`.)"
                  % (why, "" if gap["installed"] else "AskTim! setup-shell` and then `", gap["rc"]), None)
    if window_runs and n > 0 and (send_history if send_history is not None else cfg.get("send_history", True)):
        runs = list(window_runs)[-min(n, 20):]
        lines = ["  $ %s%s" % (_clean(g, r["command"], strict).replace("\n", "\n    "), "   [exit %d]" % r["status"] if r.get("status") else "")
                 for r in runs]
        g.add("Commands the user ran from AskTim!'s window (oldest first; [exit N] marks a command that failed):\n" + "\n".join(lines),
              "the commands you ran from this window")
    for f in files or ():
        g.add(*about_file(g, f, strict))
    if stdin_text:
        kind = "default"
        places = [cwd]
        if output_of:
            from . import safety

            kind = safety.output_kind(output_of[0])
            places += safety.named_dirs(output_of[0], cwd)
        fr = privacy.filter_output(stdin_text, kind=kind, strict=strict, places=places, share_data=g.share_data)
        g.withheld += fr.withheld
        g.withheld_described += fr.withheld
        body = privacy.shorten(fr.text, 6000)
        if output_of:
            label = "What `%s` printed when the user ran it from AskTim!'s window (exit status %s%s" % (
                _clean(g, output_of[0], strict), output_of[1], "):" if g.share_data else "; lines that look like data are withheld):")
            said = "what that command printed"
        else:
            label = "Output the user piped into AskTim!" + (":" if g.share_data else " (lines that look like data are withheld):")
            said = "the text you piped in"
        g.add("%s\n```text\n%s\n```" % (label, body), said if g.share_data else "%s (%s)" % (said, fr.summary()))
    return g


def about_file(g, path, strict):
    """(text for the AI, what the user is told was sent) for a file asked about with --file: its
    description, and - with the data filter off - the start of the file itself."""
    desc = _clean(g, fileinfo.describe(path, strict=strict), strict)
    name = os.path.basename(path)
    if g.share_data:
        start = fileinfo.start_of(path)
        if start:
            return ("A file the user asked about: %s\nThe start of the file:\n```text\n%s\n```" % (desc, _clean(g, start, strict)),
                    "a description of %s and the start of it" % name)
    return "A file the user asked about: " + desc, "a description of %s (its structure, not its contents)" % name


def describe_value(name, v, strict=False, share_data=False):
    """A variable of a notebook as a line: name, type, shape, columns - never its values, unless the
    user has turned the data filter off (share_data), when a short preview follows."""
    line = _describe_value(name, v, strict)
    if share_data and line:
        p = _preview(v)
        if p:
            line += "; it starts:\n" + p
    return line


def _preview(v, limit=1500):
    """The first values of a variable, for the AI when the data filter is off."""
    t = type(v)
    mod = getattr(t, "__module__", "") or ""
    try:
        if mod.startswith(("pandas", "polars")) and hasattr(v, "head"):
            text = str(v.head(5))
        elif mod.startswith("numpy") and t.__name__ == "ndarray":
            import numpy

            text = numpy.array2string(v.ravel()[:20] if v.ndim > 1 else v[:20], threshold=20)
        elif isinstance(v, (str, bytes, bytearray)):
            text = repr(v[:300])
        elif isinstance(v, (list, tuple, set, frozenset, dict)):
            text = repr(v)
        elif isinstance(v, (bool, int, float, complex)):
            text = repr(v)
        else:
            return None
    except Exception:
        return None
    return text if len(text) <= limit else text[:limit] + " …"


def _describe_value(name, v, strict=False):
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
