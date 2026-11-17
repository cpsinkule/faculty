"""The command palette (Ctrl K), the '?' shortcut sheet, the rail's system status popover and its notices, and the
phone Menu sheet, in an isolated workspace with synthetic data."""
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from test_seating import HEADERS


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = app.test_client()
        assert client.post("/api/courses", json={"name": "Business Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                 "sections": [507, 508], "multiple_sections": True}, headers=HEADERS).status_code == 201
        group = client.get("/api/state").json["course_groups"][0]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                page.goto(address)
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                palette = page.locator("dialog.palette:not(.shortcut-sheet)")
                search = page.get_by_role("combobox", name="Go to a page, course, assignment or action")

                # Ctrl K opens it on the pages, the actions and nothing else; typing searches the courses too.
                page.keyboard.press("Control+k")
                expect(palette).to_be_visible()
                expect(search).to_be_focused()
                listbox = palette.get_by_role("listbox")
                expect(listbox.get_by_role("option", name="Teaching Inbox", exact=True)).to_be_visible()
                expect(listbox.get_by_role("option", name=re.compile(r"^Upload scanned assignment"))).to_be_visible()
                expect(listbox.get_by_role("option", name=re.compile(r"^MATH 140"))).to_have_count(0)
                search.fill("math 507")
                option = listbox.get_by_role("option", name=re.compile(r"^MATH 140 · Fall 2026 · Section 507"))
                expect(option).to_be_visible()
                expect(option).to_have_attribute("aria-selected", "true")
                expect(search).to_have_attribute("aria-activedescendant", option.get_attribute("id"))
                page.keyboard.press("Enter")
                expect(palette).to_be_hidden()
                expect(page).to_have_url(re.compile(rf"#course/{re.escape(group['id'])}/section/507"))
                # The shell names the course first; the section's gradebook renames the page once its read answers.
                expect(page.locator("#page-title")).to_have_text("MATH 140 · Section 507")

                # The place just visited is offered first next time; arrow keys move and Escape closes.
                page.wait_for_timeout(1800)
                page.locator(".sidebar").get_by_role("button", name="Search and go to").click()
                expect(palette).to_be_visible()
                expect(listbox.locator(".palette-group").first).to_have_text("Recent")
                expect(listbox.get_by_role("option").first).to_contain_text("MATH 140 · Fall 2026")
                page.keyboard.press("ArrowDown")
                expect(listbox.get_by_role("option").nth(1)).to_have_attribute("aria-selected", "true")
                page.keyboard.press("Escape")
                expect(palette).to_be_hidden()

                # An action opens the page's own dialog, with its own checks.
                page.keyboard.press("Control+k")
                search.fill("add a course")
                page.keyboard.press("Enter")
                dialog = page.locator("#dialog")
                expect(dialog.get_by_role("heading", name="Add a course", exact=True)).to_be_visible()
                expect(page).to_have_url(re.compile(r"#current-courses$"))
                # Shortcuts never act while a dialog other than the palette is open, or while typing.
                page.keyboard.press("?")
                expect(page.locator("dialog.shortcut-sheet")).to_have_count(0)
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()

                # '/' searches the course list; typed in a field, '?' is just text.
                page.locator("body").click(position={"x": 700, "y": 20})
                page.keyboard.press("/")
                expect(page.locator("#course-search")).to_be_focused()
                page.keyboard.type("?")
                expect(page.locator("#course-search")).to_have_value("?")
                expect(page.locator("dialog.shortcut-sheet")).to_have_count(0)
                page.locator("#course-search").fill("")

                # Elsewhere '/' opens the palette; '?' opens the shortcut sheet listing this page's shortcuts.
                page.locator('.sidebar [data-view="rooms"]').click()
                expect(page.locator("#page-title")).to_be_focused()
                page.keyboard.press("/")
                expect(palette).to_be_visible()
                page.keyboard.press("Escape")
                page.keyboard.press("?")
                sheet = page.locator("dialog.shortcut-sheet")
                expect(sheet).to_be_visible()
                expect(sheet.get_by_role("heading", name="Keyboard shortcuts")).to_be_visible()
                expect(sheet.locator(".shortcut-list")).to_contain_text("Go to a page, course, assignment or action")
                expect(sheet.locator(".shortcut-list")).to_contain_text("Show these keyboard shortcuts")
                page.screenshot(path=str(output / "palette-shortcuts.png"))
                page.keyboard.press("Escape")
                expect(sheet).to_be_hidden()
                # There is one appearance (requirement, 2026-10-03): the palette offers no theme action.
                page.keyboard.press("Control+k")
                search.fill("dark theme")
                expect(page.locator(".palette-list")).not_to_contain_text("Switch to the dark theme")
                page.keyboard.press("Escape")

                # The rail's status is a button; its popover says what the worker, mail, backups and disks are doing.
                status = page.locator(".sidebar .server-status")
                expect(status).to_have_attribute("aria-expanded", "false")
                status.click()
                popover = page.locator("#status-pop")
                expect(popover).to_be_visible()
                expect(status).to_have_attribute("aria-expanded", "true")
                expect(popover.get_by_role("heading", name="Workspace status")).to_be_visible()
                expect(popover).to_contain_text("Worker")
                expect(popover).to_contain_text("Last backup")
                expect(popover).to_contain_text("Email gateway")
                expect(popover).to_contain_text("Nothing is running or queued.")
                expect(popover.locator("[data-status-updated]")).to_contain_text("Updated")
                expect(popover.get_by_role("link", name=re.compile("Workspace health"))).to_have_attribute("href", "#overview?operations")
                box = popover.bounding_box()
                assert box["x"] >= 0 and box["y"] >= 0 and box["y"] + box["height"] <= 900, box
                page.screenshot(path=str(output / "status-popover.png"))
                page.keyboard.press("Escape")
                expect(popover).to_be_hidden()

                # Notices come from comparing two overview reads: a scan the worker finished now needs the instructor.
                real = client.get("/api/workspace/overview").json
                stages = [{"key": "scan", "label": "Scan", "title": "Check the scanned pages", "state": "done", "status": "Done", "page": "pages", "detail": "Done.", "urgent": False},
                          {"key": "students", "label": "Students", "title": "Find whose paper each is", "state": "started", "status": "To do", "page": "students", "detail": "Find them.", "urgent": True}]
                reads = []

                def overview(route):
                    state = "wait" if not reads else "todo"
                    reads.append(state)
                    body = {**real, "generated_at": f"read-{len(reads)}",
                            "recent_scans": [{"id": "job-1", "href": "#inbox/job/job-1", "created_at": real["generated_at"], "status": "needs_review",
                                              "scan": {"headline": "Quiz 2 · papers without labels", "state": state, "stages": stages, "steps": [], "facts": []}}]}
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

                page.route("**/api/workspace/overview", overview)
                page.locator('.sidebar [data-view="overview"]').click()
                page.wait_for_function("() => document.querySelector('.home-attention') !== null")
                page.wait_for_timeout(500)
                status.click()
                popover.locator("[data-status-refresh]").click()
                notice = page.locator("#toast .toast-item").filter(has_text="Quiz 2 · papers without labels: ready for you")
                expect(notice).to_be_visible()
                notice.get_by_role("button", name="Students →").click()
                expect(page).to_have_url(re.compile(r"#inbox/job/job-1/students$"))
                page.unroute("**/api/workspace/overview")

                # Last resort for a tab older than every archived build: a lazy import that fails says so and offers a reload.
                assert page.evaluate("""async () => {
                  const entry = document.querySelector('script[type=module][src$="/app.js"]').src;
                  const ui = await import(entry.replace(/app\\.js$/, 'ui.js'));
                  try { await ui.loadModule('./missing-module-for-this-check.js'); return false; } catch { return true; }
                }""")
                updated = page.locator("#app-updated")
                expect(updated).to_be_visible()
                expect(updated).to_contain_text("Faculty was updated since this tab opened")
                expect(updated.get_by_role("button", name="Reload")).to_be_visible()
                errors[:] = [error for error in errors if "missing-module-for-this-check" not in error and "404" not in error]

                # Desktop only (requirement, 2026-10-03): no Menu sheet; the palette fits the narrowest supported window.
                page.goto(address + "/#rooms")
                page.set_viewport_size({"width": 1280, "height": 800})
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                expect(page.locator("[data-menu-toggle]")).to_have_count(0)
                expect(page.locator('.sidebar [data-view="rooms"]')).to_be_in_viewport()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.keyboard.press("Control+k")
                expect(palette).to_be_visible()
                box = palette.bounding_box()
                assert box["x"] >= 232 and box["x"] + box["width"] <= 1280, box
                page.screenshot(path=str(output / "palette-1280.png"))
                page.keyboard.press("Escape")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Palette checks passed: Ctrl K search across pages, courses and sections, keyboard selection, recent places, actions "
          "that open the page's own dialogs, '/' and '?' with typing and dialogs respected, no theme actions, the rail's status "
          "popover, a 'ready for you' notice from two overview reads, and the palette at 1280 px.")


if __name__ == "__main__":
    main()
