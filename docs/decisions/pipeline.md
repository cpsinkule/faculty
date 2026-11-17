## Every paper goes on unless it is flagged (the automatic scan-to-grade pipeline)

Rule ID: **PIPELINE**. Current binding module contract. Read root `AGENTS.md` first.

- Requirement, 2026-09-28: "I want the pipelines to be eventually automated ... every paper/response etc automatically proceeds to
  the next step unless it gets flagged for some reason needing manual review or there's something in a given step that I need to do
  before it can process (eg nothing can be sent through the grading step because there is no rubric etc, and similar things)".
  A flagged paper, sheet, page or response holds back ONLY ITSELF; a step waits as a whole only for something it cannot do
  without the instructor (an assignment, a rubric, a finalization) or for the worker's own work on it. Answer areas and answer
  keys confirm themselves since 2026-10-22 (below).
  Confirmed 2026-10-01 ("scans should pass review, I want the whole pipeline eventually automated"): scans pass review by
  themselves, and later automation should continue in this direction when requested.
  A multiple-choice question (GRADING MULTIPLE-CHOICE READER, 2026-10-08) needs no step of the instructor's: it is recognized by its
  rubric (automatic confirmation), and an unsure read is graded as written work.
- Scans: the overview marks the few open steps about the WHOLE scan `holds: scan` (no assignment, labels naming two assignments or
  no current paper, labels no longer matching the association); only those and `wait` steps hold its review (`review_blockers`),
  for the instructor's review and `auto_review_one` alike. A scan whose labels name two assignments no longer waits for the
  instructor (2026-11-10), nor does a scan without labels whose first pages print two assignments' titles and whose page counts
  agree (2026-11-11): the worker separates it into one scan per assignment, each going on by itself, and only a scan it
  leaves alone is held, with its reason (INBOX "SEPARATING A SCAN"). `scan_blockers` still lists everything open: a reviewed scan with a
  flagged paper stays under Current, `inbox_ready` stays false and certification still says papers may be in the Inbox. The
  instructor chose (asked, 2026-09-28): doubtful and non-current labels are per-page flags (`recognition`), and automatic
  GROUPING STAYS ALL-OR-NOTHING (`auto_group` unchanged: one held paper still leaves the whole grouping to Papers, the scan's grouping stage).
  The instructor's ways out (2026-10-02): a held paper grouped by hand from the sides they tick, and a stray page set aside as
  part of no paper (DOCUMENTS, SCANNER-INTAKE); each holds back only itself, and a scan whose only open step was such a page has
  nothing open once it is set aside (pages set aside before the first label no longer keep `auto_group` from the rest). A paper
  grouped by hand is settled by the instructor, not a grouping of the scan: `auto_group` then groups the papers that add up by
  itself (every other run, an undone one included, still keeps it away).
  Scan discovery retries that rule after recovered labels finish OCR, including
  previously checked scans; prior grouping and instructor undos remain authoritative.
  Inspection yields after at most twenty extracted pages and twenty bubble detections,
  retaining its existing receipts and releasing the execution lock between turns.
- A page whose codes did not read, between its own copy's labels on paper labeled on every page, takes its page by its place,
  and the empty backs inside that paper are set aside, by the worker after inspection and whenever the scan changes (instructor,
  2026-11-14, of a take-home page whose covered codes made Papers ask for a grouping: DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS").
  A place it cannot decide holds back only that paper, and Papers names the page that did not read, never a grouping.
- Assembly: papers ready at the review become reviewed papers then; papers made ready later follow by themselves (actor
  `automatic`, see `assemble_reviewed`), including copies labeled on every page, which before needed a click per copy.
- Answer areas and answer keys (`grading/auto_prepare.py`, the worker's step `auto_prepare_one`, before `auto_grade_one`;
  requirement, 2026-10-22: "I want answer areas and answer keys to automatically confirm. You can keep everything else as it
  is"): each current assignment whose record changed is looked at (every `RECHECK_EVERY` anyway); its suggested answer areas are
  confirmed as suggested, keys still unread are read, and the keys shown as not saved are saved, all with actor `automatic`
  (GRADING "ANSWER AREAS CONFIRM THEMSELVES", "ANSWER KEYS SAVE THEMSELVES"). A question with a grade, a failed check and
  changed privacy masks wait for the instructor; nothing happens while a batch of the assignment waits or runs. No switch.
- Grading (`grading/auto_grade.py`, workspace `auto_grade`, on by default, "Start grading batches by themselves" in Compute
  settings; never frozen into a batch): the worker starts the batch dialog's default batch (selection `new`, the default mode,
  read and grade, every question with an approved rubric, confirmed answer areas and a standing finalization) once an assignment's
  new work has stayed the same for `SETTLE` (120 s), keyed `auto:<assignment>:<preview fingerprint>`, event actor `automatic`.
  The worker looks again within `LOOK_EVERY` (60 s) of a change to the assignment's record, its exam's reviewed papers, a
  submission choice, or a student named on one of its exam's copies (`_auto_grade_marker`; requirement, 2026-11-06: "papers that
  needed me to manually assign them to a student automatically get graded once I assign them"), and every `RECHECK_EVERY` (15 min)
  regardless: a paper named by hand after its scan went on to grading starts grading about three minutes later.
  Never while a batch of the assignment runs, and never after its last batch was cancelled or reset
  (`auto_grade_paused`): starting a batch themselves resumes it. A refusal is recorded once (`automatic_batch_not_started`) and
  shown on the Grade step. Everything a batch does (preview, fingerprint, retries, flags, review, acceptance) is unchanged.
  The rubric finalization gates batches question by question (see Rubric finalization).
- Flag review (`grading/auto_flag_review.py`, workspace `auto_flag_review`, on by default, "Run the flag review by itself" in
  Compute settings; never frozen into a batch; requirement, 2026-10-11, reversing "keep the button" of 2026-10-03): the worker's
  fifth pipeline step (`auto_flag_review_one`, after `auto_grade_one`) requests the ordinary flag review (GRADING FLAG REVIEW) for
  a finished grading batch's held grades, `SETTLE` (120 s) after its look, keyed `auto:<assignment>:<digest of those grades>`,
  event actor `automatic`. Only grades the button would send (`candidates`: everything reserved to the instructor is left out),
  from a grading batch that finished within `WINDOW` (24 h), never from a flag review's carrier batch or a "Recalculate points"
  batch, and NEVER A GRADE TWICE: a grade any earlier flag review was sent, whether that review settled it, left it, failed or
  was stopped, waits for the instructor or the button. Never while a batch or a flag review of the assignment runs, while a batch
  is about to start by itself, or after the last batch was cancelled or reset. A decision the instructor makes while it settles
  stands: only the grades still waiting are sent. A refusal (no model that reads images) is recorded once
  (`automatic_flag_review_not_started`) and shown on the Review step. Everything a flag review does is unchanged.
  On a plan graded for participation none is sent by itself: what waits for review there is approved as the graders left it, by
  the step after it (`auto_participation_one`; GRADING "THE WAITING GRADES APPROVE THEMSELVES", requirement, 2026-11-11).
- Certification stays the instructor's,
  except a late paper of a student the latest certification gave no grade of their own work (requirement, 2026-10-09): once it
  is graded and cleared and its assignment's activity has settled, the worker's sixth pipeline step (`auto_certify_one`, after
  `auto_flag_review_one`, whose rulings may be what clears the paper; Compute settings "Certify late papers by themselves", on by default) certifies that student alone and queues
  their Gradescope send, holding back only that paper while it is still flagged (GRADING "LATE PAPERS CERTIFY THEMSELVES").
  The same step certifies a makeup's change of a student with no work (requirement, 2026-11-14): the 0 of a makeup that ended, a
  certified 0 left out once a makeup is owed, and a left-out student who dropped (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES").
  What follows a certification on Gradescope (the assignment made, what is missing sent, each clean section published) is
  Faculty's own too since 2026-10-12, on the instructor's tick in the Certify dialog (GRADESCOPE "AFTER CERTIFYING").
