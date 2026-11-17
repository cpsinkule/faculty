## Bubble sheet readings

Rule ID: **BUBBLE-SHEETS**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor grades on Gradescope and asked for a local reader of its bubble
  sheets for their own records and for redundancy. `bubble_sheets` reads filled
  bubbles, the version and the handwritten ID and name. The reader holds no answer
  key and never grades, releases, emails, identifies paper or clears the review
  gate; scoring its readings is `grading/bubble_keys.py` (below).
  Follow `bubble_sheets/README.md`.
- BUBBLE SHEET GRADING (requirement, 2026-09-25: "add bubble sheet grading ... where I
  can click the answers for each version and save as the answer key", like Gradescope's).
  Grading → an assignment → **Bubble sheets** (`?tab=bubble`, `static/grading-bubble-key.js`),
  linked from the context row and the card; it is NOT a stage of the six-stage graph. The
  key belongs to the TERM grading plan (`frq_bubble_keys`, migration 024): immutable
  revisions, each holding every version A.. with its answered questions, key-wide default
  points/scoring and per-question overrides. A save names its base revision (409 when
  stale) and a request ID (same ID and content returns that revision; other content 409);
  an identical key records nothing. Scores are DERIVED on every view from the latest key
  and each sheet's current value (latest review, else the machine reading); never stored
  (except in a certification's immutable snapshot, made at the instructor's request in Certify, as the
  gradebook stores a pulled total), published or emailed, and readings, reviews, identities and the gate are untouched.
  Scoring is Gradescope's documented rule (`bubble-scoring-v2`): exact = the letters
  marked equal the key's; partial = a share per correct letter, zero with any other
  letter; either = full for any correct letter, zero with any other letter; a blank earns
  nothing, EXCEPT a question keyed with every letter under Either/Or, which everyone
  earns, a blank included (requirement, 2026-09-25; v2); Decimal, each question rounded
  to the cent. A changed rule gets a new name. Gradescope itself gives that blank 0 (two
  students of Exam 1, 2026-10-06): GRADESCOPE "THE MC CHECK" compares Gradescope's scores
  with these and names every student the two score differently, and these scores are the
  grade: GRADESCOPE "MC CORRECTIONS" changes Gradescope's score of such a question to this one
  (requirement, 2026-10-22).
  Only each present scan's latest complete read of the plan's assignment and term counts,
  and only after that scan cleared its scan checks (the rest are counted as waiting); a sheet
  still needing review, without a version, or whose version has no key is listed with its
  reason, never scored or guessed. One version: every sheet takes it. The page's JSON
  names students but carries no UIN or enrollment ID; the CSV download is the
  instructor's and includes UINs.
- BUBBLE SHEET ITEM ANALYSIS (requirement, 2026-09-30: "For each question: how many got it right, whether the top scorers
  did better than the rest, and which wrong choice was popular. This catches a miskeyed question before you certify").
  READ ONLY: `grading/bubble_analysis.py` (rule `item-analysis-v1`) analyses exactly the sheets `_bubble_scores` scores,
  EACH VERSION APART (versions scramble questions; never pair them by number): difficulty and full credit, the corrected
  point-biserial (discrimination against the rest of the score), full credit in the top and bottom 27% by total (ties by
  sheet ID), each choice (a letter alone, blank, several; unread counts as blank) with the average total of its sheets,
  and Cronbach's alpha over the question shares. Alpha, discrimination, the groups and every flag need `MINIMUM` (10)
  scored sheets of the version. `possible_miskey` when more of the top group chose one other letter alone than earned
  full points, or discrimination below −0.1; `hard` (<0.30), `easy` (>0.95) and `weak` (0 to 0.15) only inform. It
  changes, decides and approves nothing: the instructor edits the key in the editor, which a flagged question opens.
  `GET …/bubble-sheets/analysis` and `…/analysis.csv` carry counts and shares only, no name, UIN, enrollment or sheet
  ID; the `/bubble-sheets` payload is unchanged. The page reads it lazily (a closed "Item analysis" panel) and again
  only when the saved key or a scored sheet changes. A changed statistic or threshold is a new rule name.
- SCANTRON LABELS (requirement, 2026-10-15; DOCUMENTS "SCANTRON LABELS"): a sheet carrying the label printed for a named copy
  of the read's assignment, in a ready and current print job (`_labels`), is `matched` by `label` for that student
  (`identified_by` `label`, as firm as an exact UIN) unless an exact UIN read is someone else's: then a suggestion at 50% with
  the reason, never identified without the instructor. A label for a student outside the read's sections only adds a note.
  `matching.MATCHER` became `average-5`; unlabeled sheets match exactly as before. `identity.label` appears only when some
  sheet was identified that way.
- HOME-SCANNED SHEETS (2026-10-24, Exam 2 scanned at home in stacks): the Epson and the Brother crop every page to the paper's
  own edge (the Epson's 1675 to 1695 by 2195 to 2220 pixels at 200 DPI; no Brother test page was exact Letter), and only exact Letter rasters were looked at, so not one home-scanned
  sheet was recognized. A raster within 3% of Letter each way is the whole sheet and is scaled to Letter before it is detected,
  read or cropped (`reader.letter`); further off stays no sheet. Every rule after that is unchanged. The detector is `marks-5`:
  what marks-4 really looked at is carried over as it was, and only the pages it passed over for their size are looked at again.
- A SCAN ITS LABELS ASSOCIATED (same day: each student's bubble sheet lay beside their booklet in the stack, so every stack
  waited for "Say whose bubble sheets these are"): when the booklets' labels associated the scan with an assignment, its
  recognized sheets are read for that assignment by themselves (`AssignmentFinder._read_linked_sheets`, actor `automatic`), the
  read the instructor would request. Once per scan (`linked_sheets_read` Inbox event, with any refusal); a Scantron label on the
  scan naming another assignment leaves the choice to the instructor and says so.
- VERSION FROM THE SEATING CHART (requirement, 2026-10-24, "Yes": 94 of the first 109 home-scanned Exam 2 sheets had no version
  bubbled): a sheet identified by its Scantron label takes its student's version from the seating chart of the exam and term
  (`seating_exam_versions` through the student's seat, exactly one letter), as that student's booklet does (`_current`,
  `version_from` `seating_chart`), where the bubbled version is blank, faint or several letters including the chart's. A letter
  bubbled that the chart does not give is a review reason ("Version C is bubbled, but the seating chart gave this student
  Version A."). Sheets matched any other way, and students no chart gives exactly one version, are as before.
  THE BOOKLET'S VERSION (requirement, 2026-10-25, after page 1 of the Exam 2 booklets showed 68 of 312 students wrote another
  version than their seat's, and 57 sheets with no version bubbled were scored against the seat's key): where the student's
  booklet has a settled version in a grading plan of the exam and term (`confirmed` or the instructor's, GRADING "FIRST PAGE";
  `ExamVersionsMixin.booklet_versions`, wired as `BubbleSheetService.booklet_versions`), that letter takes the seat's place in the
  rule above (`version_from` `booklet`; a letter bubbled against it is asked, "... but this student's exam booklet is Version A.").
  A booklet still being confirmed leaves the seat's rule; scores are derived from each read, so nothing stored changes, and a
  reviewed sheet keeps the instructor's version. One view reads the booklets once for all its scans (`_sheets` `memo`).
  A BOOKLET SCANNED AGAIN (2026-10-25, Exam 2: 164 booklets rescanned in the morning waited for assembly review, so their papers
  had no current revision, and 33 sheets went back to their seat's key, about 33 of 60 points lower on average): a paper without
  a current revision whose one active copy has an assembled revision takes that revision's settled version. The booklet is the
  same paper, so a new scan of it never changes which version the student wrote; grading the paper itself still waits for its
  assembly.
- A SHEET SCANNED TWICE (requirement, 2026-10-24, "automatic"): a Scantron label is one sticker, so two sheets carrying it with the
  same reading of every question and of the version, in the latest complete reads of scans still in the Inbox, are one sheet
  (`_repeats`). The instructor's reviewed copy is kept, else the first scan received; the other is `repeat`: no reasons, nobody's,
  never a rival claim and never scored. Marks that differ are asked as before. Comparing pictures is not used.
  THE VERSION BUBBLE (requirement, 2026-10-25, "I thought the version is read from the booklet, so why is version getting hung
  up"): the version bubble tells two such sheets apart only when both scans read a clear, different letter. A faint bubble read
  blank in one scan and unsure in the next held 18 rescanned Exam 2 pairs with identical answers; the kept sheet's version is
  decided as for any sheet (the booklet's, THE BOOKLET'S VERSION).
  A REVIEWED COPY (requirement, 2026-10-25, "I reviewed this one, why is it still saying it needs review"): a sheet is the label's
  student's, or the student the instructor's review names, so a copy whose label was not read but which the instructor reviewed
  as that student pairs with the labeled one. The reviewed copy is kept, and an unreviewed copy repeats it when it reads every
  question as the review decided it, or in doubt where it does not (Eva Marsh's and Lena Maxwell's Exam 2 sheets).
  A sheet whose label was not read and that nobody reviewed pairs the same way with the student its ID and name read point to
  (matched or suggested), but only while a label or a review holds that student on another sheet; the held copy is kept and the
  other repeats it only with identical marks, never by agreeing with a review alone (Lyle Ensor's, 2026-10-25: "why does it need me to select who it is when it has a
  label").
- Everything unsure goes to the instructor, never to a guess: a page that only
  partly resembles the template, a sheet whose rings are not where the markers
  say, a bubble between `EMPTY` and `FILLED` (erasures) or another answer mark in
  doubt (`unsure`; blank answers, confident double marks and marks past the last
  question are not review reasons, below), and a missing, faint or double version. Keep the
  unsure band; do not tune it away to reduce review. Thresholds come from one
  section's real scans, and the README records the measured distributions.
- Identity is UIN first: `matched` only when the digits read contain exactly one
  roster UIN of the chosen sections and the name read does not clearly belong to
  someone else. Otherwise the UIN and name similarities give a stated confidence
  (55% UIN similarity plus 45% name similarity) and at most a `suggested`
  student. This average is the instructor's decision: an evidence-weighted
  probability model was built, tried on the real reads and rejected by the
  instructor as worse. Do not replace or re-tune the average unless asked.
  `matching.MATCHER` names the rule; when it changes, `_rematch_next` appends
  `bubble_matches` rows from the retained text (no OCR, no review touched) and
  `_sheets` prefers them. The instructor asked that a confident suggestion
  identify the student WITHOUT a confirmation, at a threshold they can adjust
  (`bubble_settings`, append-only; default `matching.AUTO_IDENTIFY` = 70%,
  floor `SUGGEST` = 60%; `matching.identified(result, threshold)`): `_current` derives it from
  the immutable match (so it never writes a review, and `identified_by` says
  `uin`, `confidence` or `review` everywhere a student is shown or exported).
  Never upgrade a suggestion that carries `notes` (something argues against
  it). One sheet per student per ASSIGNMENT (`_sheets`, `_claim`): a confident
  match yields to an exact UIN or a review of the same student in this scan or
  another scan's latest read, a student recorded anywhere is `taken` and never
  suggested or preselected again (still selectable, set apart in the picker),
  and equal claims flag both sheets. Marks are decided by ONE rule,
  `reader.decide`, applied at the read and again by `_load` from each reading's
  retained `marks`, so a changed rule reaches retained readings without
  re-reading; the thresholds were measured (see the README) and a lone faint
  mark must keep going to review, since it may be dust. Blanks, marks past
  the last question and confident double marks (a grading matter) are never
  review reasons; only a mark in doubt (`unsure`) is. ERASED (requirement, 2026-10-25: "All of the MC marks that I've had to
  review for exam 2 seemed pretty obvious that the student erased and chose a different answer"): a mark under 40% of the row's
  darkest (`DOMINANT`) is an erased answer, never a doubt, however dark (Exam 2: 22% to 39%, all 9 reviewed kept the dark
  answer; one at 41% was kept as a double, so 40% and up stays a double). `align` refines only fits the
  rings do not confirm (`RING_SURE`), so other sheets read exactly as before.
  A re-read carries each page's latest review forward (`process_one`).
  The instructor names the ASSIGNMENT only, and it is never preselected without evidence (the scan's association, its
  last read, or the assignment whose labels were scanned most recently; 2026-10-02: the first one alphabetically was).
  Sections are not chosen: a read
  covers every section of the course's current term, and each sheet takes its
  section from its student (`section_ids` stays an optional API input).
  The question count may be left to the sheets (`question_source` `inferred`,
  `reader.infer_question_count`: the mode of `last_marked`, accepted at 60%
  agreement, small uploads also counting only present scans of the same assignment
  and term (`PRESENT`; deleted scans and other terms never supply evidence); all 100
  rows are read, then `reader.trim` keeps the real ones). Below the threshold
  the read FAILS with the reason (`reader.Undecided`); never guess a count.
  `mountScanReview` binds only the forms of its own details host: the bubble
  panel lives inside its host and binds its own. `reader.field_box` makes the
  ID/name crop follow the band of handwriting (students write on or below the
  rule); the OCR crop and the review snippet must stay the same box. `MARGIN`
  (0.05) only breaks near-ties and says so in `notes`: the instructor expects a
  student shown at or above their threshold to be identified. An unidentified
  sheet's picker starts on the closest student still free. OCR text is untrusted draft data: bounded, compared, escaped. Roster
  UINs stay out of match results; only the instructor's CSV includes them. STUDENT PICKERS (requirement, 2026-09-24: scrolling a
  roster in a dropdown is "nearly impossible") are the shared search box `static/student-picker.js`, here and for first pages
  without labels: words typed filter names and sections in any order; four or more digits ask the server (`POST .../students`,
  `matching.uin_search`, the body keeps them out of URLs) for the closest UINs of that roster, answered with names, sections
  and a similarity, never a UIN. The roster is the read's or the scan's assessment sections, never the whole term when the
  scan has an assessment.
- Review opens only what `current.flags` says is in doubt (identity, version,
  listed questions; everything when a sheet could not be read). Confident reads
  stay collapsed and locked until the instructor unlocks them, so a confident
  value cannot change by accident; saving still sends every value.
- Review shows the piece of the scan each question is about (ID and name boxes,
  version row, each flagged question's bubble row) beside its control;
  `reader.part_image` cuts it on request from the retained raster, upright, and
  stores nothing. Keep that: the instructor should never search a page.
- Machine readings (`bubble_sheets`) and reviews (`bubble_reviews`) are
  immutable and append-only; the latest review is the current value. Runs belong
  to the source Inbox message and reference the assignment, term and permanent
  sections; reviews reference `(section_id, enrollment_id)` memberships. Reads
  are queued for the Inbox worker (`process_one`), one live run per scan, and an
  interrupted run fails until requested again.
- Gradescope's template PDF is not in the repository. `derive_template.py`
  recognises it by SHA-256 and keeps measurements only. A changed template needs
  a new profile checked against real scans. The reader works on the retained
  200-DPI raster; `faculty_ocr.recognize_fields` reads caller-chosen boxes of one
  image in a single model load and knows nothing about what they mean.
