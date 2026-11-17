"""Student-chart email review, provider choice, and explicit request recovery.

Uses an isolated catalog, mocked failure responses, and a real preview/PDF/queue
integration phase. The mail worker never runs and no email transport is called.
Run: .venv/bin/python tests/browser_seating_student_mail.py
"""
import io
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from test_seating import SeatingTests
from test_seating_mail_merge import FakeGmail
from test_response_card_mail import FakeRelay
from response_cards.mail import MailConfig
from rosters import connect


def main():
    output = Path("test-results/seating-student-mail")
    output.mkdir(parents=True, exist_ok=True)
    fixture = SeatingTests("test_options_report_students_and_linked_dr_bookings")
    fixture.setUp()
    chart = fixture.create().json
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    endpoint = f"/api/seating/{chart['id']}/student-email"
    students = [row for row in chart["students"] if row["seat_id"]]
    recipients = [{**{key: row[key] for key in ("enrollment_id", "section_id", "name", "section_number", "seat")},
                   "email": f"student{index}@example.test"} for index, row in enumerate(students[:2])]
    skipped = [{**students[2], "reason": "Delivery unconfirmed; review before sending again."}]
    state = {"connected": True, "default_ready": True, "reply": "ok", "history": [], "queued": False}
    posts, previews = [], []

    def intercepted(route):
        request = route.request
        if request.method == "GET":
            query = parse_qs(urlparse(request.url).query)
            previews.append(query)
            audience, provider = query["audience"][0], query["mail_provider"][0]
            data = {"chart_id": chart["id"], "version": chart["version"], "title": chart["title"],
                    "code": chart["code"], "room_name": chart["room_name"], "audience": audience,
                    "mail_provider": provider, "mail": {"provider": "resend", "gmail": {
                        "connected": state["connected"], "email": "instructor@tamu.edu"}},
                    "sender": ("instructor@tamu.edu" if state["connected"] else "") if provider == "gmail" else ("Faculty PDF email service" if state["default_ready"] else ""),
                    "can_send": (state["connected"] if provider == "gmail" else state["default_ready"]) and not state["queued"],
                    "eligible_count": 0 if state["queued"] else 2, "skipped_count": 1,
                    "recipients": [] if state["queued"] else recipients, "skipped": skipped,
                    "fingerprint": f"{audience}-{provider}-saved-preview", "history": state["history"],
                    "subject": "Your seating chart for MATH 140 — Test 1"}
            route.fulfill(status=200, json=data)
            return
        assert request.method == "POST"
        payload = request.post_data_json
        posts.append(payload)
        assert set(payload) == {"audience", "mail_provider", "fingerprint", "request_id"}
        uuid.UUID(payload["request_id"])
        if state["reply"] == "stale":
            route.fulfill(status=409, json={"error": "The seating chart changed."})
        elif state["reply"] == "lost":
            route.fulfill(status=502, json={"error": "The queue receipt was lost."})
        else:
            state["queued"] = True
            state["history"] = [{**recipient, "status": "pending", "error": "", "created_at": "2026-09-17T12:00:00Z"}
                                for recipient in recipients]
            route.fulfill(status=202, json={"chart_id": chart["id"], "request_id": payload["request_id"],
                                           "queued": 2, "skipped_count": 1})

    def get_preview(response):
        return response.request.method == "GET" and urlparse(response.url).path == endpoint

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.set_default_timeout(10000)
            page.route(address + endpoint + "**", intercepted)
            page.add_init_script("""window.studentChartCsp = [];
                document.addEventListener('securitypolicyviolation', e => window.studentChartCsp.push(e.violatedDirective));""")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address + "/#seating/" + chart["id"])
            expect(page.locator(".seating-view")).to_be_visible()
            assert not chart["confirmed_at"]
            button = page.get_by_role("button", name="Email student charts", exact=True)
            expect(button).to_be_visible()
            with page.expect_response(get_preview):
                button.click()
            dialog = page.locator("#seating-student-email")
            audience = dialog.get_by_label("Student chart email audience", exact=True)
            provider = dialog.get_by_label("Student chart email provider", exact=True)
            send = dialog.locator("[data-student-email-send]")
            refresh = dialog.get_by_role("button", name="Refresh preview", exact=True)
            retry = dialog.get_by_role("button", name="Retry request", exact=True)
            expect(dialog).to_contain_text("Every other occupied seat is shaded and labelled Occupied")
            expect(dialog).to_contain_text("their seat labelled Your seat and the seat number underneath")
            expect(dialog).to_contain_text("Student names, response-card statuses, and exam versions are omitted")
            expect(audience).to_have_value("unsent")
            expect(provider).to_have_value("default")
            expect(dialog.locator("[data-student-email-sender]")).to_contain_text("Faculty PDF email service")
            expect(send).to_have_text("Send 2 emails")
            expect(send).to_be_enabled()
            expect(dialog.get_by_role("region", name="Student seating charts to email").locator("tbody tr")).to_have_count(2)
            assert posts == []
            link = dialog.get_by_role("link", name=f"Preview PDF for {recipients[0]['name']}")
            query = parse_qs(urlparse(link.get_attribute("href")).query)
            assert query == {"enrollment_id": [recipients[0]["enrollment_id"]], "audience": ["unsent"],
                             "mail_provider": ["default"], "fingerprint": ["unsent-default-saved-preview"]}
            assert link.get_attribute("data-view-file") == link.get_attribute("href") and link.get_attribute("target") is None  # the file viewer (VIEWER)
            expect(dialog).to_contain_text("No chart emails have been queued.")

            with page.expect_response(get_preview):
                provider.select_option("gmail")
            expect(dialog.locator("[data-student-email-sender]")).to_contain_text("instructor@tamu.edu")
            with page.expect_response(get_preview):
                audience.select_option("all")
            assert posts == []
            state["connected"] = False
            with page.expect_response(get_preview):
                refresh.click()
            expect(send).to_be_disabled()
            expect(provider).to_have_value("gmail")
            expect(dialog.locator("[data-student-email-connect]")).to_be_visible()
            state["connected"] = True
            with page.expect_response(get_preview):
                refresh.click()
            expect(send).to_be_enabled()

            page.screenshot(path=str(output / "desktop.png"))
            # The narrowest desktop window on a laptop-height screen (desktop only; there is no phone layout).
            page.set_viewport_size({"width": 1280, "height": 800})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
            expect(send).to_be_in_viewport()
            page.screenshot(path=str(output / "desktop-1280.png"))

            state["reply"] = "stale"
            send.click()
            expect(dialog.locator('[role="alert"]')).to_contain_text("Refresh the preview")
            expect(send).to_be_disabled()
            expect(retry).to_be_hidden()
            with page.expect_response(get_preview):
                refresh.click()
            state["reply"] = "lost"
            send.click()
            expect(retry).to_be_enabled()
            original = dict(posts[-1])
            expect(provider).to_be_disabled()
            expect(audience).to_be_disabled()
            expect(refresh).to_be_disabled()
            expect(send).to_be_disabled()
            assert len(posts) == 2
            dialog.get_by_role("button", name="Close", exact=True).click()
            preview_count = len(previews)
            button.click()
            expect(retry).to_be_enabled()
            expect(provider).to_have_value("gmail")
            expect(audience).to_have_value("all")
            assert len(previews) == preview_count and len(posts) == 2
            state["reply"] = "ok"
            retry.click()
            expect(dialog.locator("[data-student-email-result]")).to_contain_text("2 student chart emails queued")
            assert posts[-1] == original and len(posts) == 3
            expect(send).to_be_disabled()
            with page.expect_response(get_preview):
                refresh.click()
            history = dialog.get_by_role("region", name="Student chart email delivery history")
            expect(history.locator("tbody tr")).to_have_count(2)
            expect(history).to_contain_text("Queued")
            expect(dialog.locator("[data-student-email-connect]")).to_be_hidden()
            state["history"][0]["status"] = "accepted"
            state["history"][1].update(status="failed", error="The chart changed before delivery.")
            with page.expect_response(get_preview):
                refresh.click()
            expect(history).to_contain_text("Sent")
            expect(history).to_contain_text("Failed")
            expect(history).to_contain_text("The chart changed before delivery.")
            dialog.get_by_role("button", name="Close", exact=True).click()
            with page.expect_response(get_preview):
                button.click()
            expect(provider).to_have_value("default")
            expect(audience).to_have_value("unsent")

            # Exercise the actual browser/API/PDF/queue contract after the
            # mocked error cases, without running the worker or any transport.
            dialog.get_by_role("button", name="Close", exact=True).click()
            page.unroute(address + endpoint + "**", intercepted)
            mailer = fixture.app.extensions["response_cards"].mail
            mailer.gmail = FakeGmail()
            relay = FakeRelay()
            mailer.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4, provider="resend")
            mailer.transport = relay
            mail_service = fixture.app.extensions["seating_student_mail"]
            transport_calls = []

            def no_delivery(job, pdf):
                transport_calls.append(job["id"])
                raise AssertionError("The browser test must not run email delivery")

            mail_service.transport = no_delivery
            page.set_viewport_size({"width": 1440, "height": 1100})
            with page.expect_response(get_preview) as actual_preview:
                button.click()
            data = actual_preview.value.json()
            assert data["eligible_count"] == 7 and data["skipped_count"] == 2, data
            expect(send).to_have_text("Send 7 emails")
            expect(send).to_be_enabled()
            expect(provider).to_have_value("default")
            expect(provider.locator('option[value="gmail"]')).to_have_text("TAMU Gmail — instructor@tamu.edu")
            expect(dialog.get_by_role("region", name="Student seating charts to email").locator("tbody tr")).to_have_count(7)
            expect(dialog.locator("[data-student-email-sender]")).to_contain_text("Faculty PDF email service")
            actual_link = dialog.get_by_role("link", name=f"Preview PDF for {data['recipients'][0]['name']}")
            response = page.request.get(address + actual_link.get_attribute("href"))
            assert response.status == 200 and response.headers["content-type"] == "application/pdf", response.text()
            pdf = response.body()
            assert pdf.startswith(b"%PDF-")
            reader = PdfReader(io.BytesIO(pdf))
            text = " ".join(" ".join(sheet.extract_text() for sheet in reader.pages).split())
            assert "Your seat" in text and "Occupied" in text, text
            assert data["recipients"][0]["seat"] in text
            for student in chart["students"]:
                assert student["name"] not in text
                assert student["enrollment_id"] not in text
            (output / "actual-recipient.pdf").write_bytes(pdf)
            with connect(fixture.app.extensions["rosters"].path) as db:
                assert db.execute("SELECT COUNT(*) FROM seating_student_mail").fetchone()[0] == 0
            with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == endpoint) as queued:
                send.click()
            receipt = queued.value.json()
            assert queued.value.status == 200 and receipt["queued"] == 7, receipt
            expect(dialog.locator("[data-student-email-result]")).to_contain_text("7 student chart emails queued")
            with connect(fixture.app.extensions["rosters"].path) as db:
                jobs = list(db.execute("SELECT status,provider,pdf,snapshot_blob FROM seating_student_mail"))
                assert len(jobs) == 7 and all(job["status"] == "pending" for job in jobs)
                assert all(job["provider"] == "resend" and job["pdf"] is None and job["snapshot_blob"] for job in jobs)
                assert db.execute("SELECT COUNT(*) FROM response_card_mail").fetchone()[0] == 0
                assert not db.execute("SELECT confirmed_at FROM seating_charts WHERE id=?", (chart["id"],)).fetchone()[0]
            with page.expect_response(get_preview) as queue_preview:
                refresh.click()
            data = queue_preview.value.json()
            assert data["eligible_count"] == 0 and data["skipped_count"] == 9, data
            expect(send).to_be_disabled()
            expect(history.locator("tbody tr")).to_have_count(7)
            expect(history).to_contain_text("Queued")
            expect(dialog.locator("[data-student-email-connect]")).to_be_hidden()
            assert mailer.gmail.sent == [] and relay.sent == [] and transport_calls == []
            assert page.evaluate("window.studentChartCsp") == []
            assert not errors, errors
            browser.close()
        print("PASS: unconfirmed-chart action, non-mutating preview, provider/audience review, stale preview rejection, uncertain request recovery, narrow desktop/CSP, actual anonymous PDF, real seven-student queue and history without confirmation/cards. Worker and email transports never called.")
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
