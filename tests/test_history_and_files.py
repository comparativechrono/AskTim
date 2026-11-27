"""Reading shell history (and the shell hook), and describing files without their contents."""

import gzip
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from asktim import fileinfo, history, paths, shell_setup
from conftest import run_asktim


def test_parse_bash():
    cmds = history.parse_bash("#1700000000\nls -l\ncd data\n\nsamtools view x.bam\n")
    assert [c.line for c in cmds] == ["ls -l", "cd data", "samtools view x.bam"]


def test_parse_zsh_extended_and_multiline():
    text = ": 1700000000:0;ls -l\n: 1700000001:0;for f in *.fq; do\\\n  wc -l $f\\\ndone\nplain line\n"
    cmds = history.parse_zsh(text)
    assert cmds[0].line == "ls -l" and cmds[0].when == 1700000000
    assert cmds[1].line.startswith("for f in *.fq; do\n") and cmds[1].line.endswith("done")
    assert cmds[2].line == "plain line"


def test_zsh_unmetafy():
    # zsh stores some bytes as 0x83 followed by the byte XOR 32
    assert history._unmetafy(b"echo \x83\xa3") == b"echo \x83"


def test_parse_fish():
    cmds = history.parse_fish("- cmd: ls -l\n  when: 1700000000\n- cmd: echo a\\nb\n  when: 1700000001\n")
    assert [c.line for c in cmds] == ["ls -l", "echo a\nb"]


def test_hook_log_and_filtering(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "a"))
    log = tmp_path / "a" / "state" / "commands.tsv"
    log.parent.mkdir(parents=True)
    log.write_text("1700000000\t0\t/home/u\tls\n1700000001\t127\t/home/u\tfastqc x.fq\n1700000002\t0\t/home/u\tAskTim! \"why?\"\n1700000003\t0\t/home/u\t echo private\n1700000004\t0\t/home/u\tprintf 'a\\nb'\n")
    os.utime(log, None)
    cmds, src = history.recent(10)
    assert src == "the AskTim! shell hook"
    assert [c.line for c in cmds] == ["ls", "fastqc x.fq", "printf 'a\\nb'"]  # AskTim!'s own and private commands left out
    assert history.last_failed(cmds).line == "fastqc x.fq"


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_shell_hook_records_commands_and_status(tmp_path, monkeypatch, shell):
    pexpect = pytest.importorskip("pexpect")
    if not shutil.which(shell):
        pytest.skip("%s is not installed" % shell)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "asktim"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("ASKTIM_HOOK", raising=False)
    shell_setup.install(shell)
    env = dict(os.environ, HISTFILE=str(home / ".hist"))
    # zsh -d: without the system's startup files (Ubuntu's run compinit, which can stop to ask a question)
    c = pexpect.spawn(shell, ["-d", "-i"] if shell == "zsh" else ["-i"], env=env, encoding="utf-8", timeout=15)
    c.expect(r"[#$%>] ")
    # the prompt is set with split quotes, so that the echo of this line does not look like the prompt
    c.sendline("PS1='READ''Y> '; PROMPT='READ''Y> '" if shell != "fish" else "function fish_prompt; echo -n 'READ''Y> '; end")
    c.expect_exact("READY> ")
    # the hook tells AskTim! (started from this shell) that it is loaded
    c.sendline("echo hook=$ASKTIM_HOOK.")
    c.expect_exact("hook=%s." % shell)
    c.expect_exact("READY> ")
    for cmd in ["echo hello", "ls /nonexistent-folder", "false"]:
        c.sendline(cmd)
        c.expect_exact("READY> ")
    c.sendline("exit")
    c.expect(pexpect.EOF)
    c.close()
    time.sleep(0.2)
    text = (tmp_path / "asktim" / "state" / "commands.tsv").read_text()
    lines = [ln.split("\t") for ln in text.splitlines()]
    recorded = {l[3]: int(l[1]) for l in lines}
    assert recorded["echo hello"] == 0
    assert recorded["ls /nonexistent-folder"] != 0
    assert recorded["false"] == 1
    f, removed = shell_setup.remove(shell)
    assert removed


def test_describe_formats(project):
    d = project / "data"
    fq = fileinfo.describe(str(d / "reads_R1.fastq"))
    assert "FASTQ" in fq and "200 reads" in fq and "read length 76-76" in fq
    fa = fileinfo.describe(str(d / "ref.fa"))
    assert "FASTA (DNA/RNA)" in fa and "chr10" in fa
    csv = fileinfo.describe(str(d / "genes.csv"))
    assert "4 columns" in csv and "orf (text)" in csv and "length (whole numbers)" in csv and "essential (true/false)" in csv
    assert "YAL001C" not in csv  # values never
    people = fileinfo.describe(str(d / "patients.csv"))
    assert "no header line" in people and "Smith" not in people and "London" not in people
    strict = fileinfo.describe(str(d / "genes.csv"), strict=True)
    assert "orf" not in strict
    with gzip.open(str(d / "r2.fastq.gz"), "wt") as f:
        f.write((d / "reads_R1.fastq").read_text())
    gz = fileinfo.describe(str(d / "r2.fastq.gz"))
    assert "FASTQ (gzip)" in gz and "200 reads" in gz
    assert "a folder with" in fileinfo.describe(str(d))
    assert fileinfo.describe(str(d / "nope.txt")).endswith("there is no such file or folder.")


def test_describe_names_only_reference_like_sequences(tmp_path):
    p = tmp_path / "s.fa"
    p.write_text(">patient_john_smith_tumour\nACGTACGTACGTACGTACGTACGT\n")
    assert "john" not in fileinfo.describe(str(p)).lower()


def test_listing(project):
    out = fileinfo.listing(str(project))
    assert "data/" in out and "genes.csv" in out
    assert "names withheld" in fileinfo.listing(str(project), strict=True)


def test_the_inventory_notices_programs_installed_later(tmp_path, monkeypatch):
    """`apt-get install bcftools` changes neither PATH nor conda, but AskTim! must notice it at once."""
    from asktim import inventory

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "home"))
    first = inventory.gather()
    assert "bcftools" not in first["tools"]
    assert inventory.gather()["time"] == first["time"]  # nothing changed: the saved inventory is used
    prog = bin_dir / "bcftools"
    prog.write_text("#!/bin/sh\necho 'bcftools 1.19'\n")
    prog.chmod(0o755)
    assert inventory.gather()["tools"]["bcftools"]["version"] == "1.19"
    prog.unlink()
    assert "bcftools" not in inventory.gather()["tools"]


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_shell_hook_is_quiet_without_its_folder_and_skips_old_history(tmp_path, monkeypatch, shell):
    # the hook printed "No such file or directory" after every command when its folder was missing,
    # and bash recorded the last command of an earlier session as if it had just run
    pexpect = pytest.importorskip("pexpect")
    if not shutil.which(shell):
        pytest.skip("%s is not installed" % shell)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "asktim"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("ZDOTDIR", raising=False)
    shell_setup.install(shell)
    (home / ".hist").write_text("an_old_command_from_yesterday\n")
    state = tmp_path / "asktim" / "state"
    env = dict(os.environ, HISTFILE=str(home / ".hist"))
    c = pexpect.spawn(shell, ["-d", "-i"] if shell == "zsh" else ["-i"], env=env, encoding="utf-8", timeout=15)
    c.expect(r"[#$%>] ")
    c.sendline("PS1='READ''Y> '; PROMPT='READ''Y> '" if shell != "fish" else "function fish_prompt; echo -n 'READ''Y> '; end")
    c.expect_exact("READY> ")
    c.sendline("echo first")
    c.expect_exact("READY> ")
    c.sendline("rm -rf '%s'" % state)
    c.expect_exact("READY> ")
    c.sendline("echo after")
    c.expect_exact("READY> ")
    quiet = c.before
    c.sendline("exit")
    c.expect(pexpect.EOF)
    c.close()
    assert "No such file" not in quiet and "no such file" not in quiet and "redirect" not in quiet, quiet
    # a new shell makes the folder again, and does not record an old command
    shutil.rmtree(str(state), ignore_errors=True)
    c = pexpect.spawn(shell, ["-d", "-i"] if shell == "zsh" else ["-i"], env=env, encoding="utf-8", timeout=15)
    c.expect(r"[#$%>] ")
    c.sendline("PS1='READ''Y> '; PROMPT='READ''Y> '" if shell != "fish" else "function fish_prompt; echo -n 'READ''Y> '; end")
    c.expect_exact("READY> ")
    c.sendline("echo second")
    c.expect_exact("READY> ")
    c.sendline("exit")
    c.expect(pexpect.EOF)
    c.close()
    time.sleep(0.2)
    text = (state / "commands.tsv").read_text()
    assert "echo second" in text and "an_old_command_from_yesterday" not in text
    shell_setup.remove(shell)


def test_an_empty_hook_log_is_not_a_history(tmp_path, monkeypatch):
    # setup-shell starts the hook's log; while it was empty, AskTim! sent nothing about the user's commands,
    # and did not say that it had found none
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "a"))
    monkeypatch.delenv("HISTFILE", raising=False)
    log = tmp_path / "a" / "state" / "commands.tsv"
    log.parent.mkdir(parents=True)
    log.write_text("")
    assert history.recent(10, shell="bash") == ([], None)
    (tmp_path / "home" / ".bash_history").write_text("ls an_old_session\n")
    cmds, src = history.recent(10, shell="bash")
    assert [c.line for c in cmds] == ["ls an_old_session"] and src == "~/.bash_history"


def test_whether_a_shell_has_loaded_the_hook(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "a"))
    monkeypatch.delenv("ASKTIM_HOOK", raising=False)
    monkeypatch.delenv("ZDOTDIR", raising=False)
    an_hour_ago = time.time() - 3600
    assert not history.hook_loaded("bash", started=an_hour_ago)  # not set up
    shell_setup.install("bash")
    assert not history.hook_loaded("bash", started=an_hour_ago)  # set up, but not loaded by this shell
    monkeypatch.setenv("ASKTIM_HOOK", "bash")
    assert history.hook_loaded("bash", started=an_hour_ago)
    assert not history.hook_loaded("zsh", started=an_hour_ago)  # a zsh started from a bash that has it
    monkeypatch.delenv("ASKTIM_HOOK")
    # the lines that AskTim! 0.2.1 and older added do not say that they are loaded: their log's time tells
    rc = home / ".bashrc"
    rc.write_text("\n".join(ln for ln in rc.read_text().splitlines() if "ASKTIM_HOOK" not in ln) + "\n")
    with open(paths.commands_log(), "a") as f:
        f.write("1700000000\t0\t/home/u\tls\n")
    assert history.hook_loaded("bash", started=an_hour_ago)
    assert not history.hook_loaded("bash", started=time.time() + 60)


def test_asktim_not_typed_into_a_terminal_reports_no_unseen_commands(tmp_path, env, project):
    # from a notebook cell, a script or `bash -c` there is no terminal whose commands AskTim! should see
    # (here pytest starts AskTim!, as a notebook's kernel does)
    code = "from asktim import history; print(history.typed_into(), history.unseen_terminal())"
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.stdout.split() == ["None", "None"], r.stdout + r.stderr
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")
    # (`; true` keeps bash from replacing itself with AskTim!, so bash -c is its parent)
    r = subprocess.run(["bash", "-c", '"$0" -m asktim show "what is a BAM file?"; true', sys.executable], env=env,
                       cwd=str(project), capture_output=True, text=True, timeout=120)
    assert "what is a BAM file?" in r.stdout, r.stdout + r.stderr
    assert "cannot see the commands" not in r.stdout + r.stderr


def test_setup_shell_from_a_notebook_cell_sets_up_bash(tmp_path, env, project):
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")
    e = dict(env, SHELL="/bin/sh")  # (Colab's notebook: not started from bash, and $SHELL is not bash)
    for k in ("TMUX", "COLAB_RELEASE_TAG"):
        e.pop(k, None)
    r = run_asktim(["setup-shell"], e, project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert shell_setup.BEGIN in (tmp_path / "home" / ".bashrc").read_text()
    assert "in a terminal that is already open, run: source ~/.bashrc" in " ".join(r.stdout.split()), r.stdout
    # in Colab, the terminal keeps its shell when it is closed and reopened
    r = run_asktim(["setup-shell"], dict(e, COLAB_RELEASE_TAG="release-colab-external_20261120-060127_RC00"), project)
    assert "Now run this once in each terminal that is open: source ~/.bashrc" in " ".join(r.stdout.split()), r.stdout
    assert "keeps its shell" in r.stdout


def test_a_terminal_whose_commands_cannot_be_seen(tmp_path, env, mock, project):
    # Colab's terminal is bash in tmux. bash writes its history file only when it exits, and closing and
    # reopening the terminal finds the same shell, so AskTim! saw none of the commands typed there - and
    # said nothing, even after `AskTim! setup-shell` (the open shell had not read the new lines)
    pexpect = pytest.importorskip("pexpect")
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")
    (tmp_path / "home" / ".bash_history").write_text("ls from_an_earlier_session\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "AskTim!").write_text('#!/bin/sh\nexec "%s" -m asktim "$@"\n' % sys.executable)
    (bindir / "AskTim!").chmod(0o755)
    e = dict(env, PATH=str(bindir) + os.pathsep + env["PATH"], TMUX="/tmp/tmux-1000/default,4242,0")
    c = pexpect.spawn("bash", ["--norc", "-i"], env=e, cwd=str(project), encoding="utf-8", timeout=60, dimensions=(50, 250))
    c.expect(r"[#$%>] ")
    c.sendline("PS1='READ''Y> '")
    c.expect_exact("READY> ")

    def run(line):
        c.sendline(line)
        c.expect_exact("READY> ")
        return " ".join(c.before.split())

    def sent():
        return json.dumps(mock.requests[-1]["body"])

    run("ls no_such_file_here")
    out = run("AskTim! why")
    assert "cannot see the commands you typed in this terminal: bash saves them only when the terminal closes" in out, out
    assert "AskTim! setup-shell and then, in this terminal: source ~/.bashrc" in out and "runs in tmux" in out, out
    assert "OK." in out  # the answer still comes
    assert "no_such_file_here" not in sent()  # (it cannot be seen: that is the point)
    assert "ls from_an_earlier_session" in sent() and "from earlier terminal sessions" in sent()
    assert "cannot see the commands typed in the terminal it was started from" in sent()
    # an ordinary question gives the hint once a day; `why` every time
    assert "cannot see the commands" in run('AskTim! "what does ls -l show?"')
    assert "cannot see the commands" not in run('AskTim! "and ls -a?"')
    out = run("AskTim! setup-shell")
    assert "Now run this once in each terminal that is open: source ~/.bashrc" in out, out
    out = run("AskTim! why")
    assert "installed but not loaded in this terminal" in out and "run in this terminal: source ~/.bashrc" in out, out
    assert "installed but not loaded in it" in sent()
    assert "not loaded in this terminal (to load it: source ~/.bashrc)" in run("AskTim! status")
    run("source ~/.bashrc")
    run("ls still_no_such_file")
    out = run("AskTim! why")
    assert "cannot see" not in out and "not loaded" not in out, out
    assert "ls still_no_such_file   [exit" in json.loads(sent())["messages"][-1]["content"][0]["text"]
    assert "The most recent failure: `ls still_no_such_file`" in sent() and "cannot see the commands" not in sent()
    c.sendline("exit")
    c.expect(pexpect.EOF)
