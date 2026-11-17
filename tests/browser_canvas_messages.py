"""Messages to chosen sections through Canvas in a real browser (AGENTS.md "Canvas" MESSAGES): the Canvas page's Message students
opens the window with the term's linked sections ticked and "Send an individual message to each recipient" unticked; one section is
unticked, the review says exactly who it reaches and who is left out, Send makes ONE group conversation in the stand-in Canvas, the
page lists the message, and Check delivery reads Canvas. In a second window the individual box is ticked and the review says so.
The palette's link opens the same window. While Canvas is slow the window follows the send course by course. A message the
Assistant prepared waits in its drawer as the message itself, one line per section ("All students in 508 (1)") and nobody's name,
and its Send is the same explicit send (ASSISTANT "CANVAS MESSAGE CARD"); a card the model ticked individual on by itself is the
group send, and one ticked on the instructor's word says so on a warning and on Send. Desktop widths only (1440 and 1280), the one appearance.
Synthetic data only."""
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from canvas import web
from fake_canvas import FakeCanvas
from test_rosters import STUDENTS, csv_bytes
from test_storage import HEADERS

CAROL = ["", "Example, Carol", "222333444", "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@example.test"]


def main():
    shots = Path(os.environ.get("FACULTY_BROWSER_SHOTS") or tempfile.mkdtemp(prefix="canvas-messages-"))
    shots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        first = fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026C", "Fall 2026")
        second = fake.add_course("MATH-140-508: Mathematics for Business", "MATH-140-508-2026C", "Fall 2026")
        for course, people in ((first, [("Example, Alice", "001234567"), ("Example, Bob", "123456789"), ("Newcomer, Dana", "999888777")]),
                               (second, [("Example, Carol", "222333444")])):
            section = fake.add_section(course, course)
            for name, sis in people:
                fake.add_student(course, section, name, sis)
        fake.add_student(first, fake.sections[first][0]["id"], "Invited, Eli", "444555666", state="invited")
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        settings = web.Settings(session_file=os.path.join(directory, "config", "canvas-session.json"), base=fake.url)
        app.extensions["canvas"].settings = settings
        web.save(settings, fake.session(), user={"name": "Pat Instructor"})
        seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]
        for number, rows in ((507, STUDENTS), (508, [CAROL])):
            route = f"/api/course-groups/{group['id']}/sections/{number}/roster"
            assert client.post(route, data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS).status_code == 201
        sections = {s["number"]: s["section_id"] for s in client.get("/api/canvas/messages").json["terms"][0]["sections"]}
        for number, course in ((507, first), (508, second)):
            linked = client.post(f"/api/canvas/sections/{sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS)
            assert linked.status_code == 200, linked.json
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#canvas")
                action = page.locator("#primary-action")
                expect(action).to_have_text("Message students", timeout=20000)
                action.click()
                dialog = page.locator("#dialog")
                expect(dialog.get_by_role("heading", name="Message students through Canvas")).to_be_visible(timeout=20000)
                section_507 = dialog.get_by_role("checkbox", name="Section 507")
                section_508 = dialog.get_by_role("checkbox", name="Section 508")
                expect(section_507).to_be_checked()
                expect(section_508).to_be_checked()
                every = dialog.get_by_role("checkbox", name="MATH 140")
                expect(every).to_be_checked()
                expect(dialog.get_by_role("checkbox", name="Send an individual message to each recipient")).not_to_be_checked()
                section_508.uncheck()
                assert every.evaluate("box => box.indeterminate")
                dialog.get_by_role("button", name="Review").click()
                expect(dialog.locator(".form-error")).to_have_text("Write a subject.")
                dialog.get_by_label("Subject").fill("Exam 1 room")
                dialog.locator('textarea[name="body"]').fill("Exam 1 is in HECC 207.\nBring a pencil.")
                page.screenshot(path=str(shots / "compose-desktop.png"))
                dialog.get_by_role("button", name="Review").click()
                expect(dialog.get_by_role("heading", name="Review before sending")).to_be_visible(timeout=30000)
                expect(dialog.locator(".cm-reach")).to_contain_text("Reaches 3 students in 1 Canvas course, one group conversation in each")
                expect(dialog.locator(".cm-courses")).to_contain_text("one group conversation")
                expect(dialog).to_contain_text("1 student only Canvas lists")
                dialog.get_by_text("Left out on purpose").click()
                expect(dialog).to_contain_text("Canvas lists the enrollment as invited")
                expect(dialog).to_contain_text("Invited, Eli")
                assert "001234567" not in dialog.inner_text()
                assert not fake.sent_messages
                page.screenshot(path=str(shots / "review-desktop.png"))
                # Back to edit keeps what was written.
                dialog.get_by_role("button", name="Back to edit").click()
                expect(dialog.get_by_label("Subject")).to_have_value("Exam 1 room")
                expect(section_508).not_to_be_checked()
                dialog.get_by_role("button", name="Review").click()
                send = dialog.get_by_role("button", name="Send to 3 students")
                expect(send).to_be_visible(timeout=30000)
                send.click()
                expect(page.locator(".toast").last).to_contain_text("Sent through Canvas to 3 students.", timeout=30000)
                assert len(fake.sent_messages) == 1 and "bulk_message" not in fake.sent_messages[0], fake.sent_messages
                assert len(fake.conversations) == 1 and len(next(iter(fake.conversations.values()))["participants"]) == 4
                listed = page.locator(".cm-sent")
                expect(listed).to_contain_text("Exam 1 room", timeout=20000)
                expect(listed).to_contain_text("to 3 students · Section 507")
                expect(listed.locator(".chip")).to_have_text("Sent")
                listed.get_by_role("button", name="Check delivery").click()
                expect(dialog.get_by_role("heading", name="Exam 1 room")).to_be_visible()
                dialog.get_by_role("button", name="Check delivery in Canvas").click()
                expect(dialog).to_contain_text("In Canvas: every one of the 3 students has it.", timeout=30000)
                page.screenshot(path=str(shots / "record-desktop.png"))
                dialog.get_by_role("button", name="Close", exact=True).click()
                # The palette's link opens the same window, once.
                page.goto(f"{address}/#canvas?message=1")
                expect(dialog.get_by_role("heading", name="Message students through Canvas")).to_be_visible(timeout=20000)
                assert page.evaluate("location.hash") == "#canvas"
                dialog.get_by_role("button", name="Cancel").click()
                # A second window at a laptop's width (desktop only, one appearance): the window fits without sideways scrolling.
                laptop = browser.new_page(viewport={"width": 1280, "height": 900})
                laptop.on("pageerror", lambda error: errors.append(str(error)))
                laptop.goto(f"{address}/#canvas")
                expect(laptop.locator("#primary-action")).to_have_text("Message students", timeout=20000)
                laptop.locator("#primary-action").click()
                laptop_dialog = laptop.locator("#dialog")
                expect(laptop_dialog.get_by_role("checkbox", name="Section 507")).to_be_visible(timeout=20000)
                laptop_dialog.get_by_label("Subject").fill("Office hours moved")
                laptop_dialog.locator('textarea[name="body"]').fill("Office hours are on Thursday this week.")
                laptop_dialog.get_by_role("checkbox", name="Send an individual message to each recipient").check()
                # ATTACHMENTS (2026-10-09): Faculty's files open beside the message; a file from the computer goes with the review and
                # the send, uploaded once into the instructor's conversation attachments and named in every course's request.
                laptop_dialog.get_by_role("button", name="Attach from Faculty…").click()
                expect(laptop_dialog.locator("[data-cm-library]")).to_be_visible()
                laptop_dialog.locator("[data-cm-attach-file]").set_input_files({"name": "Office hours.pdf", "mimeType": "application/pdf",
                                                                               "buffer": b"%PDF-1.4 synthetic office hours"})
                expect(laptop_dialog.locator("[data-cm-attached]")).to_contain_text("Office hours.pdf")
                expect(laptop_dialog.locator("[data-cm-attached]")).to_contain_text("from your computer")
                laptop_dialog.get_by_role("button", name="Review").click()
                expect(laptop_dialog.locator(".cm-message")).to_contain_text("Office hours.pdf", timeout=30000)
                expect(laptop_dialog.locator(".cm-reach")).to_contain_text("each in their own conversation", timeout=30000)
                expect(laptop_dialog.get_by_role("button", name="Send to 4 students")).to_be_visible(timeout=30000)
                assert laptop.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "sideways scrolling at 1280 px"
                laptop.screenshot(path=str(shots / "review-1280.png"))
                assert len(fake.sent_messages) == 1
                # Canvas is slow: the window follows the send course by course, and says it can be closed.
                fake.hold_messages = threading.Event()
                laptop_dialog.get_by_role("button", name="Send to 4 students").click()
                expect(laptop_dialog.get_by_role("status")).to_contain_text("You can close this window; Faculty keeps sending", timeout=20000)
                expect(laptop_dialog.locator(".cm-courses .chip").first).to_have_text("Sending…")
                expect(laptop_dialog.get_by_role("button", name="Check delivery in Canvas")).to_have_count(0)
                assert laptop.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "sideways scrolling while sending"
                laptop.screenshot(path=str(shots / "sending-1280.png"))
                fake.hold_messages.set()
                expect(laptop.locator(".toast").last).to_contain_text("Sent through Canvas to 4 students.", timeout=30000)
                assert len(fake.sent_messages) == 3, fake.sent_messages  # 507 and 508, one request each
                assert [f["name"] for f in fake.message_files.values()] == ["Office hours.pdf"], fake.message_files
                assert all(m.get("attachment_ids") == list(fake.message_files) for m in fake.sent_messages[1:]), fake.sent_messages
                expect(laptop.locator(".cm-sent .chip").first).to_have_text("Sent", timeout=20000)
                # CANVAS MESSAGE CARD (requirement, 2026-10-12): a
                # message the Assistant prepared for both sections is drawn as the message, a line per section, nobody who gets it named.
                assistant = app.extensions["assistant"]
                answer, _ = assistant.run_tool(assistant.open_session("test"), "faculty_write", {
                    "method": "POST", "path": "/api/canvas/messages/send", "summary": "Remind both sections about the take-home quiz",
                    "body": {"sections": [sections[507], sections[508]], "subject": "Take-home quiz",
                             "body": "Hi everyone,\n\nPlease remember to bring your completed take-home quiz to class today.\n\nBest,\nChris"}})
                prepared = json.loads(answer)
                assert prepared["outcome"] == "waiting_for_approval", prepared
                page.goto(f"{address}/#canvas")
                page.locator(".assistant-launch").click()
                panel = page.locator("#assistant-panel")
                panel.get_by_role("tab", name="Changes", exact=False).click()
                card = panel.locator(f'[data-action-id="{prepared["action_id"]}"]')
                expect(card.locator("dd").first).to_contain_text("All students in 507 (2), and 1 student only Canvas lists", timeout=20000)
                expect(card.locator("dd").first).to_contain_text("All students in 508 (1)")
                expect(card.locator("blockquote")).to_contain_text("bring your completed take-home quiz")
                expect(card).to_contain_text("One group conversation in each Canvas course")
                expect(card.get_by_text("Left out on purpose")).to_be_visible()
                assert card.locator("details[open]").count() == 0  # Eli is on no roster here: nobody of a chosen section is left out
                shown = card.inner_html()
                assert not any(name in shown for name in ("Example, Alice", "Example, Bob", "Example, Carol", "Newcomer, Dana")), shown
                assert "preview says" not in shown and "001234567" not in shown, shown
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "sideways scrolling with the card open"
                card.screenshot(path=str(shots / "assistant-card.png"))
                card.get_by_role("button", name="Send to 4 students").click()
                expect(page.locator(".toast").last).to_contain_text("Approved and done.", timeout=30000)
                assert app.extensions["canvas_messages"].wait_idle(20)
                assert len(fake.sent_messages) == 5, fake.sent_messages  # one request for each of the two courses, once
                # GROUP BY DEFAULT (requirement, 2026-10-13): the model's own tick of
                # Canvas's individual box is taken off, so its card is the group send; ticked on the instructor's word, the card
                # says so on a warning and on Send itself. Declined here: nothing more goes to Canvas.
                ticked = {"method": "POST", "path": "/api/canvas/messages/send", "summary": "Remind both sections about the take-home quiz",
                          "body": {"sections": [sections[507], sections[508]], "subject": "Take-home quiz", "body": "Bring it tomorrow.", "individual": True}}
                unasked = json.loads(assistant.run_tool(assistant.open_session("test"), "faculty_write", ticked)[0])
                assert "`individual` was removed" in unasked["note"] and unasked["preview"]["result"]["individual"] is False, unasked
                session = assistant.open_session("test")
                chat = SimpleNamespace(session=session, wait_for_action=lambda ident: None,
                                       transcript=[("Instructor", "Send both sections the reminder, individually this time.")])
                with patch.object(assistant.conversations, "by_session", return_value=chat):
                    asked = json.loads(assistant.run_tool(session, "faculty_write", ticked)[0])
                assert asked["outcome"] == "waiting_for_approval" and "removed" not in asked["note"], asked
                page.reload()
                page.locator(".assistant-launch").click()
                panel.get_by_role("tab", name="Changes", exact=False).click()
                group_card = panel.locator(f'[data-action-id="{unasked["action_id"]}"]')
                expect(group_card.get_by_role("button", name="Send to 4 students")).to_be_visible(timeout=20000)
                expect(group_card).not_to_contain_text("Individual messages")
                card = panel.locator(f'[data-action-id="{asked["action_id"]}"]')
                expect(card.locator(".callout")).to_contain_text("Individual messages, not a group conversation")
                expect(card.locator(".callout")).to_contain_text("Each of the 4 students gets their own conversation")
                expect(card.get_by_role("button", name="Send 4 individual messages")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "sideways scrolling with the individual card open"
                card.scroll_into_view_if_needed()
                card.screenshot(path=str(shots / "assistant-card-individual.png"))
                card.get_by_role("button", name="Decline").click()
                group_card.get_by_role("button", name="Decline").click()
                assert len(fake.sent_messages) == 5, fake.sent_messages
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print(f"Canvas messages browser check passed; screenshots in {shots}")


if __name__ == "__main__":
    main()
