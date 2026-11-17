## Canvas

Rule ID: **CANVAS**. Current binding module contract. Read root `AGENTS.md` first.

2026-10-09 source reconciliation (instructor's eight assistant improvements): the compressed snapshots retained by Sync may now
be read by the term's reconciliation projection. Later checked send receipts supply subsequent observations; an uncertain send
is unknown. This authorizes no new automatic Canvas read/write, and never describes a retained observation as live agreement.

SETTLING (2026-10-09 second review): only the process that runs Faculty's Canvas writes (`CanvasService.owner`, set in the web
process where the morning sync starts) records a send, post or content push it no longer runs as `interrupted`. Any other
process (the Inbox worker's daily reconciliation read, a command, the checks) shows the record as it is, so a post the web
process is still running keeps its real outcome. Web-process responses are unchanged.

- The instructor asked for Canvas "on the section pages ... just like we did with gradescope" (2026-09-27), eventually posting
  gradebook scores, reading the roster, creating Canvas assignments and importing a course export (what comes over is not
  decided: do not build the course-export import until they say). Built: the kept session, each section's link, the roster
  comparison and drops, the gradebook's Sync with Canvas, the term's Canvas copy with its pushes, and messages to chosen sections
  (below); the course-export import is not.
- ONE CELL (requirement, 2026-10-07, answering a Gradescope regrade request: asked, they chose "Yes, include Canvas" in the same Apply;
  REGRADES "APPLY"; `gradebook/canvas_cell.py`, `POST /api/gradebook/terms/<term>/canvas-cell/preview` and `…/canvas-cell`, `GET
  …/canvas-cell/<send>`). One student's cell of one column, beside Sync with Canvas and never instead of it: SYNC's To-Canvas half
  (THE RULE, `agreed`, held grades, receipts) for that column and student alone (`canvas_export.plan(item_id=...)`, the setup kept
  whole for `targets`), with no From-Canvas half, no fill, no take, no column created and no `sync.in_step` (the column and course
  stay exactly as in step as they were). It reads ONE course (that column's assignment's submissions). The preview writes
  nothing; the send names its fingerprint, a request ID (8 to 120 characters) and `confirmation: "send-cell"`, is registered with
  `canvas.writes` before Canvas is read (a restart waits for it), is refused while any send of the term runs (and Sync refuses
  while it runs), sends only a plan that is exactly that one value with Canvas showing what the preview showed (a held grade waits
  for Sync's Keep Faculty's), and is ONE `gradebook_canvas_sends` row (`section_id` the student's) run by the sync's own
  `_run_send` with its receipts (uncertain before Canvas is asked, settled by reading back, never sent again by itself).
  A CANVAS QUIZ'S GRADE (requirement, 2026-10-13: "I asked to update this student's video quiz grade and it suggested a code
  change", of a one-time 2/2 the Assistant could offer only as a card to change Faculty, since a Canvas quiz is read-only here
  and never sent). One student's grade on a quiz Canvas grades (`canvas` "quiz") is set IN CANVAS, on the quiz's own assignment:
  `POST /api/gradebook/terms/<term>/canvas-quiz-grade/preview` and `…/canvas-quiz-grade` ({item_id, row, points}; owner: the
  student's section membership in the term's gradebook, the row `section_id:enrollment_id`; `gradebook/canvas_cell.py`
  `quiz_preview`, `quiz_send`). The column stays Canvas's: the cell is never typed into (every score write still refuses it),
  nothing is written to it, and the next Sync with Canvas reads the grade back like any Canvas grade (the morning's by itself).
  Never for WebAssign's columns (WebAssign sends its score again) or Faculty's own (change the cell; ONE CELL or Sync sends it).
  The preview reads ONE course and writes nothing: the grade within the column's points, the quiz's one Canvas assignment
  (published, graded in points, worth the column's points), the student there by UIN and not dropped, what Canvas shows now, and
  `ready` only when the grade differs and nothing is in the way (a quiz waiting for review in Canvas is left alone). The send
  names the preview's fingerprint, a request ID and `confirmation: "set-quiz-grade"`, is refused when Canvas, the column or the
  cell changed since the preview, and is otherwise ONE CELL's send: registered with `canvas.writes`, refused while a send of the
  term runs, ONE `gradebook_canvas_sends` row (`section_id` the student's, so never a sync and never a course's first write),
  `_run_send`'s receipts, never sent again by itself. It WAITS for its receipt (`QUIZ_WAIT`, 180 s) and answers `status`
  accepted, failed or uncertain, so a step that depends on it (a follow-up's reply telling the student) never goes first on a
  grade Canvas did not take. For the Assistant it is `approve` with that preview (ASSISTANT; one card, "Change the grade of … to
  2 / 2 in Canvas", beside what Canvas showed); no page has a control for it yet.
- SYNC WITH CANVAS (requirement, 2026-10-06: "I want to essentially control everything from faculty. I don't want to have to worry
  about accidentally overriding grades in canvas or canvas overriding my grades. I want just one button that syncs grades to canvas,
  no pull or push, just sync", with one initial import of everything each term, and WebAssign's "ground truth will always be in
  canvas before it is in faculty"; gradebook/README.md "Syncing with Canvas", `GradebookService.canvas_sync_preview` /
  `canvas_sync`, `POST /api/gradebook/terms/<term>/canvas-sync/preview` and `…/canvas-sync`). ONE term-wide action replaces Import
  from Canvas and Send to Canvas (their routes are gone; the one exception is ONE CELL, below): the summary reads every linked course ONCE with no lock held (both
  halves plan from that read; the first live summary, which read every assignment a second time, took 4.4 minutes) and writes
  nothing; Sync (asked, the instructor chose "Summary, then Sync") names the summary's fingerprint, the same options and ticked
  courses, the picks for held grades, a request ID of 8 to 128 characters and `confirmation: "sync"`. Its two halves:
  - FROM CANVAS (below, IMPORT FROM CANVAS: `canvas_import.plan(sync=...)` against the summary's own read; a gradebook change
    since is a 409). An assignment linked in this sync (a new column, or a column it goes to for the first time) fills, with
    Canvas's grade as entered (before its late penalty), only the cells Faculty never filled; an assignment CANVAS GRADES MIRRORS
    Canvas (WEBASSIGN NIGHTLY): WebAssign's, and a CANVAS QUIZ (a classic quiz, submission type `online_quiz`, or a New Quiz, the
    `external_tool` on a "quiz-lti" host; `canvas.web.canvas_quiz`; requirement, 2026-10-06, after the first live summary held 47
    grades students earned on a Canvas quiz since the last import: "Both"), its column marked `canvas` "quiz", read-only in
    Faculty and never sent, like WebAssign's (but for Faculty's counted 0s in its blanks, ZEROS GO TO CANVAS below, and one
    student's grade the instructor sets in Canvas from Faculty, ONE CELL "A CANVAS QUIZ'S GRADE" above); the
    instructor's Take Canvas's picks are written. New columns apply only to the sections
    linked to the courses that have the assignment (every section: no list), and a column limited to other sections is widened,
    as for iClicker. A term's first sync is "Start from Canvas", the instructor's initial import before the semester.
  - TO CANVAS (below, SENDING GRADES TO CANVAS: `canvas_export.plan`, after reading the target assignments again with no lock
    held; a change since is a 409 and nothing is written). Every column Faculty owns goes to its Canvas assignment: every column
    except WebAssign's (`canvas` "webassign") and one the instructor keeps off Canvas (`canvas` "off", Column settings); columns
    Faculty grades itself are Faculty's too. Per student: Canvas shows Faculty's value: same. Canvas shows what Faculty last KNEW
    there (the latest of what Canvas showed after Faculty's accepted send, a value an import from Canvas wrote into the cell, and
    the cell's value at the last sync that left that column and course in step, unless that sync sent it; with a later uncertain
    send's value beside it): Faculty's value goes, added, updated, or taken out (`"posted_grade": ""`) when Faculty's cell is
    empty; Faculty never takes out a grade it never knew. Anything else was changed in Canvas. Into a cell Faculty never filled
    (no value, or emptied by an import) of a column Faculty does not grade itself, Canvas's grade is FILLED (the same "Both"). Any
    other is HELD (asked, the instructor chose "Hold just those for me"): only that grade waits, with Keep Faculty's (sent) or Take Canvas's (written into Faculty as
    an import; never for a column Faculty grades itself), and undecided it stays as it is on both sides. A submission waiting for
    review is left alone; Canvas's form of a grade Faculty sent, alike to the hundredth, is the same grade; scoring a grade Canvas
    excused also sends `"excuse": false`. Canvas's clearing and un-excusing are read back like any value (a receipt says what
    Canvas did).
  - ZEROS GO TO CANVAS (requirement, 2026-10-09, of Canvas's blank Class 2 and Class 3 polls: "Faculty should be pushing its
    zeroes for any assignment, otherwise I have to manually put them in and that defeats the point"). An empty cell the gradebook
    counts as 0 (gradebook.md ZERO ONCE DUE: due, and not waiting for a makeup, a DR booking or a paper), read from the cells as
    the From-Canvas half leaves them, is a 0 to the To-Canvas half (`canvas_export.ZERO`, marked `zero`, kept in its receipt),
    compared by the same rule: Canvas's blank takes it, and a grade Faculty never knew is still filled or held as above. Once the
    cell stops counting, Canvas's 0 is a grade Faculty knew there, so the next sync takes it out. A column Canvas grades
    (WebAssign's, a Canvas quiz's) takes ONLY these 0s, and only where Canvas shows no grade at all; Canvas's own grades always
    stand there, and the From-Canvas half reads the 0 back into the cell. Such a column is never created, published or counted in
    step. The morning sync sends them like any value (NIGHTLY SYNC). The summary names, per column, how many of its grades are
    such 0s.
  - A COLUMN WITHOUT A CANVAS ASSIGNMENT IS CREATED (asked, the instructor chose "Create it in Canvas") once it has grades to
    send, in the Assignment Group named after its category (published, no submission). One Faculty's Canvas copy names (COLUMN
    ASSIGNMENTS, below) is created AS THE COPY HAS IT when the sync can make it exactly so (`canvas_export.from_copy`: published,
    graded in points with the column's points, no outside tool, no description, one due date for every section; its group,
    dates and submission go with it), and the copy's link is recorded with the snapshot a push records
    (`column_assignments.created_link`), so the next push finds it in step; any other waits for the copy's push.
  - AN UNPUBLISHED ASSIGNMENT IS PUBLISHED FIRST (requirement, 2026-10-07, after Quiz 4's grades could not sync in any section
    because Put on Canvas had left its assignment unpublished, the dialog's default: asked, they chose "Publish from Sync", a ticked
    choice in the summary). Sync's plan (`canvas_export.plan(publishing=True)`) marks such a column `publish` (additive, only
    then) instead of a problem; the summary lists each one with grades to send under **Publish in Canvas, then send**, TICKED by
    default, and names an unticked one under Can't sync. Sync's `publish` (column IDs, part of its request) lets those columns
    send: in each course it goes to, the send PUBLISHES the assignment (`PUT …/assignments/:id {"published": true}`) before its
    grades, Canvas read first (one published since is left alone), a receipt (`gradebook_canvas_published`, migration
    `gradebook/010`, owned by the send) recorded uncertain BEFORE Canvas is asked and settled by reading the assignment back
    (accepted, failed, or uncertain), never asked again by itself; not published, that assignment's grades are not sent (failed,
    and the summary offers it again). An unticked column sends nothing and is not left in step. When the assignment is one of
    Faculty's Canvas copy, the copy's assignment is published too (`column_assignments.published_by_sync`: a revision, its module
    items following) and, where the link was in step, relinked as published, so no later push unpublishes it or asks about it.
    Only Sync publishes (one student's cell, a regrade's Apply, still names "not published"); the assistant's Sync never carries
    `publish` (refused for the assistant actor, like `post`). The sync's summary says `publishing` and its send's progress
    `published` (both only when any).
  - The sections ticked: every linked course by default (the From-Canvas half always reads them all); a term's first sync that
    writes to Canvas, started from a section tab, starts with that section's course alone (their "one real section first").
    SYNC EVERY SECTION (requirement, 2026-10-06: "add a sync button to the course homepage that automatically syncs all of them
    (keep the syncs as they are on the individual pages still)"): the course Home's Canvas row has **Sync grades** once a
    section links a Canvas course; it opens the same Sync with Canvas with no section scope, every course ticked, the first
    sync included. The summary and Sync are unchanged (still the instructor's explicit Sync, held grades still theirs).
  - Recorded in ONE transaction: the `import` request (summary `sync`: what it sends, takes, holds and creates, and `in_step`, the
    columns and courses it left in step) with the From-Canvas writes and fills, and the send (`gradebook_canvas_sends`), which then
    runs in a thread (receipts below). Before that, Sync plans both halves again from the summary's own read and reads again only
    the assignments it writes to or takes from (and the assignments of a course where it creates one), a change there being a 409
    with nothing written. A RESTART WAITS FOR A SYNC (requirement, 2026-10-06, "make the restart wait for a sync", after a
    restart cut one off while it read Canvas and the page could not read the answer): Sync registers its write
    (`canvas/stopping.py`) before it reads Canvas, so a stop waits for it; asked to stop before it has recorded anything, it ends
    there, 503 "Faculty is restarting. Sync again in a minute; nothing was written.", and the stop lets that answer out
    (`ANSWER_WAIT`) before the process ends. NO RESTART CUTS A SYNC (requirement, 2026-10-06, "do both", after a restart waited
    its 80 s and then cut a send off with 4 of its 20 assignments unsent): the stop waits up to `STOP_WAIT`, 600 s, longer than a
    whole sync, and AGENTS.md CHECKS has a restart first confirm no sync is sending (`gradebook_canvas_sends` status `running`;
    the journal says "Stopping: waiting for ... grades synced with Canvas"). Every sync records its request, so the gradebook says when it last synced, how many of Faculty's
    grades changed since and what it left held (catalog only). The assistant's summary is a read; its Sync is an Approve card
    (`confirmation="sync"`).
  - A LOST ANSWER (requirement, 2026-10-31, "this is the second time I get this error": a 25-grade and then a 323-grade Sync,
    each sent in full while the window said "The server response could not be read"): when Sync's answer does not come in time or
    is not JSON, the window does not show that error; it asks `GET …/canvas-sync/requests/<request_id>` every 3 s (a read: Canvas
    is not asked) until the sync is "recorded" (its own answer, then followed as usual), and says "unknown" (nothing was written;
    the same Sync may be pressed again, its request ID answered once) or gives up after 15 minutes. "working" while a sync of
    that request ID still reads Canvas or records. The first ask says what the window got (`lost`, journal only), and a Sync
    slower than `SYNC_SLOW` (20 s) puts where its time went in the journal (waitress logs no requests).
  - CANVAS AS SYNC FOUND IT (requirement, 2026-10-08, told a copy per sync is about 4 MB as JSON and a few hundred KB compressed:
    "keep compressed canvas copies on sync then"). Every Sync keeps the Canvas it planned both halves from (the summary's read of
    every linked course with what it read again laid over it): each course's assignment groups, assignments, student enrollments
    and every submission, as gzip-compressed JSON (`gradebook/canvas_snapshot.py`, `gradebook_canvas_snapshots`, migration
    `gradebook/011`), built with no lock held and recorded in the sync's own transaction, one per sync, owned by the TERM through
    its `import` request; immutable and retained. No UIN is kept: a student's SIS ID is replaced by the Faculty membership it
    matches (null when none). With the send's receipts it is Canvas before and after. Nothing decides anything from it (summary,
    plan, answer and send unchanged; no route or page shows it). The morning sync keeps one like any Sync; ONE CELL's sends and the
    morning's score import (a term no sync has written to) keep none.
- POSTING GRADES (requirement, 2026-10-06: "is it possible for faculty to unhide/post grades?", then "yes, plan it": ALWAYS EVERY
  SECTION, Canvas's "EVERYONE", Post AND Hide, a mark on the grid's columns; 2026-10-08: "We change grade posting/hiding to only be
  pushed to canvas during a sync (manual or the nightly), even if I click a column to hide/post faculty will remember that choice
  and do it in canvas on the next sync (probably should have some indication that such a thing is pending)", with a policy that
  "can't hardcode this ... general settings for assignment categories (iclicker is special exception)"; `gradebook/canvas_posting.py`,
  `gradebook/posting_policy.py`, migrations `gradebook/009_canvas_posts` and `gradebook/012_canvas_posting_policy`). Canvas posts and
  hides grades only through its GraphQL API (`POST /api/graphql`, `postAssignmentGrades(input: {assignmentId, gradedOnly: false})`
  and `hideAssignmentGrades(input: {assignmentId})`, read from Canvas's own schema on 2026-10-06 by an introspection read; each
  answers `progress {_id state message}` and `errors`): `canvas.web.Client.post_grades` / `hide_grades`; and sets a posting policy
  with `setAssignmentPostPolicy(input: {assignmentId, postManually})` (read from the schema on 2026-10-08; it answers
  `postPolicy {postManually}` and `errors`, at once): `set_post_policy`. The Canvas assignment of a column in a course is the
  To-Canvas half's own (`canvas_export.targets`). Faculty's last read of a column's posting also makes a POSTED column's empty cells
  count 0 in the gradebook (gradebook.md ZERO ONCE DUE, POSTED, 2026-10-08): computed, and sent as 0 by Sync (SYNC WITH CANVAS,
  ZEROS GO TO CANVAS, 2026-10-09).
  - ONLY AT A SYNC. Nothing posts, hides or changes a posting policy but a Sync with Canvas, the instructor's or the morning one
    (NIGHTLY SYNC; the morning sync's follow-up of a post it withheld for unconfirmed grades is that sync's own: CONFIRMED BY A
    LATER READ). The immediate Post/Hide window and Sync's "Post in Canvas after sending" ticks are gone (their routes answer
    404; a Sync naming `post` is a 400 telling an old page to reload).
  - EACH COLUMN'S DESIRED STATE, the first that applies (`posting_policy.desired`): (1) THE CHOICE: **Post in Canvas at each
    sync…** / **Hide in Canvas at each sync…** / **Back to the category's posting…** in the column's menu (`POST
    …/canvas-posting/choice {item_id, choice: post|hide|rule, base, request_id, confirmation: "choose"}`; `base` the choice the page
    showed, a different one a 409) records the instructor's standing choice (`gradebook_canvas_posting_choices`, TERM-owned,
    append-only, the latest of a column counts) and asks nothing of Canvas; a column kept off Canvas has none. (2) ALWAYS BY HAND
    (instructor: "assignment categories that have 0% weighting or no effect on students' grades must always be manually posted"):
    a column that does not count or is archived and, in weighted mode, one without a category or in a category of weight 0.
    (3) ICLICKER (instructor: "iclicker's are automatically posted"): a column an iClicker session's scores go to is posted.
    (4) ITS CATEGORY'S RULE (GRADEBOOK, the category's `posting`): **after the due date** posts a column Faculty does not grade once
    its due date has passed on the campus clock (due today is not past due); **when certified** (instructor: "Post on
    Certification ... in addition to the other options ... a category that might have mixed assignments") posts a column linked to
    Faculty's grading once its plan is certified. By hand (no rule): nothing. (5) Otherwise it waits for the instructor.
  - POSTED STAYS POSTED (instructor, asked: "Yes, posted stays posted"): Canvas hides a grade that arrives after posting when the
    assignment posts by hand, so every sync posts what Canvas still hides in a column that should be posted. A rule never hides;
    only a Hide choice does, and Hide keeps hiding what shows.
  - MANUAL POSTING IN CANVAS (instructor, asked about a course that posts automatically: "Switch it to manual, but only for
    assignment categories that are set to manual posting in faculty, don't turn everything to manual"): a column Faculty SENDS
    (not WebAssign's or a Canvas quiz's, not kept off Canvas) that posts by hand here (always by hand, or not iClicker's and its
    category has no rule) whose Canvas assignment posts automatically is SWITCHED to post by hand, in each course its sync writes
    to, before its grades go; an assignment the sync creates for such a column right after it is made. Canvas is read first (one
    already by hand is left alone); an `uncertain` receipt (`gradebook_canvas_policies`, owned by the send) is recorded BEFORE
    Canvas is asked and settled by reading the assignment back; a switch that does not take sends that column nothing in that
    course (its values failed, said in the send's errors). Never in a category with a rule, never for a Hide choice.
  - AT EVERY SYNC: Sync plans the posting steps from its summary's own read (`posting_policy.steps`; part of the fingerprint, over
    what is done where, never how many grades) and the summary shows them (`posting_plan`: per column and action, the courses, the
    grades, why, and what waits) in "Posting in Canvas", beside the "Students see these grades" warning. A post WAITS in a course
    where a grade of the column changed in Canvas and waits for the instructor's pick, where its grades cannot sync, or where its
    unpublished assignment is not ticked to be published. After the send (`canvas_posting.after_sync`, under the restart wait
    `after:<send>`), the courses' assignments are read once more and each column's post or hide is ONE `gradebook_canvas_posts` row
    (request ID `policy:<send>:<column>:<action>`) with an `uncertain` receipt per course (`gradebook_canvas_posted`) recorded
    BEFORE Canvas is asked; each course is asked (at most 4 at once), Canvas's progress followed up to 120 s, and every
    submission's `posted_at` READ BACK: `accepted` (every submission posted, or none shown), `failed` or `uncertain`. A post is done
    in a course ONLY when every grade of the column that sync sent there was accepted (otherwise said, not posted: its error names
    the course and assignment with `waits` "grades", which the morning's follow-up reads, CONFIRMED BY A LATER READ). The send's
    status carries its `posts`; the sync's summary records `posting` {switch, post, hide} (only when any).
  - THE GRID'S MARK: what Faculty last read of each assignment's posting (`gradebook_canvas_posting`, TERM-owned, append-only, a row
    only when something changed): every sync's own read (in its transaction) and each sent assignment's read-back, the morning
    run's read, and every post or hide. The View's `canvas_posting` (additive) sums the latest per column with each column's
    `policy` (state, why, manual, switch, forced, rules), its `choice` and `pending` (what the next sync does by that read: post,
    hide, switch; only when any). The heading shows "N hidden" and, dashed, "posts at sync" / "hides at sync" / "by hand at sync"
    (ink: the instructor's own rule at work, not needs-you); its title and the column's menu say why. It can be out of date until
    the next read. Every count is of the course's CURRENT students only (an active or invited Canvas enrollment), the ones Canvas
    lists for an assignment and posts for (requirement, 2026-10-07: WebAssign columns said "10 hidden" while Canvas had nothing to
    post).
  - THE ASSISTANT (requirement, 2026-10-08: "Assistant should be allowed", with an Approve card): its choice is an Approve card
    (ASSISTANT, `confirmation: "choose"`); its Sync stays an Approve card and carries the posting steps its summary showed; a
    category's posting is a setup change, an Approve card too.
- IMPORT FROM CANVAS, now SYNC WITH CANVAS's From-Canvas half (requirement, 2026-09-27: "gradebook import from the canvas
  connection ... how to manage conflicts, columns in one gradebook that are titled differently than those in the other";
  `gradebook/canvas_import.py`). Reads each section's linked course (never writes to Canvas); students by UIN = SIS user ID
  through their own section's course. A Canvas assignment is matched to a column ONCE (title, then `exam_links.canonical`,
  narrowed by points and due date; ambiguity suggests nothing) and then REMEMBERED by its Canvas ID per course
  (`gradebook_canvas_links`, term-owned, append-only), so renames on either side change nothing. The instructor's choices: columns
  linked to Faculty's grading are COMPARE ONLY (never written); Canvas's `score` after late/missing penalties is imported;
  unmatched assignments default to a NEW column (in the category named after the Canvas assignment group; one column per title
  and points across courses), with nothing graded yet too (requirement, 2026-10-02: "it should default to importing all columns
  as they will eventually need to be filled"; one Canvas gives no points waits under Needs your choice for them, and the rest
  imports); grades not posted to students are imported and counted. Excused is excused;
  Canvas's missing flag is `missing` only with the tick; pending review waits. Under SYNC WITH CANVAS no value Faculty holds is
  overwritten from Canvas except WebAssign's (mirrored) and a held grade the instructor takes from Canvas; the earlier KEEPS OTHERS
  rule of Import from Canvas (2026-10-04) is that, made stricter.
  Preview writes nothing; the apply writes what the preview showed (requirement, 2026-10-02: "Write what
  the preview showed", after WebAssign grades syncing into Canvas refused every Import): it plans the preview's own Canvas read
  against the gradebook and refuses (409) any GRADEBOOK change since; it reads Canvas again, refusing any change, only when that
  read is no longer kept (a restart, 30 minutes) or a section links another course. The To-Canvas half still reads Canvas again.
  Gradebook columns no Canvas assignment goes to are listed with a search to link one (otherwise Sync creates it); every match (proposed or remembered) is
  GRADE-CHECKED (requirement, 2026-09-28): grades that differ, and grades only Faculty holds, are counted and flagged per match, and a
  proposed match that disagrees is warned about by name. The check writes and decides nothing. Sending scores
  to Canvas keeps a receipt per posted value (below), so an import does not read Faculty's own value back as a change.
  JEV MATCHES (requirement, 2026-10-03, chosen from the Jev brainstorm as "Canvas gradebook import column matching";
  `jev/matching.py`, JEV CANVAS-MATCH): an assignment matched for the FIRST time whose titles left it ambiguous, unmatched or
  without points is asked of Jev, one request per assignment (its title and assignment group, the live columns' titles and
  categories; never students or scores), the options in both orders. The planner suggests Jev's column (reason `jev`, shown as
  "suggested by Jev: check it", its group opened) only when both orders agree at confidence .8 or more, the column is free in
  that course and worth the SAME points (compared in code); otherwise Jev's column is only shown first under Likely matches.
  Import confirms it and remembers it by Canvas ID like any match, and the grade check still applies. Only the instructor's own
  preview asks (the assistant's READ preview never spends a request); Import never asks: it reads the completed receipts, so it
  plans exactly what the preview showed. A failed or uncertain request suggests nothing and is not resent.
- SIGN-IN AND THE KEPT SESSION (instructor's choice: "NetID sign-in, kept session"; `canvas/signin.py`, `canvas/web.py`). The
  AIM sign-in window signs in to Canvas (`CanvasBrowser`, a subclass of `aim_import.AimBrowser`: `TARGET`, `FINAL`, `_classify`;
  the saved Gradescope sign-in is the NetID sign-in, as for AIM; Duo is relayed, never answered for the instructor). Once a Canvas
  page shows and `/api/v1/users/self` answers, the browser's Canvas cookies are KEPT in the one file FACULTY_CANVAS_SESSION_FILE
  names (drop-in `deploy/faculty.service.d/canvas.conf`: `~/.config/faculty/canvas-session.json`, 0600, written atomically, read
  with the Gradescope file's checks, bound to the Canvas host) and the browser is discarded. This is the ONE place Faculty keeps a
  site's cookies (AIM's rule of keeping nothing stands for AIM), besides Google's for the department's makeup form (ABSENCES
  "DEPARTMENT MAKEUP EXAMS" › GOOGLE SIGN-IN, 2026-10-22). Faculty then uses Canvas's official REST API (and, for POSTING
  GRADES' posts, hides and posting policies, its GraphQL API, as Canvas offers them nowhere else) with that session
  (reads with cookies; writes also send X-CSRF-Token from `_csrf_token`), keeps the cookies Canvas renews (only those a client's own
  answers renewed, and only while the file still holds the sign-in that client opened with: each sign-in gets its own random
  `sign_in` ID, so a client open across a new sign-in never writes the old session over it), follows no redirect and
  only Link-header pages on the same host, and never visits /logout. A 401 is a signed-out session only when Canvas says it has no
  user (status "unauthenticated", or WWW-Authenticate; any other 401 is settled by asking `/api/v1/users/self`): Canvas also answers
  401 to a session it knows that may not do something (a concluded course is read-only), and that is Canvas's refusal, a 502 with
  its words, never the sign-in window (2026-10-04 review: signing in again changed nothing). Keeping the renewed cookies is best
  effort: a session file that cannot be written never turns what Canvas did into a failure. The one exception is a file attached to a message or comment
  (the communications record, 2026-09-30): Canvas's file address is asked with the session, and at most HOPS (4) redirects are
  followed: on Canvas's own host with the session, anywhere else (its file service, then where the file is stored: canvas.tamu.edu
  redirected twice, 2026-09-30) WITHOUT cookies or any header of the session, https only. The Inbox worker reads the kept session too
  (drop-in `deploy/faculty-inbox.service.d/canvas.conf`). Canvas refusing the session is a 401 from `/api/canvas`, and the
  page opens the sign-in window and tries the step once more; other Canvas failures are 502 (never 409/404, which refresh the app).
  "Forget Faculty's Canvas session" deletes the file only. `FACULTY_CANVAS_URL` may name another https Canvas; checks strip every
  `FACULTY_*` and use `tests/fake_canvas.py`.
- A SECTION'S CANVAS COURSE is SECTION-owned (`canvas_course_links`, migration namespace `canvas`, append-only, latest row counts;
  several sections may link one combined course), set on the section tab's Canvas line (GET reads Faculty only; `/courses` lists the
  courses where the instructor teaches and proposes ONE only when exactly one carries the course and section numbers in this term;
  POST with `base`, 409 when stale, only a course Canvas lists now).
- MODULES AND PAGES (requirement, 2026-09-28: "edit the modules from faculty and push the changes etc to canvas because it is very
  difficult to do it for 4 different sections"; "build it, start with modules and pages"; canvas/README.md "Modules and pages",
  `canvas/content.py`, `content_service.py`, migration `canvas/002`, `static/canvas-content.js`, `#course/<group>/home/canvas`).
  ONE TERM-owned copy (immutable revisions), started from one section's course; each Canvas course matched to it (record, then a
  unique name, then the instructor's Same as…), never guessed between duplicates. Page links are rewritten per course (files by
  folder and name); an unfindable link holds that page back there. The preview writes nothing; the push names its fingerprint,
  choices, request ID and `confirmation: "push"`, re-plans (409 when changed) and runs in a web-process thread, each write recorded
  with Canvas's answer. An object edited in Canvas (or never pushed and different) is replaced only when ticked; nothing only in
  Canvas is removed unless ticked, and only what Faculty managed and removed is ticked by default, never a page. PUBLISHING
  (2026-10-04 review): Canvas publishes a module's items with the module, and a page or assignment with its module item, so a
  module's flag is sent only when the copy changed it and always for the module alone (`skip_content_tags`), and an item of a
  page or of an assignment of the copy is published exactly when that page or assignment is (the copy keeps them alike, the
  page's or assignment's own flag deciding; the item's own flag is never sent). A module renamed in Faculty would otherwise have
  published the exam information pages its weeks hold unpublished. LINKS to a page or assignment the push makes are written once
  it exists; a page or assignment linking to one held back in a course is held back there too, and one whose link still waits
  (what it names could not be made) is reported and written again by the next push. An item found only by its title in ANOTHER
  module, with no record, is asked about ("Found by its title in another module") and moved only when ticked. ASSIGNMENTS are
  part of the copy too (requirement, 2026-09-28: "Now build the assignments and grade exporting"; migration `canvas/003`): module
  items point at the copy's assignment, an assignment may name a gradebook column, and one only in Canvas is never ticked for
  deletion. Quizzes themselves are not edited yet; files are FILES below.
- PUBLISHED IN CANVAS STANDS (requirement, 2026-11-03, a screenshot of Quiz 3 under Needs your decision, "Edited in Canvas since
  Faculty's last push", published Faculty `false`, Canvas `true`: put on Canvas unpublished on Oct 2, published in Canvas since with
  no receipt in Faculty, its grades in Canvas since Oct 6, so Replace would have hidden them). An assignment whose ONLY difference
  is that Canvas has published what the copy, unchanged since its push, holds unpublished is not asked about: its verdict is
  `same`, and no push writes it (`_published_since`; `published_in_canvas` on the entry keeps a waiting link from writing it). Each
  morning the copy follows (`follow_published`, in the morning sync, Canvas not written): one revision, request ID
  `canvas-published:<date>:<random>`, when Canvas has published it in every course it goes to, links in step recorded as published.
  In only some courses the copy stays unpublished, since publishing it would publish the others on the next push. An assignment
  the copy took unpublished on purpose (changed since its push) is an update, as before; one with other differences too, a page, a
  module and a Canvas unpublish still ask.
- FILES (requirement, 2026-10-10: "if I need to upload a file to canvas and publish it in a module, I can do it in faculty and it
  be pushed correctly to canvas", then "yes, build it"; `canvas/files.py`, migration `canvas/008`, canvas/README.md "Files"). In
  a module, **File…** among the new items takes a file from the instructor's computer (100 MB at most), and **Replace…** on a
  file item takes a corrected one under the same Canvas name. The file is KEPT at once, TERM-owned (`canvas_content_files`: one
  row and one copy in the term's verified storage per content and name, immutable and retained, checked at every backup by
  `rosters.TermStorage`), and the item names it (`content.file`) beside its folder and name in Canvas's Files; a section's direct
  edit names the term's kept file too, never a copy. Keeping a file contacts nothing: Canvas receives it only with the
  instructor's approved push, whose plan and fingerprint name the upload and the file's checksum. Per course the push puts the
  file in the course's Files where no file has its folder and name, BEFORE the item that shows it. A file already there is left
  alone when it is the one Faculty put there or one of the same size; it is replaced without a question only when Canvas still
  holds the file Faculty put there and Faculty's has changed since; anything else (a file somebody else put there, or one replaced
  in Canvas since) is listed under Needs your decision with both sizes and replaced only when ticked. Two files of that folder
  and name hold the item back. UNFILED (canvas.tamu.edu, 2026-10-16; instructor: "yes, proceed"): Canvas puts a file uploaded
  with no folder in "course files/unfiled", so a file there stands for the top folder's file of its name while the top folder
  holds none (`content.files_by_path`); before this, every push uploaded the Week 8 Take Home Assignment PDF again as "…-1.pdf". Faculty NEVER deletes a file from Canvas: removing the item removes the item. PUBLISHING: Canvas
  publishes a file with its module item, so the item's Published tick decides; a new file whose items are all unpublished is
  unpublished (`locked`) as it arrives, so it never waits published in Files, and a course that keeps a file unpublished (usage
  rights) is reported on the push. RECEIPTS (`canvas_content_file_uploads`, append-only): each upload's answer per course and
  place, `uploaded` with Canvas's file ID, `failed`, or `uncertain` (no answer); nothing is ever uploaded again by itself, and
  the next preview reads Canvas before any approved push sends it. The kept file comes back only as a download (no-store, never
  inline); the upload is the instructor's own form (the assistant has no file to send and does not read one back).
  FROM THE ASSISTANT (requirement, 2026-10-10, of a PDF attached in the chat with "Publish this in the next weekly module for
  students", which the Assistant could only explain how to do by hand: "I want the agent to be able to do this for me"; asked how
  many approvals, they chose "One card: the push"). POST `/api/canvas/content/<term>/files/place` keeps a file the instructor
  attached in the Assistant's chat (named by its `/api/assistant/uploads/<id>` route and read through that route, never bytes from
  a model) or names one the term already keeps, and adds its item at the end of ONE module of the term's copy as one revision
  (`base_revision` checked, a request ID answered once, the same file never twice in a module). It is a draft of the copy, so the
  assistant makes it at once (ASSISTANT tiers: `auto`, Content drafts), recorded with actor `assistant`; nothing reaches Canvas.
  ONLY a chat attachment or a kept file is placed: never a student's paper, a scan or any other Faculty file. The push stays the
  instructor's Approve card from its preview, unchanged. GET `.../outline` is the copy without page bodies (modules with their
  IDs and item titles, the kept files, the courses), so the Assistant finds the module in one read.
  BY ASSIGNMENT GROUP (requirement, 2026-10-06: "sorted into their respective categories on this page instead of just a continuous
  list"): the Assignments tab is one fold per Assignment Group in Canvas's order (the copy's), those without a group first.
- COLUMN ASSIGNMENTS (requirement, 2026-10-06: "a gradebook column has to correspond to an assignment in an assignment category",
  "unless it is a derived aggregate column"; asked, they chose that a new column's assignment appears in Faculty's copy RIGHT
  AWAY and that the Canvas quizzes the copy does not keep are listed, read-only; `canvas/column_assignments.py`). A derived
  aggregate (a category's percentage, the overall, the letter) is computed, never a column. Every column that is not archived or
  kept off Canvas: (1) FACULTY'S (Add column, an iClicker session, a file import's new column, a certified plan's column, linked
  to no Canvas assignment) gets its assignment in the copy after the write that made it (`GradebookService.columns_changed`:
  setup saves, linking, file imports, iClicker and Canvas imports with the after-class and nightly ones, certification; and
  when a copy starts), through the copy's own save under the lifecycle lock, never contacting Canvas and never failing the
  gradebook write: the copy's assignment that stands for it (link, else a name that stands for nothing else) is linked, else one
  is added (its title, points and sections, its category as the Assignment Group, due 11:59 PM campus time on its due date,
  published, no submission). The next Sync with Canvas creates it (above), or the push. SECTIONS FOLLOW THE COLUMN (instructor,
  2026-10-17, of Class 4 and 5 - Poll offered as "Only in Canvas": "that's not true"): an iClicker column gains each section as
  its class meets and Sync creates its assignment in each course it sends to, so the same write (and Sync's own link of what it
  made) widens the copy's assignment, when it is limited to some sections, to each course where its link is the Canvas assignment
  Faculty knows as the column's there; the push then neither deletes nor remakes it. (2) CANVAS HOLDS IT (WebAssign's, a Canvas
  quiz's, or a column linked to a Canvas assignment the copy does not keep): never copied; GET `/api/canvas/content/<term>`'s
  additive `columns.canvas_only` lists it in its category's fold, read-only. (3) NO CATEGORY: `columns.ungrouped`, a needs-you
  line on the Assignments tab linking to the gradebook setup.
  GRADED IN FACULTY, LINKED (requirement, 2026-11-07, of Quiz 7: approved, put on Canvas, then made a column by the morning sync
  from the Canvas assignment the push created, unlinked, so once due it counted 0 for everyone, Sync sent the 0s and its category's
  "after the due date" rule posted them: "quiz 7 was drafted in faculty, why wasn't it automatically linked with that column when it
  was created"): a live column that references no assignment, is not Canvas's own nor kept off Canvas, and holds no grades, whose
  assignment in the copy stands for one of the course's Faculty assignments (its `faculty_assignment`, found by the column's link,
  name or known Canvas assignment) that no live column references, is linked to that assignment's grading as Link and Certify link
  one (`GradebookService.link_copy_columns`, `column_assignments.grading_links`; the parts its grading has in the term, else FRQ;
  one setup revision `grading-link:<uuid>`; two such columns for one assignment, neither). It runs in `columns_changed`, after
  every Sync with Canvas (whose new columns have no values until the next sync plans them) and before the morning sync plans. Its
  empty cells then count only once its plan is certified (gradebook.md ZERO ONCE DUE), and it posts by the "when certified" rule,
  never by its due date (POSTING GRADES). A column holding grades is left to Certify's same-titled `use:` choice.
- FINDING IT, PUTTING FACULTY'S ASSIGNMENTS ON CANVAS, AND ONE SECTION DIRECTLY (requirement, 2026-09-30: "I want to be able to
  create assignments and manage the modules in canvas from my app"; asked, they chose all three and "one real section first";
  canvas/README.md "Finding it", "Putting a Faculty assignment on Canvas", "Editing one section directly"). The sidebar's Canvas
  entry (`#canvas`, Faculty's records; its one action is Message students, below) shows each current course's steps and ONE next
  step while any step is unfinished (folded once all are done: THE CONTENT PAGES, below); a term's first push starts
  with one course ticked (`courses` on preview and push, in the fingerprint). **Put on Canvas…** (assignment cards, the wizard's
  optional Canvas step, an approved quiz draft, a gradebook column) is a revision of the TERM copy (`faculty_assignment`,
  Faculty-side only, like `gradebook_item`), never a write to Canvas; the ordinary push takes it there. Every assignment's Assignment Group must be chosen, in Put on
  Canvas, the copy's editor and a section's direct edit, and a push never makes one without it (2026-10-02: a blank one lets Canvas
  use the course's first group, which put Quiz 3 in Checks for Understanding; "yes make it required there too"). A section's own course
  edited directly (`#course/<g>/section/<n>/canvas`) keeps nothing but SECTION-owned push receipts (`canvas_section_pushes`),
  writes no term links and never changes the term copy; the copy's next push asks about what it changed. The instructor pushes to
  one section first and checks it in Canvas (as of 2026-09-30 Faculty had made no real Canvas write).
- THE CONTENT PAGES (requirement, 2026-10-11: "The canvas menu/pages are a mess and not very user friendly", then, with a
  screenshot of the term's Canvas content page: "the gradebook pages are fine, I am primarily talking about these pages";
  `static/canvas-content.js`, `canvas-content.css`, `canvas-hub.js`, `courses.js`; canvas/README.md "The content pages"). How the
  term's Canvas content page and a section's own Canvas page are laid out. No route, API, record, fingerprint or approval
  changed: Save, the preview and the instructor's explicit Push are as above. Found on the page as it was: an item took three
  rows of controls (142 px, so a module of 22 items ran 3,300 px), 420 px of lead, bars and notices stood above the first
  module, and an assignment chosen far down the list opened its editor above the window.
  - THE BAR stays in view under the top of the window while the page scrolls. On its left, where the copy stands: when it was
    saved and its revision; a push that went through as ONE quiet line (when, how many changes, each course to check it in
    Canvas), with "the copy has changed since" when a revision was saved after that push (a statement of the record, never a
    claim about Canvas, which only a preview reads). On its right the page's ONE next step: Save (with Discard changes) while
    there are unsaved changes, which the bar marks as needs-you, else Push to Canvas…; on a section's page Review and push…,
    else Read Canvas again. A push that is running, failed, was interrupted, cancelled or stopped is a notice under the bar.
    No lead stands under the header band.
  - THE TABS above it, on the Canvas pages only, say whose Canvas course is open and stay in Canvas: All sections (the term's
    copy) and each section (that section's own Canvas course, read from Canvas as before).
  - MODULES: one row a module, marked when it is unpublished or limited to some sections, and saying when it opens only while
    that is still ahead. Open, it shows its settings and its items, ONE LINE an item: what it is, its title, what it shows (the
    page, the address, the file; a quiz's or an assignment's name only when that is not its title), Published, Move up and
    Move down. Indent, Outdent, Move to another module… (a small window; the item goes to that module's end) and Remove from
    this module are the item's ⋯ menu. After a move the focus goes with what moved.
  - ONE CLICK, ONE CHANGE: the module editor's handlers are bound ONCE to the page's host and read the draft as it is when they
    run. Bound again at every draw, as they were, each redraw added another set: after a few, one Add item added several
    items, Move down undid itself, and Remove or Delete module took more than was asked. `tests/browser_canvas_content.py` holds it.
  - PAGES AND ASSIGNMENTS: the list stays beside its editor and scrolls in its own box, so a row far down opens its editor in
    view. A row is its name; an assignment's has one muted line under it (points, due date, Gradebook column, Faculty
    assignment) and a chip only for what needs a look (no Assignment Group, unpublished). Find an assignment leaves the rows
    whose name or group holds the words. BY ASSIGNMENT GROUP and COLUMN ASSIGNMENTS are unchanged.
  - A SECTION'S PAGE marks what the term's copy keeps in step with the quiet words "term's copy" (never a chip on every row),
    under ONE sentence that says what a change there leads to.
  - THE CANVAS PAGE (`#canvas`) leads with each current course's way into its content and its sections. The four setup steps
    and the next step show while any is unfinished and fold once all are done; a course is marked only when a push has not
    reached it while others have the copy.
- SENDING GRADES TO CANVAS, now SYNC WITH CANVAS's To-Canvas half (the same request; `gradebook/canvas_export.py`, migration
  `gradebook/003`): a column goes to the assignment an import or a sync matched it to, else the one the copy made for it, else
  one the sync creates: when the column's category names exactly one assignment group and Canvas has no assignment of its
  title, it is created there (its title and points, published, no submission) and recorded (`gradebook_canvas_created`) before
  its values are sent. COURSES AT ONCE (requirement, 2026-10-06, "do both", after 20 assignments sent one after another took
  seven minutes): the send writes to its Canvas courses at once, a thread per course (at most `SEND_COURSES`, 4), each course's
  assignments one after another; a cancel, a signed-out session or a failure in one course stops every course before its next
  assignment. Canvas's throttle (403 "Rate Limit Exceeded") refuses a request before Canvas acts on it, so the send's client
  asks once more after `THROTTLE_WAIT` (3 s); refused again, those values are failed. Other Canvas writes do not retry.
  Students by UIN = SIS ID exactly. A grade changed in Canvas is held (SYNC WITH CANVAS); an empty cell
  takes out only a grade Faculty knew there; nothing goes to a pass/fail or differently-worth assignment, nor to an unpublished one
  unless the instructor ticked it to be published first (SYNC WITH CANVAS, AN UNPUBLISHED ASSIGNMENT IS PUBLISHED FIRST). Every value sent is read back and kept as a receipt (accepted, failed
  or uncertain) per section membership, recorded as uncertain BEFORE Canvas is asked and settled by the read-back, so a send cut
  off in between keeps a receipt for every value it may have written; Canvas refusing the update itself is failed; an uncertain
  one that a later read shows as sent gets an accepted receipt then (NIGHTLY SYNC, CONFIRMED BY A LATER READ). What Faculty
  last knew in Canvas is what SYNC WITH CANVAS reads from these receipts, the cell's history and the last sync. One Canvas assignment takes one column's grades: when an
  import's match and the copy's link name two columns for one assignment, neither is sent and both say why (2026-10-04 review).
  WEBASSIGN'S ASSIGNMENTS ARE NEVER SENT (requirement, 2026-10-06, asked: "Canvas always wins"; before, 2026-10-04: "if I try to
  push, I don't want it to overwrite the true scores in canvas", "left out unless I tick them"): a column WebAssign grades is
  Canvas's and is not in the To-Canvas half, except that a blank Canvas shows where the gradebook counts 0 takes that 0
  (ZEROS GO TO CANVAS, requirement, 2026-10-09: "for any assignment"); no grade Canvas holds is ever overwritten.
- WEBASSIGN NIGHTLY (requirement, 2026-10-04: "can we wire faculty to automatically import webassign grades?", "Nightly at 10 pm
  Central", then "I want updates from webassign directly, not canvas", "can we use LTI integration?" and, told that Cengage's LTI
  sends grades only to the course's LMS and that WebAssign sends each score to Canvas as it is submitted or edited: "I wanted to
  import them directly because my gradebook will have out of date scores"; `gradebook/webassign_nightly.py`). WebAssign's scores
  come through Canvas: Cengage offers instructors no API, and scraping WebAssign would read the same scores less reliably. An
  assignment is WebAssign's when Canvas hands it to Cengage's tool: submission type `external_tool` and a tool address on
  cengage.com, webassign.net or webassign.com (`canvas.web.tool_host`, `graded_by_webassign`; all 26 of MATH 140's named
  gateway.cengage.com on 2026-10-04, while Gradescope's tool assignments are also `external_tool`). Nothing names a course or a
  column, so a new term's WebAssign assignments are found the same way. Since 2026-10-08 it is part of the MORNING SYNC (NIGHTLY
  SYNC, below; the 10 pm loop no longer runs): a term a Sync has written to gets the whole sync, whose From-Canvas half does this
  for WebAssign's assignments; a term no Sync has written to runs Sync with Canvas's From-Canvas half BY ITSELF for those
  assignments only: the plan the instructor's own sync makes with no choice (remembered match, the one column of the same title,
  else a new column in the category named after the assignment group; Jev is not asked), written through that import's own apply.
  CANVAS ALWAYS WINS (requirement, 2026-10-06, asked about a WebAssign cell edited in Faculty): the column MIRRORS Canvas, its score
  (after Canvas's penalties) written over any value, a typed one included, and a blank in Canvas emptying the cell; Canvas's
  missing flag is not recorded and pending review changes nothing; the request's summary is `automatic: "webassign"`. A column a
  WebAssign assignment goes to is marked `canvas` "webassign" in the same setup revision (`gradebook/setup.py`: only Faculty's
  own revision marks it or takes the mark away): read-only in Faculty (every score write, bulk mark, paste and file import
  refuses or skips it, Faculty's grading never takes it) and never sent. Sync with Canvas does the same for WebAssign's
  assignments. Since 2026-10-06 it mirrors Canvas quizzes the same way (marked `canvas` "quiz"; SYNC WITH CANVAS); due dates
  follow Canvas for WebAssign's assignments and, since 2026-10-08, Canvas quizzes (below). An assignment the titles cannot place
  waits for the instructor's own sync and the rest is imported (PIPELINE: a flag holds back only its own item). A morning with
  nothing to write records no request. The 10 pm import's attempts (`gradebook_nightly_runs`, migration `gradebook/007`) are kept
  as history; the morning's are NIGHTLY SYNC's. The import reads Canvas with the kept session and writes nothing to Canvas or
  WebAssign.
  LEFT OUT OF IMPORTS (requirement, 2026-10-04: "skip Graphing Practice in the import"): the waiting notice's Leave out records the
  waiting assignments as skipped (`POST /api/gradebook/terms/<term>/canvas/skip`, `gradebook_canvas_links` with no column, one
  `import` request with summary `skipped`, no Canvas read, no score), so the nightly import and Sync with Canvas leave them out
  from now on. A sync's own skip of what it suggested skipping is still not remembered (the dialog sends every choice back,
  so it cannot tell one the instructor made); choosing a column for a left-out assignment in Sync with Canvas brings it back.
  WEBASSIGN DUE DATES (requirement, 2026-10-05, asking how to extend a WebAssign homework without messing up Canvas, told that
  WebAssign's LTI Advantage integration overwrites the Canvas due date itself but that the gradebook column and Faculty's Canvas
  copy kept the old one: "Is it possible to have faculty ... update those dates automatically?", then "Yes, extend it to also
  update the due date in the gradebook and canvas content page"; 2026-10-08, of Canvas quizzes, told that an extended video quiz
  would otherwise post on its old date: "Yes, follow Canvas"). Each morning, after Canvas is read and BEFORE anything is planned
  (so "after its due date" reads Canvas's date), for WebAssign's assignments and Canvas quizzes only: (1) each linked gradebook
  column takes Canvas's due date (a campus date; the LATEST of its linked
  courses when the sections' dates differ, since a column has one), unless its date was last set by anything but an Import from
  Canvas or this step (a date the instructor typed stays; a column made without one takes one); (2) Faculty's Canvas copy takes
  Canvas's date for each course Faculty has synced the assignment with, unless the copy's date for that course's sections was
  changed since that sync by anything but this step (a date changed in Faculty and not yet pushed stays, and the push asks about
  it as before); the moved sections get section dates when the courses differ. Canvas without a due date moves nothing. Each is
  one revision whose request ID starts `webassign-due:` (the gradebook's setup and the copy, both term-owned; no migration); where
  Canvas changed nothing but that date, the copy's link is recorded as alike again, so the next push has nothing to ask.
  AVAILABLE DATES (requirement, 2026-10-17, of WebAssign's 4.1 and 4.2 shown as "Edited in Canvas" when "no one has changed
  anything in canvas"): WebAssign writes its available-from and until dates into Canvas too, so for WebAssign's assignments (not
  Canvas quizzes) the copy also takes Canvas's `unlock_at` and `lock_at` on the same conditions, when every course Faculty has
  synced the assignment with holds the same one (the copy has one for all sections; courses that differ are left to the push,
  which asks as before); in the same revision, recorded under `available`. The
  gradebook's Changes page names its revision ("Due date updated from Canvas overnight"), and the Calendar
  (and Google, by its own sync, when gradebook deadlines are synced) follows the column. One student's WebAssign extension never
  reaches Canvas, so nothing follows it. A failure here is logged and counted in the morning's detail and never changes the run.
  A section's class schedule (SCHEDULING "CLASS SCHEDULE", 2026-10-10) reads the copy's due date for that section's course
  (`due_for`), never Canvas itself, and marks a column whose date differs from it only when the column is NOT that latest date.
- NIGHTLY SYNC, THE MORNING SYNC (requirement, 2026-10-08: "in addition to the manual canvas sync button, faculty automatically syncs
  with canvas every night (in the early AM to ensure it's not being worked on)"; asked what it may do unattended: "Everything sync
  defaults to except on a term's first sync"; the 10 pm WebAssign import folded in; `gradebook/canvas_nightly.py`,
  `GradebookService.canvas_sync_nightly`, migration `gradebook/013_canvas_nightly_runs`). From 05:00 campus time (after the 04:00
  nightly backup and its lifecycle lock) until 08:00, in the WEB process (its Canvas writes are registered there, so a restart
  waits for them, canvas/stopping.py; never in the Inbox worker), every current term with a linked Canvas course is run once a
  morning under one storage lock (the 10 pm import's, so a worker not yet restarted cannot overlap it): Canvas is read once with no
  lock held; what it shows of each column's posting is kept for the grid's mark; WEBASSIGN DUE DATES follow (above); then:
  - A TERM A SYNC HAS WRITTEN TO (a grade Canvas accepted, or an assignment created, by a Sync; one student's cell is not one) gets
    Sync with Canvas BY ITSELF from that read, exactly as the instructor's Sync with its summary's defaults: new columns for Canvas's
    new assignments, Canvas's grades in the cells Faculty never filled, WebAssign's and Canvas quizzes' mirrored, missing Canvas
    assignments created, unpublished ones with grades to send published (unless that column's last publish is uncertain), and each
    column's POSTING GRADES steps; the summary's fingerprint, request ID `canvas-nightly:<date>:<random>`, `automatic:
    "canvas-sync"` on its request (and `sync.automatic`), its snapshot and its send as any Sync's. It WRITES ONLY to the Canvas
    courses a Sync has written to before: a newly linked section's course is read and its grades taken, never written, until the
    instructor's own Sync (their "one real section first"). Held grades stay held (no pick is ever made for the instructor); Jev is
    not asked; a plan that needs the instructor's choice of column sends nothing (`needs_choice`; WebAssign's and Canvas quizzes'
    scores are still imported). NOTHING UNCERTAIN IS SENT AGAIN BY ITSELF (AGENTS.md OUTSIDE SERVICES): a value whose latest send
    receipt is uncertain (`canvas_export.plan(automatic=True)`, counted `unsure`), and a post, hide or switch whose latest receipt is
    uncertain, wait for the instructor's own Sync, and the grid says so. A gradebook change between its plan and its write is
    planned once more from the same read. A morning with nothing to do records no request.
  - A RESTART WAITS FOR THE MORNING SYNC (requirement, 2026-10-17, "yes" to "don't restart while the sync is running" instead of
    the fixed 05:00–08:00 no-restart window): each term's morning, its follow-up rounds included, registers with `canvas.writes`
    ("the morning sync with Canvas", key `morning:<term>`) for as long as it reads, plans, sends or follows up, so a stop waits for
    it and Apply refuses while it runs. Between rounds nothing is registered and a restart may go ahead. The one check before any
    restart is `GET /api/assistant/restart-check` (read-only; `{"idle", "busy", "canvas_writes"}`, the counts Apply refuses on).
  - A TERM NO SYNC HAS WRITTEN TO (its first sync is still the instructor's): WebAssign's and Canvas quizzes' scores are imported
    (WEBASSIGN NIGHTLY), nothing is written to Canvas.
  - CONFIRMED BY A LATER READ (requirement, 2026-10-10, shown that morning's sync: Canvas took its five grades but had not applied
    them when Faculty's two-minute wait ended, so all five stayed uncertain, two columns' posts were withheld and the gradebook
    kept two warnings, although Canvas showed every grade minutes later; offered "a later read that shows the sent values could
    mark them confirmed and carry out the skipped posts" and one banner for both, they said "make both changes";
    `gradebook/canvas_confirm.py`). Reading Canvas is never a resend. Whenever a sync reads Canvas (the instructor's Sync, in its
    own transaction; the morning run's one read, before it plans), every value whose LATEST receipt is uncertain and which Canvas
    shows as Faculty sent it gets one more receipt, `accepted`, on the send that sent it (append-only; that send's recorded
    progress stands). A value Canvas does not show as sent stays uncertain and still waits for the instructor's own Sync; a
    failed one is never changed; the summary still writes nothing. THE MORNING'S FOLLOW-UP: while the morning sync's send is the
    term's last sync and holds unconfirmed values or withheld posts, each later round of that morning (every five minutes until
    08:00, the same process and storage lock) reads ONLY those assignments again with no lock held, records what Canvas shows
    (and their posting, for the grid's mark), then carries out each post that sync withheld in a course ONLY because a grade it
    sent there was not confirmed, once every such grade is accepted: ONE more `gradebook_canvas_posts` row per column and course
    (request ID `policy:<send>:<column>:post:<course>`, `progress.confirmed` naming the course), under the restart wait
    `after:<send>`, Canvas read first and asked only where it still hides a grade and the column should still be posted by its
    choice or rule as it is then (otherwise the row is done with nothing asked); never asked twice, and its own uncertain receipt
    waits for the instructor like any. Nothing is followed once a later sync is the term's last, while the send or its posts
    still run, or while Faculty stops; a read Canvas refuses changes nothing and the next round reads again; a follow-up records
    no run (its receipts and post rows are the record). The View's `last_sync.send.unconfirmed` (only on a send that ended with
    failed or uncertain values) counts that send's values whose latest receipt is still not accepted, and `nightly.posts_failed`
    the columns whose post or hide still needs the instructor (`canvas_posting.outstanding`), only while that morning's sync is
    the term's last (a later Sync has tried again where Canvas still differed). ONE NOTICE: the grid says what the
    last sync's send left unconfirmed once, with the posts the morning sync could not make for it in the same sentence and one
    Sync with Canvas button, and says nothing once a later read has confirmed them all.
  - Each attempt is a `gradebook_canvas_nightly_runs` row (TERM-owned, append-only: the campus date, `kind` sync or import, the
    status synced, imported, unchanged, none, needs_choice, signed_out or failed, counts and waiting titles, never a student; the
    request and send it made). A morning that could not read Canvas, or whose sync failed, is tried again each hour until 08:00,
    then the next morning; a stopping Faculty starts none and records nothing. The View's `canvas.nightly` (the latest run; the 10 pm
    import's before there was one) and the grid name it only when it needs the instructor: signed out (Sign in to Canvas), a
    failure, assignments waiting for a column, grades it left to the instructor's own Sync, posts or hides that did not take
    (unless the last sync's notice says them: ONE NOTICE, above); held grades and a send Canvas did not confirm are the last
    sync's own notice. Sync's header title says "by the morning sync".
- MESSAGE CHECKS (requirement, 2026-10-03; docs/decisions/jev.md SEND-CHECKS): the Message students review shows notes from
  `POST /api/canvas/messages/check` (dates against the chosen sections' exams and due dates, a date that differs by section, a
  student named to everyone). Canvas is not read for it; Send and the fingerprint are unchanged.
- MESSAGES TO CHOSEN SECTIONS (requirement, 2026-10-01: "In the canvas page, I want an option to send mass mail through canvas where
  I can select the sections I want to send it to"; canvas/README.md "Messaging sections", `canvas/messages.py`,
  `static/canvas-message.js`, migrations `canvas/005` and `006`). The Canvas page's Message students: tick current sections with a
  linked course, write, review, send. Who it reaches is read from Canvas at review and send and matched by UIN = SIS ID exactly: a ticked section's
  current students; a student on no roster here only when every section linking that course is ticked; never a student of a section
  not ticked, a dropped student or an enrollment Canvas does not list as active; the review names everyone left out and why, and
  says for each course how many current students its ticked sections have and how many of them get the message in some course
  (`roster`, `roster_reached`; 2026-10-12, for the Assistant's card, ASSISTANT "CANVAS MESSAGE CARD"), neither a part of the
  fingerprint. One
  request per course, as Canvas's own inbox sends it. GROUP BY DEFAULT (requirement, 2026-10-01: "I'd rather send one mail to every
  student in the section so my mailbox doesn't get flooded ... defaults to group send"): one conversation per course that every
  recipient shares; a course over Canvas's 100-person group limit goes individually and the review says so. Canvas's "Send an
  individual message to each recipient", unticked by default, gives each student their own conversation (`bulk_message`; sync up to
  100 recipients, async above). The Assistant never ticks it by itself (2026-10-13; ASSISTANT "CANVAS MESSAGE CARD"). How it is sent is part of the approved fingerprint and recorded per course (`kind`). Explicit Send only: the review's fingerprint, a request ID and `confirmation: "send"`;
  the message and each course's recipients are recorded before Canvas is asked and Canvas's answer after (accepted, failed,
  uncertain); a request ID is never sent twice and nothing is resent automatically. WORKSPACE-owned receipts, append-only. The
  courses go in a thread of the web process after the receipts are committed and the window follows the record; a STOP WAITS
  (requirement, 2026-10-01, "yes fix that", after a restart cut their first send off): the SIGTERM handler refuses new sends (503) and
  lets a running one finish, at most `STOP_WAIT` before the process stops (600 s within systemd's TimeoutStopSec of 630,
  `deploy/faculty.service.d/stop-wait.conf`, since 2026-10-06; 80 s of systemd's 90 before). Since 2026-10-04 (review) the stop waits for
  EVERY write to Canvas that may still run (`canvas/stopping.py`: messages, pushes of the copy and of one section, grades sent, grades
  posted or hidden (POSTING GRADES),
  replies posted from the Mail page; since 2026-10-06 a Sync with Canvas from before it reads Canvas, see SYNC WITH CANVAS), each
  refused with 503 once Faculty is stopping; the handler only closes the door and returns
  (waiting in waitress's own loop answered nothing), a thread waits, then the handler it replaced ends the process
  (rosters._terminate closes the catalog's idle connections). Check
  delivery reads Canvas and records nothing. First real send 2026-10-01 (individual, all four MATH 140 sections): a restart of live
  mid-send left two courses without an answer (one had gone, one had not); the instructor sent the missing section again.
- MESSAGES SENT LATER (requirement, 2026-11-07; docs/decisions/mail.md MAIL-SEND-LATER, which holds the rule for both channels): the
  review's Send later… keeps the reviewed plan (`approved`: the words, the files by route, and exactly who gets it in each course)
  and Faculty sends it at its time with `send_approved`, which reads nothing from Canvas or the rosters but the Faculty files (one
  that changed refuses it, 409) and records and sends through the same receipts and thread as Send (`_start`), under the request ID
  `later-<id>`. It waits while Faculty is signed out of Canvas or stopping, at most two hours, then is held for the instructor.
- MESSAGES ATTACHMENTS (requirement, 2026-10-09, of Canvas's own compose window and its paperclip: "canvas allows attachments in the
  messages", then "yes"; migration `canvas/007`). A message to sections may carry up to 10 files, 25 MB together: from the
  instructor's computer (Attach from your computer, sent with the review and the send as a multipart form) or Faculty files named by
  the routes that serve them (Attach from Faculty…: lecture notes and saved files, never a student's paper; the Assistant's cards name
  routes the same way, a file attached in its chat included, as MAIL-ATTACHMENTS does). The review lists them and the fingerprint
  covers each file's name, type, size and SHA-256 (no key without files), so a file changed since the review is a 409. Canvas takes a
  message's files only from the sender's own "conversation attachments" folder, so Send uploads each file there once, never to a
  course's files (`web.Client.upload_message_file`: Canvas's three documented steps; the bytes go to Canvas's file service with no
  cookie or header of the session, https only, and the upload is confirmed on Canvas with the session), then sends every course with
  the same `attachment_ids`. WORKSPACE-owned receipts: the files approved (`canvas_message_attachments`: name, type, size, digest and
  source route or "computer", never the bytes), written with the message before any request; each upload's answer
  (`canvas_message_uploads`: uploaded with the Canvas file ID, failed or uncertain). A file Canvas refuses, or does not answer for,
  sends no course ("Not sent: …"); nothing is uploaded or sent again automatically, and an uncertain upload may leave a file in that
  folder that no message names. First real upload untested against canvas.tamu.edu: its first send is the test.
- REPLIES FROM THE MAIL PAGE (requirement, 2026-10-02; docs/decisions/mail.md MAIL-CANVAS-POST): Reply on a Canvas message
  notification posts the instructor's reviewed words in that Canvas conversation (`add_message`, to the answered author only), with
  the kept session, after an explicit Post in Canvas. Its receipts are the Mail page's (WORKSPACE). Faculty's only other write to a
  conversation is Message students (above).
- COMPARE ROSTER (`CanvasService.roster`) is a read-only view: the section's CURRENT memberships beside the linked course's
  student enrollments, matched by UIN = Canvas SIS user ID EXACTLY (nine digits); in both, only in Faculty, only in Canvas, in
  another section's roster here, without a university ID, or dropped here while Canvas still lists them. Nothing is imported or
  changed by the comparison; JSON carries names and enrollment IDs, never a UIN. Its "only in Faculty" list offers the roster's
  own Drop (ticked students, confirmed once more, `source: "canvas"`), and its dropped-but-in-Canvas list offers Restore.
- DROPPING A STUDENT (requirement, 2026-09-27: "an option for me to drop the student to avoid future conflicts"; their choices:
  keep every existing record, and a re-import leaves them dropped and says so). `rosters.set_dropped` (POST
  `/api/course-groups/<g>/sections/<n>/roster/drop|restore`, workspace only, request ID) appends a `roster_drops` row on the
  SECTION MEMBERSHIP; the latest row counts; nothing is deleted, and the membership, roster payload and every reference stay.
  Views: `roster_dropped` (dropped now) and `roster_current` (memberships not dropped). Current work reads `roster_current`:
  section counts, new seating (options, generation; `update` keeps a seat a dropped student already holds, `regenerate` removes
  it with reason "dropped from the roster"; a dropped student without a seat is not listed on the chart), `_check`'s missing seats,
  new response-card issues and the card audience picker, new exam participants and roster additions, the gradebook term count.
  Records stay: an existing participant stays one (`_participants` marks `dropped`); Certification leaves out a dropped participant
  with NO work at all (never a confirmed 0; `counts.dropped_left_out`; a dropped student with work is certified as usual; the
  participants digest names dropped participants only when there are any, so earlier certifications keep theirs); the gradebook
  keeps their row with a DROPPED badge and never fills it in bulk. A roster import that still lists a dropped student leaves them
  dropped (`last_import.still_dropped`). The roster panel lists dropped students under their own Dropped filter; the student
  window offers Drop from Section N… (confirmed) and Restore. The copy-assignment picker still lists every membership.
- LEAVING THE SECTION (requirement, 2026-10-15: "does the roster import handle student withdrawals and q drops?", then "yes build
  it"): Howdy keeps a Q-dropped or withdrawn student on the class roster (Reg Status "Q-Drop State Limited" with Final "Q",
  "Withdraw After Census Date" with Final "W"; `rosters.leaving_reason`). It records the section's
  students the file no longer lists (`last_import.missing`, enrollment IDs, only when there are any), and the roster response
  lists `leaving` (only when there are any): each current student whose row reads Q drop, Withdrawn or Dropped, or whom the
  latest import left out. The roster shows them in one callout with one Drop button, confirmed once, which sends the roster's own
  Drop (source `roster`) once per reason, so each drop records why. A student the instructor restored is not offered again. Import rosters names them in each
  file's result ("Leaving: name (reason)") with a link to that section's roster.
  - THE REGISTRAR'S MARK IS A DROP (requirement, 2026-10-25, a screenshot of the Assistant counting Greta Lara Castillo, Q drop
    on 508's roster since 2026-10-15, as current because she was not dropped): an import drops each student whose row reads Q
    drop, Withdrawn or Dropped and who is not dropped already, in the same transaction as the marks: a `roster_drops` row with
    source `roster`, reason "<reason> on the registrar's roster", request `registrar-<import time>`, actor `roster import`
    (`last_import.registrar_dropped`: id, name, reason, sorted by name). It is the ordinary drop in every respect (DROPPING A
    STUDENT, above; GRADING "A DROP NEVER ASKS TO CERTIFY AGAIN"), so Restore undoes it, and a student the instructor ever
    restored is never dropped by an import again. A student the file left out is still only offered (`leaving`). The roster
    says what the latest import dropped while those students stay dropped; Import rosters says "Dropped: name (reason)". Such a
    student is not counted in "still lists N students you dropped" (`still_dropped`). Marks from imports before this rule were
    dropped once by the roster's Drop (Greta Lara Castillo, 508, 2026-10-25).
- NO SEAT OR CARD FOR A Q DROP OR WITHDRAWAL (requirement, 2026-10-15: "it's not necessary to actually drop them, but we need to
  make sure that withdrawn or q drop students don't get seats in a seating chart or response cards"): each import replaces the
  section's `roster_registrar_marks` (membership, reason Q drop / Withdrawn / Dropped, the import's time) from its saved rows; view
  `roster_leaving` leaves out a membership the instructor ever restored. Seating never seats them (options,
  new charts, regeneration, and an update gives up a seat they hold, reason "<reason> on the registrar's roster"), a chart's check
  owes them no seat, and a chart lists them only while they hold one. Response cards issue them no new card and leave them out of
  the audience picker. Since 2026-10-25 the import also drops them (THE REGISTRAR'S MARK IS A DROP, above), so grades, mail,
  follow-ups and exams treat them as dropped; a student the instructor restored is treated as current everywhere.
- IMPORT FROM HOWDY (requirement, 2026-11-01: "can we build a way to import the rosters directly from howdy"; `howdy_import.py`):
  Import rosters' Import from Howdy reads each of the course page's sections' class roster from Howdy and imports it exactly as an
  uploaded export (the section's ordinary roster import, one file per section: added and updated, nobody removed, the registrar's
  marks dropped as above). Howdy has no export address: its Instructor/Associate › My Courses page reads rosters as JSON from Howdy's
  own API (POST `/api/get-instructor-courses` {termCode}, POST `/api/class-rosters` {crn, termCode}, Bearer token the page holds) and
  its Export Roster to CSV builds the file in the browser (mapped read only in the instructor's Chrome, 2026-11-01).
  - SIGN-IN: the AIM sign-in window (`aim_import.AimBrowser`, `HowdyBrowser`) with the saved NetID sign-in, Microsoft and Duo, from
    Howdy's own Sign In (`/uPortal/azureSignIn`). Howdy sits behind Cloudflare, which never lets a headless browser through, so this
    browser alone (`HEADLESS = False`) is an ordinary Chromium on a private Xvfb display (`aim_import.virtual_display`, no TCP).
    Cloudflare's check passes by itself there; if it ever does not, the window shows the page (as for an unknown step).
  - READ ONLY: once signed in, the browser opens My Courses once, takes the token Howdy's page sends to Howdy's API (or uPortal's
    userinfo, the page's own source), and from Howdy's page asks for the term's courses and each chosen section's roster; nothing
    is written to Howdy. The token, cookies and rows stay in memory in that browser; the browser and profile go once the rosters
    are read, and the files wait KEEP for the window. Nothing of a row, the token or a cookie is logged.
  - THE TERM: the course page's Fall/Spring/Summer term and year as Howdy's code at College Station (year, season 1/2/3, campus 1:
    Fall 2026 = 202631); when it lists none of the course, other campuses' codes of that season from Howdy's term list. Sections
    match Howdy's COURSE ("MATH-140-507") by course code and section number. An "Other" term is refused before signing in.
  - THE FILE: Howdy's export columns in its order and wording (`howdy_import.COLUMNS`: Name, UIN, Reg Status, Major, Class, Degree
    Candidates, Midterm Required, Midterm, Final, Updated Final, Email), named as Howdy names its export
    (`MATH-140-507_202631-11-01-2026.csv`). Howdy's "Status" column (Minor) has no field Faculty has seen and is left out, so a
    student's earlier value stays. A section Howdy does not list, or whose roster it does not give, is named under the results;
    the others import.
- OFFICE HOURS FROM THE TERM SCHEDULE (2026-10-05, SCHEDULING "CANVAS OFFICE HOURS"): the copy's Office Hours page can be redrawn
  from the term's saved Term schedule by the instructor's explicit "Put in Faculty's copy" (a revision of the copy, its heading and
  pictures kept); Canvas changes only on the ordinary Push.
### Conflict review (2026-10-09)

The instructor requested field-level conflict explanations and prepared resolutions. The ordinary term and section push
previews expose bounded current Faculty/Canvas values for differing fields. Historical snapshots remain hashes; no earlier
wording is invented. The fingerprint includes the full compared values' digest, so another remote edit invalidates the
review even when the conflict category is unchanged. Long values are marked truncated and cannot be applied from that view.
Compare changes can copy/edit text into the existing term or section draft by explicit action. It neither saves nor pushes;
the term draft says it is shared. Ask assistant explicitly sends the displayed comparison for a review-only conversation,
preserving the dialog and its unsaved choices. No section edit automatically propagates into the term copy. Existing owner
revisions, re-preview, explicit Push, receipts and no-retry rules remain authoritative.

Several conflicts in one window (requirement, 2026-10-09: "Fix all of the issues and implement your suggestions"). Text typed
under Compare changes is kept, and its comparison reopened, when the preview is read again (a tick, a pairing, a course).
"Use this text" no longer closes the push window: the row reads "Resolved in your draft · left as is in Canvas for this push",
its Replace tick is disabled (a "Replace all" leaves it out), and "Don't use this text" sets it aside. While any text waits,
the preview is stale, so the footer's Push gives way to "Add N texts to the draft" (into the draft, then the window closes)
and "Save and preview again" (term: the ordinary Save of the copy) or "Preview again" (section: the draft as edited). Neither
writes to Canvas; Push returns with the fresh preview and stays the instructor's explicit action, with its fingerprint and
owner checks unchanged. Canvas push, pairing, gradebook and message failures are danger toasts that stay until dismissed.
