## Student photos

Rule ID: **PHOTOS**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor sent Howdy's printed "roster with photos" PDFs (one per section, Taildrop) and asked (2026-09-29): "crop and associate the
  pictures with the students and have it appear in the student info panel when their name is clicked on in the gradebook and also show the
  picture whenever I open an email from that student". `student_photos/` (service, api, migration `student_photos/001_photos`),
  `document_identity` operation `roster_photos`, `tests/test_student_photos.py`.
- READING THE PDF: in the document sandbox (never in the web process). The page holds each photo as a bitmap with its caption
  "12. Last, First Middle" under it, so the photos are the PDF's own embedded images (scaled to at most 256 px, JPEG), not crops of a
  raster, and the caption is the text in the band beneath. A caption is an untrusted draft: it is matched to the section's roster by NAME,
  exactly (case, accents, punctuation and word order ignored) and only when it names one student that no other caption names. Anything
  else (no student, two students, two captions) is listed in the preview and left alone; nothing is guessed. All 340 photos of the four
  MATH 140 sections matched (2026-09-29).
- OWNER: the student's TERM ENROLLMENT (`student_enrollments.id`); `student_photos` rows are immutable revisions (latest counts, an identical
  photo records nothing) with one `student_photo_imports` receipt per import (source name and SHA-256, counts, leftovers). The source PDF
  is not kept (the originals stay in `/mnt/nvme/Teaching/incoming/roster-photos/`). Catalog rows, so the nightly catalog backup keeps them.
- PRIVATE: TAMU's page says photos are official education records (SAP 29.01.03.M02.02). They are in no JSON and no generic export; JSON
  carries only a 12-character checksum prefix (`photo`) so a page knows a student has one; the image is `GET /api/student-photos/<enrollment id>`
  (`no-store` by the app's API rule, ETag). Preview writes nothing; the import (`POST /api/student-photos/sections/<group>/<section>/import`,
  multipart) needs the preview's fingerprint, a request ID and `confirmation: "import"`. There is no upload dialog yet: imports are run
  through those routes (the preview and import script is the operator's). The assistant can show a photo in its drawer
  (`fetch_files`, which loads it from this route) and never sends one to its model (`policy.SHOW_ONLY`).
- SHOWN in the roster's student window (`courses.js`), the gradebook's student record (`gradebook-dialogs.js`, from `students[].photo` and
  `enrollment_id`) and Mail: a message written from a current student's roster address shows their photo, name and section above it
  (`gmail_mail` `_students`, in process memory, rebuilt every 2 minutes; an address two students share names nobody). TAMU delivers
  `name@email.tamu.edu` (the roster's form) and `name@tamu.edu` (how students write) to the same student, so those two domains with the same
  part before the `@` are ONE address (`variants`); no other domain is folded, and a student's mail search asks for both spellings.
- CANVAS MESSAGES (instructor, the same day: "it should also handle when students message me through canvas"): Canvas mails every message as
  "<student's name> <notifications@instructure.com>", so the address names nobody; the subject carries the course and section ("Ann Lee
  (26 FALL MATH 140 514: TITLE) just sent you a message in Canvas."). `canvas_student` names the sender only when the display name has two or
  more words, every one is a word of exactly ONE student's roster name (middle names on the roster do not matter), and, when the subject names
  a course section, that student is in it; the card then says "via Canvas". Two students, none, or a one-word name name nobody. A student's
  mail search also asks for `from:instructure.com subject:(<given> <family>)`. Every current student is in the name index, whatever their
  address. Nothing is sent to Canvas: a reply goes by email, as Canvas's own message says ("reply directly to this email").
