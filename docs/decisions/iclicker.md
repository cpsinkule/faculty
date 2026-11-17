## iClicker

Rule ID: **ICLICKER**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-09-28): "integrate iclicker into the app like we did canvas and gradescope, it is the url
  instructor.iclicker.com and it has the same credentials as the other two, but there is no duo push with this one it should be
  a straight log in". Asked what it should do, they chose ONLY importing scores into the gradebook, one column per class
  session (no roster comparison, no section-tab line, no writes to iClicker).
- SIGN-IN (`iclicker/web.py`): iClicker's own JSON API, as instructor.iclicker.com's app calls it: POST
  `https://api.iclicker.com/authproxy/login` with email and password (content type `application/vnd.reef.login-proxy-request-v1+json`)
  answers a bearer token and the user ID; then `Authorization: Bearer` and `Reef-Auth-Type: oauth`. The sign-in is the file
  FACULTY_ICLICKER_CREDENTIALS_FILE names (drop-in `deploy/faculty.service.d/iclicker.conf`, web process only, naming the
  Gradescope sign-in `~/.config/faculty/gradescope.json`, which iClicker shares), read with the Gradescope file's checks only when
  signing in, by the instructor's Import from iClicker or the import after class (AFTER CLASS, a thread of the web process). The token lives in the process's memory until iClicker's expiry (never on disk, logged or returned); a refused token
  signs in once more (or takes the newer token another reader got meanwhile); a refused sign-in is never retried, by any reader
  of the process, until the sign-in file is saved again (`_REFUSED`, 2026-10-01 audit: a parallel read tried a wrong password four times). Requests go only to `api.iclicker.com` and
  `activity-service.iclicker.com` (FACULTY_ICLICKER_URL may name ONE loopback test server for both), follow no redirect, and
  Faculty never visits a logout address. Everything but the sign-in is a GET: Faculty writes nothing to iClicker. iClicker
  failures are 502 with the reason (never 401/404/409, which the app reserves); not set up is 400. Checks strip every
  `FACULTY_*` and use `tests/fake_iclicker.py`.
- READS (field names and counts read once on the real site with the instructor's approval, 2026-09-28): the courses
  (`/v1/users/<userId>/views/instructor-courses`: `courseId`, `name` like "Math-140-507-Fall-26", `term`, `archived`; events are not
  courses), a roster (`/trogon/v4/roster/course/<id>` → `rosterEntries`: `id`, names, `email`, `studentId`, `selfRemoved`), and the
  polling sessions with every student's points (activity service `/v2/courses/<id>/activities?expandChild=userActivities&active=0`,
  paged while `has-more` is true: `activityId`, `activityType`, `name`, `started`, `ended`, `answerPoints`, `correctAnswerPoints`,
  `userActivities[]` with `userId`, `enrollmentId`, `participationPoints`, `performancePoints`, `totalPoints`). 74 of 86 real
  student IDs were nine-digit UINs; every email was TAMU's. HAND CHANGES COUNT (requirement, 2026-10-08: "Students who can't get
  iclicker to work in class can email me to let me know and I manually change it"; read on the real site with their approval the
  same day): a score changed by hand in iClicker's gradebook is a `userActivities[]` record with `adjustedParticipationPoints` /
  `adjustedPerformancePoints` (null where not changed) and NO `totalPoints` (Section 507: 3 in Class 1, 3 in Class 2, each 10
  participation). Faculty had read only the original fields, so those students looked joined with no score ("not posted") and,
  under GRADEBOOK ZERO ONCE DUE, were counted 0. Each adjusted part now stands in for its original and the value is their sum
  (`iclicker_import.points`, RULE v6), as iClicker's gradebook shows it.
- A SECTION'S iCLICKER COURSE is SECTION-owned (`iclicker_course_links`, migration namespace `iclicker`, append-only, latest row
  counts), chosen in the section's Import from iClicker (GET `/api/iclicker/sections/<id>` reads Faculty only; `/courses` reads
  iClicker and proposes ONE current course whose name carries the course and section numbers in this term; POST with `base`, 409
  when stale, only a course iClicker lists now). The import itself is gradebook/README.md "Importing from iClicker": the Canvas
  planner with an iClicker source (`gradebook/iclicker_import.py`), sessions remembered by iClicker ID (`gradebook_iclicker_links`,
  term-owned). Students by the iClicker student ID when it is a UIN of the term, else by the account email equal to exactly one
  roster email of the term (never guessed; unmatched listed by name; "@email.tamu.edu" and "@tamu.edu" are ONE mailbox, instructor
  2026-10-02, since the registrar's roster gives the first and iClicker's Canvas-synced accounts the second). ONLY POLLS ARE
  IMPORTED (requirement, 2026-10-06: "just limit it to poll", after the course coordinator said iClicker's Details view, one score
  per session, is what counts, attendance not): a session whose `activityType` is `POLL` (as read on the real site for Section
  507's Class 1 to 3 that day) becomes a column; an exit poll, a quiz, attendance or any other kind is only counted, by the
  manual import and the after-class import alike (`iclicker_import.scored`). Faculty reads each session's score from the API,
  so iClicker's Overview/Details switch, which only shapes iClicker's own grade sync (off), changes nothing here. EVERY SESSION IS 10 POINTS IN CHECKS FOR UNDERSTANDING
  (instructor, the same day: "iclickers are 10 points each and they are always in the checks for understanding category (this is
  important when importing from iclicker and subsequently exporting to canvas, imporitng is going to create its column and exporting
  will need to make the corresponding assignment in the checks for understanding category in assignments in canvas)"): a new
  session column is worth 10 and goes in the term's "Checks for Understanding" category (either name, "Category" or not; else a new
  "Checks for Understanding Category", the Canvas assignment group's name). Sync with Canvas then CREATES the column's missing
  Canvas assignment in the assignment group named after its category (for Checks for Understanding a group of either name, as the
  import accepts; the exact name first) (canvas.md SYNC WITH CANVAS, `gradebook_canvas_created`). iClicker's own grade sync to
  Canvas is off (requirement, 2026-10-06): Faculty is the only writer of iClicker grades in Canvas. A score above 10 is recorded as iClicker has it, with a warning.
- KEEPS OTHERS (requirement, 2026-10-04, showing a preview that set a student's 10/10 back to 0: "this is what I don't want ie I
  change a student's iclicker to 10/10 but the import will 0 it back out"). Import from iClicker updates only a value an earlier
  iClicker import wrote (`canvas_import.Source.own`, the cell's request by `changes.origin`). A value another import brought in
  (that 10 came in from Canvas) or a pull wrote is KEPT, like a typed one, until the instructor ticks Replace for that student;
  the preview says where it came from. Typed and cleared cells stay kept as before. Import from Canvas did the same (instructor,
  the same day: "do the same for the canvas import too"; canvas.md KEEPS OTHERS). A value an excused absence put back keeps the
  origin of the revision it restores.
- SESSION DATES AND EXCUSED ABSENCES (requirement, 2026-10-01: "Whenever a student's absence is excused, any iclicker that falls in
  that date range should automatically receive 10/10"; ABSENCES ICLICKER-CREDIT). Each import records the campus date every
  session it makes a column of started on (`gradebook_iclicker_sessions`, term-owned, migration `gradebook/006_iclicker_sessions`),
  then, in the same transaction, gives the students it covers full credit for the sessions their excused absences cover (and puts
  back what an absence no longer covers). The preview names those students and sessions first (ABSENCES ICLICKER-CREDIT PREVIEW).
  Faculty still writes nothing to iClicker.
- AFTER CLASS (requirement, 2026-10-06: "i'm imagining we set iclicker imports to be scheduled every class day after each section's
  class ends (that way we immediately capture if any iclicker was given and the date so that follow-up has more accurate
  information to go on and the auto-excuse function is improved)"; `gradebook/iclicker_after_class.py`). In the web process, a
  look at the campus clock every 2 minutes: ten minutes after a section's last class of the day ends (the Term schedule's class
  meetings of a term that uses it, days off skipped; SCHEDULING), a section that links an iClicker course gets Import from
  iClicker BY ITSELF for that section, planned with no choice made (KEEPS OTHERS and Replace as always) and written through the
  instructor's own apply, so session dates are recorded and excused absences credited in the same transaction. NO ANSWER IS 0
  (asked, the instructor chose "Missing (0)"): a roster student who did not answer one of TODAY'S sessions is recorded missing;
  only today's (the roster keeps no start date, so a student added later never gets 0s for earlier classes) and only a session
  someone answered (one nobody answered makes no column). Each attempt is a `gradebook_iclicker_runs` row (migration
  `gradebook/008_iclicker_runs`, owned by the SECTION, append-only) with the day's sessions (titles and IDs, how many still
  running), counts and a problem's words, never a student. A day is done once iClicker was read and no session of the day was
  still running; a running session is looked at again every 15 minutes, a failure or a session waiting for a column every hour,
  until campus midnight; the next class day's run brings in anything missed. The section's gradebook names a run only when it
  needs the instructor (Import from iClicker brings it in now). Follow-ups read the runs and sessions: which class days had an
  iClicker, whether the student answered, and which had none; a day a section has not read yet takes what the term's other
  sections read that day (ASSISTANT ONE ICLICKER FOR EVERY SECTION; a missed iClicker is excused only with documentation).
- CREDIT BEFORE THE IMPORT (requirement, 2026-10-21: "it is frequently the case that several students cannot open the app for
  whatever reason, so I have them email me pictures of their ID and the projected question on the screen to get credit for that
  clicker question. The issue is that they have to email this to me before the end of their class period, so followup hasn't had
  time to actually import that clicker question yet to queue up a grade change for it, so it queues it up as a to-do item for me.
  Is there anyway we can fix this so that it can handle this without me needing to manually go in and change it after the fact";
  `gradebook/iclicker_credit.py`). An iClicker credit is the instructor's full credit for ONE student's iClicker polls of ONE
  class day: POST `/api/gradebook/terms/<term>/iclicker/credits` with the student's section membership, the campus date (up to
  today) and a reason of at most 200 characters (a short phrase; a follow-up is told so after a 220-character reason was refused at
  Approve, 2026-10-21), which becomes the grade's note ("<reason>: full credit."). Owned by the SECTION MEMBERSHIP
  (`gradebook_iclicker_credits`, migration `gradebook/014_iclicker_credits`, append-only, the latest row of a credit counts);
  refused when the section links no iClicker course. It is written at once for a day already imported, and otherwise by every
  later iClicker import of that section (the instructor's or the one after class) in the import's own transaction, after the
  excused absences' credit: each poll of the section's iClicker course started that day gets its column's full points, over the
  after-class 0, with the rules of an absence's credit (source `instructor`, kept by later imports; nothing written when already
  full or when the credit's fill was changed by hand since). A second credit for the same student and day already waiting is that
  credit. Withdraw (`…/credits/<credit>/withdraw`) puts back what it filled. No iClicker given that day: nothing is written.
  Both routes are Approve cards for the assistant (never automatic); a follow-up proposes the credit in place of a To do
  (ASSISTANT "ICLICKER CREDIT BEFORE THE IMPORT").
