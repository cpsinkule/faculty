"""Move-selection recovery under stale, lost, and delayed HTTP responses.

Uses an isolated catalog with fake publication/mail providers. No live email.
Run: .venv/bin/python tests/browser_seating_moves.py
"""
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rosters import connect
from test_seating_updates import SeatingUpdateTests


def main():
    fixture = SeatingUpdateTests('test_swap_marks_both_students_and_only_queues_their_new_cards')
    fixture.setUp()
    other = fixture.create(assignment_id=fixture.quiz['id']).json
    endpoint = fixture.route + '/move'
    server = create_server(fixture.app, host='127.0.0.1', port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f'http://127.0.0.1:{server.effective_port}'

    def moves():
        with connect(fixture.path) as db:
            return db.execute("SELECT COUNT(*) FROM seating_events WHERE chart_id=? AND kind='move'", (fixture.chart['id'],)).fetchone()[0]

    def destination():
        return next(seat for seat in fixture.current()['room']['seats']
                    if not seat['student'] and not seat['broken'] and not seat['blocked'])

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.set_default_timeout(10000)
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.post_data_json)
                    if request.method == 'POST' and request.url.endswith(endpoint) else None)
            page.add_init_script("""window.moveCsp = [];
                document.addEventListener('securitypolicyviolation', e => window.moveCsp.push(e.violatedDirective));""")
            page.goto(address + '/#seating/' + fixture.chart['id'])
            expect(page.locator('.seating-view')).to_be_visible()
            selection = page.locator('.seating-move-selection')

            def select_bob():
                bob = fixture.student()
                page.locator(f'[data-move="{bob["enrollment_id"]}"]').click()
                expect(selection).to_contain_text('Example, Bob selected')
                expect(page.locator('.seat.move-source')).to_have_count(1)
                return bob

            def cleared():
                expect(selection).to_be_hidden()
                expect(page.locator('.seat.move-source')).to_have_count(0)
                expect(page.locator('.seat-grid')).not_to_have_attribute('aria-busy', 'true')

            def names_other():
                # The other chart is the one on screen: its assignment titles the page (ASSIGNMENT-WORKSPACE), its room leads the
                # status card, and the seating plan's heading names both.
                assert other['title'] != fixture.chart['title'], (other['title'], fixture.chart['title'])
                expect(page.locator('#page-title')).to_have_text(other['title'])
                expect(page.locator('.seating-workflow-scope a').first).to_have_text(other['room_name'])
                expect(page.locator('#seating-chart-title')).to_contain_text(other['title'] + ' · ' + other['room_name'])

            # The selected source carries the exact displayed chart revision.
            # A competing change yields 409, then reloads without a second POST.
            bob = select_bob()
            output = Path('test-results')
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / 'seating-move-selected.png'))
            fixture.move('Student, 4')
            target = destination()
            count = moves()
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith(endpoint)) as stale:
                page.locator(f'[data-seat="{target["id"]}"]').click()
            assert stale.value.status == 409, stale.value.text()
            cleared()
            moved_student = fixture.student('Student, 4')
            expect(page.locator(f'[data-seat="{moved_student["seat_id"]}"]')).to_contain_text('Student, 4')
            expect(page.locator(f'[data-seat="{bob["seat_id"]}"]')).to_contain_text('Example, Bob')
            page.wait_for_timeout(350)
            assert len(requests) == 1 and moves() == count
            assert fixture.student()['seat_id'] == bob['seat_id']

            # Abort the browser response after the real server commits. Reload
            # recovers the saved destination; no automatic move retry is safe.
            select_bob()
            target = destination()

            def lose_response(route):
                response = route.fetch()
                assert response.status == 200, response.text()
                route.abort('failed')

            page.route('**' + endpoint, lose_response, times=1)
            page.locator(f'[data-seat="{target["id"]}"]').click()
            expect(page.locator(f'[data-seat="{target["id"]}"]')).to_contain_text('Example, Bob')
            cleared()
            page.wait_for_timeout(350)
            assert len(requests) == 2 and moves() == count + 1
            assert fixture.student()['seat_id'] == target['id']

            # Retain a successful HTTP response while navigating to another
            # chart. Its arrival must not overwrite the currently viewed chart.
            select_bob()
            target = destination()
            delayed = []

            def delay_response(route):
                response = route.fetch()
                assert response.status == 200, response.text()
                delayed.append((route, response))

            page.route('**' + endpoint, delay_response, times=1)
            page.locator(f'[data-seat="{target["id"]}"]').click()
            expect(selection).to_contain_text('Moving Example, Bob')
            page.wait_for_timeout(150)
            assert len(delayed) == 1
            page.evaluate('(id) => { location.hash = "#seating/" + id; }', other['id'])
            # ASSIGNMENT-WORKSPACE: the header names the chart's assignment (its shell's title); the room leads the chart's status card.
            names_other()
            other_bob = fixture.student(chart=other)
            expect(page.locator(f'[data-seat="{other_bob["seat_id"]}"]')).to_contain_text('Example, Bob')
            delayed[0][0].fulfill(response=delayed[0][1])
            page.wait_for_timeout(350)
            names_other()
            expect(page.locator(f'[data-seat="{other_bob["seat_id"]}"]')).to_contain_text('Example, Bob')
            page.evaluate('(id) => { location.hash = "#seating/" + id; }', fixture.chart['id'])
            expect(page.locator('#seating-chart-title')).to_contain_text(fixture.chart['title'] + ' · ' + fixture.chart['room_name'])
            expect(page.locator(f'[data-seat="{target["id"]}"]')).to_contain_text('Example, Bob')
            cleared()
            assert len(requests) == 3 and moves() == count + 2

            # Returning before the retained response arrives must show a saving
            # placeholder instead of the other chart's still-interactive grid.
            select_bob()
            target = destination()
            delayed.clear()
            page.route('**' + endpoint, delay_response, times=1)
            page.locator(f'[data-seat="{target["id"]}"]').click()
            expect(selection).to_contain_text('Moving Example, Bob')
            page.wait_for_timeout(150)
            assert len(delayed) == 1
            page.evaluate('(id) => { location.hash = "#seating/" + id; }', other['id'])
            expect(page.locator('#seating-chart-title')).to_contain_text(other['title'] + ' · ' + other['room_name'])
            page.evaluate('(id) => { location.hash = "#seating/" + id; }', fixture.chart['id'])
            expect(page.locator('#seating-section .loading-state')).to_have_text('Saving the seating change…')
            expect(page.locator('#seating-section .seat-grid')).to_have_count(0)
            delayed[0][0].fulfill(response=delayed[0][1])
            expect(page.locator('#seating-chart-title')).to_contain_text(fixture.chart['title'] + ' · ' + fixture.chart['room_name'])
            expect(page.locator(f'[data-seat="{target["id"]}"]')).to_contain_text('Example, Bob')
            cleared()
            assert len(requests) == 4 and moves() == count + 3
            assert len(fixture.mail_rows()) == 9 and len(fixture.relay.sent) == 9
            assert fixture.gmail.sent == []
            assert page.evaluate('window.moveCsp') == []
            assert errors == [], errors
            browser.close()
        print('PASS: stale move reloads and clears selection; lost accepted response recovers without retry; delayed success preserves another chart; returning before receipt shows saving then saved move; no new emails or CSP errors.')
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
