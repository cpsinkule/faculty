"""ABSENCES EMAIL-PREFILL: Mail → Claude/Codex → editable form → explicit save and documentation, with synthetic data only."""
import json
import os
import sys
import threading
from pathlib import Path
from unittest import mock

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from rosters import connect
from test_absence_drafting import ANSWER, DraftTests, choose, fake_binaries
from test_storage import HEADERS


def main():
    fixture = DraftTests("test_unknowns_stay_blank_and_invalid_model_output_is_refused")
    fixture.setUp()
    client, app = fixture.client, fixture.app
    with connect(app.config["DATABASE"]) as db:
        term = db.execute("SELECT id FROM course_terms LIMIT 1").fetchone()[0]
    setup = client.post(f"/api/gradebook/terms/{term}/setup", headers=HEADERS, json={"base_revision": None,
        "request_id": "setup", "setup": {"items": [{"title": "Quiz", "points": "10", "due_date": "2026-09-22"}]}})
    assert setup.status_code == 200, setup.json
    server = create_server(app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    try:
        with mock.patch.dict(os.environ, fake_binaries(fixture.temp.name)), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("window.cspViolations=[];document.addEventListener('securitypolicyviolation',e=>window.cspViolations.push(e.violatedDirective));")
            for kind in ("agent_sdk", "codex_sdk"):
                choose(client, kind)
                page.goto(address + "/#mail?thread=a1")
                page.get_by_role("button", name="Draft absence", exact=True).click()
                form = page.locator("#absence-new-form")
                expect(form).to_be_visible(timeout=15000)
                expect(form).to_contain_text("Example, Alice · Section 507")
                expect(form.locator("[name=first_date]")).to_have_value(ANSWER["first_date"])
                # ABSENCE CHECK v3 (2026-10-09): Jev is off here, so the bare "Wednesday" is blanked with both candidates named.
                expect(form.locator("[name=last_date]")).to_have_value("")
                expect(form).to_contain_text("Jev could not tell whether “Wednesday” in the email is Wednesday, September 16 or Wednesday, September 23.")
                expect(form.locator("[name=reason]")).to_have_value("illness")
                expect(form.locator("[name=details]")).to_have_value(ANSWER["details"])
                expect(form.locator("[name=status][value=pending]")).to_be_checked()
                expect(form.locator("[data-mail-document]")).to_be_checked()
                assert client.get("/api/absences").json["absences"] == []
                if kind == "agent_sdk":
                    form.get_by_role("button", name="Cancel", exact=True).click()
                    expect(form).not_to_be_visible()
                    assert client.get("/api/absences").json["absences"] == []
                else:
                    form.locator("[name=details]").fill("Reviewed and corrected by instructor")
                    form.locator("[name=last_date]").fill(ANSWER["last_date"])  # the instructor picks the Wednesday Jev could not
                    form.locator("[name=status][value=excused]").check()
                    form.get_by_role("button", name="Record absence", exact=True).click()
                    expect(page.locator("#dialog")).to_be_hidden(timeout=15000)
                    expect(page.locator("#toast .toast-item[data-tone=success]")).to_contain_text("Absence recorded")
                    expect(page.locator("#absence-form")).to_have_count(0)
                    absence = client.get("/api/absences").json["absences"][0]
                    assert absence["details"] == "Reviewed and corrected by instructor"
                    assert absence["status"] == "excused"
                    assert client.get(f"/api/gradebook/terms/{term}").json["cells"] == {}
                    page.goto(address + "/#absences?status=all")  # excused: the page opens on Waiting
                    page.locator(f"[data-absence='{absence['id']}']").click()
                    detail = page.locator("#absence-form")
                    expect(detail).to_be_visible()
                    expect(detail.locator(".absence-docs")).to_contain_text("note.pdf")
                    detail.get_by_role("button", name="Close", exact=True).click()

            # A model with missing facts cannot silently supply today's dates or the illness default.
            blank = {**ANSWER, "first_date": "", "last_date": "", "reason": ""}
            fake_binaries(fixture.temp.name, blank)
            choose(client, "agent_sdk")
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(address + "/#mail?thread=a1")
            page.get_by_role("button", name="Draft absence", exact=True).click()
            form = page.locator("#absence-new-form")
            expect(form).to_be_visible(timeout=15000)
            expect(form.locator("[name=first_date]")).to_have_value("")
            expect(form.locator("[name=last_date]")).to_have_value("")
            expect(form.locator("[name=reason]")).to_have_value("")
            form.get_by_role("button", name="Record absence", exact=True).click()
            expect(form.locator(".form-error")).to_contain_text("Fill in both dates")
            assert len(client.get("/api/absences").json["absences"]) == 1
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "The 1280px desktop view overflows"
            form.get_by_role("button", name="Cancel", exact=True).click()

            # A late answer after Cancel must never open a replacement form or record anything.
            entered, released = threading.Event(), threading.Event()
            def delayed(engine, conversation, text):
                entered.set()
                released.wait(5)
                conversation.emit("assistant", text=json.dumps(ANSWER))
            with mock.patch("assistant.engines.ClaudeEngine.turn", delayed):
                page.get_by_role("button", name="Draft absence", exact=True).click()
                expect(page.locator("#absence-draft-form")).to_be_visible()
                assert entered.wait(5)
                page.locator("#absence-draft-form").get_by_role("button", name="Cancel", exact=True).click()
                released.set()
                page.wait_for_timeout(1200)
                expect(page.locator("#absence-new-form")).to_have_count(0)
                assert len(client.get("/api/absences").json["absences"]) == 1
            assert not errors, errors
            assert page.evaluate("window.cspViolations") == []
            assert not fixture.writes()
            browser.close()
    finally:
        server.close()
        fixture.doCleanups()
    print("Absence email prefill passed: selected Claude/Codex, editable fields, explicit save, documentation, missing facts, cancel and the 1280px desktop width.")


if __name__ == "__main__":
    main()
