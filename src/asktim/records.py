"""The record of an agent's run, written by AskTim! from what really happened - not by the model.

In the run's folder (asktim-runs/N-mode/):

    RUN.md     to read: the task, the model, every step with its reason, the command, its exit
               status, the end of what it printed, what the AI was shown of it, what was
               refused or changed, the files made (with checksums), the agent's report, and a
               draft statement on the use of AI
    run.json   the same, for programs
    rerun.sh   the commands that worked, in order, with the files the agent wrote: a script
               to repeat the analysis (read it before you run it)
    agent-record.ipynb   (notebook runs) the run as a notebook: explanations and code cells
"""

import hashlib
import json
import os
import shlex
import time

from . import __version__, paths

MODE_NAMES = {"approve": "approve each step", "plan": "plan first", "auto": "autonomous", "script": "write a script"}


def _md5(path, limit=2 << 30):
    try:
        if os.path.getsize(path) > limit:
            return "(too large to checksum here)"
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _files(folder, skip=("RUN.md", "run.json", "rerun.sh", "agent-record.ipynb")):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in sorted(files):
            if fn in skip and root == folder or fn.startswith(".asktim"):
                continue
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            out.append((os.path.relpath(p, folder), size, _md5(p)))
    return out


def _fence(text, lang="text"):
    text = text or ""
    fence = "```"
    while fence in text:
        fence += "`"
    return "%s%s\n%s\n%s" % (fence, lang, text.rstrip("\n"), fence)


def write(run, folder):
    """run: dict built by the agent. Returns the paths written."""
    paths.ensure(folder)
    written = []
    files = _files(run["work_dir"]) if os.path.isdir(run["work_dir"]) else []
    run["files"] = [{"path": p, "bytes": s, "md5": m} for p, s, m in files]
    # run.json
    jp = os.path.join(folder, "run.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(run, f, indent=2, ensure_ascii=False, default=str)
    written.append(jp)
    # RUN.md
    mp = os.path.join(folder, "RUN.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write(markdown(run))
    written.append(mp)
    # rerun.sh
    if any(s["tool"] in ("run_command", "write_file") for s in run["steps"]):
        sp = os.path.join(folder, "rerun.sh")
        with open(sp, "w", encoding="utf-8") as f:
            f.write(rerun_script(run))
        os.chmod(sp, 0o755)
        written.append(sp)
    if run.get("notebook"):
        np_ = os.path.join(folder, "agent-record.ipynb")
        with open(np_, "w", encoding="utf-8") as f:
            json.dump(notebook_record(run), f, indent=1, ensure_ascii=False)
        written.append(np_)
    return written


def markdown(run):
    L = []
    L.append("# AskTim! Agent – run %s (%s)" % (run["number"], MODE_NAMES.get(run["mode"], run["mode"])))
    L.append("")
    L.append("This record was written by AskTim! from what happened, not by the AI model.")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| Task | %s |" % run["task"].replace("|", "\\|").replace("\n", " "))
    L.append("| Started | %s |" % run["started"])
    L.append("| Ended | %s (%s) |" % (run.get("ended", ""), run.get("outcome", "")))
    L.append("| Model | %s |" % ", ".join(run.get("models") or ["?"]))
    L.append("| Folder | %s |" % run["work_dir_shown"])
    L.append("| Safeguards | %s; internet %s |" % (run.get("sandbox", ""), "allowed (downloads only)" if run.get("allow_network") else "off"))
    L.append("| Privacy | %s; %d line%s of output withheld from the AI as data |" % (run.get("privacy", "standard"), run.get("withheld", 0), "" if run.get("withheld", 0) == 1 else "s"))
    L.append("| AskTim! | %s |" % __version__)
    L.append("")
    if run.get("plan"):
        L.append("## The plan")
        L.append("")
        for i, p in enumerate(run["plan_rounds"] if run.get("plan_rounds") else [run["plan"]]):
            if i:
                L.append("*Changes you asked for:* %s" % (run.get("plan_feedback") or [""])[i - 1] if i - 1 < len(run.get("plan_feedback") or []) else "")
                L.append("")
            L.append(p.strip())
            L.append("")
    L.append("## Steps")
    L.append("")
    if not run["steps"]:
        L.append("(none)")
    for s in run["steps"]:
        L.append("### %d. %s" % (s["n"], s.get("why") or s["tool"]))
        L.append("")
        if s["tool"] == "run_command":
            L.append(_fence(s.get("command", ""), "bash"))
        elif s["tool"] == "run_python":
            L.append(_fence(s.get("code", ""), "python"))
        elif s["tool"] in ("write_file",):
            L.append("Wrote `%s` (%s):" % (s.get("path"), "%d lines" % (s.get("content", "").count("\n") + 1)))
            L.append("")
            L.append(_fence(s.get("content", ""), _lang_of(s.get("path", ""))))
        elif s["tool"] in ("add_code_cell",):
            L.append("Added a code cell to the notebook:")
            L.append("")
            L.append(_fence(s.get("code", ""), "python"))
        elif s["tool"] in ("add_markdown_cell",):
            L.append("Added a Markdown cell to the notebook:")
            L.append("")
            L.append(_fence(s.get("text", ""), "markdown"))
        else:
            L.append("`%s` %s" % (s["tool"], json.dumps(s.get("input", {}), ensure_ascii=False)[:300]))
        L.append("")
        bits = []
        if s.get("decision"):
            bits.append({"run": "approved by you", "auto": "run without asking (autonomous)", "all": "approved (and the rest of the run without asking)",
                         "edit": "changed by you before it ran", "refuse": "refused by you", "blocked": "refused by AskTim!'s safeguards"}.get(s["decision"], s["decision"]))
        if s.get("comment"):
            bits.append("you said: “%s”" % s["comment"])
        if s.get("original") and s.get("decision") == "edit":
            bits.append("the agent had proposed: `%s`" % s["original"].replace("`", "'")[:300])
        if s.get("blocked"):
            bits.append("why: " + "; ".join(s["blocked"]))
        if s.get("exit") is not None:
            bits.append("exit status %s" % s["exit"])
        if s.get("seconds") is not None:
            bits.append("%.1f s" % s["seconds"])
        if s.get("timed_out"):
            bits.append("stopped: it ran longer than the time limit")
        if s.get("interrupted"):
            bits.append("stopped by you (Ctrl+C)")
        if s.get("lines") is not None:
            bits.append("%d line%s of output" % (s["lines"], "" if s["lines"] == 1 else "s"))
        if s.get("withheld"):
            bits.append("%d withheld from the AI as data" % s["withheld"])
        if bits:
            L.append("*" + "; ".join(bits) + ".*")
            L.append("")
        if s.get("outside_changes"):
            L.append("**Files outside the agent's folder changed during this step:** " + ", ".join("`%s`" % x for x in s["outside_changes"][:20]))
            L.append("")
        if s.get("output_tail"):
            L.append("The end of what it printed (as you saw it):")
            L.append("")
            L.append(_fence(s["output_tail"]))
            L.append("")
        if s.get("sent") and s.get("sent") != s.get("output_tail"):
            L.append("<details><summary>What the AI was shown of this step</summary>")
            L.append("")
            L.append(_fence(s["sent"]))
            L.append("")
            L.append("</details>")
            L.append("")
    L.append("## The agent's report")
    L.append("")
    rep = (run.get("report") or "(no report)").strip()
    L.append("\n".join("> " + ln if ln.strip() else ">" for ln in rep.splitlines()))
    L.append("")
    L.append("The report is the model's own text: check it against the steps above.")
    L.append("")
    if run.get("files"):
        L.append("## Files in the folder")
        L.append("")
        L.append("| File | Size | MD5 |")
        L.append("|---|---|---|")
        for f in run["files"][:200]:
            L.append("| %s | %s | `%s` |" % (f["path"], f["bytes"], f["md5"]))
        L.append("")
    if any(s["tool"] in ("run_command", "write_file") for s in run["steps"]):
        L.append("## Repeat it")
        L.append("")
        L.append("`rerun.sh` holds the commands that worked, in order, with the files the agent wrote. Read it before you run it: `bash rerun.sh`.")
        L.append("")
    L.append("## Statement on the use of AI (a draft)")
    L.append("")
    n_run = sum(1 for s in run["steps"] if s.get("exit") is not None)
    n_ref = sum(1 for s in run["steps"] if s.get("decision") in ("refuse", "blocked"))
    n_edit = sum(1 for s in run["steps"] if s.get("decision") == "edit")
    L.append(
        "On %s I used AskTim! Agent (%s), an AI agent, in “%s” mode for this task: “%s”. It proposed %d step%s; %d command%s ran, "
        "%d step%s %s refused (by me or by AskTim!'s safeguards) and %d changed by me. The AI did not see the contents of my data files: the output of each command "
        "reached it with lines that looked like data withheld. I checked the results myself (say how), and the conclusions are mine."
        % (run["started"][:10], ", ".join(run.get("models") or ["?"]), MODE_NAMES.get(run["mode"], run["mode"]), run["task"].replace("\n", " ")[:200],
           len(run["steps"]), "" if len(run["steps"]) == 1 else "s", n_run, "" if n_run == 1 else "s", n_ref, "" if n_ref == 1 else "s", "was" if n_ref == 1 else "were", n_edit)
    )
    L.append("")
    return "\n".join(L)


def _lang_of(path):
    ext = os.path.splitext(path)[1].lower()
    return {".sh": "bash", ".py": "python", ".r": "r", ".rmd": "markdown", ".md": "markdown", ".smk": "python", ".nf": "groovy", ".yaml": "yaml", ".yml": "yaml", ".json": "json"}.get(ext, "text")


def rerun_script(run):
    L = [
        "#!/usr/bin/env bash",
        "# Made by AskTim! from agent run %s (%s), %s." % (run["number"], MODE_NAMES.get(run["mode"], run["mode"]), run["started"][:16].replace("T", " ")),
        "# Task: %s" % run["task"].replace("\n", " ")[:300],
        "#",
        "# The commands that worked, in order, and the files the agent wrote. Commands that failed or",
        "# were refused are here as comments. It runs in its own folder (as the agent did), so read the",
        "# paths before you run it somewhere else. Read the whole script before you run it:  bash rerun.sh",
        "set -eu",
        'cd "$(dirname "$0")"',
        "",
    ]
    if run.get("here"):
        L[-2] = 'cd "$(dirname "$0")/../.."   # the agent worked in your folder'
    for s in run["steps"]:
        why = (s.get("why") or "").replace("\n", " ")
        if s["tool"] == "run_command":
            cmd = s.get("command", "")
            if s.get("exit") == 0 and s.get("decision") not in ("refuse", "blocked"):
                L.append("# %d. %s" % (s["n"], why))
                L.append(cmd)
            else:
                state = "refused" if s.get("decision") in ("refuse", "blocked") else "failed (exit %s)" % s.get("exit")
                L.append("# %d. %s – %s, so left out:" % (s["n"], why, state))
                L.extend("#   " + ln for ln in cmd.splitlines())
            L.append("")
        elif s["tool"] == "write_file" and s.get("written"):
            body = s.get("content", "")
            tag = "ASKTIM_EOF"
            while tag in body:
                tag += "_"
            L.append("# %d. %s (the file the agent wrote)" % (s["n"], why))
            L.append("cat > %s <<'%s'" % (shlex.quote(s.get("path", "file")), tag))
            L.append(body.rstrip("\n"))
            L.append(tag)
            L.append("")
        elif s["tool"] == "run_python" and s.get("ok"):
            body = s.get("code", "")
            tag = "ASKTIM_EOF"
            while tag in body:
                tag += "_"
            L.append("# %d. %s (Python that ran in the notebook; it needs the notebook's variables)" % (s["n"], why))
            L.append("python3 - <<'%s'" % tag)
            L.append(body.rstrip("\n"))
            L.append(tag)
            L.append("")
    L.append('echo "Done. Compare the results with RUN.md."')
    return "\n".join(L) + "\n"


def notebook_record(run):
    cells = []

    def md(text):
        cells.append({"cell_type": "markdown", "metadata": {}, "source": text})

    def code(text):
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": text})

    md("# AskTim! Agent – run %s\n\n**Task:** %s\n\nModel: %s · mode: %s · %s\n\nWritten by AskTim! from what happened (not by the model). Run the cells to repeat the work." % (
        run["number"], run["task"], ", ".join(run.get("models") or ["?"]), MODE_NAMES.get(run["mode"], run["mode"]), run["started"][:16].replace("T", " ")))
    for s in run["steps"]:
        why = s.get("why") or ""
        if s["tool"] == "run_python":
            md("**%d.** %s%s" % (s["n"], why, "" if s.get("ok") else " *(this step failed or was refused)*"))
            code(s.get("code", "") if s.get("ok") else "# " + s.get("code", "").replace("\n", "\n# "))
        elif s["tool"] == "run_command":
            md("**%d.** %s%s" % (s["n"], why, "" if s.get("exit") == 0 else " *(this step failed or was refused)*"))
            code(("%%%%bash\ncd %s\n%s" % (shlex.quote(run["work_dir"]), s.get("command", ""))) if s.get("exit") == 0 else "# " + s.get("command", "").replace("\n", "\n# "))
        elif s["tool"] == "add_markdown_cell":
            md(s.get("text", ""))
        elif s["tool"] == "add_code_cell":
            code(s.get("code", ""))
    if run.get("report"):
        md("## The agent's report\n\n" + run["report"])
    return {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


def next_run_number(runs_dir):
    n = 0
    try:
        for name in os.listdir(runs_dir):
            head = name.split("-", 1)[0]
            if head.isdigit():
                n = max(n, int(head))
    except OSError:
        pass
    return n + 1


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
