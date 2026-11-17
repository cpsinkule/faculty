"""The Faculty assistant's drawer in a real browser (AGENTS.md "Faculty assistant"), on an isolated synthetic catalog.

The model is a scripted stand-in for a hosted connection (`assistant.engines.request_stream` answers from a list), so no provider
is reached. It checks: the top bar opens the drawer (and Ctrl+I closes it), a message runs tools shown as quiet lines, the answer
is escaped Markdown with a Faculty link, a change outside the chosen categories is an Approve card that nothing submits until the
Approve click, the Changes tab logs it, a fetched page shows as a picture that loads from its route and a PDF as a link, no CSP
violation or page error happens, and the drawer fits a laptop's window.

2026-10-09 (the drawer and the floating window share one conversation, static/assistant-chat.js): starters and the "/" list only
fill the box; a running turn lists its steps and counts its time; one message typed meanwhile waits and is sent once (Stop gives
it back); an answer's table is copied as tab-separated text; its math is drawn and survives the next poll; Ask again and Edit;
an answer that arrives with the drawer closed marks the launcher (never in orange); a click fades the drawer in and the keyboard
does not; the left edge drags the width, which is remembered, with all five tabs in view.
"""
import json
import re
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from flask import Response
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from test_storage import HEADERS
from app import create_app


def page_png(number):
    """A synthetic scanned page: a printed problem and a handwritten-looking answer."""
    import io
    image = Image.new("L", (850, 1100), 250)
    draw = ImageDraw.Draw(image)
    draw.text((60, 50), f"MATH 140 · Exam 1 · page {number}", fill=40)
    for row in range(2):
        top = 140 + row * 470
        draw.text((60, top), f"Problem {number * 2 - 1 + row}. Solve the system of equations.", fill=30)
        draw.line((90, top + 120, 520, top + 160), fill=70, width=4)
        draw.line((90, top + 220, 600, top + 230), fill=70, width=4)
        draw.rectangle((60, top + 20, 790, top + 440), outline=180)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def scripted(seen=None, gates=None):
    write = {"method": "POST", "path": "/api/tools", "summary": "Add a Canvas link to the dashboard",
             "body": {"name": "Canvas", "url": "https://canvas.tamu.edu"}, "page": "#tools"}
    answers = [
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t1", "function": {"name": "courses", "arguments": "{}"}}]}}], "usage": {}},
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t2", "function": {"name": "faculty_write", "arguments": json.dumps(write)}}]}}], "usage": {}},
        {"choices": [{"message": {"content": "You teach **MATH 140**. <script>bad()</script> See [the calendar](#calendar).\n\n- The Canvas link waits for your approval."}}],
         "usage": {"cost": 0.001}},
        {"choices": [{"message": {"content": "The approved Canvas link is now available."}}], "usage": {}},
        {"choices": [{"message": {"content": "Continued fine."}}], "usage": {}},
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t3", "function": {"name": "fetch_files", "arguments": json.dumps(
            {"paths": ["/api/test-files/page-1.png", "/api/test-files/page-2.png", "/api/test-files/paper.pdf"], "caption": "Alice Example · Exam 1"})}}]}}], "usage": {}},
        {"choices": [{"message": {"content": "Here are pages 1 and 2 of Alice's Exam 1, and the reviewed paper as a PDF."}}], "usage": {}},
        {"choices": [{"message": {"content": "I read the attached syllabus."}}], "usage": {}},
        # 8-13: the shared conversation's checks (a step, then an answer held by its gate; the queued message; Stop; ANSWER READY).
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t8", "function": {"name": "courses", "arguments": "{}"}}]}}], "usage": {}},
        {"choices": [{"message": {"content": "You have **1** course.\n\n| Course | Sections |\n|---|---|\n| MATH 140 | 2 |\n\nIts sections satisfy $n = 2$."}}], "usage": {}},
        {"choices": [{"message": {"content": "Queued answer."}}], "usage": {}},
        {"choices": [{"message": {"content": "Slow answer."}}], "usage": {}},
        {"choices": [{"message": {"content": "Ready answer."}}], "usage": {}},
        {"choices": [{"message": {"content": "Asked again."}}], "usage": {}},
    ]
    state = {"index": 0}

    def answer(url, *, headers, timeout, body, usage_option):
        if seen is not None:
            seen.append(body)
        index = state["index"]
        found = answers[min(index, len(answers) - 1)]
        state["index"] += 1
        if gates and index in gates:
            gates[index].wait(30)
        return found
    return answer


def shared_conversation(page, panel, seen, gates):
    """The conversation both surfaces share (static/assistant-chat.js), driven in the drawer."""
    box = panel.locator("#assistant-text")
    send = panel.get_by_role("button", name="Send", exact=True)
    own = panel.locator('.assistant-msg[data-from="you"]')
    answers = panel.locator('.assistant-msg[data-from="assistant"]')
    panel.get_by_role("button", name="New conversation").click()
    # STARTERS and the "/" list only write into the box: nothing reaches the model until Send.
    asked = len(seen)
    starter = panel.locator("[data-chat-starter]").first
    expect(starter).to_be_visible()
    words = starter.inner_text()
    starter.click()
    expect(box).to_have_value(words)
    box.fill("/")
    picker = panel.locator(".assistant-picker")
    expect(picker.get_by_role("option").first).to_be_visible()
    # Escape closes the list, not the drawer.
    box.press("Escape")
    expect(picker).to_be_hidden()
    expect(panel).to_be_visible()
    box.fill("/certify")
    expect(picker.get_by_role("option")).to_have_count(1)
    box.press("Enter")
    expect(box).to_have_value("Which grades are ready to certify?")
    expect(picker).to_be_hidden()
    assert len(seen) == asked and own.count() == 0
    # THE BOX: a file dropped on the drawer, or pasted without text, is attached as the paperclip attaches it.
    attached = panel.locator("[data-assistant-attached]")
    page.evaluate("""() => { const data = new DataTransfer(); data.items.add(new File(['dropped'], 'Dropped.txt', {type: 'text/plain'}));
      document.getElementById('assistant-panel').dispatchEvent(new DragEvent('drop', {dataTransfer: data, bubbles: true, cancelable: true})); }""")
    expect(attached.get_by_role("button", name="Remove Dropped.txt")).to_be_visible(timeout=15000)
    attached.get_by_role("button", name="Remove Dropped.txt").click()
    expect(attached).to_be_hidden()
    page.evaluate("""() => { const data = new DataTransfer(); data.items.add(new File(['pasted'], 'Pasted.txt', {type: 'text/plain'}));
      document.getElementById('assistant-text').dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true})); }""")
    expect(attached.get_by_role("button", name="Remove Pasted.txt")).to_be_visible(timeout=15000)
    attached.get_by_role("button", name="Remove Pasted.txt").click()
    expect(attached).to_be_hidden()
    # A paste that carries text (a spreadsheet's cells come with a picture) stays text: nothing is attached.
    page.evaluate("""() => { const data = new DataTransfer(); data.setData('text/plain', '7\t8'); data.items.add(new File(['cells'], 'Cells.txt', {type: 'text/plain'}));
      document.getElementById('assistant-text').dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true})); }""")
    page.wait_for_timeout(400)
    expect(attached).to_be_hidden()
    # LIVE STEPS: while the turn runs, its finished steps are listed and the state line counts the time.
    box.fill("Count my courses.")
    box.press("Enter")
    expect(panel.locator(".assistant-live li")).to_have_text(["Read courses"], timeout=15000)
    expect(panel.locator("[data-assistant-state]")).to_have_text(re.compile(r"^Working · \d+:\d\d$"))
    # QUEUE ONE: a message typed meanwhile waits (Send stays on for it, then off while one waits) and is sent once.
    expect(send).to_be_enabled()
    box.fill("And then?")
    box.press("Enter")
    waiting = panel.locator(".assistant-queued")
    expect(waiting).to_contain_text("And then?")
    expect(box).to_have_value("")
    expect(send).to_be_disabled()
    asked = len(seen)
    gates[9].set()
    expect(answers.last).to_contain_text("Queued answer.", timeout=20000)
    expect(waiting).to_be_hidden()
    assert own.locator(":scope > p").all_inner_texts() == ["Count my courses.", "And then?"] and own.count() == 2
    assert len(seen) == asked + 1, "the queued message reached the model once"
    # The fold says how long the turn took; the answer's math is drawn and its number column is marked.
    expect(panel.locator(".assistant-steps > summary .disclosure-hint").first).to_have_text(re.compile(r"^1 step · \d+ (s|min)"))
    first = answers.first
    expect(first.locator(".katex").first).to_be_visible(timeout=15000)
    expect(first.locator("td[data-num]")).to_have_text("2")
    first.evaluate("node => { node.dataset.testKept = 'yes'; }")
    # Copy table puts tab-separated text on the clipboard; Copy the answer's own words. Ask again is on the last turn only.
    panel.locator("[data-chat-copy-table]").click()
    page.wait_for_function("async () => (await navigator.clipboard.readText()) === 'Course\\tSections\\nMATH 140\\t2'")
    panel.locator("[data-chat-copy]").last.click()
    page.wait_for_function("async () => (await navigator.clipboard.readText()) === 'Queued answer.'")
    expect(panel.locator("[data-chat-again]")).to_have_count(1)
    # Edit puts the last own message back in the box (sending it would add a turn).
    panel.locator("[data-chat-edit]").click()
    expect(box).to_have_value("And then?")
    box.fill("")
    # Stop gives a waiting message back to the box; it is never sent.
    box.fill("Slow one")
    box.press("Enter")
    stop = panel.get_by_role("button", name="Stop", exact=True)
    expect(stop).to_be_visible()
    box.fill("Later")
    box.press("Enter")
    expect(waiting).to_contain_text("Later")
    asked = len(seen)
    stop.click()
    expect(waiting).to_be_hidden()
    expect(box).to_have_value("Later")
    gates[11].set()
    expect(stop).to_be_hidden(timeout=20000)
    assert len(seen) == asked and "Later" not in " ".join(own.all_inner_texts())
    box.fill("")
    # A finished answer is left untouched by later polls: the node and its drawn math are the same ones.
    assert first.get_attribute("data-test-kept") == "yes" and first.locator(".katex").count() > 0
    # ANSWER READY: an answer that comes in while the drawer is closed marks the launcher, in slate, until the drawer opens.
    box.fill("Ready check")
    box.press("Enter")
    expect(stop).to_be_visible()
    panel.get_by_role("button", name="Close the assistant", exact=True).click()
    expect(panel).to_be_hidden()
    launcher = page.locator(".assistant-launch")
    ready = launcher.locator("[data-assistant-ready]")
    expect(ready).to_be_hidden()
    gates[12].set()
    expect(ready).to_be_visible(timeout=20000)
    expect(launcher).to_have_attribute("aria-label", re.compile("a new answer is ready"))
    assert ready.evaluate("node => getComputedStyle(node).backgroundColor") == "rgb(62, 90, 102)"  # --tone-info, never the orange
    launcher.click()
    # A click fades the drawer in.
    expect(panel).to_have_attribute("data-entered", "click", timeout=900)
    expect(panel).to_be_visible()
    expect(ready).to_be_hidden()
    expect(answers.last).to_contain_text("Ready answer.", timeout=15000)
    # Ask again sends the same words as a new message.
    count = own.count()
    panel.locator("[data-chat-again]").click()
    expect(own).to_have_count(count + 1, timeout=15000)
    assert own.last.locator(":scope > p").inner_text().startswith("Ready check")
    expect(answers.last).to_contain_text("Asked again.", timeout=15000)
    panel.locator("[data-assistant-chat]").evaluate("chat => { chat.scrollTop = 0; }")
    expect(panel.locator("[data-chat-latest]")).to_be_visible()
    panel.locator("[data-chat-latest]").click()
    expect(panel.locator("[data-chat-latest]")).to_be_hidden()


def drawer_width(page, panel):
    """RESIZE: the drawer's left edge drags its width (never under 480 px), which is remembered; the five tabs stay in view."""
    width = lambda: page.evaluate("document.getElementById('assistant-panel').getBoundingClientRect().width")  # noqa: E731
    edge = panel.locator("[data-assistant-resize]").bounding_box()
    page.mouse.move(edge["x"] + 3, 300)
    page.mouse.down()
    page.mouse.move(edge["x"] - 217, 300, steps=5)
    page.mouse.up()
    assert 690 <= width() <= 710, width()
    assert page.evaluate("localStorage.getItem('faculty:assistant:width')") == str(round(width()))
    tabs = panel.locator("[data-assistant-tab]")
    expect(tabs).to_have_count(5)
    right = panel.bounding_box()["x"] + panel.bounding_box()["width"]
    for index in range(5):
        expect(tabs.nth(index)).to_be_visible()
        box = tabs.nth(index).bounding_box()
        assert box["x"] + box["width"] <= right, (index, box)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # It cannot be dragged under 480 px, and the arrow keys move it too.
    edge = panel.locator("[data-assistant-resize]").bounding_box()
    page.mouse.move(edge["x"] + 3, 300)
    page.mouse.down()
    page.mouse.move(page.viewport_size["width"] - 100, 300, steps=5)
    page.mouse.up()
    assert 478 <= width() <= 482, width()
    panel.locator("[data-assistant-resize]").focus()
    page.keyboard.press("ArrowLeft")
    assert 502 <= width() <= 506, width()
    # Remembered across a reload.
    page.reload()
    page.keyboard.press("Control+i")
    expect(panel).to_be_visible()
    assert 502 <= width() <= 506, width()
    panel.locator("[data-assistant-resize]").focus()
    page.keyboard.press("Home")
    assert 478 <= width() <= 482, width()


def main():
    temp = tempfile.TemporaryDirectory()
    os.environ["EXAMPLE_API_KEY"] = "synthetic"
    app = create_app(os.path.join(temp.name, "data"))
    # File routes as Faculty serves them (a scanned page, a reviewed paper's PDF), for fetch_files.
    app.add_url_rule("/api/test-files/page-<int:number>.png", "test_files.page", lambda number: Response(page_png(number), mimetype="image/png"))
    app.add_url_rule("/api/test-files/paper.pdf", "test_files.pdf", lambda: Response((REPO / "document_identity" / "fixtures" / "letter.pdf").read_bytes(),
                     mimetype="application/pdf", headers={"Content-Disposition": 'attachment; filename="reviewed-paper.pdf"'}))
    client = app.test_client()
    assert client.post("/api/courses", json={"code": "MATH 140", "name": "Business Math", "term": "Fall", "year": 2026, "sections": [507, 508], "multiple_sections": True,
                                            "is_current": True}, headers=HEADERS).status_code == 201
    current = client.get("/api/grading/settings").json
    settings = current["settings"]
    settings["endpoints"].append({"name": "helper", "kind": "hosted", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY",
                                  "model": "some/model", "modality": "text", "lineage": "", "aggregator": False})
    settings["assistant_model"] = "helper"
    saved = client.put("/api/grading/settings", json={"settings": settings, "version": current["version"]}, headers=HEADERS)
    assert saved.status_code == 200, saved.json
    server = create_server(app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{server.effective_port}"
    errors = []
    try:
        seen = []
        gates = {index: threading.Event() for index in (9, 11, 12)}
        with patch("assistant.engines.request_stream", side_effect=scripted(seen, gates)), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1400, "height": 900}, permissions=["clipboard-read", "clipboard-write"]).new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
            page.add_init_script("window.cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.cspViolations.push(e.violatedDirective));")
            page.goto(base + "/#overview")
            launcher = page.locator(".assistant-launch")
            expect(launcher).to_be_visible()
            launcher.click()
            panel = page.locator("#assistant-panel")
            expect(panel).to_be_visible()
            expect(panel.locator("[data-assistant-model]")).to_have_text("helper")
            # A long model name is cut with an ellipsis (its title names it whole) and New conversation keeps one line.
            expect(panel.locator("[data-assistant-model]")).to_have_attribute("title", re.compile(r"^helper · "))
            assert panel.locator("[data-assistant-model]").evaluate("node => getComputedStyle(node).textOverflow") == "ellipsis"
            fresh = panel.get_by_role("button", name="New conversation")
            assert fresh.evaluate("node => getComputedStyle(node).whiteSpace") == "nowrap"
            panel.locator("#assistant-text").fill("What do I teach? Add a Canvas link.")
            panel.locator("#assistant-text").press("Enter")
            answer = panel.locator('.assistant-msg[data-from="assistant"]')
            expect(answer).to_contain_text("You teach MATH 140.", timeout=15000)
            assert answer.locator("script").count() == 0
            expect(answer).to_contain_text("<script>bad()</script>")
            expect(answer.locator('a[href="#calendar"]')).to_have_count(1)
            # QUIET STEPS: the read sits in the answer's closed "How it got this", not between the question and the answer.
            steps = panel.locator(".assistant-steps").first
            expect(steps.locator(":scope > summary")).to_contain_text("How it got this", timeout=15000)
            expect(steps.locator(".assistant-tool").first).to_be_hidden()
            steps.locator(":scope > summary").click()
            expect(steps.locator(".assistant-tool").first).to_contain_text("Read courses")
            card = panel.locator(".assistant-card").first
            expect(card).to_contain_text("Add a Canvas link to the dashboard")
            expect(card.locator(".chip")).to_have_text("Waits for you")
            # One primary per card (DESIGN-LANGUAGE): its Approve is the ink block, Decline stays a text action.
            shadow = card.get_by_role("button", name="Approve").evaluate("node => getComputedStyle(node).boxShadow")
            assert "inset" in shadow and "rgb(16, 16, 16)" in shadow, shadow  # the ink, #101010 (DESIGN-LANGUAGE §2)
            page.screenshot(path="assistant-card.png")
            expect(page.locator("[data-assistant-badge]")).to_have_text("1")
            assert "Canvas" not in [t["name"] for t in client.get("/api/state").json["tools"]]
            card.get_by_role("button", name="Approve").click()
            expect(page.locator(".toast, [role=status]").filter(has_text="Approved and done.").first).to_be_visible(timeout=10000)
            assert "Canvas" in [t["name"] for t in client.get("/api/state").json["tools"]]
            expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("approved Canvas link", timeout=15000)
            # Faculty forgets the conversation (as after a restart): the window keeps it, and the next message continues it.
            conversation_id = page.evaluate("sessionStorage.getItem('faculty:assistant:conversation')")
            assert client.post(f"/api/assistant/conversations/{conversation_id}/close", json={}, headers=HEADERS).status_code == 200
            panel.locator("#assistant-text").fill("And the next thing?")
            panel.locator("#assistant-text").press("Enter")
            expect(panel.locator(".assistant-tool").filter(has_text="Continued in a new session")).to_have_count(1, timeout=15000)
            expect(panel.locator('.assistant-msg[data-from="you"]')).to_have_count(2)
            expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("Continued fine", timeout=15000)
            assert page.evaluate("sessionStorage.getItem('faculty:assistant:conversation')") != conversation_id
            # A fetched page shows as a picture loaded from its own route, opening full size; the PDF is a link.
            panel.locator("#assistant-text").fill("Show me Alice's Exam 1.")
            panel.locator("#assistant-text").press("Enter")
            pictures = panel.locator(".assistant-shown-picture img")
            expect(pictures).to_have_count(2, timeout=15000)
            expect(pictures.first).to_have_attribute("src", "/api/test-files/page-1.png")
            page.wait_for_function("[...document.querySelectorAll('.assistant-shown-picture img')].every(i => i.complete && i.naturalWidth > 0)")
            expect(panel.locator(".assistant-shown-caption")).to_have_text("Alice Example · Exam 1")
            expect(panel.locator('.assistant-shown-files a[href="/api/test-files/paper.pdf"]')).to_have_text("reviewed-paper.pdf")
            expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("Here are pages 1 and 2", timeout=15000)
            # ATTACH IN CHAT (2026-10-09): the paperclip uploads a file at once; it is listed with Remove, goes with the message (the
            # model is told its route) and the sent message shows it as a link to that route.
            panel.locator("[data-assistant-attach-input]").set_input_files({"name": "Syllabus.txt", "mimeType": "text/plain", "buffer": b"MATH 140 syllabus"})
            attached = panel.locator("[data-assistant-attached]")
            expect(attached).to_contain_text("Syllabus.txt", timeout=15000)
            expect(attached.get_by_role("button", name="Remove Syllabus.txt")).to_be_visible(timeout=15000)
            panel.locator("#assistant-text").fill("What does the attached syllabus say?")
            panel.locator("#assistant-text").press("Enter")
            expect(attached).to_be_hidden()
            mine = panel.locator('.assistant-msg[data-from="you"]').last
            expect(mine.locator('a[href^="/api/assistant/uploads/"]')).to_have_text("Syllabus.txt", timeout=15000)
            expect(panel.locator('.assistant-msg[data-from="assistant"]').last).to_contain_text("I read the attached syllabus.", timeout=15000)
            assert "/api/assistant/uploads/" in json.dumps(seen[-1]), "the model is told the attached file's route"
            # TOO LARGE FOR AN EMAIL (2026-11-12): files one email cannot carry together say so where they wait.
            panel.locator("[data-assistant-attach-input]").set_input_files([{"name": f"Chapter {n} notes.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n" + b"0" * 13_000_000} for n in (5, 6)])
            expect(attached).to_contain_text("Together these are too large for one email (25 MB at most).", timeout=30000)
            expect(attached.get_by_role("button", name="Remove Chapter 6 notes.pdf")).to_be_visible(timeout=30000)
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "the note fits the drawer"
            if os.environ.get("ASSISTANT_SCREENSHOT"):
                panel.locator("[data-assistant-chat]").evaluate("chat => { chat.scrollTop = chat.scrollHeight; }")
                panel.screenshot(path=os.environ["ASSISTANT_SCREENSHOT"])
            attached.get_by_role("button", name="Remove Chapter 6 notes.pdf").click()
            expect(attached).not_to_contain_text("Together")
            attached.get_by_role("button", name="Remove Chapter 5 notes.pdf").click()
            expect(attached).to_be_hidden()
            shared_conversation(page, panel, seen, gates)
            panel.get_by_role("tab", name="Changes").click()
            expect(panel.locator("[data-assistant-changes] .assistant-card").first).to_contain_text("Done")
            expect(page.locator("[data-assistant-badge]")).to_be_hidden()
            panel.locator("#assistant-text").focus()
            page.keyboard.press("Escape")
            expect(panel).to_be_hidden()
            page.keyboard.press("Control+i")
            expect(panel).to_be_visible()
            # The keyboard opens the drawer at once; only a click fades it in (FRONTEND "Press and open motion").
            assert panel.get_attribute("data-entered") is None
            drawer_width(page, panel)
            assert page.evaluate("window.cspViolations") == [], page.evaluate("window.cspViolations")
            # A laptop window (desktop only, DESIGN-LANGUAGE): the drawer lies over the page at its own width; nothing scrolls sideways.
            laptop = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            laptop.goto(base + "/#overview")
            laptop.locator(".assistant-launch").click()
            expect(laptop.locator("#assistant-panel")).to_be_visible()
            assert laptop.evaluate("document.documentElement.scrollWidth <= innerWidth")
            width = laptop.evaluate("document.getElementById('assistant-panel').getBoundingClientRect().width")
            assert 478 <= width <= 482, width
            browser.close()
        assert not errors, errors
        print("assistant drawer ok")
    finally:
        for gate in gates.values():
            gate.set()
        server.close()
        temp.cleanup()


if __name__ == "__main__":
    main()
