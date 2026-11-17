"""Task-focused setup: one question, preserved navigation, and a scoped key save."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_templates import AssignmentTemplateWorkflow
from test_grading_locations import confirm_templates
from frq_fixtures import confirm_for_review
from test_gradescope_rubrics import encode, export_rows

fixture = AssignmentTemplateWorkflow('test_uploaded_reference_waits_for_warning_before_any_reading')
fixture.setUp()
# Question 19's full-credit line gives its answer in math, so a key is read from it; Question 18's says only "Full credit".
KEYED = encode(export_rows(descriptions=['Correct Answer: $$x=4$$ with work', 'Part credit 1: setup', 'Part credit 2: arithmetic', 'No answer'])).encode()
for letter in 'AB':
    for number in (18, 19):
        fixture.upload(f'1_Question_{number}.csv', KEYED if number == 19 else None, exam_version=letter)
fixture.template()
confirm_templates(fixture.frq, fixture.plan['id'])
for _ in range(40):
    if not fixture.frq.process_location_one():
        break
# A rubric needs attention until the instructor confirms it for the rubric reviewer, so "Needs attention" below is empty.
confirm_for_review(fixture.frq, fixture.plan['id'])
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results'); output.mkdir(exist_ok=True)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1100})
        page.set_default_timeout(15000)
        errors, bad, key_writes = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('response', lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
        page.on('request', lambda r: key_writes.append(r.post_data_json) if r.method == 'POST' and r.url.endswith('/answer-key') else None)
        aid = fixture.plan['id']
        page.goto(address + f'/#grading/{aid}?tab=rubrics')
        board = page.locator('[data-setup-questions]')
        expect(board.locator('[data-question-panel]:visible')).to_have_count(1)
        expect(board.locator('[data-question-choice]')).to_have_count(4)
        page.get_by_label('Find a question', exact=True).fill('Version B')
        expect(board.locator('[data-question-choice]:visible')).to_have_count(2)
        expect(board.locator('[data-question-panel]:visible h3')).to_contain_text('Version B')
        page.get_by_label('Questions to show', exact=True).select_option('attention')
        expect(board.locator('[data-no-questions]')).to_be_visible()
        expect(board.locator('[data-question-panel]:visible')).to_have_count(0)
        page.get_by_label('Questions to show', exact=True).select_option('all')
        page.get_by_label('Find a question', exact=True).fill('')
        page.get_by_role('button', name='Next question →', exact=True).click()
        selected = board.locator('[data-question-panel]:visible').get_attribute('data-question-panel')
        page.locator('#grading-section > .grading-heading').get_by_role('button', name='Refresh', exact=True).click()
        expect(board.locator('[data-question-panel]:visible')).to_have_attribute('data-question-panel', selected)
        # The navigator groups the questions by version, and '[' / ']' page through them unless a field has the keys (2026-09-26).
        expect(board.locator('[data-question-group]')).to_have_count(2)
        expect(board.locator('[data-question-group]').first).to_contain_text('Version A')
        order = board.locator('[data-question-choice]').evaluate_all('nodes => nodes.map(node => node.dataset.questionChoice)')
        page.evaluate('document.activeElement?.blur()')
        page.keyboard.press('[')
        expect(board.locator('[data-question-panel]:visible')).to_have_attribute('data-question-panel', order[order.index(selected) - 1])
        page.keyboard.press(']')
        expect(board.locator('[data-question-panel]:visible')).to_have_attribute('data-question-panel', selected)
        page.get_by_label('Find a question', exact=True).press(']')  # Typed into the field, never a page turn.
        expect(page.get_by_label('Find a question', exact=True)).to_have_value(']')
        page.get_by_label('Find a question', exact=True).fill('')
        # Nothing needs attention on this step, so there is nowhere to jump to.
        expect(board.get_by_role('button', name='Next needing attention →', exact=True)).to_be_hidden()
        # Assignment versions: one row per version with its template and counts.
        rows = page.locator('[data-exam-versions] [data-version-row]')
        expect(rows).to_have_count(2)
        expect(rows.first).to_contain_text('2 of 2')
        page.locator('[data-setup-tools] > summary').click()
        page.locator('[data-exam-versions]').screenshot(path=str(output / 'setup-versions.png'))
        page.locator('[data-setup-tools] > summary').click()
        # Answer areas shows the rubric read-only and sends its edits to Rubrics & Templates, opening that question.
        page.goto(address + f'/#grading/{aid}?tab=locations')
        shown = board.locator('[data-question-panel]:visible')
        expect(shown.get_by_role('button', name='Edit interpretation', exact=True)).to_have_count(0)
        expect(shown.get_by_role('button', name='Import revision', exact=True)).to_have_count(0)
        expect(shown.get_by_text('Question settings', exact=True)).to_have_count(0)
        target = shown.get_attribute('data-question-panel')
        shown.get_by_role('link', name='Edit this rubric on Rubrics & Templates', exact=True).click()
        expect(page.locator('.grading-workflow [data-pipeline-step="rubrics"] a')).to_have_attribute('aria-current', 'step')
        expect(board.locator('[data-question-panel]:visible')).to_have_attribute('data-question-panel', target)
        expect(board.locator('[data-question-panel]:visible').get_by_role('button', name='Edit interpretation', exact=True)).to_be_visible()
        page.goto(address + f'/#grading/{aid}?tab=answer-key')
        # Two questions still need a key: Next needing attention goes to one of them.
        attention = board.get_by_role('button', name='Next needing attention →', exact=True)
        expect(attention).to_be_visible()
        attention.click()
        expect(board.locator('.grading-question-link[aria-pressed="true"]')).to_have_class('grading-question-link needs-attention')
        # Each question shows the key read from its rubric before it is saved; Confirm all saves them as shown (2026-09-25).
        expect(page.locator('[data-confirm-all-keys]')).to_have_text('Save 2 answer keys')
        expect(page.locator('[data-keys-unread]')).to_contain_text('Question 18 · Version A, Question 18 · Version B')
        page.get_by_label('Find a question', exact=True).fill('19')
        proposed = board.locator('[data-question-panel]:visible [data-key-proposed]')
        expect(proposed).to_contain_text('Read from the rubric · not saved yet')
        expect(proposed.get_by_role('button', name='Save this key', exact=True)).to_be_visible()
        page.get_by_label('Find a question', exact=True).fill('')
        page.locator('[data-confirm-all-keys]').click()
        expect(page.locator('[data-confirm-all-keys]')).to_have_count(0)
        expect(page.locator('[data-step-lead]')).to_contain_text('2 of 4 questions have a key.')  # Said once, in the header.
        page.get_by_label('Find a question', exact=True).fill('19')
        expect(board.locator('[data-question-panel]:visible .grading-key-summary .katex')).to_have_count(1)  # x=4, typeset.
        page.get_by_label('Find a question', exact=True).fill('')
        assert len(key_writes) == 1 and sorted(q['answer_key'][0]['accept'] for q in key_writes[0]['questions']) == [['x=4'], ['x=4']], key_writes
        keyed = {q['question_id']: q for q in fixture.uploaded_questions()}
        assert sum(bool(q['content'].get('answer_key')) for q in keyed.values()) == 2, keyed
        before = {q['question_id']: q['question_revision_id'] for q in keyed.values()}
        page.get_by_label('Find a question', exact=True).fill('Version B')
        target = board.locator('[data-question-panel]:visible').get_attribute('data-question-panel')
        page.get_by_role('button', name='Edit this answer key', exact=True).click()
        expect(page.locator('#dialog .grading-key')).to_have_count(1)
        # The editor typesets each key under its box as it is typed; a key is bare LaTeX, without $ signs (2026-09-25).
        page.get_by_label('Accepted final answers', exact=True).fill('a: \\dfrac{168}{11}')
        expect(page.locator('#dialog [data-key-preview] .katex')).to_have_count(1)
        expect(page.locator('#dialog [data-key-preview]')).to_contain_text('Part a:')
        page.get_by_label('Accepted final answers', exact=True).fill('x=42')
        page.get_by_role('button', name='Save answer key', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(board.locator('[data-question-panel]:visible')).to_have_attribute('data-question-panel', target)
        expect(board.locator('[data-question-panel]:visible .grading-key-summary')).to_contain_text('x=42')
        assert len(key_writes) == 2 and [q['question_id'] for q in key_writes[1]['questions']] == [target]
        after = {q['question_id']: q['question_revision_id'] for q in fixture.uploaded_questions()}
        assert after[target] != before[target]
        assert all(after[qid] == rid for qid, rid in before.items() if qid != target)
        for width in (1500, 1280):
            page.set_viewport_size({'width': width, 'height': 1100})
            for tab in ('rubrics', 'locations', 'answer-key'):
                page.goto(address + f'/#grading/{aid}?tab={tab}')
                expect(board.locator('[data-question-panel]:visible')).to_have_count(1)
                if tab == 'locations':
                    image = board.locator('[data-question-panel]:visible .grading-area-previews img').first
                    expect(image).to_be_visible()
                    expect(board.locator('[data-question-panel]:visible [data-preview-area]').first).to_be_visible()
                page.locator('.grading-content').evaluate('el => el.scrollIntoView({block: "start"})')
                page.screenshot(path=str(output / f'setup-{tab}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (tab, width)
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Setup passed: direct assignment entry, question search/filter/navigation, refresh selection, keys read from the rubric and confirmed all at once, scoped key save, highlighted areas and all three steps at 1500 and 1280 px.')
finally:
    server.close()
    fixture.doCleanups()
