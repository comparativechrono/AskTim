"""The command-line face of AskTim!: chat answers and the agent's steps."""

import os
import shutil
import subprocess
import sys
import time

from . import paths
from .render import Term, visible

MODE_LABEL = {"approve": "approve each step", "plan": "plan first", "auto": "autonomous", "script": "write a script"}


class Decision(object):
    def __init__(self, choice, text=None, comment=None):
        self.choice = choice  # run | edit | refuse | all | stop
        self.text = text
        self.comment = comment


class TerminalUI(object):
    def __init__(self, cfg=None, term=None, show_ai_view=True):
        self.t = term or Term((cfg.get("color") if cfg else "auto") or "auto")
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
        self.t.note(text)

    def warn(self, text):
        self.t.warn(text)

    def error(self, text):
        self.t.error(text)

    def status(self, text):
        if self.md is not None and self._streamed:
            self.t.write("\n")
        self.t.note("… " + text)

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
        t.line()
        t.rule("AskTim! Agent · run %d · %s" % (info["number"], MODE_LABEL.get(info["mode"], info["mode"])), "cyan")
        t.line(t.s("Task: ", "bold") + info["task"])
        t.note("Folder: %s   ·   %s" % (info["folder"], info["model"]))
        t.note("Safeguards: %s; internet %s; at most %d steps." % (info["sandbox"], "allowed for downloads" if info["network"] else "off", info["max_steps"]))
        t.note("The AI never sees your data: what your commands print reaches it with data lines withheld. Ctrl+C stops at any time.")
        t.line()

    def _step_header(self, step, kind):
        what = {"command": "run a command", "file": "write a file", "open": "open a file", "python": "run Python code", "cell": "add a cell"}.get(kind, kind)
        self.t.rule("Step %d · %s" % (step["n"], what), "blue")
        if step.get("why"):
            self.t.line(self.t.s("Why: ", "bold") + visible(step["why"]))

    def show_step(self, step, kind, show, verdict, asking=False):
        self._step_header(step, kind)
        show = visible(show or "")
        if kind == "command":
            for i, ln in enumerate(show.splitlines() or [""]):
                self.t.line(self.t.s(("$ " if i == 0 else "  ") + ln, "green"))
        elif kind == "file":
            lines = show.splitlines()
            self.t.line(self.t.s("File: %s (%d lines)" % (visible(str(step.get("path"))), len(lines)), "bold"))
            for ln in lines[:40]:
                self.t.line(self.t.s("  " + ln, "green"))
            if len(lines) > 40:
                self.t.note("  … %d more lines (press v to see them all)" % (len(lines) - 40))
        elif kind == "python":
            for ln in show.splitlines():
                self.t.line(self.t.s("  " + ln, "green"))
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
            for i, ln in enumerate(visible(step["command"]).splitlines()):
                self.t.line(self.t.s(("$ " if i == 0 else "  ") + ln, "gray"))
        self.t.error("Not allowed – " + visible("; ".join(verdict.reasons)))
        self.t.note("The agent is told why, and has to find another way.")

    def running(self, step):
        self._last_was_output = False

    def output(self, stream, text):
        # what the command prints goes straight to the student's terminal
        if stream == "stderr":
            self.t.write(self.t.s(text, "yellow") if self.t.color else text)
        else:
            self.t.write(text)
        self._last_was_output = True

    def result(self, step, res, fr):
        t = self.t
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
                return False, "this computer has no screen to show it on (no DISPLAY); the student can open %s themselves" % paths.pretty(path)
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
        t.note("AskTim! wrote the record of this run: %s" % ", ".join(written))
        t.note("Read RUN.md to check what happened, and rerun.sh before you run it. The report above is the model's own words: check it.")

    # -- chat -----------------------------------------------------------------------------------

    def chat_header(self, summary, model):
        self.t.note(summary)
        self.t.note("(%s · AskTim! show \"…\" prints everything that would be sent)" % model)
