# Printed document identity

Paper PDF and Labels are separate actions, with the remembered label stock shown directly. The Paper step can preview the selected immutable, unstamped canonical through `GET /api/documents/versions/<id>/preview`. Storage-manager locks and its retained digest protect that read; preview allocates no copies or stamps. Only Stamp and print creates printable identities.

The assistant can submit reviewed LaTeX through the existing assignment-version compilation route. The material bridge requires
an exact approval and retains a request receipt; this creates the native version without creating print jobs or bypassing the
compiler sandbox and source verification (ASSISTANT, DOCUMENTS).

Start from **Assignments** to create paper and prepare grading together. Each
assignment's Create area contains its paper and print jobs; Prepare contains its
questions, solutions, rubrics and answer areas. Printing can proceed while grading
preparation is unfinished. Existing print-job and creation links remain supported
inside the assignment workspace.

Faculty issues opaque Data Matrix ECC200 page identities, validates printed PDFs,
and attaches scanned pages to the existing Teaching Inbox review item. There are
no quiz, exam, grading, or automatic scanner action handlers.

Blank PDF, DOCX, and LaTeX [grading templates](../ASSIGNMENT_TEMPLATES.md) reuse
course-definition storage and the bounded document sandbox. Their reference PDFs
and page maps do not become stampable assignment versions or inherit a layout's
physical qualification. Definition backups verify their retained artifact hashes
alongside canonical print sources.

## Printed-label OCR fallback

With `FACULTY_ALIAS_OCR_ENABLED=1` in both the private app and Inbox worker,
newly inspected nonblank pages with **no decoded barcodes and no selected page
identity** receive background GLM-OCR of the printed recovery labels. It targets
the paired `faculty-corners-10mm-v1` layout. The original single-mark layout's
label position is not covered. Existing resolved pages are never reprocessed.
One readable barcode is sufficient to skip OCR. Unknown, malformed, conflicting,
cancelled or superseded barcode references also stay on their existing review
path; OCR cannot override them.

The local model receives only cropped pixels and the fixed text-recognition
prompt. It reads the top-right and bottom-left labels at four right-angle
orientations, with bounded offline GPU inference. Since 2026-10-01 (crop profile
`faculty-corner-alias-v2`, `faculty_ocr.prepare.alias_boxes`) each crop is the
printed alias line itself, measured in millimetres on Letter or A4 with about 2 mm
of paper around it and neither the code above nor the copy caption beside it; the
first crops, fixed page fractions, cut the top-right alias in half on Letter and
missed it on A4. Only a complete, exact
canonical `A1-` alias that resolves to issued, nonsuperseded paper identifies the
page automatically, without a confirmation click, as requested by the instructor.
No case correction, fuzzy matching, or substitutions such as `O` → `0` are used.
Different exact aliases across crops produce a conflict, even if only one is in
the catalog. Truncated or unrecognized outputs cannot identify a page.

**Scanned page review** shows the page as resolved with **Identified automatically
from printed alias**. The server links the exact catalog match, preserving the
original barcode evidence, and applies the same duplicate/changed-content rules
as barcode identification. It commits the identity, OCR report receipt and audit
events together. A failed publication/transaction cannot leave a recovered page
without its report. Conflicting or unmatched readings remain unidentified and
available for manual recovery. An optional correction with a reason remains
available for any page through **Recover using printed alias**.

Page identification never selects a reviewed assembly, clears the Inbox
instructor-review gate, or runs an Inbox action. Reports remain available in a
separate collapsed **Recovery label OCR** file group. Historical suggestion
reports remain immutable; the automatic policy applies to newly completed OCR
attempts, including explicit retries of failed attempts.

Ownership: migration `003_alias_ocr.sql` adds `scan_alias_ocr`, a durable queue
referencing the existing scan page and Inbox message. The worker shares the
cross-process Inbox execution lock and processes one page per turn. Report JSON
is an immutable artifact of that same Inbox message under its verified storage
manager, including the source hash, model revision, crop positions/hashes, raw
readings, exact-match result and attempt number. The normal verified nightly
backup covers both report payloads and queue/catalog metadata. The model weights
remain reproducible workspace runtime assets, not teaching payloads.

Failed/interrupted attempts never silently retry. Use **Retry recovery label
OCR** in the private review screen. A manual correction made while OCR runs wins;
the OCR attempt is skipped without replacing that identity. Disabling the flag
pauses queued OCR and prevents new requests. There is no hosted fallback.

Install/verify the [local runtime](../faculty_ocr/README.md) first. Stop the app,
Inbox worker and email gateway during the schema update; retain a verified
pre-migration catalog snapshot, initialize the idempotent migrations, run the
coordinated backup, then restart the existing services with the flag enabled.
Do not expose the private app or the OCR API through the email gateway.

On 2026-09-14, GLM-OCR exactly read all six labels on classroom sheets 4, 5 and
17—the sheets with top-right marks still unreadable after barcode diagnostics.
This was an OCR check on retained scans, not another physical qualification run.
The originals already resolved from the bottom-left marks and are unchanged.
See `alias-ocr-verification.json` for the additional masked-code/rotation checks.

```sh
.venv/bin/python -m unittest discover -s tests -p test_alias_ocr.py -v
.venv/bin/python tests/browser_alias_ocr.py
```

## Sorting unregistered quizzes by printed version

Quizzes printed without Data Matrix marks, carrying only a header such as
"Version A", can be sorted on request from **Scanned page review → Sort by
printed version**. The Inbox worker reads each nonblank page's label band with
the local GLM-OCR runtime (`recognize_bands`, the top or bottom quarter of the
page or the whole page, at rotations 0 and 180), matches the chosen pattern
exactly (presets for Version/Form letters and numbers, or a custom
case-insensitive regular expression with one capture group; no case repair,
character substitution or guessing) and keeps the unlabeled sides that follow a
labeled page with it. `pages_per_quiz` defaults to 2 for two-sided quizzes
scanned front then back; blank backs skip OCR and count as pages. Conflicting
readings, unlabeled leading pages and pages beyond the quiz length go to an
Unsorted PDF. Sheets fed upside down are turned the right way up in the
outputs (`auto_rotate`, default on): a page whose label was read at 180 degrees
is rotated, the sides that follow it in the same quiz turn with it, and a page
with no front to follow is turned only when one rotation read decisively more
text (`text_orientation`). Rotation sets the page's `/Rotate` attribute in the
working copy; the retained single-page originals are unchanged. Outputs are one
PDF per version, an Unsorted PDF and a JSON report (per-page readings, outcome,
assignment, orientation and reason), retained as
immutable artifacts of the same Inbox message under **Sorted by version** and
covered by the nightly backup. Sorting again creates a new attempt with its own
files; earlier outputs stay.

Queueing: migration `004_version_sort.sql` adds `scan_version_sorts` (one row
per message and attempt; a partial unique index allows one live request per
scan) and `scan_version_reads` (one row per page). The worker claims the oldest
request and reads one page per turn under the shared execution lock, so a long
scan never blocks mail or other queues; the last page also builds the PDFs. The
API returns 409 for a scan already queued or running, and for any new request
once `MAX_QUEUED` (10) scans are live; the review screen shows queue position
and pages read, and polls while a sort is live. A failed or interrupted
attempt keeps its finished readings and needs **Retry version sorting**, which
re-reads only the remaining pages. A busy OCR runtime (another explicit OCR
call) leaves the page queued without failing. Sorting requires authenticated
scanner intake with completed inspection and the installed OCR runtime. It never
identifies paper, changes observations, clears `instructor_review_required` or
dispatches an action; the sorted PDFs are working copies for the instructor.

```sh
.venv/bin/python -m unittest discover -s tests -p test_version_sort.py -v
.venv/bin/python tests/browser_version_sort.py
```

## Setup and migration

Use Python 3.12+, the pinned root `requirements.txt`, Bubblewrap, and pdfLaTeX:

```sh
.venv/bin/python -m pip install -r requirements.txt
sudo apt-get install bubblewrap texlive-latex-base texlive-latex-recommended texlive-pictures texlive-latex-extra
```

The Linux host must permit unprivileged user namespaces. Processing fails closed
if Bubblewrap cannot start; it never falls back to running uploaded LaTeX or PDF
parsers against the workspace filesystem. Containers also need working user
namespaces. The native Faculty deployment is the tested execution environment.
`FACULTY_TEXLIVE_ROOT` optionally points to an extracted, administrator-prepared
TeX Live tree with `usr/bin/pdflatex` and `formats/pdflatex.fmt`; it is mounted
read-only in the compiler sandbox. Normal system installs need no override.

Before activating this release, stop all Faculty app, Inbox worker, and email
gateway processes, and prevent a scheduled backup from starting during migration.
Keep a verified catalog backup. Old running processes must not write the catalog
after migration: permanent sections gain storage columns, and old positional
section inserts are incompatible. Starting the updated app applies the additive,
idempotent migration under the roster lifecycle lock. Restart the existing
`faculty.service`, `faculty-inbox.service`, and `faculty-email-gateway.service`,
and restore the existing backup timer. The public listener remains port 3001;
the private app remains port 3000. No Cloudflare/address/secret changes are needed.

The existing worker runs print generation, non-executing scan inspection, and the
existing Inbox job queue:

```sh
.venv/bin/python manage_inbox.py worker
# One bounded work item, including document jobs:
.venv/bin/python manage_inbox.py worker --once
```

For an isolated development workspace, pass `--data-dir /mnt/nvme/datamgmt/faculty-demo` to
`manage_inbox.py` and run the app with `DATA_DIR=/mnt/nvme/datamgmt/faculty-demo`
(keep scratch off the small root disk). Do not point a development instance at the
live catalog. An explicit `DATA_DIR` selects an isolated workspace even beside the
deployed `.faculty-storage.json`, unless `FACULTY_REQUIRE_STORAGE=1` is also set;
before serving a copy, print `create_app().config["DATABASE"]` and check it.

## Author and print

Since 2026-10-18 every page Faculty compiles carries two 7 mm Data Matrix marks
(`faculty-corners-7mm-v1`): the page token packed as raw bytes into a 20x20
symbol, centred in 8 mm boxes 10 mm from the top-right and bottom-left edges,
with no printed recovery alias; page text starts 20 mm from the top and bottom.
`compactcorners` keeps the earlier 10 mm corners and `pagecode` the single 20 mm
mark. Peel-off labels keep the 10 mm mark. Scan pages are drawn at 200 DPI and
their marks read from a 300 DPI drawing; see DOCUMENTS "SEVEN-MILLIMETRE CORNER
CODES".

1. Open **Assignments & printing**, also linked from each course Home, and
   choose **Create assignment → Create paper and print**. One window runs
   the whole process: **Details** (course, title, assignment key — filled in from
   the title with an Advanced override — and the sections it is for, all ticked by
   default), optional **Seating** (requested in Details or already attached), **Paper**, and **Stamp and print**. Steps
   already passed are links back in the window's step line. The key and title are
   retained identities; the key cannot be edited later and the title can be renamed. **Finish later** keeps what
   exists; **Continue** on an assignment's card reopens the window for it, which
   is also how you register a further version or print more paper.
   Each card shows the assignment in its course's current term as three derived
   stages, **Paper**, **Seating** and **Print** (the catalog's read-only
   `lifecycle`, computed by the rule the exam hub shares:
   `exam_workflow.overview.assignment_stages`; registration, superseded and
   cancelled jobs never count), with the next unfinished stage as its one primary
   action. Each stage opens the window at its step
   (`#documents?assignment=<id>&step=details|seating|version|print`, consumed
   once); an exam record, or a grading plan that holds responses, adds a link.
2. **Paper** offers three choices. **Faculty prints it from LaTeX**: copy
   `templates/example.tex` to `main.tex` (both are downloads there:
   GET `/api/documents/starter/main.tex` and `.../facultyassignment.cls`), using
   `\documentclass[letterpaper]{facultyassignment}` or `a4paper`. Upload `main.tex`
   and any relative `.tex`, PDF, PNG, or JPEG assets. Faculty supplies the class.
   **Add another version** adds a row (label and files) for each further version,
   labelled with the next free letter; Next compiles them in turn, and a row that
   registered before a later one failed is not sent again. The two demonstration
   fixtures can be registered without a compiler, but only on a page marked for
   testing (`<html data-testing="1">`, which the browser checks set); the Source
   choice is hidden otherwise. **Printed elsewhere: label each paper (stamps only)**: a
   seated exam chooses its pages per paper when each chart is exported; sections
   without a chart reuse the stamps-only version such an export registered (there
   is no separate way to register one). **Use a registered PDF** lists real PDFs
   only. Grading templates, rubrics and answer areas are prepared in Grading.
3. Give every source revision a new immutable version label. Faculty retains
   input bytes, the sorted filename/content-hash manifest, build log, toolchain
   details, unstamped canonical PDF, its separate SHA-256, and its page count.
4. In **Stamp and print**, the sections without a seating chart print **one stack**:
   a single job owned by the term that names no section, so any copy can go to any of
   their students (requirement, 2026-10-01; see "Print jobs owned by the term" below).
   It takes a number of **physical copies** (defaulting to those sections' students).
   **One copy per student on the roster (named)** links each copy to one of those
   students; otherwise **Choose students copy by copy** picks students by name, grouped
   by section (`GET /api/documents/terms/<term>/enrollments` returns every section's
   roster names, read under the roster lock). Extras and partial-roster prints are
   supported. Each seating chart is confirmed there
   with its usual windows (issues, export, response cards), one print job per
   section. **Continue** on an assignment already seated in a current term starts
   with its charts attached, and a chart confirmed earlier shows its latest export
   (jobs and room files) before the secondary **Export again (new stamps)**.
   The Print jobs list groups jobs by export (the catalog's read-only `export`
   request prefix and `origin`: `seating`, `section`, or `registration` for
   first-page registration jobs, which are listed apart as not for printing).
   **Print more copies…** issues a new job (the same copies again, or extra
   unassigned copies); **Supersede** asks for a ticked confirmation first.
   A section without a chart that already has a current job of the version on
   screen names it (an earlier stack with its interleaved Print PDF first), and its
   copies start at 0, so continuing never prints twice by accident; jobs of other
   versions are named apart ("Other versions printed") and leave the default alone.
   **One stack, every version** takes the copies of each version (the students
   shared out) and **Create print jobs** queues one term job per version under one
   request prefix: the Print jobs list shows them as one group whose Print PDF
   interleaves the versions, as for a drafted quiz. It holds the versions registered
   together with **Add another version**, or, when the window opens on its own
   default paper (Continue, a card's Print stage), every PDF version that is not an
   earlier layout; none may be printed for these sections yet. Then it leads, with
   **Print one version only** under a disclosure. A version registered alone or
   picked under **Use a registered PDF** prints alone first, with **Print every
   version as one stack** folded under it, so a revised version never prints beside
   the one it replaces by default. If one version's job fails, the rest are queued
   in the same stack while the window stays open. Each job also says how many of its copies **came back in scans**
   (`scanned` on the batch and a count on each catalog batch: a page observed in
   a scan still in the Inbox, `PRESENT`; a deleted submission counts nowhere),
   with the copies not seen yet and links to the scans.
5. Wait for `ready`, open the print job, and download its private PDF. The worker
   has rasterized every page at 200 DPI, recovered its exact persisted token,
   and checked barcode position, physical size, and clear quiet zones.
6. Print **Actual size / 100%**, with the printer's **Copies set to 1**. The PDF
   already contains every copy. Duplex is supported when each intended side is
   explicitly represented by a page in the canonical PDF; printer-added blanks
   have no page identity.

### Draft a quiz with a model

**Assignments & printing → Draft a quiz with a model** takes the course and term, the quiz's title, points, number of versions and
scoring, what it should cover, your material (PDF, pictures, text or LaTeX; up to 12 files) and up to two past quizzes to imitate
(their blank templates). The Rubric drafting and review model writes every version at once; Faculty lays each version out in the quiz
layout (`templates/facultyquiz.sty` inside `facultyassignment`), compiles it in the sandbox, sends anything that does not compile or fit
back to the model, has the model check the printed pages once, and drafts each problem's rubric for all versions. The draft's page
(`#documents/quiz/<id>`) shows every version's pages, each problem's rubric (shared points, each version's wording), solutions and
anything to look at. **Ask the model for these changes** makes a new revision with your comments (per problem or overall);
**Approve and print…** creates the assignment with one printed version per quiz version, sets up grading (questions and rubrics,
templates and answer areas confirmed, rubrics confirmed for the rubric review; Key & Rubric Finalization still waits for you) and
queues one unnamed print job per version for the sections ticked, printed as one stack owned by the term. Nothing printed on the
page says the version (2026-10-01): no caption beside the stamps, and the quiz's header and footer leave it out ("Page 1 of 2");
only the stamps say it. The Print page lists the quiz as ONE group whose **Print PDF** has the versions
interleaved (A, B, C, D, A, B …), and the draft's page links the same file: hand it out from the top in any section and every
section gets all the versions, neighbours different ones. Each version's own PDF stays under Other formats. When the scans come back, the stamps say each paper's version, and the
name and UIN written on page 1 say whose it is and so its section (read by the worker against every ticked section's roster; a
confident match names the paper, the rest wait on the scan's Students page).

### Seating in Create New Assignment

**This assignment requires a seating chart** starts unticked; the two controls
indented beneath it stay disabled until it is ticked.

- **Generate new seating charts** opens the usual New seating chart window on
  Next, limited to the ticked sections (or to those an attached chart leaves
  out) with the exam fixed.
- **Attach existing seating chart** lists the course and term's charts grouped
  by exam and checks each against the ticked sections. A chart that seats an
  unticked section cannot be attached; ticked sections it leaves out are
  reported, and can be generated or printed without seats.
- When the chart's exam is named differently from what you typed (a DR testing
  export names exams its own way), you choose. **Override** creates your
  assignment, moves the exam's charts for that term to it, and relinks the
  earlier exam's DR bookings of that term so the same students stay excluded. It is
  unavailable once a chart is confirmed or has print jobs, response cards,
  emailed student charts or a linked exam, because that material carries the
  earlier name. **Keep** continues with the chart's assignment and creates
  nothing. A later DR export under the earlier name links to the earlier
  assignment again and appears on the chart as bookings to relink.

The original layout contract is `faculty-page-v1`: portrait Letter/A4, matching MediaBox
and CropBox, normalized origin, ordinary user units, and no annotations. Supported
right-angle PDF rotation is normalized before checking the visible page. The
25 mm box is 10 mm from the right/bottom edges. Its upper 20 mm holds a 24×24
Data Matrix symbol and a one-module quiet zone (26×26 total; 0.76923 mm/module).
The lower 5 mm holds an independent `A1-` recovery alias. The class (option
`pagecode` since 2026-10-01) reserves a 40 mm bottom content margin; `\identifiedblankpage` emits an intentionally blank
side. Missing contracts, unsupported geometry, or content in the stamp box fail
validation. The canonical bytes are never edited.

### Two 10 mm corner marks (`compactcorners`; the default from 2026-10-01 to 2026-10-18)

From 2026-10-01 to 2026-10-18 the class laid out `faculty-corners-10mm-v1` by
default ("Use the two corner 10mm", instructor, after asking whether the 20 mm
codes had to be so big). Since 2026-10-18 the default is the 7 mm layout
(`faculty-corners-7mm-v1`, see "Author and print" above) and this 10 mm layout is
chosen with the `compactcorners` option: `\documentclass[letterpaper,compactcorners]{facultyassignment}`
(or `a4paper,compactcorners`). `pagecode` keeps the original single
20 mm mark (`faculty-page-v1`); versions made before keep their layout. Each page has two
10 mm Data Matrix squares including quiet zones, one at top-right and one at
bottom-left. That is half the original mark's width and height. Both contain the
same page token; they are redundant observations of one identity, not two page
records. Each 15 mm reserved box sits 10 mm from its adjacent page edges, with a
5 mm alias band below the barcode. With `compactcorners` the class reserves 30 mm top/bottom margins.
The version, batch configuration and page placements retain this separate profile;
issued original-layout batches and their output files are unchanged.

The print validator requires exactly two correct, correctly placed marks for this
profile. Missing, displaced, additional or conflicting marks prevent publication.
Scan intake accepts either surviving mark, deduplicates matching payloads, and
retains distinct detected locations. The review UI reports matching-mark counts.
Different teaching tokens remain ambiguous. No review gate or action policy changes.

The smaller layout passed 168 required software cases, including either corner
alone at 200 DPI. Characterization found less tolerance for severe blur (0/12 at
150 DPI with sigma 2); bottom-left alone at 150 DPI recovered 10/12, while
top-right alone recovered 12/12. All unmasked 150-DPI pages recovered correctly;
no incorrect identity was accepted. See `corners-qualification-results.json`.
Its separate classroom run received on 2026-09-14 UTC resolved all 30 pages into
ten complete copies. Bottom-left decoded on 30/30 pages, top-right on 22/30; the
second mark preserved identification on eight pages. This qualifies page identity
for the received run, not either-corner-alone recovery on every page. The covering
procedure and device settings are unconfirmed. See `corner-test.md` for Test 2.

```sh
.venv/bin/python -m document_identity.qualification --profile faculty-corners-10mm-v1 --output test-results/document-corners-qualification
```

**New print batch** allocates fresh tokens and records the original batch as the
reprint source. Reusing a downloaded PDF or photocopying it duplicates its tokens;
software cannot distinguish those pieces of paper by their stamps. For a job printed for
no student (a quiz approval's or a term job), each photocopied paper is moved to an
unused copy of its job and named from the paper (QUIZZES "PHOTOCOPIED STAMPS",
`photocopies.py`). Before `ready`, a job can be
cancelled. After `ready`, only **Supersede batch** invalidates its provenance.
Tokens remain retained in both cases. Failed or interrupted jobs require explicit
**Retry same job**, which retains all copy/page/token allocations. Additional
physical output always requires a new batch.

## Print jobs owned by the term

"If an assignment is printed this way with no seating chart, it shouldn't assign
sections to the barcode. I intend to mass print these out and not separate stacks
for each section, so I can't guarantee that a given section gets the right copies"
(requirement, 2026-10-01). Migration `013_term_print_jobs.sql` therefore lets a print
job name no section: `print_batches.section_id` NULL and `term_id` set. Its stamps
resolve to the course, term, assignment, version, copy and page; the job lists the
sections it is for (`print_batch_sections`), which scope name reading, the student
pickers and the scan's grading link. A copy can be named for a student of any
section of the term, and is then that student's paper in their own section:
collection, grading, Gradescope and the gradebook all follow the student. Seated
jobs keep their section (the chart puts each student's copy on their seat), as do
jobs printed earlier and the label jobs of exams printed without labels.

The migration rebuilds `print_batches` (SQLite cannot drop a NOT NULL in place) in
one transaction with foreign-key enforcement suspended for that transaction only;
every reference to the table is checked before commit and each row keeps its rowid.
Like any catalog migration, stop the app, Inbox worker and email gateway before the
updated code first starts.

```sh
.venv/bin/python -m unittest discover -s tests -p test_term_print_jobs.py -v
```

## Seating charts and peel-off labels

A print job can take a seating chart (Seating charts in the sidebar) for the
version's assignment and section. Copies are allocated one per seated student
in seat order plus optional extras, `printed_copies.enrollment_id` is set from
the chart, and `seat_label`/`caption` record what the stamp prints beside the
mark ("Seat K5 · Example, Alice · Sec 507 · HECC 105"). The caption is drawn
in the class's reserved bottom band next to the stamp box and never inside it;
mark size, placement, quiet zone, and validation are unchanged, and the
barcode still encodes only the page token. With `seat_marks: true` on the
batch request (the form's "Print each student's seat number beside the stamp",
stored in `print_batches.seat_marks` and part of the request hash) each seated
copy's manifest entry also carries `seat_mark` (the seat label) and the
renderer draws it in bold 13 pt beside each stamp box, top-aligned with the
box (`layouts.seat_mark_positions`: left of the single default box; left of
the top-right and right of the bottom-left corners boxes), 2 mm clear of the
box. Unseated extras and batches without a chart get none. `POST
/api/documents/batches/<id>/seat-marks` with `{"seat_marks": true|false}`
(`DocumentService.set_seat_marks`) changes the choice for a pending or failed
job under the batch lock and records a `seat_marks` event; an issued job
refuses (409), as does a job without a chart (400). The creation request's
hash is left as created.

`GET /api/seating/<chart>/room-export/print?batches=a,b` and
`.../room-export/labels?media=<media>&batches=a,b` (`DocumentService.room_export`)
return one file for a whole room from the ready, current jobs of one chart
export: seated copies in seat order across sections, unassigned extras after,
pages together per copy. The print PDF and roll labels are re-cut page by page
from the retained, verified per-section files in the sandbox (`reorder`
operation: no rendering, tokens untouched, `/FacultyRoomExport` metadata);
sheet labels are rendered afresh from the merged manifests through the normal
`labels` operation and validated. Nothing is retained or issued by this; the
per-section files remain the record.

`GET /api/documents/batches/<id>/labels?media=<media>` renders peel-off labels
with one label per page stamp: the layout's label mark at the right (20 mm for
the original layout, 10 mm for the corner layouts, the 7 mm layout included), and
seat, name, section, exam, room, copy/page and recovery alias text at the
left. Media are `dk-1201` (the default: Brother DK-1201 29 x 90 mm address
labels for the QL-800, one label per PDF page, stored rotated so the page is
the printer's portrait "29mm x 90mm" paper size), `dk-1209` (62 x 29 mm
labels, or 62 mm continuous tape cut at 29 mm, one label per page) and
`avery-5160` (30 labels per US Letter sheet, with the alias under the mark).
Validation of every stamped page and label decodes each mark, matches its
token, and compares the decoder's reported corners with the layout's expected
outline to within two raster pixels at 200 DPI, then checks the quiet zone.
Because the decoder's corner estimate can wander a pixel or two on some symbol
patterns while the printed mark is exactly where it should be, a corner miss
falls back to the mark's own outline: the extents of dark pixels inside the
barcode box (nothing else is drawn there) must match the expected outline to
the same tolerance. That keeps a correctly placed mark from being rejected on
decoder jitter without loosening the placement rule.

Print roll labels on the QL-800 at 100% with the matching paper size selected;
nothing is scaled, and the mark and text stay inside the printer's printable
area. Each media's file is built and validated in the sandbox (`labels`
operation, which also checks the page size and the mark's placement), published
beside the print output as `labels-<media>.pdf` (`labels.pdf` for Avery),
recorded in `print_batch_labels`, and verified by SHA-256 on later downloads.
Only ready, non-superseded batches have labels. Stick each label over the
reserved stamp box of the matching copy and page when the exam was printed
without stamps.

## Print at Home

Open a ready print job and choose **Print at Home** beside the file it prints: the
job's PDF, a stack's **Print PDF · every version interleaved**, or a seating chart's
**Whole room PDF** under Whole-room files. The window shows the printer's state (ready,
printing, out of paper and so on), the pages and copies, and **Sides**: two-sided
whenever every copy has an even number of pages, otherwise one-sided only. Click
**Print at Home** once; the window follows the send to **Printer accepted the job**.
A send that was not confirmed says so and is never repeated automatically: check the
printer before sending again, since a second print duplicates the stamps.

Setup is one line of workspace configuration for both `faculty.service` and
`faculty-inbox.service` (user units, no sudo), for example
`~/.config/systemd/user/faculty.service.d/printer.conf` and the same file under
`faculty-inbox.service.d`:

```
[Service]
Environment=FACULTY_PRINTER_URI=ipp://192.0.2.201/ipp/print
```

then `systemctl --user daemon-reload` and a restart of both. Keep the printer's
address fixed (a DHCP reservation on the router), or Faculty will report it as not
answering. To check it from ml-box: `ipptool -tv ipp://192.0.2.201/ipp/print
get-printer-attributes.test`.

## Email a print PDF

Open a ready print job and choose **Export by email**. The form defaults to
`instructor@tamu.edu`; select another approved instructor address or **Custom email
address**, then click **Send email**. The recipient and PDF shown in that form are
the exact export you approve. The existing **Download print PDF** option remains.

Faculty attaches the existing validated PDF. Emailing never allocates new page
tokens or additional copies. Queue and provider-acceptance status appear in the
same dialog. Acceptance means the email service accepted the message; it does not
prove it reached the recipient's inbox. Failed and uncertain sends remain in
history. If delivery is unconfirmed, check your mailbox before explicitly sending
again; the worker never retries a send automatically. Repeating the same browser
request reuses the existing export, including after a lost API response.

The button shows **Email queued**, **Sending email…**, then **Email accepted** as
the export progresses. Accepted exports keep the button disabled to prevent an
accidental duplicate, with a normal cursor and an explicit completion message.

The PDF limit is 3,500,000 bytes (about 3.3 MiB), the same limit seating chart PDFs use.
Larger print jobs remain downloadable. Email exports use the same direct access
as the rest of Faculty, with an explicit Send email action and no session cookie.
Since 2026-11-01 the email comes from your connected Gmail (the Response Cards
connection; the dialog names the account), never from Resend (DOCUMENTS "PRINT EMAIL
FROM GMAIL"). While Gmail is not connected, the form says to connect it on the
Response Cards page and leaves its Send button disabled.

Export rows reference `print_batches.id` and retain that batch's section owner;
they do not copy the PDF into another artifact tree. Recipients and credentials
are workspace configuration. The existing coordinated catalog backup includes
approval hashes, queue states and provider receipts alongside the referenced PDF.

## Export a print PDF to a laptop USB drive

Connect the USB drive to the laptop running the browser. Open a ready print job
and choose **Export to external drive**, then **Choose USB folder & export**.
Select a folder on that USB drive and grant the browser read/write access.
Faculty downloads the validated PDF through the existing app connection, writes
its exact binary bytes to that folder, closes the file, and checks the saved
file's size and SHA-256. Wait for **Exported and verified** before using the
laptop's normal eject control.

If the browser refuses folder access after selection, use **Save PDF as…** when
available. Select a PDF filename inside the USB drive's normal folder, then wait
for **Exported and verified**. This grants access to that file for this export;
it does not remember or replace the folder preference. The same binary download
and saved-file checks apply. An identical file is verified without rewriting it;
a different nonempty file requires a new filename and is preserved.

The selected folder is remembered in this browser for this workspace address.
Later clicks on **Export to external drive** export directly to it when permission
is still granted. If the browser requires permission again, click **Allow access
& export**. Use **Choose another folder & export** to change the destination or
**Forget folder** to remove the browser preference. The feature cannot identify
which laptop folder is physically on a USB drive; choose that location in the
laptop's picker. It does not mount or select a server drive.

An identical existing print PDF is verified and reported as already saved. A
different file with the same name is preserved; the export receives a unique
suffix. Permission denial, an unavailable drive, a failed write, and verification
failure are reported without claiming success or falling back to another drive.
An interrupted write may leave an empty file; subsequent exports preserve it and
choose a fresh filename. Browser export creates no new print batch or tokens.
The native Save As dialog can create an empty destination before Faculty receives
the file handle; failed verification never writes PDF bytes to that destination.

The browser's `AbortError` can mean the picker was dismissed **or** access was
blocked. Faculty distinguishes a failed folder selection, a failed file selection,
and an interrupted export, and never assumes that the user cancelled. Trying a
subfolder on the USB drive can help distinguish a rejected drive root. The file
picker opens only after a separate click, never automatically after a rejection.
Both pickers still depend on browser and operating-system permissions. If both
fail, use **Download PDF instead** and move the downloaded PDF in Finder or the
laptop's file manager. An encryption guide bundled on a drive does not establish
that the drive is locked; test a normal folder outside any software-managed vault.

Direct folder access requires a secure context (HTTPS or localhost) and a browser
supporting the [File System Access API](https://developer.chrome.com/docs/capabilities/web-apis/file-system-access),
such as Chrome or Edge. Some embedded browsers, Safari, and Firefox cannot offer
this direct export. Folder and single-file support are detected separately, and
the dialog provides the existing download option as well. A normal
download is never reported as a verified drive export. Browser permissions may
need renewal, and clearing site data or changing the workspace URL can forget the
destination.

Ownership remains the existing section print batch. The laptop/browser folder
handle is a local preference stored in IndexedDB, not a catalog field, API
parameter, or path on the Faculty server. The exported USB file is a user copy;
the catalog and original artifact retain their managed storage/backup lifecycle.
No installation, public endpoint, or native laptop helper is needed in a
supporting browser.

`tests/browser_print_drive.py`, called by the document browser workflow, exercises
real file streams and serialized directory handles in isolated browser storage.
It verifies exact PDF bytes, reuse after reload, renewed/denied permissions,
download errors and corruption, disconnected/full drives, failed close, filename
collisions, forgetting the folder, blocked folder selection, the single-file
fallback (including permission failures, source corruption, existing-file
preservation, failed close, and no folder persistence), and unsupported/mobile UI.
The picker and
device failures are simulated; an actual laptop USB drive still requires a local
user-selected destination.

## Scan and review

Send PDFs using the existing scanner address-book entry for `scan@example.com`.
No typed subject, body, course, YAML, or secret is required. The signed gateway
durably retains arrivals using its existing delivery identity. Two distinct
arrivals with identical PDF bytes remain two Inbox messages; retrying the same
delivery remains one message.

The Teaching Inbox lists every scan by what it holds and the most pressing thing
left to do, with a chip that reads **To do**, **Check**, **Working** or
**Nothing to do**: "Test 1 · MATH 140 · Sections 507 and 508 · 528 pages · 44
exams · Nothing here needs you", or "162 bubble sheets · Say whose these are".
The tabs follow the same summary: a scan with nothing left to do moves to
**History**, and every other scan stays under **Current**, its chip saying whether
it needs you or the worker is busy with it (its stored status stays "needs
review" for good, because the instructor review requirement is never cleared
automatically; that says nothing about whether anything is left to do). The
list names no student. **Delete from Inbox** (on a scan's heading, or among a submission's actions)
warns first and wants a tick: the submission leaves every list and search, and
nothing it holds counts anywhere any more (its labels no longer mark papers as
seen or conflict with other scans, its grouped exams and bubble sheet readings
are left out, students matched on it are free again). It undoes nothing already
applied and erases nothing: the original, its files and its history are
retained, so no disk space is freed, and **Restore to the Inbox** under the
**Deleted** tab brings it back. A scan whose pages are part of a reviewed
assembly cannot be deleted, and neither can anything still queued or running.

Opening a scan shows the same summary in full; the
page is laid out for one question, "what is this and what do I still have to do":

1. **What this scan is**, the card at the top: the assignment, course and
   sections its labels name (or how many bubble sheets it holds), each print
   job's papers present, and **What to do**, a short list with the most pressing
   item first. An arrow marks something only you can do (say whose bubble sheets
   these are, review sheets that were unsure, group exams whose labels are
   uneven, answer the one-label question); an exclamation mark is something to
   check (labels of two assignments on a scan Faculty did not separate by
   itself, a page that also arrived elsewhere with different content, copies
   with pages no scan has shown); a tick is done;
   "Nothing here needs you" means exactly that. Students' names stay behind
   **papers not seen in any scan yet**.
2. Only the tools that apply, each folded to one line until wanted: **Bubble
   sheets** directly under the card when the scan holds sheets (one line at the
   bottom otherwise), **Exams** with the grouped exams and their PDFs (grouping
   by hand opens by itself only when the scan did not group on arrival),
   **Pages**, **Assemblies**, and **Sort by printed version** (open by itself
   only for a scan nothing else recognized).
3. **Files, submission details and history**: the original, the separated
   pages, reports, the command, and the audit trail, closed until asked for.

Inspection
preserves the original, stores each extracted page and 200-DPI raster as existing
Inbox artifacts, and records source attachment/page, all decoded candidates,
decoder version, exact extracted-page PDF hash, provenance, and duplicate/conflict state.
It never clears `instructor_review_required`, overrides failed sender
authentication, changes the original command, or executes an action.

Unrelated codes do not route anything. Multiple teaching codes, malformed/unknown
tokens, unissued/cancelled/superseded paper, and unreadable pages remain for review.
Unstamped blank backs are ignored without supplying identity to other pages.
A cover sheet identifies only itself, except through the explicit first-page
label request below. Recover a failed decode using the exact
printed `A1-` alias plus a correction reason; the operator and correction are
retained. Copy-to-student links are administrative, never proof of authorship.

Assemblies span arrivals but are always keyed by one printed copy, in page order.
Choose one observation per page and save a reviewed revision. Missing pages need
the explicit partial-assembly checkbox. Differing observations remain retained;
review marks unchosen alternatives rejected and freezes the selected observations
in an immutable revision. Later arrivals cannot replace that revision. Exact
page-byte repeats reuse the prior revision. Different page bytes require review
even when their working rasters look identical. If multiple reviewed copies belong to
one enrollment/assignment, use **Choose this submission** with a reason. Both
assemblies and the choice history remain available.

### Section-sized scans

The department scanner saves a whole section as one PDF: hundreds of pages at
300 DPI, far past the email and ordinary submission limits. Upload such files
from the Inbox page with **Upload scanned assignment** (each PDF is sent in its
own request, `POST /api/inbox/scans`, from the instructor workspace only), or
bring the file to the server (Taildrop to `~/taildrop`, for example) and retain
it from there:

```sh
.venv/bin/python manage_inbox.py scan ~/taildrop/doc000405.pdf ~/taildrop/doc000406.pdf
```

Each file becomes its own `scan.receive` arrival that requires instructor
review, exactly like the scanner address, keyed by its content so importing it
again retains nothing new (the upload dialog says "Already in the Inbox" and links
to it). Up to 512 MB per PDF, 1 GB per import and 1,500
pages per scan are accepted on these two paths only; email, API keys and submission
codes keep their small limits. Each worker turn inspects at most twenty pages
and checks at most twenty unlabeled pages for bubble sheets, then releases the
execution lock for other queues. Unfinished scans return to the back of the
pending queue and resume from their retained pages. Grouping and label OCR wait
until inspection finishes. An interrupted running chunk still needs an explicit
retry, and resumes at the first piece not yet retained
after **Retry inspection**. A 492-page, 130 MB scan was retained in under a
second and inspected in 84 seconds, adding about 450 MB of page PDFs and rasters.

A copy can be damaged on its way to the server. On 2026-09-18 four scans
received over Taildrop each had about 300 KB of zero bytes near the start
(ending at the same byte position in all four files), so the first page of each
rendered three-quarters black and its label could not be read; the instructor's
originals were intact. A scanner's PDF is compressed images and never contains
such a run, so this import refuses a PDF holding 64 KB or more of consecutive
zero bytes and names where they start. Compare `sha256sum` on both machines
before importing, and prefer `scp`/`rsync` over Tailscale SSH for large files.

### What this scan is

**Scanned page review** opens with what the labels say the scan is for: the
assignment, version, course, term and section of every print job whose paper
appears, how many of its issued papers are in this scan, and which papers no
scan has shown yet. It reads **Recognized** for current paper of one assignment
in one term. Sections that sat the exam together are scanned together, so
several sections of that assignment are still one recognized scan, and the
sections are named. Labels that name different assignments, or that are
unknown, malformed, ambiguous, cancelled, superseded or unissued, make it
**Check before using this scan**. A scan whose labels name more than one
assignment is separated by the Inbox worker into one new scan per assignment
and moved to Deleted (restorable); only a scan it leaves alone waits, and
says why. A scan with no label on any page whose first pages print the
titles of more than one assignment is separated the same way, by those
titles, when every paper has its assignment's number of pages
(`separation.py`, INBOX "SEPARATING A SCAN").

On paper labeled on every page, a page whose codes did not read (covered by
writing, smudged) that lies between two labeled pages of the same copy, or
after a copy's last labeled page at the end of a file, takes that copy's
missing page by its place when exactly as many sides there look like a printed
page as the copy misses, and only in a file whose labels show it was scanned in
paper order (a shuffled stack is never placed: each page sorts by its own code).
The empty backs there, and the back of a paper's last sheet just before the next
paper's first page, are set aside with a note naming the paper, and Undo on
Papers takes one back for good. A place Faculty cannot
decide is named on Papers ("Copy 60's page 2 did not read …"), never offered as
a grouping (`places.py`, DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS").

Bubble sheets carry no label. Right after inspection, every page without a
label is checked against the Gradescope template's corner positioning marks
(any three of the four, since the stapled corner is usually torn). A scan of
such pages reads **Recognized as bubble sheets from their positioning marks**,
with the count. The marks say what a page is, never whose: until you choose the
assignment and sections under **Bubble sheets**, that stays flagged, and
afterwards the panel names what they were read for. Recognized sheets are also
left out of printed-label OCR, which would find nothing on them. A scan with no
readable label and no bubble sheet is **Not recognized**: nothing is assumed
from names or page order.

### Exams labeled on the first page only

When a written exam cannot be stamped before printing, export **stamps only**
and stick each student's page 1 label on the first page. The other pages reach
the scanner without a barcode, and the scan falls back to the distance from one
label to the next.

**Evenly spaced labels are grouped without asking.** As soon as a scan is
inspected, if some pages carry no label, every label-to-label distance is the
same, the last exam is as long as the rest, nothing comes before the first
label, and no exam would need review, Faculty groups it at that distance by
itself. Labels recovered later receive the same check from worker discovery once
their label OCR has finished, including scans inspected before that handoff was
added. The run is marked automatic, keeps the instructor review requirement,
and has the same **Undo**; once undone it is never redone on its own. Both real
492-page section scans qualified: 41 labels, every gap 12 sides. On paper that
expects a label on every page, an automatic run only groups: it never numbers a
page from its position.

On paper labeled on its first page only (a one-page label sheet, or a print job
you confirmed as first-page labels), an exam that adds up is in scan order, so
each side takes its position as its page: side 3 is page 3. Blank backs keep
their place. An exam that is short, long or otherwise does not add up is never
guessed. Your review of the scan in the Inbox is the approval: each such exam
then becomes that student's reviewed paper, ready for the exam's collection and
for grading. A copy you already assembled by hand is left alone, and anything
duplicated or conflicting still waits for the per-copy assembly review.

Anything less even is left alone. In **Scanned page review**, **Group exams by
first-page label** then asks for the **scanned sides per exam** (count both
sides when scanning two-sided; blank backs count), offers the usual distance
between labels in this scan, and shows a preview with the reasons. Nothing is
written until you approve that exact preview.

The label anchors an exam and the side count is its checksum. Each readable
page 1 label starts an exam. Its pages join it only when the next label, or the
end of the attachment, arrives after exactly that many sides. When a whole
number of further exams' worth of sides follows instead, the first exam is still
grouped and the rest is listed as exams whose label was not read: record the
alias printed on that label against its first page (**Recover using printed
alias**), then preview again.

Every exam that adds up is kept as an **exam packet**: the label's paper and its
scanned sides in order, listed under **grouped exams** with **Download exam
PDF**. A label exported for one page identifies the paper, not its other pages,
so those pages keep no page identity and appear in no assembly. When stamps were
exported for the exam's real page count, the print job still expects a label on
every page, and you preview and approve the grouping yourself, the written sides
are also identified as pages 2, 3 and so on of that copy (blank sides are skipped), marked
**Identified by position**, with the same duplicate and changed-content rules as
barcode intake, and they appear in the copy's assembly for the usual review.

#### Paper issued a label for every page, labeled on the first page only

A print job normally expects a Data Matrix on every page of each copy, so a
copy whose other pages arrive without one is reported with **Missing pages**.
When you open a scan in the Teaching Inbox and it shows that a job was really
labeled once per exam, Faculty asks straight away: **One label per exam?** It
asks only when the job's copies have more than one page, the scan holds at
least two of its page 1 labels, every label is the same distance from the next
and the grouping leaves nothing to review, and no label from another page of
that job was ever read on any scan.

- **Use first-page labels only** records the print job as labeled on the first
  page. Faculty then expects page 1 of each copy and nothing else, so the
  missing pages clear here, in other scans of that job, and in the exam's
  collection status. Exams stay grouped under their label, and the exams this
  scan already grouped take their other pages from their position. Nothing
  issued or scanned changes. Local question grading still needs every page,
  which your Inbox review of the scan then supplies.
- **Keep expecting a label on every page** is remembered for that scan, which
  does not ask again. You can then number the pages by position with
  **Preview**, or recover labels page by page.
- Closing the question without answering changes nothing; it asks again the
  next time the scan is opened.

**What this scan is** shows the job's current setting with **Use first-page
labels only** or **Expect a label on every page again**, available whenever no
other-page label of the job was ever read, even if the labels are unevenly
spaced. Each answer is kept in the print job's audit history and the scan's
Inbox history, and the instructor review requirement is never cleared.

Anything that does not add up is reported and left exactly as it was: too few
sides before the next label (a missing page), a stray extra sheet, a label that is
not the page 1 label, paper that is cancelled, unissued or superseded, a page
number already identified elsewhere with different content, pages an earlier run
grouped differently, and, where pages are being numbered by position, an exam
whose count of written sides differs from most others (a faint page read as
blank would shift every later page number; grouping alone is not affected). Pages
before the first label, such as a block of bubble sheets, are listed as outside
any labeled exam.

Barcodes already read, reviewed assemblies and the instructor review requirement
never change. **Undo** retires a run's packets and returns its identified pages
to unidentified, except pages already in a reviewed assembly, corrected since,
or repeated by a later scan. Page order is the only evidence here: a scan whose
sheets were shuffled between two labels passes the count, so look through the
exam PDFs before relying on them.

#### A held paper grouped by hand, and a page in no paper set aside

A paper whose sides do not add up (a student added a sheet) is held on **Papers
to check**. When its page 1 label was read, **Choose its sides…** shows every
side from that label to the next, in scan order, each with a tick: the first
page is ticked for good, and the n-th side you tick becomes page n of that copy.
A blank side you tick keeps its place and takes no identity (a blank back past
the last page is fine); a side that carries its own label must be that page; no
written side goes past the pages the copy was issued. Choose a reason and
**Group as Copy N**. If the paper changed since you opened the dialog, nothing is
grouped and you are asked to look again. The paper is then listed under
**Grouped by hand** with its reason and **Undo**, and the sides you left
unticked stay as scanned, in no paper. Once the scan's review makes it a
reviewed paper, Undo is gone: change that paper on Review. A copy that already
has a reviewed paper is not grouped by hand. With the one held paper grouped,
the worker groups the papers that add up by itself, as it would have if none
had been held.

A written page with no label that is no student's (a TA's section header sheet,
scratch paper) is set aside with **Not part of any paper…** on the Scan page
(while its step says the page belongs to no paper) or under a side in no paper on
Papers to check, with a reason. It is kept, listed
under **Set aside** with that reason and **Undo**, and no longer holds its scan
open or certification back. Both are instructor actions recorded in the scan's
Inbox history; nothing is set aside or grouped by hand automatically.

```sh
.venv/bin/python -m unittest discover -s tests -p test_sequence.py -v
.venv/bin/python -m unittest discover -s tests -p test_scan_stray_pages.py -v
LD_LIBRARY_PATH=$PWD/.tools/browser-libs/usr/lib/x86_64-linux-gnu .venv/bin/python tests/browser_sequence.py
```

## Ownership, access, and recovery

- `assignments` / `assignment_versions` and source/canonical files belong to
  `course_definitions.id`, in verified `definitions/<id>/` storage. They stay on
  NVMe while **any** descendant term is current.
- `print_batches`, their copies/stamps, PDFs and manifests belong to
  `course_sections.id`, in `sections/<id>/`; its permanent term supplies currentness.
  A job printed without a seating chart belongs to `course_terms.id` instead
  (`section_id` NULL, files in the term's `terms/<id>/` storage beside its roster),
  and lists the sections it is for in `print_batch_sections`. Every job records
  `term_id`.
- `scan_pages` and inspection jobs reference retained `inbox_messages`; extracted
  PDFs/rasters use `inbox_artifacts` under the same message. No receipt table or
  separate scan storage tree exists.
- Catalog mutations use the roster lifecycle lock, then owner locks, then catalog
  transactions. Native parsing occurs outside owner locks on isolated scratch
  copies; publication reacquires locks and checks persisted mappings.
- All new payload writes stage on NVMe. The existing nightly backup verifies
  referenced hashes and the final snapshot before purging non-current owners.
  A missing required NVMe copy never silently falls back to an older backup.
  Back up the catalog with payloads: opaque tokens cannot reconstruct provenance.
- Identity-changing course/section edits cannot detach assignments or print
  provenance from their final visible offering. Use current-status archival.
- Faculty uses its existing Tailscale/SSH access boundary with no application
  login or browser-session cookie. Document lookup, creation, download, recovery,
  and review work directly through that connection, including after a restart.
  Same-origin write checks remain in place. The public email-only listener cannot
  serve the UI or any document API. There is no public lookup URL.
- A job failure does not publish a downloadable PDF. Partial attempt files remain
  unissued; retries allocate a new attempt path while retaining tokens. Corruption
  fails verification and retains the NVMe copy. Investigate before retrying.

## Future child-job handoff

There is deliberately no child processor in v1. A future implemented handler must
be explicitly requested after instructor review and registered in both
`protocol.ACTIONS` and `handlers.HANDLERS`. Its immutable input contract is the
`assembly_revisions.id`, `selection_sha256`, ordered `assembly_pages` references,
partial approval, permanent section/enrollment context, and explicit submission
choice where applicable. It must recheck source authentication and the Inbox
review gate, obtain the existing separate exact-command approval for consequential
actions, and submit through `InboxService.submit`. An idempotency key derived from
handler + revision + command hash and the existing durable result receipt must
prevent duplicate effects. An exact-content repeat cannot create new work. Never
derive an action from a printed payload or retroactively replace processed pages.

## Verification and classroom qualification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m document_identity.qualification
.venv/bin/python tests/browser_documents.py
# Rebuild the checked-in fixture sources/PDFs/logs with the isolated compiler:
.venv/bin/python -m document_identity.compiler --fixtures
```

The software matrix persists two copies of three pages on each paper size and
checks 144 required cases: 300/200/150 DPI; 90/180/270-degree rotations; ±3-degree
skew; Gaussian blur sigma 0.6 at 300 DPI; JPEG quality 70; grayscale contrast
64/192; and combined 200 DPI + 90-degree rotation + JPEG 70. Pillow settings,
library versions and seed are recorded. Failures retain PNG fixtures under the
selected output directory. More severe blur/JPEG cases are characterization,
with no incorrectly accepted identities permitted. See `qualification-results.json`.
The criterion-by-criterion evidence is recorded in `acceptance-audit.md`.

**The original `faculty-page-v1` layout passed physical qualification on
2026-09-13:** all 30 classroom scan pages resolved into ten complete copies, with
instructor review still required. The instructor used defaults; exact scaling
and scan-panel DPI were not recorded. See `physical-qualification.md` for the
observed PDF properties and retained evidence. The separate compact-corner run
also resolved 30/30 pages, with 52/60 individual marks detected; its limits and
evidence are in `corner-test.md`. Other layouts/hardware require a separate run;
simulation does not replace it.

1. Register the three-page Letter fixture under a **Scanner qualification**
   assignment as version **Test 1** in the intended live course/section.
2. Create **10 unassigned copies** and download the validated 30-page PDF.
3. Print **single-sided, Actual size/100%, printer copies=1**.
4. Scan all 30 pages as one PDF at **300 DPI, grayscale**, to the saved `scan@`
   address. Send no subject/body secret.
5. Confirm the same Inbox review item contains 30 resolved pages and 10 complete
   assemblies of three pages, with instructor review still required. Retain the
   paper and originals until verification is complete.
6. Record date, operator, paper, scaling, DPI, color mode (models are optional),
   batch ID, Inbox message ID, 30/30 results, and any failures in
   `physical-qualification.md`. Repeat for other intended hardware/paper settings.
   If Data Matrix fails, adjust the shared profile or add the QR adapter and rerun
   both the required software matrix and physical test before production.
