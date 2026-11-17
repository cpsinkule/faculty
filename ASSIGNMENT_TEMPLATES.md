# Grading templates

Open **Exams → Manual scores → Manage grading templates** (the tab was named
Grading, and the assignment wizard's PDF version step, now Paper, offered it too, until 2026-09-26;
blank templates for assisted grading are chosen in Grading's first step). Choose an existing
course assignment, label the template, and upload:

- One PDF, including an ordinary PDF without Faculty print marks.
- One DOCX. LibreOffice Writer converts it locally with no network access.
- LaTeX (`.tex` or `.latex`), optionally with supporting `.tex`, `.latex`, PDF,
  PNG, or JPEG files. Choose the main filename when uploading several LaTeX files.
  The existing pdfLaTeX toolchain supplies standard packages and the Faculty class.

The original uploads remain unchanged. Preparation produces a flattened reference
PDF, 300-DPI PNG images in page order, native text from each page, geometry, and
conversion provenance. Visible PDF crop boxes and rotation define the normalized
page. Native text is kept separately because the reference PDF is image-based.
These outputs make the page coordinate system explicit for OCR/VLM consumers.
They are grading references and do not acquire the print layout's barcode or
physical qualification.

A template labelled **DR copy · <version>** is made by Faculty itself: the accessible Word copy of that version, rendered here
(GRADING "DR COPIES"). It is not the DR center's print and is never aligned with a scan or mapped: a DR paper's answer area for
each problem is that problem's whole sheet.

## Map the handwritten work

1. **Add problem**, name it, and set its maximum points.
2. Tick every **Answer page** where its work should appear. Selected pages use
   the whole page initially; one problem can span several pages.
3. Optionally drag a rectangle on the page preview. **Add area on this page**
   supports separate answer spaces on the same page. **Use full page** resets the
   selected area. **Area coordinates** provides equivalent keyboard input.
4. **Save mapping** creates an immutable revision. Earlier mappings remain saved.
5. In an exam, choose its printed document version and **Apply saved mapping**.
   This explicitly creates/pins that exam's rubric from the saved revision.

Compare the converted page breaks, orientation, and answer spaces against the
paper students received before applying a map. The template and printed version
must belong to the same assignment and have the same page count. Main and makeup
versions remain independent. An applied map does not follow subsequent template
edits automatically. Stale saves are rejected while keeping the browser draft.

Grading displays every selected area for the chosen problem on the student's
existing reviewed submission. **Show full page** restores context. If any selected
page is missing, the existing explicit missing-page acknowledgment is required.
Template mapping does not infer page identities, select a submission, or clear
Inbox review gates. One-label grouped packets retain their existing identity
limits. Manual rubric edits preserve additional regions while creating their own
rubric revision; the earlier template-derived provenance remains in history.

## OCR/VLM reference package

**Download OCR/VLM package** exports a ZIP with:

- Original uploads and retained LaTeX build evidence, when applicable.
- `reference.pdf` and `pages/<number>.png` at 300 DPI.
- `pages/<number>.txt` with native template text, which can be empty for image PDFs.
- `manifest.json` containing checksums, conversion settings, page dimensions,
  the exact saved mapping revision, and problem regions.

Paths in the manifest are relative to the ZIP. Page numbers start at one;
rectangles use a top-left origin with `x`, `y`, `width`, and `height` expressed as
fractions of the displayed page. The image dimensions convert those fractions
to pixel crop bounds. Document text is data, not model instructions. This feature
prepares and applies grading regions; it does not invoke a model, propose scores,
finalize grades, or send student work to a hosted service.

## Ownership, retention, and APIs

`assignment_templates` references the existing course-wide `assignments.id`.
`assignment_template_revisions` retains immutable maps of that exact upload.
Neither table is owned by a saved offering, term, or section. Original files,
reference PDFs, page images, text and checksum manifests share the existing
definition owner's verified storage under `grading-templates/<id>/`.

Conversion runs outside owner locks using only uploaded scratch copies. Publication
reacquires the roster lifecycle lock, then the definition's storage/owner lock,
then the catalog transaction. Creation UUIDs bind exact upload contents, label,
assignment, and entrypoint, preventing duplicate effects on retries. Mapping saves
use optimistic versions; unchanged maps reuse their revision. Labels and uploaded
bytes are immutable; a replacement upload uses a new label.

Nightly definition snapshots verify the saved manifest and every referenced
artifact hash before a backup can be verified or a working copy purged. Shared
templates remain on NVMe while any course term is current. The catalog backup
retains maps and rubric provenance. Existing assignment reference protection
prevents course identity edits from orphaning these resources.

Private endpoints retain the normal network boundary, same-origin write checks,
storage guards, and `no-store` policy; no login or prerequisite Home visit exists:

- `GET /api/assignment-templates?assignment_id=…`: saved upload summaries.
- `POST /api/assignment-templates`: multipart `assignment_id`, `label`,
  `request_id`, optional `entrypoint`, and one or more `files`.
- `GET /api/assignment-templates/<id>`: previews and latest mapping. Optional
  `revision_id` selects an earlier retained map.
- `PUT /api/assignment-templates/<id>/mapping`: `version` and `questions`.
- `GET …/<id>/pdf`, `…/pages/<number>.png`, `…/sources`, `…/manifest`, and
  `…/package`: verified outputs; manifest/package accept `revision_id`.
- `POST /api/exams/<id>/grading/template`: exact `template_revision_id`,
  `assignment_version_id`, and current rubric `version`.

`exam_rubric_template_sources` binds an immutable rubric to its exact template
map and checksum. Existing term exams pin their rubric separately. The new
`assignment_templates` and `exam_templates` migration namespaces run under the
roster lifecycle lock in a catalog transaction. Keep applied SQL immutable.

## Local converters and bounds

Native parsing and conversion require Bubblewrap. PDF processing uses the pinned
pypdf, PDFium, Pillow, and ReportLab packages already installed with Faculty.
LaTeX reuses the existing compiler without shell escape or network access; an
explicit entrypoint does not overwrite another uploaded `main.tex`.

`manage_template_converter.py` extracts authenticated distribution packages into
`/mnt/nvme/Teaching/runtime/libreoffice-v2`, recording package hashes. It changes no
system packages or services and refuses to replace an existing runtime. The
converter discovers that runtime automatically; `FACULTY_LIBREOFFICE_ROOT` can
select another extracted runtime. LibreOffice's profile and font cache are
disposable conversion scratch. Only the runtime, toolchain, code, and scratch
are mounted; catalog, credentials, and teaching owners are unavailable inside
the sandbox. See the [official LibreOffice command-line documentation](https://help.libreoffice.org/latest/en-GB/text/shared/guide/start_parameters.html).

The runtime includes Writer, Math, Liberation fonts and Latin Modern fonts.
DOCX math objects require the Math component; the converter refuses to silently
drop equations if a custom runtime lacks it. Conversion provenance records the
component's presence and source equation count. Word and LibreOffice can paginate
the same document differently even when its equations render correctly. A DOCX
saved by Word records the page count of Word's own layout (`docProps/app.xml`).
When the local conversion produces a different number of pages, the upload is
refused with that explanation: its page breaks moved, so every answer area would
land on the wrong page of the students' papers and scans would align poorly.
Provenance keeps the recorded count as `word_page_count`; a count written by
another editor, or none, is no evidence and refuses nothing. For an already
printed exam, use its original exported PDF (Word: File → Save As → PDF) or a scan
of one blank copy, and inspect the preview before confirming locations. A scan of
a blank copy has exactly the students' print scale and scanner geometry. Never
force a reflowed document to match by renumbering pages.

Uploads are limited to 25 MiB combined and 100 source files; templates to 100
pages, 30 million pixels per page, two million native-text characters, and 256 MiB
of prepared artifacts. DOCX unpacked contents are bounded. Native conversions have
CPU, memory, file-size, and wall-time limits. Invalid, encrypted, oversized, or
unconvertible input fails without registering a partial template. No unisolated
converter or hosted fallback is used. All scratch follows the configured NVMe
`TMPDIR` and is not retained teaching storage.

## Verification

`python3 -B manage_checks.py quick` includes template service, private API,
retention, backup corruption, multi-page grading and provenance checks. Native
conversion tests in `tests/test_template_conversion.py` exercise PDF geometry,
real DOCX page breaks and equation rendering, LaTeX entrypoints, limits, and sandbox failures. The
existing compiler/print-layout tests retain their separate physical contracts.

```sh
python3 -B manage_checks.py browser --browser assignment_templates --browser exams --browser assignment_wizard --keep
```

Browser tests exercise upload, drawing, numeric areas, multi-page mapping, stale
drafts, explicit application, grading crops, wizard return and the 1440 and 1280 px
desktop widths. These use synthetic paper and do not establish OCR accuracy or physical
printer/scanner qualification.
