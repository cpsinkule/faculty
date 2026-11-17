"""A whole scanned paper as one PDF (Chris, 2026-11-11, of Gemini Decision 1: "it has more modalities than Jev"; asked which real
material may go, he chose whole scanned papers too). Two things Faculty settles from a booklet's first page today, each asked
of the first page as a picture and of the whole booklet as a PDF of its pages in order:

- `paper_version_*`: which exam version the booklet is, with the live question (`grading.jev_versions`, JEV PAGE ONE). The
  reference is the version Faculty settled for the paper (its latest complete version check).
- `paper_student_*`: whose booklet it is, one Choice per section over "Name — UIN" plus "none of these", in both orders, as
  `identity` asks of a cropped band; here nothing is cropped. The reference is the student the paper is assembled for.

Pages come only through the live app's own read route (`images.fetch`), one at a time; the PDF is built here from those pages
and holds no text. ROSTER NAMES AND UINS ARE SENT as the options of `paper_student_*`."""
import hashlib
import io
import json

from grading.jev_versions import NONE as NO_VERSION, WHICH_VERSION
from jev.service import agreed, both

from ..cases import MEDIA, Case, label
from . import identity
from .images import _hot, fetch

RULE = 'sandbox-paper-v1'
# Papers of each version, per exam. Chris, 2026-11-11, of a first run set for 51 papers: "dont do that many, the will be costly
# and take too long, use a smaller sample"; it was stopped at 8 papers.
PER_VERSION = {'A': 2, 'B': 2, 'C': 2}
PAGE_MODES = ('jev114', 'gemini')
PAGE_STATE = "A scanned page of a student's exam."
PDF_STATE = "The attached file is the scan of one student's printed exam booklet: every page, in order."
PDF_VERSION = ('The file is a student\'s printed exam booklet. Which version of the exam is it? Read the version printed on the exam '
               'itself, such as in the title or footer of its first page; a sticker label stuck on a page does not count.')


def booklet(pages):
    """One PDF of the pages (PNG bytes) in order: pictures only, as a scanner saves them."""
    from PIL import Image
    opened = [Image.open(io.BytesIO(page)).convert('L') for page in pages]
    out = io.BytesIO()
    opened[0].save(out, format='PDF', save_all=True, append_images=opened[1:], resolution=150)
    return out.getvalue()


def _material(paper):
    """The paper's first page and its PDF, fetched once for the four cases of one paper."""
    if 'pages' not in paper:
        paper['pages'] = [fetch(f'/api/inbox/artifacts/{artifact}/download', sha) for artifact, sha in paper['rasters']]
        paper['pdf'] = booklet(paper['pages'])
    return paper['pages'][0], paper['pdf']


def _version_run(owner, paper, letters, whole):
    questions = both('version', PDF_VERSION if whole else WHICH_VERSION, dict.fromkeys([f'Version {letter}' for letter in letters] + [NO_VERSION]))

    def run(service):
        first, pdf = _material(paper)
        receipt = service.ask('sandbox-paper-version', owner, PDF_STATE if whole else PAGE_STATE, questions, RULE, images=(pdf if whole else first,))
        if receipt.get('status') != 'complete':
            return {'outcome': {'status': receipt.get('status')}, 'p': {}}
        pick, sure = agreed(receipt['answers'], 'version')
        letter = pick[len('Version '):] if pick and pick != NO_VERSION else ''
        return {'outcome': {'status': 'complete', 'version': letter, 'orders_agree': pick is not None},
                'p': {'confidence': sure}}
    return run


def _student_run(owner, paper, students, uins, whole):
    questions, options, sections = identity._questions(students, uins, 'cover')
    if whole:
        questions = {name: {**asked, 'instructions': asked['instructions'].replace('in the image', 'on the first page of the booklet')}
                     for name, asked in questions.items()}

    def run(service):
        first, pdf = _material(paper)
        receipt = service.ask('sandbox-paper-student', owner, PDF_STATE if whole else PAGE_STATE, questions, RULE, images=(pdf if whole else first,))
        if receipt.get('status') != 'complete':
            return {'outcome': {'status': receipt.get('status')}, 'p': {}}
        claims = []
        for number in sections:
            pick, sure = agreed(receipt['answers'], f's{number}')
            enrollment = options.get((number, pick)) if pick and pick != identity.NONE else None
            if enrollment:
                claims.append((sure, number, enrollment))
        claims.sort(reverse=True)
        return {'outcome': {'status': 'complete', 'student': claims[0][2] if claims else identity.NONE, 'sections_claiming': len(claims)},
                'p': {'confidence': claims[0][0] if claims else None}}
    return run


def real(ctx):
    db, cases = ctx.db, []
    uins = {r[0]: r[1] for r in db.execute('SELECT id,uin FROM student_enrollments')}
    scans = [r[0] for r in db.execute('SELECT DISTINCT sp.message_id FROM scan_unlabeled_runs w JOIN scan_pages sp ON sp.message_id=w.message_id ORDER BY w.rowid DESC')]
    scans = [message for message in scans if _hot(db, message)]
    if not scans:
        return cases
    students = identity.roster(f'/api/documents/messages/{scans[0]}/unlabeled')
    known = {s['enrollment_id'] for s in students}
    settled = db.execute('''SELECT c.id,c.assignment_id,c.enrollment_id,c.assembly_revision_id,json_extract(c.result_json,'$.decision.matched') letter
        FROM frq_version_checks c WHERE c.status='complete' AND c.rowid=(SELECT MAX(n.rowid) FROM frq_version_checks n
            WHERE n.assignment_id=c.assignment_id AND n.section_id=c.section_id AND n.enrollment_id=c.enrollment_id AND n.status='complete')''').fetchall()
    letters = {}
    for row in settled:
        letters.setdefault(row['assignment_id'], set()).add(row['letter'])
    taken = {}
    # A fixed, arbitrary order (a hash of the check's id), so a rerun picks the same papers.
    for row in sorted(settled, key=lambda row: hashlib.sha256(row['id'].encode()).hexdigest()):
        key = (row['assignment_id'], row['letter'])
        if taken.get(key, 0) >= PER_VERSION.get(row['letter'], 0) or row['enrollment_id'] not in known:
            continue
        revision = db.execute('SELECT selection_json FROM assembly_revisions WHERE id=?', (row['assembly_revision_id'],)).fetchone()
        rasters = []
        for page in sorted(json.loads(revision[0]) if revision else [], key=lambda page: page['page_number']):
            found = db.execute('''SELECT sp.message_id,sp.raster_artifact_id,a.sha256 FROM scan_pages sp LEFT JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id
                WHERE sp.id=?''', (page['scan_page_id'],)).fetchone()
            if not found or not found['raster_artifact_id'] or not _hot(db, found['message_id']):
                rasters = []
                break
            rasters.append((found['raster_artifact_id'], found['sha256']))
        if not rasters:
            continue
        taken[key] = taken.get(key, 0) + 1
        paper = {'rasters': rasters}
        asked = sorted(letters[row['assignment_id']])
        meta = {'stratum': f"exam {row['assignment_id'][:4]} version {row['letter']}", 'check': row['id'], 'pages': len(rasters), 'roster': len(students)}
        owner = 'sandbox:' + row['id']
        for check, run, wanted, modes in (
                ('paper_version_page', _version_run(owner, paper, asked, False), {'version': label(row['letter'], 'medium')}, PAGE_MODES),
                ('paper_version_pdf', _version_run(owner, paper, asked, True), {'version': label(row['letter'], 'medium')}, MEDIA),
                ('paper_student_page', _student_run(owner, paper, students, uins, False), {'student': label(row['enrollment_id'], 'medium')}, PAGE_MODES),
                ('paper_student_pdf', _student_run(owner, paper, students, uins, True), {'student': label(row['enrollment_id'], 'medium')}, MEDIA)):
            cases.append(Case(check, 'real-' + row['id'][:12], 'real', run, wanted, meta, modes=modes, serial=True))
    return cases
