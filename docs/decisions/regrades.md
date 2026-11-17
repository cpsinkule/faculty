## Regrade requests

Rule ID: **REGRADES**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-09-30), from my suggestion list: Gradescope regrade requests (already captured by the communications
  record) next to the graded response, on Needs you until answered; asked, they chose "Faculty posts my reply".
  `grading/regrade_requests.py`, `grading/regrade_api.py` (blueprints `frq_regrades`, `frq_regrade_panel`), migrations
  `grading/036_regrade_replies` and `grading/044_regrade_decisions`, `static/grading-regrades.js`, `tests/test_regrade_requests.py`,
  `tests/test_regrade_panel.py`, `tests/browser_regrades.py`.
- 2026-10-07 (instructor, of the Review step's request card: "this is not very helpful for gradescope regrade requests. let's
  brainstorm how to make this easier and more user friendly, and also ensure that the result of my decision is reflected in the
  gradebook and also in gradescope"). Asked, they chose: CERTIFY JUST THIS CHANGE for the gradebook, CANVAS INCLUDED in the same
  Apply, the RETURN STAGE WITH NAMES (over "Both", so the Review step no longer shows requests), and a reply DRAFTED BY A MODEL.
  `grading/regrade_apply.py` (`RegradeApply`), `grading/regrade_drafts.py`, `grading/certification_change.py` (GRADING "CERTIFY
  JUST THIS CHANGE"), `gradebook/canvas_cell.py` (CANVAS "ONE CELL"), the Gradescope sender's `only_responses` (GRADESCOPE "ONE
  RESPONSE").
- PLACING A REQUEST, never guessed: Faculty's own accepted grade receipt for that student's Gradescope question
  (`frq_gradescope_grades` + upload: course, Gradescope assignment and question, enrollment), else the plan and version the section's
  Gradescope exam is linked to (`frq_gradescope_targets`) with the question Faculty made there (`frq_gradescope_created_questions`) or
  the ONE question of that version printing the number the Gradescope question's title prints; then the student's response. A request
  of a linked assignment without a response is listed on that plan unmatched, with the reason; one of an unlinked assignment is only a
  Needs-you count (answered on Gradescope) and in the student's communications record. A request on a bubble-sheet exam is placed on
  the student's sheet (BUBBLE SHEETS, below).
- OPEN until a reply is captured, Faculty's own reply is accepted, the request was captured already closed, or the instructor marks it
  handled (`frq_regrade_marks`, append-only, Reopen the same way). The capture keeps a request's first state (its context is not part
  of the revision hash), so closing on Gradescope without text is seen only through Mark handled.
- REPLYING is the grading page's "Close & Respond", read on the real site 2026-09-30: `urls.regrade_request` + `/<request id>`, one
  JSON PUT of `{"regrade_request": {"id", "staff_comment"}}` with the page's token, which answers AND closes the request. The preview
  (read tier) reads the page and writes nothing: open or not, Gradescope's score for the question, Faculty's final grade, a fingerprint.
  The reply names the fingerprint, a request ID and `confirmation: "reply"`, reads the page again (409 when changed or already
  answered; with `expect_score`, also when Gradescope's score for the question is not that one), records the reply BEFORE sending
  (`frq_regrade_replies`, TERM grading plan owner), sends once and reads the page back: `accepted` only when the request shows exactly
  that reply; anything else is `failed` or, when the page cannot be read, `uncertain`, settled by Check on Gradescope (reading),
  never by sending again. A reply by itself changes no grade: Apply carries a changed grade (below). Never the Math 140 for
  Instructors course, never /logout. Rehearse a reply in the sandbox course "Faculty upload test" (1413803) before the first real one
  (requirement, 2026-10-01: "first rehearsed in gradescope fake course"). REHEARSED 2026-10-07 (the instructor's "yes, run the sandbox
  rehearsal"; `/mnt/nvme/Teaching/tmp/research/regrade-rehearsal`, guard = the sandbox only; the instructor filed the request from
  instructor@example.com enrolled there as a student): the capture's `listing` and `exchanges` read the real Regrade Requests page and
  grading page (the request open, its text and id); `save_grade` changed the question to 4/4, read back 4.0, and the request STAYED
  OPEN (changing a grade does not close it); `regrade_page`'s score then matched `expect_score`; ONE `reply_regrade` PUT answered
  `completed: true` and the page read back closed with exactly the reply (`accepted`). The Regrade Requests list's "Completed"
  column stayed blank after the reply (harmless: the capture reads the grading page again).
- SHOWN on the Return stage (2026-10-07): Return's third sub-tab, **Regrade requests** (`#grade-delivery/<plan>?view=regrades`,
  `&request=<message id>` for one), its open count the needs-you block, the Return stage's mark counting open requests (assignment
  `needs_you` key `regrades`). Return is not anonymous: the list (`GET …/regrade-requests/panel`) names each student and section
  (verified roster, one read) with Faculty's score; open requests first, answered and handled ones folded. One request opens in
  place (`GET …/regrade-requests/<message id>`, catalog only): the student's words as text, their work and grade (the certification
  drawer's `responseMarkup`), Change grade (the one review desk, approved and manual only, the ONLY final-change path), and WHERE THE
  GRADE IS, compared like with like: the question on Faculty and Gradescope (Faculty's last receipt), the exam total in the gradebook
  and Canvas (Faculty's last receipt), the one that differs named. The Review step's list (`GET …/regrade-requests`) stays anonymous
  and is no longer shown there. Home has no regrade card of its own (FOLLOW-UP, below: the request is a Follow-up conversation,
  counted by its Needs reply). An open request keeps a certified assignment out of the archive until it is answered
  (CERTIFIED-ARCHIVE).
- FOLLOW-UP (requirement, 2026-10-07: "I want regrade requests to also go through follow-up. the model should get all necessary
  context, the rubric for that given question along with the clarifications, the crop, the student's request, and other relevant
  context to enable it to suggest intelligently"; asked, they chose: approving carries it everywhere, ONE suggestion, Follow-up the
  only place that asks for attention). Each captured request is a Follow-up conversation (TEACHING-WORKFLOWS FOLLOW-UP); its
  preparation is told everything and sees the crops (ASSISTANT "A GRADESCOPE REGRADE REQUEST") and proposes one decide card
  (`POST …/<message id>/decide`, preview `…/decide/preview`, confirmation "decide"; `decide_preview` forecasts the grade change and
  every Apply step and pins the response's record, the decision, the reply, publication on Gradescope, the certification and what
  the gradebook and Canvas hold). Approving it (on Follow-up, or Approve the suggestion on Return, which saves an edited reply on
  the card first) saves a change through `save_review`, then records and starts the same Apply (the approval is its publication
  confirmation, pinned by the fingerprint); the page that approved drives it (Follow-up's in the background while Faculty is open,
  with a toast at the end). A reply or a grade change made elsewhere makes the suggestion outdated (the run's source carries each
  request's state and the response's record) and withdraws its waiting card. Return shows that suggestion (Follow-up suggests:
  …, Approve the suggestion) and fills the reply from it; the separate DRAFT runs only when Follow-up has none coming.
- EDIT THE GRADE (requirement, 2026-10-08, of a Keep card: "it doesn't give me the option to edit the regrade other than just the
  words, I already rewrote the reply, but I need to add a point back to his on a specific rubric line"). The decide card's "Edit
  grade or reply" opens the card in place with the question's rubric lines (ticked as the card proposes: its lines for a change,
  the final grade's for keep; the grade now from the preview's `current`, or for a card proposed earlier from
  `GET /api/grading/responses/<id>`), a point adjustment only when no line fits, and the reply. Save (`…/suggestions/<run>/edit`
  with `decision`, `lines`, `adjustment`) proposes the edited card through the follow-up's own write checks and the decide
  preview, in the old card's place; lines equal to the grade now and no adjustment are keep. A grade the instructor changed
  replaces the model's reason with "You changed the grade on this card." (the review's comment). Nothing changes until Apply.
  A grade the instructor changed never teaches the Assistant anything; only a reply they reworded can, and no grade, rubric
  line or adjustment is in what is compared (ASSISTANT-MEMORY "LEARNED FROM AN EDIT", 2026-10-12).
  The card names each added or removed line as the rubric numbers and words it (`line_words`), on its summary. A blind grade
  that is not shown, or a rubric that cannot be read, leaves the reply alone editable. Checked by `tests/test_regrade_followup.py`,
  `tests/test_followup_suggestions.mjs` and `tests/browser_regrade_edit.py`.
- SEE THEIR WORK (requirement, 2026-10-21: "when I get a regrade request in followup, can we have it link to the crop of the
  problem in question (ie, click on the link and a popup of the crop along with the rubric lines appears"). A regrade request's
  Student's message on Follow-up has "See their work" beside View conversation, whether or not a card waits. It reads which answer
  the conversation's latest request is about (`GET /api/grading/regrade-work?key=<follow-up source key>`: `{response_id, plan_id,
  question_title}` from `match`, or `{reason}`; catalog only, owned by the response) and opens a popup with that answer as Return
  shows it (`responseMarkup`: the grade, the rubric lines applied, the other lines folded, the crops, which enlarge, and the
  reading). It is read-only: the card's Edit grade or reply changes the grade. A request Faculty cannot place says why. Checked by
  `tests/test_regrade_followup.py` and `tests/browser_regrade_work.py`.
- APPROVE ALL (requirement, 2026-10-29: "Can we put regrade requests in their own tab in the followup menu and then add an option
  to approve all"). Follow-up's Action desk has a Regrades tab (`?filter=regrades`): the regrade conversations (source key
  `gradescope_regrade:`) that Active would show, which leave Active; Needs me and All still count them. Return's Open on Follow-up
  opens this tab. With two or more decide
  cards waiting in the tab (its search applies), a line above the queue offers Approve all N; one confirmation (how many keep, how
  many change, where a reply and a changed grade go) and the page approves each card in turn through its own Approve
  (`POST /api/assistant/actions/<id>/approve`: its preview again, then the ordinary decide), keeps first, then the list's order: a keep
  changes no grade, while an approved change outdates the exam's other cards until its Apply has certified it. A card that changed
  or no longer waits (stale, 409 or 404) was not submitted and is shown again. A LOST ANSWER (2026-10-29, the first live run: the
  second of 16 went through on the server at 04:04:16Z, but the browser's request was cancelled at 04:04:04Z, Tailscale's proxy
  logging "context canceled", and the run stopped with "Can't reach your server"): an approval with no answer is settled by
  reading the card's own record (`GET /api/assistant/actions/<id>`, read-only, the instructor's route, not the Assistant's) once no
  approval is being carried out (the restart check's count): gone through counts as approved and the run goes on; still waiting
  exactly as it was is sent once more (the server never approves a card twice); anything else stops the rest. The pages are told
  of the approvals once, at the end, so their reads do not compete with the approvals for the server's threads. A card open for
  editing or in a preparation not ready is left out. The approved changes' Applies are then driven together while Faculty is open,
  one toast at the end. No new route: the decision and its Apply are exactly a card's own. Checked by
  `tests/test_regrade_followup.py`, `tests/test_followup_suggestions.mjs` and `tests/browser_regrade_all.py`.
- BUBBLE SHEETS (requirement, 2026-10-28, asked "why arent MC regrades handled in faculty?" after Exam 2's grades were posted, then
  "extend it"). The old rule ("bubble-sheet requests are never placed") came from before MC CORRECTIONS, when Faculty never wrote a
  multiple-choice score on Gradescope; Faculty's reading of a sheet is now the grade, so a request on a bubble-sheet question is
  answered from it. `grading/regrade_mc.py` (`McApplyMixin` in `RegradeApply`), `regrade_requests.sheet_match`, tests
  `tests/test_regrade_mc.py`. No migration (`frq_regrade_applies.response_id` is empty for one).
  - CAPTURED: Gradescope keeps a bubble-sheet exam's versions off the course's assignments list (read on the real site 2026-10-28,
    reading only: 140-515 Exam 2 MC's versions absent from the list, their Regrade Requests pages present; one open request,
    140-514 Version B Question 3, had never been seen), so the capture also reads the Regrade Requests page of every version of
    each bubble-sheet exam Faculty linked in that course (`frq_gradescope_sheet_targets`, COMMUNICATIONS).
  - PLACED by Faculty's records only: the exam Faculty linked whose versions name the request's Gradescope assignment (one plan, else
    unmatched), the question by its number (the title's lead "3:" or its last number, as MC CORRECTIONS finds it by outline index),
    and the sheet Faculty uploaded for that student in that version (`frq_gradescope_sheet_uploads`, accepted). No such upload: listed
    on the plan unmatched with the reason.
  - SHOWN: the Return panel and Follow-up's See their work show the question's row of the student's scan
    (`/api/bubble-sheets/sheets/<sheet>/parts/q<n>`), what Faculty read, the key for the version (answer, scoring, points), the score
    and the sheet's total; Where the grade is compares Faculty's question score with what Faculty last made Gradescope show (its
    latest correction), and the certified total, the gradebook and Canvas as for a written answer. No reply draft (the model draft
    reads a written answer); Follow-up's suggestion fills the reply.
  - DECIDED: Follow-up's preparation is told the reading, the key, the scoring rule, the sheet's total, how the class answered that
    question in that version and the other requests on it, and sees the question's row and the version bubbles; its ONE decide card
    is "keep" (the reading stands) or "change" with `answer`, the letters the student filled ("" a blank), never rubric lines or an
    adjustment (refused). A key the student says is wrong is kept and said in `reason`: the key is the instructor's to fix on the
    Bubble sheets page, for everyone. The card's Edit grade or reply shows the row and letters A to E (ticked as it proposes; ticking
    Faculty's reading is keep). Approving a change saves a review of the sheet (BUBBLE-SHEETS; every other question, the version and
    the student as the sheet has them, the note "Regrade request: question N read as X"), refused when the sheet's reading of that
    question or its student changed since the preview; Faculty's score then follows from the key.
  - APPLIED: `gradescope` when Gradescope's score for the question (read on its grading page) is not Faculty's: Apply asks for a read
    of Gradescope's scores (the instructor's Check MC, actor `workspace`, so a correction that failed before is tried again); the read
    queues the MC CORRECTION and the sender makes it. The step is done when that correction is accepted, already when the read shows
    Faculty's score, and stops with the correction's or read's reason (another version on Gradescope: changed there by hand). A
    published exam is written to, as MC CORRECTIONS does; nothing is published. `reply` as for a written answer (`expect_score` is
    Faculty's score for the question). `gradebook`: GRADING "CERTIFY JUST THIS CHANGE" › ONE BUBBLE SHEET. `canvas` as for a written
    answer.
- KEPT ADJUSTMENTS (2026-10-08): a change keeps every point adjustment the final grade carries (a Certify batch
  line for the class among them) and adds the decision's own; it never drops one. `decide_preview` reports them in `current`.
- DRAFT (`frq_regrade_drafts`, TERM grading plan owner; the cloud dispatcher's `regrade_draft` unit, an ordinary cloud call owned
  by its row, `frq_cloud_calls.regrade_draft_id`). Asked (`POST …/<message id>/draft`) when a request opens with no draft for the grade
  as it is now (one per request text and grade content; asked again after a grade change), by the Rubric drafting and review model,
  which must read images. The model reads the request with the student's roster name and any nine-digit number replaced, the
  question (problem, answer key, solution), the rubric lines applied and missed, the adjustments, the score (and the certified one
  when it changed), the reading and the crops; it writes the reply's body and ONE sentence on whether the student's point holds
  (`assessment`: evidence for the instructor, never a grade). Faculty adds "Hi <first name>,". The page puts a draft only into an
  empty reply box, never over typed text ("Use the draft" swaps it in on request). Nothing is sent until the instructor applies.
- APPLY (`frq_regrade_applies`, immutable, and `frq_regrade_apply_steps`, append-only; TERM grading plan owner). The preview
  (`POST …/<message id>/apply/preview`, reads Gradescope and Canvas, writes nothing) lists only the steps something differs on, each
  from the module that owns it, in order:
  1. `gradescope`: that one grade (GRADESCOPE "ONE RESPONSE"), with the send's published confirmation (the page asks in a popup;
     the server checks the IDs); a send already running for the plan holds Apply.
  2. `reply`: posted only once Gradescope shows Faculty's score (`expect_score`); it answers and closes the request.
  3. `gradebook`: GRADING "CERTIFY JUST THIS CHANGE", a forecast in the preview and checked again when it runs. A REGRADE NEVER
     ASKS TO CERTIFY AGAIN (requirement, 2026-11-16: "regrade requests shouldn't trigger recertification, rather they should
     automatically change the grade in the gradebook and the assignment and also on gradescope"): whatever else changed since
     certifying, this grade goes to its cell and everyone else's change keeps waiting (GRADING, LIVE OR CARRIED MARKERS); it is a
     problem step only for this grade itself (not certified yet, or this student's other grades changed too). This supersedes the
     stop described next, which a regrade no longer meets; WHO MOVED is now recorded in the certification's `amends.moved`.
     WHO MOVED (requirement, 2026-10-21, asked "what does this mean" of a stop naming only counts): a stop because a confirmation
     changed names, after the confirmations, each student whose standing differs from the certification ("<name> (<section>) is
     now left out until their makeup, was 0 with no work"; three at most, then how many more). A classmate's drop is no move
     (GRADING "A DROP NEVER ASKS TO CERTIFY AGAIN"). CERTIFIED WITH THE WHOLE
     ASSIGNMENT: a stopped gradebook step reads "already" once a certification made after the stop holds this response's grade as
     the run approved it; the run then goes on to Canvas.
     A GRADE AS CERTIFIED (requirement, 2026-10-22, of a Keep card on the Mathematical Experiments Assignment stopped at Gradebook
     because a classmate's Q-drop had been confirmed minutes before: "the decision was to keep the grade unchanged, so why is it
     mentioning certifying again?"): when the request's grade is as the latest certification recorded it (a Keep, or a grade put
     back), the gradebook already holds it, so there is no gradebook step and nothing else that changed since certifying stops the
     request; Certification and Export still says the assignment changed.
  4. `canvas`: CANVAS "ONE CELL", sent only when the cell holds the value the preview forecast and Canvas still shows what it showed.
  Apply names the preview's fingerprint, a request ID (uuid), `confirmation: "apply"`, the reply and the published exams confirmed;
  everything is read again (409 when anything changed) and the approval is recorded before the first step starts. The PAGE drives
  the rest while it is open (`POST …/applies/<run>/continue`, every few seconds while a step waits): nothing runs by itself after a
  restart, and a closed page leaves the run waiting with Continue. Each step's request ID is `uuid5(run, step, attempt)`, so a
  repeated post never writes twice; each step names its own receipt (the send, the reply, the certification, the Canvas send) and
  its state is read from that receipt. A stopped step (someone else's grade on Gradescope, a reply Gradescope does not show, a
  certification refused, a held Canvas grade) says why and waits for the instructor's Try again, Check on Gradescope or the page that
  resolves it; Cancel stops before the next step. The assistant policy names none of these routes: an assistant request needs an
  Approve card (ASSISTANT).
