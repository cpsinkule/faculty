## Jev decisions

Rule ID: **JEV**. Requirement, 2026-10-03: "build 1, 2, 3 and 6": semantic
follow-up, student-request routing, absence draft verification, Assistant evidence
checks. No retired grading scheme is restored.

2026-10-12 LEARNING FROM CORRECTIONS (the instructor: "Replace them"): the lesson checks of 2026-10-08
(`followup-learning-analysis`, `followup-learning-match`, their provider variables and thresholds) are withdrawn with the lesson
review; their receipts stay. A correction the instructor made of a follow-up's card is judged by `assistant-memory-edit`
(MEMORY below; ASSISTANT-MEMORY "LEARNED FROM AN EDIT"), on Jev alone, with the existing receipts and no-resend rules: a
correction left mid-way keeps nothing and is not asked about again. It confers no permission. What is learned NEVER changes
an excuse, grade, identity, exact date, action tier or send decision: it is words a later preparation is told.

2026-10-09 TOOL-USE: the instructor requested routing, preflight intent/context, result coverage and completion assistance.
`assistant-tool-route`, `assistant-tool-preflight`, `assistant-tool-result` and `assistant-tool-completion` use the existing
Jev provider and workspace receipts (rule `assistant-tool-use-v1`). Requests contain bounded ephemeral task/trace evidence;
receipts retain hashes, question definitions and answers only. They recommend or restrict; none authorizes execution.
ASSISTANT defines budgets, conservative thresholds, cancellation, one corrective pass and unchanged approval boundaries.
Their requests carry the attempt of the instructor message they serve (2026-10-09 review): sending a message again is an
explicit retry, while a failed/uncertain receipt is never resent within its turn or that message's continuations.
2026-10-12 TUNED (the instructor: "do some experiments and tweak it until we tune it correctly"): the preflight's evidence,
questions and scores changed, under its own rule `assistant-tool-preflight-v2` (ASSISTANT "TUNED"); the other three purposes
keep `assistant-tool-use-v1`.

2026-10-09, instructor-requested follow-through: `todo-thanks-v2` replaces the message-only completion question with separate
`owed` (another reply) and `work_owed` questions. The follow-up's purpose and next step, recent grade entries, appointments
and absences accompany the captured conversation (`todo-thanks-v3`, below). Both must be at most .05 and context complete
before the existing automatic close; either at least .9 keeps work owed. Changed evidence requires a fresh receipt; a failed/uncertain identical request is not resent.
All To do follow-ups may receive this advisory check; automatic closing still requires the qualifying thank-you reply.
`followup-draft-v1` checks unsent proposed replies for contradictions and unsupported promises at .9; one bounded corrective
turn is allowed under ASSISTANT. These purposes use the existing Jev provider, no new credentials or provider routing.
They judge meaning; exact numeric values, dates, identities, permissions and writes remain with code and the owning services.

2026-10-09 review fixes (the instructor: "Fix all of the issues and implement your suggestions"). TO DO THANKS v3: v2's state
embedded FOLLOW-UP FACTS, whose open meeting times, rolling 14-day window and iClicker "not checked yet" lines moved with the
clock, so every To do item was asked again as time passed and every listing poll rebuilt them. Rule `todo-thanks-v3` reads a
clock-free state of catalog revisions only (messages, purpose and next step, latest grade entries, appointments, absences;
`followup_checks.obligation`): it changes only when one of them does. A listing looks the receipt up by that state's
fingerprint and builds no facts; only the worker asks. The draft check keeps the full facts. No Jev or Decisions input carries
the instructor's private student notes (STUDENT-NOTES): the draft, obligation, evidence (EVIDENCE INPUT) and tool-use
states, and a correction's comparison (MEMORY, `assistant-memory-edit`), leave them out. The draft check reads only the Assistant's drafts, never the instructor's edited version.

- **OWNERS.** The adapter, status route and `jev_calls` are workspace computing
  receipts. A conversation classification references the exact student's term
  enrollment and communications revisions; it is no new communications owner.
  Absence checks remain ephemeral section-membership drafts; Assistant evidence
  and verdicts remain in its ordinary conversation lifecycle. Receipts retain
  hashes, questions and typed answers, never copied message/source bodies or keys.
- **PROVIDER.** Only server-side `https://api.typesafe.ai/v1/systemone`, without
  redirects, with the configured `TYPESAFE_API_KEY` and pinned `jev-1.13.0`; a check that looks at
  pictures asks pinned `jev-1.14.0` (`IMAGE_MODEL`, JEV PICTURES below). Two checks ask another host in Jev's shape: student
  triage may ask OpenAI Decisions (TRIAGE PROVIDER), and whose paper a first page is asks Gemini Decisions (STUDENT MATCH
  PROVIDER).
  The key stays reserved from generic chat/grading connections. Bounded requests,
  timeouts, validated finite probabilities, exact call fingerprints, durable
  receipts and exclusive claims. An incomplete/failed/uncertain call is never
  automatically sent again. Explicit retry creates a separately identified call.
  The one exception is a host refusal before any answer (HOST FAILURES, 2026-10-09, below).
- **NOT THE COORDINATOR'S** (2026-11-14, COORDINATOR, TEACHING-WORKFLOWS "A COORDINATOR'S FOLLOW-UP"): the conversation
  analysis, SAME MATTER, the thanks and closing readings, the follow-up record check and the reply draft check read
  students' conversations only. A conversation with a term's coordinator is never sent to any of them.
- **FOLLOW-UP.** Analyze current captured conversations after the existing
  tracking cutoff, including the recent same-conversation context. No identity
  or thread matching by names/subjects (SAME MATTER below reads a new conversation's content
  against the same student's follow-ups; it never matches a name or a subject). Only a strongly supported acknowledgment
  or FYI with no unresolved instructor action moves a suggestion to FYI; ambiguous,
  failed, unavailable or changed-source checks keep the existing attention rule.
  FYI remains inspectable. Manual tracked states/reopening are never suppressed.
  Labels and existing-page shortcuts are advisory; no absence, appointment,
  grade, mailbox state, note or outside write results from classification.
- **ABSENCES.** Verify the model's proposed dates, reason and details against the
  exact server-read email. Resolve dates in code against its campus-local sent
  date; unknown/conflicting dates/reasons stay blank with review notes. Jev does
  not decide an excuse, interpret attachment proof, or save a record. Failure is
  disclosed without losing the draft. The selected generative Assistant is kept.
- **EVIDENCE.** Keep a bounded ephemeral copy of text actually read through the
  Assistant's existing tools, after secret removal. Check relevance and each
  bounded answer paragraph against those observed sources before releasing text.
  Display unsupported/contradicted/uncertain verdicts and omissions as advisory
  review notes, with source digests (in Assistant chat, inside the answer's closed
  "How it got this", only a contradiction outside it: ASSISTANT QUIET STEPS); never
  call a check proof of correctness or remote current state. Image contents cannot be verified from file metadata.
  Read failure/cancellation releases no success verdict. Checks have no tools and
  cannot authorize or execute any action. Existing tiers/Approve cards remain.
  EVIDENCE INPUT (2026-10-05, requirement, TEACHING-WORKFLOWS EVIDENCE WARNING): for a
  follow-up preparation the observed sources also include, first, the trusted text
  Faculty gave that preparation after its instructions and the exact requests of the
  cards it proposed (secrets removed, bounded); they are not reads and are not listed
  as Sources. The question is a short statement of the task, not the instruction.
  2026-10-09: that text leaves out the instructor's private notes.
- **VALIDATION.** The API rounds probabilities, scores and confidence to two
  decimals (measured 2026-10-03: sums off by .010 over 55 options, confidence off
  by .020 from its formula). Accept sums within .005 + .005·√n, choice confidence
  within .03 of (max − 1/n)/(1 − 1/n), a chosen option within .01 of the most
  likely, and a score within its rounding of the probability-weighted level.
  Anything outside these bounds is a failed answer, never resent.
  Student triage alone may ask another provider: JEV TRIAGE PROVIDER (2026-10-08) below.

### Additions of 2026-10-03 (instructor: "I like 3, 4, 8, 9 … flesh those out"; "complete the still to dos")

Shared: Jev decides, code acts; nothing saves, sends, links or grades without the
instructor's existing click (the instructor's exceptions: THANKS CLOSES of 2026-10-04, SAME MATTER and TO DO THANKS of
2026-10-07, below). Dates, numbers, points, rooms and names are compared in
code. One request per item, every question in it. A Choice that decides something is
asked twice in that request with its options reversed and counts only when both
agree (Jev 1.13 leans to the first option). The new routes are `NEVER` for the
assistant (`assistant/policy.py`): each spends a request or clears a concern.

- **URGENCY/WELLBEING** (`student_support/triage.py`, rule `student-triage-v2`).
  WELLBEING OFF (2026-10-12; the instructor asked "can we turn off the wellbeing check, does it do anything besides flag
  it?", was told what else it did, and chose **"Off entirely"**): no reading is a concern (`triage.WELLBEING` is a gate no
  probability reaches). Nothing is pinned first, forced to need attention, counted on Home, kept out of FYI or kept from
  closing on a thanks, and nothing waits for Mark reviewed; a message is listed by what it asks, like any other. The
  question stays in the one triage request, so readings already kept stay valid and no conversation is read again; reviews
  already recorded are kept. What this rule, REVIEWED HOLDS and WELLBEING CARRIED say of a concern describes the check as it
  was, and as the gate at .30 would restore it. Urgency is unchanged.
  The triage request also asks an urgency Score (routine / soon / time-sensitive)
  and a wellbeing Noul. Its state adds code-made context ANCHORED TO THE LATEST
  STUDENT MESSAGE'S SEND TIME: `relative_dates_resolved_by_code` and `upcoming`
  (the student's own term and sections' exams and due dates in the next week, as
  named buckets, never dates), so a verdict never changes with the day it is read.
  The wellbeing question names routine sickness (a cold, fever or flu that only
  explains an absence) as NOT a concern: measured on invented messages, a looser
  wording pinned every sick-day email (.65), this one gives .16/.08 while grief, an
  ER visit, hopelessness, panic attacks and a threat stay at .86 or more.
  A chip shows Soon/Time-sensitive at confidence .6 or more; the list sorts by an
  open wellbeing concern, then attention, then urgency. Wellbeing at .30 or more
  (a missed concern costs more than an extra look) pins the conversation first,
  forces attention, is never FYI, and counts on Home as "Possible wellbeing
  concerns" (a count and a link, never a name; marked `counted_in` the follow-up
  card, which already counts it, so the sidebar badge counts it once:
  TEACHING-WORKFLOWS "NEEDS ME COUNT"). The wording is advisory, never a
  diagnosis. It clears only by the instructor's Mark reviewed
  (`student_support_wellbeing_reviews`, migration `student_support/003`, owned by
  the enrollment, naming the exact receipt, append-only) or by resolving the
  follow-up. REVIEWED HOLDS (2026-10-06): the review covers later analyses of the
  same conversation until a student message is captured, edited or linked after the
  reviewed analysis was made, so new `upcoming` context alone never re-raises it; a
  newer message analysed and flagged again shows again. CARRIED (2026-10-09, below): a concern raised stays raised whatever
  reads the conversation next, until one of those.
- **SEND-CHECKS** (`jev/send_checks.py`, rule `send-check-v1`; `POST /api/mail/check`,
  `POST /api/canvas/messages/check`; `static/send-checks.js`). Notes beside the
  review of a mail, a Canvas conversation post, a Canvas mass message and an
  assistant mail card (filled by the instructor's browser). Send, the preview and
  its fingerprint never change and never wait. Code: quoted history set aside,
  dates (month names, m/d, ISO, today/tomorrow/this weekday) resolved against the
  campus date, a weekday that does not fit its date, another student's full name
  (any student's, in a mass message), a nine-digit number. Jev: each sentence of
  the student's message (read from Gmail by the server, never sent by the page) —
  does it ask something (.8), does the draft answer it (.3 / .7); which Faculty
  exam or due date a date mention refers to (event names only, never dates, both
  orders, .6), then code compares date, time and room, and notes a date that
  differs by section in a mass message; whether a draft that says "attached"
  carries nothing (.7). Jev off or failed: Faculty's own checks still run, said so.
- **ROUTING** (`jev/routing.py`, `POST /api/jev/route`, rule `palette-route-v1`;
  `static/palette.js`). A sentence of three words or more typed in Ctrl K, after a
  400 ms pause, with the palette's own places (never students, scans or recent
  places). One request: the place as "kind: name" options with each kind's purpose
  (both orders) and the grading step alongside. Measured on 18 typical sentences:
  a separate "which kind" step was unsure on half; this design agreed on all 18.
  Best match at .6 (navigation writes nothing); a weaker guess follows the word
  matches; a question offers the assistant with the text filled in, never sent.
  Numbers are matched in code. The sentence is hashed in the receipt, never stored.
  Since 2026-10-18 (FRONTEND.md "ONE ENTRY PER ASSIGNMENT") the palette lists each assignment once, under Assignments, with
  no Exams or Grading groups; an assignment with a grading plan is still sent under the "Grading" kind, so the step question
  applies, and a routed step opens `#grading/<plan>?tab=<step>` inside the shell, labelled with the tab words.
- **CANVAS-MATCH** (`jev/matching.py`; docs/decisions/canvas.md "JEV MATCHES").
  First-time Canvas assignments the titles could not place: one request each (its
  title and group, the live columns' titles and categories). Suggested only when
  both orders agree at .8, the column is free in its course and worth the same
  points (code); otherwise shown first under Likely matches. Only the instructor's
  preview asks; Import reads the receipts and never calls Jev.

- **BANK-TAGS** (`question_bank/jev_tags.py`, rule `bank-tag-jev-v1`; question-bank.md
  "JEV FIRST, MODEL ON DOUBT"). Jev places a question's section, difficulty and
  types, then its topic among that section's topics; what it is sure of is sent to
  the model as `placed`, which then writes only the objective under it; the rest is
  tagged by the model. Asking all 120 topics at once placed 13 of 40 at 2.5 times
  the tokens; section first, then topic, placed 21-23 with matching sections.
  A Choice with a single option is decided in code, never asked. A section a PDF
  printed the question under (question-bank.md "READING v2", 2026-10-03) is its
  section and is never asked; for a chapter's heading Jev's section must lie in it.

### Additions of 2026-10-04 (instructor: "Can Jev be further integrated into the follow up functionality to help?"; asked, they chose "Double-check automatic records" and "Close 'thanks, got it' replies")

- **FOLLOW-UP RECORD CHECK** (`assistant/followups.py` `_jev_confirms`, rule `followup-record-check-v1`; its dates are matched in
  code since 2026-10-08, JEV DATES and RECORD CHECK v2 below; since 2026-10-10 Jev again says whether each day is one the
  student misses, A DAY THEY MISS below). Before a follow-up
  preparation records an absence or an office-hours meeting by itself (ASSISTANT DONE FOR YOU), one request reads the
  conversation's last six messages with `relative_dates_resolved_by_code` (anchored to the latest student message) and the
  proposed record worked out in code: does the student report missing class on those days, does their reason fit, or did the
  student ask for or agree to that meeting time (a time only the instructor offered is not agreed). Every answer must be .8 or
  more; doubt, a failed or uncertain call, or Jev off or unconfigured makes the record an ordinary card, with the reason. Jev
  only holds a record back; it never makes one.
- **THANKS CLOSES** (`FollowupSuggestions.close_acknowledged`, on the Inbox worker's turn). A tracked follow-up Waiting on
  student whose new student reply has a complete triage that suggests no reply (strong acknowledgment or FYI, needs-action
  .02 or less, no wellbeing concern, full context) is marked resolved by itself: the instructor's decision of 2026-10-04 is
  the one exception to "Manual tracked states/reopening are never suppressed". It is a recorded automatic action of that
  follow-up's latest preparation (none: nothing happens), shown under Done for you with Undo (back to Waiting on student),
  once per reply, never again on a follow-up whose automatic status change the instructor undid. Nothing is sent.

### Additions of 2026-10-07 (instructor, of Fiona Weaver's and Yara Mercer's replies that each became a second follow-up: "a new reply by the student makes a completely new followup when it really belongs to the existing follow up"; asked, they chose the options below)

- **SAME MATTER** (`student_support/matching.py`, purpose `followup-match`, rule `followup-match-v1`, on the triage's worker
  turn before any conversation is analysed). A new conversation (no follow-up tracks it yet and the student wrote last), while
  the same student has follow-ups that started earlier (open, or resolved in the 14 days before it started; at most 8, newest first;
  never a regrade request or an FYI), is asked about once: one Choice over those follow-ups and "none of these", in both
  orders, reading the new conversation's last six messages and each follow-up's title, status, next step and last three
  messages. Both orders naming one follow-up at .8 or more joins the conversation to it, recorded as Jev's decision
  (`student_support_conversation_joins`, TEACHING-WORKFLOWS SAME MATTER) and listed under Done for you with Keep separate;
  both naming none at .6 or more, or a failed or uncertain call, records that nothing was found; otherwise a follow-up both
  orders give .3 or more is the instructor's one-click question (Join · Keep separate). A decision made meanwhile wins; a
  conversation the instructor started tracking meanwhile is asked about, never joined by itself. It is never asked again
  (Keep separate included). Jev only joins how the follow-up list reads the student's messages: nothing is sent, no
  follow-up record changes, and the receipt keeps no message text.
- **TO DO THANKS** (`FollowupSuggestions.close_acknowledged`, rule `todo-thanks-v1`, purpose `followup-thanks`; since 2026-10-09 `todo-thanks-v2` (the
  paragraph at the top) and then `todo-thanks-v3`: a clock-free state of catalog revisions, above). A thanks never
  reopens a Resolved follow-up and leaves a To do as To do (TEACHING-WORKFLOWS WAITING ON): the triage's `kind` must read the
  latest message strongly as acknowledgment or FYI with no wellbeing concern, while its needs-action answer and a context beyond
  six messages do not count (measured on live 2026-10-07: all six strong thanks readings scored needs-action .17 to .85, e.g.
  Fiona Weaver's To do .85 for the quizzes still to be booked, Jordan Zahn's resolved thread .31 with a drop-in planned, so the
  THANKS CLOSES bar kept none). Waiting on student, which a thanks changes to Resolved, keeps the THANKS CLOSES bar. On a To do the instructor answered after it was set, one yes/no question reads the follow-up's purpose and next
  step and the conversation's last six messages: does the instructor still owe the student another message (something
  promised, or only a later message can deliver; work done elsewhere such as grading is not a message)? .05 or less resolves
  it as THANKS CLOSES does (Done for you, Undo back to To do, once per reply, never after an undone status change); .9 or more
  keeps it To do; anything between, or a failed call, keeps it To do with the instructor's Finished? Mark resolved.
- **RELATIVE WEEKDAYS** (requirement, 2026-10-07: "fix the bug", after the Decisions sandbox showed the record check confirming
  an absence for the wrong week). `relative_dates` still never chooses a date for an ambiguous weekday, but its candidates now
  include the one just past: a bare weekday offers the latest on or before the sent day and the next after it ("I missed class
  on Monday" sent on a Friday was offered only the two coming Mondays); "last <day>" offers the two latest before the sent day;
  "this past <day>" is the latest before it; "this <day>" and "next <day>" are unchanged. A changed hint is a changed state, so
  triage reads again, once each, the current conversations whose latest student message names a weekday.

### Additions of 2026-10-08 (instructor, after the Decisions sandbox found half the record check's misses in its dates answer: "fix the asking models about dates issue"; asked, they chose "model judges tense only")

- **JEV DATES** (`jev/dates.py`). No check shows a model a calendar date to compare. Code reads the days and clock times each
  message names against its own campus send day (quoted replies set aside): written dates ("Oct 2", "10/2", ISO), today,
  tonight, this morning/afternoon/evening, tomorrow, yesterday, last night, "this <day>" when that day is the sent day or later
  in its Monday-to-Sunday week, and "this past <day>" are definite (sent before 5 am campus time, such a word keeps the day
  before as a second reading: a real notice written at 1:20 am said "this morning" of the day before); "this/next/last week"
  and "the rest of the week" name each of their days (for a record made by itself, only when the absence covers the week:
  WHOLE WEEK ONLY of 2026-10-10, below), and so does a span of days ("Oct 5-7", "October 5th through the 7th",
  "10/5-10/7", "5–7 October", "Sep 30 - Oct 2": the end after the start, in its month or the next, at most a month long); a
  bare weekday, or a "this <day>" already past in the sent week, has two candidates (the latest on or before the sent day,
  then the next after it); "last <day>" and "next <day>" keep their two candidates and are never chosen, and "next <day>"
  never offers the sent day itself. Weekdays are read written out or abbreviated as written, capitalized ("Mon", "Tue(s)",
  "Wed", "Thu(r)(s)", "Fri", "Sat", "Sun"); "Sat", "Sun" or "Wed" opening a sentence counts only before a date-like word
  ("Sun at 2?", not "Sat in the back"). A time without am/pm keeps both readings; a clock range takes the end's am/pm for its
  start unless that puts the start after the end ("11-1pm" starts at 11 am). The "this <day>", abbreviation, span, clock range
  and "next <day>" readings are of 2026-10-09 (instructor, on the autonomy review: "Fix all of the issues and implement your
  suggestions"; "this Monday" written on a weekend had named the Monday six days before, which SEND-CHECKS already held
  unclear). A record's day is named (a definite mention or
  a week holding it), a bare-weekday candidate, ambiguous, or missing. For a bare weekday a model answers only `timing` (a
  Choice in both orders: already past when the student wrote it, or the very day they wrote / a later day / unclear), and code
  picks the candidate when both orders agree at .6 or more (measured 2026-10-08 on the sandbox replay of real notices: both
  orders agreed on all 8, at .52 to .95, on the right side wherever the day is known; .9 held back true weekdays).
  `relative_dates` (triage's hints) and SEND-CHECKS' own reading are unchanged.
- **FOLLOW-UP RECORD CHECK v2** (rule `followup-record-check-v2`, replacing the v1 paragraph's date handling above). An
  absence's first and last day must each be named in the student's own messages among the last six; a missing or ambiguous day,
  or two weekdays that would need different answers, makes it a card saying which day ("the student's messages don't name Fri,
  Oct 2"; "“next Monday” in the student's message could be Mon, Oct 12 or Mon, Oct 19"). Jev answers whether the reason fits,
  plus `timing` when a bare weekday names the day: unsure, or a day other than the record's, makes it a card ("“Monday” in the
  student's message is Mon, Oct 5, not Mon, Sep 28"). A meeting's day and start time must both be written in the conversation,
  by either side (a bare weekday is its coming candidate, since a meeting is never in the past; "next <day>" with both
  candidates ahead makes a card); Jev answers only whether the student asked for or agreed to the meeting at the day and time
  quoted in those words, with their message numbers. The receipt holds no proposed day, so a re-proposal with other days in the
  same run reuses Jev's answer (since v3, A DAY THEY MISS below, only for days the student wrote in the same places). Every
  yes/no answer .8 or more, as before; `timing` as in JEV DATES.
- **ABSENCE CHECK v3** (rule `absence-source-v3`; v2 `absence-source-v2` until 2026-10-09). The drafted first and last dates
  are matched the same way against the email, whether or not Jev answers: a missing or ambiguous day is blanked with a note
  naming it; a bare weekday is kept only when `timing` picks it. Since v3 (2026-10-09, requirement, on the autonomy review:
  "Fix all of the issues and implement your suggestions") each distinct weekday mention has its own `timing` question
  (`timing_0`, `timing_1`, …) and each date is read with its own mention's answer (v2 asked only about the first and applied
  that answer to both, blanking a true "Wednesday" with a day Jev never named); with Jev unavailable, failed or uncertain a
  bare weekday's date is blanked with both candidates named ("Jev could not tell whether “Monday” in the email is Monday,
  October 5 or Monday, October 12."), and unavailable adds "Jev is not available to check this draft…". Jev answers whether the email supports the reason and the details and whether it contradicts itself;
  a conflict still blanks the dates and the reason. Measured on the same replay (Jev, real notices): the true first date kept
  in 4 of 5 (2 of 5 with v1), every week-shifted date still blanked (5 of 5), and a changed reason no longer blanks a true
  date (4 of 5 kept, 0 with v1). The record check's remaining doubts are Jev's reason answers, unchanged by this.

### Additions of 2026-10-08, triage (instructor: "implement triage with testing to confirm cutoffs"; asked, they chose "calibrate, then switch" and the "Middle" gates)

- **TRIAGE PROVIDER** (`jev/service.py` `provider_for`, `openai_decisions.py`). Student triage asks OpenAI Decisions
  (`https://api.openai.com/v1/decisions`, pinned `gpt-6-luna`, the configured `OPENAI_API_KEY`) when
  `FACULTY_TRIAGE_PROVIDER=decisions` (the `triage.conf` drop-in of both services); `jev`, or no setting, keeps Jev. Every other
  check, SAME MATTER included, asks Jev, and SAME MATTER and the recovery of interrupted calls run on the worker's turn whenever
  Jev is available, whatever the triage provider's state (2026-10-09). The conversation's last six messages go to OpenAI as they went to TypeSafe. Jev's
  questions are translated (a noul's meanings join its instructions; a score's level is labeled by its text before the colon)
  and the answers come back in Jev's shape, held to VALIDATION's bounds (a choice's confidence is Jev's formula over the
  returned probabilities); a different model, a refusal, an unknown option or an answer outside the bounds fails. Same receipts
  (`jev_calls`, the provider's model in the call key and `model`), no redirect or proxy, 15 s, no automatic resend, explicit
  retry. Switching re-reads each current conversation once (a new model is a new receipt), one per 15 s turn; ANALYSIS FIRST
  waits for it as for any read. Without the key or with `FACULTY_DECISIONS_ENABLED=0` triage is unavailable and the existing
  attention rule stands (a possible wellbeing concern already raised still shows: CARRIED). The status route adds `triage` only
  while it is not Jev, and `triage_paused` only during a host outage (HOST FAILURES).
- **GATES PER MODEL** (`student_support/triage.py` `GATES`). A receipt is read through the gates of the model that wrote it.
  Jev keeps .95/.98 strong, needs-action .02, wellbeing .30, urgency .6, chips .9. For `gpt-6-luna` the calibration
  (`manage_decisions_sandbox.py triage-calibrate`: all 408 student messages of the term as triage would have read them, both
  models; must stay visible = you replied next, an absence's notice, or a To do before the student wrote again, 239; may go
  FYI = resolved by you without a reply or never answered or tracked, 165; two labels corrected after reading them) gave, at the
  instructor's choice: strong .95/.98, needs-action .05, wellbeing .30, urgency .6, chips .9. None of the 239 reads as FYI or
  as a closing thanks; 46 of the 165 read as FYI (Jev at its own gates: 0) and 51 thanks keep a follow-up closed (Jev: 44).
  A strong reading of .8/.9 hid one thanks you answered. Wellbeing at .30 flags all 12 invented concerns and none of 12 ordinary
  sicknesses or worries (17 real messages flagged; Jev 24).

### Additions of 2026-10-09 (instructor, of the review of OpenAI Decisions: "Fix all of the issues and implement your suggestions")

- **HOST FAILURES** (`jev/client.py`, `openai_decisions.py`, `JevService.ask`, `StudentTriage.process_one`). A Decisions outage gave
  every arriving conversation a permanent failed receipt. A failure is the host's when it answers 401, 403, 404, 408, 409, 429 or
  5xx, refuses the connection or its name does not resolve (nothing was sent), times out or drops the connection, or answers
  under another model; other statuses (400, 413, 422) and unreadable or out-of-bounds answers stay the input's. The receipt keeps
  `host` (and `refused` when no answer exists: an error status or a connection never made) in its result, never host text.
  A refusal decided nothing, so it is not the conversation's analysis: triage's lookup passes over it and the worker asks that
  conversation again after the pause, as a separately identified call. A timeout, a dropped connection or a renamed model may
  have been decided: it stays the conversation's failed or uncertain receipt and is never resent. After any host failure the
  triage worker pauses for 5, 10, 20, 40, then 60 minutes per failure in a row, read from the receipts so every process agrees;
  the next ask is the probe, so an outage costs at most one receipt per pause. SAME MATTER waits out Jev's own outage the same
  way. Where triage status shows (the Follow-up page; `JevService.triage_status()` for the Automation tab) one line says
  "Student triage paused: OpenAI Decisions unavailable since 10:42 AM. Retrying every 5 minutes."
- **RETRY ALL FAILED** (`POST /api/jev/triage/retry-failed`, `StudentTriage.retry_failed`; `NEVER` for the assistant). The
  instructor's click asks again, one at a time, each current conversation whose triage receipt failed or is uncertain, at most
  25 per click, each a separately identified call under the click's request ID (the same request sent again asks nothing twice),
  and stops at the first host failure. An item's own Retry stays; a conversation not yet read offers Analyze now.
- **WELLBEING CARRIED** (`StudentTriage.apply`, `flagged`). A concern vanished whenever the current provider had no complete
  receipt of its own (a switch, the provider unavailable, a re-read pending or failed after other context changed). The newest
  complete triage receipt under the current rule, from any model, that flagged a concern at its own model's gates and was made
  after the student's newest message arrived (REVIEWED HOLDS' `seen > arrived` test) keeps the concern raised, pinned, counted
  and never FYI or a closing thanks, even when a later reading of the same messages is calmer, until Mark reviewed (on that
  receipt), resolving the follow-up, or a newer student message.
- **READ BEFORE** (`StudentTriage.apply`, `readings`; requirement, 2026-10-16, of Rina Akhtar's "okay thank you!" back as "A
  newer student message reopened this follow-up" while its fourth re-read after a due-date change waited: "these "thank you"
  replies aren't supposed to require followups"). A re-read is owed whenever other context of a conversation changes; until it
  is made (no receipt yet, or one running), the newest complete triage receipt by the current triage model under the current
  rule and questions, made after the student's newest message arrived (REVIEWED HOLDS' `seen > arrived` test), stands for the
  conversation: a thanks stays a thanks and FYI stays FYI. The re-read decides once it is made; a new student message is read
  before anything is carried, and a provider switch still waits for the new provider's own reading.

### Additions of 2026-10-10, record check (instructor, shown an absence recorded by itself on the day a student only asked to meet; asked, they chose "Ask Jev about the day")

- **A DAY THEY MISS** (FOLLOW-UP RECORD CHECK v3, rule `followup-record-check-v3`, `_jev_confirms`). v2 asked only that the
  student's messages NAME an absence's first and last day, and Jev answered only whether the reason fits: v1's "does the
  student report missing class on those days" had gone with its dates answer. On a synthetic catalog, "I was sick yesterday
  and missed class. Could I meet you October 7 at 2pm?" (sent Oct 3) had an absence on Oct 7 recorded by itself; so did the
  day a homework was due, the day the student said they would be back, and an absence stretched from the day missed to the day
  of the meeting. A day the student wrote for something else is not a day of the absence. Each of the absence's two days, still
  matched in code (JEV DATES), is now also put to Jev in the student's own words with their message numbers: `missed` ("The
  student writes "yesterday" (message 1) and "October 2" (message 1). In any of those places, does the student say they missed,
  or will miss, class then? A day written only for something else (a meeting they ask for, a due date, the day they will be
  back in class) is not a day they miss."), and `missed_last` when the last day is written in other places; a week or a span
  of days that names both is asked about once. The places are every definite mention, week or span that names the day (the
  four latest, `MISSED_PLACES`), or the bare weekday code matched (beside its `timing`). .8 or more, as every yes/no answer
  here; short of it the record is a card naming the day ("Jev could not confirm that the student's messages give Wed, Oct 7 as
  a day they miss class"). Jev is still
  given no calendar date to compare and none from the record: the words quoted are the student's, already in the messages it
  reads. The questions hold those words, so a re-proposal for a day written elsewhere is a new request. Unchanged: the days
  between the first and the last are not checked, a week names each of its days, the meeting's question, and ABSENCE CHECK v3
  (a draft the instructor saves themselves). The Decisions sandbox's record-check replay reports `missed` and `missed_last`.
  MEASURED the same day (instructor: "run the replay of real notices in the Decisions sandbox"; run
  `record-check-v3-2026-10-10`, Jev alone): the 36 absences follow-up preparations had proposed since 2026-10-03 (23 distinct
  absences of 20 students, each day a true day of absence wherever it was read) and, for the other side, days those notices
  and invented messages name for something else. v3's question answered .87 to .96 on 16 of its 19 distinct requests and
  .47, .75 and .79 on three true days ("I missed the quiz yesterday", "Will it be okay to excuse my absence for the class
  today?", and a Friday named only through "this week"), holding back 1 of the 18 proposals v2 recorded by itself. v4 (rule
  `followup-record-check-v4`) adds to the question "Missing a quiz, an exam or any other class meeting that day counts, and so
  does asking to be excused for it.": every true day .84 or more, all 18 recorded as under v2, and every day written for
  something else in a date or a relative day .06 to .47 (real: the day of an injury, a day the student says they will attend,
  the date in a quoted reply's header, an absence stretched back to the injury; invented: a meeting, a due date, the day
  back, a quiz asked about, an absence stretched to the meeting). So .8 stands. KNOWN GAP, not closed by this question: a week
  names each of its days, so Jev cannot tell them apart. A real notice from a student who missed class the day before and
  asked about "a quiz or anything this week" had another day of that week confirmed at .90. Closed in code the same day:
  WHOLE WEEK ONLY, next.
- **WHOLE WEEK ONLY** (FOLLOW-UP RECORD CHECK v5, rule `followup-record-check-v5`, `jev/dates.py` `whole_weeks`; instructor,
  2026-10-10, shown that gap and what each choice does with three messages; asked, they chose "Whole week only"). For a record
  made by itself, a week the student wrote ("this week", "next week", "last week", "the rest of the week") stands for an
  absence's days only when the absence covers every class day, Monday to Friday, that the week names; a week with no class
  day left names nothing. A week the absence covers only part of is set aside before the days are matched, so a day inside it
  has to be written by itself (a date, a span of days, a weekday, "tomorrow"); else the record is a card before Jev is asked
  anything: "the student's messages name Fri, Oct 9 only through “this week”, and this absence does not cover that week".
  Costs the instructor was shown: "I have been sick this week" written on a Thursday for Monday to Thursday is a card. Class
  days are Monday to Friday whatever days the section meets, so a Tuesday-to-Thursday absence named only by a week is a card
  too. Replayed on the same run: all 18 proposals recorded by itself before are still recorded; the day confirmed at .90 is a
  card with no request; the Friday behind "this week's material" is read as the bare weekday it is (`missed` .89; its
  `timing` "later" in both orders at .46, under the .6 gate, so still a card, as its reason answer made it before). `match`,
  `naming`, triage's hints and ABSENCE CHECK v3 are unchanged: a draft the instructor saves still reads a week as each of
  its days.
- **EVERY CLASS DAY INSIDE** (FOLLOW-UP RECORD CHECK v6, rule `followup-record-check-v6`, `assistant/followups.py`
  `_class_days_inside`; requirement, 2026-10-10: "check the days between the first and last"). Until v6 only an absence's
  first and last day were checked, so "I missed October 2 and will miss October 9" could be recorded as one absence from the
  2nd to the 9th. Each class day of the absence's section strictly between its first and last day is now checked as those
  two are: it must be written in the student's messages (a date, a span of days or a covered week that holds it, a weekday
  on the same side), else the record is a card before Jev is asked ("the student's messages don't name Mon, Oct 5, a class
  day inside this absence"); and where it is written in other places than a day already asked about, Jev answers the same
  question for it (`missed_inside_<n>`, .8 or more). The class days are the section's own meetings on the Term schedule its
  term uses (SCHEDULING "TERM SCHEDULE": its weekdays inside the term's dates, days off left out); while the term uses none,
  or its schedule has no class for the section, every Monday to Friday counts, so a schedule not set up never lets a day
  through unchecked. One check asks at most five `missed` questions: an absence written in more separate places is a card.
  Cost: a stretch written only by its ends ("I was out Monday through Friday", "sick since Monday") is a card when a class
  day lies between them. On the real notices (a fresh copy, run `days-between-2026-10-10`; all four sections meet Monday,
  Wednesday and Friday): 9 of the 36 proposals span more than one day and one has class days inside it, both written. Replayed,
  every proposal recorded by itself before is still recorded and none needed an inside question, so that question, worded as
  the measured one, has no measurement of its own. The same requests asked again moved an answer by up to .06 (a reason
  answer .78, then .80, which recorded one more approved absence). WHOLE WEEK ONLY still counts Monday to Friday whatever
  days the section meets.
- **DRAFTS LEFT AS THEY ARE** (ABSENCE CHECK v3; requirement, 2026-10-10: "test the Absences page's draft check for the same
  gap"; shown the result and asked, they chose "Leave it"). The draft check keeps the gap the record check closed today: a
  drafted day the email names for something else (a meeting, a due date, the day back), a day inside a week the draft does
  not cover, and two separate days drafted as one stretch are each kept with no warning. Tested on eight invented emails
  with a scripted Jev and with real Jev alike: Jev is sent the reason and the details, never anything about the days. Left
  because the instructor reads the pre-filled form before saving, and the live catalog held no `absence-check` receipt.
  Closing it is a new decision.

### Additions of 2026-10-10 (instructor, after the memory sandbox: "jev alone"; "readier")

- **MEMORY** (`assistant/memory.py`, `assistant/memory_decisions.py`; ASSISTANT-MEMORY). Six purposes, one request each
  (the sixth, `assistant-memory-edit`, rule `assistant-memory-edit-v1`, added 2026-10-12 on the instructor's "when I edit the
  recommended suggestions of the followup agent, it compares the changes to what was there and makes some sort of memory
  from it": of a rule the Assistant's model wrote from one correction they made of a follow-up's card and approved: is it
  shown by the correction itself, does it add, widen, record a fact, make a habit of one occasion, speak of the one
  piece of work, make a rule for grading, repeat what the draft said, lean on the follow-up, or name or point at a
  person, and, of a changed decision
  alone, does the student's message plainly give the kind of absence the rule is about; gates
  `memory_decisions.EDIT_GATES`, fitted on invented corrections and held to a half they were not fitted to. Its state holds the correction (`change`: the Assistant's draft
  against what the instructor approved, with names, contacts and every number of the draft taken out and only names and
  nine-digit numbers out of the instructor's own sentences; or the decision suggested and the one chosen; or what they
  asked for and the cards before and after), `open_item` as for a rule, and the rule. Never a grade, a date, a recipient or
  a private note. Owner: the correction's case (`student_support_learning_cases.id`). The capture request then judges the
  rule as words written to be kept, and the consolidate request compares it with the kept memories: ASSISTANT-MEMORY
  "LEARNED FROM AN EDIT")
  (the fifth, `assistant-memory-rule`, rule `assistant-memory-rule-v2` (v1 for an hour: v2 says what `adds` and `wider`
  mean when the instructor's words name no kind of case), added 2026-10-10 on the instructor's "Rule only,
  rewritten": of a rule the Assistant's model wrote, without the student, from a general statement typed about one
  student's case: does it add, widen, drop, speak of another kind of case, say something else, lean on the conversation,
  name or pick out a person, or make a rule of what was true of one student; gates `memory_decisions.RULE_GATES`, fitted on
  invented messages and held to a set they were not fitted to. Its state is the one memory state that holds something the
  instructor did not type: `open_item`, the two latest messages the student wrote in the follow-up on screen, cut short,
  with every current student's name, any nine-digit number and (since 2026-11-09) every address taken out; ASSISTANT-MEMORY "A RULE SAID OF ONE STUDENT'S
  CASE"). The capture rule is `assistant-memory-capture-v2` since that day: it also asks whether what is lasting is about
  the one student the message names (`about_student`), and a statement is refused for a student only when it is.
  (the fourth, `assistant-memory-rewrite`, rule `assistant-memory-rewrite-v1`, added 2026-10-10 on the instructor's "do 1 and
  2": does a rewording of a kept fragment add, widen, drop, speak of another thing, say something else, still lean on the
  conversation, or name a person; its state is the message, the instructor's two messages before it and the rewording; its
  gates are `memory_decisions.REWRITE_GATES`, fitted on invented fragments and held to two sets they were not fitted to;
  from 2026-10-10 the consolidate request's `standalone` under `REWORD_UNDER` .7 sends a kept chat statement to be reworded,
  and a fragment the writer declines is judged by the rule's check, told the follow-up on screen: ASSISTANT-MEMORY "THE
  REWRITE STEP").
  The capture request is also asked, with channel `form` or `followup`, of words the instructor writes to be kept
  (ASSISTANT-MEMORY "EVERY WAY IN": only its `names_student` and record questions are read there; for a follow-up's
  "Remember this", `about_student` too, and then the consolidate request with no kept memory, for `standalone` alone, and
  the rule request when a rule is written: ASSISTANT-MEMORY "A FOLLOW-UP'S REMEMBER THIS", owner `memory-followup`). The first three:
  `assistant-memory-capture` (is the instructor's chat message a lasting statement, their
  own position, naming a student, about that student, a record-like fact; its kind and scope, both asked in both orders),
  `assistant-memory-consolidate` (`-v1`: against at most five kept memories, the same, a change, or an exception; faithful;
  standalone) and `assistant-memory-relevance` (`-v2` since 2026-11-09, ASSISTANT-MEMORY "WHAT A REQUEST IS TOLD": which of at most eight kept
  memories a request needs, read by the instructor's two messages before it, `earlier_instructor_messages`; one request for
  every eight of the memories in scope, at most eight requests, all asked at once; `conflicts` gate .9). Their states
  hold only what the instructor typed and memories made from it: never mail, a scan, a tool result, a private note or an
  identifier (the rule request's `open_item` above is the one exception). A second exception since 2026-10-11 (the instructor: "allow jev for follow ups"; ASSISTANT-MEMORY "A FOLLOW-UP ASKS WITH THE STUDENT'S LAST MESSAGE"): the relevance request of a follow-up's preparation holds, as its `request`, the preparation's task in one sentence and the latest message the student wrote in that follow-up, cut to 1,500 characters with the roster's names and UINs taken out; never the preparation's instruction, a record fact or a private note. Its `conflicts` answers set no memory aside: only the instructor's own words do. A roster name is in a state only as a word the instructor typed. They stay on Jev whatever provider triage
  uses (`provider_for`): Decisions misread a list of kept memories by index in the sandbox (19 of 85 new statements called
  duplicates) and gave 59 wrong memories of 162 where Jev gave 3 of 93. Gates are `memory_decisions.GATES`, measured for
  `jev-1.13.0`; a kind the two orders disagree on is a label, never a reason to drop a statement.
- **JEV PICTURES** (requirement, 2026-10-15: "you have my ok to build it as you suggest. dont use it for answer areas"). A check
  over scanned pictures (`client.evaluate(..., images)`) asks `jev-1.14.0` with TypeSafe's opt-in header
  `TypeSafe-Danger-Zone-Images: true`, each picture once in Jev's `assets` map under a fresh reference the state names; pictures
  are made grayscale PNG within Jev's size limits, a short side padded to 280 pixels, never stretched (`prepare_image`). It is
  the one place pictures of student handwriting leave the server, and the instructor approved it for this: whose paper a scanned
  first page is and which of the course's assignments it is from (DOCUMENTS "JEV PAPERS", "JEV MIXED SCANS"), whose paper a stamped
  copy printed for no student is (QUIZZES "NAMES ON UNNAMED STAMPED COPIES"), and whether an answer the reader found empty was left blank (GRADING "JEV BLANK
  ANSWERS", 2026-10-15: "build it with that rule"). Never for answer areas (measured the same day: Jev's question boxes hug the printed
  text and stop short of the next question), or grades beyond those two. Box answers are validated
  (four edges in 0..1, left < right, top < bottom, a confidence). Receipts keep the pictures' sha256, never the pictures; a roster
  check keeps enrollment IDs and option counts with a hash of the options, never the roster names or UINs it showed
  (`JevService.ask(..., images, receipt)`). TypeSafe says images and Box are not production-ready and may change between
  releases, so a picture check's failure, refusal or changed answer shape decides nothing: the local OCR reading stands.
- **STUDENT MATCH PROVIDER** (requirement, 2026-11-12: "replace jev with gemini decisions for scan to students", after the Decisions
  sandbox of 2026-11-11 found Google's Gemini Decision 1 naming 78 of 78 scans where Jev 1.14 named 62; `jev/service.py`
  `provider_for`, `gemini_decisions.py`). Whose paper a first page is (`document_identity.jev_papers.identify`, purpose
  `paper-student`: the name against each section's roster, then the name and the UIN among the candidates) asks Gemini Decisions:
  Google's own Decisions API (`https://generativelanguage.googleapis.com/v1beta/decisions`, pinned `gemini-decision-1`, the
  configured `GEMINI_API_KEY` as `x-goog-api-key`), no redirect or proxy, 30 s, no automatic resend. `FACULTY_STUDENT_MATCH_PROVIDER=jev`,
  `FACULTY_GEMINI_DECISIONS_ENABLED=0` or no key keeps Jev 1.14 as before, so the match never stops for a missing key. Everything
  else about pictures stays Jev's: the page check (first page, which way up, the name and UIN boxes: only Jev answers a Box), the
  page's assignment, an exam's version, a blank answer. The handwritten name and UIN crops and the roster names and UINs offered as
  options go to Google as they went to TypeSafe, and the instructor approved that for this. Jev's questions are translated (a
  yes/no question's meanings become `true_description` and `false_description`; an option with no description takes ""; each
  question travels under a plain key, since Google's keys hold letters, digits, "_" and "-" only) and the answers come back in Jev's
  shape, held to VALIDATION's bounds (a choice's confidence is Jev's formula over the returned probabilities, not Google's own
  value, the top probability). Google's answer names no model, so the pinned one is the one asked. Same receipts (`jev_calls`, the
  provider's model in the call key and `model`; enrollment IDs and option counts, never the names or UINs shown). The questions,
  crops, rounds and rule `jev-papers-v1` are unchanged, so nothing already read is read again: a reading kept before the switch
  stands, and only a page read after it asks Gemini. A Gemini failure, refusal or uncertain call decides nothing and Jev is not
  asked in its place: the OCR reading stands, as under JEV PICTURES. A match Gemini gave says so (`reader`), and what the
  instructor reads names it ("Identified by Gemini from the handwritten name and UIN."). `GEMINI_API_KEY` may be named only by a
  connection to Google's own API host (`grading/config.py` `KEY_HOSTS`), and the Antigravity connection never passes it on.
  Replayed 2026-11-12 through `identify` itself on 119 first pages the app had read (Decisions sandbox, `students`): Gemini named
  118, none wrong (90 checked against the registered student or an exact OCR UIN, 110 the students Jev had named, 3 by eye), and
  left 1 unsettled that Jev had left too; it settled 8 of the 9 Jev had failed or left unsettled. With a page's own student taken
  off the roster it named nobody on 30 of 30. A round's two option orders agreed on 118 of 119 (Google sorts a choice's options
  before it answers), so the order check holds back little for this model; what holds a wrong student back is the name and UIN
  rounds answering "none" and the holds of DOCUMENTS "JEV PAPERS".
- **RUBRIC MEMORY FIT** (requirement, 2026-10-28: "can't we use a jev call when recalling rubric memories to ask if it contradicts
  clarifications that I've provided or explicit contradictions from the rubric itself?", then "proceed"; GRADING "RUBRIC MEMORIES"
  FITTED TO EACH QUESTION and NEVER WRITTEN AGAIN). Purpose `rubric-memory-fit` (rule `rubric-memory-fit-v1`, owned by the rubric
  check or quiz revision that asks): one question of a rubric with the instructor's clarifications and word on it, and up to eight
  standing memories, two `noul` questions each; .7 on either leaves the memory out of that question's requests. Purpose
  `rubric-memory-dropped` (rule `rubric-memory-dropped-v1`, owned by the learning pass): one memory a pass wrote and the course's
  dropped memories; .7 that it says what one of them says leaves it unwritten. Neither changes a rubric, clarification,
  confirmation or grade; they only decide which words a model is shown. Text only (`jev-1.13.0`); no student is in either state
  (the pass's evidence is already nameless).
- **CHECKS.** Synthetic catalogs and fake Jev only, through manage_checks.py on
  NVMe scratch. Exercise acknowledgments with embedded requests, context changes,
  cutoff, manual overrides, malformed replies, redirects, uncertain calls, no
  repeated sends, date/reason conflicts, unsupported claims and cancellation.
