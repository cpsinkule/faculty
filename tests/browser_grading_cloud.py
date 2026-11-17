"""Cloud grading in the browser: exam versions, independent checks, trial acceptance and the gate. Real APIs, the local fake host."""
import sys
import threading
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from workspace_steps import open_step  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from course_storage import timestamp
from rosters import connect
import test_grading_acceptance as acceptance
from test_grading_pipeline import CORRECT
from unittest.mock import patch
from assignment_templates import read_verified
from test_assignment_templates import prepared

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = acceptance.AcceptanceCase("configure_acceptance")
fixture.setUp()
fixture.configure_acceptance(blind_share=0.0)
fixture.students(2)
fixture.grade(CORRECT)  # Two clear, correct responses: Cloud Standard, both graders agree, trial-accepted.
# The Rubrics & Templates step imports a generated version's saved PDF as its blank template the moment it opens. The fixture's
# version is synthetic and has no saved bytes, so that import answered 503 (as in browser_grading_intake and _templates, which
# stand in the same way): its canonical is synthetic bytes, and the sandboxed converter is the synthetic one of the template tests.
fixture.enterContext(patch('assignment_templates.read_verified', side_effect=lambda root, path, checksum:
    b'Synthetic canonical PDF' if path == 'canonical.pdf' else read_verified(root, path, checksum)))
fixture.enterContext(patch('document_identity.template_conversion.convert_template', side_effect=prepared))


def hand_out_versions():
    """The seating chart hands out Versions A and B. It happens AFTER the first grading: from then on the rubrics of both
    versions are owed, so the finalization those proposals were graded under no longer covers the exam."""
    with connect(fixture.path) as db:
        term = db.execute("SELECT term_id FROM course_sections WHERE id=?", (fixture.section["id"],)).fetchone()[0]
        now, room, chart = timestamp(), str(uuid.uuid4()), str(uuid.uuid4())
        db.execute("INSERT INTO rooms(id,name,created_at,updated_at) VALUES(?,?,?,?)", (room, "Fixture room", now, now))
        db.execute("INSERT INTO seating_charts(id,assignment_id,term_id,room_id,created_at,updated_at,exam_version_count) VALUES(?,?,?,?,?,?,2)", (chart, fixture.assignment["id"], term, room, now, now))
        for index, letter in enumerate("AB"):
            seat = str(uuid.uuid4())
            db.execute("INSERT INTO room_seats(id,room_id,label,grid_row,grid_column,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (seat, room, f"A{index + 1}", 1, index + 1, now, now))
            db.execute("INSERT INTO seating_exam_versions VALUES(?,?,?)", (chart, seat, letter))


server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
plan = fixture.plan["id"]
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)

        def tool(name):
            """Tools for several responses are folded below the response being graded; a re-render folds them again."""
            if not page.get_by_role("button", name=name, exact=True).is_visible():
                page.get_by_text("Review several responses together", exact=True).click()
            page.get_by_role("button", name=name, exact=True).click()

        # Review: a trial-accepted response says so, leads with the independent checks, and is confirmed by one ordinary look.
        page.goto(f"{address}/#grading/{plan}?tab=review&browse=all")
        expect(page.locator(".grading-review-queue")).to_contain_text("Trial-accepted · not final")
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator(".grading-warning").first).to_contain_text("Trial-accepted: this is a test, not a final grade.")
        checks = page.locator("[data-independent-checks]")
        expect(checks).to_contain_text("Low risk")
        checks.locator("summary").first.click()
        expect(checks).to_contain_text("Second grader · transcript only (beta, separate assessment): agrees with every proposed line")
        expect(checks).to_contain_text("no contradiction, 4 steps verified")
        expect(checks).to_contain_text("matches your key · work shown")
        expect(checks).to_contain_text("Nothing in the evidence argues against this proposal.")
        expect(checks).to_contain_text("Cloud · Primary grader: alpha-vision (alpha)")  # roles and tier in plain words (grading-review-15)
        tool("Trial acceptance")
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("2 accepted responses await your look")
        expect(dialog).to_contain_text("No blind reviews yet")
        expect(dialog).to_contain_text("Automatic acceptance: off (the default)")
        dialog.get_by_role("button", name="Turn on automatic acceptance for this scope", exact=True).click()
        page.get_by_label("Scores of this question may become final without my look").check()
        page.get_by_role("button", name="Turn on automatic acceptance", exact=True).click()
        expect(page.locator("#dialog [role=alert]")).to_contain_text("The evidence does not allow it yet: 0 overturned in 0 blind reviews")  # Impossible to enable without evidence.
        assert [status for status, _ in bad] == [409], bad
        bad.clear()
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        tool("Trial acceptance")
        page.locator("#dialog").get_by_role("button", name="Review accepted responses", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        page.get_by_role("button", name="Approve grade", exact=True).click()
        expect(page.locator(".grading-review-queue")).to_contain_text("Approved")
        assert [a["outcome"] for a in fixture.acceptances()].count("confirmed") == 1
        tool("Trial acceptance")
        expect(page.locator("#dialog")).to_contain_text("with the proposal visible: 0 overturned in 1")
        page.locator("#dialog").get_by_role("button", name="Done", exact=True).click()

        # Grade by answer group shows the trial acceptance and the independent agreement it relied on.
        tool("Grade by answer group")
        expect(page.locator("#dialog")).to_contain_text("Trial-accepted · your look is the test")
        expect(page.locator("#dialog")).to_contain_text("2 recorded votes")
        page.locator("#dialog").get_by_role("button", name="Done", exact=True).click()

        # Exam versions: progress per version, said plainly; imported questions are told once which version they describe.
        hand_out_versions()
        open_step(page, "rubrics")  # Setup: the former Assignments and Rubrics pages.
        # The chart changed outside this page (as from another tab): a step reuses the plan read for 10 s, so Refresh reads it now.
        page.locator('#grading-section [data-grading-action="refresh"]').click()
        panel = page.locator("[data-exam-versions]")
        expect(panel).to_contain_text("Version A has no rubrics, blank assignment and answer key yet.")
        expect(panel).to_contain_text("1 question was imported before versions existed")
        expect(page.locator(".grading-guide")).to_have_count(0)
        page.get_by_role("button", name="Upload rubrics", exact=True).first.click()
        expect(page.get_by_label("Rubric CSVs for Version A", exact=True)).to_be_visible()
        expect(page.get_by_label("Rubric CSVs for Version B", exact=True)).to_be_visible()  # Each known version has its own upload slot.
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        panel.get_by_role("button", name="Say which version these describe", exact=True).click()
        page.get_by_label("Assignment version", exact=True).select_option("A")
        page.get_by_role("button", name="Record version", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-exam-versions]")).to_contain_text("Version B has no rubrics, blank assignment and answer key yet.")
        expect(page.locator("[data-rubric-question] h3").first).to_contain_text("Question 1 · Version A")

        # A versioned question reads only papers whose version is confirmed: the earlier proposals are now stale, never silently regraded.
        assert {r["state"] for r in fixture.frq.responses(plan) if not r["review_status"]} == {"stale"}
        page.get_by_role("button", name="Compute settings", exact=True).click()
        page.get_by_role("tab", name="What holds a response back", exact=False).click()
        expect(page.get_by_label("Test automatic-acceptance rules")).to_be_checked()
        expect(page.locator('.grading-settings [name="escalation_enabled"]')).to_have_count(0)
        expect(page.locator(".grading-settings")).to_contain_text("new cloud teams finalize passing results automatically")
        # One appearance, desktop only (DESIGN-LANGUAGE): the review page never scrolls sideways at its desktop width.
        page.goto(f"{address}/#grading/{plan}?tab=review&browse=all")
        expect(page.locator(".grading-review-form")).to_be_visible()
        page.screenshot(path=str(OUTPUT / "grading-cloud-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # An enabled permission outlives the trial-accepted results it was measured on (review fix GRUN-F1): with every current
        # result withdrawn (as a batch reset does) no response carries an acceptance, and Trial acceptance, the one place the
        # permission is turned off, is still in its fold.
        first = fixture.acceptances()[0]
        with connect(fixture.path) as db:
            now, report, permission = timestamp(), str(uuid.uuid4()), str(uuid.uuid4())
            db.execute("INSERT INTO frq_benchmark_reports VALUES(?,?,?,?,?,?,?,?)", (report, plan, first["question_id"], first["scope_sha256"], first["scope_json"], "{}", "0" * 64, now))
            db.execute("INSERT INTO frq_acceptance_permissions VALUES(?,?,?,?,?,?,?,?)", (permission, plan, first["question_id"], first["scope_sha256"], report, .02, .05, now))
            db.execute("INSERT INTO frq_acceptance_permission_events(permission_id,kind,reason,actor,created_at) VALUES(?,'enabled','fixture','workspace',?)", (permission, now))
            db.execute("UPDATE frq_responses SET current_result_id=NULL WHERE assignment_id=?", (plan,))
        assert not any(r["acceptance"] for r in fixture.frq.responses(plan))
        page.goto(f"{address}/#grading/{plan}?tab=review&browse=all")
        expect(page.locator(".grading-review-form, .grading-review-work")).not_to_have_count(0)
        expect(page.get_by_role("button", name="Trial acceptance", exact=True, include_hidden=True)).to_have_count(1)  # In the closed fold.
        tool("Trial acceptance")
        expect(page.locator("#dialog")).to_contain_text("ON for this exact scope")
        page.locator("#dialog").get_by_role("button", name="Turn automatic acceptance off", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        with connect(fixture.path) as db:
            assert db.execute("SELECT kind FROM frq_acceptance_permission_events WHERE permission_id=? ORDER BY rowid DESC LIMIT 1", (permission,)).fetchone()[0] == "revoked"
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Cloud grading browser workflow passed: exam versions, independent checks, trial acceptance, the gate refusing without evidence, confirm by review, groups, settings, no sideways scroll on the desktop.")
except Exception:
    print("Browser errors:", errors if "errors" in globals() else [])
    print("HTTP errors:", bad if "bad" in globals() else [])
    raise
finally:
    server.close()
    fixture.host.stop()
    fixture.doCleanups()
