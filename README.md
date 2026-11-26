<p align="center">
  <img src="https://raw.githubusercontent.com/comparativechrono/AskTim/main/assets/tim.png" alt="Tim, the AskTim! cartoon: a smiling man with glasses, a beard and a quiff" width="180">
</p>

<h1 align="center">AskTim!</h1>

<p align="center"><strong>An AI helper for learning bioinformatics that keeps your data out of the AI.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/AskTim/"><img src="https://img.shields.io/pypi/v/AskTim" alt="PyPI"></a>
  <a href="https://pypi.org/project/AskTim/"><img src="https://img.shields.io/pypi/pyversions/AskTim" alt="Python versions"></a>
  <a href="https://github.com/comparativechrono/AskTim/actions/workflows/tests.yml"><img src="https://github.com/comparativechrono/AskTim/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
</p>

AskTim! answers questions about the programs and packages installed on your computer, explains
errors, and has an agent that can run the steps of an analysis for you, at the command line and in
Jupyter or Colab notebooks. You use your own API key for Claude, Gemini or an OpenAI-compatible
service.

AskTim! was made by Tim Hearn (University of Cambridge) for teaching students to use AI responsibly.
It works like the AI panels in his practicals (bioinformatics-workbench, R-workbench,
ai-agents-practical, yeast-data-ai, r-training). The AI is told what is installed and what you did.
It gets the shape of your files, but never their contents. The aim is that students never need to
paste data into a chatbot to get help.

```console
$ AskTim! "how do I count the reads in a BAM file that mapped?"
$ samtools sort aln.sam -o aln.bam; AskTim! why
$ AskTim! Agent "map the reads in data/ to ref.fa and tell me how many map"
```

---

## Install

```bash
pip install AskTim
```

AskTim! needs Python 3.8 or newer and nothing else. It installs two names for the same command:
`AskTim!`, and `asktim` for shells and systems that don't like the `!`. To try the latest version
from GitHub instead: `pip install git+https://github.com/comparativechrono/AskTim`.

In Colab, run this at the top of the notebook (once per session):

```python
!pip install -q AskTim
import asktim
```

## Set up a key (once)

```bash
AskTim! Claude                 # then paste your key when asked (it is not shown)
AskTim! Claude sk-ant-...      # also works, but leaves the key in your shell history
AskTim! Gemini                 # or a Google Gemini key
AskTim! OpenAI --url http://localhost:11434/v1     # or an OpenAI-compatible service, e.g. a local model
```

AskTim! checks the key with a tiny request and then keeps it in `~/.config/asktim/keys.json`, a file
only you can read. A key can also come from an environment variable (`ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`/`GOOGLE_API_KEY`, `OPENAI_API_KEY`, or `ASKTIM_CLAUDE_KEY` and so on) or from Colab's
Secrets. `AskTim! status` shows which key and model are in use. `AskTim! forget` deletes stored keys.

## Ask a question

```bash
AskTim! "what does samtools flagstat tell me?"
AskTim! why                                  # why did my last command fail?
bwa mem ref.fa r1.fq 2>&1 | AskTim! "what does this error mean?"
AskTim! --file data/counts.csv "how do I plot column 2 against column 3 in R?"
AskTim!                                      # a conversation; Ctrl+D ends it
```

Chat mode explains and suggests, and never runs anything. AskTim! tells the AI which programs,
Python packages and R packages are installed (`AskTim! tools` lists them). The AI suggests only
those. If something isn't installed, it says so rather than giving you a command that cannot work.
When a question looks like coursework, it gives a hint first. For an error, it starts with what the
message means. AskTim! remembers the conversation for an hour (`AskTim! new` starts afresh).

The last 20 commands you typed are sent with each question, so "why did that fail?" works. To see
which commands failed, AskTim! needs its shell integration (below); without it, it reads your
shell's history file. Use `--no-history` to leave the commands out.

Put questions in quotes. In bash, use single quotes if the question contains `!` or `$`.

To see exactly what would be sent, without sending anything:

```bash
AskTim! show "how do I index this BAM file?"
```

## The agent

```bash
AskTim! Agent "map the reads in data/ to ref.fa with bwa and count how many map"
```

The agent works like the agents in the practicals. It plans, runs commands, writes files and reads
what they print, one step at a time. It can only use what is installed, and it cannot install
anything. Before each step you see what it wants to do and why:

```text
── Step 3 · run a command ─────────────────────────────────────────────
Why: Index the reference so that bwa can map to it.
$ bwa index -p ref ../../ref.fa
Run it? [Enter/y] yes · [n]o · [e]dit · [a]ll (stop asking) · [q]uit:
```

If you say no, it asks why and tells the agent. With `e` you can change the command first. A step
that breaks a rule is refused before you are asked, and the agent is told why.

| Option | |
|---|---|
| *(none)* | approve each step (the default) |
| `--plan` | the agent writes a plan first; you approve it, ask for changes, or approve it step by step |
| `--auto` | runs without asking (every rule still applies; risky steps still ask; Ctrl+C stops) |
| `--script` | builds one script, `analysis.sh` (or `--script-name NAME`), and gets it working |
| `--web` | lets it download things (curl, wget); nothing may be uploaded |
| `--here` | works in this folder, not in a new run folder (asks before it changes any file already there) |
| `--steps N`, `--timeout S` | at most N steps (default 25); at most S seconds per command (default 600) |

Each run gets its own folder, `asktim-runs/1-approve/`, `asktim-runs/2-auto/` and so on. The agent
can read your files, but it can only write in that folder. When it finishes, AskTim! writes (not
the model):

- `RUN.md`: the task, every step with its decision, exit status and output, and what the AI was shown;
- `run.json`: the same, for programs;
- `rerun.sh`: the commands that worked, as a script you can read and run again without any AI.

The agent can also open a file for you. An HTML report (FastQC, MultiQC), an image or a PDF opens
in its usual program. A BAM or VCF file can open in IGV, an alignment in Jalview or AliView, and a
tree in FigTree, if those programs are installed. You are asked every time.

## In a notebook (Jupyter, JupyterLab, Colab, VS Code)

```python
import asktim
```

Then start any cell with `AskTim!`:

```text
AskTim! "how do I read a gzipped FASTA file with Biopython?"
```

```text
AskTim! why
```

```text
AskTim! "why is this so slow?"
for rec in SeqIO.parse(path, "fasta"):
    ...
```

Code under the first line is sent with the question, with any data in it withheld. After a cell
fails, `AskTim! why` explains the error. `AskTim! insert 2` puts code block 2 of the last answer
into a new cell. `%AskTim!` and `%%AskTim!` work too.

`AskTim! Agent "..."` runs the agent in the notebook. It runs shell commands as at the command line,
and it can also run Python in your notebook (you see the code first), look at your variables (their
names, types and shapes, never their values), and add Markdown and code cells.

| | Jupyter Notebook 7, JupyterLab | Colab | VS Code |
|---|---|---|---|
| Questions, `why` | ✓ | ✓ | ✓ |
| Agent: shell and Python steps | ✓ | ✓ | ✓ |
| Agent: new cells | added below; running each brings the next | shown with a Copy button, and saved in `agent-record.ipynb` | as Colab |
| `AskTim! insert N` | new cell | Copy button | Copy button |

Colab doesn't let the kernel add cells, and VS Code doesn't seem to either, so they show Copy
buttons instead.

[`examples/AskTim-demo.ipynb`](https://github.com/comparativechrono/AskTim/blob/main/examples/AskTim-demo.ipynb) is a short walk-through for students that runs in Colab
([open it in Colab](https://colab.research.google.com/github/comparativechrono/AskTim/blob/main/examples/AskTim-demo.ipynb)).

**Keys in Colab.** Store the key in Colab's Secrets (the key icon on the left) as
`ANTHROPIC_API_KEY` (or `GEMINI_API_KEY`, `OPENAI_API_KEY`) and give the notebook access. You can
also type `AskTim! Claude` in a cell and paste the key into the box that appears. Colab forgets a
key stored like that when the runtime stops. If you type a key into a cell, AskTim! removes it from
the notebook's history. In Jupyter it also removes it from the cell; elsewhere it tells you to
delete it before you share the notebook.

## What reaches the AI, and what never does

**Sent with a question:**

- your question;
- the programs and packages that are installed, with their versions;
- your recent commands, with secrets (API keys, tokens, passwords), e-mail addresses, phone and NHS
  numbers and long sequences taken out, and your user name replaced by `user`;
- the names and sizes of the files in the current folder;
- with `--file`, a description of the file's structure: format, size, number of records, and for a
  table the column names and what kind of values each column has, but not the values;
- text you pipe in, with the lines that look like data withheld;
- in a notebook: the code of the cells you ran (tables pasted into code and long lists of numbers
  withheld), your variables' names, types and shapes, and the last error.

**Sent by the agent:** its commands and files (you see them first), and what each command printed,
with every line that looks like data withheld. Text gets through: if the agent reads a README or
your notes, the AI sees them.

**Never sent:** the contents of your data files, your variables' values, your keys, or anything
from `~/.ssh` and other credentials.

The data filter works line by line and recognises data by its shape: FASTQ and FASTA records,
SAM/BAM alignments, VCF records and sample names, BED and GFF intervals, table rows, lists of IDs
or numbers, long sequences, and encoded blobs. Error messages, warnings, help text, versions,
counts, summaries (such as `samtools flagstat`) and file listings get through, because those are
what the AI needs to help. Withheld lines are replaced by a note such as
`[AskTim!: 1204 lines withheld – they look like data (SAM/BAM alignments); the student can see them]`.
You always see the full output yourself.

`--strict`, or `"privacy": "strict"` in the settings, withholds more: column names, file listings,
summaries and quoted values in error messages.

**Limits.** The filter is made for bioinformatics data and works well on it. It recognises data by
its shape, so it can't promise to catch every piece of free text. A person's name quoted in an
error message, or a list of names typed into code, can get through (strict mode replaces quoted
values). An agent that set out to get round the filter, by rewriting data as words for example,
could do so. AskTim! tells it not to, and in the default mode you see every command before it
runs. Don't use AskTim!, or any AI tool, on personal or patient data unless your institution has
approved it. `AskTim! show "..."` lets you check what would be sent.

**The AI services.** What a service does with what it receives is set by its terms, and these
change, so check them before recommending one to a class. When this was written (November 2026):

- Anthropic's [Commercial Terms](https://www.anthropic.com/legal/commercial-terms), which cover API
  keys, say Anthropic may not train models on what is sent.
- Google's [Gemini API terms](https://ai.google.dev/terms) say that on the free tier,
  Google uses what is sent to improve its products. In the EEA, Switzerland and the UK, the
  paid-service terms apply to the free tier too. The terms also require users to be 18 or over, and
  say the API is for professional or business use.

## Safeguards (the agent)

- **Nothing is installed:** pip, conda, mamba, apt, brew, `install.packages`, `BiocManager`, npm and
  the like are refused, and so is `sudo`.
- **It writes only to its run folder** (and a private temporary folder). Your files are never
  changed or deleted. In-place edits (`sed -i`), indexing a reference next to your copy of it, and
  writing outside the folder are all refused. With `--here`, AskTim! asks before any change to a
  file that was already there.
- **A sandbox, where possible.** On Linux with [bubblewrap](https://github.com/containers/bubblewrap)
  (`bwrap`) installed, each command runs in a sandbox. The file system is read-only except for the
  run folder. `/tmp` is private, there is no network unless you used `--web`, and the command can't
  see your other processes. Without bubblewrap, the checks below still apply, and AskTim! reports
  any file that changes outside the agent's folder. But a check can't know every program's options,
  so the sandbox is the stronger safeguard. `AskTim! status` says which you have.
- **No network** unless `--web`. Uploads (`curl -T`, `scp`, `rsync` to another computer, `nc`, posting
  files) are refused even then.
- **No secrets.** Commands run with API keys and tokens removed from the environment. Reading
  `~/.ssh`, `~/.aws`, shell histories or AskTim!'s own keys is refused.
- **What you approve is what runs.** Commands and files with hidden control characters or
  text-direction tricks are refused, and the AI's text is shown with such characters made visible.
  In notebooks, nothing the AI writes reaches the page as HTML or script, and the agent's Python
  may not write HTML or JavaScript of its own into the page.
- **Programs that would hang** are refused: interactive programs, editors, REPLs and windowed
  programs (the agent opens files for you with `open_file` instead).
- **Limits:** by default 25 steps and 600 seconds per command (a command is stopped together with
  everything it started), and output is trimmed before it is filtered.
- **Code is checked too.** Python and R the agent writes or runs is checked for installs, network
  use, deletions and writes outside its folder. Risky code still needs your yes in `--auto`.
- **A record of every run** (`RUN.md`, `run.json`, `rerun.sh`), and a draft statement on your use of
  AI (below).

In a notebook, the agent's Python runs in your kernel with your permissions; only shell commands
can be sandboxed. AskTim! checks the code before it runs, but these checks are static. Read each
Python step before you approve it, as the default mode asks you to. If nobody is there to approve
(nbconvert, papermill), the agent refuses to start unless you used `--auto`.

## Shell integration

```bash
AskTim! setup-shell          # bash, zsh or fish; AskTim! setup-shell --remove undoes it
```

This adds a few lines to your `~/.bashrc`, `~/.zshrc` or fish config. They record each command you
type, where you typed it and whether it worked (never its output) in a file in AskTim!'s folder that
only you can read. Commands that start with a space are left out. In bash and zsh, **Alt+A** then
puts the commands from AskTim!'s last answer on your command line, one at a time, for you to read
and run. In zsh it also lets you type `?` and `*` in unquoted questions.

## Records and your statement on AI use

`AskTim! log` lists your recent questions and agent runs. The log is kept on your computer.
`AskTim! statement` drafts a "Statement on the use of AI" for coursework: what you asked, which
models answered, what was sent, and the agent's tasks with where their records are (each `RUN.md`
lists what was run and what was refused). Use `--since 2026-11-01` to limit it by date, and `--all`
for every folder, not just this one. Read it and edit it; it is a draft, and the statement is yours.

## For instructors

A settings file for a lab or a JupyterHub image, at `/etc/asktim/config.json` (on Windows
`%PROGRAMDATA%\asktim\config.json`, or wherever `ASKTIM_SITE_CONFIG` points), sets the defaults for
everyone. Keys listed under `"locked"` cannot be
changed by students:

```json
{
  "provider": "claude",
  "models": {"claude": "claude-sonnet-5-5"},
  "privacy": "strict",
  "course": "Part II Bioinformatics practical 3: RNA-seq with salmon and DESeq2. Data are in ~/data. Students use R 4.4.",
  "history_lines": 10,
  "agent": {"max_steps": 15, "command_timeout": 300, "allow_network": false, "sandbox": "on"},
  "locked": ["provider", "models", "privacy", "agent.allow_network", "agent.max_steps", "agent.sandbox"]
}
```

`course` is added to what the AI is told, so you can describe the practical, the data and any house
rules. Settings are applied in this order: defaults, the site file, the student's
`~/.config/asktim/config.json`, environment variables (`ASKTIM_PROVIDER`, `ASKTIM_MODEL`,
`ASKTIM_PRIVACY`, `ASKTIM_COURSE`, `ASKTIM_OPENAI_URL`, ...), then command-line options. Locked
values win over everything after the site file. With `"sandbox": "on"`, the agent refuses to run
without a sandbox.

Things to think about before a class:

- **Keys.** Each student can use their own key. You can also give a shared key in an environment
  variable on the lab machines, or point AskTim! at an institutional OpenAI-compatible gateway with
  `"provider": "openai"` and `"openai_base_url"`.
- **Rehearse with a real key.** The tests use stand-in AI services, so run the practical once with
  the real service and model you will use, as the practicals' READMEs recommend.
- **Install bubblewrap** on Linux lab machines (`apt install bubblewrap`) so that the agent is
  sandboxed.
- **Tell students what is sent.** `AskTim! privacy` prints it, and `AskTim! show "..."` lets them
  check for themselves. Checking is part of using AI responsibly.

## Commands

| Command | |
|---|---|
| `AskTim! "question"` | ask (also `AskTim!` alone, for a conversation) |
| `AskTim! why` | explain why the last command failed |
| `AskTim! Agent "task"` | the agent (options above) |
| `AskTim! show "question"` | print what would be sent; send nothing |
| `AskTim! Claude` / `Gemini` / `OpenAI` | store a key (and use that service) |
| `AskTim! use claude\|gemini\|openai` | switch service |
| `AskTim! model NAME` | change the model for the current service |
| `AskTim! status` | key, model, privacy, shell integration and sandbox |
| `AskTim! tools` | what AskTim! found installed |
| `AskTim! privacy` | what is and isn't sent |
| `AskTim! setup-shell` | shell integration (`--remove` to undo) |
| `AskTim! log` / `statement` | your log; a draft AI-use statement |
| `AskTim! new` / `forget` | forget the conversation; delete stored keys |
| `AskTim! insert N`, `AskTim! cells` | notebooks: code block N into a cell; the agent's waiting cells |

Options for questions: `--no-history`, `--history N`, `--no-files`, `--file PATH`, `--new`,
`--model NAME`, `--strict`, `--offline` (the built-in help only, without any AI).

## Files AskTim! keeps

| | Linux, macOS | Windows |
|---|---|---|
| settings and keys | `~/.config/asktim/` (`config.json`, `keys.json`) | `%APPDATA%\asktim\` |
| cache (installed tools, conversation, suggested commands) | `~/.cache/asktim/` | `%LOCALAPPDATA%\asktim\cache\` |
| log and command history | `~/.local/state/asktim/` | `%LOCALAPPDATA%\asktim\state\` |

`ASKTIM_HOME=/some/folder` puts all three under one folder.

## From Python

```python
import asktim
asktim.ask("how do I read a BAM file with pysam?")
asktim.agent("count the reads in data/*.fastq.gz", mode="auto")
```

## Platforms

- **Linux:** everything works. The sandbox needs bubblewrap.
- **macOS:** everything works, but the agent is not sandboxed by default (its commands are still
  checked). `"sandbox": "on"` uses macOS's `sandbox-exec`, which has had less testing.
- **Windows:** AskTim! is written to work there, but it hasn't been tested on Windows yet. Use
  `asktim` (without the `!`). The agent needs bash, so use it in WSL (which works as Linux does) or
  Git Bash.
- **zsh:** quote questions, or run `AskTim! setup-shell`.

## Development

```bash
git clone https://github.com/comparativechrono/AskTim && cd AskTim
pip install -e ".[test]"
pytest
```

The tests use stand-ins for the Claude, Gemini and OpenAI APIs. They check that no read, sequence or
table row from the test data reaches the AI. They run the agent on real tools (bwa and samtools if
installed) and in a real terminal and notebook kernel, and they attack the safeguards.
`tests/markdown_fuzz/` checks the notebook's Markdown sanitiser against marked, the parser
JupyterLab uses: set `ASKTIM_NODE_MODULES` to a `node_modules` folder with `marked` and `jsdom` to
include it.

### Making a release

1. Put the new version in `src/asktim/__init__.py` (`__version__ = "0.2.0"`) and push it.
2. On GitHub, draft a new release with the tag `v0.2.0` (the same version, with a `v`) and publish it.

The release workflow then runs the tests, builds the package, checks that the tag matches the
version, and publishes it to [PyPI](https://pypi.org/project/AskTim/) through PyPI's trusted
publishing, so no PyPI token is stored anywhere.

## Known limitations

- The data filter recognises data by its shape (see above). Free text, such as a name, can get
  through.
- Data typed into notebook code is withheld only when it looks like a table or a list of numbers.
- AskTim! has been tested against stand-ins for the AI services, so rehearse with the real one.
  Model names change: `AskTim! model NAME` sets another.
- In notebooks, the agent's Python steps are checked statically and run with your permissions.
- Colab and VS Code can't receive new cells from the kernel, so they get Copy buttons.
- On macOS the agent is not sandboxed unless the settings ask for it. On Windows the agent needs WSL
  or Git Bash.

## Licence

MIT (see [`LICENSE`](https://github.com/comparativechrono/AskTim/blob/main/LICENSE)).
