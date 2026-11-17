"""Persistent graphic sidebar, Home dashboard, and original route/action behavior."""
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        # The Inbox badge counts what the Inbox files under Current as needing the instructor (PRESENT only), never stored
        # statuses: a scan with nothing left to do keeps `needs_review` for good but counts nowhere, while one submission
        # waiting for review is the badge's 1 and the tab title's (1).
        inbox = app.extensions["inbox"]
        finished = inbox.submit({"action": "scan.receive"}, [{"filename": "navigation.txt", "content": b"no pages here"}],
                                instructor_review_required=True)
        while app.extensions["document_scans"].process_one():
            pass
        assert inbox.get(finished["id"])["status"] == "needs_review"
        inbox.submit({"action": "document.archive", "target": {"level": "course", "id": "none"}, "metadata": {}},
                     [{"filename": "navigation-note.txt", "content": b"Unauthenticated"}], authenticated=False)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1200}, reduced_motion="reduce")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                page.goto(address)
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                expect(page.locator("body > .sidebar.graphic-navigation")).to_be_visible()
                # Fourteen pages in lifecycle order and Workspace settings (the Connection guide lives inside it). The gradebook is in
                # each course page's tabs since 2026-09-28, not in the sidebar; Canvas has its own entry since 2026-09-30, and so has
                # Absences (9469ef9, the same day); Student follow-up and Teaching review since b6b4cff; one Assignments entry replaces
                # the three assignment pages since ASSIGNMENT-WORKSPACE (4507e55). Teaching review lives under Assignments since
                # 2026-10-18 (review P3: its index strip carries it); its address stays and highlights Assignments. Recordings
                # has its entry since 2026-11-14 (RECORDINGS "The page, routes and the Assistant"; the instructor: "Yes, add it").
                expect(page.locator("#workspace-nav .nav-item")).to_have_count(14)
                expect(page.locator(".sidebar .nav-item")).to_have_count(15)
                assert page.locator("#workspace-nav .nav-item").evaluate_all("links => links.map(link => link.getAttribute('aria-label'))") == [
                    "Home", "Calendar", "Mail", "Student follow-up", "Recordings", "Courses", "Canvas", "Accommodations", "Absences",
                    "Assignments", "Seating charts", "Response Cards", "Teaching Inbox", "Rooms"]
                page.goto(address + "/#teaching-review")
                expect(page.locator('#workspace-nav [data-view="assignments"]')).to_have_attribute("aria-current", "page")
                # A scan page highlights the Teaching Inbox, unless the assignment shell says it is open inside an assignment (I4).
                page.goto(address + "/#inbox/job/none")
                expect(page.locator('#workspace-nav [data-view="inbox"]')).to_have_attribute("aria-current", "page")
                page.evaluate("window.dispatchEvent(new CustomEvent('faculty:assignment-context', {detail: {active: true}}))")
                expect(page.locator('#workspace-nav [data-view="assignments"]')).to_have_attribute("aria-current", "page")
                page.evaluate("window.dispatchEvent(new CustomEvent('faculty:assignment-context', {detail: {active: false}}))")
                expect(page.locator('#workspace-nav [data-view="inbox"]')).to_have_attribute("aria-current", "page")
                page.goto(address + "/#overview")
                expect(page.locator("#workspace-nav .nav-section-label")).to_have_text(["Courses", "Assessments", "Library"])
                expect(page.locator('.sidebar [data-action="guide"]')).to_have_count(0)
                expect(page.locator(".nav-crown")).to_have_count(0)
                expect(page.locator("#primary-action")).to_be_visible()
                # Home's header is the hero; its trail is just Home, and the clock (not a label) gives the date.
                expect(page.locator(".page-heading")).to_have_attribute("data-variant", "hero")
                expect(page.locator("#today")).to_be_hidden()
                expect(page.locator("#breadcrumb li")).to_have_count(1)
                expect(page.locator('#breadcrumb li[aria-current="page"]')).to_have_text("Home")

                # Attention badges come from the server's attention cards; the name stays the page's name. The Inbox card is
                # counted from the Inbox's own filing (derived): the finished scan stored as needs_review is not in it.
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                expect(page.locator("#home-attention")).to_contain_text("Submissions that need you")
                expect(page.locator('#home-attention [data-need="inbox:submissions"] .home-attention-count')).to_have_text("1")
                expect(page.locator("#home-attention")).not_to_contain_text("No scanned pages")
                inbox_link = page.locator('.sidebar [data-view="inbox"]')
                expect(page.locator("#nav-badge-inbox")).to_be_visible()
                expect(page.locator("#nav-badge-inbox")).to_have_text(re.compile(r"^1 needs attention$"))
                expect(inbox_link).to_have_attribute("aria-describedby", "nav-badge-inbox")
                expect(page.get_by_role("link", name="Teaching Inbox", exact=True)).to_have_count(1)
                expect(page).to_have_title(re.compile(r"^\(1\) Home · "))
                expect(page.locator("#nav-badge-grading")).to_be_hidden()

                # The rail's footer stays in view on a short window.
                page.set_viewport_size({"width": 1440, "height": 800})
                settings_box = page.locator('.sidebar [data-action="settings"]').bounding_box()
                assert settings_box["y"] + settings_box["height"] <= 800, settings_box
                expect(page.locator(".sidebar .server-status")).to_be_in_viewport()
                # Laptop heights (rest-shell-3, 2026-10-02): every entry fits a 1440 x 900 window and a 1536 x 864 one, Rooms
                # included (fourteen entries since 2026-11-14), and on a shorter one the page's own entry is scrolled out from
                # under the footer; only the rail moves, never the page.
                for width, height in ((1536, 864), (1440, 900)):
                    page.set_viewport_size({"width": width, "height": height})
                    hidden = page.locator("#workspace-nav .nav-item").evaluate_all('''items => items.filter(item => {
                      const box = item.getBoundingClientRect(), hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
                      return hit?.closest('.nav-item') !== item;
                    }).map(item => item.dataset.view)''')
                    assert hidden == [], (width, height, hidden)
                    assert page.locator("body > .sidebar").evaluate("rail => rail.scrollHeight <= rail.clientHeight"), (width, height)
                # Opening a page reveals its entry; so does making the window shorter on the same page. The entry is the one the
                # rail highlights: All courses is the Courses entry, revealed from a rail scrolled down to Rooms (the lowest entry), and
                # a grading page is the Assignments entry (ASSIGNMENT-WORKSPACE).
                for height, view, entry in ((800, "rooms", "rooms"), (720, "rooms", "rooms"), (720, "inbox", "inbox"),
                                            (480, "rooms", "rooms"), (720, "grading", "assignments"), (480, "all-courses", "current-courses")):
                    page.set_viewport_size({"width": 1280 if height < 800 else 1440, "height": height})
                    page.evaluate("view => { location.hash = view; }", view)
                    expect(page.locator(f'.sidebar nav [data-view="{entry}"]')).to_have_class(re.compile(r"\bactive\b"))
                    shown = '''view => {
                      const entry = document.querySelector(`.sidebar nav [data-view="${view}"]`).getBoundingClientRect();
                      const foot = document.querySelector('.sidebar .sidebar-bottom').getBoundingClientRect();
                      return entry.bottom <= foot.top && entry.top >= document.querySelector('body > .sidebar').getBoundingClientRect().top;
                    }'''
                    for _ in range(40):
                        if page.evaluate(shown, entry):
                            break
                        page.wait_for_timeout(100)
                    assert page.evaluate(shown, entry), (height, view, "the page's own entry is outside the rail's view")
                    assert page.evaluate("scrollY === 0"), (height, view)
                    if height == 720:
                        page.screenshot(path=str(output / f"navigation-short-rail-{view}.png"), clip={"x": 0, "y": 0, "width": 260, "height": 720})
                page.locator('.sidebar [data-view="overview"]').evaluate("item => item.click()")
                page.set_viewport_size({"width": 1440, "height": 1200})
                expect(page.locator("#home-attention")).to_be_visible()
                # Needs you is the first thing under the header band and the hero's sentence.
                assert page.locator(".home-attention").bounding_box()["y"] < 400
                page.evaluate("window.originalRooms = document.querySelector('[data-view=rooms]')")

                # Every destination keeps the same sidebar nodes and opens the
                # original route, with the selected page's heading in view.
                routes = page.locator("#workspace-nav [data-view]").evaluate_all(
                    "links => links.map(link => link.dataset.view).filter(view => view !== 'overview')")
                for route in routes:
                    page.locator(f'.sidebar [data-view="{route}"]').click()
                    expect(page.locator("body")).to_have_attribute("data-workspace-view", route)
                    expect(page.locator("body > .sidebar")).to_be_visible()
                    expect(page.locator(f'#workspace-nav [data-view="{route}"]')).to_have_attribute("aria-current", "page")
                    expect(page.locator("#page-title")).to_be_focused()
                    assert page.evaluate("scrollY === 0")
                    # Every other page has a compact header and a clickable trail back to Home. No page prints the date as the
                    # label: the clock beside the trail shows it (Ledger).
                    expect(page.locator(".page-heading")).to_have_attribute("data-variant", "compact")
                    expect(page.locator("#today")).to_be_hidden()
                    expect(page.locator("#breadcrumb").get_by_role("link", name="Home", exact=True)).to_have_attribute("href", "#overview")
                    expect(page.locator('#breadcrumb li[aria-current="page"]')).to_be_visible()
                    heading = page.locator(".page-heading")
                    assert not heading.is_visible() or heading.bounding_box()["height"] <= 160
                    assert page.evaluate("window.originalRooms === document.querySelector('[data-view=rooms]')")
                    page.locator('.sidebar [data-view="overview"]').click()
                    expect(page.locator("body > .sidebar.graphic-navigation")).to_be_visible()
                    expect(page.locator("#home-attention")).to_be_visible()
                    assert page.evaluate("scrollY === 0")

                # A detail page's trail links back to its list.
                page.locator('.sidebar [data-view="rooms"]').click()
                expect(page.locator("#breadcrumb li")).to_have_text(["Home", "Rooms"])
                page.locator('.sidebar [data-view="overview"]').click()

                # Workspace settings is the settings hub: the guide is a closed section inside it.
                page.locator('.sidebar [data-action="settings"]').click()
                dialog = page.get_by_role("dialog")
                expect(dialog).to_be_visible()
                expect(dialog.get_by_role("heading", name="Workspace settings", exact=True)).to_be_visible()
                expect(dialog.get_by_role("link", name="Grading models and compute", exact=True)).to_have_attribute("href", "#grading?settings=1")
                expect(dialog.get_by_role("heading", name="Your workspace, anywhere.")).to_be_hidden()
                dialog.locator("#settings-access > summary").click()
                expect(dialog.get_by_role("heading", name="Your workspace, anywhere.")).to_be_visible()
                # The dialog's footer stays in view while its body scrolls.
                page.set_viewport_size({"width": 1440, "height": 700})
                expect(dialog.get_by_role("button", name="Save changes", exact=True)).to_be_in_viewport()
                assert dialog.locator(".dialog-body").evaluate("body => body.scrollHeight > body.clientHeight")
                page.set_viewport_size({"width": 1440, "height": 1200})
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()

                # A new page opens at its top with focus on its title; Back returns to where the page was.
                page.set_viewport_size({"width": 1440, "height": 500})
                # As far down as the page goes, up to 400 px (the slim band made Home shorter, 2026-10-04). Polled from here:
                # the app's CSP refuses wait_for_function's string predicates once they have to poll.
                target = page.evaluate("Math.min(400, document.documentElement.scrollHeight - innerHeight)")
                assert target >= 150, target
                page.evaluate(f"window.scrollTo(0, {target})")

                def scrolled_to(value):
                    for _ in range(80):
                        if page.evaluate(f"scrollY === {value}"):
                            return
                        page.wait_for_timeout(100)
                    raise AssertionError(("scroll", value, page.evaluate("scrollY")))
                scrolled_to(target)
                page.wait_for_timeout(300)
                page.locator('.sidebar [data-view="calendar"]').click()
                expect(page.locator("#page-title")).to_be_focused()
                page.wait_for_function("scrollY === 0")
                page.go_back()
                expect(page.locator("#home-attention")).to_be_visible()
                scrolled_to(target)
                page.evaluate("window.scrollTo(0, 0)")
                page.set_viewport_size({"width": 1440, "height": 1200})

                for width, height in ((1440, 1200), (1280, 800)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("window.scrollTo(0, 0)")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                    # Every entry's words are its own hit target.
                    for item in page.locator("#workspace-nav .nav-item:visible").all():
                        item.scroll_into_view_if_needed()
                        assert item.evaluate("""item => {
                          const face = item.querySelector('.nav-face').getBoundingClientRect();
                          const target = document.elementFromPoint(face.x + face.width / 2, face.y + face.height / 2);
                          return target?.closest('.nav-item') === item;
                        }"""), (width, item.inner_text())
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(output / f"navigation-home-{width}.png"), full_page=True)
                    page.locator('#workspace-nav [data-view="rooms"]').focus()
                    page.keyboard.press("Enter")
                    expect(page.locator("body > .sidebar")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "rail")
                    assert page.locator("#workspace-nav .nav-face").evaluate_all("""faces => faces.every(face =>
                      !face.getClientRects().length || (face.scrollHeight <= face.clientHeight && face.scrollWidth <= face.clientWidth))"""), (width, "label fits")
                    page.screenshot(path=str(output / f"navigation-rail-{width}.png"))
                    page.go_back()
                    expect(page.locator("body > .sidebar.graphic-navigation")).to_be_visible()
                    expect(page.locator("#home-attention")).to_be_visible()
                    assert page.evaluate("scrollY === 0")

                # Direct entry and refresh retain the normal sidebar placement.
                page.goto(address + "/#rooms")
                expect(page.locator("body > .sidebar")).to_be_visible()
                page.reload()
                expect(page.locator("body > .sidebar")).to_be_visible()
                page.locator('#workspace-nav [data-view="overview"]').click()
                expect(page.locator("body > .sidebar.graphic-navigation")).to_be_visible()
                assert page.locator("#workspace-nav .nav-item").first.evaluate("el => getComputedStyle(el).transitionDuration") == "0s"
                page.emulate_media(forced_colors="active")
                expect(page.locator('#workspace-nav [data-view="rooms"]')).to_be_visible()
                page.emulate_media(forced_colors="none", media="print")
                expect(page.locator(".page-plate > .topbar")).to_be_hidden()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Navigation checks passed: Home dashboard with persistent styled sidebar, 12 pages plus the settings hub in lifecycle order, attention badge and tab title, sticky rail footer, the header band and breadcrumb trails, node identity, keyboard focus, scroll to top and restore on Back, back/direct entry/reload, desktop widths, hit targets through the arc, reduced motion, forced colors, and print isolation.")


if __name__ == "__main__":
    main()
