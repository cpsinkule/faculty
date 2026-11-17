"""Setup steps that stay put and say what they do (usability pass, 2026-10-02).

A finalized rubric asks before an edit or a comment takes it out of the finalization, and an approved one already out of it asks
before changed lines, without the finalization's claims (grading-setup-3); Save changes waits for a change (grading-setup-19);
import notes fold once a rubric is approved (grading-setup-10); a key being read from the blank assignment is watched quietly,
without redrawing the step under the instructor or wiping a typed search (grading-setup-1, missed:perf[0]); an answer-area card is
not written again by a poll that changed nothing, yet its highlight follows a moved area, and an answer area can be drawn where
none was suggested (missed:grading-setup[0], [1]); and a plan without its PDF leads to Create (grading-setup-15).
Synthetic fixtures only; nothing leaves the machine.
"""
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_templates import AssignmentTemplateWorkflow
from test_grading_locations import confirm_templates
from frq_fixtures import finalize_rubrics
from test_gradescope_rubrics import encode, export_rows

OUTPUT = Path('test-results')
OUTPUT.mkdir(exist_ok=True)
fixture = AssignmentTemplateWorkflow('test_uploaded_reference_waits_for_warning_before_any_reading')
fixture.setUp()
KEYED = encode(export_rows(descriptions=['Correct Answer: $$x=4$$ with work', 'Part credit 1: setup', 'Part credit 2: arithmetic', 'No answer'])).encode()
for letter in 'AB':
    for number in (18, 19):
        fixture.upload(f'1_Question_{number}.csv', KEYED if number == 19 else None, exam_version=letter)
fixture.template()
confirm_templates(fixture.frq, fixture.plan['id'])
for _ in range(40):
    if not fixture.frq.process_location_one():
        break
finalize_rubrics(fixture.frq, fixture.plan['id'])
aid = fixture.plan['id']
by_name = {f"{q['content']['printed_number']}{q['exam_version']}": q for q in fixture.uploaded_questions()}
assert set(by_name) == {'18A', '18B', '19A', '19B'}, sorted(by_name)
assert all(q['rubric']['approved_at'] for q in by_name.values())
plan_path = f'/api/grading/assignments/{aid}'
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'


def shots(page, name):
    """The one appearance at two desktop widths: 1440x900 and the narrowest laptop window, 1280x800."""
    for width, height in ((1440, 900), (1280, 800)):
        page.set_viewport_size({'width': width, 'height': height})
        page.screenshot(path=str(OUTPUT / f'setup-steps-{name}-{width}.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (name, width)
    page.set_viewport_size({'width': 1440, 'height': 900})


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_default_timeout(15000)
        errors, bad, edits, reads = [], [], [], {'activity': 0, 'plain': 0}
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('response', lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
        page.on('request', lambda r: edits.append(r.post_data_json) if r.method == 'POST' and r.url.endswith('/rubric-edits') else None)

        def count(request):
            parts = urlsplit(request.url)
            if request.method == 'GET' and parts.path == plan_path:
                reads['activity' if 'include=activity' in parts.query else 'plain'] += 1
        page.on('request', count)
        board = page.locator('[data-setup-questions]')
        dialog = page.locator('#dialog')
        toast = page.locator('#toast')

        # 1. A finalized rubric on Rubrics & Templates. Save changes waits for a change; the Gradescope import note is folded.
        page.goto(address + f'/#grading/{aid}?tab=rubrics')
        first = by_name['18A']['question_id']
        board.locator(f'[data-question-choice="{first}"]').click()
        form = page.locator(f'form[data-rubric-edit="{first}"]')
        save = form.get_by_role('button', name='Save changes', exact=True)
        expect(save).to_be_disabled()
        expect(save).to_have_attribute('title', 'Edit a line’s wording, points or comment first.')
        notes = page.locator(f'[data-rubric-question="{first}"] [data-import-notes]')
        expect(notes.locator('summary')).to_have_text(re.compile(r'^Import notes \(\d+\)$'))
        expect(notes).not_to_have_attribute('open', '')
        expect(notes).to_contain_text('Gradescope exports omit item groups')
        expect(form.locator('.grading-warning')).to_have_count(0)
        # A comment takes the question out of the finalization: Save asks first, and closing the window saves nothing.
        form.get_by_label('Comment on item 1 for the rubric reviewer (optional)', exact=True).fill('Accept any equivalent form.')
        expect(save).to_be_enabled()
        save.click()
        expect(dialog.get_by_role('heading', name='Change a finalized rubric?', exact=True)).to_be_visible()
        expect(dialog).to_contain_text('Saving these comments takes it out of the finalization')
        expect(dialog.get_by_role('link', name='Correct wording on Key & Rubric Finalization')).to_have_count(0)  # Comments only.
        shots(page, 'finalized-comment')
        dialog.get_by_role('button', name='Close', exact=True).click()
        expect(dialog).not_to_be_visible()
        save.click()
        expect(dialog).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        assert not edits, edits
        save.click()
        with page.expect_response(lambda r: r.url.endswith('/rubric-edits')):
            dialog.get_by_role('button', name='Save and reopen this question', exact=True).click()
        expect(toast).to_contain_text('Comments saved')
        expect(dialog).not_to_be_visible()
        assert len(edits) == 1 and edits[0]['comments'] == {'R1_1': 'Accept any equivalent form.'}, edits
        form = page.locator(f'form[data-rubric-edit="{first}"]')
        expect(form.locator('[data-rubric-edit-status]')).to_contain_text('Not confirmed for rubric review yet')
        # Out of the finalization now: a further comment saves without asking.
        form.get_by_label('Comment on item 2 for the rubric reviewer (optional)', exact=True).fill('Either order of the terms.')
        with page.expect_response(lambda r: r.url.endswith('/rubric-edits')):
            form.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog).not_to_be_visible()
        assert len(edits) == 2, edits
        # Out of the finalization but still approved: a changed line asks again, without the finalization's claims or the
        # Correct wording link (it keeps a finalization only where one stands).
        form.get_by_label('Item 3 wording', exact=True).fill('Part credit 2: the arithmetic, carried out')
        form.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog.get_by_role('heading', name='Change an approved rubric?', exact=True)).to_be_visible()
        expect(dialog).to_contain_text('This rubric is approved. Saving 1 changed line makes a new revision')
        expect(dialog).not_to_contain_text('finalization')
        expect(dialog).not_to_contain_text('stops grading')
        expect(dialog.get_by_role('link', name='Correct wording on Key & Rubric Finalization')).to_have_count(0)
        expect(dialog.get_by_role('button', name='Save the new revision', exact=True)).to_be_visible()
        shots(page, 'approved-line')
        dialog.get_by_role('button', name='Close', exact=True).click()
        assert len(edits) == 2, edits
        # A changed line on a finalized question says what a new revision undoes and where a typo is fixed instead.
        second, third = by_name['19A']['question_id'], by_name['19B']['question_id']
        board.locator(f'[data-question-choice="{second}"]').click()
        form = page.locator(f'form[data-rubric-edit="{second}"]')
        form.get_by_label('Item 2 wording', exact=True).fill('Part credit 1: the setup, written out')
        form.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog).to_contain_text('Saving 1 changed line makes a new revision of this rubric.')
        link = dialog.get_by_role('link', name='Correct wording on Key & Rubric Finalization', exact=True)
        expect(link).to_have_attribute('href', f'#grading/{aid}?tab=answer-key&focus={second}')
        dialog.get_by_role('button', name='Close', exact=True).click()
        # Save all names every finalized question it would reopen, in one window, before anything is sent.
        board.locator(f'[data-question-choice="{third}"]').click()
        page.locator(f'form[data-rubric-edit="{third}"]').get_by_label('Comment on item 1 for the rubric reviewer (optional)', exact=True).fill('A note.')
        page.locator('[data-rubric-drafts]').get_by_role('button', name='Save all', exact=True).click()
        expect(dialog.locator('.dialog-header p')).to_have_text('2 finalized questions and 1 approved question')
        expect(dialog).to_contain_text('Your rubric finalization covers 2 of the questions you are saving: Question 19 · Version A, Question 19 · Version B.')
        expect(dialog).to_contain_text('Question 18 · Version A is approved: it needs confirming for review')
        expect(dialog).to_contain_text('Changed lines make a new revision of their rubric')
        dialog.get_by_role('button', name='Close', exact=True).click()
        assert len(edits) == 2, edits
        page.once('dialog', lambda prompt: prompt.accept())
        page.locator('[data-rubric-drafts]').get_by_role('button', name='Discard all', exact=True).click()
        expect(page.locator('[data-rubric-drafts]')).to_be_hidden()

        # 2. A key being read from the blank assignment: the step looks quietly and redraws only once the key is read.
        reading = {'status': 'running'}
        keyed = by_name['18B']['question_id']

        def mark_reading(route):
            response = route.fetch()
            data = response.json()
            for q in data.get('questions', []):
                if q['question_id'] == keyed:
                    q['answer']['read_key'] = {'status': reading['status'], 'current': True, 'key': [], 'basis': [], 'model': 'fixture-reader',
                                               'error': 'Synthetic reading failure.', 'unreadable': ''}
            route.fulfill(response=response, json=data)
        page.route(lambda url: urlsplit(url).path == plan_path, mark_reading)
        page.goto(address + f'/#grading/{aid}?tab=answer-key')
        page.reload()  # The route changes the server's answer; a step change within 10 s reuses the plan read (perf-1).
        line = page.locator('[data-keys-reading]')
        expect(line).to_contain_text('Reading the key')
        search = page.get_by_label('Find a question', exact=True)
        search.fill('18')
        page.evaluate("window.searchNode = document.querySelector('[name=\"question-search\"]')")
        before = dict(reads)
        page.wait_for_timeout(6500)
        assert reads['activity'] == before['activity'], reads  # The step was not drawn again,
        assert reads['plain'] > before['plain'], reads  # though it looked at the plan alone.
        assert page.evaluate('window.searchNode.isConnected && window.searchNode.value') == '18'
        expect(page.locator('.grading-content .loading-state')).to_have_count(0)
        reading['status'] = 'failed'
        # Finished while a search is typed: the line says so and offers Refresh instead of redrawing.
        expect(line).to_contain_text('The answer key finished reading.', timeout=9000)
        assert reads['activity'] == before['activity'], reads
        expect(search).to_have_value('18')
        shots(page, 'key-finished')
        search.fill('')
        line.get_by_role('button', name='Refresh', exact=True).click()
        expect(line).to_have_count(0)
        assert reads['activity'] == before['activity'] + 1, reads
        # Nothing held on screen: a finished key redraws the step by itself, once.
        reading['status'] = 'running'
        page.locator('#grading-section > .grading-heading').get_by_role('button', name='Refresh', exact=True).click()
        expect(line).to_be_visible()
        page.evaluate('document.activeElement?.blur()')
        drawn = reads['activity']
        reading['status'] = 'failed'
        expect(line).to_have_count(0, timeout=9000)
        assert reads['activity'] == drawn + 1, reads
        page.unroute_all()

        # 3. Answer areas: a poll that changed nothing keeps every card's nodes, and an area can be drawn where none was suggested.
        blank, waiting = by_name['19B']['question_id'], by_name['18B']['question_id']

        moved = {'on': False}

        def areas(route):
            response = route.fetch()
            data = response.json()
            for q in data.get('questions', []):
                if q['question_id'] == kept and moved['on']:
                    check = q['location_check']
                    regions = [{**area, 'region': {**area['region'], 'x': .4, 'width': .2}} for area in check['result']['regions']]
                    q['location_check'] = {**check, 'input_sha256': 'f' * 64, 'result': {**check['result'], 'regions': regions}}
                if q['question_id'] == waiting:
                    q['location_check'] = {**q['location_check'], 'status': 'running'}
                if q['question_id'] == blank:
                    check = q['location_check']
                    q['content']['regions'] = []
                    q['location_check'] = {**check, 'status': 'needs_review', 'result': {**(check.get('result') or {}), 'regions': []},
                                           'context': {**check['context'], 'issues': ['No answer area could be suggested. Draw it on its page.']}}
            route.fulfill(response=response, json=data)
        kept = by_name['18A']['question_id']
        page.route(lambda url: urlsplit(url).path == plan_path, areas)
        page.goto(address + f'/#grading/{aid}?tab=locations')
        page.reload()  # The route changes the server's answer; a step change within 10 s reuses the plan read (perf-1).
        card = page.locator(f'[data-rubric-question="{kept}"] [data-location-check]')
        expect(card.locator('img')).to_have_count(1)
        page.evaluate(f"window.cardImage = document.querySelector('[data-rubric-question=\"{kept}\"] [data-location-check] img')")
        with page.expect_request(lambda r: urlsplit(r.url).path == plan_path and not urlsplit(r.url).query, timeout=9000):
            pass
        page.wait_for_timeout(500)
        assert page.evaluate('window.cardImage.isConnected'), 'An unchanged poll wrote the card again'
        # A reading that moves the suggested area (same status, label and page) moves its highlight: the coordinates are not in
        # the compared markup, and Confirm sends the check the preview shows.
        overlay = card.locator('[data-preview-area="0"]')
        expect(overlay).to_have_attribute('style', re.compile(r'left: 0%.*width: 100%'))
        moved['on'] = True
        expect(overlay).to_have_attribute('style', re.compile(r'left: 40%.*width: 20%'), timeout=25000)  # The next whole read, ≤ 15 s.
        assert page.evaluate('window.cardImage.isConnected'), 'A moved area fetched the preview again'
        board.locator(f'[data-question-choice="{blank}"]').click()
        page.locator(f'[data-rubric-question="{blank}"]').get_by_role('button', name='Review answer area', exact=True).click()
        expect(dialog.get_by_role('heading', name='Confirm the answer area', exact=True)).to_be_visible()
        expect(dialog.locator('.dialog-header p')).to_have_text('Draw the answer area, then confirm it.')
        footer = dialog.locator('.dialog-footer')
        expect(footer.get_by_label('I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages')).to_be_visible()
        expect(dialog.locator('[data-location-hint]')).to_contain_text('No answer area was suggested')
        choice = dialog.get_by_label('Area to draw', exact=True)
        expect(choice.locator('option')).to_have_text(['New area'])
        pages = dialog.locator('details.grading-location-page')
        assert pages.count() >= 1 and all(pages.nth(i).get_attribute('open') is not None for i in range(pages.count()))
        image = dialog.locator("[data-location-page='1']")
        image.scroll_into_view_if_needed()
        expect(image.locator('img')).to_be_visible()
        box = image.bounding_box()
        page.mouse.move(box['x'] + box['width'] * .1, box['y'] + box['height'] * .3)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width'] * .9, box['y'] + box['height'] * .6)
        page.mouse.up()
        expect(image.locator('.grading-highlight')).to_have_count(1)
        expect(choice.locator('option')).to_have_text(['Area 1'])
        drawn_areas = page.evaluate("JSON.parse(document.querySelector('#dialog [name=\"regions\"]').value)")
        assert len(drawn_areas) == 1 and drawn_areas[0]['page'] == 1 and abs(drawn_areas[0]['region']['x'] - .1) < .02, drawn_areas
        expect(dialog.locator('[data-location-hint]')).to_contain_text('Drag a rectangle on a page to replace the selected answer area')
        shots(page, 'drawn-area')
        dialog.get_by_role('button', name='Close', exact=True).click()
        page.unroute_all()

        # 4. Without the assignment's PDF, the step's one primary is the link to add it; nothing offers an upload or a draft.
        def no_paper(route):
            response = route.fetch()
            data = response.json()
            data.update(questions=[], document_versions=[], rubric_upload_documents=[])
            data['template_setup'] = {**data['template_setup'], 'slots': [], 'generations': [], 'ready': False, 'can_choose': False,
                                      'confirmed': False, 'blockers': ['Upload or draft rubrics for every version first.']}
            route.fulfill(response=response, json=data)
        page.route(lambda url: urlsplit(url).path == plan_path, no_paper)
        page.goto(address + f'/#grading/{aid}?tab=rubrics')
        panel = page.locator('[data-add-paper-panel]')
        pdf = panel.get_by_role('link', name='Add paper in Create', exact=True)
        expect(pdf).to_have_class('button primary')
        expect(pdf).to_have_attribute('href', f"#documents?assignment={fixture.plan['source_assignment_id']}&term={fixture.plan['term_id']}&step=version")
        expect(panel).to_contain_text('Add a PDF in Create first.')
        expect(page.locator('[data-prepare-remaining]')).to_have_count(0)
        expect(page.get_by_role('button', name='Upload rubrics', exact=True)).to_have_count(0)
        expect(page.locator('[data-grading-action="draft-all"]')).to_have_count(0)
        expect(page.locator('[data-versions-slot], [data-templates-slot]')).to_have_count(0)
        expect(page.locator('[data-grading-action="question"]')).to_have_count(0)  # Its version list would be empty.
        shots(page, 'no-paper')
        page.unroute_all()

        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Setup steps passed: finalized rubric edits ask first, Save waits for a change, import notes folded, quiet key reading, '
              'unchanged answer-area cards kept, drawing a first answer area, and the PDF link as the first step without a paper.')
finally:
    server.close()
    fixture.doCleanups()
