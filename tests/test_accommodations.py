"""Mixed-course accommodation intake, exact identities, history and private reads."""
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from openpyxl import Workbook

from app import create_app
from accommodation_import import parse_accommodations
from rosters import connect
from test_storage import temporary_storage, HEADERS
from test_rosters import csv_bytes

HEADINGS = ['Last Name','First Name','UIN','Email Address (TAMU)','Subject','Course','Section',
            'Request Status','Last Updated','Extra Time 1.50x','Reduced Distraction Environment','Notes']


def workbook(rows=None, headings=HEADINGS):
    if rows is None:
        rows=[['Example','Alice','001234567','a@example.test','MATH','140','507','Read','2026-09-13','Yes','Yes','Keep wording']]
    book=Workbook(); book.active.append(headings)
    for row in rows: book.active.append(row)
    out=io.BytesIO(); book.save(out); book.close(); return out.getvalue()


class AccommodationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.storage=temporary_storage(self.temp.name)
        self.app=create_app(storage=self.storage); self.app.testing=True
        self.client=self.app.test_client(); self.client.get('/').close()
        self.service=self.app.extensions['accommodations']; self.inbox=self.app.extensions['inbox']
        self.groups=[]
        for code,term,current in [('MATH 140','Fall',True),('MATH 151','Fall',True),('MATH 140','Spring',False)]:
            r=self.client.post('/api/courses',json={'name':code,'code':code,'term':term,'year':2026,'sections':[507,508],
                'multiple_sections':True,'is_current':current},headers=HEADERS)
            self.assertEqual(r.status_code,201)
            self.storage.reconcile_all()
            g=next(g for g in self.client.get('/api/state').json['course_groups'] if g['code']==code and g['term']==term)
            self.groups.append(g)
            for section in (507,508):
                result=self.client.post(self.route(g,section),data={'file':(io.BytesIO(csv_bytes()),'roster.csv')},headers=HEADERS)
                self.assertEqual(result.status_code,201,result.json)

    def route(self,g,section=507):
        return f"/api/course-groups/{g['id']}/sections/{section}/roster"

    def roster(self,g,section=507):
        return self.client.get(self.route(g,section)).json

    def preview(self,content,term=None):
        r=self.client.post('/api/accommodations/preview',data={'file':(io.BytesIO(content),'export.xlsx'),'term':json.dumps(term)},headers=HEADERS)
        self.assertEqual(r.status_code,200,r.json); return r.json

    def submit(self,content,preview=None,term=None):
        preview=preview or self.preview(content,term)
        r=self.client.post('/api/accommodations/import',data={'file':(io.BytesIO(content),'export.xlsx'),'term':json.dumps(term),
            'preview_hash':preview['preview_hash'],'idempotency_key':str(uuid.uuid4())},headers=HEADERS)
        self.assertEqual(r.status_code,202,r.json)
        self.assertEqual(r.json['status'],'pending')
        self.assertTrue(self.inbox.process_one())
        return self.inbox.get(r.json['id'])

    def test_mixed_courses_match_uin_course_section_without_leaking_to_other_terms(self):
        before=[self.roster(g) for g in self.groups]
        data=workbook([
            ['Example','Alice','001234567','','math','140','0507','Read','2026-09-13','Yes','',''],
            ['Example','Alice','001234567','','MATH','151','507','Emailed','2026-09-13','','Yes','Different course'],
            ['Unknown','Student','999999999','','MATH','140','507','Read','2026-09-13','Yes','',''],
            ['Example','Alice','001234567','','PHYS','140','507','Read','2026-09-13','Yes','','']])
        preview=self.preview(data)
        self.assertEqual(preview['counts']['added'],2); self.assertEqual(preview['counts']['unmatched'],2)
        # Display only, after the hash: names read 'Last, First' as the rosters show them, matched or not.
        self.assertEqual([row['display_name'] for row in preview['rows']],['Example, Alice','Example, Alice','Unknown, Student','Example, Alice'])
        self.assertEqual(preview['rows'][2]['name'],'Student Unknown')
        self.assertEqual([self.roster(g) for g in self.groups],before)
        job=self.submit(data,preview); self.assertEqual(job['status'],'completed',job['error'])
        first=self.roster(self.groups[0]); other=self.roster(self.groups[1]); past=self.roster(self.groups[2])
        self.assertEqual(first['students'][0]['accommodations']['items'],[{'name':'Extra Time 1.50x','value':'Yes'}])
        self.assertEqual(other['students'][0]['accommodations']['items'],[{'name':'Reduced Distraction Environment','value':'Yes'}])
        self.assertNotIn('accommodations',past['students'][0])
        self.assertNotIn('accommodations',self.roster(self.groups[0],508)['students'][0])
        for previous,after in zip(before,[first,other,past]):
            self.assertEqual([s['id'] for s in previous['students']],[s['id'] for s in after['students']])
            self.assertEqual([s['fields'] for s in previous['students']],[s['fields'] for s in after['students']])
        with connect(self.service.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accommodation_revisions').fetchone()[0],2)
            message=db.execute('SELECT scope_id,definition_id FROM inbox_messages WHERE id=?',(job['message_id'],)).fetchone()
            self.assertEqual(tuple(message),(None,None))
        source=next(a for a in job['artifacts'] if a['role']=='input')
        self.assertEqual(self.inbox.read_artifact(source['id'])[1],data)
        self.assertEqual(self.storage.backup_all()['errors'],[])
        self.assertEqual(self.inbox.read_artifact(source['id'])[1],data)

    def test_course_group_summary_counts_active_accommodations_per_section(self):
        data=workbook([
            ['Example','Alice','001234567','','MATH','140','507','Read','2026-09-13','Yes','',''],
            ['Example','Bob','123456789','','MATH','140','507','Read','2026-09-13','','','Nothing active'],
            ['Example','Alice','001234567','','MATH','151','508','Read','2026-09-13','','Yes','']])
        job=self.submit(data); self.assertEqual(job['status'],'completed',job['error'])
        groups=self.client.get('/api/course-groups/summary').json['groups']
        fall=groups[self.groups[0]['id']]['sections']
        # Bob's record has no active accommodation, so only Alice counts.
        self.assertEqual((fall['507']['students'],fall['507']['with_accommodations']),(2,1))
        self.assertEqual(fall['508']['with_accommodations'],0)
        self.assertEqual(groups[self.groups[1]['id']]['sections']['508']['with_accommodations'],1)
        self.assertEqual(groups[self.groups[2]['id']]['sections']['507']['with_accommodations'],0)

    def test_reimports_keep_history_ignore_missing_students_and_reject_older_updates(self):
        self.submit(workbook())
        same=self.preview(workbook()); self.assertEqual(same['counts']['unchanged'],1)
        self.submit(workbook(),same)
        newer=workbook([['Example','Alice','001234567','','MATH','140','507','Read','2026-09-14','','Yes','Revised']])
        # The preview shows what an updated student had before (display only: the preview hash still matches the import).
        changed=self.preview(newer)
        self.assertEqual(changed['rows'][0]['previous_accommodations'],[{'name':'Extra Time 1.50x','value':'Yes'},{'name':'Reduced Distraction Environment','value':'Yes'}])
        job=self.submit(newer,changed); self.assertEqual(job['result']['counts']['updated'],1)
        self.assertNotIn('previous_accommodations',job['result']['rows'][0])
        latest=self.client.get('/api/accommodations/overview').json['last_imports']
        self.assertEqual((latest['accommodations']['job_id'],latest['accommodations']['counts']['updated'],latest['accommodations']['not_imported']),(job['id'],1,0))
        self.assertIsNone(latest['dr_exams'])
        row=self.roster(self.groups[0])['students'][0]
        self.assertEqual(row['accommodations']['items'],[{'name':'Reduced Distraction Environment','value':'Yes'}])
        older=self.preview(workbook()); self.assertEqual(older['counts']['stale'],1)
        # A normal roster import preserves separately owned accommodations.
        self.client.post(self.route(self.groups[0]),data={'file':(io.BytesIO(csv_bytes()),'roster.csv')},headers=HEADERS)
        self.assertEqual(self.roster(self.groups[0])['students'][0]['accommodations'],row['accommodations'])
        with connect(self.service.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accommodation_revisions').fetchone()[0],2)
            with self.assertRaises(sqlite3.IntegrityError): db.execute("UPDATE accommodation_revisions SET snapshot_json='{}'")

    def test_changed_preview_fails_without_partial_writes(self):
        first=workbook(); preview=self.preview(first)
        self.submit(workbook([['Example','Alice','001234567','','MATH','140','507','Read','2026-09-14','','Yes','']]))
        r=self.client.post('/api/accommodations/import',data={'file':(io.BytesIO(first),'export.xlsx'),'preview_hash':preview['preview_hash']},headers=HEADERS)
        self.assertEqual(r.status_code,409)
        self.assertEqual(len(self.roster(self.groups[0])['students'][0]['accommodations']['items']),1)

    def test_worker_retry_reuses_committed_records_after_receipt_failure(self):
        real=self.inbox._receipt
        def fail_receipt(job,result=None):
            if result is not None: raise OSError('interrupted receipt')
            return real(job,result)
        with patch.object(self.inbox,'_receipt',side_effect=fail_receipt):
            job=self.submit(workbook())
        self.assertEqual(job['status'],'failed')
        self.inbox.transition(job['id'],'retry',job['version'])
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'],'completed')
        with connect(self.service.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accommodation_revisions').fetchone()[0],1)

    def test_multiple_current_terms_require_explicit_term_selection(self):
        past=self.groups[2]['offerings'][0]
        response=self.client.patch('/api/courses/'+past['id'],json={'version':past['version'],'is_current':True},headers=HEADERS)
        self.assertEqual(response.status_code,200,response.json); self.storage.reconcile_all()
        self.assertEqual(self.preview(workbook())['counts']['ambiguous'],1)
        chosen={'term':'Fall','term_other':'','year':2026}
        self.assertEqual(self.preview(workbook(),chosen)['counts']['added'],1)

    def test_no_session_needed_and_no_generic_export_disclosure(self):
        self.submit(workbook())
        fresh=self.app.test_client()
        self.assertEqual(fresh.get('/api/accommodations/options').status_code,200)
        self.assertEqual(fresh.get(self.route(self.groups[0])).status_code,200)
        self.assertEqual(fresh.post('/api/accommodations/preview',data={'file':(io.BytesIO(workbook()),'x.xlsx')},headers=HEADERS).status_code,200)
        with fresh.get('/') as home:
            self.assertEqual(home.headers.getlist('Set-Cookie'),[])
        # Old tabs can keep obsolete cookies across an app restart without an
        # access error or a visit to the home page.
        restarted=create_app(storage=self.storage).test_client()
        restarted.set_cookie('faculty_workspace','expired-workspace-cookie')
        self.assertEqual(restarted.get(self.route(self.groups[0])).json['accommodation_count'],1)
        self.assertEqual(restarted.post('/api/accommodations/preview',data={'file':(io.BytesIO(workbook()),'x.xlsx')},headers=HEADERS).status_code,200)
        self.assertEqual(self.client.post('/api/accommodations/preview',data={'file':(io.BytesIO(workbook()),'x.xlsx')},headers={**HEADERS,'Origin':'https://elsewhere.test'}).status_code,403)
        self.assertNotIn('Extra Time',self.client.get('/api/export').get_data(as_text=True))

    def test_missing_students_and_columns_retain_existing_accommodations(self):
        self.submit(workbook())
        saved=self.roster(self.groups[0])['students'][0]['accommodations']
        self.submit(workbook([['Example','Bob','123456789','','MATH','140','507','Read','2026-09-14','Yes','','']]))
        self.assertEqual(self.roster(self.groups[0])['students'][0]['accommodations'],saved)
        columns=[h for h in HEADINGS if h!='Reduced Distraction Environment']
        columns[columns.index('Extra Time 1.50x')]='EXTRA TIME 1.50x'
        content=workbook([['Example','Alice','001234567','','MATH','140','507','Read','2026-09-14','No','']],columns)
        self.submit(content)
        self.assertEqual(self.roster(self.groups[0])['students'][0]['accommodations']['items'],
                         [{'name':'Reduced Distraction Environment','value':'Yes'}])

    def test_lost_response_reuses_completed_job_and_rejects_changed_content(self):
        content=workbook(); preview=self.preview(content); key=str(uuid.uuid4())
        def post(data):
            return self.client.post('/api/accommodations/import',data={'file':(io.BytesIO(data),'export.xlsx'),
                'preview_hash':preview['preview_hash'],'idempotency_key':key},headers=HEADERS)
        first=post(content); self.assertEqual(first.status_code,202)
        self.inbox.process_one()
        again=post(content); self.assertEqual(again.status_code,202,again.json)
        self.assertEqual(again.json['id'],first.json['id']); self.assertEqual(again.json['status'],'completed')
        self.assertEqual(post(workbook([['Example','Bob','123456789','','MATH','140','507','Read','2026-09-14','Yes','','']])).status_code,409)

    def test_cross_course_transaction_failure_rolls_back_every_student(self):
        content=workbook([
            ['Example','Alice','001234567','','MATH','140','507','Read','2026-09-13','Yes','',''],
            ['Example','Alice','001234567','','MATH','151','507','Read','2026-09-13','','Yes','']])
        with connect(self.service.path) as db:
            db.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON accommodation_revisions WHEN NEW.source_row=3 BEGIN SELECT RAISE(ABORT,'Simulated storage failure'); END")
        job=self.submit(content); self.assertEqual(job['status'],'failed')
        with connect(self.service.path) as db:
            for table in ('accommodation_imports','accommodation_revisions','student_accommodations'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        self.assertNotIn('accommodations',self.roster(self.groups[0])['students'][0])

    def test_roster_change_after_queue_stops_worker_without_writes(self):
        content=workbook(); preview=self.preview(content)
        response=self.client.post('/api/accommodations/import',data={'file':(io.BytesIO(content),'export.xlsx'),
            'preview_hash':preview['preview_hash']},headers=HEADERS)
        self.assertEqual(response.status_code,202)
        offering=self.groups[0]['offerings'][0]
        self.client.patch('/api/courses/'+offering['id'],json={'version':offering['version'],'is_current':False},headers=HEADERS)
        self.inbox.process_one(); job=self.inbox.get(response.json['id'])
        self.assertEqual(job['status'],'failed'); self.assertIn('changed after the preview',job['error'])
        with connect(self.service.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accommodation_imports').fetchone()[0],0)


class AccommodationParserTests(unittest.TestCase):
    def test_numeric_uin_format_and_dynamic_columns_preserved(self):
        book=Workbook(); book.active.append(HEADINGS)
        book.active.append(['Example','Alice',1234567,'','MATH','0140','0507','Read','2026-09-13','Yes','',''])
        book.active['C2'].number_format='000000000'
        out=io.BytesIO(); book.save(out); book.close()
        row=parse_accommodations(out.getvalue(),'x.xlsx')['rows'][0]
        self.assertEqual((row['uin'],row['code'],row['section']),('001234567','MATH 0140',507))

    def test_a_last_updated_text_with_surrounding_spaces_is_read_as_the_date(self):
        for text in (' 2026-09-13','09/13/2026 ','  2026-09-13  '):
            with self.subTest(text=text):
                row=['Example','Alice','001234567','','MATH','140','507','Read',text,'Yes','','']
                self.assertEqual(parse_accommodations(workbook([row]),'x.xlsx')['rows'][0]['updated'],'2026-09-13T00:00:00')

    def test_bad_identity_conflicting_duplicate_formula_and_extra_sheet_fail(self):
        row=['Example','Alice','001234567','','MATH','140','507','Read','2026-09-13','Yes','','']
        for col,value in ((2,'123'),(4,'123'),(6,'0'),(8,'not a date'),(11,'=1+1')):
            changed=row.copy(); changed[col]=value
            with self.subTest(col=col),self.assertRaises(ValueError):parse_accommodations(workbook([changed]),'x.xlsx')
        changed=row.copy(); changed[9]='No'
        with self.assertRaisesRegex(ValueError,'conflicts'):parse_accommodations(workbook([row,changed]),'x.xlsx')
        self.assertEqual(len(parse_accommodations(workbook([row,row]),'x.xlsx')['rows']),1)
        book=Workbook();book.active.append(HEADINGS);book.create_sheet();out=io.BytesIO();book.save(out);book.close()
        with self.assertRaises(ValueError):parse_accommodations(out.getvalue(),'x.xlsx')


if __name__=='__main__':unittest.main()
