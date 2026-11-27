"""What is installed on this computer: programs (with versions), Python and R packages.

The AI is told this list so that it suggests - and, as an agent, uses - only what is there.
Finding versions takes a second or two, so the result is kept in ~/.cache/asktim for 12
hours, or until PATH or the conda environment changes. `AskTim! tools` shows it and
looks again.
"""

import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

from . import paths

# (command, category, how to ask for the version). The version command is run with a short
# timeout; its first line that holds a version number is used.
TOOLS = [
    # quality control and trimming
    ("fastqc", "QC", ["--version"]), ("multiqc", "QC", ["--version"]), ("fastp", "QC", ["--version"]),
    ("cutadapt", "QC", ["--version"]), ("trimmomatic", "QC", ["-version"]), ("trim_galore", "QC", ["--version"]),
    ("seqkit", "QC", ["version"]), ("seqtk", "QC", []), ("NanoPlot", "QC", ["--version"]), ("nanoq", "QC", ["--version"]),
    ("porechop", "QC", ["--version"]), ("filtlong", "QC", ["--version"]), ("chopper", "QC", ["--version"]),
    ("bbduk.sh", "QC", ["--version"]), ("qualimap", "QC", ["--version"]),
    # alignment and mapping
    ("bwa", "mapping", []), ("bwa-mem2", "mapping", ["version"]), ("bowtie2", "mapping", ["--version"]),
    ("bowtie", "mapping", ["--version"]), ("minimap2", "mapping", ["--version"]), ("hisat2", "mapping", ["--version"]),
    ("STAR", "mapping", ["--version"]), ("subread-align", "mapping", ["-v"]), ("blat", "mapping", []),
    ("nucmer", "mapping", ["--version"]), ("lastz", "mapping", ["--version"]),
    # RNA-seq quantification
    ("salmon", "RNA-seq", ["--version"]), ("kallisto", "RNA-seq", ["version"]), ("featureCounts", "RNA-seq", ["-v"]),
    ("htseq-count", "RNA-seq", ["--version"]), ("stringtie", "RNA-seq", ["--version"]), ("rsem-calculate-expression", "RNA-seq", ["--version"]),
    ("umi_tools", "RNA-seq", ["--version"]), ("cellranger", "single-cell", ["--version"]), ("kb", "single-cell", ["--version"]),
    ("bustools", "single-cell", ["version"]), ("velocyto", "single-cell", ["--version"]),
    # SAM/BAM/VCF and intervals
    ("samtools", "SAM/BAM/VCF", ["--version"]), ("bcftools", "SAM/BAM/VCF", ["--version"]), ("htsfile", "SAM/BAM/VCF", ["--version"]),
    ("bgzip", "SAM/BAM/VCF", ["--version"]), ("tabix", "SAM/BAM/VCF", ["--version"]), ("sambamba", "SAM/BAM/VCF", ["--version"]),
    ("picard", "SAM/BAM/VCF", ["SortSam", "--version"]), ("gatk", "variant calling", ["--version"]),
    ("freebayes", "variant calling", ["--version"]), ("lofreq", "variant calling", ["version"]), ("varscan", "variant calling", []),
    ("delly", "variant calling", ["--version"]), ("vcftools", "SAM/BAM/VCF", ["--version"]), ("snpEff", "annotation", ["-version"]),
    ("vep", "annotation", ["--help"]), ("mosdepth", "SAM/BAM/VCF", ["--version"]), ("bedtools", "intervals", ["--version"]),
    ("bedops", "intervals", ["--version"]), ("bamCoverage", "intervals", ["--version"]), ("computeMatrix", "intervals", ["--version"]),
    ("plotHeatmap", "intervals", ["--version"]), ("macs2", "ChIP/ATAC", ["--version"]), ("macs3", "ChIP/ATAC", ["--version"]),
    ("findMotifsGenome.pl", "ChIP/ATAC", []), ("meme", "motifs", ["-version"]), ("fimo", "motifs", ["--version"]),
    ("bismark", "methylation", ["--version"]), ("MethylDackel", "methylation", ["--version"]),
    ("plink", "genetics", ["--version"]), ("plink2", "genetics", ["--version"]), ("gffread", "annotation", ["--version"]),
    ("gffcompare", "annotation", ["--version"]), ("agat_convert_sp_gxf2gxf.pl", "annotation", ["--help"]),
    # sequence search, alignment, phylogeny
    ("blastn", "search", ["-version"]), ("blastp", "search", ["-version"]), ("blastx", "search", ["-version"]),
    ("tblastn", "search", ["-version"]), ("makeblastdb", "search", ["-version"]), ("diamond", "search", ["version"]),
    ("mmseqs", "search", ["version"]), ("hmmsearch", "search", ["-h"]), ("hmmscan", "search", ["-h"]),
    ("hmmbuild", "search", ["-h"]), ("jackhmmer", "search", ["-h"]), ("nhmmer", "search", ["-h"]), ("cmsearch", "search", ["-h"]),
    ("mafft", "alignment", ["--version"]), ("muscle", "alignment", ["-version"]), ("clustalo", "alignment", ["--version"]),
    ("kalign", "alignment", ["--version"]), ("t_coffee", "alignment", ["-version"]), ("trimal", "alignment", ["--version"]),
    ("iqtree2", "phylogenetics", ["--version"]), ("iqtree", "phylogenetics", ["--version"]), ("raxml-ng", "phylogenetics", ["--version"]),
    ("FastTree", "phylogenetics", []), ("fasttree", "phylogenetics", []), ("mb", "phylogenetics", ["-v"]),
    ("treetime", "phylogenetics", ["--version"]), ("augur", "phylogenetics", ["--version"]), ("nextclade", "phylogenetics", ["--version"]),
    # assembly, metagenomics, microbial genomics
    ("spades.py", "assembly", ["--version"]), ("megahit", "assembly", ["--version"]), ("flye", "assembly", ["--version"]),
    ("canu", "assembly", ["-version"]), ("velveth", "assembly", []), ("unicycler", "assembly", ["--version"]),
    ("hifiasm", "assembly", ["--version"]), ("Trinity", "assembly", ["--version"]), ("quast.py", "assembly", ["--version"]),
    ("busco", "assembly", ["--version"]), ("racon", "assembly", ["--version"]), ("medaka_consensus", "assembly", ["-h"]),
    ("pilon", "assembly", ["--version"]), ("prokka", "annotation", ["--version"]), ("bakta", "annotation", ["--version"]),
    ("prodigal", "annotation", ["-v"]), ("abricate", "microbial", ["--version"]), ("mlst", "microbial", ["--version"]),
    ("kraken2", "metagenomics", ["--version"]), ("bracken", "metagenomics", ["-v"]), ("metaphlan", "metagenomics", ["--version"]),
    ("humann", "metagenomics", ["--version"]), ("checkm", "metagenomics", []), ("gtdbtk", "metagenomics", ["--version"]),
    # public data (these need the internet)
    ("prefetch", "public data", ["--version"]), ("fasterq-dump", "public data", ["--version"]), ("fastq-dump", "public data", ["--version"]),
    ("esearch", "public data", ["-version"]), ("efetch", "public data", ["-version"]), ("datasets", "public data", ["--version"]),
    # workflows
    ("snakemake", "workflow", ["--version"]), ("nextflow", "workflow", ["-version"]), ("cwltool", "workflow", ["--version"]),
    ("make", "workflow", ["--version"]), ("parallel", "workflow", ["--version"]),
    # languages and environments
    ("python3", "language", ["--version"]), ("python", "language", ["--version"]), ("R", "language", ["--version"]),
    ("Rscript", "language", ["--version"]), ("perl", "language", ["-v"]), ("java", "language", ["-version"]),
    ("julia", "language", ["--version"]), ("node", "language", ["--version"]), ("ruby", "language", ["--version"]),
    ("gcc", "language", ["--version"]), ("jupyter", "language", ["--version"]), ("ipython", "language", ["--version"]),
    ("conda", "environment", ["--version"]), ("mamba", "environment", ["--version"]), ("micromamba", "environment", ["--version"]),
    ("pip", "environment", ["--version"]), ("singularity", "environment", ["--version"]), ("apptainer", "environment", ["--version"]),
    ("docker", "environment", ["--version"]), ("sbatch", "cluster", ["--version"]), ("qsub", "cluster", ["--version"]),
    # general tools
    ("git", "general", ["--version"]), ("curl", "general", ["--version"]), ("wget", "general", ["--version"]),
    ("jq", "general", ["--version"]), ("yq", "general", ["--version"]), ("awk", "general", ["--version"]), ("gawk", "general", ["--version"]),
    ("sed", "general", ["--version"]), ("grep", "general", ["--version"]), ("sort", "general", ["--version"]),
    ("datamash", "general", ["--version"]), ("mlr", "general", ["--version"]), ("csvlook", "general", ["--version"]),
    ("sqlite3", "general", ["--version"]), ("duckdb", "general", ["--version"]), ("pigz", "general", ["--version"]),
    ("zstd", "general", ["--version"]), ("xz", "general", ["--version"]), ("bzip2", "general", ["--help"]),
    ("gzip", "general", ["--version"]), ("unzip", "general", ["-v"]), ("tar", "general", ["--version"]),
    ("rsync", "general", ["--version"]), ("tree", "general", ["--version"]), ("column", "general", ["--version"]),
    ("tmux", "general", ["-V"]), ("screen", "general", ["--version"]), ("nano", "general", ["--version"]), ("vim", "general", ["--version"]),
    # programs with windows
    ("igv", "viewer", []), ("igv.sh", "viewer", []), ("rstudio", "viewer", []), ("code", "viewer", ["--version"]),
    ("xdg-open", "viewer", ["--version"]), ("open", "viewer", []), ("firefox", "viewer", ["--version"]),
]

# looked for but worth naming when missing (the AI can say "X is not installed here")
COMMONLY_EXPECTED = [
    "fastqc", "multiqc", "fastp", "cutadapt", "trimmomatic", "bwa", "bowtie2", "minimap2", "hisat2", "STAR",
    "salmon", "kallisto", "featureCounts", "samtools", "bcftools", "bedtools", "gatk", "picard", "freebayes",
    "blastn", "diamond", "mafft", "iqtree2", "spades.py", "kraken2", "snakemake", "nextflow", "R", "conda",
]

PY_PACKAGES = [
    "numpy", "pandas", "scipy", "matplotlib", "seaborn", "plotly", "scikit-learn", "statsmodels", "biopython",
    "pysam", "pybedtools", "pyranges", "cyvcf2", "pyvcf", "scanpy", "anndata", "scvi-tools", "squidpy", "muon",
    "gseapy", "pydeseq2", "networkx", "umap-learn", "ete3", "dendropy", "polars", "pyarrow", "h5py", "tables",
    "jupyterlab", "notebook", "ipywidgets", "torch", "tensorflow", "esm", "fair-esm", "transformers", "openpyxl",
    "snakemake", "multiqc", "pybiomart", "mygene", "requests", "tqdm", "numba", "xarray", "zarr", "dask",
]

R_PACKAGES = [
    "tidyverse", "ggplot2", "dplyr", "tidyr", "readr", "data.table", "DESeq2", "edgeR", "limma", "Seurat",
    "SingleCellExperiment", "scran", "scater", "Biostrings", "GenomicRanges", "GenomicFeatures", "rtracklayer",
    "Rsamtools", "VariantAnnotation", "vcfR", "clusterProfiler", "org.Hs.eg.db", "biomaRt", "ComplexHeatmap",
    "pheatmap", "ape", "phangorn", "ggtree", "vegan", "phyloseq", "dada2", "tximport", "apeglm", "sva",
    "WGCNA", "rmarkdown", "knitr", "shiny", "BiocManager", "lme4", "survival",
]

_VERSION_RE = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+)+[a-z]?(?:[-+._][\w.]+)?|\d+[a-z]\d*)(?![\w])", re.I)


def _cache_file():
    return os.path.join(paths.cache_dir(), "inventory.json")


def _watched_dirs():
    """The folders whose contents say what is installed: the folders on PATH, and Python's and R's
    package folders. Installing a program or a package (apt-get, pip, install.packages) changes them."""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    try:
        import site
        import sysconfig

        dirs += [sysconfig.get_paths().get(k, "") for k in ("purelib", "platlib")]
        dirs.append(site.getusersitepackages() if hasattr(site, "getusersitepackages") else "")
    except Exception:
        pass
    for k in ("R_LIBS", "R_LIBS_USER", "R_LIBS_SITE"):
        dirs += os.environ.get(k, "").split(os.pathsep)
    dirs += ["/usr/local/lib/R/site-library", "/usr/lib/R/site-library", "/usr/lib/R/library"]
    return [d for d in dict.fromkeys(dirs) if d]


def _fingerprint():
    h = hashlib.sha1()
    for k in ("PATH", "CONDA_PREFIX", "VIRTUAL_ENV", "LOADEDMODULES"):
        h.update((os.environ.get(k, "") + "\0").encode("utf-8", "replace"))
    for d in _watched_dirs():
        try:
            stamp = os.stat(d).st_mtime_ns
        except (OSError, ValueError):
            stamp = -1
        h.update(("%s\0%d\0" % (d, stamp)).encode("utf-8", "replace"))
    return h.hexdigest()


def _still_right(data):
    """A quick check of a saved inventory: each program in it is still there, and none that it lacks has
    appeared since (some file systems do not show a new file in their folder's time)."""
    tools = data.get("tools") or {}
    for name in tools:
        if not shutil.which(name):
            return False
    for name, _cat, _args in TOOLS:
        if name not in tools and shutil.which(name):
            return False
    return True


def _env():
    env = dict(os.environ)
    for k in list(env):
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I):
            env.pop(k)
    env.update({"PAGER": "cat", "NO_COLOR": "1", "TERM": "dumb", "MPLBACKEND": "Agg", "JAVA_TOOL_OPTIONS": ""})
    return env


def _version_of(path, name, args, timeout):
    try:
        p = subprocess.run([path] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=_env())
        out = p.stdout.decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return parse_version(out, name)


def parse_version(out, name=""):
    """The version in what a program printed about itself, or ''."""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    low = name.lower().replace(".py", "").replace(".sh", "").replace(".pl", "")
    # prefer a line that names the program or says "version"
    ranked = sorted(lines[:40], key=lambda ln: (0 if low and low in ln.lower() else 1, 0 if "version" in ln.lower() else 1))
    for ln in ranked:
        if re.search(r"copyright|\(c\)|usage:|warning|license", ln, re.I) and not re.search(r"version", ln, re.I):
            continue
        ln2 = re.sub(r"\b(?:19|20)\d\d[-/]\d\d[-/]\d\d\b", " ", ln)  # dates are not versions
        m = _VERSION_RE.search(ln2)
        if m:
            v = m.group(1)
            if re.fullmatch(r"\d{4}", v):
                continue
            return v[:40]
    return ""


def _scan_tools(timeout=6.0):
    found = {}
    for name, cat, args in TOOLS:
        p = shutil.which(name)
        if p and name not in found:
            found[name] = {"path": p, "category": cat, "args": args}
    # some names are the same program twice
    for a, b in (("python", "python3"), ("fasttree", "FastTree"), ("iqtree", "iqtree2"), ("gawk", "awk")):
        if a in found and b in found and os.path.realpath(found[a]["path"]) == os.path.realpath(found[b]["path"]):
            found.pop(a)
    names = list(found)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_version_of, found[n]["path"], n, found[n]["args"], timeout if found[n]["args"] else 3.0): n for n in names}
        for f in concurrent.futures.as_completed(futs):
            n = futs[f]
            try:
                found[n]["version"] = f.result()
            except Exception:
                found[n]["version"] = ""
    for v in found.values():
        v.pop("args", None)
    return found


def _python_packages(python=None):
    """Packages of the Python that the user uses (the first python3 on PATH, or this one)."""
    code = (
        "import json,sys\n"
        "try:\n import importlib.metadata as m\nexcept ImportError:\n import importlib_metadata as m\n"
        "d={}\n"
        "for x in m.distributions():\n"
        "  try:\n   d[x.metadata['Name'].lower()]=x.version\n  except Exception: pass\n"
        "print(json.dumps({'python':sys.version.split()[0],'executable':sys.executable,'packages':d}))\n"
    )
    exe = python or shutil.which("python3") or shutil.which("python") or sys.executable
    try:
        p = subprocess.run([exe, "-c", code], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, env=_env())
        data = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}
    pk = data.get("packages") or {}
    wanted = {}
    for name in PY_PACKAGES:
        v = pk.get(name.lower())
        if v:
            wanted[name] = v
    return {"python": data.get("python"), "executable": paths.pretty(data.get("executable") or exe), "packages": wanted, "count": len(pk)}


def _r_packages():
    rs = shutil.which("Rscript")
    if not rs:
        return {}
    code = 'ip <- installed.packages()[, c("Package", "Version")]; cat(paste(ip[,1], ip[,2]), sep="\\n")'
    try:
        p = subprocess.run([rs, "--vanilla", "-e", code], stdin=subprocess.DEVNULL, capture_output=True, timeout=30, env=_env())
    except Exception:
        return {}
    allp = {}
    for ln in p.stdout.decode("utf-8", "replace").splitlines():
        parts = ln.split()
        if len(parts) == 2:
            allp[parts[0]] = parts[1]
    return {"packages": {n: allp[n] for n in R_PACKAGES if n in allp}, "count": len(allp)}


def _system():
    info = {
        "os": platform.system(),
        "os_release": "",
        "machine": platform.machine(),
        "cpus": os.cpu_count() or 0,
        "memory_gb": None,
        "shell": os.path.basename(os.environ.get("SHELL", "")),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV") or "",
        "virtualenv": os.path.basename(os.environ.get("VIRTUAL_ENV", "")) if os.environ.get("VIRTUAL_ENV") else "",
        "modules": "",
        "colab": "COLAB_RELEASE_TAG" in os.environ or os.path.exists("/content") and "COLAB_GPU" in os.environ,
        "gpu": bool(shutil.which("nvidia-smi")),
        "scheduler": "slurm" if shutil.which("sbatch") else "pbs/sge" if shutil.which("qsub") else "",
    }
    if info["os"] == "Linux":
        try:
            with open("/etc/os-release") as f:
                for ln in f:
                    if ln.startswith("PRETTY_NAME="):
                        info["os_release"] = ln.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        try:
            with open("/proc/meminfo") as f:
                for ln in f:
                    if ln.startswith("MemTotal:"):
                        info["memory_gb"] = round(int(ln.split()[1]) / 1048576.0, 1)
        except (OSError, ValueError):
            pass
        if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
            info["container"] = True
    elif info["os"] == "Darwin":
        info["os_release"] = "macOS " + platform.mac_ver()[0]
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, timeout=3).stdout
            info["memory_gb"] = round(int(out) / 1073741824.0, 1)
        except Exception:
            pass
    elif info["os"] == "Windows":
        info["os_release"] = "Windows " + platform.release()
    if os.environ.get("MODULESHOME") or os.environ.get("LMOD_CMD"):
        info["modules"] = os.environ.get("LOADEDMODULES", "").replace(":", ", ") or "(none loaded)"
    if "WSL_DISTRO_NAME" in os.environ:
        info["wsl"] = os.environ["WSL_DISTRO_NAME"]
    return info


def gather(refresh=False, max_age=12 * 3600):
    """The inventory, from the cache if it is recent and nothing has changed."""
    fp = _fingerprint()
    cf = _cache_file()
    if not refresh:
        try:
            with open(cf, encoding="utf-8") as f:
                data = json.load(f)
            if (data.get("fingerprint") == fp and time.time() - data.get("time", 0) < max_age and data.get("version") == 2
                    and _still_right(data)):
                return data
        except (OSError, ValueError):
            pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        ft = ex.submit(_scan_tools)
        fpy = ex.submit(_python_packages)
        fr = ex.submit(_r_packages)
        tools, py, r = ft.result(), fpy.result(), fr.result()
    data = {"version": 2, "time": time.time(), "fingerprint": fp, "system": _system(), "tools": tools, "python": py, "r": r}
    try:
        paths.ensure(paths.cache_dir(), private=True)
        tmp = cf + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, cf)
    except OSError:
        pass
    return data


def is_installed(name):
    return shutil.which(name) is not None


def describe(inv, notebook_packages=None):
    """The inventory as lines for the AI."""
    s = inv.get("system", {})
    lines = []
    osdesc = s.get("os_release") or s.get("os") or "unknown system"
    extra = []
    if s.get("cpus"):
        extra.append("%d CPUs" % s["cpus"])
    if s.get("memory_gb"):
        extra.append("%s GB memory" % s["memory_gb"])
    if s.get("gpu"):
        extra.append("an NVIDIA GPU")
    lines.append("System: %s (%s)%s." % (osdesc, s.get("machine", ""), (", " + ", ".join(extra)) if extra else ""))
    if s.get("colab"):
        lines.append("This is a Google Colab runtime (a temporary Linux machine; files outside Google Drive disappear when it stops).")
    if s.get("wsl"):
        lines.append("Windows Subsystem for Linux (%s)." % s["wsl"])
    env = []
    if s.get("shell"):
        env.append("shell: " + s["shell"])
    if s.get("conda_env"):
        env.append("conda environment: " + s["conda_env"])
    if s.get("virtualenv"):
        env.append("virtualenv: " + s["virtualenv"])
    if s.get("modules"):
        env.append("environment modules (module load ...); loaded: " + s["modules"])
    if s.get("scheduler"):
        env.append("a job scheduler (%s)" % s["scheduler"])
    if env:
        lines.append("Environment: " + "; ".join(env) + ".")
    tools = inv.get("tools", {})
    by_cat = {}
    for name in sorted(tools, key=str.lower):
        t = tools[name]
        by_cat.setdefault(t.get("category", "other"), []).append(name + (" " + t["version"] if t.get("version") else ""))
    if tools:
        lines.append("Installed programs (on PATH, with versions where they report one):")
        order = ["QC", "mapping", "RNA-seq", "single-cell", "SAM/BAM/VCF", "variant calling", "intervals", "annotation", "ChIP/ATAC", "motifs", "methylation", "genetics", "search", "alignment", "phylogenetics", "assembly", "microbial", "metagenomics", "public data", "workflow", "cluster", "language", "environment", "general", "viewer"]
        for cat in order + sorted(set(by_cat) - set(order)):
            if cat in by_cat:
                lines.append("  - %s: %s" % (cat, ", ".join(by_cat[cat])))
    missing = [n for n in COMMONLY_EXPECTED if n not in tools]
    if missing:
        lines.append("Not installed (often used, but not on this computer): %s." % ", ".join(missing))
    py = notebook_packages or inv.get("python") or {}
    if py.get("python"):
        pk = py.get("packages") or {}
        lines.append("Python %s%s; packages: %s%s." % (
            py["python"],
            "" if notebook_packages else " (%s)" % py.get("executable", "python3"),
            ", ".join("%s %s" % (k, v) for k, v in sorted(pk.items(), key=lambda kv: kv[0].lower())) or "(none of the usual data-science packages)",
            ("; %d packages in all" % py["count"]) if py.get("count") else "",
        ))
    r = inv.get("r") or {}
    if r.get("count"):
        lines.append("R packages: %s; %d packages in all." % (", ".join("%s %s" % kv for kv in sorted(r.get("packages", {}).items())) or "none of the usual Bioconductor/tidyverse packages", r["count"]))
    return lines
