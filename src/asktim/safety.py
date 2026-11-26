"""Checks on every command the agent wants to run, before it runs.

The command is read as the shell would read it (words, quotes, pipes, lists, redirections,
$( ), here-documents, bash -c '...', scripts that it runs), and each simple command is
checked. The result is a Verdict:

    ok       - it may run (in "approve each step" mode the student is still asked);
    confirm  - it may run only if the student says yes, even in autonomous mode;
    block    - it does not run; the agent is told why.

Blocked, always: sudo and other ways to become another user; installing software (pip,
conda, apt, brew, install.packages ...); changing the system (shutdown, mount, crontab,
kill ...); uploading or pushing anything (curl -d/-T/-F, scp/rsync to a remote, git push);
commands that are not installed; interactive programs (vim, less, a bare python or R);
programs that open windows (the agent has open_file for that); background jobs; eval;
reading AskTim!'s keys and other credentials (~/.ssh, ~/.aws, ...); writing outside the
agent's folder (redirections, tee, cp/mv/rm/mkdir/touch, sed -i, in-place gzip, index files
that tools write next to their input, ...).
Blocked unless the student allowed the internet (--web): downloads (curl, wget, git clone,
SRA tools, NCBI E-utilities, code that uses requests/urllib/download.file ...).
Confirm: deleting with rm -r or wildcards, writing to a place given by an unknown variable,
running a script or program from outside the agent's folder, cluster jobs (sbatch ...),
Python or R code that starts other programs.

A static check cannot follow everything a program does with its arguments, so where it
works AskTim! also runs the agent's commands in a sandbox (runner.py) in which the rest of
the file system is read-only and the network is off.
"""

import os
import re
import shutil

from .viewers import VIEWERS as _VIEWERS

BUILTINS = set(
    """
    alias bg bind break builtin caller cd command compgen complete continue declare dirs disown echo enable
    eval exec exit export false fc fg getopts hash help history jobs kill let local logout mapfile popd
    printf pushd pwd read readarray readonly return set shift shopt source suspend test times trap true
    type typeset ulimit umask unalias unset wait . : [ [[ ]] ]
    """.split()
)
KEYWORDS = set("if then else elif fi case esac for select while until do done in function time ! { } [[ ]] coproc".split())

SUDO = {"sudo", "su", "doas", "pkexec", "runuser", "sg", "newgrp"}
PKG_TOOLS = {
    "apt", "apt-get", "aptitude", "dpkg", "yum", "dnf", "zypper", "pacman", "apk", "brew", "port", "snap", "flatpak",
    "emerge", "nix-env", "spack", "easy_install", "cpan", "cpanm", "pipx", "gem", "cargo", "npm", "yarn", "pnpm",
}
SYSTEM = {
    "shutdown", "reboot", "halt", "poweroff", "init", "telinit", "systemctl", "service", "launchctl", "mount", "umount",
    "swapon", "swapoff", "fdisk", "sfdisk", "parted", "gdisk", "wipefs", "crontab", "at", "batch", "chown", "chgrp",
    "chattr", "setfacl", "passwd", "chpasswd", "useradd", "userdel", "usermod", "groupadd", "visudo", "iptables",
    "ip6tables", "nft", "ufw", "modprobe", "insmod", "rmmod", "sysctl", "kill", "pkill", "killall", "xkill", "renice",
    "reboot", "shred", "losetup", "cryptsetup", "dmsetup", "chroot", "nsenter", "unshare", "setcap", "pm-suspend",
}
NETWORK = {
    "curl", "wget", "aria2c", "axel", "lftp", "ftp", "sftp", "scp", "ssh", "telnet", "nc", "ncat", "netcat", "socat",
    "nmap", "prefetch", "fasterq-dump", "fastq-dump", "sam-dump", "vdb-dump", "esearch", "efetch", "elink", "einfo",
    "epost", "efilter", "xtract" if False else "esummary", "datasets", "aws", "gsutil", "gcloud", "az", "rclone",
    "ascp", "irods", "iget", "iput", "huggingface-cli", "kaggle", "osf", "zenodo_get", "youtube-dl", "yt-dlp",
}
UPLOAD_ALWAYS = {"scp", "sftp", "ssh", "nc", "ncat", "netcat", "socat", "telnet", "nmap", "ftp", "lftp", "rclone", "iput", "ascp"}
INTERACTIVE = {
    "vi", "vim", "nvim", "nano", "pico", "emacs", "joe", "micro", "ed", "less", "more", "most", "top", "htop", "btop",
    "watch", "screen", "tmux", "mc", "ranger", "nnn", "ipython", "jupyter", "jupyter-lab", "jupyter-notebook",
    "radian", "irb", "ghci", "mysql", "psql", "mongo", "redis-cli", "tig", "lazygit", "fzf", "dialog", "whiptail",
}
REPLS = {"python", "python3", "python2", "R", "julia", "node", "perl", "ruby", "lua", "Rscript", "sqlite3", "bash", "sh", "zsh", "fish", "dash", "ksh", "tclsh", "octave", "matlab"}
GUI = {"xdg-open", "open", "gio", "firefox", "chrome", "google-chrome", "chromium", "safari", "code", "rstudio", "igv", "igv.sh", "gedit", "kate", "evince", "okular", "eog", "display", "feh", "xdg-mime", "start", "explorer.exe", "notepad"} | set(_VIEWERS)
WRAPPERS = {"env", "time", "nice", "nohup", "timeout", "command", "builtin", "exec", "stdbuf", "ionice", "taskset", "chrt", "caffeinate", "unbuffer", "xargs", "parallel", "srun", "doas", "sudo", "strace", "ltrace", "valgrind", "/usr/bin/time", "numactl", "systemd-run", "flock"}
JOBS = {"sbatch", "qsub", "bsub", "salloc", "condor_submit"}
DELETERS = {"rm", "rmdir", "unlink", "shred", "trash", "trash-put"}
COMPRESSORS = {"gzip", "gunzip", "bgzip", "bzip2", "bunzip2", "xz", "unxz", "zstd", "unzstd", "pigz", "unpigz", "lz4", "brotli"}
CODE_READERS = {"cat", "head", "tail", "less", "more", "nl", "bat", "sed", "awk"}
SUMMARY_COMMANDS = {
    "wc", "md5sum", "sha1sum", "sha256sum", "sha512sum", "md5", "shasum", "cksum", "du", "df", "ls", "stat", "file",
    "pwd", "nproc", "free", "uname", "which", "type", "whereis", "date", "id", "whoami", "hostname", "ulimit", "uptime",
}

SENSITIVE = [
    "~/.ssh", "~/.gnupg", "~/.aws", "~/.azure", "~/.kube", "~/.docker/config.json", "~/.netrc", "~/.git-credentials",
    "~/.config/gcloud", "~/.config/gh", "~/.pgpass", "~/.my.cnf", "~/.bash_history", "~/.zsh_history",
    "~/.local/share/fish/fish_history", "~/.python_history", "~/.Rhistory", "~/.mozilla", "~/.config/google-chrome",
    "~/.config/chromium", "~/Library/Keychains", "~/Library/Application Support/Google/Chrome", "/etc/shadow", "/etc/gshadow",
    "/etc/sudoers", "~/.ipython/profile_default/history.sqlite", "~/.jupyter",
]

_INSTALL_TEXT = [
    (re.compile(r"\binstall\.packages\s*\("), "R's install.packages()"),
    (re.compile(r"\bBiocManager\s*::\s*install\s*\("), "BiocManager::install()"),
    (re.compile(r"\b(?:remotes|devtools|pak|renv)\s*::\s*(?:install\w*|pkg_install|restore)\s*\("), "installing R packages"),
    (re.compile(r"\bpip3?\s+(?:-\S+\s+)*install\b|\bpip\.main\s*\(|-m\s+pip\s+install\b|\bconda\s+install\b|\bmamba\s+install\b"), "installing Python packages"),
    (re.compile(r"\bR\s+CMD\s+INSTALL\b"), "R CMD INSTALL"),
]
_NETWORK_TEXT = [
    (re.compile(r"\b(?:requests|httpx|aiohttp|urllib3)\s*\.\s*(?:get|post|put|request|Session|AsyncClient|Client|urlopen|PoolManager)\b"), "web requests"),
    (re.compile(r"\burllib\.request\b|\burlopen\s*\(|\burlretrieve\s*\("), "web requests"),
    (re.compile(r"\bsocket\.socket\s*\(|\bhttp\.client\b|\bftplib\b|\bsmtplib\b|\bparamiko\b"), "network connections"),
    (re.compile(r"\b(?:download\.file|curl_download|httr::|GET\s*\(\s*['\"]https?:|RCurl::|getURL\s*\()"), "downloads from R"),
    (re.compile(r"\bEntrez\.\w+\s*\(|\bbiomart\b|\bpybiomart\b|\bmygene\b|\bbioservices\b"), "online databases"),
    (re.compile(r"['\"]https?://"), "a web address"),
]
_UPLOAD_TEXT = [
    (re.compile(r"\b(?:requests|httpx)\s*\.\s*(?:post|put|patch)\s*\(|\bhttr::(?:POST|PUT)\b|\bsmtplib\b|\bftplib\b"), "sending data out"),
]
_SUBPROCESS_TEXT = re.compile(r"\bos\.system\s*\(|\bsubprocess\.|\bos\.popen\s*\(|\bos\.exec\w*\s*\(|\bsystem2?\s*\(|\bshell\s*\(|\bpty\.spawn\b|`[^`]+`")
_DELETE_TEXT = re.compile(r"\bshutil\.rmtree\s*\(|\bos\.(?:remove|unlink|rmdir|removedirs)\s*\(|\bunlink\s*\(|\bfile\.remove\s*\(|\bPath\([^)]*\)\.(?:unlink|rmdir)\s*\(")


class Verdict(object):
    def __init__(self):
        self.level = "ok"
        self.reasons = []  # why it is blocked or needs a yes
        self.notes = []  # things worth knowing (not blocking)
        self.commands = []  # program names used
        self.writes = []  # paths written (resolved where possible)
        self.output_kind = "default"  # for the data filter: default | help | code | summary

    def block(self, why):
        self.level = "block"
        if why not in self.reasons:
            self.reasons.append(why)

    def confirm(self, why):
        if self.level == "ok":
            self.level = "confirm"
        if why not in self.reasons:
            self.reasons.append(why)

    def __repr__(self):
        return "Verdict(%s, %r)" % (self.level, self.reasons)


# ------------------------------------------------------------------------------------------------
# reading a command as the shell does (enough of it for these checks)
# ------------------------------------------------------------------------------------------------


class Word(object):
    def __init__(self):
        self.text = ""  # with quotes removed
        self.raw = ""
        self.dynamic = False  # holds $..., `...`, $( ) or a glob
        self.quoted = False

    def __repr__(self):
        return "W(%r%s)" % (self.text, "*" if self.dynamic else "")


class Parsed(object):
    def __init__(self):
        self.tokens = []  # Word or ("op", text)
        self.subs = []  # text of $( ), ` `, <( ), >( )
        self.heredocs = []  # (delimiter, body, quoted)
        self.error = None


_OPS = ["&>>", "<<<", "<<-", "&&", "||", ";;", ";&", ">>", "<<", "<&", ">&", "&>", "<>", ">|", "|&", "|", "&", ";", "(", ")", "<", ">", "\n"]


def _read_balanced(s, i, open_ch="(", close_ch=")"):
    """Index just after the matching close_ch, for s[i] just after the opening one."""
    depth = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced %s" % open_ch)


def tokenize(s):
    p = Parsed()
    i, n = 0, len(s)
    w = None
    pending_heredocs = []

    def end_word():
        nonlocal w
        if w is not None:
            p.tokens.append(w)
            w = None

    def word():
        nonlocal w
        if w is None:
            w = Word()
        return w

    try:
        while i < n:
            c = s[i]
            if c == "\n" and pending_heredocs:
                end_word()
                p.tokens.append(("op", "\n"))
                i += 1
                for delim, quoted, strip in pending_heredocs:
                    body = []
                    while i < n:
                        j = s.find("\n", i)
                        line = s[i:] if j < 0 else s[i:j]
                        i = n if j < 0 else j + 1
                        if (line.lstrip("\t") if strip else line) == delim:
                            break
                        body.append(line)
                    text = "\n".join(body)
                    p.heredocs.append((delim, text, quoted))
                    if not quoted:
                        for m in re.finditer(r"\$\(", text):
                            try:
                                e = _read_balanced(text, m.end())
                                p.subs.append(text[m.end():e - 1])
                            except ValueError:
                                pass
                pending_heredocs = []
                continue
            if c in " \t":
                end_word()
                i += 1
                continue
            if c == "#" and w is None:
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
            if c == "\\":
                if i + 1 < n and s[i + 1] == "\n":
                    i += 2
                    continue
                ww = word()
                ww.text += s[i + 1:i + 2]
                ww.raw += s[i:i + 2]
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise ValueError("a quote ' is not closed")
                ww = word()
                ww.text += s[i + 1:j]
                ww.raw += s[i:j + 1]
                ww.quoted = True
                i = j + 1
                continue
            if c == "$" and s[i + 1:i + 2] == "'":
                j = i + 2
                while j < n and s[j] != "'":
                    j += 2 if s[j] == "\\" else 1
                if j >= n:
                    raise ValueError("a quote $' is not closed")
                ww = word()
                ww.text += s[i + 2:j].encode("utf-8").decode("unicode_escape", "replace")
                ww.raw += s[i:j + 1]
                ww.quoted = True
                i = j + 1
                continue
            if c == '"':
                j = i + 1
                ww = word()
                ww.quoted = True
                buf = ""
                while j < n and s[j] != '"':
                    if s[j] == "\\" and j + 1 < n:
                        buf += s[j + 1]
                        j += 2
                        continue
                    if s[j] == "$" and s[j + 1:j + 2] == "(":
                        e = _read_balanced(s, j + 2)
                        p.subs.append(s[j + 2:e - 1])
                        buf += s[j:e]
                        ww.dynamic = True
                        j = e
                        continue
                    if s[j] == "`":
                        e = s.find("`", j + 1)
                        if e < 0:
                            raise ValueError("a backquote is not closed")
                        p.subs.append(s[j + 1:e])
                        buf += s[j:e + 1]
                        ww.dynamic = True
                        j = e + 1
                        continue
                    if s[j] == "$":
                        ww.dynamic = True
                    buf += s[j]
                    j += 1
                if j >= n:
                    raise ValueError('a quote " is not closed')
                ww.text += buf
                ww.raw += s[i:j + 1]
                i = j + 1
                continue
            if c == "$" and s[i + 1:i + 3] == "((":
                e = _read_balanced(s, i + 2)
                ww = word()
                ww.text += s[i:e]
                ww.raw += s[i:e]
                ww.dynamic = True
                i = e
                continue
            if c == "$" and s[i + 1:i + 2] == "(":
                e = _read_balanced(s, i + 2)
                p.subs.append(s[i + 2:e - 1])
                ww = word()
                ww.text += s[i:e]
                ww.raw += s[i:e]
                ww.dynamic = True
                i = e
                continue
            if c == "`":
                e = s.find("`", i + 1)
                if e < 0:
                    raise ValueError("a backquote is not closed")
                p.subs.append(s[i + 1:e])
                ww = word()
                ww.text += s[i:e + 1]
                ww.raw += s[i:e + 1]
                ww.dynamic = True
                i = e + 1
                continue
            if c in "<>" and s[i + 1:i + 2] == "(" and w is None:
                e = _read_balanced(s, i + 2)
                p.subs.append(s[i + 2:e - 1])
                ww = word()
                ww.text = "/dev/fd/63"
                ww.raw = s[i:e]
                ww.dynamic = True
                end_word()
                i = e
                continue
            if c == "$" and s[i + 1:i + 2] == "{":
                e = _read_balanced(s, i + 2, "{", "}")
                ww = word()
                ww.text += s[i:e]
                ww.raw += s[i:e]
                ww.dynamic = True
                i = e
                continue
            op = None
            for o in _OPS:
                if s.startswith(o, i):
                    op = o
                    break
            if op:
                # a file-descriptor number right before a redirection belongs to it (2> 2>&1)
                if op in ("<", ">", ">>", ">&", "<&", "<>", ">|") and w is not None and w.raw.isdigit():
                    w = None
                end_word()
                p.tokens.append(("op", op))
                i += len(op)
                if op in ("<<", "<<-"):
                    # the delimiter of a here-document
                    while i < n and s[i] in " \t":
                        i += 1
                    j = i
                    while j < n and s[j] not in " \t\n;|&<>()":
                        j += 1
                    raw = s[i:j]
                    quoted = any(q in raw for q in "'\"\\")
                    pending_heredocs.append((raw.strip("'\"").replace("\\", ""), quoted, op == "<<-"))
                    dw = Word()
                    dw.text = raw.strip("'\"")
                    dw.raw = raw
                    p.tokens.append(dw)
                    i = j
                continue
            ww = word()
            ww.text += c
            ww.raw += c
            if c in "*?[" or (c == "$" and i + 1 < n and (s[i + 1].isalnum() or s[i + 1] in "_@#?!*-")):
                ww.dynamic = True
            if c == "~" and len(ww.raw) == 1:
                pass
            i += 1
        end_word()
    except ValueError as e:
        p.error = str(e)
    return p


class Simple(object):
    """One simple command: assignments, words, redirections."""

    def __init__(self):
        self.assigns = []
        self.words = []
        self.redirs = []  # (op, Word)
        self.background = False
        self.heredoc_target = None


def split_commands(p):
    cmds = []
    cur = Simple()
    expect_redir = None
    in_case = 0
    toks = p.tokens
    i = 0
    while i < len(toks):
        t = toks[i]
        if isinstance(t, tuple):
            op = t[1]
            if op in ("<", ">", ">>", "&>", "&>>", ">|", "<>", "<&", ">&", "<<", "<<-", "<<<"):
                expect_redir = op
            elif op in ("|", "|&", "&&", "||", ";", "\n", "(", ")", ";;", ";&", "&"):
                if op == "&" and (cur.words or cur.assigns):
                    cur.background = True
                if cur.words or cur.assigns or cur.redirs:
                    cmds.append(cur)
                cur = Simple()
            i += 1
            continue
        if expect_redir:
            cur.redirs.append((expect_redir, t))
            expect_redir = None
            i += 1
            continue
        if not cur.words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", t.raw):
            cur.assigns.append(t)
            i += 1
            continue
        cur.words.append(t)
        i += 1
    if cur.words or cur.assigns or cur.redirs:
        cmds.append(cur)
    return cmds


# ------------------------------------------------------------------------------------------------
# paths
# ------------------------------------------------------------------------------------------------


class Context(object):
    def __init__(self, run_dir, home=None, allow_network=False, student_dir=None, max_depth=3):
        self.run_dir = os.path.realpath(run_dir)
        self.student_dir = os.path.realpath(student_dir or os.path.dirname(self.run_dir))
        self.home = home or os.path.expanduser("~")
        self.allow_network = allow_network
        self.tmp = set([os.path.realpath("/tmp"), os.path.realpath(os.environ.get("TMPDIR", "/tmp"))])
        if os.path.isdir("/var/tmp"):
            self.tmp.add(os.path.realpath("/var/tmp"))
        self.max_depth = max_depth
        self.vars = {}
        self.cwd = self.run_dir
        self.functions = set()
        self.extra_writable = set()
        # with --here: the files that were in the student's folder before the run (True: too many to list)
        self.protect = None
        # symbolic links made earlier in the same command: link path -> what it points to
        self.links = {}


_SAFE_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero"}


def resolve(word, ctx, follow=True):
    """An absolute path for a word, or None when it depends on something unknown.

    follow=False gives the path of a symbolic link itself (what rm or ln -sf changes), not its target."""
    if isinstance(word, Word):
        text, dynamic, raw = word.text, word.dynamic, word.raw
    else:
        text, dynamic, raw = word, False, word
    t = text
    if raw.startswith("~") or t.startswith("~"):
        t = ctx.home + t[1:] if t == "~" or t.startswith("~/") else t
    known = dict(ctx.vars)
    known.update({"HOME": ctx.home, "PWD": ctx.cwd, "TMPDIR": os.environ.get("TMPDIR", "/tmp")})

    def expand(m):
        name, op = m.group(1), m.group(2) or ""
        if name not in known:
            if op.startswith((":-", "-")):
                return op.lstrip(":-")
            return m.group(0)
        val = known[name]
        if op.startswith(("##", "#")):
            return os.path.basename(val)  # ${f##*/}: the name without its folder
        return val  # ${f%.sam}, ${f/x/y}, ${f:-d}: the folder stays the same

    t = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}", expand, t)
    t = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", lambda m: known.get(m.group(1), m.group(0)), t)
    if "$" in t or "`" in t:
        return None
    if dynamic and re.search(r"[*?\[]", t):
        # a glob: what matters is the folder it is in
        head = re.split(r"[*?\[]", t, maxsplit=1)[0]
        t = head if head.endswith("/") or not head else os.path.dirname(head) or "."
    if not os.path.isabs(t):
        t = os.path.join(ctx.cwd, t)
    if not follow:
        t = os.path.normpath(t)
        base = os.path.basename(t)
        if base and base not in (".", ".."):
            return os.path.join(os.path.realpath(os.path.dirname(t)), base)
    return os.path.realpath(t)


def inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def writable(path, ctx):
    if path in _SAFE_TARGETS or path.startswith("/dev/fd/") or path.startswith("/proc/self/fd/"):
        return True
    if inside(path, ctx.run_dir):
        return True
    if any(inside(path, w) for w in ctx.extra_writable):
        return True
    if inside(path, ctx.student_dir) or inside(ctx.student_dir, path):
        return False  # the student's own folder stays as it is, even when it lies under /tmp
    return any(inside(path, t) and path != t or path == t for t in ctx.tmp)


def _sensitive(path, ctx):
    for s in SENSITIVE:
        p = os.path.realpath(os.path.expanduser(s.replace("~", ctx.home, 1)) if s.startswith("~") else s)
        if inside(path, p):
            return s
    try:
        from . import paths as _paths

        cfg = os.path.realpath(_paths.config_dir())
        if inside(path, cfg):
            return "AskTim!'s own settings and keys"
    except Exception:
        pass
    return None


# ------------------------------------------------------------------------------------------------
# the checks
# ------------------------------------------------------------------------------------------------


def _name(word):
    return os.path.basename(word.text) if word is not None else ""


def _strip_wrappers(words):
    """The real command of `env X=1 nice -n 5 timeout 60 cmd args` and what wrapped it."""
    wrappers = []
    i = 0
    while i < len(words):
        nm = words[i].text
        base = os.path.basename(nm)
        if base not in WRAPPERS and nm not in WRAPPERS:
            break
        wrappers.append(base)
        i += 1
        # skip the wrapper's options and their values
        takes_value = {
            "timeout": {"-s", "--signal", "-k", "--kill-after"},
            "nice": {"-n", "--adjustment"},
            "ionice": {"-c", "-n", "-p"},
            "taskset": {"-c", "-p"},
            "xargs": {"-n", "-I", "-i", "-P", "-d", "-E", "-L", "-s", "-a", "--max-args", "--delimiter", "--max-procs", "--arg-file"},
            "stdbuf": set(),
            "parallel": {"-j", "--jobs", "-N", "--colsep", "-S", "--sshlogin"},
            "srun": {"-n", "-c", "-N", "-p", "-t", "--mem", "-J", "-o", "-e", "--partition", "--time", "--ntasks", "--cpus-per-task"},
            "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
            "flock": {"-w", "-E"},
            "numactl": set(),
        }.get(base, set())
        while i < len(words):
            t = words[i].text
            if base == "env" and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                i += 1
                continue
            if base == "timeout" and re.match(r"^\d+(\.\d+)?[smhd]?$", t):
                i += 1
                break
            if t.startswith("-") and t != "-":
                i += 2 if t in takes_value else 1
                continue
            break
    return wrappers, words[i:]


def _is_installed(name, ctx):
    if name in BUILTINS or name in KEYWORDS or name in ctx.functions:
        return True
    if "/" in name:
        return True  # a path: checked as a script below
    return shutil.which(name) is not None


def _flag_set(args):
    flags = set()
    for a in args:
        t = a.text
        if t.startswith("--"):
            flags.add(t.split("=", 1)[0])
        elif t.startswith("-") and len(t) > 1 and not re.match(r"^-\d", t):
            for ch in t[1:]:
                flags.add("-" + ch)
    return flags


def _path_args(args):
    """Words that are not options (and not option values we can recognise)."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        t = a.text
        if t == "--":
            continue
        if t.startswith("-") and len(t) > 1:
            continue
        out.append(a)
    return out


def _check_write(word, ctx, v, what, follow=True):
    p = resolve(word, ctx, follow=follow)
    if p is None:
        v.confirm("%s goes to %s, which depends on a variable AskTim! cannot work out" % (what, word.raw))
        return
    if follow and p in ctx.links:
        p = ctx.links[p]  # written through a link made earlier in this command
    v.writes.append(p)
    if not writable(p, ctx):
        v.block("%s would write outside your folder: %s (only %s and /tmp may be written)" % (what, _show(p, ctx), _show(ctx.run_dir, ctx)))
    elif protected(p, ctx):
        v.confirm("%s would change %s, which was in your folder before the agent started" % (what, _show(p, ctx)))


def _check_write_path(p, ctx, v, what):
    v.writes.append(p)
    if not writable(p, ctx):
        v.block("%s would write outside your folder: %s (only %s and /tmp may be written)" % (what, _show(p, ctx), _show(ctx.run_dir, ctx)))
    elif protected(p, ctx):
        v.confirm("%s would change %s, which was in your folder before the agent started" % (what, _show(p, ctx)))


def _link_target(src, dest, ctx):
    """What a relative symbolic link will point to: relative to the folder the link is made in."""
    if dest is None:
        base_dir = ctx.cwd
    else:
        d = resolve(dest, ctx, follow=False)
        if d is None:
            return None
        base_dir = d if (os.path.isdir(d) and not os.path.islink(d)) else os.path.dirname(d)
    w = Word()
    w.text, w.raw, w.dynamic = src.text, src.raw, src.dynamic
    saved = ctx.cwd
    ctx.cwd = base_dir
    try:
        return resolve(w, ctx)
    finally:
        ctx.cwd = saved


def protected(p, ctx):
    """With --here: is p (or something under it) one of the student's own files?"""
    if not ctx.protect:
        return False
    if ctx.protect is True:
        return os.path.lexists(p) and inside(p, ctx.student_dir)
    if p in ctx.protect:
        return True
    pre = p.rstrip(os.sep) + os.sep
    return os.path.isdir(p) and any(f.startswith(pre) for f in ctx.protect)


def _show(p, ctx):
    if inside(p, ctx.run_dir):
        rel = os.path.relpath(p, ctx.run_dir)
        return "./" + rel if rel != "." else "your folder"
    home = ctx.home
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


# Python in a notebook that would put HTML, JavaScript or Markdown of its own into the notebook page:
# the page renders it as trusted, and script there can work the kernel without the student
_PAGE_TEXT = [
    (re.compile(r"\b(HTML|Javascript|JavaScript|Markdown|IFrame|SVG|Latex|Math)\s*\("), "IPython's display objects"),
    (re.compile(r"\bdisplay_(html|javascript|markdown|svg|latex|pretty|json)\b"), "IPython's display functions"),
    (re.compile(r"\bfrom\s+IPython(\.core)?\.display\s+import\s+[^\n]*\b(HTML|Javascript|JavaScript|Markdown|IFrame|SVG)\b"), "IPython's display objects"),
    (re.compile(r"^\s*%%?\s*(html|javascript|js|markdown|svg|latex)\b", re.M), "a display magic"),
    (re.compile(r"_repr_(html|javascript|markdown|svg|latex|mimebundle)_"), "a display method"),
    (re.compile(r"\b(display_pub|publish_display_data|ipywidgets|widgets\.HTML)\b"), "the notebook's display machinery"),
    (re.compile(r"\.style\b|\bStyler\b|\.to_html\s*\("), "HTML tables (pandas Styler, to_html)"),
]


def check_notebook_page(code, v):
    """Python the agent wants to run in the student's notebook: does it write into the page itself?"""
    for pat, what in _PAGE_TEXT:
        if pat.search(code):
            v.block("the code would put HTML, JavaScript or Markdown of its own into the notebook page (%s); "
                    "show results as printed text, tables (a DataFrame on its own) or plots instead" % what)
            return


_HIDDEN_IN_CODE = re.compile("[\x1b\u202a-\u202e\u2066-\u2069]")


def _check_text_code(code, v, ctx, lang):
    """Python, R or Perl code given on the command line or in a script."""
    if _HIDDEN_IN_CODE.search(code):
        v.block("the code contains escape codes or direction-override characters, which make code look different "
                "from what it does; write it in plain characters")
    for pat, what in _INSTALL_TEXT:
        if pat.search(code):
            v.block("the code installs software (%s); nothing may be installed - use what is there" % what)
    for pat, what in _UPLOAD_TEXT:
        if pat.search(code):
            v.block("the code would send data out (%s)" % what)
    if not ctx.allow_network:
        for pat, what in _NETWORK_TEXT:
            if pat.search(code):
                v.block("the code uses the internet (%s), which is off for the agent (the student can allow it with --web)" % what)
                break
    if _SUBPROCESS_TEXT.search(code):
        v.confirm("the %s code starts other programs, which AskTim! cannot check" % lang)
    if _DELETE_TEXT.search(code):
        v.confirm("the %s code deletes files" % lang)
    for m in re.finditer(r"""(?:open|to_csv|to_excel|to_parquet|savefig|write\.csv|write\.table|saveRDS|ggsave|png|pdf|writeLines|sink|write)\s*\(\s*(?:file\s*=\s*)?['"]([^'"]+)['"]""", code):
        p = resolve(m.group(1), ctx)
        mode_write = True
        if m.group(0).lstrip().startswith("open"):
            tail = code[m.end():m.end() + 40]
            mode_write = bool(re.match(r"""\s*,\s*(?:mode\s*=\s*)?['"][wax]""", tail))
        if p and mode_write and not writable(p, ctx):
            v.block("the code writes to %s, outside your folder" % _show(p, ctx))
        elif p and mode_write and protected(p, ctx):
            v.confirm("the code would overwrite %s, which was in your folder before the agent started" % _show(p, ctx))
    for m in re.finditer(r"""['"]((?:~|/)[^'"]*)['"]""", code):
        p = resolve(m.group(1), ctx)
        if p:
            s = _sensitive(p, ctx)
            if s:
                v.block("the code reads %s, which holds credentials or private history" % s)


def _check_simple(c, v, ctx, depth):
    # assignments are remembered so that later paths can be resolved
    for a in c.assigns:
        name, _, val = a.text.partition("=")
        name = name.rstrip("+")
        if a.dynamic:
            ctx.vars.pop(name, None)
        else:
            if val.startswith("~/") or val == "~":
                val = ctx.home + val[1:]
            ctx.vars[name] = val
    for op, target in c.redirs:
        if op in (">", ">>", "&>", "&>>", ">|", "<>"):
            if target.text.isdigit() or target.text == "-":
                continue
            _check_write(target, ctx, v, "the output")
        elif op in ("<", "<<<"):
            p = resolve(target, ctx)
            if p:
                s = _sensitive(p, ctx)
                if s:
                    v.block("it reads %s, which holds credentials or private history" % s)
    if c.words and c.words[0].text in ("export", "declare", "local", "readonly", "typeset"):
        for a in c.words[1:]:
            if "=" in a.text and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a.text):
                name, _, val = a.text.partition("=")
                if a.dynamic:
                    ctx.vars.pop(name, None)
                else:
                    ctx.vars[name] = ctx.home + val[1:] if val.startswith("~/") or val == "~" else val
        return
    if not c.words:
        return
    if c.background:
        v.block("it runs a command in the background (&); each command must finish within the time limit")
    wrappers, words = _strip_wrappers(c.words)
    for w in wrappers:
        if w in ("sudo", "doas"):
            v.block("%s runs a command as another user; the agent may not" % w)
        if w == "nohup":
            v.block("nohup leaves a program running after the step; each command must finish")
        if w in ("srun",):
            v.confirm("srun runs the command on the cluster")
    if not words:
        return
    first = words[0]
    name = first.text
    base = os.path.basename(name)
    args = words[1:]
    if first.dynamic and "$" in name:
        v.confirm("the program to run is given by a variable (%s)" % first.raw)
        return
    # keywords of the shell: look past them
    if base in KEYWORDS:
        rest = Simple()
        rest.words = [w for w in words[1:]]
        if base in ("for", "select") and len(words) >= 3 and words[2].text == "in":
            var = words[1].text
            dirs = set()
            for w in words[3:]:
                if w.text in ("do", ";"):
                    break
                p = resolve(w, ctx)
                if p is None:
                    dirs = None
                    break
                dirs.add(p if w.dynamic else os.path.dirname(p))
            if dirs and len(dirs) == 1:
                ctx.vars[var] = os.path.join(dirs.pop(), "item")
            else:
                ctx.vars.pop(var, None)
            return
        if base in ("for", "select", "case"):
            return  # the loop's words are not commands; its body is checked separately
        if base == "function" and rest.words:
            ctx.functions.add(rest.words[0].text)
            return
        if base in ("[[", "]]"):
            return
        _check_simple(rest, v, ctx, depth)
        return
    if len(words) >= 2 and words[1].text == "()" or name.endswith("()"):
        ctx.functions.add(name.rstrip("()"))
        return
    if re.match(r"^\(\(", name):
        return
    v.commands.append(base)

    # who may run what
    if base in SUDO:
        v.block("%s would run commands as another user" % base)
        return
    if base in PKG_TOOLS or (base in ("pip", "pip3", "uv", "conda", "mamba", "micromamba", "R", "Rscript", "python", "python3") and _installs(base, args)):
        v.block("it installs or removes software (%s); the agent may use only what is installed - tell the student instead" % " ".join([base] + [a.text for a in args[:2]]))
        return
    if base in SYSTEM:
        v.block("%s changes the system or other programs; the agent may not use it" % base)
        return
    if base in ("eval",):
        v.block("eval runs text as a command, which AskTim! cannot check")
        return
    if base == "exec" and args:
        v.block("exec replaces the shell")
        return
    if base in ("history", "fc"):
        v.block("the shell's history is private to the student")
        return
    if base in INTERACTIVE:
        v.block("%s is interactive and needs a person at the keyboard; use non-interactive commands (head, grep, --help ...)" % base)
        return
    if base in GUI:
        v.block("%s opens a window and would hold up the run; to show the student a file, use the open_file tool, which can name the program" % base)
        return
    if base in ("dd",):
        for a in args:
            if a.text.startswith("of="):
                tw = Word()
                tw.text = a.text[3:]
                tw.raw = a.raw[3:]
                if tw.text.startswith("/dev/") and tw.text not in _SAFE_TARGETS:
                    v.block("dd would write to a device")
                else:
                    _check_write(tw, ctx, v, "dd's output")
    if base in JOBS:
        v.confirm("%s submits a job to the cluster's scheduler, which then runs outside AskTim!'s control" % base)
    if base == "snakemake":
        _check_snakemake(args, v, ctx, depth)
    if base == "git":
        _check_git(args, v, ctx)
    if base in NETWORK:
        if base in UPLOAD_ALWAYS:
            v.block("%s connects to other computers; the agent may not" % base)
            return
        if base == "curl" and _flag_set(args) & {"-T", "--upload-file", "-d", "--data", "--data-binary", "--data-raw", "--data-urlencode", "-F", "--form", "-X", "--request"}:
            v.block("curl would send data to a server; uploading is never allowed")
            return
        if base == "wget" and _flag_set(args) & {"--post-data", "--post-file", "--method", "--body-file", "--body-data"}:
            v.block("wget would send data to a server; uploading is never allowed")
            return
        if not ctx.allow_network:
            v.block("%s uses the internet, which is off for the agent (the student can allow downloads with --web)" % base)
            return
        v.notes.append("%s uses the internet" % base)
        # where downloads go
        if base == "curl":
            for i, a in enumerate(args):
                if a.text in ("-o", "--output") and i + 1 < len(args):
                    _check_write(args[i + 1], ctx, v, "the download")
        if base == "wget":
            for i, a in enumerate(args):
                if a.text in ("-O", "--output-document", "-P", "--directory-prefix") and i + 1 < len(args):
                    _check_write(args[i + 1], ctx, v, "the download")
    if base == "rsync":
        if any(re.match(r"^[\w.\-]+@?[\w.\-]*:", a.text) and not a.text.startswith("/") for a in args):
            v.block("rsync to or from another computer is not allowed")
            return
    if base in ("blastn", "blastp", "blastx", "tblastn", "tblastx") and any(a.text == "-remote" for a in args) and not ctx.allow_network:
        v.block("BLAST with -remote uses NCBI's servers over the internet, which is off for the agent")
    if base in ("docker", "podman"):
        v.block("%s can do anything on the computer; the agent may not use it" % base)
        return
    if base in ("singularity", "apptainer") and args and args[0].text in ("pull", "build", "push", "remote", "registry"):
        v.block("%s %s downloads or builds a container; use the images that are there" % (base, args[0].text))
        return
    if base == "nextflow" and args and args[0].text in ("pull", "clone", "self-update"):
        v.block("nextflow %s downloads pipelines" % args[0].text)
        return
    if base == "nextflow" and args and args[0].text == "run" and len(args) > 1 and "/" in args[1].text and not os.path.exists(args[1].text) and not ctx.allow_network:
        v.block("nextflow run %s would download the pipeline from the internet" % args[1].text)
        return
    if base in ("kill",):
        return

    # reading credentials
    for a in args:
        if a.text.startswith(("/", "~")) or a.text.startswith("."):
            p = resolve(a, ctx)
            if p:
                s = _sensitive(p, ctx)
                if s:
                    v.block("it reads %s, which holds credentials or private history" % s)

    # is it there?
    if not _is_installed(name, ctx):
        v.block("%s is not installed on this computer, and nothing can be installed; use one of the installed programs" % base)
        return

    # interpreters: a REPL, inline code, or a script
    if base in REPLS:
        from_stdin = any(op in ("<<", "<<-", "<<<", "<") for op, _t in c.redirs) or any(a.text == "-" for a in args)
        if from_stdin and not [a for a in args if not a.text.startswith("-")]:
            return  # the code comes from a here-document or a file, which is checked separately
        _check_interpreter(base, args, v, ctx, depth)
        return
    if base in ("source", ".") and args:
        _check_script(args[0], v, ctx, depth, how="source")
        return
    if "/" in name:
        _check_script(first, v, ctx, depth, how="run")

    # what it writes
    _check_writes(base, args, v, ctx)


def _installs(base, args):
    a = [x.text for x in args]
    if base in ("pip", "pip3"):
        return bool(a) and a[0] in ("install", "uninstall", "download", "wheel")
    if base == "uv":
        return bool(a) and (a[0] in ("add", "remove", "sync", "tool") or a[:2] == ["pip", "install"] or a[:2] == ["pip", "uninstall"] or a[:2] == ["python", "install"])
    if base in ("conda", "mamba", "micromamba"):
        return bool(a) and (a[0] in ("install", "remove", "uninstall", "update", "upgrade", "create") or (a[0] == "env" and len(a) > 1 and a[1] in ("create", "update", "remove")))
    if base in ("python", "python3"):
        return len(a) >= 3 and a[0] == "-m" and a[1] in ("pip", "ensurepip", "conda") and a[2] in ("install", "uninstall", "download")
    if base == "R":
        return len(a) >= 2 and a[0] == "CMD" and a[1] == "INSTALL"
    return False


def _check_git(args, v, ctx):
    a = [x.text for x in args]
    sub = next((x for x in a if not x.startswith("-")), "")
    if sub in ("push", "send-email", "request-pull"):
        v.block("git %s sends work to another computer; the agent may not" % sub)
    elif sub in ("clone", "fetch", "pull", "ls-remote", "submodule", "remote") and not ctx.allow_network:
        v.block("git %s uses the internet, which is off for the agent" % sub)
    if "-C" in a:
        i = a.index("-C")
        if i + 1 < len(args):
            p = resolve(args[i + 1], ctx)
            if p and not writable(p, ctx) and sub in ("init", "add", "commit", "checkout", "reset", "rm", "mv", "clean", "stash", "merge", "rebase"):
                v.block("git -C %s would change a repository outside your folder" % args[i + 1].text)
    elif sub in ("checkout", "reset", "clean", "stash", "rm", "mv", "commit", "add", "merge", "rebase", "restore", "switch"):
        if not inside(os.path.realpath(ctx.cwd), ctx.run_dir):
            v.block("git %s would change a repository outside your folder" % sub)


def _check_interpreter(base, args, v, ctx, depth):
    a = [x.text for x in args]
    if base in ("sqlite3",):
        dbs = [x for x in args if not x.text.startswith("-")]
        if len(dbs) < 2:
            v.block("sqlite3 without an SQL statement would wait for input; give the statement on the command line")
        for x in dbs[:1]:
            _check_write(x, ctx, v, "the database")
        return
    if base in ("bash", "sh", "zsh", "dash", "ksh", "fish"):
        if "-c" in a:
            i = a.index("-c")
            if i + 1 < len(args):
                _analyse_into(args[i + 1].text, v, ctx, depth + 1)
            return
        scripts = [x for x in args if not x.text.startswith("-")]
        if not scripts:
            v.block("%s on its own starts an interactive shell; give it a script or -c 'commands'" % base)
            return
        _check_script(scripts[0], v, ctx, depth, how="run")
        return
    lang = {"python": "Python", "python3": "Python", "python2": "Python", "R": "R", "Rscript": "R", "perl": "Perl", "ruby": "Ruby", "julia": "Julia", "node": "JavaScript"}.get(base, base)
    code_flags = {"python": "-c", "python3": "-c", "python2": "-c", "Rscript": "-e", "R": "-e", "perl": "-e", "ruby": "-e", "julia": "-e", "node": "-e"}
    flag = code_flags.get(base)
    if base in ("python", "python3", "python2") and "-m" in a:
        i = a.index("-m")
        mod = a[i + 1] if i + 1 < len(a) else ""
        if mod in ("pip", "ensurepip", "conda"):
            v.block("installing Python packages is not allowed")
        elif mod in ("http.server", "jupyter", "notebook", "jupyterlab", "IPython", "idlelib", "pdb", "venv", "virtualenv"):
            v.block("python -m %s starts a server, a prompt or an environment; that is not allowed for the agent" % mod)
        return
    inline = []
    for i, x in enumerate(a):
        if x == flag or (base == "perl" and re.match(r"^-[a-zA-Z]*e$", x)) or (base == "perl" and x.startswith("-") and "e" in x[1:] and len(x) < 6):
            if i + 1 < len(args):
                inline.append(args[i + 1].text)
    if base == "R" and not inline:
        if "--vanilla" in a or "-f" in a or "--file" in " ".join(a) or "CMD" in a:
            pass
        else:
            v.block("R on its own starts an interactive prompt; use Rscript -e '...' or Rscript script.R")
            return
    if base == "perl" and any(re.match(r"^-[a-zA-Z]*i", x) for x in a):
        _check_writes("sed", args, v, ctx)  # perl -i edits files in place, as sed -i does
    if inline:
        for code in inline:
            _check_text_code(code, v, ctx, lang)
        return
    scripts = [x for x in args if not x.text.startswith("-")]
    if base in ("Rscript",) and scripts:
        _check_script(scripts[0], v, ctx, depth, how="run", lang=lang)
        return
    if not scripts:
        v.block("%s without a script or code would start an interactive prompt; give it a script, or code with %s" % (base, flag or "-c"))
        return
    _check_script(scripts[0], v, ctx, depth, how="run", lang=lang)


def _check_script(word, v, ctx, depth, how="run", lang=None):
    p = resolve(word, ctx)
    if p is None:
        v.confirm("the script to %s is given by a variable (%s)" % (how, word.raw))
        return
    if not os.path.exists(p):
        return  # it will fail by itself, with a message the agent can read
    if not inside(p, ctx.run_dir) and not any(inside(p, t) for t in ctx.tmp):
        v.confirm("it runs %s, a script from outside the agent's folder" % _show(p, ctx))
    if depth >= ctx.max_depth:
        return
    try:
        if os.path.getsize(p) > 400000:
            return
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    ext = os.path.splitext(p)[1].lower()
    first = text.split("\n", 1)[0]
    if lang in ("Python", "R", "Perl", "Ruby", "Julia", "JavaScript") or ext in (".py", ".r", ".pl", ".rb", ".jl", ".js") or re.match(r"^#!.*\b(python|Rscript|perl|ruby|julia|node)", first):
        _check_text_code(text, v, ctx, lang or ext.lstrip(".") or "script")
        return
    sub = Verdict()
    saved = (dict(ctx.vars), ctx.cwd)
    _analyse_into(text, sub, ctx, depth + 1)
    ctx.vars, ctx.cwd = saved
    for r in sub.reasons:
        if sub.level == "block":
            v.block("the script %s: %s" % (os.path.basename(p), r))
        else:
            v.confirm("the script %s: %s" % (os.path.basename(p), r))
    v.writes.extend(sub.writes)


# ------------------------------------------------------------------------------------------------
# Snakemake: the command line, its profiles, and what the workflow's rules would do
# ------------------------------------------------------------------------------------------------

# options that take a value (so that the value is not read as a target)
_SMK_VALUE = {
    "-s", "--snakefile", "-d", "--directory", "--profile", "--workflow-profile", "-e", "--executor", "-c", "--cores",
    "-j", "--jobs", "--configfile", "--configfiles", "--report", "--default-storage-provider", "--default-storage-prefix",
    "--local-storage-prefix", "--conda-prefix", "--apptainer-prefix", "--singularity-prefix", "--shadow-prefix",
    "--wrapper-prefix", "--jobscript", "--js", "--archive", "--cluster", "--cluster-sync", "--max-threads",
    "--runtime-source-cache-path", "--set-threads", "--set-resources", "--default-resources", "--default-res",
    "--resources", "--res", "--latency-wait", "--output-wait", "-w", "--rerun-triggers", "--keep-storage-local-copies",
}
_SMK_INSTALL = {"--use-conda", "--use-singularity", "--use-apptainer", "--conda-create-envs-only"}
_SMK_DEPLOY = {"--software-deployment-method", "--deployment-method", "--deployment", "--sdm"}
_SMK_CLUSTER = {"--cluster", "--cluster-sync", "--slurm", "--kubernetes", "--drmaa", "--tibanna", "--google-lifesciences",
                "--flux", "--az-batch", "--tes", "--jobscript", "--js"}
_SMK_NO_JOBS = {"-n", "--dry-run", "--dryrun", "--version", "-v", "-h", "--help", "--list", "-l", "--list-rules",
                "--list-target-rules", "--lt", "--dag", "--rulegraph", "--filegraph", "--d3dag", "--summary", "-S",
                "--detailed-summary", "-D", "--lint", "--unlock", "--cleanup-metadata", "--cm", "--containerize",
                "--print-compilation", "--list-changes", "--lc", "--list-input-changes", "--li", "--list-code-changes",
                "--list-params-changes", "--list-untracked", "--lu", "--compile"}


def _smk_values(args, names):
    """The values given to any of these options (--opt VALUE, --opt=VALUE; several for --sdm)."""
    out = []
    for i, a in enumerate(args):
        t = a.text
        for n in names:
            if t == n:
                j = i + 1
                while j < len(args) and not args[j].text.startswith("-"):
                    out.append(args[j].text)
                    j += 1
                    if n not in _SMK_DEPLOY:
                        break
            elif t.startswith(n + "=") and n.startswith("--"):
                out.append(t.split("=", 1)[1])
            elif not n.startswith("--") and t.startswith(n) and len(t) > len(n) and t[1] == n[1] and len(n) == 2:
                out.append(t[2:])  # -dDIR
    return out


def _smk_profile_text(name, workdir):
    """The text of a Snakemake profile's settings (config.yaml / profile.yaml), or None if not found."""
    cands = []
    if os.path.isabs(name) or os.sep in name or name.startswith("."):
        cands.append(name if os.path.isabs(name) else os.path.join(workdir, name))
    else:
        home = os.path.expanduser("~")
        cands += [os.path.join(workdir, name), os.path.join(workdir, "profiles", name),
                  os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "snakemake", name),
                  os.path.join("/etc/xdg/snakemake", name)]
    for c in cands:
        if os.path.isfile(c):
            files = [c]
        elif os.path.isdir(c):
            files = sorted(os.path.join(c, f) for f in os.listdir(c) if re.match(r"^(config|profile)(\.v[\d.+]+)?\.ya?ml$", f))
        else:
            continue
        text = ""
        for f in files:
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text += fh.read(200000) + "\n"
            except OSError:
                pass
        return text
    return None


def _smk_check_settings(keys_values, v, ctx, where):
    """The same rules for options given on the command line and in a profile ({option: [values]})."""
    for k, vals in keys_values.items():
        if k in _SMK_INSTALL or (k in _SMK_DEPLOY and any(x in ("conda", "apptainer", "singularity") for x in vals)):
            v.block("%s: Snakemake would install software (%s creates conda environments or fetches containers); "
                    "nothing may be installed: write the rules with the tools that are installed" % (where, k))
        elif k in ("-e", "--executor") and any(x not in ("local", "dryrun", "touch") for x in vals):
            v.confirm("%s: the jobs would be sent to %s, where they run outside AskTim!'s sandbox and checks" % (where, ", ".join(vals)))
        elif k in _SMK_CLUSTER:
            v.confirm("%s: %s sends the jobs to the cluster, where they run outside AskTim!'s sandbox and checks" % (where, k))
        elif k in ("--default-storage-provider", "--default-storage-prefix"):
            if not ctx.allow_network:
                v.block("%s: Snakemake would keep the workflow's files on remote storage, over the internet, which is off for the agent" % where)
            else:
                v.confirm("%s: the workflow's files would be kept on remote storage (%s)" % (where, ", ".join(vals)))
        elif k in ("--delete-all-output", "--delete-temp-output"):
            v.confirm("%s: %s deletes the files that the workflow made" % (where, k))


def _check_snakemake(args, v, ctx, depth):
    from . import snakefile as smk

    flags = _flag_set(args)
    given = {}
    for a in args:
        t = a.text
        if t.startswith("-") and not re.match(r"^-\d", t):
            key = t.split("=", 1)[0]
            if not key.startswith("--") and len(key) > 2:
                key = key[:2]  # -dDIR, -np ...
            given.setdefault(key, [])
    for key in list(given):
        given[key] = _smk_values(args, [key])
    for f in flags:  # short options given together (-np)
        given.setdefault(f, [])
    _smk_check_settings(given, v, ctx, "snakemake's options")
    # where it works and which Snakefile it reads
    workdir = ctx.cwd
    dirs = _smk_values(args, ["-d", "--directory"])
    if dirs:
        w = Word()
        w.text = w.raw = dirs[-1]
        p = resolve(w, ctx)
        if p is None or not writable(p, ctx):
            v.block("snakemake --directory %s would work (and write) outside your folder" % dirs[-1])
            return
        workdir = p
    files = _smk_values(args, ["-s", "--snakefile"])
    path = smk.find(workdir if not files else ctx.cwd, files[-1] if files else None)
    # profiles: --profile, a workflow profile (profiles/default next to the Snakefile or in the working folder)
    profiles = [x for x in _smk_values(args, ["--profile"]) if x != "none"]
    wprof = _smk_values(args, ["--workflow-profile"])
    if not wprof or wprof[-1] != "none":
        names = wprof[-1:] or ["default"]
        for base in [workdir] + ([os.path.dirname(path)] if path else []):
            for nm in names:
                cand = nm if os.path.isabs(nm) else os.path.join(base, nm)
                if os.path.exists(cand):
                    profiles.append(cand)
                elif not os.path.isabs(nm) and os.path.exists(os.path.join(base, "profiles", nm)):
                    profiles.append(os.path.join(base, "profiles", nm))
    for prof in dict.fromkeys(profiles):
        text = _smk_profile_text(prof, workdir)
        if text is None:
            v.confirm("snakemake uses the profile %s, which AskTim! cannot read; a profile can send jobs to a cluster or install software" % prof)
            continue
        found = {}
        for m in re.finditer(r"(?m)^\s*([\w-]+)\s*:\s*(.*)$", text):
            key = "--" + m.group(1).replace("_", "-")
            val = m.group(2).strip().strip("'\"")
            if val.lower() in ("false", "no", "off", "0", "none", "[]", ""):
                if key not in ("--executor",):
                    continue
            found.setdefault(key, []).extend(re.split(r"[\s,\[\]]+", val.strip("[]")) if val else [])
        _smk_check_settings(found, v, ctx, "the Snakemake profile %s" % os.path.basename(prof.rstrip(os.sep)) if prof else "a profile")
    if path is None:
        return  # snakemake will say that there is no Snakefile
    if not inside(path, ctx.run_dir) and not any(inside(path, t) for t in ctx.tmp):
        v.confirm("it runs the workflow %s, which is outside the agent's folder" % _show(path, ctx))
    _check_workflow(path, v, ctx, depth, workdir, set())


def _check_workflow(path, v, ctx, depth, workdir, seen):
    """What the rules of a Snakefile (and the files it includes) would do, checked like any command."""
    from . import snakefile as smk

    if path in seen or len(seen) > 20:
        return
    seen.add(path)
    try:
        if os.path.getsize(path) > 2000000:
            v.confirm("the Snakefile %s is too large for AskTim! to check" % os.path.basename(path))
            return
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    _check_workflow_text(text, path, v, ctx, depth, workdir, seen)


def _check_workflow_text(text, path, v, ctx, depth, workdir, seen):
    from . import snakefile as smk

    wf = smk.parse(text, path)
    name = os.path.basename(path)
    here = os.path.dirname(path)
    saved = (dict(ctx.vars), ctx.cwd)
    ctx.cwd = workdir

    def merge(sub, what):
        for r in sub.reasons:
            (v.block if sub.level == "block" else v.confirm)("%s: %s" % (what, r))
        v.writes.extend(sub.writes)

    try:
        # Python at the top of the file runs whenever Snakemake reads it, even for a dry run
        sub = Verdict()
        _check_text_code(wf.top_code, sub, ctx, "Python")
        for cmd in smk.shell_calls(wf.top_code):
            if cmd is smk.DYNAMIC:
                sub.confirm("a shell() call whose command AskTim! cannot read")
            else:
                _analyse_into(cmd, sub, ctx, depth + 1)
        merge(sub, "the Snakefile %s" % name)
        for d in wf.workdirs:
            w = Word()
            w.text = w.raw = d if d is not smk.DYNAMIC else "$X"
            p = resolve(w, ctx) if d is not smk.DYNAMIC else None
            if p is None or not writable(p, ctx):
                v.block("the Snakefile %s: workdir: would make Snakemake work outside your folder" % name)
            else:
                ctx.cwd = workdir = p
        if wf.remote_modules and not ctx.allow_network:
            v.block("the Snakefile %s uses modules from the internet (%s), which is off for the agent" % (name, ", ".join(wf.remote_modules)))
        elif wf.remote_modules:
            v.confirm("the Snakefile %s runs rules downloaded from the internet (%s)" % (name, ", ".join(wf.remote_modules)))
        if wf.storage:
            if not ctx.allow_network:
                v.block("the Snakefile %s keeps files on remote storage, over the internet, which is off for the agent" % name)
            else:
                v.confirm("the Snakefile %s keeps files on remote storage" % name)
        for rule in wf.rules:
            what = "the Snakefile's rule %s" % rule.name
            sub = Verdict()
            for _n, out in rule.outputs + rule.logs + rule.benchmarks:
                if out is smk.DYNAMIC:
                    continue
                w = Word()
                w.text = w.raw = smk.wildcard_free(out)
                p = resolve(w, ctx)
                if p is not None:
                    _check_write_path(p, ctx, sub, "its output")
            if rule.remote:
                if not ctx.allow_network:
                    sub.block("its files are on remote storage, over the internet, which is off for the agent")
                else:
                    sub.confirm("its files are on remote storage")
            if rule.shell is smk.DYNAMIC:
                sub.confirm("its shell command is built by Python code that AskTim! cannot read")
            elif isinstance(rule.shell, str):
                _analyse_into(smk.fill(rule.shell, rule), sub, ctx, depth + 1)
            if rule.run:
                _check_text_code(rule.run, sub, ctx, "Python")
                for cmd in smk.shell_calls(rule.run):
                    if cmd is smk.DYNAMIC:
                        sub.confirm("a shell() call whose command AskTim! cannot read")
                    else:
                        _analyse_into(smk.fill(cmd, rule), sub, ctx, depth + 1)
            if rule.script is not None:
                if rule.script is smk.DYNAMIC:
                    sub.confirm("it runs a script that AskTim! cannot work out")
                else:
                    w = Word()
                    w.text = w.raw = rule.script if os.path.isabs(rule.script) else os.path.join(here, rule.script)
                    ext = os.path.splitext(rule.script)[1].lower()
                    lang = {".py": "Python", ".r": "R", ".rmd": "R", ".jl": "Julia", ".rs": None, ".sh": None}.get(ext)
                    _check_script(w, sub, ctx, depth + 1, lang=lang)
            if rule.notebook is not None:
                sub.confirm("it runs a Jupyter notebook, which AskTim! does not check")
            if rule.wrapper is not None:
                if not ctx.allow_network:
                    sub.block("it uses a Snakemake wrapper, which is downloaded from the internet (off for the agent); "
                              "write a shell command with the installed tools instead")
                else:
                    sub.confirm("it runs a Snakemake wrapper, code downloaded from the internet (and wrappers usually need --use-conda)")
            merge(sub, what)
        for inc in wf.includes:
            if inc is smk.DYNAMIC:
                v.confirm("the Snakefile %s includes a file that AskTim! cannot work out" % name)
                continue
            p = os.path.realpath(inc if os.path.isabs(inc) else os.path.join(here, inc))
            if os.path.isfile(p):
                _check_workflow(p, v, ctx, depth, ctx.cwd, seen)
    finally:
        ctx.vars, ctx.cwd = saved


# programs that write files next to their input (index files) or into a named prefix
_INDEXERS = {
    ("bwa", "index"): "next", ("samtools", "faidx"): "next", ("samtools", "index"): "next", ("bcftools", "index"): "next",
    ("tabix", None): "next", ("bwa-mem2", "index"): "next", ("picard", "CreateSequenceDictionary"): "next",
}


def _check_writes(base, args, v, ctx):
    a = [x.text for x in args]
    flags = _flag_set(args)
    pos = _path_args(args)
    if base == "cd":
        if pos:
            p = resolve(pos[0], ctx)
            ctx.cwd = p if p else ctx.cwd
        elif not a:
            ctx.cwd = ctx.home
        return
    if base in ("pushd",) and pos:
        p = resolve(pos[0], ctx)
        if p:
            ctx.cwd = p
        return
    if base == "tee":
        for x in pos:
            _check_write(x, ctx, v, "tee")
        return
    if base in DELETERS:
        for x in pos:
            _check_write(x, ctx, v, base, follow=False)  # rm removes a link, not what it points to
        if base == "rm" and ("-r" in flags or "-R" in flags or "--recursive" in flags) and v.level != "block":
            if any(x.dynamic or x.text in (".", "./", "*", "..") for x in pos):
                v.confirm("rm -r with a wildcard or a whole folder deletes a lot at once")
        return
    if base in ("mkdir", "touch", "truncate", "chmod", "mkfifo", "mktemp"):
        targets = pos[1:] if base == "chmod" and pos else pos
        if base == "truncate":
            targets = [x for i, x in enumerate(args) if not x.text.startswith("-") and not (i > 0 and args[i - 1].text in ("-s", "--size", "-r", "--reference"))]
        if base == "mktemp":
            return
        for x in targets:
            _check_write(x, ctx, v, base)
        return
    if base in ("cp", "mv", "ln", "install", "rsync", "scp"):
        if "-t" in a or "--target-directory" in " ".join(a):
            i = a.index("-t") if "-t" in a else None
            if i is not None and i + 1 < len(args):
                _check_write(args[i + 1], ctx, v, base)
            return
        symbolic = base == "ln" and ("-s" in flags or "--symbolic" in flags or any(re.match(r"^-[a-zA-Z]*s", t) for t in a))
        if base == "ln":
            sources = pos[:-1] if len(pos) >= 2 else pos
            dest = pos[-1] if len(pos) >= 2 else None
            for src in sources:
                target = resolve(src, ctx) if not symbolic or src.text.startswith("/") else _link_target(src, dest, ctx)
                if target is None:
                    continue
                if not symbolic and not writable(target, ctx):
                    v.block("a hard link to %s would let a later write change the original; use ln -s, or copy the file" % _show(target, ctx))
                elif symbolic and os.path.isdir(target) and not writable(target, ctx):
                    v.block("a link to the folder %s would let later commands write outside your folder; link single files instead" % _show(target, ctx))
                # where the link will be, so that a later write through it (in this command) is checked
                if dest is None:
                    lw = Word()
                    lw.text = lw.raw = os.path.basename(src.text.rstrip("/")) or "x"
                else:
                    lw = dest
                lp = resolve(lw, ctx, follow=False)
                if lp is not None:
                    if os.path.isdir(lp) and not os.path.islink(lp):
                        lp = os.path.join(lp, os.path.basename(target))
                    _check_write_path(lp, ctx, v, "ln")
                    ctx.links[lp] = target
            return
        if base == "cp" and ("-l" in flags or "--link" in flags):
            for src in pos[:-1]:
                t = resolve(src, ctx)
                if t is not None and not writable(t, ctx):
                    v.block("cp --link makes a hard link to %s, so a later write would change the original; copy the file instead" % _show(t, ctx))
        if len(pos) >= 2:
            _check_write(pos[-1], ctx, v, base)
            if base == "mv":
                for x in pos[:-1]:
                    _check_write(x, ctx, v, "mv (it removes the original)", follow=False)
        elif len(pos) == 1 and base == "cp":
            # cp /path/file  (into the current folder)
            nm = os.path.basename(pos[0].text.rstrip("/")) or "x"
            tw = Word()
            tw.text = nm
            tw.raw = nm
            _check_write(tw, ctx, v, base)
        return
    if base in ("sed", "perl") and ("-i" in flags or any(re.match(r"^-[a-zA-Z]*i", t) or t.startswith("--in-place") for t in a)):
        files = pos[1:] if base == "sed" and not any(t in ("-e", "-f", "--expression", "--file") for t in a) else pos
        if base == "perl":
            files = [x for x in pos if not re.match(r"^s/|^tr/|^y/", x.text)][1:] if pos else []
        for x in files:
            _check_write(x, ctx, v, "%s -i (it edits the file in place)" % base)
        return
    if base in COMPRESSORS:
        if flags & {"-c", "--stdout", "--to-stdout", "-l", "--list", "-t", "--test", "-h", "--help", "-V", "--version"}:
            return
        keep = bool(flags & {"-k", "--keep"})
        for x in pos:
            p = resolve(x, ctx)
            if p is None:
                v.confirm("%s works on files named by a variable" % base)
                continue
            if not writable(p, ctx):
                v.block("%s %s would %s next to it, outside your folder (use %s -c FILE > ./NEW, or copy the file into your folder first)" % (
                    base, x.text, "write a compressed copy" if keep else "replace the file", base))
            elif not keep and protected(p, ctx):
                v.confirm("%s would replace %s, which was in your folder before the agent started (-k keeps it)" % (base, _show(p, ctx)))
        return
    if base in ("tar",):
        if flags & {"-x", "--extract", "--get"} or (a and re.match(r"^[a-zA-Z]*x", a[0]) and not a[0].startswith("-")):
            dest = ctx.cwd
            if "-C" in a:
                i = a.index("-C")
                if i + 1 < len(args):
                    dest = resolve(args[i + 1], ctx) or dest
            if not writable(dest, ctx):
                v.block("tar would unpack outside your folder")
        if flags & {"-c", "--create"} or (a and re.match(r"^[a-zA-Z]*c", a[0]) and not a[0].startswith("-")):
            for i, x in enumerate(a):
                if (x in ("-f", "--file") or (x.startswith("-") and x.endswith("f"))) and i + 1 < len(args):
                    _check_write(args[i + 1], ctx, v, "tar's archive")
        return
    if base in ("unzip",):
        dest = ctx.cwd
        if "-d" in a:
            i = a.index("-d")
            if i + 1 < len(args):
                dest = resolve(args[i + 1], ctx) or dest
        if not ("-l" in flags or "-t" in flags or "-p" in flags) and not writable(dest, ctx):
            v.block("unzip would unpack outside your folder")
        return
    if base == "split":
        takes_value = {"-l", "-b", "-C", "-n", "-a", "-t", "--lines", "--bytes", "--line-bytes", "--number", "--suffix-length",
                       "--separator", "--additional-suffix", "--filter"}
        sp, skip = [], False
        for x in args:
            if skip:
                skip = False
            elif x.text in takes_value:
                skip = True
            elif not (x.text.startswith("-") and len(x.text) > 1):
                sp.append(x)
        prefix = sp[1] if len(sp) > 1 else None
        if prefix is not None:
            _check_write(prefix, ctx, v, "split")
        elif not writable(ctx.cwd, ctx):
            v.block("split would write its pieces outside your folder")
        return
    if base == "find":
        if any(t in ("-delete",) for t in a):
            roots = [x for x in args if not x.text.startswith("-") and not x.text.startswith("(") and x.text not in ("!",)][:1] or [None]
            for r in roots:
                p = resolve(r, ctx) if r is not None else ctx.cwd
                if p is None or not writable(p, ctx):
                    v.block("find -delete would delete outside your folder")
                else:
                    v.confirm("find -delete deletes every file it finds")
        if any(t in ("-exec", "-execdir", "-ok", "-okdir") for t in a):
            i = next(i for i, t in enumerate(a) if t in ("-exec", "-execdir", "-ok", "-okdir"))
            inner = []
            for x in args[i + 1:]:
                if x.text in (";", "+", "\\;"):
                    break
                inner.append(x)
            if inner:
                sub = Simple()
                sub.words = [x for x in inner if x.text != "{}"]
                _check_simple(sub, v, ctx, ctx.max_depth)
                if os.path.basename(inner[0].text) in DELETERS or os.path.basename(inner[0].text) in ("mv",):
                    root = [x for x in args if not x.text.startswith("-")][:1]
                    p = resolve(root[0], ctx) if root else ctx.cwd
                    if p is None or not writable(p, ctx):
                        v.block("find -exec %s would change files outside your folder" % inner[0].text)
                    else:
                        v.confirm("find -exec %s changes every file it finds" % inner[0].text)
        return
    # tools that write next to their input, or into a prefix
    sub = a[0] if a else None
    key = (base, sub) if (base, sub) in _INDEXERS else (base, None) if (base, None) in _INDEXERS else None
    if key:
        inputs = _path_args(args)[1:] if key[1] else _path_args(args)
        for x in inputs[:1]:
            p = resolve(x, ctx)
            if p and not writable(os.path.dirname(p), ctx):
                v.block("%s %s writes its index next to %s, outside your folder: link the file into your folder first (ln -s %s .) and index the link" % (
                    base, sub or "", x.text, x.text))
    if base in ("bowtie2-build", "hisat2-build", "bowtie-build", "STAR", "makeblastdb", "salmon", "kallisto", "minimap2", "diamond", "hmmpress", "bwa-mem2"):
        _check_prefixes(base, args, v, ctx)
    # -o / --output / --out FILE for any program
    for i, x in enumerate(args):
        t = x.text
        val = None
        if t in ("-o", "--output", "--out", "--outdir", "-O", "--output-dir", "--outFileNamePrefix", "-out", "--prefix", "--output-file", "-od", "--dir", "--outFile", "--output-prefix") and i + 1 < len(args):
            if base in ("grep", "egrep", "fgrep", "rg", "sort") and t == "-o" and base != "sort":
                continue
            if base in ("ls", "ps", "column", "cut", "uniq", "wc", "xargs", "env", "awk", "sed", "jq", "tr", "date", "printf", "echo"):
                continue
            if base == "sort" and t == "-o":
                val = args[i + 1]
            else:
                val = args[i + 1]
        elif re.match(r"^--(output|out|outdir|output-dir|prefix|output-file)=", t):
            val = Word()
            val.text = t.split("=", 1)[1]
            val.raw = val.text
            val.dynamic = x.dynamic
        if val is not None and val.text not in ("-", "/dev/stdout"):
            _check_write(val, ctx, v, "%s's output" % base)


def _check_prefixes(base, args, v, ctx):
    pos = _path_args(args)
    if base in ("bowtie2-build", "hisat2-build", "bowtie-build") and len(pos) >= 2:
        _check_write(pos[-1], ctx, v, "%s's index" % base)
    if base == "minimap2" and "-d" in [a.text for a in args]:
        i = [a.text for a in args].index("-d")
        if i + 1 < len(args):
            _check_write(args[i + 1], ctx, v, "minimap2's index")
    if base == "makeblastdb":
        a = [x.text for x in args]
        if "-out" not in a and "-in" in a:
            i = a.index("-in")
            if i + 1 < len(args):
                p = resolve(args[i + 1], ctx)
                if p and not writable(os.path.dirname(p), ctx):
                    v.block("makeblastdb writes the database next to %s, outside your folder; add -out ./NAME" % args[i + 1].text)
    if base in ("salmon", "kallisto") and len(args) and args[0].text == "index":
        a = [x.text for x in args]
        for flag in ("-i", "--index"):
            if flag in a:
                i = a.index(flag)
                if i + 1 < len(args):
                    _check_write(args[i + 1], ctx, v, "%s's index" % base)
    if base == "hmmpress":
        for x in pos[:1]:
            p = resolve(x, ctx)
            if p and not writable(os.path.dirname(p), ctx):
                v.block("hmmpress writes its files next to %s, outside your folder" % x.text)
    if base == "STAR":
        a = [x.text for x in args]
        if "--genomeDir" in a and "genomeGenerate" in " ".join(a):
            i = a.index("--genomeDir")
            if i + 1 < len(args):
                _check_write(args[i + 1], ctx, v, "STAR's genome index")


def _analyse_into(text, v, ctx, depth=0):
    p = tokenize(text)
    if p.error:
        v.confirm("AskTim! could not read the command fully (%s)" % p.error)
    # functions defined in this text
    for m in re.finditer(r"(?:^|[\s;&|(])(?:function\s+)?([A-Za-z_][\w\-]*)\s*\(\s*\)\s*\{?", text):
        ctx.functions.add(m.group(1))
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}", text):
        v.block("that is a fork bomb")
    cmds = split_commands(p)
    heredoc_index = 0
    for c in cmds:
        _check_simple(c, v, ctx, depth)
        for op, target in c.redirs:
            if op in ("<<", "<<-"):
                if heredoc_index < len(p.heredocs):
                    delim, body, quoted = p.heredocs[heredoc_index]
                    heredoc_index += 1
                    base = os.path.basename(c.words[0].text) if c.words else ""
                    if base in ("python", "python3", "Rscript", "R", "perl"):
                        _check_text_code(body, v, ctx, base)
                    elif base in ("bash", "sh", "zsh"):
                        _analyse_into(body, v, ctx, depth + 1)
    for sub in p.subs:
        if depth < ctx.max_depth:
            _analyse_into(sub, v, ctx, depth + 1)


# characters that change how a command looks on screen without being seen (tab and newline are fine)
_INVISIBLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def named_dirs(command, cwd, limit=20):
    """The existing folders that a command names (ls ../../data, find results): the names in its output
    are names of things there. (For the data filter, which keeps names of files that exist.)"""
    p = tokenize(command)
    ctx = Context(cwd)
    out = []
    for c in split_commands(p):
        for w in c.words[1:]:
            if w.dynamic or w.text.startswith("-"):
                continue
            q = resolve(w, ctx)
            if q and q not in out and os.path.isdir(q):
                out.append(q)
                if len(out) >= limit:
                    return out
    return out


def check(command, run_dir, allow_network=False, student_dir=None, cwd=None, protect=None):
    """The verdict on a command the agent wants to run in run_dir."""
    v = Verdict()
    ctx = Context(run_dir, allow_network=allow_network, student_dir=student_dir)
    ctx.protect = protect
    if cwd:
        ctx.cwd = os.path.realpath(cwd)
    if not command or not command.strip():
        v.block("the command is empty")
        return v
    if len(command) > 100000:
        v.block("the command is too long")
        return v
    if _INVISIBLE.search(command):
        v.block("the command contains invisible control characters (escape codes, carriage returns or direction marks), "
                "so it would not look the way it runs; write it in plain characters")
        return v
    _analyse_into(command, v, ctx, 0)
    v.output_kind = output_kind(command)
    return v


def check_file_write(path, run_dir, content=None, student_dir=None, protect=None):
    """May the agent write this file? (for the write_file tool)"""
    v = Verdict()
    ctx = Context(run_dir, student_dir=student_dir)
    ctx.protect = protect
    w = Word()
    w.text = path
    w.raw = path
    p = resolve(w, ctx)
    if p is None or not writable(p, ctx):
        v.block("files may be written only inside your folder (%s)" % ctx.run_dir)
        return v, p
    if protected(p, ctx):
        v.confirm("it would replace %s, which was in your folder before the agent started" % _show(p, ctx))
    if os.path.basename(p) in ("RUN.md", "run.json", "rerun.sh") and os.path.dirname(p) == ctx.run_dir:
        v.block("%s is the record that AskTim! writes; choose another name" % os.path.basename(p))
    if content is not None and _HIDDEN_IN_CODE.search(content):
        v.block("the file contains escape codes or direction-override characters, which make it look different "
                "from what it is; write it in plain characters")
    if content is not None and p.endswith((".sh", ".bash")):
        sub = Verdict()
        _analyse_into(content, sub, Context(run_dir, student_dir=student_dir), 1)
        if sub.level == "block":
            v.notes.append("when it runs, the script would be refused: " + "; ".join(sub.reasons[:3]))
    if content is not None and p.endswith((".py", ".R", ".r")):
        sub = Verdict()
        _check_text_code(content, sub, Context(run_dir, student_dir=student_dir), "Python" if p.endswith(".py") else "R")
        if sub.level == "block":
            v.notes.append("when it runs, the script would be refused: " + "; ".join(sub.reasons[:3]))
    if content is not None and (os.path.basename(p) in ("Snakefile", "snakefile") or p.endswith(".smk")):
        sub = Verdict()
        sctx = Context(run_dir, student_dir=student_dir)
        sctx.protect = protect
        _check_workflow_text(content, p, sub, sctx, 0, os.path.dirname(p), set())
        if sub.level != "ok":
            v.notes.append("when snakemake runs it, the workflow would be %s: %s" % (
                "refused" if sub.level == "block" else "stopped to ask the student", "; ".join(sub.reasons[:3])))
    return v, p


def output_kind(command):
    """How the data filter should treat what this command prints."""
    p = tokenize(command)
    if p.error:
        return "default"
    cmds = split_commands(p)
    if not cmds:
        return "default"
    kinds = []
    # the last command of each pipeline decides what that pipeline prints
    toks = p.tokens
    last_of_pipelines = []
    cur = []
    for c in cmds:
        cur.append(c)
    groups = []
    group = []
    i = 0
    for t in toks:
        if isinstance(t, tuple) and t[1] in ("&&", "||", ";", "\n"):
            if group:
                groups.append(group)
            group = []
        else:
            group.append(t)
    if group:
        groups.append(group)
    for g in groups:
        sub = Parsed()
        sub.tokens = g
        sc = split_commands(sub)
        if sc:
            last_of_pipelines.append(sc[-1])
    for c in last_of_pipelines:
        _, words = _strip_wrappers(c.words)
        if not words:
            kinds.append("summary")
            continue
        base = os.path.basename(words[0].text)
        args = [w.text for w in words[1:]]
        if any(a in ("--help", "-help", "--version", "-version", "--usage") for a in args) or (args[:1] in (["help"], ["version"], ["--help"])) or (len(args) == 1 and args[0] in ("-h", "-v", "-V")) or base in ("man", "whatis", "apropos", "which", "type", "whereis"):
            kinds.append("help")
        elif base in ("cd", "mkdir", "touch", "cp", "mv", "ln", "rm", "export", "set", "true", "test", "["):
            kinds.append("summary")
        elif base in SUMMARY_COMMANDS:
            kinds.append("summary")
        elif base in ("seqkit",) and args[:1] == ["stats"] or base == "samtools" and args[:1] in (["flagstat"], ["idxstats"], ["quickcheck"], ["--version"]) or base == "bcftools" and args[:2] in (["index", "-n"], ["index", "-s"], ["index", "--nrecords"]):
            kinds.append("summary")
        elif base in ("conda", "mamba", "micromamba") and args[:1] in (["list"], ["info"], ["env"]) or base in ("pip", "pip3") and args[:1] in (["list"], ["show"], ["freeze"]):
            kinds.append("summary")
        elif base in CODE_READERS and base not in ("sed", "awk"):
            from .fileinfo import is_code_file

            files = [a for a in args if not a.startswith("-") and not re.fullmatch(r"\d+", a)]
            kinds.append("code" if files and all(is_code_file(f) for f in files) else "default")
        else:
            kinds.append("default")
    if not kinds:
        return "default"
    if all(k == "help" for k in kinds):
        return "help"
    if all(k in ("help", "summary") for k in kinds):
        return "summary"
    if all(k in ("help", "summary", "code") for k in kinds):
        return "code"
    return "default"
