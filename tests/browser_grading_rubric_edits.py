"""Rubric lines prepared on Rubrics & Templates before the rubric reviewer (requirement, 2026-09-24): edited wording and points become
the rubric, comments guide the reviewer's clarifications, and nothing is sent before the instructor confirms it for review."""
import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from fake_openai import FakeHost, payload_of
from test_grading_cloud import endpoint
from test_grading_uploads import RubricUploads
from test_gradescope_rubrics import encode, export_rows

OUTPUT = Path('test-results')
OUTPUT.mkdir(exist_ok=True)
fixture = RubricUploads('test_eight_csvs_create_exact_drafts_from_empty_plan')
fixture.setUp()
COMMENT = 'A fraction such as 18/3 counts when the division is shown.'


def reviewer(body, host):
    """Clarifies nothing on its own, so the instructor's comment is proposed as it stands."""
    sent = payload_of(body)
    return {'summary': 'Nothing to add.', 'suspected_errors': [],
            'items': [{'item_id': item['id'], 'clarification': '', 'excludes': [], 'reason': ''} for item in sent['question']['rubric']['items']]}


model = FakeHost(script=reviewer).start()
fixture.addCleanup(model.stop)
current = fixture.frq.settings()
fixture.frq.save_settings({'version': current['version'], 'settings': {**current['settings'],
    'endpoints': [*current['settings']['endpoints'], endpoint(model, 'qa-text', modality='text')], 'rubric_reviewer': 'qa-text'}})
fixture.upload('1_Question_18.csv', encode(export_rows(points=(10, 6, 4, 0))).encode())
question = fixture.uploaded_questions()[0]
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1450, 'height': 1050})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: bad.append((response.status, response.url)) if response.status >= 500 and not response.url.endswith('/templates/prepare') else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        expect(form).to_be_visible()
        expect(page.locator('[data-step-lead]')).to_contain_text('0 of 1 rubric confirmed for review.')
        expect(form.locator('[data-rubric-edit-status]')).to_contain_text('Not confirmed for rubric review yet')
        points = form.get_by_label('Item 3 points', exact=True)
        wording = form.get_by_label('Item 3 wording', exact=True)
        comment = form.get_by_label('Comment on item 1 for the rubric reviewer (optional)', exact=True)
        expect(points).to_have_value('4')
        confirm = form.get_by_role('button', name='Confirm for rubric review', exact=True)
        expect(confirm).to_be_enabled()
        # Unsaved changes are not what a confirmation would name: confirming waits for the save.
        points.fill('3')
        wording.fill('Part credit 2: the value $$x=6$$ without its setup')
        comment.fill(COMMENT)
        expect(confirm).to_be_disabled()
        expect(form.locator('[data-rubric-edit-status]')).to_contain_text('Unsaved changes')
        expect(form.locator('.grading-rubric-line-preview').nth(2)).to_contain_text('without its setup')
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(OUTPUT / 'rubric-edit-1450.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        held_saves = []

        def hold_save(route):
            held_saves.append(route)

        page.route('**/rubric-edits', hold_save)
        form.get_by_role('button', name='Save changes', exact=True).click()
        expect(points).to_be_disabled()
        expect(wording).to_be_disabled()
        expect(comment).to_be_disabled()
        assert len(held_saves) == 1
        route = held_saves.pop()
        route.fulfill(response=route.fetch())
        page.unroute('**/rubric-edits', hold_save)
        expect(page.locator('#toast')).to_contain_text('Saved 1 changed line as a new rubric revision')
        saved = fixture.uploaded_questions()[0]
        line = next(i for i in saved['rubric']['rules']['items'] if i['id'] == 'R1_3')
        assert saved['rubric_id'] != question['rubric_id'] and (line['description'], line['points']) == ('Part credit 2: the value $$x=6$$ without its setup', 3), line
        assert saved['rubric']['compiler']['name'] == 'instructor-edited'
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        expect(form.get_by_label('Item 3 points', exact=True)).to_have_value('3')
        expect(form.get_by_label('Comment on item 1 for the rubric reviewer (optional)', exact=True)).to_have_value(COMMENT)
        # Step three says where the confirmation is made, and the check cannot start before it.
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=answer-key")
        status = page.locator('.quality-status')
        expect(status).to_contain_text('Confirm each rubric for review in Rubrics & Templates first')
        expect(status.get_by_role('button', name='Run rubric check', exact=True)).to_have_count(0)
        status.get_by_role('link', name='Rubrics & Templates', exact=True).click()
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        form.get_by_role('button', name='Confirm for rubric review', exact=True).click()
        expect(form.locator('[data-rubric-edit-status]')).to_contain_text('Confirmed for rubric review')
        expect(form.get_by_role('button', name='Confirmed for review', exact=True)).to_be_disabled()
        expect(page.locator('[data-step-lead]')).not_to_contain_text('rubric confirmed for review')
        assert not model.requests
        # The reviewer reads the edited rubric with the comment as guidance, and the comment reaches the finalization.
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=answer-key")
        page.locator('[data-step-actions]').get_by_role('button', name='Run rubric check', exact=True).click()
        expect(page.locator('.quality-status')).to_contain_text('Queued')
        fixture.frq.process_cloud()
        sent = payload_of(model.requests[-1]['body'])
        assert sent['question']['instructor_comments'] == {'R1_1': COMMENT}, sent['question']
        assert next(i for i in sent['question']['rubric']['items'] if i['id'] == 'R1_3')['points'] == 3
        page.get_by_role('button', name='Review and confirm', exact=True).click(timeout=20000)
        dialog = page.locator('#dialog form')
        # THE WINDOW SHOWS ONLY THE DECISIONS (2026-10-24): it opens on Decisions; the guidance waits under Rubric items.
        dialog.locator('[data-quality-tab="items"]').click()
        full = dialog.locator('.quality-item').first
        full.locator('summary').click()
        expect(full.locator('.quality-comment')).to_contain_text(COMMENT)
        expect(full.locator('.quality-comment')).to_contain_text('your comment is proposed as it stands')
        expect(full.get_by_label('Clarification for the AI graders (optional)', exact=True)).to_have_value(COMMENT)
        page.screenshot(path=str(OUTPUT / 'rubric-edit-finalization.png'))
        # A later comment needs the confirmation again.
        page.keyboard.press('Escape')
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        form.get_by_label('Comment on item 2 for the rubric reviewer (optional)', exact=True).fill('Either order of the terms.')
        form.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('#toast')).to_contain_text('Comments saved')
        expect(page.locator('[data-step-lead]')).to_contain_text('0 of 1 rubric confirmed for review.')
        assert len(fixture.frq.rubric_quality(fixture.plan['id'])['unconfirmed']) == 1
        # The question list says what the count says: uploaded is not confirmed.
        rail = page.locator('.grading-question-link').first
        expect(rail).to_contain_text('not confirmed for review')
        # Unsaved typing survives the step re-rendering (saving another question refreshes the whole step), and confirming every
        # rubric refuses while a form still holds it.
        note = 'Unsaved note for the reviewer.'
        form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True).fill(note)
        expect(rail).to_contain_text('unsaved changes')
        expect(rail).to_contain_text('· unsaved')
        # The step header counts the unsaved questions and offers Save all as its one primary action (2026-09-26).
        drafts = page.locator('[data-rubric-drafts]')
        expect(drafts).to_contain_text('1 question has unsaved changes')
        expect(drafts.get_by_role('button', name='Save all', exact=True)).to_have_class('button primary')
        expect(page.locator('[data-step-actions] .button.primary')).to_have_count(0)
        # Leaving the page asks first; declining keeps the page and the typing.
        asked = []
        page.once('dialog', lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
        page.evaluate("location.hash = '#grading'")
        for _ in range(50):
            if asked:
                break
            page.wait_for_timeout(100)
        assert asked and 'unsaved rubric edits' in asked[0], asked
        # The declined navigation puts the address back once the dialog has answered (the URL arrives after the dismissal).
        expect(page).to_have_url(re.compile(r'tab=rubrics'))
        expect(form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)).to_have_value(note)
        page.get_by_role('button', name='Refresh', exact=True).click()
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        expect(form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)).to_have_value(note)
        expect(form.locator('[data-rubric-edit-status]')).to_contain_text('Unsaved changes')
        page.get_by_role('button', name='Confirm all for rubric review', exact=True).click()
        expect(page.locator('#toast')).to_contain_text('Save or discard your unsaved changes first')
        assert len(fixture.frq.rubric_quality(fixture.plan['id'])['unconfirmed']) == 1
        form.get_by_role('button', name='Discard changes', exact=True).click()
        expect(form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)).to_have_value('')
        expect(form.get_by_role('button', name='Discard changes', exact=True)).to_be_hidden()
        expect(page.locator('.grading-question-link').first).to_contain_text('not confirmed for review')
        page.get_by_role('button', name='Refresh', exact=True).click()
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        expect(form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)).to_have_value('')
        # Save all saves each question's changes through the ordinary endpoint and says what it saved.
        form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True).fill('Saved with Save all.')
        page.route('**/rubric-edits', hold_save)
        page.locator('[data-rubric-drafts]').get_by_role('button', name='Save all', exact=True).click()
        expect(form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)).to_be_disabled()
        route = held_saves.pop()
        route.fulfill(response=route.fetch())
        page.unroute('**/rubric-edits', hold_save)
        expect(page.locator('#toast')).to_contain_text('Saved Question 18 (comments)')
        expect(page.locator('[data-rubric-drafts]')).to_be_hidden()
        form = page.locator(f'form[data-rubric-edit="{question["question_id"]}"]')
        expect(form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)).to_have_value('Saved with Save all.')
        # Once the instructor has left Grading (the shell hides the section but keeps its forms and this tab's drafts), later
        # navigations elsewhere never ask again (review fix GS-1).
        form.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True).fill('Left behind.')
        asked = []
        page.once('dialog', lambda dialog: (asked.append(dialog.message), dialog.accept()))
        page.evaluate("location.hash = '#inbox'")
        for _ in range(50):
            if asked:
                break
            page.wait_for_timeout(100)
        assert asked and 'unsaved rubric edits' in asked[0], asked
        expect(page.locator('#grading-section')).to_be_hidden()
        later = []
        page.on('dialog', lambda dialog: (later.append(dialog.message), dialog.dismiss()))
        for target in ('#overview', '#calendar', '#documents'):
            page.evaluate(f"location.hash = '{target}'")
            page.wait_for_timeout(400)
        assert not later, later
        # The last navigation went through: the old #documents link opens Assignments (ASSIGNMENT-WORKSPACE).
        assert page.url.endswith('#assignments'), page.url
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Rubric edits browser passed: wording and points saved as a new revision, comments kept, confirmation waits for the save, step three waits for the confirmation, the reviewer reads the comments, the finalization shows them, no sideways scroll on the desktop.')
finally:
    server.close()
    fixture.doCleanups()
