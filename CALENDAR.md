# Calendar

The Calendar page combines existing schedules and instructor-authored events.
`workspace_calendar.py` is a read-only projection of exam sittings, dated seating
charts, and the current DR booking revisions. A seating chart is suppressed only
when its explicit sitting link has the same date and time. Imported DR bookings
are grouped by course, term, section, exam, date, time, and campus, with a count;
the feed contains no student names, UINs, enrollment IDs, or booking IDs. A term
gradebook column's due date is a read-only deadline of its term (source `gradebook`,
category `deadline`, shown as a Deadline named Gradebook). These
sources remain editable through their existing pages. Retained archived terms
remain visible when browsing historical dates.

## Custom event ownership

`CalendarService` owns `calendar_events` in the shared catalog. Each event chooses
one level explicitly:

| Scope | Permanent references |
| --- | --- |
| Workspace | No course references |
| Course | `course_definitions.id` |
| Term | Definition and `course_terms.id` |
| Section | Definition, term, and `course_sections.id` |

The service and catalog triggers validate parent relationships. Neither saved
offering IDs nor derived navigation group IDs own calendar records. Updating an
event's scope does not copy or edit parent resources. Course identity edits and
deletions cannot detach an active event from its last matching saved offering.
Archive instead, retain another matching offering, or explicitly change/delete
the calendar event first. Soft-deleted events do not block course edits.

All custom reads and writes use the roster lifecycle lock; writes acquire a
catalog transaction inside that lock. Creation has a UUID request ID bound to a
canonical payload hash, so retrying the same request cannot duplicate the event.
Updates and deletions require the version read by the caller and reject stale
versions with 409. Deletion is soft; creation receipts remain retained. Records
use the existing coordinated catalog backup and introduce no payload directories.

`calendar_migrations/001_events.sql` is recorded through `catalog_migrations`
under the lifecycle lock in one transaction. Applied versions are immutable;
append a new migration for later schema changes.

## Dates and API

Dates use `YYYY-MM-DD`; end dates are inclusive. Custom times use `HH:MM` in
campus local time (`America/Chicago`). All-day entries contain no times. Existing
source time strings are preserved; DR bookings keep DR-center local time. The
browser uses UTC only for date arithmetic and explicit date formatting, never
to convert an event's time. Recognized clock strings sort by local clock time.
Unrecognized descriptive times remain intact and sort after clock times.

- `GET /api/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD`: inclusive range of at most
  366 days; returns campus today, events, and the permanent scope catalog.
- `POST /api/calendar/events`: custom event fields and `request_id`.
- `PUT /api/calendar/events/<custom_id>`: changed fields and `version`.
- `DELETE /api/calendar/events/<custom_id>`: JSON containing `version`.

Feed IDs are source-prefixed; custom mutation URLs use `custom_id`. The existing
network boundary, same-origin write checks, storage guards, and uncached API
policy apply. Calendar features require no login, cookie, or prior Home visit.

`static/calendar.js` owns view state, month navigation, course/type filters,
details, and forms; `static/calendar.css` uses shared screen tokens.
`static/calendar-feed.js` is the shared display vocabulary for the Calendar and
Home's Coming up: event types, one display clock ("19:05" and "7:05PM" both show
as "7:05 PM"; the stored text stays in the event dialog and chip tooltips),
groups of events that belong together (same course, date, title and type: one
exam's room charts or DR sections), and the next groups from campus today.

The page opens with one compact bar: the month and its time note, month
navigation, the Month/Agenda switch (Agenda opens on the selected day, today by default, with its own Day/Week/Month range
(requirement, 2026-09-29); the arrows step by that range), the Course and Event type filters and the
color key. Month cells show a group as one chip ("Test 1 · 3 rooms · 7:05 PM",
"Test 1 · DR · 10") that selects its day; single events keep their own chip.
The day panel lists a group under one heading with each event as its own row,
and on campus today adds Next up (the next five groups within 60 days, one
extra GET that reads again at most once a minute or when the month just read
disagrees with it). The header names the next item and the campus date.
Refresh appears only in the error line; the shell's poll repaints in place
(`dom-patch.js`), keeping focus, selects and scroll. `#calendar?add=1` opens
Add event once and drops the query. Keys: T today, P/N previous/next month.

Feed rows for exams, seating charts and DR groups also carry `assignment_id` and
`exam_href` (the assessment's Exams hub: its exam record, else
`#exams/assessment/<assignment>/<term>`). A DR group's `href` opens the DR exam
schedule filtered to its course and exam
(`#accommodations?tab=exams&course=…&exam=…&when=all`); no student field is
in any link. Failed
navigation does not display another month's data as an empty schedule, stale
responses cannot replace a newer month or dialog, and stale edits retain the
user's form. Month/day URLs can be bookmarked. Restart the web service after
frontend edits so its immutable asset snapshot includes the changes.

## Google Calendar sync

The instructor asked (2026-09-28) for the calendar to "sync with my gmail calendar"; asked, they chose both ways, the account
Faculty already sends Response Card mail from (`instructor@tamu.edu`), Faculty's events in their MAIN calendar, and automatic
sync. `google_calendar/` (service, client, `/api/google-calendar`, migration namespace `google_calendar`) is workspace-owned.

- **Connection.** The one Google connection of `response_cards/gmail.py`: the Calendar's Google Calendar dialog asks Google for
  `calendar.events` and `calendar.readonly` on top of Gmail's scopes, for the account already connected (a different account is
  refused), through the same registered callback, which returns to `#calendar?google=1`. Every sign-in sends
  `include_granted_scopes`, so reconnecting Gmail keeps the calendar and vice versa; disconnecting Gmail disconnects both. The
  Google Cloud project must have the Google Calendar API turned on; when it is not, the sync stops with the console link.
- **Faculty → Google.** Every feed event of a ticked type (Exams, Seating charts, DR exams, Deadlines, Your events) over the window
  (campus today − 60 to + 300 days) goes in as the Calendar page groups it: one event per exam, date and time (rooms listed in the
  description), one per exam's DR bookings at one time ("MATH 140 · Test 1 · DR exams (9)", counts per section, never a student),
  one per deadline or custom event. Times are sent as America/Chicago wall-clock (DR as the DR center's time, said in the
  description); a time Faculty cannot read makes an all-day event keeping the text, and a missing end is shown as one hour, said
  so. DR exams, deadlines and all-day events are free time; nobody is invited and Google sends no mail. Each event has a Faculty
  ID derived from the account and the group (a lost answer is resolved by insert → 409 → update, never a duplicate) and a private
  `faculty` property; `google_calendar_pushed` records it. Faculty changes and deletes ONLY those events. An event is written
  again only when its content changes (edits made in Google stay until then; one deleted in Google returns only when it changes
  in Faculty); a group gone inside the window, or of an unticked type anywhere, is deleted; one outside the window is left.
  "Remove Faculty's events" unticks every type, which deletes them all. One event Google refuses is recorded with its reason and
  not retried until it changes.
- **Google → Faculty.** The chosen calendars (the main one unless others are ticked) are read every round over the window and kept
  in `google_calendar_events` (replaced per calendar; without Faculty's own events, cancelled or declined ones, or working-location
  markers). `/api/calendar` adds them as source `google` (read only, campus time, a link to open each in Google) plus a `google`
  summary; the Calendar and Home's Coming up show them. Turning the sync off deletes that copy. The workspace export excludes it.
- **Rounds.** The Inbox worker runs one every 120 s beside its loop (thread `google-calendar-sync`, never under the execution
  lock); Sync now runs one in the web process; both hold one storage lock, and a busy lock is answered `busy`, not 409. A busy,
  unreachable or refusing Google, or a connection that needs the instructor, pauses the rounds with the reason shown; Sync now
  tries at once. No lock is held across a request to Google.

## Checks

`python3 -B manage_checks.py quick` includes the service, API, and course-owner
protection tests. Browser checks use synthetic data, NVMe scratch, and fake DR
bookings:

```sh
python3 -B manage_checks.py browser --browser calendar --browser calendar_resilience --browser calendar_google --keep
```

`tests/test_google_calendar.py` (quick checks) and `tests/browser_calendar_google.py` use a fake Google; nothing reaches Google.

They exercise navigation and source links, all four custom scopes, multi-day and
timed events, editing/deleting, stale versions, distant browser timezones,
the 1440 and 1280 px desktop widths, empty workspaces, failure recovery, delayed
responses, and keyboard navigation across month boundaries.
