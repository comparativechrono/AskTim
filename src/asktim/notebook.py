"""AskTim! in Jupyter and Google Colab notebooks.

After `import asktim` (or `%load_ext asktim`) a cell can start with AskTim!, exactly as at the
command line:

    AskTim! Claude                         store a key (asked for; or use Colab's Secrets)
    AskTim! "why does this cell fail?"     a question about the notebook
    AskTim! Agent "plot the library sizes in counts.csv"
    AskTim! insert 2                       put code block 2 of the last answer into a new cell
    AskTim! cells                          the cells the agent made (next one, or all)

`%AskTim! ...`, `%%AskTim! ...` (with text or code below the first line) and asktim.ask() /
asktim.agent() work too.

What goes with a question: the code of the cells you have run, the names, types and shapes of
your variables (never their values), the last error, and what is installed - never the
contents of your data or of your variables.

Adding cells: Jupyter (JupyterLab, Notebook 7, classic) can take one new cell from the kernel
at a time, below the cell that is running. So the agent's cells arrive one by one: each one
that you run brings the next. Markdown cells come as `%%markdown` cells, which show their text
when run. Colab and VS Code cannot take cells from the kernel: there the cells are shown in
the output, with a Copy button, and the whole run is saved as a notebook file you can open.
"""

import base64
import html
import io
import json
import os
import re
import shlex
import sys
import time
import traceback
import uuid

from . import config as _config
from . import context, fileinfo, inventory, logbook, paths, privacy, prompts, safety
from .providers import Conversation, ProviderError, ToolResult, make_provider
from .render import inline_code, inline_markdown, safe_markdown

_STATE = {
    "ip": None,
    "loaded": False,
    "last_error": None,
    "chat": Conversation(),
    "answers": [],  # code blocks of the last answer: (language, code)
    "queue": [],  # cells waiting to be added: {"kind": "markdown"|"code", "source": str}
    "queue_total": 0,
    "nested": 0,
    "welcomed": False,
}

MARK = re.compile(r"AskTim! cell (\d+) of (\d+)")


# ------------------------------------------------------------------------------------------------
# loading
# ------------------------------------------------------------------------------------------------


def _transform(lines):
    """Lets a cell start with `AskTim! ...` (a line further down may too)."""
    if not lines:
        return lines
    pat = re.compile(r"^(\s*)(?:AskTim!|asktim!)(?=\s|$)(.*)$")
    m = pat.match(lines[0].rstrip("\n"))
    if m:
        first = m.group(2).strip()
        rest = "".join(lines[1:])
        if rest.strip():
            return ["get_ipython().run_cell_magic('AskTim', %r, %r)\n" % (first, rest)]
        return ["get_ipython().run_line_magic('AskTim', %r)\n" % first]
    out = []
    for ln in lines:
        m = pat.match(ln.rstrip("\n"))
        out.append("%sget_ipython().run_line_magic('AskTim', %r)\n" % (m.group(1), m.group(2).strip()) if m else ln)
    return out


def load(ip):
    if _STATE["loaded"] and _STATE["ip"] is ip:
        return
    _STATE["ip"] = ip
    for name in ("AskTim!", "AskTim", "asktim"):
        ip.register_magic_function(_magic, magic_kind="line_cell", magic_name=name)
    if _transform not in ip.input_transformers_cleanup:
        ip.input_transformers_cleanup.append(_transform)
    ip.events.register("post_run_cell", _post_run_cell)
    _STATE["loaded"] = True
    if not _STATE["welcomed"]:
        _STATE["welcomed"] = True
        cfg = _config.Config()
        key, where = _config.find_key(cfg.provider)
        tail = "" if key else " First set up a key: <code>AskTim! Claude</code>" + (" (or put it in Colab's Secrets as <code>ANTHROPIC_API_KEY</code>)" if frontend() == "colab" else "") + "."
        _html("<div style='font-family:sans-serif;font-size:13px;color:#555'><b>AskTim!</b> is ready: start a cell with "
              "<code>AskTim! \"your question\"</code> or <code>AskTim! Agent \"a task\"</code>. Your data stay here.%s</div>" % tail)


def unload(ip):
    try:
        ip.input_transformers_cleanup.remove(_transform)
    except ValueError:
        pass
    try:
        ip.events.unregister("post_run_cell", _post_run_cell)
    except ValueError:
        pass
    _STATE["loaded"] = False


def frontend():
    if "google.colab" in sys.modules:
        return "colab"
    ip = _STATE["ip"]
    if os.environ.get("VSCODE_PID") or os.environ.get("VSCODE_CWD") or (ip is not None and "__vsc_ipynb_file__" in ip.user_ns):
        return "vscode"
    return "jupyter"


def _post_run_cell(result):
    if _STATE["nested"]:
        return
    try:
        info = getattr(result, "info", None)
        raw = getattr(info, "raw_cell", "") or ""
        err = getattr(result, "error_in_exec", None) or getattr(result, "error_before_exec", None)
        if err is not None and not _is_asktim_cell(raw):
            tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
            _STATE["last_error"] = {"cell": raw, "error": "%s: %s" % (type(err).__name__, err), "traceback": tb, "time": time.time()}
        elif err is None and not _is_asktim_cell(raw) and raw.strip():
            pass
        m = MARK.search(raw[:300])
        if m and _STATE["queue"]:
            _deliver_next()
    except Exception:
        pass


def _is_asktim_cell(raw):
    s = (raw or "").lstrip()
    return s.startswith(("AskTim!", "asktim!", "%AskTim", "%%AskTim", "%asktim", "%%asktim")) or "run_line_magic('AskTim'" in s or "run_cell_magic('AskTim'" in s


# ------------------------------------------------------------------------------------------------
# display helpers
# ------------------------------------------------------------------------------------------------


def _display(obj, **kw):
    try:
        from IPython.display import display

        return display(obj, **kw)
    except Exception:
        print(obj)


def _html(s, **kw):
    from IPython.display import HTML

    return _display(HTML(s), **kw)


def _md(s, **kw):
    from IPython.display import Markdown

    return _display(Markdown(s), **kw)


def _fence(code, lang=""):
    f = "```"
    while f in code:
        f += "`"
    return "%s%s\n%s\n%s" % (f, lang, code.rstrip("\n"), f)


def _copy_box(code, lang="python", title=""):
    cid = "asktim-%s" % uuid.uuid4().hex[:10]
    esc = html.escape(code)
    return (
        "<div style='border:1px solid #ccc;border-radius:6px;margin:6px 0;font-family:sans-serif'>"
        "<div style='display:flex;justify-content:space-between;align-items:center;background:#f3f3f3;padding:3px 8px;font-size:12px;color:#444'>"
        "<span>%s</span><button style='font-size:12px' onclick=\"navigator.clipboard.writeText(document.getElementById('%s').innerText);this.innerText='Copied'\">Copy</button></div>"
        "<pre id='%s' style='margin:0;padding:8px;white-space:pre-wrap;font-size:12.5px'>%s</pre></div>" % (html.escape(title or lang), cid, cid, esc)
    )


def _ansi(text, code):
    return "\x1b[%sm%s\x1b[0m" % (code, text)


# ------------------------------------------------------------------------------------------------
# cells
# ------------------------------------------------------------------------------------------------


def _insert_cell(source, replace=False):
    """Put source into a new cell below (Jupyter); False where the frontend cannot."""
    ip = _STATE["ip"]
    if ip is None or frontend() != "jupyter":
        return False
    try:
        ip.set_next_input(source, replace=replace)
        return True
    except Exception:
        return False


def _cell_source(cell, i, n):
    if cell["kind"] == "markdown":
        # the text is the model's: made safe, since the notebook will render it as trusted Markdown
        return "%%%%markdown\n<!-- AskTim! cell %d of %d: run this cell to show the text%s -->\n%s" % (i, n, " and to get the next cell" if i < n else "", safe_markdown(cell["source"].rstrip("\n")))
    head = "# AskTim! cell %d of %d%s" % (i, n, (" – " + cell["why"]) if cell.get("why") else "")
    if i < n:
        head += "\n# (run it to get the next cell)"
    return head + "\n" + cell["source"].rstrip("\n")


def _deliver_next():
    q = _STATE["queue"]
    if not q:
        return False
    n = _STATE["queue_total"]
    i = n - len(q) + 1
    cell = q[0]
    if _insert_cell(_cell_source(cell, i, n)):
        q.pop(0)
        return True
    return False


def _show_cells(cells, title):
    parts = ["<div style='font-family:sans-serif;font-size:13px;margin:4px 0'><b>%s</b></div>" % html.escape(title)]
    for i, c in enumerate(cells, 1):
        if c["kind"] == "markdown":
            parts.append("<div style='border-left:3px solid #bbb;padding:2px 10px;margin:6px 0;color:#333'><div style='font-size:11px;color:#888'>Markdown cell %d</div><pre style='white-space:pre-wrap;font-family:inherit'>%s</pre></div>" % (i, html.escape(c["source"])))
        else:
            parts.append(_copy_box(c["source"], "python", "Code cell %d%s" % (i, (" – " + c["why"]) if c.get("why") else "")))
    _html("".join(parts))


def deliver_cells(cells, record_path=None):
    """Hand the agent's cells to the notebook, as well as this frontend allows."""
    if not cells:
        return
    fe = frontend()
    _STATE["queue"] = list(cells)
    _STATE["queue_total"] = len(cells)
    if fe == "jupyter":
        if _deliver_next():
            print(_ansi("AskTim! put the first of %d cell%s below. Each one you run brings the next (or: AskTim! cells all, for all of them in one cell)." % (len(cells), "" if len(cells) == 1 else "s"), "36"))
            return
    where = "Colab" if fe == "colab" else "VS Code" if fe == "vscode" else "This notebook"
    _show_cells(cells, "%s cannot take new cells from code, so here they are: copy each code cell into a new cell (Colab: Ctrl+M B adds one below)." % where)
    if record_path:
        print(_ansi("The whole run is also saved as a notebook: %s" % paths.pretty(record_path), "90"))
    _STATE["queue"] = []


# ------------------------------------------------------------------------------------------------
# what goes with a question
# ------------------------------------------------------------------------------------------------


def _cells_run(max_cells=15, max_chars=6000):
    ip = _STATE["ip"]
    try:
        hist = list(ip.history_manager.input_hist_raw)
    except Exception:
        hist = list(ip.user_ns.get("In", []) or [])
    cells = [c for c in hist if c and c.strip() and not _is_asktim_cell(c)]
    out, size = [], 0
    for c in reversed(cells[-max_cells:]):
        if size + len(c) > max_chars and out:
            break
        out.append(c)
        size += len(c)
    return list(reversed(out)), len(cells)


def variables_text(strict=False, limit=40):
    ip = _STATE["ip"]
    ns = ip.user_ns
    hidden = set(getattr(ip, "user_ns_hidden", {}) or {})
    lines = []
    for name in sorted(ns):
        if name.startswith("_") or name in hidden or name in ("In", "Out", "exit", "quit", "get_ipython", "asktim"):
            continue
        d = context.describe_value(name, ns[name], strict=strict)
        if d:
            lines.append(d)
        if len(lines) >= limit:
            lines.append("… and more")
            break
    return lines


def _packages():
    try:
        import importlib.metadata as md
    except ImportError:  # Python 3.7
        return {}
    have = {}
    for d in md.distributions():
        try:
            have[d.metadata["Name"].lower()] = d.version
        except Exception:
            pass
    pk = {n: have[n.lower()] for n in inventory.PY_PACKAGES if n.lower() in have}
    return {"python": sys.version.split()[0], "packages": pk, "count": len(have)}


def notebook_context(cfg, attached=None):
    strict = cfg.strict
    g = context.Gathered()

    def clean(t):
        t, n = privacy.clean(t, strict=strict)
        g.redactions += n
        return t

    fe = {"colab": "Google Colab", "vscode": "a VS Code notebook", "jupyter": "a Jupyter notebook"}[frontend()]
    g.add("The student is working in %s; the notebook's code runs in %s." % (fe, clean(paths.pretty(os.getcwd()))), None)
    if cfg.get("send_file_names", True):
        g.add("Files in that folder (names and sizes only):\n" + clean(fileinfo.listing(os.getcwd(), strict=strict)), "names of the files in the notebook's folder")
    cells, total = _cells_run()
    if cells:
        parts = []
        for c in cells:
            c, w = privacy.filter_code(c, strict=strict)  # data typed into the code (a pasted table, a list of numbers)
            g.withheld += w
            parts.append("# --- cell ---\n" + clean(c))
        body = "\n\n".join(parts)
        g.add("The code of the last %d cells the student ran (of %d), oldest first:\n```python\n%s\n```" % (len(cells), total, body), "the code of your last %d cells" % len(cells))
    vs = variables_text(strict=strict)
    if vs:
        g.add("The student's variables (names, types and shapes only - not their values):\n" + "\n".join("  " + clean(v) for v in vs), "the names, types and shapes of your variables")
    err = _STATE["last_error"]
    if err and time.time() - err["time"] < 3600:
        fr = privacy.filter_output(err["traceback"], kind="default", strict=strict)
        g.withheld += fr.withheld
        g.add("The last error, in this cell:\n```python\n%s\n```\nIt raised:\n```text\n%s\n```" % (clean(privacy.filter_code(err["cell"], strict=strict)[0])[-3000:], privacy.shorten(fr.text, 3000)), "the last error")
    if attached:
        fr = privacy.filter_output(attached, kind="default", strict=strict, places=[os.getcwd()])
        g.withheld += fr.withheld
        g.add("Text or code the student attached to the question (lines that look like data are withheld):\n```text\n%s\n```" % privacy.shorten(clean(fr.text), 6000), "the text under your question (%s)" % fr.summary())
    return g


# ------------------------------------------------------------------------------------------------
# chat
# ------------------------------------------------------------------------------------------------


class _Streamer(object):
    """Shows the answer as Markdown while it arrives (updated a few times a second)."""

    def __init__(self):
        from IPython.display import Markdown

        self.text = ""
        self.handle = _display(Markdown("*AskTim! is thinking…*"), display_id=True)
        self.t = 0

    def feed(self, delta):
        self.text += delta or ""
        if time.time() - self.t > 0.25:
            self._update(" ▍")

    def status(self, msg):
        self._update(("\n\n" if self.text else "") + "*%s*" % html.escape(msg))

    def _update(self, ours=""):
        """The model's text (made safe: it is rendered as trusted Markdown) followed by AskTim!'s own."""
        self.t = time.time()
        try:
            from IPython.display import Markdown

            if self.handle is not None:
                self.handle.update(Markdown(safe_markdown(self.text) + ours))
        except Exception:
            pass

    def done(self, extra=""):
        self._update(extra)


def _footer(blocks):
    if not blocks:
        return ""
    names = ", ".join("%d (%s)" % (i, lang or "code") for i, (lang, _c) in enumerate(blocks, 1))
    fe = frontend()
    how = "`AskTim! insert N` puts block N into a new cell" if fe == "jupyter" else "`AskTim! insert N` shows block N with a Copy button"
    return "\n\n---\n<small>Code blocks: %s. %s.</small>" % (names, how)


def nb_ask(question, attached=None, new=False, model=None, files=()):
    from .render import code_blocks

    cfg = _config.Config()
    if new:
        _STATE["chat"] = Conversation()
    q_clean, n, pasted = privacy.clean_question(question, strict=cfg.strict)
    if pasted:
        print(_ansi("AskTim! withheld %d line%s of your question that look like data." % (pasted, "" if pasted == 1 else "s"), "33"))
    if not q_clean.strip() and attached:
        q_clean = "What does this mean, and what should I do?"
    if not q_clean.strip():
        err = _STATE["last_error"]
        q_clean = "Why did my last cell fail, and how do I fix it?" if err else ""
    if not q_clean.strip():
        _md("Ask a question: `AskTim! \"how do I read a tab-separated file with pandas?\"`")
        return None
    inv = inventory.gather()
    system = prompts.chat_system(inventory.describe(inv, notebook_packages=_packages()), cfg, notebook=True)
    g = notebook_context(cfg, attached=attached)
    g.redactions += n
    g.withheld += pasted
    for f in files or ():
        g.add("A file the student asked about: " + privacy.clean(fileinfo.describe(f, strict=cfg.strict))[0], "a description of %s" % os.path.basename(f))
    try:
        prov = make_provider(cfg, model=model)
    except ProviderError as e:
        from . import offline

        tips = offline.explain_text((_STATE["last_error"] or {}).get("error", "") + "\n" + (attached or ""))
        _md("**AskTim!** has no AI set up yet.\n\n%s\n\n%s" % (safe_markdown("\n".join("- " + t for t in tips)) if tips else "", str(e).replace("\n\n", "\n\n").replace("    AskTim!", "`AskTim!").replace("Claude\n", "Claude`\n")))
        return None
    print(_ansi(g.summary(), "90"))
    conv = _STATE["chat"]
    conv.user(g.text() + "Question: " + q_clean)
    st = _Streamer()
    try:
        turn = prov.complete(system, conv, purpose="chat", on_text=st.feed, on_status=st.status)
    except ProviderError as e:
        conv.items.pop()
        st.done("\n\n**The AI service returned an error.** %s" % safe_markdown(str(e)))
        return None
    except KeyboardInterrupt:
        conv.items.pop()
        st.done("\n\n*(stopped)*")
        return None
    conv.items[-1] = {"kind": "user", "text": "Question: " + q_clean}
    conv.assistant(turn)
    conv.items[:] = conv.items[-20:]
    blocks = code_blocks(turn.text)
    _STATE["answers"] = blocks
    st.done(_footer(blocks) + "\n\n<small>%s · %s</small>" % (html.escape(prov.describe()), "data stayed on this computer"))
    logbook.add({"mode": "chat", "question": q_clean, "model": turn.model, "provider": prov.name, "sent": g.sent, "cwd": paths.pretty(os.getcwd()), "notebook": True}, cfg)
    return None


def _insert_block(n):
    blocks = _STATE["answers"]
    if not blocks:
        _md("There is no answer with code yet.")
        return
    if n < 1 or n > len(blocks):
        _md("The last answer has %d code block%s." % (len(blocks), "" if len(blocks) == 1 else "s"))
        return
    lang, code = blocks[n - 1]
    src = ("%%bash\n" + code) if lang in ("bash", "sh", "shell", "console") else code
    if _insert_cell(src):
        print(_ansi("Block %d is in a new cell below: read it, then run it." % n, "36"))
    else:
        _html(_copy_box(src, lang or "python", "Block %d – copy it into a new cell" % n))


# ------------------------------------------------------------------------------------------------
# the agent in a notebook
# ------------------------------------------------------------------------------------------------


class _Tee(io.TextIOBase):
    def __init__(self, orig, buf):
        self.orig = orig
        self.buf = buf

    def write(self, s):
        self.buf.append(s)
        return self.orig.write(s)

    def flush(self):
        try:
            self.orig.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return getattr(self.orig, "encoding", "utf-8")


class NotebookTools(object):
    """The tools only a notebook has: run_python, describe_variable, add_*_cell."""

    def __init__(self, ip, cfg):
        self.ip = ip
        self.cfg = cfg
        self.cells = []

    def packages(self):
        return _packages()

    def state_text(self):
        lines = []
        vs = variables_text(strict=self.cfg.strict)
        if vs:
            lines.append("The student's notebook variables (names, types and shapes only):")
            lines += ["  " + privacy.clean(v, strict=self.cfg.strict)[0] for v in vs]
        cells, total = _cells_run(max_cells=8, max_chars=3000)
        if cells:
            lines.append("The code of the student's last %d cells (of %d):" % (len(cells), total))
            lines.append("```python\n%s\n```" % "\n\n".join(privacy.clean(privacy.filter_code(c, strict=self.cfg.strict)[0], strict=self.cfg.strict)[0] for c in cells))
        err = _STATE["last_error"]
        if err and time.time() - err["time"] < 3600:
            fr = privacy.filter_output(err["traceback"], strict=self.cfg.strict)
            lines.append("The student's last error:\n```text\n%s\n```" % privacy.shorten(fr.text, 2000))
        lines.append("Notebook: %s; its folder is %s." % (frontend(), paths.pretty(os.getcwd())))
        return "\n".join(lines)

    def handle(self, agent, call, step):
        name = call.name
        if name == "run_python":
            return self._run_python(agent, call, step)
        if name == "describe_variable":
            v = str(call.input.get("name") or "")
            ns = self.ip.user_ns
            step["decision"] = "auto"
            if v not in ns:
                return ToolResult(call.id, name, "There is no variable called %s." % v, True)
            d = context.describe_value(v, ns[v], strict=self.cfg.strict) or v
            extra = self._missing(ns[v]) if not self.cfg.strict else ""
            msg = privacy.clean(d + extra, strict=self.cfg.strict)[0]
            step["sent"] = msg
            agent.ui.note("AskTim! described the variable %s to the AI: %s" % (v, msg))
            return ToolResult(call.id, name, msg)
        if name in ("add_markdown_cell", "add_code_cell"):
            src = str(call.input.get("text") or call.input.get("code") or "")
            kind = "markdown" if name == "add_markdown_cell" else "code"
            step["text" if kind == "markdown" else "code"] = src
            if kind == "code":
                v = safety.Verdict()
                safety._check_text_code(src, v, safety.Context(agent.work_dir, student_dir=agent.student_dir, allow_network=agent.allow_network), "Python")
                if v.level == "block":
                    step["decision"] = "blocked"
                    step["blocked"] = v.reasons
                    agent.ui.blocked(step, v)
                    return ToolResult(call.id, name, "Not added: %s." % "; ".join(v.reasons), True)
            step["decision"] = "auto"
            self.cells.append({"kind": kind, "source": src, "why": str(call.input.get("why") or "")})
            agent.ui.cell_added(step, kind, src)
            return ToolResult(call.id, name, "Added a %s cell to the notebook (it is cell %d of this run's cells; they appear when the run ends)." % (kind, len(self.cells)))
        return ToolResult(call.id, name, "Unknown tool.", True)

    def _missing(self, v):
        try:
            if type(v).__name__ == "DataFrame":
                na = v.isna().sum()
                bad = [(str(c), int(n)) for c, n in na.items() if n]
                return "; missing values: %s" % (", ".join("%s %d" % x for x in bad[:20]) if bad else "none")
        except Exception:
            pass
        return ""

    def _check(self, agent, code):
        v = safety.Verdict()
        ctx = safety.Context(agent.work_dir, student_dir=agent.student_dir, allow_network=agent.allow_network)
        ctx.protect = agent.protect
        safety._check_text_code(code, v, ctx, "Python")
        safety.check_notebook_page(code, v)
        # shell lines inside the code (!cmd, %%bash cells) get the shell's checks
        shell = []
        lines = code.splitlines()
        if lines and lines[0].strip().startswith(("%%bash", "%%sh", "%%script")):
            shell.append("\n".join(lines[1:]))
        for ln in lines:
            s = ln.strip()
            if s.startswith("!"):
                shell.append(s[1:])
            elif s.startswith(("%system", "%sx")):
                shell.append(s.split(None, 1)[1] if " " in s else "")
            elif s.startswith(("%pip", "%conda", "%mamba")):
                v.block("installing packages is not allowed")
        for sh in shell:
            sv = safety.check(sh, agent.work_dir, allow_network=agent.allow_network, student_dir=agent.student_dir, cwd=os.getcwd(), protect=agent.protect)
            for r in sv.reasons:
                (v.block if sv.level == "block" else v.confirm)(r)
        return v

    def _run_python(self, agent, call, step):
        code = str(call.input.get("code") or "")
        step["code"] = code
        verdict = self._check(agent, code)
        decision, new, comment = agent.decide(step, "python", verdict, code)
        if decision == "blocked":
            return ToolResult(call.id, call.name, "Not run: AskTim! does not allow this code – %s." % "; ".join(verdict.reasons), True)
        if decision == "refuse":
            return ToolResult(call.id, call.name, "Not run: the student refused this step." + (" Their reason: %s" % comment if comment else ""), True)
        prefix = ("The student allowed this step, with a comment: %s\n" % comment) if comment else ""
        ns = self.ip.user_ns
        before = {k: id(v) for k, v in ns.items() if not k.startswith("_")}
        buf = []
        shown = []
        pub = self.ip.display_pub
        orig_publish = pub.publish

        def publish(data, *a, **k):
            try:
                kinds = sorted(data.keys()) if isinstance(data, dict) else []
                shown.append("figure" if any(x.startswith("image/") for x in kinds) else "table" if "text/html" in kinds else ", ".join(kinds))
            except Exception:
                pass
            return orig_publish(data, *a, **k)

        old_out, old_err = sys.stdout, sys.stderr
        t0 = time.time()
        _STATE["nested"] += 1
        try:
            pub.publish = publish
            sys.stdout, sys.stderr = _Tee(old_out, buf), _Tee(old_err, buf)
            res = self.ip.run_cell(code, store_history=False)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            pub.publish = orig_publish
            _STATE["nested"] -= 1
        dt = time.time() - t0
        err = getattr(res, "error_in_exec", None) or getattr(res, "error_before_exec", None)
        step["ok"] = err is None
        step["exit"] = 0 if err is None else 1
        step["seconds"] = round(dt, 2)
        printed = "".join(buf)
        fr = privacy.filter_output(printed, strict=self.cfg.strict, places=[os.getcwd(), agent.work_dir])
        agent.withheld += fr.withheld
        step["withheld"] = fr.withheld
        step["lines"] = fr.total
        step["output_tail"] = privacy.mask_identity("\n".join(printed.splitlines()[-30:]))
        parts = [prefix + ("It ran without an error (%.1f s)." % dt if err is None else "It raised %s: %s" % (type(err).__name__, privacy.clean(str(err), strict=self.cfg.strict)[0][:500]))]
        if err is not None:
            tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
            tf = privacy.filter_output(tb, strict=self.cfg.strict)
            parts.append("Traceback (data lines withheld):\n" + privacy.shorten(tf.text, 2500))
        if printed.strip():
            parts.append("What it printed (%d line%s%s):\n%s" % (fr.total, "" if fr.total == 1 else "s", "; %d withheld as data" % fr.withheld if fr.withheld else "", privacy.shorten(fr.text, agent.out_chars)))
        r = getattr(res, "result", None)
        if r is not None:
            parts.append("The result of the last line (shown to the student): " + (context.describe_value("result", r, strict=self.cfg.strict) or type(r).__name__).replace("result: ", "", 1))
        if shown:
            parts.append("Displayed to the student: %s (you are not sent what it shows)." % ", ".join(shown))
        changed = [k for k, v in ns.items() if not k.startswith("_") and (k not in before or before[k] != id(v)) and k not in ("In", "Out")]
        if changed:
            ds = [context.describe_value(k, ns[k], strict=self.cfg.strict) for k in changed[:15]]
            parts.append("New or changed variables: " + "; ".join(privacy.clean(d, strict=self.cfg.strict)[0] for d in ds if d))
        msg = "\n".join(parts)
        step["sent"] = msg
        agent.ui.python_result(step, err, fr, shown)
        return ToolResult(call.id, call.name, msg, err is not None)

    def finish_run(self, rec):
        rp = os.path.join(rec["work_dir"], "agent-record.ipynb") if not rec.get("here") else None
        deliver_cells(self.cells, record_path=rp)


class NotebookUI(object):
    """The agent's steps under a notebook cell; questions are asked with input()."""

    def __init__(self, ip):
        self.ip = ip
        self.agent_mode = True
        self._held = []
        self._think = None

    @property
    def interactive(self):
        k = getattr(self.ip, "kernel", None)
        return bool(k is not None and getattr(k, "_allow_stdin", True))

    def _input(self, prompt):
        try:
            return input(prompt)
        except (EOFError, Exception) as e:
            if type(e).__name__ == "StdinNotImplementedError":
                return None
            if isinstance(e, KeyboardInterrupt):
                raise
            return None

    def note(self, text):
        print(_ansi(text, "90"))

    def warn(self, text):
        print(_ansi(text, "33"))

    def error(self, text):
        print(_ansi(text, "31"))

    def status(self, text):
        self.note("… " + text)

    def thinking(self):
        self._held = []
        from IPython.display import Markdown

        self._think = _display(Markdown("*thinking…*"), display_id=True)

    def stream_text(self, delta):
        if delta:
            self._held.append(delta)

    def end_text(self):
        if self._think is not None:
            try:
                from IPython.display import HTML

                self._think.update(HTML(""))
            except Exception:
                pass
            self._think = None

    def agent_says(self, text):
        if (text or "").strip():
            _md(safe_markdown(text))

    def start(self, info):
        _html("<div style='font-family:sans-serif;border-left:4px solid #3a7bd5;padding:4px 10px;margin:6px 0'>"
              "<b>AskTim! Agent</b> · run %d · %s<br><b>Task:</b> %s<br><span style='color:#666;font-size:12px'>Folder: %s · %s · %s; internet %s; at most %d steps. "
              "The AI never sees your data: output reaches it with data lines withheld. "
              "Python steps run in this notebook with your own permissions (no sandbox).</span></div>" % (
                  info["number"], html.escape({"approve": "approve each step", "plan": "plan first", "auto": "autonomous", "script": "write a script"}[info["mode"]]),
                  html.escape(info["task"]), html.escape(info["folder"]), html.escape(info["model"]), html.escape(info["sandbox"]),
                  "allowed for downloads" if info["network"] else "off", info["max_steps"]))

    def show_step(self, step, kind, show, verdict, asking=False):
        what = {"command": "run a command", "file": "write a file", "open": "open a file", "python": "run Python code"}.get(kind, kind)
        lang = {"command": "bash", "python": "python"}.get(kind, "")
        body = "**Step %d · %s**%s\n\n" % (step["n"], what, (" – " + inline_markdown(step["why"])) if step.get("why") else "")
        if kind == "file":
            body += "%s\n\n" % inline_code(step.get("path"))
            body += _fence(show if show.count("\n") < 60 else "\n".join(show.splitlines()[:60]) + "\n… (%d more lines)" % (show.count("\n") - 59), "")
        elif kind == "open":
            body += "Open %s" % inline_code(show)
        else:
            body += _fence(show, lang)
        if verdict is not None and verdict.level == "confirm":
            body += "\n\n⚠️ AskTim! asks you to check this one: " + inline_markdown("; ".join(verdict.reasons))
        _md(body)

    def approve(self, step, kind, show, verdict):
        from .ui import Decision

        self.show_step(step, kind, show, verdict, asking=True)
        if kind == "open":
            a = (self._input("Open it? [y/N] ") or "n").strip().lower()
            return Decision("run" if a.startswith("y") else "refuse")
        while True:
            a = self._input("Run it? [Enter/y] yes · [n] no · [a] all (stop asking) · [q] quit: " if kind != "file" else "Write it? [Enter/y] yes · [n] no · [a] all · [q] quit: ")
            if a is None:
                return Decision("stop")
            a = a.strip().lower()
            if a in ("", "y", "yes"):
                return Decision("run")
            if a in ("a", "all") and not (verdict is not None and verdict.level == "confirm"):
                return Decision("all")
            if a in ("q", "quit", "stop"):
                return Decision("stop")
            if a in ("n", "no"):
                why = self._input("Why not, or what should it do instead? (the agent is told; Enter to skip): ") or ""
                return Decision("refuse", comment=why.strip() or None)

    def blocked(self, step, verdict):
        _md("**Step %d** – ⛔ not allowed: %s\n\n%s" % (step["n"], inline_markdown("; ".join(verdict.reasons)), _fence(step.get("command") or step.get("code") or "", "bash" if step.get("command") else "python")))

    def running(self, step):
        pass

    def output(self, stream, text):
        (sys.stderr if stream == "stderr" else sys.stdout).write(text)

    def result(self, step, res, fr):
        ok = res.exit_code == 0
        bits = ["%.1f s" % res.duration]
        if fr.withheld:
            bits.append("the AI was shown %d of %d lines; %d withheld as data" % (fr.kept, fr.total, fr.withheld))
        print(_ansi(("✓ exit 0" if ok else "✗ exit %s" % res.exit_code), "32" if ok else "31") + _ansi(" · " + " · ".join(bits), "90"))

    def python_result(self, step, err, fr, shown):
        bits = ["%.1f s" % step.get("seconds", 0)]
        if fr.withheld:
            bits.append("%d printed line%s withheld from the AI as data" % (fr.withheld, "" if fr.withheld == 1 else "s"))
        if shown:
            bits.append("the AI was told only that %s was shown" % " and ".join(shown))
        print(_ansi("✓ ran" if err is None else "✗ error", "32" if err is None else "31") + _ansi(" · " + " · ".join(bits), "90"))

    def inspected(self, step, desc):
        _md("**Step %d · look at a file** – AskTim! described %s to the AI (its structure, not its contents):\n\n%s" % (step["n"], inline_code(step.get("path")), _fence(desc, "text")))

    def cell_added(self, step, kind, src):
        _md("**Step %d · %s cell for your notebook**%s\n\n%s" % (step["n"], kind.capitalize(), (" – " + inline_markdown(step["why"])) if step.get("why") else "", _fence(src, "python" if kind == "code" else "markdown")))

    def ask_stop(self):
        a = self._input("Stop the agent? [y/N] ") or ""
        return a.strip().lower().startswith("y")

    def plan_review(self, plan):
        _md("**The agent's plan**\n\n" + safe_markdown(plan or ""))
        if not self.interactive:
            return "cancel", None
        while True:
            a = self._input("Approve the plan? [Enter/y] yes · [s] yes, but ask before each step · [c] ask for changes · [q] cancel: ")
            if a is None:
                return "cancel", None
            a = a.strip().lower()
            if a in ("", "y", "yes"):
                return "approve-auto", None
            if a.startswith("s"):
                return "approve-steps", None
            if a.startswith("c"):
                fb = (self._input("What should change? ") or "").strip()
                if fb:
                    return "change", fb
            if a.startswith("q"):
                return "cancel", None

    def reply_to_question(self, text):
        _md(safe_markdown(text or ""))
        return (self._input("The agent asked you something. Your answer (Enter to end the run): ") or "").strip()

    def open_file(self, path, program=None):
        if program:
            from . import viewers

            ok, how = viewers.launch(path, program)
            if ok:
                return ok, how
            shown, how2 = self.open_file(path)
            return shown, "%s; %s" % (how, how2) if shown else how
        try:
            low = path.lower()
            size = os.path.getsize(path)
            if low.endswith((".html", ".htm")):
                if size > 25 << 20:
                    return False, "the report is too large to show here (%s)" % fileinfo.human(size)
                with open(path, encoding="utf-8", errors="replace") as f:
                    doc = f.read()
                _html('<iframe srcdoc="%s" style="width:100%%;height:600px;border:1px solid #ccc" sandbox="allow-scripts"></iframe>' % html.escape(doc, quote=True))
                return True, "shown under the cell"
            if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
                from IPython.display import Image, SVG

                _display(SVG(filename=path) if low.endswith(".svg") else Image(filename=path))
                return True, "shown under the cell"
            if low.endswith(".pdf"):
                if size > 20 << 20:
                    return False, "the PDF is too large to show here"
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                _html('<embed src="data:application/pdf;base64,%s" type="application/pdf" style="width:100%%;height:600px">' % b64)
                return True, "shown under the cell"
            if fileinfo.is_code_file(path) or low.endswith((".txt", ".log", ".md")):
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read(200000)
                _md(_fence(text, ""))
                return True, "shown under the cell"
            return False, "AskTim! shows HTML reports, images, PDFs and text files; open %s yourself" % paths.pretty(path)
        except OSError as e:
            return False, str(e)

    def report(self, text, outcome):
        _md("**The agent's report**%s\n\n%s\n\n<small>The report is the model's own words: check it against the steps above.</small>" % ("" if outcome == "done" else " (%s)" % outcome, safe_markdown(text or "(no report)")))

    def finish(self, written, rec):
        self.note("AskTim! wrote the record of this run: %s" % ", ".join(written))


def nb_agent(task, mode="approve", here=False, web=None, steps=None, timeout=None, model=None):
    from .agent import Agent

    ip = _STATE["ip"]
    if ip is None:
        from IPython import get_ipython

        ip = get_ipython()
        load(ip)
    cfg = _config.Config()
    ui = NotebookUI(ip)
    if mode in ("approve", "plan") and not ui.interactive:
        _md("The agent needs you to approve its steps, and this notebook cannot ask (it is not being run by a person). Use `AskTim! Agent --auto ...` to let it work alone.")
        return None
    try:
        prov = make_provider(cfg, model=model)
    except ProviderError as e:
        _md("**AskTim!** " + safe_markdown(str(e)))
        return None
    if web and cfg.is_locked("agent.allow_network") and not cfg.get("agent.allow_network"):
        web = False
    tools = NotebookTools(ip, cfg)
    ag = Agent(cfg, prov, ui, task, mode=mode, here=here, allow_network=web, max_steps=steps, timeout=timeout, notebook=tools, student_dir=os.getcwd())
    return ag.run()


# ------------------------------------------------------------------------------------------------
# the magic: everything after "AskTim!" in a cell
# ------------------------------------------------------------------------------------------------


def _scrub_key(raw_line, key):
    """A key typed into a cell is in the notebook and in IPython's history: take it out where possible."""
    ip = _STATE["ip"]
    hm = getattr(ip, "history_manager", None)
    fixed = False
    try:
        for lst in (hm.input_hist_raw, hm.input_hist_parsed):
            for i in range(len(lst) - 1, max(-1, len(lst) - 4), -1):
                if key in lst[i]:
                    lst[i] = lst[i].replace(key, "[key removed]")
                    fixed = True
        for i, ent in enumerate(list(getattr(hm, "db_input_cache", []))):
            if key in str(ent):
                hm.db_input_cache[i] = tuple(x.replace(key, "[key removed]") if isinstance(x, str) else x for x in ent)
        try:
            with hm.db:
                hm.db.execute("UPDATE history SET source=replace(source, ?, '[key removed]'), source_raw=replace(source_raw, ?, '[key removed]') WHERE session=?", (key, key, hm.session_number))
        except Exception:
            pass
    except Exception:
        pass
    replaced = _insert_cell(raw_line.replace(key, "").rstrip(), replace=True)
    return fixed, replaced


def _magic(line, cell=None):
    from . import cli

    try:
        argv = shlex.split(line or "")
    except ValueError:
        argv = (line or "").split()
    a = cli.parse(argv)
    first = a.pos[0].lower() if a.pos else ""
    cfg = _config.Config()
    from .ui import TerminalUI

    ui = TerminalUI(cfg)
    ui.t.color = True
    prov = _config.normalise_provider(first)
    if prov and (len(a.pos) == 1 or (len(a.pos) == 2 and _config.looks_like_key(a.pos[1])) or a.opts.get("--url")):
        key = a.pos[1] if len(a.pos) == 2 else None
        if key:
            fixed, replaced = _scrub_key("AskTim! " + line, key)
            print(_ansi("Your key was typed into this cell, so it is in the notebook. %s Next time type just `AskTim! %s` and paste the key when asked%s." % (
                "AskTim! has removed it from the cell." if replaced else "Delete it from the cell before you save or share the notebook!",
                {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI"}[prov], " - or, in Colab, use Secrets (the key icon) with the name ANTHROPIC_API_KEY" if frontend() == "colab" else ""), "33"))
        if frontend() == "colab" and not key:
            print(_ansi("In Colab you can instead store the key in Secrets (the key icon on the left), named ANTHROPIC_API_KEY (Claude), GEMINI_API_KEY or OPENAI_API_KEY, and give this notebook access. Colab's runtime forgets a key stored here when it stops.", "90"))
        return cli.setup(cfg, ui, prov, key, a, key_on_command_line=False)
    if first == "agent":
        task = " ".join(a.pos[1:]).strip()
        if cell and cell.strip():
            task = (task + "\n" + cell).strip()
        if not task:
            print("Give the agent a task:  AskTim! Agent \"what it should do\"")
            return None
        mode = "plan" if a.opts.get("--plan") else "auto" if a.opts.get("--auto") else "script" if a.opts.get("--script") else "approve"
        nb_agent(task, mode=mode, here=bool(a.opts.get("--here")), web=True if a.opts.get("--web") else None, steps=a.opts.get("--steps"), timeout=a.opts.get("--timeout"), model=a.opts.get("--model"))
        return None
    if first == "insert" and len(a.pos) == 2 and a.pos[1].isdigit():
        _insert_block(int(a.pos[1]))
        return None
    if first == "cells":
        sub = a.pos[1].lower() if len(a.pos) > 1 else "next"
        q = _STATE["queue"]
        if not q:
            print("There are no cells waiting from the agent.")
        elif sub == "all":
            src = "\n\n".join(("# " + c["source"].replace("\n", "\n# ")) if c["kind"] == "markdown" else c["source"] for c in q)
            if not _insert_cell(src):
                _html(_copy_box(src, "python", "All the agent's cells, as one"))
            _STATE["queue"] = []
        elif sub == "clear":
            _STATE["queue"] = []
            print("The waiting cells are forgotten.")
        else:
            if not _deliver_next():
                _show_cells(q, "The agent's cells")
        return None
    if first == "new" and len(a.pos) == 1:
        _STATE["chat"] = Conversation()
        print("The conversation is forgotten; the next question starts afresh.")
        return None
    if first in ("why", "explain") and len(a.pos) == 1:
        return nb_ask("Why did my last cell fail, and how do I fix it?")
    if first == "show" and len(a.pos) >= 2:
        g = notebook_context(cfg, attached=cell)
        inv = inventory.gather()
        _md("**What AskTim! would send (nothing has been sent):**\n\n" + _fence(prompts.chat_system(inventory.describe(inv, notebook_packages=_packages()), cfg, notebook=True), "text") + "\n\n" + _fence(g.text() + "Question: " + " ".join(a.pos[1:]), "text") + "\n\n" + safe_markdown(g.summary()))
        return None
    if (len(a.pos) == 1 and first in cli.SIMPLE_COMMANDS) or (len(a.pos) == 2 and first in ("model", "use", "forget", "log")) or a.opts.get("--help"):
        if a.opts.get("--help") or first == "help":
            print(cli.HELP)
            return None
        cli.command(cfg, ui, first, a)
        return None
    question = " ".join(a.pos).strip()
    nb_ask(question, attached=cell, new=bool(a.opts.get("--new")), model=a.opts.get("--model"), files=[f for f in a.files if f])
    return None
