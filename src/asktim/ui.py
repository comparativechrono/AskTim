"""The command-line face of AskTim!: chat answers and the agent's steps.

By default AskTim! prints little besides the AI's answer: for the agent, its commands (and why,
when it asks), what they print, failures and its report. With -v (or "verbose": true in the
settings) it also prints what was sent, the safeguards, what the AI was shown of each output and
where the record of a run is.
"""

import os
import shutil
import subprocess
import sys
import time

from . import paths
from .render import Term, visible

DATA_OFF_REMINDER = "AskTim!'s data filter is off, so your data can be sent to the AI. `AskTim! privacy on` turns it back on."


def data_off_reminder_due(cfg):
    """True (once a day) when the data filter is off: the reminder is due, and is now counted as given."""
    if not cfg.share_data:
        return False
    stamp = os.path.join(paths.state_dir(), "data-filter-off-reminded")
    today = time.strftime("%Y-%m-%d")
    try:
        with open(stamp) as f:
            if f.read().strip() == today:
                return False
    except OSError:
        pass
    try:
        paths.ensure(paths.state_dir(), private=True)
        with open(stamp, "w") as f:
            f.write(today)
    except OSError:
        pass
    return True


MODE_LABEL = {"approve": "approve each step", "plan": "plan first", "auto": "autonomous", "script": "write a script"}


class Decision(object):
    def __init__(self, choice, text=None, comment=None):
        self.choice = choice  # run | edit | refuse | all | stop
        self.text = text
        self.comment = comment


class TerminalUI(object):
    def __init__(self, cfg=None, term=None, show_ai_view=True, verbose=None):
        self.t = term or Term((cfg.get("color") if cfg else "auto") or "auto")
        self.verbose = bool(cfg.verbose if (verbose is None and cfg is not None) else verbose)
        self.md = None
        self.show_ai_view = show_ai_view
        self._streamed = False
        self._last_was_output = False
        self.agent_mode = False  # in agent runs the model's text is held until it is known what it is
        self._held = []

    @property
    def interactive(self):
        return self.t.interactive()

    # -- general --------------------------------------------------------------------------------

    def note(self, text):
        """AskTim!'s own remarks: printed only with -v."""
        if self.verbose:
            self.t.note(text)

    def warn(self, text):
        self.t.warn(text)

    def error(self, text):
        self.t.error(text)

    def status(self, text):
        if not self.verbose:
            return
        if self.md is not None and self._streamed:
            self.t.write("\n")
        self.t.note("… " + text)

    def data_off_reminder(self, cfg):
        """With the data filter off, say so: with -v the summary of what was sent says it every time;
        otherwise once a day, so that it is not forgotten."""
        if not self.verbose and data_off_reminder_due(cfg):
            self.t.warn(DATA_OFF_REMINDER)

    # -- streaming text of the model ------------------------------------------------------------

    def thinking(self):
        self._held = []
        self.md = None if self.agent_mode else self.t.markdown()
        self._streamed = False
        if self.t.color and self.t.interactive():
            self.t.write(self.t.s("thinking…", "gray"))
            self._thinking_shown = True
        else:
            self._thinking_shown = False

    def _clear_thinking(self):
        if getattr(self, "_thinking_shown", False):
            self.t.write("\r\x1b[K")
            self._thinking_shown = False

    def stream_text(self, delta):
        if not delta:
            return
        if self.agent_mode:
            self._held.append(delta)
            return
        self._clear_thinking()
        self._streamed = True
        if self.md is None:
            self.md = self.t.markdown()
        self.md.feed(delta)

    def end_text(self):
        self._clear_thinking()
        if self.md is not None:
            self.md.close()
        self.md = None

    def agent_says(self, text):
        """What the model wrote before its tool calls: shown above the step."""
        text = (text or "").strip()
        if text:
            md = self.t.markdown()
            md.feed(text)
            md.close()

    # -- the agent ------------------------------------------------------------------------------

    def start(self, info):
        self.agent_mode = True
        t = self.t
        if not self.verbose:
            return
        t.line()
        t.rule("AskTim! Agent · run %d · %s" % (info["number"], MODE_LABEL.get(info["mode"], info["mode"])), "cyan")
        t.line(t.s("Task: ", "bold") + info["task"])
        t.note("Folder: %s   ·   %s" % (info["folder"], info["model"]))
        t.note("Safeguards: %s; internet %s; at most %d steps." % (info["sandbox"], "allowed for downloads" if info["network"] else "off", info["max_steps"]))
        if info.get("share_data"):
            t.note("The data filter is off (AskTim! privacy on turns it on): what your commands print reaches the AI. Ctrl+C stops at any time.")
        else:
            t.note("The AI never sees your data: what your commands print reaches it with data lines withheld. Ctrl+C stops at any time.")
        t.line()

    def _step_header(self, step, kind, asking=False):
        what = {"command": "run a command", "file": "write a file", "open": "open a file", "python": "run Python code", "cell": "add a cell"}.get(kind, kind)
        if self.verbose:
            self.t.rule("Step %d · %s" % (step["n"], what), "blue")
        if step.get("why") and (self.verbose or asking):
            self.t.line(self.t.s("Why: ", "bold") + visible(step["why"]))

    def show_step(self, step, kind, show, verdict, asking=False):
        self._step_header(step, kind, asking)
        show = visible(show or "")
        # quiet, and not asked to approve it: the step in a few lines (RUN.md has all of it)
        brief = not (self.verbose or asking)
        if kind == "command":
            lines = show.splitlines() or [""]
            for i, ln in enumerate(lines[:3] if brief else lines):
                self.t.line(self.t.s(("$ " if i == 0 else "  ") + ln, "green"))
            if brief and len(lines) > 3:
                self.t.line(self.t.s("  … (%d more lines)" % (len(lines) - 3), "gray"))
        elif kind == "file":
            lines = show.splitlines()
            self.t.line(self.t.s("File: %s (%d lines)" % (visible(str(step.get("path"))), len(lines)), "bold"))
            for ln in [] if brief else lines[:40]:
                self.t.line(self.t.s("  " + ln, "green"))
            if len(lines) > 40 and not brief:
                self.t.line(self.t.s("  … %d more lines (press v to see them all)" % (len(lines) - 40), "gray"))
        elif kind == "python":
            lines = show.splitlines()
            for ln in lines[:3] if brief else lines:
                self.t.line(self.t.s("  " + ln, "green"))
            if brief and len(lines) > 3:
                self.t.line(self.t.s("  … (%d more lines)" % (len(lines) - 3), "gray"))
        elif kind == "open":
            self.t.line("Open %s" % show)
        if verdict is not None and verdict.level == "confirm":
            self.t.warn("AskTim! asks you to check this one: " + visible("; ".join(verdict.reasons)))

    def approve(self, step, kind, show, verdict):
        self.show_step(step, kind, show, verdict, asking=True)
        if kind == "open":
            ans = self.t.ask("Open it? [y/N] ", "n").lower()
            return Decision("run" if ans.startswith("y") else "refuse")
        extra = " [v]iew all" if kind == "file" and show.count("\n") > 40 else ""
        prompt = "Run it? [Enter/y] yes · [n]o · [e]dit · [a]ll (stop asking)%s · [q]uit: " % extra if kind != "file" else "Write it? [Enter/y] yes · [n]o · [e]dit · [a]ll (stop asking)%s · [q]uit: " % extra
        if verdict is not None and verdict.level == "confirm":
            prompt = prompt.replace(" · [a]ll (stop asking)", "")
        while True:
            ans = self.t.ask(prompt, "y")
            if ans is None:
                return Decision("stop")
            a = ans.lower()
            if a in ("y", "yes", ""):
                return Decision("run")
            if a.startswith("v") and kind == "file":
                for ln in visible(show).splitlines():
                    self.t.line(self.t.s("  " + ln, "green"))
                continue
            if a in ("a", "all") and not (verdict is not None and verdict.level == "confirm"):
                self.t.note("From now on the agent's steps run without asking (blocked commands stay blocked; risky ones still ask).")
                return Decision("all")
            if a in ("q", "quit", "stop"):
                return Decision("stop")
            if a in ("n", "no"):
                why = self.t.ask("Why not? (the agent is told; Enter to skip): ", "")
                return Decision("refuse", comment=why or None)
            if a in ("e", "edit"):
                if kind == "file":
                    new = self._edit_file(step.get("path"), show)
                else:
                    new = self.t.edit("$ ", show)
                if new is None:
                    continue
                note = self.t.ask("A note for the agent about your change? (Enter to skip): ", "")
                return Decision("edit", text=new, comment=note or None)
            self.t.note("Type y, n, e, a or q.")

    def _edit_file(self, path, content):
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "vi")
        import tempfile

        suffix = os.path.splitext(path or "")[1] or ".txt"
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
            f.write(content)
            tmp = f.name
        try:
            subprocess.call(editor.split() + [tmp])
            with open(tmp) as f:
                return f.read()
        except OSError:
            self.t.warn("Could not start an editor (%s). Set EDITOR, or refuse the step and say what to change." % editor)
            return None
        finally:
            os.unlink(tmp)

    def blocked(self, step, verdict):
        self._step_header(step, "command" if step["tool"] == "run_command" else "file")
        if step.get("command"):
            lines = visible(step["command"]).splitlines()
            for i, ln in enumerate(lines if self.verbose else lines[:3]):
                self.t.line(self.t.s(("$ " if i == 0 else "  ") + ln, "gray"))
        self.t.error("Not allowed – " + visible("; ".join(verdict.reasons)))
        self.note("The agent is told why, and has to find another way.")

    def running(self, step):
        self._last_was_output = False

    def output(self, stream, text):
        # what the command prints goes straight to the user's terminal
        if stream == "stderr":
            self.t.write(self.t.s(text, "yellow") if self.t.color else text)
        else:
            self.t.write(text)
        self._last_was_output = True

    def result(self, step, res, fr):
        t = self.t
        if not self.verbose:
            # quiet: only what went wrong
            if res.exit_code != 0 or res.timed_out:
                st = ("✗ exit %s" if t.uni else "failed: exit %s") % res.exit_code
                t.line(t.s(st + (" · stopped after the time limit" if res.timed_out else ""), "red"))
            return
        if res.exit_code == 0:
            st = t.s("✓ exit 0" if t.uni else "ok: exit 0", "green")
        else:
            st = t.s(("✗ exit %s" if t.uni else "failed: exit %s") % res.exit_code, "red")
        extra = []
        if res.timed_out:
            extra.append("stopped after the time limit")
        if fr.withheld:
            extra.append("the AI was shown %d of %d lines; %d withheld as data" % (fr.kept, fr.total, fr.withheld))
        elif res.lines:
            extra.append("the AI was shown the output (no data found in it)")
        t.line("%s %s" % (st, t.s("· %.1f s%s" % (res.duration, (" · " + "; ".join(extra)) if extra else ""), "gray")))

    def inspected(self, step, desc):
        if not self.verbose:
            return
        self.t.rule("Step %d · look at a file" % step["n"], "blue")
        self.t.note("AskTim! described %s to the AI (its structure, not its contents):" % visible(str(step.get("path"))))
        self.t.line("  " + desc)

    def ask_stop(self):
        ans = self.t.ask("\nStop the agent? [y/N] ", "n")
        return bool(ans) and ans.lower().startswith("y")

    def plan_review(self, plan):
        t = self.t
        t.line()
        t.rule("The agent's plan", "cyan")
        t.line(visible(plan or ""))
        t.rule("", "cyan")
        if not self.interactive:
            return "cancel", None
        while True:
            ans = t.ask("Approve the plan? [Enter/y] yes, run the steps · [s] yes, but ask before each step · [c] ask for changes · [q] cancel: ", "y").lower()
            if ans in ("y", "yes", ""):
                return "approve-auto", None
            if ans.startswith("s"):
                return "approve-steps", None
            if ans.startswith("c"):
                fb = t.ask("What should change? ", "")
                if fb:
                    return "change", fb
                continue
            if ans.startswith("q"):
                return "cancel", None

    def reply_to_question(self, text):
        return self.t.ask("\nThe agent asked you something. Your answer (Enter to end the run): ", "")

    def open_file(self, path, program=None):
        if program:
            from . import viewers

            return viewers.launch(path, program)
        if sys.platform == "darwin":
            cmd = ["open", path]
        elif os.name == "nt":
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return True, "with its default program"
            except OSError as e:
                return False, str(e)
        else:
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                return False, "this computer has no screen to show it on (no DISPLAY); %s can be opened on another computer" % paths.pretty(path)
            if not shutil.which("xdg-open"):
                return False, "xdg-open is not installed"
            cmd = ["xdg-open", path]
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True, "with its default program"
        except OSError as e:
            return False, str(e)

    def report(self, text, outcome):
        t = self.t
        t.line()
        t.rule("The agent's report" + ("" if outcome == "done" else " (%s)" % outcome), "cyan")
        md = t.markdown()
        md.feed(text or "(no report)")
        md.close()

    def finish(self, written, rec):
        t = self.t
        t.rule("", "cyan")
        if not self.verbose:
            return
        t.note("AskTim! wrote the record of this run: %s" % ", ".join(written))
        rerun = any(os.path.basename(w) == "rerun.sh" for w in written)
        t.note("Read RUN.md to check what happened%s. The report above is the model's own words: check it."
               % (", and rerun.sh before you run it" if rerun else ""))

    # -- chat -----------------------------------------------------------------------------------

    def chat_header(self, summary, model):
        if not self.verbose:
            return
        self.t.note(summary)
        self.t.note("(%s · AskTim! show \"…\" prints everything that would be sent)" % model)
