# Scan-to-grading workflow

DR copy page codes (2026-11-10, GRADING "DR COPIES" > STAMPS): the accessible Word file handed out (and uploaded to AIM) prints
each page's own Data Matrix at two corners, the same for every student. They are copy 1's codes of a job of 20 unnamed copies of
the copy's stamped document; each paper that comes back takes its own copy (photocopied stamps) and is named from the paper.
`dr_copies.dr_stamped_copy`, `accessible_quiz.build(stamps=)`, `media.barcode_png`.

DR copies (2026-11-10, GRADING "DR COPIES"): the DR center's print of a drafted quiz's Word copy (one problem per page, no
labels) is registered under a document of its own that names the version it copies. Its papers count as that version's: the same
questions, rubrics and grades. Each problem's answer area is its whole sheet, cut without alignment; the blank Faculty renders
from the Word file only gives the sides and the way up. `grading/dr_copies.py`, `exam_workflow.grading.PAPER_DOCUMENT`,
`extraction.extract(sheet=True)`, `DocumentService.register_dr_copy`.

Fewer holds (2026-10-26, GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS", "A SETTLED NOTE STAYS SETTLED", "A GRADER'S REQUEST ON
AN EMPTY ANSWER"): in a Cloud batch the two graders decide the rubric's stated deductions and Faculty takes them off; letter case,
$ signs and spacing the rubric does not name are not doubts; a request a ruling settled under the second grader's name stays
settled when that grader is adopted; a grader's request on an empty answer, and a whole-page area that did not align, no longer
keep a blank zero from Jev. `grading/grader_deductions.py`, `arbitration.renamed_notes`, `jev_blank.asked_about_the_empty_answer`.

Jev blank answers (2026-10-26, GRADING "JEV BLANK ANSWERS"): an empty transcript's automatic warning does not count again as
uncertain handwriting when the reader found every crop empty. The blank hold remains until Jev confirms it at the cutoff (`jev_blank.CUTOFF`, .8 since 2026-10-31, was .9).
Actual reading doubts, nonzero grades, disagreement and rubric concerns still prevent automatic blank acceptance.

Claude API fallback (2026-10-25, GRADING "CLAUDE API FALLBACK"): a Claude connection can opt in to the same model through direct
Anthropic billing after a confirmed subscription usage limit. `ANTHROPIC_API_KEY` goes only to that fallback subprocess.
Unscoped keys also need `ANTHROPIC_WORKSPACE_ID`; its validated ID is the only extra request header passed to the fallback.
The receipt distinguishes API usage and its estimated cost; bundles share that cost once. Other errors and uncertain work never
trigger the switch. Old profiles stay unchanged. The setting covers grading and rubric work, not Assistant chat or coding.

Flag review deductions (2026-10-25, GRADING "FLAG REVIEW DEDUCTIONS"): new reviews choose applicability from fixed extra
deductions in the approved guidance, and the decimal scorer applies them. The claimed score and actual adjustments must
agree before automatic acceptance. This is model-independent; uncertain cases still stay with the instructor. Existing
reviews keep their original prompt/rule. The authorization and adjustment metadata belong to the term review and result.

Assignment review, 2026-10-17: a missing registered paper is Prepare's first prerequisite, with Add paper in Create;
unavailable downstream setup is folded until then. A conflicted review save keeps browser edits for comparison with
the current grade and explicit application/discard. Scoring edits never cross a changed rubric; applying edits does
not save or approve them (GRADING "KEEP CONFLICTED EDITS").

Prepare now lists missing prerequisites with direct editor links and an explicit **Prepare remaining setup** assistant handoff. Review keeps flag reasons beside the decision and offers inline fit, zoom and pan beside the rubric; all full reasons and full-screen viewing remain available. An empty review queue directs the instructor to actual remaining work. Certification blockers open their correction checks. Return separates certification, local recording, sending and publication.

The assistant's focused grading tools use these same routes, revisions and prerequisites. Reviewed material can enter a native
question rubric or quiz draft through the assistant material bridge; it does not finalize rubrics, approve a quiz, certify grades
or publish them. Readiness includes these prerequisites before papers arrive. Recovery names a confirmed failed job and uses its
existing retry route; uncertain outcomes still require explicit review (ASSISTANT, GRADING).

The main entry is now **Assignments**. Its Prepare area puts question wording,
solutions, rubrics and answer-area controls together. Existing grading URLs open
their working step inside that assignment's shell; the review desk and all source,
approval and certification rules below are unchanged. See
[ASSIGNMENT-WORKSPACE](../docs/decisions/assignment-workspace.md).

Assignments groups work by course-wide assignment and permanent teaching term
(the Grading landing page was retired on 2026-10-18; GRADING, ASSIGNMENT-WORKSPACE
"THE GRADING INDEX IS RETIRED"). Each item reuses the unique
`exam_events(term_id,assignment_id)` and its grading plan. Inbox review is a
prerequisite to the six grading stages, not a stage within them. A separate
notice links an assignment's later pending scans to Collect; Scan records remains
an unnumbered utility for revisiting originals. The counts separate new eligible
responses, running work, review work, instructor approvals, and automatic
acceptances. They use the existing collection and source validation.
The connected step graph is a read-only projection (`progress.py` and
`static/grading-guide.js`) on each assignment's grading steps, with links into the working views.
It introduces no retained owner. Completed, pending and failed states derive
from current question setup, visible arrivals and response work. Grade exports
are current only when their retained response/decision/result IDs still match;
training exports and suspended automatic acceptances do not complete the flow.
Unfinished nodes show circular progress from saved completed/total work, including
partial instructor review. `workflow.progress` counts each current response once:
new eligible, stale, actively regraded and failed work remains unfinished; final
review counts current instructor decisions and valid automatic acceptances.
Historical batches and retries never enlarge these totals. New blocked or missing
papers stay outside grading counts until eligible; pending Inbox arrivals cannot
reopen completed grading. Previously created responses still become stale when
their reviewed source changes. Setup counts its
processing and confirmation tasks; shared template page receipts are counted
once per template and failed reads do not count as successful inspection.
Unknown totals have no percentage. Failures remain red crosses, completed steps
remain green checks, and overview polling refreshes the rings without running
models, writing records or approving anything.

Current printed labels associate a scan automatically and attach compatible
print batches. A prior explicit bubble-sheet read can supply its chosen scope.
An unknown scan asks for an existing assignment or a new course-wide assignment,
its term, and sections. Creation and association commit together under the
roster lifecycle and definition locks. Adding sections to an existing assessment
requires an explicit selection; existing sections are retained.

`frq_scan_links` is immutable association history referencing the Inbox owner
and term-owned grading plan. The latest revision is current. Removed Inbox
messages count nowhere; restoring one restores its place. `frq_scan_checks` is
only a discovery cursor. Bounded worker discovery catches older scans and later
page corrections without writing during overview requests or changing worker
fairness. Once pending label OCR finishes, discovery also retries the document
service's all-or-nothing automatic grouping. Existing runs, including undone runs,
are left alone. Catalog backup retains these records through the normal lifecycle.

`POST /api/grading/scans/<message>/associate` requires the current scan
fingerprint and an idempotent request ID. Association itself identifies no page
or student, cannot override mismatched printed labels, and cannot authorize
processing. Only an assignment created from the scan (new title and sides) or one
the assignment finder found (`associate_found`) then has the scan's first pages
read, and students identified with confidence registered by `automatic`
(`_read_unlabeled`, `register_confident`; DOCUMENTS "ASSIGNMENTS CREATED FROM A SCAN").
`POST /api/grading/scans/<message>/review` requires the current fingerprint and
explicit instructor confirmation, invokes the existing Inbox review transition,
and returns the assignment's Papers route. Existing grouping, source conflicts,
authentication and assembly rules remain authoritative. Unlabeled scans may be
organized even when their unidentified pages cannot yet be graded.

The batch dialog defaults to `selection: "new"`: responses with any prior work
are excluded, including queued, failed, changed and approved work. Explicit
regrading remains available through `selection: "all"` and the existing retry
and recomputation controls. Selection is revalidated in the batch fingerprint.

Checks: `test_grading_intake.py` covers retained associations, storage modes,
term boundaries, review and new-only grading. `browser_grading_intake.py`
exercises the real handoff, subsequent arrivals, the assignment's six-step graph and the desktop layout.
Run through `manage_checks.py` to keep synthetic data and caches on NVMe.

Review opens straight on the next waiting response in the existing version-checked review desk, with a navigation rail
(progress and unfinished questions) and reason filter; the flag review and Failed to grade are one closed line each above it
(Gradescope regrade requests are answered on Return › Regrade requests: `regrade_apply.py`, `regrade_drafts.py`). A question's shared rubric concerns are settled beside the desk (`rubric-quality/clarify`, one question's new
confirmation), and only the responses that raised them are graded again as an ordinary batch. A proposed-line group can be seen
whole and approved after looking at each response (`proposal-gallery`, one review per response). Approve & next continues through a question's
groups; Later only defers navigation and leaves its response pending. Completed questions and grade summaries are disclosures.
All responses and each question's Groups & review tools keep the existing utilities accessible. These are views of existing
term-owned records; default API responses remain unchanged and `details=review` opts into anonymous member reason categories.

Part caps (GRADING "PART CAPS"): in a deducting rubric each lettered part's lines are one group `part-x-cap` whose floor is minus
the part's worth (`rubrics.capped_parts`, built where drafts are accepted; `rubrics.validate` keeps lines in the cap their wording
names). `scoring.score` reports what caps gave back as `part_caps`, which `gradescope_sync.expected` adds to the point adjustment.
The rules are the only owner of a part's worth (the question revision keeps only its total).

Rubric memories (GRADING "RUBRIC MEMORIES", "KEPT AT ONCE"): after the instructor's own Certify, and after they approve rubrics
following answers to rubric questions no earlier pass has read (`after_certified`, `after_finalization`), `rubric_memory.py` reads
the assignment's rubric questions, the instructor's answers and the graders' doubts and proposes general rules and watches for the
course. Each memory stands the moment it is written (a `keep` decision with actor `automatic`); the instructor drops the ones they
do not want on the course's Rubric memories page, and Keep brings a dropped one back. Only standing memories (kept, not retired,
not replaced by a kept revision) are sent to the rubric writers and the rubric check (`standing_rules`). A row written before
2026-10-17 can still wait for Keep. Owner: the course definition; a pass is owned by the term plan it read.
