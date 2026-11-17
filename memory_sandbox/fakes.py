"""Stand-ins that send nothing: an oracle that answers every sandbox question from the cases' own labels, and an embedder
that puts related texts together. With both, the pipeline must reach exactly the expected store; that is what the synthetic
checks assert. `decisions_sandbox.fakes.model` (answers from a hash) is the other fake: it exercises the code on arbitrary
answers."""
import json

import numpy as np

from jev.service import encoded

from .cases import PADS

YES, NO = .97, .03
BY_TEXT = ' Kept memory: '


def _noul(yes):
    return {'type': 'noul', 'noul': YES if yes else NO}


def _choice(asked, pick):
    options = list(asked['criteria'])
    rest = round(.06 / (len(options) - 1), 4)
    pick = pick if pick in options else options[0]
    probabilities = {option: (.94 if option == pick else rest) for option in options}
    n = len(options)
    return {'type': 'choice', 'choice': pick, 'probabilities': probabilities, 'confidence': (.94 - 1 / n) / (1 - 1 / n)}


class Oracle:
    def __init__(self, world, name='oracle'):
        self.world, self.name = world, name
        self.messages = {event['text']: event for event in world.events.values()}
        self.statements = {}
        for event in world.events.values():
            for text in {event['text'], event['say']} - {''}:
                if self.statements.setdefault(text, event['id']) != event['id']:
                    raise ValueError(f"Two cases share the sentence of {event['id']}")
        self.rows = {**self.statements, **{d['text']: d['id'] for d in world.distractors.values()},
                     **{e['summary']: e['id'] for e in world.episodes.values()}}
        self.unfaithful = {item['rewrite'] for item in world.unfaithful.values()}
        self.fragments = {event['text'] for event in world.events.values() if event['fragment']}
        self.queries = {query['text']: query for query in world.queries.values()}
        self.requests = {request['text']: request for request in world.requests.values()}
        self.summaries = {}
        for episode in world.episodes.values():
            self.summaries[episode['summary']] = (episode, True, False)
            if episode.get('bad'):
                self.summaries[episode['bad']] = (episode, False, False)
            if episode.get('named'):
                self.summaries[episode['named']] = (episode, True, True)
        replaced = {event['of']: event['id'] for event in world.events.values() if event['cat'] == 'update'}
        # A statement and whatever replaced it: what an exception leaves standing.
        self.chain = {event['id']: {event['id'], replaced.get(event['id'], event['id'])} for event in world.events.values()}

    def __call__(self, state, questions, images=()):
        if 'lasting' in questions:
            answers = self._capture(state, questions)
        elif 'faithful' in questions:
            answers = self._consolidate(state)
        elif 'same' in questions:
            one = self._consolidate({**state, 'candidates': [state['candidate']]})
            answers = {name: one[name + '_0'] for name in ('same', 'replaces', 'narrows')}
        elif 'use' in questions:
            one = self._relevance({**state, 'candidates': [state['candidate']]})
            answers = {'use': one['use_0'], 'conflicts': one['conflicts_0']}
        elif 'worth' in questions:
            answers = self._episode(state, questions)
        elif any(name.startswith('rank_') for name in questions):
            answers = self._rank(state, questions)
        elif 'memories' in state:
            answers = self._each(state)
        elif any(BY_TEXT in asked['instructions'] for asked in questions.values()):
            answers = self._by_text(state, questions)
        elif any(name.startswith('use_') for name in questions):
            answers = self._relevance(state)
        elif any(name.startswith('apply_') for name in questions):
            answers = self._lessons(state, questions)
        else:
            raise ValueError('The oracle has no labels for these questions.')
        return {'model': self.name, 'answers': answers, 'usage': {'input_tokens': len(encoded(state)) // 4, 'output_tokens': 0},
                'gaps': {}}

    def _capture(self, state, questions):
        event = self.messages[state['instructor_message']]
        cat, expect = event['cat'], event['expect']
        stated = expect['action'] in ('save', 'duplicate', 'supersede')
        lasting = stated or cat in ('student', 'record_fact')
        kind = expect['kind'] or ('fact' if cat == 'record_fact' else 'preference' if cat == 'student' else 'none')
        scope = expect['scope'] or 'unclear'
        return {'lasting': _noul(lasting), 'one_off': _noul(cat == 'one_off'), 'explicit': _noul(event['explicit']),
                'own_words': _noul(cat != 'pasted'), 'names_student': _noul(cat == 'student'),
                'record_fact': _noul(cat == 'record_fact'),
                **{name: _choice(questions[name], kind) for name in ('kind', 'kind__reversed')},
                **{name: _choice(questions[name], scope) for name in ('scope', 'scope__reversed')}}

    def _consolidate(self, state):
        new = self.world.events.get(self.statements.get(state['new_statement'], ''))
        answers = {'faithful': _noul(state['new_statement'] not in self.unfaithful),
                   'standalone': _noul(state['new_statement'] not in self.fragments)}
        for index, candidate in enumerate(state['candidates']):
            kept = self.rows.get(candidate['text'], '')
            answers[f'same_{index}'] = _noul(bool(new) and new['cat'] == 'restate' and kept == new['of'])
            answers[f'replaces_{index}'] = _noul(bool(new) and new['cat'] == 'update' and kept == new['of'])
            answers[f'narrows_{index}'] = _noul(bool(new) and new['cat'] == 'exception' and kept in self.chain[new['near']])
        return answers

    def _relevance(self, state):
        query = self.queries[state['request']]
        answers = {}
        for index, candidate in enumerate(state['candidates']):
            answers[f'use_{index}'] = _noul(self.rows.get(candidate['text'], '') in query['used'])
            answers[f'conflicts_{index}'] = _noul(False)
        return answers

    def _rank(self, state, questions):
        """A Choice over kept memories (`whole.rank_request`): the needed ones share the probability, or "none of these" has it."""
        used = set(self.queries[state['request']]['used'])
        answers = {}
        for name, asked in questions.items():
            options = asked['criteria']
            top = [option for option, text in options.items() if self.rows.get(text, '') in used] or ['none of these']
            rest = .06 / max(1, len(options) - len(top))
            probabilities = {option: (.94 / len(top) if option in top else rest) for option in options}
            n = len(options)
            answers[name] = {'type': 'choice', 'choice': top[0], 'probabilities': probabilities,
                             'confidence': max(0.0, (max(probabilities.values()) - 1 / n) / (1 - 1 / n))}
        return answers

    def _each(self, state):
        used = set(self.queries[state['request']]['used'])
        return {f'use_{index}': _noul(self.rows.get(memory['text'], '') in used) for index, memory in enumerate(state['memories'])}

    def _by_text(self, state, questions):
        """The relevance questions that quote one kept memory each (`whole.pick_by_text`)."""
        used = set(self.queries[state['request']]['used'])
        return {name: _noul(name.startswith('use_') and self.rows.get(json.loads(asked['instructions'].split(BY_TEXT, 1)[1]), '') in used)
                for name, asked in questions.items()}

    def _episode(self, state, questions):
        episode, supported, names = self.summaries[state['summary']]
        return {'worth': _noul(episode['worth']), 'supported': _noul(supported), 'names_student': _noul(names),
                **{name: _choice(questions[name], episode['scope']) for name in ('scope', 'scope__reversed')}}

    def _lessons(self, state, questions):
        request = self.requests[state['current_request']]
        answers = {}
        for index, candidate in enumerate(state['candidates']):
            answers[f'apply_{index}'] = _noul(candidate['id'] in request['expect'])
        answers.update({name: _noul(True) for name in questions if name.startswith('portable_')})
        return answers


class OracleEmbedder:
    """Vectors that put a statement beside its restatements, updates and exceptions, and a request beside the memories it
    should use. Related texts are nearest by construction, so a check using it tests the pipeline, not retrieval."""
    name = 'oracle'

    def __init__(self, world):
        families = {}

        def axis(key):
            return families.setdefault(key, len(families))
        self.texts = {}
        for event in world.events.values():
            for text in {event['text'], event['say']} - {''}:
                self.texts[text] = [(axis('f:' + event['family']), 1.0), (axis('i:' + event['id']), .3)]
        for row in (*world.distractors.values(), *world.padding(max(PADS))):
            self.texts[row['text']] = [(axis('i:' + row['id']), 1.0)]
        for episode in world.episodes.values():
            self.texts[episode['summary']] = [(axis('i:' + episode['id']), 1.0)]
        own = {event['id']: axis('i:' + event['id']) for event in world.events.values()}
        own.update({episode['id']: axis('i:' + episode['id']) for episode in world.episodes.values()})
        for query in world.queries.values():
            self.texts[query['text']] = [(own[ident], 1.0) for ident in query['used']] or [(axis('q:' + query['id']), 1.0)]
        self.dim = len(families)

    def vectors(self, texts, role='document'):
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for column, weight in self.texts[text]:
                matrix[row, column] = weight
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1, norms)


class Unavailable:
    """A decision service that does not answer (JevService.ask's 'unavailable' receipt): what a host outage looks like."""
    def __init__(self):
        self.used = []

    def ask(self, purpose, owner, state, questions, rule, enrollment=None, attempt='', images=()):
        return {'id': '', 'status': 'unavailable', 'input_sha256': '', 'error': 'not configured', 'model': '', 'answers': {}, 'usage': {}}
