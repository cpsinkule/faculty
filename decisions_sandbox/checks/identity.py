"""Which student wrote this paper (requirement, 2026-10-07). Decisions and, when `--modes` names them, the other picture readers in `cases.PICTURES` (Snap, Jev 1.14, Jev 1.13 with its
opt-in images, Liquid d1-ultra; the app's own Jev and Microsoft-Decision-1 read text only). The roster is the scan's own, as the live
app serves it (names), with UINs from the copy; a Choice holds
at most 255 options (measured: 342 refused), so the request asks one Choice per section ("Name — UIN" of its students, plus "none of
these"), each in both option orders. The section whose answer names a student gives the term-wide pick; the answer for the
student's own section alone is the "section known" reading. NOTE: roster names and UINs are sent as the options, to OpenAI and to
any other model `--modes` names.

Images: an exam cover's name/UIN band, a printed copy's name band, a bubble sheet's name and ID boxes. Labels: the student the
paper was recorded for (your own choice: strong; Faculty's automatic match or upload: medium)."""
import json
import threading
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from jev.service import agreed, both

from ..cases import PICTURES, Case, label
from . import images
from .images import _digits, _hot

RULE = 'sandbox-identity-choice-v1'
NONE = 'none of these'
_rosters, _lock = {}, threading.Lock()


def _json(path):
    if images._window_closed():
        raise images.ImageUnavailable('The nightly backup window: no reads of the live app.')
    with images._one:
        try:
            with build_opener(ProxyHandler({})).open(Request(images.LIVE + path, method='GET'), timeout=30) as response:
                return json.loads(response.read(20 * 1024 * 1024))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            raise images.ImageUnavailable(f'{type(error).__name__} from the live app') from None


def roster(path):
    """[{enrollment_id, name, section_number}] the live app offers for this scan (cached per route)."""
    with _lock:
        if path not in _rosters:
            _rosters[path] = [{k: s[k] for k in ('enrollment_id', 'name', 'section_number')} for s in _json(path).get('students', [])]
        return _rosters[path]


def _questions(students, uins, kind):
    sections = sorted({s['section_number'] for s in students})
    found, options = {}, {}
    look = ('the handwritten name and nine-digit UIN (university ID) in the image' if kind != 'bubble' else
            'the handwritten name and ID number in the two images (the name box, then the ID box)')
    for number in sections:
        criteria = {}
        for s in sorted((s for s in students if s['section_number'] == number), key=lambda s: s['name'].casefold()):
            text = f"{s['name']} — UIN {uins.get(s['enrollment_id'], '?')}"
            while text in criteria:
                text += ' *'
            criteria[text] = f'Section {number}'
            options[(number, text)] = s['enrollment_id']
        criteria[NONE] = f'The student is not in section {number}.'
        found.update(both(f's{number}', f'Which student of section {number} wrote {look}? Handwriting may be messy, and the name or the '
                          f'number may be partly wrong or missing: use both. Choose "{NONE}" when the student is not in section {number}.',
                          criteria))
    return found, options, sections


def _run(owner, paths, students, uins, kind, truth_section):
    memo = {}
    questions, options, sections = _questions(students, uins, kind)
    state = {'cover': 'The image is the top of a student\'s exam paper, where they handwrote their name and UIN.',
             'copy': 'The image is the top of a student\'s printed exam copy, where they handwrote their name and UIN.',
             'bubble': 'The images are the name box and the ID box of a student\'s bubble answer sheet.'}[kind]

    def run(service):
        if 'pngs' not in memo:
            memo['pngs'] = tuple(images.fetch(p) for p in paths)
        receipt = service.ask('sandbox-identity-choice', owner, state, questions, RULE, images=memo['pngs'])
        if receipt.get('status') != 'complete':
            return {'outcome': {'status': receipt.get('status')}, 'p': {}}
        claims, own, own_conf = [], None, None
        for number in sections:
            pick, sure = agreed(receipt['answers'], f's{number}')
            enrollment = options.get((number, pick)) if pick and pick != NONE else None
            if enrollment:
                claims.append((sure, number, enrollment))
            if number == truth_section:
                own, own_conf = (enrollment or (NONE if pick == NONE else 'disagree')), sure
        claims.sort(reverse=True)
        pick = claims[0][2] if claims else NONE
        return {'outcome': {'status': 'complete', 'student': pick, 'sections_claiming': len(claims), 'own_section_student': own},
                'p': {'confidence': claims[0][0] if claims else None, 'own_section_confidence': own_conf}}
    return run


def real(ctx):
    db, cases = ctx.db, []
    uins = {r[0]: r[1] for r in db.execute('SELECT id,uin FROM student_enrollments')}

    def section_of(students, enrollment):
        return next((s['section_number'] for s in students if s['enrollment_id'] == enrollment), None)

    # Exam covers (unlabeled scans): the roster the cover panel offers for that scan.
    covers = db.execute('''SELECT p.scan_page_id,sp.message_id,p.basis,p.actor,p.enrollment_id,r.reading_json FROM scan_unlabeled_plans p
        JOIN scan_unlabeled_reads r ON r.run_id=p.run_id AND r.scan_page_id=p.scan_page_id AND r.cover=1 JOIN scan_pages sp ON sp.id=p.scan_page_id
        WHERE p.run_id=(SELECT id FROM scan_unlabeled_runs w WHERE w.message_id=sp.message_id ORDER BY attempt DESC LIMIT 1) ORDER BY p.created_at''').fetchall()
    quota = {'cover_review': 8, 'cover_by_you': 12, 'cover_auto_name_match': 10, 'cover_auto_uin': 8}
    taken = {k: 0 for k in quota}
    cover_path = None
    for row in covers:
        fields = json.loads(row['reading_json']).get('fields') or {}
        exact = _digits(fields.get('uin')) == uins.get(row['enrollment_id'])
        stratum = ('cover_review' if row['basis'] == 'review' else 'cover_by_you' if row['actor'] == 'workspace'
                   else 'cover_auto_name_match' if row['basis'] == 'confidence' else 'cover_auto_uin' if exact else None)
        if not stratum or taken[stratum] >= quota[stratum] or not _hot(db, row['message_id']):
            continue
        cover_path = f"/api/documents/messages/{row['message_id']}/unlabeled"
        students = roster(cover_path)
        section = section_of(students, row['enrollment_id'])
        if section is None:
            continue
        taken[stratum] += 1
        strength = 'strong' if row['actor'] == 'workspace' else 'medium'
        cases.append(Case('identity_choice', f"real-{stratum}-{row['scan_page_id'][:10]}", 'real',
                          _run(f"sandbox:{row['scan_page_id']}", [f"/api/documents/messages/{row['message_id']}/unlabeled/{row['scan_page_id']}/cover.png"],
                               students, uins, 'cover', section),
                          {'student': label(row['enrollment_id'], strength), 'own_section_student': label(row['enrollment_id'], strength)},
                          {'stratum': stratum, 'scan_page': row['scan_page_id'], 'roster': len(students), 'ocr_uin_exact': exact},
                          modes=PICTURES, serial=True))
    cover_roster = roster(cover_path) if cover_path else None

    # Printed copies: their name band; the copy's student is the label.
    copies = db.execute('''SELECT r.scan_page_id,sp.message_id,c.enrollment_id,r.reading_json,
            (SELECT actor FROM document_events d WHERE d.kind='copy_assignment' AND d.copy_id=c.id ORDER BY d.id DESC LIMIT 1) named_by
        FROM copy_name_reads r JOIN printed_copies c ON c.id=r.printed_copy_id JOIN scan_pages sp ON sp.id=r.scan_page_id
        WHERE c.enrollment_id IS NOT NULL ORDER BY r.created_at''').fetchall()
    quota = {'copy_named_by_you': 6, 'copy_auto_wrong_uin': 6, 'copy_auto_uin': 6}
    taken = {k: 0 for k in quota}
    for row in copies:
        if cover_roster is None:
            break
        fields = json.loads(row['reading_json']).get('fields') or {}
        exact = _digits(fields.get('uin')) == uins.get(row['enrollment_id'])
        stratum = 'copy_named_by_you' if row['named_by'] == 'workspace' else 'copy_auto_uin' if exact else 'copy_auto_wrong_uin'
        section = section_of(cover_roster, row['enrollment_id'])
        if taken[stratum] >= quota[stratum] or section is None or not _hot(db, row['message_id']):
            continue
        taken[stratum] += 1
        cases.append(Case('identity_choice', f"real-{stratum}-{row['scan_page_id'][:10]}", 'real',
                          _run(f"sandbox:{row['scan_page_id']}", [f"/api/documents/messages/{row['message_id']}/copy-names/{row['scan_page_id']}/name.png"],
                               cover_roster, uins, 'copy', section),
                          {'student': label(row['enrollment_id'], 'strong' if row['named_by'] == 'workspace' else 'medium'),
                           'own_section_student': label(row['enrollment_id'], 'strong' if row['named_by'] == 'workspace' else 'medium')},
                          {'stratum': stratum, 'scan_page': row['scan_page_id'], 'roster': len(cover_roster), 'ocr_uin_exact': exact},
                          modes=PICTURES, serial=True))

    # Bubble sheets: the name and ID boxes; the student the sheet was uploaded to Gradescope for is the label.
    sheets = db.execute('''SELECT b.id,r.message_id,u.enrollment_id,COALESCE((SELECT match_json FROM bubble_matches m WHERE m.sheet_id=b.id
            ORDER BY m.rowid DESC LIMIT 1),b.match_json) match_json FROM bubble_sheets b JOIN bubble_runs r ON r.id=b.run_id
        JOIN frq_gradescope_sheet_uploads u ON u.sheet_id=b.id AND u.status='accepted' ORDER BY b.rowid''').fetchall()
    quota = {'bubble_unmatched': 4, 'bubble_suggested': 12, 'bubble_matched': 8}
    taken = {k: 0 for k in quota}
    for row in sheets:
        match = json.loads(row['match_json'] or '{}')
        stratum = {'unmatched': 'bubble_unmatched', 'suggested': 'bubble_suggested', 'matched': 'bubble_matched'}.get(match.get('state'))
        if not stratum or taken[stratum] >= quota[stratum] or not _hot(db, row['message_id']):
            continue
        students = roster(f"/api/bubble-sheets/sheets/{row['id']}")
        section = section_of(students, row['enrollment_id'])
        if section is None:
            continue
        taken[stratum] += 1
        cases.append(Case('identity_choice', f"real-{stratum}-{row['id'][:10]}", 'real',
                          _run(f"sandbox:{row['id']}", [f"/api/bubble-sheets/sheets/{row['id']}/parts/name", f"/api/bubble-sheets/sheets/{row['id']}/parts/id"],
                               students, uins, 'bubble', section),
                          {'student': label(row['enrollment_id'], 'medium'), 'own_section_student': label(row['enrollment_id'], 'medium')},
                          {'stratum': stratum, 'sheet': row['id'], 'roster': len(students), 'faculty_match': match.get('enrollment_id') == row['enrollment_id'],
                           'faculty_confidence': match.get('confidence')}, modes=PICTURES, serial=True))
    return cases
