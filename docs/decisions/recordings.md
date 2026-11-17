## Recordings

Rule ID: **RECORDINGS**. Current binding module contract. Read root `AGENTS.md` first. Proposed 2026-11-12 and binding since the
change that landed STEP 1 (Intake) of `docs/proposals/recordings-plan.md`; STEP 2 (the recording page), STEP 3 (the reading
of coordinator and instructor meetings), STEP 4 (student meetings) and STEP 5 (classes) are built since 2026-11-13, and
STEP 6 (memos, training, search by meaning), the plan's last, since 2026-11-14. Every rule here is built unless it says
otherwise. The plan had three lines for step 6 and no rule; what it is is written under its own heading below, and each
reading there that is mine says so.

- The instructor asked (2026-11-12): "I have a plaud note pro, but I want to build an integration for it in the faculty app. I
  frequently record meetings with the coordinator, other faculty, students, etc and it would be nice to have faculty be able to
  use the recordings/transcriptions and possibly even automate certain things based on those". Asked the same day, they chose:
  Faculty KEEPS ITS OWN COPY of the audio (ended 2026-11-15, under "Plaud" below: the audio plays from the Plaud intake's archive);
  recordings of their OWN CLASSES are included; a student meeting's TRANSCRIPT goes on
  that student's timeline; from a student meeting only the appointment is settled without a card, promises come as a card;
  and Faculty READS EACH FILED RECORDING ON ITS OWN, without asking first ("Yes, on its own").
  A by-hand dry run on the 2026-11-11 all-instructors meeting and an access check on ml-box preceded this contract; the choices
  not listed above are mine and are the instructor's to change.
- ONLY WHAT IS FACULTY'S IS KEPT (requirement, 2026-11-14, of the page listing every recording their recorder holds: "I want
  faculty to only keep recordings that deal with the courses/teaching. the ones that don't belong shouldn't be kept, if it is
  unsure then I should be asked to keep it or not"). Asked the same day, they chose: of a recording Faculty does not keep,
  NOTHING IS SHOWN ("Nothing shown": it disappears, and Faculty remembers only that it is not kept, so it never asks again);
  one Faculty had already kept and they then say does not belong is ERASED ("Erase it"); and Faculty asks about one it is
  unsure of on HOME AND THE SIDEBAR too, not only on the Recordings page ("Home and sidebar too"). The rules are under
  "What is kept, and who owns it" › ONLY WHAT IS FACULTY'S IS KEPT.
- A TRANSCRIPT IS EVIDENCE, NOT AN INSTRUCTION. Plaud mishears (names above all). Everything a recording raises shows the exact
  words it rests on and plays that moment. Nothing outward, and no grade, absence decision or certification, ever follows from
  a recording by itself.
- `recordings/` (`intake.py` what Faculty runs and reads of the Plaud intake, `filing.py`, `storage.py`, `transcript.py`, `service.py`, `api.py`; step 3: `reading.py` what a
  reading is asked and the quote check, `readings.py` the reading and what an item may become, `checks.py` what Faculty's own
  records say; step 4: `meetings.py` a student's meeting as the neighbouring records read it, by reference; step 5: `classes.py` a class
  against its section's class day, by reference; step 6: `memos.py` whose words a memo's are and how a request stands with
  the Assistant, `meaning.py` the passages of kept transcripts found by describing them; migrations
  `recordings/001_recordings`, `recordings/002_transcript_lines`, `recordings/003_readings`, `recordings/004_training`
  (a course link's purpose) and `recordings/005_not_kept` (Plaud's ID of a recording that is not kept, and the one way a
  recording is erased); steps 4 and 5 add none),
  `grading/prompts/recording_reading-v3.txt` (the engine's instructions; v1 and v2 are kept for the runs made with them),
  `grading/prompts/recording_student_meeting-v1.txt` (a student's meeting, step 4), `grading/prompts/recording_class-v1.txt`
  (a class, step 5), `grading/prompts/recording_memo-v1.txt` (a memo, step 6), `static/recordings.js`, `static/recording-page.js`,
  `static/recording-items.js`, `static/recordings.css`, `static/grading-recorded-rulings.js`,
  `deploy/faculty.service.d/plaud.conf`, `deploy/faculty-inbox.service.d/plaud.conf`,
  `tests/fake_intake.py`, `tests/test_recordings.py`, `tests/test_recordings_page.py`, `tests/test_recordings_reading.py`,
  `tests/test_recordings_student_meetings.py`, `tests/test_recordings_classes.py`, `tests/test_recordings_memos.py`, `tests/test_recordings.mjs`, `tests/test_recording_page.mjs`,
  `tests/test_recording_items.mjs`, `tests/test_recording_student_meeting.mjs`, `tests/test_recording_class.mjs`,
  `tests/test_recording_memo.mjs`, `tests/test_grading_recorded_rulings.mjs`, `tests/browser_recordings.py`, `tests/browser_recording_memo.py`, `tests/browser_recording_page.py`,
  `tests/browser_recording_reading.py`, `tests/browser_recording_student_meeting.py`, `tests/browser_recording_class.py`, `tests/fixtures/recordings/tone.ogg` (nine seconds of a synthetic tone, Ogg Opus as
  Plaud's audio is, and a stand-in for the archive's audio).

### Plaud

- FACULTY ASKS PLAUD NOTHING (requirement, 2026-11-15, of one centralized Plaud handler: "the brain should have everything
  and faculty just has the teaching relevant ones, but the brain has it as well"; of the audio: "audio should be stored
  somewhere, just not needed in the app itself"; asked whether Faculty should keep its own copy of teaching audio as it does now:
  "Play from archive"; and for this step, which `docs/proposals/plaud-intake-plan.md` calls "Faculty reads the archive": "build
  step 4"). The Plaud intake (`~/code/plaud-intake`, a program of its own beside Faculty) is the ONE program that talks to Plaud:
  it archives every recording in a folder of its own on `/mnt` (`/mnt/nvme/plaud-intake/`: Plaud's answer, transcript, outline, marks, notes and audio,
  each under its sha256; it deletes nothing; a recording gone at Plaud stays, marked), holds the instructor's sign-in and is its
  only renewer, and is asked through one command that answers one JSON object. Faculty sends Plaud nothing at all: `recordings/`
  imports no network module (`urllib.request`, `http.client`, `socket`), holds no token, no address of Plaud's and no link, and
  runs only that command and reads only the files it names. The instructor still signs in themself with Plaud's own CLI, and the
  sign-in is the intake's alone to hold: Faculty never reads, writes, copies, moves or locks it. THIS ENDS the 2026-11-12 choice
  that "Faculty KEEPS ITS OWN COPY of the audio": a recording kept from now on holds no audio file of Faculty's, and its audio
  plays from the archive.
- THE COMMAND. `FACULTY_PLAUD_INTAKE` (in both drop-ins, `deploy/faculty.service.d/plaud.conf` and
  `deploy/faculty-inbox.service.d/plaud.conf`: `%h/code/plaud-intake/bin/plaud-intake`; the web process runs it to say where a
  recording's audio is and to play it, the Inbox worker for capture) names it; unset, Faculty says "Plaud is not set up for this
  Faculty instance" and runs nothing. It is run as an argument list and never through a shell, with a time limit (60 seconds
  for `list`, `show`, `audio` and `status`, 15 for the page's question of where one recording's audio is, 20 minutes for
  `check-now`), its output read as one JSON object, and with Faculty's environment but no `FACULTY_*` variable. ONLY FIVE
  COMMANDS, EVER: `list`, `show <id>`, `audio <id>`, `status` (these ask Plaud nothing) and `check-now` (one round of the intake's
  own against Plaud, made only on a round the instructor asked for). Never `round` or `copy`, which the intake's timers run,
  and never `renew`, which is the intake's sign-in's. Exit statuses: 0 done, 1 a problem, 2 "Sign in to Plaud again", 64 no
  such command. A command that is missing, fails, is slow, or answers something that is not one JSON object makes the round
  WAIT and say why; an id the archive does not know (`show` answers that it holds no recording with that id) is what a recording
  Plaud no longer had used to be: what Faculty kept stays kept and it is not asked about again. No message Faculty shows or logs
  carries the contents of a file.
- THE FILES IT READS. Every path the intake names must resolve inside the archive folder its `status` names, is opened without
  following a symlink at its last component, is no larger than Faculty keeps (32 MB of words, 2 GB of audio), is an ordinary file,
  is read whole, and has its bytes checked against the sha256 the intake gave. A path outside the archive, or a checksum that
  differs, is REFUSED: it is said in the round's detail and in the recording's own, and nothing of the recording is kept that
  round (a recording already kept stays as it was). The archive is never written, moved, renamed or deleted from by Faculty.
- WHAT A ROUND ASKS. (1) `status` first. (2) On an ASKED round only (Check now, or a recording filed by hand: the rounds the
  instructor asked for), `check-now`, so the listing is as new as Plaud's; if it cannot be made that is said ("Check now could not
  ask Plaud (…)") and the round goes on with what the archive holds, and it is not made while the intake says the sign-in needs
  making again. (3) `list`: every recording in the archive, which Faculty windows by when it BEGAN (the windows under NO PUSH,
  SO A POLL), a recording Plaud no longer has but the archive holds (`at_plaud` false) listed like any other. (4) Filing by the
  calendar, unchanged. (5) `show <id>` only for a recording filed as Faculty's, exactly where Faculty asked Plaud for one
  recording before: nothing of a recording that waits for Keep or Don't keep, or that is not kept, is ever read from the archive,
  and the audio route serves a recording only once it is filed. A fetch copies the transcript, the outline and the marks as
  archived (the bytes, checked) and Plaud's notes REBUILT in Faculty's own form from the archive's (`[{type, tab, content}]` in the
  archive's order, through the same `encoded`, so that notes that did not change hash exactly as the ones already kept), each
  only when its checksum differs from what Faculty keeps, into one bundle and one revision. NO AUDIO IS COPIED. A fetch is
  complete (kept) when Faculty keeps the transcript and the archive holds the recording's audio; until then it waits and says
  what the archive lacks ("The archive has no transcript of it yet.", "The archive has no audio of it yet."), asked again as
  below. A transcript the intake kept a newer version of is the newer one, a new revision.
- WHAT THE ROUND SAYS OF ITSELF is what the intake's `status` says. If its sign-in is not ok, capture's status is `sign_in`,
  its detail begins "Sign in to Plaud again" and gives what the intake says, and the page's attention count says so, as before;
  if the archive is not ready or the command failed, the round WAITS and says which, and nothing can be read; if the intake's last
  listing of Plaud is more than thirty minutes old, the status is `waiting` with one sentence saying that the intake has not
  listed Plaud since that time and that `systemctl --user status plaud-intake.timer` says why. In the first and last of these
  what the archive already holds is still listed, filed and copied, and such a round does not count as a success (the next looks
  back as far as the last that was). `refused_file` stays in the table and is written as '' (no migration).
- THE AUDIO IS THE ARCHIVE'S. `GET /api/recordings/<id>/audio` for a recording Faculty lists and has filed asks the intake for
  `audio <id>`, reads that file (inside the archive, checked against the intake's checksum on EVERY read) and serves it with the
  intake's content type. If the intake cannot give it, and Faculty still holds its own audio file for that recording (a row kept
  before audio played from the archive), that file is served as before, checked against its own checksum; otherwise the answer is
  the existing not-found, with a sentence that the archive has no audio of it. A recording's page names its audio when the archive
  or Faculty has it. The audio Faculty already holds is LEFT where it is: nothing removes, moves or rewrites it, and no tool
  here does; its removal is the instructor's, from a displayed dry run, as STORAGE requires, once each file matches the
  archive's checksum. After Don't keep the route answers 404 as it does (the recording's rows are gone), and the archive is not
  touched.
- NO PUSH, SO A POLL. The Inbox worker's `recordings-capture` thread, under its own lock (never the execution lock), reads the
  intake's archive every ten minutes (the intake itself asks Plaud every ten minutes, so a recording reaches Faculty within about
  twenty), and "Check now" (`POST /api/recordings/check`, which only marks a round wanted; filing a
  recording by hand as Faculty's asks the same way) asks within seconds, by having the intake make a round of its own first.
  HOW FAR BACK (the archive is windowed by when a recording BEGAN, so one synced from the recorder late, or made while capture
  was down, is found only by looking back): every round lists what began in the last three days and never less than back to
  one day before the last round that succeeded, however long ago that was (a lapsed sign-in, the machine off); the first round
  of each campus day and every Check now list THIRTY days, as does the first round ever. The page's line says since when
  ("4 recordings at Plaud since Sun, Nov 8"). A recording that began longer ago than a round looks, and reached the archive
  after Faculty last looked that far, is not found: the daily thirty-day look is the bound, and an older one needs the fallback in
  the plan (Plaud's export). A round is short (at most 12 recordings copied; the
  rest a minute later) and a restart does not wait for it (a `check-now` it was waiting on is cut off; the intake writes each file of its archive whole,
  so none is left half made); an interrupted round leaves an unrecorded folder, swept at the
  worker's start once an hour old (as COMMUNICATIONS). A file Faculty cannot write on this machine (scratch or staging full
  or away) is STORAGE NOT READY, said as that and never as the intake being at fault: the round stops fetching and waits, and
  what was listed and filed stands. Lock order: the capture lock, catalog and calendar reads, the intake's commands and archive
  reads with no lock held, then for each write the roster lifecycle lock, the new bundle's owner lock and ONE catalog transaction.
- TIMES. The archive's `started_at` is UTC (Plaud's own `start_at` carries no zone marker, and the intake writes it as UTC with a Z). Faculty keeps the instant (`started_at`, UTC) and shows the campus
  wall-clock time; matching against the calendar compares campus wall-clock values (FRONTEND). A recording for which Plaud gives
  only the time it received it (`start_known` false) is never filed by the calendar.

### What is kept, and who owns it

- OWNERS. A recording (`recordings`, UNIQUE Plaud id, with immutable `recording_revisions`: what Plaud's list says of it, a fetch,
  a transcript Plaud changed, and a speaker the instructor named, step 2) is WORKSPACE-owned, as a captured message is: one
  recording may concern several owners. What it concerns is its LINKS (`recording_links`, append-only), each belonging to exactly
  one owner: a student's TERM ENROLLMENT (`student_enrollments.id`), a TERM (`course_terms.id`), a COURSE
  (`course_definitions.id`), or a SECTION's class day (`course_sections.id` with the campus date); a Memo is a link with no
  owner below the workspace (and so is `not_faculty`, the link a recording carries only while it is being erased: ONLY WHAT IS
  FACULTY'S IS KEPT, below). A TRAINING (step 6) is a COURSE's link whose `purpose` is `training`
  (migration 004): the same owner and owner key as a meeting filed under that course, the latest revision saying which
  it is. Never `course_groups` or `courses`. For each recording and owner the LATEST row counts:
  `filed` is a link, `withdrawn` takes it back. A recording with no filed link WAITS for the instructor's Keep or Don't keep
  (the API's `needs_filing`). A student's link may carry a start
  and end within the recording (`part_start_ms`, `part_end_ms`; step 4, built): that student's records then show that PART
  alone, and what was said in it is theirs and nobody else's.
- KEPT ONLY ONCE FILED. Before a recording is filed as Faculty's, Faculty holds its list row alone (Plaud id, title, start,
  length). Once filed it keeps the transcript (Plaud's `transaction` block: speakers and times), the outline, the marks made with
  the recorder's button and Plaud's own notes (shown folded as Plaud's words from step 2, never the source of anything), each
  copied from the intake's archive and checked against its checksum. It keeps NO AUDIO (Ogg, about 15 MB an hour): the audio is
  the archive's and plays from it. A recording that WAITS is never read from the archive, and one that is not kept has no row to
  read for. A recording the archive does not hold whole (no transcript or no audio yet) keeps what there is and is asked about
  again after ten minutes, doubling to a day, and at once on Check now. One already kept is READ AGAIN about 1, 6, 24 and 72 hours
  after it was kept, and then no more (four reads; they now ask the archive, not Plaud, and are cheap, but the schedule is
  unchanged): a transcript the archive kept a newer version of is a new revision and starts the four again; the intake failing
  on one of those reads leaves the recording kept and is asked again ten minutes later; the archive no longer knowing it leaves
  what was kept and ends the reads. A transcript the archive changes after the last read is not noticed in this step.
- ONLY WHAT IS FACULTY'S IS KEPT (requirement, 2026-11-14; their words and three choices are at the top of this contract).
  Before this rule a recording that was not Faculty's stayed listed, folded, with Plaud's title, its start and its length,
  and one fetched before it was marked so kept its files ("nothing here erases"). Now:
  - NOT KEPT. A recording that is not kept leaves every table of this module. All Faculty holds of it is ONE row of
    `recording_not_kept` (migration 005): Plaud's own ID for it, who decided (`calendar` or `instructor`) and when. No title,
    no start, no length, and nothing of the calendar entry that showed it to be nothing of Faculty's. Plaud listing it again
    records nothing: it is passed over, never shown and never asked about. The page says only HOW MANY recordings at Plaud
    are not kept.
  - BY THE CALENDAR. A recording that began inside an entry naming no course of Faculty's (Filing, below) is not kept, in the
    round that first lists it: its list row is written and taken away again within that round. Final, as before, so only
    while Google's calendar can vouch for that time.
  - UNSURE: KEEP OR DON'T KEEP. Anything the calendar cannot place WAITS, unfetched, holding its list row (Plaud's title is
    what the instructor decides by). The instructor is asked on the Recordings page, by a card under Needs you on Home
    (`recordings:keep`: a count and a link, never a title or a time) and by the count on the sidebar's Recordings entry. KEEP
    files it (a student, a term, a course, a course's training, a class day, a Memo) and its words are copied from the archive; DON'T KEEP
    (`POST /api/recordings/<id>/discard`) takes it away at the press: nothing was copied, so nothing is asked first.
  - ERASED WHEN IT DOES NOT BELONG. Don't keep on a recording Faculty HAS kept erases it, after one question that says what
    goes and what stays. Every row of it leaves the catalog in ONE transaction (`RecordingService._erase_rows`: revisions and
    links, the transcript's lines, readings, items and what became of them, its files' and bundles' rows), and its FILES
    leave both drives through the storage engine (STORAGE, below). The recording is hidden AT ONCE (it carries a
    `not_faculty` link from the press: listed nowhere, its page and audio 404, not searched, not filed back) and erased
    under the capture lock, by the press itself when no round is running, else by the round the press asks for, so nothing
    is fetched or read for it meanwhile. Before its rows go, every Approve card made from it that still waits is taken back
    ("This recording is no longer kept.") and a memo's runs are withdrawn (`Readings.gone`). One whose erasure fails is
    rolled back whole (its rows and the erasure's name), stays hidden, is said in the log and tried again each round, and
    does not hold back the others. A request ID is one recording's: the ID that erased or filed one is refused for another.
    WHILE A DRIVE STILL HOLDS ITS FILES (it was away, or the nightly backup was running) the page says so in orange with
    the storage engine's reason ("The files of 1 erased recording are not removed yet …"), until a later round has removed
    them: "erased" is not said of what is still on a drive.
  - WHAT STANDS. What the instructor approved or pressed out of it is theirs and stays: a calendar entry, a student's
    commitments and note, a moved topic, what they wrote into a rubric, and a memory kept by Remember (a copy of words
    they edited, which names no recording). A follow-up commitment that named one of its items simply no longer says where
    it was heard. What goes with the recording is Faculty's own record of it: that an item was done or dismissed, and that
    a ruling was accepted or declined at a rubric's finalization (a ruling still held for a rubric is no longer offered).
  - ASK AGAIN (`POST /api/recordings/ask-again`, "Ask me again" beside the count). Every recording that is not kept is
    marked `ask`. Faculty holds no date of any of them (only when it was decided, which may be months after it was made),
    so the round this asks for lists the archive two years back (`ASK_BACK`) and, of what began before its usual window, takes up ONLY the recordings asked about: nothing else that old
    is brought in. Each one the archive lists gets its list row back and WAITS (one Plaud no longer has but the archive holds is listed, and CAN be
    asked about and kept); one the archive does not list is `not_kept` again (only
    those that round looked for: one asked about while it ran is looked for by the next). The calendar decides NOTHING for one asked about: not that
    it is not Faculty's a second time, and not that it is (one the instructor chose not to keep is theirs to bring back,
    never kept again by itself). Kept, its `recording_not_kept` row goes; not kept, it is `not_kept` again; one the archive
    does not list stays not kept. Keeping it again copies it from the archive anew and reads it anew: nothing of the earlier
    reading exists.
  - THE ASSISTANT has no use of Don't keep or Ask me again (`assistant/policy.py` NEVER): what Faculty holds is the
    instructor's decision. `file` no longer takes Not Faculty's.
  - RETAINED, WITH ONE WAY OUT. Every table of a recording is still append-only and refuses a DELETE (the triggers of
    migrations 001 to 003, replaced in place by 005), except inside the erasure of one whole recording, which names that
    recording in `recording_erasing` for the length of its deletes and clears the name before it commits. Rows that carry
    their recording are checked against the one named. A term's coordinator is not a recording's and is retained as ever.
  - ONE MARKED NOT FACULTY'S BEFORE THIS RULE (a `not_faculty` link beside its list row; live held two, never fetched) is
    erased by the first round after the change, as the instructor's choice now says.
  - KNOWN LIMITS. (a) The Assistant's own record is append-only and is not touched: a card made from the recording, taken
    back or approved, still holds the words it quoted, and a memo's run its title (up to 120 characters of what was asked).
    (b) Nightly catalog backups made before the erasure hold its rows until retention prunes them (STORAGE: by the
    instructor's `prune`, never automatically), and a catalog restored from such a backup names files that are gone.
    (c) The search index holds vectors, never words; an erased recording's are dropped at the worker's next turn.
    (d) Nothing is asked of Plaud or changed in the archive: the recording stays in both, in the instructor's archive and at Plaud, and Faculty never deletes at either.
    (e) A recording the calendar wrongly showed to be nothing of Faculty's (a student met during an entry named "Lunch") is
    not kept without a word; the count and Ask me again are the only way to it.
- STORAGE. Files live in a new owner of the verified storage engine, `recording_bundles` (directory `recordings/` on both
  drives, registered in `CourseStorage`: reconcile, nightly backup, status, retention, `manage_storage.py verify`), with the
  lifecycle COMMUNICATIONS gives `comm_bundles`: staged on NVMe, backed up and verified nightly, then kept on the HDD alone,
  every read checksummed (`recording_files`: one file for each role in a bundle, `transcript.json`, `outline.json`, `marks.json`,
  `notes.json`, and `audio.ogg` only in a bundle kept before audio played from the archive: a new bundle never holds one). A
  bundle is the files ONE fetch kept, written at once and never changed. Roster lifecycle lock, then
  the bundle's owner lock, then ONE catalog transaction. Nothing is downloaded; what is copied is read whole from the archive. Rows are append-only (triggers) except the
  storage columns and the capture state (`recording_capture_state`, `recording_fetch_state`: runtime state, not a record); they
  go into the nightly catalog backup and never the generic export. Migration `recordings/migrations/001_recordings.sql`, with its
  adoption test. ERASING (ONLY WHAT IS FACULTY'S IS KEPT; migration `recordings/migrations/005_not_kept.sql`, with its
  adoption test): the transaction that erases a recording's rows names its bundles in `recording_erased_bundles` (runtime
  state); `RecordingStorage.erase` then removes each bundle's NVMe folder and its HDD folder with every snapshot, under the
  roster lifecycle lock and the bundle's owner lock, a redirected folder refused before anything is removed; the row goes
  once the files have. NEVER WHILE THE NIGHTLY BACKUP RUNS (it is asked for the `nightly-backup` lock without waiting: the
  backup's first pass copies a bundle to the HDD under no lock of the bundle's, so a folder removed meanwhile could be
  written back). A DRIVE THAT IS AWAY: what is on the drive that is present is removed, the bundle stays named, and each
  round tries again. No final copy is made first and nothing is verified: not keeping it is the point (STORAGE names this
  as the one removal outside its retention rule).
- THE LINES OF A KEPT TRANSCRIPT (step 2; migration `recordings/migrations/002_transcript_lines.sql`). A kept transcript file is
  read ONCE into `recording_lines` (its place, start and end in milliseconds, who Plaud says spoke, the words), in the
  transaction that keeps it; one kept before step 2 is read by the capture round from verified storage, checked, at most five a
  round and whatever Plaud says that round. The kept FILE stays the record; the lines are its copy in the catalog, so the page
  and a search need not read storage. They are keyed by the file, which never changes: a transcript Plaud changed is another
  file with its own lines, and only the latest revision's are shown or searched. Append-only (triggers); workspace-owned
  through the recording. A transcript not in Plaud's shape (a list of `{start_time, end_time, speaker, original_speaker,
  content}`, as the real ones were seen to be, structure only) is said to be unreadable and its file kept; nothing is guessed.
- READINGS AND ITEMS (step 3; migration `recordings/migrations/003_readings.sql`, with its adoption test). A reading is a row of
  `recording_readings` (a RUN: the transcript file read, what the recording was filed under then, the engine, the checksums of
  the instructions and of what was sent, where the meeting began, what the quote check dropped; `read` or `failed`); an item
  is a row of `recording_items` (its identity within the recording: its kind and quote); what a run said of it is
  `recording_item_readings`; what became of it is `recording_item_events` (done, dismissed, reopened, remembered, carded,
  accepted, declined; each with the request ID and who made it; step 4: a `carded` event of a student's card names the card,
  `commitments` or `note`, and the student it was made for, and every item the one card was made from carries it; step 5: a
  class's one card is `move`, naming the section and the class day, carried by the `covered` items it rests on). All WORKSPACE-owned through the recording, append-only
  (triggers), in the nightly catalog backup and never the generic export. They hold words that were said, the instructor's
  included, and no file. A term's COORDINATOR (`recording_coordinators`: a name alone, revisions, an empty name takes it
  back) is TERM-owned (`course_terms.id`); the coordinator on the term's course card stands in its place wherever there is
  one (COORDINATOR "ONE NAME", 2026-11-15). `recording_reading_state` is runtime state, not a record (when a recording is read
  next, and a Read again that waits).

### Filing

- BY THE CALENDAR, WITHOUT A CARD. A recording is filed by where its start falls (ten minutes of grace before a start), read
  from the one calendar feed and the one engine (SCHEDULING "ONE FEED", "ONE ENGINE"):
  inside a student's APPOINTMENT (`student_support_items`, kind appointment, not cancelled) → that enrollment, with the
  appointment by reference; inside a section's CLASS meeting as the Calendar shows it → that section's class day; inside a
  calendar entry (Faculty's or Google's) whose title names a current term's course code → that term; inside an entry that names
  no course of Faculty's → NOT KEPT (ONLY WHAT IS FACULTY'S IS KEPT, above: nothing of it is held). Calendar entries hold
  no attendees, so the title is all the evidence there is.
  (Dry run: four of four recordings fell within ten minutes of an entry.) A link made by the calendar records `calendar` and
  what the calendar showed: the entry's id, source, date and times, and its title where it names something of Faculty's.
  Never an appointment's words (they say what the student came about). Of the entry that showed a recording to be nothing
  of Faculty's, nothing is recorded at all.
- HOW THE RULES ARE READ (built in step 1; these readings are mine and the instructor's to change):
  - AN ENTRY NAMES A TERM by its title holding a current term's course code ("MATH 140", "math140", "Math-140"), or, for one of
    Faculty's own entries (an exam sitting, a custom entry scoped to the term or the course), by belonging to a current term.
  - OFFICE HOURS WITH NO APPOINTMENT wait for the instructor ("During office hours with no appointment. Pick the student."):
    the block says a student came by, and nothing says who. Read literally the rules would have had it not kept.
  - An appointment and a class meeting at one time DISAGREE; a term entry beside either agrees when it is the same term; an
    entry naming nothing of Faculty's beside one that does is ignored. A title naming two current terms waits.
  - A DR booking and a due date are not places the instructor is and file nothing; nor does an entry that only says the
    instructor is AWAY ("Unavailable", "Out of office", "Office hours cancelled": the engine's rule): by itself it leaves the
    recording waiting as "no entry". An all-day entry never counts; an entry with no end lasts an hour (the engine's rule).
  - NOT KEPT NEEDS THE WHOLE CALENDAR. An automatic Not kept is final (nothing of the recording is held after it, and it is
    never asked about), so it is made only while Google's calendar can vouch for that time (SCHEDULING "GOOGLE CHECKED").
    While it cannot (not connected, not read lately, or its copy unreadable), the recording waits for the instructor and says
    so ("The only calendar entry at that time names no course of yours, but Faculty has not read your Google Calendar since
    ..."), and is decided again each round that lists it.
- WAITING: KEEP OR DON'T KEEP (the API's `needs_filing`). No entry, or two that disagree: the recording waits, unfetched,
  for the instructor to KEEP it, saying what it was (a student, by the STUDENT-NOTES picker: names, never UINs; a term; a
  course; a course's training, step 6; a class day; a Memo: `POST /api/recordings/<id>/file`), or to say DON'T KEEP
  (`POST /api/recordings/<id>/discard`). The page says why it waits, and says so when Google's calendar has not been read
  lately (SCHEDULING "GOOGLE CHECKED"). Each round that lists it tries the calendar again, so an entry that arrives later
  files it (or shows it to be nothing of Faculty's), except one the instructor asked to be asked about again, which only
  they decide. A filing by hand is never undone by the calendar. Filing by hand under the owner a recording is already filed
  under is no change: no new link, and a student's appointment by reference stays.
- RE-FILING is a new link revision: the old owner's link is withdrawn and the new one filed, in one transaction. (Step 3: what
  the old filing raised and nobody answered is withdrawn; what was approved stands.)
- SEVERAL STUDENTS IN ONE RECORDING (step 4, built; `POST /api/recordings/<id>/students`, the page's "Students and parts…").
  The instructor names the students a recording is of, the whole list at once, each with their part's start and end. ONE
  student may have no part (the whole recording is theirs, as a filing by the calendar or by hand leaves it); with several,
  every student has a part, no two overlap, and none ends after the recording does. Each student's link is a new revision
  where it changed, and keeps by reference the appointment it was filed by; a student left out is withdrawn, and so is a
  filing under anything else (a re-filing). An item is the student's IN WHOSE PART ITS QUOTE BEGINS; one said in nobody's
  part is nobody's and is shown on the recording's page alone. No student's timeline, follow-up, note or facts are given
  another student's words. Changing who the students are reads the recording again by itself (it is told who they are);
  moving a part does not, and cards that waited are taken back and made again for the parts as they are. A student who
  has a part KEEPS it when the recording is filed under them again by hand (File elsewhere): the other students are
  withdrawn and their parts become nobody's, never that student's; the whole recording is one student's only where the
  instructor says so here. A card that waited for a student who no longer owns its items (a part moved, a student
  withdrawn) is taken back.

### The reading (step 3)

- FACULTY READS THE TRANSCRIPT ITSELF, with the Assistant's engine. Plaud's summary is never read for this (dry run: it missed
  three of five answers given to the instructor and called a meeting schedule unclear that the calendar settles).
- ON ITS OWN (requirement, 2026-11-12, asked "Should Faculty read each filed recording on its own, without asking first?":
  "Yes, on its own"). A recording is read once it is filed as Faculty's, a class recording included, with no card.
  BUILT IN STEP 3 FOR MEETINGS: a recording filed under a TERM or a COURSE is read (a coordinator or instructor meeting).
  BUILT IN STEP 4: one filed under a STUDENT is read, by its own instructions (Student meetings). BUILT IN STEP 5: one filed
  on a section's CLASS DAY is read, by its own instructions (Classes). BUILT IN STEP 6: one filed as a MEMO is read, by
  its own instructions (Memos). A TRAINING is kept, shown and searched and is NOT read: its page says so (Training).
- WHEN. In the capture thread, under the capture lock, after a round has said what it says of Plaud: at most two recordings a
  round (`readings.READINGS`), one asked for first, then the newest. A recording is due when its latest transcript is read into
  lines and no reading of THAT transcript, filed where it is NOW, exists: so a transcript Plaud changed, and a recording
  filed elsewhere, are read again by themselves. READ AGAIN (`POST /api/recordings/<id>/read`) asks the worker for a new run,
  made within seconds and without asking Plaud anything (one asked for while a reading is being made is made next); nothing
  else reads a recording twice (a named speaker, a named coordinator and a new version of the instructions do not: Read
  again does).
- WHAT IS SENT: ONE request to the Assistant's own connection as Compute settings name it (`grading.config.assistant`, through
  the one seam `cloud_provider`), holding the instructions by name (`recording_reading`), the transcript's lines (each with its
  number, how far in it is and who spoke) and the facts below, as a follow-up's messages go. The audio never leaves the
  machine. One answer now, never a batch job collected later (`durable` false); a hosted or subscription model's request has
  no time limit (GRADING, 2026-09-23). While the Assistant is off (no model chosen) nothing is asked and the page says so; it
  is looked at again hourly.
- FACTS, each read through its owning service and never copied: the recording's day; which speaker is the instructor (the ONE
  speaker whose name shares a word with the workspace's own name, `settings.teacher_name`; none or several: Faculty cannot
  tell, says so on the page, and the instructor names themself among the speakers and reads again); the term's coordinator;
  the term's sections and their class days; the calendar three weeks either side, NARROWED (my choice, the instructor's to
  change): Faculty's own exams, deadlines and entries and the Google entries of this course (the meeting's own series, an entry
  naming a current term's course), never a class meeting, a student's appointment, a makeup, a testing center booking or any
  other Google entry; the assessments (the term's exam records, then the course's other assignments, dated or not, as the Assignments page lists them under the term) and their given dates, each under a short name the answer
  must use; the NAMES of the modules, pages and assignments in Faculty's copy of the Canvas course (so an item about Canvas
  names the thing as the copy does; names of course material only). For a STUDENT'S MEETING (step 4, built, and NARROWER than
  first written here, which said "what a follow-up preparation is given about that student": my choice, the instructor's to
  change): each student's name and section as the roster has them, the appointment the recording was filed by (its day,
  times, purpose and how it is recorded), and, where the recording holds several students, the lines of each one's part;
  with the term's sections, assessments and calendar as above, and nothing of the coordinator or the Canvas copy. No grade,
  note, absence, accommodation or message of the student's is sent: a reading has to tell who is speaking and what a
  promise refers to, and a follow-up preparation's facts are written as instructions to another task. A recording filed under
  a course takes the course's only current term for these, or none. For A CLASS (step 5, built; my choice, the instructor's
  to change): the recording's day, which speaker is the instructor, the course and the section, the course's notes sections
  by number and title (`course_notes.service.current_sections`), and the TITLES of that section's work from three weeks
  before to six weeks after the day, as its class schedule lists it, each under a short name. Never a date Faculty holds
  (the reading says what was SAID; setting it against the records is code's), never the day's planned topics (a reading
  told the plan would find it), no calendar, no coordinator, and nothing of any student. For A MEMO (step 6, built; my
  choice, the instructor's to change): the recording's day and time, which speaker is the instructor, the voices Plaud
  heard and the course codes of the current terms. No record of Faculty's at all: the reading says what was ASKED, and
  what a request means is the Assistant's to look up.
- FEWER, MERGED ITEMS (instructions v2, 2026-11-13). The first live readings, with v1, gave 105 items for the 2026-11-11
  meeting (46 under Needs you, several of them the same task said again) and 35 for the 2026-11-10 one, where the by-hand dry
  run had about 13 for the instructor: a wall (DESIGN-LANGUAGE "No walls of text"). v2 says what an item is NOT (what was only
  discussed, background, advice), asks for 8 to 20 items an hour apart from rulings, one item for one task however often it
  was said, a `todo` only for an action that can be ticked off, and the gradebook only for dropped scores. Whether that is
  enough is seen on the readings made with it; a reading is never trimmed by code. (Read again with v2, the two meetings
  gave 13 and 9 items for the instructor.) Instructions v3, the same day, change only how a ruling names its assessment
  (A RULING › HELD BY NAME).
- AN ITEM is one sentence, its QUOTE (exact words, speaker, start and end), whom it is for (you, the coordinator, others,
  unclear), a date where one was said (weekday words resolved against the recording's day, or left unclear) with its clock
  time, whether a dated thing is happening, and what in Faculty it concerns (an assessment and question, an assessment's
  rubric and whether it was said to be final, its seating, its testing center count, a Canvas module or page by its words, the
  calendar, the gradebook), kept only where the facts named it. Kinds: `todo`, `answer` (the reply to a question the instructor
  asked), `date`, `ruling` (how something is graded), `policy` (a standing rule), `waiting` (someone else owes it), and for a
  class (step 5, built) `covered` and `announced` and no other (Classes). A memo (step 6, built) has `todo`, which in a
  memo is a REQUEST (what the instructor asked to have done), and `policy`, and no other (Memos).
- THE QUOTE CHECK (`reading.checked`: code, never a model). An item is kept only when its quote is in the transcript word for
  word (spaces apart), BEGINNING IN the line it names (a quote found only in a later line is not at that line: it would play
  the wrong moment); it may run on over at most four lines of the same voice, and is at least three words. Who spoke it and when are then taken from the transcript, never from the model. Anything else is
  dropped and recorded as dropped with the reason, and the page lists it folded. No item is raised from talk before a meeting
  begins (the reading says where it begins). At most 120 items a reading, and at most 100 dropped ones recorded.
- A reading is an immutable RUN (engine, prompt and input checksums, items). Read again, or a changed transcript, is a new run;
  an item keeps its identity by its kind and quote, so an answered item stays answered. An engine that could not be asked or
  did not answer makes NO run: why is said on the page, and it is tried again after ten minutes, an hour, six hours, a day,
  and after five tries only on Read again. An answer Faculty cannot use is a `failed` run, retried the same way; so is a
  reading Faculty itself could not keep (said on the page; never asked about every round).
- CHECKED AGAINST FACULTY (`recordings/checks.py`), by code and never by a model: where an item concerns something Faculty
  holds, the page says what Faculty found (the classrooms' seating charts for that assessment built after the last drop
  Faculty has on record: Faculty holds no drop DATE, only when each drop was recorded; whether the gradebook drops any lowest
  score; a module, page or assignment of that name published in the Canvas copy, and when no name holds every word the two
  closest names, with nothing claimed; the testing center's bookings for that assessment by day; a MEETING of the course on
  the calendar within 45 minutes of the time said, or, with no time said, one that day NAMED LIKE IT).
  NAMED LIKE IT: with no time said, an entry is the thing only when its title holds more than half of the words the reading
  called it by (or of the title of the assessment it is about; the course's own code apart, a word and its plural one word).
  Another entry of the course on that day is not the thing (second live reading: a dated item was "already done" because
  three homework deadlines fell on its day): the page says nothing named like it is on the calendar that day. With neither a
  time nor a name, the day's entries are shown and nothing is claimed or offered.
  A meeting said NOT to happen lists the course's meetings the calendar still holds (that day, or the rest of the meeting's
  own series when all were called off) and changes nothing. ONLY A MEETING COUNTS, in both directions (an exam or another
  entry of the course; `checks.MEETINGS`): a class meeting, office hours, time away, an appointment, a makeup and a testing
  center booking are never the thing a meeting spoke of (first live reading: a called-off meeting listed that day's four
  class meetings and office hours). A class event made by hand for one section ("<code> · Section N", a redefined day) is a
  class meeting too (`checks.class_meeting`, the rule step 1 files a class by; second live reading). Where nothing is left to remove but the course has classes that day, the page says they
  were not looked at and the item stays with the instructor: it may be a class that is off (step 5). "Already done" is a view, recomputed at each read; nothing is saved.

### What an item may become

- WITHOUT A CARD: capture, filing by the calendar, showing a recording where it is filed, reading a kept transcript into
  lines and searching them (step 2); from step 3 the reading, and what the page shows of it.
- AN APPOINTMENT IS SETTLED ALREADY. A passed appointment reads as Attended (TEACHING-WORKFLOWS "MEETINGS COMPLETE
  THEMSELVES"), so a recording saves no outcome. RECORDED AS ONE THAT DID NOT HAPPEN (step 4, built): a recording filed
  inside an appointment that is recorded as cancelled or a no-show is MARKED IN ORANGE on its page and its row ("Their
  2:00 PM appointment that day is recorded as a no-show, yet this recording began in it"), counts in the page's attention
  (`appointments_off`), and changes NOTHING: neither the appointment nor the filing. Correcting the appointment, or filing
  the recording elsewhere, is the instructor's and takes the mark away. A no-show is on the calendar and files as any
  appointment does. A CANCELLED appointment is not on the calendar: filing reads it by reference as the WEAKEST evidence
  (`student_support.service.cancelled_events`), so a recording that began inside one is filed under that student only
  where NOTHING ELSE was on the calendar at that time (any other entry decides as if it were not there; two cancelled
  appointments at once disagree), and only while Google's calendar can vouch that nothing else was (else it waits and
  says so, as an automatic Not kept does).
- AN APPROVE CARD, through the Assistant's own actions and the owning module's route (an unnamed write is `approve`).
  BUILT IN STEP 3: A CALENDAR ENTRY. After a reading, each item for the instructor that says something HAPPENS on a day,
  today or later, which the calendar does not hold gets ONE card proposing a Faculty entry (`POST /api/calendar/events`:
  the entry's name as said, the day and time, the term, the quote in its notes), raised as every background proposal is
  (`_proposal_only`, engine `recordings`, its page the recording at that moment) so it waits for Approve although the
  Assistant's own calendar entries are automatic. Once for an item: a card declined or lapsed (a day) is proposed again only
  by the instructor's press on the page (`POST /api/recordings/<id>/items/<item>/card`); a recording read again keeps the
  card; one filed elsewhere has its waiting cards taken back, and is proposed again where it is read. BUILT IN STEP 4, for
  a student's meeting (Student meetings): ONE card for what was promised, on that student's follow-up, and ONE for a
  private note. A day said in a student's meeting is NOT proposed as a calendar entry (a custom entry would carry a
  student's name to the calendar and to Google): it is the check-back day of its commitment. BUILT IN STEP 5, for a
  class (Classes): ONE card proposing that what the class did not reach of its day's planned topics moves to that
  section's next class day, through the class schedule's own route; a day told to a class is set against the records
  and is never proposed as a calendar entry. NOT BUILT: a reply (a follow-up preparation drafts replies, with the meeting among its facts, and
  Mail's explicit Send sends them); an appointment made from a day that was set; an edit of the Canvas copy (no step has
  it; the push stays the instructor's).
- WHAT BECAME OF AN ITEM is the instructor's own press on the page: Done, Dismiss, Open again
  (`POST /api/recordings/<id>/items/<item>`). An event; nothing else in Faculty changes.
- NEVER FROM A RECORDING: a score or grade, an Excused or Unexcused (what a student said is evidence only), a certification,
  a send, any write to Canvas, Gradescope, AIM, Gmail or Plaud. The instructor's own Google entries are never edited
  (CALENDAR): meetings said not to happen are listed for them.
- A MEMORY (ASSISTANT-MEMORY "SAID IN A RECORDING", the fifth named exception to ONLY THE INSTRUCTOR'S OWN TYPED WORDS). A
  `policy` item is kept only when the instructor presses Remember on it, in the words shown, which they may edit first
  (`POST /api/recordings/<id>/items/<item>/remember`); source `recording`, with the recording and quote as provenance (they
  stay with the item, whose `remembered` event names the memory). Once for an item while that memory stands (opened again or
  not; removed on the Memory page, it may be kept anew). Never by itself, never naming a student (the memory's own
  roster check and decision host check apply: A RULE SAID OF ONE STUDENT'S CASE, EVERY WAY IN), and term-scoped unless they
  widen it to the course or everything. The Assistant cannot use the route (`never`).
- A RULING (GRADING "RULINGS SAID IN A RECORDING"). A `ruling` names its assessment and question and waits on that
  assessment: its rubric's finalization window lists each ruling of the latest readings of the recordings filed under that
  term or course, with its quote and where to hear it (`GET /api/recordings/rulings?assignment=<graded assignment>`), and the
  instructor adds it to a rubric line's clarification, theirs to edit and recorded only by their Confirm, or declines it
  (`accepted`, `declined`: events of the item). Never applied by itself, never offered or accepted on a certified plan. Words
  that a rubric is not final (the latest thing said of that assessment's rubric) show as a warning where its rubric is
  imported: in Upload rubrics and in Import rubrics from Gradescope.
  HELD BY NAME (2026-11-13; instructor, of four rulings of the 2026-11-11 meeting that named no assessment, a paper kept on
  Canvas and the final exam, listed as "Final": "Yes, build that"). A ruling, or words that a rubric is or is not final, of
  an assessment the reading has no id for keeps the NAME the meeting called it by (`about.words`, with `assessment` empty;
  instructions v3: an assessment the facts list under a usual form of its title takes its id, any other gives its name, and
  a ruling always has one or the other). By code and never by a model (`reading.named`), at each ask and so also for an
  assessment made after the meeting: the name is an assessment of the term when it holds EVERY word of that assessment's
  title and those words are at least half of the name (the course's code and words like "the" apart; a word and its plural
  one word; a number only itself, said as a word or a figure: "exam three" is "Exam 3"), and of several only the longest title ("final exam" is "Final"; "quiz 2" is "Quiz 2",
  never also "Quiz"; "final exam review" is neither). At that assessment's rubric it is offered like any other ruling and
  says "Said of “<name>”"; a plan the name is not is refused it. The recording's page says where it is held ("Held for
  “Final”: matched by the name it was called by") or that no assessment of the term is called that yet, in which case it
  waits and nothing is guessed. THE TERM'S ASSESSMENTS, for a name as for the reading's facts, are its exam records and
  the course's other assignments, by reference and whether or not the term has given them a date (`Readings._assessments`;
  third live readings: the final exam was the course's "Final" with no date yet, so "final exam" named nothing). Only what
  is said of GRADING is held this way: a seating chart, a booking, a task or a date
  of an unlisted assessment has no record to look in by a name.

### Student meetings (step 4)

Built 2026-11-13 (instructor: "build 4"). Their choices of 2026-11-12 stand: a student meeting's transcript goes on that
student's timeline, and from a student meeting promises come as a card. The other choices below are mine and the
instructor's to change. No real student meeting had been recorded when this was built: the first one must be looked at.

- A STUDENT'S MEETING IS READ BY ITS OWN INSTRUCTIONS (`recording_student_meeting`, run version `student-meeting-v1`), for
  what the meeting leaves to be done or remembered: `todo` (what the instructor said they WILL do for this student),
  `waiting` (what the student said they will do), `date` (a day that was set between them) and `answer` (what the student
  TOLD the instructor that may be needed later, written as "They said …": what a student said is evidence, never a
  finding). Never a `ruling` or a `policy`: one student's meeting sets no rule for the course, and the quote check drops
  one and says so. The same answer shape, the same quote check, the same runs and items as any reading. Its items are
  not looked up in the course's records and raise no calendar card.
- ON THE TIMELINE BY REFERENCE (COMMUNICATIONS "FACULTY'S OWN RECORDS"; `recordings/meetings.py` `timeline`): one entry
  for each recording a student is filed under, "Meeting", dated when their part began, saying how long it ran and, in
  plain lines, what the latest reading kept of THEIR part (what the instructor said they would do, what the student said
  they would, the days that were set, what the student told them), and opening the recording at that part. An item the
  instructor DISMISSED is the instructor saying it was not so: it is on none of the student's records. Filed elsewhere,
  the entry is gone. Never a file, never exported, never sent; no address carries the student.
- THAT PART ALONE. With several students the entry opens `#recordings/<id>?t=<start>&to=<end>`: the page then shows that
  stretch alone (its lines, what was said in it, the one student, the audio stopping at its end; never what the quote
  check left out, which carries no place), with the way back to the whole recording.
- PROMISES HEARD BECOME ONE CARD proposing commitments on that student's follow-up (TEACHING-WORKFLOWS "SEPARATE
  COMMITMENTS"), opening a follow-up where none is open. After a reading, the student's open, untracked `todo`,
  `waiting` and `date` items become one Approve card, raised as every background proposal is (`_proposal_only`, engine
  `recordings`), through the follow-up's own route: on the student's open follow-up (the one changed last when several
  are open) it sends what that follow-up's own page would (its title, state and words as they stand, the student's
  messages it has seen, the revision it was read at, every commitment it holds, then the new ones), so a follow-up that
  changed meanwhile REFUSES the Approve and nothing is written; with none open it creates one, "Meeting on <day>". A
  commitment is the item's sentence, who owes the next step (`you`, or the `student`), the day that was said where one
  was (a `date` item is a `check_back`; the instructor records the day by approving), and `recording_item_id`, the item it
  was heard in: its identity is the item's, so one promise is never tracked twice, and the follow-up's own save refuses a
  commitment heard in an item that another follow-up of the student already holds (so two cards made in the same instant
  by the worker and a press cannot both be approved). The card never says more than it does: a follow-up's state is sent
  as it is saved, except that a student message still needing a reply stays Needs reply (the save also records what the
  follow-up has seen), and one already Waiting on the student is left Waiting (ASSISTANT "WAITING AFTER THE REPLY" ›
  ALREADY WAITING). What a student's OWN follow-up holds is "Already done" on the recording's page and is not proposed
  again.
- A PRIVATE NOTE (STUDENT-NOTES), ONE card: what the reading kept of their part, in the same plain lines, dated the day
  of the recording, saved only by Approve.
- ONCE. Each card is proposed once for a student of a recording: one the instructor declined or let lapse is proposed
  again only by their own press on the page (`POST /api/recordings/<id>/cards`); one Faculty itself took back (an item
  the newest reading no longer holds, a part that was moved, a recording filed elsewhere) is made again. Promises a later
  reading added join the card that still waits: it is taken back and one card made for all of them.
- FOLLOW-UP FACTS (ASSISTANT; `meetings.facts`). A follow-up preparation's facts carry the student's latest meeting
  items, at most ten, newest meeting first, each with the day, who owes it and the exact words it rests on, as they carry
  the instructor's notes; they do not make a preparation outdated. They were said with the student present: a drafted
  reply to that student MAY refer to what was said in their own meeting, and NEVER to another recording or another
  student's part; and, unlike the notes, they are given to the draft check too, so a reply keeping a promise made in the
  meeting is supported.

### Classes (step 5)

Built 2026-11-13. The instructor's choices of 2026-11-12 stand: recordings of their own classes are included, are filed on a
section's class day and never to a student, and are read on their own; a memory comes only from their own Remember. The
three rules first written here are built as they stood; every other reading below is mine and the instructor's to change.
The two class recordings Faculty already kept (2026-11-13, sections 507 and 508) were looked at read-only for their SHAPE
before this was built (about 200 to 300 lines, 14,000 to 20,000 characters each; sections said as words, "five eight"); no
model read them, and nothing of them is in the repository.

- A class recording belongs to its section's class day and to no student: it makes no enrollment link, appears on no
  timeline, and raises no item about one student, whoever is named or heard. (Filing on the class day is built in step 1.)
  HOW THAT IS HELD: a class is read by its own instructions (`recording_class`, run version `class-v1`) for TWO kinds of
  item and no other, neither about a person; the quote check drops any other kind and says so, drops talk before the class
  begins, and drops an item whose sentence holds a current student's WHOLE name or a UIN (`assistant.memory.roster_check`,
  strong; a given or family name alone is not judged, since notes titles and ordinary words are spelled like some), and a
  day told to the class whose QUOTE holds one (it was said to that student); of an item dropped for a name neither its
  sentence nor its quote is recorded, since what was left out is shown too. Its facts carry no roster; a roster that
  cannot be read leaves this check off for that reading, and the two kinds still hold. No memory can be kept from it (Remember takes a `policy`, which a class never has), no ruling is
  held, and no calendar entry is proposed.
- COVERED AGAINST PLANNED. `covered` names notes sections by NUMBER, as CLASS SCHEDULE topics do. The class day shows what
  was reached beside what was planned, orange where they differ, and a card proposes moving what was not reached to the next
  class day. The plan never changes by itself.
  HOW THE RULE IS READ (`recordings/classes.py`: code, never a model):
  - A `covered` item is ONE notes section the class WORKED THROUGH that day, resting on the words where the instructor
    names or begins it. A section the class started counts; one only named as coming another day, a later chapter's
    preview and a section named as being on an exam do not. Only a number the course's notes have is kept (with no notes
    outline in Faculty, any number written like one); an item naming none is dropped. A `covered` item the instructor
    DISMISSES was not so: its section is no longer reached.
  - PLANNED is the section's topics for that day (its own entry, else the term's). REACHED is what the readings of the
    recordings filed on that section's day kept. THEY DIFFER where a planned section was not reached that day NOR on an
    earlier recorded class day of the section. What was reached beyond the plan, and what was reached earlier, is shown
    plainly and is no difference: nothing of this day needs moving for it.
  - SET AGAINST NOTHING: a day with no topics planned shows what was reached and is never orange; a reading that kept no
    `covered` item says so and proposes nothing (a card must rest on words).
  - THE MOVE is ONE Approve card for a section's class day, made after the reading, raised as every background proposal
    is (`_proposal_only`, engine `recordings`, its page the recording), through the class schedule's own route (`POST
    /api/scheduling/terms/<term>/class-schedule`, with its preview and revision check): that SECTION's own topics for the
    day lose what was not reached, and its own topics for its next class day begin with it; the term's entries and the
    other sections' stay as they are. A day left with nothing keeps a note saying where its topics went. A move the
    class schedule would refuse is never proposed, and the page says why: the last class day has no later one, and a next
    class day that would then hold more than its 8 topics is the instructor's to thin first. Either is still orange.
  - TWO RECORDINGS OF ONE CLASS (the recorder started twice) are one class day: what both reached is the day's, the day
    has ONE card and counts ONCE, both on the first recording whose reading kept a section, and either recording's
    reading may raise or replace the card.
  - ONCE. A card the instructor DECLINED is their word that the plan stands: the day is no longer orange, and the move is
    proposed again only by their own press (`POST /api/recordings/<id>/cards`, `kind` `move`), as one that lapsed is. A
    card that still waits and no longer says what Faculty would propose (the plan changed since: another section's move
    was approved, the topics were edited; a `covered` item was dismissed) is taken back and made again as the move stands
    now, at once for a settled item and otherwise in the next capture round; one with nothing left to move is taken back,
    and so is an earlier card of the day that still waits beside a later one. A recording filed elsewhere has its card
    taken back.
- ANNOUNCED AGAINST THE RECORDS. A date the instructor told the class that disagrees with the class schedule or the Canvas
  copy is marked orange with both.
  HOW THE RULE IS READ: an `announced` item is a DAY the instructor told the class (when a quiz or exam is, when work is
  due, that class will or will not meet); one that names no day is dropped. What it is the day of is a piece of the
  section's work the facts listed, or the class itself. By code: for work, the said day against what the section's class
  schedule holds for it (the day it is given, its due date in Faculty's Canvas copy or the gradebook, a planned row: the
  schedule's own read already joins them); it AGREES when any of those is that day, and DIFFERS otherwise, saying both and
  which record each is; for the class itself, against whether the section meets that day. One that differs is under
  Needs you; one that agrees, or that Faculty holds nothing to set against, is listed and asks nothing. Days only: a
  clock time that was said is kept and not compared. Nothing follows by itself: no card, and no record is changed.
- NOT BUILT, and not decided: telling a section that was FINISHED from one that was only started; a rule told to a class
  with no day in it (dropped scores, what an exam allows); taking a section reached ahead of the plan off the later day
  it was planned for; a follow-up preparation's class schedule facts still say "covered" from the plan, not the recording.

### Memos, training, search by meaning (step 6)

Built 2026-11-14, on the plan's three lines ("a memo read as a request to the Assistant; training filed under the course;
transcript text in the embedding index") and the instructor's choices of 2026-11-12 (Faculty reads each filed recording on
its own; every item quotes the transcript word for word; a memory only on their Remember; no general task list; no write
to Plaud). The plan held no rule for this step: EVERY READING BELOW IS MINE and the instructor's to change. No real memo
or training had been recorded when this was built, and no check asks a model or the embedding worker.

**Memos.**
- A MEMO IS THE INSTRUCTOR DICTATING ALONE, and the one recording whose words ask for something. It is held to two things.
  What it asks is found by a READING whose every item rests on the exact words (the same quote check, runs and items as any
  reading). Each request then goes to the ASSISTANT under the Assistant's own policy and NARROWER (ASSISTANT "A DICTATED
  REQUEST"): it works through the app's own routes, the automatic tier is not widened but closed, so every change it
  wants waits as an Approve card, and what the policy gives the Assistant no use of stays refused. Nothing outward, and
  no grade, absence decision or certification, follows from a memo by itself.
- HOW FACULTY KNOWS THE WORDS ARE THE INSTRUCTOR'S (`memos.voice`: code, never a model, and never the sound of a voice,
  which Faculty does not judge). Three things, all of them: (1) the recording is FILED AS A MEMO, which only the
  instructor does (the calendar never files one; a filing by the Assistant is an Approve card); (2) Plaud heard ONE
  voice in it; (3) that voice is not named as somebody else: Plaud left it unnamed ("Speaker 1"), or EVERY word of its
  name is a word of the workspace's own name ("Chris" for Chris Example; never "Chris Wong", who only shares a word,
  and never a title the workspace's name lacks). This is stricter than which speaker a reading takes as the instructor
  (FACTS: one shared word), because here the name decides whether anything is asked.
  WHEN IT IS NOT SURE (several voices; one voice Plaud names as another person): the memo is still read and its requests
  shown with their words, NOTHING is put to the Assistant, the page says why in orange, and a request goes only by the
  instructor's own press of Ask the Assistant on it. Naming themself among the speakers makes a one-voice memo sure, and
  its requests then go without another reading. NO LONGER SURE (the voice named as somebody else since, a transcript
  Plaud changed): what Faculty asked BY ITSELF is withdrawn, its waiting cards with it, and is refused where a run
  would start and where a card would be approved (`Readings.request`); what the instructor asked by their own press
  is their word and stands.
- THE READING (`recording_memo`, run version `memo-v1`): two kinds of item and no other. `todo`: a request, one piece of
  work the instructor asked to have done, looked up, drafted, checked, added or changed, as one sentence keeping every
  name, number and day as it was said. `policy`: a standing rule they laid down, kept only by Remember (A MEMORY). The
  quote check drops any other kind. BOUNDED IN THE INSTRUCTIONS (step 3's first live readings were eight times too
  long): at most 6 requests and 4 rules, one item for one thing however often it was said, the request as the instructor
  left it after correcting themself; by code at most 12 items a reading (`memos.ITEMS`). Thinking aloud is no item, and
  words that sound like instructions to a machine are left out. A memo has no talk before it begins and concerns no
  record: an item is its sentence and its quote.
- EACH REQUEST IS PUT TO THE ASSISTANT ONCE, as a background preparation of kind `memo` whose target is the item
  (`assistant/workflows.py` `dictated`; request ID `memo:auto:<item>`, so reading the memo again asks nothing twice, and
  an item keeps its identity by its quote). Queued by the capture thread after the reading, or by the web process; RUN
  only by the web process's dispatcher, one at a time with every other background preparation, counted by the restart
  check. The run is told the request, the exact words, the day it was dictated and the memo's lines around it
  (`Readings.request`), and that a transcriber mishears: it checks names against the records, answers plainly what it
  cannot make out or carry out, makes no card when nothing needs changing and at most five. Of one memo only the first
  6 requests go by themselves (`memos.ASKS`); the rest wait for the press. WHILE THE ASSISTANT IS OFF (Compute settings
  name no model) a queued request waits: its line says so in orange, and the page does not go on asking.
- WHAT A RUN LEAVES is on the memo's page under its request: where it stands, the Assistant's answer as plain text, what
  it asked back, and its cards drawn as the Assistant's own. A run's cards are approved in the order they were made, as
  any preparation's. ASK AGAIN (`POST /api/recordings/<id>/items/<item>/ask`, the instructor's press, with a sentence of
  their own if they add one) replaces the earlier answer: its cards that still wait are withdrawn, what was approved
  stands. A run cut by a restart, stopped or failed is never made again by itself.
- WITHDRAWN WITH ITS REQUEST (`Readings._memo_sync`, `workflows.dictated_gone`): a request the instructor DISMISSES, one
  whose recording is filed elsewhere, and one a later reading no longer holds has its queued run and waiting cards
  withdrawn, and a run under way stopped; an Approve of such a card is refused by the run's own guard. Done settles a
  request and leaves its cards. A dismissed request opened again is asked again only by the press.
- NEEDS YOU: a request that is open and that the Assistant is not working on (it waits on the press, or it was answered),
  and a rule still open. The memo's row and the page's header count them; a request with the Assistant counts nothing.
- A MEMORY from a memo is kept as from any recording: only by the instructor's Remember on a `policy` item. The Assistant
  keeps none: a run is no chat of the instructor's typed words.

**Training.**
- A TRAINING IS FILED UNDER ITS COURSE, by hand ("A course's training, for reference"; `kind` `training` with the course),
  for reference in every term (COURSE-NOTES "TRAINING RECORDINGS"): kept like any recording filed as Faculty's (transcript,
  Plaud's notes; its audio plays from the archive), opened, played and searched like any, and listed on the course's page. NOT READ for items: a
  training leaves no task, and one the instructor wants read as a meeting is filed under the course instead (one more
  revision of the same link). The calendar never files a training.

**Search by meaning.**
- WHAT WAS SAID IS FOUND BY DESCRIBING IT (`recordings/meaning.py`; EMBEDDING "WHAT IS SENT", "USES"). A kept transcript's
  lines are gathered into PASSAGES (consecutive lines, about 1,000 characters; a passage of fewer than five words is left
  out), and each passage's words alone (no speaker, no time) are embedded ONCE by the local worker: one text of at most
  1,500 characters a request, at most eight passages a turn. The capture thread gives the index a turn after each of
  its ticks (twenty seconds apart while passages wait), outside the capture lock, only where the service names the
  worker's socket. Nothing leaves the machine.
- THE INDEX is the workspace's rebuildable computing, as the picture index is: one SQLite file under
  `<nvme>/runtime/recording-index/` (`FACULTY_RECORDING_INDEX_DIR` moves it; mode 0600 in a 0700 folder; in no backup, no
  catalog table). It holds which lines of which kept transcript file a vector was made from, the checksum of those words
  and the numbers: no word, no name, no student. Deleting it loses nothing.
- WHO MAY BE SHOWN IS DECIDED AT SEARCH TIME FROM THE CATALOG, never from the index: a passage is answered only while its
  file is the LATEST transcript of a recording FILED AS FACULTY'S NOW, and its words are read from the catalog's lines.
  A recording that waits for Keep or Don't keep was never fetched and has nothing to read; one that is not kept has no
  row; one the instructor said not to keep after it was kept is not shown from that moment, and its vectors are dropped
  at the worker's next turn, whatever else waits (`PassageIndex.strays`), before or after its rows are erased.
- THE SEARCH (`POST /api/recordings/search` with `meaning` true; the page always asks so): under the lines that hold the
  words, NEAREST BY MEANING, at most six passages nearest first, each with its recording, day, where it begins and its
  lines, opening the recording at that moment; a passage a keyword result already shows is not repeated. NEAREST IS
  NEVER A FINDING (PICTURE-SEARCH): a score orders the passages and nothing else, is not shown, and no count, flag or
  item reads it. No bar is set: none was measured (see KNOWN LIMITS). A worker that is down, slow or lending the GPU
  leaves the keyword results and one line saying so; nothing falls back to a hosted service. Without `meaning` the answer
  is byte for byte what it was.
- TROUBLE, as PICTURE-SEARCH: a worker that does not answer or has lent the GPU ends the turn and blames no passage; a
  passage refused three times in a row while the worker answers is set aside for seven days; a new model revision embeds
  everything again and the other revision's vectors are never searched.

### The page, routes and the Assistant

- ONE RECORDING'S PAGE (step 2), `#recordings/<id>`, opened by the name of any recording Faculty kept something of. Its audio, from
  the archive, in the browser's own player (where Faculty has none to play it says the archive has none yet; where it cannot be
  played it says the file is in the Plaud archive and that `plaud-intake status` says whether the archive can be read); its transcript in a region that scrolls beneath it, each line with how far in it is, who spoke
  (printed where the voice changes) and the words; a line's time is the control that PLAYS FROM THERE, and the line being said
  is marked (never in orange: it needs nothing of the instructor). Find marks the lines that hold every word typed.
  `#recordings/<id>?t=<milliseconds>` opens at that moment, the line shown and the audio set there, not played (a browser plays
  only on the reader's own press). Plaud's summary and outline are FOLDED and said to be Plaud's words, shown as text and never
  as markup; an outline topic plays from its time. Leaving the page stops the audio. A browser that cannot play Ogg Opus says so
  on the page (the transcript is still complete); writing a second copy in another format at capture is NOT built, and waits
  on the instructor's browser turning out to need it. The recorder's button MARKS are kept and not shown yet: no real
  recording has had one, so their shape is not known.
- WHAT CAME OUT OF IT (step 3; `static/recording-items.js`). Under the player the page says where the reading stands in one
  line (when it was read and who Faculty took as the instructor; why it could not be read; that it is being read: "reading it
  again now" only while a Read again waits, and for a transcript Plaud changed that it is read in the next round or why it
  could not be, with Read again left to the instructor), with Read again and the term's coordinator, then the items: NEEDS YOU first, its count the page's only orange beside a waiting card
  (open items for the instructor that Faculty's records do not show as done), then Answers to your questions, Held for a
  rubric, Others said they would, and FOLDED: Already done (settled by the instructor, or already so by Faculty's records)
  and Left out by the quote check. An item is one sentence, its kind and day, the quote with the control that plays that
  moment, what Faculty found, its waiting Approve card drawn as the Assistant's own, and its controls. Once there are items the
  TRANSCRIPT is folded beneath them (DESIGN-LANGUAGE "No walls of text"); a moment in the address, a find and Enter unfold
  it. While a reading is being made the page asks again with the shell's quiet refresh; otherwise a recording is read once.
  A STUDENT'S MEETING (step 4) says first who it was with: each student, their section and part, an appointment recorded
  as one that did not happen in orange, what their follow-up already holds, and their two cards drawn as the Assistant's
  own with what each would do in plain lines; "Students and parts…" opens the window that sets them. Its groups read
  "They told you" and "They said they would"; no coordinator is named; what a student told the instructor may be
  dismissed. With several students each item says whose it is.
  A CLASS (step 5) opens on its day (`static/recording-items.js` `classDayMarkup`): the section and the day with the way to
  its Class schedule, Planned and Reached in two rows, ONE orange line for a planned section that was not reached, and
  the move's card drawn as the Assistant's own with that section's topics as the card would write them (the route's raw
  preview answer folded under them), or "Propose the move to <day>" where no card waits. Its groups are Needs you (a day
  told to the class that disagrees), What the class reached (each `covered` item with its words; only Dismiss) and Told
  the class; no coordinator and no student.
  A MEMO (step 6): its line says the one voice was taken as the instructor's, or in orange why Faculty was not sure. Its
  groups are You asked (each request with its words, how the Assistant's run stands, its answer, its waiting cards, and
  Ask the Assistant or Ask again…, Done, Dismiss) and Rules you said (Remember…); each heading says in orange how many
  of its items need the instructor. While a request is with the Assistant the page asks again with the shell's quiet
  refresh. A TRAINING says it is kept for reference and not read, and has no items.
- SEARCH OF WHAT WAS SAID (step 2), on `#recordings` and by the Assistant: the lines that hold EVERY word typed, whatever its
  case (a typed % or _ means itself), in recordings filed as Faculty's now, each one's latest transcript, newest recording
  first; each result is the recording and its day, how far in, who spoke, the words with the found ones marked, and the line
  before and after, and opens the recording at that moment. By keyword; it writes nothing. Step 6: and BY MEANING, the
  passages nearest what was typed read as a description, listed under the keyword results (Search by meaning).
- `#recordings`, a workspace page (FRONTEND, DESIGN-LANGUAGE): KEEP OR NOT first (what waits for the instructor: its day,
  why it waits, Plaud's title as Plaud's words, ONE framed step, Keep…, and Don't keep beside it as words), then the kept
  recordings newest first, each named by what it is filed under and its day, never by Plaud's title, with File elsewhere and
  Don't keep as words (Don't keep there asks first: it erases). What is NOT KEPT is not on the page: one line says how many
  recordings at Plaud are not kept ("Faculty holds nothing of them") with Ask me again. Its line says when capture last ran and since when it listed, or what the intake says is wrong (the sign-in to be made again, an archive
  that is not ready, a listing of Plaud that is old). A list row says "Transcript kept" of a recording copied from the archive and
  "Transcript and audio kept" of one whose own audio file Faculty still holds. What erasing a kept recording says: Faculty erases its
  transcript and what it read from it; what was approved stays; the recording stays in the instructor's archive and at Plaud. The page is reached
  from the rail (TODAY, after Follow-up; requirement, 2026-11-14, who did not find the page while it opened only from the
  palette and Workspace settings: "Yes, add it"), from the palette (among its Pages) and from Workspace settings. The rail
  entry COUNTS what waits for Keep or Don't keep, from Home's card `recordings:keep` (requirement, 2026-11-14: "Home and
  sidebar too"; FRONTEND: the rail's badges come only from Home's cards), and the page tells the shell when a press here
  changed that count. A recording's own page highlights the same entry. Step 3: each recording's
  row says how many of its items need the instructor ("6 for you", orange), and the page's header says the total.
  Step 4: a row whose recording began inside an appointment recorded as cancelled or a no-show says so in orange, and
  several students of one term are named together, the term once. Step 5: a class's row counts its day once where a
  planned section was not reached and the move was not declined, and each day told to the class that disagrees.
  Step 6: a memo's row counts what it needs; `#recordings?course=<course>` lists that course's trainings alone, every
  one of them (`GET /api/recordings?course=<course>`: the list is not cut at its 300 newest), as the course Home's
  "Training recordings" line links them (how many, and the latest one's day).
  HOME shows ONE card of Recordings, "Recordings to keep or not" (`recordings:keep`, from `RecordingService.unsure`, one
  count query): how many wait, and a link to the page; never a title or a time. The rest of the service's attention count
  (`RecordingService.attention`: sign in again, since step 3 the open items for the instructor, `for_you`, and since step 4
  `appointments_off`) is on the Recordings page alone; Home does not show it, and nothing is added to `/api/state`.
- SPEAKERS (step 2) take Plaud's names. A name the instructor gives a speaker in Faculty (`POST /api/recordings/<id>/speakers`;
  an empty name takes it back) is a revision here (`source` `instructor`, with the request ID and who made it) and is never
  written to Plaud. It is one recording's, and it is kept by Plaud's FIRST label for that voice (`original_speaker`, "Speaker
  2"), so it stays when Plaud later names the voice itself. A term may name its COORDINATOR (step 3, built; term-owned, a name
  alone, `POST /api/recordings/coordinator`): a reading made afterwards is told it. ONE NAME (COORDINATOR, 2026-11-15, the
  instructor: "join the recordings coordinator name with the course card's"): a term whose course card names its coordinator
  is told and shown that name instead (`coordinator.card` true; the page's window says where it is changed, and the route
  answers 409); the name kept here serves only a term whose card names none.
- ROUTES (`recordings/api.py`). Built: `GET /api/recordings` (capture's state, the attention count, the list, what a
  recording may be filed under, and `not_kept_count`), `POST /api/recordings/check`, `POST /api/recordings/<id>/file` (Keep,
  or File elsewhere; a request ID; a repeated one
  returns what it did, one used for another recording is refused; who made it is recorded, `request_actor.current()`; a filing
  by the calendar records `automatic`). ONLY WHAT IS FACULTY'S IS KEPT, each with a request ID and who made it, and neither
  the Assistant's: `POST /api/recordings/<id>/discard` (Don't keep; a repeated request ID answers as the first did, though
  the recording is gone) and `POST /api/recordings/ask-again`. Step 2, built: `GET /api/recordings/<id>` (the recording as its page shows it; with
  `start_ms`/`end_ms` only the lines of that stretch, without Plaud's notes), `GET /api/recordings/<id>/audio` (the archive's file, checked against the intake's checksum on every read, or Faculty's own older file checked against its own; ranges, `inline`, no-store, nosniff; the app's own policy lets its own pages play
  it and nothing else frame it), `POST /api/recordings/search` (writes nothing), `POST /api/recordings/<id>/speakers` (a request
  ID; who made it). Step 3, built, each write with a request ID and who made it: `POST /api/recordings/<id>/read` (Read
  again), `POST /api/recordings/<id>/items/<item>` (done, dismissed, reopened; for a ruling accepted or declined, naming the
  graded assignment), `POST /api/recordings/<id>/items/<item>/remember`, `POST /api/recordings/<id>/items/<item>/card`,
  `POST /api/recordings/coordinator`, `GET /api/recordings/rulings?assignment=<graded assignment>`; `GET /api/recordings/<id>`
  now also answers `reading`, `items`, `counts` and `coordinator`, and `GET /api/recordings` each row's `for_you`. Step 4,
  built, each with a request ID and who made it: `POST /api/recordings/<id>/students` (the students and their parts),
  `POST /api/recordings/<id>/cards` (`kind` `commitments` or `note`, and `enrollment_id` where the recording holds
  several students: propose that card again); `GET /api/recordings/<id>` now also answers `meeting` (`student`, `staff`
  or nothing) and `students`, and an item its `enrollment_id` and the `followup` that holds it. No address carries a
  student. Step 5, built: `POST /api/recordings/<id>/cards` also takes `kind` `move` (propose a class day's move again);
  `GET /api/recordings/<id>` answers `meeting` `class` with `class_day` (the section, the day, `state`: `unread`, `none`,
  `unplanned`, `as_planned`, `differs` or `left`; `planned`, `reached`, `not_reached`, `ahead`, `earlier`, the next class
  day, its `card` and whether the move may be offered) and a class's `counts` (`you`, `covered`, `told`, `done`).
  Step 6, built: `POST /api/recordings/<id>/items/<item>/ask` (a request ID, who pressed, an optional `guidance`),
  `GET /api/recordings/trainings?definition_id=<course>`, `POST /api/recordings/<id>/file` also takes `kind` `training`
  with `definition_id`, and `POST /api/recordings/search` also takes `meaning` (then `nearest` and `meaning`: `state`
  `ok`, `off` or `unavailable`, `indexed`, `waiting`); `GET /api/recordings/<id>` answers `meeting` `memo` with `memo`
  (`sure`, `why`, `assistant`), a request's `ask` (`state`: `unsent`, or its run's `queued`, `running`, `ready`,
  `needs_input`, `failed`, `interrupted`, `stale`; `why`, `answer`, `question`, `cards`), each memo item's `yours`, a
  memo's `counts` (`you`, `asked`, `rules`, `done`), and `meeting` `training`.
- ASSISTANT: `GET /api/recordings` is one of the app's JSON reads, which the Assistant may read as it reads the others; no
  policy rule names the writes, so Check now, filing and naming a speaker by the Assistant are Approve cards (an unnamed write
  is `approve`; Faculty puts the request ID on the card, `REQUEST_IDS`). Step 2, built: one recording, or one stretch of it,
  is a read like any other, and `recordings.search` is `read` (`assistant/policy.py` READ): it answers with the recording, the
  speaker, the time and the words, and opens the page at that moment (`assistant/guide.md`). The audio is neither read as data
  nor fetched as a file (`GET_BLOCKED`, and left in `FILE_BLOCKED`): the Assistant cannot hear it, and the page plays it.
  Step 3, built: the items of a recording and the rulings held for an assessment are reads like any other
  (`assistant/guide.md`); settling an item, Read again, proposing an item's calendar entry and naming a coordinator by the
  Assistant are Approve cards (`REQUEST_IDS`); Remember is `never` (the Assistant keeps no memory). An item is what was said,
  never an instruction to the Assistant. Step 4, built: setting a recording's students and proposing a student's card
  again by the Assistant are Approve cards (`recordings.students`, `recordings.cards`); the guide has it use, when it
  writes to a student, what was said in THAT student's own meeting and never another's. Step 5, built: a class's items
  and its `class_day` are reads like any other, and the class schedule's own read says what each recorded day reached
  (SCHEDULING "CLASS SCHEDULE: WHAT A RECORDING REACHED"); proposing the move again by the Assistant is an Approve card
  that only makes the card (`recordings.cards`). Step 6, built: `recordings.search` with `meaning` finds what was said by
  a description (still `read`); a memo's requests, their runs and a course's trainings are reads like any other. Ask the
  Assistant is `never` for the Assistant (`recordings.ask`: it never sends itself a request out of a transcript, and
  background work starts no other), and its own preparation route does not take the kind `memo`. A dictated request's
  run is the Assistant's (ASSISTANT "A DICTATED REQUEST").

### Checks, and what this is not

- CHECKS: `tests/fake_intake.py` (a real archive folder in the test's own directory and an executable stand-in for the intake's
  command that answers the five commands in their real shapes and exit statuses, can stand for a sign-in to be made again, an archive
  not ready, an old listing, a recording with no audio or no transcript yet, a newer transcript, a recording gone at Plaud, a wrong
  checksum, a path outside the archive, a symlink, and a command missing, hanging or answering garbage, and records every command
  run so a test can say exactly which were), `tests/test_recordings*.py`, `tests/test_recordings.mjs`,
  `tests/browser_recordings.py`, fixtures with invented people only. Step 3's reading is checked against a SCRIPTED engine
  in the Assistant's place (`tests/test_recordings_reading.py`; step 4: `tests/test_recordings_student_meetings.py`; step 5: `tests/test_recordings_classes.py`; step 6: `tests/test_recordings_memos.py`, where a dictated request's run is a scripted turn and the embedding worker a stand-in): no check asks a model or reaches the embedding worker. No check reaches Plaud, runs the real intake, reads the real archive or holds a real recording. Each
  step adds its `CHECKS.md` entry. NOT RUN AGAINST THE REAL INTAKE through this code while step 4 was built (CHECKS: a check never
  reaches a real outside service or the live archive; the answers' shapes are those of the real command with the titles blanked):
  the first live rounds must be looked at.
- KNOWN LIMITS. (1) A START TIME IS PLAUD'S WORD. Filing rests on `start_at` as the archive keeps it. Plaud's own client describes the date it shows
  as "recording start time when known, else ingest time", so audio imported into Plaud rather than recorded by the recorder
  could carry the moment it was uploaded and be filed by that. Nothing in Plaud's answers tells the two apart (looked for in
  its CLI's source, 2026-11-12: `start_at` may be absent, and is then never filed by the calendar; the four real recordings
  had `start_at` equal to `created_at`). A recording filed wrongly this way is re-filed by hand. (2) A recording that reaches
  the archive more than thirty days after it began, or a transcript changed more than about three days after it was kept, is not
  picked up (HOW FAR BACK, READ AGAIN). (3) THE READING IS A MODEL'S. The quote check proves the words were said where the item
  says; it does not prove the sentence says what they mean, nor that nothing was missed. The page shows the quote and plays
  it for that reason. Step 3 was NOT read on the real 2026-11-11 meeting through this code before it landed (a check never
  asks a real model): the plan's acceptance (the dry run's counts) is looked at on the first live readings. (4) AN ITEM'S
  IDENTITY IS ITS QUOTE: a later reading that rests the same thing on other words makes a new item, and what was answered of
  the old one does not follow. (5) WHO IS THE INSTRUCTOR rests on the workspace's name and the speakers' names (FACTS); a
  meeting where Plaud names nobody is read without it until the instructor names themself and reads again. (6) WHOSE MEETING
  IS THE CALENDAR'S WORD (step 4): a recording that begins inside a student's appointment is theirs, whoever is heard in
  it; it is read, put on their timeline and proposed on their follow-up before anyone has looked. A recording of someone
  else made in that half hour is re-filed by hand, which takes the entry and the waiting cards away; what was already
  approved stays where it was approved. (7) A PART IS THE INSTRUCTOR'S WORD: nothing checks that the voices in a part are
  that student's. (8) A commitment approved for one student stays on their follow-up if the part it was heard in is
  later given to another (it no longer links to the recording, and is theirs to cancel). (9) A CLASS'S READING WAS NEVER
  MADE BY A MODEL before step 5 landed (a check never asks one): how many items a real class leaves, and whether a section
  said as words ("five eight") is matched to its number, is seen only on the first live readings. (10) WHAT A CLASS REACHED
  is what its reading kept: a section the reading missed reads as not reached (the instructor declines the move), one it
  wrongly kept is dismissed; started and finished are not told apart. (11) A stale move card is replaced in the next
  capture round, up to ten minutes later: until then its Approve is refused by the class schedule's own preview and
  nothing is written. (12) A NAME IN A CLASS IS CAUGHT ONLY WHOLE: a day told to one student by first name alone, under a
  sentence that names nobody, is kept out by the instructions and not by code; the transcript itself holds every name
  that was said, as any recording's does. (13) A RE-READ IS NOT STABLE (seen on live in step 3): reading a class again
  may keep other items, which changes what the day shows as reached and takes a waiting move card back for a new one.
  (14) A MEMO WAS NEVER READ BY A MODEL, NOR A REQUEST RUN BY ONE, before step 6 landed: how a real memo is split into
  requests, whether the sentence keeps a misheard name as it was said, and what the Assistant does with a request heard
  wrongly are seen only on the first live memos. Every change still waits for Approve, and the card shows what it would
  do. (15) THE VOICE IS PLAUD'S COUNT, NOT A VOICE: one voice in a recording the instructor filed as a memo is taken as
  theirs; a memo Plaud splits into two voices by mistake asks nothing until the press, and somebody else speaking alone
  into the recorder, filed as a memo by the instructor, would be taken as them. (16) A RE-READ MAY REWORD OR DROP A
  REQUEST (13): one that rests on other words is a new item and is asked as new; one dropped has its waiting cards
  withdrawn. What was approved of the old one stands, so the new run may propose the same change again: its card says
  what it would do, and the instructor declines it. (17) SEARCH BY MEANING HAS NO BAR: the nearest passages are shown however far they are, because no real
  transcript was embedded before it landed and none was measured (PICTURE-SEARCH set its bars from the live index). A
  vague description finds something. Measure on the first live index before a bar, or a use beyond a list to read, is
  added. (18) A passage is cut by length, not by topic: what was said across a cut is in two passages. (19) THE FIRST
  FILL embeds every kept transcript: about 50 passages for a 70-minute meeting, eight a turn every twenty seconds.
  (20) A FILING OR A NAME APPROVED ON A CARD HAS THE SAME EFFECT AS ONE MADE BY HAND: a card of the Assistant's that
  files a recording as a memo, or names its one voice as the instructor, is the instructor's approval, and Faculty then
  reads the memo and puts its requests to the Assistant as for any memo. The card says only the filing or the name.
- NOT BUILT, by decision: a speech model on this machine; any write to Plaud; telling speakers apart by voice; a general task
  list (an item goes to the module that owns it, or stays on its recording); per-student items from a class.
- Whether and how people are told they are recorded is the instructor's practice. Faculty records no consent and takes no
  part in it.
