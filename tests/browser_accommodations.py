"""Real browser mixed-course accommodation preview, import, rendering and privacy."""
import io
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course
from test_rosters import csv_bytes
from test_accommodations import workbook
from test_storage import HEADERS


def main():
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(directory); client=app.test_client(); client.get('/').close()
        for code in ('MATH 140','MATH 151'):
            seed_course(client,code=code,sections=[507],multiple_sections=False)
        groups=client.get('/api/state').json['course_groups']
        for g in groups:
            route=f"/api/course-groups/{g['id']}/sections/507/roster"
            assert client.post(route,data={'file':(io.BytesIO(csv_bytes()),'roster.csv')},headers=HEADERS).status_code==201
        content=workbook([
            ['Example','Alice','001234567','','MATH','140','507','Read','2026-09-13','Yes','','<img src=x onerror="window.accommodationInjection=true">'],
            ['Example','Bob','123456789','','MATH','151','507','Emailed','2026-09-13','','Yes','Literal note']])
        server=create_server(app,host='127.0.0.1',port=0)
        threading.Thread(target=server.run,daemon=True).start()
        address=f'http://127.0.0.1:{server.effective_port}'
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(address+'/#all-courses')
                assert not page.context.cookies(), 'Faculty should not issue a workspace session'
                page.get_by_role('link',name='Accommodations',exact=True).click()
                page.get_by_role('button',name='Import accommodations',exact=True).click()
                dialog=page.get_by_role('dialog')
                page.get_by_label('Accommodation export',exact=True).set_input_files({'name':'export.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':content})
                page.get_by_role('button',name='Preview import',exact=True).click()
                expect(dialog).to_contain_text('2 of 2 rows match your rosters')
                expect(dialog).to_contain_text('MATH 140');expect(dialog).to_contain_text('MATH 151')
                for g in groups:
                    assert client.get(f"/api/course-groups/{g['id']}/sections/507/roster").json['accommodation_count']==0
                page.get_by_role('button',name='Import matching students',exact=True).click()
                expect(dialog).to_contain_text('Import queued')
                assert app.extensions['inbox'].process_one()
                expect(dialog).to_contain_text('Accommodations imported: 2 added',timeout=10000)
                expect(page.get_by_label('Accommodation export',exact=True)).to_be_disabled()
                expect(dialog.get_by_role('link',name='View import record',exact=True)).to_be_visible()
                dialog.get_by_role('button',name='Close',exact=True).click()
                # The page shows what each student has inline; type chips with counts filter the table.
                results=page.locator('#accommodation-results')
                expect(results.locator('tbody tr')).to_have_count(2)
                expect(page.locator('.accommodation-status-line')).to_contain_text('2 students with accommodations')
                expect(page.locator('.accommodation-freshness')).to_contain_text('Accommodations imported')
                expect(page.locator('#accommodations-section .accommodation-actions .button').first).to_have_text('Import from AIM')
                expect(page.locator('#accommodations-section .accommodation-actions .button.primary')).to_have_count(0)  # imports are never the main action
                page.get_by_role('button',name='Extra Time 1.50x 1',exact=True).click()
                expect(results.locator('tbody tr')).to_have_count(1)
                expect(results.locator('tbody tr')).to_contain_text('Extra Time 1.50x')
                page.get_by_role('button',name='All 2',exact=True).click()
                expect(results.locator('tbody tr')).to_have_count(2)
                alice=results.locator('tbody tr').filter(has_text='MATH 140')
                source=alice.locator('.accommodation-source')
                expect(source).not_to_have_attribute('open','')
                source.locator('summary').click()
                expect(source).to_contain_text('<img src=x')
                assert not page.evaluate('Boolean(window.accommodationInjection)')
                # Steady across the shell's refreshes: the open disclosure, the search text and its focus survive.
                search=page.get_by_label('Find a student',exact=True)
                search.fill('Example');search.focus()
                page.evaluate('window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange"))')
                page.wait_for_timeout(600)
                expect(source).to_have_attribute('open','')
                assert page.evaluate('document.activeElement && document.activeElement.id')=='accommodation-search'
                expect(search).to_have_value('Example')
                # A changed record is patched in place on Refresh: Bob gains a newer record; Alice's disclosure stays open.
                newer=workbook([['Example','Bob','123456789','','MATH','151','507','Emailed','2026-09-14','Yes','Yes','Literal note']])
                plan=client.post('/api/accommodations/preview',data={'file':(io.BytesIO(newer),'export.xlsx'),'term':'null'},headers=HEADERS).json
                queued=client.post('/api/accommodations/import',data={'file':(io.BytesIO(newer),'export.xlsx'),'term':'null','preview_hash':plan['preview_hash'],'idempotency_key':'browser-newer'},headers=HEADERS)
                assert queued.status_code==202,queued.json
                assert app.extensions['inbox'].process_one()
                page.get_by_role('button',name='Refresh',exact=True).click()
                expect(page.get_by_role('button',name='Extra Time 1.50x 2',exact=True)).to_be_visible()
                expect(source).to_have_attribute('open','')
                search.fill('')
                # A name opens the student's gradebook record: the card a name opens in the section's gradebook. Its Change
                # writes that section's cell as the section tab's grid does.
                math140=next(g for g in groups if g['code']=='MATH 140')
                term_id=next(t['id'] for t in client.get('/api/gradebook').json['terms'] if t['group_id']==math140['id'])
                saved=client.post(f'/api/gradebook/terms/{term_id}/setup',json={'base_revision':None,'request_id':'acc-record-setup',
                    'setup':{'items':[{'title':'Homework 1','points':'10'}]}},headers=HEADERS)
                assert saved.status_code==200,saved.json
                alice.locator('[data-student-record]').click()
                record=page.locator('#gradebook-student-form')
                expect(record).to_be_visible()
                expect(record).to_contain_text('Section 507')
                expect(record).to_contain_text('Overall')
                expect(record).to_contain_text('Extra Time 1.50x')
                with page.expect_response(lambda r: r.request.method=='POST' and r.url.endswith(f'/api/gradebook/terms/{term_id}/scores')) as wrote:
                    record.get_by_role('button',name='Change Homework 1',exact=True).click()
                    history=page.locator('#gradebook-history-form')
                    history.get_by_label('Points').fill('8')
                    history.get_by_role('button',name='Save',exact=True).click()
                assert wrote.value.status==200 and wrote.value.request.post_data_json.get('section_id'),wrote.value.request.post_data_json
                # Opened from the record, a saved cell goes back to the record, which shows the new value.
                expect(history).to_have_count(0)
                expect(record).to_be_visible()
                expect(record.get_by_role('region',name='Grades').locator('tr',has_text='Homework 1')).to_contain_text('8 of 10')
                record.get_by_role('button',name='Close',exact=True).click()
                expect(page.locator('#dialog')).not_to_be_visible()
                cells=client.get(f'/api/gradebook/terms/{term_id}').json['cells']
                assert [cell['points'] for column in cells.values() for cell in column.values()]==['8'],cells
                expect(results.locator('tbody tr')).to_have_count(2)
                Path('test-results').mkdir(exist_ok=True)
                page.screenshot(path='test-results/accommodations-desktop.png',full_page=True)
                for code,item in [('MATH 140','Extra Time 1.50x'),('MATH 151','Reduced Distraction Environment')]:
                    g=next(g for g in groups if g['code']==code)
                    page.goto(address+f"/#course/{g['id']}/section/507/roster?from=all-courses")
                    panel=page.locator('#section-roster')
                    # One Support column, only because a student has a record; a filter shows just those students.
                    expect(panel.get_by_role('columnheader',name='Support',exact=True)).to_have_count(1)
                    expect(panel.locator('.roster-summary')).to_contain_text('1 with accommodations')
                    panel.get_by_role('button',name='With accommodations 1',exact=True).click()
                    expect(panel.locator('tbody tr:visible')).to_have_count(1)
                    expect(panel.locator('#roster-filter-count')).to_have_text('1 of 2 students')
                    panel.get_by_role('button',name='All students 2',exact=True).click()
                    expect(panel.locator('tbody tr:visible')).to_have_count(2)
                    expect(panel.locator('.student-accommodations')).to_have_count(1)
                    expect(panel.locator('.student-accommodations')).not_to_have_attribute('open','')
                    panel.locator('.student-accommodations summary').click()
                    expect(panel.locator('.student-accommodations')).to_contain_text(item)
                    if code=='MATH 140':
                        expect(panel.locator('.student-accommodations')).to_contain_text('<img src=x')
                        assert not page.evaluate('Boolean(window.accommodationInjection)')
                    # Desktop only (DESIGN-LANGUAGE, 2026-10-03): the roster fits the narrowest desktop window.
                    page.set_viewport_size({'width':1280,'height':900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'Overflow at 1280'
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.reload();expect(panel.locator('.student-accommodations')).to_have_count(1)
                # A section links to the Accommodations page, which owns both imports.
                expect(panel.get_by_role('button',name='Import accommodations',exact=True)).to_have_count(0)
                panel.get_by_role('link',name='Accommodations & DR exams',exact=True).click()
                expect(page.locator('#page-title')).to_have_text('Accommodations')
                page.get_by_role('button',name='Import accommodations',exact=True).click()
                expect(dialog).to_be_visible()
                page.get_by_label('Accommodation export',exact=True).set_input_files({'name':'export.xlsx','mimeType':'application/octet-stream','buffer':content})
                page.get_by_role('button',name='Preview import',exact=True).click()
                # Problems first: Bob's row is older than his saved record, grouped under its reason.
                expect(dialog).to_contain_text('0 new · 0 updated · 1 unchanged')
                expect(dialog.locator('.accommodation-preview-group[data-tone="warning"]')).to_contain_text('A newer accommodation record is already saved.')
                dialog.get_by_role('button',name='Close',exact=True).click()
                page.go_back()
                expect(panel.locator('.student-accommodations')).to_have_count(1)
                panel.locator('.student-accommodations summary').click()
                panel.get_by_role('link',name='View import record',exact=True).click()
                # The last roster opened is MATH 151, whose record came from the newer import.
                expect(page.locator('#inbox-section')).to_contain_text('Accommodations imported: 0 added, 1 updated')
                # Many types: All, the six most common and "N more types"; every type when open; a chosen type that is
                # normally folded stays beside the six (rest-people-11). Eight more types are added to one student's record
                # in the overview the page reads (the page only reads it).
                def more_types(route):
                    response=route.fetch(); body=response.json()
                    target=next(s for s in body['students'] if (s.get('accommodations') or {}).get('items'))
                    first=target['accommodations']['items'][0]
                    target['accommodations']['items']=target['accommodations']['items']+[{**first,'name':f'Type {letter}'} for letter in 'ABCDEFGH']
                    route.fulfill(response=response,json=body)
                page.route('**/api/accommodations/overview',more_types)
                page.goto(address+'/#accommodations');page.reload()
                chips=page.locator('#accommodations-section [data-filter-group="acc-type"]')
                toggle=chips.locator('[data-acc-more]')
                expect(toggle).to_have_text('4 more types')
                expect(toggle).to_have_attribute('aria-expanded','false')
                expect(chips.locator('button[data-filter]')).to_have_count(7)
                expect(chips.get_by_role('button',name='Type G 1',exact=True)).to_have_count(0)
                toggle.click()
                expect(toggle).to_have_text('Fewer types')
                expect(toggle).to_have_attribute('aria-expanded','true')
                expect(toggle).to_be_focused()
                expect(chips.locator('button[data-filter]')).to_have_count(11)
                chips.get_by_role('button',name='Type G 1',exact=True).click()
                expect(results.locator('tbody tr')).to_have_count(1)
                toggle.click()
                expect(toggle).to_have_text('3 more types')
                expect(toggle).to_be_focused()
                expect(chips.locator('button[data-filter]')).to_have_count(8)
                expect(chips.get_by_role('button',name='Type G 1',exact=True)).to_have_attribute('aria-pressed','true')
                expect(results.locator('tbody tr')).to_have_count(1)
                page.set_viewport_size({'width':1280,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'Type chips overflow at 1280'
                page.set_viewport_size({'width':1440,'height':1000})
                page.unroute('**/api/accommodations/overview')
                assert not errors,errors
                browser.close()
                print('Accommodation browser checks passed: mixed courses, preview before writes, queued import, escaped collapsible records, reload, reimport, Inbox provenance, the 1280px desktop layout and folded type chips.')
        finally:server.close()


if __name__=='__main__':main()
