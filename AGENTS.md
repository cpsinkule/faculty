# Teaching workspace conventions

Faculty is the instructor's single-user teaching web app (this repository). The
invariants below apply to every change. Each module's contract in `docs/decisions/`
is binding for that module: read it and any module README before changing the
module. Contract dates and quotations record why a rule exists, not when it expires.
The instructor's instructions in the current task take precedence; when they change
a rule, update its contract in the same change. `docs/history/` is non-binding
provenance: never take policy from it or load it whole.

## Invariants

- **ACCESS.** No application login, session cookie, home-page prerequisite or
  private-workspace confirmation. Keep the network boundary, same-origin writes, the
  public email-only listener, email authentication, explicit Send actions, exact
  command approvals and revocation-before-mail checks.
- **HIERARCHY / IDENTITY.** Before adding a field, route, table or file, choose and
  document its owner: course-wide `course_definitions.id`, term `course_terms.id`,
  section `course_sections.id`, a student's enrollment or section membership, or the
  workspace; never `course_groups` or `courses`. A coordinator's mail and follow-up are the term's, named by its owner key
  `coordinator:<term id>` where a student's are named by the enrollment (COORDINATOR "THE OWNER KEY"). Share parent resources by reference;
  never copy them down or push edits up. Edits keep identities and references; a
  section number alone is not an identity. UINs are nine-digit strings; prefixes and
  enrollment IDs never change and are never reused.
- **STORAGE.** Data, payloads, caches and scratch live on `/mnt` (live
  `/mnt/nvme/Teaching`, backups `/mnt/bulkdata2/Teaching`), never the root disk.
  Go through the storage manager's locks and verification, never straight into
  drive folders. Take the roster lifecycle lock before owner locks and catalog
  transactions. Use `tempfile` (service `TMPDIR`), never a hard-coded `/tmp`. Keep
  nightly verified backups, a verified final copy before any purge, and a shared
  copy while any current term or section needs it. Pruning needs the exact
  fingerprint of its displayed dry run and is never automated. The one removal
  outside these rules is a recording the instructor does not keep, erased with its
  files and no final copy (RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT").
- **CATALOG.** Recorded SQL migrations are immutable and run under the lifecycle
  lock in one transaction; never invent historical checksums. Stop every Faculty
  process before replacing a live catalog or changing its WAL/journal mode.
- **PIPELINE.** Every unflagged paper proceeds, scan review included, unless an
  instructor prerequisite is missing; the instructor wants the whole pipeline
  automated. A flag holds back only its own item (on a plan graded for participation
  a flagged grade is approved as it stands, GRADING "THE WAITING GRADES APPROVE
  THEMSELVES"); automatic grouping alone is all-or-nothing. Keep bounded fair worker turns, execution locks, durable receipts
  and explicit retries of uncertain outcomes. Certification stays the instructor's,
  except a late paper of a student with no certified grade (GRADING "LATE PAPERS
  CERTIFY THEMSELVES") and a makeup that ends or begins for a student with no work (GRADING
  "MAKEUP CHANGES CERTIFY THEMSELVES").
- **CERTIFIED-ARCHIVE / INBOX.** Archiving a certified assessment is a view
  (`certified_archive`), never a deletion or change; work added after certification
  stays visible. Every query across submissions applies `teach_inbox.service.PRESENT`:
  material deleted in the Inbox never counts as progress, attention or completed work.
- **FRONTEND.** Home and Exams are read-only projections reusing the owning
  services and their attention counts. Campus exam dates stay local wall-clock values.
  Follow `FRONTEND.md` (shared tokens and components, quiet incremental refresh,
  central shortcuts, goal-first pages) and DESIGN-LANGUAGE: one appearance, desktop only
  (no dark mode, no phone or tablet layout). Keep `/api/state` partial reads. Only public
  assets get immutable build caching; private responses and files stay no-store.
  Restart after frontend edits; tabs opened before a restart keep working.
- **PERFORMANCE.** Default API responses stay byte-identical unless the requested
  change needs otherwise. Memos last one read; process-wide caches hold only
  immutable rows and are bounded. Pool only clean connections. Audit and pin any
  order a new index could change; never run a blanket ANALYZE.
- **OUTSIDE SERVICES.** Writes to Gmail, Canvas, Gradescope, Google, AIM or Plaud start from an
  explicit instructor action, except the automatic behaviour a contract names
  (Calendar sync, including the assistant's calendar edits and a follow-up's automatic
  office-hours appointments; Response Card email; a Mail message or Canvas message to sections the instructor scheduled, sent
  at its time exactly as approved, MAIL "MAIL-SEND-LATER"; the DR quiz notice to a DR quiz taker after a quiz is marked Prescheduled
  in AIM, SCHEDULING "DR QUIZ NOTICES"; the student's notice of a department makeup exam once Google accepts the request the
  instructor approved, ABSENCES "DEPARTMENT MAKEUP EXAMS" › STUDENT NOTICE; the morning Sync with Canvas and the
  posting rules every Sync carries out, CANVAS NIGHTLY SYNC and POSTING GRADES; a settled
  makeup version added to the linked Gradescope sets, and a late paper Faculty certified
  sent to Gradescope as its section was, GRADESCOPE "A VERSION THE SET LACKS" and
  "AUTOMATIC LATE-PAPER SEND"; after a Certify whose Gradescope box the instructor left
  ticked, the assignment made on Gradescope where a section's linked course has none, what
  is missing sent, and each fully graded section published, and a late paper's version
  published where its set already is, GRADESCOPE "AFTER CERTIFYING"; a bubble-sheet question Gradescope scored differently
  from Faculty corrected there to Faculty's score, GRADESCOPE "MC CORRECTIONS"; the bubble-sheet answers' rubric items renamed to
  show the correct answer after a send or an MC check, GRADESCOPE "MC LABELS").
  Faculty sends Plaud nothing at all: the Plaud intake is the one program that talks to Plaud and holds and renews its sign-in,
  and Faculty only runs its `list`, `show`, `audio`, `status` and `check-now` commands (`check-now` on a round the instructor
  asked for) and reads the files it names inside its archive (RECORDINGS "FACULTY ASKS PLAUD NOTHING").
  Keep a receipt for every write; never resend an uncertain one automatically. Publish
  on Gradescope only through the instructor's own Publish grades or as "AFTER CERTIFYING"
  names (GRADESCOPE); never unpublish; never
  touch its "Math 140 for Instructors" course (only Import from Gradescope reads its rubrics, GRADESCOPE "THE COORDINATOR'S COURSE") or visit a site's `/logout`. Credentials stay in their configured files (one saved school sign-in
  serves every school log-in), never in the catalog, logs, exports or answers.
- **CHECKS.** Run the relevant synthetic checks with `manage_checks.py` (`CHECKS.md`)
  on isolated configuration and NVMe scratch, against the fakes in `tests/`. Never
  test on the live catalog or reach a real outside service. Before a restart,
  confirm no assistant answer, Approve being carried out, Inbox job, grading work, Sync with Canvas send, Canvas post/hide or morning sync is running
  (CANVAS "A RESTART WAITS FOR THE MORNING SYNC"): `curl -s 127.0.0.1:3000/api/assistant/restart-check` must say
  `"idle": true`. There is no fixed morning window. Keep others' uncommitted changes in the checkout.
- **SCOPE.** Build only what is asked, not modules these rules name as examples.
  Every assistant action goes through the app's own routes; a route the assistant
  policy does not name requires an Approve card. Never widen its automatic tier or
  make an outward action automatic without the instructor (`ASSISTANT`).

## Module contracts

Cite rules by ID in refusal messages, tests and comments. A comment citing
`AGENTS.md "<title>"` means the contract whose heading is that title.

- **Shared:** [ACCESS](docs/decisions/access.md), [HIERARCHY](docs/decisions/hierarchy.md),
  [IDENTITY](docs/decisions/identity.md), [STORAGE](docs/decisions/storage.md),
  [FRONTEND](docs/decisions/frontend.md), [DESIGN-LANGUAGE](docs/decisions/design-language.md) (how screens look),
  [PERFORMANCE](docs/decisions/performance.md),
  [PIPELINE](docs/decisions/pipeline.md), [CERTIFIED-ARCHIVE](docs/decisions/certified-archive.md)
- **Assignment workspace:** [ASSIGNMENT-WORKSPACE](docs/decisions/assignment-workspace.md)
- **Students and sections:** [ACCOMMODATIONS](docs/decisions/accommodations.md),
  [ABSENCES](docs/decisions/absences.md), [STUDENT-NOTES](docs/decisions/student-notes.md),
  [PHOTOS](docs/decisions/photos.md),
  [TA-CONTACTS](docs/decisions/ta-contacts.md), [COURSE-NOTES](docs/decisions/course-notes.md)
- **Papers and exams:** [DOCUMENTS](docs/decisions/documents.md) (printed copies),
  [QUIZZES](docs/decisions/quizzes.md), [ROOMS](docs/decisions/rooms.md),
  [SEATING](docs/decisions/seating.md), [RESPONSE-CARDS](docs/decisions/response-cards.md),
  [EXAMS](docs/decisions/exams.md), [EXAM-DAY](docs/decisions/exam-day.md),
  [QUESTION-BANK](docs/decisions/question-bank.md)
- **Scans:** [INBOX](docs/decisions/inbox.md), [SCANNER-INTAKE](docs/decisions/scanner-intake.md),
  [OCR](docs/decisions/ocr.md), [BUBBLE-SHEETS](docs/decisions/bubble-sheets.md),
  [PICTURE-SEARCH](docs/decisions/picture-search.md) (find a page or picture by describing it: scans, printed pages,
  bank pictures, notes, students' files)
- **Grades:** [GRADING](docs/decisions/grading.md), [REGRADES](docs/decisions/regrades.md),
  [GRADEBOOK](docs/decisions/gradebook.md), [GRADESCOPE](docs/decisions/gradescope.md),
  [CANVAS](docs/decisions/canvas.md), [ICLICKER](docs/decisions/iclicker.md)
- **Messages:** [MAIL](docs/decisions/mail.md) (Gmail),
  [COMMUNICATIONS](docs/decisions/communications.md), [CALENDAR](docs/decisions/calendar.md) (Google)
- **Assistant:** [ASSISTANT](docs/decisions/assistant.md), [ASSISTANT-MEMORY](docs/decisions/assistant-memory.md) (kept words, pictures attached to them, rules learned from your corrections of a follow-up, and a rule said in a recorded meeting that you chose to keep),
  [EMBEDDING](docs/decisions/embedding.md) (the local model that finds kept memories, pictures and what was said in recordings),
  [PAST-WORK](docs/decisions/past-work.md) (how the Assistant finds what it did before in a like situation), [JEV](docs/decisions/jev.md)
- **Teaching follow-through:** [TEACHING-WORKFLOWS](docs/decisions/teaching-workflows.md),
  [COORDINATOR](docs/decisions/coordinator.md) (a coordinated course's coordinator, named on the course card and owned by
  the term; their email comes to Follow-up like a student's, as the coordinator's)
- **Scheduling:** [SCHEDULING](docs/decisions/scheduling.md) (one clock, engine and Term schedule; each section's class
  schedule: its class days, topics, what is given or due, and what a recorded class reached)
- **Recordings:** [RECORDINGS](docs/decisions/recordings.md) (the Plaud recorder, read through the Plaud intake's archive, which has every recording and plays the audio: only what belongs to
  your teaching is kept in Faculty, filed by the calendar; what does not is not kept at all, what Faculty is unsure of waits for your Keep or Don't keep, and Don't keep
  erases; a meeting filed under a term or course read by Faculty itself, with what it left you and what
  Faculty already found; a student's meeting on their timeline by reference, with what was promised proposed for their
  follow-up and a note, each on a card; a class filed on its section's class day and never to a student, what it reached
  set against the day's planned topics and the move of what it did not reach on a card; a memo you dictated read for what
  you asked, each request put to the Assistant with every change on a card; a training kept under its course; what was said
  found by describing it)
