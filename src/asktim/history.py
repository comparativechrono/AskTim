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


def recent(n=20, shell=None):
    """(commands, source): the last n commands, oldest first, without AskTim!'s own."""
    shell = shell or detect_shell()
    source = None
    cmds = []
    if hook_active():
        cmds = parse_hook_log(_tail_bytes(paths.commands_log()).decode("utf-8", "replace"))
        source = "the AskTim! shell hook"
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
    return out[-n:] if n else out, source


def last_failed(cmds):
    for c in reversed(cmds):
        if c.status not in (None, 0):
            return c
    return None
