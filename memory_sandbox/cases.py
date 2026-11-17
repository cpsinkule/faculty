"""The invented case set (cases/*.json): what the instructor typed, what should happen to it, and what a later request should use.

Every label is written with its case, before any model reads it. Nothing here comes from a catalog: the courses, terms, people
and UINs are made up. `family` groups a statement with its restatements, updates and exceptions; a family is wholly `fit` or
wholly `test`, so a gate is never fitted on one wording and scored on another of the same statement.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent / 'cases'
EVENT_FILES = ('saves.json', 'chains.json', 'negatives.json', 'students.json', 'quoted.json')
# category -> (kind, scope) of the memory a statement becomes
SAVED = {'preference': ('preference', 'workspace'), 'course_fact': ('fact', 'course'), 'term_fact': ('fact', 'term'),
         'wordname': ('preference', 'workspace'), 'quote_rule': ('preference', 'workspace')}
ACTIONS = {**{cat: 'save' for cat in SAVED}, 'exception': 'save', 'restate': 'duplicate', 'update': 'supersede',
           'one_off': 'skip', 'quoted': 'skip', 'pasted': 'skip', 'record_fact': 'record', 'student': 'refuse_student'}
STATED = (*SAVED, 'exception', 'restate', 'update')  # the instructor's own lasting statement
KEPT = ('save', 'supersede')  # actions that put a memory in the store
NEVER_KEPT = ('one_off', 'quoted', 'pasted')  # categories no memory may be made of
PADS = (0, 1000)  # padded-store sizes: none, and a thousand neutral rows; embedding.texts and OracleEmbedder need max(PADS) rows
# A one-off that describes the student of the conversation in hand: neither answer to "names a student" is wrong.
UNLABELED_STUDENT = {'O30'}
MINIMUM = {'preference': 30, 'course_fact': 20, 'term_fact': 20, 'record_fact': 10, 'one_off': 40, 'restate': 20, 'update': 20,
           'exception': 6, 'student': 30, 'wordname': 10, 'quoted': 25, 'pasted': 5, 'quote_rule': 5}
DEFAULT_TERM = {'c140': 't140f26', 'c142': 't142f26'}
START = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def _read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def split_of(family):
    """'fit' for three families in five, by hash: stable, and blind to what the family says."""
    return 'fit' if int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) % 5 < 3 else 'test'


class World:
    def __init__(self):
        world = _read('world.json')
        self.courses = {c['id']: c for c in world['courses']}
        self.terms = {t['id']: t for t in world['terms']}
        self.roster = world['roster']
        raw = {}
        for name in EVENT_FILES:
            for event in _read(name):
                if event['id'] in raw:
                    raise ValueError('Two cases share the id ' + event['id'])
                raw[event['id']] = event
        self.events = {}
        for ident, event in raw.items():
            self.events[ident] = self._event(event, raw)
        self.episodes = {e['id']: self._episode(e) for e in _read('episodes.json')}
        self.distractors = {d['id']: self._distractor(d) for d in _read('distractors.json')}
        lessons = _read('lessons.json')
        self.lessons = {x['id']: x for x in lessons['lessons']}
        self.requests = {x['id']: {**x, 'split': split_of(x['id'])} for x in lessons['requests']}
        self.unfaithful = {x['id']: {**x, 'split': self.events[x['source']]['split']} for x in lessons['unfaithful']}
        self.queries = {q['id']: self._query(q) for q in _read('queries.json')}

    # -- places -------------------------------------------------------------------------------------------------------------
    def _place(self, item):
        term = item.get('term') or DEFAULT_TERM[item.get('course') or 'c140']
        course = self.terms[term]['course']
        if item.get('course') and item['course'] != course:
            raise ValueError(f"{item['id']}: its term is not of its course")
        return course, term

    def where(self, item):
        """What a request says about where the instructor is, in words (never an id)."""
        return {'page': item.get('page') or '#assistant', 'course': self.courses[item['course']]['name'],
                'term': self.terms[item['term']]['name']}

    @staticmethod
    def owners(item):
        """The owners whose memories are in scope where `item` was typed."""
        return ('workspace', item['course'], item['term'])

    # -- events -------------------------------------------------------------------------------------------------------------
    def _root(self, event, raw):
        seen = event
        while seen['cat'] in ('restate', 'update', 'exception'):
            seen = raw[seen.get('of') or seen['near']]
        return seen

    def _event(self, event, raw):
        cat = event['cat']
        if cat not in ACTIONS:
            raise ValueError(f"{event['id']}: unknown category {cat}")
        course, term = self._place(event)
        root = self._root(event, raw)
        kind = scope = None
        if cat in SAVED:
            kind, scope = SAVED[cat]
        elif cat == 'exception':
            kind, scope = event['kind'], event['scope']
        elif cat in ('restate', 'update'):
            kind, scope = SAVED[raw[event['of']]['cat']]
        owner = {None: None, 'workspace': 'workspace', 'course': course, 'term': term}[scope]
        family = root['id']
        stated = cat in STATED
        labels = {'lasting': 1 if stated else 0 if cat in NEVER_KEPT else None,
                  'one_off': 1 if cat == 'one_off' else 0 if stated else None,
                  'explicit': int(bool(event.get('x'))) if cat in (*SAVED, 'student', *NEVER_KEPT) else None,
                  'own_words': 1 if stated else 0 if cat == 'pasted' else None,
                  'names_student': 1 if cat == 'student' else None if event['id'] in UNLABELED_STUDENT else 0,
                  'record_fact': 1 if cat == 'record_fact' else 0 if stated else None,
                  'kind': kind if stated else 'none' if cat in NEVER_KEPT else None,
                  'scope': scope if stated else None}
        return {'id': event['id'], 'cat': cat, 'family': family, 'split': split_of(family), 'channel': event.get('channel', 'chat'),
                'course': course, 'term': term, 'text': event['text'], 'say': event.get('say', ''), 'recent': list(event.get('recent', [])),
                'fragment': bool(event.get('frag')), 'explicit': bool(event.get('x')), 'untrusted': list(event.get('untrusted', [])),
                'of': event.get('of', ''), 'near': event.get('near', ''), 'labels': labels,
                'expect': {'action': ACTIONS[cat], 'kind': kind, 'scope': scope, 'owner': owner,
                           'supersedes': event['of'] if cat == 'update' else '',
                           'duplicate_of': event['of'] if cat == 'restate' else ''}}

    def statement(self, event, variant):
        """What the memory would say: the instructor's sentence as typed ('raw'), or the hand-written standalone one ('gold')."""
        return event['say'] if variant == 'gold' and event['say'] else event['text']

    def timeline(self):
        """Every event once, in the order the instructor typed them: first statements, with other messages between; then the
        restatements; then the updates and exceptions. A base always precedes what restates or changes it, and a restatement
        precedes the update of the same statement, so each expected action is true of the store at that moment."""
        by = lambda *cats: [e for e in self.events.values() if e['cat'] in cats]  # noqa: E731 - file order within a category
        first = by(*SAVED)
        other = by('one_off', 'record_fact', 'student', 'quoted', 'pasted')
        later = by('restate') + by('update') + by('exception')
        share = len(other) * len(first) // (len(first) + len(later))
        order = _weave(first, other[:share]) + _weave(later, other[share:])
        return [{**event, 'at': (START + timedelta(hours=5 * index)).isoformat()} for index, event in enumerate(order)]

    # -- memories that are simply there -------------------------------------------------------------------------------------
    def _episode(self, episode):
        course, term = self._place(episode) if episode.get('term') else ('', '')
        owner = term or 'workspace'
        return {**episode, 'course': course or 'c140', 'term': term or 't140f26', 'owner': owner, 'scope': 'term' if term else 'workspace',
                'split': split_of(episode['id'])}

    def _distractor(self, item):
        scope = item.get('scope', 'workspace')
        course, term = self._place(item) if scope != 'workspace' else ('', '')
        return {'id': item['id'], 'text': item['text'], 'kind': 'preference' if scope == 'workspace' else 'fact', 'scope': scope,
                'owner': {'workspace': 'workspace', 'course': course, 'term': term}[scope]}

    def padding(self, count):
        """`count` more neutral memories, made from word lists: for the store sizes no one would write by hand."""
        things = ('stapler', 'spare projector bulb', 'visitor parking pass', 'seminar sign-in sheet', 'lab coat', 'poster tube',
                  'conference badge', 'loaner tablet', 'label printer', 'extension cord', 'tripod', 'whiteboard eraser',
                  'packing tape', 'spare toner', 'door wedge', 'umbrella', 'travel mug', 'badge reel', 'card reader', 'desk fan')
        places = ('the second drawer', 'the hall closet', 'the mail room shelf', 'the blue bin', 'the seminar room cabinet',
                  'the bottom file drawer', 'the supply cart', 'the window ledge', 'the front desk', 'the grey locker')
        days = ('Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays')
        rows = []
        for n in range(count):
            thing, place, day = things[n % len(things)], places[(n // len(things)) % len(places)], days[(n // 200) % len(days)]
            rows.append({'id': f'Z{n + 1:04d}', 'kind': 'preference', 'scope': 'workspace', 'owner': 'workspace',
                         'text': f'The {thing} for room {300 + n} is kept in {place} and checked on {day}.'})
        return rows

    def oracle_memories(self):
        """The store a perfect write path leaves after the whole timeline: {id: memory}, superseded ones included."""
        found = {}
        for event in self.timeline():
            expect = event['expect']
            if expect['action'] not in KEPT:
                continue
            found[event['id']] = {'id': event['id'], 'kind': expect['kind'], 'scope': expect['scope'], 'owner': expect['owner'],
                                  'saved_on': event['at'][:10], 'status': 'current', 'superseded_by': ''}
            if expect['supersedes']:
                found[expect['supersedes']].update(status='superseded', superseded_by=event['id'])
        return found

    # -- queries ------------------------------------------------------------------------------------------------------------
    def _query(self, query):
        course, term = self._place(query)
        family = self.events[query['used'][0]]['family'] if query['used'] and query['used'][0] in self.events else query['id']
        return {'id': query['id'], 'text': query['text'], 'course': course, 'term': term, 'used': list(query['used']),
                'maybe': list(query.get('maybe', [])), 'not': list(query.get('not', [])), 'family': family, 'split': split_of(family)}


def _weave(main, other):
    """`other` spread evenly through `main`, both in their own order."""
    woven, taken = [], 0
    for index, item in enumerate(main):
        woven.append(item)
        due = (index + 1) * len(other) // len(main)
        woven += other[taken:due]
        taken = due
    return woven + other[taken:]


def validate(world):
    """Raises on a case that contradicts another; returns the counts by category."""
    counts = {}
    for event in world.events.values():
        counts[event['cat']] = counts.get(event['cat'], 0) + 1
        if not event['text'].strip():
            raise ValueError(event['id'] + ': no instructor text')
        for ref in (event['of'], event['near']):
            if ref and ref not in world.events:
                raise ValueError(f"{event['id']}: refers to {ref}, which is not a case")
        if event['cat'] in (*SAVED, 'update', 'exception') and not event['say']:
            raise ValueError(event['id'] + ': a kept statement needs its standalone sentence')
        if event['cat'] == 'quoted' and not event['untrusted']:
            raise ValueError(event['id'] + ': a quoted case needs the quoted text')
        for quoted in event['untrusted']:
            if quoted['text'] in event['text']:
                raise ValueError(event['id'] + ': the quoted text is also in the instructor text')
    short = {cat: (counts.get(cat, 0), least) for cat, least in MINIMUM.items() if counts.get(cat, 0) < least}
    if short:
        raise ValueError('Too few cases: ' + ', '.join(f'{cat} {have}/{least}' for cat, (have, least) in sorted(short.items())))
    order = [event['id'] for event in world.timeline()]
    if sorted(order) != sorted(world.events):
        raise ValueError('The timeline does not hold every event exactly once.')
    place = {ident: index for index, ident in enumerate(order)}
    for event in world.events.values():
        base = event['of'] or event['near']
        if base and place[base] > place[event['id']]:
            raise ValueError(f"{event['id']} comes before {base}")
    updated = {}
    for event in world.events.values():
        if event['cat'] == 'update':
            if event['of'] in updated:
                raise ValueError(f"{event['of']} is updated twice ({updated[event['of']]}, {event['id']})")
            updated[event['of']] = event['id']
    for event in world.events.values():
        if event['cat'] == 'restate' and event['of'] in updated and place[updated[event['of']]] < place[event['id']]:
            raise ValueError(f"{event['id']} restates {event['of']} after it was replaced")
    memories = world.oracle_memories()
    known = set(memories) | {e['id'] for e in world.episodes.values() if e['worth']} | set(world.distractors)
    for query in world.queries.values():
        owners = World.owners(query)
        for ident in query['used'] + query['maybe']:
            if ident not in known:
                raise ValueError(f"{query['id']}: {ident} is not a memory")
            row = memories.get(ident) or world.episodes.get(ident) or world.distractors[ident]
            if row.get('status', 'current') != 'current':
                raise ValueError(f"{query['id']}: expects {ident}, which was replaced")
            if row['owner'] not in owners:
                raise ValueError(f"{query['id']}: expects {ident}, which another course or term owns")
        for ident in query['not']:
            if ident not in world.events:
                raise ValueError(f"{query['id']}: {ident} is not a case")
            if ident in query['used'] + query['maybe']:
                raise ValueError(f"{query['id']}: {ident} is both expected and forbidden")
    for item in world.unfaithful.values():
        if item['source'] not in world.events:
            raise ValueError(f"{item['id']}: no such source")
    for request in world.requests.values():
        for ident in request['expect']:
            if ident not in world.lessons:
                raise ValueError(f"{request['id']}: no such lesson {ident}")
    for cat in ('quoted', 'pasted', 'student', 'one_off'):
        for event in world.events.values():
            if event['cat'] == cat and event['expect']['action'] in KEPT:
                raise ValueError(event['id'] + ' must never be kept')
    return {**counts, 'queries': len(world.queries), 'episodes': len(world.episodes), 'distractors': len(world.distractors),
            'lessons': len(world.lessons), 'requests': len(world.requests), 'unfaithful': len(world.unfaithful)}
