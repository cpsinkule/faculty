"""The shell holds on every screen (DESIGN-LANGUAGE §1.12, §5).

Visits every route the design session reviewed (40; and a section's class schedule and an exam's page of rooms, added since) and opens every dialog it reviewed (12), at 1440 × 900 and
1440 × 1100, in an isolated workspace seeded like browser_seating, and checks on each (tests/shell_checks.py): the black
rail fixed from top to bottom with nothing over its edge, no arc or reprint of the menu, the footer never covered, and the
white header band from the rail's edge to the window's under its 2 px ink rule, no taller than 90 px and with no photograph.
On every route (its folds open) and in every dialog, no unframed clickable is drawn in small capitals (SENTENCE-CASE
ACTIONS, tests/shell_checks.py). Run with TMPDIR on NVMe.
"""
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_seating import seed
from test_seating import HEADERS
from shell_checks import STATE, check_sentence_case, check_shell

SIZES = ((1440, 900), (1440, 1100))


def poll(page, script, label, timeout=8000):
    """Evaluate `script` until it is truthy (the app's CSP refuses wait_for_function's string predicates)."""
    for _ in range(timeout // 100):
        if page.evaluate(script):
            return
        page.wait_for_timeout(100)
    raise AssertionError(label)


def settle(page, label):
    """The route has painted: connected, fonts in, and the header band's title set."""
    expect(page.locator("body > .sidebar .server-status")).to_have_class(re.compile(r"\bconnected\b"))
    page.evaluate("document.fonts.ready")
    poll(page, "() => { const title = document.querySelector('#page-title'); return !!title && title.textContent.trim().length > 0; }", (label, "title"))
    page.wait_for_timeout(100)


def routes_for(ids):
    group, term, assignment = ids["group"], ids["term"], ids["assignment"]
    return [
        "assignments", f"assignments/{assignment}/{term}",
        "absences", "accommodations", "accommodations?tab=exams", "all-courses", "calendar", "canvas",
        f"course/{group}/home", f"course/{group}/home/gradebook", f"course/{group}/home/notes", f"course/{group}/home/questions",
        f"course/{group}/section/507", f"course/{group}/section/507/changes", f"course/{group}/section/507/roster",
        f"course/{group}/section/507/schedule",
        f"course/{group}/section/507/setup", f"course/{group}/section/507?assignment={assignment}",
        "current-courses", "documents", f"exam-day/{assignment}/{term}", "exams", f"exams/assessment/{assignment}/{term}",
        "follow-ups", "grade-delivery", f"gradebook/{term}?assignment={assignment}", "grading", "inbox",
        f"inbox/job/{ids['job']}", "mail", "memory", "office-hours", "overview", "pinned", "recordings", "response-cards", "roadmap", "rooms",
        f"rooms/{ids['room']}", "seating", f"seating/{ids['chart']}", f"seating/exam/{assignment}/{term}", "teaching-review", "tools",
    ]


# (route, the control that opens it, words in the dialog's title; the design session's review named them by the control)
DIALOGS = [
    ("current-courses", "Add a course", "Add a course"),
    ("rooms", "Add a room", "Add a room"),
    ("tools", "Add a tool", "new tool"),
    ("calendar", "Add event", "Add to your calendar"),
    # Since ASSIGNMENT-WORKSPACE (4507e55) creating and drafting start on Assignments (#documents leads there).
    ("assignments", "Create assignment", "assignment"),
    # Since ac655a9 the quiz draft starts from Create assignment's menu (ASSIGNMENT-WORKSPACE "Workflow clarity").
    ("assignments", "menu:Create assignment/Draft a quiz with a model", "quiz"),
    ("seating", "New seating chart", "New seating chart"),
    ("absences", "Record an absence", "Record an absence"),
    ("overview", "Ctrl+K", "Go to"),
    ("overview", "Upload scanned assignment", "Upload scanned assignment"),
    ("overview", "Workspace settings", "Workspace settings"),
    # #exams leads to Assignments since ASSIGNMENT-WORKSPACE; New exam is the palette's command.
    ("assignments", "palette:New exam", "Create an exam"),
]


def click_control(page, opener):
    control = page.locator(f"#primary-action:has-text('{opener}'), main button:has-text('{opener}'), main a:has-text('{opener}'),"
                           f" .page-heading button:has-text('{opener}'), .page-heading a:has-text('{opener}')")
    control.locator("visible=true").first.click()


def open_dialog(page, opener):
    if opener.startswith("menu:"):
        # "menu:<control>/<choice>": the control opens a menu dialog, and its choice replaces it with the dialog under test.
        control, choice = opener.split(":", 1)[1].split("/", 1)
        click_control(page, control)
        poll(page, "() => !!document.querySelector('dialog[open]')", (opener, "menu open"))
        button = page.locator("dialog[open]").last.get_by_role("button", name=choice, exact=True)
        button.evaluate("button => { button.dataset.shellMenuChoice = ''; }")
        button.click()
        # The dialog the choice opened, not the menu: no open dialog still holds the choice.
        poll(page, "() => { const open = [...document.querySelectorAll('dialog[open]')];"
                   " return open.length > 0 && !open.some(d => d.querySelector('[data-shell-menu-choice]')); }", (opener, "dialog open"))
        return
    if opener == "Ctrl+K":
        page.keyboard.press("Control+k")
    elif opener.startswith("palette:"):
        page.keyboard.press("Control+k")
        poll(page, "() => !!document.querySelector('dialog[open]')", (opener, "palette open"))
        page.keyboard.type(opener.split(":", 1)[1])
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        poll(page, "() => [...document.querySelectorAll('dialog[open]')].some(d => !d.querySelector('.palette-list'))", (opener, "dialog open"))
        return
    elif opener == "Workspace settings":
        page.locator('body > .sidebar [data-action="settings"]').click()
    else:
        click_control(page, opener)
    poll(page, "() => !!document.querySelector('dialog[open]')", (opener, "dialog open"))


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = seed(app)
        state = client.get("/api/state").json
        catalog = client.get("/api/seating/catalog").json
        room = client.get("/api/rooms").json[0]
        created = client.post("/api/seating", json={
            "assignment_id": catalog["assignments"][0]["id"], "section_ids": [s["id"] for s in catalog["sections"]],
            "room_id": room["id"], "exam_version_count": 2}, headers=HEADERS)
        assert created.status_code == 201, created.json
        jobs = client.get("/api/inbox/jobs").json["jobs"]
        assert jobs, "the seed has an Inbox job"
        ids = {"group": state["course_groups"][0]["id"], "term": catalog["sections"][0]["term_id"],
               "assignment": catalog["assignments"][0]["id"], "room": room["id"], "chart": created.json["id"],
               "job": jobs[0]["id"]}
        routes = routes_for(ids)
        assert len(routes) == 44 and len(DIALOGS) == 12
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for width, height in SIZES:
                    context = browser.new_context(reduced_motion="reduce", viewport={"width": width, "height": height})
                    page = context.new_page()
                    page.set_default_timeout(15000)
                    errors = []
                    wrong = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    for route in routes:
                        label = f"#{route} @{width}x{height}"
                        page.goto(f"{address}/#{route}")
                        settle(page, label)
                        check_shell(page, label)
                        if height == SIZES[0][1]:
                            # Every fold open, so what it holds is checked too (SENTENCE-CASE ACTIONS).
                            page.evaluate("() => document.querySelectorAll('main details:not([open])').forEach(d => d.open = true)")
                            check_sentence_case(page, label)
                    for route, opener, name in DIALOGS:
                        label = f"{name} on #{route} @{width}x{height}"
                        page.goto(f"{address}/#{route}")
                        settle(page, label)
                        before = page.evaluate(STATE)
                        open_dialog(page, opener)
                        dialog = page.locator("dialog[open]").last
                        expect(dialog).to_be_visible()
                        title = (dialog.get_attribute("aria-label") or dialog.inner_text()).strip()
                        if name.lower() not in title.lower():
                            wrong.append((label, title.splitlines()[0] if title else ""))
                        after = check_shell(page, label, dialog=True)
                        check_sentence_case(page, label)
                        # Behind the dialog nothing in the shell moved or changed.
                        for key in ("rail", "plate"):
                            keep = {k: v for k, v in before[key].items() if k != "edgeClear"} if key == "rail" else before[key]
                            now = {k: v for k, v in after[key].items() if k != "edgeClear"} if key == "rail" else after[key]
                            assert keep == now, (label, key, keep, now)
                        # Closing is not under test here: Escape, and a form that holds on to its draft is closed outright.
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                        page.evaluate("() => document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close())")
                        poll(page, "() => !document.querySelector('dialog[open]')", (label, "dialog closed"))
                    assert not wrong, ("dialogs opened something else", wrong)
                    assert not errors, (width, height, errors)
                    context.close()
                browser.close()
        finally:
            server.close()
    print(f"Shell checks passed: {len(routes)} routes and {len(DIALOGS)} dialogs at 1440 × 900 and 1440 × 1100 — the black "
          "rail with nothing over its edge, no arc, the footer in view, the white header band from the rail's edge (2 px rule, "
          "at most 90 px, no photograph), and no clickable text in small capitals.")


if __name__ == "__main__":
    main()
