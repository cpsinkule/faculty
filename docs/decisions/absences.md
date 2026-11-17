## Excused absences

Rule ID: **ABSENCES**. Current binding module contract. Read root `AGENTS.md` first.

- **EMAIL-PREFILL** (requirement, 2026-10-01: "my agent prefills all of that info for me from the email", using Claude or Codex
  according to the selected Assistant model). Mail offers Draft absence on each message with exactly one roster-matched student.
  `absences/drafting.py` and POST `/api/absences/drafts/mail` own an EPHEMERAL prefill for that SECTION MEMBERSHIP, held only in the
  assistant's ordinary conversation lifecycle and busy gate, never the catalog or payload storage. Source identity and text are
  reread through Mail's own route; the model cannot choose another student. Use the selected Assistant model and reasoning with
  the existing engines (Claude, Codex, including gateways); no tools or MCP access, with a server-side refusal of every tool call.
  Email is untrusted data. Dates resolve against its campus-local sent date; unknown/ambiguous fields stay blank with review notes;
  validate dates, range, reason and bounded details. The decision starts Waiting and its note blank. No record, documentation,
  grade or outward write is made by drafting. Cancel stops the engine; late answers cannot replace another dialog.
  The ordinary Record absence form reviews/edits the draft and selects its PDF/photo attachments. Its explicit Save records the
  absence and adds chosen documentation: captured attachments remain references with their original indices; otherwise POST
  `.../documents/mail` rereads the exact message through Mail, rechecks the same student/section, fetches its chosen part through
  the app's own file route and keeps it as an ordinary verified section upload (20 MB and the existing byte-signature checks).
  Stable request IDs prevent duplicate files on retries. After the absence and selected documentation save successfully, close
  the email draft dialog and show the "Absence recorded" confirmation (requirement, 2026-10-01). Attachment failures leave the
  recorded absence visible with an explicit notice. If capture finishes during review, the Save matches the selected file by
  checksum and keeps its shared reference.
  Nothing is silently repeated. The model never determines that an attachment proves an excuse. Tests use fake Gmail and
  scripted Claude/Codex, never real credentials or models.
  JEV (2026-10-03, requested addition 3): after the selected Assistant extracts
  the draft, check the proposed dates, reason and details against the exact source
  inside the same busy conversation. Code resolves clear relative dates from the
  campus sent day and supplies ambiguous weekday candidates without choosing one.
  Since 2026-10-08 (JEV DATES) code itself matches the drafted dates against the days
  the email names; for a bare weekday Jev says only whether it was already past
  (each weekday asked about on its own, and without Jev's answer the date is blanked
  with both candidates named: JEV "ABSENCE CHECK v3", 2026-10-09).
  Unsupported/conflicting dates and reasons stay blank with warnings; a failed
  check leaves the draft usable and explicitly unverified. Nothing is saved or
  excused by either model. See JEV for computing receipts and source handling.

- **WITH DOCUMENTS** (requirement, 2026-10-04: asked why a follow-up suggested attaching a student's receipts only in a later
  round — the absence's ID did not exist until it was recorded — they chose "yes, do it": record the absence and its files in one
  step). POST `/api/absences` also takes `documents`, up to 10 `{"message_id", "index"}` naming attachments already kept in THIS
  student's communications record (the same check as `.../documents/message`). Every one is checked and read before anything is
  written; one that is not this student's, not kept or not found records nothing (no absence, no document). They are kept as
  references, exactly as Add from their messages keeps them, in the same transaction as the absence's first revision; the same
  request ID returns the same absence with its documents. Nothing else changes: the decision, iClicker credit and makeups follow
  the absence's own facts. The Absences page's own Save still records then attaches (unchanged); a follow-up suggestion proposes
  the absence with its documents as one approval card (ASSISTANT). On that card the instructor may choose another decision than
  the one proposed, from its Decision dropdown, before recording it (ASSISTANT "DECISION ON THE CARD", 2026-10-12); the absences
  service sees only the request they approve. A decision they changed and recorded may be remembered as how that kind of
  absence is decided (ASSISTANT-MEMORY "LEARNED FROM AN EDIT"): the suggested and the chosen decision, the absence's reason
  and whether documents were attached are compared, never its dates, details or note, and nothing about the student is kept.

- **DECISION DEADLINE** (requirement, 2026-10-04: "When an absence is opened, it should automatically turn into unexcused after the
  second business day after the last day of the absence"; asked, they chose the end of the second business day rather than the
  documentation deadline of the third, and that an absence with documentation keeps waiting for them; then "When an absence is
  undecided but has attached documentation, it should become brought to my attention after the 2 business days somehow"; on
  2026-10-05, told it was a day stricter than Student Rule 7.3.1.1's three business days for documentation and that holidays
  counted, they said "fix the holidays and the too strict documentation deadline"). The deadline of an absence is the campus day
  after the THIRD business day after its last day (`documentation_due`, `decision_deadline`; business days skip Texas A&M's
  university holidays, SCHEDULING BUSINESS DAYS). From then, on the Inbox worker's bounded turn
  (`expire_one`, one absence per turn, a look at most once a minute when none is due):
  - an absence of a current term still Waiting for your decision, with no standing document, gets its next revision with the
    decision Not excused, actor `automatic`, request ID `automatic-not-excused:<absence>:<revision>`, and the note "Not excused
    automatically: no documentation and no decision by the end of <day>, the third business day after the absence." before any
    note it had; its iClicker credit is brought in line as on any save (nothing to put back for an undecided absence). Once per
    absence: an absence the worker decided and the instructor reopened stays theirs. An absence recorded after its deadline with
    no documentation is decided on the worker's next look. Nothing is sent to the student.
    DEADLINE CLOSES (requirement, 2026-10-08, of a follow-up still Waiting on student for a doctor's note after its absence was
    marked Not excused automatically: "this student's available window to submit documentation has closed, but this follow up is
    still in the stack. It should resolve when that happens"): that decision also resolves the follow-up waiting on it, by itself,
    with Undo (TEACHING-WORKFLOWS WAITING ON). The absences service records nothing more; the follow-up's own route does.
  - one with documentation stays undecided; the Absences page marks it "Past deadline" (`past_deadline` in the list) and Home's
    absences card leads with it ("Absences past their deadline", `past_deadline` count) — one card, so the sidebar badge still
    counts each absence once.
  - DEATHS WAIT (requirement, 2026-10-05, of a student's bereavement absence whose funeral arrangements were not yet known and whose
    documentation had not been asked for: "deaths do not automatically expire as not excused"): a revision's `death` fact
    (migration `absences/004`, 0 or 1, carried forward like the other facts; owner: the absence's section membership) marks an
    absence for a death, whatever its reason (a death of someone not yet confirmed as immediate family is recorded as Other). The
    worker never decides one; it waits as Waiting for your decision (and, documented, is marked Past deadline like any other). The
    absence form's "A death" checkbox sets it; a follow-up preparation sets `"death": true` when the student reports a death.

- **RULE 7 NOTICE** (requirement, 2026-10-05, asked whether Faculty applies Student Rule 7's two-business-day requirement and told
  that nothing compared the student's notice with it: "build the notice on time / late check"). Rule 7.1.1: notice before the
  absence when feasible, else by the end of the second business day after its last day. An absence revision may name its notice,
  `notice_message_id` (migration `absences/003`): the student's own message that first told the instructor, a reference to a
  message in that student's communications record that the student wrote (checked on create and when it changes; owner: the
  absence's section membership, carried to each later revision like the other facts; none is "No notice on record"). The list
  and the absence read `notice` (`notice_timing`: "advance" before the first day, "after" from then to the end of `notice_due`,
  "late" after it, each on the campus wall clock from the message's sent time, with `due`) and `documentation_due`; the page shows
  a Notice column (Late marked), and the absence's window when it came, the two Rule 7 days and Choose their message… (their own
  messages; Save changes keeps it). Mail's Draft absence names the drafted email when it is captured and the student's; a follow-up
  preparation is told the conversation's first student message and must name a notice to record an absence (ASSISTANT). Notice
  timing informs the decision and changes nothing by itself: the DECISION DEADLINE alone decides automatically. Business days
  skip university holidays; an interview's documentation due in advance is not checked.

- **RECORDED BY THE ASSISTANT** (requirement, 2026-10-04, DONE FOR YOU in ASSISTANT): a follow-up preparation records a student's
  absence by itself — always Waiting for your decision, with the files they sent in that conversation, never overlapping one of
  their standing absences — and attaches their files to their absence; both are the absences service's own routes, recorded by
  the assistant. The instructor's Undo withdraws such an absence (only while it is still its first, undecided revision with only
  its own documents) or removes such a document (while the absence is undecided), as the instructor. DECISION DEADLINE applies
  to these absences like any other. A follow-up never proposes Excused on the student's own account (EXCUSE NEEDS EVIDENCE,
  ASSISTANT, 2026-10-08), whatever a kept memory says. Only days the student says they missed or will miss are recorded this way: a day they wrote for a
  meeting, a due date or their return makes the absence a card (JEV A DAY THEY MISS, 2026-10-10), and so does a day named
  only through a week ("this week") the absence does not cover (JEV WHOLE WEEK ONLY, 2026-10-10), or a class day of their
  section inside the absence that they never wrote (JEV EVERY CLASS DAY INSIDE, 2026-10-10).
  A follow-up proposes Not excused, on a card and never by itself, only when the reason the student gives is outside the ones
  Student Rule 7 excuses, so no documentation could change it (NOT EXCUSED WHEN NOTHING COULD EXCUSE IT, ASSISTANT, 2026-11-13);
  Rule 7's own reasons, a death and any absence they sent files for wait for the instructor's decision with none proposed. A
  proposed decision, or a note on one, is never an absence the preparation records by itself: a decision is the instructor's.
  A FOLLOW-UP NAMES CLASS DAYS (requirement, 2026-11-03, of a card a redo made by splitting a reported period by its decisions, which
  left Sat, Oct 31 on its own: "why is it asking me to excuse a saturday"): whichever way it is proposed, an absence a follow-up
  proposes starts and ends on a day the student can miss, `absences.totals.missable_ends`: a class day of their section (TOTAL
  ABSENCES' CLASS DAYS ONLY; every Monday to Friday while the section has none on record), a day work is given to them, planned or on record (the class schedule's
  `given` rows, `given_dates`: an evening exam is on no class day, and its department makeup needs the absence to cover it), or a
  weekday past the section's last class (the exam period). `validate_write` refuses other dates, naming the ones to propose, while the model can still correct them;
  with no such day there is nothing to record, and its summary says so. The whole period and a return date stay in `details`, where
  quiz and department makeups read the return date (`return_date`). Days between the two ends may be any days,
  as they always were: only class days count. Dates the instructor gives on the Absences page, and a card already waiting when it is
  approved, are their word and are not checked.

- **NO-GRADEBOOK** (requirement, 2026-10-01: "I don't want the absences functionality to affect the gradebook (maybe I'll implement
  it later, but not as of yet)"). Asked, they chose to remove the whole excuse step, exam records included, since an exam record
  marked excused becomes an Excused cell on the next gradebook pull; they kept the read-only Absences part of the gradebook's
  student record. An absence, whatever its decision, writes nothing outside `absences/` but its iClicker credit (ICLICKER-CREDIT,
  the instructor's one exception, the same day): no other gradebook cell, no exam participant, no receipt. There is no `covered`
  or `excuse` route and no assistant rule for one. The `absence_applications` table (migration `absences/001_absences`,
  immutable) stays in the catalog unread; live had no rows when the step was removed. Excusing a student's other work stays the
  instructor's own action on the gradebook or the Exams page. Any further effect is a new instructor decision and a change to
  this contract. One READ followed (requirement, 2026-10-06; GRADEBOOK ZERO ONCE DUE): under the gradebook's `ungraded: "due"`, a
  makeup owed (MAKEUPS, `owed`, or `if_excused` while the absence waits for a decision) keeps that student's empty cell of the
  assignment from counting as 0. It is computed with the gradebook's results; nothing is written anywhere. A second READ
  (requirement, 2026-10-07; GRADING CERTIFICATION "MAKEUP OWED"): Certify leaves out a student with no work whose
  excused or undecided absence covers the day (`makeups.covering`), and is changed once it no longer does, so the instructor's
  next certification records the 0; the absence itself still writes nothing. Since 2026-11-14 Faculty makes that certification by
  itself, and the one that leaves out a student certified 0 once a makeup is owed (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES",
  the instructor's "Build both"): the cell is still written by the certification through the gradebook's own plan, never by an
  absence. A third READ (requirement, 2026-10-31; TOTAL ABSENCES
  below) reads iClicker and quiz cells to count a student's days missed; nothing is written. The one other write absences lead to is the instructor's explicit Schedule makeup click (MAKEUPS, 2026-10-03):
  a student's SITTING on the exam record, through the Exams page's own routes; never their expectation, a cell or a receipt. A
  makeup recorded missed (MAKEUPS "MISSED", 2026-10-31; by Faculty itself for a quiz's booking card left unanswered, MAKEUP
  BOOKING › NO ANSWER, THEN A 0, 2026-11-12) is kept in `absences/` only; the gradebook and certification read it. A fourth READ
  (requirement, 2026-11-14; NO ANSWER, THEN A 0 › A SCORE STANDS) reads a quiz's gradebook cells before Faculty records or keeps
  its own 0 for an unanswered card; nothing is written there.

- **ICLICKER-CREDIT** (requirement, 2026-10-01, after NO-GRADEBOOK: "There is one way an excused absence should interact with the
  gradebook: iClicker assignments. Whenever a student's absence is excused, any iclicker that falls in that date range should
  automatically receive 10/10"). Automatic, from the instructor's own save; nothing is asked. Every absence save that records a
  new revision (create or revise), and every Import from iClicker (for the students it covers), brings the student's iClicker
  cells in line with their excused absences IN THE SAME TRANSACTION (`GradebookService.credit_excused`,
  `gradebook/excused_credit.py`):
  - COVERED: a polling session of the iClicker course linked to the absence's SECTION, whose campus start date (recorded by the
    import that read it: `gradebook_iclicker_sessions`, migration `gradebook/006_iclicker_sessions`, term-owned, latest counts)
    falls within the dates of an EXCUSED absence of that section membership, and whose column (`gradebook_iclicker_links`) is in
    the setup, not archived, applies to the section and is worth more than 0. Only iClicker columns are ever read or written.
  - FILL: a covered cell below the column's full points (10 for iClicker) gets the full points with the note "Excused absence
    (<dates>): full credit." (dates only, never the reason), source `instructor`, so an import keeps it unless Replace is ticked
    for that student. Nothing is written to a cell already at full points or above, or to one an absence filled that was changed
    after (typed in Faculty, or replaced by an import): that change stands.
  - PUT BACK: a filled cell no excused absence covers any more (the decision changed, the dates moved, the session went to
    another column) gets back exactly the revision the fill replaced (status, points, note, source), or is emptied as an import
    leaves it (cleared, source `import`) when the fill was its first value. A cell another excused absence still covers keeps it.
  - RECORD: each absence's fills, and each absence's put-backs, are one gradebook `scores` request (request ID
    `absence:<absence>:<action>:<uuid>`, summary `source: "absence"`, `absence_id`, `action` `fill`/`put_back`, `dates`), by the
    actor who saved (GRADEBOOK ACTOR: absences' create and revise routes pass `request_actor.current()`). The Changes page names it
    (`absence` origin), as does the cell's history. A save's answer carries `iclicker_credit` `{filled, put_back}` when it changed
    something; the absence's window lists the sessions it gives full credit now (`iclicker`) and the decision says what Excused does.
  - PREVIEW (requirement, 2026-10-02: "yes add the preview line"): Import from iClicker's "When you press Import" names the students
    and sessions Import will give full credit ("Gives 3 iClicker sessions full credit (10 points) for excused absences. Example,
    Bob: Session 1 (Tue, Sep 1), …", six students named, then "and N more"), and counts kept values that are an absence's credit
    apart from values typed in Faculty. The plan's `excused` `{cells, points, kept, students: [{name, section, sessions: [{title,
    date, day}]}]}` is `excused_credit` on the term as it will stand after the import (`_excused_preview`), absent when there is
    nothing to say; it is part of the fingerprint, so an absence decided between the preview and Import asks for a new preview.
  - Nothing outward: no Canvas, iClicker or mail write (Sync with Canvas stays the instructor's own action). An absence excused
    before any iClicker import is credited by the first import that reads its sessions: since 2026-10-06 the import that runs by
    itself after each class (iclicker.md AFTER CLASS), so an absence excused for today is credited when today's class ends. Tests: `test_iclicker.py`
    `ExcusedAbsenceTests` (fake iClicker), `test_absences.py` (other columns and exam records untouched).

- **MAKEUPS** (requirement, 2026-10-03: "I want excused absences to interact with the 'given on' date of an assignment so that I get a
  reminder/notification that this student needs to makeup the assignment and give the option to generate a makeup that is based on
  the original assignment/rubrics"). Asked, they chose: an EXCUSED absence counts on Home, an undecided one is a heads-up only; ONLY a
  received paper clears a reminder (not scheduling, and there is no dismiss; since 2026-10-31 a makeup the instructor records missed
  also ends it, MISSED below); the makeup is a new version of the same assignment.
  - OWED: `absences/makeups.py` `owed`, a read-only projection kept nowhere. An absence (excused: `owed`; pending: `if_excused`;
    withdrawn or not excused: nothing) of a membership not dropped covers an exam record (`exam_events`, quizzes and exams alike) of its
    SECTION when the day that record's assignment was given to the student (EXAMS "DATE GIVEN", `own_makeup=False`: a makeup sitting
    scheduled later never moves it) falls within the absence's dates. The row stands until the Exams page's own collection of the
    student's paper (`ExamService._participant_paper`, catalog only, scans deleted in the Inbox left out) is anything but no scan,
    absent or excused. One row per exam record and student, the excused absence preferred. Each row carries the student's makeup
    sitting when one is set (shown; it still counts) and the makeup versions approved into the plan (GRADING "MAKEUP VERSIONS").
  - SHOWN: Home's Needs you and the absences badge count the `owed` rows of current terms ("Makeups owed", href
    `#absences?view=makeups`, no names: `AbsenceService.makeups_owed`). GET `/api/absences/makeups?scope=current|all` lists them with
    names; the Absences page's Makeups view groups them by assignment with Schedule makeup, Draft makeup version and Open on Exams;
    the absence window lists "Work given during this absence" (the absence's additive `work`); an assignment card on Assignments &
    printing says how many makeups are owed and offers Draft makeup version.
  - SCHEDULE MAKEUP (`static/absence-makeups.js`): the instructor picks or creates a makeup sitting of that exam record and ticks the
    students; the page calls the Exams page's own routes (POST `/api/exams/<event>/sittings` with kind `makeup`, POST `.../roster` when
    a student is not on the record yet, PATCH `.../participants/<section>/<enrollment>` with `sitting_id` only). Nothing else is written.
    KEEP THEIR SITTING (requirement, 2026-10-06, as in GRADING's makeup approval window): a student already on a makeup sitting of
    the record (the row's `scheduled`) starts unticked, their row says where they are ("on <sitting>; tick to move them", or "already
    on this sitting"), and the window says how many ticked students Schedule moves off another makeup sitting. Ticking one still
    moves them. Each sitting reads once, as in GRADING's window ("A CLEAR APPROVAL WINDOW", 2026-11-01: `sittingText`).
  - SCHEDULED WITHOUT AN ABSENCE (requirement, 2026-10-19: "if I schedule a makeup for anyone, can we make it show in the makeups
    owed page?", after a Quiz 3 replacement for a lost paper, with no absence, was missing from it): GET `/api/absences/makeups` and
    so the Makeups view also list each student on a makeup sitting of an exam record whom no absence claims (`makeups.seated`),
    state `scheduled` ("Makeup scheduled", "No absence", the name not a link), until a paper of theirs comes back; the count line
    adds "N scheduled without an absence". They are the Makeups view's only: `owed`, Home's count, booking cards, the gradebook's
    blanks and Certification never see them.
  - MISSED (requirement, 2026-10-31, of a student whose excused absence covered Exam 2 and who then missed her makeup: "it won't let
    me clear her makeup owed, she missed the makeup and missed it so she got a 0"). This amends "there is no dismiss": "Missed
    makeup…" on a row owed for an excused absence, or scheduled without one, asks first ("Missed makeup, record 0") and records it
    (POST `/api/absences/makeups/missed` {event_id, section_id, enrollment_id, missed, request_id}; `absence_makeups_missed`,
    migration `absences/009_makeups_missed`; owner: the student's section membership on that exam record, the claiming absence
    named; append-only revisions). An undecided absence's row offers none: decide it first (not excused already gives the 0). From
    then on `owed`, `seated`, `covering` and the department makeup rows leave that student and exam record out: Home's count and
    the badge drop it, an unanswered booking card is withdrawn, the gradebook counts the empty cell as 0 (ZERO ONCE DUE, so Sync
    with Canvas sends it), and the certification reads changed (`makeup_ended`) so the instructor's next Certify records the 0,
    its detail "Missed the makeup" (reason `no_paper`). Faculty never certifies it by itself. Since 2026-11-12 Faculty records
    it missed itself for a quiz's booking card left unanswered (MAKEUP BOOKING › NO ANSWER, THEN A 0). The absence window lists the work as
    "Missed makeup: 0" with Undo (`missed` false), which makes it owed again. Nothing else is written: no cell, no participant, no
    receipt. Refused unless the makeup is owed now. Absence totals are unchanged: an exam's 0 is never counted, and a quiz's 0 was
    already counted on the day it was given.
  - The assistant reads GET `/api/absences/makeups` as any GET; Schedule makeup's writes are the Exams routes, under their own rules.
- **MAKEUP BOOKING** (requirement, 2026-10-04: "help me brainstorm automation of makeups (specifically quizzes, will deal with exams
  later)"). Asked, they chose: the makeup VERSION is drafted automatically the first time a quiz owes a makeup (they approve it); the
  student picks the time on a Response Card, which is also the notice; the card offers "Monday or Tuesday office hours the following
  week as well as the ability to select a time that tuesday from 1PM-5PM" (in BLOC 319, every 30 minutes, calendar-free; the week
  after the QUIZ's given date, past times dropped, else the week after); a pick seats the student automatically; the card goes out as
  soon as the absence is Excused; no answer after 3 business days, and a missed makeup, are flagged to them. `absences/makeup_booking.py`
  (service, worker turn), `absences/makeup_slots.py` (pure: the times), migration `absences/002_makeup_booking`.
  - QUIZZES ONLY: an exam record none of whose term's seating charts is for its assignment (exams are seated). Exams stay manual
    (their department makeup request is a card since 2026-10-22: DEPARTMENT MAKEUP EXAMS).
  - SWITCH: `absence_makeup_settings` (owner: the TERM, a revision per change, off until turned on; PUT
    `/api/absences/makeups/terms/<term>` `{request_id, cards, revision}`, 409 when stale). Turning it on in the Makeups view first shows
    the dry run (GET `/api/absences/makeups/plan`: each owed quiz makeup without a card or a makeup sitting, and the times its card
    would offer now; writes nothing).
  - TIMES (`makeup_slots.offer`): the Monday after the quiz's given date starts the week; its Monday and Tuesday office-hours blocks
    (the instructor's own timed calendar events saying "office hour", the follow-up assistant's rule; a student's appointment is no
    block) and a start every 30 minutes in the term's MEETING HOURS on that Monday and Tuesday (TEACHING-WORKFLOWS; each in its own
    room; none while the term has none: office hours only; a first card waits while Faculty cannot check the instructor's Google
    calendar, SCHEDULING "GOOGLE CHECKED", 2026-10-05), a start left out when anything timed on the calendar (Faculty's events,
    the Google copy, sittings other than makeup sittings; all-day entries never) overlaps its 30 minutes. SHAREABLE (instructor,
    2026-10-04: "makeup events and office hour events are fine to overlap (i.e. student has an appointment for office hours or
    other meeting)"): a student's appointment (any kind; the calendar shows each as "Office hours"), any event titled office
    hours and any titled makeup (make-up, make up; the instructor's own Google "… quiz makeup" events included) never take a
    start (`makeup_slots.shareable`; narrowed to makeups only on 2026-10-05, below), for the card and a follow-up's offered times alike. A time less
    than 3 hours away, inside the absence, or after the due date (30 calendar days after the absence's last day, the term policy) is
    left out; nothing left → the next week, until the due date; then no card ("No time left": schedule it by hand; "Send a new card"
    moves the due date, A MISSED MAKEUP below). Campus wall-clock.
    MEETING HOURS (requirement, 2026-10-05, after setting this term's meeting hours to Tuesdays and Thursdays 1–5 pm: "makeups use it
    too (but remember that makeups can also be scheduled during regular office hours and that we want to initially offer monday and
    tuesday for makeups so the student doesn't try to prolong the period before the makeup)"): the fixed Tuesday 1:00–5:00 pm in
    BLOC 319 (until then a constant in `makeup_slots.py`, which would have carried into every term) became the term's own meeting
    hours, still only on the week's Monday and Tuesday beside their office hours; Thursday's meeting hours are never a makeup time.
    Offers already saved keep their times (the kind `afternoon` stands for a meeting-hours time).
    ROOMS, SHARING AND LENGTH (requirement, 2026-10-05: "BLOC 319 is my personal office and has limited room, the other rooms 306 and 309 are rather large ... makeups in Monday/Friday office hours are fine, but we shouldn't offer makeups for Tues/Thurs Office Hours ... 1-5PM on Tues/Thurs are available for makeups, and we can even overlap makeup sittings then, but we should not overlap makeup sittings with non-makeup related meetings ... quizzes generally are 20 minutes ... multiplied by their multiplier"; asked, they chose Monday and Tuesday first, then Thursday and
    Friday that week, the rooms ticked per term, and an exact start for a makeup scheduled by hand). This replaces the rules above
    where they differ:
    - OFFICE-HOURS ROOMS: an office-hours block is offered only when its room is one the term ticked as big enough to seat a makeup
      apart from office-hours visitors (TEACHING-WORKFLOWS MEETING HOURS `makeup_rooms`; this term BLOC 309 and BLOC 306, never
      BLOC 319, the instructor's small office). A term with none ticked offers no office hours.
    - DAYS: the week's Monday and Tuesday first; only when they offer nothing, its Wednesday to Friday (Thursday's meeting hours,
      Friday's office hours); then the next week, until the due date. Replaced by NEXT TWO DAYS, below.
    - SHARING: only another makeup (a makeup sitting, any event titled makeup) shares a meeting-hours start; a student's appointment,
      an office-hours event and anything else timed take it, but a DR exam (at the testing center) and a due date never do (the
      SCHEDULING engine's SHARING table decides all of this). Office hours offered as a block stay "come any time", whatever
      overlaps them (requirement, 2026-10-06: "normal office hours can have any kind of overlap, theyre essentially a free for all").
    - AWAY (requirement, 2026-11-08: "don't schedule any makeups for Friday office hours, I have a doctor's appointment at 3PM that
      day and won't return to school", kept by the Assistant as a memory; then "I feel like this is a one-off thing that is handled
      just by putting in a calendar entry"): the one exception to the free-for-all. A timed entry whose title says the instructor
      is not there (SCHEDULING "AWAY": "Away", "Unavailable", "Out of office", "Office hours cancelled") takes its time out of an
      office-hours block; what is left is offered, with its shortened times, when the student's whole quiz fits in it, and nothing
      when it does not (`makeup_slots._day`). It takes meeting-hours starts as any timed entry does. Anything else over office
      hours still leaves them whole, and a pick from a card already sent still seats the student (no recheck at the pick).
    - LENGTH (`absences/makeup_length.py`): a quiz makeup lasts 20 minutes times the student's extra time (the largest granted
      "Extra Time N.NNx" of their current accommodation record in that section, ACCOMMODATIONS), so 20, 30 or 35 minutes; a
      meeting-hours start is offered only when that whole length is clear and ends by the window's end. A makeup sitting lasts its
      longest student's length (20 with none). The calendar projection carries it (CALENDAR), and a makeup typed before Schedule
      makeup asked for an exact start has its start read from the text ("1PM", "around 2 PM").
    - EXACT START: Schedule makeup asks for a start time (the Name carries anything else, such as "after lecture").
    NEXT TWO DAYS (requirement, 2026-10-06: "The makeup cards should be changed to suggest available times in the next 2 days instead
    of just monday and tuesday"). Asked, they chose the next two days that have an open time, and never before the quiz. This
    replaces the week after the quiz in TIMES and DAYS; rooms, sharing, length, the 3-hour lead and the due date are unchanged.
    - The search starts at the latest of today, the day after the absence ends (or after the quiz was given, if later) and, for
      "Send a new card", the day after the last day the student's previous card offered. It walks day by day to the due date and
      offers the first two days that have at least one time. Today counts while a time is at least 3 hours away; a day with nothing
      (a weekend, a Wednesday) does not count.
    - The offer's `week` (the column and the API field) holds the first day offered; offers saved before keep their Monday.
    - A card's times, and so its expiry, now pass within about two days: an unanswered card shows "No answer" (its times passed)
      sooner, and "Send a new card" offers the next two days after it.
    - A follow-up's quiz makeup times (ASSISTANT MAKEUP TIMES, cards off) come from the same `MakeupBooking.times`, so a reply
      offers the same two days.
    FROM THEIR RETURN (requirement, 2026-10-30: "fix it so that the times offered correspond to their return date, not the date of
    the absence (of course, if the followup is after their return date it shouldn't backdate it either)"). This adds to NEXT TWO DAYS:
    - The search also never starts before the day the student can return: the "return(s) / back / cleared to return ... <Month D>"
      the absence's latest details name after its last day (`absences/service.py` `return_date`, the parser DEPARTMENT MAKEUP EXAMS
      › NOT BEFORE THEIR RETURN uses; since this change it allows up to 45 characters between the words and the date, so "return to
      school without restrictions November 2" is read). Its start is the latest of now (the 3-hour lead still applies), the day
      after the quiz, the day after the absence's last day, that return day and, for "Send a new card", the day after the previous
      card's last day: a card made after the return starts from today, never backdated. No return named: unchanged.
    - The due date's 30 days count from the return day when it is later than the absence's last day, so a late return never
      shortens the window; a later due date a card already moved it to (A MISSED MAKEUP) stands, and "Send a new card"'s 21-day
      search past the due date starts no earlier than the return.
    - Every path that offers times goes through `MakeupBooking.times`: the worker's first card, the dry run, "Send a new card" and a
      follow-up's makeup times (ASSISTANT MAKEUP TIMES). The Makeups view's due date follows it. Cards already sent keep their times.
  - OFFER AND CARD: each card's times are an offer first (`absence_makeup_offers`, owner: the student's section membership on the
    term's exam record, for one absence; `attempt` counts the cards for that absence; append-only), then a Response Card issue for
    that one student naming the offer (`makeup_offer_id`, RESPONSE-CARDS "issue choices"), the course template "Quiz makeup booking
    (Faculty)" (made once, an ordinary template the instructor may edit), policy `once`, expiring at the last time's start; then
    published and its invitation queued (card email may go out by itself, RESPONSE-CARDS). Request IDs make every step retry-safe;
    a failed step waits (60 s, 5 min, 30 min) and carries on where it stopped.
    GMAIL (requirement, 2026-10-06: "send booking cards through gmail", after learning they went through the relay from
    exam@example.com): a booking card's invitation is queued for the instructor's Gmail connected in Response Cards
    (`queue_invitations(..., mail_provider="gmail")`), never the relay or the workspace default; every other card keeps the
    default. While Gmail is not connected the card waits, retried, and its row reads "Card waits for Gmail".
    The Inbox worker runs these steps, so it loads the card service's settings too (`deploy/faculty-inbox.service.d/
    response-cards.conf`; until 2026-10-06 it did not, and the first live card stopped at publishing).
    WITHDRAWN BEFORE IT WENT OUT (same day: Piper Hollis's card, stuck there, was withdrawn on the instructor's word, "don't
    resend it then, ill just use the open followup item"): a withdrawn card, sent or not, reads Card withdrawn (flagged, with
    "Send a new card", whose times then start from today); a follow-up's facts list that quiz's open times for the reply to
    offer, as with the cards off (ASSISTANT MAKEUP TIMES).
  - PICK (EXAMS exception, below): the worker reads each submitted booking card once (`absence_makeup_picks`, keyed by submission).
    A time → rechecked still owed, then the makeup sitting of the exam record with that date, time and place (label "Makeup · Mon,
    Oct 5 · 3:00–5:00 pm"; made through `ExamService.save_sitting` with the student, or joined with `update_participant`; sitting
    only, expectation and grades untouched; everyone picking it shares it; it syncs to Google as an Exams event). "None of these
    work: I need another time" → their words kept and flagged, and brought to Follow-up (BACK TO FOLLOW-UP, below). Not owed any
    more → `stale`, flagged. Five failed tries → `failed`.
  - BACK TO FOLLOW-UP (requirement, 2026-11-12, of a student's "Needs another time" flagged only on the Makeups view: "when the
    students put "need another time" on the makeup cards, it should actually land back in followup for me to review, if not, it
    will just sit in the absence page unnoticed"). The worker's next step after the picks (`_followup_one`) opens one follow-up
    of the student's for each such answer still standing (owed, no newer card, no makeup sitting, absence not held), answers
    recorded before this rule included: "<Quiz> makeup: needs another time", Needs my reply, saved by Faculty
    (`FollowupSuggestions.faculty_request`; owner: the student's term enrollment, as every follow-up; no new table). Its details
    quote their words, the days the card offered and the due date; its next step reads "Seat them at a time that works, or send a
    new card". Once per answer: request ID `makeup-other-time:<hash of the submission ID>`, so one the instructor resolved is
    never opened again. The Makeups row keeps "Send a new card", and its flag until the instructor takes the follow-up up.
    - TAKEN UP IN FOLLOW-UP (requirement, 2026-11-12, of a row still flagged "Needs another time" two hours after they had
      approved the reply from its follow-up: "i thought we made the needs-another-time replies go to followups and I thought I
      handled this one in followup already"). This amends "keeps its flag": while that follow-up is open and saved as anything
      but Needs my reply (Waiting on student, To do, Follow up later), the row is not flagged and not counted on Home's makeups
      card; its chip reads "In Follow-up" and its line the status and the day it has stood so ("Waiting on student since Thu,
      Nov 12"; `_asked_taken`, `booking.followup`), above their words and the due date. Follow-up carries it: a Waiting one
      comes back to Needs me by itself (TEACHING-WORKFLOWS CHECK BACK). Still Needs my reply, or resolved while nothing
      answers the request (no sitting, no new card): flagged "Needs another time" as before. A read of the follow-up's saved
      revisions only: no table, route or write.
    - PREPARED LIKE A MESSAGE: it has no message, so it is Needs my reply (a To do is prepared only on request) and the
      Assistant prepares it by itself, as it would an email asking the same (ASSISTANT MAKEUP TIMES › ANOTHER TIME). Every step
      is a card: the makeup sitting and the reply wait for Approve. Nothing is sent or seated by this rule itself.
    - ANSWERED OUTSIDE IT: once the student is on a makeup sitting, a new card went out, or the makeup is no longer owed, Faculty
      resolves the follow-up with a line saying which (`resolve_faculty_followup`), unless the instructor saved it or a step of
      its preparation was approved or done (`FollowupSuggestions.untouched`): then it is theirs to settle, and no step still
      waiting (the reply after the sitting they approved) is withdrawn. A hold does not resolve it.
    - The Follow-up page's makeup line for that quiz (ON THE FOLLOW-UP) adds their words.
  - NO ANSWER, THEN A 0 (requirement, 2026-11-12: "makeup cards that go to "no answer" status should wait one more day after that
    status for the student to reach out, if the student doesn't reach out then the assignment gets recorded as a 0 and then makeup
    card revoked and cleared from the makeup owed page"). This amends MISSED ("the instructor's own") and "no answer ... flagged
    to them": the worker's step after BACK TO FOLLOW-UP (`_zero_one`) records an owed quiz makeup missed by itself, exactly as
    "Missed makeup, record 0" does (`AbsenceService.makeup_missed`, actor `automatic`, request ID `makeup-no-answer:<offer>`; no
    new table), when ALL of these hold (`_zero`):
    - its current card reached the student (published, invited, delivery not failed or unconfirmed), has no answer, is not
      withdrawn, and every time it offered has passed. A card whose times passed before it went out, a card not delivered, and a
      card flagged after 3 business days while a time is still to come are never a 0 by this rule;
    - ONE MORE DAY: the whole next business day after the card's last day has ended (`ZERO_WAIT`, `business_days_after`: a card
      whose last time was Tuesday is a 0 from Thursday; Friday's from Tuesday). Chosen here, not asked: a business day, as the
      3-business-day flag counts;
    - THEY DID NOT WRITE: the communications record holds no message the student sent (Gmail or Canvas, any subject; a regrade
      request is not one) since the card's offer was made. One who wrote is never a 0 by itself, before or after the day: the row
      stays flagged for the instructor, and the message is in Follow-up as any message is;
    - MAIL READ: Gmail and Canvas each have a successful capture round since that day ended (`comm_capture_state`), so a message
      sent in time is in the record; until then it waits.
    The row's label says where it stands ("a 0 is recorded after Wed, Nov 11 unless they write to you", "they wrote to you since
    the card, so no 0 is recorded by itself", "a 0 is recorded once Gmail and Canvas have been read"). What follows is MISSED's:
    the row leaves the Makeups view and Home's count, the card is withdrawn (WITHDRAWN), the gradebook counts the empty cell as 0
    (ZERO ONCE DUE, so Sync with Canvas sends it), and the certification reads changed until that 0 is certified: by Faculty
    itself since 2026-11-14 (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES"; never over a score above 0, A SCORE STANDS below, nor
    while a paper of the assignment's scans has no student yet), else by the instructor's next Certify. The absence window lists it "Missed makeup: 0 · no answer to the booking
    card" with Undo. ONCE PER CARD: after the instructor's Undo the makeup is owed again, its card reads Card withdrawn, and
    Faculty never records that card's 0 a second time; a new card starts the rule again. A held makeup, a student on a makeup
    sitting, "Needs another time" and "Paper not in" are not No answer and are untouched. Nothing is emailed to the student by it.
    - A SCORE STANDS (requirement, 2026-11-14: "we need to make sure that 0 doesn't overwrite a positive score for that assignment.
      for example, a student says they will be absent so a makeup card is sent, but they actually show up and take the assignment
      as normal, the card shouldn't overwrite that score"). A score is a cell's latest revision scored above 0 in a gradebook
      column of the quiz's assignment that is not archived, in any section of the student's (`_score`, `totals._answered`).
      BEFORE: while the student holds one, this rule records no 0 and the row says so ("they have 8 for it in the gradebook, so
      no 0 is recorded"); it stays owed and flagged, since only a received paper ends a makeup (MAKEUPS). AFTER: one that turns
      up once Faculty's 0 stands (a late paper's certified grade, a value typed or imported) takes that 0 back, before any new 0
      is looked at (`_unzero_one`: `missed` false, actor `automatic`, request `makeup-no-answer-scored:<offer>`, current terms),
      so the next Certify leaves the student out for the makeup instead of writing "Missed the makeup" over the score (a pull
      replaces an imported value). With no paper of theirs in, the row returns as "Card withdrawn · its 0 was taken back: they
      have 6 for it in the gradebook"; ONCE PER CARD holds, so that card is never a 0 again. The instructor's own "Missed makeup,
      record 0" is never taken back. A paper received before the 0 ends the makeup (no 0); one received after is scored at
      Certify, a certified 0 included (GRADING LATE PAPERS CERTIFY THEMSELVES). Nothing is written to the gradebook.
  - WITHDRAWN: an unanswered published card is revoked (`ResponseCardService.control`, request `makeup-revoke-<card>`) once its
    makeup is not owed (paper received, decision changed, dropped), the student is on a makeup sitting set by hand, or a newer card
    replaced it. A student who already booked stays seated.
  - VERSION: a quiz owing an excused makeup, with no makeup version and no makeup draft of the instructor's (open or approved), gets
    one draft (`create_makeup_draft`, request ID `auto-makeup:<plan>`, based on the first version with rubrics): once, so a scrapped
    one is never redrafted by itself. Drafting runs whether or not the term's cards are on. A draft that cannot start (no model, one
    version, no rubrics) is shown on the quiz's group and tried again later. Approval and rubric finalization stay the instructor's,
    and so does certification except the makeup paper itself once graded and cleared and a makeup's own ending or beginning (GRADING "LATE PAPERS CERTIFY THEMSELVES"
    and "MAKEUP CHANGES CERTIFY THEMSELVES";
    the settled version is added on Gradescope by itself, GRADESCOPE "A VERSION THE SET LACKS") (GRADING "MAKEUP VERSIONS"; the rubric review is requested for them after approval, or, when another review
    was running then, by the worker once none runs: `makeup-review:<plan>:<input>`, once per rubric set, never over a review of the
    same rubrics that ended, and never while a version's rubrics are not confirmed for review).
  - COPIES (requirement, 2026-10-12: "When a student books a makeup, faculty should automatically send a print job for the current
    makeup version to print. If a makeup version hasn't been made yet, it should wait until one is and then send the print jobs
    once it is ready"; until then the job was made from 6 pm the evening before the sitting): AS SOON AS a student is seated by
    their pick, the worker's next turn makes one print job of the newest approved makeup version, one copy for each student
    of that sitting who has no paper and no copy yet (`create_batch`, term-owned, request `makeup-copies:<sitting>:u<students>`,
    actor `automatic`), attached to the sitting. UNNAMED (requirement, 2026-10-16: "Can we just have it not tied to a particular
    student? The normal quizzes aren't that way, so I don't see why we are doing it here", after asking whether a copy's Data
    Matrix refers only to that student, "because I frequently print out multiples at once and might not remember whose is
    whose"): each copy is printed for no student, as a quiz's are, and is named from the Name and UIN written on it once scanned
    (DOCUMENTS, `copy_names`); whom a job was made for is kept in `absence_makeup_copies` (migration `absences/006_makeup_copies`;
    owner: the student's enrollment; append-only), so none is printed twice. NAMED AGAIN, NAME FILLED IN (requirement, 2026-10-18: "I want to change
    makeups back to being student specific, but now in addition to the data matrices, it should fill out that student's name
    in the name spot on the document"; replaces UNNAMED for jobs made from then on): each copy is printed for its student
    (`create_batch` `students`, request `makeup-copies:<sitting>:n<students>`) with their roster name printed on the paper's
    "Name" line (DOCUMENTS "NAME FILLED IN", `fill_names`); `absence_makeup_copies` still records whom a job was made for.
    Unnamed jobs made between 2026-10-16 and then stay unnamed. Jobs made NAMED before then keep their names; a student who books the same sitting later
    gets a job of their own. No approved version → the copies WAIT (the group's version line says what the version waits on, the
    booked row "Their copy is made once the makeup version is approved") and are made, one job per sitting, as soon as one is
    approved; the approval window says so. The booked row links to the job (`#documents?batch=`) and says where it stands (being
    made, ready to print, could not be made, superseded). PRINT PDF (instructor, same day: "have a button appear next to the line
    item here that brings up pdf to print it straight from this menu"): once the copy is ready, the row's Print PDF (among the row's
    actions; framed while the copy still waits to be printed, ONE ROW, ONE STEP) opens the
    job's own PDF (`/api/documents/batches/<id>/download`) in the app's file viewer, the browser's own PDF viewer (VIEWER
    `documents.download`), as the print job's window does; a link only, nothing is issued or recorded. A job with no file to
    open (not ready yet, failed, or superseded by the instructor: the copy's `status` then reads `superseded`) offers none.
    PRINTED AT HOME (requirement, 2026-10-18: "I want the app to automatically print booked makeups. Of course, we need handling
    for when a makeup is booked and the makeup hasn't been generated/exists yet"; amends "Printing stays the instructor's"):
    once the job is ready, the worker's next turn sends it whole to the home printer (DOCUMENTS "SEND TO PRINTER" › AUTOMATIC
    MAKEUP PRINTS), so a copy waits, in order, for an approved makeup version (above), its job's build, and a printer set up.
    The row then says how it went (printed at home, going to the printer, may have printed: check the printer, did not print
    yet), Print PDF stays, and Home's count of copies to print leaves out one printed or on its way. A print email stays an
    explicit Send. Home's count otherwise is unchanged (a sitting today or tomorrow).
  - STATES (each owed quiz row's additive `booking`; flagged ones warn): Card goes out shortly, Card going out, Card sent, No answer
    (3 business days after the card, or its times passed; its label says when it becomes a 0, NO ANSWER, THEN A 0), Needs another time (their words; once you seat them on a makeup sitting by
    hand it reads as any sitting set by hand, unflagged: requirement, 2026-10-16), Could not book (likewise), Booked (time, place,
    their copy; BOOKED BY HAND, requirement, 2026-10-16: "i manually booked her makeup sitting for her, can we make it show booked
    just like the others": a quiz makeup you seat by hand on a dated makeup sitting reads Booked too, and in a term whose cards are on
    gets its copy as a pick does, unless that sitting already has a print job you made; an undated one reads as before), Paper not in (the sitting's day passed and no paper: missed, or not scanned yet), Not on a sitting (picked, then
    moved off by hand), Card not delivered, Card withdrawn, Held and Check back (HOLDS, below). "Send a new card" (POST `/api/absences/makeups/cards`, explicit; the next
    offer from the day after the last card's last day, NEXT TWO DAYS; refused while Google cannot be checked, SCHEDULING "GOOGLE
    CHECKED") on a flagged row; "Send booking card" on an owed quiz row while the term's cards are
    off. The group head names the makeup version's state (drafting, ready to approve, approving, rubrics to finalize, could not draft).
    A MISSED MAKEUP (requirement, 2026-10-18, of a student who missed her booked sitting after which the due date passed: "I need to
    send her a new booking, but it won't let me"): when no time is left before the due date, "Send a new card" still offers the
    next two days with times (looking up to `EXTEND`, 21 days, ahead) and the due date moves to the card's last day: the offer's
    `due_date` records it, and the makeup's due date is from then on the latest of the policy's and its offers' (`_due`, used by
    the row's "Due by", later cards and a follow-up's times). Refused only when nothing is open in those days.
    ALWAYS TWO DAYS (requirement, 2026-11-12, of a student's fifth card, made at 11:31 am three days before her due date, that
    offered only that afternoon's three times and went unanswered: "why did it only send her same day times?"; asked whether a
    card they send should always offer two days, going past the due date when it must: "Yes, build that"). The same search past
    the due date also runs when only ONE day with a time is left before it: the card you send then offers that day and the next
    day with a time after the due date, and the due date moves to the card's last day as above. When those `EXTEND` days hold no
    second day the one day is still offered and the due date stands. Only "Send a new card" and "Send booking card" (`new_card`,
    a follow-up's card included: it is the same route); the worker's first card, the dry run and a follow-up's offered times keep
    to the due date. The 30 days themselves are the term policy's (Rule 7.4.1, "within 30 calendar days after the initial
    absence ends"), `makeup_slots.WINDOW`. A student on a makeup
    sitting whose day has passed (Paper not in) is taken off it (`update_participant`, `sitting_id` none), so the new card goes
    out and their pick seats them again; their new sitting gets its own copy. The automatic first card never moves a due date.
  - HOME: with no current term's cards on, the "Makeups owed" card is unchanged. Otherwise one card "Makeups that need you", counts
    only (`parts`: bookings to look at, rubrics to finalize, named copies to print for today or tomorrow, and
    every makeup owed in a term whose cards are off; a version to approve has its own card since 2026-11-05, ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED"); no names. The approve dialog, when the term's cards are on, starts at no copies,
    no sitting and no students.
  - FOLLOW-UPS (2026-10-04, ASSISTANT "MAKEUP TIMES"): a follow-up's drafted reply offers a quiz makeup the way the card would.
    While the term's cards are off, the preparation is given every time `times` would offer for that quiz and the reply lists
    them all for the student to pick; while they are on, the reply proposes no time and says the card is coming (or points to the
    card sent, or the sitting booked). Reading those times writes nothing: no offer, card or sitting.
  - HOLDS (requirement, 2026-10-06, before turning the cards on, of a student they had told to wait until her concussion is
    reassessed: "I don't [want] faculty to automatically send her a response card to pick a time when she can't do that yet").
    Asked, they chose: a hold lasts until THEY release it (a check-back day only flags it, never sends anything), one hold covers
    every quiz makeup the ABSENCE owes, and the follow-up assistant may place one by itself, shown with Undo (ASSISTANT DONE FOR
    YOU "MAKEUP HOLD"). `absence_makeup_holds` (migration `absences/005_makeup_holds`; owner: the absence, so its section
    membership; a revision per change, append-only; no row is not held). POST `/api/absences/<absence>/makeup-hold`
    `{request_id, held, reason (≤300, what it waits on), check_back (YYYY-MM-DD), revision}`: a release must name the revision it
    read (409 when stale, as a hold that names one); holding what is held the same way, or releasing what is not held, records
    nothing; a withdrawn absence is never held. An undecided absence may be held before it is excused.
    - WHILE HELD: no first card goes out, an offer not yet delivered waits, an unanswered published card is withdrawn
      (`makeup-revoke-<card>`), the turn-on dry run lists the student under "Held" instead of a card (GET `…/makeups/plan` adds
      `held`), and a follow-up offers no time and mentions no card (ASSISTANT MAKEUP TIMES). A student already booked by their
      pick, or on a makeup sitting set by hand, stays seated: the hold changes nothing there.
    - SHOWN: Held (what it waits on, the check-back day), and from the check-back day Check back, flagged and counted on Home's
      "Makeups that need you" like every flag. Hold… on each quiz row not yet on a sitting; a held row has Release ("Release and
      send card" while the term's cards are on) and Change….
    - RELEASE: with the term's cards on, the card goes out by itself as a first card does; a card the hold withdrew, or one that
      never went out before its times passed, is followed by a new card (the next attempt, its request named by the release, so
      once per release) whose times start from today (NEXT TWO DAYS), not after the withdrawn card's days. "Send booking card" or
      "Send a new card" on a held absence releases the hold first (request `new-card-release:<request>`): it is the instructor's
      own explicit action.
    - A KNOWN RETURN (requirement, 2026-11-09: asked about Jason Clark's Quiz 7 card, held by the follow-up with "Medical note
      permits return November 9" and that check-back day and so never sent, they chose "Don't hold; send card at excusal" over a
      hold that releases itself on the return day). FROM THEIR RETURN already keeps the card's times from the return, so the
      assistant never holds an absence whose return day is known: the one its latest details name (`return_date`) or, for a hold
      being placed, the one the hold's reason names (`makeup_booking.known_return`). The follow-up writes the return in the
      absence's details instead, and the card goes out once it is excused. Enforced twice: a follow-up's proposed hold is refused
      while the model can still drop it (assistant/followups.py `validate_write`), and POST `…/makeup-hold` refuses `held: true`
      from the assistant (actor `assistant`, the chat's Approve cards included) with 409. A hold for an open-ended wait (a
      reassessment, an uncertain return) is placed as before, and the instructor may hold any absence. The two holds standing
      for a known return that day (sections 515 and 514, Quiz 7) were released through Release on the instructor's word.
  - ON THE FOLLOW-UP (requirement, 2026-11-03: "in a followup for an excused absence that has a quiz to makeup, it frequently says it
    will send an email for them to pick a date, but I don't see anything on my end that shows that will happen. Does it just
    automatically happen?"). It does, as OFFER AND CARD says; the follow-up did not show it, and the Excuse card's fold said "The student is
    not told". An Excuse card (a follow-up's or a chat's) now says on its face, as the line Then, that Faculty emails them a booking card
    for the quizzes named by itself from the instructor's Gmail (`MakeupBooking.excuse_cards`, `_offer_one`'s own test read before the
    decision: cards on, not held, quiz given in the dates, no paper, no makeup sitting, no card or excused absence already over it), and
    its audience says that card is all the step sends. It follows the decision chosen on the card. A quiz with no exam record yet names no
    card: a card waits for that record, as Quiz 6's did until 2026-10-30. The Follow-up page's own read adds `makeup_cards` to a student's
    follow-ups when there is something to say (`MakeupBooking.followup_cards`, the Makeups view's states): once it is excused it goes
    out, when it was emailed and from which address, whether it was opened, booked, or flagged, with Open makeups. Read only; the card's
    rules do not change. The field is left out otherwise, so other reads stay as they were.
  - Nothing here grades, excuses, certifies or writes the gradebook (NO-GRADEBOOK stands). For the assistant the new routes are
    unnamed (Approve cards), except a follow-up's own hold (above). Tests: `tests/test_makeup_slots.py`, `tests/test_makeup_booking.py`
    (`HoldTests`; BACK TO FOLLOW-UP and TAKEN UP IN FOLLOW-UP: `test_another_time_*`; NO ANSWER, THEN A 0: `test_an_unanswered_card_*`,
    `test_a_card_that_can_still_be_answered_*`; A SCORE STANDS: `test_a_0_is_never_recorded_over_a_score_*`), `tests/test_absence_makeups.mjs`.
- **TOTAL ABSENCES** (requirement, 2026-10-31: "we should keep a total number of absences on the students profile card. This should
  be calculated from actual absences handled with the absence functionality, but also a 0 on an iClicker should be considered an
  absence as well as a 0 on a quiz. We must make sure to not double count (ie unexcused absence on the same day as a clicker or quiz
  and other ways to double count)"). `absences/totals.py` `total`, a READ-ONLY projection of one SECTION MEMBERSHIP, kept nowhere:
  GET `/api/absences/students/<section>/<enrollment>` carries it as `total` and the Absences part of the student's card (the
  roster's window and the gradebook's record, `static/absences.js`) leads with it ("5 absences", then how many are excused, not
  excused and waiting for your decision) and folds the days below ("Days missed": each day, its decision and what shows it).
  - A DAY IS THE UNIT: each campus date counts once, however many of these name it. RECORDED: an absence that is not withdrawn
    covers its class days (SCHEDULING: the Term schedule's meetings while the term uses it, `scheduling.class_schedule.section_meetings`,
    days off left out); every Monday to Friday while the section has no class days on record. CLASS DAYS ONLY (instructor,
    2026-10-31, asked whether an absence on an evening exam's day, Exam 1 on a Thursday, counts: "no, only class dates"): once the
    section has class days on record, a day that is not one of them is never counted, whatever names it.
    ICLICKER: a missing or scored-0 cell of a poll of the section's linked iClicker course, on the date its import recorded
    (ICLICKER-CREDIT's sessions and columns; archived columns, columns of other sections and 0-point columns left out); several polls
    of one day are one day, and an answered poll that day (a score above 0) means the student was there, so that day's iClicker 0s
    are dropped. QUIZ: a missing or scored-0 cell of the column of an exam record that is a quiz (no seating chart for its
    assignment, as MAKEUP BOOKING reads quizzes), on the day it was given to the student's SECTION (`given_dates`,
    `own_makeup=False`: a makeup sitting on another day never moves it); a 0 on a paper that came back (the Exams page's
    collection) is a score, not an absence. TAKE-HOME (requirement, 2026-10-31: "math experiments doesn't count nor does quiz 1
    because those were take homes"): work whose exam record has no day given is never counted or named. Quiz 3 had no day on
    record; the instructor's Gmail showed Fri, Sep 25 and, at their "yes", its main sitting was added (after a makeup hold on the one
    excused absence it newly covered, so no month-late booking card went out).
  - NOT ABSENCES: an empty cell (not imported yet, or a student added after the session: the roster keeps no start date, and the
    after-class import records missing only for that day's roster, iclicker.md AFTER CLASS), full credit replacing a 0 (an excused
    absence's or an iClicker credit), a makeup taken and scored, an exam's 0 (exams are not a source), and days after the campus
    day (listed as ahead, not counted). A dropped quiz score is still the 0 in its cell and counts.
  - STATE of a day: the best decision of the absences covering it (excused, then waiting for your decision, then not excused); a
    day only a 0 shows is not excused, "No absence recorded". Rehearsed on a snapshot of the live catalog (2026-10-31, MATH 140
    Fall 2026, 340 memberships): 502 days, 11 of them named by two sources and counted once; one Quiz 4 0 on a returned paper left
    out; Quiz 3 (27 zeros, then dated: 524 days) and the Mathematical Experiments Assignment (18, take-home) had no day on record;
    at most 0.14 s a student, 4 ms on average. Tests: `test_absence_totals.py` (fake iClicker), `browser_absences.py`.
  - ON THE ABSENCES PAGE (requirement, 2026-10-31: "we should add a tab in the absences page to see the running totals of each
    student, the number of absences can be a collapsed menu that would expand upon clicking to show the details of the absences.
    Perhaps we should stratify by section so it isn't one giant list as well"). GET `/api/absences/totals?scope=current|all`
    (`AbsenceService.totals`): every student of the terms, dropped ones marked, grouped by SECTION, most days first then by name,
    each with the card's own `total` (one read shares what a term's or section's students have in common, `totals.total`'s
    `memo`: 0.43 s for 340 students on the 2026-10-31 snapshot, the same 514 days), names from the verified roster payloads, no UIN.
    The page's third view, "Totals by student" (`#absences?view=totals`), read only while it is shown: one group a section (its
    students who missed a class and its days missed), a line a student whose count opens to the days missed as the card lists them;
    a student with none reads "No absences". The course filter, Terms and Find a student narrow it. Nothing is written.
    A TABLE, NOT SENTENCES (requirement, 2026-11-12, of 340 lines each reading "12 absences" and "12 not excused": "this page is
    also unruly and not user friendly, it's just a wall of text"; DESIGN-LANGUAGE §1.6, §1.7, §1.13). Each section is a table
    whose words are said once, in its column headings (Student, Absences, Not excused, Excused, and Waiting only while a shown
    student has a day waiting for a decision); a row is the name, the count, a bar and the figures, read down their columns. A DAY
    IS A BLOCK: the bar draws one block a day, not excused in ink, waiting in the needs-you orange, excused pale, to one scale for
    the whole page (the most days any shown student missed, never under 10), so rows compare at a glance; the headings' blocks are
    its key, and it is hidden from a screen reader, which hears each figure with its words. The row still opens to the days
    missed (the framed +/− at its end). The students with no absence fold under their section ("No absences", "27 students",
    their names in columns with any days recorded ahead), closed, and open while Find a student narrows the list so a student
    searched for is never hidden. THE TOP FIVE (instructor, the same day: "we should have them partially collapsed so that every
    single student isn't shown at once, only the top 5 for each section"): a section shows its five students with the most days
    missed; the others who missed a class fold under them, closed ("53 more students", "8 absences or fewer"), above the fold of
    those with none; a section of five or fewer has no such fold, and while Find a student narrows the list every match is a row.
    The section's line still counts everyone. Same read, order, counts and words in the days; nothing is written
    (`static/absence-totals.js`, `tests/test_absence_totals.mjs`).

- **DEPARTMENT MAKEUP EXAMS** (requirement, 2026-10-22: "Would it be possible to automate this google form. It is required to fill
  out for students that have excused absences for exams"; offered a Follow-up card, automatic sending or a button, they chose "card,
  make sure it records my tamu email on the form"). Faculty fills out the Math department's makeup exam request form (a public Google
  Form; `absences/makeup_form.py`) for a student whose excused absence covers an exam, and sends it only on the instructor's Approve.
  Migrations `absences/007_department_makeups` (four append-only tables) and `008_department_notices`. `absences/department_makeups.py`.
  - WHO OWES ONE: an EXAM is a term assignment a seating chart names (not a DR exam's chart; exams are seated, as MAKEUP BOOKING
    tells quizzes from exams), so an exam needs no exam record yet. A current (not dropped) student owes a request when an EXCUSED
    absence covers the day the exam was given to their section (`term_given`, EXAMS "DATE GIVEN") and their paper has not come back
    (when the exam has a record). An undecided absence owes nothing yet: its card may be shown (a card in the same round may excuse
    it) and is sent only once it is excused. A read, recomputed each time; `GET /api/absences/department-makeups` lists it with each
    current term's form link, names from the verified roster, never the UIN.
  - THE FORM (signed in: GOOGLE SIGN-IN): one link per course term (`PUT /api/absences/department-makeups/terms/<term_id>/form` `{request_id, form_url,
    revision}`; a new term's form is a new link). Read by its questions' TITLES each time it is previewed or sent, never cached: a
    required question Faculty does not know, a question it answers that is gone, a choice that is no longer a choice, a sign-in or
    a closed form stops it with the reason; nothing is guessed. Only docs.google.com, no redirect.
  - THE RESPONSE: Yes to "Are you the instructor"; Sinkule, Christopher; the course number (from the term's code); the student's
    section number; their last and first name as the verified roster has them ("Last, First"); their UIN; the email the form
    records, `FORM_EMAIL` = instructor@tamu.edu, typed into the form's Email field (the instructor's words above); Attach
    documentation Yes when the absence has a document on file, else No. The SESSION is the first of the form's sessions after both
    the absence's last day and the exam's day and after today, and never before the day the student can return (NOT BEFORE THEIR
    RETURN, 2026-10-22, Lucia Rosales Lozano: her note cleared her for Oct 24 but her absence was recorded for Oct 22 only and the
    card picked Fri Oct 23): the card's "not_before" (YYYY-MM-DD, which the preparation adds from the student's documentation), else
    a "return(s) <Month D>" the absence's details name after its last day. The card shows it as "Not before" and warns when the
    chosen session is earlier. The exam's RULES (time allotted, Scantron, calculators, memory reset,
    special instructions) are the last ones Faculty sent for the same exam, else those of the instructor's own latest receipt
    received no earlier than three weeks before the exam (its time divided by that student's extra time), else the term's (THE
    TERM'S RULES), else none: the card asks.
    TIME ALLOTTED is the exam's own time times the student's extra time (ACCOMMODATIONS "Extra Time N.NNx", `makeup_length.multiplier`),
    rounded up: one of the form's times when it is one, else its Other box ("113 min").
    THE TERM'S RULES (requirement, 2026-11-12, with five request cards waiting once Exam 3 was seated, each asking for every rule
    again though Exam 2's requests had all given the same ones, and four still saying "Choose…" after the first was filled out and
    sent: "can we make these default to the usual settings, for a given course/term the first one I fill out should be the default,
    the only thing that should change is the student info, exam number, and date throughout the semester"):
    - An exam with no request or receipt of its own starts from the rules last given in its COURSE TERM for any exam: Faculty's
      latest accepted request, else the instructor's own latest receipt for a student of the term (`_rules`, `_receipt_rules`). So
      the first request of a term sets the rules of every later card, a rule changed on a card and sent is the default from
      then on, and an exam's own last request still comes first. The time carried is the exam's own (before extra time).
    - DOCUMENTATION is never carried from another student's request: it is Yes when this absence has a document on file, else No,
      unless chosen on the card (until then a request's answer was handed on with the rules).
    - CARDS STILL WAITING: once Google accepts a request, every request card still waiting, in any follow-up, is previewed again
      (`FollowupSuggestions._department_refresh`, `AssistantService.preview_again`: the card's own preview, recorded as its Approve
      records a changed one; nothing is sent), so it shows the rules just sent and its Send request sends them in one press. A
      card whose preview did not change, or cannot be read just now, is left as it is.
  - THE CARD (ASSISTANT; Approve card, confirmation "submit", preview `absences.department_makeup_preview`, a READ route; request
    IDs supplied by Faculty): the follow-up preparation's facts name each exam an absence of the student's covers and whether its
    request is owed, sent, refused or made by the instructor on the form, and its prompt proposes `POST
    /api/absences/<absence_id>/department-makeup` `{"assignment_id"}` once per owed exam. The card reads "Request a department
    makeup of <exam>", button Send request, with every answer the form will get; the session and each rule are dropdowns
    (`choice.field`): a choice is saved as a new waiting card in the old one's place (TEACHING-WORKFLOWS EDIT IN PLACE, `edit` with
    `{choice: {field, value}}`), and nothing is sent until Approve. Its audience line says the student is then emailed (STUDENT NOTICE).
    AFTER THE EXCUSE (requirement, 2026-10-22, of Walter Parr's follow-up, whose round recorded and excused his absence and tracked
    the department request as a To do: "still marking it as a todo instead of suggesting the google form"): a round that records
    the absence has no absence ID for the request card, so the prompt proposes neither the card nor a To do for it, and once an
    approved absence card of a run leaves an excused absence over an exam owing a request (or whose last request failed), Faculty
    adds the request card to that run itself (`FollowupSuggestions._department_after`), one per exam, waiting for Send request.
    ONCE THE EXAM IS SEATED (requirement, 2026-11-03, of Jon Bellamy's Nov 19-20 absence over an Exam 3 with no seating chart yet;
    told that no card would ever come, since an exam only counts once seated and AFTER THE EXCUSE looks once, at the approval, they said
    yes to Faculty adding it by itself): the Inbox worker looks each minute for a request OWED with no card standing, and for it opens
    one To do follow-up of the student's ("Request the department makeup of <exam>", `FollowupSuggestions.faculty_followup`, request
    `department-makeup:<enrollment>:<assignment>`, no model) holding the same card, waiting for Send request. This also covers an
    absence excused on the Absences page, the other way to a request owed with no preparation to propose its card. A card that waits,
    was sent or that the instructor declined stands, in any follow-up, so a declined request is not offered again and a preparation
    is told when one waits; an absence decided in the last 5 minutes is left to AFTER THE EXCUSE. Cards expire after a day
    (ASSISTANT), so while the follow-up is open and the request owed, an expired card is replaced by a fresh one. The follow-up is
    resolved by Faculty once Google accepted the request, the instructor requested it on the form or nothing is owed any more, unless
    they saved it since. A preview the form refuses opens nothing and is tried again 15 minutes later.
    TRY AGAIN (2026-10-22): a request card that failed (Google refused it, or a choice was missing; nothing was recorded) is
    followed at once by a fresh card in the same run (its body names `attempt`, which the route ignores, so it is a new proposal),
    waiting for Send request; an uncertain one never is.
  - PAGE BY PAGE (2026-10-22: signed in, the whole response posted at once was refused, 400 naming no question): the response
    goes as the form's own Next and Submit send it, each page's answers with the page's hidden fields (its `token` and the answers
    so far, `partialResponse`) as the previous step returned them, the Email on the first page; only the last step can record.
    A page Google does not accept names its questions (and the Email box) from the page it shows again.
  - SENT ONCE: the request row is committed before the form is posted (answers kept without the student's name), then Google's
    answer is recorded once: accepted (200), refused (failed, 502: nothing was recorded there; a new card may try again) or not
    known (uncertain, 504: a timeout or a server error after the request left; never sent again by Faculty, check the receipt in
    Gmail). A second request for the same student and exam is refused while one is accepted or uncertain; a request ID replays its
    first answer; the fingerprint is the preview's (form, answers, absence, exam).
  - NO SITTING (requirement, 2026-10-22: "makeup exams this semester are done by the department, so it is not something I need on my
    calendar unless I am scheduled to proctor the makeup (which I have already added)"): a request makes no makeup sitting on Exams
    and nothing on the calendar. (Until then an accepted request placed the student on the exam record's makeup sitting; none was
    ever made on live.)
  - RECEIPTS: the Inbox worker reads Google's receipt emails ("Thanks for filling out this form", from
    forms-receipts-noreply@google.com) from the instructor's Gmail, read only, every 15 minutes while a request of the last week
    has none and hourly otherwise, at most ten a turn, and keeps each once (UIN, section, session, rules, recorded email; never a
    name). One matching a request of Faculty's (UIN, section, session; never one Google refused) confirms it; one Faculty did not send is the instructor's own
    request on the form, so that student and exam owe no card.
  - STUDENT NOTICE (requirement, 2026-10-22: "It should also send a notice to the student through my TAMU email to let them know
    they are scheduled for a makeup on that day/time/location and that they must take it or they will receive a 0"). Once Google
    accepts a request of Faculty's, the student gets ONE email from the Mail page's sender (the connected instructor@tamu.edu
    Gmail, so it is on Mail › Sent), to their roster address only, no Cc or Bcc: subject "<code> <exam> makeup: <Mon, Oct 26>,
    <7:30 to 9:30 PM>, <room>"; "Hello,", "You are scheduled to take your <code> <exam> makeup on <Monday, October 26>, from
    <7:30 to 9:30 PM> in <room>.", "You must take the makeup at this time. If you do not, you will receive a 0 on <exam>.", "Bring
    your student ID: the makeup testing room admits no one without it." (the department's own rule), signed "Professor Sinkule".
    `gmail_mail.preview` first (nothing recorded if it fails), then the notice row (`absence_department_notices`, migration
    `absences/008_department_notices`, gmail request `department-makeup-notice:<hex>`) committed, then `gmail_mail.send` (actor
    `department-makeup-notice`). Sent right after the accepted request; the Inbox worker sends one that could not go then (Gmail
    away), for a request of the last two weeks whose session has not begun. A request with a notice row is never notified again by
    itself; its status is the send's own receipt (accepted, failed, uncertain), shown with the request. A student dropped or
    without a roster address is not emailed. A request the instructor made on the form itself (a receipt only) notifies no one.
  - GOOGLE SIGN-IN (2026-10-22: Faculty's first response, Lucia Rosales Lozano's, was refused with 401: the form opens to anyone
    but takes a response only from a signed-in TAMU Google account, and Faculty's Google API connection does not count; offered a
    pre-filled link they submit or a sign-in kept in Faculty, the instructor chose "keep a google sign in in faculty").
    `absences/google_signin.py`: the AIM sign-in window (as Canvas's, CANVAS "SIGN-IN AND THE KEPT SESSION") starts at Google's
    sign-in for instructor@tamu.edu continuing to the term's form; Faculty types that address, TAMU's NetID, password and Duo steps
    run as for AIM (Duo relayed), a Google page it does not know is shown to the instructor, and once the form is served to
    instructor@tamu.edu (its `data-user-email-address`) the browser's cookies for https://docs.google.com/forms/ are KEPT in one
    0600 file (FACULTY_GOOGLE_FORMS_SESSION_FILE, else `google-forms-session.json` beside Canvas's kept session) and the browser is
    discarded. Every read and send of the form carries them, to docs.google.com only; cookies Google renews are kept while the file
    holds the same sign-in. A preview served to no one (or another account) adds "Faculty's Google sign-in" to what the card
    misses, so nothing is recorded or sent; Google refusing it (401/403) is a failed request saying to sign in again. Absences ›
    Makeups owed says whether Faculty is signed in, with Sign in to Google… (GET/POST `/api/absences/department-makeups/google`,
    `…/google/signin…`, `…/google/forget`); the Assistant never drives the window or Forget (NEVER, as Canvas's).
    SIGN-IN ENDED (2026-10-23: the sign-in kept 2026-10-22 16:49Z no longer opened the form signed in a day later, and Sandra
    Vance's card failed "Choose Faculty's Google sign-in" while the page said signed in): every read of the form records whom Google
    served it to (process memory, one entry); the status reads `ended` (not signed in) when that was not instructor@tamu.edu for the
    kept sign-in, and Absences checks once when it opens (`?check=1`). The card then says the sign-in ended and to Sign in again.
    SIGN IN WHEN NEEDED (requirement, 2026-10-23: "if it is signed out, it will automatically start the signin process and wait for
    the successful duo push completion (if necessary, otherwise proceed) then it proceeds as usual"): Send request on a request card,
    and Approve all on a follow-up holding one, first ask `…/google?check=1` (`ensureGoogleSignIn`, static/aim-signin.js); when the
    kept sign-in ended, the Google sign-in window opens by itself with the saved NetID sign-in and Duo, and the send goes on once
    Faculty keeps the new session (a sign-in Google lets through without Duo just closes the window). Closing the window sends
    nothing ("Not signed in to Google, so nothing was sent."). A check that fails lets the send go on: the server still refuses a
    request without the sign-in, recording and sending nothing.
  - OUTSIDE SERVICES: the response, from the instructor's Approve, and the student's notice that follows it (STUDENT NOTICE, named
    in AGENTS.md OUTSIDE SERVICES); Gmail is otherwise only read. Tests: `tests/test_department_makeups.py`.
- The instructor asked (2026-09-30), from my suggestion list: an excused-absence record (Student Rule 7) per student with its
  documentation, feeding the gradebook's Excused cells and the exam records (that feeding was removed 2026-10-01, NO-GRADEBOOK;
  only the iClicker credit came back, ICLICKER-CREDIT).
  Asked, they chose its own sidebar page and documentation uploaded or picked from a captured email. `absences/` (service, api,
  migration `absences/001_absences`), `static/absences.js`, `static/absences.css`, `#absences` (sidebar entry after Accommodations,
  palette "Record an absence"), `tests/test_absences.py`, `tests/browser_absences.py`.
- OWNER: the student's permanent SECTION MEMBERSHIP `(section_id, enrollment_id)` of that section's term (`absences.term_id`, held to
  the section's term by a trigger). The facts (dates, reason, details) and the decision (`pending`, `excused`, `not_excused`,
  `withdrawn`) are immutable revisions; the highest counts; a save names `base_revision` (409 when stale) and a request ID; an
  identical revision records nothing. Reasons are a fixed list (`service.REASONS`), never numbered rule citations.
- DOCUMENTATION: an upload (PDF, JPEG, PNG, HEIC, WebP by their first bytes, 20 MB) is kept in the SECTION's verified storage
  (`absences/<absence id>/<document id>.<ext>`, `DocumentStorage.check_source` lists it), or a REFERENCE to an attachment already kept
  in the student's communications record (the message must be linked to that student; read through the record and checked against the
  checksum it had when picked; never copied). A file the student linked from Google Drive is such an attachment once Faculty has
  fetched it (COMMUNICATIONS "LINKED DRIVE FILES", 2026-10-11); a link that was not read is not a document and cannot be picked.
  Documents are removed and restored by record (`absence_document_events`), never deleted;
  the route is a download (never rendered in the app's origin), and the absence window opens a document in the app's file viewer
  (VIEWER in assistant.md: pictures of a PDF's pages drawn in the sandbox, a re-encoded picture; requirement, 2026-10-01: "switch those
  other download links to the viewer too"), whose Download saves it. The assistant cannot upload (`never`); it fetches a document only with
  `fetch_files`, shown as a link in its drawer or looked at when asked (2026-09-30, "Faculty assistant" FILES). A standing
  document's picture (a PDF's first four pages) is embedded on this machine so it can be found by how it looks
  (PICTURE-SEARCH, 2026-10-10): no words are stored about it, a result never says whose it is, and a removed document is
  never returned.
- SHOWN: the page lists absences (current terms by default; filters by decision, course, search; the address carries only filters).
  It opens on the absences Waiting for your decision, the first decision chip, with All second (`status=all`; requirement, 2026-10-08:
  "the default landing page should be the waiting absences, All and Waiting should swap places"); with none waiting it says so and
  offers every absence;
  its two views (Absences, Makeups owed) are sub-tabs (DESIGN-LANGUAGE §4) on a row of their own, the decision filter's chips sit
  with search and terms under them, and the Waiting and Makeups owed counts are the orange needs-you block when not 0 (requirement, 2026-10-05:
  the page felt "hard to use and not intuitive"; ASSIGNMENT-WORKSPACE "ONE NAVIGATION"). A row is one line where it can be: the
  section alone while one course of the current terms is shown, the reason's details cut to one line (all of it in the hint and
  the absence's window); only an undecided absence is marked (the warning chip), a settled decision reads as words. Makeups
  owed lists the owed makeups first and the booking switch (a setting) after them; a makeup version waiting for approval makes
  "Open makeup version…" the group's primary; an approved one is in the assignment's grading pipeline, so the group's way to it
  links there instead of the draft window, which would offer the next version (requirement, 2026-10-06): "Finalize rubrics" while
  its rubrics wait on the instructor, else "Open makeup version →" (`#grading/<plan>`); an owed makeup already on a sitting waits
  on the student, so its chip is neutral.
  ONE GRID, ONE ROW, ONE STEP (requirement, 2026-11-12, of each row's buttons stacked three deep in the Makeup column and every
  group's table at its own column widths: "this page is a bit unruly, I think we can improve the aesthetics and user friendliness
  at the same time"; DESIGN-LANGUAGE §1.3, §1.6): every group's table has the same five columns at the same widths (Student,
  Section, Absence, Makeup, the row's actions), so a column reads straight down the page. The Makeup column says where the makeup
  stands and has nothing to press: its chip, then short lines; a booked row's line is its day, time and room alone, in ink (the
  chip already says Booked). The row's actions are a column of their own at the right. At most one is framed, the step that moves
  the makeup on: Send a new card or Send booking card, Release (and send card) on a held row, Print PDF while the copy still waits
  to be printed (ready, did not print, or may have printed). The rest are text actions on one line under it: Print PDF of a copy
  that printed at home or is on its way there, Hold… or Change…, and Missed makeup… last. A row with nothing to press has an
  empty cell. In the group's head Open on Exams is a go-link before the two framed actions (Schedule makeup…, the makeup
  version's), and "makeup version E ready" ends the line under the title. No label, route, state or count changed
  (`absence-makeups.js` `cellMarkup`, `actionsMarkup`; `absences.css`; `tests/test_absence_makeups.mjs`);
  the gradebook's student record and the roster's student window show the student's absences and Record an absence. The
  Certify dialog lists, beside each student about to receive 0, that membership's absences with their
  decision, marked when one covers the day the assessment was given (requirement, 2026-10-02: display only, nothing is excused;
  GRADING "ABSENCES BESIDE ZEROS"); it reads them through `standing` and writes nothing. An absence
  opened from a student's card offers "Back to <name>", and a saved change returns to the card (2026-10-02); the gradebook's record
  reads the gradebook again first, so an excused absence's iClicker credit shows at once. New absences default to
  the campus date (`localToday`, never UTC). The page's list is patched in place: the shell's 30 s refresh keeps the table's scroll
  and the search's caret (desktop only since 2026-10-03: the phone layout of one record per absence is gone). Home's Needs you
  (and the sidebar badge) counts absences of current terms waiting for a decision (`absences`, href `#absences?status=pending`), and the makeups owed (MAKEUPS).
