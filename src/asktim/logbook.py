"""A record, on the user's own computer, of how they used AI.

Each question and each agent run adds a line to ~/.local/state/asktim/log.jsonl: when, which
mode, which model, the question or task, what was sent with it (as a summary), and for agent
runs the folder with the full record. Nothing here is sent anywhere.

`AskTim! log` shows the recent entries. `AskTim! statement` drafts a statement on the use of
AI for coursework from them - the practicals ask students to write one.

The chat conversation of the command line is also kept here (cache/chat.json), so that a
follow-up question can refer to the answer before; it is forgotten after an hour without
questions, or with `AskTim! new`.
"""

import json
import os
import time

from . import paths


def log_file():
    return os.path.join(paths.state_dir(), "log.jsonl")


_log_file = log_file


def add(entry, cfg=None):
    if cfg is not None and not cfg.get("log", True):
        return
    entry = dict(entry)
    entry.setdefault("time", time.strftime("%Y-%m-%dT%H:%M:%S"))
    if cfg is not None:
        entry.setdefault("privacy", cfg.get("privacy"))  # "off": data could be sent with it
    try:
        paths.ensure(paths.state_dir(), private=True)
        with open(_log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def entries(since=None, limit=None):
    out = []
    try:
        with open(_log_file(), encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if since and e.get("time", "") < since:
                    continue
                out.append(e)
    except OSError:
        return []
    return out[-limit:] if limit else out


def statement(since=None, folder=None):
    """A draft statement on the use of AI, in Markdown."""
    es = entries(since=since)
    if folder:
        f = os.path.realpath(folder)
        es = [e for e in es if (e.get("cwd") or "").startswith(paths.pretty(f)) or (e.get("cwd") or "") == paths.pretty(f)]
    if not es:
        return None
    models = sorted(set(e.get("model", "") for e in es if e.get("model")))
    chats = [e for e in es if e.get("mode") == "chat"]
    runs = [e for e in es if e.get("mode", "").startswith("agent")]
    first, last = es[0]["time"][:10], es[-1]["time"][:10]
    off = [e for e in es if e.get("privacy") == "off"]
    shared = ("my data could be sent to the AI as well: the start of files I asked about, text I pasted or piped in, the "
              "output of the commands the agent ran and, in notebooks, my code and a preview of my variables (secrets and "
              "personal details were removed)")
    if off and len(off) == len(es):
        sent = "AskTim!'s data filter was off (AskTim! privacy off) throughout, so " + shared + "."
    else:
        sent = ("AskTim! did not send the contents of my data files to the AI: it sent my questions, the names of the programs installed "
                "on the computer, my recent commands (with secrets removed), descriptions of files (format, size, columns) and, in "
                "notebooks, the code of the cells I ran. In agent mode, the output of the commands it ran was sent with lines that "
                "looked like data withheld.")
        if off:
            sent += " For %d of the %d questions and tasks, though, its data filter was off (AskTim! privacy off), so %s." % (len(off), len(es), shared)
    lines = [
        "## Statement on the use of AI",
        "",
        "Between %s and %s I used AskTim!, an AI assistant for bioinformatics (model%s: %s), in this work." % (first, last, "s" if len(models) != 1 else "", ", ".join(models) or "unknown"),
        "",
        sent,
        "",
    ]
    if chats:
        lines += ["I asked %d question%s, among them:" % (len(chats), "" if len(chats) == 1 else "s"), ""]
        for e in chats[-12:]:
            lines.append("- %s – “%s”" % (e["time"][:10], (e.get("question") or "")[:160].replace("\n", " ")))
        lines.append("")
    if runs:
        lines += ["AskTim! Agent carried out %d task%s; the record of each run (RUN.md) and a script to repeat it (rerun.sh) are in its folder:" % (len(runs), "" if len(runs) == 1 else "s"), ""]
        for e in runs[-12:]:
            lines.append("- %s – %s (%s): “%s” – %s" % (e["time"][:10], e.get("mode", "agent"), e.get("outcome", ""), (e.get("question") or "")[:140].replace("\n", " "), e.get("folder", "")))
        lines.append("")
    lines += [
        "[Say here which suggestions you used, what you changed, and how you checked the results. The analysis and its conclusions are mine.]",
    ]
    return "\n".join(lines)


# -- the chat conversation of the command line ---------------------------------------------------


def _chat_file():
    return os.path.join(paths.cache_dir(), "chat.json")


def load_chat(max_minutes=60):
    try:
        with open(_chat_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if time.time() - data.get("time", 0) > max_minutes * 60:
        return []
    if data.get("cwd") and data["cwd"] != paths.pretty(os.getcwd()) and time.time() - data.get("time", 0) > 15 * 60:
        return []
    return data.get("items", [])


def save_chat(items):
    try:
        paths.ensure(paths.cache_dir(), private=True)
        tmp = _chat_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"time": time.time(), "cwd": paths.pretty(os.getcwd()), "items": items[-20:]}, f, ensure_ascii=False)
        os.replace(tmp, _chat_file())
    except OSError:
        pass


def clear_chat():
    try:
        os.remove(_chat_file())
        return True
    except OSError:
        return False


def save_suggested(blocks):
    """The shell commands of the last answer, one per line, for Alt+A in the shell."""
    cmds = []
    for lang, code in blocks:
        if lang in ("bash", "sh", "shell", "console", "zsh", ""):
            for ln in code.splitlines():
                ln = ln.rstrip()
                if ln.startswith("$ "):
                    ln = ln[2:]
                if ln and not ln.lstrip().startswith("#"):
                    cmds.append(ln)
    try:
        paths.ensure(paths.cache_dir(), private=True)
        p = os.path.join(paths.cache_dir(), "suggested.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(cmds) + ("\n" if cmds else ""))
        try:
            os.remove(p + ".next")
        except OSError:
            pass
    except OSError:
        pass
    return cmds
