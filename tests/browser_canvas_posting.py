"""POSTING GRADES in a real browser (docs/decisions/canvas.md; requirement, 2026-10-06). Two sections, each linked to its own Canvas course whose
Quiz 1 Canvas posts by hand: after a sync the column's heading says how many grades are hidden in Canvas; Post in the column's menu
asks nothing of Canvas and puts "posts at sync" on the heading; Sync with Canvas lists it under Posting in Canvas and posts every
section's grades after sending; Setup's categories carry their posting rule, by hand when the category does not count. ONE NOTICE
and CONFIRMED BY A LATER READ (requirement, 2026-10-10): a morning sync whose grade Canvas had not applied when Faculty stopped
waiting leaves ONE warning with one Sync with Canvas button, and it goes by itself once a later round reads the grade in Canvas.
Synthetic data only, against the loopback FakeCanvas."""
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from canvas_checks import canvas_linked_app
from fake_canvas import FakeCanvas
from gradebook.service import GradebookService
from test_storage import HEADERS

ALICE, BOB, CAROL = "001234567", "123456789", "222333444"
MORNING = datetime(2026, 10, 5, 10, 5, tzinfo=timezone.utc)  # 5:05 am campus time: inside the morning sync's hours


def ended(client, base, send):
    """A send followed until it and the posts after it are over."""
    for _ in range(400):
        if not send:
            return
        seen = client.get(f"{base}/canvas-send/{send}").json
        if seen["status"] != "running" and not seen.get("posting"):
            return
        time.sleep(0.05)
    raise AssertionError("the send did not end")


def typed(client, base, quiz, name, points):
    """One score typed into Quiz 1 for the student named."""
    view = client.get(base).json
    row = next(student["row"] for student in view["students"] if student["name"] == name)
    cell = (view["cells"].get(quiz) or {}).get(row)
    answer = client.post(base + "/scores", json={"request_id": f"typed-{time.monotonic_ns()}", "changes": [
        {"item_id": quiz, "row": row, "base": cell["id"] if cell else None, "status": "scored", "points": points}]}, headers=HEADERS)
    assert answer.status_code == 200, answer.json


def main():
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        course_a = fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026C", "Fall 2026")
        course_b = fake.add_course("MATH-140-508: Mathematics for Business", "MATH-140-508-2026C", "Fall 2026")
        section_a, section_b = fake.add_section(course_a, "507"), fake.add_section(course_b, "508")
        alice = fake.add_student(course_a, section_a, "Example, Alice", ALICE)
        fake.add_student(course_a, section_a, "Example, Bob", BOB)
        carol = fake.add_student(course_b, section_b, "Example, Carol", CAROL)
        quiz_a = fake.add_assignment(course_a, "Quiz 1", 10, group="Quizzes", post_manually=True)
        quiz_b = fake.add_assignment(course_b, "Quiz 1", 10, group="Quizzes", post_manually=True)
        fake.grade(course_a, quiz_a, alice, 9, posted=False)
        fake.grade(course_b, quiz_b, carol, 7, posted=False)
        # A student who left course A: Canvas keeps the hidden grade but never lists or posts it, so it never counts (2026-10-07).
        fake.grade(course_a, quiz_a, fake.add_student(course_a, section_a, "Left, Eve", "555666777", state="inactive"), 4, posted=False)
        app, client, group, term, base = canvas_linked_app(directory, fake, course_a, course_b)
        # Start from Canvas: Quiz 1 becomes a column linked to both courses' assignment, and Faculty reads that 2 grades are hidden.
        plan = client.post(base + "/canvas-sync/preview", json={"options": {}}, headers=HEADERS).json
        started = client.post(base + "/canvas-sync", json={"options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": "start-sync-1",
                                                           "confirmation": "sync"}, headers=HEADERS)
        assert started.status_code == 200, started.json
        for _ in range(200):
            send = started.json.get("send")
            if not send or client.get(f"{base}/canvas-send/{send['id']}").json["status"] != "running":
                break
            time.sleep(0.05)
        view = client.get(base).json
        quiz = next(item["id"] for item in view["setup"]["items"] if item["title"] == "Quiz 1")
        assert view["canvas_posting"][quiz]["hidden"] == 2, view.get("canvas_posting")
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                dialog = page.locator("#dialog")
                page.goto(f"{address}/#course/{group['id']}/section/507")
                head = page.locator(f'th[data-item="{quiz}"]')
                expect(head.locator("[data-head-hidden]")).to_have_text("2 hidden", timeout=20000)
                expect(head.locator("[data-head-pending]")).to_have_count(0)  # by hand, and nobody chose
                # The column's menu: Post in Canvas at each sync. Nothing is asked of Canvas; the heading says the next sync posts it.
                head.locator("[data-column-menu]").click()
                menu = page.locator("#gradebook-column-menu")
                expect(menu.locator("[data-canvas-hidden]")).to_contain_text("2 grades hidden from students in Canvas")
                expect(menu.locator("[data-canvas-hidden]")).to_contain_text("its category posts by hand")
                menu.get_by_role("button", name="Post in Canvas at each sync…").click()
                expect(dialog.get_by_role("heading", name="Post in Canvas at each sync")).to_be_visible(timeout=20000)
                expect(dialog.locator(".callout").first).to_contain_text("Students see these grades from the next sync")
                submit = dialog.locator('button[type="submit"]')
                expect(submit).to_have_attribute("data-outward", "")
                submit.click()
                expect(dialog).to_be_hidden(timeout=20000)
                expect(head.locator("[data-head-pending]")).to_have_text("posts at sync", timeout=20000)
                assert not any(w[1] == "/api/graphql" for w in fake.writes), "a choice asks nothing of Canvas"
                # Sync with Canvas: the summary names the post and why, and posts every section once its send is over.
                page.locator(".page-heading .page-heading-secondary").get_by_role("button", name="Sync with Canvas", exact=True).click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                expect(dialog.locator(".gbd-summary")).to_contain_text("Posts 1 column in Canvas after sending", timeout=30000)
                expect(dialog.locator(".callout", has_text="Students see these grades")).to_contain_text("Posts “Quiz 1” in Canvas after sending")
                fold = dialog.locator('details[data-keep="posting"]')
                fold.locator("summary").click()
                # The term's first write to Canvas from a section's page goes to that section's course alone ("one real section first").
                expect(fold).to_contain_text("Posts 1 hidden grade")
                expect(fold).to_contain_text("your choice")
                sync = dialog.locator('button[type="submit"]')
                expect(sync).to_contain_text("posts 1 column")
                sync.click()
                expect(dialog).to_contain_text("Posted “Quiz 1” in Canvas: students see its grades in 1 section.", timeout=30000)
                # The window says the whole sync is over (requirement, 2026-10-08), and
                # its footer offers Close; the header's ✕ keeps its icon.
                state = dialog.locator("[data-sync-state]")
                expect(state).to_have_attribute("data-sync-state", "finished", timeout=30000)
                expect(state.locator("strong").first).to_have_text("Sync finished")
                expect(dialog.locator(".gbd-footer [data-action='close']")).to_have_text("Close")
                expect(dialog.locator(".dialog-header [data-action='close']")).to_have_text("")
                assert fake.submissions[course_a][(alice, quiz_a)]["posted_at"], fake.submissions[course_a]
                assert not fake.submissions[course_b][(carol, quiz_b)]["posted_at"], fake.submissions[course_b]
                dialog.get_by_role("button", name="Close", exact=True).click()
                expect(head.locator("[data-head-hidden]")).to_have_text("1 hidden", timeout=20000)
                expect(head.locator("[data-head-pending]")).to_have_text("posts at sync")
                # The next sync goes to every course and posts section 508's too. SYNC WITH CANVAS, A LOST ANSWER (instructor,
                # 2026-10-31): Faculty syncs, but its answer is lost on the way back (a proxy's empty 502); the window asks Faculty
                # what the sync did and follows it as if the answer had come.
                lost = []

                def lose(route):
                    route.fetch(timeout=60000)
                    lost.append(route.request.method)
                    route.fulfill(status=502, body="")

                page.route("**/canvas-sync", lose)
                page.locator(".page-heading .page-heading-secondary").get_by_role("button", name="Sync with Canvas", exact=True).click()
                expect(dialog.locator(".gbd-summary")).to_contain_text("Posts 1 column in Canvas after sending", timeout=30000)
                dialog.locator('button[type="submit"]').click()
                expect(dialog).to_contain_text("Posted “Quiz 1” in Canvas: students see its grades in 1 section.", timeout=30000)
                assert lost == ["POST"], lost
                expect(dialog).not_to_contain_text("could not be read")
                page.unroute("**/canvas-sync")
                assert fake.submissions[course_b][(carol, quiz_b)]["posted_at"], fake.submissions[course_b]
                dialog.get_by_role("button", name="Close", exact=True).click()
                expect(head.locator("[data-head-hidden]")).to_have_count(0, timeout=20000)
                expect(head.locator("[data-head-pending]")).to_have_count(0)
                # Chosen: the menu offers Hide and going back to the category's posting, not Post again.
                head.locator("[data-column-menu]").click()
                menu = page.locator("#gradebook-column-menu")
                expect(menu.get_by_role("button", name="Back to the category’s posting…")).to_be_visible()
                expect(menu.get_by_role("button", name="Post in Canvas at each sync…")).to_have_count(0)
                page.keyboard.press("Escape")
                # ONE NOTICE, CONFIRMED BY A LATER READ. The instructor's Sync sends Alice's 10; then a morning sync sends her 8,
                # which Canvas takes and has not applied when Faculty stops waiting: its grade is uncertain and its post withheld.
                typed(client, base, quiz, "Example, Alice", "10")
                plan = client.post(base + "/canvas-sync/preview", json={"options": {}}, headers=HEADERS).json
                sent = client.post(base + "/canvas-sync", json={"options": plan["options"], "fingerprint": plan["fingerprint"],
                                                               "request_id": "send-ten-1", "confirmation": "sync"}, headers=HEADERS)
                assert sent.status_code == 200, sent.json
                ended(client, base, sent.json["send"]["id"])
                assert fake.submissions[course_a][(alice, quiz_a)]["score"] == 10.0, fake.submissions[course_a]
                typed(client, base, quiz, "Example, Alice", "8")
                morning = app.extensions["canvas_nightly"]
                fake.hold_grades = True
                with patch.object(GradebookService, "SEND_WAIT", 0.1):
                    assert morning.round(MORNING) == {term: "synced"}
                    ended(client, base, client.get(base).json["canvas"]["nightly"]["send"]["id"])
                fake.hold_grades = False
                page.reload()
                left = page.locator("[data-gradebook-sync]")
                expect(left).to_have_count(1, timeout=20000)
                expect(left).to_contain_text("The last sync could not confirm 1 grade in Canvas, and 1 column was not posted or hidden there.")
                expect(left.get_by_role("button", name="Sync with Canvas")).to_have_count(1)
                expect(page.locator("[data-gradebook-nightly]")).to_have_count(0)  # said once, by the last sync's notice
                # Canvas catches up: the next round reads the grade there, and nothing is left to say or to post.
                assert fake.apply_held_grades() == 1
                asked = sum(1 for w in fake.writes if w[1] == "/api/graphql")
                assert morning.round(MORNING + timedelta(minutes=5)) == {}
                assert sum(1 for w in fake.writes if w[1] == "/api/graphql") == asked, "her grade already showed: nothing to post"
                page.reload()
                expect(head.locator("[data-head-hidden]")).to_have_count(0, timeout=20000)
                expect(page.locator("[data-gradebook-sync]")).to_have_count(0)
                expect(page.locator("[data-gradebook-nightly]")).to_have_count(0)
                # Setup: each category says how its columns are posted; one that does not count is always by hand.
                page.get_by_role("link", name="Setup").click()
                cell = page.locator('select[data-field="cat.posting"]').first
                expect(cell).to_have_value("manual", timeout=20000)
                cell.select_option("auto")
                expect(page.locator('input[data-field="cat.posting-due"]').first).to_be_checked()
                expect(page.locator('input[data-field="cat.posting-certified"]').first).to_be_checked()
                page.locator('input[data-field="mode"][value="weighted"]').check()
                expect(page.locator(".gbs-categories")).to_contain_text("By hand (doesn’t count)")  # Quizzes weighs 0
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Canvas posting browser check passed")


if __name__ == "__main__":
    main()
