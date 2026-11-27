"""What AskTim!'s window does, without the window: its conversation, the commands of an answer
(checked, then run in the user's folder) and the AI service's settings. Nothing here needs Tk,
so all of it can be tested on its own; gui/app.py only shows it.

The commands of an answer run the way the practicals' Run button put them into a terminal: one
after the other, in one bash (so a `cd` or a variable lasts to the end of the block), stopping at
the first that fails. AskTim! checks them first with the agent's checks. What the agent may not do
is shown to the user as a warning - they read the commands and decide (installing a package, say) -
except what never runs from the window: anything that sends data to another computer, code
straight from the internet, commands that hide what they are, reading credentials, and changes
outside the window's folder (and /tmp), to disks, to the system or as another user. Those the user
can still run in a terminal, if they are sure.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid

from .. import config as _config
from .. import context, inventory, logbook, paths, privacy, prompts, runner, safety
from ..providers import Conversation, ProviderError, make_provider
from ..render import code_blocks

SHELL_LANGS = ("bash", "sh", "shell", "console", "zsh", "shell-session", "")

# what never runs from the window, whoever allows it (the reasons of safety.check that say so):
_NEVER = (
    # data leave the computer
    "send data", "uploading is never allowed", "connects to other computers", "to or from another computer",
    "sends work to another computer", "would be made when the command runs", "would read what it sends from a file",
    "credentials or private history",
    # code that nobody could read first, or that hides what it is
    "run code from the internet straight away", "invisible control characters",
    # changes outside the window's folder (and /tmp), to devices or the system, or as another user
    "outside your folder", "would write to a device", "fork bomb", "as another user", "changes the system or other programs",
)
# programs that change disks or what is mounted
_DISKS = ("mkfs", "mke2fs", "fdisk", "sfdisk", "gdisk", "parted", "wipefs", "mkswap", "swapon", "swapoff", "mount", "umount", "losetup")

# programs that wait for someone to type: in the window they get no input
_NEEDS_TERMINAL = {"nano", "vim", "vi", "nvim", "emacs", "pico", "joe", "less", "more", "most", "man", "top", "htop", "btop", "watch",
                   "ssh", "sftp", "ftp", "telnet", "mysql", "psql", "sqlite3", "ipython", "tmux", "screen", "passwd", "su", "sudo"}
# ... and these when they are started on their own (a prompt to type at)
_REPLS = {"python", "python3", "R", "bash", "sh", "zsh", "fish", "node", "julia", "perl", "irb", "ghci"}


class Stopped(Exception):
    """The user pressed Stop."""


# ------------------------------------------------------------------------------------------------
# the commands of an answer
# ------------------------------------------------------------------------------------------------

_PROMPT = re.compile(r"^\s*(?:\([\w.-]+\)\s*)?(?:[\w.@-]+:[~\w/.-]*)?[$%] ")


def shell_blocks(markdown):
    """[(lang, code)] for the blocks of an answer that hold shell commands."""
    return [(lang, code) for lang, code in code_blocks(markdown) if lang in SHELL_LANGS and split_commands(code)]


def _balanced(text):
    """A rough test, for computers without bash: no open quote, loop or line continuation."""
    t = re.sub(r"'[^']*'", "", text.replace('\\"', ""))
    if t.count("'") % 2 or t.count('"') % 2:
        return False
    words = re.findall(r"(?<![\w-])(do|done|if|fi|case|esac)(?![\w-])", t)
    return words.count("do") <= words.count("done") and words.count("if") <= words.count("fi") and words.count("case") <= words.count("esac")


_HEREDOC = re.compile(r"<<(-?)[ \t]*\\?(['\"]?)([A-Za-z_][\w.-]*)\2")


def _heredocs_in(line):
    """The here-documents that a line starts: [(tabs are stripped, the word that ends it)]. A << inside
    quotes, a here-string (<<<) and a comment start none."""
    out, i, n, quote = [], 0, len(line), None
    while i < n:
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
        elif c in "'\"":
            quote = c
            i += 1
        elif c == "\\":
            i += 2
        elif c == "#" and (i == 0 or line[i - 1] in " \t;&|("):
            break
        elif line.startswith("<<<", i):
            i += 3
        elif line.startswith("<<", i):
            m = _HEREDOC.match(line, i)
            if m:
                out.append((m.group(1) == "-", m.group(3)))
                i = m.end()
            else:
                i += 2
        else:
            i += 1
    return out


def _open_heredocs(text):
    """The here-documents of text that still wait for the line that ends them (bash 3.2, macOS's, does
    not warn about an open one)."""
    waiting = []
    for ln in text.split("\n"):
        if waiting:
            strip_tabs, word = waiting[0]
            if (ln.lstrip("\t") if strip_tabs else ln) == word:
                waiting.pop(0)
            continue
        waiting += _heredocs_in(ln)
    return waiting


def complete(text):
    """Is this a whole shell command, not an unfinished loop, quote or here-document?"""
    if text.rstrip().endswith("\\") or _open_heredocs(text):
        return False
    sh = shutil.which("bash")
    if not sh:
        return _balanced(text)
    try:
        r = subprocess.run([sh, "-n"], input=text + "\n", capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return _balanced(text)
    err = r.stderr or ""
    if "delimited by end-of-file" in err or "unexpected end of file" in err or "unexpected EOF" in err:
        return False
    return True


def split_commands(code):
    """The commands of a code block, each one complete (a loop, a here-document, a line that ends
    with \\ stay whole). In a block that shows a terminal - a $ before each command - only the
    commands are kept; the lines without a $ are what they printed. Comments and blank lines go."""
    lines = (code or "").replace("\r\n", "\n").split("\n")
    prompted = any(_PROMPT.match(ln) for ln in lines)
    out, cur = [], []
    for ln in lines:
        if not cur:
            if prompted:
                m = _PROMPT.match(ln)
                if not m:
                    continue  # output shown in the block
                ln = ln[m.end():]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
        cur.append(ln)
        if complete("\n".join(cur)):
            out.append("\n".join(cur).strip())
            cur = []
    if cur:
        out.append("\n".join(cur).strip())
    return [c for c in out if c]


def needs_terminal(command):
    """The program in this command that waits for someone to type, or None."""
    try:
        words = shlex.split(command.split("\n")[0], comments=True)
    except ValueError:
        words = command.split()
    for i, w in enumerate(words):
        if re.match(r"^[A-Za-z_]\w*=", w) or w in ("env", "time", "nice", "command", "exec"):
            continue
        name = os.path.basename(w)
        if name in _NEEDS_TERMINAL:
            return name
        rest = [x for x in words[i + 1:] if x not in ("|", "&&", "||", ";")]
        if name in _REPLS and not rest:
            return name
        return None
    return None


class Judgement(object):
    """What AskTim! thinks of commands that the user is about to run from the window."""

    def __init__(self):
        self.level = "ok"  # ok | warn | refuse
        self.never = []  # why they will not run from the window
        self.warnings = []  # what the agent would not be allowed to do, and other things to read
        self.notes = []

    def __repr__(self):
        return "Judgement(%s, never=%r, warnings=%r)" % (self.level, self.never, self.warnings)


def _for_user(reason):
    """A reason of the agent's checks, as it applies to the user's own commands."""
    r = re.sub(r";? the agent may use only what is installed - say in the report what would be needed instead", "", reason)
    r = re.sub(r"\s*\(only your folder and /tmp may be written\)", "", r)
    r = re.sub(r" - say in the report[^;]*", "", r)
    return r


def judge(commands, folder):
    j = Judgement()
    text = "\n".join(commands)
    v = safety.check(text, folder, allow_network=True, student_dir=folder, cwd=folder)
    for r in v.reasons:
        if any(w in r for w in _NEVER):
            j.never.append(_for_user(r))
        else:
            j.warnings.append(_for_user(r))
    disks = sorted(set(c for c in v.commands if c.startswith(_DISKS)))
    if disks:
        j.never.append("%s would change the computer's disks, or what is mounted" % ", ".join(disks))
    for c in commands:
        name = needs_terminal(c)
        if name and not any(name in w for w in j.warnings):
            j.warnings.append("%s waits for you to type, and in this window it gets no input: run it in a terminal" % name)
    j.notes = list(v.notes)
    j.level = "refuse" if j.never else "warn" if j.warnings else "ok"
    return j


class RunResult(object):
    def __init__(self, command):
        self.command = command
        self.status = None  # None: not run (an earlier command failed, or the run was stopped)
        self.seconds = 0.0
        self.lines = []  # what it printed (the first and last lines of long output)
        self.dropped = 0

    def text(self):
        if not self.dropped:
            return "".join(self.lines)
        head, tail = self.lines[:200], self.lines[200:]
        return "".join(head) + "[… %d lines not kept …]\n" % self.dropped + "".join(tail)


def run_commands(commands, folder, on_event=None, stop=None, keep=400):
    """Run the commands one after the other in one bash, in folder, and stop at the first that fails.
    on_event(kind, i, data) is called as it goes: ("output", i, text), ("done", i, result) and, at the
    end, ("folder", None, the folder the commands ended in). Returns [RunResult]."""
    results = [RunResult(c) for c in commands]
    nonce = uuid.uuid4().hex[:16]
    # (the mark has a random part, so that nothing a command prints can pass for it)
    mark = re.compile("\x1eASKTIM-%s (\\d+) (-?\\d+) (.*?)\x1e" % nonce)
    lines = ["exec 2>&1"]
    for i, c in enumerate(commands):
        lines.append(c)
        lines.append("__asktim_s=$?; printf '\\036ASKTIM-%%s %%s %%s %%s\\036\\n' %s %d \"$__asktim_s\" \"$PWD\"; "
                     "[ \"$__asktim_s\" -eq 0 ] || exit \"$__asktim_s\"" % (nonce, i))
    script = "\n".join(lines) + "\n"
    state = {"i": 0, "t": time.time(), "cwd": folder}

    def emit(kind, i, data):
        if on_event is not None:
            try:
                on_event(kind, i, data)
            except Exception:
                pass

    def add(i, text):
        if not text or i >= len(results):
            return
        r = results[i]
        if len(r.lines) < 200:
            r.lines.append(text)
        else:
            r.lines.append(text)
            if len(r.lines) > 200 + keep:
                r.lines.pop(200)
                r.dropped += 1
        emit("output", i, text)

    def on_output(_stream, text):
        while text:
            m = mark.search(text)
            if not m:
                add(state["i"], text)
                return
            before = text[:m.start()]
            if before:
                add(state["i"], before + ("\n" if not before.endswith("\n") else ""))
            i, status, cwd = int(m.group(1)), int(m.group(2)), m.group(3)
            if i < len(results):
                now = time.time()
                results[i].status = status
                results[i].seconds = now - state["t"]
                state["t"] = now
                state["cwd"] = cwd or state["cwd"]
                emit("done", i, results[i])
                state["i"] = i + 1
            text = text[m.end():].lstrip("\n")

    res = runner.run(script, folder, timeout=0, sandbox=None, on_output=on_output, stop=stop)
    i = state["i"]
    ran_on = i == 0 or results[i - 1].status == 0  # (after a failure the script ended: the next command never started)
    if i < len(results) and results[i].status is None and ran_on and (res.exit_code not in (0, None) or res.interrupted):
        # this command ended the shell (exit, set -e, a signal) or was stopped
        results[i].status = res.exit_code if not res.interrupted else -15
        results[i].seconds = time.time() - state["t"]
        if res.error:
            add(i, res.error + "\n")
        emit("done", i, results[i])
    emit("folder", None, state["cwd"])
    return results


# ------------------------------------------------------------------------------------------------
# the conversation
# ------------------------------------------------------------------------------------------------


class Session(object):
    """The window's conversation, its folder, and the commands run from it."""

    def __init__(self, folder=None):
        self.folder = os.path.realpath(folder or os.getcwd())
        if os.path.dirname(self.folder) == self.folder:
            self.folder = os.path.realpath(os.path.expanduser("~"))  # (not the top of the computer: see set_folder)
        self.conv = Conversation()
        self.runs = []  # {"command", "status", "folder"}: for the context of the next questions
        self.lock = threading.Lock()

    def set_folder(self, folder):
        folder = os.path.realpath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            raise ValueError("%s is not a folder" % folder)
        if os.path.dirname(folder) == folder:
            # (commands may change files in the window's folder: the whole computer is not a project folder)
            raise ValueError("choose a project's folder, not the top of the computer (%s)" % folder)
        self.folder = folder

    def new_conversation(self):
        self.conv = Conversation()

    def record_runs(self, results):
        for r in results:
            if r.status is not None:
                self.runs.append({"command": r.command, "status": r.status, "folder": self.folder})
        del self.runs[:-50]

    def preview(self, question, output=None, output_of=None):
        """(system prompt, message) as they would be sent."""
        cfg = _config.Config()
        q_clean, _n, _pasted = privacy.clean_question(question, strict=cfg.strict, share_data=cfg.share_data)
        system = prompts.chat_system(inventory.describe(inventory.gather()), cfg, window=True)
        g = context.terminal(cfg, stdin_text=output, cwd=self.folder, window_runs=self.runs, output_of=output_of)
        return system, g.text() + "Question: " + q_clean

    def ask(self, question, on_text=None, on_status=None, stop=None, output=None, output_of=None, on_sent=None):
        """Ask the AI; returns (turn, gathered context, provider). Raises ProviderError or Stopped.
        output, output_of: what a command run from the window printed, and (command, status)."""
        cfg = _config.Config()
        q_clean, n_q, pasted = privacy.clean_question(question, strict=cfg.strict, share_data=cfg.share_data)
        prov = make_provider(cfg)
        inv = inventory.gather()
        system = prompts.chat_system(inventory.describe(inv), cfg, window=True)
        g = context.terminal(cfg, stdin_text=output, cwd=self.folder, window_runs=self.runs, output_of=output_of)
        g.redactions += n_q
        g.withheld += pasted
        if on_sent is not None:
            on_sent(g, prov, pasted)

        def emit(delta):
            if stop is not None and stop.is_set():
                raise Stopped()
            if on_text is not None and delta:
                on_text(delta)

        with self.lock:
            conv = self.conv
            conv.user(g.text() + "Question: " + q_clean)
            try:
                turn = prov.complete(system, conv, purpose="chat", on_text=emit, on_status=on_status)
            except BaseException:
                conv.items.pop()  # not answered: the question is not part of the conversation
                raise
            if stop is not None and stop.is_set():
                conv.items.pop()
                raise Stopped()
            # the conversation keeps the question, without the context sent with it
            conv.items[-1] = {"kind": "user", "text": "Question: " + q_clean}
            conv.assistant(turn)
        logbook.save_suggested(code_blocks(turn.text))
        logbook.add({"mode": "chat", "via": "window", "question": q_clean, "model": turn.model, "provider": prov.name, "sent": g.sent,
                     "cwd": paths.pretty(self.folder)}, cfg)
        return turn, g, prov


# ------------------------------------------------------------------------------------------------
# the AI service
# ------------------------------------------------------------------------------------------------


def key_status(provider):
    """(the key or None, a line saying where it comes from) for the settings window."""
    key, where = _config.find_key(provider)
    if not key:
        return None, "No key yet."
    return key, "A key is in use (%s, %s)." % (_config.mask(key), where)


def test_service(provider, model, key=None, base_url=None):
    """Ask the service for one word, as `AskTim! Claude` does. Returns the model that answered;
    raises ProviderError."""
    cfg = _config.Config({"openai_base_url": base_url} if base_url else None)
    p = make_provider(cfg, provider=provider, model=model or None, key=key or None)
    conv = Conversation()
    conv.user("Reply with the single word: ready")
    turn = p.complete("You are being tested for a connection. Reply with one word.", conv, purpose="chat")
    return turn.model or p.model


def save_service(provider, model, key=None, base_url=None):
    """Keep the service, model, address and key, for the window, the command line and notebooks.
    Returns the settings that the course fixes (and that were therefore not changed)."""
    cfg = _config.Config()
    fixed = []
    if provider == "openai" and base_url:
        if cfg.is_locked("openai_base_url"):
            fixed.append("the address")
        else:
            _config.save_user_setting("openai_base_url", base_url.rstrip("/"))
    if model:
        if cfg.is_locked("models." + provider):
            fixed.append("the model")
        else:
            _config.save_user_setting("models." + provider, model)
    if key:
        _config.save_key(provider, key)
    if cfg.provider != provider:
        if cfg.is_locked("provider"):
            fixed.append("the AI service")
        else:
            _config.save_user_setting("provider", provider)
    return fixed


def setting_from_environment(key):
    """The environment variable that overrides a setting (it wins over the settings file), or None."""
    for env, k in _config.ENV_SETTINGS.items():
        if k == key and os.environ.get(env):
            return env
    if key.startswith("models.") and os.environ.get("ASKTIM_MODEL"):
        return "ASKTIM_MODEL"
    return None


# ------------------------------------------------------------------------------------------------
# what the window remembers between sessions (where the head is ...), and the one window per user
# ------------------------------------------------------------------------------------------------


def _state_file():
    return os.path.join(paths.state_dir(), "window.json")


def load_state():
    try:
        with open(_state_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**changes):
    data = load_state()
    data.update(changes)
    try:
        paths.ensure(paths.state_dir(), private=True)
        tmp = _state_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, _state_file())
    except OSError:
        pass


def _pid_file():
    return os.path.join(paths.state_dir(), "window.pid")


def running_pid():
    """The process id of an AskTim! window that is running already, or None."""
    try:
        with open(_pid_file()) as f:
            pid = int(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if pid == os.getpid() or not _alive(pid):
        return None
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            if b"asktim" not in f.read().lower():
                return None  # (the number now belongs to another program)
    except OSError:
        pass
    return pid


def _alive(pid):
    """Is process pid running? (Not with os.kill(pid, 0) on Windows: there that ends the process.)"""
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def claim_pid():
    try:
        paths.ensure(paths.state_dir(), private=True)
        with open(_pid_file(), "w") as f:
            f.write("%d\n" % os.getpid())
    except OSError:
        pass


def release_pid():
    try:
        with open(_pid_file()) as f:
            if int(f.read().split()[0]) != os.getpid():
                return
        os.remove(_pid_file())
    except (OSError, ValueError, IndexError):
        pass


def describe_service(cfg=None):
    cfg = cfg or _config.Config()
    name = {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI-compatible"}[cfg.provider]
    return "%s · %s" % (name, cfg.model())


__all__ = ["Session", "Stopped", "split_commands", "shell_blocks", "judge", "run_commands", "needs_terminal", "test_service",
           "save_service", "key_status", "load_state", "save_state", "ProviderError"]
