"""The AskTim! command, end to end, with the stand-in AI: nothing of the data may reach it."""

import glob
import json
import os
import re
import shutil
import sys
import time

import pytest

from conftest import run_asktim


def _reads(project):
    return [ln for ln in (project / "data" / "reads_R1.fastq").read_text().split("\n")[1::4] if ln]


def test_chat_sends_context_but_no_data_or_secrets(project, env, mock):
    (project.parent / "home" / ".bash_history").write_text(
        "cd practical\nexport ANTHROPIC_API_KEY=sk-ant-REALSECRET0123456789abcdef\nbwa mem data/ref.fa data/reads_R1.fastq > aln.sam\n"
        "grep %s data/reads_R1.fastq\nAskTim! Claude sk-ant-ANOTHER0123456789abcdef\n" % _reads(project)[0])
    mock.queue = [{"text": "Divide the lines by 4:\n\n```bash\necho $(( $(wc -l < data/reads_R1.fastq) / 4 ))\n```"}]
    p = run_asktim(["-v", "how many reads are in my fastq file?"], env, project)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Divide the lines by 4" in p.stdout and "Sent with your question" in p.stdout
    sent = mock.all_text_sent()
    for bad in ["REALSECRET", "ANOTHER0123", _reads(project)[0], "sk-ant-test"]:
        assert bad not in sent
    msg = mock.requests[0]["body"]["messages"][-1]["content"][0]["text"]
    assert "reads_R1.fastq (" in msg and "bwa mem data/ref.fa" in msg and "Question: how many reads" in msg
    assert mock.requests[0]["body"]["system"][0]["text"].startswith("You are AskTim!")
    # the commands of the answer are kept for Alt+A
    sugg = open(os.path.join(env["ASKTIM_HOME"], "cache", "suggested.txt")).read()
    assert "wc -l < data/reads_R1.fastq" in sugg
    # a follow-up keeps the conversation
    mock.queue = [{"text": "Yes."}]
    run_asktim(["and for the second file?"], env, project)
    assert [m["role"] for m in mock.requests[-1]["body"]["messages"]] == ["user", "assistant", "user"]
    assert run_asktim(["new"], env, project).returncode == 0
    mock.queue = [{"text": "Fresh."}]
    run_asktim(["hello"], env, project)
    assert len(mock.requests[-1]["body"]["messages"]) == 1


def test_piped_error_output_is_filtered(project, env, mock):
    mock.queue = [{"text": "The file is missing."}]
    piped = "[E::hts_open_format] Failed to open file \"x.bam\"\n@r1\n%s\n+\n%s\n" % (_reads(project)[0], "F" * 76)
    p = run_asktim(["what does this mean?"], env, project, stdin=piped)
    assert p.returncode == 0
    sent = mock.all_text_sent()
    assert "Failed to open file" in sent and _reads(project)[0] not in sent and "withheld" in sent


def test_show_sends_nothing(project, env, mock):
    p = run_asktim(["show", "how do I sort a BAM file?"], env, project)
    assert p.returncode == 0 and "nothing has been sent" in p.stdout and "You are AskTim!" in p.stdout
    assert mock.requests == []


def test_file_option_sends_structure_only(project, env, mock):
    mock.queue = [{"text": "Use pandas."}]
    p = run_asktim(["--file", "data/genes.csv", "how do I plot length?"], env, project)
    assert p.returncode == 0
    sent = mock.all_text_sent()
    assert "length (whole numbers)" in sent and "YAL001C" not in sent


def test_without_a_key_there_is_offline_help(project, env, mock):
    e = dict(env)
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        e.pop(k)
    p = run_asktim(["what does this mean?"], e, project, stdin="bash: fastqc: command not found\n")
    assert "built-in help" in p.stdout and "no program of that name" in p.stdout and "AskTim! Claude" in p.stdout
    assert mock.requests == []


def test_status_privacy_tools_help(project, env):
    for args, want in ((["status"], "Data filter: on"), (["privacy"], "Never sent"), (["help"], "AskTim! Agent"), (["--version"], "AskTim!")):
        p = run_asktim(args, env, project)
        assert p.returncode == 0 and want in p.stdout, (args, p.stdout, p.stderr)


def test_setup_stores_key_after_checking_it(project, env, mock):
    e = dict(env)
    e.pop("ANTHROPIC_API_KEY")
    mock.queue = [{"text": "ready"}]
    p = run_asktim(["Claude", "sk-ant-api03-NEWKEY0123456789012345678901234567"], e, project)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "It works" in p.stdout and "readable by you only" in p.stdout and "shell's history" in p.stdout
    keys = json.load(open(os.path.join(e["ASKTIM_HOME"], "config", "keys.json")))
    assert keys["claude"].startswith("sk-ant-api03-NEWKEY")
    assert run_asktim(["forget"], e, project).returncode == 0
    assert not os.path.exists(os.path.join(e["ASKTIM_HOME"], "config", "keys.json"))


def test_setup_refuses_a_bad_key(project, env, mock):
    e = dict(env)
    e.pop("ANTHROPIC_API_KEY")
    mock.fail_next = [401]
    p = run_asktim(["Claude", "sk-ant-api03-BADKEY0123456789012345678901234567"], e, project)
    assert p.returncode == 1 and "not accepted" in p.stdout
    assert not os.path.exists(os.path.join(e["ASKTIM_HOME"], "config", "keys.json"))


def test_setup_asks_for_the_key_without_echo(project, env, mock):
    pexpect = pytest.importorskip("pexpect")
    e = dict(env)
    e.pop("ANTHROPIC_API_KEY")
    mock.queue = [{"text": "ready"}]
    c = pexpect.spawn(sys.executable, ["-m", "asktim", "Claude"], env=e, cwd=str(project), encoding="utf-8", timeout=30)
    c.expect("Key:")
    c.sendline("sk-ant-api03-TYPED0123456789012345678901234567")
    c.expect(pexpect.EOF)
    assert "TYPED0123" not in c.before  # not shown
    assert json.load(open(os.path.join(e["ASKTIM_HOME"], "config", "keys.json")))["claude"].endswith("TYPED0123456789012345678901234567")


@pytest.mark.skipif(not (shutil.which("bwa") and shutil.which("samtools")), reason="needs bwa and samtools")
def test_agent_auto_run_on_real_data(project, env, mock):
    D = "../../data"
    mock.queue = [
        {"text": "First the reads.", "tools": [("inspect_file", {"path": D + "/reads_R1.fastq"})]},
        {"tools": [("run_command", {"command": "head -n 8 %s/reads_R1.fastq" % D, "why": "Look at two reads."})]},
        {"tools": [("run_command", {"command": "ln -s %s/ref.fa . && bwa index ref.fa" % D, "why": "Link and index the reference."})]},
        {"tools": [("run_command", {"command": "bwa mem ref.fa %s/reads_R1.fastq > aln.sam && samtools sort -o aln.bam aln.sam && samtools index aln.bam" % D, "why": "Map, sort, index."})]},
        {"tools": [("run_command", {"command": "samtools flagstat aln.bam", "why": "Count mapped reads."})]},
        {"tools": [("run_command", {"command": "bwa index %s/ref.fa" % D, "why": "Must be blocked."})]},
        {"tools": [("run_command", {"command": "pip install pysam", "why": "Must be blocked."}), ("write_file", {"path": "count.sh", "content": "#!/usr/bin/env bash\nset -euo pipefail\nsamtools view -c aln.bam\n", "why": "A script."})]},
        {"tools": [("run_command", {"command": "bash count.sh", "why": "Run it."})]},
        {"text": "I mapped the reads; flagstat in your terminal shows how many mapped."},
    ]
    p = run_asktim(["Agent", "--auto", "map data/reads_R1.fastq to data/ref.fa and count the mapped reads"], env, project, timeout=180)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    sent = mock.all_text_sent()
    assert not [s for s in _reads(project)[:100] if s in sent], "reads reached the AI"
    assert "withheld" in sent and "mapped (" in sent  # flagstat's summary got through
    assert not os.path.exists(project / "data" / "ref.fa.bwt"), "nothing may be written next to the data"
    blocked = [b for q in mock.requests[-1:] for m in q["body"]["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result" and "Not run: AskTim! does not allow" in str(b.get("content"))]
    assert len(blocked) >= 2
    # the system prompt and tools stay the same through the run (Claude checks earlier thinking blocks against them)
    assert len(set(json.dumps(r["body"]["system"]) for r in mock.requests)) == 1
    run = sorted(glob.glob(str(project / "asktim-runs" / "*")))[-1]
    files = os.listdir(run)
    assert {"RUN.md", "run.json", "rerun.sh", "aln.bam", "count.sh"} <= set(files)
    rerun = open(os.path.join(run, "rerun.sh")).read()
    assert "bwa mem ref.fa" in rerun and "#   pip install pysam" in rerun and "cat > count.sh <<'ASKTIM_EOF'" in rerun
    md = open(os.path.join(run, "RUN.md")).read()
    assert "Statement on the use of AI" in md and "refused by AskTim!'s safeguards" in md


def test_agent_approve_mode_in_a_terminal(project, env, mock):
    pexpect = pytest.importorskip("pexpect")
    D = "../../data"
    mock.queue = [
        {"tools": [("run_command", {"command": "ls -l %s" % D, "why": "See the inputs."})]},
        {"tools": [("run_command", {"command": "head -n 2 %s/genes.csv" % D, "why": "Look at the header."})]},
        {"tools": [("run_command", {"command": "rm -rf %s" % D, "why": "Must be blocked."})]},
        {"tools": [("run_command", {"command": "wc -l %s/genes.csv" % D, "why": "Count lines."})]},
        {"text": "The table has 121 lines. Which column should I plot?"},
        {"text": "Then I stop here."},
    ]
    e = dict(env, TERM="xterm")
    c = pexpect.spawn(sys.executable, ["-m", "asktim", "Agent", "summarise data/genes.csv"], env=e, cwd=str(project), encoding="utf-8", timeout=60, dimensions=(40, 120))
    c.expect(r"Run it\?")
    c.sendline("y")
    c.expect(r"Run it\?")
    c.sendline("n")
    c.expect("Why not")
    c.sendline("use inspect_file instead")
    c.expect(r"Run it\?")
    c.sendline("e")
    c.expect(r"\$ ")
    c.send("\x15")  # clear the pre-filled command
    c.sendline("wc -l ../../data/genes.csv ../../data/ref.fa")
    c.expect("note for the agent")
    c.sendline("both files")
    c.expect("Your answer")
    c.sendline("the length column")
    c.expect(pexpect.EOF)
    assert os.path.isdir(project / "data") and (project / "data" / "genes.csv").exists()
    results = [b["content"] for m in mock.requests[-1]["body"]["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert "refused this step. Their reason: use inspect_file instead" in results[1]
    assert "does not allow" in results[2]
    assert "this ran instead" in results[3] and "both files" in results[3]
    rec = json.load(open(glob.glob(str(project / "asktim-runs" / "*" / "run.json"))[-1]))
    assert [s.get("decision") for s in rec["steps"]] == ["run", "refuse", "blocked", "edit"]


def test_agent_needs_a_person_to_approve(project, env):
    p = run_asktim(["Agent", "do something"], env, project)
    assert p.returncode == 2 and "--auto" in p.stdout


def test_agent_plan_mode(project, env, mock):
    pexpect = pytest.importorskip("pexpect")
    mock.queue = [
        {"text": "1. Count the lines of genes.csv.\n2. Report."},
        {"text": "1. Count the lines of genes.csv and ref.fa.\n2. Report."},
        {"tools": [("run_command", {"command": "wc -l ../../data/genes.csv ../../data/ref.fa", "why": "Count."})]},
        {"text": "Done."},
    ]
    c = pexpect.spawn(sys.executable, ["-m", "asktim", "Agent", "--plan", "count lines"], env=dict(env, TERM="xterm"), cwd=str(project), encoding="utf-8", timeout=60)
    c.expect("Approve the plan")
    c.sendline("c")
    c.expect("What should change")
    c.sendline("also count ref.fa")
    c.expect("Approve the plan")
    c.sendline("y")
    c.expect(pexpect.EOF)
    assert "also count ref.fa" in mock.all_text_sent() and "Run it?" not in c.before
    assert glob.glob(str(project / "asktim-runs" / "1-plan" / "RUN.md"))


def test_data_pasted_into_a_question_is_withheld(project, env, mock):
    reads = _reads(project)
    mock.queue = [{"text": "These are FASTQ records."}]
    q = "what are these lines?\n@r1\n%s\n+\n%s\n@r2\n%s\n+\n%s" % (reads[0], "F" * 76, reads[1], "F" * 76)
    p = run_asktim(["-v", q], env, project)
    assert p.returncode == 0 and "kept 8 pieces of data in your question" in p.stdout
    assert reads[0] not in mock.all_text_sent() and "what are these lines?" in mock.all_text_sent()


def test_agent_opens_a_file_in_an_installed_viewer(project, env, mock, tmp_path):
    pexpect = pytest.importorskip("pexpect")
    bin_dir = tmp_path / "viewers"
    bin_dir.mkdir()
    marker = tmp_path / "opened.txt"
    (bin_dir / "igv").write_text("#!/bin/sh\necho \"$1\" > %s\n" % marker)
    (bin_dir / "igv").chmod(0o755)
    mock.queue = [
        {"tools": [("open_file", {"path": "../../data/genes.csv", "program": "jalview", "why": "Not installed."})]},
        {"tools": [("open_file", {"path": "../../data/genes.csv", "program": "igv", "why": "Look at it in IGV."})]},
        {"text": "It is open in IGV."},
    ]
    e = dict(env, TERM="xterm", DISPLAY=":0", PATH=str(bin_dir) + os.pathsep + env["PATH"])
    c = pexpect.spawn(sys.executable, ["-m", "asktim", "Agent", "--auto", "show me data/genes.csv in IGV"], env=e, cwd=str(project), encoding="utf-8", timeout=60, dimensions=(40, 120))
    c.expect(r"in igv")
    c.expect(r"Open it\? \[y/N\]")  # asked even in --auto
    c.sendline("y")
    c.expect(pexpect.EOF)
    first = mock.requests[0]["body"]["messages"][0]["content"]
    first = first if isinstance(first, str) else " ".join(b.get("text", "") for b in first if isinstance(b, dict))
    assert "Programs with windows installed here" in first and "igv" in first
    results = [b["content"] for m in mock.requests[-1]["body"]["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert "jalview is not installed here" in results[0] and "Opened" in results[1] and "in igv" in results[1]
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.read_text().strip().endswith("data/genes.csv")


def test_the_agent_is_told_what_this_run_allows(project, env, mock):
    """--web, --steps and --timeout must reach the model's instructions, not only the checks."""
    mock.queue = [{"text": "Nothing to do."}]
    p = run_asktim(["Agent", "--auto", "--web", "--steps", "7", "--timeout", "30", "download a reference"], env, project)
    assert p.returncode == 0, p.stdout + p.stderr
    system = mock.requests[0]["body"]["system"][0]["text"]
    assert "allowed you to use the internet" in system and "There is no internet access" not in system
    assert "at most 7 steps" in system and "longer than 30 seconds" in system
    mock.queue = [{"text": "Nothing to do."}]
    run_asktim(["Agent", "--auto", "download a reference"], env, project)
    assert "There is no internet access" in mock.requests[-1]["body"]["system"][0]["text"]


def test_the_agent_sees_which_programs_and_folders_there_are(project, env, mock):
    (project / "asktim-runs").mkdir(exist_ok=True)
    for i in range(1, 6):
        (project / "asktim-runs" / ("%d-approve" % i)).mkdir()
    mock.queue = [{"tools": [("run_command", {"command": "ls ../../asktim-runs; which bash sh", "why": "Look around."})]}, {"text": "Done."}]
    p = run_asktim(["Agent", "--auto", "look around"], env, project)
    assert p.returncode == 0, p.stdout + p.stderr
    result = [b["content"] for m in mock.requests[-1]["body"]["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"][0]
    assert "withheld" not in result and "3-approve" in result and "/bin/bash" in result, result


def test_script_with_auto_still_makes_a_script(project, env, mock):
    # --auto used to win over --script, so the student got a plain run instead of one script
    mock.queue = [{"text": "Nothing to do: there is no task I can do here."}]
    p = run_asktim(["Agent", "--script", "--auto", "count the reads"], env, project, timeout=60)
    assert p.returncode == 0, p.stdout[-2000:]
    assert any(r.endswith("-script") for r in os.listdir(project / "asktim-runs"))
    assert "ONE script" in mock.requests[0]["body"]["system"][0]["text"]
    # no steps ran, so there is no rerun.sh to read
    assert "rerun.sh before you run it" not in p.stdout


def test_here_mode_does_not_report_the_agents_own_files_as_the_students(project, env, mock):
    mock.queue = [
        {"tools": [("run_command", {"command": "touch scratch.txt", "why": "Make a scratch file."})]},
        {"tools": [("run_command", {"command": "rm -f scratch.txt", "why": "Tidy up."})]},
        {"text": "Done."},
    ]
    p = run_asktim(["Agent", "--here", "--auto", "make and remove a scratch file"], env, project, timeout=120)
    assert p.returncode == 0, p.stdout[-3000:]
    assert "already in your folder" not in p.stdout
    assert "already in the user's folder" not in mock.all_text_sent()
    assert not (project / "scratch.txt").exists()


def test_the_agent_is_told_where_files_are_with_here(project, env, mock):
    # with --here the agent's folder is the student's: "../../data" sent it to the wrong place
    mock.queue = [{"text": "Nothing to do."}]
    assert run_asktim(["Agent", "--here", "--auto", "look at the data"], env, project).returncode == 0
    system = mock.requests[0]["body"]["system"][0]["text"]
    assert "the user's own folder" in system and "data/reads.fq" in system and "../../data" not in system
    mock.queue = [{"text": "Nothing to do."}]
    assert run_asktim(["Agent", "--auto", "look at the data"], env, project).returncode == 0
    assert "../../data/reads.fq" in mock.requests[-1]["body"]["system"][0]["text"]


# -- quiet by default, -v for everything -----------------------------------------------------------

def test_chat_prints_only_the_answer_unless_verbose(project, env, mock):
    mock.queue = [{"text": "Use `samtools sort`."}, {"text": "Use `samtools sort`."}, {"text": "Use `samtools sort`."}]
    p = run_asktim(["how do I sort a BAM?"], env, project)
    assert p.returncode == 0 and p.stdout.strip() == "Use `samtools sort`." and not p.stderr, (p.stdout, p.stderr)
    p = run_asktim(["--verbose", "how do I sort a BAM?"], env, project)
    assert "Sent with your question" in p.stdout and "AskTim! show" in p.stdout
    p = run_asktim(["how do I sort a BAM?"], dict(env, ASKTIM_VERBOSE="1"), project)
    assert "Sent with your question" in p.stdout


def test_the_agent_shows_its_commands_output_failures_and_report(project, env, mock):
    mock.queue = [
        {"tools": [("run_command", {"command": "ls ../../data", "why": "See the inputs."})]},
        {"tools": [("run_command", {"command": "false", "why": "Fail on purpose."})]},
        {"text": "Done: the inputs are listed."},
    ]
    p = run_asktim(["Agent", "--auto", "list the inputs"], env, project, timeout=120)
    assert p.returncode == 0, p.stdout[-3000:]
    out = p.stdout
    assert "$ ls ../../data" in out and "reads_R1.fastq" in out and "exit 1" in out and "Done: the inputs are listed." in out
    for chatter in ("Safeguards:", "the AI was shown", "wrote the record", "Step 1 ·", "Why: See the inputs"):
        assert chatter not in out, chatter
    mock.queue = [
        {"tools": [("run_command", {"command": "ls ../../data", "why": "See the inputs."})]},
        {"text": "Done."},
    ]
    out = run_asktim(["Agent", "--auto", "-v", "list the inputs"], env, project, timeout=120).stdout
    for chatter in ("Safeguards:", "the AI was shown", "wrote the record", "Step 1 ·", "Why: See the inputs"):
        assert chatter in out, chatter


# -- the data filter can be turned off, and stays off until it is turned on -------------------------

def _settings(env):
    p = os.path.join(env["ASKTIM_HOME"], "config", "config.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def test_privacy_off_lasts_until_privacy_on(project, env, mock):
    reads = _reads(project)
    p = run_asktim(["privacy", "off"], env, project)
    assert p.returncode == 0 and "now off" in p.stdout and "AskTim! privacy on" in p.stdout
    assert _settings(env)["privacy"] == "off"
    assert "Data filter: OFF" in run_asktim(["status"], env, project).stdout
    # a file asked about, text piped in and data pasted into the question now go to the AI ...
    mock.queue = [{"text": "They are reads."}, {"text": "Yes."}]
    p = run_asktim(["--file", "data/reads_R1.fastq", "what is in this file? %s" % reads[1]], env, project, stdin="@x\n%s\n+\n%s\n" % (reads[2], "F" * 76))
    assert p.returncode == 0, p.stdout + p.stderr
    sent = mock.all_text_sent()
    for r in reads[:3]:
        assert r in sent
    assert "turned AskTim!'s data filter off" in mock.requests[-1]["body"]["system"][0]["text"]
    # ... with a reminder, once a day
    assert "data filter is off" in p.stdout + p.stderr
    p = run_asktim(["and now?"], env, project)
    assert "data filter is off" not in p.stdout + p.stderr
    # secrets and personal details are still removed
    mock.queue = [{"text": "ok"}]
    run_asktim(["is sk-ant-api03-SECRETSECRETSECRET0123456789 my key? mail me at jo@example.org"], env, project)
    sent = json.dumps(mock.requests[-1]["body"])
    assert "SECRETSECRET" not in sent and "jo@example.org" not in sent
    # on again: nothing of the data goes
    p = run_asktim(["privacy", "on"], env, project)
    assert p.returncode == 0 and _settings(env)["privacy"] == "standard"
    mock.queue = [{"text": "ok"}]
    run_asktim(["--file", "data/reads_R1.fastq", "what is in this file?"], env, project)
    assert reads[0] not in json.dumps(mock.requests[-1]["body"])
    assert "You never see the contents" in mock.requests[-1]["body"]["system"][0]["text"]


def test_a_course_can_keep_the_data_filter_on(project, env, mock, tmp_path):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"privacy": "standard", "locked": ["privacy"]}))
    e = dict(env, ASKTIM_SITE_CONFIG=str(site))
    p = run_asktim(["privacy", "off"], e, project)
    assert p.returncode == 1 and "fix privacy" in p.stdout + p.stderr
    # even with "off" in the user's own settings, the course's value wins
    os.makedirs(os.path.join(e["ASKTIM_HOME"], "config"), exist_ok=True)
    with open(os.path.join(e["ASKTIM_HOME"], "config", "config.json"), "w") as f:
        json.dump({"privacy": "off"}, f)
    mock.queue = [{"text": "ok"}]
    run_asktim(["--file", "data/reads_R1.fastq", "what is in it?"], e, project)
    assert _reads(project)[0] not in mock.all_text_sent()


def test_the_agent_sees_output_with_the_data_filter_off(project, env, mock):
    reads = _reads(project)
    assert run_asktim(["privacy", "off"], env, project).returncode == 0
    mock.queue = [
        {"tools": [("run_command", {"command": "head -4 ../../data/reads_R1.fastq", "why": "Look at a read."})]},
        {"text": "It is a 76 bp read."},
    ]
    p = run_asktim(["Agent", "--auto", "show me a read"], env, project, timeout=120)
    assert p.returncode == 0, p.stdout[-3000:]
    assert reads[0] in mock.all_text_sent()
    system = mock.requests[0]["body"]["system"][0]["text"]
    assert "turned AskTim!'s data filter off" in system and "You cannot see data" not in system
    assert "with data lines withheld" not in json.dumps(mock.requests[0]["body"]["tools"])


def test_nothing_the_ai_is_told_calls_the_user_a_student():
    from asktim import config, prompts

    class Cfg(config.Config):
        def __init__(self, level):
            config.Config.__init__(self, {"privacy": level})

    for level in ("standard", "strict", "off"):
        c = Cfg(level)
        texts = [prompts.chat_system(["python 3"], c, notebook=nb) for nb in (False, True)]
        for mode in ("approve", "plan", "auto", "script"):
            for nb in (False, True):
                for rel in ("../..", "."):
                    texts.append(prompts.agent_system(["python 3"], c, mode, ".", rel, notebook=nb, allow_network=nb))
        texts += [json.dumps([t.__dict__ for t in prompts.tool_specs(notebook=nb, share_data=level == "off")]) for nb in (False, True)]
        for t in texts:
            assert "student" not in t.lower(), t[t.lower().find("student") - 80:t.lower().find("student") + 40]


def test_the_statement_says_when_the_data_filter_was_off(project, env, mock):
    mock.queue = [{"text": "ok"}, {"text": "ok"}]
    run_asktim(["first question"], env, project)
    assert "did not send the contents" in run_asktim(["statement"], env, project).stdout
    run_asktim(["privacy", "off"], env, project)
    run_asktim(["second question"], env, project)
    s = run_asktim(["statement"], env, project).stdout
    assert "For 1 of the 2 questions and tasks" in s and "data filter was off" in s


def test_verbose_on_lasts_until_off_and_a_flag_changes_one_command(project, env, mock):
    mock.queue = [{"text": "Answer."} for _ in range(5)]
    p = run_asktim(["verbose", "on"], env, project)
    assert p.returncode == 0 and "verbose now" in p.stdout and _settings(env)["verbose"] is True
    assert "everything" in run_asktim(["verbose"], env, project).stdout
    assert "Sent with your question" in run_asktim(["a question"], env, project).stdout
    p = run_asktim(["-q", "a question"], env, project)  # quiet for this command only
    assert p.stdout.strip() == "Answer.", p.stdout
    assert "Sent with your question" in run_asktim(["a question"], env, project).stdout
    p = run_asktim(["verbose", "off"], env, project)
    assert p.returncode == 0 and _settings(env)["verbose"] is False
    assert run_asktim(["a question"], env, project).stdout.strip() == "Answer."
    assert "Sent with your question" in run_asktim(["-v", "a question"], env, project).stdout
    assert run_asktim(["verbose", "loud"], env, project).returncode == 2
    assert "Output: only the answer" in run_asktim(["status"], env, project).stdout


def test_a_course_can_fix_verbose(project, env, mock, tmp_path):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"verbose": True, "locked": ["verbose"]}))
    e = dict(env, ASKTIM_SITE_CONFIG=str(site))
    p = run_asktim(["verbose", "off"], e, project)
    assert p.returncode == 1 and "fix this" in p.stdout + p.stderr
    mock.queue = [{"text": "Answer."}]
    assert "Sent with your question" in run_asktim(["a question"], e, project).stdout
