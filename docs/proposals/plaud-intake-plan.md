# One Plaud intake: plan

CARRIED OUT 2026-11-15, all five steps, each on the instructor's word ("Yes, plan it", then their answers below, "Now" for the
sign-in, "build step 4", "Now" for the tidy-up). This file is the record of the plan as it was written, and binds nothing: what
binds Faculty is RECORDINGS "Plaud" (`docs/decisions/recordings.md`), and what binds the intake is its own `AGENTS.md`.

WHERE IT STANDS: the intake (`~/code/plaud-intake`) is the one program that talks to Plaud, holds and renews the sign-in at
`~/.config/plaud-intake/tokens.json`, and keeps every recording with its audio in `/mnt/nvme/plaud-intake/` (copied nightly to
`/mnt/bulkdata2/plaud-intake/`). The brain's nightly sync builds its notes from it. Faculty runs its commands and reads its
archive (`recordings/intake.py`), asks Plaud nothing, and plays audio from the archive. The intake's shadow mode is deleted. What
differs from the steps as written: at step 3 the intake made its first renewal attended, straight after the move, in place of a
renewal by Faculty just before it; the intake's own sign-in became its default for every caller, since the brain runs it with
no settings; and the audio Faculty held before step 4 was left where it is (nothing removes it, and it still plays if the
archive cannot).

The sections below are the plan as written before it was carried out: "What is true today" is the day before.

## What is true today (looked at 2026-11-15)

- **Two programs read Plaud.** Faculty's Inbox worker every ten minutes (`recordings/service.py`, through the one client in
  `recordings/plaud.py`), and the brain's `brain-plaud-sync` at 3 am (`~/brain/scripts/plaud-sync.py`).
- **One sign-in, and it is Faculty's.** `~/.config/faculty/plaud-tokens.json`. Faculty is its only renewer; the brain reads
  its access token and never renews. Plaud ends the old refresh token at each renewal, so there can be only one renewer. A
  Faculty stopped for a day leaves the brain signed out.
- **The words of every recording are in the brain; the audio of most is nowhere on ml-box.** The brain keeps a note with the
  transcript of every recording and no audio. Faculty keeps transcript and audio of the teaching ones only.
- **Faculty's tie to Plaud is narrow.** Only `service.py` calls the client: list, one recording, its linked blocks, its
  audio, the renewal. Filing, readings, cards, the timeline and erasure work from Faculty's own rows.

## The rule this builds

**One program talks to Plaud and keeps everything. The brain has every recording. Faculty has the teaching ones as well.**

| A recording | The intake's archive | The brain | Faculty |
|---|---|---|---|
| teaching, kept | Plaud's answer and the audio | its note | the transcript as well; the audio plays from the archive |
| Faculty is unsure | the same | its note | asks Keep or Don't keep, holds nothing |
| not teaching | the same | its note | the marker alone, as now |

Don't keep in Faculty means "not teaching". It erases Faculty's copy and nothing else: the archive and the brain still have
the recording.

## The intake

A small program of its own: code in `~/code/plaud-intake` (its own repository, owned by neither reader), the archive in
`/mnt/nvme/plaud-intake/` with a nightly verified copy in `/mnt/bulkdata2/plaud-intake/`, the sign-in in
`~/.config/plaud-intake/tokens.json` (mode 0600). No network listener.

- **Holds the sign-in and renews it**, under a lock, from step 3. This is Faculty's `TokenFile` and renewal moved as they
  are, with their tests and the fake Plaud.
- **Lists** Plaud every few minutes: id, title, start, length, when first seen, whether Plaud still has it.
- **Keeps every recording** once Plaud has finished it: Plaud's answer (transcript, outline, marks, notes) and the audio (Ogg,
  about 15 MB an hour), each with its checksum. A transcript Plaud changes later is kept beside the earlier one. It never
  deletes; a recording gone at Plaud stays.
- **Says its state**: last listing, and "sign in to Plaud again" when Plaud refuses. Faculty's page and the brain's
  `_sync-error.md` both show it.
- **Is read through a command** that answers in JSON (`list`, `show`, `audio`, `status`, `check-now`). The readers only read.

## Steps

**1. The intake, in shadow.** Built and run beside what exists. It borrows Faculty's access token as the brain does now and
never renews. Done when its listing matches Plaud's, and the audio of every recording Faculty keeps has the checksum Faculty
recorded. Changes nothing in Faculty or the brain. From here the audio of everything is stored.

**2. The brain reads the archive.** In `~/brain`, by a session there (its own `AGENTS.md` makes `scripts/` the
instructor's). The sync holds no Plaud code and no token; it writes the same note for every recording, from the archive. The
notes already in the vault stay as they are.

**3. The sign-in moves.** The one risky step. The sign-in file is MOVED (never copied) to the intake, which becomes the only
renewer; Faculty reads its access token from there and never renews, as the brain does today. The renewal leaves AGENTS.md
OUTSIDE SERVICES and RECORDINGS "RENEWAL". Cutover: idle check, stop the Inbox worker, a fresh renewal, move the file, start
both. Back out by moving the file back and reverting the commit, within the day the access token lasts.

**4. Faculty reads the archive.** `recordings/intake.py` takes the place of `recordings/plaud.py`. Faculty lists from the
intake and, for a recording it files, copies the words (transcript, outline, marks, notes) into its own storage, checked
against the archive's checksums. It holds no audio: the page's player is served the archive's file, checked against its
checksum on every read, and says so when the archive cannot be read. Check now asks the intake. Faculty then sends Plaud
nothing, and the `plaud.conf` drop-ins go.

- RECORDINGS changes with it: the 2026-11-12 choice that Faculty keeps its own copy of the audio ends, by the instructor's
  "Play from archive". The page's words change too: Don't keep erases Faculty's words, and the recording stays in the archive.
- The audio Faculty already holds stays until each file matches the archive's checksum and the instructor removes it from a
  displayed dry run, as STORAGE requires.

**5. Tidy.** Shadow mode deleted, the notes and the two projects' conventions brought up to date.

## The instructor's answers (2026-11-15)

1. **The brain keeps all**: a full note for every recording, the teaching ones included. "the brain should have everything
   and faculty just has the teaching relevant ones, but the brain has it as well".
2. **The teaching transcripts already in the vault stay.**
3. **The brain's nightly sync keeps running.** It was to be paused only to stop copies the instructor wants.
4. **Don't keep in Faculty means "not teaching".** The brain and the archive keep the recording.
5. **Audio is stored for everything**, "just not needed in the app itself": in the intake's archive on `/mnt`, not in the
   vault and not in Faculty. Asked whether Faculty keeps its own copy of teaching audio, they chose "Play from archive".

## Risks

- **The sign-in at step 3.** A renewal that fails after the move signs every reader out until `plaud login` is run again
  (the instructor's browser and an SSH tunnel). The renewal code moves unchanged, the cutover follows a fresh renewal, and it
  can be backed out for a day.
- **Two renewers by accident.** Faculty's renewal is deleted in the same change that makes the intake the renewer.
- **The archive is the only copy on ml-box of the audio**, the teaching ones included once step 4 lands. Hence the nightly
  verified copy on the second drive.
- **Faculty's player depends on the archive** from step 4. The transcript is complete without it.
- **Every recording is in the vault and the archive**, a student's meeting included: under the vault's git and on the
  MacBook by Syncthing. Don't keep in Faculty does not reach them, and nothing in this plan erases from either.

## Not in this plan

Filing, readings, cards, the timeline and erasure stay in Faculty. No write to Plaud beyond the renewal. No page for the
intake. No erasing from the archive or the brain. The brain is not told how Faculty filed a recording. No second recorder.
