## Course coordinator

Rule ID: **COORDINATOR**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-11-14): "I want to add an optional setting when a course is made, like a checkbox in the course
  creation card, that indicates if it is a coordinated course or not. The checkbox will have greyed out fields for the Name and
  email of the coordinator unless I check the box. Then, I want the coordinators emails to be included in the followup pipeline
  just like student's. Obviously, we should add something so the agent knows it isn't a student but the coordinator so it can
  suggest actions appropriately". `course_coordinators/` (service, migration namespace `course_coordinators`),
  `tests/test_coordinators.py`.
- **OWNER: THE TERM** (`course_terms.id`; my choice, not asked: who coordinates a course is a fact of one teaching term, and
  Follow-up, its term policies and the communications window are all per term). `course_term_coordinators` holds immutable
  revisions of the term's whole answer (coordinated or not, the coordinator's name and email; append-only, the highest
  revision counts, earlier ones retained). A term with no row is not coordinated. A saved offering entry (`courses`) owns
  nothing: every offering of a term reads and writes the same answer, and nothing is copied to a section or to another term
  (next term's card asks again). Catalog data: in the nightly catalog backup, not in the generic export.
- **THE COURSE CARD.** Add a course and Course settings have a Coordinated course box with Name and Email under it, greyed
  out (a disabled fieldset) until the box is ticked; ticked, both are required, the address in a plain `name@domain` form.
  The card sends `coordinator: {coordinated, name, email}` with the course it saves (`POST /api/courses`, `PATCH
  /api/courses/<id>`), checked before anything is saved and recorded in that save's own transaction for the term the saved
  offering belongs to; the same answer records nothing, and a save that sends no `coordinator` leaves it as it is. Course
  settings reads it when the card opens (`GET /api/courses/<id>/coordinator`); until that read comes back the box is disabled
  and Save leaves the answer alone. The course records and `/api/state` are unchanged (PERFORMANCE). An assistant's course
  save is an Approve card as before (the policy names neither route).
- **THE OWNER KEY.** The coordinator's email comes to Follow-up as a student's does (COMMUNICATIONS "THE COORDINATOR'S MAIL",
  TEACHING-WORKFLOWS "A COORDINATOR'S FOLLOW-UP", ASSISTANT "A COORDINATOR'S FOLLOW-UP"). Where those records say whose
  conversation it is by a student's enrollment ID, a coordinator's says `coordinator:<term id>`
  (`course_coordinators.service.owner_key`): the term owns it. An enrollment ID is ten digits, so the two never collide, and
  anything that looks a student up by that key finds nobody (a 404, an empty read), which is the right answer for a record
  that is no student's. The key is never a UIN, an enrollment or a roster entry, and never appears on a roster, in the
  gradebook, on a seating chart or in any student list.
- **WHO THE COORDINATOR IS NOW** (`current`, `of_owner`): the latest revision of a current, undeleted term, when it is
  coordinated. `since` is when that address became the term's coordinator (the first of the latest revisions in a row that
  name it, however the name was spelled meanwhile): mail from before then is background, never a queue (TEACHING-WORKFLOWS).
  Unticking the box ends it: no new suggestion, no preparation, no address to write to; what was kept and tracked stays.
- **ONE NAME** (requirement, 2026-11-15: "join the recordings coordinator name with the course card's"). The course card's
  coordinator is the term's coordinator wherever Faculty names one. The reading of a recorded meeting is told that name
  (RECORDINGS "The reading" › FACTS) and the recording's page shows it, with `card` true: there "Coordinator: <name>" opens a
  window that says it is named on the course card and is changed in Course settings, and `POST /api/recordings/coordinator`
  answers 409 for that term. Read by reference, never copied. Where the card names none, the name alone that Recordings kept
  for the term (`recording_coordinators`) still stands and may still be set there (a course whose coordinator's mail is not
  wanted in Follow-up can still name them for its recordings); ticking Coordinated course later takes its place without
  changing those rows. A name changed on the card reads no recording again by itself (Read again does).
- Faculty sends nothing here and reads no outside service. Every message to the coordinator goes through Mail's review and
  its explicit Send.
