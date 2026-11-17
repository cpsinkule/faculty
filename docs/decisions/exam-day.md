## Exam-day packet

Rule ID: **EXAM-DAY**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-09-30): "Exam-day packet. One print per room: seat chart, room roster, DR-center list, how many
  copies of each version plus extras, proctor instructions, and which TA proctors where." `exam_day/` (service, api, migration
  `exam_day/001_plans`), `static/exam-day.js` (the plan page), `static/exam-day-packet.js` (the packet's markup),
  `static/exam-day.css`, the packet print in `static/seating-print.js` (print rules in `seating.css`), `tests/test_exam_day.py`,
  `tests/browser_exam_day.py`.
- OWNER: the COURSE CODE + TERM/YEAR level (`course_terms.id`) for one course-wide assignment (`assignments.id`), keyed by (term,
  assignment) because exam records are often made after the exam (never `exam_events.id`). `exam_day_plans` rows are immutable
  revisions of the whole plan (append-only, the highest revision counts; a trigger holds the assignment to the term's course):
  the proctor instructions (at most 8,000 characters; the built-in `DEFAULT_INSTRUCTIONS` until the first save) and each room's
  proctors keyed by the id of one of the term's seating charts of the assignment (checked in the save's transaction: another
  exam's chart is a 400, a removed chart a 409), at most 12 per room, each a name with an optional email (the TA contacts'
  plain `name@domain` rule) and phone. A save names `base_revision` (409 when stale) and a request ID (the same ID and plan
  returns it; another plan is a 409); an identical plan records nothing. Lock order as TA contacts: the page's read is
  catalog-only; a save takes the roster lifecycle lock, then ONE `BEGIN IMMEDIATE`. Catalog data: in the nightly catalog backup,
  not in the generic workspace export.
- The plan page `#exam-day/<assignment>/<term>` (not a sidebar entry; the Exams entry stays lit) lists the term's rooms of the
  assignment with their date, sections, seated, DR testers, the version counts of the chart's saved letters and the extras,
  and their proctors: picked from the TA lists of the room's own sections, then of the term's other sections an offering still
  lists (marked so), or typed in. Proctors keep the contact details they were saved with; a later TA-list edit changes no plan.
  "Start from" another plan's instructions (the same course, any term, not empty) and Reset to default only FILL the form, with
  Undo; nothing propagates between assessments or terms. One Save (the page's one primary); its request ID is reused only while
  the plan being saved is unchanged (a lost answer is resent with the same ID by the next Save); unsaved edits hold the shell's
  leave guard. Email proctors is a `mailto:` link (every proctor with an email once; rooms, times and proctors, then the
  instructions, which past about 1,800 characters are left to the packet): Faculty sends nothing.
- THE PACKET IS A READ: `GET /api/exam-day/charts/<chart>/packet` takes the roster lifecycle lock, reads the chart once and reuses
  `seating_roster.roster_snapshot` (Print room roster's own read and refusals: stale seating, room review, a missing UIN, a DR
  tester without a booking), and adds the copies of each version from the chart's saved letters (seated students without a
  letter counted apart), the chart's current print jobs per section (neither cancelled nor superseded; the latest job of a
  section counts and more than one current job is named, never added up; extras are copies printed for no student;
  `printing: null` when there are none: never guessed), the plan's instructions and this room's proctors, warnings,
  `chart_version` and one `fingerprint` over all of it. UINs appear only in its room roster, exactly as Print room roster
  shows them; the plan page names none.
- It prints from the room's seating chart page (the Print group's "Print exam-day packet", or `#seating/<chart>?packet=1` from
  the plan page, read once and dropped): a preview (cover, instructions, room roster with the DR list) and the seat chart's Fit
  or Tiles choice (names or versions), Print chart's own preparation. Print reads the packet again and prints only an unchanged
  packet whose chart is the one on the page; otherwise the page takes the newer chart and the preview asks for another click.
  The packet pages print in portrait on the named page `packet-portrait`, declared in the print module's adopted sheet AFTER
  the chart's own `@page` rule (Chromium lets a later unnamed `@page` rule override a named one), and the seat chart follows in
  its own orientation; everything is removed after printing and on navigation. Nothing is created: no print job, file,
  delivery or record.
- The Exams hub's Exam day stage (an exam record or an assessment's view) links the plan when the assessment has seating
  charts; the hub still stores nothing. For the assistant `exam_day.save` is `approve` (not one of the instructor's automatic
  categories).
