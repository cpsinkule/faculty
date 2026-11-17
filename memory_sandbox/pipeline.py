"""The flat pipeline a Faculty memory would be: what happens when the instructor types something (`write`) and when a later
request is read (`read`), over an in-memory store with Faculty's owners. No graph; one request to the decision model per step.

    write: capture (1 call) -> nearest kept memories (embedder, joined with keyword hits) -> consolidate (1 call) -> save,
           skip, refuse or replace
    read:  nearest kept memories (embedder, joined with keyword hits) -> relevance (1 call) -> the few that pass

`Today` is the behaviour to beat: only what the instructor explicitly asked to keep is kept, as typed, and every kept row
is pasted into every request (assistant/store.py: at most 50 rows of 4,000 characters; assistant/conversations.py).
"""
import re
from dataclasses import asdict, dataclass

from . import questions
from .cases import World
from .embedders import EmbedderDown, nearest

# The keyword rule of assistant/memory.py: words of four letters or more, less a stop list.
STOP = {'student', 'students', 'instructor', 'their', 'about', 'please', 'these', 'that', 'this', 'with', 'from', 'when', 'what',
        'should', 'never', 'always', 'every', 'math', 'term'}


def words(text):
    return set(re.findall(r'\b[a-z]{4,}\b', text.lower())) - STOP


def keyword_order(query, texts):
    """Indexes of `texts` sharing a word with `query`, most shared first."""
    wanted = words(query)
    shared = [(len(wanted & words(text)), index) for index, text in enumerate(texts)]
    return [index for count, index in sorted(shared, key=lambda item: (-item[0], item[1])) if count]


def named_in_code(text, roster):
    """The roster check a capture would run in code: 'strong' for a UIN or a full name in any case, 'weak' for a given or
    family name alone written as a name, '' for neither. Single names are case-sensitive, as
    `assistant.followup_learning.mask` matches them, so "may" and "will" pass; "Grant extensions…" and "Mark every…" are
    weak matches, which is what the word-name controls measure."""
    if re.search(r'(?<!\d)\d{9}(?!\d)', text):
        return 'strong'
    weak = False
    for person in roster:
        given, family = person['name'].split(' ', 1)
        if re.search(r'(?<!\w)' + re.escape(person['name']) + r'(?!\w)', text, re.I):
            return 'strong'
        weak = weak or any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text) for name in (given, family))
    return 'weak' if weak else ''


def roster_matches(text, roster):
    """The words of `text` that are also a current student's name, as the code check found them: what the v2 capture request
    is told, so the model can judge "Grant extensions…" against "Volkov's emails…". Only words the instructor typed."""
    found = []
    for person in roster:
        given, family = person['name'].split(' ', 1)
        for name, flags in ((person['name'], re.I), (given, 0), (family, 0)):
            match = re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, flags)
            if match and match.group(0) not in found:
                found.append(match.group(0))
    return sorted(found)


def capture_request(world, event, version=1):
    """(state, questions, rule) of the capture request for one instructor message. Never the case's quoted or tool text."""
    state = {'instructor_message': event['text'], 'channel': event['channel'],
             'earlier_instructor_messages': event['recent'][-2:], 'where': world.where(event)}
    if version == 1:
        return state, questions.CAPTURE, questions.CAPTURE_RULE
    return {**state, 'roster_matches': roster_matches(event['text'], world.roster)}, questions.CAPTURE_V2, questions.CAPTURE_RULE_V2


@dataclass
class Memory:
    id: str
    text: str
    kind: str
    scope: str
    owner: str
    saved_on: str
    status: str = 'current'
    superseded_by: str = ''

    def shown(self):
        """What a decision request is told about a kept memory: no id, no owner id."""
        return {'text': self.text, 'kind': self.kind, 'scope': self.scope, 'saved_on': self.saved_on}


class Store:
    def __init__(self, memories=()):
        self.rows = {memory.id: memory for memory in memories}

    def visible(self, owners):
        return [memory for memory in self.rows.values() if memory.status == 'current' and memory.owner in owners]

    def save(self, memory):
        self.rows[memory.id] = memory

    def supersede(self, old, memory):
        self.rows[old].status, self.rows[old].superseded_by = 'superseded', memory.id
        self.rows[memory.id] = memory

    def snapshot(self):
        return {memory.id: asdict(memory) for memory in self.rows.values()}


def seeded(world, pad=0, episodes=False):
    """The neutral memories every arm starts with, `pad` generated ones more, and (for a read-only store) the episodes."""
    rows = list(world.distractors.values()) + world.padding(pad)
    store = Store(Memory(row['id'], row['text'], row['kind'], row['scope'], row['owner'], '2026-08-20') for row in rows)
    if episodes:
        add_episodes(world, store)
    return store


def add_episodes(world, store):
    for episode in world.episodes.values():
        if episode['worth']:
            store.save(Memory(episode['id'], episode['summary'], 'episode', episode['scope'], episode['owner'], episode['on']))


def oracle_store(world, variant='gold', pad=0):
    """The store a perfect write path leaves: what the read path is measured on when write mistakes must not blur it."""
    store = seeded(world, pad, episodes=True)
    for ident, row in world.oracle_memories().items():
        store.save(Memory(ident, world.statement(world.events[ident], variant), row['kind'], row['scope'], row['owner'],
                          row['saved_on'], row['status'], row['superseded_by']))
    return store


class Flat:
    def __init__(self, world, embedder, gates=None, variant='gold', student_policy='strong', neighbours=5, keyword_neighbours=2,
                 read_k=8, read_keywords=3, inject=5, everything=False, capture=1, single=False):
        self.world, self.embedder, self.variant, self.student_policy = world, embedder, variant, student_policy
        # `capture`: which wording of the capture request. `single`: the purposes asked about each kept memory in a request of
        # its own (True for both), for a model that reads a list by index poorly.
        self.capture = capture
        self.single = {questions.CONSOLIDATE_PURPOSE, questions.RELEVANCE_PURPOSE} if single is True else set(single or ())
        self.gates = {**questions.GATES, **(gates or {})}
        self.neighbours, self.keyword_neighbours = neighbours, keyword_neighbours
        self.read_k, self.read_keywords, self.inject, self.everything = read_k, read_keywords, inject, everything

    def _ranked(self, query, memories, role, limit):
        """(ids nearest first up to `limit`, why a fallback was taken or '')."""
        texts = [memory.text for memory in memories]
        if self.embedder is None:
            return [memories[i].id for i in keyword_order(query, texts)[:limit]], ''
        try:
            order, _ = nearest(self.embedder, query, texts, role)
        except EmbedderDown:
            return [memories[i].id for i in keyword_order(query, texts)[:limit]], 'embedder'
        return [memories[i].id for i in order[:limit]], ''

    def _candidates(self, query, memories, role, limit, keywords):
        ranked, fallback = self._ranked(query, memories, role, limit)
        texts = [memory.text for memory in memories]
        extra = [memories[i].id for i in keyword_order(query, texts)[:keywords]]
        return ranked + [ident for ident in extra if ident not in ranked], fallback

    def write(self, service, event, store):
        """What the pipeline does with one instructor message. `event['untrusted']` is never read."""
        state, asked, rule = capture_request(self.world, event, self.capture)
        outcome, p = questions.capture_view(service.ask(questions.CAPTURE_PURPOSE, event['id'], state, asked, rule))
        in_code = named_in_code(event['text'], self.world.roster)
        action, scope = questions.decide_capture(outcome, p, self.gates, in_code, self.student_policy)
        result = {'event': event['id'], 'action': action, 'kind': outcome.get('kind'), 'scope': scope, 'target': '',
                  'named_in_code': in_code, 'p': p, 'fallback': ''}
        if action != 'candidate':
            return result
        statement = self.world.statement(event, self.variant)
        owner = {'workspace': 'workspace', 'course': event['course'], 'term': event['term']}[scope]
        memory = Memory(event['id'], statement, questions.kept_kind(outcome), scope, owner, event['at'][:10])
        visible = store.visible(World.owners(event))
        by_id = {m.id: m for m in visible}
        candidates, result['fallback'] = self._candidates(statement, visible, 'document', self.neighbours, self.keyword_neighbours)
        candidates = candidates[:self.neighbours]
        verdict, index = 'save', None
        if candidates:
            seen, q = questions.ask_consolidate(service, event['id'], statement, event['text'],
                                                [by_id[ident].shown() for ident in candidates],
                                                questions.CONSOLIDATE_PURPOSE in self.single)
            verdict, index = questions.decide_consolidate(seen, q, len(candidates), self.gates)
            result['consolidate'] = {'candidates': candidates, 'p': q}
        result['action'] = verdict
        if verdict == 'unavailable':  # the decision host did not answer: nothing is kept, nothing is replaced
            return result
        if index is not None:
            result['target'] = candidates[index]
        if verdict == 'save':
            store.save(memory)
        elif verdict == 'supersede':
            store.supersede(candidates[index], memory)
        return result

    def read(self, service, query, store):
        """Which kept memories reach the model for one request."""
        visible = store.visible(World.owners(query))
        by_id = {m.id: m for m in visible}
        ranked12, fallback = self._ranked(query['text'], visible, 'query', 12)
        result = {'query': query['id'], 'visible': len(visible), 'ranked': ranked12, 'fallback': fallback, 'calls': 0}
        if self.everything:
            candidates = sorted(by_id)
        else:
            candidates, _ = self._candidates(query['text'], visible, 'query', self.read_k, self.read_keywords)
        result['candidates'] = candidates
        chosen, use = [], {}
        for start in range(0, len(candidates), 11):
            part = candidates[start:start + 11]
            single = questions.RELEVANCE_PURPOSE in self.single
            seen, p = questions.ask_relevance(service, query['id'], query['text'], self.world.where(query),
                                              [by_id[i].shown() for i in part], single)
            result['calls'] += len(part) if single else 1
            if seen['status'] != 'complete':
                # The decision host did not answer: give the nearest few unjudged rather than nothing or everything.
                result['fallback'] = result['fallback'] or 'decision'
                chosen += [(0.0, ident) for ident in part[:self.inject]]
                continue
            for index in questions.passed(p, len(part), self.gates):
                chosen.append((p[f'use_{index}'], part[index]))
            use.update({part[index]: p[f'use_{index}'] for index in range(len(part))})
        result['passed'] = [ident for _, ident in sorted(chosen, key=lambda item: (-item[0], item[1]))]
        result['injected'] = result['passed'][:self.inject]
        result['use'] = use
        result['chars'] = sum(len(by_id[ident].text) for ident in result['injected'])
        return result


class Today:
    """Preferences as they are: the instructor types what they want kept into a form, and all of it goes into every request.
    Asks no model anything. `explicit` (the instructor said to remember it) stands for "they would have typed it in"."""
    ROWS, CHARS = 50, 4000

    def __init__(self, world):
        self.world = world

    def write(self, service, event, store):
        kept = event['explicit'] and len(store.rows) < self.ROWS
        if kept:
            store.save(Memory(event['id'], event['text'][:self.CHARS], 'preference', 'workspace', 'workspace', event['at'][:10]))
        return {'event': event['id'], 'action': 'save' if kept else 'skip', 'kind': 'preference' if kept else None,
                'scope': 'workspace' if kept else None, 'target': '', 'named_in_code': False, 'p': {}, 'fallback': ''}

    def read(self, service, query, store):
        rows = store.visible(('workspace',))
        ids = [memory.id for memory in rows]
        return {'query': query['id'], 'visible': len(rows), 'ranked': ids[:12], 'candidates': ids, 'passed': ids, 'injected': ids,
                'use': {}, 'fallback': '', 'calls': 0, 'chars': sum(len(memory.text) for memory in rows)}
