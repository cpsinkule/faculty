# Assisted free-response grading

Faculty → **Grading** turns reviewed exam scans into rubric proposals, then retains
the instructor's decisions separately. Nothing leaves Faculty except by an explicit
instructor action or an automatic step a contract names (AGENTS.md OUTSIDE SERVICES;
`docs/decisions/gradescope.md`). The existing manual Exams grading workflow remains available.

## Instructor workflow

**Grading schemes** (requirement, 2026-09-23). A new batch runs **Cloud**, **Fully local** or
**Hybrid**. Cloud is the scheme this document called Cloud Standard until that day; its internal key
stays `cloud_standard`, because batch profiles and the catalog freeze it. Jev Routing, One-shot,
Tag-Team, Cloud High Assurance and the plain Nebius mode were removed, with Final Arbitrator Mode v1
and the Jev tie-breaker. Their batches cannot be started, resumed or retried, and a request naming
one is refused with the reason. What they produced stays: results, automatic acceptances, receipts,
reviews and exports remain readable and final, and their catalog tables are kept. The built-in
Nebius host remains a connection any Cloud role may use.

Scans now enter through **Scan → Assignment → Students → Papers → Review → Grading**. Printed
identities connect arrivals automatically; unknown scans offer an existing or
new assignment. The Assignments index lists one row per assignment and term (Pending,
Certified and Archived tabs, ASSIGNMENT-WORKSPACE), with its status and what needs the
instructor. An assignment
appears once an associated scan passes its review.
Finishing that review opens the assignment's next grading step, starting with rubrics.
Existing grades remain accessible while additional scans need review. Batches default to new
responses only, while retries and regrading remain available. See
[scan workflow ownership and API contracts](grading/README.md).

**Every paper goes on unless it is flagged** (requirement, 2026-09-28). A paper, sheet or page flagged
in the Inbox (a first page no student was matched to, a paper the grouping holds, a missing page, a
doubtful label) holds back only itself: the scan's review, automatic or the instructor's, waits only
for steps about the whole scan (its assignment, labels naming two assignments) and for the worker's own
reading, and the flagged items stay open under Current until they are settled, then follow by themselves.
Papers labeled on every page become reviewed papers without a click per copy. Once an assignment's new,
reset or changed responses have stayed the same for about two minutes, the worker starts the batch the
dialog's first choice would, in the default mode of Compute settings (**Start grading batches by
themselves**, on by default; `grading/auto_grade.py`). It never starts one while a batch of the
assignment runs, or after its last batch was cancelled or reset, until the
instructor starts one. Each plan of the plan read and of `GET /api/grading` carries `auto_grade` (enabled,
paused, not_started); Home names an assignment whose new responses did not start grading by itself
(2026-10-02). About two minutes after a grading batch finishes, the worker also asks for the flag
review of that batch's held grades, once each (**Run the flag review by itself**, on by default;
`grading/auto_flag_review.py`, 2026-10-11): the same review the Review step's Ask button starts, which stays
for a run you ask for. What it leaves, fails on or is stopped before waits for you. The rubric finalization gates each question: a question whose rubric changed is
left out of batches, and named, until its finalization is confirmed again, while the others keep grading.
Automatic grouping stays all-or-nothing, and certification stays the instructor's.

The assignment tile and page header share the same six steps (requirement, 2026-09-20):
**Rubrics & Templates → Answer areas → Key & Rubric Finalization → Grade → Review → Certification and Export** (the sixth
renamed from Export by the instructor on 2026-09-26; its tab key stays `export`).
The first step holds everything that is uploaded: each version's rubric CSVs, then each
version's Assignment Template. It turns green when every version has its rubrics and its
confirmed template; nothing is approved there. Rubric upload comes first inside this step
because it establishes the problem numbers that template OCR must locate and crop. The
third step holds the answer key and the mandatory **rubric finalization** (below), whose
one confirmation approves every rubric. Each question there shows the answer key read from
its rubric's full-credit line, not saved yet: look through them, fix any that is wrong with
**Edit this answer key**, then **Confirm all** saves the rest as shown (or **Save this key**
one at a time). Questions whose key could not be read from the rubric (a matrix named only by its rows
and columns) are named: **Read from the blank exam** has the rubric drafting and review model work their keys
out from the printed problem, shown with how each was found and saved only by you; or write them with the editor. Old approval
and template links open the first step; old rubric-check links open the third.
Every stage can be revisited; navigation never approves work or changes
eligibility. The assessment list opens the next actionable stage. Old Assignments,
Rubrics and Jobs links still work. Compute settings sit outside the workflow.
Scan review happens in the Inbox before grading entry. Later scans show a separate
Inbox notice and join grading only when their checks are cleared; they do not
reopen completed grading while waiting. **Scan records** is a utility link for
revisiting originals and checking pending arrivals, outside the numbered graph.

Assignment Templates, on the first step's page, has a slot for every relevant printed/exam
version. Opening the page prepares references from generated assignments' verified canonical PDFs,
retaining an explicit `source_version_id` and reusing the same import on retries.
Label sheets are never imported as assignment content; those versions require an
uploaded PDF, DOCX or LaTeX template. Saved references can be replaced with blank
scans. Before **Continue to answer areas**, the instructor acknowledges that digital
templates may reduce performance and blank scans from the same scanner as the
student work give the best results. The term-owned `frq_template_reviews` receipt
records the exact choices and warning. Only then can new local template checks run.
OCR/layout checks and the explicit confirmation of each crop remain unchanged.
Changing a reference with existing areas requires an explicit reset; rubric pins
and historical confirmations remain retained. Existing checks keep their history.

Navigation loads once per route. Assignment pages request only the existing
term-plan and job metadata needed by their menus; all-assignment progress is
loaded on the tile page. Compute availability is checked when its panel opens.
Paper checks are reused only within one locked request, never across requests:
later scans, review gates, changed sources and changed rubrics are still checked
on the next read and before grading, approval or export. The default grading API
response remains compatible; these partial views introduce no retained owner.

Each assignment page draws the six steps as the numbered step graph (`grading-guide.js`
`pipelineMarkup`, styled once in `components.css`; DESIGN-LANGUAGE §4): numbered steps in ruled
columns, each with its label and the word for its state under it (Done, Failed, In progress, Not
started, a waiting step's own status, or its progress fraction). Done steps are slate, failures
red and untouched steps faint; the step on screen sits under a 3 px ink rule in the assignment
page's workflow bar, and on a card or tile the next task, which waits on you, sits under an orange
one; colour only repeats the words, there are no separate badges or legend, and the accessible
name and hover title of each step say its state and what its count includes. Home and the assignment library's cards draw
the same list as a row of thin bars (`data-size="compact"`). Every step opens its working view,
including completed steps. The graph scrolls sideways within its card when the columns do not fit.
Unfinished steps show a count-based
percentage and completed/total when the total is known: Grading counts completed current
responses, Review counts finalized responses (automatic acceptances included), setup steps
count their preparation and confirmation tasks, and partial instructor reviews keep their
progress when leaving and returning. Percentages never estimate elapsed time. Later arrivals
that clear Inbox checks reopen unfinished work; an old completed batch cannot mark new work
complete. The sixth step is complete only while a CURRENT certification exists (below, "Certification and Export";
this replaced "a retained grade snapshot matching the current decisions" on 2026-09-26): an export is not a certification.
The Assignments index re-reads its summary every 30 seconds.

**Goal-first setup steps** (2026-09-26 overhaul). Steps 1–3 open with one step header
(`stepHeader`): the stage name, one sentence of what the step still needs, and ONE primary
action on the right — the step's own next thing while it is unfinished (Upload rubrics,
Confirm all for rubric review, Decide versions, Confirm all areas, Continue to answer areas,
Continue to Grade). A finished step viewed while the work is elsewhere shows a **Next step**
callout naming the current step's own next action and its state, linking to it. Longer explanations sit in closed
disclosures ("How edits and comments work", "How this works").

- **Rubrics & Templates.** The rubric lines are edited in a compact editor, one row per line
  (label, points, wording growing with its text, the maths preview only where the wording has
  maths, a one-row comment for the reviewer). Each question's chip says one thing: Confirmed for
  review, Unsaved changes or Not confirmed for review; approval belongs to the finalization.
  While any question holds unsaved changes the header says how many, with **Save all** (each
  question in turn to the ordinary rubric-edits endpoint with its own version, revision and
  checksum; the first refusal stops the run and is reported, nothing after it is sent) and
  **Discard all**; leaving the page, reloading or closing the tab asks first. Unsaved typing
  still survives the step re-rendering in the same tab. Once every rubric is confirmed for
  review and every version's template is confirmed, the step shows a summary (rubrics confirmed,
  templates confirmed, paper versions confirmed) and folds Upload, Adapt, the AI draft, Add
  Version and the template slots into **Change rubrics or templates** (open while a draft or an
  adaptation runs, or when a link asks for the templates or versions). **Continue to answer
  areas** moves on without writing anything when the confirmed templates are unchanged; only a
  changed choice asks for the template confirmation again. **Assignment versions** shows one row
  per version: its template and how many of its questions have rubrics, confirmed areas,
  approved rubrics and keys, with what a version still lacks said under the table. Edit
  interpretation, Import revision and Question settings (question text, solution, response
  areas) live on this step only; manual question creation is under Other setup options.
- **Answer areas** shows each rubric read-only, with **Edit this rubric on Rubrics &
  Templates** opening that question on step 1. **Confirm all (N)** appears once, in the header
  (what it covers is its title; the header adds only what it leaves out). While the templates
  are not confirmed yet the header says so and offers **Choose templates**. **All answer areas** outlines every question's answer area on its version's blank
  pages; choosing an area opens its question.
- **Key & Rubric Finalization.** The header's one primary is the step's next thing: **Confirm
  all (N)** while read keys wait to be saved, else the rubric check's own **Run rubric check** or
  **Review and confirm**, then **Continue to Grade**. **Edit the whole answer key** is a plain
  button beside the per-question keys; **Correct wording** under each rubric line is a quiet text action. Once
  the finalization is confirmed and current, **Run rubric check again** (a paid model run that
  changes nothing until you confirm what it suggests) waits under Check details. When some rubrics'
  confirmed finalization still stands and others have none (a makeup version added, one rubric edited),
  the check asks only about the others, each problem still with all its versions side by side and the
  confirmed ones as the reference; the button names what it checks (**Check Version E**, else **Check
  the N changed rubrics**), and the confirmed rubrics keep their guidance and grades. Before anything is confirmed, editing a rubric after a check works the same way: the
  re-check asks only the changed rubrics, and the others keep the guidance and open concerns the last check left them. A re-run that
  was cancelled or failed leaves the last finished check of the same rubrics to review and confirm.

The question navigator on these steps groups questions by version, with counts and how many
need attention; **Next needing attention** jumps to the next one, the pager stays in view
while a long question scrolls, a question with unsaved edits is marked "· unsaved", and `[` /
`]` move to the previous or next question (never while typing in a field or with a dialog
open). Panels stay mounted, so changing questions never drops a draft. Nothing in the
navigator saves or approves anything. Rubrics remain in printed order; approval happens only
through the mandatory rubric finalization's one confirmation, which shows the exact
interpreted point values.

**The grading pages after setup** (2026-09-26 overhaul; views only, every server check unchanged).

- **Index and page bar.** The page header names the assignment ("MATH 140 · Fall 2026", the stage in the trail); a
  page bar under it holds All assignments, Scan records, Bubble sheets, Gradebook and Exam links, Refresh and the
  **Compute settings** button. Assignments whose scans still wait for Inbox checks are named under the cards, each
  linking to its scan (`waiting`), with no card or graph of their own. A card whose grades went to Gradescope says
  "Sent to Gradescope Sep 25 · 2,679/2,701" (`plan.gradescope`, from Faculty's receipts), else "On Gradescope 0/N ·
  last send …" after a send that delivered nothing. Moving between one assignment's steps reloads only the step's
  content; when the local worker finishes, the step redraws itself unless a dialog is open, a field is being typed
  in or a review decision is unsaved (then its notice offers **Refresh now**).
- **Grade** says in one sentence how many responses are new or must be graded again, and **Grade new responses** is
  the primary only when there are some. Before any scan or response (a quiz drafted but not given) it says "No papers yet"
  and its one primary is **Upload scans**, with no missing-paper warning. Responses that failed to grade are named under the
  lead ("will not be graded again by themselves"), and **Earlier batches** opens by itself when one of them lives there. With
  nothing new, waiting or failed, a **Next** callout leads to the assignment's next step (usually Certification and Export).
  A warning callout names papers that cannot be graded yet (no scan, missing pages, a partial or unreviewed collection) with
  **Open Collection** (the Exams hub's Collection tab) and Scan records; a missing scan is never an absence. A settled batch
  leads with its outcome ("21 graded · nothing waits for you"); **Cancel remaining work** on a running batch asks first
  (nothing graded is lost; the rest becomes new work), and a batch that finished meanwhile refuses it. The batch preview names
  the models on one line with **Change models…**, which opens Compute settings and starts nothing. Choosing *All eligible,
  including regrading* warns before Start how many responses were graded before (or that the grades are certified) and that a
  new grade passing every check replaces any grade they have now; with everything graded already, Start says so and offers **Grade
  them again instead**. Batch cards refresh in place. Older batches sit under **Earlier batches**.
- **Review.** Under the desk heading, **Why you are seeing this** lists the finalization's reasons first, then Faculty's own
  summaries, then the graders' notes by role, then the reader's notes on single lines, each text once (never for a hidden proposal). In
  a group the desk says beside its position how many responses of the question are left. **Save as draft** (not final), **Mark unreadable** and
  **Reject proposal** say what they did in their toast; after an unreadable or rejected mark the next response of the group
  is shown. Regrading a response from the desk marks it "Sent to be graded again" until the new grade lands (or, when its
  batch failed or was cancelled, says once that it could not be graded again), and asks first when the grades are certified. Counts say "final", naming how many were automatic. A question's page has one primary,
  **Review the first group →**. A flag review with nothing to rule on folds into one line above
  the questions, below Failed to grade. When a group is done, a next-step callout names the next
  group, the next question with work waiting, or Certification and Export. A version with nothing waiting folds into one line
  ("Version B · 1 question · 1 of 1 final ✓"), and every question tile links to its page, which states the final
  scores ("Final: mean 3.46 of 6 · 43% full · 10% zero", no student named). **Spot-check 10 automatic grades at
  random** draws automatic finalizations into All responses in the browser (nothing is stored; a correction is the
  ordinary review). All responses opens on **Needs review** when something waits. Keys: digits 1–9 tick the rubric
  line with that displayed number (0 then a digit for 10–19), never inside a text field; Alt+A approves (the review desk has
  no "I inspected…" tick since 2026-09-27: the Approve click is the confirmation); Alt+N and Alt+P move between responses. Like the
  digits, none of them fires while a text field has focus.
  Leaving the desk with an unsaved decision asks first, whatever the route.
- **Certification and Export** (2026-09-26) leads with analysis: statistics, the distribution, the question table and
  a searchable student table with a drawer for final changes; **Certify grades…** is its one primary. The Gradescope
  panel, the files for your records and Saved for training follow as secondary sections. The "Record in the term
  gradebook" link is gone: certifying records the grades. See "Certification and Export" below.
- **Scan records** lists the assignment's scans as whole-row links, those awaiting Inbox checks first, with paper
  counts only when non-zero and one primary, **Continue to <current step>**.

Papers links to the existing Inbox review and exam collection, with relevant scan
and print-job counts. Grade offers questions with approved rubrics; the preview still checks each
response's eligibility and location requirements on the server, including existing
manually configured questions without a blank template. The dialog asks for that
preview by itself, on opening and after every change, and the start button stays
disabled until it finds eligible responses, saying why in the dialog and on the button
(seen live on 2026-09-20: a disabled Start under the global busy cursor, waiting for a
click on **Find eligible responses** that nothing asked for, read as a frozen page). Regrading
options are separate from the routine read-and-grade choice. A cloud batch shows its
estimated cost and its roles before anything is sent, and then
starts at once: there is no cloud consent and no approval of crops (instructor,
2026-09-19; see *Benchmarking and limits*). **See what is sent** on a batch is an optional look.

**Reset batch…** on a finished batch (requirement, 2026-09-20) makes its responses
ungraded again, so another batch can grade them with other models or settings. It
withdraws the batch's unreviewed CURRENT proposals and nothing else: a response with
any review of yours, even a saved correction, keeps its proposal and your decision; a
response a later batch graded again is that batch's to reset; one with work under way
is left alone; a batch that is still working must be cancelled first. Grades finalized
automatically and trial acceptances go with their proposals and are no longer final,
and the dialog counts each of these before you confirm. Nothing is erased: readings,
proposals, calls, costs, acceptances and events stay, and only the response's pointer
to its current proposal is cleared (`grading/batch_resets.py`, migration 015:
immutable `frq_job_resets`, `frq_job_reset_responses` and `frq_job_reset_restores`,
owned by the term grading plan). A reset response has no proposal, so it leaves Review
and cannot be approved, accepted or exported until it is graded again; the workflow
counts it as new work (once, in the progress total) and **New, reset or changed since
grading** in the batch dialog takes it. **Put the proposals back** restores the latest reset
wherever nothing newer exists and nothing is under way; it never replaces a newer
proposal. The server requires `confirmation: "reset"`, the exact preview fingerprint
(409 when stale) and a request ID that makes the reset idempotent; the dialog's tick
is not the control. `GET/POST /api/grading/jobs/<id>/reset`, `POST .../reset/restore`.

**A changed rubric invalidates its grades** (requirement, 2026-09-25: "if a rubric is edited,
it invalidates the grading and it has to be rerun with the updated rubric"). Every new rubric
revision (a line edited on Rubrics & Templates, a line corrected in step three, Edit
interpretation, Import revision) and every new question revision makes the grades made under
the earlier one stale, and so does a changed paper. A stale grade cannot be approved,
finalized automatically or exported (`validate_task`); a batch still running stops that
question's remaining work with "The question or rubric changed after this was graded". Such a
response is not waiting for review: it is new work (`BatchResetsMixin.outdated`, and
`reset_ungraded` for one whose only attempts ran under the earlier rubric), which the Grade
step counts in its new responses ("to grade again", once in the progress total) and **New,
reset or changed since grading** takes. Nothing is erased: the earlier proposal and any
decision stay in the response's history. A decision the instructor made under the earlier
rubric gives way to the new proposal once it exists (the listing's `earlier_review_status`,
the card's `earlier_review`). If every check passes the new grade it is finalized
automatically, as any regraded response is (below); otherwise the response waits for the
instructor. A new batch still needs the rubric finalization confirmed again for the changed rubric.

Review opens on the work that waits for you, by exam version, then question, then the
rubric lines the AI proposed (requirement, 2026-09-20). Versions come first because they
may scramble their questions: Version A's question 18 need not be Version B's. Each
version lists a tile per question in printed order with its count to review (the same
count the workflow calls responses to review). A question's page lists one group per
exact combination of proposed rubric lines, largest first, with its points, size, flags
and any saved corrections; opening a group is the ordinary detailed review, stepping
through only that group's waiting responses, so an approved response makes room for the
next and a saved correction stays. The address carries the question and group, so the
browser's Back goes up a level. This is navigation only (`grading/review_queue.py`,
read-only): nothing is approved, recorded or revealed by it, and a group names no
student. A proposal hidden for a blind review is NEVER sorted by its
lines, since the pile it landed in would show them; those form one **Proposal hidden**
group per question. Work graded against an earlier rubric, question or paper is not waiting
for review at all: each question counts it as **to grade again** (below). **Failed to grade**, at the top of the page, lists responses whose latest attempt failed at a
required step (reading the handwriting, or the primary grader) and so have no proposal: per batch (every batch, however many
newer ones exist), with the recorded reasons,
the questions they belong to and that batch's **Retry failed responses**, never by student. A question whose only remaining
responses failed says so instead of "All reviewed". Optional steps (second grader, arbitrator, flag reviewer) never fail a
response: their failure is a review reason on the response itself. **All responses, approved grades and review tools** keeps
the earlier screen: one response and its rubric, where question and approval-state filters
help find remaining work; **Trial-accepted · not final** lists what the risk policy
accepted as a test, which still needs your look. Group review, bulk inspection, regrading and diagnostics
are in labeled disclosures with an explanation of each tool; **Trial acceptance** sits
with the tools for reviewing several responses together, and **Regrade with Cloud** and
**Read this response again** under *Ask for another opinion* on a response. Adjustments, manual
scores, unreadable and rejection controls remain available. Certification and Export has its own
stage: it names every participant without a grade and certifies only once each has one, is excused, or the
instructor confirmed a 0 (below). Exports revalidate eligibility as before.

1. Choose an existing term assessment. In **Setup**, select **Upload rubrics**
   and choose all Gradescope question CSVs together. No question definitions,
   point entries or response rectangles are required first. When the assessment
   has several printed document versions, select the one the files describe.
   The app creates draft questions using the printed numbers in filenames such as
   `1_Question_18_a_and_b.csv` and the exact exported maxima. A missing printed
   number requires review; the internal Gradescope index is never substituted.
   Known exam versions have separate upload slots, each accepting several question
   CSVs. Nothing is checked or approved while uploading; that is the rubric
   finalization of the third step.
   Without Gradescope CSVs, choose the version's blank template first and draft the rubrics
   from it (*Rubrics drafted from a blank template* below); you review and edit every draft.
2. Choose each version's blank template under **Assignment Templates** on the same step,
   before or after its rubrics. Local OCR verifies the printed numbers, adjacent
   totals and proposed response areas. Repeated identical uploads preserve existing
   work; revised files for an existing question use its **Import revision** control.
   Each file saves independently, with a visible outcome if another file fails.
   For other source formats, **Add question manually** and the per-question import
   remain available. Text and structured copied text, UTF-8 TXT, CSV, JSON, PDF,
   PNG and JPEG are supported there. PDF/image originals are retained
   and read on the worker (20 pages, 10 MiB maximum). Grade exposes preparation history if
   the import dialog closes. Reading never activates a rubric.
3. Inspect the original beside the interpretation. The explicit-points parser
   handles simple deductions/credits and flags wording about how an item interacts
   with other items or a cap (`unless`, `only if`, `do not also`, `mutually`,
   and `maximum`, `at most`, `no more than`, `except` or `not both` only beside an amount or a named
   line, item, part or R-id: `rule-parser-v4`, `not both` since `rule-parser-v5`). Ordinary descriptions
   of the work such as “correct answer, but no work” are not rules: flagging them
   marked the whole rubric conditional and sent every response to manual review. The optional
   local compiler interprets complex rules. Edit descriptions/points/follow-through;
   use structured rules for groups, dependencies, exclusions and exceptions.
   A rubric is approved by the rubric finalization, never on this page.
   Once a compatible blank template and rubric are present, the worker also checks
   the printed question number, adjacent point total and suggested answer areas.
   In **Problem location and printed points**, review the page overlays and choose
   **Confirm locations**, even when all points match. Drag a new rectangle or edit
   its percentages to correct an area. Failed/uncertain checks additionally require
   a manual-review acknowledgment and explanation before grading can proceed.
   A note the reader writes about a page (such as a blank scratch page) holds only
   the questions found on that page for manual review, so the others remain in
   **Confirm all**.
4. Add expected answer, coordinator/instructor/worked solutions, acceptable methods,
   and optional calibration examples in the question editor. Examples contain
   `work`, `rubric_item_ids`, `notes` and `source`; they cannot override the rubric.
5. In **Grade**, find eligible responses and start a batch. Eligibility requires the
   existing exam collection's exact reviewed assembly, current paper and source
   Inbox authentication/review gates. For paper labeled on its first page only,
   your Inbox review of the scan supplies the other pages: each exam whose label
   cadence and side count add up becomes that student's reviewed paper, pages in
   scan order. It is gradable when it has exactly the pages of the chosen blank
   exam; an exam that does not add up is never guessed. Blocked work is explained
   in the preview, grouped by reason.
6. Fully local uses GLM plus local verification/grading. Hybrid adds Mathpix while
   keeping grading local. **Cloud** assigns a handwriting reader, two independent graders,
   a disagreement arbitrator and a flag reviewer from the
   named hosted endpoints of Compute settings (the built-in `nebius` connection
   among them). The batch preview shows the estimated cost before
   anything is submitted. A cloud batch then starts at once: by the instructor's
   decision of 2026-09-19 there is no approval of cloud crops, and nothing waits for
   one. **See what is sent** on a cloud job remains an optional look at its crops.
7. Review one question of one exam version at a time: choose its tile, then a group of
   responses the AI gave the same rubric lines. Inspect original handwriting, enhanced
   views, transcript, solutions, rubric selections and quoted evidence. The compact
   response picker and Previous/Next controls replace the response-card wall.
   Original and enhanced crops open in a separate zoom dialog (fit width, actual
   pixels, zoom controls); closing it preserves the current review or group draft.
   The transcript and rubric display LaTeX with locally bundled KaTeX. Plain text
   remains editable under **Edit transcription** and unchanged in retained records
   unless the instructor saves a correction. Crossed-out/unclear work keeps its
   markings. Each rubric row keeps its checkbox and points visible, with the AI's
   full explanation and evidence in an expandable row; disagreement/ambiguity
   notices remain visible on that row. Clicking
   evidence highlights its crop-relative box when supplied. Adjust selections,
   transcript, comments or points; save as draft (not final), approve, mark unreadable, reject,
   or record a reasoned manual score. Alt+A approves; Alt+N/P moves between responses.
8. **Grade by answer group** (on a question's page and with all responses) approves
   inspected responses together; **Trial acceptance** shows what
   the risk policy accepted as a test and how your review of it went. **Review
   proposed grades** offers individually inspected bulk approvals only
   for current, high-confidence, unreviewed proposals that are not waiting for a blind
   review. Neither high confidence nor a completed job grants approval. (Shadow mode,
   which hid every proposal of a batch until your own decision, was removed on
   2026-09-20: you never used it. Settings saved with it still load, a request that
   still asks for it is graded like any other, and the one reason a proposal is hidden
   now is a blind trial acceptance you have not judged yet.)
9. The Certification and Export step (the Export step until the instructor retooled it on 2026-09-26; see the next
   section) analyses and certifies the grades. Its secondary sections keep what the Export step offered (laid out goal
   first on 2026-09-26: "so many options and it's not clear what they do or if they are redundant"): the
   **Gradescope** panel, and below it, side by side,
   **Grades for your records** (one button per file: scores per question CSV, totals per
   student CSV, the complete audit JSON) and **Saved for training** (this assignment,
   all assignments, and under "What the files hold" the **Model comparison set**, the
   local image/evaluation dataset). The Gradescope Entry View is no longer offered on
   the page (Send to Gradescope does that work, and the per-question CSV holds the same
   selections); the export API still serves it. An incomplete assignment has no total;
   its approved subtotal is explicitly labelled. Export is distinct from external
   publication.
   The same step's **Gradescope** panel sends the final grades to Gradescope (below,
   "Sending grades to Gradescope"): each student's reviewed paper and the rubric items
   of each final grade, previewed and approved first. Sending never publishes; **Publish grades on
   Gradescope…** in Certify's header is its own explicit action (`docs/decisions/gradescope.md`, PUBLISHING GRADES).

## Certification and Export (2026-09-26)

The instructor's request: "The last step of the grading pipeline should actually change to "Certification and Export" and
should be retooled so that the primary goal of the page is to be able to analyze and "fine tune" the grades (it should still
maintain the export capabilities, but that is no longer the primary focus of this step). ... There should be an option to
"certify grades" which would finalize the pipeline and import the scores into the gradebook. It should check that all students
have a grade. If they don't have a grade, or for assignments that have free response plus a bubble sheet part (like exams), it
should raise a warning and make me confirm that these students will receive a 0. It should also flag if there are
assignments/questions/bubblesheets that haven't been exported to gradescope and present the option to export those or confirm
that they shouldn't be exported. ... Until then, this step cannot complete and the assignment remains pending in the grading
pipeline."

**Completion.** The sixth step is done only while a CURRENT certification exists: `workflow.certification.state` is `none`,
`current` or `changed` (`progress.certification_summary`, on every Grading-index poll, catalog reads only). A certification is
`changed` when a counted grade's CONTENT changed (score, lines, adjustments; approving an automatic grade unchanged changes
nothing), the bubble sheets or their key moved (the identification threshold by its value), the participants, their papers, the
roster additions or the linked DR bookings changed, a left-out DR student's (latest) exam date has passed, or new work appeared.
A Gradescope send and a gradebook edit after certifying reopen nothing. There is no uncertify: a newer certification supersedes.
`workflow.export` stays for old readers; nothing in the guide reads it.

**Records** (`grading/migrations/029_certifications.sql`, owned by the TERM grading plan). `frq_certifications`: one immutable
row per certification (the markers it was made on, the snapshot digest, the summary and statistics, the checks as they were, the
instructor's confirmations, the Gradescope decisions with their reasons, and the gradebook receipt); the plan's latest counts and a
newer one names the one it supersedes. `frq_certification_students`: one immutable row per exam participant membership:
`scored` (every part complete, or a part with no work confirmed as 0, reason `missing_part`), `zero` (no work in any part:
`no_paper` or `absent`, earned "0" of one paper's points), `excused`, `left_out` (`dr_ahead` with the latest booking date,
`makeup` while an excused or undecided absence covers the day it was given, or `duplicate_membership`). `parts_json` references each question's final decision (`review:<id>` or `automatic:<id>`, with its
result and response) and the bubble sheet, its latest review and the key revision. No name or UIN is stored.

**The page** (`static/grading-certification*.js`, FRONTEND.md) reads one projection (below): statistics per part, version and
section; per question the final scores, full and zero shares, automatic versus instructor decisions and their basis, the
models' flags and Gradescope coverage; per student their parts, total, flags, DR bookings, Gradescope coverage and whether their
certified grade would change. Names come from the verified roster; THIS step may name and search students (UIN digits only in a
POST body, answered with names), the Review step stays anonymous. Final changes go through the ordinary review editor and
`save_review` (approved or manual; no drafts, which would take an automatic grade out of final). An unreadable or rejected review
records no score: it is a block ("record a grade: approve, or save a manual 0 with a comment"), never a certified 0.

**A line on every grade** (2026-09-27; the instructor: "I should be able to make batch grade changes by adding rubric lines that
apply to all responses of that version and section"; asked, they chose a new line given as a named point adjustment, on final
grades only). The question panel's **Add a line to every grade…** (`static/grading-certification-adjust.js`) asks for the section
(or every section), the line's wording and its points; a question is one version's, so that is the version. The preview (POST
`/api/grading/assignments/<aid>/certification/questions/<qid>/adjustments/preview`, `grading/batch_adjustments.py`) writes nothing
and lists each final grade (by the export's own rule, `final_decisions`) before and after, and each other response of the question
in that section with why it is left alone: not final, the same line already on it, or a score the question's floor or ceiling
holds (an adjustment counts before the bounds, like every adjustment). Applying POSTs the same line with the preview's fingerprint,
a `request_id` and `confirmation: "adjust"`; the server plans again under the roster lifecycle lock (409 when anything changed),
then ONE `BEGIN IMMEDIATE` re-checks each response row and the question's rubric pin and writes one ordinary review per grade: the
counted grade's transcript, rubric lines, earlier adjustments, status and comment, on the result that grade was made on, plus the
line as an adjustment. Each review has its feedback row (`lines_changed.batch_adjustment`, `provenance.replaces`) and
`response_reviewed` event, and one `grades_adjusted` event is the receipt a repeated request ID replays (the same ID with another
line is refused). The rubric never changes, so nothing is regraded and no other section moves; Gradescope receives the line as the
grade's point adjustment and, one row per batch line, as the grade's comment ("+1.5 Misprint in part (c)"; 2026-09-27, batch
lines only: a manual grade's reason or an adjustment typed at the desk is never sent). Already-sent grades show as changed since
sent until the next send, including grades sent before comments were sent. It is not a correction of the models: an automatic
acceptance it replaces records no acceptance review (a blind one records `blind_revealed`), and the review burden counts a batch
as one action. A later rubric change makes these grades stale and regrades them like any decision; the line is then added again.
Every grade's adjustments are named beside its rubric lines, in the question panel and the student drawer. Under the question's
rubric, "Lines added to every grade" lists each batch still on some grade ("on 27 of 28 grades it changed"), with **Edit…** (new
points or wording) and **Remove…**, and a History of every batch (the `grades_adjusted` receipts: `action` add, edit or remove,
`of` naming the batch an edit or removal changed). Edit and Remove send `action` and `batch_id` through the same preview and apply,
and change ONLY the grades that batch changed which still carry its exact line: the line is replaced where it stands, or taken off,
each in one ordinary review; a grade changed by hand since, or no longer final, is left alone and listed.

**Certify.** **Certify grades…** runs the preview (writes nothing; about 20 s on Test 1: `final_decisions` once, the gradebook's
`sources.totals` on it, every check, the snapshot and the gradebook plans), then the certify POST names its fingerprint, a
`request_id`, `confirmation: "certify"`, each confirm check's current digest and each Gradescope group's decision, all checked on
the server. Blocks refuse it: unfinished grading, unreadable/rejected reviews, version decisions, Inbox waits, papers or bubble
sheets not settled, grades that looked final and are not, scores out of range, roster additions, excused or absent students with a
paper or sheet, a live Gradescope send, a student a block names. Confirmations: students with no work receive 0 (absent included;
excused students are never 0), a missing part of an FRQ + bubble assignment counts 0, DR students whose exam is today or later are
left out with their date, duplicate memberships, a sheet version other than the paper's, sheets matched to nobody while a student
lacks one, an all-zero question, written grades or sheets not on Gradescope (send them through the existing dialog, or "do not
send" with a reason; the previous certification's reason is offered when the uncovered set is unchanged or smaller), the
gradebook column and the typed values that stay. The certify recomputes the preview under the roster lifecycle lock (payload
reads first), refuses a changed fingerprint (409), then ONE `BEGIN IMMEDIATE` re-checks catalog high-water marks only (no roster
payload is read inside it) and writes the certification, its student rows, the gradebook's values and a `grades_certified` event.
Nothing is partial; the same request ID replays. It never writes `exam_participants`, never publishes, emails or sends a grade.

**Gradebook.** The certification records its values in the term gradebook through the gradebook's own plan (`sources.plan`,
`common.change_action`: hand-typed or instructor-cleared cells stay unless the instructor ticks Replace), one request of kind
`pull`, source `grading`, summary naming the certification, in the certification's transaction. The column: the live column
linked to the assignment; its parts changed to the certified ones, or kept (it then takes only the certified values of its own
parts); an unlinked column of the assignment linked; or a new column (titled as the assignment, one paper's points, the exam's
sections when fewer than the term's), or an existing column that references nothing. A student left out now whose cell an
earlier certification wrote has that cell cleared. Once a plan is certified, the column's ordinary Pull reads its latest
certification, not live grading; a plan never certified is pulled live as before.

### Checks

| Level | Check | When |
| --- | --- | --- |
| Block | `grading_unfinished` | responses not final (proposed, in review, draft, stale, pending, trial), new work, failed or running tasks |
| Block | `no_grade_recorded` | unreadable or rejected reviews: approve, or save a manual 0 with a comment |
| Block | `version_decisions` | received papers whose exam version waits for a decision or a check |
| Block | `inbox_pending` | linked scans not cleared, bubble sheets in scans under review, OCR first pages not registered |
| Block | `papers_in_review` | collection `needs_review` or `missing_pages` |
| Block | `bubble_unsettled` | sheets needing review, without a version or a key, two sheets on one student, keyed questions past what was read |
| Block | `student_incomplete` | any other participant without every grade (the invariant: each participant is scored, zero, excused or left out, or a block names them) |
| Block | `excused_with_paper`, `excused_with_sheet` | an absent or excused participant with a received paper or a matched sheet |
| Block | `looked_final_skipped`, `score_out_of_range` | preview only: the export's rule refused a grade that looked final; a score outside its rubric's bounds or a total above its possible |
| Block | `roster_additions`, `gradescope_live`, `nothing_to_certify` | roster students not in the exam; a send running; no participants or no part |
| Confirm | `zero_no_work` | no paper and no sheet, not excused, not DR-ahead, no makeup owed (absent included): 0 in the gradebook |
| Confirm | `zero_missing_part` | one part graded, another with no work at all: that part counts 0 |
| Confirm | `dr_ahead`, `makeup_ahead`, `duplicate_membership` | left out (DR exam today or later, every booking shown; an excused or undecided absence covering the day it was given, 0 once not excused and certified again; the empty one of two memberships) |
| Confirm | `bubble_frq_version_mismatch`, `unmatched_sheets`, `zero_question_all` | a bubbled version other than the paper's; sheets of nobody while a student lacks one; a question every student scored 0 on |
| Confirm | `gradescope_written`, `gradescope_sheets` | grades, papers or sheets not on Gradescope: send, or do not send with a reason |
| Confirm | `gradebook_column`, `gradebook_kept`, `gradebook_sections` | preview only: the column to write, typed values that stay, students outside its sections |
| Info | `automatic_unchecked`, `flagged_finalized`, `answer_inconsistency`, `question_all_full`, `version_gap`, `version_max_differs`, `dr_graded`, `bubble_key_suspect`, `rubric_finalization`, `gradescope_published_unknown`, `gradescope_side_changes`, `training_waiting`, `changes_since`, `gradebook_imported_replaced`, `gradebook_cleared` | shown, never required, never part of the fingerprint |

### API

Blueprint `frq_certification`, prefix `/api/grading/assignments` (`grading/certification_api.py`), service
`app.extensions["certification"]` (`grading/certification.py`). Writes need `X-Teaching-Request: 1` and JSON (body limit 1 MB).
No UIN is in any answer; enrollment IDs appear only inside row keys `"<section_id>:<enrollment_id>"`. Every string that comes from
a model or a roster (names, titles, answers, reasons) must be escaped by the page.

| Route | What | Budget (measured on a synthetic-identity copy of Test 1: 340 students, 23 questions, 2,701 responses) |
| --- | --- | --- |
| `GET /<aid>/certification` | the page read (provisional checks) | 1.40 s, 283 KB |
| `GET /<aid>/certification/questions/<qid>` | question drill-down | 0.18 s |
| `GET /<aid>/certification/students/<section_id>/<enrollment_id>` | student detail | 0.34 s |
| `POST /<aid>/certification/students/search` | UIN digits → names | 0.01 s |
| `POST /<aid>/certification/preview` | exact checks, snapshot, gradebook plans, fingerprint (writes nothing) | 19.1 s |
| `POST /<aid>/certification` | certify (one transaction) | about the preview plus a second |

The preview and the certify hold the roster lifecycle lock for their whole read (like a gradebook pull): the page should suspend its
own polls while one is in flight, and use a client timeout of 180 s.

#### Shared shapes

```text
Stats = {n, mean, median, sd, min, max, possible,           // JSON numbers, 2 decimals; sd sample (n-1), null when n < 2
         histogram: [{from: 0, to: 10, count}, ... 10 bins of percent of possible; the last [90, 100] closed],
         excluded: {zero, missing_part, missing, left_out, excused}}   // `excluded` only on stats.total; {} elsewhere

Check = {id, level: "block"|"confirm"|"info", title, detail, count,
         digest?          (confirm only; the certify POST names it in `acknowledged`),
         requires?        "tick" | "decision" (confirm only; "decision" = the Gradescope groups),
         rows?            [{row?, name, section, detail, ...}]  at most 400; `count` is always the whole (of what the title
                          counts: grades over question rows for automatic_unchecked/flagged_finalized, grades and papers over
                          student rows for gradescope_written; the page's row folds count the rows),
         action?          {label, href} | {label, kind: "send_written"|"send_sheets"},
         provisional?     true on the page read (the preview checks everything again),
         // extras by check:
         warning          (zero_no_work: unmatched sheets / papers without a student / unregistered first pages, or ""),
         questions, never, sendable, prefill    (gradescope_written: per question {question_id, title, not_sent, changed, unsendable};
                                                 `never` count of grades/papers Faculty can never send; `prefill` the reason to offer),
         never, sendable, prefill               (gradescope_sheets),
         category_details                       (gradebook_column of a new column, when the gradebook has categories:
                                                 {category_id or "": the check's detail for that category}; no `options` list
                                                 any more, the preview's `gradebook.options` are the columns)}
```

Check ids: block `student_incomplete`, `excused_with_paper`, `excused_with_sheet`, `grading_unfinished`, `no_grade_recorded`
(unreadable/rejected reviews, critique C1), `version_decisions`, `inbox_pending`, `papers_in_review`, `bubble_unsettled`,
`looked_final_skipped` (preview), `score_out_of_range` (preview), `roster_additions`, `gradescope_live`, `nothing_to_certify`;
confirm `zero_no_work`, `zero_missing_part`, `dr_ahead`, `makeup_ahead`, `duplicate_membership`, `bubble_frq_version_mismatch`,
`unmatched_sheets` (confirm while a student lacks a sheet, else info), `zero_question_all`, `gradescope_written`,
`gradescope_sheets`, `gradebook_column`, `gradebook_kept`, `gradebook_sections` (the last three preview only); info
`rubric_finalization`, `version_gap`, `version_max_differs`, `dr_graded`, `question_all_full`,
`automatic_unchecked`, `flagged_finalized`, `answer_inconsistency`, `bubble_key_suspect`, `gradescope_published_unknown`,
`gradescope_side_changes`, `training_waiting`, `changes_since` (preview), `gradebook_imported_replaced`, `gradebook_cleared`
(preview).

#### GET /<aid>/certification

```text
{plan_id, title, event_id, term_id,
 parts: ["frq","bubble"],                                   // what is certified: the parts the gradebook can pull for this plan
 possible: {frq: "40", bubble: "60", total: "100"},         // one paper, per part (sources.maxima); total null when unknown
 versions: ["A","B","C"],
 sections: [{section_id, number}],
 certification: {state: "none"|"current"|"changed", id, created_at,
                 changed: ["grades"|"bubble_sheets"|"students"|"dr_date_passed"|"makeup_ended"|"new_work"|"rule"],
                 gradebook: {item_id, title, written, kept} | null, left_out},
 history: [{id, created_at, supersedes_id, rule, students, scored, zero, excused, left_out,
            gradebook: {item_id, title, written, kept, created_column} | null}],      // latest 10, newest first
 stats: {total: Stats, frq?: Stats, bubble?: Stats, exams?: Stats, by_version: {"A": Stats}, by_section: {"507": Stats}},
 questions: [{question_id, title, printed_number, version, max, n_final, not_final, mean, median, sd, pct_full, pct_zero,
              full, zero, automatic, instructor, basis: {team_consensus: n, final_arbitrator: n, flag_review: n, ...},
              automatic_unchecked, flagged_finalized, flagged, overridden,
              gradescope: {sent, changed, not_sent, unsendable: reason|null}}],        // version, then printed number
 bubble_questions: {"A": [{number, answer, sheets, correct_pct, common_wrong}]},
 students: [{row, name, section, exam_version, participant_version, expectation, collection,
             status: "complete"|"missing"|"missing_part"|"in_progress"|"excused"|"dr_ahead"|"makeup"|"left_out",
             reason,                                       // one line: "Every part is graded", "No bubble sheet", "Free response: 3 of 4 questions final"
             frq: {state, earned, possible, final, expected},
             bubble: {state, earned, possible, sheet_status},
             total: {earned, possible, pct} | null,        // scored and zero rows only
             scores: ["3", null, ...],                     // aligned to `questions` of the student's version (no version: only
                                                           // questions without a version); null = no final grade
             flagged, zero_questions,
             dr: [{date, start_time, end_time, campus, exam_type}],
             gradescope: {paper: bool, grades: {sent, changed, not_sent}, sheet: bool|null},
             certified: {status, earned} | null, changed_since: bool}],       // name order
 counts: {participants, complete, missing, in_progress, flagged, zero_somewhere, changed_since, dr, excused, not_on_gradescope},
 checks: [Check], blocking, to_confirm,
 gradescope: {configured, live, written: {papers: {total, sent}, grades: {final, sent, changed, not_sent}},
              sheets: {matched, sent, waiting} | null},
 live: bool}                                                 // grading tasks running or a Gradescope send live: poll while true
```

`participant_version` is the `exam_participants` row version the Exams page's PATCH needs (critique C12); `exam_version` is the
paper's letter.

#### GET /<aid>/certification/questions/<qid>

```text
{question: {question_id, title, printed_number, version, max,
            rubric: [{id, number ("3.2" from Gradescope's export, else the position), description, points}],   // original wording
            stats: Stats with histogram: [{score: "0", count}] by distinct final score},
 lines: [{id, count, pct}],                                  // how often each line is in the FINAL selection
 responses: [{response_id, row, name, section, score|null, state,
              decided: {kind: "instructor"|"automatic"|"none", status, basis|null},
              flagged, flags: ["reading","grading","finalization","reasons","arbitrator","flag_reviewer","disagreement","risk","waived"],
              overridden, selected: [line ids], gradescope: "sent"|"changed"|"not_sent"|"none",
              answer_group: the answer-group key or null}]}   // name order
```

Flags come from the result the grade was DECIDED on (an instructor review's own result, never a newer proposal, critique C8).

#### GET /<aid>/certification/students/<section_id>/<enrollment_id>

```text
{student: {row, name, section, exam_version, participant_version, expectation, reason, collection, collection_issues: [..],
           paper_version: {status, version, reason}},
 parts: {frq: {final, expected, earned|null, possible|null},
         bubble: {sheet_id, status, version, earned, possible, reason, identified_by, wrong: [numbers], job_id, page, href, several} | null},
 questions: [{question_id, title, printed_number, version, max, response_id|null, state, score|null,
              decided: {kind, status, basis, actor, at},
              flags: {names: [...], original_state, base_state, reasons: [..], risk_blocks: [..], risk_waived: {...}, risk_individual,
                      arbiter: {outcome, reason, flags}|null, flag_review: {outcome, reason, flags}|null,
                      committee: {status, reason, review_reasons}|null},
              later_proposal: bool,                        // a newer proposal exists beside the instructor's decision: label it so
              missing_reason: text|null, gradescope}],
 dr: [{date, start_time, end_time, campus, exam_type}],
 certified: {status, reason, earned, possible, exam_version, questions: {qid: {score, max, decision, result_id, response_id}},
             certification_id, created_at} | null}
```

404 when the membership is not a participant. Crops, transcript, rubric applications and model comments come from the existing
`GET /api/grading/responses/<rid>` (never with `reveal`).

#### POST /<aid>/certification/students/search

Body `{digits: "4 to 12 digits"}` → `{matches: [{row, name, section, similarity}]}`. 400 for anything else; the digits are never
echoed in an answer or an error.

#### POST /<aid>/certification/preview

Body `{}` or `{gradebook: {replace: [row], column: {action, item_id?, parts?}, category_id?}}` (`replace` may also be top level).
Neither the Replace ticks nor the column choice changes the fingerprint: every column the certification may record in has its plan
fingerprinted, and each option lists its own gradebook checks, so the dialog switches columns and ticks without a new preview. A
new column's `category_id` is in no fingerprint or digest either: the dialog chooses it (preselected with `proposed_category_id`)
without a new preview, and the certify POST carries it.

```text
{fingerprint, rule: "certify-v1", parts,
 snapshot: {students, scored, zero, excused, left_out, missing_part, stats: Stats},
 checks: [Check], blocking, to_confirm,
 confirm: [the confirm checks, as in `checks`],   // zero_no_work rows also carry `absences` (below)
 gradescope: {configured, written: {final, sent, changed, not_sent, papers: {students, sent}}, sheets: {matched, sent, waiting}|null},
 gradebook: {term_id, revision,
             options: [{key, column: {action, item_id?, parts?}, item, counts, kept, outside, checks: [that column's gradebook checks]}],
             selected: key, item: {id|null (a column to create), title, points, parts, sections, category_id},
             column, counts: {add, update, same, kept, waiting, none},
             rows: [{row, name, section, action, proposed, current, reason}],     // at most 400
             kept: [{row, name, section, current, proposed, reason}],             // typed or cleared by hand: stay unless Replace
             imported: [{row, name, section, current, proposed}],                 // import values the certification replaces
             cleared: [{row, name, section, current}],                            // earlier certified values of students left out now
             warnings: [..],
             mode: "points"|"weighted", categories: [{id|null, name, weight|null, counted}],   // Uncategorized (id null) last
             proposed_category_id: id|null},   // for a new column: an unlinked same-named column's category, else that of the
                                               // latest linked column whose title starts with the same word ("Quiz 2" for "Quiz 3")
 changes: [{row, name, section, before: {status, earned}|null, after: {status, earned}}],   // vs the latest certification, ≤ 400
 latest: certification id | null}
```

Column options (`key` → `column`): `create` → `{action: "create"}` (no live column references the assignment; also `use:<id>` →
`{action: "use", item_id}` for each live column that references nothing); `link:<id>` → `{action: "link", item_id}` (the column
references the assignment without a link); `parts:<id>` → `{action: "parts", item_id, parts: "certified"}` and `keep:<id>` →
`{action: "ok", item_id, parts: "keep"}` (its linked parts differ from the certified parts); `ok:<id>` → `{action: "ok", item_id}`.
The first option is the default. Without a column of its own that is `create`, unless exactly one `use:` column has the
assignment's title (spaces and case aside): that one is listed first and is the default (requirement, 2026-10-02); two such columns,
or none, keep `create` first. The order is in no fingerprint.

Each row of the `zero_no_work` check (the students about to receive 0) carries `absences: [{first_date, last_date, dates, status,
covers?}]` (requirement, 2026-10-02, display only): every absence recorded for that section membership in the term, oldest first,
`status` one of pending, excused, not_excused, withdrawn, and `covers` (true or false) only when the assessment has a date for the
student and the absence is not withdrawn (the student's own sitting's date when the Exams page names one, else the earliest exam
date of the assignment's seating charts naming their section, a DR exam's chart and a makeup's or DR sitting's chart left out, else
the event's first main sitting with a date). `[]` when none. Rows are in no digest: the fingerprint and every digest are the same with or without them, and certify
never reads them.

#### POST /<aid>/certification

```text
{request_id: UUID, fingerprint, confirmation: "certify",
 acknowledged: {check_id: digest, ...},                      // every confirm check with requires "tick", its CURRENT digest
 gradescope: {written?: {decision: "do_not_send", reason: 1..500 chars}, sheets?: {...}},   // each Gradescope group shown
 gradebook: {replace: [row], category_id: id|null, column: {action, item_id?, parts?} | {}}}
```

- 400 before anything is read: unknown key, no `confirmation: "certify"`, `request_id` not a UUID, a reason empty or over 500, a
  malformed row key.
- 409 `{error, checks}` (the checks as they are now): the fingerprint changed ("The grades changed since the preview. Check
  again."), a block, a confirmation missing or naming an old digest ("Confirm before certifying: ..."), a Gradescope group
  answered `send` while it is still uncovered (send it first through the Gradescope dialog, then check again), a mark changed
  between the read and the write, another certification meanwhile.
- 409 `{error}`: the request ID was used with another body; the gradebook changed (setup revision or a replaced cell).
- 201 `{certification: {id, created_at, supersedes_id, rule, students, scored, zero, excused, left_out, left_out_dr, left_out_makeup?, gradebook: {...}},
  replay: false}`; 200 with `replay: true` for the same request ID and body (nothing written again). The page reads its new state
  with its own GET (the answer carries no view: building one took the lifecycle lock again, and its failure answered 500 for a
  certification that had been recorded).

#### Gradebook

- `GET /api/gradebook/terms/<term>` → `links[item].certification = {id, created_at, may_have_changed} | null` (a catalog-only hint:
  `progress.grade_marker`, kept in the certification's `summary_json`, differs from the catalog now: stored grade content, rubric
  pins, participants, DR bookings, bubble marker; the exact state is Grading's). `links[item].grading_changed_at` is the latest
  certification's time once the plan is certified.
- A pull of a certified plan reads the latest certification: `PullPlan` adds `certification_id` and `certified_at`; its fingerprint
  names the certification (a newer one makes an older preview stale). A request row of a pull or of a certification has
  `summary.certification_id` and `summary.source = "certification"`; the certification's own request ID is `certify:<request_id>`
  (its column setup revision `certify:<request_id>:column`).
- A proposed value may now be `{status: "cleared"}`: a left-out student's cell that an earlier certification wrote.

## Rubrics drafted from a blank template (2026-09-23)

A version without Gradescope rubrics can have them drafted instead, for any assignment: an exam,
quiz, homework set, worksheet, lab or project handout (requirement, 2026-09-23: "assignment
agnostic, not necessarily just for exams"). Under **Assignment Templates** on the Rubrics &
Templates page, choose or upload the version's blank template, then **Draft rubrics from this
template**. The same model that reviews the rubrics, **Compute settings → Other models → Rubric
drafting and review model** (it must read images; the selector warns when it cannot), does the
work in two kinds of request: one reads every page of the template (at most 40) and lists its
problems (number, printed heading, printed points, pages, lettered parts, statement), then each
problem's rubric is written in its own request with the pictures of its pages, alone in its
context like the rubric check. Problems printed without whole numbers ("Exercise 2.3",
"Problem A", or none) are numbered by position, and their printed heading becomes the editable
question title. Multiple-choice problems (and multiple-choice parts) are pass/fail on the choice
alone (requirement, 2026-09-25): since writer prompt v4 (`rubric-draft-v4`; all-versions writer v2,
`rubric-draft-versions-v2`) such a problem is drafted as exactly "Correct choice: $$(c)$$ $$...$$"
for every point and "No answer or completely incorrect", with no line that credits, requires or
mentions work; on versions, each names its own correct option. The proposed answer key then accepts
either the label or the option's content. Drafts made before keep the lines they were written with.
Points need not be printed: the draft then proposes a total and says so. A
calculation gets a model solution with its final answers; a proof, explanation or other open
response gets the elements a complete answer must contain. Drafts follow the department's
Gradescope style with positive scoring: a full-credit line naming the final answers in `$$ $$`
(or "Complete and correct response: ..." where there is no single answer), step lines that add up to the
question's value (per part, after a `Part A:` line worth the part), and a zero
"No answer or completely incorrect" line. The model writes no combination rules: which lines
are alternatives is decided in Key & Rubric Finalization, as for uploaded rubrics.

Nothing is created by drafting. **Review and create questions** shows every draft beside its
warnings (points not printed, lines that do not add up, a missing full-credit or no-credit line)
and the model's notes; the printed number, the points, each line, the statement and the worked
solution are editable, and each question is kept or left out. **Create selected questions** makes
each kept draft an ordinary draft question through the rubric upload's own path
(`create_draft_question`): the accepted rubric is retained as its source file through Inbox storage,
with a draft revision (compiler `model-drafted-v1`, naming the draft, model, template and whether
it was edited), the term pin and an `frq_rubric_uploads` receipt. Questions are independent: one
that is refused (a number that already exists for the version, invalid points) never hides one
that was created. The statement and worked solution become the question's `problem` and
`worked_solution`; the answer key is still only proposed (from the full-credit line) until saved
in the third step. The template a draft was read from becomes the version's default template,
still confirmed with Continue as always. Templates can now be chosen and uploaded before any
rubric exists; confirming them still needs every version's rubrics.

A generation (`grading/rubric_generation.py`, migration 020 `frq_rubric_generations`) belongs to
the term grading plan and references the course-wide template and printed document version (plus
exam version) it read. Its requests are ordinary `frq_cloud_calls` owned by it
(`rubric_generation_id`): written before they leave, retried by the cloud policy, replayed from
their receipts after a wait or a restart, and a refused answer asked once more. The dispatcher claims waiting drafts in the rubric check's turn. A problem
that cannot be drafted is listed as failed while the others are kept; a draft can be cancelled or
drafted again. Generated rubrics do not exist on Gradescope: create them there too if you grade
there, and expect Gradescope exports to name Faculty's lines.

### Adapting one version's rubrics to the other versions

Instructor request of 2026-09-25: "take version A's rubric and generate the other version's rubrics
so that the grading is consistent across versions." When an assignment has more than one version and
one of them has rubrics, the Rubrics panel offers **Adapt Version A's rubrics to other versions**
(`static/grading-rubric-adapt.js`; another version with rubrics can be chosen as the reference). Tick
the versions to adapt to and choose each one's blank template: the model reads that version's
problems from it. Each ticked version gets its own generation, shown and reviewed in its template
slot like a draft (**Review adapted rubrics**).

The Rubric drafting and review model does three things, each an ordinary cloud call of the
generation (`kind: adapt`, rule `rubric-adapt-v1`):

1. **Survey** the version's blank template with the drafting survey (`rubric_survey`).
2. **Pair** (`rubric_adapt_match-v1`, step `rubric_adapt:match`): ONE request sees the reference
   version's problems (number, title, points, statement and answer key where known, rubric
   wording) and this version's surveyed problems, and names for each problem of this version the
   reference problem it is a version of, or null. It pairs by what the problems ask, never by
   printed number (versions may reorder problems and parts). The reference version's chosen blank
   pages are attached when some reference statement is unknown, as for an uploaded CSV. Each
   problem of this version is named once and each reference problem used at most once
   (`version_pairs`); a refused answer is asked once more.
3. **Adapt** (`rubric_adapt-v1`, step `rubric_adapt:q<number>`): one request per paired problem,
   alone in its context, sees that one reference rubric (every line with its ID, points and wording,
   and the instructor's comment on it) and the target problem with its pages. It returns the
   target's worked solution and final answers and EVERY reference line rewritten for the target,
   by ID: only the version's values, answers, names and part letters change. It never returns
   points: each line keeps its reference line's points (`adapted_rubric`). Unpaired problems are
   listed as not adapted (draft them or upload their CSVs), and unpaired reference questions are
   noted.

The review dialog shows each adapted line above the reference line it came from (with the
instructor's comment there), plus findings: printed points that differ from the reference total, a
different number of lettered parts, and lines that still name a value of the reference problem which
this version's problem and answers do not have, each value named (grading contract, "LEFTOVER
VALUES"). Every line, the points, number, title, statement and solution stay editable. What is
kept goes through `create_draft_question` like a draft (compiler `model-adapted-v1`, naming the
reference question and rubric revision), but its rules are the REFERENCE rubric's
(`adapted_rules`): the same line IDs, groups, follow-through, dependencies, exclusions and
conditions, with the accepted wording and points (the importer's own condition mark follows the
wording, as an edited line's does). A line the instructor cleared is dropped with every reference to
it; a line they added is new and read by the ordinary wording rules; a structured condition that no
longer validates goes back to review with a warning. A question whose reference rubric changed since
it was adapted is refused for that question alone: adapt again. An optional tick copies the
instructor's comments on the reference lines to the same lines of the new questions (append-only;
a line with any comment history is left alone). Nothing is approved: the rubric review and the
mandatory rubric finalization follow per question, as for every rubric.

### Drafting every version at once

Instructor request of 2026-09-25: "one button that generates the rubrics for all versions
simultaneously, that way the model sees and creates them all at once to ensure consistency across
versions." Assignment Templates now has ONE **Draft rubrics for all versions** button
(`static/grading-rubric-versions.js`; "Draft rubrics from the template" when there is one version).
It uses the blank template chosen in each version's slot and one scoring mode. Version slots no
longer draft alone; their earlier drafts and adaptations can still be reviewed there. One
generation covers the plan (`kind: versions`, `exam_version` `'*'`, rule `rubric-draft-versions-v3`,
its input naming every version's template), and only one draft runs per plan at a time.

1. **Survey** each version's template with the ordinary survey (`rubric_survey`), side by side,
   steps `rubric_draft:versions:survey:<n>`.
2. **Group** (`rubric_group_versions-v1`, step `rubric_draft:versions:group`): ONE text request sees
   every version's surveyed problems and groups each problem with its copies on the other versions,
   by what they ask, never by number (`version_groups`: every problem once, at most one of each
   version per group). A single version needs no grouping.
3. **Write** (`rubric_write_versions-v3`, step `rubric_draft:versions:problem:P<n>`): one request per
   problem sees all its versions (statements and up to three pages of each) and returns ONE list of
   lines: each line has one point value and a wording per version, naming that version's values and
   answers, plus each version's worked solution and final answers. `family_rubric` refuses a line
   without every version's wording, so the versions cannot use different kinds of lines.

Since 2026-09-28 (writers `rubric_write-v5` and `rubric_write_versions-v3`) every line is written
against the questions graders of earlier drafts kept stopping at: a required word, label, variable
name or form is stated by its meaning (by default a recognizable abbreviation or misspelling, other
letters used consistently, a label without "=" or "(x)", or an exactly equal form of a value counts,
unless the problem prescribes the characters), with examples only as examples; what happens when an
element is blank, left out or replaced by other work; which methods and how little shown work count;
what a line judged in the student's own labels does when those are missing or wrong; whether it reads
the designated answer or any work, and what an extra or conflicting item does; and which one line
charges each single mistake. Those cases go in each line's **clarification for the graders**, beside
the wording Gradescope shows: the review dialog shows it folded under the line (per version), you can
edit it, and creating the question saves it as that line's comment, so the rubric check carries it
into the clarifications you confirm. It never becomes part of the rubric. "No answer or completely
incorrect" keeps its exact wording, and its clarification says what completely incorrect means: no
other rubric item applies. A deducting draft has no such line: it is the full-credit line and its
deductions, which cover every required element as "missing or incorrect" and normally add up to the
problem's points (alternatives may add up to more; the score floors at 0). No partial-credit line is
worth as much as its part's answer line.

The review dialog shows each problem's versions side by side: one points field per line and a
wording box per version, with each version's number, title, statement and solution, and a tick per
version to create it. Created versions of a problem get the same line IDs (R1, R2 ... by line) and
points; clearing one version's wording leaves that line out of that version only. Questions are
created through `create_draft_question` as drafts are (compiler `model-drafted-versions-v1`, naming
the problem and version), and the rubric review and mandatory finalization follow per question.
The requests stay one per problem rather than one for the whole assignment, since every rubric of a
long exam in one answer would outgrow what a model returns reliably.

**Replacing an existing question's rubric** (requirement, 2026-09-27: a regenerated Quiz 2 draft could
not be used, since all 16 questions already existed). A version whose question already exists (same
document, version and printed number) shows "Replace its current rubric with this draft" instead of
"Create for": unticked, one box per version (a **Select all** at the top ticks or unticks every open
box, to create and to replace alike), its number read-only. Ticking it gives
that question a new rubric revision from the draft's lines as edited (compiler `draft-replaced-v1`,
naming the rubric it `replaces`, with the draft's own record under `based_on`); the question, its
statement, solution and answer key stay. The draft must be worth the question's points, and a rubric
changed since the review opened is not replaced. As after any rubric change the question's grades go
stale and are graded again, its confirmation for review and finalization are asked for again, and its
line comments are cleared (kept in the history); its confirmed answer area is carried forward.

## Rubric finalization (mandatory)

Instructor decision of 2026-09-20. In a measured batch 60% of 160 rubric doubts the graders
wrote asked one question a Gradescope export cannot answer (does the full-credit line stack
with the step lines?), and every doubt sends a response to the reviewer and then to you. The
rubrics are therefore settled ONCE, before grading, in the third step beside the answer key.
No batch can be previewed or started, in any mode, until the current rubric revisions are
finalized, and there is no switch to turn this off.

1. **Built-in exclusions** need no model and cannot be edited: a line worth the whole
   question excludes every other line, and so does a zero line whose whole wording is
   "No answer / Completely incorrect" for the WHOLE question ("No Answers for either parts a
   or b" counts). "Part B: No Answer or Completely Incorrect" and "No Matrix Given or
   Completely Incorrect" are one part's zero and are deliberately not matched. In a deducting rubric the line that deducts
   everything plays that role.
2. **Run rubric check** sends each question BY ITSELF (its rubric, answer key, solution and,
   for a model that reads images, the problem as printed on your confirmed blank exam) to the
   rubric model (**Compute settings → Other models → Rubric drafting and review model**, which belongs to no one scheme, with its own
   reasoning choice beside it; otherwise Standard's primary grader). Questions are asked side by
   side, so a whole exam takes about as long as its slowest question, and a question whose answer
   is refused is asked again alone. No request holds two different problems. **An assignment with
   versions is checked one PROBLEM at a time with every version side by side** (instructor,
   2026-09-25, `rubric_quality_versions-v1`): the questions are first grouped into problems, from
   how their rubrics were made (adapted from another version, or drafted together) and, where that
   does not account for every question, by one text request that groups them by what they ask and
   what their rubric lines pay for, never by printed number. Each problem's request holds every
   version's rubric, key, comments and printed problem; parallel lines get the same clarification
   and exclusions, your comment on one version's line is carried to the parallel lines of the
   others, and differences between the versions' rubrics (points, a step one version credits and
   another does not, stricter wording) are listed under **Consistency across versions**, never
   fixed. The finalization window lists each problem's versions together.
   **Linked versions** (requirement, 2026-10-02): lines that correspond across a problem's versions
   are linked per line, as **the same** on each version (one clarification fits, with each
   version's own values) or **related** (the same step with a different outcome, like a matrix
   expression undefined for different reasons on two versions and defined on a third: decided per
   version). Since `rubric_quality_versions-v4` the reviewer returns these links and names each
   finding ONCE for the versions where it arises, so the Decisions view shows it as one decision
   ("Applies to Versions A, B · not raised on Version C"), with **Decide Version C separately**
   when one version needs another answer. Your one answer reaches each version through **Resolve
   concerns**, and the reviewer writes each version's own guidance from it. Under **Rubric items**
   a problem's versions are one panel, linked lines side by side: **Copy clarification to linked
   versions** (only "same" links; it warns when the text names values), exclusions mirrored to
   "same" lines (a switch, on by default), and **Unlink**, **Mark as related/same** and **Link to**,
   saved at once on the term plan and kept for the next check. Links change no rubric, guidance,
   fingerprint or grade. On **Rubrics & Templates** a changed line comment offers **Also put this
   comment on** the linked lines of the other versions, and **Confirm Versions … for rubric
   review** confirms a problem's versions together; **Correct wording** offers the linked lines of
   the other versions, each with its own wording, none ticked. When Faculty drafted every version at
   once (a quiz draft, or **Draft rubrics for all versions**), the writer marks each shared line as
   the same or related (`quiz_rubric-v4`, `rubric_write_versions-v4`; editable as **Across the
   versions** in the draft review), so the links start from that mark, "as drafted, not reviewed
   yet", until the rubric check or you decide otherwise. Per line it proposes a **clarification**, only where two readings of real
   work would change the selection or the credit; further **exclusive pairs**; and **suspected
   errors** (a line that disagrees with the key or with the numbers printed in the problem,
   points that cannot reach the maximum). The reviewer tests hypothetical boundary cases for evidence location,
   conflicting final answers, required presentation, transposed matrices, follow-through, unfinished calculations,
   and work requirements. Since 2026-09-28 (`rubric_quality-v7`, `rubric_quality_versions-v2`) it also settles
   the near variants a class writes of any required word, label, variable or form (abbreviations, other
   letters, a missing "=", a $ identifying a revenue, 12 = 12 for 0 = 0), blank or replaced elements,
   which methods count (rref and how its use is shown, unfinished work, a graph read checked by
   substitution), labels or variables of the student's own that are missing or wrong, one mistake that two
   lines could each charge, and when "No answer or completely incorrect" applies beside the other lines. A
   clarification states the criterion itself with examples only as examples, and your comments on other
   problems' lines come along as your policy wherever the same question arises. It proposes only consequential clarifications, without a target count. When the supplied
   policy cannot settle a credit-changing distinction, it reports an **Instructor decision needed** with the
   affected item IDs and competing readings rather than guessing a clarification. Missing or invalid output fails visibly and
   confirms nothing. A check can be cancelled at any time: its requests in flight are closed, and each call keeps its record.
3. **The points decide when it is done.** When a question's lines add up to more than it is worth,
   some of them must be alternatives (the correct slope, the slope with a slip and the inverted
   ratio; one answer in point-slope and in slope-intercept form). The reviewer is asked to find
   those groupings, and asked once more with the exact lines its pairs still allowed. Whatever is
   still open is shown per question as you tick pairs ("R5_2, R5_3, R5_8, R5_10 can be selected
   together for 7 of 6 points"), and the rubrics cannot be confirmed, or a batch started, until
   no allowed combination exceeds its question. A confirmation made before this rule that leaves
   a question open holds back only that question's responses until you resolve it.
   **Comment on the concerns** (requirement, 2026-09-25): under each suspected error and
   consistency finding, open **Comment on this concern** and say how it should be resolved,
   then choose **Resolve concerns**. The rubric model revisits the problems the concerns name
   (each with all its versions, one request per problem), follows each comment, and settles the
   concerns you did not comment on itself, consistently with the problem, key, rubric and your
   comments; the window marks those it settled on its own. It revises the clarifications and
   exclusions, starting from what the window holds, your unconfirmed edits included. Each concern
   comes back resolved (with what changed) or still open (with what is still needed: a change to
   a line's wording, points or the key is yours to make in the rubric editor). Open concerns never
   stop you confirming: the confirmation tick sits beside **Confirm and approve the rubrics**. Changed questions say so, each
   changed line shows what it said before, and your last comment stays beside each concern.
   Comment again as often as needed; nothing is approved until you confirm, and a failed or
   cancelled round keeps the earlier review and your comments.
   **Show rubric** beside each concern opens that question's rubric (every version a consistency
   finding names) in a pop-up over the window, with the lines the concern names marked and each
   line's current clarification, so you can answer without scrolling to the question and back.
4. **Review and confirm.** Edit any clarification, tick or untick pairs (both directions at
   once, never transitive), read the findings, then confirm. One confirmation records an
   immutable policy AND approves every rubric. To correct a real error, import a rubric
   revision or fix the key, then run the check again; a clarification never changes points.

The finalization window opens on **Decisions**, showing one concern and the affected rubric lines together. New checks give
each concern a short title and a specific question; when the model can support concrete alternatives, you can choose one to
draft editable feedback, with its point consequence where established by the rubric. Choosing sends and approves nothing.
If the model has no supported alternatives—or the check predates this layout—you get an open feedback box instead. You can
describe your policy, ask the reviewer to investigate, edit a clarification beside its line, or leave the concern open. Use
**Resolve concerns** to send your drafted feedback. Shared decisions on parallel versions appear together, retaining each
version's lines and original findings. **Rubric items** opens the complete guidance and exclusions; switching views or decisions
keeps your edits. The footer's approval remains one explicit confirmation, and only unresolved points combinations block it.

**Department policy: the rubric's wording never changes.** Clarifications and exclusions are
internal guidance kept BESIDE the immutable rubric. Every AI grader reads each original line
with its clarification and what it may not be combined with; the rubric page, the review
screen, scores and every export keep the original wording, and nothing is written into a
rubric revision. Scores remain the approved rubric's alone: a proposal that selects two
exclusive lines is flagged for individual review and kept out of automatic acceptance (never
refused or rescored), and your own selection is recorded, never blocked.

A batch keeps the finalization it was submitted under. Confirming a newer one makes an open
preview stale. Replacing a rubric requires a new check and confirmation; correcting an answer
key does not.

**One question's rubric holds back only its own grades** (2026-09-25; before, editing one of
Test 1's sixteen rubrics took every automatic grade out of the export). The finalization stays
one confirmation that every new batch waits for, but what it decided is judged question by
question. A question whose rubric and comments are what your latest confirmation covered keeps
that confirmation's clarifications and exclusions, stays ready for the reviewer, and its
automatic grades stay final while you edit, check and confirm the others. Editing, importing or
correcting a question's rubric suspends only that question's automatic grades; asking the reviewer
again suspends nothing; and a confirmation suspends only the grades of questions whose
clarifications or exclusions it changed. When you confirm again, each question you did not change
starts from what you confirmed before (the reviewer's new suggestions are shown beside it, and
blank clarifications stay blank), and the confirmation tells you how many questions were kept,
how many changed and how many automatic grades went back to review.

**A mistake in the rubric itself** (a wrong value in its correct answer) is yours to correct:
under each question of this step, **Rubric lines · correct a wording** → **Correct wording**.
The correction is a new immutable revision with that ONE line's wording replaced; points, IDs
and every other line stay, and the uploaded wording stays on the line's record. Your confirmed
exclusions and clarifications are carried to the corrected revision and it is approved with
them, so nothing has to be confirmed again (unless the new wording changes what the built-in
rules find, such as turning a line into the no-answer line). Responses of that question graded
under the old wording need grading again. Faculty changes nothing on Gradescope: correct the
line there as well. The hidden **Edit interpretation** of the first step remains for points,
groups and dependencies; a revision made there needs a new check and confirmation.

**Full-credit selections and full numeric totals are distinct.** A grader that ticks the full-credit line beside step lines
is read as that line alone. Step lines that add up to exactly the question's value are promoted only when the grader also
explicitly confirms `full_credit_eligible: true`: the whole-question item's actual requirements are met, independently of
the total. A false or missing eligibility decision preserves the component selections and the reason for rejecting the
full-credit item, even when they earn full marks (rule `full-credit-alone-v4`, frozen per batch; a batch submitted under an earlier
rule finishes under it). What the model ticked stays on the record and on the review card, and
the score is identical. Never when the no-answer line or a zero line is ticked too, when two of
the ticked lines exclude each other, or when the ticks add up to more than the question is worth.
Since v3 a **part's** full-credit line is read the same way first: where the rubric's lines begin
"Part A:", each part has one line worth more than its others, those lines add up to exactly the
question's value, and your finalization keeps each apart from every other line of its part, a
grader that ticks "Part A: Correct Answer" beside Part A's step lines is read as that line alone.
Both parts' lines can then add up to the question's value and, with explicit eligibility under v4, be read as its full-credit line.
Never beside that part's zero line, and never if the score would change: ticks whose double
counting was hidden only by the question's ceiling stay as written and flagged. Two
*alternatives* ticked together (point-slope and slope-intercept form) are never read this way.

The grading and arbitration prompts require derivations only where the applicable rubric item requires them. An answer-only
or explicit no-work item can be supported by the quoted answer; evidence requirements do not create a new process requirement.
Operation annotations, cancellations and separate calculations must be distinguished from asserted equations using the image
and retained layout. An unresolved reading is reported, never silently repaired or treated as a proved mathematical error.
These prompt changes are versioned and change the acceptance scope. They do not revise saved grades or automatically rerun
or confirm existing rubric checks; use the ordinary rubric finalization and grading actions for a new run.

## Part caps (2026-10-15)

In a deducting rubric, a lettered part never loses more than it is worth, so a part never takes points another part earned
(contract: GRADING "PART CAPS"). Each part's lines, the ones whose wording begins "Part B:", are one capped group at the part's
points. Example: on a 2-point problem with Part A worth 0.5 and Part B worth 1.5, four −0.5 misses in Part B take 1.5, not 2, and
a correct Part A keeps its 0.5. Lines for the whole question are outside every cap.

Where a part's worth comes from: the printed points, else the rubric writer's own division of the total (it now returns each
part's worth). Accepting a drafted rubric, approving a quiz, adapting a version and replacing a rubric build the caps; a makeup keeps
its original's rules. When the parts' points are unknown or do not add up, the draft and the rubric say the parts are uncapped. The
rubric shows its caps beside its point range ("Part A loses at most 0.5 · Part B at most 1.5"), the rubric check no longer asks
whether a part's deductions can reach another part, and a grade the cap changed notes it ("Part B's deductions stopped at its 1.5
points"). Gradescope cannot cap a part: what the cap gave back goes with the grade's point adjustment, so Gradescope shows the same
score. Rubrics made earlier, and assignments already certified, are unchanged.

## Rubric memories (2026-10-14)

A course keeps general rules and watches that its rubric writers and the rubric check follow (contract: GRADING "RUBRIC
MEMORIES"). After you certify an assignment, one request to the Rubric drafting and review model reads what its rubric checks asked,
what you answered, where the graders doubted a rubric line and where two graders disagreed, and proposes a few memories: a **rule**
says how you settled a kind of case, and only your own answers make one; a **watch** names a kind of case a rubric must settle,
without choosing. Nothing is used until you keep it on the course's **Rubric memories** page (course Home › every term), where each
proposal has Keep and Drop, and a kept memory can be dropped again. A later assignment may propose narrowing, widening or retiring a
kept one; that too waits for you. The page can also ask for a pass over the course's assignments certified before this existed.

What reads them, and how: every rubric writer (a blank template's drafts, every-version drafts, quiz drafts) and every rubric check
(Key & Rubric Finalization, a quiz's draft review, Resolve concerns) is sent the kept list as `standing_rules`. A rule decides its
cases: the writer settles them in the clarification without asking, says in its notes which rules it applied, and the reviewer does
not raise them again. A watch makes the writer settle the case and list its reading for you to confirm. A memory never changes a
line's required wording, and never outranks you on the assignment at hand: your comment on a line, your answer to a concern, or a quiz's instructions or feedback decides the case there, and the rule still stands for other assignments. Courses without memories send exactly the requests they always sent. Memories change no rubric,
confirmation, clarification already confirmed, or grade, and are in no fingerprint.

What the model is shown names no student: roster names and nine-digit numbers are taken out of every sentence, and responses whose
scan was deleted in the Inbox are left out. Its answer is refused, and asked once more with the reason, when a memory is written in
the assignment's own terms (a line number, a version letter, typeset math), is too long, or calls something a rule without one of
your answers behind it.

API: `GET /api/rubric-memories/<definition_id>`; `POST .../decisions` `{decisions: [{memory_id, decision: keep|drop}]}`;
`POST .../learn` `{assignment_ids?}` (the course's certified assignments no pass has read, when none are named).

## Cloud review routing

Cloud (named Cloud Standard until 2026-09-23) is the one cloud scheme for new batches.
In Compute settings select the handwriting reader, primary and second graders,
additional reviewer (permanent final arbitrator), and **Flag reviewer**. Each role
retains its own model and reasoning selection. The two main graders still work
independently with the same reading inputs and optional fallbacks.

Only a disagreement on their rubric selections calls the additional reviewer.
It receives both complete assessments, original inputs and review flags, and must:

1. Adopt either grader's complete selections for finalization.
2. Conclude both are wrong and provide its own complete validated selections.
3. Conclude no reliable grade can be determined and require manual review.

Faculty calculates scores; no assessments are averaged or combined. Its ruling must
settle every shown flag, and the final selections are checked again. Any arbitrator
flag, failed or incomplete ruling, or new problem from checking its final grade
**always goes directly to manual review**, bypassing the fourth model.

The fourth grading model, **Flag reviewer**, handles other flagged responses. It
weighs the flag reasons against evidence for the standing grade. It can uphold that
unchanged grade when every flag is outweighed by sufficient evidence, or confirm
that manual review is needed. It cannot change selections or propose another grade.
Clean agreement calls neither reviewer. Both reviewers are required selections;
conditional calls and their prices are included in the conservative batch estimate.

The review card retains each ruling and its reasons. Automatic exports distinguish
`team_consensus`, `final_arbitrator`, and `flag_review`. Neither reviewer overrides
instructor-confirmed rubric rules, an unscorable selection, or your
own review. Batches of the removed schemes (High Assurance, Final Arbitrator Mode v1, the
Jev tie-breaker, Jev Routing, One-shot and Tag-Team) keep their receipts and results,
readable and final; none of them can be started, resumed or retried.

### Model families decide nothing (2026-09-23)

At the instructor's request every model-family rule is gone for new batches: every new batch, in
every mode, freezes `family_rule: family-blind-v1`. Two graders of one family agreeing is an
agreement (it used to be one vote, never resolved), each assessment counts as a vote
(`risk-rules-v15`), a same-family second grader raises no block, the arbitrator may adopt and the
flag reviewer may uphold a grade its own family produced, and Compute settings, fallbacks and the
model picker accept any family in any role. The family is optional metadata that neither Browse
nor the connection editor asks for; the catalogue records the families it recognizes, and the
benchmark ensemble counts one vote per model. On the trial batch 5b5e4d4e the old reviewer bar
alone held 30 of 240 responses back. Batches frozen before the key finish under the family rules
they ran with.

### Prompt contracts (2026-09-22)

A review of Cloud's prompts against the trial's batch `1c1ce6cb` (240 responses)
changed what each model is asked and given. Every prompt is a new version, so every
acceptance scope is new; nothing retained is rewritten.

- **Primary grader (`primary_vision-v1`, frozen as `primary_rule: primary-vision-v1`).**
  Under `grade-v7`, a contract written for a text-first local grader, the primary raised no
  reading issue and no rubric doubt of its own on any response of three batches, while the
  second grader reported misreadings the arbitrator then sent to the instructor. A batch
  frozen with the rule asks a primary that sees the crops under its own contract: check the
  transcript against the image and report `reading_issues`, exactly as the second grader
  does. Those reach the flags through the team's decision (`committee` review reasons). A
  batch frozen before the rule, a text-only primary, local grading and legacy teams keep
  `grade-v7`.
- **Final answers.** `second_vision-v4`, `transcript_grade-v6` and `arbitrate-v3` carry the
  primary's rule that final answers use the requested `answer_parts` labels, one entry per
  label (the second grader used other labels on 152 of 240 responses).
- **Empty inputs.** Imported questions carry no problem, solution or examples. Every grading
  prompt now says so and where the printed question is (the top of the crop; a text-only
  grader cannot see it and says so in `review_reasons` where credit depends on it).
  `reading_context.independent_readings` counts the readings made independently of the
  transcript: 0 under direct reading means nothing was compared, not that the reading was
  confirmed.
- **The key, for rulings about it.** Graders are still never shown the answer key. The
  arbitrator and the flag reviewer are, beside `answer_checks`: what Faculty compared for
  each assessment (`match`, `mismatch`, or `unknown` when the notation could not be read)
  and how much work it counted, retained on the check step. The flag reviewer had cleared
  22 of 22 key flags without either. The flag itself now says which it is:
  `risk.KEY_DIFFERS` or `risk.KEY_UNREAD` (both still block; `risk-rules-v14`).
- **Flag reviewer (`flag_review-v2`, then `-v3` without the family premise).** Told why it is asked (the graders' agreement is the
  reason, never evidence), how to rule on each kind of flag, and the rubric conventions the
  graders follow; it rules on every flag before it decides (schema order).
- **Reader (`read-v2`, `reconcile-v7`).** The free reading marks struck work as
  `[crossed out: …]` and unreadable marks as `[illegible]`, and returns nothing for a blank
  area. The reconciling reader lists no doubts about marks inside crossed-out work, and
  `models.reading_ambiguities` ignores alternatives on lines crossed out entirely (7 of 62
  in three batches), since crossed-out work alone never sends a response to review.
- **Smaller.** Graders are no longer sent a line's `original_source_text` (it repeated each
  description and kept a corrected line's OLD wording); the arbitrator is shown one
  disagreement flag instead of three restatements; the crop-ID note mentions enhanced
  copies to the reader only and now reaches the arbitrator.

## Answer key and review by answer group

**Answer key and solutions** (Setup, and its own step on the assignment tile) edits every
question at once: accepted final answers (one part per line, `a: 3x2 | 3 x 2`), an
optional worked solution and the problem text. A key is proposed from the rubric's own
full-credit line (`$$…$$` answers joined by *or* are alternatives, by *and* are parts)
and is never used until the instructor saves it. Saving changes only key and solution
text, so the rubric pin, its approval and a confirmed location are kept: the location
is carried to the new question revision by an append-only receipt. Proposals made
before the change become stale, as for any changed solution. When a location is
confirmed for an imported question with no statement, the blank exam's own reading
fills the problem text as grader context.

A matching final answer is **evidence, never a grade**. The coordinator's rubric pays
for the work and often has its own line for a correct answer without it, so:

- The grader (prompt `grade-v5`) quotes what the student presented as the final
  answer from a transcript line: only what the student wrote, so from a printed
  sentence with blanks it quotes each blank, and from `Function: C(x) = …` the
  function. An answer it cannot quote is dropped and reviewed.
- `grading/answers.py` derives facts without any model: whether every key part is
  matched (by conservative normalization, exact rational equality and, last, the
  same value in another form decided symbolically; it never solves the problem and
  uses no tolerance). The check has three values. `match`: every part is answered.
  `mismatch`: the student's answer was **understood** (a number, a plain word, or a
  formula the closed grammar certainly parses) and is another value, or a part is
  unanswered. `unknown`: no key, no answer, or an answer this check cannot read,
  such as a whole sentence or a formula with units written into it. Unknown notation
  never matches **and never accuses**: on the first real papers a third of the
  "mismatches" were correct answers written as `C(x) = $13.5 per item * x + $8541`,
  and each one raised a contradiction. A printed label before the answer
  (`Function:`) and currency signs are not part of the value. whether an independent reader
  shows the same answer, and how many handwritten lines stand beside the answer
  (`none`/`thin`/`shown`; printed problem text is not counted).
- The rubric itself says what is full credit (the one line worth the maximum), which
  line is "correct answer, but no work", and whether work is required (such a line
  exists, or full credit asks for work).
- Contradictions become review reasons: credit for a correct answer without a key
  match; full credit with little visible work where work is required; a key match
  with work shown but no full credit; the no-work line with work visible; a match
  that rests on one reading; and partial-credit lines that add up to full marks
  while the full-credit line was not proposed and the answer does not match the key
  (seen on real papers: "slope calculated with an error" and "slope inverted" ticked
  together; a Gradescope export does not say which lines exclude each other). None
  of them selects or removes a rubric line. A key that answers only some of the
  lettered parts the rubric pays for (a key for part b beside "Part A:" lines) never
  raises "key match but no full credit": the lost points may be part a's.
- A key in words (`not possible`) is matched by an answer that begins with those words
  and goes on to give its reason; an answer reported for part b never answers part a.
  The reader's `\text{...}` around a sentence is layout, not the student's first word.
- An ordered pair or triple is compared component by component, each as any answer is, so
  `(20-4t, 18+9t, t)` matches the key `(-4t+20, 9t+18, t)`, with or without `(x, y, z) =`
  before it or "where t is any real number" after it. It is *another value* only for tuples of
  numbers (`(40000, 260000)`). A parametric answer is never accused: the student may name the
  parameter `z`, or write the same solutions with `x` as the parameter, and comparing components
  cannot tell that from a wrong answer. A pair for a triple, equations instead of the triple,
  words before it and two tuples are not judged either.
- Two readers are compared on values. A sentence, a matrix layout or a value written
  with a word is compared by its numbers only; 6130.10 is 6130.1.

Review → a question → **Grade by answer group** (also under **All responses** → **Review several responses together**) gathers current, unreviewed proposals
that share a final answer, its exact notation, the same amount of visible
work and the same proposed lines. Correct answers with thin work are therefore their
own group, never part of a full-credit pile. A group opens as a contact sheet of every
member's crop with its flags; the instructor may change the rubric lines for the
group, unticks anything that does not belong, confirms having looked at each response,
and every ticked response gets its own ordinary review receipt. Groups name no
student. Nothing is approved without that look, whatever the confidence.

## Bubble sheets: answer key and scores (2026-09-25)

The instructor asked for bubble sheet grading like Gradescope's: click each version's
answers and save them as the answer key. It sits beside the free-response workflow,
not inside it: **Grading → an assignment → Bubble sheets** (`?tab=bubble`, linked
from the assignment's context row and its card). The six-stage graph is unchanged.

- **Page** (2026-09-26). A summary strip (key revision, scored, waiting, not scored, on Gradescope) whose items jump
  to their region; a **Worth a second look** callout for keyed questions where at least five sheets were scored,
  fewer than half earned full points and most chose one other letter (each opens the editor on that version and
  question; the key is never changed for you); the saved key shown compactly with **Edit answer key** (the editor
  opens by itself when there is no key or the draft is unsaved, with Done and Discard); the Gradescope sheets panel;
  then the scores with a name/section search, a Show filter, an order (scan, name, lowest or highest score) and the
  first 50 rows with **Show all N**. Unsaved key drafts survive navigation in the tab, as before.

- **Key.** One tab per version (A, B, ... **+ Add version**, **Remove version** for
  the last one), five letter cells per question. A click makes a letter the answer and
  a second click clears it; Shift-click adds another correct letter. With a letter
  focused, typing a letter answers that question and moves to the next (Backspace
  clears), so a printed key can be typed in. **Question grading defaults** (points
  and Exact / Partial / Either-Or) cover every question; each question's settings
  button overrides them. The grid shows as many questions as the sheets were read
  with (Show all 100 for more). A new key starts with the versions this term's seating
  charts handed out or were declared, else those marked on at least two sheets.
- **Scoring** follows Gradescope's documented rules (`bubble-scoring-v2`), so the two
  can be compared: *Exact* full points only when the letters marked are exactly the
  key's; *Partial* a share for each correct letter, nothing when any other letter is
  marked; *Either/Or* full points for any correct letter, nothing when any other letter
  is marked. A blank earns nothing, except on a question keyed with every letter under
  Either/Or: that question is given to everyone, a blank included (instructor,
  2026-09-25, `bubble-scoring-v2`). Points are Decimal, each question rounded to the
  cent. With one version every sheet takes it, whatever version it bubbled.
- **Which sheets.** Each present scan's latest complete read of this assignment and
  term, once the scan's Inbox review is finished (earlier they are counted as waiting,
  with a link). A sheet is scored from its current value: the latest review, else the
  machine reading. One that still needs review, has no version, or has a version
  without a key is listed with that reason and not scored.
- **Checks on the key.** Each keyed question shows the share of scored sheets that
  earned full points on the SAVED key; below half, with most sheets on another answer,
  it says so ("44% · most C"): often a key to check. A keyed question past the count a
  scan was read with is reported, since marks past that count were ignored.
- **Ownership.** The key belongs to the term grading plan (`frq_bubble_keys`, migration
  024): every save is an immutable revision holding every version, made on a named
  base revision (409 when another window saved first) with a request ID (a lost
  response is recovered by sending the same request). Saving the same key records
  nothing. Scores are derived on every view and never stored, published or emailed;
  readings, reviews, identities and the Inbox gate are untouched. **Download scores
  (CSV)** (name, UIN, section, version, score, per-question points) is the instructor's
  own file; the page itself carries no UIN or private ID.
- **Item analysis** (2026-09-30; instructor: "For each question: how many got it right, whether the top scorers did
  better than the rest, and which wrong choice was popular. This catches a miskeyed question before you certify").
  A closed panel between the key and Gradescope; its first opening reads `GET …/bubble-sheets/analysis`
  (`grading/bubble_analysis.py`, rule `item-analysis-v1`), and it is read again only when the saved key or a scored
  sheet changes. It uses exactly the sheets the page scores and analyses each version apart, since versions may put
  their questions in another order. Per question: the share earning full points and the mean share of its points
  (difficulty); the corrected point-biserial (discrimination: the question's points against the rest of the sheet's
  score); full credit among the top and the bottom 27% of sheets by total (ties by sheet); and the choices (each letter
  alone, blank, several letters; a question past what a sheet was read with counts as blank) as small bars, the key's
  letters in ink, each with the average total of the sheets that chose it. Per version: sheets, mean and SD of the total
  percent, and Cronbach's alpha over the question shares (KR-20 for right/wrong questions). Alpha, discrimination, the
  groups and the flags wait for 10 scored sheets of the version. **Possibly miskeyed** (a callout above the table, each
  question opening the key editor on it): more of the top group chose one other letter alone than earned full points,
  or discrimination below −0.1. **Hard** (difficulty under 0.30), **Easy** (over 0.95) and **Weak** (discrimination 0 to
  0.15) only inform. It changes, decides and approves nothing, and names no student: **Download item analysis (CSV)**
  (`…/analysis.csv`) is one row per version and question with counts and shares only. The page's own payload is
  unchanged. The arithmetic is exact (cents, integers and fractions), rounded half up.
- **Send bubble sheets to Gradescope** (2026-09-25, upload only) is the page's last
  panel. It works like the Certification and Export step's Gradescope panel (below, "Sending grades to
  Gradescope"): a preview per section (the section's Gradescope course, and the
  bubble-sheet exam whose title carries the assignment's number), sections that can be
  left out, a tick and **Send**, then a background upload of up to 24 sheets at once, each
  read back. Every sheet of a reviewed scan that Faculty matched to a student (exact
  UIN, confident match or your review) goes up as that student, found by UIN = SID, to
  the version its bubbles name, or the version recorded in its review: that version
  decides, whatever else says. A sheet without a version, or whose student is in doubt,
  waits for its review; a student who already has a sheet on Gradescope (any version) is
  left alone. Gradescope reads and scores the sheets with its own answer key, so no
  grade is sent; the scores on this page stay your record. The page goes as scanned
  (turned upright when it was read upside down), one page, page 1 of Gradescope's
  sheet.

## Exam versions behind one label sheet

An exam can have several versions (A on white paper, B on yellow) while one
stamps-only label sheet serves all of them, so the printed document version says
nothing about which exam a student wrote. Version B's answers differ on every
question, so every paper is read, grouped and graded against its own version only.

- **The seating chart is the source** (the instructor's rule): decoded label →
  printed copy → student → seat → `seating_exam_versions`. `grading/exam_versions.py`
  resolves it; nothing is copied into grading.
- **The paper confirms it.** The worker compares the whole reviewed paper with each
  version's blank exam in the document sandbox (`grading/version_match.py`, one paper
  per turn). One page is not enough: the cover page is identical in both versions.
  On the instructor's Test 1 scans (325 papers, 12 pages, scanned 200 dpi blanks) a
  paper's own version scored a mean page quality of 0.667–0.910 (median 0.839) and
  the other version 0.458–0.604; the right version led by 0.209–0.31, and the
  comparison agreed with the seating chart on all 325. A version is *matched* with a
  mean of at least 0.55 and a lead of at least 0.12; with only one blank exam so far
  there is no lead, so the mark is 0.635, between the two distributions. Since
  `whole-paper-v2` (2026-09-23) a tilted page is also compared straightened (see
  **Tilted scans**), since `whole-paper-v3` (2026-09-25) every page is also
  compared turned half a turn (see **Sheets fed upside down**), and since
  `whole-paper-v4` (the same day) each blank is also tried at other sizes (see
  **Paper printed at another size**); a paper an earlier
  matcher already decided keeps that decision, and only papers it could not decide
  are compared again, so a new matcher never puts every paper back to *checking*.
- **A paper is gradable only when `confirmed`** (seat and pages agree) **or decided
  by the instructor.** A paper that matches another version (`mismatch`), matches
  none clearly (`unclear`), has no seat (`proposed` by the comparison, or
  `unresolved`), is still being compared (`checking`) or whose version has no blank
  exam yet (`waiting_blank`) is listed in the batch preview with its reason and is
  not graded. Nothing switches a version silently; the instructor's decision
  (Setup → Exam versions → Decide) is an append-only `frq_version_reviews` row for
  that paper's current reviewed assembly, and it never moves a seat.
- **Everything that depends on the exam is per version**: `frq_questions.exam_version`
  (`''` is an exam without versions, graded exactly as before) makes the same printed
  number exist once per version, each with its own blank exam, locations, problem
  text, answer key, rubric and approval. Answer groups, the benchmark
  and the acceptance gate are per question and therefore never mix versions; a
  student's total needs only their own version's questions.
- **Rubric upload asks which version the files describe**, because every version's
  Gradescope files have the same names, and refuses to overwrite another version's
  question. Questions imported before versions existed are told ONCE which version
  they describe (**Say which version these describe**); their revisions, pins and
  approvals stay. One blank-exam choice covers only the questions of its own version.
- The setup guide shows each version's progress and says plainly when a version
  has no rubrics, blank exam or answer key yet.
- **A version no seating chart handed out** (a makeup Version C) is added with
  **Add Version C** in the Assignment versions panel (requirement, 2026-09-25), before
  its rubrics exist, so it has its own blank-template slot and **Draft rubrics for all
  versions** includes it. The addition belongs to the term grading plan
  (`frq_added_versions`, migration 025: append-only, the latest row per letter counts);
  only the next letter can be added, and **Remove Version C** takes it back while it has
  no questions and no draft is running. It only extends the list of versions: its
  papers have no seat, so each is compared with the blank exams and confirmed by the
  instructor (**Confirm proposed versions**) like any other paper without a seat.
  The rubric upload dialog's own **Add Version C** still works for Gradescope CSVs.

## Architecture and ownership

**Faculty owns students and grades. Cloud workers are a disposable execution layer.**
Students, rosters, identity, assignments, rubrics, scans, final grades, review
decisions, audit history, cloud call records and grading policy stay in Faculty.
A provider receives one request (a versioned prompt, a payload and crops) and
returns one answer; submission, recovery, retries, idempotency and cost are
Faculty's own durable records.

The Flask/Waitress app, SQLite catalog, fair Inbox worker and verified storage
managers are reused. Request handlers enqueue expensive work; they never perform
inference. No browser login/cookie gate was added.

| Resource | Owner |
| --- | --- |
| Questions, solution revisions, rubric revisions, original source documents | Course-wide assignment/document version (`course_definitions`, not `courses.id`) |
| Plan, explicit question/rubric pins, upload receipts, batches, exports, preparation jobs | Existing term exam (`exam_events` / `course_terms`) |
| Response and human decisions | Plan/question plus permanent section/enrollment membership |
| Original scans and derived crops | Existing source Inbox owner through `InboxStorage` |
| Compute preferences, provider configuration | Workspace; secrets in server environment only |

Changing a question creates a revision and resets only this plan's rubric pin.
Other terms keep their references. Every inference task snapshots source hashes,
assembly revision, question/solution revision, approved rubric, models and settings.
Sources are revalidated before every component, review and export. Tombstoned Inbox
messages are excluded. Stale approvals remain history but are visibly stale and
cannot be exported as current grades.

The roster lifecycle lock precedes owner locks and catalog writes. Verified owner
reads happen before `BEGIN IMMEDIATE`. Crop bytes use the source Inbox's verified
NVMe staging and nightly archival lifecycle; SQLite contains references/hashes,
not image blobs. Temporary processing uses `tempfile` and service `TMPDIR` on NVMe.
Shared catalog backup retains jobs, results, decisions and immutable provenance.

`grading/assignments.py`, `preparation.py`, `jobs.py`, `review.py` and `service.py`
contain the workflow; `api.py` exposes private `/api/grading` routes. Providers,
extraction, transcript/consensus contracts, rubric rules, decimal scoring and
metrics are separate modules. `static/grading*.js` / `grading.css` use existing
dialogs, same-origin requests, design tokens and immutable asset builds.

Catalog migrations are the `*.sql` files in `grading/migrations/`, applied in file-name order under the
`frq_grading` namespace in one lifecycle transaction (`GradingService.__init__`). Recorded files are immutable
(AGENTS.md CATALOG): never rename or renumber one (two files share the prefix 004, and both are recorded).
The first twelve are summarized here; later ones are described beside the feature they serve (for example
015 batch resets, 020 rubric generations, 029 certifications, 033 flag resolutions):

- `001_frq`: retained identities, revisions, runs, component receipts, reviews,
  exports, settings and events.
- `002_scope_guards`: permanent ownership and immutable input constraints.
- `003_preparation_and_human_inputs`: queued source/compiler work and human input
  snapshots for selective recalculation.
- `004_cloud_crop_approval`: approvals of cloud image manifests recorded before
  2026-09-19 (history only; nothing waits for one any more).
- `005_rejected_outputs`: private receipts for malformed/rejected model output.
- `006_rubric_uploads`: term receipts of rubric-first uploads.
- `007_exam_versions`: `frq_questions.exam_version`, whole-paper version checks and
  the instructor's per-paper version decisions.
- `008_cloud_calls`: job tier/cost/notice, the task lane, durable cloud calls,
  endpoint probes and the canary (removed 2026-09-28; its tables stay as history).
- `009_acceptance`: trial and automatic acceptances, the instructor's confirm/overturn
  reviews of them, retained benchmark reports, permissions with their events, and
  correction feedback.
- `010_scan_workflow`: scan associations and retained intake workflow metadata.
- `011_rubric_quality`: term-owned check options, frozen model-check inputs and
  results, immutable instructor confirmations, and cloud-call ownership. Model
  settings remain workspace preferences; course-wide rubric revisions are unchanged.
  Checks reuse the cloud lane, bounded retry/spending rules and durable receipts;
  settled check results survive a restart without another model submission.
- `012_rubric_quality_cancellation`: a cancellation marker on retained checks,
  preserving the original status constraint and recorded migration history.

## Recognition, scoring and recomputation

The stages are extraction → independent OCR → visual reconciliation → mathematical
interpretation/rubric applications → deterministic decimal scoring. The visual
prompt explicitly forbids solving the problem or repairing the student's answer.
Versioned prompts and output-schema hashes are retained with model identifiers,
reported checkpoint/fingerprint/usage, raw responses and timings. A successful
inference receipt survives even if the source or rubric changes during the call;
it cannot become an applicable proposal in that case.

Registration estimates rotation/translation against the selected blank template.
It retains an exact, unresampled original bounding crop with a small outside-writing
margin, grayscale, contrast, template-difference and separately masked cloud views.
Missing/uncertain registration forces boundary review. This is bounded similarity
registration, not arbitrary page dewarping. Faint marks remain in the original.

**Tilted scans** (requirement, 2026-09-23: some quizzes arrive 10–15° off). The aligner searches only ±3°, so on a
real Test 1 paper tilted in steps, version matching failed from 3° and the answer areas from 3°: at 12° question 19
was cut crooked, missing part of its own question and holding pieces of questions 18 and 20. `grading/deskew.py` now
reads a page's tilt from its own printed lines (the row profile is sharpest when they are horizontal), with no
template: on 60 real exam sides, upright pages read −0.7° to +0.3° and the same sides tilted 12° read 11.7°–12.7°,
at about 0.6 s a page. A page tilted at least 1° with a confident reading (a blank page scores 0, handwriting about
2, printed pages 13 or more; 6 is required) is also tried straightened, and the aligner then searches what is left
in tenths of a degree (0.2° left over had cost a tenth of the score). Both the answer-area cut (`frq-extraction-v2`)
and the version comparison (`whole-paper-v2`) compare the straightened page only where the scanner captured paper,
and use it only when it aligns better than the page as scanned, because long straight strokes can mislead the
reading. On that paper, every tilt from 1° to 15° either way then aligned (quality 0.63, upright 0.59), cut each
answer area the same size at every tilt, and matched Version A (0.71 against 0.72 upright). Pages under 1° take the
old path and keep the scanner's own pixels; a straightened crop is resampled, and its crop step records `deskew`
(angle, confidence, whether it was straightened) and `alignment_quality`. The retained scan never changes. A
straightened sheet cannot bring back what the scanner never saw: a letter sheet tilted 12° is about 10.6" wide, so
a letter-size scan loses its corners, and a label or writing there is gone.

**Sheets fed upside down** (requirement, 2026-09-25). The quiz scan of 652 pages held 21 first pages the first-page
reader read upside down, 17 of them consecutive sheets. Scan intake reads such pages (the label reader, the bubble
sheet detector and the first-page OCR each try either way up) but never turns the retained page, and a half turn is
far outside the tilt search, so such a page matched no blank exam and its answer areas were cut from the wrong end
of the sheet. Every page is now also tried turned half a turn, which is exact (the scanner's own pixels; combined
with straightening when the sheet is also tilted), and the blank exam alone decides: the turned page is used only
when it leads the page as scanned by `deskew.TURN_MARGIN` (0.06). Measured with the crop aligner on the trial's real
scans: 160 Test 1 answer pages (scanned blanks) lost 0.376–0.880 turned over and gained exactly that back when turned
half a turn first; on Quiz 2 (a digital blank) the 21 upside-down first pages and their 21 backs gained 0.148–0.286
and 0.125–0.223, while 40 upright first pages and 30 upright backs lost 0.174–0.360 and 0.041–0.254. The margin is
about half the smallest real lead. No pass mark is required as well: every Quiz 2 page stayed below 0.55 either way
up, because the quiz was printed a few percent smaller than its PDF and the aligner then searched rotation and shift
only. (Under v3 those Quiz 2 crops were therefore flagged for boundary review whichever way up; a blank scanned on
the same scanner avoids it, and the size search below is meant for exactly this paper. These measurements were
taken before it existed; with it, the same pages lead by 0.36 or more turned and trail by 0.41 or more upright.)
The answer-area cut (since `frq-extraction-v3`) records `orientation` (whether it was turned, and both qualities);
the version comparison (since `whole-paper-v3`) decides each page's way up once, against the blank exam it resembles
most, and scores every version that way up (`turned` lists the pages). A page the right way up is cut and scored
exactly as before, and without a blank exam nothing is turned over. Quarter turns are not tried: the instructor says
the department scanner never produces them.

**Paper printed at another size** (requirement, 2026-09-25: add a scale search). Quiz 2 was printed a few percent
smaller than the PDF its blank came from, as "fit to page" printing, a copier or a scanner can do, and the aligners
searched rotation and shift only, so no page of it aligned and its answer areas drifted down the page.
`deskew.search` (`size-search-v1`) now also tries the blank at other sizes, resized about its centre
(`deskew.resized`: what grows past the edge is cut off, what shrinking uncovers is filled with white):

1. The blank at its own size over the usual angles: exactly the earlier search.
2. `deskew.SIZES`, 0.86 to 1.10 of its own size in steps of 0.02, at the angle step 1 found.
3. Only if the best of those beats the own-size result by at least half of `deskew.SIZE_GAIN`: the angles again at that
   size, then `FINER` steps of ±0.005, ±0.01 and ±0.015 around it. Otherwise the search stops after step 2.

Another size is used only when it aligns better than the blank's own size by `SIZE_GAIN` (0.05). Measured on the
trial's real scans: on Test 1, whose blanks were scanned on the same scanner, no other size aligned better at all (80
pages against their own version's blank and 80 against the other's, crop aligner and version matcher alike). On Quiz 2
(102 first pages and backs, either way up) every page aligned best at 0.95–0.96 and gained 0.25–0.49 with its own
version's blank (0.19 or more with another version's), from about 0.32 to about 0.69; 97 of the 102 then passed the
pass mark, none before. The threshold is a quarter of the smallest real gain, so smaller printing differences than Quiz
2's are caught too. A page at its blank's own size costs about a dozen more comparisons and is cut and scored exactly
as under v3. The answer-area cut (`frq-extraction-v4`) scales the answer
areas by the chosen size before placing them on the page and records `template_size`: the `size` used (1 is the
blank's own) and `own_size_quality`, how well the own size aligned. The version comparison (`whole-paper-v4`) scores
each blank at its best size, in the same way. No version threshold changed: Quiz 2's four versions differ only in
their numbers, so the comparison now ranks the right version first on 50 of 51 papers (28 before) but its lead never
reaches `MARGIN`; that quiz's versions come from the version letter read on the first page. A paper an earlier matcher
decided keeps its decision, and crops cut under an earlier version are never reused for a new attempt.
Both native image parsing and template processing use the document sandbox.
A regrade does not cut its crops again (2026-09-23): an earlier attempt at the same response from the very same
source (the same fingerprint: scanned page checksum, answer area, masks, template), cut by the same extraction
version (`extraction.VERSION`), already stored them, and the new attempt references those immutable images
(`reused_from` on its crop step). On the trial every batch had stored all five images again: 33,670 stored crop
images held 5,004 distinct ones. The reader of a cloud batch is sent each crop as scanned and then its contrast
view; before, it received the cloud copy twice. A crop that carries privacy masks still sends only its masked copy.

Consensus tokenizes TeX structure, ignores limited harmless formatting and maps
disagreements to provider geometry only when token offsets match. It preserves
signs, exponents, inequality bars and fraction structure. Agreement is not proof
of correctness. The verifier receives pixels and independent candidates.

Rubric application validation rejects unknown/duplicate items, invalid geometry,
unknown/cyclic error causes, malformed JSON and nonfinite numbers. Unsupported
evidence, uncertain transcription, low confidence, conditional rules and
follow-through conflicts require review. Provider, visual, grading and agreement
signals remain separate; they are not calibrated probabilities.

The scorer applies signed item values, positive/negative mode, group single/multiple
selection, dependencies, exclusions, nonstacking rules, originating-error chains,
adjustments and floors/ceilings using Decimal. Models cannot set numeric totals.

Choose a recomputation stage in Grade (under **Regrading options**):

- **Read handwriting and grade:** new extraction and all dependent inference.
- **Grade again using retained recognition:** reuse image/OCR/verification receipts;
  use the saved human transcript where its source matches.
- **Recalculate points only:** same rubric semantics, revised values/bounds; no
  provider calls or cloud configuration required. A changed question, solution or
  calibration example requires grading again; old human selections must be reviewed
  against that context before rescoring. Retains human selections and
  adjustments when present, and a flag review's ruling with its deductions where the grade stands on one. Meaning changes require a grading run instead.

Recomputation creates a proposal. It never replaces the current human review.
Reviewing a newer proposal is an explicit action, and previous decisions remain.
Changed question/solution or source revisions require review before export.

There are two worker lanes. **The single local GPU stays serial**: the Inbox
execution lock permits one bounded local component per turn (GLM-OCR, the local
verifier and grader), alternating with rubric preparation, template reading and
exam-version comparison, and an interrupted local component fails until retried
explicitly. **Cloud work runs beside it in parallel** (`grading/cloud.py`): a
dispatcher thread started with the Inbox worker, under its own cross-process lock,
runs many cloud components at once, including crop preparation for cloud batches,
Mathpix reads, requests, polling and result handling. `frq_tasks.lane` says which
lane owns a response's next component; catalog writes keep the lifecycle lock. The
dispatcher runs the responses that are **furthest along first**, so finished
proposals arrive from the first minutes of a batch and a cancelled batch leaves whole
responses (stage by stage across 650 papers, nothing could be reviewed for fifty minutes).

A whole test batch runs at once (requirement, 2026-09-23). The dispatcher runs up to `cloud.MAX_UNITS` (256) components
together, set by **Cloud components in flight** (`concurrency`, up to 256, twice that many components) and each
connection's own limit (`max_inflight`, up to 256). A component holds its thread while its model answers and while it
waits for its connection's slot, so these limits, not the batch size, decide how many problems move at once. Cutting
answer areas is CPU work in the document sandbox and is bounded separately (`jobs.CUTTING`, the machine's cores less
two), so a whole batch never starts one sandbox per problem together. Measured on the trial that day, all 240 problems
of a 15-paper run were in flight within two minutes (the reader peaked at 115 calls, MiMo Flash through OpenRouter at
158, with no rate-limit refusals), and 200 were done in 32 minutes against 51 at the earlier limit of 64. What is
left is the longest single chain: MiMo sometimes thinks for 40,000-66,000 tokens on one problem, streaming the whole time.

Cloud calls follow the instructor's retry policy of 2026-09-19. Every request is a
`frq_cloud_calls` row written BEFORE it leaves, and what happens next depends on
whether a failure is **confirmed**:

- *Confirmed failed*: the provider answered with an error for that request (a rate
  limit, no capacity, a server error, a rejected request, a malformed answer),
  reports that exact job failed, cancelled or timed out, or refused the
  connection before anything was sent. It is retried automatically, but never
  before the provider's own status is recorded with the attempt. The ceiling is
  the attempt cap (`max_retries`, default 5 per response and stage); then the
  response fails and waits for the instructor. Rate limits and capacity errors
  wait as `Retry-After` asks, halve that endpoint's requests in flight, and have
  their own tenfold ceiling. A failure no retry can fix (HTTP 401/402/403: a
  rejected key, no credits; an answer cut off by its output budget; an answer the
  gateway ended at its maximum stream duration, about 13 minutes on Vercel) stops at once
  and says so plainly. A grader cut off at the gateway's time limit fails its response, which
  is listed under Failed to grade; the batch's Retry asks that grader again (2026-09-27). An answer that was refused (not JSON, or outside its
  contract) is asked again with a little sampling (temperature .3, recorded on the
  receipt), because at temperature 0 a request that ran away into repetition does so
  again: one real reading failed five times the same way. Every first request is
  deterministic. A model's rectangle without area locates nothing and is dropped
  like `bbox:null` instead of costing the whole answer.
  Mathpix counts requests per minute per account, not connections, so its reads are
  also **paced** (`mathpix_per_minute`, default 800, under the instructor's account
  limit of 1,000 since 2026-09-20): the first parallel batch kept
  eight reads in flight, which is about 240 a minute, met the account's limit of 200,
  and a quarter of its reads were refused before succeeding on a retry. A busy
  answer from Mathpix is recorded by its identifier (never its message) and treated
  like any other capacity failure.
- *Outcome unknown*: the request was sent, then the connection dropped, Faculty
  restarted or the wait timed out. Where the provider gave a durable job ID (the
  batch API, the dedicated provider) it is recovered through that ID and nothing is
  resent. Otherwise it is resent once its own timeout has passed, **at most twice**
  (`unknown_resends`); an occasional double charge is accepted.
  A hosted model request (OpenRouter, Vercel AI Gateway, Nebius) has **no timeout**
  (requirement, 2026-09-23). MiMo V2.6 Pro, arbitrating at high reasoning, wrote
  35–45 tokens a second under load and its rulings ran to 9,000 tokens, so every
  ruling past about 7,000 tokens timed out at the workspace's 180 seconds, and each
  resend did the same. Without the timeout, Vercel's AI Gateway then ended every
  non-streamed request at about 340 seconds (30 of 74 arbitrations of one batch), so
  every hosted answer is now **streamed** and reassembled into the ordinary answer;
  streamed requests run to about 790 seconds on Vercel. Parsing, receipts, costs and
  scopes are unchanged. A stream that ends before its finish is an unknown outcome,
  an error sent inside the stream a confirmed failure. TCP keepalive still turns a
  dead connection into an unknown outcome after about two minutes of silence. The workspace request timeout still applies to Mathpix,
  dedicated GPUs and local models, and is how long a restart waits before resending.
- *Still unknown*: the response fails, and **Retry failed responses** retries every
  such response at once.

Results are idempotent per response and attempt (`frq_steps UNIQUE(task, stage,
attempt)`): the first valid result of a component is applied, a later result of a
superseded request is kept on its call as a receipt and never applied, and no
response can end with two grades. Batch request IDs are idempotent. Successful
components survive partial failures. Cancellation stops unsent work, cancels
durable provider jobs where the provider permits, and **closes every request in
flight** (requirement, 2026-09-23: "I don't want a model running endlessly after I
thought I cancelled it"). The dispatcher checks about once a second for a cancelled
batch, rubric review or rubric draft and shuts the connection of each of its open
requests, and a request still waiting for its connection's slot never leaves. The
call is recorded as `cancelled` (sent or not), its problem ends as cancelled rather
than failed, and nothing is resent. Closing a stream stops the model and its billing
at a host that supports cancellation (OpenRouter lists which); another may still bill
what it produced before the connection closed.

## Reading, independent verification, symbolic checks, risk and escalation

The stages are unchanged (`extracting` → `recognizing` → `reconciling` → `grading` →
`scoring`); their prompts, schemas and receipts were extended, each under a new version.

**Literal reconstruction (`reconcile-v7`).** The reader receives crops and OCR
candidates and nothing else: never the solution, the expected answer, the rubric or
another model's grade, because a reader that knows the expected answer drifts toward
it. Every transcript element may carry a `state`: `crossed_out`, `inserted` or
`unclear` (ordinary work carries none, so earlier readings are unchanged). Crossed-out
work is transcribed as it reads and never deleted; a line the student struck out is
skipped by the symbolic checks and can never be the final answer: an answer quoted
from it is dropped with a review reason. Crossed-out work alone never triggers extra
grading, manual review or an acceptance block. `ambiguities` lists only marks the reader
cannot read with certainty, and a transcription holds only what the student wrote,
never a description of the page: in the first live run (`reconcile-v2`) the reader
filled `ambiguities` with confirmations ("the OCR has this correct"), which the
policy rightly counted as doubt, and wrote "with 'fixed cost' written below the
constant term" into a final answer, which then matched no key.
Since `reconcile-v6` the reader is told to keep the labels a student writes around a
matrix or table (the name before it, column headings as a row above, each row label before
its row, written dimensions, the brackets) and handwriting added to printed material:
on one batch 16 flagged responses had such writing left out of the transcript, where the
rubric pays for exactly those labels.

**Interpretation and rubric application (`grade-v5`).** The primary grader first
interprets the mathematics independently of the rubric (method, valid and invalid
transformations, contradictions, `work_consistent` yes/no/unclear, distinct
`attempts`), then decides each rubric item separately, with quoted evidence, a
confidence and an `ambiguity`. It never returns points: the deterministic Decimal
scorer computes them, with groups, dependencies, follow-through, fractions and
ceilings as before. `review_reasons` holds only specific problems a person must
look at; `grade-v3` let the model write confirmations there, and each one escalated
its response. Prompts are versioned files; every earlier version stays for its receipts.

**Historical conditional grading teams (frozen batches only).** New batches also freeze
`cloud_standard_rule: cloud-standard-v2` and use [the replacement routing](#cloud-review-routing).
The following describes batches without that rule, which freeze `grading-teams-v1` in their
profile. `grading/committee.py` records bounded components within the existing
`grading` stage; every model receipt remains immutable. High Assurance and its second
round were removed on 2026-09-23: the bullets about them describe receipts of batches
that ran, which read as they ran and cannot be resumed.

Both modes start with the **Standard** reader and team. The reader receives no
rubric or solution. Primary and secondary independently receive the same original
grading inputs and retained OCR/layout context; only image-capable models receive
crops. Neither sees another grader's decisions. Agreement means the complete map of
rubric selections, including zero-point items and differences hidden by a ceiling.

- **When the additional reviewer is bought** (requirement, 2026-09-22, `third-vote-v3`):
  only where the two graders **disagree**, in Cloud and in each (historical) High Assurance
  round. Nothing else buys a model call. A pair that never compared — the second is
  missing, failed or unscorable — and an agreement inside one model family are not
  disagreements: the response is flagged and waits for you, and its decision says which of
  the two it was. Unclear rubric wording, an ambiguous reading, a reading problem the second
  grader reports, uncertain alignment, reported confidence, a grader's own request for a
  look and evidence contradicting the proposal are not votes either: they still flag the
  response and block automatic acceptance, and stay on the record (`noted` on the check
  step), but buy nothing. In the batch measured on 2026-09-20, 54 of 130 reviewer calls were
  for pairs that agreed on every line, and 33 of the 34 that finished stayed flagged for
  exactly those doubts. `third-vote-v2` had already dropped those; v3 drops the rest.
  A pair that both give the question maximum still buys nothing, because a third cannot
  change that number, and the reviewer still settles by matching one side on every
  credit-bearing line. Each batch freezes its rule: one submitted under `third-vote-v1` or
  `third-vote-v2` finishes exactly as it ran, and its receipts read as they ran.
- **Cloud (Standard team):** when the pair agrees, stop. A disagreeing pair invokes
  the Standard additional reviewer (`adjudicate-v2`), with
  the original inputs and both complete validated assessments. It returns its own
  complete grade. Matching either earlier assessment resolves the tie; matching
  neither requires individual review. Cloud never escalates.
- **Cloud High Assurance** (removed 2026-09-23): started with the same Standard pair. Its conditional
  Standard reviewer instead uses `third_grade-v1`, without either earlier decision.
  A matching assessment resolves the tie. A third distinct assessment or unresolved
  check/request for review escalates once, if enabled, to the High Assurance team.
- **High Assurance second round** (removed 2026-09-23): its primary and secondary independently grade
  the original evidence without Standard decisions. Its additional reviewer runs
  only where that round's two graders disagree, and is also blind to previous decisions.
  A matching assessment resolves the tie; a different one or remaining check needs
  individual review. There is no third round. The Standard handwriting reading is
  reused. Model selections are not changed automatically.

The selected proposal is always one model's complete validated assessment, with
server-calculated points. No averaging or per-item majority creates a new grade.
Explicit reading problems and review requests remain actionable even if selections
match. Missing/failed/unscorable required opinions never count as agreement. Within
one team, matching opinions must come from different model families.

Receipts use `grade:primary`, `grade:second`, `grade:check`, the conditional
`grade:verifier`, and `grade:decision`, followed by equivalent `grade:ha:*`
components only when needed, then `grading`. `signals.panels` retains every opinion
and round outcome; `signals.committee` identifies the selected assessment and its
matching supporter. A model failure retains its receipts and remains reviewable.

Jobs queued before this workflow keep their frozen legacy path, including the
old objection-only reviewer; historical grades and instructor reviews are not
rewritten. New regrades use the current workflow and retain earlier results.

**Symbolic checks (`grading/symbolic.py`).** Deterministic, SymPy-backed evidence
about what was WRITTEN. Transcript text is parsed by a small closed grammar (numbers,
single-letter variables, `+ - * / ^`, parentheses, implicit multiplication, a few
named functions) and never by `eval` or SymPy's string parser; whatever does not
parse with certainty is `indeterminate`. Nothing is solved for the student and no
line is repaired.

- *Equivalent forms in the answer-key matcher* (delivered first):
  `21.5(x-3270)+79134` and `21.5x+8829` are one answer, which one real rubric lists as
  alternatives. Spelling and exact rationals are tried first; decimals are exact
  (`21.5` is `43/2`) and there is no tolerance (`0.33` is not a third). The same
  value in a different notation still forms its own answer group, because whether
  notation earns credit is the rubric's business.
- *The written work*: `arithmetic` (`18/3 = 8`), `equation_chain` (two consecutive
  one-variable equations with different solutions) and `final_value` (the last
  `x = …` does not satisfy the first equation). For `3x - 8 = 10`, `3x = 18`, `x = 8`
  it reports that `3x = 18` gives `x = 6` but the next line gives `x = 8`, and that
  with `x = 8` the first equation reads `16 = 10`. Keeping one root or gaining one
  is ordinary algebra, not a contradiction.
- Every check is retained as `{type, input, result: pass|fail|indeterminate,
  explanation}`. Mathematical validity is not rubric credit: these rubrics pay for
  follow-through, notation and set-up, so a failed check is a signal, never a decision.

**Confidence checks have a switch** (requirement, 2026-09-20): Compute settings → What holds a response back → Confidence thresholds →
**Use model-reported confidence to flag responses**, on by default. Off, a confidence NUMBER a model reports about itself (the
reader's, the OCR provider's whole-result or per-symbol figure, a grader's overall or per-line figure, the second grader's)
sends nothing to review or extra checking and keeps nothing from acceptance, and a clear, consistent, registered reading settles
OCR differences without waiting for one. What a model NAMES is not a confidence number and still counts: an ambiguity, an
unclear mark, a review reason, a rubric ambiguity; so do reader agreement, grader disagreement and every contradiction. The
numbers are still recorded with each response and the threshold values are kept (shown read-only while off). The switch lives
in the risk policy (`risk.confidence_checks`), so switching it off is part of the acceptance scope; while it is on, scopes are
exactly what they were, and a batch frozen before the switch existed keeps its checks.

**Risk (`grading/risk.py`, policy `risk-rules-v15`; review rules `review-rules-v2`).** Confidence is not calibrated, so
risk is a deterministic rule-based policy over independent evidence: reader
agreement, reader confidence, the key match, visible work, the second grader (and
whether it is of another lineage: models of one lineage are one vote), the
reviewer's objections, symbolic results, alignment, unclear marks,
malformed answers and rubric structure. A model's own confidence is one weak input
and never the gate. The crossed-out count is retained for context only.
**Every component signal is persisted** with the result
(`signals.risk.components`), not only the verdict, so a calibration model trained on
the instructor's adjudications can replace the rules later. The verdict has a
`level` (orders review), `escalate` (where more compute is worth spending),
`blocks` (why a proposal may not be accepted unseen) and `individual`.

**Require a confirmed answer-key match for full credit** applies only when the
deterministic proposed total reaches the question maximum, including totals made
from several rubric items. A proposal below full credit is not blocked by this
requirement when its answer differs, cannot be matched, or has no confirmed key.
**Require supporting work for full credit** uses the same full-credit boundary and
applies only where the rubric requires work. Missing or insufficient work alone
does not block a proposal below full credit, including the rubric's no-work credit.
Other checks, including mathematical inconsistencies and contradictory rubric
selections, continue to apply independently. Existing decisions are not rewritten.

For historical batches without `cloud_standard_rule`, any disagreement in rubric selections requests the conditional third assessment,
even when totals match. A valid recorded team resolution clears the disagreement
flag; unresolved differences require individual review. The original opinions
remain in the audit. Older proposals without a recorded resolution stay flagged
and excluded from grouped approval without changing their scores or human reviews.
The workflow, its teams, prompts, thresholds and escalation
setting are part of the acceptance scope, so old permissions do not authorize a
changed workflow.

Two rules come from the first live run, where 4 of 4 real responses escalated for
reasons true of EVERY response (`risk-rules-v1`). A reader's doubt is its
**per-symbol rate** where it reports one (Mathpix `confidence_rate`, threshold
`min_ocr_rate`, provisionally .93, the lowest tenth of 78 real responses): Mathpix's `confidence` is the chance that a
whole handwritten block is perfect, which was below the old .85 threshold for 53
of 78 real responses, while five of the six readings the vision reader itself called
ambiguous sat in the low tail of the rate (median .993). Calibrate it from corrected
transcripts once they exist. A reader that reports no rate keeps the whole-result rule
(`ocr_threshold`). A rubric condition still needing instructor judgment blocks
acceptance and identifies the applicable item, but is never a reason to
spend more compute on one response. The same holds for wording the grader finds
unclear for one response (`rubric_ambiguities`: "the rubric asks for 43/2 and the
student wrote 21.5"): another model cannot settle what the rubric leaves open, so
it blocks acceptance and is shown, and nothing is escalated for it.

Whether a rubric HAS conditional rules is decided by the current wording rule, not by
when the rubric was imported (`rubrics.conditional`). The importer marks a line by
copying its description into `exceptions`; until the evening of 2026-09-19 it also
marked descriptive wording ("Correct answer, **but** no work to justify answer"), and
rubric revisions are immutable. On the first real batch all 321 responses of one exam
version carried the sentence and none could be trial-accepted, while the identical
rubric lines of the other version, imported a few hours later, were clean. The
importer's own mark therefore counts only where today's rule still finds the wording;
a condition someone wrote (any other text in `exceptions`) is always evaluated.
`grading/review_rules.py` evaluates each condition against this response's selections.
A narrowly recognized standalone "only if" restriction on an explicitly unselected
item is inactive; exceptions, caps, "unless" and unrecognized wording still need
judgment, even for unselected items. Selected conditions require review unless the
instructor has fully expressed them as dependencies, exclusions, non-stacking or
group rules and explicitly marked `condition_handling: structured`. The checkbox
in **Edit interpretation → Dependencies and conditions** creates a new course-wide
rubric revision requiring the ordinary approval. Model compilation cannot set it.
The deterministic scorer still refuses invalid combinations.

OCR differences also have separate original and unresolved counts. A clear visual
transcript can settle a difference only when its full normalized text matches a
complete OCR candidate involved in that difference, its elements agree with that
text, its crops are registered and it passes the existing visual-confidence rule.
Explicit instructor corrections can settle differences too. Ambiguity, an unclear
element, low confidence, incomplete geometry or a novel mixed reading keep the
review flag. All original candidates and disagreements remain in the audit, and
original OCR agreement remains an automatic-acceptance check; resolving a flag
does not manufacture independent agreement or approve a grade.

These rules apply to new results. **Recalculate points only** refreshes the flags
on retained readings without a model call, keeping prior results and instructor
reviews. No existing grades are silently rewritten. Risk/review policy versions
are part of the acceptance scope, so previous permissions do not cover changed rules.

**Review measurements.** In Review → Regrading and diagnostics → **Compare with
approved work**, the report counts current instructor-reviewed responses once,
with changed/unchanged decisions by review signal and model-reported confidence
range. It separates question/rubric revisions, frozen model profiles, policy
versions and independently reviewed versus visible/unconfirmed samples. Corrections
include text, selections, points and explicitly marked reasoning errors. Categories
overlap; small or selected samples and unchanged approvals cannot establish accuracy
or unnecessary flags. The report does not calibrate confidence or change thresholds.
Only blind reviews of trial acceptances support the acceptance
gate; revealing a proposal first excludes it. All measurements reuse existing
term-owned response/review histories; there is no new owner or schema migration.

Evidence is checked the same way everywhere: a quotation must be found in the named
transcript line, as text or in its LaTeX spelling, ignoring only whitespace, wrapping
`$` signs and an escaped currency sign; one differing character is not a quotation.
What the reader added to lay a block out (math delimiters, array and `\text{}` wrappers,
grouping braces) and the spelling of a mark (× or `\times`; `\frac{-8z}{-8}` or `-8z/-8`,
while `27/(2x)` is never `(27/2)x`) are not the student's characters. A sentence the student
wrote over several handwritten lines may be quoted as the one sentence it is: it must start
in the named line and continue character for character through the next few lines (a line
of side work between them is passed over). A grader may also name a neighbouring line, in
three shapes and no others: the same run-on read backward (the quotation starts up to four
lines earlier and takes the named line, such as the last line of a wrapped sentence), a printed
label (`Function:` is named and the writing is in the line that follows) and the line directly
after or before. The quotation is kept under the line where it was found, and a crossed-out
line still refuses a final answer. Rows a grader joined its own way (`;`, `...`, a typed `->`,
a spelled `\n`) are read as rows only after the quotation failed as written, and every row must
still be in the line. A paraphrase, a corrected value, a comma-composed pair, a letter for an
operator, text elsewhere in the transcript and writing the reader never retained are still refused.
A refused quotation on a line the grader did not tick flags nothing, because that line moves
no score; it is dropped and counted.
In a positive rubric a line worth nothing ("No answer or completely incorrect")
needs no quotation, because nothing was earned and there is nothing to quote; every
line that moves the score still needs its evidence, and a deduction rubric keeps the
rule for every line. On the first real batch these two rules were behind a third of
all "evidence incomplete" alarms.

What matters is evidence that **contradicts the proposal**. Partial credit for work
with an arithmetic slip is ordinary grading: the failed check is recorded, the
response is never accepted unseen, and nothing is escalated or flagged. Full credit
for work whose own lines contradict each other is flagged, escalated and reviewed
individually, even when every model agreed: agreement among models that share an
input cannot detect an error in that input.

**Escalation (removed 2026-09-23).** High Assurance batches could proceed once from the
Standard team to the High Assurance team after the conditional third opinion left a
disagreement or another check unresolved. That mode and its escalation were removed;
the receipts, events and results of batches that escalated stay as they ran, and Cloud
never escalates.

**Review.** Review by answer group stays the primary surface, and the new signals
feed it rather than replace it: groups are ordered largest first (nothing-flagged
first among equals), a group never mixes proposals an independent grader agreed
with and proposals nobody checked, and responses where independent evidence
disagrees with the proposal itself (graders disagree, mathematics contradicts full
credit, lines that cannot be combined, or a routed objection) are reviewed one by
one and counted as `individual`. The individual review leads with **Independent
checks**: the risk level, what the second grader disagrees on (also shown on
each rubric line), failed symbolic checks in words, the reviewer's objections, reader
disagreement, the answer facts, why the response was escalated, why it would not be
accepted unseen, and which endpoint and lineage served each role. **Regrade with
Cloud** and **Read this response again** send one response as a batch of one; a
new proposal never replaces a saved decision.

## What holds a response back

Under **Compute settings → What holds a response back** (the review policy leads that tab), two choices:

- **Every check** (the default, and what Faculty always did). Any failed check keeps the response for you: reading problems,
  incomplete evidence, key mismatches, ambiguous handwriting, a grader asking for a look, and the rest.
- **Disagreement, and full credit on little work.** Only two things keep a response for you: the grading team did not agree,
  and full credit was proposed where the rubric requires work and little is visible.

Every other check still runs, is recorded and is shown on the review card under the AI's checks; it simply no longer blocks
automatic finalization. Two things are never waived, because they are not opinions: a set of ticks that cannot be scored under
the approved rules, and your own confirmed rubric finalization.

This came from reading a real batch by hand (235 responses, one exam). Of its 121 flagged responses, 87 would be waived by the
narrow policy. Reading all 13 papers where an independent run had disagreed found **one genuinely wrong grade** (3 points: full
credit for a transposed matrix) and about 4 further points of debatable generosity, against 87 fewer reviews. Both papers read
as clearly over-awarded carried the "full credit on little work" flag, which fired on just 3 of the 235 — which is why that one
check is kept. Team disagreement was the strongest signal by far: 60% of those responses had a score another team disputed,
against 8% of the unflagged ones.

The choice is part of the acceptance scope, so permissions earned under the old policy do not silently carry over, and a batch
that ran before you changed it keeps the behaviour it had.

## Flag review (2026-09-28)

**Review → Flag review** hands the held grades of the whole assignment to the Rubric drafting and review model, question by question
and version by version, 25 responses per request with their crops (`grading/flag_resolution.py`, migration 033). You asked for it
after a batch held 137 of 1,232 responses: the same rubric doubts, grader disagreements and "asked for review" flags recurred across
a question, and one reviewer looking at them together can decide them the same way.

- **What it may do.** For each response it answers every flag, then upholds an earlier grader's complete assessment, re-ticks it with
  its own complete assessment (every line decided, verbatim evidence), or leaves it with you; a flag it leaves standing keeps the
  response with you. It is told the rulings already made on the same question, so the same work gets the same decision. Since
  2026-09-29 it has its own prompt (`flag_resolution-v2`), not the arbitrator's: v1 left 48 of 108 and then 43 of 50 held grades with
  you, most because the crop showed what the transcript lacked. v2 leaves a grade with you only when the evidence itself is missing
  (illegible marks, work cut off even on the page, or a clarification of yours that says so), and then names what to look at and
  which grade each reading gives.
- **The spot to look at.** When it leaves a response with you with reading choices, the same model is asked once more, for that
  response alone, where the marks are; Review outlines them on the crop (and in the crop's full-size view) and shows each spot
  enlarged beside the reading choices, named, so the handwriting sits next to the buttons (click the enlarged view to jump to its
  outline on the scan). It is an outline of where to look, never a reading:
  it changes no grade, and a look that fails leaves the note in words alone. Measured 2026-11-14 on 177 saved notes: by eye on 42,
  on the marks in 36, on the neighbouring mark in 5, wrong in 1 (`grading/flag_look.py`, prompt `flag_look-v1`).
- **Transcript corrections.** When the crop clearly shows writing that decides credit and the reader misread or left it out (a digit,
  a symbol, a circled printed choice), the flag review corrects or adds that transcript line and quotes it. The line is marked
  "corrected by the flag review" or "added by the flag review" on the review card, with what it saw and what the reader had read; the
  earlier result keeps the reader's transcript.
- **Whole pages.** When writing that may decide credit runs past the crop's edge, it asks to see the page, and Faculty sends that
  response once more with the scanned page, the question's area outlined in red. A page can show the student's name when the
  question is on the first page.
- **What it may settle.** Every flag but your own: rubric doubts and unclear wording (which no other model may settle), rubric
  conditions, grader disagreements, "asked for review", handwriting and reading doubts, alignment warnings. It never settles a
  possibly blank answer, a grade with no line ticked, a pair of graders that never compared, an unscorable selection or your rubric
  finalization: responses held by any of those are not sent (the panel says how many and why).
- **How a ruling becomes a grade.** It finalizes nothing by itself. Faculty queues an ordinary "Recalculate points" batch for the
  responses it settled; that batch takes the ruling's grade, scores it, runs every check again and finalizes it only if every flag
  that remains was shown to the model and resolved. Finalized grades say **flag review** as their basis and stay editable in the
  ordinary review.
- **Clarifications.** Where it had to decide how a rubric line applies, it proposes a clarification. The panel lists them with a
  Copy button; nothing changes until you add one in Key & Rubric Finalization, which then grades that question again.
- The model reads refs (R1, R2 …), never a student, a section or which model graded. One flag review runs per assignment at a time;
  **Stop** sends nothing further and applies nothing. Your rubric reviewer is currently the same model as your primary grader, so on
  the primary's own doubts it is not an independent opinion.

## Trial acceptance, the gate and real automatic acceptance

Four states are never conflated, anywhere they are shown, counted or exported:

| State | What it is | Final? | Exported? |
| --- | --- | --- | --- |
| AI proposed score | a result, grouped for your one look | no | no |
| AI **trial-accepted** score | the risk policy passed it, as a TEST of the policy | **never** | **never** |
| AI automatically accepted score | final after team agreement and checks, or a legacy benchmark permission | yes | yes, as `automatic` / "accepted automatically" |
| instructor-confirmed score | an ordinary review | yes | yes, as always |

**New cloud-team finalization (instructor policy, 2026-09-20).** A resolved team
assessment with every check passing becomes final automatically, without a
benchmark prerequisite. It leaves the waiting review queue, remains inspectable
and editable, and exports with status `automatic` and basis `team_consensus`.
`frq_team_acceptances` retains the exact result/scope/policy; no report is fabricated.
These records and their correction/suspension events belong to the existing term
response. Shared read-only views combine them with legacy acceptance receipts.
Human decisions (including drafts) always take precedence. A changed rubric or finalization
suspends the unreviewed automatic grades of the questions it changed.
Automatic grades are never training/evaluation labels. Historical proposals are
not finalized retrospectively; explicitly regrade to use the new workflow.

The following trial and benchmark controls remain for **local and legacy jobs**.
They do not gate the new cloud-team workflow.

**Trial acceptance needs no gate** and is on by default (Compute settings → What holds a response back → Local and older workflows → Trial acceptance).
The risk policy runs for real and accepts only what passes every rule: a current
proposal, no review reason, a match with a confirmed key for
full-credit proposals when that requirement is enabled, sufficient visible work
for full-credit proposals where the rubric and enabled work requirement call for it,
agreeing readers, an agreeing second
grader of another lineage, no material objection, no symbolic contradiction, and
no instructor decision already on that response. It prefers low coverage with an
extremely low error rate over high coverage.

**Your review of trial-accepted responses is the measurement.** Each one you look
at records *confirmed* (you approved exactly the proposed lines) or *overturned*
(anything else; your corrected lines are in the ordinary review, and the AI's
output stays beside them). That record is the benchmark report the gate asks for,
so testing and the gate are one process. Review → **Trial acceptance** shows, per
question and scope: how many were accepted, how many await your look, the running
error rate, its upper confidence bound, how many more reviews would reach your
limit (so you can stop early), and **every overturned response with the signals
that came closest to catching it**, because those are the policy's bugs.

- Trial-accepted responses come through **Grade by answer group**, largest first,
  marked *Trial-accepted · your look is the test*, with what the policy relied on.
- Someone who sees a grade before judging tends to agree with it. A random share
  (**Blind share**, default one in five) is **blind**: the AI's decision stays
  hidden until you have graded; they are never on a contact
  sheet, and **Review the blind responses first** opens them. Revealing the
  proposal first makes that review count as *seen*. The report shows the blind
  share and the rest separately, and **the gate is computed from the blind share**.

**Real automatic acceptance is not a setting.** It is off, and impossible to turn on
without evidence: Review → Trial acceptance → **Turn on automatic acceptance for
this scope** works only when the retained report for that exact scope (question
revision, rubric, exam version, prompts, models, tier, risk rules and thresholds)
puts the **upper 95% confidence bound** of the error rate among accepted responses
at or below your limit (default 1%). The observed rate alone is not enough: zero
errors in 30 responses still allows a true rate near 10%, and zero in 300 allows
about 1% (roughly 3/n), so the refusal says both numbers and how many more
error-free blind reviews would reach the limit. Enabling retains the report
(`frq_benchmark_reports`) with the permission that rests on it.

- Changing the rubric, question, prompts, models or policy is another scope: the
  permission simply no longer applies until a new report exists.
- One accepted response in twenty (**Audit share**) always comes to you as an audit.
  An overturned audit **suspends** acceptance for that question at once. Unreviewed scores of
  a suspended permission are withheld from export until you have looked.
- A response you already decided is accepted over only by a regrade you started (a batch or
  **Grade again**) whose new grade passes every check (requirement, 2026-09-27; rule
  `regrade-supersedes-v1`). Your decision stays in the response's history and an event records
  which grade replaced it; a flagged regrade waits for you beside any decision that still counts,
  and **Recalculate points only** never replaces a decision.

## Learning from corrections

Every instructor decision is kept **beside** the AI's output, never instead of it:
the proposal, its component risk signals, both graders' opinions, objections, OCR
readings, models, prompt versions and scope stay in the result and its receipts;
the review holds the corrected transcript, rubric lines and score; and
`frq_review_feedback` adds what the correction teaches (rubric lines added and
removed, whether the reading was corrected, the override reason, **The model's
reasoning was wrong**). The **Model comparison set** (Certification and Export → Saved for training → What the files hold; the export API's `dataset`) carries
all of it per response (`provenance`), an opaque `student` key, the exam version
and whether the policy would have accepted it; automatically accepted responses
are left out, because nobody looked at them and they must never become truth.

The earlier "no automatic training" rule is lifted. `manage_grading.py
finetune-export` turns the dataset into supervised examples for one
**student-level split** (60/20/20 by a hash of the student key, so one student's
handwriting never leaks from training into the test): target `reader` (crops → your
verified transcript) or `grader` (crops, transcript, rubric → your rubric
decisions). Two targets are worth having: an 8B model that can serve the local
pipeline after tuning, and the 32B default primary grader (a QLoRA fits one 80 GB
GPU). Evaluate every fine-tune on the held-out `test` split before it grades
anything, configure it as an endpoint with **the lineage of its base model**, and
let the benchmark decide. Training itself runs on the dedicated GPU provider, which
is deferred (see below); nothing in Faculty trains by itself today.

### Every grade is saved for training

Since 2026-09-26 every grading decision is saved as a training example the moment
the worker is free (within seconds), and every decision made before then was saved
on its first run: your reviews (approved, manual, unreadable or rejected; never a
draft) and the automatic finalizations. An example keeps the answer crops (as
references to the scan's verified images), the readings, the AI's transcript, rubric
lines, reasons and score, the final ones, your correction feedback, and the models and
prompts behind the proposal, under the question and rubric revision it was graded
with. Examples never change: when you decide a response again under the same rubric
the download marks the earlier decision `superseded_by`, and a grade that goes stale
after a rubric edit stays an example of its own rubric, although the grade exports
leave it out.

**Certification and Export → Saved for training** shows the counts and downloads one assignment or all
of them. The zip holds `dataset.json` (`faculty-frq-training-v1`) and `images/` (each
original crop once, named by checksum). Students are an opaque key of that download,
with the same train/calibration/test split as every other export. Automatic grades
are labelled `automatic`: benchmarks never measure against them, and
`finetune-export` uses them only with `--labels all` (training on them teaches a model
to agree with the grading models, not with you). The rubric finalization's
clarifications are internal and not included.

```sh
.venv/bin/python manage_grading.py finetune-export /mnt/nvme/path/grading-training-data.zip --output /mnt/nvme/path/grader-train.jsonl
.venv/bin/python manage_grading.py finetune-export /mnt/nvme/path/grading-training-data.zip --target reader --labels all --output /mnt/nvme/path/reader-train.jsonl
```

## Benchmarking: measure before building the committee

No response had been graded when this was built, so no threshold here is
calibrated. The **Phase 0 loop** is the way to get there, and costs a few dollars:

1. Grade one short-answer and one multi-step question with the current pipeline,
   and grade a sample of each yourself. Blind reviews of trial acceptances are the
   independent labels.
2. Certification and Export → Saved for training → What the files hold → **Model comparison set**.
3. `manage_grading.py benchmark DATASET`: every retained profile against your
   approved work, with the **error breakdown**: misread handwriting, wrong rubric
   line, ambiguous rubric, alignment, malformed output, and what that says about
   where independence would pay (another reader, a second grader of another
   lineage, or rubric structure).
4. `manage_grading.py run-endpoint DATASET --endpoint NAME --output FILE`: the
   SAME responses through one hosted endpoint, **one variable at a time**: the
   model (`qwen3-vl-235b`, `qwen3-vl-235b-thinking`, `gpt-oss-120b` with `--role
   second`, `kimi-k2.5`, `yardstick`), or the transcript it is given
   (`--transcript proposal` end to end, `--transcript human` grading alone).
5. `manage_grading.py benchmark DATASET --predictions a.json b.json --baseline
   qwen3-vl-32b`: every comparison against the default primary grader **on the
   same responses, with an exact McNemar test and interval** (failed or unscorable
   predictions count as wrong; the baseline shares the role and transcript source;
   `--split test` keeps to held-out students), and a verdict: better beyond noise,
   worse beyond noise, or *within noise: keep the default*, which is cheaper,
   self-hostable and fine-tunable.

```bash
set -a; . ~/.config/faculty/grading.env; set +a   # the key variables the endpoints name
.venv/bin/python manage_grading.py run-endpoint /mnt/nvme/path/dataset.zip --endpoint qwen3-vl-235b --output /mnt/nvme/path/235b.json
.venv/bin/python manage_grading.py benchmark /mnt/nvme/path/dataset.zip --predictions /mnt/nvme/path/235b.json --output /mnt/nvme/path/report.json
```

Per profile it reports exact score agreement (with its interval), mean absolute
score error, errors of a point or more, rubric-item precision and recall,
automatic-accept coverage, **the error rate among accepted responses with its
upper 95% bound** (the number that matters for acceptance, not average accuracy),
the human-review rate, the OCR disagreement rate, median latency and estimated
cost. `benchmark.ensemble` counts one vote per model. Review → **Compare with
approved work** adds the instructor's burden, which is the goal: answer groups
per question and responses per group, the share approved by group, individually
and automatically, instructor actions and minutes per 100 responses (from the
times of your decisions, a gap beyond five minutes counting as a break), group
purity (members removed, groups regraded) and false alarms (flagged responses you
approved unchanged; this is also the adversarial reviewer's precision).

## Server and compute configuration

Install the optional `deploy/{faculty,faculty-inbox}.service.d/grading.conf` drop-ins.
Both services read `~/.config/faculty/grading.env` (mode 0600). Only local endpoint
URLs and credentials belong there; hosted endpoints, models, tiers, budgets and
review settings are in Grading → Compute. Never put real keys in this repository
or SQLite: an endpoint only NAMES the variable that holds its key.

```ini
FACULTY_GRADING_VERIFIER_URL=http://127.0.0.1:11435/v1
FACULTY_GRADING_GRADER_URL=http://127.0.0.1:11435/v1
# FACULTY_GRADING_LOCAL_KEY=server-only-if-required
# OPENROUTER_API_KEY=one-aggregator-key-reaches-every-default-role
# AI_GATEWAY_API_KEY=vercel-ai-gateway-key
# DEEPINFRA_API_KEY=... (or any other host; name the variable on its endpoint)
# NEBIUS_API_KEY=your-token-factory-key-or-endpoint-bearer-token
# NEBIUS_GRADING_URL=https://api.tokenfactory.nebius.com/v1
# MATHPIX_APP_ID=your-app-id
# MATHPIX_APP_KEY=your-app-key
# RUNPOD_API_KEY=only-when-the-dedicated-GPU-provider-is-enabled
```

Fully local endpoints must be loopback HTTP(S); hosted endpoints require HTTPS
(plain HTTP only on loopback, for the fake host of tests and the scratch app).
Redirects and environment proxies are disabled. A key variable must end in
`_API_KEY` and cannot be a `FACULTY_` or `MATHPIX_` variable, so a setting can never
send another secret to a host. Which services receive student work, and under what
agreement, is the instructor's own responsibility and is not enforced by software.

### Tiers, roles and hosted endpoints

Grading offers the local modes and Cloud (key `cloud_standard`).
Cloud uses reader, primary grader, second grader, verifier/arbitrator and flag_reviewer roles, never in vendor or model
names; `grading/config.py` is the only place those appear. A tier is a policy over
roles and endpoints, not a piece of hardware. Replacing a model is a settings
change. A batch freezes its whole profile, so it keeps the models it started with.

| Role | Sees | Default endpoint |
| --- | --- | --- |
| reader (literal reconstruction) | crops and OCR candidates; **never** the rubric, solution or expected answer | `qwen3-vl-32b` |
| primary grader | crops, the fixed transcript, problem, solution, rubric | `qwen3-vl-32b` (Qwen3-VL-32B-Instruct: the model every candidate has to beat) |
| second grader | the transcript, reading/layout context, problem, solution, rubric; crops for vision endpoints; no primary decisions | `gpt-oss-120b` (text-only, another lineage) |
| additional reviewer / final arbitrator | original inputs and both assessments, only for a disagreement | Choose an image-capable model |
| flag reviewer | original inputs, standing grade and review flags; no arbitrator-flagged responses | Choose an image-capable model |
| yardstick (benchmark only) | as the primary grader | `anthropic/claude-sonnet-5` through the aggregator |

Each OpenRouter or Vercel model field has a **Host** list below the model: the hosts that serve
that model through the gateway, with their prices, quantization, uptime (OpenRouter) and whether
they take a JSON schema or images. It is read from the gateway's public
`/models/{model}/endpoints` listing when you open it, never just to draw the screen. **Any host**
lets the gateway choose; a named host pins every request to it with no fallback (OpenRouter by its
endpoint slug such as `xiaomi/fp8`, Vercel by provider name such as `xiaomi`). Choosing a host also
takes its prices, so estimates and recorded costs count what it charges. The host is part of the
connection, so every role using that connection changes with it; the list says which roles share it.

`deepseek-v4.1-flash` is an optional OpenRouter connection, pinned to
`deepseek/deepseek-v4.1-flash` with the `deepseek` lineage. OpenRouter's public
catalog, checked 2026-09-20, lists image input and structured JSON output support.
It is available for any cloud team role without changing the existing assignments.
The preset uses an 8,192-token answer limit with reasoning disabled (editable in
Connections), and estimated prices of $0.15/M input and $0.60/M output tokens;
provider prices vary and reported call costs take precedence.
See the [OpenRouter model listing](https://openrouter.ai/deepseek/deepseek-v4.1-flash).

Every cloud model field (team, fallback, rubric reviewer and benchmark) has a
**Provider** selector for **OpenRouter**, **Vercel AI Gateway**, or **Other connections**.
Changing it filters both the dropdown and its **Browse** button. Browse opens that
provider's saved connections and live catalogue; Other connections keeps custom
and built-in providers available. Dropdowns show five recent choices from the
selected provider, keeping the current selection visible, plus None. Switching
back restores the previous draft choice. If the new provider has no saved connection
for that exact model, select a model from its own catalogue; provider-specific IDs
are never guessed. The existing workspace settings
own `recent_models`, a UI-only selection history (up to 40 names, newest first),
saved with the draft through the usual versioned settings API. It does not change
grading policy or retained grades and is excluded from grading previews and frozen
grading/template-reading profiles. Before choices have been recorded, currently
assigned models seed the list. Cancelling Browse or discarding settings preserves
the saved history; renaming/removing a connection updates its references.
All saved connections remain available in their provider's Browse, even offline.
Connections also has a provider selector and Browse button. Search names/IDs, sort by input
price and filter image/text support; incompatible models are hidden unless requested,
then shown disabled with the reason. The picker checks image requirements and
that the model can see images before adding a connection to the settings draft. Existing
connections for that exact model and provider are reused with all overrides intact.
New connections fill the model ID, server key-variable name, image/JSON support,
bounded answer limit and token-price estimates. Recognized base families are filled
in; unfamiliar families require the instructor to name the base family, never
inference from the publisher or tokenizer alone. Connection details remain editable.
Use model only changes the draft; Save compute settings persists the ordinary
versioned workspace preferences. Catalogue refresh never reassigns roles, changes
saved prices, starts a probe or rewrites a frozen batch profile.

`grading/model_catalog.py` reads the selected provider's fixed public `/models` URL with no
credentials or teaching content, only when the picker opens or Refresh is chosen.
Each provider has a separate in-memory one-hour cache and one concurrent fetch;
an eight-second timeout, bounded response size and 30-second failure backoff keep
an outage contained. A failed refresh retains the previous list and timestamp with
a stale notice. Normal navigation/settings reads do not load it. This disposable
metadata has no catalog table, retained payload or course/term/section owner;
selected connections remain existing workspace configuration. The API retains
the app's private-response `no-store` policy. Only text-producing models are listed;
dynamic routers and moving `~` aliases are excluded from these fixed model choices.
OpenRouter structured requests require supporting provider parameters while retaining
any explicit upstream pin. Vercel uses `https://ai-gateway.vercel.sh/v1`, bearer
`AI_GATEWAY_API_KEY`, its own model IDs and prices, image inputs and JSON schema output.
Its catalog's reasoning controls and temperature support travel with the selected
connection; unsupported temperature is omitted. Explicit host pins use Vercel's
`providerOptions.gateway.order` and `only`; OpenRouter-only routing/usage fields are
not sent to Vercel. See [Vercel's chat-completions contract](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions).
New metadata stays absent from legacy connections, preserving frozen profiles.
Provider metadata does not establish grading quality;
the existing probe and acceptance evidence still apply.

Every endpoint records its **lineage**: the family of its language model (and vision
encoder), and a fine-tune has the lineage of its base. Batches frozen before
2026-09-23 counted models of one lineage as one vote; new Cloud batches
ignore lineage (see "Model families decide nothing"). One
`OPENROUTER_API_KEY` reaches every default role; pin the upstream host per endpoint
(**Pinned upstream host**) so receipts name what actually served each request.

The visual transcript retains optional `layout_relations` (continuations, side
calculations, insertions, replacements, separate work and reading order) and
`reading_alternatives`. Each relationship names existing literal transcript lines
and describes visible evidence; uncertain relationships/alternatives block acceptance
and require review even when the model's free-text ambiguity list is empty. A note
naming a line that was not transcribed (or one line twice) does not refuse the reading:
on real papers one reading in eight was thrown away, asked again and billed again for a
note such as "line-10 feeds line-3" beside nine good lines (2026-09-20). The note is
dropped and counted with the reading (`layout_notes_dropped`, for the audit), and it is
NOT a doubt. It was one for a few hours that day, on the theory that the named line
might be missing work; 174 real readings said otherwise: a quarter carried such a note,
three quarters of them named a line past the end or linked a line to itself (the model
misnumbering, not skipping work), and those readings were as long as earlier readings
of the same responses (median ratio 1.00). As a doubt it put a quarter of all responses
into reading review and sent them to the additional reviewer for nothing.
A malformed note (unknown kind, no plain true/false) still refuses the answer. Old
transcripts remain valid and are never rewritten. These are fields of the existing
response/recognition JSON, owned by the same permanent term/section/enrollment
response; there is no new catalog owner or migration.

Model-provided rectangles are optional. Invalid shape, non-finite or out-of-range
coordinates, and rectangles without positive area become `bbox: null`; no scale or
decimal place is guessed. The affected transcript element or grading quotation
retains `bbox_discarded: true` for audit, including after revalidation, and the step
keeps the original provider reply. This marker belongs to the same term-owned
response evidence and does not create a reading doubt. Literal text, handwriting
marks, crop references, quotations and rubric checks retain their existing rules.
Required geometry, including instructor-defined answer areas, remains strict.

Both graders receive `reading_context`: retained OCR text, original OCR differences,
visual-versus-OCR differences and conservative resolution status. A discrepancy may
be formatting or reading order, never by itself a student error. Neither grader
receives the other's opinion. The second grader's endpoint capability determines
its input: vision endpoints receive the same crops as the primary, while text-only
endpoints keep the transcript-only contract. A structured second-grader
`reading_issue` survives score agreement and requires individual review; no reader
or grader may silently repair the student's math or replace the fixed transcript.
The review page shows layout notes and alternatives in a disclosure and identifies
whether the second grader saw images. Its reading concerns appear in Independent
checks. Changing the second grader's modality changes the acceptance scope; prompt
Benchmarks send crops
to vision second graders too. Existing settings/model selections are preserved.

The **Compute settings** button in the Grading page bar opens a popup over the
current assignment, without changing the selected grading step. Closing it keeps
unsaved edits until the page is reloaded; **Save compute settings** applies them
and **Discard changes** restores the saved values. Existing `tab=settings` links
open this popup too, and so does `#grading?settings=1` (Workspace settings → Settings elsewhere links there; the query
is dropped once read). Defaults starts with a **Next Cloud batch uses** table: each role's connection and model and
whether its key is set and its test passed, following the unsaved draft. It opens with the default processing mode, **Full concurrency**, a token-budget
toggle (on by default; each connection keeps its own answer and reasoning caps;
the model is not shown those numbers) and reading/review preferences. Picking a grading scheme there populates that scheme's own
model choices directly below the picker — Cloud's reader, two graders and two conditional reviewers — so the local models are not in the way;
**What holds a response back** leads with the review policy, then the evidence checks, with trial acceptance folded under Local and older workflows. **Advanced** holds
local runtimes, Nebius, throughput and dedicated GPU profiles in disclosures.
Settings apply to new batches and retain the existing workspace ownership.

**To add an endpoint**: Compute settings → **Connections** → **Add connection**.
Enter a name, model ID, model family, base URL and the name of its server key
variable. Capability and price details expand in that connection's editor.
**Apply to settings** keeps the edit in your draft; **Save compute settings**
persists it. **Test** checks a saved connection, and **Defaults** assigns its
role to the picked scheme. A persistent Save/Discard bar marks unsaved changes across sections.
Renaming updates draft role references; remove a connection only after replacing
its team and benchmark uses. No deployment is involved. `capabilities()` reports image
input, schema-constrained output, separated reasoning, the batch API, durable call
IDs, concurrency and rate-limit behaviour and prices. A **probe** sends one tiny
synthetic request (a sum, and the digits 47 drawn on a small image) and records
what the endpoint was *seen* to do, who served it and what it cost. An endpoint
that was never probed, or changed since, is shown as *unverified*; that is
recorded, never a blocker.

**How much a role's model thinks** (requirement, 2026-09-20) is a choice beside that
role's model in Compute → Defaults, offered from what the catalogue says about the
model and never guessed: whether it can think, whether it must, and which efforts it
offers (OpenRouter's `reasoning` block: `mandatory`, `supported_efforts`,
`default_effort`). A model with one possible configuration simply uses it, and the
screen says **Never thinks** or **Always thinks** instead of offering a dropdown. A
model that must think never offers **No reasoning**. The choice belongs to the ROLE of
a tier (`role_reasoning`), so a reviewer can think harder than a grader on the same
model. A role that made no choice keeps what its connection did before: an unmarked
model is asked not to think, one marked as a reasoning model keeps its host's default
effort, and an unmarked model that must think takes the least effort it offers. The
facts are saved with the connection like its prices (`reasoning_options`): Browse adds
them with a new model, shipped connections saved earlier take them from their shipped
definition, and any other earlier connection gets them by one explicit **Read reasoning
options from OpenRouter**, because this screen never fetches anything to draw itself. On
the wire an effort is sent as that effort, **Reasoning on** as the connection's reasoning
budget, **No reasoning** as `enabled: false`, and a model that never thinks is sent no
such field at all (that field alone once left it without an eligible host). The wire
limit still reserves that connection's reasoning budget beside its answer's (Compute
→ Defaults sends those per-connection caps when the workspace toggle is on; off omits
them for every model). The retained
step records what was asked, the estimate counts thinking by how much was asked for, and
the acceptance scope names the choice wherever one exists. A connection without facts
(a manual one) keeps its **Reasoning model** tick, and a batch frozen before these facts
existed behaves exactly as it did. Settings always load: a saved choice its model no
longer offers falls back to that default. A **reasoning model** keeps its reasoning apart from the
schema-constrained answer: the request's limit is its answer budget PLUS its
reasoning budget, so the answer always has room. An aggregator endpoint that is not
marked as a reasoning model is told not to think (`reasoning.enabled: false`); hosts
of models that never think ignore it, and a direct host is sent no such field. That
hint is advice, never a requirement. A structured aggregator request also carries
`require_parameters`, which makes EVERY field a routing requirement: a model whose
hosts do not list `reasoning` then has no eligible host, and the router answers 404
before choosing one (nothing ran, nothing was billed). Measured live on 2026-09-20,
this refused every reader and primary request of both cloud tiers. On exactly that
answer `HostedEndpoint.submit` withholds the hint and sends the same request once
more, remembers the route for fifteen minutes in the process (no retained record
depends on it), and notes `reasoning_hint: withheld` with the retained step. Any
other 404 fails again unchanged and stays a confirmed failure. The opposite refusal
was measured the same day: a model that cannot answer without thinking
(`z-ai/glm-5.3-flash`, saved from the catalogue without the reasoning mark) answered
400 "Reasoning is mandatory for this endpoint and cannot be disabled" to every request,
five unbilled attempts per response, so every response the two graders disagreed on
lost its additional reviewer and went to review without one (47 of the first 60). On
that answer the request is sent once more as the reasoning model it is, with its
reasoning budget beside its answer budget, and the step notes why. Mark such a
connection as a **Reasoning model** in Compute settings anyway: the estimate then
counts its thinking. Any other 400 is sent once and stays a confirmed failure. A
**truncated answer** (the host reports it ran out of tokens) is a confirmed failure
that is **never asked again automatically**: at temperature 0 the same request ends
the same way and every attempt is billed, so it fails with what to change. The host's
bill for a refused answer (malformed or truncated) is recorded on its call and
counted in the batch's cost. This too comes from the live run: the default reviewer
spent all 8,192 tokens thinking and returned nothing six times in seven, at 2.4
cents each; without thinking the same request took 25 s instead of 217 s, cost a
fifth, and raised the same objections. Where a host offers the OpenAI-style batch API, tick it: requests then
become durable batch jobs that survive a restart.

**The canary was removed** (requirement, 2026-09-28: "delete the canary functionality, I don't see myself ever using it").
It graded the earliest approved responses again before every cloud batch and stopped a batch whose agreement with them
dropped. A batch now starts grading at once. Saved settings that still name it load as before, and an earlier batch
still shows what its canary did.

### Cost controls

The batch preview shows the estimate before submission: the instructor's own
`cost_per_response` where set, otherwise assumed tokens per role times each
endpoint's configured prices (Mathpix is billed separately); without prices it says
so. The estimate is information only. **Faculty has no spending cap of its own**
(requirement, 2026-09-23: the OpenRouter and Vercel accounts carry the spending
limits): no batch, rubric review or rubric draft is refused or paused for its cost.
A request a gateway refuses for credit (HTTP 402) fails at once without being resent,
since resending cannot help; retry the failed responses after raising the limit. Every call records token usage and cost: the host's own
figure where it reports one, otherwise tokens times prices. Jobs and the Overview
show estimated and recorded cost; neither is a bill. Defaults: 5,000 responses per
batch, 8 cloud batches at once, 16 requests in flight per endpoint (halved
automatically on a rate limit), attempt cap 5, two automatic resends for an unknown
outcome.

**Full concurrency** (Compute settings → Advanced → Cloud requests; requirement, 2026-09-23: "lets them
fly as they are ready") starts every cloud component as soon as it is ready. While it is on,
**Cloud components in flight** and each connection's own limit are set aside (up to
`cloud.FULL_UNITS`, 5,000, the largest batch Faculty accepts); a connection whose host answers
"too busy" is still halved to what it had in flight and grows back, answer areas are still cut
a few at a time (`jobs.CUTTING`), and Mathpix keeps its per-minute pace. It is a workspace
preference read by the dispatcher every second, so switching it changes running batches too;
it is not part of any batch's frozen profile or acceptance scope. Off by default.

GLM uses the existing isolated `faculty_ocr` runtime/model manifest. See
[`faculty_ocr/README.md`](faculty_ocr/README.md). Local verifier and grader are
independent configurable roles, initially targeting quantized Qwen3-VL-8B-class
vision models through a compatible chat API or native Ollama.

This host has an RTX 2080 Ti (11 GiB). The dedicated
`deploy/faculty-grading-inference.service` serves quantized `qwen3-vl:8b-instruct` on loopback
port 11435, with Ollama cloud features disabled. Its weights and caches live under
`/mnt/nvme/Teaching/runtime/grading-models`; disposable scratch lives under
`/mnt/nvme/Teaching/tmp/grading-inference` and is cleared on service start. These
reinstallable runtime assets are not teaching payload owners or catalog backups.
The existing Ollama service on port 11434 is independent. The Faculty native
Ollama profile uses a 16,384-token context, deterministic
temperature, strict JSON schemas, `think:false`, one request at a time, and
`keep_alive: 0` to release GPU memory for GLM between turns. Native Ollama rejects
remote-backed models. Model accuracy still needs representative human-verified
handwriting benchmarks; a synthetic transport test is not classroom qualification.
Do not run a permanently resident full-size VLM beside GLM on this GPU.

Install the unit as `~/.config/systemd/user/faculty-grading-inference.service`, run
`systemctl --user daemon-reload`, then
`systemctl --user enable --now faculty-grading-inference.service`.
Pull the model with `OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen3-vl:8b-instruct` so its
weights go to the dedicated NVMe store. In Grading → Compute settings → Advanced → Local models choose **Ollama** for both local roles, model `qwen3-vl:8b-instruct`, and a timeout suitable for this
GPU (600 seconds). Review real samples yourself while establishing accuracy.
Use the explicit `8b-instruct` tag: the generic `8b` tag selects the Thinking
checkpoint, which produced unfinished responses in the local synthetic check.
`tests/grading_local_smoke.py` provides an explicit local-only synthetic check;
it is deliberately excluded from the offline regression suite. Use the isolated
check environment from `manage_checks.environment` so scratch and caches stay on
NVMe. It requires the pictured incorrect answer to remain incorrect in the
transcript and the approved rubric deduction to produce the expected backend score.

Provider availability checks never run inference: GLM checks installation,
compatible endpoints list models, native Ollama checks local model metadata, and
Mathpix reports credential configuration only. Nebius distinguishes unconfigured,
authentication failure, unreachable endpoint and unavailable model. Health is
cached for 60 seconds. Pending/failed work is also in Home's operational projection,
with no student names or teaching payload details.

## Deployment, the trial instance and live smoke testing

**Hosted endpoints need no deployment**: configuration only. After merging, install
nothing new except `sympy` (already in `requirements.txt` and in the shared
environment), make sure the key variables the endpoints name are in the server's
grading key file, and restart the web and Inbox worker services only after queued
and running work and storage activity are idle, as for any release. The cloud
dispatcher starts with the Inbox worker (`manage_inbox.py worker`) as a thread
beside its loop; no new service or timer exists, and the local Faculty service
lifecycle is not coupled to any cloud worker. With every cloud provider disabled,
unreachable or without a key, Overview, setup, local batches, review and export
work as before: endpoint health reports `not_configured` or `unavailable`, and a
cloud batch simply cannot be started or fails with its receipts.

**The trial instance** (this checkout's code on port 3200, against a COPY of the
preview catalog; never the live catalog, never the preview itself):

```bash
cd /mnt/nvme/Teaching/worktrees/cloud-grading && python3 manage_trial.py
```

It verifies the database path the application would open before serving anything,
refuses any data directory outside `/mnt/nvme/Teaching/tmp` and the ports of the
live service, the email listener and the preview, and stops both of its processes
(web and worker) by PID on Ctrl+C. From the server's key file it passes on only what
grading reads (endpoint key variables, Mathpix, and the loopback address of the
local model that reads answer areas, `FACULTY_GRADING_VERIFIER_URL`), without printing
a value. No other setting named `FACULTY_` reaches it: no mail relay, Gmail sender or
response-card Worker, so the copy's worker can grade and can do nothing outward. The
local model was added on the instructor's word (2026-09-25) so the trial's Answer
areas step reads blank templates. It is the GPU live's worker also uses, and the two
workers do not coordinate: avoid reading scans on live while the trial reads answer
areas, since a reading on both at once can run out of GPU memory and fail until retried. `--refresh-copy` replaces the copy with a fresh one of the
preview (SQLite's backup API plus the payload folders) and discards the trial's work.

**The scratch app without credentials.** `python tests/fake_openai.py --port 3290`
is a local fake OpenAI-compatible host (scripted answers; the batch API; the job
API of a dedicated GPU provider). Add an endpoint with base URL
`http://127.0.0.1:3290/v1` and an empty key variable, assign it to a tier, and
every cloud path can be exercised: submission, progress, retries,
escalation, trial acceptance and review. Plain HTTP is accepted on loopback only.

**Live smoke test** (cents; explicit, never part of the offline checks):

```bash
set -a; . ~/.config/faculty/grading.env; set +a
DATA_DIR=/mnt/nvme/Teaching/tmp/cloud-grading-3200/data .venv/bin/python tests/grading_cloud_smoke.py --sample 2 --question 22
```

It refuses any data directory outside `/mnt/nvme/Teaching/tmp`, grades a sample of
real scanned responses of each exam version with their images, shows that creating
the batch returns before any inference, prints every call with its endpoint,
upstream host, tokens and cost, and forces one confirmed failure (HTTP 500) and
one unknown outcome in front of the real endpoint to show the retry and the
bounded resend. The local GPU is not used.

**Grade a sample first.** The batch dialog's **Sample: papers per question** grades
a stable pseudo-random handful of each question's eligible papers (the same
students for every question of their exam version), which is what the Phase 0
loop and the live smoke test use before a whole exam is sent.

### Dedicated GPU provider: interface built, live deployment deferred

`DedicatedGpuProvider` implements the same interface as a hosted endpoint
(`submit` returns a durable job ID at once; `poll` and `cancel` work across
restarts; the retry policy applies unchanged) in the shape of RunPod serverless,
where the instructor has an account: `POST /run`, `GET /status/<id>`,
`POST /cancel/<id>`, `GET /health`, carrying the same chat-completion body to an
OpenAI-compatible worker. It is exercised against the fake host: successful
submission, durable job ID, polling, completed result, cancellation, a transient
failure retried, a permanent one stopping at the cap, a job the provider reports
failed, restart recovery and duplicate-result prevention. Resource profiles are
central settings (`dedicated_profiles`: GPU type and count, CPU, RAM, timeout,
container concurrency, quantization, engine, minimum workers, idle seconds); the
default profile is **one 24–48 GB GPU** that scales to zero. An endpoint of kind
*Dedicated GPU provider* names its profile, and is refused while **Enable
endpoints of the dedicated GPU provider** is off.

The live deployment is **deferred by design** until the benchmark shows a need no
host serves: a specialist handwriting reader, a small independent verifier, or the
instructor's own fine-tuned model. Every default role is served by a hosted
endpoint today (verified live), hosting a large model on rented GPUs costs about
three times as much at this volume, and no labeled data exists yet to say a
specialist is needed. When it is built: a vLLM worker image with pinned
dependencies and the weights in a network volume (never downloaded per response),
`min_workers` 0 so nothing runs while grading is idle, workers kept warm only
during an active batch, `RUNPOD_API_KEY` in the server's key file, the endpoint's
base URL `https://api.runpod.ai/v2/<endpoint-id>`, then **Probe this endpoint**.
The live checks to run then: the requested GPU is allocated, the configured model
(or a lightweight stand-in) loads, inference completes, the result returns
asynchronously, and the endpoint scales down.

## Troubleshooting cloud grading

| What you see | What it means | What to do |
| --- | --- | --- |
| Endpoint `not configured` | the key variable it names is not set for the web and worker services | add it to the server's key file; restart both services when idle |
| `The account has no credits (HTTP 402) … Retrying cannot fix this` | the aggregator account is empty | add credits, then **Retry failed responses** |
| `Authentication failed (HTTP 401/403)` | the key was rejected | replace it; nothing is retried automatically |
| An earlier batch shows a canary notice | it ran before the canary was removed (2026-09-28) | nothing: new batches run no canary |
| `stopped at the attempt cap` | the same confirmed failure repeated | read the error, fix the cause, **Retry failed responses** |
| `outcome … still unknown after 2 automatic resends` | the host kept dropping or timing out | check the host; retry when it is healthy |
| `The account has no credits (HTTP 402)` | the OpenRouter or Vercel account's own spending limit | raise it at the gateway, then retry |
| Papers blocked: *exam version is still being confirmed* | the whole-paper comparison has not reached them | wait for the worker; Setup → Exam versions shows progress |
| Papers blocked: *seated for Version X, but its pages match Version Y* | seat and paper disagree | Setup → Exam versions → **Decide** |
| Many Mathpix reads fail with *Mathpix is rate limited or busy* and then succeed | the account's requests-per-minute limit was met | lower **Mathpix requests per minute** in Compute below the account's limit (Mathpix: 200 by default; the instructor's is 1,000 since 2026-09-20) |
| Many responses escalate | the evidence often contradicts proposals | read the escalation reasons on the batch in Grade; usually an ambiguous rubric or a wrong key, which another model cannot fix |
| A model's answers are rejected as malformed | the host has no schema-constrained output for it | set **Structured output** to what the probe saw, or use another host |

Logs carry identifiers, timings, token counts and status only (`cloud call
completed call=… step=… endpoint=… model=… upstream=… seconds=… tokens=… cost=…`,
`cloud call failed (confirmed)`, `outcome unknown`, `response escalated`); never a
payload, an image or a provider body. Why a response was escalated, failed or sent
to review is on the response itself (`signals.escalation`, `reasons`,
`signals.risk.blocks`), and which provider, model, tier, prompt version and
upstream host served each request, with its submitted and finished times, is on
its `frq_cloud_calls` rows and step receipts.

## Nebius: supported transport and account requirements

The implementation uses documented Token Factory vision chat completion with
inline images and bearer authentication. The same `LocalVlmProvider` client, named
`nebius`, serves it for health checks. Faculty supplies the
durable asynchronous batch; it does not depend on an undocumented remote batch API.
The built-in hosted endpoint `nebius` uses the same client, call records, retry
policy and parallel lane as every other endpoint, and can be named for any Cloud
role. (Its own Nebius mode was removed on 2026-09-23.) The UI shows the stages actually performed
(queued, extracting, reading, checking, grading, complete/failed/cancelled).

Choose **Hosted inference** for a suitable available hosted model, or
**Faculty-managed inference endpoint** for your already provisioned GPU server
running a vision-capable compatible server such as vLLM. The same configurable
HTTPS transport supports both. The model target is Qwen3-VL-30B-A3B-class; the name
is a setting, not a promise that a particular account currently offers it.

You supply the endpoint/account key, model availability and account retention
agreement. Nebius has no configured prices, so set a conservative estimated cost
per response and maximum job spend. The estimate includes verification and grading;
it is not a provider quote. Retries add to estimated spend. Token usage is
measured; actual billing cannot be inferred from runtime. Mathpix charges are
separate and are not included in the estimate.

This adapter creates no remote VMs, disks, buckets, files, datasets or retained
conversations. Each request asks for `store:false`; terminal cloud runs record a
`no_remote_files` cleanup receipt. Provider operational logs remain governed by
the account agreement. This does not assert zero retention or deletion of provider
logs. Cancellation stops unsent work and cannot retract an accepted HTTP inference.

For self-managed compute, provisioning/readiness, GPU capacity, TLS/authentication,
container lifecycle and infrastructure cleanup are the operator's responsibility.
Faculty does not silently provision or tear down billable infrastructure. The
provider boundary can later support a provisioner without changing grading owners.

Official documentation checked during implementation on 2026-09-19:

- [Nebius vision requests](https://docs.tokenfactory.nebius.com/api-reference/examples/vision-capabilities)
  and [chat completion](https://docs.tokenfactory.nebius.com/api-reference/inference/create-chat-completion):
  bearer token, inline image inputs, structured output and `store:false`.
- [Dedicated endpoint lifecycle/readiness](https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/lifecycle-and-status):
  ready-to-serve status is distinct from infrastructure lifecycle.
- [Service-account authentication](https://docs.nebius.com/iam/service-accounts/authentication):
  cloud infrastructure IAM is distinct from Token Factory endpoint credentials.
- [Host a model on Compute](https://docs.nebius.com/compute/quickstart-host-model),
  [GPU platforms](https://docs.nebius.com/compute/virtual-machines/types), and
  [stop/start](https://docs.nebius.com/compute/virtual-machines/stop-start): managed
  GPU infrastructure is an alternative to a hosted endpoint; capacity is project-dependent.
- [Object Storage transfer/access](https://docs.nebius.com/object-storage/interfaces/aws-cli)
  and [VM deletion](https://docs.nebius.com/compute/virtual-machines/delete): separate
  storage/resources need explicit lifecycle management. They are unnecessary for
  this inline transport. Do not infer that cancelling Faculty work stops VM billing.
- [Qwen3-VL-30B-A3B model](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct),
  [Ollama local vision family](https://ollama.com/library/qwen3-vl), and
  [native structured chat/keep-alive](https://docs.ollama.com/api/chat).
  [Structured-output guidance](https://docs.ollama.com/capabilities/structured-outputs)
  informs the explicit schema included in both the prompt and decoder; backend
  validation still independently checks every output.
- [Mathpix text API](https://docs.mathpix.com/reference/post-v3-text): line geometry,
  text/math output and `metadata.improve_mathpix:false`. This option is not a
  contractual assertion of zero provider retention.

## Gradescope formats and exports

### Automatic template verification

Verification starts on the worker after both a question's rubric and a compatible
course-owned blank template exist, in either upload order. An explicitly selected
question template takes precedence; one compatible template can be proposed
automatically. Several compatible templates or an incompatible page count require
manual attention. **Choose blank template** selects the reference and reruns OCR
without defining or confirming answer areas. It retains the rubric, creates a new
question revision, and clears previous response regions; existing privacy masks
require acknowledgment before replacement. The dialog's default **Also use it for
the other questions** option records the same explicit choice for every question of
that printed version that still has no response areas, so an imported exam needs
one decision rather than one per question. Questions that already have response
areas keep their own template, regions and confirmation. While pages are read, each
question's status reports the shared progress (`5 of 12 pages read`). A stamps-only version's page count
describes labels, so it does not constrain the blank exam's page count. The
scanned papers do: when most grouped papers of that version have a different
number of written sides than the chosen blank exam has pages, the check says so
and requires manual review, because those papers could not be graded against it. Ordinary
exam versions still require a matching page count. This does not identify any
student pages: grading still requires every reviewed exam page, and a first-page
label alone cannot supply them. The question editor's **Printed question number** takes precedence
over a rubric filename such as `1_Question_18_a_and_b.csv`; that filename identifies
printed problem 18, while `R1_1` remains Gradescope's internal rubric-item ID. Without
either, the question title/key supplies the candidate number. A number is never
chosen from a matching point total or a solved answer.

Local GLM reads each verified template raster and the local visual model proposes
numbered headings, adjacent totals and page-relative response rectangles. It never
receives the expected rubric maximum. The backend requires one matching heading,
agreement between the two readings and exact equality with the rubric maximum.
Each proposed response area runs the full width of the sheet from the question's
printed heading down to the next printed problem on that page, or to the bottom of
the page, because students write in every available gap. Space above a heading is
never added, and problems printed side by side keep the reader's column width.
This proposal rule is part of a check's input but not of the page-reading profile,
so changing it recomputes checks from the retained page receipts without new OCR.
Missing/unreadable headings or totals, low confidence, duplicate question numbers,
unclear continuations and mismatched points require manual review. Per-part scores
are not summed into a guessed question total. These are OCR checks, not guarantees
of recognition accuracy; **every** location proposal still needs confirmation.
Reader v3 retains question-specific warnings with that question; an unreadable
equation or absent total for another question does not fail all uploaded rubrics.
Unassignable continuation pages and page-wide reading failures still require review.

`frq_template_readings` retains immutable page receipts under the course-wide
template reference; shared page readings avoid repeating OCR for each question.
`frq_location_checks` and append-only `frq_location_reviews` belong to the existing
term grading plan and reference exact question, rubric and template inputs. The
worker rotates verification with rubric preparation and response grading, reads at
most one template page per turn, and retains failed/interrupted attempts for review
or explicit bounded retry. No hosted OCR is used for this automatic template check.
A check records the local model's address it was queued with and refuses to read when
the configured one differs; an explicit retry of a FAILED check records the current
address (migration 023, 2026-09-25), so a check queued before the model was connected
or moved can still be read. Nothing else about a check changes, and the retry's event
keeps the address it replaced.
The web and worker must both have the local verifier endpoint and selected model
configured, including independently launched scratch apps. A missing endpoint
fails before spending time on OCR and reports that cause explicitly.

Confirmation creates an immutable question revision with the approved template and
response regions, keeping the rubric's separate approval intact. It never changes
printed originals, shared template maps or point values. A manual resolution needs
a reason; a typo on the printed paper can be recorded without silently changing
the rubric. Changes to the pinned question or rubric invalidate the confirmation.
Grading previews, worker execution, grade approval and export revalidate this gate.
Existing manual-region workflows without an uploaded blank template remain available.

### Rubric import

The CSV importer accepts `id,description,points,notes,exceptions` columns and
per-question Gradescope Export Evaluations files. The supplied compact layout has
nine submission columns before the descriptions, but six columns before the footer
items; these ranges are matched by item order, not absolute CSV column. Older aligned
footers remain supported. Rubric numbers, descriptions (including LaTeX and quoted
commas), fractional/zero points, and full-credit alternatives are preserved.

`Scoring Method` controls positive/negative scoring. The exported question total
must match the Faculty question maximum; edit the question when they differ. Values
are never scaled, and alternative item values are never summed to infer a total.
The upload dialog displays detected item count, scoring method, total and mismatches.
Malformed, incomplete and mixed-question footers are refused. Select one question's
rubric at a time. Student evaluation rows do not become rubric rules or compiler input.

Exports omit grouping and selection settings. Every import flags full-credit versus
partial-credit alternatives, part totals and item combinations for instructor review.
Explicit follow-through wording is retained; multiple positive awards for correct
subsequent work may share an originating error. Repeated negative penalties remain
subject to the approved follow-through rules. JSON represents the complete rules.
The optional local compiler receives parsed facts for CSV/JSON and cannot change
item IDs, point values, scoring method or total. It may propose rule structure for
review, while exact exported descriptions and source details are retained.

An explicit workspace source upload archives its original under the course owner
through the existing Inbox storage lifecycle; it never approves a rubric. Uploaded
bytes remain immutable, including BOM and line endings. Interpretation saves the
submitted text and a reference to that retained original in a new draft revision.

The current documented Gradescope workflow does not provide a general arbitrary
rubric-application write API. Faculty used the Gradescope Entry View and safe CSV
exports. Since 2026-09-25 the instructor allows Faculty to use Gradescope's private web
endpoints, signed in as the instructor, to upload reviewed papers and check the rubric
items of final grades (next section, and "Sending grades to Gradescope" in AGENTS.md). See the
official [rubric guide](https://guides.gradescope.com/hc/en-us/articles/22249389005709-Grading-submissions-with-rubrics)
and [exports guide](https://guides.gradescope.com/hc/en-us/articles/22239057709453-Exporting-Grades-Evaluations-and-Submissions).

### Sending grades to Gradescope (2026-09-25)

Gradescope has no public API; Faculty signs in with the instructor's saved email and
password and reads and sends what the website itself does. It sends only what an export
would call final, and every write is checked on Gradescope afterwards.

**Setting it up.** Run `python3 ~/.config/faculty/save-gradescope.py` in a terminal: it asks
for the email and a hidden password, saves them to `~/.config/faculty/gradescope.json`
(readable by this account only) and signs in once to check them. The live web app and
worker read that file only because their drop-ins (`deploy/faculty{,-inbox}.service.d/
gradescope.conf`) name it; the trial reads it only when started with
`manage_trial.py --gradescope`; the preview and every check never do. Faculty never opens
the course **Math 140 for Instructors**.

**Preview.** On Certification and Export, **Send to Gradescope...** asks for each section's Gradescope course
(proposed: the course whose short name ends in the section number, in the same term) and
the exam in it (proposed: the one exam that is not a bubble sheet and has every version the
plan grades). **Leave this section out of this send** under a section keeps its choices but sends
nothing of it this time: its course is not even read, no link is recorded, and the next preview
offers it again, so one section can go first and be checked on Gradescope before the rest. The
preview then says, per section, version and question:

- papers to upload, already there (uploaded by Faculty earlier) and skipped, with the reason:
  no reviewed paper, version not decided, no Gradescope student with that UIN (or two), a
  page count unlike the Gradescope template's, or a paper on Gradescope Faculty did not
  upload (that student's grades are then left alone too);
- grades to send, to update (Faculty's own earlier grade on Gradescope, replaced only if
  Gradescope still shows exactly it), already there, refused and held (their paper was not
  sent), and how many grades are not final yet;
- refusals, grouped by reason: the rubric was not imported from Gradescope's export and the
  question was not made by Faculty on Gradescope (so no item can be named for sure), a line worth other points on Gradescope, a question whose
  number, title or worth differs, a grade Gradescope would score differently, a grade with no
  line checked;
- warnings: lines worded differently on Gradescope (only shown);
- papers and grades of sections left out of this send (counted, not listed).

Rubric lines are matched by the export numbering Faculty keeps on every imported line (the
question's Gradescope number and the item's place in the export order) and by points; each
section's exam is matched on its own, because sections' rubrics can differ.

**Send.** The tick and **Send** name the preview's fingerprint. Faculty reads Faculty and
Gradescope again and refuses a changed preview (look again and send). The background sender
then uploads the papers (`faculty-<receipt>.pdf`, as the student) and sets the grades one at a
time, reading each back; the panel shows the counts as they move and **Show problems** lists
what did not go (student names, no UINs). **Cancel** stops what is still waiting; **Retry
failed** sends again only what Faculty read as not kept. A Gradescope exam whose grades are
already published is a warning in the preview, not a refusal (requirement, 2026-09-26): Send then
asks in a popup, naming each published exam and what would go there, and the server refuses a
send that does not confirm each one. An exam published after the send was approved still gets
nothing from it (preview and send again to confirm). A send never publishes or emails students. The only comment it
sends with a grade is that grade's batch lines and, for a plan graded for participation, its participation line.
Publishing is the separate, explicit **Publish grades on Gradescope…** action (`docs/decisions/gradescope.md`,
PUBLISHING GRADES).

**No assignment on Gradescope yet?** (2026-09-27; for any assignment, not only exams.) **Create the assignment on
Gradescope…** on the panel, also offered in the send dialog when a section's course has none, makes it in every section's
Gradescope course that has no assignment linked: one instructor-uploaded assignment per version with that version's confirmed
blank as its template, one question per Faculty question (printed number in its title, its points, its confirmed answer areas
as the boxes) and each question's rubric lines with Faculty's wording and points; with versions, the versions are grouped as one
set named after the assignment (A, B, C in order). The dialog shows what would be made per version and where, why anything
cannot be (answer areas not confirmed, a name already taken in that course) and warnings, and asks for a tick. Faculty then makes
it in the background, a step at a time; the panel shows each course's step, **Cancel** and, when a course stopped because
Gradescope shows something Faculty did not make, the reason and **Retry**. When it is done, each section is linked to it and
Send to Gradescope uses it. Faculty remembers which Gradescope item each rubric line became, so rubrics drafted or adapted in
Faculty (not imported from Gradescope's export) can be sent to what it made.

**Papers without grades** (2026-09-28). The send dialog asks **What to send**: papers and final grades, or **Papers only**
(**Upload papers only…** on the panel opens it on that choice). Papers only uploads each student's reviewed paper, matched to
them, to the assignment of their version, by exactly the rules above, and sets no grade: no rubric is read and nothing is
ticked. The preview says how many final grades stay in Faculty. Sending papers and grades later finds those papers already
there and sends only the grades. To put the assignment and its rubric on Gradescope first, create it (above), then upload.

**When Gradescope is busy** (it answers 429 Too Many Requests, 502, 503 or 504), nothing fails
for it. The sender works on 24 papers and grades at once (8 until 2026-09-26), each on its own
connection of one sign-in; one at a time a paper took about 7 s and a grade 4 s in the sandbox
rehearsal, and sandbox tests answered up to 24 grade sends (about 230 grades a minute) and 8
uploads at once with no refusal. 24 uploads at once were never tried there: if Gradescope
answers busy, the rule below slows the sender down. A grade whose paper
Gradescope is still processing (5 to 11 s measured) tries again after 15 s. On a busy
answer the whole sender stops asking for 30 s, twice as long after each busy answer in a row
up to 15 minutes, and never less than a Retry-After Gradescope sent (honoured up to an hour);
the session then waits twice as long between requests (at least 1 s, at most 5 s) for the rest
of the run. The paper or grade goes again by itself after the wait: after a 429 or 503
Gradescope did not do it, and after a 502 or 504 Faculty first reads whether it landed
(accepted if it did). Every try reads Gradescope before it writes, so nothing is ever sent
twice. Twenty busy answers in a row for one paper or grade fail it for **Retry failed**. A
sign-in Gradescope was too busy to answer is waited for, not taken for a refused password. A
500 is not "busy": it is a definite refusal, as before.

The audit includes original source/assembly hashes, crop hashes, raw OCR/model
responses, recursively reused receipts, prompt/model/schema identifiers, exact
question/solution/rubric revisions, machine proposal and instructor decision.
CSV includes question scores, selected items, adjustments and comments. Totals
require every configured question of that student's printed version.

## Benchmarking and limits

Run alternative batches on the same source/rubric and preserve the human decision.
Use local, GLM-only OCR, Mathpix-only OCR, combined OCR, different
local/cloud verifier/grader model settings. Each job freezes its
profile. Review → **Compare with approved work** reports the latest result per
exact profile and response, using the instructor-approved work as truth.

Download the **Model comparison set** (Certification and Export → Saved for training → What the files hold), then:

```bash
.venv/bin/python manage_grading.py benchmark /mnt/nvme/path/grading-dataset.zip \
  --output /mnt/nvme/path/benchmark-report.json
```

The command is offline and never runs inference or training. Its report compares
retained GLM/Mathpix readings, visual verification and local/cloud grading profiles.
You can also supply a JSON dataset with a `predictions` map per response for externally
produced benchmark candidates. OCR-only candidates have no fabricated grade metrics.
Metrics include CER, math/symbol/operator disagreement, transcription correction,
exact scores, point MAE, item precision/recall, false/missed deductions, human
override and review rates. Missing denominators report null. Results describe the
reviewed sample, not unbiased population accuracy or calibrated confidence.

Dataset IDs are opaque and roster fields are omitted, but handwritten images,
transcripts and coordinator material can still identify people. These are private
local evaluation artifacts. By the instructor's decision of 2026-09-19 cloud
grading has no privacy constraints: no crop approval, masking, scrubbing or payload
restriction is required, and none may block or slow a batch. Cloud payloads carry
the crops (with any privacy masks the instructor drew), an opaque response ID and
the question, rubric, solution and transcript; text is sent as transcribed,
because rewriting it breaks the grader's exact quotes. Faculty still owns the
identity mapping: no roster field is sent, because none helps grading. Instructor
corrections may be used to fine-tune readers and graders (see *Learning from
corrections*).

Synthetic fixtures cover ambiguous digits, faint signs, fractions, superscripts,
inequalities, matrices, erroneous arithmetic and follow-through. They exercise
contracts; they do not certify recognition accuracy. Mathpix/Nebius integration is
mock-tested until account credentials and an approved real benchmark are supplied.
Strong page warping, writing far outside configured regions, unfamiliar notation
and rubric exceptions remain instructor review cases.

See [CHECKS.md](CHECKS.md) for isolated validation and [FRONTEND.md](FRONTEND.md)
for UI contracts. After frontend edits, restart Faculty only after queued/running
work and storage activity are idle. Keep the email-only public listener on port
3001; the private Faculty service stays on port 3000.

## Cloud grading validation — 2026-09-20

`python3 manage_checks.py full` and the grading browser workflows pass on the
`cloud-grading` branch, which was merged into `main` (after merging main's five-stage
workflow into it and testing that) and activated on 2026-09-20: the web and worker
services were restarted only after the Inbox queue, storage and the nightly backup
were verified idle, and answered without errors afterwards. The live run is described in `CLOUD_GRADING_REPORT.md`: on a
copy of the preview catalog (port 3200, never the live catalog or the preview) all 325
papers were confirmed against their exam version's blank exam (325 of 325 agreed with
the seating chart), the live smoke test passed with a forced confirmed failure and a
forced unknown outcome in front of the real endpoint, and 650 real responses of two
questions were graded in 65 minutes for $4.84. The two graders disagreed about the
score on 27% of them, the adversarial reviewer objected materially to 82% of what it
saw, and 6.8% passed every rule for trial acceptance: the committee is not ready to
accept anything unseen, which is what the gate is for. That run found twelve defects
no synthetic test had shown (a reviewer that spent its whole budget thinking, unbilled
refused answers, signals true of every response, a key check that accused correct
work, quotations refused over line breaks, Mathpix's per-minute limit, and a crop
preparation that crashed on poorly aligned scans); each is fixed with a regression
test and described where its rule is documented above. Simulated responses cannot
replace that run, and nothing here is calibrated until the instructor has reviewed
real responses.

## Implementation validation — 2026-09-19

The full regression run passed 681 Python tests plus frontend syntax, unit and
type checks. The final focused grading suite passed 37 tests, including added
context-change and long-response metric checks. Every browser workflow passed;
the chart zoom check was rerun after fixing its import to use the actual immutable
frontend build. The grading workflow covered real APIs with synthetic providers,
rubric approval, cloud crop approval, failed-job recovery, human corrections,
exports, light/dark themes and desktop/mobile widths.

The installed local pipeline passed a separate synthetic smoke with GLM-OCR and
`qwen3-vl:8b-instruct`: it preserved `x=8` in visibly incorrect work, applied the
sole approved deduction for a backend score of 9/10, and preserved a follow-through
rule during rubric compilation. No student work was used for these model checks.

Web and worker services were restarted only after verifying idle queues and
storage, under the existing execution/lifecycle locks. The live browser smoke
visited all six grading tabs at 1500px and 390px in both themes, with no console
or HTTP errors and no grading-data writes. Local provider health, the worker
heartbeat, private `no-store` responses and the email-only port 3001 boundary
were verified. At that initial check, Mathpix/Nebius were unconfigured; their
contracts were tested with synthetic transports. Classroom accuracy still requires
a reviewed sample.

The subsequent Gradescope import correction checked all eight supplied CSVs:
89 exact description/number/point mappings and question maxima 4, 2, 2, 4, 6, 8,
6 and 8. Regression fixtures reproduce their structure with synthetic wording.
The focused grading/import/app run passed 68 tests, including managed source
ownership, request limits, rejected total mismatches and changed compiler points.
The quick suite passed 145 tests and frontend checks. The browser grading workflow
passed CSV upload, automatic scoring detection and draft review through real APIs,
followed by grading, human approval and exports. A separate synthetic CSV-derived
rubric passed the installed local compiler with the `compile-v2` prompt while
preserving all scoring facts. After the idle service reload, the new immutable
rubric module, grading health, storage and worker status were verified live.

Automatic template verification passed 21 dedicated tests in both storage modes,
the 145-test quick suite and frontend checks. Browser checks covered automatic
status updates, visible overlays under the app's CSP, drawing and editing areas,
required confirmation, manual point-mismatch resolution and the existing grading
workflow at desktop/mobile widths in both themes. The installed GLM-OCR and
`qwen3-vl:8b-instruct` read a synthetic blank exam's questions 18 and 19 with
printed totals of 4 and 2; both matched their rubric maxima, while a deliberately
incorrect maximum required manual review. Every result still required location
confirmation. These checks used synthetic material and isolated NVMe scratch.

Rubric-first setup passed 47 focused upload/location/migration tests, the 145-test
quick suite and all three grading browser workflows. The supplied eight CSVs also
passed through the new upload endpoint in an isolated empty assessment, creating
questions 18–25 with all 89 items and their exact maxima. Browser checks covered
one multi-file selection, automatic shared template readings, required confirmation,
repeat uploads preserving confirmed work, and a failed file beside a successful
import. No live grading data was created by these checks.
