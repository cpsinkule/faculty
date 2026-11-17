# Accommodation imports

Open **Accommodations** in the sidebar and choose **Import accommodations**.
Section rosters link to this page, which owns both imports. Upload the disability
accommodation XLSX export and preview the matches; the term is a choice only when
more than one term is current. One file can include every current course and
section. The preview leads with the rows that can't be imported, grouped by
reason, then new rows, changed rows (previously → now) and unchanged rows folded.
**Import matching students** queues the import; the dialog reports completion and
links to its retained Inbox record. The matching policy sits in the dialog's
"How matching works". A DR export given to this importer (or the reverse) is named
as such, with a button that opens the right importer with the same file.
Request status (for example, Read or Emailed) is retained as metadata; it is not
treated as an accommodation being granted or revoked.

Both files are AIM instructor-portal exports. **Import from AIM** gets them
without leaving Faculty: it signs in behind the scenes on a private browser on the
server, with the Gradescope sign-in saved on the server (the same NetID and
password; `save-gradescope.py` updates it), or, when none is saved, with the NetID
and password you type into its form. When Duo asks, the
window shows the code to enter in Duo Mobile, with **Send the push again** and
**Use a Duo passcode instead** for a push that does not arrive. Faculty answers
"Stay signed in?" and Duo's "Is this your device?" with no, continues past AIM's
FERPA access-policy page for you (signing in is your acknowledgment, as the form
says), and exports accommodation requests for the term AIM shows (cancelled requests
left out) and every upcoming DR exam. One review then shows both previews with one
**Import both**; each part is the ordinary import with its own checks and Inbox
record, and if one export failed the other is still offered. If Faculty meets a
sign-in step it does not recognize it opens the page itself (**Show the page**),
where you click and type, and it carries on after that step; **Sign in on the page
instead** does the whole sign-in there. Your password is used once and never kept;
only your NetID is remembered in this browser. The files stay in the page's memory
(the **From AIM** notice reopens the review) and a reload forgets them. If AIM
changes its pages the window says which export it could not find; export it in AIM
yourself and use the upload buttons.

## The Accommodations page (2026-09-26)

The page is a view of the saved records; its writes are each student's DR quiz taker
checkbox (the same value as the box on their Ctrl-K student card; offered only to a
student with a granted accommodation or a DR booking, and kept, with "No accommodations
on record.", for one marked before) and the DR quizzes
tab's **Prescheduled in AIM**, **Undo** and **Retry**. A status line counts current
students with accommodations and upcoming DR bookings and names the next DR exam;
a freshness line says when each export was last imported (`last_imports` in the
overview: latest import per kind, Inbox-deleted submissions excluded), links each
Inbox record and counts rows that did not match. The current tab's import is the
primary action; the other stays beside it. Student accommodations are shown
inline as tags (wording verbatim), with coordinator, notes and source in a small
disclosure, and type chips with counts filter the table (the six most common and
the chosen one; "N more types" lists the rest). "Next: Test 2 · Thu, Oct
22" opens the student's DR bookings (found by name in memory; no name or UIN in
the address). The page refreshes quietly: a read that finds the same records
changes nothing, and a changed one is patched in place, keeping open disclosures,
focus and typed text.

The DR exam schedule opens on **Upcoming** (campus date strings compared, never
time-zone converted), with **Past** and **All**. Bookings are grouped by the
Faculty assignment they are linked to, in its course and term, with the DR
center's own exam name when it differs, the date span, start-time spread, links
to that assignment's seating charts (where these students are not seated) and its
Exams hub, and a warning for an unlinked booking. **Details** shows the booking's
versions with the changed fields named, the student's accommodations and the
import records; rows whose date or times changed are tagged Rescheduled
(`revision_count`, `rescheduled` on each booking, read-only). **Print DR list**
prints one exam group (name, UIN, section, date, DR-center time, length, campus,
accommodations) through an ephemeral view removed after printing. Filters live in
the address: `#accommodations?tab=exams&when=all&course=<code>&exam=<name>`, where
`exam` matches the DR center's exam name or the linked assignment's title; the
Calendar and the Exams hub link here that way.

## DR quizzes

The **DR quizzes** tab (`#accommodations?tab=quizzes`) lists, for each current
term, the class schedule's upcoming quizzes in every section with a DR quiz taker
(a student whose DR quiz taker box is ticked; a DR booking or Extra Time alone does
not count): date, time
(the class meeting's when the plan has none), the 20-minute base length, and
whether the imported DR bookings agree (its "Booked in AIM" column: "None yet",
the number of bookings, or "Differs · booked" and the day).

Once you have prescheduled a quiz in AIM yourself, choose **Prescheduled in AIM** on
its row. It first asks you to confirm, saying how many students it emails ("This
emails 3 students"); on **Send** the section's DR quiz takers are emailed within
seconds. **Undo** takes the mark back only until an email has gone out. Under the
mark each student's notice shows: **Told**, **Waiting to send**, **Skipped** with the
reason (no email address on the roster, dropped), **Failed**, or **Uncertain** (check
Gmail's Sent folder first: it may have gone). A failed or uncertain notice has
**Retry**, which after its own confirm sends one new email. If the quiz's date or
time changes after you mark it, the row says so and asks you to mark it again, which
sends an updated notice. Faculty writes to AIM only through Send to AIM, which you start
and confirm (SCHEDULING "SEND TO AIM"): it adds each ready quiz's date in AIM and
uploads its accessible copy. Marking a quiz Prescheduled in AIM, Undo and Retry write
nothing there.

## DR exam bookings

On the same page, choose **Import DR exam bookings** for the DR center's exam
registration export. Preview new, changed, unchanged and unmatched bookings, then
choose **Import matching bookings**. The completion names any assignment the
import created to link its bookings. The DR exam schedule can be filtered by term,
course, exam or student. Dates and times are displayed in the DR center's local
time as supplied, not converted to the browser's timezone. The roster also lists
each student's bookings. **Details** shows current and previous schedules
and links to their source Inbox imports. Past terms remain available in the term
filter even after courses are archived.

Bookings match exact UIN, canonical Subject + Course, section and normalized
Exam Type within the selected current term. A date/time change updates that same
booking ID. Distinct exams (Test 1, Test 2, Final) stay separate. Case and repeated
spaces in an exam name are normalized for matching; other renamed exams are new
keys. Conflicting rows for one booking reject the file rather than guessing which
schedule is current. Exam dates do not choose the course term. `Requested On`
records when the student requested a booking; it does not establish which export
is newer. Each accepted upload supplies the latest values for its matched rows.

The instructor confirmed that later exports **may be filtered**. Missing bookings
remain saved, including bookings for other students, exams and courses. Omission
never means cancellation. Importing a booking does not grant accommodations,
create an exam assignment, grade work, or send a message to the DR center.

Each imported booking is linked to one of the course's exam assignments in the
same transaction (`dr_exam_assignment_links`, see `exam_links.py`): the exam
name is matched to assignment titles with "Test" and "Exam" interchangeable, and
a booking with no match creates the assignment. Seating charts use these links
to leave DR-booked students unseated; the instructor can redirect a wrongly
matched exam's bookings from the seating chart dialogs.

`dr_exam_bookings` belongs to an existing section/enrollment membership, with a
unique exam key and immutable current-revision pointer. `dr_exam_revisions` keeps
schedule and source-field history, tied to `dr_exam_imports` and its retained
workspace Inbox original. `accommodations.exams.sync` applies the validated file
atomically through the Inbox worker. Preview changes prevent partial writes;
duplicate submissions or receipt retries reuse the original result. The catalog
and verified Inbox backups retain the records and workbook together. General
workspace JSON export excludes these student records.

The parser reuses the bounded XLSX reader and exact roster identity parsing. It
accepts Excel/ISO/US dates, Excel times or 12/24-hour clock text, a literal campus,
and duration in minutes. Formula/error cells are rejected. All source text remains
data and is escaped in the UI.

## Matching and updates

- Match the exact nine-digit UIN, canonical course code (Subject + Course), and
  section number to an existing roster membership in a current teaching term.
  Names, email addresses and CRNs do not override that match. Never create a
  student or assign new private IDs from an accommodation export.
- The export has no teaching-term column. With one current term, that term is
  selected initially. If the same course and section are current in several
  terms, select the intended term; otherwise those rows remain ambiguous.
- Each row applies to its listed course and section. A MATH 140 record does not
  silently become a MATH 151 record. A mixed file containing both courses routes
  each row to its corresponding roster. Archived sections are not import targets.
- Unmatched UINs, missing current sections, ambiguous terms and older updates are
  reported in the preview. No names are guessed. Import the missing roster first,
  correct the source, or choose a term and preview again as appropriate.
- Reimports update matching records. Students absent from the new file keep their
  accommodations. An accommodation column omitted from a subsequent file keeps
  its prior value; an included column with blank/No/False/0/N clears that flag.
  Other nonempty values are preserved verbatim, including extra details.
- Older `Last Updated` timestamps cannot overwrite newer records. Conflicting
  duplicate student/course/section rows reject the workbook. Identical duplicates
  are coalesced. No update replaces retained history.

## Ownership and processing

`student_accommodations` is owned by the permanent section and existing
`student_enrollments.id`, enforced by the corresponding `roster_memberships`
foreign key. It points to immutable `accommodation_revisions`. These structured
records live in the shared catalog and its verified nightly backups. Neither
`courses.id` nor a name is an accommodation owner. Roster payloads, private IDs,
enrollment prefixes and imported roster fields remain unchanged.

The original mixed-course workbook is retained once as an immutable workspace
Inbox artifact, with the import command, SHA-256, exact preview hash and completion
receipt. `accommodation_imports` binds each revision to that Inbox job and source
row. Originals and receipts use `InboxStorage` verification, staging, nightly
backup and restore rules; no direct NVMe/HDD payload access is introduced.

The private upload API submits `accommodations.sync` through `InboxService.submit`;
only the registered worker handler applies it. All course updates in one import
commit in a single catalog transaction while holding the roster lifecycle lock
before owner locks. The worker checks the preview again before writing. Changes
to affected mappings or accommodation revisions invalidate the preview. The
Inbox job ID is the idempotency key for effects: retry after an interrupted receipt
reuses the committed result, and resending the same HTTP submission returns the
original job. Interrupted workers require explicit retry.

This action has workspace scope because a workbook can span several courses.
It does not add a `scan.receive` handler or bypass email authentication, instructor
review, or consequential-action approval. Single-use course submission codes do
not support this action. The private roster and accommodation APIs require a
network connection to Faculty, with no login or browser-session cookie. Writes
enforce the existing same-origin request boundary.
The general workspace JSON export contains neither accommodations nor their source
workbooks. Coordinated backups retain both the catalog records and Inbox evidence.

## API and validation

- `GET /api/accommodations/options`: distinct current teaching terms.
- `GET /api/accommodations/overview`: accommodation records and DR bookings with
  explicit course/term/section context (`term_id` included), including retained
  past terms, and `last_imports` (`accommodations`/`dr_exams`: `job_id`,
  `imported_at`, `counts`, `not_imported`, or null).
- `POST /api/accommodations/preview`: multipart `file` plus JSON `term` (or `null`).
  Returns row outcomes, counts and an exact `preview_hash`; no import writes.
- `POST /api/accommodations/import`: same file/term plus `preview_hash` and a unique
  `idempotency_key`. Returns the Inbox job with HTTP 202. Changed previews return
  HTTP 409. Poll the existing private Inbox job endpoint for completion.
- Section roster responses add `accommodations` only to students with saved
  records and `accommodation_count` for students with active items. `dr_exams` and
  `dr_exam_count` expose saved DR bookings separately; each booking carries its
  linked `assignment`, `revision_count` and `rescheduled`.
- Accommodation previews add `previous_accommodations` to updated rows for display,
  after the preview hash is computed (the hash and the import are unchanged).
- `POST /api/accommodations/exams/preview` and `/exams/import`: the same multipart
  preview/import contract, for DR exam workbooks.
- `GET /api/accommodations/exams/<booking-id>/history`: retained schedule revisions
  and source-import links.

Accepts one XLSX worksheet up to 10 MiB, with bounded ZIP expansion, rows, columns
and cell text. Formula/error cells are rejected. Accommodation columns are derived
from the export headings; identity, course, contact, request, date and notes
columns remain metadata. All workbook text is treated as data and escaped in HTML.

Run `python -m unittest discover -s tests -p test_accommodations.py` and
`python tests/browser_accommodations.py`. Fixtures are synthetic. Checks include
mixed-course matching, term/section isolation, unchanged private identities,
history and missing-record retention, stale previews, atomic rollback, interrupted
receipts, duplicate HTTP submissions, verified source backups, private reads,
formula rejection, literal text rendering and the 1440 and 1280 px desktop widths.

DR checks: `python -m unittest discover -s tests -p test_dr_exams.py` and
`python tests/browser_dr_exams.py`. Fixtures are synthetic. Checks cover schedule
updates with unchanged request dates, fixed booking IDs, filtered omissions,
multiple exams/courses/terms, history, atomic failure, retries, duplicate uploads,
verified source backups, filters, roster links and the 1440 and 1280 px desktop widths.
