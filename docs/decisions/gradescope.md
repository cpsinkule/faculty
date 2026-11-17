## Sending grades to Gradescope

Rule ID: **GRADESCOPE**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor lifted the ban on Gradescope's private web endpoints (2026-09-25: "there's no way I can manually put in all
  these grades"). Faculty may talk to Gradescope as the instructor's signed-in browser would, ONLY for this: read courses,
  assignments, outlines, rubrics, rosters and submissions; upload each student's reviewed paper to the Gradescope assignment of
  that student's exam version, already matched to that student; and check the rubric items of Faculty's final grade on it.
  Since 2026-09-27 also CREATE the assignment there (instructor: "the option to create a new assignment in gradescope in case I
  haven't made one there yet"; "it should not just be for exams, but for general assignments"), below. Since 2026-09-30 also READ
  regrade requests and their replies for the communications record (see "Communications record"; reads only), and PUT the
  instructor's REPLY to a regrade request, which answers and closes it and changes no grade (regrades.md); a regrade request's
  Apply sends that student's ONE grade first (ONE RESPONSE, below). Since 2026-10-02 also
  PUBLISH a plan's grades there, only on the instructor's explicit request (instructor: "I want the option to publish grades on
  gradescope from the certification page"; PUBLISHING GRADES, below). Since 2026-10-09 two writes happen BY THEMSELVES, on the
  instructor's word: a settled makeup version added to the linked sets (A VERSION THE SET LACKS) and a late paper Faculty
  certified sent as its section was (AUTOMATIC LATE-PAPER SEND). Since 2026-10-12 also what follows a certification (AFTER
  CERTIFYING, below): after the instructor's Certify with its Gradescope box ticked, Faculty makes the assignment where a section's
  linked course has none, sends what is missing and publishes each section that is fully graded there; and it publishes a late
  paper's version where its set is already published. Everything else still starts from the instructor's click.
- The **Gradescope** panel of the grading Certify step (`static/grading-gradescope.js`; routes `/api/grading/gradescope/...`,
  `grading/gradescope_api.py`) is its only home (instructor's choice). Its headline ("40 of 42 papers · 500
  of 520 final grades are on Gradescope", `GradescopeSender.status` `coverage`, 2026-09-26) counts from Faculty's own
  receipts without contacting Gradescope: a final grade counts as sent only while its latest accepted receipt holds
  exactly the current decision and result. `grading/gradescope_web.py` is the transport and page
  reading, `grading/gradescope_sync.py` (`GradescopeSender`) decides. A PREVIEW (POST, writes nothing anywhere) reads Gradescope
  and Faculty and says per section, version and question what would go and why anything would not; the SEND names its
  fingerprint with `request_id` and `confirmation: "send"` (all checked on the server), reads everything again (409 when anything
  changed, in Faculty or on Gradescope) and records the links, the send and one pending unit per paper and per grade. The
  sender thread (`GradescopeSender.worker`, started beside the Inbox worker loop, its own lock `LOCK`, never the execution
  lock or the lifecycle lock across a request) works on `PARALLEL` (24) units at once, uploads included (`UPLOADS_AT_ONCE` 24; instructor,
  2026-09-26, raised from 8, after sandbox tests answered 1 to 24 parallel grade sends, 18 to 231 grades a minute, and 1 to 8 parallel
  12-page uploads, up to 59 papers a minute, without one refusal; 24 uploads at once were never tried, the busy rule below is the net). A grade whose paper Gradescope is still processing (`Processing`, 5 to 11 s
  measured) waits `PROCESSING_WAIT` (15 s), not a minute. Each thread has its own connection on ONE sign-in (`Session(share=...)`): the connections
  sign in again once for all (`login` under the group's lock), a refused sign-in is never tried again by another connection, and none
  signs in during the pause. A unit in flight is never chosen again and is still claimed before anything leaves; one that stops
  unexpectedly is settled by reading, like a unit of a stopped process. Tests and `drain()` stay one at a time unless `parallel`.
  Cancel stops what waits; Retry is explicit and only
  for units Faculty READ as not kept. A section may be LEFT OUT of a send (requirement, 2026-09-25: send one section first and
  check it): `targets[section] = {leave_out: true}`, part of the fingerprint; its course is not read, nothing of it is sent,
  no link is recorded, its papers and grades are counted apart (`left_out`), and the next preview offers it again.
- THE BUBBLE SHEETS TOO (requirement, 2026-10-26: "send will send the bubblesheets too?", then "would it be bad to have the send
  function send both?", then "yes"). The written Send dialog (papers and grades, or papers only) reads the Bubble
  sheets page's own preview beside its own and shows a Bubble sheets part when the plan has sheets read: their counts, each
  section's problem (choose its bubble-sheet exam on the Bubble sheets page), the skipped sheets, and a tick, ON by default, "Also
  upload N bubble sheets to the bubble-sheet exam, before the papers". The confirmation names them. Ticked, the send request names
  `sheets` {fingerprint, targets, published} of the sheet preview shown, and the server records that bubble-sheet send exactly as
  the Bubble sheets page's Send would (`sheet_send`: its fingerprint, published confirmation, one live send, its own receipts),
  AFTER the written send passed every check of its own and just BEFORE recording it, under the request ID
  `uuid5("faculty-sheets:<request ID>")`: a repeated request returns both sends already recorded, a written send refused records no
  sheets, and a sheet send refused (409) records nothing. Sheets of a published bubble-sheet exam ask in their own popup. When
  only the sheets have anything to send, the dialog sends them alone through the Bubble sheets page's own route. `sheets` is not
  in the written request's hash, so a send without it hashes as it always did. Nothing is published either way; the Bubble sheets
  page keeps its own Send.
- THE SAME KIND AND NUMBER (requirement, 2026-10-26, "Proceed", after Exam 2's send proposed "Quiz 2", the only assignment of the
  course with a 2 in it, before Exam 2 was on Gradescope, and skipped all 312 papers against its two-page template). The send and
  the Bubble sheets send PROPOSE an unlinked section's assignment only when exactly one fits and its title carries a number of the
  plan's title AND names no other kind of work (`gradescope_sync.fits_title`: exam, test, midterm and final are one kind, quiz
  another, homework another; a title naming no kind is judged by its number alone, as before). The instructor may still choose
  any assignment. Faculty's own "may be this one" check before making an assignment by itself (`_alike`, AFTER CERTIFYING) keeps
  the number alone, so it stays as cautious as it was.
- Owners (migration 026): a section's Gradescope course is SECTION-owned (`frq_gradescope_courses`, latest row counts); the
  exam a plan's section and version go to, every send and every upload/grade receipt belong to the TERM grading plan
  (`frq_gradescope_targets`, `_sends`, `_uploads`, `_grades`). THE SECTION'S COURSE IS SET ONCE (requirement, 2026-09-27: "have
  each course/section linked to its specific gradescope course instead of ... asking me to pick each time"): a section tab's work
  list shows its Gradescope course with Change… (GET `/api/grading/gradescope/sections/<id>` reads Faculty only; `/courses` reads
  Gradescope's list and proposes by `propose_course`; POST records a new `frq_gradescope_courses` row with `base`, 409 when stale,
  only for a course Gradescope lists and never the coordinator's). The send, sheet and create dialogs show a linked course
  (`linked` in the preview) as a fact with Change…, a picker only for unlinked sections; a send still records its links as before.
  The course list is still read once per preview: it is the guard that a course is the instructor's. Receipts keep Gradescope's own IDs, the paper's assembly
  revision and the uploaded file's sha256, never a UIN, name, password, cookie or PDF. Inputs and receipts never change;
  settled units never change; one live send per plan.
- What is sent: only FINAL grades by the export's own rule (`ReviewMixin.final_decisions`, which `export` also uses; a grade is
  checked again just before it is written, with `final_decisions(..., response_id)`). A paper is the student's current reviewed
  assembly in paper order (a sheet fed back side first goes front first), every page's checksum verified, merged in the document
  sandbox with each page grading found UPSIDE DOWN turned a half turn (requirement, 2026-09-27: "pages got uploaded to gradescope
  upside down if the original scan had them upside down"; `_turned_pages`: the paper's own whole-paper version check where one
  exists, else only a page EVERY crop grading cut from agrees was turned over; lossless page rotation, the retained scan never
  changes; the receipt names the pages turned), uploaded AS that student (roster SID = UIN exactly,
  never guessed; none or several is a skip) to the exam of THEIR version, as `faculty-<receipt id>.pdf` so Faculty can always
  tell its own upload apart. Page counts must equal the Gradescope template's. A student with a paper there that Faculty did not
  upload is left alone, grades included (that paper may not be the one Faculty graded).
- Rubric items are matched by the Gradescope export's numbering, which Faculty's imported lines keep (`rubric_number` "q.i"):
  the question is Gradescope's question q (its title must print Faculty's question number and its worth must equal Faculty's),
  the item is the i-th in export order (`gradescope_web.export_order`: top-level items and groups by position, a group's items
  by position; checked on all 890 Test 1 lines of every section, 2026-09-25) and must be worth the same; different wording is
  only a warning. Rubrics that did not come from that export (drafted, adapted) are refused per question, unless Faculty made that question on Gradescope (matched by its receipt, below). Gradescope's score
  for the chosen items and adjustment (`gradescope_score`: its scoring type, floor and ceiling) must EQUAL Faculty's final score,
  else that grade is refused; a grade with no line checked is refused (it looks ungraded there). Adjustments go as Gradescope's
  point adjustment, together with what a rubric's part caps gave back (the score's `part_caps`, GRADING "PART CAPS", 2026-10-15:
  Gradescope cannot cap a part), so a capped grade shows Faculty's score there; nothing is commented for it. The ONLY comment ever sent with a grade is the grade's batch lines from Certify, and on a plan graded for participation its
  participation line, "+2.5 Graded for participation", sent with the grade's point adjustment and counted in `expected_comments`
  like a batch line (GRADING "GRADED FOR PARTICIPATION", 2026-11-01) (a regrade
  reply's `staff_comment` is not a grade comment; regrades.md) (requirement, 2026-09-27:
  "the additional line item doesn't show the comment in gradescope"; asked, they chose batch lines only): each adjustment that
  matches a line a batch added or set on that response (`batch_adjustments.batch_lines`, `comment_text`), one row each,
  "+1.5 Misprint in part (c)". A manual grade's reason, an adjustment typed at the review desk and Faculty's automatic notes are
  never sent. The comment is part of the grade: it is in the unit (`items_json.comment`), the fingerprint and the read-back
  (compared as `gradescope_web.comment` normalizes it), and a grade whose accepted receipt carries another comment than the one it
  should (sent before 2026-09-27, or its lines edited or removed) counts as changed since sent (`expected_comments`); a comment
  Faculty wrote and the grade no longer has is cleared with an empty comment when Faculty replaces its own grade.
- IMPORTING RUBRICS FROM GRADESCOPE (`grading/gradescope_rubric_import.py`, `static/grading-gradescope-rubrics.js`; instructor,
  2026-10-24: "I want to be able to import rubrics directly from gradescope instead of me having upload them"). **Import from
  Gradescope** on Rubrics & Templates (beside Upload rubrics) is the export's CSV upload without the download: a READ of Gradescope
  only (the course list, the course's assignments, the chosen assignment's Edit Rubric page per version), never a write there. The
  dialog proposes the first section's linked course (else `propose_course`) and the assignment its sections are linked to (else the
  only one titled as the plan), and lists each whole top-level question with its points, item count and scoring, ticking those it
  can import that Faculty does not have yet; a question with parts, no items, an item without wording, or points Faculty's rubric
  rules refuse is listed with its reason and cannot be ticked. A version set imports every version under its letter; an assignment
  without versions describes the version the instructor names, as a CSV upload does. Import (POST `.../rubrics/import`, reading
  Gradescope again) turns each ticked question into exactly the per-question evaluation CSV the export would give (`export_csv`: the
  question total under Score, the scoring method, every item in `export_order` with its export number "q.i" and a group's items
  worded "Group: item"; a deduction under negative scoring printed negated, as Faculty's negative rubrics hold it) named as the
  export names it ("3_Question_7.csv", the printed number from the start of the title only, `printed_from_title`), and gives it to
  the ordinary rubric upload (`upload_rubric`): the same retained source, draft question, `frq_rubric_uploads` receipt (request
  `<request>:<Gradescope question>`, so Import again repeats nothing), refusals and later steps; nothing is approved. Because the
  numbering is the export's, the send matches its items as for an uploaded export (above). Matching compares a Faculty deduction
  under negative scoring with Gradescope's item weight negated (`map_question`), as Create sends it (`line_weight`).
  THE TEMPLATE AND ANSWER AREAS COME TOO (requirement, 2026-10-24: "the answer areas are imported as well if the assignment has an
  accompanying outline with it and also the entire scan as our template"). With **Also import the blank template and answer areas**
  (ticked by default; `areas: false` leaves them out) each version's outline is read as well: its finished template is read from
  the signed link the outline gives (`Session.template_pdf`: only Gradescope's uploads store on S3, `UPLOADS_HOST`, or a loopback
  test server; no cookie, no redirect, a PDF of at most 25 MB) and saved through the ordinary template upload as one of the
  assignment's Assignment Templates ("<title> · Gradescope template"; request ID from the course, assignment, Gradescope template
  and the file's sha256, so the same file is saved once), and each question's boxes (`crop_rect_list`, percent of the page) become
  its answer areas on it (`crop_regions`, page fractions through `locations.validate_regions`), with `regions_from: "gradescope"`
  on the new question only. Only a template whose page count is the printed document's (or any for a label-sheet document) is
  used; otherwise, and when the template cannot be read or saved, the rubric is imported alone and the import says why. Nothing is
  confirmed by the import: the template is the slot's default choice on Assignment Templates (confirmed there as any), and the
  answer-area check still reads the blank for the printed number and points but suggests Gradescope's boxes instead of its own
  (`location_context` `gradescope_regions`, present only for such a question so no other check's input changes; `locations.combine`),
  confirmed as any suggestion is (by the instructor, or by itself, GRADING "ANSWER AREAS CONFIRM THEMSELVES"). A new template choice
  or other confirmed areas drop the mark.
  THE BUBBLE SHEET KEY COMES TOO (requirement, 2026-10-25: "I also want to be able to import the bubble sheet key from there as
  well"). The dialog lists the course's bubble-sheet exams after its written assignments ("… (bubble sheet answer key)"), and the
  Bubble sheets page's key has **Import from Gradescope** (the same dialog, bubble-sheet exams only). For one, Faculty reads its
  versions from the set's Edit Exam Version Set page (`version_set`; no student is on it; a plain bubble-sheet assignment is
  Version A) and each version's outline, lettered by its own title ("… Version B"; Gradescope's "?" version is left out), whose
  questions hold the key (`data.mc_autograder_correct_answer_letters`, `mc_autograder_scoring_type`, the question's weight;
  `bubble_questions`): each question with letters, numbered from its title else its place, its letters, points and scoring (exact,
  partial and either, Faculty's rules of the same names). Letters past E, another scoring type, odd points, a number twice or
  versions not lettered A, B, C … in order stop the import with their reasons. Import (POST `.../rubrics/bubble-key`, reading
  Gradescope again) saves it through the key editor's own Save (`save_bubble_key`): a new revision of the whole key, the most usual
  points and scoring as its defaults, `base_revision` the revision the dialog showed (409 when another save came first). Earlier
  revisions are kept; nothing is written to Gradescope. THE EXAM'S KEY COMES WITH ITS RUBRICS (requirement, 2026-10-25: "shouldn't I
  be able to import them as part of exam 2?"): choosing a written assignment proposes the one bubble-sheet exam of the course whose
  title is the same but for its part (`exam_stem`: "Common Exam 2 - Partial Credit" and "- Multiple Choice"), only when its key
  reads without a problem; the dialog shows it under **Also import the answer key of** (another exam or none may be chosen, `sheet_id`;
  a chosen one shows why it cannot import), and Import saves that key after the rubrics as above (`key` in the answer; a key that
  cannot be saved fails alone, the rubrics stay). Deduction lines of a positive rubric import too (GRADING "DEDUCTIONS IN A
  POSITIVE RUBRIC").
- CREATING THE ASSIGNMENT ON GRADESCOPE (`grading/gradescope_create.py`, migration 030, `static/grading-gradescope-create.js`;
  requirement, 2026-09-27; asked, they chose everything, in every section at once). For a term grading plan of ANY kind (quiz,
  homework, worksheet, exam: never exam-only wording), **Create the assignment on Gradescope…** on the panel (and offered in the
  send dialog when a chosen course has no assignment) makes it in every section's own Gradescope course that has none linked:
  one instructor-uploaded assignment per version (Gradescope's "Exam / Quiz" type, a fixed-length paper with a template, whatever
  the paper is), titled with the plan's title (editable) plus "Version A" ... when there are versions, its template the version's
  confirmed blank (`templates.read(id, "pdf")`); one question per Faculty question in printed-number order, its printed number in
  the title, its points, its CONFIRMED answer areas as boxes (percent of the page, one decimal); the question's scoring and floor
  and ceiling on; each rubric line in Faculty's order with its current wording and points (under negative scoring a deduction of -2
  is weight 2); several versions grouped as one version set named with the title (Gradescope letters them A, B ... in order, so
  Faculty's letters must run from A without a gap). No groups, no comments, no name or ID region. A version without a confirmed
  area, rubric or printed number, or questions on two blanks, blocks it with the reason; Faculty bounds or line groups Gradescope
  cannot express are warnings (such a grade is refused when sending, never changed); a part's cap is none, since what it gives back goes as the grade's point adjustment. A course that already has an assignment of
  one of those names is refused per course. The PREVIEW (POST, writes nothing anywhere) reads Faculty and Gradescope; CREATE names
  its fingerprint with `request_id` and `confirmation: "create"` (checked on the server; 409 when anything changed) and records the
  approval (`frq_gradescope_creations`) and one unit per Gradescope course (`frq_gradescope_creation_units`, `send_id` naming its
  creation); both belong to the TERM grading plan. The sender works a unit one step at a time (make one version's assignment,
  wait while Gradescope reads its template, save its outline, one question's scoring and rubric, group the versions), and EVERY
  step reads Gradescope before it writes, so a write whose answer was lost is only read again, never made twice. An assignment
  found under its title counts as Faculty's only while it holds no paper and no set; an outline or rubric unlike what Faculty is
  making stops the unit (`failed`, with the reason) for the instructor's Retry after fixing it there. Faculty deletes NOTHING on
  Gradescope except the default item ("Correct", 0) Gradescope puts on each question it just made (recorded right after the
  outline is saved). Cancel stops what waits; what is made stays. A finished unit writes immutable receipts
  (`frq_gradescope_created_questions`: per Faculty question the Gradescope question and the item each line became, as sent) and
  links each of its sections to the new assignment (`frq_gradescope_targets`), so the next send preview uses it. Grades for such
  a question are matched by that receipt (`map_created`, instead of the export numbering), which is how drafted and adapted
  rubrics can be sent: each current line must still have its item, worth what was sent, worded as Faculty's line is now or as
  sent, and the question must hold nothing else; everything else about a grade is checked as before. Verified in the sandbox
  course 2026-09-27 (every request, then Faculty's own client methods end to end; everything made was deleted afterwards).
- RENAMED ITEMS (requirement, 2026-10-31: "do the small change", after Exam 2 Version B Q25's items Faculty made were renamed on
  Gradescope on 2026-10-29 from the coordinator's "fire" / "leaky roof" to the booklet's "flood" / "structural humidity", and every
  B Q25 grade was then refused: worded neither as sent nor as Faculty's line). `record_item_wordings` (POST
  `/api/grading/gradescope/assignments/<aid>/item-wordings`: `question_id`, `items`, `reason`; an explicit instructor action) READS
  each course's rubric where Faculty made that question and records, per line, the wording Gradescope shows now
  (`frq_gradescope_item_wordings`, migration 051, owned by the term grading plan, append-only), only for the item Faculty's receipt
  names, worth exactly what Faculty's line is (else nothing is recorded); a wording already known is not recorded again. The
  receipt match (`map_created`) then accepts a recorded wording as it accepts the sent one; points and every other check are as
  before. Faculty's rubric is left as it is: correcting a line's wording there is a new rubric meaning, and every grade on it would
  be graded again. Nothing is written to Gradescope.
- THE BUBBLE-SHEET EXAM TOO (requirement, 2026-10-26, of Create the assignment on Gradescope… for Exam 2: "is it going to create the
  MC too or just the FRQ?", asked how big a change it would be, then "go ahead and build it"). It lifts BUBBLE SHEETS ARE UPLOAD ONLY
  for making the exam and its answer key only: still no grade is ever sent for multiple choice. When the plan has an answer key
  (the Bubble sheets page's), the same dialog and the same Create also make Gradescope's Bubble Sheet in every section's course
  where no bubble-sheet exam is linked for the plan (`frq_gradescope_sheet_targets`), named by a second, editable name (default
  "<title> - Multiple Choice", `sheet_title`, in the request hash only when given), with versions named "Version A" … after it.
  The preview's `sheets` part (present only with a key, so a plan without one keeps its fingerprint and answer) shows each version
  with its question count and points, where, the sections already linked, and its OWN problems: a key of one version (Gradescope
  lists a bubble sheet without versions like any assignment, so the Bubble sheets page could not find it: make it there), more
  than five versions (Edit Answer Key's `maxVersions`), a version with no answered question, and per course an assignment of one
  of its names already there. Each part is held back only by its own problems: what the written assignment lacks never holds up
  the bubble-sheet exam, nor the reverse, and only the bubble-sheet exam is made where the written one is already linked. One
  unit per course still (migration 030's `UNIQUE(send_id,course_id)`): a course that gets both makes the written one first, then
  the bubble sheets (`plan_json.sheets`); a course that gets only the bubble sheets has a unit with no written versions. The
  sender, one step at a time, each step reading Gradescope first: Create Assignment with `assignment[bubble_sheet]=true`, no
  template, uploaded by the instructor (`Session.create_bubble_sheet`; found under its name afterwards, it is Faculty's only while
  it is a lone assignment with no sheet); Edit Answer Key's Add Version (`add_bubble_version`, POST JSON to `addVersionUrl`) one
  version per step until the page lists exactly Faculty's version names (the first turns the assignment into Version A of a new set
  of the same name); the set's versions then read from its Manage Submissions page must be Faculty's letters with Version A the
  assignment it made; then every version's key in ONE Save (`save_bubble_key`, PATCH `{bubble_sheet_assignment_answer_key: {"Version
  A": {"1": {answer, scoring_type, weight, rubricsLocked}}}}` to `containerSaveUrl`): each question's letters, Faculty's scoring
  (exact, partial, either) and points (its own, else the key's defaults), read back and compared exactly. A key already there that
  is not Faculty's, versions other than Faculty's, or sheets in it stop the unit for the instructor's Retry; nothing is ever
  deleted, a lost answer is only read again. The finished unit links its sections to the set with its letters, so the Bubble sheets
  page's send uses it. ONLY THE INSTRUCTOR'S CREATE: never after certifying (`linked_only`) nor for a makeup version, so no
  automatic tier widens. Read on the real site 2026-10-26 (Create Assignment's form and `featuresAccess.BUBBLE_SHEET`, Edit Answer
  Key's props and its Save, Add Version and Delete requests in its script, read only in 140-507), then rehearsed with Faculty's own
  client in the sandbox course: "Faculty probe MC" made, two Add Versions gave Versions B and C of set 424926, one Save kept all
  three keys (exact, partial and either; 4, 2.5 and 1 points) and its outline read them back as the rubric import reads a key; then
  Faculty's own sender worked a unit there step by step on a synthetic catalog: "Faculty rehearsal MC" made, Version B added, set
  424929 read as A and B, both keys saved in one Save and read back exactly, the section linked. Both sets are left in the sandbox.
- A VERSION THE SET LACKS (requirement, 2026-10-06, after a makeup's Version E of Quiz 4 warned "Quiz 4 has no version E." on every
  section; asked, they chose both: warn only where it matters, and "Faculty would create 'Quiz 4 Version E' on Gradescope ... and
  add it to the Quiz 4 set in the sections that need it"). What a section NEEDS is the versions its current papers are in, decided
  versions only (`gradescope_sync.paper_letters`). The send preview says a section's exam "has no version X" only for a needed
  version (a version of the plan no paper of the section is in is no problem there) and names the lettered ones a version set
  could get (`missing_versions`); the send dialog then offers **Add Version X on Gradescope…**, which opens Create the assignment
  on Gradescope with the dialog's choices. There, a section already linked to a version set that lacks a needed version is an
  ADD unit of its Gradescope course: Faculty makes the missing version exactly as above (named after the dialog's title plus
  "Version X"), then saves it into the existing set through the set's own Edit page (`Session.version_set` / `save_version_set`:
  its form PATCHes the set with `assignment_ids`, the comma-joined IDs of its versions in version order, every other field as
  the page holds it). Gradescope letters a set's versions by that order, so the set must run from A without a gap and Faculty
  adds every letter up to the needed one (each a version of the plan), after the set's own; otherwise the section says why and
  nothing is made. The step reads before and after: the set must still have exactly the versions the preview showed, its Edit
  page must list them in that order and offer the new assignment; afterwards the set must hold its old versions unchanged plus
  the new ones under their letters, else the unit stops for the instructor. A Save whose answer was lost is only read again.
  Only the added versions' problems and warnings count (a version already on Gradescope holds nothing up); a published set
  gets a warning that the new version is added unpublished. The finished unit writes receipts for the new questions only and
  links the sections to the set with all its versions. Nothing else of the set changes; Faculty never removes a version.
  BY ITSELF FOR A MAKEUP (requirement, 2026-10-09: "When the makeup version is approved (ie, everything is settled up to the point
  grading begins), it should automatically create that version in all sections' gradescopes"): the sender looks once a minute
  (`_makeup_next`, catalog only) for a current plan's makeup version whose approval is carried out (`approved_makeup_versions`)
  and that is settled up to grading (GRADING `makeup_settled`), for a plan some section already links on Gradescope. It then plans
  with `makeup` set (`_creation_plan(..., makeup=X)`, in the sender with its own sign-in, pauses and busy waits): every section
  whose LINKED set lacks X needs it, whether or not a paper is in it yet ("all sections"); only ADD units, never a whole new
  assignment, a proposed course or a proposed set; a set must already hold every letter before X. It is recorded as an ordinary
  creation with actor `automatic` and `plan_json.automatic` {letter, skipped sections and why}, under request ID
  `uuid5("auto-version:<plan>:<letter>")`, so it is made once; its units then follow every rule above. When nothing can be added
  (no linked set lacks it, a set that cannot continue, a problem of the version) it says why once (`gradescope_version_not_added`)
  and leaves the rest to the instructor's Create. One automatic attempt per version; the panel's creation card says Faculty added it,
  and (2026-10-09 review: the reason was recorded and never read) lists each version Faculty could not add with its first reason
  and "Create the assignment on Gradescope… adds it" (`status.automatic.versions_not_added`: the newest event per letter that no
  completed creation of the plan followed; a reason saying the set already has the version is left out). Nothing is retried.
  The Edit page and its form were read in the sandbox course on 2026-09-27 (`edit_assignment_container_<id>`). Rehearsed there
  2026-10-07 with Faculty's own client (`version_set` / `save_version_set`): a set of A and B with a paper in A, a third
  assignment saved after them answered 302 and became "Version C"; A and B kept their IDs and letters, the paper stayed, the Edit
  page then listed all three in order and the send read the set as A, B, C. Everything made was deleted afterwards.
- PAPERS WITHOUT GRADES (requirement, 2026-09-28: "the ability to send the assignment and rubric to gradescope without sending the
  grades"; asked, they chose uploading the papers too). The send dialog's "What to send" choice (**Upload papers only…** on the
  panel opens it on Papers only) sends `papers_only: true` with the preview and the send. It is the ordinary send with the SAME
  paper rules and receipts, minus every grade: no grade unit is planned, no rubric is read or matched, nothing is ticked. The flag
  is in the fingerprint and the request hash only when true (a send of papers and grades keeps the ones it always had), and the
  send's record and event say `papers_only`. A later ordinary send finds those papers already there (Faculty's own) and sends
  only the grades. The assignment and its rubric are made by Create the assignment on Gradescope, above; papers only never makes one.
- REPLACING OTHERS' GRADES (requirement, 2026-10-01: "I want to be able to overwrite gradescope scores when I send scores to
  gradescope (not always, but I want the option)", after the 508 TA graded Quiz 3 on Gradescope over Faculty's papers-only
  upload). The send dialog's **Replace grades someone else made on Gradescope** box, off in every new dialog and unavailable with
  papers only, sends `replace_others: true` with the preview and the send (in the fingerprint and the request hash only when true;
  without it the preview and send are exactly what they were). The preview then reads, for every grade on a paper Faculty
  uploaded earlier, the Gradescope question's list of submissions (`/courses/C/questions/Q/submissions`, table
  `question_submissions`: who last graded each, score, graded; read on the real site 2026-10-01, `gw.question_rows`, strict) and
  the grading page of each one graded there. A graded question showing Faculty's final grade is recorded as on Gradescope (counted
  `same` when Faculty never sent it); one showing what Faculty itself last wrote is replaced as before (when the list shows
  Faculty's own sent score it is taken as unchanged and not opened: the list shows scores, not items); anything else is a
  REPLACEMENT: the grade unit carries exactly what Gradescope showed (`items_json.others`: items, adjustment, comment, score and
  who last graded it there) and counts as `replace` instead of `send`, `update` or `already`. The preview lists every replacement
  (student, question, the score there, who graded it, Faculty's) and `replacing` totals them (`grades`, `score_changes`, `same`,
  `by` grader). `same_by` names who graded the `same` ones there (the list page's grader), and the dialog says they were graded
  on Gradescope by someone else exactly as Faculty graded them, naming who: nothing changes there (requirement, 2026-10-02: the
  earlier wording, "already on Gradescope as Faculty graded them", read as grades Faculty had sent, for a Quiz 3 sent papers only). Send opens a popup naming them; the send names the number confirmed in `replacing` (`check_replacing`, on the
  server: a missing or different number is refused). The sender replaces a grade only while Gradescope still shows exactly what
  the preview showed (`_others`); a grade changed there after the preview is left alone (`present`). The send does NOT read every
  graded question again (requirement, 2026-10-01, "yes, make that change": that second read doubled the wait, and a TA grading
  meanwhile made the whole send stale): the preview gives every replacement as shown (`replacements`: [response ID, what Gradescope showed]), the send
  carries them back (`shown_replacements`), and its plan takes them as shown (`_as_shown`); they are in the fingerprint, so a list
  other than the preview's, or one of another length, makes the send stale. The send route alone takes up to 16 MiB for them
  (2026-10-02: about 200 replacements were over the ordinary 32 KiB, refused as too large). The rest of the plan is read again as for any send.
  The confirmed number is therefore at most what is replaced: a grade a TA changes after the preview is left alone and listed
  with the send's problems, and one a TA makes on an ungraded question is left alone as without the choice. A send that carries
  no `replacements` (a dialog opened before this change) reads Gradescope again as its preview did. The receipt of a replacement
  keeps what was replaced (`receipt_json.replaced`). The send's record and event say `replace_others` and `replacing`. Reads run
  on up to 8 connections of the preview's one sign-in (`PREVIEW_READS`). Bubble sheets are unaffected (upload only).
- Writes onto an exam whose grades are PUBLISHED only with the instructor's confirmation of that exam (requirement, 2026-09-26:
  "instead of preventing me from exporting when grades are published, can you allow it but have a confirmation popup/warning to
  confirm"; papers, grades and bubble sheets alike). The preview warns per section (`published`, `warning`; publication is in the
  fingerprint) and lists the published exams this send would write to, with counts; Send opens a popup naming them, and the send
  names each confirmed exam ID in `published` (`check_published`, on the server: a missing one is refused, the popup is not the
  control). The send keeps every assignment ID those exams cover (`plan_json.published`), and the sender checks publication before
  every write as before, writing to a published exam only for a send that confirmed it: an exam published AFTER the approval
  still gets nothing (`PUBLISHED`; preview and send again to confirm). A send never publishes (PUBLISHING GRADES is the instructor's
  separate request). Never over a submission
  someone else made: the paper's owner and file are read again before EVERY grade (a paper reassigned or replaced on Gradescope
  while its grades go gets no more of them, 2026-10-01 audit). Never over a grade someone else made unless the instructor chose
  to replace such grades for that send (REPLACING OTHERS' GRADES, below); otherwise Faculty replaces only its OWN earlier grade,
  and only while Gradescope still shows exactly that. Every
  write is READ BACK and is `accepted` only when the page shows it (an upload answers 302 either way; a save once answered 404
  and had saved). A request whose outcome is unknown is `uncertain` and settled by reading (its own file or grade there:
  accepted; nothing: failed), never by sending again; a unit still `sending` when the sender starts is uncertain. A read or
  sign-in that fails before anything was written waits a minute (`WAIT`) and tries again. Grades wait for Gradescope to make
  the question submissions after an upload.
- ONE RESPONSE (requirement, 2026-10-07, "ensure that the result of my decision is reflected in the gradebook and also in
  gradescope"; REGRADES "APPLY"). The preview and the send take `only_responses: [one response ID]` (never with papers only): the
  plan reads that student and that grade alone (`final_decisions(response_id=...)`, the participant filtered, only their version
  read), never uploads a paper (one Faculty did not upload is skipped: the grade goes only onto Faculty's own upload), and the send
  refuses anything but exactly one grade unit to the section's linked assignment. `only` joins the fingerprint and the request hash
  only when set (old fingerprints unchanged); `plan_json.only_responses` and the event name it; a finished one-grade send queues
  no MC check. Published exams, other people's grades, one live send per plan and every per-unit check are exactly as for any send.
- AUTOMATIC LATE-PAPER SEND (requirement, 2026-10-09: "It should also automatically send it to gradescope if the assignment uses
  gradescope"; asked, they chose sending to a published Gradescope assignment anyway; GRADING "LATE PAPERS CERTIFY THEMSELVES",
  `grading/gradescope_automatic.py`, migration 045 `frq_gradescope_automatic_sends`). When Faculty certifies a late paper by
  itself, the certification's own transaction queues, per student and kind, what to send: the written paper with its grades where
  Faculty's grades went to Gradescope for that section, the paper only where only papers went (a papers-only send), and the bubble
  sheet where sheets went; nothing where nothing of the plan went for that section or Gradescope is not set up (the certification
  records why, `gradescope_json.automatic`). The sender (`_auto_next`, before the MC check) prepares each row as the dialog would,
  limited to that student (`_faculty`/`_compose` and `_sheet_faculty`/`_sheet_compose` with `student`; `students` joins the
  fingerprint only then), with the section's LINKED course and assignment only (a proposed course or set is skipped, with why), and
  records it as an ordinary send (actor `automatic`, `plan_json.automatic`, request ID `uuid5` of the row) in the same transaction
  that marks the row recorded. A published assignment is written to (the instructor's answer; the receipt says so). The row waits,
  never sending twice, while another send or a publication of the plan runs (read from the catalog first, before the plan is read
  under the lifecycle lock), while the student's version is being added on Gradescope (or is an approved makeup version of the plan
  Faculty has not tried to add yet: `_makeup_next` adds it within a minute; 2026-10-09 review: a send prepared first was skipped
  for good), or while Gradescope is busy or refused the sign-in; it is skipped with why when the student cannot be sent this way
  (not on the roster, page count, nothing left) or after six hours of waiting. Only the student's own version is read on
  Gradescope, and only their section's final grades in Faculty. From the recorded send on, every rule of a send holds: uncertain
  units are settled by reading, failed ones go again only on the instructor's Retry. The panel's card says Faculty sent it; where
  the set is published but the student's version is not (a makeup version Faculty added lands unpublished), the send records
  `automatic.unpublished_version`, its card says "Version E is not published on Gradescope" and Certify's band
  says so too (`gradescope.unpublished_versions`, until a publication of the plan completes after it), each saying "Use Publish
  grades on Gradescope… at the top of Certify" in words, with no button of its own (PUBLISHING GRADES, ONE PLACE TO PUBLISH). Since 2026-10-12 Faculty
  publishes that version by itself once the paper is there (AFTER CERTIFYING, "A LATE PAPER"): the card's and the band's notice
  are then not shown while its run works, publishes or holds the section (the run's own line says which), and stand as before
  only with the switch off.
  WHY IT DID NOT SEND (2026-10-09 review: the reasons were recorded and never shown): on Certify the student's
  Gradescope cell and drawer say "Faculty sends this by itself · Waiting: …" while a row waits, or "Faculty did not send it: …"
  while a skipped row's student is still not on Gradescope (`students[].gradescope.automatic`, only when there is one; "Already
  on Gradescope." and "Nothing to send." say nothing), and the Gradescope fold lists them, a skipped one with Send to
  Gradescope…; `status` carries them as `automatic.sends` (row keys only). Read only: nothing is sent again by itself.
  The Assistant's Automation tab (ASSISTANT "Automation visibility (2026-10-09)") reads these rows and the
  `gradescope_version_not_added` events read-only: recorded sends in its "Done by Faculty" log, rows still waiting as a note,
  and the reason of a skipped send, or of a version not added until a later creation adds it, as its row's blocker.
- AFTER CERTIFYING (requirement, 2026-10-12, when a makeup quiz had been certified and sent by itself and only publishing was left:
  "Can we also automate, in the same way, uploading/publishing to gradescope after it has been initially certified"; asked, they
  chose "First Certify too", "Create it too" and "Hold that section"; `grading/gradescope_after.py` `AfterCertifyingMixin` in
  `GradescopeSender`, migration 046 `frq_gradescope_automatic_runs`, Compute settings "Send to Gradescope and publish after
  certifying", `auto_gradescope`, on by default). It lifts "Faculty never publishes by itself" for exactly this.
  - THE TICK. With the switch on, Gradescope set up and a written or bubble part, the Certify preview says `gradescope.after` and
    the dialog shows one tick, on by default: "Send to Gradescope and publish the grades after certifying". Ticked, the request
    names `gradescope_after: true` (only then, so a request without it hashes as it always did), a Gradescope group left unanswered
    is the decision `send` (one Faculty carries out; "Do not send these" with its reason still stands for its group), and the
    footer beside Certify says "Faculty then sends them to Gradescope and publishes them". Unticked, or not offered, Certify is
    what it was: a `send` decision is refused and nothing goes anywhere by itself. A tick the server does not offer is refused.
    The Assistant cannot certify, so it cannot tick it. DECLINED BEFORE STAYS DECLINED (2026-10-12 review): a group the last
    certification declined with a reason that still covers it (the server's `prefill`) is shown with "Do not send these" ticked
    again (`groupAnswer`), sent only when the instructor unticks it, and a "Do not send" ticked in the dialog stands through
    Check again: Faculty never starts sending by itself what was declined.
  - ONE RUN PER CERTIFICATION, written in the certification's own transaction (`queue_run`): kind `certified` with each group's
    decision, or `late` with the late students' rows (below). The sender looks at it one step at a time (`_run_next`, after the
    late papers' own sends), under its sign-in pauses and busy waits; every step first waits while a creation, send, sheet send,
    publication or late-paper send of the plan is still going (`_run_busy`, catalog only). A step records an ORDINARY creation,
    send or publication with actor `automatic` and `plan_json.automatic` {certification_id, run, after}, under a request ID
    `uuid5("after-<step>:<run>")` so it is made once (a step cut off after recording only moves the run: `_run_earlier`), and that
    record then follows every rule of its kind: read back, uncertain settled by reading, failed only on the instructor's Retry.
    What a step did or why it did nothing is kept in `outcome_json` (section numbers and reasons only); `plan_json` keeps each
    group's decision only (the instructor's reason stays with the certification). Nothing is retried by itself; a finished run
    never changes. A QUEUED RUN CAN GO STALE (`_run_stale`, read before every step; 2026-10-12 review: a run queued while the
    sender could not work must never resume days later): it stops, saying why, when Gradescope is no longer set up, when the
    switch was turned off (the instructor's way to stop what waits), when the instructor certified the whole assignment again
    since (a later certification that is not Faculty's own and amends nothing: it decides, with its own run or none), or when
    `GIVE_UP` (six hours) has passed since its certification.
  - `create`: the instructor's Create with its default title (`_creation_plan(..., linked_only=True)`): a new assignment only in a
    section's LINKED Gradescope course (never a course Faculty would only propose), and a version a linked set lacks. A course
    that already has an assignment of that name is refused as ever, so Faculty never links to something it did not make; and
    by itself it never makes a second assignment beside one that MAY be this one (`_alike`, 2026-10-12 review: a hand-made
    "Common Exam 1 - Partial Credit" beside the plan "Exam 1"): one that has every version letter and carries a number of the
    title, or whose title holds the plan's or is held by it. It says which, and the instructor chooses it with Send to
    Gradescope… or creates the assignment. Skipped when the written group was declined.
  - `sheets`, then `written` (upload first, then the written grades, as the MC check wants): the dialogs' own plans
    (`_sheet_compose`, `_compose`) with every section left out that is not linked (no bubble-sheet exam or assignment chosen:
    never a proposed one) or, for the written part, for which only papers ever went to Gradescope (its grades are made there).
    A published assignment is written to (the instructor ticked publishing). Never `replace_others`.
  - `publish`: `_publish_plan` on the sender's session, then HOLD THAT SECTION: a section's assignment is published only when it
    is clean. Held, with why: Gradescope shows it less than 100% graded (`graded_share`: the row's own share, else the least of
    its versions that hold a paper), or says nothing about a written assignment's share (unknown is not clean); a final grade
    of a paper Faculty uploaded is not there as Faculty has it (`coverage_detail`: changed or not sent); something of this
    run's own send did not go through; a row problem. For bubble sheets, compared ONE SECTION AT A TIME (`_mc_sections`):
    Gradescope holds another sheet than Faculty's, scored a sheet differently from the key, scored a sheet Faculty has no score
    for, has not scored a sheet yet, or its scores could not be read or compared. The run asks for one MC check when the last
    read is older than the newest sheet there and waits for it; while a sheet is not scored yet it reads again a minute later,
    at most `MC_TRIES` (5) reads, then holds (2026-10-12 review: an unscored sheet had counted as agreeing). A student with
    nothing on Gradescope (not on its roster) holds nothing up, and a section with nothing there is simply not published.
    Sections sharing one Gradescope assignment are held together. What is clean is recorded as one publication (the held rows
    as its `leave_out`); a held section waits for the instructor's own Publish grades. A send or a creation of the plan seen
    while the plan was read makes the run look again, never finish silently.
  - A LATE PAPER (kind `late`; GRADING "LATE PAPERS CERTIFY THEMSELVES"): queued with Faculty's own certification when it sends a
    written paper and the switch is on. Its one step publishes the version set the paper went to, only where that set is
    already published (its own row or any of its versions: Gradescope shows a set as unpublished while one version is, as
    Quiz 4 in section 515 did on 2026-10-12 with A to D published and E added later) AND the late paper's own version is not (a makeup version Faculty added lands unpublished; a set the
    instructor has not published, or a paper on a version that is published, needs nothing and gets nothing). Gradescope's
    "Publish All Grades" shows EVERY version not yet published, so the hold rule is read for every paper Faculty uploaded to
    one of those versions, not only the late one (2026-10-12 review: another student's changed grade in the same unpublished
    version would have been published with it).
  - SHOWN. Certify's band (`gradescope.after`, `after_runs`: the latest run of each kind): one quiet line while
    it works, what it published, each held section with why and the words "Use Publish grades on Gradescope… at the top of
    Certify" (the header's own button; no second button, ONE PLACE TO PUBLISH), what it could not
    send, a publication Gradescope refused; a held section, a note or a stop is shown until a later publication of the plan
    finishes. The page keeps following a run that works. The panel's creation, send and publication cards say "by Faculty by
    itself, after you certified". The Assistant's Automation tab has the row "Gradescope after certifying" and its lines in
    "Done by Faculty" (read only).
  - NEVER: unpublishing, emailing students, a section whose course or assignment Faculty would only propose, replacing someone
    else's grade, a second attempt by itself.
- PUBLISHING GRADES (requirement, 2026-10-02: "I want the option to publish grades on gradescope from the certification page"; it
  lifted AGENTS.md's "Never publish on Gradescope" for this request only). `grading/gradescope_publish.py` (`PublishMixin` in
  `GradescopeSender`), migration 040, `static/grading-gradescope-publish.js`: a **Publishing on Gradescope** section, last in the Gradescope fold of Certify,
  shows what was published (each publication's units, Cancel while it runs, Retry of what failed) and names the header's
  button in words; the dialog opens only from the secondary **Publish grades on Gradescope…** in Certify's header, shown while
  Gradescope is set up (requirement, 2026-10-02: "add the button to the header"; a publication started there opens the fold to
  follow it). ONE PLACE TO PUBLISH (requirement, 2026-10-18: "make all of your suggested changes", pipeline review item 10):
  that header button is the only place the instructor publishes from. A notice that a grade is not published yet (the band's
  unpublished-version line, a held section after certifying, an automatic send's card) says "Use Publish grades on
  Gradescope… at the top of Certify" in words and carries no button of its own. Besides it Faculty publishes only by itself after a certification, as AFTER CERTIFYING says, with the
  same plan, units and read-back (`_record_publication`, actor `automatic`). What: each section's LINKED Gradescope assignment
  of the plan (`frq_gradescope_targets`; with versions its version set, published by Gradescope's "Publish All Grades", which
  publishes every version) and its linked bubble-sheet exam (`frq_gradescope_sheet_targets`), one row each; a section with no
  link is a row saying to send first. The PREVIEW (POST `.../publish/preview`, writes nothing anywhere) reads Faculty (the
  certification state, coverage from receipts) and each linked course's assignments list (published, versions, submissions,
  Gradescope's `grading_progress`), never a row the instructor left out (`leave_out: ["written:<section>", "sheets:<section>"]`).
  Already published (the set and every listed version) is shown and skipped; partly published is published whole; none
  submitted is a row problem. NOT CERTIFIED, CHANGED SINCE CERTIFIED, papers, final grades or sheets not on Gradescope as Faculty
  has them, and a share graded there under 100% (said to one decimal, never rounded up to 100 or down to 0: `percent`) are WARNINGS, repeated in the last popup: never refusals (the instructor decides).
  A live send of the plan (written or sheets) or a live creation is a problem; and a send waits while a publication is live
  (`PUBLISHING`, checked in its transaction): a grade written onto an assignment published under it would stop at `PUBLISHED`.
  The fingerprint pins rows, targets, published states, problems, warnings and certification; Gradescope's graded share and
  submission counts are shown, not pinned (a TA grading there meanwhile does not make it stale). PUBLISH (POST `.../publish`)
  names `request_id`, the fingerprint, `confirmation: "publish"` and the same `leave_out`; everything is read again (409 when
  changed); the approval (`frq_gradescope_publications`) and one unit per Gradescope assignment (`frq_gradescope_publication_units`,
  sections sharing one assignment share its unit, and leaving only some of them out is a problem) belong to the TERM grading
  plan; events `gradescope_publish`, `_complete`, `_cancelled`, `_retried`. The page asks once more in a popup naming each
  assignment, its section, part and submissions, with the warnings. The sender works a unit like any other (claimed first, busy
  rules, `_recover`): it READS the assignments list (already published: `present`, nothing sent, unless its own earlier request
  landed: `accepted`), records `requested_at`, sends Review Grades' own form exactly (`gradescope_web.publish_form`, read on four
  real courses 2026-10-02: the action bar's `review-grades-next-button` form, PATCH `assignment_container[published]=true` for a
  set, PUT `assignment[published]=true` for an assignment; else the modal's `js-publishGradesForm`; hidden fields only, never the
  "Don't show this message again" checkbox; an Unpublish form never matches), and READS BACK: published is `accepted` (receipt:
  what Gradescope showed), not is `failed` for the instructor's Retry; a page without the form is `failed` at once
  (`PublishRefused`); an unanswered request is `uncertain`, settled by reading only. Cancel stops units still waiting; what is
  published stays. Gradescope emails nobody for publishing (its "Email Students" is a separate button Faculty never presses), and
  Faculty never unpublishes. The assistant's tier is `page` (it opens the page; `publish_preview` is `read`).
- A BUSY Gradescope (requirement, 2026-09-25: "wait and retry automatically ... slowing down after each one"; `gw.BUSY`: 429,
  502, 503, 504, never 500) fails nothing. `GradescopeBusy` is raised for reads and writes alike; the whole sender pauses
  (`_pause`: `BUSY_WAIT` 30 s doubling per busy answer in a row to `BUSY_MAX` 15 min, never under a Retry-After, honoured to
  `BUSY_CAP` an hour) and the session doubles its pace (`slow_down`: `CAREFUL` 1 s to `SLOWEST` 5 s) for the rest of the run.
  After a 429 or 503 the write was not done; after a 502 or 504 it is read back first (landed: accepted), and the unit goes
  again by itself after the wait. This is safe only because every try reads Gradescope before it writes: keep that. An
  uncertain unit whose read is busy stays uncertain. `BUSY_TRIES` (20) busy answers in a row fail a unit for Retry. A busy
  sign-in is not `SignInRefused`.
- Credentials: ONLY the file named by `FACULTY_GRADESCOPE_CREDENTIALS_FILE` (no default in code; the drop-ins
  `deploy/faculty{,-inbox}.service.d/gradescope.conf` name `~/.config/faculty/gradescope.json`, which
  `~/.config/faculty/save-gradescope.py` writes 0600 from a terminal). It is read when signing in, never kept, logged or
  returned. It is the instructor's one school sign-in (requirement, 2026-10-01: "those credentials work for most of my school
  accounts, so it uses them for all of the log ins"): iClicker and the NetID sign-in that AIM and Canvas use read the same file
  (iclicker.md, accommodations.md, canvas.md), each sending it only to its own sign-in hosts. Here, Gradescope's cookies and
  the password go only to https://www.gradescope.com (a loopback test server may replace it) and no redirect is followed. Faculty NEVER visits Gradescope's /logout (requirement, 2026-09-25: "I get signed out whenever you use gradescope"):
  it signs the instructor out everywhere, their own browser included. A session is only forgotten (`Session.close`). Signing in
  does not end other sessions (checked on the real site: six at once, and a second sign-in kept the first), so several may be open. The trial gets it only with `manage_trial.py --gradescope`; the preview never; checks strip it
  (`manage_checks`, also `GRADESCOPE_*` and `*_PASSWORD`) and use `tests/fake_gradescope.py`. Rehearse writes in the sandbox
  course "Faculty upload test" (1413803), never first in a real one.
- BUBBLE SHEETS ARE UPLOAD ONLY (making the bubble-sheet exam and its key on Gradescope aside: THE BUBBLE-SHEET EXAM TOO, 2026-10-26)
  (requirement, 2026-09-25: "once the student is matched in faculty, it should upload according to that
  student's uin. the bubble sheet version controls over anything else. separate on bubble sheets page"). The Bubble sheets page has its
  own panel (`mountGradescope(..., "sheets")`, routes `.../assignments/<aid>/sheets...`, `grading/gradescope_sheets.py`, mixed into
  `GradescopeSender`, migration 027: `frq_gradescope_sheet_targets`, `_sends`, `_uploads`, owned as 026's). Each sheet of a scan that
  cleared its scan checks (the Inbox review) and that Faculty matched to a student (exact UIN, confident match or review; never one flagged in doubt) goes
  up as that student (UIN = SID exactly) to the version of the section's Gradescope bubble-sheet exam that the sheet's CURRENT version
  names (read, or recorded in its review); no seating chart or blank exam overrides it, and a sheet without a version waits for its
  review. Gradescope scores it with its own key: NO grade or comment is ever sent for multiple choice, and a score only as MC CORRECTIONS below (THE MC CHECK reads them). A bubble-sheet exam's
  versions are hidden from the assignments list: its own Manage Submissions page names them (`Session.exam`); the "?" version is never
  chosen, and an upload to a version's page stays in that version (checked on the real sandbox, 2026-09-25). The page goes as scanned,
  one page (Faculty reads page 1, questions 1 to 100; a one-page upload to Gradescope's two-page template was accepted there), turned
  upright losslessly when it was read upside down. A sheet whose student, version or review changed after the approval fails at its
  turn. One sheet per student per exam, whatever its version; a sheet someone else put there is left alone.
  A RESCAN IS NOT SKIPPED (requirement, 2026-10-27: "what are all these skipped sheets when trying to send to gradescope exam 2"):
  a sheet that repeats another (BUBBLE-SHEETS "A SHEET SCANNED TWICE") is counted as `summary.sheets.repeats` (named only when
  there is one) and shown as "scanned twice", never listed under Skipped sheets: Exam 2's 49 rescans read "No student is matched
  to this sheet yet" while their first copies were all going up.
- THE MC CHECK (requirement, 2026-10-06: "I want gradescope to grade the MC itself but also faculty to grade it and compare", then
  "upload first, send FR grades, then check MC"; it came from Exam 1, where Gradescope gave 0 for a blank on Version B question 6,
  which the instructor's key gives to everyone). A redundancy check that READS only: `grading/gradescope_mc.py` (`McCheckMixin` in
  `GradescopeSender`), migration 042 `frq_gradescope_mc_checks` (owned by the TERM grading plan, like 026, 027 and 040; one row per
  section read, scores kept per Faculty enrollment by SID = UIN exactly, never a name, email or UIN; settled reads never change).
  WHEN: a written-grades send that completes queues one check (`_settle_sends`: the papers and sheets went first, so Gradescope has
  scored the sheets), and **Check MC now / Check MC again** in the Gradescope panels queues one any time (POST
  `.../assignments/<aid>/mc-check`, 202; one check of a plan waits or runs at a time; a route the assistant policy does not name, so
  an Approve card). The sender works it last, after papers, sheets and grades, one unit per section: it waits while a bubble-sheet
  send of the plan is still running, then reads the section's linked bubble-sheet exam's Download Grades file
  (`gradescope_web.container_scores`: GET `/courses/<c>/assignment_containers/<x>/scores/csv.zip`, read on the real site 2026-10-06:
  one CSV per version, "?" for unread versions, columns SID, Total Score, Status, Submission ID, then "N: Question N (P pts)"; a
  layout Faculty does not know is a failed read, never a guess). Only exams with versions are read (the one layout verified). A busy
  Gradescope or a refused sign-in waits as for any unit; any other failure fails the read with its reason for Check MC again.
  WHAT: every page compares the latest read of each section with Faculty's scoring of the same sheets AS IT IS NOW (`bubble_keys`;
  a key edited since needs no new read): a student DIFFERS when Gradescope filed the sheet under another version or "?", or gave any
  question other points; each difference names the questions with both scores and what Faculty read (a blank on a question keyed
  with every letter says so). Sheets Faculty scored that are not on Gradescope, sheets not graded there yet, and Gradescope rows
  matching no student are counted, not listed; a sheet not graded there whose ungraded questions the read found (MC CORRECTIONS
  UNGRADED) is listed too, those questions as "not graded". WHERE: both Gradescope panels (written grades in Certify, bubble
  sheets on the Bubble sheets page) show it (`mc_check` in their GET, Faculty's records only), ONCE per page: where Certify has the
  bubble-sheets panel beside the written one, only the bubble-sheets panel shows it (requirement, 2026-10-27: "why does it have the
  send bubble sheets functionalities twice"), and a written send started or settled there makes that panel read again, since the
  send carried its sheets (THE BUBBLE SHEETS TOO; it had kept its pre-send "0 of 324" after Exam 2's send); Certification shows a difference as a
  check to CONFIRM (`gradescope_mc_differs`, its rows the students) and an exam on Gradescope never compared as information
  (`gradescope_mc_unchecked`); Certification still never contacts Gradescope. Faculty changes a multiple-choice score on Gradescope
  only as MC CORRECTIONS says.
- MC CORRECTIONS (requirement, 2026-10-22, from Exam 1's two blanks on Q6: "since we now have our own bubble sheet grader, I want to
  just use gradescopes MC grader as a check instead of the ground truth. Basically I want faculty's bubble sheet score to control and
  then flag a discrepancy between faculty and gradescope MC"; asked whether Faculty should correct Gradescope or only flag it: "correct
  it, but also flag the change so I have a chance to manually inspect the discrepancies"). FACULTY'S SCORE IS THE GRADE; Gradescope's
  own scoring is the check. `grading/gradescope_mc_corrections.py` (`McCorrectionMixin` in `GradescopeSender`), migration 048
  `frq_gradescope_mc_corrections` (owned by the TERM grading plan like 042, the student by their section membership like 027; one row
  per question, `read_id` the read that found it; no name, email or UIN). It lifts "NO score is ever sent for multiple choice" for
  exactly this, automatically (an outside write a contract names; the instructor asked for it).
  - WHEN: every MC check read that settles (after a written send, the instructor's Check MC, an AFTER CERTIFYING run's read) queues a
    correction for each question of a student's sheet that Gradescope scored differently from Faculty's scoring as it is now, when both
    filed the sheet under the same version and the sheet is Faculty's accepted upload in that section's exam. Never for a sheet
    Gradescope filed under another version or "?", or a question only one side scores: those stay a difference to confirm. A
    question with a correction waiting or landed for the same score is never queued again; one that failed or was refused is queued
    again only by the instructor's own Check MC (actor `workspace`), never by an automatic read.
  - HOW (read on the real site 2026-10-22, the one approved write, Sophie Shaw's Q6 in 140-508 Version B): a bubble-sheet
    question's grading page is the written questions' `SubmissionGrader` (rubric items "A".."E", "A, B, C, D, E" and "Blank", the one
    Gradescope applied present; `bubble_sheet?` true); its `save_grade` with the applied items unchanged and a point adjustment
    (Blank + 4 = 4) made the sheet 54 and Download Grades 54. The unit (kind `mc_corrections`, worked before the MC reads) reads the
    sheet's submission first (`_still_ours`: still Faculty's file), finds the question by its number in the version's outline and
    its question submission in the upload's receipt, reads the grading page and REFUSES, writing nothing, when someone put a point
    adjustment there (Faculty's OWN adjustment, its latest accepted correction's of that question and sheet, is Faculty's to change
    or take away when its score changes: REGRADES "BUBBLE SHEETS", 2026-10-28), when Gradescope's score changed since the read, when it has not scored the question, or when its bounds cannot
    give Faculty's score. Already Faculty's score is accepted as it is. Otherwise it records the adjustment (Faculty's score minus
    what the applied items give), saves it with the applied items and the comment as they are, and READS BACK: Faculty's score is
    `accepted` (receipt: items, adjustment, score); Gradescope's untouched score is `failed` (it did not land); anything else
    `failed` for the instructor to look at. A lost answer (502, 504, a dropped connection, a stop) is `uncertain`, settled by
    reading only; NOTHING IS RESENT BY ITSELF: the instructor's Check MC tries again with a new row. Busy and sign-in pauses as for
    any unit; a page that will not load before anything is written waits a minute, five times, then fails. A published exam is
    written to (the instructor asked for its scores to be right). Nothing is ever published, unpublished or commented.
  - AFTERWARDS: when the plan's last open correction settles and one landed since the section was last read, the scores are read
    once more (actor `automatic`), so every page compares what Gradescope shows now. AFTER CERTIFYING's publish step waits while a
    correction is open (`_mc_behind` is `running`), so a section is held only for a difference Faculty did not correct.
  - UNGRADED (requirement, 2026-10-28, after Exam 2's 515 sheets held publishing at 99.9%: "it should work by defaulting to
    faculty's scores for ground truth, the MC compare is just to flag things for me to check manually"): Download Grades shows a
    question Gradescope's reader left for a person as 0 and the sheet as Ungraded, so a read looks at each question's grading page
    of a sheet in Faculty's version that is not Graded (at most `UNGRADED_SHEETS`, 12, per section; more means Gradescope is still
    at work) and keeps the questions nobody graded (no evaluation, no item, no score; read on the real site 2026-10-28, 140-515
    Exam 2 Q7 and Q17). Each is queued like a difference (`gradescope_points` empty, shown "not graded"): the unit applies the
    rubric item that IS the answer Faculty read (its description, "B", "A, B, C, D, E", "Blank") with the adjustment that gives
    Faculty's score (none when the item gives it), and reads it back. REFUSED, writing nothing, when someone graded it since the
    read, or when Gradescope has no single item for what Faculty read (two filled bubbles it did not group): grade it there.
  - THE FLAG: `mc_check.corrections` (the Gradescope panels and Certification) lists every correction, the latest per student
    question: name, section, version, question, Gradescope's score and Faculty's, why they differ, and where it is (corrected on
    Gradescope, being corrected, being checked, not corrected with its reason). Certification shows them as a check to CONFIRM,
    "Multiple choice corrected on Gradescope" (`gradescope_mc_corrected`, one row per student; its digest covers each student
    question and Faculty's score), so the instructor looks at each sheet before certifying; a difference every question of which a
    correction covers is `corrected` and leaves `gradescope_mc_differs`, which keeps only what Faculty did not correct. The panels
    poll while a correction is open.
  - SETTLED (requirement, 2026-11-01, of Exam 2's Certify box listing 84 corrections while all 331 sheets agreed: 68 were the four
    Version C makeup sheets, read seconds after upload before Gradescope had graded them; asked whether it should list only
    differences still open: "yes make that change"): a correction is `settled` when it is finished (not pending, sending or
    uncertain), its section was read after it, Gradescope's sheet there is Graded with a score for the question, and the
    comparison no longer lists that question (or the sheet) as differing, whether Faculty's correction landed or Gradescope graded
    it itself. Certification's `gradescope_mc_corrected` lists only corrections that are not settled, and is absent when none is
    left; the panels keep every correction, the settled ones folded under "Earlier corrections Gradescope now agrees with".
- MC LABELS (requirement, 2026-10-28: "a lot of my students are getting confused by the rubrics that were uploaded for the multiple
  choice part of the exam ... it say +0 A and the student chose A, but the correct answer is actually B and they can't tell so they
  think they got it right"). Gradescope's bubble-sheet grader makes one rubric item per answer it meets, named only by the letters
  filled ("A", "A, C", "Blank"; read on the real site 2026-10-28: Exam 2 140-508 Version A Question 1 has "D" worth 4, its key, and
  "A" worth 0, added as sheets came in). **Label answers on Gradescope** (the Multiple choice on Gradescope box of the panels; POST
  `.../assignments/<aid>/mc-labels`, 202; a route the assistant policy does not name, so an Approve card) renames each such item,
  by FACULTY'S key (its score is the grade), to say whether it is right and what the answer is (`answer_label`): "D: correct",
  "A: incorrect (correct answer: D)", "Blank: no answer (correct answer: D)", "A: partial credit (correct answer: A and C)",
  "A: incorrect (correct answer: B or D)" (either/or), "Blank: correct (every answer accepted)". The letters stay first, so
  `answer_letters` (MC CORRECTIONS UNGRADED, REGRADES "BUBBLE SHEETS") reads a labelled item as before. ONLY THE DESCRIPTION
  CHANGES: the PUT of Gradescope's own rubric editor (`Session.rename_rubric_item`, the write of 2026-10-27's Q24 fix) with the item's
  points and group as they are; no grade, score, comment or publication; an item that is not one of Gradescope's answers (a
  person's own, or one in a group), and a version Faculty has no key for, are left alone. `grading/gradescope_mc_labels.py`
  (`McLabelMixin`), migration 050 `frq_gradescope_mc_labels` (owned by the TERM grading plan like 042 and 048; one row per linked
  section of a run, its receipt every item renamed with its old and new name, read back; no student). One run of a plan at a time;
  a run renames only what does not say its label yet, so Label answers again after more sheets went up labels just the new answers.
  A rename whose answer is lost makes the unit `uncertain`, settled by READING only: what did not land is left for the instructor's
  Label answers again (a new run), never renamed again by itself. Busy and sign-in pauses as for any unit; a unit waits while a
  bubble-sheet send of its plan is running.
  - AUTOMATICALLY TOO (requirement, 2026-10-28, asked whether future exams should get these names after each send: "yes for future
    exams"): a bubble-sheet send that completes queues a run (actor `automatic`) that waits ten minutes (`AFTER_SEND`; Gradescope
    grades the sheets and makes their answers' items in the minutes after they arrive; the instructor's Label answers makes a
    waiting run go at once), and an MC check whose last section read settles queues one (answers Gradescope met since). Never while
    a run of the plan is open, never after a run that did not finish (that is the instructor's Label answers again), and only for a
    plan whose bubble-sheet exam was linked from `AUTOMATIC_FROM` (2026-10-28T14:05:44Z) on, or that the instructor labelled once
    themselves: an exam students already saw (Exam 1, Exam 2) is renamed only by the instructor's own Label answers.
- NEVER touch the Gradescope course **Math 140 for Instructors** (requirement, 2026-09-25): it is the course coordinator's
  course, from which every instructor duplicates the rubrics. Skip it wherever courses are listed, and refuse any upload, grade,
  rubric, roster or other request that resolves to it (`Session.guard`), with ONE exception. THE COORDINATOR'S COURSE (instructor,
  2026-10-25: "I don't see the math 140 instructors course from the menu"; asked, "yes, only show for importing purposes"): Import
  from Gradescope lists it last as the coordinator's course (read only), never proposes it, and when the instructor chooses it
  READS its assignments list, Edit Exam Version Set pages, outlines and Edit Rubric pages (`coordinator_read=True`, the import's
  only callers) and its
  templates' signed links; nothing else is ever requested there, and no write, send, upload, link or publication may name it.
