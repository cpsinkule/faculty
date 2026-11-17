"""Create assignment in the real browser: generated and attached seating charts, the name choice, and printing.

Run from the project root: python tests/browser_assignment_wizard.py
"""
import io
import json
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_seating import seed
from test_dr_exams import booking, workbook as dr_workbook
from test_seating import HEADERS


def import_dr(app, client, rows):
    content = dr_workbook(rows)
    preview = client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None)}, headers=HEADERS)
    assert preview.status_code == 200, preview.json
    submitted = client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None),
                            "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
    assert submitted.status_code == 202, submitted.json
    assert app.extensions["inbox"].process_one()


# The Paper step lists the demonstration fixtures only on a page marked for testing (print-14); this script registers them.
TESTING = ("document.documentElement && (document.documentElement.dataset.testing = '1');"
           "document.addEventListener('DOMContentLoaded', () => { document.documentElement.dataset.testing = '1'; });")
PRIMARY = re.compile(r"\bprimary\b")


def fits(page, label):
    """The window and its body stay within the narrowest desktop width (DESIGN-LANGUAGE: desktop only)."""
    for width in (1280,):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{label} overflows the page at {width}"
        assert page.evaluate("(() => { const body = document.querySelector('#assignment-wizard .dialog-body'); return body.scrollWidth <= body.clientWidth + 1; })()"), f"{label} overflows the window at {width}"
    page.set_viewport_size({"width": 1440, "height": 1000})


def step(wizard, name):
    # Paper's Next prepares the term's grading templates from the registered versions before Stamp and print (ASSIGNMENT-WORKSPACE,
    # DOCUMENTS), a template conversion that can take longer than the default wait on a loaded machine.
    expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text(name, timeout=60000)


def to_list(page):
    """Assignments (ASSIGNMENT-WORKSPACE), the sidebar entry whose header holds Create assignment's one button."""
    page.locator(".sidebar").get_by_role("link", name="Assignments", exact=True).click()
    expect(page).to_have_url(re.compile(r"#assignments$"))
    # The list composes every assignment's progress (/api/assignments), slower than one page's catalog on a loaded machine.
    expect(page.locator("#assignment-workspace [data-assignment-list]")).to_be_visible(timeout=60000)


def create_area(page, title):
    """An assignment's Create area, from the list: its paper, printing and print jobs (ASSIGNMENT-WORKSPACE)."""
    to_list(page)
    link = page.locator("[data-assignment-list]").get_by_role("link", name=title, exact=True)
    try:
        expect(link).to_be_visible(timeout=60000)
    except AssertionError:
        page.screenshot(path="test-results/assignment-list-failure.png", full_page=True)
        print(page.locator('#assignment-workspace').inner_text(), flush=True)
        raise
    link.click()
    page.locator("[data-assignment-nav]").get_by_role("link", name="Create", exact=True).click()
    expect(page).to_have_url(re.compile(r"#assignments/[^/]+/[^/?]+\?area=create$"))
    area = page.locator("#assignment-workspace [data-assignment-body]")
    expect(area.get_by_role("heading", name="Paper and printing")).to_be_visible()
    return area


def details(page, key, title, untick=()):
    to_list(page)
    page.get_by_role("button", name="Create assignment", exact=True).click()
    page.get_by_role("button", name="Create paper and print", exact=True).click()
    wizard = page.locator("#assignment-wizard")
    step(wizard, "Details")
    wizard.get_by_text("Advanced: permanent assignment key", exact=True).click()
    wizard.get_by_label("Assignment key", exact=True).fill(key)
    wizard.get_by_label("Assignment title", exact=True).fill(title)
    for number in untick:
        wizard.get_by_label(f"Section {number}").uncheck()
    wizard.get_by_label("Arrange assigned seats for this assessment", exact=True).check()
    wizard.get_by_role("button", name="Next").click()
    step(wizard, "Seating")
    return wizard


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = seed(app)
        # The DR center's name for an exam created its own assignment, and a chart was made under it for Section 507.
        import_dr(app, client, [booking(**{"Exam Type": "Midterm Exam"})])
        catalog = client.get("/api/seating/catalog").json
        midterm = next(item for item in catalog["assignments"] if item["title"] == "Midterm Exam")
        sections = {item["number"]: item["id"] for item in catalog["sections"]}
        existing = client.post("/api/seating", json={"assignment_id": midterm["id"], "section_ids": [sections[507]], "room_id": catalog["rooms"][0]["id"]}, headers=HEADERS)
        assert existing.status_code == 201, existing.json
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.add_init_script(TESTING)
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                page.goto(address + "/#documents")

                # --- Generate new charts: the sections come from the first window. ---
                wizard = details(page, "quiz-9", "Quiz 9")
                attach = wizard.get_by_role("button", name="Attach existing seating chart")
                generate = wizard.get_by_label("Generate new seating charts")
                expect(attach).to_be_enabled()
                expect(generate).to_be_enabled()
                expect(attach).to_be_enabled()
                expect(generate).to_be_enabled()
                wizard.get_by_role("button", name="Next").click()
                expect(wizard.locator(".dialog-body > .form-error")).to_contain_text("Attach an existing seating chart or tick Generate")
                generate.check()
                expect(wizard.locator(".dialog-body > .form-error")).to_be_hidden()
                page.screenshot(path=str(output / "assignment-wizard-seating.png"))
                wizard.get_by_role("button", name="Next").click()
                create = page.locator("#seating-create")
                expect(create.locator('select[name="assignment_id"] option')).to_have_text(["MATH 140 · Quiz 9"])
                expect(create.locator("select[data-section]")).to_have_count(2)
                create.get_by_role("button", name="Back").click()
                step(wizard, "Seating")
                expect(wizard.get_by_label("Generate new seating charts")).to_be_checked()
                wizard.get_by_role("button", name="Next").click()
                for number in (507, 508):
                    create.get_by_label(f"Room for Section {number}").select_option(label=create.get_by_label(f"Room for Section {number}").locator("option").nth(1).inner_text())
                create.get_by_role("button", name="Create seating chart and continue").click()
                step(wizard, "Paper")
                assert page.evaluate("location.hash") == "#assignments", "The window should not leave the assignments page"
                # Going back shows the charts just generated as the attached plan, and Next accepts it.
                wizard.get_by_role("button", name="Back").click()
                step(wizard, "Seating")
                generated = wizard.locator('.wizard-plan:has(> span > strong:text-is("Quiz 9"))')
                expect(generated.locator("input")).to_be_checked()
                expect(generated).to_contain_text("Seats exactly the ticked sections.")
                expect(wizard.get_by_label("Generate new seating charts")).to_be_disabled()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                # A seated exam without paper starts on stamps only; LaTeX, with its starter files, is one click away.
                expect(wizard.get_by_label("Printed elsewhere: label each paper (stamps only)")).to_be_checked()
                wizard.get_by_label("Faculty prints it from LaTeX").check()
                expect(wizard.get_by_role("link", name="Download starter main.tex")).to_have_attribute("href", "/api/documents/starter/main.tex")
                expect(wizard.get_by_role("link", name="Download facultyassignment.cls")).to_have_attribute("href", "/api/documents/starter/facultyassignment.cls")
                expect(wizard.locator('select[name="source"] optgroup[label="Demonstration (testing)"] option')).to_have_count(2)
                # Grading templates are no longer prepared here: one line points to Grading.
                expect(wizard.get_by_role("button", name="Manage grading templates")).to_have_count(0)
                # Leaving Seating created the assignment and prepared its term's grading plan (ASSIGNMENT-WORKSPACE, DOCUMENTS): the
                # line says so and continues to its questions from a secondary button; the step's one primary stays Next, and
                # nothing links to a Grading page directly.
                listed = next(row for row in client.get("/api/assignments").json["assignments"] if row["title"] == "Quiz 9")
                assert listed["plan_id"], listed
                expect(wizard.locator(".wizard-grading-link")).to_contain_text("Grading is prepared for this term. Continue with its rubrics, blank templates and answer areas.")
                expect(wizard.locator(".wizard-grading-link").get_by_role("button", name="Prepare questions")).not_to_have_class(PRIMARY)
                expect(wizard.locator("[data-grading-problems]")).to_have_count(0)
                expect(wizard.locator("[data-grading-link]")).to_have_count(0)
                wizard.get_by_label("New version label").fill("A")
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator("[data-chart]")).to_have_count(1)
                expect(wizard.locator("[data-chart]")).to_contain_text("HECC 105 · Sections 507, 508 · 9 seated")
                expect(wizard.locator("[data-term-stack]")).to_have_count(0)
                # The chart's own confirmation runs inside the window; Back returns to it from any of its dialogs.
                wizard.get_by_role("button", name="Confirm and export").click()
                export = page.locator("#seating-export")
                expect(export.locator('select[name="version_id"] option:checked')).to_contain_text("A · ")
                expect(export.get_by_role("button", name="Continue to response cards")).to_be_visible()
                export.get_by_role("button", name="Continue to response cards").click()
                cards = page.locator("#seating-cards")
                expect(cards).to_be_visible()
                cards.get_by_role("button", name="Back", exact=True).click()
                step(wizard, "Stamp and print")
                assert not page.evaluate('document.querySelector("#dialog").classList.contains("dialog-wide")')
                wizard.get_by_role("button", name="Confirm and export").click()
                export.locator('input[name="prepare_cards"]').uncheck()
                export.get_by_role("button", name="Confirm and export").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator("[data-chart]")).to_contain_text("Confirmed: Section 507 · 6 copies, Section 508 · 3 copies.")
                expect(wizard.get_by_role("button", name="Open Section 507 job")).to_be_visible()
                # Nothing is left to do in the window: Done is its one primary.
                expect(wizard.get_by_role("button", name="Done")).to_have_class(re.compile(r"\bprimary\b"))
                page.screenshot(path=str(output / "assignment-wizard-print.png"))
                wizard.get_by_role("button", name="Done").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                # Done returns to the assignment itself (ASSIGNMENT-WORKSPACE, DOCUMENTS: #assignments/<assignment>/<term>).
                expect(page).to_have_url(re.compile(r"#assignments/[^/]+/[^/?]+$"))
                expect(page.locator("#page-title")).to_have_text("Quiz 9")
                expect(page.locator("#page-context")).to_contain_text("MATH 140")
                # Its Create area lists its print jobs. The chart's export is one group: the room, both sections and their copies together.
                page.locator("[data-assignment-nav]").get_by_role("link", name="Create", exact=True).click()
                area = page.locator("#assignment-workspace [data-assignment-body]")
                expect(area.locator(".document-job")).to_have_count(2)
                room_group = area.locator("section[data-export-group]")
                expect(room_group).to_have_count(1)
                expect(room_group.locator(".document-export-facts")).to_contain_text("HECC 105 · Sections 507, 508 · 9 copies · Version A")
                # Both rows say Queued, so the header does not repeat it.
                expect(room_group.locator(".document-export-side .chip")).to_have_count(0)
                expect(room_group.locator(".document-status")).to_have_text(["Queued", "Queued"])
                expect(room_group.locator("[data-print-files-note]")).to_contain_text("once every section")

                # --- The card is the assignment's life: Paper, Seating and Print, with the next unfinished stage as its one primary.
                # (Assignments replaced the print library's sidebar entry, ASSIGNMENT-WORKSPACE; a course's page still links the library,
                # #documents?definition=<course>, whose cards these are.) ---
                definition = next(item["id"] for item in client.get("/api/documents/catalog").json["definitions"] if item["code"] == "MATH 140")
                page.goto(f"{address}/#documents?definition={definition}")
                quiz9 = page.locator("#document-assignments article[data-assignment-card]").filter(has_text="Quiz 9")
                expect(quiz9.locator("[data-pipeline-step]")).to_have_count(3)
                expect(quiz9.locator("[data-pipeline-step] strong")).to_have_text(["Paper", "Seating", "Print"])
                expect(quiz9.locator(".button.primary")).to_have_count(1)
                expect(quiz9.locator("[data-assignment-next]")).to_have_attribute("data-assignment-next", "print")
                expect(quiz9.locator(".document-card-facts")).to_contain_text("PDF A")
                # A stage opens the window at its own step, with the assignment's charts attached and nothing created.
                stage_link = quiz9.locator("[data-assignment-next]").get_attribute("href")
                quiz9.locator("[data-assignment-next]").click()
                wizard = page.locator("#assignment-wizard")
                # (The closed window keeps its last markup; the earlier export's summary shows the new one has opened.)
                expect(wizard.locator("[data-chart] [data-export-status]")).to_contain_text("Exported ")
                step(wizard, "Stamp and print")
                expect(wizard.locator("[data-chart] [data-export-status]")).to_contain_text("Sections 507, 508 · 9 copies")
                # The stage link keeps its address, so its assignment and term stay known (ASSIGNMENT-WORKSPACE), and its window
                # opens once: a refresh of the page never opens it again.
                assert page.evaluate("location.hash") == stage_link, page.evaluate("location.hash")
                # Steps already passed are links back.
                wizard.locator(".wizard-steps").get_by_role("button", name="Paper", exact=True).click()
                step(wizard, "Paper")
                wizard.get_by_role("button", name="Close dialog").click()
                with page.expect_response(lambda response: "/api/assignments/resolve" in response.url):
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_timeout(300)
                expect(page.locator("#dialog")).not_to_be_visible()
                assert len(client.get("/api/documents/catalog").json["batches"]) == 2, "Opening a stage must not create print jobs"

                # --- Continue a seated assignment: its own charts come attached, so no section is offered as unseated copies. It is
                # continued from its Create area (ASSIGNMENT-WORKSPACE): Seating and arrangements opens the window, whose Back leads
                # to its Details. ---
                create_area(page, "Quiz 9").get_by_role("button", name="Seating and arrangements", exact=True).click()
                wizard = page.locator("#assignment-wizard")
                step(wizard, "Seating")
                wizard.get_by_role("button", name="Back", exact=True).click()
                step(wizard, "Details")
                expect(wizard.locator('input[name="section"]:checked')).to_have_count(2)
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Seating")
                expect(wizard.get_by_label("This assignment requires a seating chart")).to_be_checked()
                expect(wizard.locator('.wizard-plan:has(> span > strong:text-is("Quiz 9")) input')).to_be_checked()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                expect(wizard.get_by_label("Use a registered PDF")).to_be_checked()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator("[data-chart]")).to_have_count(1)
                expect(wizard.locator("[data-term-stack]")).to_have_count(0)
                expect(wizard.get_by_role("button", name="Create print job")).to_have_count(0)
                # What the earlier confirmation exported is shown, with its jobs; exporting again is secondary and says it stamps anew.
                seated_row = wizard.locator("[data-chart]")
                expect(seated_row.locator("[data-export-status]")).to_contain_text("Exported ")
                expect(seated_row.locator("[data-export-status]")).to_contain_text("Sections 507, 508 · 9 copies · Preparing")
                expect(seated_row.locator("[data-print-files-note]")).to_contain_text("once every section")
                expect(wizard.get_by_role("button", name="Open Section 507 job", exact=True)).to_have_class(re.compile(r"\bprimary\b"))
                again = wizard.get_by_role("button", name="Export again (new stamps)", exact=True)
                expect(again).to_be_visible()
                expect(again).not_to_have_class(re.compile(r"\bprimary\b"))
                expect(seated_row).to_contain_text("new stamps for every section")
                expect(wizard.get_by_role("button", name="Done")).not_to_have_class(re.compile(r"\bprimary\b"))
                page.screenshot(path=str(output / "assignment-wizard-continue.png"))
                wizard.get_by_role("button", name="Close dialog").click()
                assert len(client.get("/api/documents/catalog").json["batches"]) == 2, "Continuing must not create print jobs"

                # --- The key follows the title until the instructor types their own. ---
                to_list(page)
                page.get_by_role("button", name="Create assignment", exact=True).click()
                page.get_by_role("button", name="Create paper and print", exact=True).click()
                wizard = page.locator("#assignment-wizard")
                wizard.get_by_label("Assignment title", exact=True).fill("Unit Test 5: Limits")
                expect(wizard.get_by_label("Assignment key", exact=True)).to_have_value("unit-test-5-limits")
                wizard.get_by_text("Advanced: permanent assignment key", exact=True).click()
                wizard.get_by_label("Assignment key", exact=True).fill("ut5")
                wizard.get_by_label("Assignment title", exact=True).fill("Unit Test 5")
                expect(wizard.get_by_label("Assignment key", exact=True)).to_have_value("ut5")
                wizard.get_by_role("button", name="Close dialog").click()

                # --- Unseated sections print ONE stack for the term (requirement, 2026-10-01): a job naming no section, any copy for
                # any of their students; one named copy per roster student, or students chosen copy by copy by name. ---
                wizard = details(page, "quiz-10", "Quiz 10")
                wizard.get_by_label("This assignment requires a seating chart").uncheck()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                # Once the assignment exists its key and title are fixed: going back cannot make a second assignment.
                wizard.locator(".wizard-steps").get_by_role("button", name="Details", exact=True).click()
                step(wizard, "Details")
                wizard.get_by_text("Advanced: permanent assignment key", exact=True).click()
                expect(wizard.get_by_label("Assignment title", exact=True)).to_be_disabled()
                expect(wizard.get_by_label("Assignment key", exact=True)).to_be_disabled()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                wizard.get_by_label("New version label").fill("A")
                # WHAT WAS TYPED ON PAPER STAYS (2026-10-11): the label typed, the files chosen and a second version's row come back
                # after Back and after the step line (both lost them), under the same choice.
                chosen_files = wizard.locator('[data-new-version] input[name="files"]').first
                chosen_files.set_input_files({"name": "main.tex", "mimeType": "text/x-tex", "buffer": b"\\documentclass{facultyassignment}"})
                wizard.get_by_role("button", name="Add another version").click()
                expect(wizard.get_by_label("Another version label")).to_have_value("B")
                for leave in (lambda: wizard.get_by_role("button", name="Back").click(),
                              lambda: wizard.locator(".wizard-steps").get_by_role("button", name="Details", exact=True).click()):
                    leave()
                    step(wizard, "Details")
                    wizard.get_by_role("button", name="Next").click()
                    step(wizard, "Paper")
                    expect(wizard.get_by_label("Faculty prints it from LaTeX")).to_be_checked()
                    expect(wizard.get_by_label("New version label")).to_have_value("A")
                    expect(wizard.get_by_label("Another version label")).to_have_value("B")
                    assert chosen_files.evaluate("node => [...node.files].map(file => file.name)") == ["main.tex"]
                # ASK FIRST: Finish later names what would be left behind; declined, the window stays with everything in it.
                asked = []
                page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
                wizard.get_by_role("button", name="Finish later").click()
                assert asked == ["Finish later without adding Versions A, B? What you typed for them is not saved. Quiz 10 itself is saved."], asked
                step(wizard, "Paper")
                expect(wizard.get_by_label("New version label")).to_have_value("A")
                wizard.get_by_role("button", name="Remove Version B").click()
                expect(wizard.locator("[data-new-version]")).to_have_count(1)
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                rows = wizard.locator("[data-term-stack]")
                expect(rows).to_have_count(1)
                roster_507 = client.get(f"/api/documents/sections/{sections[507]}/enrollments").json
                roster_508 = client.get(f"/api/documents/sections/{sections[508]}/enrollments").json
                everyone = len(roster_507["enrollments"]) + len(roster_508["enrollments"])
                copies_field = wizard.get_by_label("Physical copies for Sections 507, 508")
                expect(copies_field).to_have_value(str(everyone))
                named = rows.get_by_label("One copy per student on the roster (named)")
                named.check()
                expect(copies_field).to_have_value(str(everyone))
                expect(copies_field).to_be_disabled()
                expect(rows.locator("[data-copy-by-copy]")).to_be_hidden()
                named.uncheck()
                copies_field.fill("2")
                copies_field.blur()
                rows.get_by_text("Choose students copy by copy").click()
                pickers = rows.locator("[data-student-picker]")
                expect(pickers).to_have_count(2)
                first = pickers.nth(0).get_by_role("combobox")
                first.click()
                first.fill("carol")
                expect(page.get_by_role("option", name="Example, Carol")).to_be_visible()
                first.press("Enter")
                expect(first).to_have_value("Example, Carol")
                # A third copy keeps the students already chosen and starts unassigned (2026-10-11: it cleared them all); the
                # count changed with the list folded keeps them too, so they are still the ones sent.
                copies_field.fill("3")
                copies_field.blur()
                expect(pickers).to_have_count(3)
                expect(pickers.nth(0).get_by_role("combobox")).to_have_value("Example, Carol")
                for index in (1, 2):
                    expect(pickers.nth(index).get_by_role("combobox")).to_have_value("Unassigned")
                rows.get_by_text("Choose students copy by copy").click()
                expect(pickers.nth(0).get_by_role("combobox")).to_be_hidden()
                copies_field.fill("2")
                copies_field.blur()
                rows.get_by_text("Choose students copy by copy").click()
                expect(pickers).to_have_count(2)
                expect(pickers.nth(0).get_by_role("combobox")).to_have_value("Example, Carol")
                # Sections still to print: Create print job is the step's one primary, and Done is secondary.
                expect(wizard.get_by_role("button", name="Create print job")).to_have_class(re.compile(r"\bprimary\b"))
                expect(wizard.get_by_role("button", name="Done")).not_to_have_class(re.compile(r"\bprimary\b"))
                wizard.get_by_role("button", name="Create print job").click()
                expect(wizard.get_by_role("button", name="Open print job")).to_have_count(1)
                expect(wizard.locator(".wizard-row").filter(has_text="one stack")).to_contain_text("Sections 507, 508")
                catalog_now = client.get("/api/documents/catalog").json
                quiz10 = next(item for item in catalog_now["assignments"] if item["assignment_key"] == "quiz-10")
                versions = {item["id"] for item in catalog_now["versions"] if item["assignment_id"] == quiz10["id"]}
                made = [app.extensions["documents"].get_batch(item["id"]) for item in catalog_now["batches"] if item["assignment_version_id"] in versions]
                carol = next(item["enrollment_id"] for item in roster_508["students"] if item["name"] == "Example, Carol")
                assert len(made) == 1 and made[0]["section_id"] is None, made
                assert [s["number"] for s in made[0]["sections"]] == [507, 508]
                assert [copy["enrollment_id"] for copy in made[0]["copies"]] == [carol, None]
                # A second version in the same window: its step shows only its own jobs, names Version A's apart, and offers
                # Version B's job (create-1). Nothing is created here.
                wizard.get_by_role("button", name="Back").click()
                step(wizard, "Paper")
                wizard.get_by_label("Faculty prints it from LaTeX").check()
                expect(wizard.get_by_label("New version label")).to_have_attribute("placeholder", "B")
                wizard.get_by_label("New version label").fill("B")
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator(".section-lead")).to_contain_text("Version B · ")
                expect(wizard.locator("[data-queued-job]")).to_have_count(0)
                expect(wizard.locator(".wizard-row").filter(has_text="queued")).to_have_count(0)
                expect(wizard.get_by_role("button", name="Create print job", exact=True)).to_have_class(PRIMARY)
                expect(wizard.locator("[data-printed-before]")).to_have_count(0)
                expect(wizard.locator("[data-printed-other]")).to_contain_text("Other versions printed: Version A · ")
                expect(wizard.locator("[data-printed-other]")).to_contain_text("2 copies")
                expect(wizard.locator("[data-term-stack] > label.field")).to_contain_text("physical copies of Version B")
                expect(wizard.locator("[data-version-only]")).to_have_text("Prints this version only.")
                expect(copies_field).to_have_value(str(everyone))  # Version B was never printed.
                expect(wizard.locator("[data-stack-every-version]")).to_have_count(0)  # Version A is printed already.
                # The registered PDFs list the printed version first, and choosing it shows its queued stack again.
                wizard.get_by_role("button", name="Back").click()
                step(wizard, "Paper")
                options = wizard.locator('select[name="version_id"] option')
                expect(options).to_have_count(2)
                expect(options.nth(0)).to_have_text(re.compile(r"^A · \d+ pages? · printed \w+ \d+$"))
                expect(options.nth(1)).to_have_text(re.compile(r"^B · \d+ pages?$"))
                wizard.get_by_label("Version", exact=True).select_option(index=0)
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator("[data-queued-job]")).to_contain_text("Version A · Sections 507, 508")
                expect(wizard.locator("[data-printed-other]")).to_have_count(0)
                assert len(client.get("/api/documents/catalog").json["batches"]) == len(catalog_now["batches"]), "Going back and forth must not create print jobs"
                # The job lists its copies by student name, and Change assignment chooses by name.
                wizard.get_by_role("button", name="Open print job").click()
                detail = page.locator("#document-batch-detail")
                detail.locator("[data-copy-assignments] > summary").click()
                expect(detail.locator(".document-batch-copies")).to_contain_text("Example, Carol")
                expect(detail.locator(".document-batch-copies")).to_contain_text("Unassigned")
                detail.get_by_role("button", name="Change assignment").first.click()
                expect(page.locator("#document-copy-assignment").get_by_role("combobox", name="Student")).to_have_value("Example, Carol")
                page.get_by_role("button", name="Back to print job").click()
                expect(page.locator("#document-batch-detail")).to_be_visible()
                page.get_by_role("button", name="Close dialog").click()

                # --- Continuing Quiz 10 at Version B (the latest): Done says Version B has no print job yet (create-1). ---
                def continue_quiz10():
                    # From its Create area (ASSIGNMENT-WORKSPACE): Add or revise paper opens the window at Paper.
                    create_area(page, "Quiz 10").get_by_role("button", name="Add or revise paper", exact=True).click()
                    found = page.locator("#assignment-wizard")
                    step(found, "Paper")
                    expect(found.get_by_label("Use a registered PDF")).to_be_checked()
                    found.get_by_role("button", name="Next").click()
                    step(found, "Stamp and print")
                    expect(found.locator(".section-lead")).to_contain_text("Version B · ")
                    expect(found.locator("[data-printed-other]")).to_contain_text("Version A")
                    return found
                wizard = continue_quiz10()
                wizard.get_by_role("button", name="Done").click()
                expect(page.locator("#toast")).to_contain_text("Quiz 10 is saved. Version B has no print job yet.")
                wizard = continue_quiz10()
                wizard.get_by_label("Physical copies for Sections 507, 508").fill("1")
                wizard.get_by_role("button", name="Create print job", exact=True).click()
                expect(wizard.locator("[data-queued-job]")).to_contain_text("Version B · Sections 507, 508 · one stack · 1 copy queued")
                catalog_now = client.get("/api/documents/catalog").json
                labels = {item["id"]: item["label"] for item in catalog_now["versions"] if item["assignment_id"] == quiz10["id"]}
                term_jobs = sorted(labels[item["assignment_version_id"]] for item in catalog_now["batches"] if item["assignment_version_id"] in labels and not item["section_id"])
                assert term_jobs == ["A", "B"], term_jobs
                # Done ends with the next step and returns to the assignment (ASSIGNMENT-WORKSPACE, DOCUMENTS), whose Collect takes the
                # scans; its exam page is linked from there.
                wizard.get_by_role("button", name="Done").click()
                finished = page.locator("#toast .toast-item").filter(has_text="Quiz 10 is set up. After it is given, upload the scans in Collect or the Teaching Inbox.")
                expect(finished).to_have_count(1)
                expect(page).to_have_url(re.compile(rf"#assignments/{quiz10['id']}/[^/?]+$"))
                expect(page.locator("#page-title")).to_have_text("Quiz 10")
                expect(page.locator("[data-assignment-nav]").get_by_role("link", name="Collect", exact=True)).to_be_visible()
                # Its Print stage on Version B: Version B's own job is Already printed, more copies start at 0, and opening
                # that job is the step's one primary (create-1, print-3).
                page.goto(f"{address}/#documents?assignment={quiz10['id']}&step=print")
                wizard = page.locator("#assignment-wizard")
                step(wizard, "Stamp and print")
                expect(wizard.locator(".section-lead")).to_contain_text("Version B · ")
                expect(wizard.locator("[data-printed-before]")).to_contain_text(re.compile(r"Already printed: \w+ \d+ · Version B · 1 copy\."))
                expect(wizard.locator("[data-printed-other]")).to_contain_text("Other versions printed: Version A · ")
                expect(wizard.get_by_label("Physical copies for Sections 507, 508")).to_have_value("0")
                expect(wizard.get_by_role("button", name=re.compile(r"^Open \w+ \d+ job$"))).to_have_class(PRIMARY)
                expect(wizard.get_by_role("button", name="Create print job", exact=True)).not_to_have_class(PRIMARY)
                expect(wizard.locator("[data-stack-every-version]")).to_have_count(0)
                wizard.get_by_role("button", name="Close dialog").click()
                page.goto(address + "/#documents")
                expect(page.get_by_role("button", name="Create assignment", exact=True)).to_be_visible()

                # --- Several versions at once print as ONE stack, the versions interleaved (create-2). A version's job that
                # fails leaves the rest of the stack to make again with the same request IDs. ---
                wizard = details(page, "quiz-11", "Quiz 11")
                wizard.get_by_label("This assignment requires a seating chart").uncheck()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_label("New version label").fill("A")
                wizard.get_by_role("button", name="Add another version").click()
                expect(wizard.get_by_label("Another version label")).to_have_value("B")
                wizard.get_by_role("button", name="Add another version").click()
                expect(wizard.get_by_role("button", name="Remove Version B")).to_have_count(1)
                wizard.get_by_label("Another version label").nth(1).fill("Z")
                expect(wizard.get_by_role("button", name="Remove Version Z")).to_have_count(1)  # The name follows the label.
                wizard.get_by_role("button", name="Remove Version Z").click()
                expect(wizard.locator("[data-new-version]")).to_have_count(2)
                fits(page, "Two new versions")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(page.locator("#toast")).to_contain_text("Versions A, B registered.")
                expect(wizard.locator(".section-lead")).to_have_text(re.compile(r"^Versions A, B · \d+ pages? each\.$"))
                stack = wizard.locator("[data-stack-every-version]")
                expect(stack).to_contain_text("One stack, every version")
                expect(stack.get_by_label("Copies of Version A")).to_have_value(str((everyone + 1) // 2))
                expect(stack.get_by_label("Copies of Version B")).to_have_value(str(everyone // 2))
                expect(stack.locator("label.field").first).to_contain_text("Copies of Version A")  # The visible label is its name.
                expect(stack.locator("[data-stack-total]")).to_contain_text(f"{everyone} copies in all")
                create_stack = stack.get_by_role("button", name="Create print jobs", exact=True)
                expect(create_stack).to_have_class(PRIMARY)
                expect(wizard.get_by_role("button", name="Done")).not_to_have_class(PRIMARY)
                # Printing one version only is folded away, with no named or copy-by-copy choice in the stack itself.
                expect(wizard.get_by_role("button", name="Create print job", exact=True)).to_have_count(0)
                expect(stack.locator('input[name="named"], [data-copy-by-copy]')).to_have_count(0)
                wizard.locator("[data-one-version] > summary").click()
                expect(wizard.get_by_role("button", name="Create print job", exact=True)).not_to_have_class(PRIMARY)
                wizard.locator("[data-one-version] > summary").click()
                stack.get_by_label("Copies of Version A").fill("2")
                stack.get_by_label("Copies of Version B").fill("1")
                expect(stack.locator("[data-stack-total]")).to_contain_text("3 copies in all")
                fits(page, "One stack, every version")
                page.screenshot(path=str(output / "assignment-wizard-stack.png"))
                quiz11 = next(item for item in client.get("/api/documents/catalog").json["assignments"] if item["assignment_key"] == "quiz-11")
                versions11 = {item["label"]: item["id"] for item in client.get("/api/documents/catalog").json["versions"] if item["assignment_id"] == quiz11["id"]}
                sent = []

                def fail_version_b_once(route):
                    body = route.request.post_data_json or {}
                    sent.append(body.get("request_id"))
                    if body.get("version_id") == versions11["B"] and sent.count(body.get("request_id")) == 1:
                        route.fulfill(status=500, json={"error": "Synthetic failure."})
                    else:
                        route.continue_()
                page.route("**/api/documents/batches", fail_version_b_once)
                create_stack.click()
                expect(wizard.locator("[data-stack-made]")).to_contain_text("Version A is queued; the rest of the stack is not.")
                expect(wizard.locator("[data-stack-made]")).to_contain_text("Version B was not: Synthetic failure. Create print jobs queues the rest")
                expect(wizard.locator("[data-stack-made]")).to_contain_text("Keep this window open until it does")
                expect(stack.get_by_label("Copies of Version A")).to_be_disabled()
                expect(stack.get_by_label("Copies of Version B")).to_have_value("1")
                expect(wizard.locator("[data-one-version]")).to_have_count(0)  # Its versions print in the stack, none twice.
                # Enter in a copies field presses the stack's own Create print jobs, never Done.
                stack.get_by_label("Copies of Version B").press("Enter")
                expect(wizard.locator("[data-queued-job]")).to_contain_text("Versions A, B · Sections 507, 508 · one stack · 3 copies queued")
                page.unroute("**/api/documents/batches")
                assert len(sent) == 3 and sent[1] == sent[2] and sent[0].rsplit(":", 1)[0] == sent[1].rsplit(":", 1)[0], sent
                stack_jobs = [item for item in client.get("/api/documents/catalog").json["batches"] if item["assignment_version_id"] in versions11.values()]
                assert len(stack_jobs) == 2 and len({item["export"] for item in stack_jobs}) == 1 and all(item["section_id"] is None for item in stack_jobs), stack_jobs
                assert sorted((item["quantity"], item["assignment_version_id"]) for item in stack_jobs) == [(1, versions11["B"]), (2, versions11["A"])]
                # Rendered, the two jobs are one group on Print jobs whose Print PDF interleaves the versions.
                while app.extensions["documents"].build_one():
                    pass
                wizard.get_by_role("button", name="Done").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                # Its print jobs are listed in its Create area (ASSIGNMENT-WORKSPACE), where Done returned.
                expect(page.locator("#page-title")).to_have_text("Quiz 11")
                page.locator("[data-assignment-nav]").get_by_role("link", name="Create", exact=True).click()
                interleaved = page.locator("#assignment-workspace [data-assignment-body] section[data-export-group]").filter(has_text="Versions A, B interleaved")
                expect(interleaved).to_have_count(1)
                expect(interleaved.get_by_role("link", name="Print PDF", exact=True)).to_have_attribute("href", re.compile(r"/api/documents/stack\?batches="))
                # Its Print stage names the printed stack, offers its interleaved file first and starts more copies at 0 (print-3).
                page.goto(f"{address}/#documents?assignment={quiz11['id']}&step=print")
                wizard = page.locator("#assignment-wizard")
                step(wizard, "Stamp and print")
                expect(wizard.locator(".section-lead")).to_have_text(re.compile(r"^Versions A, B · \d+ pages? each\.$"))
                expect(wizard.locator("[data-printed-before]")).to_contain_text(re.compile(r"Already printed: \w+ \d+ · one stack · Versions A, B · 3 copies\."))
                expect(wizard.locator("[data-printed-other]")).to_have_count(0)
                printed_pdf = wizard.locator(".wizard-printed-actions").get_by_role("link", name="Print PDF", exact=True)
                expect(printed_pdf).to_have_attribute("href", re.compile(r"/api/documents/stack\?batches="))
                expect(printed_pdf).to_have_class(PRIMARY)
                expect(wizard.get_by_role("button", name=re.compile(r"^Open \w+ \d+ job$"))).to_have_count(1)
                expect(wizard.get_by_label("Physical copies for Sections 507, 508")).to_have_value("0")
                expect(wizard.locator("[data-version-only]")).to_have_text("Prints this version only. To print more of every version, open the stack’s job and use Print more copies…")
                expect(wizard.get_by_role("button", name="Create print job", exact=True)).not_to_have_class(PRIMARY)
                expect(wizard.locator("[data-stack-every-version]")).to_have_count(0)
                wizard.get_by_role("button", name="Close dialog").click()

                # --- A revised version prints alone: A, then A-fixed registered on its own. The one-version form comes first and
                # the stack is folded under it, so the replaced A never prints by default (create-2). ---
                wizard = details(page, "quiz-12", "Quiz 12")
                wizard.get_by_label("This assignment requires a seating chart").uncheck()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_label("New version label").fill("A")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                wizard.get_by_role("button", name="Back").click()
                step(wizard, "Paper")
                wizard.get_by_label("Faculty prints it from LaTeX").check()
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_label("New version label").fill("A-fixed")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator(".section-lead")).to_contain_text("Version A-fixed · ")
                expect(wizard.get_by_label("Physical copies for Sections 507, 508")).to_be_visible()
                expect(wizard.get_by_label("Physical copies for Sections 507, 508")).to_have_value(str(everyone))
                expect(wizard.get_by_role("button", name="Create print job", exact=True)).to_have_class(PRIMARY)
                expect(wizard.locator("[data-one-version]")).to_have_count(0)
                expect(wizard.locator("[data-stack-every-version]")).to_be_hidden()
                folded = wizard.locator("[data-stack-option]")
                folded.locator("summary").click()
                expect(folded.get_by_label("Copies of Version A", exact=True)).to_be_visible()
                expect(folded.get_by_label("Copies of Version A-fixed", exact=True)).to_be_visible()
                expect(folded.get_by_role("button", name="Create print jobs", exact=True)).not_to_have_class(PRIMARY)
                fits(page, "The folded stack")
                wizard.get_by_role("button", name="Close dialog").click()

                # --- A version whose jobs were all cancelled or superseded (an earlier layout) never joins a stack. Opened from its
                # Print stage, the window's default paper offers the current versions A2, B2, C2 as the stack. ---
                quiz12 = next(item for item in client.get("/api/documents/catalog").json["assignments"] if item["assignment_key"] == "quiz-12")
                term_id = next(item["term_id"] for item in client.get("/api/documents/catalog").json["sections"] if item["id"] == sections[507])

                def term_job(version_id, quantity=1):
                    made = client.post("/api/documents/batches", json={"version_id": version_id, "section_id": None, "term_id": term_id, "section_ids": [sections[507], sections[508]],
                                                                      "quantity": quantity, "students": [None] * quantity, "request_id": f"{uuid.uuid4()}:term"}, headers=HEADERS)
                    assert made.status_code == 202, made.json
                    return made.json["id"]
                versions12 = {item["label"]: item["id"] for item in client.get("/api/documents/catalog").json["versions"] if item["assignment_id"] == quiz12["id"]}
                cancelled = term_job(versions12["A"])
                assert client.post(f"/api/documents/batches/{cancelled}/cancel", json={}, headers=HEADERS).status_code == 200
                superseded = term_job(versions12["A-fixed"])
                while app.extensions["documents"].build_one():
                    pass
                assert client.post(f"/api/documents/batches/{superseded}/supersede", json={}, headers=HEADERS).status_code == 200
                for label in ("A2", "B2", "C2"):
                    made = client.post("/api/documents/versions/fixture", json={"assignment_id": quiz12["id"], "label": label, "paper": "letter"}, headers=HEADERS)
                    assert made.status_code in (200, 201), made.json
                    versions12[label] = made.json["id"]
                page.goto(f"{address}/#documents?assignment={quiz12['id']}&step=print")
                wizard = page.locator("#assignment-wizard")
                step(wizard, "Stamp and print")
                stack = wizard.locator("[data-stack-every-version]")
                expect(wizard.locator(".section-lead")).to_have_text(re.compile(r"^Versions A2, B2, C2 · \d+ pages? each\.$"))
                expect(stack.locator("input[data-stack-version]")).to_have_count(3)
                expect(stack.get_by_label("Copies of Version A", exact=True)).to_have_count(0)
                expect(stack.get_by_label("Copies of Version A-fixed", exact=True)).to_have_count(0)
                for label in ("A2", "B2", "C2"):
                    stack.get_by_label(f"Copies of Version {label}", exact=True).fill("1")
                # B2 fails part way, then is printed apart (another tab): the stack never sends it again, and C2 joins A2.
                sent = []

                def fail_b2(route):
                    body = route.request.post_data_json or {}
                    sent.append(body.get("request_id"))
                    if body.get("version_id") == versions12["B2"]:
                        route.fulfill(status=500, json={"error": "Synthetic failure."})
                    else:
                        route.continue_()
                page.route("**/api/documents/batches", fail_b2)
                stack.get_by_role("button", name="Create print jobs", exact=True).click()
                expect(wizard.locator("[data-stack-made]")).to_contain_text("Version A2 is queued; the rest of the stack is not.")
                expect(wizard.locator("[data-one-version]")).to_have_count(0)
                assert len(sent) == 2, sent  # A2, then B2 failed; C2 waits.
                apart = term_job(versions12["B2"], 2)
                wizard.get_by_role("button", name="Back").click()
                step(wizard, "Paper")
                options = wizard.locator('select[name="version_id"] option')
                expect(options).to_have_count(5)
                expect(options.nth(0)).to_have_text(re.compile(r"^A2 · \d+ pages? · printed \w+ \d+$"))
                expect(options.nth(1)).to_have_text(re.compile(r"^B2 · \d+ pages? · printed \w+ \d+$"))
                expect(wizard.locator('select[name="version_id"] option', has_text="A-fixed")).to_have_text(re.compile(r"^A-fixed · \d+ pages? · earlier layout$"))
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                stack = wizard.locator("[data-stack-every-version]")
                expect(stack.get_by_label("Copies of Version B2", exact=True)).to_be_disabled()
                expect(stack).to_contain_text("Printed apart")
                expect(wizard.locator("[data-printed-apart]")).to_contain_text(re.compile(r"Printed apart from this stack: Version B2 · \w+ \d+ · 2 copies\."))
                expect(wizard.locator("[data-stack-made]")).not_to_contain_text("Synthetic failure")
                stack.get_by_label("Copies of Version C2", exact=True).press("Enter")
                expect(wizard.locator("[data-queued-job]")).to_contain_text("Versions A2, C2 · Sections 507, 508 · one stack · 2 copies queued")
                expect(wizard.locator("[data-printed-other]")).to_contain_text("Other versions printed: Version B2 · ")
                page.unroute("**/api/documents/batches")
                assert len(sent) == 3 and sent[2].rsplit(":", 1) == [sent[0].rsplit(":", 1)[0], "C"], sent
                jobs12 = [item for item in client.get("/api/documents/catalog").json["batches"] if item["assignment_version_id"] in (versions12["A2"], versions12["B2"], versions12["C2"])
                          and item["origin"] != "registration"]
                assert sorted(item["assignment_version_id"] == versions12["B2"] for item in jobs12) == [False, False, True], jobs12
                assert len({item["export"] for item in jobs12 if item["id"] != apart}) == 1, jobs12
                # Versions registered together are offered as a stack even though other versions were printed already.
                wizard.get_by_role("button", name="Back").click()
                step(wizard, "Paper")
                wizard.get_by_label("Faculty prints it from LaTeX").check()
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_label("New version label").fill("D3")
                wizard.get_by_role("button", name="Add another version").click()
                wizard.get_by_label("Another version label").fill("E3")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator(".section-lead")).to_have_text(re.compile(r"^Versions D3, E3 · \d+ pages? each\.$"))
                expect(wizard.locator("[data-stack-every-version] input[data-stack-version]")).to_have_count(2)
                expect(wizard.locator("[data-printed-other]")).to_contain_text("Versions A2, C2")
                wizard.get_by_role("button", name="Close dialog").click()

                # --- Without the testing mark the Paper step offers no demonstration fixture and no Source choice (print-14). ---
                plain = browser.new_page(viewport={"width": 1440, "height": 1000})
                plain.set_default_timeout(10000)
                quiz9_id = next(item["id"] for item in client.get("/api/documents/catalog").json["assignments"] if item["assignment_key"] == "quiz-9")
                plain.goto(f"{address}/#documents?assignment={quiz9_id}&step=version")
                paper = plain.locator("#assignment-wizard")
                step(paper, "Paper")
                paper.get_by_label("Faculty prints it from LaTeX").check()
                expect(paper.get_by_label("New version label")).to_be_visible()
                expect(paper.locator('select[name="source"] option')).to_have_count(1)
                expect(paper.locator("[data-source-field]")).to_be_hidden()
                paper.get_by_role("button", name="Close dialog").click()
                # Once Grading lists the assignment (the catalog's grading_entry_plan_id, else grading_plan_id), the line links its plan.
                def listed_in_grading(route):
                    response = route.fetch()
                    data = response.json()
                    for row in data.get("lifecycle", []):
                        if row["assignment_id"] == quiz9_id:
                            row["grading_entry_plan_id"] = "plan-quiz-9"
                    route.fulfill(response=response, json=data)
                plain.route("**/api/documents/catalog", listed_in_grading)
                # (The link keeps its address once opened, so the page leaves it before following it again.)
                plain.goto(f"{address}/#assignments")
                plain.goto(f"{address}/#documents?assignment={quiz9_id}&step=version")
                step(paper, "Paper")
                expect(paper).to_be_visible()
                # The line says grading is prepared, and Prepare questions continues to the assignment's question preparation
                # (ASSIGNMENT-WORKSPACE, DOCUMENTS): its plan's Rubrics & Templates in the assignment's Prepare area.
                expect(paper.locator(".wizard-grading-link")).to_contain_text("Grading is prepared for this term. Continue with its rubrics, blank templates and answer areas.")
                expect(paper.locator("[data-grading-link]")).to_have_count(0)
                quiz9_plan = next(row for row in client.get("/api/assignments").json["assignments"] if row["assignment_id"] == quiz9_id)["plan_id"]
                paper.get_by_role("button", name="Prepare questions", exact=True).click()
                expect(plain).to_have_url(re.compile(rf"#grading/{quiz9_plan}\?tab=rubrics&workspace=1$"), timeout=60000)
                plain.close()

                # --- Typing the key and title of an assignment that exists continues it, with its seating chart attached. ---
                assignments_before = len(client.get("/api/seating/catalog").json["assignments"])
                batches_before = len(client.get("/api/documents/catalog").json["batches"])
                to_list(page)
                page.get_by_role("button", name="Create assignment", exact=True).click()
                page.get_by_role("button", name="Create paper and print", exact=True).click()
                wizard = page.locator("#assignment-wizard")
                wizard.get_by_label("Assignment title", exact=True).fill("Midterm Exam")
                expect(wizard.get_by_label("Assignment key", exact=True)).to_have_value("midterm-exam")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Seating")
                expect(wizard.locator("[data-continues]")).to_contain_text("Midterm Exam already exists, so this continues it with its seating charts attached")
                expect(wizard.get_by_label("This assignment requires a seating chart")).to_be_checked()
                expect(wizard.locator('.wizard-plan:has(> span > strong:text-is("Midterm Exam")) input')).to_be_checked()
                # Only Section 508 is left without a seat; Section 507 is never offered as unseated copies.
                expect(wizard.locator("[data-generate-hint]")).to_contain_text("for Section 508, which the attached chart leaves out")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard.locator("[data-chart]")).to_have_count(1)
                expect(wizard.locator("[data-chart]")).to_contain_text("Section 507")
                expect(wizard.get_by_label(re.compile(r"^Physical copies for .*507"))).to_have_count(0)  # Section 507 is seated
                wizard.get_by_role("button", name="Close dialog").click()
                assert len(client.get("/api/seating/catalog").json["assignments"]) == assignments_before, "Continuing must not create an assignment"
                assert len(client.get("/api/documents/catalog").json["batches"]) == batches_before, "Continuing must not create print jobs"

                # --- Attach an existing chart whose exam the DR export named differently. ---
                wizard = details(page, "exam-2", "Exam 2")
                wizard.get_by_label("This assignment requires a seating chart").check()
                wizard.get_by_role("button", name="Attach existing seating chart").click()
                plans = wizard.locator(".wizard-plan")
                expect(plans).to_have_count(3)  # Midterm Exam, the confirmed Quiz 9, and None.
                midterm_plan = plans.filter(has_text="Midterm Exam")
                expect(midterm_plan).to_contain_text("HECC 105 (Section 507, 5 seated)")
                expect(midterm_plan).to_contain_text("Leaves out Section 508")
                expect(midterm_plan).to_contain_text("Named Midterm Exam, not Exam 2")
                midterm_plan.locator("input").check()
                expect(wizard.get_by_label("Generate new seating charts")).to_be_enabled()
                wizard.get_by_role("button", name="Next").click()
                expect(wizard.locator(".dialog-body > .form-error")).to_contain_text("Choose which name")
                page.screenshot(path=str(output / "assignment-wizard-names.png"))
                wizard.get_by_label("Override with Exam 2").check()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                moved = next(item for item in client.get("/api/seating").json if item["id"] == existing.json["id"])
                assert (moved["title"], moved["assignment_key"]) == ("Exam 2", "exam-2"), moved["title"]
                assert client.get(f"/api/seating/{moved['id']}").json["counts"]["excluded_dr"] == 1, "The DR booking should follow the chart"
                expect(wizard.get_by_label("Printed elsewhere: label each paper (stamps only)")).to_be_checked()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Stamp and print")
                expect(wizard).to_contain_text("Sections without a seating chart")
                expect(wizard.locator("[data-loose-waiting]")).to_contain_text("Export a seating chart as stamps only first")
                wizard.get_by_role("button", name="Confirm and export").click()
                stamps = page.locator("#seating-stamps-only")
                expect(stamps).to_contain_text("No exam PDF is attached")
                expect(stamps.get_by_role("link", name="Register a PDF first")).to_have_count(0)
                stamps.get_by_role("button", name="Back", exact=True).click()
                step(wizard, "Stamp and print")
                wizard.get_by_role("button", name="Done").click()

                # --- A confirmed chart keeps its name; keeping it continues that assignment and creates nothing. ---
                wizard = details(page, "exam-3", "Exam 3")
                wizard.get_by_label("This assignment requires a seating chart").check()
                wizard.get_by_role("button", name="Attach existing seating chart").click()
                wizard.locator(".wizard-plan").filter(has_text="Quiz 9").locator("input").check()
                expect(wizard.get_by_label("Override with Exam 3")).to_be_disabled()
                expect(wizard.locator(".wizard-naming")).to_contain_text("Not available: HECC 105: it is confirmed for printing")
                expect(wizard.get_by_label("Generate new seating charts")).to_be_disabled()
                wizard.get_by_label("Keep Quiz 9").check()
                wizard.get_by_role("button", name="Next").click()
                step(wizard, "Paper")
                expect(wizard.locator(".dialog-header")).to_contain_text("MATH 140 · Quiz 9")
                expect(wizard.get_by_label("Use a registered PDF")).to_be_checked()
                # ASK FIRST (2026-10-11): accepted, the window closes and its notice says the version typed was not added. A version
                # typed and then set aside for a registered PDF is not asked about.
                wizard.get_by_label("Faculty prints it from LaTeX").check()
                wizard.get_by_label("New version label").fill("Z")
                asked = []
                page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))
                wizard.get_by_role("button", name="Finish later").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                assert asked == ["Finish later without adding Version Z? What you typed for it is not saved. Quiz 9 itself is saved."], asked
                expect(page.locator("#toast")).to_contain_text("Quiz 9 is saved. Version Z was not added. Continue it from the Assignments list.")
                assert "Exam 3" not in [item["title"] for item in client.get("/api/seating/catalog").json["assignments"]]

                # --- A chart that seats an unticked section cannot be attached. ---
                wizard = details(page, "exam-4", "Exam 4", untick=(507,))
                wizard.get_by_label("This assignment requires a seating chart").check()
                wizard.get_by_role("button", name="Attach existing seating chart").click()
                blocked = wizard.locator(".wizard-plan").filter(has_text="Exam 2")
                expect(blocked.locator("input")).to_be_disabled()
                expect(blocked).to_contain_text("Seats Section 507, which is not ticked for this assignment")
                for width in (1440, 1280):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Window overflow at {width}"
                page.set_viewport_size({"width": 1440, "height": 1000})
                wizard.get_by_role("button", name="Close dialog").click()

                # --- "Print exams for this room" on a chart page opens the window at the Paper step. ---
                page.goto(f"{address}/#seating/{existing.json['id']}")
                page.get_by_role("link", name="Print exams for this room").click()
                wizard = page.locator("#assignment-wizard")
                step(wizard, "Paper")
                expect(wizard.locator(".dialog-header")).to_contain_text("MATH 140 · Exam 2")
                # The link keeps its address (ASSIGNMENT-WORKSPACE): the window opened once, over the assignment's paper and printing.
                assert page.evaluate("location.hash") == f"#documents?chart={existing.json['id']}", page.evaluate("location.hash")
                expect(page.locator("#documents-section").get_by_role("heading", name="Paper and printing")).to_be_visible()
                wizard.get_by_role("button", name="Cancel").click()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Create assignment: generated charts, attached charts, the name choice, blocked overrides, incompatible sections, confirmation and chart-page entry passed.")


if __name__ == "__main__":
    main()
