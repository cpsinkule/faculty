"""FRONTEND / PERFORMANCE: workspace-owned synthetic checks for fresh follow-up and office-hours navigation.

All records belong to the fixture's enrollments. Delayed responses never reach outside services or the live catalog.
"""
import logging
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_student_support import StudentSupportTests


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    fixture = StudentSupportTests()
    fixture.setUp()
    server = None
    try:
        fixture.incoming()
        assert fixture.save_support(date="2036-10-01").status_code == 201
        server = create_server(fixture.app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(address + "/") else route.abort())
            page = context.new_page()
            page.clock.install()
            page.set_default_timeout(10000)
            errors, reads, states = [], [], []
            hold_state = False
            page.on("pageerror", lambda error: errors.append(str(error)))

            def support_read(route):
                if urlsplit(route.request.url).path != "/api/student-support" or route.request.method != "GET":
                    route.continue_()
                    return
                reads.append({"route": route, "response": route.fetch(),
                              "kind": parse_qs(urlsplit(route.request.url).query)["kind"][0]})

            def state_read(route):
                if hold_state:
                    states.append((route, route.fetch()))
                else:
                    route.continue_()

            page.route("**/api/student-support?*", support_read)
            page.route("**/api/state?*", state_read)
            root = page.locator("#student-support-section")

            def wait_reads(count):
                deadline = time.monotonic() + 10
                while len(reads) < count and time.monotonic() < deadline:
                    page.wait_for_timeout(10)
                assert len(reads) == count, (len(reads), count)
                return reads[-1]

            def release(read, title=None, fail=False):
                data = read["response"].json()
                if title:
                    data["items"][0]["title"] = title
                read["route"].fulfill(status=503 if fail else 200,
                                      json={"error": "Synthetic read failure"} if fail else data)
                page.wait_for_timeout(60)

            def navigate(target, followup_view='action'):
                before = len(reads)
                name = 'Office hours' if target == '#office-hours' else 'Tasks' if followup_view == 'tasks' else 'Action desk'
                navigation = root.get_by_role('navigation', name='Student follow-through')
                navigation.get_by_role('button', name=name, exact=True).click()
                expect(navigation.get_by_role('button')).to_have_text(['Action desk', 'Tasks', 'Office hours'])
                expect(navigation.locator('[aria-pressed="true"]')).to_have_text(name)
                expect(root.locator(".loading-state")).to_be_visible()
                expect(root.locator(".support-card")).to_have_count(0)
                return wait_reads(before + 1)

            page.goto(address + "/#follow-ups")
            first = wait_reads(1)
            expect(root.locator(".loading-state")).to_be_visible()
            release(first)
            expect(root.locator(".support-review")).to_be_visible()

            # A state response arriving after the list must not cause a second list request.
            hold_state = True
            office = navigate("#office-hours")
            page.screenshot(path=str(Path.cwd() / "office-hours-loading.png"))
            release(office)
            expect(root.locator(".support-card")).to_be_visible()
            deadline = time.monotonic() + 10
            while not states and time.monotonic() < deadline:
                page.wait_for_timeout(10)
            assert states
            hold_state = False
            for route, response in states:
                route.fulfill(response=response)
            page.wait_for_timeout(150)
            assert len(reads) == 2, "FRONTEND: duplicate read after shell refresh"

            # Two obsolete reads finish after a fresh visit to the same tab.
            old_followup = navigate("#follow-ups")
            old_office = navigate("#office-hours")
            current_followup = navigate("#follow-ups")
            release(current_followup, "Current follow-up")
            release(old_followup, "Obsolete follow-up")
            release(old_office, fail=True)
            expect(root.locator(".support-review h3")).to_have_text("Current follow-up")
            expect(root).not_to_contain_text("Synthetic read failure")

            # Leaving for another sidebar page also invalidates the visit.
            old_office = navigate("#office-hours")
            page.evaluate("location.hash = '#courses'")
            expect(root).not_to_be_visible()
            before = len(reads)
            page.evaluate("location.hash = '#office-hours'")
            current_office = wait_reads(before + 1)
            expect(root.locator(".loading-state")).to_be_visible()
            release(current_office, "Current meeting")
            release(old_office, "Obsolete meeting")
            expect(root).to_contain_text("Current meeting")
            expect(root).not_to_contain_text("Obsolete meeting")

            # A save during a background read gets a fresh response; the pre-save one cannot overwrite it.
            before = len(reads)
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            before_save = wait_reads(before + 1)
            root.get_by_role("button", name="Update", exact=True).click()
            dialog = page.locator("#dialog")
            dialog.get_by_label("Purpose", exact=True).fill("Newly saved meeting")
            dialog.get_by_role("button", name="Save", exact=True).click()
            expect(dialog).not_to_be_visible()
            after_save = wait_reads(before + 2)
            release(after_save)
            expect(root).to_contain_text("Newly saved meeting")
            release(before_save, "Obsolete pre-save meeting")
            expect(root).to_contain_text("Newly saved meeting")
            expect(root).not_to_contain_text("Obsolete pre-save meeting")

            # Quiet refresh keeps typed filters and the current list when its read fails.
            root.locator("[data-support-query]").fill("Newly")
            before = len(reads)
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            release(wait_reads(before + 1), fail=True)
            expect(root).to_contain_text("Refresh could not finish")
            expect(root).to_contain_text("Newly saved meeting")
            expect(root.locator("[data-support-query]")).to_have_value("Newly")

            # A failed first read retains navigation and has an explicit retry.
            release(navigate("#follow-ups"), fail=True)
            expect(root).to_contain_text("The list could not be read")
            before = len(reads)
            root.get_by_role("button", name="Try again", exact=True).click()
            expect(root.locator(".loading-state")).to_be_visible()
            release(wait_reads(before + 1))
            expect(root.locator(".support-review")).to_be_visible()

            # Each destination is reachable directly from Office hours, including Tasks during loading.
            release(navigate('#office-hours'))
            for width in (1440, 1280):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert root.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), 'Office hours overflows its desktop page'
                page.screenshot(path=str(Path.cwd() / f'office-hours-{width}.png'), full_page=True)
            release(navigate('#follow-ups', 'tasks'))
            expect(root.locator('.support-commitments')).to_be_visible()
            before = len(reads)
            root.get_by_role('button', name='Action desk', exact=True).click()
            expect(root.locator('.support-review')).to_be_visible()
            assert len(reads) == before, 'FRONTEND: switching follow-up views rereads the list'

            # Polling stays active; hidden tabs defer it and visibility triggers one fresh read.
            before = len(reads)
            page.clock.run_for(30010)
            release(wait_reads(before + 1))
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})")
            before = len(reads)
            page.clock.run_for(60010)
            page.wait_for_timeout(60)
            assert len(reads) == before, "FRONTEND: hidden-tab poll"
            page.evaluate("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
            release(wait_reads(before + 1))
            expect(root.locator(".support-review")).to_be_visible()
            page.set_viewport_size({"width": 1280, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(Path.cwd() / "followup-navigation-ready.png"))
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print("Follow-up navigation checks passed: immediate tabs, one fresh read, stale-response guards, saves, failures and polls.")


if __name__ == "__main__":
    main()
