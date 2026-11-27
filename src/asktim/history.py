"""The user's recent shell commands (never their output).

Two sources, best first:

1. AskTim!'s shell hook (installed with `AskTim! setup-shell`): after every command the
   shell appends one line - time, exit status, folder, command - to
   ~/.local/state/asktim/commands.tsv. It knows which command failed, and sees commands
   as soon as they have run.
2. The shell's own history file (~/.bash_history, ~/.zsh_history, fish's history). Bash
   writes it only when a terminal closes, so the latest commands may be missing; there is
   no exit status.

AskTim!'s own commands are left out (they hold questions, and perhaps a key). Everything
returned here still goes through privacy.clean() before it is sent.
"""

import os
import re
import sys
import time

from . import paths


class Command(object):
    def __init__(self, line, status=None, cwd=None, when=None):
        self.line = line
        self.status = status  # exit status, or None if not known
        self.cwd = cwd
        self.when = when

    def __repr__(self):
        return "Command(%r, status=%r)" % (self.line, self.status)


HOOK_SOURCE = "the AskTim! shell hook"  # recent()'s source for commands from the hook's log

_ASKTIM = re.compile(r"^\s*(?:command\s+|builtin\s+|\\)?(?:\S*/)?(?:AskTim!?|asktim!?)(?:\s|$)", re.I)


def is_asktim(line):
    return bool(_ASKTIM.match(line or ""))


def detect_shell():
    """bash, zsh, fish, sh, powershell ... - the shell that started us, else $SHELL."""
    try:
        ppid = os.getppid()
        with open("/proc/%d/comm" % ppid) as f:
            name = f.read().strip()
        if name in ("bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "csh"):
            return name
    except (OSError, AttributeError):
        pass
    if sys.platform.startswith("win"):
        return "powershell"
    return os.path.basename(os.environ.get("SHELL", "")) or "sh"


def usual_shell():
    """The user's usual interactive shell, when AskTim! was not started from one (a notebook cell):
    $SHELL if it is bash, zsh or fish, else bash where there is one (it is Colab's)."""
    name = os.path.basename(os.environ.get("SHELL", ""))
    if name in ("bash", "zsh", "fish"):
        return name
    if sys.platform.startswith("win"):
        return "powershell"
    import shutil

    return "bash" if shutil.which("bash") else (name or "sh")


def _unmetafy(data):
    """zsh writes some bytes in its history as 0x83 followed by the byte XOR 32."""
    out = bytearray()
    it = iter(data)
    for b in it:
        if b == 0x83:
            try:
                out.append(next(it) ^ 32)
            except StopIteration:
                break
        else:
            out.append(b)
    return bytes(out)


def _tail_bytes(path, limit=400000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read()
        if size > limit:
            data = data.split(b"\n", 1)[-1]
        return data
    except OSError:
        return b""


def history_file(shell):
    home = os.path.expanduser("~")
    if os.environ.get("HISTFILE") and shell in ("bash", "zsh", "sh", "ksh"):
        return os.environ["HISTFILE"]
    if shell == "zsh":
        return os.path.join(os.environ.get("ZDOTDIR", home), ".zsh_history")
    if shell == "fish":
        data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
        return os.path.join(data, "fish", "fish_history")
    if shell == "powershell":
        return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "PowerShell", "PSReadLine", "ConsoleHost_history.txt")
    return os.path.join(home, ".bash_history")


def parse_bash(text):
    cmds = []
    for ln in text.splitlines():
        if ln.startswith("#") and ln[1:].strip().isdigit():
            continue  # HISTTIMEFORMAT time stamps
        if ln.strip():
            cmds.append(Command(ln.rstrip()))
    return cmds


def parse_zsh(text):
    cmds = []
    buf = None
    for ln in text.splitlines():
        if buf is not None:
            buf += "\n" + ln
        else:
            m = re.match(r"^: (\d+):\d+;(.*)$", ln)
            if m:
                buf = m.group(2)
                when = int(m.group(1))
            else:
                buf = ln
                when = None
        if buf.endswith("\\"):
            buf = buf[:-1]
            continue
        if buf.strip():
            cmds.append(Command(buf, when=when))
        buf = None
    return cmds


def parse_fish(text):
    cmds = []
    cur = None
    for ln in text.splitlines():
        if ln.startswith("- cmd: "):
            if cur:
                cmds.append(cur)
            cmd = ln[len("- cmd: "):].replace("\\n", "\n").replace("\\\\", "\\")
            cur = Command(cmd)
        elif ln.startswith("  when: ") and cur is not None:
            try:
                cur.when = int(ln.split(":", 1)[1])
            except ValueError:
                pass
    if cur:
        cmds.append(cur)
    return cmds


def parse_hook_log(text):
    cmds = []
    for ln in text.splitlines():
        parts = ln.split("\t", 3)
        if len(parts) != 4:
            continue
        when, status, cwd, line = parts
        try:
            st = int(status)
        except ValueError:
            st = None
        try:
            w = int(float(when))
        except ValueError:
            w = None
        cmds.append(Command(line.replace("\x1e", "\n"), status=st, cwd=cwd, when=w))
    return cmds


def hook_active(max_age_hours=12):
    """Is the shell hook writing its log (and recently)?"""
    p = paths.commands_log()
    try:
        return time.time() - os.path.getmtime(p) < max_age_hours * 3600
    except OSError:
        return False


def shell_started(pid=None):
    """When the shell that started AskTim! started (seconds since the epoch), or None if not known."""
    pid = pid or os.getppid()
    try:
        with open("/proc/%d/stat" % pid) as f:
            fields = f.read().rsplit(")", 1)[1].split()
        ticks = int(fields[19])  # field 22 of the line: when the process started, in clock ticks after boot
        with open("/proc/stat") as f:
            boot = next(int(ln.split()[1]) for ln in f if ln.startswith("btime "))
        return boot + ticks / float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, StopIteration, AttributeError):
        pass
    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                                 env=dict(os.environ, LC_ALL="C")).stdout.strip()
            return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")) if out else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    return None


def _process_name(pid):
    """The program that process pid runs (bash, python3 ...), or None if it is not known."""
    try:
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip() or None
    except OSError:
        pass
    if sys.platform == "darwin":
        import subprocess

        try:
            out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return os.path.basename(out).lstrip("-") or None  # a login shell is "-zsh"
    return None


def typed_into():
    """The interactive shell (bash, zsh or fish) that AskTim! was typed into, or None when it was
    started some other way: from a notebook cell, a script, `bash -c`, an editor or another program."""
    try:
        ppid = os.getppid()
        # an interactive shell gives each command a process group of its own (job control); a script and `bash -c` do not
        if os.getpgrp() == os.getpgid(ppid):
            return None
    except (OSError, AttributeError):  # (no process groups on Windows)
        return None
    name = _process_name(ppid)
    return name if name in ("bash", "zsh", "fish") else None


def hook_loaded(shell, started=None):
    """Has the shell that AskTim! was typed into loaded AskTim!'s hook, so that its commands are recorded?"""
    if os.environ.get("ASKTIM_HOOK") == shell:
        return True
    from . import shell_setup

    block = shell_setup.installed_block(shell)
    if block is None or "ASKTIM_HOOK" in block:
        return False  # not set up; or set up by this AskTim!, whose lines say so when they are loaded
    # lines added by AskTim! 0.2.1 or older do not say so: they are loaded if their log has been written since this shell started
    started = shell_started() if started is None else started
    try:
        p = paths.commands_log()
        return started is not None and os.path.getsize(p) > 0 and os.path.getmtime(p) > started
    except OSError:
        return False


def unseen_terminal():
    """When AskTim! cannot see the commands typed in the terminal it was typed into, why: a dict with
    "shell", "installed" (AskTim!'s hook is in the shell's settings but not loaded in this terminal),
    "rc" (that settings file) and "tmux" (the terminal runs in tmux, as Colab's does) - else None.

    bash writes its history file only when the shell exits, so a terminal that stays open (and tmux
    keeps it open: closing and reopening Colab's terminal finds the same shell) is invisible to
    AskTim! unless its hook records each command."""
    shell = typed_into()
    if shell is None:
        return None
    started = shell_started()
    if hook_loaded(shell, started):
        return None
    try:
        # (the start time is known to within a second: the boot time is in whole seconds)
        fresh = started is not None and os.path.getmtime(history_file(shell)) > started + 2
    except OSError:
        fresh = False
    if fresh:
        return None  # the shell writes its history as it goes (zsh's INC_APPEND_HISTORY, bash's history -a, fish)
    from . import shell_setup

    return {"shell": shell, "installed": shell_setup.installed(shell), "rc": paths.pretty(shell_setup.rc_file(shell)),
            "tmux": bool(os.environ.get("TMUX"))}


def unseen_hint(gap):
    """What to tell the user about unseen_terminal()'s answer."""
    load = "source %s" % gap["rc"]
    if gap["installed"]:
        text = ("AskTim!'s shell hook is installed but not loaded in this terminal, so AskTim! cannot see the commands you typed here.\n"
                "To load it, run in this terminal:  %s" % load)
    else:
        saves = {"bash": "bash saves them only when the terminal closes", "zsh": "zsh has not saved them yet",
                 "fish": "fish has not saved them yet"}[gap["shell"]]
        text = ("AskTim! cannot see the commands you typed in this terminal: %s.\n"
                "To let it see them as you type them, run:  AskTim! setup-shell   and then, in this terminal:  %s" % (saves, load))
    if gap["tmux"]:
        text += "\n(This terminal runs in tmux, as Colab's does: closing and reopening it keeps the same shell.)"
    return text


def recent(n=20, shell=None):
    """(commands, source): the last n commands, oldest first, without AskTim!'s own."""
    shell = shell or detect_shell()
    source = None
    cmds = []
    if hook_active():
        cmds = parse_hook_log(_tail_bytes(paths.commands_log()).decode("utf-8", "replace"))
        source = HOOK_SOURCE
    if not cmds:
        f = history_file(shell)
        raw = _tail_bytes(f)
        if raw:
            if shell == "zsh":
                cmds = parse_zsh(_unmetafy(raw).decode("utf-8", "replace"))
            elif shell == "fish":
                cmds = parse_fish(raw.decode("utf-8", "replace"))
            else:
                cmds = parse_bash(raw.decode("utf-8", "replace"))
            source = paths.pretty(f)
    out = []
    for c in cmds:
        if is_asktim(c.line) or c.line.startswith(" "):
            continue
        if out and out[-1].line == c.line and out[-1].status == c.status:
            continue
        out.append(c)
    if not out:
        source = None  # nothing found (an empty hook log is not a history)
    return out[-n:] if n else out, source


def last_failed(cmds):
    for c in reversed(cmds):
        if c.status not in (None, 0):
            return c
    return None
