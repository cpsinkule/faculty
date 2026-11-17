## Student accommodations

Rule ID: **ACCOMMODATIONS**. Current binding module contract. Read root `AGENTS.md` first.

- The Accommodations navigation page owns the combined view and its import
  controls, not the underlying student records. Student accommodations and DR
  exam schedules remain owned by their permanent section/enrollment memberships.
  Since the 2026-09-26 overhaul the section tab links to this page instead of
  holding its own import. The DR schedule opens on Upcoming (campus date strings,
  no time-zone conversion), grouped by linked assignment and term; its address
  carries only `tab`, `when`, `course` and `exam` (never names or UINs), which is how
  the Calendar and the Exams hub deep-link to one exam's group. The freshness
  line (`AccommodationService.overview` `last_imports`) applies `PRESENT`, so an
  import deleted from the Inbox counts nowhere. A wrong-export message only names
  the other importer; nothing is imported by guessing the file's kind.
- AIM (the Disability Resources portal, behind TAMU's Microsoft sign-in and a Duo
  verified push) has no API; both exports come from its instructor portal. **Import
  from AIM** (the page's primary; `aim_import.py`, `static/aim-signin.js`) signs in
  with the NetID and password the instructor types into Faculty's own form (their
  request, 2026-09-26: "prompted for the credentials through faculty's own interface
  and it handles everything behind the scenes ... and auto click the continue
  button"). With the saved Gradescope sign-in (`FACULTY_GRADESCOPE_CREDENTIALS_FILE`,
  read with `gradescope_web.load_credentials`; instructor: "the credentials are
  actually the same as the gradescope credentials") it starts at once with no form
  and goes straight to Duo; the password never reaches the browser page, and GET
  `/aim/saved` names only the NetID. One private headless Chromium at a time; a driver reads each page's
  address, visible text and visible fields (`classify`, host names not required)
  and fills the NetID (`NetID@tamu.edu` in Microsoft's field) and password, relays
  Duo's verification code (after continuing TAMU's "Verification Required: You will be
  redirected to Duo" page; a field counts only when shown on screen, since sign-in
  pages keep earlier steps' fields in the page), sends the push again or uses a passcode ONLY on the
  instructor's answer, answers "Stay signed in?" and Duo's "Is this your device?"
  with no, clicks AIM's FERPA "Continue to view student accommodations" (the form
  states that signing in is the instructor's acknowledgment), and repeats AIM's two
  Export postbacks with the fields AIM's pages give it (posting only to AIM's host).
  A page it does not recognize, or cannot act on, for STEP_WAIT opens the live page
  (Chrome's screencast, clicked and typed into) and the driver takes over after it.
  The window then opens ONE review with both previews and one Import (instructor:
  DR bookings come in with the same import; `accommodations.js importAimExports`),
  each part the ordinary preview/import with its own hash, idempotency key and Inbox
  job; importing stays the instructor's click. The password lives in memory only
  until typed and is never stored, logged or repeated in an error (the log gets
  step names and hosts only); the NetID alone is remembered in the browser. The
  browser runs with a plain environment (no service credentials) and is discarded
  after the export, on Cancel, after IDLE (10 min unwatched) or LIFETIME (30 min).
  Never persist AIM/NetID credentials (Faculty only reads the instructor's saved
  school sign-in) or cookies, keep a browser profile, write to
  AIM (except SCHEDULING "SEND TO AIM", the instructor's own Send, 2026-10-27), or keep
  the exports anywhere but the page's memory. Session routes answer
  410 when a sign-in has ended (404/409 refresh the whole app).
- AIM FORM CAPTURE (2026-10-15): **Capture AIM's form** (a quiet button after Import
  from AIM; `AimCaptureBrowser`, `openAimCapture`) records the structure of AIM's
  prescheduling form for building and testing the DR-quiz prescheduling driver. It
  signs in exactly as Import does (one AIM session at a time: either ends the other)
  and never exports; once in AIM it shows the live page and stays signed in. **Record
  this form** is the only thing that records, and only from a page on AIM's portal
  host and path (POST `/aim/<id>/record`; never automatically on a page change).
  What is recorded: the URL's path only (no query or fragment), the h1–h3 headings,
  every input, select and textarea by tag, type, name, id, label, required and a
  select's option texts, and every button by tag, type, name, id and text; never a
  value, checked or selected state, the page's text, HTML, cookies or a picture.
  Student data is left out on the server (`form_structure`): an option holding a
  current roster's student name (every word, either order; dropped students included,
  `StudentNoteService.students()`) or nine digits in a row is removed, any other such
  text becomes "", and each is counted in `dropped`. Captures are kept as workspace
  Inbox `aim.form_capture` artifacts (one JSON file each) through the storage manager,
  listed newest first with a Download in the window (GET `/aim/captures`; a capture
  deleted in the Inbox is not listed). Faculty clicks, types and submits nothing in
  AIM: only the instructor acts in the live page. The rule above against writing to
  AIM stands, with its one exception (SCHEDULING "SEND TO AIM"). Agents and checks never sign in to AIM or open the live AIM
  window; tests use synthetic pages only (`tests/test_aim_capture.py`).
- DR QUIZZES (2026-10-15): the page's DR quizzes tab (`#accommodations?tab=quizzes`) is a read of SCHEDULING "CLASS SCHEDULE: DR
  QUIZZES" for each current term: the class schedule's upcoming quizzes to preschedule with the DR center, compared with the
  imported DR bookings. Its controls (SCHEDULING "DR QUIZ NOTICES", 2026-10-15): "Prescheduled in AIM" marks a ready quiz
  against the list's fingerprint after a confirm naming how many students it emails, Undo while no notice has gone out, each DR
  quiz taker's notice status, and an explicit Retry after a confirm. Send to AIM (SCHEDULING "SEND TO AIM", 2026-10-27; the
  instructor: "it needs to schedule the quiz on the same date as the quiz is scheduled for in Faculty and then upload the
  accessible version to the correct spot") is the one write to AIM: from the instructor's click on a term, after a confirm, it
  adds each ready quiz's date (the day, 20 minutes, and 10 minutes after the section's class ends), marks it, uploads its accessible copy to the students' requests,
  and exports and imports the DR bookings by itself (the one import that is not the instructor's click: their request,
  2026-10-27, "I need this to be fairly automated"), falling back to the ordinary import review when it cannot.
- `dr_exam_import.py` parses the DR booking export. `DrExamService` matches exact
  UIN + course + section + normalized exam name within selected current terms.
  The booking ID stays fixed when dates, times, duration, campus or request details
  change; immutable revisions retain prior schedules. Never use a booking's date
  or `Requested On` as its identity or as a source-update timestamp, with one exception:
- QUIZ BOOKINGS BY DATE (2026-11-07; the instructor's 2026-11-07 export stopped at "Row 21 conflicts with another
  booking" after Send to AIM had added the Nov 13 and Dec 1 quizzes and each DR quiz taker booked both). AIM's
  Exam Type "Quiz" names no particular quiz: every quiz date is booked as another "Quiz" row. A booking whose
  Exam Type is exactly "Quiz" (case and spacing aside) is keyed by that type and its date (`exam_key`
  "quiz YYYY-MM-DD", `dr_exam_import.booking_key`), so each quiz date is its own booking, and later exports
  update that date's times, length and campus. A quiz AIM moves to another day is a new booking; the old one stays
  saved, as an omitted booking does. Two "Quiz" rows on one day that differ still stop the import. The bookings
  made before this rule keep their key "quiz". Links to assignments still follow the Exam Type (`exam_links`).
  A conflicting duplicate's message names both rows, the student, the exam, the course and section, and the
  columns that differ.
- The instructor confirmed exports may be filtered. Every DR upload upserts its
  rows; omitted bookings remain saved. There is no inferred cancellation, deletion,
  accommodation grant or grading/exam execution. Changed exam names are new keys;
  conflicting duplicate bookings need corrected source data rather than guessing.
- `accommodations.exams.sync` uses the same preview/revalidation, atomic roster
  lifecycle locking, Inbox job idempotency, retained originals, receipts and
  verified backups as accommodation imports. `dr_exam_bookings`,
  `dr_exam_revisions` and `dr_exam_imports` are catalog records. Times are retained
  as DR-center local wall-clock values without browser timezone conversion.

- Mixed-course disability exports use `accommodation_import.py` and
  `AccommodationService`. Follow `ACCOMMODATIONS.md`. Match exact UIN, canonical
  course code and section within selected current terms; never guess names or
  propagate one course's accommodations into another course not listed in the file.
- Current accommodation records and immutable revisions belong to permanent
  section/enrollment roster memberships in the catalog. Do not rewrite roster
  payloads or private IDs. One workspace Inbox artifact retains the original mixed
  workbook; revision provenance references its job and source row. Catalog and
  verified Inbox backups retain both. Generic workspace exports exclude this data.
- `accommodations.sync` is an explicit workspace-scoped Inbox action. Private
  preview/import adapters enqueue only; the handler revalidates the exact preview
  and applies all matched rows atomically under the roster lifecycle lock before
  owner locks. Job IDs make effects idempotent. Missing students/columns retain
  prior data; explicit blank flags clear only those fields; older updates do not
  replace newer ones. Preserve immutable history and normal Inbox review gates.
- MAKEUP LENGTH (2026-10-05, ABSENCES "MAKEUP BOOKING" LENGTH): `absences/makeup_length.py` reads, and never writes, a student's
  current record in the section of their makeup for the largest granted "Extra Time N.NNx" column (the multiplier is the column's
  name; the value says whether it is granted, by the `active` rule), to size their quiz makeup at 20 minutes times it. Only the
  length leaves this read; no accommodation name or value reaches a card, the calendar or a model.
- DR QUIZ TAKERS (requirement, 2026-10-15: "It actually needs to be specific students that affirmatively say they want to use their
  accomodation for quizzes, not every student that registers for exams"): one flag owned by the TERM ENROLLMENT, append-only
  `dr_quiz_taker_changes` naming who and when. `GET`/`POST /api/accommodations/dr-quiz-takers/<enrollment_id>`: the POST sets `on`
  (never toggles) with a request ID (repeated, it answers again; for another value, 409). The Ctrl-K student card and the
  Accommodations page show two checkboxes of one value. The Assistant's write is an Approve card (in no `auto` category). The flag
  alone decides who is a DR quiz taker: Job 1's DR quizzes list (`scheduling/dr_quizzes.py` `_testers`) lists a section only when a
  current (not dropped) student of it has the flag on, and SCHEDULING "DR QUIZ NOTICES" emails only them; a DR booking or a granted
  Extra Time alone never does. Unticking a student after a notice went out sends nothing further.
  OFFERED (requirement, 2026-10-16: "dr quiz taker shouldn't be an option for students who don't have accomadations"): the
  checkbox is drawn only for an enrollment with a granted accommodation (the `active` rule) on a current record in any of its
  sections or a DR booking (`dr_quiz_takers.service.offered`; `offered` on the GET, `dr_quiz_taker_offered` on overview rows). A
  student already marked without one keeps the flag and the ticked box, hinted "No accommodations on record.", so it can be
  unticked; nothing clears a flag on its own, and the route still records what it is sent.
