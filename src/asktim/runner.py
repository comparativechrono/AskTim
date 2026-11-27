"""Running the agent's commands.

Each command runs in bash (sh if there is no bash), in the agent's folder, with no input
(stdin is /dev/null, so nothing can wait for typing), with a time limit, in a process group
of its own (so that a timeout or Ctrl+C stops everything it started). Secrets are taken out
of its environment (variables whose names hold KEY, TOKEN, SECRET, PASSWORD ...), so a
command cannot print the user's API keys.

The user sees everything a command prints, as it prints it. A bounded copy (the beginning
and the end) is kept for the data filter, which decides what the AI may see.

Sandbox: on Linux, when bubblewrap (bwrap) is installed and works, every command runs in a
sandbox where the whole file system is read-only except the agent's folder and a private
/tmp, and - unless the user allowed the internet - there is no network. Where there is
no sandbox, AskTim! compares the user's folder before and after each step and reports
any file that changed outside the agent's folder.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|_AUTH|COOKIE|SESSION", re.I)
KEEP_ENV = {"SSH_AUTH_SOCK"}  # not a secret itself, but give the agent no ssh either
SNAPSHOT_SKIP = {".git", "__pycache__", "node_modules", ".snakemake", ".nextflow", "work", ".ipynb_checkpoints", ".cache", ".conda", "asktim-runs"}


class RunResult(object):
    def __init__(self):
        self.exit_code = None
        self.timed_out = False
        self.interrupted = False
        self.duration = 0.0
        self.head = []  # first lines of the combined output
        self.tail = deque(maxlen=400)  # last lines
        self.lines = 0
        self.bytes = 0
        self.stderr_lines = 0
        self.error = None  # could not start

    def text(self):
        """The output as kept: beginning and end, with a note of what is missing between them."""
        head = list(self.head)
        tail = list(self.tail)
        if self.lines <= len(head) + len(tail):
            # tail overlaps head: rebuild without duplicates
            seen = len(head)
            rest = tail[max(0, len(tail) - (self.lines - seen)):] if self.lines > seen else []
            return "".join(head + rest)
        gap = self.lines - len(head) - len(tail)
        return "".join(head) + "[… %d lines not kept here …]\n" % gap + "".join(tail)

    def last_lines(self, n=30):
        lines = self.text().splitlines()
        return "\n".join(lines[-n:])


def clean_env(extra=None, tmpdir=None):
    env = {}
    for k, v in os.environ.items():
        if SECRET_ENV.search(k) and k not in KEEP_ENV:
            continue
        if k in ("SSH_AUTH_SOCK", "SNAKEMAKE_PROFILE"):
            continue  # (a default Snakemake profile could send the agent's jobs to a cluster unseen)
        env[k] = v
    env.update({
        "PAGER": "cat", "MANPAGER": "cat", "GIT_PAGER": "cat", "SYSTEMD_PAGER": "", "LESS": "-FRX",
        "TERM": "dumb", "NO_COLOR": "1", "CLICOLOR": "0", "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg",
        "DEBIAN_FRONTEND": "noninteractive", "GIT_TERMINAL_PROMPT": "0", "ASKTIM_AGENT": "1",
        "R_DEFAULT_DEVICE": "png",
    })
    if tmpdir:
        env["TMPDIR"] = tmpdir
        # In the sandbox the home folder is read-only, so programs that keep a cache there
        # (Snakemake's source cache, matplotlib's and fontconfig's font caches, R) keep it in
        # the private temporary folder instead. (Their settings in ~/.config can still be read.)
        env["XDG_CACHE_HOME"] = os.path.join(tmpdir, ".cache")
        env["XDG_STATE_HOME"] = os.path.join(tmpdir, ".local-state")
    if extra:
        env.update(extra)
    return env


def shell_path():
    return shutil.which("bash") or shutil.which("sh")


class Sandbox(object):
    """bubblewrap on Linux (sandbox-exec on macOS, only when asked for)."""

    def __init__(self, kind, run_dir, student_dir, allow_network):
        self.kind = kind
        self.run_dir = os.path.realpath(run_dir)
        self.student_dir = os.path.realpath(student_dir)
        self.allow_network = allow_network
        self.private_tmp = tempfile.mkdtemp(prefix="asktim-tmp-")
        # a private /tmp would hide a user's folder that lies under /tmp: then the real /tmp
        # stays (read-only, like everything else) and commands get a private scratch folder
        tmp_root = os.path.realpath(tempfile.gettempdir())
        self.overlay_tmp = not any(
            p == tmp_root or p.startswith(tmp_root + os.sep) or p == "/tmp" or p.startswith("/tmp/")
            for p in (self.student_dir, self.run_dir))

    def describe(self):
        if self.kind == "bwrap":
            return "sandbox: bubblewrap – only the agent's folder can be written%s" % ("" if self.allow_network else "; no network")
        if self.kind == "sandbox-exec":
            return "sandbox: macOS sandbox-exec – only the agent's folder can be written%s" % ("" if self.allow_network else "; no network")
        return "no sandbox"

    def wrap(self, argv):
        if self.kind == "bwrap":
            a = [shutil.which("bwrap"), "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "--die-with-parent"]
            for d in sorted(os.listdir("/dev")) if os.path.isdir("/dev") else []:
                if d.startswith("nvidia"):
                    a += ["--dev-bind", "/dev/" + d, "/dev/" + d]
            if self.overlay_tmp:
                a += ["--bind", self.private_tmp, "/tmp"]
            else:
                a += ["--bind", self.private_tmp, self.private_tmp]
            a += ["--bind", self.run_dir, self.run_dir]
            if not self.allow_network:
                a += ["--unshare-net"]
            a += ["--chdir", self.run_dir, "--"]
            return a + argv
        if self.kind == "sandbox-exec":
            prof = [
                "(version 1)", "(allow default)", "(deny file-write*)",
                '(allow file-write* (subpath "%s") (subpath "/private/tmp") (subpath "/private/var/folders") (subpath "/dev"))' % self.run_dir,
            ]
            if not self.allow_network:
                prof.append("(deny network*)")
            return ["/usr/bin/sandbox-exec", "-p", "\n".join(prof)] + argv
        return argv

    def env_tmp(self):
        if self.kind == "bwrap":
            return "/tmp" if self.overlay_tmp else self.private_tmp
        return None

    def cleanup(self):
        shutil.rmtree(self.private_tmp, ignore_errors=True)


def make_sandbox(mode, run_dir, student_dir, allow_network):
    """A working sandbox, or (None, reason)."""
    if mode == "off":
        return None, "the sandbox is turned off in the settings"
    if sys.platform.startswith("linux"):
        if not shutil.which("bwrap"):
            return None, "bubblewrap (bwrap) is not installed"
        sb = Sandbox("bwrap", run_dir, student_dir, allow_network)
    elif sys.platform == "darwin" and mode == "on" and os.path.exists("/usr/bin/sandbox-exec"):
        sb = Sandbox("sandbox-exec", run_dir, student_dir, allow_network)
    else:
        return None, "there is no sandbox for this system"
    # does it work here? (user namespaces can be switched off, for example on clusters)
    try:
        sh = shell_path() or "/bin/sh"
        p = subprocess.run(sb.wrap([sh, "-c", "touch .asktim-sandbox-test && rm .asktim-sandbox-test"]), cwd=run_dir, stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        if p.returncode != 0:
            sb.cleanup()
            return None, "the sandbox does not work here (%s)" % (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["exit %d" % p.returncode])[-1][:200]
    except Exception as e:
        sb.cleanup()
        return None, "the sandbox could not start (%s)" % e
    return sb, None


def run(command, cwd, timeout=600, sandbox=None, on_output=None, env_extra=None):
    """Run a command; on_output(stream, text) gets what it prints as it prints it."""
    res = RunResult()
    sh = shell_path()
    if not sh:
        res.error = "There is no bash or sh on this computer, so the agent cannot run commands."
        res.exit_code = 127
        return res
    argv = [sh, "-c", command]
    if sandbox is not None:
        argv = sandbox.wrap(argv)
    env = clean_env(env_extra, tmpdir=sandbox.env_tmp() if sandbox else None)
    t0 = time.time()
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kwargs)
    except OSError as e:
        res.error = "The command could not be started: %s" % e
        res.exit_code = 126
        return res
    lock = threading.Lock()
    head_limit = 300

    def pump(stream, name):
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", "replace")
            kept = text if len(text) <= 4000 else text[:4000] + "…[line cut]\n"
            with lock:
                res.lines += 1
                res.bytes += len(raw)
                if name == "stderr":
                    res.stderr_lines += 1
                if len(res.head) < head_limit:
                    res.head.append(kept)
                else:
                    res.tail.append(kept)
            if on_output:
                try:
                    on_output(name, text)
                except Exception:
                    pass
        stream.close()

    threads = [threading.Thread(target=pump, args=(proc.stdout, "stdout")), threading.Thread(target=pump, args=(proc.stderr, "stderr"))]
    for t in threads:
        t.daemon = True
        t.start()
    try:
        while True:
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if timeout and time.time() - t0 > timeout:
                    res.timed_out = True
                    _kill(proc)
                    break
    except KeyboardInterrupt:
        res.interrupted = True
        _kill(proc)
    for t in threads:
        t.join(timeout=5)
    res.exit_code = proc.returncode if proc.returncode is not None else -9
    res.duration = time.time() - t0
    return res


def _kill(proc):
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


# ------------------------------------------------------------------------------------------------
# what changed outside the agent's folder (when there is no sandbox)
# ------------------------------------------------------------------------------------------------


def snapshot(root, exclude, limit=20000, max_depth=6):
    """{path: (size, mtime_ns)} of the files under root, or None if there are too many."""
    out = {}
    exclude = os.path.realpath(exclude)
    root = os.path.realpath(root)
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        if d == exclude or d.startswith(exclude + os.sep):
            continue
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name not in SNAPSHOT_SKIP and depth < max_depth:
                            stack.append((e.path, depth + 1))
                        continue
                    st = e.stat(follow_symlinks=False)
                    out[e.path] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    continue
                if len(out) > limit:
                    return None
    return out


def changes(before, after):
    if before is None or after is None:
        return None
    created = sorted(p for p in after if p not in before)
    deleted = sorted(p for p in before if p not in after)
    changed = sorted(p for p in after if p in before and after[p] != before[p])
    return {"created": created, "changed": changed, "deleted": deleted}
