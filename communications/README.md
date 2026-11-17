# Communications record

Every electronic message between the instructor and a student, kept and shown as one timeline per student for each term
(requirement, 2026-09-30). The rules are in AGENTS.md "Communications record"; this file is how to run and check it.

## Where things are

| Piece | What it does |
| --- | --- |
| `service.py` | Capture rounds (Gmail, Canvas, Gradescope), their schedule and status, and reading the record |
| `gmail_capture.py` | The Gmail search by roster address, `format=raw` reads, parsing a kept message and its attachments, and Canvas's message notification emails (the backup of the Canvas inbox) |
| `drive_links.py` | A file a student linked from Google Drive: finding the link in their own words (the mail filter's rewriting undone), and reading that one file (`files.get`, `files.export`) |
| `gradescope_capture.py` | Regrade Requests pages and the grading page's requests; who the student is |
| `record.py` | Recording a message (revisions, links) and one student's timeline, with Faculty's own sends by reference |
| `storage.py` | Bundles of kept originals: the `communications/` owner of the verified storage engine |
| `api.py` | `/api/communications/...`: status, Capture now, the Google Drive read permission (`POST /drive/connect`), a student's timeline, revisions, kept files (downloads only), and a seat-chart email's PDF (`GET /seat-mail/<id>/chart.pdf`) |
| `canvas/web.py` | `conversations`, `conversation` (never marked read), `submission_comments`, `download` |

## Running

- The Inbox worker (`faculty-inbox`) runs the capture thread. Gmail needs the Mail page's read connection; Canvas needs the kept
  Canvas session in the worker (`deploy/faculty-inbox.service.d/canvas.conf`) and a live sign-in; Gradescope needs the saved sign-in
  (`FACULTY_GRADESCOPE_CREDENTIALS_FILE`, already in the worker's drop-in). A file a student links from Google Drive needs one
  more permission of the same Google connection (read-only Drive): "Allow Faculty to read it" on the timeline's Google Drive
  line, or on the file in a follow-up; the Google Drive API must be on for Faculty's Google Cloud project.
- `GET /api/communications/status` says how each channel stands; `POST /api/communications/capture` (`{"channels": [...]}`, all by
  default) asks for a round now.
- THE COORDINATOR'S MAIL (2026-11-14): mail with a coordinated term's coordinator (`course_coordinators`) is searched for and
  kept like a student's; its link names the term's owner key `coordinator:<term id>` with `matched_by`
  `coordinator_address` (migration `003_coordinator_links`), its author role is `other`, and it is read with
  `GET /api/communications/coordinators/<term id>`.
- Kept originals are backed up by the nightly coordinator like any owner (`manage_storage.py status` lists a bundle's storage error).

## Checking

```
PYTHONPATH=.:tests .venv/bin/python -m unittest test_communications
node --test tests/test_communications.mjs
.venv/bin/python tests/browser_communications.py
```

The tests use loopback fakes (`tests/fake_canvas.py`, `tests/fake_gradescope.py`, a fake Gmail and a fake Drive in the test file);
nothing reaches a real service.

## Not verified on the real sites

- Gradescope's Regrade Requests page and the grading page's `open_request`/`closed_requests` were not read on the real site while
  this was built (a read-only probe was declined by the session's permission check). Since then they have been: the live captures
  (from 2026-09-30) and the sandbox regrade rehearsal of 2026-10-07 (REGRADES), which read an open request, then the same request
  closed with Faculty's reply. The list's "Completed" column stays blank after a reply; the grading page says it is closed.
- Google Drive (`drive_links.py`, 2026-10-11) follows Drive's documented v3 API (`files.get` with `fields`, `alt=media`,
  `files.export`); it was exercised only against the fake. Check the first live fetch: the file's line on the message, and that
  it opens.
- Canvas conversations, submission comments and attachment downloads follow Canvas's documented REST API; the kept session had
  expired when this was built, so they were exercised only against the fake. Check the first live rounds.
