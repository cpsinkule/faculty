## Performance rules (2026-09-29)

Rule ID: **PERFORMANCE**. Current binding module contract. Read root `AGENTS.md` first.

- The course-group roster summary reads term and section owners once per summary, reusing only that read's maps.
  Its default JSON and section order are unchanged. Workspace request limits live in `request_limits.py`, with endpoint
  exceptions before blueprint defaults; extraction preserves every size, multipart-part bound and refusal body.

- The instructor asked (2026-09-29): "find inefficiencies in the repo and optimize it for performance, eliminate unnecessary reloads
  etc, but do not sacrifice functionality". Every change of that pass returned byte-identical API bodies (measured on isolated copies
  of live data) except the opt-in parameters and additive fields named below. Keep that bar for later performance work.
- ONE READ OF THE TRIAGE RECEIPTS (2026-11-04, same bodies, hash-checked on a snapshot): a listing's `JevTriage.apply`
  takes every conversation's receipt from `newest`, one read, instead of `lookup` once per conversation (jev_calls has no index for it,
  so each scanned the table: 2.1 s of a 3.7 s Follow-up read). `lookup` stays for the worker, whose receipts change as it goes.
- ONE READ OF A LISTING'S CONVERSATIONS (2026-11-05, same bodies, hash-checked on a snapshot): `apply` hands `snapshots` the conversations
  `_items` already read and the keys it will look up, so only those students' messages are read and only those conversations
  are prepared for Jev (the term's 548 conversations were prepared for 229 follow-ups). Every `/api/student-support` listing about 0.95 ->
  0.48 s. The Follow-up page's list read is opt-in lighter on top (`brief=1`, TEACHING-WORKFLOWS "RESOLVED WHEN OPENED"; `_all_cards` reads
  every card once, a memo of that read): 1.67 -> 0.76 s and 4.2 -> 0.8 MB on the snapshot, and one student's read 0.09 s.
- MEMOS LIVE FOR ONE READ. `workflow_summary(shared, quality)`, `_responses(quality)`, `location_status`/`location_context`/
  `require_location_review(memo)`, `setup_summary(checks, quality, memo)` and `validate_task(..., *, memo)` share one memo per plan
  per read (a request, a discovery pass, one `final_decisions` call), never across a write. `workflow_summary` keeps each reviewed
  scan's overview in `shared["scan_overviews"]`; the Assignments list and detail (2026-10-03) reuse it instead of reading every scan
  again, and the list does not read an archived row's lifecycle stages, which it discards (bodies byte-identical; list ~900 -> ~750
  ms on the live copy). The assignment overview's 30 s quiet refresh asks `GET /api/assignments/<a>/<t>?delivery=0` (opt-in;
  the default body is unchanged): no delivery receipts are read and the body has no `delivery`, the page keeping those it read on
  opening, on Refresh progress or on Try again (receipts change only through the instructor's certify, send and publish).
  `paper_version` reads the plan's seats,
  version decisions and comparisons (`frq_version_checks`, one per student and input) once from the second paper on a memo, and
  reads documents and registrations once only when the caller opts in with `shared["prefetch"]` (discovery, `paper_versions`, and
  `_responses` when it lists every student: the grading overview, an assignment read, the review queue, the batch preview,
  Certify); registrations tied on `created_at` use the single-row query. The "new" batch preview lists responses
  on COPIES of the participants, so `queue_version_checks` queues only what it looked at. `final_decisions` shares its question and
  participant dicts between decisions: treat them as read-only.
- ONE READ OF EACH PLAN FACT (2026-10-02, Grading index and assignment reads, same bodies): `_responses` keeps the questions it read
  in `shared["read_questions"]` (an assignment read's `plan["questions"]` count as read) and `workflow_summary` takes its questions
  from there; question dicts are read-only for both. `workflow_summary` takes each paper's printed document from `paper_documents`:
  a selected paper of the exam's own print jobs from the prefetch, a student without one from one read of the exam's current
  copies, anything else alone, each exactly as `manual._paper_version` answers for one student. The listing joins only the
  acceptances of its own plan's current results (the rows the whole `frq_all_acceptances` view gives) and asks the pure
  `quality_flags` and `selection_disagrees` once per distinct input of the listing.
- ONE READ OF EACH EXAM'S PAPERS (2026-10-02, Grading index, Exams list and hub, exam record, assignment reads, Certification; same
  bodies): `ExamService._collection` reads the scan observations and assembly revisions of every copy of the exam's print jobs in
  one statement each (`_exam_copy_reads`: each copy's rows exactly its own one-copy query's, in that order), the Inbox jobs of
  those observations' messages in one (every scan page's message exists, foreign keys being on, and a message has at most one
  job), and every student's newest submission choice in one (`_submission_choices`); one student's paper (`_participant_paper`,
  a grading step's check) keeps its own indexed reads. When the caller opted in with `shared["prefetch"]`, `JobsMixin.source`
  gives `manual._source` a memo: every assembly of the exam's print jobs with its pages in two reads (`_assemblies`), and the
  PRESENT state of every assembled page of the exam in one; an assembly or page not found there is read alone, and every check
  runs as before. The Gradescope card's "sent" read keeps each EXISTS on its send's own receipts (`+status`, so SQLite takes the
  `send_id` index instead of walking every accepted receipt), `_grade_coverage` parses a receipt's items only for a grade it
  compares (once per distinct items), and with the caller's `decision_rows` (the Grading index passes its `workflow_summary`
  details; `coverage_detail`) takes each final grade's decision from there instead of reading the reviews and acceptances again,
  falling back to those reads when the rows do not cover every final grade.
- PROCESS-WIDE CACHES ONLY OVER IMMUTABLE ROWS, each bounded and least-recently-used first: `review._FACTS` (what the listings read
  of a retained result, by catalog and result id, 20,000; `forget_result_facts()` for tests that rewrite a result),
  `progress._EXPORTED` (an export's decision ids, 32), `progress.score_content` (lru_cache by score_json text),
  `rubric_quality._COMBINATIONS` (`heaviest_combination`'s answer by the digest of the rubric, exclusions, value and search limit it
  searched, 2,048; 2026-10-27: 45 ms of every `final_decisions` on Exam 2, once per grade a Gradescope send checks) and
  `rooms._spaced_count` (by the exact usable-seat geometry); `gradebook.service._PREVIEWED` holds no catalog rows at all: the
  Canvas or iClicker read behind an import preview (4, 30 minutes), which Import plans against the catalog as it is then.
  `DocumentService._stack_memo` holds the last interleaved stack served (`section_stack`, GET `/api/documents/stack` and a quiz
  draft's stack): ONE entry, keyed by each job's id and verified output sha256 (immutable once a job is ready, `issued_batch_immutable`),
  so the file viewer's describe, its PDF and Download cut a 70 MB stack once instead of three times (up to about 75 MB resident; the
  instructor is asked to accept that bound). It is consulted only after the ready-and-current check, so a superseded job is still
  refused. Their triggers (`frq_result_immutable`, `frq_export_immutable`, `issued_batch_immutable`) are what
  make them safe; never key a cross-request cache by anything a write can change.
- THE ASSIGNMENTS PAGES (2026-11-01, requirement: "the assignment's page takes a long time to load"; measured on a snapshot of the
  live catalog: the list 2.3 s warm and 6.9 s after a restart, Exam 2's page 2.4 s and 5.3 s). `workflow_summary(export=False)`
  leaves out `export` for the callers that discard it or never show it: the Assignments list and an assignment's read
  (`_projection`), Home and Exams' `grading_summary`, and Certify's `_read`. It parses the latest export's whole snapshot (Exam 2's
  is 654 MB: 3.3 s whenever `_EXPORTED` is cold, as after every restart); no page read `workflow.export` of an assignment's read,
  which therefore no longer carries it (the requested change; the Grading page's own plan read keeps it). `same_finalization`
  keeps each confirmation's `guidance_key` per question and rubric revision (`rubric_quality._GUIDANCE`, 4,096, least recently
  used first; derived only from the immutable confirmation, ~200 ms of the list). The regrade match reads the student's uploads
  before their grades (a CROSS JOIN onto `frq_gradescope_grades_upload`; ties on `created_at`, none on live, go to the last grade
  recorded) and `_has` counts the tables in SQL (together 180 -> 40 ms of the list). The overview draws from `?delivery=0` and
  reads its delivery receipts after (`GET /api/teaching-review/<plan>/delivery`, the same `delivery()` the default detail
  embeds; ~1.5 s of Exam 2's page), and only on the overview: Create and Collect never read them. Coming back to the list draws
  its last read while the new one comes. List bodies are byte-identical; the list is ~2.0 s warm and 3.3 s after a restart.
- THE PRINT PAGE'S CATALOG IS UNBOUNDED IN PRINT JOBS, a recorded choice (2026-10-02): `DocumentService.catalog` lists every print
  job ever made, every term and archived ones included, plus the newest 100 registration jobs (DOCUMENTS), so a job never drops off
  the list a wizard's "Already printed" reads. It grows by about one job per section or version printed (12 print jobs, 44 KB on
  live in October of the first term) and is polled every 3 s only while a job or a quiz draft is being prepared. Bound it (current
  terms plus a recent window) only together with that wizard check, never by dropping old jobs silently.
- CONNECTION REUSE (`rosters.connect`): a connection goes back to a small pool (8 per file, 16 per process) only after a clean block
  with no open transaction, no live cursor, default settings, no state-leaving SQL (TEMP, ATTACH, a PRAGMA that sets something
  other than `foreign_keys`) and the same file (pid, st_dev, st_ino); anything else closes as before. Do not keep a cursor or a
  connection past its block. The `execute`, `executemany` and `executescript` shortcuts return the same tracked cursor as
  `cursor()`: later statements on that cursor must still check its thread and exclude state-changing SQL from the pool.
  Idle pooled connections keep a shared lock on the catalog and its `-shm`, and the WAL now stays
  while services run: a journal-mode change, removing the WAL or replacing the catalog needs every Faculty process STOPPED, and
  the inode check never makes an in-place replacement safe (a new connection recovers the `-wal` beside the path). A process
  ended by SIGTERM/SIGHUP with the default action closes its pool first, so a stopped catalog is complete without its WAL; after
  SIGKILL the WAL remains and is recovered on the next open. Statement counters must also hook `rosters._checkout` (the trace is
  cleared at each checkout). `app.database()`, `CourseStorage.connect` and backup connections are not pooled.
- FILE VIEWER (ASSISTANT VIEWER): callers rendering the same PDF batch share its lock until every current caller has finished,
  including callers waiting after a failed render. The registry retains no idle locks. A requested page is returned from the
  render result even when the batch or page exceeds the 96 MB cache budget; eviction bounds retention, never page availability.
- INDEXES AND ORDER PINS: grading 035 (`frq_responses(current_result_id)`, `frq_tasks(response_id)`, `frq_events(assignment_id,
  kind)`, `frq_exports(assignment_id, kind, created_at, id)`), document_identity 012 (`scan_unlabeled_registrations(printed_copy_id)`,
  `assembly_pages(scan_page_id)`, `printed_copies(enrollment_id)`, `submission_choices(enrollment_id, assignment_id, created_at)`)
  and response_cards 002 (`response_card_mail(card_id, created_at)`). A new index can reorder an unordered query: EXPLAIN every
  changed plan and pin a visible order (`ORDER BY c.id` in `scans.assembly`, `ORDER BY c.copy_number` in `unlabeled.apply`, the
  CROSS JOINs keeping the latest-task reads on their batch index). Do not add `print_batches(assignment_version_id)` without that
  audit (it reorders `copy_names` holders and `assignment_stages` batches). Do not run a blanket ANALYZE: it moves the cloud
  dispatcher's every-2-s recovery off its status index (2,000x slower).
- OPT-IN AND ADDITIVE API: `GET /api/inbox/jobs?files=input` (input files only, no events: the list, Next in Current and the
  palette use it); `GET /api/documents/messages/<id>?view=summary` (`page_count` instead of `pages` and `assemblies`; the Scan and
  Review stages read the full view); `GET .../review-queue?question_id=` (that question's entry exactly as the whole queue has it,
  totals counting it alone, the answer naming `question_id`; 404 for a question the plan does not pin; the review desk reads the
  next step only from the whole queue); `GET /api/mail/threads?tab=students&step=1` (one Gmail page, plus `scan` naming the scan's limits; the Mail page uses it); `ExamService.list` rows carry `main_date` (the seating chart's exam date); `GET /api/gradebook/terms/<t>/grading-check` (GRADEBOOK DIFFERS FROM GRADING: its own read, never in the View, whose body is unchanged; grading is read live only for a column whose catalog markers moved, ~0.14 s otherwise on the live copy);
  `GET /api/gradebook` term rows carry `scale` (the term's grading scheme, for the course Home's Grades row; the View is unchanged);
  `GET /api/canvas/content/<term>` carries `columns` (the gradebook columns Canvas holds beside the copy, and those without a
  category: canvas/column_assignments.py `overview`, Faculty's records only);
  `GET /api/gradebook/terms/<term>?section=<id>&cells=section` (that section's rows in `cells`, every column key kept, plus
  `cell_counts`, each column's term-wide count for the shared setup; a section tab reads it, about a third of a four-section
  term's body; the write routes still answer with every section's cells). Defaults are
  unchanged. `GET /api/inbox/jobs/<id>` and `GET /api/documents/messages/<id>` carry a strong ETag and answer 304, still no-store.
- REVIEW DETAILS (2026-10-02): `review-queue?details=review` adds only member `reason_kinds` for navigation. The default body is unchanged; hidden proposals expose only `blind`. The review workspace reads all questions on entry/boundaries and refreshes only the current question after a save. Since 2026-10-11 a change made in the Review step is also followed by ONE default-body plan read for the step graph (`grading.js` `planFollower`): one second after the last change, so a run of quick approvals costs one read and not one each, and never beside another such read; no body changes. That read took a median 456 ms on Exam 1 (2,701 responses, 23 questions; live, read only, 2026-10-11) beside 137 ms for the one-question queue read every decision already makes, and it holds the lifecycle lock for that time. Since 2026-10-03 the Review step opens in the workspace, so entering it reads the queue once with `details=review` (no default-body read). The opt-in `GET .../proposal-gallery` reads one group's members through `review_queue` and their crops only when the gallery is opened; `rubric-quality/clarify` and `proposal-gallery/approve` are new writes; every existing default body is unchanged (`confirm_rubric_quality` answers exactly as before). `GET .../responses/<id>` adds `flag_review` only for a response the latest flag review left with the instructor (one indexed read of that review's result when the response has a current result and no decision); every other response reads byte-identically.
- ENCODING: 200 JSON bodies of 8 KB to 8 MB go gzip-encoded (level 5) when the request accepts it, still no-store; HEAD, 304s,
  attachments, file and streamed responses are untouched, and view-set ETags stay. This build's `.js`/`.css`/`.svg` go
  gzip-encoded with their own ETag (sha256 + `-gz`) and `Vary: Accept-Encoding`; earlier builds come from the archive as stored.
- WORKER IDLE WORK: the training capture skips its search while the high-water marks of the six tables it reads
  (`training.HIGH_WATER`) are unchanged (read in the search's own snapshot; a 600 s fallback); a table added to `frq_all_acceptances`
  must be added there. `copy_names._waiting` starts from the `quiz:` print batches. The storage guard checks subpaths with an
  lstat walk (falling back to `resolve()` where it finds a link, so every refusal and message is as before) and caches the marker
  reading by its stat, ctime included.
- GRADEBOOK ZERO ONCE DUE (2026-10-06): under `ungraded: "due"` the gradebook's `results` carry `blanks` (the empty cells
  counted as 0 or waiting, by row), the requested change; the other policies' bodies are byte-identical. Each result (View, score
  save, CSV) adds one `latest_certification` read per linked column and, only for EMPTY cells of due columns whose assignment
  has an exam record (in practice a certified quiz's or exam's few blanks), the makeups owed of those students, one DR query and
  each remaining cell's paper (`_participant_paper`, catalog only). WebAssign's and Canvas's columns add nothing. POSTED
  (2026-10-08) adds one catalog query per result over the term's latest posting reads (`canvas_posting.posted`) and
  `results.due_posted` only when a column is due by posting alone.
- POSTING GRADES (2026-10-06; at every sync since 2026-10-08): the View's `canvas_posting` (only when a term has a read or a
  choice: one query over `gradebook_canvas_posting`, one over `gradebook_canvas_posting_choices`, the term's iClicker columns and
  one certification read per linked column, for each column's `policy` and `pending`), Sync's summary `posting_plan` (planned from
  its own read, no new Canvas request; it replaces `posting`) and the send status's `posts` / `posting` (only when there is one;
  the sync's own answer now carries them too) are additive; every other body is unchanged. After a sync's send, its posts and hides
  read the step assignments of their courses once (4 courses at once); a switch reads its course's assignments before and after
  its one GraphQL request. Sync reads each sent assignment's submissions back as before (`posted` is one more field of that read).
- THE MORNING SYNC (2026-10-08): one full read of each current term's linked courses a morning (it replaces the 10 pm import's
  read), planned and recorded as a Sync, at 05:00 campus time when nobody works; a morning with nothing to do writes nothing.
- PUBLISH FROM SYNC (2026-10-07): Sync's summary column `publish`, the sync's `publishing` and the send progress's `published`
  appear only when an assignment is unpublished or published; every other body is unchanged. A publish reads the course's
  assignments before and, only when Canvas's answer does not say, after the one PUT, and its groups once for the copy's link.
- FRONTEND: see FRONTEND.md "Quiet refresh contract" (hidden-tab polls, conditional reads, one read per navigation,
  `CLIENT_OWNED` attributes, compare with what was written). The Inbox list's signature holds the worker's 90 s `recent` flag,
  not the heartbeat time, so the stalled-worker notice appears at the 90 s mark. The step graph has no animation since
  2026-10-03 (DESIGN-LANGUAGE), so nothing watches graphs scrolled off screen (`motion.js` was removed).
- FOLLOW-UP TABS (2026-10-08, TEACHING-WORKFLOWS TAB NAVIGATION): `kind=appointment` reads only appointments through the
  same revision projection and order; its response is byte-identical to the old full-list filter. The browser always reads
  on entry, showing the selected tab and a loading state first. Repeat shell renders share the current visit's read until
  its next poll (4 s while follow-up preparation runs, otherwise 30 s); focus, visibility and writes still refresh.
  No record data is reused across visits; request generations discard obsolete results, including the calendar snippet.
