# Student follow-through

## Action desk and commitments

The Action desk and Tasks views share the same enrollment-owned follow-ups and existing actions. Tasks opens a selected
task in Action desk, where its conversation remains visible. Every view, including Office hours, shares the same ordered
Action desk / Tasks / Office hours navigation. The two follow-up views switch in place; from Office hours, either opens
Follow-up at the chosen view. The same navigation remains available while loading or after a failed read.
`commitments.py` validates the optional `commitments` array in a follow-up's immutable content revisions; no separate
catalog table or migration is needed. Each task has a stable id, title, kind (`work`, `reply`, `check_back`), state
(`open`, `completed`, `cancelled`), `waiting_on` (`you`, `student`, `date`), optional `check_back`, `depends_on` ids,
`source_message_id`, `action_id`, `blocks_reply`, and `completion_note`. Save through the ordinary revision-checked
student-support POST. Omission preserves existing tasks; cancellation retains them. The projection adds `effective_state`
for a confirmed linked receipt. Manual completion/cancellation needs a note; linked work cannot claim completion without
its receipt. Dependencies are acyclic and belong to the same follow-up. Explicit reply prerequisites are checked before
the follow-up's Mail send. Unfinished work survives a reply and prevents resolving the follow-up. Dates are recorded
check backs; an explicit Watch until done remains the way to prepare a later review automatically.

## Remembered from your corrections

`assistant/followup_learning.py` finds the instructor's confirmed corrections of a follow-up's cards and hands each to the
Assistant's memory (ASSISTANT-MEMORY "LEARNED FROM AN EDIT", 2026-10-12): a reply edited on its card or sent from Mail in a
draft's place, a Decision changed on an absence card, a round prepared again at their request. `discover()` gives each one
a case in `student_support_learning_cases` (references only; no words are copied), `comparison()` reads what changed with
names, the draft's numbers, grades, dates and private notes left out, and `AssistantMemory.learn` has the Assistant's own
model write the rule the change shows, checks it and keeps it with Undo. A case's last event says what came of it
(`remembered`, `known`, `nothing`, `failed`, `interrupted`); it is considered once.

The follow-up dispatcher takes the look (`turn`): at once after an approval, else once a minute. It needs no engine and
holds no preparation up. `FACULTY_LEARNING_ENABLED=0` turns it off; nothing confirmed before `LEARNS_FROM` is learned from.
The `suggestions=1` read says what was kept on the follow-up it came from (`suggestions.learned`) and while a correction is
being considered (`suggestion_settings.remembering`); default list and policy responses are unchanged.

The lesson review these tables were made for (Term policies → Learn from your feedback, 2026-10-08) was withdrawn on
2026-10-12: its routes, analysis, evaluation CLI and provider variables are gone; its rows stay as they are.

Follow-up memory scope comes from the run's enrollment, including when two terms are current. Opening context records
memory IDs and exact revisions in `assistant_draft_memories` (ASSISTANT-MEMORY "DRAFT REFERENCES"), owned through that run;
no draft text is copied. Exposure and instructor-edit counts describe observations, not rule effectiveness.
`assistant/outcomes.py` reads confirmation from the route and its local receipts for learning, past work and metrics;
an action that merely started work does not count as complete, and is not a problem either: what follows it waits, and
nothing is stopped or flagged for it (ASSISTANT "STILL RUNNING IS NOT A PROBLEM").

A COORDINATOR'S FOLLOW-UP (2026-11-14, COORDINATOR, TEACHING-WORKFLOWS): a conversation with a coordinated term's
coordinator is a follow-up too, owned by the course TERM. Its records carry the owner key `coordinator:<term id>` in the
column that holds a student's enrollment ID (`course_coordinators.service.owner_key`; migration `018_coordinator_owner`
makes `student_support_items`, the suggestion runs, mutes and written mail again so they accept it, run with
`REBUILDS` under `migration_transaction(rebuild=...)`). `_owner` reads either kind of owner, `_coordinator_people` names
the coordinator where `people()` names a student, and `correspondent_role` counts the coordinator's messages as the ones
the conversation is with. Suggested only for mail that came after the course card named them. Appointments, joins,
watches, wellbeing, the conversation analysis and the closing rules stay a student's alone.

`service.py` owns enrollment-scoped follow-up and appointment revisions. The
communications queue is a read of captured message metadata; it performs no network
access. `calendar_events` projects appointments without student details. The
contract is [TEACHING-WORKFLOWS](../docs/decisions/teaching-workflows.md).

`settled_mail` answers the Mail page's Students list (MAIL-FOLLOW-UP): which student messages are part of a tracked follow-up
that shows anything but Needs my reply. It reads only the students asked about who have a follow-up, and saves nothing.

ANSWERED (2026-10-13): `effective` reopens a tracked follow-up for a newer student message only while the instructor has
not written after it (`latest_reply`: the conversation's own latest message, or mail written from the follow-up, read on
every listing by `_written`). Otherwise the item keeps its saved state and carries `new_reply_answered`; nothing is saved.
`attention()` passes the not-yet-captured written mail (`_unconfirmed`) so the badge agrees with the page.

`/api/student-support` serves the lists and explicit revision-checked writes.
Student names come from the existing verified roster picker only on student-facing
views; Home uses the catalog-only attention projection. Request fingerprints make
retries safe and reject reuse with different contents. Catalog backups retain all
records; the generic workspace export contains none of them.

`POST /api/student-support/followups/start-now/preview` previews a fixed `through`
instant. `POST /api/student-support/followups/start-now` requires that preview's
fingerprint, an exact request ID and `confirmation: "start-now"`. Its workspace-owned
baseline suppresses pre-cutoff messages even if a later capture first discovers them.
Tracked follow-ups through the cutoff are resolved by revision; newer activity stays.

`triage.py` takes a bounded worker turn for one new/current captured conversation
through the workspace Jev adapter. Results are pinned to exact source revisions.
FYI suggestions remain inspectable in the FYI/All filters; saved instructor states
are authoritative. Request shortcuts only open the existing owning controls.
Failed/uncertain checks stay visible and require an explicit revision-checked retry
at `POST /api/jev/triage/retry`; its assistant tier remains Approve. JEV HOST FAILURES
(2026-10-09): after a host-level failure the worker pauses (5 to 60 minutes) instead of failing
every new conversation; a host refusal before any answer is asked again after the pause, and
`POST /api/jev/triage/retry-failed` (instructor-only, NEVER) is Retry all failed.

WELLBEING OFF (2026-10-12, requirement: "Off entirely"; JEV "WELLBEING OFF"):
`triage.WELLBEING` is a gate no probability reaches, so no reading is a concern today. The
question is still asked in the same request, so kept readings stay valid, and urgency is
unchanged. What follows (pinned first, never FYI, counted on Home, Mark reviewed, REVIEWED
HOLDS, CARRIED) describes the check as it was, and as the .30 gate would restore it. The
reviewed route and its recorded reviews are kept.

The same decision (rule `student-triage-v2`) rates urgency and asks about a possible
wellbeing concern, with dates worked out in code relative to the message's send time.
An open concern sorts first, is never FYI and counts on Home; `POST
/api/student-support/wellbeing/reviewed` (instructor-only) records Mark reviewed for
that exact analysis in `student_support_wellbeing_reviews` (migration 003). REVIEWED
HOLDS: it also covers later analyses of the conversation until the student writes again
(a message captured, edited or linked after the reviewed analysis), so a changed due
date or exam in the student's week never brings the concern back. CARRIED (2026-10-09): the
newest flagging reading of the student's newest message, from any model, keeps an unreviewed
concern raised through a provider switch, an outage or a re-read (`StudentTriage.flagged`).

## Automatic follow-up suggestions

The 2026-10-08 completion rules in **ASSISTANT** take precedence over the earlier
mail-first descriptions below. Review and execution put record changes first,
messages second, and tracking last. Failed or uncertain work remains in Needs me,
including work on a resolved follow-up. Waiting on student requires a confirmed
reply to the student's verified address after the latest student message.

`014_assistant_workflows.sql` adds enrollment-owned context observations and
one-shot watches with immutable receipts; `015_watch_identity.sql` adds identity
protection and the due-watch index without changing the recorded migration.
`assistant/student_context.py` provides
`GET /api/student-support/students/<enrollment_id>/context?source_key=…`: the
selected conversation, other messages, current policies, private notes, that
student's grades, commitments, and assessment/meeting facts. `student_context`,
`draft_student_reply` and `track_followup` use the same projection through normal
Faculty routes. Before a follow-up approval, source fingerprints check policies,
notes, absences/documents, grades/due dates, accommodations, papers, schedules,
calendar availability and communications. A changed source requires Retry
suggestions and fresh review. Exact writes from the current plan are accounted
for without masking subsequent instructor changes.
Drafts prepared before these context observations existed need one fresh Retry;
their old source context cannot be verified after the fact.

**Watch until done** appears in the follow-up review and Assistant Changes. The
instructor chooses a check-back date, absence, assessment paper, or gradebook
column, reviews the preview, then saves. Routes under `/api/student-support`:

- `GET /followups/watch-options/<enrollment_id>` lists owned target records.
- `POST /followups/watches/preview` checks an open current follow-up and returns
  its exact fingerprint; it writes nothing.
- `POST /followups/watches` creates the reviewed watch with a request ID. The
  assistant's `watch_followup` tool always produces an Approve card.
- `POST /followups/watches/<id>` pauses, resumes or removes a watch against its
  displayed revision. This is an instructor-only assistant policy route.

Each worker turn checks at most one due watch, at two-minute intervals, while
ordinary queue discovery continues. The active/paused/error watch limit is 50;
check-back dates are at most 180 days away. A trigger prepares one fresh review
and records its run ID. It never sends, closes tracking, or retries failed or
uncertain work. Removed/paused watches and the global pause prevent execution;
an observation failure needs an explicit Resume. A closed/non-current follow-up,
muted preparation, unavailable model or existing failed work leaves a visible
receipt instead of preparing. Dismiss removes the watch from the view while
retaining its receipts.

The Term policies window's `areas=1` read includes suggested guidance from
repeated edits. `assistant/followup_guidance.py` reads at most 300 retained email
edits in a stable order and proposes at most eight repeated replacements used
for different students in the same term. Suggestions show the old/new wording
and keep source action references. Review as guidance puts editable text in
the selected policy area; only Save policies adopts it through the existing
revision-checked route. Reading or dismissing the window saves nothing.

`assistant/followups.py` owns generation, source checks and student/capability bounds. The fair Inbox worker discovers one
eligible source per turn; the web-only dispatcher runs the selected Assistant engine/reasoning, one background turn at a time,
giving interactive answers priority. Its exclusive storage-manager lock prevents multiple web dispatchers; an interrupted
claim requires an explicit retry. It participates in the normal Assistant busy/restart gate.

Migration `004_suggestions.sql` is enrollment-owned catalog metadata: exact source IDs/revisions/digests and tracking cutoff,
append-only run events, bounded explanations, clarification state, instructor feedback and existing Assistant action IDs.
No automatic transcript or copied message bodies are retained. Existing communications and receipts stay with their owners.
The service initialization applies this recorded migration under the lifecycle lock in the existing migration transaction.

Reads opt in with `GET /api/student-support?kind=followup&suggestions=1`; defaults remain byte-identical. The Suggested actions
area uses the shared Assistant cards and approval handlers also used by Changes. Every write needs approval, including
normally automatic chat routes; Send is explicit and uses Mail's exact preview/advisory checks. The server restricts proposals
(WIDE SCOPE, 2026-10-03) to anything the assistant may prepare, for any student or a whole class, a code change included,
each an approval card; deliveries reach only the student, their sections' TAs and the instructor, never whole sections.
Approval and batch preflight recheck the source. Changed sources expire pending cards and permit fresh eligible preparation.
ONE ROUND (2026-10-04): a preparation proposes everything at once; nothing is prepared from approved cards' receipts. Work
that needs another card's new record goes in that card's own request (an absence's `documents`, a new sitting's `participants`);
Waiting on student is proposed beside the reply card and submitted only after its confirmed send (Approve all finishes prerequisites, sends mail, then updates tracking).
WAITING ON (2026-10-04): a follow-up's revision may carry `next` (one line, at most 200 characters, kept only when given), and
`todo` ("To do") is an action the instructor still owes after replying; both feed the "Waiting on …" line at the top of the
review and in the queue. The `suggestions=1` read adds `answered` (the instructor's message after the student's latest one),
shown as "Your reply" under the student's message. WRITTEN FROM A FOLLOW-UP (2026-10-07): an email sent with a conversation's
Write to student… is recorded with it (`POST /api/student-support/followups/written`, table `student_support_conversation_mail`,
enrollment-owned, append-only) and counts for `answered` once captured for that student, in whichever Gmail thread it went;
WRITTEN AT ONCE (2026-10-08): until a Gmail round could capture it, an accepted send to the student's roster address counts
from Mail's send record (`answered` [sent_at, ""] plus `answered_mail`), and recording it asks for a capture round now. The Assistant's tracking change after a reply is never Needs my reply and
gives `next`; To do is prepared only on request.
WHAT YOU DID (2026-10-13): the `suggestions=1` read also adds `updated_by` (the actor of the follow-up's latest revision;
an item's `actor` is whoever created it) and, from the suggestion service, `suggestions.approved` {at, count}: the steps the
instructor approved in the newest of the follow-up's preparations in which they approved one. The queue's "Your to-dos" rows
read them ("You approved 2 steps · Oct 12", "You updated it Oct 12").
SAME MATTER (2026-10-07): `service.joins` reads a conversation as part of another when its Gmail thread is one Canvas
conversation, when the latest decision in `student_support_conversation_joins` (enrollment-owned, append-only) joins it, or when
its Gmail thread holds an email written from that conversation; every read resolves stored keys through it. Two tracked
follow-ups of one conversation are one (the one saved under its key, else the first saved). The instructor's
`POST /api/student-support/followups/join` (`enrollment_id`, `source_key`, `with_key`, `request_id`; the later conversation joins
the earlier) and `/followups/separate` (`enrollment_id`, `source_key`, `request_id`).
CONNECT A MESSAGE (2026-10-10): the join also takes `with_message_id` in place of `with_key`, one captured message of that
student (an email Faculty sent them, such as a booking card's invitation), and joins its conversation; `/followups/join/preview`
(same fields, no request ID) checks the same and writes nothing. Asked in the Assistant chat, `join_followup` reads the preview
and joins at once, and `separate_followup` keeps a joined conversation separate again (assistant tier `auto`, by the
instructor's choice); `student_context` lists a conversation's `joined` parts. A follow-up preparation does neither.
SEVERAL AT ONCE (2026-10-12, ASSISTANT): asked in the chat for the same change to several follow-ups, the Assistant reads
`open_followups` (this list with `suggestions=1`, in brief, resolved ones left out) and `redo_followups` makes
`/followups/suggestions/redo` once for each follow-up it names, with the same `text`; no route was added.
`matching.SameMatter` (Jev, on the triage's worker turn before analysis) asks once per new conversation that has earlier open or
recently resolved follow-ups: a confident match joins (Done for you, Keep separate), an unsure one is the review's one-click
`match`, none records nothing found. A thanks leaves a Resolved follow-up resolved and a To do as To do (Jev's strong
acknowledgment reading with no wellbeing concern, `StudentTriage.acknowledged`; `thanks`); on a To do answered since it was set, Jev's TO DO THANKS question resolves it when nothing more is owed, or the
review offers Finished? Mark resolved. Write to student… on an email conversation writes in its Gmail thread.
Uncertain sends never imply success or cause resending.

Instructor-only POST controls at `/api/student-support/followups/suggestions/<run_id>/revise`, `/answer` and `/retry` require
`request_id`, `seen` (latest run event) and `fingerprint` (source digest). Revision supplies `text`; answer supplies `question_id`
and an `answers` object keyed by question ID. They create replacement attempts, supersede only unsubmitted proposals and keep
all completed receipts. Answers supply information, never approval. Failed/interrupted/declined/expired/uncertain states do
not retry automatically. Missing configuration links to Compute settings. See CHECKS.md for synthetic coverage and rollout.

The Assistant's Changes tab reads the same opt-in follow-up projection for revision, retry and clarification controls. Both
locations use `assistant-workspace.js`'s shared revision form and request-ID handler; Changes patches existing cards/forms
in place. Each location has distinct clarification input IDs, even while the same question is displayed in both.

### Own writes and the instructor's controls (2026-10-03)

A run's source (v2) leaves out what its own approved cards did: its reply once captured (matched by Gmail message ID, or by text
for Canvas posts and unconfirmed sends) and its own tracking revisions. Sending the reply or approving a tracking change never
withdraws the other cards; a conversation the own reply answered stays listed as `replied` in the `suggestions=1` read while
cards wait. Runs recorded before this have no `v` and keep their original comparison.

Migration `005_suggestion_controls.sql` adds append-only, request-ID-checked controls, each an instructor-only POST under
`/api/student-support/followups/suggestions/`: `<run_id>/approve` (`seen` per card: the run's cards in order, preflighted
and stopped at the first problem or unconfirmed send), `<run_id>/replace` (`action_id`, `seen`, `send_request_id`: an edited
version sent from Mail in a mail card's place), `<run_id>/note` (`action_id`, `text`, optional `remember`), `prepare`
(`enrollment_id`, `item_key`), `mute` (`enrollment_id`, `item_key`, `muted`) and `pause` (`paused`, workspace-owned).
`revise` also takes `remember`; Remember saves an ordinary Assistant preference. Dispatch order: Prepare now, then the
instructor's own requests (and receipt continuations recorded before ONE ROUND), then the automatic queue in the list's order. Automatic preparation
of a conversation the student wrote last first waits (at most 10 minutes) for Jev's analysis of that exact state, so a
thank-you that becomes FYI never costs an Assistant turn; Prepare now and the instructor's own requests never wait.

Term policies (migration `006_term_policies.sql`, term-owned): `GET /api/student-support/policies` lists each current term
with its latest text; `POST /api/student-support/policies/<term_id>` (`content`, `base_revision`, `request_id`) records the
next revision. Each preparation carries the text and records `policy_revision`; the `suggestions=1` read marks
`policies_changed` on suggestions prepared under an older revision. `static/followup-suggestions.js`
draws the section and handles its controls in both review locations.

Retry suggestions is available on every ready, needs-input, failed, interrupted or settled preparation, including older
ones without a recorded policy revision. It uses the existing instructor-only retry route and latest saved term guidance;
queued, running and superseded preparations cannot retry. Unsubmitted proposals are replaced; completed receipts remain.
The retry prompt explicitly requests fresh preparation with current sources, latest policies and retained guidance, with no
new revision notes required. The model's receipt projection recognizes `replaced_by_request` on old/new action events as
supersession rather than instructor rejection. New replacement events describe the operation accurately; history stays intact.

FOLLOW-UP CLARITY (2026-10-03): in the `suggestions=1` read each waiting card carries `effect`, from
`assistant/followup_effects.py`: `title`, `where`/`href` (owning page), `own` and `who` (whom it is for), `changes` (label, value
and, for a revision, `before`), `notes` (what follows), `audience` (who is told), `button`, and `sends` for deliveries. It reads
the card's exact request and the records it names as they are now (absence and follow-up revisions, section numbers, exam
titles, roster names) and writes nothing; a request that no longer routes gets no `effect`.

EDIT IN PLACE (2026-10-04, `008_suggestion_edits.sql`, enrollment-owned through the run): instructor-only
`POST /api/student-support/followups/suggestions/<run_id>/edit` with `action_id`, `seen`, `subject`, `body` and `request_id`
saves the instructor's version of a waiting mail card as a new waiting card of the same run (recipients and attachments kept)
and declines the old card as `replaced_by_edit`; the `suggestions=1` read shows the new card (`edited: true`) in the old one's
place and leaves the old one out. Nothing is sent; the new card waits for Send. The same request ID returns the same edit.

DONE FOR YOU (2026-10-04, `009_automatic_undos.sql`, enrollment-owned through the run): a dispatcher preparation records
a pending absence (with this conversation's files), attaches the student's files to their absence and schedules an office-hours
appointment by itself within the bounds in ASSISTANT; the `suggestions=1` read lists them as `done` and leaves them out of the
cards. Instructor-only `POST /api/student-support/followups/suggestions/<run_id>/undo` (`action_id`, `seen`, `request_id`)
withdraws, removes or cancels one through its owning service as the instructor; `FollowupSuggestions.automatic` (on) is the
dispatcher's switch, turned off by synthetic checks that do not test it. Jev double-checks an automatic absence or meeting against
the student's messages (rule `followup-record-check-v6`: the days matched in code, a week only for an absence that covers it, every class day inside it checked, each confirmed as a day the student misses), and a Waiting-on-student follow-up the student only thanked for is
resolved by itself on the worker's turn (THANKS CLOSES, JEV), both listed under Done for you; the page's Show filter has
"Done for you". So is one waiting on documentation for an absence its conversation recorded once that absence is Not excused
automatically (DEADLINE CLOSES, TEACHING-WORKFLOWS WAITING ON), and one whose awaited record came in, Jev reading strongly that a
paper back in a scan, a Response Card, an absence decided or a meeting passed is what it waited for (EVIDENCE CLOSES,
`assistant/followup_evidence.py`). A To do resolves the same way once the absence it asks to decide is decided, wherever that was
done (DECISION CLOSES). Every other Waiting on student follow-up comes back to Needs me on its check-back day (CHECK
BACK, `service.check_back`: the day after the latest day its next step names, else a week after it was set; `check_back` on the
item, a view), with Resolved, Wait a week and what came in since (`suggestions.arrived`, the `suggestions=1` read only).

The Follow-up page's list read adds `brief=1`: a resolved follow-up with settled suggestions comes without its drafts and evidence
(`suggestions.brief`), and opening it reads `enrollment_id` (RESOLVED WHEN OPENED, TEACHING-WORKFLOWS).

Per-area term policies (`007_policy_areas.sql`) belong to the same course term and policy revision. The opt-in
`GET /api/student-support/policies?areas=1` adds `areas` to each term and returns the editor's area/mode definitions;
ordinary reads are unchanged. POST also accepts `areas: {area_id: {mode, guidance}}` instead of `content`. Modes are
`unset` (empty notes only; omitted areas are unset), `guidance` (nonempty notes) and `ask` (optional notes). The shared
`policy_areas.py` validates them and generates the bounded text read by existing preparation code. Mode-only changes
are revisioned even if the projection is identical. Older text appears verbatim in Additional guidance; legacy POST
replaces all guidance with its text, leaving no hidden area values. The frontend keeps one draft per term and shows one
area's controls at a time, preserving typed changes while switching areas/terms or waiting for Save. Save stays in the
dialog and applies only to the selected term. Schema additions run through the existing lifecycle-locked migration path.
