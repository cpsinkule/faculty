# Recordings: build plan

PROPOSED 2026-11-12. Its contract is binding since step 1 was built: `docs/decisions/recordings.md` (RECORDINGS). STEP 1 IS BUILT
(live since 2026-11-12). STEP 2 IS BUILT (2026-11-13: the recording page, the search of what was said, speaker names, the
Assistant's read; a copy of the audio in another format is NOT built, see below). STEP 3 IS BUILT (2026-11-13: the reading of
meetings filed under a term or a course, the quote check, items, the checks against Faculty's records, an Approve card for a
date the calendar lacks, Remember, rulings held for an assessment's rubric; see its acceptance below). STEP 4 IS BUILT
(2026-11-13: a student's meeting read by its own instructions, on their timeline by reference, what was promised and a note
each on one Approve card, the meeting's items among a follow-up preparation's facts, several students each with their part,
an appointment recorded as one that did not happen marked). STEP 5 IS BUILT (2026-11-13: a class filed on its section's
class day is read by its own instructions for the notes sections it worked through and the days told to it; reached against
planned on the recording's page and the section's class schedule, the move of what was not reached on one Approve card;
a day told to the class against the records). STEP 6 IS BUILT (2026-11-14, the last: a memo the instructor filed is read
into requests that rest on its words, and each is put to the Assistant as a background preparation whose every change is an
Approve card; a training is filed under its course and kept for reference; a kept transcript's passages are embedded on
this machine, so what was said is found by describing it). Every step of this plan is built.
SINCE 2026-11-14, ONLY WHAT IS FACULTY'S IS KEPT (the contract's rule of that name): what the calendar shows to be
nothing of the instructor's teaching is not kept at all, what Faculty is unsure of waits for Keep or Don't keep, and Don't
keep erases; where a step below says "Not Faculty's" or "Needs filing", read it so.
Each step lands on its
own, restarts the app only when the idle check passes, amends the contracts it touches in the same change, and adds its
`CHECKS.md` entry.

## What is already true

- Plaud can be read from plain Python with no one at the keyboard (checked 2026-11-12: three read calls, one forced renewal).
- The sign-in sits in a trial location, `/mnt/nvme/Teaching/tools/plaud/home/.plaud/tokens.json`, and **lapses on
  2026-11-19 20:36 UTC** unless something renews it. Until step 1 is live, running
  `/mnt/nvme/Teaching/tmp/plaud-spike/renew_check.py` once extends it seven days.
- Before step 1 Faculty had no meeting or audio record of any kind.

## Steps

| Step | What lands | You can then | Contracts amended |
|---|---|---|---|
| 1. Intake | Plaud fetch module, token renewal, the capture thread, the storage owner, the migration, filing by the calendar, a plain list page | See every recording, filed or waiting, and file one by hand | RECORDINGS becomes binding; AGENTS index and OUTSIDE SERVICES (renewal named); STORAGE (new owner) |
| 2. The recording page | Transcript with speakers, audio that plays from any line, Plaud's summary folded, speaker names, keyword search, the Assistant's read tool | Open a meeting, jump to a moment, ask the Assistant what was said | ASSISTANT (the read tool) |
| 3. Coordinator and instructor meetings | The reading, the quote check, items, the checks against Faculty, cards for calendar entries, Remember, rulings held on an assessment | See what a meeting asked of you, with what Faculty already found | ASSISTANT-MEMORY (SAID IN A RECORDING), GRADING (rulings at finalization), CALENDAR |
| 4. Student meetings | Filing on the appointment, the timeline entry, the promises card, the note card, follow-up facts | Find a meeting on the student's record and approve its promises | COMMUNICATIONS, TEACHING-WORKFLOWS, ASSISTANT, STUDENT-NOTES |
| 5. Classes | Filing on a section's class day, covered against planned, announced against the records | See what each section reached and move what it did not | SCHEDULING (class schedule) |
| 6. Memos, training, search by meaning | A memo read as a request to the Assistant; training filed under the course; transcript text in the embedding index | Dictate a request; search by description | ASSISTANT, COURSE-NOTES, EMBEDDING |

## Step 1 in detail

- `recordings/plaud.py`: GET-only, two hosts, no redirects, `FACULTY_PLAUD_URL` for the fake; renewal under a lock, written
  atomically, refused means "Sign in to Plaud again".
- Move the token file to the path `FACULTY_PLAUD_TOKENS_FILE` names and add `deploy/faculty.service.d/plaud.conf`.
- `recordings/migrations/001_recordings.sql` (`recordings`, `recording_revisions`, `recording_links`, `recording_files`,
  capture state) with its adoption test in `tests/test_catalog_migrations.py`.
- `recording_bundles` registered in `course_storage.py`, following `communications/storage.py`.
- `recordings-capture` thread in the Inbox worker; restart both services after it lands.
- Filing by the calendar: appointment, class meeting, entry naming a term's course code, Not Faculty's, Needs filing.
- `tests/fake_plaud.py` and `tests/test_recordings.py`: renewal replaces both tokens, a refused renewal stops capture, the
  five-minute audio link is fetched in the same round, an unfiled recording is never fetched, the four filings.
- Done when: on an isolated copy with the fake, a new recording appears filed within one round (DONE 2026-11-12); on the live
  app, the four recordings now at Plaud show as two filed to the term and two Not Faculty's, with nothing fetched for the
  latter (NOT DONE: it waits for the instructor's go).
- LEFT FOR THE LIVE MACHINE, each on the instructor's word: copy `deploy/faculty.service.d/plaud.conf` and
  `deploy/faculty-inbox.service.d/plaud.conf` to `~/.config/systemd/user/…`; MOVE (never copy) the sign-in from the trial location to
  `~/.config/faculty/plaud-tokens.json` (mode 0600) before it lapses on 2026-11-19 20:36 UTC; `systemctl --user daemon-reload`;
  restart `faculty` and `faculty-inbox` once the idle check passes; then look at the first live rounds (the code has not read
  the real Plaud).

## How each later step is accepted

- Step 2: the page plays a quote from the kept file; the Assistant answers "what did the coordinator say about
  calculators" with the time and words. Confirm first that your browser plays Ogg; if it does not, capture also writes an
  M4A copy. (Built 2026-11-13 the other way round: the page itself says so where a browser cannot play Ogg Opus, which
  Chrome, Edge, Firefox and current Safari do; the M4A copy waits on that sentence ever showing for the instructor.)
- Step 3: reading the 2026-11-11 meeting on an isolated copy reproduces the dry run: testing-center counts 14, 1 and 1;
  five Google entries listed as not happening; seating charts and the Exam 3 module marked already done; the five answers to
  your questions; about 25 rubric statements held for Exam 3. The repository's own fixtures use invented people.
  (Built 2026-11-13 against a scripted engine: no check asks a real model, so this comparison was NOT made before landing.
  The two meetings Faculty already keeps are read by the first rounds after the restart; compare that reading with the dry
  run then, and use Read again after naming the coordinator and yourself among the speakers if the first one lacks them.)
- Step 4: a recording inside a test appointment appears on that student's timeline and nowhere else; the promises card
  adds commitments only on Approve. (Built 2026-11-13 and checked so, against a scripted engine: no check asks a real
  model, and NO REAL STUDENT MEETING had been recorded yet, so the first one is the first real reading by these
  instructions and must be looked at.)
- Step 5: a class recording makes no student link; a day planned 5.6 to 5.8 and recorded through 5.7 shows orange and
  offers the move. (Built 2026-11-13 and checked so, against a scripted engine: no check asks a real model, so the first
  live class readings are the first real ones by these instructions and must be looked at. Two class recordings, of
  sections 507 and 508 on 2026-11-13, were already kept and unread when this was built; the term's class schedule held
  NO topics on any day then, so until topics are planned a class day shows what was reached, unmarked, and offers nothing.)

- Step 6 (written 2026-11-14, before it was built; the plan had none): on an isolated copy with the fake, a recording
  the instructor files as Memo, with one voice, is read into its requests, each resting on its exact words; each request
  is put to the Assistant once, its run answers on the memo's page, and a change it wants (one that is automatic in chat
  included) waits as an Approve card; a memo with two voices asks the Assistant nothing until Ask the Assistant is
  pressed. A recording filed as a course's training is kept, listed on the course's page and never read. Describing what
  was said finds the passage that says it without sharing a word with it, and never a passage of a recording marked Not
  Faculty's or one that was never fetched. (Built 2026-11-14 and checked so, against a scripted engine and a stand-in
  embedder: no check asks a model or the embedding worker, and NO REAL MEMO OR TRAINING had been recorded, so the first
  memo is the first real reading by these instructions and the first real request run from a transcript, and the first
  live index is the first real ranking. Search by meaning has no bar until that index is measured.)

## Risks and open points

- **Plaud's side can change.** The API is a limited beta. If it breaks, the fallback is Plaud's export dropped into
  `~/dropsync`; step 1 keeps the fetch module small so that swap is cheap.
- **Sign-in cap.** The seven days slid forward once; whether Plaud ends a sign-in eventually is unknown.
- **The 20-recording report.** Untestable with four recordings. Capture lists by date, so a cap on old ones would not matter.
- **Transcription minutes.** Four sections three times a week is about 40 hours of class a month. As I recall, Plaud's plans
  cap monthly transcription minutes; check yours before recording every class. Faculty keeps the audio, so a speech model
  on this machine could transcribe classes later, but none is planned and the GPU is spoken for.
- **Names in transcripts.** A pass that corrects names against the roster and the term's staff is not in any step yet.
- **Reading cost.** About 13,000 tokens of transcript for a 71-minute meeting, per reading, on the Assistant's engine.
  A class is smaller: the two kept on 2026-11-13 (41 and 46 minutes, mostly one voice) are 296 and 194 lines, about
  20,000 and 14,000 characters (by the usual four characters a token, some 5,000 and 3,500 tokens each, with the notes
  outline and the work's titles beside them). NOT measured: what the engine answers for one, how long it takes, and what
  twelve a week cost on the Assistant's connection.
- **A memo costs two kinds of request.** One reading (small: a memo is a minute or two), then one Assistant run for each
  request in it, each an agent turn with tools. NOT measured: what a run costs or how long it takes.
- **The first fill of the search index.** Every transcript already kept is embedded once after step 6 lands: eight
  passages every twenty seconds, on the embedding worker's quarter of the GPU (text only, one text a request).

## Not in this plan

A speech model on this machine, any write to Plaud, telling speakers apart by voice, a general task list, and items about
one student drawn from a class recording.
