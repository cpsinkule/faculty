# Teaching follow-through

2026-10-18 ONE LINK PER PLACE (instructor: "make all of your suggested changes", pipeline review item 10). On Return's Grade
delivery checklist the three Canvas lines (Recorded locally, Sent to Canvas, Posted in Canvas) carry one link, on Recorded
locally: "Open gradebook to sync, post or hide" (or "Choose a gradebook column" without a column). "Review sending grades"
and "Review publication" open Return's own **Gradescope details** fold in place (the tab stays Return, the address does not
change); `#grade-delivery/<plan>?open=gradescope` opens that fold once per address. The fold's "Send or publish on
Gradescope" link still opens Certify (`?tab=export&open=gradescope`). Teaching review has no sidebar entry since that day: the
unscoped page opens from the Assignments index strip and the palette (FRONTEND).

2026-10-17 ASSIGNMENT RETURN (instructor, assignment review item 7): the six independent delivery facts stay visible;
repeated certification, Canvas and Gradescope evidence is folded in named details, preserving explicit comparison, Send
and Publish controls. A combined Canvas posting state shows the oldest-to-newest read times when they differ, never
labels old facts with only the newest time. Each column's details show its own posting state and read time. Uses the
existing projection only; no additional Canvas reads or writes. Scoped Teaching review stays in the assignment shell
(ASSIGNMENT-WORKSPACE); the unscoped page remains its own page (no sidebar entry since 2026-10-18, above).

2026-10-12 REMEMBERED FROM YOUR CORRECTIONS (the instructor: "when I edit the recommended suggestions of the followup agent,
it compares the changes to what was there and makes some sort of memory from it"; asked, all three kinds of correction teach,
"Kept at once, with Undo", and the lesson review of 2026-10-08 is replaced: "Replace them"). A reply the instructor edits on
its card or sends from Mail in a draft's place (a regrade's reply included), a Decision they change on an absence card, and a
round prepared again at their request (Ask for changes, the Assistant chat, an "Instead" choice) are each one correction once
what they approved has a confirmed outcome. Faculty compares the Assistant's own card with what they approved and, when the
change shows how a kind of case is handled, keeps that as a memory (ASSISTANT-MEMORY "LEARNED FROM AN EDIT"). On this page:
a toast says it once, **Remembered from your edit: “…”** ("from your decision", "from the change you asked for") with Undo,
on the page's next read (it reads again within seconds while a correction is being considered, `suggestion_settings.remembering`);
and the follow-up keeps one line under its steps, **Remembered for later drafts**, with Undo and Change, which reads "Taken
back" once undone (`suggestions.learned`, only on the `suggestions=1` read). A correction that teaches nothing says nothing.
Term policies has no Learn from feedback tab and a draft no "Shaped by" line; the policy editor's "Guidance from your edits"
proposals (repeated wording, ASSISTANT "LEARN FROM EDITS") are as they were. Migration `student_support/016_feedback_learning`'s
tables stay: its cases are the queue of corrections.

2026-10-09 FOLLOW-THROUGH FIXES (the instructor, of the autonomy review: "Fix all of the issues and implement your
suggestions"). (Its lesson rules went with the lesson review on 2026-10-12, above.) The evidence check and the tool-use
report are both kept, so the EVIDENCE WARNING fires again.
EDIT IN PLACE waits while a preparation runs: no Edit is offered and the server refuses one, since the draft check at the end
of the turn would withdraw it; the check never withdraws the instructor's own version. BEFORE YOU SEND: a draft-check
warning left after the one repair is a "Before you send" callout on the waiting mail card it concerns and leaves when that card
is edited; an unavailable or partial check stays a muted run-level note. FYI: the capture note says "Thanks and
information-only messages Jev reads confidently appear under FYI." with a "Filed as FYI · N" link to that filter.

2026-10-09 assignment flow: delivery adds the source assignment ID to its read projection so a missing gradebook column links to the exact term/assignment. Return displays certification, local recording, Canvas receipts, Gradescope receipts and publication as independent facts with retained receipt dates and existing owner actions. Accepted historical receipts are identified as receipts, not a claim that every current grade is delivered. Sending and publishing stay explicit.

EACH CANVAS SITUATION BY ITSELF (requirement, 2026-10-11: "build the stalled line and gaps 1 and 3"): Return's "Sent to Canvas" line gives each situation its own count, in this order and only where there is any: accepted, never sent (a recorded cell with no send receipt), changed since sent (the local cell differs from the accepted send), unconfirmed (sent with no answer yet; a later Sync's read confirms it) and failed; with nothing at all it reads "Nothing sent". It said "N missing, changed or unconfirmed" in one number, and each calls for something different. `canvasReceipts` (teaching-review.js) is the one reading of the delivery read's columns; the per-column cards and the read itself are unchanged.

POSTED IN CANVAS (requirement, 2026-10-11: "build the Canvas posted/hidden line"): Return's checklist has a line of its own, after "Sent to Canvas", for whether students see the grades there: "Students see all N grades", "H of N grades hidden from students", "All N grades are hidden from students", "No grades in Canvas", each with the time it was read, or "Not read from Canvas yet"; a column not read yet is said beside the others. It is what the gradebook last read of each column's posting (`canvas_posting.marks`, the grid's own "hidden" mark: current students only, the latest read of every Canvas assignment of the column), which the delivery read now carries as `columns[].posting` (None before any read): catalog only, one query, and Canvas is never asked from Return. A read, never a claim about now; what the next sync will post or hide stays the grid's chip, and Post and Hide stay the column's menu, which the line links.

2026-10-08: the instructor requested the six assistant-review improvements. ASSISTANT's **Completion and follow-up
improvements** governs prerequisite-first execution, retained attention for failures, student-addressed reply receipts,
shared context, explicit Watch until done, and reviewable term guidance. The one-round rule still governs each preparation;
an explicitly watched event or check-back is a new trigger, not a continuation from every receipt. Existing default list
responses remain unchanged; additional review data is opt-in.

Rule ID: **TEACHING-WORKFLOWS**. The instructor requested the six additions from the
repository review: student follow-up, student search, teaching insights, grade
delivery status, assessment blueprints, and office-hours tracking. Grade what-if
calculations and next-term setup are outside this change.

- **ACTION DESK** (2026-10-11, requirement: "implement the action desk design and your suggested functionalities and changes").
  The Follow-up page leads with the next action and its prerequisites, then the selected conversation beside its action
  cards and reply editor. Its compact needs-you line replaces the large paragraph; completed work is folded with its
  receipts and Undo, while failures, uncertain outcomes, changed sources and wellbeing concerns remain visible. An
  Assistant review being complete is labelled separately from the student's work being finished. Queue titles derive from
  the next task or proposed action; the original subject stays in the conversation. Action desk and Tasks are views of
  the same follow-up records, sharing drafts and disclosures, never new owners. Tasks lists open work across the filtered
  follow-ups and opens its selected task in Action desk. The Conversation layout was removed at the instructor's request
  (2026-10-10); messages remain in the Action desk's conversation card. Action desk, Tasks and Office hours share one
  navigation row in that order on every view, including loading and read failures, with the current view highlighted.
  Office hours opens either follow-up view directly; there is no separate Follow-up button.
  Selection never moves on a background refresh. Existing Send, approvals, revision checks and central shortcuts stay.
  Write to student… is in the row under the conversation on EVERY follow-up, whatever it waits on (2026-10-11: the desk's
  own button only leads to a step or to the conversation, and a follow-up waiting on the instructor with nothing suggested
  had been left with no way to write).
  An older compound next-step note offers Prepare separate tasks: an explicit request through the existing redo route,
  preparing only a task-tracking proposal for review. It saves no commitments and performs no promised work itself.
  ONE CLICK ON A TO DO (2026-10-13, requirement, of a To do naming work their approval had already done; asked, they chose
  the buttons as well as ASSISTANT "ASK AFTER A CHANGED DECISION"): a tracked To do with nothing waiting for review, nothing
  being approved and no open task has Done (Resolved) and "Now waits on <first name>" (Waiting on student) in its decision
  box, in place of the button that only led to the task list (`todoSettles`). Both are the instructor's own saves through the
  follow-up's existing revision-checked route. Now waits on does not carry the To do's next step over: it was the
  instructor's own task; the row reads "A reply to your last message" until Edit follow-up… says more. A To do split into
  tasks keeps Mark completed on each (SEPARATE COMMITMENTS: an open task still prevents resolving).
- **SEPARATE COMMITMENTS.** Each follow-up may keep `commitments` in its existing immutable content revisions: ownership
  is its student's enrollment, through `student_support_items.id`, never a course or a second message store. Each has a
  stable id, title, kind (work, reply, check_back), who it waits on, optional recorded check-back date, dependencies and
  references to its source message or one of that follow-up's action receipts. No message body is copied. Omitted
  commitments survive an ordinary follow-up edit; removing one explicitly cancels it with a note and retains its history.
  A manual completion of work performed outside Faculty requires a note. A linked action completes only with its confirmed
  receipt; a failed, declined, expired or uncertain action is never completion. An unfinished prerequisite prevents
  completing its dependent task. Complete these first offers neither the task itself nor a task that already waits on
  it, directly or through others (prerequisites never form a cycle; 2026-10-24). Only an explicit `blocks_reply` commitment holds a reply; the server checks it before
  sending. An open commitment prevents resolving the whole follow-up, and open work owed by the instructor or a due
  check-back remains in attention, including after the reply is sent. Existing action cards and watches are projected as
  separate steps too, without copying them into commitments. The overview counts tasks and conversations separately.
  Dates are instructor-recorded check backs, never inferred deadlines. A task's Watch control creates an existing,
  explicitly reviewed Watch until done with its stable commitment id in the enrollment-owned `target_json`. Its normal
  one-shot check ends quietly with a receipt if the linked task has finished or was cancelled, without preparing another
  review. Setting a date alone does not send or prepare anything. Assistant changes to commitments go
  through the ordinary follow-up revision and require approval: this grants no automatic tier. Legacy records and default
  responses without commitments keep their shapes.
  HEARD IN A RECORDED MEETING (2026-11-13, RECORDINGS "Student meetings", step 4; instructor: "build 4"). A commitment may
  name the recording item it was heard in, `recording_item_id`: a third reference beside the source message and the action
  receipt, kept ONLY where it is given (every other commitment's saved content is what it was), and checked when it is
  first saved: the item must be of THIS student's own meeting, its words in their own part of the recording
  (`recordings.meetings.source`); never another student's part or another recording; and never twice: not two commitments of
  one follow-up for the same item, and not on a second follow-up of the student while another holds it. Read back, such a commitment carries
  `heard` (a view: where to hear it), and the Action desk's task line links it, "Said in your meeting". Promises heard in a
  student's recorded meeting are proposed as ONE Approve card (engine `recordings`): on the student's open follow-up, the
  one changed last when several are open, with every commitment it holds resent and the new ones after them, at the
  revision it was read at (changed meanwhile, the Approve is refused); with none open, a new follow-up "Meeting on <day>"
  with no conversation, state To do when the instructor owes something and Waiting when only the student does. A day that
  was said is a check back recorded by the instructor's Approve. Nothing is tracked until Approve; a resolved follow-up is
  never opened again by a recording.
  RESOLVED WHEN OPENED (2026-11-05, requirement: "the followup page seems to take a while to load, is that just on the first
  load after a restart ?"; measured, it was every load: 1.7 s and 4.3 MB a read, 3.9 MB of it the drafts and evidence of 217 resolved
  follow-ups, again at every 30-second refresh). The page's list read adds `brief=1`: a resolved follow-up whose suggestions are
  settled (no card waiting, under way or needing a look, and no question) comes marked `suggestions.brief`, with what its queue
  row, the filters and search read (each card's label and whether it finished, each Done for you line's state) and without its
  drafts, evidence and verification. Anything else on a resolved follow-up comes in full, so it stays in Active and Needs me.
  Opening one reads the student's follow-ups (`enrollment_id`, the ordinary read) and the review says "Reading the Assistant's
  work here…" until they come; the page keeps up to 20 of them while the list says the same of each, and reads again after the
  instructor's own change. The default `suggestions=1` read, which the Assistant's Changes tab uses, is unchanged.
  RESOLVED WITH ITS TASKS (2026-10-22, requirement, of Mina Takeda's and Juliet Dane's Exam 2 makeup follow-ups, booked
  and emailed, whose chat card "Close completed makeup follow-ups" stopped at "finish or explicitly cancel the open commitments
  before resolving this follow-up"): the Assistant's `track_followup` resolving a follow-up with open tasks completes each in the
  same revision, its `done` (what finished them) as the completion note, so approving the card is the instructor's completion.
  Without `done` the tool refuses and names the open tasks; a task linked to an action still needs that action's confirmed receipt.
  Joining conversations keeps each original follow-up's tasks visible by reference (`related_commitments`); Keep separate
  restores that record's editor. An explicit Start from now cancels the cleared records' open tasks in the same revision,
  with a note, rather than leaving tasks that would reopen the cleared follow-ups.

- **OWNERS.** Follow-ups and office-hours appointments belong to a student's term
  enrollment, with immutable revisions, exact request IDs and optimistic revision
  checks. A follow-up with the course coordinator belongs to the course term instead (A COORDINATOR'S FOLLOW-UP below). They reference communications, never copy message bodies. They stay out
  of generic exports. Decisions that one of a student's conversations is part of another (SAME MATTER,
  `student_support_conversation_joins`) belong to the same enrollment. A blueprint belongs to its course-wide quiz draft, retained in the draft’s
  immutable input (no independent blueprint table); it pins question-bank revisions. Insights and delivery status are read projections, not
  new grade or publication owners. Every file and route below follows these owners.
- **FOLLOW-UP.** Current student conversations whose latest author is that student
  are suggestions, derived from captured Gmail, Canvas and Gradescope regrade records (REGRADES "FOLLOW-UP", 2026-10-07: a
  regrade request's conversation is one question submission's requests and replies; Gradescope's staff reply counts as the
  instructor's, and the thread is answered once its latest request is answered or handled). Explicit tracking
  supports needs-reply, to-do, waiting, snoozed-until and resolved (WAITING ON). A newer student message
  in the exact linked conversation reopens a tracked item in the view, until the instructor has written after it (WAITING ON
  "ANSWERED", 2026-10-13). Subject or
  name similarity never joins conversations (SAME MATTER joins them only by an exact thread, a decision, or Jev reading the
  content). Canvas notifications naming the same
  conversation share its key. EMAIL REPLY JOINS (2026-10-06, requirement, of a reply Faculty emailed to a Canvas message, since
  the conversation was a class announcement, that left the follow-up looking unanswered; they chose to link it): a Gmail thread
  whose captured Canvas notices all name one conversation is that conversation, so its other emails (the instructor's reply,
  the student's answer to it) join it and the reply shows as Your reply. The exact Gmail thread links them; a thread whose
  notices name two conversations, or none, joins nothing. Records keep the key they were saved with: one saved under such a
  thread's `gmail:` key reads the Canvas conversation (`student_support.service.canvas_threads`). Gradescope regrade requests are
  Follow-up conversations (requirement, 2026-10-07: "I want regrade requests to also go through follow-up"; they chose Follow-up as
  the only place that asks for attention): Home's Needs reply counts them, Return › Regrade requests lists them per assignment.
  No mailbox is marked read or changed. Home shows only counts and links. Capture
  freshness is shown; a missing capture is never described as no unanswered mail.
  MAIL'S STUDENTS LIST (2026-10-10, MAIL "MAIL-FOLLOW-UP", the instructor: "when an email from a student is related to a follow
  up, can we have it cleared from the inbox the same as those emails that have a reply"): `settled_mail` tells the Mail page
  which of the student messages that end its Students threads are part of a tracked follow-up showing anything but Needs my
  reply; Mail leaves those threads out. It is this module's own view of each such student (`_items` for the one enrollment,
  the conversation analyses read for that enrollment alone), so a thanks that leaves a follow-up Resolved is settled and a
  message that reopens one is not. A read: nothing is saved, and every other read is unchanged.
  START FROM NOW (2026-10-03): at the instructor's explicit request, a workspace-owned
  `student_support_baselines` receipt records a cutoff instant. The previewed reset
  resolves current-term tracked follow-ups through that instant and suppresses older
  automatic suggestions, including messages captured later with earlier sent times.
  Newer student messages and newly created manual follow-ups still appear. The reset
  never alters communications, appointments, notes, grades or outside services.
  Its own routes retain exact request IDs, a checked preview fingerprint, the lifecycle
  lock and one transaction; the assistant's reset remains an Approve action.
  JEV (2026-10-03, requirement's requested additions 1 and 2): a background decision
  classifies the exact captured conversation after that cutoff. A strongly supported
  acknowledgment/FYI without an open action appears under FYI, with no attention;
  ambiguous/unavailable/changed-source results keep the existing suggestion. Manual
  tracking and reopening remain authoritative, except THANKS CLOSES (JEV, 2026-10-04): a
  Waiting-on-student follow-up the student only thanked for is resolved by itself, with Undo. Request labels open existing absence,
  makeup, office-hours, grading and materials controls; they save/send nothing. Since
  2026-10-08 that decision may be OpenAI Decisions, read through its own calibrated gates (JEV TRIAGE PROVIDER). During
  its host's outage triage pauses rather than failing every new conversation, and the page says so in one line beside a
  Retry all failed (JEV HOST FAILURES and RETRY ALL FAILED, 2026-10-09).
  JEV URGENCY/WELLBEING (2026-10-03, brainstorm item 3; docs/decisions/jev.md): the
  same decision rates urgency (sorting and a Soon/Time-sensitive chip). WELLBEING OFF (requirement, 2026-10-12: "Off
  entirely"; JEV "WELLBEING OFF"): no message is a possible wellbeing concern any more, here or wherever these rules name
  one below; what follows describes the check as it was. The same decision asked about
  a possible wellbeing concern, which pins the conversation first, is never FYI and
  counts on Home (a count only) until the instructor marks it reviewed
  (enrollment-owned, append-only) or resolves the follow-up. REVIEWED HOLDS
  (2026-10-06, requirement, of a reviewed concern back after a nightly due-date move:
  "why did this pop back up after I already handled it?"): Mark reviewed covers every
  later analysis of that conversation until the student writes again (a message
  captured, edited or linked after the reviewed analysis), even when Jev reads the same
  messages again because other context, such as the week's due dates, changed. JEV WELLBEING CARRIED (2026-10-09): an
  unreviewed concern raised by a reading of the student's newest message stays until then, whichever provider reads the
  conversation next, unavailable, pending, failed or calmer.
- **A COORDINATOR'S FOLLOW-UP** (2026-11-14, COORDINATOR; the instructor: "I want the coordinators emails to be included in the
  followup pipeline just like student's"). A conversation with a coordinated term's coordinator (COMMUNICATIONS "THE
  COORDINATOR'S MAIL") is a follow-up like a student's: listed when the coordinator wrote last, tracked with the same
  statuses and next step, reopened by a newer message of theirs until the instructor has written after it, counted in Needs
  me and on Home. OWNER: the course TERM. Its records carry the term's owner key `coordinator:<term id>` where a student's
  carry the enrollment ID (COORDINATOR "THE OWNER KEY"); migration `student_support/018_coordinator_owner` makes four tables
  again, every row carried over, so that key may own a follow-up (`student_support_items`), a preparation
  (`student_support_suggestion_runs`), a mute and mail written from the follow-up, each held by a trigger to an enrollment
  (of its term, where it has one) or a term's coordinator key. In such a conversation the one it is with is the coordinator,
  by the link that says they wrote the message; the instructor's messages are the instructor's; a student's message that
  only copied the coordinator is that student's and not part of it.
  WHAT COUNTS (my choice, not asked): a conversation is suggested only while the term is coordinated by that address, and
  only for mail that came after the course card named them. The term's earlier mail with them is kept and read as
  background, never queued, so ticking the box mid-term does not fill the queue with months of old mail. A follow-up
  already tracked stays when the box is unticked, still naming them.
  THE PAGE (`static/student-support.js`): the queue row and the review name the coordinator with "Coordinator" where a
  student's show the section; the name opens no student record; the message is headed "Coordinator's message" and read from
  the coordinator's record; the status reads "Waiting on coordinator"; "Write to coordinator…" opens Mail's compose window to
  the address on the course card, in their thread when their latest message here is an email, and the send is recorded with
  the follow-up (WRITTEN FROM A FOLLOW-UP); Edit follow-up… names them in place of the student picker.
  A STUDENT'S ALONE: office-hours appointments (refused), joins and Keep separate (SAME MATTER; not offered, refused), Watch
  until done, wellbeing reviews, the conversation analysis and its FYI, urgency and thanks rules (JEV), the closing rules,
  and the Mail page's Students list. A coordinator's thanks therefore reopens its follow-up like any message of theirs.
  The Assistant prepares it under its own instructions, every step a card (ASSISTANT "A coordinator's follow-up").
  Checks: `tests/test_coordinator_mail.py` (`FollowupTests`), `tests/test_coordinator_followups.py`,
  `tests/test_coordinator_followups.mjs`.
- **TERM POLICIES** (2026-10-03, requirement: "a place for me to save Term Policies which act as context/guide the agent's
  responses/actions/suggestions"). Owner: the course term (`course_terms.id`), `student_support_term_policies` (migration
  `student_support/006`): immutable revisions of guidance per term (at most 20,000 characters in its text projection; empty clears it), exact
  request IDs and the revision the page showed. Written from the Follow-up page's Term policies window (`GET` / `POST
  /api/student-support/policies[/<term_id>]`); an assistant write is an Approve card. Every follow-up preparation for the
  term's students carries the latest text as the instructor's guidance, records the revision it used, and is flagged on the
  page when the policies changed since (Retry or Ask for changes applies them; nothing is regenerated by itself).
  Retry suggestions also applies policies on every ready, needs-input, failed, interrupted or settled preparation,
  including older preparations that recorded no policy revision. Queued, running and superseded preparations do not offer
  Retry. The existing revision/source checks still apply; only unsubmitted proposals are replaced and completed receipts stay.
  RETRY BUTTON (2026-10-10, requirement): the control is always named "Retry suggestions", including in the Action desk
  after policies change or preparation fails. It is a framed secondary button with the shared circular refresh-arrow icon;
  contextual warnings explain what changed without renaming the action.
  A plain Retry carries its meaning into the preparation: use current sources, latest policies and earlier instructor guidance.
  It needs no new revision instructions. Replacement events marked `replaced_by_request` are superseded proposals, not instructor
  rejections, so any later preparation may propose the same card again, an automatic one after the student's next message too
  (2026-10-19, Laura Dunham's new booking card refused as already recorded); historical events are interpreted in the prompt without rewriting them. Genuine missing facts may still require a question.
  Policies never permit what Faculty refuses, and a student's message never changes them. They decide what a reply asks for,
  offers or declines; a drafted reply never recites them (REPLY VOICE, ASSISTANT), asks for absence documentation only when
  graded work is missed (DOCUMENTATION ONLY FOR MISSED WORK, ASSISTANT), never proposes excusing an absence on the student's
  own account (EXCUSE NEEDS EVIDENCE, ASSISTANT), and never asks the student for what the
  records show; each preparation is told when the student wrote and what their section was given then (FOLLOW-UP FACTS), and,
  for a quiz they have no paper from, the makeup times to offer or that the booking card offers them (MAKEUP TIMES), the
  instructor's office hours by weekday with their rooms, so a reply names the right room and sets a student's mix-up right
  (OFFICE HOURS, ASSISTANT), and the instructor's private notes about the student, never mentioned in a reply (INSTRUCTOR'S
  NOTES). A preparation answers its own
  conversation and reads the student's other conversations as background (OTHER CONVERSATIONS AS BACKGROUND, ASSISTANT). Catalog backups keep them; generic exports
  leave them out.
  Instructor refinement, 2026-10-03: "individual entry fields/options for each of the various policy areas" based on previous
  student interactions. Term-owned `student_support_term_policy_areas` (`student_support/007`) stores the area IDs, handling
  modes and notes with the same immutable revision; `student_support/policy_areas.py` owns their definitions, validation and
  deterministic text projection. Areas distinguish absences/documentation, attendance/iClicker, in-class quiz makeups,
  exam makeups/conflicts, online/video quizzes, homework/late work, grades/regrades, office hours, learning support,
  accommodations, technology/access, exam-day logistics, wellbeing, reply style and additional guidance. Each has No guidance
  yet, Follow my guidance or Ask me case by case; unset adds no instruction, and Ask me requires instructor guidance before
  proposing a decision in that area. Editor questions are not policies and are never fed to preparations. Existing text is
  preserved in Additional guidance without rewriting history or automatically deriving rules from old replies. The editor's
  `GET ?areas=1` supplies the area definitions and values; ordinary reads retain their existing body. POST accepts either
  `areas` or legacy `content`, never both. The dialog edits one area at a time, retains drafts across area/term switches and
  saves the selected term while staying open; saving must preserve edits typed during the request. No new routes, assistant
  tier or outward automation is introduced.
- **MEETING HOURS** (2026-10-05, requirement: "for this semester, I want to only allow scheduled meetings on tuesdays or
  thursdays from 1PM-5PM. What's the best way to implement this so that it doesn't carry through to every semester (future
  semesters will have a different schedule)"; then "yes build it, makeups use it too"). Owner: the course term (`course_terms.id`),
  `student_support_meeting_hours` (migration `student_support/010`): immutable revisions of the term's weekly windows
  `[{weekday 0 (Monday)–6, start, end "HH:MM", location}]` (at most 14, each at least 30 minutes, none overlapping on a day; an
  empty list clears them), exact request IDs and the revision the page showed (`POST /api/student-support/meeting-hours/<term_id>`
  `{request_id, base_revision, windows}`, 409 when stale or a request ID is reused for other hours; identical windows record
  nothing). A new term starts with none, so nothing carries from one term to the next. Written in the Term policies window
  ("Meeting hours", beside Office hours & appointments, saved with the policies; only its `?areas=1` read carries them, the
  plain read is unchanged); an assistant write is an Approve card. Read by: a follow-up preparation's facts (the open times a
  reply offers instead of asking the student's availability, ASSISTANT MEETING HOURS) and the bound on a meeting it puts on the
  calendar by itself (ASSISTANT DONE FOR YOU); quiz makeup cards (ABSENCES "MAKEUP BOOKING" TIMES). `student_support/meeting_hours.py`
  (plain values, no calendar). The instructor may still schedule any time by hand. MAKEUP ROOMS (2026-10-05, requirement: "BLOC
  319 is my personal office and has limited room, the other rooms 306 and 309 are rather large"; asked, they chose to tick rooms per
  term): the same revision holds `makeup_rooms`, the office-hours rooms big enough to seat a makeup (migration `student_support/011`,
  at most 10, each once, case ignored; a save that leaves them out keeps them). The window lists the rooms of the instructor's own
  office-hours blocks on the calendar over the next six weeks (`office_rooms`, only on the `?areas=1` read) to tick. Once a term uses its
  Term schedule (SCHEDULING "USE THIS SCHEDULE"), its meeting hours and makeup rooms come from the schedule instead, are edited
  only there (this route answers 409), and the Term policies window shows them read-only with a link to it. An office-hours
  appointment window warns of an overlap or a time outside office and meeting hours (SCHEDULING "MEETING HOURS AND APPOINTMENTS")
  and never refuses the save.
- **TAB NAVIGATION** (2026-10-08, requirement-approved response to lag between Follow-up and Office hours).
  The selected tab changes immediately, with a loading state until that visit's fresh response arrives. No previous visit's
  records or actions appear while loading. Returning from another sidebar page also starts a fresh visit. Shell redraws
  share that visit's read; polling, focus/visibility refresh and reads after writes remain. Late responses from an earlier
  visit or a read before a completed write cannot replace the fresh result. A failed first read keeps the tabs and Try again;
  a failed background read keeps the current view and its input with a visible error. The calendar snippet also reads afresh
  on each visit. Office-hours listing skips conversation reconstruction, with the same response, ordering, ownership and
  revision checks. This adds no retained owner, route or cache; all records stay with their existing enrollment owners.
- **APPOINTMENTS.** An appointment has a date, campus-local start/end time, purpose,
  location and scheduled/attended/cancelled/no-show state. Calendar projection titles it
  "Office hours: <student's roster name> · <purpose>" (2026-10-06, CALENDAR "OFFICE HOURS") and omits private details; the existing
  Google calendar sync projects that same event when Your events is enabled;
  each appointment has its own stable calendar identity. Outcome recording changes no
  grade, absence or note. MEETINGS COMPLETE THEMSELVES (2026-10-21, requirement: "I don't want to have to manually type office
  hours outcomes, let's retire this requirement to type something about the office hours after they happen"): a scheduled
  appointment whose end time has passed reads as Attended, outside Active, and never needs attention (no Needs me count, no Home
  card). A view, nothing saved; Update still records a cancellation or no-show, and a note stays a separate optional action. Adding a private note, follow-up or message is a separate
  explicit action through its owning service. Student identifiers never enter URLs.
  MEETING LENGTH (2026-10-06, requirement: "make the default meeting length 30 minutes", then "make the end time follow the
  start in the appointment form"): a new appointment starts at 2:00–2:30 pm; moving Start moves End with it, keeping the length
  the form had (30 minutes when End was not after Start), never past 23:59 (`followingEnd`). Editing End sets a new length.
- **AUTOMATIC SUGGESTIONS** (2026-10-03, requirement's approved plan; ASSISTANT-FOLLOWUP-SUGGESTIONS). The Assistant prepares
  actions for eligible current-term follow-ups needing attention, including the current queue, with every write held for
  instructor approval except the record-keeping it does by itself (DONE FOR YOU, ASSISTANT: a pending absence with the student's
  files, attaching their files, an office-hours appointment, a hold on an absence's quiz makeups (ABSENCES HOLDS, 2026-10-06),
  and this follow-up's own status once its reply is sent; listed
  on the follow-up with Undo or Don't). FYI, resolved, waiting and not-yet-due snoozed behavior and Start from now remain authoritative.
  One generation per exact eligible source state; changed conversations invalidate obsolete proposals and permit a fresh
  generation. Failed, interrupted, declined, expired and uncertain work needs an explicit retry or revision. WIDE SCOPE
  (2026-10-03, requirement's request): suggest any change the conversation calls for, for this student, other students or
  a whole class, a change to Faculty's code included; an absence or grading decision is the instructor's approval of its card.
  Messages reach only the student, their sections' TAs and the instructor (ASSISTANT-FOLLOWUP-SUGGESTIONS). No Waiting on
  student change takes effect for a student-last conversation until a reply has a confirmed successful Mail receipt (it may be
  proposed beside the reply card, ONE ROUND below).
  Runs/events/action references belong to the student's term enrollment (`student_support/004_suggestions`), never copied
  correspondence. They pin exact linked message revisions/digests, follow-up revision, memberships and cutoff. Metadata,
  bounded explanations, clarifications and instructor feedback are retained; no background transcript is saved. Every action
  and receipt remains Assistant-owned and every captured communication keeps its existing owner.
  `GET /api/student-support?...&suggestions=1` adds the Suggested actions area; default reads stay unchanged. It uses the shared
  Assistant approval cards, Send/Approve/Decline handlers, source evidence and outcomes also shown in Changes. Instructor-only
  `POST /api/student-support/followups/suggestions/<run_id>/{revise,answer,retry}` requires an exact request ID, `seen` run event
  and source `fingerprint`; answers also pin `question_id`. Feedback supersedes only unsubmitted proposals. Quiet refresh keeps
  focus, text and disclosures, including on refresh failure. Home remains a count-only projection.
  OWN WRITES (2026-10-03, requirement's follow-up review): a run's source (v2) is the student's messages, anyone else's
  replies, the follow-up's tracking revisions not made by its own approved cards, memberships, the term and the cutoff. A
  captured instructor message is this follow-up's own reply when its Gmail message ID is a send of one of its cards (or of an
  edited version sent from Mail in a card's place), else when its text matches such a send made no more than ten minutes
  later; a tracking revision is its own when an approved card's request produced it after the approval. So sending the reply,
  or approving a tracking change, never withdraws the other cards. A conversation the own reply answered stays listed as
  Replied (in the `suggestions=1` read only) while its cards wait or its preparation runs. Runs recorded before this rule keep
  their original comparison. CONTROLS (same request; enrollment-owned unless noted, migration `student_support/005`):
  Prepare now (`student_support_suggestion_priorities`), Don't suggest for this conversation
  (`student_support_suggestion_mutes`: a queued preparation is withdrawn, waiting cards stay), Pause automatic suggestions
  (`student_support_suggestion_pauses`, workspace-owned: Prepare now and the instructor's own requests still run), Edit in Mail
  (`student_support_suggestion_replacements`, referencing the Mail send by ID) and a note on a declined card
  (`student_support_suggestion_notes`). All are append-only with exact request IDs. The page shows the waiting proposals as a
  numbered plan (FOLLOW-UP CLARITY), the queue position, Approve all, the calendar beside a meeting card, a Suggestions ready filter and count, and says
  when the Inbox worker that queues new conversations is not running. Changes lists only follow-ups that wait for the
  instructor, each with its own cards. The pause switch lives on the follow-up page, not in Compute settings (whose values
  feed grading fingerprints).
  ANALYSIS FIRST (2026-10-03, requirement: "yes build it"): automatic preparation of a conversation the student wrote last
  waits while Jev's analysis of that exact state is pending or running, at most 10 minutes from when the Inbox worker first
  saw it waiting (process memory; a restart starts the wait again). A thank-you the analysis moves to FYI then costs no
  Assistant turn, and urgency and a possible wellbeing concern order the queue from the start. A completed, failed or
  uncertain analysis, Jev not configured, Prepare now, and the instructor's own requests never wait. The page says "Waiting
  for the conversation analysis".
- **SEARCH.** Ctrl K reads current students from the existing roster-backed picker
  service and opens the existing gradebook student record. Students remain distinct
  by enrollment and section. No student result enters persisted recent places.
- **INSIGHTS.** Teaching review uses certified FRQ grades, their pinned questions
  and rubric evidence, and the bank's objective links. Show denominators, versions,
  certification and missing/untagged evidence. Exclude Inbox-deleted submissions.
  Low scores are evidence for review, not a diagnosis. Review-lesson and practice
  drafts start only from an instructor action in the existing Assistant and
  identify their source evidence. They remain chat drafts unless separately saved.
- **DELIVERY.** Show grading, certification, gradebook and outward delivery separately.
  A receipt is evidence of a past write, not proof of the remote site's current
  state. Comparison reads use the owners' existing APIs; fresh comparisons are
  explicit. Neither a read nor a local grade automatically publishes or sends.
- **BLUEPRINTS.** Specify objectives, points, question kinds and fixed bank questions.
  Retain the selected bank content revision with the blueprint; later bank edits
  do not silently change it. Validate the resulting assessment against the blueprint
  during writing and before approval, and show per-version structural coverage.
  Objective alignment is the instructor’s review, not a claim of semantic validation.
  To change constraints, create a new draft. Fixed questions with separate image assets
  are refused until the bank contains a printable LaTeX figure; no picture is dropped. A failed constraint requires a
  revision, never silent substitution. Reuse quiz drafting/approval and its receipts.
- **CHECKS.** Exercise synthetic catalogs, scripted model responses and fake outside
  services through manage_checks.py. New write routes remain assistant Approve
  actions by default; no automatic tier is broadened.

- **FOLLOW-UP REVIEW LAYOUT** (2026-10-03, requirement: "way too complicated, spread out, confusing"). The Follow-up page shows
  a compact filtered queue and one selected conversation, with previous/next controls. Selection follows the enrollment and
  exact conversation key through tracking; it stays in browser memory, never the address. Read the selected student's captured
  timeline through Communications and show only the exact observed student-authored message (including merged Canvas email
  copies), never a subject/name match. The complete message and reply remain readable and keyboard-scrollable at bounded height.
  Send, Edit in Mail, Decline and exact approvals stay on the shared cards. Put narration, evidence, related owner shortcuts
  and automation controls in disclosures; keep wellbeing concerns and a confident evidence contradiction visible (EVIDENCE
  WARNING below). Combine identical observed sources
  into one list. Quiet refresh and conversation switches preserve unsent form text and disclosures in bounded browser memory.
- **FOLLOW-UP CLARITY** (2026-10-03, requirement: "The follow-up page is not very user friendly or intuitive. It's not clear what
  actions the agent will take in its suggestions."). Every waiting card says plainly what Approve would do before anyone reads
  its request: a title naming the action ("Record an absence", "Mark this follow-up resolved", "Reply by email"), the owning
  page, whom it is for (this follow-up's student by name, or the other student it names), each field it sets in words (dates,
  reasons, decisions and states by their page labels; a revision shows the current value struck through beside the new one;
  request IDs, revision checks and record IDs never read as changes), what follows from it (an excused absence's iClicker
  credit and makeups owed, a calendar entry, that the student is not told) and the button named for the action ("Record
  absence", "Schedule", "Send"). The Assistant's own summary stays as its note and the exact request stays one click away.
  The opt-in `suggestions=1` read adds this as `effect` on each waiting card (`assistant/followup_effects.py`): a read of the
  card's exact request and the records it names as they are now, writing nothing and never changing what Approve submits; a
  request that no longer reads (a route since removed) shows as the Assistant wrote it. Above the cards the section says how
  many steps there are and that nothing happens until the instructor approves, with a numbered list when there are two or more;
  each card carries the same number. An open question comes before the cards and says that answering prepares again and
  replaces the steps not yet approved; the question reads at body size. The narration disclosure is "Why the Assistant
  suggests this"; a confident evidence contradiction stays visible above the cards (EVIDENCE WARNING). A card revising or naming a record of this student
  (a follow-up or absence named only by its ID) is not described as wider in scope. Drafts and messages keep their bounded,
  keyboard-scrollable height (taller now) and show an ink rule under them until read to the end. The selected review's edit
  control reads "Edit follow-up…", and the queue counts "suggested steps to review".
  CANVAS PUSH CARDS (2026-10-06, requirement of two "Save a change on Canvas" cards moving a WebAssign due date: "give the
  cards clearer titles"): a push of one section's Canvas course or of the term's copy is read from the card's latest preview,
  the plan Approve runs (a changed plan is refused), not from its draft: one change is its title with the sections it reaches
  ("Move the due date of “…” to Wed, Oct 14 in section 507", its fields on the campus clock), several are counted and listed
  (a change only some sections get says which), whom it reaches is everyone in those sections and the button reads "Push to
  Canvas"; a preview that does not read titles the card by its course only.
  GRADES ON THE CARD (2026-10-09, requirement of a "Save a change on Gradebook" card giving a poll 10/10: "if it proposes a
  grade change, I should see it in the card"): a Gradebook score change is titled by what it does ("Change the grade of “Class 5 -
  Poll” to 10 / 10", "Excuse …", "Mark … missing", several counted) and each cell shows on the summary by its column (and student,
  when several) with the grade it replaces struck through beside the new one, read from the revision the request names (none is
  an empty cell); a cell's note is a long field like any other.
  RUBRIC NOTE CARDS (2026-10-21, requirement of a "Save a change on Grading" card that showed only a Sha256: "what is going
  to be changed here if I approve this"): a rubric edit is titled by its question ("Change the note on Quiz 5 Version A
  Question 2"), is for every student who answered it, and shows on the summary each line it touches as a short "Line 2" row with
  its points and words, then the words a note adds (or its old and new text, struck through), changed wording or points, and how
  many automatic grades of the question go back to review in Grading, since any rubric or note change ends the finalization they
  were accepted under. Field names stay short and the name column never takes more than 14rem, so a value is never squeezed
  (2026-10-21, requirement's screenshot of the note one letter per line).
  SIDE BY SIDE (2026-10-04, requirement: "the painting here is wasted space, I think the layout would be much more
  appropriate to have the suggested actions in a more compact form where this painting is in a side-by-side to the original
  email"): the selected review shows the student's email (with Write to student, Edit follow-up and related records), held in
  view as the page scrolls, beside its suggested actions in a compact form; one column when nothing is suggested. The Office
  hours column and its painting are gone; the week's office hours are a short list under the conversation queue.
  REVIEW FLOW (2026-10-04, requirement's plan to need less of their time; replies always keep their Send): the queue lists a
  possible wellbeing concern first, then conversations whose reply waits for Send, then other waiting cards, then questions,
  the server's order within each (`reviewOrder`). LEDGER QUEUE AND DECISION (2026-10-04, requirement's "go with Ledger",
  DESIGN-LANGUAGE §1.4, §1.6): the queue is grouped under "To review", "Your to-dos", "Waits on students", "Follow up later"
  and "Nothing waits" (`waitGroup`, `queueGroup`, `groupedOrder`: suggested steps or a question waiting, a wellbeing flag or the
  follow-up waiting on the instructor wait on the instructor; a To do is the instructor's whatever its line reads, Work still
  owed and Reply still owed included, by `waitingOn`'s `on` and never its wording: requirement, 2026-10-11, "there are
  things in the 'waiting on students' section that are actually waiting on me"), the review order kept within each group and
  the selection moving in the same order; what waits on others reads muted.
  WHAT YOU DID (2026-10-13, requirement: "a lot of these are 'waiting on me', but I have already approved the suggested
  actions. This view makes it seem like I haven't looked at the follow up at all yet"; asked, they chose two groups over one
  group with a new line): what waits on the instructor is "To review" when something there is new to decide (steps waiting or
  being approved, a question, a wellbeing flag, a student message they have not answered) and "Your to-dos" when they have
  dealt with the conversation and still owe work (a To do, their open tasks, a due check-back, or their own reply with nothing
  recorded since). Both counts are the orange needs-you block and both still count under Needs me. A to-do row says what the
  instructor did last (`actedLine`): "You approved 2 steps · Oct 12" (`suggestions.approved` on the `suggestions=1` read: the
  steps they approved in the newest of the follow-up's preparations in which they approved one, and when; an edited card
  counts once, a reply sent from Mail in a card's place is their reply, the Assistant's own records stay Done for you), "You
  replied Oct 12" (`answered`) or "You updated it Oct 12" (`updated_by`, the actor of the latest revision, on that read only),
  the newest of them, the approval when its own reply followed within ten minutes. A row never repeats its title: when the
  next step is the title, the line keeps only whom it waits on, and the bare "Waiting on you" is left out (`queueNext`). Under the review's title, what it waits on: when it waits on the instructor an orange box with
  the action that settles it, Approve all when two or more steps wait (`approveAllMarkup`; the Suggested actions section then
  leaves it out) or Write to student… when nothing is suggested; when it waits on others a quiet line. After the instructor's own Send, Approve, Decline or Approve all leaves the
  selected conversation with nothing waiting, the next conversation that waits is selected (never on a poll or while a draft
  is open for editing). Shortcuts through the central registry: Alt+J / Alt+K next / previous conversation, Alt+A Approve all,
  Alt+E edit the first waiting draft. A card's "Assumed" note offers the Assistant's alternatives (ASSISTANT).
  SUMMARY FIRST (2026-10-04, requirement of an absence card: "this absence record is cluttering the page, I don't need to see
  the entire proposal, just a summary and option to expand to see the whole thing"): a step card shows its title, page and
  person, its one-line fields (section, dates, reason, decision; a field over 80 characters or on several lines is not one),
  any other-student or certified-work note, a calendar overlap and its buttons; "Show the whole proposal" holds the long
  fields, what follows and who is told, the Assistant's note, the page's preview (open when it failed) and the exact request.
  A field the card marks `show` stays on the summary whatever its length: a regrade decision's reply (requirement, 2026-10-07: "the
  response should be easily visible from the initial suggestion without having to expand"), and a change's rubric lines. A
  regrade decision that keeps the grade carries no certified-work note: it changes no grade. Its "Edit grade or reply" edits the
  rubric lines, a point adjustment and the reply on the card (REGRADES "EDIT THE GRADE").
  FINISHED STEPS (2026-10-04, requirement of a reviewed follow-up's Done cards: "why do these still show if they are taken
  care of", then "yes, do it"): once nothing waits for the instructor (no waiting card, no open question), the cards already
  decided fold into one line ("4 steps done · 1 declined"; a Mail replacement whose send was accepted counts as done), each
  card, a declined card's "Tell the Assistant why" included, one click away; nothing is removed or changed. A failed card, a
  send not confirmed and a card still submitting stay in view. The "Evidence needs review" warning shows only while something
  waits. Done for you, Retry suggestions and Ask for changes are unchanged.
  APPROVING (2026-10-06, requirement of a follow-up whose Approve all of four steps showed two still waiting, then "A selected
  card changed or expired": a 30-second refresh had landed mid-run, about a minute for a reply and two Canvas pushes, and drew the
  steps not yet reached as waiting with a second "Approve all 2"; "yes, build that fix"): from the click until the request
  returns, the steps sent from this browser (Approve all, or one step's Approve or Send, in either review location) read
  "Approving…" in every refresh: no Approve, Decline, Approve all or Write to student…, the plan and the queue say "Approving N
  steps…", and the server's record takes over when the request returns. Browser memory only (`withApproving`). A second Approve
  all whose steps all went through anyway reads "Already approved. Nothing was submitted again." (ASSISTANT-BATCHES).
  A step the server has approved and is still running counts as Approving… too, and "Some work still needs attention" waits for
  the request to return, standing meanwhile only for another step that needs a look (`needsAttention`; 2026-10-08, requirement of
  a regrade decision whose approval ran 51 s: "it said there was an error but then it just disappeared").
  EVIDENCE WARNING (2026-10-05, requirement of the "Evidence needs review" callout: "this error keeps popping up"; measured: it
  had shown on all 33 Jev evidence checks since 2026-10-03, 29 follow-ups and 4 chat answers, correct drafts included, with 3
  of about 160 paragraphs reaching Jev's .8 confidence; asked, they chose both changes): (1) the callout above a follow-up's
  cards appears only when a paragraph is "contradicted" (Jev keeps that verdict only at .8 or more); uncertain and
  unsupported verdicts, irrelevant sources and omissions are shown in Why the Assistant suggests this, unchanged. (2) The check
  is given what the preparation was given and did (EVIDENCE INPUT, JEV): a short question instead of the preparation's
  instruction, whose length alone marked every check partial, and, before the turn's own reads, Faculty's text given to the
  preparation (identities, FOLLOW-UP FACTS, term policies, earlier cards, the instructor's requests) and the exact requests of
  the cards it proposed. Chat answers keep their own display.
  ONE-CLICK ANSWERS (same day: "I selected one of the answers and it just put it in the prompt box instead of just doing
  it"): choosing an offered answer sends it once every question of the form has an answer; the box is for any other answer.
  EDIT IN PLACE (2026-10-04, requirement: "I needed to edit one of the emails, but instead of editing it in place, it wants me to
  send it immediately. I would rather edit it in place because I want to see everything in the follow up card at once before I
  approve any part of it"): a waiting mail card's Edit opens its subject and message on the card itself; Save changes puts the
  instructor's version on the card in the draft's place and sends nothing, and Send stays the instructor's own click. Approve
  all waits while a card is open for editing. Recipients and attachments are not edited there; Edit in Mail, inside the editor,
  still opens Mail's compose window (which sends from Mail). Server: instructor-only `POST
  /api/student-support/followups/suggestions/<run_id>/edit` (`action_id`, `seen`, `subject`, `body`, `request_id`) makes the
  edited mail a new waiting card of the same run through the follow-up's ordinary write checks and Mail's preview, recorded as
  the instructor's proposal, declines the old card as edited (`replaced_by_edit`, not an instructor rejection) and records the
  link in enrollment-owned, append-only `student_support_suggestion_edits` (migration `student_support/008`, owner: the run's
  enrollment). The review shows the edited card where the old one stood, marked "Your edited version", and leaves the old one
  out; later preparations read it as the instructor's edit. An edited reply also turns the round's scheduled status change into
  a card that waits for the instructor (ASK AFTER AN EDIT, ASSISTANT).
  DECISION ON THE CARD (2026-10-12, requirement: "make it into a dropdown menu that defaults to the model's suggestion, but
  allows me to change it from the dropdown"): a waiting absence card's Decision is a dropdown on what the Assistant proposed.
  Choosing another decision saves it on the card through the same route (`status` in place of `subject` and `body`) and
  records nothing; Record absence stays the instructor's own click, and the card says the decision is theirs and what was
  suggested. Rules: ASSISTANT "DECISION ON THE CARD".
  ONE ROUND (2026-10-04, requirement: "Does it indefinitely generate new suggestions even after you already approve the first
  ones?", then "I don't understand why a followthrough would ever be needed, shouldn't everything be suggested the first time",
  and "yes, do it"): a preparation proposes everything the follow-up needs at once, and nothing is prepared from the receipts of
  approved cards (the receipt continuation is retired; a settled run stays settled until a new student message or the
  instructor's Retry). What used to wait for a second round is in the first: an absence carries the student's captured files
  (ABSENCES WITH DOCUMENTS), a new makeup sitting seats the student (EXAMS WITH PARTICIPANTS), and this follow-up's tracking
  change is proposed beside the reply card (and, since DONE FOR YOU, recorded by itself once the reply is sent: SCHEDULED STATUS
  in ASSISTANT). Waiting on student, for a conversation the student wrote last, may be proposed only
  after a reply card in the round (or a confirmed reply) and is submitted only once that reply has a confirmed receipt; Approve
  all submits record changes, then mail, then tracking (ASSISTANT PLAN COMPLETION, 2026-10-08). STILL WAITING (2026-11-04, requirement: "some of
  them aren't actually waiting on me, but rather waiting on the student", of Britt Bowers's "I will get that information to you as
  soon as possible", which stayed Needs my reply for a week because the preparation's Waiting on student was refused): a follow-up
  already Waiting on student, whose new message Jev reads as only an acknowledgment or information with no wellbeing concern, may
  stay Waiting on student with no reply (`FollowupSuggestions._still_waiting`), recorded by itself under Done for you with Undo: what
  it waits for was already asked, and the status does not change. Runs recorded with reason `receipts` before this stay readable, and their plan still begins
  "Follow-through on the steps you approved" (the `suggestions=1` read carries the run's `reason`). Attaching a student's
  captured file to their absence reads as "Attach <file> to the absence record" with the absence's dates and decision; a new
  tracking of this conversation is titled by its state ("Mark this conversation resolved").
- **WAITING ON** (2026-10-04, requirement of a follow-up still shown as "Needs my reply" after their reply said they would look
  into DR-center quizzes: "it doesn't show where it said I would follow up with her", then "all of the current ones in the
  follow up menu don't really show what it is waiting on"; asked, they chose a short next-step line and a new To do status).
  A follow-up revision may carry `next`, one line (at most 200 characters) saying what it waits on; kept only when given, so
  records without one and their reads are unchanged. Status `todo` ("To do") is an action the instructor still owes after
  replying, as `needs_reply` is a reply still owed: both need attention and count under Needs me; a newer student message
  reopens it as Needs my reply, except one the instructor has answered (ANSWERED, 2026-10-13, requirement of a follow-up
  still reading "Reply to Mary's new message" after they had approved its reply and declined its status card: "I have already
  approved the suggested actions"; asked, they chose "Back to its saved status" over a row asking what it waits on now): once
  the instructor has written after the student's newest message (in the conversation, or mail written from the follow-up,
  WRITTEN FROM A FOLLOW-UP and WRITTEN AT ONCE), the follow-up keeps its saved status on every read, as it was before the
  student wrote, until a status change is approved or recorded or the student writes again; a view, nothing saved,
  `new_reply_answered` on the item, and the review says "<first name> wrote again and you replied. The status is as you left
  it." The worker reads such a follow-up as it read a reopened one (`FollowupSuggestions.answered_again`): a reply written
  outside the cards is still followed by one preparation that records its status, THANKS CLOSES and DEADLINE CLOSES still
  count the student's message, and a reply sent from a card asks for a Gmail capture round at once. Also except a thanks
  (A THANKS ON A TRACKED FOLLOW-UP, 2026-10-07, requirement: "what if thanks is
  actually the end of the follow-up?"): a new reply Jev's triage reads, strongly, as only thanks or information, with no
  wellbeing concern, leaves a Resolved follow-up resolved and a To do as To do (the conversation-wide needs-action answer and
  context beyond six messages do not count: 2026-10-07, the instructor of Jordan Zahn's "Sounds great, thank you!" that
  reopened his resolved Exam 1 follow-up, needs-action .31: "why is this one still showing up"), a view, nothing saved, `thanks`
  on the item, and while a re-read after other context changed waits, the earlier reading of that message stands (JEV READ
  BEFORE, 2026-10-16); on a To
  do the instructor answered after it was set, Jev decides whether they still owe the student another message (JEV TO DO
  THANKS): nothing owed resolves it by itself under Done for you with Undo, owed keeps it To do, and an unsure answer keeps it To
  do with Finished? Mark resolved (`finished` in the review's suggestions). The Follow-up page's `suggestions=1` read adds `answered` ([sent_at, message ID]) to a tracked
  conversation the instructor wrote in last (other reads stay byte-identical). The review shows "Waiting on you / Waiting on
  <first name> / Follow up <date>", the next step (or, without one, what the status implies) and when the instructor last
  wrote, and shows that latest message as "Your reply" under the student's; the queue row shows the same line without what
  its title already says (LEDGER QUEUE "WHAT YOU DID"). Edit
  follow-up… edits Next step and offers To do. The Assistant's tracking change after a reply is Resolved, Waiting on student,
  To do or Snoozed (never Needs my reply), and every one but Resolved gives `next`; To do and `next` are part of the scheduled
  status change it records by itself (SCHEDULED STATUS, ASSISTANT). A To do follow-up is prepared only on request (Suggest
  actions), never automatically: the action is the instructor's own.
  DEADLINE CLOSES (2026-10-08, requirement of Avery Hart's follow-up, Waiting on student for a doctor's note a day after the
  absence was marked Not excused automatically: "this student's available window to submit documentation has closed, but this
  follow up is still in the stack. It should resolve when that happens"): a follow-up Waiting on student is resolved by itself on
  the Inbox worker's turn (`FollowupSuggestions.close_past_deadline`), under Done for you with Undo, once an absence of its
  conversation is Not excused automatically (ABSENCES DECISION DEADLINE) after the follow-up was set waiting, the student has
  not written since (whether or not the instructor answered that message: ANSWERED), and none of the conversation's absences
  is still undecided. An absence is the conversation's when one of
  its preparations recorded it (the create's request ID), or revised or attached a file to it with a confirmed result, or when
  its notice is one of the conversation's messages; days, names or subjects never link one. Only a follow-up with a preparation
  (its Done for you list); once per follow-up revision and absence (request `deadline-closes:<follow-up>:<revision>:<absence>`);
  never after the instructor undid an automatic status change on it; a follow-up set waiting after the decision stays theirs.
  Nothing is sent, and the absence is unchanged. Tests: `tests/test_followup_deadline_closes.py`.
  EVIDENCE CLOSES (2026-10-22, requirement: "there are a lot of followups that are still there that are actually stale"; of
  24 follow-ups Waiting on student, 17 were already settled by records Faculty held: 13 paper copies of the take-home scanned
  days before, a Response Card, a makeup paper and two absence decisions; asked, they chose closing on evidence together with
  CHECK BACK): a follow-up Waiting on student is resolved by itself on the Inbox worker's turn
  (`FollowupSuggestions.close_on_evidence`), under Done for you with Undo, once a record that came in for its student after its
  latest revision (`assistant/followup_evidence.py` `arrived`, catalog only: their paper of an assessment back in a scan, PRESENT
  applied; a Response Card they submitted; an absence of theirs decided, Not excused automatically being DEADLINE CLOSES' own; an
  office-hours meeting of theirs that has passed) is, Jev reads (rule `followup-evidence-v1`, the conversation, its next step and
  at most six records, newest first), what it waits for at .8 or more, with at most .4 that something else is still awaited from
  the student or owed by the instructor. Which record is meant is Jev's question, never a match of words; the 2026-10-22 replay
  on a copy confirmed 2 of the 17 (a Response Card, a makeup paper): nothing in Faculty says the paper the students called "the
  take-home quiz" is "Mathematical Experiments Assignment", stamped after it was scanned, so those wait for CHECK BACK. Only a
  follow-up with a preparation, whose student has not written since (an answered message included), with no open task and no
  open wellbeing concern; once per follow-up revision; never after the instructor undid an automatic status change on it. One
  Jev question per exact state: its receipt is kept and a failed or uncertain one is never asked again by itself. Nothing is
  sent and no record changes. Tests: `tests/test_followup_evidence_closes.py`.
  DECISION CLOSES (2026-11-04, requirement of Sanya Moorthy's To do "Select Excused for the October 30 absence", open four days
  after they did so on the Absences page; asked whether such a To do should resolve itself with Undo: "yes"): a To do is resolved by
  itself on the Inbox worker's turn (`FollowupSuggestions.close_decided_todo`), under Done for you with Undo, once an absence of its
  student is decided after its latest revision (`followup_evidence.decisions`; a To do the Assistant set counts from the student's
  newest message too, since it is recorded after the round's own decision cards; one the instructor saved themselves does not), Jev
  reads (rule `followup-decided-v1`) that this is the absence it asks to decide or record at .8 or more, with at most .4 that
  anything else is awaited from the student or owed by the instructor (what Faculty sends by itself once an absence is decided, a
  booking card, does not count). Which absence is Jev's question, never a match of words. Never while an absence of the student is
  undecided, or a promised reply, a check-back or a joined follow-up's task is open; open work tasks of the instructor's are completed
  with it, each naming the decision, and Undo puts them back. Only a follow-up with a preparation, whose student has not written since,
  with no open wellbeing concern; once per follow-up revision; never after the instructor undid an automatic status change on it. One
  Jev question per exact state; a failed or uncertain one is never asked again by itself. Nothing is sent and no record changes.
  Tests: `tests/test_followup_evidence_closes.py`.
  CHECK BACK (2026-10-22, the same request; they chose a check-back date for every Waiting on student follow-up): one with no
  date of its own comes back to Needs me on its check-back day (`student_support.service.check_back`, read in code from the
  revision that set it: the day after the latest definite day or span its next step names after that day, else a week
  after, never more than 60 days on; the day it was set does not count, requirement, 2026-11-04, "some of them aren't actually
  waiting on me", of two follow-ups set on October 30 for a note "covering October 30" that came back on October 31), shown as "Check back <day>" (`check_back` on the item: a view, nothing saved; a default
  read now carries it on such follow-ups and counts them once they are back). One with a date comes back on it as before. Back,
  it waits on the instructor ("Still waiting on <first name>?", in the To review group): its decision box names what it waited
  for and since when, lists what came in for the student since (EVIDENCE CLOSES' records, `suggestions.arrived` on the
  `suggestions=1` read only), and offers Resolved and Wait a week (the instructor's own revision: Waiting on student kept, its
  next step kept, dated a week from campus today); Write to student… stays in the row under the conversation and Suggest
  actions in its actions. A follow-up back on its day with no open task is prepared only on request, never automatically
  (`FollowupSuggestions.eligible`): it is the instructor's to look at, and nothing new was said.
- **A REPLY FROM ITS CARD** (2026-11-04, requirement, of Ashton Nolan's follow-up still "Needs my reply" after its card's reply went out and the
  student booked: "what is going on with this one"): a reply a follow-up's own card sent, or Mail sent in a card's place, counts as written from
  that conversation once captured (`_card_replies`, read beside WRITTEN FROM A FOLLOW-UP), since to a Canvas conversation it starts a Gmail thread of its own.
  It answers the conversation as a reply in its own thread does: ANSWERED for a tracked follow-up, and off the list for one with no saved status, until
  the student writes again. It joins no thread: the Gmail thread stays its own conversation (SAME MATTER is unchanged).

- **WRITTEN FROM A FOLLOW-UP** (2026-10-07, requirement of Fiona Weaver's To do follow-up, whose "Write to student…" email went
  out as a new Gmail thread, so the review still showed their older reply: "I should be able to see a reply I send through the
  follow-up … any reply sent through the follow up itself will automatically update it to show the reply", and the follow-up
  stays unresolved while they still owe something). After Mail sends (or cannot confirm) an email written with a conversation's
  Write to student…, the page records it with that conversation (`POST /api/student-support/followups/written` with
  `enrollment_id`, `source_key` and the send's `send_request_id`; idempotent by send; a send Gmail refused, another student's
  conversation and a second conversation for one send are refused; the assistant's policy is NEVER). Owner: the student's
  enrollment, by conversation (`student_support_conversation_mail`, append-only; no message text is copied). Once the
  communications record has captured that email for the same student, `answered` in the `suggestions=1` read is the newest
  of the conversation's own instructor message after the student's latest one and any such email sent after it, wherever Gmail
  threaded it, so "Your reply" and "You wrote …" show it (the review reads the record again when `answered` changes). WRITTEN
  AT ONCE (2026-10-08, requirement of Dana Velez Archer's follow-up, whose email showed only after the next Gmail capture
  round, minutes later: "whenever I use that function from the follow up menu, it will always mean that it is related to the
  follow up, so it should be shown"): before that, such an email that Mail accepted (with its Gmail message ID), that went (To,
  Cc or Bcc) to the student's roster address (every spelling the record links by), and that no successful Gmail round has
  started since it was accepted, counts too: `answered` [sent_at, ""] with `answered_mail` {subject, text} read from Mail's own
  send record (nothing copied), shown as "Your reply" at once; an uncertain or refused send never shows, and once a round had its
  chance only the captured copy counts. Recording a new one asks for a Gmail capture round now (Capture now). The
  follow-up's status, revision and suggestions are unchanged, and the email's own thread is not a second follow-up; a newer
  student message comes first again as ever. Since SAME MATTER (2026-10-07) that thread is part of the conversation, the
  student's answer in it included, so a conversation no follow-up tracks yet is answered by such an email as by a reply in its
  own thread. WRITE IN THE THREAD (2026-10-07, requirement's choice): Write to student… on a conversation whose student
  message is an email (not a Canvas notice) writes in that Gmail thread with its "Re:" subject; otherwise a new email as before.
- **SAME MATTER** (2026-10-07, requirement, of Fiona Weaver's "Okay thank you so much!" in the thread their Write to
  student… email started, and of Yara Mercer's new email answering what "This Week" waited on, each a second follow-up: "a new
  reply by the student makes a completely new followup when it really belongs to the existing follow up"; asked, they chose
  that an exact link joins without a model, that Jev joins a confident match with Keep separate under Done for you and asks
  with one click when unsure, among open follow-ups and those resolved in the last 14 days, and that they can join and
  separate conversations themselves). A conversation is read as part of another when (in order) its Gmail thread is one Canvas
  conversation (EMAIL REPLY JOINS); the latest decision about it says so (`student_support_conversation_joins`: `join` by the
  instructor's Join to another follow-up…, their one-click Join, or Jev, JEV SAME MATTER; `separate`, the instructor's Keep
  separate, ends a join and the thread join below; `ask`, Jev's unsure reading, is the review's one-click question; `none`,
  Jev found nothing); or it is a Gmail thread holding an email written from that conversation (WRITTEN FROM A FOLLOW-UP), by
  its exact captured thread. Chains are followed; a cycle joins nothing. Owner: the student's term enrollment; append-only,
  latest decision per conversation; nothing is sent and no message text is copied. Records keep the keys they were saved
  with; every read resolves them (`student_support.service.joins`), including the preparation's source, Jev's triage and the
  Assistant's prompt. Two tracked follow-ups that read one conversation are one: the one saved under its key, else the first
  saved, keeps the status; the other keeps its records and leaves the list, its counts and Needs me, and its waiting cards are
  withdrawn as the conversation changed. The instructor's join reads the conversation that started later as part of the
  earlier one (`POST /api/student-support/followups/join` with `enrollment_id`, `source_key`, `with_key`, `request_id`;
  `POST /api/student-support/followups/separate` with `enrollment_id`, `source_key`, `request_id`; idempotent by request ID;
  another student's conversation, the same conversation, a regrade request and a separate with nothing joined are refused;
  the assistant's policy was NEVER for both until 2026-10-10: CONNECT A MESSAGE below). The
  `suggestions=1` read adds `joins` (the conversations joined by Jev or the
  instructor, each with its first subject) and, on Jev's unsure reading, `match`; other reads stay byte-identical apart from
  the joined follow-ups themselves. SEE WHAT IT MEANS (2026-11-08, requirement: "I want to be able to see what it is
  referring to when suggesting to join"): the one-click question holds a closed "Read “…”" fold with that follow-up's
  captured messages (its own conversation and those joined to it, from the student's record the review already reads), what it
  waited on, its date and notes; those messages then leave "Other conversations". It reads nothing new and changes nothing.
- **CONNECT A MESSAGE** (2026-10-10, requirement, in the Assistant chat on Brenna Lyons's follow-up: "can you connect the
  response card I sent to Brenna to her followup?"; her Quiz 4 booking card's invitation had gone out as an email of its own,
  the follow-up did not show it, and neither the page's Join, which lists only other follow-ups, nor the Assistant, for which
  the join was NEVER, could join it. Then: "joins shouldn't be instructor only"; they chose that the chat joins at once, and
  keeps separate again the same way). An email Faculty sent a student that the communications record captured (a Response
  Card or booking card's invitation sent through the instructor's Gmail, a seat notice, any other email) is one of that
  student's conversations, so SAME MATTER's join connects it: the follow-up then shows it as Your reply, lists it under "Also
  in this follow-up" with Keep separate, and the student's answer in that thread reopens this follow-up instead of starting
  another. The join may name the other conversation by one of its captured messages (`with_message_id`, the message's ID
  in the communications record, linked to that enrollment) instead of `with_key`, never both.
  `POST /api/student-support/followups/join/preview` (`enrollment_id`, `source_key`, `with_key` or `with_message_id`) checks
  exactly what the join checks and writes nothing: `source_key` and `into_key` as the join would save them, and for each
  named conversation its key, first subject, start and the saved follow-up's title and status. Asked in the chat, the
  Assistant reads that preview and records the join at once, and Keep separate likewise (ASSISTANT "CONNECT A MESSAGE");
  either is the instructor's decision in the record (`decided_by` instructor, actor assistant). The follow-up's status and
  next step are unchanged, nothing is sent, and no owner or table is added: the decision is the same enrollment-owned,
  append-only row. An email with no captured copy (sent through the relay, or not read from Gmail yet) names no conversation
  and is refused. A follow-up's own preparation never joins or separates.
- **READABLE REVIEW** (2026-10-04, requirement of Gina Porter's absence review: "this is hard to read because it is a bunch of
  endless text. The clickable options aren't very well distinguished from regular text and there's nothing to draw my
  attention to what is important vs what isn't"). In the follow-up review (both columns, and the Suggested actions section
  wherever it is drawn) small capitals are labels only; every action is a framed button or an underlined sentence-case link,
  and arrow links are sentence case (since made the app's rule on the instructor's "make it the rule everywhere",
  DESIGN-LANGUAGE §1.12); the review's text actions and arrow links are at body size beside its framed buttons. What needs the instructor stands out and the rest steps
  back: the plan leads with the count in bold ("2 steps wait for you.") and one line that nothing happens until they approve;
  a confident evidence contradiction is a warning callout ("Evidence needs review") whose button opens Why the Assistant suggests this;
  each waiting step is its own framed white box (title, page and person, quiet field names over the values, Show the whole
  proposal, then the guess, then its buttons; Ledger, 2026-10-04); an assumption is an "Assumed" note in a dashed orange box
  directly above the step's buttons, with each alternative a framed choice in its own words ("Not right? Prepare again instead with:"; a
  trailing full stop dropped); offered answers to a question are framed choices in their own words; a draft's text is set off
  by rules; a fold's +/− follows its words. Done for you comes after the steps that wait and Approve all (it is news, not a
  decision) and leads the section when nothing waits. Nothing an action submits, no route and no read changes.
  CONVERSATION CARD (2026-10-10, requirement): the conversation/message section sits together in a padded, framed white
  card, distinct from the warm page background. The subject, messages, attachments, earlier conversation and its controls
  stay in that card; its existing scrolling, sticky position and quiet refresh are preserved.
- **STALE SUGGESTIONS** (2026-10-04, usability audit: "The follow-up changed; these proposals no longer apply" left a conversation
  with no way forward when nothing would prepare it again by itself, e.g. a To do follow-up, which is prepared only on request):
  a superseded set the server will not renew (`renew` false) but can prepare (`preparable`) offers "Suggest again for the
  conversation as it is now", the existing Prepare now request; a set that renews by itself says so as before.
- **REDO FROM CHAT** (2026-10-09, requirement, of Jared Zane's follow-up: the chat said it redid the suggestions, but its cards
  stayed in the chat and approving its Waiting on student card withdrew the follow-up's own; "yes, build it"). Asked in the
  Assistant chat to redo or change a follow-up's suggestions, the Assistant prepares them again through the follow-up's own
  preparation (`POST /api/student-support/followups/suggestions/redo`, ASSISTANT "REDO FROM CHAT"), so the new steps replace
  the unapproved ones here, also after the proposals stopped applying, and at once: asking for it in the chat needs no card of
  its own, since every step it prepares still waits here (requirement, 2026-10-10, ASSISTANT "NO CARD FOR A REDO"; said again
  2026-10-12, when the Assistant's tool-use check had still made each one a card: "STILL NO CARD"). Asked for the same change
  to several follow-ups, it makes that request once for each (requirement, 2026-10-12: "no card, build it"; ASSISTANT "SEVERAL
  AT ONCE"), and each follow-up's new steps wait here as after one redo. The
  `suggestions=1` read's `chat` lists the chat's
  cards still waiting for the conversation (a reply in its thread, its tracking, a redo), shown in Suggested actions under
  "N steps from your Assistant chat wait for you" with Approve and Decline; Prepare now, Retry, Ask for changes, an answer and
  a redo replace them. A chat's Waiting on
  student, like a follow-up card's, is approved only after a confirmed reply. Run owners, tables and counts are unchanged.
- **NEEDS ME COUNT** (2026-10-06, requirement: "why does follow up show 3 items on the sidebar but only 2 in the actual page",
  then "fix both"). The sidebar's Follow-up badge, the tab title and Home's total count each follow-up once, as the page's Needs
  me tab does: one that needs attention, or one whose suggestions wait for the instructor (cards waiting or a question; a
  Replied conversation among them). `StudentSupportService.attention()` counts
  `followup` that way through the suggestion service's read-only `waiting` (the same records and shown state as the page's
  `decorate`, without names, descriptions or the calendar). A follow-up whose new message the instructor answered counts by
  its saved status (WAITING ON "ANSWERED", 2026-10-13), mail written seconds ago included, as on the page. A possible
  wellbeing concern always needs attention, so Home's
  "Possible wellbeing concerns" card carries `counted_in: "support:followup"`: it stays on Home with its count and adds
  nothing to the badge, the tab title or Home's total. No route, table or stored record changes.
2026-10-09 (instructor, requested automation review items 1 and 2): a To do follow-up is checked for both a reply still owed
and promised work still owed, against current bounded records. The review distinguishes Work still owed, Reply still owed,
and both; unavailable or ambiguous completion evidence remains for the instructor. Both must support completion before the
existing automatic close, which still requires a qualifying thank-you reply. A follow-up preparation checks proposed replies for contradictions and unsupported promises and
may replace unsent reply/tracking proposals in one bounded corrective turn. Existing record actions, explicit Send, manual
overrides, Undo, source guards and no-repeat rules remain. See ASSISTANT and JEV for owners and thresholds.
