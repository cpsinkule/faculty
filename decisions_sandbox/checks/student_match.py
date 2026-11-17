"""Whose paper a first page is, through the app's own code (Chris, 2026-11-12: "replace jev with gemini decisions for scan to
students"): `document_identity.jev_papers.identify` replayed on first pages the app already read, with the model under test
answering its three requests (the name against each section's roster, the name among the candidates, the UIN among the
candidates) in place of Jev. Nothing is asked of Jev: the page check (where the name and UIN are) and Jev's own pick are read
from the kept reading.

The page comes whole through the live app's read route and is cut as the app cuts it. The roster is the one the app offers
for an unlabeled scan (names) with UINs from the copy. ROSTER NAMES AND UINS ARE SENT as options. Labels: an unlabeled first
page's student is the one it was registered for; a stamped copy's is the roster student whose UIN the local OCR read exactly,
when it read one (a copy photocopied for the class is one copy with many students, so the copy's own student is no label).

`student_absent` asks the same of a registered first page with its own student taken off the roster: the right outcome is that
nobody is named."""
import hashlib
import json

from document_identity import jev_papers

from ..cases import Case, label
from . import identity
from .images import _digits, _hot, fetch

MODES = ('google', 'googleultra', 'gemini')
QUOTA = {'unlabeled_jev_named': 40, 'unlabeled_ocr_named': 20, 'unlabeled_jev_failed': 5, 'copy_jev_unsettled': 10, 'copy_one_round': 30,
         'copy_two_rounds': 20}
ABSENT = 30  # registered first pages asked again without their student on the roster


class Kept:
    """`JevService.ask` as `identify` calls it, answered by the model under test: the answers come back as a receipt keeps them
    (enrollment IDs, never the option's words), and each request's lower confidence of its two orders is remembered."""
    def __init__(self, service):
        self.service, self.confidence = service, {}

    def ask(self, purpose, owner, state, questions, rule, images=(), receipt=None):
        view = self.service.ask(purpose, owner, state, questions, rule, images=tuple(images))
        if view.get('status') == 'complete':
            for name in questions:
                if name + '__reversed' in questions:
                    first, second = view['answers'][name], view['answers'][name + '__reversed']
                    self.confidence[name] = (min(first['confidence'], second['confidence']), first['choice'] == second['choice'])
            if receipt:
                view = receipt['result'](view)
        return view


def _run(owner, raster, sha, check, students):
    memo = {}

    def run(service):
        if 'page' not in memo:
            memo['page'] = fetch(f'/api/inbox/artifacts/{raster}/download', sha, whole=True)
        kept = Kept(service)
        found = jev_papers.identify(kept, owner, memo['page'], check, students)
        name, uin = kept.confidence.get('name', (None, None)), kept.confidence.get('uin', (None, None))
        return {'outcome': {'status': found.get('status'), 'student': found.get('enrollment_id') or 'none', 'name_round': found.get('name_round') or '',
                            'uin_round': found.get('uin_round') or '', 'candidates': len(found.get('candidates') or []),
                            'name_orders_agree': name[1], 'uin_orders_agree': uin[1]},
                'p': {'name_confidence': name[0], 'uin_confidence': uin[0]}}
    return run


def real(ctx):
    db, cases = ctx.db, []
    uins = {r[0]: r[1] for r in db.execute('SELECT id,uin FROM student_enrollments')}
    by_uin = {}
    scans = [r[0] for r in db.execute('SELECT DISTINCT sp.message_id FROM scan_unlabeled_runs w JOIN scan_pages sp ON sp.message_id=w.message_id ORDER BY w.rowid DESC')]
    scans = [message for message in scans if _hot(db, message)]
    if not scans:
        return cases
    students = {s['enrollment_id']: {'name': s['name'], 'uin': uins.get(s['enrollment_id'], ''), 'section_id': str(s['section_number'])}
                for s in identity.roster(f'/api/documents/messages/{scans[0]}/unlabeled')}
    for key, student in students.items():
        by_uin.setdefault(student['uin'], []).append(key)
    taken = {}

    def add(stratum, ident, message, raster, sha, reading, truth, strength):
        check, mine = reading.get('jev_page') or {}, reading.get('jev_student') or {}
        if (taken.get(stratum, 0) >= QUOTA[stratum] or check.get('status') != 'complete' or not check.get('name_box') or not raster
                or not _hot(db, message)):
            return
        taken[stratum] = taken.get(stratum, 0) + 1
        if truth and stratum.startswith('unlabeled') and taken.get('absent', 0) < ABSENT:
            taken['absent'] = taken.get('absent', 0) + 1
            others = {key: student for key, student in students.items() if key != truth}
            cases.append(Case('student_absent', f'real-absent-{ident[:10]}', 'real', _run('sandbox:absent:' + ident, raster, sha, check, others),
                              {'student': label('none', 'medium')}, {'stratum': 'absent', 'scan_page': ident, 'roster': len(others)},
                              modes=MODES, serial=True))
        cases.append(Case('student_match', f'real-{stratum}-{ident[:10]}', 'real', _run('sandbox:' + ident, raster, sha, check, students),
                          {'student': label(truth, strength)} if truth else {},
                          {'stratum': stratum, 'scan_page': ident, 'jev_pick': mine.get('enrollment_id') or '', 'jev_status': mine.get('status') or '',
                           'roster': len(students)}, modes=MODES, serial=True))

    # A fixed, arbitrary order (a hash of the page's id), so a rerun picks the same pages.
    def shuffled(rows):
        return sorted(rows, key=lambda row: hashlib.sha256(row['scan_page_id'].encode()).hexdigest())

    pages = db.execute('''SELECT r.scan_page_id,r.reading_json,sp.message_id,sp.raster_artifact_id,a.sha256,p.enrollment_id,p.basis,p.actor
        FROM scan_unlabeled_reads r JOIN scan_pages sp ON sp.id=r.scan_page_id LEFT JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id
        JOIN scan_unlabeled_plans p ON p.run_id=r.run_id AND p.scan_page_id=r.scan_page_id
        WHERE r.run_id=(SELECT id FROM scan_unlabeled_runs w WHERE w.message_id=sp.message_id ORDER BY attempt DESC LIMIT 1)
          AND json_extract(r.reading_json,'$.jev_student') IS NOT NULL''').fetchall()
    for row in shuffled(pages):
        reading = json.loads(row['reading_json'])
        if row['enrollment_id'] not in students:
            continue
        stratum = ('unlabeled_jev_failed' if reading['jev_student'].get('status') != 'complete' else
                   'unlabeled_jev_named' if row['basis'] == 'jev' else 'unlabeled_ocr_named')
        add(stratum, row['scan_page_id'], row['message_id'], row['raster_artifact_id'], row['sha256'], reading, row['enrollment_id'],
            'strong' if row['actor'] == 'workspace' else 'medium')

    copies = db.execute('''SELECT r.scan_page_id,r.reading_json,sp.message_id,sp.raster_artifact_id,a.sha256 FROM copy_name_reads r
        JOIN scan_pages sp ON sp.id=r.scan_page_id LEFT JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id
        WHERE json_extract(r.reading_json,'$.jev_student') IS NOT NULL''').fetchall()
    for row in shuffled(copies):
        reading = json.loads(row['reading_json'])
        mine = reading['jev_student']
        read = by_uin.get(_digits((reading.get('fields') or {}).get('uin')), [])
        truth = read[0] if len(read) == 1 else None
        if mine.get('enrollment_id') and mine['enrollment_id'] not in students:
            continue
        stratum = ('copy_jev_unsettled' if not mine.get('enrollment_id') else
                   'copy_two_rounds' if mine.get('name_round') == mine.get('uin_round') == mine['enrollment_id'] else 'copy_one_round')
        add(stratum, row['scan_page_id'], row['message_id'], row['raster_artifact_id'], row['sha256'], reading, truth, 'medium')
    return cases
