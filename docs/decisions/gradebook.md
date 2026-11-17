## Gradebook

Rule ID: **GRADEBOOK**. Current binding module contract. Read root `AGENTS.md` first.

ASSISTANT selected-area selection temporarily preserves an active cell editor while focus is in selection/chat. Neither blur
nor a quiet redraw may save or discard it; returning restores its focus. Selected cells carry their existing term, section,
enrollment and item references plus displayed values; an editor's value is explicitly marked unsaved. Explicit normal
gradebook actions keep their rules.

SOURCE RECONCILIATION (2026-10-09, requirement's eight assistant improvements): the term-owned read projection
`GET /api/gradebook/terms/<term>/reconciliation` compares actual retained Canvas observations plus later confirmed receipts,
latest iClicker import values and Gradescope checked scores with Faculty values. It reuses final-grading eligibility and excludes
Inbox-deleted work. Dates and unavailable/uncertain evidence remain explicit. It changes no grades, source precedence or remote
state and makes no external request. Existing default API bodies are unchanged.
2026-10-09 review fixes: Canvas is compared by Sync's own column map and rule, a failed Canvas send never hides an earlier
observation, Gradescope only in linked sections with never-sent grades counted (not unknown), and `unknown_counts` added.
2026-10-09 second review: a column kept off Canvas is not compared with Canvas (Sync's map leaves it out in both directions), and
each Canvas difference says `faculty_changed_since_sync` when Faculty's value of a column Sync sends changed after Canvas was
observed: it only waits for the next Sync. Only the process that runs Faculty's Canvas writes settles a running send or post
(CANVAS "SETTLING"); this term read, served in the Inbox worker for the daily review, shows them as they are.

- ASSIGNMENT-WORKSPACE (requirement, 2026-10-03). An assignment's linked gradebook column also opens within its Return area.
  Resolve the latest setup's column reference and permanent term; old gradebook links keep their section/course redirects.
  The section editor, scope, request IDs, previews and the explicit Sync with Canvas remain unchanged. Unlinked/general gradebook
  views remain course views. A pre-column assignment/pull link may carry that same validated assignment and term context.

- The instructor asked for it on 2026-09-26 ("Build a gradebook feature in the faculty app and integrate it with the other
  workflows of the app"). `gradebook/` (service, `/api/gradebook`, `static/gradebook*.js`; follow `gradebook/README.md`) holds
  the instructor's own records of a term's scores: columns, categories, weights, drops, a letter scale and each student's
  values, typed in, imported from a file, or PULLED from Faculty's grading. Nothing in it publishes or emails a grade; the only
  send is the instructor's Sync with Canvas (canvas.md, SYNC WITH CANVAS; requirement, 2026-10-06: "I want just one button that
  syncs grades to canvas, no pull or push, just sync"): one term-wide action from any section tab, or from the course Home's
  Canvas row (Sync grades: every course ticked; requirement, 2026-10-06), whose summary writes nothing.
- IN THE COURSE PAGE (requirement, 2026-09-28: "I want the gradebook to split per section, I also think the entire gradebook page
  should go in the course page inside each section", then "the gradebooks go where the roster currently is on each section's page
  (the roster becomes the link you have to click)", and "don't have the combined gradebook on the course homepage"). A SECTION TAB IS
  ITS GRADEBOOK: `#course/<gid>/section/<n>` shows its gradebook where the roster was, then the section's work list under it (Ledger,
  2026-10-04: the grid starts on the first screen, DESIGN-LANGUAGE §1.1; Add column is the page's primary only while the gradebook
  has no column, a secondary after that, §1.4; the attention bar marks blanks past due orange and D and F red) (that section's
  students only; the View, its statistics and its export are the section's: GET `?section=`, and `section_id` on score writes, setup
  saves and linking); `.../section/<n>/setup` is the setup; `.../section/<n>/roster` is the roster, reached by the work list's Roster
  link (roster-context links from accommodations, DR bookings and roster imports open it directly). The SETUP STAYS THE TERM'S
  (columns, categories, weights, drops, scale; a column may still be limited to sections) and the section's header says so. A pull,
  a file import or an iClicker import started there covers ONLY that section (`GradebookService._scoped`; other sections' rows of a file
  are counted as `other_section`). The course HOME HAS NO COMBINED GRID: `#course/<gid>/home/gradebook` lists the sections to choose
  from (a course of one section opens it), keeping a link's column or pull; the one opened last (browser memory) is its primary, and
  its `/setup` opens the term's setup from that section (else the first) without asking. No sidebar entry and no term list; every earlier
  `#gradebook…` address (exam hub, calendar, grading, palette) is resolved (`gradebook.js legacyPage`); bare `#gradebook` opens
  Current courses.
- Owners: the setup revisions (`gradebook_setups`) and the request log (`gradebook_requests`) belong to the TERM
  (`course_terms.id`); each score revision (`gradebook_scores`) belongs to one permanent SECTION membership
  `(section_id, enrollment_id)` of that term, with triggers holding the section and the request to the score's term. A column
  REFERENCES a course-wide `assignments.id` and, through the term's `exam_events` row, its grading plan; it never copies
  them. Never attach anything to `courses.id`, and never let one term's gradebook change another's.
- THREE RUNS WITHOUT THE INSTRUCTOR'S PREVIEW AND CLICK, each through the instructor's own plan and apply with no choice made
  and waiting items left for the instructor:
  - THE MORNING SYNC (requirement, 2026-10-08; docs/decisions/canvas.md NIGHTLY SYNC): each morning from 05:00 campus time, a term
    a Sync has written to gets Sync with Canvas with its summary's defaults (it WRITES TO CANVAS: grades, created and published
    assignments and POSTING GRADES, only in the Canvas courses a Sync has written to; held grades and anything uncertain wait for
    the instructor); its request is an ordinary sync's (summary `automatic: "canvas-sync"`), labelled "Synced with Canvas by the
    morning sync". A term no Sync has written to gets the WebAssign import alone (below). Its attempts
    (`gradebook_canvas_nightly_runs`, migration 013) belong to the TERM.
  - THE WEBASSIGN IMPORT (requirement, 2026-10-04; docs/decisions/canvas.md WEBASSIGN NIGHTLY): Sync with Canvas's From-Canvas half
    for the assignments Canvas grades (WebAssign's and Canvas quizzes), each night at 10 pm until 2026-10-08, since then in the
    morning sync. Canvas ALWAYS wins there (requirement, 2026-10-06): the column mirrors Canvas, typed values included. The 10 pm
    import's attempts (`gradebook_nightly_runs`, migration 007) stay as history; its requests are ordinary `import` requests
    (summary `automatic: "webassign"`), labelled "Copied from Canvas by the nightly WebAssign import". It writes to no outside
    service.
  - ICLICKER AFTER CLASS (requirement, 2026-10-06; docs/decisions/iclicker.md AFTER CLASS): each section's Import from iClicker
    once its class ends on a Term schedule class day. Its attempts (`gradebook_iclicker_runs`, migration 008) belong to the
    SECTION; its requests are ordinary `import` requests (summary `automatic: "iclicker"`), labelled "Imported from iClicker
    after class".
- A COLUMN'S PLACE IN SYNC WITH CANVAS is the setup item field `canvas` (stored only when set, so earlier setups keep their
  canonical form): absent, Faculty's, synced to Canvas; "off", kept off Canvas (Column settings: Keep off Canvas); "webassign",
  WebAssign's through Canvas, and "quiz", a Canvas quiz's (`setup.OWNED`: Canvas grades them), which only Faculty's own revision
  (Sync with Canvas, the morning run) gives or takes away and a setup save from the browser refuses to change. Such a column is
  read-only: every score write refuses it (typed, bulk
  marks, paste, "Check now"), file imports skip it, it never takes a grading link, and Faculty's grading never fills it. One
  student's grade on a Canvas quiz is changed in Canvas itself from Faculty (CANVAS ONE CELL "A CANVAS QUIZ'S GRADE",
  2026-10-13), and comes into the cell with the next Sync.
- Every row is immutable and retained; the latest revision counts (highest setup `revision`, highest cell `seq`, which a
  trigger makes replace exactly the cell's `seq - 1`). A setup save names its `base_revision` and a score change the cell's
  revision it was made on (`base`; null also matches a cleared cell): anything newer is a 409, and a stale cell refuses the
  WHOLE batch with the stale cells listed. Every write carries a request ID (`UNIQUE(term_id, request_id)`): the same ID and
  content returns what it did, other content is a 409. An identical setup or an unchanged score batch records nothing; an
  import or a pull always records its request. A change without a note keeps the cell's note; clearing resets it; imports and
  pulls keep it.
- Hand-entered values are never silently replaced: `common.change_action` is the ONE rule for what an import or a pull does to
  a cell (`add`, `same`, `kept` for a value typed in Faculty, or a cell cleared there, unless the instructor ticks Replace for
  that student, `update`). A grade the instructor took out must not come back with the next pull. Import from iClicker and
  Sync with Canvas also keep a value another import or a pull wrote (iclicker.md and canvas.md KEEPS OTHERS, instructor
  2026-10-04).
- Pulls read ONLY final grades, each source by its own rule (`gradebook/sources.py`): FRQ by the export's `final_decisions`
  over the questions of the student's paper (every expected question final, the paper's version settled), bubble sheets by
  the plan's answer key (exactly one scored sheet), the Exams page by its finalized or released, not stale, totals. They read
  under the roster lifecycle lock, preview first (writing nothing anywhere) and apply only the preview's fingerprint (409 when
  anything changed), checking again in the write transaction that the setup and every replaced cell are unchanged. A pull
  never changes grading, the Exams page, the Inbox or a review gate, and never guesses a score for a student who is waiting.
  A certified 0 is not a guess: it is the instructor's explicit confirmation in Certify, recorded with the
  certification (below, "CERTIFIED VALUES"); a live pull never makes one, and a pull of a certified plan records only the
  certified 0 the instructor confirmed.
  An Exams-page excuse is proposed as `excused`; absent is `missing` only when the instructor ticks it.
- A linked column's default points are one paper of its plan (`sources.maximum`: the pinned FRQ questions of one version,
  the latest bubble key's total, the Exams page's pinned rubric; the largest version of each), else 100. Never read them from
  students' grades or change a column's points when the plan changes; the view only offers the new figure.
- Imports match the nine-digit UIN EXACTLY (`student_enrollments.uin`; Import from iClicker alone falls back to an account email
  equal to exactly one roster email, iclicker.md): never pad or guess; a UIN in two sections of the
  term or on two rows of the file is listed, not imported. Blanks change nothing unless the instructor ticks it; unreadable
  values (Gradescope's `Ungraded` included) are listed, never written; more than four decimals are rounded to four with a
  warning. UINs appear only in the instructor's CSV export: never in JSON, previews or the request log.
- DISPLAY (requirement, 2026-10-04: "show 2 decimals but store all 4"): the grid shows scores and column averages with at most
  two decimals (10.6667 reads 10.67, its title the stored value). Storage, the editor's starting text, a copy, the cell's
  description and history, imports, exports and every total keep the stored value (up to four decimals).
- The generic workspace JSON export excludes gradebook data; Home shows a Gradebook shortcut and no gradebook student data.
  Column due dates reach the Calendar as read-only source events (source `gradebook`, category `deadline`), edited only in the
  gradebook; a WebAssign column's date that Canvas set also follows Canvas each night (docs/decisions/canvas.md WEBASSIGN DUE
  DATES, requirement, 2026-10-05), never a date the instructor typed. A section's class schedule lists a column's due date for
  the sections the column concerns, read from the setup and never copied (SCHEDULING "CLASS SCHEDULE", 2026-10-10); a planned
  row of that schedule may follow a column (its `gradebook_item`), and the Add column window offers such rows as "From the
  schedule". Offering identity edits go through `gradebook.service.protect_references` (called by
  `rosters.protect_roster_references`), which refuses to detach a term's gradebook or a section's scores.
- Lock order is the usual one: roster lifecycle lock, verified roster payload read, then `BEGIN IMMEDIATE`. The migration
  namespace is `gradebook`; recorded SQL versions are immutable, so a schema change is a new file, and new tables go into the
  pre-ledger fixture of `tests/test_catalog_migrations.py`.
- The grid (UX overhaul, 2026-09-26) adds no write path: `#gradebook/<term>?assignment=<aid>&pull=1` (the Exams hub's
  Return stage, key `recorded`, while the assessment has no current certification; certifying records the grades, so a certified
  assessment's column opens without `pull=1` or its preview, 2026-10-02) opens that column's pull PREVIEW once and drops
  `pull=1`, and **Pull N scores** stays
  an explicit click; "Add all N as columns" is ONE ordinary setup save on the revision on screen and never pulls; bulk
  "Mark/Excuse N empty cells…" is ONE ordinary scores write for the rows shown, leaving DR-booked cells alone unless ticked;
  a write whose outcome is unknown is re-sent with the SAME request ID only by **Check now**, never automatically; a paste
  that replaces values or writes more than 50 cells asks first. The "grading changed since the last pull" dot is
  `links[item].grading_changed_at`, a read-only catalog timestamp (bubble sheets: present scans' latest complete reads; once
  the plan is certified, the latest certification's time).
- DIFFERS FROM GRADING (requirement, 2026-10-07, worried that a grade changed in grading after certifying never reaches the
  gradebook: "make it show in the gradebook very visibly when gradebook grades differ from what is in the grading pipeline").
  `GET …/grading-check` (`gradebook/grading_check.py`, README "Checking against grading") names every cell of a live column linked
  to grading, for a section grades were brought into (a pull or a certification), whose value is not grading's final grade now,
  by the pull's one rule (`sources.totals`, `sources.plan`, no Replace): `changed` (`add`/`update`) or `typed` (`kept`). A student
  grading has no final grade for never differs, and nothing is guessed. It READS ONLY: it never pulls, certifies or writes a
  cell, and the gradebook keeps the certified values until the instructor certifies again (CERTIFIED VALUES); each column says
  how to bring it in line (`certify` after a change since the latest certification, else `pull`; none for typed values alone).
  Grading is read live only where catalog rows say something could differ (a certified plan's `grade_marker` hint or Exams-page
  finalizations after it; a plan never certified changed after a section's last pull, or a typed or imported cell); otherwise
  a certified column is compared with its certification. The grid reads it after each View it draws, after a write to a
  linked column and when the window comes back after 30 s, and marks in place with the needs-you orange (DESIGN-LANGUAGE §1.2):
  the cell (tint and rule, its title saying what grading has), its heading ("N differ"), the attention bar's chip (a filter of
  those students), the key, and one warning notice listing each column with Show and Certify again or Pull. A mark lasts only
  while the cell holds the revision the check read.
- CHANGES (requirement, 2026-10-01: "fix the label and add the changes page"). `#course/<gid>/section/<n>/changes` lists every
  setup revision of the term and every request that touched the section, newest first, each with what made it and what it changed
  (README "Changes"; `gradebook/changes.py`, `GET …/changes`). It is a read-only projection of the immutable rows: it adds no
  record, write path or migration, and takes no roster lock. A cell's history names an import's source (its file, Canvas or
  iClicker); the grid, whose cells carry only their `source`, says "Imported". An excused absence's iClicker credit (ABSENCES
  ICLICKER-CREDIT) is a `scores` request with summary `source: "absence"`: origin `absence`, "Full iClicker credit for an excused
  absence" (or "… taken back"); its cells are source `instructor`, so the grid says "Typed" and the cell's note names the absence.
- ACTOR (requirement, 2026-10-01: "record the assistant as the author of its gradebook edits"). Every gradebook write records who
  made it: `assistant` for the in-app assistant's own requests (`request_actor.current()`: an environ marker its in-process transport
  sets, which no HTTP client can), else `workspace`. Every write route passes it, Absences' create and revise included (their
  iClicker credit, ABSENCES ICLICKER-CREDIT). Only the actor changes: an entered value keeps
  source `instructor` (hand-entered for `change_action`). The history, the grid's titles, Recent changes and the Changes page name
  "the assistant".
- GRADING SCHEME (requirement, 2026-10-06: "There should be a way for me to change the grading scheme for a given course (that
  applies to all sections in that course/semester). It should default to the standard grade definitions, but I should be able to
  edit it to be something like this", showing Canvas's scheme A 90, B 80, C 67, D 57, F 0). The grading scheme IS the term
  setup's letter scale (`scale`): owned by the TERM, so every section's letters follow it, and no new field, table or migration.
  A term starts with the standard A 90, B 80, C 70, D 60, F 0 (`setup.SCALE`). The course Home's Grades row shows it in one line
  (`GET /api/gradebook` term rows carry `scale`, additive) and its **Grading scheme** dialog edits it the way Canvas writes one
  (each letter from below the minimum of the letter above it, "< 90% to 80%"; the last letter down to 0%, the floor, saved as 0).
  Save is ONE ordinary setup save of the scale alone on the revision on screen (409 when the setup moved; the usual request-ID
  rules); the rest of the setup is kept. The setup page's panel is the same scheme under the same name. Canvas's own course
  grading scheme is neither read nor changed: Sync with Canvas sends scores, never letters.
- EVERY COLUMN HAS AN ASSIGNMENT (requirement, 2026-10-06: "a gradebook column has to correspond to an assignment in an assignment
  category", "unless it is a derived aggregate column"): canvas.md COLUMN ASSIGNMENTS. After each write that can add a column,
  `columns_changed` gives Faculty's new columns their assignment in the term's Canvas copy (the copy's own save; never a Canvas
  write; a failure is logged and never fails the gradebook write).
- A REPLACEMENT column (requirement, 2026-09-28: "add the replacement rule", for Canvas's "Replacement Exam Grade (Only Used if
  Higher)"): item `replaces_lowest` (stored only when true, one per category) never counts on its own; a scored value higher, as a
  percentage, than the student's lowest in its category takes that column's place before drops (`compute._replace`); missing,
  empty or excused does nothing. README "Computation".
- CERTIFIED VALUES (Certification and Export, 2026-09-26). Certifying a plan records its values in the term gradebook in the
  certification's own transaction, through `sources.plan` and `common.change_action` (request kind `pull`, source `grading`,
  summary `certification_id`, request ID `certify:<request_id>`); no gradebook migration. A certified plan's ordinary Pull reads
  its latest certification (`sources.certified`), and its plan fingerprint names that certification; plans never certified are
  pulled live exactly as before. A certified 0 is the instructor's confirmation in Grading, recorded with the certification; a
  pull never makes one. `links[item].certification` is `{id, created_at, may_have_changed}` (a catalog-only content hint,
  `grading.progress.grade_marker` kept in the certification's summary; approving a grade unchanged never raises it).
- ZERO ONCE DUE (requirement, 2026-10-06: "I want the gradebook to assign a zero to missing scores unless the student is owed a
  makeup for that assignment and except when the assignment isn't due yet (mostly for webassign, I don't want students getting
  zeroes on assignments they don't have to complete yet)"). Setup `ungraded: "due"`, the default of a term without a revision
  (`setup.default_setup`; a first revision saved before 2026-10-06 19:45 UTC, `DUE_DEFAULT_SINCE`, was made on `exclude`, which
  the Changes page compares it with). It is COMPUTED, never written: no cell or request. ZEROS GO TO CANVAS (instructor,
  2026-10-09, of Canvas's blank Class 2 and Class 3 polls: "Faculty should be pushing its zeroes for any assignment, otherwise I
  have to manually put them in and that defeats the point"; canvas.md SYNC WITH CANVAS): every Sync with Canvas, the
  instructor's or the morning one, sends each counted 0 as a 0 (receipts as for any grade), and once the cell stops counting
  takes it out again; WebAssign's and Canvas quizzes' columns stay Canvas's, and take a 0 only where Canvas shows no grade. `gradebook/blanks.py` reads it with every result (`_results`: the View, a
  score save's answer, the CSV's totals; the CSV's cells stay as stored):
  - DUE: a counted, live column that is neither extra credit nor a replacement. Linked to grading, once its plan is certified
    (asked, the instructor chose this for quizzes and exams: papers still being graded never read 0); otherwise the day after its
    due date on the campus clock (`scheduling.clock.campus_today`; due today is not past due). An undated column an iClicker
    import fills: the day after the latest campus date of its sessions (`gradebook_iclicker_sessions`; requirement, 2026-10-07,
    of a student with no Class 2 poll score: "I thought we changed it to automatically put zeroes in missing assignment
    cells"). POSTED (requirement, 2026-10-08, of "Quiz 1 (Take Home)", a Canvas column with no due date whose 18 empty cells
    never counted: "We can keep it the way it is, but also add the condition that any posted column automatically imputes 0s for
    missing scores (except for the iclicker excused absence deal)"): a column whose grades students see in Canvas is due whatever
    its date or certification (`canvas_posting.posted`: in Faculty's latest read of EVERY Canvas assignment of the column, by Sync,
    the morning run or a post/hide, the assignment posts by hand, holds grades and hides none). Since 2026-10-08 the posting rules
    post a column only once it is due anyway (after its due date, once certified, an iClicker column after class), so only a Post
    choice makes a column due earlier, as Post by hand did. My choice, asked about
    in the report: an AUTOMATIC posting policy does not count, since Canvas shows each grade as it comes in (a WebAssign column
    with early scores would otherwise read 0 for everyone before its due date). WAITS still apply, and an excused absence's iClicker
    credit is a written 10/10, never an empty cell (ABSENCES ICLICKER-CREDIT). Results add `due_posted` (the columns due only by
    posting, setup order, only when there is one) so the cell's words say "its grades are posted in Canvas". A column with none
    of these never is.
  - WAITS (not counted, its reason named): for the column's assignment, a makeup owed (ABSENCES MAKEUPS `owed`; asked, the
    instructor chose that an undecided absence's `if_excused` waits too, until decided), a DR-center booking, or a paper the Exams
    page received (`makeups.RECEIVED`) with no value recorded yet (a makeup handed in after certification ends its makeup row).
    Certification leaves such a student's cell empty rather than certifying a 0 (GRADING "MAKEUP OWED", 2026-10-07), so this
    wait is what holds it; an absence marked not excused makes it count 0 here at once, and certifying again records it.
    Only empty cells of due columns whose assignment has an exam record are looked at.
  - Every other empty cell of a due column counts 0 over the column's points. Results carry `blanks` `{row: {item: "zero" |
    "makeup" | "if_excused" | "dr" | "paper"}}` only under `due` (the other policies' results keep their shape). The grid shows a
    muted 0 (the missing rule beside it), MU for a makeup or an undecided absence, IN for a paper in, DR as before; its past-due
    chip counts exactly the server's zeros ("N empty cells counted as 0"); the student's record and the assistant's
    `student_grades` say the same.
- POSTING GRADES (requirement, 2026-10-06 and 2026-10-08; canvas.md POSTING GRADES): posting reaches Canvas only during a Sync with
  Canvas (the instructor's or the morning one). A column's menu records a standing Post / Hide (or back to the category's rule;
  `gradebook_canvas_posting_choices`, TERM-owned) that asks nothing of Canvas. A CATEGORY'S POSTING is the setup category field
  `posting` (`setup._posting`, stored only when automatic, so earlier setups keep their canonical form): absent, by hand;
  `{"due": bool, "certified": bool}`, automatically after a column's due date and/or once Faculty certifies it (both may apply:
  a category may mix imported and graded columns). Weight 0 in weighted grading, a column that does not count or is archived, and
  one without a category in weighted grading are always by hand; iClicker's columns post automatically; a column's choice wins
  (`posting_policy.desired`). Setup's categories table shows the rule ("Posting in Canvas": By hand / Automatically, after the due
  date, when certified; "By hand (doesn't count)" at weight 0); the Changes page describes it ("posted in Canvas by hand → after
  its due date"). The heading shows "N hidden" (a line of its own) from what Faculty last read and, dashed, what the next sync does
  ("posts at sync", "hides at sync", "by hand at sync"; `View.canvas_posting`: `policy`, `choice`, `pending`, additive), counting
  only students currently enrolled in the Canvas course (2026-10-07). The gradebook's own cells never change by it.
- PUBLISH FROM SYNC (requirement, 2026-10-07; canvas.md SYNC WITH CANVAS): a column whose Canvas assignment is not published is
  ticked under "Publish in Canvas, then send" in Sync's summary; Sync publishes it in each course before its grades (receipts,
  Faculty's Canvas copy published too). Unticked, it sends nothing. The gradebook's own cells never change by it.
