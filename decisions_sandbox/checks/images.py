"""Use case 6, images (Decisions and, when `--modes` names them, the other picture readers in `cases.PICTURES`: Snap, Jev 1.14,
Jev 1.13 with its opt-in images, Liquid d1-ultra; the app's own Jev and Microsoft-Decision-1 read text only). Pictures come ONLY
through the live app's own read routes on 127.0.0.1:3000
(the storage manager's locks and sha256 checks apply there), one request at a time, never during the nightly backup window, never
for an archived, pending or deleted message, never with `reveal=1` and never a POST. Roster names are never sent: the questions ask
only whether the handwriting is there and legible.

(a) the name/UIN band of exam covers and printed copies, in strata of what the local OCR managed (exact UIN, a name match with a
    wrong UIN, nothing it could settle);
(b) grading answer-area crops: does the work run past a crop's edge, is a crop blank."""
from datetime import datetime, time as clock
import hashlib
import io
import json
import threading
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from jev.service import noul
from scheduling.clock import CAMPUS_ZONE
from teach_inbox.service import PRESENT

from ..cases import PICTURES, Case, label, noul_p, status

LIVE = 'http://127.0.0.1:3000'
BACKUP_WINDOW = (clock(3, 45), clock(4, 30))
MAX_SIDE = 1600
_one = threading.Lock()


class ImageUnavailable(Exception):
    pass


def _window_closed():
    now = datetime.now(CAMPUS_ZONE).time()
    return BACKUP_WINDOW[0] <= now <= BACKUP_WINDOW[1]


def fetch(path, sha256=None, whole=False):
    """One GET to the live app (no credentials, no proxy), PNG checked; grayscale, long side at most MAX_SIDE. `whole`: the PNG
    as the app holds it, for a check that cuts its own crops from the page as the app does."""
    from PIL import Image
    if _window_closed():
        raise ImageUnavailable('The nightly backup window (3:45–4:30 campus time): no reads of the live app.')
    with _one:
        try:
            with build_opener(ProxyHandler({})).open(Request(LIVE + path, method='GET'), timeout=30) as response:
                body = response.read(30 * 1024 * 1024)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise ImageUnavailable(f'{type(error).__name__} from the live app') from None
    if sha256 and hashlib.sha256(body).hexdigest() != sha256:
        raise ImageUnavailable('The artifact did not match its recorded sha256.')
    if not body.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ImageUnavailable('The live app did not return a PNG.')
    if whole:
        return body
    picture = Image.open(io.BytesIO(body)).convert('L')
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    picture.save(out, format='PNG', optimize=True)
    return out.getvalue()


def _hot(db, message_id):
    """A message the routes may read without side effects: present, not deleted, its storage active or staged."""
    return db.execute('SELECT 1 FROM inbox_messages m WHERE m.id=? AND m.deleted_at IS NULL AND m.storage_status IN (\'active\',\'staged\') AND '
                      + PRESENT.format('m.id'), (message_id,)).fetchone() is not None


# ---------------------------------------------------------------------------------------------------------------- (a)
BAND_RULE = 'sandbox-identity-band-v1'
BAND_STATE = ('The image is the top of a student\'s exam paper, where the student handwrites their name and nine-digit university ID '
              '(UIN). Judge only what is handwritten in it.')
BAND = {
    'band_blank': noul('Is the name and UIN area in the image blank, with nothing handwritten in it?'),
    'name_legible': noul('Is a handwritten student name written in the image, legible enough to read every letter of it?'),
    'uin_legible': noul('Is a handwritten nine-digit UIN written in the image, with every one of its nine digits legible?'),
}


def _band_run(owner, path):
    memo = {}

    def run(service):
        if 'png' not in memo:
            memo['png'] = fetch(path)
        receipt = service.ask('sandbox-identity-band', owner, BAND_STATE, BAND, BAND_RULE, images=(memo['png'],))
        p = {name: noul_p(receipt, name) for name in BAND}
        return {'outcome': {'status': status(receipt), **{name: (None if v is None else v >= .5) for name, v in p.items()}}, 'p': p}
    return run


def _digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


# ---------------------------------------------------------------------------------------------------------------- (b)
CROP_RULE = 'sandbox-cut-off-v1'
CROP_STATE = ('The images are the answer-area crops of one student\'s handwritten response to one exam question, in order (image 1 '
              'first). Printed question text may appear; judge the handwritten work.')


def _crop_questions(count):
    asked = {'answer_continues_past_crop': noul('Does the handwritten work run past an edge of any of the images, so that part of the '
                                                'answer is cut off?')}
    asked.update({f'crop_{i}_blank': noul(f'Is image {i + 1} essentially blank: no handwritten answer work in it (printed text alone '
                                          'does not count)?') for i in range(count)})
    return asked


def _crop_run(owner, crops):
    memo = {}

    def run(service):
        if 'pngs' not in memo:
            memo['pngs'] = [fetch(f'/api/inbox/artifacts/{artifact}/download', sha) for artifact, sha in crops]
        questions = _crop_questions(len(memo['pngs']))
        receipt = service.ask('sandbox-cut-off', owner, CROP_STATE, questions, CROP_RULE, images=tuple(memo['pngs']))
        p = {name: noul_p(receipt, name) for name in questions}
        return {'outcome': {'status': status(receipt), 'cut_off': None if p['answer_continues_past_crop'] is None
                            else p['answer_continues_past_crop'] >= .5}, 'p': p}
    return run


def _crops_of(db, task_id, seen=None):
    """[(artifact id, sha256)] of the task's latest extracting step (a reused result: the task that made it)."""
    seen = seen or set()
    if task_id in seen:
        return []
    seen.add(task_id)
    row = db.execute("SELECT output_json FROM frq_steps WHERE task_id=? AND stage='extracting' ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
    if row:
        crops = json.loads(row[0]).get('crops') or []
        found = [((c.get('images') or {}).get('original') or {}) for c in crops]
        return [(f['artifact_id'], f.get('sha256')) for f in found if f.get('artifact_id')]
    reuse = db.execute('SELECT reuse_result_id FROM frq_tasks WHERE id=?', (task_id,)).fetchone()
    if reuse and reuse[0]:
        made = db.execute('SELECT task_id FROM frq_results WHERE id=?', (reuse[0],)).fetchone()
        if made:
            return _crops_of(db, made[0], seen)
    return []


def _readable(db, crops):
    for artifact, _ in crops:
        row = db.execute('SELECT message_id FROM inbox_artifacts WHERE id=?', (artifact,)).fetchone()
        if not row or not _hot(db, row[0]):
            return False
    return bool(crops)


# ================================================================================================================ cases
def real(ctx):
    db, cases = ctx.db, []
    covers = db.execute('''SELECT p.scan_page_id,sp.message_id,r.reading_json,p.basis,p.actor,e.uin true_uin FROM scan_unlabeled_plans p
        JOIN scan_unlabeled_reads r ON r.run_id=p.run_id AND r.scan_page_id=p.scan_page_id AND r.cover=1
        JOIN scan_pages sp ON sp.id=p.scan_page_id JOIN student_enrollments e ON e.id=p.enrollment_id
        WHERE p.run_id=(SELECT id FROM scan_unlabeled_runs w WHERE w.message_id=sp.message_id ORDER BY attempt DESC LIMIT 1)
        ORDER BY p.created_at''').fetchall()
    quota = {'cover_review': 8, 'cover_registered': 2, 'cover_name_wrong_uin': 6, 'cover_exact_uin': 6,
             'copy_named_by_hand': 6, 'copy_wrong_uin': 6, 'copy_exact_uin': 6}
    taken = {k: 0 for k in quota}

    def add(stratum, ident, path, exact, mid):
        if taken[stratum] >= quota[stratum] or not _hot(db, mid):
            return
        taken[stratum] += 1
        wanted = {'uin_legible': label(1, 'medium'), 'band_blank': label(0, 'medium')} if exact else {}
        cases.append(Case('identity_band', f'real-{stratum}-{ident[:10]}', 'real', _band_run(f'sandbox:{ident}', path), wanted,
                          {'stratum': stratum, 'scan_page': ident, 'ocr_uin_exact': exact}, modes=PICTURES, serial=True))

    for row in covers:
        fields = (json.loads(row['reading_json']).get('fields') or {})
        exact = _digits(fields.get('uin')) == _digits(row['true_uin'])
        stratum = ('cover_review' if row['basis'] == 'review' else 'cover_registered' if row['basis'] == 'confidence' and row['actor'] == 'workspace'
                   else 'cover_name_wrong_uin' if row['basis'] == 'confidence' and not exact else 'cover_exact_uin' if row['basis'] == 'uin' and exact
                   else None)
        if stratum:
            add(stratum, row['scan_page_id'], f"/api/documents/messages/{row['message_id']}/unlabeled/{row['scan_page_id']}/cover.png",
                exact, row['message_id'])
    copies = db.execute('''SELECT r.scan_page_id,sp.message_id,r.reading_json,e.uin true_uin,
            (SELECT actor FROM document_events d WHERE d.kind='copy_assignment' AND d.copy_id=c.id ORDER BY d.id DESC LIMIT 1) named_by
        FROM copy_name_reads r JOIN printed_copies c ON c.id=r.printed_copy_id JOIN scan_pages sp ON sp.id=r.scan_page_id
        JOIN student_enrollments e ON e.id=c.enrollment_id ORDER BY r.created_at''').fetchall()
    for row in copies:
        fields = (json.loads(row['reading_json']).get('fields') or {})
        exact = _digits(fields.get('uin')) == _digits(row['true_uin'])
        stratum = 'copy_named_by_hand' if row['named_by'] == 'workspace' else 'copy_exact_uin' if exact else 'copy_wrong_uin'
        add(stratum, row['scan_page_id'], f"/api/documents/messages/{row['message_id']}/copy-names/{row['scan_page_id']}/name.png",
            exact, row['message_id'])

    # (b) cut-off answers and blank crops.
    picked = []
    for (result,) in db.execute("SELECT result_json FROM frq_flag_resolutions WHERE result_json LIKE '%page_round%'"):
        for question in json.loads(result).get('questions', []):
            for response in question.get('responses', []):
                if (response.get('ruling') or {}).get('page_round') and response.get('task_id'):
                    picked.append(('page_round', response['task_id'], {'answer_continues_past_crop': label(1, 'medium')}))
    for (task_id,) in db.execute("SELECT task_id FROM frq_results WHERE reasons_json LIKE '%response boundaries%' ORDER BY created_at"):
        picked.append(('misaligned', task_id, {'answer_continues_past_crop': label(1, 'weak')}))
    for task_id, output in db.execute("SELECT task_id,output_json FROM frq_steps WHERE stage='recognizing' AND output_json LIKE '%blank_crops%' ORDER BY rowid"):
        blank = {i for c in json.loads(output).get('candidates', []) for i in (c.get('blank_crops') or [])}
        if blank:
            picked.append(('blank_crop', task_id, {f'crop_{i}_blank': label(1, 'weak') for i in sorted(blank)}))
    for (task_id,) in db.execute('''SELECT res.task_id FROM frq_reviews v JOIN frq_results res ON res.id=v.result_id
            WHERE v.status='approved' AND res.reasons_json IN ('[]','{}','') ORDER BY v.created_at'''):
        picked.append(('control', task_id, {'answer_continues_past_crop': label(0, 'weak')}))
    per, seen = {}, set()
    for stratum, task_id, wanted in picked:
        if per.get(stratum, 0) >= 10 or task_id in seen:
            continue
        crops = _crops_of(db, task_id)
        if not _readable(db, crops):
            continue
        if stratum == 'control':
            wanted = {**wanted, **{f'crop_{i}_blank': label(0, 'weak') for i in range(len(crops))}}
        seen.add(task_id)
        per[stratum] = per.get(stratum, 0) + 1
        cases.append(Case('cut_off', f'real-{stratum}-{task_id[:10]}', 'real', _crop_run(f'sandbox:{task_id}', crops), wanted,
                          {'stratum': stratum, 'task': task_id, 'crops': len(crops)}, modes=PICTURES, serial=True))
    return cases
