"""What may go to the AI, and what may not.

Everything AskTim! sends to an AI service passes through this module.

1. Secrets are removed: API keys, tokens, passwords, credentials in URLs, private keys.
2. Personal details are removed: e-mail addresses, NHS numbers, UK phone and National
   Insurance numbers, and long DNA/RNA/protein sequences written into a command or a message.
3. The student's user name, home folder and computer name are replaced by "user", "~", "host".
4. Output of commands and code is filtered line by line (DataFilter). Lines that look like
   data - sequences, reads, alignments, variants, intervals, table rows, columns of numbers,
   lists of IDs, encoded blobs - are withheld, and a note says how many lines were withheld
   and what they looked like. What gets through is the kind of text a tutor needs: error
   messages, warnings, help text, versions, counts and summaries, file listings.

The filter works on the shape of the text, so it errs on the side of withholding. It cannot
promise to recognise every piece of data in every format: a value quoted in an error message,
for example, can still get through (the "strict" setting replaces quoted values too).
"""

import getpass
import os
import re
import socket
from collections import Counter

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")

# -----------------------------------------------------------------------------------------------
# secrets
# -----------------------------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), "[private key]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), "[API key]"),
    (re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"), "[API key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[API key]"),
    (re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"), "[API key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), "[token]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), "[token]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "[token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[token]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[access key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "[token]"),
    # credentials inside URLs: https://name:password@host
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[credentials]@"),
    # Authorization headers
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic|token)\s+)(?!\[)[^\s'\"]+"), r"\1[secret]"),
    (re.compile(r"(?i)\b(x-api-key|x-goog-api-key|api-key|apikey)(\s*[:=]\s*)(?!\[)[^\s'\"]+"), r"\1\2[secret]"),
    # NAME_KEY=value, PASSWORD=value, export TOKEN="value" ... : see _assignments() below
    # --password VALUE, --token=VALUE, -p VALUE for mysql-like tools is too ambiguous to touch
    (re.compile(r"(?i)(--(?:password|passwd|token|api[-_]?key|secret|auth[-_]?token)(?:=|\s+))(?!\[)(\"[^\"]*\"|'[^']*'|[^\s;&|]+)"), r"\1[secret]"),
]

# AskTim!'s own set-up command: AskTim! Claude sk-...  (the key may not match a pattern above)
_SETUP = re.compile(r"(?i)\b(asktim!?\s+(?:claude|anthropic|gemini|google|openai|chatgpt|gpt|local)\s+)(\S{12,})")


_SECRET_WORDS = {"key", "apikey", "token", "secret", "password", "passwd", "pass", "pwd", "credential", "credentials", "auth", "pat"}
_ASSIGN = re.compile(r"(?i)\b([A-Za-z_][A-Za-z0-9_]*)(\s*[=:]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s;&|'\"]+)")


def _assignments(text):
    """NAME=value where NAME holds a word such as KEY, TOKEN, SECRET or PASSWORD, and the value
    is a literal (not code: secret_rows = df.head() stays as it is)."""
    count = [0]

    def rep(m):
        name, sep, val = m.group(1), m.group(2), m.group(3)
        words = set(w.lower() for w in re.split(r"_+|(?<=[a-z])(?=[A-Z])", name) if w)
        if not words & _SECRET_WORDS or name in ("PWD", "OLDPWD") or val.startswith("[") or val.startswith("$"):
            return m.group(0)
        if val[:1] in "\"'":
            inner = val[1:-1]
            if len(inner) < 4 or re.fullmatch(r"[A-Za-z_][\w]*", inner) and len(inner) < 16 and not re.search(r"\d", inner):
                return m.group(0)  # a short word, such as sort_key = "length"
            count[0] += 1
            return name + sep + val[0] + "[secret]" + val[0]
        if re.search(r"[()\[\]{}]", val) or re.fullmatch(r"[A-Za-z_][\w.]*", val) and not re.search(r"\d", val) or len(val) < 4:
            return m.group(0)  # code (df.head(), other_variable) or nothing worth hiding
        count[0] += 1
        return name + sep + "[secret]"

    out = _ASSIGN.sub(rep, text)
    return out, count[0]


def redact_secrets(text):
    """(text without secrets, number of replacements)."""
    if not text:
        return text, 0
    n = 0
    text, k = _SETUP.subn(r"\1[API key]", text)
    n += k
    text, k = _assignments(text)
    n += k
    for pat, rep in _SECRET_PATTERNS:
        text, k = pat.subn(rep, text)
        n += k
    # long random-looking tokens (mixed case and digits, 32+ characters) are treated as secrets
    def _entropy(m):
        tok = m.group(0)
        if _is_checksum(tok) or _is_sequence(tok):
            return tok
        if re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok) and re.search(r"\d", tok) and not re.search(r"[/\\.]", tok):
            return "[secret]"
        return tok

    new = re.sub(r"(?<![\w/.\-])[A-Za-z0-9_\-+=]{32,}(?![\w/.])", _entropy, text)
    if new != text:
        n += 1
    return new, n


def _is_checksum(tok):
    return bool(re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{56}|[0-9a-f]{64}|[0-9a-f]{96}|[0-9a-f]{128}", tok, re.I))


def _is_sequence(tok):
    return bool(re.fullmatch(r"[ACGTUNacgtun\-.]+", tok))


# -----------------------------------------------------------------------------------------------
# personal details and sequences inside text
# -----------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_NHS = re.compile(r"(?<![\d.:/\-])(\d{3})[ \-]?(\d{3})[ \-]?(\d{4})(?![\d.:/\-])")
_UK_PHONE = re.compile(r"(?<![\w+])(?:\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}(?!\d)|(?<![\w+])(?:\+44\s?|0)(?:1\d{2,4}|2\d)\)?\s?\d{3,4}\s?\d{3,4}(?!\d)")
_NI = re.compile(r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")
_SEQ_IN_TEXT = re.compile(r"(?<![A-Za-z])[ACGTUNacgtun]{40,}(?![A-Za-z])")
_PROT_IN_TEXT = re.compile(r"(?<![A-Za-z])[ACDEFGHIKLMNPQRSTVWY]{50,}(?![A-Za-z])")
_BLOB = re.compile(r"(?<![\w/+=])[A-Za-z0-9+/]{48,}={0,2}(?![\w/+=])")

# Adapters and primers that are not data (they appear in commands such as cutadapt -a ...)
_COMMON_ADAPTERS = {
    "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA",
    "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT",
    "CTGTCTCTTATACACATCTCCGAGCCCACGAGAC",
    "CTGTCTCTTATACACATCTGACGCTGCCGACGA",
    "AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTCTTCCGATCT",
}


def _nhs_valid(digits):
    if len(digits) != 10 or len(set(digits)) == 1:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1)))
    check = 11 - total % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(digits[9])


def redact_personal(text, strict=False):
    if not text:
        return text, 0
    n = 0
    text, k = _EMAIL.subn("[e-mail]", text)
    n += k

    def nhs(m):
        digits = "".join(m.groups())
        return "[NHS number]" if _nhs_valid(digits) else m.group(0)

    new = _NHS.sub(nhs, text)
    n += new != text
    text = new
    text, k = _UK_PHONE.subn("[phone number]", text)
    n += k
    text, k = _NI.subn("[NI number]", text)
    n += k

    def quoted_row(m):
        body = m.group(2)
        if (body.count(",") >= 2 and ", " not in body) or body.count("\t") >= 2 or body.count("\\t") >= 2:
            return m.group(1) + "[data]" + m.group(1)
        return m.group(0)

    new = re.sub(r"([\"'])([^\"'\n]{5,400})\1", quoted_row, text)
    n += new != text
    text = new

    def seq(m):
        s = m.group(0)
        return s if s.upper() in _COMMON_ADAPTERS else "[sequence]"

    text, k = _SEQ_IN_TEXT.subn(seq, text)
    n += k
    text, k = _PROT_IN_TEXT.subn("[protein sequence]", text)
    n += k

    def blob(m):
        s = m.group(0)
        return s if _is_checksum(s) or "/" in s.strip("/") and s.count("/") > 2 and not re.search(r"[+=]", s) else "[encoded data]"

    text, k = _BLOB.subn(blob, text)
    n += k
    if strict:
        short = re.compile(r"[ACGTUNacgtun]{20,}")
        text, k = short.subn("[sequence]", text)
        n += k
    return text, n


# -----------------------------------------------------------------------------------------------
# who and where
# -----------------------------------------------------------------------------------------------


def _identity():
    home = os.path.expanduser("~")
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = ""
    return home, user, host


_IDENT = None


def mask_identity(text):
    """The student's home folder becomes ~, their user name 'user', the computer's name 'host'."""
    global _IDENT
    if not text:
        return text
    if _IDENT is None:
        _IDENT = _identity()
    home, user, host = _IDENT
    if home and len(home) > 1:
        text = re.sub(r"(?<![\w/.\-~])" + re.escape(home) + r"(?=/|\s|$|['\":;,)])", "~", text)
    if user and len(user) >= 3 and user not in ("root", "user", "jovyan", "student", "ubuntu"):
        text = re.sub(r"(?<![\w.\-])" + re.escape(user) + r"(?![\w\-])", "user", text)
    if host and len(host) >= 3 and host.lower() not in ("localhost", "host"):
        text = re.sub(r"(?<![\w.\-])" + re.escape(host) + r"(?![\w\-])", "host", text)
    return text


def clean(text, strict=False):
    """Secrets, personal details and identity out of any text that is going to the AI."""
    if not text:
        return text, 0
    text, a = redact_secrets(text)
    text, b = redact_personal(text, strict=strict)
    return mask_identity(text), a + b


def clean_question(text, strict=False):
    """A student's question, ready for the AI: (text, details removed, data lines withheld).

    Text pasted into a question goes through the data filter first, while its shape is intact
    (four FASTQ lines are recognisable as a read; after redaction they are not), then through clean().
    """
    text = (text or "").strip()
    withheld = 0
    if text.count("\n") >= 2:
        fr = filter_output(text, strict=strict)
        text, withheld = fr.text, fr.withheld
    text, n = clean(text, strict=strict)
    return text, n, withheld


# -----------------------------------------------------------------------------------------------
# the data filter for output
# -----------------------------------------------------------------------------------------------

_NUM = re.compile(r"^[-+]?(?:\d{1,3}(?:,\d{3})+|\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?%?$")
_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]*[A-Za-z]$")
_STRIP = "()[]{}<>\"'`,;:.!?*"
_SEQ_LINE = re.compile(r"^[ACGTUNRYKMSWBDHVacgtunrykmswbdhv.\-*]{20,}$")
_PROT_LINE = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYXBZJUO*\-]{30,}$")
_QUAL_LINE = re.compile(r"^[!-~]{12,}$")
_LS_L = re.compile(r"^[-dlcbpsD][rwxsStT\-]{9}[.+@]?\s+\d+\s")
_WC = re.compile(r"^\s*\d+(?:\s+\d+){0,3}\s+\S+\s*$")
_SUM = re.compile(r"^[0-9a-fA-F]{32,128}\s+\*?\S")
_DU = re.compile(r"^\s*[\d.,]+[KMGTPkB]?i?B?\s+\S+\s*$")
_FLAGSTAT = re.compile(r"^\d+ \+ \d+ [a-z]")
_SN = re.compile(r"^SN\t")
_KV = re.compile(r"^\s*\"?([A-Za-z][A-Za-z0-9 _./()%#\-]{1,60})\"?\s*[:=]\s*(.*)$")
_LOGTAG = re.compile(r"^\s*(\[(?:[A-Za-z][\w:.*\-/]{0,60}|\d{4}-\d\d-\d\d[ T][\d:.,]+)\]|(?:INFO|WARN|WARNING|ERROR|Error|Warning|FATAL|DEBUG|NOTE|Note|Traceback|Exception|Usage|usage)\b:?)")
_OPTION_HELP = re.compile(r"^\s*--?[A-Za-z0-9@?][\w\-]*[,=]?(\s|$)")
_CODE = re.compile(r"^\s*(?:[A-Za-z_][\w.]*(?:\[[^\]]*\])?\s*(?:[-+*/]?=|<-)\s*\S|(?:def|class|import|from|return|for|while|if|elif|else|try|except|with|raise|assert|lambda|library|function|echo|printf|export|local|set)\b|[A-Za-z_][\w.]*\(.*\)\s*:?\s*$|\s*#|%|!)")
_SAM_HEADER = re.compile(r"^@(HD|SQ|RG|PG|CO)\t")
_VCF_META_KEEP = re.compile(r"^##(fileformat|INFO=<ID=|FORMAT=<ID=|FILTER=<ID=|ALT=<ID=|source=)")
_PATHLIKE = re.compile(r"^[\w.\-~/]*(/|\.[A-Za-z0-9]{1,8}(\.gz|\.bz2|\.xz|\.zst)?)$")
# a path without an extension (/usr/bin/bwa, results/sample1): its parts are names, mostly with lower-case
# letters - unlike a quality string, which may hold slashes too (/AAA/EEEE)
_SLASH_PATH = re.compile(r"^(?:~|\.{1,2})?/?[\w.\-+@]+(?:/[\w.\-+@]+)*/?$")


def _tokens(line):
    return line.split()


def _core(tok):
    return tok.strip(_STRIP)


def _is_word(tok):
    c = _core(tok)
    if not _WORD.match(c):
        return False
    # a run of A/C/G/T letters is a sequence, not a word, from 8 letters on
    return not (len(c) >= 8 and re.fullmatch(r"[ACGTUNacgtun]+", c))


def _is_num(tok):
    c = _core(tok)
    return bool(c) and bool(_NUM.match(c))


def _textlike(s):
    """Mostly words: prose, messages, help text."""
    toks = _tokens(s)
    if not toks:
        return True
    words = sum(1 for t in toks if _is_word(t))
    if len(toks) <= 2:
        return words == len(toks)
    return words / float(len(toks)) >= 0.5


def _numeric_heavy(s):
    toks = _tokens(s)
    if len(toks) < 3:
        return False
    nums = sum(1 for t in toks if _is_num(t) or re.fullmatch(r"[0-9a-fA-F]{2,8}:?", _core(t) or "x"))
    return nums / float(len(toks)) >= 0.5


def _csv_row(line):
    if line.count(",") < 2 or ", " in line:
        return False
    fields = line.split(",")
    return len(fields) >= 3 and all(len(f) <= 120 for f in fields)


def _fields(line):
    if line.count("\t") >= 2:
        return line.rstrip("\n").split("\t"), "\t"
    if _csv_row(line):
        return line.split(","), ","
    if line.count(";") >= 2 and "; " not in line and " " not in line.strip():
        return line.split(";"), ";"
    if line.count("|") >= 2 and " | " not in line and " " not in line.strip():
        return line.split("|"), "|"
    return None, None


def _name_like(field):
    f = field.strip().strip("\"'")
    return bool(f) and len(f) <= 40 and not _is_num(f) and not _SEQ_LINE.match(f) and bool(re.match(r"^[A-Za-z_#.][\w .()%/\-#]*$", f))


def _codecall(s):
    """A line of code with a call or an index: df['x'].mean(), print(n), x[0] = f(y)."""
    return bool(re.match(r"^[A-Za-z_][\w.]*(?:\[[^\]]*\])*(?:\.[A-Za-z_]\w*)*(?:\(.*\))?(?:\[[^\]]*\])*\s*(?:[-+*/]?=.*)?$", s)) and bool(re.search(r"[a-z]{2}", s)) and bool(re.search(r"[(\[=]", s))


def _pathish(s):
    if len(s) > 200:
        return False
    if _PATHLIKE.match(s):
        return True
    if "/" in s and _SLASH_PATH.match(s):
        parts = [x for x in s.split("/") if x and x not in ("~", ".", "..")]
        return bool(parts) and sum(1 for x in parts if re.search(r"[a-z]", x)) >= max(1, 0.6 * len(parts))
    return False


def _keep_special(s, strict):
    """Output that is not data although it is not prose: listings, counts, checksums, summaries,
    log lines, code. In strict mode only counts, checksums and log/error lines."""
    if _FLAGSTAT.match(s) or _SUM.match(s):
        return True
    if _TIMESTAMP.match(s) or _PROGRESS.match(s) or _VERSION_LINE.match(s) or _HEAD_TAIL.match(s):
        return True
    if _WC.match(s) and not all(_is_num(t) for t in _tokens(s)):
        label = _tokens(s)[-1]
        return label == "total" or _pathish(label)
    if _LS_L.match(s) or (_DU.match(s) and (re.match(r"^\s*[\d.,]+[KMGTPk]", s) or _pathish(_tokens(s)[-1]))):
        return not strict
    if _OPTION_HELP.match(s) and not _numeric_heavy(s):
        return True
    m = _LOGTAG.match(s)
    if m:
        rest = s[m.end():]
        return _textlike(rest) or (not _numeric_heavy(rest) and len(_tokens(rest)) <= 8)
    m = _KV.match(s)
    if m and _keylike(m.group(1)) and len(_tokens(m.group(2))) <= 6 and not _numeric_heavy(m.group(2)):
        return not strict
    if _kv_summary(s):
        return not strict
    if _CODE.match(s) and not _numeric_heavy(s):
        return True
    if _commandlike(s):
        return True
    return False


_KV_PAIR = re.compile(r"\s*([A-Za-z][A-Za-z0-9 _./%#\-]{0,40}?)\s*[:=]\s*([-+]?\d[\d.,]*\s*%?(?:\s*\([^()]{0,24}\))?)\s*[,;|]?")


def _kv_summary(s):
    """A summary line with a few counts: "Total reads: 1000  Mapped: 950 (95.00%)"."""
    pos, pairs = 0, 0
    while pos < len(s):
        m = _KV_PAIR.match(s, pos)
        if not m or m.end() == pos or not _keylike(m.group(1)):
            return False
        pairs += 1
        pos = m.end()
    return 2 <= pairs <= 8


def _keylike(key):
    """A key of a summary line: words, possibly joined by _ . - or spaces (total_reads, Q20 bases)."""
    parts = [p for p in re.split(r"[\s_.\-/()]+", key) if p]
    if not parts:
        return False
    words = sum(1 for p in parts if _WORD.match(p) or re.fullmatch(r"[A-Za-z]\d{1,3}", p))
    return words / float(len(parts)) >= 0.5


_OPTION = re.compile(r"^--?[A-Za-z][\w\-]*=?")


_SHELL_OPS = re.compile(r"\s(?:\|\|?|&&|;|>>?|<|2>>?|2>&1|&>)\s")


def _commandlike(s):
    """A command line (echoed by set -x, or written in a log): a program name and options, or a program
    whose output is piped or redirected (Snakemake's echo of a rule's shell command)."""
    toks = _tokens(s.lstrip("+$> "))
    if len(toks) < 2 or not re.match(r"^[A-Za-z_][\w.\-]*$", toks[0]):
        return False
    return (any(_OPTION.match(t) for t in toks[1:]) or toks[0] in ("cd", "mkdir", "cp", "mv", "rm", "cat", "for", "while", "if", "then", "do", "done", "fi")
            or bool(_SHELL_OPS.search(" %s " % s.strip())))


# lines that are not data although they hold numbers: a date and time on its own (Snakemake writes one
# before each job), progress ("3 of 7 steps (43%) done"), a version ("Python version: 3.13.1 (main, ...)")
_TIMESTAMP = re.compile(
    r"^\[?\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?"
    r"\s+(?:\d{4}\s+)?\d{1,2}:\d{2}(?::\d{2})?(?:\s+\d{4})?\s*\]?$"
    r"|^\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\]?$")
_PROGRESS = re.compile(r"^\s*\d+\s+of\s+\d+\s+(?:steps?|jobs?|tasks?|files?|samples?|chunks?)\b.*\bdone\b", re.I)
_VERSION_LINE = re.compile(r"^\s*[\w .()/-]{0,40}\bversion\b[\w .()/-]{0,20}:\s*\S.{0,150}$", re.I)
_HEAD_TAIL = re.compile(r"^==> .{1,300} <==$")  # head and tail name each file they show


def _gaps(line):
    """Character positions of runs of 2+ spaces inside the line (column separators)."""
    body = line.rstrip()
    start = len(body) - len(body.lstrip())
    return set(i for m in re.finditer(r"  +", body[start:]) for i in range(m.start() + start, m.end() + start))


def _aligned_blocks(lines, labels):
    """Blocks of 3+ lines laid out in columns (pandas/R prints, column -t): their rows are data."""
    n = len(lines)
    i = 0
    while i < n:
        j = i
        common = None
        while j < n and lines[j].strip() and len(_tokens(lines[j])) >= 2:
            g = _gaps(lines[j])
            if not g:
                break
            nc = g if common is None else (common & g)
            if common is not None and len(_runs(nc)) < 1:
                break
            common = nc
            j += 1
        if j - i >= 3 and common is not None and len(_runs(common)) >= 1:
            rows = range(i, j)
            texty = sum(1 for k in rows if _textlike(lines[k].strip()))
            indexed = _incrementing_index(lines[i + 1:j]) or _incrementing_index(lines[i:j])
            ends_in_number = sum(1 for k in rows if _is_num(_tokens(lines[k])[-1]))
            if indexed or texty < (j - i) * 0.5 or ends_in_number >= (j - i) * 0.6:
                first = i
                if not indexed and _textlike(lines[i].strip()) and all(_name_like(t) for t in _tokens(lines[i])):
                    first = i + 1  # a header line of column names
                elif indexed and not _incrementing_index(lines[i:i + 2]):
                    first = i + 1  # pandas: the header is the line before the numbered rows
                for k in range(first, j):
                    if labels[k] is None and not _keep_special(lines[k].strip(), False):
                        labels[k] = "table rows"
            i = j
        else:
            i += 1


def _numbered_rows(lines, labels):
    """3+ lines that start 0, 1, 2 ... or 1, 2, 3 ... (or [1], [2] ...) followed by values: printed rows."""
    n = len(lines)
    i = 0
    while i < n:
        j = i
        prev = None
        while j < n:
            toks = _tokens(lines[j])
            if len(toks) < 2:
                break
            m = re.fullmatch(r"\[?(\d+)[\],]?", toks[0])
            if not m or (prev is not None and int(m.group(1)) != prev + 1):
                break
            prev = int(m.group(1))
            j += 1
        if j - i >= 3:
            for k in range(i, j):
                if labels[k] is None:
                    labels[k] = "table rows"
            i = j
        else:
            i += 1


def _runs(positions):
    runs, prev = [], None
    for p in sorted(positions):
        if prev is None or p != prev + 1:
            runs.append(p)
        prev = p
    return runs


def _incrementing_index(lines):
    if len(lines) < 2:
        return False
    try:
        idx = [int(_tokens(ln)[0]) for ln in lines if _tokens(ln) and _tokens(ln)[0].isdigit()]
    except ValueError:
        return False
    if len(idx) < max(2, len(lines) - 1):
        return False
    return all(b - a == 1 for a, b in zip(idx, idx[1:]))


_SEQ_KINDS = ("sequences", "protein sequences", "alignments", "FASTA sequences", "FASTQ reads")
_NT_TOKEN = re.compile(r"^[ACGTUNRYKMSWBDHVacgtunrykmswbdhv.\-*]{20,}$")
_AA_TOKEN = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYXBZJUO*\-.]{30,}$")
_NT_GROUP = re.compile(r"^[ACGTUNacgtun]+$")
_AA_GROUP = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYX*]+$")


def _seq_tokens(s):
    """The kind of sequence data in a line that is not a table row, or None: a sequence among other words
    (an alignment line: "seq1  ACG-TTA...", "Query 1 MKTAY... 60"), or a sequence written in groups
    ("ATG GCC TTA ...", GenBank and EMBL blocks of ten, codons)."""
    toks = s.split()
    for t in toks:
        letters = sum(c.isalpha() for c in t)
        if letters >= 12 and letters >= len(t) * 0.5 and (_NT_TOKEN.match(t) or _AA_TOKEN.match(t)):
            return "alignments" if ("-" in t or "." in t) else "sequences"
    groups = [t for t in toks if not t.isdigit()]
    if len(groups) >= 4:
        if all(_NT_GROUP.match(t) for t in groups) and sum(map(len, groups)) >= 20:
            return "sequences"
        if all(_AA_GROUP.match(t) for t in groups) and sum(map(len, groups)) >= 30:
            lengths = [len(t) for t in groups]
            if max(lengths.count(k) for k in set(lengths) if k >= 5) >= 3 if any(k >= 5 for k in lengths) else False:
                return "protein sequences"
    return None


def _classify(lines, strict):
    """For each line: None (it may go to the AI) or the kind of data it is."""
    n = len(lines)
    labels = [None] * n
    # records that span lines: FASTQ (4 lines) and FASTA (a header and its sequence lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if s.startswith("@") and i + 1 < n and _SEQ_LINE.match(lines[i + 1].strip() or "x"):
            labels[i] = labels[i + 1] = "FASTQ reads"
            k = i + 2
            if k < n and lines[k].startswith("+"):
                labels[k] = "FASTQ reads"
                if k + 1 < n and " " not in lines[k + 1].strip() and _QUAL_LINE.match(lines[k + 1].strip() or "x"):
                    labels[k + 1] = "FASTQ reads"
                    k += 1
                k += 1
            i = k
            continue
        if s.startswith("+") and i + 1 < n and labels[i - 1 if i else 0] == "sequences" and _QUAL_LINE.match(lines[i + 1].strip() or "x"):
            labels[i] = labels[i + 1] = "FASTQ reads"
            i += 2
            continue
        if s.startswith(">") and i + 1 < n and (_SEQ_LINE.match(lines[i + 1].strip() or "x") or _PROT_LINE.match(lines[i + 1].strip() or "x")):
            labels[i] = "FASTA sequences"
            i += 1
            while i < n and lines[i].strip() and not lines[i].lstrip().startswith(">") and re.fullmatch(r"[A-Za-z*\-.]+", lines[i].strip()):
                labels[i] = "FASTA sequences"
                i += 1
            continue
        i += 1
    for i, L in enumerate(lines):
        if labels[i]:
            continue
        s = L.strip()
        if not s:
            continue
        if _SEQ_LINE.match(s):
            labels[i] = "sequences"
        elif _PROT_LINE.match(s):
            labels[i] = "protein sequences"
        elif _SAM_HEADER.match(L):
            labels[i] = "SAM header"
        elif L.startswith("##"):
            if strict or not _VCF_META_KEEP.match(L):
                labels[i] = "VCF header"
        elif L.startswith("#CHROM\t"):
            if strict:
                labels[i] = "VCF header"
        elif L.startswith(">") and not _textlike(s[1:]):
            labels[i] = "FASTA sequences"
        else:
            fields, delim = _fields(L)
            if fields is not None and len(fields) >= 3:
                if _SN.match(L):
                    labels[i] = None if not strict else "summary table"
                elif len(fields) >= 11 and fields[1].isdigit() and fields[3].isdigit():
                    labels[i] = "SAM/BAM alignments"
                elif delim == "\t" and len(fields) >= 8 and fields[1].isdigit() and not fields[0].isdigit():
                    labels[i] = "VCF records"
                elif delim == "\t" and len(fields) == 9 and fields[3].strip().isdigit() and fields[4].strip().isdigit():
                    labels[i] = "intervals (GFF/GTF)"
                elif delim == "\t" and fields[1].strip().isdigit() and fields[2].strip().isdigit():
                    labels[i] = "intervals (BED-like)"
                else:
                    labels[i] = "table rows"
            elif _seq_tokens(s):
                labels[i] = _seq_tokens(s)
            elif _keep_special(s, strict):
                labels[i] = None
            elif _numeric_heavy(s):
                labels[i] = "numbers"
            elif re.fullmatch(r"[\s~^\-=_*+|.:]+", s):
                labels[i] = None  # decoration: Python's ~~~^^^ markers, rules, underlines
            elif " " not in s and _QUAL_LINE.match(s) and sum(1 for c in s if not c.isalnum()) >= len(s) * 0.25 and not _pathish(s) and not _codecall(s):
                labels[i] = "quality strings or encoded data"
            elif _BLOB.fullmatch(s) and not _is_checksum(s):
                labels[i] = "encoded data"
            elif _textlike(s):
                labels[i] = None
            elif len(_tokens(s)) <= 2:
                labels[i] = None  # a single value or name; long lists of them are caught below
            else:
                labels[i] = "values"
    # the middle line of an alignment (BLAST's line of matching residues between Query and Sbjct)
    for i in range(1, n - 1):
        if labels[i] is None and labels[i - 1] in _SEQ_KINDS and labels[i + 1] in _SEQ_KINDS:
            body = lines[i].strip()
            if len(re.sub(r"[^A-Za-z]", "", body)) >= 5 and re.fullmatch(r"[A-Za-z+|:.*\- ]+", body):
                labels[i] = "alignments"
    # tables laid out with spaces (print(df), R data frames, column -t)
    _aligned_blocks(lines, labels)
    _numbered_rows(lines, labels)
    # runs of short, value-like lines (lists of IDs, positions or numbers) are data
    i = 0
    while i < n:
        j = i
        while j < n and labels[j] is None and lines[j].strip() and len(_tokens(lines[j])) <= 2 and not _textlike(lines[j].strip()) and not _pathish(lines[j].strip()) and not _keep_special(lines[j].strip(), strict):
            j += 1
        if j - i >= 5:
            for k in range(i, j):
                labels[k] = "lists of values"
        i = max(j, i + 1)
    # a short piece of sequence next to other data (the end of a wrapped sequence, for example)
    for i in range(n):
        if labels[i] is None and re.fullmatch(r"[ACGTUNacgtun]{4,}", lines[i].strip() or "x") and (
                (i > 0 and labels[i - 1] is not None) or (i + 1 < n and labels[i + 1] is not None)):
            labels[i] = "sequences"
    # the line before table rows: a header of column names may stay (not in strict); a row of values may not
    for i in range(n):
        if labels[i] is not None or not lines[i].strip() or i + 1 >= n:
            continue
        if labels[i + 1] in ("table rows", "numbers", "values", "intervals (BED-like)"):
            fields, delim = _fields(lines[i])
            nfields, ndelim = _fields(lines[i + 1])
            same_shape = fields is not None and ndelim == delim and len(fields) == len(nfields)
            if strict and labels[i + 1] in ("table rows", "numbers"):
                labels[i] = "table header"
            elif same_shape and not all(_name_like(f) for f in fields if f.strip()):
                labels[i] = "table rows"
    if strict:
        # in strict mode only text-like lines (messages, help, errors) and counts stay
        for i in range(n):
            s = lines[i].strip()
            if labels[i] is None and s and not (_textlike(s) or _keep_special(s, True)):
                labels[i] = "values"
    # Snakemake's table of jobs per rule ("Job stats:", "Job counts:" before version 7): rule names and counts
    for i, L in enumerate(lines):
        if L.strip() in ("Job stats:", "Job counts:"):
            j = i + 1
            while j < n and lines[j].strip() and j - i <= 300 and re.fullmatch(r"[\w\s\-.]+", lines[j]):
                labels[j] = None
                j += 1
    return labels


def _redact_line(line, strict):
    line, _ = redact_secrets(line)
    line, _ = redact_personal(line, strict=strict)
    if strict:
        line = re.sub(r"'[^']{1,200}'", "'[value]'", line)
        line = re.sub(r"\"[^\"]{1,200}\"", '"[value]"', line)
    return line


def _vcf_header_line(line):
    cols = line.rstrip("\n").split("\t")
    if len(cols) > 9:
        k = len(cols) - 9
        return "\t".join(cols[:9]) + "\t[%d sample column%s – names withheld]" % (k, "" if k == 1 else "s")
    return line


def _mask_ls_owner(line):
    # -rw-r--r-- 1 owner group size date name: owner and group become "user"
    lead = line[: len(line) - len(line.lstrip())]
    parts = line.split(None, 4)
    if len(parts) == 5 and parts[1].isdigit():
        return lead + " ".join([parts[0], parts[1], "user", "user", parts[4]])
    return line


class FilterResult(object):
    def __init__(self, text, kept, withheld, kinds, total):
        self.text = text  # what the AI gets
        self.kept = kept
        self.withheld = withheld
        self.kinds = kinds  # Counter: kind of data -> lines withheld
        self.total = total  # lines in the original

    def summary(self):
        if not self.withheld:
            return "nothing withheld"
        what = ", ".join(k for k, _ in self.kinds.most_common(3))
        return "%d of %d lines withheld (%s)" % (self.withheld, self.total, what)


def _existing_names(lines, labels, places):
    """Lines that name a file or folder that exists (ls, find and which print names, not data): kept."""
    places = [p for p in places if p]
    for i, ln in enumerate(lines):
        if labels[i] not in ("lists of values", "values", "quality strings or encoded data"):
            continue
        name = ln.strip().rstrip("/*@=|")
        if not name or len(name) > 300 or "\t" in name:
            continue
        try:
            if os.path.isabs(name) or name.startswith("~"):
                found = os.path.lexists(os.path.expanduser(name))
            else:
                found = any(os.path.lexists(os.path.join(p, name)) for p in places)
        except (OSError, ValueError):
            found = False
        if found:
            labels[i] = None


def filter_output(text, kind="default", strict=False, places=()):
    """Filter the output of a command or of code before the AI sees it.

    kind: "default" - classify every line; "help" (help or version text), "code" (a script
    being read) and "summary" (counts, checksums, listings, flagstat ...) are only cleaned of
    secrets and personal details - except in strict mode, where every line is classified.
    places: the folders the command ran in and looked at; a line that names a file or folder
    there is a file name, not data (not in strict mode, which withholds file names).
    """
    text = ANSI.sub("", text or "")
    # progress bars: keep what a terminal finally shows on each line
    lines = [ln.split("\r")[-1] for ln in text.replace("\r\n", "\n").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if kind in ("help", "code", "summary") and not strict:
        out = [mask_identity(_redact_line(ln, strict)) for ln in lines]
        return FilterResult("\n".join(out), total, 0, Counter(), total)
    if kind == "help" and strict:
        labels = [None if (_textlike(ln.strip()) or not ln.strip() or _keep_special(ln.strip(), True) or ln.lstrip().startswith("-")) else "values" for ln in lines]
    else:
        labels = _classify(lines, strict)
        if places and not strict:
            _existing_names(lines, labels, places)
    vcf_meta = 0
    for i, ln in enumerate(lines):
        if labels[i] is None and ln.startswith("##"):
            vcf_meta += 1
            if vcf_meta > 25:
                labels[i] = "VCF header"
    out, kinds, withheld = [], Counter(), 0
    i = 0
    while i < total:
        if labels[i] is None:
            ln = lines[i]
            if ln.startswith("#CHROM\t"):
                ln = _vcf_header_line(ln)
            if _LS_L.match(ln.strip()):
                ln = _mask_ls_owner(ln)
            out.append(mask_identity(_redact_line(ln, strict)))
            i += 1
            continue
        j = i
        run = Counter()
        while j < total and (labels[j] is not None or (not lines[j].strip() and j + 1 < total and labels[j + 1] is not None)):
            if labels[j]:
                run[labels[j]] += 1
            j += 1
        count = j - i
        withheld += count
        kinds.update(run)
        what = ", ".join(k for k, _ in run.most_common(2)) or "data"
        out.append("[AskTim!: %d line%s withheld – %s like data (%s); the student can see %s]" % (count, "" if count == 1 else "s", "it looks" if count == 1 else "they look", what, "it" if count == 1 else "them"))
        i = j
    return FilterResult("\n".join(out), total - withheld, withheld, kinds, total)


_TRIPLE = re.compile(r'("""|\'\'\')(.*?)(\1)', re.S)
_ONE_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_NUM_LIST = re.compile(r"([\[(]\s*)((?:%s\s*,\s*){7,}%s)(\s*,?\s*[\])])" % (_ONE_NUMBER, _ONE_NUMBER))


def filter_code(code, strict=False):
    """Code from a notebook cell, with the data that can be typed into code withheld: a text block in a
    string (a CSV pasted between triple quotes) goes through the data filter, and a literal list of
    eight or more numbers is shortened. (text, lines or lists withheld)"""
    if not code:
        return code, 0
    count = [0]

    def block(m):
        body = m.group(2)
        if body.count("\n") < 2:
            return m.group(0)
        fr = filter_output(body, kind="default", strict=strict)
        count[0] += fr.withheld
        return m.group(1) + fr.text + m.group(3)

    def nums(m):
        count[0] += 1
        n = m.group(2).count(",") + 1
        return "%s...AskTim!: %d numbers withheld...%s" % (m.group(1), n, m.group(3))

    code = _TRIPLE.sub(block, code)
    code = _NUM_LIST.sub(nums, code)
    return code, count[0]


def shorten(text, limit):
    """Keep the beginning and the end of long text (the end matters most for errors)."""
    if text is None or len(text) <= limit:
        return text
    head = int(limit * 0.3)
    tail = limit - head
    cut = len(text) - head - tail
    return "%s\n[AskTim!: %d characters left out here]\n%s" % (text[:head], cut, text[-tail:])
