"""DR exam bookings owned by existing section/enrollment memberships.

A source row updates one named exam. Schedule revisions and source workbooks are
retained; filtered uploads never remove other bookings or accommodation grants.
"""
from contextlib import ExitStack
import json
import uuid

from werkzeug.exceptions import NotFound

from accommodations import validate_metadata
from course_storage import timestamp
from accommodation_import import display_name
from dr_exam_import import parse_dr_exams
from rosters import connect
from teach_inbox.service import encoded, sha


class DrExamService:
    def __init__(self, accommodations):
        self.accommodations = accommodations
        self.rosters, self.path = accommodations.rosters, accommodations.path
        with self.rosters.lock(), connect(self.path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS dr_exam_imports (
                    job_id TEXT PRIMARY KEY REFERENCES inbox_jobs(id),
                    source_artifact_id TEXT NOT NULL REFERENCES inbox_artifacts(id),
                    source_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dr_exam_revisions (
                    id TEXT PRIMARY KEY, section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
                    exam_key TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES dr_exam_imports(job_id),
                    source_row INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
                );
                CREATE TABLE IF NOT EXISTS dr_exam_bookings (
                    id TEXT PRIMARY KEY, section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
                    exam_key TEXT NOT NULL, revision_id TEXT NOT NULL REFERENCES dr_exam_revisions(id),
                    UNIQUE(section_id,enrollment_id,exam_key),
                    FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
                );
                CREATE INDEX IF NOT EXISTS dr_exam_history ON dr_exam_revisions(section_id,enrollment_id,exam_key,created_at);
                CREATE TRIGGER IF NOT EXISTS dr_exam_revision_owner BEFORE INSERT ON dr_exam_bookings
                    WHEN NOT EXISTS(SELECT 1 FROM dr_exam_revisions WHERE id=NEW.revision_id AND section_id=NEW.section_id AND enrollment_id=NEW.enrollment_id AND exam_key=NEW.exam_key)
                    BEGIN SELECT RAISE(ABORT,'DR exam revision owner mismatch'); END;
                CREATE TRIGGER IF NOT EXISTS dr_exam_update_owner BEFORE UPDATE ON dr_exam_bookings
                    WHEN OLD.id IS NOT NEW.id OR OLD.section_id IS NOT NEW.section_id OR OLD.enrollment_id IS NOT NEW.enrollment_id OR OLD.exam_key IS NOT NEW.exam_key OR
                    NOT EXISTS(SELECT 1 FROM dr_exam_revisions WHERE id=NEW.revision_id AND section_id=NEW.section_id AND enrollment_id=NEW.enrollment_id AND exam_key=NEW.exam_key)
                    BEGIN SELECT RAISE(ABORT,'DR exam identity is retained'); END;
                CREATE TRIGGER IF NOT EXISTS dr_exam_booking_retained BEFORE DELETE ON dr_exam_bookings
                    BEGIN SELECT RAISE(ABORT,'DR exam bookings are retained'); END;
            ''')
            for table in ('dr_exam_imports','dr_exam_revisions'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'DR exam history is immutable'); END")

    def preview(self, parsed, term=None):
        validate_metadata({'term':term,'preview_hash':'0'*64})
        with self.rosters.lock():
            plan = self._plan(parsed,term)
        # Display only, added after the preview hash: the name as rosters show it ('Last, First').
        for source, row in zip(parsed['rows'],plan['rows'],strict=True):
            row['display_name'] = display_name(source)
        return plan

    def _plan(self, parsed, term):
        plan = self.accommodations.match_rows(parsed,term)
        with connect(self.path) as db:
            for source, row in zip(parsed['rows'],plan,strict=True):
                row['schedule'] = {k:v for k,v in source['snapshot'].items() if k!='source_fields'}
                row['exam_key'] = source['exam_key']
                if row['status'] != 'matched':
                    continue
                prior = db.execute('''SELECT b.id booking_id,r.* FROM dr_exam_bookings b
                    JOIN dr_exam_revisions r ON r.id=b.revision_id
                    WHERE b.section_id=? AND b.enrollment_id=? AND b.exam_key=?''',
                    (row['section_id'],row['enrollment_id'],source['exam_key'])).fetchone()
                digest = sha(encoded(source['snapshot']).encode())
                previous = json.loads(prior['snapshot_json']) if prior else None
                row.update(status='unchanged' if prior and prior['snapshot_sha256']==digest else 'updated' if prior else 'added',
                           booking_id=prior['booking_id'] if prior else None, previous_revision=prior['id'] if prior else None,
                           previous_schedule={k:v for k,v in previous.items() if k!='source_fields'} if previous else None,
                           snapshot=source['snapshot'],snapshot_sha256=digest)
        counts = {status:sum(r['status']==status for r in plan) for status in ('added','updated','unchanged','unmatched','ambiguous','stale')}
        return {'rows':plan,'counts':counts,'total':len(plan),'preview_hash':sha(encoded({'source':parsed,'term':term,'plan':plan}).encode())}

    def import_job(self, job, file):
        metadata = job['parameters']['metadata'];validate_metadata(metadata)
        parsed = parse_dr_exams(file['content'],file['filename'])
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                prior = db.execute('SELECT * FROM dr_exam_imports WHERE job_id=?',(job['id'],)).fetchone()
                if prior:
                    if prior['source_artifact_id']!=file['id'] or prior['source_sha256']!=sha(file['content']):
                        raise ValueError('The saved DR exam import belongs to different input.')
                    return json.loads(prior['result_json'])
            plan = self._plan(parsed,metadata['term'])
            if plan['preview_hash']!=metadata['preview_hash']:
                raise ValueError('Rosters or DR exam bookings changed after the preview. Preview and import the file again.')
            changes = [r for r in plan['rows'] if r['status'] in ('added','updated')]
            if self.rosters.storage:
                for owner in sorted({r['section_id'] for r in changes}):
                    locks.enter_context(self.rosters.storage.lock(owner))
            result = {'message':f"DR exam bookings imported: {plan['counts']['added']} added, {plan['counts']['updated']} updated, {plan['counts']['unchanged']} unchanged.",
                      'counts':plan['counts'],'total':plan['total'],
                      'rows':[{k:v for k,v in r.items() if k not in ('snapshot','snapshot_sha256','previous_revision')} for r in plan['rows']]}
            with connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                # ACCOMMODATIONS: keep the complete first answer for receipt recovery, including the assignments
                # linked below. Defer only this transaction's foreign keys so the immutable receipt can be inserted last.
                db.execute('PRAGMA defer_foreign_keys=ON')
                imported_at = timestamp()
                for row in changes:
                    revision = str(uuid.uuid4())
                    db.execute('INSERT INTO dr_exam_revisions VALUES(?,?,?,?,?,?,?,?,?)',(revision,row['section_id'],row['enrollment_id'],row['exam_key'],
                        job['id'],row['row'],encoded(row['snapshot']),row['snapshot_sha256'],timestamp()))
                    db.execute('''INSERT INTO dr_exam_bookings VALUES(?,?,?,?,?)
                        ON CONFLICT(section_id,enrollment_id,exam_key) DO UPDATE SET revision_id=excluded.revision_id''',
                        (row['booking_id'] or str(uuid.uuid4()),row['section_id'],row['enrollment_id'],row['exam_key'],revision))
                if getattr(self, 'exam_links', None):
                    # New bookings are matched to the course's exam assignments (creating one
                    # when none matches) in the same transaction as the import itself.
                    result['exam_links'] = self.exam_links.sync(db)
                db.execute('INSERT INTO dr_exam_imports VALUES(?,?,?,?,?)',
                           (job['id'],file['id'],sha(file['content']),encoded(result),imported_at))
            return result

    @staticmethod
    def record(row):
        snapshot = json.loads(row['snapshot_json'])
        return {**{k:v for k,v in snapshot.items() if k!='source_fields'},'id':row['booking_id'],
                'revision_id':row['id'],'job_id':row['job_id'],'imported_at':row['created_at']}

    def attach(self, roster, db=None):
        if db is None:
            with connect(self.path) as db:
                return self.attach(roster, db)
        rows = db.execute('''SELECT b.id booking_id,r.* FROM dr_exam_bookings b
            JOIN dr_exam_revisions r ON r.id=b.revision_id WHERE b.section_id=?''',(roster['section_id'],)).fetchall()
        links = {}
        if getattr(self, 'exam_links', None):
            links = {r['booking_id']:{'id':r['assignment_id'],'title':r['title'],'method':r['method']} for r in db.execute('''SELECT l.booking_id,l.assignment_id,l.method,a.title
                FROM dr_exam_assignment_links l JOIN assignments a ON a.id=l.assignment_id
                JOIN dr_exam_bookings b ON b.id=l.booking_id WHERE b.section_id=?''',(roster['section_id'],))}
        # Read only: how many imported versions each booking has, and whether its date or times ever changed.
        versions = {(r['enrollment_id'],r['exam_key']):(r['revisions'],r['schedules']) for r in db.execute('''SELECT enrollment_id,exam_key,
            COUNT(*) revisions,COUNT(DISTINCT json_extract(snapshot_json,'$.date')||' '||json_extract(snapshot_json,'$.start_time')||' '||json_extract(snapshot_json,'$.end_time')) schedules
            FROM dr_exam_revisions WHERE section_id=? GROUP BY enrollment_id,exam_key''',(roster['section_id'],))}
        records = {}
        for row in rows:
            revisions, schedules = versions.get((row['enrollment_id'],row['exam_key']),(1,1))
            records.setdefault(row['enrollment_id'],[]).append({**self.record(row),'assignment':links.get(row['booking_id']),
                'revision_count':revisions,'rescheduled':schedules > 1})
        for student in roster['students']:
            if student['id'] in records:
                student['dr_exams'] = sorted(records[student['id']],key=lambda r:(r['date'],r['start_time'],r['exam_type']))
        roster['dr_exam_count'] = len(rows)
        return roster

    def history(self, booking_id):
        with self.rosters.lock(), connect(self.path) as db:
            booking = db.execute('SELECT * FROM dr_exam_bookings WHERE id=?',(booking_id,)).fetchone()
            if not booking:
                raise NotFound('DR exam booking not found.')
            rows = db.execute('''SELECT ? booking_id,r.* FROM dr_exam_revisions r
                WHERE section_id=? AND enrollment_id=? AND exam_key=? ORDER BY created_at DESC,rowid DESC''',
                (booking_id,booking['section_id'],booking['enrollment_id'],booking['exam_key'])).fetchall()
            return {'revisions':[self.record(row) for row in rows]}
