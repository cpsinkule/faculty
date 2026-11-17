## Exam seating charts

Rule ID: **SEATING**. Current binding module contract. Read root `AGENTS.md` first.

- **Print room roster** is an ephemeral instructor print view of the existing
  term-owned chart. `seating_roster.room_roster` / GET
  `/api/seating/<id>/room-roster` reads verified roster data under the lifecycle
  lock and lists seated students alphabetically by exported roster name, with
  nine-digit UIN strings, section and seat. Both sections include a response-card
  column: bold **No response** for an issued current card without a submission,
  **Responded** (including answers needing review), or **No card issued**.
  Opening/starting a card does not count as responding; retained submissions
  still count when a card is revoked. An unissued replacement is shown separately
  as **Updated card not issued**. This is derived from saved response records,
  without syncing, publishing or emailing, and participates in preview revalidation.
  A following DR section contains
  current booking revisions linked to this assignment and the chart's permanent
  sections/memberships only: exam name, date, local start/end times, duration,
  campus and request date. Multiple linked bookings are all shown and flagged;
  none is guessed or dropped. Room review and stale seating block printing.
  The browser revalidates a content fingerprint before printing and requires
  another click when the preview changes. No new data owner, retained artifact,
  print batch, student-email content or delivery record is created.
- **Email student charts** belongs to the existing term-owned chart. The
  `seating_student_mail` queue references its permanent section/enrollment
  membership and retains each approved compressed anonymous snapshot, PDF,
  content/hash, pinned provider and
  delivery receipt in the catalog's verified nightly backup. It creates no
  response cards, print batches, public links or separate storage owner.
  `seating_student_pdf.sanitize_chart` allowlists only room geometry, seat
  labels and occupancy. PDFs contain no student names, enrollment IDs, UINs,
  response statuses, exam versions or private notes: the recipient's blue seat
  says **Your seat**, other occupied seats are gray **Occupied**, with seat
  numbers beneath both. Large rooms include an enlarged local detail.
  GET `/api/seating/<id>/student-email` previews recipients/history, and
  `.../student-email/preview` returns one fingerprint-checked PDF; neither
  sends. Explicit POST approves the exact fingerprint and atomically queues
  the batch with an idempotent request receipt in `seating_events`.
  The Inbox worker prepares and durably stores each PDF from its approved
  snapshot before recording a send attempt, keeping bulk rendering outside the
  HTTP request. It revalidates the roster, address, room and sanitized chart
  under the lifecycle lock before delivery; changed copies fail without being
  sent. Missing/duplicate addresses, DR/nonseated students and active jobs are
  excluded. `unsent` excludes accepted copies of the same content; `all` permits
  explicit resends, but neither automatically retries unconfirmed copies.
  PDFs use the existing signed `/send` relay or the pinned Gmail sender.
  Definite provider pauses are shared with response-card mail; receipts commit
  before completion, and interrupted/uncertain sends never auto-retry.
- `seating_charts` is owned by `course_terms.id` and lists its sections in
  `seating_chart_sections`; it references `assignments.id` (course-wide) and
  `rooms.id`. `seating_assignments` references `room_seats.id` and the permanent
  `roster_memberships(section_id, enrollment_id)`. No roster or chart data is
  copied; names are read from the term roster payload under the roster lock.
- DR exclusion uses `dr_exam_assignment_links` (`exam_links.py`): one link per
  booking to an `assignments.id` of the booking's course. `DrExamService.import_job`
  calls `ExamLinkService.sync` inside the import transaction, and startup links
  earlier bookings. Matching compares `canonical()` names, where test/exam words
  are dropped and numbers normalized ("Exam 1" ≡ "Test 1", "Final Exam" ≡ "Final");
  an unmatched booking creates the assignment (title as the DR center wrote it, except that the word "Test" becomes
  "Exam", `exam_links.exam_title`, requirement, 2026-09-27; slug key; the `document_events` record keeps the DR's own name). Only an explicit instructor relink
  (`relink_assignment_ids` on seating create/settings) moves links, and only
  within one course. The old `dr_exam_key`/`dr_confirmed_none` columns are unused.
- Chart pages (UX overhaul, 2026-09-26; `static/seating-state.js`): the header's one primary is the chart's next
  step (review the room, review seating issues, send updated seat cards, print the room roster on or after the exam
  day, confirm for printing, print the chart); the other actions sit in Seats, Print and Email students groups with
  Regenerate last as a danger action that names what reseating a confirmed chart undoes. Count tiles and filter chips
  filter the student table and outline seats (browser memory only; outline-only, never the reserved card-state
  colours). The scope line links the exam hub. `GET /api/seating` rows add read-only counts (`card_counts`,
  `version_totals`, `seat_updates_pending`, the same rule as Home's seat-update card); no identities.
- **One tile per exam** (requirement, 2026-11-12, of the list's tile for every room under each exam's heading: "each exam's
  seating charts should be behind a tile for that exam instead of each tile per room on this page"). The Seating charts
  list holds one tile for each exam (an assignment in a term; `seating-state.js` `chartGroups`): its name, course and term,
  date, rooms and sections, any section still without a room, response-card answers, and the rooms' seated, DR and
  free-seat counts together, with its state (Held, Confirmed, "1 of 2 confirmed", Not confirmed) and what needs you (rooms
  that need attention, updated seat cards to send; Grades certified once archived). The tile opens the exam's page of
  rooms, `#seating/exam/<assignment>/<term>`: the summary line and chips the list's heading carried, Add rooms while a
  section has none, and a tile for each room, drawn from the list rows the page already holds (no new route or read).
  An exam with one room and every section seated opens that room's chart itself (`hasRoomsPage`). A chart's back link
  leads to its exam's rooms when that page exists, else to the list; creating several rooms opens the exam's page. The
  exam's page sits in its assignment like a chart's (`assignment_workspace.resolve`; an exam with no chart is no
  assignment's page and says so). Upcoming, Past exams and Archived stay as they were; nothing is owned or stored anew.
- One chart per room. `create_plan` creates every room's chart for an exam in one
  transaction; a section may appear in one chart per assignment (`_check_overlap`,
  409), except within a mix.
- **Mixed rooms** (requirement, 2026-10-15: "choose my rooms and then choose sections that get randomly
  seated in those rooms"). `create_plan` with `pool: {room_ids, section_ids}` (two or more ready rooms)
  creates one chart per room sharing `seating_charts.pool_id`, an attribute of the existing term-owned
  chart; every chart of the mix lists all its sections. The students to seat (DR-linked, dropped and
  leaving students excluded, after any relink) are split by `pool_shares`: in proportion to the rooms'
  spaced seats while everyone fits spaced, otherwise to their usable seats (largest remainder, the larger
  room on a tie; every room seats somebody when there are enough students), and each room seats a random
  share. A chart's students are its sections' students less those seated in another room of its mix
  (`_scope`), so `_check`, `get`, Update and Regenerate act on that room's own students plus students seated
  nowhere in the mix; a student seated in one room cannot be moved into another (409). DR-booked students
  are listed, counted and given DR cards on the mix's first chart only, and DR cards already issued by any
  chart of the mix count as current. Deleting a mixed chart leaves its students unseated in the others.
  `GET /api/seating/options?rooms=` returns `pool` (each room's share) for the creation forecast; list rows
  carry `pool_rooms` and `pool_dr_room`. Print jobs stay one per section per chart.
- **Choosing rooms** (requirement, 2026-11-10, of the mix's 53 ready rooms as one flat list of checkboxes: "I think we can make
  this room selection menu more user friendly"). In the New seating chart window the ready rooms an earlier chart seated come
  first, the latest chart first, each saying which exam ("Used for Exam 2"; `seating-state.js` `roomOptions`, from the chart
  list the page already holds, no new route): above a hairline in a mix's list, and as a "Used before" group in each section's
  menu. A mix's rooms are aligned rows (room, usable, spaced) that scroll inside their own box; with more than eight ready rooms
  a "Find a room" box narrows them to the names holding every word typed, never unticking a room it hides, and Enter in it
  creates nothing. Under the list one line names the rooms chosen and totals their usable and spaced seats.
- Generation uses `secrets.SystemRandom` over usable seats (not broken, not
  blocked). Placement prefers spacing: `rooms.seat_runs`/`seat_neighbors` define
  neighbours as touching seats in one row or seats of one table; `_place` takes
  an alternating pattern per run (one seat per table) first, then fills the
  least crowded seats. `spacing()` reports spaced/crowded/adjacent pairs and
  `spaced_capacity`; rooms expose `spaced_count`. Capacity, empty-roster, and all-excluded cases raise before any write.
  `update` keeps valid seats and reseats only students whose seat became unusable,
  who gained a DR booking, who left, or who are new; `regenerate` reshuffles all.
  Every generation, update, move, and DR-name change records a `seating_events` row.
- Move or swap in a seat's card popover (or Move in the student table) highlights
  the source seat; the next usable seat click submits the version-checked move API
  and swaps occupants when needed. It opens no destination dialog. Escape, Cancel
  move, the source seat again, navigation, and chart replacement clear selection.
  Selection is transient browser state, with no new data owner. Disable duplicate
  move clicks while saving and never automatically retry a failed move request.
- Chart deletion is two-tier: a yes/no confirmation until a non-cancelled
  `print_batches` row exists for any version of the chart's assignment in the
  chart's term or the chart has `confirmed_at`, then the DELETE body must carry
  `confirmation: "delete"` (enforced server-side, not only in the dialog).
- `confirm()` (POST `/api/seating/<id>/confirm`) requires `acknowledge_issues`
  when the live check has issues, then creates one print batch per seated
  section through `DocumentService.create_batch` (outside the roster lock,
  request IDs `<request>:<section>` for idempotent retries) and records
  `confirmed_at`/`confirmed_mode`/`confirmed_version_id` plus a `confirmed`
  event. A browser opens every export with a fresh request ID, so exporting a
  confirmed chart again creates NEW batches with new stamps for every section;
  the earlier batches stay valid until superseded or cancelled. The chart button
  and both export dialogs say so (2026-09-24). A submit refused because the
  chart changed while the dialogs were open takes the chart's current version and
  shows its current seats; only another explicit submit confirms it, and the
  server checks the chart and its issues again. Mode `stamps_only` uses `register_stamp_pages`, a real immutable
  version of N blank pages labelled "Stamps only · N pages", so tokens,
  validation, labels and scan resolution work exactly as for any version. N is labeled pages (one label per page from the front; 1 labels the
  front page only). `sheet_labels` (both export dialogs, off by default) adds each student's Scantron label (DOCUMENTS
  "SCANTRON LABELS"), which the chart's print files then offer on its own button, apart from the page labels.
- With `prepare_cards` (default true), `confirm()` requires `exam_date`
  (ISO) and `exam_time` (text), stores them on the chart (they feed the
  `exam.date`/`exam.time` variables), takes the schemas edited in the review
  window (`cards.seat`/`cards.dr`, validated and forced to end with
  `seating.UIN_FIELD`), saves them as the course's managed templates when
  changed, freezes, and issues twice: a seat card issue in seating-chart
  context (seated students only) and a DR card issue in assignment context with
  explicit `enrollment_ids` for DR-booked students holding exactly one linked
  booking (others are reported, never guessed). Issue request IDs are
  `<request>:cards:seat|dr`, so retries reuse the same cards; `cards_json`
  records every issue the chart produced (`issue_ids`, latest in `issue_id`)
  and, for seat cards, the seat each card names (`seats`). `_prepare_cards`
  issues only to students without a current card (`_current_cards`): a
  re-confirmation adds cards for newly seated students. Confirmed seat changes
  invalidate old cards at the move; replacement cards use the fixed
  `seat_update` template (updated seat notice followed only by `UIN_FIELD`) and
  are emailed as UPDATED. A
  confirmation with `prepare_cards: false` keeps the earlier record. Managed
  templates still at version 1 are refreshed to the current built-in wording
  when the window opens or cards are issued.
  A RESTART WAITS (2026-10-15): a Confirm and a send of updated seat cards register with the web process's
  stop wait (`canvas/stopping.py`) for as long as they run, and none starts once Faculty is stopping (503,
  nothing written). A restart 4 s into HECC 207's Exam 2 Confirm had left its seat cards issued, its DR
  cards never issued and the chart unconfirmed.
  `seating_card_updates` belongs to the existing term-owned chart and permanent
  section/enrollment membership. A move, swap, update, or regeneration records
  the current seat/revision and clears its card pointer in the same transaction;
  no-op moves do nothing. Card-owned `response_card_invalidations` permanently
  blocks old links from local distribution, cancels their pending mail, and
  retains idempotent remote-revoke obligations (`invalidate-<card_id>`).
  Revoke immediately after the seat transaction commits; the Inbox worker
  retries failures. Never claim remote deactivation without its receipt or
  publish/send a replacement while an earlier revocation is pending. Sync
  retains earlier responses without resurrecting invalidated cards. Publication
  records intent before remote writes; uncertain publication is resolved by
  idempotent PUT then revoke. No email is triggered by moving seats.
  `POST /api/seating/<id>/seat-updates` explicitly prepares/publishes/queues only
  changed students, without print jobs, using the selected default or Gmail
  provider. Per-student revision issue keys recover interrupted preparations;
  `seating_events` retains exact request receipts. Source/version checks precede
  distribution, and the lifecycle lock spans delivery and receipt persistence.
  Definite failed sends may be retried by a new explicit send; uncertain sends
  require a reviewed individual resend. Export alone is not evidence of sending.
  `publish_cards` and `email_cards` default to true: after `ResponseCardService.publish`,
  `_prepare_cards` calls `response_cards.mail.queue_invitations(issue_id)`
  (when `email_cards` and `CardMailer.enabled`), which inserts one `response_card_mail` row per
  published card and sets `delivery_status='queued'`. `POST
  /api/response-cards/cards/<id>/resend` (`CardMailer.queue_resend`, guarded by
  `resend_block`) adds one more invitation row for a single pending, published
  card with an address and no mail already waiting; `invitation(resend=True)`
  adds a "second copy" sentence and keeps the subject. The table's old
  `UNIQUE(card_id,kind,submission_id)` was replaced by the partial unique index
  `response_card_mail_confirmation` (confirmations only); `ResponseCardService`
  renames a catalog that still carries the constraint to
  `response_card_mail_legacy` before `migration.sql` runs, then copies the rows
  back (`INSERT OR IGNORE`, so an interrupted start is safe). The Inbox worker's
  `work_once` calls `mail.process_one()` to send as the instructor's connected
  Gmail account (`response_cards/gmail.py`: PKCE sign-in started from
  `POST /api/response-cards/gmail/connect`, finished by the header-free
  `GET .../gmail/callback` bound to a stored state, refresh token kept in
  `FACULTY_GMAIL_TOKEN_FILE` with mode 0600, client from
  `FACULTY_GMAIL_CLIENT_FILE`; inert unless both are set, so tests never reach
  Google) or else through the print-export relay's `/message` route (same
  `FACULTY_PRINT_EMAIL_URL`/`SECRET`, reply-to from `FACULTY_MAIL_REPLY_TO`).
  Workspace `FACULTY_OUTBOUND_EMAIL_PROVIDER=resend` selects the relay's Resend
  transport even with Gmail connected; `auto` retains the legacy Gmail-first
  preference, `relay` selects the relay, and `gmail` requires a connection.
  A per-send `mail_provider` (`default` or `gmail`) is available in seating
  confirmation, issue sending, single-card resend and test dialogs; each new
  dialog starts with the workspace default. Gmail connection/selection never
  changes the configured Resend default. Automatic submission receipts use that
  default. New mail rows pin `provider` and `gmail_sender` as delivery attributes
  of the existing recipient-owned record, not a chart or course preference.
  A disconnected or different Gmail account cannot reroute a queued Gmail send
  through another account or provider. Unknown selections disable sending.
  The relay's private `RESEND_API_KEY`
  never enters the catalog, exports or browser. A validated relay rate-limit
  429 stores `response_card_mail.not_before` and pauses that provider's pending mail across
  restarts; unknown/uncertain outcomes never authorize an automatic resend. A
  `DeliveryError(unavailable=True, retry_after=…)` keeps the row pending and
  pauses the worker (Gmail limits, an expired grant, an unreachable Google);
  receipts commit before completion and nothing is resent after an unconfirmed
  delivery. `sync()` queues a confirmation
  (`kind='confirmation'`, one per submission) listing the student's answers;
  flagged answers are marked but expected values are never emailed.
  Invitation and submission-receipt greetings are `Hello,`, without student
  names. Receipts still include the card and submitted answers; this is not
  redaction of arbitrary instructor wording or student answers.
- `seating_gmail.py` exposes confirmed-chart **Send with Gmail** with
  GET/POST `/api/seating/<chart>/gmail-send`. Preview shares the exact current
  seat/DR-card audience checks with mail merge; it exposes no personal links.
  Only the explicit Send action queues the fingerprinted audience through Gmail,
  pins its sender, and atomically retains a request/hash/receipt in the existing
  term-owned chart's `seating_events` (`gmail_send`). Duplicate requests return
  that receipt even after delivery, and changed commands need a new request ID.
  Original invitation wording is preserved (no name or added reminder paragraph).
  Resend remains default; there is no publication or email on preview.
- `seating_mail_merge.py` derives an ephemeral XLSX from a confirmed term-owned
  chart and its existing published seat/DR cards; it creates no owner, catalog
  table, retained file or delivery record. **Invitation delivery → Gmail mail
  merge** confirms with `publish_cards: true, email_cards: false` and opens the
  preview; Faculty email remains the default. Export itself never publishes,
  queues, sends or marks delivery. Gmail sends remain untracked; card responses
  retain normal synchronization.
  Preview/download hold the roster lifecycle lock and use verified roster reads.
  Download revalidates the exact preview fingerprint (409 when stale). The default
  `unsent` audience excludes accepted/unconfirmed/active Faculty invitations;
  explicit `pending` (All awaiting response) permits previous/uncertain deliveries
  but never active mail jobs. Enforce exact chart/term/assignment/membership,
  usable seat, unique linked DR booking, current roster and frozen-card details;
  skip inactive cards, invalid addresses and duplicate eligible emails with reasons.
  `ResponseCardService._variables` supplies the same live values as issue creation;
  normalize the salted UIN-digest expectation when comparing; the retained
  membership fixes student identity.
  XLSX puts all-text `Recipients` first: Email, FirstName (constant `Student`),
  Course, Section, Exam, ResponseCardLink and Message; omit Name/LastName columns.
  Message uses the initial invitation's `Hello,` wording, never an automatic
  second-copy paragraph based on delivery status; retain the original replacement
  notice for an actual replacement card. Actual names stay in instructor preview
  and optional Skipped only. Include Instructions; older downloads require a
  fresh export to receive these wording/column changes. Never export UINs,
  enrollment IDs or verification values, or put personal links in previews/logs.
  Gmail uses a constant subject and an inserted Message merge chip; raw
  `@Message` text or hyperlinked merge values are insufficient. Stale date/DR/name/
  email cards require explicit revocation in Response Cards and reconfirmation.
- `media._validate_symbol` compares the decoder's corners with the expected
  outline (2 px at 200 DPI); on a miss it measures the dark-pixel extents inside
  the barcode box and applies the same tolerance to that outline before
  failing, because zxing's corner estimate drifts by a few pixels on some
  symbols (a real label in HECC 209 was rejected by 2.7 px while its printed
  outline was within 1 px). The quiet-zone check is unchanged.
- Print batches take `seat_marks` (`DocumentService.create_batch`, the
  `/api/documents/batches` body, and `SeatingService.confirm` which forwards it
  to every section's batch). It is a `print_batches` column added by
  `storage.initialize`, hashed into `request_hash` only when true so earlier
  request IDs keep their hash, copied into each seated manifest entry as
  `seat_mark`, and drawn by `media.stamp_pdf` at `layouts.seat_mark_positions`
  (bold 13 pt beside each stamp box, top edge aligned, outside the box). Labels
  are unchanged. `set_seat_marks(batch_id, enabled)` (`POST
  .../batches/<id>/seat-marks`) flips it for a pending or failed job only,
  under `batch_lock`, leaving `request_hash` as created and adding a
  `seat_marks` event; the worker's post-render manifest comparison still
  rejects a change that lands mid-render. Stamps identify the printed copy and its `enrollment_id`, not
  a seat; seat changes after export never alter stamps, and a copy that reached
  the wrong student is corrected with `assign_copy` ("Change assignment").
- Print jobs stay one per section (section ownership, locks, and files are
  unchanged). A room-wide file is `DocumentService.room_export(batch_ids, kind,
  media)`, reached through `GET /api/seating/<chart>/room-export/<print|labels>`
  after `SeatingService.room_export` checks the jobs belong to the chart. It
  orders manifest entries by `seat_order(seat_label)` across sections (extras
  last), then re-cuts the verified per-section PDFs page by page with the
  sandbox `reorder` operation (roll labels too, one label per page) or lays
  sheet labels out afresh via `labels`. It refuses until every job in the
  export is ready and current. `print_batches[].export` on a chart is the
  request-ID prefix shared by one confirmation's jobs.
- Exam letters belong to the existing term-owned seating chart: optional
  `seating_charts.exam_version_count` records 1–26 versions (A–Z),
  `allow_unequal_versions` records the chart's distribution choice (default false),
  and `seating_exam_versions` associates that chart and its `room_seats.id` with
  a letter. These are independent of immutable `assignment_versions` and PDF
  revisions. The optional count at creation applies to every room in that plan;
  an omitted count leaves **Assign exam versions**, which becomes **Print
  version list** after assignment. The verified nightly catalog backup retains
  saved letters; the metadata JSON export omits per-seat version assignments,
  just as it omits student seat assignments.
- Default automatic exam-letter assignment cycles over occupied current
  memberships only, keeping version totals equal or differing by at most one
  (161 students and two versions gives 81/80). Multi-room creation continues
  the cycle between rooms so the plan's total also stays balanced. The optional
  **Allow unequal version counts** checkbox starts unchecked; it permits the
  automatic assignment to trade balanced totals for fewer matching versions
  near students in adjacent rows, while preserving the hard rule that consecutive
  occupied seats in the same row have different letters when there are at least
  two versions. The relaxed optimizer starts with the balanced cycle and accepts
  deterministic row shifts or legal single-seat recolors only when they reduce
  distance-weighted repeated letters between nearest occupied seats in neighboring
  source rows/tables. Distances use PDF seat-box centers or workbook grid centers.
  It does not guarantee a global optimum or necessarily produce unequal totals.
  Group by source `row_label`, preserving that row across aisles and
  tables; workbook members follow grid position, floor-plan members natural
  seat-label order. Unlabelled studio plans use named tables. Never reset at
  empty seats or section boundaries. One version is explicitly all A.
  **Reassign versions** explicitly resets letters and can change the count or
  distribution option, replacing manual letters. Student moves,
  swaps, seating updates, and seating regeneration retain letters on their seats;
  never silently recompute them. Flag newly occupied seats without a letter and
  consecutive occupied students in a row with equal letters when at least two
  versions are configured, including across empty seats. Manual single-seat
  choices and later seating changes may unbalance the current counts; retain
  those choices and show a warning when unequal counts are not enabled. Swapping
  two occupied seats' letters retains the counts.
- Version view replaces student names/response-card status with saved letters.
  Each letter has a fixed blue, purple, magenta or neutral hue, shared
  by its seats, count key and editor choices. On screen, `seating-theme.css`
  mixes that hue into the current neutral theme; `--exam-version-fill` retains
  the original print color. The instructor requested Berkeley Mono box-drawing
  seat frames on screen; their shared aria-hidden markup ignores pointer events
  and is hidden for printing. Preserve seat dimensions and fitted name fonts.
  Green, yellow, red and orange
  remain reserved for response-card statuses. Conflicts keep the version fill
  with a dashed border; selection uses an outline. Palette choices are browser
  presentation only and add no stored chart preference or data owner.
  Selecting an occupied seat permits swapping letters with another occupied
  seat or setting one available letter directly. Version-checked writes change
  letters only, retain event history and instructor choices, and never move
  students, invalidate cards, publish, or email. Returning to student view restores
  the existing student move/swap behavior. `POST /api/seating/<id>/exam-versions`
  takes `{version, count, allow_unequal_versions?}` to assign or reassign; `POST
  /api/seating/<id>/exam-versions/edit` takes `{version, seat_id, target_seat_id}`
  to swap, or `{version, seat_id, exam_version}` to set a letter.
- **Print version list** previews the saved current letters with each student's
  row, seat, name and section, version totals and any version conflicts. Each row
  begins with its student count and per-version totals derived from the saved
  letters, including zero counts for configured versions unused in that row.
  Missing letters and unresolved chart/room issues block printing; manually retained
  duplicate letters and changed count balance remain printable with warnings.
  Recheck the locked chart before printing and require review of a changed
  preview. The list creates no
  print batch or delivery. Print CSS resets after printing/navigation.
- Printing a chart (`openPrint` in `static/seating.js`) offers two layouts.
  Spreadsheet "Fit" reflows the on-screen grid using `--columns`/`--rows`,
  container width (`cqw`), and the `--print-height` budget for Letter paper
  inside 10 mm margins minus the title and landmark rows. Spreadsheet "Tiles"
  clones column bands with 58 px seats, repeating row labels;
  `columnBands` never splits a `.seat-table`. Table chairs stack with enough
  height for their names in the readable print layout. PDF floor plans use separate
  print clones positioned from the retained source geometry, independent of
  screen zoom. "Fit" is a one-page seat-label overview. "Tiles" partitions
  seats in two dimensions with padding to keep each seat/name wholly on one
  page; every page repeats the exam, room, sections, and tile position. Measure
  wrapped captions before setting the page's crop height, and fit long names
  at the printed chair width rather than inheriting the screen's line clamp.
  Floor-plan crops must have explicit width AND height: an absolutely
  positioned grid cannot establish its clipping parent's height. Both layouts
  choose an orientation via a constructed `@page` stylesheet (Chrome/Edge
  honour it); Safari users select the indicated orientation. Set dynamic
  geometry through CSSOM, never inline markup styles (CSP). Run
  `tests/browser_seating_print.py`, which verifies actual PDFs using
  `page.pdf(prefer_css_page_size=True)` after stubbing `window.print`, across
  all reviewed ILCB profiles and spreadsheet charts with synthetic names.
- `SeatingService.get()` attaches `card` to each roster entry and seat tile
  (`_card_states`, latest card per student across the chart's issues): state
  `complete`, `pending`, `issue`, or `update_pending` with `summary` and `detail` lines. Issues cover flagged answers (with the mismatched fields),
  publish or mail failures, no roster address, revoked or expired cards,
  students seated after the cards, and a seat that differs from the one the
  card names. `problems` carries the problem lines on their own (the table
  note joins them; `detail` remains problems followed by status lines), and
  the student table's status button opens the same panel in a dialog so DR
  students, who have no seat tile, are reachable. Each state carries `can_resend` (mail enabled, not a stale
  seat, and `resend_block` empty) and `link_available` (not stale and
  `CardMailer.awaiting_response` empty); the popover shows Resend email and
  the card link with a Copy button only then, and the server re-checks on the
  request. The link is fetched for the one open seat through `GET
  /api/response-cards/cards/<id>/link` (`ResponseCardService.link`, 409 once
  the card is answered, revoked, or expired) and never embedded in the chart.
  `update_pending` takes orange precedence on a moved student's new named seat
  until an accepted invitation with a provider receipt belongs to that exact
  current revision/card. Preparation, publication, queued mail, old submissions,
  failures and unconfirmed delivery never clear it. `seat_updates` exposes the
  pending count and current recipients without tokens. Startup backfills known
  stale confirmed seats without sending mail. The UI paints tiles
  green/yellow/red/orange and expands a clicked tile
  into a popover; keep the per-student payload free of tokens and UINs.
- Verification modes `compact` (letters/digits only, case-folded) and
  `uin-digest` (salted SHA-256 of the nine UIN digits, expected value
  `salt:hex` from the `student.uin | uin_digest` variable) exist in both
  `response_cards/schema.py` and the Worker's `schema.ts`; the raw UIN never
  enters a snapshot. The deployed Worker must include these modes before cards
  using them can be published.
- `_check` is a SQL-only comparison of `seating_assignments` with
  `roster_memberships`, DR-linked bookings, and seat conditions. It runs after
  every generation/update (recorded in `check_json`/`checked_at` and the event)
  and live in `list()`/`get()`; chart pages derive their issues from it rather
  than from the placement code, so a placement bug would still be flagged.
- Seat and chart deletions cascade to `seating_assignments`.
- Print jobs may reference a chart (`print_batches.seating_chart_id`, ON DELETE
  SET NULL). `SeatingService.print_plan` (roster lock held by the caller) lists
  one copy per seated student of a section in seat order with a caption;
  `printed_copies.seat_label`/`caption` snapshot what was printed. Captions are
  drawn beside the stamp box inside the class's reserved bottom band and are
  omitted from manifests when empty, so earlier batches' manifests are unchanged.
  The Data Matrix payload stays the opaque token. `media.labels_pdf` renders
  peel-off stamps on a chosen `LABEL_MEDIA` (Brother DK-1201 29 x 90 mm roll
  labels by default, DK-1209 62 x 29 mm, or Avery 5160 sheets), validated by
  `validate_labels` in the sandbox (page size, mark placement, quiet zone) and
  retained once per batch and media in `print_batch_labels`; labels are an
  alternative physical form of the same issued tokens, never new tokens. Roll
  pages are exactly the label size so the label printer needs no scaling. Rooms used by a chart
  refuse deletion. Student-facing seat notices are the chart mail and Response Card flows
  above; do not build attendance or exam grading from this module unless requested.
