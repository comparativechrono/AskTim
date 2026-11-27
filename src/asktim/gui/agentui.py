"""The agent's side of the window: the methods that agent.py calls on its UI (as on the terminal's
and the notebook's), from the agent's own thread. Each one hands the news to the window; the ones
that need an answer (approve a step, a plan, a question) wait for the user's click."""

from .. import ui as _ui
from ..agent import Stop
from ..ui import Decision
from .app import Answer


class GuiAgentUI(object):
    interactive = True

    def __init__(self, chat, stop_event, verbose=False):
        self.chat = chat
        self.post = chat.app.bridge.post
        self.stop_event = stop_event
        self.verbose = verbose
        self.agent_mode = True

    def _check(self):
        if self.stop_event.is_set():
            raise Stop()

    # -- general ------------------------------------------------------------------------------

    def note(self, text):
        if self.verbose:
            self.post(self.chat.note, text)

    def warn(self, text):
        self.post(self.chat.add, text.rstrip() + "\n", "warn")

    def error(self, text):
        self.post(self.chat.add, text.rstrip() + "\n", "error")

    def status(self, text):
        self.post(self.chat.set_status_note, text)

    def data_off_reminder(self, cfg):
        if _ui.data_off_reminder_due(cfg):
            self.post(self.chat.warn_box, "The data filter is off", [_ui.DATA_OFF_REMINDER])

    # -- the model's text (held until it is known what it is: a step's reason, a plan, a report) -----

    def thinking(self):
        self._check()
        self.post(self.chat.set_status_note, "The agent is thinking…")

    def stream_text(self, delta):
        self._check()

    def end_text(self):
        self._check()

    def agent_says(self, text):
        if (text or "").strip():
            self.post(self.chat.agent_text, text)

    # -- steps ----------------------------------------------------------------------------------

    def start(self, info):
        self.post(self.chat.agent_start, info, self.verbose)

    def show_step(self, step, kind, show, verdict, asking=False):
        self._check()
        self.post(self.chat.agent_step, dict(step), kind, show, verdict, None)

    def approve(self, step, kind, show, verdict):
        self._check()
        ans = Answer()
        self.post(self.chat.agent_step, dict(step), kind, show, verdict, ans)
        d = ans.wait(self.stop_event)
        return d if d is not None else Decision("stop")

    def blocked(self, step, verdict):
        self.post(self.chat.agent_blocked, dict(step), list(verdict.reasons))

    def running(self, step):
        self._check()
        self.post(self.chat.agent_running, dict(step))

    def output(self, stream, text):
        self.post(self.chat._queue_out, text)

    def result(self, step, res, fr):
        self.post(self.chat.agent_result, res.exit_code, res.duration, res.timed_out, res.interrupted,
                  getattr(fr, "withheld", 0), getattr(fr, "kept", 0), getattr(fr, "total", 0), self.verbose)

    def inspected(self, step, desc):
        if self.verbose:
            self.post(self.chat.note, "Step %d: AskTim! described %s to the AI (its structure, not its contents): %s" % (step["n"], step.get("path"), desc))

    def ask_stop(self):
        # (the command was stopped by the window's Stop button, which is the answer)
        return self.stop_event.is_set()

    def plan_review(self, plan):
        ans = Answer()
        self.post(self.chat.agent_plan, plan, ans)
        v = ans.wait(self.stop_event)
        return v if v is not None else ("cancel", None)

    def reply_to_question(self, text):
        ans = Answer()
        self.post(self.chat.agent_question, text, ans)
        v = ans.wait(self.stop_event)
        return v or ""

    def open_file(self, path, program=None):
        return _ui.open_path(path, program)

    def report(self, text, outcome):
        self.post(self.chat.agent_report, text, outcome)

    def finish(self, written, rec):
        self.post(self.chat.agent_finish, list(written), {k: rec.get(k) for k in ("outcome", "work_dir", "number", "mode")})
