"""Compact review, real math DOM and nested crop zoom, with retained synthetic work."""
import copy
import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
from rosters import connect

fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
aid = fixture.plan["id"]
question = fixture.plan["questions"][0]
descriptions = [r"Correct direction, but gives $$\dfrac{148}{11}$$ instead of $$\dfrac{168}{11}$$ units.",
                r"Shows the change as $\Delta x = -\frac{168}{11}$.",
                r"Uses $m=\frac{11}{8}$ consistently.", "Explains why the value decreases.",
                r"Substitutes into $y=mx+b$.", "Checks the units of the answer.",
                r"Retains exact values: $\sqrt{4}=2$.", "No answer or completely incorrect."]
plan = fixture.frq.import_rubric(aid, fixture.qid, {"version": question["version"], "source_text": "Synthetic rubric",
    "rules": {"items": [{"id": f"R{i+1}", "points": -1, "description": description} for i, description in enumerate(descriptions)]}})
question = plan["questions"][0]
fixture.frq.approve_rubric(aid, fixture.qid, {"version": question["version"], "rubric_id": question["rubric_id"], "sha256": question["rubric"]["sha256"]})
old_grade, old_verify, old_read = fixture.fake.grade, fixture.fake.verify, fixture.fake.recognize
transcript = {"plain_text": "3x - 8 = 10\nx = 148/11", "latex": r"3x-8=10\\x=\frac{148}{11}", "confidence": .97, "ambiguities": [],
    "lines": [{"id": "line-1", "elements": [{"type": "math", "text": "3x - 8 = 10", "latex": "3x-8=10"}]},
              {"id": "line-2", "elements": [{"type": "math", "text": "x = 148/11", "latex": r"x=\frac{148}{11}"}]}]}
# A provider may label a line as text while supplying its LaTeX spelling. Include the forms that leaked raw commands.
for number, (kind, text, latex) in enumerate([
    ("text", "G-H · J", r"G-H \cdot J"),
    ("text", "Not possible because the dimensions", r"\text{Not possible because the dimensions}"),
    ("text", "do not match so you cannot multiply", r"\text{do not match so you cannot multiply}"),
    ("math", "2 2 2 5 5 2", r"2\ 2\ 2\ 5\ 5\ 2"),
    ("text", "2 x 2 matrix", r"2 \times 2 \text{ matrix}"),
], start=3):
    transcript["lines"].append({"id": f"line-{number}", "elements": [{"type": kind, "text": text, "latex": latex}]})
    transcript["plain_text"] += "\n" + text
def read(image):
    result = old_read(image)
    result["provider"] = "mathpix"
    result["transcript"] = copy.deepcopy(transcript)
    return result
def verify(payload, images):
    result = old_verify(payload, images)
    result["output"]["transcript"] = copy.deepcopy(transcript)
    return result
def grade(payload, images):
    result = old_grade(payload, images)
    first = result["output"]["applications"][0]
    result["output"]["applications"] = [{**copy.deepcopy(first), "rubric_item_id": f"R{i+1}", "applied": i == 0,
        "reason": r"The student writes $x=\frac{148}{11}$; compare this with the retained work. " * 5} for i in range(8)]
    return result
fixture.fake.recognize, fixture.fake.verify, fixture.fake.grade = read, verify, grade
with connect(fixture.path) as db:
    second_student = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? LIMIT 1", (fixture.section["id"], fixture.enrollment)).fetchone()[0]
fixture.reviewed_source(batch=fixture.ready_batch(enrollment_id=second_student), changed="second-response")
job, _ = fixture.queue()
value = fixture.finish(job)
original = copy.deepcopy(value["result"]["transcript"])
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
output = Path("test-results")
output.mkdir(exist_ok=True)
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad, external = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("response", lambda response: bad.append(response.url) if response.status >= 400 else None)
        page.on("request", lambda request: external.append(request.url) if not request.url.startswith(address) else None)
        page.goto(f"{address}/#grading/{aid}?tab=review&browse=all")
        expect(page.get_by_label("Response", exact=True)).to_be_visible()
        expect(page.get_by_label("Response", exact=True).locator("option")).to_have_count(2)
        page.get_by_label("Response", exact=True).select_option(index=1)
        expect(page.locator(".grading-response-heading")).to_contain_text("Response 2")
        page.get_by_role("button", name="← Previous", exact=True).click()
        expect(page.locator(".grading-response-heading")).to_contain_text("Response 1")
        page.get_by_label("Response", exact=True).select_option(value=value["id"])
        expect(page.locator(f"#review-{value['id']}")).to_be_visible()
        expect(page.locator(".grading-rubric-choice")).to_have_count(8)
        expect(page.locator(".grading-item-reason[open]")).to_have_count(0)
        expect(page.locator(".grading-rubric-choice .katex").first).to_be_visible()
        expect(page.locator("[data-transcript-preview] .katex")).to_have_count(7)
        expect(page.locator("[data-transcript-preview] mfrac")).to_have_count(1)
        expect(page.locator("[data-transcript-preview] .grading-math-literal")).to_have_count(0)
        visible_math = [" ".join(text.split()) for text in page.locator("[data-transcript-preview] .katex-html").all_text_contents()]
        assert all("\\" not in text for text in visible_math), visible_math
        assert "Not possible because the dimensions" in visible_math[3], visible_math
        assert "matrix" in visible_math[6], visible_math
        assert page.locator(".grading-rubric-items").bounding_box()["height"] < 660
        expect(page.locator("[data-transcript-editor]")).not_to_have_attribute("open", "")
        crop = page.get_by_role("button", name="Enlarge original response crop 1", exact=True)
        reader = crop.locator("xpath=ancestor::*[contains(@class, 'grading-inline-reader')]")
        expect(reader).to_be_visible()
        reader.get_by_role("button", name="100%", exact=True).click()
        reader.get_by_role("button", name="Zoom in", exact=True).click()
        expect(reader.locator("output")).to_have_text("125%")
        assert crop.locator("img").bounding_box()["width"] == 1000, crop.locator("img").evaluate("img => ({width: img.clientWidth, natural: img.naturalWidth, frame: img.closest('.grading-image').style.width})")
        expect(page.locator(".grading-rubric-items")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "review-inline-zoom.png"))
        reader.get_by_role("button", name="Full screen", exact=True).click()
        zoom = page.get_by_role("dialog", name="Enlarged response crop", exact=True)
        expect(zoom).to_be_visible()
        zoom.get_by_role("button", name="Actual pixels").click()
        expect(zoom.locator("output")).to_have_text("100%")
        zoom.get_by_role("button", name="Zoom in").click()
        expect(zoom.locator("output")).to_have_text("125%")
        assert zoom.locator("img").bounding_box()["width"] == 1000
        page.screenshot(path=str(output / "review-crop-zoom.png"))
        page.keyboard.press("Escape")
        expect(zoom).to_have_count(0)
        expect(crop).to_be_focused()
        # A click on the crop itself pops it out too, never zooming in place (requirement, 2026-10-17).
        crop.click()
        expect(zoom).to_be_visible()
        expect(reader.locator("output")).to_have_text("125%")
        page.keyboard.press("Escape")
        expect(zoom).to_have_count(0)
        page.locator(".grading-item-reason summary").first.click()
        expect(page.locator(".grading-item-reason").first.locator(".katex").first).to_be_visible()
        expect(page.locator(".grading-item-reason").first.locator(".grading-model-label")).to_have_attribute("data-grading-role", "primary")
        expect(page.locator(".grading-item-reason").first.locator(".grading-model-comment")).to_have_attribute("data-grading-role", "primary")
        page.locator('[data-evidence-item="R1"]').click()
        expect(page.locator(".grading-highlight")).to_be_visible()
        page.locator(".grading-item-reason summary").first.click()
        page.locator(".grading-review-audit summary").first.click()
        expect(page.locator(".grading-review-audit .katex").first).to_be_visible()
        page.locator(".grading-review-audit summary").first.click()
        # The long rubric scrolls without moving the paper, score, or decision controls.
        page.set_viewport_size({"width": 1500, "height": 900})
        page.locator(".grading-review-desk").evaluate("el => el.scrollIntoView({block: 'start'})")
        page.locator(".grading-evidence").evaluate("el => el.scrollTop = 0")
        page.locator(".grading-item-reason").evaluate_all("nodes => nodes.forEach(el => el.open = true)")
        pane = page.locator(".grading-review-scroll")
        assert pane.evaluate("el => el.scrollHeight > el.clientHeight")
        paper_top = crop.bounding_box()["y"]
        page_top = page.evaluate("scrollY")
        pane.evaluate("el => el.scrollTop = el.scrollHeight")
        assert abs(crop.bounding_box()["y"] - paper_top) < 1
        assert page.evaluate("scrollY") == page_top
        decision = page.locator(".grading-review-decision").bounding_box()
        assert 0 <= decision["y"] and decision["y"] + decision["height"] <= 900
        expect(page.locator("[data-score]")).to_be_in_viewport()
        expect(page.get_by_role("button", name="Approve grade", exact=True)).to_be_in_viewport()
        # Keyboard users can scroll the focused rubric pane as well.
        pane.focus()
        pane.evaluate("el => el.scrollTop = el.scrollHeight")
        before = pane.evaluate("el => el.scrollTop")
        page.keyboard.press("Home")
        for _ in range(20):
            if pane.evaluate("el => el.scrollTop") < before:
                break
            page.wait_for_timeout(50)
        assert pane.evaluate("el => el.scrollTop") < before
        page.locator(".grading-item-reason").evaluate_all("nodes => nodes.forEach(el => el.open = false)")
        pane.evaluate("el => el.scrollTop = 0")
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): the desk at two desktop widths, then a short window, where
        # the paper sits over the rubric and the rubric scrolls with the page (the side-by-side desk needs 650px of height).
        for width, height in ((1500, 900), (1280, 900), (1500, 600)):
            page.set_viewport_size({"width": width, "height": height})
            page.locator(".grading-review-desk").evaluate("el => el.scrollIntoView({block: 'start'})")
            page.screenshot(path=str(output / f"review-compact-{width}x{height}.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, height)
            assert pane.evaluate("el => getComputedStyle(el).overflowY") == ("visible" if height < 650 else "auto"), (width, height)
        page.set_viewport_size({"width": 1500, "height": 900})
        # Bad math and untrusted markup stay literal; no links, scripts or network requests.
        page.evaluate(r"""async () => {
          const prefix = document.querySelector('script[src$="/app.js"]').src.replace(/app\.js$/, '');
          const {renderMath} = await import(prefix + 'grading-math.js');
          const box = document.createElement('div'); box.id = 'math-safety';
          for (const text of [String.raw`$\frac{1}{$`, '<img src=x onerror=alert(1)>', String.raw`$\href{https://example.invalid}{x}$`, String.raw`$\htmlClass{bad}{x}$`]) {
            const node = document.createElement('span'); node.dataset.math = ''; node.textContent = text; box.append(node);
          }
          document.body.append(box); renderMath(box);
        }""")
        expect(page.locator("#math-safety .grading-math-literal").first).to_be_visible()
        expect(page.locator("#math-safety img, #math-safety a, #math-safety .bad")).to_have_count(0)
        page.locator("#math-safety").evaluate("node => node.remove()")
        # A question's printed layout (FRONTEND.md "grading-math.js", 2026-10-05): bold, answer blanks at their printed width (set
        # through CSSOM under the CSP), a centred tableau and a ruled answer grid, with no LaTeX source left on screen; markup in the
        # source stays text.
        page.evaluate(r"""async () => {
          const prefix = document.querySelector('script[src$="/app.js"]').src.replace(/app\.js$/, '');
          const {renderMath} = await import(prefix + 'grading-math.js');
          const box = document.createElement('div'); box.id = 'math-layout'; box.className = 'grading-panel';
          const node = document.createElement('span'); node.className = 'grading-math'; node.dataset.math = '';
          node.textContent = String.raw`Explain why it is final. <img src=x>
\[\begin{array}{cc|c} x & y & \text{constant}\\ 1 & 0 & 4\\ \hline 0 & 1 & 20 \end{array}\]
\textbf{This is the final tableau because:} \underline{\hspace{8.5cm}}\\[7mm]
$x=$\underline{\hspace{1.1cm}}\hfill $y=$\underline{\hspace{1.1cm}} costs \$40
\begin{center}\begin{tabular}{|*{2}{>{\centering\arraybackslash}p{1.2cm}|}}\hline a & b\\ \hline &\\ \hline\end{tabular}\end{center}`;
          box.append(node); document.body.append(box); renderMath(box);
        }""")
        layout = page.locator("#math-layout .grading-math")
        expect(layout.locator("strong")).to_have_text("This is the final tableau because:")
        expect(layout.locator(".latex-blank")).to_have_count(3)
        widths = layout.locator(".latex-blank").evaluate_all("els => els.map(el => Math.round(el.getBoundingClientRect().width))")
        assert widths[0] > 300 and 35 < widths[1] < 50, widths  # 8.5 cm and 1.1 cm at 96 px to the inch
        expect(layout.locator(".katex-display")).to_have_count(1)
        expect(layout.locator(".latex-table.latex-ruled tr")).to_have_count(2)
        expect(layout.locator("img")).to_have_count(0)
        shown = layout.evaluate("el => { const c = el.cloneNode(true); c.querySelectorAll('.katex, .sr-only').forEach(k => k.remove()); return c.textContent; }")
        assert "\\" not in shown and "costs $40" in shown and "<img src=x>" in shown, shown
        page.locator("#math-layout").evaluate("node => node.remove()")
        # Exercise the shared comment renderer, with math and the
        # same Faculty disagreement flag the instructor sees in the review list.
        page.evaluate(r"""async () => {
          const prefix = document.querySelector('script[src$="/app.js"]').src.replace(/app\.js$/, '');
          const {modelLabel, modelComment, reviewReasonMarkup} = await import(prefix + 'grading-model-comments.js');
          const {renderMath} = await import(prefix + 'grading-math.js');
          const h = {e: text => {const span = document.createElement('span'); span.textContent = text; return span.innerHTML;}};
          const box = document.createElement('section'); box.id = 'model-comments'; box.className = 'grading-panel';
          box.innerHTML = ['primary', 'second', 'verifier', 'arbiter', 'flag_reviewer', 'reader', 'jev'].map(role =>
            `<p>${modelLabel(role, '', h)}: ${modelComment(role, String.raw`Check $x=\frac{148}{11}$ and <the margin>.`, h)}</p>`).join('') +
            `<p id="faculty-flag">${reviewReasonMarkup('The primary and secondary graders differ on R2_3, R2_6; review this response individually.', h)}</p>`;
          document.body.append(box); renderMath(box);
        }""")
        palette = page.locator("#model-comments p:has(.grading-model-label)").evaluate_all("""rows => rows.map(row => ({
          label: getComputedStyle(row.querySelector('.grading-model-label')).color,
          comment: getComputedStyle(row.querySelector('.grading-model-comment')).color,
          math: getComputedStyle(row.querySelector('.katex')).color,
          ordinary: getComputedStyle(row).color,
        }))""")
        assert len({row["label"] for row in palette}) == 7, palette
        assert all(row["label"] == row["comment"] == row["math"] != row["ordinary"] for row in palette), palette
        expect(page.locator("#faculty-flag [data-grading-role]")).to_have_count(0)
        expect(page.locator("#model-comments img, #model-comments a")).to_have_count(0)
        page.locator("#model-comments").screenshot(path=str(output / "review-model-comments.png"))
        page.locator("#model-comments").evaluate("node => node.remove()")
        # With a question chosen, a save reads only that question's responses again (perf-12: 2.6 MB → 0.16 MB on Exam 1).
        page.get_by_label("Question", exact=True).select_option(fixture.qid)
        page.get_by_label("Response", exact=True).select_option(value=value["id"])
        expect(page.locator(f"#review-{value['id']}")).to_be_visible()
        # The picker names the question as printed, never its internal key (grading-review-15).
        expect(page.get_by_label("Response", exact=True).locator("option").first).to_contain_text(question["content"]["title"])
        expect(page.get_by_label("Response", exact=True).locator("option").first).not_to_contain_text(question["question_key"])
        lists = []
        page.on("request", lambda request: lists.append(request.url) if "/responses" in request.url and f"/assignments/{aid}/" in request.url else None)
        graph = page.locator('.grading-workflow [data-pipeline-step="review"] .grading-pipeline-fraction')
        expect(graph).to_have_text("0% · 0/2")
        # In a short window the next response opens at its heading, so its handwriting is seen before its Approve button
        # (grading-review-16).
        page.set_viewport_size({"width": 1500, "height": 600})
        page.get_by_role("button", name="Approve grade", exact=True).scroll_into_view_if_needed()
        # An untouched displayed transcript is sent back exactly as retained.
        page.get_by_role("button", name="Approve grade", exact=True).click()
        # The list opened on Needs review, so approving moves on to the other response still waiting.
        expect(page.get_by_label("Response", exact=True).locator("option")).to_have_count(1)
        expect(page.get_by_label("Response", exact=True)).not_to_contain_text("Approved")
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator("[data-review-heading]")).to_be_in_viewport()
        assert [url.split("?", 1)[1] if "?" in url else "" for url in lists if url.split("?")[0].endswith("/responses")] == [f"question_id={fixture.qid}"], lists
        expect(page.locator("[data-review-progress]").first).to_have_text("1 of 2 final · 1 shown")
        # FRONTEND "The step graph follows the review" (2026-10-11): a decision in All responses is followed by the plan read too,
        # and the next response stays as the desk drew it.
        desk_drawn = page.locator(".grading-review-form").get_attribute("id")
        expect(graph).to_have_text("50% · 1/2")
        expect(page.locator(".grading-review-form")).to_have_attribute("id", desk_drawn)
        assert fixture.frq.response(value["id"])["review"]["transcript"] == original
        # A response sent to be graded again says so until its new grade lands (missed: the old proposal stayed approvable with no
        # mark). The regrade request is answered here, so nothing is queued.
        page.set_viewport_size({"width": 1500, "height": 1100})
        regrades = []
        page.route("**/api/grading/responses/*/regrade", lambda route: (regrades.append(route.request.url), route.fulfill(json={"id": "synthetic-job"})))
        desk_id = page.locator(".grading-review-form").get_attribute("id")
        # Each desk drawn for the response reads the batch grading it again once; one that settled without a new grade ends the line.
        regrade_batch = {"status": "pending"}
        page.route("**/api/grading/jobs/synthetic-job", lambda route: route.fulfill(json={"id": "synthetic-job", "status": regrade_batch["status"],
            "tasks": [{"response_id": desk_id.removeprefix("review-"), "status": regrade_batch["status"], "error": "Synthetic host refused the request." if regrade_batch["status"] == "failed" else None}]}))
        page.get_by_text("Ask for another opinion", exact=True).click()
        page.get_by_role("button", name="Regrade with Cloud", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Queued.")
        expect(page.locator("[data-desk-regrading]")).to_contain_text("Sent to be graded again at")
        page.get_by_label("Show responses", exact=True).select_option("")  # the same response drawn again: still marked
        page.get_by_label("Response", exact=True).select_option(value=desk_id.removeprefix("review-"))
        expect(page.locator(f"#{desk_id}")).to_be_visible()
        expect(page.locator("[data-desk-regrading]")).to_be_visible()
        page.wait_for_timeout(300)  # its batch, still pending, was read: the line stays
        expect(page.locator("[data-desk-regrading]")).to_contain_text("Sent to be graded again at")
        assert len(regrades) == 1, regrades
        regrade_batch["status"] = "failed"  # the regrade failed: the next desk drawn for it says so once, then nothing
        page.get_by_label("Show responses", exact=True).select_option("needs-review")
        expect(page.locator(f"#{desk_id}")).to_be_visible()
        expect(page.locator("[data-desk-regrade-failed]")).to_have_text(re.compile(r"^It could not be graded again: Synthetic host refused the request\. This grade is unchanged"))
        page.get_by_label("Show responses", exact=True).select_option("")
        page.get_by_label("Response", exact=True).select_option(value=desk_id.removeprefix("review-"))
        expect(page.locator(f"#{desk_id}")).to_be_visible()
        page.wait_for_timeout(300)
        expect(page.locator("[data-desk-regrading]")).to_have_count(0)
        # A certified assignment asks before grading one of its responses again; declining sends nothing (grading-review-4).
        certified = re.compile(rf".*/api/grading/assignments/{aid}\?include=activity$")
        def with_certification(route):
            body = route.fetch().json()
            body["workflow"]["certification"] = {**(body["workflow"].get("certification") or {}), "state": "current", "created_at": "2026-09-29T15:00:00+00:00"}
            route.fulfill(json=body)
        page.route(certified, with_certification)
        page.reload()
        expect(page.locator(".grading-review-form")).to_be_visible()
        asked = []
        page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
        page.get_by_text("Ask for another opinion", exact=True).click()
        page.get_by_role("button", name="Regrade with Cloud", exact=True).click()
        page.wait_for_timeout(300)
        assert asked and asked[0].startswith("These grades are certified."), asked
        assert len(regrades) == 1, regrades
        page.unroute(certified)
        page.unroute("**/api/grading/responses/*/regrade")
        # A crop popup over the bulk-review dialog must not replace its form.
        page.set_viewport_size({"width": 1500, "height": 1100})
        page.get_by_text("Review several responses together", exact=True).click()
        # The already-approved response is not bulk-approvable; use the group view's image component directly.
        page.evaluate("""async () => {
          const prefix = document.querySelector('script[src$="/app.js"]').src.replace(/app\\.js$/, '');
          const {mountCropZoom} = await import(prefix+'grading-crops.js');
          const dialog = document.querySelector('#dialog');
          dialog.innerHTML='<input aria-label="Kept draft" value="unsaved"><div></div>';
          dialog.querySelector('div').append(document.querySelector('[data-crop-zoom]').cloneNode(true));
          mountCropZoom(dialog); dialog.showModal();
        }""")
        page.locator("#dialog [data-crop-zoom]").click()
        expect(zoom).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_label("Kept draft")).to_have_value("unsaved")
        expect(page.locator("#dialog")).to_be_visible()
        page.keyboard.press("Escape")
        # With nothing new, waiting or failed, the Grade step leads on to the assignment's next step as its one primary
        # (grading-review-14), the graph's own: this fixture never confirmed its blank template, so that is step 1.
        other = next(r for r in fixture.frq.responses(aid) if r["id"] != value["id"])
        fixture.approve(fixture.frq.response(other["id"]))
        page.goto(f"{address}/#grading/{aid}?tab=jobs")
        page.reload()  # The approval above went straight to the catalog; a step change within 10 s reuses the plan read (perf-1).
        onward = page.locator(".grading-content .next-step")
        expect(onward.locator(".next-step-title")).to_have_text("Upload the rubrics and the assignment templates")
        expect(page.locator(".grading-content .button.primary")).to_have_count(1)
        expect(onward.get_by_role("link")).to_have_text("Rubrics & Templates →")
        expect(onward.get_by_role("link")).to_have_attribute("href", f"#grading/{aid}?tab=rubrics")
        expect(page.get_by_role("button", name="Grade new responses", exact=True)).not_to_have_class("button primary")
        # GRADING KEEP CONFLICTED EDITS: a refused save keeps the draft, reads current work only on request,
        # restores compatible edits without saving, and refuses to carry scoring into a changed rubric.
        conflict_page = browser.new_page(viewport={"width": 1280, "height": 1000})
        attempts = []
        changed_rubric = [False]
        def refuse_review(route):
            attempts.append(route.request.post_data_json)
            route.fulfill(status=409, json={"error": "The response changed. Read it again."})
        reads = []
        def current_response(route):
            reads.append(route.request.url)
            body = route.fetch().json()
            if changed_rubric[0]:
                body["question"]["rubric"]["id"] = "new-rubric"
            route.fulfill(json=body)
        conflict_page.route("**/api/grading/responses/*/review", refuse_review)
        conflict_page.route(re.compile(r".*/api/grading/responses/[^/?]+\?.*details=review.*"), current_response)
        conflict_page.goto(f"{address}/#grading/{aid}?tab=review&browse=all")
        conflict_page.get_by_label("Response", exact=True).select_option(value=value["id"])
        expect(conflict_page.locator(f"#review-{value['id']}")).to_be_visible()
        conflict_page.get_by_text("Edit transcription", exact=True).click()
        transcript_field = conflict_page.locator('[name="transcript"]')
        transcript_field.fill("My retained correction")
        conflict_page.get_by_role("button", name="Approve grade", exact=True).click()
        expect(conflict_page.locator('[data-review-recovery]')).to_contain_text("Your edits are kept")
        expect(transcript_field).to_have_value("My retained correction")
        assert len(attempts) == 1
        reads.clear()
        conflict_page.get_by_role("button", name="Review updated grade", exact=True).click()
        expect(conflict_page.get_by_role("button", name="Apply my edits to this grade", exact=True)).to_be_visible()
        assert reads and not any("reveal=1" in url for url in reads), reads  # the page's own reread: nothing revealed
        conflict_page.get_by_role("button", name="Apply my edits to this grade", exact=True).click()
        expect(transcript_field).to_have_value("My retained correction")
        assert len(attempts) == 1  # applying a recovered draft never resubmits it
        changed_rubric[0] = True
        conflict_page.get_by_role("button", name="Approve grade", exact=True).click()
        conflict_page.get_by_role("button", name="Review updated grade", exact=True).click()
        expect(conflict_page.get_by_role("button", name="Apply my text edits", exact=True)).to_be_visible()
        conflict_page.get_by_role("button", name="Discard my edits", exact=True).click()
        expect(conflict_page.locator('[data-review-recovery]')).to_have_count(0)
        assert len(attempts) == 2
        conflict_page.close()
        assert not errors, errors
        assert not bad, bad
        assert not external, external
        browser.close()
        print("Review browser passed: compact rubric, rendered math, literal fallback, crop zoom, nested drafts, exact transcript preservation and desktop layouts.")
finally:
    server.close()
    fixture.doCleanups()
