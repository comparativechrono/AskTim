"""Describe a file without sending what is in it.

`describe(path)` reads the start of a file (decompressing .gz) to work out its format, and
counts records where that is quick. It returns a short description such as

    data/reads_R1.fastq.gz: FASTQ (gzip), 12.4 MB; about 152,000 reads (estimated);
    read length 76-76 (mean 76.0); qualities look like Phred+33.

For tables it gives the number of rows and columns, and - when the first line is a header -
the column names and the kind of values in each column (numbers, text, true/false), but
never the values. Sequence names are given only when they look like reference names
(chr1, NC_000913.3 ...), and sample names never.
"""

import csv
import gzip
import io
import json
import os
import re
import shutil
import subprocess

from . import paths

SAMPLE_BYTES = 1 << 20  # what is read to recognise a format
COUNT_LIMIT = 200 << 20  # files up to this size (uncompressed reading) are counted exactly

_REF_NAME = re.compile(r"^(chr[\w.]+|\d{1,2}|[XYM]T?|NC_\d+(\.\d+)?|NZ_\w+|NT_\d+(\.\d+)?|NW_\d+(\.\d+)?|CM\d+(\.\d+)?|[A-Z]{1,2}\d{5,8}(\.\d+)?|contig_?\d+|scaffold_?\d+|NODE_\d+\w*|human_\w+|ENS\w+|[OPQ]\d[A-Z0-9]{3}\d|sp\|\w+\|\w+|tr\|\w+\|\w+)$")

INDEX_EXT = {
    ".bai": "a BAM index (made by samtools index)", ".csi": "a CSI index (samtools or bcftools)", ".crai": "a CRAM index",
    ".tbi": "a tabix index (made by tabix)", ".fai": "a FASTA index (made by samtools faidx)", ".gzi": "a bgzip index",
    ".amb": "part of a BWA index (bwa index)", ".ann": "part of a BWA index (bwa index)", ".bwt": "part of a BWA index (bwa index)",
    ".pac": "part of a BWA index (bwa index)", ".sa": "part of a BWA index (bwa index)", ".bt2": "part of a Bowtie 2 index (bowtie2-build)",
    ".bt2l": "part of a Bowtie 2 index (bowtie2-build)", ".ht2": "part of a HISAT2 index (hisat2-build)", ".mmi": "a minimap2 index (minimap2 -d)",
    ".dict": "a sequence dictionary (Picard/GATK CreateSequenceDictionary)", ".idx": "an index file",
}

CODE_EXT = {
    ".py", ".r", ".rmd", ".qmd", ".sh", ".bash", ".zsh", ".smk", ".nf", ".pl", ".awk", ".sed", ".md", ".rst", ".txt",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".config", ".wdl", ".cwl", ".ipynb", ".json", ".js", ".html", ".css", ".tex",
}
CODE_NAMES = {"snakefile", "makefile", "dockerfile", "readme", "license", "nextflow.config", "environment.yml", "requirements.txt"}


def human(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def _open_text(path, gz):
    raw = gzip.open(path, "rb") if gz else open(path, "rb")
    return raw


def _magic(path):
    try:
        with open(path, "rb") as f:
            return f.read(512)
    except OSError:
        return b""


def _bgzf(head):
    return head[:4] == b"\x1f\x8b\x08\x04" and b"BC" in head[12:20]


def _read_sample(path, gz, limit=SAMPLE_BYTES):
    try:
        with _open_text(path, gz) as f:
            data = f.read(limit)
    except (OSError, EOFError, gzip.BadGzipFile if hasattr(gzip, "BadGzipFile") else OSError):
        return None
    return data


def _count_lines(path, gz, cap=COUNT_LIMIT):
    """(lines, exact): exact if the whole file was read."""
    n = 0
    read = 0
    try:
        with _open_text(path, gz) as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    return n, True
                n += chunk.count(b"\n")
                read += len(chunk)
                if read > cap:
                    return n, False
    except (OSError, EOFError):
        return n, False


def _estimate(path, gz, sample, per_record_lines):
    """Records in the whole file, from the sample (for big or compressed files)."""
    size = os.path.getsize(path)
    lines = sample.count(b"\n")
    if not lines:
        return None
    if gz:
        try:
            with open(path, "rb") as f:
                comp = f.read(1 << 20)
            ratio = len(sample) / float(max(1, min(len(comp), size)))
            approx_uncompressed = size * max(1.0, ratio) if len(sample) >= SAMPLE_BYTES else len(sample)
        except OSError:
            return None
    else:
        approx_uncompressed = size
    return int(approx_uncompressed / (len(sample) / float(lines)) / per_record_lines)


def describe(path, strict=False, base=None):
    """A short description of a file or folder, safe to send to the AI."""
    shown = paths.pretty(path) if base is None else os.path.relpath(path, base)
    if not os.path.exists(path):
        return "%s: there is no such file or folder." % shown
    if os.path.isdir(path):
        return _describe_dir(path, shown)
    size = os.path.getsize(path)
    head = _magic(path)
    name = os.path.basename(path)
    low = name.lower()
    gz = head[:2] == b"\x1f\x8b"
    comp = "BGZF" if _bgzf(head) else "gzip" if gz else ""
    for ext_, what in INDEX_EXT.items():
        if low.endswith(ext_):
            return "%s: %s, %s." % (shown, what, human(size))
    # binary formats
    if low.endswith(".bam") or (gz and _bam_inside(path)):
        return _describe_bam(path, shown, size, strict)
    if head[:4] == b"CRAM":
        return "%s: CRAM alignment file, %s. %s" % (shown, human(size), _samtools_header_summary(path, strict) or "")
    if low.endswith(".bcf"):
        return "%s: BCF (binary VCF), %s. Use bcftools to read it." % (shown, human(size))
    for sig, kind in ((b"PK\x03\x04", "a zip archive (or an .xlsx/.docx file)"), (b"%PDF", "a PDF document"), (b"\x89PNG", "a PNG image"),
                      (b"\xff\xd8\xff", "a JPEG image"), (b"\x89HDF", "an HDF5 file (for example .h5ad or .h5)"), (b"PAR1", "a Parquet table"),
                      (b"SQLite format 3", "an SQLite database"), (b"BZh", "a bzip2-compressed file"), (b"\xfd7zXZ", "an xz-compressed file"),
                      (b"(\xb5/\xfd", "a zstd-compressed file"), (b"GIF8", "a GIF image")):
        if head.startswith(sig):
            extra = _zip_members(path) if sig == b"PK\x03\x04" else ""
            return "%s: %s, %s.%s" % (shown, kind, human(size), extra)
    sample = _read_sample(path, gz)
    if sample is None:
        return "%s: %s, %s; it could not be read (damaged or incomplete?)." % (shown, "compressed file" if gz else "file", human(size))
    if b"\x00" in sample[:8192] and not gz:
        return "%s: a binary file, %s." % (shown, human(size))
    text = sample.decode("utf-8", "replace")
    lines = text.split("\n")
    if len(sample) >= SAMPLE_BYTES:
        lines = lines[:-1]  # the last line may be cut
    stem = re.sub(r"\.(gz|bgz|bz2|xz|zst)$", "", low)
    ext = os.path.splitext(stem)[1]
    fmt = (" (%s)" % comp) if comp else ""
    first = next((ln for ln in lines if ln.strip()), "")
    if not first:
        return "%s: an empty file%s, %s." % (shown, fmt, human(size))
    if first.startswith("@") and len(lines) > 3 and lines[2].startswith("+"):
        return _describe_fastq(path, shown, size, gz, sample, lines, fmt)
    if first.startswith(">"):
        return _describe_fasta(path, shown, size, gz, sample, lines, fmt, strict)
    if first.startswith("##fileformat=VCF") or ext == ".vcf":
        return _describe_vcf(path, shown, size, gz, sample, lines, fmt, strict)
    if first.startswith("@HD") or first.startswith("@SQ") or ext == ".sam":
        return "%s: SAM alignments%s, %s. %s" % (shown, fmt, human(size), _samtools_header_summary(path, strict) or "")
    if ext in (".gff", ".gff3", ".gtf") or first.startswith("##gff-version"):
        return _describe_gff(path, shown, size, gz, lines, fmt)
    if ext in (".bed", ".bedgraph", ".narrowpeak", ".broadpeak"):
        return _describe_bed(path, shown, size, gz, lines, fmt)
    if ext == ".json" or (first.lstrip()[:1] in "{[" and ext not in CODE_EXT - {".json"}):
        return _describe_json(path, shown, size, gz, text, fmt, strict)
    if ext in CODE_EXT and ext not in (".txt", ".json") or stem in CODE_NAMES or name.lower() in CODE_NAMES:
        n, exact = _count_lines(path, gz)
        return "%s: a text file (%s)%s, %s, %s%d lines. It is code or writing, not data: you may read it." % (
            shown, ext.lstrip(".") or name, fmt, human(size), "" if exact else "at least ", n)
    return _describe_table_or_text(path, shown, size, gz, lines, fmt, strict, ext)


def _describe_dir(path, shown):
    counts = {}
    n_files = n_dirs = 0
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            n_dirs += len(dirs)
            for fn in files:
                if fn.startswith("."):
                    continue
                n_files += 1
                low = fn.lower()
                m = re.search(r"(\.[a-z0-9]{1,8}(\.(gz|bz2|xz|zst))?)$", low)
                counts[m.group(1) if m else "(no extension)"] = counts.get(m.group(1) if m else "(no extension)", 0) + 1
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
            if n_files > 50000:
                break
    except OSError:
        pass
    kinds = ", ".join("%d %s" % (v, k) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:12])
    return "%s/: a folder with %d files (%s) in %d subfolders, %s in all." % (shown.rstrip("/"), n_files, kinds or "none", n_dirs, human(total))


def _zip_members(path):
    try:
        import zipfile

        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        if any(n.startswith("xl/") for n in names):
            return " It is an Excel workbook (.xlsx)."
        return " It holds %d files." % len(names)
    except Exception:
        return ""


def _bam_inside(path):
    try:
        with gzip.open(path, "rb") as f:
            return f.read(4) == b"BAM\x01"
    except Exception:
        return False


def _samtools_header_summary(path, strict):
    st = shutil.which("samtools")
    if not st:
        return ""
    try:
        p = subprocess.run([st, "view", "-H", path], capture_output=True, timeout=20)
    except Exception:
        return ""
    hdr = p.stdout.decode("utf-8", "replace").splitlines()
    sq = [ln for ln in hdr if ln.startswith("@SQ")]
    rg = [ln for ln in hdr if ln.startswith("@RG")]
    pg = []
    for ln in hdr:
        if ln.startswith("@PG"):
            m = re.search(r"\tPN:([^\t]+)", ln) or re.search(r"\tID:([^\t]+)", ln)
            if m and m.group(1) not in pg:
                pg.append(m.group(1))
    so = ""
    for ln in hdr:
        if ln.startswith("@HD"):
            m = re.search(r"\tSO:(\S+)", ln)
            so = m.group(1) if m else ""
    names = []
    for ln in sq[:30]:
        m = re.search(r"\tSN:(\S+)\tLN:(\d+)", ln) or re.search(r"\tSN:(\S+)", ln)
        if m:
            names.append(m.group(1) if _REF_NAME.match(m.group(1)) else "(name withheld)")
    parts = ["%d reference sequence%s in the header" % (len(sq), "" if len(sq) == 1 else "s")]
    if names and not strict:
        parts[-1] += " (%s%s)" % (", ".join(names[:10]), ", …" if len(sq) > 10 else "")
    if so:
        parts.append("sorted by %s" % so)
    if rg:
        parts.append("%d read group%s (sample names withheld)" % (len(rg), "" if len(rg) == 1 else "s"))
    if pg and not strict:
        parts.append("made with: %s" % ", ".join(pg[:6]))
    return "; ".join(parts) + "."


def _describe_bam(path, shown, size, strict):
    idx = any(os.path.exists(path + e) for e in (".bai", ".csi")) or os.path.exists(re.sub(r"\.bam$", ".bai", path))
    out = "%s: BAM alignment file, %s; %s." % (shown, human(size), "indexed" if idx else "no index (.bai) next to it")
    hs = _samtools_header_summary(path, strict)
    if hs:
        out += " " + hs
    elif not shutil.which("samtools"):
        out += " (samtools is not installed, so its header was not read.)"
    if idx and shutil.which("samtools"):
        try:
            p = subprocess.run(["samtools", "idxstats", path], capture_output=True, timeout=30)
            mapped = unmapped = 0
            for ln in p.stdout.decode().splitlines():
                f = ln.split("\t")
                if len(f) == 4:
                    mapped += int(f[2])
                    unmapped += int(f[3])
            out += " %s mapped and %s unmapped reads (from the index)." % (format(mapped, ","), format(unmapped, ","))
        except Exception:
            pass
    return out


def _describe_fastq(path, shown, size, gz, sample, lines, fmt):
    seqs, quals = [], []
    for i in range(0, len(lines) - 3, 4):
        if not lines[i].startswith("@"):
            break
        seqs.append(len(lines[i + 1].strip()))
        quals.append(lines[i + 3].strip())
    if gz or size > COUNT_LIMIT:
        est = _estimate(path, gz, sample, 4)
        count = "about %s reads (estimated)" % format(est, ",") if est else "reads"
        if size <= COUNT_LIMIT // 4 and gz:
            n, exact = _count_lines(path, gz)
            if exact:
                count = "%s reads" % format(n // 4, ",")
    else:
        n, exact = _count_lines(path, gz)
        count = "%s reads" % format(n // 4, ",") if exact else "at least %s reads" % format(n // 4, ",")
    mn = min(quals_ord(q) for q in quals if q) if quals else 64
    enc = "Phred+33 (Sanger/Illumina 1.8+)" if mn < 59 else "possibly Phred+64 (old Illumina)" if mn >= 64 else "Phred+33"
    hdr = lines[0]
    paired = " Read names end in /1 or /2 (paired reads)." if re.search(r"/[12]\s*$", hdr.split()[0]) else (" The names look like Illumina CASAVA 1.8 (with a read number after the space)." if re.search(r"\s[12]:[YN]:", hdr) else "")
    lens = "read length %d-%d (mean %.1f)" % (min(seqs), max(seqs), sum(seqs) / float(len(seqs))) if seqs else "read lengths unknown"
    return "%s: FASTQ%s, %s; %s; %s; qualities look like %s.%s" % (shown, fmt, human(size), count, lens, enc, paired)


def quals_ord(q):
    return min(ord(c) for c in q)


def _describe_fasta(path, shown, size, gz, sample, lines, fmt, strict):
    names, lengths = [], []
    cur = None
    alphabet = set()
    for ln in lines:
        if ln.startswith(">"):
            if cur is not None:
                lengths.append(cur)
            names.append(ln[1:].split()[0] if ln[1:].split() else "")
            cur = 0
        elif cur is not None:
            s = ln.strip()
            cur += len(s)
            if len(alphabet) < 30:
                alphabet.update(s.upper()[:200])
    if cur is not None:
        lengths.append(cur)
    nuc = set("ACGTUNRYKMSWBDHV-*.")
    kind = "DNA/RNA" if alphabet and alphabet <= nuc else "protein" if alphabet else "sequences"
    whole = size <= COUNT_LIMIT and not (gz and size > COUNT_LIMIT // 4)
    if whole:
        n = 0
        total_len = 0
        try:
            with _open_text(path, gz) as f:
                for raw in f:
                    if raw.startswith(b">"):
                        n += 1
                    else:
                        total_len += len(raw.strip())
        except (OSError, EOFError):
            pass
        count = "%s sequence%s, %s letters in all" % (format(n, ","), "" if n == 1 else "s", format(total_len, ","))
    else:
        count = "at least %d sequences (the file is large; not all were counted)" % len(names)
    shown_names = ""
    if not strict and names:
        ok = [x for x in names[:12] if _REF_NAME.match(x)]
        if ok and len(ok) == len(names[:12]):
            shown_names = "; first names: %s%s" % (", ".join(ok[:8]), ", …" if len(names) > 8 else "")
        else:
            shown_names = "; sequence names withheld"
    lens = ""
    if lengths and len(sample) < SAMPLE_BYTES:
        lens = "; lengths %s-%s" % (format(min(lengths), ","), format(max(lengths), ","))
    idx = " An index (.fai) is next to it." if os.path.exists(path + ".fai") else ""
    return "%s: FASTA (%s)%s, %s; %s%s%s.%s" % (shown, kind, fmt, human(size), count, lens, shown_names, idx)


def _describe_vcf(path, shown, size, gz, sample, lines, fmt, strict):
    info, fmt_ids, contigs, samples = [], [], 0, 0
    data_lines = 0
    for ln in lines:
        if ln.startswith("##INFO=<ID="):
            info.append(ln[11:].split(",")[0])
        elif ln.startswith("##FORMAT=<ID="):
            fmt_ids.append(ln[13:].split(",")[0])
        elif ln.startswith("##contig="):
            contigs += 1
        elif ln.startswith("#CHROM"):
            samples = max(0, len(ln.split("\t")) - 9)
        elif ln and not ln.startswith("#"):
            data_lines += 1
    if size <= COUNT_LIMIT and not (gz and size > COUNT_LIMIT // 4):
        n = 0
        try:
            with _open_text(path, gz) as f:
                for raw in f:
                    if not raw.startswith(b"#") and raw.strip():
                        n += 1
        except (OSError, EOFError):
            pass
        count = "%s variant record%s" % (format(n, ","), "" if n == 1 else "s")
    else:
        count = "many variant records (not counted: the file is large)"
    idx = " It has an index (%s)." % ", ".join(e for e in (".tbi", ".csi") if os.path.exists(path + e)) if any(os.path.exists(path + e) for e in (".tbi", ".csi")) else ""
    parts = ["%s: VCF%s, %s; %s; %d sample%s (names withheld); %d contigs in the header" % (shown, fmt, human(size), count, samples, "" if samples == 1 else "s", contigs)]
    if not strict:
        if info:
            parts.append("INFO fields: %s" % ", ".join(info[:25]))
        if fmt_ids:
            parts.append("FORMAT fields: %s" % ", ".join(fmt_ids[:25]))
    if gz and fmt != " (BGZF)":
        parts.append("it is gzip- but not bgzip-compressed, so it cannot be indexed with tabix as it is")
    return "; ".join(parts) + "." + idx


def _describe_gff(path, shown, size, gz, lines, fmt):
    types = {}
    for ln in lines:
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) >= 9:
            types[f[2]] = types.get(f[2], 0) + 1
    n, exact = _count_lines(path, gz)
    t = ", ".join("%s (%d)" % kv for kv in sorted(types.items(), key=lambda kv: -kv[1])[:10])
    return "%s: GFF/GTF annotation%s, %s, %s%s lines; feature types in the first part: %s." % (shown, fmt, human(size), "" if exact else "at least ", format(n, ","), t or "none found")


def _describe_bed(path, shown, size, gz, lines, fmt):
    cols = 0
    for ln in lines:
        if ln.strip() and not ln.startswith(("#", "track", "browser")):
            cols = len(ln.split("\t"))
            break
    n, exact = _count_lines(path, gz)
    return "%s: BED-like intervals%s, %s; %s%s lines; %d columns." % (shown, fmt, human(size), "" if exact else "at least ", format(n, ","), cols)


def _json_shape(obj, depth=0):
    if depth > 2:
        return "…"
    if isinstance(obj, dict):
        keys = list(obj)[:15]
        inner = ", ".join("%s: %s" % (k, _json_shape(obj[k], depth + 1)) for k in keys)
        return "{%s%s}" % (inner, ", …" if len(obj) > 15 else "")
    if isinstance(obj, list):
        return "[%d item%s%s]" % (len(obj), "" if len(obj) == 1 else "s", (" of " + _json_shape(obj[0], depth + 1)) if obj else "")
    return type(obj).__name__.replace("str", "text").replace("float", "number").replace("int", "number")


def _describe_json(path, shown, size, gz, text, fmt, strict):
    try:
        if size <= COUNT_LIMIT // 4:
            with _open_text(path, gz) as f:
                obj = json.loads(f.read().decode("utf-8", "replace"))
        else:
            obj = json.loads(text)
    except ValueError:
        return "%s: JSON-like text%s, %s (it could not be read as JSON)." % (shown, fmt, human(size))
    shape = _json_shape(obj) if not strict else type(obj).__name__
    return "%s: JSON%s, %s; structure (keys and types, no values): %s" % (shown, fmt, human(size), shape)


def _describe_table_or_text(path, shown, size, gz, lines, fmt, strict, ext):
    rows = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
    if not rows:
        return "%s: a text file of comments only%s, %s." % (shown, fmt, human(size))
    delim = None
    for d in ("\t", ",", ";", "|"):
        counts = [r.count(d) for r in rows[:50]]
        if counts and min(counts) >= 1 and max(counts) == min(counts):
            delim = d
            break
    if delim is None and ext in (".csv", ".tsv", ".tab"):
        delim = "," if ext == ".csv" else "\t"
    n, exact = _count_lines(path, gz)
    total = "%s%s lines" % ("" if exact else "at least ", format(n, ","))
    if delim is None:
        words = sum(len(r.split()) for r in rows[:200]) / float(min(len(rows), 200))
        return "%s: plain text%s, %s, %s (about %.0f words per line); not a table AskTim! recognises." % (shown, fmt, human(size), total, words)
    name = {"\t": "tab-separated", ",": "comma-separated (CSV)", ";": "semicolon-separated", "|": "pipe-separated"}[delim]
    try:
        parsed = list(csv.reader(io.StringIO("\n".join(rows[:2000])), delimiter=delim))
    except csv.Error:
        parsed = [r.split(delim) for r in rows[:2000]]
    if not parsed:
        return "%s: %s table%s, %s, %s." % (shown, name, fmt, human(size), total)
    ncol = max(len(r) for r in parsed[:50])
    header = _looks_like_header(parsed)
    body = parsed[1:] if header else parsed
    kinds = []
    for j in range(ncol):
        vals = [r[j].strip() for r in body[:2000] if j < len(r)]
        kinds.append(_column_kind(vals))
    out = "%s: %s table%s, %s, %s; %d columns" % (shown, name, fmt, human(size), total, ncol)
    if header and not strict:
        cols = ["%s (%s)" % (_clip(h), k) for h, k in zip(parsed[0], kinds)]
        out += "; the first line is a header. Columns: " + ", ".join(cols[:60]) + (", …" if ncol > 60 else "")
    elif header:
        out += "; the first line is a header (column names withheld in strict mode); kinds of values: " + ", ".join(kinds[:60])
    else:
        out += "; no header line was found; kinds of values by column: " + ", ".join("%d: %s" % (j + 1, k) for j, k in enumerate(kinds[:60]))
    return out + "."


def _clip(s, n=40):
    s = s.strip().strip('"')
    return s if len(s) <= n else s[: n - 1] + "…"


def _is_number(v):
    try:
        float(v.replace(",", "")) if v.count(",") <= 3 else float(v)
        return True
    except ValueError:
        return v.lower() in ("na", "nan", "inf", "-inf", "")


def _column_kind(vals):
    vals = [v for v in vals if v != ""]
    if not vals:
        return "empty"
    low = set(v.lower() for v in vals[:500])
    if low <= {"true", "false", "t", "f", "yes", "no", "0", "1"} and len(low) <= 4 and not all(v in ("0", "1") for v in low):
        return "true/false"
    nums = sum(1 for v in vals if _is_number(v))
    if nums == len(vals):
        ints = all(re.fullmatch(r"-?\d+", v) for v in vals if v.lower() not in ("na", "nan"))
        return "whole numbers" if ints else "numbers"
    if nums >= 0.9 * len(vals):
        return "numbers (some missing or text)"
    distinct = len(set(vals))
    if distinct <= 12 and len(vals) >= 20:
        return "text, %d distinct values" % distinct
    if all(re.fullmatch(r"\d{4}-\d\d-\d\d.*", v) for v in vals[:50]):
        return "dates"
    return "text"


def _looks_like_header(parsed):
    if len(parsed) < 2:
        return all(not _is_number(c) for c in parsed[0]) if parsed else False
    first, rest = parsed[0], parsed[1:20]
    if any(_is_number(c) and c.strip() for c in first):
        return False
    # a header if some column is numeric below it, or its cells are unlike the cells below
    for j, c in enumerate(first):
        below = [r[j] for r in rest if j < len(r)]
        if below and all(_is_number(b) and b.strip() for b in below):
            return True
    try:
        return csv.Sniffer().has_header("\n".join(",".join(r) for r in parsed[:20]))
    except csv.Error:
        return False


def is_code_file(path):
    low = os.path.basename(path).lower()
    stem = re.sub(r"\.(gz|bz2|xz)$", "", low)
    ext = os.path.splitext(stem)[1]
    return (ext in CODE_EXT and ext not in (".txt", ".json", ".ipynb", ".html")) or stem in CODE_NAMES


def listing(folder, max_entries=50, strict=False):
    """Names and sizes of the files in a folder (one level, then one more for subfolders)."""
    if strict:
        try:
            n = len([e for e in os.listdir(folder) if not e.startswith(".")])
        except OSError:
            n = 0
        return "%d entries (names withheld in strict mode)" % n
    out = []
    more = 0

    def walk(d, depth):
        nonlocal more
        try:
            entries = sorted(os.scandir(d), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        for e in entries:
            if e.name.startswith(".") or e.name in ("__pycache__", "node_modules"):
                continue
            if len(out) >= max_entries:
                more += 1
                continue
            try:
                if e.is_dir():
                    out.append("  " * depth + e.name + "/")
                    if depth == 0 and e.name not in ("asktim-runs",):
                        walk(e.path, depth + 1)
                else:
                    out.append("  " * depth + "%s (%s)" % (e.name, human(e.stat().st_size)))
            except OSError:
                pass

    walk(folder, 0)
    if more:
        out.append("… and %d more" % more)
    return "\n".join(out) if out else "(empty)"
