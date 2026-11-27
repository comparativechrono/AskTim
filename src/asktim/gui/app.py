"""AskTim!'s window: the floating head, the chat (with the agent) and the settings, in Tk.

Everything that takes time - the AI's answers, the commands, the agent - runs in a thread of
its own. Those threads never touch Tk: they hand their news to the Bridge, which shows it from
Tk's own thread, a few times a second.
"""

import os
import queue
import signal
import sys
import threading
import time
import traceback

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, ttk

from .. import paths
from . import backend
from .head import Head, image_file

COLORS = {
    "window": "#f3f4f6",
    "text_bg": "#ffffff",
    "fg": "#1f2328",
    "muted": "#6e7781",
    "border": "#d0d7de",
    "accent": "#1f6feb",
    "accent_dark": "#1158c7",
    "user_bg": "#e7f0fd",
    "code_bg": "#f3f4f6",
    "out_bg": "#1f2428",
    "out_fg": "#e6edf3",
    "ok": "#1a7f37",
    "fail": "#cf222e",
    "warn_bg": "#fff8c5",
    "warn_fg": "#7d4e00",
    "never_bg": "#ffebe9",
    "link": "#0969da",
}


def _guarded(fn):
    """fn(), or None (with the error in the window's log) if it fails."""
    try:
        return fn()
    except Exception:
        traceback.print_exc()
        return None


class Bridge(object):
    """Calls from other threads, made in Tk's thread."""

    def __init__(self, root, interval=40):
        self.root = root
        self.q = queue.Queue()
        self.interval = interval
        self.alive = True
        self._after = root.after(interval, self._poll)

    def stop(self):
        self.alive = False
        try:
            self.root.after_cancel(self._after)
        except (tk.TclError, ValueError):
            pass

    def post(self, fn, *a, **k):
        self.q.put((fn, a, k))

    def _poll(self):
        for _ in range(500):
            try:
                fn, a, k = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*a, **k)
            except Exception:
                traceback.print_exc()
        if self.alive:
            try:
                self._after = self.root.after(self.interval, self._poll)
            except tk.TclError:
                pass


class Answer(object):
    """A question that a worker thread asks the user (approve a step ...), and waits for."""

    def __init__(self):
        self.event = threading.Event()
        self.value = None

    def set(self, value):
        self.value = value
        self.event.set()

    def wait(self, stop=None):
        while not self.event.wait(0.2):
            if stop is not None and stop.is_set():
                return None
        return self.value


class App(object):
    def __init__(self, folder=None, root=None, head=True):
        self.root = root or tk.Tk(className="AskTim")
        self.root.withdraw()
        self.root.title("AskTim!")
        try:
            self.scale = float(self.root.tk.call("tk", "scaling")) * 72.0 / 96.0
        except (tk.TclError, ValueError):
            self.scale = 1.0
        self.colors = dict(COLORS)
        self.state = backend.load_state()
        if not folder or not os.path.isdir(folder):
            last = self.state.get("folder")
            folder = last if last and os.path.isdir(last) else os.path.expanduser("~")
        self.session = backend.Session(folder)
        self.bridge = Bridge(self.root)
        self._fonts()
        self._style()
        try:
            self.icon = tk.PhotoImage(master=self.root, file=image_file(False, False))
            self.root.iconphoto(True, self.icon)
        except tk.TclError:
            self.icon = None
        self.chat = None
        self.settings = None
        self.workers = []
        self.head = Head(self) if head else None
        self.quitting = False
        self.closed = False
        # (seconds: the window's own process sets it, and then a Quit ends the process even if Tk or a thread hangs)
        self.end_process_after = None

    def start(self, fn):
        """Run fn in a thread of its own (it must not touch Tk: it posts to the bridge)."""
        self.workers = [t for t in self.workers if t.is_alive()]
        t = threading.Thread(target=fn, daemon=True)
        self.workers.append(t)
        t.start()
        return t

    def wait_workers(self, timeout=5.0):
        """Let the threads end (after quit: Stop is pressed for them), so that nothing of Tk is freed outside Tk's thread."""
        end = time.time() + timeout
        for t in list(self.workers):
            t.join(max(0.0, end - time.time()))

    # -- looks ----------------------------------------------------------------------------------

    def _fonts(self):
        base = tkfont.nametofont("TkDefaultFont").actual()
        mono = tkfont.nametofont("TkFixedFont").actual()
        size = abs(int(base.get("size") or 10))
        size = max(size, 10) + (0 if sys.platform == "darwin" else 1)
        fam = base.get("family") or "Helvetica"
        self.fonts = {
            "body": tkfont.Font(root=self.root, family=fam, size=size),
            "bold": tkfont.Font(root=self.root, family=fam, size=size, weight="bold"),
            "italic": tkfont.Font(root=self.root, family=fam, size=size, slant="italic"),
            "small": tkfont.Font(root=self.root, family=fam, size=max(size - 2, 8)),
            "small_bold": tkfont.Font(root=self.root, family=fam, size=max(size - 2, 8), weight="bold"),
            "h1": tkfont.Font(root=self.root, family=fam, size=size + 5, weight="bold"),
            "h2": tkfont.Font(root=self.root, family=fam, size=size + 3, weight="bold"),
            "h3": tkfont.Font(root=self.root, family=fam, size=size + 1, weight="bold"),
            "mono": tkfont.Font(root=self.root, family=mono.get("family") or "Courier", size=max(size - 1, 9)),
            "mono_bold": tkfont.Font(root=self.root, family=mono.get("family") or "Courier", size=max(size - 1, 9), weight="bold"),
        }

    def _style(self):
        st = ttk.Style(self.root)
        names = st.theme_names()
        if sys.platform != "darwin" and os.name != "nt" and "clam" in names:
            st.theme_use("clam")
        c = self.colors
        f = self.fonts
        st.configure(".", font=f["body"])
        st.configure("TFrame", background=c["window"])
        st.configure("TLabel", background=c["window"], foreground=c["fg"])
        st.configure("TCheckbutton", background=c["window"])
        st.configure("TRadiobutton", background=c["window"])
        st.configure("Muted.TLabel", foreground=c["muted"], font=f["small"])
        st.configure("Status.TLabel", foreground=c["muted"], font=f["small"])
        st.configure("Title.TLabel", font=f["h3"])
        st.configure("Good.TLabel", foreground=c["ok"], font=f["small"])
        st.configure("Bad.TLabel", foreground=c["fail"], font=f["small"])
        st.configure("Accent.TButton", foreground="white", background=c["accent"], bordercolor=c["accent_dark"])
        st.map("Accent.TButton", background=[("disabled", "#9db9e6"), ("active", c["accent_dark"])], foreground=[("disabled", "#eef3fb")])
        st.configure("Small.TButton", font=f["small"], padding=(6, 2))
        st.configure("Link.TButton", font=f["small"], foreground=c["link"], background=c["window"], borderwidth=0, padding=(2, 0))
        st.map("Link.TButton", background=[("active", c["window"])], foreground=[("disabled", c["muted"])])
        st.configure("Toolbutton", padding=(14, 4), font=f["bold"])
        st.map("Toolbutton", background=[("selected", c["accent"]), ("active", "#dbe7fb")], foreground=[("selected", "white")])
        st.configure("TNotebook", background=c["window"])
        st.configure("TLabelframe", background=c["window"])
        st.configure("TLabelframe.Label", background=c["window"], font=f["bold"])

    # -- windows ----------------------------------------------------------------------------------

    def open_chat(self, mode=None):
        from .chatwin import ChatWindow

        if self.chat is None:
            self.chat = ChatWindow(self)
        self.chat.show(mode)
        return self.chat

    def toggle_chat(self):
        if self.chat is not None and self.chat.visible():
            self.chat.hide()
        else:
            self.open_chat()

    def open_settings(self, tab=None):
        from .settingswin import SettingsWindow

        if self.settings is None or not self.settings.alive():
            self.settings = SettingsWindow(self)
        self.settings.show(tab)
        return self.settings

    def choose_folder(self):
        parent = self.chat.win if self.chat is not None and self.chat.visible() else self.root
        path = filedialog.askdirectory(parent=parent, initialdir=self.session.folder, title="The folder AskTim! works in")
        if path:
            self.set_folder(path)

    def set_folder(self, path, quiet=False):
        try:
            self.session.set_folder(path)
        except ValueError as e:
            from tkinter import messagebox

            messagebox.showwarning("Not that folder", "AskTim! cannot work in that folder: %s." % e, parent=self.chat.win if self.chat else self.root)
            return False
        self.remember(folder=self.session.folder)
        if self.chat is not None:
            self.chat.folder_changed(quiet)
        return True

    def remember(self, **kw):
        self.state.update(kw)
        backend.save_state(**kw)

    def set_busy(self, busy):
        if self.head is not None:
            self.head.set_busy(busy)

    def refresh(self):
        """The settings changed: show them."""
        if self.chat is not None:
            self.chat.refresh_status()

    def quit(self):
        """Close AskTim!'s window, and so end its main loop. Nothing that fails on the way stops it closing; a
        second call finishes what a first one could not."""
        if self.closed:
            return
        first = not self.quitting
        self.quitting = True
        if first:
            if self.end_process_after:
                self._end_process_later(self.end_process_after)
            places = _guarded(self._places) or {}
            _guarded(self._hide)  # (the head and the chat go at once, whatever happens next)
            _guarded(self._stop_work)
            if places:
                _guarded(lambda: self.remember(**places))
            _guarded(self._stop_timers)
        self._close()

    def _end_process_later(self, seconds):
        """Quit means quit: if the process has not ended a little while after it (Tk, or a thread, hangs), end it."""

        def watch():
            time.sleep(seconds)
            try:
                sys.stderr.write("AskTim!: the window was still running %g seconds after Quit, so it was ended.\n" % seconds)
                sys.stderr.flush()
            except Exception:
                pass
            backend.release_pid()
            os._exit(0)

        threading.Thread(target=watch, name="asktim-quit", daemon=True).start()

    def _places(self):
        """Where the head and the chat are (to come back there next time)."""
        places = {}
        if self.chat is not None:
            try:
                places["chat_geometry"] = self.chat.win.geometry()
            except tk.TclError:
                pass
        if self.head is not None:
            try:
                places["head"] = self.head.position()
            except tk.TclError:
                pass
        return places

    def _hide(self):
        for w in (self.head.win if self.head else None, self.chat.win if self.chat else None):
            if w is not None:
                try:
                    w.withdraw()
                except tk.TclError:
                    pass

    def _stop_work(self):
        if self.chat is not None:
            self.chat.stop_event.set()

    def _stop_timers(self):
        # (what is due later - the bridge's poll, the head's nod - would fire into a Tk that is gone)
        self.bridge.stop()
        if self.head is not None:
            self.head.stop()

    def _close(self):
        _guarded(self._hide)
        try:
            self.root.destroy()
            self.closed = True
        except Exception:
            traceback.print_exc()
            try:
                self.root.quit()  # (Tk could not take it all down: the main loop ends anyway, and with it the process)
            except Exception:
                pass

    def run(self):
        self.root.mainloop()


def main(folder=None, open_chat=False):
    """Show the head (and keep running until it is closed). Returns an exit status."""
    pid = backend.running_pid()
    if pid:
        if hasattr(signal, "SIGUSR1"):
            try:
                os.kill(pid, signal.SIGUSR1)  # (the window that is already running opens its chat)
                return 0
            except OSError:
                pass
        sys.stderr.write("AskTim! is already on your screen.\n")
        return 0
    backend.claim_pid()
    try:
        app = App(folder)
        app.end_process_after = 6.0  # (this process is the window's: a Quit ends it, come what may)
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, lambda *a: app.bridge.post(app.open_chat))
        for sig in ("SIGTERM", "SIGINT", "SIGHUP"):
            if hasattr(signal, sig):
                signal.signal(getattr(signal, sig), lambda *a: app.bridge.post(app.quit))
        if open_chat:
            app.open_chat()
        app.run()
        app.wait_workers(3.0)
        import gc

        gc.collect()  # (in Tk's thread)
    finally:
        backend.release_pid()
    return 0


def log_file():
    return os.path.join(paths.state_dir(), "window.log")
