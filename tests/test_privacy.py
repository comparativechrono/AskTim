"""What may go to the AI: the data filter and the redaction of secrets and personal details."""

import random

import pytest

from asktim.privacy import clean, filter_output, redact_secrets

rnd = random.Random(3)


def seq(n):
    return "".join(rnd.choice("ACGT") for _ in range(n))


FASTQ = "".join("@SRR098401.%d/1\n%s\n+\n%s\n" % (i, s, "".join(rnd.choice("FGHIJ#:,") for _ in s)) for i, s in enumerate([seq(76) for _ in range(3)]))
WITHHELD = {
    "fastq": FASTQ,
    "fasta": ">chr10 slice\n%s\n%s\n>sp|P69905|HBA_HUMAN Hemoglobin\nMVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHF" % (seq(60), seq(60)),
    "sam": "@HD\tVN:1.6\tSO:coordinate\n@RG\tID:1\tSM:PATIENT42\nr1\t99\tchr10\t11580\t60\t76M\t=\t11700\t196\t%s\tIIII\tNM:i:0" % seq(20),
    "csv": "orf,gene,essential\nYAL001C,TFC3,True\nYAL002W,VPS8,False\nYAL003W,EFB1,True",
    "people": "John,Smith,London,1985-03-02\nMary,Jones,Leeds,1990-11-12\nAhmed,Khan,York,1979-05-30",
    "pandas": "       orf  protein_length  essential\n0  YAL001C            1160       True\n1  YAL002W            1274      False\n2  YAL003W             206       True",
    "pandas_text": "                         description  essential\n0               hypothetical protein       True\n1      vacuolar protein sorting gene      False\n2  translation elongation factor 1 beta  True\n3               hypothetical protein      False",
    "bcftools_query": "chr10 96541616 G A 0/1\nchr10 96541720 C T 1/1\nchr10 3047 A G 0/1",
    "uniq_c": "     42 SAMPLE_A\n     17 SAMPLE_B\n      9 SAMPLE_C\n      3 SAMPLE_D\n      1 SAMPLE_E",
    "ids": "NA12878\nNA12891\nNA12892\nHG00096\nHG00097\nHG00099",
    "bed": "chr1\t100\t200\tpeak1\nchr1\t300\t400\tpeak2",
    "numbers": "[[0.1 0.2 0.3]\n [0.4 0.5 0.6]\n [0.7 0.8 0.9]]",
    "base64": "QUdBVENHR0FBR0FHQ0FDQUNHVENUR0FBQ1RDQ0FHVENBR0FUQ0dHQUFHQUdDQUNBQ0dU\nQ0FHVENBR0FUQ0dHQUFHQUdDQUNBQ0dUQ1RHQUFDVENDQUdUQ0FHQVRDR0dBQUdBR0NB",
    "hexdump": "00000000: 4041 4243 4445 4647 0a41 4243 4445  @ABCDEFG.ABCDE\n00000010: 4142 4344 4546 4748 494a 4b4c 4d4e  ABCDEFGHIJKLMN",
    "pylist": "['YAL001C', 'YAL002W', 'YAL003W']",
}
KEPT = {
    "flagstat": "7038 + 0 in total (QC-passed reads + QC-failed reads)\n6817 + 0 mapped (96.86% : N/A)",
    "help": "Usage: samtools view [options] <in.bam>\n  -b, --bam                  Output BAM\n  -o, --output FILE          Write output to FILE [standard output]\n  -U, --unoutput FILE, --output-unselected FILE",
    "wc": "  14076 reads_R1.fastq\n  14076 reads_R2.fastq\n  28152 total",
    "md5": "d41d8cd98f00b204e9800998ecf8427e  reference.fa",
    "traceback": "Traceback (most recent call last):\n  File \"plot.py\", line 3, in <module>\n    df = pd.read_csv(\"counts.csv\")\n    ~~~~~~~~~~~^^^^^^^^^^^^^^\nFileNotFoundError: [Errno 2] No such file or directory: 'counts.csv'",
    "fastp": "Read1 before filtering:\ntotal reads: 3519\nQ20 bases: 259000(96.84%)\nDuplication rate: 7.16%",
    "minimap2": "[M::mm_idx_gen::0.004*1.40] collected minimizers\n[M::worker_pipeline::0.123*0.98] mapped 7038 sequences\n[M::main] Version: 2.26-r1175",
    "prose": "The index was built. 3 files were written to results/.\nDone.",
    "setx": "+ samtools sort -o out.bam in.bam\n+ samtools index out.bam",
    "bwa_help": "Usage: bwa mem [options] <idxbase> <in1.fq> [in2.fq]\n\nAlgorithm options:\n\n       -t INT        number of threads [1]\n       -k INT        minimum seed length [19]\n       -w INT        band width for banded alignment [100]",
    "json_summary": "{\n  \"summary\": {\n    \"total_reads\": 7038,\n    \"gc_content\": 0.412\n  }\n}",
}


@pytest.mark.parametrize("name", sorted(WITHHELD))
def test_data_is_withheld(name):
    r = filter_output(WITHHELD[name])
    assert r.withheld >= 1, r.text
    # none of the records' values survive
    for line in WITHHELD[name].splitlines():
        for tok in line.replace(",", " ").replace("\t", " ").split():
            if len(tok) >= 6 and any(c.isdigit() for c in tok) and tok.isalnum():
                assert tok not in r.text, (name, tok, r.text)
    assert "the student can see" in r.text


@pytest.mark.parametrize("name", sorted(KEPT))
def test_messages_and_summaries_get_through(name):
    r = filter_output(KEPT[name])
    assert r.withheld == 0, r.text


def test_sequences_never_get_through_in_any_form():
    s = seq(76)
    for text in (s, "read: " + s, "@r1\n" + s + "\n+\n" + "I" * 76, "ERROR in " + s):
        assert s not in filter_output(text).text


def test_fastq_records_withheld_completely():
    r = filter_output(FASTQ)
    assert r.withheld == len(FASTQ.strip().splitlines())
    assert "FASTQ" in r.text


def test_vcf_keeps_structure_hides_samples_and_records():
    vcf = "##fileformat=VCFv4.2\n##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT_0042\nchr10\t11616\t.\tG\tA\t222\t.\tDP=106\tGT\t0/1"
    r = filter_output(vcf)
    assert "PATIENT_0042" not in r.text and "11616" not in r.text
    assert "##INFO=<ID=DP" in r.text and "1 sample column" in r.text
    assert "##INFO" not in filter_output(vcf, strict=True).text


def test_strict_mode_hides_column_names_and_quoted_values():
    r = filter_output(WITHHELD["pandas"], strict=True)
    assert "protein_length" not in r.text
    r = filter_output("KeyError: 'Essential'\nValueError: could not convert string to float: 'John Smith'", strict=True)
    assert "John Smith" not in r.text and "KeyError" in r.text


def test_help_kind_is_only_cleaned():
    text = "Options:\n  chr1 100 200\nexport TOKEN=abc123def456"
    r = filter_output(text, kind="help")
    assert "chr1 100 200" in r.text and "abc123def456" not in r.text


@pytest.mark.parametrize("text,gone", [
    ("AskTim! Claude sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "sk-ant-api03"),
    ("export ANTHROPIC_API_KEY=sk-ant-xyz123456789012345", "sk-ant-xyz"),
    ("export TOKEN=abc123def456", "abc123def456"),
    ("PASSWORD=hunter2", "hunter2"),
    ("git clone https://tim:hunter2@github.com/me/repo.git", "hunter2"),
    ("curl -H 'Authorization: Bearer abc.def.ghi' https://x.org", "abc.def.ghi"),
    ("mysql --password=s3cret -u root", "s3cret"),
    ("token=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_"),
    ("echo AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "AIzaSy"),
    ("grep 'tim.hearn@cam.ac.uk' contacts.txt", "tim.hearn@cam.ac.uk"),
    ("grep 9434765919 patients.csv", "9434765919"),
    ("grep '943 476 5919' patients.csv", "943 476 5919"),
    ("grep GATCTGAAGATCTGAGGCTTCCAGGTCACAGAGCTTCCAGTAGCTGCATTTCTGGG reads.fq", "GATCTGAAGATCTGAGGCTTCC"),
])
def test_secrets_and_personal_details_removed(text, gone):
    out, n = clean(text)
    assert gone not in out and n >= 1, out


@pytest.mark.parametrize("text", [
    "secret_rows = df.head()",
    "passed = df[df.qc]",
    "key_genes = ['BRCA1']",
    "sort_key = \"length\"",
    "token_count = 5",
    "cd $PWD && ls",
    "samtools view x.bam chr10:96541616-96541617",
    "md5sum ref.fa  # d41d8cd98f00b204e9800998ecf8427e",
    "cutadapt -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCA -o out.fq in.fq",
])
def test_ordinary_code_is_left_alone(text):
    assert clean(text)[0] == text


def test_home_folder_and_user_name_masked(monkeypatch):
    import asktim.privacy as P

    monkeypatch.setattr(P, "_IDENT", ("/home/th123", "th123", "lab-pc-7"))
    out = P.mask_identity("ls /home/th123/data; whoami: th123 on lab-pc-7; /home/th1234/x stays")
    assert out == "ls ~/data; whoami: user on host; /home/th1234/x stays"


def test_data_typed_into_notebook_code_is_withheld():
    from asktim.privacy import filter_code

    code = 'data = """sample,condition,age\nS1,control,34\nS2,treated,51\nS3,treated,47\nS4,control,29\n"""\n' \
           "counts = [12, 45, 7, 88, 23, 91, 5, 17]\nfig, ax = plt.subplots(figsize=(6, 4))\nthresholds = [0.01, 0.05, 0.1]\n" \
           'doc = """Reads the file\nand returns\na data frame."""\n'
    out, n = filter_code(code)
    assert "S2,treated" not in out and "88, 23" not in out and n == 6
    assert "plt.subplots(figsize=(6, 4))" in out and "[0.01, 0.05, 0.1]" in out and "and returns" in out


def test_sequences_in_alignments_and_groups_are_withheld():
    import random

    from asktim.privacy import filter_output

    random.seed(5)

    def s(n, a="acgt"):
        return "".join(random.choice(a) for _ in range(n))

    aa = "ACDEFGHIKLMNPQRSTVWY"
    samples = {
        "GenBank": "ORIGIN\n" + "\n".join("%9d %s" % (1 + 60 * i, " ".join(s(10) for _ in range(6))) for i in range(8)) + "\n//",
        "Clustal": "CLUSTAL W (1.83) multiple sequence alignment\n\n" + "\n".join("seq%d      %s" % (i, s(60, "ACGT-")) for i in range(4)),
        "BLAST": "\n".join(["Query  1    %s  60" % s(60, aa), "            %s" % s(60, aa + "+ "), "Sbjct  1    %s  60" % s(60, aa), ""] * 3),
        "codons": "\n".join(" ".join(s(3, "ACGT") for _ in range(20)) for _ in range(10)),
        "protein blocks": "\n".join(" ".join(s(10, aa) for _ in range(6)) for _ in range(6)),
        "MAF": "##maf version=1\na score=23262.0\n" + "\n".join("s hg18.chr7    27578828 38 + 158545518 %s" % s(38, "ACGT-") for _ in range(3)),
    }
    for name, text in samples.items():
        out = filter_output(text).text
        assert not [ln for ln in out.splitlines() if "AskTim!" not in ln and sum(c.isupper() or c in "acgt" for c in ln) >= 20], (name, out)
    for line in ["WARNING: THE FILE IS MISSING AND WILL BE SKIPPED", "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG AGAIN AND AGAIN",
                 "a cat at a tag", "Adapter sequence: AGATCGGAAGAGC", "Total reads: 1000 Mapped: 950 (95.00%)", "[main] CMD: bwa mem -t 4 ref.fa r1.fq"]:
        assert filter_output(line).withheld == 0, line
