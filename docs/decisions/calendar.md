## Google Calendar sync

Rule ID: **CALENDAR**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-09-28): "make the app calendar sync with my gmail calendar". Asked, they chose BOTH WAYS, the account
  Faculty already sends Response Card mail from (instructor@tamu.edu), Faculty's events in their MAIN calendar (not a calendar of
  Faculty's own), and AUTOMATIC sync. `google_calendar/` (CALENDAR.md "Google Calendar sync"), WORKSPACE-owned: append-only
  settings revisions, a ledger of the events Faculty put in Google (`google_calendar_pushed`), Faculty's copy of the chosen Google
  calendars (`google_calendar_events`, never exported) and the sync state.
- One Google connection (`response_cards/gmail.py`): the calendar adds `CALENDAR_SCOPES` to it, for the account already connected
  only, with `include_granted_scopes` on every sign-in; the one registered callback returns to the page that started it.
- Faculty changes and deletes ONLY events it made (its own derived IDs, recorded in the ledger); writes only what changed; never
  invites anyone or lets Google send mail; groups events as the Calendar page does; sends no student identity (DR groups carry
  sections and counts). Google events are shown read-only; Faculty never edits them. Faculty events the assistant creates or
  edits (its `auto` tier) sync like any other (requirement, 2026-10-01: "assistant should be able to change my calendar").
- SAID IN A RECORDED MEETING (2026-11-13, RECORDINGS "AN APPROVE CARD", step 3). A date a meeting fixed that the calendar does
  not hold is PROPOSED as a Faculty entry on an Approve card (`POST /api/calendar/events`, raised by the reading as a
  background proposal, so it waits for the instructor although the Assistant's own entries are automatic): the entry is made
  only by Approve, and then syncs like any other. Whether the calendar "holds" it is read from the one feed (an entry within
  45 minutes of the time said, or the course's own entries that day), Google's copy included. A meeting said NOT to happen
  changes nothing here: the recording's page lists what the calendar still holds that day, or the rest of the meeting's own
  series, and removing an entry of the instructor's own Google calendar stays theirs (Faculty never edits Google events).
- Rounds run in the Inbox worker (every 120 s, own lock, never the execution lock) and on Sync now; a stopped round records its
  reason and pauses. Checks never reach Google (tests replace `client.http` and the connection's `http`).

- TERM SCHEDULE (2026-10-05, SCHEDULING "USE THIS SCHEDULE"): a term that uses its Term schedule shows its class meetings and
  office hours as `source:"schedule"` rows (Google: "Your events", one each, keyed by entry and date) and stops showing the custom
  events the Use replaced (a view; nothing is deleted); Undo shows them again. Home's Coming up leaves schedule rows out.
- GOOGLE CHECKED (2026-10-05, SCHEDULING): while the sync is on, a copy of the Google events read more than 15 minutes ago (or no
  connection with the calendar permission) cannot vouch for a time; follow-ups, their cards and quiz makeup cards say so or wait.
- TYPED RANGES (2026-10-05, SCHEDULING "TYPED TIMES"): a sitting or seating chart whose time is typed as a range shows as its start
  and end (a timed event on the Calendar and in Google); other times stay as written.
- ONE FEED (2026-10-05, SCHEDULING): `/api/calendar`, Google sync (`_wanted`) and makeup cards assemble the calendar through
  `scheduling/feed.py` `assemble`; the event bodies and Google content hashes are unchanged.
- MAKEUP LENGTH (2026-10-05, ABSENCES "MAKEUP BOOKING" LENGTH): a makeup sitting projects with `makeup: true`, `minutes` (its longest
  student's quiz: 20 minutes times extra time) and, when its start is a clock time, the end that length makes; other sittings are
  unchanged. Google sync carries the new end by its own rules (a changed time is a changed group).
- OFFICE HOURS (2026-10-03, TEACHING-WORKFLOWS). Enrollment-owned appointments project as Office hours events with campus-local start/end and location. Since 2026-10-06 (requirement, 2026-10-06: "Those two rules for frontend and google should be deleted, I have no need for those restrictions and the student's names should be on their appointments"; the earlier rule kept every name out) each is titled "Office hours: <roster name>" on the Calendar page, Home and Google: `StudentSupportService.name_appointments` names them after the calendar is assembled, outside its catalog transaction (the names come from the verified roster files; a student no longer on a current roster keeps "Office hours"). The entry's ID stays the opaque appointment ID. Its purpose follows the name ("Office hours: <roster name> · <purpose>", `appointment_title`; requirement, 2026-10-06: "put the purpose on the appointments too"), left off when it only says office hours; private details are never projected. They belong to the existing Your events filter and Google sync preference; appointments have separate identities and cancelled appointments leave the projection. Edits open Office hours.
