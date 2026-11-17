"""SCHEDULING "DR REQUEST EMAILS" in the browser: the DR center's request email opens the student's Follow-up with its upload card;
Approve on the Follow-up page opens AIM's sign-in window on the session the approval started, Duo's code shows there, and the
window's end says the copy was uploaded. AIM's browser is a stand-in; the fixture is tests/test_dr_requests.py's."""
import logging
import threading
from unittest import mock

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_dr_requests import ACCOUNT, ALICE, SENDER, DrRequestsTest, request_text

from scheduling import aim_send


class Browser:
    """AIM's sign-in as the window sees it: Duo's code for a few reads, then the send's end."""
    id = "s-upload-1"
    sends = True

    def __init__(self, portal, netid, password, *, recorder, quizzes, importer):
        self.reads, self.state = 0, "starting"

    def status(self):
        self.reads += 1
        if self.reads < 4:
            self.state = "signing_in"
            return {"id": self.id, "state": "signing_in", "step": "duo_code", "code": "246810", "message": "", "place": "", "needs": "",
                    "show_page": False, "frame": 0, "width": 1100, "height": 760, "files": [], "problems": [], "send": True, "sent": []}
        self.state = "done"
        return {"id": self.id, "state": "done", "step": "aim", "code": "", "message": "", "place": "Sent to AIM", "needs": "",
                "show_page": False, "frame": 0, "width": 1100, "height": 760, "files": [], "problems": [], "send": True,
                "sent": [{"key": "k", "title": "Quiz 5", "section": 507, "date": "2026-10-23", "did": "uploaded", "detail": "1 request"}],
                "imported": None}

    def watch(self):
        pass

    def close(self):
        pass

    def expired(self):
        return False


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    fixture = DrRequestsTest()
    fixture.setUp()
    server = None
    try:
        fixture.inbox_mail({"d1": [(("e1", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(ALICE)), {})]})
        while fixture.turn():
            pass
        patches = [mock.patch.object(aim_send, "browser_class", return_value=Browser),
                   mock.patch("aim_import.saved_sign_in", return_value=("netid1", "secret"))]
        for patcher in patches:
            patcher.start()
            fixture.addCleanup(patcher.stop)
        server = create_server(fixture.app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address + "/#follow-ups")
            page.get_by_text("Upload Quiz 5's accessible copy to AIM").first.click()
            card = page.locator('[data-endpoint="scheduling.aim_upload"]').first
            expect(card).to_be_visible()
            card.locator("[data-assistant-approve]").click()
            dialog = page.locator("#dialog")
            expect(dialog.get_by_role("heading", name="Upload to AIM")).to_be_visible()
            expect(dialog.get_by_text("246810")).to_be_visible()
            expect(page.get_by_text("Uploaded the accessible copy of Quiz 5 to AIM (1 request).")).to_be_visible(timeout=15000)
            assert not errors, errors
            browser.close()
        print("browser_dr_requests: OK")
    finally:
        if server:
            server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
