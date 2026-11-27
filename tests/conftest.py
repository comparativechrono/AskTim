import os
import random
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from mock_ai import MockAI  # noqa: E402

KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-test-0123456789012345678901234567890123",
    "GEMINI_API_KEY": "AIzaTEST0123456789012345678901234567",
    "OPENAI_API_KEY": "sk-test-0123456789012345678901234",
}


def _seq(n, rnd):
    return "".join(rnd.choice("ACGT") for _ in range(n))


@pytest.fixture
def project(tmp_path):
    """A student's folder with small but real-looking data."""
    rnd = random.Random(7)
    p = tmp_path / "practical"
    d = p / "data"
    d.mkdir(parents=True)
    ref = _seq(3000, rnd)
    (d / "ref.fa").write_text(">chr10\n" + "\n".join(ref[i:i + 60] for i in range(0, len(ref), 60)) + "\n")
    with open(d / "reads_R1.fastq", "w") as f:
        for i in range(200):
            start = rnd.randrange(0, 2900)
            s = ref[start:start + 76]
            f.write("@SRR098401.%d/1\n%s\n+\n%s\n" % (i, s, "".join(rnd.choice("#,:FFF") for _ in s)))
    with open(d / "genes.csv", "w") as f:
        f.write("orf,gene,essential,length\n")
        for i in range(120):
            f.write("YAL%03dC,GENE%d,%s,%d\n" % (i, i, rnd.choice(["True", "False"]), rnd.randint(100, 3000)))
    (d / "patients.csv").write_text("John,Smith,London,1985-03-02\nMary,Jones,Leeds,1990-11-12\nAhmed,Khan,York,1979-05-30\n")
    return p


@pytest.fixture(autouse=True)
def _not_typed_into_a_terminal(monkeypatch):
    """pytest itself may have been started from an interactive terminal: AskTim! code run in the tests' own
    process must not take that terminal for one it was typed into. (AskTim! run as a program still checks.)"""
    from asktim import history

    monkeypatch.setattr(history, "typed_into", lambda: None)


@pytest.fixture
def mock():
    m = MockAI()
    yield m
    m.stop()


@pytest.fixture
def env(tmp_path, mock):
    home = tmp_path / "home"
    home.mkdir()
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("ASKTIM_") or k in ("HISTFILE",):
            e.pop(k)
    e.update(KEYS)
    e.update({
        "HOME": str(home),
        "ASKTIM_HOME": str(tmp_path / "asktim"),
        "ASKTIM_SITE_CONFIG": str(tmp_path / "no-site-config.json"),
        "ASKTIM_ANTHROPIC_URL": mock.url,
        "ASKTIM_GEMINI_URL": mock.url,
        "ASKTIM_OPENAI_URL": mock.url + "/v1",
        "PYTHONPATH": os.path.join(os.path.dirname(HERE), "src"),
        "NO_COLOR": "1",
        "TERM": "dumb",
        "SHELL": "/bin/bash",  # the tests' history files are bash's, whatever shell runs the tests
    })
    return e


def run_asktim(args, env, cwd, stdin=None, timeout=120):
    """The AskTim! command, as a student runs it (python -m asktim is the same program)."""
    return subprocess.run([sys.executable, "-m", "asktim"] + list(args), env=env, cwd=str(cwd), input=stdin,
                          stdin=None if stdin is not None else subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


@pytest.fixture
def asktim():
    return run_asktim
