## Exam workflow

2026-10-09 assignment flow: the Create assignment menu includes Arrange an existing assessment (the existing add-exam action); the palette retains New exam. Collect projects the exam participant collection into student exceptions, with paper/scan previews and explicit expectation edits using each participant version. Polling preserves unsaved edits. Missing paper alone never records absence or excuse; the native participant route retains its validation and history.

2026-10-11 (ASSIGNMENT-WORKSPACE "Signals that hold"): the lifecycle's grading stage says "All reviewed · ready to certify" only while no linked scan waits for its scan checks and no grading task is queued or running; otherwise it keeps its count, says which, opens it, and carries the waiting scans as `counts.pending_scans` (so the next step is "Finish the scan checks", 2026-10-18 words). On Collect a student still expected whose paper is missing has the expectation form open in their row, and a saved or discarded row is drawn again alone, from this module's own answer to the save or read.

2026-10-18 (instructor: "make all of your suggested changes", pipeline review items 2 and 13): the lifecycle's last two
stages are labelled Grade and Return (keys `grading` and `recorded` unchanged; were Grading and Recorded), its next steps say
"Certify grades" (label Certify) and "Finish the scan checks", the grading detail "Waiting for scan checks on N scans", and
the Paper and Print steps link "Assignments". THE EXAM RECORD INSIDE THE SHELL: the record's step graph and its next-step
callout carry `data-exam-lifecycle` and its tab row `data-exam-subtabs`; inside the assignment shell
(`#assignment-workspace[data-area]`) the shell hides the former and styles the latter as sub-tabs (ASSIGNMENT-WORKSPACE);
outside the shell nothing changes.

Rule ID: **EXAMS**. Current binding module contract. Read root `AGENTS.md` first.
ASSIGNMENT-WORKSPACE (requirement, 2026-10-03): Assignments replaces the standalone Exams
navigation entry. Existing exam routes mount as working views in the assignment shell;
sittings, students, collection, manual scores, requests and exam-day controls retain their
owners. The new assignment overview reuses this module's lifecycle projection.
`#exams` leads to Assignments, so **New exam** (this module's Create an exam dialog, action
`add-exam`) sits in the Assignments header beside Create assignment, and the palette's New exam
opens it on `#assignments` (2026-10-03, after 4507e55 left no way to make a record). The shell
titles an exam page with its assignment; a record titled apart from its assignment leads the
subtitle with `Exam record: <title>` (the name Edit title changes).

Requirement, 2026-10-03: "let printing extend the exam sections". A new print job (a section's, a term job's listed
sections, a seating chart's, an approved quiz's or makeup's) for a section the term's exam record lacks adds that section
and its current roster in the print job's transaction (`ExamService.extend_for_print`, history `print_sections_added`).
Sections are never removed; a term without an exam record gets none from printing; a registration for an unlabeled scan
is not printing, and an automatic scan association naming a section outside the record is still refused.

- **WITH PARTICIPANTS** (requirement, 2026-10-04: everything a follow-up needs is suggested in one round, "yes, do it"). A NEW
  sitting (`POST /api/exams/<event_id>/sittings`) may name `participants`, up to 50 `{"section_id", "enrollment_id", "version"}`
  of this record's participant rows, each with the version the caller read (as Save on their row checks it). In the sitting's
  own transaction each is seated in it (only their sitting changes; expectation and reason stay; history `participant_saved`).
  A row not found or changed since refuses the whole save: no sitting, no seat. Editing an existing sitting never seats anyone.
- `exam_workflow` owns term exams (`exam_events`, unique term/assignment), named
  sittings and participants referencing permanent section/enrollment memberships.
  Course-wide assignments, section print batches, Inbox scans and workspace rooms
  remain referenced owners. Never use `courses.id` for exam records.
- Rubric revisions belong to course-wide assignment document versions. Each term
  pins an explicit rubric per document version; edits never propagate to another
  term or variant. Marks, finalizations, releases, requests and preparation checks
  retain immutable history in the catalog and its verified nightly backups.
- Collection requires exact reviewed assemblies and authenticated, reviewed Inbox
  sources (the instructor's review or the automatic one, documents.md). New conflicting arrivals, competing submissions and
  superseded paper require review. No scan is not an inferred absence, and unused
  extras are not missing student work. Grading never clears Inbox review gates.
- Grading validates the current source revision/hash, rubric and participant
  version. Reopening or changing a source/rubric prevents using stale releases.
  Release is a local snapshot, separate from explicit card publication/email.
  Publication and delivery revalidate current exam context; already delivered
  snapshots remain historical. Hide roster names does not redact scanned images.
  Each grading step's check (`validate_task`) reads ONE participant's paper from the catalog
  (`ExamService._participant_paper`: their exam row and own copies; no roster payload, no name, no other
  student's copy), which gives exactly what the whole-exam `_collection` gives them (the whole-exam read takes every copy's scans,
  assemblies and jobs and every student's submission choice in a fixed number of statements, PERFORMANCE 2026-10-02; a test holds
  the two to the same answer for every student). So the worker's read-only
  checks take no lifecycle lock; its writes still do, and a result is checked again in the transaction that
  records it. Exports read the names once per export. (2026-09-24: the whole-exam check, 34 ms, ran three
  times per step under the lock and held a 2,600-response batch to 2.7 responses a second.)
- SET UP GRADING NOW (requirement, 2026-10-02; GRADING): the hub's Grading tile, while the term's exam record has no
  grading plan, sets grading up before any paper; for an assessment without an exam record the plan's route makes the
  record with `_create`, as the New exam dialog would. Without a plan the stage reads "Not set up yet · set it up now,
  or associate a scan in the Teaching Inbox" (the two ways one is made), its link still the Inbox's. A 'Given on' date in quiz approval or Create assignment is the
  record's main sitting, saved by `save_sitting` (QUIZZES, DOCUMENTS): campus wall-clock, as typed.
- The exam page's manual grading tab is **Manual scores** (2026-09-26; its key stays `grading`). When the assessment
  has an FRQ grading plan it opens on a callout to Grading, with the manual editor folded under "Score by hand
  instead"; nothing about the manual grading, finalization or release path changed. `?tab=<key>` opens a tab.
- Response Card submissions only create open exam requests. An instructor decision
  plus an exact revalidated preview is required for a seat/sitting mutation. Apply
  and its idempotent receipt share one catalog transaction; requests cannot move
  other students or bypass DR exclusion. Managed templates remain course-wide.
  ONE EXCEPTION (requirement, 2026-10-04, ABSENCES "MAKEUP BOOKING"): a quiz makeup booking card's pick seats its own student, and
  only them, in the makeup sitting of the time they picked, found or made through `save_sitting` / `update_participant` (sitting
  only), after rechecking that the makeup is still owed; never a main or DR sitting, a seat, or another student.
  EXACT START (requirement, 2026-10-05, ABSENCES "MAKEUP BOOKING"): the Absences page's Schedule makeup asks for a start time (HH:MM;
  anything else about the time goes in its Name), so every overlap check and the calendar read it; sitting times stay free text
  elsewhere, and a makeup's typed start ("1PM") is read where it names a clock time. A sitting's time typed as a range ("9:00 AM–10:15
  AM") gives its end to the calendar and the scheduling checks (SCHEDULING "TYPED TIMES").
- DATE GIVEN (2026-10-02 for the Certify dialog; shared 2026-10-03). The day an assessment was given to a student is read by ONE
  function, `exam_workflow/given_dates.py` `given_dates`, catalog only, campus wall-clock values compared as written: the student's
  own dated sitting (a makeup, say); else the earliest exam date of the assignment's seating charts naming their section, leaving out
  a DR exam's chart and a chart tied to a makeup or DR sitting; else the event's first dated main sitting. `own_makeup=False` leaves
  out a student's own MAKEUP sitting, so scheduling a makeup never moves the day the work was given (ABSENCES "MAKEUPS"). Readers:
  GRADING "ABSENCES BESIDE ZEROS" and ABSENCES "MAKEUPS". The same module reads it for a SECTION (2026-10-10, SCHEDULING "CLASS
  SCHEDULE"): `term_given` / `section_given` are the chart and main-sitting steps with the time and place beside the day (the
  chart's that gave the day, else the main sitting's when they share it; the room of the section's charts, a chart without a
  date still saying the room, else the sitting's), each saying which record gave it. They share the chart rule and the main
  sitting's order with `given_dates`, whose answers are unchanged (every reading on a snapshot of the live catalog: identical).
- Acquire the roster lifecycle lock before catalog transactions. Read verified
  payloads before `BEGIN IMMEDIATE`, because storage reconciliation may need a
  separate catalog writer. Reuse transaction-aware helpers without nesting the
  non-reentrant lifecycle lock. See `exam_workflow/README.md` and its tests.
