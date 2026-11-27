"""The chat window: questions and answers, the commands of an answer, and the agent.

Under each block of commands in an answer are Run and Copy. Run shows the commands, with what
AskTim!'s checks say about them, and runs them in the window's folder when the user says so: one
after the other, stopping at the first that fails. A failed command offers "Ask AskTim! about
this error". In Agent mode the same window gives a task to AskTim!'s agent, in one of the ways of
working of the practicals; its steps, approvals and report appear here.
"""

import os
import re
import shutil
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import ttk

from .. import config as _config
from .. import paths, privacy
from ..providers import ProviderError
from . import backend, mdview
from .agentview import AgentView
from .dialogs import RunDialog

WAYS = [("approve", "Approve each step"), ("plan", "Plan first"), ("auto", "Autonomous"), ("script", "Write a script")]
WAY_LABEL = dict(WAYS)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def visible(text):
    """Text for the window: without terminal colour codes, and a progress bar's last state of each line."""
    text = privacy.ANSI.sub("", text or "")
    if "\r" in text:
        text = "\n".join(ln.rstrip("\r").split("\r")[-1] for ln in text.split("\n"))
    return _CONTROL.sub("", text)


class ChatWindow(AgentView):
    def __init__(self, app):
        self.app = app
        self.colors = app.colors
        self.fonts = app.fonts
        self.win = tk.Toplevel(app.root)
        self.win.title("AskTim!")
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.minsize(440, 420)
        self.mode = tk.StringVar(master=self.win, value=app.state.get("mode") if app.state.get("mode") in ("chat", "agent") else "chat")
        cfg = _config.Config()
        self.way = tk.StringVar(master=self.win, value=WAY_LABEL.get(app.state.get("agent_way"), WAY_LABEL["approve"]))
        self.web = tk.BooleanVar(master=self.win, value=bool(cfg.get("agent.allow_network")))
        self.here = tk.BooleanVar(master=self.win, value=False)
        self.busy = None  # "answer" | "run" | "agent"
        self.stop_event = threading.Event()
        self.answers = 0
        self._streams = {}
        self._out = []  # output waiting to be shown
        self._out_lines = 0
        self._last_run = None
        self._build()
        geo = app.state.get("chat_geometry")
        if geo and re.match(r"^\d+x\d+[+-]-?\d+[+-]-?\d+$", geo):
            self.win.geometry(geo)
        else:
            self.win.geometry("600x740")
        self.on_mode(save=False)
        self.refresh_status()
        self.greet()

    # -- the window -----------------------------------------------------------------------------

    def _build(self):
        c, f, w = self.colors, self.fonts, self.win
        w.configure(bg=c["window"])
        w.columnconfigure(0, weight=1)
        w.rowconfigure(1, weight=1)

        top = ttk.Frame(w, padding=(10, 8, 10, 6))
        top.grid(row=0, column=0, sticky="ew")
        seg = ttk.Frame(top)
        seg.pack(side="left")
        self.rb_chat = ttk.Radiobutton(seg, text="Chat", value="chat", variable=self.mode, style="Toolbutton", command=self.on_mode)
        self.rb_agent = ttk.Radiobutton(seg, text="Agent", value="agent", variable=self.mode, style="Toolbutton", command=self.on_mode)
        self.rb_chat.pack(side="left")
        self.rb_agent.pack(side="left", padx=(2, 0))
        self.settings_btn = ttk.Button(top, text="Settings", style="Small.TButton", command=self.app.open_settings)
        self.settings_btn.pack(side="right")
        self.folder_btn = ttk.Button(top, style="Small.TButton", command=self.app.choose_folder)
        self.folder_btn.pack(side="right", padx=(0, 6))
        ttk.Label(top, text="Folder:", style="Muted.TLabel").pack(side="right", padx=(0, 4))

        mid = tk.Frame(w, bg=c["text_bg"], highlightthickness=1, highlightbackground=c["border"], bd=0)
        mid.grid(row=1, column=0, sticky="nsew", padx=10)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        t = self.text = tk.Text(mid, wrap="word", bd=0, padx=14, pady=10, highlightthickness=0, bg=c["text_bg"], fg=c["fg"],
                                font=f["body"], cursor="arrow", spacing1=1, spacing3=2, insertwidth=0, undo=False)
        t.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(mid, command=self._scrollbar)
        sb.grid(row=0, column=1, sticky="ns")
        t.config(yscrollcommand=sb.set)
        # the view follows what is added, unless the user has scrolled up to read something
        self._follow = True
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Prior>", "<Next>", "<Up>", "<Down>"):
            t.bind(ev, self._scrolled, add=True)
        # read-only, but text can be selected and copied
        t.bind("<Key>", self._readonly)
        for ev in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<<PasteSelection>>"):
            t.bind(ev, lambda e: "break")
        self._tags()

        self.agent_bar = ttk.Frame(w, padding=(10, 6, 10, 0))
        ttk.Label(self.agent_bar, text="Way of working:").pack(side="left")
        self.way_box = ttk.Combobox(self.agent_bar, state="readonly", width=19, textvariable=self.way, values=[l for _k, l in WAYS])
        self.way_box.pack(side="left", padx=(4, 10))
        self.web_box = ttk.Checkbutton(self.agent_bar, text="Allow downloads", variable=self.web)
        self.web_box.pack(side="left")
        self.here_box = ttk.Checkbutton(self.agent_bar, text="Work in my folder", variable=self.here)
        self.here_box.pack(side="left", padx=(10, 0))

        bottom = ttk.Frame(w, padding=(10, 8, 10, 4))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.entry = tk.Text(bottom, height=3, wrap="word", font=f["body"], bd=0, relief="flat", highlightthickness=1,
                             highlightbackground=c["border"], highlightcolor=c["accent"], padx=8, pady=6, undo=True, bg=c["text_bg"], fg=c["fg"])
        self.entry.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        self.entry.bind("<FocusIn>", lambda e: self._hint(False))
        self.entry.bind("<FocusOut>", lambda e: self._hint(True))
        self.entry.tag_configure("hint", foreground=c["muted"])
        self.send_btn = ttk.Button(bottom, text="Ask", style="Accent.TButton", command=self.send, width=8)
        self.send_btn.grid(row=0, column=1, sticky="new", padx=(8, 0))
        self.stop_btn = ttk.Button(bottom, text="Stop", command=self.stop, width=8, state="disabled")
        self.stop_btn.grid(row=1, column=1, sticky="new", padx=(8, 0), pady=(4, 0))

        st = ttk.Frame(w, padding=(10, 0, 10, 6))
        st.grid(row=4, column=0, sticky="ew")
        self.badge = tk.Label(st, text=" Data filter OFF ", bg=c["fail"], fg="white", font=f["small_bold"], cursor="hand2")
        self.badge.bind("<Button-1>", lambda e: self.app.open_settings("privacy"))
        self.status = ttk.Label(st, style="Status.TLabel")
        self.status.pack(side="left")
        self.new_btn = ttk.Button(st, text="New conversation", style="Link.TButton", command=self.new_conversation)
        self.new_btn.pack(side="right")
        self._hint(True)

    def _tags(self):
        c, f, t = self.colors, self.fonts, self.text
        t.tag_configure("who", font=f["small_bold"], foreground=c["muted"], spacing1=10)
        t.tag_configure("who_ai", font=f["small_bold"], foreground=c["accent"], spacing1=10)
        t.tag_configure("user", background=c["user_bg"], lmargin1=8, lmargin2=8, rmargin=8, spacing1=4, spacing3=4)
        t.tag_configure("gap", font=f["small"], spacing1=0, spacing3=0)
        t.tag_configure("h1", font=f["h1"], spacing1=6, spacing3=2)
        t.tag_configure("h2", font=f["h2"], spacing1=6, spacing3=2)
        t.tag_configure("h3", font=f["h3"], spacing1=4, spacing3=2)
        t.tag_configure("bold", font=f["bold"])
        t.tag_configure("italic", font=f["italic"])
        t.tag_configure("icode", font=f["mono"], background=c["code_bg"])
        t.tag_configure("pre", font=f["mono"], background=c["code_bg"], lmargin1=14, lmargin2=14, rmargin=8, spacing1=0, spacing3=0)
        t.tag_configure("quote", foreground=c["muted"], lmargin1=14, lmargin2=14)
        for d in range(5):
            t.tag_configure("li%d" % d, lmargin1=8 + 18 * d, lmargin2=22 + 18 * d)
        t.tag_configure("link", foreground=c["link"], underline=True)
        t.tag_configure("muted", foreground=c["muted"], font=f["small"])
        t.tag_configure("note", foreground=c["muted"], font=f["small"], spacing1=2)
        t.tag_configure("cmd", font=f["mono_bold"], foreground="#0b5d1e", lmargin1=8, lmargin2=20, spacing1=6)
        t.tag_configure("cmd_off", font=f["mono"], foreground=c["muted"], lmargin1=8, lmargin2=20, spacing1=4)
        t.tag_configure("out", font=f["mono"], background=c["out_bg"], foreground=c["out_fg"], lmargin1=14, lmargin2=14, rmargin=8,
                        spacing1=0, spacing3=0)
        t.tag_configure("ok", foreground=c["ok"], font=f["small_bold"], lmargin1=8)
        t.tag_configure("fail", foreground=c["fail"], font=f["small_bold"], lmargin1=8)
        t.tag_configure("error", foreground=c["fail"])
        t.tag_configure("warn", background=c["warn_bg"], foreground=c["warn_fg"], lmargin1=8, lmargin2=8, rmargin=8, spacing1=3, spacing3=3)
        t.tag_configure("step", font=f["bold"], spacing1=10, foreground="#3d2a96")
        t.tag_configure("card", lmargin1=8, lmargin2=8)
        t.tag_raise("link")

    def _readonly(self, e):
        ctrl = e.state & 0x4 or e.state & 0x8 or e.state & 0x10  # Control, Alt/Meta, Command
        if ctrl and e.keysym.lower() in ("c", "a", "insert"):
            return None
        if e.keysym in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
            return None
        return "break"

    def _hint(self, show):
        e = self.entry
        has_hint = bool(e.tag_ranges("hint"))
        content = e.get("1.0", "end-1c")
        if show and not content.strip():
            e.delete("1.0", "end")
            e.insert("1.0", "Describe a task for the agent…" if self.mode.get() == "agent" else "Ask a question…", "hint")
        elif not show and has_hint:
            e.delete("1.0", "end")

    def _entry_text(self):
        if self.entry.tag_ranges("hint"):
            return ""
        return self.entry.get("1.0", "end-1c").strip()

    def _on_return(self, e):
        if e.state & 0x1:  # Shift+Enter: a new line
            return None
        self.send()
        return "break"

    # -- showing and hiding ------------------------------------------------------------------

    def visible(self):
        try:
            return self.win.winfo_viewable() and self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    def show(self, mode=None):
        if mode in ("chat", "agent") and not self.busy:
            self.mode.set(mode)
            self.on_mode()
        self.win.deiconify()
        self.win.lift()
        try:
            self.win.focus_force()
            self.entry.focus_set()
        except tk.TclError:
            pass

    def hide(self):
        try:
            self.app.remember(chat_geometry=self.win.geometry())
        except tk.TclError:
            pass
        self.win.withdraw()

    def on_mode(self, save=True):
        agent = self.mode.get() == "agent"
        if agent:
            self.agent_bar.grid(row=2, column=0, sticky="ew")
        else:
            self.agent_bar.grid_remove()
        self.send_btn.config(text="Start" if agent else "Ask")
        if self.entry.tag_ranges("hint") or not self._entry_text():
            self._hint(True)
        if save:
            self.app.remember(mode=self.mode.get())

    def folder_changed(self, quiet=False):
        self.folder_btn.config(text=_short(self.app.session.folder))
        if not quiet:
            self.note("AskTim!'s folder is now %s." % paths.pretty(self.app.session.folder))

    def refresh_status(self):
        cfg = _config.Config()
        self.folder_btn.config(text=_short(self.app.session.folder))
        filt = {"standard": "data filter on", "strict": "data filter strict", "off": "data filter OFF"}[cfg.get("privacy")]
        self.status.config(text="%s · %s%s" % (backend.describe_service(cfg), filt, " · showing everything" if cfg.verbose else ""))
        if cfg.share_data:
            self.badge.pack(side="left", padx=(0, 8), before=self.status)
        else:
            self.badge.pack_forget()
        if cfg.is_locked("agent.allow_network"):
            self.web.set(bool(cfg.get("agent.allow_network")))
            self.web_box.state(["disabled"])

    def greet(self):
        key, _where = backend.key_status(_config.Config().provider)
        self.add("AskTim!", "who_ai")
        self.add("\n")
        if key or _config.Config().provider == "openai":
            self.add("Ask about a command, an error or an analysis. Commands in my answers have a Run button: you see them first, "
                     "and they run in the folder above. Agent mode gives me a whole task.\n", "note")
        else:
            self.add("I need an API key for an AI service before I can answer. ", "note")
            self.add_buttons([("Set up a key…", lambda: self.app.open_settings("service"))])

    # -- the transcript -----------------------------------------------------------------------

    def _scrollbar(self, *a):
        self.text.yview(*a)
        self._scrolled()

    def _scrolled(self, e=None):
        def check():
            try:
                self._follow = self.text.yview()[1] > 0.995
            except tk.TclError:
                pass

        self.win.after_idle(check)

    def follow(self):
        """Show the end, and keep showing it as text is added (a new question, a run, a task)."""
        self._follow = True
        self.text.see("end")

    def at_end(self):
        return self._follow

    def add(self, s, *tags):
        follow = self.at_end()
        self.text.insert("end", s, tags)
        if follow:
            self.text.see("end")

    def note(self, s):
        self.add(s.rstrip("\n") + "\n", "note")

    def add_who(self, who):
        self.add(("\n" if self.text.index("end-1c") != "1.0" else "") + who + "\n", "who_ai" if who.startswith("AskTim") else "who")

    def add_buttons(self, buttons, where="end"):
        """A row of buttons in the transcript: [(label, command)] or (label, command, style)."""
        fr = tk.Frame(self.text, bg=self.colors["text_bg"])
        out = []
        for b in buttons:
            label, cmd = b[0], b[1]
            btn = ttk.Button(fr, text=label, command=cmd, style=b[2] if len(b) > 2 else "Small.TButton")
            btn.pack(side="left", padx=(0, 6))
            out.append(btn)
        follow = self.at_end()
        self.text.window_create(where, window=fr, padx=8, pady=3)
        self.text.insert(where, "\n")
        if follow:
            self.text.see("end")
        return fr, out

    def warn_box(self, title, lines, never=False):
        tag = "warn"
        if never:
            self.text.tag_configure("never", background=self.colors["never_bg"], foreground=self.colors["fail"], lmargin1=8, lmargin2=8, rmargin=8)
            tag = "never"
        self.add(title + "\n", tag, "bold")
        for ln in lines:
            self.add("• " + ln + "\n", tag)

    def open_link(self, url):
        if mdview.SAFE_LINK.match(url):
            webbrowser.open(url)

    # -- busy -------------------------------------------------------------------------------------

    def set_busy(self, what):
        self.busy = what
        self.app.set_busy(bool(what))
        if what:
            self.stop_event.clear()
            self.send_btn.state(["disabled"])
            self.stop_btn.state(["!disabled"])
            for b in (self.rb_chat, self.rb_agent, self.way_box, self.web_box, self.here_box, self.new_btn):
                b.state(["disabled"])
        else:
            self.send_btn.state(["!disabled"])
            self.stop_btn.state(["disabled"])
            for b in (self.rb_chat, self.rb_agent, self.way_box, self.here_box, self.new_btn):
                b.state(["!disabled"])
            if not _config.Config().is_locked("agent.allow_network"):
                self.web_box.state(["!disabled"])
            self.refresh_status()

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.status.config(text="Stopping…")

    def set_status_note(self, s):
        self.status.config(text=s)

    def new_conversation(self):
        if self.busy:
            return
        self.app.session.new_conversation()
        self.text.delete("1.0", "end")
        self._streams.clear()
        self.greet()

    # -- asking -----------------------------------------------------------------------------------

    def send(self):
        if self.busy:
            return
        q = self._entry_text()
        if not q:
            return
        self.entry.delete("1.0", "end")
        if self.mode.get() == "agent":
            self.start_agent(q)
        else:
            self.ask(q)

    def ask(self, question, shown=None, output=None, output_of=None):
        cfg = _config.Config()
        self.set_busy("answer")
        self.follow()
        self.add_who("You")
        if isinstance(shown, (list, tuple)):
            for part, tags in shown:
                self.add(part, "user", *tags)
            self.add("\n", "user")
        else:
            self.add((shown or question).rstrip() + "\n", "user")
        if cfg.share_data:
            from ..ui import DATA_OFF_REMINDER, data_off_reminder_due

            if data_off_reminder_due(cfg):
                self.warn_box("The data filter is off", [DATA_OFF_REMINDER])
        aid = self.begin_answer()
        post = self.app.bridge.post

        def work():
            try:
                turn, g, prov = self.app.session.ask(
                    question, on_text=lambda d: post(self.stream, aid, d), on_status=lambda s: post(self.set_status_note, s),
                    stop=self.stop_event, output=output, output_of=output_of)
                post(self.finish_answer, aid, turn, g, prov)
            except backend.Stopped:
                post(self.answer_failed, aid, None, "(stopped)")
            except ProviderError as e:
                post(self.answer_failed, aid, e, str(e))
            except Exception as e:
                traceback.print_exc()
                post(self.answer_failed, aid, e, "Something went wrong in AskTim!: %s" % e)
            finally:
                post(self.set_busy, None)

        self.app.start(work)

    def begin_answer(self):
        self.answers += 1
        aid = self.answers
        self.add_who("AskTim!")
        s, e = "a%d.s" % aid, "a%d.e" % aid
        self.text.mark_set(s, "end-1c")
        self.text.mark_gravity(s, "left")
        self.text.mark_set(e, "end-1c")
        self.text.mark_gravity(e, "right")
        self.text.insert(e, "thinking…\n", ("note",))
        self._streams[aid] = {"text": "", "pending": False}
        self.text.see("end")
        return aid

    def stream(self, aid, delta):
        st = self._streams.get(aid)
        if st is None:
            return
        st["text"] += delta
        if not st["pending"]:
            st["pending"] = True
            self.win.after(120, lambda: self._render(aid, final=False))

    def _render(self, aid, final=False, turn=None):
        st = self._streams.get(aid)
        if st is None or (st.get("final") and not final):
            return  # (a render of the stream that was due after the answer had come whole)
        st["pending"] = False
        st["final"] = st.get("final") or final
        s, e = "a%d.s" % aid, "a%d.e" % aid
        follow = self.at_end()
        self.text.delete(s, e)
        blocks = mdview.parse(st["text"])
        r = mdview.Renderer(self.text, e, on_code=(self._code_buttons if final else None), on_link=self.open_link)
        r.render(blocks)
        if follow:
            self.text.see("end")

    def finish_answer(self, aid, turn, g, prov):
        st = self._streams.get(aid)
        if st is None:
            return
        st["text"] = turn.text or ""
        self._render(aid, final=True)
        cfg = _config.Config()
        if not (turn.text or "").strip():
            self.add("The model sent an empty answer. Ask again: a second try usually works.\n", "error")
        if turn.stop_reason == "refusal":
            self.note("(The model declined to answer this.)")
        if cfg.verbose:
            self.note("%s · %s" % (g.summary(), prov.describe()))
        self.set_status_note(backend.describe_service(cfg))

    def answer_failed(self, aid, err, message):
        st = self._streams.get(aid) if aid is not None else None
        if st is not None and not st["text"].strip():
            self.text.delete("a%d.s" % aid, "a%d.e" % aid)
        if message == "(stopped)":
            if st is not None and st["text"].strip():
                self._render(aid, final=True)
            self.note("(stopped)")
            return
        self.add(message.strip() + "\n", "error")
        if isinstance(err, ProviderError) and err.kind in ("config", "auth", "credit", "notfound"):
            self.add_buttons([("Open the settings…", lambda: self.app.open_settings("service"))])

    def _code_buttons(self, lang, code, closed):
        if not closed or not code.strip():
            return None
        fr = tk.Frame(self.text, bg=self.colors["text_bg"])
        if lang in backend.SHELL_LANGS and backend.split_commands(code):
            ttk.Button(fr, text="Run…", style="Small.TButton", command=lambda: self.run_block(code)).pack(side="left", padx=(0, 6))
        ttk.Button(fr, text="Copy", style="Small.TButton", command=lambda: self.copy(code)).pack(side="left")
        return fr

    def copy(self, text):
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.set_status_note("Copied.")

    # -- running the commands of an answer -------------------------------------------------------

    def run_block(self, code):
        if self.busy:
            self.win.bell()
            self.set_status_note("Wait until AskTim! has finished (or press Stop).")
            return
        cmds = backend.split_commands(code)
        if cmds:
            self.last_dialog = RunDialog(self, cmds, self.app.session.folder, self.start_run)

    def start_run(self, cmds):
        folder = self.app.session.folder
        self.set_busy("run")
        self.follow()
        self.add_who("You ran")
        self.note("in %s" % paths.pretty(folder))
        self._run_state = {"cmds": cmds, "shown": -1, "folder": folder}
        self._show_cmd(0)
        post = self.app.bridge.post

        def work():
            try:
                results = backend.run_commands(cmds, folder, on_event=lambda k, i, d: post(self._run_event, k, i, d), stop=self.stop_event)
                post(self._run_done, results)
            except Exception as e:
                traceback.print_exc()
                post(self.add, "The commands could not be run: %s\n" % e, "error")
                post(self.set_busy, None)

        self.app.start(work)

    def _show_cmd(self, i):
        rs = self._run_state
        if i >= len(rs["cmds"]) or i <= rs["shown"]:
            return
        rs["shown"] = i
        self._flush_out()
        self.add("$ " + rs["cmds"][i].replace("\n", "\n  ") + "\n", "cmd")
        self._out_lines = 0

    def _run_event(self, kind, i, data):
        if kind == "output":
            self._show_cmd(i)
            self._queue_out(data)
        elif kind == "done":
            self._show_cmd(i)
            self._flush_out()
            st, secs = data.status, data.seconds
            if st == 0:
                self.add("done · %.1f s\n" % secs, "ok")
            elif st == -15:
                self.add("stopped after %.1f s\n" % secs, "fail")
            else:
                self.add("failed · exit status %s · %.1f s\n" % (st, secs), "fail")
            if st == 0:
                self._show_cmd(i + 1)
        elif kind == "folder":
            self._flush_out()
            # (the window's folder does not follow a cd: it is where AskTim!'s checks let commands change files,
            # so only the user moves it, with the Folder button)
            if data and os.path.realpath(data) != os.path.realpath(self.app.session.folder):
                self.note("The commands ended in %s. The next ones start in %s again (the Folder button changes that)."
                          % (paths.pretty(data), paths.pretty(self.app.session.folder)))

    def _queue_out(self, text):
        self._out.append(text)
        if len(self._out) == 1:
            self.win.after(80, self._flush_out)

    def _flush_out(self):
        if not self._out:
            return
        text = visible("".join(self._out))
        self._out = []
        lines = text.splitlines(True)
        room = max(0, 1500 - self._out_lines)
        shown = lines[:room]
        self._out_lines += len(lines)
        if shown:
            self.add("".join(shown), "out")
        if len(lines) > room and self._out_lines - len(lines) <= 1500:
            self.add("… (more output is not shown here)\n", "note")

    def _run_done(self, results):
        self._flush_out()
        for r in results:
            if r.status is None:
                self.add("$ " + r.command.replace("\n", "\n  ") + "\n", "cmd_off")
                self.add("(not run)\n", "note")
        self.app.session.record_runs(results)
        failed = [r for r in results if r.status not in (None, 0)]
        self.set_busy(None)
        if failed and failed[0].status != -15:
            r = failed[0]
            self._last_run = r
            holder = {}
            holder["frame"], holder["buttons"] = self.add_buttons([("Ask AskTim! about this error", lambda: self.ask_about(r, holder), "Accent.TButton")])

    def ask_about(self, r, holder=None):
        if self.busy:
            return
        if holder:
            for b in holder["buttons"]:
                b.state(["disabled"])
        if self.mode.get() != "chat":
            self.mode.set("chat")
            self.on_mode()
        first = r.command.split("\n")[0]
        cmd = first[:120] + ("…" if len(first) > 120 or "\n" in r.command else "")
        self.ask("Why did this command fail, and how do I fix it?",
                 shown=[("Why did ", ()), (cmd, ("icode",)), (" fail (exit status %s)?" % r.status, ())], output=r.text(), output_of=(r.command, r.status))

    # -- the agent --------------------------------------------------------------------------------

    def start_agent(self, task):
        from ..agent import Agent
        from ..providers import make_provider
        from .agentui import GuiAgentUI

        cfg = _config.Config()
        way = {l: k for k, l in WAYS}.get(self.way.get(), "approve")
        self.app.remember(agent_way=way)
        if os.name == "nt" and not shutil.which("bash"):
            self.add("The agent runs commands in bash, which this computer does not have. Use WSL or Git Bash.\n", "error")
            return
        self.set_busy("agent")
        self.follow()
        self.add_who("You")
        self.add(task.rstrip() + "\n", "user")
        ui = GuiAgentUI(self, self.stop_event, verbose=cfg.verbose)
        folder = self.app.session.folder
        post = self.app.bridge.post
        web = bool(self.web.get())
        here = bool(self.here.get())  # (Tk's variables are read here, in Tk's thread: never in the worker)

        def work():
            try:
                try:
                    prov = make_provider(cfg)
                except ProviderError as e:
                    post(self.answer_failed, None, e, str(e))
                    return
                ag = Agent(cfg, prov, ui, task, mode=way, here=here, allow_network=web, student_dir=folder)
                ag.run()
            except Exception as e:
                traceback.print_exc()
                post(self.add, "The agent stopped because of an error in AskTim!: %s\n" % e, "error")
            finally:
                post(self.set_busy, None)

        self.app.start(work)


def _short(path, width=34):
    p = paths.pretty(path)
    return p if len(p) <= width else "…" + p[-(width - 1):]

