"""ASSISTANT: explicit preparation controls at both supported desktop widths; synthetic only."""
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from app import create_app
from browser_course_pages import seed_course
from rosters import connect
from test_storage import HEADERS


class Engine:
    name = 'test'
    def close(self):
        pass
    def stop(self):
        pass
    def turn(self, c, text):
        result, event = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
            'body': {'title': 'Review grading', 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'},
            'summary': 'Add a reminder to review grading'})
        assert json.loads(result)['outcome'] in ('waiting_for_approval', 'not_repeated'), result
        c.tool_event(event)
        c.emit('assistant', text='Review the proposed reminder. Nothing has been changed.')


def main():
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(str(Path(folder) / 'data'))
        seed_course(app.test_client(), sections=[507, 508])
        service = app.extensions['assistant']
        with connect(service.database) as db:
            term, definition = db.execute('SELECT id,definition_id FROM course_terms').fetchone()
            section = db.execute('SELECT id FROM course_sections WHERE term_id=?', (term,)).fetchone()[0]
        assignment = app.extensions['documents'].create_assignment(definition, 'readiness-example', 'Readiness example')
        created = app.test_client().post('/api/exams', json={'term_id': term, 'assignment_id': assignment['id'],
                                                           'section_ids': [section]}, headers=HEADERS)
        assert created.status_code == 201, created.json
        exam = created.json['id']
        snapshot = {'term_id': term, 'title': 'Synthetic grade reconciliation', 'page': '#overview', 'needs_review': True, 'version': 1}
        server = create_server(app, host='127.0.0.1', port=0, threads=8)
        threading.Thread(target=server.run, daemon=True).start()
        errors = []
        try:
            with patch('assistant.conversations.config.assistant', return_value={'name': 'Synthetic', 'model': 'test'}), \
                 patch('assistant.conversations.engine_for', side_effect=lambda _: Engine()), \
                 patch.object(service.followups, 'endpoint', return_value={'name': 'Synthetic', 'model': 'test'}), \
                 patch.object(service.workflows, 'observe', side_effect=lambda *a: dict(snapshot)), sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.clock.install()
                page.add_init_script("window.cspViolations=[]; document.addEventListener('securitypolicyviolation', e => cspViolations.push(e.violatedDirective));")
                page.goto(f'http://127.0.0.1:{server.effective_port}/#overview')
                page.locator('.assistant-launch').click()
                panel = page.locator('#assistant-panel')
                panel.get_by_role('tab', name='Automation', exact=True).click()
                expect(panel.locator('[data-assistant-automation]').get_by_text('Morning Canvas sync', exact=True)).to_be_visible()
                expect(panel.locator('[data-assistant-automation]').get_by_text('Automatic grading', exact=True)).to_be_visible()
                panel.get_by_role('tab', name='Saved').click()
                saved = panel.locator('[data-assistant-workspace]')
                expect(saved).to_be_visible()
                saved.get_by_text('Prepare teaching work', exact=True).click()
                form = saved.locator('[data-preparation-form]')
                form.locator('[name=mode]').select_option('automatic')
                form.locator('[name=lead_days]').fill('4')
                expect(form.locator('[name=mode] option:checked')).to_have_text('When the records change')
                form.locator('[name=target]').select_option('reconciliation:' + term)
                expect(form.locator('[name=mode] option:checked')).to_have_text('Daily at 5:00 AM Central')
                expect(form.locator('[name=lead_days]')).to_be_hidden()
                expect(form.locator('[name=lead_days]')).to_be_disabled()
                form.locator('[name=target]').select_option('readiness:' + exam)
                expect(form.locator('[name=lead_days]')).to_be_visible()
                expect(form.locator('[name=lead_days]')).to_have_value('4')
                form.locator('[name=mode]').select_option('once')  # a one-off run has no reminder (2026-10-09 review)
                expect(form.locator('[name=lead_days]')).to_be_hidden()
                expect(form.locator('[name=lead_days]')).to_be_disabled()
                form.locator('[name=mode]').select_option('automatic')
                form.locator('[name=target]').select_option('reconciliation:' + term)
                form.get_by_role('button', name='Review automatic preparation').click()
                expect(form.locator('[data-preparation-preview]')).to_contain_text('Enable this rule?')
                expect(form.locator('[data-preparation-preview]')).to_contain_text('5:00 AM Central')
                assert service.workflows.listing()['workflows'] == []
                form.get_by_role('button', name='Enable automatic preparation').click()
                expect(panel.get_by_role('button', name='Pause', exact=True)).to_be_visible()
                panel.get_by_role('button', name='Pause', exact=True).click()
                expect(panel.get_by_role('button', name='Resume', exact=True)).to_be_visible()
                panel.get_by_role('button', name='Resume', exact=True).click()
                expect(panel.get_by_role('button', name='Pause', exact=True)).to_be_visible()
                with connect(service.database) as db:
                    due = db.execute('SELECT next_check FROM assistant_workflows').fetchone()[0]
                with patch('assistant.workflows.time.time', return_value=due):
                    assert service.workflows.process_one()
                assert service.followups.dispatch_one()
                expect(panel.get_by_role('button', name='Review changes')).to_be_visible(timeout=35000)
                retry = panel.locator('[data-prepare-again] textarea')
                retry.fill('Keep my unsent guidance while the queue refreshes.')
                page.wait_for_timeout(5200)
                expect(retry).to_have_value('Keep my unsent guidance while the queue refreshes.')
                # A Prepare again that got no answer is retried under the same request ID, though the timed patch of the runs has
                # redrawn its form between the two tries (the five-second patch rewrites a form's attributes to its markup).
                sent = []
                def lost(route):
                    if route.request.method != 'POST':
                        route.fallback()
                        return
                    sent.append(json.loads(route.request.post_data)['request_id'])
                    route.fulfill(status=500, content_type='application/json', body=json.dumps({'error': 'The answer was lost.'}))
                page.route('**/api/assistant/preparations', lost)
                panel.get_by_role('button', name='Prepare again').click()
                expect(panel.locator('[data-prepare-again] .form-error')).to_be_visible()
                with page.expect_response(lambda r: r.url.endswith('/api/assistant/workflows') and r.request.method == 'GET'):
                    page.clock.run_for(31000)
                page.wait_for_timeout(300)
                panel.get_by_role('button', name='Prepare again').click()
                expect(panel.locator('[data-prepare-again] .form-error')).to_be_visible()
                assert len(sent) == 2 and sent[0] == sent[1], sent
                page.unroute('**/api/assistant/preparations', lost)
                for width in (1440, 1280):
                    page.set_viewport_size({'width': width, 'height': 900})
                    page.screenshot(path=str(Path.cwd() / f'assistant-preparation-{width}.png'))
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                panel.get_by_role('button', name='Review changes').click()
                expect(panel.locator('[data-assistant-changes]')).to_be_visible()
                assert not errors, errors
                assert page.evaluate('window.cspViolations') == []
                browser.close()
        finally:
            server.close()
    print('Assistant preparations: explicit opt-in, pause/resume, review, retained input, desktop layout and CSP passed.')


if __name__ == '__main__':
    main()
