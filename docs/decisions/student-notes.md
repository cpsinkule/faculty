## Student notes

Rule ID: **STUDENT-NOTES**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-10-01): "add a way in the app for me to take personal notes about the students, for example, a
  student scheduled one-on-one office hours today and didn't show. I'd like to record that". Built without questions; the choices
  below were mine and are the instructor's to change. `student_notes/` (service, api, migration `student_notes/001_notes`),
  `static/student-notes.js`, `static/student-notes.css`, `tests/test_student_notes.py`, `tests/test_student_notes.mjs`,
  `tests/browser_student_notes.py`.
- OWNER: the student's TERM ENROLLMENT (`student_enrollments.id`; `student_notes.term_id` is held to the enrollment's term by a
  trigger). A note stays with the student through a section change within the term and never reaches another term or course (a
  student in two courses has two enrollments, each with its own notes). Not the section membership: office hours and conversations
  are not one section's.
- A NOTE is the day it is about (a campus wall-clock date, today by default, never converted) and its words (1 to 5,000
  characters, plain text, shown escaped with its line breaks). No categories, times or attachments. Its date and words are
  immutable revisions (`student_note_revisions`, the highest counts). A save names `base_revision` (409 when the note changed since
  the page read it) and a request ID (the same ID returns what it did; a new note's ID already used for another student is a 409);
  a revision identical to the latest records nothing. DELETE is a revision marked removed and RESTORE another (Undo);
  a deleted note is restored before it can be changed. Rows are append-only (triggers): nothing is erased.
- SHOWN: the roster's student window and the gradebook's student record have a Notes part (before Absences): Add a note… opens an
  editor in place (date, note, Save; Ctrl/⌘ Enter saves), and each note, newest day first, has Edit and Delete; a delete offers Undo
  in the Notes part itself (the window is modal, so a toast's button outside it cannot be clicked). The editor is not a `<form>`,
  since the gradebook's record already is one. Words typed in the editor and not saved are kept in the tab's memory for that
  student (2026-10-02: Escape, Close or opening Communications… from the card used to lose them): the card's editor opens with
  them again, saying "Your unsaved note was kept."; Save and Cancel drop them, and so does removing every word of a new note or
  putting an edit back to the saved note; a reload forgets them (nothing is stored or sent). The palette's "Write a note about a student"
  opens a dialog with the student picker: the current terms' students by name and section, one entry per enrollment; four or more
  digits ask `POST /api/student-notes/students/search`, which answers names, never UINs. Notes are not on Home, Exams, the
  communications timeline, the gradebook grid or any export, no address carries a student, and nothing is sent: students never see
  them.
- ROUTES (`student_notes/api.py`): `GET /api/student-notes/students` (the picker's list), `POST .../students/search` (writes
  nothing), `GET .../students/<enrollment id>` (a student's notes that stand, catalog only), `POST /api/student-notes` (a new note,
  201), `POST .../<note id>` (its date or words), `POST .../<note id>/remove|restore`. Each write answers with the student's notes
  and the note's latest revision, and records who made it (`request_actor.current()`: the assistant or the workspace).
- ASSISTANT: it reads notes like any page's data (the instructor's full-access choice in ASSISTANT). Note writes are in no `auto`
  category, so each is an Approve card (the policy's default for an unnamed write); `student_notes.search` is `read`.
  - SEEN BY THE ASSISTANT (requirement, 2026-10-04: "does the agent see the notes I've typed on the students?", then "yes, do it";
    before this nothing pointed the assistant to them and no follow-up preparation had read one): each follow-up preparation's
    facts carry the student's standing notes, newest day first (at most 10, 600 characters each, through `_view`, the owner's
    reader; ASSISTANT "INSTRUCTOR'S NOTES"), and the guide has chat read them whenever it looks into or writes to one student
    (`find_student` now answers `enrollment_id`). Students still never see them: a drafted reply never quotes, mentions or hints
    at a note. Jev does not read them: every Jev/Decisions input leaves them out (2026-10-09 review, `assistant.evidence.without_notes`:
    the draft, To do obligation, evidence and tool-use checks; and since 2026-10-12 what a correction of a follow-up's card is
    compared from, ASSISTANT-MEMORY "LEARNED FROM AN EDIT", which reads the cards and the student's messages and no note).
- STORAGE: catalog data only, no payload files: the nightly catalog backup keeps it; the generic workspace export leaves it out.
  Reads of a student's notes take no lock; the picker's list reads names from each current term's verified roster payload under the
  roster lifecycle lock. A write takes the lifecycle lock, then ONE `BEGIN IMMEDIATE`.

- A NOTE OF A RECORDED MEETING (2026-11-13, RECORDINGS "Student meetings", step 4; instructor: "build 4"). After the reading of
  a student's recorded meeting, ONE Approve card (engine `recordings`, the ordinary `student_notes.create`) proposes a note
  dated the day of the recording: "Recorded meeting on <day>." and, in plain lines, what the reading kept of that student's
  part (what the instructor said they would do, what the student said they would, the days that were set, what the student
  told them). Saved only by Approve, once for a student of a recording (declined or lapsed, it is proposed again only by the
  instructor's press on the recording's page; saved, never again), and then a note like any other: theirs to edit or delete,
  private, never shown to the student. It holds a reading of a transcript: the recording and its exact words stay on the
  recording's page.
- OFFICE-HOURS PREFILL (2026-10-03, TEACHING-WORKFLOWS). Add private note from an appointment prefills the existing new-note form with its student, date and outcome. The instructor’s Save records the note; recording an appointment outcome alone never creates one.
