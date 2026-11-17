# Frontend modules

Claude API fallback (GRADING "CLAUDE API FALLBACK"): the Claude connection editor and grading/rubric model rows show one
opt-in checkbox for direct Anthropic billing after the subscription limit. It belongs to the shared connection, saves with the
ordinary versioned Compute settings draft, and remains absent from other subscription providers and Assistant/coding model rows.
No credential value is requested or displayed in the browser.

Memos, training and search by meaning (2026-11-14, RECORDINGS step 6). `recording-items.js`: `outcomeMarkup` uses
`MEMO_GROUPS` (You asked, Rules you said) for `meeting` `memo`, each heading with its count and, in orange, how many of its
items need the instructor (`item.yours`); `readingLine(reading, memo)` says "One voice, taken as yours." or, in orange, why
Faculty was not sure (`memo.why`). `askMarkup(item, e, {card})` draws a request's run under its quote (`.recording-ask
[data-ask-state]`): `askLine(ask)` (orange only for `unsent`, `needs_input`, `failed`, `interrupted`), the Assistant's answer
as text in `.recording-ask-answer` (folded past 700 characters), what it asked back, the run's waiting cards as the
Assistant's own, and the others counted in a line. Controls: `data-item-ask` "Ask the Assistant" (unsent), `data-item-ask-again`
"Ask again…" (a dialog with an optional sentence for the Assistant, `recording-page.js` `openAskAgain`), Done, Dismiss;
Remember… on a rule. `stillReading` is also true while a request is queued or running, so the page asks again with the
shell's quiet refresh. `recordings.js`: the filing window has "A course's training, for reference" (the course select is
shared with "A course": `data-file-part="course training"`); `trainingsMarkup(data, course, e)` draws one course's
trainings for `#recordings?course=<course>` (`[data-recordings-trainings]`, with the way back); `summary(group, node)` fills
the course Home's "Training recordings" line (`courses.js` `.course-trainings-link [data-trainings-status]`,
`trainingsSummary` from `app.js`); the search asks with `meaning` and `nearestMarkup(found, e)` draws "Nearest by meaning"
(`[data-recordings-nearest]`: each passage's recording, day, where it begins and at most four lines; never a score; one
line when the worker is silent, nothing when it is not set up). `assistant-preparations.js`: a run of kind `memo` offers
no Prepare again (it is asked again from its memo's page). `tests/test_recording_memo.mjs`, `tests/browser_recording_memo.py`.

A recorded class (2026-11-13, RECORDINGS step 5). `recording-items.js`: `classDayMarkup(data, e, {card})` draws `class_day`
first on a class's page (`.recording-class[data-recording-class][data-state]`: the section and day with the go-link to its
Class schedule, Planned and Reached as a two-row `dl`, ONE `.recording-capture[data-tone="warning"][data-class-differs]` for
a planned section that was not reached, plain lines for what was reached beyond the plan or earlier, the waiting move card,
or `data-class-move` "Propose the move to <day>"); `moveLines(day, action, e)` is that section's topics as the card's own
body would write them, passed to `cardMarkup` as `extra` with `foldPreview` (new option of `assistant.js` `cardMarkup`: the
route's raw preview answer is folded when the caller's lines say it; a preview that refused stays open). `outcomeMarkup`
uses `CLASS_GROUPS` (Needs you, What the class reached, Told the class) for `meeting` `class`, with no coordinator and no
student; a `covered` item has only Dismiss. `class-schedule.js` `recordedLine(day)` draws a day's `recorded` under its
topics (`.cs-recorded[data-recorded-day]`: the link "Reached 5.6, 5.7" and an orange chip "Not reached 5.8"), an earlier
week holding one opens unfolded, and the page reads the schedule again when it is come back to (`left`, on `hashchange`).
`tests/test_recording_class.mjs`, `tests/browser_recording_class.py`.

A student's recorded meeting (2026-11-13, RECORDINGS step 4). `recording-items.js`: `studentsMarkup(data, e, {card})` (who the
meeting was with: a `.recording-student` keyed by the enrollment, the section and `partText(part)`, an appointment recorded as
one that did not happen as `.recording-capture[data-tone="warning"][data-student-off]`, what their follow-up holds, their two
cards while they wait, and `data-student-card` buttons that propose one again) and `cardLines(kind, action, e)` (what a card
would do in plain lines, passed to `cardMarkup` as its `extra`, so the card itself stays the Assistant's). `outcomeMarkup` puts
it under the status line and gives the page "Students and parts…" (`data-recording-students`); in a student's meeting the
groups read "They told you" and "They said they would", an answer may be dismissed, no coordinator is named, and with several
students `draw.whose` tags each item. `recording-page.js`: `stretched(data, {start, end})` is the recording as ONE PART shows
it (`#recordings/<id>?t=<start>&to=<end>`: the lines and items that begin in it, the one student, never what the quote check
left out; the audio pauses at its end; the same recording at another stretch is drawn again from what was read, nothing is
fetched), `parseOffset("12:30")`, and `openStudents()` (rows of a native `<select>` of the STUDENT-NOTES list with From and To
as minutes and seconds, "Now" filling the row's first empty time from the player; Save posts the whole list). `recordings.js`
`appointmentOff(row)` is the row's second orange chip. `communications.js`: a `meeting` entry falls under the Meetings filter
(`channelGroup`) and `recordingHref(entry)` (only `#recordings/<id>` with `t`/`to`) is its "Open the recording". 
`followup-desk.js` `heardLink(task, e)` links a task heard in a meeting, "Said in your meeting". `tests/test_recording_student_meeting.mjs`,
`tests/browser_recording_student_meeting.py`.

What came out of a recording (2026-11-13, RECORDINGS step 3). `recording-items.js` is pure markup for the reading's items on
`#recordings/<id>`: `outcomeMarkup(data, e, {card})` (the line saying where the reading stands, `readingLine`; then the groups in
`GROUPS` order, Needs you first with the page's only orange count; `Already done` and `Left out by the quote check` in remembered
disclosures), `itemMarkup` (a `.recording-item` keyed by the item: one sentence, its kind and day, the quote with a `data-play`
button, what Faculty found, its controls), `controlsMarkup`, `becameMarkup`, `foundMarkup`, `stillReading`, `transcriptLabel`.
A waiting Approve card is drawn by the Assistant's own `cardMarkup` (passed in, `here` its own page) and answered with
`submitActionReview`; nothing about cards is drawn twice. `recording-page.js` draws it into `[data-recording-outcome]` with
`patchChildren`, between the player (still built once) and the transcript, which now sits in a native
`details[data-recording-fold]`: folded ONCE when items first arrive, never against the reader's toggle, and opened by a moment
in the address, a find and Enter. Every control posts with a fresh request ID and draws the recording the server answers
(`took`); a button is re-enabled BEFORE that drawing, so a control the drawing says must wait (Read again while a reading is
made) stays disabled. The page reads again by itself only while `stillReading(data)` (the shell's quiet refresh; never with a
dialog open). Item controls use `data-recording-item`, not `data-item`, which the gradebook and the Assistant's selection read.
On the list a row shows `for_you` as an orange chip and `headline` says the total. In grading, `grading-recorded-rulings.js`
(`loadRulings`, `rulingsMarkup`, `bindRulings`, `notFinalMarkup`, `mountNotFinal`) adds "Said in your recorded meetings" to the
finalization window (read beside the check; absent when nothing was said or the read fails) and the "not final" warning to the
two rubric import dialogs; Add fills the chosen line's clarification field and fires its `input`, so the window's own drawing
and Confirm treat it as typed.

One recording's page and the search of what was said (2026-11-13, RECORDINGS step 2). `recordings.js` now dispatches by the
address: `#recordings/<id>` is `recording-page.js` (`createRecordingPage({api, escape, toast, showDialog, dialogHeader, header,
when})`, drawn into the same `#recordings-section`; the shell routes `recordings/…` to the `recordings` view and names its last
crumb from `DETAIL_LABELS` until the page sets the recording's name). The page reads `GET /api/recordings/<id>` ONCE for a
recording (a poll reads nothing again; a forced render after a speaker is named does) and builds its player ONCE: the
`<audio>` (`src` the recording's `/audio`, which answers ranges) sits in a part that is never patched, since a patch would start
it over; speakers, lines and Plaud's notes are patched beside it. Pure: `lineMarkup(line, before, words, e)` (a
`.recording-line` keyed by its place; its time is a button, `data-play` in milliseconds; the speaker printed where the voice
changes), `transcriptMarkup`, `speakersMarkup`, `plaudMarkup` (a remembered disclosure, folded), `lineAt(lines, ms)`,
`findWords`, `holds`, `markedText` (escape first, then `<mark>`), `offsetText`, `pageSubtitle`. The line being said carries
`aria-current` (ink rule and grey ground, never orange). `?t=<ms>` is taken once for each address. The audio is paused when the
address leaves the recording (`hashchange`) and before its frame is replaced: an element taken off the page can go on playing.
`audio.canPlayType` decides the one sentence shown where a browser cannot play Ogg. In browser checks the app's policy lets no
script be evaluated from a string inside the page, so `wait_for_function` cannot be used: ask the page again and again with
`page.evaluate` (`until` in `tests/browser_recording_page.py`). On the list, `resultsMarkup(found, e)` draws
`POST /api/recordings/search` (typed into a `data-page-search` field, sent 250 ms after typing stops, an older answer dropped)
above the groups, and a row's name is a link (`.recording-open`) only where something of the recording is kept.

The Recordings page (2026-11-12, RECORDINGS step 1): `recordings.js` is `#recordings`, a plain list. It has a sidebar entry since
2026-11-14 (TODAY, after Follow-up; the instructor did not find the page while it opened only from the palette and Workspace
settings: "Yes, add it"). Since the same day (RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT"; the instructor: "if it is unsure then
I should be asked to keep it or not", and where: "Home and sidebar too") its entry counts what waits for Keep or Don't keep,
from Home's card `recordings:keep` (`BADGE_ROUTES` in `page-header.js`, `#nav-badge-recordings`): the rail's badges still
come only from Home's cards.
The palette lists it among its Pages (its Actions entry is gone), and Workspace settings still links it (`ELSEWHERE` in
`app.js`). Pure: `listMarkup(data, e)` (Keep or not with its orange count and one line of why, then Recordings newest first,
then, where files of an erased recording are still on a drive, ONE orange line saying so with the storage engine's reason, and,
where some are not kept, ONE line with how many and Ask me again: what is not kept has no row and no fold),
`recordingRowMarkup(row, e)` (a `.recording-row` keyed by the recording: a filed one is named by what it is filed under,
with File elsewhere and Don't keep as words; one that waits by its day, with Plaud's title only as Plaud's words, ONE framed
step, Keep…, and Don't keep beside it as words), `keptText` (what is kept or waited on), `notKeptText(count)`, `ERASES` (what
erasing a kept recording does, said before it is done), `captureLine`, `headline`,
`whenText`, `lengthText`, `clockText`. `createRecordings({api, escape, toast, showDialog, dialogHeader, header, changed})` reads
`GET /api/recordings` in `render(force)` (a joined read, a signature, `patchChildren`: a poll that finds the same list changes
nothing), sets the header's orange chip from the attention count (`header.set({attention})`), and polls every four seconds
only while a check that was asked for has not run. Its controls: Check now (`POST /api/recordings/check`, a plain button,
never a primary); Keep… and File elsewhere, which open ONE dialog for any row (the STUDENT-NOTES picker, a term, a course,
a course's training, a section's class day, Memo) and send the choice in the body with a request ID; Don't keep
(`POST /api/recordings/<id>/discard`), at the press for a row that waits and after one question for a kept one, since that
erases it; and Ask me again (`POST /api/recordings/ask-again`). After a press that changes what waits it calls `changed`
(the shell's `overviewStore.refresh`), so Home's card and the rail's count are read again at once. Nothing is added to
`/api/state`. `recordings.css` is the page's sheet (Ledger, tokens only).

The Memory page (2026-11-10, ASSISTANT-MEMORY "ONE PAGE"): `memory-settings.js` is `#memory`, opened from Workspace settings
(`ELSEWHERE` in `app.js`), with no sidebar entry. Pure: `settingsMarkup(settings)` (three switches, three numbers with their
measured values), `memoriesMarkup(listing, {filter, editing})` and `memoryRowMarkup(memory, listing, editing)` (a memory is one
`.memory-row`: words, how it was kept, then its controls on the row), `ownerFields`. `createMemorySettings({api, toast,
rubricMemories, groups})` draws three parts into `#memory-section`, each patched on its own (`[data-memory-part]`): the settings,
the Assistant's memories, and a kept host (`[data-rubric-memories-host]`) that `rubric-memories.js` draws a course's rubric
memories into (that module is created with no header, since the page has its own). A tick, a choice and a saved number are set
to what the server holds after each draw, because a patch leaves a touched control its own state. `#memory?memory=<id>` opens
that memory's row for its words (`new`: the form that adds one) and `#memory?course=<definition>` chooses the course; either is
taken once and dropped from the address. `assistant-memory.js` keeps the chat's note and "Memories given" and links to the page
with `memoryHref(id)`; the Saved tab (`assistant-workspace.js`) counts the memories and links there; a course Home's Rubric
memories line links there, and `#course/<group>/home/rubric-memories` is sent on by the shell. `memory-settings.css` is the
page's sheet (Ledger, tokens only).

Remembered from your corrections (2026-10-12, ASSISTANT-MEMORY "LEARNED FROM AN EDIT"; it replaces the learning review that
`followup-learning.js` drew inside Term policies, and "Shaped by"). `assistant-workspace.js` `learnedNotice(learned, api, toast,
after)` is the toast that says what a correction taught, quoted, with Undo (`LEARNED_FROM` words the three kinds of correction).
`student-support.js` `sayLearned(items)` toasts each new entry of `suggestions.learned` once after a read (at most three a read,
a day old at most; which were said is a `localStorage` convenience), and `schedule()` reads again in 4 s while
`suggestion_settings.remembering`. `followup-suggestions.js` `learnedMarkup(s, e)` is the follow-up's own line, drawn as Done for
you is (`.support-done`, no new class): "Remembered for later drafts", "From your edit: “…”", Undo (`data-followup-forget`, the
memory's own undo route) and Change (a link to that memory's row on the Memory page), "Taken back" once undone.
`assistant-memory.js` says "written by the Assistant from your correction" under Memories given, and the Memory page on its row.
In `followup-suggestions.js`, draft-check warnings are
"Before you send" callouts on their pending mail card; no Edit while the run is `running`.

Conversation readability (2026-10-10, ASSISTANT "Readable conversation"): `eventMarkup` labels each speaker and puts the
instructor's timestamp and Edit in the message header. Shared chat CSS uses a right-aligned, softly tinted own message,
16 px prose with a 70ch limit, a denser reading surface, and compact answer actions and step review. These use shared tokens;
`ledger.css` applies the glass's rounded corners. Copy still uses the stored answer, without the speaker label.

The main Assistant drawer, floating chat and selected-area overlay use DESIGN-LANGUAGE "Assistant glass": shared `--assistant-glass-*` tokens,
frosted glass with an opaque fallback, rounded controls and soft depth, scoped in `ledger.css`. It keeps the existing
selection, focus preservation, quiet chat rendering and explicit actions. Other app surfaces keep Ledger.

One conversation for both surfaces (2026-10-09, ASSISTANT "One conversation, real windows"): `assistant-chat.js` holds what the
drawer (`assistant.js`) and the floating window (`assistant-area.js`) share. Pure: `chatMarkup` / `turnMarkup` (live step list,
the fold's "N steps · S s", Copy / Copy table / Ask again under an answer), `eventMarkup(event, ctx)` (one drawing of a message,
step, card or failure), `emptyChatMarkup` (starters), `composerMarkup({id, hooks})`, `contextChips`, `chatTitle`. Behaviour:
`createChatView({host, turns, onAgain, onEdit, onStarter})` owns a pane (`[data-chat-events]`, `[data-chat-question]`,
`[data-chat-latest]`; `draw({html, question})` patches only when the markup changed and draws math), `createComposer(...)` owns
a box (growing height, Enter, paste and drop, `enqueue` / `settle` for the one waiting message, chips, the state line's clock),
and `sendMessage` sends once (a lost response is settled by one read, never resent). `assistant-markdown.js` is the message
text (`markdown`, `tablesTSV`, `messageTime`); `assistant-pickers.js` the "/" and "@" lists. A surface keeps its own hooks
(`#assistant-text`, `[data-assistant-state]`, `#area-question`, `[data-area-state]`, ...) by passing them to `composerMarkup`.
New chat behaviour goes into these modules, never into one surface. What a browser remembers: `faculty:assistant:width` (the
drawer's width, as `--assistant-width` on the root; `assistant.css` clamps it into `--assistant-drawn`) and
`faculty:assistant:window` (the floating window's size and place); both are conveniences read in try/catch.
Changes beside the chat (2026-10-10, ASSISTANT "CHANGES BESIDE THE CHAT"): the floating window draws its conversation's cards
in a panel at its right, not among the messages. `conversationCards(events, actions, batches)` (`assistant.js`, beside
`chatCards`) is pure: `panel` (the cards, what waits first), `count`, `waiting` and `line(id)`, which the window passes to
`eventMarkup` as `cards` so a card is one `.assistant-card-line` (its chip and a `[data-card-show]` button) in the conversation.
The window is `header`, then `.assistant-area-body`: `.assistant-area-main` (the conversation's column) and
`aside[data-area-changes]` (`[data-area-changes-list]`, patched with `patchChildren`; `[data-area-changes-hide]`; the footer's
`[data-area-changes-show]`). `position()` adds the panel's width to the window's, and the remembered `w` stays the
conversation's. The cards' clicks are the window's existing ones (delegated on the window). The drawer is unchanged.
A card comes into the window in one place, `read`, which reads the cards before it takes the step's event in (and tries again
when it cannot). The answer to a message, a selection, a question or Stop goes through `receive`: one that already holds a card
(`bringsCard`) is left whole to `read`, never taken in without its card.
Picture search (2026-10-10, PICTURE-SEARCH): `picture-search.js` is pure markup for the Inbox search's strip of pages and
pictures. `describesAPicture(query)` says whether a search asks for them at all (words, not a job ID or a filename);
`pictureStripMarkup(found, escape, {open})` draws a keyed shared disclosure of small pictures (each result's own `thumb`),
each a `viewLink` to what it is (a PDF at `open_page`) with a caption linking the page it belongs to; a student's file with
no page of its own has a plain caption, and a printed page with scanned copies adds "N scans" linking the newest scan. The
search form's `[data-inbox-like]` file input posts one picture to `POST /api/picture-search/like`; the strip is then headed
"Pages and pictures like “<file name>”" (`found.like`) and the typed words are cleared. `inbox.js` asks `GET /api/picture-search` once per submitted search, never per poll,
and redraws the list it already holds; figures carry `data-patch-key`, so polls keep them.
Memory (2026-10-10, ASSISTANT-MEMORY): `assistant-memory.js` is pure markup for every place a memory shows. `memoriesGiven(turn)` and `memoriesGivenMarkup(given, memories)` draw "Memories given" inside a turn's "How it got this" (`turnMarkup` takes the conversation view's `memories` map in its options and counts them in the fold's hint): the `memories` events hold references only, so the words and state come from that map on every draw. Its Remove and Restore buttons (`data-given-remove`, `data-given-restore`) and the note's "Keep my words" (`data-memory-typed`) reach a surface through `createChatView`'s `onMemory(operation, id, revision, button)`; Change is a link with `data-assistant-tab-open="workspace"`. `memoryReworded(events)` gives the memories whose note shows the Assistant's rewording with what was typed (or the instructor's own, made in the form: `actor` "instructor", no Undo). STILL BEING CONSIDERED: a surface keeps the conversation view's `remembering` apart from the merged conversation (the key is absent once settled), reads on while it is set, and passes `remembering.rewording` to `eventMarkup` as `rewording`, so the note of the memory being reworded says so and offers no link. A memory's own link carries `data-memory-open="<id>"` (`new` for the form that adds one) beside `data-assistant-tab-open="workspace"`: the drawer hands it to `workspace.reveal(id)`, and the Saved tab's next draw opens that form at the top with the caret in its words. A click on such a link inside the floating chat puts that window away when it would cover the drawer. The Saved tab announces a saved memory with the window event `faculty:assistant-memory-changed`; both chats read their conversation once on it. A memory whose view has `ruled` (a rule the Assistant wrote without the student a message mentioned) says so in the note, the list and "Memories given", and has no typed words to show or go back to; action `unruled` is the line for one it could not write. A follow-up's "Remember this" says what it kept in a toast: `rememberedNotice(result, api, toast)` in `assistant-workspace.js` gives the message and options for each outcome (a rule quoted with an Undo action and a 20-second stay, words kept as typed, a fragment, a refusal), and a request sent with `remember` waits `REMEMBER_WAIT` (90 s) instead of the usual 15. A memory's `pictures` are drawn small (`keptPictures`, 72 px, each a `viewLink` to its own route) in the note and the list; with pictures the Erase button says so. `memoryNote(event,
undone)` is the chat's line for a `memory` event ("Remembered: “…” Undo", what it replaced, or why it was not kept), drawn by
`eventMarkup` for both surfaces; `memoryUndone(events)` gives the memories a later event took back, so a note offers Undo once.
`createChatView({onUndo})` hands the click to its surface, which posts `memory/<id>/undo` with its conversation and reads the
chat again. The list itself is the Memory page's since 2026-11-10 (`memory-settings.js`, above); the Saved tab keeps a count and a
link. The browser decides nothing about what is kept.

Selected-area Assistant (`assistant-area.js`, `assistant-selection.js`): Hold Command with the pointer still for 600 ms, draw a box, then release Command to send the actual visible words, fields, gradebook records and original image crops without screen sharing. The header/viewer button also permits ordinary click-and-drag selection. The selector blocks underlying actions and excludes hidden content, credentials and student photos. Images account for object-fit and clipping; inline charts use their own pixels. The file viewer temporarily renders native PDF pages using its existing service, keeping the same iframe mounted for restoration. The bubble uses shared chat/card rendering and transfers its existing conversation to Assistant above an open dialog. Double-tapping Command opens the same glass window as an empty, ordinary Assistant chat; it starts no model turn until a message is sent. `ui.holdCaptureFocus` preserves the source editor while focus stays in the selector, bubble or drawer; focusing the page elsewhere ends it and delivers the editor's own blur (owners check `isCaptureHeld`). Selected values are bounded, marked as possible drafts, and kept only in temporary conversation memory. Close, navigation and hidden tabs release the selector and stop unfinished analysis; the floating chat stays across navigation and hidden tabs (each message sends the page on screen then), and so does a finished selection chat across hidden tabs. A box sent while a chat window is open goes to that chat (waiting while it answers or asks; a box drawn in another modal dialog moves the chat there); over any open bubble a hold or Select only hides the bubble until a box is sent. The chat's title bar drags it like a window; it keeps that place, whole inside the window, until closed. The drawer and the floating chat share a paperclip (`assistant-attach.js`, ATTACH IN CHAT): files upload as soon as they are chosen, are listed with Remove, go with the next message by ID and show on the sent message. The hold never arms while typing in the chat, a click without a drag during a hold passes through as Command+click, and Escape is the assistant's only in the selector or inside the bubble. Command gestures do not reach Faculty while a native PDF has keyboard focus, so the viewer's area button is the way in there.

Assignment workflow clarity (2026-10-09): `assignment-collection.js` mounts the native exam participant projection and version-checked correction forms; its leave guard and dirty checks preserve edits. `grading-crops.js` keeps image zoom/pan inside the split review desk. Preparation and delivery checklists link to their owning editors. New canonical previews use the shared viewer. These views add no retained teaching state and do not change `/api/state`.

Coding Apply (`assistant-code.js`, 2026-10-09 review): while the server reports `applying`, the task shows one status line,
"Applying · <stage> · <minutes>" (bringing it up to date with main, running checks n of m, merging, pushing, scheduling the
restart), with a disabled Applying… button in place of Apply now, Discard and Ask for changes; the tab polls every four seconds
until it ends. The line appears on the click itself, so a refresh during a long Apply never offers a second Apply.

The Assistant's Automation tab (`assistant-automations.js`) projects built-in automation and opted-in rules with scope, schedule,
next check, last result and blockers. Built-in settings link to their owning pages; rule toggles keep revision checks. Quiet
refresh patches keyed rows and preserves preparation form input. `assistant-context.js` captures only route/selected-record IDs
and the existing leave guard's unsaved-work boolean, never field values. Gradebook cells and the visible follow-up identify their
owners explicitly; all server writes still revalidate them. Both supported desktop widths keep the five Assistant tabs visible,
at any width the drawer is dragged to (never under 480 px).
Since 2026-10-09 a row renders `waiting` as a muted note (no Diagnose), `held` with Prepare again and no Next, and
`last_outcome`/`last_checked_at` as its Last result, each only when present. Prepare now writes its outcome into the form's
status line and resets the form; active runs sit below the form, polled alone every five seconds while one is queued or
running. "Done by Faculty · last 7 days" is a closed fold of one-line links. Diagnose sends the row's title, never its route;
errors are toasts. Home's "Assistant changes waiting" row opens the drawer through `data-assistant-tab-open`, which never toggles.

`static/assistant-preparations.js` renders the Assistant Saved tab's opt-in
teaching preparations. Setup is folded; queued/running/failed/review states and
review links stay visible. Runs are grouped by kind and target: the newest is shown
in full with its one Prepare again; older drafts fold into "N earlier drafts" (state
and date only, no form). Poll only the run area every five seconds while work is queued/running,
or every thirty seconds while a preparation rule is enabled, so a daily result appears without reopening the tab.
Hidden browser tabs skip reads. Keyed patches keep a guidance field's text and focus. Leaving the tab
cancels its timer; generations discard late results. Rule creation previews the
exact target before Enable; pause/resume uses its revision. The underlying cards
remain the ordinary Changes renderer and approval handlers (ASSISTANT).
Grade reconciliation's automatic option and active rule say "Daily at 5:00 AM Central";
assessment readiness keeps "When the records change". Switching targets preserves the
selected mode and reminder value, hiding/disabling the assessment-only reminder for
reconciliation. The server schedules the check and waits for the morning Canvas sync.

`static/assignment-workspace.js` supplies the Assignments list, read-only progress
view and common navigation around existing owner editors. `/api/assignments`
projects workspace records by permanent `(assignment_id, term_id)`; it retains no
workflow state. Owner routes are preserved to keep review navigation and leave
guards intact. `renderWorkspace` on the document controller reuses its creation and
print dialogs in the canonical assignment page. See ASSIGNMENT-WORKSPACE.

Faculty uses native browser JavaScript modules. There is no frontend build step
or production TypeScript dependency. Serve the source modules through the existing
application; all retained records stay in their current server-owned services.

`static/app.js` coordinates routing and screen state. `static/request.js` supplies
the shared JSON request client, preserving same-origin write markers, private-data
cache policy, and operation-specific timeouts. It never retries. An `ApiError`
retains its HTTP status and body for the caller; workflows decide whether a lost
response can be recovered by explicitly resubmitting the same request ID. A 404 or
409 answering a write makes the shell read its state again (`onConflict`); one
answering a read (GET, plain or `revalidate`) never does, since that refresh renders
the page, which reads the missing record again: the page shows the missing record
itself (2026-10-02: a link to a purged scan read it about 50 times a second).

`static/ui.js` supplies small shared primitives:

- `runFormAction(form, operation)` prevents duplicate submissions, reports errors,
  and restores the previous disabled states. It leaves destructive-action warnings
  visible and marks the form busy for assistive technology.
- `setBusy(control, busy)` disables one button for the request it started and marks it `aria-busy`, or
  gives it back; `markBusy(element, busy)` sets only the marker, for a button whose disabled state a dialog's
  `sync()` derives (busy while it reads a preview). See "Busy and blocked buttons" under Button tiers.
- `dialogSession(host, dialog)` guards against late responses updating closed or
  replaced dialog content. Use a request-generation counter as well when the
  audience or selected scope can change inside one dialog.
- `tableMarkup(escape, headings, rows, label)` gives preview tables consistent
  accessible headings and a keyboard-scrollable region (`.data-table` in
  `components.css`; it was `.seating-merge-table` until 2026-09-26). Rows must
  already be escaped markup from the caller.
- `escape(value)` escapes text for HTML content or a quoted attribute; `fold(text)` lower-cases text and drops its accents,
  for matching what is typed against names.
- `plural(n, one, many)` (`many` defaults to `one` + "s") names a count and its noun ("1 copy", "3 copies") in plain digits; a page that
  groups thousands or wants a different rule keeps its own variant.

The component vocabulary, the page header and the rest of the shell (UX overhaul, 2026-09-26) are described in
**Component vocabulary** below; use those helpers instead of page-specific notice, empty, loading or header markup.

`static/dom-patch.js` redraws polled pages in place: `patchChildren(target, html, opaque)` and
`patchElement(target, source, opaque)` keep every element still present (matched by position and
tag), update attributes and text, and replace the rest, so a poll that changes one count does not
restart animations, reload thumbnails or drop focus. Handlers assigned as properties or delegated
survive; elements bound with addEventListener must match `opaque` and are replaced whole. The Inbox
job page, the Inbox list, the scan card, Home, the Calendar, the Accommodations page and the Exams hub use it
(2026-09-26), since 2026-10-08 the Assistant's chat (an opened "How it got this" stays open while the next answer comes in,
ASSISTANT QUIET STEPS), and since 2026-10-02 the Absences page (its 30 s refresh had reset the table's scroll), the Grade step's batch cards and the Review step's flag review
(`grading-jobs.js patchKeepingDisclosures`: since children are matched by position, a line that comes or goes moves the
disclosures after it onto other elements, so each disclosure's open state is kept by its name (`data-job-disclosure`, the flag
review's question, last-review and fold names) and focus goes back to the control or summary it was on; an unchanged read writes
nothing).

`static/grading-review-desk.js` is the one review desk (`mountReviewDesk(work, value, h, {index, total, groupSize, heading,
statuses, zeroShortcut, explain, scroll, onSaved, onMove, onReveal, reread, onConflict, active, moveKeys, student, certified, moved, left,
approveNext, onLater, recovery})` → `{dirty(), leave(), destroy()}`; `certified`, the plan's current certification date, makes a per-response regrade ask first; `moved` scrolls a
short window to the new response's heading after a move; `left` says beside a group's position how many of the question still
wait; `approveNext` labels the primary action Approve & next and puts Save as draft with Notes; `onLater` adds Later / Alt+L; `reread` and
`onConflict` serve GRADING "KEEP CONFLICTED EDITS": the desk reads the response again with the page's own reveal choice and the page
refreshes its counts around it; `moveKeys` names the caller's own next/previous keys when there is no `onMove`): a response's
crops, reading and rubric with the decision form, saved only through `POST /api/grading/responses/<rid>/review`. It owns the
desk's keys (1–9, 0, Alt+A, and Alt+N/Alt+P only when the caller passes `onMove`) and its leave guard; one desk is mounted at a
time (`closeReviewDesk()`), so a caller holding unsaved work asks through `leave()` first. The Review step mounts it with every
decision; Certify offers only `approved` and `manual` (plus the "No credit (manual 0)" shortcut), since a draft
takes an automatic grade out of final and an unreadable or rejected review leaves the question without a grade.

`static/grading-certification.js` is the sixth grading step's page (Certify; "Certification and Export" from 2026-09-26 until the tab's word replaced it on
2026-10-18): the step header with the
secondary Final quality audit… and (Gradescope set up) Publish grades on
Gradescope… (`grading-gradescope-publish.js`'s dialog, 2026-10-02; the only place to publish, GRADESCOPE "ONE PLACE TO PUBLISH") and the one primary Certify grades…, a one-line status band whose Details
hold the checks, the summary cards with the distribution under them (no painting since 2026-10-04), and
Questions | Students as two tabs of one panel whose top row holds the student search (`data-page-search`, so `/` focuses it; it filters
both tabs, and typing from Questions opens Students), all drawn from
`GET /api/grading/assignments/<aid>/certification` and patched in place (`dom-patch.js`) on later reads; Gradescope, the files
(`grading-certification-files.js`) and the training downloads are secondary sections below. The Gradescope panels mount only when
their fold opens (or at once, focused, when the page is given `open: "gradescope"`, which `grading.js` passes for
`?tab=export&open=gradescope`); a send that starts or settles in a panel reads the page
again, a send started from the Certify dialog refreshes the panel (`mountGradescope` resolves to `{refresh}`), and each read updates
the fold's hint and, once a grade is final, the downloads. The fold's open state is remembered per plan and separately under
each default, so a choice applies again whenever the default it was made under returns; a finished
Final quality audit is one such fold, open while it is newer than the certification. Its pure pieces
(checks, band, histogram as SVG rectangles, cards, question sorting, student filters and name matching) are
`grading-certification-stats.js`; the Certify dialog is `grading-certification-dialog.js` and the question panel's "Add a line to
every grade…" dialog (a batch point adjustment, previewed, then applied by fingerprint; the same dialog edits or removes a batch
listed under "Lines added to every grade") `grading-certification-adjust.js`, both imported lazily and warmed when the page mounts.
Late papers Faculty certified by itself (GRADING "LATE PAPERS CERTIFY THEMSELVES", 2026-10-09 review) are said in the band, never as
one long sentence: the lead's "who" is a text button (`stepHeader`'s `leadMarkup`, markup the caller escaped) opening Students on
"Certified by Faculty" (`markFaculty` marks those rows `by_faculty` from `automatic.latest.students` after each read; the rows and
the drawer carry a "By Faculty" tag); the folds "Late papers waiting · N" (only while the switch is on; one line per student, the
name opening the drawer) and "Certified by Faculty · N"; the plan's own reason; and a version a late paper went to unpublished
("Publish grades…" only focuses the header's Publish grades on Gradescope…). Faculty's own send of a late paper is said in the
student's Gradescope cell, the drawer and a list at the top of the Gradescope fold (patched on each read). Four or more digits
typed into the search go to `POST …/certification/students/search` in the body and come back as names; they never reach an
address, an attribute or a status line. The page is six containers that are never replaced (events are delegated to them): the
patched analysis, the Final quality audit's host (patched on its own read), the question panel (`grading-certification-question.js`, drawn on explicit changes only, since it typesets the
rubric's math), the patched Questions | Students panel, the student drawer and the secondary sections. An open question is kept in
the address as `question=<qid>` through the `address` callback `grading.js` passes (it rewrites the render's own route, so the page
stays active); a student is never in the address (row keys go only into API paths). The gradebook's **See the graded paper** (a cell
recorded from grading) hands its row to this page in the tab's memory (`handoff.js`: `give({plan, row})`, and `take(plan)` once as
the page mounts; the student's drawer opens after the first read. Any other `take`, or a minute without one, drops it, so a later
visit opens nobody). The drawer (`grading-certification-students.js`)
is an `aside role=dialog aria-modal=false` inside the page, NOT `#dialog` (the Send to Gradescope, Excuse and Certify dialogs use
that), docked at the right of the page (desktop only; × or Escape closes it). It shows
one question at a time read only, goal first (requirement, 2026-10-06: "a wall of text"; DESIGN-LANGUAGE principle 13): the grade, the
lines applied each with the model's first sentence, and Change grade; the paper at the drawer's width; then one fold each for the
other rubric lines, the reading (open while a reading flag stands), the models' original flags (an orange mark when one still
stands, else who settled them) and the checks. It mounts the review desk only on Change grade; opening another question, another student or a link asks the desk's `leave()` first. The page polls every
20 s only while `view.live` and holds its polls while a certify preview or certify is in flight (both hold the lifecycle lock); the
graph's sixth node follows the page read through `onPlan`. A Gradescope send started from the Certify dialog is followed on the page
(`handoffMarkup`, the Gradescope panel's `unitProgress`), which offers Check again once it settles. A certify request whose answer
never came is kept in memory per plan and only its explicit Retry request sends the same body (and request ID) again.

`static/followup-suggestions.js` (2026-10-03) draws one follow-up's Suggested actions section and handles its controls
(`suggestionsMarkup`, `suggestionClick`, `suggestionSubmit`) for both the Follow-up page and the Assistant's Changes tab, which
pass their own item list and refresh. Cards still use `assistant.js` `cardMarkup` (its `here`, `editMail` and `extra` options)
and the shared Approve/Send/Decline handlers; Edit in Mail opens `mail.js`'s compose window (`attachments`, `onSent`).
Retry suggestions remains available on ready/needs-input preparations even without an outdated-policy flag, so older
suggestions can use the latest saved policies. Queued, running and superseded preparations do not offer Retry.
FOLLOW-UP CLARITY: a waiting card with the server's `effect` is drawn by `followup-suggestions.js` itself as a summary (title,
page and person, one-line fields via `changesMarkup` with struck-through old values, a button named for the action, the same
`data-assistant-approve`/`-decline` handlers) with "Show the whole proposal" folding long fields, what follows, the Assistant's
note and the exact request; a question's choice sends the answer (`assistant-workspace.js` `chooseQuestionOption`);
the Follow-up queue is in review order (`reviewOrder`) within its groups (`queueGroup`: To review, Your to-dos, Waits on
students, Follow up later, Nothing waits; a to-do row says what the instructor did last, `actedLine`), selection auto-advances after the instructor's own decisions empty
a conversation (`nextReady`), Alt+J / Alt+K / Alt+A / Alt+E are registered through ui.js `registerShortcut`, and a card's
`assumption` renders as an "Assumed" note right above the card's buttons with each alternative a framed `.support-choice`
(`assumptionMarkup`, each a `revise` request; READABLE REVIEW below);
with nothing waiting, decided cards fold into one `.support-finished` disclosure ("4 steps done · 1 declined", `finishedAs` /
`finishedMarkup`; FINISHED STEPS) while failed, unconfirmed and submitting cards stay in view;
steps the browser is approving (`whileApproving`, module memory shared by both review locations) are re-marked on every read
(`withApproving`: `pending: false`, `status: "approving"`, chip "Approving…") and drawn at once through the context's `redraw`,
so a refresh mid-run offers no second Approve or Approve all (APPROVING);
a waiting mail card's Edit opens it for editing on the card (`followup-suggestions.js` `editMarkup`, editing state in module
memory for both review locations; Save posts `<run_id>/edit`, and Approve all waits while a card is open); a waiting absence
card's Decision line is a dropdown (`decisionMarkup`, from the line's `choice`; `suggestionChange` on the page's and the drawer's
`change` event posts `<run_id>/edit` with `status`, keeps the dropdown disabled on the choice through reads that land meanwhile
(`deciding`), puts it back when the choice is refused, and returns focus to the dropdown of the card that took its place;
ASSISTANT "DECISION ON THE CARD"); mail and coding cards keep `cardMarkup` with its
`mark` (step number in place of the chip) and `audience` options. The section leads with an open question, then the plan.
READABLE REVIEW (TEACHING-WORKFLOWS): `student-support.css` sets the review's `.text-button`s and `.link-arrow`s at body size
with `:root:root :is(.support-review, .support-suggestions)` selectors, which outrank `button-theme.css`'s (0,4,0) text-action
type (page rules at (0,3,0) or (0,4,0) lose to it, as Don't, Undo and the alternatives once did; sentence case itself is the
app's rule, Button tiers); the evidence warning is a
`.callout` with a `data-followup-why` button (`suggestionClick` opens and scrolls to `.support-evidence`, no request); Done for
you renders after the controls while anything waits (`doneMarkup` at the top only when quiet).
A COORDINATOR'S FOLLOW-UP (TEACHING-WORKFLOWS, COORDINATOR): a listed follow-up whose `student.coordinator` is true is with
the course coordinator. `student-support.js` asks `withCoordinator(item)` and words it with `shortName`, `someone` and
`stateLabel` ("Waiting on coordinator"), reads its messages from `recordPath(item)` (the term's coordinator record), shows the
name as text (no student record opens), offers "Write to coordinator…" (Mail's compose window, to the address on the course
card) and no join. The course card's Coordinated course box (`app.js`) greys out a disabled `fieldset.course-coordinator`
until it is ticked; its rule in `style.css` is written above the form and ledger themes' field rules, which load later.

The Follow-up page uses a compact queue with one selected review (`student-support.js`): enrollment plus exact conversation
key keeps selection through the first tracking save. RESOLVED WHEN OPENED (TEACHING-WORKFLOWS): its list read is `brief=1`, and
selecting a `suggestions.brief` follow-up reads the student's full view (`readDetail`), kept in `details` (20 at most) and put back
over each later list read while the list says the same of it (`withDetails`); `render(true)` marks them to be read again. Its one Communications timeline read shows the exact observed student
message, including a merged Canvas notification, with a generation guard on selection changes and an explicit retry on read
failure; the same read shows the instructor's message after it as "Your reply" (`answeredMessage`, from the service's `answered`).
WAITING ON: the review head and each queue row say whom the follow-up waits on and for what (`waitingOn`:
"Waiting on you", "Waiting on <first name>", "Follow up <date>", with the recorded next step or what the status implies); a
queue row leaves out what its title already says (`queueNext`). ONE CLICK ON A TO DO (2026-10-13): a tracked To do with
nothing to review and no open task has Done and "Now waits on <first name>" in its decision box (`todoSettles`,
`data-support-finished` / `data-support-waits`), the instructor's own saves through the follow-up's route.
SAME MATTER (2026-10-07): Jev's unsure reading is an info callout above the student's message ("Same matter as “…”?" Join ·
Keep separate); conversations the instructor joined are a short list under it, each with Keep separate (Jev's joins are under
Done for you, `doneMarkup(s, index)`); "Join to another follow-up…" opens a window of the student's other follow-ups
(`joinChoices`); an unsure TO DO THANKS adds "<first name> only thanked you. Is this follow-up finished?" and Mark resolved to the
decision box; Write to student… passes `replyTarget` (the student's Gmail thread and "Re:" subject) to Mail's compose.
DONE AT ONCE (2026-10-10, ASSISTANT "CONNECT A MESSAGE"): when a chat's turn brings an action (a join or Keep separate it made, a
follow-up it prepared again, a new card), the drawer and the floating chat dispatch `faculty:assistant-pages-changed` on `window`
and the Follow-up page reads again, so the change shows beside the chat without waiting for the page's 30-second refresh. The
event reads nothing itself and no chat listens to it. A bounded map of 20 detached review articles preserves unsent forms and open disclosures during switches. Explanations
and checks share one disclosure; source entries are deduplicated by kind, path, fingerprint and observation time. Evidence
limitations stay visible. JEV HOST FAILURES (2026-10-09): one status line under the toolbar (`triageStatusMarkup`) says
"Student triage paused: … Retrying every N minutes." while triage waits out its provider (`jev.triage_paused`) and counts the
failed analyses with a Retry all failed text button (one request ID per click); an item's own Retry reads Analyze now while
nothing has read it, and the read-error Try again is `data-support-reload`, so it no longer swallows that Retry.
GRADING MULTIPLE-CHOICE READER HEALTH (2026-10-09): a batch card that read by choice adds one supporting line ("Multiple choice:
N read by choice · M graded as written work (…)", `choiceReads` in `grading-jobs.js`) and a `grading-warning` only while reads
failed. Shared cards take `compact`/`heading` options here to use a short draft heading and omit internal route
and confirmation copy; their full previews, attachments, reasons and approval handlers are unchanged.

Term policies uses a grouped area list beside one area's handling dropdown and guidance field. Definitions/prompts come
from the owning service's `?areas=1` read; old text stays in Additional guidance. Drafts are per term and remain intact
across area/term switches and in-flight saves. Save applies to the selected term and stays open; status distinguishes
unsaved changes, configured areas and the saved revision. The header/footer remain visible at desktop widths.

`static/student-picker.js` is the searchable student picker (an ARIA combobox over a
listbox) for choosing one student among hundreds: `studentPicker(escape, spec)` returns
markup and a `bind(root, onChoose)`; the value sits in a hidden input named by
`spec.name`, so forms read it as they read a select, and `locked` marks the text box
`data-locked` for the bubble review's unlock. Words typed filter the options (any order,
case and accents ignored); four or more digits call `spec.search(digits)`, which asks the
server for the closest UINs and gets names back, never UINs. The list is placed against
the window through the CSSOM, so a scrolling table or a dialog never clips it, and
`keepPickerTyping(root)` carries what is being typed across a re-render.

`static/seating.js` remains the chart controller and its public API is unchanged.
It delegates creation to `seating-create.js`, seat moves and exam-version editing
to `seating-interactions.js`, print previews and geometry to `seating-print.js`,
and student-chart email, Gmail invitations, and mail merge export to
`seating-delivery.js`. The controllers receive their existing services and callbacks
as dependencies. Creation still uses the
catalog/options/plan endpoints and the assignment wizard's callbacks. Delivery
keeps uncertain request payloads for the lifetime of the seating controller,
including closing and reopening a dialog. Previewing sends nothing; explicit send
and exact request-receipt checks remain in the individual delivery handlers.

The interactions controller retains selections and in-flight guards across chart
re-renders, including navigation during a request. It reports replacement chart
data through an explicit host callback and invalidates pending renders before
writes. Printing reads the current host chart when preparing each view; saved
version lists and room rosters still revalidate their previews before printing.
Its temporary DOM, stylesheet and browser-print state are cleared on navigation
and after printing. These modules do not introduce new storage owners.

One room's exam-day packet (AGENTS.md "Exam-day packet") prints through the same
module (`openPacketPrint`): `exam-day-packet.js` draws the cover, instructions and
room roster from `GET /api/exam-day/charts/<chart>/packet`, `chartChoice` and
`startPrint` are Print chart's own layout choice and preparation, and the packet
node goes first in `body` on the named page `packet-portrait`. Both `@page` rules
live in the adopted sheet, the named one after the chart's (Chromium lets a later
unnamed `@page` override an earlier named one). The plan page is `exam-day.js`
(`#exam-day/<assignment>/<term>`); its Email proctors link is a `mailto:` built in
the page (`proctorMail`).

## Component vocabulary

The UX overhaul (2026-09-26) gave every page the same parts. Markup helpers live in `static/ui.js` and return strings,
escaping every text they are given (only `body`/`html` of the disclosure helpers is caller-built markup); styles live in
`static/components.css` (screen only, linked after the page sheets and before the theme sheets, so themes still dress
the components and page sheets cannot fork them). New page-specific notice, empty-state, summary or header classes are
not allowed. The older notice and boxed-empty classes now sit beside the canonical class on the same element
(`class="callout seating-issues"`, `class="empty-state roster-empty"`, `class="chip inbox-badge"`) and stay only as hooks
for scripts and tests; they carry no look of their own. Two notice classes are still aliased in `components.css`
because their inline content is not a callout's: `.grading-warning` (the grading modules) and `.guide-note` (the
connection guide). Inbox job and scan states are chips whose tone comes from `scan-flow.js` `SCAN_TONES`/`STATUS_TONES`.
A `.callout` hidden with `[hidden]` stays hidden (`.callout[hidden]`). Prefer plain class selectors: a `:has()` on a
common element (the graph's scroller, a list item) measurably slowed style recalculation when a large dialog opened.

**Legacy sheets.** `style.css`, `layout.css` and the page sheets linked before `design-tokens.css` apply to print as well
as screen, so the old light palette in them is what an arbitrary page prints with. On screen they carry no light
literal and no corner radius of their own (theme sweep, 2026-09-26): a literal the screen themes always overrode was
removed, and one that still showed became `var(--line)`, `var(--muted)`, `var(--ink)` or `var(--paper)` (each defined in
`style.css :root` for print). Print rules (`@media print`), the seat, room-legend and exam-version palettes, and the white
frames behind scanned page images keep their literals. Do not add a hex colour or a radius to a screen rule: use the
tokens in `design-tokens.css`. Many page sheets keep their screen restyle in a trailing `@media screen { … }` block so
that print is untouched. There are no phone or tablet rules (desktop only, requirement, 2026-10-03): no screen `@media` or
`matchMedia` width condition below 1180 px (`tests/test_design_language.py`); a print-only query may name the paper's width,
and a container query may still adapt a component to a narrow column of a desktop page.

**Page rules.** Every page answers, in its first screen: what this is (the header names the object), what needs the
instructor (one derived sentence, chips or a callout) and what to do (exactly one primary action). A lead is one sentence
(about 120 characters); policy, rationale, measurements and how-to text go into a closed 'About this page' or 'How this
works' disclosure. No slogan eyebrows: an eyebrow carries context only (course · term, stage N of M). Raw internal codes
(`todo`, `review_ready`, `READY`) are never shown; `stateLabel(code)` gives the human name. Counts, stages and next steps
are always derived from saved records on the server; the browser never decides one.

| Component | Owner | Markup / API | Use it for |
| --- | --- | --- | --- |
| Page header | `page-header.js`, `page-title-fit.js`, shell views table in `app.js` | `header.set({route, title, context, subtitle, crumbs, action, secondary, updated, variant, label, attention})` | The object on screen (the title on the header band), its context line (the band's label), one primary action |
| Breadcrumb | `page-header.js` | `#breadcrumb` `nav > ol`, links only, last crumb `aria-current` | Where the page sits; never a row of buttons |
| Button tiers | `button-theme.css` | `.button.primary`, `.button`, `.text-button`, `.link-arrow`, `.button.danger` / `.danger-button` | One ink primary per region; everything else quieter |
| Callout | `calloutMarkup({tone, title, body, action})` | `.callout[data-tone=info\|warning\|danger\|success\|neutral]` | One notice or problem, one sentence, at most one action; `role=alert` only for danger |
| Disclosure | `disclosureMarkup({label, body, hint, key, open})`, `aboutMarkup(routeKey, html)` | `details.disclosure` (a bold summary over a hairline) with a +/− marker at the right (also for a bare-text summary; the label never shrinks under its longest word, the hint wraps); `[data-framed]` for one ruled record | Anything longer than a sentence; `key` remembers the viewer's own open or close (a click on its summary, `rememberDisclosures`; never the toggle the browser fires when it parses `<details open>` or a script or patch changes `open`, 2026-10-02) |
| Who sees it | `seenByMarkup(who)`, `SEEN_BY` | `small.field-hint[data-seen-by=you\|students\|student]` inside a `.field` label, under its words (the grading helpers' `field` and `area` take it as `hint`) | Who reads what a field holds: "Only you see this", "Students see this on Gradescope" (GRADING "WHO SEES A COMMENT") |
| Empty state | `emptyMarkup({icon, title, body, action, kind})` | `.empty-state[data-kind=first-use\|filtered]`, a ruled block (below) | 'Nothing yet' (offer the first step) vs 'filtered to nothing' (offer to clear) |
| Next step | `nextStepMarkup({eyebrow, title, detail, label, href})` | `aside.next-step` (`scan-flow.js scanNextStep` emits it with `data-scan-next-step`) | The hand-off to the page where the next step is done; it only links |
| Chips, filters, stats | `filterChipsMarkup`, `bindFilterChips` | `.chip[data-tone]`, `.filter-chips` (`aria-pressed`), `.stat` | Counts and filters instead of sentences |
| Action menu | `actionMenuMarkup({id, label, items})`, `bindActionMenu(root)` | `.action-menu` on a native popover | Destructive and rare actions (Delete, Start over, Supersede) away from the primary |
| Toast v2 | `toast()` in `app.js`, `toastSpec` in `ui.js` | `#toast .toast-item[data-tone]` (a manual popover, moved into an open modal dialog; left of the open Assistant drawer) | Short confirmations; errors (tone `danger`, alias `error`) persist; actions such as Restore |
| Loading | `loadingMarkup(label, rows)` | `.loading-state.loading-skeleton` | A view's first mount only (never on a poll) |
| Leave guard | `registerLeaveGuard(() => message \| null)` | shell `installLeaveGuard()` | Unsaved edits: a hash change or unload asks first |
| Step graph | `grading-guide.js pipelineMarkup`, CSS in `components.css` | `.grading-pipeline`: numbered steps (`.grading-pipeline-number`, `strong` label, `small.grading-pipeline-status` or `.grading-pipeline-fraction`) in ruled columns under a double rule, the current step (`aria-current="step"`) under a 3px rust rule; `[data-size=compact]` draws a row of thin bars | A workflow's stages; status through the words under each label (colour only repeats them) |
| Card | `components.css` | `article.card > .card-head, .card-body, .card-foot` | A separate object (a plan, a course) as a ruled block with one next thing in its footer |
| Data table | `tableMarkup(escape, headings, rows, label)` | `.data-table` (a labelled, scrolling region around one table) | Previews and lists inside dialogs (mail merge, pulls, imports, Gradescope) |
| Dialog layout | `style.css`, `typography.css`, `layout.css` | `#dialog > form > header.dialog-header, .dialog-body, footer.dialog-footer` | Header and footer stay in view; only the body scrolls; one heading |
| Command palette | `palette.js` | `dialog.palette` (combobox over a grouped listbox) | Going anywhere; Ctrl K / ⌘K, the rail's Search, or '/' where a page has no search |
| Shortcut sheet | `palette.js openSheet`, `ui.js registerShortcut` | `dialog.palette.shortcut-sheet` | '?' lists the shortcuts registered for the page on screen |
| System status | `system-status.js` | `.sidebar .server-status` button + `#status-pop` popover | Worker, email gateway, backup, disks, running and queued work; the 'ready for you' / 'finished' toasts |

**Ruled cards, ruled disclosures and disclosure paddings** (`components.css`). Every `.card` and `.empty-state` is a ruled
block (DESIGN-LANGUAGE "Regions and records"): no fill, no side or bottom frame, one hairline above. `.card--flat` and
`.empty-state--flat` remain as hooks; a page sheet still sets the padding and may drop the hairline (`border-width: 0`)
where a heading's rule is directly above. `details.disclosure[data-framed]` is a disclosure the page draws as one ruled
record (a hairline above and below, the body under a hairline). A page that only needs other paddings sets
`--disclosure-summary-padding` on the summary or `--disclosure-body-padding` on `.disclosure-body` (any specificity; the
shared rules read these properties) instead of out-ranking `details.disclosure > summary` with an id. Both are registered
with `@property … { inherits: false }`: set them on the summary or body itself (not on the `details`), and a disclosure
nested in another's body keeps the default paddings.

**Notices, detail groups and dialogs** (2026-09-27; DESIGN-LANGUAGE Ledger 2026-10-04). A `.callout` (and the two older
notice classes aliased to it, `.grading-warning` and `.guide-note`) is a framed box (`ledger.css`), warning, danger and
success lightly tinted in their tone, its title 16px 800 in the tone's ink. A `.next-step` is a 3px orange rule over a heading and its one link. A `details` folded into a
callout spans it (`.callout > details { justify-self: stretch }`), so its +/− sits at the callout's right edge.
`.detail-group` (`typography.css`) is a flat section with no frame, side rule or indent (which read as a nested frame
inside panels). Only a group that directly follows another group draws a hairline (`.detail-group + .detail-group`:
the sections of a dialog such as New seating chart); a group after a heading, a paragraph or a page's own records draws
none, so it never doubles a record list's own divider and never becomes a partial-width rule inside a toolbar column
(the exam's Changes note). A page that wants a divider there draws it on its own record. `.detail-group.wizard-indent`,
the options that belong to the choice above them, keeps its indent without any rule. Dialogs (`#dialog`, the palette, every `dialog`) are one
plane of raised `--paper` in a 1px `--ink` frame, like the status pop-over and the action menus: header, body and footer
share the surface and are divided by hairlines. Inside a dialog `--canvas`, `--surface`, `--surface-alt`,
`--surface-hover` and `--highlight-bg` point at that paper (`outline-theme.css`), so a data table's opaque sticky head and a
hover take the dialog's surface; tokens derived on `:root` inherit as finished colours, so add any new paper-derived token
to that rule too. Seat fills keep their own contract. A tab bar's look is set through hooks on the bar
(`--tab-padding`, `--tab-align`, `--tab-rule`, `--tab-rule-hover`, `--tab-rule-selected`, `--menu-selected-marker`; the compute
menu's ruled vertical list sets them), never with a selector that has to out-rank the shared nested rule.

**Shared page helpers.** Besides the components, pages share these pure modules instead of re-deriving the same
thing: `ui.js` `wallDate`, `daysFrom` (campus wall-clock dates without time-zone conversion), `localToday` (today's campus date in America/Chicago, whatever the browser's zone) and
`stateLabel`; `grading-guide.js` `guideSteps`, `pipelineMarkup` and the setup steps' `stepHeader`/`stepNext`;
`grading-overview.js` `assignmentNext` (a grading plan's next step, used by Grading, the Scan records page and Home; its
`waiting` says no paper reached grading yet, and its button then leads to the scan: Upload scans or Finish the scan checks,
opening Collect's upload or waiting scans when the assignment is known, I1/I2);
`scan-flow.js` `scanCard`, `scanNext`, `scanNextStep`, `scanFlowNav`, `revealCurrentStage` and the chip tones
`SCAN_TONES`/`STATUS_TONES`; `print-files.js` (a job's or an export's files and the remembered label media);
`calendar-feed.js` (the calendar vocabulary, grouping and one clock format, shared by Calendar and Home);
`seating-state.js` (a chart's next action, filters, list grouping and room review seats). The stage rule the
Assignments cards and the exam hub share is server-side (`exam_workflow.overview.assignment_stages`).

**Page header contract.** `app.js` gives every route its defaults from one views table (title, subtitle, trail, variant,
primary action; the actions are the former per-route ones, dispatched by `data-action`). Every `createX(options)`
receives `header`. A view that knows its object calls `header.set({route: location.hash, title, context, subtitle,
crumbs, action, secondary, updated, variant})` from its render: the call is ignored unless `route`'s path is the one on
screen, lasts until the path changes (the shell's own re-renders keep it), and overrides only the keys given. `action:
null` hides the primary action; an action is `{label, icon, run}` or `{label, icon, action, data}` (the shell's
`data-action` dispatch); `secondary: [{label, run | href | action}]`; `updated: "Updated 04:51"` fills the one 'Updated'
slot (pages show at most one, never per-heading Refresh buttons). A `title` also names the trail's last crumb
(`crumbLabel` names it differently), and `tab` overrides the tab title's label (the Inbox's "Students · Quiz 2").
The shell calls `header.defaults(spec)` with the views table's entry on every route and `header.setTitleParts({count,
name})` with the attention total and workspace name; views never call those two.
`header.reset()` returns to the defaults. The shell puts the defaults back whenever the path changes, so a view whose
quiet `render()` keeps its content when the instructor returns to the same path must call `header.set` again there (the
Exams page, Response Cards and the Inbox's scan pages keep their last spec for this). Home is the only `hero` header
variant; the header band is the same on every route ("The screen design language" below). The header paints the band
too (`createHeader({onPaint})` fits the title after every paint, `page-title-fit.js`): `label` replaces the band's label
(else the context line), and
`attention: true` prints the subtitle's first clause (up to " · ") as an orange block. Keep the ids `#page-title`, `#page-subtitle`,
`#primary-action`, `#page-context` and `#today`. The tab title is `(N) <last crumb> · <workspace name>`. The header's
actions (the primary, the secondaries as text actions, the Updated time) sit right of the subtitle on the page under the
band; actions that do not fit beside a 24rem subtitle wrap to their own right-aligned row, so a page with many actions
never squeezes its subtitle.

**Quiet refresh contract.** Every view implements `render(force = false)`. A poll whose data is unchanged changes
nothing; a changed poll patches the page in place with `dom-patch.js` (keeping focus, scroll, open disclosures and typed
input). 'Loading…' (`loadingMarkup`) appears only on a view's first mount; later reads keep the page on screen while they
run. `force` is for the instructor's own Refresh and for the read after a mutation.
Follow-up and Office hours (TEACHING-WORKFLOWS TAB NAVIGATION, 2026-10-08) paint their selected tab and loading state on
every new visit, then show that visit's fresh response. Repeated shell renders share one read; a return, write, poll or
focus/visibility refresh still reads. Earlier visits and pre-write reads cannot replace newer results. Their current
view's background refresh keeps input and disclosures; a first-load failure keeps the tabs and offers Try again.
Since the performance pass of 2026-09-29:
- **Compare with what was written, never with `innerHTML`.** A browser never serializes an SVG icon back to the source
  string, so `el.innerHTML !== markup` is always true. Keep the markup last written per element in a `WeakMap`
  (`page-header.js`, `rooms.js`, the Answer areas cards in `grading-locations.js`) and write only when it differs. What
  CSP forces out of the markup (an overlay's coordinates set on `style`) is applied on every draw, not only on a write.
- **Timed read polls wait while the tab is hidden.** Wrap the timer callback in `ui.js whenVisible(run)`, which runs it now,
  or once when the tab is shown again, keeping the page's own `active()`/`isConnected` guard inside. Never wrap the wait of
  an action the instructor started (import status loops, the rubrics sleep). Certification (with its
  send hand-off and audit), the Gradescope panel, batch cards, the scan page's 5 s look and Key & Rubric Finalization's
  key-reading look follow this.
- **An unchanged poll does no work.** A poll compares its JSON with what is on screen and draws only a changed one (the
  Grading index did so until it was retired on 2026-10-18; ASSIGNMENT-WORKSPACE "THE GRADING INDEX IS RETIRED"). A failed
  read clears what was drawn
  (`signature`), so the next good read redraws even when the data did not change. A failed BACKGROUND read of the
  page already on screen keeps it (scroll, open disclosures, typing) under one warning callout, "Couldn't refresh. Showing
  the last loaded results." (the Inbox, `documents.js`); a first load or a new page still shows its unavailable state.
- **Poll as often as the page changes.** The Inbox list and scan pages reschedule their own poll after each read: 4 s on
  a scan's page and on Current while anything listed is queued, running or with the worker (or a scan is uploading), 15 s
  on an idle Current, a minute on History, Archived and Deleted. Showing the tab again, returning to the window or coming
  back to the Inbox from another section reads at once, joining a read under way; the shell's other redraws read only
  when that poll would by now. A one-shot link parameter (`?upload=1`, `?codes`) is taken off the address before the
  read, so the page is read once. Every action still reads straight away.
- **Conditional reads.** `api.revalidate(path)` (request.js) sends `If-None-Match` and returns `{data, etag}`, keeping the
  last answer for up to 4 paths; a 304 never reaches `onConflict`. `api()` is unchanged. Callers must not mutate the
  returned value. Used for the Inbox job page and the scan read (`/api/inbox/jobs/<id>`, `/api/documents/messages/<id>`).
- **One read per navigation.** The shell renders a view and then renders it again after its state read: an unforced render
  of the address already loading joins that load (Inbox, Exams, seating chart; Absences, the question bank, Canvas and a course's
  Canvas content since 2026-10-02), cleared in `finally` and keyed by the
  address and generation so leaving and coming back always reads again. Any write drops a joined read (the Inbox wraps its
  `api`), so no read that started before a write answers after it. Grading reuses an assignment's plan read for up to 10 s
  across its own steps and review links, never after a write made through the view (its `api` is wrapped the same way), a
  forced render, leaving `#grading`, or a grading change in the shell's overview (the instructor's freshness call, perf-1).
- **Client-owned attributes.** `dom-patch.js` removes every attribute the new markup lacks, except those in `CLIENT_OWNED`
  (none today: `motion.js`'s off-screen mark left with the cube graph's animated dashes, 2026-10-03). A new attribute that
  page scripts set on patched elements must be added there, or the next poll strips it. A patch never closes an open disclosure
  (2026-10-02): `open` stays on a `<details>` whose other attributes the new markup keeps, so a fold the instructor opened
  (and what was typed in it) survives a poll that changed only a count; a patch may still open one, and a fold whose other
  attributes change (another list item now at its position) follows the new markup.
- **Keyed lists.** Children are matched by position and tag, so a list whose members come and go (a filtered list, such
  as the Scan page's "Pages that need a look") gives each item `data-patch-key` (2026-10-02). A keyed child is matched by its
  key among its siblings: an item that stays moves into place with its open folds and typing, a new one is inserted, and
  one item's element is never patched into another's (a recovery form typed for one page would otherwise post to the next
  page's id). Plain markup never takes a keyed element's place.
- **Finished content.** An element that never changes once drawn and that a script has since built inside (an Assistant answer
  whose math KaTeX drew) carries `data-patch-static="<mark>"` (2026-10-09): while the new markup has the same mark at that
  place, the element and everything in it are left untouched; another mark there is patched as usual. Use it only for content
  that cannot change, with a mark that changes when the content would.

**Button tiers** (`button-theme.css`, `ledger.css`, DESIGN-LANGUAGE §4; button and action labels are sentence case,
DESIGN-LANGUAGE §1.12 SENTENCE-CASE ACTIONS).
`.button.primary`: the ink block (`--button-fill`) with white type; at most one per page header, panel or dialog.
`.button`: white in the 2px ink frame with the 3px hard shadow, square, 14px 700 sentence case; hover lifts it 1px, press
sinks it 2px, and a pressed/selected `.button` (an applied choice, a pinned tool) is the ink block (`ledger.css`).
`.button[data-outward]` (send, publish, push) has the outward blue behind its frame. `.text-button`: the tertiary action, no
box, 14px 700 ink underlined at 2px, the underline thicker on hover (never orange); the header's secondary
actions are drawn the same way, all sentence case (one `:root:root :is(.text-button, .page-heading-secondary > …)` rule at
(0,6,0) outranks the tier type and page rules up to (0,5,0)). `.link-arrow`: a sentence-case go-link with a trailing arrow
("Review →"), ink, underlined on hover, for navigation. `.button.danger` / `.danger-button`: red type in the frame. Tab
bars are framed words with the chosen one the ink block. `tests/shell_checks.py` `check_sentence_case` fails any unframed clickable drawn in capitals on the shell routes
(folds open) and their dialogs. Exam-version and seat buttons
keep their own palettes (excluded by the existing `:not()` list); forced colours use system colours.

**Busy and blocked buttons** (requirement, 2026-10-02: an Import blocked on a column's points showed the macOS pinwheel
and read as stuck). A disabled button shows `not-allowed` (`style.css`): it waits on the instructor, and the page says
why. The wait cursor is only for work under way: a `button[aria-busy="true"]`, or a disabled button inside an element
marked `aria-busy="true"`, `data-saving="true"` or `data-busy="true"` (what `runFormAction`, `saveForm` and the page
save helpers already set). A button that starts a request without one of those uses `setBusy`; pages add no cursor
rules of their own.

**Press and open motion** (requirement, 2026-10-04, after the Assignments review: "apply both"). Motion only where it
answers the instructor, never where it delays them. Since Ledger (2026-10-04, after an Emil Kowalski motion review) a framed
control (`.button`, `.danger-button`, `.icon-button`, the Assistant launcher, a suggestion's choice, and the records the whole
card opens) lifts 1px under the pointer with its hard shadow growing from 3 to 4px, and sinks 2px onto it while pressed, in
`--lift` (80ms `--ease-out`, transform and box-shadow only; the shadow animates to a zero-offset ink shadow, never `none`);
the keyboard's state is the slate ring, not a lift; a `::before` hit area that stays put keeps a quick click at the edge
from being lost. A control drawn as a text action or a tab never lifts. `.text-button` and `.link-arrow` still give 3% while
pressed (`transform: scale(.97)`, 120ms `--ease-out`, `button-theme.css`; never a disabled control or a seat). A toast comes
in over 200ms (up 8px from clear, `@starting-style`) and leaves at once. A `<dialog>` opened with a click enters in 160ms: it fades and settles from `scale(.98)` around its
centre with its backdrop fading alongside (`components.css`, `@starting-style`), and closes at once. The Assistant's drawer and
floating window follow the same rule (2026-10-09): opened by a click (`data-entered="click"`), the drawer fades in and the window
fades and settles from `scale(.98)` in 160ms; Ctrl/⌘ I and the Command gestures show them at once. The command palette
and the shortcut list (`dialog.palette`) open from the keyboard many times a day and appear without motion; so do tab and
area switches, which change content in place. `--ease-out` is `cubic-bezier(.23, 1, .32, 1)`; colour hovers keep
`--snappy` (0.1s `ease`). Reduced motion is gentler motion, not none: fades of colour and opacity stay, and nothing moves or
scales (`layout.css`, `ledger.css`). A dialog that opens on a form puts focus on its first field to type into (`autofocus`), not on its
close button: Create assignment and Draft a quiz on the title, New exam on the course assignment.

**Navigation.** A change of the route's path scrolls to the top and focuses `#page-title` (not while the instructor is
typing in a field); a query-only change keeps the page. Back and Forward restore the page's scroll (each history entry
carries `history.state.facultyEntry`, positions in sessionStorage; the shell keeps the key when a view rewrites its
address with `history.replaceState(null, …)`, though views should pass `history.state`). Moving between one course's tabs keeps the tab focus.
The breadcrumb links the same pages as the sidebar, so a test that clicks a section by its name scopes it to `.sidebar`.

**Sidebar.** Fourteen pages in lifecycle order (Home, Calendar, Mail, Student follow-up, Recordings · Courses, Canvas, Accommodations,
Absences · Assignments, Seating charts, Response Cards, Teaching Inbox · Rooms), then the sticky footer with Workspace settings
and the system status. Teaching review has
no rail entry since 2026-10-18 (pipeline review P3, approved: "make all of your suggested changes"): `#teaching-review` stays,
opens from the Assignments index strip and the palette's Actions, and highlights Assignments. A scan page (`#inbox/job/...`)
highlights Assignments while the assignment shell's context is active (`faculty:assignment-context` with `active: true`, or
`#assignment-workspace[data-area]`), otherwise the Teaching Inbox (`page-header.js navEntry`). The footer's top rule is its own, so a list that runs
on under it visibly continues beneath it. Windows up to 960 px tall get a tighter rhythm (same type and rules) so every
entry fits 1440 × 900 and 1536 × 864; on a shorter window the shell scrolls the rail (never the page) so the page's own
entry sits above the footer, on every navigation (course pages included) and when the window is made shorter (`app.js
revealNavEntry`, 2026-10-02); the entry revealed is the one the rail highlights. One Courses entry stands for both course lists and every course page except a course's
Canvas pages (`#course/…/canvas`), which highlight the Canvas entry (`#canvas`, `canvas-hub.js`; on those pages the course's
tabs read All sections and each section and stay in Canvas, canvas.md THE CONTENT PAGES, 2026-10-11); the course list's
own Current | All switch carries `#current-courses-count` and `#all-courses-count`. Attention badges are the server's
`/api/workspace/overview` attention cards, read once per shell poll into `overviewStore` (Home renders from the same
read) and mapped by href (`attentionBadges`, `BADGE_ROUTES`: Teaching Inbox, Response Cards, Seating charts, Absences,
Student follow-up; cards routed to #documents, #exams or #grading are Assignments'); link names stay the page names and the
count is the link's description. ONE COUNT OF WHAT NEEDS YOU (requirement, 2026-10-18: "make all of your suggested changes",
pipeline review item 3; frontend.md): the Assignments badge counts decisions, one per such card (`assignmentDecisions`), as
the assignment tabs and the index count needs-you items, so `grading:<plan>:review` is one decision however many responses it
holds; the tab title's '(N)' counts those cards the same way, and Home's rows of an assignment's work are one item each
(`items: 1`; the count column still shows the responses), so the hero, the tab title and the badges agree.
Worker, storage and backup problems show as Home's health dot. The tab title gets `(N)`. A Teaching Inbox card counts
only with `derived: true` (counted from the Inbox's own filing, PRESENT only): a scan's stored status stays
`needs_review` forever, so a stored count would never go down. Cards carry additive `key`, `detail` and `verb`: the Inbox
card is `{key: "inbox", label: "Scans and submissions that need you", derived: true, scans, submissions,
submissions_failed}`; grading cards are `grading:<plan>:review|failed|finalization|areas`, labelled with the assignment's
needs-you labels (2026-10-18): "Review grades · <title>", "Inspect failed grading · <title>" (`#grading/<plan>?tab=review&open=failed`,
verb Inspect), "Review the rubric check · <title>", "Check answer areas and templates · <title>" (verb Check); Home's rows read
once Grading is read say "Decide paper versions", "Finish grading preparation" (was "Papers waiting for <step>"), "Certify
grades" and "Grading did not start by itself" (verb "Open Grade") (light counts: responses
waiting for review, failed to grade, a finished rubric check waiting for review and the questions whose suggested answer
areas wait for a look, 2026-10-02; the finalization card follows the Grading page's own rule, reading what the check read
through the grading service, so saving an answer key leaves it and a line comment or rubric edit makes the check stale;
version decisions are left out, amendment 24, and appear in Home's Needs you once Home has read `/api/grading`; so does
`grading:<plan>:certify`, "Certify grades", when the plan's current step is Certify: every paper
graded and reviewed, or grades changed since a certification, and no linked scan still in its scan checks (2026-10-11,
ASSIGNMENT-WORKSPACE "READY TO CERTIFY MEANS NOTHING IS STILL COMING": the row said "every paper is graded and reviewed" while
certification refused); usability audit, 2026-10-04; students with no scanned paper have no row, ASSIGNMENT-WORKSPACE "THE ZEROS
ARE SAID AT CERTIFY", 2026-11-05); card mail is `cards_flagged`, `cards_unconfirmed`, `cards_failed`, `cards_receipts` (one issue
links to `#response-cards/issue/<id>` on its Flagged or Not delivered filter, several to `#response-cards?attention`);
`seat_updates:<chart>`; queue cards `queue:<key>:failed|review`, and `queue:<key>:failed|review:<plan>` for a current
plan's rubric preparation, rubric drafts, rubric checks and answer-area checks, linking the step that shows them
(`#grading/<plan>?tab=rubrics&focus=preparations|templates`, `?tab=answer-key&open=quality`, `?tab=locations`); an
answer-key reading, which shares the drafts' queue, is `queue:frq_rubric_drafts:failed|review:<plan>:key`, named "Answer key
readings" and linking `?tab=answer-key`. A grading-prep queue whose failed and review rows all belong to one current plan's
step takes that step as its own `href`, so its health-table row and the shell's notice open it too. Grading
task and scan inspection failures have no queue card (their owner cards count them: `grading:<plan>:failed`, the Inbox
card) and stay in the health table; one failed print job links `#documents?batch=<id>`. A queue counts a failure only while
it is the latest word on its work (`workspace_overview.py` QUEUE_SPECS; drafts and answer-key readings each against their
own kind). The overview also returns `current_scans` (at
most eight, Current order), `current_scans_total`, `finished_recent`, `finished_recent_archived` (those of them whose
assessment is certified, which the Inbox files under Archived) and `current_grading_plans`.

**Deep links that open a control.** A page reads its query once, opens the control, then drops the query with
`history.replaceState(history.state, "", …)`: `#grading?settings=1` (Compute settings), `#response-cards?email` (Email
settings), `#response-cards?attention` (the attention strip), `#inbox?codes` (Codes & API access), `#inbox?upload=1` (Upload
scanned assignment; an 'Upload scans' next step links there only when no assignment is known),
`#assignments/<a>/<t>?area=collect&upload=1` (Collect's own Upload scans, once; every other 'Upload scans' links there, I1,
2026-10-18), `#assignments/<a>/<t>?area=collect&scans=waiting` (Collect's scans still in their scan checks, I2),
`#calendar?add=1` (Add
event), `#documents?create=1` (Create assignment), `#documents?assignment=<id>&step=details|seating|version|print` and
`#documents?chart=<id>` (the assignment window at that step), `#documents?jobs=<assignment>` (Print jobs filtered to that
assignment's current jobs: the Exams hub's Print stage, an approved quiz's page), `#documents?batch=<id>` (that print job's
window), `#gradebook/<term>?assignment=<aid>&pull=1` (that column's
pull preview), `#grading/<plan>?tab=rubrics&focus=preparations|versions`, `#grading/<plan>?tab=export&open=gradescope`
(Certify's Gradescope panel, linked from the grading page bar: `grading.js` passes `open` to the
certification view, which opens and focuses that fold), `#grade-delivery/<plan>?open=gradescope` (Return's own Gradescope
details fold, once per address; TEACHING-WORKFLOWS "ONE LINK PER PLACE"), `#seating/<chart>?packet=1` (that room's
exam-day packet dialog), and
`#accommodations?tab=exams&course=<code>&exam=<title>&when=all` (the DR schedule filtered to one exam; filters stay in the
address). Filters that stay in the address the same way: `#rooms?filter=review|ready|empty`,
`#response-cards/issue/<id>?status=<state>` or `?delivery=problem`, and `#exams/<id>?tab=<key>`; the exam hub of an
assessment without an exam record is `#exams/assessment/<assignment>/<term>`. Workspace settings' 'Settings elsewhere' rows use the first three. Links only navigate: every action keeps its
page's own checks. `tests/browser_cross_links.py` follows each cross-link between one assessment's pages.

**Keyboard.** All shortcuts go through `registerShortcut({key, label, run, release, cancel, when, allowInInput, scope, group})` in `ui.js`,
one registry with shared document listeners; it returns the function that unregisters. `Meta Meta` registers two complete
Command taps; `Hold Meta` runs after 600 ms, calls `release` on an unmodified Command release, and calls `cancel` on an
interrupted hold. Drawing during a hold keeps it armed; other pointer actions clear partial taps. Neither consumes ordinary
Command chords. The shortcut sheet shows these as `⌘ ⌘` and `Hold ⌘`. Alt+letter also matches the physical
key (`event.code`), because Option+letter types another character on macOS; `group` lists several keys as one row of the
'?' sheet (the review desk's digits). A shortcut never fires for an event something else handled, while the instructor types (inputs,
text areas, selects, editable text) unless it allows that, inside a `[role=grid]` (the gradebook), or while a dialog
other than the palette is open; the newest active registration of a key wins. The shell owns `Ctrl K` / `⌘K` (palette),
`?` (the shortcut sheet: `listShortcuts()` for the page on screen) and `/` (the course or tools search, else the first
visible `main [data-page-search]` box, else the palette). No shortcut may tick a group approval's "I looked at every ticked response" box.
Registered page shortcuts (each active only while its page or panel is on screen, so the sheet lists only these):
Calendar T today, P and N the previous and next month; the grading setup navigator `[` and `]` (previous and next
question); the review desk 1–9 (tick that rubric line by its displayed number), 0 then a digit (lines 10–19), Alt+A
(submits the ordinary approval, which the server refuses without the tick), Alt+N and Alt+P (next and previous response);
Certify: Escape closes the student drawer, else the open question, and Alt+J and Alt+K open the next and
previous student of the table as filtered (these three also act from a button or a checkbox, never while text is typed; not
Alt+N/P, which the desk in the drawer keeps).
The gradebook grid handles its own keys inside `[role=grid]` and lists them in its Shortcuts popover. Every searchable
page marks its main search box `data-page-search`.
Typing is not a shortcut: the Assistant's box takes "/" (first in the box) and "@" (before a name) as text and opens a list
(`assistant-pickers.js`), whose arrows, Enter, Tab and Escape work only while it is open; Escape then closes the list, not the
drawer or the chat. The drawer's left edge is a focusable separator: Left and Right move its width by 24 px, Home and End go to
its limits.

**Command palette** (`palette.js`). Pages, recent places (detail pages visited in this browser, localStorage, at most
eight), courses and sections (a section is found by "gradebook" too, since its tab is its gradebook) and each section's
roster, assignments, seating charts, rooms, gradebooks (also found by "setup",
"categories", "weights"), the Inbox's Current scans, and actions. Every query word must appear; a word of digits matches
only from the first digit of a number, so "quiz 4" never matches the 4 of "MATH 140" (2026-10-02). Entity lists are read
lazily from existing GET endpoints (`/api/documents/catalog`,
`/api/grading?view=navigation&health=0`, `/api/gradebook`, `/api/inbox/jobs?status=current`, and `/api/state` rooms and
seating when the shell has not loaded them) the first time it opens and kept for a minute. ONE ENTRY PER ASSIGNMENT
(2026-10-18, pipeline review P3, approved: an assignment was listed up to three times): each assignment is one entry under
Assignments, opening its assignment shell (`#assignments/<a>/<t>`): current terms from the catalog's lifecycle rows, another
term's grading plan by its assignment and term, and an assignment with no term opens Assignments' edit page
(`#documents?assignment=<id>&step=details`). There are no Exams or Grading entity groups. For Jev routing an assignment with
a grading plan is sent under the "Grading" kind so the step question still applies; a routed step opens
`#grading/<plan>?tab=<step>` (inside the shell), labelled with the tab words (Grade, Review, Certify, Answer key, Answer
areas…). Create assignment is the deep link `#documents?create=1`. Other actions go
to the owning page and press that page's own control (a transient `data-action` button), so every
dialog, check and confirmation is the page's own. It writes nothing. Recent places are read and written only through a
try/catch accessor, so blocked site data leaves the palette working with no recent places. One scan's stage pages, one
grading plan's steps and one gradebook's pages are ONE recent place each (`placeKey`), named by the object (the scan's
headline, the assignment) with the page within it as the detail; each context part is named once.

**Dialogs and navigation.** `showDialog` records the page the shared `#dialog` was opened on; leaving that page (Back, a
link, the sidebar, the palette) closes it, after the shell's leave guard has asked about unsaved input (Create New
Assignment registers one while it holds typed, unsaved input). A dialog that is saving stays, and a query-only change on
the same page closes nothing (review fix, 2026-09-26). A window opened from a student's card (the roster's student window, the
gradebook's record: the timeline, an absence, a cell's history, their mail) replaces the card in `#dialog`, so it starts its footer
with "Back to <name>" (`data-dialog-back`), and a saved change to an absence or a cell returns to the card; a new absence opens
its own window, which offers Back (2026-10-02). After an absence save the gradebook's record reads its View again before it comes
back (an excused absence fills iClicker cells, ICLICKER-CREDIT).

**System status and notices** (`system-status.js`). The rail's status button says whether the server answers and whether
work is running, queued or needs a health check; its popover lists the worker heartbeat, the email gateway
(`/api/inbox/access`), the latest coordinated backup (called verified only when the server reported it complete), the
disks and the queues with work, each linking to its page, and 'Updated … ago · Refresh'. Comparing two successive
overview reads gives the notices: a scan the worker finished that now needs the instructor ('Quiz 2: ready for you ·
Students →', linking to the stage page `scanNext` names), a finished scan or queue, a new failure or attention card. At
most three per read, none on the first read, none pointing at the page on screen. The shell owns these toasts; views do
not toast the same events.

**Tabs opened before a restart.** `web_assets.py` serves each build's public files (`.js .css .svg .png .jpg .json .woff2` only)
under an immutable `/assets/<build>/` prefix and, once a page of that build has been served, keeps them in a small
content-addressed archive (`builds/<build>.json` manifests plus `blobs/<sha256>`, blobs written first and the manifest
last under a file lock; unreferenced blobs go after an hour's grace) (`FACULTY_WEB_ASSET_CACHE`, else `<nvme_root>/runtime/web-assets` with configured storage or
`<DATA_DIR>/web-assets`; never `/tmp` or the services' `tmp/web|worker` scratch; the last eight builds). A later process
serves an earlier prefix from it, checked against each file's SHA-256, so a module a page imports lazily still resolves
in an old tab. HTML, API data and teaching payloads never enter it. As a last resort `loadModule(pathOrLoader)` shows a
persistent 'Faculty was updated since this tab opened · Reload' callout when a dynamic import still fails; views warm
their lazily imported modules when they are created.

## Screen typography and hierarchy

Rubric finalization (`grading-rubric-quality.js`, `grading-rubric-concerns.js`; GRADING "GUIDED RUBRIC REVIEW") presents one
decision at a time with its exact affected rubric lines alongside it. Decisions and Rubric items switch views without replacing
draft fields.
Model choices only populate editable feedback; absent choices use the same open text field. Only the existing Resolve concerns
action sends feedback, and only the footer confirmation approves. The summary and full explanations are disclosures. A concern's
prose names lines by their display label: `plainIds` (`grading-rubric-concerns.js`) turns the item IDs of the questions it names
("R7_10") into "Item 10" for display only (whole words, IDs with a letter and a digit), in the decisions and the resolved concerns;
the stored concern and the feedback sent back keep the IDs (2026-10-02). Keep the shared tokens, math escaping, explicit approval,
and the server's points-combination check.

Every scan stage page that is not the current stage opens with a next-step banner (`scanNextStep` in
`scan-flow.js`: the overview's current stage, its words and a button to its page). Every Scan page
(2026-10-02) shows the pages that need a look open (changed content, an alias suggestion, and labels
not read or not current; a page with no label is left to Students, Papers or Bubble sheets where that
stage settles it, a doubtful label never is, nor is a failed label reading that can still be retried,
since its Retry is only on the page card and Home counts it until then, 2026-10-06) and folds every
page under "All N pages", whose cards are drawn only while it is open (a section's scan is hundreds),
with version sorting under "Other options". A scan whose pages carry no label also shows its first
pages as small images (one paper's worth when the assignment's blank template says how long a paper
is); the Students form starts from that paper length and from no assignment until one is the run's or
the scan's. Review draws every other assembled paper only while its fold is open, folds the papers
missing nothing but pages while pages still wait for grouping on Papers (any other question keeps a
paper open), and shows each page's candidates by scan and attachment page with a picture, the latest
review's choice preselected. Page cards and assembled papers carry `data-patch-key`. A failed inspection
is a danger callout with Retry as the card's one action. A failed read says "Couldn't refresh" beside
the page's Updated time and tries again in 15 s; the page reads nothing while the Inbox is hidden.

The Inbox scan page uses `scan-review.css`, scoped to `.inbox-scan-job`, for a
quiet surface, readable sans-serif copy, and one prominent next action. Its
actions and ordering still come from `ScanService.overview`; the browser adds
short labels and tool descriptions without choosing processing or review policy.
Recognition evidence, label settings, and completed steps sit in disclosures;
warnings and ongoing work stay visible. Named tool shortcuts open and focus their
destination. Summary disclosures keep their state through background refreshes.
Page records start folded, with counts and recovery controls inside. No new
retained owner, API, or storage state is introduced.

`grading.js` owns Grading routes and assessment/question setup. Its overview,
rubric, job, review, compute and region modules reuse the shared API/dialog helpers
and design tokens. Async views retain generation guards; job polling stops after
navigation. A forced render of the step on screen (Refresh, the read after a save, finished
local work) keeps the drawn step until the new one replaces it, with no skeleton, the page's
height held while it is rebuilt and the scroll where the instructor was; a step view returns
true when it scrolled on purpose. The setup steps, Grade, Bubble sheets and Certify get `busy` (a dialog, typing or unsaved work) in their options for background refreshes
of their own (Review's options are its address scope). A link followed in the page bar or the
graph, and Refresh or Compute settings pressed from the keyboard, keep keyboard focus. The
Grade step, Review's empty state and its question pages subscribe to the shell's overview store and are drawn
again once when grading starts or stops anywhere or the assignment's review/failed card appears
or clears, never while the instructor is busy; that redraw is quiet: the folds the instructor
opened, the control with keyboard focus and the scroll stay as they were.
Cloud jobs start without any crop approval; **See what is sent** is an
optional look at a job's crops. The Grade page shows only the latest batch; every
older batch sits in Earlier batches, collapsed and unloaded until opened (opened by itself once when a failure the
workflow counts lives there). Each
batch separates completed progress, waiting work, failures and review actions.
`grading-model-comments.js` gives model labels and comments consistent role colors
in review reasons, rubric-item explanations, independent checks and group/bulk views.
The saved assessment source determines the rubric explanation's author, including
adopted and revised arbitration. Faculty flags and instructor decisions stay neutral;
merely mentioning a model does not make a Faculty flag a model comment. Role names
remain visible alongside the theme-aware colors in `design-tokens.css`.
Apply decisions use green and do-not-apply decisions use red, independently of
authorship. Model roles use other colors; the additional reviewer uses slate blue.
In detailed review, only the rubric checkbox changes a selection. The adjacent
rubric heading is a native details summary, so clicking or pressing Enter/Space
opens its explanation without changing a grade. The explanation stays outside
that click target. Unresolved disagreements have a steady yellow outline with
a subtle inner glow around the heading, without animation.
Grouped review shows the current position as “X of Y in this group” beside the
question title, using supporting-copy typography. Previous/Next navigate the
waiting group; its size refreshes after a saved decision. The response dropdown
and approval counts remain in All responses only.
Failed-to-grade responses appear in a compact disclosure, closed by default,
with the count visible. Opening it shows the recorded batch reasons and retry
controls, the only Retry of failed responses (GRADING "ONE RETRY", 2026-10-18: a batch card and the Grade step link
there); collapsing or opening the notice never retries a batch.
Recorded cost stays visible; Batch details & controls holds the full progress
breakdown, measured processing time, estimated cost, settings and optional controls.
Skipped canaries have a quiet visible notice; blocking notices stay prominent.
An exam-sized batch lists only responses needing attention. Polling preserves
disclosure state by stable key and restores keyboard focus. `grading-settings.js` edits tiers (role →
endpoint), named hosted endpoints (it never asks for a key, only the NAME of the
server variable holding one), probes, cost and retry limits. The **Compute settings**
button in the Grading page bar (visible label, settings icon; `#grading?settings=1`
opens it too, over Assignments since the Grading index was retired on 2026-10-18, as does the old `#grading?tab=settings`)
opens `grading-settings-dialog.js` over the current grading step. Settings and
endpoint status load on opening; no extra assessment fetch is needed. The native
popup retains unsaved drafts on close, returns focus to that button, and closes
on route changes. It uses six keyboard-accessible sections, named in the menu by their
titles alone (each one-line description heads its own section): Defaults, Other models
(rubric drafting and review, the Faculty assistant, the coding agent and the benchmark
reference), Connections, What holds a response back (the review policy first; trial
acceptance folded under Local and older workflows), Advanced (Cloud requests: full
concurrency, token limits and one compact row of caps per connection, before the
folded batch limits, local models, Nebius and dedicated GPUs) and, since 2026-10-18, Availability and costs (what the
Grading index showed: `/api/grading/health` and the navigation read's recorded batch costs, read when that section is
opened and on Check again, never on a timer). The split is the
instructor's (2026-10-03: the dialog was "cluttered/hard to read"; Defaults had grown
to about 6,800 px). Defaults opens with a
"Next Cloud batch uses" table (role, connection, model, key and test status, and the
settings a batch would use as one wrapping line of label/value pairs, drawn from
the draft; each role's name is a button that scrolls to that role's controls and
focuses its model, ending any pending keep-your-place restore) and owns the grading-mode
picker and, directly under it, only the picked scheme's own choices
(`[data-scheme-for]`, one or more scheme keys): Cloud's reader, two main graders,
permanent additional-reviewer arbitrator and fourth flag reviewer. The picker offers
Cloud (key `cloud_standard`) and Fully local; Jev Routing, One-shot, Tag-Team, High
Assurance and the optional arbitrator/Jev tie-break controls were removed on
2026-09-23. Historical batches retain their labels (marked "removed") and review details. Other schemes' controls stay mounted, hidden and never
disabled, so switching preserves drafts. An invalid hidden field is revealed
where it stands (`data-scheme-reveal`) instead of changing the selected scheme.
The settings dialog keeps its header and save actions outside the scrolling form.
Cloud teams occupy full-width sections with separate role descriptions (one sentence,
the role's colour from the review page as a marker, `data-compute-role`) and model
controls. Each model selector puts Provider and Model (with Browse) on one row and the
chosen model's options (Reasoning, Host, Responses per call, Concurrency) on the next,
with their short notes, the shared-connection note (once) and the price under that
row; the cards and each role's controls are the containers, so a narrow column stacks
them, and Browse wraps under the model rather than leaving it under 240 px;
provider and reasoning controls must never squeeze the model dropdown. Model roles stay together under the selected scheme.
Changing sections returns to the top; applying a model or saving preserves the
scroll position. Longer arbitration and Jev explanations use disclosures.
Cloud Standard has one team. The fourth model can only uphold the standing grade
or require manual review; arbitrator flags always bypass it. The companion
`grading-settings-layout.js` keeps everyday choices visible and specialist controls
in disclosures. Connections use the shared dialog above the settings popup; edits, renames and removals stay in
a draft until the versioned Save succeeds. Rename updates draft role references,
used connections cannot be removed, and editing invalidates the displayed probe
status. Raw profile JSON and other controls survive tabs and dialog redraws.
Save reveals invalid fields inside hidden sections and freezes inputs during the
request; the waiting cursor and Saving label appear only during that request.
Discard restores the last saved snapshot. `browser_grading_settings.py`
checks full configuration preservation, drafts, references, validation and the
1500 and 1280 px desktop widths. `grading-model-picker.js` opens the searchable OpenRouter catalogue
from the **Browse** button beside each team/benchmark selector or the Connections tab.
Selectors show five recent model choices with the current selection retained, plus
None. Before any history exists they use currently assigned models. Selected
connection prices appear below each selector as estimated USD per million
input/output tokens, including the inherited Standard primary for rubric review.
These reuse the draft's prices without a catalogue request; unknown prices stay
explicitly unavailable. The workspace `recent_models` preference records selections
in the draft in order and persists only
on Save; Discard restores it, and connection renames/removals update references.
Browse also lists all saved connections (including custom providers), usable even
when OpenRouter is unavailable. It
fetches `/api/grading/model-catalog` only inside its guarded child dialog, keeps
search/filter/sort state local, reuses configured model overrides, and applies a
selection only to the existing settings draft. Family metadata is optional and never
restricts selection, including unknown models and Xiaomi MiMo. Neither Browse nor
the connection editor asks for a family. Existing metadata stays on unchanged
connections; manually changing the model clears its inferred family. Role image
requirements still use each provider's advertised capabilities.
Closing, cancelling or refreshing the catalogue never writes settings. The server
caches public metadata separately from grading/health requests and returns a stale
list with a notice on failure. `browser_grading_model_catalog.py` covers these
behaviors, late replies, literal rendering of model names and the 1500 and 1280 px desktop widths.
`grading-versions.js` owns the Exam
versions panel in Setup (the former Rubrics page): per-version progress, the one-time **Say which
version these describe**, and the papers waiting for the instructor's version
decision; the pure per-version progress lives in the import-free `grading-guide.js`.
The route guard also covers in-flight loads, so the shell's state refresh does not
duplicate grading requests. Assignment pages use `/api/grading?view=navigation`
for the existing plan/job/preparation menu, without recomputing every tile. The
navigation reads ask `health=0`; compute health (`/api/grading/health`) loads only when Compute
settings' Availability and costs section is opened, with its own loading/error/retry state. Forced refreshes and mutations
still fetch fresh data. `browser_grading_loading.py` exercises delayed requests,
navigation, retries, deferred health, and leaving a route while it is loading.
`grading-rubric-display.js` numbers items from 1 in each question's complete rubric
order, regardless of the IDs in the uploaded file. Review groups, breadcrumbs,
rubric choices, structured evidence, finalization and the Gradescope Entry View
share this mapping; selected subsets retain those numbers and follow rubric order.
Form values, API requests, grouping keys, stored rubrics and exported data keep
the original IDs. Raw source, quoted model explanations and advanced rule editing
remain faithful to the retained records. Missing items are never assigned a guessed
number. Browser checks import unrelated IDs and verify the approval payload.

`grading-evidence.js` draws **Independent checks** at the top of the individual review from the
retained signals only (risk level, grader disagreement per rubric line, failed symbolic checks in
words, objections, escalation reasons, blocks, endpoint and lineage per role); it is hidden while a
blind review waits and decides nothing. `grading-acceptance.js` owns Review → *Review several responses together* → **Trial acceptance**: what awaits a
confirm-or-overturn look (blind responses first), the running error rate with its upper bound and the
reviews still needed, overturned responses with the signals that should have caught them, and the
only control that can turn real automatic acceptance on (the server refuses without the evidence).
`trial_accepted` and `auto_accepted` have their own labels everywhere a state is shown. Number inputs
whose value is not a multiple of their step silently block a form: use `step="any"` for such limits.
Bulk review selects each inspected proposal explicitly; the review workspace's proposal gallery includes every eligible response
shown, Set aside or Open alone takes one out, and its required tick clears whenever what is shown changes. Human decisions remain
separate from new model results.
Question regions reuse the existing blank-template drawing tool and have numeric
percentage inputs; advanced JSON retains masks/multiple-page precision. No grading
UI action publishes, emails, or clears Inbox review. See [GRADING.md](GRADING.md).

Browser tests that dynamically import an already running module must resolve its
URL against the page's immutable module entry URL. `/static/seat-grid.js` is a
different module instance from `/assets/<build>/seat-grid.js`; importing both does
not test rebinding the same module and can duplicate module-local state.

**The screen design language: DESIGN-LANGUAGE, Ledger** (requirement, 2026-10-04: "go with Ledger"; the binding contract
is `docs/decisions/design-language.md`). A neo-brutalist work tool: one off-white page, white things you work in, black ink,
2px ink frames, a hard ink shadow on what you press, and one loud colour, orange, that means "needs you". It replaced the
Magazine edition (2026-10-03; paper, serif, photograph plates, the sunset arc) after the 2026-10-04 usability audit.
**One appearance and one desktop layout** (requirement, 2026-10-03: "get rid of dark mode and mobile"): there is no theme
switch, no `data-theme`, no Appearance settings, and no phone or tablet tier; `layout.css` sets `html { min-width: 1180px }`
and a narrower window scrolls sideways instead of rearranging. The Lambda language (2026-09-26) is non-binding history in
`docs/history/frontend-lambda-design-language.md`.

- Tokens (`design-tokens.css`, screen only; semantic names only in screen rules). `--canvas` `#f3f2ec` is the page, flat (no
  texture); `--paper`/`--surface` `#ffffff` are fields, framed records, dialogs, pop-overs and the header band. Ink
  `--ink`/`--heading` `#101010` for headings, frames and anything interactive, `--text` `#1e1e1e` for copy,
  `--muted`/`--label` `#5a5a55` for metadata and labels, `--faint` only for decorative marks. `--line` divides groups,
  `--nested-line` rows. Ledger's own: `--frame` (2px solid ink), `--hard-shadow` (3px 3px 0 ink; `-hover` 4px, `-strong` 6px
  for dialogs), `--needs-fill` (`--accent-fill` `#ff6a1a`) with black `--needs-ink`, `--accent` `#c2410c` (needs-you as text),
  `--danger-fill` `#d7263d` with white type (failing), `--outward-fill` `#dce8ff` (send, publish, push), `--font-head`
  (Manrope), `--font-num` (Geist Mono), `--head-weight` 800. The focus ring is slate `--focus` (`--blue`). Status inks:
  `--success` green, `--warning` amber, `--error` red (always with the word "failed"), `--blue` slate; `--grading-role-*`
  keep distinct hues at 4.5:1. Radii are 0. Removed: the Lambda ramps (`--l-*`), `--neon-*`, `--edge-*`, `--wire-*`,
  `--button-edge-shadow`, `--text-edge-shadow`, `--sidebar-slash`; `tests/test_design_language.py` fails if a sheet or
  script references one, keeps a theme switch or a width query below 1180 px, adds paper grain, or if the Ledger tokens
  change.
- Faces (`static/fonts/`, provenance and SHA-256 in its README; nothing is fetched at run time): Manrope for everything
  (headings 800, controls 700, body 500, labels 700 small capitals; `--font-serif` and `--font-display` alias it, so older
  rules that name them get the heading sans); Geist Mono (`--font-num`) for meta figures (the clock, rail counts, badges,
  IDs); Berkeley Mono (`--font-code`) for code, keys, raw records and the seat frames. Seat names keep Inter (print and
  fitting are measured in it). A column of figures read down (a gradebook's percentages) is Manrope tabular, never mono.
  Sizes: `--text-caption` 11, `--text-small` 13, `--text-control` 13, `--text-body` 15, `--text-lead` 17; headings
  `--text-item` 18, `--text-section` 21, `--text-title` 24, `--text-count` 30. The 2026-10-04 switch rewrote every
  `font: 400 … var(--font-serif|display)` rule to `var(--head-weight)` at 0.8× its size.
- `static/ledger.css` (linked last in `index.html`, screen only) states each Ledger component once, at the specificity it
  needs to beat the older theme sheets (several `button-theme.css` rules are (0,4,0) or more): buttons framed with the hard
  shadow (hover lifts 1px, press sinks 2px, nothing under reduced motion), the ink primary, `.button[data-outward]`,
  underlined text actions and ink go-links (hover never orange), framed tab groups with the chosen one an ink block
  (`.assignment-nav`, `.support-tabs` and the shared tab list), chips (needs-you orange fill, failing red fill), framed
  callouts, white framed fields with the slate ring, table heads, folds, dialogs and pop-overs with the strong shadow, the
  toast, the rail and the header band. Page sheets keep layout; colour and frame decisions belong in `ledger.css` or the
  tokens.
- The rail (`workspace-navigation.css`, `workspace-navigation.js`, overridden by `ledger.css`): black from top to bottom;
  the wordmark "faculty." in white with an orange full stop; Search (the palette, Ctrl K) as a dark framed field; group
  labels "A · Today", "B · Courses"… in muted small capitals (the letters are decorative counters); entries 14.5px; the page
  on screen an inverted block (canvas on black, 800); needs-you badges orange blocks with black mono figures; inventory
  counts muted mono. Windows up to 960 px tall get a tighter rhythm so every entry fits 1440 × 900; the footer (Workspace
  settings, the status) is sticky and `scroll-padding-bottom` keeps a revealed entry above it. `workspace-navigation.js`
  only wraps each entry's words in `.nav-face`/`.nav-label-text`; the links, counts and delegated actions keep their original
  nodes, and polling never rebuilds the rail. The page's scroll costs nothing in the rail: no scroll handler and nothing
  written to the root (`browser_theme` checks it). `tests/shell_checks.py` holds the shell's checks for `browser_theme` and
  `browser_shell_routes` (every route and dialog, at 1440 × 900 and 1440 × 1100). The Magazine edition's arc is removed.
- The header band (`.page-plate`; `header-band.css`, `ledger.css`): white under a 2px ink rule, at
  most 90 px, no photograph, grain or filter. First line: the top bar (the trail, the clock in Geist Mono, the area button
  and the Assistant launcher framed with the hard shadow, both 34 px tall: `ledger.css` keeps the area button off the page's
  36 px icon-button size, which made the band 92 px). Second line: `#page-title` in Manrope 800 ink (22–28px; `page-title-fit.js` fits a
  long title before it wraps) and its label beside it on the baseline (muted small capitals: the view's `label`, else its context
  line, less every part the breadcrumb already says (`plateLabel`); no date, which the clock shows, so `#today` stays hidden
  though header-clock.js keeps it current; never a student name). The Magazine edition's plates (`plates.js`,
  `static/plates/`, the generator in `tools/plates/`) and the views table's plate keys are removed. The title takes focus on navigation but draws no ring (it is not a control). Print and forced
  colours: the title in plain ink.
- The needs-you chip (requirement, 2026-10-04: "change all of those"): with `header.set({attention: true})` the subtitle's
  first clause, the attention count, is the orange chip on the title's line in the band and the rest of the sentence follows
  it muted (`#page-status`, no closing full stop: Home's "3 things need you  Next: …"); `#page-subtitle` keeps the whole
  sentence, hidden. Under the band, on the page (`.page-heading`): a lead line that only describes the page (`#page-subtitle`,
  muted 15px) when there is no chip, then the page's one primary and its secondaries as text actions on the right. A row with
  nothing in it is not drawn. Settings and imports are never the primary (course home has none: Course settings is a
  secondary; Accommodations' Import from AIM is a plain button). Course pages have no "Back to …" link: the trail's Courses
  crumb goes back to the collection that opened the course.
- No paintings (requirement, 2026-10-04: "remove paintings"): `paintings.js`, `paintings.css` and `static/plates/paintings/`
  are gone, and `tests/test_design_language.py` checks nothing names one. Student follow-up's review puts the student's email
  (sticky, with the follow-up's own actions, `.support-review-source`) beside the suggested actions (compact,
  `.support-review-body`), and the week's office hours are a short list under the conversation queue (`.support-side`,
  `.support-hours`, from the calendar feed `GET /api/calendar`, campus today through six days, events titled "Office hours" or
  ending in it after a separator ("MATH 140 · Office hours"), read on entry, after a save and at most every five minutes).
- Regions and records (`outline-theme.css` lists the regions; `ledger.css` draws them): a page region (Home's panels,
  course, grading and compute panels, Inbox lists…) is ruled, not boxed: a 2px ink rule above, no fill, its text on the
  page's column, rows on `--nested-line`. A record in a grid of records (a tool, a room, a seating chart, an assessment, a
  scan) is a white box in the frame; one the whole card opens takes the hard shadow on hover. Dialogs, the palette,
  pop-overs and menus are white in the frame with the strong shadow; inside a dialog the page tokens point at that white,
  so sticky heads and hovers match it.
- Decorative pseudo-content (the "A · " group letters, the rail's dot, `+`/`−` markers, the link arrow) is written
  `content: counter(decor, none) "→" / "";`: the empty counter draws nothing, the string is what shows, and the empty
  alternative after `/` keeps it out of accessible names. Do not write the shorter `content: "→" / ""`: Playwright 1.49 (the
  checks' version) does not read the alternative text of a plain string and puts it into the element's accessible name.
- Specificity hooks: `:root:root` (two pseudo-classes, (0,2,0)) is the theme sheets' bump over page sheets; it replaced
  `:root[data-theme]` one for one when the appearance switch was removed, so the cascade did not change. The heading rule
  in `typography.css` stays at (0,1,1): a page sheet turns an `h3`/`h4` into a small label with any two-class selector;
  never add a compound selector to that list.
- `icons.js` draws the plain ink line pictograms (their RGB edge layer is never shown); every SVG is decorative and
  unfocusable, the parent control names it. `accent-theme.css` maps older literal-colour badges, dots, swatches and crop
  overlays onto the status inks (round dots; the template editor's answer area is an orange wash over white paper).
- `static/header-clock.js` renders the top bar's date and clock in the browser's local time zone, "Sun, Oct 4 · 2:31 PM": no
  year, seconds or time-zone name, moving once a minute on the minute (Ledger; requirement, 2026-10-04: "change all of
  those"); it updates the Today label's date at local midnight and suspends its timer while hidden; it needs no server polling
  and never converts campus-local exam dates. The top bar's only controls are the Faculty assistant's two launchers (the area button and the Assistant).
- Seats (`seating-theme.css`): Berkeley Mono box-drawing glyphs form decorative seat frames (`seatFrame` in
  `seat-grid.js`, aria-hidden, no pointer events, hidden in print; they change no seat dimension or label). Version seats,
  count keys and editor choices derive their screen outlines and labels from each letter's hue; gray versions stay
  neutral; `--exam-version-fill` and the print palette are untouched, and frame glyphs and screen colours never reach a
  printed chart. Printed documents, paper previews and fitted seat labels keep their own font contracts (hidden seating
  print measurement nodes too).
- Checks: `tests/test_design_language.py` (static: removed tokens, theme switches, phone widths, Ledger's tokens, no paper
  grain, `ledger.css` last, no plate or arc code, no paintings, fonts against their README) and `tests/browser_theme.py`
  (one appearance on a dark OS, 13 screens at 1440 and 1280 on the white header band beside the black rail, framed buttons,
  a framed dialog, the slate focus ring, contrast, seats and their palettes, print as plain ink, forced colours, the local
  clock at midnight). `tests/tools/app_shots.py` screenshots a demo server's routes
  (one appearance; `--width`, default 1440). Restart the web service after frontend changes to refresh its immutable
  asset snapshot.

`static/typography.css` follows the shared palettes and feature styles, for screens only. Use `section-lead` for
the primary instruction, `supporting-copy` for an explanation, and `detail-group`
for subordinate records or form controls. A detail group is a flat section instead of
another nested panel, divided from a group directly before it by one hairline;
`.wizard-indent` groups (the options of the choice above them) are indented without a
rule. `.supporting-copy` scores one class (`:where(body)
.supporting-copy`), so a page sheet's own `.panel p` rule (a margin reset, a size) wins
over it although `typography.css` loads later; `.section-lead` still scores (0,1,1). Keep the
existing layout classes alongside these semantic classes. Do not repeatedly nest
groups or move essential actions into supporting text.

`static/bubble-sheets.css` keeps the scan reader's context, review counts, and
actions separate. Recognition evidence and identification breakdowns belong in
Reading details; the confidence control belongs in Identification settings.
Disclosures retain their open state across refreshes, while warnings remain
visible beside the counts. These are views of the existing read, not a replacement
for the scan's canonical `ScanService.overview` summary.

Entering or leaving Home returns scroll and keyboard focus to the chosen page heading. Reduced motion keeps fades and drops movement ("Press and open motion").
Run `python3 manage_checks.py browser --browser navigation` to check routes, dialogs, persistent node identity, keyboard
focus, browser history, direct entry, desktop widths and hit targets through the reprint over the plate.

`static/calendar.js` owns the Calendar month/agenda views and custom event forms;
`static/calendar.css` keeps day schedules and event details separated and follows
the shared themes. Dates use explicit wall-clock arithmetic, without browser
timezone conversion. The loaded range and request generation prevent old data
from appearing as an empty new month; dialog intent and session guards preserve
newer dialogs and drafts. See [Calendar](CALENDAR.md) for ownership and checks.

`static/assignment-templates.js` is the upload/mapping editor of Exams → Manual scores
(the assignment wizard used it too until 2026-09-26; its Paper step now points to Grading). Its optional page rectangles use normalized
top-left coordinates and CSSOM overlays under the existing CSP. Saving a mapping
and applying it to an exam are separate actions. Multi-page maps remain intact
when editing rubric criteria; grading displays every selected region. The
screen-only styles live in `static/assignment-templates.css`.

Assignments & printing has separate assignment-library and print-job tabs in
`static/documents.js`. The library is a list of lifecycle cards (one per assignment and
current term, from the catalog's read-only `lifecycle`: a compact Paper → Seating → Print
step graph (thin bars) whose stages open `#documents?assignment=<id>&step=…`, the stage that matters now
in words under it, one primary for the next unfinished stage, else
Grading for a plan with responses or set up before any paper (an approved quiz, or Set up grading now), and Exams page,
Grading and Quiz draft links; while the term's exam record has no grading plan at all, a ⋯ menu offers Set up grading now,
requirement, 2026-10-02); print jobs are grouped by export, with
registration jobs folded under "Issued for scanned papers". One counting rule
(`countedJob` in `static/documents.js`): the Print jobs list opens on Current (no archived,
superseded or cancelled job), and the tab badges and the results line count what that list
shows, registration jobs left out (their folded group has its own count); a card's
"N print jobs" counts the list its button opens (`jobsOf`: its current jobs, else its
archived ones, superseded included, under Archived). An empty list names the status that
holds the jobs (All statuses, or Archived) instead of looping back to Current. A quiz draft
ready for review leads the attention line above the tabs. `static/print-files.js` is the
one renderer of a job's or an export's files (`jobFiles`, `exportFiles`, `stackFiles` for
one interleaved stack of every version, `MEDIA`, the remembered label media), shared by the
job dialog, the group headers and cards, the wizard's last step (Stamp and print: a stack's
printed row, its queued jobs and the stack it creates, all through `stackFiles`) and the
seating chart's print-jobs table. Search, canonical course filters, job status, and pagination
are browser view state; they create no new resource owners. The catalog lists every
print job and the newest 100 registration jobs. Polling
preserves filters, focus, and unchanged result nodes; deep links survive catalog
retries. `static/documents.css` styles the library, while
`static/document-batch.css` separates print downloads from optional labels, copy
assignments, and audit disclosures. The existing assignment wizard and export
operations retain their original checks and explicit actions.

`static/gradebook.js` (`createGradebook`) owns the term gradebook's pages (its grid names the morning sync with Canvas,
`nightlyNotice`, only when it needs the instructor: Sign in to Canvas, or Sync with Canvas for an assignment waiting for a column,
grades left to the instructor's own Sync or posts that did not take; its header's one Canvas action is Sync with Canvas, Start
from Canvas until the term is linked, `syncState`, with a notice only for held grades or a send Canvas did not confirm, counted
from `send.unconfirmed` so it goes once a later read confirms them, and saying in the same sentence the posts the morning sync
could not make for that send, which `nightlyNotice` then leaves out: one banner, one button; a
column's heading shows "N hidden" and, dashed in ink, what the next sync does about its posting, `pendingChip`; `iclickerNotice`
names an after-class iClicker import that needs the instructor; a WebAssign column's cells are read-only, `data-locked`)
(`gradebook/README.md`): the grid at `#course/<gid>/section/<n>[?item=&assignment=]`, where
`item` focuses a column and `assignment` focuses the column linking that assignment or offers
to add one, its setup and changes at `.../setup` and `.../changes`, and the section chooser at
`#course/<gid>/home/gradebook`; the earlier `#gradebook`, `#gradebook?group=<gid>[&section=<n>]`
and `#gradebook/<term_id>[/setup|/changes][?section=<sid>&item=&assignment=]` addresses are
resolved and replaced by these (a bare `#gradebook` opens Current courses). Its exported pure helpers (`parseRoute`, `parseEntry`,
`parseBlock`, `gridModel`, `sortRows`, `cellParts`, `gridMarkup` …) hold no state. The grid
is one fixed-layout table with a sticky header and name column; widths are set by the script.
On wide screens Overall and Letter are frozen at the right edge, and the category percentages
join them (`table.gb-freeze-cats`) when there are at most three and three score columns still
fit beside the frozen block; the decision is measured before the table is added and again on
resize. The frozen block has a heavier left edge, shaded while scores pass beneath it
(`.gradebook-scroll.is-overlapped`), and its own band row cells, so no column band or partial
column reads as part of it. The grid is ONE tab stop (roving tabindex; the scrolling region is
not focusable). Keyboard: arrows move (Ctrl or Cmd to the edge), Home/End and Ctrl+Home/End,
Page Up/Down by 20 rows, Up from the first row reaches the column headings and Left from the
first heading the corner's Name and Section sort buttons; Enter, F2, Space or typing edits;
in the editor Enter/Shift+Enter and Tab/Shift+Tab save and move, Esc cancels and leaving the
cell saves; Delete or Backspace clears; Shift+F10 (or the context-menu key, a double-click or
the cell's info button) opens the cell's history; Alt+Down opens a column's menu. `EX` and
`MI` are words, a number is points (four decimals at most), nothing clears. Ctrl+C copies one
cell; a pasted tab-separated block fills from the focused cell (a blank in the copy clears its
cell, and the toast says how many), refused whole when any value is unreadable or any target
cell is still saving. Score writes are one POST per commit, sent one at a time and never
retried by the page; the same change entered again after an unknown outcome reuses its request
ID, and a 409 naming stale cells reloads the View. The write's `requests` (the latest 20)
replace "Recent changes" in place, so a write never reads the View again.
A cell's source dot, note corner and tints have
text alternatives: the focused cell's accessible name, its hover title and the Keyboard and
entry help. The column menu is a non-modal popover (`role="dialog"`), closed by Esc, a click
elsewhere or tabbing out, which returns focus to its heading. An error page (a 404 term) is
that address's finished page: the shell's refreshes do not load it again. Sort, search, the
focused cell and the setup's Find a column are browser state only (a section tab shows only its own section); the section of each course opened last
(the section chooser's primary) is a per-viewer `localStorage` convenience. A section tab reads its View with `cells=section`
(that section's cells, plus the term-wide `cell_counts` the setup shows). The setup page and the dialogs
live in `gradebook-setup.js` (`renderSetup`, `unsavedSetup`) and `gradebook-dialogs.js`
(`openColumn`, `openLink`, `openPull`, `openImport`, `openHistory`), loaded by dynamic
`import()` on first use (dialogs are warmed after the grid draws), so a fault in either leaves
the grid and the rest of the workspace working. Each receives the `GradebookContext` typedef'd
at the top of `gradebook.js`: `api`, `escape`, `icon`, `toast`, `showDialog`, `dialogHeader`,
`termId`, `view` and `sectionId` getters (always the View on screen), `reload()`,
`replaceView(view)` for a View returned by a write (one carrying `item_id` highlights that
column), `applyScores({cells, results, requests})` for a scores write, and `requestId()`. The
setup editor warns before removing a column named in the View's `scored_items` (any score
revision, cleared ones included: exactly what the server refuses to remove), and the pull and
import previews show a hand-cleared current cell as "Cleared by you" with its Replace tick. The Grading
context row and each grading overview card link to `#gradebook/<term>?assignment=<assignment>`
(`gradebookLink` in `grading-overview.js`).

## Incremental type checking

`grading-upload.js` lets an empty assessment upload multiple Gradescope CSVs in
one selection. Each file has a stable request ID and visible outcome; explicit
retry skips completed files. File/version controls stay fixed while uploading.
Closing after partial success refreshes the question list. No placeholder region
or question-definition dialog precedes upload, and a retry never approves work.

`grading-guide.js` holds pure functions for printed question order, per-question
location/rubric states, workflow routing and the shared connected graph
(`tests/test_grading_guide.mjs`). Both the assignment tile and page header render
`guideSteps` with `pipelineMarkup`: identical labels, order, destinations and states.
The tile marks the next task; the compact header marks the task being viewed, even when
complete. Each step is a column of the numbered graph (DESIGN-LANGUAGE §4): its numeral, its label and the word
for its state under it (Done, Failed, In progress, Not started, a waiting step's own status, or its progress fraction),
the columns divided by hairlines under a double rule; done steps' numerals are slate, failures red, untouched steps
faint, and the step on screen (`aria-current="step"`) sits under a 3px ink rule with an upright ink numeral (Ledger: where you are
is never the needs-you orange; on overview cards and tiles, where the marked step is the next task, it stays orange). Being
selected or having work available does not start a step. Overview cards use the same graph a step smaller; Home's
and Assignments & printing's cards use its compact form, a row of thin bars with the labels under them. Accessible
names and hover titles keep each step's full status. Saved-work percentages/counts and keyboard links remain in the
steps. Upload comes first to
establish the problem numbers for later template OCR. Six steps (requirement, 2026-09-20): rubric uploads and
Assignment Templates share the first page (`rubricView` mounts `templatesView` in its own slot, so template state survives
the page's other updates); `rubricProgress` is that step's completion. The answer key and the mandatory rubric finalization
share the third page: `finalizationProgress` is its completion, and the finalization panel's polling refreshes the page
status and the header graph without recreating anything else. The panel's next thing (Run rubric check, Review and confirm) is the step header's one primary;
while a review waits the panel also offers a secondary Open the review, and Run rubric check again sits under Check details
(2026-10-02: a check that finished below the fold left the panel with only the re-run). While an answer key is read from the blank assignment, the third
page looks at the plan alone (no activity) every 5 s and draws nothing; only a key that finished reading redraws the step, and
never while `view.busy()` (a dialog, typing, unsaved work) or a typed question search holds the page: the reading line then says
the keys finished and offers Refresh (2026-10-02). `rubricView` returns true when it scrolled the page on purpose (a link's
question, templates, versions or preparations focus, shown once per address, or the next answer area to confirm), so a refresh of
the same step can keep the scroll position otherwise. Without a registered paper (no `rubric_upload_documents`), every Prepare step is replaced by
one **Add paper in Create** panel (`#documents?assignment=<id>&term=<term>&step=version`; "in Assignments & printing" until 2026-10-18), so neither Upload rubrics,
a draft button nor **Add question manually** (its version list would be empty) is offered. Crop checks appear in Answer areas. Old `approve`/`templates`
routes open the first step and `rubric-quality` the third. `open=quality` and `open=answer-key` requests
consume the corresponding dialog request once. Landing-page refreshes preserve graph scroll and focus.
The assignment cards are the only assignment picker; detail pages link back to
**All assignments**. `grading-question-workspace.js` provides a browser-only
question list, search, attention filter, and previous/next controls for the first
three steps and the finalization dialog. It remembers the selected question per
plan/stage during navigation and refreshes. Panels remain mounted and are hidden
when unselected, preserving unsaved finalization fields and symmetric pair edits.
Uploads/version status and templates sit beside each other. Answer areas shows
the blank page with suggested or confirmed overlays next to its review action and
**Confirm N answer areas** (requirement, 2026-09-25: flip through the previews, fix any problem in the review, one click for the
rest; named **Confirm all (N)** until 2026-10-02). The step calls one thing an "answer area" everywhere.
Step three shows key/check status above the selected question's answers and rubric
lines. Its individual key editor submits only that question; the bulk editor is
available under **Edit the whole answer key**. These are views of existing owners;
the server still validates every revision and approval.
Under each question of the third page, **Rubric lines · correct a wording** lists the rubric's lines with a **Correct wording**
button (`correctLine` in `grading-rubrics.js`): one textarea, one POST to `.../rubric-lines`, then a refresh. The server decides
whether the confirmed finalization is carried; the toast only reports its answer. The keys shown but not saved are saved by the
header's **Save N answer keys** (named **Confirm all (N)** until 2026-10-02).
On the first page the line editor (`grading-rubric-edit.js`) enables **Save changes** only while its form holds a change. Where
the question's confirmed finalization stands (`rubric_quality.standing`), Save changes and **Save all** first open **Change a
finalized rubric?**, which says what saving undoes (the question stops grading until it is confirmed for review, checked and
approved again; changed lines are graded again; a current certification shows as changed) and, for a changed line, links to
**Correct wording** for a typo. Changed lines on an approved revision already out of the finalization ask **Change an approved
rubric?** instead: a new revision needs confirming, the check and approval again, without the finalization's claims or the link
(Correct wording carries a finalization only where one stands). Closing either window sends nothing (2026-10-02). Once a rubric is approved its import
notes (`rules.warnings`, `warningsMarkup` in `grading-rubric-display.js`) fold under **Import notes (N)**; before that they stay
warnings, and a note that starts with one of the rubric's item IDs names the line by its label.
`workflowProgress` adds visible percentages/fractions and accessible progress
values to unfinished nodes. Rubrics count uploads,
approvals and any optional QA/confirmation; templates count prepared/confirmed
version references; answer areas count shared template pages once, completed
checks and explicit confirmations. Answer keys use their retained
counts. Grade/Review use server `workflow.progress` for current responses and
final decisions, including new eligible work; regrades do not borrow completion
from an older result. Unknown totals show no percentage; failures and completions
color the title and connectors red or green. The existing 15-second landing
refresh updates counts without timers estimating model progress. Tests cover
partial work, retries, reload and keyboard access at desktop widths
(`browser_grading_progress.py`).
**The step graph follows the review** (2026-10-11; a kept screenshot showed the graph at "Review 0% · 0/5" beside a rail at
"4 of 5 final" until a reload: the Review step was never handed the graph, and after a decision it reads only its own queue).
`grading.js` draws the graph once per render; a step that hands back a newer plan (`onPlan` of the setup steps and
Certification, `planFollower` for Review) patches it in place with `dom-patch.js`, and an unchanged graph writes nothing, so the
graph's scroll and a focused step link stay. The Review step says when its counts changed (`onChanged`: a decision saved or
found changed, in the workspace or in All responses; a gallery approval; "Regrade N affected responses"; a retry of failed
responses; a flag review that finished). `planFollower` then reads the plan (`GET /api/grading/assignments/<aid>`, the default
body) one second after the LAST change and one read at a time, because that read holds the lifecycle lock: a run of quick
approvals is followed by one read once it pauses. Only the graph is drawn: the response on screen, its form, the focus and the
address are not touched. A read that another change overtook is not drawn (the plan is read once more); a failed read leaves the
graph as it was; nothing is read once the page is left. `node --test tests/test_grading_run.mjs` covers the follower,
`browser_grading_review_blank.py` the graph after a gallery confirmation and after each approval in the workspace,
`browser_grading_review.py` after one in All responses.
Scan review is an Inbox prerequisite, outside the six-step grading graph.
`inboxNoticeMarkup` shows pending arrivals separately on cards and assignment
pages, linking straight to the Inbox. Scan records remain accessible through a
utility link and legacy `tab=papers` without a numbered or selected graph node.
Inbox arrivals do not reopen completed grading or enter its progress denominator
until eligible. Existing responses still invalidate when their source changes.
The graph is a browser projection over existing owners. Template confirmation is
an explicit term-plan receipt; navigation never infers approval.

Review filters by question and approval state, invalidating pending detail loads
even when a filter yields no responses. Grade resets eligibility previews when
question, processing mode or operation changes, and discards superseded previews.
The export stage reads the existing response list and uses the existing export
endpoint, including its current-source and rubric validation.

`grading-groups.js` owns the answer key dialog (one selected question or all questions, `label: answer |
alternative` lines, `keyText`/`parseKey`; `splitAnswers` splits only at a `|` outside braces, brackets and parentheses) and review by answer group: a list of
groups, then one contact sheet per group with editable rubric lines and a required
confirmation. The guide links to the key with `open=answer-key`; `grading.js`
consumes that parameter once and removes it from the address, remembering it across
superseded renders, so a refresh after saving does not reopen the dialog.

`grading-settings-layout.js` renders a role's reasoning beside its model (`reasoningMarkup`): a select when the connection's
saved `reasoning_options` allow several choices, a plain statement for one, a note when nothing is known. `reasoningChoices`
and `reasoningDefault` mirror the server for display; the server validates and resolves. `grading-settings.js` captures
`role_reasoning`, forgets a role's choice when its model changes, drops a connection's facts when its model or host is edited,
and reads facts for earlier connections only on the explicit button (`/api/grading/model-catalog`), marking the form unsaved.

`createJob` in `grading-jobs.js` requests the server preview itself (`findEligible`, on
opening and on every change; the latest request wins) and explains a disabled Start in
the preview area and its `title`. `grading-batch-layout.js` renders the choices beside
a compact count/cost preview. The dialog's header
and actions stay outside its scrolling body. Regrading, model/estimate details and
source pages are disclosures; skipped quality checks are routine information.
Over-budget or unknown cloud costs disable Start; point-only rescoring calls no model
and does not use those estimates. The server remains authoritative for all limits.
Editing a sample limit invalidates the preview immediately, then refreshes after a
short debounce. Invalid limits cannot reuse an older preview. In `#grading-dialog`, as everywhere, a disabled button shows
`not-allowed`, and the busy cursor only while the form is `aria-busy` ("Busy and blocked buttons"). Browser checks
must wait for a FRESH dialog before ticking a box: a closed dialog keeps its last form,
and Playwright's `check()` returns at once on a box that is already ticked.
If starting a batch returns 409, the dialog keeps its choices, refreshes the preview
and requires another explicit Start click. It never retries the rejected write or
reuses its request ID; a failed refresh leaves Start disabled. Other failures retain
the request ID so an uncertain submission can be checked through the existing receipt.

`grading-jobs.js` offers **Reset batch…** on a finished batch whose payload says
`reset.resettable`. The dialog renders `GET .../jobs/<id>/reset` (what would be withdrawn,
kept or left alone) and posts its fingerprint with `confirmation: "reset"`; the card then
shows `reset.latest` with **Grade them again** (the ordinary batch dialog, whose first
choice is now *New, reset or changed since grading*) and **Put the proposals back**. It holds no
reset state of its own.

`grading-review-queue.js` keeps Groups & review tools (a question's proposed-line group overview, `{question}`) and the markup
the workspace draws: Failed to grade, a question's shared rubric concerns, the grade summary and the breadcrumb. Since 2026-10-03
the Review step has no overview page: it opens in the workspace (instructor: "go straight to the workspace"). The full rubric
finalization stays one link away from a concern (`focus=<question id>&open=quality`; its dialog selects the matching rubric and
linked versions without changing any values). Automatically finalized counts are separate from instructor decisions, and
completed questions say No review needed. Failed/regrade/not-yet-graded work stays visible.

`grading-review-workspace.js` is the Review step. With no question in the address it starts on `reviewStart` (the remembered
response if it still waits, else the first waiting one in printed order) and writes it into the address, so a background redraw
never remounts the desk; with nothing waiting the main area shows one next step (`emptyBody`, from the server's counts). Above
it: a one-line heading and lead, the batch line, the flag review row (`grading-flag-review.js`, assignment-wide), Failed to
grade (closed; `open=failed` opens it). The flag review reports actual replies received and requests in progress, without an
estimated-total fraction; queued, processing and usage-limit/retry waits are stated explicitly (GRADING "FLAG REVIEW PROGRESS").
A reset review says its original flags were restored and names any protected later decisions or changed responses (GRADING
"FLAG REVIEW RESET"); the past model replies and grades remain in the records. Its rail is the queue's progress (a `<progress>` element: no inline styles under the CSP) and the unfinished
question rows, a question with a shared rubric concern marked. Below it: Completed questions and Grade summary (closed, their
open state kept through redraws) and the links to All responses and certification. A finished flag review re-reads the queue
for the rail and footer only. It mounts the existing desk beside version/question navigation and the current group's anonymous
response list. A question's concerns are a disclosure above the desk with `grading-review-concerns.js`: Clarify the rule here
(drawn when opened from `GET .../rubric-quality`; edits only the named lines' clarifications and pairs and posts
`rubric-quality/clarify`, everything else sent as it stands; falls back to the finalization link when it cannot) and Regrade N
affected responses (preview, then jobs, `response_ids` of the members with `rubric` reasons). An edited line offers its "same" linked lines on the other versions (ticked), which get only the added sentence (`grading-clarify.js siblingPreview`, `passedOn`); everything is one `saveClarifications` call. The flag review's proposals open `adoptProposal` (one box per version) from "Add to the rubric…". The desk's decision callout shows `flagReadingsMarkup`: the flag review's note and one button per reading, ticking exactly its lines and approving (hidden where the desk cannot approve). "See all N in this group" opens
`grading-review-gallery.js` for a proposal group of two or more: the desk and its keys are closed first, the gallery registers
no shortcut, pages of 24 tiles with lazy crops, Set aside (reviewed alone next) and Open alone, the native "I looked at every
response shown" tick, and Approve N (`proposal-gallery/approve`, every response's version and result). The reason filter uses opt-in `review-queue?details=review` member facts; hidden proposals reveal only their
blind status. Default API bodies are unchanged. The whole queue is read on entry and at question boundaries; after a save,
only the current question is read and its entry replaced. Groups of the same question continue automatically; the next
question/version is a labelled, explicit handoff. Batch scope stays in every address and read. The `address` callback keeps
question/group/response in the current route without remounting the entire page. Loading generations suppress late responses.

Gradescope regrade requests are Return's third sub-tab, Regrade requests
(`grading-regrades.js` `mountRegradePanel`, mounted by `teaching-review.js` beside its patched page so the desk and a typed reply
survive a redraw; `#grade-delivery/<plan>?view=regrades[&request=<message id>]`; REGRADES 2026-10-07).

`grading-review-session.js` holds navigation over existing term-owned records, with no new retained owner. A bounded tab-local
sessionStorage object keeps only the last question/group/response IDs for each plan/batch (20 contexts); unavailable storage
is harmless. Filters and Later IDs are page-session state, pruned against the current queue. Later (Alt+L) invokes no write,
leaves counts untouched, and skips deferred work until explicitly selected or Review responses left for later is chosen.
The shared desk guards unsaved edits on these moves and does not move while a save is in progress.

`grading-review-layout.js` shows a decision prompt, exact rubric-line shortcuts, and why the response waits one line a cause
(`reasonsMarkup`, 2026-10-11): the shared `.review-said` list with a cause's label, its first sentence (`reviewReasonMarkup`), and
"+N more" with the authors (`reasonRole`, `modelLabel`) when the cause holds more; every reason stays verbatim with its authorship
in the closed "All recorded reasons (N)" fold, under the same labels. The causes come with the desk's read
(`responses/<rid>?details=review`, asked by `grading-review-workspace.js`, `grading-review.js`, `grading-regrades.js` and
`grading-certification-students.js`; never present for a hidden proposal) and are drawn only while `reasonCauses` finds exactly the
sentences `reviewReasons` lists, each once; a read without them draws the plain list as before (three shown, the fold when there
is more). The fold is a native disclosure: no handler is added, and `reviewReasons` and `decisionPrompt` are unchanged. Inspecting a line opens its explanation and focuses its checkbox without changing it. Original scans, evidence
coordinates, the editable transcription, scoring and all approval checks remain the shared desk's. The workspace labels its
primary Approve & next and displays the computed score beside it; Certification and All responses keep their own labels.
On desktop, the queue is sticky and the evidence and rubric scroll independently with the approval controls visible. Narrow
or short screens use normal page scrolling and a folding queue. Question/version context remains in the response heading.
`grading-math.js` renders only marked display content using pinned local KaTeX
(`static/vendor/katex/` includes its license, provenance and hashes). It builds DOM
through CSSOM under the existing CSP, keeps trust disabled, bounds expansion/size,
and falls back to literal escaped text for unsupported math. Editable text and
retained provider transcripts are never rewritten by rendering. Explicit LaTeX
spellings are rendered even when an element's type is `text`; bare LaTeX
recognition includes single-character commands such as escaped spaces. Plain prose
and invalid expressions retain the existing literal fallback.
A question's printed layout is read as well (`latex-text.js`, requirement, 2026-10-05: "this is not rendering latex properly", a
Prepare step showing `\textbf{…} \underline{\hspace{8.5cm}}\\[7mm]` as source). Text that uses a layout command outside the
mathematics (`\textbf`, `\emph`, `\underline`, `\hspace`, `\hfill`, `\quad`, `\\`, `\par`, `\$`, `\%`, tabular, minipage, center,
lists) is built as DOM: bold and italics, an underlined space as an answer line at its printed width, gaps, line breaks, a ruled grid,
side-by-side columns, display mathematics on its own centred line; vertical space, struts and `\renewcommand` are dropped; any other
command is offered to KaTeX with its arguments and stays literal if KaTeX cannot read it; `\left(`/`\right)` around prose keep only the
delimiter. Text goes in as text nodes and widths only from known units through CSSOM, so the CSP and the escaping are unchanged. A text
with none of these commands (prose, transcripts, `\text{…}` in answers written without delimiters) keeps the reading above, untouched.
`tests/test_latex_text.mjs` and `browser_grading_review.py` cover it.

`term-schedule.js` (SCHEDULING "TERM SCHEDULE") renders `#course/<group>/home/schedule`, a course Home sub-page like the
lecture notes: its own header (the trail names the course and term), three ruled regions, one primary by state, a leave guard,
request IDs kept for a retried save, and a reply to Save never replacing edits made while it was in flight.

`class-schedule.js` (SCHEDULING "CLASS SCHEDULE") renders `#course/<group>/section/<n>/schedule`, a section sub-page without the
work list: one `.workspace-table` by date (Week, Date, Class, Topics, Given and due) in fixed columns, earlier weeks in a second
table of the same columns inside one disclosure, today marked with `aria-current="date"`. It reads once per visit (no timer),
draws from what it last read when the host comes back, and patches in place (`patchChildren`, keyed weeks and days). A real
record's title links to its owner; a planned row opens its window; orange is only the "Dates differ" chip and the header's
count. One primary by state (Save topics while typed topics differ; Add a date while the plan is empty). "Edit topics" turns
the Topics column into one field per class day (Enter and the arrow keys move between days); the page is not redrawn while they
are typed, and leaving with unsaved topics asks first. A stale save (409) keeps what was typed and offers Reload the schedule.
The pure parts are `class-schedule-model.js` (type-checked; `tests/test_class_schedule.mjs`). `schedule-prefill.js` adds "From
the schedule" to the forms that create work (quiz approval, Create assignment, the card's Given on, a new gradebook column): it
renders nothing when the term has no open planned rows, and never replaces a date that was typed.

The transcript editor is associated with the review form through its HTML `form` attribute.
`grading-crops.js` uses a separate native dialog so zooming over a group-review
dialog preserves that form; Escape restores focus, and route changes close it.
`browser_grading_review.py` covers math, untrusted input, crop zoom, independent
pointer/keyboard scrolling, visible approval controls, transcript preservation and
the desktop presentation.

`grading-evidence.js` distinguishes resolved OCR history from remaining differences
and shows per-item rubric condition dispositions. `grading-rubrics.js` exposes
dependencies/exclusions and an explicit assertion that structured rules fully cover
a condition; saving creates an unapproved revision through the existing endpoint.
`grading-review-metrics.js` renders the read-only correction counts returned by
the metrics API in the existing diagnostics dialog. Confidence labels describe
model-reported values, never probabilities of correctness. `browser_grading_review_rules.py`
covers these controls, preserved grades and the report at desktop widths.

Cloud team settings explain the conditional additional reviewer, which sees both
earlier assessments. Evidence displays each retained team outcome and model selections, with
the full per-model assessments collapsed by default. Resolved ties remain visible
as history without being presented as outstanding disagreements.

`grading-templates.js` presents Assignment Templates within the first step, with one
reference slot per version. It imports generated canonical PDFs on entry (a slot's empty choice reads "Preparing assignment
PDF…" only while its preparation runs or waits its turn; a failed one shows its warning and Retry preparation), retains
upload retry IDs and per-slot file drafts, and shows the scanner-quality warning
before the explicit handoff to answer-area checks. Navigation and closing the
warning never acknowledge it. Generated references can be replaced with blank scans;
the upload form is folded under **Replace this template** once one is selected.
**Draft rubrics from this template** opens the generation menu with positive or
negative scoring. Positive starts at zero and adds credit; negative starts at full
credit and deducts for errors or omissions. The choice stays in the dialog through
reopening, and **Generate rubrics** submits it with the request ID. Mode is part
of the retry identity and the server's immutable generation inputs. The review
dialog shows the saved mode and accepts signed points accordingly, including new
lines; older generations default to positive. Acceptance uses that saved mode,
and never converts a rubric by negating its existing lines.
The grading list excludes assignments still waiting for their first cleared scan;
direct links send the instructor back to the relevant Inbox item.

`grading-locations.js` polls only the template-verification cards on the Answer
areas page, leaving an open review dialog intact, and writes a card (and the header's button and summary) only when its markup
changed, so a poll keeps the preview images and focus; the card's buttons get their handlers as properties on every draw.
Its page overlays use DOM style
properties to respect the existing CSP. Dragging or editing percentages changes only
the draft regions; with no suggested area every template page opens, "Area to draw" offers **New area**, and the first rectangle
dragged becomes Area 1. The review window (**Confirm the answer area**) keeps its required tick ("I checked every part and
continuation page") and any refusal in the footer beside **Confirm answer area**, as the finalization window does; failed checks
also need an explicit manual-review acknowledgment and explanation. **Confirm N answer areas**,
drawn once in the Answer areas step header, lists each question not yet confirmed whose printed
points match and whose preview can be drawn (no privacy masks) with the version and
check it drew, and POSTs `.../locations/confirm-all`; the server confirms each exactly
as its check suggested (template, printed number, areas) in ONE transaction and refuses
the whole request (409) if any is stale or needs manual review. Questions left out are
named under the button: they are confirmed one at a time in the review dialog. After a
confirmation the page opens the next question (printed order) whose area still needs
confirming: browser navigation only. The
server revalidates the exact check and question version before saving. Dialog
closure/navigation suppresses late updates. Template changes return to Assignment
Templates and require a renewed warning confirmation. The shared template manager
remains available on Exams → Manual scores for general reference management (the
assignment wizard's Paper step no longer opens it, 2026-09-26).

`tsconfig.frontend.json` checks the modules in its `files` array (that list is authoritative;
today it covers the shared request/UI modules, the read-only delivery API adapter, the page
header and its title fitting, the command palette, the print-file renderer, the calendar,
mail and communications helpers, `dom-patch.js`, `class-schedule-model.js` and the seating
state helpers) with strict TypeScript checking of JSDoc; `icons.d.ts` types `icons.js` for them. `contracts.d.ts`
defines response shapes and distinct opaque identities for definitions, terms,
sections, saved offerings, navigation groups, charts, enrollments, Inbox jobs, and
UINs. These are declarations only; they do not replace server validation. Add
modules to the config as they acquire useful contracts rather than suppressing
errors across the whole application.

With an installed TypeScript compiler, run:

```sh
tsc --project tsconfig.frontend.json
```

The repository's existing Cloudflare development dependencies also supply it:

```sh
node cloudflare/teach-inbox/node_modules/typescript/bin/tsc --project tsconfig.frontend.json
```

Shared helper checks run without installing npm packages:

```sh
node --test tests/test_request.mjs tests/test_ui.mjs
```

The `browser_seating.py`, `browser_seating_creation.py`, `browser_assignment_wizard.py`,
`browser_seating_student_mail.py`, `browser_seating_gmail.py`, and
`browser_seating_mail_merge.py` exercise the real extracted workflows with isolated
fixtures and fake delivery providers. Keep temporary catalogs, browser profiles,
and screenshots on the configured NVMe scratch drive when running them. Create New
Assignment lists its Letter and A4 demonstration fixtures only on a page marked for
testing (`document.documentElement.dataset.testing === "1"`); a check that registers
one sets the mark with an init script (`browser_assignment_wizard.py`,
`browser_documents.py`), never the app.

Rubric setup uses `grading-upload.js` for a separate multi-file slot per known exam
version. Rubric finalization is a separate mandatory action in the third step.
Stable request IDs survive retries within the upload dialog;
individual successes remain saved. `grading-rubric-quality.js` polls only while
needed and while its host is live, shows an explicit review action when suggestions
are ready, and edits symmetric pairs in the selected question's item disclosures.
Polling never opens the dialog automatically. The wider dialog keeps its
confirmation footer visible. Model wording and LaTeX use
escaped math rendering. `rubric_reviewer` uses the existing recent-model dropdown
and adjacent Browse picker. No rubric-quality suggestions enter scoring-rule fields.
