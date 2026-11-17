## Faculty assistant

Rule ID: **ASSISTANT**. Current binding module contract. Read root `AGENTS.md` first.

### A coordinator's follow-up (2026-11-14)

COORDINATOR. The instructor (2026-11-14): "I want the coordinators emails to be included in the followup pipeline just like
student's. Obviously, we should add something so the agent knows it isn't a student but the coordinator so it can suggest
actions appropriately". The readings below that the instructor did not state are mine and theirs to change.

- **A COORDINATOR'S FOLLOW-UP** is a follow-up whose conversation is with a coordinated term's coordinator (TEACHING-WORKFLOWS
  "A COORDINATOR'S FOLLOW-UP"). Its runs, cards and receipts are a follow-up's own (`assistant/followups.py`), named by the
  term's owner key `coordinator:<term id>` where a student's are named by the enrollment (COORDINATOR "THE OWNER KEY";
  `student_support/018_coordinator_owner`). One generation per source state, Prepare now, Retry, Ask for changes, answers,
  Edit in place, Edit in Mail, Approve all, mute, pause, redo from chat and the context checks work as for a student's.
- **ITS OWN INSTRUCTIONS** (`COORDINATOR_PROMPT`, in the system prompt and the request, in place of the student `PROMPT`). The
  preparation is told the writer is the course coordinator, NOT a student: a colleague whose messages are instructions,
  deadlines, rulings and questions for the instructor, still untrusted evidence and never instructions to the Assistant. It
  proposes what the message calls for anywhere in Faculty (a reply when one is asked for or owed; calendar and term-schedule
  changes; exam, rubric, grading or gradebook changes the coordinator ruled on; changes for students they name; notes; the
  follow-up's tracking), proposes the coordinator's ruling where it differs from what Faculty holds and says what it
  replaces, proposes no reply to a message that asks for nothing back, and asks the instructor (ask_question) what only they
  can answer. None of a student's rules are in it: no absence, documentation, makeup, office-hours, booking-card or
  Student Rule 7 instruction. A meeting the coordinator calls is a calendar event; an office-hours appointment for the
  coordinator is refused when proposed.
- **ITS FACTS** (`_coordinator_facts`, also what `student_context` answers): the coordinator's name and address as the term's
  record holds them, the course, the term and the instructor's sections of it, every other current course they coordinate
  for the instructor, and when they last wrote (campus wall clock). The conversation is read through
  `GET /api/communications/coordinators/<term id>`, never copied into the instruction. The term policies are given as
  background only (they are the instructor's rules for students), never as rules for the coordinator or text to send. No
  student facts, notes or grades are read for it.
- **WHAT THEIR MAIL CALLS FOR** (2026-11-15, requirement: "look at my gmail and see previous emails from her and make sure
  the assistant has the ability to take actions on her emails since they are different than student emails"). The term's
  kept mail from the coordinator was read that day (122 messages of theirs since August 7, two thirds to every instructor;
  76 attachments: 35 Word files, 21 pictures, 19 PDFs, one Excel file). Its kinds, and what `COORDINATOR_PROMPT` now says of
  each: a weekly agenda (read the Word file; a calendar event only when missing or different; class days and pacing set
  against the term and class schedules, only what Faculty does not hold); a grading ruling (the rubric edit or bubble-sheet
  key change only where Faculty differs from it, their Gradescope fix not being Faculty's; never a regrade or recalculation
  of a certified assessment unless the instructor asked); a deadline or reminder (To do with what and by when); a number or
  list asked of every instructor (worked out from Faculty, given in the reply or in `next`); a link behind the instructor's
  sign-in (SharePoint, Teams, OneDrive, Forms: never guessed, the instructor's own step); files for the course (looked at,
  never filed: uploads stay the instructor's, policy NEVER); an answer about a particular student (proposed on that student's
  own follow-up and absences as cards, the reply to the student left to the student's follow-up); and what needs nothing
  (Resolved, no reply). Every write route the policy leaves open is a card here as anywhere, so these need no new tier.
- **WORD AND EXCEL FILES** (same request; FILES). `fetch_files` with `look` reads a .docx or .xlsx as its words
  (`document_identity/office_text.py`, sandbox operation `office_text`): a .docx's paragraphs and table rows in order, an
  .xlsx's sheets as saved (a formula's saved value), tab-separated. Read as a zip of XML in the document sandbox with the
  standard library alone, never in the web process; nothing in it is run, followed or rendered; a part over 24 MB or one
  declaring its own XML entities is refused; old .doc/.xls and other zips are not read. The file is still listed and shown
  as the file it is. For every conversation, not only a coordinator's.
- **NOTHING BY ITSELF.** DONE FOR YOU is a student's follow-up's record-keeping and is not widened: in a coordinator's
  follow-up every write is an Approve card, the follow-up's own status included (no scheduled status), and no closing rule
  runs (THANKS CLOSES, DEADLINE CLOSES, EVIDENCE CLOSES, DECISION CLOSES). ONE ROUND's order stands: Waiting is proposed
  beside the reply and submitted once that reply has a confirmed receipt.
- **WHOM IT WRITES TO** (amends WIDE SCOPE's deliveries for this follow-up only): the coordinator at the address on the
  course card (either TAMU spelling), the TAs of the sections the term lists now, and the instructor's connected address.
  Any student's address is refused, as is everything BROADCASTS names. Mail to the coordinator takes any Faculty file the
  Mail page accepts, like mail to TAs and the instructor alone. Every message is an explicit Send. A course no longer
  coordinated has no address: its follow-up is not prepared and a reply is refused.
- **THE CARDS SAY SO.** A step of its own tracking reads "waiting on the coordinator" and "The coordinator is not told"; a
  step that changes other records says "It is a change the coordinator's message calls for: check whom it affects."
- **THE CHAT** takes the same key: `open_followups` lists a coordinator's follow-up with their name, and `student_context`,
  `draft_student_reply`, `track_followup` and `redo_followup` work with its `enrollment_id` (guide.md). `student_context`
  then answers `coordinator` in place of `student`, with no notes or grades.
- **CHECKS THAT DO NOT APPLY.** The student conversation analysis (JEV FOLLOW-UP), SAME MATTER, the Jev record check and the
  reply draft check (which reads a draft against a student's analysis and the student rules) never read a coordinator's
  conversation. The evidence check and tool-use assistance are every preparation's and run here too, with a task that
  names the coordinator. A correction of a coordinator's card is not learned from (LEARNED FROM AN EDIT reads students'
  follow-ups), and Watch until done is not offered.
- **CHECKS.** `tests/test_coordinator_followups.py` (a scripted engine): the instructions and facts given, every step a card
  with DONE FOR YOU on, the recipient rule, a meeting as a calendar event, Waiting only beside a reply, an approved status
  saved under the term's key, the context read, a course no longer coordinated, a student's preparation unchanged, the
  instructions for what a coordinator usually sends, and an answer about a student proposed and approved on that student's own
  follow-up while mail to the student is refused. `tests/test_assistant_files.py` and `tests/test_coordinator_mail.py`: a Word
  and an Excel file read as their words through the sandbox, also as an attachment kept with a coordinator's message. NO
  MODEL HAS READ `COORDINATOR_PROMPT`: what a real preparation proposes for a real coordinator's message is seen on the first
  live ones.

### A dictated request (2026-11-14)

RECORDINGS "Memos (step 6)". The instructor's plan for recordings (2026-11-12) ends with "a memo read as a request to the
Assistant", and they chose that day that Faculty reads each filed recording on its own. The plan held no rule for it: the
readings below are mine and the instructor's to change.

- **A DICTATED REQUEST** is one thing the instructor asked for in a memo they dictated alone and filed as a memo, as
  Faculty's own reading found it: one sentence resting on the exact words (RECORDINGS "THE QUOTE CHECK"). It is the one
  place words from a transcript are a request and not evidence, and it is so only while Faculty is sure the words are the
  instructor's (RECORDINGS "HOW FACULTY KNOWS THE WORDS ARE THE INSTRUCTOR'S": their own filing, one voice, not named as
  somebody else). Not sure, nothing reaches the Assistant but by the instructor's own press on the memo's page; and a run
  Faculty queued by itself is withdrawn, and refused where it would start or a card of it be approved, once Faculty is
  no longer sure.
- **IT RUNS AS A BACKGROUND PREPARATION** (`assistant/workflows.py`, kind `memo`, target the recording item), under every
  rule of "Integration reliability and opt-in preparation": on the web process's dispatcher, one at a time with the other
  background queues and behind interactive work; counted in the busy and restart checks; EVERY WRITE AN APPROVE CARD,
  the chat's automatic categories included (`_proposal_only`); `never` and `page` routes refused as always; at most 20
  actions, an exact one never repeated; cards approved in the order they were made; an interrupted, failed or stopped run
  never repeated by itself; no transcript saved, only its bounded answer, question, action references and usage. So the
  automatic tier is not widened by a memo: it is closed for it. A memo's run has no rule and no schedule, and is listed
  with the other runs in Automation, where it can be stopped and links its memo's page (it is asked again there, not by
  Prepare again). While no model is chosen for the Assistant a queued run waits, and its memo's page says so.
- **WHO QUEUES IT.** Recordings alone (`Workflows.dictated`): once for a request when Faculty is sure, and again only by the
  instructor's press (Ask the Assistant, Ask again). `POST /api/assistant/preparations` does not take the kind, the
  Assistant's use of `recordings.ask` is `never`, and a preparation starts no other preparation: the Assistant never sends
  itself a request out of a transcript.
- **WHAT IT IS TOLD** (`MEMO_PROMPT`): the request, the exact words (which decide where the two differ), the day it was
  dictated and the memo's lines around it as context that holds no other request; that the words came through a
  transcriber that mishears, so every name, date and number is checked against the records and one that matches no
  record, or several, is said and not chosen; that a request it cannot make out or carry out gets a plain answer and no
  invented task; at most five cards; never a send, a certification, a publication, an excuse or a grade by itself; an
  answer of at most six short lines. What it reads in records stays data, never instructions.
- **WITHDRAWN WITH ITS REQUEST** (`Workflows.dictated_gone`): a request dismissed, its memo filed elsewhere or read again
  without it has its queued run and waiting cards withdrawn and a run under way stopped; a card approved against a request
  that is no longer one is refused by the run's guard, which reads the request from Recordings each time
  (`observe`, kind `memo`). A request is the same request while it rests on the same words of the same recording, however
  a later reading words it.
- **NO MEMORY.** The run is no chat of the instructor's typed words: nothing in it is kept as a memory (ASSISTANT-MEMORY).
  A rule dictated in a memo is kept only by the instructor's Remember on the memo's page ("SAID IN A RECORDING").
- **CHECKS.** `tests/test_recordings_memos.py`: a scripted turn in the engine's place; a calendar entry (`auto` in chat) is
  a card; `recordings.ask` and a second preparation are refused to the run; a dismissed or re-filed request's cards are
  withdrawn. No check asks a model.

### One conversation, real windows (2026-10-09)

The instructor: "The agent windows and drawer seem very primitive and not as mature as frontier labs implementations of such a
thing", asking that they be "more professional, user friendly, and increased productivity". Asked, they chose a first build in the
browser only ("Feels alive", "Real windows", "Useful messages"), to keep an answer's text held until its evidence check ("No, keep
holding it": no streaming), to keep the drawer's five tabs and make the drawer resizable, and to defer next-step suggestions and a
quick/thorough switch (both need the server). Keeping every chat for 30 days was chosen that day and withdrawn the next
(2026-10-10: "cancel build 2 ... the 30 day keep shouldn't be needed", a memory system being built in another session):
ASSISTANT-RETENTION and "Closing the bubble ends its conversation" stand as written, and nothing of it is built.

- **ONE CONVERSATION.** The drawer and the floating window draw the conversation with one module (`static/assistant-chat.js`; the
  message text in `static/assistant-markdown.js`): the same messages, steps, cards, question form, empty state and composer (the
  window draws its cards beside the conversation: CHANGES BESIDE THE CHAT). On
  both, a failure is the "The assistant stopped" callout, a failed step stays in view and the instructor's own lines keep their
  breaks. Each surface still owns its conversation, its cards' handlers and its window. OWNER: the workspace; nothing is added to
  the catalog (what a browser remembers below is that viewer's convenience, in `localStorage`).
- **LIVE STEPS** (amends QUIET STEPS). While a turn runs, the steps it has finished are listed (the latest five, "N earlier steps"
  above them) over the line it is on now (a check's own words, else "Working…"), and the composer's state line counts the time
  ("Working · 0:14") on this browser's clock. Once answered, the fold reads "How it got this · N steps · S s", S from the turn's
  own event stamps. No time is given per step: the gaps between stamps are mostly the model thinking.
- **QUEUE ONE.** Send stays on while the assistant answers: one message typed meanwhile waits above the box ("Sends when this
  answer finishes", with Edit and Remove) and is sent once, when the answer is in and no question is open, with the page and
  hints of the moment it was written. Stop, a failed turn, a question or a conversation that is gone gives it back to the box,
  unsent. In the window a waiting selection goes first. Only one waits. Page buttons (`ask`) never queue: they still refuse while
  an answer runs or anything is unsent (typed words, attached files or a waiting message).
- **SENT ONCE.** The drawer follows the floating chat's rule: a lost or ambiguous message response (network, 5xx, 409) is settled
  by reading the conversation once and is never resent; what did not go returns to the box, with "It may already have been sent"
  when that is not known. Only a conversation Faculty has forgotten (410: nothing ran) continues in a new one, as before.
- **ANSWER READY.** A running answer is still read with the drawer closed (every 2.5 s). When it comes in then, the launcher shows
  a small slate mark and says "a new answer is ready" until the drawer opens: never orange (nothing waits on the instructor) and
  no toast. The launcher's label and badges are written in one place (`drawLauncher`).
- **MESSAGE TEXT.** Still escaped first, Faculty or https links only, no inline styles. Added: nested lists, block quotes, rules,
  emphasis (upright and heavier: no italics), number columns right-aligned in tabular figures, and math: `$...$`, `\(...\)`,
  `$$...$$` and `\[...\]` keep their source as text and are drawn by the vendored KaTeX (`static/grading-math.js`, fetched when
  an answer first holds math). A lone dollar sign follows pandoc's rule, so "$5 to $10" stays text. A finished answer carries
  `data-patch-static`, so a poll's patch leaves it and its drawn math alone (FRONTEND "Quiet refresh contract").
- **ANSWER ACTIONS.** Under an answered turn: Copy (the answer's own words, and its formatted form where the browser allows), Copy
  table (tab-separated, for a spreadsheet) when it holds a table, and, on the last turn only, Ask again, which sends the
  instructor's own words as a new message. Ask again is offered only where it cannot repeat a change or need what is gone: the
  turn began with a typed message and holds no card, receipt, selection, review-only flag or file. The instructor's own message
  shows when it was sent (the reader's clock, as the header's); the latest one offers Edit, which puts its words back in an empty
  box (sending adds a turn; nothing already sent is rewritten).
- **CHIPS.** Above the box, "With your message:" names what the next message carries besides its words: the page (always, not
  removable: the server is told where the instructor is), the selected record when it holds more than the page's address says
  (removable until another record is selected; removing it sends the address's own hints), and selected text (removable). Chips
  show names, never IDs or values.
- **STARTERS AND PICKERS.** An empty chat offers three questions for the page on screen. Typed first in the box, "/" lists those,
  a few that fit anywhere and this tab's own recent messages (kept in memory only); "@" before a name lists current students and
  their sections (`GET /api/student-notes/students`, as the palette reads it). A starter or a "/" choice only writes into the box;
  an "@" choice writes the name and adds that record's IDs to the message's hints (one student, one section a message) while the
  name is still in it. Nothing is sent before Send, and opening a chat still starts no turn. Escape closes an open list first,
  not the chat. Assignments are not in the "@" list yet.
- **THE BOX.** It grows with what is typed (to 40% of the window in the drawer, 12 rem in the floating window), then scrolls.
  Files pasted (when the clipboard holds no text) or dropped on the drawer or the window are attached as the paperclip attaches
  them (ATTACH IN CHAT).
- **REAL WINDOW** (amends MOVABLE). The floating window's right and bottom edges and their corner drag its size (360 by 320 at
  least, always whole in the browser window). Its size, and the floating chat's place, are remembered in this browser: the next
  Command-Command chat opens there; a selection chat still opens beside its selection, at the remembered size. MINIMISE puts the
  chat away as its title bar at the lower right ("Working…", "Answer ready", "Has a question for you"): the conversation, the
  unsent message and a waiting message stay and nothing ends; the bar, its button, Command-Command or a selection sent to it
  brings it back. The title bar names the chat by its first message.
- **CHANGES BESIDE THE CHAT** (2026-10-10, amends REAL WINDOW and where the window draws a card). The instructor: "When the
  assistant proposes an action or I tell it to do something, can we make it so that it enlarges the chat bubble/window to show
  the change in a separate panel next to the chat instead of clogging up the chat messages with the proposals/cards?" In the
  floating window (the Command-Command chat and a selection chat alike) a conversation's cards, the ones waiting for Approve and
  the ones already done, are drawn in a "Changes" panel at the window's right, never among its messages. Where a card was made
  the conversation keeps ONE line: the card's own state chip and its name (a one-route group: its title and count, once); the
  name is a button that shows the panel and brings that card into view. A line never approves anything. The panel lists what
  waits first, in the order it was made, then the finished cards, the latest first; its head counts the changes that wait.
  The window widens by the panel's width (440 px; less only where the browser window could not also hold a 360 px
  conversation), so the conversation keeps its width, and stays whole in the browser window; without the panel it is the
  conversation's width again. A card that was not there before opens the panel, also after Hide; Hide puts it away until then,
  and "Show changes" in the window's footer (with the number that wait) or a card's line brings it back. No panel while the
  conversation has no card. Dragging the window's right edge sizes the conversation (still 360 px at least), and that width is
  what the browser remembers; the panel keeps its own. Approve, Decline, Leave out, Approve all, the JEV send checks and every
  server check are the cards' own, unchanged (ONE CARD FOR ONE CHANGE, MAIL-SEND-CHECKS): only where a card is drawn moved. The
  drawer is unchanged: its chat still shows cards among the messages and its Changes tab lists every card. OWNER: the workspace;
  nothing is stored (whether the panel is put away lasts as long as the window). Not built: a width of its own for the panel, and
  a panel in the drawer.
- **BOTH WAYS.** "Open in Assistant" moves the window's chat to the drawer; the drawer's "Open this chat in a window" moves it
  back: the same conversation (moved, never copied or ended), with the unsent words and files. It is refused while a chat window
  is already open (ONE CHAT).
- **DRAWER WIDTH.** The drawer's left edge drags its width (the arrow keys when it is focused; Home and End for its limits):
  never under 480 px, so the five tabs stay in view, and always leaving 420 px of the page. The width is remembered in this
  browser. The head's second line names the open chat and holds the model's chip.
- **OPENING.** A click opens the drawer or the window with FRONTEND's 160 ms entrance; the keyboard or a Command gesture shows it
  at once; both close at once.
- **NOT BUILT.** Streaming answer text (the instructor's decision), keeping chats beyond ASSISTANT-RETENTION (withdrawn,
  above), next-step suggestions, a quick/thorough switch, Approve cards in words, changing a sent message in place.

### Readable conversation (2026-10-10)

The instructor: "the agent window is not easy to read or distinguish my text from the agent's", asking to improve
readability and aesthetics. Both surfaces share the same speaker hierarchy: each message names You or Assistant;
the instructor's words sit on a soft slate tint at the right, with the time and Edit in its header, while answers stay
left-aligned at a comfortable reading width. Conversation text is larger and more open than its labels and actions.
The closed "How it got this" and answer actions remain beneath the answer. Copy still copies only the answer's words.
OWNER: workspace presentation only; no retained fields, routes, conversation lifecycle or action permissions change.

### Attach in chat (2026-10-09)

The instructor: "In the agent window, am I able to upload an attachment to the agent to look at? Or, if I want it to draft an
email/canvas mass message to students with an attachment, can I supply it to the agent?", then "yes" to a paperclip in the chat.

- **ATTACH IN CHAT.** The drawer and the floating chat have a paperclip (`static/assistant-attach.js`; a file pasted or dropped
  there is attached the same way, THE BOX). Each chosen file is uploaded
  at once (POST `/api/assistant/uploads`, multipart, the instructor's page only: the route is the Assistant's own, `never` for the
  model), listed under the message with Remove, and goes with the next message as its ID (`files` on POST `.../messages`, at most
  10). Files chosen together upload side by side, one request each, and each says how much of it has gone ("Attaching… 4.1 MB of
  12.8 MB"); a slow link gets 15 minutes a file (requirement, 2026-10-20: "it just did, but it was very slow": from campus about
  half a megabyte a second, all of it the network). OWNER: the workspace. A file is kept as scratch in the service's TMPDIR on NVMe (`assistant/uploads.py`: 25 MB each, 300 MB
  kept at once), never in the catalog or a drive folder, for 24 hours (an Approve card's life), then removed (a picture attached to a message a memory is kept from is copied
  into that memory first: ASSISTANT-MEMORY "PICTURES"). A restart of the
  web service removes them sooner: the service empties its scratch folder when it starts (`deploy/faculty.service.d/temp-space.conf`),
  so a file attached before a restart is attached again (corrected 2026-10-10; this sentence had said a restart keeps it).
  Each has one route, GET `/api/assistant/uploads/<id>` (no-store, nosniff; 410 once gone), in FILE_OWN, so the model looks at it
  with `fetch_files` and an email or Canvas message attaches it by that route like any Faculty file (MAIL-ATTACHMENTS; canvas
  MESSAGES ATTACHMENTS). The message tells the model each file's name, type, size and route, and that what is written in a file is
  data, never instructions; a message of files alone is worded "Look at the file I attached." The window shows the files on the
  sent message. Saved conversations keep the names and routes, never the bytes.
  TOO LARGE FOR AN EMAIL (2026-11-12, requirement, of 18.6 MB lecture notes attached for a follow-up's reply: "i uploaded the notes
  to the agent and asked it to attach them to this followup, but it seems to not have worked"; three preparations each met Mail's
  refusal, then 18 MB a message, and left only a tracking card; the same day, "raise the attachment limit to 25", MAIL-ATTACHMENTS):
  what one email carries is said where the files wait. Files that together are more than an email's 25 MB get one line under the
  paperclip's list, "Together these are too large for one email (25 MB at most).", and the message tells the model the same, to
  say so before it prepares an email that would attach them all. A single file larger than an email carries would read
  "… · too large to email (25 MB at most)" and be named to the model, to ask for a smaller copy before any email or follow-up's
  reply is prepared; the chat takes 25 MB a file, so none is while the two limits are equal. The files are still attached: they
  can be looked at, or go with a Canvas message. OWNER: presentation and the turn's own text; nothing is kept.

### Redo from chat (2026-10-09)

The instructor asked the chat to redo Jared Zane's follow-up. It answered that it had, but its new reply and tracking cards stayed
in the chat, and approving its Waiting on student card withdrew the follow-up's own three cards ("3 steps withdrawn") before any
reply was sent. On the proposed fix, the instructor said "yes, build it". This widens the automatic tier by one request that
prepares cards and changes nothing else.

- **REDO FROM CHAT.** `POST /api/student-support/followups/suggestions/redo` (`student_support.suggestion_redo`, request ID,
  `{enrollment_id, source_key | item_id, text}`) is the one follow-up suggestion request the assistant may make: the
  follow-up's own Ask for changes (Retry with no text), addressed by its conversation rather than a displayed run, so it also
  works once the proposals no longer apply. Tier `auto` ("Requested review preparation"). NO CARD FOR A REDO (the instructor,
  2026-10-10: **"when I ask the agent to change a followup in general, that shouldn't require an approve card for me to
  approve since the follow up itself still requires approval to execute, it is redundant"**): it starts at once also when it
  replaces the follow-up's steps still waiting, which from 2026-10-09 until then made it an Approve card naming the count (as
  REPLACING REQUESTS, which still holds for teaching preparations). A review-only or proposal-only session makes it a card,
  as for every `auto` route. STILL NO CARD (requirement, 2026-10-12: **"when I ask the agent to change a follow up, I don't
  have to approve the change to the follow up. Since the follow up itself has to be approved already, it is redundant to ask
  me to approve the change I just requested"**): all seven chat redos on the live record since 2026-10-10 had still become
  Approve cards, each called uncertain by the tool-use check ("The intent or supporting context of this change needs your
  review."). The rule is marked `result_waits`: an uncertain, spent or unavailable tool-use check never makes it a card, and
  neither does a corrective pass. The check is still made and still holds a redo it finds clearly unasked for or unread
  (.1 or less): nothing is recorded or replaced and the model is told. Replaced cards are declined as replaced, never
  rejected; completed receipts stay; a step still finishing is refused. A REDO WHILE PREPARING (requirement, 2026-10-21, of
  Carolyn Torres's follow-up, whose second change was refused while the first was still being prepared: **"why did this
  fail"**): the running preparation is stopped and superseded, its cards withdrawn as replaced at the instructor's request,
  and the new one runs next, given every change asked for the follow-up so far. A follow-up or teaching
  preparation never uses it. The chat's `redo_followup` tool makes it, and `student_context` names the follow-up's
  `suggested_actions` (state, steps waiting). The chat changes a follow-up that has suggested actions this way, never with
  reply, tracking or absence cards of its own (guide.md). The page's own controls stay instructor-only (NEVER).
- **SEVERAL AT ONCE** (2026-10-12, requirement: **"is the assistant able to modify multiple followups at once, for instance
  if I say something like 'do X with all followups requesting Y'"**; told a batch could first show its matches on one card to
  confirm, or start at once and name them: **"no card, build it"**). The chat's `redo_followups` tool makes REDO FROM CHAT's
  request once for each follow-up it names (`followups`: `{enrollment_id, source_key | item_id}`, at most 40 a call, one named
  twice asked once) with one `changes` text for all. It is no new route and no wider request: each has its own request ID,
  receipt, tool-use check (while the task's checks last; a redo it cannot check starts, as one redo does) and refusal, so a
  follow-up with a step still finishing, or resolved, is refused by itself and the others start (one being prepared is
  prepared again, A REDO WHILE PREPARING). Which
  follow-ups match is the Assistant's reading and waits on no card: it names them in its answer, and a wrong match costs that
  follow-up's unapproved steps, which Retry prepares again. To find them it reads `open_followups` (a read), the Follow-up
  page's own `suggestions=1` list cut to what tells follow-ups apart: the student, the IDs, title, state, next step, the
  triage reading and request kinds, the last preparation's explanation (700 characters) and its steps still waiting;
  resolved follow-ups are left out and `find` keeps those that mention its words. (That list whole was 1.3 MB on the live
  term, and the plain list says nothing of what a student asks.) The receipts of one call are one group
  (`AssistantService.batch(made=True)`: of one route, whatever their state), so the chat and Changes draw them as one card
  (ONE CARD FOR ONE CHANGE): "Prepare N follow-ups again: “<changes>”", how many were done and failed, and each follow-up
  on a line, "Prepare “<title>” again with your changes: <roster name>". A review-only or proposal-only chat gets that group
  as one card with Approve all. A stop leaves the rest unstarted. A follow-up or teaching preparation never uses it, and the
  model's own `group_actions` still groups only waiting cards. Preparations run one at a time, the instructor's requests
  first, so a batch holds back the automatic queue until it is prepared.
- **CONNECT A MESSAGE** (2026-10-10, requirement, in the chat on a follow-up: "can you connect the response card I sent to
  Brenna to her followup?"; the chat found the card's email in her record and answered that no action could attach it. Then:
  **"joins shouldn't be instructor only"**; asked how the chat's join should happen, they chose **"Do it at once"**, and for
  Keep separate **"Yes, same way as the join"**). `student_support.join` and `student_support.separate` (TEACHING-WORKFLOWS
  SAME MATTER) leave NEVER and are tier `auto`, category "Follow-up conversations", each with Faculty's request ID: this widens
  the automatic tier by these two requests, at the instructor's word. Nothing is sent, the follow-up's status and next step
  stay, each undoes the other, and the join stays the instructor's decision in the record (`decided_by` instructor, actor
  assistant). The chat's `join_followup` tool names the follow-up's conversation and the message (its `message_id` in
  `student_context` `communications`, or another conversation's key), reads `student_support.join_preview` (tier `read`,
  writes nothing) and then joins, with the summary "Join “<email's subject>” to the follow-up “<title>”"; a refusal (another
  student's message, no captured copy, already one follow-up) is told to the model and nothing is recorded.
  `separate_followup` names the joined conversation by its own key, which `student_context` lists under `joined` (only when
  something is joined; the read is otherwise as it was). The guide has the Assistant say when the join changes what the
  follow-up waits on. A review-only or proposal-only session makes either a card, as for every `auto` route. A follow-up
  preparation never joins or separates (either would change the conversation its other steps were prepared from: refused in
  `validate_write`). `student_support.written` stays NEVER. So that "at once" is also what the instructor sees, a chat whose
  turn brings an action tells the open page (`faculty:assistant-pages-changed`), and the Follow-up page reads again (FRONTEND.md).
- **CHAT CARDS ON THE FOLLOW-UP.** The `suggestions=1` read adds `chat` to a follow-up: the chat's Approve cards still waiting
  (not a preparation's, not past their day) that are a reply in its Gmail thread (EMAIL REPLY JOINS / SAME MATTER applied), its
  tracking change or a redo of it, each with its plain reading. The page shows them under their own lead with Approve and
  Decline, the same action IDs and handlers as Changes (a mail card has no Edit there). A preparation the instructor asks for
  (Prepare now, Retry, Ask for changes, an answer, a redo) expires the chat's waiting cards for that conversation as replaced,
  so one set of steps remains. Counts (Needs me, Home) are unchanged.
- **WAITING AFTER THE REPLY.** A chat card marking a follow-up Waiting on student, while the student wrote last, is submitted
  only once a reply to that student sent after their newest message has Mail's accepted receipt (ONE ROUND, as a follow-up
  card's); otherwise Approve is refused (409) and the card keeps waiting. ALREADY WAITING (2026-11-13, RECORDINGS "Student
  meetings"): a card that leaves a follow-up already saved as Waiting on student as it is, with the same student messages
  seen, marks nothing and is not held (the card that adds what was promised in a recorded meeting to such a follow-up); one
  that would also take a newer student message as seen is held as before.

### Automation visibility (2026-10-09)

The instructor asked to fix the autonomy review and implement its suggestions ("Fix all of the issues and implement your
suggestions"), which approves the Home card the review left for their decision. Nothing here widens what runs by itself.

- **WAITS AND HOLDS.** A rule's normal wait (`waiting`) is a neutral note with no Diagnose. A held rule (`held`) hides
  Next and offers Prepare again, the instructor's explicit request through the existing preparation route with its own
  request ID. Last result shows `last_outcome` and `last_checked_at` when the rule row has them; a ready run with nothing
  left to approve reads "Reviewed"; a built-in row's fixed description reads "How it works". Only real problems are blockers.
- **PREPARE NOW.** After a create, the form says "Preparing in the background · its changes will appear here" (or "Enabled.
  First check: …") and starts over. Automation lists active runs (queued, running, needing guidance, or ready with changes
  waiting) with Stop, reading only the runs every five seconds while one is queued or running. Saved groups runs by kind
  and target: the newest keeps Prepare again; older drafts fold into "N earlier drafts" with state and date only.
- **DONE BY FACULTY.** A folded "Done by Faculty · last 7 days" log lists automatic late-paper certifications, automatic
  Gradescope sends, makeup versions Faculty added, morning Canvas Sync attempts and the automatic tier's own receipts (counted
  per day), each with its time and owning page. It reads existing receipts and events only, bounded, with plan titles and
  counts, never student names. The response carries `done` only when there is a line. Since 2026-10-12 it also lists each
  finished run of GRADESCOPE "AFTER CERTIFYING" that did something or holds a section ("Sent to Gradescope · Published 2
  sections on Gradescope", "1 section held back on Gradescope"); a creation Faculty made after a certification is never listed
  as a makeup version.
- **REAL OUTCOMES.** "Late papers certify themselves" shows the newest automatic certification, what waits to certify or
  to go to Gradescope, and late papers not sent with their recorded reason. "Makeup versions on Gradescope" is on only
  while Gradescope is set up (a local file check), links to grading and names a version Faculty could not add by itself
  until a later creation adds it. "Gradescope after certifying" (2026-10-12; GRADESCOPE "AFTER CERTIFYING") is on while its
  Compute settings switch is on and Gradescope is set up, shows its newest run that did something and how many are under way,
  and names the newest section Faculty holds for the instructor's own Publish grades until a later publication of that
  assessment finishes. Read only: the Assistant cannot certify, tick the box, or publish. "Student triage" names the provider and model and, over 30 days, counts conversations
  shown as FYI, those the instructor later answered or tracked, and follow-ups closed on a thanks (and undone). These are
  observed lower bounds, not an accuracy rate. Its link opens Follow-up's FYI filter.
- **DIAGNOSE.** The request names the row the instructor saw, never a raw route; the page stays the review context.
  Errors are toasts. A review-only message carries the flag the server recorded and shows "Review only · every change
  waits for Approve".
- **HOME CARD.** Home's Needs you shows "Assistant changes waiting" while Approve cards wait in Changes, counted by the
  assistant's own rule (`automation.changes_waiting`: latest event proposed or previewed, within EXPIRY), less follow-up
  preparation cards that Student follow-up already counts. It opens the drawer's Changes tab, adds to Home's count and
  to no sidebar badge or tab title, and is absent at zero.

The new code is workspace-owned read projection (`assistant/automation.py`); every source keeps its owner (term grading
plan, term, enrollment, workspace). No migration, table or outward call is added.

### Learning from the instructor's corrections (2026-10-12)

The instructor (2026-10-12): "tweak the memory system so that when I edit the recommended suggestions of the followup agent, it
compares the changes to what was there and makes some sort of memory from it". Asked directly, they chose that the replies they
edit, the decisions they change and the changes they ask for all teach; **"Kept at once, with Undo"**; and, of the lesson review
of 2026-10-08, **"Replace them"**. The rule is ASSISTANT-MEMORY "LEARNED FROM AN EDIT". What it means here:

- **WITHDRAWN: THE LESSON REVIEW** ("Learning from reviewed follow-ups", 2026-10-08, and its LEARNING REVIEW FIXES of
  2026-10-09). In four days it analysed 142 reviews and showed the instructor none: its check asked for .95 on four questions,
  and their 42 real corrections answered at most .88 on one of them. Gone: the background analysis, Term policies › Learn from
  feedback and its count, adoption, the lessons and examples in a preparation's prompt, "Shaped by", the evaluation export and
  its CLI, and the `/api/student-support/learning/*` routes. Its text is in `docs/history/assistant-lesson-review-2026-10-08.md`,
  which binds nothing.
- **WHAT STAYS.** The tables of `student_support/016_feedback_learning` and `017_learning_mail_outcomes` and every row in them
  (triggers still refuse any change). `student_support_learning_cases` is now the durable queue of corrections
  (`assistant/followup_learning.py`): a case belongs to its enrollment through its run, references immutable actions, runs and
  sends, copies no words, and its last event says what came of it. Cases the lesson review analysed stay `ready` and are never
  picked up. No lesson exists and none is made.
- **NO DATE, GRADE OR PERSON BECOMES A RULE.** No enrollment note, date, grade, recipient or identifier reaches the rule's writer
  or is kept. A changed absence decision teaches at most how a kind of absence is decided; a regrade's grade never teaches.
- **A MEMORY CHANGES NO PERMISSION.** A learned rule is words a later request is told. It changes no tier, makes nothing
  automatic, sends nothing, and never lets the Assistant propose Excused without evidence (`_excuse_evidence` reads no memory).
- **STILL TRUE OF EVERY PREPARATION** (the 2026-10-09 review's CHECKS AND EDITS). A preparation keeps its latest evidence result
  and its latest tool-use report separately, so the EVIDENCE WARNING can fire. Private student notes reach the preparation's
  prompt and chat only: every Jev/Decisions input (the draft, obligation, evidence and tool-use checks, and a correction's
  comparison) leaves them out (STUDENT-NOTES). Edit waits while a preparation runs (409), and the automatic draft check never
  reads or withdraws the instructor's edited version.
- No route is added. The memory's own routes stay `never` for the Assistant (ASSISTANT-APPROVALS), so nothing it reads can plant
  a memory; a correction is the instructor's own edit, choice or request, confirmed by their own approval.

### Tool-use assistance (2026-10-09)

The instructor requested all five tool-use improvements: relevant tools, context before action, intent checks, result
coverage and completion/loop checks. `assistant/tool_guidance.py` is workspace-owned integration code. Each in-app turn
keeps a bounded ephemeral trace; external MCP clients can start the same guidance with `tool_use_advice` and their request
(one task per MCP session).
The real tool registry supplies suggested names. All engines keep access to their normal tools; advice never grants permission.

Complex requests may receive a finite area recommendation (both choice orders must agree at .8). Meaningful proposed writes
are checked for intent and enough context: either at .1 or less returns a request to read/correct before making a card;
intent or context below the measured scores (TUNED, below; until 2026-10-12 intent below .9 or context below .8) demotes
automatic changes to approval. An unavailable configured check also
demotes, never authorizes. A follow-up redo is held but never demoted, by a check or a corrective pass, since what it starts
waits for approval anyway (REDO FROM CHAT "STILL NO CARD", requirement, 2026-10-12). Disabled/unconfigured Jev leaves ordinary policy enforcement in place. Numeric values, identities,
dates, exact revisions, previews and approvals stay in code. No check can weaken a route's tier or make an outward action automatic.

At most two ambiguous/large read results receive semantic coverage checks. Three identical read results trigger a no-progress
response on repetition until a new write receipt permits a fresh verification read; confirmed/failed/uncertain write receipts
are reused, never submitted twice within a turn. At most 64 distinct change receipts are kept before requiring a new task. A completion
check distinguishes a completed answer from missing work and unsupported success claims; clearly pending approval is a valid
stopping point. At most one corrective pass precedes final release, with new changes requiring approval and submitted writes
never repeated. Follow-up draft checks still run afterward. Cancellation prevents new actions after a check returns.

At most seven decision calls per turn (two reserved for completion), stopped after a failed/uncertain call, use the existing Jev receipt and no-resend rules.
Source bodies, tool arguments and result excerpts remain only in bounded turn memory; saved reviews contain receipt IDs and
short findings. Image metadata is never treated as evidence of image contents. `FACULTY_ASSISTANT_TOOL_GUIDANCE=0` disables the
integration without disabling ordinary route validation. The route, coverage and completion thresholds are conservative review
controls, not measured accuracy; the preflight's were measured (TUNED).
Synthetic trace checks exercise routing, abstention, missing context, intent mismatch, coverage, loops and completion.

Review fixes (2026-10-09; the instructor: "Fix all of the issues and implement your suggestions"; they want their automatic
categories kept):
- **Task.** The checked request is the instructor's newest message, with up to two earlier ones as context. A continuation
  (receipts, answered questions) keeps that task and adds its own text as context, never as the request. All of it stays in
  turn memory.
- **Retries.** Each instructor message is an explicit retry: checks carry its attempt, so a failed/uncertain receipt of an
  earlier identical message is not replayed. Within a turn and its continuations, nothing is resent.
- **Budget.** A check skipped because the turn's budget is spent still demotes. It says so ("used all its tool-use checks",
  never "unavailable"), and the tool-use review notes it. An approve-tier write becomes a card anyway, so its preflight
  (only a hold matters) runs only while two non-completion checks would remain for automatic writes. An error result gets
  no coverage check.
- **Fixed prompts.** A follow-up preparation and the read-only first selected-area analysis get no area recommendation:
  their prompts name their reads. That analysis also gets no coverage or completion check, since Jev cannot see the selection.
- **Approval lock.** In a follow-up preparation, the preflight runs outside `_approve_lock`. Validation, the guard, the
  duplicate check (repeated after the preflight) and recording stay under it. Since APPROVALS SIDE BY SIDE (2026-10-31), an
  Approve holds it only for its checks and its "approved" record.
- **Corrective pass.** Its answer replaces the first and must be complete. If it fails, is stopped or writes nothing, the
  first answer is shown with a short status line and the completion concern is kept for review. New changes in it are cards,
  DONE FOR YOU records included. Preparation continuations are exempt (CONTINUATIONS), and a follow-up redo (STILL NO CARD).

**TUNED** (2026-10-12; the instructor, told that the preflight had let none of its 134 checks through and that no change had been
automatic since 2026-10-09: "in a copy do some experiments and tweak it until we tune it correctly"). Measured on a snapshot
copy with the real Assistant model and Jev (CHECKS.md): the check was shown the latest ten tool calls whatever they read (after
a refusal, the model reading Faculty's own code); a follow-up preparation's request was its fixed line; and neither question
said what yes meant. Rule `assistant-tool-preflight-v2`:
- **Shown.** The reads of this turn that mention what the change names (record IDs, enrollment IDs, conversation keys), as
  excerpts around them; a one-line list of the turn's reads; reads of Faculty's code and documents left out; plain words for
  the routes that can be automatic (`GLOSS`). The reads stay in turn memory, without private notes, as before.
- **A preparation's task.** A change a follow-up preparation proposes is checked against what it is asked to do
  (`SuggestionConversation.TASK`) with the changes the instructor asked for in that run, and is told the follow-up Faculty named.
  The coverage and completion checks and the evidence check keep the fixed line (EVIDENCE INPUT).
- **Asked.** Each question says what true and false mean: is the change what the task asks for, on the record it concerns;
  do the reads and handed facts cover the record the change is made to.
- **Scores.** Held at .1 or less (unchanged). Made at once from intent .75 and context .5. In a follow-up preparation from
  intent .3 and context .5: its request is Faculty's standing task, not the instructor's words, and code already limits its
  automatic records to its own student and conversation (DONE FOR YOU). These were set on the instructor's own records
  replayed against Jev, and a later change of them repeats that replay. No route's tier changes; an outward action is no
  more automatic than before.
- **Waits.** A Jev request is announced as the working line only when it is made: "Choosing where to look first…", "Checking
  this change…", "Checking this result…", "Checking the work is complete…".
- **Refusals.** An owner guard's refusal (an HTTP error such as Conflict) reaches the model as its own text, with no traceback.

### Review fixes (2026-10-09)

The instructor asked to fix the review of the workflow-capability and selected-area commits.

- **CONTINUATIONS.** A continuation is checked (stage limit, this run's own teaching actions, one per stage, well-formed)
  before it is recorded; a refused one leaves no action. Continuations are never teaching actions: approvals, rules,
  watches and the next stage judge only the run's teaching actions. The next stage reads its source with its rule's lead
  time, and its prompt lists the parent stage's receipts first. Stop cancels a next stage still waiting; the stage's cards stay.
  Tool-use checks and the corrective pass never hold, review, demote or spend a decision on a continuation; code validates it.
- **READINESS.** The fingerprint holds each answer-area check's status and review only, not its reading progress.
- **MATERIALS.** A request ID's retained outcome is read before any new preview. An import refused before or by its
  destination (4xx) is `failed` (migration `assistant/008_material_failed`), never repeated under that ID; other failures
  stay `uncertain`. Receipts name the destination's owner (rubrics and quiz drafts: the term). Previews use the retained
  artifact's recorded hash.
- **SOURCE COMPARISON.** Canvas columns are those Sync maps (with Faculty's Canvas copy) and compare by Sync's rule; grades
  Canvas holds for its own review are unknown; a failed send never hides an earlier observation. Gradescope is compared only
  in linked sections; a grade never sent is counted under its source, not unknown. A typed value over an iClicker import is
  listed with `typed_value` and is not daily-review drift. Each Gradescope plan holds the lifecycle lock alone.
- **SELECTED AREA.** Closing the bubble ends its conversation on the server; opening one in the Assistant releases the
  drawer's previous idle conversation. A model process that already holds the selection gets a short reference instead of
  its text and image again; a new process or the hosted loop gets it in full. The upload bound covers the selection limits.
- **VIEWS.** The Automation tab's timed refresh leaves the preparation form (preview, fingerprint, request ID) alone and
  reads its choices only on opening and after a create. Follow-up record hints come only from a review on screen. Review
  time averages approved cards only.

### Preparation review fixes (2026-10-09, second review)

The instructor: "Fix all of the issues and implement your suggestions." Migration `assistant/009_preparation_status` adds
workspace-owned rule status columns and re-baseline receipts that reference their immutable preparation runs.

- **SOURCE FACTS.** A preparation's fingerprint (scheme `v2:`) digests a narrowed basis of decision-relevant owner facts,
  part by part, never the whole observation: readiness keeps the phase, reminder window, exam dates, next step, readiness
  checks, stage states, grading plan, rubric approvals and answer-area outcomes, not today's date, response counts or detail
  text; reconciliation keeps final-grading differences, source drift and unverified grades, unlinked assessments, the morning
  check's problems and held grades, and the gradebook's columns, not sync or observation times, whether today's check is
  recorded yet, or Faculty changes that only wait for the next Sync. The full observation stays the prompt's. Each run keeps a
  part map with its queued event, so a refusal names what changed ("the exam date moved", "rubric changed"). The same comparison
  serves the rule's check, dispatch, approval and the rule's record after an approval. A run fingerprinted under the earlier
  scheme is re-baselined to the facts current when it is next compared, never expired by the change of scheme.
- **WITHDRAWAL BY STATE.** A new run of a target withdraws the unsubmitted cards and waiting stage of earlier ones: queued,
  unanswered and card-holding ready drafts become stale, the latest ready one is replaced, and a failed or interrupted run keeps
  its state (never repeated automatically) while its cards are withdrawn. Pausing a rule or watch revokes pending work in every
  state. A pending card blocks an identical proposal only while it can still be approved (its own run, or a current run prepared
  against the same facts). "Once, now" observes with the default seven-day reminder, as dispatch and approval do.
- **PLANS AND STOP.** A run with a waiting next stage, or a next stage still under way, belongs to the plan: the rule's check
  records its fingerprint and leaves it. Stop is recorded (stale cards, `interrupted`, stopped) under the locks before the engine
  is told; a card recorded afterwards is refused in the transaction that would record it, and a finish writes only over
  `running`.
- **LOCKS AND READS.** The dispatcher decides under the approval and lifecycle locks, releases them, observes and starts the
  model, then re-takes them to record; each Gradescope plan of a reconciliation read takes the lifecycle lock alone. A preparation's
  source is observed only when a card is about to be recorded, and once per approval or approved batch. The Automation view reads
  the catalog without the lifecycle lock.
- **STATUS.** Rules project `waiting` (the normal morning wait, no longer a blocker), `held` (why automatic preparation stopped:
  a failed or uncertain step, a failure, a restart, or the instructor's Stop; Prepare again resumes), `last_checked_at` and
  `last_outcome` ("No discrepancies", "Prepared N changes for review", "Source unavailable", "Finished: assessment given",
  "Finished: term ended"). A given assessment's rule is checked each morning instead of every two minutes; a rule whose term is
  no longer current is skipped. Neither changes `enabled`. Readiness of an assignment-and-term target opens its assignment
  workspace. Unfinished preparations count a ready run only while a card or a next stage still waits.
- **PREPARE AGAIN.** A new run's prompt also carries the run it follows: its retained questions with their options and the
  instructor's guidance, at most 1,000 characters.
- **REPLACING REQUESTS.** An assistant request to prepare a target whose current preparation still holds the instructor's
  unsubmitted cards is an Approve card naming how many it would replace. This narrows REQUESTED ANALYSIS's automatic tier.

### Follow-through and conflict review (2026-10-09)

The instructor requested the review's items 1, 2, 6 and 7. Follow-up checks distinguish a reply owed from work still owed,
using current enrollment-owned records. A thank-you never proves that promised work was completed. The existing automatic
closing rule requires both answers to support completion; unavailable, incomplete or ambiguous evidence keeps the follow-up
visible. Check receipts contain source fingerprints, not copied records.

Before a follow-up preparation is shown, a bounded Jev check examines unsent replies for contradictions and unsupported
promises. A confident warning permits one corrective model turn. Only pending reply/tracking proposals are superseded;
completed work and other record proposals are kept. The corrective turn can propose only replies and follow-up tracking,
and never repeats a record change. Remaining warnings or unavailable checks are shown for review. Send stays explicit.

Home's workspace-health failures and Assistant Automation blockers offer Diagnose and prepare fix; the grading checklist's and
the assignment workspace's Prepare remaining setup (2026-10-09 review: they promised approval but ran an ordinary turn) are
review-only too. These explicit requests start an ordinary Assistant conversation turn with server-enforced `review_only`: even normally automatic writes become
approval cards for that turn. The assistant groups common causes, reads actual job status and uses existing owner routes.
Unknown, interrupted or successful operations are never automatically retried. The flag cannot grant any additional ability.

Canvas conflict comparisons can explicitly hand their bounded displayed evidence to the assistant. The dialog and its edits
remain open. A proposed resolution retains the ordinary owner and approval policy; the existing Push remains explicit.
Button requests show a short user-facing question; bounded `review_context` supplies the explicit comparison or page identity
to the model as untrusted evidence. That context is ephemeral and not included in saved conversation snapshots. Review-only
restrictions persist through receipt continuations and saved-conversation restoration; a new ordinary instructor request
returns to the normal policy. The integration modules and conversation flags are workspace-owned; follow-up evidence references enrollment-owned records,
and Canvas evidence retains the term/section owner's identity. No new catalog tables or source-payload storage are added.

### Selected-area conversations (instructor-approved)

The instructor changed the gestures (2026-10-08): hold an unmodified Command key, with the pointer still, for 600 ms to select Faculty's own content
in Chrome and Safari, draw a box, then release Command to send that box for analysis. Mouse-up alone does not send it;
the box can be redrawn before Command is released. No box, a tiny box, Escape, another key/modifier, cancelled pointer input,
or leaving the window cancels selection. Releasing while a PDF is still preparing cancels it and restores the viewer.
Two quick unmodified Command taps instead open a floating Assistant chat with no selected content and no initial model
turn. Its typed messages use the same ordinary tools and approvals as the main drawer. The conversation can transfer to
the drawer (and back: BOTH WAYS), repeated double taps focus its current draft, and changing tabs preserves an unsent typed chat. Normal Command shortcuts remain intact. The toolbar/viewer
buttons remain available as click-and-drag selection, submitted on mouse-up. The instructor replaced screen sharing with this direct
selection: no display permission, window calibration or screenshot of the browser. Visible selected words and fields go as
text; selected gradebook cells include their displayed values and permanent record references, with unsaved edits marked.
Selected images, canvases and charts are cropped from their own pixels; multiple visible crops can share one image. Native
PDFs temporarily use the existing sandbox-rendered page view for selection, preserving and then restoring the same native
viewer. The instructor can scroll those pages before dragging. Hidden/occluded content, credentials and student photos are
excluded. General page context still contains only IDs and an unsaved-work flag; the selected field content is an explicit
exception, never a background form scrape.

A fresh workspace-owned conversation, or the open chat the selection joins (below), automatically analyzes
the selection read-only, then accepts typed follow-ups under the ordinary action policy. Selected content is untrusted evidence, never an instruction or proof of current server state.
Request IDs prevent duplicate initial analysis; uncertain requests are checked, never automatically resent. The selection
holds at most 12,000 text characters, 40 record hints and 8 image labels; the combined image stays within 4 MiB and 12 megapixels.
It stays in bounded conversation memory. Images use inline model inputs, including Codex, and are not staged as image files.
Saved conversations retain conversation text and selection metadata, not selected source text, field values or image pixels;
expired selections must be made again. Closing, replacement, navigation and tab hiding release the selection UI; closing also
stops unfinished analysis (what navigation and tab hiding keep is set out below). No migration or automatic teaching-record change is authorized. These integration modules belong
to the workspace; record hints refer to their existing term, section and enrollment owners.

Floating chat and selection lifecycle (requirement, 2026-10-09 review: "Fix all of the issues and implement your
suggestions"). ONE CHAT (instructor, later on 2026-10-09: "if I have one of those chat windows open, I want the cmd-hold block to
populate that specific opened chat window"): a box sent while a chat window is open goes to that chat, whether Command-Command or
an earlier selection opened it, and its unsent message stays. The same conversation receives the capture under a new request
ID, with the same limits and the same read-only first analysis (`_capture_analysis_only`); a later selection replaces an earlier
one, which expires, and a replaced request ID is never analyzed again. While the chat is answering or has an open question, the
box waits in the window ("Your selection is sent when this answer finishes" / "once you answer") and is sent once it is idle (a
box the server turned away because the chat had just become busy waits the same way; one taken is never sent again); a newer
box replaces a waiting one. A selection chat takes the new box's page. A box drawn inside another modal dialog moves the chat
into that dialog, which then closes it. Only a chat that never opened or whose conversation has ended is replaced by a fresh one,
asking about an unsent message then. Holding Command or pressing Select over an open bubble only hides it; a cancelled
selection (Escape, a chord, blur, no or a tiny box) brings it back unchanged, never asking while Command is held.
MOVABLE (instructor, same day: "move the chat panel around like a window opened on a computer if it is blocking something"):
the chat window's title bar drags it (grab cursor; a press that does not move is not a drag; its Close button keeps its click).
It keeps the place it was dragged to through answers, selections and window resizes, always whole inside the window. Since
REAL WINDOW (2026-10-09) that place is remembered for the next floating chat (before: "a new chat opens in the usual place");
a selection chat still opens beside its selection. The hold never
arms while focus is in the chat, so Command chords typed there keep their meaning. Moving the pointer more than about 4 px before
the hold arms drops it, and a click without a drag during a hold passes through as an ordinary Command+click (links open in a
new tab). Escape belongs to the assistant only for a selection or when pressed inside the bubble; page controls keep theirs.
Following a Faculty link, including the chat's own answer links and "Review on its page", keeps the floating chat and its
conversation, like the drawer; navigation still releases a selection and ends a selection chat. Each typed floating-chat message
carries the page and record hints on screen when it is sent. A lost or ambiguous message response (network, 5xx, 409) is settled
by reading the conversation once and is never resent; a definite refusal keeps the draft. A failed read is retried (Stop stays
while the server answers); only a gone conversation stops reading. Mail cards in the bubble run the same JEV send checks as the
drawer. A finished selection chat survives a hidden tab; tab hiding ends only a selector, a preparing PDF or a first analysis
still running.

### Complete workflow capabilities (2026-10-09)

The instructor requested all eight workflow/automation improvements from the repository review.

- **TASK ACTIONS AND CONTEXT.** Focused tools prepare grading, assessment sittings, rubric edits, grade pulls and delivery
  reviews through the existing owner routes. The page supplies bounded record IDs and an unsaved-work flag, not form contents;
  these are navigation hints, never authority. Owners revalidate every identity, revision and proposed change.
- **REQUESTED ANALYSIS.** An instructor's explicit request for a preparation starts its analysis immediately, without a
  preliminary Approve card. Its retained run belongs to the workspace and references the term. Background work cannot start
  other preparations or enable rules. Every teaching write from a preparation still requires approval. An assistant request
  that would replace the instructor's unsubmitted cards of the same target is an Approve card (2026-10-09 second review).
- **STAGED PREPARATIONS.** This supersedes the single-round restriction for general assessment/reconciliation preparations,
  not the enrollment follow-up rules below. A preparation can retain one next-stage instruction and references to its exact
  prerequisite actions. Once all succeed, one fresh preparation reads their receipts and current owner projections; newly
  proposed changes still require approval. At most eight stages; declined, failed, uncertain, paused and stopped work never
  advances automatically. Continuation metadata is workspace-owned and references its immutable preparation/action owners.
  Action completion, past-work recall, learning and metrics share `assistant/outcomes.py` (2026-10-13, requirement:
  "Implement these refined changes"). A route acknowledging a start is not completion. Linked local receipts may
  establish completion later; reading them never polls a remote service, retries work or widens the automatic tier.
  **STILL RUNNING IS NOT A PROBLEM** (requirement, 2026-10-13, of a review of that change: "Please fix"). A step
  Faculty is still carrying out (`started`: a Canvas push or message, a regrade's Apply) is not done, so a later card,
  a scheduled status and a next stage wait for it, and it is not a problem, so no stage is stopped, no rule is held and
  no follow-up is flagged for it (`followup_plan.running`, beside `problem`). A card approved while the step before it
  still runs is refused in those words ("the step before this one is still running"), and approving it again once that
  step shows Done sends it. A step the instructor cancelled before anything was done is out of the plan as a declined
  one is; one cancelled, stopped or failed after part of it was done is `partial` and needs a look, as a failed one
  does. A Canvas message one course did not confirm is `uncertain`, never partly sent. Sync with Canvas reads its
  send's own status. Making an assignment on Gradescope (`frq_gradescope.create`, which answers `pending`) is done only
  once every course has it; a course whose assignment stopped makes it partly done, or failed when none was made, until
  the instructor's Retry on its panel (requirement, 2026-10-13: "add the gradescope route"). A result too long to keep whole keeps the ids its receipts are found by (`outcomes.link`), and a
  receipt that cannot be read leaves the action as its own event recorded it, said once in the log.
- **SOURCE COMPARISON.** Reconciliation's existing daily schedule compares gradebook values against final grading, retained
  Canvas observations and subsequent confirmed send receipts, retained Gradescope observations and the latest imported iClicker
  values where available. Sources retain their original owners and dates. Missing/unavailable observations are unknown, never
  agreement. No new remote read or send is authorized; typed values, intentional overrides and uncertain outcomes are preserved.
- **TEACHING MATERIALS.** Reviewed generated text or an explicitly selected retained artifact can enter the existing quiz,
  assignment-version, rubric and course-note routes. The bridge accepts bounded content/retained IDs, never arbitrary filesystem
  paths or network URLs; it validates the destination owner and records an idempotent workspace request receipt. Compilation uses
  the existing document sandbox. Approval is required; it never prints, sends, certifies or publishes on the instructor's behalf.
- **EARLY PREREQUISITES.** Readiness also addresses an assessment by its stable assignment and term when no exam event exists.
  Opt-in rules inspect owning grading prerequisite projections before scan day. Recovery tools explain blocked work and prepare
  only the existing exact retry controls; they never retry unknown outcomes or override instructor prerequisites.
- **AUTOMATION VISIBILITY.** One Assistant Automation view projects built-in automation and opt-in rules, scope, next check,
  last result and blockers; blockers are real problems only, beside neutral waits, held reasons and the last check's time and
  outcome (2026-10-09 second review). Configuration keeps its original owners and controls. Review metrics describe observed waiting,
  corrections and unfinished/stopped preparations, not inferred accuracy or time saved. Since 2026-10-09 it also lists active runs
  and a "Done by Faculty" log, and shows real outcomes for the late-paper, makeup-version and Student triage rows ("Automation
  visibility (2026-10-09)" above).

New implementation files for these capabilities are workspace integration code. Reconciliation outputs are term read projections;
teaching material payloads remain with the destination course definition/term/section and existing storage managers. Development
fixtures live only in isolated NVMe scratch. Earlier migration SQL remains immutable.

### Integration reliability and opt-in preparation (2026-10-08)

The instructor asked to implement the integration review's fixes and improvements.

- **USABLE APPROVALS.** Schedule Use/Undo, putting office hours in the Canvas copy,
  sending one Canvas cell, and quiz Rebuild/Scrap supply their owning route's exact
  confirmation only on the instructor's approval. Use and one-cell sends re-preview.
  The standalone regrade Apply remains a page action with its acknowledgements;
  the existing exact regrade Decide card is unchanged. New route integrations must
  test a real submission, not only the route's tier.
- **COMPLETE READS.** Assistant output is bounded valid JSON, with omitted JSON
  pointers and offsets. `faculty_get` can page an array, object or text field by
  pointer, offset and limit. Continuations reread the owner and check its fingerprint;
  nothing caches mutable responses. The fingerprint covers the pointer and the node it selects (2026-10-09 review: an
  overview's `generated_at` refused every continuation); a change to that node still refuses. A partial result's note
  names `faculty_get` and the route only for a tool that reads one GET route; a composed tool's says to ask it for less. Student lookup uses verified roster projections,
  not whole-term grade calculation. Ordinary page APIs remain unchanged.
- **PREPARE TEACHING WORK.** Workspace-owned opt-in rules (`assistant/004_preparations`)
  target one assessment's readiness or one current term's grade reconciliation.
  Readiness choices use the owning assessment lifecycle's planning/upcoming phase,
  never every exam in a current term (requirement, 2026-10-09: already-given assignments
  were appearing). Past assessments refuse new readiness preparations and no longer
  queue them automatically; grading/reconciliation and retained receipts remain
  available. Early papers do not hide an assessment whose main sitting is still ahead.
  Their runs reference the owning term; an unassigned scan watch's run belongs to
  the workspace. No copied source bodies, alternative gradebook or task-owned
  teaching records are created. The worker examines one due local projection per
  turn. Readiness checks are at least two minutes apart; a relevant change, or entering a configured
  1–30-day assessment reminder window, queues one preparation.
  DAILY RECONCILIATION (requirement, 2026-10-09: "check once a day (at 5AM central) for grade discrepancies between the sources"):
  reconciliation is scheduled at 05:00 in the shared campus zone, following daylight saving time.
  Migration `assistant/006_daily_reconciliation` reschedules existing rules without enabling paused rules or changing receipts.
  Enabling/resuming schedules the next 05:00; after downtime a due check runs on the next available turn.
  The persisted `next_check` advances to the next campus morning before the read, so restarts and failed reads never repeat
  that day's check. Explicit Prepare now remains available. At 05:00 it waits for the existing morning Canvas run and any
  running Canvas writes (the check runs in the Inbox worker: the catalog's sends and posts of the term still running from the
  last 15 minutes), and while a signed-out or failed morning attempt will still be tried again before 08:00; if no morning run
  is recorded by 08:00, it checks available records and reports missing evidence.
  It starts no new remote read or sync. It compares the owning grading/gradebook projections and includes recorded Canvas
  held grades, uncertain outcomes and failed source reads. Unchanged results queue no duplicate preparation.
  The web process's
  existing exclusive dispatcher alternates background preparation queues; interactive
  work and explicit follow-up requests keep priority. Background answers count in
  busy/restart checks. No inference runs in the Inbox worker.
  Every write in these runs is an Approve card, including chat's automatic categories.
  The preparation names missing prerequisites and discrepancies using the owning
  exam/gradebook services; Canvas facts say they are the last recorded observation.
  Final grading, typed grade preservation, certification, Send and publication keep
  their existing rules. A preparation cannot enable more preparation by itself.
  Rules are off until explicitly enabled, with an exact preview; pause/revision
  checks revoke pending work. The instructor can also prepare once, or explicitly
  prepare again with guidance. Changed sources withdraw unsubmitted drafts; before
  the first approval the source's decision-relevant facts are rechecked, and subsequent steps retain
  their exact owner previews and revision guards. Records precede messages; failed
  or uncertain steps stop later steps. An interrupted or failed preparation, or
  uncertain write, never repeats automatically. Completed and declined actions
  remain receipts and exact duplicates are refused. Background transcripts are not
  silently saved; bounded explanations, questions, action references and usage are.
- **WATCH PREPARATION.** A scan, grading or exam watch may explicitly request
  `prepare_review` (`assistant/005_watch_preparation`). Its exact preview includes that choice. Only the requested
  meaningful transition queues a review; ordinary watches continue notifying only.
  Pausing/removing the watch revokes pending preparation; existing failed/uncertain
  work is never retried by a watch. No watch sends, publishes or certifies.
- **VERIFIED CODE APPLY.** Apply independently runs relevant synthetic Python/Node
  checks selected from changed files, using the trusted `manage_checks.environment`
  and fresh NVMe scratch. The retained receipt identifies the tested head/base and
  check selection. Rebase is followed by checks; a changed head/base or dirty clone,
  failed checks, unavailable activity checks, or Canvas writes (the morning sync with
  Canvas included while it runs; no fixed morning window since 2026-10-17) prevents merging/restarting. Recheck busy state after verification. The
  coding agent's own test report grants no exemption.
  SELECTION (2026-10-09 review: grading, Inbox and scheduling changes ran only `test_app`): a changed file selects the test
  files it changes; its own tests (`test_<family>_<stem>*`, `test_<stem>*`, the test modules importing it unless more than 25
  do, the scripts importing a static file); then its package's family (`code_checks.FAMILIES`, e.g. grading →
  `test_grading_*`, `test_frq_*`, `test_gradescope_*`; teach_inbox → `test_inbox*`). Deterministic and bounded: at most 40
  Python modules, ten to a command, and 30 Node scripts by that priority; modules beyond the cap are named as `not_run` in
  the receipt and on the Code tab. Apply and the post-rebase check refuse any added or changed symbolic link (git mode
  120000), and the source tools never read through one. While Apply runs, `applying` (stage, start, selected counts) is held
  in memory only, returned by the task reads and cleared when Apply ends.
  THE DIRTY-CLONE COMPARISON HAS NO EXCEPTIONS OF ITS OWN (requirement, 2026-10-12: "an agent editing files during verification
  must still be caught"). Apply's own steps write nothing into the clone: the compile step compiles in memory (`coding.COMPILES`;
  `py_compile` wrote `__pycache__/` whatever `-B` said, hidden only by the repository's `.gitignore`), and the checks keep their
  bytecode and caches on scratch. A step that needs to write there is the thing to change, never the comparison. What
  `.gitignore` names is still not compared (git does not list it); it is not in the commit, so it cannot reach `main`.
- **MEASURED REVIEW.** The offline plan evaluator accepts exact expected body fields,
  enrollment and recipient constraints, and supported-claim labels in addition to
  steps/order/completion. The read-only review-activity projection counts bounded
  retained approvals, edits, declines, failures and questions; it makes no claim of
  measured model accuracy or of whether a question was necessary.

The typed assessment-readiness, grade-reconciliation, preparation and makeup-context
tools use Faculty's routes and these same tiers. Preparation configuration/update
and retry controls are instructor-owned; only explicit creation proposals are
available as approval cards. See `assistant/workflows.py`, `task_tools.py`,
`pagination.py`, `code_checks.py`, and the synthetic integration/workflow checks.

### Completion and follow-up improvements (2026-10-08)

The instructor requested implementation of the assistant review's six improvements. These rules supersede earlier
mail-first execution and message-only freshness rules below; explicit Send and exact approvals remain unchanged.

- **PLAN COMPLETION.** Prepare one complete plan. Its displayed and submitted order is prerequisite record changes,
  reviewed messages, then tracking. A message cannot be submitted while a required earlier record change is pending,
  failed or uncertain. Check every selected card before starting, retain each result, and stop on the first problem.
  A failed or uncertain action keeps its conversation in Needs me, including a previously resolved conversation.
  Waiting on student requires a confirmed message delivered to this student for the current conversation; staff-only
  mail and replies predating its newest student message do not qualify, except on a follow-up already waiting whose student
  only acknowledged it (TEACHING-WORKFLOWS STILL WAITING). Scheduled tracking waits for the plan's work.
  DECLINED STEPS (2026-10-08, requirement's review of this change, "yes please fix"): a step the instructor declined, or one
  that expired unsubmitted, is no longer part of the plan, so it never holds back a later message or tracking change (a mail card
  replaced by the instructor's own Mail send still stands for that send). Scheduled tracking waits for the reply that reaches this
  student; a note to the instructor or a TA neither holds it back nor stands in for it (only a round with no reply to the student
  goes by its other messages). A reply posted in Canvas (MAIL-CANVAS-POST, no email recipients) reaches the student when it was
  posted in this follow-up's own conversation.
- **CONTEXT.** A shared enrollment-owned read projection supplies chat and follow-up preparation with current conversations,
  policies, notes, commitments and assessment/meeting facts. It references existing owners and retains no copied messages.
  Focused task tools still call Faculty's routes and ordinary policy enforcement. Preparation records bounded source
  fingerprints, and approval checks relevant policy, calendar and record changes; stale proposals require refreshed review.
  The calendar is compared over the draft's own 21-day window (from its preparation day), so a draft approved on a later
  campus day is not stale for that alone (2026-10-08 review: the window had moved with the day). An exam this plan wrote to
  (a makeup sitting, the student seated in it) changes its sittings' calendar rows; those are the plan's own while nothing in
  that exam changed after the plan's last write there, as its own appointments and calendar events already are. Like its own
  appointments, the plan's makeup sitting must still be at the day and time it booked, and the student still in the sitting
  it seated them in; otherwise approval is refused.
  WHAT CAN MAKE A STEP WRONG (2026-10-08, requirement, of approvals refused for any change at all, replayed at 14 of 84:
  "implement your suggestion"): approval is refused only for (1) a calendar entry added, moved, edited or removed at a time the
  plan books (its meeting, makeup sitting or calendar card) or names (the days and clock times of its messages, read in code by
  `jev/dates`; a day named without a time is the whole day, with times from the earliest to an hour after the latest; all-day
  entries take no time, and a makeup only added there shares it); (2) a paper that arrived for one of the student's exams (a
  new exam, or another scan, is none); (3) a changed decision on one of the student's absences (excused, not excused,
  withdrawn; a new pending one is none). Every other change (term policies, notes, other absence details, grades or due
  dates, other communications, accommodations or DR bookings, the term schedule, the class schedule's planned rows, topics
  and links, under a key of their own so a draft from before 2026-10-10 is not outdated by it) is a warning. The review shows both before
  the click, for drafts with steps waiting (`context_changes` on the `suggestions=1` read, opt-in): "This draft is out of
  date" for a refusal, "Since this was drafted, … changed. Retry suggestions to redraft, or approve as it is." for warnings.
  Records kept before this (v1, no times) are compared as warnings only. `assistant/student_context.py` (`changes`).
- **WATCH UNTIL DONE.** An instructor may explicitly watch an enrollment's follow-up until a check-back date or a selected
  owning record changes. Bounded worker turns observe local projections, keep receipts, and prepare a new review on the
  requested transition, including To do. A watch never sends, retries an uncertain write, or resolves the matter itself.
  Paused/removed watches do nothing; stale conversations and withdrawn students cannot receive new preparations.
- **LEARN FROM EDITS.** Repeated instructor corrections may be shown as candidate guidance, with the original/edited
  evidence referenced. Applying guidance is the instructor's explicit, revision-checked term-policy edit: nothing becomes a
  term policy by itself. What one confirmed correction teaches the Assistant's memory is a rule of its own, kept at once and
  said with Undo (ASSISTANT-MEMORY "LEARNED FROM AN EDIT", 2026-10-12). Nothing is silently learned, and no enrollment's
  note becomes a global preference. Complete-plan synthetic scenarios measure
  missing work, unnecessary questions, edits, and incorrect completion as well as transport behavior.

Ownership: `assistant/followup_plan.py` projects enrollment-owned plans from workspace action receipts. New follow-up
context/watch metadata belongs to the run's or follow-up's enrollment. Candidate guidance is a read projection for its
course term, referencing existing enrollment-owned edits; adopted text belongs to the term's existing policies. Test
fixtures and their reports are workspace development artifacts, confined to isolated NVMe scratch.

- The instructor asked (2026-09-29): "integrate an AI agent into my app so I can have it work in the app, answer questions about
  information in the app/database". Asked, they chose: a Faculty tool set first (an MCP server usable from Claude Code/desktop),
  then an in-app panel; the model a setting among EVERY connection kind (OpenRouter, Vercel, Claude subscription, ChatGPT
  subscription); full access to student data (what the pages show); and "access to everything that I do", in two tiers (below).
  Hermes Agent was considered and not used as the core: it keeps its own memory, skills and sessions outside Faculty's owners
  and backups, and ships a shell; it could connect to the same MCP server later. `assistant/` (policy, transport, tools, service,
  api, engines, conversations, guide.md, migration `assistant/001_actions`), `manage_assistant.py mcp`, `static/assistant.js`,
  `static/assistant.css`, `tests/test_assistant.py`, `tests/test_assistant.mjs`.
- ONE ENFORCEMENT POINT. Every tool call, from the panel's engines and from outside MCP clients (the stdio proxy relays to
  `POST /api/assistant/sessions/<id>/tools/<name>` with the session's token), lands in `AssistantService.run_tool` in the web
  process. Reads and writes go through the app's OWN routes in-process (`transport`: `app.test_client()` with the same-origin
  header), so `protect_writes`, the storage guards, locks, request IDs, previews and every privacy omission apply unchanged, and the
  assistant reads exactly the JSON the pages read. Every one of its requests carries the WSGI environ marker `faculty.actor:
  assistant` (`request_actor.py`; no HTTP client can set it), so records that name an actor say the assistant made them (the
  gradebook's, requirement, 2026-10-01: "record the assistant as the author of its gradebook edits"; contract GRADEBOOK, ACTOR).
  Reads return JSON; files come back only through `fetch_files` (below).
  Credentials are removed from every answer (`policy.SECRET_KEY`); GET routes of sign-in windows, callbacks, Response Card links,
  photos and the workspace export are not read as data (`GET_BLOCKED`), nor is a recording's kept audio (RECORDINGS, 2026-11-13: it
  is played by the recording's own page and is neither read nor fetched as a file; the Assistant answers with the recording, the
  speaker, the time and the words, and opens `#recordings/<id>?t=<milliseconds>`), and a read never passes `reveal` (revealing a
  hidden grade records that it was seen). `catalog_sql` is one SELECT on a `mode=ro`, `query_only` connection with an authorizer (no ATTACH,
  PRAGMA or writes; secret columns read as NULL), 500 rows, 5 seconds. Repository documents and tracked source can be read so the
  model learns what a route expects.
- FILES (requirement, 2026-09-30: "I want to be able to ask the agent in my app about anything and it be able to fetch it and give it
  to me. for instance, if I ask about a student's exam, I want it to be able to fetch the actual scan of the document if needed";
  `assistant/files.py`, `tests/test_assistant_files.py`). `fetch_files` takes up to 12 GET routes that answer with a file and fetches
  each through the app's own route (`Transport.fetch`; never sign-in windows, callbacks, access links, the workspace export or the
  app's shell: `policy.FILE_BLOCKED`, which leaves photos, the communications record's kept files and absence documentation open).
  SHOW (default) puts them in the drawer: only the route Faculty fetched and checked travels, the browser loads it from the app
  itself (pictures inline, opening full size, a HEIC photo's thumbnail drawn from the viewer's re-encoded picture since most browsers cannot draw HEIC, and a HEIC photo LOOKED at is decoded in the document sandbox first; other files as links; `filesMarkup` draws `/api/` routes only), and the conversation
  keeps the routes, never the bytes. LOOK (`look: true`, the model's choice when the answer depends on the page) sends pictures to
  the model scaled to fit 1568 px (a scanned page fetched 1200 px wide), at most 8 a call; a PDF's chosen pages (default the first
  four) are rendered at 150 DPI in the document sandbox (operation `look`, never in the web process); text files go as text.
  A PAGE TOO LARGE FOR THAT IS DRAWN SMALLER, NOT REFUSED (2026-10-11, requirement, of a doctor's note the follow-up attached but
  left Waiting for your decision: "it still says waiting for my decision"; the preparation had been refused the page as an
  "oversized scan page"). An iPhone's scan is one page the size of its photograph, a point to each pixel: that note is 2228 by
  3193 points (31 by 44 inches) around a 7.1-megapixel picture, 30.9 million pixels at 150 DPI, just past the 30-million limit.
  `look` now draws such a page with its longer side 3300 px (`media.LOOK_SIDE`, a Letter page at 300 DPI; the note at 2304 by
  3300, about its picture's own size), for the file viewer, the model's look and picture search alike, which all read pages
  through it. A page within the limit is drawn exactly as before, and a scan's own pages (pixels at their resolution, the Inbox
  and the labels) are still refused past it. Photos
  are shown, never sent (`policy.SHOW_ONLY`). Pictures reach the model as MCP image content (Claude Code and Codex both read it;
  measured 2026-09-30, 8 pages in one call), or in the hosted loop as one user message after the tool results, dropped from the
  history at the next turn. MAIL DRAFTS WITH FILES (requirement, 2026-10-01: "I want the agent to be able to attach attachments to
  drafts of emails for me to approve"): a `gmail_mail.send` the assistant prepares may name the same file routes in `attachments`
  (MAIL-ATTACHMENTS); it stays an Approve card, and the card draws the Mail preview as a draft (recipients, subject, text, each file
  shown from Faculty's own route, Send). Nothing about mail becomes automatic. **VIEWER** (requirement, 2026-10-01: "whenever the agent
  finds me a file ... it always downloads it locally on my macbook instead of opening it in the app. I would prefer pdfs/images/documents
  open in the app with a download button"; `file_viewer/`, `static/file-viewer.js`, `app_files.py`): a link marked `data-view-file` (the
  drawer's shown files and Approve-card attachments, the Mail compose and review attachment lists) opens Faculty's file viewer, a modal
  dialog with Download (the route itself), Close and Fit/Actual size; a modifier-click keeps the browser's own behaviour. Since 2026-10-01 ("switch those other download links to the viewer too") also
  the communications record's kept files, absence documents and the course page's lecture notes (a topic opens at its page); explicit
  Download buttons stay downloads. Then ("switch the exports and print PDFs to the viewer too"): print-job PDFs and labels (the shared
  print-files links and their menu), compiled quiz versions and print stacks, question-bank PDFs, students' seating-chart previews,
  grouped scan papers and scan reports, an assignment's template PDF, a print job's manifest, the gradebook's CSV export and the bubble
  sheets' scores and item analysis (a CSV is shown as a table: 5,000 rows, 100 columns at most), and a Mail thread's attachments
  (MAIL-ATTACHMENTS-READ). Downloads stay downloads where the viewer cannot help: the certification step's files (each click records an
  export, a POST), ZIP and .xlsx files, starter sources, and the workspace backup. Then ("switch the scan page images to the viewer too and anything else
  ... useful"): scan page images (a scan's first look, the papers check, a page's retained PDF and image), version-sort outputs and
  reviewed-pages PDFs on the scan pages, grading's region and blank-exam pages, Exams' reviewed papers, the Inbox job's files, a bubble
  sheet run's readings CSV, the assistant's saved files and the sources it read that were files (a JSON source still opens as raw data
  in a new tab). Left as they are: links within Faculty, outside sites, the mail-merge .xlsx the page builds, the print-drive
  saver and the downloads above. The Response Card answers CSV, built in the page, opens as a table too ("switch the Response Card
  answers CSV to the viewer too"): `viewTable` shows the page's own rows, nothing is fetched, and Download saves the same CSV. The viewer
  reads the route through `GET /api/viewer/file|page.png|picture?route=` (WORKSPACE read projection, no-store, nothing stored), which
  read the file through its own route in process (`AppFiles`, the same blocked list as FILES; viewer and mail routes are never read):
  a PDF becomes pictures of its pages rendered in the document sandbox (`look`, four pages a call, loaded as they scroll into view;
  rendered pages kept in memory by SHA-256 and page, 96 MB at most; the page addresses carry the digest the file was opened with, so a
  page already drawn is served without reading a long PDF again, and a file that changed since is a 409), a picture is re-encoded (upright, PNG/JPEG, at most 3,000 px; an iPhone's JPEG, which Pillow calls MPO, is its first picture; a HEIC photo is decoded in the document sandbox, `heif`, once per file and kept with the rendered pages; requirement, 2026-10-02: "install pillow_heif so HEIC photos show too"), a
  text is shown as escaped text (200,000 characters), an email (`message/rfc822`, or a route that says only octet-stream for a file
  named `.eml`) as its headers, text (HTML reduced to text) and attachment names, anything else shows its name and Download. The
  Inbox's "Original submission" opens there too (requirement, 2026-10-01: "switch the Inbox original submission link to the viewer too"). FACULTY'S OWN PDFs IN THE BROWSER'S PDF VIEWER (requirement, 2026-10-01: "open PDFs from Faculty in the
  browser's own PDF viewer"): a PDF from a route in `file_viewer.service.NATIVE_PDFS` (lecture notes and their parts, question-bank
  uploads, print batches and labels, exam-day packets, assembled student papers, Faculty's seating charts, compiled quizzes and their
  print stacks, the assistant's saved files: what Faculty made or the instructor uploaded) opens in the browser's own PDF viewer inside
  the viewer, framed from `GET /api/viewer/pdf?route=&sha256=`, when the browser shows PDFs itself (`navigator.pdfViewerEnabled`) and
  has a fine pointer (a phone or tablet shows only a framed PDF's first page, so it keeps the pictures). That response alone carries
  `X-Frame-Options: SAMEORIGIN` and `Content-Security-Policy: frame-ancestors 'self'` (app.py); the route serves nothing but a PDF
  from that list, checked by its first bytes, and every other response stays unframeable. Files from students or outside senders
  (the communications record, absence documentation, Inbox submissions) are never on the list. Except for those, no file is ever opened as a document
  in the app's origin (COMMUNICATIONS keeps student files downloads), and SVG or HTML is never a picture. `student_paper` (student row or name, assignment title or ID, optional question) reads
  `GET /api/documents/papers?enrollment_id=&assignment_id=` (`ScanService.student_papers`, read only: each printed copy of the
  student's with its latest reviewed paper's pages and PDF, or the pages seen so far), and where a grading plan exists the
  certification drawer's questions and, for one question, its crops and transcript (a grade hidden for a blind review stays
  hidden). `class_schedule` (a section, optional start and end) reads `GET /api/scheduling/sections/<section>/class-schedule`
  (SCHEDULING "CLASS SCHEDULE", 2026-10-10; the instructor asked for a section's class schedule "to allow the agent to reference
  when needed"): each day's class meeting, what it covered and the work given or due with its time and place; without dates,
  two weeks back to four ahead, which fits the read whole. It reports a `planned` date as planned and gives both dates of a
  `differs` entry without choosing one. A planned date or a topic is a record, never a memory (ASSISTANT-MEMORY): the Assistant
  changes them only through the plan's own routes, each an Approve card (`scheduling.class_plan_save`,
  `scheduling.class_plan_link`; Faculty supplies their request IDs; a plan change is checked by the plan's preview before its card
  is made, SCHEDULING "CLASS SCHEDULE: THE PLAN"). `dr_quizzes` (a term) reads
  `GET /api/scheduling/terms/<term>/dr-quizzes` (SCHEDULING "CLASS SCHEDULE: DR QUIZZES"), read only: the upcoming quizzes to
  preschedule with the DR center and how the imported DR bookings compare. `find_pictures` (a description, optional limit and kinds) reads `GET /api/picture-search` (PICTURE-SEARCH, 2026-10-10): the
  pages and pictures nearest the words (printed pages, bank pictures and notes by Faculty's own text about them; a scanned
  page through the page it was printed from, one result a printed page with its count of scans; what Faculty has no words
  about by look), as image routes for `fetch_files`; with `like` (a file's route, such as a file attached in the chat) it
  finds what looks like that picture instead. It shows nothing itself, nearest is never a
  finding about a picture or a student, and the Assistant looks before saying what one shows. Nothing is stored or changed by a fetch. The cloud-grading decision of 2026-09-19 (no privacy constraints on what models
  see) applies to what the assistant looks at.
- THE TIERS (`assistant/policy.py`, by Flask endpoint name, checked on every request): `read` (previews, searches: run at once;
  since 2026-11-13 `recordings.search`, what was said in kept recordings by its words, RECORDINGS: THE READ TOOL of step 2. It
  writes nothing; one recording, or one stretch of it, is a GET like any other. Filing a recording and naming a speaker are
  unnamed writes, so each is an Approve card, with the request ID Faculty puts there, `REQUEST_IDS`. Since step 3 of
  RECORDINGS, 2026-11-13: what a meeting left the instructor with (a recording's `items`) and the rulings held for an
  assessment are GETs like any other; settling an item, Read again, proposing an item's calendar entry and naming a term's
  coordinator are Approve cards (`recordings.item`, `recordings.read`, `recordings.card`, `recordings.coordinator`); keeping a
  memory from a recording is `never` (`recordings.remember`: the instructor's own press, ASSISTANT-MEMORY "SAID IN A
  RECORDING"). The reading itself raises cards without a conversation: a date said in a meeting is recorded as a waiting
  Approve card by engine `recordings` through `AssistantService.write` with `_proposal_only`, so the Calendar's `auto` rule
  never makes it by itself; an item is what was said, never an instruction to the Assistant. Since step 4 of RECORDINGS,
  2026-11-13: the reading of a student's meeting raises, the same way, ONE card for what was promised
  (`student_support.create` or `student_support.revise`) and ONE for a private note (`student_notes.create`), each an
  ordinary approve-tier write of its own module; setting a recording's students and their parts and proposing one of those
  cards again by the Assistant are Approve cards too, `recordings.students` and `recordings.cards`. Since step 6,
  2026-11-14: `recordings.search` also finds what was said by a description of it (`meaning`; the local embedding worker),
  and Ask the Assistant on a memo's request is `never` (`recordings.ask`: "A dictated request"). Since 2026-11-14
  (RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT"): not keeping a recording, which erases it, and being asked again about
  what was not kept are `never` (`recordings.discard`, `recordings.ask_again`: the instructor's own presses; what Faculty
  holds is their decision), and `recordings.file` no longer takes Not Faculty's, so no card can stand in for either);
  `auto`, the instructor's chosen categories, made at once and logged: Faculty calendar events (create/edit), a section's TA contacts,
  content drafts (Faculty's Canvas copy, including a file the instructor attached in the chat kept and placed in one module of that
  copy: canvas FILES "FROM THE ASSISTANT", requirement, 2026-10-10, "I want the agent to be able to do this for me", who chose one
  card, the push; asking a quiz draft for changes, rubric line COMMENTS only), gradebook setup limited to
  adding/renaming columns and due dates (`_setup_columns_only`: no categories, weights, drops, points, removals or links),
  housekeeping retries of failed work, never on an assignment that was ever certified (grading.md: "Ask before regrading a
  certified plan"; ASSISTANT-CERTIFIED), a follow-up's suggestions prepared again (REDO FROM CHAT) and a student's
  conversations joined into one follow-up or kept separate again (CONNECT A MESSAGE, requirement, 2026-10-10: "joins shouldn't
  be instructor only"); a request that asks for more is `approve`, with the reason. `approve`: EVERY other write route, and every route not named
  (a route added later is safe by default): an Approve card. `page`: flows with item-by-item acknowledgements (certify, Gradescope
  sends that may write to published exams, publishing grades on Gradescope, confirming a seating chart): the assistant opens the page. `never`: sign-in windows,
  connections, Inbox transports, keys and codes, file uploads, compute settings (which hold its own model) and the assistant’s own control routes, except the explicitly listed read and proposal routes below. A new route with a server-checked `confirmation` gets its entry in `APPROVE` (value, preview route,
  fingerprint field).
- APPROVE CARDS. The assistant NEVER supplies a `confirmation` (it is removed from any body it sends); the page's own preview runs
  first, and a refused preview makes no card (the error goes back to the model). The instructor's Approve (`POST
  /api/assistant/actions/<id>/approve` with the card's `seen` event, only from the page) runs the preview again: a changed
  fingerprint refreshes the card and waits for another click; otherwise the ordinary route is submitted with the preview's
  fingerprint and the policy's confirmation, and the outcome recorded. A waiting card may also be previewed again by Faculty
  itself (`preview_again`: the same refresh, never a submission; only ABSENCES "DEPARTMENT MAKEUP EXAMS" › THE TERM'S RULES uses
  it, for the request cards still waiting once one was sent, 2026-11-12). Cards expire after 24 h; decline is recorded; nothing is
  retried by itself. `"$new_request_id"` in a body becomes a fresh UUID. Every route whose handler refuses a write without
  a request ID is listed in `policy.REQUEST_IDS` (with Inbox's start-over): when the model's body has none, or one that is not a
  UUID, Faculty puts a fresh UUID there, so no card fails only at the Approve click (2026-10-07: a regrade Keep card for Question
  20 did, "A decision needs a request ID."; the instructor then asked for every such card). A UUID already there is kept. A route
  added later that needs a request ID joins the list; one that refuses unknown fields and takes none stays off it. `assistant_actions` and `assistant_action_events`
  (WORKSPACE-owned, append-only, nightly catalog backup, not in the generic export) log every write the assistant made or prepared;
  the owning module's own records and receipts are unchanged. Conversations remain ephemeral by default. Optional saving, continuation and retention are specified below.
- FLAG REVIEW RESET (GRADING, 2026-10-25) is an Approve card: the review's own GET reset preview supplies its fingerprint,
  and approval supplies `confirmation: reset`. It restores only the previewed original result references.
- CANVAS ANNOUNCEMENT CARD (requirement, 2026-10-15: the Assistant said it could not post an Exam 2 announcement in their Canvas
  courses; canvas ANNOUNCEMENTS). `canvas.announcement_post` is an Approve card from its preview (`canvas.announcement_preview`, a
  read) with `confirmation="post"`, never automatic; the guide tells the model when an announcement (not a message) is asked for.
  `static/assistant.js canvasAnnouncementCardMarkup` draws it as posted: each Canvas course with its chosen sections, the title,
  the words whole (never the model-sized excerpt), a warning naming any unticked section that shares a course, a section with no
  course, and "Post in N Canvas courses". Its outcome is read from the post receipts (`outcomes.ANNOUNCE`): a course Canvas did
  not answer for makes it uncertain, never "partly". Follow-ups never propose it (BROADCASTS).
- CANVAS MESSAGE CARD (requirement, 2026-10-12, of a card for one message to all four sections that listed every student under
  "What the page's preview says": "it makes an approve card that is extremely long. Can it simply says All Students In 507, All
  Students in 508, etc"; `static/assistant.js canvasMessageCardMarkup`). A waiting `canvas.message_send` card is drawn as the
  message, from the Canvas page's own review: one To line per Canvas course ("All students in 507 (85)"), the subject, the
  words, each attached file, and Send named with the number reached. A line says "All students in" ONLY when the review counts
  every current student of that course's chosen sections among those reached (`roster_reached` equal to `roster`, CANVAS
  MESSAGES); otherwise "82 of 84 students in 508". The names of everyone who gets it are never on the card. Named instead: a
  chosen section's students Canvas does not list in its course and a chosen section with no course (marked lines, as the
  review words them), and everyone left out on purpose folded by reason, open when a chosen section's own student is among
  them. A review made before the counts existed says how many students, never "All". The card's preview keeps the message's
  words whole (never the model-sized excerpt). Display only: the preview, `seen`, the fingerprint (exactly who, in which
  course, how), the confirmation and every server recheck are unchanged, and a refused preview still draws the ordinary card.
  GROUP BY DEFAULT ON THE CARD TOO (requirement, 2026-10-13, of a message to all four sections that went to 339 students one
  by one: "i told the assistant to send a canvas message to all my sections and it again sent it to individual recipients";
  the model had ticked Canvas's "Send an individual message to each recipient" by itself, as on 2026-10-10, and the card said
  so only in its last grey line). That box is the instructor's: the Assistant's `individual: true` stands only when one of
  the instructor's own latest messages in the conversation (at most three) asks for individual or separate messages
  (`service.INDIVIDUAL_ASKED`, their typed words, never a file's name); otherwise Faculty unticks it before the review, so
  the card and the fingerprint are of the group send, and tells the model once (`INDIVIDUAL_REMOVED`). A session without
  their words (a follow-up, a preparation, an outside session) always sends as a group. A card whose box IS ticked says so
  on a warning ("Individual messages, not a group conversation") and on Send itself ("Send 339 individual messages").
  The Canvas page's own Message students is unchanged (CANVAS "MESSAGES TO CHOSEN SECTIONS").
- THE MODEL is `settings["assistant_model"]`/`assistant_reasoning` in Compute settings, resolved by `config.assistant` like the rubric
  reviewer and popped wherever settings are frozen or fingerprinted (batch previews and profiles; a template-reading profile keeps only the verifier's model and backend and the timeout, GRADING "reading profile", requirement, 2026-11-06), so
  choosing it changes no grading fingerprint. Empty = assistant off. Where the instructor is (page, heading, selected text) travels with EACH message (`Conversation.context_note`), since the kept sessions never see a new opening instruction. Engines (`assistant/engines.py`): `ClaudeEngine` (the signed-in
  `claude` binary, `--tools ""`, `--strict-mcp-config` with only the Faculty proxy, `--allowedTools mcp__faculty`, no session
  persistence, no setting sources, plain environment; NOT `--safe-mode`, which also drops MCP), `CodexServerEngine` (instructor, the same day: "is there any way to do the same for codex?": one `codex app-server` kept for the conversation, one EPHEMERAL thread whose developer instructions are the primer, one `turn/start` per message, `turn/interrupt` for Stop; stable protocol only, no experimental dynamic tools; read-only sandbox, `approval_policy="never"` with the Faculty server's `default_tools_approval_mode="approve"`, the grading provider's disabled features plus `view_image` (`code_mode_host` stays on: this Codex routes MCP calls through it), every MCP server of the instructor's own `~/.codex/config.toml` switched off BY NAME at each start (the app server has no `--ignore-user-config`; a `-c mcp_servers={...}` table MERGES, measured: their Cloudflare servers stayed loaded), and any request Codex makes of the client refused). OPENROUTER AND VERCEL RUN THROUGH CODEX TOO (requirement, 2026-09-29: "use the codex harness for vercel and openrouter"; `engines.gateway`, `provider_flags`): the same app-server engine with the gateway as Codex's model provider (`wire_api = "responses"`, the only protocol this Codex speaks to other providers; both gateways serve `/responses`), its bound key passed to the Codex process alone (`config.key_problem` holds it to its own host) and kept from every command Codex runs (`shell_environment_policy` excludes `*KEY*`, `*SECRET*`, `*TOKEN*`; measured in a real coding round: `env | grep -ci key` printed 0). A connection's pinned host (`upstream`) is not applied: Codex cannot add the gateways' routing fields. Verified 2026-09-29 on DeepSeek V4.1 Flash and GPT-6 Luna (OpenRouter) and Qwen 3.8 Flash (Vercel). Other OpenAI-compatible hosts (a custom URL, Nebius) keep `HostedEngine` (Faculty's own tool loop, now only for them over streamed chat completions; `base.assemble_stream` joins `tool_calls` pieces by index,
  a path grading never takes). The session token reaches the proxy through a 0600 file, never the command line. The MCP proxy runs
  one tool call at a time, so a conversation holds at most one web thread. Verified live on 2026-09-29 against a scratch copy with the
  Claude and ChatGPT subscriptions.
  GEMINI SUBSCRIPTION (requirement, 2026-10-14: "now make it work for the assistant too"; `AntigravityEngine`, engine name `gemini`,
  for a connection of kind `antigravity`, GRADING "GEMINI SUBSCRIPTION"): Google's Antigravity CLI (`agy`) kept for the conversation
  like Claude's process, in Faculty's own folder for the program with its plain environment, as the grading provider runs it. Each
  rule below was measured on agy 1.3.4 with invented prompts, a throwaway tool server and an isolated copy of Faculty.
  One process, a turn per message (`--input-format stream-json`; a second turn recalled the first); Stop ends the process and the
  next message starts another with the conversation's words replayed. The primer is the body of an AGENT DEFINITION in the
  conversation's scratch folder (`space/.agents/agents/faculty.md`, run with `--agent faculty`), `tools: [view_file]` on a
  connection that reads pictures and `tools: []` otherwise (the line is always written: without it the program offers all sixty of
  its tools), and the Faculty proxy is that agent's one MCP server (`--engine gemini`, `timeoutSeconds` 600 as for Codex; the
  program's own limit is three minutes). Asked to search the web, run a command and write a file, the model had no such tool. The
  PROXY MARKS FACULTY'S TOOLS ALWAYS LOADED for this engine alone (`_meta` `antigravity/alwaysLoad` on each tool of `tools/list`):
  the program otherwise gives its model only the tools' names, to look up in files, and the model then guessed their arguments;
  marked, all of Faculty's tools were offered directly and called with their own parameters. ONE PERMISSION: a headless run cannot
  ask, so the program refuses an MCP call and ends the turn unless its settings allow it; Faculty's folder allows exactly the
  server named "faculty" (`antigravity.ALLOW`, written by `antigravity.prepare`; nothing else defines such a server). Never
  `--dangerously-skip-permissions`. The session token is in a 0600 file BESIDE the model's folder, never in it: the file viewer
  reads only that folder (the program refused its own folder, where its sign-in is, and anything else outside ended the turn; such a
  turn is said as an error, to ask again). PICTURES: one a tool returns (fetch_files) reaches the model, kept by the program as a
  file in its conversation folder until that is deleted; pictures sent with a turn (a regrade preparation's crops) are files under
  `space/pictures/` the model opens with `view_file`. A SELECTED AREA'S PICTURE IS NOT SENT on this engine: those pixels stay in
  memory and are never staged as files ("Selected-area conversations"), and this program takes a picture only as a file; the
  selection's words and fields go as always and the model is told there was a picture it was not shown. WHAT THE PROGRAM KEEPS: it
  saves every conversation under its HOME; the engine holds the folder's runs lock while its process lives (no sweep removes a
  conversation in use) and deletes the conversation when the process ends: Stop, close, or the conversation resting. It reports a
  conversation's usage so far, so each turn records only its own. The coding agent does NOT run on this connection
  (`config.TOOL_KINDS`; saving it as one is refused and Compute settings do not offer it). Verified on 2026-10-14 against an
  isolated copy holding two invented courses, with Gemini 3.8 Flash and Gemini 4 Argon: Faculty's own `courses` tool called and
  answered from, a second turn that recalled the first, a picture sent with a message read, Stop within seconds, a message after
  Stop answered with the earlier words, and nothing left in the program's folder after closing. `tests/fake_agy.py` holds a
  conversation the same way (`tests/test_antigravity.py`).
- UNTRUSTED CONTENT. The assistant reads student mail, OCR of papers, Canvas pages and other records that can carry instructions
  (prompt injection). The guide tells it such text is data, but the TIERS are the defence: what it can do by itself is limited to
  the low-risk, logged categories above, and everything else waits for the instructor's click. Do not widen `auto` without the
  instructor, and never make an outward action (mail, Canvas, Gradescope, Google, printing) automatic. One exception (instructor,
  2026-10-01: "assistant should be able to change my calendar"): its `auto` edits of Faculty calendar events reach the
  instructor's Google calendar through the automatic sync (calendar.md).
- CHANGING FACULTY ITSELF (requirement, 2026-09-29: "ask the agent about a current thing I'm looking at or something in general ... it
  gathers the necessary information and then passes it to a coding agent"; `assistant/coding.py`, `assistant/coding_prompt.md`,
  migration `assistant/002_code_tasks`, `static/assistant-code.js`, `tests/test_assistant_coding.py`). The assistant never writes
  code: it gathers the page (route, heading, selected text the drawer sends), its data, the source and the AGENTS.md rules, and calls
  `propose_code_change`, an Approve card (`assistant.code_task_start` is an explicitly allowed own route, and only as a card). Asked,
  the instructor chose: the coding agent is a setting (`coding_model`/`coding_reasoning` in Compute settings, a Claude or ChatGPT
  subscription connection, or an OpenRouter or Vercel model run by Codex with the task's gateway kept in its folder's `provider.json`; empty = Claude Code's default model); it may edit AND run commands in its copy; and ONE Apply click merges,
  restarts and pushes. UNLOCKED CODER (2026-10-11, requirement: "unlock the coding agent, let it have full capabilities like regular
  claude code"; until then the Claude coder had nine tools and no settings, plugins, skills or subagents). The Claude coder is
  Claude Code as the instructor runs it: their own settings, plugins and hooks, skills and slash commands, and its tools by name
  (`coding.ALLOWED`): files and the shell, subagents and messages to them, skills, web search and fetch, and the MCP servers the
  repository itself declares (`.mcp.json`: the browser, which may open only the synthetic demo, never the live app). Permission
  checks stay on and a tool that is not named is refused, since nobody is there to answer a prompt; they are never bypassed.
  What reaches past its copy stays shut, because Apply is the one step that touches the live app: `git push`, `git remote add`,
  `systemctl`, `sudo`, `ssh`, `scp`, `rsync` and scheduling agents on the instructor's account are refused (`coding.REFUSED`);
  its environment keeps no credentials; no MCP server or connector of the instructor's account is loaded (`--strict-mcp-config`:
  mail, calendar, hosting, anything that acts as them); sessions are not kept (the home disk is small). With no model set it
  runs the model and effort of the instructor's own Claude Code settings. A plugin the instructor installs for themselves is the
  coder's too from the next task. Codex is unchanged. Read on the real CLI that day, on a throwaway repository: 8 plugins and 95
  skills loaded, the browser connected, a subagent and a web search ran, and `git push` was refused.
  Each task works in its own `git clone --shared` with its origin removed (it cannot reach the live checkout's
  refs), on branch `faculty/code-<id>`, under `<nvme_root>/runtime/code-tasks` (or `FACULTY_CODE_TASKS_DIR`; never TMPDIR), as a
  transient `systemd-run --user` unit so a Faculty restart does not end it, with no service credentials: scratch TMPDIR/DATA_DIR, a
  git config without credential helper, an empty GH_CONFIG_DIR, and Claude's `git push`/`systemctl`/`ssh` refused; Codex runs in its
  workspace-write sandbox (its `.git` is read-only, so Faculty commits what a round leaves uncommitted). Rounds are folders (prompt,
  event stream, exit code) read on request; "Ask for changes" is another round with the earlier reports. Follow-ups, Stop, Discard and
  Apply are the instructor's clicks only. Apply (`confirmation: "apply"`, the exact `head` and `seen` event) refuses while the
  workspace is busy (assistant answers, Inbox jobs, grading tasks, open model requests), rebases onto a moved `main` (a conflict is
  refused, the branch kept), compiles the changed Python and parses the changed JavaScript, fast-forwards `main` in the live checkout
  (uncommitted work of other sessions stays; an overlap refuses), pushes `main` to its upstream and schedules the restart three
  seconds later. A new migration file reaches the live catalog within a minute of the merge (the per-minute timer): the task names it
  before Apply. Verified 2026-09-29 with real Claude Code and Codex runs on a scratch clone.
- RESTARTS AND ANSWERS IN FLIGHT. Restarting the web service ends every assistant answer in progress (its engine processes are in
  the service's cgroup, so nothing runs away). The idle gate before a restart includes `GET /api/assistant/status` `busy` (answers
  running), and Apply waits for it too.
- THE PANEL (`static/assistant.js`): the top bar's Assistant button, Ctrl/⌘ I and the palette's "Ask the assistant" open a drawer
  with Chat (the conversation, each tool call as a quiet line, answers as escaped Markdown, Approve cards inline) and Changes
  (cards waiting, then the recent log). The launcher's badge counts cards waiting. Markdown is escaped first; links are only Faculty
  routes (`#...`) or https in a new tab; no inline styles (CSP). MESSAGE TEXT (2026-10-09) adds forms and math under the same rule.

### Current assistant workspace contract (2026-10-01)

These rules implement the instructor's requested additions and supersede the earlier ephemeral-only conversation rule.
The historical snapshot in `docs/history` is provenance, not instructions.

- **ASSISTANT-APPROVALS.** Every pending approval is returned independently of the paginated history, and counted by the
  badge. A cursor pages only completed/history actions. Existing exact previews, `seen` checks, expiry, confirmations,
  immutable receipts and uncertain-outcome handling remain. No tool can approve, decline, answer an instructor question,
  change retention, edit memories, or operate the coding task's follow-up/Apply controls.
- **ASSISTANT-CONTINUATION.** An in-app conversation remembers the actions it proposed. Once all are resolved, their actual
  retained receipts continue the original task, including verification reads. Busy turns finish first; stopped, paused and
  completed tasks never resume automatically. Never automatically retry failed, declined, expired or uncertain writes.
  After a restart, saved tasks resume when opened; model processes and credentials are never persisted with them.
- **ASSISTANT-RETENTION.** Saving a conversation/task is opt-in in the Saved tab. Title, goal, transcript, tool evidence,
  pending action IDs and unanswered clarifications are workspace catalog metadata. Keep for 7, 30, 90 or 365 days after
  activity; allow active/paused/completed, explicit deletion, and expiration. Snapshots keep the latest 80 messages (up to
  750,000 characters) and up to 2 MB of recent events; the Saved form states the bounds. Deletion removes the live saved copy, not
  immutable action receipts or prior backups. Preferences are the workspace's memories
  (ASSISTANT-MEMORY, 2026-10-10, requirement: "Automatic, with Undo"): instructor-editable and removable, with revision
  checks; kept from the instructor's own typed words only (and, with them, the pictures attached to that message:
  ASSISTANT-MEMORY "PICTURES"; a fragment may then be reworded to stand alone, with their words kept beside it: "THE REWRITE
  STEP"; a general rule typed about one student's case is kept only as the rule written without the student: "A RULE SAID
  OF ONE STUDENT'S CASE"; and a rule the Assistant writes from a correction they made of a follow-up's card and approved:
  "LEARNED FROM AN EDIT", 2026-10-12), each said where it happened with Undo, never silently. What they write by hand is checked as a chat message is ("EVERY
  WAY IN"). Preferences cannot
  override the tool policy. Ephemeral conversations retain
  their existing resting/replay behavior.
- **ASSISTANT-EVIDENCE.** Source metadata comes from actual tool reads, never a model's claim: route, observation time,
  SHA-256 and available source revision/certification metadata. File reads include their content hash. Show these with the
  tool results (in chat, once per turn in its fold: QUIET STEPS) and preserve them in saved conversations. Observed data is not
  a claim of currentness: current grades and historical certifications must remain distinguishable. File showing/looking,
  labels and student-paper crops above remain.
- **QUIET STEPS** (2026-10-08, requirement, of the "→ Read …" and "Sources · 1" lines between a question and its answer:
  "Do you think it would be a good idea to remove all of these intermediate steps ... I haven't needed it so far"; then "Yes"
  to one closed line per answer, a quieter evidence check and no re-reading of the guide). In chat, a read on the way to an
  answer and the evidence check's status line stay out of the conversation: while the assistant works, its finished steps are
  listed over the line it is on (LIVE STEPS, 2026-10-09; before that, one line named its latest step); once it has answered, the
  turn's steps, its evidence check (ASSISTANT-JEV) and one list of the sources it read (deduplicated by kind, path, fingerprint
  and observation time) share one closed "How it got this · N steps · S s" under the answer. The memories the turn's
  requests were given are listed in the same fold, which then also counts them ("N memories"; ASSISTANT-MEMORY "MEMORIES
  GIVEN", 2026-10-10): a turn with no steps, check or sources but with memories given has the fold for them.
  Pages it opens, files it shows, Approve cards, failed steps, stops and errors stay in the conversation. Only a paragraph Jev
  confidently calls contradicted raises a warning outside the fold, quoting it (TEACHING-WORKFLOWS EVIDENCE WARNING; measured
  before the change: all 14 chat checks since 2026-10-03 had shown "Claims to review", none for a contradiction, 35 of 49
  paragraphs below Jev's .8 confidence). The primer is in the system prompt whole, so `read_docs` does not list
  `assistant/guide.md`, a read of it returns a note, and that note is not evidence (DOCS-RETRIEVAL).
- **ASSISTANT-WATCHES.** Allow explicitly approved watches for a specific scan, grading plan or exam. Run bounded turns in
  the existing fair worker, at least two minutes apart; no new model calls, grading, sending or automatic changes. Store
  baselines and notifications in the catalog. Notify only on requested meaningful changes, failure or recovery; timestamps
  and heartbeat changes are not progress. Deleted Inbox items are not completed scans. The instructor can pause/remove
  watches and mark notices read. New targets/conditions require an explicit rule and tests.
- **ASSISTANT-ARTIFACTS.** A generated file has exactly one permanent owner: course definition, course term or section.
  Use the existing verified storage contexts; include the recorded digest in backup verification. Never retain teaching
  content in scratch. An approval shows the destination and full text before creating an immutable new Markdown, text,
  CSV, JSON or plain-paragraph PDF file. Use request IDs, exact content fingerprints and download routes. This grants no
  permission to replace existing files, publish, send mail, change a grade or execute generated code.
- **ASSISTANT-QUESTIONS.** `ask_question` is shared by all in-app engines and exposed to MCP clients. Ask one to three
  bounded questions, optionally with choices and always allowing free text (choosing a choice sends it once every question of
  the form is answered, 2026-10-04; ONE-CLICK ANSWERS, TEACHING-WORKFLOWS). End the turn and wait; an answer continues
  the original task. Answers are instructor input, not approvals. Outside MCP clients display and handle questions in
  their own UI. Keep unknown/native client approval requests refused.
- **ASSISTANT-BATCHES.** Group 2–20 already-prepared exact actions from one conversation. The instructor selects individual
  cards to approve jointly. Preflight every selected card (version, expiry, route, preview) before submitting any; refresh
  stale previews and require review again. Submit in displayed order through the ordinary approval path; stop on a stale
  preview or failure, keep per-item receipts, leave the rest pending. No rollback claim, automatic retry or blanket approval.
  A Mail send whose delivery is not confirmed (Mail's outcome is not `accepted`) also stops the run (2026-10-03, with
  ASSISTANT-FOLLOWUP-SUGGESTIONS' Approve all, which uses the same preflight and order). ALREADY APPROVED (2026-10-06): a
  selected card that already went through (succeeded; a Mail send only once Mail accepted it) is not "changed or expired". When
  every selected card did, the answer is "Already approved. Nothing was submitted again." with the cards as `already`; when only
  some did, nothing is submitted and the refusal says how many were already approved. A failed, uncertain or still-submitting
  card refuses the selection as before.
- **APPROVALS SIDE BY SIDE** (2026-10-31; the instructor: "it takes a long time after ive approved a followup for it to complete
  ... see if there is a reliable way to parallelize them so I can approve multiple followups at while others are finalizing ... we
  have to be careful so that followups being finalized don't get corrupted by ones that I approve while they are finalizing").
  Measured on the live catalog the evening before: one student's Approve all took 9-16 s on the server, and each approval held
  the one approval lock (`_approve_lock`) from its first check to its last bookkeeping, so every other Approve, and a regrade
  decision's 25-45 s, waited for all of it. The gaps between a follow-up's steps (5-9 s after a 0.1 s absence record, before a step
  with no preview at all) were waits, not work: each step checks its follow-up several times, and every check took the
  lifecycle lock, which the Inbox worker held about 15 % of the time in turns of up to 3 s. Now (`assistant/lanes.py`):
  - An approval holds its LANE throughout: a follow-up's card its student's lane, so one student's follow-ups still finalize one
    at a time and in order; a card of another route, and every card that is not a follow-up's (chat cards, batches,
    preparations), also the one `serial` lane, so those still go one at a time among themselves as before. Only routes that write
    their own student's records, each in its own transaction under the lifecycle lock with a request ID applied once, run beside
    another student's (`PARALLEL`: Mail send, which also keeps Mail's own send lock; absence record, revision and makeup hold;
    follow-up tracking; private note). A regrade decision, a department makeup request, Canvas, the gradebook and any new route
    stay `serial` until they are shown safe and added.
  - The approval lock is held only while a card is checked and while "approved" is recorded. The card is checked again then:
    still waiting, the version the instructor saw (a Decline, expiry, edit or Mail replacement during its preview stands, and a
    new preview is recorded only on a card still waiting as it was), its follow-up unchanged and its preceding steps confirmed.
    Previews, the submission and what follows it run outside it. Exact previews, fingerprints, confirmations, receipts, the
    stop at an unconfirmed send and never resending stay as they were.
  - A lane is taken before the approval lock, and an approval never asks for a lane it does not already hold (Approve all takes
    all its cards' lanes at once, in one order), so two approvals can never wait for each other.
  - Checking an unchanged follow-up reads it without the lifecycle lock; a change to record takes the lock and reads again.
  - An approval that takes more than 5 s says in the log where its time went (waiting for its lane, preview, checks,
    submission, what followed).
  ONE CARD FOR ONE CHANGE (2026-10-09, requirement, of one grade change the assistant prepared for 119 students: "Instead of
  sending one summary card for everyone (since it's the same action for each student), it sent 119 individual approval cards";
  the model had grouped them, 20 at most, and the floating chat drew no groups). Cards of ONE route, the same change for each
  record, group up to 200 (different changes stay at 2–20), and a card named again moves to the new group, so groups made in
  parts join. Such a group is ONE card wherever its cards are drawn (chat, the floating chat's panel since CHANGES BESIDE THE
  CHAT, Changes): its title, count and states,
  each change on one line with Leave out (that card's own Decline), what the first one sends, Approve all and Decline all.
  Approve all approves exactly the waiting cards the card lists, with the same preflight, order, stop at the first problem and
  per-card receipts as above; it approves nothing prepared later. Decline all declines only the cards still waiting. The
  `group_actions` description and the guide tell the model to make every card, then group the same change for many records in
  one call. `/api/assistant/actions` also lists groups made in the last day (a finished group stays one card in the chat), each
  with `route` (the shared endpoint, else null), `states` and `pending`; `POST /api/assistant/batches/<id>/decline` is
  instructor-only, like the batch approval. A tool that makes the same change for several records itself groups its own
  receipts, done at once or waiting (SEVERAL AT ONCE: follow-ups a chat prepared again).
  The receipts that continue the task (ASSISTANT-CONTINUATION) are shortened past 60,000
  characters to each card's outcome, HTTP status and error, and the model is told so: 60 grade changes' whole page answers
  overflowed the 200,000-character continuation and ended the task with an error.
- **ASSISTANT-CERTIFIED.** Housekeeping includes preparation and scan retries. Resolve preparation ownership and a scan's
  historical explicit associations and identified papers before classifying; any ever-certified associated grading plan
  makes the retry an approval. Unknown/invalid identifiers are still checked by the ordinary route. This does not broaden
  any other automatic-write category.
- **DOCS-RETRIEVAL.** `read_docs` and `read_source` return bounded pages with a fingerprint and continuation position. Positions
  describe the text actually returned, including long source lines; a changed fingerprint refuses continuation. The root
  AGENTS.md is the invariant/index document; module decisions are read on demand. Historical snapshots are not active rules.
  The assistant's primer, `assistant/guide.md`, is given whole in its instructions: it is not listed, and a read of it
  returns a note instead of its pages (QUIET STEPS, 2026-10-08).

Allowed own routes: coding-task reads and proposals; watch/artifact lists, previews and proposals; preferences read-only;
and its own past work found by situation (`find_past_work`, PAST-WORK, 2026-10-10: the instructor's "build it"); the action
log's own route stays instructor-only.
Watch and artifact creation each requires an Approve card. All remaining assistant control routes are instructor-only.

- **ASSISTANT-FOLLOWUP-SUGGESTIONS** (2026-10-03, requirement's approved automatic follow-up plan). When an Assistant model is
  configured, prepare the current eligible follow-up queue and new source states automatically with that model/reasoning.
  The fair Inbox worker queues one enrollment-owned source state per turn; only the web process's exclusive dispatcher runs
  an engine, with one background turn at a time and interactive conversations taking priority at dispatch. Existing engines,
  tool limits and verification apply. Background answers count in Assistant `busy` and the restart/Apply gates.
  A server-owned proposal-only session promotes every permitted write to `approve`, including chat's normal `auto` routes,
  except the record-keeping DONE FOR YOU names below;
  `page` and `never` remain intact. WIDE SCOPE (2026-10-03, requirement: suggest "any change to the app ... for me to
  approve"; asked, they chose anyone's records including class-wide, messages to the student, their TAs and themselves, and
  code changes allowed): a suggestion may propose any change the assistant may prepare, for any student or a whole class,
  including grading, rubric, gradebook and absence decisions and a coding-agent handoff, every one an approval card; a card
  not limited to the follow-up's student, or touching a certified assessment, says so; one that changes another student's
  record also shows the Assistant's note (why) on its face (2026-10-15). Deliveries reach only the follow-up's
  student (fresh verified roster address), their current sections' TAs (latest TA list) and the instructor's connected
  address; another student's address is refused; routes that reach whole sections or other people (Canvas section messages,
  seating and Response Card mail, print-export email) are refused from a follow-up. Mail that includes the student attaches
  only course material or their own work, or a file the instructor attached in the chat (ATTACH IN CHAT) whose route their own
  request for this follow-up names (Ask for changes, an answer, or the chat's request to prepare it again; instructor,
  2026-10-20: the filled-in notes they attached for the follow-ups that asked for them were refused); to TAs and the instructor
  alone, any file Mail accepts. A regrade reply only answers
  the student's own request. A GRADESCOPE REGRADE REQUEST (REGRADES "FOLLOW-UP", 2026-10-07) is prepared with its facts in the
  prompt (`grading/regrade_followup.py`: the question, answer key and solutions, every rubric line with its points, whether it is
  applied, the clarification and exclusions of the finalization it was graded under and the instructor's line comments, the
  grade, who decided it, where it is (Gradescope, certified, gradebook, Canvas), the reading, the class's results on the
  question and how other requests on it were answered) and its crops ATTACHED TO THE TURN (`Conversation.send(images=...)`:
  Claude image blocks, Codex `localImage` files in the session's scratch folder, hosted `image_url` parts; a hosted engine keeps
  the words and drops the pictures on later turns; verified live 2026-10-07 with Codex gpt-6-astra and Claude Opus 5.5 on a
  synthetic picture; only to a connection whose modality is vision: a text-only Assistant model gets none and is told it could not
  see the work). It is answered with ONE `frq_regrade_panel.decide` card (approve, preview
  `decide_preview`, confirmation "decide"): keep or change (the full set of rubric lines, an adjustment only when no line fits; the grade's own adjustments stay, REGRADES KEPT ADJUSTMENTS),
  the reply and one sentence of why; approving it saves a change through the ordinary review and runs the same Apply as Return's.
  A request on a bubble-sheet question (REGRADES "BUBBLE SHEETS", 2026-10-28) is told Faculty's reading, the key and its scoring
  rule, the sheet's total and how the class answered, sees the question's row and the version bubbles, and its card's change names
  `answer` (the letters filled), never lines or an adjustment.
  `frq_regrades.reply` and a Mail reply are refused for such a conversation. Clarifications and comments are the instructor's
  internal guidance: the reply never quotes them. Waiting on student is submitted only after a confirmed reply (proposed beside the reply card), a
  makeup seat names a real sitting or is made with it (ONE ROUND).
  Source identities, exact conversation revisions/digests, follow-up revision, active memberships and tracking cutoff bind
  every run. Recheck before preparation, approval, batch preflight and after a preview; changes expire unsubmitted cards.
  Failed/interrupted/declined/expired/uncertain work never automatically repeats. ONE ROUND (2026-10-04, requirement: "shouldn't
  everything be suggested the first time"; TEACHING-WORKFLOWS): a preparation proposes everything at once and nothing is prepared
  from approved cards' receipts; work that needs another card's new record goes in that card's own request (ABSENCES WITH
  DOCUMENTS, EXAMS WITH PARTICIPANTS); a confirmed receipt means Mail's accepted outcome rather than HTTP success, checked again
  just before each card is submitted. Exact duplicate submitted actions are refused.
  Instructor-only revision/retry/clarification routes require request IDs, the displayed run event and source fingerprint.
  Ask for changes replaces only unsubmitted proposals; completed receipts remain. Clarification answers supply information,
  never approval. A plain Retry means prepare again with current sources, latest saved term policies and retained instructor
  guidance; an empty retry instruction is intentional and does not require the instructor to invent revision notes. Existing
  workspace-owned action events marked `replaced_by_request` describe superseded unsubmitted proposals, not instructor
  rejections. The model's receipt projection distinguishes them from actual declines, including older replacement events;
  new replacement events describe Retry, revision or clarification accurately. Completed/uncertain work is never resent.
  Inline follow-up cards and Changes use the same Assistant action IDs, shared card renderer and handlers,
  including Mail's complete preview, attachment links, advisory checks and explicit Send. Other actions use Approve/Decline.
  Enrollment-owned migration `student_support/004_suggestions` retains bounded review explanations, source metadata,
  clarifications, instructor feedback and existing action references; no automatic chat transcript or copied message bodies.
  Communications and action receipts keep their current owners. Follow-up reads opt in with `suggestions=1`; defaults remain
  byte-identical. Missing model configuration shows the existing Compute settings guidance. See TEACHING-WORKFLOWS and CHECKS.md.
  FOLLOW-UP REVIEW (2026-10-03, requirement asked to fix the review's bugs and build every feature it suggested):
  OWN WRITES: a run is withdrawn only by what its proposals answer changing (TEACHING-WORKFLOWS); its own approved reply and
  tracking change never withdraw the cards beside them. A withdrawn (changed source) or expired card was never the instructor's
  decision: a fresh preparation may propose it again; only a card the instructor declined stays refused outside Ask for changes,
  answers and retries. Every instruction and decline note given for a follow-up reaches each later preparation of it, across
  new messages. A start that fails is recorded as failed, and a run left `running` with no engine active in the dispatcher's
  process is marked interrupted, so neither blocks the queue (retry stays explicit). Dispatch order: Prepare now, then the
  instructor's own requests (and receipt continuations recorded before ONE ROUND), then the automatic queue in the follow-up list's order (possible
  wellbeing concern, then urgency). The instructor's controls (`approve`, `replace`, `edit`, `note`, `prepare`, `mute`, `pause` under
  `/api/student-support/followups/suggestions/`) are NEVER routes for the assistant: Approve all is the instructor's click on
  the cards they reviewed, labelled "Send and approve all" when it sends; Edit in Mail opens Mail's own compose window and only
  the instructor's Send there links the send to the card (declined as replaced, counted as this follow-up's reply). Remember
  saves an ordinary Assistant preference. The prompt asks the model to read `/api/calendar` before proposing a time, and a
  waiting meeting card shows the calendar as it is when read (overlaps, office-hours blocks). ANALYSIS FIRST: automatic
  preparation waits up to 10 minutes for Jev's conversation analysis of the exact state (TEACHING-WORKFLOWS). TERM POLICIES:
  each preparation carries the term's policies as the instructor's guidance and records their revision (TEACHING-WORKFLOWS).
  FOLLOW-UP REVIEW LAYOUT (2026-10-03, requirement's simplification request): the shared suggestion renderer puts full draft
  previews and explicit action controls first. Narration and advisory check details share a closed disclosure with one
  deduplicated observed-source list; limitations remain visible beside the drafts. Compact headings omit repeated approval
  boilerplate and internal route/confirmation copy while preserving wider-scope and certified-work reasons. The Follow-up
  page's queue, exact captured message and retained unsent forms follow TEACHING-WORKFLOWS.
  FOLLOW-UP CLARITY (2026-10-03, requirement: "It's not clear what actions the agent will take in its suggestions"): a waiting
  follow-up card other than mail and a coding handoff is drawn from the server's plain reading of its exact request (`effect`,
  TEACHING-WORKFLOWS): title, page, whom it is for, each field it sets, what follows and who is told, with an Approve button
  named for the action; mail cards take the same title, step number and audience line beside Mail's complete preview. The
  reading is display only: approval, previews, `seen`, confirmations and every server recheck are unchanged, and the exact
  request remains on the card. The wider-scope note is not added to a proposal that changes a record of the follow-up's own
  student named by ID (a follow-up item, or an absence by any absence route, its documents included); a card naming another
  student or a whole class still says so.
  REPLY VOICE (2026-10-04, requirement, of a suggested reply: "not very natural sounding, it also recites student rule 7 like a
  robot instead of simply asking for the documentation"): every mail the assistant drafts to a student, in a follow-up or in
  chat, reads like the instructor's own replies ("Hi <first name>,", a few plain sentences, "Best," and "Chris"); it answers
  first and asks plainly for what is needed. Term policies and Faculty's rules decide what a reply asks for, offers or
  declines; a reply never quotes, cites or paraphrases them unless the student asked what a rule is, and leaves out process
  and bookkeeping (assistant/guide.md "Mail to a student sounds like the instructor"; the follow-up prompt says the same).
  FOLLOW-UP FACTS (2026-10-04, requirement: the retried draft asked which day the student missed "but he already said he missed
  the quiz which has a given on date"): a reply never asks the student for what Faculty or their message already shows (which
  quiz, which day, their section); a policy's "confirm"/"verify" means check Faculty. Each follow-up preparation's prompt states,
  from the catalog, when the student last wrote (campus wall clock) and the assessments given to their current sections from
  three weeks before to a week after (EXAMS "DATE GIVEN" without a later makeup; whether their paper came back). ICLICKER AFTER
  CLASS (2026-10-06, requirement: iClicker imported after each class "so that follow-up has more accurate information to go on"):
  it also states each class day of their sections in that window up to today, from the Term schedule and the after-class
  imports (iclicker.md AFTER CLASS): the iClicker sessions given and whether they answered, "no iClicker given", or "not checked
  yet", saying that a missed iClicker is excused only with documentation. ONE ICLICKER FOR EVERY SECTION (requirement, 2026-10-09:
  "if I do a clicker, I will do it for all classes, so if any of them have imported a new iclicker for the day, then it can assume
  we've had one"): a day the student's section has not read yet takes the term's other linked sections that day: a session
  imported there means one is expected here; their finished imports with none mean none is expected. Catalog only: preparation
  never signs in to iClicker (the after-class imports already read it; credentials stay with the import). ICLICKER CREDIT
  BEFORE THE IMPORT (requirement, 2026-10-21, of in-class iClicker trouble emailed before class ends that became a To do because
  the day was not imported yet; iclicker.md CREDIT BEFORE THE IMPORT): the iClicker facts also give the credit route with the
  student's IDs and the credits already given them; when the student was in class but could not answer and the term policies
  give credit for what they sent, the preparation proposes that credit (an Approve card that applies at once or by itself at
  the day's import), never a To do or a commitment to check the import later. CLASS SCHEDULE
  (2026-10-10, requirement asked that follow-ups read it; SCHEDULING "CLASS SCHEDULE"): it also states, from the student's
  section's class schedule (`_schedule_facts`, the read the page and the `class_schedule` tool make), each day the student
  named in their last six messages (`jev.dates.mentions`, at most six, within 60 days of when they wrote): the class meeting or
  that it was a day off or no class day, what it covered, and what was given or due; what was due from three days before to
  three after those days; and what is given or due in the next two weeks. A planned date is said to be planned, and a date
  Faculty's records disagree about is never to be stated as settled. At most 25 lines. An absence card's note names the sessions an excuse
  gives full credit for when they are known (`FollowupEffects._iclicker_given`). SYNC WITH CANVAS (canvas.md): the summary
  (`gradebook.canvas_sync_preview`) is a read; Sync (`gradebook.canvas_sync`) is an Approve card with `confirmation="sync"`, never
  carries `publish` (refused for the assistant actor) and carries the posting steps its summary showed. POSTING GRADES (canvas.md;
  requirement, 2026-10-08: "Assistant should be allowed", then chose an Approve card): a column's standing Post / Hide / back to its
  category's rule (`gradebook.canvas_posting_choice`) is an Approve card with `confirmation="choose"`; nothing reaches Canvas until
  a sync. A category's posting rule is a setup change (`gradebook.save_setup` with categories: Approve).
  A CANVAS QUIZ'S GRADE (canvas.md ONE CELL; requirement, 2026-10-13, of a request to give one student 2/2 on a video quiz that a
  follow-up could answer only with a Change Faculty card): `gradebook.canvas_quiz_grade` is an Approve card with its preview
  (`gradebook.canvas_quiz_grade_preview`, a read) and `confirmation="set-quiz-grade"`, never automatic; the primer names it
  under Changing things. Its card reads "Change the grade of “…” to 2 / 2 in Canvas" on Canvas, the new grade beside what
  Canvas showed (`FollowupEffects._gradebook_canvas_quiz_grade`), button "Set grade in Canvas". In a follow-up it is a step
  before the reply, and its answer's `status` (accepted, failed, uncertain) is what PLAN COMPLETION reads: a reply that
  confirms the grade waits for accepted.
  DOCUMENTATION ONLY FOR MISSED WORK (2026-10-05, requirement, of a redraft that still asked a student for her appointment type
  and documentation after they chose "no quiz Friday": "I only require documentation if there was an assignment missed"): a
  drafted reply asks for documentation of an absence, or what it was for, only when the absence misses graded work given or due
  in class that day (a quiz, an exam, work handed in, or an iClicker). When it misses none, the reply says so and asks for nothing
  more. The rule narrows the term policies' documentation requirements (follow-up prompt; guide "Mail to a student sounds like the
  instructor"). ICLICKER NEEDS A NOTE (2026-10-09, requirement, of Kira Lowell's reply, which said there was no quiz to make up but
  "completely ignored the iclicker part of her question, it would need a doctor's note to excuse it"; replacing an earlier
  unconfirmed reading that an iClicker alone never counted): a missed iClicker is excused only with documentation (a doctor's note
  for an illness), and the reply says so whenever the absence misses one or may; every part of the student's message is answered.
  EXCUSE NEEDS EVIDENCE (2026-10-08, requirement, of a To do round that proposed excusing a student's fever "because she missed
  no graded work and documentation is not required": "this should not be suggesting the student's word is sufficient for an
  excuse"): the student's own account of an absence (or a relative's) never excuses it, and asking for no documentation never
  does either. A follow-up proposes Excused only for an absence with documentation that stands (or a create that brings the
  student's files) or on the instructor's word; otherwise the absence waits for their decision, with no decision proposed or
  offered as an assumption or alternative, and a reply never says it is excused or that a grade will be adjusted for it (follow-up
  prompt; guide). Server: `FollowupSuggestions._excuse_evidence` refuses such a proposal unless the absence is already excused,
  has documents that stand, or the instructor has given this follow-up an instruction or answer (the prompt narrows that to
  their telling it to excuse). Approving a card is the instructor's own decision and is not refused; neither is Excused chosen
  by them from the card's dropdown (DECISION ON THE CARD). A kept memory never stands in for that evidence or that word: the
  server check reads none, whatever a rule learned from an earlier decision says (ASSISTANT-MEMORY "LEARNED FROM AN EDIT").
  NOT EXCUSED WHEN NOTHING COULD EXCUSE IT (2026-11-13, requirement, of a follow-up card for a student missing class for a BIOL 111
  exam on West Campus, left Waiting for your decision: "I had to manually change this to not excused. It seems like the agent
  defaults to waiting for me even if the absence is clearly not excused"; asked how far that reaches, they chose the reason itself
  — "Reason can't be excused" — and that it stays a card they press, not a record made by itself): a follow-up proposes Not excused
  when the reason the student gives is outside the ones Student Rule 7 excuses, so no documentation could change it (another
  course's class, exam, lab or assignment, work, a trip, traffic, oversleeping, a social or family occasion that is no death or
  major illness), with a short `decision_note` naming the reason they gave; the card stands on that decision and its dropdown still
  offers the others (DECISION ON THE CARD). Such a reply asks for no documentation, whatever the absence missed. Rule 7's own
  reasons (illness, a death or major illness in the immediate family, an ill dependent, a university-authorized activity, a
  religious holy day, a legal or administrative proceeding, military duty, a graduate or professional school interview, pregnancy),
  a death (DEATHS WAIT) and any absence the student sent files for wait for the instructor's decision, documented or not: EXCUSE
  NEEDS EVIDENCE is untouched, and nothing proposes a decision for them. Server: `FollowupSuggestions._not_excused_clear` refuses
  such a proposal unless the absence's reason is `other` with no death and no documentation that stands, or the instructor has
  given this follow-up an instruction or answer; their own approval and their own choice on the dropdown are never refused. A
  proposed decision is never recorded by itself: `_bounds` already keeps any decision off DONE FOR YOU, so it is a card
  (ABSENCES "RECORDED BY THE ASSISTANT"). A reply never tells the student an absence is excused or not excused before it is.
  A LINKED FILE COUNTS ONCE FETCHED (2026-10-11, requirement, of a second doctor's note sent as a Google Drive link, which left
  the absence waiting and had the reply ask for the note again: "it should have proposed approved because a doctor's note is
  attached"; then "fetch the file"): Faculty fetches a file a student links from Google Drive and lists it with the message's
  attachments (COMMUNICATIONS "LINKED DRIVE FILES"), where the preparation reads and uses it like any file they sent,
  documentation included. A link that was not read is never documentation. The reply asks the student to attach the file itself
  only when that is theirs to settle (not shared with the instructor, too large, a folder); when Faculty may not read Drive yet
  or Drive did not answer, it asks them for nothing about it, leaves the absence waiting and says so in its summary (follow-up
  prompt; guide).
  MAKEUP TIMES (2026-10-04, requirement, of a drafted reply that offered one Tuesday office-hours time for a Quiz 4 makeup and
  said "I'll follow up to confirm the arrangements": "why is this reply only offering Tuesday ... why is it saying I will follow
  up when she is the one that needs to follow up with her date?"; asked, they chose all three fixes): (1) for each quiz in
  those facts with no paper from the student (at most three), the prompt lists, while the term's booking cards are off, every
  open time the booking card would offer (`MakeupBooking.times`, ABSENCES MAKEUP BOOKING, bounded by the absence over that day
  when one is recorded), and the reply names them all and asks the student to pick; the follow-up then waits on their pick.
  A meeting-hours time (the Tuesday afternoon until 2026-10-05) goes to the model as the window its open starts make ("a time they choose between 1:00 pm and 5:00
  pm", split where a calendar entry took starts), never as half-hour slots (instructor, of the next draft's slot list: "very
  unnatural ... in an email it should just ask for a time between 1PM to 5PM"; the card keeps its slots). The reply asks which
  time the student will take, never whether any of them works (instructor: "it should more or less ask which of these times
  work so that the student doesn't automatically feel invited to reject these proposals"). A quiz whose absence's makeups are
  held (ABSENCES HOLDS, 2026-10-06) is listed as held, with what it waits on: no time and no card, cards on or off, and a reply
  that touches it says the makeup is set up once that is settled. A quiz whose booking card was withdrawn (sent or not; 2026-10-06)
  is listed with its open times even while the cards are on, and the reply offers them all as with the cards off.
  Students' appointments and events titled office hours or makeup never shorten the window (ABSENCES "SHAREABLE").
  ANOTHER TIME (2026-11-12, requirement: "when the students put "need another time" on the makeup cards, it should actually land
  back in followup for me to review"; ABSENCES "MAKEUP BOOKING" › BACK TO FOLLOW-UP opens the follow-up): a quiz whose booking
  card the student answered "None of these work: I need another time" is listed, cards on or off, with their words, the days the
  card offered, the due date and the open times after the card's days (`MakeupBooking.times`, as "Send a new card" counts them),
  never as a card to point them to (it cannot be answered again; until this rule the line read "point them to it"). When they
  name a time that the calendar shows open (nothing timed but other makeups) and that is not after the due date, the preparation
  proposes the makeup sitting at it with them seated and a reply confirming it; when it is taken, they named none or their words
  are unclear, the reply offers those open times and asks which works best. Both are cards (a makeup sitting is never DONE FOR
  YOU). The follow-up Faculty opens for the answer has no message: its FOLLOW-UP FACTS are read around the moment they answered
  and always include that quiz (`_card_answers`); any other follow-up without a message has no facts, as before (follow-up
  prompt, the "Quiz makeups" facts, guide).
  MEETING HOURS (2026-10-05, requirement, of a drafted reply asking a student whose classes conflict with office hours to "send a
  few other times you're available this week": "for this semester, I want to only allow scheduled meetings on tuesdays or
  thursdays from 1PM-5PM ... so that it doesn't carry through to every semester"): when the student's term has meeting hours
  (TEACHING-WORKFLOWS), the facts state them and list their open times from now (at least 30 minutes ahead, from the next half hour) over the next 14 days, at
  most 4 days, each day's room and windows ("any time between 1:00 pm and 2:00 pm, or between 3:00 pm and 5:00 pm"): a timed
  calendar entry takes its time except the instructor's own office-hours blocks and another student's appointment (meetings
  overlap one another: SCHEDULING SHARING, 2026-10-06); a makeup takes it (MAKEUPS AND MEETINGS). The prompt says a meeting outside office hours is offered only inside them, naming the open times and asking which works
  best, never asking for the student's general availability, never a time outside them; with none open it says so and offers
  office hours; a calendar that cannot be read says to read it. A term without meeting hours gets none of this (the term policies
  decide). DONE FOR YOU (below) puts a meeting on the calendar by itself only inside them while the term has them.
  MEETING LEAD (2026-10-22, requirement, of a reply prepared at 1:18 pm that offered a student only 4:30–5 of that Thursday's
  1–5 pm: "why was he only offered 4:30 to 5"): a meeting prints nothing, so its open times never wait the makeup's 3-hour lead
  (ABSENCES, for printing a copy); today's start at the next half hour at least 30 minutes away (`MEETING_LEAD`).
  THE STUDENT'S TIME (2026-10-05, requirement, of a reply to a student who asked to come "this Tuesday after 2:30" that said
  "Yes, I can meet Tuesday ... after 2:30" and then listed four days of open windows, with no appointment: "why isn't this
  suggesting to put Kenna on my schedule for appointment office hours tomorrow?"): when the student asks for a time that is
  open (in office hours or the open meeting times, overlapping nothing but other appointments), the preparation takes it, the start of what they name
  ("after 2:30" is 2:30): an appointment at that time (agreed, so DONE FOR YOU may put it on the calendar within its checks) and
  a reply confirming it, with no other times listed. Open times are offered only when theirs is not open or they named none
  (follow-up prompt, the meeting-hours facts, guide "Mail to a student sounds like the instructor").
  MEETING LENGTH (2026-10-06, requirement: "make the default meeting length 30 minutes"): a meeting the assistant proposes, in a
  follow-up or in chat, lasts 30 minutes unless the student asks for another length or the instructor names one (follow-up
  prompt, meeting-hours facts, guide). DONE FOR YOU's bound (an hour or less) is unchanged. LENGTH UNSAID (2026-10-06,
  instructor, of a reply asking "What start time between 1 and 1:50 PM ... works best for a 30-minute meeting?": "we shouldn't
  say that to the student. That rule is just for scheduling purposes, it's fine if they go over or under that when they
  actually show up"): the length sets only the appointment's end_time, which ends sooner when its open time does; a reply
  never states how long a meeting lasts and names the open times as listed, never narrowed to leave room for it.
  MEETINGS ARE APPOINTMENTS (2026-10-06, requirement, of a "Meeting with Kenna Walsh" section calendar event a follow-up
  proposed for the time the student asked for: "make follow-ups use appointments for meetings"): a meeting with the follow-up's
  student is an office-hours appointment (TEACHING-WORKFLOWS), never a calendar event. A follow-up's calendar event create or
  update whose title reads as a meeting (meet, meeting, appointment, one-on-one, 1:1) or whose title or notes name the
  student (a word of three letters or more of their roster name) is refused, telling the model the appointment route, so the
  card it proposes is the student's own record, eligible for DONE FOR YOU, and reaches the calendar and Google as an
  Office hours event with their name (CALENDAR "OFFICE HOURS"). Other calendar events (a class-wide fix) stay WIDE SCOPE cards.
  ONLY FACULTY BOOKS (2026-10-06, requirement, of a reply confirming a student's pick of "1:30" with no appointment, because a
  Google event "Office hours: <the student> (MATH 140-515)" made outside Faculty held her window and the preparation called an
  appointment a duplicate: "why isn't the suggestion putting madison on my schedule/calendar?"): only the student's Faculty
  appointment books a meeting. The facts list their scheduled appointments from the day of their latest message, and the timed
  Google or hand-made calendar entries of the next two weeks whose title names them (a word of their family name and one of
  their given names, so classmates sharing a family name are told apart; none titled makeup, which follow the quiz makeup
  facts) as entries that book nothing; an agreed time no listed appointment covers gets its appointment even where such
  an entry holds it, and the explanation (never the reply) says the entry is still on the calendar. Nothing is written to
  Google by it; the instructor removes the entry. Both lists are left out when empty (follow-up prompt, guide).
  ONE ENGINE (2026-10-05, SCHEDULING): a card's calendar note and a reply's open meeting times are `scheduling/engine.py`'s `check`
  and `busy`/`free_spans`; a DR exam or a due date no longer counts as an overlap or takes a meeting's time.
  MAKEUPS AND MEETINGS (2026-10-05, requirement: "we should not overlap makeup sittings with non-makeup related meetings"): a makeup
  (a makeup sitting, or any event titled makeup) takes a meeting's open time for its quiz's length (ABSENCES "MAKEUP BOOKING"
  LENGTH; a makeup typed as text, "1PM", read for its start), in the facts, in a card's calendar note and so in DONE FOR YOU's
  calendar check; the instructor's own office-hours blocks still do not. A proposed makeup sitting's calendar note counts other
  makeups as shared time, not overlaps, and lasts its longest student's quiz. MAKEUPS NEVER CONFLICT (requirement, 2026-10-19,
  after a follow-up moved a student's Quiz 3 replacement from 3:00 to 3:30 to clear a Quiz 4 makeup at 3:00 in the same room:
  "makeups are not supposed to be a conflict with each other"): the follow-up instruction and the guide say so, so a proposed
  makeup sitting takes the student's time or the start of the open time even while another makeup sits then. The "Quiz makeups" facts describe the card's
  offer (office hours only in the rooms that can seat a makeup, meeting hours, on the next two days that have one and never
  before the quiz since 2026-10-06, ABSENCES "MAKEUP BOOKING" NEXT TWO DAYS; Monday and Tuesday first before that; never the
  instructor's small office).
  (2) A reply says the instructor will follow up only when the instructor still owes the next step; one that asks the student
  for something ends with that request (guide "Mail to a student sounds like the instructor"). (3) While the term's cards are
  on, a reply proposes no quiz makeup time: it says a separate email to pick one is coming once the absence is excused (or
  points to a card already sent, or names the sitting already booked), and the follow-up does not wait on the student's
  availability. Exam makeups are unchanged (seated, departmental). Read only; nothing is booked or sent by it.
  INSTRUCTOR'S NOTES (2026-10-04, requirement: "does the agent see the notes I've typed on the students?", then "yes, do it"):
  the follow-up facts also carry the student's standing notes (STUDENT-NOTES "SEEN BY THE ASSISTANT"); they inform the
  proposals, and a reply never quotes, mentions or hints at them. In chat, `find_student` answers each match's
  `enrollment_id` and the guide says to read `/api/student-notes/students/<enrollment_id>` whenever looking into or writing
  to one student.
  SAID IN THEIR RECORDED MEETINGS (2026-11-13, RECORDINGS "Student meetings", step 4; instructor: "build 4"): the follow-up
  facts also carry the student's latest items from their OWN recorded meetings, at most ten, newest meeting first
  (`recordings.meetings.facts`, the recordings' own reader: never an item the instructor dismissed, never another student's
  part of a shared recording): each with the day of the meeting, who owes it and the exact words it rests on, said to be a
  reading of a transcript. They do not take part in a preparation's source, so a new recording does not make one outdated.
  They were said with the student present: a reply to THAT student may refer to what was said in their own meeting, and
  never to another student's meeting or any other recording; and, unlike the notes, they are given to the draft check too
  (`_record_facts(notes=False)` keeps them), so a reply that keeps a promise made in the meeting is supported. A decision is
  still the instructor's: what a student said in a meeting is evidence, never an Excused, a grade or a makeup.
  OFFICE HOURS (2026-10-04, requirement, of a drafted reply to a student who had come to Friday office hours and looked for them
  "in your office in Blocker": it apologized and named Monday's room but "left out clarification about office hours location",
  so they added that the room depends on the day, with each day's room, "so the student wouldn't be confused"): the follow-up
  facts also list the instructor's office hours by weekday with time and room, read from the calendar as the Calendar page reads
  it (the booking card's office-hours rule, ABSENCES MAKEUP BOOKING, leaving out events titled makeup): a block is weekly when
  it falls on its weekday more than once within two weeks either side of the student's message; one seen once within a week of
  it is listed by date. A reply that sets an office-hours time names that day's room, and one whose student had something wrong
  (a wrong room, time or date) sets it right kindly in a sentence or two (guide "Mail to a student sounds like the instructor";
  the follow-up prompt says the same). Read only; nothing is booked or sent by it. A STUDENT'S HOLD (2026-10-06, requirement, of
  a reply offering a student "extra office hours this Thursday ... from 1:00 to 2:20 PM" that were a Google event "Office hours:
  <another student> (MATH 140-515)" holding that student's meeting window: the open-time check letting meetings overlap it "is
  supposed to be that way, appointment office hours can overlap", the reply is the issue): an office-hours entry whose title
  names a current student (a word of three letters or more of a roster name) is that student's and is not listed as office
  hours (unless it is a Faculty appointment it books nothing for them either: ONLY FACULTY BOOKS); the prompt says the open meeting times listed are already checked against the calendar, another student's
  appointment there is neither office hours nor a reason to narrow them, and a reply never mentions it.
  DROP-IN OFFICE HOURS (2026-10-08, requirement, of a drafted reply offering a student "tomorrow, Friday, October 9, from 3-5 PM
  in BLOC 306. What time in that window works best for you?": "If it only proposes regular office hours, then it shouldn't
  require the student to respond with a defnite time, it should tell the student anytime during those hours are ok. A definite
  time is only required for non-regular office hours appointments"): regular office hours are drop-in. A reply that offers
  them names the day, hours and room and says the student can come any time then; it never asks which time they will come, and
  offering them proposes no appointment (the follow-up prompt, the office-hours facts, guide "Mail to a student sounds like the
  instructor"). Only a meeting outside regular office hours (MEETING HOURS) asks for a definite time. A student who names a
  time themselves still gets it as THE STUDENT'S TIME says; a quiz makeup follows MAKEUP TIMES, where an office-hours block is
  already offered as "come any time".
  OTHER CONVERSATIONS AS BACKGROUND (2026-10-04, requirement: "does the agent also pull communications to and from the student if
  it needs them?", then "yes, do it"): each preparation already read the student's whole communications record but was told to
  "use only this exact conversation". It now answers only that conversation and uses the student's other conversations, both
  ways, as background, so a reply never asks for what they already sent or said or contradicts what the instructor already told
  them; it says when an earlier message decided something. Bindings are unchanged: the run, its staleness checks and its cards
  stay tied to the selected conversation, and automatic record-keeping still takes files from it alone (a file from another
  conversation is a card). In chat, the guide has the assistant read `/api/communications/students/<enrollment_id>` (every
  channel) whenever it looks into or writes to one student.
  EDIT IN PLACE (2026-10-04, TEACHING-WORKFLOWS): the instructor's edit of a waiting mail card's subject and text is a new
  waiting card of the same run, made through the same write checks and Mail preview and recorded with the instructor as its
  proposer (`_actor`); the replaced card is declined as `replaced_by_edit`. The edit route is NEVER for the assistant, sends
  nothing and approves nothing: the edited card still needs the instructor's Send.
  DECISION ON THE CARD (2026-10-12, requirement, of a follow-up's Record an absence card: "in the 'decision' field, can you make
  it into a dropdown menu that defaults to the model's suggestion, but allows me to change it from the dropdown"): a waiting
  absence card (`absences.create`, or an `absences.revise` that names a decision) shows its Decision as a dropdown standing on
  what the Assistant proposed. Its line in the card's `effect` carries `choice` (`value`, `options` in the Absences page's
  order; a new absence is not offered Withdrawn; `suggested` once the instructor chose). Choosing another decision posts the
  same edit route with `status` alone and is saved like any EDIT IN PLACE: a new waiting card in the old one's place through
  the follow-up's own write checks, the old one declined as `replaced_by_edit`. Nothing is recorded until the instructor's own
  Record absence. Their choice of Excused is their word (EXCUSE NEEDS EVIDENCE refuses only the Assistant). A note the
  Assistant wrote on its own decision (`decision_note`) is not kept under another decision. A decision chosen before can be
  chosen again (the card an edit replaced was never the instructor's refusal). The card then reads "Your decision. The
  Assistant suggested …". A changed decision holds the round's scheduled status as an edited reply does (ASK AFTER A CHANGED
  DECISION, 2026-10-13, under ASK AFTER AN EDIT; until then it did not apply). No dropdown while a preparation
  runs, on a follow-up that changed, or on a step an Assistant chat prepared. A decision they chose and then recorded may
  teach how that kind of absence is decided, never anything about the student (ASSISTANT-MEMORY "LEARNED FROM AN EDIT").
  A CHOICE ON THE CARD (2026-10-22, ABSENCES "DEPARTMENT MAKEUP EXAMS"): a card whose lines name the request field they set
  (`choice.field`; only the endpoints `FollowupSuggestions.CHOICES` lists: `absences.department_makeup`'s session, time, Scantron,
  documentation, calculators and reset) has a dropdown on each. Choosing posts the same edit route with `{choice: {field, value}}`
  and is saved like any EDIT IN PLACE (a new waiting card, the old one `replaced_by_edit`, the route's own preview checking the
  value); it holds no scheduled status and sends nothing.
  An absence and the documentation the student sent are one card: the prompt and guide have the assistant put the student's
  captured attachments in the absence's own POST (`documents`, ABSENCES WITH DOCUMENTS) instead of a later round; the card lists
  the files (2026-10-04).
  ASSUME, DON'T ASK (2026-10-04, requirement's plan to need less of their time; asked, they chose "decide, flag the guess"
  after 30% of preparations stopped on a question, most skipped with Retry): when unsure, the assistant chooses the most likely
  reading from the term policies and Faculty's records and proposes it, stating the guess in `faculty_write`'s optional
  `assumption` ({text, up to 3 alternatives}, bounded server-side and kept in the card's proposed event); the card shows
  the guess under an "Assumed" label with each alternative a framed choice (READABLE REVIEW, TEACHING-WORKFLOWS), and each
  alternative is the instructor's Ask for changes ("Instead of …"). Each alternative is the other reading in a few words, never
  an edit to a card, and a correction reworks everything that followed from the assumption in every card, not only the words it
  names (2026-10-05: an alternative written as "replace the opening with 'We won't have a quiz Friday'" left the reply's
  documentation request, which only the assumed quiz needed). Questions remain
  for an Ask-me policy area, a genuinely ambiguous student or thread, or a fact the reply depends on that neither the message nor
  Faculty holds. The facts each preparation starts with also list the student's absences in the window (attach to one, never
  record an overlapping absence), their approved accommodations and their Disability Resources bookings.
  DONE FOR YOU (2026-10-04, requirement: "increase automation and integration in the app to minimize my effort, especially for
  tasks that are easily handled and managed by the agent"; asked, they chose that the Assistant records the absence, attaches
  the student's files and puts agreed office hours on the calendar by itself, each shown with Undo; makeup sittings, excuse
  decisions and replies stay theirs; evidence: absence cards approved unchanged 46 of 46): in a dispatcher preparation
  (`_followup_auto`, never an Edit-in-place session), `validate_write` alone may return `auto` (category "Follow-up
  record-keeping") for: `absences.create` with status pending and no decision note, for the follow-up's own student in one of
  their current sections, dated within three weeks before to a week after their latest message, overlapping none of their
  standing absences that term, documents only from this conversation; `absences.attach_message` to this student's standing
  absence, from this conversation, not already attached; `student_support.create` of an office-hours appointment for this
  student within the next three weeks, an hour or less, no other appointment that day, nothing on the calendar at that time, and,
  while the student's term has MEETING HOURS (2026-10-05), wholly inside one of them (else a card saying they are outside them);
  never while the instructor's Google calendar cannot be checked (2026-10-05, SCHEDULING "GOOGLE CHECKED": a card saying why;
  the meeting facts and card notes say so too).
  An absence or appointment also needs Jev's confirmation against the student's messages (JEV FOLLOW-UP RECORD CHECK), its days
  and time matched in code against the days the conversation names (JEV DATES, 2026-10-08). A day the student wrote for
  something else (a meeting they ask for, a due date, the day they are back) is not a day of an absence: Jev must also confirm
  each of its two days, quoted in the student's own words, as one they say they missed or will miss, else it is a card naming
  the day (JEV A DAY THEY MISS, 2026-10-10; the instructor, asked, chose "Ask Jev about the day"). A week the student wrote
  ("this week") stands for an absence's days only when the absence covers that week's class days, Monday to Friday; a day
  inside it must be written by itself, else it is a card (JEV WHOLE WEEK ONLY, 2026-10-10; they chose "Whole week only").
  The class days of the student's section between an absence's first and last day (the Term schedule the term uses; every
  Monday to Friday without one) are checked as those two are, each written and each confirmed by Jev, so two separate days
  are never recorded as one stretch (JEV EVERY CLASS DAY INSIDE, 2026-10-10: "check the days between the first and last").
  Any absence a follow-up proposes, card or not, starts and ends on a day the student can miss (ABSENCES "A FOLLOW-UP NAMES CLASS DAYS",
  2026-11-03): refused in `validate_write`, naming the days to propose, or with none, that there is nothing to record.
  RULE 7 NOTICE (2026-10-05, ABSENCES): a preparation's facts give the conversation's first student message (ID and campus time)
  and each listed absence's notice timing and Rule 7 days; `validate_write` refuses an `absences.create` without
  `notice_message_id` while the conversation has a student message, naming that message, and one recorded by itself names a
  notice from this conversation (else a card). An absence for a death is recorded with `"death": true` (ABSENCES DEATHS WAIT), and
  each listed absence that is one says so.
  A STUDENT'S OWN COPY (2026-11-08, QUIZZES "A COPY BY EMAIL"; the instructor asked that a follow-up attach the take-home it was
  asked for): `documents.student_copy` for the follow-up's own student and an assignment of their course is done at once, up to
  three per preparation, so the reply card can attach its route; Done for you lists it without Undo (declining the reply keeps
  it from the student). In the chat it is `auto`, category "A student's own copy". This widens the automatic tier by that one
  request, at the instructor's word: nothing leaves Faculty, and the email that carries the copy stays an Approve card.
  Never with an `assumption`, never touching certified work, never in the tool-use corrective pass; one absence, three attachments and one appointment per
  preparation; `duplicate()` covers them. Anything else, or a failed check, is a card and the model is told why. The run's
  automatic actions are `tier='auto'` receipts linked to it; the `suggestions=1` read leaves them out of the cards and lists
  them as `done` (past-tense line, state, Undo while the record is untouched, else its page). Undo is the instructor-only
  `POST /api/student-support/followups/suggestions/<run_id>/undo` (NEVER for the assistant): the owning service's own write as
  the instructor (absence withdrawn while still its first, undecided revision with only its own documents; document removed
  while the absence is undecided; appointment cancelled while scheduled, unchanged and not past), request ID `undo:<action>`,
  recorded in enrollment-owned, append-only `student_support_suggestion_undos` (migration `student_support/009`). Once undone,
  that kind of record is a card on that follow-up. Nothing outward is automatic: the appointment's calendar projection is the
  existing calendar sync (AGENTS.md OUTSIDE SERVICES).
  MAKEUP HOLD (2026-10-06, requirement, asked whether the assistant should place holds when a conversation says to wait,
  chose "automatically with Undo"; ABSENCES MAKEUP BOOKING HOLDS): `absences.makeup_hold` with `held: true` on this student's own
  absence (not withdrawn), its check-back day (if any) within the next 60 days, one per preparation, is `auto`. No Jev check: a
  hold only keeps a booking card and offered times back. The prompt holds when the instructor told the student to wait or their
  documentation says they cannot take a quiz makeup yet and no return day is known (a reassessment, a recovery), never for a known
  return date (ABSENCES MAKEUP BOOKING HOLDS › A KNOWN RETURN, 2026-11-09: "Don't hold; send card at excusal"; the date goes in the
  absence's details, the card's times start from it, and a hold proposed anyway is refused in code), never for a student who only
  prefers another time (the card's "None of these work" is for that); each listed absence that is held says so with its hold
  revision. A release is always a card (proposed when a held student says they can take it now). Undo, while the revision it
  made is still the latest, puts back the one before (a release when there was none), as the instructor, request `undo:<action>`.
  SCHEDULED STATUS (same decision, "follow-up status"): this conversation's own follow-up resolved, To do, waiting on the student
  or snoozed (at most 60 days), a revision changing nothing but status, date and next step, is `auto` with category "Follow-up record-keeping,
  after the reply": `_write` records it as a `tier='auto'` action with a `proposed` event `{"deferred": true}` (never a card,
  answered `scheduled`). `FollowupSuggestions.run_deferred` runs it once its round's reply has a confirmed send (right after the
  instructor's Send in `approve`, after Edit in Mail on the Inbox worker's next turn), at once when the finished round has no
  reply, after `validate_write(..., _submitting=True)` (so Waiting on student still needs the confirmed reply); a declined,
  expired or failed reply, or a changed conversation, cancels it; an uncertain send keeps it waiting. Done for you shows it as
  "After you send the reply: … [Don't]" (Don't = the undo route, recorded `cancelled`); once run, Undo puts the previous revision
  back (a new follow-up back to Needs my reply) while it is still the latest. Done for you leaves out a scheduled step that a Retry or Ask
  for changes replaced (declined `replaced_by_request`) or a changed conversation withdrew (expired), unless the instructor
  undid it: it never happened and the new preparation lists its own (2026-10-04, requirement, of the same "Marked this
  conversation waiting on the student" line three times after two Retries: "why does it have that line duplicated"). OWN WRITES counts these revisions, and the
  instructor's Undo revisions, as the follow-up's own. A conversation with cards still waiting stays in Active and Needs me.
  Besides THANKS CLOSES (Waiting on student), JEV TO DO THANKS (2026-10-07) resolves a To do by the same recorded automatic action
  when Jev reads that a strongly supported thanks leaves the instructor owing the student no further message; Undo puts it back
  to To do. DEADLINE CLOSES (2026-10-08, requirement; TEACHING-WORKFLOWS WAITING ON) resolves a Waiting on student follow-up by
  the same recorded automatic action once its conversation's absence is Not excused automatically; no model is asked.
  EVIDENCE CLOSES (2026-10-22, requirement; TEACHING-WORKFLOWS WAITING ON) resolves one by the same recorded automatic action
  once Jev reads, strongly, that a record that came in for the student since (their paper back in a scan, a Response Card, an
  absence decided, a meeting passed) is what it waited for; no other model is asked, nothing is sent. DECISION CLOSES (2026-11-04,
  the instructor; TEACHING-WORKFLOWS WAITING ON) resolves a To do the same way once Jev reads that the absence it asks to decide
  has been decided, wherever that was done. A Waiting on student
  follow-up back on its check-back day (CHECK BACK) is prepared only on request. Done for you also lists a conversation Jev joined to the follow-up (TEACHING-WORKFLOWS SAME MATTER), with Keep
  separate in place of Undo.
  ASK AFTER AN EDIT (2026-10-06, requirement, of a To do "correct WebAssign 4.2" recorded by itself one second after they sent a
  reply rewritten to say nothing needed correcting; asked, they chose "Ask me" over re-deciding it automatically): a scheduled
  status was chosen for the draft, so once the instructor edits the round's reply (EDIT IN PLACE, or Edit in Mail) it no longer
  runs by itself. `_hold_scheduled` expires it (`held_by_edit`, left out of Done for you like any expired step) and proposes the
  same request again as an `approve` card of the run, recorded as the Assistant's (its engine), whose reason adds "You edited the
  reply, so this no longer happens by itself." and whose card says so. Approve all finishes prerequisites, then sends the reply, then updates tracking. A follow-up that
  changed since makes no card, and nothing is done.
  A REDO KEEPS THE STATUS (2026-11-04, requirement, of Ashton Nolan's follow-up left "Needs my reply" by a redo asked only to change an
  absence's dates, whose model proposed the reply and no status): `_keep_status` gives a round prepared again at their request, with a reply and no
  status of its own, the status of the round it replaced: the same request through the follow-up's own checks, scheduled or a card as the round's own
  would be. A round with its own status, or no reply, takes none; a follow-up that changed since refuses it.
  A DECIDED STEP IS NOT CARRIED (2026-11-04, requirement, of a To do "Decide October 28" kept on a follow-up by the card they had changed
  to Not excused and approved in the same round: "ive already decided those dates"): the changed decision still makes it a card
  (ASK AFTER A CHANGED DECISION, below), but the card no longer asks what they just settled. `_without_decision` takes out of a To do's
  next step each part that asks to decide and whose days are all decided by the round's absence cards (matched in code, `jev/dates.py`), or that
  names no day when no card is left undecided. Work left stays the To do (`Await her paper quiz`); no work left and the card asks to close
  the follow-up instead, and the card says why ("You decided this on the absence card, so it no longer asks you to decide it. Check nothing
  else is left."). A day not decided leaves the step word for word. A To do card already waiting is asked the same way. A decision made
  elsewhere (the Absences page) changes no card here.
  ASK AFTER A CHANGED DECISION (2026-10-13, requirement, of a To do "Review the obituary link and decide the October 12–14
  absence" recorded by itself one second after they had chosen Excused on the absence card and approved it with the reply;
  asked, they chose this and ONE CLICK ON A TO DO, TEACHING-WORKFLOWS ACTION DESK): the decision the instructor chooses on an
  absence card (DECISION ON THE CARD) holds the scheduled status the same way, since it too was chosen for the round as the
  Assistant drafted it. The card's reason adds "You changed the decision on the absence card, so this no longer happens by
  itself." (`HELD_DECISION`) and the card says "Check it still fits your decision." A choice equal to the card's decision
  changes nothing and holds nothing; a decision put back afterwards leaves the status a card.
  WAITING ON (2026-10-04, requirement chose "the Assistant writes it with every status change" and a To do status the
  Assistant uses when a reply promises something; TEACHING-WORKFLOWS WAITING ON): To do and the one-line `next` joined this
  automatic status change; title, details and location still make it a card.
  ONE FOLLOW-UP PER CONVERSATION (2026-10-05, requirement of Renata Ames's failed "Resolve" card: "why did this fail", then
  "yes fix it"): a follow-up preparation's `student_support.create` of a follow-up for a conversation that is already tracked is
  refused in `validate_write` (so also before a scheduled status runs or a card is submitted), naming the tracked follow-up and
  its revision so the model proposes the change as a revision of it; the service's own 409 stays. The preparation is told to
  revise the selected follow-up when it has an `item_id` and to create one only when it has none.
  A STEP LEFT BEHIND (2026-10-13, requirement, of Zoe Sheng's "Save follow-up" card answered with that refusal, a follow-up's
  ID and a route): the refusal written for the model is for the model only. At the instructor's click, or when a scheduled
  status is about to run, the same case says "this conversation's follow-up is already saved (its status), so this step was
  withdrawn" and names Edit follow-up…; the card is withdrawn there (`AlreadyTracked`, `expired` with `superseded`), so it
  never waits again. THE SCHEDULED STATUS TOO: the draft check's one repair withdraws the round's scheduled status with the
  reply it withdraws (`withdraw_draft_messages` reads every step of the run; `plan_actions` leaves a scheduled status out), so
  the status proposed with the repaired reply is scheduled in its place. Left scheduled, the old one made the follow-up when the
  reply was sent and the repaired one, a card by "one per preparation", could never be saved: the one live case, and the only
  card that limit ever made.

- **ASSISTANT-EMAIL-PREFILL** (2026-10-01, requirement's mailbox absence prefill request; ABSENCES EMAIL-PREFILL): an ephemeral
  absence draft uses the selected Assistant model/reasoning and the ordinary conversation lifecycle, including busy/restart checks.
  Its engine has no tools or MCP servers; `run_tool` also refuses every call for that session. This grants no new automatic tier.
  POST `/api/absences/drafts/mail` and `.../documents/mail` are instructor-only controls (`never` for assistant tools). The draft's validated fields open
  the ordinary absence form for explicit review/save; no saved conversation, absence, decision, documentation, grade or mail write
  is produced by model extraction. Captured documentation stays a reference; explicit form Save may keep a live email attachment
  through the ordinary section upload lifecycle. Existing chat sessions and tool availability remain unchanged.

- TEACHING DRAFTS (2026-10-03, TEACHING-WORKFLOWS). The teaching-review buttons submit an explicit instructor request in the existing Assistant chat, naming the certification and source route and supplying up to five questions with their observed results. A running answer is never interrupted. These actions request lesson/practice drafts in chat, not grading, publication or sending; the tool tiers and saving rules are unchanged.

- **ASSISTANT-JEV** (2026-10-03, requested addition 6). The configured JEV adapter
  checks source relevance and answer paragraphs against text actually observed by
  this turn's ordinary tools, before releasing buffered answer text. Source bodies
  stay bounded and ephemeral, after secret removal; returned/saved provenance has
  metadata only. Missing, omitted, image-only, contradictory or uncertain evidence
  is disclosed, never called verified (in chat inside the turn's closed fold, with
  only a contradiction shown outside it: QUIET STEPS). Verdicts are advisory and retained only by
  the existing conversation lifecycle. Checks have no tools and confer no permission
  to run a route. Send/Publish/Approve tiers and instructor actions are unchanged.
  The later Jev routes (2026-10-03: `jev_routing.palette_route`, `gmail_mail.check`,
  `canvas.message_check`, `student_support.wellbeing_reviewed`; 2026-10-07 `student_support.written`, the instructor's
  own record of mail written from a follow-up; the join and Keep separate of a student's conversations, TEACHING-WORKFLOWS
  SAME MATTER, were NEVER too until CONNECT A MESSAGE, 2026-10-10) are `NEVER` for the
  assistant: each spends a request or clears a wellbeing concern, and only the
  instructor's own pages call them (a pending mail card's check runs in the
  instructor's browser through the Mail route, not as an assistant tool).
