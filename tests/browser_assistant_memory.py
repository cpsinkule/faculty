"""ASSISTANT MEMORY in the real drawer (requirement, 2026-10-10): a typed statement is remembered and
says so, a newer one replaces it, Undo takes it back in one click, a statement naming a student is refused, and the Saved tab
lists what stands with what was replaced. Then (2026-10-10, "do 1 and 2 ... fix findings 1 and 2"): the form refuses what a
chat message would, an answer's "How it got this" lists the memories its request was given with Remove and Restore, and a
fragment is reworded with what was typed beside it and "Keep my words". Then (2026-10-10, "Rule only, rewritten"): a rule
said of one student's case is remembered as the rule, without the student. A scripted engine, a scripted rewording model and
scripted Jev answers on synthetic data; no model is reached.
MEMORY_SCREENSHOTS=<folder on /mnt> keeps the pictures taken at 1280 and 1440."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from app import create_app
from browser_course_pages import seed_course
from jev import client


class ScriptedEngine:
    name = "test"

    def close(self):
        pass

    def stop(self):
        pass

    def turn(self, c, text):
        c.emit("assistant", text="Understood.")


def judge(state, questions):
    """Jev's answers for the memory requests of this script: every statement lasting and the instructor's own, a UIN named,
    and "Three versions…" replacing the kept "four versions"."""
    message = state.get("instructor_message", "")
    answers = {}
    for name, question in questions.items():
        base = name.split("__")[0]
        if question["type"] == "choice":
            pick = "preference" if base == "kind" else "workspace"
            pick = pick if pick in question["criteria"] else next(iter(question["criteria"]))
            answers[name] = {"type": "choice", "choice": pick, "confidence": 1.0,
                             "probabilities": {option: float(option == pick) for option in question["criteria"]}}
            continue
        stem, _, index = name.rpartition("_")
        value = .99 if name in ("lasting", "own_words", "explicit", "faithful", "standalone") or stem == "use" else .01  # every memory bears on a request here
        if name == "lasting" and message.startswith(("What", "Draft", "Again")):
            value = .02
        if name == "about_student" and "double time" in message:
            value = .95  # true of that one student, not a rule for a kind of case
        if "rewrite" in state:  # THE REWRITE STEP: this script's rewording says what was typed and no more
            value = .95 if name in ("subject", "says", "standalone") else .02
        elif name == "standalone" and str(state.get("new_statement", "")).startswith("Three of those"):
            value = .1  # a fragment: it leans on the message before it
        if stem == "replaces" and index.isdigit():
            kept = state["candidates"][int(index)]["text"]
            value = .95 if state["new_statement"].startswith("Three versions") and "four versions" in kept else .01
        answers[name] = {"type": "noul", "noul": value}
    return {"model": client.MODEL, "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 0}}


class Writer:
    """The Assistant's own model, scripted, for the one fragment this script types."""

    def submit(self, request):
        if request["prompt"] == "memory_rule":  # A RULE SAID OF ONE STUDENT'S CASE: the rule, without the student
            assert request["payload"]["instructor_message"].startswith("For UIN 531004218"), request["payload"]
            return {"state": "completed", "result": {"output": {"resolved": True, "rewrite": "An absence to attend another course's exam review is not excused."}}}
        assert request["prompt"] == "memory_rewrite", request["prompt"]
        assert request["payload"]["earlier_instructor_messages"][-1] == "Draft Quiz 5 with four versions.", request["payload"]
        time.sleep(2.5)  # STILL BEING CONSIDERED: as on live, the model is still writing when the answer is in
        return {"state": "completed", "result": {"output": {"resolved": True, "rewrite": "Make three versions of each quiz from now on, not four."}}}


def main():
    shots = os.environ.get("MEMORY_SCREENSHOTS")
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(str(Path(folder) / "data"))
        seed_course(app.test_client(), sections=[507, 508])
        server = create_server(app, host="127.0.0.1", port=0, threads=12)
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{server.effective_port}"
        errors = []
        try:
            with patch("assistant.conversations.config.assistant", return_value={"name": "Scripted"}), \
                 patch("assistant.conversations.engine_for", side_effect=lambda _: ScriptedEngine()), \
                 patch("jev.client.available", return_value=True), patch("jev.client.evaluate", side_effect=judge), \
                 patch("jev.checks.evidence", return_value=None), patch.dict(os.environ, {"FACULTY_ASSISTANT_TOOL_GUIDANCE": "0"}), \
                 patch.object(app.extensions["frq_grading"], "cloud_provider", return_value=Writer()), \
                 sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.cspViolations=[]; document.addEventListener('securitypolicyviolation', e => cspViolations.push(e.violatedDirective));")
                page.goto(base + "/#overview")
                page.locator(".assistant-launch").click()
                panel = page.locator("#assistant-panel")
                notes = panel.locator(".assistant-memory")

                def send(text, answers):
                    field = panel.locator("#assistant-text")
                    field.fill(text)
                    field.press("Enter")
                    expect(panel.locator('.assistant-msg[data-from="assistant"]')).to_have_count(answers, timeout=15000)

                # A passing question leaves no note.
                send("What is waiting for me today?", 1)
                expect(notes).to_have_count(0)
                # A lasting statement is remembered as typed, and says so with Undo.
                send("Make four versions of every quiz.", 2)
                expect(notes).to_have_count(1, timeout=15000)
                expect(notes.nth(0)).to_contain_text("Remembered: “Make four versions of every quiz.”")
                expect(notes.nth(0).get_by_role("button", name="Undo")).to_be_visible()
                # A newer statement replaces it and names what it replaced.
                send("Three versions from now on, not four.", 3)
                expect(notes).to_have_count(2, timeout=15000)
                expect(notes.nth(1)).to_contain_text("It replaces “Make four versions of every quiz.”")
                # A statement about one student is refused, with the reason and no Undo.
                send("Remember that UIN 531004218 gets double time.", 4)
                expect(notes).to_have_count(3, timeout=15000)
                expect(notes.nth(2)).to_contain_text("Not remembered: it is about one student.")
                expect(notes.nth(2).get_by_role("button")).to_have_count(0)
                if shots:
                    panel.screenshot(path=str(Path(shots) / "memory-chat-1280.png"))
                # Undo takes the replacement back in one click; the earlier one stands again.
                notes.nth(1).get_by_role("button", name="Undo").click()
                expect(notes.nth(1)).to_contain_text("Taken back", timeout=15000)
                expect(notes.nth(1)).to_contain_text("The earlier one stands again.")
                expect(notes.nth(1).get_by_role("button")).to_have_count(0)
                listing = json.loads(page.evaluate("fetch('/api/assistant/memory').then(r => r.text())"))
                standing = [m["content"] for m in listing["memories"] if m["status"] == "current"]
                assert standing == ["Make four versions of every quiz."], standing
                # ASSISTANT-MEMORY "ONE PAGE" (requirement, 2026-11-10): the Saved tab only points to the Memory page, where what
                # stands is one row each and what was taken back is listed apart.
                panel.get_by_role("tab", name="Saved").click()
                saved = panel.locator("[data-assistant-workspace]")
                expect(saved).to_contain_text("1 memory kept.", timeout=15000)
                expect(saved.locator("form[data-memory], [data-memory-remove]")).to_have_count(0)
                saved.get_by_role("link", name="Open Memory").click()
                expect(page.locator("#page-title")).to_have_text("Memory")
                panel.locator('[data-assistant="close"]').click()
                memory = page.locator("#memory-section")
                rows = memory.locator('[data-memory-list="assistant"] > .memory-row')
                gone = memory.locator("details.memory-gone")

                def unfold():
                    if gone.get_attribute("open") is None:
                        gone.locator("summary").click()
                expect(rows).to_have_count(1, timeout=15000)
                expect(gone.locator("summary")).to_have_text("Replaced and removed1")
                unfold()
                expect(gone).to_contain_text("Taken back")
                gone.locator("[data-memory-restore]").click()
                expect(rows).to_have_count(2, timeout=15000)
                # Its words open in place; a Save of new words is checked as a chat message's is.
                row = rows.filter(has_text="Make four versions of every quiz.")
                row.get_by_role("button", name="Edit").click()
                expect(row.locator("textarea")).to_be_focused()
                row.locator("textarea").fill("Make four versions of every quiz, each on its own page.")
                row.get_by_role("button", name="Save", exact=True).click()
                row = rows.filter(has_text="each on its own page")
                expect(row).to_have_count(1, timeout=15000)
                # WHAT A REQUEST IS TOLD: the mark and what it applies to are on the row and save as they change.
                tick = row.get_by_label("With every request")
                expect(tick).not_to_be_checked()
                tick.check()
                expect(page.locator("#toast")).to_contain_text("Given with every request.", timeout=15000)
                row.get_by_label("Applies to").select_option(label="MATH 140, every term")
                expect(page.locator("#toast")).to_contain_text("Moved.", timeout=15000)
                expect(rows.first).to_contain_text("each on its own page")  # what goes with every request leads the list
                marked = json.loads(page.evaluate("fetch('/api/assistant/memory').then(r => r.text())"))["memories"]
                assert [(m.get("always"), m["scope"]) for m in marked if m["status"] == "current" and "own page" in m["content"]] == [(True, "course")], marked
                memory.locator('[data-filter-group="memory-filter"]').get_by_role("button", name="With every request").click()
                expect(rows).to_have_count(1)
                memory.locator('[data-filter-group="memory-filter"]').get_by_role("button", name="All").click()
                expect(rows).to_have_count(2)
                if shots:
                    page.screenshot(path=str(Path(shots) / "memory-page-1280.png"), full_page=True)
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.screenshot(path=str(Path(shots) / "memory-page-1440.png"), full_page=True)
                    page.set_viewport_size({"width": 1280, "height": 900})
                width = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                assert width[0] <= width[1], width  # the narrowest desktop window: no sideways scrolling
                # The switches and the numbers save where they are; a number is back at the measured one in a click.
                memory.get_by_label("Keep what I type in chat").uncheck()
                expect(page.locator("#toast")).to_contain_text("Keep what I type in chat: off.", timeout=15000)
                memory.get_by_label("Most memories for one request").fill("3")
                memory.locator("[data-memory-limits]").get_by_role("button", name="Save").click()
                expect(page.locator("#toast")).to_contain_text("Saved.", timeout=15000)
                settings = json.loads(page.evaluate("fetch('/api/assistant/memory-settings').then(r => r.text())"))
                assert (settings["capture"], settings["given"], settings["use_gate"]) == (False, 3, .6), settings
                memory.get_by_role("button", name="Use the measured values").click()
                expect(memory.get_by_label("Most memories for one request")).to_have_value("8", timeout=15000)
                memory.get_by_label("Keep what I type in chat").check()
                expect(page.locator("#toast")).to_contain_text("Keep what I type in chat: on.", timeout=15000)
                # Remove, then Erase: the words are gone from the list and from its history.
                row.get_by_role("button", name="Remove").click()
                expect(rows).to_have_count(1, timeout=15000)
                unfold()
                erase = gone.locator("[data-memory-erase]")
                erase.click()
                expect(erase).to_have_text("Erase for good?")
                erase.click()
                expect(gone).to_have_count(0, timeout=15000)

                # PICTURES (requirement, 2026-10-10): a picture attached to a kept message is kept with
                # it, shown small in the note and on its row, and erased with its words.
                import io
                from PIL import Image, ImageDraw
                drawn = Image.new("RGB", (640, 480), "white")
                pen = ImageDraw.Draw(drawn)
                for seat in range(12):
                    pen.rectangle((40 + seat % 4 * 150, 60 + seat // 4 * 130, 160 + seat % 4 * 150, 150 + seat // 4 * 130), outline="black", width=3)
                out = io.BytesIO()
                drawn.save(out, "PNG")
                page.locator(".assistant-launch").click()
                panel.get_by_role("tab", name="Chat", exact=True).click()
                panel.locator("[data-assistant-attach-input]").set_input_files({"name": "room.png", "mimeType": "image/png", "buffer": out.getvalue()})
                expect(panel.get_by_text("room.png", exact=False).first).to_be_visible(timeout=15000)
                # the attach list shows Remove only once the upload is done (static/assistant-attach.js)
                expect(panel.locator("[data-assistant-detach]")).to_have_count(1, timeout=15000)
                send("This is how I arrange the room for exams.", 5)
                expect(notes).to_have_count(4, timeout=15000)
                expect(notes.nth(3)).to_contain_text("The picture you attached is kept with it.")
                kept = notes.nth(3).locator(".assistant-memory-pictures img")
                expect(kept).to_have_count(1)
                page.wait_for_function("(() => { const i = document.querySelector('#assistant-panel .assistant-memory-pictures img'); return i && i.complete && i.naturalWidth > 0; })()")
                if shots:
                    notes.nth(3).scroll_into_view_if_needed()
                    panel.screenshot(path=str(Path(shots) / "memory-picture-chat-1280.png"))
                route = kept.get_attribute("src")
                assert route.startswith("/api/assistant/memory-pictures/"), route
                # The page was open behind the drawer: it is told a memory changed only by a visit, so it is opened again.
                panel.locator('[data-assistant="close"]').click()
                page.goto(base + "/#overview")
                page.goto(base + "/#memory")
                pictured = rows.filter(has_text="arrange the room")
                expect(pictured.locator(".memory-pictures img")).to_have_count(1, timeout=15000)
                if shots:
                    page.screenshot(path=str(Path(shots) / "memory-picture-page-1280.png"), full_page=True)
                pictured.get_by_role("button", name="Remove").click()
                unfold()
                erase = gone.locator("[data-memory-erase]")
                expect(erase).to_have_text("Erase its words and pictures")
                erase.click()
                erase.click()
                expect(gone).to_have_count(0, timeout=15000)
                assert page.evaluate("(route) => fetch(route).then(r => r.status)", route) == 404

                # EVERY WAY IN: the form refuses what a chat message would, and says why in place.
                memory.locator("details.memory-add > summary").click()
                blank = memory.locator("form[data-memory-new]")
                blank.locator('[name="content"]').fill("UIN 531004218 gets double time on quizzes.")
                blank.get_by_role("button", name="Add memory").click()
                expect(blank.locator(".form-error")).to_contain_text("names a student", timeout=15000)
                listed = json.loads(page.evaluate("fetch('/api/assistant/memory').then(r => r.text())"))["memories"]
                assert [m["content"] for m in listed] == ["Three versions from now on, not four."], listed

                # MEMORIES GIVEN: under an answer, closed, the memories its request was given, each with Change and Remove.
                page.locator(".assistant-launch").click()
                panel.get_by_role("tab", name="Chat", exact=True).click()
                send("Always sign as Dr. S.", 6)
                expect(notes).to_have_count(5, timeout=15000)
                send("Draft a hello to the class.", 7)
                folds = panel.locator("details.assistant-steps")
                asked = folds.count() - 1  # the turn just answered
                fold = folds.nth(asked)
                expect(fold.locator("summary")).to_contain_text("2 memories")
                assert fold.get_attribute("open") is None
                fold.locator("summary").click()
                given = fold.locator("[data-memories-given]")
                expect(given).to_contain_text("Memories given · 2")
                row = given.locator("li", has_text="Always sign as Dr. S.")
                expect(row).to_contain_text("Everything · from a chat message")
                expect(row.get_by_role("link", name="Change")).to_be_visible()
                if shots:
                    fold.scroll_into_view_if_needed()
                    panel.screenshot(path=str(Path(shots) / "memory-given-1280.png"))
                # Remove takes it out of the next message's request; the answer it was given to says it was removed since.
                row.get_by_role("button", name="Remove").click()
                expect(row).to_contain_text("removed since", timeout=15000)
                send("Again, shorter.", 8)
                later = folds.nth(folds.count() - 1)
                expect(later.locator("summary")).to_contain_text("1 memory")
                later.locator("summary").click()
                expect(later.locator("[data-memories-given]")).to_contain_text("Three versions from now on, not four.")
                expect(later.locator("[data-memories-given]")).not_to_contain_text("Always sign")
                row.get_by_role("button", name="Restore").click()
                expect(row.get_by_role("button", name="Remove")).to_be_visible(timeout=15000)
                expect(row).not_to_contain_text("removed since")

                # THE REWRITE STEP: a fragment is kept as typed at once, then reworded; the note shows both and the way back.
                send("Draft Quiz 5 with four versions.", 9)
                send("Three of those from now on, not four.", 10)
                expect(notes).to_have_count(6, timeout=15000)
                # STILL BEING CONSIDERED (requirement, 2026-10-10): the
                # answer is in and the model is still writing. The note says so and offers no form, and the chat reads on
                # until the rewording is in (well inside the drawer's 20-second idle read).
                expect(notes.nth(5)).to_contain_text("“Three of those from now on, not four.” It may not read well on its own; I’m rewording it.")
                expect(notes.nth(5).get_by_role("link")).to_have_count(0)
                expect(notes.nth(5)).to_contain_text("Remembered: “Make three versions of each quiz from now on, not four.”", timeout=6000)
                expect(notes.nth(5)).to_contain_text("You typed “Three of those from now on, not four.”")
                if shots:
                    notes.nth(5).scroll_into_view_if_needed()
                    panel.screenshot(path=str(Path(shots) / "memory-reworded-1280.png"))
                notes.nth(5).get_by_role("button", name="Keep my words").click()
                expect(notes.nth(5)).to_contain_text("Remembered: “Three of those from now on, not four.”", timeout=15000)
                expect(notes.nth(5).get_by_role("button", name="Keep my words")).to_have_count(0)
                expect(notes.nth(5).get_by_role("button", name="Undo")).to_be_visible()
                # "reword it" on words left as typed opens that memory's own row on the Memory page, the caret in its words;
                # saved there, the chat's note shows the instructor's wording and no longer offers the Assistant's Undo.
                notes.nth(5).get_by_role("link", name="reword it").click()
                expect(page.locator("#page-title")).to_have_text("Memory")
                words = memory.locator("textarea:focus")
                expect(words).to_have_value("Three of those from now on, not four.", timeout=15000)
                expect(words).to_be_in_viewport()
                if shots:
                    page.screenshot(path=str(Path(shots) / "memory-reword-it-1280.png"))
                words.fill("Make three versions of every quiz from now on, not four.")
                words.locator("xpath=ancestor::form").get_by_role("button", name="Save", exact=True).click()
                expect(rows.filter(has_text="Make three versions of every quiz from now on, not four.")).to_have_count(1, timeout=15000)
                expect(notes.nth(5)).to_contain_text("Remembered: “Make three versions of every quiz from now on, not four.” You reworded it in Memory.", timeout=15000)
                expect(notes.nth(5).get_by_role("button")).to_have_count(0)
                expect(notes.nth(5).get_by_role("link")).to_have_count(0)

                # A RULE SAID OF ONE STUDENT'S CASE: the rule is remembered without the student, says whose words it is, and
                # what was typed is nowhere in the note or the list.
                send("For UIN 531004218's follow-up: this type of absence is not excused.", 11)
                expect(notes).to_have_count(7, timeout=15000)
                expect(notes.nth(6)).to_contain_text("Remembered: “An absence to attend another course's exam review is not excused.” I wrote this from your message, without the student.", timeout=15000)
                expect(notes.nth(6)).not_to_contain_text("531004218")
                expect(notes.nth(6).get_by_role("button", name="Keep my words")).to_have_count(0)
                expect(notes.nth(6).get_by_role("button", name="Undo")).to_be_visible()
                if shots:
                    notes.nth(6).scroll_into_view_if_needed()
                    panel.screenshot(path=str(Path(shots) / "memory-rule-1280.png"))
                listed = json.loads(page.evaluate("fetch('/api/assistant/memory').then(r => r.text())"))["memories"]
                assert not [m for m in listed if "531004218" in m["content"]], listed
                assert [m.get("ruled") for m in listed if m["content"].startswith("An absence to attend")] == [True], listed

                # The floating chat has no idle read at all: its note says the Assistant is rewording, then shows the rewording.
                panel.locator('[data-assistant="close"]').click()
                expect(panel).to_be_hidden()
                page.keyboard.press("Meta", delay=120)
                page.keyboard.press("Meta", delay=120)
                floating = page.locator(".assistant-area-bubble")
                expect(floating).to_be_visible()
                box = floating.get_by_label("Message the assistant")
                for count, text in enumerate(("Draft Quiz 5 with four versions.", "Three of those from now on, not four."), 1):
                    box.fill(text)
                    box.press("Enter")
                    expect(floating.locator('.assistant-msg[data-from="assistant"]')).to_have_count(count, timeout=15000)
                note = floating.locator(".assistant-memory")
                expect(note).to_have_count(1, timeout=15000)
                expect(note).to_contain_text("It may not read well on its own; I’m rewording it.")
                expect(note).to_contain_text("Remembered: “Make three versions of each quiz from now on, not four.” You typed “Three of those from now on, not four.”", timeout=15000)
                # Its "reword it" opens that memory's row on the Memory page; the window, in the top layer, is put away.
                note.get_by_role("button", name="Keep my words").click()
                link = note.get_by_role("link", name="reword it")
                expect(link).to_be_visible(timeout=15000)
                link.click()
                expect(memory.locator("textarea:focus")).to_have_value("Three of those from now on, not four.", timeout=15000)
                assert page.evaluate("document.querySelector('.assistant-area-bubble').hasAttribute('data-minimised')"), "the floating chat still covers the page it opened"
                if shots:
                    page.screenshot(path=str(Path(shots) / "memory-reword-it-floating-1280.png"))
                assert not page.evaluate("window.cspViolations"), page.evaluate("window.cspViolations")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Assistant memory browser check passed.")


if __name__ == "__main__":
    main()
