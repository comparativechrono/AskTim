"""AskTim! in a real IPython kernel, driven as a notebook frontend drives it."""

import glob
import html
import json
import os
import sys

import pytest

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")

from kernel_harness import Kernel  # noqa: E402


@pytest.fixture
def kernel(project, env):
    e = dict(env, PATH=os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", ""))
    k = Kernel(env=e, cwd=str(project))
    k.run("import asktim")
    yield k
    k.stop()


def test_the_syntax_works_in_cells(kernel, mock):
    mock.queue = [{"text": "one"}, {"text": "two"}, {"text": "three"}]
    for cell in ('AskTim! "a question"', "%AskTim! a question", "AskTim! a question\nwith code below = 1"):
        r = kernel.run(cell)
        assert r["status"] == "ok" and not r["errors"], (cell, r)
    assert len(mock.requests) == 3
    assert "with code below = 1" in mock.requests[-1]["body"]["messages"][-1]["content"][0]["text"]
    r = kernel.run("x = 1\nprint(x + 1)")
    assert r["stream"].strip() == "2"  # ordinary code is untouched


def test_error_help_sends_code_and_shapes_not_values(kernel, mock):
    pytest.importorskip("pandas")
    kernel.run("import pandas as pd\ndf = pd.read_csv('data/genes.csv')\nfirst = df.head()\ndf['lenght'].mean()")
    mock.queue = [{"text": "The column is `length`:\n\n```python\ndf['length'].mean()\n```"}]
    r = kernel.run('AskTim! "why did that fail?"')
    assert r["status"] == "ok"
    sent = mock.all_text_sent()
    assert "KeyError" in sent and "df: pandas DataFrame, 120 rows" in sent and "length (int64)" in sent
    assert "YAL001C" not in sent and "GENE1," not in sent
    r = kernel.run("AskTim! insert 1")
    assert r["payload"] and r["payload"][0]["source"] == "set_next_input" and r["payload"][0]["text"] == "df['length'].mean()"


def test_key_typed_into_a_cell_is_scrubbed(kernel, mock):
    mock.queue = [{"text": "ready"}]
    r = kernel.run("AskTim! Claude sk-ant-api03-INCELL0123456789012345678901234567")
    assert r["payload"] and r["payload"][0]["replace"] is True and "INCELL" not in r["payload"][0]["text"]
    hist = kernel.run("k = 'INC' + 'ELL'\nprint([h for h in get_ipython().history_manager.input_hist_raw if k in h and 'print' not in h])")
    assert hist["stream"].strip() == "[]"


def test_notebook_agent(kernel, mock, project):
    pytest.importorskip("pandas")
    mock.queue = [
        {"text": "First the table.", "tools": [("run_python", {"code": "import pandas as pd\ngenes = pd.read_csv('data/genes.csv')\nprint(genes.head())\ngenes.shape", "why": "Load the table."})]},
        {"tools": [("describe_variable", {"name": "genes"})]},
        {"tools": [("run_python", {"code": "n = int(genes['essential'].astype(str).eq('True').sum())\nprint('essential genes:', n)", "why": "Count."})]},
        {"tools": [("add_markdown_cell", {"text": "## Essential genes"}), ("add_code_cell", {"code": "genes['essential'].value_counts()", "why": "Counts."})]},
        {"tools": [("run_python", {"code": "!pip install seaborn", "why": "Must be blocked."})]},
        {"tools": [("run_command", {"command": "wc -l ../../data/genes.csv", "why": "Count lines."})]},
        {"text": "Done: the count is printed above."},
    ]
    r = kernel.run('AskTim! Agent "count the essential genes in data/genes.csv"', inputs=["y", "a"])
    assert r["status"] == "ok" and not r["errors"], r
    assert len(r["prompts"]) == 2
    sent = mock.all_text_sent()
    assert "YAL001C" not in sent and "withheld" in sent and "essential genes:" in sent
    assert "installs software" in sent
    # the agent's variables stay in the student's kernel
    assert kernel.run("print(n, genes.shape)")["stream"].split()[1] == "(120,"
    # the cells arrive one by one
    assert r["payload"][0]["text"].startswith("%%markdown\n<!-- AskTim! cell 1 of 2")
    r2 = kernel.run(r["payload"][0]["text"])
    assert r2["payload"] and r2["payload"][0]["text"].startswith("# AskTim! cell 2 of 2")
    rec = sorted(glob.glob(str(project / "asktim-runs" / "*" / "agent-record.ipynb")))[-1]
    assert json.load(open(rec))["nbformat"] == 4


def test_agent_will_not_run_unattended_in_approve_mode(project, env, mock):
    nbformat = pytest.importorskip("nbformat")
    nbclient = pytest.importorskip("nbclient")
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("import asktim"), nbformat.v4.new_code_cell('AskTim! Agent "delete everything"')]
    e = dict(env)
    old = dict(os.environ)
    os.environ.update(e)
    try:
        nbclient.NotebookClient(nb, timeout=60, kernel_name="python3", resources={"metadata": {"path": str(project)}}).execute()
    finally:
        os.environ.clear()
        os.environ.update(old)
    out = json.dumps(nb.cells[1].outputs)
    assert "needs you to approve" in out and mock.requests == []


def test_the_demo_notebook_runs_and_sends_no_reads(tmp_path, env, mock):
    pytest.importorskip("pandas")  # the demo's error cell uses pandas
    nbformat = pytest.importorskip("nbformat")
    nbclient = pytest.importorskip("nbclient")
    demo = os.path.join(os.path.dirname(__file__), "..", "examples", "AskTim-demo.ipynb")
    nb = nbformat.read(demo, as_version=4)
    mock.queue = [
        {"text": "Use Biopython:\n\n```python\nfrom Bio import SeqIO\nlengths = [len(r) for r in SeqIO.parse('reads.fastq', 'fastq')]\n```"},
        {"text": "The column is called `control_1`; you wrote `contrl_1`."},
    ]
    work = tmp_path / "demo"
    work.mkdir()
    old = dict(os.environ)
    os.environ.update(env)
    try:
        nbclient.NotebookClient(nb, timeout=120, kernel_name="python3", resources={"metadata": {"path": str(work)}}).execute()
    finally:
        os.environ.clear()
        os.environ.update(old)
    outs = [json.dumps(c.get("outputs", [])) for c in nb.cells if c.cell_type == "code"]
    assert "Wrote reads.fastq" in outs[2]
    assert "KeyError" in outs[4] and "contrl_1" in html.unescape(outs[5]) and "&lt;small" not in outs[5]
    assert "nothing has been sent" in outs[6] and "reads.fastq" in outs[6]
    assert "needs you to approve" in outs[7]  # nobody to approve when nbclient runs it
    assert "Statement on the use of AI" in outs[8]
    reads = [ln for ln in (work / "reads.fastq").read_text().splitlines()[1::4]]
    sent = json.dumps([r["body"] for r in mock.requests])
    assert len(mock.requests) == 2 and not any(r[:30] in sent for r in reads)
