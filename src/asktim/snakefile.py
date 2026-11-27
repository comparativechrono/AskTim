"""Reading a Snakemake workflow (never running it) for AskTim!'s checks.

The agent may write a Snakefile and run `snakemake`. The command line alone says little about what
that will do, so safety.py asks this module for what is inside the workflow: each rule's shell
command (with {input}, {output} and the like filled in, so that the shell checks see real paths),
its Python code (`run:` blocks), the scripts it runs, the files it writes (output, log, benchmark),
and the parts that would download code (wrappers, remote modules). Top-level Python code is
returned too: Snakemake runs it whenever it reads the Snakefile, even for a dry run.

The reader is deliberately simple: it follows the layout of rules and directives line by line and
reads directive values with Python's own parser. What it cannot read is reported as unknown, so
that the checks can ask the user rather than guess.
"""

import ast
import os
import re
import textwrap

DYNAMIC = object()  # a value that depends on Python code: AskTim! cannot know it

RULE_DIRECTIVES = {
    "input", "output", "log", "benchmark", "params", "threads", "resources", "priority", "version", "message",
    "shell", "run", "script", "notebook", "wrapper", "template_engine", "conda", "container", "singularity",
    "containerized", "envmodules", "shadow", "group", "cache", "handover", "default_target", "localrule",
    "retries", "name", "wildcard_constraints", "pathvars",
}
TOP_DIRECTIVES = {
    "include", "workdir", "configfile", "localrules", "ruleorder", "wildcard_constraints", "report", "container",
    "containerized", "conda", "envvars", "pepfile", "pepschema", "storage", "singularity", "scattergather",
    "resource_scopes", "inputflags", "outputflags", "pathvars",
}
PYTHON_BLOCKS = {"onstart", "onsuccess", "onerror"}

_RULE = re.compile(r"^(\s*)(rule|checkpoint)\b\s*([A-Za-z_]\w*)?\s*:\s*(#.*)?$")
_DIRECTIVE = re.compile(r"^(\s*)([A-Za-z_]\w*)\s*:(.*)$")
_MODULE = re.compile(r"^(\s*)module\s+([A-Za-z_]\w*)\s*:\s*(#.*)?$")
_USE_RULE = re.compile(r"^(\s*)use\s+rule\b.*$")
NAMES = ("snakefile", "Snakefile", os.path.join("workflow", "Snakefile"), os.path.join("workflow", "snakefile"))


class Rule(object):
    def __init__(self, name):
        self.name = name
        self.inputs = []  # [(name or None, path or DYNAMIC)]
        self.outputs = []
        self.logs = []
        self.benchmarks = []
        self.params = []
        self.shell = None  # the command (str), DYNAMIC, or None
        self.run = None  # Python code
        self.script = None  # path (str) or DYNAMIC
        self.notebook = None
        self.wrapper = None
        self.remote = False  # uses storage(...) for its files


class Workflow(object):
    def __init__(self, path):
        self.path = path
        self.rules = []
        self.top_code = ""  # Python run when Snakemake reads the file
        self.includes = []  # paths (str) or DYNAMIC
        self.workdirs = []
        self.remote_modules = []  # modules read from GitHub, GitLab or the web
        self.storage = False  # storage providers (files on remote servers)


def find(workdir, given=None):
    """The Snakefile that `snakemake` would read: -s/--snakefile, or one of the default names."""
    if given:
        p = given if os.path.isabs(given) else os.path.join(workdir, given)
        return os.path.realpath(p) if os.path.isfile(p) else None
    for name in NAMES:
        p = os.path.join(workdir, name)
        if os.path.isfile(p):
            return os.path.realpath(p)
    return None


def _indent(line):
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))


def _open_quotes(text):
    """True if text ends inside a triple-quoted string."""
    state = None
    i = 0
    while i < len(text):
        if state is None:
            if text.startswith('"""', i) or text.startswith("'''", i):
                state = text[i:i + 3]
                i += 3
                continue
            if text[i] == "#":
                j = text.find("\n", i)
                i = len(text) if j < 0 else j
                continue
            if text[i] in "\"'":
                q = text[i]
                j = i + 1
                while j < len(text) and text[j] != q and text[j] != "\n":
                    j += 2 if text[j] == "\\" else 1
                i = j + 1
                continue
        elif text.startswith(state, i):
            state = None
            i += 3
            continue
        i += 1
    return state is not None


def _block(lines, i, base_indent):
    """The lines after lines[i] that belong to it (indented more, or inside a triple-quoted string)."""
    out = []
    acc = lines[i]
    j = i + 1
    while j < len(lines):
        ln = lines[j]
        if _open_quotes(acc) or not ln.strip() or _indent(ln) > base_indent or ln.lstrip().startswith("#"):
            out.append(ln)
            acc += "\n" + ln
            j += 1
            continue
        break
    while out and not out[-1].strip():
        out.pop()
        j -= 1
    return out, j


def _call_args(value):
    """The arguments of a directive's value, read as a call: [(name or None, ast node)], or None."""
    value = value.strip()
    if not value:
        return []
    try:
        tree = ast.parse("f(\n%s\n)" % value, mode="eval")
    except SyntaxError:
        try:
            tree = ast.parse("f(\n%s\n)" % re.sub(r"#[^\n'\"]*$", "", value, flags=re.M), mode="eval")
        except SyntaxError:
            return None
    call = tree.body
    return [(None, a) for a in call.args] + [(k.arg, k.value) for k in call.keywords]


def _strings(node):
    """The file names in a directive value: strings, and the strings inside expand(), temp(), multiext() ...
    Returns (paths, remote) where a path may be DYNAMIC."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value], False
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        out, remote = [], False
        for e in node.elts:
            p, r = _strings(e)
            out += p
            remote = remote or r
        return out, remote
    if isinstance(node, ast.Call):
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
        if owner == "storage" or name in ("remote", "storage"):
            return [DYNAMIC], True
        args = [a for a in node.args]
        if name == "multiext" and args:
            first = args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                exts = [a.value for a in args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                return [first.value + e for e in exts] or [first.value], False
        if name in ("expand", "temp", "temporary", "protected", "directory", "ancient", "touch", "pipe", "service",
                    "report", "unpack", "local", "update", "before_update", "flag", "ensure", "lookup") and args:
            return _strings(args[0])
        return [DYNAMIC], False
    return [DYNAMIC], False


def _parse_rule(name, body):
    rule = Rule(name)
    lines = [ln for ln in body]
    first = next((ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")), None)
    if first is None:
        return rule
    d = _indent(first)
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = _DIRECTIVE.match(ln)
        if not (m and _indent(ln) == d and m.group(2) in RULE_DIRECTIVES):
            i += 1
            continue
        key, rest = m.group(2), m.group(3)
        more, j = _block(lines, i, d)
        value = rest + ("\n" + "\n".join(more) if more else "")
        i = j
        if key == "run":
            rule.run = textwrap.dedent((rest.strip() + "\n" if rest.strip() else "") + "\n".join(more))
            continue
        args = _call_args(value)
        if key in ("input", "output", "log", "benchmark", "params"):
            items = []
            for nm, node in (args if args is not None else [(None, None)]):
                if node is None:
                    items.append((nm, DYNAMIC))
                    continue
                paths, remote = _strings(node)
                rule.remote = rule.remote or remote
                items += [(nm, p) for p in paths]
            getattr(rule, {"input": "inputs", "output": "outputs", "log": "logs", "benchmark": "benchmarks", "params": "params"}[key]).extend(items)
        elif key in ("shell", "script", "notebook", "wrapper"):
            val = DYNAMIC
            if args:
                node = args[0][1]
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    val = node.value
            setattr(rule, key, val)
    return rule


def parse(text, path=""):
    wf = Workflow(path)
    lines = text.splitlines()
    top = []
    i = 0
    n = len(lines)
    count = 0
    while i < n:
        ln = lines[i]
        m = _RULE.match(ln)
        if m:
            count += 1
            body, j = _block(lines, i, _indent(ln))
            wf.rules.append(_parse_rule(m.group(3) or "number %d" % count, body))
            i = j
            continue
        m = _MODULE.match(ln)
        if m:
            body, j = _block(lines, i, _indent(ln))
            text_ = "\n".join(body)
            if re.search(r"\b(github|gitlab)\s*\(|https?://|ftp://", text_):
                wf.remote_modules.append(m.group(2))
            i = j
            continue
        if _USE_RULE.match(ln):
            body, j = _block(lines, i, _indent(ln))
            i = j
            continue
        m = _DIRECTIVE.match(ln)
        if m and _indent(ln) == 0 and (m.group(2) in TOP_DIRECTIVES or m.group(2) in PYTHON_BLOCKS):
            key, rest = m.group(2), m.group(3)
            more, j = _block(lines, i, 0)
            i = j
            if key in PYTHON_BLOCKS:
                top.append(textwrap.dedent((rest.strip() + "\n" if rest.strip() else "") + "\n".join(more)))
                continue
            value = rest + ("\n" + "\n".join(more) if more else "")
            args = _call_args(value)
            if key in ("include", "workdir"):
                for _nm, node in (args or [(None, None)]):
                    paths, _r = _strings(node) if node is not None else ([DYNAMIC], False)
                    (wf.includes if key == "include" else wf.workdirs).extend(paths)
            elif key == "storage":
                wf.storage = True
            continue
        top.append(ln)
        i += 1
    wf.top_code = "\n".join(top)
    if re.search(r"\bstorage\.\w+\s*\(", text):
        wf.storage = True
    return wf


def shell_calls(code):
    """The commands in shell("...") calls of Python code (run: blocks, onstart: ...): str or DYNAMIC."""
    out = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [DYNAMIC] if re.search(r"\bshell\s*\(", code) else []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "shell":
            a = node.args[0] if node.args else None
            out.append(a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else DYNAMIC)
    return out


def fill(template, rule):
    """A rule's shell command with {input}, {output}, {log}, {params.x}, {wildcards.x} ... filled in."""
    t = template.replace("{{", "\x00").replace("}}", "\x01")

    def group(items, key):
        if key is None:
            return " ".join(p if p is not DYNAMIC else "X" for _n, p in items) or "X"
        if key.isdigit():
            k = int(key)
            return items[k][1] if k < len(items) and items[k][1] is not DYNAMIC else "X"
        for n, p in items:
            if n == key:
                return p if p is not DYNAMIC else "X"
        return "X"

    def rep(m):
        expr = re.split(r"[:!]", m.group(1).strip(), maxsplit=1)[0]
        mm = re.match(r"^(\w+)(?:\.(\w+)|\[\s*['\"]?(\w+)['\"]?\s*\])?$", expr)
        if not mm:
            return "X"
        head, key = mm.group(1), mm.group(2) or mm.group(3)
        if head in ("input", "output", "log", "benchmark", "params"):
            return group(getattr(rule, {"input": "inputs", "output": "outputs", "log": "logs", "benchmark": "benchmarks", "params": "params"}[head]), key)
        if head in ("threads", "resources"):
            return "1"
        if head == "wildcards":
            return "W"
        return "X"

    t = re.sub(r"\{([^{}\n]*)\}", rep, t)
    return t.replace("\x00", "{").replace("\x01", "}")


def wildcard_free(path):
    """A file name with its wildcards ({sample}) replaced, for checking where it lies."""
    return re.sub(r"\{[^{}]*\}", "W", path)
