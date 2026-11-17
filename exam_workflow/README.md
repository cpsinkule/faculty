# Exams

Assignments → Collect lists student collection exceptions with retained paper and Inbox preview links. Its expectation form calls the existing participant route with the displayed row version; unsaved edits survive polling, and stale edits can be discarded and reloaded. No missing paper is automatically called absent or excused. Arrange an existing assessment is available in the unified Create assignment menu.

The primary entry is **Assignments**. Open an assignment's arrangements for its
sittings, collection, manual scores and requests. Existing exam links remain usable
inside the shared assignment shell; the lifecycle remains a read-only projection.

Open **Exams** in Faculty to manage an exam across its sections, main sitting,
makeup sittings, received paper, grading and student requests. Choose an existing
course assignment and permanent course term. One term/assignment pair has one
exam; add sittings for different dates rather than duplicating the exam.

## Ownership and storage

- `exam_events` and `exam_sittings` belong to `course_terms.id`. Sections and
  participants reference `course_sections.id` and permanent
  `(section_id, enrollment_id)` roster memberships.
- Assignments, immutable document versions and immutable rubric revisions are
  course-wide resources. Each term exam explicitly pins a rubric for each
  document version. Editing a rubric creates a revision; other terms keep their
  pinned revisions. Main and makeup paper may use different layouts together.
- Print batches retain section ownership. Scans and rasters retain their Inbox
  owners; the exam references them without copying files. Rooms remain workspace
  resources, and linked seating charts retain their existing term ownership.
- Marks, finalizations, feedback releases, requests, decisions and preparation
  checks are catalog records owned by the exam and its permanent memberships.
  Course-wide Response Card templates provide reusable wording; personalized
  issues remain term-owned.

The new SQL migrations run at app startup. They add catalog tables and require no
new storage directories or public service deployment. Existing verified catalog
backups include these records; generic workspace JSON exports omit student
requests and grading data. File reads go through the existing verified storage
managers under the roster lifecycle lock. Normal NVMe staging, nightly backups,
and verification before archival purge are unchanged.

## Plan and collect

1. Create the exam, select its sections, then add named main, makeup or DR
   sittings. Dates and times are local wall-clock values.
2. Assign students to sittings. Record absence or excuse explicitly with a
   reason. **Add new roster students** adds later imports without erasing saved
   decisions. Changing a sitting does not rewrite DR-center bookings.
3. Link the existing seating charts and print batches. A link must match the
   assignment, term and selected sections. Linking a chart does not automatically
   link all its print batches; choose the batches whose papers this exam collects.
   Explicit chart deletion keeps the original chart reference and an exam audit
   event while clearing its live link. Existing paper and scan identities survive.
4. Inspect arrivals in Teaching Inbox and review exact assemblies. A scanner
   message that requires instructor review must also pass the Inbox review step.
   A reviewed assembly alone does not clear that gate.
5. **Collection** combines linked arrivals by printed copy, then by student.
   Complete, partial, missing-page, unreviewed, no-scan, absent and excused states
   remain distinct. Unused unassigned extras do not count as missing students.
   Competing submissions or changed scan observations require an explicit
   assembly/submission choice. Superseded or unissued paper cannot satisfy
   collection. Links open the relevant Inbox job for review.

**Changes** compares roster membership, student plans, sittings, room/seat state,
DR revisions, paper and notices against the last preparation checkpoint. It shows
before/after values and affected students, cards and print batches. Recording a
checkpoint revalidates its exact fingerprint and retains the prior checks. Live
schedule conflicts, unusable seats and stale built-in notices remain visible
even after a checkpoint. This view reports effects; it does not silently reseat,
reprint, revoke or send anything.

## Grade reviewed work

In **Grading**, choose a document version and configure its questions, maximum
points, page regions, optional additive rubric criteria and reusable feedback.
Regions use page-relative coordinates between zero and one. A question can span
multiple pages or separate areas of the same page. The browser clips the retained
rasters; the source scan stays intact. Select another document version to configure
a makeup layout independently.

**Manage grading templates** accepts blank PDF, DOCX, or LaTeX paper and provides
page selection and drawn answer boxes. Saving its map creates a course-wide
template revision; **Apply saved mapping** explicitly pins it to this exam's
selected printed version. Assignment and page count must match. Later template
edits do not change earlier rubrics or another term's grades. The exported
reference package includes originals, normalized PDF, 300-DPI pages, text and
checksummed JSON regions. See [grading templates](../ASSIGNMENT_TEMPLATES.md).

Choose a question and student, inspect the image, then save the mark. Each mark
revision records the exact rubric and reviewed assembly/hash. Prior scores and
comments remain in history. With additive criteria, the score must equal the
selected criteria's points. Missing question pages require an explicit
acknowledgment before recording a mark.

**Hide roster names** hides names in grading metadata; handwriting, printed names
or other details in the scan may still identify a student. This is not image
redaction or full anonymization.

**Finalize** requires current marks for every question. **Release feedback**
creates an immutable snapshot eligible for CSV export and feedback cards; it
does not publish or email it. **Reopen** requires a reason and invalidates the
current release. A changed rubric, reviewed assembly, paper assignment or source
review gate prevents stale marks/releases from being used. Previous history is
retained. CSV exports contain current released results only and escape
spreadsheet formula prefixes.

**Prepare feedback card** uses one student's current release, with server-resolved
scores and comments. Review it in Response Cards, then explicitly publish and
send using the existing actions. Publication and email delivery revalidate the
release; an intervening correction blocks further distribution. An outdated card
remains visible in instructor history, with its copy link disabled and a reason
shown. Information already published or delivered is an immutable historical snapshot: reopen does
not retract a message already received. Use the existing card revoke action
when a published link should stop accepting responses, then prepare a corrected
release and notice.

## Review requests

**Prepare request cards** creates local personalized request forms for the exam's
participants. It makes no external calls. Publish and send them through Response
Cards when ready. Syncing their responses creates open requests, once per
immutable submission. The instructor may also record a request directly.

Record an explicit approval or decline and explanation. Approved seat requests
can move the student to an empty usable seat in a linked chart. Makeup and
scheduling requests can assign a sitting of the same exam. **Preview change**
shows the exact proposed effect and notices/paper needing review; **Apply**
revalidates the decision, participant, sitting or room/chart versions. The effect
and immutable receipt commit together. Retrying the same preview cannot apply
the effect twice. A request cannot silently move another student, bypass a DR
booking exclusion or change a different exam.

Decision cards are prepared from the saved decision and its exact version.
Student text cannot select an executable action. Other requests may be resolved
with an explanation without a seat/sitting mutation. Once a decision has been
applied, further changes require a new request so its receipt remains intact.

## Interfaces and verification

Private routes are under `/api/exams`; the public email listener does not expose
them. The existing same-origin JSON write checks apply, without an application
login or browser-session prerequisite. Response Card students use the existing
separate public Worker. New variables are resolved in Faculty before publication;
the Worker receives its existing supported schema. No Canvas integration,
automatic grading, automatic absence inference, or gradebook push is introduced.

Run the focused catalog/source tests with:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_exam_*.py' -v
```

`tests/browser_exams.py` exercises the real private APIs through the browser,
including grading/release, requests, document links, the narrowest desktop window
(1280 px) and unsaved edits. Its retained scan fixtures are synthetic: these tests
do not replace the document layout's physical printer/scanner qualification.
Existing seating tests also exercise the bounded Bubblewrap PDF renderer and need
host namespace access.

## Assessment lifecycle hub (read-only)

`exam_workflow/overview.py` projects one assessment — a course-wide assignment in one term, keyed by
`(assignment_id, term_id)` because exam events are often created after the exam — through eight stages: Paper,
Seating, Notices, Print, Exam day, Collected, Grade and Return (keys `grading` and `recorded`; the labels are the assignment's
tab words since 2026-10-18, so "the Grading stage" and "Recorded" below mean those two). The step graph and the next step carry
`data-exam-lifecycle` and the record's tab row `data-exam-subtabs`: the assignment shell hides the first and restyles the second
(I6). Each stage names its owner's page; the projection
stores, decides, sends and clears nothing, names no student, and reads scans only through `PRESENT`. Paper, Seating and
Print use the shared rule `assignment_stages` (UX amendment 15; registration `unlabeled:` jobs, superseded and cancelled
batches never count). Grading reads the Grading page's own `grading_entry`/`workflow_summary` under the lifecycle lock.
A pre-exam readiness checklist is returned only while the exam is ahead. The exam is ahead until its last date passes; papers
scanned earlier (DR or makeup papers before the main sitting) end it only when no main date is still ahead, and Exam day is
done only once its dates have passed (with no date recorded, once papers are scanned). Papers count as written once a
scan is linked to the grading plan (waiting for its scan checks or cleared) or grades are retained; a plan set up by a quiz
draft's approval, or by the instructor's Set up grading now (2026-10-02), is on Grading before any paper exists and never ends
the preparation by itself (its Grading stage reads "Rubrics ready · waiting for the papers", "Set up from the approved quiz ·
finish the rubrics" or "Set up before the papers · finish the rubrics"). A printed assessment with
no main date and nothing scanned asks to "Upload the scans once it is given", even when linked DR bookings have started
Exam day; one whose main date is ahead and that is ready has no step ("Ready for the day"). Every scan step opens the Inbox's upload window (`#inbox?upload=1`). Without a grading plan the Grading stage
reads "Not set up yet · set it up now, or associate a scan in the Teaching Inbox" (the two ways one is made); its tile
offers Set up grading now (POST
/api/grading/assignments with the exam record, or the assignment and term, which makes the record), and its link still leads
to the Teaching Inbox, where associating a scan makes one. Print links to the assignment's existing print jobs
(`#documents?jobs=<assignment>`, with their copies and "one stack" for a term job's interleaved versions) and to the
creation step only while there are none. Paper names the versions whose current jobs are ready when earlier layouts stay
registered beside them (a rebuilt quiz: "4 versions printed · 8 registered", as its Assignments card names A2–D2; 2026-10-02).
Without a gradebook column Recorded opens the gradebook on the assignment (`?assignment=`), which focuses the column that links
it or offers to add one. Recording is the next step only as a `soft` suggestion while no (unarchived)
gradebook column references the assessment; the Exams list folds such past exams under Earlier exams. Once the grades are
certified (certifying records them), Recorded opens the column without the pull preview and the step is "Settle the students
without a grade" (Collection when papers were never scanned, else the gradebook), never "Pull" again. An assessment without
an exam record lists the sections of its seating charts, its section jobs and its term jobs (`print_batch_sections`).

Routes: `GET /api/exams/<event_id>/overview`, `GET /api/exams/lifecycle?assignment_id=&term_id=`, and `GET /api/exams`
(each exam's lifecycle under `summary`, plus current-term `assessments` with charts or print jobs but no exam event;
`?summary=0` for the light list). `GET /api/exams/<id>` adds `grading_plan_id` and `request_counts.open`, and sends each
collection record once (`reconciliation.students`, no longer also under `participants[].collection`).
`GET /api/exams/<id>/requests` adds `waiting`: synced request-card answers the next import would add.
The Notices stage counts an email as not delivered by Response Cards' own rule (`response_cards.service.delivery_problem`:
an unopened, unanswered, unreplaced card whose invitation failed or was not confirmed), so the hub, the Response Cards
attention strip and its issue filter agree. Unsaved manual rubric or score edits are protected by the shell's leave
guard (ui.js `registerLeaveGuard`, same question as the page's own tab switches).

The Exam day stage links the assessment's exam-day plan, `#exam-day/<assignment>/<term>`, whenever it has seating charts
(AGENTS.md "Exam-day packet"): proctors and instructions kept by `exam_day/` at the term level for the assignment, never on the
exam record, and one printed packet per room from its seating chart. The hub only links there.
