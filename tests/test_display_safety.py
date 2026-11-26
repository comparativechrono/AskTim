"""What the AI writes is shown to the student exactly, and cannot act on the screen or the notebook page."""

import html
import json
import os
import random
import re
import shutil
import subprocess

import pytest

from asktim import safety
from asktim.render import inline_code, inline_markdown, safe_markdown, visible

ATTACKS = [
    "<img src=x onerror=alert(1)>", "<script>alert(1)</script>", "<svg onload=alert(1)>", "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))", "[x](\njavascript:alert(1))", "[x][r]\n\n[r]: javascript:alert(1)", "![a\" onerror=\"alert(1)](x)",
    "`[x](javascript:alert(1))`", "<details open ontoggle=alert(1)>", "<b>\n<img src=x onerror=alert(1)>",
    "- item\n```\n<img src=x onerror=alert(1)>\n```", "Some text\n    ```\n    [x](javascript:alert(1))",
    "<details>\n```\n<img src=x onerror=alert(1)>\n```", "$\\href{javascript:alert(1)}{x}$",
]


def test_control_characters_are_made_visible():
    assert visible("a\x1b[2Kb\rc\x07d\x7f\u202e\tz\n") == "a^[[2Kb^Mc^Gd^?\\u202e\tz\n"


def test_commands_with_hidden_characters_are_refused(tmp_path):
    for cmd in ["rm -rf ~/thesis #\r\x1b[2Kls -la", "echo \u202eabc", "ls\x08\x08rm x"]:
        v = safety.check(cmd, str(tmp_path), student_dir=str(tmp_path / "student"))
        assert v.level == "block" and "invisible" in v.reasons[0]
    assert safety.check('printf "a\\tb\\n"', str(tmp_path)).level == "ok"


def test_files_with_direction_overrides_are_refused(tmp_path):
    v, _p = safety.check_file_write("x.py", str(tmp_path), "access = 'user' # \u202e } \u2066if admin\u2069 \u2066 begin")
    assert v.level == "block"
    v, _p = safety.check_file_write("x.py", str(tmp_path), "print('fine')\n")
    assert v.level == "ok"


def test_safe_markdown_leaves_no_html_or_script_links():
    allowed = re.compile(r"</code></pre>|<pre><code(?: class=\"language-[\w+#.\-]+\")?>|</?code>")
    for a in ATTACKS:
        for wrap in ("{}", "1. step\n   {}", "| a | b |\n|---|---|\n| {} | x |", "# {}", "> {}"):
            out = safe_markdown(wrap.format(a))
            rest = allowed.sub("", out)
            assert "<" not in rest, (a, out)
            assert not re.search(r"\]\(\s*(?:&lt;)?\s*javascript", out, re.I), (a, out)
            assert "\\href" not in out


def test_safe_markdown_keeps_ordinary_answers_readable():
    md = "Use **samtools**:\n\n```bash\nsamtools view -h in.bam > out.sam 2>&1\n```\n\n- see [the manual](https://www.htslib.org/doc/samtools.html)\n- `a < b`"
    out = safe_markdown(md)
    assert "**samtools**" in out and "[the manual](https://www.htslib.org/doc/samtools.html)" in out
    code = re.search(r'<pre><code class="language-bash">(.*?)</code></pre>', out, re.S).group(1)
    assert html.unescape(code) == "samtools view -h in.bam > out.sam 2>&1"
    assert html.unescape(re.search(r"<code>(.*?)</code>", out.split("</pre>")[1]).group(1)) == "a < b"
    assert inline_markdown("index\nthe ```reference```") == "index the <code>reference</code>"
    assert html.unescape(inline_code("data/<x>.bam")[6:-7]) == "data/<x>.bam"


def test_notebook_code_may_not_write_its_own_html_into_the_page():
    for code in ["from IPython.display import HTML\nHTML('<b>x</b>')", "%%javascript\nalert(1)", "df.style.background_gradient()",
                 "display(Markdown(text))", "class A:\n    def _repr_html_(self):\n        return ''"]:
        v = safety.Verdict()
        safety.check_notebook_page(code, v)
        assert v.level == "block", code
    for code in ["df = pd.read_csv('x.csv')\ndf.head()", "plt.plot(x, y)\nplt.show()", "display(df)", "print(df.describe())"]:
        v = safety.Verdict()
        safety.check_notebook_page(code, v)
        assert v.level == "ok", code


def _marked_dir():
    d = os.environ.get("ASKTIM_NODE_MODULES")
    if d and shutil.which("node") and os.path.isdir(os.path.join(d, "marked")) and os.path.isdir(os.path.join(d, "jsdom")):
        return d
    return None


@pytest.mark.skipif(_marked_dir() is None, reason="set ASKTIM_NODE_MODULES to a node_modules folder with marked and jsdom to run this")
def test_markdown_fuzz_rendered_by_marked(tmp_path):
    """Random Markdown full of attacks, sanitised, then rendered by marked (JupyterLab's parser): nothing may run."""
    random.seed(7)
    toks = ["`", "``", "```", "~~~", "    ", "  ", "\n", "\n\n", "- ", "1. ", "> ", "|", "[", "]", "(", ")", "](", "]:", "!", "![", "\"",
            "javascript:alert(1)", "data:text/html,x", "<", ">", "<img src=x onerror=alert(1)>", "<script>alert(1)</script>",
            "<details ontoggle=alert(1) open>", "<b>", "<pre>", "</pre>", "&", "&#106;", "&lt;", "\\", "*", "_", "$", "https://ok.org", "x", " "]
    cases = []
    for c in ATTACKS:
        cases.append({"raw": c, "safe": safe_markdown(c)})
    for _ in range(3000):
        raw = "".join(random.choice(toks) for _ in range(random.randint(3, 40)))
        cases.append({"raw": raw, "safe": safe_markdown(raw)})
    f = tmp_path / "cases.json"
    f.write_text(json.dumps(cases))
    script = os.path.join(os.path.dirname(__file__), "markdown_fuzz", "check.mjs")
    p = subprocess.run(["node", script, str(f)], capture_output=True, text=True, env=dict(os.environ, NODE_PATH=_marked_dir()), timeout=600)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]


def test_open_file_can_start_an_installed_viewer(tmp_path, monkeypatch):
    import time

    from asktim import viewers

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "opened.txt"
    igv = bin_dir / "igv"
    igv.write_text("#!/bin/sh\necho \"$1 ${ANTHROPIC_API_KEY:-no-key}\" > %s\n" % marker)
    igv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    bam = tmp_path / "aln.bam"
    bam.write_bytes(b"x")
    assert "igv" in viewers.installed()
    assert viewers.find("rm")[0] is None and viewers.find("jalview")[0] is None
    ok, how = viewers.launch(str(bam), "igv")
    assert ok and how == "in igv"
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.05)
    assert marker.read_text().split() == [str(bam), "no-key"]  # the program gets no API keys
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    ok, how = viewers.launch(str(bam), "igv")
    assert not ok and "no DISPLAY" in how
