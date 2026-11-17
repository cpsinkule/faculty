"""Per-version uploads, then the mandatory rubric finalization: built-in exclusions, editable clarifications and pairs, one confirmation."""
import re
import sys
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from fake_openai import FakeHost, payload_of, prompt_name
from test_grading_cloud import endpoint
from test_grading_uploads import RubricUploads
from test_grading_rubric_quality import Quality
from test_grading_pipeline import CORRECT
from test_gradescope_rubrics import encode, export_rows
from frq_fixtures import confirm_for_review

OUTPUT = Path('test-results')
OUTPUT.mkdir(exist_ok=True)
fixture = RubricUploads('test_eight_csvs_create_exact_drafts_from_empty_plan')
fixture.setUp()
fixture.enterContext(patch.object(fixture.frq, 'chart_versions', return_value=['A', 'B']))
CLEAR = 'Credit the value even when it is left as an unsimplified fraction.'
# What a recorded meeting said of this assessment's grading, as `GET /api/recordings/rulings` answers it (GRADING "RULINGS SAID IN A
# RECORDING"; the server's side is tests/test_recordings_reading.py). Invented people and words.
SAID = {'id': 'r1', 'recording_id': 'rec1', 'sentence': 'On question 18, an unsimplified answer earns full credit.',
        'quote': 'give full credit for an unsimplified answer', 'speaker': 'Avery Quill', 'at': '0:24', 'day': 'Tue, Nov 10', 'question': '18',
        'href': '#recordings/rec1?t=24000', 'state': 'waiting'}
HELD = {'assignment': 'plan', 'title': 'Exam', 'certified': False, 'rulings': [SAID], 'not_final': None}
RESOLVED = 'Credit the value the answer key gives as well.'


def reviewer(body, host):
    """Every version of a problem in one request (2026-09-25), after a grouping request: Gradescope uploads say nothing about which
    question is which. Part credit 1 and 2 are alternatives, part credit 1 needs a clarification, Version A's part credit 2 looks
    wrong, and the versions' rubrics are reported as differing."""
    sent = payload_of(body)
    if prompt_name(body) == 'rubric_quality_resolve':
        # GRADING: the shared decision settles item 3 on both versions; the points difference needs the rubric editor.
        return {'summary': 'R1_3 clarified on both versions.', 'versions': [{'version': v['version'], 'summary': '', 'items': [
                    {'item_id': row['item_id'], 'clarification': RESOLVED if row['item_id'] == 'R1_3' else row['clarification'],
                     'excludes': row['excludes'], 'reason': 'Alternative levels of part credit.' if row['excludes'] else ''} for row in v['current']]} for v in sent['problem']['versions']],
                'concerns': [{'concern': c['concern'], 'resolved': c['kind'] == 'suspected_error',
                              'how': 'Item 3 of Version A now accepts the key value.' if c['kind'] == 'suspected_error' else 'Change the points of Version B in the rubric editor.'} for c in sent['concerns']],
                'suspected_errors': [], 'consistency_issues': []}
    if prompt_name(body) == 'rubric_quality_group':
        return {'groups': [{'refs': [q['ref'] for q in sent['questions']]}], 'notes': []}
    if prompt_name(body) == 'rubric_quality_added':  # a version added later: its guidance fits, nothing to decide
        return {'summary': 'Version C matches the confirmed versions.', 'suspected_errors': [], 'consistency_issues': [],
            'parallel_lines': [{'kind': 'same', 'lines': [{'version': v['version'], 'item_id': f'R1_{n}'} for v in sent['problem']['versions']], 'note': ''} for n in (1, 2, 3, 4)],
            'versions': [{'version': v['version'], 'summary': '', 'items': [{'item_id': item['id'], 'clarification': CLEAR if item['id'] == 'R1_2' else '',
                'excludes': ['R1_3'] if item['id'] == 'R1_2' else [], 'reason': ''} for item in v['question']['rubric']['items']]} for v in sent['problem']['versions']]}
    items = lambda version: [{'item_id': item['id'], 'clarification': CLEAR if item['id'] == 'R1_2' else '', 'excludes': ['R1_3'] if item['id'] == 'R1_2' else [],
                              'reason': 'Alternative levels of part credit.' if item['id'] == 'R1_2' else ''} for item in version['question']['rubric']['items']]
    # LINKED VERSIONS (2026-10-02): versions v4 names the finding ONCE for both versions and returns the parallel lines.
    return {'summary': 'The part-credit lines are alternatives on both versions.',
        # The finding Faculty fixes sits between the two rows of the shared decision (concern 1 of 0..3).
            # BOOKKEEPING IS FIXED, NOT ASKED (2026-10-24): one right answer, carried in the guidance, listed apart with Undo.
        'suspected_errors': [{'versions': ['B'], 'item_ids': [{'version': 'B', 'item_ids': ['R1_2']}], 'version_notes': [],
             'message': "Version B's comment on R1_2 uses Version A's numbers.", 'title': "Version B's comment uses Version A's numbers", 'decision': '', 'group': '',
             'choices': [], 'fix': {'kind': 'guidance', 'note': "The clarification carries the comment with Version B's own values.", 'keys': []}},
            {'versions': ['A', 'B'], 'item_ids': [{'version': v, 'item_ids': ['R1_3']} for v in 'AB'], 'version_notes': [],
            'message': 'R1_3 names a value the answer key does not accept.',
            'title': 'Which values earn item 3?', 'decision': 'Should item 3 also credit the value given by the answer key?', 'group': '',
            'choices': [{'label': 'Credit the key value', 'feedback': 'The key is right: credit its value on item 3 too.', 'consequence': '5 points'},
                        {'label': 'Keep the current criterion', 'feedback': 'Keep item 3 limited to its current criterion.', 'consequence': ''}]},],
        # AN EDIT ONLY THE INSTRUCTOR CAN MAKE (2026-10-29): the points change asked for, offered as one Approve.
        'consistency_issues': [{'versions': ['A', 'B'], 'message': 'Version B pays the setup line differently from Version A.',
                                'edit': {'lines': [{'version': 'B', 'item_id': 'R1_2', 'points': 5}]}}],
        'parallel_lines': [{'kind': 'same', 'lines': [{'version': v, 'item_id': f'R1_{n}'} for v in 'AB'], 'note': ''} for n in (1, 2, 3, 4)],
        'versions': [{'version': v['version'], 'summary': f"Version {v['version']}: the part-credit lines are alternatives.", 'items': items(v)} for v in sent['problem']['versions']]}


model = FakeHost(script=reviewer).start()
fixture.addCleanup(model.stop)
current = fixture.frq.settings()
roles = current['settings']['tiers']
fixture.frq.save_settings({'version': current['version'], 'settings': {**current['settings'],
    'endpoints': [*current['settings']['endpoints'], endpoint(model, 'qa-text', modality='text')], 'rubric_reviewer': 'qa-text'}})
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
        # The first step also prepares the saved PDF as a template; this fixture has no converter, which is that page's own check.
        page.on('response', lambda response: bad.append((response.status, response.url)) if response.status >= 500 and not response.url.endswith('/templates/prepare') else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        # Where a rubric is imported, a meeting's words that it is NOT final are a warning with the quote (RECORDINGS "A RULING").
        page.route('**/api/recordings/rulings?*', lambda route: route.fulfill(json={**HELD, 'rulings': [], 'not_final': {**SAID, 'quote': 'The rubric is not final yet'}}))
        page.get_by_role('button', name='Upload rubrics', exact=True).click()
        expect(page.locator('#dialog [data-recorded-not-final]')).to_contain_text('A meeting said this rubric is not final. Avery Quill, Tue, Nov 10 at 0:24: “The rubric is not final yet”')
        page.unroute('**/api/recordings/rulings?*')
        expect(page.locator('#dialog')).not_to_contain_text('Optional')  # The check is no longer a choice made while uploading.
        expect(page.locator('.rubric-upload-version')).to_have_count(2)
        # The two part-credit lines add up to 11 of 10 points: by the instructor's rule they must be alternatives.
        file = {'name': '1_Question_18.csv', 'mimeType': 'text/csv', 'buffer': encode(export_rows(points=(10, 6, 5, 0))).encode()}
        for letter in ('A','B'): page.get_by_label(f'Rubric CSVs for Version {letter}', exact=True).set_input_files(file)
        for width in (1450, 1280):
            page.set_viewport_size({'width': width, 'height': 1050})
            page.screenshot(path=str(OUTPUT/f'quality-upload-{width}.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.set_viewport_size({'width':1450,'height':1050})
        page.get_by_role('button', name='Upload and continue', exact=True).click()
        try:
            expect(page.locator('#dialog')).not_to_be_visible(timeout=20000)
        except AssertionError:
            page.screenshot(path=str(OUTPUT/'quality-upload-failure.png'), full_page=True)
            print(page.locator('#dialog').inner_text(), flush=True)
            raise
        # Six steps. The first holds the uploads AND the templates, and nothing is approved there.
        expect(page.locator('.grading-workflow a strong')).to_have_text(['Rubrics & Templates', 'Answer areas', 'Key & Rubric Finalization', 'Grade', 'Review', 'Certify'])
        expect(page.get_by_role('heading', name='Assignment Templates', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Approve rubric', exact=True)).to_have_count(0)
        expect(page.locator('.quality-status')).to_have_count(0)
        assert not model.requests
        # The reviewer reads nothing the instructor has not confirmed on this step, as uploaded or edited and commented (2026-09-24).
        expect(page.locator('[data-step-lead]')).to_contain_text('0 of 2 rubrics confirmed for review.')  # The header says it once.
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=answer-key")
        status = page.locator('.quality-status')
        expect(status).to_contain_text('Confirm each rubric for review in Rubrics & Templates first (2 not confirmed')
        expect(status.get_by_role('button', name='Run rubric check', exact=True)).to_have_count(0)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        page.get_by_role('button', name='Confirm all for rubric review', exact=True).click()
        expect(page.locator('[data-step-lead]')).not_to_contain_text('rubrics confirmed for review')
        expect(page.get_by_role('button', name='Confirm all for rubric review', exact=True)).to_have_count(0)
        assert not model.requests
        # Step three: nothing grades before this is confirmed, and there is no switch to turn it off.
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=answer-key")
        expect(status).to_contain_text('Rubric check required')
        expect(status).to_contain_text('Required before grading')
        # The check that has to run is the step header's one primary; the panel keeps no copy (review fix GS-D1).
        # Its one button is the quick rubric choices' (GRADING "RUBRIC CHOICES", 2026-10-24), quiet beside the check.
        expect(status.get_by_role('button')).to_have_count(1)
        expect(status.get_by_role('button', name='Set choices', exact=True)).to_have_class('text-button')
        onward = page.locator('[data-step-actions]').get_by_role('button', name='Run rubric check', exact=True)
        expect(onward).to_have_class('button primary')
        expect(page.locator('.grading-setup-columns .button.primary:visible')).to_have_count(0)
        onward.click()
        expect(page.locator('.quality-status')).to_contain_text('Queued')
        expect(page.locator('.quality-status').get_by_role('button', name='Cancel this check', exact=True)).to_be_visible()
        expect(page.locator('.grading-workflow [data-pipeline-step="key"]')).to_have_class('is-pending')
        assert not model.requests
        fixture.frq.process_cloud()
        expect(page.get_by_role('button', name='Review and confirm', exact=True)).to_be_visible(timeout=20000)
        expect(page.locator('#dialog')).not_to_be_visible()
        # The review waits in the header and in the panel; the paid re-run waits under Check details (2026-10-02).
        expect(status.get_by_role('button', name='Open the review', exact=True)).to_be_visible()
        expect(status.locator(':scope > .grading-actions').get_by_role('button', name='Run rubric check again', exact=True)).to_have_count(0)
        # RULINGS SAID IN A RECORDING: what a meeting said of this assessment's grading waits in the window with its exact words.
        # Adding one puts its sentence in the chosen line's clarification, the instructor's to edit, recorded only by Confirm;
        # the choice itself is recorded with the recording's item. This window is then closed without confirming.
        settled = []
        page.route('**/api/recordings/rulings?*', lambda route: route.fulfill(json=HELD))
        page.route('**/api/recordings/rec1/items/r1', lambda route: (settled.append(route.request.post_data_json), route.fulfill(json={}))[1])
        page.get_by_role('button', name='Review and confirm', exact=True).click()
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible(timeout=20000)
        said = page.locator('#dialog form [data-recorded-rulings]')
        expect(said).to_have_attribute('open', '')
        expect(said.locator('summary')).to_have_text('Said in your recorded meetings · 1 waiting')
        expect(said.locator('[data-ruling="r1"]')).to_contain_text('Question 18. On question 18, an unsimplified answer earns full credit.')
        expect(said.locator('[data-ruling="r1"]')).to_contain_text('Avery Quill, Tue, Nov 10 at 0:24: “give full credit for an unsimplified answer”')
        expect(said.locator('[data-ruling="r1"] a')).to_have_attribute('href', '#recordings/rec1?t=24000')
        expect(said.locator('[data-ruling-line] option')).to_have_count(9)   # the lines of question 18 in both versions, and "Choose"
        said.get_by_role('button', name='Add to its clarification', exact=True).click()
        expect(page.locator('#dialog .form-error, .toast').filter(has_text='Choose the rubric line this is about first.').first).to_be_visible()
        assert not settled
        said.locator('[data-ruling-line]').select_option(index=2)
        said.get_by_role('button', name='Add to its clarification', exact=True).click()
        expect(said.locator('[data-ruling="r1"] [data-ruling-settled]')).to_have_text('Added to a clarification.')
        expect(said.locator('summary')).to_have_text('Said in your recorded meetings · 1 statement, all settled')
        expect(page.locator('#dialog form .quality-item[data-item-label="Version A · Item 2"] [data-clarification]')).to_have_value(re.compile(r'On question 18, an unsimplified answer earns full credit\.$'))
        assert [(sent['state'], sent['assignment']) for sent in settled] == [('accepted', fixture.plan['id'])], settled
        page.keyboard.press('Escape')   # nothing was confirmed: the next window starts from what stands
        expect(page.locator('#dialog')).not_to_be_visible()
        page.unroute('**/api/recordings/rulings?*')
        page.unroute('**/api/recordings/rec1/items/r1')
        page.get_by_role('button', name='Review and confirm', exact=True).click()
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible(timeout=20000)
        form = page.locator('#dialog form')
        item = lambda n, version='A': form.locator(f'.quality-item[data-item-label="Version {version} · Item {n}"]')
        expect(form.locator('[data-recorded-rulings]')).to_have_count(0)   # no meeting said anything: the window is as it was
        expect(form.locator('.quality-question')).to_have_count(2)
        # The confirmation tick sits beside the Confirm button, visible without scrolling a long window (2026-09-25).
        expect(form.locator('.dialog-footer').get_by_label('I approve these rubrics and their guidance for grading', exact=True)).to_be_in_viewport()
        expect(form.locator('.quality-question > summary').first).to_contain_text('Version A')
        expect(form.locator('.quality-item[open]')).to_have_count(0)
        # What waits on the instructor leads the window, one short marked line each (2026-10-06).
        expect(form.locator('[data-lead-concerns]')).to_have_text('2 reviewer concerns to decide under Decisions. Open concerns never stop you approving.')
        expect(form.locator('[data-lead-clear]')).to_be_hidden()
        expect(form.locator('[data-lead-fixed]')).to_have_text('Faculty fixed 1 finding with one right answer instead of asking: see Fixed for you under Decisions, where each can be undone.')
        fixed = form.locator('[data-quality-fixed]')
        expect(fixed.locator('summary')).to_have_text('Fixed for you · 1')
        expect(fixed.locator('[data-fixed-row]')).to_have_count(1)
        fixed.locator('summary').click()
        expect(fixed.locator('[data-fixed-row]')).to_contain_text("The clarification carries the comment with Version B's own values.")
        expect(fixed.get_by_role('button', name='Undo', exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT / 'quality-fixed-for-you.png'))
        fixed.locator('summary').click()
        # Deciding Version B separately, with a finding fixed for you before its row, shows the version just taken out (not the next decision).
        form.locator('[data-split-versions] > summary:visible').click()
        form.get_by_role('button', name='Decide Version B separately', exact=True).click()
        expect(form.locator('[data-decision-choice]')).to_have_count(3)
        expect(form.locator('[data-decision-applies]:visible')).to_have_text(re.compile(r'^Applies to Version B:'))
        page.keyboard.press('Escape')  # The split is this window's own: the next one starts together again.
        expect(page.locator('#dialog')).not_to_be_visible()
        page.get_by_role('button', name='Review and confirm', exact=True).click()
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible(timeout=20000)
        expect(form.locator('[data-decision-choice]')).to_have_count(2)
        # What the check proposes is counted where it is read first (WHAT THE CHECK PROPOSES, 2026-10-06).
        expect(form.locator('[data-lead-proposed]')).to_have_text('The reviewer proposes 2 clarifications and 2 exclusions for Versions A and B. They are approved with the rubrics; read or edit them under Rubric items.')
        expect(item(2).locator('[data-item-guidance]')).to_have_text('Proposed clarification ' + CLEAR)
        expect(form.locator('[data-overflow-item]')).to_be_hidden()
        expect(form.locator('[data-decision-panel]:visible')).to_have_count(1)
        expect(form.locator('[data-decision-applies]:visible')).to_have_text('Applies to Versions A and B: the reviewer adapts your answer to each.')
        # Every version's copy of the line at once, no tabs: the line named once, one row per version with its clarification and
        # exclusions shown closed, as in Rubric items (2026-10-06).
        reference = form.locator('.quality-decision-reference:visible')
        expect(reference.locator('.quality-reference-line')).to_have_count(1)
        expect(reference.locator('.quality-reference-head')).to_contain_text('Item 3 · 5 points')
        expect(reference.locator('.quality-reference-guidance > summary strong')).to_have_text(['Version A', 'Version B'])
        expect(reference.locator('[data-reference-guidance]')).to_have_text(['No clarification'] * 2)
        expect(reference.locator('[data-reference-exclusions]')).to_have_text(['Cannot combine with items 2'] * 2)
        expect(form.locator('[data-decision-choice="0"] [data-decision-status]')).to_have_text('')
        expect(form.locator('[data-policy-choice]:checked')).to_have_count(0)
        expect(form.locator('[data-decision-feedback="0"]')).to_have_value('')
        expect(form.locator('[data-feedback-box="0"]')).to_be_hidden()  # the answer's words show once an answer is picked
        expect(form.locator('[data-resolve-scope]')).to_have_text('Sends all 2 decisions to the reviewer; it settles them itself. Nothing is approved until you approve.')
        before_feedback = len(model.requests)
        form.get_by_label('Credit the key value', exact=True).check()
        expect(form.locator('[data-decision-feedback="0"]')).to_have_value('The key is right: credit its value on item 3 too.')
        expect(form.locator('[data-feedback-box="0"]')).to_be_visible()
        expect(form.locator('[data-decision-choice="0"] [data-decision-status]')).to_have_text('Answered')
        expect(form.locator('[data-resolve-scope]')).to_have_text('Sends all 2 decisions to the reviewer, with your answer on 1; it settles the others itself. Nothing is approved until you approve.')
        assert len(model.requests) == before_feedback  # GRADING: choosing a policy drafts feedback, never sends or approves it.
        for width in (1450, 1280):
            page.set_viewport_size({'width': width, 'height': 1050})
            page.screenshot(path=str(OUTPUT / f'quality-decisions-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            footer = form.locator('.dialog-footer').bounding_box()
            assert footer['y'] + footer['height'] <= 1050, footer
        page.set_viewport_size({'width': 1450, 'height': 1050})
        # The concern names its line as the rubric beside it does, never by its internal ID (grading-setup-17).
        expect(form).to_contain_text('Item 3 names a value the answer key does not accept.')
        expect(form).not_to_contain_text('R1_3 names')
        expect(form).to_contain_text('1 problem checked, each with all its versions side by side in one request')
        expect(form).to_contain_text('Consistency across versions')  # Both versions were in one request, so their rubrics were compared.
        expect(form).to_contain_text('Version B pays the setup line differently from Version A.')
        expect(form.locator('.quality-question').first).to_contain_text('Version A: the part-credit lines are alternatives.')
        # Each concern opens its question's rubric in a pop-up over the window (2026-09-25), with the lines it names marked and the
        # guidance as the window holds it; closing it leaves the window where it was.
        popup = form.locator('[data-rubric-popover]')
        expect(popup).not_to_be_visible()
        form.locator('[data-show-rubric]').first.click()
        expect(popup).to_be_visible()
        expect(popup.locator('h5')).to_have_text(['Version A · Question 18 · 10 points', 'Version B · Question 18 · 10 points'])
        expect(popup.locator('li.is-named')).to_have_count(2)
        expect(popup.locator('li.is-named').first).to_contain_text('Item 3 · 5 points')
        expect(popup.locator('li').nth(1)).to_contain_text('Clarification: ' + CLEAR)
        expect(popup.locator('li').nth(1)).to_contain_text('Cannot combine with items 3')
        expect(popup.locator('li').first).to_contain_text('Full-credit line: excludes every other item.')
        for width in (1450, 1280):
            page.set_viewport_size({'width': width, 'height': 1050})
            page.screenshot(path=str(OUTPUT/f'quality-rubric-popup-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.set_viewport_size({'width': 1450, 'height': 1050})
        page.keyboard.press('Escape')
        expect(popup).not_to_be_visible()
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible()  # Only the pop-up closed.
        # Inline clarification edits update the same policy form and survive a change of view; no parallel rubric is copied.
        inline = form.locator('.quality-decision-reference:visible .quality-reference-guidance').first
        inline.locator('summary').click()
        inline.get_by_label('Clarification for the AI graders (optional)', exact=True).fill('Draft guidance for item 3.')
        form.get_by_role('button', name='Rubric items (1 question)', exact=True).click()
        line3 = item(3)
        line3.locator('summary').click()
        expect(line3.get_by_label('Clarification for the AI graders (optional)', exact=True)).to_have_value('Draft guidance for item 3.')
        line3.locator('summary').click()
        form.get_by_role('button', name='Decisions (2)', exact=True).click()
        form.get_by_role('button', name='Next decision →', exact=True).click()
        expect(form.locator('[data-no-policy-choices]:visible')).to_be_visible()
        offer = form.locator('[data-edit-offer]:visible')
        expect(offer).to_contain_text('Version B · Question 18 · Item 2: 6 → 5 points')
        expect(offer.get_by_role('button', name='Approve this change', exact=True)).to_be_visible()
        form.get_by_role('button', name='Show rubrics', exact=True).click()  # The consistency finding names both versions.
        expect(popup.locator('h5')).to_have_text(['Version A · Question 18 · 10 points', 'Version B · Question 18 · 10 points'])
        popup.get_by_role('button', name='Close', exact=True).click()
        expect(popup).not_to_be_visible()
        expect(page.locator('#dialog')).to_be_visible()
        # GRADING: one decision at a time; a finding with no choices takes free feedback and retains it across navigation.
        respond = form.locator('[data-quality-respond]')
        expect(respond).to_contain_text('Open concerns never stop you confirming.')
        expect(form.locator('[data-decision-feedback]')).to_have_count(2)
        expect(form.locator('[data-concern-comment]:visible')).to_have_count(0)
        form.locator('[data-decision-feedback="1"]').fill('Keep the points as they are for now.')
        form.get_by_role('button', name='← Previous decision', exact=True).click()
        expect(form.locator('[data-decision-feedback="0"]')).to_have_value('The key is right: credit its value on item 3 too.')
        form.get_by_role('button', name='Next decision →', exact=True).click()
        expect(form.locator('[data-decision-feedback="1"]')).to_have_value('Keep the points as they are for now.')
        expect(form.locator('.quality-decision-reference:visible')).to_contain_text('It concerns the whole rubric of Question 18 · Versions A and B.')
        page.screenshot(path=str(OUTPUT/'quality-comments-1450.png'))
        form.get_by_role('button', name='Resolve concerns', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(page.locator('.quality-status')).to_contain_text('Resolving the concerns you commented on')
        expect(page.locator('.quality-status').get_by_role('button', name='Cancel resolving', exact=True)).to_be_visible()
        fixture.frq.process_cloud()
        expect(page.get_by_role('button', name='Review and confirm', exact=True)).to_be_visible(timeout=20000)
        expect(page.locator('.quality-status')).to_contain_text('The reviewer answered your comments')
        asked = payload_of(model.requests[-1]['body'])
        assert [c.get('instructor_response') for c in asked['concerns']] == ['The key is right: credit its value on item 3 too.', None, 'The key is right: credit its value on item 3 too.', 'Keep the points as they are for now.'], asked['concerns']
        # The fixed finding goes along marked as Faculty's own, for the reviewer to settle as resolved.
        assert asked['concerns'][1]['handled_by_faculty'] == "The clarification carries the comment with Version B's own values.", asked['concerns'][1]
        assert [row['clarification'] for row in asked['problem']['versions'][0]['current']][1] == CLEAR
        assert [row['clarification'] for row in asked['problem']['versions'][0]['current']][2] == 'Draft guidance for item 3.'
        page.get_by_role('button', name='Review and confirm', exact=True).click()
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible(timeout=20000)
        expect(form).to_contain_text('3 resolved, 1 still open')
        expect(form.locator('[data-quality-resolved] > summary')).to_have_text('Resolved concerns · 3')
        expect(form).to_contain_text('Still open: Change the points of Version B in the rubric editor.')
        expect(form.locator('[data-no-policy-choices]:visible')).to_be_visible()
        form.get_by_role('button', name='Rubric items (1 question)', exact=True).click()
        expect(form.locator('.quality-question').first.locator('[data-resolved-change]')).to_be_visible()
        expect(form.locator('.quality-question').nth(1).locator('[data-resolved-change]')).to_have_count(1)
        expect(form.locator('.quality-question').first.locator('[data-resolution-change]')).to_contain_text('changed the clarification of item 3.')
        changed = item(3)
        changed.locator('summary').click()
        expect(changed.get_by_label('Clarification for the AI graders (optional)', exact=True)).to_have_value(RESOLVED)
        expect(changed.locator('[data-before]')).to_have_text('Before resolving the concerns: Draft guidance for item 3.')
        expect(form.locator('[data-concern-comment]')).to_have_count(1)  # Another round is possible on the concern still open ...
        expect(form.locator('[data-concern-comment]')).to_have_value('')
        expect(form).to_contain_text('Keep the points as they are for now.')  # ... beside what you said last time.
        expect(form.locator('[data-quality-resolved]')).to_contain_text('Your comment: The key is right: credit its value on item 3 too.')
        for width in (1450, 1280):
            page.set_viewport_size({'width':width,'height':1050})
            page.screenshot(path=str(OUTPUT/f'quality-resolved-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.set_viewport_size({'width':1450,'height':1050})
        changed.locator('summary').click()
        # Built-in rules: the line worth the whole question and the no-answer line, stated and not editable.
        expect(form.locator('.quality-builtin').first).to_contain_text('Item 1 is the full-credit line and excludes every other item; Item 4 is the no-credit line and excludes every other item.')
        full = item(1)
        expect(full.locator('[data-item-summary]')).to_have_text('Full-credit line: excludes every other item')
        full.locator('summary').click()
        expect(full.locator('[data-pair]')).to_have_count(0)
        expect(full).to_contain_text('This cannot be edited.')
        # The model's clarification is a proposal the instructor edits; its pair can be removed, in either direction.
        part = item(2)
        expect(part.locator('[data-item-summary]')).to_have_text('Cannot combine with items 3')
        expect(part.locator('[data-item-guidance]')).to_have_text('Clarification ' + CLEAR)  # read without opening the line
        expect(item(4).locator('[data-item-guidance]')).to_have_text('No clarification')
        part.locator('summary').click()
        note = part.get_by_label('Clarification for the AI graders (optional)', exact=True)
        expect(note).to_have_value(CLEAR)
        note.fill('Credit the value in any equivalent form.')
        # LINKED VERSIONS: both versions of the problem are one panel; a linked line's copy on Version B sits right under it.
        expect(form.locator('[data-quality-workspace] [data-question-choice]')).to_have_count(1)
        expect(form.locator('.quality-problem:visible .quality-question')).to_have_count(2)
        expect(form.locator('.quality-linked')).to_have_count(4)
        expect(form.locator('.quality-unlinked')).to_have_count(0)
        # Copying is explicit, lands in Version B's own field, and says when the text names values.
        part.get_by_role('button', name='Copy this clarification to Version B', exact=True).click()
        expect(part.locator('[data-copy-status]')).to_have_text('Copied to Version B · Item 2.')
        expect(item(2, 'B').get_by_label('Clarification for the AI graders (optional)', exact=True)).to_have_value('Credit the value in any equivalent form.')
        item(2, 'B').get_by_label('Clarification for the AI graders (optional)', exact=True).fill(CLEAR)  # put back: Version B keeps its own
        # A change of view is never a lost finalization draft.
        form.get_by_role('button', name='Decisions (1)', exact=True).click()
        form.get_by_role('button', name='Rubric items (1 question)', exact=True).click()
        expect(note).to_have_value('Credit the value in any equivalent form.')
        expect(part.locator('[data-pair]')).to_have_count(1)  # Only the other part-credit line: the built-in lines are not offered.
        pair = part.locator('[data-pair]').first
        key = pair.get_attribute('data-pair')
        expect(pair).to_be_checked()
        expect(form.locator('[data-overflow]:visible')).to_have_count(0)  # The reviewer's pairs leave nothing over the question's value.
        pair.uncheck()
        expect(form.locator('[data-mirror-status]')).to_have_text('Also cleared on Version B.')  # mirrored along the "same" links
        assert page.evaluate("key => [...document.querySelectorAll('#dialog [data-pair]')].filter(i=>i.dataset.pair===key).filter(i=>i.dataset.question===document.querySelector('#dialog [data-quality-item]').dataset.qualityQuestion).every(i=>!i.checked)", key)
        expect(part.locator('[data-item-summary]')).to_have_text('No exclusions')
        # Without that pair the part-credit lines can be combined for 11 of 10 points: the dialog says so at once, and will not confirm.
        first_question = form.locator('.quality-question').first
        expect(first_question.locator('[data-overflow]')).to_contain_text('items 2, 3 can be selected together for 11 of 10 points')
        expect(first_question.locator('[data-overflow-chip]')).to_be_visible()
        expect(form.locator('[data-overflow-summary]')).to_contain_text('2 questions still allow more points than they are worth: Version A')
        expect(form.locator('[data-overflow-item]')).to_be_visible()  # a blocker leads the window, with its way to the rubric
        page.get_by_label('I approve these rubrics and their guidance for grading', exact=True).check()
        page.get_by_role('button', name='Confirm and approve the rubrics', exact=True).click()
        expect(form.locator('.form-error')).to_contain_text('still allow more points than they are worth')
        page.get_by_label('I approve these rubrics and their guidance for grading', exact=True).uncheck()
        pair.check()
        expect(form.locator('[data-mirror-status]')).to_have_text('Also set on Version B.')
        expect(form.locator('[data-overflow]:visible')).to_have_count(0)
        expect(part.locator('[data-item-summary]')).to_have_text('Cannot combine with items 3')
        expect(form.locator('[data-overflow-item]')).to_be_hidden()
        for width in (1450, 1280):
            page.set_viewport_size({'width':width,'height':1050})
            page.screenshot(path=str(OUTPUT/f'quality-confirm-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            footer = form.locator('.dialog-footer').bounding_box()
            assert footer['y'] + footer['height'] <= 1050, footer
        page.set_viewport_size({'width':1450,'height':1050})
        page.get_by_role('button', name='Confirm and approve the rubrics', exact=True).click()
        expect(form.locator('.dialog-footer .form-error')).to_contain_text('Open concerns never stop you confirming.')
        expect(page.get_by_label('I approve these rubrics and their guidance for grading', exact=True)).to_be_focused()
        page.get_by_label('I approve these rubrics and their guidance for grading', exact=True).check()
        page.get_by_role('button', name='Confirm and approve the rubrics', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(page.locator('.quality-status')).to_contain_text('Rubrics finalized')
        expect(page.locator('.quality-status')).to_contain_text('4 clarifications')
        status = fixture.frq.rubric_quality(fixture.plan['id'])
        assert status['state'] == 'confirmed'
        # One grouping request, then the problem once with both versions side by side.
        assert [prompt_name(r['body']) for r in model.requests] == ['rubric_quality_group', 'rubric_quality_versions', 'rubric_quality_resolve'], [prompt_name(r['body']) for r in model.requests]
        assert [v['version'] for v in payload_of(model.requests[1]['body'])['problem']['versions']] == ['A', 'B']
        first, second = sorted(status['review']['policy']['questions'], key=lambda q: next(c['exam_version'] for c in status['context']['questions'] if c['question_id'] == q['question_id']))
        assert (first['clarifications'], first['pairs'], second['pairs']) == ({'R1_2': 'Credit the value in any equivalent form.', 'R1_3': RESOLVED}, [['R1_2', 'R1_3']], [['R1_2', 'R1_3']]), (first, second)
        assert status['unresolved'] == []
        assert second['clarifications'] == {'R1_2': CLEAR, 'R1_3': RESOLVED} and first['builtin'] == [{'item_id': 'R1_1', 'rule': 'full_credit'}, {'item_id': 'R1_4', 'rule': 'no_credit'}]
        # One confirmation approved both rubrics, and wrote nothing into them: the record keeps the original wording.
        questions = fixture.uploaded_questions()
        assert all(q['rubric']['approved_at'] for q in questions)
        assert all(not item['excludes'] and 'clarification' not in item for q in questions for item in q['rubric']['rules']['items'])
        page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=rubrics')
        expect(page.locator('[data-rubric-question]').first).to_contain_text('Rubric approved')
        expect(page.locator('[data-rubric-question]').first).not_to_contain_text('any equivalent form')
        for width in (1450, 1280):
            page.set_viewport_size({'width': width, 'height': 1050})
            page.screenshot(path=str(OUTPUT/f'rubrics-complete-{width}.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        # Older links to the check open the step that now holds it.
        page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=rubric-quality')
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('.grading-workflow [aria-current="step"]')).to_contain_text('Key & Rubric Finalization')
        # GRADING: a shared-concern link selects its exact owning question without changing any finalization.
        target = next(q for q in questions if q['exam_version'] == 'B')
        before = fixture.frq.rubric_quality(fixture.plan['id'])
        page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=answer-key&focus={target["question_id"]}&open=quality')
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible()
        expect(page.locator('[data-quality-tab="items"]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator(f'[data-quality-fold="{target["question_id"]}"]')).to_have_attribute('open', '')
        expect(page.locator(f'[data-quality-fold="{target["question_id"]}"]')).to_be_visible()
        assert fixture.frq.rubric_quality(fixture.plan['id']) == before
        page.keyboard.press('Escape')
        page.set_viewport_size({'width':1450,'height':1050})
        page.get_by_role('button', name='Compute settings',exact=True).click()
        page.get_by_role('tab', name='Other models', exact=True).click()
        picker = page.get_by_label('Rubric drafting and review model',exact=True)
        expect(picker).to_have_value('qa-text')
        expect(page.get_by_role('button',name='Browse models for Rubric drafting and review model',exact=True)).to_be_visible()
        # One model drafts and reviews the rubrics; a text-only one can review but not draft from a template's pages.
        expect(page.locator('[data-model-scan-warning="rubric_reviewer"]')).to_contain_text('cannot draft them from a blank template')
        picker.select_option('')
        page.get_by_role('button',name='Save compute settings',exact=True).click()
        expect(page.locator('[data-settings-status]')).to_have_text('All changes saved')
        assert fixture.frq.settings()['settings']['tiers'] == roles
        assert fixture.frq.settings()['settings']['rubric_reviewer'] == ''
        # A mistake in the rubric itself is corrected from the finalization step: one line, a new revision, the confirmation kept.
        page.keyboard.press('Escape')
        page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=answer-key')
        page.reload()
        expect(page.locator('.quality-status')).to_contain_text('Rubrics finalized')
        page.locator(f'[data-question-choice="{first["question_id"]}"]').click()
        lines = page.locator(f'[data-rubric-lines="{first["question_id"]}"]')
        if lines.get_attribute('open') is None:
            lines.locator('summary').click()
        lines.get_by_role('button', name='Correct the wording of item 2', exact=True).click()
        expect(page.locator('#dialog')).to_contain_text('Faculty changes nothing on Gradescope')
        expect(page.locator('#dialog .grading-correct-linked')).to_contain_text('Question 18 · Version B · Item 2')
        expect(page.locator('#dialog [name="also-correct"]:checked')).to_have_count(0)  # never ticked by default
        wording = page.get_by_label('Corrected wording', exact=True)
        original = wording.input_value()
        wording.fill(original + ' (corrected)')
        page.get_by_role('button', name='Save corrected line', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(page.locator('.quality-status')).to_contain_text('Rubrics finalized')  # Nothing to confirm again.
        expect(lines).to_contain_text('(corrected)')
        corrected = fixture.frq.rubric_quality(fixture.plan['id'])
        assert corrected['state'] == 'confirmed' and corrected['review']['id'] != status['review']['id']
        kept = next(q for q in corrected['review']['policy']['questions'] if q['question_id'] == first['question_id'])
        # A corrected line drops the clarification of its old wording; other questions' guidance and the exclusions carry.
        assert kept['clarifications'] == {key: value for key, value in first['clarifications'].items() if key != 'R1_2'}
        assert kept['pairs'] == first['pairs'] and kept['rubric_id'] != first['rubric_id']
        assert next(q for q in corrected['review']['policy']['questions'] if q['question_id'] == second['question_id']) == second
        assert corrected['review']['policy']['carried'][-1]['clarification_dropped'] == first['clarifications']['R1_2']
        fixed = next(q for q in fixture.uploaded_questions() if q['question_id'] == first['question_id'])
        line = next(i for i in fixed['rubric']['rules']['items'] if i['id'] == 'R1_2')
        assert line['description'] == original + ' (corrected)' and '(corrected)' not in line['original_source_text'] and fixed['rubric']['approved_at']
        # LINKED VERSIONS on Rubrics & Templates: a changed comment is offered to the same line of Version B ("same": ticked), the
        # save names both questions it takes out of the finalization, and both versions are confirmed for review in one go.
        page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=rubrics')
        page.reload()
        edit_a = page.locator(f'form[data-rubric-edit="{first["question_id"]}"]')
        page.locator(f'[data-question-choice="{first["question_id"]}"]').click()
        also = edit_a.locator('[data-also-comment="R1_3"]')
        expect(also).to_be_hidden()  # nothing to pass on until the comment changes
        edit_a.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True).fill('Accept the value written as a decimal.')
        expect(also).to_be_visible()
        expect(also).to_contain_text('Question 18 · Version B · Item 3')
        expect(also.locator('input[type=checkbox]')).to_be_checked()
        edit_a.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('#dialog')).to_contain_text('Your rubric finalization covers 2 of the questions you are saving')
        page.get_by_role('button', name='Save and reopen these questions', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        saved = fixture.frq.rubric_quality(fixture.plan['id'])['readiness']
        assert saved[first['question_id']]['comments'].get('R1_3') == saved[second['question_id']]['comments'].get('R1_3') == 'Accept the value written as a decimal.', saved
        assert not saved[first['question_id']]['ready'] and not saved[second['question_id']]['ready']
        page.locator(f'[data-question-choice="{first["question_id"]}"]').click()
        both = edit_a.get_by_role('button', name='Confirm Versions A, B for rubric review', exact=True)
        expect(both).to_be_visible()
        both.click()
        expect(both).to_be_hidden()
        ready = fixture.frq.rubric_quality(fixture.plan['id'])['readiness']
        assert ready[first['question_id']]['ready'] and ready[second['question_id']]['ready'], ready
        page.screenshot(path=str(OUTPUT/'rubrics-linked-comment-1450.png'))
        # Only what is ADDED travels (2026-10-03: copying the whole comment had put Version A's numbers on the other versions); a
        # sentence naming values is flagged, and a rewrite is not passed on at all.
        page.locator(f'[data-question-choice="{first["question_id"]}"]').click()
        comment_a = edit_a.get_by_label('Comment on item 3 for the rubric reviewer (optional)', exact=True)
        comment_a.fill('Accept the value written as a decimal. Also accept 0.5 written as a fraction.')
        expect(also.locator('[data-also-preview]')).to_contain_text('Adds “Also accept 0.5 written as a fraction.” after each ticked version’s own comment.')
        expect(also.locator('[data-also-preview]')).to_contain_text('It names values')
        comment_a.fill('Credit any exact form.')
        expect(also.locator('[data-also-preview]')).to_contain_text('nothing is passed on')
        expect(also.locator('input[type=checkbox]')).to_be_disabled()
        edit_a.get_by_role('button', name='Discard changes', exact=True).click()
        # A flagged grade names both real rubric descriptions and keeps the computed score and selections.
        review_fixture = Quality('test_the_full_credit_line_with_any_other_line_is_flagged_by_rule')
        review_fixture.setUp()
        fixture.addCleanup(review_fixture.doCleanups)
        review_fixture.confirm(review_fixture.check(), [])  # No confirmed pair: the built-in rule alone flags it.
        response, _ = review_fixture.grade(CORRECT, primary=['R1', 'R2'])
        review_server = create_server(review_fixture.app, host='127.0.0.1', port=0)
        fixture.addCleanup(review_server.close)
        threading.Thread(target=review_server.run, daemon=True).start()
        page.goto(f"http://127.0.0.1:{review_server.effective_port}/#grading/{review_fixture.plan['id']}?tab=review&browse=all")
        expect(page.locator('[data-independent-checks]')).to_contain_text('Rubric exclusivity conflict')
        page.locator('[data-independent-checks] > summary').click()
        expect(page.locator('[data-independent-checks]')).to_contain_text('Confirmed rubric exclusions violated')
        expect(page.locator('[data-independent-checks]')).not_to_contain_text('Nothing in the evidence argues against this proposal')
        expect(page.locator('.grading-rubric-choice input:checked')).to_have_count(2)
        assert response['result']['score']['score'] == 10
        page.screenshot(path=str(OUTPUT/'quality-flagged-grade.png'))
        # A CHECK ASKS ONLY WHAT DOES NOT STAND (requirement, 2026-10-05): a version added after A and B were confirmed is checked
        # beside them. The step names it, and the window marks A and B "not asked again"; confirming keeps them as they were.
        added = RubricUploads('test_eight_csvs_create_exact_drafts_from_empty_plan')
        added.setUp()
        fixture.addCleanup(added.doCleanups)
        settings = added.frq.settings()
        added.frq.save_settings({'version': settings['version'], 'settings': {**settings['settings'],
            'endpoints': [*settings['settings']['endpoints'], endpoint(model, 'qa-text', modality='text')], 'rubric_reviewer': 'qa-text'}})
        with patch.object(added.frq, 'chart_versions', return_value=['A', 'B']):
            for letter in 'AB':
                added.upload(exam_version=letter)
            confirm_for_review(added.frq, added.plan['id'])
            first = added.frq.rubric_quality(added.plan['id'])
            added.frq.queue_rubric_quality(added.plan['id'], {'request_id': 'first-check', 'input_sha256': first['input_sha256']})
            added.frq.process_cloud()
            first = added.frq.rubric_quality(added.plan['id'])
            confirmed = added.frq.confirm_rubric_quality(added.plan['id'], {'version': first['version'], 'check_id': first['check']['id'], 'input_sha256': first['input_sha256'],
                'confirm_review': True, 'questions': first['check']['result']['questions']})
            assert confirmed['state'] == 'confirmed'
        added.enterContext(patch.object(added.frq, 'chart_versions', return_value=['A', 'B', 'C']))
        added.upload(exam_version='C')
        confirm_for_review(added.frq, added.plan['id'])
        added_server = create_server(added.app, host='127.0.0.1', port=0)
        fixture.addCleanup(added_server.close)
        threading.Thread(target=added_server.run, daemon=True).start()
        model.requests.clear()
        page.goto(f"http://127.0.0.1:{added_server.effective_port}/#grading/{added.plan['id']}?tab=answer-key")
        status = page.locator('.quality-status')
        expect(status).to_contain_text('Only Version C is checked, beside the confirmed Versions A and B as the reference.')
        page.locator('[data-step-actions]').get_by_role('button', name='Check Version C', exact=True).click()
        expect(status).to_contain_text('Queued')
        added.frq.process_cloud()
        page.get_by_role('button', name='Review and confirm', exact=True).click(timeout=20000)
        expect(page.get_by_role('heading', name='Finalize the rubrics', exact=True)).to_be_visible()
        form = page.locator('#dialog form')
        expect(form.locator('[data-kept]')).to_have_text(['Confirmed earlier, not asked again'] * 2)
        expect(form.locator('[data-checked]')).to_have_text(['Checked now: 1 clarification and 1 exclusion proposed'])
        expect(form.locator('[data-settled]')).to_have_count(2)
        expect(form.locator('.quality-check-summary')).to_contain_text('Only the rubrics without a standing confirmation were checked (Version C)')
        # Nothing to decide: the window says so first, names what was checked, and shows the rubric lines without a Decisions view;
        # a short list has no search or filter (2026-10-06).
        # WHAT THE CHECK PROPOSES (requirement, 2026-10-06): the window says
        # the reviewer found nothing, counts what it proposes for the version it checked, and marks those lines.
        expect(form.locator('[data-lead-clear]')).to_have_text('Nothing needs a decision: the reviewer found no errors and no differences between the versions.')
        expect(form.locator('[data-lead-proposed]')).to_have_text('The reviewer proposes 1 clarification and 1 exclusion for Version C. They are approved with the rubrics; read or edit them under Rubric items.')
        # THE WINDOW SHOWS ONLY THE DECISIONS (2026-10-24): it opens on Decisions, which says nothing waits; the guidance is one click away.
        expect(form.locator('[data-quality-concerns] [data-no-decisions]')).to_be_visible()
        expect(form.locator('[data-quality-items]')).to_be_hidden()
        form.get_by_role('button', name='Rubric items (1 question)', exact=True).click()
        expect(item(2, 'C').locator('[data-item-guidance]')).to_have_text('Proposed clarification ' + CLEAR)
        expect(item(2, 'A').locator('[data-item-guidance]')).to_have_text('Clarification ' + CLEAR)  # confirmed earlier: not a proposal
        line = item(2, 'C')
        expect(line.locator('[data-pair]')).to_have_count(0)  # a line's ticks are drawn when it is opened (2026-10-06)
        line.locator('summary').click()
        expect(line.locator('[data-pair]:checked')).to_have_count(1)
        line.locator('summary').click()
        expect(form.locator('[data-lead-needs]')).to_be_hidden()
        expect(form.locator('[data-lead-scope]')).to_have_text('Only Version C was checked. Versions A and B keep the guidance you confirmed.')
        expect(form.locator('.quality-review-tabs')).to_be_visible()
        expect(form.locator('[data-quality-items]')).to_be_visible()
        expect(form.get_by_label('Find a question', exact=True)).to_be_hidden()
        expect(form.locator('.quality-version')).to_have_count(3)
        expect(form.locator('.quality-version[open]')).to_have_count(0)  # nothing in them waits on you
        page.screenshot(path=str(OUTPUT/'quality-added-version-1450.png'))
        page.get_by_label('I approve these rubrics and their guidance for grading', exact=True).check()
        page.get_by_role('button', name='Confirm and approve the rubrics', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(status).to_contain_text('Rubrics finalized')
        assert [prompt_name(r['body']) for r in model.requests] == ['rubric_quality_group', 'rubric_quality_added'], [prompt_name(r['body']) for r in model.requests]
        kept = {q['question_id']: q for q in confirmed['review']['policy']['questions']}
        after = added.frq.rubric_quality(added.plan['id'])['review']['policy']['questions']
        assert {q['question_id']: q for q in after if q['question_id'] in kept} == kept
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Rubric finalization browser passed: per-version upload, six steps, mandatory check, a rubric pop-up per concern, concerns resolved with comments, built-in exclusions, edited clarification and pairs, one confirmation approving the rubrics, original wording kept, one line corrected with the confirmation carried, linked versions (one decision, one panel, copy, mirror, comment and confirmation across versions), model settings and desktop widths, and a version added later checked beside the confirmed ones.')
finally:
    server.close()
    fixture.doCleanups()
