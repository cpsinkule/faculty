"""A course's question bank in a real browser (AGENTS.md "Question bank"): from the course Home's Question bank line, the outline the
lecture notes made filters the questions; a question written through the page's primary action is tagged by the model (a loopback host)
and the page shows it without a reload; the outline entry, search and difficulty filters narrow the list; a question opens with its
choices and answer; the instructor's tags and edits stand; Retire hides and Restore brings back; questions read out of a PDF are added
after review; a picture is cut from a PDF page by dragging a box, or added from a file, and removed and restored; the page fits a 1280 px window. Synthetic data only; nothing reaches a real service."""
import io
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from fake_openai import FakeHost, payload_of, prompt_name
from test_course_notes import OUTLINE, PAGES, notes_pdf
from test_question_bank import HEADERS, configure_model, extract_answer, questions_pdf, tag_answer, versions_answer


def png():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (120, 80), (40, 90, 200)).save(out, "PNG")
    return out.getvalue()


def textbook_pdf():
    """Two pages of a textbook's exercises: a section heading, problems, and a graph drawn on the first page."""
    from reportlab.pdfgen import canvas
    target = io.BytesIO()
    page = canvas.Canvas(target, pagesize=(612, 792), invariant=1)
    page.drawString(72, 740, "2.1 Review of Lines")
    page.drawString(72, 716, "1. Find the slope of the line through (1, 2) and (3, 8).")
    page.drawString(72, 692, "2. Write the inequality shown by the graph.")
    page.line(120, 520, 320, 660)
    page.rect(100, 500, 240, 180)
    page.showPage()
    page.drawString(72, 740, "3. Find the intercepts of 2x + 3y = 12.")
    page.drawString(72, 700, "Chapter 2 Review")
    page.drawString(72, 676, "1. Solve the system x + y = 5, x - y = 1.")
    page.showPage()
    page.save()
    return target.getvalue()


def textbook_answer(payload):
    """bank_extract v2 for textbook_pdf(): sections, a check and a figure box."""
    problem = lambda number, page, statement, **extra: {"section": "", "number": number, "page": page, "kind": "free_response", "statement": statement,  # noqa: E731
                                                         "parts": [], "choices": [], "correct_choice": "", "points": None, "final_answers": [], "solution": "",
                                                         "figures": [], "check": [], **extra}
    found = []
    for item in payload["pages"]:
        if item["page"] == 1:
            found += [problem("1", 1, "Find the slope of the line through $(1, 2)$ and $(3, 8)$.", section="2.1",
                              check=["The PDF prints the second point as $(3, 8)$; the answer key may use $(3, 6)$."]),
                      problem("2", 1, "Write the inequality shown by the graph.", section="2.1",
                              figures=[{"page": 1, "box": {"left": .16, "top": .14, "right": .56, "bottom": .37}, "description": "A line through two points."}])]
        if item["page"] == 2:
            found += [problem("3", 2, "Find the intercepts of $2x + 3y = 12$."), problem("1", 2, "Solve the system.", section="Chapter 2 Review")]
    return {"questions": found, "notes": [], "complete": True}


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    host = FakeHost().start()
    answers = {"bank_tag": tag_answer, "bank_extract": extract_answer, "bank_versions": versions_answer}
    host.script = lambda body, _host: answers[prompt_name(body)](payload_of(body))
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        definition = seed_course(client)["definition_id"]
        group = client.get("/api/state").json["course_groups"][0]
        bank, frq = app.extensions["question_bank"], app.extensions["frq_grading"]
        configure_model(frq, host)
        app.extensions["course_notes"].upload(definition, notes_pdf(PAGES, OUTLINE), "notes.pdf", "notes-1")
        bank.sync(definition)
        quiz = app.extensions["documents"].create_assignment(definition, "quiz-1", "Quiz 1")

        def work():
            """The worker's turn and the dispatcher, as the Inbox worker would run them."""
            bank.sync(definition)
            frq.process_cloud()

        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#course/{group['id']}/home")
                line = page.locator(".course-bank-link")
                expect(line.locator("[data-bank-status]")).to_have_text("No questions yet")
                line.get_by_role("link", name="Question bank, by chapter, section and topic").click()
                expect(page.locator("#page-title")).to_have_text("Question bank")
                expect(page.locator("#page-context")).to_have_text("MATH 140 · every term")
                outline = page.locator(".bank-outline")
                expect(outline.locator(".bank-tree-root > .bank-tree-item > .bank-node-row .bank-node-name")).to_have_text(["Chapter 1 · Matrices", "Chapter 2 · Functions"])
                expect(page.get_by_role("heading", name="No questions yet")).to_be_visible()

                # Write a question (COMPACT ROWS: adding is a secondary action, the bank has no primary button): the statement's
                # mathematics is previewed, then the model tags it.
                primary = page.locator(".page-heading-secondary").get_by_role("button", name="Add a question")
                expect(page.locator("#primary-action")).to_be_hidden()
                primary.click()
                dialog = page.locator("#dialog")
                dialog.get_by_label("Statement").fill("A store's costs are $C = 2x + 5$. Find $C$ when $x = 3$.")
                expect(dialog.locator("[data-bank-preview] .katex").first).to_be_visible()
                dialog.get_by_label("Points", exact=True).fill("4")
                dialog.get_by_label("Final answers").fill("$C = 11$")
                dialog.get_by_role("button", name="Add to the bank").click()
                expect(page.locator("#toast")).to_contain_text("The model tags it within a minute")
                cards = page.locator(".bank-card")
                expect(cards).to_have_count(1)
                expect(cards.first.locator(".bank-tags")).to_contain_text("Not tagged yet")
                work()
                # The page asks again by itself. A row says only what needs a look; its facts give the section by number (the full
                # tag as the hint) and the question's window the rest: who tagged it and its types.
                expect(cards.first.locator(".bank-tags")).to_be_empty(timeout=15000)
                expect(cards.first.locator(".bank-tag")).to_have_text("1.1")
                expect(cards.first.locator(".bank-tag")).to_have_attribute("title", re.compile("1.1 Basic Matrix Operations › Defining a Matrix"))
                expect(cards.first.locator(".bank-facts")).not_to_contain_text("Word problem")
                cards.first.locator(".bank-source").click()
                expect(dialog.locator(".bank-facts")).to_contain_text("Word problem")
                expect(dialog).to_contain_text("Tagged by the model")
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()

                # A multiple-choice question with the instructor's own tags.
                primary.click()
                dialog.get_by_label("Multiple choice").check()
                dialog.get_by_label("Statement").fill("Which matrix is square?")
                dialog.get_by_label("Choices").fill("$2 \\times 3$\n$3 \\times 3$")
                dialog.get_by_label("Correct choice").select_option("b")
                dialog.get_by_text("Tags (optional)").click()
                dialog.locator(".bank-picker label").filter(has_text="2.1 Relations and Functions").locator("input").check()
                dialog.get_by_label("Hard").check()
                dialog.get_by_role("button", name="Add to the bank").click()
                expect(page.locator("#toast")).to_contain_text("Added to the bank with your tags.")
                expect(cards).to_have_count(2)

                # The outline filters: an entry shows the questions under it and under every entry below it.
                outline.locator(".bank-node").filter(has_text="Chapter 2 · Functions").click()
                expect(cards).to_have_count(1)
                expect(cards.first).to_contain_text("Which matrix is square?")
                expect(page.locator(".bank-result-line")).to_contain_text("1 question under Chapter 2 · Functions")
                page.get_by_role("button", name="Clear filters").first.click()
                expect(cards).to_have_count(2)
                page.get_by_label("Difficulty").select_option("medium")
                expect(cards).to_have_count(1)
                page.get_by_label("Difficulty").select_option("")
                page.get_by_role("searchbox", name="Find").fill("square")
                expect(cards).to_have_count(1)
                page.get_by_role("searchbox", name="Find").fill("")

                # Open it: its choices with the correct one; change its tags; edit it.
                cards.filter(has_text="Which matrix is square?").locator(".bank-source").click()
                expect(dialog.locator(".bank-choices li[data-correct='true']")).to_contain_text("(b)")
                dialog.get_by_role("button", name="Change tags…").click()
                dialog.locator(".bank-picker label").filter(has_text="1.2 Matrix Multiplication").locator("input").check()
                dialog.get_by_role("button", name="Save tags").click()
                expect(page.locator("#toast")).to_contain_text("Tags saved.")
                square = cards.filter(has_text="Which matrix is square?")
                expect(square.locator(".bank-tag")).to_have_text("1.2, 2.1")
                square.locator(".bank-source").click()
                expect(dialog).to_contain_text("Tagged by you")
                dialog.get_by_role("button", name="Edit…").click()
                dialog.get_by_label("Statement").fill("Which of these matrices is square?")
                dialog.get_by_role("button", name="Save").click()
                expect(cards.filter(has_text="Which of these matrices is square?").locator(".bank-facts")).to_contain_text("Edited")

                # Retire hides it; Show: Retired finds it; Restore brings it back.
                cards.filter(has_text="Which of these matrices is square?").locator(".bank-source").click()
                dialog.get_by_role("button", name="Retire").click()
                expect(page.locator("#toast")).to_contain_text("The question was retired.")
                expect(cards).to_have_count(1)
                page.locator(".bank-more-filters > summary").click()  # Show is one of the folded filters
                page.locator("[data-bank-filter='status']").select_option("retired")
                expect(cards).to_have_count(1)
                cards.first.locator(".bank-source").click()
                dialog.get_by_role("button", name="Restore").click()
                expect(page.locator("#toast")).to_contain_text("back in the bank")
                page.locator("[data-bank-filter='status']").select_option("")
                expect(cards).to_have_count(2)

                # A PDF: read by the model, reviewed, the chosen questions added.
                page.locator(".page-heading-secondary").get_by_role("button", name="Read questions from a PDF").click()
                dialog.get_by_label("PDF").set_input_files({"name": "Old quiz.pdf", "mimeType": "application/pdf", "buffer": questions_pdf()})
                dialog.get_by_role("button", name="Upload and read").click()
                expect(page.locator("#toast")).to_contain_text("The model is reading the questions.")
                frq.process_cloud()
                uploads = page.locator(".bank-upload")
                expect(uploads.locator(".chip")).to_contain_text("Found 3 questions", timeout=15000)
                uploads.get_by_role("button", name="Review and add").click()
                expect(dialog.locator(".bank-found")).to_have_count(3)
                dialog.locator(".bank-found").nth(0).locator("input[name='pick']").uncheck()
                dialog.get_by_label("Assignment").select_option(label="Quiz 1")
                dialog.get_by_label("Version").select_option("A")
                dialog.get_by_role("button", name="Add 2 questions").click()
                expect(page.locator("#toast")).to_contain_text("Added 2 questions.")
                expect(cards).to_have_count(4)
                expect(page.locator(".bank-source").filter(has_text="Quiz 1 · Problem 2 · Version A")).to_have_count(1)
                work()
                expect(page.locator(".bank-card .bank-tags .chip").filter(has_text=re.compile("Not tagged|Tagging"))).to_have_count(0, timeout=15000)

                # Pictures: the reading said Problem 3 relies on a figure; cut it from its page by dragging a box, add a file, remove one.
                page.locator(".bank-source").filter(has_text="Quiz 1 · Problem 3 · Version A").click()
                expect(dialog).to_contain_text("relies on a figure")
                dialog.get_by_role("button", name="Cut from a PDF page…").click()
                expect(dialog.get_by_label("Page", exact=True)).to_have_value("2")
                expect(dialog.get_by_label("Caption")).to_have_value("A table of prices.")
                stage = dialog.locator("[data-bank-cut-stage] img")
                page.wait_for_function("() => document.querySelector('[data-bank-cut-stage] img')?.naturalWidth > 0")
                box = stage.bounding_box()
                page.mouse.move(box["x"] + box["width"] * .1, box["y"] + box["height"] * .05)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] * .7, box["y"] + box["height"] * .3, steps=5)
                page.mouse.up()
                expect(dialog.locator("[data-bank-cut-box]")).to_be_visible()
                dialog.get_by_role("button", name="Cut and add").click()
                expect(page.locator("#toast")).to_contain_text("cut from the page and added")
                figure = dialog.locator(".bank-picture")
                expect(figure).to_have_count(1)
                expect(figure).to_contain_text("Cut from Old quiz.pdf, page 2")
                page.wait_for_function("() => document.querySelector('#dialog .bank-picture img')?.naturalWidth > 0")
                dialog.get_by_role("button", name="Add a picture…").click()
                dialog.get_by_label("Picture").set_input_files({"name": "graph.png", "mimeType": "image/png", "buffer": png()})
                dialog.get_by_label("Caption").fill("A bar chart of prices")
                dialog.get_by_role("button", name="Add", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("The picture was added.")
                expect(figure).to_have_count(2)
                figure.nth(1).get_by_role("button", name="Remove").click()
                expect(page.locator("#toast")).to_contain_text("The picture was removed")
                expect(figure).to_have_count(1)
                dialog.locator(".bank-removed-pictures").get_by_role("button", name="Restore").click()
                expect(page.locator("#toast")).to_contain_text("back with its question")
                expect(figure).to_have_count(2)
                dialog.get_by_role("button", name="Close", exact=True).click()
                expect(cards.filter(has_text="break-even").locator(".bank-card-pictures img")).to_have_count(2)

                # A textbook's exercises: grouped by the section printed above them (a section ticks all of its problems), what to check
                # in a problem flagged and filtered, the graph shown as it will be cut, and the PDF read again. Nothing is added here.
                answers["bank_extract"] = textbook_answer
                page.locator(".page-heading-secondary").get_by_role("button", name="Read questions from a PDF").click()
                dialog.get_by_label("PDF").set_input_files({"name": "Textbook.pdf", "mimeType": "application/pdf", "buffer": textbook_pdf()})
                dialog.get_by_role("button", name="Upload and read").click()
                expect(page.locator("#toast")).to_contain_text("The model is reading the questions.")
                frq.process_cloud()
                book = uploads.filter(has_text="Textbook.pdf")
                expect(book.locator(".chip")).to_contain_text("Found 4 questions", timeout=15000)
                book.get_by_role("button", name="Review and add").click()
                heads = dialog.locator(".bank-found-head")
                expect(heads).to_have_count(2)
                expect(heads.nth(0)).to_contain_text("Section 2.1")
                expect(heads.nth(0)).to_contain_text("3 problems")
                expect(heads.nth(1)).to_contain_text("Chapter 2 Review")
                expect(dialog.locator(".bank-found .chip").filter(has_text="Check")).to_have_count(1)
                drawn = dialog.locator("canvas.bank-found-figure")
                for _ in range(100):  # drawn once the page picture loads (the page's CSP forbids wait_for_function's polling)
                    if drawn.evaluate("canvas => canvas.width") > 1:
                        break
                    page.wait_for_timeout(100)
                assert drawn.evaluate("canvas => canvas.width") > 1, "the figure was never drawn"
                page.screenshot(path=str(output / "question-bank-review.png"))
                add = dialog.locator("[data-bank-add-count]")
                expect(add).to_have_text("Add 4 questions")
                heads.nth(0).get_by_role("checkbox").uncheck()
                expect(add).to_have_text("Add 1 question")
                dialog.locator(".bank-found input[name='pick']").nth(0).check()
                assert heads.nth(0).get_by_role("checkbox").evaluate("box => box.indeterminate"), "a partly ticked section shows it"
                heads.nth(0).get_by_role("checkbox").check()
                expect(add).to_have_text("Add 4 questions")
                dialog.get_by_label("Only the 1 problem to check").check()
                expect(dialog.locator(".bank-found:visible")).to_have_count(1)
                expect(dialog.locator(".bank-found-section:visible")).to_have_count(1)
                dialog.get_by_role("button", name="Read again").click()
                expect(page.locator("#toast")).to_contain_text("Reading the PDF again")
                expect(book.locator(".chip")).to_contain_text("Reading", timeout=15000)
                frq.process_cloud()
                expect(book.locator(".chip")).to_contain_text("Found 4 questions", timeout=15000)

                # Version B of Problem 2, printed as Quiz 1's problem 5: the model links the versions, and one card holds both.
                version_b = client.post(f"/api/question-bank/definitions/{definition}/questions", headers=HEADERS,
                                        json={"request_id": "b5", "content": {"kind": "free_response", "statement": "Which of the matrices below is square?"}}).json
                client.post(f"/api/question-bank/questions/{version_b['id']}/printed", headers=HEADERS,
                            json={"request_id": "b5-printed", "based_on": 0, "assignment_id": quiz["id"], "exam_version": "B", "number": "5"})
                work()
                page.reload()  # added behind the page's back (through the API), so the page looks again
                problem = cards.filter(has=page.locator(".bank-version-pick"))
                expect(problem).to_have_count(1, timeout=15000)
                expect(problem.locator(".bank-version-pick")).to_have_text(["A", "B"])
                expect(cards).to_have_count(4)  # five questions, four problems
                expect(page.locator(".bank-result-line")).to_contain_text("4 problems (5 questions with their versions)")
                problem.get_by_role("button", name="B", exact=True).click()
                expect(problem.locator(".bank-source")).to_have_text("Quiz 1 · Problem 5 · Version B")
                problem.locator(".bank-source").click()
                expect(dialog.locator(".bank-version-list")).to_contain_text("Version A")
                expect(dialog).to_contain_text("Linked by the model")
                dialog.get_by_role("button", name="Unlink this version").click()
                expect(page.locator("#toast")).to_contain_text("Unlinked.")
                expect(dialog).to_contain_text("Not linked to other versions.")
                dialog.get_by_role("button", name="Close", exact=True).click()
                expect(cards).to_have_count(5)
                page.locator(".bank-more-filters > summary").click()  # folded again since the reload; it stays open across redraws
                page.get_by_label("One row per problem, its versions together").uncheck()
                expect(page.locator(".bank-more-filters")).to_have_attribute("open", "")
                expect(cards).to_have_count(5)
                page.screenshot(path=str(output / "question-bank.png"), full_page=True)

                # The course Home's line counts the bank.
                page.goto(f"{address}/#course/{group['id']}/home")
                expect(page.locator(".course-bank-link [data-bank-status]")).to_have_text("5 questions · 5 tagged")

                # The narrowest desktop window (desktop only, 2026-10-03): no sideways scrolling.
                phone = browser.new_page(viewport={"width": 1280, "height": 800})
                phone.on("pageerror", lambda error: errors.append(str(error)))
                # How students did (here a stub of the statistics read): each card drawn gets its '% earned' chip in place, without
                # drawing the list (and typesetting its math) again; the Measured filter still redraws it.
                standing = [q for q in client.get(f"/api/question-bank/definitions/{definition}").json["questions"] if not q.get("retired")]
                stats = {standing[0]["id"]: {"earned_pct": 40, "students": 12, "terms": ["Fall 2026"], "sources": ["certified grades"]}}
                bank_reads = []
                phone.on("request", lambda request: bank_reads.append(request.url) if request.url.endswith(f"/definitions/{definition}") else None)
                held = []  # the statistics answer waits until the cards drawn first are marked
                phone.route(f"**/definitions/{definition}/statistics", lambda route: held.append(route))
                phone.goto(f"{address}/#course/{group['id']}/home/questions")
                expect(phone.locator(".bank-card")).to_have_count(5)
                phone.evaluate("() => { window.firstBankCard = document.querySelector('.bank-card'); }")
                expect(phone.locator(".bank-measured")).to_have_count(0)
                for _ in range(50):
                    if held:
                        break
                    phone.wait_for_timeout(100)
                assert held, "the statistics were never asked for"
                held[0].fulfill(json={"questions": stats})
                expect(phone.locator(".bank-measured")).to_have_count(1)
                expect(phone.locator(f'.bank-card[data-question="{standing[0]["id"]}"] .bank-measured')).to_have_text("40% earned")
                assert phone.evaluate("() => window.firstBankCard === document.querySelector('.bank-card')"), "the list was drawn again for the statistics"
                assert len(bank_reads) == 1, f"the bank was read {len(bank_reads)} times for one visit"
                phone.locator('[data-bank-filter="measured"]').select_option("low")
                expect(phone.locator(".bank-card")).to_have_count(1)
                phone.locator('[data-bank-filter="measured"]').select_option("")
                expect(phone.locator(".bank-card")).to_have_count(5)
                width = phone.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                phone.screenshot(path=str(output / "question-bank-1280.png"), full_page=True)
                assert width[0] <= width[1], width

                # Add all: every question the textbook's reading found joins the bank without a review; the one with something to check
                # is found again through Show → Something to check.
                page.goto(f"{address}/#course/{group['id']}/home/questions")
                book = page.locator(".bank-upload").filter(has_text="Textbook.pdf")
                book.get_by_role("button", name="Add all").click()
                expect(dialog.locator("h2")).to_have_text("Add all 4 questions")
                expect(dialog.locator(".callout")).to_contain_text("1 question with something to check")
                page.screenshot(path=str(output / "question-bank-add-all.png"))
                dialog.get_by_role("button", name="Add all 4 questions").click()
                expect(page.locator("#toast")).to_contain_text("Added 4 questions and 1 picture.")
                expect(page.locator(".bank-card")).to_have_count(9)
                expect(book.get_by_role("button", name="Add all")).to_have_count(0)
                expect(book).to_contain_text("4 added from it · all in the bank")
                # What a reading asked to check is counted beside the list, one press from Show → Something to check.
                expect(page.locator("[data-bank-show-check]")).to_have_text("1 question to check")
                page.locator("[data-bank-show-check]").click()
                expect(page.locator("[data-bank-filter='status']")).to_have_value("check")
                expect(page.locator("[data-bank-show-check]")).to_have_count(0)
                expect(page.locator(".bank-card")).to_have_count(1)
                expect(page.locator(".bank-card .chip").filter(has_text="Check")).to_have_count(1)
                expect(page.locator(".bank-card .bank-source")).to_contain_text("Section 2.1")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            host.stop()
    print("Question bank browser check passed.")


if __name__ == "__main__":
    main()
