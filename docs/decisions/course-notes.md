## Course notes

Rule ID: **COURSE-NOTES**. Current binding module contract. Read root `AGENTS.md` first.

2026-10-09 assistant materials: an instructor-approved generated LaTeX document (compiled in the existing sandbox) or retained
course-wide PDF can enter the normal course-note upload route. The existing course-definition owner, extraction and revision
rules apply. It is not an outward send and never bypasses the exact material/destination approval.

- The instructor asked (2026-09-30): "I want to be able to upload the notes to the course page and have them there for reference. I
  want the sections to automatically be split and saved for me". `course_notes/` (sections, service, api, migration
  `course_notes/001_notes`), `static/course-notes.js`, `static/course-notes.css`, `#course/<group>/home/notes` (the course Home's
  course-wide panel links it as "Lecture notes" with its section count), `tests/test_course_notes.py`, `tests/browser_course_notes.py`.
- NAMED FROM OUTSIDE BY NUMBER (2026-10-10, SCHEDULING "CLASS SCHEDULE"): a reader outside this module that names a notes section
  (a class day's topics) keeps its NUMBER ("2.3"), never a part's position, which shifts with every edition, and resolves it
  at each read against the edition the course page shows (`course_notes.service.current_sections`, catalog only).
- TRAINING RECORDINGS (2026-11-14, RECORDINGS "Training", step 6 of the instructor's recordings plan: "training filed under
  the course"; how it is read here is mine and the instructor's to change). A recording filed as a course's TRAINING is
  reference material of the course, as its notes are, and has the same owner: the COURSE DEFINITION, so it serves every
  term and section of the code. It is shared by reference: the recording, its files and its link stay Recordings'
  (`recording_links`, kind `course`, purpose `training`; nothing is copied into this module, and no term or section gets a
  copy). The course Home's course-wide panel links "Training recordings" beside "Lecture notes", with how many there are
  and the latest one's day (`GET /api/recordings/trainings?definition_id=`), and opens `#recordings?course=<course>`,
  that course's trainings alone. A training is kept, played and searched like any kept recording and is never read for
  items, and nothing of it is sent anywhere but to the local embedding worker (EMBEDDING). Filing one is the instructor's
  (the calendar never does); by the Assistant it is an Approve card, as any filing.
- OWNER: the COURSE DEFINITION (`course_definitions.id`): one set of notes serves every term and section of the code. Files live in
  the definition's verified storage (`notes/<notes id>/original.pdf` and `part-<n>.pdf`, published immutably under the lifecycle and
  owner locks; `DocumentStorage.check_source` verifies them before a snapshot); `course_notes`, `course_notes_parts` and
  `course_notes_events` are append-only catalog rows. An EDITION is one PDF split by one rule (`UNIQUE(definition, checksum, rule)`,
  its ID derived from them, so a retried upload finishes its own folder); uploading the same file again records the upload,
  and restores the edition if it was removed (requirement, 2026-11-06: the page already said "shown again"). An edition stands from
  its latest upload; Remove (server-checked `confirmation: "remove"`) hides it until Restore or a new upload; the page shows
  the newest edition that stands, earlier and removed ones listed below. Nothing is deleted.
- UPLOAD SAVES AT ONCE (the instructor's "automatically"), a PDF only (a Word file is told to Save As PDF; the Canvas export's notes
  come as both). The document sandbox reads it (`notes_outline`: bookmarks, the first lines of every page) and cuts each part page for
  page (`notes_split`, pypdf: nothing re-rendered; each part's title is its section and its topics are its bookmarks).
  `course_notes/sections.py` (rule `notes-sections-v1`) decides from BOTH bookmarks and the heading that opens a page ("2.3 Systems
  …", a capital-letter title among the first lines once the running header is set aside), never one guessed from the other: a
  section with only one of them is kept and named in the edition's notes (the MATH 140 Fall 2026 notes, 204 pages, have no bookmark
  for 5.1: found from its heading), a contents page (three or more numbered lines) opens nothing, sections must keep number and page
  order (one that breaks it is left out and named), a section beginning below the top of a page shares that page with the one
  before, pages before the first section are the front matter, other bookmarks become topics of the part holding their page, and no
  section found keeps the notes whole. The chapter title is the text printed above a chapter's first section. A changed rule is a
  new RULE name. Nothing is sent anywhere (the current edition's pages and their own text are embedded on this machine so a page can be found by describing it: PICTURE-SEARCH); the PDF routes are inline (no-store), and on the course page a section,
  a topic (at its page) or the whole notes open in the app's file viewer (VIEWER in assistant.md; requirement, 2026-10-01: "switch those
  other download links to the viewer too"), with Download there and on each section's download button; the assistant never uploads (`course_notes.upload` is
  `never`) and asks before removing.
