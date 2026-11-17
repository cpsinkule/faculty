"""Canvas modules and pages in a real browser (canvas/README.md "Modules and pages"): from the course Home, start Faculty's copy from
section 507's Canvas course, rename a module and edit a page body in the sandboxed editor, save, then push: the preview names what
each course receives and asks about a page edited in 508's Canvas, a pairing by hand is offered for a module named differently there,
and the push reaches both courses with 508's own file links. Synthetic data only; Canvas is the loopback stand-in."""
import io
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from canvas import web
from fake_canvas import FakeCanvas
from rosters import connect
from test_rosters import STUDENTS, csv_bytes
from test_storage import HEADERS


def main():
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        a = fake.add_course("MATH 140 507", "MATH 140 507", "Fall 2026")
        b = fake.add_course("MATH 140 508", "MATH 140 508", "Fall 2026")
        ids = {}
        for course in (a, b):
            banner = fake.add_file(course, "course files/Template", "banner.png")
            office = fake.add_page(course, "Office Hours", "<p>BLOC 309</p>")
            fake.add_page(course, "Home", f'<h2>Welcome</h2><p><img alt="banner" src="/courses/{course}/files/{banner}/preview"></p>', front_page=True)
            fake.add_module(course, "Course Materials", items=[{"type": "SubHeader", "title": "Syllabus"},
                                                               {"type": "Page", "title": "Office Hours", "page_url": office["url"]}])
            week = fake.add_module(course, "Week 1" if course == a else "Week One")
            ids[course] = {"banner": banner, "office": office, "week": week}
        next(p for p in fake.pages[b] if p["title"] == "Office Hours")["body"] = "<p>Section 508's own hours</p>"
        alice = fake.add_student(a, fake.add_section(a, "507"), "Example, Alice", STUDENTS[0][2])
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        settings = web.Settings(session_file=os.path.join(directory, "config", "canvas-session.json"), base=fake.url)
        app.extensions["canvas"].settings = settings
        web.save(settings, fake.session(), user={"name": "Pat Instructor"})
        seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]
        for number, chosen in ((507, STUDENTS[:1]), (508, STUDENTS[1:])):
            answer = client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes(chosen)), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        sections = {s["number"]: s["id"] for s in client.get("/api/gradebook").json["terms"][0]["sections"]}
        for number, course in ((507, a), (508, b)):
            assert client.post(f"/api/canvas/sections/{sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS).status_code == 200
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                for width in (1440, 1280):
                    page = browser.new_page(viewport={"width": width, "height": 1000})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("dialog", lambda d: d.accept())
                    if width == 1280:  # a laptop's window (desktop only): nothing scrolls sideways
                        page.goto(f"{address}/#course/{group['id']}/home/canvas")
                        expect(page.locator("[data-cc-savebar]")).to_be_visible(timeout=20000)
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways at 1280 px"
                        assert not errors, errors
                        page.close()
                        continue
                    # The sidebar's Canvas page says what to do first and leads to the course's Canvas content.
                    page.goto(f"{address}/#overview")
                    page.locator(".sidebar nav").get_by_role("link", name="Canvas", exact=True).click()
                    hub = page.locator("#canvas-section")
                    expect(hub.locator(".next-step")).to_contain_text("Start Faculty’s copy", timeout=20000)
                    expect(hub.locator(".canvas-hub-steps li").first).to_contain_text("2 of 2 sections linked")
                    page.goto(f"{address}/#course/{group['id']}/home?from=current-courses")
                    expect(page.locator("[data-canvas-home-status]")).to_have_text("Not started", timeout=20000)
                    page.get_by_role("link", name="Canvas modules, pages and assignments of every section").click()
                    expect(page.locator('.sidebar [data-view="canvas"]')).to_have_attribute("aria-current", "page")
                    host = page.locator("[data-canvas-content-host]")
                    expect(host.get_by_role("button", name="Start from this course")).to_be_visible(timeout=20000)
                    host.get_by_role("button", name="Start from this course").click()
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("revision 1", timeout=30000)
                    assert fake.writes == []
                    # Rename a module.
                    host.locator("summary", has_text="Course Materials").click()
                    host.locator('[data-cc-module-name="0"]').fill("Course Materials (read first)")
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("Unsaved changes")
                    # The bar stays in view while the page scrolls, with the page's one next step (Save, now).
                    assert host.locator("[data-cc-savebar]").evaluate("bar => getComputedStyle(bar).position") == "sticky"
                    # On the Canvas pages the course's tabs say whose Canvas course is open, and stay in Canvas.
                    tabs = page.get_by_role("tablist", name="Course sections", exact=True)
                    expect(tabs.get_by_role("tab")).to_have_text(["All sections", "Section 507", "Section 508"])
                    expect(tabs.get_by_role("tab", name="Section 508")).to_have_attribute("href", f"#course/{group['id']}/section/508/canvas")
                    # ONE CLICK, ONE CHANGE (2026-10-11): the module editor's handlers were bound again at every draw, so after a
                    # few draws Add item added several items and Move down undid itself. Draw it a few times, then add one text
                    # header, move it up once and take it out again through its menu.
                    for _ in range(2):
                        host.get_by_role("tab", name="Pages").click()
                        host.get_by_role("tab", name="Modules").click()
                    module = host.locator('[data-cc-module="0"]')
                    expect(module.locator(".cc-item")).to_have_count(2)
                    module.locator('[data-cc-add-item="0"]').click()
                    expect(module.locator(".cc-item")).to_have_count(3)
                    expect(module.locator("[data-cc-item-title]").last).to_have_value("New header")
                    assert module.locator(".cc-item").last.bounding_box()["height"] < 60, "an item is one line"
                    module.locator('[data-cc-item-move="0:2:-1"]').click()
                    expect(module.locator("[data-cc-item-title]").nth(1)).to_have_value("New header")
                    expect(module.locator('[data-cc-item-move="0:1:-1"]')).to_be_focused()  # the focus went with the item
                    module.locator(".cc-item").nth(1).get_by_role("button", name="More for New header").click()
                    page.get_by_role("menuitem", name="Remove from this module").click()
                    expect(module.locator(".cc-item")).to_have_count(2)
                    assert [i.input_value() for i in module.locator("[data-cc-item-title]").all()] == ["Syllabus", "Office Hours"]
                    # Edit the Home page's body in the sandboxed editor.
                    host.get_by_role("tab", name="Pages").click()
                    host.get_by_role("button", name="Home").click()
                    frame = page.frame_locator("[data-cc-frame]")
                    expect(frame.locator("h2")).to_have_text("Welcome", timeout=20000)
                    frame.locator("h2").click()
                    page.keyboard.press("End")
                    page.keyboard.type(" to MATH 140")
                    host.get_by_role("button", name="Save", exact=True).click()
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("revision 2", timeout=20000)
                    content = client.get(f"/api/canvas/content/{client.get('/api/gradebook').json['terms'][0]['id']}").json["content"]
                    home = next(p for p in content["pages"] if p["title"] == "Home")
                    assert "Welcome to MATH 140" in home["body"], home["body"]
                    assert f"/courses/{a}/files/{ids[a]['banner']}/preview" in home["body"]  # the image kept as it was
                    # Push: the preview lists each course.
                    host.get_by_role("button", name="Push to Canvas…").click()
                    dialog = page.locator("#dialog")
                    expect(dialog.get_by_role("heading", name="Push to Canvas")).to_be_visible()
                    # The first push starts with one section ticked; tick the other to push to both here.
                    expect(dialog.locator(".cc-targets")).to_contain_text("Push to one section first", timeout=30000)
                    expect(dialog.locator(".cc-course")).to_have_count(1, timeout=30000)
                    dialog.locator("[data-cc-target]").nth(1).check()
                    expect(dialog.locator(".cc-course")).to_have_count(2, timeout=30000)
                    course_a = dialog.locator(".cc-course").nth(0)
                    course_b = dialog.locator(".cc-course").nth(1)
                    expect(course_b).to_contain_text("Needs your decision", timeout=30000)
                    expect(course_b).to_contain_text("Office Hours")
                    expect(course_b).to_contain_text("Differs from Faculty’s copy")
                    course_b.get_by_text('Compare changes', exact=True).first.click()
                    expect(course_b.locator('.cc-diff-text').first).to_be_visible()
                    expect(course_b.locator('[data-conflict-value]').first).to_have_value("<p>Section 508's own hours</p>")
                    # CANVAS "Conflict review": text typed under Compare changes survives the preview reload the pairing below makes.
                    office_row = course_b.locator("[data-conflict-key]", has_text="Office Hours")
                    office_row.locator("[data-conflict-value]").first.fill("<p>Typed before the reload</p>")
                    expect(course_a).to_contain_text("1 page updated · 1 module updated")
                    # 508 calls week 1 "Week One": pair it by hand instead of making a new module.
                    expect(course_b).to_contain_text("New in this course")
                    course_b.get_by_label("Same as in Canvas").select_option(label="Week One")
                    expect(course_b).not_to_contain_text("New in this course", timeout=30000)
                    expect(office_row.locator("[data-conflict-value]").first).to_be_visible()  # its comparison opened again
                    expect(office_row.locator("[data-conflict-value]").first).to_have_value("<p>Typed before the reload</p>")
                    # Choosing it keeps the window open, leaves the row as is in Canvas and puts "Save and preview again" in place of
                    # Push (a stale preview is never pushed) until it is set aside.
                    office_row.get_by_role("button", name="Use this text in the shared term draft").click()
                    expect(office_row.locator("[data-conflict-resolved]")).to_have_text("Resolved in your draft · left as is in Canvas for this push")
                    expect(office_row.locator("[data-cc-replace]")).to_be_disabled()
                    expect(dialog.locator("[data-cc-push]")).to_have_count(0)
                    expect(dialog.locator("[data-cc-resolve-add]")).to_have_text("Add 1 text to the draft")
                    expect(dialog.locator("[data-cc-resolve-preview]")).to_have_text("Save and preview again")
                    office_row.get_by_role("button", name="Don’t use this text").click()
                    expect(office_row.locator("[data-conflict-resolved]")).to_have_count(0)
                    expect(office_row.locator("[data-cc-replace]")).to_be_enabled()
                    expect(dialog.locator("[data-cc-push]")).to_be_visible()
                    expect(course_b.locator("[data-cc-replace]")).to_have_count(2)  # Office Hours, and Week 1's name
                    dialog.locator("[data-cc-push]").click()
                    expect(dialog).to_be_hidden(timeout=30000)
                    expect(host.locator("[data-cc-push-note]")).to_contain_text("Last push", timeout=30000)
                    # A push that went through is one quiet line of the bar, with each course to check it in.
                    expect(host.locator("[data-cc-savebar] [data-cc-push-note]")).to_contain_text("Check it in Canvas")
                    expect(host.locator("[data-cc-savebar]").get_by_role("link", name="Section 507 ↗")).to_have_attribute("href", f"{fake.url}/courses/{a}/modules")
                    for course in (a, b):
                        page_home = next(p for p in fake.pages[course] if p["title"] == "Home")
                        assert "Welcome to MATH 140" in page_home["body"], page_home["body"]
                        assert f"/courses/{course}/files/{ids[course]['banner']}/preview" in page_home["body"], page_home["body"]
                        assert fake.modules[course][0]["name"] == "Course Materials (read first)"
                    assert next(p for p in fake.pages[b] if p["title"] == "Office Hours")["body"] == "<p>Section 508's own hours</p>"  # not ticked
                    assert [m["name"] for m in fake.modules[b]] == ["Course Materials (read first)", "Week One"]  # paired, not ticked: kept
                    # Every step done: the Canvas page leads with the way into the content and folds its finished setup steps.
                    done = browser.new_page(viewport={"width": 1440, "height": 900})
                    done.goto(f"{address}/#canvas")
                    finished = done.locator("#canvas-section .canvas-hub-setup")
                    expect(finished.locator("summary")).to_contain_text("4 of 4 steps done", timeout=20000)
                    assert finished.evaluate("fold => fold.open") is False
                    expect(done.locator("#canvas-section").get_by_role("link", name="Modules, pages and assignments")).to_have_attribute("href", f"#course/{group['id']}/home/canvas")
                    done.close()
                    # A gradebook column becomes a Canvas assignment of the copy, is pushed, and Sync with Canvas sends its grades there.
                    term = client.get("/api/gradebook").json["terms"][0]["id"]
                    saved = client.post(f"/api/gradebook/terms/{term}/setup", json={"base_revision": None, "request_id": "b-setup-quiz",
                                        "setup": {"categories": [{"ref": "q", "name": "Quizzes"}], "items": [{"title": "Quiz 3", "points": "10", "category_id": "q"}]}}, headers=HEADERS).json
                    quiz = saved["setup"]["items"][0]["id"]
                    row = next(st["row"] for st in saved["students"] if st["name"] == "Example, Alice")
                    assert client.post(f"/api/gradebook/terms/{term}/scores", json={"request_id": "b-quiz-score", "changes": [
                        {"item_id": quiz, "row": row, "base": None, "status": "scored", "points": "8"}]}, headers=HEADERS).status_code == 200
                    # COLUMN ASSIGNMENTS (2026-10-06): the column's assignment joined Faculty's copy as the column was made (published,
                    # no submission, in its category's group); a page opened before that is reloaded to see it.
                    page.reload()
                    host.get_by_role("tab", name="Assignments").click()
                    quizzes = host.locator('.cc-assignment-list .cc-group[data-cc-group="Quizzes"]')
                    expect(quizzes.locator("[data-cc-open-assignment]")).to_contain_text("Quiz 3")
                    expect(quizzes.locator("[data-cc-open-assignment]")).to_contain_text("Gradebook")
                    host.get_by_role("button", name="From the gradebook…").click()
                    expect(dialog).to_contain_text("Every gradebook column already has an assignment in Faculty’s copy.")
                    dialog.get_by_role("button", name="Cancel").click()
                    quizzes.locator("[data-cc-open-assignment]").click()
                    expect(host.get_by_label("Published")).to_be_checked()
                    host.get_by_label("Due", exact=True).fill("2026-10-09T23:59")
                    # The Assignment Group is required: cleared, Save opens the assignment and names it (nothing saved).
                    host.get_by_label("Assignment Group").fill("")
                    host.get_by_role("button", name="Save", exact=True).click()
                    expect(page.get_by_text("Choose the Assignment Group of “Quiz 3”")).to_be_visible(timeout=10000)
                    # An error toast is danger-toned and stays until dismissed (never a green four-second success toast).
                    group_toast = page.locator("#toast .toast-item", has_text="Choose the Assignment Group of “Quiz 3”")
                    expect(group_toast).to_have_attribute("data-tone", "danger")
                    group_toast.get_by_role("button", name="Dismiss").click()
                    expect(group_toast).to_have_count(0)
                    expect(host.locator(".cc-page-list")).to_contain_text("Choose its Assignment Group")
                    # BY ASSIGNMENT GROUP (2026-10-06): the list is one fold per group, those without a group first.
                    first_fold = host.locator(".cc-assignment-list .cc-group").first
                    expect(first_fold.locator("summary")).to_contain_text("No Assignment Group")
                    expect(first_fold).to_contain_text("Quiz 3")
                    host.get_by_label("Assignment Group").fill("Quizzes")
                    host.get_by_role("button", name="Save", exact=True).click()
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("revision 4", timeout=20000)  # 3: the column's assignment
                    expect(quizzes.locator("summary")).to_contain_text("1 assignment")
                    expect(quizzes.locator("[data-cc-open-assignment]")).to_contain_text("Quiz 3")
                    expect(host.locator('.cc-assignment-list .cc-group[data-cc-group=""]')).to_have_count(0)
                    # The list scrolls in its own box beside the editor, and its find box leaves the rows that match.
                    assert host.locator(".cc-assignment-list .cc-list-scroll").evaluate("box => getComputedStyle(box).overflowY") == "auto"
                    expect(host.locator(".cc-page-editor")).to_be_in_viewport()
                    host.get_by_label("Find an assignment").fill("no such words")
                    expect(host.locator(".cc-assignment-list .cc-list-scroll")).to_have_text("No assignment has those words.")
                    host.get_by_label("Find an assignment").fill("quiz")
                    expect(host.locator(".cc-assignment-list [data-cc-open-assignment]")).to_have_text([re.compile("Quiz 3")])
                    host.get_by_label("Find an assignment").fill("")
                    expect(quizzes.locator("summary")).to_contain_text("1 assignment")
                    page.screenshot(path="test-results/canvas-assignments-by-group.png")
                    host.get_by_role("button", name="Push to Canvas…").click()
                    expect(dialog.locator(".cc-course").nth(0)).to_contain_text("1 Assignment Group made · 1 assignment made", timeout=30000)
                    dialog.locator("[data-cc-push]").click()
                    for _ in range(300):
                        if any(x["name"] == "Quiz 3" for g in fake.groups.get(a, []) for x in g["assignments"]) and \
                                client.get(f"/api/canvas/content/{term}").json["push"]["status"] != "running":
                            break
                        time.sleep(0.1)
                    made = next(x for g in fake.groups[a] for x in g["assignments"] if x["name"] == "Quiz 3")
                    assert made["published"] and made["points_possible"] == 10.0, made
                    page.goto(f"{address}/#course/{group['id']}/section/507?from=current-courses")
                    heading = page.locator(".page-heading")
                    heading.get_by_role("button", name="Start from Canvas", exact=True).click()
                    expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                    expect(dialog.locator(".gbd-summary")).to_contain_text("Sends 1 grade to Canvas", timeout=30000)
                    expect(dialog).to_contain_text("Canvas posts it automatically")
                    # POSTING GRADES (2026-10-08): its category posts by hand, so Sync makes the assignment post by hand first.
                    expect(dialog.locator(".gbd-summary")).to_contain_text("Sets the Canvas assignments of 1 column to post by hand first")
                    dialog.get_by_role("button", name="Sync · 1 grade to Canvas").click()
                    expect(dialog).to_contain_text("Sent. 1 assignment set to post by hand; 1 grade now shows in Canvas", timeout=30000)
                    assert fake.submissions[a][(alice, made["id"])]["score"] == 8.0
                    assert made["post_manually"] is True, made
                    # "Put on Canvas…" in an assignment's Create area (ASSIGNMENT-WORKSPACE: Create retains the Canvas placement control,
                    # beside where it stands): into the copy in a new module, then the ordinary push.
                    with connect(app.config["DATABASE"]) as db:
                        definition_id = db.execute("SELECT definition_id FROM course_terms WHERE id=?", (term,)).fetchone()[0]
                    made_quiz4 = client.post("/api/documents/assignments", json={"definition_id": definition_id, "key": "quiz-4", "title": "Quiz 4"},
                                             headers=HEADERS)
                    assert made_quiz4.status_code in (200, 201), made_quiz4.json
                    create_area = f"#assignments/{made_quiz4.json['id']}/{term}?area=create"

                    def open_create_area():
                        # Assignments lists it; the assignment's Create area holds its paper, printing and tools.
                        page.goto(f"{address}/#assignments")
                        page.locator("[data-assignment-list]").get_by_role("link", name="Quiz 4", exact=True).click()
                        page.locator("[data-assignment-nav]").get_by_role("link", name="Create", exact=True).click()
                        expect(page).to_have_url(re.compile(re.escape(create_area) + "$"))
                        area = page.locator("#assignment-workspace [data-assignment-body]")
                        expect(area.get_by_role("heading", name="Paper and printing")).to_be_visible(timeout=20000)
                        area.locator("summary", has_text="Assignment tools").click()
                        return area

                    area = open_create_area()
                    expect(area.locator("[data-workspace-canvas-fact]")).to_have_count(0)  # not in the copy yet
                    area.get_by_role("button", name="Put MATH 140 · Quiz 4 on Canvas").click()
                    expect(dialog.get_by_role("heading", name="Put on Canvas")).to_be_visible(timeout=20000)
                    dialog.get_by_label("Points").fill("15")
                    dialog.locator("select[name=\"module\"]").select_option(label="New module…")
                    dialog.get_by_label("New module’s name").fill("Quizzes")
                    # No column, so no group yet: Save asks for one (Canvas would use the course's first group).
                    dialog.get_by_role("button", name="Save to Faculty’s copy").click()
                    expect(dialog.locator(".form-error")).to_contain_text("Choose its Assignment Group")
                    dialog.locator("select[name=\"group\"]").select_option(label="New Assignment Group…")
                    dialog.get_by_label("New Assignment Group’s name").fill("Quiz Category")
                    dialog.get_by_role("button", name="Save to Faculty’s copy").click()
                    expect(dialog).to_contain_text("“Quiz 4” is in Faculty’s copy", timeout=20000)
                    assert not any(x["name"] == "Quiz 4" for g in fake.groups.get(a, []) for x in g["assignments"])  # nothing in Canvas yet
                    dialog.get_by_role("button", name="Push to Canvas…").click()
                    expect(dialog.locator(".cc-course")).to_have_count(2, timeout=30000)  # both courses were pushed before: both ticked
                    dialog.locator("[data-cc-push]").click()
                    for _ in range(300):
                        if all(any(x["name"] == "Quiz 4" for g in fake.groups.get(c, []) for x in g["assignments"]) for c in (a, b)) and \
                                client.get(f"/api/canvas/content/{term}").json["push"]["status"] != "running":
                            break
                        time.sleep(0.1)
                    for course in (a, b):
                        quiz4 = next(x for g in fake.groups[course] for x in g["assignments"] if x["name"] == "Quiz 4")
                        assert quiz4["points_possible"] == 15.0, quiz4
                        assert next(g["name"] for g in fake.groups[course] if quiz4 in g["assignments"]) == "Quiz Category"
                        assert fake.modules[course][-1]["name"] == "Quizzes" and fake.modules[course][-1]["items"][0]["content_id"] == quiz4["id"]
                    area = open_create_area()
                    expect(area.locator("[data-workspace-canvas-fact]")).to_have_text("On Canvas", timeout=20000)
                    expect(area.get_by_role("button", name="Update MATH 140 · Quiz 4 on Canvas")).to_have_text("Update on Canvas…")
                    # One section's own Canvas course, edited directly: only 508 changes, and the term's copy is not touched.
                    revision = client.get(f"/api/canvas/content/{term}").json["revision"]
                    page.goto(f"{address}/#course/{group['id']}/section/508/canvas")
                    # The title is printed on the page's plate (DESIGN-LANGUAGE §5), no longer inside .page-heading.
                    expect(page.locator("#page-title")).to_have_text("Canvas · Section 508", timeout=20000)
                    host = page.locator("[data-canvas-content-host]")
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("As in Canvas", timeout=30000)
                    expect(host.locator("[data-cc-managed]")).to_be_visible()  # the term's copy manages what it pushed here
                    expect(page.locator('.sidebar [data-view="canvas"]')).to_have_attribute("aria-current", "page")
                    host.locator("summary", has_text="Week One").click()
                    host.locator('[data-cc-module-name="1"]').fill("Week One (508 only)")
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("Changes not pushed yet")
                    host.get_by_role("button", name="Review and push…").click()
                    expect(dialog.get_by_role("heading", name="Push to Section 508’s Canvas course")).to_be_visible()
                    expect(dialog.locator(".cc-course")).to_contain_text("1 module updated", timeout=30000)
                    expect(dialog.locator("[data-cc-target]")).to_have_count(0)  # one course: nothing to choose
                    dialog.locator("[data-cc-push]").click()
                    expect(host.locator("[data-cc-push-note]")).to_contain_text("Last push", timeout=30000)
                    expect(host.locator("[data-cc-savebar]")).to_contain_text("As in Canvas", timeout=30000)
                    assert [m["name"] for m in fake.modules[b]][1] == "Week One (508 only)", [m["name"] for m in fake.modules[b]]
                    assert [m["name"] for m in fake.modules[a]][1] != "Week One (508 only)"
                    assert client.get(f"/api/canvas/content/{term}").json["revision"] == revision  # the copy is unchanged
                    # CANVAS: a remote conflict can be resolved in the section draft without writing or changing its parent.
                    office = next(p for p in fake.pages[b] if p['title'] == 'Office Hours')
                    office['body'] = '<p>Remote edit after opening</p>'
                    home_b = next(p for p in fake.pages[b] if p['title'] == 'Home')
                    home_b['body'] = '<p>Remote welcome</p>'
                    host.locator('[data-cc-module-name="1"]').evaluate('(input) => { input.closest("details").open = true; }')
                    host.locator('[data-cc-module-name="1"]').fill('Week One (508 revised)')
                    host.get_by_role('button', name='Review and push…').click()
                    expect(dialog.get_by_text('Compare changes', exact=True)).to_have_count(2, timeout=30000)
                    # Several conflicts are resolved without closing the window; Push waits for a fresh preview.
                    for title, text in (('Office Hours', '<p>Reviewed resolution</p>'), ('Home', '<p>Reviewed welcome</p>')):
                        row = dialog.locator('[data-conflict-key]', has_text=title)
                        row.get_by_text('Compare changes', exact=True).click()
                        row.locator('[data-conflict-value]').first.fill(text)
                        row.get_by_role('button', name='Use this text in this section’s draft').first.click()
                        expect(row.locator('[data-conflict-resolved]')).to_be_visible()
                        expect(row.locator('[data-cc-replace]')).to_be_disabled()
                    expect(dialog).to_be_visible()
                    expect(dialog.locator('[data-cc-push]')).to_have_count(0)
                    expect(dialog.locator('[data-cc-resolve-add]')).to_have_text('Add 2 texts to the draft')
                    dialog.get_by_role('button', name='Preview again', exact=True).click()
                    expect(dialog.locator('[data-cc-push]')).to_be_visible(timeout=30000)
                    expect(dialog.locator('[data-conflict-resolved]')).to_have_count(0)
                    expect(host.locator('[data-cc-savebar]')).to_contain_text('Changes not pushed yet')
                    assert office['body'] == '<p>Remote edit after opening</p>' and home_b['body'] == '<p>Remote welcome</p>'
                    # Canvas changes again before Push: refused (409) with a danger toast that stays, and nothing is written.
                    office['body'] = '<p>Another remote edit</p>'
                    dialog.locator('[data-cc-push]').click()
                    refused = page.locator('#toast .toast-item[data-tone="danger"]', has_text='Canvas changed since the preview')
                    expect(refused).to_be_visible(timeout=30000)
                    page.wait_for_timeout(4500)
                    expect(refused).to_be_visible()
                    expect(refused.get_by_role('button', name='Dismiss')).to_have_count(1)  # it stays until dismissed
                    assert office['body'] == '<p>Another remote edit</p>' and home_b['body'] == '<p>Remote welcome</p>'
                    assert [m['name'] for m in fake.modules[b]][1] == 'Week One (508 only)'
                    # "Add 1 text to the draft" puts it in the draft and closes the window, with one toast.
                    row = dialog.locator('[data-conflict-key]', has_text='Office Hours')
                    expect(row).to_be_visible(timeout=30000)
                    row.get_by_text('Compare changes', exact=True).click()
                    row.locator('[data-conflict-value]').first.fill('<p>Final resolution</p>')
                    row.get_by_role('button', name='Use this text in this section’s draft').first.click()
                    dialog.get_by_role('button', name='Add 1 text to the draft').click()
                    expect(dialog).to_be_hidden()
                    expect(page.locator('#toast')).to_contain_text('1 text added to your draft. Review and push when ready.')
                    expect(host.locator('[data-cc-savebar]')).to_contain_text('Changes not pushed yet')
                    assert office['body'] == '<p>Another remote edit</p>'
                    assert client.get(f'/api/canvas/content/{term}').json['revision'] == revision
                    assert not errors, errors
                    page.close()
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Canvas modules and pages browser check passed")


if __name__ == "__main__":
    main()
