"""Mixing sections across rooms from the New seating chart window (SEATING "mixed rooms").

Run with NVMe TMPDIR and a scratch working directory. Uses synthetic roster data;
no email transport runs. Ticks two rooms and both sections, reads the forecast's
shares, creates the mix, and checks every student is seated once across the rooms.
The room list: the room an earlier chart used comes first and says for which exam,
Find a room narrows the list without unticking anything, and the chosen rooms and
their seats are totalled under it.
"""
import io
import os
import re
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from test_rooms import chart_bytes
from test_seating import HEADERS, SeatingTests


def main():
    fixture = SeatingTests("test_options_report_students_and_linked_dr_bookings")
    fixture.setUp()
    # Tiny has seated Quiz 3 before, and eight more rooms make the list long enough for its Find box.
    used = fixture.create(assignment_id=fixture.quiz["id"], section_ids=[fixture.sections[508]["id"]], room_id=fixture.small["id"])
    assert used.status_code == 201, used.json
    for number in range(101, 109):
        made = fixture.client.post("/api/rooms", data={"name": f"BLOC {number}", "file": (io.BytesIO(chart_bytes({"A1": "A1", "B1": "A2", "C1": "A3"})), "room.xlsx")}, headers=HEADERS)
        assert made.status_code == 201, made.json
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address + "/#seating")
            page.locator("#primary-action").click()
            dialog = page.locator("#seating-create")
            dialog.get_by_label("Exam assignment", exact=True).select_option(fixture.assignment["id"])
            # A room used before leads each section's menu too.
            expect(dialog.get_by_label("Room for Section 507", exact=True).locator("optgroup").first).to_have_attribute("label", "Used before")
            expect(dialog.get_by_label("Room for Section 507", exact=True).locator("optgroup").first.locator("option")).to_contain_text(["Tiny — 3 usable · 2 spaced · Quiz 3"])
            dialog.get_by_label("Mix sections across rooms").check()
            expect(dialog.get_by_label("Room for Section 507", exact=True)).to_be_hidden()
            # The current term's sections start ticked; one room alone is not a mix.
            expect(dialog.locator('input[name="mix_section"]:checked')).to_have_count(2)
            rows = dialog.locator(".seating-room-row")
            expect(rows).to_have_count(10)
            expect(rows.nth(0)).to_contain_text("Tiny")
            expect(rows.nth(0)).to_contain_text("Used for Quiz 3")
            expect(rows.nth(1)).to_contain_text("HECC 105")
            expect(dialog.locator("#seating-mix-tally")).to_be_hidden()
            dialog.get_by_label("HECC 105").check()
            expect(dialog.locator("#seating-forecast")).to_contain_text("Choose at least two rooms")
            # Find a room: every word typed, in any order; a ticked room it hides stays ticked and named; Enter creates nothing.
            find = dialog.get_by_label("Find a room")
            find.fill("103 bloc")
            expect(dialog.locator(".seating-room-row:visible")).to_have_text(["BLOC 103 3 usable 2 spaced"])
            expect(dialog.locator("#seating-mix-tally")).to_contain_text("1 room chosen: HECC 105")
            find.press("Enter")
            expect(dialog).to_be_visible()
            find.fill("nowhere")
            expect(dialog.locator(".seating-room-row:visible")).to_have_count(0)
            expect(dialog.locator("#seating-mix-rooms")).to_contain_text("No room has that name.")
            find.fill("")
            expect(dialog.locator(".seating-room-row:visible")).to_have_count(10)
            dialog.get_by_label("Tiny").check()
            expect(dialog.locator("#seating-mix-tally")).to_contain_text("2 rooms chosen: Tiny, HECC 105")
            table = dialog.locator("#seating-forecast table")
            expect(table).to_be_visible()
            expect(dialog.locator("#seating-forecast")).to_contain_text("7 students from Sections 507, 508 to mix · 2 DR bookings not seated")
            shares = [int(text) for text in table.locator("tbody tr:not(.seating-forecast-note) td:nth-child(2)").all_inner_texts()]
            assert sum(shares) == 7 and len(shares) == 2, shares
            if os.environ.get("SEATING_MIX_SCREENSHOT"):
                page.locator("#dialog").screenshot(path=os.environ["SEATING_MIX_SCREENSHOT"])
            dialog.get_by_role("button", name="Create seating chart", exact=True).click()
            expect(page.locator("#dialog")).to_be_hidden()
            charts = fixture.client.get("/api/seating").json
            charts = [chart for chart in charts if chart["assignment_id"] == fixture.assignment["id"]]
            assert sorted(chart["room_name"] for chart in charts) == ["HECC 105", "Tiny"], charts
            assert all(chart["sections"] == [507, 508] and chart["check"]["ok"] for chart in charts), charts
            seated = [student["enrollment_id"] for chart in charts for student in fixture.client.get(f"/api/seating/{chart['id']}").json["students"] if student["seat"]]
            assert len(seated) == len(set(seated)) == 7, seated
            # SEATING "One tile per exam": creating several rooms opens the exam's page, a tile for each room.
            expect(page).to_have_url(re.compile(r"#seating/exam/[^/]+/[^/]+$"))
            expect(page.locator("#page-title")).to_have_text("Test 1")
            expect(page.locator(".seating-group-heading")).to_contain_text("2 rooms · 7 seated")
            expect(page.locator(".seating-group .seating-card")).to_have_count(2)
            expect(page.get_by_label("Open Tiny seating for Test 1")).to_contain_text("mixed with HECC 105")
            expect(page.locator("#primary-action")).to_be_hidden()
            expect(page.get_by_role("button", name="Add rooms")).to_have_count(0)  # every section has a room
            # The list holds one tile per exam, with its rooms, sections and counts together.
            page.get_by_role("link", name="All seating charts", exact=True).click()
            expect(page.locator(".seating-group")).to_have_count(0)
            expect(page.locator(".seating-exam-card")).to_have_count(2)
            exam = page.get_by_label("Open Test 1 seating, MATH 140 · Fall 2026, 2 rooms")
            for text in ("Not confirmed", "HECC 105", "Tiny", "Sections 507, 508", "7 seated", "2 DR testing"):
                expect(exam).to_contain_text(text)
            if os.environ.get("SEATING_TILES_SCREENSHOT"):
                page.screenshot(path=os.environ["SEATING_TILES_SCREENSHOT"], full_page=True)
            # The tile opens the exam's rooms; a room's chart leads back to them.
            exam.click()
            page.get_by_label("Open HECC 105 seating for Test 1").click()
            expect(page.locator(".seat-chart")).to_be_visible()
            back = page.locator(".room-breadcrumbs").get_by_role("link", name="All rooms for Test 1", exact=True)
            expect(back).to_have_attribute("href", re.compile(r"^#seating/exam/[^/]+/[^/]+$"))
            back.click()
            expect(page.locator(".seating-group .seating-card")).to_have_count(2)
            # An exam with a section still without a room opens its page too, where Add rooms starts that section's chart.
            page.get_by_role("link", name="All seating charts", exact=True).click()
            quiz = page.get_by_label("Open Quiz 3 seating, MATH 140 · Fall 2026, 1 room")
            expect(quiz).to_contain_text("Not yet seated: Section 507")
            quiz.click()
            expect(page.locator(".seating-group-heading")).to_contain_text("1 room · 3 seated · not yet seated: Section 507")
            page.locator(".seating-group-heading").get_by_role("button", name="Add rooms", exact=True).click()
            expect(page.locator("#seating-create").get_by_label("Exam assignment", exact=True)).to_have_value(fixture.quiz["id"])
            page.locator("#seating-create").get_by_role("button", name="Cancel", exact=True).click()
            # An address for an exam with no chart says so.
            page.goto(address + f"/#seating/exam/missing/{charts[0]['term_id']}")
            expect(page.locator("#seating-section")).to_contain_text("No seating charts for this exam.")
            assert errors == [], errors
            browser.close()
        print("PASS: the mix forecast shows each room's share, creating it seats every student once across the rooms, and the list holds one tile per exam.")
    finally:
        server.close()
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
