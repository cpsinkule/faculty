"""Exam seating charts in the real browser: creation with DR exclusion, moves, updates, print view.

Run from the project root: python tests/browser_seating.py
"""
import io
import json
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from test_dr_exams import booking, workbook as dr_workbook
from test_rooms import fixture
from test_rosters import csv_bytes
from test_seating import HALL_SPACED, HEADERS, SECTION_507, SECTION_508

ROOM_NOTE = "Two chairs short in the room, location not recorded. Check the room before seating it nearly full."

def seed(app):
    client = app.test_client()
    assert client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                             "sections": [507, 508], "multiple_sections": True}, headers=HEADERS).status_code == 201
    state = client.get("/api/state").json
    group = state["course_groups"][0]
    for number, rows in ((507, SECTION_507), (508, SECTION_508)):
        assert client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster",
                           data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS).status_code == 201
    assert client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1", "title": "Test 1"}, headers=HEADERS).status_code == 201
    assert client.post("/api/rooms", data={"name": "HECC 105", "notes": ROOM_NOTE, "file": (io.BytesIO(fixture("HECC 105")), "HECC 105.xlsx")}, headers=HEADERS).status_code == 201
    content = dr_workbook([booking(), booking(UIN="222222222", Section="508", **{"Last Name": "Example", "First Name": "Carol"})])
    preview = client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None)}, headers=HEADERS)
    assert preview.status_code == 200, preview.json
    submitted = client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None),
                            "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
    assert submitted.status_code == 202, submitted.json
    assert app.extensions["inbox"].process_one()
    # Cards publish to a fake public service and mail goes to a fake relay; nothing leaves the process.
    from response_cards.mail import MailConfig
    from test_response_cards import FakeRemote
    cards = app.extensions["response_cards"]
    cards.remote = FakeRemote()
    cards.mail.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4)
    cards.mail.transport = lambda job: {"mail_id": job["id"], "status": "accepted", "message_id": "provider-1"}
    return client


def check_chart_names(page, title, room, context=None):
    """ASSIGNMENT-WORKSPACE: a chart's route opens in its assignment's shell. The page title is the assignment's and the trail runs
    Home › Assignments › the assignment › its area; the chart stays named: its room leads the status card (a link to the room),
    the seating plan's heading names assignment and room, and the chart keeps its own way back to every chart."""
    expect(page.locator("#page-title")).to_have_text(title)
    if context is not None:
        expect(page.locator("#page-context")).to_have_text(context)
    expect(page.locator("#breadcrumb li")).to_have_text(["Home", "Assignments", title, re.compile(r"\S")])
    scope = page.locator(".seating-workflow-scope a").first
    expect(scope).to_be_visible()
    expect(scope).to_have_text(room)
    expect(page.locator("#seating-chart-title")).to_contain_text(f"{title} · {room}")
    expect(page.locator(".room-breadcrumbs").get_by_role("link", name="All seating charts", exact=True)).to_have_attribute("href", "#seating")


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = seed(app)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(10000)
                errors = []
                move_requests = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: move_requests.append(request.post_data_json)
                        if request.method == "POST" and request.url.endswith("/move") else None)
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                page.goto(address + "/#seating")
                expect(page.locator("#page-title")).to_have_text("Seating charts")
                # No intro card: the header says what the list holds in one line.
                expect(page.locator(".seating-intro")).to_have_count(0)
                expect(page.locator("#seating-count")).to_have_text("0")
                expect(page.locator("#seating-section .empty-state")).to_be_visible()

                # Create a chart: the DR exam name is suggested from the assignment title.
                page.locator("#primary-action").click()
                dialog = page.get_by_role("dialog")
                expect(dialog).to_contain_text("New seating chart")
                expect(dialog.get_by_label("Exam assignment", exact=True)).to_have_value(re.compile(".+"))
                # How seating works sits in the dialog, closed; ready rooms come first in their own group.
                expect(dialog.locator(".seating-create-guide")).not_to_have_attribute("open", "")
                expect(dialog.locator(".seating-create-guide")).to_contain_text("Usable seats only.")
                expect(dialog.get_by_label("Room for Section 507", exact=True).locator("optgroup").first).to_have_attribute("label", "Ready rooms")
                hall = client.get("/api/rooms").json[0]["id"]
                dialog.get_by_label("Room for Section 507", exact=True).select_option(hall)
                # The forecast is one row per room: sections, students, DR bookings, to seat, usable, spaced.
                forecast_row = dialog.locator("#seating-forecast tbody tr").first
                expect(forecast_row.locator("th, td")).to_have_text(["HECC 105", "507", "6", "1", "5", "154", str(HALL_SPACED)])
                dialog.get_by_label("Room for Section 508", exact=True).select_option(hall)
                expect(forecast_row.locator("th, td")).to_have_text(["HECC 105", "507, 508", "9", "2", "7", "154", str(HALL_SPACED)])
                expect(dialog.locator("#seating-forecast caption")).to_contain_text("DR bookings for Test 1 are not seated")
                expect(dialog.locator(".seating-relink")).to_have_count(0)
                dialog.get_by_label("Notes", exact=False).fill("Bring photo ID")
                dialog.get_by_role("button", name="Create seating chart", exact=True).click()
                expect(dialog).not_to_be_visible()
                # ASSIGNMENT-WORKSPACE: the chart opens in its assignment's shell. The title is the assignment's; the context line
                # keeps the chart's course, term and sections, and the room leads the chart's status card (check_chart_names).
                check_chart_names(page, "Test 1", "HECC 105", "MATH 140 · Fall 2026 · Sections 507, 508")
                expect(page.locator("#page-subtitle")).to_be_hidden()
                # The header's one action is the chart's next step; Chart settings is a quiet button in the status card.
                expect(page.locator("#primary-action")).to_have_text("Confirm for printing")
                expect(page.locator("#assignment-workspace [data-assignment-editor] > #seating-section")).to_be_visible()
                expect(page.locator("#seating-next-title")).to_have_text("Review seats, then confirm for printing")
                expect(page.locator(".seating-workflow").get_by_role("button", name="Chart settings", exact=True)).to_be_visible()
                expect(page.locator(".seating-workflow-scope").get_by_role("link", name="HECC 105", exact=True)).to_have_attribute("href", re.compile("#rooms/"))
                expect(page.locator(".seating-workflow .button.primary")).to_have_count(0)
                # ROOMS: what is known about the room (a chair shortage without a location) is on each of its charts.
                expect(page.locator(".seating-room-note .callout-title")).to_have_text("About HECC 105")
                expect(page.locator(".seating-room-note")).to_contain_text(ROOM_NOTE)
                expect(page.locator("#seating-section .button.primary")).to_have_count(0)
                # Actions come in three labelled groups; the header's action is not repeated there.
                groups = page.locator(".seating-actions .seating-action-group h3")
                expect(groups).to_have_text(["Seats", "Print"])
                # The email actions sit with the notices they send, under the student table.
                notices = page.locator(".seating-notices-panel")
                expect(notices.locator('.seating-action-group[data-group="email"] h4')).to_have_text("Email students")
                expect(notices).to_contain_text("Response cards are prepared when you confirm the chart for printing.")
                expect(notices.get_by_role("button", name="Email student charts", exact=True)).to_be_visible()
                # The chart links to its exam hub (no exam record yet: the assessment's hub).
                expect(page.locator(".seating-exam-link a")).to_have_attribute("href", re.compile(r"^#exams/assessment/[^/]+/[^/]+$"))
                expect(page.locator(".seating-exam-link a")).to_have_text("Exam: Test 1")
                expect(page.locator('.seating-actions [data-seating-action="confirm"]')).to_have_count(0)
                expect(page.locator('.seating-action-group[data-group="print"]').get_by_role("button", name="Print chart", exact=True)).to_be_visible()
                # Before any export the Print group leads to the stamped papers (the assignment window at this chart); the room
                # handouts sit on their own row.
                print_group = page.locator('.seating-action-group[data-group="print"]')
                expect(print_group.locator(".seating-print-papers a.link-arrow")).to_have_text("Print exams for this room")
                expect(print_group.locator(".seating-action-row").filter(has_text="Room handouts").get_by_role("button")).to_have_text(["Print room roster"])
                expect(page.locator('.seating-action-group[data-group="seats"] .danger-button')).to_have_text("Regenerate")
                expect(page.locator("#seating-count")).to_have_text("1")
                grid = page.locator(".seating-view .seat-grid")
                expect(grid.locator(".seat.seated")).to_have_count(7)
                expect(page.locator(".room-stat").first).to_have_text("7seated")
                expect(page.locator(".room-stat.left strong")).to_have_text("2")
                table = page.locator(".seating-table tbody tr")
                expect(table).to_have_count(9)
                expect(page.locator(".seating-status.dr_testing")).to_have_count(2)
                expect(page.locator(".seating-students")).to_contain_text("Example, Alice")
                # DR names are no longer repeated as prose: the DR testing count filters the table instead.
                expect(page.locator(".seating-students")).not_to_contain_text("are not seated:")
                expect(page.locator(".seating-student-filters button")).to_have_text([re.compile("^All 9$"), re.compile("^DR testing 2$"), re.compile("^No seat 0$")])
                page.locator(".room-stat.left").click()
                expect(page.locator(".room-stat.left")).to_have_attribute("aria-pressed", "true")
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(2)
                expect(page.locator('.seating-student-filters [data-filter="dr"]')).to_have_attribute("aria-pressed", "true")
                expect(grid.locator(".seat-highlight")).to_have_count(0)
                page.locator(".room-stat.left").click()
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(9)
                page.locator('.seating-student-filters [data-filter="unseated"]').click()
                expect(page.locator(".seating-filter-empty")).to_be_visible()
                page.locator("[data-clear-student-filter]").click()
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(9)
                expect(page.locator('.seating-student-filters [data-filter="all"]')).to_have_attribute("aria-pressed", "true")
                # The legend lists only states on the chart.
                expect(page.locator(".seat-legend > span")).to_have_text(["Seated", "Free", "Broken", "Blocked"])
                expect(page.locator(".room-source")).to_contain_text("DR exam bookings for Test 1: 2 students not seated")
                expect(page.locator(".room-source")).to_contain_text("Every seated student has an empty seat on either side.")
                expect(page.locator(".room-stat").nth(4)).to_have_text("7with an empty seat beside")
                expect(grid.locator(".seat.seated.crowded")).to_have_count(0)
                assert grid.locator(".seat.seated", has_text="Example, Alice").count() == 0
                expect(page.locator(".seating-history")).to_contain_text("Generated: 7 seated at random with an empty seat beside each, 2 left out for DR testing.")
                page.screenshot(path=str(output / "seating-desktop.png"), full_page=True)

                # Selecting a source is local: a source click, broken target,
                # Escape, Cancel, and leaving the chart never write a move.
                chart_url = page.url
                chart_id = chart_url.rsplit("/", 1)[-1]
                chart_route = f"/api/seating/{chart_id}"
                current = client.get(chart_route).json
                unusable = next(seat for seat in current["room"]["seats"] if not seat["student"])
                response = client.post(f"/api/rooms/{hall}/seats", json={
                    "version": current["room"]["version"], "seat_ids": [unusable["id"]], "broken": True}, headers=HEADERS)
                assert response.status_code == 200, response.json
                page.reload()
                expect(grid.locator(".seat.broken")).to_have_count(1)
                current = client.get(chart_route).json
                bob = next(student for student in current["students"] if student["name"] == "Example, Bob")
                bob_seat = grid.locator(".seat.seated", has_text="Example, Bob")
                old_label = bob_seat.locator(".seat-tag").inner_text()
                bob_seat.click()
                selection = page.locator(".seating-move-selection")
                expect(selection).to_be_visible()
                expect(selection).to_contain_text("Example, Bob")
                expect(grid.locator(".seat.move-source")).to_have_count(1)
                expect(bob_seat).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#seating-move")).to_have_count(0)
                bob_seat.click()
                expect(selection).to_be_hidden()
                expect(grid.locator(".seat.move-source")).to_have_count(0)
                bob_seat.click()
                expect(bob_seat).to_have_attribute("aria-pressed", "true")
                grid.locator(".seat.broken").first.click()
                expect(bob_seat).to_have_attribute("aria-pressed", "true")
                expect(selection).to_be_visible()
                page.keyboard.press("Escape")
                expect(selection).to_be_hidden()
                expect(grid.locator(".seat.move-source")).to_have_count(0)
                expect(bob_seat).to_be_focused()
                page.locator(f'[data-move="{bob["enrollment_id"]}"]').click()
                expect(bob_seat).to_have_attribute("aria-pressed", "true")
                selection.locator("[data-cancel-move]").click()
                expect(selection).to_be_hidden()
                bob_seat.click()
                page.get_by_role("link", name="All seating charts", exact=True).click()
                page.locator(".seating-card").click()
                expect(grid).to_be_visible()
                expect(selection).to_be_hidden()
                expect(grid.locator(".seat.move-source")).to_have_count(0)
                assert move_requests == []
                assert client.get(chart_route).json["version"] == current["version"]

                # The next chart click moves directly. Repeated destination
                # clicks while its response is outstanding produce one write.
                free = next(seat for seat in current["room"]["seats"]
                            if not seat["student"] and not seat["broken"] and not seat["blocked"])
                new_label = free["label"]
                target = grid.locator(f'[data-seat="{free["id"]}"]')
                pending_moves = []
                page.route("**" + chart_route + "/move", lambda route: pending_moves.append(route))
                bob_seat.click()
                with page.expect_request(lambda request: request.method == "POST" and request.url.endswith(chart_route + "/move")):
                    target.click()
                target.dispatch_event("click")
                target.dispatch_event("click")
                assert len(pending_moves) == 1
                assert move_requests == [{"version": current["version"], "enrollment_id": bob["enrollment_id"], "seat_id": free["id"]}]
                response = pending_moves[0].fetch()
                assert response.status == 200, response.text()
                pending_moves[0].fulfill(response=response)
                page.unroute("**" + chart_route + "/move")
                expect(selection).to_be_hidden()
                expect(grid.locator(".seat.seated", has_text="Example, Bob").locator(".seat-tag")).to_have_text(new_label)
                expect(grid.locator(f'[data-seat="{bob["seat_id"]}"]')).not_to_have_class(re.compile(r"\bseated\b"))
                assert new_label != old_label
                expect(page.locator(".seating-history")).to_contain_text(f"Moved a student to {new_label}.")

                # Clicking an occupied destination swaps both occupants without
                # opening that student's card/details or a destination dialog.
                current = client.get(chart_route).json
                other = next(student for student in current["students"]
                             if student["seat_id"] and student["enrollment_id"] != bob["enrollment_id"])
                bob_seat.click()
                grid.locator(f'[data-seat="{other["seat_id"]}"]').click()
                expect(grid.locator(".seat.seated", has_text="Example, Bob").locator(".seat-tag")).to_have_text(other["seat"])
                expect(grid.locator(f'[data-seat="{free["id"]}"]')).to_contain_text(other["name"])
                expect(selection).to_be_hidden()
                expect(page.locator("#seating-move")).to_have_count(0)
                assert len(move_requests) == 2
                assert client.get(chart_route).json["version"] == current["version"] + 1
                new_label = other["seat"]

                # The room changes underneath: Bob's seat gets blocked, the chart flags it, Update fixes it.
                rooms = client.get("/api/rooms").json
                room = client.get(f"/api/rooms/{rooms[0]['id']}").json
                seat = next(item for item in room["seats"] if item["label"] == new_label)
                assert client.post(f"/api/rooms/{room['id']}/seats", json={"version": room["version"], "seat_ids": [seat["id"]], "blocked": True}, headers=HEADERS).status_code == 200
                page.reload()
                expect(page.locator(".seating-issues")).to_contain_text("What the roster check found")
                expect(page.locator(".seating-issues")).to_contain_text(f"Example, Bob is seated at {new_label}, which is now blocked.")
                expect(page.locator(".room-source")).to_contain_text("Roster check at generation")
                expect(page.locator(".room-source")).to_contain_text("passed — 9 roster students, 2 with DR bookings kept out, everyone else seated.")
                page.get_by_role("link", name="All seating charts", exact=True).click()
                expect(page.locator(".seating-card .seating-flag")).to_have_text("1 room needs attention")
                page.locator(".seating-card").click()
                page.locator(".seating-issues").get_by_role("button", name="Update seating", exact=True).click()
                expect(page.locator(".seating-issues")).to_have_count(0)
                expect(grid.locator(".seat.seated")).to_have_count(7)
                expect(grid.locator(".seat.seated", has_text="Example, Bob").locator(".seat-tag")).not_to_have_text(new_label)
                expect(page.locator(".seating-history")).to_contain_text("Updated: 6 kept their seats, 1 newly seated, 1 removed (seat is no longer usable). Roster check passed.")
                # Charts generated before the roster check existed have no recorded result and must still open.
                import sqlite3
                with sqlite3.connect(app.extensions["rosters"].path) as legacy:
                    legacy.execute("UPDATE seating_charts SET check_json = '{}', checked_at = ''")
                page.reload()
                expect(page.locator(".room-source")).to_contain_text("Roster check at generation: not recorded — Regenerate or Update seating to record one.")
                expect(grid.locator(".seat.seated")).to_have_count(7)
                expect(page.locator(".seating-history")).to_contain_text("2 left out for DR testing. Roster check passed.")

                # Searching the student table and printing hide the right things.
                page.get_by_label("Find a student", exact=True).fill("carol")
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(1)
                # Narrowing to one seated student finds their seat on the plan.
                page.get_by_label("Find a student", exact=True).fill("example, bob")
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(1)
                expect(grid.locator(".seat.seat-found")).to_contain_text("Example, Bob")
                page.get_by_label("Find a student", exact=True).fill("")
                page.emulate_media(media="print")
                expect(page.locator(".sidebar")).to_be_hidden()
                expect(page.locator(".seating-actions")).to_be_hidden()
                expect(page.locator(".seating-room-note")).to_be_hidden()
                expect(grid).to_be_visible()
                page.emulate_media(media="screen")

                # Regenerate reshuffles but keeps the DR exclusions.
                page.get_by_role("button", name="Regenerate", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="Regenerate", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(grid.locator(".seat.seated")).to_have_count(7)
                expect(page.locator(".room-stat.left strong")).to_have_text("2")

                # At the narrowest desktop width the chart scrolls inside its panel, never the page (DESIGN-LANGUAGE:
                # desktop only; there is no phone layout).
                page.set_viewport_size({"width": 1280, "height": 900})
                page.goto(page.url)
                expect(grid.locator(".seat.seated")).to_have_count(7)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "seating-1280.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})

                # The list shows the exam as one tile (SEATING "One tile per exam"), and settings can remove its chart.
                page.get_by_role("link", name="All seating charts", exact=True).click()
                card = page.locator(".seating-card")
                expect(card).to_have_count(1)
                expect(card).to_have_class(re.compile(r"\bseating-exam-card\b"))
                expect(card.locator("h3")).to_have_text("Test 1")
                expect(card).to_contain_text("MATH 140 · Fall 2026")
                expect(card).to_contain_text("HECC 105 · Sections 507, 508")
                expect(card).to_contain_text("7 seated")
                expect(card).to_contain_text("2 DR testing")
                expect(card.locator(".seating-card-status")).to_have_text("Not confirmed")
                # One room with every section seated: the tile opens that room's chart itself.
                expect(card).to_have_attribute("href", re.compile(r"^#seating/(?!exam/)[^/]+$"))
                expect(page.locator("#page-subtitle")).to_have_text("1 room chart")
                # The room lists the exam charts that use it (in a second tab, so this page keeps one room plan in its DOM).
                room_page = browser.new_page(viewport={"width": 1440, "height": 1000})
                room_page.goto(address + f"/#rooms/{hall}")
                expect(room_page.locator(".room-used-by")).to_contain_text("Test 1 · MATH 140 · Fall 2026")
                room_page.locator(".room-used-by a").click()
                check_chart_names(room_page, "Test 1", "HECC 105", "MATH 140 · Fall 2026 · Sections 507, 508")
                room_page.close()
                expect(page.get_by_role("button", name="Add rooms")).to_have_count(0)
                card.click()
                # Before any print job exists, removal is a plain yes/no.
                page.locator(".seating-workflow").get_by_role("button", name="Chart settings", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("Seating chart settings")
                page.get_by_role("dialog").get_by_role("button", name="Remove chart", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("Remove this seating chart?")
                expect(page.get_by_role("dialog").get_by_label("Type delete to confirm", exact=True)).to_have_count(0)
                expect(page.get_by_role("dialog").get_by_role("button", name="Remove chart", exact=True)).to_be_enabled()
                page.get_by_role("dialog").get_by_role("button", name="Keep chart", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                # Confirm and export: no PDF is attached, so the stamps-only path is offered.
                page.get_by_role("button", name="Confirm for printing", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog).to_contain_text("No exam PDF is attached")
                expect(dialog).to_contain_text("Section 507: 5 seated copies")
                expect(dialog).to_contain_text("Section 508: 2 seated copies")
                dialog.get_by_label("Labeled pages per paper exam", exact=True).fill("3")
                # DOCUMENTS "SCANTRON LABELS": an extra label per student is a choice of its own, off by default.
                expect(dialog.get_by_label("Also print a Scantron label for each student", exact=False)).not_to_be_checked()
                # The response card step is a ticked-by-default option; unticking it exports without the review window.
                cards_option = dialog.get_by_label("Prepare response cards and email each student their link", exact=False)
                expect(cards_option).to_be_checked()
                expect(dialog.get_by_role("button", name="Export stamps only, then response cards", exact=True)).to_be_visible()
                # The chain says where it is, and what confirming does, in three lines.
                expect(dialog.locator("[data-dialog-step]")).to_have_text("Step 1 of 2 · Print jobs")
                expect(dialog.locator(".seating-what-happens li")).to_have_count(3)
                cards_option.uncheck()
                expect(dialog.locator("[data-dialog-step]")).to_have_text("Step 1 of 1 · Print jobs")
                expect(dialog.get_by_role("button", name="Export stamps only", exact=True)).to_be_visible()
                cards_option.check()
                dialog.get_by_role("button", name="Export stamps only, then response cards", exact=True).click()
                # The review window: two cards side by side, editable, with the exam time to fill in.
                expect(dialog).to_contain_text("Response cards for this exam")
                expect(dialog.locator(".dialog-step")).to_have_text("Step 2 of 2 · Response cards")
                expect(dialog).to_contain_text("Students taking the exam in HECC 105 (7)")
                expect(dialog).to_contain_text("Disability Resource Center students (2)")
                expect(dialog.get_by_label("Seat card statement", exact=True)).to_have_value(re.compile("assigned seat is \\{\\{seat.number\\}\\}"))
                expect(dialog.get_by_label("DR card statement", exact=True)).to_have_value(re.compile("You MUST take your exam as scheduled with the Disability Resource Center"))
                expect(dialog.get_by_label("Seat card item 1 expected answer", exact=True)).to_have_value("{{exam.room}}")
                expect(dialog.get_by_label("DR card item 1 prompt", exact=True)).to_have_value("Confirm the date of your scheduled DR exam:")
                expect(dialog).to_contain_text("Always last:")
                seat_column = dialog.locator('[data-column="seat"]')
                seat_column.get_by_role("button", name="Add response checkbox", exact=True).click()
                expect(seat_column.locator(".sc-field")).to_have_count(3)
                seat_column.get_by_label("Seat card item 3 prompt", exact=True).fill("I will bring a pencil.")
                seat_column.get_by_role("button", name="Add response box", exact=True).click()
                seat_column.get_by_label("Seat card item 4 prompt", exact=True).fill("Which calculator will you bring?")
                seat_column.get_by_label("Seat card item 4 check", exact=True).select_option("none")
                seat_column.get_by_label("Remove Seat card item 4", exact=True).click()
                expect(seat_column.locator(".sc-field")).to_have_count(3)
                expect(dialog.get_by_label("Publish the cards and email each student their link now", exact=False)).to_be_checked()
                expect(dialog).to_contain_text("IMPORTANT: Information about your upcoming Test 1 for MATH 140")
                # The date and time are required by the form itself before anything is sent.
                dialog.get_by_role("button", name="Confirm, export, and prepare cards", exact=True).click()
                expect(dialog).to_be_visible()
                assert dialog.get_by_label("Exam date", exact=True).evaluate("el => el.validity.valueMissing")
                dialog.get_by_label("Exam date", exact=True).fill("2026-09-25")
                dialog.get_by_label("Exam time", exact=True).fill("7:00 PM")
                # The chart changes while this window is open (another device moves a student away and back): the submit says so, the
                # chart shows its current seats, and nothing is confirmed until the instructor submits again.
                live = client.get(chart_route).json
                mover = next(student for student in live["students"] if student["seat_id"])
                spare = next(seat for seat in live["room"]["seats"] if not seat["student"] and not seat["broken"] and not seat["blocked"])
                for seat_id in (spare["id"], mover["seat_id"]):
                    moved = client.post(chart_route + "/move", json={"version": client.get(chart_route).json["version"],
                                        "enrollment_id": mover["enrollment_id"], "seat_id": seat_id}, headers=HEADERS)
                    assert moved.status_code == 200, moved.json
                dialog.get_by_role("button", name="Confirm, export, and prepare cards", exact=True).click()
                expect(dialog).to_contain_text("The seating chart changed while this window was open.")
                assert not client.get(chart_route).json["confirmed_at"]  # Unconfirmed charts report it empty.
                dialog.get_by_role("button", name="Confirm, export, and prepare cards", exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(page.locator("#toast")).to_contain_text("Seating chart confirmed. 2 print jobs queued for stamps only. 7 seat notices and 2 DR notices prepared and emailed.")
                expect(page.locator(".seating-cards")).to_contain_text("Seat assignment notices · 7 cards · 7 published")
                expect(page.locator(".seating-cards")).to_contain_text("DR testing notices · 2 cards · 2 published")
                expect(page.locator(".seating-cards")).to_contain_text("Published and emailed to each student.")
                expect(page.locator(".seating-cards").get_by_role("link", name="Open in Response Cards")).to_have_count(2)
                # Every card is pending until the students respond; the overlays and table show it.
                expect(page.locator(".seat.card-pending")).to_have_count(7)
                expect(page.locator(".room-stat.card-pending")).to_contain_text("9")
                expect(page.locator(".seat-legend")).to_contain_text("Response card pending")
                expect(page.locator(".seating-table")).to_contain_text("Response card")
                expect(page.locator(".seating-table .seating-card-state.pending")).to_have_count(9)
                expect(page.locator(".seating-table .seating-card")).to_have_count(0)
                # The pending count filters the table and outlines those students' seats.
                page.locator(".room-stat.card-pending").click()
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(9)
                expect(grid.locator(".seat.seat-highlight")).to_have_count(7)
                page.locator('.seating-student-filters [data-filter="all"]').click()
                expect(grid.locator(".seat.seat-highlight")).to_have_count(0)
                cards = app.extensions["response_cards"]
                while cards.mail.process_one():
                    pass
                chart_id = client.get("/api/seating").json[0]["id"]
                seat_issue = cards.get_issue(client.get(f"/api/seating/{chart_id}").json["response_cards"]["seat"]["issue_id"])
                by_name = {item["student_name"]: item for item in seat_issue["recipients"]}
                ok = {"matched": True, "on_mismatch": "BLOCK_SUBMISSION"}
                cards.remote.event(by_name["Example, Bob"]["id"], "verified", submission={"id": "sub-1", "request_id": "r-1", "status": "verified", "submitted_at": "2026-09-15T14:05:00.000Z",
                    "answers": {"confirm_room": "HECC 105", "confirm_seat": "x", "custom_1": True, "confirm_uin": "[UIN confirmed]"}, "verification": {"confirm_room": ok, "confirm_seat": ok, "confirm_uin": ok}})
                cards.remote.event(by_name["Student, 3"]["id"], "needs_review", submission={"id": "sub-2", "request_id": "r-2", "status": "needs_review", "submitted_at": "2026-09-15T15:00:00.000Z",
                    "answers": {"confirm_room": "HECC 106", "confirm_seat": "x", "custom_1": True, "confirm_uin": "[UIN confirmed]"}, "verification": {"confirm_room": {"matched": False, "on_mismatch": "ALLOW_AND_FLAG"}, "confirm_seat": ok, "confirm_uin": ok}})
                cards.sync()
                page.reload()
                expect(page.locator(".seat.card-complete")).to_have_count(1)
                expect(page.locator(".seat.card-issue")).to_have_count(1)
                expect(page.locator(".seat.card-pending")).to_have_count(5)
                expect(page.locator(".room-stat.card-complete")).to_contain_text("1")
                expect(page.locator(".seating-cards")).to_contain_text("7 emailed · 2 responded · 1 flagged for review")
                # A flagged count filters the student table to the card issues.
                page.locator(".seating-cards .seating-flagged-filter").click()
                expect(page.locator('.seating-student-filters [data-filter="issue"]')).to_have_attribute("aria-pressed", "true")
                expect(page.locator(".seating-table tbody tr:visible")).to_have_count(1)
                page.locator('.seating-student-filters [data-filter="all"]').click()
                # A red tile expands to show what went wrong; green and yellow just say completed or pending.
                # The student table names the specific problem and opens the same details for anyone, DR students included.
                expect(page.locator(".seating-table")).to_contain_text("records show “HECC 105”")
                page.locator(".seating-table").get_by_role("button", name="Response card details for Student, 3", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("Answers need review")
                expect(page.get_by_role("dialog")).to_contain_text("Please confirm your building and room number as shown above: answered “HECC 106”; records show “HECC 105”.")
                page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
                page.locator(".seat.card-issue").click()
                popover = page.locator(".seat-popover")
                expect(popover).to_contain_text("Student, 3")
                expect(popover).to_contain_text("Answers need review")
                expect(popover).to_contain_text("Please confirm your building and room number as shown above: answered “HECC 106”; records show “HECC 105”.")
                page.keyboard.press("Escape")
                expect(popover).to_have_count(0)
                page.locator(".seat.card-complete").click()
                expect(page.locator(".seat-popover")).to_contain_text("Completed response card")
                expect(page.locator(".seat-popover")).to_contain_text("Confirmation email queued.")
                page.locator(".seat-popover").get_by_role("button", name="Move or swap", exact=True).click()
                expect(page.locator(".seat-popover")).to_have_count(0)
                expect(page.locator(".seating-move-selection")).to_contain_text("Example, Bob")
                expect(page.locator(".seat.card-complete.move-source")).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#seating-move")).to_have_count(0)
                page.locator("[data-cancel-move]").click()
                expect(page.locator(".seat.move-source")).to_have_count(0)
                page.locator(".seat.card-pending").first.click()
                expect(page.locator(".seat-popover")).to_contain_text("Pending response card")
                # A pending seat offers the card link to hand over, and a resend.
                expect(page.locator(".seat-popover [data-pop-link-text]")).to_contain_text("https://respond.example.test/r/")
                expect(page.locator(".seat-popover").get_by_role("button", name="Copy", exact=True)).to_be_enabled()
                expect(page.locator(".seat-popover").get_by_role("button", name="Resend email", exact=True)).to_have_count(1)
                page.locator(".seat-popover").get_by_role("button", name="Close", exact=True).click()
                expect(page.locator(".seat-popover")).to_have_count(0)
                expect(page.locator(".room-source")).to_contain_text("Confirmed for printing")
                expect(page.locator(".room-source")).to_contain_text("(stamps only)")
                jobs = page.locator(".seating-print-jobs")
                expect(jobs.locator(".seating-export-head")).to_contain_text("version Stamps only · 3 pages · seat numbers beside stamps")
                expect(jobs).to_contain_text("Print either the whole-room files or the section files, never both")
                expect(jobs.locator(".seating-export-room")).to_contain_text("Waiting for every section")
                expect(jobs.locator('.seating-export-section[data-section="507"]')).to_contain_text("Queued · seat numbers beside stamps")
                expect(jobs.locator('.seating-export-section[data-section="507"] td').nth(1)).to_have_text("5")
                expect(jobs.locator('.seating-export-section[data-section="508"] td').nth(1)).to_have_text("2")
                # Files come from the shared print-files renderer; none are offered until the jobs are ready.
                expect(jobs.locator(".seating-export-table thead th").nth(3)).to_have_text("Download")
                expect(jobs.locator(".seating-export-files")).to_have_count(3)
                expect(jobs.locator(".seating-export-files [data-print-files]")).to_have_count(0)
                # Once every section's job is ready, the room row offers the whole room in seat order and each section its own
                # files, named with the app's one label-media vocabulary. (Only the catalog says ready: no file is downloaded.)
                import sqlite3
                with sqlite3.connect(app.extensions["rosters"].path) as db:
                    db.execute("""UPDATE print_batches SET status = 'ready', output_path = 'fixture/print.pdf', output_sha256 = ?, manifest_path = 'fixture/manifest.json',
                        manifest_sha256 = ? WHERE seating_chart_id = ?""", ("c" * 64, "d" * 64, chart_id))
                page.reload()
                room_files = jobs.locator(".seating-export-room [data-print-files]")
                expect(room_files).to_have_count(1)
                expect(room_files.locator("a").first).to_have_attribute("href", re.compile(rf"/api/seating/{chart_id}/room-export/labels\?media=dk-1201&"))
                expect(room_files.locator("a").first).to_have_attribute("title", "Room labels · Brother QL-800 · 29 × 90 mm (DK-1201)")
                # A room of two sections names both jobs in one file route: the click opens the app's file viewer (it once did nothing,
                # print-1). The fixture's files are not real, so the viewer says so; that it opened is what is checked.
                expect(room_files.locator("a").first).to_have_attribute("href", re.compile(r"batches=[^&,]+%2C[^&,]+$"))
                room_files.locator("a").first.click()
                viewer = page.locator("dialog.file-viewer[open]")
                expect(viewer).to_be_visible()
                viewer.get_by_role("button", name="Close the viewer", exact=True).click()
                expect(viewer).to_have_count(0)
                expect(jobs.locator(".seating-export-section [data-print-files]")).to_have_count(2)
                expect(jobs.locator(".seating-earlier-exports")).to_have_count(0)
                # The ready export's room files lead the Print group (the same files as the room row below), with a jump to them all;
                # the assignment window stays one link away beside Export again.
                papers = page.locator('.seating-action-group[data-group="print"] .seating-print-papers')
                expect(papers.locator(".seating-action-label")).to_have_text("Stamp labels for this room")
                expect(papers.locator("[data-print-files] a").first).to_have_attribute("href", re.compile(rf"/api/seating/{chart_id}/room-export/labels\?media=dk-1201&"))
                papers.get_by_role("button", name="All print files ↓", exact=True).click()
                expect(jobs).to_be_focused()
                # The two copies of the room files keep their own "Other formats" menus: no id is repeated on the page, and the room
                # row's menu opens beside it (a shared popover id opened the Print group's menu, far above the row).
                duplicates = page.evaluate("""() => { const seen = {}; document.querySelectorAll('[id]').forEach((el) => { seen[el.id] = (seen[el.id] || 0) + 1; });
                    return Object.entries(seen).filter(([, count]) => count > 1); }""")
                assert duplicates == [], duplicates
                jobs.locator(".seating-export-room .action-menu-trigger").click()
                room_menu = jobs.locator(".seating-export-room .action-menu-list")
                expect(room_menu).to_be_visible()
                assert room_menu.evaluate("(menu) => menu.matches(':popover-open')")
                box = room_menu.bounding_box()
                assert box and 0 <= box["y"] and box["y"] + box["height"] <= page.viewport_size["height"], box
                expect(papers.locator(".action-menu-list")).to_be_hidden()
                page.keyboard.press("Escape")
                expect(room_menu).to_be_hidden()
                papers.locator(".action-menu-trigger").click()
                expect(papers.locator(".action-menu-list")).to_be_visible()
                page.keyboard.press("Escape")
                expect(papers.locator(".action-menu-list")).to_be_hidden()
                expect(page.locator(".seating-action-reexport a.link-arrow")).to_have_text("Print exams for this room")
                expect(page.get_by_role("button", name="Export again as new print jobs", exact=True)).to_be_visible()
                expect(page.locator(".seating-action-reexport")).to_contain_text("New stamps for every copy")
                # The exam date reads as a campus date; after it the chart says the exam was held.
                expect(page.locator(".seating-exam-date")).to_contain_text("Fri, Sep 25 · 7:00 PM")
                expect(page.locator(".seating-cards")).to_contain_text("for Fri, Sep 25 at 7:00 PM")
                import datetime
                if datetime.date.today() > datetime.date(2026, 9, 25):
                    expect(page.locator("#seating-next-title")).to_have_text("Exam held Fri, Sep 25")
                    expect(page.locator("#primary-action")).to_have_text("Print room roster")
                # Regenerating a confirmed chart says what it changes before anything happens.
                page.get_by_role("button", name="Regenerate", exact=True).click()
                expect(page.get_by_role("dialog").locator(".seating-regenerate-impact")).to_contain_text("Up to 7 response cards will be revoked")
                expect(page.get_by_role("dialog").locator(".seating-regenerate-impact")).to_contain_text("2 print jobs will no longer match")
                page.get_by_role("dialog").get_by_role("button", name="Keep current seats", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator(".seating-history")).to_contain_text("Confirmed and exported to printing (stamps only): Section 507 · 5 copies, Section 508 · 2 copies.")
                # Unresolved issues are flagged before exporting again.
                rooms = client.get("/api/rooms").json
                room = client.get(f"/api/rooms/{rooms[0]['id']}").json
                seat = next(item for item in client.get(f"/api/seating/{client.get('/api/seating').json[0]['id']}").json["room"]["seats"] if item["student"])
                assert client.post(f"/api/rooms/{room['id']}/seats", json={"version": room["version"], "seat_ids": [seat["id"]], "blocked": True}, headers=HEADERS).status_code == 200
                page.reload()
                page.get_by_role("button", name="Export again as new print jobs", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("The roster check found 1 issue.")
                page.get_by_role("dialog").get_by_role("button", name="Go back and resolve", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                page.locator(".seating-issues").get_by_role("button", name="Update seating", exact=True).click()
                expect(page.locator(".seating-issues")).to_have_count(0)
                # Exporting again says that it makes a second set of stamped paper beside the first.
                page.get_by_role("button", name="Export again as new print jobs", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("This creates new print jobs.")
                expect(page.get_by_role("dialog")).to_contain_text("The 2 earlier print jobs from this chart stay valid.")
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                # Removal now needs typed confirmation.
                page.reload()
                expect(page.locator(".room-source")).to_contain_text("2 print jobs with page stamps exist for this exam")
                expect(page.locator(".seating-print-jobs .seating-earlier-exports")).to_have_count(0)
                # Leaving a chart while it is still being read, and coming back before that read ends, reads it once more and draws and
                # names it; the shell's own renders on the way join the read under way instead of reading the chart again.
                chart_address = page.url.split("#", 1)[1]
                held = []
                chart_read = re.compile(r".*/api/seating/[^/?]+(\?.*)?$")
                page.route(chart_read, lambda route: held.append(route))
                page.evaluate("location.hash = '#seating'")
                expect(page.locator("#page-title")).to_have_text("Seating charts")
                page.evaluate(f"location.hash = '#{chart_address}'")
                while not held:
                    page.wait_for_timeout(50)
                page.evaluate("location.hash = '#rooms'")
                expect(page.locator("#rooms-section")).to_be_visible()
                page.evaluate(f"location.hash = '#{chart_address}'")
                while len(held) < 2:
                    page.wait_for_timeout(50)
                page.wait_for_timeout(500)
                for route in held:
                    route.continue_()
                check_chart_names(page, "Test 1", "HECC 105", "MATH 140 · Fall 2026 · Sections 507, 508")
                expect(page.locator(".room-source")).to_contain_text("2 print jobs with page stamps exist for this exam")
                assert len(held) == 2, [route.request.url for route in held]
                page.unroute(chart_read)
                page.locator(".seating-workflow").get_by_role("button", name="Chart settings", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="Remove chart", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("2 print jobs with Data Matrix page stamps already exist for Test 1 this term.")
                remove = page.get_by_role("dialog").get_by_role("button", name="Remove chart", exact=True)
                expect(remove).to_be_disabled()
                page.get_by_role("dialog").get_by_label("Type delete to confirm", exact=True).fill("del")
                expect(remove).to_be_disabled()
                page.get_by_role("dialog").get_by_label("Type delete to confirm", exact=True).fill("delete")
                expect(remove).to_be_enabled()
                remove.click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator("#page-title")).to_have_text("Seating charts")
                expect(page.locator("#seating-count")).to_have_text("0")
                browser.close()
                assert not errors, errors
        finally:
            server.close()
    print("Seating chart browser checks passed.")


if __name__ == "__main__":
    main()
