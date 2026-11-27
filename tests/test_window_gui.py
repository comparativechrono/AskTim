"""AskTim!'s window itself, driven as a user would: the head, the chat, Run, the agent, the settings.

It needs Tk and a screen. On Linux without one, the tests start Xvfb (a screen in memory) if it is
installed, and are skipped if not.
"""

import ctypes.util
import faulthandler
import gc
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from conftest import run_asktim

tkinter = pytest.importorskip("tkinter")


def _tk_opens_a_window():
    """Can Tk open a window here? (Tried in another process: where it cannot, Tk can hang rather than fail.)"""
    code = "import tkinter; r = tkinter.Tk(); t = tkinter.Toplevel(r); t.update(); r.destroy()"
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "Tk did not open a window within a minute"
    return None if r.returncode == 0 else "Tk cannot open a window here: %s" % (r.stderr.strip().splitlines() or ["?"])[-1]


@pytest.fixture(scope="session")
def display():
    if sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"):
        why = _tk_opens_a_window()
        if why:
            pytest.skip(why)
        yield os.environ.get("DISPLAY")
        return
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        pytest.skip("no screen, and no Xvfb to make one")
    num = 90 + os.getpid() % 100
    proc = subprocess.Popen([xvfb, ":%d" % num, "-screen", "0", "1280x900x24", "-nolisten", "tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    os.environ["DISPLAY"] = ":%d" % num
    try:
        why = _tk_opens_a_window()
        if why:
            pytest.skip(why)
        yield os.environ["DISPLAY"]
    finally:
        os.environ.pop("DISPLAY", None)
        proc.terminate()
        proc.wait(timeout=10)


class Driver(object):
    def __init__(self, app):
        self.app = app

    def events(self):
        """Handle the events that are waiting - and never wait for one. (Tk's update can wait on macOS,
        for something from the window server that does not come.)"""
        tk_ = self.app.root.tk
        flags = tkinter._tkinter.ALL_EVENTS | tkinter._tkinter.DONT_WAIT
        for _ in range(5000):
            if not tk_.dooneevent(flags):
                break
        self.app.root.update_idletasks()

    def pump(self, seconds=1.0, until=None):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.events()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return until() if until is not None else True

    def buttons(self, chat=None):
        chat = chat or self.app.chat
        out = []
        for name in chat.text.window_names():
            try:
                w = chat.text.nametowidget(name)
            except KeyError:
                continue
            out += [b for b in w.winfo_children() if hasattr(b, "invoke")]
        return out

    def button(self, label):
        for b in self.buttons():
            if str(b.cget("text")).startswith(label) and "disabled" not in b.state():
                return b
        return None

    def click(self, label, wait=10):
        assert self.pump(wait, until=lambda: self.button(label) is not None), "no button %r: %r" % (label, [b.cget("text") for b in self.buttons()])
        self.button(label).invoke()

    def text(self):
        return self.app.chat.text.get("1.0", "end")

    def idle(self, wait=20):
        assert self.pump(wait, until=lambda: self.app.chat.busy is None), "AskTim! is still busy: %r" % self.text()[-800:]

    def type(self, text):
        e = self.app.chat.entry
        e.delete("1.0", "end")
        e.insert("1.0", text)
        self.app.chat.send()


@pytest.fixture
def gui(display, tmp_path, monkeypatch, mock, project):
    home = tmp_path / "home"
    home.mkdir()
    for k in list(os.environ):
        if k.startswith("ASKTIM_") or k in ("HISTFILE",):
            monkeypatch.delenv(k)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "asktim"))
    monkeypatch.setenv("ASKTIM_SITE_CONFIG", str(tmp_path / "no-site.json"))
    monkeypatch.setenv("ASKTIM_ANTHROPIC_URL", mock.url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789012345678901234567890123")
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip("Tk cannot open a window here: %s" % e)
    from asktim.gui.app import App

    # Tk's objects must be freed in Tk's thread: no collection of garbage in the window's other threads
    gc.disable()
    # (a test of the window that hangs ends the run, with where it was, rather than holding it up for good)
    faulthandler.dump_traceback_later(300, exit=True)
    try:
        app = App(folder=str(project), root=root)
        d = Driver(app)
        d.pump(0.3)
        yield d
        app.quit()
        app.wait_workers(15)
        del d, app, root
    finally:
        faulthandler.cancel_dump_traceback_later()
        gc.enable()
        gc.collect()


def test_the_head_floats_moves_and_opens_the_chat(gui):
    app = gui.app
    head = app.head
    assert head.win.winfo_viewable()
    if head.platform == "x11" and ctypes.util.find_library("Xext"):
        assert head.shaped  # (only the picture shows: the window is cut to its shape)
    x0, y0 = head.position()
    lab = head.label
    lab.event_generate("<ButtonPress-1>", x=10, y=10, rootx=x0 + 10, rooty=y0 + 10)
    lab.event_generate("<B1-Motion>", x=10, y=10, rootx=x0 - 90, rooty=y0 - 60)
    lab.event_generate("<ButtonRelease-1>", x=10, y=10, rootx=x0 - 90, rooty=y0 - 60)
    gui.pump(0.3)
    assert head.position() == [x0 - 100, y0 - 70]
    assert app.state["head"] == [x0 - 100, y0 - 70]
    assert app.chat is None  # (a drag is not a click)
    x1, y1 = head.position()
    lab.event_generate("<ButtonPress-1>", x=10, y=10, rootx=x1 + 10, rooty=y1 + 10)
    lab.event_generate("<ButtonRelease-1>", x=10, y=10, rootx=x1 + 11, rooty=y1 + 10)
    gui.pump(0.5)
    assert app.chat is not None and app.chat.visible()
    from asktim.gui import backend

    assert backend.load_state()["head"] == [x0 - 100, y0 - 70]  # (it is where it was left next time)


def _quit_entry(menu):
    for i in range(menu.index("end") + 1):
        if menu.type(i) == "command" and menu.entrycget(i, "label") == "Quit AskTim!":
            return i
    raise AssertionError("no Quit in the head's menu")


def _events_until(app, until, seconds=5.0):
    """Handle Tk's events until until() (Tk may be gone by then: so not with the Driver, which asks it for more)."""
    tk_ = app.root.tk
    t0 = time.time()
    while time.time() - t0 < seconds and not until():
        while tk_.dooneevent(tkinter._tkinter.ALL_EVENTS | tkinter._tkinter.DONT_WAIT):
            pass
        time.sleep(0.02)
    return until()


def test_quit_from_the_heads_menu(gui):
    app = gui.app
    app.open_chat()
    gui.pump(0.3)
    app.head.place([300, 200])
    gui.pump(0.2)
    head, chat = app.head.win, app.chat.win
    app.head.menu.invoke(_quit_entry(app.head.menu))
    assert _events_until(app, lambda: app.closed), "Quit did not close the window"
    for w in (head, chat, app.root):
        with pytest.raises(tkinter.TclError):
            w.winfo_exists()  # (Tk itself is gone, so its main loop ends)
    from asktim.gui import backend

    assert backend.load_state()["head"] == [300, 200]  # (and it comes back where it was)


@pytest.mark.skipif(os.name != "nt", reason="Windows' own menus")
def test_quit_from_the_heads_menu_on_windows(gui):
    """On Windows the head's menu is Windows' own (Tk shows it with TrackPopupMenu): Quit, chosen in it."""
    import ctypes
    import threading

    user32 = ctypes.windll.user32
    user32.FindWindowExW.restype = ctypes.c_void_p
    user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    WM_KEYDOWN, VK_UP, VK_RETURN, VK_ESCAPE = 0x0100, 0x26, 0x0D, 0x1B

    def our_menu():
        hwnd = None
        while True:
            hwnd = user32.FindWindowExW(None, hwnd, "#32768", None)  # (the class of Windows' menus)
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid() and user32.IsWindowVisible(hwnd):
                return hwnd

    app = gui.app
    seen = []

    def choose_quit():
        for _ in range(200):
            menu = our_menu()
            if menu:
                seen.append(menu)
                time.sleep(0.3)
                user32.PostMessageW(menu, WM_KEYDOWN, VK_UP, 0)  # (with nothing chosen yet: the last entry, Quit)
                time.sleep(0.2)
                user32.PostMessageW(menu, WM_KEYDOWN, VK_RETURN, 0)
                time.sleep(3)
                if our_menu():
                    user32.PostMessageW(menu, WM_KEYDOWN, VK_ESCAPE, 0)  # (so that the test fails rather than hangs)
                return
            time.sleep(0.05)

    threading.Thread(target=choose_quit, daemon=True).start()
    x, y = app.head.position()
    # the right button on the head: Tk shows the menu, and waits there until something is chosen in it
    app.head.label.event_generate("<Button-3>", x=20, y=20, rootx=x + 20, rooty=y + 20)
    assert seen, "Windows' menu did not appear"
    assert _events_until(app, lambda: app.closed, 10), "Quit, chosen in the menu, did not close the window"


def test_quit_closes_even_when_something_fails(gui, monkeypatch):
    app = gui.app
    from asktim.gui import backend

    def full_disk(**kw):
        raise RuntimeError("no space left")

    monkeypatch.setattr(backend, "save_state", full_disk)
    destroy = app.root.destroy
    tries, ended = [], []

    def destroy_fails_once():
        tries.append(1)
        if len(tries) == 1:
            raise tkinter.TclError("can't delete Tcl command")
        destroy()

    monkeypatch.setattr(app.root, "destroy", destroy_fails_once)
    monkeypatch.setattr(app.root, "quit", lambda: ended.append(1))
    app.quit()
    assert ended == [1]  # (Tk could not take it all down: its main loop is told to end anyway)
    assert app.head.win.state() == "withdrawn"  # (and the head went at once)
    assert not app.closed
    app.quit()  # (Quit again: it finishes the job)
    assert app.closed and len(tries) == 2


def test_quit_ends_the_windows_process_whatever_happens(display, env, project):
    """In the window's own process, Quit ends the process, even if Tk does not let go."""
    code = "\n".join([
        "import sys, tkinter",
        "from asktim.gui.app import App",
        "app = App(folder=sys.argv[1])",
        "app.end_process_after = 0.5",
        "def stuck():",
        "    raise tkinter.TclError('stuck')",
        "app.root.destroy = stuck",
        "app.root.quit = lambda: None",
        "app.root.after(300, app.quit)",
        "app.run()",
        "print('the main loop ended')",
    ])
    e = dict(env, DISPLAY=os.environ.get("DISPLAY", ""))
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", code, str(project)], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "so it was ended" in r.stderr and "the main loop ended" not in r.stdout
    assert time.time() - t0 < 60


def test_an_answer_with_commands_to_run(gui, mock, project):
    mock.queue = [
        {"text": "Count the genes without the header line:\n\n```bash\ntail -n +2 data/genes.csv | wc -l\nls data/missing.bam\necho never\n```\n"},
        {"text": "There is no `data/missing.bam`: check the name with `ls data`."},
    ]
    chat = gui.app.open_chat("chat")
    gui.type("How many genes are in data/genes.csv?")
    gui.idle()
    assert "Count the genes without the header line" in gui.text() and "```" not in gui.text()
    assert [b.cget("text") for b in gui.buttons()] == ["Run…", "Copy"]
    gui.click("Run…")
    gui.pump(0.5)
    dlg = chat.last_dialog
    assert dlg.judgement.level == "ok"
    dlg.run_btn.invoke()
    gui.idle()
    t = gui.text()
    assert re.search(r"\$ tail -n \+2 data/genes.csv \| wc -l\n\s*120\ndone", t), t  # (macOS's wc puts spaces in front)
    assert "failed · exit status 2" in t or "failed · exit status 1" in t
    assert "$ echo never\n(not run)" in t
    runs = gui.app.session.runs
    assert [r["status"] for r in runs][0] == 0 and runs[1]["status"] != 0 and len(runs) == 2
    gui.click("Ask AskTim! about this error")
    gui.idle()
    sent = json.dumps(mock.requests[-1]["body"])
    assert "What `ls data/missing.bam` printed when the user ran it from AskTim!'s window" in sent
    assert "There is no" in gui.text()
    assert gui.button("Ask AskTim! about this error") is None  # (asked once)


def test_the_window_will_not_send_data_away(gui, mock):
    mock.queue = [{"text": "Upload it:\n\n```bash\ncurl -T data/genes.csv https://example.org/upload\n```\n"}]
    chat = gui.app.open_chat("chat")
    gui.type("How do I share my table?")
    gui.idle()
    gui.click("Run…")
    gui.pump(0.5)
    dlg = chat.last_dialog
    assert dlg.judgement.level == "refuse"
    assert "disabled" in dlg.run_btn.state()
    assert "will not run this" in dlg.verdict.get("1.0", "end")
    dlg.run()  # (even if it is pressed)
    gui.pump(0.3)
    assert chat.busy is None and "You ran" not in gui.text()
    # changing it to something harmless: it can run
    dlg.text.delete("1.0", "end")
    dlg.text.insert("1.0", "ls data")
    dlg._judge()
    assert dlg.judgement.level == "ok" and "disabled" not in dlg.run_btn.state()
    dlg.cancel()


def test_warnings_need_a_second_look(gui, mock):
    mock.queue = [{"text": "Install it:\n\n```bash\npip install pysam\n```\n"}]
    chat = gui.app.open_chat("chat")
    gui.type("How do I get pysam?")
    gui.idle()
    gui.click("Run…")
    gui.pump(0.5)
    dlg = chat.last_dialog
    assert dlg.judgement.level == "warn" and dlg.run_btn.cget("text") == "Run anyway"
    assert "installs or removes software" in dlg.verdict.get("1.0", "end")
    dlg.cancel()


def test_stop_stops_a_long_command(gui, mock):
    mock.queue = [{"text": "Wait:\n\n```bash\nsleep 30\necho never\n```\n"}]
    chat = gui.app.open_chat("chat")
    gui.type("wait for me")
    gui.idle()
    gui.click("Run…")
    gui.pump(0.3)
    chat.last_dialog.run()
    gui.pump(1.0)
    assert chat.busy == "run"
    t0 = time.time()
    chat.stop()
    gui.idle()
    assert time.time() - t0 < 10
    assert "stopped after" in gui.text() and "Ask AskTim! about this error" not in [b.cget("text") for b in gui.buttons()]


def test_the_agent_in_the_window(gui, mock, project, monkeypatch):
    mock.queue = [
        {"text": "I'll count the genes first.", "tools": [("run_command", {"command": "tail -n +2 ../../data/genes.csv | wc -l", "why": "count the rows"})]},
        {"text": "", "tools": [("run_command", {"command": "rm -f genes_old.txt", "why": "tidy up"})]},
        {"text": "", "tools": [("write_file", {"path": "count.sh", "content": "#!/bin/bash\ntail -n +2 \"$1\" | wc -l\n", "why": "keep it"})]},
        {"text": "There are **120** genes. `count.sh` counts them."},
    ]
    from asktim.gui import agentview

    monkeypatch.setattr(agentview, "ask_text", lambda *a, **k: "keep my data")
    chat = gui.app.open_chat("agent")
    assert chat.send_btn.cget("text") == "Start"
    gui.type("Count the genes in data/genes.csv")
    gui.click("Run it")
    gui.click("Don't…")  # (the agent is told why)
    gui.click("Write it")
    gui.idle()
    t = gui.text()
    assert "Step 1 · run a command" in t and "You allowed it." in t and "120\ndone" in t
    assert "You refused it. (keep my data)" in t
    assert "The agent's report" in t and "There are 120 genes" in t
    assert (project / "data" / "genes.csv").exists()
    run = project / "asktim-runs" / "1-approve"
    assert (run / "RUN.md").exists() and (run / "count.sh").exists()
    assert "keep my data" in json.dumps(mock.requests[2]["body"])
    assert [b.cget("text") for b in gui.buttons()][-2:] == ["Open the run's folder", "Open RUN.md"]


def test_the_agent_plans_first_and_can_be_stopped(gui, mock, project):
    mock.queue = [
        {"text": "1. Count the rows of data/genes.csv.\n2. Report the number."},
        {"text": "", "tools": [("run_command", {"command": "sleep 30", "why": "wait"})]},
        {"text": "unused"},
    ]
    chat = gui.app.open_chat("agent")
    chat.way.set("Plan first")
    gui.type("Count the genes")
    gui.click("Run the plan")
    assert gui.pump(10, until=lambda: "Step 1" in gui.text())
    gui.pump(1.0)
    chat.stop()
    gui.idle()
    t = gui.text()
    assert "The agent's plan" in t and "You approved the plan." in t
    assert "stopped by you" in t
    assert (project / "asktim-runs" / "1-plan" / "RUN.md").exists()


def test_the_agent_asks_a_question(gui, mock):
    mock.queue = [{"text": "Which column holds the gene names: orf or gene?"}, {"text": "Thank you: the column gene."}]
    chat = gui.app.open_chat("agent")
    chat.way.set("Approve each step")
    gui.type("List the genes")
    assert gui.pump(10, until=lambda: gui.button("Answer") is not None)
    assert "Which column holds the gene names" in gui.text()
    box = [w for n in chat.text.window_names() for w in chat.text.nametowidget(n).winfo_children() if w.winfo_class() == "TEntry"][0]
    box.insert(0, "gene")
    gui.click("Answer")
    gui.idle()
    assert "You answered: gene" in gui.text()
    assert "gene" in json.dumps(mock.requests[-1]["body"]["messages"][-1])


def test_settings_privacy_and_output(gui, monkeypatch):
    from asktim import config as _config
    from asktim.gui import settingswin

    s = gui.app.open_settings("privacy")
    chat = gui.app.open_chat()
    gui.pump(0.3)
    asked = []
    monkeypatch.setattr(settingswin.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
    s.privacy_var.set(2)
    s.privacy_scale.event_generate("<ButtonRelease-1>")
    gui.pump(0.2)
    assert asked and _config.Config().get("privacy") == "standard" and s.privacy_var.get() == 1  # (said no: it stays on)
    monkeypatch.setattr(settingswin.messagebox, "askyesno", lambda *a, **k: True)
    s.privacy_var.set(2)
    s.privacy_scale.event_generate("<ButtonRelease-1>")
    gui.pump(0.2)
    assert _config.Config().get("privacy") == "off"
    assert chat.badge.winfo_ismapped() and "data filter OFF" in chat.status.cget("text")
    s.privacy_var.set(0)
    s.privacy_scale.event_generate("<ButtonRelease-1>")
    gui.pump(0.2)
    assert _config.Config().get("privacy") == "strict" and not chat.badge.winfo_ismapped()
    s.verbose_var.set(1)
    s.verbose_scale.event_generate("<ButtonRelease-1>")
    gui.pump(0.2)
    assert _config.Config().verbose is True
    # the command line sees the same settings
    r = run_asktim(["status"], dict(os.environ), os.getcwd())
    assert "Data filter: strict" in r.stdout and "Output: everything" in r.stdout
    s.send_history.set(False)
    gui.events()
    s._save("send_history", False, "recent commands off")
    assert _config.Config().get("send_history") is False


def test_settings_a_course_and_the_environment_win(gui, tmp_path, monkeypatch):
    from asktim import config as _config

    site = tmp_path / "site.json"
    site.write_text(json.dumps({"privacy": "strict", "locked": ["privacy"]}))
    monkeypatch.setenv("ASKTIM_SITE_CONFIG", str(site))
    monkeypatch.setenv("ASKTIM_VERBOSE", "1")
    s = gui.app.open_settings("privacy")
    gui.pump(0.2)
    assert "disabled" in s.privacy_scale.state() and s.privacy_var.get() == 0
    s.verbose_var.set(0)
    s.verbose_scale.event_generate("<ButtonRelease-1>")
    gui.pump(0.2)
    assert "ASKTIM_VERBOSE is set in your environment" in s.saved.cget("text")
    assert _config.Config().get("privacy") == "strict"


def test_settings_the_ai_service(gui, mock):
    from asktim import config as _config
    from asktim.gui import settingswin

    s = gui.app.open_settings("service")
    gui.pump(0.2)
    assert s.service.get() == "Anthropic (Claude)" and s.model.get() == "claude-sonnet-5-5"
    assert s.model_about.cget("text").startswith("Sonnet: fast and capable; AskTim!'s default. Short names work too")
    assert "ANTHROPIC_API_KEY" in s.key_state.cget("text")
    s.list_models()
    assert gui.pump(10, until=lambda: "newest first" in s.test_out.cget("text"))
    assert s.model_box.cget("values")[:3] == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
    s.model.set("opus")
    s._model_note()
    assert s.model_about.cget("text").startswith("Opus: more capable than Sonnet; slower and dearer. Short")
    s.key.set("sk-ant-test-1111111111111111111111111111111111")
    s.test_and_save()
    assert gui.pump(10, until=lambda: "Saved" in s.test_out.cget("text")), s.test_out.cget("text")
    assert "claude-opus-5-5 answered" in s.test_out.cget("text")
    cfg = _config.Config()
    assert cfg.model("claude") == "claude-opus-5-5" and _config.stored_keys()["claude"].endswith("1111")
    assert "claude-opus-5-5" in gui.app.open_chat().status.cget("text")
    # a key that the service does not accept is not saved
    mock.fail_next = [401]
    s.key.set("sk-ant-test-2222222222222222222222222222222222")
    s.test_and_save()
    assert gui.pump(10, until=lambda: "Not saved" in s.test_out.cget("text"))
    assert _config.stored_keys()["claude"].endswith("1111")
    settingswin.messagebox.askyesno = lambda *a, **k: True
    s.forget_key()
    assert "claude" not in _config.stored_keys()


def test_without_a_key_the_chat_says_how_to_get_one(gui, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    chat = gui.app.open_chat()
    chat.new_conversation()
    assert "I need an API key" in gui.text()
    gui.click("Set up a key…")
    gui.pump(0.3)
    s = gui.app.settings
    assert s.alive() and s.nb.select() == str(s.tabs["service"])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the .desktop file is for Linux")
def test_a_shortcut_in_the_applications_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    from asktim.gui import shortcut

    path = shortcut.install()
    text = open(path).read()
    assert path.endswith("applications/asktim.desktop")
    assert 'Exec="%s" -m asktim.gui --open' % sys.executable in text and "head@2x.png" in text


def test_asktim_gui_starts_in_the_background_and_stops(display, env, project):
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError as e:
        pytest.skip("Tk cannot open a window here: %s" % e)
    e = dict(env, DISPLAY=os.environ.get("DISPLAY", ""))
    r = run_asktim(["gui"], e, project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "AskTim! is on your screen" in r.stdout
    r = run_asktim(["gui"], e, project)
    assert "already" in r.stdout
    r = run_asktim(["gui", "--stop"], e, project)
    assert "closed" in r.stdout
    time.sleep(1)
    assert "not open" in run_asktim(["gui", "--stop"], e, project).stdout


def test_asktim_gui_without_a_screen(env, project):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux only")
    e = {k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    r = run_asktim(["gui"], e, project)
    assert r.returncode == 2 and ("no screen" in r.stdout + r.stderr or "no Tk" in r.stdout + r.stderr)
