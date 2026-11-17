"""Calendar workflows in a real browser using an isolated synthetic catalog.

Run with TMPDIR and the working directory on NVMe. No production data, mail
provider, native document renderer, or retained teaching payload is involved.
"""
import calendar
import re
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from test_exam_workflow import ExamWorkflowTests, HEADERS


def seed():
    fixture = ExamWorkflowTests("test_sittings_and_participants_use_versions_and_preserve_expectations")
    fixture.setUp()
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    day = today.replace(day=15)
    fixture.event = fixture.exams.save_sitting(fixture.event["id"], {
        "version": fixture.event["version"], "label": "Main sitting", "kind": "main",
        "date": day.isoformat(), "time": "2 PM", "location": "Synthetic Hall 105",
    })
    fixture.import_dr(**{
        "Date": datetime.combine(day, datetime.min.time()), "Exam Type": "Exam 1",
        "Start Time": "07:05 PM", "End Time": "08:58 PM",
    })
    response = fixture.client.post("/api/courses", json={
        "name": "Second synthetic course", "code": "MATH 151", "term": "Fall",
        "year": today.year, "sections": [501], "is_current": True,
    }, headers=HEADERS)
    assert response.status_code == 201, response.json
    return fixture, today, day


def listing(fixture, day):
    response = fixture.client.get("/api/calendar", query_string={
        "start": (day.replace(day=1) - timedelta(days=7)).isoformat(),
        "end": (day.replace(day=calendar.monthrange(day.year, day.month)[1]) + timedelta(days=7)).isoformat(),
    })
    assert response.status_code == 200, response.json
    return response.json


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
        "({viewport: innerWidth, document: document.documentElement.scrollWidth})")


def watch_errors(page, errors):
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
    page.add_init_script("""window.calendarCspViolations = [];
      document.addEventListener('securitypolicyviolation', event =>
        window.calendarCspViolations.push([event.violatedDirective, event.blockedURI]));""")


def event_button(page, event):
    return page.locator(f'[data-calendar-event="{event["id"]}"]:visible').first


def create_event(page, *, title, day, scope="workspace", definition_id=None,
                 term_id=None, section_id=None, category="event", end_date=None,
                 time=None, end_time=None):
    page.locator("#primary-action").click()
    form = page.locator("#calendar-event-form")
    expect(form).to_be_visible()
    form.get_by_label("Title", exact=True).fill(title)
    form.get_by_label("Date", exact=True).fill(day.isoformat())
    form.get_by_label("End date", exact=True).fill((end_date or day).isoformat())
    form.get_by_role("combobox", name="Type", exact=True).select_option(category)
    form.get_by_role("combobox", name="Scope", exact=True).select_option(scope)
    if definition_id:
        form.get_by_role("combobox", name="Course", exact=True).select_option(definition_id)
    if term_id:
        form.get_by_role("combobox", name="Term", exact=True).select_option(term_id)
    if section_id:
        form.get_by_role("combobox", name="Section", exact=True).select_option(section_id)
    form.get_by_label("All day", exact=True).set_checked(time is None)
    if time:
        form.get_by_label("Time", exact=True).fill(time)
        form.get_by_label("End time", exact=True).fill(end_time or "")
    else:
        expect(form.get_by_label("Time", exact=True)).to_be_disabled()
        expect(form.get_by_label("End time", exact=True)).to_be_disabled()
    form.get_by_label(re.compile(r"^Location")).fill("Synthetic teaching room")
    form.get_by_label(re.compile(r"^Notes")).fill("A synthetic calendar record for browser verification.")
    with page.expect_response(lambda response: response.request.method == "POST"
                              and response.url.endswith("/api/calendar/events")) as response:
        form.get_by_role("button", name="Save event", exact=True).click()
    assert response.value.status == 201, response.value.text()
    saved = response.value.json()
    expect(form).not_to_be_visible()
    expect(event_button(page, saved)).to_be_visible()
    return saved


def edit_event(page, event):
    page.locator("#calendar-section").get_by_role("button", name="Agenda", exact=True).click()
    page.locator("#calendar-section").get_by_role("button", name="Month", exact=True).last.click()  # Agenda opens on one day; these checks read the month
    event_button(page, event).click()
    page.locator("#dialog").get_by_role("button", name="Edit event", exact=True).click()
    form = page.locator("#calendar-event-form")
    expect(form).to_be_visible()
    return form


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    fixture, today, day = seed()
    feed = listing(fixture, day)
    assert feed["today"] == today.isoformat(), feed
    assert feed["time_zone"] == "America/Chicago", feed
    exam = next(event for event in feed["events"] if event["source"] == "exam")
    dr = next(event for event in feed["events"] if event["source"] == "dr")
    assert dr["time"] == "19:05" and dr["booking_count"] == 1, dr
    other_course = next(course for course in feed["catalog"]["courses"] if course["code"] == "MATH 151")
    definition_id = fixture.term["definition_id"]
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            # A browser a full calendar day away from campus must not shift
            # stored dates or interpret retained wall-clock times as UTC.
            context = browser.new_context(timezone_id="Pacific/Kiritimati", reduced_motion="reduce",
                                          viewport={"width": 1440, "height": 1050})
            page = context.new_page()
            page.set_default_timeout(10000)
            errors = []
            watch_errors(page, errors)
            page.goto(address + "/#calendar")
            root = page.locator("#calendar-section")
            expect(root).to_be_visible()
            expect(page.locator("#calendar-month-title")).to_have_text(today.strftime("%B %Y"))
            expect(event_button(page, exam)).to_be_visible()

            page.get_by_role("button", name="Previous month", exact=True).click()
            previous = today.replace(day=1) - timedelta(days=1)
            expect(page.locator("#calendar-month-title")).to_have_text(previous.strftime("%B %Y"))
            page.get_by_role("button", name="Next month", exact=True).click()
            expect(page.locator("#calendar-month-title")).to_have_text(today.strftime("%B %Y"))
            following = today.replace(day=28) + timedelta(days=4)
            root.get_by_label("Go to month", exact=True).fill(following.strftime("%Y-%m"))
            expect(page.locator("#calendar-month-title")).to_have_text(following.strftime("%B %Y"))
            page.get_by_role("button", name="Today", exact=True).click()
            expect(page.locator("#calendar-month-title")).to_have_text(today.strftime("%B %Y"))

            # Source records link to the existing owner and expose no custom
            # edit/delete path. DR details contain no student identities.
            event_button(page, exam).click()
            dialog = page.locator("#dialog")
            expect(dialog.locator(f'a[href="{exam["href"]}"]')).to_be_visible()
            expect(dialog.get_by_role("button", name="Edit event", exact=True)).to_have_count(0)
            expect(dialog.get_by_role("button", name="Delete event", exact=True)).to_have_count(0)
            dialog.locator(f'a[href="{exam["href"]}"]').click()
            expect(page).to_have_url(address + "/" + exam["href"])
            page.goto(address + "/#calendar")
            event_button(page, dr).click()
            expect(dialog).to_contain_text(re.compile(r"19:05|7:05"))
            expect(dialog).not_to_contain_text("Example, Alice")
            # A DR group opens the DR exam schedule filtered to its course and exam (no student fields in the link).
            expect(dialog.get_by_role("link", name="Open DR exam schedule", exact=True)).to_have_attribute("href", dr["href"])
            assert dr["href"].startswith("#accommodations?tab=exams&course=") and dr["href"].endswith("&when=all"), dr["href"]
            assert "Example" not in dr["href"] and "Alice" not in dr["href"], dr["href"]
            dialog.get_by_role("button", name="Close", exact=True).click()

            workspace = create_event(page, title="Faculty planning window", day=day.replace(day=10),
                                     end_date=day.replace(day=12))
            for number in (10, 11, 12):
                date = day.replace(day=number).isoformat()
                cell = root.locator("td.calendar-day").filter(has=page.locator(f'.calendar-day-number[data-calendar-day="{date}"]'))
                expect(cell.locator(f'[data-calendar-event="{workspace["id"]}"]')).to_have_count(1)
            after_range = day.replace(day=13).isoformat()
            cell = root.locator("td.calendar-day").filter(has=page.locator(f'.calendar-day-number[data-calendar-day="{after_range}"]'))
            expect(cell.locator(f'[data-calendar-event="{workspace["id"]}"]')).to_have_count(0)
            course = create_event(page, title="Course office hours", day=day, scope="course",
                                  definition_id=definition_id, time="00:30", end_time="01:15")
            term = create_event(page, title="Term grading deadline", day=day, scope="term", category="deadline",
                                definition_id=definition_id, term_id=fixture.term["id"])
            section = create_event(page, title="Section review session", day=day, scope="section",
                                   definition_id=definition_id, term_id=fixture.term["id"],
                                   section_id=fixture.section["id"], time="16:00", end_time="17:00")
            other = create_event(page, title="Second course consultation", day=day, scope="course",
                                 definition_id=other_course["id"])
            saved = {event["id"]: event for event in listing(fixture, day)["events"]}
            for event, scope, references in (
                (workspace, "workspace", (None, None, None)),
                (course, "course", (definition_id, None, None)),
                (term, "term", (definition_id, fixture.term["id"], None)),
                (section, "section", (definition_id, fixture.term["id"], fixture.section["id"])),
            ):
                row = saved[event["id"]]
                assert row["scope"] == scope, row
                assert tuple(row[field] for field in ("definition_id", "term_id", "section_id")) == references, row
            assert saved[workspace["id"]]["end_date"] == day.replace(day=12).isoformat()
            assert saved[course["id"]]["time"] == "00:30"

            root.get_by_role("button", name="Agenda", exact=True).click()
            root.get_by_role("button", name="Month", exact=True).last.click()  # Agenda opens on one day; these checks read the month
            expect(root.get_by_role("button", name="Agenda", exact=True)).to_have_attribute("aria-pressed", "true")
            expect(event_button(page, course)).to_be_visible()
            expect(event_button(page, course)).not_to_contain_text("Workspace")
            expect(event_button(page, term)).not_to_contain_text("Workspace")
            expect(event_button(page, workspace)).to_contain_text("Workspace")
            ordered = root.locator(".calendar-agenda [data-calendar-event]").evaluate_all(
                "nodes => nodes.map(node => node.dataset.calendarEvent)")
            assert ordered.index(course["id"]) < ordered.index(exam["id"]) < ordered.index(section["id"]) < ordered.index(dr["id"]), ordered
            root.get_by_label("Course", exact=True).select_option(definition_id)
            expect(event_button(page, course)).to_be_visible()
            expect(page.locator(f'[data-calendar-event="{other["id"]}"]')).to_have_count(0)
            root.get_by_label("Course", exact=True).select_option("")
            expect(event_button(page, other)).to_be_visible()
            root.get_by_label("Event type", exact=True).select_option("deadline")
            expect(event_button(page, term)).to_be_visible()
            expect(page.locator(f'[data-calendar-event="{course["id"]}"]')).to_have_count(0)
            root.get_by_label("Event type", exact=True).select_option("exam")
            expect(event_button(page, exam)).to_be_visible()
            expect(page.locator(f'[data-calendar-event="{term["id"]}"]')).to_have_count(0)
            root.get_by_label("Event type", exact=True).select_option("")

            # A changed retained version wins over an old open browser form.
            form = edit_event(page, course)
            expect(form.get_by_label("Date", exact=True)).to_have_value(day.isoformat())
            expect(form.get_by_label("Time", exact=True)).to_have_value("00:30")
            form.get_by_label("Title", exact=True).fill("Stale overwrite attempt")
            concurrent = fixture.client.put(f'/api/calendar/events/{course["custom_id"]}', json={
                "version": course["version"], "title": "Newer retained office hours",
            }, headers=HEADERS)
            assert concurrent.status_code == 200, concurrent.json
            with page.expect_response(lambda response: response.request.method == "PUT"
                                      and "/api/calendar/events/" in response.url) as stale:
                form.get_by_role("button", name="Save event", exact=True).click()
            assert stale.value.status == 409, stale.value.text()
            expect(form.locator(".form-error")).to_be_visible()
            expect(form.get_by_label("Title", exact=True)).to_have_value("Stale overwrite attempt")
            actual = next(event for event in listing(fixture, day)["events"] if event["id"] == course["id"])
            assert actual["title"] == "Newer retained office hours", actual
            form.get_by_role("button", name="Cancel", exact=True).click()

            form = edit_event(page, section)
            form.get_by_label("Title", exact=True).fill("Section review, updated")
            with page.expect_response(lambda response: response.request.method == "PUT"
                                      and "/api/calendar/events/" in response.url) as changed:
                form.get_by_role("button", name="Save event", exact=True).click()
            assert changed.value.status == 200, changed.value.text()
            section = changed.value.json()
            expect(event_button(page, section)).to_contain_text("Section review, updated")

            # Desktop widths (DESIGN-LANGUAGE: one appearance, desktop only) keep useful event content and every control
            # inside the window in both month and agenda presentations, including a laptop's 1280 px, where the day
            # panel moves under the month.
            expect(page.locator("#toast")).not_to_be_visible()
            for width in (1440, 1280):
                page.set_viewport_size({"width": width, "height": 1050})
                for view in ("Month", "Agenda"):
                    root.locator(f'[data-calendar-mode="{view.lower()}"]').click()
                    expect(root.locator(f'[data-calendar-mode="{view.lower()}"]')).to_have_attribute("aria-pressed", "true")
                    no_overflow(page)
                    assert page.evaluate("calendarCspViolations") == []
                    page.screenshot(path=str(output / f"calendar-{view.lower()}-{width}.png"), full_page=True)
            form = edit_event(page, section)
            no_overflow(page)
            assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
            dialog.screenshot(path=str(output / "calendar-dialog.png"))
            form.get_by_role("button", name="Cancel", exact=True).click()

            event_button(page, section).click()
            dialog.get_by_role("button", name="Delete event", exact=True).click()
            delete = page.locator("#calendar-delete-form")
            expect(delete).to_be_visible()
            with page.expect_response(lambda response: response.request.method == "DELETE"
                                      and "/api/calendar/events/" in response.url) as deleted:
                delete.get_by_role("button", name="Delete event", exact=True).click()
            assert deleted.value.status == 200, deleted.value.text()
            expect(page.locator(f'[data-calendar-event="{section["id"]}"]')).to_have_count(0)
            assert section["id"] not in {event["id"] for event in listing(fixture, day)["events"]}

            # Events that belong together share one chip and one heading (each still opens on its own), today's panel
            # lists what is next, the header names it, and Refresh shows only beside an error.
            soon = today + timedelta(days=2)
            grouped = []
            for room in ("Room A", "Room B"):
                response = fixture.client.post("/api/calendar/events", json={"request_id": str(uuid.uuid4()), "title": "Study group",
                    "date": soon.isoformat(), "all_day": False, "time": "19:05", "location": room}, headers=HEADERS)
                assert response.status_code == 201, response.json
                grouped.append(response.json)
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.goto(address + "/#calendar")
            expect(page.locator("#calendar-month-title")).to_have_text(today.strftime("%B %Y"))
            expect(root.get_by_role("button", name="Refresh", exact=True)).to_have_count(0)
            expect(root.locator(".calendar-legend")).to_contain_text("Your events")
            next_up = root.locator(".calendar-next-up")
            expect(next_up).to_contain_text("Study group")
            expect(next_up).to_contain_text("2 rooms")
            expect(page.locator("#page-subtitle")).to_contain_text("Next: ")
            chip = root.locator(f'.calendar-chip-group[data-calendar-day="{soon.isoformat()}"]')
            expect(chip).to_have_text("Study group · 2 rooms · 7:05 PM")
            expect(root.locator(f'.calendar-chip[data-calendar-event="{grouped[0]["id"]}"]')).to_have_count(0)
            chip.click()
            group = root.locator(".calendar-day-agenda .calendar-group")
            expect(group).to_contain_text("Study group")
            expect(group.locator(".calendar-subevent[data-calendar-event]")).to_have_count(2)
            expect(group.locator(f'[data-calendar-event="{grouped[1]["id"]}"]')).to_contain_text("7:05 PM")
            group.locator(f'[data-calendar-event="{grouped[1]["id"]}"]').click()
            expect(dialog).to_contain_text("19:05")
            dialog.get_by_role("button", name="Close", exact=True).click()

            west = browser.new_context(timezone_id="America/Los_Angeles", viewport={"width": 1440, "height": 1050})
            west_page = west.new_page()
            watch_errors(west_page, errors)
            west_page.goto(address + "/#calendar")
            form = edit_event(west_page, course)
            expect(form.get_by_label("Date", exact=True)).to_have_value(day.isoformat())
            expect(form.get_by_label("Time", exact=True)).to_have_value("00:30")
            assert west_page.evaluate("calendarCspViolations") == []
            assert errors == [], errors
            browser.close()
        print("PASS: Calendar navigation, source details, filters, scoped event CRUD, stale edits, local dates, desktop layout and CSP.")
    finally:
        server.close()
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
