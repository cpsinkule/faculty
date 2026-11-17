"""Room roster preview, DR revalidation, full PDF output and print cleanup.

Uses an isolated catalog, synthetic students and an intercepted window.print.
No real data is printed or emailed.
"""
import copy
from datetime import datetime
import io
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from test_seating_roster import RoomRosterTests
from rosters import connect
from test_dr_exams import booking


def main():
    fixture = RoomRosterTests('test_alphabetical_students_exact_uins_seats_and_current_exam_only')
    fixture.setUp()
    chart = fixture.chart
    endpoint = f"/api/seating/{chart['id']}/room-roster"
    server = create_server(fixture.app, host='127.0.0.1', port=0)
    threading.Thread(target=server.run, daemon=True).start()
    base = f'http://127.0.0.1:{server.effective_port}'
    output = Path('test-results/seating-roster')
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, timezone_id='America/Los_Angeles')
            page.set_default_timeout(10000)
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: writes.append(request.url)
                    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else None)
            page.add_init_script('''window.rosterPrintCalls = 0; window.rosterCsp = [];
                window.print = () => { window.rosterPrintCalls += 1; };
                document.addEventListener('securitypolicyviolation', event => window.rosterCsp.push(event.violatedDirective));''')
            page.goto(base + '/#seating/' + chart['id'])
            page.get_by_role('button', name='Print room roster', exact=True).click()
            form = page.locator('#seating-roster-print')
            expect(form).to_be_visible()
            preview = form.locator('#seating-roster-preview')
            expect(preview.locator('.seating-roster-seated tbody tr')).to_have_count(7)
            expect(preview.locator('.seating-roster-dr tbody tr')).to_have_count(2)
            expect(preview).to_contain_text('001234567')
            expect(preview).to_contain_text('No card issued')
            expect(preview).to_contain_text('7:05 PM–8:58 PM')
            expect(preview).not_to_contain_text('Final')
            names = preview.locator('.seating-roster-seated tbody td:first-child').all_text_contents()
            assert names == sorted(names, key=str.casefold)
            fixture.import_dr([booking(Date=datetime(2026, 9, 20), **{'Start Time': '09:30 AM', 'End Time': '11:23 AM'})])
            form.get_by_role('button', name='Print', exact=True).click()
            expect(form.locator('[role="alert"]')).to_contain_text('changed')
            expect(preview).to_contain_text('09/20/2026')
            expect(preview).to_contain_text('9:30 AM–11:23 AM')
            assert page.evaluate('window.rosterPrintCalls') == 0
            fixture.prepare_cards()
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET status='verified' WHERE student_name='Example, Bob'")
                db.execute("UPDATE response_card_recipients SET status='needs_review' WHERE student_name='Example, Carol'")
            form.get_by_role('button', name='Print', exact=True).click()
            expect(form.locator('[role="alert"]')).to_contain_text('response cards changed')
            expect(preview.locator('.seating-roster-seated .seating-roster-response strong')).to_have_count(6)
            expect(preview.locator('.seating-roster-dr .seating-roster-response strong')).to_have_count(1)
            expect(preview).to_contain_text('Responded — needs review')
            assert page.evaluate('window.rosterPrintCalls') == 0
            form.get_by_role('button', name='Print', exact=True).click()
            expect(page.locator('body')).to_have_class('print-room-roster')
            assert page.evaluate('window.rosterPrintCalls') == 1
            page.emulate_media(media='print')
            pdf = page.pdf(path=str(output / 'room-roster.pdf'), prefer_css_page_size=True)
            text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
            expected = fixture.client.get(endpoint).json
            for student in expected['room_students'] + expected['dr_students']:
                assert student['name'] in text and student['uin'] in text
                assert 'enrollment_id' not in text
                if 'seat' in student:
                    assert student['seat'] in text
            assert text.count('No response') == 7
            assert 'Responded' in text and 'needs review' in text
            assert text.rindex('DR testers') > text.index(expected['room_students'][-1]['name'])
            assert '09/20/2026' in text and '9:30 AM' in text
            assert 'Print room roster' not in text and 'Response cards' not in text
            page.evaluate('window.dispatchEvent(new Event("afterprint"))')
            expect(page.locator('.seating-room-roster')).to_have_count(0)
            page.emulate_media(media='screen')

            # Exercise real pagination with a large, already ordered API snapshot.
            large = copy.deepcopy(expected)
            large['fingerprint'] = 'synthetic-large-roster'
            large['room_students'] = [{'name': f'Example{index:03d}, Learner', 'uin': f'{index:09d}',
                'section_number': 507 + index % 2, 'seat': f'K{index}',
                'response_card': {'label': 'No response' if index % 2 else 'Responded', 'no_response': bool(index % 2)}} for index in range(1, 161)]
            schedule = large['dr_students'][0]['bookings'][0]
            large['dr_students'] = [{'name': f'Tester{index:03d}, Learner', 'uin': f'{1000 + index:09d}',
                'section_number': 507, 'response_card': {'label': 'No response', 'no_response': True}, 'bookings': [{**schedule, 'campus': 'College Station'}]} for index in range(1, 17)]
            page.route(base + endpoint, lambda route: route.fulfill(json=large))
            page.get_by_role('button', name='Print room roster', exact=True).click()
            form = page.locator('#seating-roster-print')
            expect(form.locator('.seating-roster-seated tbody tr')).to_have_count(160)
            page.set_viewport_size({'width': 1280, 'height': 800})
            page.screenshot(path=str(output / 'preview-1280.png'), full_page=True)
            assert page.locator('#dialog').evaluate('el => el.getBoundingClientRect().right <= innerWidth')
            form.get_by_role('button', name='Print', exact=True).click()
            page.emulate_media(media='print')
            pdf = page.pdf(path=str(output / 'large-room-roster.pdf'), prefer_css_page_size=True)
            reader = PdfReader(io.BytesIO(pdf))
            pages = [p.extract_text() for p in reader.pages]
            text = '\n'.join(pages)
            assert len(pages) >= 4
            for student in large['room_students'] + large['dr_students']:
                assert student['name'] in text and student['uin'] in text, student
            for text_page in pages:
                assert text_page.strip() and ('Student' in text_page and 'UIN' in text_page), text_page
                assert 'Example' in text_page or 'Tester' in text_page, text_page
            assert text.rindex('DR testers') > text.index('Example160, Learner')
            assert text.count('No response') == 96
            assert not writes, writes
            assert not errors, errors
            assert page.evaluate('window.rosterCsp') == [], page.evaluate('window.rosterCsp')
            browser.close()
        print('PASS: alphabetic room roster, exact UINs/seats, linked DR schedules and stale revalidation, local times, complete multipage PDFs, repeated headers, narrow desktop preview, print cleanup and CSP; no writes.')
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
