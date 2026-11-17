"""Actual print PDFs for all reviewed floor plans and the three workbook rooms.

Uses retained room profiles with synthetic names in a temporary app. window.print
is stubbed; Chromium page.pdf renders the real @page rules without printing paper.
ILCB 111 and HECC 105 also exercise saved exam letters in both print layouts.
Run: .venv/bin/python tests/browser_seating_print.py
"""
import io
import re
import tempfile
import threading
import sys
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from app import create_app
from room_pdf_import import source_index
from test_room_pdf_import import chart as profile
from test_rooms import fixture
from test_rosters import csv_bytes
from test_seating import HEADERS, student_row


def seed(app):
    client = app.test_client()
    result = client.post('/api/courses', json={'name': 'Calculus', 'code': 'MATH 140', 'term': 'Fall', 'year': 2026, 'sections': [507]}, headers=HEADERS)
    assert result.status_code == 201, result.json
    state = client.get('/api/state').json
    group = state['course_groups'][0]
    result = client.post(f"/api/course-groups/{group['id']}/sections/507/roster",
                        data={'file': (io.BytesIO(csv_bytes([student_row('Example, Synthetic', '123456789')])), 'roster.csv')}, headers=HEADERS)
    assert result.status_code == 201, result.json
    section = client.get('/api/seating/catalog').json['sections'][0]['id']
    charts = []
    # The public copy leaves out the reviewed ILCB plans and the department's HECC workbooks.
    workbooks = [name for name in ('HECC 105', 'HECC 207', 'HECC 209') if (Path(__file__).parent / 'fixtures' / 'rooms' / f'{name}.xlsx').exists()]
    names = sorted(entry['room'] for entry in source_index().values()) + workbooks
    for index, name in enumerate(names):
        rooms = app.extensions['rooms']
        if name.startswith('ILCB'):
            with patch.object(rooms, 'parse_chart', return_value=profile(name)):
                room = rooms.create(name, '', b'print-test-profile', 'room.pdf')
            result = client.patch(f"/api/rooms/{room['id']}", json={'version': room['version'], 'resolve_review': True}, headers=HEADERS)
            assert result.status_code == 200, result.json
        else:
            room = rooms.create(name, '', fixture(name), name + '.xlsx')
        assignment = client.post('/api/documents/assignments', json={'definition_id': state['course_definitions'][0]['id'], 'key': f'print-{index}', 'title': 'Print test'}, headers=HEADERS).json
        result = client.post('/api/seating', json={'assignment_id': assignment['id'], 'room_id': room['id'], 'section_ids': [section]}, headers=HEADERS)
        assert result.status_code == 201, result.json
        chart = result.json
        if name == 'ILCB 111':
            chart['title'] = 'Long exam title ' * 16
            chart['sections'] = list(range(501, 511))
        # Fill every chair only in the browser fixture response. This tests all
        # positions without creating fictitious real roster memberships.
        for number, seat in enumerate(chart['room']['seats']):
            seat['student'] = {'name': 'Alexandria Montgomery-Wellington' if number == 0 and name.startswith('ILCB') else f'Learner{number:04d}', 'section_number': 507, 'enrollment_id': str(number), 'card': None}
        charts.append(chart)
    return charts


def check_version_print(page, address, chart, output):
    """Exam letters and seat labels must survive the chart's printable scale."""
    room_name = chart['room_name']
    floor = chart['room']['layout'].get('kind') == 'floorplan'
    chart['exam_version_count'] = 3
    for number, seat in enumerate(chart['room']['seats']):
        seat['exam_version'] = 'ABC'[number % 3]
    chart['exam_versions'] = {
        'count': 3, 'total': len(chart['room']['seats']), 'ready': True,
        'unassigned': [], 'conflicts': [],
        'counts': [{'version': letter,
                    'count': sum(seat['exam_version'] == letter for seat in chart['room']['seats'])}
                   for letter in 'ABC'],
    }
    for mode in ('tiles', 'fit'):
        page.emulate_media(media='screen')
        page.goto(address + '/#seating/' + chart['id'])
        expect(page.locator('.seating-view .seat-grid [data-seat]')).to_have_count(len(chart['room']['seats']))
        toggle = page.locator('[data-seating-action="version-view"]')
        if toggle.get_attribute('aria-pressed') != 'true':
            toggle.click()
        expect(page.locator('.seat-chart .seat-exam-version')).to_have_count(len(chart['room']['seats']))
        page.evaluate('window.print = () => {}')
        page.get_by_role('button', name='Print chart', exact=True).click()
        page.locator(f'input[name="layout"][value="{mode}"]').check()
        page.get_by_role('dialog').get_by_role('button', name='Print', exact=True).click()
        page.emulate_media(media='print')
        printed = page.locator('.seating-print-tiles' if floor or mode == 'tiles' else '.seat-chart')
        geometry = printed.evaluate('''holder => {
          const seats = [...holder.querySelectorAll('[data-seat]')];
          const outside = [];
          for (const seat of seats) {
            const box = seat.getBoundingClientRect();
            for (const text of seat.querySelectorAll('.seat-exam-version, .seat-tag')) {
              const range = document.createRange(); range.selectNodeContents(text);
              const rect = range.getBoundingClientRect();
              if (!rect.width || !rect.height || rect.left < box.left - 1 || rect.right > box.right + 1
                  || rect.top < box.top - 1 || rect.bottom > box.bottom + 1)
                outside.push({seat: seat.dataset.seat, text: text.textContent,
                  seatSize: [box.width, box.height], textSize: [rect.width, rect.height]});
            }
          }
          return {ids: seats.map(seat => seat.dataset.seat), outside,
                  letters: holder.querySelectorAll('.seat-exam-version').length};
        }''')
        assert sorted(geometry['ids']) == sorted(seat['id'] for seat in chart['room']['seats']), (room_name, mode)
        assert geometry['letters'] == len(chart['room']['seats']), (room_name, mode, geometry)
        assert not geometry['outside'], (room_name, mode, 'clipped version/seat text', geometry['outside'][:8])
        path = output / f"{room_name.replace(' ', '-')}-versions-{mode}.pdf"
        reader = PdfReader(io.BytesIO(page.pdf(prefer_css_page_size=True, print_background=True, path=str(path))))
        extracted = [''.join(part.extract_text().split()) for part in reader.pages]
        text = ''.join(extracted)
        missing = [seat['label'] for seat in chart['room']['seats']
                   if seat['exam_version'] + re.sub(r'\s+', '', seat['label']) not in text]
        assert not missing, (room_name, mode, 'missing version/seat pairs', missing)
        assert 'Learner' not in text and 'Montgomery-Wellington' not in text, (room_name, mode, 'student names in version view')
        assert all(extracted), (room_name, mode, 'blank version print pages')
        if mode == 'fit' and floor:
            assert len(reader.pages) == 1, (room_name, mode, 'version overview is not one page')
        print(f'{room_name} versions {mode}: {len(reader.pages)} pages; complete unclipped letters and seat labels', flush=True)


def main():
    output = Path('test-results/seating-print')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        charts = seed(app)
        if not charts:
            print('Seating print PDFs skipped: this copy has no room plans or workbooks.')
            return
        if len(sys.argv) > 1:
            charts = [chart for chart in charts if chart['room_name'] in sys.argv[1:]]
            assert charts, 'Choose a known room name.'
        server = create_server(app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.set_default_timeout(10000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' and not message.text.startswith('Failed to load resource') else None)
                for chart in charts:
                    page.route(f"**/api/seating/{chart['id']}", lambda route, request, data=chart: route.fulfill(json=data))
                for chart in charts:
                    floor = chart['room']['layout'].get('kind') == 'floorplan'
                    room_name = chart['room_name']
                    for mode in ('tiles', 'fit'):
                        page.emulate_media(media='screen')
                        page.goto(address + '/#seating/' + chart['id'])
                        expect(page.locator('.seating-view .seat-grid [data-seat]')).to_have_count(len(chart['room']['seats']))
                        page.evaluate('window.print = () => {}')
                        if floor:
                            page.locator('.seat-plan-controls [data-plan-action="actual"]').click()
                            page.locator('.seat-plan-controls [data-plan-action="in"]').click()
                        page.get_by_role('button', name='Print chart', exact=True).click()
                        if floor:
                            expect(page.locator('input[name="layout"][value="tiles"]')).to_be_checked()
                            expect(page.get_by_role('dialog')).to_contain_text('overview with seat labels only')
                        page.locator(f'input[name="layout"][value="{mode}"]').check()
                        page.get_by_role('dialog').get_by_role('button', name='Print', exact=True).click()
                        page.emulate_media(media='print')
                        if floor:
                            geometry = page.locator('.seating-print-floorplan').evaluate('''holder => {
                              const crops = [...holder.querySelectorAll('.seating-print-crop')];
                              return {pages: crops.length, ids: [...holder.querySelectorAll('[data-seat]')].map(n=>n.dataset.seat),
                                clipped: crops.flatMap(crop=>{const c=crop.getBoundingClientRect();return [...crop.querySelectorAll('[data-seat]')].filter(n=>{const r=n.getBoundingClientRect();return r.left<c.left-.1||r.right>c.right+.1||r.top<c.top-.1||r.bottom>c.bottom+.1}).map(n=>n.dataset.seat)}),
                                zero: crops.some(n=>!n.offsetWidth||!n.offsetHeight),
                                sizes:[...holder.querySelectorAll('.seat-name')].map(n=>parseFloat(getComputedStyle(n).fontSize))};
                            }''')
                            assert sorted(geometry['ids']) == sorted(seat['id'] for seat in chart['room']['seats']), room_name
                            assert not geometry['clipped'] and not geometry['zero'], (room_name, mode, geometry)
                            if mode == 'tiles':
                                assert min(geometry['sizes']) >= 8
                        path = output / f"{room_name.replace(' ', '-')}-{mode}.pdf"
                        data = page.pdf(prefer_css_page_size=True, print_background=True, path=str(path))
                        reader = PdfReader(io.BytesIO(data))
                        extracted = [''.join(p.extract_text().split()) for p in reader.pages]
                        text = ''.join(extracted)
                        if mode == 'tiles':
                            missing = [seat['student']['name'] for seat in chart['room']['seats'] if re.sub(r'\s+', '', seat['student']['name']) not in text]
                            assert not missing, (room_name, len(reader.pages), 'missing names', missing)
                            assert all(extracted), (room_name, 'empty print pages')
                            if floor:
                                assert all('Learner' in value for value in extracted), (room_name, 'blank floor-plan tiles')
                        if floor:
                            assert len(reader.pages) == geometry['pages'], (room_name, mode, len(reader.pages), geometry['pages'])
                            if mode == 'fit':
                                assert len(reader.pages) == 1
                                assert all(re.sub(r'\s+', '', seat['label']) in text for seat in chart['room']['seats']), room_name
                        print(f'{room_name} {mode}: {len(reader.pages)} pages; complete geometry/text', flush=True)
                for chart in charts:
                    if chart['room_name'] in ('ILCB 111', 'HECC 105'):
                        check_version_print(page, address, chart, output)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print('Seating print PDFs passed for selected rooms: complete seat/name coverage, no blank pages or clipped floor-plan seats, zoom-independent print geometry, CSP; ILCB 111 and HECC 105 also check version letters and seat labels in both layouts.')


if __name__ == '__main__':
    main()
