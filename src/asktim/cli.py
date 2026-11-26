"""The AskTim! command.

    AskTim! Claude                     store a Claude API key (you are asked for it; it is not shown)
    AskTim! Claude KEY                 the same, with the key on the command line
    AskTim! "my question"              ask (chat mode: explains and suggests, runs nothing)
    AskTim! Agent "my task"            the agent does the task, asking before each step
    AskTim! status | tools | privacy | help
"""

import getpass
import os
import sys

from . import __version__
from . import config as _config

HELP = """\
AskTim! – an AI helper for bioinformatics that keeps your data out of the AI.

Set up (once):
  AskTim! Claude              store your Claude API key (paste it when asked; it is not shown)
  AskTim! Gemini              …or a Google Gemini key        AskTim! OpenAI --url URL  …or another service
  AskTim! setup-shell         let AskTim! see your latest commands and which ones failed

Ask:
  AskTim! "how do I sort a BAM file?"
  AskTim! why                 explain why your last command failed
  some_command 2>&1 | AskTim! "what does this error mean?"
  AskTim! --file data/counts.csv "how do I plot column 2 against 3?"
  AskTim!                     a conversation (type your questions; Ctrl+D to stop)

Let the agent do it (it asks before each step; nothing is installed; data stay out of the AI):
  AskTim! Agent "map the reads in data/ to ref.fa and count how many map"
      --plan      show a plan first           --auto     run without asking (Ctrl+C stops)
      --script    write one script, analysis.sh, and get it working
      --web       allow downloads (curl, wget)  --here   work in this folder, not in asktim-runs/
      --steps N   at most N steps             --timeout S  seconds per command

More:
  AskTim! show "question"     print exactly what would be sent, and send nothing
  AskTim! status              settings, key, model, safeguards
  AskTim! tools               the programs and packages AskTim! found
  AskTim! privacy             what is sent to the AI, and what never is
  AskTim! model NAME          change the model          AskTim! use claude|gemini|openai
  AskTim! log                 your recent questions     AskTim! statement   a draft statement on your use of AI
  AskTim! new                 forget the conversation   AskTim! forget      delete your stored key(s)

Options for questions: --no-history, --history N, --no-files, --new, --offline, --model NAME
Put your question in quotes. (In zsh, an unquoted ? or * is read by the shell first.)
In a notebook: `import asktim`, then type AskTim! … in a cell. `asktim` is the same command without the "!".
"""

SIMPLE_COMMANDS = {"status", "tools", "help", "version", "privacy", "new", "reset", "forget", "logout", "log", "statement",
                   "setup-shell", "model", "use", "why", "explain", "doctor", "insert", "cells"}


class Args(object):
    def __init__(self):
        self.pos = []
        self.files = []
        self.opts = {}


FLAGS = {"--plan", "--auto", "--script", "--here", "--web", "--no-web", "--approve", "--no-history", "--no-files", "--new", "--offline",
         "--strict", "--no-check", "--remove", "--refresh", "--all", "--color", "--no-color", "--help", "--version", "--show", "--dry-run", "--yes"}
VALUE_OPTS = {"--model", "--provider", "--history", "--file", "-f", "--steps", "--timeout", "--url", "--key", "--since", "--shell", "--script-name", "--effort", "--folder"}


def parse(argv):
    a = Args()
    i = 0
    rest_is_text = False
    while i < len(argv):
        t = argv[i]
        if rest_is_text:
            a.pos.append(t)
        elif t == "--":
            rest_is_text = True
        elif t in ("-h",):
            a.opts["--help"] = True
        elif t in ("-V",):
            a.opts["--version"] = True
        elif t in FLAGS:
            a.opts[t] = True
        elif t.split("=", 1)[0] in VALUE_OPTS:
            if "=" in t:
                k, v = t.split("=", 1)
            else:
                k, v = t, (argv[i + 1] if i + 1 < len(argv) else None)
                i += 1
            if k in ("--file", "-f"):
                a.files.append(v)
            else:
                a.opts[k] = v
        else:
            a.pos.append(t)
        i += 1
    return a


def _read_stdin(wait=0.5):
    """Text piped into AskTim! (cmd 2>&1 | AskTim! ...), or None.

    Standard input is read only if something arrives on it soon: under Jupyter's `!`, in some
    editors and in cron, it is a pipe that never sends anything, and reading it would hang.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        fd = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return None
    chunks = []
    total = 0
    limit = 2000000
    if os.name == "posix":
        import select

        try:
            ready, _, _ = select.select([fd], [], [], wait)
        except (OSError, ValueError):
            return None
        if not ready:
            return None
        while total < limit:
            try:
                ready, _, _ = select.select([fd], [], [], 30)
            except (OSError, ValueError):
                break
            if not ready:
                break
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            total += len(b)
    else:
        import threading

        box = []

        def reader():
            try:
                box.append(sys.stdin.buffer.read(limit))
            except Exception:
                pass

        th = threading.Thread(target=reader)
        th.daemon = True
        th.start()
        th.join(wait + 30)
        chunks = box
    data = b"".join(chunks).decode("utf-8", "replace")
    return data if data.strip() else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        return _main(argv)
    except KeyboardInterrupt:
        sys.stderr.write("\n(stopped)\n")
        return 130
    except BrokenPipeError:
        return 0


def _cfg(a):
    over = {}
    if a.opts.get("--strict"):
        over["privacy"] = "strict"
    if a.opts.get("--provider"):
        over["provider"] = a.opts["--provider"]
    if a.opts.get("--no-color"):
        over["color"] = "never"
    if a.opts.get("--color"):
        over["color"] = "always"
    if a.opts.get("--effort"):
        over["effort.chat"] = a.opts["--effort"]
        over["effort.agent"] = a.opts["--effort"]
    return _config.Config(over)


def _main(argv):
    a = parse(argv)
    if a.opts.get("--version") or (len(a.pos) == 1 and a.pos[0].lower() == "version"):
        print("AskTim! %s" % __version__)
        return 0
    cfg = _cfg(a)
    from .ui import TerminalUI

    ui = TerminalUI(cfg)
    if a.opts.get("--help") or (len(a.pos) == 1 and a.pos[0].lower() in ("help", "-help")):
        print(HELP)
        return 0
    first = a.pos[0].lower() if a.pos else ""

    # set-up: AskTim! Claude [KEY]
    prov = _config.normalise_provider(first)
    if prov and (len(a.pos) == 1 or (len(a.pos) == 2 and _config.looks_like_key(a.pos[1])) or a.opts.get("--url") or a.opts.get("--key")):
        key = a.opts.get("--key") or (a.pos[1] if len(a.pos) == 2 else None)
        return setup(cfg, ui, prov, key, a, key_on_command_line=bool(key))

    if first == "agent":
        task = " ".join(a.pos[1:]).strip()
        stdin = _read_stdin()
        if stdin and not task:
            task = stdin.strip()
        if not task:
            if not ui.interactive:
                ui.error("Give the agent a task:  AskTim! Agent \"what it should do\"")
                return 2
            task = ui.t.ask("What should the agent do? ")
            if not task:
                return 2
        return run_agent(cfg, ui, task, a)

    if first == "show" and len(a.pos) >= 2:
        from . import chat

        chat.ask(cfg, ui, " ".join(a.pos[1:]), files=a.files, dry_run=True, send_history=False if a.opts.get("--no-history") else None,
                 history_n=a.opts.get("--history"), send_files=False if a.opts.get("--no-files") else None)
        return 0

    if (len(a.pos) == 1 and first in SIMPLE_COMMANDS) or (len(a.pos) == 2 and first in ("model", "use", "forget", "log", "insert")):
        return command(cfg, ui, first, a)

    question = " ".join(a.pos).strip()
    stdin = _read_stdin()
    if not question and not stdin:
        return conversation(cfg, ui, a)
    if a.opts.get("--offline"):
        from .chat import offline_answer

        offline_answer(ui, question, stdin)
        return 0
    from . import chat

    files = [f for f in a.files if f]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        ui.error("There is no file %s." % ", ".join(missing))
        return 2
    try:
        ans = chat.ask(cfg, ui, question, files=files, stdin_text=stdin, new=bool(a.opts.get("--new")), model=a.opts.get("--model"),
                       send_history=False if a.opts.get("--no-history") else None, history_n=a.opts.get("--history"),
                       send_files=False if a.opts.get("--no-files") else None)
    except Exception as e:  # a last resort: never show a student a raw traceback for a network problem
        if os.environ.get("ASKTIM_DEBUG"):
            raise
        ui.error("AskTim! had a problem: %s" % e)
        return 1
    return 0 if ans is not None else 1


def conversation(cfg, ui, a):
    """`AskTim!` on its own: a conversation in the terminal."""
    from . import chat
    from .providers import make_provider, ProviderError

    t = ui.t
    try:
        make_provider(cfg)
    except ProviderError as e:
        print(HELP)
        t.note(str(e).split("\n")[0])
        return 0
    if not ui.interactive:
        print(HELP)
        return 0
    t.line(t.s("AskTim!", "bold") + t.s(" – ask about the tools on this computer. Your data stay here. Empty line or Ctrl+D to stop; /agent TASK for the agent; /new to start afresh.", "gray"))
    new = bool(a.opts.get("--new"))
    while True:
        try:
            q = input(t.s("\nAskTim!> ", "cyan", "bold"))
        except EOFError:
            t.line()
            return 0
        q = q.strip()
        if not q or q in ("exit", "quit", "/exit", "/quit", "bye"):
            return 0
        if q == "/new":
            from . import logbook

            logbook.clear_chat()
            t.note("A new conversation.")
            continue
        if q.startswith("/agent "):
            run_agent(cfg, ui, q[7:], a)
            continue
        chat.ask(cfg, ui, q, new=new)
        new = False


def setup(cfg, ui, prov, key, a, key_on_command_line=False):
    t = ui.t
    name = {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI-compatible"}[prov]
    if prov == "openai" and a.opts.get("--url"):
        _config.save_user_setting("openai_base_url", a.opts["--url"].rstrip("/"))
        cfg = _config.Config()
    if a.opts.get("--model"):
        _config.save_user_setting("models." + prov, a.opts["--model"])
        cfg = _config.Config()
    if not key:
        if prov == "openai" and any(h in cfg.get("openai_base_url") for h in ("localhost", "127.0.0.1")):
            key = ""
        elif not ui.interactive:
            ui.error("Give the key:  AskTim! %s KEY   (or run this in a terminal to be asked for it)" % name)
            return 2
        else:
            where = {"claude": "console.anthropic.com → Settings → API keys", "gemini": "aistudio.google.com/apikey", "openai": "your service's website"}[prov]
            t.line("Paste your %s API key (from %s). It is not shown as you paste it." % (name, where))
            try:
                key = getpass.getpass("Key: ").strip()
            except (EOFError, KeyboardInterrupt):
                return 1
            if not key and prov != "openai":
                ui.error("No key was given.")
                return 2
    if prov == "claude" and key and not key.startswith("sk-ant-"):
        t.warn("That does not look like a Claude key (they start with sk-ant-). Storing it anyway.")
    if key or prov == "openai":
        if not a.opts.get("--no-check"):
            ok = _check_key(cfg, ui, prov, key)
            if ok is False:
                return 1
        if key:
            where = _config.save_key(prov, key)
            t.good("Your %s key is stored in %s (readable by you only)." % (name, _short(where)))
    if cfg.provider != prov and cfg.is_locked("provider"):
        t.note("Your course's settings use %s, so AskTim! keeps using it; this key is stored for later." % _config.PROVIDER_NAMES[cfg.provider])
    else:
        if cfg.provider != prov:
            _config.save_user_setting("provider", prov)
        t.line("AskTim! will use %s (model %s). Try:  AskTim! \"what does samtools flagstat tell me?\"" % (name, _config.Config().model(prov)))
    if key_on_command_line:
        t.warn("Your key is now in your shell's history. Next time type just `AskTim! %s` and paste the key when asked. "
               "To remove it from bash's history now: history -d $((HISTCMD-1)). (Starting a command with a space keeps it out of history on most systems.)" % name)
    from . import history, shell_setup

    sh = history.detect_shell()
    if sh in ("bash", "zsh", "fish") and not shell_setup.installed(sh):
        t.note("Tip: AskTim! setup-shell lets AskTim! see your latest commands and which of them failed.")
    t.note("Remove the key at any time with:  AskTim! forget")
    return 0


def _short(p):
    from . import paths

    return paths.pretty(p)


def _check_key(cfg, ui, prov, key):
    """True: works; False: refused; None: could not check."""
    from .providers import Conversation, ProviderError, make_provider

    t = ui.t
    t.note("Checking the key with a tiny request…")
    try:
        p = make_provider(cfg, provider=prov, key=key)
        conv = Conversation()
        conv.user("Reply with the single word: ready")
        turn = p.complete("You are being tested for a connection. Reply with one word.", conv, purpose="chat")
        t.good("It works (%s answered)." % (turn.model or p.model))
        return True
    except ProviderError as e:
        if e.kind in ("auth", "credit", "notfound"):
            ui.error(str(e))
            if e.kind == "notfound":
                t.note("The key may be fine; the model was not found. Store it with --no-check and choose a model with: AskTim! model NAME")
            return False
        t.warn("Could not check the key (%s). It is stored anyway; AskTim! status shows it." % e)
        return None


def run_agent(cfg, ui, task, a):
    from .agent import Agent
    from .providers import ProviderError, make_provider

    mode = "plan" if a.opts.get("--plan") else "auto" if a.opts.get("--auto") else "script" if a.opts.get("--script") else "approve"
    if mode in ("approve", "plan") and not ui.interactive:
        ui.error("The agent needs you at the keyboard to approve its steps. Run it in a terminal, or let it work alone with --auto.")
        return 2
    if os.name == "nt" and not __import__("shutil").which("bash"):
        ui.error("The agent runs commands in bash, which this computer does not have. Use WSL or Git Bash, or ask questions without Agent.")
        return 2
    try:
        prov = make_provider(cfg, model=a.opts.get("--model"))
    except ProviderError as e:
        ui.error(str(e))
        return 3
    web = True if a.opts.get("--web") else (False if a.opts.get("--no-web") else None)
    if web and cfg.is_locked("agent.allow_network") and not cfg.get("agent.allow_network"):
        ui.warn("Your course's settings do not allow the agent to use the internet; --web is ignored.")
        web = False
    script_name = a.opts.get("--script-name") or "analysis.sh"
    ag = Agent(cfg, prov, ui, task, mode=mode, here=bool(a.opts.get("--here")), allow_network=web,
               max_steps=a.opts.get("--steps"), timeout=a.opts.get("--timeout"), script_name=script_name)
    rec = ag.run()
    return 0 if rec.get("outcome") == "done" else 1


def command(cfg, ui, name, a):
    t = ui.t
    arg = a.pos[1] if len(a.pos) > 1 else None
    if name == "status" or name == "doctor":
        return status(cfg, ui)
    if name == "tools":
        from . import inventory

        t.note("Looking for programs and packages…")
        inv = inventory.gather(refresh=True)
        for ln in inventory.describe(inv):
            t.line(ln)
        t.note("This is what AskTim! tells the AI is installed. The agent can use any program on your PATH, but nothing else.")
        return 0
    if name == "privacy":
        from . import logbook, paths

        print(PRIVACY.format(level=cfg.get("privacy"), log=paths.pretty(logbook.log_file())))
        return 0
    if name in ("new", "reset"):
        from . import logbook

        logbook.clear_chat()
        t.note("The conversation is forgotten; the next question starts afresh.")
        return 0
    if name in ("forget", "logout"):
        p = _config.normalise_provider(arg) if arg and arg.lower() != "all" else None
        gone = _config.forget_key(p)
        t.good("Deleted the stored key%s for: %s." % ("s" if len(gone) != 1 else "", ", ".join(gone))) if gone else t.note("There was no stored key to delete.")
        env = [e for p_ in _config.PROVIDERS for e in _config.KEY_ENV[p_] if os.environ.get(e)]
        if env:
            t.warn("A key is still set in the environment variable %s (remove it from your shell's settings)." % ", ".join(env))
        return 0
    if name == "log":
        from . import logbook

        n = int(arg) if arg and arg.isdigit() else 15
        es = logbook.entries(limit=n)
        if not es:
            t.note("Nothing in the log yet.")
        for e in es:
            t.line("%s  %-14s %s  %s" % (e.get("time", "")[:16].replace("T", " "), e.get("mode", ""), t.s(e.get("model", ""), "gray"), (e.get("question") or "")[:90].replace("\n", " ")))
        return 0
    if name == "statement":
        from . import logbook

        s = logbook.statement(since=a.opts.get("--since"), folder=None if a.opts.get("--all") else os.getcwd())
        if not s:
            s = logbook.statement(since=a.opts.get("--since"))
        print(s or "AskTim!'s log is empty, so there is nothing to put in a statement yet.")
        return 0
    if name == "setup-shell":
        from . import history, shell_setup

        sh = a.opts.get("--shell") or history.detect_shell()
        if sh not in ("bash", "zsh", "fish"):
            t.error("AskTim! can set up bash, zsh and fish; your shell is %s." % sh)
            return 2
        if a.opts.get("--remove"):
            f, done = shell_setup.remove(sh)
            t.good("Removed AskTim!'s lines from %s." % _short(f)) if done else t.note("AskTim!'s lines were not in %s." % _short(f))
            return 0
        f, had = shell_setup.install(sh)
        t.good("%s AskTim!'s lines in %s." % ("Updated" if had else "Added", _short(f)))
        t.line("They record each command you type and whether it worked (never its output), so AskTim! can help with the latest one.")
        if sh != "fish":
            t.line("Alt+A then puts the commands from AskTim!'s last answer on your command line, one at a time, for you to read and run.")
        t.line("Start a new terminal, or run:  source %s" % _short(f))
        t.note("To undo:  AskTim! setup-shell --remove")
        return 0
    if name == "model":
        if not arg:
            t.line("%s: %s" % (_config.PROVIDER_NAMES[cfg.provider], cfg.model()))
            return 0
        if cfg.is_locked("models." + cfg.provider):
            t.error("Your course's settings fix the model.")
            return 1
        _config.save_user_setting("models." + cfg.provider, arg)
        t.good("AskTim! will use %s for %s." % (arg, _config.PROVIDER_NAMES[cfg.provider]))
        return 0
    if name == "use":
        p = _config.normalise_provider(arg)
        if not p:
            t.error("Choose claude, gemini or openai.")
            return 2
        if cfg.is_locked("provider") and p != cfg.provider:
            t.error("Your course's settings fix the AI service (%s)." % _config.PROVIDER_NAMES[cfg.provider])
            return 1
        _config.save_user_setting("provider", p)
        key, _w = _config.find_key(p)
        t.good("AskTim! will use %s." % _config.PROVIDER_NAMES[p])
        if not key and p != "openai":
            t.note("There is no key for it yet:  AskTim! %s" % {"claude": "Claude", "gemini": "Gemini"}[p])
        return 0
    if name in ("why", "explain"):
        from . import chat

        stdin = _read_stdin()
        return 0 if chat.ask(cfg, ui, "Why did my last command fail, and how do I fix it?", stdin_text=stdin) is not None else 1
    if name in ("insert", "cells"):
        t.note("`AskTim! %s` works in a notebook (it adds cells there)." % name)
        return 0
    print(HELP)
    return 0


def status(cfg, ui):
    from . import history, paths, runner, shell_setup

    t = ui.t
    t.line(t.s("AskTim! %s" % __version__, "bold"))
    for p in _config.PROVIDERS:
        key, where = _config.find_key(p)
        mark = "→ " if p == cfg.provider else "  "
        t.line("%s%-26s model %-22s key %s" % (mark, _config.PROVIDER_NAMES[p], cfg.model(p) or "(none)", ("%s (%s)" % (_config.mask(key), where)) if key else "not set"))
    if cfg.provider == "openai":
        t.line("  address: %s" % cfg.get("openai_base_url"))
    t.line("Privacy: %s (AskTim! privacy explains it)" % cfg.get("privacy"))
    sh = history.detect_shell()
    hooked = shell_setup.installed(sh) if sh in ("bash", "zsh", "fish") else False
    t.line("Shell: %s; AskTim!'s shell integration %s%s" % (sh, "is installed" if hooked else "is not installed (AskTim! setup-shell)",
                                                           "; it is recording" if history.hook_active() else ""))
    sb, why = runner.make_sandbox(cfg.get("agent.sandbox") or "auto", os.getcwd(), os.getcwd(), False) if os.access(os.getcwd(), os.W_OK) else (None, "this folder cannot be written")
    t.line("Agent: at most %s steps, %s s per command, internet %s; %s" % (cfg.get("agent.max_steps"), cfg.get("agent.command_timeout"),
                                                                         "allowed" if cfg.get("agent.allow_network") else "off (unless --web)", sb.describe() if sb else "no sandbox (%s)" % why))
    if sb:
        sb.cleanup()
    t.note("Settings: %s%s" % (paths.pretty(_config.config_file()), ("; course settings: %s" % paths.site_config_file()) if os.path.exists(paths.site_config_file()) else ""))
    if cfg.data.get("locked"):
        t.note("Fixed by your course: %s" % ", ".join(cfg.data["locked"]))
    if cfg.get("course"):
        t.note("Course notes for the AI: %s" % cfg.get("course")[:200])
    return 0


PRIVACY = """\
What AskTim! sends to the AI service you chose (with your own key), and what it never sends.

Sent with a question:
  • your question;
  • what is installed on this computer: programs (and their versions), Python and R packages;
  • your recent shell commands - with secrets (API keys, tokens, passwords), e-mail addresses,
    NHS numbers, phone numbers and long sequences taken out, and your user name replaced by "user";
  • the names and sizes of the files in the current folder;
  • if you ask about a file (--file), a description of its structure: format, size, number of
    records, and for a table the column names and the kind of values in each - not the values;
  • if you pipe text in (cmd 2>&1 | AskTim!), that text with lines that look like data withheld.
  In a notebook: the code of the cells you have run (with tables pasted into the code and long
  lists of numbers withheld), the names, types and shapes of your variables (not their values),
  and the last error message.

Sent by the agent: its commands and files (which you see first), and what each command printed
with every line that looks like data withheld - sequences, reads, alignments, variants,
intervals, table rows, lists of IDs or numbers, encoded data. Error messages, warnings, help
text, versions, counts, summaries and file listings get through, and so does text: if the
agent reads a README or your notes, the AI sees them.

Never sent: the contents of your data files, your keys, your shell history beyond the last
commands, anything from ~/.ssh and other credentials.

Settings: privacy is "{level}". "strict" (AskTim! --strict ..., or "privacy": "strict" in the
settings) also withholds column names, file listings and summaries, and quoted values in errors.

Limits: the filter recognises data by their shape. It is made for bioinformatics data and
works well on them, but it cannot promise to catch every piece of free text - a name quoted in
an error message, for example (strict mode replaces quoted values). Do not use AskTim!, or any
AI tool, on personal or patient data unless your institution has approved it. `AskTim! show
"question"` prints everything that would be sent, without sending it.

What the AI service does with what it receives is set by its terms (for Claude: Anthropic's
API terms; for Gemini's free tier, Google may use what is sent to improve its products outside
the UK, Switzerland and the EEA). A local log of your questions is kept in
{log} (AskTim! log); it never leaves your computer.
"""
