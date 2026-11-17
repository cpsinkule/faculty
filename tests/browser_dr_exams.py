"""Accommodations navigation and repeat DR exam imports through the real UI."""
import io
import sys
import tempfile
import threading
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course
from test_rosters import csv_bytes
from test_dr_exams import booking, workbook
from test_accommodations import workbook as accommodation_workbook
from test_storage import HEADERS


def wall(day):
    """The page's campus date: 'Thu, Sep 17' (ui.js wallDate)."""
    return f'{day:%a}, {day:%b} {day.day}'


def main():
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(directory);client=app.test_client()
        for code in ('MATH 140','MATH 151'):seed_course(client,code=code,sections=[507],multiple_sections=False)
        groups=client.get('/api/state').json['course_groups']
        for group in groups:
            response=client.post(f"/api/course-groups/{group['id']}/sections/507/roster",data={'file':(io.BytesIO(csv_bytes()),'roster.csv')},headers=HEADERS)
            assert response.status_code==201
        server=create_server(app,host='127.0.0.1',port=0)
        threading.Thread(target=server.run,daemon=True).start()
        address=f'http://127.0.0.1:{server.effective_port}'
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(address+'/#current-courses')
                page.locator('nav [data-view="accommodations"]').click()
                expect(page.locator('#page-title')).to_have_text('Accommodations')
                root=page.locator('#accommodations-section')
                expect(root.get_by_role('button',name='Import accommodations',exact=True)).to_be_visible()
                expect(root.get_by_role('button',name='Import DR exam bookings',exact=True)).to_be_visible()
                root.get_by_role('link',name='DR exam schedule',exact=True).click()
                expect(root).to_contain_text('0 bookings')
                expect(root.locator('.empty-state')).to_contain_text('No DR bookings yet')
                # Import from AIM comes first on either tab (no primary: imports are setup); the tab's own upload comes next.
                expect(root.locator('.accommodation-actions .button').first).to_have_text('Import from AIM')
                expect(root.locator('.accommodation-actions .text-button').nth(1)).to_have_text('Import DR exam bookings')  # Capture AIM's form, then this tab's upload
                dialog=page.get_by_role('dialog')
                xlsx='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                # The wrong export names the right importer, and switching carries the chosen file across.
                root.get_by_role('button',name='Import DR exam bookings',exact=True).click()
                page.get_by_label('DR exam export',exact=True).set_input_files({'name':'accommodations.xlsx','mimeType':xlsx,'buffer':accommodation_workbook()})
                dialog.get_by_role('button',name='Preview import',exact=True).click()
                expect(dialog).to_contain_text('This looks like an accommodation export. Use Import accommodations.')
                dialog.get_by_role('button',name='Switch to Import accommodations',exact=True).click()
                expect(page.locator('#dialog-title')).to_have_text('Import accommodations')
                assert page.get_by_label('Accommodation export',exact=True).evaluate('input => input.files.length')==1
                dialog.get_by_role('button',name='Close',exact=True).click()
                def preview(content):
                    root.get_by_role('button',name='Import DR exam bookings',exact=True).click()
                    expect(dialog).to_be_visible()
                    page.get_by_label('DR exam export',exact=True).set_input_files({'name':'exams.xlsx','mimeType':xlsx,'buffer':content})
                    dialog.get_by_role('button',name='Preview import',exact=True).click()
                    try:
                        expect(dialog.get_by_role('button',name='Import matching bookings',exact=True)).to_be_enabled()
                    except AssertionError as error:
                        raise AssertionError(dialog.inner_text()) from error
                def commit(expected):
                    dialog.get_by_role('button',name='Import matching bookings',exact=True).click()
                    expect(dialog).to_contain_text('Import queued')
                    assert app.extensions['inbox'].process_one()
                    expect(dialog).to_contain_text(expected,timeout=10000)
                today=date.today();past=today-timedelta(days=9);moved=past+timedelta(days=1);soon=today+timedelta(days=26)
                first=workbook([booking(Date=datetime.combine(past,time())),booking(**{'Exam Type':'Test 2','Date':datetime.combine(soon,time())}),
                    booking(Course='151',UIN='123456789',Date=datetime.combine(past,time()),**{'First Name':'Bob','Campus':'<img src=x onerror="window.drInjection=true">'})])
                preview(first);expect(dialog).to_contain_text('3 new');expect(dialog).to_contain_text('3 will be imported')
                commit('DR exam bookings imported: 3 added')
                # The import says which assignments it created to link the bookings, and where to see them.
                expect(dialog).to_contain_text('New assignment created: Exam 1 (MATH 140)')  # DR's "Test" is Faculty's "Exam" (exam_links.exam_title)
                expect(dialog.get_by_role('link',name=re.compile('^Show these bookings'))).to_be_visible()
                dialog.get_by_role('button',name='Close',exact=True).click()
                # Upcoming is the default: only Test 2 is ahead; the status line names it.
                expect(root.locator('tbody tr')).to_have_count(1)
                expect(root.locator('.accommodation-count')).to_have_text('1 booking · 1 student · upcoming')
                expect(root.locator('.dr-group h3')).to_have_text(['Exam 2'])
                expect(root.locator('.accommodation-status-line')).to_contain_text(f'1 upcoming DR booking · next: Exam 2, {wall(soon)} · 1 student')
                expect(root.locator('.accommodation-freshness')).to_contain_text('DR bookings imported')
                root.get_by_role('button',name=re.compile('^All')).click()
                expect(root.locator('tbody tr')).to_have_count(3)
                expect(root).to_contain_text('3 bookings · 2 students')
                expect(root).to_contain_text('<img src=x')
                assert not page.evaluate('Boolean(window.drInjection)')
                # Each exam's group links to its hub and says it has no seating chart yet.
                # Groups are per exam in its course and term: Test 1 in MATH 140, Test 1 in MATH 151 and Test 2.
                expect(root.locator('.dr-group')).to_have_count(3)
                group=root.locator('.dr-group').filter(has=page.get_by_role('heading',name='Exam 1',exact=True)).filter(has_text='MATH 140 · ')
                expect(group).to_contain_text('No seating chart yet')
                expect(group.get_by_role('link',name=re.compile('^Exam hub'))).to_have_attribute('href',re.compile('^#exams/'))
                expect(group.locator('th')).to_have_text(['Student','Course & section','Date','Time (DR center)','Details'])
                expect(group.locator('.dr-group-meta')).to_contain_text('College Station')
                root.get_by_label('Course',exact=True).select_option('MATH 151')
                expect(root.locator('tbody tr')).to_have_count(1)
                root.get_by_label('Course',exact=True).select_option('')
                root.get_by_label('Exam',exact=True).select_option('Test 2')
                expect(root.locator('tbody tr')).to_have_count(1)
                root.get_by_label('Exam',exact=True).select_option('')
                root.get_by_label('Find a student',exact=True).fill('001234567')
                expect(root.locator('tbody tr')).to_have_count(2)
                root.get_by_label('Find a student',exact=True).fill('')
                changed=workbook([booking(**{'Date':datetime.combine(moved,time()),'Start Time':'11:00 AM','End Time':'12:53 PM'})])
                preview(changed)
                expect(dialog).to_contain_text('1 updated')
                expect(dialog.locator('.accommodation-preview-group').filter(has_text='Changed')).to_contain_text(f'Test 1 · {wall(past)}')
                commit('DR exam bookings imported: 0 added, 1 updated')
                dialog.get_by_role('button',name='Close',exact=True).click()
                expect(root.locator('tbody tr')).to_have_count(3)
                row=root.locator('tbody tr').filter(has_text=wall(moved))
                expect(row).to_contain_text('11:00 AM–12:53 PM')
                expect(row).to_contain_text('Rescheduled')
                row.get_by_role('button',name=re.compile('^Details')).click()
                expect(page.locator('#dialog-title')).to_have_text('Example, Alice')
                expect(dialog).to_contain_text('Current booking');expect(dialog).to_contain_text('Previous booking')
                expect(dialog).to_contain_text(f'Date: {wall(past)} → {wall(moved)}')
                dialog.get_by_role('button',name='Close',exact=True).click()
                preview(changed);expect(dialog).to_contain_text('0 new · 0 updated · 1 unchanged')
                expect(dialog).to_contain_text('Everything in this file is already saved.')
                dialog.get_by_role('button',name='Close',exact=True).click()
                # The address keeps the date choice (no student data); a reload lands on the same view.
                assert 'when=all' in page.evaluate('location.hash') and '001234567' not in page.evaluate('location.hash')
                page.reload();expect(root.locator('tbody tr')).to_have_count(3)
                # Steady across the shell's refreshes: typing and focus survive, and nothing is rebuilt.
                search=root.get_by_label('Find a student',exact=True)
                search.fill('Example');search.focus()
                root.locator('tbody tr').first.evaluate('row => row.dataset.marker = "kept"')
                page.evaluate('window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange"))')
                page.wait_for_timeout(600)
                assert page.evaluate('document.activeElement && document.activeElement.id')=='accommodation-search'
                expect(search).to_have_value('Example')
                expect(root.locator('tbody tr[data-marker="kept"]')).to_have_count(1)
                # Refresh reads again; the same records redraw nothing.
                root.get_by_role('button',name='Refresh',exact=True).click()
                page.wait_for_timeout(600)
                expect(root.locator('tbody tr[data-marker="kept"]')).to_have_count(1)
                search.fill('')
                # A calendar/exam-hub deep link lands on one course's exam group.
                page.goto(address+'/#accommodations?tab=exams&course=MATH%20151&exam=Test%201&when=all')
                expect(root.locator('tbody tr')).to_have_count(1)
                expect(root.locator('tbody tr')).to_contain_text('Example, Bob')
                expect(root.get_by_label('Course',exact=True)).to_have_value('MATH 151')
                root.get_by_role('button',name='Clear filters',exact=True).first.click()
                expect(root.locator('tbody tr')).to_have_count(3)
                # Print DR list: an ephemeral list of one exam's students, removed after printing.
                page.evaluate("() => { window.__printed = []; window.print = () => window.__printed.push({classes: document.body.className, text: document.querySelector('.dr-print-list')?.textContent || ''}); }")
                root.locator('.dr-group').filter(has=page.get_by_role('heading',name='Exam 1',exact=True)).filter(has_text='MATH 140 · ').get_by_role('button',name='Print DR list for Exam 1',exact=True).click()
                printed=page.evaluate('window.__printed')
                assert len(printed)==1 and 'print-dr-list' in printed[0]['classes'],printed
                for text in ('Test 1','MATH 140','Example, Alice','001234567','Time (DR center)',wall(moved),'113 min','College Station'):
                    assert text in printed[0]['text'],(text,printed[0]['text'])
                assert 'Test 2' not in printed[0]['text'] and 'Example, Bob' not in printed[0]['text']
                page.evaluate('window.dispatchEvent(new Event("afterprint"))')
                expect(page.locator('.dr-print-list')).to_have_count(0)
                assert 'print-dr-list' not in page.evaluate('document.body.className')
                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): at 1280px the page does not scroll sideways, the filters stay
                # in the toolbar and the bookings stay a table.
                page.set_viewport_size({'width':1280,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'Overflow at 1280'
                expect(root.locator('#accommodation-filters')).to_be_visible()
                expect(root.locator('thead').first).to_be_visible()
                Path('test-results').mkdir(exist_ok=True)
                page.screenshot(path='test-results/dr-exams-1280.png',full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                page.screenshot(path='test-results/dr-exams-desktop.png',full_page=True)
                row=root.locator('tbody tr').filter(has_text=wall(moved))
                row.get_by_role('link',name='MATH 140 · 507',exact=True).click()
                expect(page.locator('#section-roster')).to_contain_text('2 DR exam bookings')
                assert not errors,errors
                browser.close()
                print('DR exam browser checks passed: dedicated page, Upcoming default, exam groups and links, wrong-export switch, imports, filters, repeat schedule updates, retained missing bookings, details, deep link, steady refresh, print DR list, reload, roster links, escaping and the 1280px desktop layout.')
        finally:server.close()


if __name__=='__main__':main()
