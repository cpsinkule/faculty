"""Saved exam versions: creation, assignment, editing, and printed label lists.

An isolated real catalog exercises durable edits. Only the multipage PDF fixture
intercepts a detail response; no real roster, mail, or printer is touched.
Run: .venv/bin/python tests/browser_seating_versions.py
"""
import copy
import io
import re
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from app import create_app
from rosters import connect
from test_rooms import chart_bytes
from test_rosters import csv_bytes
from test_seating import HEADERS, student_row


def seed(app):
    client = app.test_client()
    result = client.post('/api/courses', json={
        'name': 'Calculus', 'code': 'MATH 140', 'term': 'Fall', 'year': 2026,
        'sections': [507, 508], 'multiple_sections': True}, headers=HEADERS)
    assert result.status_code == 201, result.json
    state = client.get('/api/state').json
    group = state['course_groups'][0]
    for section, start in ((507, 1), (508, 7)):
        students = [student_row(f'Learner{number:02d}, Example', f'{number:09d}')
                    for number in range(start, start + 6)]
        result = client.post(f"/api/course-groups/{group['id']}/sections/{section}/roster", data={
            'file': (io.BytesIO(csv_bytes(students)), 'roster.csv')}, headers=HEADERS)
        assert result.status_code == 201, result.json
    cells = {f'A{row}': label for row, label in ((1, 'A'), (2, 'B'))}
    cells.update({f'{chr(65 + column)}{row}': column
                  for row in (1, 2) for column in range(1, 13)})
    room = app.extensions['rooms'].create('Version list room', '', chart_bytes(cells), 'room.xlsx')
    assignments = []
    for number in (1, 2):
        result = client.post('/api/documents/assignments', json={
            'definition_id': state['course_definitions'][0]['id'],
            'key': f'exam-{number}', 'title': f'Exam {number}'}, headers=HEADERS)
        assert result.status_code == 201, result.json
        assignments.append(result.json)
    return client, room, assignments


def table_rows(page):
    return page.locator('.seating-version-list .seating-version-student').evaluate_all(
        "rows => rows.map(row => [...row.querySelectorAll('td, th')].map(cell => cell.textContent.trim()))")


def saved_rows(chart):
    return [[row['label'], student['seat'], student['name'], str(student['section_number']), student['version']]
            for row in chart['exam_versions']['rows'] for student in row['students']]


def row_summaries(chart):
    summaries = []
    for row in chart['exam_versions']['rows']:
        counts = Counter(student['version'] for student in row['students'])
        versions = [f'{chr(65 + index)}: {counts[chr(65 + index)]}'
                    for index in range(chart['exam_version_count'])]
        summaries.append(' · '.join([row['label'], f"{len(row['students'])} students", *versions]))
    return summaries


def occupied(chart):
    return [seat for seat in chart['room']['seats'] if seat['student']]


def placements(chart):
    return {student['enrollment_id']: student['seat_id'] for student in chart['students']}


def letters(chart):
    return {seat['id']: seat['exam_version'] for seat in chart['room']['seats']}


def synthetic_chart(original):
    """Large saved list with gaps and split source rows, for physical pagination."""
    chart = copy.deepcopy(original)
    seat_template = copy.deepcopy(chart['room']['seats'][0])
    student_template = copy.deepcopy(chart['students'][0])
    seats, students, rows, totals = [], [], [], Counter()
    for row_index, row_label in enumerate('ABCD'):
        members = []
        for index in range(30):
            serial = row_index * 30 + index + 1
            number = 2 * index + 1 + (4 if index >= 5 else 0)
            grid_row = 2 * row_index + (1 if index < 5 else 2)
            row_versions = 'AB' if row_index == 0 else 'ABC'
            letter = row_versions[(serial - 1) % len(row_versions)]
            student = {**student_template, 'name': f'Learner{serial:03d} Synthetic',
                       'enrollment_id': str(9100000000 + serial),
                       'section_number': 507 + (serial % 2), 'status': 'seated',
                       'email': f'private{serial}@example.test', 'card': None,
                       'seat': f'{row_label}{number}', 'seat_id': f'synthetic-seat-{serial}',
                       'exam_version': letter}
            seat = {**seat_template, 'id': student['seat_id'], 'label': student['seat'],
                    'display': str(number), 'row': grid_row, 'column': number,
                    'row_label': row_label, 'number': number, 'group': '',
                    'group_size': 0, 'seat_index': 0, 'row_span': 1, 'column_span': 1,
                    'broken': False, 'blocked': False, 'left_handed': False,
                    'student': student, 'exam_version': letter}
            seats.append(seat)
            students.append(student)
            members.append({key: student[key] for key in
                            ('seat_id', 'enrollment_id', 'name', 'section_number', 'seat')})
            members[-1]['version'] = letter
            totals[letter] += 1
            seats.append({**seat, 'id': f'empty-{serial}', 'label': f'{row_label}{number + 1}',
                          'display': str(number + 1), 'number': number + 1,
                          'column': number + 1, 'student': None, 'exam_version': None})
        rows.append({'label': f'Row {row_label}', 'students': members})
    students.append({**student_template, 'name': 'Excluded DR Student',
                     'enrollment_id': '9299999999', 'status': 'dr_testing',
                     'seat': None, 'seat_id': None, 'card': None, 'exam_version': None})
    chart['room'].update(seats=list(reversed(seats)), grid_rows=8, grid_columns=64,
                         landmarks=[], layout={}, usable_count=len(seats), seat_count=len(seats))
    chart['students'] = list(reversed(students[:-1])) + students[-1:]
    chart['live_counts'].update(seated=120, excluded_dr=1, unseated=0, usable_seats=240, free_seats=120)
    chart['counts'].update(seated=120, excluded_dr=1, students=121, usable_seats=240, free_seats=120)
    chart['dr']['excluded_students'] = 1
    chart['notes'] = 'CHART_ONLY_PRIVATE_NOTE'
    chart['exam_version_count'] = 3
    chart['exam_versions'] = {'count': 3, 'rows': rows,
                              'counts': [{'version': letter, 'count': totals[letter]} for letter in 'ABC'],
                              'total': 120, 'unassigned': [], 'conflicts': [], 'ready': True}
    return chart


def main():
    output = Path('test-results/seating-versions')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client, room, assignments = seed(app)
        server = create_server(app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.set_default_timeout(10000)
                errors, writes = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: writes.append((request.url, request.post_data_json))
                        if request.method in ('POST', 'PATCH', 'PUT', 'DELETE') else None)
                page.add_init_script("""window.versionPrintCalls = 0; window.versionPrintCsp = [];
                    window.print = () => { window.versionPrintCalls += 1; };
                    document.addEventListener('securitypolicyviolation', event => window.versionPrintCsp.push(event.violatedDirective));""")

                def create(assignment, count=None):
                    page.goto(address + '/#seating')
                    expect(page.locator('#page-title')).to_have_text('Seating charts')
                    page.locator('#primary-action').click()
                    form = page.locator('#seating-create')
                    expect(form).to_be_visible()
                    form.get_by_label('Exam assignment', exact=True).select_option(assignment['id'])
                    expect(form.get_by_label('Exam assignment', exact=True)).to_have_value(assignment['id'])
                    for section in (507, 508):
                        form.get_by_label(f'Room for Section {section}', exact=True).select_option(room['id'])
                    field = form.locator('[name="exam_version_count"]')
                    expect(field).to_have_value('')
                    assert not field.evaluate('input => input.required')
                    expect(form.locator('[name="allow_unequal_versions"]')).not_to_be_checked()
                    if count is not None:
                        for invalid in ('0', '27', '2.5'):
                            field.fill(invalid)
                            assert not field.evaluate('input => input.checkValidity()'), invalid
                        field.fill(str(count))
                    with page.expect_response(lambda response: response.request.method == 'POST'
                                              and response.url.endswith('/api/seating/plan')) as saved:
                        form.get_by_role('button', name='Create seating chart', exact=True).click()
                    assert saved.value.status == 201, saved.value.text()
                    expect(form).not_to_be_visible()
                    expect(page.locator('.seating-view .seat.seated')).to_have_count(12)
                    chart_id = page.url.rsplit('/', 1)[-1]
                    return client.get(f'/api/seating/{chart_id}').json

                # Supplying the optional field assigns versions with generation.
                automatic = create(assignments[0], 3)
                assert automatic['exam_version_count'] == 3
                assert automatic['allow_unequal_versions'] is False
                assert automatic['exam_versions']['ready']
                assert set(seat['exam_version'] for seat in occupied(automatic)) == set('ABC')
                counts = [item['count'] for item in automatic['exam_versions']['counts']]
                assert max(counts) - min(counts) <= 1
                assert next(payload for url, payload in reversed(writes) if url.endswith('/api/seating/plan'))['allow_unequal_versions'] is False
                expect(page.locator('[data-seating-action="print-versions"]')).to_be_visible()
                expect(page.locator('.seating-actions [data-seating-action="assign-versions"]')).to_have_count(0)
                # Exporting prints one PDF on every seat: with seat letters A–C, the export window says so before anything prints.
                with connect(app.extensions['rosters'].path) as db:
                    version_id = 'fixture-pdf-version'
                    db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, assignments[0]['id'], 'Version A',
                        'fixture/source.json', 'a' * 64, 'fixture/canonical.pdf', 'b' * 64, 2, '{}', 'faculty-page-v1', '2026-09-01T00:00:00+00:00'))
                page.locator('#primary-action').click()
                export = page.locator('#seating-export')
                expect(export.locator('.seating-export-versions')).to_contain_text('Every copy prints from the one PDF chosen here')
                expect(export.locator('.seating-export-versions')).to_contain_text('Versions A–C')
                expect(export.locator('.seating-export-versions')).to_contain_text('(Print exams for this room, then Printed elsewhere)')
                # A stamps-only version labels papers printed elsewhere, which is the advice itself: the warning steps aside for it.
                export.get_by_role('button', name='Cancel', exact=True).click()
                expect(export).not_to_be_visible()
                with connect(app.extensions['rosters'].path) as db:
                    db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", ('fixture-stamps-version', assignments[0]['id'], 'Stamps only · 2 pages',
                        'fixture/stamps.json', 'c' * 64, 'fixture/stamps.pdf', 'd' * 64, 2, '{"generator": "faculty-stamps-only"}', 'faculty-page-v1', '2026-08-01T00:00:00+00:00'))
                page.locator('#primary-action').click()
                expect(export.locator('[name="version_id"]')).to_have_value(version_id)
                expect(export.locator('.seating-export-versions')).to_be_visible()
                export.locator('[name="version_id"]').select_option('fixture-stamps-version')
                expect(export.locator('.seating-export-versions')).to_be_hidden()
                export.locator('[name="version_id"]').select_option(version_id)
                expect(export.locator('.seating-export-versions')).to_be_visible()
                export.get_by_role('button', name='Cancel', exact=True).click()
                expect(export).not_to_be_visible()
                for row in automatic['exam_versions']['rows']:
                    assigned = [student['version'] for student in row['students']]
                    assert all(a != b for a, b in zip(assigned, assigned[1:]))
                assert any(b['column'] - a['column'] > 1 for label in ('A', 'B')
                           for a, b in zip([seat for seat in occupied(automatic) if seat['row_label'] == label],
                                           [seat for seat in occupied(automatic) if seat['row_label'] == label][1:]))

                # Leaving it blank postpones assignment and changes the action.
                chart = create(assignments[1])
                endpoint = f"/api/seating/{chart['id']}"
                current = lambda: client.get(endpoint).json
                assert chart['exam_version_count'] is None
                assign = page.locator('.seating-actions [data-seating-action="assign-versions"]')
                expect(assign).to_have_text('Assign exam versions')
                expect(page.locator('[data-seating-action="print-versions"]')).to_have_count(0)
                assign.click()
                form = page.locator('#seating-version-assign')
                expect(form).to_be_visible()
                expect(form.locator('[name="allow_unequal_versions"]')).not_to_be_checked()
                before = len(writes)
                form.get_by_role('button', name='Cancel', exact=True).click()
                expect(form).not_to_be_visible()
                assert len(writes) == before and current()['exam_version_count'] is None
                assign.click()
                field = form.locator('[name="version_count"]')
                for invalid in ('0', '27', '2.5', ''):
                    field.fill(invalid)
                    assert not field.evaluate('input => input.checkValidity()'), invalid
                field.fill('3')
                form.locator('[name="allow_unequal_versions"]').check()
                form.get_by_role('button', name='Assign versions', exact=True).click()
                expect(form).not_to_be_visible()
                expect(page.locator('[data-seating-action="print-versions"]')).to_have_text('Print version list')
                assert current()['exam_version_count'] == 3
                assert current()['allow_unequal_versions'] is True
                assert next(payload for url, payload in reversed(writes) if url.endswith('/exam-versions'))['allow_unequal_versions'] is True
                # With versions, the print dialog says what the printed seats show, starting from the view on screen;
                # printing versions from the student view switches for the print and returns afterwards.
                page.get_by_role('button', name='Print chart', exact=True).click()
                show = page.locator('#seating-print .seating-print-show')
                expect(show.get_by_label('Student names', exact=True)).to_be_checked()
                show.get_by_label('Exam versions', exact=True).check()
                page.locator('#seating-print').get_by_role('button', name='Print', exact=True).click()
                expect(page.locator('.seating-view.version-view')).to_have_count(1)
                page.evaluate("window.dispatchEvent(new Event('afterprint'))")
                expect(page.locator('.seating-view.version-view')).to_have_count(0)

                # Version mode replaces names/status overlays with saved letters.
                toggle = page.locator('[data-seating-action="version-view"]')
                toggle.click()
                expect(toggle).to_have_text('Student view')
                expect(page.locator('.seating-view')).to_have_class(re.compile(r'\bversion-view\b'))
                expect(page.locator('.seat-exam-version')).to_have_count(12)
                expect(page.locator('.seating-view .seat.card-pending, .seating-view .seat.card-complete, .seating-view .seat.card-issue')).to_have_count(0)
                chart = current()
                seats = occupied(chart)
                first = seats[0]
                other = next(seat for seat in seats if seat['exam_version'] != first['exam_version'])
                original_placements = placements(chart)
                original_letters = letters(chart)
                seat_button = lambda seat: page.locator(f'[data-seat="{seat["id"]}"]')
                editor = page.locator('#seating-version-editor')

                def open_print():
                    page.locator('[data-seating-action="print-versions"]').click()
                    form = page.locator('#seating-version-print')
                    expect(form).to_be_visible()
                    expect(form.locator('[name="version_count"]')).to_have_count(0)
                    return form

                def clear_print():
                    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
                    expect(page.locator('body')).not_to_have_class(re.compile(r'\bprint-version-list\b'))
                    expect(page.locator('.seating-version-list')).to_have_count(0)
                    page.emulate_media(media='screen')

                assert first['student']['name'] not in seat_button(first).inner_text()
                empty = next(seat for seat in chart['room']['seats'] if not seat['student'])
                seat_button(empty).click()
                expect(editor).not_to_be_visible()
                seat_button(first).click()
                expect(editor).to_be_visible()
                for letter in 'ABC':
                    expect(editor.locator(f'[data-set-exam-version="{letter}"]')).to_be_visible()
                page.keyboard.press('Escape')
                expect(editor).not_to_be_visible()
                seat_button(first).click()
                seat_button(other).click()
                expect(editor).not_to_be_visible()
                expect(seat_button(first).locator('.seat-exam-version')).to_have_text(other['exam_version'])
                expect(seat_button(other).locator('.seat-exam-version')).to_have_text(first['exam_version'])
                changed = current()
                assert placements(changed) == original_placements
                assert letters(changed)[first['id']] == original_letters[other['id']]
                assert letters(changed)[other['id']] == original_letters[first['id']]

                # Direct assignment changes only that seat and survives reload.
                seat_button(first).click()
                new_letter = next(letter for letter in 'ABC' if letter != letters(changed)[first['id']])
                editor.locator(f'[data-set-exam-version="{new_letter}"]').click()
                expect(editor).not_to_be_visible()
                expect(seat_button(first).locator('.seat-exam-version')).to_have_text(new_letter)
                assert placements(current()) == original_placements
                page.reload()
                expect(page.locator('.seating-view')).to_be_visible()
                assert letters(current())[first['id']] == new_letter
                if page.locator('.seating-view.version-view').count() == 0:
                    toggle.click()
                expect(seat_button(first).locator('.seat-exam-version')).to_have_text(new_letter)

                # A stale edit is rejected once and reloads saved letters.
                seat_button(first).click()
                live = current()
                direct = client.post(endpoint + '/exam-versions/edit', json={
                    'version': live['version'], 'seat_id': other['id'],
                    'exam_version': next(letter for letter in 'ABC' if letter != letters(live)[other['id']])}, headers=HEADERS)
                assert direct.status_code == 200, direct.json
                previous = letters(direct.json)
                edits_before = sum(url.endswith('/exam-versions/edit') for url, _ in writes)
                with page.expect_response(lambda response: response.request.method == 'POST'
                                          and response.url.endswith('/exam-versions/edit')) as stale:
                    editor.locator(f'[data-set-exam-version="{next(letter for letter in "ABC" if letter != previous[first["id"]])}"]').click()
                assert stale.value.status == 409, stale.value.text()
                expect(editor).not_to_be_visible()
                expect(seat_button(other).locator('.seat-exam-version')).to_have_text(previous[other['id']])
                page.wait_for_timeout(300)
                assert sum(url.endswith('/exam-versions/edit') for url, _ in writes) == edits_before + 1
                assert letters(current()) == previous

                # Recover an accepted edit whose HTTP response was lost without
                # repeating the write or swapping the versions back again.
                seat_button(first).click()
                new_letter = next(letter for letter in 'ABC' if letter != previous[first['id']])
                edits_before = sum(url.endswith('/exam-versions/edit') for url, _ in writes)

                def lose_response(route):
                    response = route.fetch()
                    assert response.status == 200, response.text()
                    route.abort('failed')

                page.route('**' + endpoint + '/exam-versions/edit', lose_response, times=1)
                editor.locator(f'[data-set-exam-version="{new_letter}"]').click()
                expect(editor).not_to_be_visible()
                expect(seat_button(first).locator('.seat-exam-version')).to_have_text(new_letter)
                page.wait_for_timeout(300)
                assert sum(url.endswith('/exam-versions/edit') for url, _ in writes) == edits_before + 1
                assert letters(current())[first['id']] == new_letter

                # Manual conflicts remain visible, and printing uses the exact
                # saved choices instead of silently alternating them again.
                neighbors = current()['exam_versions']['rows'][0]['students'][:2]
                conflict_seat = next(seat for seat in occupied(current()) if seat['id'] == neighbors[1]['seat_id'])
                seat_button(conflict_seat).click()
                editor.locator(f'[data-set-exam-version="{neighbors[0]["version"]}"]').click()
                expect(editor).not_to_be_visible()
                expect(page.locator('.seating-version-status')).to_contain_text('Same-version neighbors')
                assert current()['exam_versions']['conflicts']
                form = open_print()
                expect(form.locator('.seating-version-warning')).to_contain_text('Review same-version neighbors')
                expect(form.locator('.seating-version-row-summary')).to_have_text(row_summaries(current()))
                before = len(writes)
                form.get_by_role('button', name='Print', exact=True).click()
                expect(form).not_to_be_visible()
                expect(page.locator('body')).to_have_class(re.compile(r'\bprint-version-list\b'))
                assert table_rows(page) == saved_rows(current())
                expect(page.locator('.seating-version-list .seating-version-row-summary')).to_have_text(row_summaries(current()))
                expect(page.locator('.seating-version-list .seating-version-warning')).to_contain_text('Review same-version neighbors')
                assert len(writes) == before
                clear_print()

                # Reassignment restores a conflict-free baseline for print.
                live = current()
                result = client.post(endpoint + '/exam-versions', json={'version': live['version'], 'count': 3}, headers=HEADERS)
                assert result.status_code == 200, result.json
                page.reload()
                expect(page.locator('[data-seating-action="print-versions"]')).to_be_visible()
                if page.locator('.seating-view.version-view').count() == 0:
                    toggle.click()
                toggle.click()
                expect(toggle).to_have_text('Version view')
                expect(page.locator('.seating-view')).not_to_have_class(re.compile(r'\bversion-view\b'))
                expect(seat_button(first)).to_contain_text(first['student']['name'])

                before = len(writes)
                form = open_print()
                form.get_by_role('button', name='Print', exact=True).click()
                expect(form).not_to_be_visible()
                expect(page.locator('body')).to_have_class(re.compile(r'\bprint-version-list\b'))
                assert table_rows(page) == saved_rows(current())
                assert len(writes) == before
                clear_print()

                # Version edits have no student moves or card/mail side effects.
                with connect(app.extensions['rosters'].path) as db:
                    assert db.execute("SELECT COUNT(*) FROM seating_events WHERE chart_id=? AND kind='move'", (chart['id'],)).fetchone()[0] == 0
                    assert db.execute('SELECT COUNT(*) FROM response_card_mail').fetchone()[0] == 0
                    assert db.execute('SELECT COUNT(*) FROM response_card_invalidations').fetchone()[0] == 0

                # Moving a student preserves the old seat's letter. A new seat
                # has no guessed version and blocks printing until addressed.
                before_move = current()
                saved_letters = letters(before_move)
                seat_button(first).click()
                seat_button(empty).click()
                expect(seat_button(empty)).to_contain_text(first['student']['name'])
                after_move = current()
                assert letters(after_move) == saved_letters
                assert first['id'] not in {student['seat_id'] for student in after_move['students']}
                assert empty['label'] in after_move['exam_versions']['unassigned']
                expect(page.locator('.seating-version-status')).to_contain_text(f'Version needed at {empty["label"]}')
                form = open_print()
                expect(form.get_by_role('button', name='Print', exact=True)).to_be_disabled()
                expect(form.locator('[role="alert"]')).to_contain_text('Assign an exam version to every occupied seat')
                form.get_by_role('button', name='Cancel', exact=True).click()
                seat_button(empty).click()
                seat_button(first).click()
                expect(seat_button(first)).to_contain_text(first['student']['name'])
                assert placements(current()) == original_placements
                assert letters(current()) == saved_letters
                assert current()['exam_versions']['ready']

                # At the narrowest desktop width on a laptop-height screen (desktop only; there is no phone layout) the
                # controls and editor stay inside the viewport under CSP.
                page.set_viewport_size({'width': 1280, 'height': 800})
                toggle.click()
                seat_button(first).click()
                expect(editor).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # The earlier move's success toast fades on its own (app.js toast); at this height it can sit over the
                # editor's lower edge in the window's corner until then, so the hit test waits it out.
                expect(page.locator('.toast-item[data-tone="success"]')).to_have_count(0)
                for letter in 'ABC':
                    choice = editor.locator(f'[data-set-exam-version="{letter}"]')
                    expect(choice).to_be_visible()
                    placed = choice.evaluate("""button => {
                        const box = button.getBoundingClientRect();
                        const target = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
                        return {ok: box.x >= 0 && box.right <= innerWidth && box.y >= 0 && box.bottom <= innerHeight
                            && (target === button || button.contains(target)),
                            box: [box.x, box.y, box.right, box.bottom], target: target && target.outerHTML.slice(0, 160)};
                    }""")
                    assert placed['ok'], (f'Version {letter} must be visible, unclipped, and clickable at 1280 wide.', placed)
                page.screenshot(path=str(output / 'version-editor-1280.png'))
                page.keyboard.press('Escape')
                form = open_print()
                expect(form.get_by_role('button', name='Print', exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                dialog = page.get_by_role('dialog').bounding_box()
                assert dialog and dialog['x'] >= 0 and dialog['x'] + dialog['width'] <= 1280
                page.screenshot(path=str(output / 'print-dialog-1280.png'))
                form.get_by_role('button', name='Cancel', exact=True).click()
                page.set_viewport_size({'width': 1440, 'height': 1000})

                # The large saved detail fixture checks actual PDF pagination.
                synthetic = synthetic_chart(current())
                page.route('**' + endpoint, lambda route: route.fulfill(json=synthetic))
                page.reload()
                expect(page.locator('.seating-view .seat.seated')).to_have_count(120)
                form = open_print()
                expect(form.locator('.seating-version-row-summary')).to_have_text(row_summaries(synthetic))
                form.get_by_role('button', name='Print', exact=True).click()
                expect(form).not_to_be_visible()
                expect(page.locator('body')).to_have_class(re.compile(r'\bprint-version-list\b'))
                assert table_rows(page) == saved_rows(synthetic)
                expect(page.locator('.seating-version-list .seating-version-row-summary')).to_have_text(row_summaries(synthetic))
                expect(page.locator('.seating-version-list .seating-version-totals')).to_contain_text('120 students')
                page.emulate_media(media='print')
                expect(page.locator('.seating-version-list')).to_be_visible()
                expect(page.locator('.seating-view')).to_be_hidden()
                expect(page.locator('.seating-actions')).to_be_hidden()
                expect(page.locator('.sidebar')).to_be_hidden()
                data = page.pdf(prefer_css_page_size=True, print_background=True,
                                path=str(output / 'three-versions.pdf'))
                pages = [item.extract_text() for item in PdfReader(io.BytesIO(data)).pages]
                text = ''.join(pages)
                compact = re.sub(r'\s+', '', text)
                assert len(pages) > 1, len(pages)
                assert all('Learner' in item for item in pages), 'Blank or heading-only printed page.'
                for student in synthetic['students'][:-1]:
                    assert re.sub(r'\s+', '', student['name']) in compact, student['name']
                    assert student['enrollment_id'] not in text
                    assert student['email'] not in text
                for forbidden in ('Excluded DR Student', 'CHART_ONLY_PRIVATE_NOTE', 'Regenerate',
                                  'Find a student', 'Confirm seating chart', 'Zoom out', 'History'):
                    assert forbidden not in text, forbidden
                assert all('Exam version' in item for item in pages), 'Column headings should repeat.'
                for summary in row_summaries(synthetic):
                    assert re.sub(r'\s+', '', summary) in compact, summary
                clear_print()

                # A changed saved snapshot refreshes an open preview and needs
                # a second explicit Print click before using the new list.
                form = open_print()
                calls = page.evaluate('window.versionPrintCalls')
                changed_member = synthetic['exam_versions']['rows'][0]['students'][0]
                changed_member['name'] = 'Updated Synthetic Name'
                for student in synthetic['students']:
                    if student['enrollment_id'] == changed_member['enrollment_id']:
                        student['name'] = changed_member['name']
                form.get_by_role('button', name='Print', exact=True).click()
                expect(form.locator('[role="alert"]')).to_contain_text('Review the updated list')
                expect(form.locator('#seating-version-preview')).to_contain_text('Updated Synthetic Name')
                assert page.evaluate('window.versionPrintCalls') == calls
                form.get_by_role('button', name='Print', exact=True).click()
                expect(form).not_to_be_visible()
                assert page.evaluate('window.versionPrintCalls') == calls + 1
                assert table_rows(page) == saved_rows(synthetic)
                clear_print()

                assert page.evaluate('window.versionPrintCsp') == []
                assert errors == [], errors
                browser.close()
        finally:
            server.close()
    print('PASS: optional versions at creation, later assignment, durable swaps/direct edits, stale edit without retry, conflict warnings and exact saved printing with per-row counts, versions stay with seats, new seats need versions, student view, multipage PDF, mobile controls, no card/mail effects or CSP errors.')


if __name__ == '__main__':
    main()
