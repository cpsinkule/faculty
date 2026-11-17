"""Per-version template preparation and a term-owned, explicit handoff to OCR.

Canonical PDFs and converted references remain course-wide; this plan only pins
its choices. Reading and crop confirmations reuse the existing bounded worker.
"""
import json

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect
from .models import digest, encoded, uid


TEMPLATE_WARNING = ('Digital templates may cause reduced performance. Uploaded blank scans work best, '
                    'using the same scanner used to scan the students’ work. '
                    'You can continue with these templates or go back and upload blank scans.')


class TemplateWorkflowMixin:
    @staticmethod
    def template_selections(db, aid):
        row = db.execute('SELECT selections_json FROM frq_template_reviews WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1', (aid,)).fetchone()
        return json.loads(row[0]) if row else []

    def template_authorized(self, db, aid, question, template):
        return bool(template and any(s['document_id'] == question['assignment_version_id']
            and s['exam_version'] == question['exam_version'] and s['template_id'] == template['id']
            for s in self.template_selections(db, aid)))

    def template_setup(self, db, aid):
        plan = self.plan(db, aid)
        context, missing = self.rubric_quality_context(db, aid)
        questions = [self.question(db, aid, r[0]) for r in db.execute(
            'SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid', (aid,))]
        # Templates share the first step with the rubric uploads; the rubric finalization follows the answer areas. They may be
        # chosen before any rubric exists (a model can draft the rubrics from them); confirming them still needs every version's rubrics.
        blockers = (['Upload or draft rubrics for every version first.'] if not questions or missing else [])
        templates = [dict(r) for r in db.execute('SELECT id,label,page_count,manifest_sha256,source_version_id FROM assignment_templates WHERE assignment_id=? ORDER BY created_at,id', (plan['source_assignment_id'],))]
        selections = self.template_selections(db, aid)
        slots = []
        for document in context['documents']:
            source = db.execute('SELECT page_count,compiler_json FROM assignment_versions WHERE id=?', (document['id'],)).fetchone()
            generated = json.loads(source['compiler_json']).get('generator') != 'faculty-stamps-only'
            groups = [q for q in questions if q['assignment_version_id'] == document['id']]
            # Declared versions (an assignment made from a scan) and versions the instructor added (a makeup Version C) each expect their
            # own rubrics: a version still without any has its slot.
            single = len(context['documents']) == 1
            declared = self.declared_versions(db, plan) + self.added_versions(db, plan) if single else []
            written = {q['exam_version'] for q in groups}
            letters = sorted(written | set(declared)) if written else sorted(set(context['versions'] if single else []) | set(declared)) or ['']
            for letter in letters:
                members = [q for q in groups if q['exam_version'] == letter]
                options = [t for t in templates if not generated or t['page_count'] == source['page_count']]
                pinned = {q['content'].get('template_id') for q in members if q['content'].get('template_id')}
                previous = next((s['template_id'] for s in selections if s['document_id'] == document['id'] and s['exam_version'] == letter), None)
                automatic = next((t['id'] for t in options if t['source_version_id'] == document['id']), None)
                # The blank exam its rubrics were drafted from is the natural default: a suggestion, confirmed like any other.
                drafted = db.execute("SELECT template_id FROM frq_rubric_generations WHERE assignment_id=? AND document_id=? AND exam_version=? AND status='complete' "
                                     "AND COALESCE(json_extract(input_json,'$.kind'),'')!='key' ORDER BY rowid DESC LIMIT 1",  # Not an answer key reading.
                                     (aid, document['id'], letter)).fetchone()
                if not drafted:  # A draft of every version at once names each version's template in its input.
                    together = db.execute("SELECT input_json FROM frq_rubric_generations WHERE assignment_id=? AND exam_version='*' AND status='complete' ORDER BY rowid DESC LIMIT 1",
                                          (aid,)).fetchone()
                    drafted = next(((v['template_id'],) for v in (json.loads(together[0])['versions'] if together else [])
                                    if (v['document_id'], v['exam_version']) == (document['id'], letter)), None)
                chosen = next(iter(pinned)) if len(pinned) == 1 else previous if not pinned else None
                chosen = chosen or (drafted[0] if drafted else None) or automatic
                if chosen not in {t['id'] for t in options}:
                    chosen = None
                slots.append({'key': document['id'] + ':' + letter, 'document_id': document['id'], 'exam_version': letter,
                    'label': f'Version {letter}' if letter else document['label'], 'document_label': document['label'],
                    'can_import': generated, 'template_id': chosen, 'options': options,
                    'questions': len(members), 'has_regions': any(q['content']['regions'] for q in members),
                    'region_templates': list(dict.fromkeys(q['content'].get('template_id') for q in members if q['content']['regions'])),
                    'confirmed': bool(members) and all(q['content'].get('template_id') == chosen and
                        (self.template_authorized(db, aid, q, {'id': chosen}) or db.execute(
                            'SELECT 1 FROM frq_location_reviews WHERE assignment_id=? AND question_id=? AND question_revision_id=? AND rubric_id=? AND template_id=?',
                            (aid, q['question_id'], q['question_revision_id'], q['rubric_id'], chosen)).fetchone()) for q in members)})
        slots.sort(key=lambda s: (s['exam_version'], s['label'].casefold(), s['document_id']))
        fingerprint = digest({'questions': [(q['question_id'], q['version']) for q in questions],
            'slots': slots, 'selections': selections})
        return {'slots': slots, 'blockers': blockers, 'ready': bool(slots) and not blockers, 'can_choose': bool(slots),
                'generations': self.rubric_generations(db, aid),
                'confirmed': bool(slots) and all(s['confirmed'] for s in slots), 'input_sha256': fingerprint,
                'warning': TEMPLATE_WARNING}

    def assignment_templates(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            return self.template_setup(db, aid)

    def prepare_assignment_template(self, aid, data):
        with self.rosters.lock(), connect(self.path) as db:
            status = self.template_setup(db, aid)
            if not any(s['document_id'] == data.get('document_id') and s['can_import'] for s in status['slots']):
                raise ValueError('This version needs an uploaded assignment template.')
        return self.templates.import_version(data['document_id'])

    def confirm_assignment_templates(self, aid, data):
        if data.get('acknowledge_warning') is not True:
            raise ValueError('Confirm the template quality warning before continuing to answer areas.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            status = self.template_setup(db, aid)
            if data.get('input_sha256') != status['input_sha256']:
                raise Conflict('Templates or rubrics changed. Refresh and review the current templates before continuing.')
            if not status['ready']:
                raise Conflict(' '.join(status['blockers']))
            choices = data.get('selections')
            if not isinstance(choices, list) or len(choices) != len(status['slots']) or any(not isinstance(c, dict) for c in choices):
                raise ValueError('Choose one assignment template for every version.')
            keyed = {c.get('key'): c.get('template_id') for c in choices if isinstance(c.get('key'), str)}
            if set(keyed) != {s['key'] for s in status['slots']}:
                raise ValueError('Choose each version exactly once.')
            selections = []
            for slot in status['slots']:
                template = next((t for t in slot['options'] if t['id'] == keyed[slot['key']]), None)
                if not template:
                    raise ValueError(f"Upload or choose a compatible template for {slot['label']}.")
                selections.append({'document_id': slot['document_id'], 'exam_version': slot['exam_version'],
                                   'template_id': template['id'], 'manifest_sha256': template['manifest_sha256']})
                rows = db.execute('''SELECT p.question_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                    WHERE p.assignment_id=? AND q.assignment_version_id=? AND q.exam_version=?''', (aid, slot['document_id'], slot['exam_version'])).fetchall()
                for row in rows:
                    question = self.question(db, aid, row[0])
                    # Choosing a new reference is an explicit reset, never a silent
                    # change to previously reviewed areas or masks.
                    if question['content'].get('template_id') != template['id']:
                        replacing = bool(question['content'].get('template_id'))
                        if replacing and question['content']['regions'] and data.get('reset_regions') is not True:
                            raise Conflict('Changing this template resets existing answer areas. Confirm that reset before continuing.')
                        self._set_location_template(db, aid, question, template['id'], keep_regions=not replacing)
            receipt = uid()
            db.execute('INSERT INTO frq_template_reviews VALUES(?,?,?,?,?,?,?)',
                (receipt, aid, status['input_sha256'], encoded(selections), TEMPLATE_WARNING, 'workspace', timestamp()))
            self.touch(db, aid)
            self.event(db, aid, 'assignment_templates_confirmed', {'review_id': receipt, 'selections': selections})
        self.inbox.wake.set()
        return self.assignment_templates(aid)
