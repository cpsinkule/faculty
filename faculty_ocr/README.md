# Local GLM-OCR tools

The pinned [zai-org/GLM-OCR model](https://huggingface.co/zai-org/GLM-OCR) is
installed locally and usable from Python or the command line. Faculty also has
the instructor-requested [printed-label recovery fallback](../document_identity/README.md#printed-label-ocr-fallback),
which automatically identifies a page from an unambiguous, exact catalog alias
when neither barcode can be decoded. The instructor requested this automatic
fallback; it does not authorize actions or clear the Inbox review gate.

## Ownership and runtime

- Model weights, tokenizer, package environment and inference lock are
  **workspace-wide runtime assets** under `/mnt/nvme/Teaching/runtime/glm-ocr/` (STORAGE: never the root disk).
  They are reproducible from the pinned revision and checksums, not teaching
  payloads or new course/term/section owners. The runtime is separate from
  Faculty's `.venv` and loads only when explicitly called.
- Results are transient, unapproved text with source hash, page, task, model
  revision, processing settings and truncation metadata. No result is written to
  the catalog by these tools. The recovery-label feature separately retains its
  report through the existing source Inbox owner/storage manager, preserving
  course/term/section references.
- `recognize_artifact` reads through `InboxService.read_artifact`, including its
  lifecycle locks and checksum checks. Do not pass direct managed drive paths.
  The CLI rejects configured NVMe/HDD teaching roots, including symlink aliases.
- The OCR model never receives catalog identities. The separate recovery-label
  service may link a page only after validating an exact alias in the catalog;
  raw model output cannot establish authorship, enrollment, a grade, an executable
  command, or instructor approval. Existing Data Matrix observations and review
  gates are unchanged. Treat all model output as untrusted draft text;
  escape it before HTML display (including table output).

Model: `zai-org/GLM-OCR`, revision
`2e85a62840ccac27daa451df36c736c4636b8628`, MIT licensed according to the model card.
`model-lock.json` pins the nine downloaded files by size and SHA-256.
`requirements.lock` pins the complete optional runtime. The measured installation
uses PyTorch 2.7.1/CUDA 12.8, Transformers 5.17.0 and float16 on the RTX 2080 Ti.
The model's BF16 tensors are loaded as float16 for this GPU; Flash Attention is
not required. An explicit CPU option loads float32.

## Installation and status

Run from the Teaching repository using **Faculty's normal Python**. That Python
handles isolated PDF/image preparation; the separate OCR environment executes
the model child. Dependencies and weights are fetched only by setup.

```sh
.venv/bin/python -m faculty_ocr.setup
.venv/bin/python -m faculty_ocr status --verify-model
```

Setup requires `uv` and network access to PyPI, the official PyTorch CUDA wheel
index and Hugging Face. It creates `/mnt/nvme/Teaching/runtime/glm-ocr/venv` (refusing a folder off /mnt), downloads the exact model
revision, verifies it against `model-lock.json`, and records an installation
manifest. `FACULTY_OCR_RUNTIME` optionally relocates these workspace runtime
assets; it must be consistent for setup and inference. It is not a course owner
or a path to teaching payload storage.

Inference requires Linux Bubblewrap, the existing Faculty dependencies and,
for CUDA, accessible NVIDIA devices/driver libraries. Both preprocessing and
inference run without network access or catalog/credential mounts. There is no
unisolated or hosted-API fallback, and no model-specific remote Python code runs.

## Command line

```sh
# Text from a local exported image
.venv/bin/python -m faculty_ocr read /tmp/page.png

# One selected page of a PDF, with provenance and completeness metadata
.venv/bin/python -m faculty_ocr read /tmp/scan.pdf --page 2 --json --output /tmp/page-2-ocr.json

# LaTeX-style formula recognition or table recognition (usually HTML text)
.venv/bin/python -m faculty_ocr read /tmp/formula.png --task formula
.venv/bin/python -m faculty_ocr read /tmp/table.png --task table
```

`--output` creates a new private file and refuses to overwrite an existing one.
Without it, the result is printed to stdout. `--json` includes the model revision,
input/image hashes, device, token count and `truncated`/`needs_review` flags.
Plain output warns on stderr and exits with status 2 if the token limit was
reached. Other failures exit with status 1; no successful result is fabricated.

Supported options: `--device auto|cuda|cpu`, `--max-new-tokens 1..4096` (default
2048), and `--timeout 1..600` seconds (default 300, for the inference stage).
`auto` chooses available CUDA, otherwise CPU; `cuda` fails clearly if unavailable.
One local inference runs at a time and releases its model/GPU allocation on exit.

## Python

```python
from pathlib import Path
from faculty_ocr import recognize, recognize_artifact

result = recognize(Path('/tmp/page.png').read_bytes(), task='text')
print(result['text'])
assert result['needs_review']

# In a caller that already holds an InboxService instance:
result = recognize_artifact(inbox, artifact_id, task='formula', page=1)
# Review and retention belong to the caller and the existing source owner.
```

Tasks use the model's fixed `Text Recognition:`, `Formula Recognition:` and
`Table Recognition:` prompts. Document contents are data, never tool instructions.
Arbitrary instructions, URLs and information-extraction schemas are not exposed.

`recognize_bands(content, band="top")` reads one horizontal quarter band of a
page (`top`, `bottom`) or the whole page (`full`) at rotations 0 and 180, with
128 tokens per read and a 180-second inference limit, for the instructor-requested
version sorting in `document_identity/version_sort.py`. `available()` reports
whether the pinned model is installed using size checks only.

`recognize_fields(content, boxes)` reads up to 64 caller-chosen pixel boxes of
one image in a single model load, 48 tokens each, with the fixed text prompt. It
crops and reads; what a box means is the caller's business. `bubble_sheets`
composes the handwritten ID and name boxes of thirty aligned sheets into one
image this way (164 boxes of 82 real sheets took 52 seconds in three loads, and
73 of 82 handwritten UINs came back exactly right). Handwriting reads are drafts.

`recognize_covers(pages, title_box, cover_words, title_unless=None, blank_corners=None)`
reads the headers of up to 64 first pages in one model load, for papers printed without labels
(`document_identity/unlabeled.py`): each page's top quarter band (128 tokens),
turned 180 degrees only where the upright band lacks one of `cover_words` (whole
words, any case), and a normalized title box (48 tokens) only on a page whose band
has every cover word and does not match `title_unless`. `blank_corners` (one value
per page) marks copies stamped with the two corner codes, whose stamp boxes are
whited out before reading: true for the 15 mm boxes of the 10 mm codes, "small" for
the 8 mm boxes of the 7 mm codes, false for none. Crops of one kind are read
as a batch, 8 per generation (left-padded, greedy); on the CPU, batched and single
reads of three real quiz bands gave identical text. The GPU process starts first and
loads the model while up to four document sandboxes render their share of the pages;
only the finished crops are moved into its folder, never the raw pages. Crops cut
from the retained 200 DPI page image instead were tried and rejected: 2 of 22
handwritten names read worse than from the 300 DPI render. Pages are
rendered and cropped in the document sandbox; the GPU process gets the crops only.

`recognize_pages(pages, max_new_tokens=2048)` reads up to 8 whole pages in one model
load, for the blank exam pages of the answer-area check (`grading/location_checks.py`;
requirement, 2026-09-25: starting the model took about as long as reading a page). Each
page is prepared by the same sandbox step as `recognize` and read the same way, one at a
time with greedy decoding, so each result is what `recognize` returns for that page. The
GPU process loads the model while up to four sandboxes prepare the pages. Eight pages of
the longest output stay inside the child's CPU limit.

`recognize_aliases(content)` reads the compact layout's top-right and bottom-left
recovery labels at four right-angle orientations. The isolated preprocessor
renders a PDF at 300 DPI, crops the two label regions, and enlarges each crop up
to 2× (one million pixels maximum per crop). A single offline CUDA model process
reads the eight crops using the fixed text prompt, with 64 tokens per crop and a
120-second inference limit. It returns raw readings and crop/source hashes;
it never receives catalog aliases or resolves identities. The separate
`document_identity.alias_ocr` service validates exact readings, automatically
links an unambiguous catalog match, and retains an audit report. This crop
profile targets `faculty-corners-10mm-v1`, not arbitrary
label positions or the legacy single-mark layout.

## Bounds and current limits

- Input: up to 25 MiB; PNG, JPEG, WebP, single-frame TIFF, or one selected page of
  a PDF of at most 100 pages. The default is page 1; the tool never silently OCRs
  a whole PDF. Use explicit calls for additional pages.
- Native PDF parsing/rasterization and image normalization use a bounded process
  with a 30-million-pixel source limit, 2 GiB address space and a 70-second wall
  limit. PDF rendering is 300 DPI; OCR input is then bounded to one million
  pixels for predictable memory use on this GPU. This may lose fine print.
- The model child receives only the normalized image. It uses offline loaders,
  safetensors, a private temporary directory, a CPU-time limit and an output-file
  limit. CUDA allocations are capped at 75% of GPU memory. CUDA virtual address
  reservations are not constrained with `RLIMIT_AS`; CPU inference is limited
  to 16 GiB of address space. These are practical limits, not a GPU driver sandbox.
- This is direct model recognition, not the separate PP-DocLayout pipeline for
  complex multi-region document layout. No model accuracy score is treated as
  proof that a particular extraction is correct. Handwriting, dense multi-column
  documents and arbitrary assignments need their own checks.

The loading API and fixed task prompts follow the
[official Transformers example](https://github.com/huggingface/transformers/blob/main/docs/source/en/model_doc/glm_ocr.md)
and [GLM-OCR model card](https://huggingface.co/zai-org/GLM-OCR).

## Verification

On 2026-09-14, all downloaded assets passed checksum verification and the offline
GPU child recognized:

1. The practice-assignment text from a retained classroom scan page (2.501 s model
   stage, 2,682,509,312 peak allocated GPU bytes; excludes startup/preparation).
2. `x² + 2x + 1 = (x + 1)²` as the expected formula.
3. A two-column table with the expected headers, items and quantities.
4. The explicitly selected second page of the three-page PDF fixture.

All four returned without truncation and remained marked for review. These small
smoke cases establish a working installation, not general OCR accuracy. See
`verification.json` for settings, hashes, versions and measured results. Raw
classroom content and model weights are not committed to this repository.

```sh
.venv/bin/python -m unittest discover -s tests -p test_faculty_ocr.py -v
```

The twelve focused tests cover bounded inputs, corrupt assets, concurrent calls,
verified Inbox reads, managed-storage boundaries, existing output preservation,
transparent image handling, selected PDF pages and malformed inputs, the crops and
hashes every preparation operation writes, and the child processes a failed batch
releases. No GPU or model download is needed for those tests; Bubblewrap is required
for preparation.
