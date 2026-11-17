"""Every tile node opens the matching task in the shared workflow navigation."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_templates import AssignmentTemplateWorkflow
from test_grading_locations import confirm_templates

fixture = AssignmentTemplateWorkflow('test_uploaded_reference_waits_for_warning_before_any_reading')
fixture.setUp()
fixture.upload()
fixture.template()
confirm_templates(fixture.frq, fixture.plan['id'])
before = fixture.uploaded_questions()
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results'); output.mkdir(exist_ok=True)
read_steps = '''nodes => nodes.map(node => ({key: node.dataset.pipelineStep,
    label: node.querySelector('strong').textContent, href: node.querySelector('a').getAttribute('href')}))'''


# The step graph (DESIGN-LANGUAGE §4, grading-guide.js pipelineMarkup): numbered steps, each with its label and a status word or
# progress fraction; the current step is the one link with aria-current="step", under a 3px rust rule with a rust numeral.
# The colours only repeat what the words and aria-current say.
PROBE = '''token => { const probe = document.createElement('span'); probe.style.color = `var(${token})`;
    document.body.append(probe); const value = getComputedStyle(probe).color; probe.remove(); return value; }'''


def ink(page, token):
    return page.evaluate(PROBE, token)


def check_step(link, number, current):
    page = link.page
    expect(link.locator('.grading-pipeline-number')).to_have_text(str(number))
    expect(link.locator('strong')).to_have_count(1)
    expect(link.locator('small:is(.grading-pipeline-status, .grading-pipeline-fraction)')).to_have_count(1)
    if current:
        expect(link).to_have_attribute('aria-current', 'step')
        expect(link).to_have_css('border-top-width', '3px')
        # The step on screen is ink (where you are), never the needs-you orange (DESIGN-LANGUAGE §1.2, Ledger).
        expect(link).to_have_css('border-top-color', ink(page, '--ink'))
        expect(link.locator('.grading-pipeline-number')).to_have_css('color', ink(page, '--ink'))
        expect(link.locator('.grading-pipeline-number')).to_have_css('font-style', 'normal')
    else:
        expect(link).not_to_have_attribute('aria-current', 'step')
        expect(link.locator('.grading-pipeline-number')).to_have_css('font-style', 'normal')


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(15000)
        errors, bad, writes = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.on('request', lambda request: writes.append((request.method, request.url)) if request.method not in ('GET', 'HEAD') else None)
        for _ in (1,):
            # The Grading index is retired (2026-10-18): the assignment's own page carries every step in its graph (the tabs show theirs).
            page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=rubrics')
            card = page.locator('[data-grading-workflow]')
            expect(card.locator('[data-pipeline-step]')).to_have_count(6)
            steps = card.locator('[data-pipeline-step]').evaluate_all(read_steps)
            assert [step['label'] for step in steps] == ['Rubrics & Templates', 'Answer areas', 'Key & Rubric Finalization', 'Grade', 'Review', 'Certify'], steps
            # Inside the assignment (ASSIGNMENT-WORKSPACE "ONE NAVIGATION", 2026-10-05) its tabs are the only navigation: the page's graph
            # shows only the steps of the tab on screen, unnumbered, and no "Step N of 6" line; the tab is the current one.
            area_of = {'rubrics': 'Prepare', 'locations': 'Prepare', 'key': 'Prepare', 'grade': 'Grade', 'review': 'Grade', 'export': 'Certify'}
            for step in steps:
                page.goto(address + '/' + step['href'])
                nav = page.locator('[data-grading-workflow] .grading-workflow')
                selected = nav.locator(f'[data-pipeline-step="{step["key"]}"] a')
                expect(selected).to_have_attribute('aria-current', 'step')
                expect(nav.locator('[aria-current="step"]')).to_have_count(1)
                expect(page.locator('[data-assignment-nav] [aria-current="page"]')).to_have_text(area_of[step['key']])
                page.mouse.move(0, 0)
                for other in steps:
                    shown = nav.locator(f'[data-pipeline-step="{other["key"]}"]')
                    expect(shown).to_be_visible() if area_of[other['key']] == area_of[step['key']] != 'Certify' else expect(shown).to_be_hidden()
                expect(nav.locator('.grading-pipeline-number').first).to_be_hidden()
                assert nav.locator('[data-pipeline-step]').evaluate_all(read_steps) == steps
                expect(page.locator('.grading-workflow-position')).to_be_hidden()
                if step['key'] == 'rubrics':
                    expect(page.locator('.rubric-upload-first')).to_contain_text('problem numbers')
                    expect(page.get_by_role('heading', name='Rubrics', exact=True)).to_be_visible()
                    # Uploads and templates share the step; approval belongs to the rubric finalization.
                    expect(page.get_by_role('heading', name='Assignment Templates', exact=True)).to_be_visible()
                    expect(page.get_by_role('button', name='Approve rubric', exact=True)).to_have_count(0)
                    # GRADING / ASSIGNMENT-WORKSPACE (2026-10-03): the step also shows each question's wording and solution
                    # with its existing answer-area controls; confirming the areas stays its own step (below).
                    expect(page.get_by_role('heading', name='Question and solution', exact=True)).to_be_visible()
                    expect(page.locator('[data-location-check]')).to_have_count(1)
                elif step['key'] == 'key':
                    expect(page.get_by_role('heading', name='Answer key', exact=True)).to_be_visible()
                    expect(page.locator('.quality-status')).to_contain_text('Required before grading')
                elif step['key'] == 'locations':
                    expect(page.get_by_role('heading', name='Answer areas', exact=True)).to_be_visible()
            # Old dialog links keep the correct step selected after closing and reload.
            for action, key in [('answer-key', 'key'), ('quality', 'key'), ('templates', 'rubrics')]:
                page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=rubrics&open={action}')
                selected = page.locator(f'.grading-workflow [data-pipeline-step="{key}"] a')
                expect(selected).to_have_attribute('aria-current', 'step')
                if action == 'answer-key':
                    expect(page.locator('#dialog')).to_be_visible()
                    page.locator('#dialog').get_by_role('button', name='Close', exact=True).click()
                page.reload()
                expect(selected).to_have_attribute('aria-current', 'step')
                expect(page.locator('#dialog')).not_to_be_visible()
        for old_tab, key, tab in (('approve', 'rubrics', 'rubrics'), ('templates', 'rubrics', 'rubrics'), ('rubric-quality', 'key', 'answer-key')):
            page.goto(address + f'/#grading/{fixture.plan["id"]}?tab={old_tab}')
            expect(page.locator(f'.grading-workflow [data-pipeline-step="{key}"] a')).to_have_attribute('aria-current', 'step')
            expect(page.get_by_role('heading', name='Rubrics' if key == 'rubrics' else 'Answer key', exact=True)).to_be_visible()
            assert f'tab={tab}' in page.url
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): the graph at a desktop width, on Prepare's own steps (Certify
        # shows none inside the assignment).
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.goto(address + f'/#grading/{fixture.plan["id"]}?tab=answer-key')
        selected = page.locator('.grading-workflow [aria-current="step"]')
        expect(selected).to_contain_text('Key & Rubric Finalization')
        box = selected.bounding_box()
        assert 0 <= box['x'] < box['x'] + box['width'] <= 1440 + 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        # Each step's numeral repeats its saved state, independently of which page is selected: done (and started) slate, failed
        # red, not started faint; the step on screen ink.
        states = page.locator('.grading-workflow .grading-pipeline li').evaluate_all('''items => items.map(item => ({
            state: [...item.classList].find(name => name.startsWith('is-')),
            current: item.querySelector('a').getAttribute('aria-current') === 'step',
            number: getComputedStyle(item.querySelector('.grading-pipeline-number')).color}))''')
        expected = {'is-done': ink(page, '--pipeline-done-ink'), 'is-pending': ink(page, '--pipeline-done-ink'),
                    'is-failed': ink(page, '--pipeline-failed-ink'), 'is-idle': ink(page, '--pipeline-future-ink')}
        assert len(states) == 6 and all(row['number'] == (ink(page, '--ink') if row['current'] else expected[row['state']]) for row in states), states
        check_step(selected, 3, True)
        page.screenshot(path=str(output / 'grading-workflow-1440.png'), full_page=True)
        first = page.locator('.grading-workflow [data-pipeline-step="rubrics"] a')
        # Hover underlines the step's label (never the needs-you orange, DESIGN-LANGUAGE §1.2) and never moves the current step.
        resting = first.locator('strong').evaluate('el => getComputedStyle(el).textDecorationLine')
        assert resting == 'none', resting
        first.hover()
        expect(first.locator('strong')).to_have_css('text-decoration-line', 'underline')
        assert first.locator('strong').evaluate('el => getComputedStyle(el).color') != ink(page, '--accent')
        expect(selected).to_have_attribute('aria-current', 'step')
        page.mouse.move(0, 0)
        expect(first.locator('strong')).to_have_css('text-decoration-line', resting)
        page.keyboard.press('Tab')
        first.focus()
        expect(first).to_have_css('outline-style', 'solid')
        first.press('Enter')
        expect(page.locator('.grading-workflow [data-pipeline-step="rubrics"] a')).to_have_attribute('aria-current', 'step')
        # The graph is drawn again for the new step; keyboard focus stays on the step link just followed.
        expect(page.locator('.grading-workflow [data-pipeline-step="rubrics"] a')).to_be_focused()
        check_step(page.locator('.grading-workflow [data-pipeline-step="rubrics"] a'), 1, True)
        # Forced colours keep the current step's 3px rule (components.css draws it in the system highlight).
        page.emulate_media(forced_colors='active')
        expect(page.locator('.grading-workflow [aria-current="step"]')).to_have_css('border-top-width', '3px')
        page.emulate_media(forced_colors='none')
        assert not writes, writes
        assert fixture.uploaded_questions() == before, 'Navigation must not change questions, crops or rubric approval'
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Workflow navigation passed: every tile/page step, numbered steps with labels and status words, the current step marked, state colours, hover and focus, focus kept on a followed step link, forced colors, legacy links, reload and keyboard.')
finally:
    server.close()
    fixture.doCleanups()
