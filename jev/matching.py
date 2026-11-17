"""JEV CANVAS-MATCH: which gradebook column a Canvas assignment matched for the first time belongs to (requirement, 2026-10-03).

Jev only names a likely column; the import planner (gradebook/canvas_import.py) decides whether that becomes the suggestion
(both option orders agree, confidence, equal points in code), and the instructor's Import confirms it. One request per Canvas
assignment: its state is that assignment and the term's live columns only, so a later preview reuses the same receipt. A preview
asks for what has no receipt yet; Import never calls Jev, it reads the receipts, so it plans exactly what the preview showed.
"""
from concurrent.futures import ThreadPoolExecutor
import json

from rosters import connect

from . import client
from .service import MAX_CHOICE_OPTIONS, agreed, both, distinct_labels, encoded, fingerprint

PURPOSE = 'canvas-match'
RULE = 'canvas-match-v1'
NEW = 'none of these'
PARALLEL = 8


def _options(items):
    """{option: item id} for the live columns, the title as the option (a repeated title is numbered)."""
    active = [item for item in items if not item.get('archived')]
    labels = distinct_labels((item['title'] for item in active), reserved=(NEW,), casefold=True)
    return {label: item['id'] for label, item in zip(labels, active)}


def request(column, items, categories):
    """The exact state and questions for one Canvas assignment, and the option → item map; None when it cannot be asked."""
    options = _options(items)
    if not options or len(options) > MAX_CHOICE_OPTIONS:
        return None
    names = {category['id']: category['name'] for category in categories}
    by_id = {item['id']: item for item in items}
    criteria = {option: ('Category: ' + names[by_id[ident]['category_id']]) if by_id[ident].get('category_id') in names else None
                for option, ident in options.items()}
    criteria[NEW] = 'No gradebook column is this same assignment; it is a different one.'
    instructions = ('Which gradebook column records the same assignment as `canvas_assignment`? Titles may be worded '
                    'differently: Roman or Arabic numerals, "Test" or "Exam", abbreviations, or extra words such as "Grade". '
                    'A different number or a different kind of work means a different assignment. Choose "' + NEW + '" when '
                    'no column is clearly the same assignment.')
    state = {'canvas_assignment': {'title': column['title'], 'assignment_group': column.get('group') or ''}}
    questions = both('column', instructions, criteria)
    return state, questions, options


def _owner(term_id, column):
    return term_id + ':' + column['key']


def _hint(receipt, options):
    """An agreed pick: both option orders name the same column. {"item_id", "confidence"} (the lower confidence) or None."""
    answers = receipt.get('answers') or {}
    if not answers.get('column') or not answers.get('column__reversed'):
        return None
    pick, sure = agreed(answers, 'column')
    ident = options.get(pick)
    return {'item_id': ident, 'confidence': sure} if ident else None


def _stored(db, term_id, column, asked):
    state, questions, _ = asked
    row = db.execute('''SELECT result_json FROM jev_calls WHERE purpose=? AND owner_id=? AND input_sha256=? AND rule=? AND model=?
        AND questions_json=? AND status='complete' ORDER BY rowid DESC LIMIT 1''',
        (PURPOSE, _owner(term_id, column), fingerprint(state), RULE, client.MODEL, encoded(questions))).fetchone()
    return json.loads(row['result_json']) if row else None


def stored(db, term_id, columns, items, categories):
    """The hints Import uses: completed receipts only, never a request. {column key: hint}."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jev_calls'").fetchone() is None:
        return {}
    hints = {}
    for column in columns:
        asked = request(column, items, categories)
        found = asked and _stored(db, term_id, column, asked)
        hint = found and _hint(found, asked[2])
        if hint:
            hints[column['key']] = hint
    return hints


def suggest(service, term_id, columns, items, categories):
    """The preview's hints: the stored ones, then one request (in parallel) for each assignment still without a receipt. A
    failed or uncertain request gives no hint and is not resent (JEV PROVIDER)."""
    with connect(service.path) as db:
        hints = stored(db, term_id, columns, items, categories)
        missing = []
        for column in columns:
            asked = request(column, items, categories)
            if asked and column['key'] not in hints and _stored(db, term_id, column, asked) is None:
                missing.append((column, asked))
    if not missing or not client.available():
        return hints

    def ask(entry):
        column, (state, questions, options) = entry
        receipt = service.ask(PURPOSE, _owner(term_id, column), state, questions, RULE)
        return column['key'], (_hint(receipt, options) if receipt.get('status') == 'complete' else None)

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        for key, hint in pool.map(ask, missing):
            if hint:
                hints[key] = hint
    return hints
