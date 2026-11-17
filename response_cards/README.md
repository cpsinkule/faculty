# Response Cards

Response Cards are independent of email, seating, and document delivery. The
private Faculty navigation provides a visual template builder, a preview,
version freezing, audience selection, issuance, publishing, and a dashboard of
recipient responses. Nothing sends email as a side effect of issuing or publishing.

## Ownership and retained history

* `response_card_templates` belongs to `course_definitions.id`. Editing the
  draft increments its optimistic version; it never edits existing frozen versions.
* `response_card_versions` is immutable. Freezing the same draft revision again
  reuses its version. Later revisions create new numbered versions.
* `response_card_issues` belongs to `course_terms.id`, including custom Other
  terms. It references a course template version, selected sections, and optional
  existing assignment and seating chart. The initial context is retained even
  if a referenced seating chart is later deleted.
* `response_card_recipients` references the permanent
  `(section_id, enrollment_id)` membership. Each membership gets one card; a
  student in two selected sections receives two distinct section-context cards.
  A seating-context issue includes only students seated by that chart in the
  selected sections. Selected students are validated against the selected audience.
* Recipient schema snapshots, submission revisions, remote events, and issue
  settings are immutable. Missing data needed for a template variable blocks the
  entire local issue before anything is published. No values are guessed.

The shared catalog owns all these local records. Every mutation acquires the
roster lifecycle lock before its catalog transaction. Roster payload access uses
`RosterService.files` / the verified term storage manager while holding that lock.
No feature code writes NVMe/HDD payload directories directly. The existing
verified nightly catalog backup includes the records and distribution tokens;
generic `/api/state` and `/api/export` omit them. Course edits cannot remove the
last visible course definition with templates or detach imported memberships.
Archiving preserves access to history.

## Authoring and use

1. Open **Response Cards**, choose a course code, and create a template.
2. Add information, acknowledgments, typed responses, choices, or confirmations.
   Move fields up/down to order them. Conditions can depend on an earlier field.
3. Preview and save the draft, then freeze the version to issue.
4. Choose a current term, sections, and optionally students, an assignment, or a
   seating chart. Set the submission policy and optional deadline/expiry.
5. Issue the cards. Their personalized content is now frozen locally.
6. Publish to the configured public service. Copy each recipient's link from the
   issue dashboard for distribution through the desired channel.
7. Sync responses, inspect immutable attempts and verification results, revoke
   access, or grant one additional submission with **Unlock**.

Policies are `once`, `until_due`, `unlimited`, and `instructor_unlock` (first
submission allowed; subsequent submission needs an explicit unlock). An unlock
never bypasses an expiry or deadline, and does not restore a revoked card.
Due dates are UTC instants displayed in the browser's timezone. DR variables
remain center-local date/time text without conversion.

Response status and delivery status are separate. This version supports direct
link distribution; `not_sent` means Faculty has not sent an email. It does not
assert whether someone copied or independently distributed a link.

### The pages (UX overhaul, 2026-09-26)

- **Response Cards** leads with **Needs attention**: every current term's issues with
  answers flagged for review, invitations whose delivery failed or was not confirmed
  on a still-unopened card, submission receipts not confirmed, and replaced cards whose
  remote revocation is pending (`GET /api/response-cards/attention`, read-only, counts
  and titles only). Each item opens its issue on the matching filter (`?status=needs_review`,
  `?delivery=problem`); `#response-cards?attention` scrolls to the strip. Issued cards
  follow, grouped by exam with one status line and a responded/total rule, then the
  course's templates (those Faculty keeps for seating or the Exams page say so, from the
  read-only `managed` key). Email delivery is one line plus an **Email settings**
  disclosure that `#response-cards?email` opens.
- Responses sync automatically every minute (the deployed timer); the pages say when
  the last sync succeeded (`context().sync.synced_at`) and keep a quiet **Sync now**.
- An issue's title fills in `{{exam.name}}`, `{{assignment.name}}`, `{{course.code}}` and
  `{{exam.room}}` from its own context (additive keys `assignment_title`, `course_code`,
  `term_label`, `room_name`, `exam_event_id`); student variables stay literal. The issue
  page links to its seating chart and exam hub, and its one primary follows the state:
  **Publish N cards**, then **Email N students**, then nothing but the done line.
  Filter chips (with the select kept) count flagged, undelivered, awaiting, responded,
  unpublished and closed cards; a flagged row names the answer that differed, and the
  history marks each checked answer ✓/✗ without its expected value.
- **Resend email** per card uses the existing `POST /cards/<id>/resend` (the server's
  `resend_block` decides); **Copy emails (N awaiting)** only copies addresses; **Download
  answers (CSV)** is built in the page from the loaded issue, spreadsheet-escaped, without
  the typed UIN confirmation, verification results, expected values, links, UINs or
  enrollment IDs.
- Issue counts add `delivery_problem` (the shared `delivery_problem()` rule, also used by the
  exam hub), `queued`, `receipt_problem` and `revocation_pending`.
- The template editor has one primary in a sticky footer: **Save draft**, then **Freeze as
  version N**, then **Issue version N…**. Unsaved edits are protected by the shell's leave guard.

## Seating chart confirmations

Confirming a seating chart (Seating charts → **Confirm seating chart and export
to printing**) offers **Prepare response cards and email each student their
link, or export for Gmail** as a ticked-by-default option in the export dialog; when it is left
ticked the export ends with a review window showing two cards side by side, built
from the managed course templates "Exam seat assignment (Faculty)" and "DR
testing notice (Faculty)". Edits made there are saved back to those templates.
The seat card is issued in seating-chart context (seated students only); the
DR card in assignment context for DR-booked students with a single linked
booking. Both end with a salted-hash UIN confirmation. The window's publish option
is on by default, with the workspace's default email provider selected. Choose
**TAMU Gmail** (shown as **Gmail** for other domains) to send these invitations
directly through the connected account while keeping the workspace default, or
**Gmail mail merge** to publish the cards without queuing invitations and open
the spreadsheet preview after confirmation. Untick publishing to retain ordinary
local issues that you publish and distribute from this page. The API preserves
the existing defaults: `publish_cards: true`, `email_cards: true`,
`mail_provider: "default"`. Direct Gmail uses `mail_provider: "gmail"`; manual
mail merge uses `email_cards: false` while keeping publication enabled.

## Direct Gmail from a confirmed seating chart

**Send with TAMU Gmail** opens a recipient preview without republishing cards or
sending mail. The default **Not emailed yet** audience excludes earlier accepted
or unconfirmed delivery; **All awaiting response** explicitly includes them.
Both audiences exclude completed/inactive cards and active mail jobs, and use
the same exact membership, roster, seat, DR, frozen-card and duplicate-address
checks as mail merge. Each invitation keeps the original generic greeting and
wording, including an actual replacement card's UPDATED notice.

`GET /api/seating/<id>/gmail-send?audience=unsent|pending` returns the connected
sender, recipient/skip lists and a fingerprint, never personal card links.
`POST` to the same route requires `{audience, fingerprint, request_id}` from the
explicit Send action. It revalidates that exact preview and atomically inserts
Gmail mail jobs, marks the cards queued, and retains a receipt in the existing
term-owned chart's `seating_events` (`gmail_send`). Repeating the same request
returns the original receipt even after delivery; reusing its ID for a different
command is rejected. These are existing card-owned mail records and chart-owned
events, with no new owner or spreadsheet. The worker sends the messages, records
receipts, and preserves the chosen Gmail account. Resend remains the default.

## Gmail mail merge from a seating chart

The **Gmail mail merge** button on a confirmed chart downloads an `.xlsx`
workbook for manual sending. It uses existing published cards from that chart's
seat and DR issues. It does not publish cards, send mail, enqueue invitations,
or mark anybody as emailed. Faculty cannot observe a later manual Gmail send;
response-card completion and its normal synchronization still work.

The first sheet, **Recipients**, has text columns `Email`, `FirstName`,
`Course`, `Section`, `Exam`, `ResponseCardLink`, and `Message`. FirstName is
always `Student` for Gmail's required mapping; no Name or LastName column is
exported. Message contains Faculty's complete initial invitation with `Hello,`
and its personal link. Earlier email delivery does not add a second-copy
paragraph; a card that actually replaces an earlier card retains its replacement
notice. **Instructions** supplies the constant subject and
Gmail steps; **Skipped** appears when students were excluded and records why.
Actual student names remain in the instructor preview and Skipped sheet only.
Import into Google Sheets, leave Recipients first, link that sheet in Gmail mail
merge, and map Email/FirstName. Type **@** in Gmail and
select the **Message** chip. Gmail requires an eligible Workspace plan and cannot
merge subject lines or hyperlinked text; the invitation uses a plain URL.
[Google's mail merge guide](https://support.google.com/mail/answer/12921167?hl=en)
documents these requirements.

The default **Not emailed yet** audience excludes Faculty invitations that are
queued, sending, accepted, or unconfirmed. **All awaiting response (including
previously emailed)** permits previously sent or unconfirmed invitations after
instructor review, but still excludes queued/sending work. Neither audience
includes answered, revoked, expired, unpublished, invalid-address, mismatched
scope, stale-detail, unusable-seat, or ambiguous-DR cards. Every eligible card
sharing an email address with another eligible card is excluded until the
duplicate address is resolved. The preview gives the reason for each exclusion.
It cannot detect invitations previously sent through Gmail manually.

`GET /api/seating/<id>/mail-merge?audience=unsent|pending` returns names,
addresses, reasons and a fingerprint, without tokens or personal links.
`GET /api/seating/<id>/mail-merge/download` takes that audience and fingerprint,
revalidates the complete audience, and returns 409 if anything relevant changed
or no cards qualify. Refresh and review again after a 409. Source data is read
under the roster lifecycle lock through verified roster storage. The workbook
is generated in memory from the term-owned chart; it creates no catalog owner,
retained payload, or delivery record.

All cells are explicitly text, including formula-like names. UINs, enrollment
IDs and verification expectations are excluded. Only the downloaded workbook
contains the personal links; keep it private and out of logs or source control.
The current roster, seat, room, exam and exact DR booking are compared with the
frozen card before export. If a date, DR booking, name or address changed,
revoke the old card in **Response Cards**, then reconfirm the chart to prepare
its replacement. A confirmed seat change immediately invalidates the earlier
card and requests deactivation of its public link. Prepare its simplified
replacement through **Send updated seat cards**, or reconfirm without Faculty
email when preparing a manual export. The new card states the updated seat
and asks only for UIN confirmation. Re-export after changes; Faculty cannot update an already downloaded
spreadsheet.
Download a fresh workbook before sending: existing exports retain their earlier
greetings and recipient columns.

## Email

Faculty uses the workspace setting `FACULTY_OUTBOUND_EMAIL_PROVIDER=gmail`
(requirement, 2026-10-16: "change the default to be from my gmail") to send
response-card invitations, submission receipts and reminders through the
connected Gmail account. The setting is in `~/.config/faculty/print-email.env`
for the web app and Inbox worker, and in the
`faculty-response-cards.service.d/gmail.conf` drop-in (with `gmail.env`) for the
minute sync that queues receipts and reminders. If Gmail is disconnected, mail
queued for it waits for a reconnect and never falls back to Resend. Setting
`resend` restores the print-export relay's Resend connection as the default. The relay uses `EMAIL_PROVIDER=resend` and its private
`RESEND_API_KEY`; both sender domains must be verified before activation. See
[`PRINT_EXPORT.md`](../cloudflare/teach-inbox/PRINT_EXPORT.md#resend-outbound-delivery)
for setup and the current activation status. These settings belong to the
workspace; the existing term-owned issues and recipient mail records retain
their owners.

The legacy `auto` setting (the default when unset) uses a connected Gmail account
first, then the relay. Explicit `relay` selects the relay, and `gmail` requires
a connected Gmail account. An unknown provider disables sending. To configure
the optional Gmail connection, set
`FACULTY_GMAIL_CLIENT_FILE` (the OAuth client JSON downloaded from Google Cloud
Console, a "Web application" client whose authorized redirect URI is
`http://127.0.0.1:3000/api/response-cards/gmail/callback`, overridable with
`FACULTY_GMAIL_REDIRECT`) and `FACULTY_GMAIL_TOKEN_FILE` (where Faculty keeps
the connection, created with mode 0600) for both the web app and the Inbox
worker (`~/.config/faculty/gmail.env`), then click **Connect Gmail** on the
Response Cards page and sign in with the Google account to send from. Only the
`gmail.send` and `userinfo.email` scopes are requested; the sign-in uses PKCE
and a stored state, the callback route accepts nothing else, and Faculty never
reads the mailbox. Mail then arrives from "Professor Sinkule <that address>",
with Reply-To only when it differs, and copies land in that account's Sent
folder. Gmail rate or daily limits, an expired or revoked grant, and an
unreachable Google keep the message queued and pause sending (5 or 15 minutes)
rather than failing it; a definite rejection fails that message only.
**Disconnect Gmail** revokes the grant and deletes the token file. Without both
files Gmail is inert and tests never touch it.

When Resend is explicitly selected, a configured Gmail connection still shows
**Connect Gmail** (or the connected account and **Disconnect Gmail**). Connecting
does not change the default. **Invitation delivery** in seating confirmation and
**Send from** in invitation, single-card resend, and test dialogs offer the
connected Gmail account as an optional choice. New dialogs always start with the
workspace default; choosing Gmail applies only to that send. Automatic submission
receipts continue to use the default.

The seating confirmation, issue `/send`, card `/resend`, and `/mail/test` APIs
accept `mail_provider: "default" | "gmail"` (omitted means `"default"`). Invalid
choices and unavailable explicit Gmail are rejected before sending or queuing.
New `response_card_mail` rows retain the effective `provider` and, for Gmail,
`gmail_sender`. These are delivery attributes of the existing recipient mail
record, not a course, term, section, or chart preference. A queued Gmail message
never falls back to the relay or silently changes Gmail accounts. Provider quota
pauses are independent, so Gmail being unavailable does not pause Resend mail.

Relay mail goes through the print-export relay's `/message` route
(configured by `FACULTY_PRINT_EMAIL_URL` and `FACULTY_PRINT_EMAIL_SECRET`;
`FACULTY_MAIL_REPLY_TO` sets the Reply-To, default `instructor@tamu.edu`).
The Response Cards page shows the active provider under **Email delivery**,
with a test-message dialog that sends to the Reply-To address for the default
provider, or to the connected Gmail address when Gmail is selected
(`POST /api/response-cards/mail/test`).
`POST /api/response-cards/issues/<id>/send` queues an invitation for every
published card that has none; each row in `response_card_mail` is sent once by
the Inbox worker and its provider receipt is committed before completion.
`POST /api/response-cards/cards/<id>/resend` queues one more copy of a single
card's invitation at the instructor's request (the seating chart offers it on a
clicked seat while the card is pending): only a published card that is still
awaiting a response, has a roster address, and has no email already waiting
qualifies; a responded, revoked, or expired card, or one whose seat the chart
no longer shows, is refused. The copy keeps the subject and personal link and
says it is a second copy of an earlier email. Nothing is ever resent on its
own. Confirmations stay one per submission (a partial unique index).
`GET /api/response-cards/cards/<id>/link` returns one card's personal link
while it still awaits a response (409 once answered, revoked, or expired); the
seating chart popover uses it to show the link with a Copy button. The
invitation subject is "IMPORTANT: Information about your upcoming <assignment>
for <course code>" and the body asks the student to open their personal link
and complete the card. A quiz makeup booking card (an issue naming a
`makeup_offer_id`) is for a quiz the student already missed: its subject is
"IMPORTANT: Book your <assignment> makeup for <course code>", and the body says
they missed it with an excused absence, asks them to choose when on the card,
and says the card closes when its last time begins. It never mentions the
card's "None of these work" choice. A later booking card for the same student
and quiz ("Send a new card") is "UPDATED: Book your …" and says it offers new
times and replaces the one sent earlier. Invitations and submission receipts use `Hello,` without
a student name in the generated greeting. Receipts still quote the card and
submitted answers; this does not redact names entered in those answers.
Mail arrives from "Professor Sinkule <exam@example.com>"
with Reply-To set to the instructor. A card that replaces an earlier one (a
re-seated student) is sent as "UPDATED: …" and says the earlier link no longer
works. This wording is retained on later resends too. The chart keeps the new
seat orange until its current replacement invitation has an accepted provider
receipt; publishing, queueing, old responses and manual spreadsheet downloads
are not proof of sending. Seat changes never send email on their own.

`seating_card_updates` retains the current notice revision for an existing
chart/membership. Card-owned `response_card_invalidations` retains permanent
invalidation and remote-revoke receipts/retry state in the catalog. The move
transaction also cancels pending old-card envelopes. Remote revocation follows
commit and is retried by the Inbox worker; failures are visible and block the
replacement from distribution. Publication and sending recheck the current
revision while holding the lifecycle lock. Earlier events and submissions
remain retained without making an invalidated link usable again. Interrupted
preparation reuses a per-student revision issue ID. Explicit seat-update sends
retain request receipts in the chart history and create no print batches.

Time answers are shown in twelve-hour form. Every synced submission queues a confirmation email
listing the student's answers, marking flagged ones; the expected values on
record are never included, and a UIN answer is only ever "confirmed". Card
rows show `delivery_status` (`queued`, `sent`, `failed`, `unconfirmed`,
`no_email`, `cancelled`) and a `mail` history. A documented provider rate/quota rejection is
returned by the relay as HTTP 429 with exactly `error: "rate_limited"` and a
bounded `retry_after_seconds`, plus a matching `Retry-After` header. Only this
validated response keeps the message pending and saves `not_before` on the
existing mail record. The whole pending queue waits until that deadline, even
after a restart or a new arrival. No roster fields are changed. Unknown responses
retain their failed/unconfirmed behavior. Nothing is retried automatically after a
failed or unconfirmed delivery.

## Schemas and variables

`schema.py` defines bounded, explicit fields and variables. No HTML, expressions,
custom code, regex evaluators, file uploads, or handwritten signatures run inside
a template. The visual and public renderers escape text.

Initial types: static information, acknowledgment, short text, long text,
confirmation, single select, multiple select, radio, boolean, integer, decimal,
date, time, and typed attestation. A typed attestation is a text response with
the statement as its label and the submission timestamp as its receipt; it is
not a verified identity or handwritten signature.

Expected-value modes are `exact`, `case-insensitive`, `normalized` (Unicode NFKC,
trim, collapsed whitespace, lowercase), `compact` (NFKC, lowercase, letters and
digits only, for seat and room codes), `numeric`, `one-of`, and `uin-digest`
(the answer's digits are hashed with the salt in the expected `salt:hex` value,
produced by `{{student.uin | uin_digest}}`; the UIN itself is never stored). A mismatch can
block, flag for review (the default), or be accepted. Required visible responses
are enforced on the server. Hidden answers are discarded; hidden required fields
do not prevent submission. Verification expectations never appear in the public
schema or submission history.

The allowlist includes student names/email/section, course code/name/term/year,
assignment/exam name, seat label/row/column/room, the exam date/time recorded on
a seating chart (`exam.date_long` spells the date out, e.g. "Friday, September
25, 2026"), and the uniquely linked DR booking's date/time/campus. `student.uin` is available only with the explicit
`last4` filter; a full UIN is forbidden. Raw UINs and enrollment IDs are never
part of the public API payload. `seat.number` is the seat's full label (e.g. C17).
Ambiguous or missing DR context is not guessed. Only variables actually used in
the card are published; unrelated accommodation/roster data stays in Faculty.

A quiz makeup booking card (absences/makeup_booking.py, ABSENCES "MAKEUP BOOKING")
is an issue for one student that names their makeup offer (`makeup_offer_id`):
`ResponseCardService.makeup_booking.issue_context` supplies `makeup.given` and
`makeup.due` (spelled out, "Friday, October 2") and the offer's times, which
`schema.with_choices` puts in place of the template's `makeup_when` options before
personalization. Only a choice field's options can be replaced, and a condition on
that field must still name one of them; the result is the card's exact snapshot.
The course template "Quiz makeup booking (Faculty)" is made the first time a card
is needed and stays an ordinary template.

## Public service and configuration

The separate [Worker](../cloudflare/response-cards/README.md) owns the student
form, published snapshots, hashed access tokens, drafts, and immutable submissions
in D1. It serves independently of Faculty being online. Faculty makes outbound
authenticated calls only; the public email-only listener and private app routing
are unchanged.

Configure Faculty with:

```
FACULTY_RESPONSE_CARDS_URL=https://respond.example.com
FACULTY_RESPONSE_CARDS_SECRET=<same high-entropy value as FACULTY_API_SECRET>
```

Store these in the existing private configuration directory, for example
`~/.config/faculty/response-cards.env`, mode 0600. Add an `EnvironmentFile` entry
for it to the existing `faculty.service` user override and to `faculty-inbox.service`
(the Inbox worker publishes quiz makeup booking cards and sends card email:
`deploy/faculty-inbox.service.d/response-cards.conf`), then restart both.
Never put either secret or live magic links in source control or command logs.
The public origin becomes bound to the local synchronization cursor on first use;
switching to another deployment requires an explicit migration, not an env edit.
The client identifies itself as `Faculty-Response-Cards/1.0`, consistent with the
existing print-export client; Cloudflare's browser-integrity check rejects the
generic Python urllib user agent before the Worker receives the request.

The catalog retains the original random distribution token so Faculty can show
the same link after a restart. D1 receives only its SHA-256 hash. Links are bearer
access to a card, not proof of student identity. Keep local catalogs/backups and
links private. No third-party analytics or email-open pixels are used.

Publish retries use the exact retained snapshot/card ID. Submission retries use
a request ID plus answer hash; immutable revisions are never overwritten. Revoke
and unlock operations also carry retained request IDs. Faculty validates a sync
page and commits its events, submissions, recipient status, and cursor together.
A changed historical event/submission stops synchronization rather than replacing
the retained record. Network errors preserve local data and permit explicit retry.

## Reconciliation and operations

Use **Sync responses** on the dashboard or run:

```sh
.venv/bin/python manage_response_cards.py sync
```

For automatic recovery after Faculty downtime, install the user unit and timer
from `deploy/faculty-response-cards.service` and `.timer`, which load the private
environment file and reconcile once a minute. The bounded reconciler drains up
to 20 pages of 100 events per run; any remaining pages drain on the next run.
This polling path does not require opening another inbound listener. Push event
notifications, arbitrary dynamic audiences, file uploads, SSO, and LMS
integration are future adapters, not implemented processors.

The normal verified nightly catalog backup is the local recovery point. D1 is
the public source for newer events and drafts; replay the cursor after local
restoration. Events for cards missing from a restored local catalog stop sync
for recovery; the reconciler never guesses a recipient or skips unknown cards.
Recover the newer catalog before reconciling those events. Do not replace the
D1 database or truncate its event sequence while
cards are live. Recreate the Worker from the pinned source and retain its D1
binding and secrets. Cloud deployment and real-widget validation are separate
from local test success.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_response_card*.py'
.venv/bin/python -m unittest discover -s tests -p 'test_seating_mail_merge.py'
.venv/bin/python tests/browser_response_cards.py
.venv/bin/python tests/browser_seating_mail_merge.py
cd cloudflare/response-cards
npm ci
npm run types
npm run check
npm test
npm run build
```

The backend fixture covers ownership, immutable versions, exact audience/context
resolution, retry after a lost publish receipt, transactional reconciliation,
export exclusion, same-origin checks, and retention. The private browser check
exercises authoring, preview, freezing, issuance, and dashboard behavior. Worker
checks exercise D1 policy enforcement, Turnstile validation, drafts, expected
answers, status events, history, expiry, and access controls.
