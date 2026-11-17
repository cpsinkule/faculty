## Response Cards

Rule ID: **RESPONSE-CARDS**. Current binding module contract. Read root `AGENTS.md` first.

- `response_cards` owns reusable templates at `course_definitions.id`, immutable
  frozen versions, term-owned issues, and recipient cards referencing permanent
  `(section_id,enrollment_id)` memberships. Never attach these to `courses.id`.
  Seating and assignments provide optional issuance context, not resource owners.
- Issue all recipient snapshots atomically before publication. Retries retain
  card IDs, random access tokens, and exact snapshots. Editing a template never
  edits issued paper or cards. Memberships in two selected sections receive
  separate section-context cards. Seating audiences include only seated members.
- The separate `cloudflare/response-cards` Worker/D1 serves public snapshots,
  drafts and immutable submissions. Faculty makes outbound-only authenticated
  calls; never expose port 3000 or add student routes to the email listener.
  D1 stores only access-token hashes. Local distribution tokens are private
  catalog data excluded from generic exports; verified catalog backups retain them.
- Public submissions validate Turnstile server-side with exact action/hostname,
  enforce the frozen schema and submission policy atomically, and retain immutable
  attempts. Expected answers are never returned in public schemas. Typed names and
  magic links do not establish identity. Card email may be automatic (instructor,
  2026-10-01: "response cards are allowed to be auto"): the flow that publishes
  queues the invitations (`mail.queue_invitations`; `publish` itself sends nothing),
  and each submission gets a receipt (seating.md).
- ISSUE CHOICES (2026-10-04, ABSENCES "MAKEUP BOOKING"): an issue with an exam
  record may name a quiz makeup offer (`makeup_offer_id`, part of its request).
  Each card then carries that offer's times as the option list of the template's
  choice field (`schema.with_choices`: only a choice field's options change, and
  every condition on it must still name one of them) and the variables
  `makeup.given` and `makeup.due`, baked into its exact snapshot like any other
  value. The offer must be for that card's own student. The Worker is unchanged:
  every card already carries its own schema.
  BOOKING INVITATION (requirement, 2026-10-06, of the subject "Information about
  your upcoming Quiz 4" on a card for a quiz already missed: "yes, fix the email
  wording for booking cards"): such a card's invitation is "IMPORTANT: Book your
  <assignment> makeup for <course code>", says the quiz was missed with an
  excused absence and that the card closes when its last time begins, and never
  mentions the "None of these work" choice. A later booking card for the same
  student and quiz is "UPDATED: …" and says it replaces the one sent earlier.
  Receipts and every other card keep their wording. A booking card's invitation always goes
  through the instructor's connected Gmail (requirement, 2026-10-06: "send booking
  cards through gmail"; ABSENCES MAKEUP BOOKING GMAIL); other cards keep the
  workspace default, which is the connected Gmail (requirement, 2026-10-16: "change the default to be from my gmail";
  every card mail, receipts and reminders included, is well under Gmail's daily limit). An invitation the communications record captured from Gmail can be joined to the student's follow-up
  (TEACHING-WORKFLOWS "CONNECT A MESSAGE", 2026-10-10); the card, its issue and its mail record are unchanged by that.
- SEATING CARDS CLOSE AFTER THE EXAM (requirement, 2026-10-15: "Seating chart response cards should be revoked at midnight
  after the exam has occurred CST"): from 12:00 am campus time (America/Chicago) the day after a seating chart's exam date,
  every card issued from that chart (`response_card_issues.seating_chart_id`, seat and seat-update cards) is revoked: an
  invalidation with reason `exam_over` (`close_finished_exams`), its pending mail cancelled, then its public link deactivated
  by the idempotent revoke that seat changes use, retried with back-off. The minute timer (`manage_response_cards.py sync`)
  runs it. A chart with no exam date never closes.
  DR NOTICES CLOSE TOO (requirement, 2026-11-01, asked whether DR notice cards close like seat cards: "yes, add that"): a DR testing
  notice, issued without a chart, closes the same way from 12:00 am campus time the day after its exam day as "NO FLAGS AFTER THE
  EXAM" reads it (the later of the DR exam date the card states and its assessment's chart dates). It keeps its status as below.
  THEY KEEP THEIR STATUS (requirement, 2026-10-23: "My goal was to have the cards preserve the status at the time they were revoked
  so I could see who responded and who didn't. … if the card is revoked early (ie before midnight on the exam day), then it should
  show as red"): the seating chart, its counts, its list row and Home's seat-update count read a card closed this way as it stood
  when it closed (`closed_statuses`: its current submission's settled status, else "No response", neither red), never as revoked;
  its stored status stays `revoked`, so its link stays closed. A card revoked before (a seat change, a Revoke) stays red, and the
  close leaves an already revoked card as it is.
- NO FLAGS AFTER THE EXAM (requirement, 2026-10-23: "there shouldn't be flagged response card notifications after the exam has
  occurred and the response cards have been revoked"): from 12:00 am campus time the day after a card's exam, its flagged answers
  leave Home's "Flagged card answers" and the Response Cards attention strip (`exam_over`). Its exam day is the latest of its
  chart's exam date (the chart it came from, else every chart of its assessment and term) and, on a DR notice, the DR exam date
  the card states; a card with neither keeps notifying. The card, its status and the issue page's Flagged filter are unchanged.
- REMINDERS BEFORE THE EXAM (requirement, 2026-10-15: "I want pending response cards to automatically send reminders 3 days,
  2 days, and 1 day before the exam"): from 9:00 am campus time three, two and one days before a seating chart's exam date, every
  card issued from that chart that still awaits a response, whose invitation was sent (and more than 12 hours earlier), gets one
  reminder email (`mail.queue_reminders`, kind `reminder` keyed by its days-before number, migration `003_reminders`), through the
  transport its invitation used while it is still connected. The minute timer queues them and the Inbox worker sends them; a
  reminder whose card was answered or closed before sending is cancelled, a day missed entirely is not made up, and an uncertain
  reminder is never resent. DR notice cards and cards not issued from a chart get no reminders.
- DR LOCATION IS NOT CHECKED (requirement, 2026-10-18: "the DR center changes the location so frequently that the check doesn't
  really tell me anything"; "I want to keep the field for them to respond, but the flag check removed"): the DR testing notice
  still asks for the location (campus) of the student's DR exam, but that answer is never compared with the booking. The
  built-in and saved DR templates carry no check on `confirm_dr_location` (`seating.with_uin_confirmation` drops it), a moved
  location is no stale notice on the Exams page, and for cards issued earlier with the check Faculty ignores its result
  (`schema.UNCHECKED_FIELDS`): sync settles a card that only it flagged to verified/submitted, and the issue page, the
  student's row and the receipt email never call it a difference. The date and start-time checks stay. The Worker and the
  immutable submissions are unchanged.
- Sync events, immutable local submissions and the remote cursor commit together
  under the roster lifecycle lock. Retain remote history across deployment updates.
  All local records use the shared catalog's nightly verified backup; roster
  context reads use the existing verified term storage manager and lifecycle lock.
  Follow `response_cards/README.md` for operation, configuration and limits.
- The Response Cards page (UX overhaul, 2026-09-26) leads with **Needs attention** across current terms
  (`GET /api/response-cards/attention`: counts and titles only, no identifiers), linking each count to the issue filter
  that shows it. An issue's header primary follows its state (**Publish N cards**, then **Email N students**, both the
  existing explicit actions); per-card **Resend email** posts to the existing resend route, whose `resend_block` decides.
  **Download answers (CSV)** is built in the browser from the issue detail and never holds the typed
  `seating.UIN_FIELD` answer, uin-digest fields, verification objects, expected values, links, UINs or enrollment IDs;
  every cell is spreadsheet-escaped (UX overhaul amendment 10). **Copy emails** only copies addresses.
