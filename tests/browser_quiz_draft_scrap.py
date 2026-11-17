"""Scrap a quiz draft in the browser (QUIZZES "SCRAPPING A DRAFT", requirement, 2026-10-01): the draft page's ⋯ menu opens a confirmation
that names what happens, "Keep the draft" changes nothing, "Scrap draft" stops the running revision, takes the draft off Assignments
(ASSIGNMENT-WORKSPACE: its list of drafts) and returns there; a tab still on the draft's page then says it was scrapped and offers nothing to do. A loopback model host
answers, as in browser_quiz_drafts.py."""
import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_quiz_drafts import QuizDrafts

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = QuizDrafts("test_fragments_cannot_change_the_page_or_read_files")
fixture.setUp()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"


def screenshots(page, name):
    # One appearance, desktop only (DESIGN-LANGUAGE): no overflow at 1500 or 1280.
    for width in (1500, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.screenshot(path=str(OUTPUT / f"{name}-{width}.png"), animations="disabled")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (name, width)
    page.set_viewport_size({"width": 1500, "height": 1100})


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(20000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        drafted = fixture.draft()  # its first revision is still waiting for the model
        # Assignments lists the unapproved draft (the former #documents leads there), linking to its page.
        page.goto(address + "/#documents")
        drafts = page.locator("[data-assignment-drafts]")
        expect(drafts).to_contain_text("Chapter 3 Quiz")
        expect(drafts.get_by_role("link", name="Chapter 3 Quiz", exact=True)).to_have_attribute("href", f"#documents/quiz/{drafted['id']}")
        page.goto(address + f"/#documents/quiz/{drafted['id']}")
        expect(page.locator(".quiz-draft-status")).to_contain_text("Revision 1")
        menu = page.get_by_role("button", name="Draft actions", exact=True)
        menu.click()
        expect(page.get_by_role("menuitem", name="Scrap this draft…", exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT / "quiz-draft-actions-1500.png"), animations="disabled")
        page.get_by_role("menuitem", name="Scrap this draft…", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog.get_by_role("heading", name="Scrap this draft?")).to_be_visible()
        expect(dialog).to_contain_text("revision 1, still in progress, is stopped")
        screenshots(page, "quiz-draft-scrap")
        dialog.get_by_role("button", name="Keep the draft", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert fixture.frq.quiz_draft(drafted["id"])["state"] == "drafting"
        # A second tab stays on the draft's page while the first scraps it.
        other = browser.new_page(viewport={"width": 1500, "height": 1100})
        other.goto(address + f"/#documents/quiz/{drafted['id']}")
        expect(other.locator(".quiz-draft-status")).to_contain_text("Revision 1")
        menu.click()
        page.get_by_role("menuitem", name="Scrap this draft…", exact=True).click()
        dialog.get_by_role("button", name="Scrap draft", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page).to_have_url(re.compile(r"#assignments$"))
        expect(page.locator("#toast")).to_contain_text("Scrapped Chapter 3 Quiz.")
        expect(page.locator("[data-assignment-list]")).to_be_visible()
        expect(drafts).not_to_contain_text("Chapter 3 Quiz")
        fixture.frq.process_cloud()
        assert fixture.host.requests == [], fixture.host.requests
        expect(other.locator(".quiz-draft-page .callout")).to_contain_text("This draft was scrapped.", timeout=10000)  # its 4 s read
        expect(other.get_by_role("button", name="Draft actions")).to_have_count(0)
        expect(other.locator(".quiz-draft-revision")).to_have_count(0)
        screenshots(other, "quiz-draft-scrapped")
        other.get_by_role("link", name="Back to Assignments", exact=True).click()
        expect(other).to_have_url(re.compile(r"#assignments$"))
        expect(other.locator("[data-assignment-list]")).to_be_visible()
        expect(other.locator("[data-assignment-drafts]")).not_to_contain_text("Chapter 3 Quiz")
        assert not errors, errors
        assert not bad, bad
        browser.close()
    print("Quiz draft scrap browser checks passed.")
finally:
    server.close()
    fixture.doCleanups()
