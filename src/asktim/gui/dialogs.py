"""Small windows that ask something: run these commands?, change this step, why not?"""

import tkinter as tk
from tkinter import ttk

from .. import paths
from . import backend


def _center(top, parent):
    top.update_idletasks()
    try:
        x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + max(40, (parent.winfo_height() - top.winfo_reqheight()) // 3)
        top.geometry("+%d+%d" % (max(0, x), max(0, y)))
    except tk.TclError:
        pass


def _modal(top, parent):
    top.transient(parent)
    _center(top, parent)
    try:
        top.grab_set()
    except tk.TclError:
        pass
    top.focus_force()


class RunDialog(object):
    """Shows the commands of a block, what AskTim!'s checks say of them, and runs them if the user says so."""

    def __init__(self, chat, commands, folder, on_run):
        self.chat = chat
        self.folder = folder
        self.on_run = on_run
        app = chat.app
        c, f = app.colors, app.fonts
        self.top = top = tk.Toplevel(chat.win)
        top.title("Run these commands?")
        top.configure(bg=c["window"])
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        ttk.Label(top, text="AskTim! will run these commands in %s, one after the other, and stop at the first that fails.\n"
                            "Read them first. You can change them here." % paths.pretty(folder),
                  wraplength=560, justify="left").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        self.text = tk.Text(top, height=min(14, 2 + sum(c_.count("\n") + 1 for c_ in commands)), width=72, wrap="none", font=f["mono"],
                            bd=0, highlightthickness=1, highlightbackground=c["border"], padx=8, pady=6, undo=True)
        self.text.insert("1.0", "\n".join(commands))
        self.text.grid(row=1, column=0, sticky="nsew", padx=14)
        self.verdict = tk.Text(top, height=5, width=72, wrap="word", font=f["small"], bd=0, highlightthickness=0, bg=c["window"],
                               cursor="arrow", padx=2, pady=4)
        self.verdict.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 0))
        self.verdict.tag_configure("ok", foreground=c["ok"])
        self.verdict.tag_configure("warn", foreground=c["warn_fg"], background=c["warn_bg"])
        self.verdict.tag_configure("never", foreground=c["fail"], background=c["never_bg"])
        self.verdict.tag_configure("bold", font=f["small_bold"])
        btns = ttk.Frame(top, padding=(14, 8, 14, 12))
        btns.grid(row=3, column=0, sticky="ew")
        self.run_btn = ttk.Button(btns, text="Run", style="Accent.TButton", command=self.run)
        self.run_btn.pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="right", padx=(0, 6))
        ttk.Button(btns, text="Copy", command=self.copy).pack(side="left")
        top.bind("<Escape>", lambda e: self.cancel())
        top.bind("<Control-Return>", lambda e: self.run())
        top.protocol("WM_DELETE_WINDOW", self.cancel)
        self.text.bind("<<Modified>>", self._changed)
        self._after = None
        self.judgement = None
        self._judge()
        _modal(top, chat.win)
        self.text.focus_set()

    def commands(self):
        return backend.split_commands(self.text.get("1.0", "end-1c"))

    def _changed(self, e=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self._after:
            self.top.after_cancel(self._after)
        self._after = self.top.after(400, self._judge)

    def _judge(self):
        self._after = None
        cmds = self.commands()
        v = self.verdict
        v.config(state="normal")
        v.delete("1.0", "end")
        if not cmds:
            self.judgement = None
            v.insert("end", "There is no command to run.")
            self.run_btn.state(["disabled"])
            v.config(state="disabled")
            return
        j = self.judgement = backend.judge(cmds, self.folder)
        if j.level == "refuse":
            v.insert("end", "AskTim! will not run this from its window:\n", ("never", "bold"))
            for r in j.never:
                v.insert("end", "• %s\n" % r, ("never",))
            v.insert("end", "If you are sure about it, copy the commands into a terminal and run them there.", ("never",))
            self.run_btn.state(["disabled"])
            self.run_btn.config(text="Run")
        elif j.level == "warn":
            v.insert("end", "Check these before you run them (AskTim!'s agent would not be allowed to):\n", ("warn", "bold"))
            for r in j.warnings:
                v.insert("end", "• %s\n" % r, ("warn",))
            self.run_btn.state(["!disabled"])
            self.run_btn.config(text="Run anyway")
        else:
            v.insert("end", "AskTim!'s checks found nothing to warn you about. You are still the one who decides: read the commands.", ("ok",))
            self.run_btn.state(["!disabled"])
            self.run_btn.config(text="Run")
        v.config(state="disabled")

    def run(self):
        self._judge()
        j = self.judgement
        if j is None or j.level == "refuse":
            return
        cmds = self.commands()
        self.top.destroy()
        self.on_run(cmds)

    def copy(self):
        self.top.clipboard_clear()
        self.top.clipboard_append(self.text.get("1.0", "end-1c"))

    def cancel(self):
        self.top.destroy()


def ask_text(parent, app, title, prompt, initial="", multiline=False, ok="OK", note_prompt=None):
    """A short question; returns the answer (or (answer, note) with note_prompt), or None if cancelled."""
    c, f = app.colors, app.fonts
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=c["window"])
    top.columnconfigure(0, weight=1)
    ttk.Label(top, text=prompt, wraplength=520, justify="left").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
    if multiline:
        box = tk.Text(top, height=12, width=76, wrap="none", font=f["mono"], bd=0, highlightthickness=1, highlightbackground=c["border"],
                      padx=8, pady=6, undo=True)
        box.insert("1.0", initial)
        top.rowconfigure(1, weight=1)
        box.grid(row=1, column=0, sticky="nsew", padx=14)

        def value():
            return box.get("1.0", "end-1c")
    else:
        var = tk.StringVar(master=top, value=initial)
        box = ttk.Entry(top, textvariable=var, width=60)
        box.grid(row=1, column=0, sticky="ew", padx=14)

        def value():
            return var.get()
    note = None
    if note_prompt:
        ttk.Label(top, text=note_prompt, style="Muted.TLabel").grid(row=2, column=0, sticky="w", padx=14, pady=(8, 2))
        note = tk.StringVar(master=top)
        ttk.Entry(top, textvariable=note, width=60).grid(row=3, column=0, sticky="ew", padx=14)
    result = {}
    btns = ttk.Frame(top, padding=(14, 10, 14, 12))
    btns.grid(row=4, column=0, sticky="ew")

    def done(ok_):
        if ok_:
            result["v"] = value()
            result["n"] = note.get() if note is not None else None
        top.destroy()

    ttk.Button(btns, text=ok, style="Accent.TButton", command=lambda: done(True)).pack(side="right")
    ttk.Button(btns, text="Cancel", command=lambda: done(False)).pack(side="right", padx=(0, 6))
    top.bind("<Escape>", lambda e: done(False))
    if not multiline:
        top.bind("<Return>", lambda e: done(True))
    top.protocol("WM_DELETE_WINDOW", lambda: done(False))
    _modal(top, parent)
    box.focus_set()
    top.wait_window()
    if "v" not in result:
        return None
    return (result["v"], result["n"]) if note_prompt else result["v"]
