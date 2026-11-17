## Gmail in Faculty

Rule ID: **MAIL**. Current binding module contract. Read root `AGENTS.md` first.

- **MAIL-SEND-CHECKS** (requirement, 2026-10-03, Jev brainstorm item 4, "Notes, Send unchanged"; docs/decisions/jev.md
  SEND-CHECKS): the review (and a pending assistant mail card) shows notes from `POST /api/mail/check`, which plans the same
  message as the preview, reads the answered message from Gmail itself, and never touches the fingerprint or Send.
- **MAIL-SUMMARY** (2026-10-03): decode Gmail snippet character references once
  before removing controls and applying the summary length limit. Summaries remain
  plain text escaped by the page; raw cached Gmail payloads, message bodies and
  headers keep their existing handling.

- **MAIL-ABSENCE-DRAFT** (requirement, 2026-10-01): Draft absence on a roster-matched student's individual message asks the
  selected Assistant model (Claude or Codex) to prefill the ordinary absence form. ABSENCES EMAIL-PREFILL owns the ephemeral
  draft, trusted identity, restricted engine, review/save and documentation lifecycle. The Mail page supplies only thread/message
  IDs, never authority from email text. Drafting modifies no mailbox, sends nothing and records no absence; the instructor saves
  the reviewed form, and the absence changes nothing in the gradebook but its iClicker credit once excused (ABSENCES
  NO-GRADEBOOK, ICLICKER-CREDIT).

- The instructor asked (2026-09-28): "I want gmail to be integrated into my app". Asked, they chose reading mail in Faculty,
  composing and sending from Faculty, and a student's mail on their records, with read + send access (not the Needs-you flagging of
  unanswered student mail: not built). `gmail_mail/` (client, message, service, api, migration `gmail_mail/001_mail`),
  `static/mail.js`, `#mail` (sidebar entry under Calendar, palette "Compose an email"), `tests/test_gmail_mail.py`, `tests/test_mail.mjs`.
- ONE Google connection (`response_cards/gmail.py`): the Mail page's Connect (`purpose: "mail"`) adds `MAIL_SCOPES` (`gmail.readonly`) to
  the account already connected, never switching it; its callback returns to `#mail`. Sending needs only `gmail.send`, which Response
  Cards already holds. Reading a file a student linked from Google Drive is a further permission of the same connection
  (`purpose: "drive"`, `DRIVE_SCOPES`, back to Follow-up; COMMUNICATIONS "LINKED DRIVE FILES").
- READING keeps nothing on disk (thread summaries are cached in process memory by history ID only, so a refresh fetches just what changed) and changes NOTHING in Gmail (no labels, read marks or deletes). Bodies are shown as escaped plain text (HTML is
  reduced to its text, never rendered); attachments are named, and **MAIL-ATTACHMENTS-READ** (requirement, 2026-10-01: "I'm not able to
  click the attachment in the email"): one opens when clicked, in the app's file viewer, through `GET /api/mail/messages/<message
  id>/parts/<part id>` (`read_thread` names each file's `part`), which fetches it from Gmail then (`messages.get` full, then
  `attachments.get`; IDs checked before any URL is built), answers it as a download (attachment, nosniff), and keeps nothing. A
  sender's file is never on the viewer's NATIVE_PDFS: the viewer shows pictures of it. Thread IDs are checked as hex before any URL is built. Gmail allows about 250 quota units a second per user and a thread read costs 10, so a page reads at most 6 threads at once (`client.WORKERS`; measured 2026-09-29 on the live mailbox: a cold Students scan of 60 reads took 5.1 s two at a time, 2.3 s six at a time, never rate-limited), and a rate-limited READ waits (1, 2, 4 s) and asks again; a send is never repeated that way.
  A STUDENT'S MAIL (the section roster's student window, "Mail with this student…", `GET /api/mail/student`) is a live Gmail search on
  the roster address (from, to or cc); a student without a usable address has no mail there, nothing is guessed. Since 2026-09-30 the
  student window opens the communications record instead (below), whose timeline keeps this live search as "Search Gmail now…". The
  Mail page still stores nothing it reads; the record keeps copies of mail with students by the instructor's decision. Writing to a
  student (2026-10-02): the roster's student window has "Write to <first name>…", the compose window prefilled with the roster
  address; the gradebook's record reaches the same through its timeline, where the address comes from `GET /api/mail/student` (and a
  student without one gets the reason, never a guess). Either way the message is sent only from the review's explicit Send. The
  reader opens beside the list (desktop only, 2026-10-03).
- MAILBOXES (requirement, 2026-09-29): Students (the default; there is no Unread tab), Sent and All mail. **Students** is the inbox filtered to
  mail from current students: Gmail is searched by the roster's mail domains and Canvas (`from:(@email.tamu.edu OR ... OR @instructure.com)`),
  then the server keeps only threads whose LAST message (drafts aside; requirement, 2026-09-29: a thread I answered belongs only in All mail) is
  from exactly one student's roster address (`variants`) or a Canvas message `canvas_student` names (less those a follow-up has settled, MAIL-FOLLOW-UP), scanning up to `STUDENT_SCAN` pages until `STUDENT_WANT` threads are found. Each row names the student, section and "via Canvas". The page asks one Gmail page at a time (`step=1`, which returns those limits as `scan`) and shows each page's students as they arrive, ending exactly where the server's own scan would; reading a listed mailbox again swaps the whole result in at once. A read of the same mailbox and search already under way is reused, never started again (requirement, 2026-09-29, on the page's slow first load: the shell's render and refresh each restarted the scan, three cold scans per page load).
  Read-only view; earlier `tab=inbox`/`unread` links open Students.
- **MAIL-FOLLOW-UP** (requirement, 2026-10-10: "when an email from a student is related to a follow up, can we have it cleared from
  the inbox the same as those emails that have a reply"; that day all six threads Students listed were part of follow-ups already
  Resolved: three thank-yous that had left theirs Resolved, three Canvas notices). A thread leaves Students, as an answered one
  does, while the student's message that ends it is part of one of that student's follow-ups and that follow-up shows anything
  but Needs my reply (Resolved, Waiting on student, Follow up later, To do), as the Follow-up page shows it: a thanks that leaves
  it Resolved goes with it, and a newer message that reopens it keeps the thread listed. A Follow up later whose day has come
  asks on the Follow-up page, not here.
  - The Follow-up module answers (`StudentSupportService.settled_mail`, TEACHING-WORKFLOWS), from the communications record's
    captured copy of that exact Gmail message, written by that student: its own thread, the Canvas conversation its notice
    names, or a conversation joined to another (SAME MATTER).
  - Never hidden: a message the record has not captured yet, a conversation no follow-up tracks (a suggestion, FYI included),
    and anything by another student's follow-up.
  - A view. Nothing is saved and Gmail is not touched: the thread keeps its Inbox label, is in All mail and opens as before.
    Sent and All mail never ask.
  - PERFORMANCE: one catalog read a Gmail page that holds students' threads, reading only the students who have a follow-up
    (about 11 ms each on the live catalog, 2026-10-10; nothing for the others). A settled thread does not count toward
    `STUDENT_WANT`, so the scan reads on.
- SENDING is workspace-owned and explicit: preview (writes nothing) returns the exact message and its fingerprint; POST send needs the
  fingerprint, a request ID and `confirmation: "send"`, all checked on the server. The intent is committed to `gmail_mail_sends`
  BEFORE the request leaves and the outcome (`accepted`, `failed`, `uncertain`) to `gmail_mail_outcomes` after (both append-only). A
  timeout, a 5xx or a send with no outcome is uncertain and is NEVER sent again; the same request ID returns what happened. A reply's
  recipient, thread and In-Reply-To/References are rebuilt by the server from Gmail (a newer message in the thread makes the review
  stale, 409). A class goes on Bcc in ONE message addressed to the instructor, so nobody sees a list; at most 200 addresses. Section
  audiences read the roster and the TA list at compose time and show who was left out and why. Faculty sends nothing by itself,
  except the one named exception: the DR quiz notice to a DR quiz taker (ACCOMMODATIONS "DR QUIZ TAKERS") after a quiz is marked Prescheduled in AIM
  (SCHEDULING "DR QUIZ NOTICES"), sent through this same preview and send with its receipts, and never resent automatically; and a
  message the instructor scheduled, sent at its time exactly as they approved it (MAIL-SEND-LATER).
- **MAIL-SEND-LATER** (requirement, 2026-11-07: "I want to be able to schedule emails to go out to students at a pre-determined
  time (both through canvas and/or gmail)"; asked, they chose the Mail page's new messages and the Canvas page's messages to
  sections, recipients exactly as approved, up to two hours late and then held for them, and the Assistant through its Approve
  card). `scheduled_sends/` (service, api, migration `scheduled_sends/001_scheduled`), `static/scheduled-sends.js`,
  `tests/test_scheduled_sends.py`. WORKSPACE-owned, like the sends it makes.
  - SCHEDULING. The review is the ordinary one (Mail's preview; the Canvas message's preview, which reads Canvas). Send later…
    asks a campus date and time (the field's value is a wall-clock time read in America/Chicago; at least a minute ahead, at most
    180 days) and Schedule records instead of sending: `POST /api/mail/schedule` or `/api/canvas/messages/schedule` with the
    review's fingerprint, a request ID, `confirmation: "schedule"` and `send_at`, all checked on the server. Kept
    (`scheduled_sends`, append-only): the send's own fields and fingerprint, and for Canvas the reviewed plan itself (exactly
    who gets it in which course). Recipients are EXACTLY AS APPROVED: nothing is read again to decide who gets it, so a student
    who dropped since still gets it and one added does not. Not offered for a reply (the student may write again first) or with
    a file from the instructor's computer (never kept; JSON only, request_limits.py); Faculty files are kept by route and read at
    the send, where one that changed since holds the message. The same request ID returns the same scheduled message.
  - SENDING. A thread of the web process (app.py `__main__`, never the checks) looks every 30 s and sends a message whose time
    has come through the ordinary send (Mail's `send`, the Canvas message's `send_approved`, the plan kept, its files read now)
    under the request ID `later-<id>`: its receipts are that send's own, recorded before anything leaves, and a second try
    returns what the first did, never a second message; an uncertain one is never sent again. A send to Gmail registers with
    `canvas.writes` ("a message sent later"), so a restart waits for it, as for a Canvas message.
  - LATE AND HELD. While Gmail is not connected, Faculty is signed out of Canvas (the kept session is asked, `users/self`) or is
    restarting, the message waits and is tried each round, at most two hours after its time (the record says how late it went);
    then it is HELD: never sent by itself, on Home's Needs you ("Scheduled messages that need you", `#mail?scheduled=1`) with
    Send now and Cancel. A send that failed or could not be confirmed stays on Needs you for 14 days until Dismiss (nothing is
    sent again). Events (`scheduled_send_events`): `sent`, `held`, `cancelled`, `acknowledged`; the latest counts.
  - WHERE IT SHOWS. Mail's Scheduled tab lists every message sent later (to go, held, and those of the last 14 days), the
    Canvas page its own still to go above "Messages sent through Canvas". Cancel (until it is sent) and Send now ask first.
  - THE ASSISTANT: `gmail_mail.schedule` and `canvas.message_schedule` are Approve cards from the ordinary previews
    (`confirmation: "schedule"`), the card saying when it goes; `scheduled_sends.send_now` is an Approve card (`"send"`), Cancel
    and Dismiss unnamed routes (Approve).
- **MAIL-REPLY-TO** (requirement, 2026-10-02: replies to Canvas message notifications "hasn't recently" reached the students; asked
  to fix it). Canvas writes each message notification From `notifications@instructure.com`, a no-reply address that drops mail
  without a bounce, and takes replies at its Reply-To (`reply+…@notifications.canvaslms.com`, which posts them in the Canvas
  conversation). Reply wrote To from the From, so four replies (2026-09-30 to 10-02) never reached Canvas. A reply now goes to the
  answered message's Reply-To whenever it names one: `read_thread` gives a message's `reply_to` (only when it has one, so other
  threads read as before), Reply prefills it, and the server, which reads the thread itself, replaces the answered sender's From
  address in To/Cc/Bcc with its Reply-To (`redirect_reply`), so a tab opened before the fix is corrected too. The review says so.
  In-Reply-To and References keep each Message-ID as written, one per line (`compose`, raw headers): the email package's folding
  had turned Canvas's long IDs into RFC 2047 encoded words. Nothing lost earlier is resent (OUTSIDE SERVICES).
  For Canvas messages the Reply-To is no longer used: MAIL-CANVAS-REPLY (below) sends those replies to the student by email.
- **MAIL-CANVAS-REPLY** (requirement, 2026-10-02: "send replies to the student's gmail account regardless of if the original message
  was from canvas or from gmail"). After the MAIL-REPLY-TO fix, Canvas accepted none of four replies Faculty sent to the correct
  `reply+…` addresses: no bounce, nothing in the Canvas conversation. Gmail's own replies do post there, and the cause was not
  found. So a reply to a Canvas message notification goes to the student BY EMAIL, at their roster address. The student is
  identified as everywhere else (`canvas_student`: every word of Canvas's display name in exactly one current student's roster name,
  in the section the subject names), and the address must name only that student. The thread view gives that message
  `reply_to: [{name, email}]` (the roster address replaces Canvas's Reply-To) and `reply_subject: "Re: <the subject the student gave in
  Canvas>"`. In `_plan`, the server, which reads the thread itself, replaces every Canvas address (`instructure.com`,
  `canvaslms.com`) in To/Cc/Bcc with that address, so an older tab is corrected too. The review says the message goes to the student
  by email, not through Canvas. When no single student with a roster address fits, the reply is refused while it would go to Canvas
  or to nobody, and the instructor writes the address. Faculty never writes to a Canvas address in a reply. Mail from a student's
  own address is answered at that address, as before. The reply stays in the instructor's Gmail thread; the student gets it as a new
  email.
  Since MAIL-CANVAS-POST, this is the fallback: a reply is posted in Canvas when it can be.
- **MAIL-CANVAS-POST** (requirement, 2026-10-02: after Canvas silently ignored the replies Faculty emailed to its reply+ addresses,
  they asked to reply through Canvas's conversations API instead; "yes, build it"). A reply to a Canvas message notification is
  POSTED IN THE CANVAS CONVERSATION, as the instructor, through the kept Canvas session (CANVAS "SIGN-IN AND THE KEPT SESSION";
  `POST /api/v1/conversations/:id/add_message`, `canvas.web.Client.add_message`). It goes to the answered author only, as Canvas's
  own Reply sends it, never to everyone in a group conversation. A restart waits for a post under way, and none starts once Faculty
  is stopping (canvas/stopping.py; 2026-10-04). The conversation is the one the notification links to
  (`…/conversations/N` on Faculty's Canvas host, exactly one). The preview reads it then (`auto_mark_as_read=false`, so nothing
  is marked read), and the reply answers its latest message by someone else. That message's author must be the student the
  notification names: every word of Canvas's display name must be in their Canvas name. The conversation, the author's Canvas
  user ID and that message's ID are in the fingerprint, so a newer message from the student makes the review stale (409).
  The review says "Post in Canvas" and offers **Send by email instead** (`via: "email"`).
  It goes BY EMAIL instead (MAIL-CANVAS-REPLY), and the review says why, when:
  - the reply carries files;
  - it has Cc, Bcc or any To other than the student's roster address or a Canvas address;
  - Canvas is not set up, or Faculty is signed out;
  - the conversation cannot be read or found;
  - its latest message is not the student's.

  Receipts (migration `gmail_mail/006_canvas_posts`, WORKSPACE-owned, append-only):
  - The intent is the ordinary `gmail_mail_sends` row, with empty To/Cc/Bcc.
  - `gmail_mail_canvas_posts` records the conversation, recipient and answered message, committed with it BEFORE Canvas is asked.
  - `gmail_mail_outcomes` records Canvas's answer: `thread_id` is the conversation, and `message_id` stays empty, because it names
    Gmail messages only and the communications record reads it.
  - A 4xx, or Canvas refusing the session, is `failed`. No answer, or a 5xx, is `uncertain`, and it is never posted again.

  An accepted post is shown in its Gmail thread as "You, in Canvas". A thread answered in Canvas after its latest message leaves
  Students, as one answered in Gmail does. Gmail itself is not touched.
  SIGNED OUT (requirement, 2026-10-02: "yes add it"): when the reply goes by email only because Faculty's Canvas session is gone, the
  preview says `canvas_sign_in: true` (absent otherwise), and the review offers **Sign in to Canvas**. That is the Canvas page's own
  sign-in window (`openCanvasSignIn`; NetID and password from the saved school sign-in; Duo's verified-push code shown for the
  instructor to enter, never approved for them). When it closes, signed in or not, the review runs again with the same words: it
  becomes Post in Canvas once Faculty holds a session. Replying itself never triggers Duo.
- **MAIL-SENDER-NAME** (requirement, 2026-10-02: asked why Faculty's replies showed "Professor Sinkule" rather than "Christopher Sinkule",
  then chose that the Mail page alone changes). Everything the Mail page sends (the instructor's own messages and replies, and the
  assistant's approved drafts) is From `Christopher Sinkule <account>` (`service.SENDER_NAME`), as Gmail itself writes. The shared Google
  connection keeps "Professor Sinkule" (`response_cards.mail.SIGNATURE`) for the Response Card and seating mail to classes.
- **MAIL-ATTACHMENTS** (requirement, 2026-10-01: "I want the agent to be able to attach attachments to drafts of emails for me to
  approve"; `gmail_mail/attachments.py`, migration `gmail_mail/004_attachments`). A message may carry up to 10 files, 25 MB together
  (Gmail's own limit on attachments; requirement, 2026-11-12, after 18.6 MB lecture notes for a follow-up's reply were refused: "raise
  the attachment limit to 25". It was 18 MB, on the reading that Gmail's 25 MB counts the message once base64 has grown it; not
  tried against Gmail before it was raised, so a message Google refuses as too large is a refusal and nothing is sent), named in `attachments` by the Faculty GET routes that serve them (a route, or
  `{route, name}`), never by bytes from a browser or a model. The server reads each through the app's OWN route in process
  (`AppFiles`, so every guard applies; never what `assistant.policy.FILE_BLOCKED` names, the assistant's routes other than its saved
  files, or mail routes; JSON answers are data, not files; the route's characters are those the drawer links) at the preview AND
  again at the send. The preview names each file (route, name with a fitting extension, type, size, SHA-256) and the fingerprint
  covers them, so a file that changed since the review is a 409; a message without files keeps its old fingerprint and answer.
  Nothing is copied: `gmail_mail_send_attachments` (append-only, same transaction as the intent, before the request leaves) keeps
  each file's reference and digest, and "Sent from Faculty" lists them. The message is built before anything is recorded. Up to
  3.5 MB it goes as `raw` JSON (the verified path); larger, as a multipart upload (`/upload/.../messages/send?uploadType=multipart`,
  JSON metadata with the thread, then `message/rfc822`), the same uncertainty rules. A kept `.eml` goes as
  `application/octet-stream` (RFC 2046 forbids base64 `message/*`). The assistant's Approve card for `gmail_mail.send` draws the
  message (recipients, subject, text, every attachment from its route) with a Send button. Not yet sent through real Gmail: the first
  real send with files is the check of both paths.
- **MAIL-COMPOSE-ATTACH** (requirement, 2026-10-01: "add an attach button to the Mail page compose window"; asked where from, they
  chose "Both"). Under the message: "Attach from your computer" (a file picker, several at once) and "Attach from Faculty…", an
  inline picker over `GET /api/mail/files?kind=papers|notes|saved&q=` (`gmail_mail/library.py`, a read-only projection owned by the
  Mail page, WORKSPACE): a current student's reviewed papers (found by name or roster address in the current rosters, the search
  starting from the first To address; each paper read through `GET /api/documents/papers`, named "<assignment> - <student>.pdf"; a
  paper not reviewed yet is listed without Attach), the current courses' lecture notes (the edition the course page shows, whole and
  by part, through the course notes route), and the assistant's saved files. No identifier is shown. Each attached file is named,
  sized and removable (a Faculty file opens in the app's file viewer, VIEWER in assistant.md), and the review dialog lists them. A Faculty file is a route attachment as above. A file from the computer is
  never stored: the preview and the send are then `multipart/form-data` (the fields as JSON in `message`, each file a `file` part,
  named in `attachments` as `{upload: n, name}`; 19 MB per request, 12 parts), the page sends the same files to both, every file
  sent must be listed once, its type comes from its name (else the browser's), and the fingerprint covers its digest.
  `gmail_mail_send_uploads` (migration `gmail_mail/005_uploads`, append-only, positions shared with the route attachments) keeps its
  name, type, size and digest. The assistant's requests are JSON, so it never uploads. Extensions Gmail blocks (.exe, .js, …) are
  refused in the preview.
- MOVE TO TRASH (requirement, 2026-09-28: "how do I delete mail from here", then yes): a button on an open thread, the whole conversation,
  recoverable (Gmail keeps Trash 30 days) with an Undo in the toast; NEVER a permanent delete, labels (Archive's Inbox label aside) or read marks. It needs the wider
  `gmail.modify` grant (`MAIL_MANAGE_SCOPES`), asked for only when the instructor first chooses Allow (`connect` with `manage: true`,
  same account, returns to `#mail`); `status.can_trash` says whether it is held. The server checks `confirmation: "trash"` (Undo needs
  none) and a request ID; a request ID already recorded returns its record and calls nothing. `gmail_mail_trash` (migration 002,
  append-only) records the thread ID, direction and Google's answer, never a subject, sender or text.
- ARCHIVE (requirement, 2026-09-30: "a way to archive emails in the email inbox instead of just deleting them"): Archive beside Reply on an
  open thread is Gmail's own archive: ONE click (no confirmation; the mail stays in All mail), the whole conversation loses the INBOX label
  (`threads/<id>/modify`, `removeLabelIds: ["INBOX"]`) and so leaves Students, with Undo in the toast; a thread already out of the Inbox
  offers Move to Inbox instead (`read_thread` `inbox`). No other label or read mark is ever touched. It uses the same `gmail.modify` grant
  as Trash (`can_trash`, one Allow for both). POST `/api/mail/threads/<id>/archive|unarchive` with a request ID; a request ID already
  recorded returns its record and calls nothing. `gmail_mail_archive` (migration 003, append-only) records the thread ID, direction and
  Google's answer, never the mail. The assistant reaches these routes only through an Approve card (unnamed routes are `approve`).
- Google's failures answer 502 with the reason (never 401/404/409). Checks never reach Google (tests replace `client.http`).
