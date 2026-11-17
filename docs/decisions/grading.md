## Assisted FRQ grading

2026-10-09 assignment flow: Prepare shows missing templates, rubrics, answer areas, keys and finalization as links to their owning controls, with an explicit Prepare remaining setup handoff to the assistant. This widens no automatic approval tier: the handoff is review-only (server-enforced `review_only`, 2026-10-09 review), so every change it prepares, automatic categories included, is an Approve card. Review shows the first three concise flag reasons beside the decision, retaining every full reason in a disclosure. The split review desk supports inline fit/zoom/pan, a browser-session zoom preference and full-screen viewing without changing source images or evidence coordinates. An empty queue points to unfinished preparation, grading or other review work before offering certification; blocking certification checks lead to their existing corrections.

Rule ID: **GRADING**. Current binding module contract. Read root `AGENTS.md` first.

KEEP CONFLICTED EDITS (requirement, 2026-10-17, assignment review item 1): a refused review save keeps the
instructor's edits in the browser and keeps the leave guard. Review updated grade reads the same response, shows
the current evidence alongside the kept edits, and offers explicit Apply or Discard. Applying only restores fields
the instructor changed, never saves or approves. Rubric selections and scoring edits are restored only while the
question and complete rubric still match; otherwise only transcription and comment edits may be restored and the
instructor selects scoring again. A failed reread leaves the edits intact with retry. The reread is the page's own, with
its reveal choice: a blind proposal is never revealed by it. The page's queue and counts are read again around the kept
desk, never by remounting it. No persistent draft owner,
automatic resubmission, grade change, or new permission is introduced.

2026-10-09 assistant capabilities: typed preparation and rubric tools use the existing routes and exact current question/rubric
revisions. The approved material bridge may import text into one existing question's rubric; approval, answer-area confirmation,
finalization and certification retain their own requirements. Readiness can prepare missing prerequisites before papers arrive;
it never treats preparation metadata as evidence that the instructor approved a grading prerequisite.
ASSIGNMENT-WORKSPACE (requirement, 2026-10-03): Assignments is the primary navigation
entry. The existing grading routes, review desk, source gates and six internal steps
remain usable in its shared shell. Rubrics & Templates additionally shows each question's
wording, worked solution and existing answer-area controls together; each confirmation
retains its own owner and validation. Explicit assignment creation now creates/reuses
the term grading plan and prepares later registered PDF templates, with retryable partial
failures and printing independent of grading readiness. No overview read performs setup.
The Grading index is retired (requirement, 2026-10-18; ASSIGNMENT-WORKSPACE "THE GRADING INDEX IS RETIRED"):
`#grading?view=list` and every `#grading` address without an assignment open Assignments; compute availability and costs
are Compute settings' "Availability and costs" section; the assignments whose scans wait are named on Collect and the Inbox.
Measurements, incident notes and background quotations moved on 2026-10-01 to
`docs/history/grading-provenance-2026-10-01.md` (non-binding).

- **Cloud** (named Cloud Standard until 2026-09-23; `grading/standard.py`, instructor,
  2026-09-22) is the ONE cloud scheme for new batches. The rename is display only: the key
  stays `cloud_standard`, frozen in profiles, scopes and the `frq_jobs.tier` CHECK. Keep the two main graders
  and their reading/fallback behavior. Only a disagreement on their validated
  rubric selections calls the additional reviewer, permanently the final
  arbitrator: adopt either complete assessment, propose a new validated assessment,
  or declare no reliable grade and require manual review. Other flags never call
  it. A fourth grading role, `tiers.cloud_standard.flag_reviewer` (with ordinary
  per-role reasoning), reviews other flagged standing grades: uphold that unchanged
  grade when its evidence outweighs every flag, or require manual review. It cannot
  change ticks or scores. It is asked only about a grade two complete assessments
  agreed on (review fixes, 2026-09-22): an incomplete pair, a standing grade no
  retained assessment is, or any `risk.instructor_only` flag (no/failed second
  grader, blank crop, rubric condition, unclear rubric wording) is recorded as
  `not_asked` on `grade:flag-review:check` (`standard.barred`) and stays with the
  instructor. No Cloud ruling settles an instructor-only flag
  (`arbitration.apply` keeps it standing), and a ruling's own re-asked answer is
  never a new flag. MODEL FAMILIES DECIDE NOTHING (requirement, 2026-09-23): EVERY new
  batch, in every mode, freezes `family_rule: family-blind-v1` (`jobs.py`,
  `standard.family_blind`). It counts two same-family graders agreeing as an agreement and
  each assessment as a vote (`votes.basis: assessments`, `risk-rules-v15`), raises no
  `risk.ONE_LINEAGE` block, and lets the arbitrator adopt, and the flag reviewer uphold, its
  own family's grade. Model family (`lineage`) is optional audit metadata: settings,
  fallbacks and the model picker accept any, shared or unknown family in any role, and
  neither Browse nor the connection editor asks for one; the catalogue still records the
  families it recognizes (GPT-6 is `gpt`, Xiaomi MiMo `mimo`). The benchmark ensemble counts
  one vote per model. A batch frozen before the key keeps the family rules below
  (one-family agreement unresolved, same-family reviewer barred, same-family adoption
  `unsettled`, one vote per lineage), and a blank family there never counts as independent.
  Spares are labelled `<tier>.<role>_fallback`, so the standing grade always has a
  label or nothing finalizes. EVERY arbitrator flag, failed ruling, unresolved ruling,
  and new flag from checking its final selections bypasses the fourth model and
  goes directly to manual review. Model settings are workspace preferences frozen
  on existing term-owned batches as `cloud_standard_rule: cloud-standard-v2`.
  Requests/checks/rulings remain steps of the existing response, no new owners or
  migrations. Both reviewers must be selected before starting a batch. Preserve
  deterministic scoring, rubric finalization and human-review gates, receipts,
  bounded retries, costs, scope hashes and all historical batches. `flag_review`
  identifies fourth-model finalizations in review/export. The older team rules
  below stay in code, unreachable for new batches, as the harness of their tests; the
  new rule supersedes them for new work.

- **MULTIPLE-CHOICE READER** (`grading/choice.py`, rule `choice-reader-v1`; requirement, 2026-10-08: "add decisions API as a
  multiple-choice grader for multiple choice questions which aren't bubble sheets"; asked, they chose that Faculty proposes and
  they confirm at Key & Rubric Finalization, and that an unsure read falls back to the ordinary path). Not bubble sheets
  (BUBBLE-SHEETS reads those by pixels).
  - CLASSIFICATION, AUTOMATIC (requirement, 2026-10-08: "automatic confirmation", replacing a click per question). A grading
    question is multiple choice when its rubric is exactly the drafted two-line shape (MULTIPLE CHOICE IS PASS/FAIL ON THE CHOICE
    ALONE, below: "Correct choice: (x)" worth every point beside "No answer or completely incorrect" worth 0, or, deducting, worth 0
    beside a line taking every point; nothing else, no group, requires or excludes), it has one answer area, and its statement
    prints two to five lettered options including the rubric's key (a drafted quiz's multiple-choice problem is drafted in exactly
    that shape; this term all 13 multiple-choice questions and no other). The rule (`choice.resolved`) reads the question revision
    and rubric a task pins, so nothing is saved and nothing goes stale; it applies to new tasks of questions graded earlier too,
    never to a grade already given. Key & Rubric Finalization shows it on the question with Grade as written work
    (`content.choice_grading` {"written": true} through `save_answer_key`, the key's own path), and Grade by choice to undo that;
    neither once any response to the question has a grade or a decision, since a new revision sends its grades back as new work.
    A finalization's clarifications never hold a question back (requirement, 2026-10-17, of Quiz 5 Question 1 graded by the
    ordinary path: "I thought a decision model was supposed to grade the multiple choice"). The finalization writes one for
    every multiple-choice line, describing how a choice is marked, so the earlier hold ("no clarification in the finalization")
    kept nearly every multiple-choice question from the reader since it was built (2026-10-08); the 954 answer areas the reader
    was calibrated on carried such clarifications, and none differed from its grade. A misprint or a thrown-out option is Grade
    as written work.
    A read with no saved key is checked against the rubric's correct choice; a saved key that differs holds the read back
    (KEY_DIFFERS). A confirmation saved by the first version of this rule stands for its own rubric.
  - THE READ. With the setting `choice_reader` on (off by default), a Cloud batch freezes `choice_reader_rule` and `choice_cutoff`
    (`choice.CUTOFF`, .8: the instructor's choice of the calibration over all 952 quiz multiple-choice answer areas of the term,
    where the reader graded 834 and none differed from the current grade, nor at any cutoff from .5 to .95). Before any OCR, a multiple-choice question's task (one answer
    area, operation recognize) asks OpenAI Decisions (`gpt-6-luna`, `OPENAI_API_KEY`) once:
    the original crop in grayscale, long side at most 1600 px, and one Choice over the options plus "none" and "several", in both
    option orders (`frq_cloud_calls`, step `choice`, role `choice_reader`, provider `decisions`, on the cloud lane, never the GPU
    worker's). Both orders naming one option with the lower confidence at the cutoff or more is a confident read. The question
    is `choice.ASK`, "Which answer choice did the student select?", over the bare option labels, "No answer choice is selected."
    and "More than one answer choice is selected." (requirement, 2026-10-16: "test a simple prompt ... using 'Which answer choice
    did the student select?'", then "yes, go ahead"; the earlier wording named "circled, ticked, underlined, boxed", and a tick
    beside another option misled Jev 1.14 on one paper). On the same 954 areas (`run-20261016-mc`) Decisions grades 880 at .8
    (the earlier wording 836), none differing from its current grade at any cutoff from .5 to .95, and no area both wordings
    grade is read differently. The rule name stays `choice-reader-v1`: the classification and the grade are unchanged.
  - THE GRADE. A confident read IS the grade: code ticks the correct-choice line when the letter is the rubric's correct choice, else the
    other line, through the ordinary grade validation, with the letter as the transcript, the final answer and the evidence. This
    is the instructor's exception to A FINAL ANSWER IS EVIDENCE, NEVER A GRADE, for multiple-choice questions alone. The
    read is ONE assessment: its two orders check position bias and never stand in for a second grader. Scoring then runs every
    deterministic check; the checks that ask for an independent OCR reading and a second grader are dropped by name
    (`signals.risk.waived.choice_reader`), the reported confidences decide nothing more, and everything else stands (a key that does
    not match, a blank crop, rubric conditions, an empty selection). It finalizes as a team grade does (`committee.status`
    resolved by `choice_reader`, no team convened), basis `choice_reader`, its scope naming the reader's rule, model and cutoff.
  - ANYTHING ELSE (orders that disagree, a lower confidence, "none", "several", a failed request) is kept as the `choice` step and
    the response is read and graded on the ordinary Cloud path, as if the question were not multiple choice. A desk regrade or a
    batch that starts after reading (operation grade or rescore) never reads by choice.
  - HEALTH (2026-10-09, requirement: "implement your suggestions"; a rejected key or an outage otherwise sent every
    multiple-choice response down the ordinary path unseen). A batch that read by choice carries `progress.choice_reads`
    (`jobs.choice_reads`, catalog only): responses graded by the read, those graded as written work, why (several, none,
    unsure, failed, alignment uncertain) and the failed reads' leading errors. The batch card shows one quiet line, and a
    warning while any read failed; nothing is held for them. Other batches' progress is unchanged. A 401 or 403 receipt of
    any cloud call keeps the status and the API's error type and code, never its message (OpenAI's names the key, masked).

- **Removed schemes** (requirement, 2026-09-23).
  Jev Routing, One-shot, Tag-Team, Cloud High Assurance with its escalation, the plain Nebius
  mode, optional Final Arbitrator Mode v1 and the Jev tie-breaker are gone: code, settings, UI,
  prompts and tests. `config.MODES` is `local`, `hybrid` and `cloud_standard`;
  `config.REMOVED_SCHEMES`, `historical_scheme` and `removed` name a removed batch, and preview,
  create, run and retry refuse it with the reason. PLAIN WORDS ON THE DESK AND BATCH CARDS (requirement, 2026-10-18: "make all
  of your suggested changes", pipeline review item 11): retired methods are named "Routed grading (retired)", "Two models in
  turn (retired)", "One model (retired)", "Cloud with extra checks (retired)", "Nebius cloud (retired)"; "Recalculate points"
  reads "Points counted again from the rubric"; escalations "sent for an extra check"; arbitrations "checks of
  disagreements"; a flag review's revised ruling "Rubric lines changed"; the canary "Check against your approved grades",
  shown under Batch details for a settled batch; automatic-finalization notes name no Jev, Tag-Team or One-shot; the desk's
  rubric heading reads "Rubric · proposed grade" (or "Rubric · your grade" once decided) instead of "Approved rubric".
  Technical detail stays in "Independent checks", "Batch details & controls" and "Audit and recognition details". The Review
  step's detail reads "N responses to review · N reviewed by you · N finalized automatically". Stored keys, schemes and
  receipts keep their names. What they produced stays READABLE and FINAL:
  results, automatic acceptances (`frq_one_shot_*`, `frq_tag_team_*`, team acceptances with basis
  `final_arbitrator` or `jev_tiebreak`), `frq_jev_routes`, receipts, reviews and exports, with
  their tables and migrations. Saved settings carrying their keys still load (`config.RETIRED`);
  a stored `system_one` connection refuses before anything is sent. The built-in Nebius host
  stays a connection for any Cloud role. Do not bring any of them back unless asked. Deleting
  the unreachable pre-Cloud team rules, with their tests moved to Cloud, is a separate follow-up.
  The separate JEV decision adapter (requirement, 2026-10-03) uses TypeSafe only for
  student triage and absence/Assistant source checks; it restores no grading role
  or scheme. Its key remains barred from generic model connections. Since 2026-10-08
  student triage may ask OpenAI Decisions instead (JEV TRIAGE PROVIDER); `OPENAI_API_KEY`
  may be named only by a connection to `api.openai.com` (`KEY_HOSTS`). Since 2026-11-12 whose paper a first page is asks
  Gemini Decisions (JEV STUDENT MATCH PROVIDER); `GEMINI_API_KEY` may be named only by a connection to
  `generativelanguage.googleapis.com`.

- **Cloud prompt contracts** (2026-09-22; GRADING.md "Prompt contracts"). A batch frozen
  with `primary_rule: primary-vision-v1` (`standard.freeze`) asks a primary that sees the crops
  under `primary_vision-v1` (check the transcript against the image, report `reading_issues`,
  which reach the flags through the team decision); earlier batches, text-only primaries, local
  and legacy teams keep `grade-v7` (`standard.primary_contract`). Every grader reporting final answers uses the `answer_parts`
  labels. Prompts say that problem/solution/examples may be empty and what
  `reading_context.independent_readings: 0` means. Graders still never see the answer key; the
  arbitrator and flag reviewer see it with `answer_checks` (retained on their check step) to rule
  on key flags, whose wording distinguishes `KEY_DIFFERS` from `KEY_UNREAD`. Graders are not sent
  `original_source_text`. Doubts about wholly crossed-out lines are no reading doubt
  (`models.reading_ambiguities`). A change to any of these is a new prompt version or rule key.

- **Reset batch** (Grade step; requirement, 2026-09-20) lets the same responses be graded again
  with other models or settings. It is a recorded, reversible withdrawal, never an erasure:
  `grading/batch_resets.py` clears only `frq_responses.current_result_id` for the batch's
  unreviewed CURRENT proposals and records each pointer in immutable migration-015 tables.
  Never withdraw a response that has ANY review (a saved correction included), one a later
  batch graded, or one with work under way; never delete or rewrite results, steps, calls,
  costs or acceptances for it; a restore never replaces a newer proposal. Reset responses
  are new work for the next batch (`reset_ungraded`), counted once in the progress total.
  A response whose first attempt merely FAILED is not reset work: its own batch retries it,
  so keep it out of that rule. A response whose every batch was CANCELLED before proposing anything is new work too
  (2026-09-24: nothing retries a cancelled batch and a reset has nothing to withdraw). The server checks `confirmation: "reset"`, the preview
  fingerprint and the request ID; a dialog tick is not the control. New grading migrations
  must also be added to the pre-ledger fixture in `tests/test_catalog_migrations.py`.
- A CHANGED RUBRIC INVALIDATES ITS GRADES (requirement, 2026-09-25).
  A grade (proposal or instructor decision) made under an earlier rubric revision, question
  revision or paper is `stale`: never approved, finalized or exported (`validate_task`), and NOT waiting for review. It is new work:
  `BatchResetsMixin.outdated` (stale, nothing under way) and `reset_ungraded` (only attempts under the earlier rubric) feed the
  Grade step's new responses (`regrade_responses`, counted once in progress) and the batch dialog's first choice, **New, reset or
  changed since grading**; the Review queue counts it per question as "to grade again", never as a pile or a failure. Nothing is
  withdrawn or erased. A decision made under the earlier rubric gives way to the regraded proposal (`earlier_review_status` in the
  listing, `earlier_review` on the card) and stays in the history; since 2026-09-27 the regraded grade is finalized automatically
  when every check passes it (`acceptance.REVIEW_RULE`, below), else the response waits for the instructor. The finalization must be
  confirmed again first; the regrade then starts by itself when automatic grading is on (below), else it is the instructor's.
- A role's reasoning is CHOSEN FROM FACTS, never guessed (requirement, 2026-09-20).
  `config.reasoning_choices`/`reasoning_default` derive what a connection's saved `reasoning_options` (the catalogue's
  supported/mandatory/efforts/default) allow; `role_reasoning[tier][role]` holds the instructor's choice, and
  `config.roles` attaches it as `reasoning_choice`. One possible configuration is used and stated, not offered. Keep these
  invariants: a connection without facts and a frozen batch from before them behave exactly as before (the tick decides);
  settings ALWAYS load (an unavailable choice falls back, never raises); the settings screen fetches nothing to draw itself
  (facts arrive with Browse, from shipped definitions, or by the explicit Read button); a model that never thinks is sent no
  reasoning field; the choice is part of the acceptance scope and of the estimate. `static/grading-settings-layout.js`
  mirrors the two functions for display only, and both sides are held to `tests/fixtures/grading/reasoning_cases.json`.
  `HostedEndpoint.submit`'s one-time correction of a refused "do not think" stays as the net under stale or missing facts.
  A reasoning EFFORT a host refuses as an invalid parameter is the same kind of net (requirement, 2026-09-24):
  the request is sent
  once more one effort lower among those the facts list, kept for the route (`effort_refused`, like the other lessons) only if
  answered, and the step records the choice and what was sent (`reasoning_sent`, `reasoning_note`). One step only; the saved
  choice is never rewritten. A failed arbitration or flag review never fails its response, so the batch card counts them with
  the host's reasons (`progress.reviewers_failed`).
  The rubric reviewer is no team role, so its choice has its own key (`rubric_reviewer_reasoning`, resolved by
  `config.rubric_reviewer` exactly as `roles` resolves a role; 2026-09-21).
  Like the reviewer itself it is rubric preparation: keep it out of batch
  previews and frozen profiles.
- Review opens by exam version, then question, then the exact combination of rubric
  lines the AI proposed; a group opens the ordinary detailed review of its waiting
  responses (requirement, 2026-09-20). Version comes FIRST because versions may
  scramble their questions: never pair questions across versions by number.
  `grading/review_queue.py` is a read-only view that approves, records and reveals
  nothing and names no student. A proposal hidden for a blind
  acceptance must never be sorted by its lines (the pile would show them): it goes
  to the question's one hidden group, and stale work to its own. Waiting means what
  the workflow counts as responses to review. Unclear rubric wording is ONE question
  about the problem, not one issue per student (requirement, 2026-09-21): the question's
  page gathers its waiting responses' rubric doubts by the rubric lines each names
  (`doubt_lines`; wording differs from response to response, so never match text) and
  links to the rubric finalization, where a clarification settles it. Gathering is a view:
  it changes nothing about what flags a response, and never shows a hidden proposal's doubt. The earlier one-response screen with
  its filters and tools stays behind **All responses** (`browse=all`). Since the 2026-09-26
  overhaul a version with nothing waiting folds into one line, a finished group names the next
  group or question (`continueStep`, a link only), and **Spot-check** draws automatic grades
  at random into All responses (`status=automatic&spot=10`; browser memory only, a correction
  is the ordinary review). On the review desk Alt+A submits the ordinary approval; there is
  no "I inspected…" tick (requirement, 2026-09-27): clicking
  Approve or Record manual grade is the confirmation. Digits tick rubric lines by their
  displayed number, never inside a text field.
  A batch's **Review responses** button scopes navigation with `job=<id>` and the
  queue API with `job_id` (2026-09-24): only proposals whose CURRENT result came
  from that batch enter its groups, never older flags while a regrade runs. Keep
  that scope through questions, groups, breadcrumbs and approval refreshes. Name
  the batch and link to the assignment-wide queue; the unscoped Review step and
  All responses still cover the assignment. Batch waiting counts exclude finalized,
  withdrawn and superseded results. This is a view of the existing term-owned
  batches/responses, with no change to retained proposals or instructor decisions.
  **Failed to grade** (requirement, 2026-09-23) heads the Review step (since 2026-10-03 a closed line above the review workspace): responses whose LATEST attempt failed and that have no
  proposal, which no question's queue can show. `review_queue` counts them per batch (a retry is `retry_job`, the batch's own
  action), with their recorded reasons and per-question counts, never by student; the workspace's question rows say "failed"
  and such a question is never listed as completed. A failed regrade of a response that still has a proposal is not listed: it is reviewed as usual.
  EVERY batch's failures are listed, each under the batch that made the attempt (2026-10-02): an agent's 2026-09-23 narrowing to
  the plan's newest batch let any newer batch (an automatic one for late papers, a one-response regrade, a flag review's carrier)
  hide older failures from every page while Home and the Grade step still counted them. A batch-scoped queue (`job_id`) lists its
  own only. A response a reset or a cancelled batch left without a proposal is new work (`reset_ungraded`), not a failure, as the
  workflow's and Home's `failed` count it. The Grade step names the failures under its lead and opens Earlier batches when a
  failure lives there. ONE RETRY (requirement, 2026-10-18: "make all of your suggested changes", pipeline review item 11):
  Retry of failed responses is only in Review's Failed to grade (per batch, with reasons; the same
  `/api/grading/jobs/<id>/retry`). A batch card states "N failed responses: retry in Review →" linking
  `#grading/<plan>?tab=review&open=failed`; the Grade step's failed line says "retry them under Failed to grade in Review" and
  links there. Nothing grades failures again by itself (unchanged).
  With nothing waiting, the workspace's one action opens Failed to grade (`?tab=review&open=failed` opens it too) and
  certification is a footer link; a batch's job details name
  each listed task by its question and version (`tasks[].question_id`), never by student (2026-09-24).
- **Continuous review workspace** (requirement, 2026-10-02; entry changed 2026-10-03, "go straight to the workspace").
  Opening the Review step lands on the next waiting response: the remembered one if it still waits, else the first in printed
  order (`reviewStart`). There is no overview page: the queue's progress (left, failed, final out of all) and the unfinished
  question rows are the workspace's rail; the flag review and Failed to grade are one closed line each above it (the flag review in
  the assignment-wide view only; Gradescope regrade requests moved to Return › Regrade requests, REGRADES 2026-10-07); completed questions and grade statistics are
  disclosures below it, with the links to All responses and to certification. With nothing waiting, the main area shows the
  one next step (failed responses, other batches, Grade, or certification) from the server's counts. "No review needed" does not claim an instructor
  reviewed automatically finalized work; show those two counts separately. A question's shared rubric concerns, gathered by
  the existing exact line references, are shown with it in the workspace and marked on its rail row. They are SETTLED THERE
  (2026-10-03, "fix inside review, then regrade"): "Clarify the rule here" edits the clarifications and "cannot be combined"
  pairs of the lines the concerns name and records a confirmation of that one question (`rubric-quality/clarify`,
  `clarify_question`, which also takes several questions at once as `questions`); a ticked "same" linked line on another version
  gets only the sentence added here, after its own confirmed rule (`passedOn`), in the same confirmation: every other question
  keeps its confirmed entry exactly, the overflow check, approvals and the
  suspension of exactly the changed question's automatic grades are `confirm_rubric_quality`'s own (`_record_confirmation`),
  and it is refused unless the plan's standing confirmation of the current rubrics is the one shown and no rubric check is
  waiting or running (then the link to the full finalization is offered instead). "Regrade N affected responses" then grades
  ONLY the waiting responses that raised a concern (`reason_kinds` has `rubric`; never a blind proposal) again as an ordinary
  batch (preview, then jobs, with `response_ids`), in the mode of the plan's latest batch (Cloud by default). Certified grades
  are asked about first. Neither step approves anything: the regraded responses come back as new proposals.
  FOCUS LAYOUT (requirement, 2026-10-17: the page "still seems overwhelming and not user friendly"; offered three directions, the
  layout one was built): the student's work began three quarters down the window under ten rows of controls, so each is one line:
  the heading beside its count, the breadcrumb beside the batch line, ONE ROW OF GROUP TOOLS (the group, its gallery buttons, the
  proposed lines and the shared rubric concerns as folds that take their own row only when opened, Groups & review tools), the
  response's title with Show student and Previous / Next, and Decision needed with its title beside its question. A rail row is one
  line, how many wait at its right ("to review" read out, a rubric concern a warning dot that reads "rubric concern"). Layout only
  (`grading.css` "FOCUS LAYOUT"): every control, count, reason and route is unchanged.
  Unfinished question rows open the waiting response directly. Version, question and exact proposed-line
  groups remain the hierarchy, visible together with the original scan and the ordinary review desk. Groups & review tools
  keeps the older question overview accessible. After an approval, continue through the same question's groups; changing
  questions/versions is an explicit, clearly labelled next step. Keep batch scope through every move and refresh.
  The page's step graph follows what is decided here (2026-10-11; it read "Review 0% · 0/5" beside a rail at 4 of 5 final until a
  reload): after a decision, a gallery approval, a regrade or retry sent from here, or a finished flag review, the plan is read
  again behind the workspace and only the graph is patched, never the response on screen or its focus (FRONTEND "The step graph
  follows the review").
  Later (Alt+L) is navigation only: no review, draft, rejection, or other server write. Deferred responses remain in the waiting
  count, are labelled in the queue, and can be revisited explicitly. A reason filter never treats hidden or deferred work as
  complete. The browser tab may remember its last location (plan/batch/question/group/response IDs only, at most 20 contexts);
  filters and deferred IDs are bounded page-session state. Revalidate remembered IDs against the current queue on entry.
  The workspace and session modules are views of existing term-owned plans/responses, not new retained owners. The opt-in
  `review-queue?details=review` adds each member's `reason_kinds`, derived from retained rubric doubts and current review state;
  hidden proposals expose only `blind`, and the default response stays identical. Read the whole queue on entry/boundaries and
  only the current question after a decision. The shared desk says why a response waits as ONE LINE PER CAUSE (requirement, 2026-10-11, choosing
  "One line per cause on the card"; a held card carried 3 to 5 kinds of reason, one cause often worded two or three ways): a
  cause's label, its first recorded sentence, and "+N more" with the authors when it holds more; every recorded reason stays
  verbatim, with its author, under All recorded reasons, grouped by the same causes. Display only: what is held, flagged, blocked
  or said is unchanged, and the listed sentences and their order are those the desk always listed. The causes are `finalization`,
  `blank`, `disagreement`, `reading`, `rubric`, `checks`, `grader` and `other` (`review_queue.reason_causes`, a view of the
  response's retained result; nothing is kept). A cause is read from what the result retains (the finalization's reasons, the
  transcript's reading notes, each assessment's rubric doubts, reading issues and review reasons, the team's decision) and from the
  sentences' NAMES in the modules that say them, never from their words: a sentence added to `risk.assess` or
  `recognition.review_state` gets a name and a cause (a test fails until it has both), a required assessment that is missing, failed
  or not independent is a check and not a disagreement, and a sentence nothing places is `other`, never dropped or reworded. The
  opt-in `responses/<rid>?details=review` adds `reason_causes` for a proposal that is not hidden; a hidden proposal's body is the
  same whatever is asked, and the default response stays identical. The desk draws the causes only while they hold exactly the
  sentences it lists, each once, and the plain list otherwise. It links exact rubric
  IDs to their displayed lines, and shows the score beside Approve & next. It never invents evidence coordinates or rewrites a
  model's uncertainty as fact. All decisions use the existing explicit, version-checked, append-only review endpoint, or the
  proposal gallery below, which saves through it response by response.
  **Proposal gallery** (requirement, 2026-10-03: "see all, approve the rest"). "See all N in this group" shows every response of
  one exact proposed-line group (never the hidden group) with its crops and the lines once (`GET .../proposal-gallery`, in
  `answer_groups.py`; the review queue decides the group and its members, `review_queue` stays read-only). Only plain proposals
  and proposals held by a grading check alone may be approved together, with their reasons shown on the tile; a response with a
  reading to check, a rubric concern, graders that disagree or a finalization flag (the answer groups' own test), one with a
  decision the instructor started, a trial acceptance or anything no longer waiting is shown greyed, to be opened alone. The
  BLANK ANSWERS ARE APPROVED HERE TOO (requirement, 2026-10-11: "Blank answers in one gallery"; 122 of the 429 held grades of
  the four graded assignments were "the reader found no handwriting", each confirmed alone in the desk, and the instructor
  changed 6, all on one quiz where the reader had missed real writing). A response held for a blank crop is the one reading to
  check that may be approved together, when everything that holds it follows from the empty reading (`blank_answer`, from the
  stored result alone): every one of its areas was read as empty and nothing was transcribed, its crops are registered, the
  proposal is a zero with a line ticked, the graders agree, and neither a rubric concern nor the finalization flags it. Credit
  proposed beside an empty area, a partly empty response, a crop that may be misaligned and a disagreement are greyed and
  A CLICK POPS A CROP OUT (requirement, 2026-10-17: "its supposed to pop out to full size, not zoom in place"): on the desk a click on
  a crop opens its full-size dialog, as Full screen does; the inline reader's Fit, 100%, − and + still zoom in place. CROPS ASK AGAIN (requirement, 2026-10-17: the gallery's crops "don't show unless i click on them"; asked for while Faculty
  restarted, they stayed broken until the click's dialog asked again): a crop that fails to load is asked for again by itself after 1,
  3 and 8 s (`grading-crops.js` `retryWhenBroken`, a `retry` number on the same address). opened alone. NOTHING CONFIRMS A BLANK BUT THE INSTRUCTOR'S LOOK, and it remains `RESERVED` from every model. The tile shows each
  ORIGINAL CROP ALONE (requirement, 2026-10-17: "it shouldn't show all of the models' commentary and only show the original scan
  crop, not both original and high contrast to save space"; until then each crop was shown twice, as scanned and in high
  contrast, with what holds it): no models' reasons on an approvable tile, a greyed tile still says why it is opened alone, and
  the high-contrast view is the desk's (Open alone, Enhanced views). The server still lists `contrast`. `?only=blank` (and `only` on the approval) lists just the group's blank answers, with the tick "I looked at every crop
  shown here"; the review is saved with the comment "Confirmed blank in its gallery after looking at each crop." and the event
  counts them (`blank`). Replayed on those 122: 93 may be approved together, the 6 changed ones among them, each crop shown.
  JEV BLANK ANSWERS (requirement, 2026-10-15: "I want to see if jev can judge accurately that a problem has been left blank so
  that a 0 credit score can stand without needing my review", then, shown the measurement, "build it with that rule";
  `grading/jev_blank.py`, rule `jev-blank-answers-v1`, JEV "JEV PICTURES"). The one exception to "nothing confirms a blank but the
  instructor's look": a zero held ONLY because the reader found every area empty (`jev_blank.eligible`: state `ocr_review`, every
  area read empty and nothing transcribed, a zero with no empty selection, every block a blank-crop block,
  no grader disagreement, no rubric concern) is shown to Jev, all its crops in one request (purpose `grading-blank-answer`, owned
  by the response); at `CUTOFF` .8 or above the empty reading is settled the way a ruling settles what it was shown (the blocks,
  reasons and state it held stay on the record under `signals.jev_blank.settled`), and the zero goes to the ordinary acceptance
  gate, finalized on the basis `jev_blank` ("left blank (reader and Jev)"). Below .8, a failed, refused or uncertain check, or
  Jev not set up, it waits as before. Last step of scoring, after every ruling, the rubric finalization and the empty-selection
  hold. When the reader found every crop empty and the arranged transcript has no lines, the automatic
  `NOTHING_TRANSCRIBED` note is the same blank hold, not a separate handwriting ambiguity that prevents Jev's check.
  The note stays in the transcript and review reasons; any additional reading doubt still blocks this automatic path.
  A GRADER'S REQUEST ON AN EMPTY ANSWER (requirement, 2026-10-26: "there were too many blanks that should have auto resolved"; then
  "build a b c and d"): on an answer every area of which was read empty and nothing transcribed, a grader's request for a look can
  only be about that empty answer, so it is part of this check (`jev_blank.asked_about_the_empty_answer`: the sentences that say a
  grader asked, and each grader's own request in its words, under its name or not); state `both` qualifies only when such a request
  explains it. A grader's reading issue, a rubric doubt, a disagreement and every other hold still keep it. A WHOLE-PAGE ANSWER AREA
  (`jev_blank.WHOLE_PAGE`, width and height at least 0.8 of the page, no privacy mask) whose crop did not align is shown to Jev as its
  whole scanned page, checked against its recorded checksum (`whole_pages` on the receipt), since alignment fails on pages covered in
  writing (every Exam 2 Question 23 page, 0.28 to 0.54 against 0.55). Replayed on the
  32 blank zeros of Exam 2, which the instructor all kept at 0, none reached Jev and 25 now would. AN UNALIGNED CROP (instructor,
  2026-11-06: "yes", after asking why a blank Quiz 7 answer was not cleared and being shown the replay) of any other area is shown
  to Jev as it was cut, its "may not show the whole response" hold being part of this check: a page left nearly empty aligns
  poorly too, though the shift found is right (that answer 0.48). Replayed read-only on the 55 answers since 2026-10-15 held for
  nothing else (54 Quiz 6 Question 4, 0.42 to 0.55), each checked by eye with the contrast raised: Jev cleared 25 at .8, every one a
  correct zero (15 empty, 8 the problem crossed out, 2 a tick on its number; it calls a large X "blank" at up to .96), none with
  work, though none of the 55 had work to miss; a crop 6% of the page taller, asked about the problem by number, cleared 18.
  Measured that day on 272 graded answers, every answer Jev scored .5 or above checked by eye with the contrast raised: at .9
  and above, 79 calls, every one an empty answer area (3 held erased pencil the instructor had scored 0, 3 showed 1.5 from the
  instructor's "Adjustment for calculator part" line, not writing); between .5 and .9, real marks. Replayed on the 122 confirmed
  one by one: 96 meet the gallery's test and 77 of those reach .9. Work written outside the answer areas is seen by neither.
  CUTOFF .8 (requirement, 2026-10-31: "I think .8 is enough confidence to clear a blank/no credit", after 82 blank Quiz 6
  problem 4 answers scored .28 to .89, the three lowest blank pages with page 1's work showing through from the back). Until
  then .9. On the 2026-10-15 measurement, .8 to .9 held 7 answers: 6 empty by eye and 1 with 1.5 points (Quiz 2 q4-C, approved 0
  with the instructor's "Adjustment to calculator part" line, like the three at .9 and above), so no writing. The rule name is unchanged; results already stored keep
  the cutoff they record (`signals.jev_blank.cutoff`) and are not settled again.
  A response whose reading waits is no longer told "It is no longer waiting for review" (it was, until 2026-10-11).
  The
  instructor sets aside or opens what does not fit, ticks "I looked at every response shown" (a native checkbox that clears
  whenever what is shown changes; no key ticks it or approves) and approves the rest with the group's own lines
  (`POST .../proposal-gallery/approve`): under the lifecycle lock the lines, each response's version and current result and its
  eligibility are checked again and anything changed saves nothing; then each response gets its own `save_review` (status
  approved, at most 100 per action) and one `proposal_group_approved` event. The ones set aside are reviewed alone next.
- Keep physical transcription, mathematical interpretation, approved rubric
  applications and Decimal scoring separate. Preserve visibly incorrect work.
  Missing evidence, malformed outputs and ambiguous rules cannot become automatic
  approvals. Raw provider receipts are private; ordinary logs contain safe errors.
- Questions/solutions/rubrics are immutable course-wide assignment-version
  resources; each term plan pins its own revisions. A pin leaves its plan only
  when a drafted quiz is rebuilt before anything was printed back, graded or
  edited (QUIZZES "REBUILDING AN APPROVED QUIZ"); the version it replaced then has
  no template slot and takes no question again (`rebuilt_documents`). Responses use permanent
  section/enrollment memberships. Human review is append-only: a review row is never
  changed; a regrade the instructor starts may replace which decision counts (`acceptance.REVIEW_RULE`). Rescore reuses saved human selections/adjustments only when semantics
  match; source/rubric changes are revalidated before approval and export.
- Gradescope evaluation CSVs can have nine leading header columns but six leading
  footer columns. `grading/gradescope_import.py` aligns explicit item ranges, honors
  exported scoring mode and checks the question total. Preserve rubric IDs, wording,
  fractional/zero points and source bytes; ignore student rows when interpreting.
  CSVs omit group/selection settings: keep the review warning. `wording_rules`
  flags only wording about other items or caps; descriptive words (`but`, `either`,
  `no work`) must not set `exceptions`, and `except`/`maximum`/`at most`/`no more than` count only
  for a cap (an amount) or an exception naming a line, item, part or R-id (`rule-parser-v4`); `not both` counts only beside other lines ("this item or the next, but not both", "not both R3 and R4"), never where it describes the work ("x ≥ 0 and y ≥ 0 are not both stated"; `rule-parser-v5`, 2026-09-28). Rubric revisions are immutable, so `rubrics.conditional` honours
  the importer's own mark (a line's description copied into `exceptions`) only where the CURRENT
  rule still finds the wording; a condition someone wrote is always evaluated. `review_rules.condition_checks`
  checks each response: only narrowly recognized unselected "only if" restrictions are inactive;
  unknown exceptions/caps still require judgment. `condition_handling: structured` is an explicit
  instructor assertion in an approved rubric revision with actual scorer-enforced rules, never
  a compiler assertion. Recalculating points refreshes flags without inference. Bump the parser provenance label when it changes. Model compilation
  cannot replace exported scoring facts. Positive follow-through credits are not
  repeated negative penalties. DEDUCTIONS IN A POSITIVE RUBRIC (requirement, 2026-10-25: the coordinator added -1 lines "so the
  rubrics aren't wholly positive scoring", e.g. Exam 2's "Corner Points are not written as ordered pairs"): a positive rubric's
  line may be worth from minus the question's points to its points (`rubrics.validate`); a checked deduction is subtracted from
  the credits and the rubric's floor and ceiling (0 and the question's points unless set) bound the score, exactly as Gradescope's
  positive scoring with a negative item, so such a rubric imports, sends and matches as any (GRADESCOPE). A deducting rubric's
  lines stay at 0 or below. Explicit rubric source uploads use course-scoped
  `document.archive`; they do not use the unclassified scanner intake gate and do
  not approve any rubric.
- Rubric-first setup (`grading/rubric_uploads.py`, `static/grading-upload.js`)
  creates draft questions directly from Gradescope CSVs, uploaded or read straight from Gradescope by Import from Gradescope
  (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE", 2026-10-24: the same upload of the export's own CSV); never require manual
  question definitions or invented placeholder regions before uploading them.
  Question numbers come from filenames, maxima and scoring from exported metadata; a misspelt "question" or "problem" just
  before the number still names it (2026-10-25: Exam 2 Version C's "8_Quesiton_25.csv"), a number after any other word does not.
  Unknown printed numbers require review, never a substitution of internal indices.
  Upload comes first because those printed numbers tell template OCR which problem
  areas to locate and crop. The tile graph and page navigation share `guideSteps`
  and `pipelineMarkup`; preserve identical steps, labels, order and destinations.
  Rubric uploads and the Assignment Templates are ONE first step, Rubrics &
  Templates (requirement, 2026-09-20): complete when every version has its rubrics,
  its confirmed template, and every rubric confirmed for the rubric reviewer (below). Nothing is approved there: the rubrics are checked,
  clarified and approved in the third step (below). The page marks the step being
  viewed, even when already complete.
  Setup layout (requirement, 2026-09-22) uses assignment cards as the entry, with
  All assignments to return; do not restore the duplicate assignment dropdown.
  The first three steps share a searchable, filterable question navigator and
  show one question at a time. This is browser state, not a new data owner or an
  approval. Keep panels mounted while switching questions so unsaved finalization
  clarifications and exclusions survive. Uploads and templates share the first
  step; Answer areas shows the blank page and highlighted regions beside the
  existing review action; step three offers an individual answer-key editor as
  well as the whole-key editor. The finalization dialog opens on the instructor's
  review action or an explicit dialog link, never just because polling finishes.
  Its confirmation still validates and submits every question, including hidden
  panels. All server revision, template, location and rubric gates remain.
  The graph is DESIGN-LANGUAGE §4's numbered step graph (requirement, 2026-10-03; it
  replaced the wireframe cubes of 2026-09-22): numbered serif steps in ruled
  columns, each with its label and the word for its state under it (Done, Failed,
  In progress, Not started, a waiting step's own status, or its progress
  fraction); done steps slate, failures brick, untouched steps faint, and the step
  on screen under a 3px rust rule. Status is in the words; colour only repeats them.
  Selecting a step or having available work does not start it. Do not restore
  separate status badges or a legend; accessible names and hover titles retain
  status. Keep count-based progress and keyboard navigation. This display does not
  change workflow states.
  Unfinished graph nodes show count-based percentages/counts (`workflowProgress`),
  including partial human reviews. Keep percentages derived from saved work,
  never elapsed-time guesses. Server `workflow.progress` counts current responses
  once; new eligible/stale work and active regrades stay unfinished. Shared template page reads
  count once per template, and failed receipts are not successful progress.
  Course-wide question/rubric revisions and the term pin commit atomically with a
  term-owned immutable upload receipt. Originals use verified Inbox storage before
  that transaction. Exact retries/reuploads preserve existing work; changed files
  for a pinned question require explicit revision import. Drafts with no regions
  cannot grade even if no template is uploaded yet. Multiple files have individual
  outcomes and stable retry IDs; failures never hide successful imports.
- Assignment entry and templates (requirement, 2026-09-20): scan association alone
  never puts a new assignment on Grading. Its associated scan must pass its review
  first (since 2026-09-28 papers still flagged on a reviewed scan wait in the Inbox
  on their own, and `grading_entry` counts that scan as cleared and as pending). Existing grades remain accessible
  while later arrivals wait in the Inbox. Reuse the overview; never clear a gate
  or infer a student/page identity to make an assignment visible.
  SET UP GRADING NOW (requirement, 2026-10-02: "Yes, add 'Set up grading now'"): the instructor's explicit action makes
  the plan before any paper, so rubrics, blank templates, answer areas and the answer key are finished before scan day,
  as a quiz's approval does (QUIZZES). It is Create assignment's Paper step (a version typed there is registered
  first), the Exams hub's Grading tile and the Assignments card's ⋯ menu, each only while the term's exam record has
  no plan, each then opening Rubrics & Templates. `POST /api/grading/assignments` (`set_up_grading`) takes `{event_id}`
  or `{assignment_id, term_id, section_ids?}`; under the lifecycle lock, in one transaction, it reuses the term's exam
  record or makes it by the Exams page's own `ExamService._create` (for `section_ids`, else the sections the
  assessment's seating charts and print jobs name in that term, else every section of the term, since an automatic
  association naming a section outside the record is refused), inserts the plan and writes `assignment_created`
  `{event_id, before_papers: true}` with the request's actor. Then, outside the lock and only for a plan made now, each
  current registered PDF version is imported as the assignment's blank template (`templates.import_version`, which
  reuses one per version), so the assignment finder can match an unlabeled scan of it. Current: not stamps-only, and
  the versions the term's current print jobs hand out, or, with nothing printed in the term yet, every version not
  retired there (every print job of it superseded or cancelled). A version that cannot be imported, whatever the
  failure, is named in the answer (`set_up.templates`) and the plan stands; a PDF registered later is prepared in
  Rubrics & Templates (the success notice says so when the plan has no blank template). An exam
  record that already has a plan returns it (200, `set_up.created` false) and makes nothing, no template included; a plan an associated
  scan made stays waiting for that scan's review. `grading_entry` lists such a plan as `ready`, marked
  `set_up_before_papers` (additive), as it does `set_up_by_quiz_draft`; a later association, the instructor's or the
  finder's, reuses its exam record and plan. OWNERS: the plan belongs to the term's exam record (course_terms), the
  templates to the course-wide assignment (HIERARCHY). Scan association alone still never puts an assignment on Grading.
  "Finish the scan checks (N)" (on Home's In
  progress) counts only assignments with a scan still in the Inbox
  (`grading_entry.pending_scans`) and leads to Collect's waiting scans (I2, ASSIGNMENT-WORKSPACE "SCANS IN THEIR
  CHECKS") when every waiting scan is one known assignment's, else to that scan (`waiting_href`); an assignment whose
  scans were all deleted waits for nothing there (2026-09-26). `GradingService.overview` `waiting` (read-only) still names
  those assignments, each with a link to its scan, and its entries carry additive `assignment_id` and `term_id`
  (2026-10-18; the Grading index that listed them under its cards is retired).
  Papers waiting for the instructor's VERSION decision (no seat, or seat and
  pages disagree: `exam_versions.DECIDE`) are counted per assignment
  (`workflow.version_decisions`, from `VersionUndecided`) and named in a notice on the
  assignment and its card that opens Assignment versions (`?tab=rubrics&focus=versions`)
  (since
  2026-09-26 they also keep the first step and Grade unfinished, "Decide versions", below).
  Scan review is NOT a grading step. The graph has SIX stages (instructor,
  2026-09-20): Rubrics & Templates, Answer areas, Key & Rubric Finalization,
  Grade, Review and Certify (the sixth renamed from Export to "Certification and Export" by the instructor,
  2026-09-26, and named by its tab's word, Certify, since 2026-10-18; the rule headings below keep the old name as their ID;
  its tab key stays `export`, and it completes only with a current certification, below). Older `tab=templates` and `rubric-quality` links
  open the step that now holds them.
  Later arrivals show a separate Inbox notice linking to the scan's Inbox page;
  awaiting/failed inspections do not reopen completed grading. New blocked or
  missing papers stay outside grading progress until eligible; already-created
  responses still invalidate normally when their reviewed source changes.
  The old `tab=papers` remains an unnumbered Scan records utility, never the next
  grading task; each scan reads its file, pages and papers (additive `workflow.scans`
  `filename`, the first file uploaded, `files`, how many, and `papers`, the printed
  copies its pages were matched to). Before any paper of
  an assignment reaches grading (no response, nothing new, queued, failed or waiting for a
  version decision, no reviewed paper and no scan cleared by the Inbox: a drafted quiz not
  given yet), the Grade step WAITS FOR PAPERS (2026-10-02 review): it keeps its name, place
  and done rule, and its action, the card's button and Home's become "Upload scans"
  (Collect's own upload, `#assignments/<a>/<t>?area=collect&upload=1`, when the plan names its assignment, I1; else
  `#inbox?upload=1`), or "Finish the scan checks" (Collect's waiting scans, I2) when one waits there; the card's
  title still opens the assignment. Scan records makes that action its one primary, never
  "Continue to Grade", and counts no student as missing a scanned paper until a scan of it
  clears the Inbox. THE CERTIFY STEP WAITS FOR A LATE SCAN (requirement, 2026-10-11: "align
  the Grading card's certify wording too"; ASSIGNMENT-WORKSPACE "READY TO CERTIFY MEANS
  NOTHING IS STILL COMING"): with every grade reviewed, no current certification and a
  linked scan still in its scan checks (`awaiting_review`), the last step does the same
  as the Grade step above. It keeps its name, place and done rule; its status reads
  "Waiting for a scan", and its action, the card's button and every "Next" line drawn from
  the graph (Home's In progress, a step's "Next step" callout)
  become "Finish the scan checks" ("… still waits for scan checks"; 2026-10-18 words), opening Collect's waiting scans
  (I2) when the plan read names its assignment and term, otherwise the first scan, or the Inbox, since certification refuses
  until it is through (`inbox_pending`); the card's title still opens Certify. Upload scans opens Collect's upload (I1)
  the same way, falling back to `#inbox?upload=1`. It read "Next: certify the grades" before. A current certification is not
  reopened by this, and a review still open comes first. Browser only (`guideSteps`).
  The Review step's own way on (`reviewNext`, "No responses need review") follows the
  same day ("align the Review step's 'Continue to certification' line too"): in that state
  its line says a later scan still waits and its button is "Finish the scan checks",
  opening them, in place of "Continue to Certify". The button at the head of All
  responses, shown whatever is left to review, does too ("change the All responses
  'Continue to certification' button too"): in that state it reads "Finish the scan
  checks" and opens them, the reason its title; otherwise it is "Continue to
  Certify" (a quiet go-link inside an assignment, ASSIGNMENT-WORKSPACE "A FINISHED TAB NEVER PUSHES THE NEXT ONE"). ONE READING of the rule serves the three (`lateScan`,
  grading-guide.js: `awaiting_review` and no current certification); the step graph also
  asks that the review be done, the other two that nothing come before them.
  Preserve all source, entry and review
  checks on the server.
  **Assignment Templates** share the first step with the rubric uploads and need
  only the uploaded rubrics (never the rubric finalization, which comes after the
  answer areas). `grading/template_workflow.py`
  projects one slot per printed/exam version. Existing generated versions reuse
  their verified unstamped canonical PDF through `AssignmentTemplateService.import_version`
  and the bounded converter; stamps-only versions always require uploaded templates.
  The reference and its `source_version_id` remain course-wide. The term plan's
  append-only `frq_template_reviews` retains exact selections and acknowledgment:
  digital templates may reduce performance; blank scans made with the same scanner
  as student papers work best. No new location check runs before that handoff.
  Existing checks/confirmations retain their history. A changed reference resets
  existing answer areas only after explicit confirmation; rubric pins stay intact.
- Rubrics drafted from a blank template (`grading/rubric_generation.py`, migration 020; requirement, 2026-09-23) are the
  alternative to Gradescope CSVs on Rubrics & Templates, for ANY assignment (exam, quiz, homework, worksheet, lab, project
  handout), never only exams (survey prompt v2, writer prompt v5, rule `rubric-draft-v5`, below at THE RUBRIC PROMPTS):
  problems without whole-number labels are numbered by position with their printed heading as the title, points need not
  be printed, and open responses get a complete-response line. MULTIPLE CHOICE IS PASS/FAIL ON THE CHOICE ALONE (instructor,
  2026-09-25): writer v4 and all-versions writer v2 draft
  such a problem (or part) as exactly "Correct choice: $$(c)$$ $$...$$" for every point and "No answer or completely incorrect"
  (in a deducting draft since 2026-09-28, "Wrong choice, more than one choice, or no choice", below at THE RUBRIC PROMPTS),
  with no line crediting, requiring or mentioning work; each version names its own correct option. The label and content in
  $$ $$ side by side make the proposed key accept either (`answers.key_from_rubric`), and nothing marks work as required.
  `frq_rubric_generations` belongs to the TERM grading plan and
  references the course-wide template and printed document/exam version it read; its calls are `frq_cloud_calls` owned by
  `rubric_generation_id` under the ordinary cloud retry/replay rules. The Rubric drafting and review model (the rubric reviewer; requirement, 2026-09-23: one model does both jobs, image-capable) surveys
  the whole blank template once, then writes each problem alone in its own request (on a subscription connection the survey may look
  closer at a page and each writer reads the course's context: DRAFTING TOOLS, below). A draft creates NOTHING: only the instructor's
  explicit, editable acceptance makes each kept problem an ordinary draft question through `create_draft_question` (the rubric
  upload's own path: retained source, compiler `model-drafted-v1`, term pin, `frq_rubric_uploads` receipt), independently per
  question. Drafted rubrics are approved only by the mandatory rubric finalization, like uploaded ones. Since 2026-10-11 the
  acceptance is also the instructor's confirmation for the rubric review of the questions it CREATES, and each one's key is read
  without being asked once its answer area is confirmed (ACCEPTING A DRAFT READIES IT, below); the answer key stays a
  proposal until saved, by the instructor and by nothing else. Templates may be chosen before rubrics exist; confirming them still needs every version's rubrics.
  The generation menu offers positive scoring (start at zero, add credit) and negative scoring (start at full credit, deduct)
  by the instructor's request of 2026-09-24. `mode` belongs to the term-owned generation's immutable `input_json`, participates
  in its request fingerprint, and controls the writer, line validation, editable draft and accepted rubric/source. Older
  generations without a mode remain positive and keep their original rule and replayable requests. The model never chooses
  or changes this mode. Writer v3 explicitly checks every line for ambiguity before returning it; unresolved policy choices
  are named for the instructor, and the ordinary rubric finalization still follows.
- ADAPTING ONE VERSION'S RUBRICS TO THE OTHERS (requirement, 2026-09-25).
  **Adapt Version A's rubrics to other versions** on the
  Rubrics panel (`static/grading-rubric-adapt.js`) queues one `frq_rubric_generations` row per ticked version (`kind: adapt`, rule
  `rubric-adapt-v1`, no migration), read from that version's blank template, by the same Rubric drafting and review model. Its
  immutable input snapshots the reference questions as sent, with their question and rubric IDs. The model surveys the template,
  PAIRS the versions' problems in one request (`rubric_adapt_match-v1`; by what they ask, never by printed number, since versions
  may reorder problems and parts; the reference blank pages are attached where a reference statement is unknown), then adapts
  each paired rubric alone (`rubric_adapt-v1`): every reference line rewritten by ID, values and answers changed, criteria kept.
  Points are never the model's: each line keeps its reference line's. The result is a draft reviewed like one, each line beside
  the line it adapts. Only the instructor's acceptance creates a question (`create_draft_question`, compiler `model-adapted-v1`),
  whose rules are the reference rubric's (`adapted_rules`: same IDs, groups, follow-through, dependencies, exclusions and
  conditions; cleared lines dropped with their references; added lines new). A question whose reference rubric changed since is
  refused: adapt again. Copying the instructor's line comments is an explicit tick, append-only, never over a line with comment
  history. Nothing is approved: the acceptance confirms each question it creates for the rubric review, after the copied comments
  are saved (ACCEPTING A DRAFT READIES IT, below), and the rubric check and the mandatory finalization follow. Versions stay independent
  after acceptance: a later edit to Version A never propagates.
- ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11, chosen from a measured list: on the paper path they read the same
  model-drafted rubric three times, to accept the draft, to press Confirm all for rubric review and to confirm the finalization,
  with Run rubric check and the key's reading each a click between). Three reads become two (`grading/rubric_generation.py`; no
  migration, no new route).
  ACCEPTANCE CONFIRMS FOR REVIEW. `_after_acceptance`, the one tail of the three acceptances (a draft, every version at once, an
  adaptation), runs after the drafted clarifications or copied comments are saved, since a confirmation names the comments digest,
  and records `frq_rubric_review_ready` with actor `draft-acceptance` through `confirm_rubric_review`, only for the questions the
  acceptance CREATED: never a rubric it replaced (asked again, as after any rubric change), and for a request sent again
  (`already_imported`) only a question never confirmed that still holds the rubric and exactly the comments the acceptance gave
  it. It never fails an acceptance; what is refused stays for the page's button. Uploaded CSVs keep the explicit confirmation.
  THE KEY IS READ WITHOUT BEING ASKED, ONCE ITS ANSWER AREA IS CONFIRMED. Nothing is read at acceptance: a created question has
  no confirmed area yet, and a reading without the picture of the printed problem would be a poorer proposal than the button's
  (the instructor's condition: no loss of grading quality). When `confirm_location` or `confirm_all_locations` confirms the area
  of a question whose CURRENT confirmation for review has actor `draft-acceptance`, with no saved key, no key its rubric proposes
  and no reading yet, `after_answer_areas` queues the ORDINARY reading for it (`_queue_key_readings`, the route's own path,
  conditions and refusals: `answer-key-read-v2` with the picture of the confirmed area, the problem's text and the stored worked
  solution), request ID `area-confirmed:<location confirmation>:<question>`, event actor `automatic`. One confirmation asks once;
  a refusal (no reviewer model, nothing to read the problem from) never fails the confirmation and leaves the question to the
  button, as do an uploaded rubric and a rubric the instructor confirmed for review themselves. The readings are queued BEFORE the
  check is asked for, so the last confirmed area never starts a check ahead of the key it just asked for; the reading's end asks
  again. The key is saved as any proposal is (ANSWER KEYS SAVE THEMSELVES, 2026-10-22: by the worker, or Save this key and Save
  N answer keys), and the acceptance dialogs have no key field.
  THE RUBRIC CHECK STARTS BY ITSELF WHEN THE PLAN IS READY. `rubric_review_wait(db, aid, status, questions)` ONLY READS and says
  where the check stands, for the questions a check would ask (those whose finalization does not stand): `yours` (none has a
  CURRENT confirmation with actor `draft-acceptance`: uploaded rubrics, a quiz's or makeup's approval and rubrics the instructor
  confirmed again themselves keep Run rubric check or their own request), `finalized`, `checking` (a check is pending or running),
  `checked` (one exists for the current input: finished, failed or cancelled, it is the instructor's to review or re-run),
  `waiting` with one sentence (a version without rubrics; a rubric unconfirmed; a rubric draft pending or running; an answer area
  unconfirmed, since the snapshot's `rubric_reference` carries the printed crop only then; a key still to be read or being read; a
  key proposal, the rubric's or the latest reading's, not saved), or `ready`. `request_rubric_review_when_ready(aid)` queues the
  check only on `ready`, request ID `draft-review:<plan>:<first 40 hex of the input digest>` (one state asks once), and returns ''
  or the sentence; it writes nothing otherwise. It is called, never failing its caller (`rubric_review_follows`), after an
  acceptance, after confirmed answer areas (`after_answer_areas`, after their readings are queued), `save_answer_key`, a key
  reading that finished, and by the `rubric-review-ready` route after the instructor's own confirmation; NEVER inside
  `confirm_rubric_review`: a quiz's and a makeup's approval keep their own check requests. Confirming the finalization stays the
  instructor's.
  THE PAGE SAYS SO. The acceptance's answer carries `readied`, `keys_to_read` (its questions whose key is read once their area is
  confirmed) and `review` (the sentence, '' once the check was queued; none of them when it created nothing); the draft reviews
  say creating a question confirms its rubric for review, and their toast ends with that sentence (`afterAcceptance`). The plan
  read (`assignment`) carries `rubric_review_waits`, that sentence, beside `rubric_quality` ONLY in state `waiting`: every other
  plan's body is unchanged, and a plan no acceptance readied costs one indexed read. Key & Rubric Finalization's lead
  (`finalizationProgress`) says it instead of "Run the rubric check: it is required before grading."; a check that runs, failed or
  waits for review says its own state. Do not widen this to uploaded rubrics or to the finalization's confirmation.
- MAKEUP VERSIONS (requirement, 2026-10-03: excused absences should lead to "the option to generate a makeup that is based on the
  original assignment/rubrics"). ANSWER KEYS (2026-10-11): a makeup's revision reads each problem's key once as a quiz draft's
  does, against the original's CURRENT rules with the adapted wording (`makeup_rules`; an original whose rubric changed gives no
  key and a warning); the window shows it under Answer and its summary says how many keys approving saves; an approval sent with
  `save_keys` creates the makeup's questions with them before the rubric review is requested. Asked, they chose: the makeup is a new VERSION of the same assignment (graded in the same plan,
  counted in the same gradebook column), the model writes PARALLEL problems (the same skills, structure, points and steps, new
  numbers and contexts), and each rubric is the original's adapted line by line. `grading/makeup_drafts.py`, `static/makeup-drafts.js`.
  - Draft makeup version… (the Absences page's Makeups view, the assignment card) reads GET `/api/grading/assignments/<plan>/makeups`
    and creates with POST there: the version it is based on (a template slot with rubrics; `reference_problems` snapshots its CURRENT
    questions and rubrics with their IDs), the next letter (`_makeup_letter`; a plan without lettered versions is refused), and the
    instructor's optional instructions. One open makeup draft per plan at a time, so the letter is still the next at approval.
    One ready to approve, or failed with nothing to approve, is a needs-you item of its assignment (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED").
  - THE REVIEW (requirement, 2026-10-05: "this is not user friendly at all, walls of text are never helpful"; DESIGN-LANGUAGE
    principle 13). The window (1120 px wide, only its body scrolls) leads with the version, its pages as thumbnails and a needs-you
    count of what to check. Then one row per problem: the problem as printed (an SVG crop of its page from the LaTeX layout's
    fractions, `problemCrop`, opening the page) beside its checks (orange-marked: layout space, the page check's issue, rubric
    warnings, a rubric that could not be adapted) and the first sentence of the writer's paragraph about it ("Problem N: …") and of
    its rubric's notes; the whole wording and the rubric lines with the solution fold. A rubric line shows its ID and points and,
    under it, "Same wording as Version A" or what Version A's line said. `reviewNotes` sorts the stored notes and warnings (nothing
    is re-asked; a warning of no known shape stays at the top). Approve sits beside "Or ask for changes". The sorting, the crop
    (from the problem's top to the next problem on its page less a heading's room, or to just under its last printed line: the
    footer's place moved with the class's reserved bottom) and the row pieces are `static/review-notes.js`, shared with the quiz
    draft page (QUIZZES "THE REVIEW").
    READ AT A GLANCE (requirement, 2026-10-06: "do the same for the makeup draft window", after the quiz draft page's). Beside each
    problem the makeup's answer comes first ("Answer", `answerMarkup`, shared with the quiz page), then the notes labelled "Makeup
    writer" and "Rubric writer"; the room warning asks "Enough room?". The window's footer keeps its two ways on in view: "Ask for
    changes" and "Approve Version X…" each bring their form into view (they sat under every problem). The Approve column leads
    with what approving does as its form stands (`approveSummary`: the version it adds, the copies it prints or "prints nothing",
    how many ticked students it places on which sitting, or that students book their own time on their card, and that its rubrics
    still need finalizing), and a sitting's date reads as the campus date (`sittingText`, "Tue, Oct 6"). Display only: the
    approval request and its rules are unchanged.
  - A makeup draft IS a quiz draft (`quiz_drafts`, course-wide authoring tied to the plan's term; `input_json.makeup` names the plan,
    the source version, the references, every version's statements to avoid and, when the plan came from a quiz approval, the
    original version's own fragments and title line). No table was added. Its revisions, revise/stop/scrap, compiled files and
    approval use the quiz-draft routes and worker (`run_quiz_revision` hands it to `run_makeup_revision`); the quiz drafts list leaves
    it out. The writer (`makeup_write-v2`, quiz_write's layout and LaTeX rules) returns ONE version; `structure` refuses another
    count of problems, other points, or (with the original's fragments) other kinds or parts. Compile, fix rounds (`_quiz_fixed` with
    the makeup's writer), one check of the printed pages beside the original's and its one fix round are a quiz draft's. Then each
    problem's rubric is the original problem's adapted (`rubric_adapt-v1`, `adapted_rubric`: every line once by its ID, the points the
    original's); the pair is known by construction (problem n is the original's n-th), so nothing is paired. An unchanged problem
    with the same original rubric and no grading feedback keeps the earlier revision's adaptation.
    LEFTOVER VALUES (requirement, 2026-11-05, of Quiz 5 Version E's Problem 2, "R3, R4, R6, R7 are word for word Version A's, though they
    name values": "it copies the numerics from the original versions that conflict with the makeup version's actual answers, is this an
    issue?"). A value of the original left in a line would be: graders apply a line as written. But that finding fired on every unchanged
    line with a digit or any mathematics, and read back on the term's six makeups it was 21 false alarms and no value left over in 153
    lines; R3 there is the union rule, the same on every version. `adaptation_warnings` (`leftover_values`, also for Adapt on Rubrics &
    Templates) now names a line and its value only when the value is in the original's line and the problem it came from and this
    version's statement, worked solutions and final answers do not have it: a number or symbol they lack, an original's final answer
    this version's answers do not give (a correct choice's letter, printed on both), or, on a line left word for word, a whole
    expression they lack (`x(x-3)` where this version's answer is `x(x-6)`, when both happen to have a 3). Replayed: no finding on the 153
    lines, and with each of the 106 rewritten lines put back to the original's wording 101 are found. Not found: a context word with no
    mathematics ("Number of tickets"), and a list of symbols this version has in another arrangement (`x, y, s_3`). Findings only, as
    before, and Key & Rubric Finalization still reads each line against its key. A revision drafted earlier stored the old sentence: its
    window reads it again on the same rule (`_makeup_leftovers_now`, read only), since Quizzes 3 and 5 were waiting on it.
  - APPROVING (POST `/api/quiz-drafts/<draft>/approve`, `confirmation: "approve"`; `approve_makeup_draft`) names the copies (0 to
    500), the makeup sitting (an existing makeup sitting of the exam record, a new one by label/date/time/place, or none), the
    students to place on it (memberships of the exam's sections) and, for a CERTIFIED plan, `certified_ok` (adding a version brings
    it back as Certify again; no grade changes; refused without it). The worker (`_carry_out_makeup`) refuses when an original rubric
    changed since it was adapted or the letter was taken meanwhile, then: adds the letter (`change_added_version`), registers the
    approved PDF as the assignment's version of that letter, creates one question per problem on it (compiler
    `model-drafted-makeup-v1`, `adapted_from` naming the original question and rubric; rules from `adapted_rules`: the original's
    IDs, groups, follow-through, dependencies, exclusions and conditions), leaving every other question, pin and grade untouched;
    imports its template and confirms it with the answer areas from the LaTeX layout ONLY when every other version's template is
    already confirmed (else a note says to confirm it in Rubrics & Templates, so no other version's template or area is touched);
    confirms only the makeup's rubrics for the rubric review and requests the review, which asks only the makeup's questions
    while the other versions' finalization stands (A CHECK ASKS ONLY WHAT DOES NOT STAND; requirement, 2026-10-04, ABSENCES "MAKEUP
    BOOKING"; request `makeup-review:<revision>`; when it cannot be requested, another review running or a version not confirmed
    for review, a note says so, and for a quiz the makeup booking worker requests it once nothing runs; the mandatory finalization
    stays the instructor's); saves a new makeup sitting through
    `ExamService.save_sitting`; makes one unnamed term print job (request ID
    `makeup:<revision>:<letter>`) attached to the exam record and the sitting; and places the ticked students on the sitting
    (`update_participant`, sitting only, after a roster sync when one is not on the record yet). Every step reuses what an earlier
    attempt made. Makeup papers are stamped on their own version's document, so `printed_document_version` decides their version.
    KEEP THEIR SITTING (requirement, 2026-10-06, after an approval with every owed student ticked moved Gina off the makeup sitting
    she had agreed to, onto another student's): in the approval window a student already on a makeup sitting (the makeups list's
    `scheduled`) starts unticked, their row says where they are ("on <sitting>; tick to move them", or "already on this sitting"),
    and the summary says how many ticked students it moves off another makeup sitting. Ticking one still moves them.
    A CLEAR APPROVAL WINDOW (requirement, 2026-11-01, of its sitting and student lists: "confusing and not user friendly"): a sitting
    reads once (`sittingText`, absence-makeups.js: its name unless only "Makeup", the campus day, the time, the room; a booking
    card's label already carries the day and time) with how many students are on it; each student is one aligned row of name,
    section and one short status (`studentStatus`: "Booked <sitting>", "Already on this sitting" (ticked and fixed), "Moves here
    from <sitting>" once ticked, the booking card's state, "Held: waiting on …", or "Absence not decided yet"). With booking cards
    on, "None: students book their own time on their card" comes first and is chosen; the students are then a list to read, with
    no boxes, until a sitting is chosen. NO COPIES TO ASK FOR (requirement, 2026-11-05, of the field "Extra unnamed copies": "I don't
    think I need this. If I need such a copy, can't I just print it out using what's already available in the print menus?"): with
    booking cards on the window has no copies field and sends `copies` 0, since each student's copy prints by itself (ABSENCES
    "COPIES"); a spare comes from the assignment's Print copies or a job's Print more copies…. Without cards "Copies to print"
    stays, the only copies such a sitting gets. A certified plan says so in a warning box of its own, and its button reads "Approve Version <letter> and bring <title> back to Certify": pressing it is `certified_ok`
    (a separate box to tick first was missed the same day, and Approve seemed to do nothing).
    A REFUSED APPROVAL SAYS WHY (requirement, 2026-11-01, "I approved it, but it brought me right back to here": the worker refused
    Quiz 6's Version E because Version A's Question 2 rubric changed after drafting, `approved` leaves failed approvals out, and the
    window redrew its form with no word): the latest revision's last approval, when failed, is shown with its reason at the top and
    beside Approve; for a rubric changed since drafting, "Adapt the rubrics again" asks for a new revision (`based_on` the latest,
    feedback naming the reason), which is reviewed and approved as any other.
    CURRENT RUBRICS (same day: revision 2 of Quiz 6's Version E was refused again, because every revision adapted the draft's
    saved `makeup.references`, the rubrics as they were when the makeup was requested): each revision reads the original's
    questions and rubrics as they are now (`reference_problems` of the draft's source slot), so a changed rubric is adapted from its
    new lines and the revision's `source.rubric_id` is the current one; an unchanged rubric keeps its earlier adaptation as before.
    The saved copy stays only when the original's questions themselves changed (then the approval is still refused).
  - AUTOMATIC DRAFT (requirement, 2026-10-04, ABSENCES "MAKEUP BOOKING"): the worker drafts a QUIZ's makeup by itself the first time
    an excused absence owes one (request ID `auto-makeup:<plan>`, the default source version, no instructions), once per plan; the
    instructor approves it as any makeup draft. With booking cards on, approval need print nothing and place no one: each booked
    student gets a copy with their name for their sitting, as soon as they book, or as soon as the version is approved when they
    booked first (ABSENCES "COPIES", 2026-10-12).
  - ON GRADESCOPE BY ITSELF (requirement, 2026-10-09: "When the makeup version is approved (ie, everything is settled up to the point
    grading begins), it should automatically create that version in all sections' gradescopes"): once the makeup's approval is
    carried out and the version is settled (`makeup_settled`: every question of that letter has an approved rubric, answer areas
    confirmed for it and a rubric finalization that stands for it, what a grading batch requires), the Gradescope sender adds it
    to every section's LINKED set that lacks it, once (GRADESCOPE "A VERSION THE SET LACKS"). Not built: instructor line comments
    are not copied to the makeup's lines. For the assistant, the GET is a read, the
    create route is unnamed (an Approve card), and the quiz-draft routes keep their rules (`quiz_drafts.revise` auto as a content
    draft, `quiz_drafts.approve` an Approve card with its confirmation); no tier was widened.
- DR COPIES (requirement, 2026-11-10: "I just scanned the DR quizzes, they likely need their own template in the assignment/grading
  pipeline to be graded correctly, but they can still use the same rubrics"; `grading/dr_copies.py`). The DR center prints the
  accessible Word copy of one version of a drafted quiz (QUIZZES "ACCESSIBLE COPY"): the same problems, one to a page, on plain
  paper without labels, so its scanned paper has other pages than the printed version (four sides for Quiz 7's two) and none of
  the version's answer areas lie on it. Until then such a paper could not be graded at all: a drafted quiz's questions sit on
  each version's own document, and a paper without labels was registered under a plain label sheet.
  - THE COPY IS A DOCUMENT OF ITS OWN (`DocumentService.register_dr_copy`; owner the course-wide assignment, as every version): a
    label-only document like the stamps-only one (`faculty-stamps-only`, one label, the other sides by position), labelled "DR copy
    · <version's label>", whose `compiler_json.dr_copy` names by reference the version's document it copies (`version_id`), its
    blank (`template_id`), its sides (`pages`), the approved revision and the Word file's sha256 and rule. It is made from the
    term's approved quiz (rule `quiz-draft-v1`): the first version a screen reader can read, the one Send to AIM uploads, and the
    document holding that letter's questions in the plan now. A changed Word file, revision or version document gets a new copy.
    A quiz with no readable version, or an assignment not drafted in Faculty, has none.
  - ITS PAPERS ARE THAT VERSION'S PAPERS wherever a paper's document is asked (`exam_workflow.grading.PAPER_DOCUMENT`:
    `_paper_version`, `paper_documents`, the version prefetch, `printed_document_version`): the document settles the version
    (A PAPER'S STAMPS DECIDE ITS VERSION, no seat, comparison or question to anyone), the version's own questions and rubrics
    grade it, and gradebook, certification and batches count it as that version's. Nothing is copied: no question, rubric, key or
    finalization exists twice, and a ruling on the version reaches its DR papers. The copy takes no rubric upload slot and no
    template slot (`rubric_upload_documents` leaves it out). The manual scores page lays nothing out on a copy and refuses it as
    before; only assisted grading's `source` accepts it (`rubric["dr_copies"]`).
  - ITS OWN BLANK. `dr_copy` makes the Word file again exactly as it was made for AIM (the file's sha256 equals the upload
    receipt's while the builder's rule stands) and renders it with the template converter (`accessible_quiz.for_rendering` drops
    the "1 page" pandoc's own properties claim under Word's name) into a blank labelled "DR copy · <label>". It must come out one
    page per problem, or no copy is made, saying so. The rendering is NOT the DR center's print: Word there sets the file in
    another font. Measured 2026-11-10 on the first scan (20 pages, Quizzes 6 and 7): its pages aligned with ours at .12 to .40
    against the .55 pass mark, and the best-aligning page was the right one on only 13 of 20; a real print aligned with another
    student's at .55 to .86. So nothing is aligned with this blank. It gives the copy's sides and tells which way up a sheet is
    (20 of 20 sheets fed upside down were turned upright, none of 80 upright sheets).
  - ITS OWN ANSWER AREAS (`dr_copy_areas`, rule `dr-copy-sheets-v1`): the question with the k-th printed number of its version is
    the WHOLE of sheet k, cut without alignment (`extraction.extract(sheet=True)`: the crop is the scanned sheet, `registered`
    with `sheet: true`, turned upright where the blank says so, and no viewing aid subtracts the blank). No area is read, drawn or
    confirmed for a copy, and the version's own areas, blank and location reviews are untouched. Sheet 1's top margin
    (`NAME_BAND`, .10 of the page: the copy prints nothing there and its directions send the name and UIN there) is masked from
    hosted readers, as a name line above a printed heading stays out of an ordinary area. A response's source says `dr_copy`
    (the copy's document and the rule) and names the copy's blank. Refused, each paper on its own: a paper whose reviewed pages
    are not the copy's sides ("has 3 pages, but the DR copy has 2"; empty sides past the last are left out as EMPTY BACKS), and a
    version whose questions are not one to a sheet (a question without its printed number, another count).
  - WHEN IT IS MADE. By registering (DOCUMENTS "EXAMS PRINTED WITHOUT LABELS" › DR COPIES: a drafted quiz's paper without labels
    is registered under its DR copy, made then if needed), before a found scan's sides are asked (`associate_found`), and by the
    worker (`dr_copy_one`, the last of the pipeline's automatic steps): one current-term quiz per turn whose Word file went up to
    AIM (an `aim_sends` upload of its approval, read only) and has no copy yet, each looked at again after 15 minutes. So a
    scan's papers are counted at the copy's sides (`UnlabeledService.paper_sides`) before anyone is asked. Rendering runs outside
    the lifecycle lock. No migration; no outside service is reached.
  - STAMPS (requirement, 2026-11-10: "Let us start adding the data matrix stamps to the accessible versions that get uploaded to
    AIM (note that they shouldn't be student specific because the DR center is likely to give them to the wrong students)").
    Every Word file `quiz_accessible_copy` hands out (the DR quizzes tab's link, Send to AIM's upload, a DR request's upload card)
    prints its page codes: on each page that page's own Data Matrix, the peel-off labels' 10 mm mark (`STAMP_PROFILE`
    `faculty-corners-10mm-v1`, chosen for paper printed and scanned elsewhere) at the two corners that layout puts it
    (`stamp_places`), as pictures anchored to the page itself, so they print there whatever font and line breaks the words get,
    and marked decorative, so a screen reader passes over them (`accessible_quiz.build(stamps=)`, rule
    `faculty-accessible-stamps-v1`; without `stamps` the file is byte for byte what it was). The codes are NO STUDENT'S:
    `dr_stamped_copy` registers, on the first request, a second document of the copy ("DR copy · <label> · page codes",
    generator `faculty-dr-copy`, as many pages as the copy has, each stamped, `dr_copy.stamps`; its canonical is blank pages,
    since its paper is the Word file) and ONE job of `COPIES` (20) unnamed copies for the term and the assessment's sections
    (request ID `unlabeled:dr-copy:<document>`: a registration, never a printing, `origins`), renders it at once
    (`build_now`) so its stamps are issued, and puts copy 1's codes in the file. The same bytes come back for every later
    request, so one file serves every DR quiz taker and every upload. `create_batch` refuses any other job of a DR copy.
    What comes back is ordinary stamped paper printed for no student: the codes file the scan under the quiz by themselves and
    number its pages; every paper after the first on copy 1's codes takes an unused copy of the job (QUIZZES "PHOTOCOPIED
    STAMPS"; a 21st paper waits for the instructor, as there); each copy is named from the name written on its page 1 (QUIZZES
    "NAMES ON UNNAMED STAMPED COPIES", both corner codes whited out before the band is read, Jev as there); a paper is whole at
    the copy's pages and is assembled at the scan's review; and it is graded as above (ITS PAPERS ARE THAT VERSION'S PAPERS,
    ITS OWN ANSWER AREAS: whole sheets, still without alignment). CHECKED BEFORE IT GOES OUT (`_dr_stamps_check`, once per file
    and process): the stamped file rendered here must show, on each page, that page's code at both places and no other code.
    A file whose codes cannot be made or checked goes out WITHOUT them, as it always did, with a logged warning: the DR
    center's copy is never held up for its codes, and its papers are then found as papers without labels (WHEN IT IS MADE).
    The accessible copy's GET therefore writes on its first request (a document, a job, never anything outside Faculty); the
    worker's `dr_copy_one` makes nothing for a quiz that has either kind of copy. NOT VERIFIED HERE: Word's own opening and
    printing of the stamped file (ml-box has no Word); the instructor opens the first one in Word before it is sent.
  - KNOWN LIMITS, not built: for a copy WITHOUT page codes, sheets scanned out of order are not noticed by Faculty (nothing
    tells its pages apart; the graders see the printed problem on the sheet), and a scan holding two quizzes' papers is filed
    under one by the instructor while Jev files the other's first pages (DOCUMENTS "JEV MIXED SCANS"). The Gradescope sender
    skips a DR paper, saying its pages are not the Gradescope template's.
- EVERY VERSION DRAFTED AT ONCE (requirement, 2026-09-25).
  Assignment Templates has ONE draft
  button (`static/grading-rubric-versions.js`); a version slot no longer drafts alone (its own earlier drafts and adaptations stay
  reviewable there). One `frq_rubric_generations` row covers the plan (`exam_version` `'*'`, `kind: versions`, rule
  `rubric-draft-versions-v2`, no migration; its input names every version's template). Each version is surveyed with the ordinary
  survey, ONE request groups the problems across versions (`rubric_group_versions-v1`: by what they ask, never by number; skipped
  for a single version), then ONE request per problem writes all its versions (`rubric_write_versions-v2`): a single list of lines,
  each with ONE point value and a wording per version; `family_rubric` refuses a line without every version's wording. Review shows
  a problem's versions side by side with one points field per line, and acceptance (`_accept_versions`) gives every created version
  the same line IDs (R1.. by line) and points; a wording cleared for one version leaves that line out of that version only. Compiler
  `model-drafted-versions-v1` names the problem (`family`) and version. One draft runs per plan at a time. Nothing is approved:
  the rubric review and mandatory finalization follow per question. Keep one request per problem (not one for the whole
  assignment).
  A DRAFT MAY REPLACE AN EXISTING QUESTION'S RUBRIC (requirement, 2026-09-27).
  In the review, a version whose question already exists (same document, version and printed number)
  offers "Replace its current rubric with this draft", unticked, one box per version, with a Select all at the top that ticks
  or unticks every open box, to create and to replace alike (asked for the same day); its number is read-only. Acceptance (`_replace_with_draft`) makes a NEW rubric revision of that question from the draft's lines as edited
  (compiler `draft-replaced-v1` with `replaces` and the draft's record under `based_on`, so `rubric_quality.origins` still finds
  the problem and the draft never lists it as created); the question, statement, solution and answer key stay. It refuses a rubric
  changed since the review opened (`replaces`), a draft worth other points, and create-plus-replace; an identical rubric makes no
  revision; the source is retained with an `frq_rubric_uploads` receipt keyed by the request, so a repeat replaces nothing twice.
  As after any rubric change, the question's grades are stale and regraded, its confirmation for review and finalization are asked
  again, and a finalization that stood suspends only its automatic acceptance; its line comments are cleared (append-only: they
  were about the old lines, which the new ones' IDs reuse), and a confirmed answer area is carried by receipt as the line editor
  carries it.
- Rubric finalization (`grading/rubric_quality.py`, migrations 011–012; requirement, 2026-09-20) is MANDATORY:
  no question is graded, in any mode, without a confirmation that stands for its current rubric revision
  (the whole plan's until 2026-09-28, each question's since), and there is no switch (`frq_rubric_quality_options.enabled` stays as unused history).
  It is owned by the TERM grading plan, pins exact
  course-wide rubric revisions and lives in the third step beside the answer key, after the answer areas.
  BUILT-IN exclusions are decided by rule alone (`builtin_exclusions`, label `BUILTIN` in the confirmed
  input): a line worth the whole question (all the credit, or in a deducting rubric all of the deduction)
  and a zero line whose WHOLE wording says no answer/completely incorrect each exclude every other line
  of their question. They need no model, cannot be edited away or switched off, and are separate from the
  pairs a model suggests. Keep the wording rule a whole-line match that speaks for the WHOLE question
  ("No Answers for either parts a or b" does; "Part B: No Answer or Completely Incorrect" and "No Matrix
  Given or Completely Incorrect" are one part's zero beside another part's credit).
  THE POINTS DECIDE WHEN A FINALIZATION IS DONE (requirement, 2026-09-21; `heaviest_combination`): when the lines of a
  question (or of one of its parts) add up to more than it is worth, some of them MUST be alternatives, and the finalization
  has to determine which. "Resolved" is a fact, not an opinion: no selection the built-in rules, the rubric's own structure
  and the confirmed pairs still allow may exceed the question's value (positive rubrics; a deducting rubric has no such bound).
  The reviewer is told the rule, and told ONCE more exactly which lines its pairs still allowed (`unresolved` in its payload);
  what it leaves open is reported, never a failed check. A confirmation is REFUSED while any question is open, the dialog says
  which lines as pairs are ticked (`heaviestCombination`, held with the server's rule to `tests/fixtures/grading/overflow_cases.json`;
  the server decides), and a confirmation from before the rule that leaves a question open needs the instructor again for that
  question: only ITS responses are held back, and it is left out of every batch until it is resolved (2026-09-28). Parts need no rule of their own: their
  values add up to the question's, so an open part shows as an open question. Pair lines because they are alternatives, never
  merely to get under the maximum. The search is exact; one that reaches its limit is UNDECIDED and blocks confirmation like an
  open question, never "resolved" (both sides, review fixes 2026-09-22).
  A TICKED FULL-CREDIT LINE IS READ AS THAT LINE ALONE (requirement, 2026-09-21; `full_credit_alone`, frozen per batch as
  profile `full_credit_rule` and named in the scope).
  This is the ONE place code changes a model's
  ticks, and only because the step lines beside a ticked full-credit line say nothing it does not: they are unticked where a
  grader's answer is validated, what the model ticked stays on the record (`normalized`, `subsumed_by`, the raw answer) and on
  the review card, and the score is identical. Never when the no-credit line is ticked too (a contradiction to look at),
  never if the score would change, never for a batch without the rule. FULL MARKS BY THE STEP LINES are read the same way
  (requirement, 2026-09-21; rule `full-credit-alone-v2`, `full_marks_by_steps`).
  Only where the ticked lines all earn points, none excludes another, and they add up to EXACTLY
  the question's value: the full-credit line is ticked with the steps' evidence (`read_from`), the steps are unticked with
  `subsumed_by`, and the record says `route: steps`. Never for a sum above the value (lines that exclude each other), a ticked
  zero line, the no-credit line, a deducting rubric, or a batch frozen under `full-credit-alone-v1`, which finishes under v1.
  A PART'S FULL-CREDIT LINE is read the same way first (requirement, 2026-09-21; rule `full-credit-alone-v3`, `part_lines`,
  `part_full_credit_alone`).
  Nothing is guessed
  about what a part's line is: lines whose wording BEGINS "Part A:" are a part, every part has ONE line worth more than its
  others, those lines add up to EXACTLY the question's value, and the confirmed finalization already keeps each apart from
  every other line of its part; anything less gives no part lines. The part's other ticked lines are unticked with
  `subsumed_by`, the record says `normalized.parts`, and both parts' lines may then be read as the question's line by the
  steps rule. Never beside that part's zero line, the question's full- or no-credit line, or if the score would change: ticks
  whose double counting only the question's ceiling hid stay as written and flagged. MEASURED SMALL:
  of 26 selections breaking a confirmed exclusion in that batch 13 were two ALTERNATIVES ticked together, which must keep
  going to a third vote, and 3 were a part's line; do not widen this to alternatives. A batch frozen under v2 finishes under v2.
  v4 (instructor-requested arbitration follow-up, 2026-09-21) qualifies the step-promotion rule above: a full numeric total
  becomes the whole-question full-credit item only with `full_credit_eligible: true` from the validated model assessment.
  False or missing preserves the steps and the explicit reason an unmet criterion excludes full credit. Never infer that
  eligibility from points or parse a prose reason to guess it. Already-ticked full-credit and part-line subsumption stays;
  v1/v2/v3 batches keep their frozen behavior. The boolean is response-assessment evidence, not a rubric change or score.
  Under v4, full marks reached with `full_credit_eligible: false` add a review reason (ticks and score unchanged). In EVERY
  version a zero or negative line ticked beside the full-credit line, or a selection that cannot be scored, is left as written.
  A MISTAKE IN THE RUBRIC ITSELF is the instructor's to correct (2026-09-21).
  `correct_rubric_line` (POST
  `.../questions/<qid>/rubric-lines`, "Correct wording" under each question of the finalization step) writes a NEW immutable
  revision: the same lines, IDs, points and rules with ONE line's wording replaced, the uploaded file and the line's
  `original_source_text` kept, compiler `instructor-corrected` naming what it corrects. It is never an AI clarification (those
  stay the overlay), and Faculty changes nothing on Gradescope. What a confirmed finalization decided does not depend on a
  line's wording, so it is CARRIED to the corrected revision as a new immutable review (`policy.carried`, event
  `rubric_quality_carried`) and that revision is approved with it, except the corrected line's own clarification, which was
  written against the old wording and is dropped (`clarification_dropped`); unless the wording changes what the built-in rules find,
  when the finalization is asked for again like after any change. That question's acceptance permissions are suspended and its
  earlier proposals are stale, as after any rubric change (only that question's: see ONE QUESTION'S RUBRIC below). This is the ONE exception to "changed rubric inputs require a fresh
  check": do not widen it to points, IDs, added or removed lines, or imported revisions.
  RUBRIC LINES PREPARED BEFORE THE REVIEWER (requirement, 2026-09-24).
  On Rubrics & Templates each line's wording and points are editable, with a
  comment per line (`static/grading-rubric-edit.js`, POST `.../questions/<qid>/rubric-edits`, `edit_rubric`). Changed wording or
  points are a NEW immutable revision (compiler `instructor-edited` listing every change; IDs, groups and rules kept; the uploaded
  file and each `original_source_text` kept); graders read it and scoring uses it; Faculty changes nothing on Gradescope. Nothing
  of a confirmed finalization is carried (changed inputs need the reviewer and the instructor again); a confirmed answer area is
  carried by receipt, as for a corrected line. Comments and the confirmation for review belong to the TERM grading plan, append-only
  (`frq_rubric_line_comments`, latest per line, empty clears; `frq_rubric_review_ready`, naming the exact revision and comments
  digest; migration 021). A confirmation's `actor` says which act of the instructor it was: `workspace` (the page's button), or
  `draft-acceptance`, the acceptance of a model's draft that created the question (2026-10-11). Both are the same confirmation: a
  changed line, a replaced rubric or a changed comment reopens that question alone. The rubric check refuses to start until every
  question is confirmed (POST `.../rubric-review-ready`; after it the check of a plan with accepted drafts may start by itself,
  below),
  except questions whose confirmed finalization STANDS (below): what it covered includes the rubric and its comments, so
  that covers finalizations from before this confirmation and one carried to a line corrected in step three. The check's input names
  a question's comments only where some exist, so plans without comments keep their fingerprints. Comments and confirmations for
  review leave the plan's version alone (they have their own fingerprint); a changed line bumps it, as any rubric revision does. The reviewer (`rubric_quality-v6`)
  treats each comment as the instructor's guidance and carries it into that line's clarification; where it leaves one empty the
  comment itself is proposed (`comment_fallback`), and the review window shows each comment beside its line. Graders see comments
  only through the clarifications the instructor confirms. The question list marks a question done only once it is confirmed for
  review, not when its rubric exists. Unsaved wording, points and comments are browser memory that survives the step re-rendering
  after another question's save (never saved; dropped when the revision it was typed on changes, and the form says so), and
  **Confirm all for rubric review** refuses while any form holds them. **Save all** (2026-09-26) posts each question's draft in
  printed order through the ordinary rubric-edits route with that question's own version, revision and checksum and stops at
  the first refusal; leaving the page with drafts asks first. Saving where the question's confirmed finalization stands first asks **Change a
  finalized rubric?**, naming what the save undoes and, for a changed line, pointing to Correct wording for a typo; changed lines
  on an approved revision already out of the finalization ask **Change an approved rubric?** without the finalization's claims
  or that link; closing either sends nothing (2026-10-02, display only: the server's rules are unchanged). **Edit interpretation** changes only how lines combine
  (follow-through, dependencies, structured rules; its JSON refuses changed wording or points of existing lines), and a revision it
  or **Import revision** makes that replaces a rubric a confirmed finalization covered suspends that question's automatic
  acceptance, as the line editor does (2026-09-24; one question only since 2026-09-25). Either KEEPS A CONFIRMED ANSWER AREA
  (requirement, 2026-10-11: confirming it again repeated what was already confirmed) by the same append-only receipt the line
  editor writes (`carry_location`, the one helper every carry uses), when the new revision has the same rubric maximum: the
  template, pages, areas and printed number live in the question revision, which neither touches, and the maximum is the one
  thing of the rubric the printed points were compared with. Another maximum is asked again; the `rubric_imported` event says
  which (`location_carried_forward`). Only the area is carried, never the confirmation for review or the finalization.
  ONE QUESTION'S RUBRIC HOLDS BACK ONLY ITS OWN GRADES (requirement, 2026-09-25).
  The finalization is still ONE mandatory
  confirmation that approves every rubric, but what it decided
  is judged question by question (`rubric_quality_status['standing']`, `finalization_standing`), and since 2026-09-28 so is every new batch
  (instructor: when one question's rubric changes, the others keep grading): a batch takes only the questions whose finalization
  stands with nothing left open (`JobsMixin.batch_finalization`), freezes that one confirmation, names the others (`held_questions`)
  and is refused only when no question is left: while the plan's confirmation is
  current every question it covers stands under it, exactly as before; otherwise a question STANDS while its input
  (`question_input`: its entry of `rubric_quality_context`, rubric revision and comments included) is what the plan's latest
  confirmation covered. Confirmations record those inputs (`policy.inputs`, also on a carried one); an older one is read from
  its check's input ONLY where that reproduces its fingerprint exactly (`confirmation_record`), else nothing stands and the plan
  waits as before. A grade stays final (export, `final_decisions`, the listing, `accept_result`) while the confirmation its
  graders read (`scope.rubric_quality`) gave its question and rubric revision the same GUIDANCE as the one standing
  (`same_finalization`, `guidance_key`: rules label, rubric revision, pairs, built-in exclusions, clarifications, everything a
  grader or the flags read). Import revision, Edit interpretation, the line editor and a corrected line suspend only their own
  question (`suspend_acceptance(..., question_ids=[qid])`), and only where its finalization stood for the replaced revision;
  queueing a check suspends nothing; a confirmation suspends exactly the acceptances and permissions whose question's guidance it
  changed (`keep=`), and says how many were kept, changed and suspended. The dialog starts a kept question from its confirmed
  entry, with blank clarifications left blank, never from the reviewer's new suggestions (shown beside it). Suspensions stay permanent and append-only; nothing is reinstated.
  ONE QUESTION PER REQUEST (requirement, 2026-09-21): the reviewer never sees two questions in one context. EXCEPT a problem's
  VERSIONS (requirement, 2026-09-25):
  a check of a plan with two or more versions is queued under `VERSIONS_SCHEME`
  (`versions-v1`, in the check's input, never in what a confirmation covers). Its questions are grouped into problems, each with
  its copies on the other versions: by how the rubrics were made (`origins`: `adapted_from`, a drafted-together `family`, followed
  back through `based_on`), and where that leaves any question unaccounted for, by ONE text request (`rubric_quality_group-v1`,
  step `rubric_quality:group`) that groups by what the questions ask and their rubric lines, never by printed number, keeping
  those links (refused twice: unlinked questions are checked alone and the summary says so). Then ONE request per PROBLEM
  (`rubric_quality_versions-v1`, step `rubric_quality:problem:<first question>`) holds every version's rubric, key, comments
  and printed problem (on a subscription connection the reviewer may also look closer at the blank pages and read the course's
  context: DRAFTING TOOLS, below). Parallel lines get the same clarification and exclusions; an instructor comment on one version's line
  is carried to the parallel lines of the others; differences between the versions' RUBRICS are reported, never fixed, as
  `consistency_issues` (the dialog's "Consistency across versions"). The answer stays per question, so confirmation, grading
  and the record are unchanged; the dialog lists each problem's versions together (`families`). Still never the whole exam in
  one request. A plan without versions, and a check queued before the scheme, are checked one question per request as below.
  A CHECK ASKS ONLY WHAT DOES NOT STAND (requirement, 2026-10-05: Quiz 4's makeup Version E sent all five versions' rubrics to the
  reviewer again, though A to D's finalization stood and only E had none; asked whether the reviewer must still see A to D for
  consistent critiques: yes). Where the plan's latest confirmation stands for some questions with nothing left open
  (`finalization_standing`) and not for others, those are SETTLED (`settled_entries`): the check's input carries their confirmed
  entries as `settled` (never in any fingerprint; `confirmation_record` drops it) and only the others are asked. Under the versions
  scheme a problem is still ONE request with every version side by side, rubric, key, comments and printed pages included, the
  settled ones marked with their confirmed clarifications and exclusions as decided policy and the reference for consistency
  (`rubric_quality_added-v1`, `review_added`): the reviewer answers line guidance for the other versions only (`validate_problem`
  with `asked`; an echoed settled version is ignored) and may still name any version in suspected errors, consistency issues and
  parallel lines, which are reported and change nothing settled. A problem with nothing to ask is not asked, and the reviewer's
  earlier links of its lines are kept (`settled.links`). Without versions only the other questions are asked. The result gives
  each settled question its confirmed entry and names them (`settled`), so the window (each marked "not asked again", no model
  suggestion shown), Resolve concerns and the ONE confirmation that approves every rubric work unchanged: a settled question keeps
  its guidance and its automatic grades unless the instructor edits it. Every question standing (an explicit re-check) or none is
  a full check, as before. The status names what a check would ask only then (`to_check`), and the panel's button says it
  ("Check Version E", else "Check the N changed rubrics").
  A CHECK ASKS ONLY WHAT CHANGED (requirement, 2026-10-25: on Exam 2, changing Version B's R7_16 from 1 to 0.5, the one concern left
  after two rounds of Resolve concerns, asked for a check of all 24 rubrics again, which would have replaced the 65 concerns already
  resolved). Before a confirmation stands for a question, the plan's latest finished check is where the instructor's work on it
  stands: once that check is no longer current, each question whose input (`question_input`) is still what it read, with an entry
  for the current rubric revision and nothing left open, is CARRIED (`carried_entries`, only where the check's input reproduces its
  own fingerprint) and settled with that check's entry exactly as a standing confirmation's entry is; a standing confirmation
  wins. The status names them (`carried`), the check's input marks them (`settled.carried`) with the concerns that check left open
  on carried questions alone, outside every problem asked again (a fix made there is done and not listed again; one skipped or
  undone is a decision), and the result lists those concerns first and names the carried questions (`carried`, the window's
  "Unchanged since the last check, not asked again"). Carrying confirms nothing and suspends nothing: the one confirmation still
  approves every rubric, and a resolution may still change a carried question (only a confirmed settled one stays settled).
  A SETTLED VERSION STAYS SETTLED (requirement, 2026-10-15: resolving the concerns of Quiz 2's makeup Version E, several of them
  naming Versions A to D, rewrote A to D's clarifications, and the confirmation sent 612 of their graded answers back to review).
  Resolve concerns of a check with settled questions still asks each named problem with every version, but changes only the asked
  questions' entries (`run_rubric_resolution`): a settled question keeps its confirmed entry whatever the reviewer answers for it,
  and a concern naming only settled questions stays listed, unresolved, with the note that it changed none of them. To change a
  settled version's guidance the instructor edits that rubric or clarifies it (`clarify_question`), which suspends as always.
  GUIDANCE PUT BACK (same day): a team grade a finalization suspended because its question's guidance changed counts again when a
  later finalization gives the question back the guidance it was graded under, while it is still the current result, unreviewed,
  and held back by nothing else (`reinstate_guidance_suspensions`, a reinstatement row per suspension; the confirmation's event
  and answer carry `reinstated` only when there are some).
  `run_rubric_quality` asks EVERY question at once (requirement, 2026-09-21;
  `PARALLEL` is
  now only a ceiling on threads, the reviewer connection's own `max_inflight` in `cloud_call` is what paces the requests, and only
  the sandbox crops of the printed problems are bounded, `CROPS`), each call owned by the check under step `rubric_quality:<question>` (`CheckHost`: who owns the requests, where the printed
  problems are read and what the reviewer may call; a quiz revision runs the same check before there is a plan, its calls owned by
  the revision under the same step names, `quiz_drafts.DraftCheckHost`, QUIZZES "A REVISION"), so a later turn after a
  wait or a restart sends only what is missing, and a refused answer is asked once more for that question
  alone; if that re-ask fails, the first valid answer stands and the failure is on its receipt. Versions are compared only under `VERSIONS_SCHEME`, one problem at a time (above); do not bring a whole-exam
  request back for that. A reviewer that reads images is shown each problem as PRINTED on the confirmed blank exam
  (`rubric_problem_images`: the template page cropped in the document sandbox like a response; course
  material, never student work);
  an unavailable page or sandbox sends the question without its picture and never fails the
  mandatory check. The prompt asks for a clarification ONLY where two readings of real work would change the
  selection or the credit. v5 tests hypothetical eligibility boundaries
  without a clarification quota: evidence location, conflicting final answers, presentation, transposed matrices, follow-through
  and work requirements. Unsupported credit-changing policy choices are reported for the instructor, not guessed. The configured
  `rubric_reviewer` (otherwise Standard's primary) reads each question's rubric with its problem, solution and
  answer key (`rubric_reference`, NOT part of what a confirmation covers: a corrected key never reopens it) and
  proposes clarified wording per line, further exclusive pairs and suspected errors through the existing
  cloud lane. The instructor edits all of it; ONE confirmation records the immutable policy and approves
  every rubric of the check. A drafted quiz's approval may be that confirmation (requirement, 2026-10-11; QUIZZES "APPROVAL"): only
  on the instructor's explicit tick in the approval window, only for the check carried from the draft with no decision open and
  no points overflow, and recorded by the same `confirm_rubric_quality` → `_record_confirmation`, so standing, the overflow rule,
  the rubric approvals and what a batch may take are unchanged. Nothing weaker than an explicit tick confirms a finalization. Pairs are symmetric and never transitively expanded.
  RESOLVING THE LISTED CONCERNS (requirement, 2026-09-25: the reviewer asked again to resolve the concerns at the top "by further augmenting the comments it added to each rubric line";
  asked, they chose that it may change the exclusions too, and that only the
  problems the concerns name are asked). Each suspected error and
  consistency finding has its own collapsed comment box; **Resolve concerns** (POST `.../rubric-quality/resolve` with `responses`
  `[{concern, response}]`, numbered as `concern_list` lists them; `resolve_rubric_concerns`, rule `RESOLVE`, now `resolve-concerns-v3`
  with prompt `rubric_quality_resolve-v3`, below; it began as v2) queues a check of the SAME rubric inputs whose input is the completed check's plus `resolves`:
  the concerns as listed, each commented one with its comment, the window's clarifications and exclusions (unconfirmed edits
  included) and the problems to ask. No migration: it is a `frq_rubric_quality_checks` row, owned like any check. Under v2 only COMMENTED
  concerns were sent, each with its own comment (v3 sends every concern of the named problems), and only the problems they name are asked, each ONE request with all its versions,
  starting from the window's guidance and answering every sent concern by number (`resolved` or still open, with how or what is
  still needed); new findings only where a comment creates them. Under v2 a concern without a comment stayed listed as it was; every question
  not asked keeps what the window held. The result is shaped like a check's (plus `resolution`: `before`, `asked`, `commented`,
  `resolved`; each concern keeps the latest `comment`), so review and confirmation are unchanged and nothing is approved until the
  instructor confirms; a rubric's wording, points or key are never changed by it (those stay open for the rubric editor; since 2026-10-24 a check's key fix may only ADD accepted forms, FEWER RUBRIC QUESTIONS below). A failed
  or cancelled resolution leaves the review it answered, with the comments kept (`resolution_failed.responses`). A v1 input (one
  response for every concern, never used) is read as that response on each concern.
  v3 (instructor, the same day;
  `resolve-concerns-v3`, prompt `rubric_quality_resolve-v3`): EVERY concern of the problems a concern names is sent, a commented one
  with its comment (or the comment an earlier round kept beside it), and the reviewer settles each concern without a comment
  itself, consistently with the printed problem, key, rubric and the instructor's comments (their comments on other problems'
  concerns come along as `instructor_feedback_elsewhere`); it leaves one open only for an edit guidance cannot make or when nothing
  favors one reading. No comment is required. Such a concern comes back `without_comment` and the window says the reviewer settled
  it. OPEN CONCERNS NEVER BLOCK CONFIRMING (instructor, the same day): only the confirmation
  tick, the points rule and the server's current-check rule do; the tick and any refusal sit in the window's footer beside the
  Confirm button, where a long window cannot hide them.
  Each concern (and each resolved one) has **Show rubric** (requirement, 2026-09-25): a
  native popover over the review window with the rubric of every question it names, the named lines marked, and the exclusions,
  line comments and clarifications as the window holds them (unsaved edits included). A view: it stores and sends nothing.
  GUIDED RUBRIC REVIEW (requirement, 2026-10-01, after approving the "One decision at a time" layout: "I like that layout. go ahead.").
  `static/grading-rubric-concerns.js` is a frontend projection of the TERM plan's retained check, not a new resource owner.
  The review window has Decisions and Rubric items views: one decision with its affected rubric lines alongside it, version
  controls for parallel lines, the original explanation folded, and open feedback. Navigation keeps all drafts mounted.
  Reviewer prompts v8 / versions v3 / resolve v5 may return a short `title`, precise `decision`, problem-local shared `group`,
  and 2–4 supported `choices` (label, standalone editable feedback, and a consequence only where the rubric establishes it).
  NEVER manufacture alternatives or numeric consequences to populate the pane. `choices: []` and older checks without these
  fields use free-text feedback, with the same clarification editor and approval rules. No option is preselected; choosing one
  only drafts feedback in the existing numbered concerns channel. Resolving sends that feedback and the window's guidance by
  the existing route, explicitly; it approves nothing. A shared group combines only different versions in the same known
  problem family with matching decision, choices and comments (since LINKED VERSIONS, below, by the server's `shared` key
  first), retains every original finding and item reference, and fans
  shared feedback back to their original concern numbers. Different policies or drafts stay separate. Resolution may replace
  obsolete choices with no choices when the remaining issue needs investigation or a source correction. Display metadata
  belongs to the check receipt; it changes no rubric identity, score, guidance fingerprint or catalog schema. Inline edits of
  a clarification update the same policy form as Rubric items, never a copied rubric. Full-rubric popovers remain available.
  Keep one explicit approval at the footer, with no per-concern approval prerequisite; unresolved points combinations still
  block confirmation and link directly to their rubric, whereas open concerns without choices never become new blockers.
  LINKED VERSIONS (requirement, 2026-10-02: versions of one question had to be clarified "the same way over and over", while
  some versions test the same mathematics with a genuinely different structure, such as a matrix expression undefined by
  multiplication on one version, by addition of different sizes on another and defined on a third; chosen: link per line,
  reviewer-scoped; one answer adapted per version by the reviewer; links saved; also step-1 comments, Correct wording and
  confirmation per problem). Lines of one problem's versions are linked PER LINE, never whole problems: "same" lines credit
  the same criterion with the same kind of answer (guidance carries, written for each version's own values), "related"
  lines are the same step with a different outcome or structure (shown together, decided per version), and the instructor's
  "none" unlinks. `rubric_links.effective_sets` puts them together, strongest first: the instructor's decisions
  (`frq_rubric_line_links`, migration 041, owned by the TERM grading plan, append-only, one row per pair of lines named by
  item ID, latest row wins; unlinking a line writes "none" to every other line of its set; linking takes a line out of its
  own set and out of the target set any line of its question), then the latest completed check's reviewer links
  (`line_links`), which replace the seeds of every problem that check saw, then seeds (the same item ID on questions whose
  rubrics were drafted together or adapted, `origins`), shown as "not verified". A set holding two lines of one question
  is a `conflict`: shown, never copied or mirrored. GET/POST `.../rubric-links` read and change them; the POST takes
  `operations` (unlink, link, kind) and changes nothing else. LINKS ARE NEVER IN ANY FINGERPRINT: not in
  `rubric_quality_context`, `question_input`, `input_sha256`, a confirmation or `guidance_key` (`confirmation_record` drops
  the check input's `known_links` with `scheme`), so changing one stales no check, confirmation or grade.
  The reviewer (`rubric_quality_versions-v4`) gets `known_links` (the instructor's pairs as fixed, everything else as
  candidates; only where some exist) and returns `parallel_lines` (same or related, one line per version per set). Sets it
  names badly are DROPPED (`parallel_links`) and counted in the summary, never a reason to fail the mandatory check. It
  names each finding ONCE for every version it arises on (`versions`, `item_ids` per version, `version_notes`) and ONLY those
  versions; `validate_problem` keeps one row per question, so numbering, resolution, confirmation and the record stay per
  question, with one server key `shared` (and `versions_named`, `version_note`). An older single-`version` finding is still
  read. Consistency findings keep each version's items as `lines`. The Decisions view groups by `shared`, says where a
  decision applies and which versions of the problem it leaves alone, lists every version's affected lines at once (no
  version tabs) and offers "Decide Version X separately" (window state: that row gets its own feedback). One answer reaches
  every version through Resolve concerns: `rubric_quality_resolve-v6` gets the links as they stand (`resolves.links`), carries
  an answer to "same" lines adapted to each version's values, applies only what fits on "related" lines, never crosses
  "none", and lists versions where it does not fit (`not_applicable`, kept on the resolved concern); a new finding of a
  resolution gets its own `shared` key. In Rubric items a problem with versions is ONE panel: each version's state, then one
  block per linked set, then the lines linked to nothing (each with "Link to"); every line keeps its own version's field.
  Copying a clarification to the linked versions is an explicit button along "same" links only, and says when the text names
  values. Mirroring exclusions (a per-problem switch, on by default) ticks the same pair on each version where BOTH lines
  have a "same" copy, never through related, unverified or unlinked lines, and never onto a built-in rule. On Rubrics &
  Templates a changed line comment offers "Also put this comment on" its linked lines ("same" ticked, related and unverified
  not), each saved through that question's own rubric-edits route (comments only). ONLY WHAT WAS ADDED TRAVELS (2026-10-03: one
  sentence added to Version A's comment had replaced Versions B, C and D's comments with A's, numbers and all): an edit that keeps
  the saved comment as its start or end passes the new part on after each linked line's OWN comment (`passedOn`); an added part
  that names values is flagged; a rewrite or deletion passes nothing (the boxes are disabled and say so), and the reopen window names linked
  questions a comment takes out of a standing finalization; "Confirm Versions … for rubric review" confirms a problem's
  versions in one request. Correct wording lists the linked lines of the other versions with their OWN wording, none ticked;
  each ticked one is its own `correct_rubric_line` (its own revision and carried finalization), one at a time, stopping at the
  first refusal. Nothing here writes a rubric, guidance or score by itself.
  A FINALIZATION THAT READS AT A GLANCE (requirement, 2026-10-06, of a Quiz window with five versions and nothing left to decide:
  "this seems not very user friendly and unintuitive to use"). The window leads with what waits on the instructor, one short
  marked line each (concerns to decide, point conflicts with "Review point conflicts", reviewer suggestions on confirmed versions,
  and the consequence of approving changes to confirmed ones), or "Nothing needs a decision"; then, when a check asked only some
  rubrics, which versions were checked and which keep their confirmed guidance. The header says the guidance is for the AI
  graders only and the rubric's wording, points and exports stay. With no concern and nothing resolved the Decisions view is not
  offered (the tabs are hidden) and Rubric items shows alone; its count is of questions as the list names them (a problem with
  versions is one). A problem's versions are one quiet row each (the letter and its state: "Checked now", "Confirmed earlier", "Confirmed
  earlier, not asked again"), opened by themselves only when one waits on the instructor or a point conflict appears. A linked
  block names its line once (the first version's label, points and wording), then one row per version that repeats only what
  differs; every rubric line shows its clarification (or "No clarification") and its exclusions while closed, with the framed
  + of every fold, so the guidance is read without opening anything. Copy ("Copy this clarification to Version B", disabled
  while the clarification is empty), Unlink and Link to sit inside the line being edited; changing a set's kind is a text
  action of the block (confirming an unverified one stays a button). The panel's explanation of linked lines and the mirroring
  switch (unchanged: per problem, on by default) fold under "How linked lines work"; what a tick means sits beside the ticks.
  A list of six questions or fewer has no search or filter (`mountQuestionWorkspace(..., {find: false})`). Display only: no
  route, field, fingerprint, confirmation or rule changed.
  WHAT THE CHECK PROPOSES (instructor, the same day, after checking Quiz 4's Version E: "I ran the review but I don't see any
  proposed issues"; the reviewer had found none, and its 22 clarifications and 20 exclusions sat unmarked among A–D's lines).
  When a fresh check found no suspected error and no difference, "Nothing needs a decision" says so ("the reviewer found no errors
  and no differences between the versions"). A question with no confirmation of its own, not settled and not under a resolution,
  starts from the reviewer's guidance: the lead counts it ("The reviewer proposes 22 clarifications and 20 exclusions for Version
  E: read them below, then approve", by whole versions where it can), its version row says "Checked now: 13 clarifications and 12
  exclusions proposed", and each of its closed lines reads "Proposed clarification" while the text is still the reviewer's.
  Display only. A WINDOW THAT KEEPS UP (the same day: "clicking through the various questions seems laggy"): a line's "Never
  selected together with" ticks are drawn the first time it opens (they repeat every other line's wording: 108,000 of the window's
  123,000 elements on Quiz 4); the exclusions live in the window's state, so an unopened line loses nothing. Rubric lines off
  screen are not rendered (`content-visibility: auto`) and a question not shown keeps its layout (`content-visibility: hidden`).
  The Decisions view the same way (instructor, the same day: "do the same for the Decisions view"). A decision's row in the list
  says only what changed ("Answered" once its feedback is drafted, "Still open" after a resolution), never "Needs review" on
  every row; a finding the reviewer gave no title is named by its kind ("Consistency across versions", "Possible error in the
  rubric") with its question under it, never "Concern about Question N"; the header drops "N findings grouped" and says where a
  shared decision applies in one sentence. The feedback box shows once an answer is picked (at once when a decision has no preset
  answers, or holds a draft). The Resolve block (Resolve concerns, what it sends: "Sends all N decisions to the reviewer, with
  your answers on M; it settles the others itself", and "How feedback is resolved") moves with the decision on screen; a failed
  or cancelled resolution is said at the top of Decisions. Affected rubric lines are named once per linked set with one row per
  version (its clarification or "No clarification" and its exclusions shown closed, opened to edit that version's own field,
  with "Copy this clarification to Versions …", disabled while empty); a finding that names no line says it concerns the whole
  rubric; "Show rubrics" and "Open in Rubric items" follow. Display only, as above.
  LINKS FROM THE DRAFT (requirement, 2026-10-02, the same day: "integrate this linking from the start at assignment generation"):
  the writers that draft every version at once (`rubric_write_versions-v4`, `quiz_rubric-v4`) give each shared line a `link`,
  "same" or "related"; `family_rubric` keeps it where it is one of those (an older writer gives none) and the all-versions review
  shows it per line ("Across the versions": the same, related, or not marked), editable. Acceptance and quiz approval record
  `line_kinds` ({line ID: kind}) in each created rubric's provenance; `rubric_links.drafted_kinds` follows it back through the
  instructor's edits and corrections, and a seed whose rubrics all carry a kind for that line starts as that kind (any
  "related" wins), origin "drafted", shown "as drafted, not reviewed yet". It is still a seed: the reviewer's links replace it
  and the instructor's decisions win; a check still sends it to the reviewer as a candidate to verify (kind "seed"), never as
  decided. No migration, no new route; nothing already drafted changes.
  THE RUBRIC PROMPTS SETTLE WHAT GRADERS STOPPED AT (requirement, 2026-09-28; extended 2026-10-03 from Quiz 4's 58 held grades:
  writers `rubric_write-v6`, `rubric_write_versions-v5` and `quiz_rubric-v5` also settle a wholly blank part, letters the student
  never defined or wrote only over the printed problem's words, the rubric's own letters for a student who chose others, a label that
  is already one of the student's letters, a non-variable entry in a list, a relation other than "=" or the wrong one, and marks that
  are not answers, such as a stray stroke or a faint erased mark).
  Measured on Exam 1, Quiz 2 and Quiz 3:
  about 149 of 234 rubric-wording doubts were a literal word, label, variable name or form;
  then methods and
  shown work (rref, unfinished algebra, a graph read checked in one equation), omissions no line charged,
  the no-credit line beside deductions, one mistake two lines, and labels or variables of the student's own
  that are missing or wrong; and a drafted part line worth the whole part beside the part's answer line made 297 of Quiz 2's 655
  grader disagreements. Writers v5 (`rubric-draft-v5`) and all-versions v3 (`rubric-draft-versions-v3`), reviewers
  `rubric_quality-v7` and `rubric_quality_versions-v2`, and `rubric_quality_resolve-v4` therefore check those cases; state a
  requirement by its meaning with examples only as examples; by default count recognizable variants (abbreviations and
  misspellings, other letters used consistently, a label without "=" or "(x)", a unit or symbol identifying a quantity, an exactly
  equal form for a VALUE) unless the problem, line or a comment prescribes the characters, and say whether rounded values count;
  and never write a partial-credit line worth as much as its part's answer line. "COMPLETELY INCORRECT" HAS ONE MEANING
  (instructor, the same day), stated as the no-credit line's CLARIFICATION, never in its wording (which Gradescope shows and the
  built-in rule matches exactly): in positive scoring, apply it only when no other rubric item applies; in a deducting rubric, only
  when the deductions that apply add up to at least the problem's points (a part's, for a part's line). NEGATIVE SCORING HAS NO
  NO-CREDIT LINE (instructor, the same day): a deducting draft is the zero-deduction full-credit line and its deductions, which
  normally add up to the problem's points; alternatives may add up to more, and the score floors at 0; a multiple-choice problem
  deducts "Wrong choice, more than one choice, or no choice" for all its points. `draft_warnings` flags a no-credit line in a
  deducting draft and deductions short of the total (it no longer asks for a whole-question deduction). THE DRAFTER WRITES ITS OWN
  CLARIFICATIONS (instructor, the same day): each drafted line (each version's, in an all-versions draft) has a `clarification`
  beside its Gradescope wording; the review dialogs show it folded and editable, and accepting a question saves each non-empty one
  as that line's COMMENT (`_save_drafted_clarifications`, actor `workspace`, event `rubric_draft_clarifications_saved`, only on the
  rubric just created and only on lines with no comment history; a replacement writes them right after clearing the old comments,
  in the same transaction). The comment is the existing channel into the rubric review, which carries it into the clarifications
  the instructor confirms: nothing drafted is ever written into a rubric revision or its source. Each review and resolution also
  reads the instructor's line comments on the plan's OTHER problems (`comments_elsewhere`, payload `instructor_comments_elsewhere`,
  only when there are some) as their policy. Checks, confirmations and grades are unchanged, and nothing already drafted, finalized
  or graded is redone.
  PART CAPS (requirement, 2026-10-15). Shown their Quiz 3 ruling "Part B is capped at 1.5 deductions" (never built into a rubric: the
  writers were told to write no cap, and a sentence changes no score), the instructor ruled for every problem: "the parts should be
  capped, a part b problem shouldnt eat into part a just because there is more deductions than points available for part b", and
  chose "Build it". In a DEDUCTING rubric each lettered part's lines (wording that BEGINS "Part B:", the part-line rule above) are one
  line group `part-b-cap`, selection multiple, ceiling 0, floor minus the part's worth, so `scoring.score` stops the part's deductions
  at its worth; whole-question lines stay outside every cap. The floor is the only place a part's worth is kept: the writers
  (`rubric_write` v8, `rubric_write_versions` v7, `quiz_rubric` v7) return each part's worth in `parts` (the printed points, or
  their division of the total), a printed worth wins over theirs (`rubrics.part_worths`), and accepting a draft, a quiz approval, a
  version adapted from another and a replacement build the caps (`rubrics.capped_parts`); a makeup keeps its original's rules, caps
  or none. Where the worths are unknown or do not add up to the question's points the parts stay uncapped, with a draft warning and
  a rubric warning saying so. `rubrics.validate` keeps every line in the cap its wording names, so an edit that rewords "Part A:" as
  "Part B:" moves the line, and a cap never makes a condition structured. What a cap gives back is the score's `part_caps`, with a
  note naming the part; Gradescope cannot cap a part, so it goes with the grade's point adjustment (GRADESCOPE). The reviewers
  (`rubric_quality` v10, `_versions` v6, `_added` v3, `_resolve` v8) read the caps in `rubric.groups` and never ask whether a part's
  deductions reach another part; they report only a worth that contradicts the printed points or caps that do not add up. Regrade
  replies see the caps and what they gave back. MY CHOICES (theirs to change): a positive rubric is not capped (a part's credits
  cannot exceed it already); a problem with one lettered part is not capped; the caps show beside the rubric's point range ("Part A
  loses at most 0.5"). Assignments already certified without caps stay as they are (instructor: "Leave them"); a rubric made before
  this change keeps its rules, and a rescore across a capped and an uncapped revision is refused as a change of meaning.
  RUBRIC MEMORIES (requirement, 2026-10-14). Asked why a drafted rubric still needs review, the instructor proposed "a sort of memory
  system for the rubric drafter that helps it avoid issues in the past", turned down memories found by question similarity ("the
  assignments naturally progress to different material"), chose "the flags of the grading models/rubric reviewer" made into "general
  memories that apply to a broad class of problems", asked for a dry run, then a re-draft of Quiz 4 with and without them (the draft
  followed 1 of 6 of their rulings without and 5 of 6 with; the reviewer's questions, 6 and 3 without and 3 and 4 with, did not
  clearly drop), and said "Build it" with the test's two fixes (`grading/rubric_memory.py`, `grading/rubric_memory_api.py`, migration
  047, prompt `rubric_memory-v1`, page `static/rubric-memories.js`, drawn inside the Memory page since 2026-11-10: ASSISTANT-MEMORY
  "ONE PAGE").
  - OWNER: the COURSE (`course_definitions.id`): one list for every term of the course. A learning pass (`frq_rubric_memory_runs`) is
    owned by the TERM grading plan whose evidence it read; memories (`frq_rubric_memories`) and the instructor's decisions
    (`frq_rubric_memory_decisions`) belong to the course. All three are immutable and retained; a revision is a new row that names
    the row it replaces and keeps its number (M4 stays M4).
  - WHAT TEACHES: one pass per plan, after the instructor's own Certify (`certification.certify` → `after_certified`: never an
    automatic or amending certification; never a reason for Certify to fail; nothing queued without a Rubric drafting and review
    model; nothing either while the instructor has switched "Learn rubric memories by itself" off on the Memory page, which also
    holds back the pass after rubrics are approved: ASSISTANT-MEMORY "ONE PAGE"), or on the instructor's request on the Memory
    page's Rubric memories, for the course's certified assignments, whatever that switch says. The pass reads, when
    it runs: the concerns the plan's rubric checks raised and the answers the instructor typed to them (Resolve concerns, and at a
    quiz's draft review), the graders' `rubric_ambiguities` by rubric line, and the lines the first two graders ticked differently
    (at least 5 times and 5% of the responses graded by both). Responses with a page whose scan was deleted in the Inbox count
    nowhere (`PRESENT`). Every sentence is read without the current rosters' names, the names on the roster of the plan's own
    term (2026-11-09 review: a pass can read an assignment of a term that is over, whose students are on no current roster;
    `student_notes.term_names`), nine-digit numbers and addresses (`assistant.memory.nameless`; a roster that cannot be read
    fails the pass rather than send a name). Evidence a finished pass of the plan already read asks
    nothing again (`evidence_digest`). A course's passes run one at a time, oldest first, so each reads what the one before proposed.
  - WHAT IT WRITES: one request to the Rubric drafting and review model (`rubric_memory-v1`, owner `frq_cloud_calls.rubric_memory_run_id`,
    receipts retained, an answer refused once with the reason; of the second answer a memory still refused is left out and
    named in the run's result under `refused`, and the rest is written, where until 2026-11-09 one such memory failed the
    whole pass; an answer that is no answer still fails it): at most 8 new memories, kind "rule" (how the instructor wants a class
    of cases settled; only from their answers) or "watch" (a class of cases a rubric must settle, never picking the answer), and
    revisions of kept ones (confirm, narrow, widen, answer a watch, retire). THE TWO FIXES from the test: a memory's `covers` names the
    case in the student's response, never how a line is worded or judged, so a rule reaches lines judged against a printed result as
    much as the student's work; and a memory never changes a line's required wording (a multiple-choice or no-credit line), only how
    its clarification applies it. Refused in code: a rule without one of the instructor's answers among its sources, sources that are
    not this pass's evidence, a line ID, a version letter or typeset math (`SPECIFIC`), `rule` over 450 characters, `covers` over 300,
    `not_when` over 450. A row of a matrix is no line ID (`ROWS`, 2026-11-09 review: this course teaches row operations, and
    "swap rows R1 and R2" or "R2 - 2R1" was refused as naming a rubric line): rows named after the word "row" and rows joined
    by an operation or an arrow are read out of a sentence before it is looked at, and "the version I wrote" names no version.
    "Swap R1 and R2" alone still reads as two lines, and so does "R2-R4" (a hyphen joins two rows only with a space each side).
  - WHAT STANDS: what is KEPT. KEPT AT ONCE (requirement, 2026-10-17: "why aren't rubric memories automatically kept", then "yes" to
    keeping each pass's memories the moment it writes them): a pass records a Keep (actor `automatic`) for every row it writes, in
    the same transaction; the instructor's Drop on the Memory page's Rubric memories for the course (`#memory?course=<definition>`; until 2026-11-10 the
    course's own page, `#course/<group>/home/rubric-memories`, which now lands there) takes one back and
    Keep brings a dropped one back; a row written before that still waits for Keep or Keep all; the latest decision about a row is
    its state. A kept revision
    replaces what it revises; a kept retirement takes it away, and is listed on the page under "Retired by a learning pass"
    with what it retired, where Bring back (the instructor's Drop of the retirement) makes that memory stand again (2026-11-09
    review: kept at once, a retirement was in no list of the page, so nothing could take it back). A revision of a memory the
    instructor dropped while the pass was reading (the model takes a minute or more, and what stood was read before it) is
    not written: kept at once it would put back what they had just taken out; the run's result names it under `stale`.
    `standing(db, course, at)` is the list as the decisions up to `at` leave it.
  - WHAT READS IT: `standing_rules` (id, kind, rule, covers, not_when), only when the course has some, so every other request is the
    request it always was: the rubric writers (`rubric_write-v7`, `rubric_write_versions-v6`, `quiz_rubric-v6`) as the list stood
    when the draft or revision was asked for (the same on every retry), and the rubric check (`rubric_quality-v9`,
    `rubric_quality_versions-v5`, `rubric_quality_added-v2`, `rubric_quality_resolve-v7`): frozen into the plan's check input at queue
    time, read at a quiz's draft-time check as the revision's writers read it. A resolution of concerns is a request of its own
    and reads the list as it stands when it is queued, not as the check it answers froze it (2026-11-09 review: it copied that
    check's input, so a memory dropped since, perhaps for the very concern being resolved, reached the reviewer again). Each prompt is its predecessor plus one section: a
    rule decides its cases (the writer does not list it as a reading to confirm and names the rules it applied in `notes`; the
    reviewer applies it and does not ask), a watch only names cases, and a required wording that a rule refines is no conflict.
    THIS ASSIGNMENT COMES FIRST (requirement, 2026-10-15, before keeping all 26 of the first proposals: "there may be some
    assignments where I take a different stance on some of these"; they chose that their comments win): the instructor's own word
    on the assignment at hand (a line comment, an answer to a concern, a quiz's instructions or feedback) decides a case for that
    assignment wherever it differs from a standing rule, which still stands for the others (`rubric_quality` v11, `_versions` v7,
    `_added` v4, `_resolve` v9, `quiz_rubric` v8; the template writers read no such word). The next learning pass after that
    assignment's Certify may then narrow the rule, kept at once like every revision (their Drop restores the earlier wording).
    The memories are in no confirmation's fingerprint (`confirmation_record` drops them like `known_links`), change no rubric,
    clarification already confirmed, or grade, and nothing already drafted or checked is redone.
  - THE COORDINATOR'S RUBRIC (requirement, 2026-10-26: "fix all six", of a review that found Exam 2's rulings kept at once as
    course rules, one of them (M26, from the Q23 corners ruling) the ruling that broke the coordinator's sheet; the morning's "if my
    ruling contradicts the coordinators, you should default to the coordinator"). From 2026-10-26 to 2026-10-28 a plan whose rubrics
    came from Gradescope read no standing rule and its passes waited for Keep. REPLACED 2026-10-28 by FITTED TO EACH QUESTION and
    NEVER WRITTEN AGAIN below (instructor, asked why Exam 2's ten new memories were not kept: "can't we use a jev call when recalling
    rubric memories to ask if it contradicts clarifications that I've provided or explicit contradictions from the rubric itself?",
    then "proceed"): every plan reads the course's memories, and every pass is kept at once. The coordinator's decisions Faculty
    holds (the uploaded rubric, its clarifications, the instructor's answers) are what a memory is fitted against; a ruling found only
    on the coordinator's own sheet is invisible to Faculty until it is entered as a clarification (M26 agreed with Faculty's own Q23
    clarification; it disagreed only with the sheet). Memories kept or dropped meanwhile keep their decisions.
  - RULINGS SAID IN A RECORDING (2026-11-13, RECORDINGS "A RULING", step 3; instructor: "build step 3"). A coordinator's
    ruling is often SAID in a meeting before it is on any sheet. Faculty's reading of a recorded meeting keeps each statement of
    how a question is graded as a `ruling` naming its assessment and question, with the exact words it rests on. It WAITS on
    that assessment: the finalization window reads `GET /api/recordings/rulings?assignment=<plan>` beside the check and, when a
    meeting said anything, lists the rulings above the reviewer's summary ("Said in your recorded meetings", open while one
    waits; `static/grading-recorded-rulings.js`), each with its quote, who said it and a link that plays it. The instructor
    chooses a rubric line (those of the questions printed with the number that was said, else every line) and ADDS the sentence
    to that line's clarification, theirs to edit, or presses Not for this rubric. NEVER APPLIED BY ITSELF: adding only fills
    the clarification field of this window, and counts exactly as a clarification they typed, by their own Confirm; the
    choice (`accepted`, `declined`) is recorded with the recording's item and changes no rubric, clarification or grade. NEVER
    ON A CERTIFIED PLAN: the route answers no ruling for one and refuses a choice. A window with no ruling is unchanged, and
    a failed read of the rulings leaves it as it was. Words that this assessment's rubric is NOT final (the latest thing a
    meeting said of it) show as a warning at the top of Upload rubrics and of Import rubrics from Gradescope. The rubric
    check itself is told nothing of a ruling until it is a clarification.
    HELD BY NAME (2026-11-13, RECORDINGS "A RULING" › HELD BY NAME; instructor: "Yes, build that"): a ruling whose
    assessment the reading had no id for is offered here when the name the meeting called it by is this assessment's title
    (by code, every word of the title, at each ask: also on an assessment made after the meeting), and its line says
    "Said of “<name>”" so the instructor sees why it is here. It is added or declined like any other.
  - FITTED TO EACH QUESTION (`rubric_memory.memory_fit`, `rubric_quality.rules_for`; JEV "RUBRIC MEMORY FIT"). Before a rubric check
    sends anything (a plan's check, a quiz's draft-time check, a resolution of concerns: `run_rubric_check`), Jev reads each question
    (its rubric lines, the instructor's clarifications of them, the instructor's answers about it, the assignment's quick rubric
    choices and, in a resolution, the answers being sent) with the standing memories, FIT_CHUNK (8) to a call, and asks of each
    whether it contradicts the rubric or clarifications, and whether it contradicts the instructor's word. Either at FIT_CUTOFF (.7)
    or above leaves that memory out of that question's requests (a problem's request with versions: out where it contradicts any of
    them); the other questions still read it, and nothing about the memory changes. Measured before building (2026-10-28, read only,
    Exam 2's last check input, `/mnt/nvme/Teaching/tmp/rubric-memory-fit/`): 66 memories by 24 questions, 1,584 pairs, none at .7
    (the highest .61); eight memories written to contradict one clarification or answer each, seven at .71 to .91 (one missed at
    .45); about 0.2 s a call. The receipts are owned by the check; a turn after a wait reads them again, and a failed, refused or
    uncertain one leaves its memories in, as before. The writers are not fitted: a draft has no rubric yet, and the check of what
    they draft is.
  - NEVER WRITTEN AGAIN (`rubric_memory.not_dropped`; JEV "RUBRIC MEMORY FIT"). A pass is shown only what stands, so Exam 2's pass of
    2026-10-28 wrote again the Q23 answer the instructor had dropped two days before. Before a pass writes, Jev asks of each new
    memory and each revision whether it is essentially the same grading rule as a dropped one (it would grade the same responses the
    same way); at SAME_CUTOFF (.7) or above it is not written, and the run's result names it under `dropped_again`. Measured
    2026-10-28 (read only, the course's 70 memories against its two dropped ones): the re-written Q23 answer .83, every other memory
    .24 or below. Without Jev, or on a receipt that is not complete, it is written as before.
  - MY CHOICES, unasked (tell the instructor if asked): Keep/Drop for every proposal was mine until 2026-10-17, when the instructor
    chose KEPT AT ONCE above; no switch and no
    automatic pass after an automatic or amending certification; the caps above; the evidence limits (60 answers, 40 questions, 30
    doubted lines with 5 sentences each, 20 disagreement lines); routes the Assistant's policy does not name, so any Assistant use
    needs an Approve card.
  - REAL-MODEL GATE (2026-10-14, isolated copy of live, deleted after; scripts and results in `/mnt/nvme/Teaching/tmp/rubric-memory-gate/`):
    migration 047 applied to the copy of the real catalog; four passes over Exam 1, Quiz 2, Quiz 3 and Quiz 4 (95, 30, 62 and 73
    seconds) proposed 27 memories, 16 rules and 11 watches, 94 words each at the median (the dry run's: 130); every multiple-choice
    rule says the line's wording stays, and the follow-through rule is stated by the carried mistake. All kept, Quiz 4's stored draft
    re-drafted through `_quiz_rubrics`, `_quiz_keys` and `_quiz_rubric_check`: its lines follow all six of the instructor's Quiz 3
    and Quiz 4 rulings that apply (the dry-run test: 1 of 6 without memories, 5 of 6 with); its two checks asked 6 and 7 questions,
    none one the instructor had ruled on and none about the multiple-choice wording (the test's memory arm asked that in both
    checks). The count of new questions did not drop, as in the test. One question came from a memory: a part's cap on deductions
    (from one Quiz 3 answer) written in clarifications, which do not change scoring, was asked about as not built into the rubric.
  FEWER RUBRIC QUESTIONS (requirement, 2026-10-24: "it is very cumbersome to review and try to fix all of the comments of the rubric
  reviewer"; shown that of about 36 questions the reviewer asked since Oct 1 on Quiz 4, Quiz 2's makeup, Quiz 5 and the Mathematical
  Experiments Assignment about 10 had one right answer, about 8 repeated a question already asked, the rest were real decisions that
  recur as themes, and about 1,000 proposed clarifications had been approved with about a dozen edits and no exclusion ever changed;
  offered four ways to cut the burden: "I actually like all of these ideas, implement them"). Four parts, one contract:
  - BOOKKEEPING IS FIXED, NOT ASKED. The reviewers (`rubric_quality` v12, `_versions` v8, `_added` v5, `_resolve` v10; schema `FIX`)
    give every suspected error a `fix`: "none" (a decision, as every finding was), "guidance" (exactly one right reading follows from
    the instructor's own sources, the rubric, a comment, their choices or earlier answers, a standing rule or the other versions,
    and the clarifications already carry it: a comment written with another version's values, a key that labels a part otherwise, an
    `exceptions` field that repeats its description) or "key" (an answer key part accepts fewer forms than the instructor's own line,
    comment, choice or the parallel versions' keys: `keys` names each version's part and the forms to add). A finding with choices,
    one needing a change of a rubric's wording or points, a source correction (a printed typo, a wrong or empty key) or with any doubt
    is never a fix; a fix that cannot be read is dropped and the finding is asked (`fix_fields`, `version_fix`: a shared key fix
    reaches each version with its own keys, a version whose key needs nothing has it in its guidance only). After a check completes
    (and after a drafted quiz's check is carried, `record_drafted_check`), `apply_rubric_fixes` ADDS the forms through the ordinary
    `save_answer_key` (actor `automatic`), once per check, only on questions this check asked (never a settled one) that have no
    grade yet, only to a part the key has (a named part by its label; an unnamed one is the key's unlabeled or only part), never
    removing anything, at most 20 forms a part; one event `rubric_fixes_applied` names each fix's outcome (`applied` with what it
    added, or `skipped` with why). A key is no part of what a confirmation covers, so the check stays current. `fix_states`
    (`check.fixes` of the status's details) gives each fixed finding its state: guidance fixes `applied`, key fixes `waiting` until
    the event, then `applied` or `skipped`, and `undone` after Undo. The review window lists applied fixes apart under "Fixed for
    you" (one row per finding, a shared one once), with what was added, each with Undo (POST `.../rubric-quality/fixes`
    {check_id, concerns}, `undo_rubric_fix`): the concern is a decision from then on, and a key fix takes back exactly the forms it
    added, while its question has no grade (else refused: the key is edited under Answer key). Concern numbers never change. A
    resolution sends each fix as it stands (`with_fix_states`: an undone or skipped fix is gone; an applied key fix names what it
    added as `applied`, so the resolution's result keeps it fixed and adds nothing again), tells the reviewer a fixed concern is
    `handled_by_faculty` (settle it as resolved), and the resolution's own new key fixes are made the same way. A quiz draft's page
    lists the reviewer's guidance fixes apart with "Ask me instead" (kept for the revision on the page, `page.unfixed`); a key fix
    there is asked, since its keys are the draft's until approval, and is made on the plan once the drafted check is carried. This
    is the one exception to "a rubric's wording, points or key are never changed by" the check: a key gains accepted forms only.
  - NOTHING ASKED TWICE. A check's input carries `earlier_answers` (`earlier_answers`): every answer the instructor typed to a rubric
    question of THIS plan (the concerns of its completed resolutions, and the answers at the draft review of the quiz it was approved
    from, `RubricMemory._draft_answers`), once each by question and answer, with the questions it was `about`, the latest 80 (`EARLIER_ANSWERS`; 40 until 2026-10-26, when
    Exam 1's 47 answers left its seven earliest out). The
    reviewers read them as the instructor's word on this assignment: a case one settles gets the clarification that applies it and is
    never reported again, and an answer about one version settles the same case on every version of that problem. ACROSS
    ASSIGNMENTS: once the instructor confirms a finalization after answering rubric questions no pass of the plan has read
    (`RubricMemory.after_finalization`, called by `confirm_rubric_quality`), one RUBRIC MEMORIES pass is queued at once (trigger
    "instructor", actor `automatic`, input `after: "finalization"`; the course page says "after you approved its rubrics"), so the
    next assignment's draft and check know the rulings before this one is certified; a pass already waiting, no rubric model or the
    same evidence asks nothing. Never a reason for the confirmation to fail.
  - RUBRIC CHOICES. A few recurring questions, answered once for an assignment before its rubrics are drafted (`grading/rubric_choices.py`
    `CHOICES`: `work` expected or not needed, `decimals` exact only or rounded counts, `variants` recognizable or exact words only,
    `all_incorrect` only when nothing else applies or whenever every answer is wrong; "Ask me" leaves one to the check). OWNER: a
    plan's choices belong to its TERM grading plan (`frq_rubric_choices`, migration 049, append-only and immutable, the latest row
    stands, `rubric_choices.record` writes only a change); a quiz draft's are in its request (`quiz_drafts.input_json.rubric_choices`,
    named only where some were chosen, so other requests keep their fingerprints), course-owned like the draft, and the approval
    records them as the new plan's (actor `quiz-approval`). Each form starts from the course's latest answers (`of_course`), never
    sends them unasked. Asked in the "Draft rubrics for all versions" dialog (and the single-template draft; the generation's input
    keeps them and the plan's row is recorded), in the new quiz draft form (sent as JSON text with its files), and set on their own
    from Key & Rubric Finalization's panel ("Set choices" / "Change choices", GET/POST `.../rubric-choices`). The writers
    (`rubric_write` v9, `rubric_write_versions` v8, `quiz_rubric` v9) and the reviewers read them as `assignment_choices` (topic and
    the sentence chosen, `rubric_choices.stated`): decided policy for this assignment, before a standing rule (THIS ASSIGNMENT COMES
    FIRST); a line comment, being about its line, decides over them. The check reads the plan's choices when it is queued; a check
    that already ran keeps what it asked. Like `standing_rules` and `known_links`, `assignment_choices` and `earlier_answers` are in
    no fingerprint (`confirmation_record` drops them), and they change no rubric, confirmation or grade by themselves.
  - THE WINDOW SHOWS ONLY THE DECISIONS ("you're not reading 120 lines you'd approve anyway"). The review window opens on Decisions
    (unless a question was asked for by name), also when nothing is left to decide ("Nothing needs a decision. The reviewer's guidance
    is approved with the rubrics; read or edit it under Rubric items."), with Fixed for you under it. The proposed guidance waits
    under Rubric items, unchanged, approved with the rubrics unless edited: the lead says "The reviewer proposes N clarifications and M
    exclusions for … They are approved with the rubrics; read or edit them under Rubric items", and a line on what Faculty fixed. The
    explicit tick stays ("I approve these rubrics and their guidance for grading"); nothing weaker confirms a finalization. This
    supersedes, for the window, "With no concern and nothing resolved the Decisions view is not offered" and "read them below, then
    approve" above.
  - MY CHOICES, unasked (theirs to change): the four questions and their wording; "Ask me" as each question's default when a course
    has no answers yet; a key fix only adds forms, only on an asked, ungraded question; a fixed guidance finding needs no event; the
    finalization pass only after answers no pass read; the tabs stay (Rubric items one click away) rather than a single page.
  - 2026-10-29 (instructor: "go ahead", after a review of the 103 rubric questions asked since Oct 1, report
    `/mnt/nvme/Teaching/reports/rubric-questions-review-2026-10-29.md`: since Oct 24, 21 of 44 were judgment calls only they could
    make, 12 source or pipeline errors, 7 had one right answer (4 of them key fixes a draft's page still asked), 4 repeated a question
    of the same check). Eight parts:
    SETTLED BY THE INSTRUCTOR'S OWN SOURCES. The reviewers (`rubric_quality` v13, `_versions` v9, `_added` v6, `_resolve` v11) also
    fix as guidance, never ask: a form or precision the printed problem prescribes; a comment that says otherwise than its own line's
    wording, `follow_through` flag or `exceptions` (the comment governs; never "confirm it governs" or "edit the wording"); parallel
    lines whose scope or wording (never points) differs on one version only, with no comment on it (read as the others; this comes
    before standing watch M56's "ask which is intended", which the instructor may drop); and a reading the instructor's own sources
    favor without stating it (a choice, a standing rule or an earlier answer applied to a close case of its kind, or how their comments
    treat the same case on another line). The first check now meets the bar Resolve already applied to a concern without a comment;
    each such fix is listed under "Fixed for you" with Undo, so nothing is decided out of sight. Not changed: Quiz 6's uppercase-X
    reading, which the instructor rules on.
    ONE QUESTION ONCE. Every decision has a `topic` (`decision_fields`): one of a short list ("shown work", "rounded decimals", "exact
    form", "letters and notation", "spelling", "answer outside the answer line", "all incorrect line", "ordered pairs and tuples",
    "interval notation"), else the standing watch's id, else "". The decisions view (`concernGroups`) shows the decisions of one topic
    on several problems as ONE decision with a part per problem ("How it is asked on each problem"), one answer reaching every row (the
    reviewer adapts it to each problem's lines), and "Decide Question N separately" (window state `apart:<row>`); a differing unsent
    draft, a fixed finding or a row decided apart never joins. A quiz draft counts decisions the same way (`quiz_drafts.decisions`).
    AN EDIT ONLY THE INSTRUCTOR CAN MAKE. A decision may carry `edit`: the points change the instructor's own word already asks for
    (`edit_fields`: [{question_id, item_id, points}], only real lines whose points it changes, within the maximum; anything unreadable
    dropped). The window offers "Approve this change"; the plan's POST `.../rubric-quality/edits` {check_id, concerns}
    (`apply_rubric_edit`, current check only) saves it through `edit_rubric` (actor workspace: the instructor's own edit, a new
    immutable revision) and names those rubrics again for review (`confirm_rubric_review`), event `rubric_edit_approved`; the check of
    the changed rubrics follows as after any edit. A draft's Approve asks for a revision making exactly that change. Wording is never
    proposed there. (Exam 2's R7_16 was asked four times over two days.)
    KEY FIXES ON A DRAFT ARE MADE ON THE PLAN, NOT ASKED. A quiz draft's page lists key fixes under "Fixed for you" too ("will also
    accept … once the quiz is approved"; "Ask me instead" sends `unfixed`, which strips their fix for that answer), and its counts no
    longer take a fixed finding for an open decision (`check_counts`, which also kept the approval's one tick from ever confirming a
    check holding a fix). `apply_rubric_fixes` also makes a key fix an answers revision moved to `resolution.resolved` (named
    `resolved: n` in its event, without Undo, which reads only listed findings): Quiz 6's Problems 2 and 3 would have lost theirs.
    A DRAFT'S ANSWER REWRITES ITS DRAFTED COMMENT. Where the instructor answered a decision about a draft's line and Resolve changed that
    line's clarification, the drafted clarification it had (the check's "comment"; a line drafted without one gains none) becomes the
    new one (`answered_comments`; the revision's
    `rubric_answers.comments_rewritten`), and the check's content digests are recorded for the new comments, so the approval still
    carries the check. (Quiz 5 asked twice for its R7 comment to be edited.)
    THE WRITERS (`rubric_write` v10, `rubric_write_versions` v9, `quiz_rubric` v10) write "with supporting work" only when a line charges
    missing work, else "Correct answer:", and settle one kind of case the same way in every part and version.
    LETTERS AND NOTATION is a fifth rubric choice (printed letters only / the other case too / own consistent letters and standard
    notation; "Ask me" by default, never answered for the instructor), and the Gradescope import and rubric upload dialogs ask the
    choices too (`choicesFold`), so a plan whose rubrics were never drafted has them before its first check.
  DEPARTMENT POLICY: a rubric's wording is never changed by anyone but the instructor (a line corrected in step three, or lines
  the instructor edits on Rubrics & Templates, above; 2026-09-24). Clarifications and exclusions are an overlay
  BESIDE the immutable rubric, for internal use only: never written into a rubric revision, its
  `excludes` or scoring rules, never shown as the rubric's wording, never exported. `grader_rubric` is the
  one place they meet the rubric: every grader of a team reads each original line with its
  `clarification` and `exclusive_with`, without the import warnings, which are addressed to the instructor.
  A batch freezes the confirmation it was submitted under
  (`profile.rubric_finalization`); its results name that one in their scope, a newer confirmation makes a
  preview stale and keeps older proposals out of automatic acceptance where it changed their question's guidance, and a batch from before
  finalizations reads what it always read. Scores stay the approved rubric's alone: a selection that
  violates an exclusion is flagged for individual review and withheld from automatic acceptance, never
  refused, re-billed or rescored; an instructor's own conflicting selection is recorded, not blocked.
  Changed rubric inputs require a fresh check and confirmation. Retain model receipts and immutable
  confirmations, suspend the automatic acceptance of questions whose guidance a policy change changed, and preserve human
  decisions. A cancelled check sends nothing further and its requests in flight are closed; each call keeps its record.
  A RE-RUN THAT NEVER FINISHED HIDES NOTHING (requirement, 2026-10-05: cancelling Quiz 4's second check left the finished one of the
  same rubrics unreachable): when the plan's newest check (not a resolution) was cancelled or failed, the status reads the newest
  finished, uncancelled check of exactly the same input before it, to review and confirm, and says so (`rerun_failed`);
  `workspace_overview.setup_waiting` reads it the same way. Nothing is written.
- After Assignment Templates is confirmed, `grading/location_checks.py`
  automatically queues local OCR/layout verification on bounded worker turns.
  Template page receipts are course-wide; checks and append-only confirmations
  belong to the term grading plan and pin its question/rubric/template inputs.
  Match printed question numbers (an explicit setting, then the rubric filename,
  then the question label), never Gradescope's internal index or a guessed score.
  Read adjacent printed totals independently of rubric maxima; missing, conflicting,
  uncertain or failed readings require manual review and a recorded explanation.
  EVERY proposed location requires a confirmation, regardless of confidence.
  ANSWER AREAS CONFIRM THEMSELVES (requirement, 2026-10-22: "I want answer areas and answer keys to automatically confirm. You can
  keep everything else as it is", after the record showed all 198 areas confirmed so far kept their suggested crop): the worker's
  pipeline step `auto_prepare_one` (`grading/auto_prepare.py`, before `auto_grade_one`) confirms a finished check's suggested
  areas exactly as suggested, through the ordinary `_confirm_location` with actor `automatic` (receipt and event), for a check
  that `needs_confirmation` and for one that `needs_review` (the instructor acknowledged every such check with "it's fine"; the
  receipt is `manual_review` with the note `auto_prepare.NOTE`). It never confirms a failed check, a check without suggested
  areas or a printed number, areas that would change privacy masks, a question with a grade or a decision (`graded_yet`), or
  anything while a batch of the assignment waits or runs; those keep the page's controls. The status reads "Locations confirmed
  automatically". The Assignment Templates step, the template choice and page layouts stay the instructor's. BOXES FROM GRADESCOPE
  (requirement, 2026-10-25: "why do so many answer areas for exam 2 need me to confirm?", after the blank's page 8 could not be read
  for Versions A and B): a question whose areas were imported from Gradescope's outline (GRADESCOPE "IMPORTING RUBRICS FROM
  GRADESCOPE", the check suggesting exactly those boxes, `regions_from: "gradescope"`) is confirmed as above even when its check
  FAILED, since those are the boxes Gradescope grades with and the reading only verifies the printed number and points; the
  receipt is `manual_review` with the failed reading in its result. A printed number is still required.
  Only that confirmation creates a question revision with the chosen regions; OCR
  cannot alter questions, shared maps, point values, rubric approvals or Inbox gates.
  Changed inputs invalidate confirmations (a new rubric revision with the same maximum is no changed input: its confirmed area
  is carried, 2026-10-11); grading, approval and export revalidate
  the gate. Failed/interrupted OCR requires manual review or explicit bounded retry. A check records the local model
  address it was queued with; an explicit retry of a FAILED check records the current one when it changed (migration 023,
  2026-09-25). Its inputs stay immutable.
  A retry never takes the worker's execution lock (held through every page read);
  the lifecycle lock orders it. A retry re-reads only the pages that FAILED: a page an earlier attempt read
  without error is reused (`_reading`, requirement, 2026-09-25), so only FAILED checks can be retried (a check that needs
  review has no failed page and would come back the same; it is reviewed instead). On a question showing no answer
  area, Retry retries EVERY failed question with neither a confirmed nor a suggested area and a retry left
  (`retry_all_locations`; one transaction, a stale check refuses all). A turn reads up to `OCR_BATCH` (8) unread pages
  of the blank in ONE GLM-OCR model load (`faculty_ocr.recognize_pages`, each page prepared and read exactly as
  `recognize` does) and keeps the others in the worker's memory, used once, for their own turns; each turn still sends
  one page to the layout model. A failed batch reads that page alone. The assignment page shows, on every step, what the
  local worker is doing for it (requirement, 2026-09-25: `local_work`, GET `.../local-work`, `static/grading-background.js`):
  papers compared with the blank exams in the current round (`version_check_progress`) and blank pages read per template,
  as counts polled while anything runs, never a time estimate. A view: it queues, decides and changes nothing. A reader's heading that omits its number
  stands only where the independent OCR prints that number as a heading beginning with the same words
  (`locations.ocr_heading`, marked `number_from: ocr_heading`); a heading naming another number never does.
  Proposed areas span the sheet from a heading to the next printed problem or the
  page end (`locations.AREAS`); version that rule in the check input, never in the
  reading profile, so retained page receipts are reused instead of repeating OCR. The reading profile is an ALLOW-LIST (instructor,
  2026-11-06: "make the template-reading profile an allow-list"; `location_checks.READING_SETTINGS`): the local verifier model and
  backend and the timeout capped at 120 s, nothing else, so a preference that is no part of a reading (endpoints, concurrency,
  workspace, assistant, coding or training switches) never makes a later check on the same blank read its pages again. A stored
  check's `profile_json` stays as it was stored; only checks queued afterwards carry the new profile.
  A page's own notes from the reader hold for review only the questions found on that page (`locations.NOTES`,
  versioned the same way; requirement, 2026-09-25).
  A question's own warnings, a failed page and every other check still hold it.
  An explicit blank-template choice may cover the other questions of that printed
  version only while they have no response areas (`apply_to_all`); never switch or
  clear a question that already has regions or a confirmation as a side effect.
- A final answer is EVIDENCE, NEVER A GRADE (`grading/answers.py`, `answer_groups.py`).
  The rubric pays for the work and may have its own no-work line, so a key match
  must never select, add or remove a rubric line or approve anything. It only
  groups responses (answer, exact notation, work band, proposed lines) and raises
  review reasons when a proposal contradicts it. Keep matching conservative: no
  solving, simplifying or tolerance; unknown notation simply does not match. A
  key proposed from the rubric, or read by a model, is unused until it is saved. ANSWER KEYS SAVE THEMSELVES (instructor,
  2026-10-22, with the answer areas above; every key saved until then was the one Faculty proposed): the same step
  (`auto_prepare_one`) saves the key a question shows as "not saved yet" (`key_stands` 'unsaved': the rubric's proposal, else
  its latest reading under the current rubric) through the ordinary `save_answer_key` with actor `automatic`, and has the key
  of a question with a confirmed area, no saved key, no key its rubric proposes and no reading yet read by the ordinary
  reading (`_queue_key_readings`, request ID `auto-key:<location confirmation>:<question>`), for uploaded rubrics as for
  drafted ones. It never saves over a saved key, a proposal `clean_key` refuses, or a key of a question with a grade or a
  decision (a new question revision would send its grades back as new work), and nothing while a batch of the assignment waits
  or runs; Save this key, Save N answer keys and the editor stay for those and for any correction. For a quiz or makeup drafted by a
  model that save is the instructor's approval of the draft that showed the keys (requirement, 2026-10-11, "Carry the key
  through"; QUIZZES "THE KEY IS READ WHILE THE QUIZ IS DRAFTED"): `create_draft_question(answer_key=…)` stores the key in the
  question's first revision, only for an approval that says `save_keys`. Every other question starts without a key, as before. The one
  exception is the instructor's (2026-10-08): a multiple-choice question (the drafted rubric and its
  printed options), read confidently by the MULTIPLE-CHOICE READER (above), is graded from the
  marked option.
  Each question of Key & Rubric Finalization shows that proposal ("Read from the rubric · not saved yet") with **Save this key**,
  and **Save N answer keys** (named Confirm all until 2026-10-02) saves every question's shown key for the questions without one, through the ordinary `save_answer_key`
  (requirement, 2026-09-25: the step said "check the answer read from the rubric" without showing it). Because it is saved as shown,
  `key_from_rubric` reads a line's lettered parts ("a. ..., b. ...") as parts labelled by letter, "Not possible because ..." as the
  words "Not possible", never math inside an explanation (because/since/where, "with work") as an answer, and proposes NOTHING when
  any part cannot be read. Those questions can instead be READ FROM THE BLANK EXAM
  (instructor, the same day; `queue_answer_key_reading`, POST
  `.../answer-keys/read`, rule `KEY_RULE` `answer-key-read-v2`, prompt `answer_key_read-v2`; v2, requirement, 2026-10-11: the
  prompt is v1 word for word plus one paragraph, and the request (`key_request`) also carries the question's stored
  `worked_solution` where it has one, to be used instead of solving the problem again, a contradiction with the printed problem
  or the full-credit line said in that part's `basis`; a reading recorded under v1 displays as before, and a request ID queued
  under v1 and sent again is the same reading; the same contract (prompt, schema, `read_key`) reads a drafted quiz's or makeup's
  keys while it is drafted, `quiz_drafts._quiz_keys`, owned by the draft's revision and saved only by an approval that says
  `save_keys`): the rubric drafting and review model
  works each key out from the question's printed problem (its text read from the blank exam and a picture of its confirmed area)
  and the rubric lines, one request per question, only for questions without a saved key whose rubric gives none. Each reading is a
  `frq_rubric_generations` row of kind `key` (term plan, that version's template; no migration), kept out of the drafts, their
  defaults and guards. Its key is shown as "Read from the blank exam by <model> · not saved yet" with how each part was found, only
  while its rubric revision is current; Save this key, Save N answer keys and, since 2026-10-22, the worker (ANSWER KEYS SAVE
  THEMSELVES) save it through `save_answer_key`, and nothing else does.
  ONE PART, SEVERAL VALUES (2026-10-29): the prompt gives each value of a part its own entry labelled by the part's letter, so
  a part asking two things ("How many ... ? What is the maximum revenue?") comes back as two parts labelled "b". `read_key`
  numbers a label repeated with different answers in order ("b (1)", "b (2)"; a blank label "(1)", "(2)") instead of refusing the
  reading; the same label with the same answers twice, or a blank label beside lettered ones, is still refused ("Label each part of
  the answer key once."). Until then Quiz 6's draft lost Problem 4's key on Versions B, C and D (both attempts answered a, b, b, c)
  and the Quiz 2 makeup's Problem 3 on Version E ("", ""), the only four readings lost so (nine answers refused).
  A key is written as bare LaTeX (no $ signs): the page and the editor typeset each accepted answer in the renderer's bare mode
  (`keyMarkup`), and the editor previews each key under its box as it is typed; words ("Not possible") stay text. In the editor's
  text a `|` separates accepted answers only outside braces, brackets and parentheses, never after a backslash or inside
  `\left| … \right|` (`splitAnswers`; 2026-09-27).
  `save_answer_key` changes only key/solution text: keep the rubric pin and
  approval, and carry a confirmed location forward by an append-only receipt
  (never `save_question`, which unpins the rubric). Group approval requires the
  instructor's explicit look at each ticked response, excludes proposals hidden for a
  blind review, revalidates membership and versions, and writes one ordinary review
  per response.
- Instructor decisions of 2026-09-19 for cloud grading: (1) NO privacy constraints. There is no
  approval of cloud crops, masking, scrubbing, payload or retention rule, and none may block or
  slow a cloud batch or cause a failure or a review; do not add them back. Text is sent as
  transcribed. Faculty still owns the identity
  mapping; no roster field is sent because none helps grading. (2) Instructor corrections MAY be
  used for training and calibration. (3) Billable cloud actions need no separate go-ahead, and
  Faculty has NO spending cap of its own (requirement, 2026-09-23;
  `max_job_cost` is in `config.RETIRED`). (4) Cloud calls retry automatically by the policy below.
  (5) Cloud work runs in parallel. Credentials remain server environment only: an endpoint NAMES
  its key variable (`*_API_KEY`, never `FACULTY_*`/`MATHPIX_*`/`TYPESAFE_API_KEY`). A key bound to
  one service (`config.KEY_HOSTS`: OpenRouter, Vercel AI Gateway, Nebius or the server's
  `NEBIUS_GRADING_URL` host) is named only by an HTTPS connection to that host: saving a violation is
  refused (`validate(saving=True)`); a stored one still loads, reports `key_not_allowed_for_host`, and
  its provider refuses every request before anything leaves. Fully local endpoints are
  loopback. Native Ollama must use local vision weights and unload between worker turns. Every request
  to a fully local model first borrows the whole GPU from the embedding worker and returns it after
  (EMBEDDING "LENDS THE GPU"; requirement, 2026-10-11: "build the hand-off"): the two do not fit on
  the card together.
- **Faculty owns students and grades; cloud workers are a disposable execution layer.** Grading
  reasons in tiers (`local`, `cloud_standard`) and roles (reader, primary,
  second, verifier); vendors and models appear only in `grading/config.py` settings. The job
  `mode` CHECK cannot change, so a tier job is stored as mode `nebius` with `frq_jobs.tier`; the
  built-in endpoint `nebius` is an ordinary connection (its own mode was removed 2026-09-23). Every endpoint records its LINEAGE, and a fine-tune
  has its base's lineage. For batches frozen before `family-blind-v1` (2026-09-23) models of one
  lineage were one vote and the independent check had to be of another lineage; new batches ignore
  lineage, which is optional audit metadata (see Cloud above). `GradingService.cloud_provider` is the one seam to a
  vendor (`grading/providers/cloud.py`: `submit`, `poll`, `cancel`, `health`, `capabilities`;
  `HostedEndpoint`, `DedicatedGpuProvider`, `MathpixEndpoint`). Tests and the scratch app use
  `tests/fake_openai.py`; checks never reach a real host, and `manage_checks.environment` strips
  every cloud credential.
- Cloud model fields select OpenRouter or Vercel AI Gateway (Other connections retains custom/Nebius).
  Browse and recent dropdowns filter by provider; model identity includes the gateway URL, never just
  the model ID. Connections remain workspace settings, with no course/term/section owner. Public
  discovery uses fixed allowlisted URLs and separate caches; normal settings reads never fetch it.
  Vercel connections name server-only `AI_GATEWAY_API_KEY`, retain their catalog's reasoning controls
  and temperature support, and use Vercel host-routing options rather than OpenRouter-only fields.
  Keep this additional metadata absent from old connections so frozen profiles retain their hashes.
  Every OpenRouter/Vercel model field also has a **Host** dropdown (requirement, 2026-09-23).
  Its list comes from the gateway's public
  `/models/{model}/endpoints` (`ModelCatalog.hosts`, fixed gateway URL, validated model ID, ten-minute cache, GET
  `/api/grading/model-catalog/hosts`), read only when the instructor opens the control, never to draw the screen. The choice is
  the connection's existing `upstream`: OpenRouter's endpoint slug (`tag`, e.g. `deepinfra/fp8`, sent as `provider.order` with
  no fallbacks), Vercel's provider name (`providerOptions.gateway.order/only`); choosing a host also takes its prices. Browsing a gateway takes its CURRENT prices into the draft for every saved
  connection of that gateway (requirement, 2026-09-27): a pinned host's from its host listing, any other
  the catalogue's; an unlisted price or an unreachable catalogue keeps the saved one; nothing is saved until compute settings are. The host
  belongs to the CONNECTION, so every role using it changes together and the control says which roles share it.
  With token limits OFF every request still names a maximum when its connection knows one (requirement, 2026-09-24):
  a host's DEFAULT otherwise applies.
  `output_limit` is a connection FACT from the gateway
  (`model_catalog.output_room`): the chosen host's published ceiling, or for any host the smallest ceiling among hosts that allow at
  least `ROOM_FLOOR` (65,536), always leaving a tenth of the context for the prompt; 0 means read and not worth sending. Browse takes
  the catalogue's figure, choosing a host takes that host's, and "Read answer limits from providers" fills older connections; editing
  a connection's model or host drops it. Never send more than the host allows (OpenRouter drops such hosts, 404 when none is left, and
  refuses more than the context, 400; Xiaomi behind Vercel answers "Param Incorrect"): `submit` resends once without it on those
  refusals. Absent on older connections and frozen batches, which send nothing as before. With limits ON the instructor's caps apply.
- CLAUDE SUBSCRIPTION CONNECTIONS (requirement, 2026-09-27: "use agent SDK in the compute settings instead of making openrouter
  or vercel API calls", "for the grader side too", and "bundle responses together (same version same question) and have them graded
  simultaneously in the same call"; `grading/providers/agent_sdk.py`, kind `agent_sdk`, rule `agent-sdk-bundles-v1`). Compute
  settings' provider "Claude subscription" makes a connection with no URL, key or price that runs the installed Claude Code binary
  headless (`claude -p`, stream-json, `--json-schema`), signed in through Anthropic's own flow: Faculty never reads, stores or passes
  the sign-in, and the subscription subprocess gets a plain environment (no `ANTHROPIC_API_KEY`), no tools, MCP,
  settings, skills or CLAUDE.md, no saved session, and a scratch folder under TMPDIR.
  Anthropic's terms say Pro/Max limits assume ordinary individual use and allow enforcement without notice; the instructor chose this knowing that. It bundles by default
  (`bundle_size` 6, below) with at most `sessions` calls at once (its concurrency: a typed number from 1 to 256, default 2, beside the model
  in Compute settings and in the connection editor; requirement, 2026-09-28); the limiter lets in exactly `bundle_size ×
  sessions`. A reached usage limit (429, `usage_limit`) pauses the connection until Anthropic's reset time without halving the
  limiter, and never counts toward the busy ceiling (`max_retries × 10`; 2026-09-28), however long the reset takes. Cost is 0 ("included in the Claude subscription"), never "unpriced". Checks never reach the binary (`manage_checks`
  points `FACULTY_CLAUDE_CLI` nowhere); tests use `tests/fake_claude.py`.
  **CLAUDE API FALLBACK** (requirement, 2026-10-25: "add api usage fallback for claude when a subscription model runs out of usage";
  chose "Direct Anthropic API"). A Claude connection may opt in with `api_fallback: true`, a workspace preference frozen with
  grading and rubric-work profiles. Absent or off preserves the earlier behavior and profile shape. Only a confirmed subscription
  usage-limit rejection before any answer/tool activity can switch that request to the same Claude model through direct Anthropic
  API billing. While the subscription is paused, subsequent requests use that API; after its reset they try the subscription first.
  The fallback subprocess alone receives the configured `ANTHROPIC_API_KEY`; keys remain in Faculty's existing credentials file,
  never in settings, arguments, receipts or logs. An unscoped API key also needs `ANTHROPIC_WORKSPACE_ID` in that credentials file;
  only the validated workspace ID is sent as the fallback's `anthropic-workspace-id` header. Missing workspace errors stop with
  that setup instruction rather than retrying. Missing keys leave the ordinary subscription wait intact. Capacity errors, sign-in
  errors, API rate limits and uncertain or partially executed requests do not trigger a fallback. Cancellation and the existing
  unknown-outcome policy still apply. Each original cloud-call receipt records the switch, actual model, usage and the SDK's
  estimated API cost (split once across bundle members); a missing cost is unknown, never free. This setting applies to grading
  and rubric work, not the Assistant chat or coding engine. Existing completed grades and frozen profiles do not change.
  THE PLANS' MODELS ARE LISTED LIVE (requirement, 2026-10-01: Sonnet 5.5 and GPT-6.1-Sol were out and Browse did not show them, "load
  the list live"). Browse and its Refresh ask the plan's installed program which models it offers now (`model_catalog.subscription_listing`,
  GET `/api/grading/model-catalog?provider=claude|chatgpt`): Claude Code's `initialize` answer (`agent_sdk.listed_models`, run with a
  grading call's isolation, its aliases folded into the models they name) and Codex's `model/list` (`codex_sdk.listed_models`,
  `codex app-server` with no MCP servers and every acting feature off, models Codex hides left out). No model runs, no thread starts,
  nothing of Faculty's is sent and nothing is kept; a settings read never asks. Each model takes the reasoning levels its program offers,
  limited to `config.EFFORTS` (a Claude model without effort levels gets none), and only IDs the connection's kind accepts are listed.
  When the program is missing, signed out, silent for `agent_sdk.LIST_SECONDS` or names nothing usable, the built-in lists
  (`agent_sdk.MODELS`, `codex_sdk.MODELS`, `antigravity.MODELS`, mirrored in `grading-model-picker.js`) stand in and the picker says why.
  CHATGPT SUBSCRIPTION (requirement, 2026-09-27; kind `codex_sdk`,
  `grading/providers/codex_sdk.py`, a subclass of the Claude one sharing bundles, the whose-answer check, concurrency, the pause and
  zero cost; `config.SUBSCRIPTION_KINDS`). It runs `codex exec --json` signed in with ChatGPT through
  OpenAI's own flow (Faculty checks only that `auth.json` exists), in a fresh empty folder with the READ-ONLY sandbox, `--ephemeral`,
  `--ignore-user-config`, `--ignore-rules`, web search off and every tool that could act switched off by name (`codex_sdk.DISABLED`:
  apps and connectors, plugins, browser and computer use, image generation, shell and exec, sub-agents, skills, hooks), with a plain
  environment (never `OPENAI_API_KEY`). The instruction opens the prompt; images go as `-i` files in
  order with "[image N]" markers where each belongs; `--output-schema` carries the contract. Models and each one's reasoning levels
  come from Codex's own list (above); `codex_sdk.MODELS` stands in for it and gives a connection saved without levels its levels;
  "ultra" (automatic task delegation) is never offered.
  A usage-limit message pauses only the ChatGPT connection for the time it names
  (else 15 minutes). A service AT CAPACITY ("Selected model is at capacity", Claude's "overloaded"/529) is a busy host, not a
  failure (2026-09-27): status 529 with `capacity`, waited for with a backoff shared by the connection (requirement, 2026-09-28: 30 s, doubling per busy
  answer while nothing succeeds, to 15 minutes; a success starts over; nothing is sent during the wait) and asked
  again, without halving the subscription connection's limiter. Checks point `FACULTY_CODEX_CLI` nowhere; tests use `tests/fake_codex.py`.
  GEMINI SUBSCRIPTION (requirement, 2026-10-14: "add my google gemini subscription to the available model choices for faculty";
  kind `antigravity`, `grading/providers/antigravity.py`, a subclass of the Claude one sharing bundles, the whose-answer check,
  concurrency, the pause, the capacity backoff and zero cost; provider "Gemini subscription" in Compute settings). It runs Google's
  Antigravity CLI headless (`agy -p`, stream-json in and out, `--json-schema`), signed in with Google through Google's own flow
  (Faculty checks only that the sign-in file exists), with a plain environment (never `GEMINI_API_KEY`) and its self-update off
  (`AGY_CLI_DISABLE_AUTO_UPDATE=true`; measured: `1` does not stop its updater).
  Three rules are its own, each measured on 2026-10-14 with invented prompts (agy 1.3.4).
  ITS OWN HOME: `agy` cannot run without saving (every run keeps its transcript and a copy of each picture viewed under
  `$HOME/.gemini`, about half a megabyte), so it NEVER runs in the instructor's own home. Its HOME is a folder Faculty made
  (`antigravity.home`, `prepare`: `/mnt/nvme/Teaching/runtime/antigravity` set by `create_app`, else `FACULTY_ANTIGRAVITY_HOME`; 0700,
  marked, never the account's home and never a folder holding what Faculty did not put there), where the instructor signs in ONCE
  (`HOME=<that folder> agy`; a connection without it says so with the command and starts nothing). A run's conversation is deleted
  when the run ends, answered, refused or cancelled, and everything runs keep there is swept once no run of any Faculty process is
  in progress (a lock on the folder); the sign-in and the program's settings stay. Faculty adds ONE line to those settings, the
  permission for its own tool server (`antigravity.ALLOW`, `mcp(faculty/*)`: ASSISTANT "GEMINI SUBSCRIPTION"; a grading agent has
  no such server), and nothing else (measured: the program removes a telemetry key written into its settings file, so that choice
  is made in the program itself). The folder is runtime work: never backed up.
  ITS OWN AGENT: `agy` has no flag for a system prompt or for switching off its sixty tools (shell, browser, web search, file writes,
  sub-agents). Each call writes an agent definition into its scratch folder (`.agents/agents/faculty.md`) and runs as it: the body
  is the instruction and replaces the program's own (a 59 KB body arrived whole), and its tools are `finish` (how `agy` returns the
  schema's answer; without it there is none) and, with pictures, `view_file`. No other tool is offered to the model; rule files
  above the scratch folder do not reach it and one run sees nothing of another (both measured). Never `--dangerously-skip-permissions`.
  PICTURES AS FILES: the prompt takes text only, so pictures are files in the scratch folder, marked where each belongs
  ("[image 3: image-3.png]"), which the model views. An answer is ACCEPTED ONLY when the run's own events show every attached picture
  viewed and nothing done but viewing those files and finishing; an action `agy` refused for want of permission (`denied_actions`)
  refuses the answer too. A refused answer is malformed: asked again like any other, its conversation deleted all the same.
  Models are named without their reasoning level (`gemini-4-argon` with `--effort`; `agy models` lists one name per level, folded
  into the model with its levels by `listed_models`, Browse's live list as above; `antigravity.MODELS` stands in). The Google plan
  also serves Claude and GPT-OSS models, which a connection may name (GPT-OSS reads no pictures). An enum holding an empty string is
  sent as a string naming its choices (`accepted`: Gemini refuses it, 400; 37 of the 39 contracts went unchanged), Faculty's own
  validation unchanged. A usage limit pauses the connection until the reset `agy` reports for the model's group (`/usage`: a weekly
  and a five-hour limit for Gemini models, and for Claude and GPT models; else 15 minutes). NOT ON THIS CONNECTION
  (`config.TOOL_KINDS`): drafting tools (its drafting requests go as one request each, as on any API connection) and the coding
  agent; saving it as the coding agent is refused and its picker does not offer it. THE ASSISTANT DOES run on it (instructor, the
  same day: "now make it work for the assistant too"; ASSISTANT "GEMINI SUBSCRIPTION", `assistant/engines.AntigravityEngine`).
  Checks point `FACULTY_ANTIGRAVITY_CLI` nowhere; tests use `tests/fake_agy.py`, which keeps a conversation in its HOME as the
  real program does.
- DRAFTING TOOLS ON THE SUBSCRIPTION CONNECTIONS (requirement, 2026-10-01, after asking whether the other places that use models should
  get a harness with tools like the assistant's: "go ahead and build the quiz drafting, rubric drafting, and question bank changes you
  recommended make them work for claude and codex depending on the model choice"; `grading/providers/tools.py`, `tool_proxy.py`,
  `grading/drafting_tools.py`, `tests/test_drafting_tools.py`). When the Rubric drafting and review model is a Claude or ChatGPT
  subscription connection (`tools.offered`), these drafting requests carry tools: the quiz writer and its fix rounds (`compile_quiz`,
  `look_at_pages`, and since `quiz-writer-v3` `lecture_notes` and `question_bank`: instructor, the same day, "add them to the quiz
  writer"; QUIZZES), the rubric surveys (`look_closer`), the rubric writers of a draft, an all-versions draft, an adaptation and
  a drafted quiz (`look_closer`, `lecture_notes`, `question_bank`, `past_rubrics`), and the question bank's PDF reader (`pdf_page`;
  QUESTION-BANK). THE RUBRIC CHECK too (instructor, the same day: "give the rubric check the same tools too"): one question, a
  problem's versions together, and resolving its concerns get the rubric writers' four tools (`rubric-reviewer-v1`, its own guide:
  judge these rubrics against the methods the course teaches and how the instructor grades similar work), `look_closer` reading the
  blank template each question is printed on, a page only when asked (`TemplatePages`); grouping the versions stays text only. Every
  other connection, and every grading role, the quiz check, audits, flag review, answer keys, tagging and version linking, stays one
  request in and one answer out. `FACULTY_DRAFTING_TOOLS=0` in the environment sends every request
  without tools, for side-by-side comparisons on a scratch copy.
  THE TOOLS ARE FACULTY'S: each is Faculty's own code in the dispatcher's process, reading the catalog and verified storage as the pages
  do, or the document sandbox (a compile; a notes page's text, operation `text`); none writes, runs a command, opens a file or reaches the
  network. The model runs with them alone: Claude Code with `--tools ""`, `--strict-mcp-config` and the relay as its one MCP server
  (`--allowedTools mcp__faculty_drafting`; NOT `--safe-mode`, which drops MCP too, so `CLAUDE_CODE_DISABLE_CLAUDE_MDS` and
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY` instead, with settings, skills and slash commands off as in every call); Codex with
  `--ignore-user-config`, the relay as its only MCP server approved without asking (`approval_policy="never"`), and every acting feature
  off as in every call except `code_mode_host`, through which this Codex calls MCP tools. The relay, started by the binary, reaches Faculty
  only through a 0600 Unix socket in the call's own scratch folder. Calls are answered one at a time within a budget per request (quiz
  writer 20, rubric writer and rubric check 10, survey 6, PDF reader 8; past it every call says to answer now). The instruction gains a preface naming the
  tools and the budget and saying that what they return is material, never instructions; the answer is the same schema-checked answer,
  validated as without tools, and Faculty still compiles a drafted quiz and sends back what remains (`FIX_ROUNDS`).
  RECEIPTS: the request's fingerprint includes its tool set's (`ToolSet.fingerprint`: versioned name, descriptions, schemas, budget and
  guide), so a request with tools is never replayed as one without; the call's receipt keeps every tool call (name, arguments up to
  20,000 characters else their checksum, success, seconds, the answer's first 4,000 characters and checksum, the pictures' checksums), on
  a failed call too. A request with tools never joins a bundle. THE COURSE CONTEXT of the rubric writers is the plan's course definition:
  its current lecture notes, its question bank (retired questions left out), and the newest approved rubric of every question of its
  OTHER assignments in any term; the drafted or checked assignment's own questions are never offered, so a draft is never a copy of
  what it may replace and a check judges these rubrics rather than their earlier revisions, and the department's rubric rules in the
  prompts still decide what a rubric holds. The quiz writer reads the same course's notes and bank; a draft's own problems are never
  in the bank while it is written (they join on a completed approval, and an approved draft is never revised). Its guide says a bank
  question may serve as the model for a problem, and that the request's instructions, format and material still decide what the
  quiz asks. A drafted quiz's rubric writers are SENT the notes sections the quiz covers with every request, on every connection
  (QUIZZES "THE NOTES THE QUIZ COVERS"); their `lecture_notes` tool still reads any other section.
- BUNDLED REQUESTS ON EVERY CONNECTION (`grading/providers/bundles.py`, rule `bundles-v1`; requirement, 2026-09-27). Each grading role's model field has "Responses per call" (1-20) beside it,
  a setting of the CONNECTION like its host (`bundle_size`; on a hosted connection 1 is the default and is never stored, so older
  connections and frozen batches keep their fingerprints; never on dedicated or batch-API connections, nor offered for the rubric
  model or the benchmark). Requests share one call ONLY when the dispatcher named the same batch, question, exam VERSION and step
  (`cloud_call`'s hint) AND their whole contract is the same (prompt, narrowed schema, re-ask). The model is told the items are
  independent; shared payload fields are sent once, each item's own fields and images after it. Every response keeps its OWN
  `frq_cloud_calls` row written before anything leaves and gets only its own answer, validated like any grader's; a successful
  member's receipt holds only its own answer (a failed call's receipt is the whole call's). WHOSE ANSWER (requirement, 2026-09-27): what each
  answer says the STUDENT WROTE (`bundles.CONTENT_KEYS`: transcript text/latex, final answers, quoted evidence; never the grader's
  own explanations or a reader's layout notes) is compared with what each
  item's student wrote (never the shared question, rubric or key; identifier fields and UUID/hex strings excluded; "19,000" is
  19000), counting only words some items have and others lack (rule `bundles-v3`); an answer matching another item clearly more
  than its own (`bundles.matches`: at least 5 telling words, own share under .5, another's .25 higher) is refused as that response's
  refused answer and asked again; fewer telling words decide nothing. A request with no text
  of its own (the reader's free-text pass over one crop) is therefore never bundled. The check refuses only, never reassigns.
  A failed call fails each member under the ordinary policy; a missing or malformed item is that response's refused answer; an answer cut off at its output limit is asked
  again in bundles half the size for an hour, never given up on. On an API connection a bundle is one chat request through every
  usual correction; its answer budget is the connection's cap per response × items; `max_inflight` counts requests (the limiter lets
  in `max_inflight × bundle_size` responses); usage and a reported cost are split evenly over the members. Among responses equally
  far along, the dispatcher claims one question's together so bundles fill.
- Crops (2026-09-23, requirement): a new attempt at a response REFERENCES the crops of an earlier attempt at the same
  response with the same `source_sha256` and the same `extraction.VERSION` (`jobs.reusable_crop`, `reused_from`) instead of
  cutting and storing all five images again; a changed cut gets a new VERSION. The cloud reader gets each crop as scanned,
  then its contrast view (never the same image twice); a crop with privacy masks still sends only its masked cloud copy.
- Tilted scans (requirement, 2026-09-23). `grading/deskew.py` reads a page's tilt from its own
  printed lines (row profile, no template). A page tilted at least `MINIMUM` (1 degree) with a
  `CONFIDENT` reading is ALSO tried straightened, compared only where the scanner captured paper (`deskew.masked`), with the
  residual searched in tenths (`FINE`), and used only when it aligns better than the page as scanned: the reading only
  proposes, the blank exam decides. Crops (since `frq-extraction-v2`, metadata `deskew`,
  `alignment_quality`) and version checks (since `whole-paper-v2`) do this; pages under 1 degree take the old path with the
  scanner's own pixels, and retained scans never change. A version decision `version_match.EARLIER` reached stands
  (`paper_version`); only undecided papers are compared again. Keep the measured thresholds unless re-measured; a changed
  rule gets a new `deskew.RULE`, extraction VERSION and MATCHER. Straightening cannot restore corners a letter-size scan never captured.
- Sheets fed UPSIDE DOWN (requirement, 2026-09-25).
  Scan intake reads such pages (labels, bubble sheets and first-page OCR try either way up) but never turns the retained
  raster, so grading turns them itself: every page is ALSO tried turned half a turn (`deskew.upside_down`: exact, the
  scanner's own pixels, combined with straightening), and the blank exam alone decides (`deskew.turned_wins`: the turned
  page must lead the page as scanned by `TURN_MARGIN` 0.06, half the smallest real lead measured on Test 1 and Quiz 2 pages; no pass mark). Crops
  (since `frq-extraction-v3`, metadata `orientation` with both qualities) are then cut from the page turned upright; version
  checks (since `whole-paper-v3`, `turned` in the result) decide each page's way up ONCE, against the blank it resembles most,
  and score every version that way up. A page the right way up is cut and scored exactly as before; without a blank exam
  nothing is turned over. Quarter turns are never tried. The manual exam-workflow
  grading screen shows the page as scanned and aligns nothing.
- Paper printed at ANOTHER SIZE than its blank (requirement, 2026-09-25). `deskew.search` (`size-search-v1`) first aligns the blank at its OWN size over the usual angles, exactly the
  earlier search, then tries it resized about its centre (`deskew.resized`) at `SIZES` 0.86 to 1.10 in steps of 0.02 at
  that angle, the angles again at the best size, and `FINER` steps of 0.005 to 0.015 around it. Another size is used only
  when it aligns better than the blank's own size by `SIZE_GAIN` 0.05; when no coarse size leads by at least half of that,
  the search stops there, so a page at its blank's own size is cut and scored
  exactly as under v3. Crops (`frq-extraction-v4`) scale the answer areas, the ink view's blank and the search
  with the chosen size and record `template_size` (`size`, and `own_size_quality`); version checks (`whole-paper-v4`) score
  each blank at its best size. No version threshold changed: Quiz 2's versions differ only in their numbers, so the comparison ranks the right one first (50 of 51 papers, 28 before) but never clears `MARGIN`, and its versions come from the printed letter. Papers an earlier matcher decided keep that decision (`version_match.EARLIER`), and crops cut under v3
  are never reused for a v4 attempt. `SIZE_GAIN` and the ranges are measured like `TURN_MARGIN`: never change them by guess;
  a change gets a new `SIZE_RULE`, extraction VERSION and MATCHER.
- Two worker lanes. The single local GPU stays serial under the Inbox execution lock, one bounded
  component per turn, interrupted work failing until retried explicitly. Cloud work (requests,
  polling, results, Mathpix, crop preparation for cloud batches) runs in `grading/cloud.py`'s
  dispatcher thread beside the worker loop under its OWN cross-process lock; never take the
  execution lock for it and never serialize it. `frq_tasks.lane` owns a response's next component.
  A FINISHED BATCH IS NOT CANCELLED (2026-10-02): a Cancel click that lands after the batch finished once turned it into a
  cancelled batch, which paused automatic grading and, for a batch that finished with failures (status `failed`), took away
  the Retry of those failures and made them new work. `cancel_job(jid, seen)` refuses (409) a `review_ready` batch, and a
  `failed` batch when `seen` (the status the batch card showed; the card's Cancel posts it) was `pending` or `running`; the
  batch keeps its status, its Retry and automatic grading. Pending and running batches still cancel; a failed batch still
  cancels when asked without a card that showed it working (its failures become new work); cancelling one already
  cancelled answers as before.
  CANCELLING CLOSES WHAT IS IN FLIGHT (requirement, 2026-09-23: no model may keep running after a cancel). `cloud_call` runs
  every request under a `providers.base.Abort` (the socket is registered when the connection opens); the dispatcher's
  `_stop_cancelled` (about once a second) shuts the connections of requests whose batch (`cancel_requested`), rubric review
  or rubric draft (`cancelled=1`) was cancelled, and a request still waiting for its slot stops there. It raises `Cancelled`,
  never a ProviderError, so no retry policy or optional step swallows it: the call is `cancelled`, the task `cancelled`.
  A whole batch may run at once (requirement, 2026-09-23): up to `cloud.MAX_UNITS` (256) components, set by `concurrency`
  and each connection's `max_inflight` (both up to 256); a component holds its thread while its model answers or waits
  for a slot. Crop cutting stays bounded apart (`jobs.CUTTING`, cores less two). FULL CONCURRENCY (workspace
  `full_concurrency`, off by default) lets every component start as soon as it is ready: `concurrency` and `max_inflight` are
  set aside (up to `cloud.FULL_UNITS`, 5,000); a 429 still halves that connection to what it had in flight, Mathpix keeps its
  per-minute pace and CUTTING still holds. The dispatcher reads it every round; it is never frozen into a batch or its scope.
  Catalog writes keep the lifecycle lock; `frq_steps UNIQUE(task,stage,attempt)` with
  `INSERT OR IGNORE` makes results idempotent: the first valid result is applied, a superseded
  request's late result is a receipt on its call, and no response ends with two grades.
- Cloud retry policy (requirement, 2026-09-19): every request is a `frq_cloud_calls` row written
  BEFORE it leaves. A CONFIRMED failure (the provider's error answer, a failed/cancelled/timed-out
  job, a refused connection, a malformed answer) retries automatically, but never
  before its confirmation is recorded, up to the attempt cap (`max_retries`), then waits for the
  instructor; 401/402/403, a 400 whose host says the request's parameters are INVALID (after the
  effort correction below; 2026-09-24), and an answer
  TRUNCATED by its output budget stop at once (a refused answer's cost is still
  recorded on its call and the batch). So does an answer the GATEWAY ENDED AT ITS MAXIMUM STREAM DURATION (requirement, 2026-09-27:
  Vercel's `gateway_stream_timeout`): `base.STREAM_DURATION`, marked
  `stream_duration` on the call. A primary or second grader cut off that way FAILS its response (listed under Failed to grade), not
  a missing opinion left with the instructor, and the batch's Retry asks that grader again (`committee.cut_off`: a cut-off step of an
  earlier task attempt is asked anew); a ruling cut off that way leaves the response as the team left it, like any failed ruling. An UNKNOWN outcome (dropped, timed out, restart) is
  recovered by the provider's durable call ID where one exists, otherwise resent at most
  `unknown_resends` (2) times. A gateway timeout (HTTP 408/504/522/524) is an UNKNOWN outcome. A durable
  job is polled a bounded number of times: a busy host is waited for, a job gone (404/410) or an
  unfixable poll failure settles the call `unknown` at once, other poll failures after `POLL_FAILURES`;
  a job the host fails, cancels or lets expire is a confirmed failure, and `cancelling` is still in flight. Do not loosen these or retry before the record exists. A refused
  answer (not JSON, or outside its contract) is asked again with a little sampling (temperature
  .3, on the receipt); every FIRST request is deterministic. Mathpix reads are also paced per
  minute (`mathpix_per_minute`). The
  dispatcher claims the responses that are furthest along first; do not return to stage-by-stage order across a whole batch. Within one stage the response with more steps done
  goes first (2026-09-24). Two things measured
  live on 2026-09-20 are NOT failures and must not become ones again: an aggregator's 404 for
  the advisory `reasoning: {enabled: false}` hint under `require_parameters` (`HostedEndpoint.submit` withholds the hint and
  sends the same request once, while any other 404 stays a confirmed failure; a host's 400
  "Reasoning is mandatory ... cannot be disabled" for that hint is answered the same way, by
  sending the request once as a reasoning model with its budget), a `temperature` no host of the model
  accepts (2026-09-21; the router's PARAMETER refusal makes `submit` try each advisory field
  alone, then both, keeping only what helped, and the step records `temperature: null` with a note; the
  output contract and the instructor's token cap are requirements and are never withheld), and a reader's
  layout note naming a line that was not transcribed (dropped and counted in `layout_notes_dropped`
  for the audit; never refuse and re-bill a whole reading for it, and never raise it as a doubt).
- THE CANARY WAS REMOVED (requirement, 2026-09-28). It regraded the earliest approved responses before each cloud batch and stopped a batch whose agreement dropped. Nothing queues, claims, gates
  or settles a canary run any more (`grading/canary.py` is gone; the dispatcher no longer holds a batch behind one), and saved
  settings carrying `canary_enabled`, `canary_size` or `canary_margin` still load (`config.RETIRED`). What it recorded stays: the
  `frq_canary_runs`, `_items` and `_baselines` tables, the `frq_cloud_calls.canary_item_id` column and their receipts, and an
  earlier batch's card still shows its canary runs (`job()['canary']`). Do not bring it back unless asked. Estimates are shown before submission as
  information only: nothing is refused or paused for its cost; a request a gateway refuses for credit
  (402) fails at once and is never resent. Calls record usage and cost. A connection with no configured price has an UNKNOWN
  cost, never zero: the estimate then says it is unknown. `retry_job` retries each failed
  response on its own, reports what it skipped, and never replaces a later batch's result; a reset
  restore skips responses with a newer result.
  Workspace `token_limits` (default on) sends each connection's own `max_tokens`
  and `reasoning_tokens` as request stop conditions; they are not prompt text.
  Off omits those caps for every cloud model. The numbers belong to the connection,
  so any OpenRouter team combination keeps its own budgets.
  A hosted model request has NO client timeout (requirement, 2026-09-23). `cloud_call` passes
  `timeout=None` for `kind: hosted`; `request_json`/`request_stream` set TCP keepalive
  (`base.KEEPALIVE`) so a dead connection still ends as an unknown outcome.
  Mathpix, dedicated GPUs, local models and the restart wait keep the workspace
  `timeout`. Do not bring a wall-clock limit back for hosted models. Every hosted chat
  answer is STREAMED (instructor, same day). `base.request_stream` asks for
  the stream (with `stream_options.include_usage`, except on OpenRouter) and `assemble_stream`
  rebuilds the ordinary completion, so parsing, receipts, costs and request hashes are
  unchanged. Its output limit counts the assembled answer, never the stream's repeated
  chunk envelope (counted, 8 MB was a hidden ~70,000-token cap on OpenRouter's MiMo, 2026-09-23). A stream that ends without its finish
  is an UNKNOWN outcome; an error chunk is a confirmed failure. Do not go back to
  non-streamed hosted requests.
- Exam versions behind one label sheet (`grading/exam_versions.py`, `version_match.py`): the
  SEATING CHART is the source of a paper's version (label → copy → student → seat →
  `seating_exam_versions`), its page 1 printing that version (FIRST PAGE, below) or else the whole-paper comparison with each
  version's blank exam confirms it, and only `confirmed` or instructor-decided papers are graded. Never switch a version
  silently, never grade an unresolved paper, never decide from one page. `frq_questions.exam_version`
  (`''` = no versions) is set once; everything that depends on the exam is per version; groups,
  benchmark and the acceptance gate never mix versions. Do not change the measured
  thresholds without new measurements. A version no seating chart handed out (2026-09-24) is added in the rubric upload dialog (**Add Version C**) and then has its questions and
  blank exam like any other. Without CSVs (rubrics drafted from blank templates; requirement, 2026-09-25)
  **Add Version C** in the Assignment versions panel records it BEFORE any question exists
  (`frq_added_versions`, migration 025, term plan, append-only, latest row per letter; only the next
  letter; removable while it has no questions and no draft is running), so it has a template slot and
  is drafted with the others. It only extends the list of versions (`version_letters`, `exam_versions.added`):
  unlike `frq_declared_versions` it never changes how a paper's version is decided. Papers without a seat (DR-center, makeup) need the instructor's
  decision unless their page 1 agrees with the comparison (NO SEAT, under FIRST PAGE below); **Confirm proposed versions** lists the papers the comparison proposed a version for and
  records each one the instructor TICKS through the ordinary per-paper endpoint. Nothing is preselected. Both dialogs
  show the paper itself (requirement, 2026-09-25: `paper_pages`, the reviewed paper's scanned pages in order, cut on request
  by the scan's page-image route): Decide every page, Confirm proposed versions each paper's first page.
  A plan with DECLARED versions (an assignment created from a scan, above; requirement, 2026-09-24) has
  no seat to go by: the version read on the paper's registered first page (`printed_version`, the
  title-box OCR) DECIDES when it is one of the declared letters (`confirmed`, with `printed`). No blank
  exam is compared, since one may not exist. No version read, or another letter, is `unresolved` and
  waits for the instructor's per-paper decision. A seat, where one exists, still comes first.
  REGISTERED IN THE INBOX (requirement, 2026-09-26). The
  instructor's own Register click on Students / Matched papers confirms the printed version the Inbox showed beside the paper
  (`registered_version`: the registration's reading and its `scan_unlabeled_plans.actor`; `automatic` confirms nothing) when it
  is one of the assignment's letters (graded, seated, declared or added), EVEN AGAINST A SEAT: `confirmed` with `printed` and
  `registered`. The blank-exam comparison still runs where blank exams exist and is waited for; only a clear match with ANOTHER
  version holds the paper (`mismatch`, naming both), while no blank, a failed comparison or no clear match confirm it. A later
  per-paper decision in grading still comes first, and a declared plan without a seat keeps its own rule above. Papers waiting
  for a version decision keep the graph's first step and Grade unfinished ("Decide versions", `workflow.version_decisions`) and
  count as unfinished grading work (`decision_responses`, in `progress.grade`).
  FIRST PAGE (requirement, 2026-10-25: "it should just read the first page of their exam booklet to confirm because it literally
  says the version"; the whole-paper comparison took about 40 s a paper, 3 hours for Exam 2's 312). A paper with a seat whose
  letter has questions in the plan, and no Inbox registration naming another letter, is first confirmed by its page 1: the local
  OCR reads the title box the cover reader uses (`unlabeled.TITLE_BOX`) and the footer (`exam_versions.FIRST_PAGE_BOXES`),
  `FIRST_PAGE_BATCH` papers in one model load, before any whole-paper comparison (`first-page-version-v1` rows of
  `frq_version_checks`, input naming the seat's letter as `expected`). A line that looks like a seat label (seat, section, copy or
  a middle dot) is skipped, since a label prints the SEAT's version; exactly one letter read, and the seat's, confirms the paper
  (`confirmed`, `first_page`). Another letter, two, none, or a failed read compares the whole paper as above; where page 1 read
  another letter AND the whole paper clearly matches that letter's blank exam, the booklet says which it is twice over and the
  paper is `confirmed` as that version (`first_page`, with `seat` and `matched` kept): on Exam 2, 68 of 312 booklets differed from
  their seats, and the whole-paper comparisons checked against them (4 of 4) agreed with page 1. Page 1 and the pages disagreeing still wait for the
  instructor (`mismatch`). A paper compared before FIRST PAGE whose pages match another version has its page 1 read before it
  waits. A
  comparison already decided stands, so no certified paper changes. A whole-paper comparison still waiting for a paper whose
  page 1 agreed is set aside (`failed`, `exam_versions.NOT_NEEDED`, not counted in the progress), and queued again should page 1
  stop settling it (a changed seat).
  NO SEAT (requirement, 2026-10-26, of 12 Exam 2 papers with no seat whose pages matched Version A 67-72% against B 44-49%: "why aren't
  the versions being assigned"): a paper with no seat is compared whole first; when its pages clearly match one version, its page 1
  is read (Jev, then the OCR, input `expected` null so its reading agrees with nothing and sets no comparison aside), and page 1
  printing that same letter confirms it (`confirmed`, `first_page`, `matched` kept). Page 1 naming another letter, none, or a
  failed read leaves it `proposed`. JEV FALLBACK (instructor, same day: "Why not ask Jev on the first page as a fallback if this
  way fails"): a paper with no seat whose comparison FAILED or matched no version clearly takes the one letter JEV read on its
  page 1 (both orders agreeing on one of the plan's letters; `confirmed`, `first_page`, `jev_alone`). The OCR's reading alone,
  Jev's split or "none", or a failed read leaves it `unresolved` for the instructor. Pages clearly matching one version and Jev
  naming another still wait (`proposed`): neither overrules the other.
  JEV PAGE ONE (requirement, 2026-10-25: "test using jev to differentiate the versions on that first page and the old way as a
  fallback. if that works well, change it to that"): Jev reads page 1 FIRST (`grading/jev_versions.py`, the whole page, the plan's
  letters and "No version is printed" asked in both orders, a Jev receipt of purpose `paper-version` owned by the check); both
  orders choosing one of the plan's letters is the reading (`reader` `jev`), and the rules above apply to it as to the OCR's. A
  split, "none", a failed or unavailable Jev leaves the page to the local OCR read above, and that to the whole-paper comparison.
  Measured on the live scans that day: Exam 2 312 of 312 and Exam 1 338 of 338 first pages right (three makeup Version C among
  them), both orders agreeing at confidence 1.0, about 0.3 s a page, none wrong or split.
- Cloud grading teams live INSIDE the existing `grading` stage (`grading/committee.py`,
  `grading/escalation.py`); no parallel task stages or new data owners. New jobs freeze
  `grading-teams-v1` in their profile; jobs without it retain the legacy path.
  Cloud starts with the Standard reader/team (High Assurance, the second cloud mode, was removed;
  below). Primary and secondary grade independently. Every Cloud batch now freezes the arbitrator
  (`final-arbitrator-v2`, below), so NO new batch buys a third assessment: its frozen `third_rule`
  decides only which responses go to the arbitrator (`arbitrator_reserved`). The purchase rules
  below govern batches frozen without the arbitrator, which finish as they ran.
  A third assessment is BOUGHT ONLY WHERE A THIRD VOTE CAN SETTLE SOMETHING (instructor,
  2026-09-21; `committee.third_votes`, frozen per batch as profile `third_rule`): the pair alone does
  not resolve (they differ on a line, the second is missing, failed or unscorable, or, in a batch
  frozen on `third-vote-v1` or `third-vote-v2`, both share a lineage). Doubts no vote can settle (rubric wording, a reader's ambiguity, a reading problem the
  second grader reports, alignment, reported confidence, a grader's own request for a look,
  contradicting evidence) still flag the response and
  block acceptance and are kept on the check step as `noted`; they are never a purchase order.
  `third-vote-v2` (requirement, 2026-09-20) does not buy when both scores already equal the question
  maximum; same-score partial credit still buys. The third settles by matching one side on every
  credit-bearing line; a zero-point-only split is not "neither."
  `third-vote-v3` (requirement, 2026-09-22) narrows the purchase to ONE case: the
  two graders DISAGREE. A pair that never compared (a missing, failed or unscorable assessment) is not
  a disagreement, nor, in a batch frozen before `family-blind-v1`, is an agreement inside one model
  family (since `family-blind-v1` such an agreement simply counts as an agreement, above). Nothing is
  bought for them; they keep their flag, stay out of automatic finalization, and the instructor takes
  them in the next step of the pipeline. `decide` then says on the record what the pair itself left. Everything else is unchanged: the flags, `noted`, the v2 full-credit exemption, the
  credit-bearing comparison (`committee.credit_only`, v2 and later). New batches freeze `third-vote-v3`; a batch without
  `third_rule` or frozen on `third-vote-v1`/`third-vote-v2` finishes under the rule it ran with.
  Keep the marker `grading-teams-v1`: migration 013's guard on team acceptances
  names it, so a changed team rule gets its own profile key and scope entry, never a renamed workflow.
  In a batch frozen without the arbitrator, Standard's additional reviewer sees both complete
  assessments plus original inputs; a match with either resolves the tie, otherwise individual
  review. With the arbitrator (every Cloud batch now) it rules instead (below). High Assurance (a blind
  Standard third grader, then one escalation to a second, independent team) was removed on
  2026-09-23; its batches' receipts and both round outcomes read as they ran, and nothing
  resumes them. Cloud has no second round and never escalates.
  Each decision compares EVERY rubric selection, including zero-point items and
  ceiling-absorbed differences. A tie-break may select one complete validated model
  assessment; never average scores or combine per-item votes. Points remain deterministic.
  Missing/failed/unscorable required assessments never count as agreement. All model
  receipts and both round outcomes remain in `signals.panels`; `signals.committee`
  records resolution and proposal provenance. Explicit reading/review concerns and
  final rubric exclusivity checks remain actionable despite selection agreement.
  The reader never receives solutions, rubrics or grades. Both graders receive OCR
  differences and visual/layout context; text-only secondary models receive no crops.
  Optional transcript layout/alternative fields reference retained lines. Histories and
  instructor decisions remain intact. Estimates and exact acceptance
  scopes cover the team and its prompts.
- CLOUD'S ARBITRATOR (`grading/arbitration.py`, rule `final-arbitrator-v2`; requirement, 2026-09-21) is the team's
  additional reviewer (the tier's `verifier` connection), frozen on every Cloud batch by `standard.freeze`; the optional
  setting that once switched it (`final_arbitrator`) and its v1 fourth model were removed on 2026-09-23. That connection is
  never bought for an ordinary tie-break (`arbitration.reviewer_arbitrates` forces `needs_third` false and records the votes
  it would have bought as `arbitrator_reserved`); a response that would wait goes to arbitration at once, so it is called at
  most ONCE per response and only to arbitrate. With no reviewer nothing arbitrates and the response waits (`not_asked`).
  It sees the original inputs and crops, every earlier model's complete validated assessment by label, the team's decision
  and every flag with an id. It is NOT told which model made an assessment (requirement, 2026-09-24; `standard.IDENTITY_RULE`
  `model-blind-v1`, frozen per batch as `identity_rule` and named in the scope): each assessment goes to it, and to the flag
  reviewer, by its label and `input_mode` (whether that grader saw the crops) without `arbitration.IDENTITY` (connection, model,
  family), which stay recorded on the grade, its receipts and the review card. A batch frozen without the key finishes as it ran. It decides one of three: `adopt` one earlier assessment's ticks, `revise` (its own complete
  ticks, validated like any grader's), or `unreliable` (the response stays with the instructor). Keep these invariants.
  The standing grade is always ONE complete validated assessment: never an average, never ticks mixed from several models,
  and Faculty calculates the points. A ruling settles ONLY the flags the arbitrator was SHOWN and marked resolved
  (`flag_rulings`, one per flag; a flag left standing or unanswered makes the outcome `unsettled`, which stays flagged):
  the final ticks go through every deterministic check again by the ONE method the scoring stage uses (`jobs.scored`), and
  any NEW flag keeps the response with the instructor, the ruling on its review card. Flags of every other candidate are
  computed and shown too. No model settles the rubric finalization (`apply_quality` runs AFTER `arbitration.apply`), an
  unscorable selection or an instructor's review (even a draft). A grade that ticks NO rubric line on a
  rubric that has a zero-point line waits for the instructor under every review policy (`risk.empty_selection`, checked
  last in `jobs.scored`, after any ruling and `apply_quality`; frozen per batch as `empty_selection_rule`; instructor,
  2026-09-24). It never changes the score. On a NEGATIVE rubric the zero-point line is the correct-answer line and ticking
  nothing scores full credit, so since `empty-selection-v2` (2026-10-19) that grade is held with `risk.EMPTY_FULL` ("gives full
  credit although no grader ticked the rubric's correct-answer line") instead of v1's "has a line for no answer"; the arbitrator
  and the flag reviewer still never settle it, but the flag review (`flag_resolution`) is sent it and may tick a line, page round
  included. An empty grade it upholds is held again, since the hold comes last. A failed, refused or malformed ruling
  leaves the response exactly as the team left it and never fails the task (`ask(..., optional=True)`). It lives INSIDE
  the `grading` stage as two retained steps (`grade:arbiter:check`, which also records why nothing was sent, and
  `grade:arbiter`). A finalized ruling reuses `frq_team_acceptances` and honestly satisfies migration 013's guard:
  `signals.committee` records the resolution (`status: resolved`, `resolved_by: final_arbitrator`, `team_status` as the
  team left it) while the team's own decision stays in `signals.panels`; basis `final_arbitrator` (`arbitration.basis`)
  is shown on the review card and in exports. The scope names the rule, the prompt (`arbitrate-v1`) and that the
  additional reviewer arbitrates. Do not let it buy further rounds, vote, or rule on flags it was not shown.
- FLAG REVIEW (requirement, 2026-09-28: "an option in the review stage to quash ... flags whereby the rubric review model is called to
  give definitive judgements", question by question and version by version, "in batches of 25, but it would receive the crops and
  everything for a full picture"; asked, they chose uphold or re-tick, every flag but their own, clarifications proposed for their
  confirmation and one button for the whole assignment; `grading/flag_resolution.py`, rule `flag-resolution-v2`, prompt
  `flag_resolution-v2`, migration 033, `static/grading-flag-review.js`). A resolution is a `frq_flag_resolutions` row of the TERM
  grading plan; its calls are ordinary cloud calls it owns (`flag_resolution_id`, replayed from receipts, cancelled like an audit).
  Its input snapshots the held responses a ruling could finalize (`candidates`: current result of a Cloud team batch, unreviewed, not
  finalized, nothing under way, graded under the rubric, question and paper that stand) by reference. The Rubric drafting and review
  model (it must read images) rules on each question's responses in chunks of `CHUNK` (25, cut short at `MAX_IMAGES`/`IMAGES`), in
  order, each chunk shown the question's earlier rulings (`precedents`); questions run in parallel.
  **FLAG REVIEW PARALLELISM** (requirement, 2026-10-25: "allow up to 100 parallel requests for the flag review run"):
  a run processes up to 100 question/version groups concurrently, or fewer when the run contains fewer groups. Each group's
  chunks and full-page follow-ups remain in order so later requests receive its earlier rulings; provider pacing and retry
  limits still apply. An already-running review retains its current worker pool until it finishes.
  **FLAG REVIEW DEDUCTIONS** (requirement, 2026-10-25: "fix the app so the required deductions actually get applied and it catches
  those contradictions before finalizing grades"; `flag-resolution-v3`, `grading/flag_adjustments.py`): extra deductions explicitly
  stated with a point amount in the approved rubric or confirmed guidance are executable by this review. Faculty exposes the
  recognized amount and its complete source condition as a fixed choice; the model decides whether the condition applies and
  cannot choose an amount or introduce a new deduction. Existing negative rubric lines remain ordinary selections, not extra
  adjustments. Each settled ruling must address every offered deduction and name its expected final score. Faculty calculates
  the score with the approved deductions using the ordinary decimal scorer, including caps, floor and ceiling; a missing or
  duplicate choice, invented deduction, inconsistent expected total or explicit contradictory score claim fails that response
  alone and keeps it with the instructor. A deduction merely mentioned in the explanation is never silently applied or ignored.
  The term-owned ruling retains the authorization, applicability reason and amount; its carrier stores the actual deductions
  in the existing result's score adjustments. The final acceptance gate checks that those exact adjustments and the expected
  score survived scoring. Instructor-only/uncertain replies still stay with the instructor, for every model, including Opus.
  No completed grade, instructor decision or certified assessment is rewritten. Existing queued reviews retain their earlier
  prompt and rule; v3 uses the separate `flag_resolution_adjustments` prompt. New metadata belongs to the existing term review
  and response result; there is no new table or independent storage owner. Unrecognized or ambiguous deduction wording remains
  a matter for review, never authority for a guessed amount.
  **GRADERS TAKE OFF APPROVED DEDUCTIONS** (requirement, 2026-10-26: "build a b c and d", after the Exam 2 review: 83 of the 357
  held answers carried a coordinator deduction the graders recognized and could only ask about, 60 were held for nothing else, and
  the flag review then took off exactly that amount on 53; `grader-deductions-v1`, `grading/grader_deductions.py`). The same fixed
  choices as above (`flag_adjustments.rules_of` over the rubric graders are sent) go to the two graders of a Cloud batch frozen with
  the rule (`standard.freeze`) as `deductions`; each decides every offered id once (`applied`, `reason`), never an amount, and a
  missing, repeated, unknown or malformed decision breaks its output contract and is asked again. Faculty takes off the applied
  amounts with the decimal scorer as the grade's score adjustments, which "Recalculate points" and the full-credit reading keep
  (`grader_deductions.adjustments`, `rubric_quality.adjusted`). Two graders who differ on a deduction disagree (`risk.compare` names
  it "deduction on R2_1") and go to the arbitrator, which adopts an assessment with its deductions; an arbitrator's own grade is
  not asked about them and waits for this flag review or the instructor (`grader_deductions.UNDECIDED`). The prompts (`grade` v8,
  `transcript_grade` v7, `second_vision` v5, `primary_vision` v2) are the earlier versions word for word and three paragraphs: the
  deductions; PRESENTATION THE RUBRIC DOES NOT NAME (letter case of a printed variable, $ and unit signs, spacing and thousands
  separators never deny an item and are never a doubt unless the rubric names them; a symbol that changes what is written, such as
  a comma for ∪, is not presentation; replayed on Exam 2, 30 held answers had only such doubts and none changed); and an empty
  answer's no-answer line needs no quotation. The output schema asks for `deductions` only when a request carries them, so every
  other request keeps the contract it had. A batch without the rule, and a rubric that states no amount, grade as before.
  **A SETTLED NOTE STAYS SETTLED** (requirement, 2026-10-26: "build a b c and d"; `settled-notes-v1`, `arbitration.renamed_notes`).
  A grader's request or rubric doubt reaches a ruling in its own words under the grader's name beside a sentence naming only who
  asked (`risk.SECOND_ASKED`). Adopting that grader's assessment made the scored grade say `risk.GRADER_ASKED` or `risk.WORDING`
  instead, which the ruling was never shown, and the grade waited: 16 of the 19 Exam 2 grades this review settled on 2026-10-25 came
  back so (the instructor kept 14). Now such a sentence is settled when every request or doubt behind it in the scored grade (its
  own `review_reasons` other than rubric conditions, or its `rubric_ambiguities`) was shown and ruled resolved; any word the ruling
  did not settle keeps it, and a sentence only the instructor settles is never touched (beside the arbitrator rubric wording stays
  theirs). The same rule applies to the arbitrator's adoption. Frozen by every Cloud batch (`standard.freeze`) and by every flag
  review queued from now (`settled_notes` in its input, carried to its carrier batch); one without it finishes as it ran.
  ITS OWN PROMPT, NOT THE ARBITRATOR'S
  (requirement, 2026-09-29): `flag_resolution-v2` asks
  for definitive judgments, every flag answered, a grade left with the instructor ONLY when the evidence itself is missing (illegible in
  the crop, cut off even on the page, or a confirmed clarification says so), with a `reason` naming what to look at and the grade each
  reading gives. It answers `uphold`/`retick`/`instructor`, stored and checked as the arbitrator's adopt/revise/unreliable
  (`DECISIONS`, `arbitration.validate`/`outcome`: every flag ruled; a revise with a doubt is unsettled); a malformed ruling fails only its
  response. Asked, the instructor chose two more powers, for this feature only: it may CORRECT OR ADD the transcript lines the crop
  clearly shows (`transcript_changes`, at most `CHANGES` per response, checked by `changes_of`: a misread digit or symbol, a circled
  printed choice the reader left out) and quote them; each such line is marked `source: flag_review` with what it `saw` and, for a
  correction, the reader's `original` elements (the review card shows the mark), the carrier batch scores on that transcript
  (`amend`, `carry`, `amended` in `jobs`), and the result the ruling was made on keeps the reader's. And it may ask for the WHOLE PAGE
  (`needs_page`) when writing that may decide credit runs past the crop's edge: one more request (a page round) sends that response
  with each scanned page its crops came from, turned and straightened as the crop was, the area outlined in red (`_page_views`,
  through `_scan_page`'s checksum; a page that cannot be read leaves the response with the instructor). Whole pages can show the
  student's name when the question is on the first page. Keep these invariants. A ruling FINALIZES NOTHING by itself: the settled responses get one
  ordinary `rescore` batch per source batch (`_carry`, its settings the source batch's frozen profile plus `flag_resolution_rule`),
  whose scoring stage takes the ruling's grade (`carry`: the adopted grader's retained assessment or the model's own, second opinion
  set aside, the earlier arbiter and flag review moved into the record so neither applies again), runs every check (`jobs.scored`) and
  settles only flags that were SHOWN and resolved (`apply`); then the ordinary acceptance gate, basis `flag_resolution`, scope entry
  `flag_resolution`. A response whose current result moved since the snapshot is not carried, and one whose paper, question or rubric changed since is left out of the review (named in the run's `left_out`) while the others finish (requirement, 2026-11-06). RECALCULATING KEEPS A FLAG RULING (2026-10-26;
  Exam 2's Q24 lost 17 settled grades to the first grader's lines when one point value changed): a later "Recalculate points" batch
  of a grade that stands on a ruling (`ruling_carrier`: the reused result followed back through recalculations to its carrier) takes
  the ruling again through that carrier's own `flag_resolution_grade`, with its deductions, and the score the ruling expects is its
  lines and deductions on the current points; an instructor's own selections still come first. It EXTENDS `risk.instructor_only` for
  this feature alone: rubric wording and rubric conditions may be settled here, never by the arbitrator or the flag reviewer.
  `RESERVED` stays the instructor's everywhere: a blank crop, `EMPTY_SELECTION` (not `EMPTY_FULL`, above), an incomplete pair (`NO_SECOND` and the rest),
  `UNSCORABLE`, and the rubric finalization; responses held by any of those are not sent. (A blank crop stays theirs in the
  proposal gallery too, where they may confirm several they looked at together: Proposal gallery, 2026-10-11.) Clarifications are proposals in its
  Details, never written into the finalization by themselves. Since prompt v3 (2026-10-03) the payload's `linked_lines` gives, for
  each line with "same" links (LINKED VERSIONS), those lines on the problem's other versions with their own wording, and a proposal
  may carry `linked`, the same rule worded in each of those versions' values (`linked_clarifications` keeps only known linked lines,
  one per line). "Add to the rubric…" beside a proposal opens one dialog with a box per version (that line's confirmed clarification
  followed by the proposal, each ticked and editable) and saves the ticked ones in ONE `rubric-quality/clarify` confirmation; it
  grades nothing again. A response left with the instructor names, besides its `reason`, its possible `readings` when the deciding
  marks have two to four readings that each settle every flag: each a complete set of line IDs (`readings_of`: 2 to 4, known lines,
  no two alike, else none). `GET .../responses/<id>` carries `flag_review` ({reason, readings with their scores under today's rubric,
  at}) only for a response whose CURRENT result the latest flag review left with the instructor and that has no decision yet; the
  desk shows the reason and one button per reading, which ticks exactly its lines and approves through the ordinary review route.
  EVERY CHOICE SHOWS (requirement, 2026-11-14, of a note about two marks, the sign in (x?3) and a '−' or '=': "this is asking about
  the sign between x and 3 but both of the options give the same choice"): the review had named four readings and the response read
  carried all four, but Decision needed is capped at 30% of the window and scrolls, so the two "(x+3)" choices were out of sight.
  A Decision needed that carries reading choices is never capped or scrolled (`grading.css`, the `:has([data-flag-reading])` rule);
  without choices it keeps its cap. IT ALSO STARTS BY ITSELF (requirement, 2026-10-11, choosing "Flag review runs itself" from a measured list: pressed, it had left
  them 33 of 137 held grades on Quiz 3 and 14 of 58 on Quiz 4; this reverses "keep the button, make it small" of 2026-10-03, and
  the button stays for a run they ask for): about two minutes after a grading batch finishes, the worker requests this same review
  for that batch's held grades, once each (`grading/auto_flag_review.py`; workspace `auto_flag_review`, on by default, Compute
  settings "Run the flag review by itself"; the rules are PIPELINE's). `queue_flag_resolution(..., actor="automatic", only=<result
  IDs>)` sends those of the named grades that still wait and marks the review `automatic`; the button's request is unchanged and
  still sends every held grade a ruling could finalize. `GET .../flag-resolution` adds `automatic` ({enabled, waits, not_started,
  settle_seconds, due: {responses, response_ids}}: the grades it is about to send, or why it did not start), and a queued or
  running review names its `response_ids`.
  **THE SPOT TO LOOK AT** (requirement, 2026-11-14, of a flag asking them to confirm what the student wrote: "is it possible for us
  to put a bbox around the specific spot that needs clarification? Maybe using Jev's bbox feature or the models themselves?"; after
  the trial below, "Build it"; `grading/flag_look.py`, rule `flag-look-v1`, prompt `flag_look-v1`): a response the review leaves
  with the instructor WITH reading choices is asked about once more before the review finishes, alone: its crops, the note and
  the readings go to the review's own model, which answers one to three `spots`, each {label, crop, bbox} with the box in fractions
  of that crop. `spots_of` keeps a spot only on a crop that exists with a rectangle of positive area inside it and never repairs
  one; the usable spots are kept on the ruling as `look`, and `flag_review` carries `look` only when there is one, so every other
  response reads as before. The desk outlines each spot on its crop (the box grown 1.2% of the crop each way, an outline and never
  a fill) and in the crop's full-size view. BESIDE THE CHOICES (instructor, the same day: "can we make a crop of the boxed unsure
  part and put it up with the choices so that I can see the handwriting next to the choices?"): each spot is also shown enlarged
  in the note, to the left of the reading choices: the same scan picture cut to the box and what surrounds it (as much again each
  way, 6% to 15% of the crop's width and 4% to 10% of its height, inside the crop; `flagSpotView`), 9rem tall where its width
  allows, never stretched, with the same outline on the marks and the spot's name under it; the view is a button that scrolls to
  its outline on the scan. No new picture is made or stored: the page scales the crop it already shows. The views keep to the
  left half of the note at most (stacked there when two do not fit side by side), so the choices are always beside them. THE REST
  OF THE DESK KEEPS ITS SIZE (instructor, the same day, of the note shown whole with its views: "why did the rest of the page
  change? I don't see the rubric lines anymore ... I didn't want to change the rest of the page"): a note with reading choices is
  shown whole (EVERY CHOICE SHOWS) and takes nothing from the paper and the rubric: the desk grows by what the note needs, and
  their box stays as tall as under a note at its usual 30dvh (70dvh less 7rem, or more), the page scrolling down to it. Measured
  live in a 1280 by 780 window before the fix: the note 656 px, paper and rubric 16 px; after: 434 px. It is an outline, never evidence: it decides nothing, changes no grade, transcript or flag, and no model is shown it.
  Each look is one of the review's own cloud calls (step `look:<response id>`, counted in its progress, replayed from its receipt).
  A look that fails or names nothing usable leaves the note as it was and never fails the review or its response; only a wait (a
  usage limit) or a stop is passed on, as for a ruling. MEASURED 2026-11-14 on the 177 saved notes with reading choices, asked this
  way of claude-opus-5-5 (`/mnt/nvme/Teaching/tmp/flag-box-trial/`): all answered, about 5 s each, 41 with two or three spots; by eye
  on 42 the boxes were on the marks in 36, on the neighbouring mark in 5 and wrong in 1. Jev 1.14's Box from the same note was wrong
  in 9 of the 42 and answers one box only, so it is not used (JEV PICTURES is unchanged). Nothing stored could stand in: since
  direct reading the transcript carries almost no boxes (41 of 33,163 elements). FUTURE REVIEWS ONLY, and the responses still
  waiting when it was built (instructor, the same day: "this should be applied the currently flagged quiz 8 responses that it
  applies to and future scans, don't retroactively apply the boxes to things that have already been graded"): a finished review's
  result is immutable (migration 033), so Quiz 8's 14 notes that still waited were given their outlines once, by one counted
  `flag_looks_added` event of the plan ({resolution_id, looks: {response: spots}}, the trial's boxes for those very results, each
  checked by eye), which `flag_review_note` reads where the review's own ruling has no look; no decided response and no other
  review was touched.
  **FLAG REVIEW PROGRESS** (requirement, 2026-10-25: "fix the progress counter so it shows what's actually happening and doesn't
  say 22/22 when it's still working"): progress is a read-only projection owned by this term plan's flag review, from its
  existing `frq_cloud_calls` receipts. `progress.requests` counts actual recorded attempts, `answered` counts completed replies
  without a cap, and `running` counts submitted requests still awaiting a result; replaying a saved reply adds no attempt.
  A deferred review adds `progress.wait` with the scheduled retry time and a reason (subscription usage limit, provider
  capacity, unconfirmed outcome or retry), using the latest attempt of each step so a recovered failure does not explain a
  later wait. The page says replies received and requests in progress, never a fraction against an estimated total: image
  limits, full-page checks and answer repairs can add requests. It distinguishes preparing, queued, processing and paused
  work; only the review's completed state means its rulings are ready for scoring. No worker, retry, cancellation or grade
  application rule changes, and no new retained fields. The existing five-second quiet polls preserve focus and disclosures.
  **FLAG REVIEW RESET** (requirement, 2026-10-25: "reset this back to the 324 original flags"): the finished review's
  GET/POST `.../flag-resolution/<rid>/reset` previews and restores its original result references. Only current proposals
  made by that review's carrier batches are restored; grades still on the original result stay there. Instructor decisions,
  newer grading, changed rubrics or papers (including INBOX PRESENT), and certified assessments are protected. No reset
  while the assessment has grading or a flag review under way. The write requires the exact displayed fingerprint,
  `confirmation: reset` and an idempotent request ID. Term-owned `frq_events` keeps the reset receipt and the before/after
  result references; no result, acceptance, model reply or completed review is erased or rewritten. Restored results
  were already sent to a flag review, so PIPELINE NEVER A GRADE TWICE keeps them from being sent automatically again.
  The old review is labelled reset; its original rulings remain in history. Assistant requests require an Approve card.
  The page offers both in one row above the review workspace, naming the model by its
  readable name (`modelDisplayName`) and saying that it starts by itself, for which grades, or why it did not, with how it works,
  what was not sent and the last review folded into Details. SAID AS WHAT IT DOES TO THE QUEUE (requirement, 2026-10-17: pressed, it
  "didn't seem to do anything"; it had sent 14, finalized 9 and returned 5, and its line read "9 of 14 flagged grades finalized · 210
  not sent"): the button names what it sends ("Send 14 flagged grades to <model>"), a finished review adds ", N back in your queue",
  and what it cannot rule on reads "N flagged grades it cannot rule on, still in your queue" (the Details hint "N it cannot rule
  on"). Wording only. The workspace steps past the responses a review is ruling on, or
  is about to by itself, as it does those left for later: labelled "With the flag review", never hidden, still counted as work
  to review, and brought back by Review them yourself when only they are left. The response on screen stays open, with ONE
  exception (requirement, 2026-10-11, told Review could open first a response the flag review was about to settle: "Step past it
  on entry"): the response Review itself opened on entry is chosen before the flag review's first read answers, so when that
  first read names it, and it is still on screen with nothing typed, ticked, clicked or keyed in the workspace, Review moves
  to the next response that is the instructor's (or says what is with the flag review) and the address no longer names it. A
  response the address named, and anything a later read names, stays open. `RESERVED` is as reserved from the automatic review as from the
  button's. The restart gate counts a queued or running flag review (`assistant/coding.py` `_busy`). The model reads refs, never an identifier, a section or which model graded (`_model_row`).
- `grading/symbolic.py` parses retained transcript text with a closed grammar, NEVER `eval` or
  SymPy's string parser; anything not certainly a formula is `indeterminate`. It never solves for the
  student, repairs a line or uses a tolerance, and a check never selects a rubric line. Equivalent
  forms in the answer-key matcher come last, after spelling and exact rationals; exact notation still
  keeps its own answer group. Crossed-out lines (`state: crossed_out`, sparse on transcript elements)
  are never the final answer and are skipped by the checks. Crossed-out work alone never
  triggers extra grading, manual review or an acceptance block; its count is informational.
- Cloud may read the handwriting itself, with no OCR beside it (`grading/direct_read.py`, `direct_reading`).
  A crop with NOTHING written on it is a BLANK ANSWER for the instructor, never a failed reading (requirement, 2026-09-22;
  rule `direct-reading-v2`, frozen per batch as `direct_reading_rule`, which `direct_read.rule` resolves): the empty reading
  is kept and marked (`raw.empty`), reconcile still looks at the image and decides what is on it, the marked crops travel
  with the candidate (`blank_crops`), and `recognition.review_state` turns them into a reading reason, so the response is
  graded as usual and waits in `ocr_review`, out of automatic finalization. It is also a risk block (`risk.blank_reason`) that
  neither review policy waives and no model ruling settles, in any batch (review fixes, 2026-09-22). Faculty decides nothing about the page
  here. `direct_reading_rule` is frozen from now on so a later rule can leave v2 batches alone; a batch from BEFORE the key
  runs under the current rule, because v1's answer to an empty reading was a failed component and never a grade, so its
  failed responses can simply be retried and finish.
  THE SECOND PASS KEEPS THE FIRST READING'S NUMBERS (requirement, 2026-09-29; rule `direct-reading-v3`, prompt `reconcile-v8`). Now a `direct`
  candidate is the reader's own reading and is only ARRANGED: its characters copied, never a number changed or added, a mark it
  doubts kept with a `reading_alternatives` entry, `[illegible]` an unclear element with no text. `direct_read.introduced` compares
  the digit runs of both passes (a separator added or dropped is no new number); a number the transcript introduces is asked about
  once more (step `reconciling:first-reading`, payload `introduced_numbers`), and if it stays the transcript keeps it with a reading
  doubt naming both readings (`direct_read.doubt`), so the response waits for the instructor or the flag review. The step records
  `first_reading_check`. Batches frozen on v2 finish without the check (`keeps_numbers`).
  New batches use `direct-reading-v4` and `reconcile_literal-v1`: compare literal tokens per crop in BOTH text and LaTeX,
  including missing writing, signs, decimals, order and choice letters. A changed arrangement is asked about once and any
  remaining difference becomes a reading concern under the existing review policy. Harmless TeX/layout normalization is allowed;
  wholly crossed-out writing remains excluded. Retained v2/v3 batches keep their rules and prompts.
  Optional `reading_page_recovery` is OFF by default pending representative-scan evaluation. A new direct-reading batch with
  it on freezes `reading-page-v1`: the reader can request one bounded whole-page round for visibly connected writing cut off by
  the answer area. The first reading and original extraction stay intact; added writing and derived page images are retained
  through ordinary task/Inbox storage, the literal check runs again, and graders/review/audits use the recovered images.
  Missing, masked or unresolved context raises a reading concern. This never changes answer areas or opens a second page round.
  A MARKED PRINTED CHOICE IS ITS LABEL (requirement, 2026-09-29; `read-v3`, `reconcile-v9`). The free reading writes
  `[marked: (b)]` for a printed choice the student circled, boxed, filled, ticked or underlined (or a printed word they marked,
  `[marked: bounded]`) and `[crossed out: (c)]` for one they struck; the arrangement writes the label as an ordinary element, never a
  mark or a description. Future readings only.
- Confidence checks have ONE instructor switch (`risk.confidence_checks`, default on; 2026-09-20). Off, no model-reported confidence
  NUMBER flags, escalates, blocks or gates anything (`risk.assess`, `recognition.review_state`/`low_ocr_confidence`, the committee's
  second-grader check, `review_rules.reading_resolution`); the numbers are still recorded and the thresholds kept. It never covers
  what a model NAMES (ambiguities, unclear marks, review reasons), reader agreement, grader disagreement or contradictions. Route any
  new confidence-number check through `risk.confidence_checks(settings)`. Off is part of the acceptance scope; on leaves scopes as
  they were, and a frozen batch without the key keeps its checks. The settings inputs are read-only while off, never disabled.
- `grading/risk.py` is a rule-based policy (`risk-rules-v15`) over independent evidence; model
  confidence is one weak input, never the gate. Persist the COMPONENT signals, not only the verdict.
  WHAT MAY HOLD A RESPONSE BACK is the instructor's choice (`risk.review_policy`, default `all_checks`, 2026-09-21). `disagreement_and_work` keeps only the two checks — the grading team did not agree, and full credit was proposed where the rubric wants work and little is visible
  (`risk.WORK_BLOCK`) — and waives every other block, reason and review state. A waived check still RUNS and is RECORDED under
  `signals.risk.waived` and shown on the card; only its power to block is removed, in `assess` and again in `jobs.scored`, which
  is where the reading state and the graders' own doubts are narrowed. NEVER waived, because they are not opinions: a selection
  that cannot be scored (`risk.UNSCORABLE`), a blank crop, and the instructor's confirmed rubric finalization, which `apply_quality`
  applies afterwards. On a job without a team the narrow policy keeps the two graders' disagreement (that IS "the team did not
  agree"), and its `waived` record merges with `waived.direct_reading` rather than replacing it (v13). The choice is part of the acceptance scope, and a risk key at its default is left out of that scope
  (`acceptance.RISK_QUIET`) so a batch that changed nothing keeps the scope, and the permissions, it always had.
  `require_key_match` and `require_work_shown` gate full-credit proposals only, using the deterministic total
  against the question maximum (including full marks accumulated from several items).
  A missing/unknown/mismatching key or insufficient work alone never blocks a proposal below full credit;
  the remaining reading, rubric and mathematical checks still apply.
  Nothing true of EVERY response may escalate one: a reader's doubt is its per-symbol rate
  (`min_ocr_rate`), and the
  unresolved rubric condition blocks acceptance but is not a reason for extra AI checking.
  `review_rules.reading_resolution` may settle OCR differences only from a clear, internally
  consistent visual transcript matching a complete retained OCR candidate (or an explicit
  instructor correction), with registered crops and existing confidence checks. Retain original
  disagreements and original agreement for acceptance; no manufactured agreement. Review/risk
  versions belong in the exact acceptance scope. Correction counts reuse current term response
  reviews, separating frozen profiles/revisions/policies and independent versus visible samples;
  counts do not calibrate confidence or tune thresholds. A blind acceptance revealed before the
  decision cannot count as independent gate evidence. Shadow mode (every proposal of a batch hidden
  until the instructor's own decision) was REMOVED on 2026-09-20 at the instructor's request; do not
  bring it back. Saved settings that carry it still load (`config.RETIRED`), a request that still
  asks for it is graded like any other, and `frq_jobs.shadow` stays as an unused column.
  "The final answer rests on one reading" means two readers REALLY differ on a reported final answer (2026-09-21):
  `facts.readers_agree` is None when no final answer was reported, which flags nothing, and `(43/2)x` is the same reading as
  `\frac{43}{2}x`. A parenthesis that changes the value is kept everywhere `answers.normalize` is used: a product in a
  DENOMINATOR keeps its parentheses, because `27/(2x)` is not `(27/2)x`. The reader's `ambiguities` are for ILLEGIBLE MARKS ONLY, each with two readings
  (`reconcile-v5`). Structured doubts (uncertain layout relations,
  reading alternatives) are counted by `reading_ambiguities` and are not repeated in the free text.
  The reader KEEPS THE LABELS AROUND A MATRIX (`reconcile-v6`, 2026-09-21). The name before it, column headings as a row above, each row label before its row,
  written dimensions, the student's brackets kept; handwriting added to printed material gets its own line. The arrangement it
  asks for stays quotable (`\hline` is layout). A changed reader prompt is a new acceptance scope; never restart the trial
  mid-batch, or one batch is read under two prompts.
  What two readers can differ on is a VALUE. `answers._compared`: a value is compared
  whole; a sentence, a matrix layout and a value written with a word are compared by their NUMBERS only; nothing to compare is None; 6130.10 is 6130.1. Real differences stay
  (`9t+10` for `9t+18`). A key in WORDS ("not possible") is matched by an answer that BEGINS with those words and goes on to give
  its reason (`answers.worded`: words only, whole words, from the first word; never by merely containing them; the reader's
  `\text{}` wrapper is its layout, not the student's first word), an answer
  reported for part b never answers the key's part a, and "two plain words differ" holds for single words only: a sentence stays
  `unknown`. A key that answers only SOME of the lettered parts a rubric pays for (`partial_key`: a key for part b beside
  "Part A:" lines) never raises "matches the key, but full credit was not proposed". Only where key and rubric both speak in part letters; any other key is the whole answer as before.
  An ORDERED PAIR OR TRIPLE is its components (`answers._tuple`, 2026-09-21). Each component is
  compared as any answer is; only a whole answer that IS the tuple, with an optional `(x, y, z) =` before it and nothing after it
  but the parameter's clause in words without a number ("where t is any real number"). `different` only for tuples of NUMBERS,
  every one understood (`(40000, 260000)`): a PARAMETRIC answer is never accused. Also `unknown`: a pair for a triple, equations instead of the triple, words before it, two tuples.
  An unresolved team has no SUPPORTER, which is not "No independent second grader looked": where a second assessment was
  compared (`committee.pair`) the team's own reason is the block.
  A quotation is checked by `models.quotes`, for evidence and final answers alike: every ROW of it must be in the named line
  in the student's own characters. The reader's layout markup (math delimiters, `&`, array and aligned wrappers, spacing
  commands, `\text{}` wrappers, grouping braces) and a mark's spelling (× for `\times`, a fraction bar written `\frac{-8z}{-8}`
  or `-8z/-8`; a PRODUCT in a denominator keeps its parentheses, so `27/(2x)` is never quoted for `(27/2)x`) are not the
  student's. `models.quoted` also accepts a sentence the student wrote over several handwritten lines and a grader quotes as the
  one sentence it is: it must START in the named line, take it to its end and continue character for character through at
  most `RUN_ON` following lines, passing over a line of side work between them. A paraphrase or a corrected value still fails; nothing else is forgiven.
  A row is found AS WRITTEN (2026-09-22): a number is never cut from a longer one (`12` from `x = -12`, `x = 0.5`
  from `x = 0.55`, `40` from `40,000`); rows, elements and matrix cells never join into one, and a quotation passes from one to
  the next only where it has a space or cuts no word; a line's text and LaTeX spellings are never mixed; a parenthesis stays
  wherever dropping it changes the value. A final answer must also be found with crossed-out elements left out, and a run-on
  through a struck line refuses it. `answers.normalize` keeps a space between digits, evaluates an unambiguous mixed number
  exactly (`23 1/8` = 185/8) and reads a trailing ellipsis as unknown; `readers_agree` finds each value only as a whole number
  with its sign; `symbolic-v2` reads numbers side by side, square brackets, scientific notation and `23\frac{1}{8}` as
  indeterminate, bounds powers (4096 bits) and expansions (200 terms), and never throws.
  A grader may name a NEIGHBOURING line (requirement, 2026-09-21; `models.located`). Three shapes and no others: the run-on read BACKWARD
  (it starts up to `RUN_ON` lines earlier and TAKES the named line: the last line of a wrapped sentence, the value in
  `decreases | by | 23 1/8 | units`); a printed LABEL (`Function:` is named and the writing is in the line that follows; the
  label may open the quotation, and the line before a label is never tried); and the line directly after or before. The
  quotation is kept under the line where it was FOUND (`named_line_id` keeps what the grader said), and a crossed-out line
  refuses a final answer whether it was the line named or the line found. Text anywhere else in the transcript and writing
  the reader never retained still fail. Rows joined the GRADER's way are still the student's rows: `;`, an
  elision `...`, a typed arrow `->` and a line break spelled `\n` before a letter are tried as row breaks only after the
  quotation failed as written, and every row must still be in the line; a comma-composed pair (`46,206, 2790`) and a letter for
  an operator (`2 x 2`) fail as before, and the run-on never uses these joins.   A failed quotation on a line the grader did
  NOT tick flags nothing: that line moves no score, so it is dropped and counted (`unticked_quotes_dropped`).
  An empty evidence slot is missing evidence, not a failed citation (2026-09-21: `{}` / `""`). Skip the blank; "no supported evidence" still flags the standing grade. A third that only
  votes does not become the citation record: the matched side's quotations stand, and the third's empty slots are not a
  team concern. Do not loosen further: the 44 that still failed were another value read in the image (260000 for 280000, which two graders saw and which SHOULD reach the instructor), descriptions, recomposed layouts and writing missing from the transcript.
  Model fields meant for doubt (`ambiguities`, `review_reasons`) must stay empty when nothing is
  in doubt; the prompts say so. A grader
  DISAGREEMENT is about rubric selections: equal totals, equal-point alternatives,
  zero-point items and ceiling-absorbed differences are all disagreements, which go to the
  arbitrator (or, in a batch frozen without it, require the conditional third assessment). A recorded current-workflow resolution can settle the disagreement;
  otherwise it remains high risk and individual review. Old receipts without such a
  resolution remain flagged; never invent one or rewrite an instructor's decision.
  The key check is three-valued: `mismatch` only for an answer that
  was understood and is another value (or a missing part); notation the check cannot read is
  `unknown`, which never matches and never accuses a proposal of contradicting the key.
  What escalates or flags is evidence that contradicts the PROPOSAL (full credit for inconsistent
  work), not every error a proposal already accounts for. Cloud never escalates. New signals order and split
  answer groups and send grader disagreements to individual review; they never replace review by
  answer group.
- Acceptance (`grading/acceptance.py`, migrations 009 and 013). Proposals, trials,
  automatic decisions and instructor reviews remain distinct. Instructor decision of
  2026-09-20: NEW CLOUD TEAMS finalize automatically when their complete assessment is
  resolved and ALL checks pass; there is no benchmark prerequisite. Retain this in
  `frq_team_acceptances`, owned by the existing term plan/response, with exact scope
  and policy. Never fabricate a benchmark report or silently finalize older results.
  `frq_team_acceptance_reviews/events` retain corrections and suspensions;
  `frq_all_acceptances` and `frq_all_acceptance_reviews` are shared read-only projections
  over these and the original benchmark receipts. A changed rubric or finalization suspends only its
  own question's unreviewed automatic scores (2026-09-25).
  A CANCELLED CANARY IS NO FAILED CANARY (2026-09-28: cancelling Quiz 2's batch during its canary counted the requests the cancel closed as failed items, failed the canary and suspended 1,018 current automatic grades; fixed that day, then the canary itself was removed). What that bug suspended was REINSTATED at the instructor's choice (the one exception to "nothing is reinstated"):
  `frq_team_acceptance_reinstatements` (migration 032, append-only) names exactly the suspension each row undoes, the
  `frq_all_acceptances` view counts a team suspension only while it has none, and `reinstate_cancelled_canary(job, request_id)`
  reinstates only suspensions a cancelled batch's canary made, of grades still current, unreviewed and suspended for nothing else.
  A REGRADE REPLACES THE INSTRUCTOR'S EARLIER DECISION WHEN EVERY CHECK PASSES IT (requirement, 2026-09-27; sending responses through the graders again is their affirmative choice). A batch frozen with `acceptance.REVIEW_RULE`
  (`regrade-supersedes-v1`, every new batch that reads or grades; never "Recalculate points only") finalizes a new grade that passes
  every check even where the instructor had reviewed the response, stale or current, draft included: `supersede_review` moves
  `frq_responses.current_review_id` off the review (the row stays; migration 013's guard needs the pointer clear) and records a
  `review_superseded_by_regrade` event naming the review, result and batch. A flagged regrade waits as before, beside any decision
  that still counts; only Cloud team finalizations do this (a benchmark permission is earned from the instructor's blind reviews,
  and a trial is a test, so neither is accepted over a decision). Measured on Quiz 2's regrade after its rubrics were replaced: 40 responses passed every check and waited only for having been reviewed once. `refinalize_regraded(job)` applied the rule once to
  that batch, which ran before it; batches frozen without the rule otherwise finish as they ran. An instructor can inspect/change an
  automatic grade normally.
  Exports say `automatic` and identify `team_consensus` versus `benchmark` as their
  basis. Trial/benchmark acceptance remains available for local and legacy workflows;
  those permissions still require retained exact-scope reports and independent reviews.
  Human corrections of team decisions count as seen evaluations, never blind evidence.
- Corrections are kept BESIDE the AI's output (`frq_review_feedback`, dataset `provenance`), never
  instead of it, and may be used for training (requirement, 2026-09-19). Dataset splits are by
  STUDENT, never by response, and a dataset's `split` comes from the enrollment's permanent
  workspace prefix (`benchmark.enrollment_split`), so it is the same in every export; the per-export
  pseudonym stays unjoinable. Automatically accepted responses are never evaluation labels. `manage_grading.py`
  (`benchmark`, `run-endpoint`, `finetune-export`, `splits`) reads a dataset file only, never a
  catalog, and trains nothing. Comparisons against the default primary grader use an exact McNemar
  test and interval on the same responses, count failed or unscorable predictions as wrong, and
  compare only with a baseline of the same role and transcript source; keep the default when a
  challenger is within noise. `--split test` evaluates tuned models on held-out students.
- EVERY GRADE IS SAVED FOR TRAINING (requirement, 2026-09-26;
  `grading/training.py`, migration 028). Each DECISION, an instructor review that decides (never a draft) or an automatic
  finalization (never a trial), becomes one immutable `frq_training_examples` row of the term plan's response (UNIQUE per
  decision): crop references (never copies), the readings, the AI's transcript, lines, reasons and score, the final ones, the
  review feedback, and the models and prompts behind the proposal; question and rubric revisions by reference. The worker saves
  it when its local lane has nothing else to do (`capture_training_one` at the end of `process_one`; NOT a rotation slot), catalog work only, no execution lock; its first run saved every earlier decision. It
  decides, finalizes and sends nothing, and nothing rewrites an example: a later decision on the same response, rubric and paper
  is marked in the export (`superseded_by`, `standing`), and a grade gone stale under a changed rubric stays an example of the
  rubric it was graded under. Certify → **Saved for training** downloads one assignment or all of them (GET, read only, no
  `frq_exports` row): `faculty-frq-training-v1` with questions and rubrics listed once, original crops stored once by checksum, a
  per-export student key and `enrollment_split`. Automatic grades are labelled `automatic`: never evaluation truth
  (`evaluate.labelled`, used by `benchmark` and `run-endpoint`), and in `finetune-export` only with `--labels all`. The
  finalization's clarifications stay internal: an example names its confirmation, never copies it. A change to what an example
  holds is a new `training.RULE`.
- THE SAVED TRANSCRIPTIONS STAND (requirement, 2026-11-03: "go back to the originals", after 2026-11-02 "Cancel that"). The training
  download and `finetune-export --target reader` use the transcription saved with each grade. Faculty does not reread saved answers
  with other models: a recheck ran on 2026-11-02 for 956 Exam 1 answers and was cancelled, and its code and setting are gone (the
  stored setting loads and is dropped, `config.RETIRED`). Migration 053 stays recorded, so `frq_transcript_checks` keeps those 956 rows
  and `frq_cloud_calls.transcript_check_key` its receipts: nothing reads them, no grade or example changed, and they were not deleted
  because the instructor did not ask for that. Do not start such a recheck again without the instructor asking. Kept from that work:
  `models.transcript` adds a blank answer's note once, and the download's `feedback.transcript_corrected` says whether the words differ
  (827 unchanged blank answers were marked corrected, and those stored rows stay).
- CERTIFICATION AND EXPORT (requirement, 2026-09-26: "The last step of the grading pipeline should actually change to
  "Certification and Export" and should be retooled so that the primary goal of the page is to be able to analyze and "fine
  tune" the grades ... There should be an option to "certify grades" which would finalize the pipeline and import the scores
  into the gradebook. It should check that all students have a grade. If they don't have a grade, or for assignments that have
  free response plus a bubble sheet part (like exams), it should raise a warning and make me confirm that these students will
  receive a 0. ... Until then, this step cannot complete"; GRADING.md "Certification and Export"). The sixth stage is done
  only while a CURRENT certification exists (`progress.certification_summary`, `workflow.certification`: none, current,
  changed). This REPLACES the Export completion rule ("a retained grade snapshot matching the current decisions"); `workflow.export`
  stays for old readers. Owner: the TERM grading plan (`frq_certifications`, migration 029), one immutable row per certification
  with `frq_certification_students` per exam participant membership (scored, zero, excused, left_out); the latest counts, a
  newer one supersedes, nothing uncertifies. `CertificationService(frq, gradescope, gradebook)` (`grading/certification.py`,
  built in `create_app` after the three; the Inbox worker, which shares `create_app`, calls it only to certify late papers, LATE
  PAPERS CERTIFY THEMSELVES below) serves the page, the drill-downs, the UIN search, the preview
  and the certify (`grading/certification_api.py`). Keep these invariants:
  - A certification is "changed" only when what a grade IS changes (score, lines, adjustments: approving an automatic grade
    unchanged reopens nothing), the bubble sheets or key move (the identification threshold by value), the participants,
    their papers, roster additions or linked DR bookings change, a left-out DR date passes, a left-out makeup is no longer owed
    (`makeup_ended`, MAKEUP OWED below), or new work appears. A Gradescope send or a gradebook edit after certifying reopens nothing.
  - A DROP NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-10-23: "q drops and withdrawals should not trigger recertification", after
    Camden's Q-drop made Quiz 3, Quiz 4, Quiz 5 and the Mathematical Experiments Assignment ask to be certified again). A drop or
    restore (`roster_drops`, a Q drop, a withdrawal or "Not in Canvas") is not in the participants marker, and the registrar's marks
    never were; the certified grades of a dropped student stay as certified. A certification recorded while the marker still named
    the drops (2026-09-27..10-23) is compared with that older marker rebuilt from the drops recorded by its `created_at`
    (`progress.participants_changed`), so it reopens for nothing a drop did.
    Nor does a drop stop Certify just this change (requirement, 2026-11-03: Edda's Quiz 6 regrade stopped at Gradebook after five
    classmates' Q-drops, "These changed since you certified"): a student certified 0 with no work and dropped since is no change
    (`certification_change.dropped_no_work`); "no work receive 0" still holds with them counted as they were, and the new
    certification keeps their rows exactly as certified. LATE PAPERS CERTIFY THEMSELVES treats them the same (instructor,
    2026-11-03): such a drop leaves the markers live, and what it keeps of the confirmation still names them, as their rows stand,
    so a later drop or change reads it alike. Where classmates' copies stand on Gradescope never stops one grade's change either (a
    send after certifying reopens nothing; a late paper's send may not have finished): it asks only for this grade's own copy.
    A question nobody has answered yet (a makeup version's) is no
    "Written grades not on Gradescope" member either, whether Certify or the change asks: it has no written grade to leave off.
  - CERTIFY JUST THIS CHANGE (requirement, 2026-10-07, answering a Gradescope regrade request: asked, they chose "Certify just this
    change"; REGRADES "APPLY"; `grading/certification_change.py` `OneChange`, migration 044 `frq_certifications.amends_id`). When the
    ONLY difference between the latest certification and the live grades is one response's grade (`changed == ["grades"]` and the
    live decisions, with that response's entry put back to its certified content, digest to the latest `decisions_sha256`), a new
    certification may record just that change: built from the exact state (`_evaluate`), it needs no block, every confirm check's
    digest equal to the latest confirmations (the gradebook's and Gradescope coverage's own checks aside), that grade on Gradescope (`sent`), and exactly that
    student's row differing in content; it copies the latest checks and confirmations, stores the live markers (so it reads
    current), `amends_id` and `summary_json.amends` {from, to, response_id, question_id, message_id}, every student row, and writes
    the gradebook through the same `prepare_certification`/`record_certification` with `certification_options` on THAT student's
    row alone (every other cell plans "not in the certification" and is left as it is; a typed cell is kept unless Replace).
    Idempotent by request ID; the same `BEGIN IMMEDIATE` re-check of the catalog marks as a certify. Anything more changed is
    refused with the reason, and the instructor certifies the whole plan again as before (SUPERSEDED for the refusal by A REGRADE
    NEVER ASKS TO CERTIFY AGAIN, below). It extends "a newer one supersedes"
    (an amendment is a newer certification) and "planned from the certified rows" (the plan of that one row); the history labels
    it "Changed one grade". The certified archive keeps the cutoff of the newest certification that is not an amendment.
    ONE BUBBLE SHEET (REGRADES "BUBBLE SHEETS", 2026-10-28): the same for one student's multiple-choice score after a regrade
    decision read their sheet again (`check_sheet`, `amend` with `sheet_row`): every other row as certified, this row's other parts
    as certified, and the decisions marker equal to the latest (no free-response grade changed); "the multiple-choice sheets
    changed" is this change itself. The checks that say where Gradescope's copy differs (`gradescope_mc_corrected`,
    `gradescope_mc_differs`) never stop it, since Faculty's score is the grade and the request's own Gradescope step corrects that
    question; no Gradescope receipt is asked for. `summary_json.amends` is {part: "bubble", row, from, to, message_id}.
    A REGRADE NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-11-16, of Quiz 8's regrade stopped at Gradebook because a classmate had
    become owed a makeup since the last certification: "regrade requests shouldn't trigger recertification, rather they should
    automatically change the grade in the gradebook and the assignment and also on gradescope"). What else changed since certifying
    (another grade or sheet, another student's row, a block, a confirmation whose digest moved, a DR date, a makeup) never stops the
    change: it is refused only for this grade itself (not certified, not on Gradescope, this student's other grades or parts changed
    too, or the column gone). LIVE OR CARRIED MARKERS, as LATE PAPERS CERTIFY THEMSELVES: with nothing else changed it stores the live
    markers, as above; otherwise it records every other row exactly as the latest certification did and this student's row as it is
    now, writes only this cell, and CARRIES the latest's markers, grade marker and snapshot of its rows (the decisions marker live when
    this grade is the only decision that changed), so everyone else's change waits exactly as before for the automatic certification
    or the instructor's Certify. `summary_json.amends.markers` is live or carried; carried adds `others` (what else changed) and
    `moved` (WHO MOVED). The preview's fingerprint is accepted against any certification of the plan with the same from and to, so a
    certification recorded between the preview and the step (another regrade, a late paper) is no "changed since the preview".
  - LATE PAPERS CERTIFY THEMSELVES (requirement, 2026-10-09: "when an assignment's grades are certified, any additional scan that
    gets graded for that assignment should automatically be certified (assuming it wasn't flagged for review and not cleared by the
    flag reviewer/arbitrator). It should also automatically send it to gradescope if the assignment uses gradescope"; asked, they
    chose students with no certified grade only, and sending to a published Gradescope assignment anyway;
    `grading/certification_automatic.py` `LateCertification`, the worker's `grading/auto_certify.py`, Compute settings
    "Certify late papers by themselves", `auto_certify`, on by default). A student is LATE when the latest certification gave
    them no grade of their own work (a confirmed 0, left out until a DR exam or a makeup, a part confirmed as 0, or no row: a
    participant added since) and their work is now graded: classified scored with every part, every response final
    (`final_decisions`) and CLEARED (the instructor's approved or manual review, on a plan graded for participation Faculty's own
    approval of a waiting grade (THE WAITING GRADES APPROVE THEMSELVES, 2026-11-11), no flag but a waived check, or flags settled by
    the arbitrator, the flag reviewer or a flag review), named by no block and by no confirm check but Gradescope's and the
    gradebook's, a part certified before unchanged, their gradebook cell not a value the instructor typed or outside the
    column, and no standing batch line of their question and section missing from their grade (below). A rescan that changes an
    already certified grade is not late. The worker looks at a certified current plan once its
    activity has settled (`SETTLE` 300 s; never while its batch or flag review runs), after a cheap catalog test, and waits
    without recording anything while the Inbox may still hold a paper (`inbox_pending`) or a Gradescope send runs. It records
    ONE new certification (actor `automatic`, superseding the latest): the late students' rows as they are, every other row as
    certified, the gradebook write of the late students' cells only (`certification_options` on their rows, as CERTIFY JUST THIS
    CHANGE), the Gradescope sends to queue (GRADESCOPE "AUTOMATIC LATE-PAPER SEND") and, when a written paper is sent and the
    switch `auto_gradescope` is on, the run that publishes its version where the set already is (GRADESCOPE "AFTER CERTIFYING",
    2026-10-12), in one `BEGIN IMMEDIATE` re-checking the
    catalog marks; idempotent by a request ID derived from what it certifies. LIVE OR CARRIED: it keeps the live markers (the
    plan reads current; `amends_id` NULL, an ordinary archive cutoff) only when the plan, the late students aside, is exactly
    what the base certified (decisions, papers and sheets rebuilt without them digest to the base's markers; every other row
    equal; no block, no new work, no past DR date or ended makeup; every confirmation implied by what the instructor confirmed,
    with the late students counted as they were; a live or settled `gradescope_mc_corrected` is no confirmation to imply: Gradescope's
    copy moving reads like `gradescope_mc_differs`, but a late student whose own sheet it names still waits for the instructor, the
    instructor's 2026-11-06 decision, `certification_automatic.UNSTORED`). Otherwise it carries the base's markers and gradebook hint (`amends_id` set:
    it covers nothing new), so the instructor's own uncertified changes and any paper still waiting keep reading "changed" and
    wait for their Certify; `summary.automatic` names the base (`markers_of`) and who Faculty certified since (`pending`).
    A REGRADE CARRIES THE CHAIN (requirement, 2026-11-16: "fix the confirmation mismatch from the automatic certification", of Quiz 8:
    Faculty certified a makeup by itself with carried markers because the regrade's grade was saved and not yet certified, and the
    regrade's certification then copied the lists as confirmed before; `certification_automatic.carried_chain`). A certification
    of one changed grade whose markers were carried stands on the chain of the one it amends: the next automatic certification
    starts from its markers (the regrade in them) and counts the `pending` students as they stood in the chain's base rows
    (`rows_of` when that is not `markers_of`), and the regrade's own certification compares a confirmation the same way, so it reads
    live once the plan is otherwise as certified and keeps the lists as they are now. One made before this rule was always live. Why a
    late paper waits is recorded once per reason as `automatic_certification_not_made` (row keys only) and shown on the page;
    the history says who certified ("By": You or Faculty) and the header names the students Faculty certified.
    REVIEW OF 2026-10-09 (instructor: "Fix all of the issues and implement your suggestions."):
    - A LINE NEVER REACHES A LATE PAPER. "Add a line to every grade…" reaches only the grades final when it is applied (BATCH
      ADJUSTMENTS), so a DR, found or roster-added paper graded afterwards lacks it, and Faculty once certified, posted and sent
      it at the lower score. A late student whose final grade lacks a STANDING line of its question in their section or every
      section (`batch_adjustments.standing_lines`: each add/edit chain that does not end in a removal) and would move by it
      (a line the question's bounds hold changes nothing) waits: "A line you added to every grade of <question> is not on this
      paper; certify it yourself." Faculty adds only a full-credit line itself (BATCH ADJUSTMENTS › A FULL-CREDIT LINE REACHES LATER
      PAPERS); for any other, the instructor's Certify, or the same line applied again (it goes only where it is missing), lets it go.
    - A MAKEUP DECIDED LATER IS IMPLIED. "Leave these students out until their makeup" spells each member with its absence's
      decision, so an absence undecided at Certify and excused afterwards (MAKEUP OWED reopens nothing) made every later late
      certification carried. For `makeup_ahead` alone, a moved digest is implied when its rows now, plus the late students as
      they were, are exactly the rows confirmed (a stored list cut at the cap never is); coverage itself is `_live`'s
      `makeups.covering` as before. The instructor's own Certify digest is unchanged.
    - THE WORKER. The cheap test is part-aware: a response counts only where the certified row's written part was not the
      student's own complete work, and the bubble marker only where its bubble part was not (a student certified "scored, a part
      missing" for the bubble sheet holds written answers from the start, which kept the full evaluation running under the
      lifecycle lock every `RECHECK_EVERY`). An exact look that found nothing of anyone's waits for new activity; one that raised
      waits `RECHECK_EVERY` (no traceback every minute); one that met a write (`changed`) looks again at the next turn.
    - THE PAGE. The late papers waiting are shown only while the switch is on, as a fold "Late papers waiting · N" (open when
      short) with one line per student, the name opening the drawer and their own reason (the check row's own detail, not the
      plan-wide title); the plan's own reason, when the whole plan waits, reads "Faculty waits for you to certify again: …"
      (`auto_certify_state` and the view's `automatic.reason`, only when set). The lead's "who" ("N late papers", or the names)
      is a text button opening Students on the "Certified by Faculty" filter (front end only, from `automatic.latest.students`),
      whose rows carry a "By Faculty" tag (the drawer too), and a fold "Certified by Faculty · N" lists them. The gradebook
      names actor `automatic` "Faculty, by itself" and the change "Recorded <column> from a certification Faculty made by itself".
  - MAKEUP CHANGES CERTIFY THEMSELVES (requirement, 2026-11-14: "after assignments have been certified for the first time, it still
    seems like later scans or makeups still trigger the need to certify again. I thought we made it so that anything arriving
    after the first certify is automatically certified and uploaded to gradescope"; shown that of 17 certifications Faculty had
    made that term 9 left the plan reading changed, and that eight of their own re-certifications recorded nothing but a makeup
    that ended or began or a drop, they chose "Build both"; `certification_automatic.makeup_change`, `_find`'s `changes`). The
    same worker step, switch and single certification as LATE PAPERS CERTIFY THEMSELVES also take, with no paper at all:
    - ZERO: a student the latest certification left out until their makeup (MAKEUP OWED) who now counts 0 with no work: the
      absence is not excused, withdrawn or no longer covers the day, or the makeup was recorded missed (ABSENCES MAKEUPS
      "MISSED"). Their 0 is certified and written to their gradebook cell, as the instructor's next Certify would have (their
      2026-10-07 words: "it should put a 0 in if the absence is turned into not excused"). This is the one 0 Faculty certifies
      by itself: it follows from a decision already made, and ZERO ONCE DUE already counts the empty cell 0. It WAITS, with the
      reason on the page, while a paper or sheet of the assignment's scans has no student yet ("Not given 0 yet: 1 received paper
      without a student…", the "no work receive 0" check's own warning: it may be their makeup), while any other check names
      them, or while their gradebook cell holds a score above 0 or an excused or missing mark however it came there ("Not given 0: they
      have 14 for it in the gradebook; certify it yourself.", ABSENCES "A SCORE STANDS": a pull would replace an imported value;
      `_holds`). Its 0 goes only into a cell that is empty, cleared or already 0.
    - LEFT OUT: a student it gave 0 with no work (`no_paper` or `absent`) whose excused or undecided absence now covers the day
      it was given (`makeups.covering`, exactly whom the instructor's Certify would leave out): certified as left out until
      their makeup, and their certified 0 cleared (C11). If that makeup ends, ZERO gives the 0 back.
    - GONE: a student left out until their makeup who dropped with no work is no row of the live grades (a dropped student is
      never a confirmed 0): the new certification does not keep their row and writes nothing, so that drop asks for no Certify
      either (A DROP NEVER ASKS TO CERTIFY AGAIN).
    Nothing goes to Gradescope for any of them (no paper). The plan-wide waits (`inbox_pending`, a Gradescope send running), a
    value the instructor typed and a section outside the column hold them as they hold a late paper. LIVE OR CARRIED is the
    same rule: the confirmations are compared with these students counted as they were (`_confirmations` leaves every late
    student out of the live lists and adds them as the base classified them; a check nobody confirmed that lists only them is
    implied), and what a live certification keeps names the lists as they are now, so a plan whose only changes are Faculty's own
    reads current. `summary.automatic.changes` is {row: `zero`|`left_out`|`gone`} and `since_changes` the same since the
    instructor's Certify (a student left out by itself whose makeup paper then comes is a late paper). THE WORKER: the cheap test
    is also true when `progress.makeup_ended` is, or a certified 0 is now covered; its activity marker also counts
    `absence_revisions`, `absence_makeups_missed` and `roster_drops` (term-wide counts: any of them restarts every certified
    plan's settle) and follows the exam record's sittings and participants, whose writes are no event of the assignment. A DR date
    that passes with no paper, and a participant added since with no work, still wait for the instructor's Certify.
    THE PAGE SAYS WHAT FACULTY IS ABOUT TO DO (same day: twice in three days they pressed Certify again within a minute of a late
    paper being graded, during the `SETTLE` wait the page never mentioned). The view's `automatic.next` is Faculty's own rule
    read from the page's provisional state (`certification_automatic.forecast`: `papers`, `zero`, `left_out` with names, `rest`
    when a student moved who is not one of them, `wait` when a plan-wide block comes first; absent when there is nothing, the
    switch is off or the whole plan waits). While the certification reads changed the lead says "Faculty does this by itself in
    a few minutes: certifies …’s late paper; gives … 0 (makeup ended); leaves … out until their makeup." in place of "… until
    you certify again", adds "Your other changes wait for you to certify again." with `rest`, and Certify again… is the primary
    action only when something waits for the instructor. Afterwards the header names whom Faculty gave 0 and whom it left out
    beside the late papers ("gave … 0 after their makeup ended", `latest.students[].change`), and the fold "Certified by
    Faculty" says it on each row: a 0 Faculty gave is always said there.
  - Every participant is exactly one of scored, zero, excused or left out, or a BLOCK names them (`certification_rules.classify`).
    Unreadable or rejected reviews are a block ("record a grade"), never a certified 0. Excused students are never 0. A student
    with no work (no paper, no sheet; absent included, by the instructor's answer) receives a SCORED 0 only with the instructor's
    tick; a part with no work of an FRQ + bubble assignment counts 0 the same way; DR students whose (latest) booking is today or
    later are left out with their date and their own tick. "No scan is not an inferred absence" still holds: certify never
    writes `exam_participants`.
  - MAKEUP OWED (requirement, 2026-10-07, on the Quiz 4 Certify dialog: "is this going to give the students with pending excuses a
    0?"; asked, they chose to leave them out, "but it should put a 0 in if the absence is turned into not excused"). A student
    with no work whose excused OR undecided absence covers the day it was given (ABSENCES MAKEUPS' choice, read through
    `absences.makeups.covering`: the same absences and EXAMS "DATE GIVEN" as `owed`, without its paper read) is left out
    (`left_out`, reason `makeup`, view `makeup`) after a DR exam ahead and before a 0, under their own tick (`makeup_ahead`,
    "Leave these students out until their makeup"; members `row|absence|decision`). Nothing is recorded for them: their cell
    stays empty, an earlier certified value is cleared (C11), and GRADEBOOK ZERO ONCE DUE keeps it from counting while the
    makeup is owed. Once the absence is marked not excused, withdrawn or no longer covers the day, or the instructor records the
    makeup missed (ABSENCES MAKEUPS "MISSED", 2026-10-31; the 0's detail reads "Missed the makeup"), the empty cell counts 0 at
    once (ZERO ONCE DUE) and the certification is changed (`makeup_ended`): certifying again lists them among the students who
    receive 0 and records it, and since 2026-11-14 Faculty certifies that 0 by itself (MAKEUP CHANGES CERTIFY THEMSELVES). A makeup paper that comes back reopens it as any paper does, and is certified by itself once it is
    graded and cleared (LATE PAPERS CERTIFY THEMSELVES). Counts: `left_out_makeup` beside
    `left_out_dr` (summary, history, preview snapshot and workflow, each only when nonzero); the exam hub's Return stage (key `recorded`)
    names them as left out until their makeup. Excuse… on the Exams page is unchanged and still the way to excuse outright.
  - Gradescope not covered is either sent through the existing Gradescope dialog or answered "do not send" with a reason, one
    decision for the written grades and one for the bubble sheets; the previous reason is offered when the uncovered set is
    unchanged or smaller. Certification never contacts Gradescope. With the dialog's Gradescope tick (GRADESCOPE "AFTER
    CERTIFYING", 2026-10-12: "Send to Gradescope and publish the grades after certifying", offered while `auto_gradescope` is on
    and Gradescope is set up, ticked by default) a group left unanswered is the decision `send`, which Faculty carries out by
    itself afterwards; "do not send" with a reason still stands for its group.
  - The page may name and search students (names from the verified roster; UIN digits only in a POST body, answered with names);
    the Review step stays anonymous, except that its review desk's **Show student** (requirement, 2026-09-29) fetches one response's
    name and section number when clicked (GET `/api/grading/assignments/<aid>/responses/<rid>/student`, `response_student`; no UIN or
    enrollment ID) and keeps nothing: the next response starts anonymous. Final changes only through the ordinary review editor and `save_review` (no drafts), or a
    batch adjustment (below), whose reviews are ordinary reviews too. The page's own state is browser-only: the open question in the address (`?question=<qid>`), the student drawer never in
    the address, and two localStorage conveniences in try/catch (`faculty:certification:tab`, `faculty:certification:hide-names`).
  - FLAGS SETTLED BY A REVIEWING MODEL (requirement, 2026-09-27): a grade is `settled`
    (`certification_rules.settled`) when its flags are only `arbitrator` and/or `flag_reviewer` and each ruled (adopted or revised;
    finalized). The page counts it apart ("Still flagged · N more settled by a reviewing model", "+N settled" per question, a
    "Settled by a reviewing model" response filter) with a neutral chip naming who settled it; `flagged` means still flagged. The
    models' original flags are still recorded and shown in the drawer. An unreliable or unsettled ruling, a flag reviewer asking
    for manual review, or any other flag keeps a grade flagged.
  - BATCH ADJUSTMENTS (requirement, 2026-09-27; asked, they chose a NEW line given as a named
    point adjustment, on FINAL grades only; `grading/batch_adjustments.py`, `static/grading-certification-adjust.js`). A question
    is one version's; the question panel's **Add a line to every grade…** takes a section (or `all`), the line's wording and its
    points (nonzero, at most the question's worth, two decimals). Preview (POST `.../certification/questions/<qid>/adjustments/preview`)
    writes nothing; apply names its fingerprint, `request_id` and `confirmation: "adjust"`, plans again under the lifecycle lock
    (409 when anything changed), re-checks each response row and the rubric pin in ONE `BEGIN IMMEDIATE`, and writes one ordinary
    review per final grade (`final_decisions`: the counted grade's transcript, lines, earlier adjustments, status and comment, on
    the result it was made on, plus the line; the adjustment counts before the bounds like every adjustment), each with its
    feedback row and `response_reviewed` event, and ONE `grades_adjusted` receipt a repeated request ID replays. A response not
    final, already carrying the line, or whose score the bounds hold is left alone and listed. The rubric never changes: nothing is
    regraded and no other section moves; Gradescope gets the line as the grade's point adjustment and its comment (Sending grades
    to Gradescope, below). It is not a correction of the
    models: an automatic acceptance it replaces records NO acceptance review (a blind one records `blind_revealed`), the feedback
    row names the batch, and the review burden counts a batch as one action. This supersedes the instructor's "no bulk grade
    editing" of 2026-09-26 for this one kind of change. A later rubric change regrades these grades like any decision.
    SEEN AND EDITED (instructor, the same day): every grade's adjustments are named beside its rubric lines (the question panel's responses, from
    `question`'s `adjustments`, and the drawer's read-only view), and "Lines added to every grade" lists the question's batches
    (`batches`, from the `grades_adjusted` receipts) still on some grade, each with **Edit…** (`action: "edit"`, new points and
    wording) and **Remove…** (`action: "remove"`), plus their history. Both take `batch_id`, go through the same preview and
    apply, and act ONLY on the grades that batch changed which still carry its exact line (edited in place, or removed); a grade
    changed by hand since, or no longer final, is left alone and listed. Each edit or removal is a receipt of its own (`action`,
    `of`), so an edited line can be edited or removed again; a removal cannot be. A line still standing (`standing_lines`) that a
    late paper graded afterwards lacks keeps Faculty from certifying that paper by itself (LATE PAPERS CERTIFY THEMSELVES,
    2026-10-09 review); applying the line again adds it only where it is missing.
  - GRADED FOR PARTICIPATION (requirement, 2026-11-01: "I want to add an option to the assignment drafter that is "graded for
    participation". this shouldn't change anything in the pipeline itself, but after grading, it should give everyone that has a
    submission full credit. I still want the rubric lines to be shown so that students get feedback"; shown the plan, "i like
    everything", and "graded for participation assignments should default to only one version"; `grading/participation.py`).
    The setting is the TERM grading plan's (`frq_participation`, migration 052, append-only, the latest row stands, none is
    off): ticked in Draft a quiz with a model (recorded by its approval; ticking it sets Versions to 1 until a number is typed)
    or on Create assignment's Details (recorded once the plan exists; unticked writes nothing), and changed at any time on
    Certification and Export (GET/PUT `/api/grading/assignments/<aid>/participation`, `{enabled}`). Grading never reads it:
    rubrics, models, flags, reviews, acceptances, training examples and metrics keep the real scores, and a batch adjustment or a
    regrade starts from the real grade. What COUNTS a final grade reads it (`counted_decisions` on `final_decisions`' answer,
    `counted_entries` on `decision_rows`): the gradebook's totals (`sources`, so Certification and Export, its certified rows
    and every pull), the export, Sending grades to Gradescope and its coverage, and the Gradescope comparison. Each final grade
    below its question's worth gets one more adjustment, "Graded for participation", the worth less the grade's UNCLAMPED score
    (Gradescope adds an adjustment before its own floor, so the same line gives exactly the worth there), and counts as the
    worth. It is never stored on a grade, so a regrade, a flag review or a late paper gets it on the next read and turning the
    setting off takes it away (a certified assignment then shows the change until it is certified again). Only a final grade
    gets it: a student with no paper has no grade (ZERO ONCE DUE), and a paper is a submission however little is written on it.
    It is named beside the grade's lines (the question panel, the student drawer) and an information check counts the grades it
    lifts. Written questions only: an assignment with a bubble-sheet answer key cannot be set (409), and Certification and Export
    blocks (`participation_bubble`) while a plan has both.
    THE CERTIFICATION COUNTS THEM TOO (requirement, 2026-11-12, of the first Take Home Assignment reading "Grades changed since the
    certification" from the moment it was certified, asking the Assistant "what changed to cause the certification to revert?";
    nothing had). A certification records its grades as they count, with the line, and whatever asks whether it still stands
    reads them the same way (`progress.decision_state` counts whichever rows it is given; counting a counted row changes
    nothing): a certified plan reads changed when a grade's lines or the setting changed, never by being graded for
    participation. Until then the check read the grades as stored, so every such certification with a grade below its worth
    read changed at once, and the setting turned on after certifying read current. Certify just this change reads the grade as
    certified with the line it was certified with (`certified_content`: an entry certified at its worth whose stored grade is
    below it), and a change of lines that moves no points, as every change on such a plan is, is certified alone as any other
    (no row then differs from the certification, which is not "more than this one grade").
    THE WAITING GRADES APPROVE THEMSELVES (requirement, 2026-11-11, of 105 Take Home Assignment answers waiting for their review:
    "for assignments that are created with the 'grade for participation' option checked, can we make it so that flagged responses
    automatically get approved, in other words, I want it to still grade and flag things as normal so I can see that information,
    but then I want it to approve the responses automatically without fixing the flags since the student will get full credit
    anyway"; shown what that meant point by point, "you've understood everything correctly, build it"; `participation.approve_waiting`,
    the worker's `auto_participation_one`). Grading and flagging are unchanged. While the setting is on, however it came to be on
    (ticked at the draft, on Details, or later on Certification and Export), every grade that waits for the instructor's review
    (`WAITING`: the states Certify counts as "waiting for review") is approved by the worker as the graders left it: an ordinary
    approved review of the grade's own result (`final_decisions` counts it as any review), actor `automatic`, comment
    `participation.APPROVED`, with the result's transcript, ticked lines and score, checked as a review of the instructor's is
    (`validate_task`: the rubric, question and paper that stand, a completed task). The result is untouched, so its flags stay
    where the pages read them (Certification and Export's question table and student drawer, and the answer itself). One look a minute at most per plan
    whose activity changed, never while a batch or a flag review of it is queued or running or after a cancelled or reset batch;
    one `participation_approved` event names every review written, each `response_reviewed` saying `participation`. NO FLAG REVIEW
    IS SENT BY ITSELF on such a plan (`_flag_review_waits` answers `participation`; the button still sends one). LEFT FOR THE
    INSTRUCTOR: an answer with no proposed score or whose ticked lines do not add up to it, a draft, an answer they marked
    unreadable or rejected, a failed answer, a paper waiting for its version, and Certify. THE APPROVAL IS FACULTY'S, NOT THEIRS
    (`participation.APPROVAL`): it is no training example (`_unsaved_decisions`), no truth the graders are measured by (`metrics`,
    `signal_report`), no action or false alarm of the instructor's (`burden`), and it writes no feedback row; the rubric-memory
    pass at Certify reads the graders' doubts as on any plan and nothing of the approvals. A LATE PAPER settled this way
    CERTIFIES ITSELF (instructor, same day, told that it waited for their Certify: "i want late participation-only assignments
    to be auto certified as well"): the approval clears its grade as a review of theirs does (`certification_automatic.cleared`),
    so LATE PAPERS CERTIFY THEMSELVES takes it once every other condition holds, with its Gradescope send where the assignment
    uses Gradescope (GRADESCOPE "AUTOMATIC LATE-PAPER SEND"). Certification and Export's "Graded for participation" note counts them. Turning the
    setting off approves nothing more and takes nothing back. This is the second automatic exception to "nothing confirms a blank
    but the instructor's look" (the first: A FULL-CREDIT LINE REACHES LATER PAPERS): the answer cannot change the score.
    A FULL-CREDIT LINE REACHES LATER PAPERS (requirement, 2026-11-04, of two Quiz 6 makeups scanned after they told students problem 4
    was not required: "they automatically get full credit for it"; the makeup was a new version E, which the lines on A to D never
    reached, and nothing held those papers back; `batch_adjustments.full_credit_lines`, `reach_full_credit`, the worker's
    `auto_full_credit_one`). A standing line in every section worth at least the whole question is full credit for everyone, and
    no other kind of line gets this. Faculty adds it by itself, actor `automatic` on the receipt and reviews, when no batch or flag
    review of the plan runs and before it certifies a late paper: to every answer of that question lacking it, including one held
    for review (the only automatic exception to "nothing confirms a blank but the instructor's look": the answer cannot change the
    score, and the review's comment says it was not looked at). A later version of the question (`question_key` up to the version
    letter) has the line too when every version that existed at the first such line carries one, so a line on one version alone,
    that version's misprint, reaches no other. A grade the instructor typed (`manual`), a draft, unreadable or rejected review and
    a failed answer are left alone. Removing the line stops it only once every batch of it on that question is removed.
  - WHO SEES A COMMENT (requirement, 2026-11-11, of a grade Faculty approved for participation whose comment said the answer was
    not looked at: "is this part seen by students?", then "Can we make sure it's clear which comments will be seen by students
    and which won't"). What is sent is unchanged (Sending grades to Gradescope, below: the ticked lines, the point adjustment and,
    as the only comment, the grade's batch lines and its participation line; a regrade reply is the instructor's own send). Every
    field that holds such words says who reads them, in one wording inside the field's label (`static/ui.js` `SEEN_BY`,
    `seenByMarkup`): "Only you see this" on the review desk's Comment / manual grade reason, and beside "Your comment" in the
    read-only grade; "Students see this on Gradescope" on the wording of a line added to every grade; "The student sees this on
    Gradescope" on a regrade reply. Under the desk's Adjust points: students see the points on Gradescope, not the reason, and
    only a line added to every grade shows its wording there. A new field whose words reach students, or that could be taken
    for one that does, carries the same mark.
  - PUTTING BACK CERTIFIED GRADES (requirement, 2026-09-29): `restore_certified(job, request_id)` (POST `/api/grading/jobs/<jid>/restore-certified`
    with `confirmation: "restore"`, checked on the server) points each response whose current result came from that batch back at the
    result the latest certification certified, only where it certified an AUTOMATIC decision whose acceptance of that result is still
    active, the response has no instructor decision and nothing runs. Nothing is erased; one `certified_grades_restored` event names
    every pointer moved and every response left alone. Ask before regrading a certified plan.
  - Certify: preview (writes nothing), then POST with the fingerprint, `request_id`, `confirmation: "certify"`, each confirm
    check's digest and each Gradescope decision, all checked on the server. The dialog has NO FINAL TICK (requirement, 2026-10-11:
    a repeat of what the groups above it confirm; it was never a server rule): once every group is answered Certify is offered,
    the footer says which gradebook column it records in, and clicking Certify is the confirmation, as Approve is on the review
    desk. Enter on a tick never submits the dialog. The gradebook-column confirmation stays: it is asked once per column and is
    where a new column gets its assignment. Only block and confirm checks are fingerprinted (an
    information check such as training still being saved moves on its own). Lock order: the roster lifecycle lock, every
    payload and grading read OUTSIDE any transaction, then ONE `BEGIN IMMEDIATE` that re-checks catalog high-water marks only
    and writes the certification, its rows, the gradebook's values and a `grades_certified` event. Nothing is partial; the same
    request ID replays. Nothing here publishes, emails or sends a grade: with `gradescope_after: true` in the request (the
    dialog's tick; refused when not offered) the same transaction only queues Faculty's own Gradescope run
    (`gradescope_after.queue_run`, kind `certified`), which the Gradescope sender works afterwards (GRADESCOPE "AFTER
    CERTIFYING"); the `grades_certified` event then says `gradescope_after`.
  - ONE PLACE TO SEND (requirement, 2026-10-18: "make all of your suggested changes", pipeline review item 10): Send to
    Gradescope… is the Gradescope fold's panel button (plus, unchanged, the per-student "Send to Gradescope…" on a late paper
    Faculty did not send, and the Certify dialog's own send of a group). The checks "Written grades / Bubble sheets not on
    Gradescope" keep their server action (`send_written` / `send_sheets`, API body unchanged); the page shows it as **Open the
    Gradescope section**, which opens the fold, mounts its panels and focuses that kind's Send button. It sends nothing itself.
    Publishing has one place too (GRADESCOPE "ONE PLACE TO PUBLISH").
  - The gradebook values are planned from the certified rows by one pure function (`sources.certified_totals`) for the
    preview, the certify and later pulls, against the setup as it was, a column to create named `new:<assignment>` until
    written, so the preview's and the apply's plans agree. A column linked to some parts takes only those parts' certified
    values; a left-out student's cell an earlier certification wrote is cleared.
  - THE COLUMN AND ITS CATEGORY (2026-10-02, after Quiz 3's certification made an uncategorized column that a weighted gradebook
    does not count). The Certify dialog chooses the column in ONE select: "Same name" (live unlinked columns titled like the
    assignment, spaces and case aside), "New column", "Other columns (N)". SAME-TITLED DEFAULT (requirement, 2026-10-02: "if
    exactly one live column has the assignment's title (e.g. the Canvas-imported 'Quiz 4'), certification records into it by
    default; otherwise it creates a new one; the instructor still sees and can change the choice"): `certification_options` lists
    that one `use:` option first, so it is the preview's `selected`; two same-titled columns, or none, keep a new column first.
    Archived columns and columns linked to another assignment never count. The order is in no fingerprint (the options' plans are
    keyed by option), so the default moving needs no new preview and changes no digest. With the same-named column chosen the
    dialog says where the values go ("Certifying records into the existing column “Quiz 4” in Quiz Category, which has this
    assignment's name") and what becomes of those already there, by `common.change_action`: a value typed or cleared in Faculty
    stays unless Replace is ticked; an imported value (Canvas, a file) or an earlier pull is replaced (with nothing typed there for
    these students, no Replace box: "Nothing there was typed in Faculty for these students"); no duplicate-name warning. Choosing
    a new column instead warns that it makes a second column of that name. Check again (after Excuse…, say) keeps a column and a
    category the instructor chose while the server still offers them: both choices may carry the same title. Any existing column
    chosen, the same-named default and the assignment's own column included, that would not count toward Overall in a weighted
    gradebook (no known category, or a category of weight 0) gets the same warning a new column gets, with where to move it
    (the gradebook's Setup) or, when one is offered, a new column (review of 2026-10-02: the default must not record silently into
    a column that counts for nothing). A new column's category is chosen beside
    it: the preview's additive `gradebook.categories` ({id, name, weight, counted}, Uncategorized last; weighted mode counts a
    category with a weight above 0 and never Uncategorized) and `gradebook.mode`, preselected with `gradebook.proposed_category_id`
    (the category of a same-named unlinked column, else of the most recent grading-linked column whose title starts with the same
    word, else none). The dialog warns when the category chosen would not count toward Overall. The category is in no fingerprint
    and no digest, so choosing it needs no new preview; the certify request's `category_id` (null: Uncategorized) is what a new
    column gets. The new column's check names the category (`category_details` holds its words for each one) and, uncategorized
    in a weighted gradebook, says it will not count. Nothing recategorizes a column already made (Quiz 3's stays the instructor's
    to move). An option's column check no longer repeats every option (the preview's `gradebook.options` holds them).
  - ABSENCES BESIDE ZEROS (requirement, 2026-10-02: "in the Certify dialog, show recorded absences next to students about to get 0
    (display only; nothing is excused automatically)"). The preview's `zero_no_work` rows gain an additive `absences`: every
    absence recorded for that SECTION MEMBERSHIP in the plan's term (ABSENCES' owner, read through `absences.service.standing`,
    catalog only), each {first_date, last_date, dates, status} plus `covers` when the assessment has a date for the student and
    the absence is not withdrawn (a withdrawn absence claims nothing). That date (EXAMS "DATE GIVEN", `exam_workflow/given_dates.py`, shared since 2026-10-03 with ABSENCES "MAKEUPS") is the student's own sitting's on the Exams page
    when it has one (a makeup, say); else the earliest exam date of the assignment's seating charts naming their section, leaving
    out a DR exam's chart and a chart tied to a makeup or DR sitting; else the event's first main sitting with a date (campus
    wall-clock values compared as written). The dialog lists them under the row's detail and Excuse… ("Absence
    recorded Fri, Sep 25 · not excused"), with a "Covers the day it was given" chip, a warning for an excused or undecided absence.
    Display only: nothing is excused, no exam record, gradebook cell or absence is written (ABSENCES NO-GRADEBOOK), and excusing
    stays the instructor's Excuse… on the Exams page record. Only the preview reads them (certify does not), and a check's rows
    are in no digest (`zero_no_work`'s members are `row|reason`), so the fingerprint and every digest are unchanged.
  - The page and the dialog say what else certifying does ("What certifying changes": the assignment folds into Archived on its
    other pages, the Exams page included, its flagged charts, card issues and failed print jobs stop asking for attention
    (CERTIFIED-ARCHIVE), a view that deletes nothing, and any later change brings it back as Certify again). Once certified, the band
    links the gradebook column (`#gradebook/<term>?item=<id>`), the lead counts the certification's own record (students, scored,
    given 0, excused, left out), students certified at 0 read "Certified 0" instead of a warning, and a finished quality audit
    is one fold, open while it is newer than the certification. Before certifying, zeros are the band's warning title,
    "Certifying records 0 for N students", with every other item to confirm counted in its body; "Nothing blocks certification"
    is said only when nothing is left to confirm (usability audit, 2026-10-04: 28 zeros sat in a calm notice by that title).
    While nobody has a grade yet, the band says certifying would
    record only zeros and the pipeline's current step is the header's primary (Certify grades… stays, plain). The Gradescope
    panels mount when their fold opens (or when the page is given `open: "gradescope"`, which `?tab=export&open=gradescope` does
    once the grading frame passes `open` to the export tab), and a send settling in a panel reads the page again.
    `?tab=export&open=students` (2026-10-09, a certified assignment's row on Assignments, ASSIGNMENT-WORKSPACE "INDEX TABS") opens
    the page once on Students with every student shown; like any `open`, it leaves the address, and the tab remembered for an
    ordinary visit stays as it was.
  - Decisions this SUPERSEDES or EXTENDS, named as such: the Export completion rule (replaced, above); bubble sheet grading's
    "scores are DERIVED on every view ... never stored" (extended: the certification's immutable snapshot stores each certified
    bubble score, made at the instructor's request, as the gradebook already does); the gradebook's "never guesses a score for
    a student who is waiting" and "absent is `missing` only when the instructor ticks it" (extended: a certified 0, absent
    included, is the instructor's explicit confirmation recorded with the certification, never a guess; a live pull never
    makes one, and a pull of a certified plan records only the certified 0 the instructor confirmed); and "Pulls read ONLY final grades" (extended by the orchestrator's default "the gradebook keeps the certified values
    until the next certification": a certified plan's Pull reads its latest certification; plans never certified pull live).
- FINAL QUALITY AUDIT (requirement, 2026-09-27: a "Final Quality Audit" button on Certification and Export "to have the grades
  reviewed for consistency and fairness"; they chose the Rubric drafting and review
  model, findings that INFORM and never block certifying, and one audit of the whole assignment; `grading/quality_audit.py`,
  migration 031, `static/grading-certification-audit.js`). An audit is a `frq_quality_audits` row of the TERM grading plan, its
  calls ordinary cloud calls it owns (`quality_audit_id`, replayed from receipts, cancelled like a rubric draft). Its input is a
  snapshot of the FINAL grades (`final_decisions`): per response its lines, score, final answers, a transcript excerpt and who
  decided it, labelled R1.. in an order that keeps similar work together; the model never sees an identifier, name or section.
  Three layers: free checks without a model (`audit_checks`: same final answer with different lines, versions or a section scoring
  far apart; an answer that is only a mark, a circle, slash or tick or a circle read as O, groups nothing, `telling`, 2026-09-29); a TEXT pass per question and version (`quality_audit` prompt, `CHUNK` 60 rows per request, instructor-reviewed
  anchors beside each chunk so every chunk is judged by one standard; instructor-reviewed grades are the standard); and a SCAN check
  of each finding (at most `MAX_CONFIRM` per question) with the crops of the responses it names (`quality_audit_confirm`). Findings
  keep only refs they were given (never an anchor alone) and known line IDs. It changes, approves and finalizes NOTHING: each
  response in a finding opens that student's grade in the ordinary drawer, where the instructor changes it through the review desk
  or a batch adjustment; a finding notes grades that changed since the snapshot. One audit per plan runs at a time.
  `quality-audit-v2` adds a stable image-first sample of up to three automatic grades per question/version across available
  zero/partial/full score bands, even when the text pass finds nothing. The model reads images and rubric BEFORE saved work
  or grades; a separate call compares its reading with the snapshot. The result reports selected/read counts, unavailable
  images, retained readings and findings; a sample is diagnostic, not a measured accuracy rate. Cross-version checks use
  retained rubric origins and standing finalization families, NEVER matching printed numbers. Unmapped questions are reported.
- No remote infrastructure or files are provisioned by hosted endpoints. `store:false` does not
  prove provider log deletion. Cancellation stops remaining work and cancels durable provider
  jobs where permitted. A cancelled batch is never claimed again, so a restart SETTLES its interrupted
  cloud work (`_cloud_recover`: tasks cancelled, requests still `submitted` become `unknown`, nothing
  resent, one `cancelled_work_settled` event); never put it back to `pending`, where it stayed for
  good and the Grade step counted it as queued (2026-09-20). A request can
  also outlive its task (interrupted by a restart, its task back to `pending`, then cancelled with the
  batch): `cancel_job` settles such a request without a durable provider ID as `unknown` with its task,
  and `_cloud_recover` settles any `submitted` request of a cancelled task (2026-09-21), or of a
  failed or complete one (2026-09-24). The Inbox worker raises its own
  open-file soft limit to 65,536 at start (`manage_inbox.raise_open_files`). Never automatically publish grades or send grading email.
