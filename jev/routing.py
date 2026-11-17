"""JEV ROUTING: a sentence typed in the Ctrl K palette, matched to one of the palette's OWN places (requirement, 2026-10-03).

The palette sends its own non-student items (pages, actions, courses, sections, assignments, exams, grading, seating, rooms,
gradebooks), so every answer is a place it already lists and opens the usual way; nothing here navigates, writes or decides
for the instructor. ONE request (measured 2026-10-03 on 18 typical sentences: a first "which kind of place" question was the
weak step, a single pick over "kind: name" options with each kind's purpose agreed on all 18): the place, in both option
orders, and the grading step asked alongside (speculative fan-out, used only for a grading place). Numbers are matched in
code. The typed sentence is hashed in the receipt, never stored.
"""
import re

from flask import Blueprint, jsonify, request

from . import client
from .service import MAX_CHOICE_OPTIONS, agreed, both, choice

PURPOSE = 'palette-route'
RULE = 'palette-route-v1'
OWNER = 'workspace'
NONE = 'none of these'
CONFIDENT = .6  # navigation writes nothing: 15 of the 18 measured sentences were above it
STEP_CONFIDENT = .7
MAX_BODY = 100000
GROUPS = {
    'Pages': 'A whole page of the app, such as the Inbox, Mail, Absences, Calendar, Exams, Grading or Seating list.',
    'Actions': 'Start something: open a dialog to create, compose, upload, record, import or switch the theme.',
    'Courses': "A course's home page.",
    'Sections': "One section's gradebook tab or its roster.",
    'Canvas': 'Canvas content: modules, pages and assignments of a course or of one section.',
    'Assignments': "An assignment's details, files and printing.",
    'Exams': "An assessment's Exams page: its dates, rooms, makeups and lifecycle.",
    'Grading': 'Grading one assignment: rubrics, answer areas, grading runs, reviewing or flagged grades, certification.',
    'Seating charts': 'A seating chart for an exam.',
    'Rooms': 'A room and its seat layout.',
    'Gradebooks': "A term's whole gradebook and its setup (categories, weights).",
}
TABS = {
    'overview': 'The assignment\'s grading summary and its next step.',
    'rubrics': 'Its rubrics: drafting and editing them.',
    'locations': 'Where each answer is on the page (answer areas).',
    'answer-key': 'Its answer key and rubric finalization.',
    'jobs': 'Its grading runs (batches) and their progress.',
    'review': 'Reviewing its grades: flagged or held grades, regrade requests, one student\'s responses.',
    'export': 'Certifying its grades and sending or publishing them (gradebook, Gradescope).',
    'bubble': 'Its bubble sheets: answer key and item analysis.',
    'papers': 'Its scanned papers (scan records).',
}
DIGITS = re.compile(r'\d+')


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('The palette sent an unreadable place.')
    return ' '.join(value.split())


def clean(data):
    """{query, groups: {name: [{id, label, detail}]}} from the palette; students and scans are never among them."""
    if not isinstance(data, dict) or set(data) - {'query', 'groups'}:
        raise ValueError('Send what was typed and the palette\'s places.')
    query = _text(data.get('query'), 300)
    if len(query.split()) < 2:
        raise ValueError('Type a few words to describe where to go.')
    groups = data.get('groups')
    if not isinstance(groups, dict) or not groups or len(groups) > len(GROUPS) or set(groups) - set(GROUPS):
        raise ValueError('The palette sent an unknown kind of place.')
    cleaned = {}
    for name, items in groups.items():
        if not isinstance(items, list) or len(items) > 2000:
            raise ValueError('The palette sent too many places.')
        rows = []
        for item in items:
            if not isinstance(item, dict) or set(item) - {'id', 'label', 'detail'}:
                raise ValueError('The palette sent an unreadable place.')
            rows.append({'id': _text(item.get('id'), 40), 'label': _text(item.get('label'), 200),
                         'detail': ' '.join(str(item.get('detail') or '').split())[:200]})
        if rows:
            cleaned[name] = rows
    if not cleaned:
        raise ValueError('The palette has no places yet.')
    return query, cleaned


def _numbers(text):
    return set(DIGITS.findall(text))


def candidates(query, items):
    """The options: places naming none of the typed numbers are dropped when some place names them (in code; "Quiz 3" must
    not become "Quiz 4"), places without numbers stay; then by shared words, at most MAX_CHOICE_OPTIONS."""
    wanted = _numbers(query)
    words = set(re.findall(r'[a-z]+', query.casefold()))

    def numbers(item):
        found = _numbers(item['label'] + ' ' + item['detail'])
        return sum(1 for n in wanted if any(f == n or f.startswith(n) for f in found)), bool(found)
    best = max((numbers(item)[0] for item in items), default=0)
    kept = [item for item in items if not best or numbers(item)[0] == best or not numbers(item)[1]]
    kept.sort(key=lambda item: -len(words & set(re.findall(r'[a-z]+', (item['label'] + ' ' + item['detail']).casefold()))))
    return kept[:MAX_CHOICE_OPTIONS]


def options(query, groups):
    """{"kind: name" option: (kind, item id)}; a repeated name carries its detail."""
    rows = candidates(query, [{**item, 'kind': kind} for kind, items in groups.items() for item in items])
    texts = [f"{row['kind']}: {row['label']}" for row in rows]
    found = {}
    for row, text in zip(rows, texts):
        if texts.count(text) > 1 and row['detail']:
            text += f" ({row['detail']})"
        while text in found or text == NONE:
            text += ' *'
        found[text] = (row['kind'], row['id'])
    return found


def route(service, query, groups):
    """{status, ...}: `ok` with picks (best first) and whether the first is confident; `not_navigation`, `unsure`, or the
    receipt's failure (`failed`, `uncertain`, `unavailable`). Never resends a failed request (JEV PROVIDER)."""
    places = options(query, groups)
    criteria = {text: GROUPS[kind] for text, (kind, _) in places.items()}
    criteria[NONE] = 'The request asks a question about data, or for something none of these places is for.'
    questions = both('place', 'Which place in the teaching app should open for the instructor\'s `request`? Each option is '
                     '"kind: name" with what that kind of place is for. Match names and numbers exactly; choose "' + NONE +
                     '" when no place fits.', criteria)
    questions['step'] = choice('If the `request` is about grading an assignment, which part of its grading does it ask for? '
                               'Choose overview when it names no particular part.', dict(TABS))
    receipt = service.ask(PURPOSE, OWNER, {'request': query}, questions, RULE)
    if receipt['status'] != 'complete':
        return {'status': receipt['status'], 'detail': receipt.get('error', '')}
    answers = receipt['answers']
    place, sure = agreed(answers, 'place')
    ranked = sorted(((p, text) for text, p in answers['place']['probabilities'].items() if text != NONE), reverse=True)
    alternatives = [places[text][1] for p, text in ranked if p >= .1][:3]
    if place == NONE and sure >= CONFIDENT:
        return {'status': 'not_navigation'}
    if place in (None, NONE):
        return {'status': 'unsure', 'picks': [{'id': ident} for ident in alternatives]}
    kind, ident = places[place]
    pick = {'id': ident, 'confidence': round(sure, 3)}
    step = answers['step']
    if kind == 'Grading':
        pick['tab'] = step['choice'] if step['confidence'] >= STEP_CONFIDENT else 'overview'
    picks = [pick, *({'id': other} for other in alternatives if other != ident)][:3]
    return {'status': 'ok', 'kind': kind, 'confident': sure >= CONFIDENT, 'picks': picks}


def register(app, service):
    """POST /api/jev/route: an ordinary guarded write (it spends a request and keeps a receipt); the instructor's palette only."""
    api = Blueprint('jev_routing', __name__, url_prefix='/api/jev')

    @api.post('/route')
    def palette_route():
        if (request.content_length or 0) > MAX_BODY:
            raise ValueError('The palette sent too many places.')
        if not client.available():
            return jsonify({'status': 'unavailable'})
        query, groups = clean(request.get_json(silent=True))
        return jsonify(route(service, query, groups))

    app.register_blueprint(api)
