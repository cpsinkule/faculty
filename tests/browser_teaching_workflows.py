"""TEACHING-WORKFLOWS: real browser, synthetic graded course and fake services."""
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from fake_openai import FakeHost
from test_question_bank import configure_model
from test_grading_certification import Certify, tearDownModule


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    Certify.setUpClass()
    fixture = Certify()
    fixture.setUp()
    try:
        fixture.certify()
        app = fixture.app
        fake = FakeHost().start()
        fixture.addCleanup(fake.stop)
        configure_model(fixture.frq, fake)
        bank = app.extensions['question_bank']
        definition = fixture.client.get('/api/quiz-drafts/options').json['courses'][0]['definition_id']
        parent = None
        for level in ('chapter', 'section', 'topic', 'objective'):
            parent = bank.add_node(definition, {'request_id': str(uuid.uuid4()), 'level': level,
                'parent_id': parent, 'title': 'Interpret a linear equation'})['node_id']
        app.extensions['student_support'].save({'request_id': 'browser-followup', 'kind': 'followup',
            'enrollment_id': fixture.enrollment, 'title': 'Discuss the quiz', 'state': 'needs_reply'})
        server = create_server(app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: (errors.append(str(error)), print('Browser error:', error, flush=True)))
                page.set_default_timeout(10000)
                page.goto(f'{address}/#follow-ups')
                root = page.locator('#student-support-section')
                expect(root.locator('.support-review').get_by_role('heading', name='Discuss the quiz', exact=True)).to_be_visible()
                root.get_by_role('button', name='Edit follow-up…', exact=True).click()
                page.screenshot(path=str(Path.cwd() / 'followup-edit.png'))
                dialog = page.locator('#dialog')
                expect(dialog.locator('#support-form')).to_be_visible(timeout=10000)
                dialog.locator('select[name=state]').select_option('resolved')
                dialog.get_by_role('button', name='Save', exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(root.get_by_text('Discuss the quiz', exact=True)).to_have_count(0)
                root.get_by_role('button', name='Office hours', exact=True).click()  # the shared navigation row (ACTION DESK)
                root.get_by_role('button', name='Schedule office hours', exact=True).click()
                dialog.get_by_role('combobox', name='Student', exact=True).fill('Alice')
                dialog.get_by_role('option').filter(has_text='Alice').first.click()
                dialog.get_by_label('Purpose', exact=True).fill('Review the quiz privately')
                dialog.get_by_label('Date', exact=True).fill((datetime.now(ZoneInfo('America/Chicago')).date() + timedelta(days=3)).isoformat())  # ahead of the run date: office hours already past are not listed
                dialog.get_by_label('Location', exact=True).fill('Office 101')
                dialog.get_by_role('button', name='Save', exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(root.get_by_text('Review the quiz privately', exact=True)).to_be_visible()
                root.get_by_role('button', name='Update', exact=True).click()
                expect(dialog).to_be_visible()
                expect(dialog.get_by_role('heading', name='Update office hours')).to_be_visible()
                dialog.locator('select[name=state]').select_option('attended')
                dialog.get_by_role('button', name='Save', exact=True).click()
                expect(dialog).not_to_be_visible()
                root.locator('[data-support-tab="all"]').click()
                expect(root.locator('.support-card .chip')).to_have_text('Attended')
                page.screenshot(path=str(Path.cwd() / 'office-hours.png'), full_page=True)
                # Palette student actions open the existing record without a student URL.
                page.keyboard.press('Control+k')
                page.locator('[data-palette-input]').fill('Alice')
                page.locator('.palette [role="option"]').filter(has_text='Alice').first.click()
                expect(dialog).to_be_visible()
                expect(dialog).to_contain_text('Alice')
                assert fixture.enrollment not in page.url
                page.keyboard.press('Escape')
                page.goto(f'{address}/#teaching-review/{fixture.aid}')
                review = page.locator('#teaching-review-section')
                expect(review.get_by_role('heading', name='What should I revisit?')).to_be_visible()
                expect(review.locator('.support-card')).to_have_count(4)
                expect(review.get_by_role('button', name='Draft review lesson', exact=True)).to_be_enabled()
                page.screenshot(path=str(Path.cwd() / 'teaching-review.png'), full_page=True)
                review.get_by_role('link', name='Grade delivery', exact=True).click()
                for width in (1440, 1280):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    page.screenshot(path=str(Path.cwd() / f'grade-delivery-folded-{width}.png'), full_page=True)
                # Item 10 (2026-10-18): the three Canvas lines carry one gradebook link, and "Review sending grades" opens this page's
                # own Gradescope details in place: the address and the tab stay on Grade delivery.
                checklist = review.locator('.delivery-checklist')
                gradebook_links = checklist.locator('a[href^="#gradebook/"]')
                expect(gradebook_links).to_have_count(1)
                expect(checklist.locator('dl > div').filter(has_text='Recorded locally').locator('a[href^="#gradebook/"]')).to_have_count(1)
                gradescope_fold = review.locator('[data-delivery-details="gradescope"]')
                expect(gradescope_fold).not_to_have_attribute('open', '')
                before = page.url
                checklist.get_by_role('link', name='Review sending grades', exact=True).click()
                expect(gradescope_fold).to_have_attribute('open', '')
                expect(gradescope_fold.locator('summary')).to_be_focused()
                assert page.url == before, page.url
                gradescope_fold.locator('summary').click()  # folded again; the address asks for it as well
                page.goto(f'{address}/#grade-delivery/{fixture.aid}?open=gradescope')
                expect(gradescope_fold).to_have_attribute('open', '')
                gradescope_fold.locator('summary').click()
                expect(gradescope_fold).not_to_have_attribute('open', '')
                review.get_by_text('Gradebook and Canvas details', exact=True).click()
                expect(review).to_contain_text('2 cells without a send receipt')
                review.get_by_text('Gradescope details', exact=True).click()
                review.get_by_role('button', name='Check Gradescope now', exact=True).click()
                expect(review).to_contain_text('Nothing was published.')
                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): the narrowest desktop window does not scroll sideways.
                page.set_viewport_size({'width': 1280, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.screenshot(path=str(Path.cwd() / 'grade-delivery-1280.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.goto(f'{address}/#documents?quiz=new')
                expect(dialog.locator('#quiz-draft-create')).to_be_visible()
                dialog.get_by_label('Use an assessment blueprint').check()
                expect(dialog.locator('[data-blueprint-slot]')).to_have_count(1)
                dialog.get_by_role('button', name='Add question', exact=True).click()
                expect(dialog.locator('[data-blueprint-slot]')).to_have_count(2)
                expect(dialog).to_contain_text('Blueprint total: 10 points')
                assert not errors, errors
                page.screenshot(path=str(Path.cwd() / 'quiz-blueprint.png'), full_page=True)
                browser.close()
        finally:
            server.close()
    finally:
        fixture.doCleanups()
        tearDownModule()
    print('Teaching workflows browser checks passed.')


if __name__ == '__main__':
    main()
