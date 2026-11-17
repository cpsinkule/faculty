## Home, frontend, and checks

2026-10-09 assignment flow: queued/running work, eligible work waiting to start and blocked work are distinct. Local gradebook recording never marks Return complete or means published. Prepare, Collect and Return show actionable checklists from their owning projections. Collection edits survive quiet refresh and use the central leave guard. Inline review zoom preserves the split desk and browser-session viewing preference; full reasons remain available. The shared file viewer accepts verified canonical version previews.

Rule ID: **FRONTEND**. Current binding module contract. Read root `AGENTS.md` first.

Assistant tool-use checks appear in the existing closed “How it got this” review, separately labeled from evidence checks.
They are advisory findings about relevance, context and completion, never proof that an action ran. Existing approval cards,
unsent text, dialogs and quiet refresh behavior stay unchanged.

Assistant gestures: the shared shortcut registry permits holding Command, with the pointer still, for 600 ms in fields, grids and dialogs to draw
an area, then releasing Command to send it. Two quick Command taps open an empty floating chat for ordinary typed requests;
opening it never selects page content or starts a model turn. These opt-ins do not widen other shortcuts.
A top-layer selector prevents underlying actions while reading the intersected visible words, fields and record
references. Images are cropped from their own coordinate space, including object-fit letterboxing, rather than the screen.
There is no screen-sharing prompt or browser-window calibration. Native PDFs temporarily show the viewer's rendered pages;
the original iframe stays mounted and is restored on selection, cancellation or failure. Hidden content and credentials are
excluded; only explicitly selected editable values can be sent, marked as possibly unsaved.
The floating conversation and main Assistant drawer use the DESIGN-LANGUAGE "Assistant glass" material over shared controls and existing assistant rendering. The floating conversation can transfer the same conversation
to Assistant without closing source dialogs or losing edits, and the drawer's conversation can move to a floating window the same way (2026-10-09, ASSISTANT BOTH WAYS).
Since 2026-10-09 both surfaces draw the conversation and its composer with one module (`static/assistant-chat.js`; ASSISTANT "One
conversation, real windows"): the window resizes, is remembered and can be put away as a bar; the drawer's left edge drags its
width (never under 480 px). Since 2026-10-10 the floating window draws its conversation's cards in a panel at its right and
widens by that panel, the conversation keeping one line a card (ASSISTANT "CHANGES BESIDE THE CHAT"). Browser checks exercise the actual DOM and image extraction,
not a mocked screen stream. Native macOS Safari qualification still requires testing on that browser.
Double Command fires on the second key release (each tap shorter than the 600 ms selection hold, releases within 700 ms).
The gestures accept physical left/right Command codes as well as the Meta key name. Other keys/modifiers, window blur and
visibility changes cancel a hold. Pointer actions clear partial taps and pointer movement over 4 px drops a pending hold, but
drawing during an active hold keeps it armed; a click without a drag during a hold passes through as Command+click.
Escape cancels only the selector, preserving an underlying dialog and unsent fields; inside the floating chat it closes the chat
(an open "/" or "@" list in the box first, 2026-10-09: Escape then closes only that list, in the drawer too),
and elsewhere it stays with its page control. Command chords retain their normal behavior, including in the chat.

2026-10-09 review (instructor approved all fixes and suggestions): `ui.holdCaptureFocus` protects only the editor that was focused
when an assistant surface took focus, and only while focus stays in the selector, bubble or drawer; clicking or tabbing into the
page ends the hold and delivers the editor's deferred blur, so its owner's normal rule runs (a gradebook cell commits). Owners ask
`isCaptureHeld(element)` instead of a page-wide flag. The floating chat survives navigation and hidden tabs and hides (not closes)
while a selection is drawn over it. Toasts stay reachable: the stack is a manual popover shown above the top layer, moves inside an
open modal dialog (everything outside a modal is inert; it is put back if the dialog's content is rewritten) and sits left of the
open Assistant drawer. Only a toast's own buttons take clicks, so its text never blocks a control beneath it. `toastSpec` treats tone
`error` as `danger`.

2026-10-09 automation visibility: the Automation tab shows waits as quiet notes, held rules with Prepare again, active runs
below the form (runs-only reads every five seconds while one is queued or running, patched in place) and a folded "Done by
Faculty · last 7 days" log; Saved folds older drafts of a target. Desktop only, one appearance (ASSISTANT "Automation
visibility (2026-10-09)").
2026-10-09 assistant capabilities: the Automation tab reads its workspace projection and retains existing owner controls.
Page context conveys bounded record IDs and the leave guard's unsaved-work flag, not draft field contents. Quiet patches and
desktop-only layout remain required; this context authorizes no change by itself.
ASSIGNMENT-WORKSPACE (requirement, 2026-10-03): one Assignments sidebar entry replaces
Assignments & printing, Exams and Grading. ONE COUNT OF WHAT NEEDS YOU (requirement, 2026-10-18: the pipeline review's
"one count of what needs you, the same on Home, sidebar, tabs and index"; "make all of your suggested changes"): the
Assignments badge counts decisions, one per attention card of an assignment's work (cards routed to #documents, #exams or
#grading, or marked `assignment_work`: a makeup version waiting to be approved; `page-header.js assignmentDecisions`), as the assignment tabs and the index count needs-you items:
`grading:<plan>:review` is one decision however many responses it holds. The tab title's '(N)' counts those cards the same
way, and Home's rows of an assignment's work are one item each (`items: 1`; the count column still shows the responses), so
the hero, the tab title and the badges agree. Items the overview's light read cannot know (version decisions, certify,
missing papers, upload, paper, readiness, regrades) still add only to Home's count once Home has read `/api/grading`, and to
the tabs and index, not to the badge (UX overhaul amendment 24's cost reason unchanged). Canonical list/detail routes are
`#assignments` and `#assignments/<assignment>/<term>`. Existing owner routes keep working
inside the shell. The workspace read API `/api/assignments` owns no records and does not
change `/api/state` or the owners' default responses. Summary refresh patches in place;
embedded working views retain their own leave guards, keyboard shortcuts and refresh.

- `workspace_overview.py` is a read-only workspace projection, not a retained
  resource owner. Reuse `ScanService.overview`. Home may show student names (the rule keeping
  it free of them was removed, requirement, 2026-10-06: "Those two rules for frontend and google should be deleted, I have no need for those restrictions and the student's names should be on their appointments"). Exam dates remain campus-local
  wall-clock values. Operational status must not describe an incomplete backup
  as verified or infer progress from an old heartbeat.
- `/api/state` remains backward compatible; `include=core,rooms,seating,storage`
  selects only needed sections. Missing sections in a partial response do not
  clear frontend state. Room/seating details load on their own pages. Catalog-only
  Home diagnostics may operate without the HDD only when the live catalog is
  verified on NVMe; all other storage guards remain.
- A RESTART NOBODY SEES (requirement, 2026-11-03: "im getting this error a lot", of "The server response could not be read"). Faculty
  restarted 148 times 2026-10-20..11-03, and all 63 empty 502s Tailscale serve gave in those days fell in one: the port closed for a
  second, or a request was cut off. Now (`web_server.py`, `deploy/faculty.service.d/keep-port.conf`) systemd's file descriptor store
  keeps the listening socket while the process is replaced, so what arrives in between waits for the new process, and a stopping
  process takes no more connections and finishes what it is answering, at most `DRAIN_WAIT` (8 s), after any writes to Canvas it waits
  for (CANVAS "A STOP WAITS"). The store is released on a stop, so a tab's poll never starts a Faculty stopped on purpose (CATALOG),
  which a systemd socket unit would. Tested on real restarts of a scratch unit: 2562 requests over three restarts, none lost, the
  slowest 2.6 s. The first restart under a new `keep-port.conf` still has the gap, the old process having left nothing with systemd.
  What remains (a request past `DRAIN_WAIT`, a stop then a start) a GET survives by itself: `static/request.js` asks again after
  500, 1500 and 3000 ms when an answer could not be read, GET only, since a lost write's answer can hide a completed write, and
  never when the request's own timer ended it, since that is no answer in time ("Can't reach your server") and asking adds load.
  Waitress has 8 threads (4 before 2026-11-03, with a queue up to 28 deep that day), so a page's small reads do not wait behind a 5 s
  Follow-up read or an Approve of up to a minute.
  A BUSY FACULTY IS NOT AN UNREACHABLE ONE (requirement, 2026-11-04, "got this error again after approving a followup": all four cards had
  gone through, and "Can't reach your server" was a read that gave up at 15 s while they ran). A request waits 60 s unless its caller sets
  its own (`static/request.js`; multipart 120 s). A server that has truly gone stops answering at the connection or at this minute. A
  request of 10 s or more goes in the journal (`SLOW_REQUEST`, app.py), since waitress logs none: read it before guessing at the next one.
- `web_assets.py` snapshots public frontend sources under an immutable build
  prefix, including relative module imports. Restart the app after frontend edits.
  Never give private API responses, HTML, exports or teaching artifacts this
  cache policy. So that a tab opened before a restart still loads its lazily
  imported modules (UX overhaul amendment 4, 2026-09-26), the first time a build
  serves `/` its public files (`.js .css .svg .png .jpg .json .woff2` from `static/` only) go
  into a content-addressed archive: `FACULTY_WEB_ASSET_CACHE`, else
  `<nvme_root>/runtime/web-assets` with configured storage, else
  `<DATA_DIR>/web-assets`; never `/tmp` or `tmp/<web|worker>` (the archive is then
  off). It keeps the newest eight builds, serves an earlier prefix only by its
  manifest and only when each file's SHA-256 matches, and is runtime cache, not a
  storage owner. `ui.js loadModule` ('Faculty was updated since this tab opened ·
  Reload') stays the last resort; views warm their lazy modules when created.
  The screen design language is DESIGN-LANGUAGE (`docs/decisions/design-language.md`, requirement, 2026-10-04,
  "Ledger"; FRONTEND.md "The screen design language"), on every surface. One appearance and one desktop
  layout (requirement, 2026-10-03: "get rid of dark mode and mobile"): no theme switch or `data-theme`, no phone or
  tablet tier, a 1180 px minimum width (`tests/test_design_language.py` holds both).
  Shared screen styles use `static/design-tokens.css`; printed documents and
  seat/version palettes retain their own contracts. Screen rules
  carry no hex colour or corner radius of their own (FRONTEND.md "Legacy sheets");
  notices, empty states and state pills use the canonical `.callout`, `.empty-state`
  and `.chip` classes, keeping old class names only as hooks.
- `manage_checks.py` runs synthetic checks with NVMe scratch and isolated
  configuration. See `CHECKS.md` for quick/full/browser/restore coverage and
  `FRONTEND.md` for request/dialog utilities and incremental JSDoc contracts.
  `catalog_migrations.py` owns workspace-level migration metadata only; new
  recorded SQL versions are immutable and run under the existing lifecycle lock
  in one transaction. Do not invent checksums for untracked historical versions.
- Home's Needs you, the sidebar attention badges and the tab title's '(N)' come from
  the same `/api/workspace/overview` attention cards (derived, PRESENT only; the
  Inbox card counts the Inbox's own filing, `derived: true`; a card marked
  `counted_in` names things another card already counts, stays on Home with its
  count and adds to no badge, tab title or Home total: TEACHING-WORKFLOWS
  "NEEDS ME COUNT", 2026-10-06). The Grading badge
  counts responses waiting for review and failed to grade, a finished rubric check
  waiting for review, the questions whose suggested answer areas wait for a look
  (2026-10-02: a check that finished while the instructor was elsewhere raised
  nothing) and the grading-prep queues' failures: papers waiting for
  a version decision are left out, since deciding that is the per-paper
  `paper_version` check (about 1 s per poll); they appear in Home's Needs you once
  Home has read `/api/grading`, and on Grading (orchestrator decision 2026-09-26,
  UX overhaul amendment 24). A failure is counted once: a grading task's by its
  plan's card and a scan inspection's by the Inbox card, never again by a queue
  card (the health table keeps every queue), and a failure superseded by a later
  attempt, a cancelled batch or a recovery by hand is history, not work. Home's
  Coming up reads the calendar feed; its exam rows link to the Exams hub.
- Home's sources (`workspace_overview.py`, `static/home.js`), all read-only, with
  `PRESENT` on every query that looks across submissions. **Needs you** lists the
  attention cards: the Inbox card (present scans the Inbox files as todo/check or
  failed, plus present non-scan jobs in needs_review/failed: exactly Current's
  needs-you share), up to five of `current_scans` (Current's order; received time and
  page count, never filenames) with their stage's verb and `scanNext` deep link,
  Response Card cards (flagged answers, undelivered invitations, unconfirmed
  receipts; `response_cards.service.delivery_problem` is the one delivery rule), seat
  updates per chart and specific queue cards. While Approve cards wait in the Assistant's
  Changes, an "Assistant changes waiting" row (the overview's `assistant_changes`, present
  only when nonzero and counted by the assistant's own rule, ASSISTANT "HOME CARD",
  requirement, 2026-10-09) opens the drawer's Changes tab; it adds to Home's count, not to a
  badge or the tab title. Once Home has read
  `/api/grading?health=0` (after first paint, only while Home is shown, at most every
  two minutes, only `current_grading_plans`), its grading rows replace the server's
  light grading cards and add version decisions, papers waiting for a setup step
  (papers are in and the assignment's current step is Rubrics & Templates, Answer
  areas or Key & Rubric Finalization, waiting for the instructor rather than in
  progress; the row says what to do there) and new responses whose grading did not start
  by itself (the plan's `auto_grade`: switched off, paused after a cancelled or reset
  batch, or refused), 2026-10-02: the pipeline waits only for the instructor, and
  these are the places it does; they add to Home's count, not to the badges. **Working
  on it** lists the scans the worker has, then one line for the last seven days'
  finished scans, linked to where the Inbox files them: History, or Archived for those
  whose assessment is certified (`finished_recent_archived`). **In progress** holds
  the place it will take from the first paint (none without a current grading plan,
  one line for a single plan no attention card names, else a row of cards) and
  shows a compact step-graph card per grading
  plan under way (`assignmentNext`; one waiting for its papers says so), or names the
  one assignment not started. Since 2026-10-18 (pipeline review I2, I7) its links are "Finish the scan checks (N)" (to
  `#assignments/<a>/<t>?area=collect&scans=waiting` when every waiting scan is one known assignment's, else Grading's
  `waiting_href`) and "All grading" → `#assignments?filter=grading` (the Grading index is retired); a card's title and the
  one not-started assignment open the assignment shell (`#assignments/<a>/<t>`) when Home knows the assignment, and "N
  grading assignments not started" links "Assignments" → `#assignments?filter=grading`. Home's grading rows use the
  assignment's needs-you labels and the tab words (2026-10-18): "Review grades · <title>", "Inspect failed grading · <title>"
  (`#grading/<plan>?tab=review&open=failed`, verb Inspect), "Review the rubric check · <title>", "Check answer areas and
  templates · <title>" (verb Check), and once Grading is read "Decide paper versions", "Finish grading preparation" (was
  "Papers waiting for <step>"; the detail still names what to do), "Certify grades" (was "Grades ready to certify") and
  "Grading did not start by itself" (verb "Open Grade"). Students with no scanned paper have no row (ASSIGNMENT-WORKSPACE "THE ZEROS
  ARE SAID AT CERTIFY", 2026-11-05). **Coming up** reads
  `GET /api/calendar` from campus today to +21 days, grouped by date, course, kind
  and title; exam rows link to their `exam_href` (the exam record, else
  `#exams/assessment/<assignment>/<term>`). The hero sentence counts the same items
  as Needs you; its primary uploads scans and its secondaries (Create assignment,
  Draft a quiz) link to the owning page's own windows. Workspace health states itself
  while closed; Add calendar event stays under it.
- Shell and page rules (the UX overhaul the instructor asked for, 2026-09-26: calm,
  goal-first pages, trimmed redundancy, no capability lost; FRONTEND.md "Component
  vocabulary"). The sidebar is TODAY Home, Calendar, Mail, Student follow-up, Recordings
  (requirement, 2026-11-14, "Yes, add it": `#recordings`; its badge counts the recordings that wait for Keep or Don't keep,
  from Home's card `recordings:keep`, the instructor the same day: "Home and sidebar too";
  RECORDINGS "The page, routes and the Assistant") · COURSES Courses (one entry for
  both course lists and every course page except a course's Canvas pages; the list has its
  own Current | All switch), Canvas (requirement, 2026-09-30: `#canvas`, every current
  course's Canvas state and next step; a course's Canvas pages highlight it), Accommodations
  · ASSESSMENTS in lifecycle order: Assignments, Seating charts, Response Cards,
  Teaching Inbox · LIBRARY Rooms (the gradebook is each section tab since
  2026-09-28); its sticky footer holds Workspace settings and the system status popover.
  Teaching review has no rail entry since 2026-10-18 (pipeline review P3, approved): `#teaching-review` stays, opens from
  the Assignments index strip and the palette's Actions, and highlights Assignments. A scan page (`#inbox/job/...`)
  highlights Assignments while the assignment shell's context is active (`faculty:assignment-context` with `active: true`,
  or `#assignment-workspace[data-area]`; ASSIGNMENT-WORKSPACE "ASSIGNMENT CONTEXT"); otherwise the Teaching Inbox
  (`page-header.js navEntry`).
  Workspace settings is the settings hub: Profile, "Settings elsewhere"
  (links opening Compute settings `#grading?settings=1`, Email settings
  `#response-cards?email`, Codes & API access `#inbox?codes`, and Print email
  recipients on the print jobs, `#documents?tab=batches`), Storage & backups, Access from other devices
  (the Connection guide, no longer a sidebar item) and Export. Every view names its
  object through `header.set` (`static/page-header.js`) and opens on the white header band (DESIGN-LANGUAGE §5);
  only Home's header is the hero variant.
  Button tiers (`button-theme.css`): at most one ink `.button.primary` per header,
  panel or dialog; `.button` is secondary; `.text-button` and `.link-arrow` navigate;
  destructive actions are `.danger`, in a '⋯' action menu or away from the primary.
  A disabled button shows `not-allowed`; the wait cursor only while it, or its form, is busy with a request
  (requirement, 2026-10-02; FRONTEND.md "Busy and blocked buttons"). Controls give 3% while pressed and click-opened
  dialogs enter in 160ms; the keyboard's palette and shortcut list, tabs and area switches do not move; reduced motion
  keeps fades only (requirement, 2026-10-04; FRONTEND.md "Press and open motion").
  A lead is one sentence; the rest goes into a closed disclosure. Views implement
  `render(force = false)` and patch polls in place. Sidebar badges and the tab
  title's '(N)' are the server's attention cards mapped by href (`attentionBadges`):
  a Teaching Inbox card counts only with `derived: true`, operations cards become
  Home's health dot, and no view counts a badge in the browser. Every keyboard
  shortcut goes through `ui.js registerShortcut`; none ticks a review's "I
  inspected…" box. Deep links that open a control drop their query once read; links
  only navigate, and every action keeps its page's own checks. The status popover
  (`static/system-status.js`) reuses the overview read (no extra poll) and owns the
  "ready for you" / "finished" toasts, derived by comparing two successive reads;
  views do not toast the same events. The command palette (Ctrl K) reads existing
  GET endpoints lazily and writes nothing: its actions open the owning page's own
  control. JEV ROUTING (requirement, 2026-10-03; docs/decisions/jev.md): after a 400 ms
  pause, a typed sentence of three words or more is sent with the palette's OWN places
  (never students, scans or recent places) to `POST /api/jev/route`, whose only write
  is its Jev receipt. The answer names one of those places (and, for grading, a step
  as `?tab=`), so a guess opens exactly what its row would. A confident guess leads
  ("Best match"); others follow the word matches ("Did you mean"); a question offers
  "Ask the assistant", which fills the assistant's box without sending it. Jev off,
  failed or unreachable adds no row.
- The Exams hub (`exam_workflow/overview.py`, `GET /api/exams/<event>/overview` and
  `/api/exams/lifecycle?assignment_id=&term_id=`) is a read-only projection of one
  assessment in one term, keyed by (assignment, term) because exam records are often
  made after the exam (UX overhaul amendment 14): it stores, decides and sends nothing and only
  links to the owning pages. It draws eight stages (Paper, Seating, Notices, Print,
  Exam day, Collected, Grade, Return; keys `grading` and `recorded` unchanged, the assignment's tab words since 2026-10-18)
  with the step graph, one next step from the
  stages alone, and a readiness checklist only while the exam is ahead. The Exams list
  also shows current-term assessments with seating charts or non-registration print
  jobs but no exam record, opening `#exams/assessment/<assignment>/<term>`; that
  view's only way to make a record is the existing New exam dialog, prefilled.
  `assignment_stages` is the one Paper/Seating/Print rule the hub and the Assignments
  cards share (UX overhaul amendment 15: registration jobs, `unlabeled:` request IDs in
  `document_identity/origins.py`, and superseded or cancelled batches never count).
  Pages link to it by the exam record when one exists, else
  `#exams/assessment/<assignment>/<term>`. Its Grade stage repeats the Grading
  page's `workflow_summary`, which reads the term's roster payload: it is read at
  most every `GRADING_EVERY` (120 s) per plan while the plan's grading record (its
  version, newest audit event and newest scan link) is unchanged, so the
  instructor's own certification, review or retry shows at once (review fix,
  2026-10-02); a plan or assessment that cannot be read shows "could not be read"
  (or is listed without its lifecycle) instead of failing the Exams list or the hub
  (review fix, 2026-09-26). Only a scan or a retained grade says an assessment's
  papers were written: a grading plan set up by a quiz draft's approval, or by the
  instructor's Set up grading now, exists before any paper does, so that assessment
  stays ahead with its checklist and, printed and undated, its next step is to
  upload its scans once it is given (2026-10-02). Without a plan the Grading tile
  offers Set up grading now (requirement, 2026-10-02), a secondary button: the next
  step keeps the one primary. Its next steps use the assignment's words (2026-10-18): "Certify grades" (label Certify),
  "Finish the scan checks", the grading detail "Waiting for scan checks on N scans", and the Paper and Print steps link
  "Assignments". THE EXAM RECORD INSIDE THE SHELL (I6): the record's step graph and its next-step callout carry
  `data-exam-lifecycle`, its tab row `data-exam-subtabs`; inside the assignment shell (`#assignment-workspace[data-area]`)
  the shell hides the former and styles the latter as DESIGN-LANGUAGE §4 sub-tabs; outside the shell nothing changes
  (ASSIGNMENT-WORKSPACE).
- Worker scheduling rotates bounded processor turns; do not restore fixed
  priority starvation. Preserve the execution locks, exact command approvals,
  revocation-before-mail checks, durable receipts, and explicit uncertain retries.
2026-10-09: Workspace-health failures and Automation blockers offer a review-only Assistant recovery request. Canvas conflict
details display escaped exact values, mark truncation, and explicitly copy resolved text into the existing unsaved draft.
The Assistant can open above that dialog without closing it. Unsent Assistant text blocks a new request without discarding
it (since 2026-10-09 attached files and a message waiting for the running answer count as unsent too, ASSISTANT QUEUE ONE);
background refresh does not replace the current request. The added integration files are workspace-owned, retaining the
existing term/section/enrollment references. See ASSISTANT and CANVAS for behavior and approval constraints.
