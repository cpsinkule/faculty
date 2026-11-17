"""Exam-day packets in a real browser (AGENTS.md "Exam-day packet"): the Exams hub links the plan; the plan page picks proctors
from the TA lists or typed in, edits the instructions, saves with a request ID and handles a stale save; Email proctors is a
mailto: link only; the room's Print packet link opens the chart page's packet dialog once and drops its query; Print reads the
packet again and asks for another click when it changed; the printed PDF holds the portrait cover, instructions and room roster
before the seat chart; everything is cleaned up after printing; the desktop window (1280 wide) has no sideways scroll.

Uses an isolated catalog, synthetic students and an intercepted window.print. Nothing is printed, stored or emailed.
Run: LD_LIBRARY_PATH=$PWD/.tools/browser-libs/usr/lib/x86_64-linux-gnu .venv/bin/python tests/browser_exam_day.py
"""
import io
import re
import sys
import threading
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from rosters import connect
from test_exam_day import ExamDayTests

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


def prepare(fixture):
    """Two TA lists, an exam date, exam versions A/B and one export with two extras per section."""
    fixture.set_tas(507, [{"name": "Ada Tutor", "email": "ada.tutor@tamu.edu", "phone": "979-555-0101"}])
    fixture.set_tas(508, [{"name": "Bea Grader", "email": "bea.grader@tamu.edu"}])
    with connect(fixture.path) as db:
        db.execute("UPDATE seating_charts SET exam_date='2026-10-08', exam_time='7:00 PM' WHERE id=?", (fixture.chart["id"],))
    chart = fixture.client.get(f"/api/seating/{fixture.chart['id']}").json
    assigned = fixture.client.post(f"/api/seating/{fixture.chart['id']}/exam-versions", json={"version": chart["version"], "count": 2}, headers=HEADERS)
    assert assigned.status_code == 200, assigned.json
    fixture.confirm("browser-export", extras=2)


def no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def main():
    fixture = ExamDayTests("test_a_new_plan_shows_the_default_instructions_and_each_room_without_uins")
    fixture.setUp()
    prepare(fixture)
    chart_id, aid, term_id = fixture.chart["id"], fixture.assignment["id"], fixture.term_id
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{server.effective_port}"
    output = Path("test-results/exam-day")
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, timezone_id="America/Chicago")
            page.set_default_timeout(10000)
            errors, writes = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text)
                    if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
            page.on("request", lambda request: writes.append(request.url)
                    if request.method in ("POST", "PUT", "PATCH", "DELETE") else None)
            page.add_init_script("""window.packetPrints = 0; window.packetCsp = [];
                window.print = () => { window.packetPrints += 1; };
                document.addEventListener('securitypolicyviolation', event => window.packetCsp.push(event.violatedDirective));""")

            # The Exams hub's Exam day stage links the plan; the hub itself stores nothing.
            page.goto(f"{base}/#exams/assessment/{aid}/{term_id}")
            tile = page.locator('.exams-stage[data-stage="exam_day"]')
            expect(tile.get_by_role("link", name="Exam-day packet")).to_be_visible()
            tile.get_by_role("link", name="Exam-day packet").click()
            expect(page).to_have_url(re.compile(rf"#exam-day/{aid}/{term_id}$"))
            expect(page.locator("#page-title")).to_have_text("Test 1")
            room = page.locator(f'.exam-day-room[data-chart="{chart_id}"]')
            expect(room.locator("h3")).to_have_text("HECC 105")
            expect(room).to_contain_text("Thu, Oct 8 · 7:00 PM")
            expect(room.locator(".exam-day-stats")).to_contain_text("A 4 · B 3")
            expect(room.locator(".exam-day-stats")).to_contain_text("4")  # extras
            expect(page.locator(".exam-day-state")).to_contain_text("Not saved yet")
            expect(page.locator("[data-save]")).to_be_disabled()
            assert "001234567" not in page.locator("#exam-day-section").inner_text()
            expect(page.locator("[data-mail]")).to_have_count(0)

            # Proctors: a TA of the room's sections, then someone typed in (a bad email first).
            room.locator("select[data-pick]").select_option(label="Ada Tutor · Section 507")
            expect(room.locator(".exam-day-proctors li")).to_have_count(1)
            expect(room.locator(".exam-day-proctors")).to_contain_text("979-555-0101")
            room.locator("select[data-pick]").select_option("typed")
            typed = room.locator("[data-typed]")
            typed.locator('[name="name"]').fill("Riley Ramos")
            typed.locator('[name="email"]').fill("riley at tamu")
            typed.get_by_role("button", name="Add proctor").click()
            expect(typed.locator('[role="alert"]')).to_contain_text("does not look like one")
            typed.locator('[name="email"]').fill("riley.ramos@tamu.edu")
            typed.locator('[name="phone"]').fill("979 555 0199")
            typed.locator('[name="phone"]').press("Enter")
            expect(room.locator(".exam-day-proctors li")).to_have_count(2)
            expect(room.locator("[data-typed]")).to_have_count(0)
            room.get_by_role("button", name="Remove Ada Tutor from HECC 105").click()
            expect(room.locator(".exam-day-proctors li")).to_have_count(1)
            room.locator("select[data-pick]").select_option(label="Bea Grader · Section 508")
            expect(room.locator(".exam-day-proctors li")).to_have_count(2)

            # Instructions: typed, reset to the default with Undo.
            text = page.locator("[data-instructions]")
            text.fill("Arrive 20 minutes early.\nSeat everyone by the chart.\nCollect every paper, extras included.")
            expect(page.locator(".exam-day-state")).to_have_text("Unsaved changes")
            page.get_by_role("button", name="Reset to default").click()
            expect(text).to_have_value(re.compile(r"^Before the exam"))
            page.get_by_role("button", name="Undo").click()
            expect(text).to_have_value(re.compile(r"^Arrive 20 minutes early"))
            mail = page.locator("[data-mail]").get_attribute("href")
            assert mail.startswith("mailto:riley.ramos@tamu.edu,bea.grader@tamu.edu?subject="), mail
            assert len(mail) <= 1800, len(mail)
            body = unquote(mail.split("&body=")[1])
            assert "HECC 105" in body and "Riley Ramos" in body and "Arrive 20 minutes early" in body, body

            # Save: one request, the saved plan shown back.
            page.get_by_role("button", name="Save plan").click()
            expect(page.locator(".exam-day-state")).to_contain_text("Saved")
            plan = fixture.plan()
            assert plan["revision"] == 1 and plan["instructions"].startswith("Arrive 20 minutes early"), plan
            assert [person["name"] for person in plan["proctors"][chart_id]] == ["Riley Ramos", "Bea Grader"], plan["proctors"]
            assert sum(url.endswith(f"/api/exam-day/{aid}/{term_id}") for url in writes) == 1, writes

            # A plan saved elsewhere in the meantime: the save is refused, and Load the saved plan shows it.
            other = fixture.client.post(f"/api/exam-day/{aid}/{term_id}", json={"base_revision": 1, "request_id": "elsewhere",
                "instructions": "Saved in another tab.", "proctors": plan["proctors"]}, headers=HEADERS)
            assert other.status_code == 200, other.json
            text.fill("A late edit.")
            page.get_by_role("button", name="Save plan").click()
            expect(page.locator(".exam-day-feedback")).to_contain_text("changed elsewhere")
            page.get_by_role("button", name="Load the saved plan").click()
            expect(text).to_have_value("Saved in another tab.")
            expect(page.locator(".exam-day-state")).to_contain_text("Saved")
            assert fixture.plan()["revision"] == 2

            # Desktop only (DESIGN-LANGUAGE, 2026-10-03): the narrowest desktop window has no sideways scroll.
            page.set_viewport_size({"width": 1280, "height": 900})
            page.screenshot(path=str(output / "plan-1280.png"), full_page=True)
            assert no_sideways_scroll(page), "the plan page scrolls sideways at 1280 px"
            page.set_viewport_size({"width": 1440, "height": 1000})

            # Print packet: the chart page opens the packet once and drops ?packet=1 from its address.
            room.get_by_role("link", name="Print packet").click()
            form = page.locator("#seating-packet-print")
            expect(form).to_be_visible()
            expect(page).to_have_url(re.compile(rf"#seating/{chart_id}$"))
            preview = form.locator("#seating-packet-preview")
            expect(preview.locator(".packet-cover")).to_contain_text("HECC 105")
            expect(preview.locator(".packet-cover")).to_contain_text("Thu, Oct 8, 2026 · 7:00 PM")
            expect(preview.locator(".packet-copies")).to_contain_text("Version A")
            expect(preview.locator(".packet-copies")).to_contain_text("Extras (printed for no student)")
            expect(preview.locator(".packet-proctors")).to_contain_text("Riley Ramos")
            expect(preview.locator(".packet-proctors")).to_contain_text("bea.grader@tamu.edu")
            expect(preview.locator(".packet-instructions")).to_contain_text("Saved in another tab.")
            expect(preview.locator(".seating-roster-seated tbody tr")).to_have_count(7)
            expect(preview.locator(".seating-roster-dr tbody tr")).to_have_count(2)
            expect(form.locator('input[name="show"]')).to_have_count(2)
            # A changed plan after the preview: Print shows the new packet and waits for another click.
            changed = fixture.client.post(f"/api/exam-day/{aid}/{term_id}", json={"base_revision": 2, "request_id": "late-change",
                "instructions": "Final instructions for the room.", "proctors": plan["proctors"]}, headers=HEADERS)
            assert changed.status_code == 200, changed.json
            form.get_by_role("button", name="Print", exact=True).click()
            expect(form.locator('[role="alert"]')).to_contain_text("changed")
            expect(preview.locator(".packet-instructions")).to_contain_text("Final instructions for the room.")
            assert page.evaluate("window.packetPrints") == 0
            orientation = page.locator('input[name="layout"]:checked').get_attribute("value")
            form.get_by_role("button", name="Print", exact=True).click()
            expect(page.locator("body")).to_have_class(re.compile(r"\bprint-packet\b"))
            assert page.evaluate("window.packetPrints") == 1
            assert page.evaluate("document.body.firstElementChild.classList.contains('seating-exam-packet')")
            chart_paper = page.evaluate("""[...document.adoptedStyleSheets].flatMap(sheet => [...sheet.cssRules]).map(rule => rule.cssText)
                .join(' ').match(/size: letter (portrait|landscape)/)?.[1]""")
            assert chart_paper in ("portrait", "landscape"), chart_paper
            page.emulate_media(media="print")
            pdf = page.pdf(path=str(output / "packet.pdf"), prefer_css_page_size=True, print_background=True)
            reader = PdfReader(io.BytesIO(pdf))
            pages = [p.extract_text() for p in reader.pages]
            sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]
            text_all = "\n".join(pages)
            squashed = re.sub(r"\s+", "", text_all)
            assert "exam-day packet" in pages[0].lower() and "HECC 105" in pages[0] and "Riley Ramos" in pages[0], pages[0]
            assert "Version A 4" in pages[0] and "Extras (printed for no student) 4" in pages[0], pages[0]
            assert "Final instructions for the room." in text_all
            roster = fixture.client.get(f"/api/seating/{chart_id}/room-roster").json
            for student in roster["room_students"] + roster["dr_students"]:
                assert student["name"] in text_all and student["uin"] in text_all, student["name"]
            chart = fixture.client.get(f"/api/seating/{chart_id}").json
            for seat in chart["room"]["seats"]:
                if seat["student"]:
                    assert re.sub(r"\s+", "", seat["label"]) in squashed, seat["label"]
            roster_end = max(index for index, value in enumerate(pages) if "DR testers" in value)
            assert roster_end + 1 < len(pages), "the seat chart follows the room roster"
            for width, height in sizes[:roster_end + 1]:
                assert width < height, ("packet pages print in portrait", sizes)
            for width, height in sizes[roster_end + 1:]:
                assert (width > height) == (chart_paper == "landscape"), ("the chart keeps its orientation", chart_paper, sizes)
            assert all(value.strip() for value in pages), "blank printed page"
            page.evaluate('window.dispatchEvent(new Event("afterprint"))')
            expect(page.locator(".seating-exam-packet")).to_have_count(0)
            assert "print-packet" not in (page.locator("body").get_attribute("class") or "")
            page.emulate_media(media="screen")
            print(f"packet: {len(pages)} pages ({roster_end + 1} portrait packet pages, chart {chart_paper}, layout {orientation})", flush=True)

            # The chart page's own Print group offers the packet too; its dialog stays on screen.
            page.get_by_role("button", name="Print exam-day packet").click()
            expect(page.locator("#seating-packet-print")).to_be_visible()
            assert page.locator("#dialog").evaluate("el => el.getBoundingClientRect().right <= innerWidth + 1")
            page.screenshot(path=str(output / "packet-dialog.png"))
            page.locator("#seating-packet-print").get_by_role("button", name="Cancel").click()

            # Only the plan's own saves were written; nothing was printed on the server or sent.
            assert all(f"/api/exam-day/{aid}/{term_id}" in url for url in writes), writes
            assert not errors, errors
            assert page.evaluate("window.packetCsp") == [], page.evaluate("window.packetCsp")
            browser.close()
        print("PASS: Exams hub link, proctors from TA lists and typed, instructions with reset/undo, one save and a stale save, "
              "mailto only, ?packet=1 opened once, re-checked preview, portrait packet pages before the chart, cleanup, no sideways scroll at 1280, CSP.")
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
