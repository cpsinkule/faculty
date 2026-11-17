"""DR bookings: exact ownership, filtered reimports, revisions and atomic recovery."""
import io
import json
import sqlite3
import unittest
import uuid
from datetime import datetime, time
from unittest.mock import patch

from openpyxl import Workbook

from dr_exam_import import parse_dr_exams
from rosters import connect
import test_accommodations as accommodation_tests
from test_storage import HEADERS

HEADINGS = ['Email Address (TAMU)','CRN','Subject','Course','Section','Last Name','First Name','UIN',
            'Exam Type','Date','Start Time','End Time','Length','Campus','Requested On']


def booking(**changes):
    return {'Email Address (TAMU)':'alice@example.test','CRN':'123456','Subject':'MATH','Course':'140','Section':'507',
            'Last Name':'Example','First Name':'Alice','UIN':'001234567','Exam Type':'Test 1',
            'Date':datetime(2026,9,17),'Start Time':'07:05 PM','End Time':'08:58 PM','Length':113,
            'Campus':'College Station','Requested On':datetime(2026,9,3),**changes}


def workbook(rows=None):
    book=Workbook();book.active.append(HEADINGS)
    for row in rows or [booking()]:book.active.append([row.get(h,'') for h in HEADINGS])
    out=io.BytesIO();book.save(out);book.close();return out.getvalue()


class DrExamTests(unittest.TestCase):
    route=accommodation_tests.AccommodationTests.route
    roster=accommodation_tests.AccommodationTests.roster

    def setUp(self):
        accommodation_tests.AccommodationTests.setUp(self)
        self.dr=self.app.extensions['dr_exams']

    def post(self,kind,content,preview=None,key=None,term=None):
        fields={'file':(io.BytesIO(content),'exams.xlsx'),'term':json.dumps(term)}
        if preview:fields.update(preview_hash=preview['preview_hash'],idempotency_key=key or str(uuid.uuid4()))
        return self.client.post('/api/accommodations/exams/'+kind,data=fields,headers=HEADERS)

    def preview(self,content,term=None):
        response=self.post('preview',content,term=term)
        self.assertEqual(response.status_code,200,response.json);return response.json

    def submit(self,content,preview=None,key=None,term=None,process=True):
        response=self.post('import',content,preview or self.preview(content,term),key,term)
        self.assertEqual(response.status_code,202,response.json)
        if process:self.inbox.process_one()
        return self.inbox.get(response.json['id'])

    def records(self,group=0):
        return self.roster(self.groups[group])['students'][0].get('dr_exams',[])

    def test_mixed_courses_share_import_but_preserve_independent_bookings(self):
        before=[self.roster(g) for g in self.groups]
        content=workbook([booking(),booking(Course='151',**{'Start Time':'11:00 AM','End Time':'12:53 PM'}),booking(UIN='999999999')])
        preview=self.preview(content)
        self.assertEqual(preview['counts']['added'],2);self.assertEqual(preview['counts']['unmatched'],1)
        self.assertEqual([self.roster(g) for g in self.groups],before)
        job=self.submit(content,preview);self.assertEqual(job['status'],'completed',job['error'])
        self.assertEqual(self.records()[0]['start_time'],'19:05')
        self.assertEqual(self.records(1)[0]['start_time'],'11:00')
        self.assertEqual(self.records(2),[])
        self.assertNotIn('dr_exams',self.roster(self.groups[0],508)['students'][0])
        for prior,group in zip(before,self.groups):
            self.assertEqual([(s['id'],s['fields']) for s in prior['students']],[(s['id'],s['fields']) for s in self.roster(group)['students']])
        self.assertNotIn('accommodations',self.roster(self.groups[0])['students'][0])
        source=next(a for a in job['artifacts'] if a['role']=='input')
        self.assertEqual(self.inbox.read_artifact(source['id'])[1],content)
        self.assertEqual(self.storage.backup_all()['errors'],[])
        self.assertEqual(self.inbox.read_artifact(source['id'])[1],content)
        overview=self.client.get('/api/accommodations/overview')
        self.assertEqual(overview.status_code,200,overview.json)
        self.assertEqual(len(overview.json['students']),2)
        # Freshness: the latest import, its Inbox record and the row it could not match; each student's term owner.
        latest=overview.json['last_imports']['dr_exams']
        self.assertEqual((latest['job_id'],latest['counts']['added'],latest['not_imported']),(job['id'],2,1))
        self.assertIsNone(overview.json['last_imports']['accommodations'])
        terms=self.client.get('/api/course-groups/summary').json['groups']
        self.assertEqual({s['term_id'] for s in overview.json['students']},{terms[self.groups[0]['id']]['term_id'],terms[self.groups[1]['id']]['term_id']})
        # A submission deleted from the Inbox counts nowhere, the freshness line included.
        self.inbox.remove(job['id'],self.inbox.get(job['id'])['version'],'delete')
        self.assertIsNone(self.client.get('/api/accommodations/overview').json['last_imports']['dr_exams'])
        self.assertNotIn('dr_exams',self.client.get('/api/export').json)

    def test_rescheduling_keeps_booking_identity_and_filtered_omissions(self):
        content=workbook([booking(),booking(**{'Exam Type':'Test 2','Date':datetime(2026,10,22)})])
        self.submit(content);initial=self.records()
        self.assertEqual(self.submit(content)['result']['counts']['unchanged'],2)
        moved=workbook([booking(**{'Exam Type':' test 1 ','Date':datetime(2026,9,18),'Start Time':'11:00 AM','End Time':'12:53 PM'})])
        preview=self.preview(moved)
        self.assertEqual(preview['counts']['updated'],1)
        self.assertEqual(preview['rows'][0]['previous_schedule']['date'],'2026-09-17')
        self.assertRegex(preview['rows'][0]['display_name'],r'^[^,]+, [^,]+$')
        self.submit(moved,preview)
        later=self.records();self.assertEqual(len(later),2)
        self.assertEqual(later[0]['id'],initial[0]['id']);self.assertEqual(later[0]['date'],'2026-09-18')
        self.assertEqual(later[1],initial[1])
        # Read-only: how many versions a booking has, and whether its date or times changed.
        self.assertEqual((later[0]['revision_count'],later[0]['rescheduled']),(2,True))
        self.assertEqual((later[1]['revision_count'],later[1]['rescheduled']),(1,False))
        history=self.client.get('/api/accommodations/exams/'+later[0]['id']+'/history').json['revisions']
        self.assertEqual([r['date'] for r in history],['2026-09-18','2026-09-17'])
        self.assertEqual({r['requested_on'] for r in history},{'2026-09-03'})
        with connect(self.dr.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dr_exam_revisions').fetchone()[0],3)
            with self.assertRaises(sqlite3.IntegrityError):db.execute("DELETE FROM dr_exam_revisions")

    def test_quiz_bookings_are_told_apart_by_date(self):
        # ACCOMMODATIONS "QUIZ BOOKINGS BY DATE": AIM books every quiz date as another "Quiz" row (the 2026-11-07 export).
        def quiz(day,**changes):
            return booking(**{'Exam Type':'Quiz','Date':datetime(2026,11,day),'Start Time':'07:30 PM','End Time':'08:05 PM','Length':35,
                              'Requested On':datetime(2026,11,3),**changes})
        content=workbook([quiz(13),booking(**{'Exam Type':'Test 3','Date':datetime(2026,11,19)}),quiz(1,Date=datetime(2026,12,1))])
        rows=parse_dr_exams(content,'x.xlsx')['rows']
        self.assertEqual([r['exam_key'] for r in rows],['quiz 2026-11-13','test 3','quiz 2026-12-01'])
        self.assertEqual(self.submit(content)['result']['counts']['added'],3)
        saved=sorted((r['exam_type'],r['date']) for r in self.records())
        self.assertEqual(saved,[('Quiz','2026-11-13'),('Quiz','2026-12-01'),('Test 3','2026-11-19')])
        with connect(self.dr.path) as db:  # both quiz dates link to the one assignment their Exam Type names
            linked=db.execute("SELECT COUNT(DISTINCT l.assignment_id),COUNT(*) FROM dr_exam_assignment_links l JOIN dr_exam_bookings b ON b.id=l.booking_id WHERE b.exam_key LIKE 'quiz %'").fetchone()
        self.assertEqual(tuple(linked),(1,2))
        # A later export with one quiz date changes only that date's booking.
        later=workbook([quiz(13,**{'Start Time':'08:00 PM','End Time':'08:35 PM'})])
        preview=self.preview(later)
        self.assertEqual((preview['counts']['updated'],preview['rows'][0]['previous_schedule']['start_time']),(1,'19:30'))
        # Two different Quiz bookings on one day are still the export's to correct, and a Test still has one booking.
        with self.assertRaisesRegex(ValueError,r'^Row 3 conflicts with row 2: Alice Example has two Quiz bookings .* differ in Start Time, End Time\.'):
            parse_dr_exams(workbook([quiz(13),quiz(13,**{'Start Time':'03:00 PM','End Time':'03:35 PM'})]),'x.xlsx')
        with self.assertRaisesRegex(ValueError,r'^Row 3 conflicts with row 2: .* two Test 3 bookings .* differ in Date\.'):
            parse_dr_exams(workbook([booking(**{'Exam Type':'Test 3','Date':datetime(2026,11,19)}),booking(**{'Exam Type':'Test 3','Date':datetime(2026,11,20)})]),'x.xlsx')

    def test_course_group_summary_counts_current_bookings_per_section(self):
        content=workbook([booking(),booking(**{'Exam Type':'Test 2','Date':datetime(2026,10,22)}),booking(Course='151')])
        self.assertEqual(self.submit(content)['status'],'completed')
        groups=self.client.get('/api/course-groups/summary').json['groups']
        section=groups[self.groups[0]['id']]['sections']['507']
        self.assertEqual((section['dr_bookings'],section['with_dr_bookings']),(2,1))
        self.assertEqual(groups[self.groups[0]['id']]['sections']['508']['dr_bookings'],0)
        self.assertEqual(groups[self.groups[1]['id']]['sections']['507']['dr_bookings'],1)
        self.assertEqual(groups[self.groups[2]['id']]['sections']['507']['dr_bookings'],0)
        # A rescheduled booking is still one booking.
        self.submit(workbook([booking(**{'Date':datetime(2026,9,18)})]))
        self.assertEqual(self.client.get('/api/course-groups/summary').json['groups'][self.groups[0]['id']]['sections']['507']['dr_bookings'],2)

    def test_lost_response_and_worker_receipt_retry_do_not_reapply_schedules(self):
        content=workbook();preview=self.preview(content);key=str(uuid.uuid4())
        real=self.inbox._receipt
        def fail(job,result=None):
            if result is not None:raise OSError('Receipt interrupted')
            return real(job,result)
        with patch.object(self.inbox,'_receipt',side_effect=fail):job=self.submit(content,preview,key)
        self.assertEqual(job['status'],'failed')
        with connect(self.dr.path) as db:
            first = json.loads(db.execute('SELECT result_json FROM dr_exam_imports WHERE job_id=?', (job['id'],)).fetchone()[0])
        self.assertTrue(first['exam_links']['created_assignments'])
        self.inbox.transition(job['id'],'retry',job['version']);self.inbox.process_one()
        duplicate=self.post('import',content,preview,key)
        self.assertEqual(duplicate.status_code,202,duplicate.json)
        self.assertEqual((duplicate.json['id'],duplicate.json['status']),(job['id'],'completed'))
        self.assertEqual(duplicate.json['result'],first)
        self.assertEqual(self.post('import',workbook([booking(Length=120)]),preview,key).status_code,409)
        self.assertEqual(len(self.client.get('/api/accommodations/exams/'+self.records()[0]['id']+'/history').json['revisions']),1)

    def test_stale_preview_and_concurrent_worker_changes_stop_before_writes(self):
        old=self.preview(workbook())
        self.submit(workbook([booking(**{'Date':datetime(2026,9,18)})]))
        self.assertEqual(self.post('import',workbook(),old).status_code,409)
        job=self.submit(workbook(),process=False)
        offering=self.groups[0]['offerings'][0]
        self.client.patch('/api/courses/'+offering['id'],json={'version':offering['version'],'is_current':False},headers=HEADERS)
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'],'failed')
        self.assertEqual(self.records()[0]['date'],'2026-09-18')

    def test_multi_course_transaction_failure_rolls_back_every_booking(self):
        with connect(self.dr.path) as db:
            db.execute("CREATE TRIGGER inject_dr_failure BEFORE INSERT ON dr_exam_revisions WHEN NEW.source_row=3 BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        job=self.submit(workbook([booking(),booking(Course='151')]))
        self.assertEqual(job['status'],'failed')
        with connect(self.dr.path) as db:
            for table in ('dr_exam_imports','dr_exam_revisions','dr_exam_bookings'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_deferred_link_failure_rolls_back_the_complete_import(self):
        def bad_link(db):
            db.execute('INSERT INTO dr_exam_assignment_links VALUES(?,?,?,?,?)',
                       ('missing-booking', 'missing-assignment', 'matched', 'now', 'now'))
            return {'matched': 1, 'created': 0, 'created_assignments': []}
        with patch.object(self.dr.exam_links, 'sync', side_effect=bad_link):
            job = self.submit(workbook())
        self.assertEqual(job['status'], 'failed')
        with connect(self.dr.path) as db:
            self.assertEqual(db.execute('PRAGMA defer_foreign_keys').fetchone()[0], 0)
            for table in ('dr_exam_imports', 'dr_exam_revisions', 'dr_exam_bookings', 'dr_exam_assignment_links'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_term_choice_no_cookie_and_cross_site_boundary(self):
        offering=self.groups[2]['offerings'][0]
        self.client.patch('/api/courses/'+offering['id'],json={'version':offering['version'],'is_current':True},headers=HEADERS)
        self.storage.reconcile_all()
        self.assertEqual(self.preview(workbook())['counts']['ambiguous'],1)
        term={'term':'Fall','term_other':'','year':2026}
        job=self.submit(workbook(),term=term);self.assertEqual(job['status'],'completed')
        self.assertEqual(self.records(2),[])
        fresh=self.app.test_client()
        self.assertEqual(fresh.get('/api/accommodations/overview').status_code,200)
        self.assertEqual(fresh.post('/api/accommodations/exams/preview',data={'file':(io.BytesIO(workbook()),'x.xlsx')}).status_code,403)
        self.assertEqual(fresh.post('/api/accommodations/exams/preview',data={'file':(io.BytesIO(workbook()),'x.xlsx')},headers={**HEADERS,'Origin':'https://elsewhere.test'}).status_code,403)


class DrExamParserTests(unittest.TestCase):
    def test_excel_dates_times_and_leading_zero_uin(self):
        row=parse_dr_exams(workbook([booking(**{'Start Time':time(19,5),'End Time':time(20,58)})]),'x.xlsx')['rows'][0]
        self.assertEqual(row['uin'],'001234567')
        self.assertEqual(row['snapshot']['start_time'],'19:05')
        self.assertEqual(row['snapshot']['date'],'2026-09-17')

    def test_duplicate_conflicts_formulas_wrong_export_and_invalid_fields_rejected(self):
        for field,value in [('UIN','123'),('Exam Type',''),('Date','not a date'),('Start Time','25:00'),('Length',0),('Campus','=1+1')]:
            with self.subTest(field=field),self.assertRaises(ValueError):parse_dr_exams(workbook([booking(**{field:value})]),'x.xlsx')
        with self.assertRaisesRegex(ValueError,r'^Row 3 conflicts with row 2: Alice Example has two Test 1 bookings in MATH 140 section 507 that differ in Length\.'):
            parse_dr_exams(workbook([booking(),booking(Length=120)]),'x.xlsx')
        self.assertEqual(len(parse_dr_exams(workbook([booking(),booking()]),'x.xlsx')['rows']),1)
        # The other export names the importer to use (message only).
        with self.assertRaisesRegex(ValueError,'looks like an accommodation export. Use Import accommodations'):parse_dr_exams(accommodation_tests.workbook(),'x.xlsx')
        with self.assertRaisesRegex(ValueError,'looks like a DR exam bookings export. Use Import DR exam bookings'):accommodation_tests.parse_accommodations(workbook(),'x.xlsx')
        other=workbook();from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(other));book.active.delete_cols(HEADINGS.index('Exam Type')+1);out=io.BytesIO();book.save(out)
        with self.assertRaisesRegex(ValueError,'needs distinct headings'):parse_dr_exams(out.getvalue(),'x.xlsx')


if __name__=='__main__':unittest.main()
