"""Printing and scan review in the real private browser UI and worker loop."""
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course
from browser_print_drive import verify_drive_export
from types import SimpleNamespace

from document_identity.email_exports import EmailExportConfig
from document_identity.printer import PrinterConfig
from test_printer_send import FakePrinter
from faculty_ocr import OcrError


def verify_listing(page, app, address):
    """Find the correct course-owned assignment and section-owned print job."""
    client = app.test_client()
    seed_course(client, name="Calculus III", code="MATH 251", sections=[501], multiple_sections=False, crn="25101")
    documents = app.extensions["documents"]
    catalog = documents.catalog()
    owners = {item["code"]: item["id"] for item in catalog["definitions"]}
    scanner = next(item for item in catalog["assignments"] if item["assignment_key"] == "scanner-test")
    practice = {}
    for code in ("MATH 140", "MATH 251"):
        practice[code] = documents.create_assignment(owners[code], "practice-set", "Practice set")
    for number in range(1, 8):
        title = f"Weekly review {number}"
        if number == 7:
            title += " — Connecting derivatives, integrals, and mathematical models with a detailed explanation of each result"
        documents.create_assignment(owners["MATH 140"], f"weekly-{number}", title)
    version = documents.register_stamp_pages(practice["MATH 251"]["id"], 1)
    section = next(item for item in documents.catalog()["sections"] if item["code"] == "MATH 251")
    waiting = documents.create_batch(version["id"], section["id"], 1, str(uuid.uuid4()))
    cancelled = documents.create_batch(version["id"], section["id"], 1, str(uuid.uuid4()))
    documents.transition(cancelled["id"], "cancel")
    # First-page registration of papers printed without labels issues a label job that is never printed.
    weekly = next(item for item in documents.catalog()["assignments"] if item["assignment_key"] == "weekly-1")
    math140 = next(item for item in documents.catalog()["sections"] if item["code"] == "MATH 140")
    registration = documents.create_batch(documents.register_stamp_pages(weekly["id"], 1)["id"], math140["id"], 1, f"unlabeled:synthetic-run:{math140['id']}:0123456789abcdef")

    page.set_viewport_size({"width": 1440, "height": 1080})
    # The print library (every course's assignments and print jobs) is no longer a sidebar entry: Assignments replaced it
    # (ASSIGNMENT-WORKSPACE) and bare #documents leads there. A course's page still links the library ("Assignments & printing",
    # #documents?definition=<course>), which opens filtered to that course; this check lists every course.
    library = address + f"/#documents?definition={owners['MATH 140']}"
    page.goto(library)
    page.get_by_role("tab", name="Assignments", exact=True).click()
    root = page.locator("#documents-section")
    assignments = root.locator("#document-assignments")
    batches = root.locator("#document-batches")
    search = root.get_by_role("searchbox", name="Search assignments and print jobs", exact=True)
    course = root.get_by_role("combobox", name="Filter by course", exact=True)
    status = root.get_by_role("combobox", name="Filter print jobs", exact=True)
    assignment_tab = root.get_by_role("tab", name="Assignments", exact=True)
    job_tab = root.get_by_role("tab", name="Print jobs", exact=True)
    clear = root.locator("#document-clear")
    expect(course).to_have_value(owners["MATH 140"])
    course.select_option("")
    expect(assignment_tab).to_have_attribute("aria-selected", "true")
    expect(status).to_be_hidden()
    expect(assignments.locator("[data-assignment]")).to_have_count(8)
    first_page = assignments.locator("[data-assignment]").evaluate_all("nodes=>nodes.map(node=>node.dataset.assignment)")
    root.get_by_role("button", name="Next results", exact=True).click()
    expect(assignments.locator("[data-assignment]")).to_have_count(2)
    second_page = assignments.locator("[data-assignment]").evaluate_all("nodes=>nodes.map(node=>node.dataset.assignment)")
    assert not set(first_page).intersection(second_page), "Pagination must not repeat assignments"
    root.get_by_role("button", name="Previous results", exact=True).click()
    expect(assignments.locator("[data-assignment]")).to_have_count(8)
    # Cards follow the shared stage rule: a registration job is no printing, an untouched assignment offers Continue.
    def card(key):
        item = next(a for a in documents.catalog()["assignments"] if a["assignment_key"] == key and a["definition_id"] == owners["MATH 140"])
        return assignments.locator(f'article[data-assignment-card="{item["id"]}"]')
    search.fill("weekly")
    weekly1, weekly2 = card("weekly-1"), card("weekly-2")
    expect(weekly1.locator('[data-pipeline-step="paper"]')).to_have_class(re.compile(r"\bis-done\b"))
    expect(weekly1.locator('[data-pipeline-step="print"]')).to_have_class(re.compile(r"\bis-idle\b"))
    expect(weekly1.locator("[data-assignment-next]")).to_have_attribute("data-assignment-next", "seating")
    expect(weekly2.locator(".button.primary[data-assignment]")).to_have_count(1)
    expect(weekly2.locator(".document-card-facts")).to_contain_text("No paper yet")
    search.fill("scanner")
    finished = card("scanner-test")
    expect(finished.locator("[data-pipeline-step].is-done")).to_have_count(3)
    # No preparation stage is left to claim the primary. Creating it prepared its term's grading (ASSIGNMENT-WORKSPACE), so the
    # card's one primary is Grading (DOCUMENTS: grading_entry_plan_id).
    expect(finished.locator(".button.primary")).to_have_count(1)
    expect(finished.locator(".button.primary")).to_have_attribute("href", re.compile(r"^#grading/"))
    # The stage that matters, in words a phone shows too: what was printed, when and how many (create-8, print-5). Printed without a
    # seating chart, for the term: one stack, never "by section".
    expect(finished.locator(".document-card-stage")).to_have_text(re.compile(r"^Print · Printed \w+ \d+ · 4 copies · one stack$"))
    expect(finished.locator("[data-pipeline-step=seating] a")).to_have_attribute("title", re.compile("No seating chart · printed as one stack"))
    expect(finished.locator(f'[data-assignment-jobs="{scanner["id"]}"]')).to_have_text("2 print jobs")
    clear.click()

    search.fill("  SCANNER  ")
    expect(search).to_be_focused()
    expect(assignments.locator("[data-assignment]")).to_have_count(1)
    expect(assignments.locator(f'[data-assignment="{scanner["id"]}"]')).to_be_visible()
    search.fill("No matching synthetic assignment")
    expect(assignments.locator("[data-assignment]")).to_have_count(0)
    clear.click()
    expect(search).to_have_value("")
    search.fill("practice")
    expect(assignments.locator("[data-assignment]")).to_have_count(2)
    course.select_option(owners["MATH 251"])
    expect(assignments.locator("[data-assignment]")).to_have_count(1)
    expect(assignments.locator(f'[data-assignment="{practice["MATH 251"]["id"]}"]')).to_be_visible()
    job_tab.click()
    expect(job_tab).to_have_attribute("aria-selected", "true")
    expect(status).to_be_visible()
    expect(search).to_have_value("practice")
    expect(course).to_have_value(owners["MATH 251"])
    # The list opens on Current: the cancelled job is history, one status away (print-6).
    expect(status).to_have_value("current")
    expect(batches.locator("[data-batch]")).to_have_count(1)
    status.select_option("all")
    expect(batches.locator("[data-batch]")).to_have_count(2)
    status.select_option("working")
    expect(batches.locator("[data-batch]")).to_have_count(1)
    expect(batches.locator(f'[data-batch="{waiting["id"]}"]')).to_be_visible()
    # A pending job triggers catalog polling. The controls, selection, and focus
    # must remain intact when that asynchronous refresh arrives.
    waiting_row = batches.locator(f'[data-batch="{waiting["id"]}"]')
    waiting_row.focus()
    retained_row = waiting_row.element_handle()
    with page.expect_response(lambda response: response.url.endswith("/api/documents/catalog"), timeout=10000):
        pass
    page.wait_for_timeout(100)
    expect(waiting_row).to_be_focused()
    assert retained_row.evaluate("node=>node.isConnected"), "Unchanged polling must preserve the actual result elements"
    expect(search).to_have_value("practice")
    expect(course).to_have_value(owners["MATH 251"])
    expect(status).to_have_value("working")
    status.select_option("history")
    expect(batches.locator("[data-batch]")).to_have_count(1)
    batches.locator(f'[data-batch="{cancelled["id"]}"]').click()
    expect(page.locator("#document-batch-detail")).to_contain_text("MATH 251")
    expect(page.locator("#document-batch-detail")).to_contain_text("cancelled", ignore_case=True)
    page.get_by_role("button", name="Close dialog", exact=True).click()
    status.select_option("ready")
    expect(batches.locator("[data-batch]")).to_have_count(0)
    clear.click()
    expect(course).to_have_value("")
    expect(status).to_have_value("current")
    status.select_option("ready")
    expect(batches.locator("[data-batch]")).to_have_count(2)
    expect(batches).to_contain_text("Scanner qualification")

    # Secondary print controls stay discoverable without dominating the job's
    # primary download/export actions. One appearance, desktop only (DESIGN-LANGUAGE): no overflow at 1280.
    batches.locator("[data-batch]").first.click()
    detail = page.locator("#document-batch-detail")
    expect(detail.get_by_role("heading", name="Print this job", exact=True)).to_be_visible()
    expect(detail.get_by_role("link", name="Open paper PDF", exact=True)).to_be_visible()
    expect(detail.get_by_role("link", name="Open paper PDF", exact=True)).to_have_class(re.compile(r"\bprimary\b"))
    expect(detail.get_by_role("button", name="Export by email", exact=True)).to_be_visible()
    expect(detail.get_by_role("link", name="Manifest (JSON)", exact=True)).to_be_visible()
    expect(detail.locator(".document-batch-status")).to_have_text("Ready to print")
    expect(detail).to_contain_text("Labeled on every page")
    # Came back in scans: both copies were seen in a scan still in the Inbox.
    expect(detail.locator("[data-batch-scanned]")).to_contain_text("Scanned: 2 of 2 copies")
    expect(detail.locator("[data-batch-scanned] a")).to_have_count(1)
    for selector in ("[data-other-formats]", "[data-copy-assignments]", "[data-batch-audit]"):
        assert not detail.locator(selector).evaluate("node=>node.open"), f"{selector} starts closed"
    expect(detail.get_by_role("link", name="Labels · DK-1201", exact=True)).to_be_visible()
    detail.locator("[data-other-formats] > summary").click()
    expect(detail.get_by_role("link", name="Labels · Avery 5160 sheet", exact=True)).to_be_visible()
    detail.locator("[data-other-formats] > summary").click()
    # The audit history is summarised by kind in human words, with every event one level further down.
    detail.locator("[data-batch-audit] > summary").click()
    expect(detail.locator("[data-audit-summary]")).to_contain_text("Print job created")
    expect(detail.locator("[data-audit-summary]")).not_to_contain_text("batch_allocated")
    expect(detail.locator("[data-audit-all] > summary")).to_contain_text("events")
    detail.locator("[data-batch-audit] > summary").click()
    detail.locator("[data-copy-assignments] > summary").click()
    expect(detail.get_by_role("button", name="Change assignment", exact=True)).to_have_count(2)
    detail.locator("[data-copy-assignments] > summary").click()
    for width in (1440, 1280):
        page.set_viewport_size({"width": width, "height": 1080})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Print detail overflow: {width}"
        dialog = page.locator("#dialog")
        assert dialog.evaluate("node=>node.scrollWidth <= node.clientWidth"), f"Print detail dialog overflow: {width}"
        dialog.evaluate("node=>node.scrollTop=0")
        dialog.screenshot(path=f"test-results/documents-detail-{width}.png")
    page.get_by_role("button", name="Close dialog", exact=True).click()
    page.set_viewport_size({"width": 1440, "height": 1080})

    assignment_tab.click()
    search.fill("scanner")
    assignments.locator(f'[data-assignment-jobs="{scanner["id"]}"]').click()
    expect(job_tab).to_have_attribute("aria-selected", "true")
    expect(batches.locator("[data-batch]")).to_have_count(2)
    expect(batches).not_to_contain_text("Practice set")
    clear.click()
    # Current jobs: the cancelled one is left out, and the badge and the results line count what the list shows, print jobs only
    # (the registration job sits apart, folded, with its own count).
    expect(batches.locator("[data-batch]")).to_have_count(4)
    expect(batches.locator("[data-export-group]")).to_have_count(3)
    expect(job_tab.locator('[data-count="batches"]')).to_have_text("3")
    expect(root.locator("#document-results")).to_contain_text("3 print jobs in 3 groups")
    status.select_option("all")
    expect(batches.locator("[data-batch]")).to_have_count(5)
    # Jobs are grouped by export, each header naming the files to print; the registration job sits apart, folded.
    expect(batches.locator("[data-export-group]")).to_have_count(4)
    scanner_group = batches.locator("section[data-export-group]").filter(has_text="Scanner qualification").first
    # A one-job group: its row names the section, copies and date, so the header does not repeat them.
    expect(scanner_group.locator(".document-export-facts")).not_to_contain_text("copies")
    expect(scanner_group.locator(".document-job-quantity")).to_contain_text("2")
    # Both copies came back in a scan still in the Inbox.
    expect(scanner_group.locator(".document-export-facts")).to_contain_text("Scanned 2/2")
    expect(scanner_group.get_by_role("link", name="Paper PDF", exact=True)).to_be_visible()
    expect(scanner_group.locator(".document-status")).to_have_text("Ready to print")
    registration_group = batches.locator("[data-registration-jobs]")
    expect(registration_group.locator("summary")).to_contain_text("Issued for scanned papers")
    # Its count is the fold's hint, as on every other fold (2026-10-02).
    expect(registration_group.locator("summary .disclosure-hint")).to_have_text("1")
    expect(registration_group.locator("summary")).to_contain_text("Not for printing")
    assert not registration_group.evaluate("node=>node.open"), "Registration jobs start folded"
    registration_group.locator("summary").click()
    registration_group.locator(f'[data-batch="{registration["id"]}"]').click()
    registration_detail = page.locator("#document-batch-detail")
    expect(registration_detail).to_contain_text("Not for printing")
    expect(registration_detail.get_by_role("heading", name="Print this batch")).to_have_count(0)
    expect(registration_detail.get_by_role("button", name="Print more copies…")).to_have_count(0)
    page.get_by_role("button", name="Close dialog", exact=True).click()
    registration_group.locator("summary").click()

    # A deep link opens its exact retained job even with a different course in
    # the listing controls; a course filter must not become an access boundary. (It opens in its assignment's paper and
    # printing view, ASSIGNMENT-WORKSPACE, which keeps the link's address.)
    course.select_option(owners["MATH 140"])
    page.goto(address + f"/#documents?batch={cancelled['id']}")
    expect(page.locator("#document-batch-detail")).to_contain_text("MATH 251")
    page.get_by_role("button", name="Close dialog", exact=True).click()
    expect(root.get_by_role("heading", name="Paper and printing")).to_be_visible()
    page.goto(library)

    # A transient catalog failure must not consume the requested job. Retrying
    # should still open the exact deep-linked job once the catalog is available.
    catalog_available = False
    def fail_catalog_until_retry(route):
        if not catalog_available:
            route.fulfill(status=503, json={"error": "Synthetic temporary catalog failure"})
        else:
            route.continue_()
    page.route("**/api/documents/catalog", fail_catalog_until_retry)
    page.goto(address + f"/#documents?batch={cancelled['id']}")
    failure = root.locator("[data-workspace-error]")
    expect(failure).to_contain_text("This assignment could not be read")
    expect(failure).to_contain_text("Synthetic temporary catalog failure")
    expect(page.locator("#dialog")).not_to_be_visible()
    # The shell's follow-up read after a navigation re-renders the page once more; let it fail too before the catalog
    # comes back, so only Try again can open the job.
    page.wait_for_load_state("networkidle")
    expect(failure).to_be_visible()
    expect(page.locator("#dialog")).not_to_be_visible()
    catalog_available = True
    failure.get_by_role("button", name="Try again", exact=True).click()
    expect(page.locator("#document-batch-detail")).to_contain_text("MATH 251")
    page.get_by_role("button", name="Close dialog", exact=True).click()
    expect(failure).to_have_count(0)
    page.unroute("**/api/documents/catalog", fail_catalog_until_retry)
    page.goto(library)
    clear.click()

    # A failed job is announced by one clickable line that filters to it.
    def failed_catalog(route):
        response = route.fetch()
        data = response.json()
        for item in data["batches"]:
            if item["id"] == waiting["id"]:
                item["status"] = "failed"
        route.fulfill(response=response, json=data)
    page.route("**/api/documents/catalog", failed_catalog)
    page.goto(library)
    job_tab.click()
    attention = root.get_by_role("button", name="1 job needs attention · Show", exact=True)
    expect(attention).to_be_visible()
    attention.click()
    expect(status).to_have_value("attention")
    expect(batches.locator("[data-batch]")).to_have_count(1)
    expect(batches.locator(f'[data-batch="{waiting["id"]}"]')).to_be_visible()
    page.unroute("**/api/documents/catalog", failed_catalog)
    clear.click()

    # Print more copies: extra unassigned copies make a new job with new stamps; the earlier job stays.
    status.select_option("ready")
    scanner_job = batches.locator("[data-batch]").first.get_attribute("data-batch")
    batches.locator(f'[data-batch="{scanner_job}"]').click()
    page.get_by_role("button", name="Print more copies…", exact=True).click()
    reprint = page.locator("#document-reprint")
    expect(reprint).to_contain_text("new stamps")
    expect(reprint.get_by_label("Number of extra copies")).to_be_disabled()
    reprint.get_by_label("Extra unassigned copies", exact=True).check()
    reprint.get_by_label("Number of extra copies").fill("3")
    before = {item["id"] for item in app.extensions["documents"].catalog()["batches"]}
    reprint.get_by_role("button", name="Create print job", exact=True).click()
    expect(page.locator("#document-batch-detail")).to_contain_text("3 copies")
    made = [item for item in app.extensions["documents"].catalog()["batches"] if item["id"] not in before]
    assert len(made) == 1 and made[0]["quantity"] == 3 and made[0]["reprint_of_batch_id"] is None, made
    assert all(copy["enrollment_id"] is None for copy in app.extensions["documents"].get_batch(made[0]["id"])["copies"])
    page.get_by_role("button", name="Close dialog", exact=True).click()

    # Supersede sits in the job's menu and asks first: nothing happens until the consequences are ticked.
    batches.locator(f'[data-batch="{scanner_job}"]').click()
    detail = page.locator("#document-batch-detail")
    detail.get_by_role("button", name="Job actions", exact=True).click()
    page.get_by_role("menuitem", name="Supersede print job…", exact=True).click()
    confirm = page.locator("#document-supersede")
    expect(confirm).to_contain_text("cannot be undone")
    submit = confirm.get_by_role("button", name="Supersede print job", exact=True)
    expect(submit).to_be_disabled()
    assert not app.extensions["documents"].get_batch(scanner_job)["superseded_at"]
    confirm.get_by_role("button", name="Back to print job", exact=True).click()
    expect(page.locator("#document-batch-detail")).to_be_visible()
    assert not app.extensions["documents"].get_batch(scanner_job)["superseded_at"]
    page.locator("#document-batch-detail").get_by_role("button", name="Job actions", exact=True).click()
    page.get_by_role("menuitem", name="Supersede print job…", exact=True).click()
    confirm.get_by_label("I understand that scans of these 2 copies will count as superseded").check()
    submit.click()
    expect(page.locator("#document-batch-detail .document-batch-status")).to_have_text("Superseded")
    assert app.extensions["documents"].get_batch(scanner_job)["superseded_at"]
    page.get_by_role("button", name="Close dialog", exact=True).click()
    clear.click()

    # Certified grades archive print jobs (a view, mocked here). A card's "N print jobs" counts the list its button opens: only its
    # current jobs while some are (an earlier term's archived ones left out), else every archived one, superseded included. An
    # empty Current list names the status that holds the jobs instead of looping back to Current.
    extra_job = made[0]["id"]
    scanner_versions = {v["id"] for v in app.extensions["documents"].catalog()["versions"] if v["assignment_id"] == scanner["id"]}
    certification = {"plan_id": "synthetic-plan", "certification_id": "synthetic-certification", "certified_at": "2099-01-01T00:00:00+00:00"}
    archive = {"mode": "earlier"}
    def certified_catalog(route):
        response = route.fetch()
        data = response.json()
        for item in data["batches"]:
            if archive["mode"] == "all" or (item["assignment_version_id"] in scanner_versions and item["id"] != extra_job):
                item["archived"] = certification
        for row in data["lifecycle"]:
            if archive["mode"] == "all" and row["assignment_id"] == scanner["id"]:
                row["archived"] = certification
        route.fulfill(response=response, json=data)
    page.route("**/api/documents/catalog", certified_catalog)
    page.reload()
    assignment_tab.click()
    search.fill("scanner")
    jobs_button = assignments.locator(f'[data-assignment-jobs="{scanner["id"]}"]')
    expect(jobs_button).to_have_text("1 print job")
    jobs_button.click()
    expect(status).to_have_value("current")
    expect(root.locator("#document-results")).to_contain_text(f"1 print job for {scanner['title']} in 1 group")
    archive["mode"] = "all"
    page.reload()
    assignment_tab.click()
    search.fill("scanner")
    root.locator("[data-document-archived] > summary").click()
    expect(jobs_button).to_have_text("3 print jobs")
    jobs_button.scroll_into_view_if_needed()
    page.screenshot(path="test-results/documents-archived-card.png")
    jobs_button.click()
    expect(status).to_have_value("archived")
    expect(root.locator("#document-results")).to_contain_text(f"3 print jobs for {scanner['title']} in ")
    clear.click()
    expect(status).to_have_value("current")
    advice = batches.locator(".empty-state")
    expect(advice).to_contain_text("No current print jobs")
    expect(advice).to_contain_text("Archived (grades certified)")
    for width in (1440, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Archived advice overflow: {width}"
        page.screenshot(path=f"test-results/documents-archived-advice-{width}.png")
    page.set_viewport_size({"width": 1440, "height": 1080})
    advice.get_by_role("button", name="Show archived jobs", exact=True).click()
    expect(status).to_have_value("archived")
    expect(batches.locator(f'[data-batch="{extra_job}"]')).to_be_visible()
    status.select_option("all")
    expect(batches.locator(".empty-state")).to_contain_text("Every print job is archived")
    page.unroute("**/api/documents/catalog", certified_catalog)
    page.reload()

    for tab, name in ((assignment_tab, "assignments"), (job_tab, "jobs")):
        tab.click()
        for width in (1440, 1280):
            page.set_viewport_size({"width": width, "height": 1080})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{name} overflow: {width}"
            page.screenshot(path=f"test-results/documents-{name}-{width}.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1080})
    # A link to an assignment the catalog does not list says so, as ?assignment= does.
    page.goto(address + "/#documents?jobs=no-such-assignment")
    expect(page.locator("#toast")).to_contain_text("That assignment is no longer listed.")
    assert page.evaluate("documentCsp") == []


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        seed_course(app.test_client(), sections=[501,502])
        inbox = app.extensions["inbox"]
        exports = app.extensions["print_email_exports"]
        exports.config = EmailExportConfig(("instructor@tamu.edu", "owner@example.test"))
        exports.gmail = SimpleNamespace(connected=True, email="instructor@tamu.edu")  # send is replaced below; Gmail is never reached
        sent = []
        def fake_send(job, content):
            sent.append((job["id"], job["recipient"], content))
            return {"export_id":job["id"], "attachment_sha256":job["output_sha256"], "status":"accepted", "message_id":f"browser-test-{len(sent)}"}
        exports.send = fake_send
        # DOCUMENTS "SEND TO PRINTER": a fake IPP printer on loopback; no check reaches the office printer.
        printer = FakePrinter()
        app.extensions["printer_sends"].config = PrinterConfig(printer.uri)
        stop = threading.Event()
        # A job printed without a seating chart names no section, so the worker reads the name on its copies' first pages
        # (copy_names); this check never runs the local OCR model: a reading fails at once and the papers wait for the instructor.
        unread = patch("faculty_ocr.recognize_covers", side_effect=OcrError("The local OCR model is not part of this check."))
        unread.start()
        worker = threading.Thread(target=inbox.worker, args=(stop,), daemon=True)
        worker.start()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width":1440,"height":1080}, accept_downloads=True, reduced_motion="reduce")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.documentCsp=[];document.addEventListener('securitypolicyviolation',e=>documentCsp.push(e.violatedDirective))")
                # The wizard lists its demonstration fixtures only on a page marked for testing (print-14).
                page.add_init_script("document.documentElement && (document.documentElement.dataset.testing='1');document.addEventListener('DOMContentLoaded',()=>{document.documentElement.dataset.testing='1'})")
                # The former Assignments & printing leads to Assignments (ASSIGNMENT-WORKSPACE). The page header holds the one
                # creation entry; the page itself starts with its filters and list.
                page.goto(address + "/#documents")
                expect(page).to_have_url(re.compile(r"#assignments$"))
                expect(page.locator("#page-title")).to_have_text("Assignments")
                expect(page.locator("#assignment-workspace [data-assignment-list]")).to_be_visible()
                expect(page.locator("#primary-action")).to_have_text("Create assignment")
                expect(page.get_by_role("button", name="Create assignment")).to_have_count(1)
                expect(page.locator(".document-print-guide")).to_have_count(0)
                assert not page.context.cookies(), 'Faculty should not issue a workspace session'
                page.context.add_cookies([{'name':'faculty_workspace','value':'expired-cookie-from-before-restart','url':address}])
                # One window: details, no seating chart, a fixture version, then a print job for one section.
                page.get_by_role("button", name="Create assignment").click()
                page.get_by_role("button", name="Create paper and print", exact=True).click()
                wizard = page.locator("#assignment-wizard")
                expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text("Details")
                wizard.get_by_text("Advanced: permanent assignment key", exact=True).click()
                wizard.get_by_label("Assignment key", exact=True).fill("scanner-test")
                wizard.get_by_label("Assignment title", exact=True).fill("Scanner qualification")
                expect(wizard.locator('input[name="section"]:checked')).to_have_count(2)
                wizard.get_by_label("Section 502").uncheck()
                wizard.get_by_role("button", name="Next").click()
                # No seating requested: go straight from Details to Paper.
                expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text("Paper")
                wizard.get_by_label("New version label").fill("Test 1")
                wizard.get_by_label("Source", exact=True).select_option("letter")
                wizard.get_by_role("button", name="Next").click()
                expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text("Stamp and print")
                expect(wizard.get_by_label("Physical copies for Section 502", exact=True)).to_have_count(0)
                wizard.get_by_label("Physical copies for Section 501", exact=True).fill("2")
                wizard.get_by_label("Physical copies for Section 501", exact=True).blur()
                wizard.get_by_text("Choose students copy by copy").click()
                expect(wizard.locator('.wizard-students [data-student-picker]')).to_have_count(2)
                wizard.get_by_role("button", name="Create print job").click()
                expect(wizard.get_by_role("button", name="Create print job")).to_have_count(0)
                wizard.get_by_role("button", name="Open print job").click()
                expect(page.locator("#document-batch-detail")).to_be_visible()
                for _ in range(10):
                    if page.get_by_role("link", name="Open paper PDF").count(): break
                    page.wait_for_timeout(600)
                    page.get_by_role("button", name="Refresh status", exact=True).click()
                # The print PDF opens in the app's file viewer (VIEWER); its Download saves the print file itself.
                page.get_by_role("link", name="Open paper PDF").click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator("figure.file-viewer-page").first).to_be_visible(timeout=20000)
                with page.expect_download() as capture:
                    viewer.get_by_role("link", name="Download").click()
                viewer.get_by_role("button", name="Close the viewer").click()
                pdf = Path(capture.value.path()).read_bytes()
                # The job is listed in its assignment's Create area (ASSIGNMENT-WORKSPACE): drive export reopens it there after reloads.
                page.get_by_role("button", name="Close dialog", exact=True).click()
                page.locator("[data-assignment-list]").get_by_role("link", name="Scanner qualification", exact=True).click()
                page.locator("[data-assignment-nav]").get_by_role("link", name="Create", exact=True).click()
                created_job = page.locator("#assignment-workspace [data-assignment-body] [data-batch]")
                expect(created_job).to_have_count(1)
                created_job.click()
                expect(page.locator("#document-batch-detail").get_by_role("link", name="Open paper PDF")).to_be_visible()
                verify_drive_export(page, pdf)
                page.get_by_role("button", name="Export by email").click()
                email_form = page.locator("#document-email-form")
                expect(email_form.get_by_label("Send to", exact=True)).to_have_value("instructor@tamu.edu")
                expect(email_form.get_by_label("Custom email address", exact=True)).to_be_hidden()
                # Lose the first API response after the real submission commits.
                # Clicking Send again must reuse the same pending/sent export.
                lost = []
                def lose_once(route):
                    if route.request.method == "POST" and not lost:
                        route.fetch(); lost.append(True); route.abort()
                    else: route.continue_()
                page.route("**/api/documents/batches/*/email", lose_once)
                email_form.get_by_role("button", name="Send email", exact=True).click()
                expect(email_form.locator('[role="alert"]')).not_to_be_empty()
                email_form.get_by_role("button", name="Send email", exact=True).click()
                expect(page.locator("#document-email-history")).to_contain_text("Accepted for delivery", timeout=15000)
                accepted_button = email_form.get_by_role("button", name="Email accepted", exact=True)
                expect(accepted_button).to_be_disabled()
                expect(accepted_button).to_have_attribute("aria-busy", "false")
                expect(accepted_button).to_have_css("cursor", "not-allowed")
                expect(page.locator("#document-email-result")).to_contain_text("You can close this window.")
                assert len(sent) == 1 and sent[0][1] == "instructor@tamu.edu" and sent[0][2] == pdf
                email_form.get_by_label("Send to", exact=True).select_option("owner@example.test")
                email_form.get_by_role("button", name="Send email", exact=True).click()
                expect(page.locator("#document-email-history strong").filter(has_text="Accepted for delivery")).to_have_count(2, timeout=15000)
                email_form.get_by_label("Send to", exact=True).select_option("__custom__")
                email_form.get_by_label("Custom email address", exact=True).fill("custom@example.test")
                email_form.get_by_role("button", name="Send email", exact=True).click()
                expect(page.locator("#document-email-history strong").filter(has_text="Accepted for delivery")).to_have_count(3, timeout=15000)
                assert [entry[1] for entry in sent] == ["instructor@tamu.edu", "owner@example.test", "custom@example.test"]
                assert all(entry[2] == pdf for entry in sent)
                for width in (1440, 1280):
                    page.set_viewport_size({"width":width,"height":1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Email export overflow at {width}"
                Path("test-results").mkdir(exist_ok=True)
                page.screenshot(path="test-results/document-email-export.png",full_page=True)
                page.get_by_role("button", name="Back to print job").click()
                # Print at Home sits beside the paper PDF; the window reads the printer and prints the same verified PDF once.
                page.get_by_role("button", name="Print at Home", exact=True).click()
                printer_form = page.locator("#document-printer-form")
                expect(page.locator("[data-printer-status]")).to_have_text("Brother MFC-L5915DW series · Ready")
                expect(page.locator("#document-printer-history")).to_contain_text("Nothing printed at home yet.")
                printer_form.get_by_role("button", name="Print at Home", exact=True).click()
                expect(page.locator("#document-printer-result")).to_contain_text("Printer accepted the job", timeout=15000)
                expect(printer_form.get_by_role("button", name="Printer accepted the job", exact=True)).to_be_disabled()
                assert printer.documents == [pdf], "The printer should receive the verified print PDF once"
                for width in (1440, 1280):
                    page.set_viewport_size({"width":width,"height":1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Printer window overflow at {width}"
                page.screenshot(path="test-results/document-printer-send.png",full_page=True)
                page.get_by_role("button", name="Back to print job").click()
                page.get_by_role("button", name="Print at Home", exact=True).click()
                expect(page.locator("[data-printer-before]")).to_contain_text("Sending again prints the same stamps twice.")
                page.get_by_role("button", name="Back to print job").click()
                # The PDF opened in the viewer has Print at Home too (requirement, 2026-10-18); its Back returns to the file.
                page.locator("#document-batch-detail a.print-files-primary").click()
                viewer = page.locator("dialog.file-viewer")
                viewer.get_by_role("button", name="Print at Home", exact=True).click()
                expect(page.locator("[data-printer-status]")).to_have_text("Brother MFC-L5915DW series · Ready")
                expect(page.locator("[data-printer-before]")).to_be_visible()
                page.get_by_role("button", name="Back to print job").click()
                expect(viewer.get_by_role("button", name="Print at Home", exact=True)).to_be_visible()
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(page.locator("#dialog")).not_to_be_visible()  # Back closed the Print at Home window as well
                job = inbox.submit({"action":"scan.receive"}, [{"filename":"scan.pdf","content":pdf}],
                                   source="email",sender="scanner",instructor_review_required=True)
                page.goto(address + "/#inbox/job/" + job["id"] + "/pages")
                expect(page.locator(".document-scan-review")).to_be_visible()
                # Every page is folded under "All N pages" (drawn only while open); the fold appears once pages are retained.
                for _ in range(20):
                    if page.locator("[data-scan-all-pages]").count(): break
                    page.wait_for_timeout(500)
                    page.get_by_role("button", name="Refresh page review").click()
                page.locator("[data-scan-all-pages] > summary").click()
                for _ in range(20):
                    if page.locator(".document-scan-page").count() == 6: break
                    page.wait_for_timeout(500)
                    page.get_by_role("button", name="Refresh page review").click()
                expect(page.locator(".document-scan-page")).to_have_count(6)
                # A refresh still in flight re-renders the panel and would wipe the form filled next.
                page.wait_for_load_state("networkidle")
                # Assemblies are on the Review page; its heading says copies with every page become reviewed papers when the scan
                # passes review (`SequenceService.assemble_reviewed`, 2026-09-28), and one can still be saved by hand before that.
                page.locator("[data-scan-flow] [data-pipeline-step='review'] a").click()
                expect(page.locator("[data-scan-content] .students-page-heading")).to_contain_text(
                    "2 copies have every page and become their students’ reviewed papers when the scan passes review.")
                expect(page.locator(".document-assembly")).to_have_count(0)  # folded, and drawn only once opened
                page.locator("[data-scan-assemblies] > summary").click()
                expect(page.locator(".document-assembly")).to_have_count(2)
                assembly = page.locator("form[data-assembly]").first
                # Each page names where it was scanned, with a picture, and its only observation is already chosen.
                first_page = assembly.locator('select[name="selection"]').first
                expect(first_page).not_to_have_value("")
                expect(first_page.locator("option:checked")).to_have_text(re.compile(r"^This scan · attachment page [14]$"))
                expect(assembly.locator(".document-assembly-thumbs img")).to_have_count(3)
                for select in assembly.locator('select[name="selection"]').all(): select.select_option(index=1)
                assembly.get_by_label("Review reason").fill("Confirmed three pages in order")
                assembly.get_by_role("button", name="Save reviewed paper").click()
                expect(page.get_by_role("link", name="Reviewed pages (PDF)")).to_have_count(1)  # opens in the file viewer (VIEWER)
                # Reviewed with nothing to settle, the paper shows its revision and folds its form under "Edit pages…".
                edit = page.locator("[data-assembly-edit]")
                expect(edit).to_have_count(1)
                assert not edit.evaluate("node => node.open")
                expect(page.locator("form[data-assembly]")).to_have_count(1)  # the other paper's form; this one's is folded
                edit.locator("> summary").click()
                expect(edit.locator("form[data-assembly] select")).to_have_count(3)
                for width in (1440,1280):
                    page.set_viewport_size({"width":width,"height":1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Scan review overflow at {width}"
                Path("test-results").mkdir(exist_ok=True)
                page.screenshot(path="test-results/document-review.png",full_page=True)
                page.goto(address + "/#documents")
                expect(page.get_by_role("button", name="Create assignment")).to_be_visible()
                expect(page.locator("[data-assignment-list]")).to_contain_text("Scanner qualification")
                for width in (1440,1280):
                    page.set_viewport_size({"width":width,"height":1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Printing overflow at {width}"
                page.set_viewport_size({"width":1440,"height":1080})
                page.screenshot(path="test-results/document-printing-desktop.png",full_page=True)
                # Upload the separate corner-layout source through the real UI.
                # Continue the listed assignment from its Create area (ASSIGNMENT-WORKSPACE): Add or revise paper opens the window at
                # Paper; back at its Details, its key and title are fixed, and a second version is compiled.
                page.locator('[data-assignment-list]').get_by_role('link', name='Scanner qualification', exact=True).click()
                page.locator('[data-assignment-nav]').get_by_role('link', name='Create', exact=True).click()
                page.locator('#assignment-workspace').get_by_role('button', name='Add or revise paper', exact=True).click()
                wizard = page.locator('#assignment-wizard')
                expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text('Paper')
                wizard.get_by_role('button', name='Back', exact=True).click()
                expect(wizard.locator('.wizard-steps [aria-current="step"]')).to_have_text('Details')
                wizard.get_by_text('Advanced: permanent assignment key', exact=True).click()
                expect(wizard.get_by_label('Assignment key', exact=True)).to_be_disabled()
                expect(wizard.get_by_label('Assignment title', exact=True)).to_have_value('Scanner qualification')
                wizard.get_by_label('Section 502').uncheck()
                wizard.get_by_role('button', name='Next').click()
                expect(wizard.get_by_label('Use a registered PDF')).to_be_checked()
                expect(wizard.locator('select[name="version_id"] option')).to_have_count(1)
                wizard.get_by_label('Faculty prints it from LaTeX').check()
                wizard.get_by_label('New version label').fill('Test 2 - corner codes')
                wizard.get_by_label('Source', exact=True).select_option('compile')
                source = (Path(__file__).resolve().parents[1] / 'document_identity/fixtures/letter-corners.tex').read_bytes()
                wizard.locator('input[name="files"]').set_input_files({'name':'main.tex','mimeType':'application/x-tex','buffer':source})
                wizard.get_by_role('button', name='Next').click()
                expect(wizard).to_contain_text('Version Test 2 - corner codes · 3 pages.')
                # Section 501 was printed before in Version Test 1: that job is named apart, not as this version's (create-1: only
                # a job of the same version is "Already printed" and starts the copies at 0).
                expect(wizard.locator('[data-printed-other]')).to_contain_text('Other versions printed: Version Test 1 · ')
                expect(wizard.locator('[data-printed-before]')).to_have_count(0)
                wizard.get_by_label('Physical copies for Section 501', exact=True).fill('2')
                wizard.get_by_role('button',name='Create print job').click()
                wizard.get_by_role('button',name='Open print job').click()
                expect(page.locator('.document-layout-description')).to_contain_text('Two 10 mm Data Matrix marks per page')
                for _ in range(20):
                    if page.get_by_role('link',name='Open paper PDF',exact=True).count(): break
                    page.wait_for_timeout(500)
                    page.get_by_role('button',name='Refresh status',exact=True).click()
                page.get_by_role('link',name='Open paper PDF',exact=True).click()
                viewer=page.locator('dialog.file-viewer')
                expect(viewer.locator('figure.file-viewer-page').first).to_be_visible(timeout=20000)
                with page.expect_download() as capture:
                    viewer.get_by_role('link',name='Download').click()
                viewer.get_by_role('button',name='Close the viewer').click()
                corner_pdf=Path(capture.value.path()).read_bytes()
                corner_job=inbox.submit({'action':'scan.receive'},[{'filename':'corner-scan.pdf','content':corner_pdf}],
                                        source='email',sender='scanner',instructor_review_required=True)
                page.get_by_role('button',name='Close dialog',exact=True).click()
                with page.expect_response(lambda response: response.url.endswith('/api/documents/messages/'+corner_job['message_id'])):
                    page.goto(address+'/#inbox/job/'+corner_job['id']+'/pages')  # the Scan stage: every page, under All pages
                for _ in range(20):
                    if page.locator('[data-scan-all-pages]').count(): break
                    page.wait_for_timeout(500)
                    page.get_by_role('button',name='Refresh page review').click()
                page.locator('[data-scan-all-pages] > summary').click()
                for _ in range(20):
                    if page.get_by_text('Its label was read 2 times on this page.',exact=True).count()==6: break
                    page.wait_for_timeout(500)
                    page.get_by_role('button',name='Refresh page review').click()
                expect(page.locator('.document-scan-page')).to_have_count(6)
                expect(page.get_by_text('Its label was read 2 times on this page.',exact=True)).to_have_count(6)
                page.locator("[data-scan-flow] [data-pipeline-step='review'] a").click()
                page.locator('[data-scan-assemblies] > summary').click()
                expect(page.locator('.document-assembly')).to_have_count(2)
                stop.set()
                worker.join(timeout=5)
                assert not worker.is_alive(), "Stop the synthetic worker before seeding stable pending jobs"
                verify_listing(page, app, address)
                assert not errors, errors
                browser.close()
        finally:
            stop.set()
            server.close()
            unread.stop()
    print("Document printing without a browser session, PDF/drive/email exports, Inbox review, corner-layout scan, paginated assignment search, course/job filters, polling/tab state, exact job navigation, desktop layout and CSP checks passed.")


if __name__ == "__main__":
    main()
