"""AskTim! Agent: the model works through tools, one step at a time, with the student watching.

The ways of working are those of the AI agents practical:

    approve   (default) nothing runs until the student allows it; they can change or refuse a step
    plan      the agent writes a plan first; the student approves it (or asks for changes), then it works
    auto      the agent works alone; the student watches and can press Ctrl+C
    script    the agent writes one script and gets it to run

Whatever the mode, AskTim! - not the model - decides what may run (safety.py), runs it
(runner.py, in a sandbox where possible), decides what the model may see of the output
(privacy.py), and writes the record of the run (records.py).
"""

import json
import os
import re
import time

from . import config as _config
from . import context, fileinfo, history, inventory, logbook, paths, privacy, prompts, records, runner, safety, viewers
from .providers import Conversation, ProviderError, ToolResult

QUESTION_END = re.compile(r"\?\s*(\*\*|_|\))?\s*$")


class Stop(Exception):
    """The student stopped the run."""


class Agent(object):
    def __init__(self, cfg, provider, ui, task, mode="approve", here=False, allow_network=None, max_steps=None, timeout=None,
                 script_name="analysis.sh", notebook=None, student_dir=None):
        self.cfg = cfg
        self.provider = provider
        self.ui = ui
        self.task = task.strip()
        self.mode = mode if mode in ("approve", "plan", "auto", "script") else "approve"
        self.here = here
        self.allow_network = bool(cfg.get("agent.allow_network")) if allow_network is None else bool(allow_network)
        if cfg.is_locked("agent.allow_network"):
            self.allow_network = bool(cfg.get("agent.allow_network"))
        self.max_steps = self._limit(cfg, "agent.max_steps", max_steps, 25)
        self.timeout = self._limit(cfg, "agent.command_timeout", timeout, 600)
        self.out_chars = int(cfg.get("agent.output_chars") or 4000)
        self.script_name = script_name
        self.nb = notebook  # the notebook side (notebook.NotebookTools), or None at the command line
        self.strict = cfg.strict
        self.student_dir = os.path.realpath(student_dir or os.getcwd())
        self.ask_each = self.mode == "approve"
        self.withheld = 0
        self.steps = []
        self.models = []

    # -- set-up ---------------------------------------------------------------------------------

    def _prepare(self):
        runs_dir = os.path.join(self.student_dir, self.cfg.get("agent.runs_dir") or "asktim-runs")
        n = records.next_run_number(runs_dir)
        self.number = n
        self.record_dir = os.path.join(runs_dir, "%d-%s" % (n, self.mode))
        paths.ensure(self.record_dir)
        self.work_dir = self.student_dir if self.here else self.record_dir
        self.sandbox, why_not = (None, "you asked the agent to work in your folder (--here)") if self.here else runner.make_sandbox(
            self.cfg.get("agent.sandbox") or "auto", self.work_dir, self.student_dir, self.allow_network)
        self.sandbox_why = why_not
        self.sandbox_note = self.sandbox.describe() if self.sandbox else "no sandbox (%s): AskTim! checks each command, and reports files that change outside the agent's folder" % why_not
        self.protect = None
        if self.here:
            self.sandbox_note = "no sandbox: the agent works in your folder (--here); AskTim! checks each command and asks before it changes a file that is already there"
            snap = runner.snapshot(self.student_dir, self.record_dir)
            self.protect = set(snap) if snap is not None else True

    def _rel(self, p):
        try:
            r = os.path.relpath(p, self.work_dir)
        except ValueError:
            return p
        return r if not r.startswith("../../../..") else paths.pretty(p)

    def _first_message(self):
        g = context.Gathered()
        task, _n, pasted = privacy.clean_question(self.task, strict=self.strict)  # data pasted into the task are withheld too
        self.withheld += pasted
        lines = ["The student's task:", task, ""]
        listing = fileinfo.listing(self.student_dir, max_entries=60, strict=self.strict)
        txt, n = privacy.clean(listing, strict=self.strict)
        lines += ["The files in the student's folder (%s, which is %s from your folder):" % (paths.pretty(self.student_dir), self._rel(self.student_dir) or "."), txt, ""]
        # files that the task names: describe their structure
        for word in re.findall(r"[\w./~\-]+\.[A-Za-z0-9]{1,8}(?:\.gz)?", self.task):
            for base in (self.student_dir, self.work_dir):
                p = os.path.join(base, os.path.expanduser(word))
                if os.path.exists(p) and not os.path.isdir(p):
                    d, _ = privacy.clean(fileinfo.describe(p, strict=self.strict, base=self.work_dir), strict=self.strict)
                    lines.append("About a file the task names: " + d)
                    break
        n_hist = self.cfg.get("history_lines")
        n_hist = 10 if n_hist is None else min(10, int(n_hist))
        if self.cfg.get("send_history", True) and n_hist > 0:
            cmds, _src = history.recent(n_hist)
            if cmds:
                lines += ["", "The student's last shell commands (for context; oldest first):"]
                for c in cmds:
                    t, _ = privacy.clean(c.line, strict=self.strict)
                    lines.append("  $ %s%s" % (t, "   [exit %d]" % c.status if c.status else ""))
        lines += ["", viewers.describe()]
        if self.nb is not None:
            lines += ["", self.nb.state_text()]
        if self.mode == "plan":
            lines += ["", "Write your plan now (no tool calls)."]
        return "\n".join(lines)

    # -- the run --------------------------------------------------------------------------------

    @staticmethod
    def _limit(cfg, name, asked, default):
        """A limit from --steps/--timeout, never above the course's value when the course has locked it."""
        base = int(cfg.get(name) or default)
        try:
            asked = int(asked) if asked else None
        except (TypeError, ValueError):
            asked = None
        if asked is None or asked < 1:
            return base
        return min(asked, base) if cfg.is_locked(name) else asked

    def run(self):
        self._prepare()
        if (self.cfg.get("agent.sandbox") or "auto") == "on" and self.sandbox is None:
            # the course requires the sandbox: without it, the agent does not start
            why = "--here works in your folder without one" if self.here else self.sandbox_why
            self.ui.error("The agent can only run in its sandbox here (the settings say \"sandbox\": \"on\"), and there is none: %s." % why)
            for d in (self.record_dir, os.path.dirname(self.record_dir)):
                try:
                    os.rmdir(d)  # only if empty
                except OSError:
                    pass
            return {"outcome": "no sandbox", "steps": [], "task": self.task, "mode": self.mode}
        inv = inventory.gather()
        inv_lines = inventory.describe(inv, notebook_packages=self.nb.packages() if self.nb is not None else None)
        system = prompts.agent_system(inv_lines, self.cfg, self.mode, self._rel(self.work_dir) or ".", self._rel(self.student_dir) or ".",
                                      notebook=self.nb is not None, sandbox_note=(self.sandbox_note + ".") if self.sandbox else "", script_name=self.script_name)
        tools = prompts.tool_specs(notebook=self.nb is not None)
        self.conv = Conversation()
        self.conv.user(self._first_message())
        rec = {
            "number": self.number, "mode": self.mode, "task": self.task, "started": records.now(), "work_dir": self.work_dir,
            "work_dir_shown": paths.pretty(self.work_dir), "student_dir": paths.pretty(self.student_dir), "here": self.here,
            "sandbox": self.sandbox_note, "allow_network": self.allow_network, "privacy": self.cfg.get("privacy"),
            "provider": self.provider.name, "steps": self.steps, "notebook": self.nb is not None,
        }
        self.ui.start({"number": self.number, "mode": self.mode, "task": self.task, "folder": paths.pretty(self.work_dir),
                       "sandbox": self.sandbox_note, "model": self.provider.describe(), "network": self.allow_network, "max_steps": self.max_steps})
        outcome = "done"
        report = ""
        try:
            if self.mode == "plan":
                self._plan_phase(system, tools, rec)
            report = self._loop(system, tools)
        except Stop:
            outcome = "stopped by the student"
            report = "(The student stopped the run.)"
        except KeyboardInterrupt:
            outcome = "stopped by the student"
            report = "(The student stopped the run with Ctrl+C.)"
        except ProviderError as e:
            outcome = "error"
            report = "(The run ended because of an error of the AI service: %s)" % e
            self.ui.error(str(e))
        finally:
            if self.sandbox is not None:
                self.sandbox.cleanup()
        if outcome == "done" and len(self.steps) >= self.max_steps:
            outcome = "step limit reached"
        rec.update({"ended": records.now(), "outcome": outcome, "report": report, "models": self.models, "withheld": self.withheld})
        written = records.write(rec, self.record_dir)
        if self.nb is not None:
            self.nb.finish_run(rec)
        self.ui.report(report, outcome)
        self.ui.finish([paths.pretty(p) for p in written], rec)
        logbook.add({"mode": "agent-" + self.mode, "question": privacy.clean(self.task, strict=self.strict)[0], "model": ", ".join(self.models), "provider": self.provider.name,
                     "outcome": outcome, "folder": paths.pretty(self.record_dir), "steps": len(self.steps), "cwd": paths.pretty(self.student_dir)}, self.cfg)
        return rec

    def _ask_model(self, system, tools):
        self.ui.thinking()
        turn = self.provider.complete(system, self.conv, tools=tools, purpose="agent", on_text=self.ui.stream_text, on_status=self.ui.status)
        self.ui.end_text()
        if turn.tool_calls:
            self.ui.agent_says(turn.text)
        if turn.model and turn.model not in self.models:
            self.models.append(turn.model)
        self.conv.assistant(turn)
        return turn

    def _plan_phase(self, system, tools, rec):
        rec["plan_rounds"] = []
        rec["plan_feedback"] = []
        nudges = 0
        while True:
            turn = self._ask_model(system, tools)
            if turn.tool_calls:
                nudges += 1
                self.conv.results([ToolResult(c.id, c.name, "Not run: write the plan first. Tools can be used once the student has approved it.", True) for c in turn.tool_calls],
                                  text="Send the plan now, without tool calls." if nudges < 3 else None)
                if nudges >= 3:
                    raise ProviderError("The model kept calling tools instead of writing a plan.", kind="other")
                continue
            plan = turn.text.strip()
            rec["plan"] = plan
            rec["plan_rounds"].append(plan)
            choice, feedback = self.ui.plan_review(plan)
            if choice == "cancel":
                raise Stop()
            if choice == "change":
                rec["plan_feedback"].append(feedback)
                self.conv.user("The student asks for changes to the plan: %s\nSend the whole plan again, changed, without tool calls." % feedback)
                continue
            self.ask_each = choice == "approve-steps"
            self.conv.user("The student approved the plan%s. Carry it out now, step by step." % (" and will approve each step" if self.ask_each else ""))
            return

    def _loop(self, system, tools):
        asked_for_script = False
        nudges = 0
        while True:
            if len(self.steps) >= self.max_steps:
                self.conv.user("You have used all %d steps. Write your report now, without tool calls: what you did, the files you made, the result as far as "
                               "you know it, and what is not finished." % self.max_steps)
                turn = self._ask_model(system, tools)
                if turn.tool_calls:
                    self.conv.results([ToolResult(c.id, c.name, "Not run: the step limit is reached.", True) for c in turn.tool_calls], text="Write the report now.")
                    turn = self._ask_model(system, tools)
                return turn.text
            turn = self._ask_model(system, tools)
            if not turn.tool_calls:
                if turn.stop_reason == "max_tokens" and nudges < 2:
                    nudges += 1
                    self.conv.user("Your reply was cut off (it was too long). Go on, in shorter steps.")
                    continue
                if turn.stop_reason == "malformed" and nudges < 2:
                    nudges += 1
                    self.conv.user("Your last tool call could not be read. Send it again.")
                    continue
                if self.mode == "script" and not asked_for_script and not os.path.exists(os.path.join(self.work_dir, self.script_name)) and len(self.steps) < self.max_steps - 1:
                    asked_for_script = True
                    self.conv.user("There is no %s in your folder. The student asked for one script that does the whole task. Write it now with write_file, "
                                   "run it, and report when it has run without an error." % self.script_name)
                    continue
                if QUESTION_END.search(turn.text or "") and self.ui.interactive and self.mode != "auto":
                    reply = self.ui.reply_to_question(turn.text)
                    if reply:
                        self.conv.user(reply)
                        continue
                return turn.text
            nudges = 0
            results = []
            for call in turn.tool_calls:
                if len(self.steps) >= self.max_steps:
                    results.append(ToolResult(call.id, call.name, "Not run: the step limit is reached.", True))
                    continue
                results.append(self._handle(call))
            left = self.max_steps - len(self.steps)
            note = "(%d step%s left.)" % (left, "" if left == 1 else "s") if 0 < left <= 3 else None
            self.conv.results(results, text=note)

    # -- one tool call ------------------------------------------------------------------------

    def _handle(self, call):
        res = self._handle_inner(call)
        # a last line of defence: nothing goes back to the model without the identity and secret cleaning
        res.content, _ = privacy.clean(res.content or "", strict=self.strict)
        return res

    def _handle_inner(self, call):
        n = len(self.steps) + 1
        step = {"n": n, "tool": call.name, "why": str(call.input.get("why") or "").strip(), "input": {k: v for k, v in call.input.items() if k not in ("content", "code")}}
        self.steps.append(step)
        if "_incomplete" in call.input:
            step["decision"] = "blocked"
            step["blocked"] = ["the request was cut off"]
            self.ui.warn("The agent's request was cut off; it is asked to send it again.")
            return ToolResult(call.id, call.name, "Your tool call was cut off before it was complete. Send it again (shorter).", True)
        try:
            if call.name == "run_command":
                return self._run_command(call, step)
            if call.name == "write_file":
                return self._write_file(call, step)
            if call.name == "inspect_file":
                return self._inspect(call, step)
            if call.name == "open_file":
                return self._open(call, step)
            if self.nb is not None and call.name in ("run_python", "describe_variable", "add_markdown_cell", "add_code_cell"):
                return self.nb.handle(self, call, step)
        except Stop:
            raise
        step["decision"] = "blocked"
        step["blocked"] = ["unknown tool"]
        return ToolResult(call.id, call.name, "There is no tool called %s." % call.name, True)

    def decide(self, step, kind, verdict, show):
        """Ask the student when needed. Returns (decision, new_text_or_None, comment)."""
        if verdict is not None and verdict.level == "block":
            step["decision"] = "blocked"
            step["blocked"] = list(verdict.reasons)
            self.ui.blocked(step, verdict)
            return "blocked", None, None
        must_ask = self.ask_each or (verdict is not None and verdict.level == "confirm") or kind == "open"
        if not must_ask:
            self.ui.show_step(step, kind, show, verdict, asking=False)
            step["decision"] = "auto"
            return "auto", None, None
        if not self.ui.interactive:
            step["decision"] = "refuse"
            step["comment"] = "nobody could be asked (no terminal)"
            return "refuse", None, "Nobody is at the keyboard to approve this step, so it was not run."
        d = self.ui.approve(step, kind, show, verdict)
        if d.choice == "stop":
            raise Stop()
        if d.choice == "all":
            self.ask_each = False
            step["decision"] = "all"
            return "run", None, d.comment
        step["decision"] = {"run": "run", "edit": "edit", "refuse": "refuse"}[d.choice]
        if d.comment:
            step["comment"] = d.comment
        return d.choice, d.text, d.comment

    def _run_command(self, call, step):
        cmd = str(call.input.get("command") or "").replace("\r\n", "\n").strip()
        step["command"] = cmd
        verdict = safety.check(cmd, self.work_dir, allow_network=self.allow_network, student_dir=self.student_dir, protect=self.protect)
        decision, new, comment = self.decide(step, "command", verdict, cmd)
        if decision == "blocked":
            return ToolResult(call.id, call.name, "Not run: AskTim! does not allow this command – %s." % "; ".join(verdict.reasons), True)
        if decision == "refuse":
            return ToolResult(call.id, call.name, "Not run: the student refused this step." + (" Their reason: %s" % comment if comment else ""), True)
        prefix = ""
        if decision == "edit" and new is not None and new.strip() != cmd:
            step["original"] = cmd
            cmd = new.strip()
            step["command"] = cmd
            verdict = safety.check(cmd, self.work_dir, allow_network=self.allow_network, student_dir=self.student_dir, protect=self.protect)
            if verdict.level == "block":
                step["decision"] = "blocked"
                step["blocked"] = list(verdict.reasons)
                self.ui.blocked(step, verdict)
                return ToolResult(call.id, call.name, "The student changed the command, but AskTim! does not allow the new one either – %s." % "; ".join(verdict.reasons), True)
            prefix = "The student changed the command before it ran; this ran instead:\n%s\n" % cmd + ("Their comment: %s\n" % comment if comment else "")
        elif comment:
            prefix = "The student allowed this step, with a comment: %s\n" % comment
        before = None if self.sandbox else runner.snapshot(self.student_dir, self.record_dir)
        self.ui.running(step)
        res = runner.run(cmd, self.work_dir, timeout=self.timeout, sandbox=self.sandbox, on_output=self.ui.output)
        if res.interrupted:
            step["interrupted"] = True
            if self.ui.ask_stop():
                step["exit"] = res.exit_code
                raise Stop()
        after = None if before is None else runner.snapshot(self.student_dir, self.record_dir)
        ch = runner.changes(before, after)
        step.update({"exit": res.exit_code, "seconds": round(res.duration, 2), "timed_out": res.timed_out, "lines": res.lines})
        raw = res.text()
        step["output_tail"] = privacy.mask_identity(res.last_lines(30))
        fr = privacy.filter_output(raw, kind=verdict.output_kind, strict=self.strict)
        self.withheld += fr.withheld
        step["withheld"] = fr.withheld
        body = privacy.shorten(fr.text, self.out_chars)
        parts = [prefix + "exit status %s (%.1f s, %d line%s of output%s)" % (res.exit_code, res.duration, res.lines, "" if res.lines == 1 else "s",
                 "; %d withheld as data" % fr.withheld if fr.withheld else "")]
        if res.error:
            parts.append(res.error)
        if res.timed_out:
            parts.append("The command was stopped: it ran longer than %d seconds. Work on less at a time, or split the work into steps." % self.timeout)
        if res.interrupted:
            parts.append("The student stopped this command with Ctrl+C.")
        if body.strip():
            parts.append("Output (stdout and stderr together):\n" + body)
        else:
            parts.append("It printed nothing.")
        if ch and self.here:
            # in the student's folder, new files are expected; changes to their own files are reported
            own = [paths.pretty(p) for p in ch["changed"] + ch["deleted"]]
            if own:
                step["outside_changes"] = own
                parts.append("Note: this step changed or deleted files that were already in the student's folder: %s." % ", ".join(own[:10]))
                self.ui.warn("This step changed or deleted files that were already in your folder: %s" % ", ".join(own[:6]))
        elif ch:
            outside = [paths.pretty(p) for p in ch["created"] + ch["changed"] + ch["deleted"]]
            if outside:
                step["outside_changes"] = outside
                parts.append("Warning: files outside your folder changed during this step: %s. Write only inside your folder." % ", ".join(outside[:10]))
                self.ui.warn("Files outside the agent's folder changed during this step: %s" % ", ".join(outside[:6]))
        if verdict.notes:
            parts.append("Note: " + "; ".join(verdict.notes))
        sent = "\n".join(parts)
        step["sent"] = sent
        self.ui.result(step, res, fr)
        return ToolResult(call.id, call.name, sent, res.exit_code != 0)

    def _write_file(self, call, step):
        path = str(call.input.get("path") or "").strip()
        content = call.input.get("content")
        if not isinstance(content, str):
            content = "" if content is None else json.dumps(content, indent=2)
        step["path"] = path
        step["content"] = content
        verdict, full = safety.check_file_write(path, self.work_dir, content, student_dir=None if self.here else self.student_dir, protect=self.protect)
        decision, new, comment = self.decide(step, "file", verdict, content)
        if decision == "blocked":
            return ToolResult(call.id, call.name, "Not written: %s." % "; ".join(verdict.reasons), True)
        if decision == "refuse":
            return ToolResult(call.id, call.name, "Not written: the student refused this step." + (" Their reason: %s" % comment if comment else ""), True)
        if decision == "edit" and new is not None:
            step["original"] = "(the file as the agent wrote it)"
            content = new
            step["content"] = content
        try:
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            existed = os.path.exists(full)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            if full.endswith((".sh", ".bash")):
                os.chmod(full, 0o755)
        except OSError as e:
            return ToolResult(call.id, call.name, "Could not write %s: %s" % (path, e), True)
        step["written"] = True
        lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        msg = "%s %s (%d line%s, %d bytes)." % ("Replaced" if existed else "Wrote", self._rel(full), lines, "" if lines == 1 else "s", len(content.encode("utf-8")))
        if decision == "edit":
            msg = "The student changed the file before it was written. " + msg + " It now holds:\n" + privacy.shorten(content, 3000)
        if comment:
            msg += " The student's comment: %s" % comment
        if verdict.notes:
            msg += " Note: " + "; ".join(verdict.notes)
        step["sent"] = msg
        self.ui.note("  " + msg.split(" It now holds")[0])
        return ToolResult(call.id, call.name, msg)

    def _resolve_read(self, path):
        p = os.path.expanduser(path)
        if not os.path.isabs(p):
            p = os.path.join(self.work_dir, p)
        p = os.path.realpath(p)
        ctx = safety.Context(self.work_dir, student_dir=self.student_dir)
        return p, safety._sensitive(p, ctx)

    def _inspect(self, call, step):
        path = str(call.input.get("path") or "").strip()
        p, sens = self._resolve_read(path)
        step["path"] = path
        if sens:
            step["decision"] = "blocked"
            step["blocked"] = ["%s holds credentials or private history" % sens]
            return ToolResult(call.id, call.name, "Not allowed: %s holds credentials or private history." % sens, True)
        desc = fileinfo.describe(p, strict=self.strict, base=self.work_dir)
        desc, _ = privacy.clean(desc, strict=self.strict)
        step["decision"] = "auto"
        step["sent"] = desc
        self.ui.inspected(step, desc)
        return ToolResult(call.id, call.name, desc)

    def _open(self, call, step):
        path = str(call.input.get("path") or "").strip()
        program = str(call.input.get("program") or "").strip() or None
        p, sens = self._resolve_read(path)
        step["path"] = path
        if program:
            step["program"] = program
        if sens:
            step["decision"] = "blocked"
            step["blocked"] = ["%s holds credentials" % sens]
            return ToolResult(call.id, call.name, "Not allowed.", True)
        if not os.path.exists(p):
            return ToolResult(call.id, call.name, "There is no file %s." % path, True)
        if program:
            argv, why_not = viewers.find(program)
            shows, no_screen = viewers.can_show_windows()
            if argv is None or not shows:
                return ToolResult(call.id, call.name, "Cannot open it in %s: %s. Installed programs with windows: %s." % (
                    program, why_not or no_screen, ", ".join(viewers.installed()) or "none"), True)
        show = paths.pretty(p) + (" in %s" % program if program else "")
        decision, _new, comment = self.decide(step, "open", None, show)
        if decision in ("refuse", "blocked"):
            return ToolResult(call.id, call.name, "Not opened: the student said no." + (" Their reason: %s" % comment if comment else ""), True)
        ok, how = self.ui.open_file(p, program=program)
        step["exit"] = 0 if ok else 1
        msg = ("Opened %s for the student (%s). Nothing of it was sent to you." % (self._rel(p), how)) if ok else ("Could not open it: %s" % how)
        step["sent"] = msg
        return ToolResult(call.id, call.name, msg, not ok)
