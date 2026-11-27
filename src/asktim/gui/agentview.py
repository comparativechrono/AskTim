"""How an agent's run looks in the chat window: its steps, the buttons that approve them, its plan,
its questions and its report. (ChatWindow is an AgentView; the calls come from agentui.py.)"""

import os

import tkinter as tk
from tkinter import ttk

from . import mdview
from .dialogs import ask_text

WAY_LABEL = {"approve": "Approve each step", "plan": "Plan first", "auto": "Autonomous", "script": "Write a script"}


def visible(text):
    from .chatwin import visible as v

    return v(text)


class AgentView(object):
    """What the agent's run looks like in the chat window (mixed into ChatWindow)."""

    def _render_static(self, text):
        self._marks = getattr(self, "_marks", 0) + 1
        mark = "r%d" % self._marks
        self.text.mark_set(mark, "end-1c")
        self.text.mark_gravity(mark, "right")
        follow = self.at_end()
        mdview.Renderer(self.text, mark, on_link=self.open_link).render(mdview.parse(text or ""))
        self.text.mark_unset(mark)
        if follow:
            self.text.see("end")

    def _decided(self, holder, label):
        """The buttons give way to what the user decided, as text in the transcript."""
        self._replace_widget(holder["frame"], label)

    def _replace_widget(self, widget, label):
        try:
            idx = self.text.index(str(widget))
            self.text.delete(idx)
            self.text.insert(idx, "  " + label, ("note",))
        except tk.TclError:
            pass
        try:
            widget.destroy()
        except tk.TclError:
            pass

    def agent_start(self, info, verbose):
        self._flush_out()
        self.add_who("AskTim! Agent")
        self.add("Run %d · %s\n" % (info["number"], WAY_LABEL.get(info["mode"], info["mode"])), "step")
        self.note("Working in %s · %s" % (info["folder"], info["model"]))
        if verbose:
            self.note("Safeguards: %s; internet %s; at most %d steps." % (info["sandbox"], "allowed for downloads" if info["network"] else "off", info["max_steps"]))
        if info.get("share_data"):
            self.note("The data filter is off: what the commands print reaches the AI. Stop ends the run at any time.")
        else:
            self.note("The AI never sees your data: what the commands print reaches it with data lines withheld. Stop ends the run at any time.")

    def agent_text(self, text):
        self._flush_out()
        self._render_static(text)

    def agent_step(self, step, kind, show, verdict, ans):
        from .. import ui as _ui

        self._flush_out()
        what = {"command": "run a command", "file": "write a file", "open": "open a file"}.get(kind, kind)
        self.add("Step %d · %s\n" % (step["n"], what), "step")
        if step.get("why"):
            self.add("Why: %s\n" % visible(step["why"]), "note")
        show = visible(show or "")
        if kind == "command":
            self.add("$ " + show.replace("\n", "\n  ") + "\n", "cmd")
        elif kind == "file":
            lines = show.splitlines()
            self.add("File: %s (%d line%s)\n" % (step.get("path"), len(lines), "" if len(lines) == 1 else "s"), "bold")
            self.add("\n".join(lines[:30]) + "\n", "pre")
            if len(lines) > 30:
                self.note("… and %d more lines (Change… shows them all)" % (len(lines) - 30))
        elif kind == "open":
            self.add("Open %s\n" % show, "bold")
        else:
            self.add(show + "\n", "pre")
        confirm = verdict is not None and verdict.level == "confirm"
        if confirm:
            self.warn_box("AskTim! asks you to check this one:", list(verdict.reasons))
        self._out_lines = 0
        if ans is None:
            return
        holder = {}

        def decide(d, label):
            if ans.event.is_set():
                return
            ans.set(d)
            self._decided(holder, label)

        def edit():
            r = ask_text(self.win, self.app, "Change the file" if kind == "file" else "Change the command",
                         "Change it, then press %s. The agent is told what you changed." % ("Write this" if kind == "file" else "Run this"),
                         initial=show, multiline=True, ok="Write this" if kind == "file" else "Run this", note_prompt="A note for the agent (optional):")
            if r is not None:
                decide(_ui.Decision("edit", text=r[0], comment=(r[1] or None)), "You changed it.")

        def refuse():
            why = ask_text(self.win, self.app, "Not this step", "Why not? The agent is told, and has to find another way. (You can leave this empty.)",
                           ok="Don't do it")
            if why is not None:
                decide(_ui.Decision("refuse", comment=why or None), "You refused it." + (" (%s)" % why if why else ""))

        verb = {"file": "Write it", "open": "Open it"}.get(kind, "Run it")
        buttons = [(verb, lambda: decide(_ui.Decision("run"), "You allowed it."), "Accent.TButton")]
        if kind != "open":
            buttons.append(("Change…", edit))
        buttons.append(("Don't…" if kind != "open" else "Don't open it", refuse if kind != "open" else lambda: decide(_ui.Decision("refuse"), "You refused it.")))
        if not confirm and kind != "open":
            buttons.append(("Run all, stop asking", lambda: decide(_ui.Decision("all"), "You allowed it; the next steps run without asking (risky ones still ask).")))
        buttons.append(("Stop the run", lambda: decide(_ui.Decision("stop"), "You stopped the run.")))
        holder["frame"], holder["buttons"] = self.add_buttons(buttons)
        self.set_status_note("The agent waits for you.")
        self.app.set_busy(False)
        self._wake()

    def _wake(self):
        """Bring the window forward: the agent waits for an answer."""
        if not self.visible():
            self.show()

    def agent_blocked(self, step, reasons):
        self._flush_out()
        self.add("Step %d\n" % step["n"], "step")
        if step.get("command"):
            self.add("$ " + visible(step["command"]).replace("\n", "\n  ") + "\n", "cmd_off")
        self.add("Not allowed – %s\n" % visible("; ".join(reasons)), "error")
        self.note("The agent is told why, and has to find another way.")

    def agent_running(self, step):
        self._flush_out()
        self._out_lines = 0
        self.app.set_busy(True)
        self.set_status_note("Step %d is running… (Stop stops it)" % step["n"])

    def agent_result(self, code, secs, timed_out, interrupted, withheld, kept, total, verbose):
        self._flush_out()
        if code == 0:
            self.add("done · %.1f s\n" % secs, "ok")
        else:
            self.add("failed · exit status %s · %.1f s%s\n" % (code, secs, " · stopped after the time limit" if timed_out else " · stopped" if interrupted else ""), "fail")
        if verbose and total:
            self.note("The AI was shown %d of %d lines; %d withheld as data." % (kept, total, withheld) if withheld else "The AI was shown the output (no data found in it).")

    def agent_plan(self, plan, ans):
        self._flush_out()
        self.add("The agent's plan\n", "step")
        self._render_static(plan)
        holder = {}

        def decide(v, label):
            if not ans.event.is_set():
                ans.set(v)
                self._decided(holder, label)

        def changes():
            fb = ask_text(self.win, self.app, "Changes to the plan", "What should change?", ok="Ask for these changes")
            if fb:
                decide(("change", fb), "You asked for changes: %s" % fb)

        holder["frame"], holder["buttons"] = self.add_buttons([
            ("Run the plan", lambda: decide(("approve-auto", None), "You approved the plan."), "Accent.TButton"),
            ("Run it, asking before each step", lambda: decide(("approve-steps", None), "You approved the plan; each step asks first.")),
            ("Ask for changes…", changes),
            ("Cancel", lambda: decide(("cancel", None), "You cancelled the run.")),
        ])
        self.set_status_note("The agent waits for you to read its plan.")
        self.app.set_busy(False)
        self._wake()

    def agent_question(self, text, ans):
        self._flush_out()
        self._render_static(text)
        self.add("The agent asked you something.\n", "note")
        fr = tk.Frame(self.text, bg=self.colors["text_bg"])
        var = tk.StringVar(master=self.win)
        entry = ttk.Entry(fr, textvariable=var, width=46)
        entry.pack(side="left", padx=(0, 6))

        def decide(v, label):
            if not ans.event.is_set():
                ans.set(v)
                self._replace_widget(fr, label)

        b1 = ttk.Button(fr, text="Answer", style="Accent.TButton", command=lambda: decide(var.get().strip(), "You answered: %s" % var.get().strip()) if var.get().strip() else None)
        b1.pack(side="left", padx=(0, 6))
        b2 = ttk.Button(fr, text="End the run", command=lambda: decide("", "You ended the run."))
        b2.pack(side="left")
        entry.bind("<Return>", lambda e: b1.invoke())
        self.text.window_create("end", window=fr, padx=8, pady=3)
        self.text.insert("end", "\n")
        self.text.see("end")
        self.app.set_busy(False)
        self._wake()
        entry.focus_set()

    def agent_report(self, text, outcome):
        self._flush_out()
        self.add("The agent's report%s\n" % ("" if outcome == "done" else " (%s)" % outcome), "step")
        self._render_static(text or "(no report)")

    def agent_finish(self, written, rec):
        from .. import ui as _ui

        if not written:
            return
        full = [os.path.expanduser(w) for w in written]
        run_md = [w for w in full if os.path.basename(w) == "RUN.md"]
        folder = os.path.dirname(run_md[0] if run_md else full[0])
        self.note("AskTim! wrote the record of this run: %s. The report is the model's own words: RUN.md says what really happened."
                  % ", ".join(os.path.basename(w) for w in full))
        buttons = [("Open the run's folder", lambda: _ui.open_path(folder))]
        if run_md:
            buttons.append(("Open RUN.md", lambda: _ui.open_path(run_md[0])))
        self.add_buttons(buttons)

