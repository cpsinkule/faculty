"""Real drawer flows for saved tasks, clarification, evidence, grouped approval and files, and one card for one change in the
floating chat (ASSISTANT-BATCHES ONE CARD FOR ONE CHANGE), drawn in the panel beside it (ASSISTANT "CHANGES BESIDE THE CHAT").
Uses a scripted local engine and synthetic data, never a model connection."""
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server
from app import create_app
from browser_course_pages import seed_course
from rosters import connect
from test_storage import HEADERS


class ScriptedEngine:
    name = "test"

    def close(self):
        pass

    def stop(self):
        pass

    def turn(self, c, text):
        def tool(name, **args):
            import json
            result, event = c.service.run_tool(c.session, name, args)
            c.tool_event(event)
            return json.loads(result)
        if "Faculty action receipts" in text:
            tool("artifacts")
            c.emit("assistant", text="Verified the reviewed work from its receipts.")
        elif "instructor answered your clarification" in text:
            c.emit("assistant", text="Continuing with your selected sections.")
        elif text.endswith("Ask me a question"):
            tool("ask_question", questions=[{"title": "Which sections?", "options": ["507", "508", "Both"]}])
        elif text.endswith("Make a worksheet"):
            with connect(c.service.database) as db:
                owner = db.execute("SELECT id FROM course_definitions").fetchone()[0]
            tool("create_artifact", level="definition", owner_id=owner, title="Practice worksheet", filename="practice.txt", content="1. Show your reasoning.\n\n2. Check your answer.")
            c.emit("assistant", text="The worksheet is ready for you to review.")
        elif text.endswith("Add related links"):
            ids = [tool("faculty_write", method="POST", path="/api/tools", body={"name": name, "url": "https://example.test"}, summary=f"Add {name}")["action_id"] for name in ("One", "Two")]
            tool("group_actions", title="Teaching links", action_ids=ids)
            c.emit("assistant", text="Review both links together in Changes.")
        elif text.endswith("Add one link"):
            tool("faculty_write", method="POST", path="/api/tools", body={"name": "Solo", "url": "https://example.test"}, summary="Add Solo")
            c.emit("assistant", text="One link waits for you.")
        elif text.endswith("Give everyone the credit"):
            # The same change for 25 records, grouped in parts first (the 20-card limit of 2026-10-09 morning), then as one group.
            ids = [tool("faculty_write", method="POST", path="/api/tools", body={"name": f"Credit {n:02d}", "url": "https://example.test"},
                        summary=f"Award Student {n:02d} the Q24 credit")["action_id"] for n in range(25)]
            tool("group_actions", title="Credit, part 1", action_ids=ids[:20])
            tool("group_actions", title="Credit, part 2", action_ids=ids[20:])
            tool("group_actions", title="Exam 1 Q24: credit for parameterization work", action_ids=ids)
            c.emit("assistant", text="25 grade changes wait on one card.")
        else:
            tool("courses")
            c.emit("assistant", text="Read the current courses. Ready to help.")
            c.emit('verification', check_type='tool_use', status='checked',
                   detail='Advisory tool-use review; permissions and receipts remain authoritative.', checks=[], sources=[])


def main():
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(str(Path(folder) / "data"))
        client = app.test_client()
        seed_course(client, sections=[507, 508])
        server = create_server(app, host="127.0.0.1", port=0, threads=12)
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{server.effective_port}"
        errors = []
        try:
            with patch("assistant.conversations.config.assistant", return_value={"name": "Scripted"}), \
                 patch("assistant.conversations.engine_for", side_effect=lambda _: ScriptedEngine()), sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.cspViolations=[]; document.addEventListener('securitypolicyviolation', e => cspViolations.push(e.violatedDirective));")
                page.goto(base + "/#overview")
                page.locator(".assistant-launch").click()
                panel = page.locator("#assistant-panel")
                def send(text):
                    field = panel.locator("#assistant-text")
                    field.fill(text)
                    field.press("Enter")
                send("Read courses")
                expect(panel.locator(".assistant-evidence")).to_have_count(1, timeout=15000)
                expect(panel.get_by_text('Tool-use review', exact=True)).not_to_be_visible()
                panel.locator(".assistant-steps > summary").click()  # QUIET STEPS: the sources are in the answer's fold
                expect(panel.get_by_text('Tool-use review', exact=True)).to_be_visible()
                panel.locator(".assistant-evidence summary").click()
                expect(panel.locator('.assistant-evidence a[href="/api/state?include=core"]')).to_be_visible()
                panel.get_by_role("tab", name="Saved").click()
                panel.get_by_text("Save this conversation", exact=True).click()
                save = panel.locator("[data-save-conversation]")
                save.locator('[name="title"]').fill("Term preparation")
                save.locator('[name="goal"]').fill("Prepare teaching materials")
                save.locator('[name="retention_days"]').select_option("7")
                save.get_by_role("button", name="Save conversation", exact=True).click()
                expect(panel.locator('[data-open-saved]')).to_have_count(1)
                # ASSISTANT-MEMORY "ONE PAGE": the Saved tab counts the memories and links to the Memory page, where one is added.
                expect(panel.locator("[data-assistant-workspace]")).to_contain_text("0 memories kept.")
                expect(panel.get_by_role("link", name="Open Memory")).to_have_attribute("href", "#memory")
                # ASSISTANT-MEMORY "EVERY WAY IN": what the form saves is checked as a chat message is, so Jev is scripted
                # for this save alone (nothing wrong with it); the rest of this script runs with Jev off, as before.
                from test_assistant_memory import answering
                with answering():
                    added = page.evaluate("""() => fetch('/api/assistant/memories', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Teaching-Request': '1'},
                        body: JSON.stringify({content: 'Prefer short answers.', scope: 'workspace'})}).then(r => r.status)""")
                assert added == 201, added
                panel.get_by_role("tab", name="Chat", exact=True).click()
                send("Ask me a question")
                question = panel.locator("[data-assistant-question]")
                expect(question).to_be_visible(timeout=15000)
                # Choosing an offered answer would send it at once (ONE-CLICK ANSWERS); the box takes any other answer, and a
                # conversation poll must keep the answer being typed.
                expect(question.get_by_role("button", name="Both", exact=True)).to_be_visible()
                question.locator("textarea").fill("Both, including late additions")
                page.wait_for_timeout(1100)
                expect(question.locator("textarea")).to_have_value("Both, including late additions")
                question.get_by_role("button", name="Answer and continue").click()
                expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("selected sections", timeout=15000)
                send("Add related links")
                expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("Review both links", timeout=15000)
                # ONE CARD FOR ONE CHANGE: two cards of one route are one card in the chat and in Changes, with Approve all.
                expect(panel.locator("[data-assistant-chat] .assistant-batch-card")).to_have_count(1)
                expect(panel.locator("[data-assistant-chat] [data-assistant-approve]")).to_have_count(0)
                panel.get_by_role("tab", name="Changes").click()
                group = panel.locator("[data-assistant-changes] .assistant-batch-card")
                expect(group).to_have_count(1)
                expect(group).to_contain_text("2 changes · 2 wait for you")
                group.get_by_role("button", name="Approve all 2").click()
                expect(panel.locator("[data-assistant-changes] [data-batch-approve]")).to_have_count(0, timeout=15000)
                expect(panel.locator('[data-assistant-changes] .assistant-batch-card[data-done="true"]')).to_contain_text("2 changes · 2 done")
                panel.get_by_role("tab", name="Chat", exact=True).click()
                expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("Verified", timeout=15000)
                send("Make a worksheet")
                expect(panel.locator('[data-assistant-chat] [data-assistant-approve]').last).to_have_text("Save file", timeout=15000)
                panel.locator('[data-assistant-chat] [data-assistant-approve]').last.click()
                expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("Verified", timeout=15000)
                panel.get_by_role("tab", name="Saved").click()
                expect(panel.get_by_role("link", name="practice.txt")).to_have_count(1)
                url = panel.get_by_role("link", name="practice.txt").get_attribute("href")
                assert client.get(url).data == b"1. Show your reasoning.\n\n2. Check your answer."
                # Forget only the process conversation; the saved task restores in the same tab after refresh.
                ident = page.evaluate("sessionStorage.getItem('faculty:assistant:conversation')")
                assert client.post(f"/api/assistant/conversations/{ident}/close", json={}, headers=HEADERS).status_code == 200
                page.reload()
                page.locator(".assistant-launch").click()
                expect(page.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("Verified", timeout=15000)
                assert page.evaluate("sessionStorage.getItem('faculty:assistant:conversation')") == ident
                # The 1280 px window this check runs in keeps the drawer's Saved tab inside the window (desktop only).
                page.locator('[data-assistant-tab="workspace"]').click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                # ONE CARD FOR ONE CHANGE in the floating chat (the instructor's report, 2026-10-09): 25 cards of one change, first
                # grouped in parts, are one card; one is left out, Approve all submits the other 24, each with its own receipt.
                panel.locator('[data-assistant="close"]').click()
                page.keyboard.press("Meta", delay=120); page.keyboard.press("Meta", delay=120)
                floating = page.get_by_role("dialog", name="Assistant chat", exact=True)
                expect(floating).to_be_visible()
                field = floating.get_by_label("Message the assistant")
                # CHANGES BESIDE THE CHAT: no panel before the conversation has a card, and the window is as wide as the chat.
                changes = floating.locator("[data-area-changes]")
                expect(changes).to_be_hidden()
                page.evaluate("localStorage.removeItem('faculty:assistant:window')")
                wide = lambda node: round(node.bounding_box()["width"])
                narrow = wide(floating)
                column = wide(floating.locator(".assistant-area-main"))
                field.fill("Give everyone the credit"); field.press("Enter")
                expect(floating.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("one card", timeout=15000)
                card = floating.locator(".assistant-batch-card")
                expect(card).to_have_count(1)
                # The card is in the panel beside the conversation, which keeps one line for it; the window grew by the panel's
                # width, whole in the browser window, and the conversation kept its own.
                expect(changes).to_be_visible()
                expect(changes.locator(".assistant-batch-card")).to_have_count(1)
                expect(floating.locator("[data-area-chat] .assistant-card")).to_have_count(0)
                line = floating.locator("[data-area-chat] .assistant-card-line")
                expect(line).to_have_count(1)
                expect(line).to_contain_text("Waits for you")
                expect(line).to_contain_text("Exam 1 Q24: credit for parameterization work · 25 changes")
                expect(changes.locator("[data-area-changes-note]")).to_have_text("25 wait for you")
                assert wide(floating) == narrow + 440 and wide(floating.locator(".assistant-area-main")) == column, (wide(floating), narrow, column)
                box = floating.bounding_box()
                assert box["x"] >= 0 and box["x"] + box["width"] <= 1280, box
                # The composer stays in the window beside a panel taller than it (the panel scrolls, not the window).
                send_box = floating.locator(".assistant-area-compose [type=submit]").bounding_box()
                assert send_box["y"] + send_box["height"] <= box["y"] + box["height"], (send_box, box)
                page.screenshot(path="assistant-changes-panel.png")
                # Hide puts the panel away and the window is the chat's again; the line in the conversation brings its card back.
                changes.get_by_role("button", name="Hide", exact=True).click()
                expect(changes).to_be_hidden()
                assert wide(floating) == narrow
                expect(floating.locator("[data-area-changes-show]")).to_have_text("Show changes (25)")
                line.get_by_role("button").click()
                expect(changes).to_be_visible()
                assert page.evaluate("document.activeElement?.matches('[data-area-changes-list] > .assistant-batch-card')")
                changes.get_by_role("button", name="Hide", exact=True).click()
                floating.locator("[data-area-changes-show]").click()
                expect(changes).to_be_visible()
                expect(floating.locator("[data-area-changes-show]")).to_be_hidden()
                # RESIZE beside the panel: the window's right edge drags the conversation's width (never under 360 px), which is
                # what is remembered; the panel keeps its own.
                def drag_edge(by):
                    edge = floating.bounding_box()
                    page.mouse.move(edge["x"] + edge["width"] - 2, edge["y"] + 300); page.mouse.down()
                    page.mouse.move(edge["x"] + edge["width"] - 2 + by, edge["y"] + 300, steps=5); page.mouse.up()
                drag_edge(-60)
                assert wide(floating) == narrow - 60 + 440 and wide(changes) == 440, (wide(floating), wide(changes), narrow)
                kept = page.evaluate("JSON.parse(localStorage.getItem('faculty:assistant:window'))")
                assert kept["w"] == narrow - 60, (kept, narrow)
                drag_edge(-400)
                assert wide(floating) == 360 + 440 and wide(changes) == 440, (wide(floating), wide(changes))
                expect(card).to_contain_text("Exam 1 Q24: credit for parameterization work")
                expect(card).to_contain_text("25 changes · 25 wait for you")
                expect(floating.locator("[data-assistant-approve]")).to_have_count(0)
                card.get_by_text("Each change", exact=True).click()
                expect(card.locator(".assistant-batch-list li")).to_have_count(25)
                card.locator(".assistant-batch-list li").first.get_by_role("button", name="Leave out").click()
                expect(card).to_contain_text("25 changes · 24 wait for you · 1 left out", timeout=15000)
                expect(card.locator(".assistant-batch-list li")).to_have_count(25)  # the list stays open through the redraw
                card.get_by_role("button", name="Approve all 24").click()
                expect(card).to_contain_text("25 changes · 24 done, 1 left out", timeout=30000)
                expect(card.locator("[data-batch-approve]")).to_have_count(0)
                # Done, the card stays in the panel (nothing waits), and its line in the conversation says so.
                expect(changes).to_be_visible()
                expect(changes.locator("[data-area-changes-note]")).to_have_text("")
                expect(line).to_contain_text("Done")
                page.screenshot(path="assistant-changes-panel-done.png")
                with connect(app.extensions["assistant"].database) as db:
                    kinds = [r[0] for r in db.execute("""SELECT (SELECT kind FROM assistant_action_events e WHERE e.action_id=a.id
                        ORDER BY e.id DESC LIMIT 1) FROM assistant_actions a WHERE a.summary LIKE 'Award Student %' ORDER BY a.summary""")]
                assert kinds == ["declined"] + ["succeeded"] * 24, kinds
                # A LOST ANSWER (2026-10-28): Faculty did the approved change but its answer never reached the page. The window
                # says it may be done and reads the cards again, so the card shows Done, not its Approve button under the error.
                field.fill("Add one link"); field.press("Enter")
                solo = changes.locator("[data-assistant-approve]")
                expect(solo).to_have_count(1, timeout=15000)
                def lose(route):
                    route.fetch()  # Faculty answers; the page never hears it
                    route.abort("connectionreset")
                page.route("**/api/assistant/actions/*/approve", lose)
                solo.click()
                problem = floating.locator("[data-area-error]")
                expect(problem).to_contain_text("It may already be done", timeout=15000)
                expect(solo).to_have_count(0, timeout=15000)
                expect(problem).to_be_hidden()
                page.unroute("**/api/assistant/actions/*/approve")
                with connect(app.extensions["assistant"].database) as db:
                    assert db.execute("""SELECT (SELECT kind FROM assistant_action_events e WHERE e.action_id=a.id ORDER BY e.id DESC LIMIT 1)
                        FROM assistant_actions a WHERE a.summary='Add Solo'""").fetchone()[0] == "succeeded"
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                assert page.evaluate("window.cspViolations") == []
                browser.close()
        finally:
            server.close()
    print("assistant saved tasks, input, evidence, batches and files ok")


if __name__ == "__main__":
    main()
