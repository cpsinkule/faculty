## Communications record

Rule ID: **COMMUNICATIONS**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-09-30): "the app should track all electronic communications between me and the students". Asked, they
  chose EVERY channel (Gmail both ways, Faculty's own sends including the Resend mail, the Canvas inbox and submission comments,
  Gradescope regrade requests), KEEPING COPIES, and ONE TIMELINE PER STUDENT FOR EACH TERM. `communications/` (service, record,
  storage, gmail_capture, drive_links, gradescope_capture, api, migration `communications/001_record`, README), Canvas reads in `canvas/web.py`,
  `static/communications.js`, `static/communications.css`, `tests/test_communications.py`, `tests/test_communications.mjs`,
  `tests/browser_communications.py`.
- OWNERS. A captured message (`comm_messages`) and its immutable revisions (`comm_revisions`, latest counts: a Canvas comment edited
  or a Gradescope reply added is a new revision) are WORKSPACE-owned: the instructor's own correspondence, and one message may concern
  students of several terms. Each link to a student (`comm_links`: role author/to/cc/bcc/about, and how it was known:
  `roster_address`, `faculty_send`, `canvas_sis`, `gradescope_sid`, `canvas_notice`; migration 002 rebuilt the table with its every
  row, index and trigger to allow the last) belongs to that student's TERM ENROLLMENT; the timeline of a
  student in a term is exactly its links. KEPT ORIGINALS (a Gmail message whole, as Gmail holds it, with its attachments; a file
  attached in Canvas) live in a NEW OWNER of the verified storage engine: `comm_bundles` (directory `communications/` on both drives,
  registered in `CourseStorage` beside terms, Inbox, definitions and sections: reconcile, nightly backup, status, retention and
  `manage_storage.py verify`). A bundle is the files one capture batch kept, written at once and never changed, and never current: it
  is staged on NVMe, backed up and verified by the nightly coordinator, then kept on the HDD alone and read from the verified
  snapshot (the Inbox's lifecycle for non-current payloads); `check_source` checks every `comm_files` checksum and every read checks
  it again. Everything is append-only (triggers) except the storage columns and `comm_capture_state` (runtime state). Catalog rows go
  into the nightly catalog backup and never into the generic export. An interrupted capture leaves an unrecorded folder, swept at the
  worker's start once it is an hour old.
- CAPTURE runs in the Inbox worker's `communications-capture` thread under its own lock (never the execution lock): every 20 s the
  channels that are due (Gmail every 5 min, Canvas 15, Gradescope 30) or asked for (Capture now: `POST /api/communications/capture`
  only marks them wanted; recording an email written from a follow-up asks for Gmail the same way, TEACHING-WORKFLOWS WRITTEN AT
  ONCE). A channel that cannot run (not connected, signed out, not set up, busy) WAITS and its status line says
  why; it never stops the others. Lock order: the capture lock, roster reads, network reads with no lock held, then per batch of new
  messages the roster lifecycle lock, the new bundle's owner lock and ONE catalog transaction recording bundle, files and messages.
  Nothing is sent, changed or marked read anywhere: every read is a GET (or Gmail's messages.list/get).
  - GMAIL: a search by every current student's roster address (both TAMU spellings, `gmail_mail.service.variants`), from, to, cc or
    bcc, 20 addresses per query, `after:` the term's window; drafts and spam out, Trash IN (`includeSpamTrash`, so a message deleted
    soon after it arrived is still kept). Each new message is fetched `format=raw` and kept whole; its text and attachment list are
    recorded. Mail sent from the Mail page is fetched by its Gmail ID and its Bcc'd students linked from Faculty's record
    (`gmail_mail_sends`). An address two students with different UINs share names nobody (never guessed). A message kept before a
    student joined the roster is linked to them from its kept addresses, never read again. Mail from an address not on the roster (a
    personal Gmail) is not the record's; a message that named no single student is looked at again after an hour.
  - THE COORDINATOR'S MAIL (2026-11-14, COORDINATOR; the instructor: "I want the coordinators emails to be included in the
    followup pipeline just like student's"). Each Gmail round also searches, for every current term that is coordinated, its
    coordinator's address (both TAMU spellings; from, to, cc or bcc; `after:` the term's window) and keeps each message whole,
    as a student's is kept. Its link belongs to the TERM: `comm_links.enrollment_id` holds the term's owner key
    `coordinator:<term id>` (COORDINATOR "THE OWNER KEY"), `matched_by` `coordinator_address`; migration
    `communications/003_coordinator_links` makes the links table again with every row, and a trigger holds every other link
    to an enrollment of its term as the dropped reference did. A message the coordinator wrote is no student's: its
    `author_role` is `other` and the link says they wrote it; the instructor's reply is `instructor`. One message may link a
    student and the coordinator alike (a student who copied them): each timeline shows it with its own role. One address
    names ONE term: someone who coordinates several current terms is linked under the term they coordinated first, so a
    message is one conversation, never one per course. An address a student's roster entry also holds names nobody. A
    message kept before the course was coordinated gains the coordinator's link from its kept addresses, never read again.
    A linked Drive file is fetched only from a student's message, so the coordinator's links are not. Read with
    `GET /api/communications/coordinators/<term id>` (the timeline's own shape, with `coordinator`); the students route
    answers 404 for the key. Mass reach counts students only. Nothing is sent, changed or marked read.
    `tests/test_coordinator_mail.py`. NOT READ ON REAL GMAIL while this was built: the first live round after a course is
    marked coordinated must be looked at.
  - LINKED DRIVE FILES (requirement, 2026-10-11, of a follow-up that left an absence Waiting for your decision although the student
    had sent a second doctor's note: it was a Google Drive link, and Gmail held no such attachment; offered fetching the file or
    trusting the link, they chose "fetch the file"). A student who attaches from Google Drive sends only a link. For a Gmail message
    a STUDENT wrote (never the instructor's, never a Canvas notification), each Drive file linked in the words written now is read
    through Faculty's one Google connection and kept in the capture's bundle, listed AFTER the attachments Gmail holds (`drive_id`,
    `file_id`, checksum; an attachment's place never changes, so an absence's reference to it stands), up to 5 files a message and
    25 MB each. `communications/drive_links.py`: a file by its ID (`drive.google.com/file/d/`, `open?id=`, `uc?id=`) or a Google
    Doc, Sheet, Slides or Drawing (read as a PDF); a folder, a form and a published page are not files. TAMU's mail filter
    rewrites links (Proofpoint URL Defense v3, every Drive link on the live record that day): they are put back first. Quoted
    history is set aside (`>` lines and everything from a reply header on), and a file the instructor linked in an earlier
    message is the instructor's, whoever's reply carries it: neither is fetched. On the live record that day 4 students' messages
    held a Drive link; 2 were the student's own file, 2 only quoted one.
    READS ONLY, and only a file a student's message links: `files.get` (name, kind, size, then the bytes) and `files.export`, to
    `https://www.googleapis.com/drive/v3/files` alone, no redirect followed; nothing in Drive is changed, shared, listed or
    searched. It needs `drive.readonly` (`DRIVE_SCOPES`, `purpose: "drive"`; Google has no narrower permission that reads a
    file someone else shared), asked for by the instructor's own click: `POST /api/communications/drive/connect` returns
    Google's sign-in for the account already connected (never switching it) and comes back to Follow-up, where a Gmail round is
    asked for at once (as Capture now asks), so the files that waited are fetched within seconds. The timeline's status says so
    while Faculty may not read Drive (`status.drive`), and a file waiting on it offers the same click. A message kept before
    Faculty read Drive lists no such file until Drive is allowed, so on Follow-up its link alone is enough: under the student's
    message the page says the linked file was not fetched and offers the click, and so does "Based on captured communications"
    (requirement, 2026-10-11, looking at that message for the button: "I dont see the option"). A view: nothing is recorded.
    The files are read just before their batch of messages is recorded, so a message's FIRST revision already holds them and a
    follow-up prepared from it sees them. One that could not be read is listed as not kept with the reason and what it waits on
    (`wait`): `permission` (Faculty may not read Drive, or the Drive API is off for its Google Cloud project) and `google` (Drive
    did not answer) are tried again each Gmail round; `sharing` (Drive does not show the file to the connected account) once per
    worker run; too large, a folder or not a file, never. A later round also takes the links of messages kept before Faculty
    read Drive (students' own messages of the current terms, at most 20 files a round, only while Faculty may read Drive). The
    message then gets a revision with the file, as a Canvas attachment kept later does ("edited" means only that its words
    changed); that revision makes a follow-up's preparation outdated, so it is prepared again where it would be anyway.
    A fetched file is an attachment the student sent wherever those are used: the timeline and the follow-up's message ("from
    Google Drive"), an absence's documentation (ABSENCES), a preparation's files (ASSISTANT), picture search. A link that was
    not read is never documentation. NOT READ ON REAL GOOGLE DRIVE while this was built (CHECKS): the first live fetch must be
    looked at.
  - CANVAS MESSAGE EMAILS, A BACKUP OF THE CANVAS INBOX (requirement, 2026-09-30: "build the backup", after 55 such emails, 44 archived,
    were found outside the record while the Canvas session was signed out). Each Gmail round also searches Canvas's message
    notifications (`from:@instructure.com subject:"sent you a message in Canvas"`, since the earliest window) and keeps each whole. Canvas
    sends them as "<sender's name> <notifications@instructure.com>" with the course and section in the subject, so the address names
    nobody: one is linked (author, `canvas_notice`) to the ONE current student whose roster name holds every word of the sender's name
    (two words at least) and, when the subject names a course and section, who is in it (`gmail_capture.notice_students`, the Mail
    page's rule counted per enrollment); none or several name nobody. The timeline shows the message alone (the lines between Canvas's
    "Subject:" line and its "You can reply ..." line; the whole text if the email is laid out otherwise) as "Canvas message (email)",
    under the Canvas filter, with the whole email as its original and the conversation's https link. Canvas's other email (grades,
    announcements) is not kept. A message read from Canvas and its notification are ONE entry, the Canvas copy with "Email copy"
    (`record.same_message`: the email's attachment link names that Canvas message, or it names the conversation, came within an hour
    after the message, from the student who wrote it, and holds its words); only the display merges them, both records stay.
  - TERM WINDOW (a fixed rule, `service.opens`): Fall from July 1, Spring from the previous November 1, Summer I and Summer (Full) from
    April 1, Summer II from May 15, Other from 120 days before the term was created in Faculty. A message links only to CURRENT terms
    whose window it falls in; what was captured stays when the term ends.
  - CANVAS: for each current section's linked course, the instructor's conversations (inbox, archived and sent) with a student of that
    course (Canvas SIS ID exactly a UIN of the term), each opened with `auto_mark_as_read=false` and only when its last message is newer
    than what was kept, READERS (4) at a time in chunks of 40 (each reader its own use of the kept session; the first pass over the
    four MATH 140 courses on 2026-09-30 was 792 conversations, about nine minutes one at a time); generated (system) messages skipped.
    Comments on a student's submission written by the student or the instructor (a TA's or grader's are counted, not kept), from
    each course's listing of every submission (about 40 pages a course), the courses' listings read at once.
    Attachments up to 25 MB are kept (the redirect rule in "Canvas"), each Canvas file ONCE by its ID (a message sent to many students
    one by one is one file in Canvas); one that cannot be fetched is listed as not kept with the reason and is tried again once per worker
    run (its message then gets a revision with the file; "edited" means only that a message's words changed). The kept session expires
    within hours; capture then waits for the instructor to sign in again. Canvas runs after Gmail and Gradescope in a round, so a long
    Canvas pass never holds them back.
  - GRADESCOPE: for each current section's linked course (never Math 140 for Instructors, never /logout, GET only through
    `gradescope_web.Session`), every assignment's Regrade Requests page (and, since 2026-10-28, those of each version of the
    bubble-sheet exams Faculty linked in the course, which Gradescope keeps off the assignments list: REGRADES "BUBBLE SHEETS") and each listed request's grading page (`open_request`,
    `closed_requests`). The student is the listing's name matched to exactly ONE student of Gradescope's own roster (`name_key`),
    whose SID must be a UIN of the section's term; owners the grading page names must agree. Anything else is counted unmatched and read
    again next round, never guessed. A request and its reply are two messages. THESE PAGES WERE NOT READ ON THE REAL SITE while this was
    built (a read-only probe was declined by the session's permission check, 2026-09-30); the parser refuses anything unlike the
    documented shapes and says so, and the first live rounds must be checked.
- FACULTY'S OWN RECORDS are shown BY REFERENCE, never copied: Response Card invitations and receipts (`response_card_mail`, relay or
  Gmail), seat-chart email with its PDF (`seating_student_mail`), and the student's Response Card answers (never the UIN field or a
  uin-digest field). A Faculty send that went through Gmail (its receipt names the Gmail ID) is ONE entry: the Faculty record, with the
  Gmail copy as its original.
  A RECORDED MEETING (2026-11-13, RECORDINGS "Student meetings", step 4; requirement, 2026-11-12: a student meeting's transcript
  goes on that student's timeline): each recording filed under the student is ONE entry, "Meeting" (`channel` `meeting`,
  `recordings.meetings.timeline`, read where Faculty's other records are), dated when their part of it began: how long it ran,
  what the recording's latest reading kept of THEIR part in plain lines (never an item the instructor dismissed, never another
  student's part), and "Open the recording", which opens `#recordings/<id>` at that part. It has no file, no address and no
  message ID: nothing to download, nothing a student wrote, never sent and never exported. The timeline's Meetings filter lists
  them; the student window's newest three may show one. The Assistant reads it with the rest of the timeline.
- THE TIMELINE: `GET /api/communications/students/<enrollment id>` (newest first, at most 1,000 entries), `.../messages/<id>/revisions`,
  and the kept files as DOWNLOADS only (`original` .eml, `attachments/<n>` checked against the checksum recorded at capture,
  `seat-mail/<id>/chart.pdf`; Content-Disposition attachment, nosniff, a sandbox CSP), never rendered in the app's origin. The
  timeline opens each kept file in the app's file viewer (VIEWER in assistant.md; requirement, 2026-10-01: "switch those other download
  links to the viewer too"), which shows only what it makes from the file: pictures of a PDF's pages drawn in the document sandbox, a
  re-encoded picture, escaped text, or an email as a mail program shows it (its From, To, Cc, Date and Subject, its text with HTML
  reduced to text, and its attachments by name); HTML and SVG files are offered for Download only. The
  assistant may read the JSON (the instructor chose full access); it is never handed the download routes as data (`GET_BLOCKED`), but
  `fetch_files` shows a kept file to the instructor and lets the model look at a picture or PDF among them (2026-09-30, "Faculty
  assistant" FILES). A kept picture or PDF on a message a student wrote is embedded on this machine so it can be found by
  how it looks (PICTURE-SEARCH, 2026-10-10: read from its bundle one a turn; no words about it, never whose it is; every
  one once, where it was first sent, since 2026-10-11; an icon, what the instructor sent first and what an absence already
  holds are left out). The roster's student window shows
  the newest three entries, "Communications…" and "Write to <first name>…" (a new message to the roster address in Mail's compose
  window); the gradebook's student record has "Communications…", whose timeline also offers "Search Gmail now…" and "Write to <first
  name>…" (Mail reads the address from the section's roster: `GET /api/mail/student`). The timeline filters by channel, searches, links
  Mail's own thread route and https addresses only, shows text escaped, and keeps "Search Gmail now…" (the Mail page's live search);
  when Gmail cannot capture yet, its status line links Open Mail. Opened from a student's card, the timeline (and each window it opens)
  offers "Back to <name>" (2026-10-02). Capture now is the only write, and it only asks; every message still goes through Mail's
  review and its explicit Send.
- MASS MESSAGES ARE FOLDED (requirement, 2026-09-30: "there's lots of canvas mass emails polluting the thread, it would be best if those
  could default to a collapsed state that I can expand"). A message the instructor or course staff wrote that reached more than MASS (5)
  students is `mass` (`record._reach`: the students linked to every message with the same writer, subject and words, so a message sent
  one by one, a Canvas conversation or an email per student, counts as one), as are Response Card invitations (everyone the card was
  issued to) and seat-chart emails (everyone on the chart) past the same count; receipts and anything a student wrote never are. The
  timeline shows one folded line (who, "to N students", the subject, the date) that opens to the whole entry, and the student window's
  newest three leave them out. Consecutive class-wide messages fold together into one line ("N class-wide messages · dates") while no
  filter or search is on (2026-10-02: on the live record 21 of a student's 23 entries were class-wide, so their own messages began
  tenth to twelfth); each opens to its own folded line, a search or a channel filter lists every entry on its own. A view: nothing
  recorded changes.
