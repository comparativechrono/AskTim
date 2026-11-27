"""AskTim!'s window itself, driven as a user would: the head, the chat, Run, the agent, the settings.

It needs Tk and a screen. On Linux without one, the tests start Xvfb (a screen in memory) if it is
installed, and are skipped if not.
"""

import ctypes.util
import gc
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import run_asktim

tkinter = pytest.importorskip("tkinter")


@pytest.fixture(scope="session")
def display():
    if sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"):
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
        yield os.environ["DISPLAY"]
    finally:
        os.environ.pop("DISPLAY", None)
        proc.terminate()
        proc.wait(timeout=10)


class Driver(object):
    def __init__(self, app):
        self.app = app

    def pump(self, seconds=1.0, until=None):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.app.root.update()
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
    try:
        app = App(folder=str(project), root=root)
        d = Driver(app)
        d.pump(0.3)
        yield d
        app.quit()
        app.wait_workers(15)
        del d, app, root
    finally:
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
    assert "$ tail -n +2 data/genes.csv | wc -l\n120\ndone" in t
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
    s.top.nametowidget(str(s.tabs["privacy"])).update()
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
    assert "ANTHROPIC_API_KEY" in s.key_state.cget("text")
    s.list_models()
    assert gui.pump(10, until=lambda: "newest first" in s.test_out.cget("text"))
    assert s.model_box.cget("values")[:3] == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
    s.model.set("opus")
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
