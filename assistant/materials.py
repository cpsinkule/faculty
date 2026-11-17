"""ASSISTANT: reviewed teaching content enters its owning service; workspace receipts only."""
import json
import uuid

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect
from .evidence import digest
from .transport import clean

KINDS = ('assignment_version', 'quiz_draft', 'rubric', 'course_notes')


class Refused(ValueError):
    """The import certainly changed nothing: refused before any destination call, or by the destination itself."""


class Materials:
    def __init__(self, service):
        self.service = service

    def preview(self, data):
        kind = data.get('kind')
        if kind not in KINDS:
            raise ValueError('ASSISTANT: choose assignment_version, quiz_draft, rubric or course_notes.')
        title = str(data.get('title') or '').strip()
        if not title or len(title) > 200:
            raise ValueError('ASSISTANT: give the material a title of up to 200 characters.')
        source = data.get('content')
        artifact = data.get('artifact_id')
        if bool(source) == bool(artifact) or (source is not None and (not isinstance(source, str) or not source.strip() or len(source) > 180000)):
            raise ValueError('ASSISTANT: provide bounded text content or one retained artifact ID, not both.')
        with connect(self.service.database) as db:
            assignment = plan = term = None
            if kind == 'assignment_version':
                assignment = db.execute('SELECT id,definition_id,title FROM assignments WHERE id=?', (data.get('assignment_id'),)).fetchone()
                definition = assignment['definition_id'] if assignment else None
            elif kind == 'rubric':
                plan = db.execute('''SELECT f.id,e.term_id,a.definition_id,a.title FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id
                    JOIN assignments a ON a.id=e.assignment_id WHERE f.id=?''', (data.get('plan_id'),)).fetchone()
                definition = plan['definition_id'] if plan else None
            elif kind == 'quiz_draft':
                term = db.execute('SELECT id,definition_id,code,term,year FROM course_terms WHERE id=? AND deleted_at IS NULL', (data.get('term_id'),)).fetchone()
                definition = term['definition_id'] if term else None
            else:
                definition = data.get('definition_id')
            course = db.execute('SELECT code FROM course_definitions WHERE id=?', (definition,)).fetchone() if definition else None
            if not course:
                raise NotFound('HIERARCHY: the material destination does not exist.')
            destination = ' · '.join(filter(None, [course['code'], assignment['title'] if assignment else plan['title'] if plan else
                                                 f"{term['term']} {term['year']}" if term else 'Course-wide notes']))
            meta = db.execute('SELECT * FROM assistant_artifacts WHERE id=?', (artifact,)).fetchone() if artifact else None
            if artifact and (not meta or meta['definition_id'] != definition):
                raise ValueError('HIERARCHY: choose a retained course-wide artifact of the destination course.')
        fields = {k: data[k] for k in ('assignment_id', 'plan_id', 'question_id', 'term_id', 'definition_id', 'mode', 'versions', 'total', 'format', 'instructions', 'blueprint') if k in data}
        basis = None
        if kind == 'rubric':
            status, view, _ = self.service.transport.get('/api/grading/assignments/' + data['plan_id'])
            if status != 200 or not any(q['question_id'] == data.get('question_id') for q in view.get('questions', [])):
                raise ValueError('GRADING: choose a question belonging to this grading plan.')
            basis = next(q['version'] for q in view['questions'] if q['question_id'] == data['question_id'])
        if meta:
            media, filename = meta['media_type'], meta['filename']
            if meta['size'] > 25 * 1024 * 1024:
                raise ValueError('ASSISTANT: retained material must be at most 25 MB.')
            # STORAGE: the retained file's recorded sha256 is verified on every read (artifacts.read, at import).
            source_hash = digest([artifact, meta['sha256']])
        else:
            filename, media = ('main.tex', 'text/x-tex') if kind in ('assignment_version', 'course_notes') else ('material.txt', 'text/plain')
            source_hash = digest(source)
        if kind == 'assignment_version' and artifact and not filename.endswith('.tex'):
            raise ValueError('ASSISTANT: assignment compilation needs LaTeX content or a retained .tex artifact.')
        if kind == 'course_notes' and artifact and media != 'application/pdf':
            raise ValueError('COURSE-NOTES: choose a retained PDF or supply complete LaTeX content.')
        if kind == 'rubric' and artifact and not (media.startswith('text/') or media == 'application/json'):
            raise ValueError('GRADING: rubric import needs text; read the PDF and prepare its rubric text first.')
        if kind == 'quiz_draft':
            from grading.quiz_drafts import MAX_VERSIONS
            if type(fields.get('versions')) is not int or not 1 <= fields['versions'] <= MAX_VERSIONS:
                raise ValueError('QUIZZES: choose the number of quiz versions before importing material.')
            if not isinstance(fields.get('total'), (int, float)) or not .5 <= fields['total'] <= 100 or len(title) > 120:
                raise ValueError('QUIZZES: choose 0.5–100 total points and a title of at most 120 characters.')
        canonical = {'kind': kind, 'title': title, 'definition_id': definition, 'fields': fields,
                     'artifact_id': artifact, 'source_hash': source_hash, 'basis': basis, 'destination': destination}
        # HIERARCHY: the receipt names the destination's owner (rubrics and quiz drafts belong to a term).
        owner = ('term', plan['term_id']) if plan else ('term', term['id']) if term else ('definition', definition)
        return {**canonical, 'fingerprint': digest(canonical), 'owner': owner,
                'note': 'Imports the reviewed material into its owning service. Nothing is printed, sent, certified or published.'}

    def replayed(self, request, fingerprint):
        """A request ID's retained outcome, read before any new preview: an import changes its own destination's basis."""
        with connect(self.service.database) as db:
            old = db.execute('SELECT * FROM assistant_material_requests WHERE request_id=?', (request,)).fetchone()
        if not old:
            return None
        if old['fingerprint'] != fingerprint:
            raise Conflict('ASSISTANT: that request ID names different material.')
        if old['state'] == 'failed':
            raise Conflict('ASSISTANT: that import was refused and changed nothing: ' +
                           str(json.loads(old['result_json']).get('error') or 'review the material and prepare it again.'))
        if old['state'] != 'complete':
            raise Conflict('ASSISTANT: the previous import outcome needs review; it will not be repeated.')
        return json.loads(old['result_json'])

    def create(self, data):
        request = self.service.workflows.request(data)
        done = self.replayed(request, data.get('fingerprint'))
        if done is not None:
            return done
        preview = self.preview(data)
        if data.get('fingerprint') != preview['fingerprint']:
            raise Conflict('ASSISTANT: review the material and destination again.')
        with self.service.rosters.lock(), connect(self.service.database) as db:
            if db.execute('SELECT 1 FROM assistant_material_requests WHERE request_id=?', (request,)).fetchone():
                raise Conflict('ASSISTANT: this import is already recorded; read its outcome again.')
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO assistant_material_requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (ident, request, preview['fingerprint'], preview['kind'], *preview['owner'],
                        'running', '{}', timestamp(), timestamp()))
        try:
            result = self.import_material(data, preview)
        except Refused as error:
            with self.service.rosters.lock(), connect(self.service.database) as db:
                db.execute("UPDATE assistant_material_requests SET state='failed',result_json=?,updated_at=? WHERE id=?",
                           (json.dumps({'error': str(error)[:2000]}), timestamp(), ident))
            raise
        except Exception:
            with self.service.rosters.lock(), connect(self.service.database) as db:
                db.execute("UPDATE assistant_material_requests SET state='uncertain',updated_at=? WHERE id=?", (timestamp(), ident))
            raise
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute("UPDATE assistant_material_requests SET state='complete',result_json=?,updated_at=? WHERE id=?",
                       (json.dumps(clean(result)), timestamp(), ident))
        return result

    def import_material(self, data, preview):
        kind, fields = preview['kind'], preview['fields']
        if preview['artifact_id']:
            meta, content = self.service.artifacts.read(preview['artifact_id'])
            filename = meta['filename']
        else:
            content = data['content'].encode('utf-8')
            filename = 'main.tex' if kind in ('assignment_version', 'course_notes') else 'material.txt'
        if kind == 'rubric':
            try:
                text = content.decode('utf-8')
            except UnicodeDecodeError:
                raise Refused('GRADING: the rubric material is not UTF-8 text.') from None
            body = {'version': preview['basis'], 'source_text': text, 'format': fields.get('format', 'text'), 'mode': fields.get('mode', 'positive')}
            code, result, _ = self.service.transport.request('POST', '/api/grading/assignments/' + fields['plan_id'] + '/questions/' + fields['question_id'] + '/rubrics', body)
        elif kind == 'assignment_version':
            code, result = self.service.transport.material_upload('/api/documents/versions/compile',
                {'assignment_id': fields['assignment_id'], 'label': preview['title']}, {'files': [('main.tex', content)]})
        elif kind == 'quiz_draft':
            form = {k: str(v) for k, v in fields.items() if k in ('term_id', 'mode', 'versions', 'total', 'format', 'instructions')}
            form.update(request_id=data['request_id'], title=preview['title'])
            if fields.get('blueprint'):
                form['blueprint'] = json.dumps(fields['blueprint'])
            code, result = self.service.transport.material_upload('/api/quiz-drafts', form, {'files': [(filename, content)]})
        else:
            if not preview['artifact_id']:
                from document_identity.compiler import compile_sources
                try:
                    content, _, _ = compile_sources({'main.tex': content})
                except Exception as error:  # nothing reached the destination
                    raise Refused('COURSE-NOTES: the notes did not compile: ' + str(error)[:500]) from None
            code, result = self.service.transport.material_upload('/api/course-notes/definitions/' + preview['definition_id'],
                {'request_id': data['request_id']}, {'file': [('notes.pdf', content)]})
        if code not in (200, 201, 202):
            error = result.get('error', code) if isinstance(result, dict) else code
            # A 4xx is the destination's own refusal (nothing changed); any other status leaves the outcome uncertain.
            raise (Refused if 400 <= code < 500 else ValueError)('ASSISTANT: the material destination refused the import: ' + str(error))
        return {'material_request_id': data['request_id'], 'kind': kind, 'result': result}
