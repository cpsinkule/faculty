## Scheduling

Rule ID: **SCHEDULING**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-10-05), after a day of separate fixes to makeup times, meeting hours, makeup rooms and quiz lengths:
  "should we just make an entire scheduling infrastructure instead of piecemeal work like this?" Asked, they chose ONE ENGINE AND A
  REAL WEEKLY SCHEDULE first (no self-booking, room capacities or holidays yet), FACULTY'S TERM SCHEDULE as the source of truth for
  office hours and class meetings (shown on the Calendar and Google, drafting the Canvas Office Hours text), DR bookings never
  blocking, switching with every difference listed and confirmed (Undo), routine schedule rows left out of Home's Coming up, and
  meeting hours as availability, never calendar blocks. `scheduling/` is built in stages (1a one clock, 1b one engine and feed, 1c
  the Term schedule's data and editor, 1d using it, 1e meeting hours and appointments, 1f the Canvas text, 1g sitting end times);
  each stage below is binding once it lands.
- ONE CLOCK (stage 1a): `scheduling/clock.py` is the one reader and writer of campus wall-clock times. `parse_clock(value, mode)`:
  "hhmm" is a stored field ("13:00"), "any" a clock as typed ("7:05 PM", "7pm", "19:05"; the rule of `calendar-feed.js`
  `clockMinutes`), "loose" a stored time or the first am/pm clock in text ("After lecture, around 2 PM"; a typed range starts at
  its start). `parse_span` reads ranges ("7:05–8:55 PM", "9–10 AM", "13:00-17:00"), carrying the end's am/pm to a bare start when
  that keeps it first. Formats: `twelve`/`span_text` ("3:00–5:00 pm") in scheduling text, `display_clock` ("2:00 PM") on cards.
  The modules that had their own (`absences/makeup_slots.py` clock/loose_clock/twelve/span/day_label/long_day,
  `google_calendar/service.py` clock, `student_support/meeting_hours.py` CLOCK/minutes/twelve, `assistant/followups.py`
  CLOCK/_minutes/_clock_plus, `assistant/followup_effects.py` clock, `jev/send_checks.py` _clock) now call it; their answers are
  unchanged (`tests/test_scheduling_clock.py` checks the old names against every case). `dr_exam_import.clock_time` keeps its own
  import rule (seconds, exact formats) until DR imports move. `tests/fixtures/scheduling/clock_cases.json` holds Python and
  JavaScript to the same readings (`tests/test_clock_cases.mjs`).
- BUSINESS DAYS (2026-10-05, requirement of Student Rule 7's deadlines counted over university holidays: "fix the holidays"):
  `scheduling/clock.py` `UNIVERSITY_HOLIDAYS` lists Texas A&M University's holidays per fiscal year as the Texas A&M System
  publishes them (https://www.tamus.edu/hr/holiday/; fiscal year 2027: Sep 7; Nov 26–27; Dec 24–Jan 1; Jan 18; Mar 18–19; May 31),
  `HOLIDAYS_THROUGH` its last day. `business_day(day)` is a Monday to Friday not in it; `business_days_after(day, n)` counts them.
  Owner: the workspace (one university calendar, in code). Add each fiscal year's list before it starts; past `HOLIDAYS_THROUGH`
  a date counts Monday to Friday only. ABSENCES' Rule 7 days use it, and so does makeup booking's 3-business-day "No answer" mark
  (`absences/makeup_booking`; it counted Monday to Friday until the 2026-10-24 refactor sweep, kept by the instructor).
- ONE ZONE (stage 1h, 2026-10-05): the campus zone is named once on the server, `scheduling/clock.py` `ZONE_NAME`/`CAMPUS_ZONE`
  (with `campus_now`, `campus_today`), and once in the browser, `static/calendar-feed.js` `CAMPUS_ZONE` (seating-state.js imports
  it). The modules that named "America/Chicago" themselves keep their old names as aliases of it (`workspace_calendar.TIME_ZONE`,
  `google_calendar.service.ZONE`/`ZONE_NAME`, `CAMPUS` in canvas/placement, gradebook/canvas_import, exam_workflow/overview,
  assistant/conversations and jev/checks, `absences.makeup_booking.TIME_ZONE`, `document_identity.assignment_finder.TIME_ZONE`),
  so nothing they answer changes; `tests/test_scheduling_clock.py` fails if a third place names the zone. `dr_exam_import.clock_time`
  still keeps its own stricter import rule (above): DR bookings never block, so its readings never reach the engine.
- ONE ENGINE (stage 1b): `scheduling/engine.py` decides what each calendar entry is and what it does to a new makeup or meeting.
  `kind_of` maps a row to one kind (appointment: a student's office-hours appointment; makeup: a makeup sitting or a title saying
  makeup; dr; deadline: a deadline category or a gradebook due date; office_hours: a title saying "office hour"; exam: a sitting or
  seating chart; else other). `SHARING` is the one table of effects: for a new MAKEUP another makeup shares, meeting hours host,
  office hours host only in a room the term ticked as able to seat one ("room"), a DR exam and a due date are free (instructor,
  2026-10-05: DR exams are at the testing center), anything else blocks; for a new MEETING (an appointment, a reply's offered time)
  office and meeting hours host, another appointment shares (requirement, 2026-10-06, after confirming a reply's open times may
  overlap a student's office-hours hold: "appointments should overlap too"; only a makeup needs its room to itself), a DR exam and
  a due date are free, anything else (a makeup included) blocks. All-day entries never
  take time; an entry without an end lasts an hour, a makeup sitting its quiz (`minutes`). `busy`, `open_starts` (starts every
  step whose whole length is clear and ends by the window's end), `free_spans` and `check` (overlaps and within) are what makeup
  cards (`absences/makeup_slots.py`, its old helpers kept as wrappers), a reply's open meeting times (`_meeting_facts`) and a
  card's calendar note (`calendar_note`, which for a makeup sitting reads the term's makeup rooms from its students' section) use.
- ONE FEED (stage 1b): `scheduling/feed.py` `assemble` builds the calendar once (Faculty's sources, custom events, then Faculty's
  copy of the Google events when asked) for `/api/calendar`, makeup cards and Google sync, which each built it by hand before. Each
  caller keeps its own sort and filters; the bodies are unchanged. Checked on a copy of the live catalog: the Calendar body, Google's
  planned events, every makeup card and the reply meeting times were identical before and after; the only difference was a makeup
  proposed in the small office's Tuesday office hours, now an overlap (ABSENCES "MAKEUP BOOKING" ROOMS).
- TERM SCHEDULE (stage 1c): owner the course term (`course_terms.id`). `scheduling_revisions` (migration `scheduling/001`) holds
  immutable revisions of the term's first and last day of classes and its weekly entries `[{entry, weekdays [0 Monday..6], start,
  end "HH:MM", room, seats_makeup}]`; `scheduling_entries` gives each entry one stable identity and its kind (class, office_hours,
  meeting_hours), a class meeting also its section (`course_sections.id`, a trigger keeping it the term's own). Entries are never
  rewritten or deleted (an entry left out of a revision stays retained). Validation: at most 60 entries over at most 200 days, each
  on at least one weekday, at least 10 minutes, a room of at most 80 characters; meeting-hours windows never overlap on a day; an
  entry keeps its kind and section; office hours say whether their room can seat a makeup (`seats_makeup`), meeting hours always
  can. Exact request IDs, the revision the page showed (409 when stale or a request ID is reused for another schedule), identical
  schedules record nothing. Routes: `GET /api/scheduling/groups/<group>` (a course page's term), `GET`/`POST
  /api/scheduling/terms/<term>`, `GET .../import` (a proposal from the term's per-date "<code> · Section N" and "<code> · Office
  hours" events, each weekday, time and room seen on two or more dates, the dates from the first and last of them, the legacy
  meeting hours and makeup rooms; it writes nothing). An assistant write is an Approve card. A course edit that would leave the
  term, or a section a class meeting names, without an offering is refused (`scheduling.service.protect_references`, the
  calendar's rule). Edited on the course Home's "Schedule" row, `#course/<group>/home/schedule` (`static/term-schedule.js`): Term
  dates, Classes (per section), Office and meeting hours (with "Seats a makeup"); one primary by state (Import from your calendar
  while empty and unsaved, Save schedule while edited); a leave guard. Nothing reads a revision until the term switches to it
  (stage 1d): the Calendar, makeup cards and replies are unchanged, and the page says so. Replayed on a copy of the live catalog,
  the import read MATH 140 Fall 2026's 224 events as its four sections' Monday/Wednesday/Friday meetings, Monday (BLOC 309) and
  Friday (BLOC 306) office hours that seat a makeup, Tuesday/Thursday (BLOC 319) office hours that do not, the Tuesday/Thursday
  1–5 pm meeting hours, and August 24 to December 3.
- USE THIS SCHEDULE (stage 1d): a term USES its Term schedule while its latest `scheduling_uses` receipt (migration
  `scheduling/002`) is a Use. Then: the schedule's class meetings and office hours show on the Calendar and Google as rows
  `schedule:<entry>:<date>` (source `schedule`, title "Section 507" or "Office hours", the course code apart, a link to the Term
  schedule; meeting hours never show: they are availability), on each weekday of the term's dates but its DAYS OFF; the per-date
  custom events the Use matched one for one (same kind, section, date, start, end and room) stop showing (`scheduling_replaced_events`,
  a view mark: `calendar_events` is never changed or deleted); `availability` (meeting hours and the office-hours rooms that seat a
  makeup) comes from the schedule for makeup cards, a reply's open meeting times, DONE FOR YOU's bound and a card's calendar note
  (else from the legacy meeting hours); the engine reads a schedule's office hours by their own "Seats a makeup". Use is explicit:
  `GET /api/scheduling/terms/<term>/use` is the dry run (matched, by kind; the legacy events left showing; dates the schedule adds;
  how many replaced events are from today on, which Google replaces too) with a fingerprint, and `POST .../use` `{request_id,
  fingerprint, confirmation:"use"}` records it (409 when the fingerprint is stale or the term already uses it); `POST .../use/undo`
  `{request_id, confirmation:"undo"}` brings every hidden event back. Both are Approve cards for the assistant. Google: a schedule
  row goes as "Your events" (`kind` custom), one event each keyed by its id, so a later time or room edit updates it in place; Use
  replaces the matched events in Google over the following rounds (the old ones carried a doubled course code). Home's Coming up
  leaves schedule rows out (requirement, 2026-10-05); the Calendar page shows them as "Term schedule".
- DAYS OFF (stage 1d, found by the rehearsal on a copy of the live catalog: the per-date events skip Labor Day, Thanksgiving and a
  day run on another day's schedule, which a weekly schedule alone would fill): each revision keeps the dates without its weekly
  entries (`days_off`, at most 60, inside the term's dates). The import proposes them, and "Find days off from your calendar"
  (`GET .../days-off`, for a saved schedule) adds them: every date where none of the times the schedule would show has a matching
  per-date event. Rehearsed for MATH 140 Fall 2026: days off Sep 7, Nov 25, 26, 27 and Dec 1; Use then hides 219 events (164 class,
  55 office hours), adds none, leaves Dec 1's five showing; makeup cards and reply meeting times are unchanged; Google replaces 112
  events in its window; Undo restores all 219.
- MEETING HOURS AND APPOINTMENTS (stage 1e): once a term uses its Term schedule, its meeting hours and the office-hours rooms that
  seat a makeup are edited only there: `POST /api/student-support/meeting-hours/<term>` answers 409 naming the Term schedule, and the
  Term policies window's Meeting hours shows them read-only with "Edit in the Term schedule" (the `?areas=1` read adds each term's
  `schedule: {in_use, href, windows, makeup_rooms}`; the plain read is unchanged). The legacy table and its revisions stay. The
  office-hours facts a reply reads are inferred from the calendar as before, which now carries the schedule's weekly rows.
  `GET /api/scheduling/check?term_id|enrollment_id&date&time&end_time[&own]` (read only) says what a proposed office-hours
  appointment overlaps (the engine's `check` for a meeting: a makeup, a class, anything timed but office hours, another
  appointment, a DR exam or a due date) and whether it falls outside the term's office and meeting hours; the appointment window shows it as a warning
  ("Overlaps Section 507 (8:00–8:50 AM)", "Outside your office and meeting hours") and never refuses a save.
- CANVAS OFFICE HOURS (stage 1f): the Office Hours page of the term's Canvas copy is drafted from its saved Term schedule
  (`canvas/office_hours.py`, pure): the page keeps its own opening heading and closing pictures, and between them lists the office
  hours ("In person (regularly scheduled)", "Mondays, 3:00–5:00 PM · BLOC 309", days sharing a time and room together) and the
  meeting hours ("Or by appointment (email to request)"). `GET /api/canvas/content/<term>/office-hours` says whether the page differs
  (its words, tags and spacing aside) and returns the draft; `POST` `{request_id, base_revision, confirmation:"put"}` records it as a
  revision of the copy (CANVAS: the copy only; Canvas changes on the ordinary Push). The Term schedule page says whether the page
  matches, with "Update the copy…" (a window listing the new times, linking to Canvas content to push). Placed on the Term
  schedule page rather than as a Canvas hub step: the schedule is where the instructor changes the times.
- TYPED TIMES (stage 1g): a sitting's or seating chart's time is read as typed: a clock in any form ("7:05PM", "9:00 AM") and a
  range with its end ("9:00 AM–10:15 AM", "7:05–8:55 PM"); a makeup also from loose text. So such a sitting takes its time for
  makeups and meetings (before, only "HH:MM" did), lasting its typed range, else its `end_time`, else its quiz (a makeup), else an
  hour. The calendar projection shows a time typed as a range as its start and end (`workspace_calendar.typed_range`), so the
  Calendar and Google show a timed event (a group whose identity changes is replaced once in Google); every other time stays as
  written. The plan's separate end-time column and form field were not needed: the sitting's "Local time" already asks for a
  range ("9:00 AM–10:15 AM"), and reading it gives the end. On a copy of the live catalog (no ranges typed yet) the Calendar body,
  Google's planned events, makeup cards and reply meeting times were identical before and after.
- GOOGLE CHECKED (requirement, 2026-10-05, before reading office hours from the Term schedule: "my concern is that the agent looks
  at the term schedule only and then forgets to check my calendar in case there is something external there that conflicts with
  scheduling appointments with students"; they chose this guard). The Term schedule says when the instructor is willing to meet;
  every check of a time still reads the whole calendar (`assemble`: Faculty's events, the schedule, and Faculty's copy of the
  Google events). `GoogleCalendarService.unchecked` (through `feed.google_gap`, which counts not knowing as unchecked) says why that
  copy cannot vouch for a time now: while the sync is on, Faculty is not connected with the calendar permission, or has not read
  Google in the last 15 minutes (`STALE`; rounds run every 2 minutes); None while the sync is off or was never set up (Faculty then
  reads no Google events, by the instructor's choice). While unchecked: a follow-up's meeting facts say so and tell the draft to say
  it in its explanation; a card's calendar note shows "Google Calendar not checked"; DONE FOR YOU books no appointment by itself (a
  card says why); a quiz makeup's first card waits (state `waiting`, "Card waits", its label the reason; not a flag) and goes out
  on the first worker turn after Google is read again (no back-off); a card sent by hand ("Send a new card", or "Send booking card"
  while the term's cards are off) is refused with the reason and nothing recorded (requirement, 2026-10-06: "Number 3 would be good
  to fix"; a replay of a request already made still returns its card). A pick from a card already sent still seats the student (its
  times were checked when it went out; requirement, 2026-10-06: "i'll just deal with those conflicts if they arise"; no recheck at the pick).
- AWAY (requirement, 2026-11-08, of a doctor's appointment one Friday afternoon: "this is a one-off thing that is handled just by
  putting in a calendar entry"). `engine.AWAY`/`is_away`: a timed entry (Faculty's or Google's) whose title
  says the instructor is not there ("Away", "Unavailable", "Not available", "Out of office", "OOO", "No office hours", "Office
  hours cancelled") is the kind `away`, read before makeup so "Unavailable for makeups" is time away; it is never an office-hours
  block, and it blocks every new thing (SHARING's default). `engine.away(events, day)` gives its minutes on a day: the one thing
  that shortens an office-hours block a makeup card offers (ABSENCES "MAKEUP BOOKING" › AWAY), and a follow-up's office-hours facts
  name a block it covers within a week of the message ("Not on Fri, Nov 13 · 3:00–5:00 pm: your calendar says you are away").
  A student's appointment is never away, whatever its title. The Assistant writes such an event for a day the instructor says they
  will be away (assistant/guide.md), never a memory (ASSISTANT-MEMORY "ONE DAY GOES ON THE CALENDAR"). On the live calendar two
  earlier custom events read as away ("Exam labeling — unavailable for new bookings", "Unavailable for appointments"), both past,
  neither over office hours; no other title matched.
- ALL-DAY ENTRIES (requirement, 2026-10-05): an all-day entry never takes time, warns or holds anything; only timed entries
  count. The same day a warn-and-hold rule was built at their word ("Warn, don't block"; makeup cards "Hold it for you"), then
  removed when they saw what their all-day events are: "those all day events are old things faculty published to my calendar about
  student follow ups and assignment due dates" (notes about students' absences, TA notes, school deadlines; none time they are
  away). Asked again, they chose "Stop counting them".
- ICLICKER AFTER CLASS (requirement, 2026-10-06; docs/decisions/iclicker.md AFTER CLASS): the Term schedule also says when each
  section's iClicker is imported. `gradebook/iclicker_after_class.py` reads today's class meetings (`occurrences`, days off
  skipped) of every current term that uses its schedule (`current_use`) and runs a section's import ten minutes after its last
  class of the day ends. A term not using its schedule has no automatic import; the section's gradebook says so.
- CLASS SCHEDULE (requirement, 2026-10-10: "For each section, I want to add a class schedule to list due dates, given on dates, etc
  to allow the agent to reference when needed and other automations"). Asked, they chose: every date Faculty already holds shown
  beside PLANNED ROWS for work not created yet (a planned row follows the real record once it exists); one set of rows for the
  term with a section's own values where they differ ("Most are the same, my one concern is Exams, each section has its own final
  exam time/date/place. Also, regular exams are at the same time, but different places for each section"); what each class day
  covers; the Assistant's lookup, pre-fill on new work, follow-ups reading it and orange where records disagree; planned rows NOT
  on the Calendar for now; built whole and landed once. A section's class schedule is a READ
  (`scheduling/class_schedule.py` `section_schedule`): per date, the section's class meetings, a day off, the day's topics and the
  work given or due, each date read from the record that owns it and never copied: class days as the Calendar shows them (the Term
  schedule's meetings while the term uses it, plus the per-date class events the Use left showing, such as a day run on another
  day's schedule; no revision is read before the term uses it), the day, time and place an assessment is given
  (`exam_workflow.given_dates.term_given`, EXAMS "DATE GIVEN"), a gradebook column's due date (honoring the column's sections),
  and the section's due date in Faculty's Canvas copy (`canvas.content.due_for`; Canvas itself is never contacted). A work is
  joined once: by its course-wide assignment (its exam record, seating charts, the columns and the copy's assignment naming it),
  else its column, else the copy's assignment alone. A due date equal to the day given is one line. Certified work stays listed.
- CLASS SCHEDULE: THE PLAN. Owner the course term (`course_terms.id`); a section's own values are keyed by `course_sections.id`
  inside the term's revision (as the Canvas copy's `section_due`). Migration `scheduling/003`: `scheduling_plan_items` (a planned
  row's stable identity), `scheduling_plan_revisions` (immutable: `items_json` and `topics_json`, exact request IDs) and
  `scheduling_plan_links` (append-only; a row's latest link counts). A planned row: `{item, kind quiz|exam|final|homework|other,
  title, sections ([] = every section), date, time, place, due_date, section_values {section: {date, time, place, due_date}},
  note}`: a quiz, exam or final is GIVEN on `date` (and due on `due_date` only when that is another day); homework is DUE on
  `date`; anything else is on `date`. At most 150 rows; titles unique; a time is a clock or a range as typed
  (`scheduling.clock.parse_span`); dates within a month of the term's classes once a Term schedule is saved; a row may have no
  date yet. Topics: `{date: {notes [numbers], note, sections {section: {notes, note}}}}`, the term's by date with a section's own
  entry where it differs, at most 200 days and 8 notes sections a day. A topic names a notes section of the course by its NUMBER
  ("2.3": COURSE-NOTES, the one reference that outlives a new upload), resolved at each read
  (`course_notes.service.current_sections`); a number the notes lack is kept and shown as written. This is a notes-section number
  inside one course, not the section number IDENTITY warns about. `POST /api/scheduling/terms/<term>/class-schedule`
  `{request_id, base_revision, set [rows], remove [items], topics {date: entry|null}}` records ONE new whole revision (409 when
  stale or a request ID is reused for another change; a change that leaves the plan as it was records nothing); a new row gets
  its identity there. A course edit that would leave the term, or a section the plan names, without an offering is refused
  (`protect_references`). An assistant write is an Approve card, checked when it is made by
  `POST .../class-schedule/preview` (same body, no request ID; writes nothing): a change the plan would refuse, such as a second
  row of one title or a stale revision, is refused there and no card is made; the card shows the rows it adds or changes and the
  titles it removes, and Approve sends the preview's `fingerprint` back, which the save checks (2026-10-28: a follow-up planned a
  second "In-class quiz" beside the Oct 16 one, and its card failed only on Approve with "Two planned rows share a title"). A
  title clash names the title and the date of the row already planned.
- CLASS SCHEDULE: FOLLOWING A RECORD. `POST .../class-schedule/links` `{request_id, item, assignment_id | gradebook_item}` (or
  `action: "unlink"`) records the real record a planned row became: the course-wide assignment (with the term, its exam record
  and seating charts) or a column of the term's gradebook; one record follows one row. Kept apart from the revisions so a link
  never refuses a schedule being edited. A linked row FOLLOWS its record: the schedule shows the record's date, time and place
  (`planned_was` keeps what was planned, never orange), and the planned value only where the record has none. For one section,
  each of an assessment's day, time and place comes from the most specific record that has it: the section's seating chart, then
  the planned row's own value for that section, then the exam record's main sitting, then the planned row's value for every
  section; so one "Given on" typed for a final never replaces the sections' own planned finals. A column that left the gradebook
  leaves its row planned again (`missing`), with Unlink.
- CLASS SCHEDULE: DISAGREEMENTS. Marked (`differs`, orange "Dates differ" with each date a link to where it is changed), never
  settled on the schedule; campus dates compared as written. `gradebook_canvas`: a column's due date and the section's due date in
  the Canvas copy differ and the column does not carry the LATEST of its sections' Canvas dates (CANVAS "WEBASSIGN DUE DATES": a
  column has one date, the latest of its courses'; the earlier section is then right, the later one would count zeros early);
  not for a column kept off Canvas. `given_gradebook` / `given_canvas`: the day the record says it is given differs from that due
  date, and the due date is not the latest given day among the column's sections (or the sections sharing the Canvas course);
  not for work whose planned row records a due date apart from its day (take-home work, on purpose), and never against a merely
  planned day. `plan_given`: a section's own planned day differs from a main sitting dated for every section while no seating
  chart names the section. Certified work is never marked (CERTIFIED-ARCHIVE). A due date the copy cannot resolve for the
  section's course is listed as `unclear`, not marked. Rehearsed on a snapshot of the live catalog (2026-10-10, MATH 140 Fall
  2026, four sections): 85 works (4 by assignment, 80 by column, 1 the copy's alone), 100 comparable gradebook/Canvas-copy pairs,
  all agreeing, so nothing marked; no given day beside a due date to compare yet (the four assessment columns carry none); 42
  class days and 3 days off a section, the redefined Dec 1 a class day; the per-student `given_dates` identical for all 2,716
  readings; a page read 9 ms in 50 statements.
- CLASS SCHEDULE: ROUTES, PAGE AND READERS. `GET /api/scheduling/sections/<section>/class-schedule?start&end` (both or neither:
  without them two weeks back to four ahead of the campus day, at most 43 dates, under the Assistant's 60-item trim; top-level
  `revision` is the plan's), `GET /api/scheduling/groups/<group>/sections/<n>/class-schedule` (the page: every date, the term's
  sections, the plan as stored and its open rows), `GET /api/scheduling/terms/<term>/class-schedule` (the plan with links) and
  `GET .../class-schedule/open?kind=` (rows no record follows yet, each with the term's one unlinked record of the same title as
  `match`). Page `#course/<group>/section/<n>/schedule` (`static/class-schedule.js`), from the section tab's "Class schedule"
  row: one table (Week, Date, Class, Topics, Given and due), earlier weeks folded, today marked, finals under "After classes end",
  undated rows under "No date yet"; "Add a date" (all sections by default, "Sections differ" for each section's own date, time
  and place) and "Edit topics" (one field per class day, notes section numbers first, then a note; "Only <section>"); one primary
  by state (Save topics; Add a date while the plan is empty); while on a schedule the course tabs lead to each section's
  schedule and Home to the Term schedule. The Assistant reads it with `class_schedule` (ASSISTANT); a follow-up's preparation
  reads the student's section (ASSISTANT "FOLLOW-UP FACTS").
- CLASS SCHEDULE: PRE-FILL. A form that creates the work offers the term's open planned rows as "From the schedule"
  (`static/schedule-prefill.js`), preselected by title: choosing one fills the form's date while that field is empty or still
  holds a planned date (a typed date is never replaced), and the row then follows the new record. Quiz approval sends
  `planned_item` (checked open at approval; the worker links it once the assignment exists, request ID
  `quiz-approval:<approval>`; a link that cannot be made never fails the approval); Create assignment and the assignment card's
  "Given on…" link after their own save; a new gradebook column links to its due date; a seating chart's empty exam date and time
  start from the linked row's values for that chart's sections (each section's own final). With no open rows every form is as it
  was.
- CLASS SCHEDULE: NOT ELSEWHERE (requirement, 2026-10-10, asked whether planned rows also show on the Calendar: "Not now"). Planned
  rows and topics reach no other read: `/api/calendar`, Home's Coming up, Google sync, makeup and meeting time checks, the
  gradebook and `/api/state` are unchanged (held byte-identical in `tests/test_scheduling_class_schedule.py`); a planned row
  reaches them when its real record exists. Custom calendar events are not listed on the schedule. The one exception is the DR
  quizzes read (CLASS SCHEDULE: DR QUIZZES below), which reads the term's planned quiz rows and is shown only on the Accommodations
  page's DR quizzes tab and to the Assistant's `dr_quizzes`.
- CLASS SCHEDULE: WHAT A RECORDING REACHED (2026-11-13, step 5 of RECORDINGS; docs/decisions/recordings.md "Classes").
  A class day that a recording is filed on carries `recorded` in the section's class schedule read (`section_schedule`,
  so its page, the Assistant's `class_schedule` and every other reader of that read): `{recording_id, href, read, reached
  [{number, title}], not_reached [numbers], differs}`, read BY REFERENCE from the Recordings module that owns it
  (`recordings/classes.py` `recorded`) and never copied to the plan. Only such a day carries the key, so a schedule with
  no class recording reads exactly as before; a reading that cannot be set against the plan never keeps the schedule from
  being read. `reached` is what the recording's reading kept as worked through; `not_reached` the planned topics of that
  section's day reached neither that day nor on an earlier recorded class day; `differs` while that is so and the
  instructor has not declined the move. The page shows it under the day's topics: "Reached 5.6, 5.7" as the way to the
  recording, and an orange "Not reached 5.8" (an earlier week that holds one opens unfolded). THE MOVE is a change to this
  plan like any other and never automatic: one Approve card through `POST .../class-schedule` with its preview and
  `base_revision` (so a plan changed meanwhile refuses it), setting THAT SECTION's own topics for the two days (TOPICS:
  "a section's own entry where it differs"); Recordings takes back and re-proposes a card the plan has moved past. The
  page reads the schedule again when it is come back to from another page (it kept the view it had first read).
  NOT ELSEWHERE holds: `recorded` reaches no other read, and a follow-up preparation's "covered" still names the plan.
- CLASS SCHEDULE: DR QUIZZES (requirement, 2026-10-15, answers 3(a), 4(a), 5(a)). `scheduling/dr_quizzes.py` `term_quizzes`, owner
  the course term (no table, column or file of its own beyond DR DATE below), at `GET /api/scheduling/terms/<term_id>/dr-quizzes`
  (`scheduling.dr_quizzes`, GET only; 404 for an unknown term). A read of the catalog only: it writes nothing, takes no lock and
  contacts nothing outside Faculty. A section is listed when a current (not dropped) student of it is a DR quiz taker (their
  ACCOMMODATIONS "DR QUIZ TAKERS" flag is on; requirement, 2026-10-15: "specific students that affirmatively say they want to use
  their accomodation for quizzes, not every student that registers for exams", so a DR booking or a granted Extra Time alone never
  counts), with `dr_students` its distinct flagged enrollments. For each
  listed section: every planned `quiz` row that applies to it, open or linked, not certified (unless its section's DR date is
  today or ahead, DR DATE below), given on or after the campus day or not dated yet (or recently given, DR DATE below). Date, time and place come from `TermRead.facts` (the most-specific-record rule above); with no time, the
  section's first class meeting that day (`time_source` "class"). `minutes` is the base quiz length (20; AIM applies each
  student's extra time); `ready` needs a date and a start, else `reason` "No date yet" or "No time yet". `aim` compares dates only
  with the section's imported DR bookings of the same canonical name or linked to the row's assignment: `not_in_aim`, `in_aim`
  (all on the row's date) or `aim_differs`. No student name, UIN or enrollment ID is in the response. `fingerprint` is the sha256
  of `{sections, quizzes}` as canonical JSON, for a later AIM write to confirm against. The tab also shows each quiz's accessible copy for the DR center from
  QUIZZES "ACCESSIBLE COPY", read beside this read and never part of it or its fingerprint.
  - DR DATE (requirement, 2026-10-31: "I want to try to send quiz 6 to DR center and get it ready for the 3 DR quiz takers", Quiz 6
    having been given in class the day before, two of them flagged after it; "yes, good suggestion go ahead" to a one-time DR date
    for a past quiz). A row's DR date is the day its section's DR quiz takers sit the quiz at the DR center when it is not the class's
    day. Table `dr_quiz_sittings` (migration `scheduling/007_dr_quiz_sittings`; owner the section, naming its term's planned row by
    reference; append-only, the latest row of a (row, section) counts, an empty date clears), written only by
    `scheduling/dr_quiz_sittings.py` `record` at `POST /api/scheduling/terms/<term_id>/dr-quizzes/dr-date` {request_id, key, date}
    (the row's key; a date on or after campus today; exact request IDs; answers the read), from the DR quizzes tab's "Set DR date",
    under the lifecycle lock. In the read a DR date replaces the class day as `date` (`class_date` keeps the class day, `dr_date` the
    DR date or ""), its time is that section's class meeting that day (else the row's time), and it is the date AIM, Send to AIM and
    the DR quiz notices use; the class schedule, its record and every other read are unchanged. A quiz given in class at most
    `PAST_DAYS` (14) days ago with no DR date stays listed, `ready` false with `reason` "Given in class", so a DR date can be set;
    a row whose DR date has passed, or given longer ago, is not listed. A DR date is not limited to past quizzes.
    CERTIFIED WITH A DR DATE AHEAD (requirement, 2026-11-01: Quiz 6, certified after class, left the list and Send to AIM before its
    Nov 4 DR sitting's accessible copy could be uploaded): a certified quiz stays listed for a section whose DR date is today or
    ahead, and only for that section (certification already leaves a student with a DR date ahead out, GRADING).
- DR QUIZ NOTICES (requirement, 2026-10-15, answers 1(a), 2(a), 3(a), 4(a), 6). `scheduling/dr_quiz_notices.py` is the only place
  that records "Prescheduled in AIM" marks and sends DR quiz notices; migration `scheduling/004_dr_quiz_notices`, both tables
  append-only. Nothing here signs in to AIM, reads it or writes to it.
  - TRIGGER. Before Job 2 (the AIM prescheduling write) exists, the instructor's own mark of a DR quizzes row, after they
    prescheduled it in AIM: `mark` checks the key is in the current `term_quizzes`, is `ready` (else 400) and that `fingerprint` is
    the read's current one (else 409), and records the row's `date` and `start`. Since SEND TO AIM (below), a date AIM's list shows
    after Send records the same mark (`aim_send.Recorder.mark`, with the sender as actor). A quiz's state is its latest mark: `marked` while the row is ready at the marked date and start,
    `changed` once either moved (the mark has LAPSED and starts nothing), else `unmarked`. `unmark` is allowed only until a notice
    for the mark's date exists (409 after).
  - THE DAY ONLY (requirement, 2026-10-18: "the emails should ask them to register, the DR center decides the time and place (I
    still decide the day though, so you can tell them the day)"). A student told of a quiz on a day is told of it once, whatever its
    start: notices count by (enrollment, section, quiz, date).
  - UPDATED NOTICE. Marking a changed quiz again records a new mark at its new date and start; each DR quiz taker already told of it
    on another day gets one notice whose line ends " (new date)". A change of time alone, re-marked, emails nobody.
  - ONE EMAIL PER STUDENT. To the student's roster address only (no Cc or Bcc, no other student named), from the Mail page's sender
    (`SENDER_NAME`, the connected account) so it is listed on Mail › Sent; subject "<code> quizzes ready to register at the DR center
    (AIM)"; "Hello,", "These <code> section <n> quizzes are prescheduled with the DR center. Please register for each one in AIM, as
    you do for exams. The DR center sets the time and room.", one line per quiz "- <title>: <Weekday, Month D>" with no time and no
    room (the class's time and place are never sent), signed "Professor Sinkule". The flag
    decides who is told: the email goes only to the section's DR quiz takers (`_testers`, ACCOMMODATIONS "DR QUIZ TAKERS"), never to
    a student only because of a DR booking or an Extra Time; a student not on a current roster, dropped or with no roster address
    is skipped with that reason. Unticking a student after a notice went out sends nothing further (they are no longer reached).
  - SWEEP. `DrQuizNotices.process_one`, a bounded turn of the Inbox worker (`inbox.dr_quiz_notices`): while Gmail is connected, it
    reads each term with a mark and sends ONE student ONE email listing every marked upcoming quiz of their section with no notice
    row at its date and start (so a student flagged later is reached too); with nothing to send it idles 60
    seconds. A mark wakes it, so the email follows within seconds.
  - RECEIPTS, NEVER RESENT. `gmail_mail.preview` first (nothing recorded if it fails), then the notice rows (attempt 1, gmail request
    ID `dr-quiz-notice:<uuid hex>`) committed under the roster lock, then `gmail_mail.send` (actor `dr-quiz-notice`) with the lock
    released. A notice's status is its send's latest outcome (`accepted`, `failed`; `uncertain` with no outcome or no send record:
    check Gmail's Sent folder). A notice that has a row is never put in another automatic email, whatever its status.
  - RETRY. The instructor's explicit action on one failed or uncertain notice email not yet retried (`batch`, its gmail request ID;
    409 otherwise): one new email, sent at once, of its quizzes still marked at the same date and start, as attempt + 1 under
    `dr-quiz-retry:<first 32 hex of sha256(term_id + request_id)>`; a repeated request ID returns what it did and sends nothing.
  - OWNERS. A mark (`dr_quiz_marks`) is the course term's (`course_terms.id`), naming its planned row (`scheduling_plan_items.id`)
    and section (`course_sections.id`) by reference. A notice (`dr_quiz_notices`) is the student's enrollment in the section
    (`student_enrollments.id` with `course_sections.id`), naming the mark and the `gmail_mail_sends.request_id` that carried it.
  - ROUTES. `GET /api/scheduling/terms/<term_id>/aim-prescheduling` (`scheduling.aim_prescheduling`): every DR quizzes row in order,
    `{key, state, mark {date, start, actor, at} | null, students [{name, status, detail, batch}]}`, a student's status `accepted`,
    `failed`, `uncertain`, `waiting` (marked, not sent yet), `skipped` (`detail` "no email address on the roster", "dropped" or "not
    on the roster") or null (not marked); no UIN or enrollment ID. POST `…/aim-prescheduling/mark` `{request_id, key, fingerprint}`,
    `…/unmark` `{request_id, key}` and `…/retry` `{request_id, batch}` answer that read; same-origin, 404 for an unknown term,
    exact request IDs (ASSISTANT: in `REQUEST_IDS`, the default Approve card). `GET /dr-quizzes` is unchanged. The page's controls
    (Accommodations DR quizzes tab): mark against the fingerprint after a confirm naming the count, Undo until a notice, each
    student's status, explicit Retry after a confirm.
- SEND TO AIM (requirement, 2026-10-27: "it needs to schedule the quiz on the same date as the quiz is scheduled for in Faculty and
  then upload the accessible version to the correct spot", "even if we need to use a duo push"). `scheduling/aim_send.py` is the
  only place Faculty writes to AIM (ACCOMMODATIONS), and only from the instructor's Send to AIM on a term of the DR quizzes tab: a
  confirm lists what it will do (GET `/api/scheduling/terms/<term_id>/aim-send`, `scheduling.aim_send`: every ready DR quizzes row,
  its accessible copy or why none, and its earlier writes), then POST the same address (`scheduling.aim_send_start`: request ID
  and the DR quizzes read's `fingerprint`, 409 when it changed; `saved`, or a typed NetID and password) starts AIM's sign-in
  exactly as Import from AIM's (the saved school sign-in, the instructor's Duo, AIM's FERPA page continued as their
  acknowledgment) in the same one-at-a-time window (`AimSessions.install`). Once in AIM, on the browser's thread:
  - DATES. AIM's Exam Dates list (Testing.aspx?Step=Dates, every row on one page) is read; each ready quiz whose section lists
    no Quiz on its day is added on that section's List Exam Dates page (InstructionAdd.aspx?Step=ExamTypes&From=Class&CID&ID, the
    CID and ID its own rows' links carry): type Quiz, the day, the base length (20 minutes; AIM adds each student's extra time)
    and the time 10 MINUTES AFTER THAT SECTION'S CLASS ENDS that day (`sitting`; requirement, 2026-10-27: "the quizzes should be
    scheduled for 10 minutes after the end of class for that student's section and class time on the day the quiz is scheduled to
    be given"): the section's class meeting that day, else the quiz row's own end, rounded up to AIM's five-minute steps; with no
    end to go by, the quiz is not added and says why. (Until then AIM was given no time; DR QUIZ NOTICES' email still tells the
    day only. Migration 005's comment predates this and stays as recorded.) The list read again confirms it. A quiz AIM lists (added now, earlier, or by the instructor in AIM)
    records its "Prescheduled in AIM" mark (DR QUIZ NOTICES), which emails the section's DR quiz takers to register.
  - UPLOADS. AIM attaches files to students' exam requests, not to the date. Upcoming Exams (Testing.aspx?Step=Exams, every row on
    one page) is read; each request of a listed quiz (its section, type Quiz and day) not already given the copy by an accepted
    or unsettled upload gets the quiz's accessible copy (QUIZZES "ACCESSIBLE COPY", its first ready version, with its page codes
    since 2026-11-10, GRADING "DR COPIES" > STAMPS) in one upload per
    quiz, titled "<title> (accessible copy)". AIM's "Number of Exams without Files Uploaded", read again, falling by exactly
    the requests sent confirms it. A quiz no student registered for yet waits for the next Send.
  - RECEIPTS, NEVER RESENT. Migration `scheduling/005`: `aim_sends` (owner the course term, naming the planned row and section;
    an upload also the approval, version and sha256 of the file, and each request only as the sha256 of its row: no name is
    kept), recorded before the post, and `aim_send_outcomes` after: accepted (the read confirmed it), failed (AIM refused it, or
    the page was not as mapped, so nothing was posted), uncertain (posted, and no read confirmed it). An unsettled write is never
    posted again by itself: a later read showing it settles it as accepted; else it waits for the instructor's Retry (POST
    `…/aim-send/retry`, `scheduling.aim_send_retry`, request ID and `send_id`; 409 for a settled write), which records
    `released` so the next Send may post it again, after a confirm telling them to check AIM first.
  - A page not as mapped (no table with the columns, no form field or Quiz type where expected, a form posting elsewhere, AIM's
    access policy or sign-in page) stops the send with the reason; nothing more is posted. After the writes, the DR bookings are
    exported and IMPORTED BY THEMSELVES (requirement, 2026-10-27: "I need this to be fairly automated. For all of the DR quiz
    takers, it needs to do what's necessary"): `import_bookings` runs the DR exam import's own preview and submits its import job
    against that preview's hash (term: all current terms, idempotency key `<run>:exams`), exactly what Import from AIM's review
    would; the send then ends "done" and the window shows a summary of what was done in AIM and imported. Only when that import
    cannot be made does the export go to the ordinary import review instead.
  - AFTER APPROVAL, NOT ON A TIMER (requirement, 2026-10-27: "This should ideally happen after a quiz is generated, not on some
    scheduled timer"). When a quiz is approved while its draft page is open (QUIZZES APPROVAL) and the term's Send to AIM plan
    has it (by its assignment, or by its title for a class-schedule row not linked to it: `quiz-drafts.js aimMatches`), the page
    opens Send to AIM at once with the plan's fingerprint: the instructor is there to read Duo's verified-push code. Uploads wait
    for students to register, so Home's card below and the next send carry them. Faculty starts no send by itself.
  - RUNS (migration `scheduling/006`): every send is an `aim_send_runs` row (the term, `instructor` or `automatic`, the campus day,
    how many quizzes it had) with at most one `aim_send_run_ends` row: `sent`, `stopped` (a page not as mapped, a failure),
    `not_signed_in` or `closed` (the window or browser ended first). The restart check counts a send signing in or writing ("AIM
    sends", `AimSessions.sending`).
  - DUE, ON HOME. `due`: a ready DR quiz whose date AIM has not confirmed and no write of it waits for Retry, or a quiz with an
    approved drafted copy, its date confirmed and still ahead, whose uploads AIM accepted cover fewer requests than the section
    has DR quiz takers (`dr_students` in the plan), when no send of the term ended sent that day (requirement, 2026-10-31: "yes" to
    the card staying up every day until each DR student's request has its file; it was 5 to 3 business days ahead, one day for a
    quiz sent late). A DR quiz taker who never registers keeps it up until the quiz. Home's
    "Send to AIM" card (workspace_overview, key `aim_send`, `aim_send.attention`) counts those over the current terms and the
    writes waiting for Retry ("AIM writes to check" when only those), says when the last send did not get through, and opens the
    DR quizzes tab, whose Send to AIM does the rest after the instructor's Duo (TAMU's verified push shows its code only in the
    window).
  - Mapped read only in the instructor's AIM on 2026-10-27; the fake AIM in `tests/test_aim_send.py` serves those pages as saved
    (`tests/fixtures/aim`, every student name, nine-digit number, email and opaque key replaced). Checks never sign in to AIM.
  - The assistant never starts a send (`scheduling.aim_send_start` is in its never tier: an AIM sign-in window); Retry is an
    Approve card (`REQUEST_IDS`). The tab shows each row's receipts under its AIM chip, with Retry for an unsettled one. The one
    other start is DR REQUEST EMAILS' upload card below, which only the instructor's Approve starts.
- DR REQUEST EMAILS (requirement, 2026-11-01: "one of the DR quiz students registered for quiz 6, can we make it so the app can
  recognize those confirmation emails and then queue up the accessible upload as a follow up (I know it can't do it automatically
  because of the duo push, but it would help to have it in the followups so I can just approve it there)").
  `scheduling/dr_requests.py`, a bounded turn of the Inbox worker (`inbox.dr_requests`); migration `scheduling/008_dr_exam_requests`.
  - READ. While Gmail is connected, every 10 minutes, the DR Testing Center's emails from info@disability.tamu.edu titled "[DR] New
    Exam Request" (a student requested to sit an exam or quiz at the Testing Center) or "[DR] Testing Request Approved" are read
    (reads only, the last 45 days, at most ten kept a turn) and each is kept once in `dr_exam_requests`: the UIN, course, section,
    exam type, day, start and Late Request, never the student's name, with the current student's enrollment and section when the
    UIN, course code and section number are theirs (owner: that enrollment in that section; neither when none matches). Any other
    email, or one from another sender, is not kept.
  - THE FOLLOW-UP. A kept request of type Quiz on the day of a DR quizzes row of its section (CLASS SCHEDULE: DR QUIZZES) whose
    accessible copy is ready, while AIM's accepted uploads of that quiz (SEND TO AIM's receipts) cover fewer requests than were kept
    for it, opens the student's Follow-up, once per student, row, section and day (`dr_exam_request_followups`): a tracked follow-up
    made by Faculty (To do, "Upload <quiz>'s accessible copy to AIM", what the email said, next step "Approve the upload; AIM's
    sign-in asks for your Duo"), a ready run of it prepared by Faculty, not a model (`FollowupSuggestions.faculty_followup`), and one
    card: `POST /api/scheduling/terms/<term_id>/aim-send/uploads` `{request_id, key, enrollment_id}` (`scheduling.aim_upload`,
    ASSISTANT Approve tier, confirmation "upload", in `REQUEST_IDS`). An exam's request opens nothing (Faculty uploads no exam).
  - THE CARD. Approve (on the Follow-up page, its Approve all, or wherever the card shows) runs the route with the instructor's
    confirmation: the quiz must still be in the plan with a ready copy (409 otherwise) and a sign-in saved on the server (else 400:
    Send to AIM from the tab); it starts SEND TO AIM's own send of that one quiz (its date added too when AIM lacks it; uploads,
    receipts and the bookings import as there) and answers the sign-in's session with `aim_upload` {title, section, date}. The page
    then opens AIM's sign-in window on that session ("Upload to AIM", app.js on `faculty:aim-upload`), where Duo's code shows, and
    says how it ended. A request ID starts once. Without the confirmation the route refuses (400): only the instructor's Approve
    starts it.
  - AGAIN (requirement, 2026-11-01, of Fiona Weaver's Quiz 6 card: "I got the push, but it never showed me the numbers"; the
    tab predated the restart, so no window opened, Duo's push timed out and the approved card read done). A follow-up whose upload is
    not covered yet, with no card waiting, whose last card failed or whose approval's send ended without "sent" (a send with no
    recorded end counts as over 30 minutes after it started: a restart cuts one off), gets a fresh card in the same run (`attempt`
    n, which the route ignores), at most three in all. A declined card is the instructor's decision and gets none.
  - RESOLVED BY ITSELF. Once AIM's accepted uploads of the quiz cover every request kept for it, Faculty saves the follow-up
    Resolved with a line saying so, unless the instructor saved it since (then it is theirs to settle). Its card, if still waiting,
    is withdrawn with it.
