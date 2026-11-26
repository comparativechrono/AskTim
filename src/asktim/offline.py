"""Help without an AI: what common error messages and exit statuses mean.

Used when no key is set up, when the AI service cannot be reached, and with --offline.
The explanations are adapted from the offline help of the bioinformatics workbench.
"""

import re

SHELL_HELP = [
    (r"command not found|not found$|: not found", "The shell has no program of that name. Check the spelling (names are case-sensitive), "
     "and whether it is installed: `command -v NAME` prints its path if it is. `AskTim! tools` lists what is installed here. "
     "A script in the current folder runs as `bash script.sh` or `./script.sh`; on a cluster you may need `module load NAME` first."),
    (r"No such file or directory|cannot open|[Cc]ould not open|[Ff]ailed to open|does not exist|cannot access", "A file or folder in the command "
     "is not where the command looked. Check the folder you are in (`pwd`) and what is there (`ls`). A path without a leading `/` or `~` "
     "starts from the current folder; `~` inside quotes is not your home folder."),
    (r"Permission denied|Read-only file system|Operation not permitted", "That file or folder cannot be changed or run by you. To run a script, "
     "use `bash script.sh` or make it executable (`chmod +x script.sh`). Shared course data is usually read-only: copy what you need into "
     "your own folder and work on the copy."),
    (r"unrecognized option|unrecognised option|invalid option|unknown option|illegal option|unknown argument|no such option", "The program does "
     "not have that option (in this version). Check its real options with `PROGRAM --help` or `man PROGRAM` - AI tools often invent options "
     "that sound right."),
    (r"syntax error|unexpected token|unexpected EOF|unexpected end of file|unterminated", "The shell could not read the command: look for an "
     "unmatched quote or bracket, a `do` without `done`, or an `if` without `fi`. Characters such as `(`, `)`, `;`, `&`, `|`, `<`, `>` must "
     "be quoted when they are part of a name."),
    (r"Is a directory", "The command expected a file but got a folder. Give the path of a file in it, or use the option for folders (`cp -r`, `rm -r`)."),
    (r"[Nn]ot a directory", "Part of the path is a file, not a folder."),
    (r"out of memory|Cannot allocate memory|MemoryError|std::bad_alloc|Killed", "The program ran out of memory. Work on a smaller input "
     "(`seqtk sample`, `head -n 400000 reads.fastq > small.fastq`), one region or chromosome at a time, or ask for more memory "
     "(on a cluster: the --mem option of your job)."),
    (r"truncated file|EOF marker is absent|invalid BGZF|not compressed with bgzip|fail to read the header|unexpected end of file", "An input file "
     "is incomplete or not in the format the program expects. Check it with `file NAME`, `ls -lh` (is it empty?) or `samtools quickcheck`. "
     "A .gz file that is to be indexed must be compressed with `bgzip`, not `gzip`."),
    (r"No space left on device|Disk quota exceeded", "The disk (or your quota) is full. `df -h .` shows the free space and `du -sh *` what is "
     "using it; delete intermediate files you can make again (SAM files are large: keep the BAM)."),
    (r"[Ss]egmentation fault|core dumped", "The program crashed. Often an input is in the wrong format or the reference and the reads do not match; "
     "check the inputs, and try a small test first."),
    (r"Argument list too long", "The wildcard matched too many files. Use `find . -name '*.fq' -print0 | xargs -0 ...` or a loop."),
    (r"[Ii]ndex.*(not found|missing|could not)|could not load index|fail to locate the index", "The index the program needs is missing: build "
     "it first (`bwa index ref.fa`, `samtools faidx ref.fa`, `samtools index aln.bam`, `bowtie2-build ref.fa ref`, `tabix -p vcf x.vcf.gz`)."),
    (r"sort.*(not|un)sorted|must be sorted|is not sorted|unsorted positions", "The input must be sorted by position first: `samtools sort -o sorted.bam in.bam` "
     "(then `samtools index sorted.bam`)."),
    (r"[Cc]onnection (refused|timed out)|Could not resolve host|Name or service not known|Network is unreachable", "The computer could not reach "
     "the server: check the address and your internet connection (clusters' compute nodes often have no internet)."),
]

PY_HELP = {
    "NameError": "Python does not know that name: it is misspelt (names are case-sensitive), or the cell that creates it has not been run since "
    "the kernel started. After a restart, run the cells again from the top.",
    "SyntaxError": "Python could not read the code: look for a missing bracket, quote, comma or colon on the line shown - or just above it.",
    "IndentationError": "Python groups lines by their indentation: the lines inside `for`, `if`, `def` or `with` must all be indented the same (4 spaces).",
    "KeyError": "That key is not in the dictionary - or that column is not in the table. Keys are case-sensitive: print `df.columns` to see what is there.",
    "IndexError": "An index is out of range: a list of 3 items has positions 0, 1 and 2.",
    "TypeError": "A value of the wrong type was used - a number added to a string, or something called that is not a function.",
    "FileNotFoundError": "The file is not where Python looked. `import os; os.getcwd()` shows the folder your code runs in: give the path from there, or the full path.",
    "ModuleNotFoundError": "That package is not installed in this Python. Check the name (Biopython is imported as `Bio`, scikit-learn as `sklearn`); "
    "ask your instructor before installing anything.",
    "ImportError": "The package is there, but that name is not in it (or not in this version): check the spelling and the package's documentation.",
    "AttributeError": "The object has no attribute or method of that name - often a typo, or a method of another kind of object (`type(x)` shows what it is).",
    "ValueError": "The value has the right type but cannot be used - for example `int('NA12878')`, or two lists of different lengths.",
    "ZeroDivisionError": "Something was divided by zero.",
    "PermissionError": "That file cannot be written: save results in your own folders.",
    "MemoryError": "Python ran out of memory. Work on part of the data, or use a format and types that need less (for example `dtype` in pandas).",
    "UnicodeDecodeError": "The file is not text in the encoding Python expected - it may be compressed (`.gz`) or binary (`.bam`), or need `encoding='latin-1'`.",
    "ParserError": "pandas could not read the table: check the separator (`sep='\\t'` for tab-separated files) and whether the file has comment lines (`comment='#'`).",
}

R_HELP = [
    (r"could not find function", "R does not know that function: load its package first (`library(dplyr)`), or check the spelling - names are case-sensitive."),
    (r"object '.*' not found", "R does not know that name: it is misspelt (names are case-sensitive), or the line that creates it has not been run."),
    (r"there is no package called", "That package is not installed in this R. Check the name, and ask your instructor before installing anything."),
    (r"cannot open (file|the connection)", "The file is not where R looked: `getwd()` shows R's working folder; give the path from there, or the full path."),
    (r"unexpected (symbol|string constant|numeric constant|'\)'|'\}'|','|'='|input|end of input)", "R could not read the code: look for a missing or extra bracket, comma, quote or operator on that line, or the line before."),
    (r"non-numeric argument to (binary operator|mathematical function)", "A calculation got text instead of numbers - often a column that was read as text. Look at it with `str(df)`."),
    (r"arguments imply differing number of rows", "The vectors or columns have different lengths, so they cannot form one table."),
    (r"subscript out of bounds|undefined columns selected", "A row, column or element that does not exist was asked for: check `names(df)` and `dim(df)`."),
]

EXIT_HELP = {
    127: "Exit status 127 means the command was not found: a program that is not installed (or not on PATH), or a typo.",
    126: "Exit status 126 means the file was found but could not be run: make it executable (`chmod +x`) or run it with `bash`.",
    130: "Exit status 130 means the command was stopped with Ctrl+C.",
    137: "Exit status 137 means the program was killed - often because it ran out of memory.",
    139: "Exit status 139 means the program crashed (segmentation fault): check that its inputs are in the right format.",
    141: "Exit status 141 means a pipe closed early (for example with `| head`) - usually harmless.",
    2: "Exit status 2 usually means the command was used wrongly, or a file was missing.",
    1: "Exit status 1 is a general error: the program's own message says more.",
}


def explain_text(text):
    """Explanations for an error message (shell, Python or R); [] if nothing matches."""
    out = []
    t = text or ""
    m = re.search(r"\b([A-Z][A-Za-z]*(?:Error|Exception))\b", t)
    if m and m.group(1) in PY_HELP:
        out.append(PY_HELP[m.group(1)])
    for pat, msg in R_HELP if re.search(r"\bError in\b|^Error:|could not find function|object '", t, re.M) else []:
        if re.search(pat, t):
            out.append(msg)
    for pat, msg in SHELL_HELP:
        if re.search(pat, t, re.M) and msg not in out:
            out.append(msg)
    return out[:3]


def explain_command(cmd):
    """For a command from the history: what its exit status means."""
    if cmd is None or cmd.status in (None, 0):
        return []
    return [EXIT_HELP.get(cmd.status, "Exit status %d: the command did not succeed; its own message (above it in your terminal) says why." % cmd.status)]
