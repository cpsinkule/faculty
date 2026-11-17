## Local OCR tools

Rule ID: **OCR**. Current binding module contract. Read root `AGENTS.md` first.

- `faculty_ocr` provides explicit Python/CLI GLM-OCR recognition. The instructor
  also requested a fallback for unreadable paired Data Matrix marks. With
  `FACULTY_ALIAS_OCR_ENABLED=1`, unreadable nonblank scan pages with no decoded
  barcode receive background recovery-label OCR. The instructor authorized
  automatic page identification when the readings contain one unambiguous exact
  alias matching issued, nonsuperseded paper; no confirmation click is needed.
  This does not authorize new handlers, grading, or general automatic OCR (the
  specific automatic reads authorized later are listed below). PICTURE-SEARCH's automatic pass over the pictures it names is not
  OCR: it reads no text from a picture (the words it matches are the catalog's own and a PDF's own text) and has its own
  authorization there.
- Weights and the separate environment under `/mnt/nvme/Teaching/runtime/glm-ocr/` (moved from `.tools/glm-ocr/` on the root disk,
  requirement, 2026-11-06) are reproducible
  workspace runtime assets, not teaching payload owners. Pin model revisions and
  checksums. Use the offline, isolated runtime; never enable remote model code or
  silently substitute a hosted API. Follow `faculty_ocr/README.md`.
- OCR outputs are untrusted drafts. Only these may identify a printed page or its
  student automatically, each after server-side catalog validation and under its own
  contract's conditions: the exact-alias fallback; a confident name/UIN match on the
  first page of a paper printed without labels, for any assignment (instructor,
  2026-09-24; documents.md); a confident match on a quiz copy printed for no
  student (quizzes.md, NAMES ON UNNAMED STAMPED COPIES); and a bubble sheet's
  confident student match (bubble-sheets.md). Jev's reading of a scanned first page is not OCR: it identifies only under
  documents.md "JEV PAPERS", where the OCR reading stands whenever Jev does not answer. OCR cannot
  select an executable action or clear the Inbox instructor-review gate.
  Recovery-label reports and
  their durable `scan_alias_ocr` queue reference the existing source Inbox
  message/page, use its verified storage manager, and preserve the original
  barcode observations. No fuzzy matching or guessed character substitutions;
  conflicting aliases remain unresolved. Automatic matches retain an audit
  record and the same duplicate/changed-content rules as barcode identification.
  Identity, report receipt and audit records commit together. A manual correction
  made while OCR runs takes precedence. Interrupted attempts require explicit
  retry. Other OCR calls remain transient unless their own contract records the
  reading (`copy_name_reads`, bubble sheet readings); future persistence must use
  the source owner's verified storage manager.
  STICKER ALIASES (2026-10-24, home-scanned Exam 2: stickers whose code ran off the sticker's top edge held whole stacks until the
  instructor typed the alias printed under the code): on a scan labeled with peel-off stickers only, which the corner fallback
  skips, the first page of each paper the grouping holds for a label not read (a run of pages in no paper, a whole number of
  papers long at the usual label spacing, `SequenceService._read_missing_labels`) is read once, in its top band where the
  corner positions show no alias (`AliasOcrService.read_sticker`); every `A1-` alias in the band is a read, and the ordinary exact-alias rules decide. The three
  clipped stickers of that day read exactly, about 7 s a page.
  Read retained inputs with `recognize_artifact`/`InboxService.read_artifact`, never
  directly from NVMe/HDD payload paths. Escape OCR text before displaying it as HTML.
- `document_identity/version_sort.py` sorts unregistered scan pages by a printed
  version label only on an explicit request from the scan's Scan page (DOCUMENTS). Readings
  are exact pattern matches on band OCR (`faculty_ocr.recognize_bands`, two
  rotations); unlabeled and blank sides follow the labeled page before them
  within `pages_per_quiz`; everything else is Unsorted. Upside-down sheets are
  turned in the outputs only (label rotation, then the quiz's front, then a
  decisive text-amount comparison); retained originals never change. Outputs (one PDF per
  version, Unsorted, JSON report) are immutable artifacts of the source Inbox
  message. `scan_version_sorts`/`scan_version_reads` are retained queue records:
  one live request per scan, `MAX_QUEUED` overall, one page per worker turn,
  explicit retry after failure or interruption. Sorting never identifies paper,
  changes observations, clears the review gate or dispatches an action.
