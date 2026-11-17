"""Set up grading now and the 'Given on' date in the real browser (requirement, 2026-10-02).

Create assignment's Details step takes the date, typed in a browser on the campus clock late in the evening, and the exam record
keeps exactly the typed date; its Paper step sets grading up before any paper (the new version registered first, its blank template
imported); the Exams hub's Grading tile and the Assignments card's ⋯ menu do the same. Synthetic data; the template conversion is
scripted. SET_UP_GRADING_SHOTS names a directory for the screenshots at 1440x900 (default test-results).

Run from the project root: python tests/browser_set_up_grading.py
"""
import os
import re
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import expect, sync_playwright  # noqa: E402
from waitress.server import create_server  # noqa: E402

from app import create_app  # noqa: E402
from browser_seating import seed  # noqa: E402
from scheduling.clock import campus_today, day_label  # noqa: E402
from test_assignment_templates import prepared  # noqa: E402
from test_seating import HEADERS  # noqa: E402

TESTING = ("document.documentElement && (document.documentElement.dataset.testing = '1');"
           "document.addEventListener('DOMContentLoaded', () => { document.documentElement.dataset.testing = '1'; });")
SHOTS = Path(os.environ.get("SET_UP_GRADING_SHOTS", "test-results"))
SHOTS.mkdir(parents=True, exist_ok=True)


def wait_until(page, expression, timeout=15000):
    for _ in range(timeout // 100):
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Timed out waiting for {expression}")


def shots(page, name, locator=None):
    """One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width."""
    if locator is not None:
        locator.scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS / f"{name}-1440.png"), animations="disabled")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name


def plan_of(client, assignment_id, term_id=None):
    exam = next((item for item in client.get("/api/exams?summary=0").json["exams"] if item["assignment_id"] == assignment_id
                 and (term_id is None or item["term_id"] == term_id)), None)
    if not exam:
        return None, None
    plans = [p for p in client.get("/api/grading?view=navigation").json["assignments"] if p["event_id"] == exam["id"]]
    return client.get(f"/api/exams/{exam['id']}").json, plans[0] if plans else None


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = seed(app)
        definition = client.get("/api/state").json["course_definitions"][0]["id"]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with patch("document_identity.template_conversion.convert_template", side_effect=prepared), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            # Quiz 12 is given tomorrow by the campus clock the server reads: Home's Coming up lists only what is on or after the
            # server's `today`, so the fixed 2026-10-08 this check typed before left that list on 2026-10-09.
            quiz_day = campus_today() + timedelta(days=1)
            typed = quiz_day.isoformat()
            # The campus clock late in the evening before (04:30 UTC on the quiz's day): the typed date must stay the typed date.
            context = browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="America/Chicago")
            context.add_init_script(TESTING)
            page = context.new_page()
            # Request interception stays on for the whole check: a route of its own passes everything on. Without it the
            # synthetic failures' `times=1` routes expiring (and the last unroute) switched interception off while the page was
            # starting its next read, which then never completed: the wizard stayed on its step (Playwright, 2026-10-03).
            page.route("**/*", lambda route: route.fallback())
            page.clock.set_fixed_time(f"{typed}T04:30:00Z")
            page.set_default_timeout(15000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text)
                    if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
            loads = iter(range(1, 100))
            # A full page load each time (not a hash change): the fixed clock would keep a page's own short-lived reads forever.
            load = lambda target, hash_: target.goto(f"{address}/?load={next(loads)}{hash_}")
            load(page, "#assignments")
            assert page.evaluate("new Date().getDate()") == (quiz_day - timedelta(days=1)).day  # the browser's own day is still the day before

            # --- Create assignment: Given on (empty by default), then Set up grading now on the Paper step. ---
            # Its starting menu comes first, and seating is left out unless asked for: leaving Details makes the assignment and
            # prepares grading, as leaving Seating does (ASSIGNMENT-WORKSPACE "Workflow clarity", 2026-10-09).
            page.get_by_role("button", name="Create assignment", exact=True).click()
            page.get_by_role("button", name="Create paper and print", exact=True).click()
            wizard = page.locator("#assignment-wizard")
            expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text("Details")
            expect(wizard.get_by_label("Arrange assigned seats for this assessment", exact=True)).not_to_be_checked()
            given = wizard.get_by_label("Given on (optional)")
            expect(given).to_have_value("")
            wizard.get_by_label("Assignment title", exact=True).fill("Quiz 12")
            given.fill(typed)
            shots(page, "wizard-given-on", given)
            page.route("**/api/grading/assignments", lambda route: route.fulfill(status=503, json={"error": "Synthetic setup failure"}), times=1)
            wizard.get_by_role("button", name="Next").click()
            expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text("Paper")
            expect(wizard.locator(".wizard-steps")).not_to_contain_text("Seating")
            quiz = next(item for item in client.get("/api/documents/catalog").json["assignments"] if item["assignment_key"] == "quiz-12")
            assert plan_of(client, quiz["id"])[1] is None
            expect(wizard.locator("[data-grading-problems]")).to_contain_text("Synthetic setup failure")
            wizard.get_by_role("button", name="Retry grading preparation").click()
            expect(wizard.locator("[data-grading-problems]")).to_have_count(0)
            assert len([item for item in client.get("/api/documents/catalog").json["assignments"] if item["assignment_key"] == "quiz-12"]) == 1
            exam, plan = plan_of(client, quiz["id"])
            # ASSIGNMENT-WORKSPACE: explicit creation prepares its term plan before any paper exists.
            assert [(s["label"], s["kind"], s["date"]) for s in exam["sittings"]] == [("Quiz 12", "main", typed)], exam["sittings"]
            assert sorted(s["number"] for s in exam["sections"]) == [507, 508], exam["sections"]
            assert plan and plan["entry"]["set_up_before_papers"]
            initial_plan_id = plan["id"]
            # Back on Details the date this window saved stays editable; clearing it takes it off that sitting again, and typing
            # it again dates the same sitting (review fix, 2026-10-02).
            step = wizard.locator('.wizard-steps [aria-current="step"]')
            def back_to_details():
                wizard.get_by_role("button", name="Back", exact=True).click()
                expect(step).to_have_text("Details")
            def on_to_paper():
                wizard.get_by_role("button", name="Next").click()
                expect(step).to_have_text("Paper")
            back_to_details()
            expect(wizard.get_by_label("Given on (optional)")).to_have_value(typed)
            wizard.get_by_label("Given on (optional)").fill("")
            on_to_paper()
            cleared, _ = plan_of(client, quiz["id"])
            assert [(s["id"], s["kind"], s["date"]) for s in cleared["sittings"]] == [(exam["sittings"][0]["id"], "main", "")], cleared["sittings"]
            back_to_details()
            expect(wizard.get_by_label("Given on (optional)")).to_have_value("")
            wizard.get_by_label("Given on (optional)").fill(typed)
            on_to_paper()
            dated, _ = plan_of(client, quiz["id"])
            assert [(s["id"], s["date"]) for s in dated["sittings"]] == [(exam["sittings"][0]["id"], typed)], dated["sittings"]
            line = wizard.locator("[data-grading-setup]")
            expect(line).to_contain_text("Grading is prepared for this term.")
            setup = line.get_by_role("button", name="Prepare questions")
            expect(setup).not_to_have_class(re.compile(r"\bprimary\b"))
            expect(wizard.locator(".dialog-footer .button.primary")).to_have_text("Next")
            shots(page, "wizard-set-up-grading", line)
            # A failed template read cannot block printing, and its explicit retry keeps the paper and plan.
            wizard.get_by_label("New version label").fill("A")
            wizard.get_by_label("Source", exact=True).select_option("letter")
            template_route = f"**/api/grading/assignments/{initial_plan_id}/templates"
            page.route(template_route, lambda route: route.fulfill(status=503, json={"error": "Synthetic template read failure"}), times=1)
            wizard.get_by_role("button", name="Next").click()
            expect(step).to_have_text("Stamp and print")
            expect(wizard.locator("[data-grading-problems]")).to_contain_text("Printing can continue.")
            wizard.locator('input[name="quantity"]').fill("1")
            wizard.get_by_role("button", name="Create print job", exact=True).click()
            expect(wizard.locator("[data-grading-problems]")).to_be_visible()
            wizard.get_by_role("button", name="Retry grading preparation").click()
            expect(wizard.locator("[data-grading-problems]")).to_have_count(0)
            wizard.get_by_role("button", name="Done", exact=True).click()
            expect(page.locator("#assignment-wizard")).not_to_be_visible()
            exam, plan = plan_of(client, quiz["id"])
            assert plan["id"] == initial_plan_id
            expect(page).to_have_url(re.compile(rf"#assignments/{quiz['id']}/{exam['term_id']}$"))
            page.locator("[data-assignment-nav]").get_by_role("link", name="Prepare", exact=True).click()
            wait_until(page, "location.hash.startsWith('#grading/') && location.hash.includes('tab=rubrics')")
            expect(page.locator("#grading-section")).not_to_contain_text("Finish Inbox review first")
            exam, plan = plan_of(client, quiz["id"])
            assert plan and plan["entry"] == {**plan["entry"], "ready": True, "set_up_before_papers": True}, plan
            assert page.evaluate("location.hash") == f"#grading/{plan['id']}?tab=rubrics&workspace=1"
            read = client.get(f"/api/grading/assignments/{plan['id']}").json
            versions = {v["label"]: v["id"] for v in client.get("/api/documents/catalog").json["versions"] if v["assignment_id"] == quiz["id"]}
            assert [t["source_version_id"] for t in read["templates"]] == [versions["A"]], read["templates"]
            shots(page, "grading-after-set-up")
            # Home's Coming up and the Calendar show the quiz on the typed date.
            load(page, "#overview")
            coming = page.locator(".home-coming-item").filter(has_text="Quiz 12")
            expect(coming).to_have_count(1)
            expect(coming).to_contain_text(day_label(quiz_day))
            # The Assignments card links Grading now, and its ⋯ menu no longer offers to set it up.
            load(page, "#documents?view=assignments")
            card = page.locator("#document-assignments article[data-assignment-card]").filter(has_text="Quiz 12")
            expect(card.get_by_role("button", name="More actions for MATH 140 · Quiz 12")).to_have_count(0)
            expect(card).to_contain_text(f"Exam {day_label(quiz_day)}")

            # --- The Exams hub's Grading tile, for an exam record without a plan. ---
            quiz13 = client.post("/api/documents/assignments", json={"definition_id": definition, "key": "quiz-13", "title": "Quiz 13"}, headers=HEADERS).json
            term = next(s for s in client.get("/api/seating/catalog").json["sections"] if s["number"] == 507)["term_id"]
            created = client.post("/api/exams", json={"term_id": term, "assignment_id": quiz13["id"], "title": "Quiz 13",
                                                      "section_ids": [s["id"] for s in client.get("/api/seating/catalog").json["sections"]]}, headers=HEADERS)
            assert created.status_code == 201, created.json
            load(page, f"#exams/{created.json['id']}")
            tile = page.locator('.exams-stage[data-stage="grading"]')
            expect(tile).to_contain_text("Not set up yet · set it up now, or associate a scan in the Teaching Inbox")
            button = tile.get_by_role("button", name="Set up grading now")
            expect(button).to_be_visible()
            shots(page, "exams-grading-tile", tile)
            button.click()
            wait_until(page, "location.hash.startsWith('#grading/') && location.hash.endsWith('?tab=rubrics')")
            # Quiz 13 has no paper yet: the notice says how its blank template comes (the finder needs one for scans without labels).
            expect(page.locator("#toast")).to_contain_text("Grading is set up for Quiz 13: add its rubrics now. It has no blank template yet: "
                                                           "register its PDF in Assignments, then prepare its blank template in Rubrics & Templates.")
            _, plan13 = plan_of(client, quiz13["id"])
            assert plan13 and plan13["entry"].get("set_up_before_papers"), plan13
            load(page, f"#exams/{created.json['id']}")
            tile = page.locator('.exams-stage[data-stage="grading"]')
            expect(tile).to_contain_text("Set up before the papers · finish the rubrics")
            expect(tile.get_by_role("button", name="Set up grading now")).to_have_count(0)
            # A date its Exams page records is shown on Create assignment's Details, naming where it is changed.
            record = client.get(f"/api/exams/{created.json['id']}").json
            assert client.post(f"/api/exams/{created.json['id']}/sittings", json={"version": record["version"], "label": "Quiz 13", "kind": "main",
                               "date": "2026-10-20", "time": "", "location": "", "room_id": None}, headers=HEADERS).status_code in (200, 201)
            load(page, f"#documents?assignment={quiz13['id']}&step=details")
            wizard = page.locator("#assignment-wizard")
            expect(wizard.locator("[data-given-recorded]")).to_have_text("Given on Tue, Oct 20, as its Exams page records. Change it there.")
            expect(wizard.get_by_label("Given on (optional)")).to_have_count(0)
            shots(page, "wizard-given-recorded", wizard.locator("[data-given-recorded]"))
            page.keyboard.press("Escape")

            # --- An assessment with no exam record: the hub sets it up for the assignment and term, making the record. ---
            quiz14 = client.post("/api/documents/assignments", json={"definition_id": definition, "key": "quiz-14", "title": "Quiz 14"}, headers=HEADERS).json
            load(page, f"#exams/assessment/{quiz14['id']}/{term}")
            tile = page.locator('.exams-stage[data-stage="grading"]')
            tile.get_by_role("button", name="Set up grading now").click()
            wait_until(page, "location.hash.startsWith('#grading/') && location.hash.endsWith('?tab=rubrics')")
            exam14, plan14 = plan_of(client, quiz14["id"])
            assert exam14 and plan14 and sorted(s["number"] for s in exam14["sections"]) == [507, 508], exam14

            # --- The Assignments card's ⋯ menu, for an assignment with no plan in its current term. ---
            quiz15 = client.post("/api/documents/assignments", json={"definition_id": definition, "key": "quiz-15", "title": "Quiz 15"}, headers=HEADERS).json
            load(page, "#documents?view=assignments")
            card = page.locator("#document-assignments article[data-assignment-card]").filter(has_text="Quiz 15")
            more = card.get_by_role("button", name="More actions for MATH 140 · Quiz 15")
            more.click()
            item = page.get_by_role("menuitem", name="Set up grading now")
            expect(item).to_be_visible()
            page.screenshot(path=str(SHOTS / "card-menu-1440.png"), animations="disabled")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            item.click()
            wait_until(page, "location.hash.startsWith('#grading/') && location.hash.endsWith('?tab=rubrics')")
            exam15, plan15 = plan_of(client, quiz15["id"])
            assert plan15 and plan15["entry"].get("set_up_before_papers") and exam15["sittings"] == [], (exam15, plan15)

            # --- Given on… on the card itself (requirement, 2026-10-03): the record's main sitting, through the Exams page's routes. ---
            load(page, "#documents?view=assignments")
            card = page.locator("#document-assignments article[data-assignment-card]").filter(has_text="Quiz 15")
            card.get_by_role("button", name="Given on date for MATH 140 · Quiz 15").click()
            dialog = page.locator("#assignment-given-on-form")
            expect(dialog.get_by_label("Given on")).to_have_value("")
            expect(dialog.locator("fieldset")).to_have_count(0)  # the term already has its exam record
            dialog.get_by_label("Given on").fill("2026-10-09")
            shots(page, "card-given-on", dialog)
            dialog.get_by_role("button", name="Save").click()
            expect(dialog).to_be_hidden()
            exam15, _ = plan_of(client, quiz15["id"])
            assert [(s["kind"], s["label"], s["date"]) for s in exam15["sittings"]] == [("main", "Quiz 15", "2026-10-09")], exam15["sittings"]
            expect(card.locator(".document-card-facts")).to_contain_text("Exam Fri, Oct 9")
            # Changed, then cleared: the same sitting each time, and the date comes off it.
            sitting = exam15["sittings"][0]["id"]
            card.get_by_role("button", name="Given on date for MATH 140 · Quiz 15").click()
            expect(dialog.get_by_label("Given on")).to_have_value("2026-10-09")
            dialog.get_by_label("Given on").fill("2026-10-12")
            dialog.get_by_role("button", name="Save").click()
            expect(dialog).to_be_hidden()
            card.get_by_role("button", name="Given on date for MATH 140 · Quiz 15").click()
            expect(dialog.get_by_label("Given on")).to_have_value("2026-10-12")
            dialog.get_by_label("Given on").fill("")
            dialog.get_by_role("button", name="Save").click()
            expect(dialog).to_be_hidden()
            exam15, _ = plan_of(client, quiz15["id"])
            assert [(s["id"], s["date"]) for s in exam15["sittings"]] == [(sitting, "")], exam15["sittings"]
            expect(card.locator(".document-card-facts")).not_to_contain_text("Exam ")
            # No exam record yet: it is made for the sections ticked in the dialog.
            quiz16 = client.post("/api/documents/assignments", json={"definition_id": definition, "key": "quiz-16", "title": "Quiz 16"}, headers=HEADERS).json
            load(page, "#documents?view=assignments")
            card = page.locator("#document-assignments article[data-assignment-card]").filter(has_text="Quiz 16")
            card.get_by_role("button", name="Given on date for MATH 140 · Quiz 16").click()
            expect(dialog.get_by_role("checkbox", name="Section 507")).to_be_checked()
            expect(dialog.get_by_role("checkbox", name="Section 508")).to_be_checked()
            dialog.get_by_role("checkbox", name="Section 508").uncheck()
            dialog.get_by_label("Given on").fill("2026-10-08")
            shots(page, "card-given-on-new-record", dialog)
            dialog.get_by_role("button", name="Save").click()
            expect(dialog).to_be_hidden()
            exam16, _ = plan_of(client, quiz16["id"])
            assert sorted(s["number"] for s in exam16["sections"]) == [507], exam16["sections"]
            assert [(s["kind"], s["label"], s["date"]) for s in exam16["sittings"]] == [("main", "Quiz 16", "2026-10-08")], exam16["sittings"]
            # A date a seating chart records is named with a link to the chart, never edited here.
            def chart_dated(route):
                body = route.fetch().json()
                for row in body["lifecycle"]:
                    if row["assignment_id"] == quiz16["id"]:
                        row.update(exam_date="2026-10-01", exam_time="7:05 PM", exam_date_chart_id="chart-x")
                route.fulfill(json=body)
            page.route("**/api/documents/catalog*", chart_dated)
            load(page, "#documents?view=assignments")
            card.get_by_role("button", name="Given on date for MATH 140 · Quiz 16").click()
            expect(dialog).to_contain_text("Given on Thu, Oct 1 · 7:05 PM, as its seating chart records. Change it on the chart.")
            expect(dialog.get_by_label("Given on")).to_have_count(0)
            expect(dialog.get_by_role("link", name="Open the seating chart")).to_have_attribute("href", "#seating/chart-x")
            page.keyboard.press("Escape")
            page.unroute("**/api/documents/catalog*")

            # ASSIGNMENT-WORKSPACE: reuse the course-wide paper in an explicitly selected,
            # noncurrent term. Neither setup nor its navigation may fall back to Fall.
            added = client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Spring", "year": 2027,
                                "sections": [509], "multiple_sections": False, "is_current": False}, headers=HEADERS)
            assert added.status_code == 201, added.json
            spring = next(item for item in client.get("/api/seating/catalog").json["sections"] if item["number"] == 509)["term_id"]
            load(page, f"#assignments/{quiz['id']}/{spring}?area=create")
            page.get_by_role("button", name="Add or revise paper", exact=True).click()
            expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text("Paper")
            wizard.get_by_role("button", name="Set up grading now", exact=True).click()
            wait_until(page, "location.hash.startsWith('#grading/') && location.hash.includes('tab=rubrics')")
            spring_exam, spring_plan = plan_of(client, quiz["id"], spring)
            assert spring_plan and spring_plan["id"] != initial_plan_id
            assert [item["number"] for item in spring_exam["sections"]] == [509]
            expect(page.locator("[data-assignment-nav]").get_by_role("link", name="Needs you & progress")).to_have_attribute(
                "href", f"#assignments/{quiz['id']}/{spring}")
            assert plan_of(client, quiz["id"], term)[1]["id"] == initial_plan_id
            assert not errors, errors
            context.close()
            browser.close()
        server.close()
    print("Set up grading now and Given on browser checks passed.")


if __name__ == "__main__":
    main()
