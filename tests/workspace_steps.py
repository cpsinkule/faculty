"""Opening a grading step, or a page the grading page's own bar used to link, the way the instructor does inside an assignment
(ASSIGNMENT-WORKSPACE "ONE NAVIGATION", 2026-10-05). There the assignment's tabs are the only navigation: a grading page's step graph
shows only the steps of the tab on screen, so a step of another tab is reached through that tab (Prepare, Grade, Certify) and then,
unless the tab opens on it, its own step; Scan records and Bubble sheets are Collect's buttons; the exam record and the gradebook are
the overview's links. Outside an assignment the grading page keeps its full graph and bar, and the first look finds the link there."""
from playwright.sync_api import expect

AREA = {"rubrics": "Prepare", "locations": "Prepare", "answer-key": "Prepare", "jobs": "Grade", "review": "Grade", "export": "Certify"}
OPENS_ON = {"Prepare": "rubrics", "Grade": "jobs", "Certify": "export"}


def step_link(page, tab):
    return page.locator(f'.grading-tabs a[href$="tab={tab}"]')


def nav(page):
    return page.locator("[data-assignment-nav]")


def _inside(page):
    """Whether the grading page on screen is inside an assignment: its tabs drawn within 8 s (the shell is hidden while it resolves
    the address, so a hidden shell alone does not say the page is outside one)."""
    expect(page.locator("#grading-section .grading-page-bar")).to_be_attached()
    try:  # a locator's wait: the page's CSP refuses the eval a polled wait_for_function needs
        expect(page.locator("#assignment-workspace[data-area] [data-assignment-nav] a").first).to_be_visible(timeout=8000)
        return True
    except AssertionError:
        return False


def open_step(page, tab):
    """The grading step whose tab is `tab` (rubrics, locations, answer-key, jobs, review, export)."""
    inside = _inside(page)
    link = step_link(page, tab).first
    if not inside or link.is_visible():
        link.click()
        return
    area = AREA[tab]
    nav(page).get_by_role("link", name=area, exact=True).click()
    expect(nav(page).get_by_role("link", name=area, exact=True)).to_have_attribute("aria-current", "page")
    if OPENS_ON[area] != tab:
        expect(step_link(page, tab).first).to_be_visible()
        step_link(page, tab).first.click()


def open_scan_records(page):
    """Scan records: the bar's link outside an assignment, Collect's button inside one."""
    if not _inside(page):
        page.locator("[data-grading-scan-records]").click()
        return
    nav(page).get_by_role("link", name="Collect", exact=True).click()
    page.locator("#assignment-workspace [data-assignment-body]").get_by_role("link", name="Scan records", exact=True).click()


def overview_link(page, name):
    """A link of the assignment's overview (Sittings, students and makeups; Manual scores; Gradebook), opened from its tab."""
    nav(page).get_by_role("link", name="Needs you & progress", exact=True).click()
    return page.locator("#assignment-workspace .assignment-links").get_by_role("link", name=name, exact=True)
