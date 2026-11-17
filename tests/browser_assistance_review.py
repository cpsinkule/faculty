"""ASSISTANT / CANVAS: conflict handoff preserves the dialog and unsaved resolution. Fakes only."""
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_canvas_content import ContentBase


def main():
    fixture = ContentBase()
    fixture.setUp()
    flags = []
    class Engine:
        name = 'synthetic'
        def close(self): pass
        def stop(self): pass
        def turn(self, c, text):
            flags.append(c.session.get('_review_only'))
            c.emit('assistant', text='Keep the useful information from both versions in the reviewed draft.')
    try:
        fixture.start()
        revision = fixture.client.get(fixture.base).json['revision']
        remote = next(p for p in fixture.fake.pages[fixture.a] if p['title'] == 'Office Hours')
        remote['body'] = '<p>Hours changed in Canvas</p>'
        group = fixture.client.get('/api/state').json['course_groups'][0]['id']
        server = create_server(fixture.app, host='127.0.0.1', port=0, threads=8)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with patch('assistant.conversations.config.assistant', return_value={'name': 'Synthetic', 'model': 'test'}), \
                 patch('assistant.conversations.engine_for', side_effect=lambda _: Engine()), sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.effective_port}/#course/{group}/home/canvas')
                page.locator('[data-cc-push-open]').click()
                dialog = page.locator('#dialog')
                expect(dialog.locator('[data-conflict-ask]').first).to_be_visible(timeout=30000)
                dialog.get_by_text('Compare changes', exact=True).first.click()
                text = dialog.locator('[data-conflict-value]').first
                text.fill('<p>Keep my unsaved resolution</p>')
                dialog.locator('[data-conflict-ask]').first.click()
                panel = page.locator('#assistant-panel')
                expect(panel).to_be_visible()
                expect(panel).to_contain_text('Keep the useful information', timeout=15000)
                expect(panel.locator('[data-assistant-chat]')).to_contain_text('Help resolve the Canvas conflict for Office Hours.')
                expect(panel).not_to_contain_text('Hours changed in Canvas')
                assert flags == [True]
                assert dialog.evaluate('(el) => el.open')
                expect(text).to_have_value('<p>Keep my unsaved resolution</p>')
                for width in (1440, 1280):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.screenshot(path=str(Path.cwd()/f'canvas-assistant-review-{width}.png'))
                panel.locator('[data-assistant="close"]').click()
                expect(dialog).to_be_visible()
                dialog.locator('[data-conflict-keep]').first.click()
                # CANVAS (2026-10-09): choosing a text keeps the window open; the instructor adds it to the draft explicitly.
                dialog.get_by_role('button', name='Add 1 text to the draft').click()
                expect(dialog).to_be_hidden()
                expect(page.locator('[data-cc-savebar]')).to_contain_text('Unsaved changes')
                assert fixture.client.get(fixture.base).json['revision'] == revision
                assert remote['body'] == '<p>Hours changed in Canvas</p>'
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    finally:
        fixture.doCleanups()
    print('Conflict analysis preserved the open dialog, unsaved text and explicit Save/Push.')


if __name__ == '__main__':
    main()
