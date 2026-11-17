"""Rooms and seating charts in the real private browser UI, on desktop widths (1280 and 1440; desktop only since 2026-10-03).

Run from the project root: python tests/browser_rooms.py
"""
import re
import io
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from test_rooms import FIXTURES, HEADERS, chart_bytes, fixture


def upload(page, content, filename="chart.xlsx"):
    page.get_by_label("Seating chart file", exact=True).set_input_files(
        {"name": filename, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": content})


def seat(page, label):
    # The accessible name is "Seat K5" plus any conditions, so anchor the label exactly.
    return page.locator(".seat-grid").get_by_role("button", name=re.compile(rf"^Seat {re.escape(label)}(,|$)"))


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # A rejected upload answers 400 on purpose; every other console error fails the check.
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                page.goto(address + "/#rooms")
                expect(page.locator("#page-title")).to_have_text("Rooms")
                expect(page.locator("#rooms-count")).to_have_text("0")
                expect(page.locator("#rooms-section .empty-state")).to_be_visible()

                # Add a room from the seating chart workbook.
                page.locator("#primary-action").click()
                expect(page.get_by_role("dialog")).to_contain_text("Add a room")
                page.get_by_label("Room name", exact=True).fill("BLOC 117")
                page.get_by_label("Notes", exact=False).fill("Lecture hall")
                upload(page, chart_bytes({"A1": "A1", "B1": "=A1"}))
                page.get_by_role("dialog").get_by_role("button", name="Add room", exact=True).click()
                expect(page.get_by_role("dialog").locator('[role="alert"]')).to_contain_text("Cell B1 contains a formula")
                upload(page, chart_bytes())
                page.get_by_role("dialog").get_by_role("button", name="Add room", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator("#page-title")).to_have_text("BLOC 117")
                expect(page.locator("#page-subtitle")).to_have_text("Lecture hall")
                expect(page.locator("#rooms-count")).to_have_text("1")
                assert "#rooms/" in page.url, page.url
                grid = page.locator(".seat-grid")
                expect(grid.get_by_role("button")).to_have_count(6)
                expect(grid.locator(".seat-landmark")).to_have_text(["Front of room"])
                expect(page.locator(".room-stat.broken strong")).to_have_text("0")
                expect(page.get_by_role("button", name="Mark broken", exact=True)).to_be_disabled()
                # Seats sit where their worksheet cells are: A3 is three columns right of A1, past the aisle gap.
                a1, a3, b1 = (seat(page, label).bounding_box() for label in ("A1", "A3", "B1"))
                assert abs((a3["x"] - a1["x"]) - 3 * (a1["width"] + 6)) < 2, (a1, a3)
                assert abs((b1["y"] - a1["y"]) - (a1["height"] + 6)) < 2, (a1, b1)
                page.screenshot(path=str(output / "room-desktop.png"), full_page=True)

                # Select one seat and mark it broken.
                seat(page, "A1").click()
                expect(page.locator("#seat-selection-count")).to_have_text("1 seat selected")
                expect(seat(page, "A1")).to_have_attribute("aria-pressed", "true")
                page.get_by_role("button", name="Mark broken", exact=True).click()
                expect(page.locator(".room-stat.broken strong")).to_have_text("1")
                expect(seat(page, "A1")).to_have_class("seat broken")
                expect(seat(page, "A1")).to_have_attribute("aria-pressed", "true")
                expect(page.get_by_role("button", name="Unmark broken", exact=True)).to_be_enabled()

                # Shift-click selects a block: B1 through B3 across the aisle gap.
                page.get_by_role("button", name="Clear selection", exact=True).click()
                expect(page.locator("#seat-selection-count")).to_have_text("No seats selected")
                seat(page, "B1").click()
                seat(page, "B3").click(modifiers=["Shift"])
                expect(page.locator("#seat-selection-count")).to_have_text("3 seats selected")
                page.get_by_role("button", name="Mark left-handed", exact=True).click()
                expect(page.locator(".room-stat.left strong")).to_have_text("3")
                expect(grid.locator(".seat.left-handed")).to_have_count(3)
                expect(page.get_by_role("button", name="Unmark left-handed", exact=True)).to_be_enabled()
                page.get_by_role("button", name="Clear conditions", exact=True).click()
                expect(page.locator(".room-stat.left strong")).to_have_text("0")
                expect(page.locator(".room-stat.broken strong")).to_have_text("1")
                page.get_by_role("button", name="Clear selection", exact=True).click()
                seat(page, "B2").click()
                seat(page, "B2").click()
                expect(page.locator("#seat-selection-count")).to_have_text("No seats selected")
                seat(page, "B2").click()
                page.get_by_role("button", name="Mark left-handed", exact=True).click()
                expect(page.locator(".room-stat.left strong")).to_have_text("1")
                expect(page.locator("#toast")).to_contain_text("1 seat marked left-handed")
                # Blocked: not broken, just not to be assigned; usable seats drop accordingly.
                page.get_by_role("button", name="Clear selection", exact=True).click()
                seat(page, "B3").click()
                page.get_by_role("button", name="Mark blocked", exact=True).click()
                expect(page.locator(".room-stat.blocked strong")).to_have_text("1")
                expect(page.locator(".room-stat:nth-child(2) strong")).to_have_text("4")
                expect(seat(page, "B3")).to_have_class("seat blocked")
                expect(page.get_by_role("button", name="Unmark blocked", exact=True)).to_be_enabled()

                # Conditions persist across a reload and appear on the room list.
                page.reload()
                expect(seat(page, "A1")).to_have_class("seat broken")
                expect(seat(page, "B2")).to_have_class("seat left-handed")
                expect(seat(page, "B3")).to_have_class("seat blocked")
                expect(page.locator("#seat-selection-count")).to_have_text("No seats selected")
                page.get_by_role("link", name="All rooms", exact=True).click()
                expect(page.locator("#page-title")).to_have_text("Rooms")
                card = page.get_by_role("link", name="Open BLOC 117", exact=True)
                expect(card).to_contain_text("4 usable of 6")
                expect(card).to_contain_text("1 broken")
                expect(card).to_contain_text("1 blocked")
                expect(card).to_contain_text("1 left-handed")
                search = page.get_by_label("Search rooms", exact=True)
                search.fill("missing room")
                expect(page.locator("#room-result-count")).to_have_text("Showing 0 of 1 room")
                expect(page.locator(".room-search-empty")).to_contain_text("No rooms match")
                expect(search).to_be_focused()
                search.fill("lecture")
                expect(card).to_be_visible()
                expect(page.locator("#room-result-count")).to_have_text("Showing 1 of 1 room")
                search.fill("")
                expect(card).to_be_visible()
                # Readiness chips filter the list (and the address keeps the filter); zero facts are not listed.
                expect(page.locator(".rooms-status-host [data-filter]")).to_have_text([re.compile("^All 1$"), re.compile("^Ready 1$"), re.compile("^Needs review 0$"), re.compile("^No assignable seats 0$")])
                page.locator('.rooms-status-host [data-filter="review"]').click()
                expect(page.locator("#room-result-count")).to_have_text("Showing 0 of 1 room")
                assert page.url.endswith("#rooms?filter=review"), page.url
                page.get_by_role("button", name="Clear filters", exact=True).click()
                expect(page.locator("#room-result-count")).to_have_text("Showing 1 of 1 room")
                page.goto(address + "/#rooms?filter=ready")
                expect(page.locator('.rooms-status-host [data-filter="ready"]')).to_have_attribute("aria-pressed", "true")
                expect(card).to_be_visible()
                expect(page.locator(".rooms-intro")).to_have_count(0)
                page.screenshot(path=str(output / "rooms-list.png"), full_page=True)
                card.click()
                expect(page.locator("#page-title")).to_have_text("BLOC 117")
                # One settings entry: the header's; the plan panel is named Room plan.
                expect(page.locator("#rooms-section").get_by_role("button", name="Room settings")).to_have_count(0)
                expect(page.locator("#room-chart-title")).to_have_text("Room plan")

                # Rename and replace the chart; conditions follow the seat labels.
                page.locator("#primary-action").click()
                expect(page.get_by_role("dialog")).to_contain_text("Room settings")
                page.get_by_label("Room name", exact=True).fill("BLOC 118")
                page.get_by_role("dialog").get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator("#page-title")).to_have_text("BLOC 118")
                page.get_by_role("button", name="Replace chart file", exact=True).click()
                upload(page, chart_bytes({"A1": "Door", "B2": "a1", "C2": "B2", "B3": "C1"}), "v2.xlsx")
                page.get_by_role("dialog").get_by_role("button", name="Replace chart", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(grid.get_by_role("button")).to_have_count(3)
                expect(seat(page, "a1")).to_have_class("seat broken")
                expect(seat(page, "B2")).to_have_class("seat left-handed")
                expect(page.locator(".room-imports p")).to_have_count(2)
                expect(page.locator(".room-imports")).to_contain_text("3 seats (1 added, 2 kept, 4 removed)")

                # The narrowest desktop window: the chart scrolls inside its panel, never the page.
                page.set_viewport_size({"width": 1280, "height": 900})
                page.goto(page.url)
                expect(grid.get_by_role("button")).to_have_count(3)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                # The plan's zoom bar: no button overlaps another or leaves the plan panel.
                boxes = page.locator(".seat-plan-controls .text-button").evaluate_all(
                    "nodes => nodes.map(node => { const box = node.getBoundingClientRect(); return [box.left, box.top, box.right, box.bottom, node.scrollWidth <= node.clientWidth + 1]; })")
                panel = page.locator(".room-chart-panel").bounding_box()
                for index, (left, top, right, bottom, fits) in enumerate(boxes):
                    assert fits, ("zoom button text is clipped", boxes)
                    assert left >= panel["x"] - 1 and right <= panel["x"] + panel["width"] + 1, (boxes, panel)
                    for other in boxes[index + 1:]:
                        assert right <= other[0] + 0.5 or other[2] <= left + 0.5 or bottom <= other[1] + 0.5 or other[3] <= top + 0.5, ("zoom buttons overlap", boxes)
                page.screenshot(path=str(output / "room-1280.png"), full_page=True)
                seat(page, "C1").click()
                page.get_by_role("button", name="Mark broken", exact=True).click()
                expect(page.locator(".room-stat.broken strong")).to_have_text("2")

                # Full width: the plan's panel takes the window on screen only; printing keeps the page as it is.
                page.set_viewport_size({"width": 1440, "height": 1000})
                panel_position = "node => getComputedStyle(node.closest('.room-chart-panel')).position"
                page.get_by_role("button", name="Full width", exact=True).click()
                expect(page.get_by_role("button", name="Full width", exact=True)).to_have_attribute("aria-pressed", "true")
                assert grid.evaluate(panel_position) == "fixed"
                assert page.locator(".room-chart-panel").bounding_box()["y"] <= 1
                page.emulate_media(media="print")
                assert grid.evaluate(panel_position) != "fixed"
                page.emulate_media(media="screen")
                page.keyboard.press("Escape")
                expect(page.get_by_role("button", name="Full width", exact=True)).to_have_attribute("aria-pressed", "false")
                assert grid.evaluate(panel_position) != "fixed"

                # Remove the room.
                page.locator("#primary-action").click()
                page.get_by_role("dialog").get_by_role("button", name="Remove room", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("Remove this room?")
                page.get_by_role("dialog").get_by_role("button", name="Remove room", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator("#page-title")).to_have_text("Rooms")
                expect(page.locator("#rooms-count")).to_have_text("0")
                expect(page.locator("#rooms-section .empty-state")).to_be_visible()

                # The public copy leaves out the department's workbooks; this part runs where they are.
                if (FIXTURES / "HECC 209.xlsx").exists():
                    # A real department chart: row letters, tables, merged captions, and pre-marked broken seats.
                    page.locator("#primary-action").click()
                    page.get_by_label("Room name", exact=True).fill("HECC 209")
                    upload(page, fixture("HECC 209"), "HECC 209.xlsx")
                    page.get_by_role("dialog").get_by_role("button", name="Add room", exact=True).click()
                    expect(page.get_by_role("dialog")).not_to_be_visible()
                    expect(page.locator("#page-title")).to_have_text("HECC 209")
                    expect(page.locator(".room-stat:first-child strong")).to_have_text("142")
                    expect(page.locator(".room-stat.broken strong")).to_have_text("7")
                    expect(page.locator(".room-stat.blocked")).to_have_class(re.compile(r"\bis-zero\b"))
                    expect(grid.locator(".seat-rowlabel")).to_have_text(list("KJHGFEDCBA"))
                    expect(grid.locator(".seat-table")).to_have_count(2)
                    expect(grid.locator(".seat.seat-mini")).to_have_count(6)
                    expect(grid.locator(".seat-landmark")).to_have_count(6)
                    expect(grid.locator(".seat-landmark.vertical")).to_have_text(["STAIRS", "STAIRS"])
                    expect(seat(page, "G2")).to_have_class("seat broken")
                    expect(seat(page, "G2")).to_have_text("2")
                    # Row letters and captions land where their cells are: J1 sits under row label J.
                    label_j = grid.locator(".seat-rowlabel", has_text="J").first.bounding_box()
                    j1 = seat(page, "J1").bounding_box()
                    assert abs(label_j["y"] - j1["y"]) < 2, (label_j, j1)
                    back = grid.locator(".seat-landmark", has_text="BACK OF THE ROOM").bounding_box()
                    assert back["width"] > 12 * j1["width"], back
                    page.get_by_role("button", name="Select all seats at K Table 1", exact=True).click()
                    expect(page.locator("#seat-selection-count")).to_have_text("3 seats selected")
                    page.get_by_role("button", name="Mark left-handed", exact=True).click()
                    expect(page.locator(".room-stat.left strong")).to_have_text("3")
                    page.get_by_role("button", name="Fit room", exact=True).click()
                    expect(page.get_by_role("button", name="Fit room", exact=True)).to_have_attribute("aria-pressed", "true")
                    assert grid.bounding_box()["width"] <= page.locator(".seat-chart").bounding_box()["width"]
                    page.screenshot(path=str(output / "room-hecc-209.png"), full_page=True)
                    expect(page.locator("#rooms-count")).to_have_text("1")

                # Floor-plan geometry uses the same buttons and conditions. The
                # response fixture isolates UI placement from the PDF parser.
                content = chart_bytes()
                created = app.test_client().post("/api/rooms", data={
                    "name": "Floor plan preview", "file": (io.BytesIO(content), "preview.xlsx")},
                    headers=HEADERS)
                assert created.status_code == 201, created.json
                preview = created.json
                preview["layout"] = {"kind": "floorplan", "width": 1600, "height": 700,
                    "seats": {item["label"]: {"x": 80 + index % 3 * 300, "y": 120 + index // 3 * 180,
                                              "width": 50, "height": 50, "rotation": 15 if index == 2 else 0}
                              for index, item in enumerate(preview["seats"])},
                    "features": [{"kind": "rect", "x": 15, "y": 15, "width": 1560, "height": 660},
                                 {"kind": "path", "d": "M 30 650 L 1570 650"},
                                 {"kind": "text", "x": 800, "y": 620, "text": "Podium <front>", "font_size": 24}]}
                preview["review_required"] = ["Check chair <location> against the source.", "Seats A2 and B1 share one position in the source.",
                                              "Cell A2 note “H” was not understood; seat B1 was imported without it."]
                preview["seats"][0]["notes"] = "Broken <arm> reported in Word document"
                for item in preview["seats"][:2]:
                    item["group"] = "Desk 1"
                    item["group_size"] = 2
                endpoint = f"**/api/rooms/{preview['id']}"
                page.route(endpoint, lambda route: route.fulfill(json=preview))
                page.goto(address + f"/#rooms/{preview['id']}")
                expect(grid).to_have_class("seat-grid seat-grid-floorplan")
                expect(grid.locator(".seat")).to_have_count(6)
                expect(grid.locator("svg text")).to_have_text("Podium <front>")
                expect(grid.locator("svg rect")).to_have_count(1)
                expect(grid.locator("svg path")).to_have_count(1)
                expect(page.locator(".room-review")).to_contain_text("Check chair <location>")
                expect(page.locator(".room-review > strong")).to_have_text("3 things to check before seating an exam here")
                # A reason naming seats selects exactly those seats; nothing is guessed for the other, and a spreadsheet
                # cell reference ("Cell A2") is never taken for the seat with the same letters and digits.
                expect(page.locator(".room-review [data-review-seats]")).to_have_count(2)
                page.get_by_role("button", name="Show seats (A2, B1)", exact=True).click()
                expect(page.locator("#seat-selection-count")).to_have_text("2 seats selected")
                page.get_by_role("button", name="Show seat (B1)", exact=True).click()
                expect(page.locator("#seat-selection-count")).to_have_text("1 seat selected")
                page.get_by_role("button", name="Clear selection", exact=True).click()
                first = seat(page, preview["seats"][0]["label"])
                expect(first).to_have_attribute("title", re.compile("Broken <arm>"))
                page.get_by_role("button", name="Actual size", exact=True).click()
                first.click()
                expect(first).to_have_attribute("aria-pressed", "true")
                before = first.bounding_box()
                other = seat(page, preview["seats"][1]["label"]).bounding_box()
                assert abs(other["x"] - before["x"] - 300) < 2, (before, other)
                page.get_by_role("button", name="Fit room", exact=True).click()
                fit_width = first.bounding_box()["width"]
                assert fit_width < before["width"]
                assert page.locator(".seat-chart").evaluate("node => node.scrollWidth <= node.clientWidth + 1")
                page.get_by_role("button", name="Zoom in", exact=True).click()
                assert first.bounding_box()["width"] > fit_width
                page.get_by_role("button", name="Actual size", exact=True).click()
                assert abs(first.bounding_box()["width"] - before["width"]) < 1
                page.get_by_role("button", name="Clear selection", exact=True).click()
                page.locator(".seat-plan-groups summary").click()
                page.get_by_role("button", name="Select all seats at Desk 1", exact=True).click()
                expect(page.locator("#seat-selection-count")).to_have_text("2 seats selected")
                # A room to confirm before seating: confirming is the page's one primary, Room settings beside it; the callout keeps its own.
                expect(page.locator("#primary-action")).to_have_text("Confirm the room is ready…")
                expect(page.locator(".page-heading").get_by_role("button", name="Room settings", exact=True)).to_be_visible()
                page.locator(".room-review").get_by_role("button", name="Confirm the room is ready…", exact=True).click()
                expect(page.get_by_label("I checked the seat labels and chair locations; this room is ready for exam seating")).not_to_be_checked()
                expect(page.get_by_label("I checked the seat labels and chair locations; this room is ready for exam seating")).to_be_focused()
                page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
                page.locator("#primary-action").click()
                expect(page.get_by_label("I checked the seat labels and chair locations; this room is ready for exam seating")).to_be_focused()
                page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
                page.get_by_role("button", name="Fit room", exact=True).click()
                page.locator('.nav-item[href="#overview"]').click()
                page.set_viewport_size({"width": 1280, "height": 900})
                page.evaluate("hash => { location.hash = hash; }", f"#rooms/{preview['id']}")
                expect(grid).to_be_visible()
                expect(page.locator(".seat-plan-zoom")).not_to_have_text("NaN%")
                assert grid.bounding_box()["width"] <= page.locator(".seat-chart").bounding_box()["width"]
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                expect(first).to_have_attribute("aria-pressed", "true")
                page.screenshot(path=str(output / "room-floorplan-1280.png"), full_page=True)
                browser.close()
                assert not errors, errors
        finally:
            server.close()
    print("Room browser checks passed.")


if __name__ == "__main__":
    main()
