"""Snakemake under AskTim!: what the checks make of its options, profiles and Snakefiles, what reaches the
AI of its log, and (when Snakemake is installed) a whole agent run that builds and runs a workflow."""

import glob
import json
import os
import shutil
import stat
import sys

import pytest

from asktim import privacy, safety
from asktim import snakefile as smk

GOOD = '''SAMPLES = ["reads_R1"]

rule all:
    input:
        expand("results/{sample}.flagstat.txt", sample=SAMPLES)

rule copy_reference:
    input:
        "../../data/ref.fa"
    output:
        "ref/ref.fa"
    shell:
        "cp {input} {output}"

rule index:
    input:
        "ref/ref.fa"
    output:
        multiext("ref/ref.fa", ".amb", ".ann", ".bwt", ".pac", ".sa")
    shell:
        "bwa index {input}"

rule map:
    input:
        ref="ref/ref.fa",
        idx="ref/ref.fa.bwt",
        reads="../../data/{sample}.fastq"
    output:
        "results/{sample}.bam"
    threads: 1
    shell:
        "bwa mem -t {threads} {input.ref} {input.reads} | samtools sort -o {output} -"

rule flagstat:
    input:
        "results/{sample}.bam"
    output:
        "results/{sample}.flagstat.txt"
    shell:
        "samtools flagstat {input} > {output}"
'''


@pytest.fixture
def run_dir(project, monkeypatch, tmp_path):
    """The agent's folder, with a stand-in `snakemake` on the PATH (the checks refuse programs that are not installed)."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name in ("snakemake", "bwa", "samtools"):
        if name == "snakemake" or not shutil.which(name):
            f = bin_dir / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    d = project / "asktim-runs" / "1-auto"
    d.mkdir(parents=True)
    return d


def level(cmd, run_dir, **kw):
    v = safety.check(cmd, str(run_dir), student_dir=str(run_dir.parent.parent), **kw)
    return v.level, " | ".join(v.reasons)


def test_reading_a_snakefile():
    wf = smk.parse(GOOD + '''
include: "rules/more.smk"
onsuccess:
    shell("echo finished")

rule tricky:
    input:
        reads=expand("data/{s}.fq", s=SAMPLES),
        ref="ref.fa",
    output: temp("out/{s}.txt"), log2="logs/x.txt"
    params: extra="-k 2"
    log: "logs/{s}.log"
    shell:
        """
        bowtie2-build {input.ref} idx {params.extra} > {log} 2>&1
        awk '{{print $1}}' {input.ref} > {output[0]}
        """
''')
    names = [r.name for r in wf.rules]
    assert names == ["all", "copy_reference", "index", "map", "flagstat", "tricky"]
    assert wf.includes == ["rules/more.smk"] and 'SAMPLES = ["reads_R1"]' in wf.top_code
    assert smk.shell_calls(wf.top_code) == ["echo finished"]
    m = wf.rules[3]
    assert smk.fill(m.shell, m) == "bwa mem -t 1 ref/ref.fa ../../data/{sample}.fastq | samtools sort -o results/{sample}.bam -"
    assert [p for _n, p in wf.rules[2].outputs][:2] == ["ref/ref.fa.amb", "ref/ref.fa.ann"]
    t = wf.rules[5]
    cmd = smk.fill(t.shell, t)
    assert "bowtie2-build ref.fa idx -k 2 > logs/{s}.log 2>&1" in cmd and "awk '{print $1}' ref.fa > out/{s}.txt" in cmd
    assert ("log2", "logs/x.txt") in t.outputs


def test_snakemake_options(run_dir):
    (run_dir / "Snakefile").write_text(GOOD)
    for cmd in ["snakemake --cores 2", "snakemake -n --cores 2", "snakemake -np --cores 1", "snakemake --sdm env-modules --cores 1",
                "snakemake -e local --cores 1", "snakemake --report report.html", "snakemake --dag | dot -Tpng > dag.png", "snakemake --version"]:
        assert level(cmd, run_dir)[0] == "ok", cmd
    for cmd in ["snakemake --use-conda --cores 1", "snakemake --sdm conda --cores 1", "snakemake --software-deployment-method apptainer --cores 1",
                "snakemake --use-singularity --cores 1", "snakemake --directory ../.. --cores 1", "snakemake --default-storage-provider s3 --cores 1"]:
        assert level(cmd, run_dir)[0] == "block", cmd
    for cmd in ["snakemake --executor slurm --jobs 10", "snakemake --cluster sbatch --jobs 4", "snakemake --delete-all-output --cores 1",
                "snakemake --profile nowhere --cores 1"]:
        assert level(cmd, run_dir)[0] == "confirm", cmd


def test_profiles_are_read(run_dir):
    (run_dir / "Snakefile").write_text(GOOD)
    prof = run_dir / "profiles" / "default"
    prof.mkdir(parents=True)
    (prof / "config.yaml").write_text("use-conda: true\ncores: 2\n")
    lv, why = level("snakemake --cores 1", run_dir)
    assert lv == "block" and "profile default" in why
    assert level("snakemake --workflow-profile none --cores 1", run_dir)[0] == "ok"
    (prof / "config.yaml").write_text("executor: slurm\njobs: 10\n")
    assert level("snakemake --cores 1", run_dir)[0] == "confirm"
    (prof / "config.yaml").write_text("cores: 2\nuse-conda: false\nprintshellcmds: true\n")
    assert level("snakemake --cores 1", run_dir)[0] == "ok"


def test_what_the_rules_would_do(run_dir):
    bad = '''import shutil
shutil.rmtree("../../data")

rule index_in_place:
    input: "../../data/ref.fa"
    output: "x.txt"
    shell: "bwa index {input} && touch {output}"

rule overwrite:
    output: "../../data/reads_R1.fastq"
    shell: "echo hi > {output}"

rule wrapped:
    output: "y.bam"
    wrapper: "v3.0.0/bio/bwa/mem"

rule install:
    output: "z.txt"
    shell: "pip install pysam && touch {output}"

rule python_run:
    output: "w.txt"
    run:
        import requests
        requests.get("https://example.org")
'''
    (run_dir / "Snakefile").write_text(bad)
    lv, why = level("snakemake --cores 1", run_dir)
    assert lv == "block"
    for part in ["the Python code deletes files", "rule index_in_place: bwa index writes its index next to", "rule overwrite: its output would write outside",
                 "rule wrapped: it uses a Snakemake wrapper", "rule install: it installs", "rule python_run: the code uses the internet"]:
        assert part in why, part
    # an included file is read too, and so is a Snakefile given with -s
    (run_dir / "Snakefile").write_text('include: "rules/extra.smk"\n')
    (run_dir / "rules").mkdir()
    (run_dir / "rules" / "extra.smk").write_text('rule r:\n    output: "../../data/new.txt"\n    shell: "touch {output}"\n')
    assert level("snakemake --cores 1", run_dir)[0] == "block"
    (run_dir / "other.smk").write_text(GOOD)
    assert level("snakemake -s other.smk --cores 1", run_dir)[0] == "ok"


def test_a_snakefile_is_checked_when_it_is_written(run_dir):
    v, _p = safety.check_file_write("Snakefile", str(run_dir), GOOD)
    assert v.level == "ok" and not v.notes
    v, _p = safety.check_file_write("Snakefile", str(run_dir), 'rule x:\n    output: "out.txt"\n    shell: "conda install -y bwa && touch {output}"\n')
    assert v.level == "ok" and "the workflow would be refused" in v.notes[0]


SNAKEMAKE_LOG = """Assuming unrestricted shared filesystem usage.

SNAKEMAKE
=========
  Date: 2026-11-26 20:49:13
  Snakemake version: 9.27.2
  Python version: 3.13.18 (main, Oct 26 2026, 14:08:19) [Clang 23.1.3 ]
  Command: snakemake --cores 2

Building DAG of jobs...
Job stats:
job               count
--------------  -------
copy_reference        1
index                 1
map                   2
flagstat              2
all                   1
total                 7

Select jobs to execute...
Execute 1 jobs...

[Thu Nov 26 20:49:13 2026]
localrule map:
    input: ref/ref.fa, ../../data/reads_R1.fastq
    output: results/reads_R1.bam
    jobid: 2
    wildcards: sample=reads_R1
    threads: 2
[Thu Nov 26 20:49:14 2026]
Finished jobid: 2 (Rule: map)
3 of 7 steps (43%) done
Error in rule failing:
    shell:
        samtools view nonexistent.bam > out/result.txt
        (command exited with non-zero exit code)
"""


def test_snakemakes_log_reaches_the_ai():
    fr = privacy.filter_output(SNAKEMAKE_LOG)
    assert fr.withheld == 0, fr.text
    for part in ["Python version: 3.13.18", "map                   2", "[Thu Nov 26 20:49:13 2026]", "3 of 7 steps (43%) done",
                 "samtools view nonexistent.bam > out/result.txt"]:
        assert part in fr.text
    # data among it is still withheld
    mixed = SNAKEMAKE_LOG + "==> data/reads_R1.fastq <==\n@r1\n%s\n+\n%s\n" % ("ACGT" * 19, "F" * 76)
    fr = privacy.filter_output(mixed)
    assert "ACGTACGTACGT" not in fr.text and "==> data/reads_R1.fastq <==" in fr.text


@pytest.mark.skipif(not (shutil.which("snakemake") and shutil.which("bwa") and shutil.which("samtools")), reason="needs snakemake, bwa and samtools")
def test_the_agent_builds_and_runs_a_workflow(project, env, mock):
    from conftest import run_asktim

    mock.queue = [
        {"text": "A Snakemake workflow.", "tools": [("write_file", {"path": "Snakefile", "content": GOOD, "why": "The workflow."})]},
        {"tools": [("run_command", {"command": "snakemake -n --cores 1", "why": "A dry run."})]},
        {"tools": [("run_command", {"command": "snakemake --cores 1", "why": "Run it."})]},
        {"tools": [("run_command", {"command": "snakemake --use-conda --cores 1", "why": "Must be refused."})]},
        {"tools": [("run_command", {"command": "head -n 8 results/*.flagstat.txt ref/ref.fa", "why": "Read the summary."})]},
        {"text": "The workflow ran; the summary is in results/reads_R1.flagstat.txt."},
    ]
    p = run_asktim(["Agent", "--auto", "make a snakemake workflow that maps the reads"], env, project, timeout=300)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    run = sorted(glob.glob(str(project / "asktim-runs" / "*")))[-1]
    assert os.path.exists(os.path.join(run, "results", "reads_R1.flagstat.txt")), p.stdout[-4000:]
    results = [b["content"] for m in mock.requests[-1]["body"]["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert "steps (100%) done" in results[2] and "withheld" not in results[2]
    assert "Not run: AskTim! does not allow" in results[3] and "--use-conda" in results[3]
    assert "==> results/reads_R1.flagstat.txt <==" in results[4] and "mapped (" in results[4]
    assert "==> ref/ref.fa <==" in results[4] and "FASTA sequences" in results[4]  # the reference itself is withheld
    reads = [ln for ln in (project / "data" / "reads_R1.fastq").read_text().split("\n")[1::4] if ln]
    sent = mock.all_text_sent()
    assert not [s for s in reads[:100] if s in sent], "reads reached the AI"
    rerun = open(os.path.join(run, "rerun.sh")).read()
    assert "snakemake --cores 1" in rerun and "cat > Snakefile <<'ASKTIM_EOF'" in rerun
