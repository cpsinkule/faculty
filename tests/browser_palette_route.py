"""JEV ROUTING in the Ctrl K palette (docs/decisions/jev.md), in an isolated workspace with a scripted Jev: a typed sentence's
Best match opens its place; a question offers the assistant with the text filled in, never sent. Nothing reaches TypeSafe."""
import re
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from jev import client, routing
from test_seating import HEADERS


def evaluate(state, questions):
    """Section 508's gradebook for "gradebook" sentences; anything else is a question."""
    wanted = "Sections: MATH 140 · Fall 2026 · Section 508" if "gradebook" in state["request"] else routing.NONE
    answers = {}
    for name, question in questions.items():
        pick = wanted if wanted in question["criteria"] else ("overview" if name == "step" else routing.NONE)
        answers[name] = {"type": "choice", "choice": pick, "confidence": .9,
                         "probabilities": {option: (1.0 if option == pick else 0.0) for option in question["criteria"]}}
    return {"model": client.MODEL, "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 5}}


def main():
    with tempfile.TemporaryDirectory() as directory, patch("jev.client.available", return_value=True), \
            patch("jev.client.evaluate", side_effect=evaluate) as asked:
        app = create_app(directory)
        test = app.test_client()
        assert test.post("/api/courses", json={"name": "Business Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                               "sections": [507, 508], "multiple_sections": True}, headers=HEADERS).status_code == 201
        group = test.get("/api/state").json["course_groups"][0]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                errors, posts = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
                page.goto(address)
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                palette = page.locator("dialog.palette:not(.shortcut-sheet)")
                search = page.get_by_role("combobox", name="Go to a page, course, assignment or action")
                listbox = palette.get_by_role("listbox")

                # A sentence no word match finds: after the pause, Jev's place leads and Enter opens it.
                page.keyboard.press("Control+k")
                search.fill("take me to the gradebook of the second section")
                best = listbox.get_by_role("option", name=re.compile(r"^MATH 140 · Fall 2026 · Section 508"))
                expect(best).to_be_visible()
                expect(listbox.locator(".palette-group").first).to_have_text("Best match")
                expect(best).to_have_attribute("aria-selected", "true")
                page.keyboard.press("Enter")
                expect(page).to_have_url(re.compile(rf"#course/{re.escape(group['id'])}/section/508(\?|$)"))
                sent = asked.call_args.args
                assert sent[0] == {"request": "take me to the gradebook of the second section"}, sent[0]
                assert not any("Example" in option for option in sent[1]["place"]["criteria"])

                # A question: the assistant opens with the text in its box; nothing is sent to it.
                page.keyboard.press("Control+k")
                search.fill("why did bob lose points")
                ask = listbox.get_by_role("option", name=re.compile(r"^Ask the assistant: “why did bob lose points”"))
                expect(ask).to_be_visible()
                ask.click()
                expect(page.locator("#assistant-text")).to_have_value("why did bob lose points")
                page.wait_for_timeout(500)
                assert not [url for url in posts if "/api/assistant/" in url], posts

                # Short queries never ask Jev.
                calls = asked.call_count
                page.keyboard.press("Escape")
                page.keyboard.press("Control+k")
                search.fill("inbox")
                page.wait_for_timeout(800)
                assert asked.call_count == calls
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("browser_palette_route: ok")


if __name__ == "__main__":
    main()
