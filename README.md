# Faculty

Faculty is a self-hosted web app that runs my teaching. I teach several sections of an introductory math course at
Texas A&M, with hundreds of students a term. Faculty takes the work from the roster to the final grade:

- exams are printed with a code on every page;
- scanned papers are sorted back to their students;
- handwritten answers are graded by AI models against rubrics;
- I review what the models were unsure of and certify the grades;
- the grades go out to Gradescope and Canvas.

Faculty also handles the work around that: accommodations, seating charts, absences and makeups, student email and
follow-up, the calendar, and recordings of classes and meetings.

It has been in daily use since September 2026. This repository is a public snapshot. Every student, colleague, address
and account in it has been replaced with an invented one ([ABOUT_THIS_REPOSITORY.md](ABOUT_THIS_REPOSITORY.md)).

## What it shows

- **Deployment against real systems that were not built to be integrated.** Faculty connects to:
  - Canvas, Gradescope and iClicker;
  - Gmail and Google Calendar;
  - the campus accommodations office's system;
  - a networked copier/scanner;
  - two Cloudflare Workers (email intake and student response forms);
  - local and hosted vision models.

  Where a system has an API, Faculty uses it. Where it has none, Faculty works through the system's own web pages with
  my sign-in, and records every write it makes.
- **AI with a human in the loop.** The models grade, and anything they are unsure of is flagged and held for my review.
  A flag holds back only its own paper; the rest of the pipeline keeps moving. Certifying grades, sending them and
  publishing them are my actions, except for the few automatic steps each module's contract names.
- **Reliability work.** Every outward write keeps a durable receipt and an idempotency key, and an uncertain outcome is
  never retried automatically. The catalog is SQLite with immutable, checksummed migrations. Course data is copied
  every night to a second drive, and each copy is checked after it is written.
- **Written decisions.** `docs/decisions/` holds one contract per module, recording what it may do and why, with the
  date of each decision. `AGENTS.md` lists the rules that apply across the whole app.

## How it was built

I designed Faculty and wrote its requirements and the module contracts in `docs/decisions/`. Comments and documents
quote each requirement with its date ("requirement, 2026-10-01: ...") next to the code it explains, and `CHECKS.md` is
the log of what was checked for each change.

By the numbers:

- 155,000 lines of Python in the app and 153,000 in its tests;
- 63,000 lines of JavaScript;
- about 4,650 Python unit tests, 89 Node test files and 166 browser check scripts;
- 178 SQL migrations.

## Architecture

| Layer | What it is |
| --- | --- |
| Server | Python 3.12, Flask served by Waitress, one process per role (web, Inbox worker, grading, embedding) under systemd user services (`deploy/`) |
| Data | A SQLite catalog (`catalog_migrations.py`, `*_migrations/`), with files kept per course, term and section (`course_storage.py`, `rosters.py`); nightly verified backups (`manage_storage.py`) |
| Frontend | Plain ES modules type-checked with `tsc` through JSDoc, hand-written CSS on shared design tokens, a strict CSP; no framework or build step (`static/`, `FRONTEND.md`) |
| Pipeline | Scan → Assignment → Students → Papers → Review → Grading → Certify (`teach_inbox/`, `document_identity/`, `grading/`) |
| Models | Pluggable connections: fully local models on the server's GPU, or hosted providers (`grading/providers/`) |
| Edge | Two Cloudflare Workers: email intake and student response forms (`cloudflare/`) |

## Where to look first

| To see | Read |
| --- | --- |
| How a module's rules are written and enforced | `docs/decisions/grading.md`, then `grading/certification.py` |
| Receipts and idempotency | `canonical_json.py`, `grading/gradescope_sync.py`, `docs/decisions/gradescope.md` |
| Schema changes that cannot be rewritten | `catalog_migrations.py` and `tests/test_catalog_migrations.py` |
| Storage that is checked after every write | `course_storage.py`, `manage_storage.py` |
| Testing against fake services | `tests/fake_gradescope.py`, `tests/fake_canvas.py`, `manage_checks.py` |

## Data handling and security

- **Access.** Faculty has one user and no login. The app binds to the loopback address and is reached only through
  Tailscale. Writes need a same-origin header, and the only listener open to the internet takes email
  (`docs/decisions/access.md`, "Access boundary" below).
- **Model connections.** Each model connection, local or hosted, is one I set up and choose per feature. Grading sends
  a model the cropped answer areas, which leave out the lines where students write their name and UIN.
- **Campus sign-ins.** For campus systems without an API, Faculty signs in with my own school account in a hidden
  browser. I approve every Duo push on my phone, and Faculty answers "no" to "remember this device". Credentials stay in
  their own configuration files and never enter the catalog, logs or exports.
- **Tests never touch real systems.** The checks run on synthetic data against fake versions of every outside service
  (`tests/fake_*.py`), and the check runner strips every credential from the environment.

## Run it

You need Python 3.12 or newer; if `python3` is older, use `python3.12` in the first command. Tested on Linux.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHON=.venv/bin/python3 tests/tools/serve_port.sh 8791
```

The second command seeds a workspace with invented demo data and prints "serving 8791" when it is ready, after about a
minute. Then open http://127.0.0.1:8791.

Uploaded PDFs are opened inside [Bubblewrap](https://github.com/containers/bubblewrap), a Linux sandbox
(`sudo apt install bubblewrap`). The demo and the quick checks run without it.

To run the quick checks:

```bash
PYTHONPATH=tests .venv/bin/python3 -m unittest test_app test_course_codes test_catalog_migrations
```

`manage_checks.py` runs the full and browser suites in an isolated scratch directory (`--scratch-root`, a directory under
`/mnt`).

---

# Operating guide

The instructor's own guide to running and using Faculty, as kept in the private repository.

The Assistant can analyze a selected area of Faculty: **hold Command (⌘)** for a moment with the pointer still, draw a box, then release Command to send it (or click the area button beside Assistant and drag; releasing the mouse sends). No screen-sharing permission is needed. A focused chat opens about the selected text, records or original image crops. **Double-tap Command** for an empty floating Assistant chat; nothing is sent until you type a message, and a selection made from it joins that chat. PDF selection temporarily shows rendered pages; scroll to the page you want, then drag. Unsaved edits stay intact and are marked as drafts. Close stops unfinished analysis. Selected source text, field values and image pixels are temporary and are not saved with conversations.

A personal, self-hosted workspace for one instructor's courses, assessments and
scanned papers. Everything runs on your own server; nothing is published, emailed
or sent to Gradescope unless you click the action that does it or a rule below names
the automatic step (for example the Response Card confirmation email).

## What works

The sidebar lists fourteen pages in lifecycle order: Home, Calendar, Mail,
Student follow-up, Recordings, Courses, Canvas, Accommodations, Absences, Assignments, Seating
charts, Response Cards, Teaching Inbox and Rooms. Exams, Grading and the Gradebook
have no sidebar entry; they are reached through Assignments and each section's tab.
Each page names what it shows, says in one line what needs you, and offers one main
action; longer explanations sit in closed "How this works" disclosures.

- **Home**: what needs you now (scans to check, flagged or undelivered Response
  Cards, seat updates to send, grading waiting for review, failed work), what the
  worker is on, grading in progress as small step graphs, and what is coming up in
  the next three weeks from the calendar. **Workspace health** states itself while
  closed: worker, queues, disks and the latest verified backup.
- **Calendar**: month and agenda views of exams, seating charts, DR bookings and
  gradebook due dates, grouped per exam, plus your own events and deadlines.
- **Mail**: read your Gmail, see a student's mail, and write from Faculty
  (`docs/decisions/mail.md`).
- **Student follow-up**: conversations and commitments that need you; see
  "Student follow-through and teaching review" below.
- **Courses**: current or all courses; a course home with its current sections,
  the term's work (exams, seating, grading, cards, print jobs, gradebook) and
  course documents; a section tab per section with a sortable roster imported from
  CSV/XLSX, private ten-digit IDs and a student quick view.
- **Canvas**: edit your Canvas modules, pages and assignments in Faculty and push
  them to every section (`docs/decisions/canvas.md`).
- **Accommodations**: accommodation records and the DR exam schedule, both
  imported from the campus exports with a preview first; the schedule opens on
  upcoming exams, grouped by exam, and prints a DR list per exam.
- **Absences**: absences students ask you to excuse, their documentation and your
  decision (`docs/decisions/absences.md`).
- **Assignments**: one row per assignment and current term (Pending, Certified and
  Archived tabs), each opening an overview with the tabs Create, Prepare, Collect,
  Grade, Certify and Return; **Create assignment** walks through details,
  seating, paper and printing. Print jobs are grouped by export, with every file
  and label format in one place.
- **Seating charts** and **Rooms**: random, spaced exam seating that leaves out
  DR-booked students, exam versions, moves, printed charts and rosters, updated
  seat cards, and campus rooms uploaded from the department's seating charts.
- **Response Cards**: seat notices and short forms for students, led by what
  needs attention (flagged answers, undelivered invitations).
- **Teaching Inbox**: upload scanned PDFs (or drop them on the list), follow each
  scan through its flow graph (Scan → Assignment → Students → Papers → Review →
  Grading, with Bubble sheets after Students when a scan has them), and review
  email and API submissions.
- **Exams**: no sidebar entry (`#exams` opens Assignments); an exam record opens
  inside its assignment and is the hub of one assessment in one term: Paper,
  Seating, Notices, Print, Exam day, Collected, Grade and Return, each linking to
  the page that owns it, with a readiness checklist before the exam.
- **Grading**: assisted handwritten free-response grading in six steps (Rubrics &
  Templates, Answer areas, Key & Rubric Finalization, Grade, Review, Certify),
  reached as the assignment's Grade and Certify tabs, with local or cloud models,
  your review of anything in doubt, exports and sending papers and final grades
  to Gradescope; plus bubble-sheet answer keys and scores.
  See [GRADING.md](GRADING.md).
- **Gradebook**: a term's columns, categories, drops and letter scale, typed in,
  imported, or pulled from Faculty's final grades; reached from each section tab,
  not from the sidebar.
- **Search · Ctrl K** under the sidebar brand jumps to any page, course,
  assignment, chart, room, gradebook or current scan, and runs common actions;
  **?** lists the keyboard shortcuts of the page on screen. The status line at the
  foot of the sidebar opens the system status: worker, email gateway, backup,
  disks and running work.
- **Workspace settings** (sidebar foot): your name, storage and backups, links to each page's own settings, the connection guide for
  other devices, and a JSON export.
- Course data lives on NVMe with verified nightly HDD backups; non-current courses
  are archived to HDD before their NVMe copies are removed.
- Use the same workspace on your computers (desktop browsers). Open pages refresh in place
  every 30 seconds and when you return to the tab, keeping what you are typing;
  a tab opened before an update keeps working after Faculty restarts.
- Conflicting changes from different devices are rejected with a message to reload,
  rather than silently overwriting the newer edit.

The earlier **Tools** launcher (linked apps in Planned → Building → Ready stages,
pins and a roadmap) keeps its routes and API but has no sidebar entry.

## Appearance

Faculty has one appearance and one desktop layout (DESIGN-LANGUAGE, "Ledger",
`docs/decisions/design-language.md`; summary in `DESIGN.md`): an off-white page, white work surfaces, black ink
and 2px ink frames, a hard shadow on what you act on, and orange only for "needs you". There are no
photographs, paintings or serif type. There is no dark mode and no phone or tablet layout; below 1180 px wide the window
scrolls sideways. Paper previews and print colors are unchanged. `tests/browser_theme.py` and
`tests/test_design_language.py` check the single appearance, the Ledger components, print isolation, and that the
withdrawn Magazine edition's plates, sidebar arc and paintings stay gone.

Screen colors, spacing, type sizes, controls, and table treatments share
`static/design-tokens.css`. Every area uses the same off-white page, white surfaces and black ink with orange for
needs-you; status colors are quiet inks that keep their meaning. Exam and
seating summaries lead with their course/term/section context and an existing
next action, with supporting records and explanations in disclosures.

Manrope for titles (weight 800), reading and controls, Geist Mono for meta figures (the clock, counts, badges, IDs),
and Berkeley Mono for code and raw records give titles, instructions and records distinct roles.
Hairline rules, small uppercase labels over a 2px ink rule and an ink primary button complete the screen theme.
Supporting details and dependent form controls are indented under their parent section. `static/typography.css` applies this screen-only hierarchy; bundled
font sources and licenses are recorded in `static/fonts/README.md`.

Room and seating charts follow the same palette, with Berkeley Mono box-drawing
seat frames, neutral empty/occupied seats and subdued exam-version colors. Card
statuses retain their green/yellow/red/orange meanings. These are screen styles;
printed charts retain their existing colors and fitted geometry.

## Home and operational status

Home is a read-only projection of existing owners, not a new place to store
teaching data. **Needs you** uses the same derived counts as the sidebar badges
and the Inbox (a deleted submission counts nowhere), each row linking to the exact
page where the step is done; its scan rows reuse `ScanService.overview`.
Grading rows appear once Home has read the Grading overview (papers waiting for a
version decision are shown there, not in the Grading badge). **Coming up** reads
the calendar for the next three weeks; dates use campus time (`America/Chicago`),
and entered dates/times display without browser timezone conversion.

The health panel distinguishes queued, running, failed, and review work. A stale
or missing heartbeat with queued work appears in Needs you. Long native
operations can delay the heartbeat; the panel reports this uncertainty for up to
ten minutes. After that, running work is no longer described as in progress:
Home says the worker stopped responding and the work may be stuck. Only a
completed coordinated backup is described as verified. When the catalog lives
on NVMe, catalog-only Home diagnostics remain available if the backup drive goes
offline; normal payload access and write guards remain in place.

The Inbox worker rotates among ready processors so a busy queue cannot consume
every turn. Each processor remains bounded by its existing implementation;
running PDF/OCR operations are not preempted. Revocation checks still precede
dependent mail, and each processor retains its approval, locking, receipt, and
explicit retry rules.

Frontend scripts and styles use one content-versioned asset prefix, including
relative JavaScript imports. They are cached as immutable public source files;
HTML, raw `/static/` files, APIs, exports, and teaching artifacts remain uncached.
The app holds a consistent asset snapshot until restart. Restart Faculty after
frontend changes, then reload the browser to load the new build. A tab opened
before the restart keeps loading its modules from a small archive of the last
eight builds (`FACULTY_WEB_ASSET_CACHE`, by default under the NVMe runtime folder);
if one still cannot load, the page says Faculty was updated and offers Reload. Page-specific
state reads avoid loading room and seating details on unrelated pages; unchanged
Home cards preserve their DOM, keyboard focus, and open disclosures.

Use `python3 manage_checks.py` for the everyday checks, or add `full`, `browser`,
or `restore`. All scratch data stays on NVMe and tests use synthetic workspaces.
See [repeatable checks and migration rules](CHECKS.md) and
[frontend module boundaries and type checks](FRONTEND.md).

## Student follow-through and teaching review

**Follow-up** gathers captured student conversations that need a reply and lets you
track other commitments as needs reply, to do, waiting, snoozed or resolved, each with a one-line next step. New replies
reopen the matching conversation. **Office hours**, alongside Follow-up, schedules
meetings and records attendance; private notes and messages are separate actions.
Calendar entries contain only “Office hours,” the time and location. Student
records link to these tools, and **Ctrl/⌘ K** finds students by name and section.

**Teaching review** reads the latest certification’s question scores and pinned
rubric evidence, shows sample sizes and current learning-objective tags, and opens
lesson or practice drafts in Assistant. **Grade delivery** beside it separates
certification, gradebook values, Canvas send receipts and Gradescope coverage and
publication history. Destination comparisons run only when clicked; sending and
publishing use their existing review controls.

When drafting a quiz, enable **Assessment blueprint** to set an objective, type and
points for every question. Select a bank question for exact reuse in all versions.
Faculty retains those revisions and checks structural constraints during drafting
and approval. Review objective alignment yourself; create a new draft to change
its constraints. The rules and ownership are in
[Teaching follow-through](docs/decisions/teaching-workflows.md).

## Calendar

Open **Calendar** in the sidebar or its link on Home. Select a day for
its schedule, switch to **Agenda** for a list, or jump with **Today** and the month
picker. Event colors distinguish exams, seating, DR exams, custom events, and
deadlines. Details open separately.

**Add event** supports all-day, timed, and multi-day entries with an optional
location and notes. Custom entries can be edited or deleted from their details.
Existing exam, seating, and DR dates open their original workflows for changes;
DR calendar summaries show booking counts without student identities. Dates and
times keep their campus or DR-center local meaning across browser timezones.
See [calendar ownership, APIs, and checks](CALENDAR.md).

## Student accommodations

Open **Accommodations** in the sidebar for student accommodation records and the
**DR exam schedule**. Its two import buttons accept disability accommodation and
DR exam-registration XLSX exports across current courses. Preview matches before
importing. DR uploads update existing bookings when schedules change, keep history,
and retain bookings omitted from filtered exports. Rosters also show accommodation
details and DR bookings. See [accommodation imports and storage](ACCOMMODATIONS.md).

## Teaching Inbox

**Teaching Inbox** in the sidebar accepts files for an explicit course, term, or
section and queues a deterministic command. A separate worker currently supports
roster imports, accommodation and DR exam-booking imports, AIM form-structure capture
and document archiving (see `teach_inbox/README.md`). Other declared actions retain
their inputs for review until their processors are implemented. Jobs show status, original
files, output receipts, and an audit trail. Web upload, API keys, the local CLI,
and opaque one-time codes/QR images use the same intake service. Cloudflare email
delivery is configured, including the short scanner address `scan@example.com`.

See [Teaching Inbox's command, API, storage, and worker guide](teach_inbox/README.md).
All Inbox originals and outputs stage on NVMe, including unclassified mail.
The nightly backup verifies their HDD copies before purging non-current Inbox
payloads. Course-wide documents remain on NVMe while any term of that code is current.

## Exams and reviewed grading

**Exams** lists each assessment of the current terms, and its page is the
assessment's hub: one step graph from paper to recorded grades, the next thing to
do, a readiness checklist before the exam, and a tile per stage linking to the
page that owns it. An assessment with seating or print jobs but no exam record
still has a hub; **Create exam record** there opens the usual New exam dialog.
**Exams** connects a course assignment to one term and its sections. Plan main,
makeup and DR sittings, reconcile linked scan arrivals against expected students,
and inspect changes affecting seats, notices and printed paper. Grade exact
reviewed assemblies with question regions, rubrics, reusable comments and retained
mark history. Finalization and feedback release are explicit steps.

On an exam's **Manual scores** tab, upload PDF, DOCX, or LaTeX **grading templates**, select each problem's answer
pages, and optionally draw smaller answer areas. Saved maps can be applied to an
exam's printed version and exported with page images/text for OCR/VLM work.
Originals and earlier maps are retained. See the
[grading template guide](ASSIGNMENT_TEMPLATES.md).

Students can submit requests through existing Response Cards. Instructor decisions
have an exact preview before a seat or sitting changes; feedback and decision
cards are prepared locally for explicit publication and sending. See the
[exam workflow guide](exam_workflow/README.md) for ownership, source-review gates,
backups and operation.

## Add or edit a course

- Choose **New course — enter details**, or select a previous course to prefill
  only its name and code. Term, year, CRN, and section numbers are never copied.
- Choose a **Course prefix** from the dropdown, which includes **MATH** and all
  prefixes saved previously. **Other** reveals a required custom prefix field.
  Prefixes contain letters and are saved in uppercase. Saved prefixes remain
  available even after their offerings are removed.
- Enter the required **Course number** in the numeric box. It accepts whole-number
  digits, preserves leading zeros, and combines with the prefix as `MATH 101`.
  The previous-course dropdown lists each code once, using its most recently
  saved offering's name.
- Course name, course code, term, year, and a section number are required. CRN is optional and
  preserves leading zeros.
- Choose **Fall**, **Spring**, **Summer I**, **Summer II**, **Summer (Full)**, or
  **Other**. Other reveals a required term identifier field.
- Set the year beside the term using the arrow buttons, keyboard arrows, or
  scrolling while the year field is focused. You can also type a four-digit year.
- One required section field is always visible. Check **Multiple Sections?** to
  expand to two required numeric fields, keeping the first value. **Add section**
  adds another required field; additional fields can be removed. Section numbers
  must be different positive whole numbers. Unchecking the box keeps the first
  section and saves the course with that section only.
- Every course appears in **All Courses**. The **Current course** checkbox also
  includes it in **Current Courses**. Adding a non-current course from the current
  view takes you to All Courses so you can see the saved course.

Existing course records are upgraded at startup. A term such as `Fall 2026` is
split into `Fall` and `2026`; custom labels are preserved under Other. Missing
years, codes, and section numbers are left blank and must be supplied when editing that course.

## Course homes and section tabs

The collections group courses by **course code + term + year**. For example,
`MATH 140`, Fall 2026, sections 1–4 appears as one item; `MATH 140`, Spring 2026
appears separately. If section 5 is added separately for the same Fall term, it
joins that Fall course home. Collection counts count these groups, not the
individual saved entries. Groups are organized under term headings and sorted by
course code within the term.

Click anywhere on a course card to open its standardized home (the settings
buttons still edit details). **Home** brings together the course-wide shared area,
current sections across its terms, and a separate selected-term overview with
section shortcuts and settings. The shared area is the same for a course code
regardless of which term opened Home; teaching-term links change the selected
term. Each **Section N** tab initially offers **Import roster**. After importing,
it shows a searchable roster with private IDs, UINs, names, and every exported
column. Course settings remain on Home and in the page's Course settings button.

Every group appears in **All Courses**. A group appears in **Current Courses** if
any of its saved entries is current; mixed groups are labeled **Partly current**.
Both collections open the same home and all of its section tabs. Each saved entry
retains its current flag, CRN, and independent NVMe/HDD storage lifecycle. Grouping
does not merge, move, or discard saved files, even when section numbers overlap.

Home and section tabs have bookmarkable URLs and support browser back/forward and
reload. The back link remembers the collection that opened the course. Arrow keys,
Home, and End navigate the tabs. Editing a code or term follows the saved course
into its new home; removing the selected section returns to Home. Removing the
last saved entry returns to the originating collection. Stale links show explicit
course/section-not-found states.

`/api/state` and the JSON export include a read-only `course_groups` projection
alongside the original `courses` array. Each group has a deterministic ID based
on its code, term identifier, and year, plus section unions and complete offering
records. Missing legacy identity fields remain separate until corrected. Shared
definitions below continue to connect the same course across different terms.

## Shared course identity and individual offerings

The canonical course code identifies a shared course definition. `MATH 101` in
Fall section 1 and `MATH 101` in Spring sections 2–3 share one definition, while
each offering keeps its own ID, name, term, year, CRN, sections, current flag, and
course files. Changing one offering's code links that offering to the matching
definition or creates a new definition; it does not rename other offerings.

The catalog's `course_definitions` table has a unique `code` and an internal ID.
Each `courses` row has a `definition_id` foreign key. Future shared attributes and
resources reference that definition, resolved by course code. Term resources
belong to the course plus term/year; section resources belong to that term plus
the section number. Definition records
are retained when offerings are removed and are included in the HDD catalog
backups and JSON export. Each course's question bank is owned by its definition
(`question_bank/`, docs/decisions/question-bank.md); test banks are not implemented yet.

The required ownership hierarchy for future features is:

| Level   | Example identity                   | What belongs here                                             |
| ------- | ---------------------------------- | ------------------------------------------------------------- |
| Course  | `MATH 140`                         | Attributes and resources shared across all terms and sections |
| Term    | `MATH 140 / Fall 2026`             | Material specific to that term, shared across its sections    |
| Section | `MATH 140 / Fall 2026 / Section 2` | Records and resources for that one section                    |

Each new field or resource must have an explicit owner at one of these levels.
Parents can be referenced from their descendants; shared resources should not be
duplicated into each descendant. For example, a reusable source and a particular
term/section's use of that source can have different owners. A section number
alone is never a unique identity. Custom Other term identifiers are part of the
term key. The course Home is a combined view of these levels, not a signal that
everything on it belongs to the currently selected term.

These rules are also recorded in [AGENTS.md](AGENTS.md) for future development.
The current offering entries can cover multiple sections and therefore are not
canonical section owners. The term group ID is currently a navigation projection;
rosters now resolve it to permanent `course_terms` and `course_sections` owners.
Future resources reference these owners or the course-wide definition as appropriate.

The API returns `code`, `code_prefix`, `code_number`, and `definition_id` for each
offering, plus `course_definitions` and `course_prefixes` in `/api/state` and the
export. New forms send the split fields; older clients may send a combined code
containing both components. Prefix case and spacing are normalized; leading zeros
remain part of the code (`MATH 0101` and `MATH 101` are distinct).

On migration, recognizable existing codes such as `math101` or `MATH-101` link to
the canonical `MATH 101` definition. Offering IDs and data directories stay in
place. Unrecognized legacy codes are preserved, with blank split fields for
explicit correction when editing. Pending storage moves keep their recovery
state; active and archived metadata copies are refreshed through the existing
storage worker.

## Import a section roster

Open a course from either collection, select its **Section N** tab, choose
**Import roster**, and upload a `.csv` or `.xlsx` export for that section. The
provided 12-column export is supported, including multiline headings, numeric
Excel UINs, and UTF-8 BOMs. Files may be up to 10 MB and contain up to 10,000
students and 64 columns. XLSX workbooks must have one worksheet; formula cells
are rejected. CSV formula-like text stays literal and is escaped in the browser.

UIN and Name columns are required; UINs must contain nine digits. Leading zeros
are preserved. Duplicate UINs or invalid rows reject the whole import before any
student IDs are allocated. The selected course/term/section determines the import
destination; filenames and institutional term codes are not interpreted.

Each student gets a random **ten-digit private course ID**:

- A workspace-wide indexed `student_identities` table remembers each UIN's first
  five digits. `student_enrollments` reserves globally unique full IDs.
- A new course or term uses the same prefix and a fresh random suffix. Full-ID
  collisions retry against the unique database index. Prefixes alone are not
  identifiers; all student records use the full enrollment ID.
- The same UIN in another section of the same course and term uses the existing
  full ID. Reimports also retain it. Section memberships reference the enrollment.
- Imports add/update student fields and preserve students absent from the new
  file. They do not automatically transfer or remove section memberships.
- Every source column is stored with the enrollment ID. Original column headings
  and import history are retained; display headings normalize whitespace.

Course-term rosters live in `terms/<permanent-term-id>/data/roster.json`, with
section records keyed by permanent section IDs. The same verified storage engine
handles current NVMe copies, nightly HDD snapshots, archived HDD-only edits, and
verified final copies before purge. A term stays on NVMe while any saved section
of that term is current. Registry lookups use the shared NVMe catalog and are
included in its nightly backup. Overlapping saved offerings share one roster.

Imports serialize with course changes and backups. ID reservations commit before
atomic roster publication; if publication fails, the old roster remains and a
retry reuses the reserved IDs. Backups copy term payloads before backing up the
catalog under the same lifecycle lock, so registry reservations and published
roster IDs remain consistent. No student IDs are recycled.

Once a section has an imported roster, an edit or removal cannot detach it from
its last visible offering. Keep its identity and uncheck **Current course** to
archive it. Names, CRNs, additional sections, and current status remain editable.

The roster API is `GET`/`POST /api/course-groups/<group-id>/sections/<number>/roster`.
POST accepts one multipart `file` and requires the same-origin request header.
Responses include the permanent term/section IDs, private IDs, all student fields,
and import counts. The metadata JSON export excludes the private student registry
and rosters; full server backups include both. This is the existing private,
single-user instructor workspace; these IDs are not student-facing credentials.

Excel parsing uses [openpyxl's read-only mode](https://openpyxl.readthedocs.io/en/stable/optimized.html)
with defusedxml and bounded uploads; workbooks are always closed after parsing.

## Rooms and seating charts

**Rooms** in the sidebar lists campus rooms. Choose **Add a room**, name it (for
example `HECC 105`), and upload the department's seating chart as an Excel
`.xlsx` workbook or a supported ILCB PDF. An optional **Seat issues** Word
document records broken desks, missing furniture, and source corrections.
For Excel, the first worksheet is read and its physical arrangement is retained:

- Row letters in the leftmost column name each row (a copy in the rightmost
  column is ignored). Letters may be merged over two worksheet rows.
- A numbered cell inside a lettered row is a seat: `5` in row K is seat **K5**.
  `7 Broken` imports seat 7 as broken, and a bare `Broken` (or `Left-handed`)
  in the cell above or below a seat marks the seat in that column. Rows that
  only hold such notes are collapsed so the chart keeps its shape.
- `11 H`, `8 H (moveable chair)`, `Accessible` or `4 Wheelchair Only` keeps that
  accessible seat or wheelchair space free: it is imported blocked, with the
  chart's wording as the seat's note. Unblock it when a student needs it.
- `Table (4 seats)`, or `TABLE` beside `3 Chairs`, is a table with that many
  seats, numbered left to right within the row as **K Table 1 · 1** and so on.
  A cell such as `Chair` becomes a seat named **L Chair**.
- Merged captions such as `FRONT OF THE ROOM`, `Door`, or `STAIRS` are drawn
  as captions spanning the same cells. Blank cells are aisles and gaps.
- Charts without a row-letter column use each cell's text as the seat label.

Seat labels must be unique within the room; a repeated number in one row is
rejected with both cell references. Formula cells are rejected, so paste values
first. A chart may span up to 200 rows and 100 columns between its first and last
filled cells, with up to 2,000 seats, in files of 10 MB. Blank or merely
formatted rows and columns around the chart are ignored. Notes the parser could
not use (a table without a seat count, an unrecognized word after a seat number,
a `Broken` cell with no seat in its column) are listed under **Chart imports**.

Open a room to see its chart on a white architectural canvas with outlined seats,
row labels, tables, aisles, and room features. **Fit room**, **100%**, and zoom
controls switch between the whole layout and readable individual seats. Hover
over the chart and pinch on a trackpad, or hold **Ctrl** while scrolling a mouse
wheel, to zoom around the pointer. Ordinary scrolling still scrolls the page or
chart, and the zoom buttons remain available. This works in both Rooms and
Seating charts, for spreadsheet charts and PDF floor plans. The
same renderer is used for exam seating. Spreadsheet rooms keep their worksheet
geometry; the ten supplied ILCB layouts keep their curved rows and table positions.
Click seats to
select them, shift-click to select a block from the last seat you clicked, click
a table's title to select every seat at that table, then use **Mark broken**,
**Mark left-handed**, **Mark blocked**, or **Clear conditions**. Blocked means
the seat is fine but nobody should be assigned to it. A seat can carry several
conditions. The summary shows total, usable (neither broken nor blocked),
broken, blocked, and left-handed counts, and the room list repeats them. Changes save immediately to the server; another
device editing the same room is rejected with a reload message rather than
overwriting.

**Replace seating chart** uploads a corrected workbook. Seats whose labels match
keep their identity and conditions; seats missing from the new chart are removed
and new labels are added. The chart's own marks (`Broken`, `Left-handed`, an accessible seat
or wheelchair space kept free) add conditions but never clear ones you set; every other blocked
mark comes only from you. Each import is logged with its filename, SHA-256, and
added/kept/removed counts. **Room settings** renames a room, edits its notes, or
removes it. Uploaded workbooks, PDFs, and Word files are not retained; extracted
geometry, issue paragraphs, and source hashes are retained in the catalog.
The department's HECC 105, 207,
and 209 charts are kept under `tests/fixtures/rooms/` as parser fixtures.

The ILCB CAD PDFs draw their labels as outlines. `room_pdf_import.py` recognizes
the exact ten reviewed source files by SHA-256 and imports their verified
numeric geometry and seat mappings. A changed or unknown PDF is rejected instead
of guessing labels. Native extraction used to prepare these source profiles runs
inside the existing Bubblewrap document sandbox.

Exact broken/missing-desk labels in the accompanying Word document mark those
seats broken. Shortages without locations, ambiguous corrections, and unsupported
seat annotations remain explicit review notes. Administrative intake can retain
duplicate spreadsheet numbers using source-cell suffixes without inventing a
corrected number. These rooms appear in Rooms, but require review before exam
seating, seat moves, or printing. After checking labels and chair locations and
updating conditions, the instructor can confirm readiness in **Room settings**.
The version-checked confirmation is retained in the import history. Movable-table
charts without chair counts have zero assignable seats until a counted chart is supplied.

What the department's files cannot say is kept as a reviewed list in
`room_corrections.py`, each entry with its source cell or DR note: accessible seats
and wheelchair spaces kept free, a seat number printed twice left unassigned, typos
settled by the numbers around them, ILSB 1105's rows lettered A–N from the front,
ILCB 111's row A renumbered, and chair shortages without a location as room notes.
A room's notes appear on each of its seating charts. To see what the list would
change in the saved rooms, and then save it:

```bash
python3 manage_rooms.py --corrections
python3 manage_rooms.py --corrections --apply
```

A corrected seat stays the same seat under its new label. Each room is corrected
once, with a "Reviewed room corrections" line under **Chart imports**; a room that a
seating chart already uses is left as it is and named in the output.

To import a received directory of room charts, with matching `ROOM Seat Issues.docx`
files, preview first and then add the validated rooms in one catalog transaction:

```bash
.venv/bin/python manage_rooms.py /path/to/received-charts
.venv/bin/python manage_rooms.py /path/to/received-charts --apply
```

Existing rooms with the same name and source hash remain untouched, including
their seat IDs and instructor-set conditions. A changed existing source or new
issue document requires the normal version-checked replacement flow.

Rooms are workspace-wide physical resources, independent of any course, term, or
section. They live in the shared catalog database and are included in its
nightly backup and in the JSON export. The API is `GET`/`POST /api/rooms`,
`GET`/`PATCH`/`DELETE /api/rooms/<id>`, `POST /api/rooms/<id>/chart` (multipart
`file` plus the current `version`), and `POST /api/rooms/<id>/seats` with JSON
`{"version", "seat_ids", "broken"?, "left_handed"?}`. Writes need only the
same-origin request header, like courses and rosters; no session cookie is involved.

## Exam seating charts

**Seating charts** in the sidebar shows one tile for each exam; the tile opens
the exam's rooms, a tile for each room's chart (an exam with a single room opens
that chart directly).
Choose **New seating chart**, pick the exam assignment (from Assignments &
printing), and choose a room for each section taking it. Sections may share a
room or spread across several rooms; each room becomes its own chart, and all
of them are created together or not at all. The dialog forecasts, per room, how
many students will be seated against its usable seats and how many are left out
because their imported DR exam booking is linked to that assignment. A section
is seated once per exam: the dialog shows where an already-seated section sits,
and the exam's tile names sections not yet given a room; its page of rooms has
an **Add rooms** shortcut.

The optional **Exam versions** field accepts 1–26 versions (A–Z). Enter a count
to save letters automatically when seating is generated; it applies to every
room in that new plan. Leave it blank to choose versions later on each chart.
Automatic assignment balances the copy totals: each version gets the same
number of students, or one extra when they do not divide evenly. For example,
161 students with two versions gives 81 A and 80 B. **Allow unequal version
counts** is unchecked by default. Check it to let automatic assignment use
unequal totals when that reduces matching versions near students in adjacent
rows. It may still keep balanced totals when unequal counts offer no improvement.
Successive occupied seats in the same row still receive different versions.

**DR bookings link to assignments automatically.** When a DR exam export is
imported, each booking's exam name is matched to the course's assignment titles
with "Test" and "Exam" interchangeable and numbering normalized: "Exam 1",
"Test #1", and "test-1" all mean the same exam, and "Final Exam" matches
"Final". A booking with no matching assignment creates one, titled as the DR
center wrote it, so it appears under Assignments & printing. Bookings imported
before this feature are linked when Faculty starts. If a booking landed on a
different exam of the same course (for example the DR center wrote "Midterm 1"
for your "Test 1"), the seating dialog and Chart settings list it so you can
tick it and link those bookings to this exam explicitly; nothing else changes a
link.

Creating the chart seats every roster student of the chosen sections, except
students whose DR booking is linked to the assignment, at a random usable seat.
Seats are chosen so that each student has an empty seat on either side within
the row, and one student per table, whenever the room has enough room for that;
otherwise the remaining students take the least crowded seats. The chart shows
how many students have an empty seat beside them, marks those who don't with a
small dot, and the dialog forecasts each room's spaced capacity. Broken and
blocked seats are never used. If more students need seats than the
room has free, nothing is created and the message lists the counts (seats,
broken, blocked, DR exclusions). Sections must belong to one course and term,
and to the assignment's course. Rosters must be imported first.

Every generation and update ends with an independent **roster check** that
compares the seat assignments with the section rosters and DR bookings: a
student with a DR booking for the exam who has a seat, a roster student without
a booking who has no seat, a seated student on a broken or blocked seat, or a
seated student no longer in the sections. The result is recorded with the
chart and its history, and the same check runs live whenever the list or a
chart is shown, so later roster imports, DR imports, or room changes raise a
"Needs attention" flag on the exam's card and a banner on the chart with the
students named. **Update seating** clears the flags by reseating only those
students.

The chart page draws the room with each seated student's name, lists every
student with their seat or status (Seated, DR testing, No seat, Seat unusable),
and keeps a history. Click a seated student (then **Move or swap** if their
response-card details appear) to highlight them. Click the destination seat
to move them there, or swap the two students if it is occupied. **Move** in
the student table starts the same selection. Press Escape, click **Cancel move**,
or click the selected seat again to cancel. Click a free seat without a selected
student to seat a student who has none.
**Regenerate** reshuffles everyone. When a roster gains students, a seat is
marked broken or blocked, the room's chart is replaced, or new DR bookings
arrive, the chart shows what no longer fits and **Update seating** reseats only
those students, keeping everyone else in place. **Chart settings** links other
exams' DR bookings to this exam, edits notes, or removes the chart. Removing a
chart asks a plain yes/no until a print job with Data Matrix page stamps exists
for that exam in the same term; after that, you must type `delete` in the
warning box, and the server refuses the removal without it. Cancelled print
jobs do not count. **Print chart** opens a small dialog with two layouts and
hides the rest of the page when printing. **Fit the whole room on one page**
shrinks the room to the paper's width and height. Spreadsheet charts retain
names when the seats stay wide enough; PDF floor plans print a whole-room
overview with seat labels. **Readable, with names, across several pages**
keeps seats large enough for names. Spreadsheet charts use column bands with
row labels repeated on each band; chairs within small table cells stack for
readability. PDF floor plans use pages ordered across
the room and then down, with each seat and name kept together on one page.
Each floor-plan page repeats the exam, room, and sections above the drawing.
The dialog estimates the page count and preselects the readable layout when
the overview would omit names. Printing uses the room geometry independently
of the chart's screen zoom. Chrome and Edge apply the selected paper
orientation automatically; in Safari, use the orientation shown in the
dialog. Cmd+P or Ctrl+P without the dialog prints the one-page overview.
Print on Letter paper at 100%.

If no exam-version count was entered at creation, **Assign exam versions** asks
for it and saves the letters. It then becomes **Print version list**. Default
assignment cycles letters over occupied seats only, across gaps, aisles, tables
within a row, and section boundaries. With at least two versions, successive
students in a row receive different letters. One version gives everyone A.
Rows without row letters in studio floor plans are grouped by their named tables.
The assignment dialog also offers **Allow unequal version counts**; leave it
unchecked to keep automatic totals equal or differing by at most one.

Switch to the version view to see the letters in the room's seats. Each letter
has a consistent blue, purple, pink or neutral background, repeated in the
version count key and editor choices. Response-card green, yellow, red and
orange remain reserved for the student view. Click an occupied seat, then
another occupied seat to swap their versions, or choose a
letter directly from the available versions. These edits change only exam
letters; student seating and response cards stay as they are. Switch back to
the student view to use the usual student move/swap controls. **Reassign
versions** changes the count or distribution option, or resets the automatic
pattern, replacing any manual letter choices. Swapping letters preserves the
copy totals. Choosing a different letter for one seat may change those totals;
the chart warns if they are no longer balanced and unequal counts are not enabled.

Saved versions stay with their seats when students move, swap, or are reseated
by **Update seating** or **Regenerate**. The chart flags newly occupied seats
without a version and successive students in a row with the same version,
including across empty seats. Seating changes can also affect the copy totals.
Review and edit those letters as needed; manual choices are retained.

**Print version list** previews the saved letters grouped by row, with each
student's seat, name, section, and exam letter. Use it to attach each student's
label to the correct exam. Totals show how many copies of each version are
needed overall and at the start of each row, including zeroes for versions
unused in that row. Consecutive duplicate letters, or unbalanced totals when unequal counts
are not enabled, appear as warnings and may still be printed. Assign missing
letters and resolve roster/seat or room-review issues before printing. If the
chart changes while the preview is open, the updated
list appears for review before printing. Print on Letter paper in portrait
orientation, and reprint after changing seating or letters. This list does not
create or change print jobs.

**Print room roster** previews an alphabetical list of students seated in this
room, including their UINs, sections and seats. The **Response card** column in
both sections marks **No response** in bold. Submitted answers needing review
still count as responses; students without an issued card show **No card issued**.
A following **DR testers**
section lists their names and UINs with the bookings linked to this exam:
exam name, date, start/end times, duration, campus and request date. Leading
zeroes in UINs are preserved; DR times are displayed in the center's local time.
Multiple linked bookings are all listed and flagged for review. The roster
rechecks current seating, DR schedules and saved card responses before printing; if anything changed,
review the updated preview and click Print again. Use Letter paper in portrait
orientation. This instructor roster is separate from the anonymous PDFs emailed
to students and creates no print job or saved artifact.

**Email student charts** lets you review the recipient list and open each
student's PDF before choosing **Send emails**. Their own seat is blue and says
**Your seat**, with its seat number underneath. Other occupied seats are gray
and say **Occupied**, with their seat numbers underneath. Empty seats stay
unshaded. The PDF includes no student names, response-card statuses or exam
versions. Large rooms include a full-room overview and an enlarged view of the
student's seating area.

This action works without confirming the chart for printing or preparing
response cards. Choose the default email service or connected Gmail. Each
student receives a separate attachment at their roster address; missing or
shared addresses and students without classroom seats are excluded. The default
audience skips students already sent the current chart. **Send again** includes
previously sent copies, while active and unconfirmed deliveries of the same
chart remain excluded. **Refresh preview** shows delivery history. If a queued
copy becomes outdated before delivery, Faculty stops it; review the updated
preview to send the new chart. Sending does not alter student response cards.

Preview and send use `GET`/`POST /api/seating/<id>/student-email`, with
`audience=unsent|all` and `mail_provider=default|gmail`. Sending requires the
preview fingerprint and a unique request ID; retrying that exact request
retrieves its receipt without queueing duplicates. The Inbox worker prepares
each PDF from its approved anonymous snapshot, sends it and retains delivery
receipts. Uncertain deliveries are never
automatically retried. PDFs and delivery records belong to the existing chart
in the catalog and its nightly backup, and are excluded from metadata exports.

A seating chart belongs to the course term and lists its sections explicitly;
it references the course-wide assignment, the workspace room, and permanent
section/enrollment identities without copying roster or chart data. A room used
by a chart cannot be removed until the chart is. The API is
`GET`/`POST /api/seating` (one room), `POST /api/seating/plan` (`allocations`
of `room_id` plus `section_ids`, one chart per room), `GET /api/seating/catalog`,
`GET /api/seating/options` (`assignment_id`, comma-separated `sections`, with
per-section counts), `GET`/`PATCH`/`DELETE /api/seating/<id>`, and
`POST /api/seating/<id>/update|regenerate|move`, and `POST
/api/seating/<id>/exam-versions` (`count`, optional `allow_unequal_versions`) or
`.../exam-versions/edit` (`seat_id` with `target_seat_id` to swap, or
`exam_version` to set), all JSON with the
room-style `version` check. Exam-version counts and seat letters belong to the term's
existing chart and are retained by its nightly catalog backup. These letters
are separate from assignment document/PDF versions. Student seat assignments
and per-seat exam letters are not part of the metadata JSON export.

## Seating charts on printed stamps

Under Assignments & printing, **Create New Assignment** attaches or generates
the seating chart, and its last step confirms each chart for printing (the same
confirmation as on the chart page). One copy is then allocated per seated
student of each section, in seat order, plus any **extra unassigned copies**
you ask for. Each copy is linked to the student, and its page stamps
print a caption beside the Data Matrix mark: `Seat K5 · Example, Alice · Sec
507 · HECC 105`. The caption sits in the bottom band the LaTeX class already
reserves, outside the stamp box, so the mark, its quiet zone, and the
qualified layout are unchanged. The barcode payload remains the opaque page
token; the seat and student are recorded on the printed copy in the catalog.
**Print each student's seat number beside the stamp** (ticked by default in
both export dialogs on a chart) adds the seat label
itself, in bold 13 pt, immediately left of the stamp box with its top on the
box's top edge, so a proctor can read the seat from across the desk. It stays
in the reserved band and never touches the mark. The choice is part of the
job's print instructions (a retry with the opposite choice is a different
job) and is recorded in the job's manifest; peel-off labels already print the
seat as their first line and are unchanged. While a job is still waiting in
the print queue (pending, or failed and not yet retried), **Add seat numbers
beside stamps** / **Remove seat numbers** on the job itself and on the chart's
print-job list changes the choice before the Inbox worker renders it; the
change is recorded in the job's history. An issued job's pages are fixed.

Each export creates one print job per section, because print jobs are owned by
sections, but the chart page also offers the **whole room, in seat order**: one
print PDF and one label file per export, with every section's copies
interleaved row by row (seated copies first in seat order, unassigned extras
last) so papers and labels can go straight into folders per row. The room
files are cut from the sections' already-verified outputs (the print PDF and
the roll labels are page re-orderings; the Avery sheet is laid out afresh and
validated) and carry exactly the same stamps as the section files, so print one
or the other, never both. They become available once every section's job is
ready and current, and go away when one is superseded. The same room links
appear on each section job's dialog under Assignments & printing, so the
print queue leads to them too.

**Confirm seating chart and export to printing** on a chart page runs the
whole hand-off. If the roster check has unresolved issues, they are listed and
you choose to go back and resolve them or continue anyway (the acknowledged
issues are recorded). If the exam has a registered PDF version, you pick it
and Faculty creates one print job per section with one stamped copy per seated
student, plus any extras. If no PDF is attached, it says so and offers a
**stamps-only export**: enter the number of pages in the paper exam and Faculty
registers a "Stamps only · N pages" version of blank pages, creates the print
jobs from it, and their labels are the stamps to stick on the exams printed
elsewhere. Labels come as a PDF sized for the Brother QL-800 label printer:
one 29 × 90 mm DK-1201 address label per page by default, with 62 × 29 mm
labels and Avery 5160 sheets as alternatives on the chart's print-job list and
in the print job itself. Print them at 100% with the matching paper size
selected in the Brother driver. Either way the chart is marked confirmed, later seat changes no
longer reach the printed stamps, and removing the chart needs typed
confirmation from then on, whether or not the print jobs are later cancelled.

A stamp identifies the printed copy, and the copy is tied to the student it
was printed for, not to a seat. Scanned work resolves through the stamp to
that student regardless of where they eventually sat, so moving a student
after printing needs nothing on the scanning side as long as each student
receives the paper carrying their own name. The seat printed on the paper and
its label is a snapshot from the moment of export; the chart flags a moved
student's stale card but does not reprint. If a student ends up with a paper
printed for someone else, use **Change assignment** on that copy in the print
job (Assignments & printing) to move the copy to the right student before or
after scanning; the change is recorded with a reason.

Both export dialogs carry a ticked-by-default option, **Prepare response
cards and email each student their link, or export for Gmail**. Untick it and the export finishes
right there with no cards prepared (the cards from an earlier export are kept
on record). With it ticked, a review window follows, showing the two response
cards side by side, with the exam date and time to fill in at the top. The **seat card**
tells each seated student the exam time, the room, and their seat, then asks
them to confirm the building/room and the seat; those answers accept spacing,
punctuation, and capitalization differences but reject anything else. The
**DR card** says only that records show they are registered with the
Disability Resource Center, that they MUST take the exam as scheduled with the
DR center, and to inform Professor Sinkule immediately if that is wrong; it
never states the time or place, but asks them to enter the date, start time,
and campus; the date and start time are checked against their current booking and
flagged for your review if they differ (the campus is recorded but never checked,
since the DR center moves locations often). Both cards end with "Type your UIN to confirm that
you have read and understood all of the above", which accepts only the
student's own UIN (digits only; spaces and dashes ignored) and is verified
against a salted hash so the UIN itself never reaches the public service.
Edit the statements, add response boxes or checkboxes, remove items, or skip
the cards; edits become the course's "(Faculty)" card templates for next time.
**Publish the cards and email each student their link now** is ticked by
default, with **Invitation delivery → Faculty email** selected: confirming publishes every card and queues one email per student with
the subject "IMPORTANT: Information about your upcoming Test 1 for MATH 140",
telling them to open the personal link and complete the card. The Inbox worker
sends the mail through the configured outbound provider, shown under
**Email delivery** on the Response Cards page. When a
student submits their card, a confirmation email listing what they entered goes
back to them automatically (flagged answers are marked, and the DR booking on
record is never disclosed). Invitations and receipts use **Hello,** without a
student name in the greeting; receipts still include the card and submitted
answers. Untick the box to keep the cards in Faculty only.
Students with more than one booking linked to the exam get no DR card and are
listed for you to contact directly; students with no roster email address are
listed too. Card mail (invitations, receipts and reminders) comes from your
connected TAMU Gmail, the workspace default since 2026-10-16 (well within Gmail's
2,000 messages a day; a full exam round is about 350). Print jobs' **Export by email**
sends from the same Gmail since 2026-11-01, so Faculty no longer sends anything through
Resend. **Invitation delivery → TAMU Gmail** when confirming a
chart and Gmail under **Send from** remain as explicit choices; each new dialog
starts with the default provider. Gmail sends directly from Faculty and needs no spreadsheet.
Queued messages retain the selected provider and never fall back to another
provider if Gmail becomes unavailable. See the [email setup and activation status](cloudflare/teach-inbox/PRINT_EXPORT.md#resend-outbound-delivery).
Confirmed charts also have **Send with Gmail** beside **Gmail mail merge**.
It previews the current seat and DR card recipients and excluded students.
Choose **Not emailed yet**, or explicitly choose **All awaiting response** for
earlier recipients, then click **Send emails**. Faculty queues the displayed
invitations through the connected Gmail account and tracks their delivery;
opening the dialog sends nothing. Gmail is the workspace default.
Moving a student after confirmation turns their **new seat orange**, with their
name still visible. Both students turn orange after a swap. The old response
card is deactivated, and any queued email for it is cancelled. Moving a seat
does not email anyone. Use **Send updated seat cards** to review the new seats
and choose the default sender or connected Gmail account. The updated card says
that the seat changed, names the new seat, and asks only for UIN confirmation.
Orange remains while mail is queued or sending cannot be confirmed; it clears
after the current update is confirmed sent. Refresh the chart or the send
preview to see that result. If deactivating an earlier link fails, Faculty
shows the problem and waits before distributing the replacement.

With Faculty email, confirming the same chart again later also prepares these
short "UPDATED" cards and preserves current cards for everyone else. A definite
failed send can be explicitly retried; check an unconfirmed delivery with the
student before using **Resend email** on their seat. Downloading a spreadsheet
does not clear orange because Faculty cannot observe a manual Gmail send.
The DR card asks for the booked start time with hour,
minute, and AM/PM boxes.

To send these invitations yourself from Gmail, select **Invitation delivery →
Gmail mail merge** in that review window. Confirmation publishes the personal
response links, queues no Faculty invitations, and opens the spreadsheet preview.
On any confirmed chart, **Gmail mail merge** opens the same preview later.
Keep **Not emailed yet** for the usual first export; it excludes invitations
accepted, unconfirmed, queued or being sent by Faculty. **All awaiting response
(including previously emailed)** also permits earlier and uncertain deliveries,
but always excludes active Faculty mail jobs. Review the listed students and the
reasons for everyone skipped, then choose **Download .xlsx**.

1. Import the workbook into Google Sheets, keeping **Recipients** first.
2. In Gmail, compose with mail merge enabled and choose **Add from a spreadsheet**.
   Map **Email** and **FirstName**. FirstName contains the fixed value **Student**;
   the recipient sheet has no Name or LastName columns.
3. Use the constant subject shown by Faculty. In the body, type **@** and select
   the **Message** merge chip, then preview and send in Gmail. Pasting the text
   `@Message` alone does not create the chip.

Each Message contains the initial invitation with **Hello,** and its personal
plain-URL response-card link. Previously emailed recipients get the same wording,
without an added second-copy paragraph; actual replacement cards retain their
replacement notice. Gmail needs an eligible Workspace plan and does not support
merge tags in subjects or hyperlinked text. See [Google's mail merge guide](https://support.google.com/mail/answer/12921167?hl=en).
The workbook includes **Instructions** and, when needed, **Skipped** sheets.
Actual student names remain in the instructor preview and Skipped sheet only.
Download a fresh workbook before sending; earlier exports keep their old
greetings and columns.
It contains personal links; keep it private. Exporting sends nothing, changes no
delivery records, and does not track a later Gmail send. Student responses still
sync to Faculty normally, so check Gmail's Sent folder before exporting again.
Refresh the preview after any change; an outdated export is refused. For a card
whose exam date, DR booking, name or email changed, revoke the old card in
**Response Cards**, then reconfirm the chart and download a fresh workbook.
See the [mail merge contract](response_cards/README.md#gmail-mail-merge-from-a-seating-chart)
for eligibility and data handling.

Once cards exist, the chart colours each seated student by card status on every
refresh: green means the card was completed, yellow means it is still pending
(queued, emailed, opened, or started), and red means something needs you: the
answers were flagged for review, the email failed or has no address, the card
was revoked or expired, the student was seated after the cards went out, or the
card names a different seat than the chart now shows. Click a coloured seat to
expand it: green and yellow say completed or pending, red lists the problem,
and the popover also offers Move or swap and a link to the card in Response
Cards. While a card is still pending (emailed but not yet answered, or the
email failed or could not be confirmed), the popover also offers **Resend
email**: after a short confirmation, a second copy of the invitation with the
same personal link is queued for the student's roster address, worded as a copy
of an earlier email, and the seat shows "Emailed 2 times" once it goes out. A
card that was answered, revoked, or expired, or that names a different seat
than the chart now shows, cannot be resent; re-confirm the chart instead.
Nothing is ever resent automatically. While a card still awaits a response,
the popover also shows its personal link with a **Copy** button, so you can
hand the link to a student directly when the email did not reach them (for
instance when the roster address is not one they read). The student table gets a Response card column with the same states, DR
students included: the note under each status names the specific problem
(for example which answer differed and what the records show), and clicking
the status opens the same details, link, and actions in a dialog, which is how
you reach a DR student's card since they have no seat to click. The room
summary counts completed, pending, and issues.

**Download labels (Avery 5160)** on a ready print job produces peel-off stamps:
one label per page stamp with the Data Matrix at the label size of the paper's layout (10 mm
for current paper; 20 mm only for single-mark pagecode paper and stamps-only label versions), recovery alias, seat,
name, section, exam, room, and copy/page number, thirty per US Letter sheet.
Use it when the exams are printed elsewhere and you stick the stamps on
afterwards. Label sheets are rendered and validated in the same sandbox as
print PDFs, retained beside the print output, and verified on every download.
Print jobs without a seating chart get generic labels (exam, section, copy and
page). A seating chart page lists the print jobs made from it and offers
**Print exams for this room**, which opens Create New Assignment at its PDF
version step with the exam's charts attached. Once the latest export's files are
ready, its Print group starts with the room's stamped papers (or stamp labels) and a
jump to every print file; the exam-day packet follows, then the room handouts (room
roster, version list). Exporting prints the one PDF chosen on every seat, so a chart
whose seats have Versions A, B, … says so in the export window (unless a stamps-only version
is chosen, which labels papers printed elsewhere). Removing a chart keeps the captions already printed.

## Response Cards

**Response Cards** is a standalone course-owned template and acknowledgment
engine. Use its visual builder to author and freeze a version, issue personalized
cards to a current term's section enrollments, and inspect responses and
verification history. Optional seating and assignment context supplies variables.
Publishing creates an immutable snapshot on a separate Cloudflare Worker/D1
service; student forms work while Faculty is offline. Draft saving, conditional
fields, expected-value checks, revocation, and submission policies are supported.
Issuing or publishing does not send email.

See [Response Cards](response_cards/README.md) for ownership, configuration,
reconciliation, backups, and checks, and
[the public service](cloudflare/response-cards/README.md) for Cloudflare setup.
Local authoring works before the public service is configured; publishing and
student submissions require the Worker, D1, and Turnstile deployment.

## Run directly

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open [http://localhost:3000](http://localhost:3000) on the same machine.
Waitress serves the application; no frontend build step, Node runtime, external
font service, or CDN is needed. Internet access is needed for the initial dependency
installation, and for any external tools you choose to link.

Configuration uses environment variables:

| Variable                  | Default                                 | Purpose                                                                                  |
| ------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------- |
| `HOST`                    | `127.0.0.1`                             | Bind address                                                                             |
| `PORT`                    | `3000`                                  | HTTP port                                                                                |
| `DATA_DIR`                | `data/` alongside `app.py`              | Isolated development workspace; when set (without `FACULTY_REQUIRE_STORAGE=1`) it wins over a storage config. Otherwise the SQLite directory when drive storage is not configured                        |
| `FACULTY_STORAGE_CONFIG`  | `.faculty-storage.json` beside `app.py` | Drive paths and deployment identity                                                      |
| `FACULTY_REQUIRE_STORAGE` | unset                                   | Set to `1` to refuse an unconfigured fallback workspace (enabled in the systemd service) |

For example, use another port if 3000 is occupied:

```bash
PORT=3100 .venv/bin/python app.py
```

## Optional Docker development workspace

**The home-server storage deployment uses the native systemd services below.**
The default Docker configuration is a separate development workspace. Its named
volume binds only `/mnt/nvme/Teaching/docker-development`, with separate web and
worker scratch directories there; it does not mount your live course workspace or
run the nightly backup timer.
Do not use it to replace the configured home-server service.

Requires Docker Engine with the Compose plugin.
Create the development directory on the mounted NVMe drive first (the image runs
as user and group 10001):

```bash
sudo install -d -m 700 -o 10001 -g 10001 /mnt/nvme/Teaching/docker-development
```

```bash
docker compose up -d --build
docker compose logs -f faculty
```

The container runs as an unprivileged user, stores its database in the
`faculty-data` named volume, and restarts unless explicitly stopped. The host port
is bound to **127.0.0.1:3000**, ready for Tailscale Serve or an SSH tunnel.

```bash
docker compose down                 # Stop; keep saved tools and settings
docker compose up -d --build         # Rebuild and restart after code changes
```

`docker compose down -v` removes the named volume's binding; the files in that
NVMe directory remain. Direct Python and Docker use separate databases by
default; see the backup/migration instructions below to carry a workspace
between them.

## Reach it through Tailscale

Install and sign in to Tailscale on your home server and client devices. Start
Faculty, then run this on the server:

```bash
tailscale serve --bg http://127.0.0.1:3000
tailscale serve status
```

Follow any Tailscale setup prompt, then open the HTTPS address printed by the
command on a device connected to your tailnet. The `--bg` configuration persists
in Tailscale; the app still needs Docker or a service manager to stay running.
Use the Tailscale access rules for your tailnet to restrict access to your devices.

See the official [Tailscale Serve reference](https://tailscale.com/docs/reference/tailscale-cli/serve).
The Connection guide in **Workspace settings → Access from other devices** also includes these instructions. Faculty does not
install or configure Tailscale itself.

Alternatively, from your laptop, open an SSH tunnel over the server's Tailscale
hostname or IP:

```bash
ssh -N -L 3000:127.0.0.1:3000 your-user@your-server
```

Then visit [http://localhost:3000](http://localhost:3000) on that laptop. Keep the
SSH session open. If local port 3000 is taken, change the first `3000` to `3100`
and visit `http://localhost:3100` instead.

### Access boundary

This is a single-user workspace with **no application login or browser-session
requirement**. Rosters, accommodations, documents and print exports work directly;
restarting Faculty does not require renewing a cookie or reopening its home page.
Anyone who can
reach the service can read and change the workspace. Keep the loopback binding
and use a private tunnel or Tailscale Serve with suitable access rules. Do not
publish this version through Tailscale Funnel or a public reverse proxy.

Writes require a custom same-origin header; cross-site browser writes are
rejected. Roster uploads accept bounded multipart data; other writes require JSON. The frontend uses a same-origin content policy, escapes stored
text, and accepts only HTTP(S) URLs or paths beginning with a single `/`.
These protections do not replace access control.

## Keep it running without Docker

A user-level systemd unit is included in `deploy/faculty.service`. Its example
path is `~/code/Teaching`; adjust both paths if you clone somewhere else. Run the
Python setup and **drive provisioning below** before installing it. Use this instead of Docker, so both
processes do not compete for port 3000.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/faculty.service deploy/faculty-inbox.service deploy/faculty-backup.service deploy/faculty-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now faculty.service faculty-inbox.service faculty-backup.timer
systemctl --user list-timers faculty-backup.timer
journalctl --user -u faculty -f
```

The root disk of this server is small, and a section's scan is written to scratch
space many times while it is inspected. `deploy/faculty.service.d/temp-space.conf`
and `deploy/faculty-inbox.service.d/temp-space.conf` set `TMPDIR` to a per-service
folder under `/mnt/nvme/Teaching/tmp/`, so sandboxed PDF work, LaTeX builds and
upload spooling stay on the NVMe data drive. Copy them beside the units, adjusting
the path to your NVMe root. That folder is runtime scratch next to the managed
storage, never a payload owner and never backed up. Because nothing empties it at
reboot the way `/tmp` is emptied, each service deletes only its own leftovers when
it starts.

`deploy/faculty.service.d/stop-wait.conf` lets a restart wait for Faculty's writes to Canvas, a whole Sync with
Canvas included (`TimeoutStopSec`, canvas/stopping.py `STOP_WAIT`): copy it beside the unit as well.
`deploy/faculty.service.d/keep-port.conf` keeps the port open while a restart replaces the process (FRONTEND "A RESTART NOBODY SEES").

```bash
mkdir -p ~/.config/systemd/user/faculty.service.d ~/.config/systemd/user/faculty-inbox.service.d
cp deploy/faculty.service.d/temp-space.conf deploy/faculty.service.d/stop-wait.conf deploy/faculty.service.d/keep-port.conf ~/.config/systemd/user/faculty.service.d/
cp deploy/faculty-inbox.service.d/temp-space.conf ~/.config/systemd/user/faculty-inbox.service.d/
systemctl --user daemon-reload && systemctl --user restart faculty faculty-inbox
```

For startup before login and continued operation after logout, enable lingering
for your Linux account (this may require administrator rights):

```bash
loginctl enable-linger "$USER"
```

## Connect future tools

Rosters are built into the course section pages. The original planned Course
Rosters shortcut remains editable like any other tool. For a separate service,
enter its URL and change its stage to **Ready**; tools open in a new browser tab.

Use a URL the client device can reach, such as its Tailscale Serve HTTPS address.
A `localhost` link opens on the phone/laptop you are using. The command center's
tunnel does not tunnel other apps, and Faculty is not a general reverse proxy.
Preserve the original Host header if adding a reverse proxy so same-origin writes
continue working. Future modules may be Flask blueprints or separate services;
student-related records should refer to the private course enrollment ID.

## Course storage, backup, and recovery

The home-server deployment uses these dedicated paths:

| Location                                                 | Contents                                                            |
| -------------------------------------------------------- | ------------------------------------------------------------------- |
| `/mnt/nvme/Teaching/courses/<course-id>/`                | Current course metadata and working `data/` directory               |
| `/mnt/bulkdata2/Teaching/courses/<course-id>/snapshots/` | Timestamped, verified course snapshots                              |
| `/mnt/nvme/Teaching/terms/<term-id>/`                   | Current term rosters and section payloads                            |
| `/mnt/bulkdata2/Teaching/terms/<term-id>/snapshots/`      | Verified term/section roster snapshots                               |
| `/mnt/nvme/Teaching/catalog/faculty.sqlite3`             | Live course identities, UIN prefixes, enrollment IDs, settings, and jobs |
| `/mnt/nvme/Teaching/locks/`                              | Cross-process lifecycle, owner, worker, and backup locks             |
| `/mnt/nvme/Teaching/inbox/<message-id>/`                  | Inbox intake and processing data awaiting nightly backup, plus current resources |
| `/mnt/bulkdata2/Teaching/workspace-backups/`             | Standalone, integrity-checked SQLite catalog snapshots              |

The shared live catalog stays on NVMe and is backed up to HDD nightly, so Inbox
request threads do not write their transactions or lock files to HDD. **All Courses**
still includes archived courses without keeping all their payloads on NVMe.
Every current course also has its own
metadata JSON and data directory on NVMe. The original development `data/`
directory is retained after migration as a safety copy; the running service no
longer writes to it. All data directories and deployment configuration are ignored
by Git.

Existing HDD-catalog deployments must be migrated while all Faculty processes are
stopped: stop `faculty.service`, `faculty-inbox.service`, `faculty-email-gateway.service`,
`faculty-backup.timer` and `faculty-response-cards.timer` (if the Response Cards timer
is installed; its reconciler opens the catalog), and ensure `faculty-backup.service` and
`faculty-response-cards.service` have finished. Run
`.venv/bin/python manage_storage.py migrate-catalog`, then restart those services
and the timers. The SQLite online backup API includes WAL transactions; migration
checks database integrity, logical equality, and read-back bytes before atomically
setting `catalog_on_nvme: true` in `.faculty-storage.json`. It also moves the lock
domain to NVMe. The old `/mnt/bulkdata2/Teaching/catalog/faculty.sqlite3` is retained
as a pre-migration copy and must never be reopened as the live catalog. A missing
NVMe catalog stops startup instead of silently falling back to that old copy.

### Provision the two drives once

Stop the existing app before migration. Both physical drives must already be
mounted. Create dedicated empty directories (substitute your Linux username if
needed):

```bash
sudo install -d -o faculty -g faculty -m 700 /mnt/nvme/Teaching /mnt/bulkdata2/Teaching
.venv/bin/python manage_storage.py init
```

`init` copies the existing `data/faculty.sqlite3` with SQLite's online backup API,
checks it, upgrades the schema, prepares each course, and runs an initial backup.
The source database is preserved. Use `--source /absolute/path/faculty.sqlite3`
for a different original catalog. Local configuration is activated only after the
initial backup succeeds. If setup is interrupted, rerun `init`; its setup marker
allows it to resume without replacing a catalog or another workspace's drive
identity.

Then install the user services using the commands above. The included paths assume
`~/code/Teaching`. The service requires drive storage and will refuse to open a
fallback database if its configuration is missing. Each drive carries a matching
workspace identity marker; missing mounts, wrong disks, or redirected paths stop
storage operations.

### Lifecycle and nightly schedule

- **Current:** the working data lives on NVMe. The backup timer runs at **4:00 am Central
  time** (America/Chicago; 02:00 UTC until 2026-09-27) every night. A current course that is
  unchanged since its recorded HDD snapshot keeps that snapshot once it is read back from the
  HDD and verified again. A changed course is copied to a new HDD snapshot, flushed, and
  checked against SHA-256 checksums and the complete directory tree. It also takes a
  consistent SQLite catalog backup and checks database integrity. The app waits only while
  every working copy is hashed again and the catalog is copied. Verification and copying
  happen before that wait, and the catalog is checked and written to the HDD after it.
- **No longer current:** saving the checkbox queues a final HDD snapshot immediately.
  A successful verification and its exact snapshot pointer are committed to the
  catalog before any NVMe deletion begins. If the copy, verification, or catalog
  commit fails, deletion does not begin. An interrupted purge resumes using that
  committed snapshot, without overwriting it with partially deleted data.
- **Current again:** the verified HDD snapshot is copied back to NVMe and checked
  before the course becomes available for editing.
- **Created or edited as non-current:** data stays on HDD; edits publish a new
  snapshot. All Courses includes current and non-current courses.
- **Removed:** the course disappears from the collection, while the final verified
  HDD snapshot is retained. Any NVMe working copy follows the same safe archive
  procedure. Removal keeps that final snapshot; older snapshots follow the
  retention rule below.

Moves run in a background worker and survive application restarts. Settings are
unavailable during a move; operations with errors retry every minute. The UI shows
pending moves, per-course verification times, the last nightly result, and errors,
including errors for removed courses. Pages refresh pending moves every 3 seconds.
The timer uses `Persistent=true`, so a missed scheduled run is caught up when the
timer next starts. Linux user lingering keeps the services available after logout.

Snapshots are full copies. Nothing is deleted automatically. The instructor's
retention rule (2026-09-27) is applied only by `manage_storage.py prune`, below. Current
course changes since the last successful nightly copy remain on NVMe until the
next backup or final archive. Failed HDD staging copies are retained for inspection,
except a copy of data that changed while it was being copied. A snapshot copied
ahead of the locked step and not recorded (its course changed in between) stays as
an unreferenced snapshot. Only published snapshots referenced by the catalog are
authoritative. A recorded snapshot that fails verification is reported in the
nightly result and is never reused. NVMe restore
staging is expendable and is reused on retry. The app does not automatically
replace a missing active directory with an older backup.

### Inspect and run backups

```bash
.venv/bin/python manage_storage.py status
.venv/bin/python manage_storage.py backup       # Run the nightly job now
.venv/bin/python manage_storage.py verify       # Check each recorded course snapshot
.venv/bin/python manage_storage.py reconcile    # Retry pending moves now
.venv/bin/python manage_storage.py prune        # Dry run: what the retention rule would delete, and its fingerprint
.venv/bin/python manage_storage.py prune --apply FINGERPRINT   # Delete exactly that list
systemctl --user list-timers faculty-backup.timer
journalctl --user -u faculty-backup.service -u faculty.service
```

A failed backup exits nonzero and is recorded both in the UI and the service log.
There is no email or push notification integration yet. Verification and reads of
archived data never make those snapshots into writable working copies.

The retention rule keeps every catalog backup for 14 days, then the newest of each week
for about three months, and the newest of each month for good. It keeps every snapshot
that the catalog or a kept catalog backup records, and every snapshot younger than 14
days. `prune` without `--apply` only lists what the rule would delete, with sizes and a
fingerprint. `--apply` deletes that exact list, and refuses if anything changed since the
list was shown. Prune runs only after a complete nightly backup. It deletes catalog
backups first, once the newest one reads back. It deletes a course's snapshots only once
the snapshot the catalog records for that course reads back. Each run is added to
`retention-log.jsonl` on the HDD.

### Integrate roster data and future course modules

Existing saved-offering payloads use the storage manager, so backups and moves
can hold the same cross-process lock as writers. This helper currently accepts a
saved entry ID. Canonical term payloads use `storage.term_storage.course_files`
under `app.extensions["rosters"].lock()`, as the roster service demonstrates:

```python
storage = app.extensions["course_storage"]
with storage.course_files(offering_id, write=True) as data_dir:
    # Only data owned by this existing saved entry belongs here.
    # Close every file and database connection before leaving this context.
    (data_dir / "notes.txt").write_text(notes, encoding="utf-8")
```

Use `write=False` for reads, and never modify a snapshot returned for a read.
Archived writes stage on HDD and commit a new verified snapshot only if the context
completes successfully. Files must be regular files or directories; symbolic links
and special files are rejected. All SQLite writers must close their connections
before releasing the course lock, so backups capture a quiescent database and its
sidecars. Do not keep connections alive between contexts or write directly into
these folders from an uncoordinated process.

New modules must follow the three-level ownership
hierarchy and integrate the corresponding owners with locking and verified
backups. A course-wide NVMe copy must remain while any current term/section needs
it, and a term-wide copy must remain while any current section of that term needs
it. Archiving one descendant must not purge another's shared resources. Term/section
roster storage and course-wide definition storage (`DefinitionStorage`, owned by
`course_definitions.id`; used by assignment versions, templates, course notes and the
question bank) implement this rule.

A shortcut to an unrelated app does not bring that app's database into
these backups; separate services must integrate with this manager (or an API using
it). The command center cannot infer their data locations from URLs.

### Recovery

Keep both drive identity markers and the local configuration with the deployment.
For normal reuse of an archived course, mark it current and let Faculty restore it.
For disaster recovery, stop `faculty.service`, `faculty-inbox.service`,
`faculty-email-gateway.service`, `faculty-backup.timer` and `faculty-response-cards.timer`
(if the Response Cards timer is installed; its reconciler opens the catalog) and
ensure `faculty-backup.service` and `faculty-response-cards.service` have finished. Preserve the damaged catalog and any
WAL/SHM files before replacing them. Choose a catalog snapshot whose integrity and
checksum pass, and copy its standalone `faculty.sqlite3` into the NVMe catalog directory
with mode 600 and the service user's ownership. Do not mix it with old WAL/SHM files.
Keep all course snapshots: the catalog contains pointers to the required versions.
Pruning keeps every snapshot that a kept catalog backup points to, so any catalog
backup that is still on the HDD can be restored.
Run `verify` before starting the services and timers again. Inspect pending states and NVMe
copies before retrying moves after an older catalog has been restored.

If NVMe is lost, retain the HDD snapshots, recreate the dedicated NVMe directory
and its matching identity marker, restore the catalog from the latest verified
nightly snapshot, then restore current course and Inbox payloads from their recorded
verified snapshots under an explicit recovery procedure. The app intentionally
stops when active data is missing instead of hiding possible changes made after
the latest backup. There is not yet a disaster-recovery UI.

**Workspace settings → Export** remains a readable metadata-only JSON export
(schema version 4), without roster files, Inbox jobs/artifacts, or snapshot contents. It is separate from
the full filesystem and SQLite backups above.

The backup implementation uses the official
[SQLite online backup API](https://www.sqlite.org/backup.html); scheduling follows
[systemd timer semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).

## Project layout and checks

```text
app.py                  Flask API, SQLite persistence, Waitress entry point
canonical_json.py       Canonical JSON text and SHA-256 for receipts, idempotency keys and fingerprints
course_codes.py         Course-code normalization and component validation
course_groups.py        Term/code collection and course-home projection
course_storage.py       Verified snapshots and recoverable course/term moves
rosters.py              Permanent owners, student ID registry, roster storage
roster_import.py        Bounded CSV and XLSX export parser
rooms.py                Campus rooms, seat conditions, chart replacement, and API
seating.py              Exam seating charts: random seating, DR exclusion, updates, moves
seating_mail_merge.py   Confirmed-chart Gmail spreadsheet preview and download
exam_links.py           DR booking to exam assignment links: matching, auto-creation, relinking
room_import.py          Bounded XLSX seating-chart parser
manage_storage.py       Drive provisioning and backup command line
manage_inbox.py         Local command submissions and independent queue worker
teach_inbox/            Protocol, routing, auth, API, handlers, queue, scoped storage
static/index.html       Accessible page shell
static/app.js           Shell: routes, views table, sidebar badges, settings hub, toasts
static/page-header.js   Page header and breadcrumb (header.set)
static/ui.js            Shared helpers: callouts, disclosures, empty/loading states, action menus, shortcuts
static/components.css   Shared components: header, cards, callouts, chips, step graph, palette
static/palette.js       Command palette (Ctrl K) and the '?' shortcut sheet
static/system-status.js Sidebar system status popover and ready/finished notices
static/dom-patch.js     In-place redraw of polled pages
static/home.js          Home: Needs you, In progress, Coming up, Workspace health
static/courses.js       Course lists, course homes, section tabs and rosters
static/scan-pages.js    Inbox scan stage pages
static/print-files.js   Print job and export files, label media
static/inbox.js         Teaching Inbox client, review, audit, and access controls
static/inbox.css        Inbox views
static/icons.js         Local SVG icon set
static/style.css        Components and forms
static/layout.css       Layout and print styles
static/course-pages.css Course collections, home pages, and section tabs
static/rooms.js         Room list, seating chart, seat selection and conditions
static/rooms.css        Room cards and the seating-chart grid
static/seat-grid.js     Shared seat/table/caption grid renderer
static/seating.js       Exam seating chart controller: list, chart view, history, update and regenerate
static/seating-create.js Chart creation (catalog, options and plan workflow)
static/seating-interactions.js Seat moves and exam-version editing
static/seating-print.js Print previews and print cleanup (chart, version list, room roster, exam packet)
static/seating-delivery.js Student chart email, Gmail invitations and mail-merge dialogs
static/seating-state.js Pure read-only views of one chart payload (next step, filters, exam date)
static/seating-roster.js Room roster print output
static/seating-versions.js Print-time check of the saved exam-version list (the server assigns the letters, in seating_versions.py)
static/seating.css      Seating chart cards, chart view, and print layout
compose.yaml            Private host binding and persistent Docker volume
deploy/faculty*.service Application and backup user services
deploy/faculty-backup.timer Nightly 4:00 am Central schedule
web_assets.py           Immutable asset prefix and the archive of earlier builds
exam_workflow/overview.py Read-only exam hub projection (one assessment in one term)
tests/                  API and real-browser checks
```

Run the API tests (no test dependencies required):

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Check saved exam versions, balanced counts and room ordering, seat retention,
edits, conflicts, and API validation:

```bash
.venv/bin/python -m unittest tests.test_seating_versions -v
```

Check saved version-list validation with Node (no packages needed):

```bash
node tests/test_seating_versions.mjs
```

Optional browser checks use an isolated temporary database and write screenshots
and print PDFs to `test-results/`. They never modify your real workspace:

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
# On Linux, if browser system libraries are missing:
# .venv/bin/playwright install-deps chromium
.venv/bin/python tests/browser_smoke.py
.venv/bin/python tests/browser_navigation.py
.venv/bin/python tests/browser_palette.py
.venv/bin/python tests/browser_badges.py
.venv/bin/python tests/browser_cross_links.py
.venv/bin/python tests/browser_storage.py
.venv/bin/python tests/browser_course_pages.py
.venv/bin/python tests/browser_rosters.py
.venv/bin/python tests/browser_inbox.py
.venv/bin/python tests/browser_rooms.py
.venv/bin/python tests/browser_chart_zoom.py
.venv/bin/python tests/browser_seating.py
.venv/bin/python tests/browser_seating_moves.py
.venv/bin/python tests/browser_seating_updates.py
.venv/bin/python tests/browser_seating_print.py
.venv/bin/python tests/browser_seating_versions.py
.venv/bin/python tests/browser_seating_mail_merge.py
```

The checks cover tools and courses, two-browser persistence, pinning, search,
filters, roadmap, settings, export, text escaping, and the desktop layout at 1280 px. Course form
checks also cover previous-course prefilling, required fields, custom terms,
year controls, dynamic sections, and edit/reload persistence. API tests cover the
upgrade of the previous course schema without inventing missing details.
Chart gesture checks cover both room and seating views, pointer anchoring,
ordinary scrolling, zoom bounds, and listener cleanup. Seating print checks
render PDFs from the reviewed ILCB floor-plan geometries with synthetic student
names and verify complete seat coverage, page bounds, and screen-zoom independence.
Exam-version checks cover optional counts at creation, saving and editing seat
letters, switching chart views, retaining letters when seating changes, and
printing saved letters with conflict warnings and current copy totals.

Storage checks cover byte-for-byte backup/archive/restore, late changes, archived
writes, corruption, disk-full failures, partial purges, failed commits, missing
drives, redirected paths, locks, removal, and catalog restore integrity.
Course-code checks cover shared identity across offerings, concurrent creation,
prefix persistence, migration, relinking, numeric validation, and browser prefilling.
Course-page checks cover term/code grouping, section-specific details, current
filtering, keyboard navigation, direct links, history, edits that change a route,
missing courses/sections, and desktop widths.

Roster checks cover CSV/XLSX parity, all fields, leading zeros, duplicate rejection,
ID collisions, concurrent/repeated imports, cross-term prefixes, section isolation,
protected references, archive failures, last-current retention, and desktop tables.

Inbox checks cover queue transitions, retained originals, idempotency, explicit
course scopes, code/key authorization, deferred email authentication, crash recovery,
approval hashes, shared-scope backup lifecycles, and full browser workflows.

Room checks cover the three department charts (row letters, mirrored letters,
tables, merged captions, collapsed note rows, pre-marked broken seats), synthetic
layout rules and warnings, duplicate and formula rejection, size bounds, same-origin
guards, seat conditions with version conflicts, chart replacement that keeps
conditions by label, the first-schema rebuild, rename, removal, restart
persistence, and the browser workflow with shift-click block selection, table
selection, and reloads.

Exam-link checks cover Test/Exam name equivalence, matching existing
assignments, auto-created assignments and keys, startup linking of earlier
bookings, roster booking links, and explicit relinking within one course.
Seating checks cover option forecasts, multi-room plans that commit together or
not at all, the one-room-per-section guard, validation before any chart exists,
random generation that skips DR-booked students and unusable seats, capacity
errors with counts, update mode after room, roster, and DR changes, room chart
replacement, the room deletion guard, moves and swaps, confirmed-none charts,
settings changes, and the browser workflow including print media.

## Printed assignments and scanned work

Faculty now includes course-wide immutable assignment versions, section print jobs with opaque page barcodes, and Inbox page review/reassembly. See [the document identity operating guide](document_identity/README.md) for setup, migration, printing, recovery, and tests. The original layout passed the 30-page classroom printer/scanner test on 2026-09-13 using the instructor's defaults; [the physical record](document_identity/physical-qualification.md) documents the settings and evidence. The separate compact-corner test also resolved 30/30 pages, with 52/60 individual marks detected; [Test 2's record](document_identity/corner-test.md) documents per-corner performance and limits. Changed layouts require their own physical test.

Optional [local GLM-OCR tools](faculty_ocr/README.md) provide explicit Python/CLI
recognition of text, formulas and tables in images or selected PDF pages. The
pinned model runs in a separate offline environment on the server GPU. These
tools do not write teaching records themselves. The optional printed-label
fallback automatically identifies unreadable paired-code pages when an exact,
unambiguous printed alias matches the catalog. It retains an OCR report and never
authorizes actions or clears the Inbox review gate.
