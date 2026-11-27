"""Reading shell history (and the shell hook), and describing files without their contents."""

import gzip
import os
import shutil
import subprocess
import sys
import time

import pytest

from asktim import fileinfo, history, shell_setup


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
    shell_setup.install(shell)
    env = dict(os.environ, HISTFILE=str(home / ".hist"))
    # zsh -d: without the system's startup files (Ubuntu's run compinit, which can stop to ask a question)
    c = pexpect.spawn(shell, ["-d", "-i"] if shell == "zsh" else ["-i"], env=env, encoding="utf-8", timeout=15)
    c.expect(r"[#$%>] ")
    # the prompt is set with split quotes, so that the echo of this line does not look like the prompt
    c.sendline("PS1='READ''Y> '; PROMPT='READ''Y> '" if shell != "fish" else "function fish_prompt; echo -n 'READ''Y> '; end")
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
