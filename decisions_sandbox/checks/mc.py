"""Multiple choice from images (requirement, 2026-10-07). Decisions and, when `--modes` names them, the other picture readers (`cases.PICTURES`: Snap, Jev 1.14,
Jev 1.13 with its opt-in images, Liquid d1-ultra; the app's own Jev and Microsoft-Decision-1 read text only); each question a Choice
over the letters plus "blank"/"none" and "several", asked in both option orders (position bias is measured, as Faculty does for Jev).

Bubble sheets: one row of bubbles (the live app's own `/parts/q<N>` cut), and whole sheets (one request, one question per row).
Their reference is Faculty's pixel reader (`bubble_sheets.reader.decide`, today's rule); no instructor has corrected a mark, so the
reader is the label, strongest on clean rows, and printed rows past the last question are known blanks.
Quizzes: the answer area of a question whose rubric reads "Correct choice: (x)". Full credit means the student marked the key's
letter (your own review: strong; the automatic grade: medium); otherwise the reader's single transcribed letter is a silver label."""
import json
import re

from bubble_sheets import reader
from jev.service import agreed, both

from ..cases import PICTURES, Case, label
from .images import _crops_of, _hot, _readable, fetch

LETTERS = 'ABCDE'
BUBBLE_RULE = 'sandbox-bubble-choice-v1'
BUBBLE_STATE = ('The image is one row of a bubble answer sheet: a question number and five bubbles labelled A to E. Judge which '
                'bubbles the student filled in (darkened).')
BUBBLE_CRITERIA = {**{letter: f'Only bubble {letter} is filled in.' for letter in LETTERS},
                   'blank': 'No bubble in the row is filled in.', 'several': 'Two or more bubbles are filled in.'}
BUBBLE_ASK = ('Which bubbles in this row are filled in? A deliberate mark counts even if it is light; the faint ghost of an erased mark '
              'beside a darker filled bubble does not, and neither do printed letters or outlines.')
SHEET_RULE = 'sandbox-bubble-sheet-choice-v1'
SHEET_STATE = ('The image is a whole bubble answer sheet. Each numbered question has a row of five bubbles labelled A to E. Judge '
               'which bubbles the student filled in (darkened) for each question asked.')
QUIZ_RULE = 'sandbox-quiz-choice-v1'


def _answer(reading):
    """Faculty's reading of one row as a choice option: a letter, 'blank' or 'several' ('unsure' when the reader is in doubt)."""
    state, filled, _ = reading
    return {'single': filled, 'blank': 'blank', 'multiple': 'several'}.get(state, 'unsure')


def _decide(marks):
    return reader.decide([float(marks.get(letter, 0)) for letter in LETTERS])


def _pick(receipt, name):
    """(first order's choice, its probability, both orders agree, the lower confidence, p of each option averaged over both orders)."""
    if receipt.get('status') != 'complete':
        return None, None, None, None, {}
    answers = receipt['answers']
    first, second = answers[name], answers[name + '__reversed']
    together, _ = agreed(answers, name)
    mean = {option: (first['probabilities'][option] + second['probabilities'][option]) / 2 for option in first['probabilities']}
    return first['choice'], first['probabilities'][first['choice']], together is not None, min(first['confidence'], second['confidence']), mean


def _row_run(owner, path):
    memo = {}

    def run(service):
        if 'png' not in memo:
            memo['png'] = fetch(path)
        receipt = service.ask('sandbox-bubble-choice', owner, BUBBLE_STATE, both('marked', BUBBLE_ASK, BUBBLE_CRITERIA), BUBBLE_RULE,
                              images=(memo['png'],))
        choice, p, together, confidence, mean = _pick(receipt, 'marked')
        best = max(mean, key=mean.get) if mean else None
        return {'outcome': {'status': receipt.get('status'), 'choice': choice, 'mean_choice': best, 'orders_agree': together},
                'p': {'confidence': confidence, 'choice_p': p}}
    return run


def _sheet_run(owner, raster, sha, numbers):
    memo = {}
    questions = {}
    for n in numbers:
        questions.update(both(f'q{n}', f'Question {n}: ' + BUBBLE_ASK, BUBBLE_CRITERIA))

    def run(service):
        if 'png' not in memo:
            memo['png'] = fetch(f'/api/inbox/artifacts/{raster}/download', sha)
        receipt = service.ask('sandbox-bubble-sheet-choice', owner, SHEET_STATE, questions, SHEET_RULE, images=(memo['png'],))
        outcome, p = {'status': receipt.get('status')}, {}
        for n in numbers:
            choice, _, together, confidence, _ = _pick(receipt, f'q{n}')
            outcome[f'q{n}'] = choice
            outcome[f'q{n}_orders_agree'] = together
            p[f'q{n}_confidence'] = confidence
        return {'outcome': outcome, 'p': p}
    return run


def _quiz_run(owner, crop, options, key):
    memo = {}
    # The app's question since 2026-10-16 (`grading.choice.ASK`); run-20261016-mc holds the longer wording's answers beside it.
    criteria = {**{o: o for o in options}, 'none': 'No answer choice is selected.', 'several': 'More than one answer choice is selected.'}
    asked = both('marked', 'Which answer choice did the student select?', criteria)
    state = ('The image is a student\'s answer area for one multiple-choice question on a printed quiz: the question, its lettered options, '
             'and the student\'s handwriting.')

    def run(service):
        if 'png' not in memo:
            memo['png'] = fetch(f'/api/inbox/artifacts/{crop[0]}/download', crop[1])
        receipt = service.ask('sandbox-quiz-choice', owner, state, asked, QUIZ_RULE, images=(memo['png'],))
        choice, p, together, confidence, mean = _pick(receipt, 'marked')
        best = max(mean, key=mean.get) if mean else None
        return {'outcome': {'status': receipt.get('status'), 'choice': choice, 'mean_choice': best, 'orders_agree': together},
                'p': {'confidence': confidence, 'choice_p': p, 'is_key': mean.get(key) if mean else None}}
    return run


# ================================================================================================================ cases
def _bubble_cases(db):
    cases = []
    runs = db.execute('''SELECT r.id,r.message_id,r.question_count FROM bubble_runs r WHERE r.id=(SELECT id FROM bubble_runs w
        WHERE w.message_id=r.message_id ORDER BY w.created_at DESC, w.rowid DESC LIMIT 1) ORDER BY r.created_at''').fetchall()
    rows, sheets = [], []
    for run in runs:
        if not _hot(db, run['message_id']):
            continue
        for sheet in db.execute('''SELECT b.id,b.reading_json,sp.raster_artifact_id,a.sha256 FROM bubble_sheets b JOIN scan_pages sp ON sp.id=b.scan_page_id
                LEFT JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id WHERE b.run_id=? ORDER BY b.rowid''', (run['id'],)):
            reading = json.loads(sheet['reading_json'])
            if not reading.get('aligned'):
                continue
            sheets.append((sheet, reading, run['question_count']))
            for answer in reading.get('answers', []):
                if answer['question'] > (run['question_count'] or 0):
                    continue
                marks = answer.get('marks') or {}
                values = sorted((float(v) for v in marks.values()), reverse=True)
                decided = _decide(marks)
                rows.append({'sheet': sheet['id'], 'question': answer['question'], 'answer': _answer(decided), 'state': decided[0],
                             'top': values[0] if values else 0, 'second': values[1] if len(values) > 1 else 0})
    strata = {
        'clean_single': lambda r: r['state'] == 'single' and r['second'] <= .03 and r['top'] >= .19,
        'light_single': lambda r: r['state'] == 'single' and r['top'] < .19,
        'ghost_single': lambda r: r['state'] == 'single' and r['second'] > .03,
        'multiple': lambda r: r['state'] == 'multiple',
        'blank': lambda r: r['state'] == 'blank',
        'unsure': lambda r: r['state'] == 'unsure',
    }
    quota = {'clean_single': 15, 'light_single': 12, 'ghost_single': 10, 'multiple': 10, 'blank': 13, 'unsure': 10}
    strengths = {'clean_single': 'medium', 'light_single': 'weak', 'ghost_single': 'weak', 'multiple': 'weak', 'blank': 'weak', 'unsure': 'weak'}
    for stratum, test in strata.items():
        found = [r for r in rows if test(r)]
        # spread across sheets: lightest first for the light stratum, otherwise every k-th
        if stratum == 'light_single':
            found.sort(key=lambda r: r['top'])
        step = max(1, len(found) // quota[stratum]) if stratum == 'clean_single' else 1
        for r in found[::step][:quota[stratum]]:
            wanted = {'choice': label(r['answer'], strengths[stratum])} if r['answer'] != 'unsure' else {}
            cases.append(Case('bubble_row', f"real-{stratum}-{r['sheet'][:8]}-q{r['question']}", 'real',
                              _row_run(f"sandbox:{r['sheet']}:q{r['question']}", f"/api/bubble-sheets/sheets/{r['sheet']}/parts/q{r['question']}"),
                              wanted, {'stratum': stratum, 'sheet': r['sheet'], 'question': r['question'], 'reader': r['answer'],
                                       'top': round(r['top'], 3), 'second': round(r['second'], 3)}, modes=PICTURES, serial=True))
    # Printed rows past the last question: blank on every real sheet.
    for sheet, reading, count in sheets[::60][:5]:
        n = (count or 17) + 2
        cases.append(Case('bubble_row', f"real-unused_row-{sheet['id'][:8]}-q{n}", 'real',
                          _row_run(f"sandbox:{sheet['id']}:q{n}", f"/api/bubble-sheets/sheets/{sheet['id']}/parts/q{n}"),
                          {'choice': label('blank', 'strong')}, {'stratum': 'unused_row', 'sheet': sheet['id'], 'question': n, 'reader': 'blank'},
                          modes=PICTURES, serial=True))
    # Version rows: up to 3 each of A, B and C.
    taken = {'A': 0, 'B': 0, 'C': 0}
    for sheet, reading, _ in sheets:
        version = reading.get('version') or {}
        answer = _answer(_decide(version.get('marks') or {}))
        if answer in taken and taken[answer] < 3:
            taken[answer] += 1
            cases.append(Case('bubble_row', f"real-version-{sheet['id'][:8]}", 'real',
                              _row_run(f"sandbox:{sheet['id']}:version", f"/api/bubble-sheets/sheets/{sheet['id']}/parts/version"),
                              {'choice': label(answer, 'medium')}, {'stratum': 'version_row', 'sheet': sheet['id'], 'reader': answer},
                              modes=PICTURES, serial=True))
    # Whole sheets: one request each, one question per printed row the exam uses; the reader's rows are the labels.
    picked = [s for s in sheets if s[0]['raster_artifact_id'] and s[0]['sha256']][::40][:8]
    for sheet, reading, count in picked:
        numbers = list(range(1, (count or 17) + 1))
        wanted = {}
        for answer in reading.get('answers', []):
            if answer['question'] in numbers:
                decided = _answer(_decide(answer.get('marks') or {}))
                if decided != 'unsure':
                    wanted[f"q{answer['question']}"] = label(decided, 'medium')
        cases.append(Case('bubble_sheet', f"real-sheet-{sheet['id'][:8]}", 'real',
                          _sheet_run(f"sandbox:{sheet['id']}:sheet", sheet['raster_artifact_id'], sheet['sha256'], numbers), wanted,
                          {'sheet': sheet['id'], 'questions': len(numbers)}, modes=PICTURES, serial=True))
    return cases


def calibration(ctx):
    """GRADING MULTIPLE-CHOICE READER calibration (2026-10-08): every quiz multiple-choice answer area, asked exactly as the app asks
    (`grading.choice.questions` is word for word `_quiz_run`'s question; the crop is the original, grayscale, long side 1600)."""
    from grading import choice
    assert choice.questions(['(a)', '(b)']) == both('marked', 'Which answer choice did the student select?', {
        '(a)': '(a)', '(b)': '(b)', 'none': 'No answer choice is selected.', 'several': 'More than one answer choice is selected.'})
    return _quiz_cases(ctx.db, quota=None)


def _quiz_cases(db, quota={'reviewed_by_you': 30, 'confident': 16, 'flagged': 20}):
    cases = []
    keys = {}
    for rubric_id, question_id, rules in db.execute('SELECT id,question_id,rules_json FROM frq_rubric_revisions'):
        for item in json.loads(rules).get('items', []):
            found = re.search(r'Correct choice:\s*\$*\s*\(([a-e])\)', item.get('description') or '')
            if found:
                keys[rubric_id] = found.group(1)
    rows = db.execute('''SELECT resp.id response_id,resp.current_result_id,resp.current_review_id,aq.rubric_id,aq.question_revision_id,
            res.task_id,res.transcript_json,res.score_json result_score,res.signals_json,res.state result_state
        FROM frq_responses resp JOIN frq_assignment_questions aq ON aq.assignment_id=resp.assignment_id AND aq.question_id=resp.question_id
        JOIN frq_results res ON res.id=resp.current_result_id ORDER BY resp.created_at''').fetchall()
    pools = {'reviewed_by_you': [], 'confident': [], 'flagged': []}
    for row in rows:
        key = keys.get(row['rubric_id'])
        if not key:
            continue
        content = json.loads(db.execute('SELECT content_json FROM frq_question_revisions WHERE id=?', (row['question_revision_id'],)).fetchone()[0])
        problem = json.dumps(content.get('problem') or content)
        options = sorted(set(re.findall(r'\(([a-e])\)', problem))) or list('abcd')
        if key not in options:
            options = sorted(set(options) | {key})
        review = db.execute('SELECT score_json,actor FROM frq_reviews WHERE id=?', (row['current_review_id'],)).fetchone() if row['current_review_id'] else None
        score = json.loads((review or row)['score_json' if review else 'result_score'] or '{}')
        full = score.get('score') is not None and score.get('max_score') and float(score['score']) >= float(score['max_score'])
        by_you = bool(review and review['actor'] == 'workspace')
        transcript = json.loads(row['transcript_json'] or '{}')
        marked, struck = set(), 0
        for line in transcript.get('lines', []):
            for element in line.get('elements', []):
                match = re.fullmatch(r'\s*\(?([a-e])\)?\s*', element.get('text') or '')
                if match:
                    if element.get('state') == 'crossed_out':
                        struck += 1
                    else:
                        marked.add(match.group(1))
        risk = (json.loads(row['signals_json'] or '{}').get('risk') or {}).get('level')
        entry = {'row': row, 'key': key, 'options': options, 'full': bool(full), 'by_you': by_you, 'reader': next(iter(marked)) if len(marked) == 1 else '',
                 'readers': len(marked), 'struck': struck, 'risk': risk}
        if by_you:
            pools['reviewed_by_you'].append(entry)
        elif risk == 'low' and len(marked) == 1 and not struck and row['result_state'] == 'proposed':
            pools['confident'].append(entry)
        elif struck or len(marked) != 1 or risk == 'high':
            pools['flagged'].append(entry)
        elif quota is None:  # the calibration reads every answer area
            pools.setdefault('other', []).append(entry)
    for stratum, entries in pools.items():
        if quota and stratum == 'confident':  # half full credit, half not
            entries = [e for e in entries if e['full']][:quota[stratum] // 2] + [e for e in entries if not e['full']][:quota[stratum] // 2]
        taken = 0
        for e in entries:
            if quota and taken >= quota[stratum]:
                break
            crops = _crops_of(db, e['row']['task_id'])
            if not _readable(db, crops):
                continue
            strength = 'strong' if e['by_you'] else 'medium'
            option = f"({e['key']})"
            wanted = {'is_key': label(1 if e['full'] else 0, strength)}
            if e['full']:
                wanted['choice'] = label(option, strength)
            elif e['reader']:
                wanted['choice'] = label(f"({e['reader']})", 'silver')
            options = [f'({o})' for o in e['options']]
            cases.append(Case('quiz_choice', f"real-{stratum}-{e['row']['response_id'][:10]}", 'real',
                              _quiz_run(f"sandbox:{e['row']['response_id']}", crops[0], options, option), wanted,
                              {'stratum': stratum, 'response': e['row']['response_id'], 'key': option, 'full_credit': e['full'],
                               'reader': f"({e['reader']})" if e['reader'] else '', 'reader_letters': e['readers'], 'struck': e['struck'],
                               'risk': e['risk']}, modes=PICTURES, serial=True))
            taken += 1
    return cases


def real(ctx):
    return _bubble_cases(ctx.db) + _quiz_cases(ctx.db)
