"""Grouped course navigation checks against an isolated temporary workspace.

Run from the project root: python tests/browser_course_pages.py
"""

import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from course_code_checks import fill_course_code

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


def seed_course(client, **overrides):
    response = client.post(
        "/api/courses",
        json={
            "name": "Calculus I",
            "code": "MATH 140",
            "term": "Fall",
            "year": 2026,
            "sections": [1, 2, 3, 4],
            "multiple_sections": True,
            "is_current": True,
            "crn": "14001",
            **overrides,
        },
        headers={"X-Teaching-Request": "1", "Origin": "http://localhost"},
    )
    assert response.status_code == 201, response.json
    return response.json


def nav(page, name):
    return page.locator("nav").get_by_role("link", name=name, exact=True)


def check_sections(page, expected):
    tabs = page.get_by_role("tablist", name="Course sections", exact=True)
    expect(tabs.get_by_role("tab")).to_have_text(["Home", *[f"Section {n}" for n in expected]])
    expect(tabs.get_by_role("tab", selected=True)).to_have_count(1)


def tab_url(home_url, tab):
    base, fragment = home_url.split("#", 1)
    route, separator, query = fragment.partition("?")
    return base + "#" + route.rsplit("/", 1)[0] + "/" + tab + separator + query


def check_mutating_course_routes(page, client, address):
    """Editing identity or sections must never strand the open course route."""
    seed_course(client, name="Navigation regression", code="MATH 321", sections=[1, 2], crn="32101")
    page.set_viewport_size({"width": 1440, "height": 1080})
    page.goto(address + "/#current-courses")
    page.reload()
    expect(page.locator(".course-card")).to_have_count(3)
    page.get_by_role("link", name="Open MATH 321 · Fall 2026", exact=True).click()
    old_home = page.url
    page.get_by_role("link", name="Open Section 2", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("tab", name="Section 2", exact=True)).to_be_focused()
    expect(page.get_by_role("tab", name="Section 2", exact=True)).to_have_attribute("aria-selected", "true")

    # A section tab keeps Course settings as a quiet control in its work list; the header's primary is the gradebook's, not it.
    expect(page.locator("#primary-action")).not_to_have_text(re.compile("Course settings"))
    page.locator("#course-panel").get_by_role("button", name="Course settings", exact=True).click()
    fill_course_code(page, "MATH 322")
    page.get_by_label("Term", exact=True).select_option("Summer I")
    page.get_by_role("spinbutton", name="Year", exact=True).fill("2027")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    # A section tab is its gradebook, which names the page.
    expect(page.locator("#page-title")).to_have_text("MATH 322 · Section 2")
    # The band's label repeated the trail ("MATH 322 · Summer I 2027 · Gradebook"), so it is left out (DESIGN-LANGUAGE §5, Ledger).
    expect(page.locator("#page-context")).to_be_hidden()
    expect(page.get_by_role("tabpanel", name="Section 2", exact=True)).to_contain_text("Import this section’s roster")
    assert old_home.split("#course/", 1)[1].split("/", 1)[0] not in page.url
    assert "from=current-courses" in page.url, page.url
    new_home = page.url.replace("/section/2", "/home")

    page.locator("#course-panel").get_by_role("button", name="Course settings", exact=True).click()
    page.get_by_label("Multiple Sections?", exact=True).uncheck()
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("1")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    expect(page).to_have_url(new_home)
    expect(page.get_by_role("tab", name="Home", exact=True)).to_be_focused()
    expect(page.get_by_role("tabpanel", name="Home", exact=True)).to_be_visible()
    check_sections(page, [1])

    # The trail's Courses crumb is the way back (no separate back link), from the keyboard too.
    back = page.locator("#breadcrumb").get_by_role("link", name="Courses", exact=True)
    back.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#page-title")).to_have_text("Current courses")
    expect(page.locator("#page-title")).to_be_focused()
    expect(page.locator(".course-card")).to_have_count(3)
    expect(page.get_by_role("link", name="Open MATH 321 · Fall 2026", exact=True)).to_have_count(0)
    page.get_by_role("link", name="Open MATH 322 · Summer I 2027", exact=True).click()
    # Course settings is a secondary in the course home's header (settings are never the main action, DESIGN-LANGUAGE §1.4).
    page.locator(".page-heading-secondary").get_by_role("button", name="Course settings", exact=True).click()
    page.get_by_role("button", name="Remove", exact=True).click()
    page.get_by_role("button", name="Remove course", exact=True).click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    expect(page.locator("#page-title")).to_have_text("Current courses")
    expect(page.locator("#page-title")).to_be_focused()
    expect(page.locator(".course-card")).to_have_count(2)
    expect(page.locator("#all-courses-count")).to_have_text("4")


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = app.test_client()
        fall = seed_course(client)
        spring = seed_course(client, term="Spring", is_current=False, crn="14002")
        seed_course(client, year=2025, is_current=False, crn="14003")
        seed_course(client, name="Calculus II", code="MATH 240", crn="24001")
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1080})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(address + "/#all-courses")
                expect(page.locator(".course-card")).to_have_count(4)
                expect(page.locator("#all-courses-count")).to_have_text("4")
                expect(page.locator("#current-courses-count")).to_have_text("2")
                fall_link = page.get_by_role("link", name="Open MATH 140 · Fall 2026", exact=True)
                expect(fall_link).to_have_count(1)
                expect(page.get_by_role("link", name="Open MATH 140 · Spring 2026", exact=True, include_hidden=True)).to_have_count(1)
                expect(page.get_by_role("link", name="Open MATH 140 · Fall 2025", exact=True, include_hidden=True)).to_have_count(1)
                expect(page.get_by_role("link", name="Open MATH 240 · Fall 2026", exact=True)).to_have_count(1)

                # Saving another section of an existing term must update its one card.
                page.locator("#primary-action").click()
                page.get_by_label("Course name", exact=True).fill("Calculus I evening")
                fill_course_code(page, "MATH 140")
                page.get_by_label("Term", exact=True).select_option("Fall")
                page.get_by_role("spinbutton", name="Year", exact=True).fill("2026")
                page.get_by_role("spinbutton", name="Section 1", exact=True).fill("5")
                page.get_by_label("CRN", exact=False).fill("55005")
                page.get_by_label("Current course", exact=True).uncheck()
                page.get_by_role("button", name="Add course", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator(".course-card")).to_have_count(4)
                expect(page.locator("#all-courses-count")).to_have_text("4")
                expect(page.locator("#current-courses-count")).to_have_text("2")
                expect(fall_link).to_have_count(1)
                fall_link.click()
                expect(page.locator("#course-detail")).to_be_visible()
                expect(page.locator("#page-title")).to_have_text("MATH 140")
                # The header names the course; its term is the context line and the trail leads back to the course list.
                # The band's label repeated the trail ("Fall 2026"), so it is left out (DESIGN-LANGUAGE §5, Ledger).
                expect(page.locator("#page-context")).to_be_hidden()
                expect(page.locator("#page-subtitle")).to_contain_text("Calculus I")
                trail = page.locator("#breadcrumb")
                expect(trail.get_by_role("link", name="Courses", exact=True)).to_have_attribute("href", "#all-courses")
                expect(trail.locator('[aria-current="page"]')).to_have_text("MATH 140 · Fall 2026")
                check_sections(page, [1, 2, 3, 4, 5])
                expect(page.get_by_role("tab", name="Home", exact=True)).to_have_attribute("aria-selected", "true")
                home_url = page.url
                assert home_url.split("#", 1)[1].partition("?")[0].endswith("/home"), home_url
                assert "#course/" in home_url
                group_id = home_url.split("#course/", 1)[1].split("/", 1)[0]
                assert str(uuid.UUID(group_id)) == group_id
                assert group_id not in (fall["id"], spring["id"]), "A grouped page has its own stable identity"
                expect(page.get_by_role("tabpanel", name="Home", exact=True)).to_be_visible()

                # The term's work hub links to this term's work from the existing list reads; a section tab shows only what
                # names that section. (The Exams list is stubbed; the hub only reads it.)
                counts = client.get("/api/course-groups/summary").json["groups"][group_id]
                hub = browser.new_page(viewport={"width": 1440, "height": 1080})
                hub.route(re.compile(r".*/api/exams(\?.*)?$"), lambda route: route.fulfill(json={"exams": [
                    {"id": "exam-1", "title": "Midterm", "term_id": counts["term_id"], "sections": [{"id": counts["sections"]["1"]["section_id"]}]},
                    {"id": "exam-2", "title": "Elsewhere", "term_id": "another-term", "sections": []}]}))
                hub.goto(home_url)
                overview = hub.locator("#course-term-overview")
                expect(overview.get_by_role("link", name="Midterm", exact=True)).to_have_attribute("href", "#exams/exam-1")
                expect(overview).not_to_contain_text("Elsewhere")
                expect(overview.get_by_role("link", name="Gradebook", exact=True)).to_have_count(0)  # a section tab is its gradebook
                # The course Home's links are in its right-hand column: no empty Records row in the term's work.
                expect(overview.locator(".course-work-row", has_text="Records")).to_have_count(0)
                expect(overview.locator("[data-course-records]")).to_have_count(0)
                hub.get_by_role("tab", name="Section 1", exact=True).click()
                # A section tab's goal is its gradebook: its exams, seating, grading and cards fold into one closed line with counts,
                # remembered per viewer; Records stays in view.
                fold = hub.locator('.course-work details[data-disclosure-key="course:section-work"]')
                expect(fold.locator("summary")).to_contain_text("Assessments")
                expect(fold.locator(".disclosure-hint")).to_have_text("1 exam")
                expect(fold).not_to_have_attribute("open", "")
                expect(hub.locator(".course-work").get_by_role("link", name="Midterm", exact=True)).to_be_hidden()
                expect(hub.locator("[data-course-records]").get_by_role("link", name="Section 1 roster", exact=True)).to_be_visible()
                fold.locator("summary").click()
                expect(hub.locator(".course-work").get_by_role("link", name="Midterm", exact=True)).to_be_visible()
                hub.get_by_role("tab", name="Section 2", exact=True).click()
                expect(hub.locator(".course-work")).to_contain_text("No exams, seating charts, grading or response cards name this section yet.")
                expect(hub.locator(".course-work").get_by_role("link", name="Section 2 roster", exact=True)).to_have_attribute("href", re.compile(r"/section/2/roster$"))
                hub.get_by_role("tab", name="Section 1", exact=True).click()
                expect(hub.locator('.course-work details[data-disclosure-key="course:section-work"]')).to_have_attribute("open", "")  # remembered
                # Only the instructor's own toggles are remembered (2026-10-02): Chromium fires `toggle` when it PARSES a keyed
                # <details open>, which once stored every default-open fold as the viewer's choice, so its default never applied again.
                parsed = hub.evaluate("""() => new Promise(resolve => {
                    const host = document.createElement('div'); host.id = 'drawn-open-check'; document.querySelector('main').append(host);
                    host.innerHTML = '<details class="disclosure" data-disclosure-key="test:drawn-open" open><summary><span>Drawn open</span></summary><div class="disclosure-body">x</div></details>';
                    host.querySelector('details').addEventListener('toggle', () => setTimeout(() => resolve(localStorage.getItem('faculty:disclosure:test:drawn-open')), 0), {once: true});
                })""")
                assert parsed is None, parsed
                drawn = hub.locator('details[data-disclosure-key="test:drawn-open"]')
                drawn.locator("summary").click()
                expect(drawn).not_to_have_attribute("open", "")
                hub.wait_for_function("localStorage.getItem('faculty:disclosure:test:drawn-open') === '0'")
                # A script that opens it again is no choice either: the instructor's close stays remembered.
                hub.evaluate("""() => new Promise(resolve => { const node = document.querySelector('details[data-disclosure-key="test:drawn-open"]');
                    node.addEventListener('toggle', () => setTimeout(resolve, 0), {once: true}); node.open = true; })""")
                assert hub.evaluate("localStorage.getItem('faculty:disclosure:test:drawn-open')") == "0"
                hub.evaluate("document.querySelector('#drawn-open-check').remove()")
                # The roster names itself like the gradebook: the section in the title, Roster at the end of the trail.
                hub.locator("[data-course-records]").get_by_role("link", name="Section 1 roster", exact=True).click()
                expect(hub.locator("#page-title")).to_have_text("MATH 140 · Section 1")
                # The band's label repeated the trail ("Fall 2026 · Roster"), so it is left out (DESIGN-LANGUAGE §5, Ledger).
                expect(hub.locator("#page-context")).to_be_hidden()
                expect(hub.locator("#breadcrumb li")).to_have_text(["Home", "Courses", "MATH 140 · Fall 2026", "Section 1", "Roster"])
                expect(hub.locator("#breadcrumb").get_by_role("link", name="Section 1", exact=True)).to_have_attribute("href", re.compile(r"/section/1\?from="))
                # The roster tab keeps the section's work open: it is not the gradebook.
                expect(hub.locator('.course-work details[data-disclosure-key="course:section-work"]')).to_have_count(0)
                expect(hub.locator(".course-work").get_by_role("link", name="Midterm", exact=True)).to_be_visible()
                hub.close()

                # Course-wide Home content stays the same when changing selected terms.
                shared = page.locator("#course-shared")
                current_sections = page.locator("#course-current-sections")
                expect(shared).to_contain_text("MATH 140")
                expect(shared.locator(".course-term-links").get_by_role("link")).to_have_count(3)
                expect(shared.get_by_role("link", name="Spring 2026", exact=True)).to_be_visible()
                # Fall 2026 holds every current section, so its section tiles are the overview and the strip stays hidden.
                expect(current_sections).to_be_hidden()
                expect(page.locator("#primary-action")).to_be_hidden()
                expect(page.locator(".page-heading-secondary").get_by_role("button", name="Course settings", exact=True)).to_be_visible()
                shared_text = shared.inner_text()
                shared.get_by_role("link", name="Spring 2026", exact=True).click()
                expect(page.locator("#course-term-overview")).to_contain_text("Spring 2026")
                expect(page.locator("#course-shared")).to_have_text(shared_text, use_inner_text=True)
                # From another term, "Teaching now" is the overview of the course's current sections.
                expect(current_sections).to_be_visible()
                expect(current_sections.get_by_role("link")).to_have_count(4)
                expect(current_sections).to_contain_text("Fall 2026")
                expect(current_sections).not_to_contain_text("Spring 2026")
                expect(current_sections).not_to_contain_text("MATH 240")
                expect(current_sections.get_by_role("link", name="Open Section 5 in Fall 2026", exact=True)).to_have_count(0)
                check_sections(page, [1, 2, 3, 4])
                page.locator("#course-current-sections").get_by_role("link", name="Open Section 1 in Fall 2026", exact=True).click()
                expect(page.get_by_role("tabpanel", name="Section 1", exact=True)).to_contain_text("Import this section’s roster")
                expect(page.get_by_role("tabpanel", name="Section 1", exact=True)).not_to_contain_text("14002")
                # A section tab is its gradebook (the roster a link away): the header offers no competing Course settings.
                expect(page.locator("#primary-action")).not_to_have_text(re.compile("Course settings"))
                page.get_by_role("tab", name="Home", exact=True).click()
                expect(page).to_have_url(home_url)

                # The standardized home exposes each stored offering's actual settings.
                page.get_by_role("button", name="Edit Calculus I evening", exact=True).click()
                expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("5")
                expect(page.get_by_label("CRN", exact=False)).to_have_value("55005")
                page.get_by_label("CRN", exact=False).fill("55006")
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator("#course-detail")).to_be_visible()
                expect(page).to_have_url(home_url)

                # Tabs must show data belonging to the selected section only.
                page.get_by_role("tab", name="Section 5", exact=True).click()
                expect(page).to_have_url(tab_url(home_url, "section/5"))
                section = page.get_by_role("tabpanel", name="Section 5", exact=True)
                expect(section).to_be_visible()
                expect(section).to_contain_text("Import this section’s roster")
                expect(section).not_to_contain_text("14001")
                # A section tab shows its gradebook; with no roster yet it links to the roster, where the import is.
                section.get_by_role("link", name="Import this section’s roster").click()
                expect(page).to_have_url(re.compile(r"/section/5/roster"))
                expect(section.get_by_role("button", name="Import roster", exact=True)).to_be_visible()
                page.reload()
                expect(page.get_by_role("tab", name="Section 5", exact=True)).to_have_attribute("aria-selected", "true")
                expect(page.get_by_role("tabpanel", name="Section 5", exact=True)).to_contain_text("Import this section’s roster")
                page.get_by_role("tab", name="Section 1", exact=True).click()
                section = page.get_by_role("tabpanel", name="Section 1", exact=True)
                expect(section).to_contain_text("Import this section’s roster")
                expect(section).not_to_contain_text("55006")
                page.go_back()
                expect(page.get_by_role("tab", name="Section 5", exact=True)).to_have_attribute("aria-selected", "true")
                page.go_forward()
                expect(page.get_by_role("tab", name="Section 1", exact=True)).to_have_attribute("aria-selected", "true")
                page.get_by_role("tab", name="Home", exact=True).click()
                expect(page).to_have_url(home_url)
                page.get_by_role("tab", name="Home", exact=True).focus()
                page.keyboard.press("ArrowRight")
                expect(page.get_by_role("tab", name="Section 1", exact=True)).to_be_focused()
                expect(page.get_by_role("tab", name="Section 1", exact=True)).to_have_attribute("aria-selected", "true")
                page.keyboard.press("End")
                expect(page.get_by_role("tab", name="Section 5", exact=True)).to_be_focused()
                expect(page.get_by_role("tab", name="Section 5", exact=True)).to_have_attribute("aria-selected", "true")
                page.keyboard.press("Home")
                expect(page.get_by_role("tab", name="Home", exact=True)).to_be_focused()
                expect(page.get_by_role("tab", name="Home", exact=True)).to_have_attribute("aria-selected", "true")
                page.screenshot(path=str(output / "course-home-desktop.png"), full_page=True)

                # The trail's Courses crumb goes back to the collection that opened the course (no separate back link).
                expect(page.locator("#course-detail .course-back-link")).to_have_count(0)
                page.locator("#breadcrumb").get_by_role("link", name="Courses", exact=True).click()
                expect(page.locator("#page-title")).to_have_text("All courses")
                nav(page, "Current courses").click()
                expect(page.locator(".course-card")).to_have_count(2)
                expect(page.get_by_role("link", name="Open MATH 140 · Spring 2026", exact=True)).to_have_count(0)
                page.get_by_role("link", name="Open MATH 140 · Fall 2026", exact=True).click()
                check_sections(page, [1, 2, 3, 4, 5])
                expect(page.locator("#page-title")).to_have_text("MATH 140")
                page.reload()
                page.locator("#breadcrumb").get_by_role("link", name="Courses", exact=True).click()
                expect(page.locator("#page-title")).to_have_text("Current courses")

                # A copied deep link works in another browser session.
                second = browser.new_page()
                second.goto(tab_url(home_url, "section/4"))
                expect(second.get_by_role("tabpanel", name="Section 4", exact=True)).to_be_visible()
                # A section tab is its gradebook, which names the section (the course's name shows only until it loads).
                expect(second.locator("#page-title")).to_have_text("MATH 140 · Section 4")
                second.close()
                nav(page, "All courses").click()
                # Terms with no current section fold away in All courses; searching opens them.
                other_terms = page.locator("details.course-other-terms")
                expect(other_terms).not_to_have_attribute("open", "")
                expect(other_terms.locator("summary")).to_contain_text("2 courses")
                expect(page.get_by_role("link", name="Open MATH 140 · Spring 2026", exact=True)).not_to_be_visible()
                page.get_by_label("Find a course", exact=True).fill("Spring")
                expect(other_terms).to_have_attribute("open", "")
                page.get_by_label("Find a course", exact=True).fill("")
                other_terms.locator("summary").click()
                page.get_by_role("link", name="Open MATH 140 · Spring 2026", exact=True).click()
                # The band's label repeated the trail ("Spring 2026"), so it is left out (DESIGN-LANGUAGE §5, Ledger).
                expect(page.locator("#page-context")).to_be_hidden()
                check_sections(page, [1, 2, 3, 4])
                assert group_id not in page.url

                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): at the narrowest desktop width the course home and a section
                # tab fit the window, and the tabs still switch.
                page.goto(home_url)
                page.set_viewport_size({"width": 1280, "height": 900})
                expect(page.locator("#course-detail")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Home overflow at 1280px"
                page.get_by_role("tab", name="Section 5", exact=True).click()
                expect(page.get_by_role("tabpanel", name="Section 5", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Section overflow at 1280px"
                page.get_by_role("tab", name="Home", exact=True).click()
                page.screenshot(path=str(output / "course-home-1280.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1080})

                check_mutating_course_routes(page, client, address)

                # Stale links show an explicit missing state, never unrelated data.
                page.goto(tab_url(home_url, "section/999"))
                expect(page.get_by_role("heading", name="Section not found", exact=False)).to_be_visible()
                expect(page.get_by_role("tabpanel")).not_to_contain_text("55006")
                page.goto(address + f"/#course/{uuid.uuid4()}/home")
                expect(page.get_by_role("heading", name="Course not found", exact=False)).to_be_visible()
                expect(page.locator("#course-detail")).not_to_contain_text("55006")
                nav(page, "All courses").click()
                expect(page.locator(".course-card")).to_have_count(4)
                assert not errors, errors
                browser.close()
                print("Course page browser checks passed: term/code grouping, grouped creation, section data, settings, current filtering, deep links, history, edit/delete navigation, keyboard focus, missing routes, and the 1280px desktop layout.")
        finally:
            server.close()


if __name__ == "__main__":
    main()
