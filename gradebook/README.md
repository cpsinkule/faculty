# Gradebook

`GET /api/gradebook/terms/<term>/reconciliation` is the read-only projection used by the assistant's daily review. It combines the
existing grading check with retained Canvas snapshots and checked send receipts, iClicker import values, and checked Gradescope
receipts. Each source keeps its observation time; missing or uncertain evidence stays unknown. This route performs no remote reads
or writes and preserves manual values and posting choices (ASSISTANT, GRADEBOOK).

Assignment-scoped column and pull links also retain the Assignments shell (ASSIGNMENT-WORKSPACE), including after legacy
gradebook links resolve to their course/section editor. This uses the current column's assignment reference and permanent
term; it adds no gradebook state, combined grid, write path, or automatic send.

The gradebook is where a term's scores come together: one column per graded item (an exam, a quiz, homework, participation),
one row per student of the term's sections, category weights, drops and a letter scale. Scores are the instructor's records.
They are typed in, imported from a Gradescope or spreadsheet export, or PULLED from Faculty's own grading (assisted FRQ grading,
bubble sheets, the Exams page's manual grading). Nothing here publishes, emails or sends a grade anywhere. The instructor asked
for it on 2026-09-26 ("Build a gradebook feature in the faculty app and integrate it with the other workflows of the app").

## Where it is (2026-09-28)

A section tab of the course page IS that section's gradebook, where the roster was (`#course/<gid>/section/<n>`; the roster is
`.../roster`, a link from the section's work list; the setup `.../setup`). It shows that section's students, and the View it reads is
the section's (`GET /api/gradebook/terms/<term>?section=<sid>&cells=section`: students, statistics over them and their cells, with
each column's term-wide `cell_counts`); score writes, setup saves and
linking send `section_id` so the View they answer is the section's too. The setup is still ONE per term. Pulls, file imports and
iClicker imports started there take `section_id` and preview and write only that section's students. `.../changes` is the section's
Changes page ("Changes" below). The course Home has no combined grid: `#course/<gid>/home/gradebook` lists the sections, the one
opened last (browser memory, per course) as the page's primary marked "Last opened"; its `/setup` opens the term's setup from that
section (else the first) without asking, since the setup is the same from every section. Earlier `#gradebook/<term>...` addresses
are resolved to these. Above the grid, the section's work list keeps Records (Roster, Assignments & printing, Course settings), TAs,
Gradescope and Canvas in view and folds its exams, seating charts, grading and Response Cards into one closed "Assessments" line
with their counts (remembered per viewer), so the grid starts higher on the page; the roster tab and the course Home keep that
list open.

## Ownership (AGENTS.md, Course data hierarchy)

| Record | Owner | Why |
| --- | --- | --- |
| Setup: categories, columns, letter scale, policies (`gradebook_setups`) | the TERM (`course_terms.id`) | a term's sections share one syllabus and its assessments; another term of the same course keeps its own |
| Scores (`gradebook_scores`) | the SECTION membership `(section_id, enrollment_id)`, within the term's setup | a student's record belongs to their permanent section membership, as accommodations and FRQ responses do |
| Score writes, imports and pulls (`gradebook_requests`) | the TERM | provenance of the score revisions they wrote |

A column may REFERENCE a course-wide `assignments.id` (the Assignments & printing identity) and, through the term's
`exam_events` row for that assignment, its grading plan. It never copies the assignment. Nothing is attached to `courses.id`.
Another term's gradebook is never edited by a change in this one.

## Records (migration `001_gradebook.sql`, namespace `gradebook`)

- `gradebook_setups(id, term_id, revision, setup_json, sha256, request_id, request_sha256, actor, created_at)`: immutable
  revisions, the latest counts. A save names the revision it was made on (`base_revision`, null for the first); another save
  in between is a 409. Saving a setup identical to the latest records nothing. `UNIQUE(term_id, revision)`,
  `UNIQUE(term_id, request_id)`; a trigger makes each revision follow the term's latest one (never skipped or reused).
- `gradebook_scores(id, term_id, item_id, section_id, enrollment_id, seq, status, points, note, source, request_ref,
  replaces_id, actor, created_at)`: immutable, append-only revisions of ONE cell; the highest `seq` counts. `status` is
  `scored` (with `points`), `excused` (not counted), `missing` (counts as zero) or `cleared` (back to no score). `source` is
  `instructor` (typed in Faculty), `import` (a file, Canvas or iClicker: its request's summary says which) or `grading` (a pull). `request_ref` names the `gradebook_requests` row
  that wrote it. The membership is a foreign key to `roster_memberships`. Triggers check that the section and the request
  belong to the score's term, and that a revision with `seq > 1` replaces exactly the same cell's revision `seq - 1`.
- `gradebook_requests(id, term_id, request_id, request_sha256, kind, summary_json, actor, created_at)`: one row per score
  write that changed something, and one per import or pull ALWAYS, even when it wrote nothing (its counts are the record of
  what it found). `kind` is `scores`, `import` or `pull`. `UNIQUE(term_id, request_id)` makes every write idempotent: a
  repeated request ID with the same content returns what it did; with other content it is a 409. An import that creates
  columns also records ONE setup revision under the same request ID, in the same transaction.

`actor` says who made a row (requirement, 2026-10-01: "record the assistant as the author of its gradebook edits"): `assistant` when
the in-app assistant's own request made it (its in-process requests carry a WSGI environ marker no HTTP client can set,
`request_actor.py`; every write route passes `request_actor.current()`),
else `workspace`. A value the assistant enters keeps source `instructor` (hand-entered: imports and pulls keep it, `change_action`).
The Canvas send records (`gradebook_canvas_sends`) and the remembered Canvas and iClicker matches name their actor the same way.

All three are catalog tables in the nightly verified backup. The generic workspace JSON export carries none of them.
Rows are never updated or deleted (triggers). A schema change is a new migration file: recorded versions are immutable, and
new tables are added to the pre-ledger fixture of `tests/test_catalog_migrations.py`.

## Setup (`setup_json`, validated by `gradebook/setup.py`)

```json
{
  "rule": "gradebook-v1",
  "mode": "points",
  "categories": [{"id": "cat_…", "name": "Exams", "weight": "50", "drop_lowest": 0, "aggregate": "points"}],
  "items": [{"id": "item_…", "title": "Test 1", "category_id": "cat_…", "points": "100", "due_date": "2026-10-01",
             "counts": true, "extra_credit": false, "archived": false, "sections": [],
             "assignment_id": "…", "link": {"kind": "grading", "parts": ["frq", "bubble"]}, "notes": ""}],
  "scale": [{"letter": "A", "min": "90"}, {"letter": "B", "min": "80"}, {"letter": "C", "min": "70"},
            {"letter": "D", "min": "60"}, {"letter": "F", "min": "0"}],
  "ungraded": "due",
  "letter_rounding": null
}
```

- `mode`: `points` (every counted point over every counted possible point; categories only group and drop) or `weighted`
  (each category's percentage times its weight, over the weights of categories that have a result). Default `points`.
- Categories: `name` 1–60 characters, unique (case-insensitive); `weight` 0–1000 (weights need not add to 100: they are
  renormalized over categories with a result); `drop_lowest` 0–50; `aggregate` `points` (sum over sum) or `equal` (the mean of
  item percentages); `posting` (POSTING GRADES, stored only when automatic) `{"due": bool, "certified": bool}`: its columns are
  posted in Canvas after their due date and/or once Faculty certifies them (absent: by hand). At most 50 categories.
- Items (columns): `title` 1–120 characters; `category_id` one of the categories or null (Uncategorized; not counted in
  weighted mode); `points` 0–100000 in ten-thousandths (0 only with `extra_credit`); `due_date` `YYYY-MM-DD` or ""; `counts`
  (false: shown, never counted); `extra_credit` (earned points count, possible points do not); `archived` (hidden and not
  counted, kept with its scores); `sections` empty for every section, else the section IDs of this term the column applies
  to; `assignment_id` a course-wide assignment of this term's course or null; `link` null or
  `{"kind": "grading", "parts": [...]}` with parts from `frq`, `bubble`, `exams` (needs `assignment_id`; stored in that
  order); `notes` ≤ 1000 characters; `replaces_lowest` (stored only when true; the column must have a category and not be
  extra credit; at most one per category that is not archived). At most 500 items. Order in the list is the column order. Two columns that are not
  archived may not both pull from one assignment.
- IDs: categories and items sent without `id` get one from the server (`cat_…`, `item_…`, `common.new_id`). An `id` sent must
  already exist in the base revision: IDs are never invented by the browser or reused. An item or category missing from a
  sent list is REMOVED; an item that has any score revision cannot be removed (archive it) and a category still used by an item
  cannot be removed.
- A category created in the same save can be chosen for a column before it has an ID: the new category carries a `ref`
  (browser text, 1–64 characters, never stored) and the column names that ref as its `category_id`.
- A field left out of an existing entry keeps the base revision's value, and a top-level field left out keeps the base's (a
  save of `{"items": [...]}` alone keeps the categories, scale and policies); a list that is sent replaces the whole list. A new
  entry takes the defaults, except a category's name and a column's title and points, which it must give. Every value sent is
  checked and an instructor-readable error says what is wrong; nothing is clamped. Keys the setup does not know are ignored
  (the browser's view-only fields). Points, weights and minimums are plain decimal digits (`common.parse_points`): no
  exponents, digit separators or other scripts' digits, at most four decimals.
- `scale`: 1–30 entries, letters 1–4 characters unique (case-insensitive), `min` 0–1000 strictly decreasing. The last entry
  is the floor: a percentage below every minimum still gets the last letter. The instructor calls it the term's **grading
  scheme** ("Grading scheme" on the setup page, the Changes page and the course Home); it starts as the standard A 90, B 80,
  C 70, D 60, F 0.
- `ungraded`: `due` (an empty cell counts as zero once its column is due, unless the student waits on it: "Empty cells counted as
  zero once due" below), `exclude` (an empty cell does not count) or `zero` (an empty counted cell counts as zero, due or not).
  `due` is a new term's default (requirement, 2026-10-06; GRADEBOOK ZERO ONCE DUE).
- `letter_rounding`: null (the exact percentage decides the letter) or 0–2 (the percentage is rounded half up to that many
  decimals first, e.g. 0 makes 89.5 an A with a 90 cutoff). A student's overall percentage is shown the way it decides their
  letter: cut to two decimals under null (89.995 shows 89.99 beside its B, never 90.00), rounded half up otherwise. Category
  percentages and class means are always rounded half up.

`setup.default_setup()` is the setup of a term without a revision (no categories or items, the scale above, `points`, `due`).
`default_setup(made_at)` is the one in force when a revision was saved: `exclude` empty cells before `DUE_DEFAULT_SINCE`, so the
Changes page reads an earlier first revision as it was made.

## Computation (`gradebook/compute.py`, pure, exact arithmetic)

Nothing computed is stored: results are computed on every view and after every write. Points are whole ten-thousandths, so
sums are integer sums; a percentage is rounded half up once, for display. Never floats.

For each student and each item that is not archived, counts, and applies to the student's section:
excused → skipped; scored → earned points over the item's points; missing → 0 over the item's points; empty (no revision or
cleared) → skipped, or 0 over the points when `ungraded` is `zero`, or under `due` when the column is due and the student does
not wait on it (below). Extra-credit items add their earned points and never their possible points; a missing or empty
extra-credit item adds nothing.

### Empty cells counted as zero once due (`ungraded: "due"`, `gradebook/blanks.py`)

Requirement, 2026-10-06: "I want the gradebook to assign a zero to missing scores unless the student is owed a makeup for that
assignment and except when the assignment isn't due yet". Computed with every result, never written as a cell (no request or
score); Sync with Canvas sends each counted 0 to Canvas (ZEROS GO TO CANVAS, "To Canvas" below):
- A column is DUE when it counts, is not archived, not extra credit and not a replacement, and: linked to grading, its plan is
  certified (`sources.latest_certification`; papers still being graded never read 0); otherwise its due date is before the
  campus date (`scheduling.clock.campus_today`; due today is not past due). An undated column an iClicker import fills is due
  once the latest of its sessions' campus dates (`gradebook_iclicker_sessions`) is before the campus date (instructor,
  2026-10-07). A column whose grades students see in Canvas is due whatever its date or certification (POSTED, instructor
  2026-10-08: "any posted column automatically imputes 0s for missing scores"; `canvas_posting.posted`: Faculty's latest read of
  each of its Canvas assignments shows them posted by hand, none hidden; automatic posting does not count). No due date, no session
  date, no certified link and not posted: never due.
- An empty cell of a due column WAITS, with its reason, when the column's assignment has an exam record and the student owes a
  makeup of it (`absences.makeups.owed`: `makeup` for an excused absence, `if_excused` while one waits for a decision), has a
  DR-center booking for it (`dr`), or has a paper the Exams page received with no value recorded yet (`paper`).
- Every other empty cell of a due column counts 0 over the column's points. `results.blanks` (only under `due`) is
  `{row: {item: "zero" | reason}}`, and `results.due_posted` (only when there is one) the columns due only by posting; item
  statistics still count those cells as empty, never as scores.

A REPLACEMENT column (`replaces_lowest`; requirement, 2026-09-28, for MATH 140's "Replacement Exam Grade (Only Used if
Higher)" from Canvas) never counts on its own. When the student has a SCORED value in it whose percentage is higher than their
lowest regular percentage in the category (ties: the later column), that column counts the replacement's percentage of its own
points (exact, a fraction of a point kept); otherwise it does nothing. Missing, empty or excused, it does nothing whatever
`ungraded` says: taking it is optional. It applies before drops. The category's result then carries
`replaced: [{"item", "by"}]` (only when used), and the grid strikes the replaced score like a dropped one.

A category's graded entries first drop `drop_lowest` non-extra entries with possible points (never the last one): for
`points` aggregation the set whose removal gives the highest remaining sum ratio (exact, Dinkelbach), for `equal` the lowest
percentages; ties go to the later column. Then `points`: (Σ earned incl. extra) / (Σ possible); `equal`: the mean of the kept
items' percentages plus each extra-credit item's percentage of its own points divided by the number of kept items. No
possible points → no result for that category (a category of extra credit only still adds its points to a points-mode total).

Overall: `points` mode, Σ earned / Σ possible over every category after drops (Uncategorized included); `weighted`,
Σ weight × category percentage / Σ weight over categories with a result and weight > 0. No result → no percentage and no
letter. The letter is the first scale entry (highest min first) whose min the (optionally rounded) percentage reaches, else
the last. A student's `earned`/`possible` are the points-mode sums after drops in both modes.

Results: percentages as text with two decimals, points with `common.points_text`. Item statistics count scored, excused,
missing and empty cells and give mean, median, low and high points and the mean percentage of scored cells.

In `weighted` mode a column that counts, is not archived and has no category (or one the setup no longer has) counts toward
nothing: Uncategorized has no weight. `results.uncounted` names those columns in the setup's order, and is present only when
there is one, so other results keep their shape. The list is term-wide; a section's grid names only the columns that apply to
that section. The grid says so once ("“Quiz 3” has no category, so it does not count toward Overall.", with **Choose its
category** opening that column's settings; several open Setup) and on the column's heading ("not counted (no category)").
Nothing is recategorized by itself.

## Typed scores (`POST …/scores`)

A save is a batch of changes, each naming the cell's current revision as the browser saw it (`base`, null for an empty cell;
null also matches a cleared cell, which the browser shows as empty). Any stale base refuses the WHOLE batch with a 409 listing
the stale cells; nothing is saved. A cell appears at most once per batch; an archived column, or one that does not apply to the
student's section, refuses changes. A change identical to the cell (status, exact points and note) is skipped, and a batch of
nothing but such changes records nothing (no request row; a replay is simply evaluated again). A change without `note` keeps
the cell's note; a change that clears the cell clears its note too.

## Pulling from grading (`gradebook/sources.py`)

A column linked to an assignment (`link.kind == "grading"`) can PULL its students' totals from this term's grading of that
assignment. A pull is always explicit: preview first (writes nothing), then apply with the preview's fingerprint (409 when
anything changed). Parts, and when each is available:

- `frq`: assisted FRQ grading (`grading/`), available when the plan pins questions. Only FINAL grades by the export's own
  rule (`final_decisions`), summed per student over the questions of their paper (its printed document version and exam
  version); a student counts only when EVERY expected question is final, and a paper whose version is unsettled never counts.
- `bubble`: bubble sheets scored with the plan's answer key (`bubble_keys`), available once a key is saved: exactly one scored
  sheet for the student. Sheets of scans still in Inbox review are only counted, as on the Bubble sheets page.
- `exams`: the Exams page's manual grading, available when that page has a rubric pinned for the exam or a finalization: a
  finalized or released total that is not stale.

A student's value is recorded only when every chosen part that is available is complete; otherwise the preview says why they
wait (k of n questions final, sheet needs review, no paper, a DR-center booking for this exam, marked absent in Exams). A
participant the Exams page marked `excused` is proposed `excused` when they have no complete score. An excuse given after
grading changes the participant's version, which grading's own rule treats as a changed paper: their FRQ grades stop being
final, so the preview proposes the excuse instead of the old total. Marked `absent` is proposed `missing` only when the
instructor ticks that option. Earned points are recorded as they are; when a student's possible points differ from the
column's points the preview says so. Nothing in grading is changed by a pull; a pull writes source `grading` and keeps the
cell's note.

Final grades are read by the same rule the grading export and the Gradescope send use, under the roster lifecycle lock. The
apply reads grading again and writes before releasing that lock (grading writes take it too), after checking inside its
transaction that the setup revision and every cell it replaces are still what its read saw. On a real 2,600-response exam
that read takes about 20 s, for the preview and again for the apply. The pull fingerprint therefore does not depend on the
Replace ticks: the instructor ticks them after seeing the preview and the apply writes those rows as updates. "Absent as
missing" changes the proposals, so it is part of the fingerprint.

### Certified plans (Certification and Export, 2026-09-26)

Grading's sixth step, **Certification and Export** (GRADING.md), certifies a plan's grades and records them here in the
certification's own transaction (instructor: "certify grades ... would finalize the pipeline and import the scores into the
gradebook"). No second write path and no gradebook migration:

- `sources.certified_totals(records, parts, ...)` turns the certified rows (`frq_certification_students`) into `totals()`'s shape,
  pure. The certify preview runs it on the prospective snapshot, the certify apply on the rows it has just inserted, and every
  later pull on the stored rows, so the preview's and the apply's plans agree. A column takes only the certified values of ITS
  linked parts (a column linked to FRQ alone records the FRQ part; a part confirmed as 0 counts only where it is linked). A
  scored or zero row is `complete` (a confirmed-0 part detailed "confirmed 0: no bubble sheet"), excused is `excused`, a
  student left out (a DR exam still ahead, or the empty one of two memberships) has no proposal, and a student not in the
  certification is "not in the certification". A certified 0 is the instructor's explicit confirmation made in Grading; a
  pull never makes one.
- `GradebookService.certification_options` lists every column the certification may record in (the linked column, with its
  parts changed or kept; an unlinked column of the assignment, then linked; else a new column, titled as the assignment, one
  paper's points, the exam's sections when fewer than the term's, or an existing column that references nothing; the one
  such column titled like the assignment comes first and is the default when there is exactly one, requirement, 2026-10-02) with
  each one's plan (`sources.plan`, `common.change_action`), and the setup's `mode`, its `categories` ({id, name, weight, counted},
  Uncategorized last) and a `proposed_category_id` for a new column, which the Certify dialog preselects (a proposal only: the
  request's `category_id` is what the column gets, and it is in no fingerprint). `prepare_certification` and `record_certification` then write inside the
  certification's `BEGIN IMMEDIATE`: the setup revision when the column needs one (request ID `certify:<request_id>:column`),
  ONE request of kind `pull`, source `grading`, request ID `certify:<request_id>`, summary with `certification_id` and
  `source: "certification"`, and the `add`/`update` cells. Hand-typed and instructor-cleared cells stay unless the instructor
  ticked Replace in the Certify dialog; values an import wrote are replaced (the dialog lists them).
- A student the latest certification leaves out whose current cell an EARLIER certification wrote (that request's summary names a
  certification) has that cell CLEARED (`status: cleared`, source `grading`): the certified value no longer stands, and the next
  value simply fills the cell.
- Once a plan has a certification, the column's ordinary **Pull** reads its LATEST certification (`sources.certified`), catalog
  rows only, instead of live grading (the orchestrator's default: the gradebook keeps the certified values until the instructor
  certifies again). Its `PullPlan` adds `certification_id` and `certified_at`, and its fingerprint names the certification, so a
  newer certification makes an older preview stale; its request summary names the certification too. A plan never certified is
  pulled live exactly as before.
- `links[item].grading_changed_at` is the latest certification's time once the plan is certified (the Pull dot appears when a newer
  certification exists), and `links[item].certification` is `{id, created_at, may_have_changed}`: `may_have_changed` is a cheap
  catalog-only hint (`grading.progress.grade_marker`, stored in the certification's summary: each response's stored grade
  content, the rubric pins, the participants, the DR bookings and the bubble marker differ from what was certified; approving a
  grade unchanged moves nothing), never the exact state, which only Grading computes.

### Checking against grading (`gradebook/grading_check.py`; requirement, 2026-10-07)

The instructor: "make it show in the gradebook very visibly when gradebook grades differ from what is in the grading pipeline".
`GET …/grading-check` reads, for every live column linked to grading that grades were brought into (a pull, or a certification's
write, recorded for the student's section), each cell that differs from grading's final grade NOW, by the pull's own rule:
`sources.totals` read live, then `sources.plan` against the cells with no Replace ticked. `add`/`update` is `changed` (the cell
holds what a pull, a certification or an import recorded, or is empty); `kept` is `typed` (a value typed, or a cell cleared, in
the gradebook). A student grading has no final grade for (waiting, none) never differs. Each column's `fix`: `certify` when
grading changed after the plan's latest certification (its pull reads the certification, so only certifying again records the
change), `pull` otherwise, null when only typed values differ.

Reading grading live takes seconds on a big exam, so the check reads it only where something could differ, from catalog rows:
a certified plan whose `grade_marker` hint moved (`may_have_changed`), or whose Exams-page part was finalized or released after
the certification; a plan never certified whose grading changed after a section's last pull, or whose column holds a typed or
imported value. A certified plan otherwise is compared with its certification (as a pull of it reads it, milliseconds). On a
copy of live data (2026-10-07) the check took ~0.14 s with every column as certified, ~2 s once an exam's grade changed. It
writes nothing, takes the roster lifecycle lock like a pull preview, and answers only the columns that differ.

## Linking an assignment (`POST …/items/link`)

Adds ONE column for an assignment of the term's course: titled as the assignment, linked to every part available (FRQ when
none is yet), in the chosen category. An existing column of that assignment that is not archived is returned instead (no new
revision); a replayed request ID returns the column it made. The new column's points are what one paper of the plan is worth
(`sources.maximum`): the pinned FRQ questions of one printed version (questions without an exam version count for every
version), plus the latest bubble key's total for one version (each answered question's own points, else the key's default),
plus the Exams page's pinned rubric total; for each part the largest version, and a part whose maximum cannot be read adds
nothing. When nothing is known the column starts at 100 points. Points are never read from a student's grades, and a later
change to the plan never changes a column's points: the view offers the current figure as `maximum`.

## Imports (`gradebook/imports.py`)

CSV or XLSX (≤ 10 MB, the roster reader's limits; formulas refused). Recognized: Gradescope's course "Download Grades" export
(SID + one `<name> - Max Points` column per assignment), a Gradescope assignment scores export (SID, Total Score, Max Points,
Status), and a generic sheet with a UIN/SID/Student ID (or Canvas's SIS User ID) column and one column per item (a Canvas-style
"Points Possible" row gives maxima). The file itself is not retained: the request keeps its name, SHA-256 and format.

- Students match by the nine-digit UIN EXACTLY (`student_enrollments.uin`); nothing is padded or guessed. Rows without an ID,
  with an ID that is not nine digits or matches no student, whose UIN is in two sections of the term, or whose ID appears on
  two rows of the file are listed as unmatched (file line, name and reason) and imported nowhere.
- Values: a plain number (no sign, exponent or thousands separator, at most 100000) is a score; more than four decimals are
  rounded half up to four with a warning counting them. `EX`/`excused` is excused, `MI`/`missing` missing, a blank changes
  nothing (or is recorded missing when the instructor ticks that), anything else is listed as unreadable, with any run of eight
  or more digits masked. A Gradescope total whose Status is `Ungraded` is unreadable, never a score or a missing.
- Generic sheets: identity columns (Section, Email, Canvas's own ID, names …) are never score columns; a column holding other
  text is left out with a warning; a Points Possible cell that is text (Canvas's "(read only)") leaves its column out; a row with
  neither an ID nor a name is not a student.
- Each file column maps to an existing column (suggested by title, case-insensitive), a NEW column (created in the same apply,
  as one setup revision) or is skipped. An explicit mapping must name exactly the file's columns; two file columns never go to
  one column, and a new title must not exist (archived included). A new column needs its points, and the new columns must fit
  in the setup's 500 (archived included): until then the plan is not `ready` and cannot be applied.
- Preview, then apply with the file, the same options and the fingerprint. Unlike a pull's, the import fingerprint covers the
  options, Replace ticks included, so a changed tick needs a new preview.

## Syncing with Canvas (`gradebook/canvas_import.py`, `gradebook/canvas_export.py`; requirement, 2026-10-06)

"I want to essentially control everything from faculty ... I want just one button that syncs grades to canvas, no pull or push,
just sync" (docs/decisions/canvas.md SYNC WITH CANVAS). **Sync with Canvas** (each section tab's header, offered once a section of
the term links a Canvas course; **Start from Canvas** until the term has linked an assignment; and the course Home's Canvas row,
**Sync grades**, which opens it with every course ticked: `gradebook.js openTermSync`, no section scope, its View and the Sync
answer one section's) is ONE term-wide action that
replaced Import from Canvas and Send to Canvas. `POST …/canvas-sync/preview` reads every linked course ONCE with the kept session
(`CanvasService.read_grades`, laid out for the To-Canvas half by `canvas_export.from_read`), with no lock held, plans both halves
under the roster lock, keeps its read (`_PREVIEWED`, key `("sync", term, "", fingerprint)`) and writes nothing. `POST …/canvas-sync` (the
summary's fingerprint, options, ticked `courses`, `held` picks, a request ID of 8 to 128 characters, `confirmation: "sync"`)
plans both halves again from the summary's own read, then reads again only the assignments it writes to or takes from
(`canvas_export.written`, `overlay`; and the assignments of a course where it creates one), refuses (409) when either changed,
then records, in ONE transaction, the `import` request (summary `source: "canvas"` and `sync`: `send`, `sending`, `taken`,
`held`, `creating`, `courses`, `in_step` {course: [columns]}), the From-Canvas writes (and Take Canvas's picks) and the send row,
and starts the send (`_run_send`). Its answer is `{sync, send, gradebook}`; the send's progress is `GET …/canvas-send/<id>`.
An answer the window could not read (none in time, or not JSON) is asked after by its request ID, `GET …/canvas-sync/requests/<id>`
(`canvas_sync_lookup`, docs/decisions/canvas.md A LOST ANSWER): the same answer once recorded, else `working` or `unknown`.
The View's `canvas` adds `started`, `sent_before`, `courses` {section: Canvas course}, `last_sync` {at, sending, held, taken,
creating, send?} and `changed_since` (cells of Faculty's columns changed since the last sync, catalog only).

**Canvas as Sync found it** (requirement, 2026-10-08: "keep compressed canvas copies on sync then"; `gradebook/canvas_snapshot.py`,
migration `011_canvas_snapshots.sql`). In its transaction every Sync also records ONE `gradebook_canvas_snapshots(id, term_id,
request_ref, format, courses, submissions, size, sha256, data, created_at)` row for its `import` request: the read it planned from
(`sendread`, the summary's read with `read_for_send`'s re-read laid over it), per course `groups`, `assignments`, `students`
(`sis` dropped, `row` the matching membership or null), `submissions` and `read_again`, as compact sorted JSON compressed with gzip
(built before the lock is taken). `size` and `sha256` are the JSON's; `canvas_snapshot.load(db, term, request_id)` reads one back
and checks both. Immutable and retained (triggers); a replayed or refused Sync keeps none. An estimate on 2026-10-08 (MATH
140's four courses, 82 columns, 340 students): about 28,000 submissions, 4 MB of JSON, at most a few hundred KB stored.

### From Canvas

- **Students**: a membership reads the Canvas course linked to ITS section, the student there whose SIS user ID is the UIN
  exactly. Dropped students are never filled in bulk; students of sections without a link, Faculty students not found in Canvas,
  Canvas students on no roster here and Canvas students without an ID are counted (the first and second listed by name).
- **Matching, once**: each Canvas assignment (`"<course>:<assignment>"`; `not_graded` and deleted ones are not columns) takes
  its REMEMBERED column, else the one live column of the same title, else of the same `exam_links.canonical` title ("Test 1" is
  "Exam 1"), ties narrowed by points and then by due date; an ambiguity suggests nothing (except a column Jev names, docs/decisions/canvas.md "JEV MATCHES"). An assignment with no match defaults to
  a NEW column (the instructor's choice) titled as in Canvas, worth its points, due on its campus-local due date, in the category
  named after its Canvas assignment group (an existing one of that name, else a new category created in the same setup
  revision). That holds with nothing graded yet too (requirement, 2026-10-02: "it should default to importing all columns as
  they will eventually need to be filled"). One Canvas gives no points (none or 0) suggests nothing: it waits under **Needs your
  choice**, left out until the instructor gives a new column its points or chooses a column, while the rest imports (the same day:
  one such column, unnamed in a folded group, held back an import of 43). The same new title in several courses is ONE column
  when points and category agree; one course's two assignments are never one column. (Import from iClicker still skips a session
  with no scores, without remembering it.)
- **Remembered** (`gradebook_canvas_links`, migration `002_canvas_links`, TERM-owned, append-only, latest per course and
  assignment counts): at the apply, every assignment sent to a column (and every new column, with its ID) is recorded, and a
  skip the instructor chose over Faculty's suggestion is recorded as skip. Later imports use the ID: renaming either side
  changes nothing. A remembered column that is archived or gone asks again.
- **What is written**: an assignment linked in this sync (a new column, or a column it goes to for the first time: `sync.adopting`)
  fills only the cells Faculty never filled (no revision, or emptied by an import), with Canvas's grade AS ENTERED (before its late
  penalty: what Faculty sends back); an assignment Canvas grades (`sync.mirror`: WebAssign's, and a Canvas quiz, `canvas.web.
  canvas_quiz`, requirement, 2026-10-06) MIRRORS Canvas (below, the nightly import);
  every other cell is the To-Canvas half's. New columns apply only to the sections linked to the courses that have the assignment
  (all of them: no list), and a column limited to other sections is widened (`widen`). A column a WebAssign assignment goes to is
  marked `canvas: "webassign"` or `"quiz"` in the same setup revision (`sync.mark` {column: mark}).
- **Values**: Canvas's `score` (after its late and missing penalties; WebAssign's) or, for Faculty's own columns, its
  `entered_score`, is a score, rounded half up to four decimals with a warning; excused is `excused`; `pending_review` waits; the missing flag with no score is `missing` only with
  the tick "Record Canvas's missing flag as missing"; anything else is blank (nothing changes). Grades not yet posted to students
  in Canvas are imported and counted (`unposted`; the instructor's choice).
- **Conflicts**: the From-Canvas half never writes over a value Faculty holds, except WebAssign's (mirrored) and a held grade
  the instructor takes from Canvas (Import from Canvas's KEEPS OTHERS of 2026-10-04, made stricter). A column linked to Faculty's
  grading (FRQ, bubble sheets, Exams) is never written from Canvas. A Canvas assignment worth other points than its column is recorded as Canvas has it, with a
  warning.
- **Columns not in Canvas** (requirement, 2026-09-28): every live gradebook column that no Canvas assignment of a linked course goes
  to, where the column applies to that course's students, is listed (`unlinked_items`, per course) with a search over that course's
  Canvas assignments; choosing one sends that assignment to the column (the ordinary mapping, taken from wherever it went) and the
  summary is read again. Otherwise the To-Canvas half creates it once it has grades.
- **The grade check** (the same day): every match, proposed or remembered, compares the grades both sides hold: `compared`,
  `mismatch` (both hold a grade and they differ) and `faculty_only` (Faculty has a grade, Canvas none: listed as a
  `faculty_only` row, never written), per column as `check` with `proposed` (a match not yet remembered). A proposed match that
  disagrees is also warned about by name, since it may be the wrong assignment. The check decides nothing: an import-written value
  that differs is still updated, a typed one kept, a compare-only one listed.
- **The dialog** (requirement, 2026-09-29: "very cumbersome and not very intuitive"; `static/gradebook-dialogs.js` `openCanvasSync`,
  shared with iClicker) opens on a plain summary of what Sync will do (grades sent, assignments created, grades taken, new
  columns, held grades), the warnings (grades students see at once, grades taken out of Canvas), the held grades with a choice
  each (**Changed in Canvas: your choice**: Decide later, Keep Faculty’s, Take Canvas’s; never folded), the courses written to,
  then the assignments in groups: **Needs your choice** (open), **New from Canvas** (open up to eight), **WebAssign**, **Already
  linked** and **Left out** (folded), then **To Canvas**, **Not this time** and **Can’t sync** (folded). Its outward button
  says what it does ("Sync · 12 grades to Canvas · 3 grades from Canvas"); while the send runs the dialog shows its progress. Only the first shows a picker; the rest show where they go with a Change button (a new column missing its
  title or points stays open under Needs your choice, and the footnote names it). A picker lists likely matches first ("Exam I Grade" for "Exam 1"). Notes, counts and the per-student changes
  ("Review every change") are folded. All state is browser memory; the server's plan, fingerprint and checks are unchanged.
- **Summary, then Sync**: the summary writes nothing. Sync names its fingerprint (options, mapping, what would be
  remembered, the assignments' titles, points and due dates, and every change with the cell it replaces) and WRITES WHAT THE
  PREVIEW SHOWED (requirement, 2026-10-02, after WebAssign grades syncing into Canvas refused every Import): it plans the
  preview's own Canvas read (`_PREVIEWED`, memory only, by kind, term, section and fingerprint, the latest 4 for 30 minutes)
  against the catalog as it is then, and is a 409 when that differs, so a gradebook change since the preview is still refused
  while grades Canvas gets after the preview come with the next import. Only when that read is not kept (a restart, too old) or
  a section now links another course does it read Canvas again, a 409 on any change. Inside `BEGIN IMMEDIATE` it checks again
  that the setup revision, the remembered matches and every replaced cell are unchanged. It records ONE `import` request (summary `source: "canvas"`, the courses, the columns
  per assignment, counts), any new categories and columns as ONE setup revision under the same request ID, the remembered
  matches and the `add`/`update`/`clear` cells (source `import`). Every sync records its request.

### To Canvas

The instructor asked on 2026-09-28: "Now build the assignments and grade exporting" (then Send to Canvas, now Sync with Canvas's
To-Canvas half). Every column FACULTY OWNS goes to Canvas: every column but WebAssign's (`canvas: "webassign"`), a Canvas quiz's
(`canvas: "quiz"`) and one kept off Canvas (`canvas: "off"`, Column settings); columns Faculty grades itself included. WebAssign's
and Canvas quizzes' columns take only Faculty's counted 0s (ZEROS GO TO CANVAS, below).

- **Where a column goes**, per Canvas course: the Canvas assignment an import matched it to (`gradebook_canvas_links`, the latest
  per assignment), else the assignment Faculty's Canvas copy made for it (an assignment of the copy naming the column as its
  `gradebook_item`, pushed to that course; canvas/README.md). Two assignments for one column in one course are never guessed
  between. A column with no assignment is listed and sends nothing unless the send creates it (below).
- **Students** are found through their permanent membership: the Canvas course linked to THEIR section, the student there whose
  SIS user ID is their nine-digit UIN exactly. Dropped students are never sent. No UIN is in the preview.
- **Values**: scored sends its points, excused sends "excused", missing sends 0 (Canvas's bulk update has no "missing"); scoring a
  grade Canvas excused adds `"excuse": false`; an empty cell takes the grade out (`"posted_grade": ""`) only where Canvas shows
  what Faculty last knew there.
- **ZEROS GO TO CANVAS** (requirement, 2026-10-09, of Canvas's blank Class 2 and Class 3 polls: "Faculty should be pushing its
  zeroes for any assignment, otherwise I have to manually put them in and that defeats the point"): `_sync_plan` reads the
  results of the cells as the From-Canvas half leaves them (`_results`, the grid's own) and passes their `blanks` "zero" cells
  as `canvas_export.plan(zeros=)`. Such an empty cell's value is `canvas_export.ZERO` (0, marked `zero`, kept in its receipt) and
  goes by the rule below like any value (Canvas's blank takes it; a grade Faculty never knew is still filled or held). A cell
  that stops counting (a makeup owed, a later due date) is empty again, and Canvas's 0 is a grade Faculty knew, so the next sync
  takes it out. A WebAssign or Canvas-quiz column gets ONLY those 0s and only where Canvas shows no grade (plan entries
  `zeros_only`, never created, published or left `in_step`); Canvas's own grades stand there and the From-Canvas half reads the 0
  back. The summary's column says `zeros` (how many of its grades are such 0s) and `zeros_only`, each only when there is one.
  The Canvas comparison of `reconciliation.read` and Teaching review's delivery count such a 0 as Faculty's value.
- **Per value** (`canvas_export.plan`; requirement, 2026-10-06: a grade changed in Canvas since the last sync is HELD): Canvas's
  grade is read as entered (`entered_score`), rounded to four decimals, the score after its penalty counting as the same grade.
  Canvas shows Faculty's value → same. Canvas shows what Faculty last KNEW there (`agreed`: the latest of what Canvas showed
  after an accepted send, a value an import from Canvas wrote into the cell, and the cell's value at the last sync that left the
  column and course in step (`sync.in_step`) unless that sync sent it; plus a later uncertain send's value) → added, updated or
  taken out. Anything else, into a cell Faculty never filled (no value, or emptied by an import) of a column Faculty does not
  grade itself → FILLED from Canvas (`fills`, `filling`; requirement, 2026-10-06). Anything else → held (`held`), sent with **Keep Faculty’s** (a `replace` value) or written into Faculty with **Take
  Canvas’s** (not for a column Faculty grades itself; `canvas_export.choose`). A submission pending review is left alone; Canvas's
  form of a grade Faculty sent, alike to the hundredth, is the same.
- **Nothing is sent** to an assignment that is graded pass/fail or not graded, or is worth other points than the column. One that is
  not published is offered **Publish in Canvas, then send** in the summary, ticked by default (requirement, 2026-10-07: "Publish
  from Sync"; `canvas_export.plan(publishing=True)` marks it `publish`, `choose(..., publish)`): ticked, the send publishes it in
  each course before its grades (`_publish_assignment`, receipts `gradebook_canvas_published`, migration 010, and Faculty's Canvas
  copy published too, `column_assignments.published_by_sync`); unticked, it sends nothing and is not left in step. An assignment Canvas posts automatically is named (students see its grades at once) and the Send asks once more.
- **The send**: Sync records it with its request (above) and it runs in threads of the web process, one per Canvas course, at
  most `SEND_COURSES` (4) at once, each course's assignments one after another (requirement, 2026-10-06: one after another, 20
  assignments in four courses took seven minutes); a cancel, a signed-out session or a failure in one course stops every course
  before its next assignment. Its Canvas client waits out Canvas's throttle once (`web.Client(patient=True)`, `THROTTLE_WAIT`).
  One Canvas bulk update per assignment, followed until Canvas finishes (`SEND_WAIT`), then READ BACK. Each value is recorded
  (`gradebook_canvas_sent`, migration 003, owned by the section membership, with the send in `gradebook_canvas_sends`, owned by the
  term): uncertain BEFORE Canvas is asked (so a send cut off in between keeps a receipt for every value it may have written), then
  settled by the read-back: accepted when Canvas shows what was sent, failed when Canvas finished and does not (or refused the update
  itself), uncertain when Canvas never said it finished or could not be read back. The latest receipt counts. A restart waits for a
  running send, the whole of it (canvas/stopping.py `STOP_WAIT`, 600 s since 2026-10-06), and for a Sync still reading Canvas, which then ends before recording anything (503, nothing
  written; requirement, 2026-10-06); a send this process no longer runs is `interrupted`; the next preview reads Canvas as it is.
- **What Faculty knew** is read from these receipts (`GradebookService._receipts`), the cells' revisions (`_revisions`, with
  which ones an import from Canvas wrote) and the syncs' `in_step` (`_synced`).
- **One assignment, one column**: when an import's match and the copy's link name two columns for one Canvas assignment, neither is
  sent there and both say why (`canvas_export.targets`).
- **Creating a missing assignment** (requirement, 2026-09-28, of iClicker sessions: "exporting will need to make the corresponding
  assignment in the checks for understanding category in assignments in canvas"; 2026-10-06, for every column: "Create it in
  Canvas"): a column with no Canvas assignment in a course whose sections it applies to is created when it has grades to send,
  its category naming exactly ONE assignment group of that course and Canvas having no assignment of the column's title (then
  Sync with Canvas offers to link that one); a column Faculty's Canvas copy names waits for the copy's push. The send reads Canvas again,
  creates it (the column's title and points, published, no submission, in that group; its values are then all "added"), records it
  (`gradebook_canvas_created`, migration 005, owned by the term through the send, `UNIQUE(course, assignment)`) and sends its values.
  An answer lost while creating is settled by reading Canvas: the one new assignment of that name is adopted (`adopted` in the
  record), anything else fails that column with the reason, never a second POST. The next sync's two halves treat the
  record like an import's match (an import's own later decision wins). A new assignment is assumed to post automatically, so the
  summary's warning names it.
- **WebAssign's assignments are never sent** (requirement, 2026-10-06: Canvas always wins; before, 2026-10-04: "I don't want it to
  overwrite the true scores in canvas"). RULE `gradebook-canvas-sync-v1`.
- **The sections ticked** (`courses`): every linked course by default; a term's first sync that writes to Canvas starts with the
  instructor's section's course alone. A course not ticked is planned and shown (**Not this time**) but not sent.
- The only assignments it creates are the missing ones above; the only posting policies it changes are POSTING GRADES' switches.

### Posting and hiding grades in Canvas (`gradebook/canvas_posting.py`, `gradebook/posting_policy.py`; requirement, 2026-10-06 and 2026-10-08; docs/decisions/canvas.md POSTING GRADES)

Posting reaches Canvas only during a Sync with Canvas, the instructor's or the morning one.

- **Each column's desired state** (`posting_policy.desired`, first that applies): the instructor's choice (Post / Hide); always by
  hand when the column does not count (archived, not counted, no category or a category of weight 0 in weighted mode); posted when
  iClicker's sessions go to it; its category's `posting` rule (`due`: a column Faculty does not grade, once its due date has
  passed on the campus clock; `certified`: a column linked to grading, once its plan is certified); otherwise nothing. Posted
  stays posted: every sync posts what Canvas still hides. A rule never hides.
- **A column's menu**: **Post in Canvas at each sync…** / **Hide in Canvas at each sync…** / **Back to the category's posting…**
  (sections linking a Canvas course, a column not kept off Canvas) open a confirmation that asks nothing of Canvas; `POST
  …/canvas-posting/choice {item_id, choice: post|hide|rule, base, request_id, confirmation: "choose", section_id?}` records it
  (`gradebook_canvas_posting_choices`, migration 012, term-owned, append-only; `base` the choice the page showed, a different one
  now a 409) and answers the gradebook.
- **At every sync**: `_sync_plan` adds `out["posting"]` (`posting_policy.steps`, from the sync's own read: per column and course,
  post, hide or switch, with what waits) and folds `posting_policy.digest` into the fingerprint; the summary shows it as
  `posting_plan` ("Posting in Canvas"). Sync settles it with the instructor's picks (`posting_policy.settle`: a post waits where a
  grade of it is held and undecided, or its unpublished assignment is left unpublished) and starts `canvas_posting.after_sync`
  (restart key `after:<send>`): the send (`_run_send`: each course's switches first, `_switch_policy` with
  `gradebook_canvas_policies` receipts, then its grades; an assignment the sync creates for a by-hand column switched right
  after), then the posts and hides: one read of the step assignments, then one `gradebook_canvas_posts` row per column and action
  (request ID `policy:<send>:<column>:<action>`) with an uncertain receipt per course before Canvas is asked and each settled by
  reading back. A post is done in a course only when every grade the sync sent there for the column was accepted; one withheld
  for that says so in its errors (`waits: "grades"`, `course_id`, `assignment_id`), and the morning's follow-up carries it out
  once those grades are confirmed (`canvas_posting.follow_posts`, below). The send's status (`GET …/canvas-send/<id>`, and the
  sync's own answer) carries `posts` and `posting` (posts still running).
- **The mark**: `View.canvas_posting {item: {hidden, graded, courses, manual, read_at, source, policy, choice?, choice_id?,
  pending?}}` (a column Faculty has read, or one with a choice) gives the heading a chip "N hidden" and, dashed, "posts at sync" /
  "hides at sync" / "by hand at sync"; its title and the column's menu say why. Every count is of the course's current students
  (an active or invited Canvas enrollment; `posting_policy.CURRENT`).
- The assistant's choice is an Approve card (`confirmation: "choose"`); its Sync carries the posting steps its summary showed.

## The morning sync (`gradebook/canvas_nightly.py`; requirement, 2026-10-08; docs/decisions/canvas.md NIGHTLY SYNC)

- **When**: the web process starts `CanvasNightly.worker` (`app.py`, never in checks: they call `round` with their own clock;
  lock `6723d2e1-…`, the 10 pm import's); every five minutes it looks at the campus clock and, from 05:00 until 08:00, runs each
  current term with a linked Canvas course once that morning. A morning that could not read Canvas or whose sync failed is tried
  again after an hour; a stopping Faculty starts none.
- **What**: one read of the linked courses (no lock held), the grid's posting mark, the due dates of WebAssign's assignments and
  Canvas quizzes following Canvas (`WebAssignNightly._follow`), then a term a Sync has written to (`_written_courses`: an accepted
  grade or a created assignment, sends without a section) gets `canvas_sync_nightly`: Sync with Canvas from that read for those
  courses only, with the summary's defaults (options none, no held pick, no Jev, default publish ticks minus uncertain ones,
  `canvas_export.plan(automatic=True)` leaving out values whose latest receipt is uncertain), request ID
  `canvas-nightly:<date>:<random>`, `automatic: "canvas-sync"`; a term no Sync has written to gets the WebAssign import below.
- **Confirmed by a later read** (`gradebook/canvas_confirm.py`; requirement, 2026-10-10; canvas.md CONFIRMED BY A LATER READ):
  Canvas sometimes applies a bulk grade update after the send stopped waiting (`SEND_WAIT`), leaving its receipts uncertain.
  `record(db, term, canvas)` appends an `accepted` receipt, on the send that sent it, for every value whose latest receipt is
  uncertain and which a read shows as sent; it runs in every Sync's transaction and on the morning run's read (`read`, best
  effort). `follow(svc, term, send)` is the morning's follow-up, called by every later `round` of that morning for the run's
  send (`CanvasNightly._follow`): one `read_for_send` of the assignments still unconfirmed, `record`, the grid's posting mark,
  then `canvas_posting.follow_posts`: the posts that send withheld for unconfirmed grades, one row per column and course
  (request ID `policy:<send>:<column>:post:<course>`, fingerprint `confirmed`, `progress.confirmed`), asked only where Canvas
  still hides a grade and the column is still to be posted. Nothing is ever sent again. `left(db, term, send)` is the View's
  `last_sync.send.unconfirmed`.
- **Records**: `gradebook_canvas_nightly_runs` (migration 013, term-owned, append-only): `night` (campus date), `kind` (`sync`,
  `import`), `status` (`synced`, `imported`, `unchanged`, `none`, `needs_choice`, `signed_out`, `failed`), `detail_json`
  (counts, waiting `{key, title}`, `unsure`, `posting`, a problem's words; no student), the request and send. The View's
  `canvas.nightly` is the latest run (the 10 pm import's before there was one) with its send's status and `posts_failed` (the
  columns whose post or hide still needs the instructor, `canvas_posting.outstanding`). A follow-up records no run.

## The WebAssign import (`gradebook/webassign_nightly.py`; requirement, 2026-10-04; in the morning sync since 2026-10-08)

The instructor asked for WebAssign's grades to come in by themselves, nightly at 10 pm Central; since 2026-10-08 the morning sync
runs these steps (the 10 pm loop is gone, its rows stay as history). WebAssign sends each score to
Canvas itself (Cengage's LTI sync, as a student submits and as a score is edited in WebAssign), so the scores come from Canvas.

- **Which assignments**: those Canvas hands to Cengage's tool (`canvas.web.tool_host`: submission type `external_tool`, tool
  address on cengage.com, webassign.net or webassign.com; `graded_by_webassign`). The Canvas readers (`assignment_groups`,
  `assignments`) carry the tool's host as `tool`. Gradescope's tool assignments do not count.
- **When**: each morning, for a term no Sync has written to (the morning sync above).
- **What**: the linked courses are read with no lock held, every other assignment is dropped (`webassign_only`), and the Canvas
  planner plans the rest with no options (`normalize_options(None)`: remembered matches, then titles, then new columns; no Jev).
  When it writes anything, `_source_apply` imports exactly that plan (`given` the read, summary `automatic: "webassign"`, a
  request ID `webassign-nightly:<night>:<random>`, `sync={"mirror": None}`): CANVAS ALWAYS WINS (requirement, 2026-10-06), the
  column mirrors Canvas (a typed value replaced, a blank emptying the cell), the column is marked `canvas: "webassign"` (read-only
  in Faculty), Canvas's missing flag is not recorded and pending review changes nothing. A plan that is not `ready` writes nothing (`needs_choice`);
  an assignment suggested as skip for `ambiguous` or `points` is listed as waiting and the rest imports.
- **Records**: the morning sync's (above); the 10 pm import's `gradebook_nightly_runs` (migration 007) are read as history. The
  View's `canvas.nightly` names the waiting titles not matched or skipped since and, for a signed-out run, `signed_in_since` (a
  newer Canvas sign-in is kept). The grid shows a notice only when it needs the instructor.
- **Leave out** (requirement, 2026-10-04: "skip Graphing Practice in the import"): the waiting notice's second button posts the
  view's `waiting_keys` ([{key, title}]) to `POST …/canvas/skip` (`canvas_skip`: keys of the term's linked courses only, at most
  200; one `import` request, summary `skipped` titles, `written: 0`; a `gradebook_canvas_links` row with no column per assignment
  not already skipped; no Canvas read). The nightly import and Sync with Canvas then treat them as remembered skips; choosing a
  column in Sync with Canvas brings one back. The Changes page says "Left out of Sync with Canvas: “…”".
- **Due dates follow Canvas** (requirement, 2026-10-05: "extend it to also update the due date in the gradebook and canvas
  content page"; 2026-10-08, Canvas quizzes too; docs/decisions/canvas.md WEBASSIGN DUE DATES). Each morning before anything is
  planned, `follow_gradebook` gives each column linked to one of the morning's WebAssign assignments or Canvas quizzes (the latest `gradebook_canvas_links` row per Canvas assignment)
  Canvas's due date as a campus date, the LATEST of its linked courses when they differ (a column has one date). Only a column
  whose date was last set by an import from Canvas (a sync, the nightly import) or by this step, or that was made without a date and never given one, moves
  (`due_origins` walks the setup revisions); a date the instructor typed stays, and a Canvas assignment without a due date moves
  nothing. The moves are one setup revision with request ID `webassign-due:<night>:<random>` (actor `workspace`); the Changes
  page labels it "Due date(s) updated from Canvas overnight" (the entry's `automatic: "webassign"`), the
  Calendar shows the new deadline, and the Google calendar follows if gradebook deadlines are synced. Then Faculty's Canvas copy
  follows (`CanvasContentService.follow_due_dates`, canvas/README.md). The night's `detail_json` counts `due_dates` and
  `copy_due_dates` when something moved (`copy_published` when the copy followed what Canvas published), and names `due_problem`/`copy_due_problem`/`copy_published_problem` when a step failed (logged; the status stays
  the import's).

## Importing from iClicker (`gradebook/iclicker_import.py`)

The instructor asked on 2026-09-28 to integrate iClicker "like we did canvas and gradescope" and chose: import scores into the
gradebook, ONE COLUMN PER CLASS SESSION. **Import from iClicker** is offered on a section's gradebook wherever iClicker is set up
(AGENTS.md "iClicker"). The section's iClicker course is chosen in the dialog the first time (a proposal is preselected) and
remembered for the section; **Change course…** chooses again. Faculty reads that course's roster and polling sessions with the
saved sign-in, with no lock held, then plans with the Canvas planner (`canvas_import.plan` with the `ICLICKER` source), so
everything "Syncing with Canvas" says of its From-Canvas half holds (as Import from Canvas did), except:

- **Columns**: one per POLL session that has ended (requirement, 2026-10-06: "just limit it to poll": exit polls, quizzes, attendance
  and any other kind are only counted, `iclicker_import.scored`; a session still running waits),
  keyed `<iClicker course>:<session>` and remembered by the session's ID (`gradebook_iclicker_links`, migration
  `004_iclicker_links`, TERM-owned, append-only). A session's title is its iClicker name; repeated names in one course take the
  session's date ("Session · Sep 3"). New columns have no due date (a class meeting is not a Calendar deadline); the preview shows
  the session's date and question count. EVERY SESSION IS WORTH 10 POINTS AND BELONGS IN CHECKS FOR UNDERSTANDING (the instructor,
  the same day: "iclickers are 10 points each and they are always in the checks for understanding category"): a new column is worth
  10 and goes in the term's category named "Checks for Understanding" (with or without "Category"), else in a new category
  "Checks for Understanding Category" (the Canvas assignment group's name, so Sync with Canvas creates its assignment there). A score
  above 10 is recorded as iClicker has it, with a warning. **Put the N new columns in** moves every new column at once.
- **Per section**: a new column applies only to the sections linked to its course; the same title and points in several courses
  are one column for all of them. A session matched to a column limited to OTHER sections WIDENS that column to this section
  (`widen` in the plan, recorded in the import's setup revision), never skips it; columns Faculty grades itself keep their sections.
- **Values**: the student's total points in the session (participation plus performance), as iClicker's gradebook counts them,
  including a score the instructor changed by hand there (docs/decisions/iclicker.md HAND CHANGES COUNT: each adjusted part stands
  in for its original, `iclicker_import.points`). A student who never joined a session has no iClicker record there: `missing` only with the tick "Record a session a student did not
  join as missing", else nothing changes. A matched column keeps its own points (a warning names one worth other than 10).
- **Only its own values are updated** (ICLICKER KEEPS OTHERS; requirement, 2026-10-04, of a student's iClicker given 10/10 that the
  import set back to 0: "this is what I don't want ie I change a student's iclicker to 10/10 but the import will 0 it back out"):
  Import from iClicker updates a cell only when an earlier iClicker import wrote its value. A value another import brought in
  (Canvas, a file) or a pull wrote is `kept`, like a typed one, unless Replace is ticked for that student; the change names where
  it came from (`from`: `changes.origin`, "10 from Canvas" in the preview). Sync with Canvas never overwrites Faculty's values from
  Canvas either (except WebAssign's and a held grade the instructor takes from Canvas).
- **Students**: the iClicker student ID when it is a nine-digit UIN of this term's roster; else the iClicker account's email when it
  equals exactly one roster email of the term (case-insensitive, counted and warned about; "@email.tamu.edu" and "@tamu.edu" are
  one mailbox, requirement, 2026-10-02: the roster says one, iClicker's Canvas-synced accounts the other); else not matched and listed by name
  ("could not match"). Students who removed themselves from the iClicker course are read for their scores but never listed.
  Emails, like UINs, never leave the server.
- Nothing is written to iClicker; the request's summary says `source: "iclicker"`.
- **Session dates and excused absences** (requirement, 2026-10-01: "Whenever a student's absence is excused, any iclicker that falls
  in that date range should automatically receive 10/10"; docs/decisions/absences.md ICLICKER-CREDIT): the apply records the campus
  date each session started on (`gradebook_iclicker_sessions(id, term_id, course_id, session_id, session_date, request_ref, actor,
  created_at)`, migration `006_iclicker_sessions`, TERM-owned, append-only, the latest per session counts; a row only when the date
  is new or differs), then `credit_excused` for the students the import covers, in the same transaction: each session of the
  section's iClicker course dated within an excused absence of the student gets its column's full points (`excused_credit.plan`:
  FILL, unless already full or changed by hand after an earlier fill; PUT BACK what a fill replaced once no excused absence covers
  it). The same runs for one student whenever an absence of theirs is saved with a new revision. Each absence's fills (or
  put-backs) are one `scores` request, summary `{source: "absence", absence_id, action: "fill"|"put_back", dates, cells, items,
  section_id, rule}`; the cells are source `instructor` (an import keeps them unless Replace is ticked) with the note "Excused
  absence (<dates>): full credit." A later import's preview lists such a cell as kept, and says it is an absence's credit.
- **The preview says it** (requirement, 2026-10-02: "yes add the preview line"): the plan's `excused` (`_excused_preview`: the same
  `excused_credit` rule on the term as it will stand after the import, its new columns, matches, widened sections and values laid
  over the catalog) names the students and sessions Import gives full credit, and how many kept values are an absence's credit;
  "When you press Import" shows both (`excusedLines`). It is folded into the fingerprint, so an absence decided after the preview
  makes Import ask for a new one. No key when there is nothing to say.
- **Credit before the import** (requirement, 2026-10-21: students who cannot open iClicker email their ID and the projected question
  before class ends, "so followup hasn't had time to actually import that clicker question yet"; docs/decisions/iclicker.md CREDIT
  BEFORE THE IMPORT): `POST …/iclicker/credits` records one student's credit for their section's polls of one campus date (up to
  today) with the cell's note (`gradebook_iclicker_credits`, migration `014_iclicker_credits`, owned by the SECTION MEMBERSHIP,
  append-only, `credit_id` groups a credit's rows and the latest counts: `waiting` or `withdrawn`). `credit_iclicker`
  (`iclicker_credit.covered` + `excused_credit.plan`) runs at once and after `credit_excused` in every iClicker apply, so a poll of
  that day gets its column's full points as soon as it has a column, the after-class import's 0 included. Same FILL and PUT BACK
  rules as an absence's credit; the note is "<reason>: full credit."; each credit's batch is one `scores` request, summary
  `{source: "iclicker_credit", credit_id, action, class_date, cells, items, section_id, rule}`. A second credit for a student and
  day already waiting answers that one. Withdraw puts back what it filled. The preview's `excused` line does not list these.

## iClicker after class (`gradebook/iclicker_after_class.py`; requirement, 2026-10-06)

"i'm imagining we set iclicker imports to be scheduled every class day after each section's class ends (that way we immediately
capture if any iclicker was given and the date so that follow-up has more accurate information to go on and the auto-excuse
function is improved)" (docs/decisions/iclicker.md AFTER CLASS).

- **When**: `IClickerAfterClass.worker`, a thread of the web process (`app.py` starts it when Faculty serves; lock `3c0f2a8e-…`),
  looks at the campus clock every two minutes. For each current term that uses its Term schedule (`scheduling.service.current_use`),
  today's class meetings (`occurrences`, days off skipped) give each section's last class end; ten minutes after it (`DELAY`) a
  section that links an iClicker course is run once for the day. A day is done after a run that read iClicker with no session of
  the day still running; a running session is looked at again after 15 minutes (`RUNNING`), a failure or a session waiting for a
  column after an hour (`RETRY`), until campus midnight.
- **What**: `_source_plan("iclicker", …, missing_on={day})` with no choice but `missing_as_missing`: a roster student who did not
  answer one of TODAY'S sessions is `missing` (asked, the instructor chose "Missing (0)"), only today's (a student added later
  never gets 0s for earlier classes) and only a session someone answered (one nobody answered makes no column). Then
  `_source_apply(…, given=…, note={"automatic": "iclicker"}, missing_on={day})` for that section (request ID
  `iclicker-after-class:<day>:<random>`): KEEPS OTHERS and typed values as always, session dates recorded and excused absences
  credited in the same transaction.
- **Records**: `gradebook_iclicker_runs` (migration 008, section-owned, append-only): `class_date`, `status` (`imported`,
  `unchanged`, `needs_choice`, `not_linked`, `failed`), `detail_json` (`sessions` [{id, title}] iClicker started that day,
  `running`, `written`, `new_columns`, `waiting` titles, `problem`; never a student) and, for `imported` only, the request. The
  View's `iclicker` adds `after_class` {section: latest run} and `scheduled`; the grid's `iclickerNotice` names a run only when it
  needs the instructor (Import from iClicker brings it in now), and says how to turn the runs on while the term does not use its
  Term schedule. The Changes page says "Imported from iClicker after class".
- **Follow-ups** read the runs and sessions (`FollowupSuggestions._iclicker_facts`): each class day of a student's sections around
  their message, with the sessions given and whether they answered, "no iClicker given", or "not checked yet".

## What an import or a pull does to a cell (`common.change_action`)

`add` when the cell is empty, `same` when it already holds that value (nothing written), `kept` when the instructor typed its
value in Faculty, or CLEARED it there, or (Import from iClicker or from Canvas, `foreign`) another import or a pull wrote it, and
did not tick Replace for that student, `update` otherwise. Hand-entered values are never silently replaced, and a grade the
instructor took out (a mis-assigned paper) is not quietly put back by the next pull. Imports and pulls propose no note: a written
cell keeps its note.

## Changes (`gradebook/changes.py`; requirement, 2026-10-01)

The instructor asked whether the app tracks every change to the gradebook; told that every write is kept but only one cell's
history could be seen, they asked to "fix the label and add the changes page". `#course/<gid>/section/<n>/changes` (the grid's
**Changes** link, and **All changes** under Recent changes) lists every setup revision of the term and every request that touched
the section, newest first and grouped by day, each with what it changed. It reads the immutable rows only: no record, no write
path, no migration.

- A SETUP REVISION says what it changed from the revision before (`changes.setup_changes`: how it counts, the categories, the
  columns, the letter scale; the first revision is compared with `setup.default_setup()`). A revision an import or a certification
  wrote (the same request ID, or `<request ID>:column`) is shown with that request, not on its own.
- A REQUEST says what made it (`changes.origin`): `typed` (the grid, a dialog, a bulk mark), `absence` (an excused absence's
  iClicker credit, a `scores` request whose summary says `source: "absence"`), `file`, `canvas` or `iclicker` (an
  import, by its summary's `source`), `pull`, or `certification` (recorded in a certification's transaction, request ID
  `certify:…`; a later Pull of a certified plan is a `pull` naming the certification). It
  carries its stored summary and counts the section's cells it `added` (no value before), `changed` and `cleared`; `elsewhere`
  counts other sections' cells, and a pull's or an import's own counts say what else it found (kept, waiting, unmatched …).
- On a section tab a request is listed when it wrote one of the section's cells, wrote a setup revision (shared by every section),
  or wrote nothing and was not started on another section's tab (its summary's `section_id`). Setup revisions are listed on every
  section's page.
- Cells: a request that wrote at most five of the section's cells lists them with its page (`detail`); a larger one is read when
  the instructor opens it (`…/changes/<request>`). Each is `{item_id, row, seq, before, after}`, `before` the revision it replaced
  (null for the cell's first). Row keys and column IDs only: the page names students and columns from the View on screen.
- 50 entries a page; `next` names the page's last entry (`<created_at>|<type>|<position>`), and **Show older** asks for the
  entries after it. Catalog rows only and no roster lifecycle lock (like the term list), so the page never waits behind a pull.
- Labels: a cell's history names an import's source ("Imported from Canvas", "Imported from iClicker", "Imported from <file>") and
  an absence's credit ("Full credit for an excused absence (<dates>)", "Put back: no excused absence covers this session now"); a
  View cell carries only its `source`, so the grid's key and a cell's title say "Imported" (or "Typed" for an absence's credit);
  Recent changes names an absence's credit "Excused absence (iClicker credit)".
- The assistant's own entries say so ("Entered by the assistant", "Setup changed · by the assistant"), as do a cell's history and
  title ("Entered in Faculty · the assistant") and Recent changes (`actorText` in gradebook.js).

## API (blueprint `gradebook`, prefix `/api/gradebook`; writes need `X-Teaching-Request: 1` like every route)

Row key: `"<section_id>:<enrollment_id>"` (`common.row_key`). JSON never carries a UIN; the CSV export does (it is the
instructor's file). JSON writes are limited to 2 MB; the two import routes are multipart with the file and named fields only.

| Route | Body / query | Returns |
| --- | --- | --- |
| `GET /api/gradebook` | | `{"terms": [TermSummary + scale]}` current terms first, then newest |
| `GET /api/gradebook/terms/<term_id>` | `?section=<section_id>` optional (that section's students); with it, `&cells=section` optional | `View` |
| `POST /api/gradebook/terms/<term_id>/setup` | `{"base_revision": int\|null, "request_id": str, "setup": {...}}` | `View` |
| `POST /api/gradebook/terms/<term_id>/scores` | `{"request_id": str, "changes": [Change]}` (≤ 5000) | `{"cells": Cells, "results": Results, "requests": [...]}` |
| `GET /api/gradebook/terms/<term_id>/history` | `?item=<item_id>&row=<row key>` | `{"revisions": [Revision]}` newest first |
| `GET /api/gradebook/terms/<term_id>/changes` | `?section=<section_id>&before=<next>`, both optional | `{"changes": [ChangeEntry], "next": str\|null}` newest first, 50 a page |
| `GET /api/gradebook/terms/<term_id>/changes/<request>` | `?section=<section_id>` optional | `{"cells": [WrittenCell]}` in the order written |
| `GET /api/gradebook/terms/<term_id>/grading-check` | `?section=<section_id>` optional | `{term_id, section_id, columns: {item_id: {fix: "certify"\|"pull"\|null, plan_id, certified_at, rows: [{row, section_id, kind: "changed"\|"typed", cell: {id, status, points, source}\|null, grading: {status, points}}], elsewhere?: {changed, typed}}}}`: only the columns that differ; with a section its students, the others counted under `elsewhere` |
| `POST /api/gradebook/terms/<term_id>/items/<item_id>/pull/preview` | `{"replace": [row], "absent_as_missing": bool}` | `PullPlan` |
| `POST /api/gradebook/terms/<term_id>/items/<item_id>/pull` | same + `"fingerprint"`, `"request_id"` | `View` |
| `POST /api/gradebook/terms/<term_id>/items/link` | `{"assignment_id", "request_id", "category_id"?: str\|null}` | `View` (+ `"item_id"`) |
| `POST /api/gradebook/terms/<term_id>/imports/preview` | multipart: `file`, `options` (JSON text) | `ImportPlan` |
| `POST /api/gradebook/terms/<term_id>/imports` | multipart: `file`, `options`, `fingerprint`, `request_id` | `View` |
| `POST /api/gradebook/terms/<term_id>/canvas-sync/preview` | `{"options": {"mapping"?, "missing_as_missing"?}, "courses"?: [course]}` | `{fingerprint, ready, options, courses, ticked, held, columns, posting_plan, to_canvas, from_canvas}` (401 when Canvas needs a sign-in) |
| `POST /api/gradebook/terms/<term_id>/canvas-sync` | `{"options", "courses"?, "held"?: {key: "faculty"\|"canvas"}, "publish"?: [column], "fingerprint", "request_id", "confirmation": "sync", "section_id"?}` (`post` is refused: posting follows each column's choice or rule) | `{sync, send (with its posts), gradebook: View}` |
| `GET /api/gradebook/terms/<term_id>/canvas-sync/requests/<request_id>` | `section_id`, `lost` | A LOST ANSWER: `{state: recorded, sync, send, gradebook}`, or `{state: working}` / `{state: unknown}` |
| `GET /api/gradebook/terms/<term_id>/canvas-send/<send_id>` | | the send's `{id, status, progress}` |
| `POST /api/gradebook/terms/<term_id>/canvas-send/<send_id>/cancel` | `{}` | the send, stopping before its next assignment |
| `POST /api/gradebook/terms/<term_id>/canvas-posting/choice` | `{item_id, choice: post\|hide\|rule, base?, request_id, confirmation: "choose", section_id?}` | the gradebook (nothing asked of Canvas; the next sync carries it out) |
| `GET /api/gradebook/terms/<term_id>/canvas-posting/<post_id>` | | the post's `{id, status, progress}` |
| `POST /api/gradebook/terms/<term_id>/canvas/skip` | `{"assignments": [{key, title}], "request_id", "section_id"?}` | `View` |
| `POST /api/gradebook/terms/<term_id>/iclicker/preview` | `{"options": {"mapping"?, "replace"?, "missing_as_missing"?}, "section_id"?}` | iClicker `ImportPlan` (plus `widen`, `unmatched`; 502 when iClicker cannot be read) |
| `POST /api/gradebook/terms/<term_id>/iclicker` | `{"options", "fingerprint", "request_id", "section_id"?}` | `View` |
| `POST /api/gradebook/terms/<term_id>/iclicker/credits` | `{"section_id", "enrollment_id", "class_date", "reason", "request_id"}` | `{credit, given: [{item_id, title, points}], filled, put_back}` (400 when the section links no iClicker course) |
| `POST /api/gradebook/terms/<term_id>/iclicker/credits/<credit_id>/withdraw` | `{"request_id"}` | the same |
| `GET /api/gradebook/terms/<term_id>/export.csv` | `?section=<section_id>` optional | CSV (below) |

The CSV has Name, UIN, Section, one `<title> (<points>)` column per column that is not archived (points, `EX` or `MI`), each
category's %, the overall % and the letter. Text that could start a spreadsheet formula is prefixed with `'`.

Shapes:

- `TermSummary`: `{id, group_id, definition_id, code, name, term_label, is_current, sections: [{id, number}], students, items, revision,
  graded_without_column}`. `students` and `items` are counts (items not archived). `graded_without_column` counts the
  assignments whose exam in this term has something to pull (`sources.graded_by_term`, the same three sources as
  `available`) that no column which is not archived references: read-only, catalog rows only, returned by `GET /api/gradebook` (no page shows it now). `group_id` is the course page's navigation ID for this code and term
  (`course_groups.navigation_id`), so `#gradebook?group=<id>` can resolve. The term list's rows (`GET /api/gradebook`, not the
  View's `term`) also carry `scale`, the latest setup's (else the default), for the course Home's Grades row.
- `View`:
  ```json
  {"term": TermSummary, "sections": [{"id", "number"}],
   "setup": {"revision": 3, "created_at": "…", "actor": "workspace", …setup fields} ,
   "students": [{"row", "section_id", "section": 507, "name": "…", "accommodations": ["Extra Time 1.50x"], "dr": ["<assignment_id>"]}],
   "cells": Cells, "results": Results,
   "canvas": {"linked_sections": 2, "last_import": "…"|null}|null,
   "assignments": [{"id", "title", "key", "plan_id": str|null, "event_id": str|null, "parts": ["frq", "bubble", "exams"],
                    "maximum": "33"|null, "items": ["item_…"]}],
   "links": {"<item_id>": {"plan_id": str|null, "event_id": str|null, "parts_available": [...], "maximum": "33"|null,
                           "last_pull": {"at", "actor", "counts"}|null}},
   "requests": [{"id", "kind", "created_at", "actor", "summary"}], "scored_items": ["item_…"]}
  ```
  `cell_counts` (`{"<item_id>": int}`) is present only on a section's View read with `cells=section` (opt-in; a section tab reads
  it so): `cells` then hold only that section's rows (every column key kept, possibly `{}`), and `cell_counts` is each column's
  term-wide count of visible cells, which the shared setup shows beside a column. Without it a section's View carries every
  section's cells, as before; the write routes always answer that way. `results.uncounted` ("Computation") is additive too.
  `setup.revision` is null before the first save. `students` are sorted by name, then section. `dr` lists the assignments a
  student has a current DR-center booking linked to. `assignments[].maximum` is what one paper of every available part is worth
  and `links[item].maximum` that of the column's own parts (both from `sources.maximum`, null when unknown): a suggestion for
  the column's points, never applied by itself. `links[item].grading_changed_at` (UTC ISO text or null, `sources.changed_at`)
  is the latest catalog timestamp of what the column pulls (FRQ decisions other than drafts, automatic acceptances and their
  suspensions; the bubble answer key, each present scan's latest complete sheet read and its reviews, a scan deleted in the Inbox counting nowhere; Exams-page finalizations and releases): a
  read-only hint shown as a dot on the Pull icon when it is newer than the last pull; it never pulls. `requests` are the latest 20 score writes, imports and pulls, newest first (a score write returns them too). `scored_items` are the columns with any score revision, cleared ones included: the ones a setup save cannot remove.
- `Cells`: `{"<item_id>": {"<row>": {"id", "status", "points": str|null, "note", "source", "actor", "at"}}}`; cleared cells are
  omitted from the view (but returned by a write that cleared them, with status `cleared`).
- `Results` (from `compute.compute`):
  ```json
  {"students": {"<row>": {"percent": "87.50"|null, "letter": "B"|null, "earned": "…", "possible": "…",
                          "categories": {"<cat_id>": {"percent", "earned", "possible", "dropped": ["item_…"]}}}},
   "items": {"<item_id>": {"scored", "excused", "missing", "empty", "mean", "median", "low", "high", "mean_percent"}},
   "categories": {"<cat_id>": {"mean_percent", "students"}},
   "overall": {"mean_percent", "students", "letters": {"A": 12}},
   "uncounted": ["item_…"]}
  ```
  `uncounted` only when a counted column has no category in weighted mode ("Computation").
- `Change`: `{"item_id", "row", "base": score id|null, "status": "scored"|"excused"|"missing"|"cleared", "points"?: str, "note"?: str}`
  (see "Typed scores"). A stale batch is a 409 with `{"error", "stale": [{"item_id", "row"}]}`.
- `Revision`: `{"id", "seq", "status", "points", "note", "source", "actor", "created_at", "request": {"kind", "summary"}}`.
- `ChangeEntry`: a setup revision `{"id", "type": "setup", "created_at", "actor", "revision", "changes": [str]}`, or a request
  `{"id", "type": "request", "kind", "origin", "created_at", "actor", "summary", "cells": {"here", "added", "changed", "cleared",
  "elsewhere"}, "items": [item_id], "detail": [WrittenCell]|null, "setup": {"revision", "changes": [str]}|null}` ("Changes").
- `WrittenCell`: `{"item_id", "row", "seq", "before": {"status", "points", "note"}|null, "after": {"status", "points", "note"}}`.
- `PullPlan`: `{"fingerprint", "item_id", "assignment_id", "plan_id", "parts", "points": column points, "rows": [PullRow],
  "counts": {"add", "update", "same", "kept", "waiting", "none"}, "warnings": [str]}` where `PullRow` is `{"row", "name",
  "section", "action", "proposed": {"status", "points"}|null, "current": Cell|null, "detail": {"earned", "possible", "parts": {…}},
  "reason": str}`.
- `ImportPlan`: `{"fingerprint", "rule", "format", "filename", "sha256", "revision", "ready", "options", "columns": [{"key",
  "title", "max_points", "mapping": {"action": "item"|"new"|"skip", "item_id"?, "title"?, "category_id"?, "points"?},
  "counts": {"add", "update", "same", "kept", "unreadable", "blank", "not_applicable"}}], "new_items": [{"column", "title",
  "category_id", "points"}], "changes": [{"column", "row", "name", "section", "action", "proposed", "current", "item_id"}],
  "unmatched": [{"line", "name", "reason"}], "unreadable": [{"column", "row", "name", "line", "raw"}], "counts": {the column
  counts summed, "unmatched", and "other_section"}, "warnings": [str]}`. `options` is `{"mapping": {column key: mapping}|null,
  "replace": [row], "blank_as_missing": bool, "section_id"?: str}`; `section_id` is present only when the import was started from
  a section tab (a whole-term import omits it, which keeps its fingerprints), and `other_section` counts the file rows of students
  in other sections, which that import skips (counted, never listed; 0 without `section_id`). A null mapping asks for the
  suggested one, and the plan's normalized `options` can be sent back with the apply.

## Browser (`static/gradebook*.js`)

Routes: `#course/<gid>/section/<n>` (the section's grid; `?item=<item_id>` focuses a column, `?assignment=<assignment_id>`
focuses its linked column or offers to add one), with `/setup` and `/changes` under it, and `#course/<gid>/home/gradebook` (the
section chooser; its `/setup` opens the term's setup from a section). The earlier `#gradebook`, `#gradebook?group=<group_id>[&section=<number>]`
and `#gradebook/<term_id>[/setup|/changes][?section=<section_id>&item=&assignment=&pull=]` addresses resolve and replace the hash
with these (`legacyPage`, `groupPage`); a bare `#gradebook` opens Current courses. The grid is a
spreadsheet: sticky header and name column, arrow keys (Ctrl to the edge), Home/End, Page Up/Down, Enter/F2 or typing to edit,
Enter/Tab to save and move, Esc to cancel, Delete to clear, paste of a block of cells, Ctrl+C to copy a cell, `EX`/`MI` for
excused/missing, Shift+F10 for a cell's history and Alt+Down for a column's options. Imports, pulls, a cell's history and a
column's settings open dialogs. Unsaved setup edits are browser memory only. Score writes go one at a time and are never
retried by the page; after an unknown outcome the batch is kept with its request ID, its cells are marked, and a notice's
**Check now** (only on the instructor's click) sends the SAME batch again, so the server answers with what it did; the same change
entered again reuses that request ID too. `?assignment=<aid>&pull=1` (the Exams hub's Recorded stage until the grades are
certified; a certified assessment's link has no `pull=1`) opens the pull PREVIEW of that
assignment's linked column once and drops `pull=1` from the address; without such a column the "Add a column for it" notice takes
the focus. Pulling stays the preview's explicit click.

Layout (UX overhaul, 2026-09-26):

- The shell's compact page header names the section's gradebook (`header.set`: `CODE · Section n` as the title, `CODE · Term ·
  Gradebook` as context, the trail Home / Courses / `CODE · Term` / Section n / Gradebook [/ Setup or Changes]) and holds the
  grid's actions: **Add column** (the primary only while the gradebook has no column and no graded assignment is offered to add;
  a secondary after that), **Import scores**, the Canvas action, **Import from iClicker** where iClicker is set up, and **Setup**.
  Export CSV, Refresh and a **Shortcuts** popover (the keyboard help) are quiet controls in the toolbar.
- A term with no live column shows ONE start panel, "No columns yet", with the three ways to begin: from Faculty's grading
  (assignments graded this term first, each **Add and pull** opening the link dialog, whose success offers Pull scores now;
  **Add all N as columns** is one setup save on the revision on screen with a request ID, previewed in a dialog and never
  pulling; ungraded assignments folded under "Plan ahead"), importing an export, or building it by hand in Setup. Once a column
  exists the same lists sit in the closed "Assignments without a column" disclosure with **Add linked column**.
- DIFFERS FROM GRADING (2026-10-07): after each View the grid draws (a load, a write, Refresh) and when the window comes back
  after 30 s, the page reads `…/grading-check` (one at a time; a failure leaves the marks as they were). Its answer marks in
  place, never redrawing the grid: each differing cell wears the needs-you tint and an orange rule (`is-differs`; its title says
  what grading has and how to bring it in), its heading an orange rule and the chip "N differ", the attention bar the chip "N
  grades differ from Grading" (a filter of those students), the key "Differs from Grading", and one warning notice lists each
  column with **Show** (its first differing cell in focus), and **Certify again** (Certification and Export) or **Pull** (its
  preview). A mark lasts only while the cell holds the revision the check read (`differences`), so a write never leaves a stale
  one.
- The grid's headings are one block: a linked column has a thin blue rule and a small Pull icon (same `data-pull` and name);
  "not counted" and "extra credit" join the meta line. A one-line key lists only the marks present. Dropped scores (the
  server's `results…categories[cat].dropped`) are dimmed and struck, with ", dropped" in the cell's accessible name. Colours
  have one meaning each: blue from grading, violet imported, warning missing, green over the maximum; notes, DR and ACC are
  neutral. A category band collapses to its percentage (view memory only; the column menu offers it from the keyboard).
- The grid is as tall as the window below its own top, or as tall as the window when it starts below half of it (`--gb-top`,
  set through the CSSOM), so once the page is scrolled to it the grid shows a screenful of rows. It is measured again whenever
  the page's height changes (a disclosure above it, the section's work list or TAs arriving after the grid drew).
- The setup editor and the dialogs are loaded through ui.js `loadModule` and warmed once when the workspace starts; a module
  that still cannot be fetched says "Faculty was updated since this tab opened. Reload to continue." with a Reload button.
- An attention strip under the toolbar counts the letters students have (each a filter), offers "Below" a letter, and counts
  the blanks past due (counted, not extra-credit columns whose due date has passed, where the column applies and the student has
  no value and no DR-center booking for its assignment); that filter also outlines those cells. Under `ungraded: "due"` the chip
  is "N empty cells counted as 0" and counts exactly the server's zeros (`results.blanks`), not the browser's date.
- Under `ungraded: "due"` an empty cell the results count as 0 shows a muted 0 beside the missing rule; one that waits shows MU
  (a makeup owed, or an absence waiting for a decision), IN (a paper in, not graded yet) or DR, its title saying why. The key
  lists "0 no score", "MU makeup" and "IN paper in" when present; the student's record says the same in words. One filter at a time, view memory
  only; rows stay put while scores are typed and follow the filter when it or the search changes.
- A student's name opens their record (Left from the first column reaches it): overall, letter and points, accommodations and DR
  bookings, the student's notes and absences, then **Grades**: one row per category (its percentage, how many columns and the
  dropped ones; columns without a category last, as Uncategorized), every category folded when the record opens (instructor,
  2026-10-06). A category's row opens its columns beneath it: each column's value, source, note and due date; each **Change** opens
  that cell's history (with "Back to <name>", and back to the record after a save, with the same categories open). A gradebook
  without categories opens its columns at once. A grade
  recorded from grading (a column linked to grading, the cell's source `grading`) has **Paper** in the record (its Actions column,
  beside Change) and **See the graded
  paper** in its history: Certification and Export of that assignment, which opens the student's drawer. The student goes in the
  tab's memory (`static/handoff.js`), never in the address or storage, and the gradebook still only reads grading. The record reads
  the View on screen and writes nothing; after an absence is saved from it, the View is read again before the record comes back (an
  excused absence fills iClicker cells, ICLICKER-CREDIT).
- A column's options offer **Mark N empty cells missing…** and **Excuse N empty cells…** for the rows shown now (the name search
  and attention filter apply; N leaves out DR-booked cells, named apart as "+k with a DR booking"): a
  confirmation lists what it writes and says when a search or filter hides rows, students with a DR-center booking for the column's assignment stay empty unless ticked, and
  the grid then sends ONE ordinary scores write (base revisions, request ID, 409 on a stale cell).
- A paste that replaces or clears values already there, or writes more than 50 cells, asks first and lists what it replaces;
  the cells are checked again before the one write. Smaller pastes into empty cells write at once.
- Setup's Columns table has **Find a column** above it once it holds more than five columns (and an **Uncategorized** filter while
  a column has no category): view memory only, it hides rows and never changes the draft, its order or what Save sends. A column
  edited while it is on stays in view until the find changes (a category chosen under **Uncategorized** keeps its row and the
  filter; pressing the filter again lets it go); a field it hides (a problem's link, a new column) clears it. A category select's
  title is the category's whole name.
- Setup's Columns table keeps title, category, points, due, counts, extra credit, archived, order and × removal visible; a row's
  **More** opens its sections, linked assignment and notes beneath it (view memory; it opens by itself when one of them needs
  fixing). Each panel says one line and keeps the rest under "How it counts"; the letter scale shows a preview of the letters
  today's percentages would get (the server decides on save). Panels are `.card`s. Unsaved setup edits survive navigation inside
  the workspace; closing or reloading the tab asks first (ui.js `registerLeaveGuard`, quiet on a hash change).
- The course Home's **Grades** row (GRADEBOOK GRADING SCHEME) shows the term's grading scheme in one line ("Standard · A 90% · …
  · F below 60%") from the term list, and its **Grading scheme** opens `gradebook-dialogs.js openGradingScheme` over the term's
  whole View (`gradebook.js openTermScheme`): each letter's range written as Canvas writes it (the top from 100%, the others from
  below the minimum of the letter above, "< 90% to 80%", the last down to 0%, which is not a field and is saved as 0), the presets
  (Standard A–F, A–F with +/−), Add a letter (above the last), × to remove, the letters today's percentages would get, and Save:
  one setup save of `{"scale": [...]}` alone on the revision on screen (409: reload, make the change again). The setup page's
  panel is the same scheme with the same ranges (`static/grading-scheme.js` holds the shared rules).

## Integration

- Grading (`#grading/<plan>`): a **Gradebook** link in the context row, and on each assignment card of the grading overview,
  opens `#gradebook/<term>?assignment=<assignment>` (the plan's `term_id` and `source_assignment_id`). Certifying a plan's grades
  (its sixth step) records them in a column here ("Certified plans" above).
- Course pages: a section tab is its gradebook ("Where it is"); the course Home's gradebook address lists the sections.
- Home: a Gradebook shortcut (no student data on Home).
- Calendar: a column's due date is a read-only deadline of its term (`workspace_calendar.source_events`, source `gradebook`,
  category `deadline`, shown as a Deadline from Gradebook), edited in the column's settings.
- Accommodations and DR exams: shown beside a student's name and explained in a pull's waiting reasons; never changed.
- Offering identity edits: `rosters.protect_roster_references` calls `service.protect_references`, which refuses a change that
  would detach a term's gradebook or a section's scores from its last visible offering.
- Checks: `test_gradebook`, `test_gradebook_compute`, `test_gradebook_imports` and the node `test_gradebook_changes.mjs` run in
  `manage_checks.py quick`;
  `test_gradebook_sources` (real graded workspaces) runs in the full suite (CHECKS.md).
