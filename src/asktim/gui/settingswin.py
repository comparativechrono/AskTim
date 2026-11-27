"""The settings window. What is set here is what `AskTim! privacy`, `AskTim! verbose`,
`AskTim! model` and the other commands set: the same file (~/.config/asktim/config.json), used by
the command line, notebooks and this window alike. A setting that the course fixes is shown, but
cannot be changed; one that an environment variable overrides says so."""

import os

import tkinter as tk
from tkinter import messagebox, ttk

from .. import __version__
from .. import config as _config
from .. import models, paths
from ..providers import ProviderError
from . import backend

SERVICES = [("claude", "Anthropic (Claude)"), ("gemini", "Google Gemini"), ("openai", "OpenAI-compatible service")]
SERVICE_LABEL = dict(SERVICES)
WHERE_KEYS = {
    "claude": "Get a key at console.anthropic.com (Settings, then API keys).",
    "gemini": "Get a key at aistudio.google.com/apikey (Gemini has a free tier).",
    "openai": "The service's own website gives keys. A service on this computer (Ollama, LM Studio) needs none.",
}
PRIVACY_STOPS = ["strict", "standard", "off"]
PRIVACY_TEXT = {
    "strict": "Strict: no data, and no file or column names either. Of what commands print, only errors and help text go to the AI.",
    "standard": "On (the default): your data stay on this computer. The AI gets your question, what is installed, the names and "
                "sizes of your files, the structure of a file you ask about (not its contents), and errors and summaries from your "
                "commands, with any line that looks like data withheld.",
    "off": "Off: your data can be sent to the AI – what commands print, and the start of files you ask about. Secrets, e-mail "
           "addresses and other personal details are still removed. Use this only with data you may share, never personal or patient data.",
}
VERBOSE_TEXT = {
    False: "Only the answer: the command line and notebooks print the AI's answer and what went wrong, nothing else.",
    True: "Everything: they also show what was sent with each question, which model answered, and AskTim!'s notes. "
          "This window then shows what was sent, under each answer.",
}
EFFORTS = ["low", "medium", "high"]


class SettingsWindow(object):
    def __init__(self, app):
        self.app = app
        c = app.colors
        self.top = top = tk.Toplevel(app.root)
        top.title("AskTim! settings")
        top.configure(bg=c["window"])
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.minsize(560, 520)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        self.nb = ttk.Notebook(top)
        self.nb.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))
        self.tabs = {}
        for name, label in (("service", "AI service"), ("privacy", "Privacy and output"), ("agent", "Agent"), ("about", "About")):
            f = ttk.Frame(self.nb, padding=14)
            f.columnconfigure(1, weight=1)
            self.nb.add(f, text=label)
            self.tabs[name] = f
        foot = ttk.Frame(top, padding=(12, 6, 12, 10))
        foot.grid(row=1, column=0, sticky="ew")
        self.saved = ttk.Label(foot, style="Good.TLabel")
        self.saved.pack(side="left")
        ttk.Button(foot, text="Close", command=self.close).pack(side="right")
        self._service_tab()
        self._privacy_tab()
        self._agent_tab()
        self._about_tab()
        top.geometry("640x600")

    # -- the window ------------------------------------------------------------------------------

    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def show(self, tab=None):
        if tab in self.tabs:
            self.nb.select(self.tabs[tab])
        self.top.deiconify()
        self.top.lift()
        try:
            self.top.focus_force()
        except tk.TclError:
            pass

    def close(self):
        self.top.destroy()

    def flash(self, text, bad=False):
        self.saved.config(text=text, style="Bad.TLabel" if bad else "Good.TLabel")
        self.top.after(6000, lambda: self.saved.config(text="") if self.alive() else None)

    def _save(self, key, value, what):
        cfg = _config.Config()
        if cfg.is_locked(key):
            self.flash("Your course fixes this setting.", bad=True)
            return False
        _config.save_user_setting(key, value)
        env = backend.setting_from_environment(key)
        if env:
            self.flash("Saved, but %s is set in your environment, and it wins over this setting." % env, bad=True)
        else:
            self.flash("Saved: %s. The command line and notebooks use it too." % what)
        self.app.refresh()
        return True

    def _locked_note(self, parent, row, key, column=1):
        if _config.Config().is_locked(key):
            ttk.Label(parent, text="Fixed by your course.", style="Muted.TLabel").grid(row=row, column=column, sticky="w")
            return True
        return False

    def _wrap_label(self, parent, text, row, column=0, columnspan=3, style="Muted.TLabel", pady=(2, 8)):
        lab = ttk.Label(parent, text=text, style=style, wraplength=560, justify="left")
        lab.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=pady)
        return lab

    # -- AI service ------------------------------------------------------------------------------

    def _service_tab(self):
        f = self.tabs["service"]
        cfg = _config.Config()
        self.service = tk.StringVar(master=self.top, value=SERVICE_LABEL[cfg.provider])
        self.model = tk.StringVar(master=self.top, value=cfg.model())
        self.base_url = tk.StringVar(master=self.top, value=cfg.get("openai_base_url") or "")
        self.key = tk.StringVar(master=self.top)
        ttk.Label(f, text="Service").grid(row=0, column=0, sticky="w", pady=4)
        self.service_box = ttk.Combobox(f, state="readonly", textvariable=self.service, values=[l for _p, l in SERVICES], width=30)
        self.service_box.grid(row=0, column=1, sticky="w", pady=4)
        self.service_box.bind("<<ComboboxSelected>>", lambda e: self._service_changed())
        ttk.Label(f, text="Model").grid(row=1, column=0, sticky="w", pady=4)
        mrow = ttk.Frame(f)
        mrow.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        self.model_box = ttk.Combobox(mrow, textvariable=self.model, width=34)
        self.model_box.pack(side="left")
        self.model_box.bind("<<ComboboxSelected>>", lambda e: self._model_note())
        self.model_box.bind("<KeyRelease>", lambda e: self._model_note())
        self.list_btn = ttk.Button(mrow, text="List the models", style="Small.TButton", command=self.list_models)
        self.list_btn.pack(side="left", padx=(8, 0))
        self.model_about = ttk.Label(f, style="Muted.TLabel", wraplength=520, justify="left")
        self.model_about.grid(row=2, column=1, columnspan=2, sticky="w")
        self.url_label = ttk.Label(f, text="Address")
        self.url_entry = ttk.Entry(f, textvariable=self.base_url, width=44)
        ttk.Label(f, text="API key").grid(row=4, column=0, sticky="w", pady=(10, 4))
        self.key_entry = ttk.Entry(f, textvariable=self.key, show="•", width=44)
        self.key_entry.grid(row=4, column=1, sticky="w", pady=(10, 4))
        self.key_state = ttk.Label(f, style="Muted.TLabel", wraplength=520, justify="left")
        self.key_state.grid(row=5, column=1, columnspan=2, sticky="w")
        krow = ttk.Frame(f)
        krow.grid(row=6, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.test_btn = ttk.Button(krow, text="Test and save", style="Accent.TButton", command=self.test_and_save)
        self.test_btn.pack(side="left")
        self.save_anyway = ttk.Button(krow, text="Save without testing", command=lambda: self._save_service(tested=None))
        self.forget_btn = ttk.Button(krow, text="Forget the key", command=self.forget_key)
        self.forget_btn.pack(side="left", padx=(8, 0))
        self.test_out = ttk.Label(f, style="Muted.TLabel", wraplength=520, justify="left")
        self.test_out.grid(row=7, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.effort_frame = ttk.LabelFrame(f, text="How hard Claude thinks (more is slower, and costs more)", padding=8)
        self.effort_frame.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.effort = {}
        for i, (purpose, label) in enumerate((("chat", "Questions"), ("agent", "The agent"))):
            ttk.Label(self.effort_frame, text=label).grid(row=0, column=2 * i, sticky="w", padx=(0 if i == 0 else 18, 6))
            var = tk.StringVar(master=self.top, value=(cfg.get("effort") or {}).get(purpose) or "the model's default")
            box = ttk.Combobox(self.effort_frame, state="readonly", textvariable=var, width=18, values=EFFORTS + ["the model's default"])
            box.grid(row=0, column=2 * i + 1, sticky="w")
            box.bind("<<ComboboxSelected>>", lambda e, p=purpose, v=var: self._save("effort." + p, None if v.get() not in EFFORTS else v.get(),
                                                                                       "effort for %s: %s" % ("questions" if p == "chat" else "the agent", v.get())))
            if cfg.is_locked("effort." + purpose):
                box.state(["disabled"])
            self.effort[purpose] = (var, box)
        self._wrap_label(f, "What is sent, and where: your key stays on this computer (in %s, readable by you only) and goes only to the "
                            "service you choose. To that service go your questions and what AskTim! sends with them (the Privacy tab says "
                            "what). The data filter keeps your data out." % paths.pretty(_config.keys_file()), row=9, pady=(14, 0))
        if cfg.is_locked("provider"):
            self.service_box.state(["disabled"])
            ttk.Label(f, text="Fixed by your course.", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=6)
        self._service_changed(initial=True)

    def _provider(self):
        return {l: p for p, l in SERVICES}.get(self.service.get(), "claude")

    def _service_changed(self, initial=False):
        cfg = _config.Config()
        p = self._provider()
        if not initial:
            self.model.set(cfg.model(p) or "")
            self.key.set("")
            self.test_out.config(text="")
        self.model_box.config(values=[cfg.model(p)] if cfg.model(p) else [])
        if cfg.is_locked("models." + p):
            self.model_box.state(["disabled"])
            self.list_btn.state(["disabled"])
        else:
            self.model_box.state(["!disabled"])
            self.list_btn.state(["!disabled"])
        if p == "openai":
            self.url_label.grid(row=3, column=0, sticky="w", pady=4)
            self.url_entry.grid(row=3, column=1, sticky="w", pady=4)
        else:
            self.url_label.grid_remove()
            self.url_entry.grid_remove()
        if p == "claude":
            self.effort_frame.grid()
        else:
            self.effort_frame.grid_remove()
        key, line = backend.key_status(p)
        self.key_state.config(text=line + " " + WHERE_KEYS[p] + (" Paste a new key above to replace it." if key else ""))
        if key and _config.stored_keys().get(p):
            self.forget_btn.state(["!disabled"])
        else:
            self.forget_btn.state(["disabled"])
        self.save_anyway.pack_forget()
        self._model_note()

    def _model_note(self):
        p = self._provider()
        fam = models.Model(self.model.get().strip()).family() if p != "openai" else ""
        short = models.SHORT.get(p, {})
        about = models.ABOUT.get(fam, "")
        names = ", ".join("%s (%s)" % (k, models.ABOUT.get(k, "")) for k in sorted(short)) if short else ""
        text = ("%s: %s. " % (fam.capitalize(), about) if about else "") + (
            "Short names work too: %s." % names if names else "Type the name of a model that your service offers.")
        self.model_about.config(text=text)

    def list_models(self):
        p = self._provider()
        key = self.key.get().strip() or None
        url = self.base_url.get().strip() or None
        self.list_btn.state(["disabled"])
        self.test_out.config(text="Asking the service for its models…", style="Muted.TLabel")
        post = self.app.bridge.post

        def work():
            try:
                cfg = _config.Config({"openai_base_url": url} if (p == "openai" and url) else None)
                found = models.list_models(cfg, p, key=key)
                post(self._listed, p, found, None)
            except ProviderError as e:
                post(self._listed, p, None, str(e))

        self.app.start(work)

    def _listed(self, p, found, err):
        if not self.alive():
            return
        self.list_btn.state(["!disabled"])
        if p != self._provider():
            return
        if err:
            self.test_out.config(text="Could not list them: " + err, style="Bad.TLabel")
            return
        ids = [m.id for m in found]
        self.model_box.config(values=ids)
        self.test_out.config(text="%d models, newest first: choose one in the list, then Test and save." % len(ids), style="Muted.TLabel")

    def test_and_save(self):
        p = self._provider()
        word = self.model.get().strip()
        if not word and p != "openai":
            word = _config.Config().model(p)
        key = self.key.get().strip() or None
        if p == "claude" and key and not key.startswith("sk-ant-"):
            self.test_out.config(text="That does not look like a Claude key (they start with sk-ant-); testing it anyway…", style="Muted.TLabel")
        url = self.base_url.get().strip() or None
        if p == "openai" and not word:
            self.test_out.config(text="Give the name of the model.", style="Bad.TLabel")
            return
        self.test_btn.state(["disabled"])
        self.save_anyway.pack_forget()
        if not (self.test_out.cget("text") or "").startswith("That does not"):
            self.test_out.config(text="Asking the model to answer…", style="Muted.TLabel")
        post = self.app.bridge.post

        def work():
            try:
                cfg = _config.Config({"openai_base_url": url} if (p == "openai" and url) else None)
                try:
                    model, how = models.resolve(cfg, word, provider=p) if p != "openai" else (word, "as given")
                except ValueError as e:
                    post(self._tested, p, word, None, str(e), None)
                    return
                answered = backend.test_service(p, model, key=key, base_url=url)
                post(self._tested, p, model, answered, None, None)
            except ProviderError as e:
                post(self._tested, p, word, None, str(e), e.kind)

        self.app.start(work)

    def _tested(self, p, model, answered, err, kind):
        if not self.alive():
            return
        self.test_btn.state(["!disabled"])
        if err:
            self.test_out.config(text="Not saved: " + err, style="Bad.TLabel")
            if kind in ("network", "busy", "rate", "other", None):
                self.save_anyway.pack(side="left", padx=(8, 0), before=self.forget_btn)
            return
        self.model.set(model)
        self._save_service(tested=answered)

    def _save_service(self, tested=None):
        p = self._provider()
        model = self.model.get().strip()
        key = self.key.get().strip() or None
        url = self.base_url.get().strip() or None
        fixed = backend.save_service(p, model, key=key, base_url=url)
        self.key.set("")
        self.save_anyway.pack_forget()
        msg = ("It works: %s answered. Saved." % tested) if tested else "Saved, without a test."
        if fixed:
            msg += " Your course fixes %s, so that was not changed." % " and ".join(fixed)
        self.test_out.config(text=msg, style="Good.TLabel" if tested else "Muted.TLabel")
        self.flash("Saved: %s, %s. The command line and notebooks use it too." % (SERVICE_LABEL[p], model))
        self._service_changed(initial=True)
        self.app.refresh()

    def forget_key(self):
        p = self._provider()
        if not messagebox.askyesno("Forget the key?", "Delete the stored %s key from this computer?" % SERVICE_LABEL[p], parent=self.top):
            return
        gone = _config.forget_key(p)
        self.flash("The key was deleted." if gone else "There was no stored key.")
        self._service_changed(initial=True)
        self.app.refresh()

    # -- privacy and output -------------------------------------------------------------------------

    def _slider(self, parent, row, labels, value, on_change, locked=False):
        fr = ttk.Frame(parent)
        fr.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        for i in range(len(labels)):
            fr.columnconfigure(i, weight=1, uniform="stop")
        var = tk.DoubleVar(master=self.top, value=value)
        state = {"last": int(round(value))}

        def snap(_v=None):
            n = int(round(var.get()))
            var.set(n)
            if n != state["last"]:
                old = state["last"]
                state["last"] = n
                if on_change(n) is False:
                    state["last"] = old
                    var.set(old)

        scale = ttk.Scale(fr, from_=0, to=len(labels) - 1, orient="horizontal", variable=var)
        scale.grid(row=0, column=0, columnspan=len(labels), sticky="ew", padx=40)
        scale.bind("<ButtonRelease-1>", snap)
        scale.bind("<KeyRelease>", snap)
        for i, lab in enumerate(labels):
            ttk.Label(fr, text=lab, style="Muted.TLabel", anchor=("w" if i == 0 else "e" if i == len(labels) - 1 else "center")).grid(
                row=1, column=i, sticky="ew", padx=(30 if i == 0 else 0, 30 if i == len(labels) - 1 else 0))
        if locked:
            scale.state(["disabled"])
        return var, scale

    def _privacy_tab(self):
        f = self.tabs["privacy"]
        cfg = _config.Config()
        ttk.Label(f, text="The data filter", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        level = cfg.get("privacy")
        self.privacy_var, self.privacy_scale = self._slider(f, 1, ["Strict", "On", "Off"], PRIVACY_STOPS.index(level), self._privacy_changed,
                                                            locked=cfg.is_locked("privacy"))
        self.privacy_text = self._wrap_label(f, PRIVACY_TEXT[level], row=2, style="TLabel")
        self._locked_note(f, 3, "privacy", column=0)

        ttk.Label(f, text="What goes with a question", style="Title.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 4))
        self.send_history = tk.BooleanVar(master=self.top, value=bool(cfg.get("send_history", True)))
        cb = ttk.Checkbutton(f, text="My recent shell commands (and the ones I ran from this window) – how many:", variable=self.send_history,
                             command=lambda: self._save("send_history", bool(self.send_history.get()), "recent commands %s" % ("on" if self.send_history.get() else "off")))
        cb.grid(row=5, column=0, columnspan=2, sticky="w")
        self.history_n = tk.StringVar(master=self.top, value=str(cfg.get("history_lines") if cfg.get("history_lines") is not None else 20))
        sp = ttk.Spinbox(f, from_=0, to=100, width=5, textvariable=self.history_n, command=self._history_n_changed)
        sp.grid(row=5, column=2, sticky="w")
        sp.bind("<FocusOut>", lambda e: self._history_n_changed())
        if cfg.is_locked("send_history"):
            cb.state(["disabled"])
        if cfg.is_locked("history_lines"):
            sp.state(["disabled"])
        self.send_names = tk.BooleanVar(master=self.top, value=bool(cfg.get("send_file_names", True)))
        cb2 = ttk.Checkbutton(f, text="The names and sizes of the files in the folder", variable=self.send_names,
                              command=lambda: self._save("send_file_names", bool(self.send_names.get()), "file names %s" % ("on" if self.send_names.get() else "off")))
        cb2.grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))
        if cfg.is_locked("send_file_names"):
            cb2.state(["disabled"])
        ttk.Label(f, text="The command line remembers a conversation for this many minutes:").grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.memory = tk.StringVar(master=self.top, value=str(cfg.get("chat_memory_minutes") or 60))
        sp2 = ttk.Spinbox(f, from_=0, to=1440, width=5, textvariable=self.memory, command=self._memory_changed)
        sp2.grid(row=7, column=2, sticky="w", pady=(6, 0))
        sp2.bind("<FocusOut>", lambda e: self._memory_changed())

        ttk.Label(f, text="What the command line and notebooks show", style="Title.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(16, 0))
        self.verbose_var, self.verbose_scale = self._slider(f, 9, ["Only the answer", "Everything"], 1 if cfg.verbose else 0, self._verbose_changed,
                                                            locked=cfg.is_locked("verbose"))
        self.verbose_text = self._wrap_label(f, VERBOSE_TEXT[cfg.verbose] + " (-v or -q changes it for one command.)", row=10, style="TLabel")
        self._locked_note(f, 11, "verbose", column=0)

    def _privacy_changed(self, n):
        level = PRIVACY_STOPS[n]
        if level == "off":
            ok = messagebox.askyesno(
                "Turn the data filter off?",
                "With the data filter off, your data can be sent to the AI service: what your commands print, the start of files you "
                "ask about, and what the agent's commands print. Secrets, e-mail addresses and other personal details are still "
                "removed.\n\nOnly do this with data you may share – never personal or patient data. It stays off until you turn it on.",
                parent=self.top, icon="warning")
            if not ok:
                return False
        if not self._save("privacy", level, "the data filter is %s" % {"strict": "strict", "standard": "on", "off": "OFF"}[level]):
            return False
        self.privacy_text.config(text=PRIVACY_TEXT[level])
        return True

    def _verbose_changed(self, n):
        if not self._save("verbose", bool(n), "the command line shows %s" % ("everything" if n else "only the answer")):
            return False
        self.verbose_text.config(text=VERBOSE_TEXT[bool(n)] + " (-v or -q changes it for one command.)")
        return True

    def _int_setting(self, var, key, what, lo, hi):
        try:
            n = max(lo, min(hi, int(var.get())))
        except ValueError:
            return
        var.set(str(n))
        if _config.Config().get(key) != n:
            self._save(key, n, what % n)

    def _history_n_changed(self):
        self._int_setting(self.history_n, "history_lines", "send the last %d commands", 0, 100)

    def _memory_changed(self):
        self._int_setting(self.memory, "chat_memory_minutes", "remember a conversation for %d minutes", 0, 1440)

    # -- the agent ---------------------------------------------------------------------------------

    def _agent_tab(self):
        f = self.tabs["agent"]
        cfg = _config.Config()
        rows = [("agent.max_steps", "At most this many steps for a task:", 1, 200, "at most %d steps"),
                ("agent.command_timeout", "At most this many seconds for one command:", 5, 86400, "at most %d seconds per command")]
        self.agent_vars = {}
        for r, (key, label, lo, hi, what) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, columnspan=2, sticky="w", pady=4)
            var = tk.StringVar(master=self.top, value=str(cfg.get(key)))
            sp = ttk.Spinbox(f, from_=lo, to=hi, width=7, textvariable=var, command=lambda v=var, k=key, w=what, a=lo, b=hi: self._int_setting(v, k, w, a, b))
            sp.grid(row=r, column=2, sticky="w")
            sp.bind("<FocusOut>", lambda e, v=var, k=key, w=what, a=lo, b=hi: self._int_setting(v, k, w, a, b))
            if cfg.is_locked(key):
                sp.state(["disabled"])
            self.agent_vars[key] = var
        self.allow_net = tk.BooleanVar(master=self.top, value=bool(cfg.get("agent.allow_network")))
        cb = ttk.Checkbutton(f, text="Let the agent download (curl, wget, git clone) unless I say otherwise. It never uploads.", variable=self.allow_net,
                             command=lambda: self._save("agent.allow_network", bool(self.allow_net.get()), "downloads %s" % ("allowed" if self.allow_net.get() else "off")))
        cb.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 4))
        if cfg.is_locked("agent.allow_network"):
            cb.state(["disabled"])
        ttk.Label(f, text="Sandbox:").grid(row=3, column=0, sticky="w", pady=4)
        self.sandbox = tk.StringVar(master=self.top, value=cfg.get("agent.sandbox") or "auto")
        sb = ttk.Combobox(f, state="readonly", width=8, textvariable=self.sandbox, values=["auto", "on", "off"])
        sb.grid(row=3, column=1, sticky="w")
        sb.bind("<<ComboboxSelected>>", lambda e: self._save("agent.sandbox", self.sandbox.get(), "sandbox %s" % self.sandbox.get()))
        if cfg.is_locked("agent.sandbox"):
            sb.state(["disabled"])
        self._wrap_label(f, "auto: on Linux the agent works in a sandbox (bubblewrap) when it can. on: it works only in a sandbox. off: never "
                            "in one. Whatever this says, AskTim! checks every command, and the agent writes only in its own folder "
                            "(asktim-runs/ in your folder), unless you tick “Work in my folder” for a task.", row=4)

    # -- about ---------------------------------------------------------------------------------------

    def _about_tab(self):
        f = self.tabs["about"]
        cfg = _config.Config()
        ttk.Label(f, text="AskTim! %s" % __version__, style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        lines = [
            "Settings: %s" % paths.pretty(_config.config_file()),
            "Keys: %s (readable by you only)" % paths.pretty(_config.keys_file()),
        ]
        if os.path.exists(paths.site_config_file()):
            lines.append("Your course's settings: %s" % paths.site_config_file())
        if cfg.data.get("locked"):
            lines.append("Fixed by your course: %s" % ", ".join(cfg.data["locked"]))
        if cfg.get("course"):
            lines.append("Your course's notes for the AI: %s" % cfg.get("course")[:300])
        lines.append("Your questions are logged for your statement on AI use: AskTim! log and AskTim! statement, at the command line.")
        for i, ln in enumerate(lines):
            self._wrap_label(f, ln, row=1 + i, style="TLabel", pady=(4, 0))
        r = 2 + len(lines)
        ttk.Label(f, text="Start AskTim! from your desktop", style="Title.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", pady=(16, 4))
        self.shortcut_out = ttk.Label(f, style="Muted.TLabel", wraplength=540, justify="left")
        ttk.Button(f, text="Add a shortcut", command=self.add_shortcut).grid(row=r + 1, column=0, sticky="w")
        self.shortcut_out.grid(row=r + 2, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def add_shortcut(self):
        from . import shortcut

        try:
            where = shortcut.install()
            self.shortcut_out.config(text="Added: %s" % where, style="Good.TLabel")
        except Exception as e:  # (anything the system refuses)
            self.shortcut_out.config(text="Could not add a shortcut: %s" % e, style="Bad.TLabel")
