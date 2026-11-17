## Printed document identity

2026-10-09 assignment flow: Create assignment first offers paper/printing, model-assisted quiz drafting, or arranging an existing assessment. The permanent key is generated, with an Advanced override. Seating is omitted unless requested or already attached. Leaving Details without seating performs the same explicit grading setup handoff as leaving Seating. Paper PDF and Labels are separate actions; the remembered label stock is directly available. GET `/api/documents/versions/<version_id>/preview` reads the immutable course-definition-owned canonical through the storage manager and digest verification, without allocating copies or stamps. Its viewer identifies it as unstamped. Existing duplicate-stamp warnings remain binding.

Rule ID: **DOCUMENTS**. Current binding module contract. Read root `AGENTS.md` first.

2026-10-09 assistant materials: a reviewed material bridge may supply generated LaTeX or a matching course-wide retained artifact
to the existing version compiler. It creates a new course-wide version through the same sandbox and registration route; it
neither replaces a version nor prints, sends or changes grading by itself. Workspace receipts prevent repeating an uncertain import.
ASSIGNMENT-WORKSPACE (requirement, 2026-10-03): the primary entry is now Assignments.
The existing creation controls and print-job dialogs also mount inside an assignment's
Create area. An explicitly selected term is retained throughout continuing an assignment.
Leaving Seating creates or reuses its term grading plan; Paper's Next prepares missing
templates from registered versions through the grading service. Failed setup/imports
remain visible with explicit retry, keep completed work, and never block printing.
Done and Finish later return to `#assignments/<assignment>/<term>`; Prepare questions
opens that assignment's shared question preparation. These rules supersede the old
standalone-menu entry and optional-only grading handoff below.
One-time links still open their window once (2026-10-03 fix): `#assignments?create=1` and
`?quiz=new` (Home, the palette, old `#documents?…` links) drop the query before opening, so
Back, a reload or a refresh never reopens it. Old paper/print links (`?assignment`, `?jobs`,
`?batch`, `?chart`) keep their address and open their window or job once per arrival, after
the first successful read; a failed read says so with Try again, and leaving the page then
following the link again opens it again. Closing the creation window over the Assignments
list reads the list again. The Create area's Put on Canvas… names where the assignment
stands, as its card does. The print library (cards, Print jobs) remains at
`#documents?definition=<course>`, linked from a course's page.

A new print job for a section its term's exam record lacks extends that record to the section (EXAMS, 2026-10-03);
`batch_allocated` then names `exam_sections_added`. Registrations for unlabeled scans never extend it.
Measurements, incident notes and background quotations moved on 2026-10-01 to
`docs/history/documents-provenance-2026-10-01.md` (non-binding).

- `document_identity` owns course-wide assignments and immutable canonical versions,
  section or term print batches, printed copies, and page stamps. Optional student links
  reference existing section roster memberships. Never attach these to `courses.id`.
- TERM PRINT JOBS (migration 013; requirement, 2026-10-01: "If an assignment is printed
  this way with no seating chart, it shouldn't assign sections to the barcode. I intend
  to mass print these out and not separate stacks for each section, so I can't guarantee
  that a given section gets the right copies."). Every print without a seating chart
  (Create assignment's sections without a chart, a drafted quiz's approval) is ONE
  job per version owned by the term: `print_batches.term_id` set, `section_id` NULL, its
  files in the term's verified storage (`DocumentFiles.files("term", ...)`, checked by
  `rosters.TermStorage.check_source`), its lock the term's. It lists the sections it is
  for in `print_batch_sections` (references it owns, written once when it is made, part
  of its request hash); they scope name matching and the scan's grading link, never a
  copy's identity. Its stamps resolve to course, term, assignment, version, copy and
  page with no section. A copy may be named for a student of any section of the term
  (`copy_student_*` triggers); it is then that student's paper in their own roster
  section: collection, grading (`_source`), Gradescope and the gradebook match its copy
  by enrollment (`section_id` NULL or the participant's), never by a section the paper
  never had. Seated jobs, jobs printed before, and registration jobs of exams printed
  without labels keep their section. THE CHOICES STAY WITH THEIR COPIES (instructor,
  2026-10-11): in Create assignment's "Choose students copy by copy", changing the number
  of copies keeps each student already chosen with their copy; a copy added starts
  unassigned and one taken away takes its choice with it, also when the count changes
  with the list folded (a third copy cleared the two already assigned; browser only, the
  job's request is unchanged). WHAT WAS TYPED ON PAPER STAYS (requirement, 2026-10-11,
  "#2: ask first"): the new-version rows of Create assignment's Paper step (labels typed,
  files chosen, rows added) are kept while the window shows another step and put back,
  under "Faculty prints it from LaTeX" when that was the choice, when Paper is shown
  again; Back and the step line lost them. Rows a Next registered are no draft. A version
  typed and not added is not saved with the assignment, so Finish later ASKS FIRST, as
  leaving the page does ("Finish later without adding Version B? What you typed for it is
  not saved. <title> itself is saved."): declined, the window stays as it is; accepted,
  its notice says "<title> is saved. Version B was not added." It never registers the
  version itself (a registered version is permanent), and asks nothing when no version
  is typed or the typed one was set aside for another choice. Leaving the page asks for
  a kept, unfinished version too. Browser only. ONE STACK, EVERY VERSION (requirement, 2026-10-01:
  "I don't want versions per section, I want each section to get 4 versions so the
  students cant cheat off each other"): the term jobs of one request and assignment
  in several versions (a drafted quiz's approval, or Create assignment's term jobs
  of several versions created together) are one group on the Print page,
  whose Print PDF (GET `/api/documents/stack?batches=...`, `section_stack`) cuts the
  ready, current jobs into one file with the versions interleaved copy by copy (A1,
  B1, C1, D1, A2 ...); a version job's own window offers it first, with that version's
  own file and exports folded under "Version X only". Print more copies… on such a job
  may print extra unassigned copies of EVERY version as a new stack (one term job per
  version, request `<request>:<version id>`, so they group and interleave the same way);
  its other choices print that version only. Superseding one version ends the stack file
  (its window says so first); from then on a still-current version's window prints its
  own file and an ended one's says it prints nothing more. Like the room file
  it only re-orders verified pages: nothing is issued, and each version's file stays
  the record (under Other formats). Every job records its term (`term_id`, the
  section's for a section job); reads that need a job's term use it, not a join through
  its section. The table was rebuilt to drop the NOT NULL: `catalog_migrations`
  `migration_transaction(rebuild=...)` suspends foreign keys for that one transaction
  and checks every reference before commit; rowids are kept.
- `T1-` tokens encode 128 independent random bits in canonical Crockford Base32;
  `A1-` aliases are independent recovery keys. Neither is an authentication token,
  student identifier, action instruction, or evidence of authorship.
- Allocate page mappings before rendering. Failed-job retries retain their tokens;
  additional paper requires a new batch. Ready batches cannot be cancelled; an
  explicit supersede retains and marks their provenance. Never recycle tokens.
- Layout contracts are retained on immutable assignment versions, section print
  batches and page placements. `faculty-page-v1` keeps its original one-mark
  contract. `faculty-corners-10mm-v1` repeats the same page token at top-right and
  bottom-left at 10 mm. Publication must validate both physical marks; scan intake
  deduplicates the identity but retains distinct detected locations. A new layout
  does not inherit the old layout's physical qualification. TWO CORNER CODES BY
  DEFAULT (requirement, 2026-10-01, asking whether the 20 mm codes had to be so big:
  "Use the two corner 10mm"): the `facultyassignment` class (v2) lays out
  `faculty-corners-10mm-v1` unless a source asks for `pagecode`, so every version
  Faculty compiles from then on (drafted quizzes, the wizard's LaTeX) carries two
  10 mm codes with 30 mm top and bottom margins; its qualification is the corner
  test's (30/30 pages, 52/60 marks, `corner-test.md`). Versions, batches and stamps
  issued before keep their layout. Stamps-only label versions keep `faculty-page-v1`.
  SEVEN-MILLIMETRE CORNER CODES (requirement, 2026-10-18: "I want all stamping to be this size matrix now, for standalone
  labels, though, keep them their current size"; of the printed alias: "get rid of it"): the class (v3) lays out
  `faculty-corners-7mm-v1` unless a source asks for `compactcorners` (the 10 mm corners) or `pagecode`. The same token is
  packed as raw bytes (`0x01` + its 16 bytes, encoding `bytes-v1`) into a 20x20 symbol drawn 7 mm with its 1-module quiet zone,
  centred in an 8 mm box 10 mm from the top-right and bottom-left edges; no recovery alias is printed (one is still allocated);
  page text starts 20 mm from the top and bottom. Readers turn a packed mark back into its `T1-` token
  (`media.teaching_payload`), so scans, validation and recovery see one token form. Publication checks this layout at 300 DPI
  (its quiet zone is 2.5 pixels at 200). Its qualification is the printed size test of 2026-10-18 (22/22 packed marks at 7 mm on
  both home scanners, where the 24x24 text code at 7 mm read 18/22 and 13/22). Peel-off labels of such paper keep the 10 mm
  corner mark (`label_profile`); printed-alias OCR skips scans of this paper and labels only.
  SECOND LOOK (requirement, 2026-10-25: "it has a label though, why wasn't the data matrix read?"): scans are read from a 300 DPI
  drawing (`media.READ_DPI`); a page that is not blank and shows no `T1-` mark there is read once more from that drawing at
  250 DPI (`SECOND_DPI`), and its marks are kept only when that finds one. Scantron labels printed against the sticker's top edge
  lose their quiet zone: of four Exam 2 sheets read at 300 DPI with no label, three read at 250 with the label of the student the
  instructor chose by hand; 298 booklet pages without a mark found nothing new. About 0.1 s more for each unmarked page.
  FAINT MARKS (requirement, 2026-11-06, Quiz 7: "I made copies of some that I printed out"; "yes proceed" to Faculty reading them): a
  page still showing no `T1-` mark is read once more from its 300 DPI drawing made black and white at 100, then at 120
  (`media.FAINT_THRESHOLDS`, `media.faint_look`), and those marks are kept only when one is found (the decoder records `threshold`).
  Stamped quizzes photocopied for the class came back with codes too faint to read: all 139 such pages of the scan read this way
  (127 at 100, 12 at 120), every one a stamp of the four copies photocopied. About 0.14 s more for each unmarked page. A scan
  inspected before this is read again only on request: `manage_inbox.py faint-marks MESSAGE_ID` lists its pages with no code, no
  identity, in no grouped paper, set aside by nobody and read by no first-page search whose code reads now (a dry run), and
  `--write` gives each that code's identity as a recovery does (status `manual`, the intake rule's review state; the original
  observation stays as read) with one Inbox event `faint_marks_read` naming each page and what was read
  (`ScanService.read_faint_marks`). The worker carries on from there (QUIZZES "PHOTOCOPIED STAMPS").
  GREY PAPER (requirement, 2026-11-11, of the Take Home Assignment: "the scan inbox seems to be having issues with this one, likely
  because I scanned double sided when the assignment was printed as single sided so there are lots of blank pages"; "this was
  actually printed on color paper, that's why it showed up as grey"): a side is blank when under .0001 of it is darker than 230,
  which no side of coloured paper can be, nor any side the Epson scans in grey: it draws the take-home's coloured paper at 155 to
  180 and white paper at about 210, and none of the 2,872 sides without a label it had scanned before was ever blank. So every
  empty back of the take-home was a written page with no label, each of its four scans asked for 192 to 276
  pages to be set aside one by one, and Certify stayed blocked on them (`inbox_pending`, "4 scans waiting for their scan
  checks"). Where the paper itself is darker than
  230 (the median inside 4% margins), a side is blank when under .0001 of what lies inside those margins (the scanner's shadow of
  the paper's edge is no ink) is more than 25 levels darker than the paper around it, the mean of the 41 pixels square about each
  pixel, so a crease or uneven light is no ink (`media.blank_side`, `GREY_INK`): as far below the paper as 230 is below white.
  White paper is judged exactly as before. Measured on every page kept that day (18,322): the white test names the same 293 blank
  sides; of 9,932 grey pages (4 a copier's, the rest the Epson's) the 6,381 with a label or an identity have .0042 of the side and
  more that dark, 42 times the cut; of the take-home's 864 sides without a label 763 are blank. The 101 left are not: 3 are one
  paper whose codes did not read, 45 carry writing or ink soaked through (.004 and more), and the rest show the front's writing
  through or a few written letters ("IDK", .0003). A blank side is read for a code once and gets neither the SECOND LOOK nor
  FAINT MARKS. A scan inspected before this is measured again only on request: `manage_inbox.py blank-sides MESSAGE_ID` lists its
  sides with no code, no identity, in no grouped or reviewed paper, set aside by nobody and read by no first-page search that are
  blank now (a dry run), and `--write` makes each a blank side as intake does (status `blank`, review state `ignored`; its picture
  and what was read on it stay) with one Inbox event `blank_sides_read` naming them (`ScanService.read_blank_sides`). The cut
  stays (2026-11-14): copy 60's two empty backs measured .00018 and .00011, the front showing through and the edge of a stain,
  close to a few written letters (.0003); inside one copy's paper such a back is set aside as that paper's (A PAGE BETWEEN ITS
  OWN LABELS).
  NOTHING STAMPED ON THE PAGE NAMES THE VERSION (requirement, 2026-10-01; QUIZZES "NO
  VERSION ON THE PAGE"): `create_batch` refuses a caption that names one
  (`quiz_latex.unversioned`) and a drafted quiz's jobs carry no caption; seat captions
  (seat, name, section, room) never did. Label-maker labels keep their version line, and a
  seat label names its student's version (SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION).
- The unstamped canonical and source manifest remain immutable. The versioned
  `facultyassignment` class and PDF stamper share the physical layout contract.
  Native PDF processing and source compilation require the bounded Bubblewrap
  environment; do not introduce an unisolated fallback.
- THE COMPILER'S PACKAGES (requirement, 2026-10-25: "delete and fix the pdf step"). The Assistant's 5.1 clicker questions used
  `enumitem` and failed to compile, leaving an assignment with no paper. The toolchain is TeX Live's base, latex-recommended,
  pictures (TikZ) and latex-extra (`enumitem` and the other common LaTeX packages): `.tools/texlive` holds the extracted Ubuntu
  packages kept in `.tools/texlive-debs`, and the system install and Dockerfile name the same four.
- `scan_pages` references existing Inbox messages/artifacts. Non-executing page
  inspection never clears the instructor review gate or dispatches an action.
- SCANTRON LABELS (requirement, 2026-10-15: "I need 2 labels per exam ... one is going on the scantron and one on the front
  page of the booklet itself"). The stamps-only count is LABELED PAGES, each label belonging to one page from the front (entering
  2 for a booklet and a Scantron made the Scantron page 2 of the booklet). So a job may instead give every copy one more label for
  its student's bubble sheet (`create_batch(sheet_labels=True)`, the seating exports' "Also print a Scantron label", migration 015
  `sheet_labels`, owned by the printed copy). It is printed with the peel-off labels only (`label_manifest`: after each copy's
  pages, "copy N · Scantron"), never stamped on a page, and the print manifest, its hash and older jobs' labels are unchanged.
  Scan intake keeps a page carrying one `unreadable` with the payload in `candidates_json` (`ScanService.classify`), so papers,
  sequence grouping and bubble detection see exactly what an unlabeled Scantron gave them; a bubble sheet read takes its student
  from it (BUBBLE-SHEETS "SCANTRON LABELS"). Their own print button (requirement, 2026-10-18: "I want the bubble sheet labels to
  have their own print button in the seating chart pages"): a seating export whose jobs carry Scantron labels shows "Room page
  labels" (`room_export` kind `page-labels`, the page stamps alone) and "Scantron labels" (kind `scantron-labels`, the bubble
  sheet labels alone, in seat order) beside the room PDF; the combined room labels stay in the menu as "Page and Scantron labels together" (instructor,
  2026-10-18: "I still want to be able to print the exam label and scantron labels together"). Both are cut from the same manifests and files; the
  combined `labels` kinds and each job's own labels file stay byte-identical.
- SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION (requirement, 2026-10-25: "why did the labels print without the versions on
  them, also why did they print with every section instead of that particular student's section"). Exam 2's room labels on Avery
  sheets read "Exam 2 · Sections 507, 508, 514, 515" (`room_export` laid out one header for the room and the seat label printed it)
  and no label of a seated copy named a version (the stamps-only job's own version says nothing; the seating chart gives the seat's
  letter). Each peel-off label of a seated copy now prints its own job's section and, where the chart gave the student's seat a
  version (`seating_exam_versions`, read as grading reads it), "Seat B4 · Version B" on its first line, or "Version B · Exam 2 · ..."
  when a long table seat leaves no room beside it (`DocumentService.label_text`, `media.label_lines`). This is label text only:
  `label_manifest`, the tokens and the stamped pages are unchanged, and nothing printed on the page names the version. Labels
  rendered before stay as kept (`print_batch_labels`), so a job's label-maker file printed before 2026-10-25 comes back unchanged.
- The instructor first graded on Gradescope and kept Faculty for records and
  verification; Faculty now grades too (grading.md). Written exams often cannot be stamped before printing, so one
  stamps-only label (usually exported for ONE page) goes on the first page.
  `document_identity/sequence.py` groups such scans from the label-to-label
  distance. The instructor asked that an even cadence be handled by default:
  after inspection, `auto_group` applies the plan itself only when some pages
  carry no label, no run was ever made for the scan, every label-to-label gap
  in every attachment is identical, and the plan at that distance has no exam
  to review and no page outside a labeled exam. Anything less even waits for
  an explicit request from the scan's Papers stage with the sides per exam (ONE WAY TO GROUP). An
  automatic run is an ordinary run (`actor` automatic, same undo, review gate
  untouched) and is never repeated once undone. Do not loosen those conditions.
  When labels are recovered later, the worker's scan discovery revisits this same
  rule after pending label OCR has finished. Its versioned discovery fingerprint
  also catches scans checked before this handoff existed. Grouping and association
  run under the execution and lifecycle locks; only success acknowledges the check.
  An automatic run never numbers `every_page` paper from its position
  (`identify=False`), and so ignores how many sides are written there.
  Paper labeled on its FIRST PAGE ONLY is different by the instructor's decision
  of 2026-09-19: a stamps-only version, or a job the instructor confirmed as
  `first_page`. Every such exam that adds up gives each written side its
  POSITION as its page (side n is page n; a blank back keeps its place and takes
  no identity), in automatic and instructor runs alike, and again for exams
  already grouped when the instructor confirms `first_page`
  (`identify_grouped`). The rule is the CADENCE, not the label: "never infer
  pages from a first-page label" means that when the label-to-label distance
  or an exam's side count does not add up, nothing behind that label may be
  inferred. It does not mean an exam that adds up keeps its pages anonymous.
  The page 1 label anchors an exam and the side count is its checksum: pages
  join only when the next label or the end of the attachment arrives after
  exactly that many sides, or after a whole number of further exams (an unread
  label, listed for alias recovery). Never guess for a short exam, stray extra sides, a label that is not
  page 1, non-current paper, a stamp already observed with other content, pages
  an earlier run grouped differently, or a count of written sides unlike most
  exams; report them and leave the pages alone.
  GROUPING A HELD PAPER BY HAND (requirement, 2026-10-02: "for a paper whose sides don't add up (a student added a sheet). You
  tick its sides in order and group it. Today only approving a partial paper or rescanning helps"): grouping a held paper is an
  explicit instructor choice of its sides, never inferred. On Papers a held paper whose page 1 label of current paper
  was read offers **Choose its sides…** (`papers_to_check` `choose`: the fingerprint of its sides as shown): every side from its
  label to the next, in scan order, with a tick, the first page ticked for good, and **Group as Copy N** with a reason. POST
  `/api/documents/messages/<id>/papers/held` (`SequenceService.group_held`: `first_scan_page_id`, `scan_page_ids` in order,
  `fingerprint`, `reason`, `request_id`) checks under the lifecycle lock and every affected owner's lock that the paper is still
  held with exactly those sides (409 otherwise), and makes one run (`origin` `held_paper`, its `sides` the count the paper was held
  at; like every run, an audit record of the source Inbox message) with one packet: the n-th ticked side becomes page n of that copy, with the intake's duplicate/changed-content rules (a
  stamps-only copy's other identities reserved as for any run); a blank ticked side keeps its place and takes no identity (a
  duplex back past the pages issued, as in any plan), a side labeled with its own page must be that page, no written side past
  the pages the copy was issued, and never a side set aside. Unticked sides
  stay as scanned, retained and unidentified, listed with the paper under **Grouped by hand** where they can be set aside
  (SCANNER-INTAKE). A `held_paper_grouped` Inbox event names the actor, the copy, the pages and the reason; the same request ID
  returns what it made. Undo is the run's ordinary Undo until the paper is a reviewed paper: then it is refused (409) and the
  paper is changed on Review, and Papers says so instead of offering Undo. A copy that already has a reviewed paper
  (say, approved as partial) is not grouped by hand, nor is a held paper on a scan deleted from the Inbox. No plan groups,
  numbers or holds such a paper again until it is undone, and its packet is no side count for `identify_grouped`; the Inbox
  review (or, after it, the worker's catch-up) assembles it like any first-page paper. It is no grouping of the scan:
  `auto_group` still groups the papers that add up once the one held paper is grouped by hand (its run is not a prior grouping,
  its label-to-label distance is left out of the cadence, and it counts as settled), and Papers counts it apart ("2
  papers grouped, and 1 by hand"). A page set aside is never given a place in a paper: a paper that would take one is held, and pages in no
  paper made only of pages set aside (and blank sides) are no stretch at all, so they neither hold automatic grouping back nor
  count as "more" for `_guard`.
  SHEETS SCANNED BACK SIDE FIRST (requirement, 2026-09-24). The count alone is not
  enough: two such sheets in a row, or a whole stack fed that way, shift the papers between by one side and each still adds up
  with the next student's page. So `SequenceService._guard` holds, in every plan, a paper that adds up between one with more
  sides and one with fewer (either order, nothing else between; pages before the first label count as more). And
  `SequenceService._sheets` reads a two-sided scan as sheets: in an attachment with an even page count where no sheet
  (sides 1-2, 3-4 ...) holds two first pages and at most a third of the first pages are on a back, both faces of a sheet
  belong to one paper. A first page on the back of its sheet was scanned back first: on one-sheet papers its front becomes that
  paper's page 2, but ONLY in the instructor's own preview/apply (`turn=True`, the grouping API; Papers's Group button)
  or where an earlier run grouped it so. EXCEPT when every chunk passes (requirement, 2026-09-24; `SequenceService._chunked`): every `sides` pages of an attachment from its first page
  hold exactly one first page, on the chunk's first side or, on one-sheet papers, its second with nothing else identified on its
  first. A first page found on paper printed without labels counts whether or not its student is settled (`found_first`; not
  after the instructor said it is not a first page). Then every plan turns the sheets, automatic ones included (`auto_group` also takes that distance when the labels are
  not evenly spaced, `_chunk_sides`), however many were fed back first. On longer papers such a paper is only reported
  (`first_sheet_turned`): the order of its other sheets cannot be told. A first page left on the back of its sheet (`on_back`)
  is held when the other face lies before any label or in a paper that adds up (a stack fed back first with a stray page makes
  every paper add up with the next student's page 2), not when it is the tail of a paper already held as too long; the
  between-two guard holds at most `GUARD_SPAN` papers, so one stray page cannot hold a whole section.
- `print_batches.label_mode` (`every_page` default, or `first_page`) records
  how a job's paper is labeled. It changes expectations only, never issued
  tokens, pages, files or observations: with `first_page`, assemblies
  (`ScanService.assembly`, `select_assembly`) and exam collection
  (`missing_pages`) expect page 1 alone, other observed pages are still listed,
  and an exam that adds up takes its other pages from their position on the
  identities the copy was already issued. Local grading still needs every page. `DocumentService.set_label_mode` changes it
  under the batch lock with a `label_mode` document event; both directions are
  allowed at any status. `SequenceService.label_modes` is the evidence: a job
  is OFFERED for conversion (ONE LABEL PER PAPER below: a line on Papers, no longer a pop-up on opening the scan) only
  when it is `every_page` with more than one page, this scan holds at least two
  of its page 1 labels, the scan's cadence is even with a clean grouping plan,
  no label of its other pages was ever read on ANY scan (position identities
  excluded), and the instructor has not declined for this scan
  (`scan_label_prompts`, also written when switching back). The instructor's
  own choice (`can_convert`) needs only that no such label was read. Never
  convert without the instructor's answer; every answer is an Inbox event
  (`label_mode_decision`) and the review gate is untouched. ONE LABEL PER PAPER (instructor-approved, 2026-10-18, review item
  7: "No interrupting dialog"): the one-label question no longer opens a dialog by itself. It is one line at the top of
  Papers, "One label per paper? … expects a label on every page." with the two choices (**Use first-page labels only**,
  **Keep expecting a label on every page**) and its details folded under "What this changes"; it stays until answered
  (`keep` records the answer, as before). The scan's step (action `label_mode`) leads to Papers. Bubble sheets' **Review
  all** still asks it first in its dialog, since that is an explicit click.
- A grouped exam is an immutable `scan_packets`/`scan_packet_pages` record of
  the run: the label's printed copy and its sides in order, with a merged PDF
  through the verified Inbox storage manager. The packet itself claims no page
  identity; identities are `scan_pages.page_stamp_id` with a `scan_sequence_pages`
  receipt. On `every_page` paper only an instructor-approved preview numbers
  written sides (blank sides skipped) onto the copy's issued stamps, save the one side A PAGE BETWEEN ITS OWN LABELS (below)
  gives by its place between its own copy's labels. On
  first-page-labeled paper position numbers them (above). A stamps-only copy
  was issued page 1 alone, so `DocumentService.issue_sequence_stamp` reserves
  the others as `page_stamps.symbology='sequence'` (migration 008): never
  printed, issued at once, reused after an undo, allowed by their own trigger
  only behind an issued printed page 1 of current stamps-only paper, and
  excluded from the printed manifest and `_batch_layout`. Every printed identity
  keeps the original `stamp_page_bound`. All use barcode intake's
  duplicate/changed-content rules and status `manual` (the `scan_pages.status`
  CHECK is unchanged). The instructor's Inbox review of the scan is their
  approval: `InboxService.transition` then calls `assemble_reviewed`, which
  makes each first-page-labeled exam that added up that copy's reviewed paper
  through the ordinary `select_assembly` (actor `inbox-review`, provenance in
  `scan_sequence_assemblies`). It never runs before that review, never touches
  a copy that has ANY assembly, and leaves anything duplicated, conflicting or
  out of position to the per-copy assembly review. An exam with one written side
  adds up when its packet holds every side and the rest are blank (2026-10-17:
  281 one-page take-homes with blank backs were never assembled), a packet of
  one written side included (2026-10-18: two unlabeled one-sided take-homes,
  grouped one side each, sat at "Scan needs review" with nothing to review). EMPTY BACKS (same
  day): grading takes a paper whose sides past the blank exam's last page carry no
  writing (`JobsMixin._empty_side`: under .5% dark inside 4% margins; 38 such backs
  measured .00-.09%, written sides 2.4% and more) as having the blank's pages, and a
  Gradescope send uploads such a paper as the template's pages (GRADESCOPE; instructor
  2026-10-17: "I need this fixed"; `GradescopeSync._empty_past`). Since 2026-09-28 it also makes
  every copy labeled on EVERY page whose pages each arrived once (on reviewed
  scans, nothing in conflict, no other copy of that student and assignment)
  its reviewed paper (`_assemble_labeled`), and it runs again, actor
  `automatic`, for papers made ready AFTER the review: registration
  (`unlabeled._attach_and_group`), the Group button on Papers, and the
  worker's catch-up `GradingService.assemble_later_one` (one reviewed scan per
  turn whose unassembled papers changed). Failed assembly attempts are retried with a 20-second exponential cooldown capped at
  five minutes, not marked as completed checks; swallowed per-copy failures return `retry`. Successful no-op checks are also
  revisited after 15 minutes, since eligibility can change outside the page timestamp. Scan intake, automatic review, assembly
  catch-up and automatic batch creation rotate their first opportunity within the worker's scan turn. A stamps-only version counts
  its label sheet, so `_source` requires consecutive pages from 1 instead of
  that count, and FRQ grading requires exactly the chosen blank exam's pages. `preview` writes nothing; `apply` revalidates the exact
  plan fingerprint (409 when stale) under the lifecycle and every affected
  owner's lock. Runs, packets and immutable `scan_sequence_pages` are audit
  records of the existing source Inbox message in the catalog's nightly backup;
  an Inbox event retains each run and undo. Barcode observations, reviewed
  assemblies and the review gate never change. Undo retires the run's packets
  and skips pages in a reviewed assembly, corrected since, or repeated later.
- A PAGE BETWEEN ITS OWN LABELS (`document_identity/places.py`, `PlaceService`; requirement, 2026-11-14). A late Take Home
  Assignment, copy 60, printed one-sided on coloured paper and scanned two-sided, came in as page 1, a blank back, page 2 with
  both corner codes covered by the student's writing, an empty back showing the front through, page 3, and another such back.
  Its Papers stage asked "Group the pages into papers" (one first page, so no cadence; 3 sides suggested, at which page 3 fell
  into "a paper whose label was not read", and at 6 the show-through back counted as written and held the paper). The
  instructor: "I don't think that is the actual issue", it "has happened before and the grouping button misleads". Asked whether
  Faculty should decide by itself that such a side is its copy's page, and set aside the empty backs inside that copy's paper
  with a note naming it, they answered Yes to both. On paper labeled on every page (not a stamps-only label sheet; current paper;
  a labeled page in conflict or rejected bounds nothing), in one file of a scan in the Inbox:
  - A PLACE is the run of sides between two labeled pages of the SAME copy (page a, then a later page b, only blank or unread
    sides between), or after a copy's last labeled page in the file with nothing labeled after it. Its unread sides (no code
    read, no identity, not set aside, in no grouped or reviewed paper, no bubble sheet or its back, no first page found on paper
    printed without labels) are that copy's. Only paper that shows labels on its later pages has places: a code of a page after
    page 1 read around the place, or of that print job on any scan in the Inbox (paper whose later pages were printed bare is
    grouped by its first-page labels, as before). A side between two different copies is no place: it may be either paper's, so
    it is left to Papers as before. A copy with a reviewed paper is changed on Review and is left alone.
  - IN PAPER ORDER ONLY (instructor, same day, before it went live: "if an assignment has every page stamped, I may scan them
    shuffled assuming that the data matrix will sort them out"; asked, they chose the check). A file has places only when its
    own labels show it was scanned in paper order: each copy's labeled pages together, no other copy's label among them, and in
    increasing page order (`PlaceService.in_paper_order`; a shuffled stack, or a page scanned twice, fails at once). In a file not
    in paper order nothing is given or set aside by place, and every page still sorts by its own code. Of the 31 kept files
    holding paper labeled on every page, 29 were in paper order (copy 60's included); the other 2 (a Quiz 4 and a Quiz 6 file,
    every code read) repeat a copy. With the check the replay below is unchanged but for 2 fewer hidden codes reached.
  - The copy's MISSING pages there are a+1 to b-1 (after its last label: to its page count) that no scan in the Inbox has read,
    each with an issued identity. A side CAN CARRY A PRINTED PAGE when it holds `media.PRINTED_SHARE` (.001) of ink, as
    `media.ink_share` measures it (the share the blank test compares: GREY PAPER), a tenth of the least printed page measured
    (.011 white, .017 grey, over 3,559 labeled sides), and is no back of one-sided paper: in a file of an even number of sides
    whose labeled pages, at least two, all lie on odd sides, the even sides are backs.
  - When exactly as many sides can carry a printed page as the place misses pages, each takes the next missing page as a
    recovered label gives it (status `manual`, the intake's duplicate and changed-content rule), and every other unread side
    there is that paper's BACK: set aside with the reason "A back of copy N's paper (between its pages a and b), with no printed
    page on it: set aside by Faculty", actor `automatic`, through the same review state (`ignored`) and Inbox event
    (`page_set_aside`) as the instructor's **Not part of any paper…**, so Papers lists it with its Undo (SCANNER-INTAKE). A back
    the instructor took back (`page_set_aside_undone`) is never set aside again. Each pass is one Inbox event `pages_by_place`
    naming every page given and back set aside. A place that changed between reading its pictures and the write is left for the
    next pass. Anything else is HELD: nothing is written.
  - The Inbox worker runs it right after a scan's inspection, before the automatic grouping, and again whenever the scan changes
    (`GradingService.process_scan_one`, `GROUPING_RULE` v4 so every scan is looked at once more); a paper it makes whole after
    its scan's review is assembled by `assemble_later_one`.
  - Papers names the page instead of offering a grouping: a held place is a to-do (action `set_aside`) saying "Copy N's page p
    did not read (its codes may be covered or smudged). Pages s and t lie between its pages a and b, and 2 of them look like a
    printed page, so Faculty could not tell which is its page p. Set aside the side that is not, and Faculty gives the rest that
    page by their place." or, when none does, "… did not read or did not arrive: nothing between its pages a and b (page s) looks
    like a printed page. Look at those sides; if the page is missing from the stack, the paper waits for it on Review."; a place
    the worker has yet to write is an info line; what it did is a done line ("By its place, page 3 is copy 60's page 2, between its
    pages 1 and 3 (its codes did not read). 2 empty backs inside papers were set aside."). In a file not in paper order whose
    paper shows labels on its later pages, the sides without a code are one to-do (tool `pages`, action `set_aside`): "1 page
    carries no code that could be read (page 2). This stack is not in paper order, so Faculty does not guess whose it is; still
    missing a page: copy 1 (page 2). If one is a student's page, recover its label from its printed alias; otherwise set it
    aside." (a grouping by a side count could only misplace them). A place's sides are left out of the
    other steps' pages without a label, the stage shows no count of papers grouped while one is listed, and Papers plans no
    grouping when every unread side of the scan lies in a place or such a file
    (`PlaceService.holds_every_unread_side`, beside NOTHING TO GROUP). Each side's ink is kept by its retained picture's id, at most
    512 (PERFORMANCE: immutable rows, bounded).
  - Replayed read only on the 102 scans kept that day (19,096 sides): on the scans in the Inbox it gives one page (copy 60's
    page 2) and sets aside two backs (that paper's); with the backs the instructor had set aside counted as they were, 55 backs,
    of which they had set aside 53 by hand on 2026-11-11 (the other 2 are copy 60's) and none ever became a page; hiding each of
    the 308 codes that read in such a place, it put back 307 right, 0 wrong, and held 1 (its page was also read in another scan).
    (The instructor was shown 321 of 322 and 55 of 57, before the later-page-label condition above, added while building.)
    Not built (not asked): the same rule between two papers (hiding 2,006 such codes, 1,896 right, 0 wrong).
  - A PAPER'S LAST BACK (instructor, the same day, once the rule was live: "Yes, build it"). In a file of one-sided paper scanned
    two-sided and in paper order, a side without a code right after a copy's labeled page, before the next copy's labels, is the
    back of that page's own sheet: it is set aside like the backs inside a paper, with the reason "A back of copy N's paper (the
    back of its page p), with no printed page on it: set aside by Faculty", the same event, Undo and never-again
    (`PlaceService.places(last_backs=…)`). Only the back: a front there may be either paper's page and stays for Papers. Replayed
    read only on the kept scans: 42 such backs, every one set aside by the instructor by hand on 2026-11-11 (26 with some
    writing); none on the scans in the Inbox that day.
- EXAMS PRINTED WITHOUT LABELS (`document_identity/unlabeled.py`, migration 009; instructor,
  2026-09-24): DR-center and makeup exams arrive without the label, in one scan with bubble sheets
  and labeled paper. On an explicit request from the scan page (assignment and scanned sides per
  exam) the local OCR runtime reads the top band of unlabeled pages at the side-count CADENCE, up to
  `BATCH` (48) pages per worker turn in ONE model load (`faculty_ocr.recognize_covers`, rule
  `unlabeled-covers-v4`, requirement, 2026-09-24): the batch is where the count says the next papers start, each taken
  for a cover until read; the pages are rendered (300 DPI, 4 sandboxes at once) while the reader loads
  the model; crops of one kind are read as a batch in one generation (left-padded, 8 at a time); the 180-degree reads only where the upright band lacks the printed Name and UIN; the
  title box only on a first page whose band gave no version (`title_unless`). **Stop** (`cancel`) ends a run after the batch being
  read, keeping every reading; **Look again** keeps the first run's declared letters and automatic
  registration for the same assignment. The students a first page is matched against, and offered in its picker, are the
  sections chosen for the scan's assessment when it has one (requirement, 2026-09-24), else every section of the run's term.
  After a stop, a failure or a finished run (2026-10-16: the instructor asked a finished search again at 2 sides per paper),
  asking again for the same assignment
  CONTINUES (the scan's next step offers **Continue**): the new attempt carries the earlier attempt's
  readings and the instructor's word on them (copied rows, same immutable readings, `continues_run` on
  the request event), so no page is read twice. Where the page the count points to is no cover, the pages after the last cover
  are read in turn, and at the end any unread page that LOOKS like a found cover (`LIKE`) is read too, so a cover hidden by two short exams is found. A cover carries the
  printed Name and UIN field names; its name, UIN, section and printed version (a second, title-box
  read) are untrusted drafts, escaped wherever shown. The roster match is the bubble sheets' rule at
  the instructor's bubble threshold: by the instructor's decision a confident match identifies the student with no
  click, never for a student who already has a scanned paper of the assignment in that term, nor for
  two covers read as one student; those and anything unsure wait. OTHER PAPER (requirement, 2026-10-17: "already has paper flags
  should be clickable to bring a popup of the other paper that is assigned to that person"): either hold's "Check: already has a
  paper" opens a read-only dialog with the flagged first page, the other first pages of the scan read as that student, and each
  scanned paper of the assignment the student has in that term (every page, as `_scanned` counts it); a cover the instructor named
  anyway offers "See the other paper". A cover registered by any attempt on the scan stays registered when the instructor looks
  again, and is never its own student's other paper (2026-10-17: page 97 of a take-home scan was held against itself). The
  instructor's word on a cover
  (student, not a cover, back to the reading) is append-only and the latest counts. Registering is the
  instructor's fingerprinted request: each identified cover takes page 1 of that student's stamps-only
  label, as if the label had been stuck on (an issued label no scan has shown is reused, else a new
  label job of the student's section is issued, never printed, and attached to the exam), as a
  `manual` identity with a `scan_unlabeled_registrations` receipt; then the ordinary first-page
  grouping gives the other sides their position, and the Inbox review assembles them. Nothing here
  grades or clears the review gate; the printed version is evidence only, except where versions were
  declared (below) and where the instructor's own Register click confirms it (Exam versions, below). ASSIGNMENTS CREATED FROM A SCAN (requirement, 2026-09-24: such a scan carries no
  label, so it goes through this OCR pipeline): **Choose the assignment** (formerly Associate scan) → Create a new assignment asks the
  number of versions (0 or 1 = one version, no version handling; kept per term grading plan in
  `frq_declared_versions`, append-only) and, when the scan has unlabeled pages that are not bubble
  sheets, the scanned sides per paper (default 2). Saving starts this run itself
  (`grading.intake._read_unlabeled`) with the declared letters (`scan_unlabeled_runs.versions`) and
  `register_confident`: when the run ends it registers every identified cover exactly as Register
  would (actor `automatic`, same fingerprint); conflicts, unsure matches and unreadable covers still
  wait for the instructor. A version read that is not one of the declared letters is a note on the
  cover, never a reason to withhold identity. A
  recognized bubble sheet, and the blank side scanned right after it, end an exam in the grouping plan
  (`sequence._plan`) and join none. The tool is for ANY assignment, not only exams (requirement, 2026-09-24: "grading is not supposed to be restricted just to exams"; quizzes carry Name and UIN on page 1): a "cover" is any first page with the printed
  Name and UIN fields, and the scan page says "paper" and "first page" for it and for first-page grouping. Stored identifiers
  (`unlabeled_exams_*` event kinds, `not_cover`, `unlabeled-covers-v1`) keep their names; the Inbox history shows neutral ones.
  `LIKE` and `TITLE_BOX` were measured on exam covers only. On the real Chapter 2 Quiz scan (2026-09-24, read-only sample) the first pages were found and the names read on 10 of 10 and the UIN on 9 of 10, but `TITLE_BOX` read no version on any (the quiz prints it above the box) while the top band read it on 6 of 6:
  since `unlabeled-covers-v2` the version
  comes from either reading, one alone counts, and two that disagree count as none (since v4 the box is read only when the band
  has no version). `LIKE` is still unmeasured on quizzes.
  Runs, readings, reviews, requests and receipts belong to the source
  Inbox message; readings are immutable, the rest append-only, all in the catalog's nightly backup.
  The first pages have pages of their own, never a list on the scan's page (requirement, 2026-09-24): `#inbox/job/<id>/students`, **Students** (needs a name or in conflict, plus the instructor's
  choices until registered; "Find students" until 2026-10-18), and `.../students/matched`, **Matched papers** (identified, waiting for a label, registered). They are
  views of the same summary and store nothing; once first pages exist the step opens Students. Likewise, once a scan is
  grouped, the pages no grouped paper holds are named by the paper they follow and its side count ("5 papers have more sides
  than the 2 per paper", or "5 sheets look scanned back side first", `ScanService.ungrouped`), and that step opens `.../papers`,
  **Papers** ("Papers to check" until 2026-10-18; `papers_to_check`: the instructor's plan, held papers with their reason, turned sheets and the paper
  before each, scaled page images from `pages/<id>/image.png`, and a Group button applying exactly that plan's fingerprint;
  since 2026-10-02 also **Choose its sides…** on a held paper, the papers grouped by hand with their Undo and the sides they
  left, "Not part of any paper…" on a side in no paper, and the pages set aside), never the list of every grouped paper.
  ONE WAY TO GROUP (requirement, 2026-10-18: the pipeline review's "give each stage one page and one way to do its job"; "make
  all of your suggested changes"). Papers has one primary path, **Group N papers**. Every other way to group sits under one
  fold, **Other ways to group**: **Choose its sides…** for each held paper whose page 1 label was read (the held paper's row
  keeps its reason; why it cannot be chosen stays on the row), and grouping by first-page label at a number of sides per paper
  with its Preview. The fold is open while there is nothing to group the usual way (no Group button and a held paper to
  choose, or no grouping run yet) and keeps the instructor's choice. The grouping runs with their Undo and the grouped papers
  are listed under **Grouping so far**. Sides per paper is asked in one place: Papers' grouping form. The Students page's
  "Look for first pages again" shows the last search's count as a reference with a Change fold holding the same field and a
  link to Papers; the Choose assignment dialog still asks it once for a new assignment made from a scan of unlabeled pages (it
  starts the first-page search) and says later changes are made on Papers. Nothing about grouping, its gates or registration
  changed. A
  paper grouped by hand is not a grouping of the scan: until a grouping ran, its other unlabeled pages still read "N pages
  without a label follow the labeled first pages". Pages in no grouped paper close with "Recover a label from its printed alias,
  or set aside a page that belongs to no paper." A STRAY SHEET (requirement, 2026-10-02): before any grouping, when every page
  without a label comes before the first label of its file, or every copy on the scan is labeled on every page and has every
  page, the step is "N written pages carry no label and belong to no paper (page 1). If one is a student's page, recover its
  label from its printed alias; otherwise set it aside." (tool `pages`, action `set_aside`; the action places it in the
  **Papers** stage, `ScanService.ACTION_STAGE`), copies missing pages are flagged at once, and Papers says "Each page carries
  its own label." While that step is open the Papers stage shows no count: its count is papers grouped of first pages found,
  and papers labeled on every page are never grouped (requirement, 2026-11-11, of a take-home scan whose 90 papers were put
  together and whose tile read "Papers 0% · 0/90" over three stray pages: "it's showing 90 papers"). NOTHING TO GROUP (same day:
  "it says i need to group 74 pages"): where every copy on the scan is labeled on every page with every page read and no
  grouping of the scan was made, Papers plans none at the side count the labels suggest (`ScanService._labeled_throughout`,
  `papers_to_check`): it had offered "Group 74 papers" and held 16 more for the backs set aside in them, all 90 already put
  together from their labels. The page then lists only the pages in no paper and those set aside. Nor does it where every side
  without a code lies between its own copy's labels (A PAGE BETWEEN ITS OWN LABELS, 2026-11-14). Setting a page aside lives on Papers only (instructor-approved, 2026-10-18): Papers lists every page in no
  paper with **Not part of any paper…**, the stray sheet before any grouping included (read from the scan's page rows when
  Papers' plan has no side count), with the pages set aside and their Undo. The Scan page still lists the stray page under
  Pages that need a look (for label recovery) with "No student's page? Set it aside on Papers", and says "N pages set aside as
  part of no paper: see them on Papers"; Papers reads the full review item (not `?view=summary`). A page set aside is no page left to group (`ungrouped`, `auto_group`, the summary's `unidentified`), so a scan
  whose only open step was that page has nothing open: its blockers, Inbox filing and certification's "Papers may still be in
  the Inbox" clear; nor is it among the scan's pages without a label (`recognition.without_label`, with `set_aside` counting
  them), so it never asks for local OCR when the scan is filed under a new assignment. Pages without a label wait for
  Papers, where the grouping shows which are in no paper. The scan says it was recognized from
  first pages (`recognition.first_pages_read`) when no label was read. All read only. A registered or registering
  first page is not matched again: it claims the student its registration names; other
  matches are remembered per reading and roster (`UnlabeledService._current_match`).
  JEV PAPERS (requirement, 2026-10-15, after a challenge scan of one-page quizzes scanned two-sided, some without a Name or UIN
  line and one upside down: "you have my ok to build it as you suggest. dont use it for answer areas"; `document_identity/
  jev_papers.py`, rule `jev-papers-v1`, JEV "JEV PICTURES"). When Jev is set up, every page the run reads is also shown to
  Jev whole (`page_check`, purpose `paper-first-page`): a handwritten name anywhere (`FIRST_PAGE` .5 since 2026-10-17, when a
  take-home first page with its name in the top corner read .70; replayed on 472 pages: first pages .93-.99, written later pages
  at most .37), which way up, and a box
  around the handwritten name and one around the UIN. A page with a handwritten name is a first page even when its top carries
  no printed Name and UIN (the OCR band it was read in still gives its fields; turned when Jev sees it upside down). UPSIDE DOWN
  (2026-10-17, requirement: "why didn't jev catch that these were upside down"): Jev saw four take-home first pages upside down but
  read their names at .21 to .74, so a page Jev sees upside down with no name found is asked again turned upright, and that look
  decides (`turned_receipt`; its boxes turned back onto the page as scanned). The OCR's "VIN" for a handwritten "UIN" label counts
  as UIN (`cover_fields`). LOOSE FIRST PAGES (same day: "so how do I fix the ones that are still upside down"): once a run
  finished and its scan is grouped, a page it read as no first page that is in no paper (written, no identity, not set aside) is
  listed with the first pages ("Read as no first page, and it is in no paper"), and the instructor's choice of its student is
  reviewed, registered and grouped like any first page's (`UnlabeledService._loose`). For every
  first page Jev then reads whose paper it is (`identify`, purpose `paper-student`, against the roster of the run's sections at
  the time of the reading): the name, cut from its box with a 2% margin, is asked of each section's roster in both option orders
  with "None of these students"; every student any section picked is a candidate; a NAME round (the whole name, among the
  candidates) and a UIN round (the UIN cut the same way, among the candidates' roster UINs) are each asked in both orders. A
  round decides when its orders agree on a candidate; Jev names the student when one round decides and the other names no one
  else (the instructor was asked whether both must agree; this is the default until they say). Such a first page is
  identified on the basis `jev` (migration 014 rebuilds `scan_unlabeled_plans` to allow it), with the same conflict holds as any
  confident match; where the OCR's confident match and Jev name different students it waits for the instructor with a note,
  and a page Jev could not settle carries Jev's reason. The instructor's word still counts last. Jev failing, refusing or not
  set up changes nothing: the OCR reading and its rule stand. The cover image of a first page only Jev found shows where Jev
  saw the name and UIN. Measured 2026-10-15 with the built code on the challenge scan (100 sides): 50 of 50 first pages found,
  none false, 50 of 50 students right; the OCR alone found 42 and identified 42 (the 7 without the printed field names and the
  upside-down one without a UIN were never first pages). On graded scans: 91 later pages with work scored at most .16 for a
  handwritten name, 32 first pages at least .98.
  GEMINI MATCHES THE STUDENT (requirement, 2026-11-12: "replace jev with gemini decisions for scan to students"; JEV "STUDENT MATCH
  PROVIDER"): `identify`'s three requests are asked of Gemini Decisions when it is set up, with the same questions, crops, rounds,
  rule and holds; the page check stays Jev's. The basis stays `jev` in the plan; a match Gemini gave carries `reader`, the first
  page's row says "Identified by Gemini from the handwritten name and UIN.", and its notes begin "Gemini". A reading kept before
  the switch stands and is not asked again. Gemini failing, refusing or not set up changes nothing: without its key Jev matches as
  before, and a failed match leaves the OCR reading and its rule standing. Replayed that day through `identify` on 119 first
  pages the app had read: 118 named, none wrong, 1 left for the instructor; nobody named on 30 of 30 pages whose own student was
  taken off the roster.
  DR COPIES (requirement, 2026-11-10; GRADING "DR COPIES"): a quiz drafted in Faculty is printed with labels everywhere but at the
  DR center, so its paper found without labels is the DR center's print of the accessible copy. Registering gives it page 1 of a
  label of the quiz's DR copy document (`GradingService.dr_copy`, made then when it is not there yet; a copy that cannot be made
  refuses the registration with its reason, since under the plain label sheet the paper could never be graded), never of a
  "Stamps only" version, and no such version is made for the quiz. Its papers are counted at the copy's sides
  (`UnlabeledService.paper_sides`: the newest DR copy's `pages` before the blank templates' page count). Everything else about
  finding, reading, matching and registering first pages is unchanged; an assignment with no DR copy keeps the label sheet.
  A DR copy whose Word file prints its page codes (GRADING "DR COPIES" > STAMPS) needs none of this: its pages carry stamps, of a
  job of unnamed copies of the copy's stamped document (`DocumentService.register_dr_copy`, generator `faculty-dr-copy`, laid out
  `faculty-corners-10mm-v1`), and `create_batch` refuses every job of a DR copy that is not a registration.
  JEV MIXED SCANS (requirement, 2026-10-15: "this way Jev can separate different assignments even if they are jumbled together with
  the same scan file", then "yes"; `jev_papers.which_assignment`, purpose `paper-assignment`). When the run's course has more than
  one assignment, Jev is also asked which of them each first page is from: every assignment of the course, each described by how
  its blank paper begins (`DESCRIBED` 300 characters of its first template's first page: the text layer, else the title lines the
  assignment finder reads; "No blank paper is on file yet." without one), and "None of these assignments", in both orders. A paper
  belongs to another assignment of the course only when both orders choose it: it is then registered under that assignment (its
  own stamps-only label job, reused labels and "already has a paper" hold of that assignment, the new job attached to that
  assignment's exam), with a note saying so; a split keeps the run's assignment; both orders choosing "None" holds the page for the
  instructor with a note, but only when Jev was shown the run's own assignment's blank paper (2026-10-16: an assignment made
  from its own scan has none, and Jev then read all 131 first pages of the take-home as "None"; the reading records `run_blank`,
  and one kept before that is judged by whether a blank is on file now). The run, its sides and the scan's grouping stay one per scan: papers of different lengths in one scan are
  held for Papers as before, where the instructor chooses their sides. Measured with the built code that day: 97 of 98
  pages right (48 graded pages of four assignments, page 1 and page 2, and the 50 quizzes of an assignment Faculty does not have,
  all "None"), none wrong, one split.
- `ScanService.overview(db, message_id)` is the ONE summary of a scan: a
  headline, facts, a state and `steps` (todo/check/wait/done/info, most
  pressing first, each optionally naming a `tool` or an `action`). It only
  DERIVES from retained records and never decides, identifies or clears
  anything; it names no student (the Inbox list is glanceable) and must stay
  cheap, since `InboxService.listing` attaches it to every `scan.receive` row
  (`job.scan`) on each poll and `review_item` returns the same object.
  A SCAN IS ITS FLOW GRAPH (requirement, 2026-09-24). The overview's `stages` (`ScanService._stages`:
  Scan, Assignment, Students, Bubble sheets only when a scan has them, Papers, Review,
  Grading) place every step in one stage by its action or tool; a stage is `started` while a
  step waits for the instructor or the worker, `done` when its work is finished, `failed`
  only when inspection stopped, else `idle`. Stage statuses use the one scan vocabulary (instructor-approved, 2026-10-18):
  **Needs you** (a `todo` or `check` step; `urgent` keeps them apart), **Working** (the worker's `wait`), **Failed**
  (inspection stopped; was "Stopped"), **Done**, **Not started**. The card's footer reads "<status> · <stage>" ("Needs you ·
  Students", "Working · Students", "Failed · Scan"); Home's row detail "<status>: <goal>". Older words ("To do", "Needs a
  look") still map to Needs you client-side. Each stage page's heading is its stage name: Scan, Assignment, Students (its
  sub-page Matched papers), Bubble sheets, Papers, Review (were "Scanned pages", "Find students", "Papers to check").
  The overview's step names the scan's assignment as "Assignment: <title>." (its papers in grading, or the review passing by
  itself; was "Filed under" / "Associated with"), as the Assignment stage's done text does. `static/scan-flow.js` draws them with the
  grading workflow's step graph (`pipelineMarkup`): the Inbox list shows each scan as a card
  like an assignment card in Grading (header, facts, graph, the one next thing), the scan's
  page shows the same card, and each stage opens its own page (`#inbox/job/<id>/pages`,
  `assignment`, `students` and `students/matched`, `sheets`, `papers`, `review`) with the
  graph on top. Keep it that way: a new scan feature adds its step to `overview` (and so to a
  stage) and its tool to that stage's page, never a panel on the scan's card or a client-side
  rule. Each scan page names the scan in the page header (the overview's headline; "Stage N of
  M" under the graph) and has ONE "Updated · Refresh page review" control in its heading row
  (2026-09-26); a stage route the scan does not have says so in one line, except Bubble sheets,
  which always offers the reader. A finished scan links to the next present scan in Current
  that needs the instructor ("Next in Current", a view).
  GOAL FIRST (requirement, 2026-09-26). Every stage page
  that is not the current stage opens with the next step and a button to its page
  (`scan-flow.scanNextStep`, the same stage the card's footer names). A scan where no page carries
  a label and more than one page is left (not bubble sheets) has ONE next step, Students: "they
  look like papers printed without labels ... find whose paper each is", with the papers it makes
  from one blank template's length (`UnlabeledService.paper_sides`, the page count most of the
  assignment's templates share, also the Students form's suggested sides). Sorting by printed
  version and alias recovery are then an `info` step (the Scan stage is done), and the Scan page
  shows the first pages small, with the page-by-page readings and those tools folded. A single
  stray page, or labels read on any page, keeps the earlier steps.
  WHAT THE CHECKS COUNT (2026-10-02). A copy with a reviewed paper, a partial one too, is settled: it is never counted among
  the copies with "pages that no scan has shown with a label" (an accepted partial paper used to hold its scan, its grading
  item and certification for good). A page of a paper accepted as partial (labeled on every page) that arrives later is not
  left out silently: the paper is flagged on its own ("N paper accepted with missing pages has more pages now: save its
  reviewed paper again", tool `assemblies`) until it is saved again. While the scan's labeled pages still wait for their first
  grouping (the Papers `todo`), the copies they would complete are not flagged on Review as well: one cause, one step; the
  check returns once any grouping ran. Each assembled paper says `grown` by that check's rule (pages read in scans still in
  the Inbox), so Review lists exactly the papers the check counts. A failed inspection says why when the reason is one of
  Faculty's own inspection sentences (`scans.INSPECTION_REASONS`: decode_message, process.run and media.py's scan limits;
  "The original is kept." added when there is one); any other error, a library's `ValueError` the sandbox passes on
  included, keeps the generic sentence. Its step reads
  "Its pages could not be checked: <reason>. Retry the inspection; if it fails again, upload a readable copy of the PDF."
  First pages read for an assignment the scan is not filed under name it (headline "<title> · papers without labels", and
  the Assignment step adds "First pages were read for <title>.", or "are being read" while the run lasts); nothing is filed automatically. A page recovered by its
  printed alias follows barcode intake's duplicate/changed-content rule among scans still in the Inbox
  (`ScanService._identity_state`, shared with identification by position). A page row of the review item says
  `bubble_sheet: true` for a page recognized as a bubble sheet (the key is absent otherwise): it carries no label or alias,
  so the Scan page leaves it to the Bubble sheets stage.
  Until inspection is complete (queued, running or stopped) the overview says
  only that, as "N of M" pages (`document_decode_jobs.pages_total`, counted before
  extraction), and offers no other step. Work the worker does afterwards shows its
  progress: bubble detection and reading and first-page reading as `wait` steps
  (they held a scan back before), printed-label OCR and version sorting as `info`
  steps marked `progress` (they never held anything back, and showing them must
  not start to). An Inbox tile shows the most pressing step and every `wait` or
  `progress` step (requirement, 2026-09-24).
  A retained scan's stored job status stays `needs_review` forever (`scan.receive`
  has no processor), even after its review clears the gate, so `InboxService.listing` files scan rows,
  counts and totals by the overview's state instead (`listed_as`: done ->
  Completed, wait -> Running, todo/check -> Needs review; all but Completed show
  under Current) and the scan page's badge shows the scan's own state in the one vocabulary (Needs you, Working, Nothing to
  do, Failed; INBOX, 2026-10-18). That is a VIEW: never write the derived state to
  `inbox_jobs.status` or `instructor_review_required`.
  The Bubble sheets page's "Review all" only SEQUENCES the existing
  pop-ups (the one-label question while unanswered, ONE LABEL PER PAPER, then each `needs_review` sheet in page
  order); every answer is saved by its own existing endpoint as it is made, a
  sheet is visited once per pass, and nothing is batched or decided for the
  instructor. Files, command and audit
  history sit in `[data-inbox-record]`, closed by default, the job ID, attempts, storage, audit history and command under its
  one **Technical details** fold (INBOX, 2026-10-18); its audit history is drawn when that fold is opened, and each
  run of like events when that run is opened (a section scan holds a thousand events). The record shows a scan job's
  stored error only when the job failed: "This processor has not been implemented yet." is how every retained scan waits
  for its review. Grading's response crops (`response-N-kind.png`) are listed as **Grading crops**, apart from the
  separated pages. Roster names appear
  only inside disclosures and on stage pages. A scan offers no **Review command** there: its
  review is **Finish review & open grading** on its Review page, once the overview's checks are done, since
  a review assembles its first-page-labeled exams (`assemble_reviewed`) and an
  early one would assemble before them; non-scan jobs keep the button. BLOCKERS YOU CAN ACT ON (instructor-approved,
  2026-10-18, review item 6): while a step for the whole scan (`holds: scan`) or the worker's `wait` holds the review, the
  Review page's **Finish review & open grading** is disabled, described by "Waits for the scan checks:" and each holding step
  as a link to the stage whose words carry it. The server check is unchanged; a click made before a refresh still gets the
  dialog, now titled "Finish the scan checks first", its blockers linked the same way.
  PLAIN WORDS ON SCAN PAGES (same approval, review item 8; FRONTEND.md "Page rules": raw internal codes are never shown): the A1- alias is "the code printed
  beside the label" (form label "Code printed beside the label (A1-…)", fold "Recover its label from the printed code",
  report "Label reading report", retry "Read the code again"); an "assembly" is a "paper" (Save reviewed paper, Accept it
  with pages missing, Papers to check / Every other paper on Review, "Version N · pages missing · the student's paper", "Use
  this as the student's paper"); no "Data Matrix" on scan or bubble pages; "Check its pages" (was Inspect retained pages),
  "Page PDF" (was Retained page PDF); page states "label from cancelled/replaced paper", "label from paper never printed", "a
  label Faculty never printed", "a label Faculty cannot read". Label recovery is drawn open on a page that needs a look;
  version sorting is drawn open on the Scan page itself when the scan's step asks for it (tool `sort`, `todo`), else it stays
  under Other options. A SCAN WITH NOTHING TO CHECK PASSES REVIEW BY ITSELF (requirement, 2026-09-27): `GradingService.auto_review_one` (the worker's scan turn) runs the ordinary review (`_review_scan`, actor `automatic`, the same checks and first-page assembly) only when the scan has an assignment, its inspection is complete, its labels are not `mixed`, nothing about the WHOLE scan is open (`review_blockers`: the overview's steps marked `holds: scan` and every `wait` step) and no background reading (`progress`) is under way. Since 2026-09-28 papers, sheets and pages flagged on the scan no longer hold its review back (see "Every paper goes on unless it is flagged"): they stay open in the Inbox and are named in a `review_exceptions` event. `InboxService.transition` accepts actor `automatic` for a `scan.receive` review only; every other job still needs the workspace. The `reviewed` event names `automatic` and the Review stage says so. This EXTENDS "only review in the private instructor workspace clears it" for scans alone; an automatically chosen assignment is reviewed the same way (its info step stays). Each page's
  content is redrawn by `refresh()`; the Bubble sheets page's `[data-bubble-slot]` is
  not, so the bubble panel survives refreshes and tells the page about changes with a
  `scan-changed` event. Non-scan jobs keep the original job page. POLLED PAGES PATCH IN
  PLACE (requirement, 2026-09-26). The job
  page keeps the mounted card and patches only its heading and record, the card's `refresh()` is
  one coalesced draw that changes only what differs (`static/dom-patch.js`; nothing when nothing
  did), the Inbox list patches too, and a file group draws its files only while open. Bind by property (`onclick =`) or delegate; anything bound
  with addEventListener (student pickers) is `opaque` to a patch and replaced whole.
- A SCAN WITHOUT LABELS FINDS ITS ASSIGNMENT (requirement, 2026-09-27: "automatically select the assignment without a label if it is high confidence"; they chose the evidence and what follows; `document_identity/assignment_finder.py`, rule
  `assignment-from-template-v4`). A worker stage looks ONCE per rule at each inspected scan with no label, no association, no bubble
  read and no first-page search, after bubble detection and alias recovery have settled. ALIASES SAMPLED (requirement, 2026-10-31:
  "make that change as long as it doesn't cause any pathological behavior", of a 98-page scan of Exam 2 booklets and bubble sheets
  that waited on 60 alias reads of about 18 s each and was linked to Exam 3 by hand meanwhile): recovery has settled once every alias
  read is done, or once `ALIAS_SAMPLE` (8) are done and none saw a printed alias (outcome `no_match`, no alias read); a read that saw
  one keeps the wait for every read. The other reads go on as before. On the live catalog that day every alias any scan's reads
  found was found by its 1st or 2nd read, and 27 scans' 590 reads found none. Papers: its pages are compared with page 1
  of every current assignment's blank templates (the cover likeness, upright and turned, at every print size in `SIZES` and shift in
  `SHIFTS`; a page votes at
  `PAGE_SCORE` .45 with a `LEAD` of .12 over every other assignment; since v3 (requirement, 2026-09-29) no LEAD is needed when the
  page prints the title of exactly the assignment it looks most like), the `READ` (8) pages most like a
  template have their top band read in one model load, and a page's TITLE is a printed line of the TEMPLATE's top (its text
  layer, else OCR of its page 1) that no other assignment's template prints, never the assignment's name in Faculty. It is chosen only when `AGREE` (3, or
  every page read when fewer) pages both look like it and print its title, and no page points elsewhere; then the ordinary
  association (`GradingIntake.associate_found`, actor `automatic`, the exam's sections or else the term's) and the ordinary
  first-page search with registration of confident students at the blank's length. JEV PROPOSES (v4, requirement, 2026-10-26: "have
  Jev propose, and then if that assignment fits (ie the page counts line up) then it assigns that to the scan"): when the templates
  choose nothing and their pages do not point to two assignments (a scan whose first pages do is separated by its printed titles
  when its page counts agree, INBOX "SEPARATING A SCAN"), Jev is asked which current assignment each of `JEV_PAGES` (6)
  pages is from (the scan's first page, then those most like any blank; `jev_papers.which_assignment`, both orders, each assignment
  described by how its blank begins). It proposes an assignment when no page is read as another one and at least
  min(`AGREE`, papers that fit, pages asked) pages are read as it; it is chosen only when the scan's paper pages (every page a paper
  may be among, blank backs included, never a bubble sheet or its back) are whole papers of its blank template's page count. No
  template, a count that does not divide, a split or another assignment: nothing is chosen, and the event says why. Replayed read
  only that day on seven earlier scans with Jev asked about their first six pages in scan order: Quiz 3 (616 pages), Exam 2 (168),
  and three two-page quizzes chosen right; the take-home and a stray page beside bubble sheets read as "None"; none wrong. Bubble sheets: the ONE exam of a current term
  that the calendar dates (sittings, seating charts, linked DR bookings) within `WINDOW` (14) days up to the arrival, and when
  some of those exams have a bubble-sheet answer key (`frq_bubble_keys` of their term plan) the one of THOSE whose latest key has, in
  every version, as many questions as the sheets answer (the question most of `SHEETS_READ` (40) sheets have their last mark on,
  `reader.infer_question_count`, as the read infers it; sheets that do not agree choose nothing) (2026-10-26: Exam 2's
  sheets chose nothing because Quizzes 2 to 5, none with a key, had makeup sittings in the same two weeks; the instructor asked
  why they were not filed under Exam 2); then the ordinary sheet read (actor `automatic`). Papers and sheets naming different assignments choose nothing. Every look is an Inbox
  event `assignment_found` with its evidence (chosen or why not), which the scan's overview states; the instructor changes a
  wrong choice with the ordinary Change assignment. This EXTENDS "Bubble sheets carry no label: the instructor names the
  assignment" and the association being the instructor's: nothing else changes, no page is identified by it, and the review
  gate stays. A quiz with bubble sheets that is not on the calendar is not seen by the calendar rule: another exam in the window
  would be chosen, so the overview says which dates it went by.
- `ScanService.recognition` answers "what is this scan for" from what is
  printed on it: print jobs seen, issued papers present and those no scan has
  shown, labels that are not current, and a verdict. `recognized` is current
  paper of ONE assignment in one term across any of its sections (sections
  sit exams together; `sections` lists them, a term job contributing the sections
  it lists as `for_sections`); `mixed` is current labels naming
  different assignments (the worker then separates the scan into one per assignment, INBOX "SEPARATING A SCAN"), or no label
  of current paper; `bubble_sheets` is no
  label but pages recognized as sheets; `unrecognized` otherwise. A doubtful
  label (unknown, malformed, ambiguous) or a label of paper that is not current
  is a flag on its own page since 2026-09-28 (instructor's choice): the scan is
  still `recognized` and its per-page check step lists them, the grouping and
  assembly rules hold the pages behind them, and `current_papers` (current
  paper first in `papers`) is what association and review compare.
- Bubble sheets are recognized by their corner positioning marks right after
  inspection (`BubbleSheetService.detect_message`, called by
  `ScanService.process_one` through `scans.bubble_detector` BEFORE printed-label
  OCR is queued, which skips recognized sheets; `_detect_next` catches up older
  scans). `reader.detect`: the whole-page resemblance proposes, then at least
  three trusted markers placed within 3% of true size decide; the stapled
  corner is normally cut off, so never require four, and never require the ring
  check for recognition (it gates reading only). EXCEPT where the proposal does
  not hold (requirement, 2026-09-25): `reader.settle` aligns every other template page either way up and
  accepts only three trusted markers within 3% AND rings at `RING_SURE`; detection
  and reading both use it (`settled_by`, `DETECTOR` marks-4; marks-5 adds `reader.letter`, BUBBLE-SHEETS "HOME-SCANNED SHEETS"); the rings also say WHICH template page it
  is, since both pages share marker positions but not their grid. `bubble_detections` rows are
  immutable per (page, `DETECTOR`); a changed rule gets a new `DETECTOR` name.
  Recognition is the page TYPE only. Bubble sheets carry no label: the
  instructor names the assignment and sections explicitly (flagged in the
  recognition panel until a read exists), and a sheet is recorded for a student
  only by an exact roster UIN, an uncontradicted match at or above the
  instructor's auto-identify threshold (default 70%; bubble-sheets.md), or the
  instructor's review. Never by position.
- Section scans are hundreds of pages. `manage_inbox.py scan` retains PDFs
  already on the server through `InboxService.submit(large_scans=True)`: only
  `web`/`cli` sources, only `scan.receive`, only with instructor review
  required, PDFs up to 512 MB each. Email, API and code adapters keep the
  10 MB/25 MB limits, except that an email to a scanner intake address takes each attachment up to 25 MB (SCANNER-INTAKE
  "A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL"). The Inbox page's **Upload scanned assignment** is the web twin
  (POST `/api/inbox/scans`, instructor workspace only, one PDF per request, keyed
  `scan:<sha256>` like the CLI, so a file sent again returns its scan); the web
  server's body limit is set for it and every other route keeps its own. `decode_message` asks the sandbox for the page count
  (max 1,500) and extracts at most `DECODE_CHUNK` pages per worker turn, skipping
  pieces already retained. Bubble detection is bounded to that many pages per turn
  too. Between successful chunks the existing Inbox-owned decode job returns to
  `pending`, behind other pending scans by `updated_at,created_at,message_id`;
  retained `scan_pages` are its resume cursor. A running chunk interrupted by a
  process exit still fails and needs explicit retry. A continuation is not a new
  attempt; explicit Retry increments the attempt. Grouping, association and label
  OCR wait for the whole inspection. Printed-label OCR is queued per scan after
  inspection (`enqueue_message`), never for a scan whose identified pages come
  only from peel-off labels, and at most `MAX_PER_MESSAGE` pages per scan.
  Large scans holding `DAMAGE_RUN` (64 KB) or more of consecutive zero bytes
  are refused as damaged copies: four Taildrop transfers on 2026-09-18 arrived with a zero-filled hole near the start that blackened each file's first page while the instructor's originals were intact. Do not "repair" such pages or
  blame the scanner; compare checksums and ask for the file again.
  Assemblies contain pages from one printed copy, with immutable reviewed revisions
  and explicit choices among changed observations or multiple submissions.
- **Create assignment** (`static/assignment-wizard.js`) is the only entry on
  Assignments (the page header's primary, and `#documents?create=1`):
  Details, Seating, Paper, Stamp and print, plus **Continue** and each stage of an
  assignment card (`#documents?assignment=<id>&step=details|seating|version|print`)
  and `#documents?chart=<id>` from a chart page. It owns no data. The Paper step no
  longer opens "Manage grading templates" (they stay on Exams → Manual scores and
  Grading); stamps only is offered for sections without a chart only when the
  assignment already has a stamps-only version. Assignments stay course-wide; the ticked sections are
  browser state that scopes the term-owned charts and the print jobs the existing
  services create (sections without a chart print ONE term job listing them: TERM
  PRINT JOBS), so never persist them on the assignment. The
  assignment is created when leaving the Seating step, not before. Seating
  creation and confirmation reuse `seating.js` (`openCreate`/`confirmChart`
  with a `wizard` context that turns Cancel into Back and returns results);
  keep their checks, request IDs, response-card and mail steps there.
  Paper's LaTeX choice registers one version or several in one pass (Add another
  version; each row compiled in turn, a registered row never sent again); its Letter
  and A4 demonstration fixtures are listed only on a page marked for testing
  (`<html data-testing="1">`, set by the browser checks), since a registered version
  never goes away. Stamp and print counts the jobs of the sections without a chart
  version by version: a job of the version on screen is named (Already printed, its
  stack's interleaved file first) and starts more copies at 0; jobs of other versions
  are named apart and change nothing. One stack, every version: one copies field per
  version (the students shared out, the first versions one more) and one Create print
  jobs that makes one term job per version under one request prefix
  (`<stack>:<letter>`, the letter by the version's place in the stack). Its versions
  are those registered together in this window (Add another version), else every PDF
  version that is not an earlier layout (all its jobs this term superseded or
  cancelled); two or more, none printed for those sections yet. It leads the step, with
  printing one version only under a disclosure, only for versions registered together
  or when the paper is the window's own default (Continue, a card's stage link); a
  version registered alone or picked by the instructor prints alone first, the stack
  folded under it, so a revised version never prints beside the one it replaces by
  default. A version that fails leaves the jobs already made in place and named, and
  Create print jobs sends only the rest with the same request IDs; meanwhile no single
  version is offered, a stack version printed apart (another tab) counts as done and
  is never sent again, and the stack lasts only while the window stays open (closed,
  the rest prints apart). Enter in a copies field presses its own Create print job(s),
  never Done. No caption is printed and nothing else changes on the server. Paper
  links Grading only once Grading lists the assignment (the lifecycle row's
  `grading_entry_plan_id`, else `grading_plan_id`). While the term's exam record has no
  grading plan at all (`has_grading_plan` false) it offers Set up grading now (GRADING
  "SET UP GRADING NOW", requirement, 2026-10-02), a secondary button under "Grading can be
  prepared now, before the papers come back: rubrics, answer areas and the answer key.":
  a version typed on the step is registered first, as Next would, then the plan is made
  for the window's term and ticked sections and the window closes on its Rubrics &
  Templates step. With a plan an associated scan made, it says Grading lists the
  assignment once the scanned papers pass Inbox review. Details' box "Graded for
  participation" (requirement, 2026-11-01; GRADING "GRADED FOR PARTICIPATION") is put on
  the term grading plan through its own route once the window's grading setup made the
  plan; unticked it writes nothing, and Paper starts with one version as before. Details takes an optional
  'Given on' date (requirement, 2026-10-02) below the sections, empty by default and
  kept exactly as typed; once the assignment exists (Seating's Next) the term's exam
  record gets its main sitting on that date through the Exams page's own routes
  (`POST /api/exams` when the term has none, then its sittings route), which puts it on
  Home's Coming up, the Calendar and its Exams page. A date the term already records
  is shown instead, naming where it is changed: the seating chart whose date it is
  (`lifecycle.exam_date_chart_id`, additive), else its Exams page (the main sitting).
  One the window recorded is changed if the instructor goes back and types another,
  and taken off that sitting again if they clear the field (review fix, 2026-10-02).
  Each assignment card also offers Given on… for its current term (instructor,
  2026-10-03: "add it to the assignment page itself"): the same date through the same
  routes, in a dialog. It edits the main sitting the card's date comes from (earliest
  dated, else the first main sitting), adds a main sitting titled like the record when
  there is none, and makes the record first, for the sections ticked there (all the
  term's by default), when the term has none; clearing the field takes the date off.
  A date a seating chart records is named with a link to that chart, not edited.
  Set up grading now's notice (here, on the Exams hub and in the card's menu alike)
  names a version whose blank template could not be prepared, and for a plan with no
  blank template says to register its PDF, then prepare it in Rubrics & Templates.
  Done ends with the next step (give it, then
  upload the scans in the Teaching Inbox) and opens the exam page from its toast, or
  names the version still without a print job.
  Continue, a card's stage link, and a Details key and title matching an existing
  assignment of the course all CONTINUE that assignment with its current-term charts
  attached (nothing is written before Next; its key and title are then locked), so
  seated sections are never offered as fresh unseated copies; Stamp and print names the
  existing current jobs without a chart (the term's, or an earlier section's) before
  offering more copies. The page
  lists assignments as lifecycle cards (`DocumentService.catalog` `lifecycle`: one row
  per assignment and CURRENT term, stages from `assignment_stages`; a Grading link
  once the plan holds responses, or an approved quiz or Set up grading now set it up
  before any paper (`grading_entry_plan_id`, QUIZZES, GRADING; `grading_plan_id` still
  names only a plan with responses), since scan association alone never puts an
  assignment on Grading; while the term's exam record has no plan at all
  (`has_grading_plan`, additive) a ⋯ menu offering Set up grading now for that term
  (requirement, 2026-10-02), which opens the new plan's Rubrics & Templates; a Quiz draft link to the
  approved draft's page (`quiz_draft_id`); what was printed, from current print jobs
  only: `printed_copies`, `printed_versions`, `printed_stack` for one term stack in
  several versions) and print jobs grouped by export (a term stack's versions
  together, ONE STACK, EVERY VERSION above), with registration jobs
  folded under "Issued for scanned papers" (Not for printing). The catalog lists every
  print job and the newest 100 registration jobs, so a print job never drops off the
  list. All of it is read-only.
- `assignments` rows are retained identities (`assignment_immutable` trigger): id, course, key and creation never change.
  The TITLE changes only by a recorded rename (requirement, 2026-09-27; document_identity
  migration 010): `DocumentService.rename_assignment` (POST `/api/documents/assignments/<id>/rename` with `title` and
  `request_id`; Rename… on the assignment card) appends an `assignment_names` row keeping the previous title, and the trigger
  refuses any title that is not the latest recorded name. Every page then shows the new title; what was printed, sent or recorded
  keeps its text; an exam record still titled like the assignment takes the new title with a history entry. Titles stay unique
  per course. `GET /api/seating/attachable` compares an exam's charts of one
  term with the chosen sections and typed title without writing. `POST
  /api/seating/attach` (`SeatingService.move_plan`) moves every chart of that
  exam and term to another assignment of the course and relinks the earlier
  assignment's DR bookings of that term only (`ExamLinkService.relink` with
  `term_id`; another term's charts keep their exclusions), recording
  `exam_changed` seating events and a `seating_charts_attached` document
  event. It refuses (409) a chart that is confirmed or referenced by print
  batches, response-card issues, student chart mail or an exam event, a partial
  plan, another course, or a section the target already seats. Keeping the
  chart's name means continuing its assignment; nothing is created or moved.
- Follow `document_identity/README.md` for operation, migration, backups, and tests.
  Keep `document_identity/physical-qualification.md` honest: simulated scans cannot
  satisfy the required 30-page classroom printer/scanner qualification.
- Print email exports reference their existing section- or term-owned batch and validated
  PDF; recipient preferences are workspace configuration.
  Only an explicit Send email action in Faculty approves the exact recipient
  and PDF hash. Persist provider receipts before completion; uncertain/interrupted
  sends must never be automatically retried. Custom export recipients do not gain
  Inbox submission permission.
- PRINT EMAIL FROM GMAIL (requirement, 2026-11-01, of Export by email, the last mail still sent through Resend: "move that to
  gmail as well"). An export is sent by the instructor's connected Gmail, the same connection Response Card mail and seating
  chart PDFs use (`response_cards.mail.gmail`), as that account, with the PDF attached as `print-<batch>.pdf`. With Gmail not
  connected the dialog says to connect it on the Response Cards page and Send stays disabled; nothing falls back to the
  print-export relay or Resend. A send Google never took (not connected, a rate limit, Google unreachable, a rejected address)
  is "Not sent" with Gmail's reason; anything else is "Delivery unconfirmed" and is never retried by itself. The receipt keeps
  Gmail's message ID with `provider: "gmail"`. Exports before 2026-11-01 went through `cloudflare/teach-inbox/PRINT_EXPORT.md`.
- NAME FILLED IN (requirement, 2026-10-18: "it should fill out that student's name in the name spot on the document"; migration
  017, `printed_copy_name_fills`, owner the printed copy). A job made with `fill_names` (only with named students, never with a
  seating chart) keeps each named copy's roster name, read once when the job is made, and prints it in Helvetica 11 pt on every
  page of that copy whose text has a whole word "Name": just after the label (and its colon), on its baseline, up to the next
  word on that line (a quiz's "UIN:") or 3 inches, cut with an ellipsis if longer. A page without such a line gets nothing;
  nothing is printed in a stamp box. The kept name is part of the job's manifest (`name_fill`), so the retained PDF keeps
  verifying; jobs and copies without one keep their manifest shape. Used by booked makeup copies (ABSENCES "COPIES"); POST
  `/api/documents/batches` takes `fill_names` too (a named copy made by hand, e.g. a makeup copy made before the name fill).
- A STUDENT'S EMAILED COPY is never printed again: Print at Home and Export by email refuse a job that QUIZZES "A COPY BY EMAIL"
  made for one student once Mail has recorded a send attaching it.
- SEND TO PRINTER (requirement, 2026-10-18: "can we set up faculty so that it can send prints to the brother printer
  directly?"; `document_identity/printer.py`, `ipp.py`, migration 016). The office printer (Brother MFC-L5915DW on the home
  network) takes PDFs over IPP itself, so Faculty sends to it directly; ml-box needs no CUPS queue, driver or sudo. The printer
  is workspace configuration: `FACULTY_PRINTER_URI` (plain `ipp://` to a private address or a `.local` name only; unset, nothing
  is offered and checks never reach it). The button and window say "Print at Home" (requirement, 2026-10-18: "so I know it
  explicitly means to print on my personal printer"); it sits beside the file it prints: a job's PDF, a stack's interleaved PDF
  (ONE STACK, EVERY VERSION) or a seating chart's whole-room PDF; never labels. The same files opened in the file viewer (the
  Makeups row's Print PDF, say) carry Print at Home beside Download (requirement, 2026-10-18: "this doesn't have the print at
  home option"; `file-viewer.js` `printableRoute`), opening the same window, whose Back returns to the file. A send references its print jobs
  (`print_printer_send_batches`, the jobs' owners) and is bound, by its request ID, to those jobs' verified output digests,
  the sides and the printer; nothing is copied. Only the instructor's Print at Home approves it; the assistant needs an Approve card. AUTOMATIC MAKEUP PRINTS (instructor,
  2026-10-18: "I want the app to automatically print booked makeups") are the one exception: a booked student's makeup print
  job (ABSENCES "COPIES", `absence_makeup_copies`), once ready and while its sitting's day has not passed, is sent by the makeup
  worker as actor `automatic`, whole, two-sided when allowed, once; a send the printer refused or never answered (nothing
  printed) is tried again after 15 minutes until the sitting's day ends, an unconfirmed one never, and a job the instructor
  already sent is never sent again.
  It prints once (printer copies 1), black and white, at actual size; two-sided only when every copy has an even page count, so
  no two copies share a sheet. A file over ~40 MB goes as several documents of one printer job, cut between copies (the
  printer holds at most 62.5 MB of PDF). The worker commits the printer's receipt (its job ID) before completion. A printer
  that refuses or never answers the job prints nothing (failed); a dropped or unconfirmed document send, or an interrupted
  send, is "unconfirmed", is cancelled on the printer where possible, and is never resent automatically: printing twice
  duplicates the printed page identities, so the window warns when the same jobs were already sent.
- External-drive export runs in the laptop browser using its granted directory
  handle, or a one-export file handle from Save PDF as. Only the directory handle
  is a browser/origin preference in IndexedDB, never a server
  path or new catalog owner. Fetch the section's existing print
  artifact and verify its SHA-256 before writing and after closing the local file.
  Preserve different existing files. Never report a normal download, denied
  permission, or failed drive write as a successful USB export. A picker AbortError
  may mean blocked access; never assume the user cancelled. Single-file fallback
  requires its own click and must not replace the remembered folder.
