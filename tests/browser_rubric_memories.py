"""Rubric memories in a real browser (GRADING "RUBRIC MEMORIES"): the course Home's Rubric memories line says how many wait; the page
draws what waits (a revision with what it was), what is kept and what was dropped; Keep, Drop and Keep all change the page in place
and the server's view; Drop takes a kept memory back and its Keep under Dropped brings it back; Learn from certified assignments is
disabled with its reason until a Rubric drafting and review model is chosen, then lists what it will read, queues the pass and the
page follows it quietly to its end; a course with nothing shows the empty state; the page fits a 1280 px window. The memories and the
certified plan are seeded straight into the synthetic catalog; the model is a loopback host that this pass never needs to ask."""
import logging
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from fake_openai import FakeHost
from grading.models import encoded
from rosters import connect
from test_question_bank import configure_model
from test_storage import HEADERS

RULE = "A value wrong only because of a mistake already charged on an earlier line is not charged again on a later line."
NARROWER = "A value wrong only because of a mistake already charged on an earlier line of the same part is not charged again."
WATCH = "Where a line requires a label, say which near-labels count, such as the name without its equals sign."
DROPPED = "Every sign error costs one point wherever it appears."
NEW_RULE = "A final answer left unsimplified earns the line when the rubric never asks for a simplified form."
NEW_WATCH = "Where a line pays for units, say whether units written only once at the end count."
LATER_RULE = "An answer boxed twice is read by the box that agrees with the work."


def certified_plan(client, frq, definition, title):
    """A grading plan of the course's term set up through Set up grading now, with one certification row (nothing graded): what
    `certified_plans` lists. Returns the plan's ID."""
    with connect(frq.path) as db:
        term = db.execute("SELECT id FROM course_terms WHERE definition_id=?", (definition,)).fetchone()[0]
    documents = client.application.extensions["documents"]
    assignment = documents.create_assignment(definition, "quiz-4", title)["id"]
    made = client.post("/api/grading/assignments", json={"assignment_id": assignment, "term_id": term}, headers=HEADERS)
    assert made.status_code in (200, 201), made.json
    plan = made.json["id"]
    with connect(frq.path) as db:
        db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
            participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
            VALUES(?,?,?,?,?,'browser-check','[]',?,?,?,?,'{}','[]','[]','{}','{}',NULL,'workspace','2026-10-09T15:00:00+00:00')""",
                   (str(uuid.uuid4()), plan, str(uuid.uuid4()), "0" * 64, "0" * 64, "0" * 64, "0" * 64, "0" * 64, "0" * 64))
    return plan


def no_model(frq):
    """No Rubric drafting and review model (the synthetic workspace's settings name one by default)."""
    current = frq.settings()
    frq.save_settings({"version": current["version"], "settings": {**current["settings"], "rubric_reviewer": "",
                                                                    "tiers": {"cloud_standard": {"reader": "", "primary": "", "second": "", "verifier": ""}}}})


def seed_memories(frq, definition, plan):
    """M1 and M2 kept, M3 dropped; waiting: M1 narrowed, and M4, M5, M6 new. Returns {name: memory id}."""
    ids = {}

    def memory(name, number, kind, rule, at, action="new", replaces=None, covers="Any later line a student's earlier, already charged mistake carries into.",
               not_when="", why="", sources=("R1", "R2", "D1", "D2", "D3")):
        ids[name] = str(uuid.uuid4())
        with connect(frq.path) as db:
            db.execute("""INSERT INTO frq_rubric_memories(id,definition_id,number,kind,action,replaces_id,rule,covers,not_when,why,sources_json,run_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?)""", (ids[name], definition, number, kind, action, replaces, rule, covers, not_when, why,
                                                         encoded({"assignment_id": plan, "ids": list(sources)}), at))

    def decide(name, decision, at):
        with connect(frq.path) as db:
            db.execute("INSERT INTO frq_rubric_memory_decisions(id,memory_id,decision,actor,created_at) VALUES(?,?,?,'workspace',?)",
                       (str(uuid.uuid4()), ids[name], decision, at))
    memory("M1", 1, "rule", RULE, "2026-10-01T10:00:00+00:00", not_when="The later line asks for the method again from the start.")
    memory("M2", 2, "watch", WATCH, "2026-10-01T10:00:01+00:00", covers="Any line that pays for a label on an answer.", sources=("D1",))
    memory("M3", 3, "rule", DROPPED, "2026-10-01T10:00:02+00:00", covers="Any line about signs.", sources=("R3",))
    decide("M1", "keep", "2026-10-01T11:00:00+00:00")
    decide("M2", "keep", "2026-10-01T11:00:01+00:00")
    decide("M3", "drop", "2026-10-01T11:00:02+00:00")
    memory("M1 narrowed", 1, "rule", NARROWER, "2026-10-09T16:00:00+00:00", action="narrow", replaces=ids["M1"],
           covers="A later line of the same part that the earlier mistake carries into.", why="Your answer charged it again on another part.", sources=("R1",))
    memory("M4", 4, "rule", NEW_RULE, "2026-10-09T16:00:01+00:00", covers="Any line that grades a final answer.",
           not_when="The problem asks for the simplest form.")
    memory("M5", 5, "watch", NEW_WATCH, "2026-10-09T16:00:02+00:00", covers="Any line that pays for units.", sources=("D4",))
    memory("M6", 6, "rule", LATER_RULE, "2026-10-09T16:00:03+00:00", covers="Any final answer boxed more than once.", sources=("R4", "A1"))
    return ids


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    host = FakeHost().start()
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        definition = seed_course(client)["definition_id"]
        other = seed_course(client, name="Linear Algebra", code="MATH 304", sections=[1, 2], crn="30401")["definition_id"]
        groups = client.get("/api/state").json["course_groups"]
        group = next(g for g in groups if g["definition_id"] == definition)
        empty = next(g for g in groups if g["definition_id"] == other)
        frq = app.extensions["frq_grading"]
        plan = certified_plan(client, frq, definition, "Quiz 4")
        ids = seed_memories(frq, definition, plan)
        no_model(frq)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def view():
                    answer = page.request.get(f"{address}/api/rubric-memories/{definition}")
                    assert answer.ok, answer.status
                    return answer.json()

                def state_of(name):
                    found = view()
                    return next(m["state"] for key in ("waiting", "standing", "dropped") for m in found[key] if m["id"] == ids[name])

                # The course Home's line: four wait for the instructor (the needs-you block).
                page.goto(f"{address}/#course/{group['id']}/home")
                line = page.locator(".course-memories-link")
                expect(line.locator("[data-memories-status]")).to_have_text("4 waiting")
                expect(line.locator("[data-memories-status] .chip")).to_have_attribute("data-tone", "warning")
                page.locator("#course-shared").screenshot(path=str(output / "rubric-memories-course-home.png"))
                # ASSISTANT-MEMORY "ONE PAGE" (2026-11-10): the line opens the Memory page on this course's rubric memories.
                line.get_by_role("link", name="Rubric memories, what your rubrics are drafted and checked by (opens Memory)").click()
                expect(page.locator("#page-title")).to_have_text("Memory")
                expect(page).to_have_url(f"{address}/#memory")  # the course in the link is taken once
                rubric = page.locator('[data-memory-part="rubric"]')
                expect(rubric.get_by_role("heading", name="Rubric memories")).to_be_visible()
                expect(rubric.locator('[data-filter-group="memory-course"] [aria-pressed="true"]')).to_have_text("MATH 140")
                expect(rubric.locator(".memory-why")).to_have_text(
                    "What the rubric drafter and the rubric check follow, learned from your answers and from where graders stopped.")
                expect(page.locator("#primary-action")).to_be_hidden()

                # What waits: each proposal with what it does, its scope, where it came from; a revision with what it was.
                waiting = page.locator('[data-memory-list="waiting"] > .rubric-memory')
                kept = page.locator('[data-memory-list="kept"] > .rubric-memory')
                dropped = page.locator('[data-memory-list="dropped"] > .rubric-memory')
                expect(page.get_by_role("heading", name="Waiting for you 4")).to_be_visible()
                expect(waiting).to_have_count(4)
                expect(waiting.locator(".rubric-memory-label")).to_have_text(["M1", "M4", "M5", "M6"])
                expect(waiting.locator(".rubric-memory-action")).to_have_text(["Narrows M1", "New", "New", "New"])
                expect(waiting.locator(".chip")).to_have_text(["Rule", "Rule", "Watch", "Rule"])
                narrowed = waiting.nth(0)
                expect(narrowed.locator(":scope > .rubric-memory-rule")).to_have_text(NARROWER)
                expect(narrowed.locator(":scope > .rubric-memory-scope")).to_contain_text("WhyYour answer charged it again on another part.")
                expect(narrowed.locator(":scope > .rubric-memory-from")).to_have_text("From Quiz 4: 1 of your answers")
                was = narrowed.locator("details.rubric-memory-was")
                expect(was.locator(".disclosure-body")).to_be_hidden()
                was.locator("summary").click()
                expect(was.locator(".rubric-memory-rule")).to_have_text(RULE)
                expect(was.locator(".rubric-memory-scope")).to_contain_text("Not whenThe later line asks for the method again from the start.")
                fourth = waiting.nth(1)
                expect(fourth.locator(":scope > .rubric-memory-rule")).to_have_text(NEW_RULE)
                expect(fourth.locator(".rubric-memory-scope")).to_have_text("CoversAny line that grades a final answer.Not whenThe problem asks for the simplest form.")
                expect(fourth.locator(".rubric-memory-from")).to_have_text("From Quiz 4: 2 of your answers, 3 grader doubts")
                expect(waiting.nth(3).locator(".rubric-memory-from")).to_have_text("From Quiz 4: 1 of your answers, 1 rubric check question")
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M2"])
                expect(kept.nth(0).locator(".rubric-memory-rule")).to_have_text(RULE)
                expect(kept.nth(0).locator(".disclosure-body")).to_be_hidden()  # covers, not when and where from fold
                folds = page.locator("details.rubric-memories-dropped")
                expect(folds.locator("summary")).to_have_text("Dropped1")
                expect(folds).not_to_have_attribute("open", "")
                expect(dropped.locator(".rubric-memory-rule")).to_have_text([DROPPED])

                # Learning: certified Quiz 4 has not been read, but without a model the button waits and says why.
                learn = page.get_by_role("button", name="Learn from certified assignments")
                expect(learn).to_be_disabled()
                why = page.locator("#rubric-memories-learn-why")
                expect(why).to_have_text("Choose a Rubric drafting and review model in Compute settings first: it writes the memories.")
                expect(why.get_by_role("link", name="Compute settings")).to_have_attribute("href", "#grading?settings=1")
                expect(learn).to_have_attribute("aria-describedby", "rubric-memories-learn-why")
                page.screenshot(path=str(output / "rubric-memories.png"), full_page=True)

                # Keep one: it moves to Kept at once, the server keeps it, and focus goes to the next proposal's Keep.
                waiting.nth(0).locator("details.rubric-memory-was summary").click()  # the fold closes again, as the instructor left it
                page.get_by_role("button", name="Keep M4").click()
                expect(page.locator("#toast")).to_contain_text("Kept M4.")
                expect(waiting.locator(".rubric-memory-label")).to_have_text(["M1", "M5", "M6"])
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M2", "M4"])
                expect(page.get_by_role("button", name="Keep M5")).to_be_focused()
                assert state_of("M4") == "kept"

                # Drop one: it goes under Dropped.
                page.get_by_role("button", name="Drop M5").click()
                expect(page.locator("#toast")).to_contain_text("Dropped M5.")
                expect(waiting.locator(".rubric-memory-label")).to_have_text(["M1", "M6"])
                expect(folds.locator("summary")).to_have_text("Dropped2")
                assert state_of("M5") == "dropped"

                # Keep all: nothing waits; the narrowed M1 now stands in the earlier one's place.
                page.get_by_role("button", name="Keep all").click()
                expect(page.locator("#toast")).to_contain_text("Kept 2 memories.")
                expect(page.get_by_role("heading", name="Waiting for you")).to_have_count(0)
                expect(page.locator("#rubric-memories-kept")).to_be_focused()  # the list the proposals went to
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M2", "M4", "M6"])
                expect(kept.nth(0).locator(".rubric-memory-rule")).to_have_text(NARROWER)
                kept.nth(0).locator("summary").click()
                expect(kept.nth(0).locator(".disclosure-body")).to_contain_text(f"Replaced the earlier M1: {RULE}")
                standing = view()["standing"]
                assert [m["label"] for m in standing] == ["M1", "M2", "M4", "M6"], standing
                assert standing[0]["id"] == ids["M1 narrowed"] and not view()["waiting"], standing

                # A quiet Drop takes a kept memory back; its Keep under Dropped brings it back.
                page.get_by_role("button", name="Drop M2").click()
                expect(page.locator("#toast")).to_contain_text("Dropped M2: the rubric drafter and the check no longer follow it.")
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M4", "M6"])
                expect(folds.locator("summary")).to_have_text("Dropped3")
                assert state_of("M2") == "dropped"
                folds.locator("summary").click()
                expect(dropped.locator(".rubric-memory-label")).to_have_text(["M2", "M5", "M3"])  # the latest decision first
                page.get_by_role("button", name="Keep M2").click()
                expect(page.locator("#toast")).to_contain_text("M2 is kept again.")
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M2", "M4", "M6"])
                expect(folds.locator("summary")).to_have_text("Dropped2")
                assert state_of("M2") == "kept"

                # A kept retirement (2026-11-09 review: it was in no list, so nothing could drop it): its memory leaves Kept and is
                # listed under "Retired by a learning pass"; Bring back drops the retirement and the memory stands again.
                retirement = str(uuid.uuid4())
                with connect(frq.path) as db:
                    db.execute("""INSERT INTO frq_rubric_memories(id,definition_id,number,kind,action,replaces_id,rule,covers,not_when,why,sources_json,run_id,created_at)
                        VALUES(?,?,6,'rule','retire',?,?,?,'',?,?,NULL,?)""", (retirement, definition, ids["M6"], LATER_RULE, "Any final answer boxed more than once.",
                                                                              "Your answer on Quiz 5 says otherwise.", encoded({"assignment_id": plan, "ids": ["R4"]}),
                                                                              "2026-10-09T17:00:00+00:00"))
                    db.execute("INSERT INTO frq_rubric_memory_decisions(id,memory_id,decision,actor,created_at) VALUES(?,?,'keep','automatic',?)",
                               (str(uuid.uuid4()), retirement, "2026-10-09T17:00:01+00:00"))
                page.reload()
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M2", "M4"])
                away = page.locator("details.rubric-memories-retired")
                expect(away.locator("summary")).to_have_text("Retired by a learning pass1")
                away.locator("summary").click()
                expect(away.locator(".rubric-memory-rule")).to_have_text([LATER_RULE])
                page.get_by_role("button", name="Bring back M6").click()
                expect(page.locator("#toast")).to_contain_text("M6 stands again.")
                expect(kept.locator(".rubric-memory-label")).to_have_text(["M1", "M2", "M4", "M6"])
                expect(away).to_have_count(0)

                # The course Home's line now counts what is kept.
                page.goto(f"{address}/#course/{group['id']}/home")
                expect(page.locator(".course-memories-link [data-memories-status]")).to_have_text("4 kept")

                # With a model chosen, Learn lists what it reads and queues one pass; the page follows it without redrawing itself.
                configure_model(frq, host)
                page.goto(f"{address}/#course/{group['id']}/home/rubric-memories")
                expect(learn).to_be_enabled()
                expect(page.locator(".rubric-memories-why")).to_have_text("1 certified assignment not read yet.")
                learn.click()
                dialog = page.locator("#dialog")
                expect(dialog.locator(".rubric-memories-learn-list li")).to_have_count(1)
                expect(dialog.locator(".rubric-memories-learn-list li strong")).to_have_text("Quiz 4")
                expect(dialog.locator(".rubric-memories-learn-list li small")).to_contain_text("Fall 2026 · certified Oct 9")
                dialog.get_by_role("button", name="Learn from 1 assignment").click()
                expect(dialog).not_to_be_visible()
                expect(page.locator("#toast")).to_contain_text("Learning from 1 assignment. This page updates by itself.")
                expect(page.locator("#rubric-memories-learning")).to_be_focused()
                runs = page.locator(".rubric-memory-run")
                expect(runs).to_have_count(1)
                expect(runs.locator("strong")).to_have_text("Quiz 4")
                expect(runs.locator(".chip")).to_have_text("Waiting")
                expect(runs.locator("small")).to_contain_text("Waits its turn · you asked")
                expect(learn).to_have_count(0)  # nothing is left to learn from
                page.evaluate("""() => { window.__memoriesPage = document.querySelector('.rubric-memories-page');
                    window.__keptRow = document.querySelector('[data-memory-list="kept"] > .rubric-memory');
                    window.__loading = false; window.scrollTo(0, 120); window.__scroll = window.scrollY;
                    new MutationObserver(() => { if (document.querySelector('[data-rubric-memories-host] .loading-state')) window.__loading = true; })
                      .observe(document.querySelector('[data-rubric-memories-host]'), {childList: true, subtree: true}); }""")
                frq.process_cloud()  # the dispatcher's turn: the pass reads Quiz 4 (nothing to learn from it, so no request)
                expect(runs.locator(".chip")).to_have_text("Done", timeout=15000)
                expect(runs.locator("small")).to_contain_text("Nothing new to propose · you asked")
                expect(page.locator("#toast")).to_contain_text("Learning finished: nothing new.")
                kept_in_place = page.evaluate("""() => document.querySelector('.rubric-memories-page') === window.__memoriesPage
                    && document.querySelector('[data-memory-list="kept"] > .rubric-memory') === window.__keptRow
                    && !window.__loading && window.scrollY === window.__scroll""")
                assert kept_in_place, "the poll redrew the page instead of patching it"
                assert [r["status"] for r in view()["runs"]] == ["complete"], view()["runs"]
                assert not [r for r in host.requests if r["path"].endswith("/chat/completions")], host.requests
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(output / "rubric-memories-learning.png"), full_page=True)

                # A course with nothing: no status on its Home, and the shared empty state.
                with page.expect_response(lambda response: f"/api/rubric-memories/{other}" in response.url):
                    page.goto(f"{address}/#course/{empty['id']}/home")
                expect(page.locator(".course-memories-link")).to_be_visible()
                expect(page.locator(".course-memories-link [data-memories-status]")).to_have_text("")
                page.goto(f"{address}/#course/{empty['id']}/home/rubric-memories")  # an old link lands on the Memory page, on that course
                expect(page).to_have_url(f"{address}/#memory")
                expect(page.locator('[data-filter-group="memory-course"] [aria-pressed="true"]')).to_have_text("MATH 304")
                state = page.locator("[data-rubric-memories-host] .empty-state")
                expect(state.get_by_role("heading", name="No rubric memories yet")).to_be_visible()
                expect(state).to_contain_text("After you certify an assignment, Faculty learns memories here and keeps them. Drop any you disagree with.")
                expect(page.locator("#rubric-memories-learning")).to_have_count(0)
                # The other course is one click away, on the same page.
                page.locator('[data-filter-group="memory-course"]').get_by_role("button", name="MATH 140").click()
                expect(page.locator('[data-memory-list="kept"] > .rubric-memory')).to_have_count(4)
                page.screenshot(path=str(output / "rubric-memories-empty.png"), full_page=True)

                # The narrowest desktop window (DESIGN-LANGUAGE, 2026-10-03): no sideways scrolling.
                narrow = browser.new_page(viewport={"width": 1280, "height": 900})
                narrow.on("pageerror", lambda error: errors.append(str(error)))
                narrow.goto(f"{address}/#course/{group['id']}/home/rubric-memories")
                expect(narrow.locator('[data-memory-list="kept"] > .rubric-memory')).to_have_count(4)
                width = narrow.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                assert width[0] <= width[1], width
                narrow.screenshot(path=str(output / "rubric-memories-1280.png"), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            host.stop()
    print("Rubric memories browser check passed.")


if __name__ == "__main__":
    main()
