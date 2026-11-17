"""Proposed gates, as counts. Reads results-components.jsonl (probabilities and ids only) and, for each model, tries every
gate on the `fit` families, then reports the chosen gates on the held-out `test` families.

The rule, in order: no forbidden outcome (a passing remark kept, a student's statement kept, a memory wrongly replaced or
dropped as a duplicate); then the most wanted outcomes; a tie goes to the stricter gate. Nothing here decides anything: the
instructor chooses the gates.
"""
import itertools
import json
from pathlib import Path

from . import questions
from .cases import NEVER_KEPT, STATED

MODES = ('jev', 'decisions')
LEVELS = (.5, .6, .7, .8, .9, .95, .98)
STUDENT = (.1, .2, .3, .5, .7)
# On a tie the earlier rule wins: a certain roster match is trusted before the model is trusted alone.
PREFERRED = ('strong', 'either', 'model', 'code', 'both')


def rows(folder, check):
    path = Path(folder) / 'results-components.jsonl'
    if not path.exists():
        return []
    return [row for row in map(json.loads, filter(None, path.read_text().splitlines())) if row['check'] == check]


def _side(row, mode):
    result = row['results'].get(mode) or {}
    return result.get('outcome') or {}, result.get('p') or {}


def capture_counts(found, mode, gates, policy, split=None):
    counts = {'lasting_kept': 0, 'lasting': 0, 'false_saves': 0, 'never_kept': 0, 'student_kept': 0, 'student': 0,
              'student_refused': 0, 'wordname_refused': 0, 'wordname': 0, 'record_seen': 0, 'record_kept': 0, 'record': 0,
              'scope_right': 0, 'scope_narrower': 0, 'scope_wider': 0, 'unanswered': 0}
    reach = {'term': 0, 'course': 1, 'workspace': 2}
    for row in found:
        meta = row['meta']
        if split and meta['split'] != split:
            continue
        outcome, p = _side(row, mode)
        action, scope = questions.decide_capture(outcome, p, gates, meta['named_in_code'], policy)
        counts['unanswered'] += action == 'unavailable'
        category = meta['category']
        if category in STATED:
            counts['lasting'] += 1
            counts['lasting_kept'] += action == 'candidate'
            wanted = (row['labels'].get('scope') or {}).get('value')
            if action == 'candidate' and wanted:
                counts['scope_right'] += scope == wanted
                counts['scope_narrower'] += reach[scope] < reach[wanted]
                counts['scope_wider'] += reach[scope] > reach[wanted]
        if category in NEVER_KEPT:
            counts['never_kept'] += 1
            counts['false_saves'] += action in ('candidate', 'record')
        if category == 'student':
            counts['student'] += 1
            counts['student_kept'] += action in ('candidate', 'record')
            counts['student_refused'] += action == 'refuse_student'
        if category == 'wordname':
            counts['wordname'] += 1
            counts['wordname_refused'] += action == 'refuse_student'
        if category == 'record_fact':
            counts['record'] += 1
            counts['record_seen'] += action == 'record'
            counts['record_kept'] += action == 'candidate'
    return counts


def capture(found, mode):
    """Gates for the capture request. A statement kept wider than it was meant counts against a gate as a forbidden outcome
    does not, but before recall: a term's fact kept for every term is the scope mistake that lasts."""
    best = None
    for lasting, own, student, scope, policy in itertools.product(LEVELS, LEVELS, STUDENT, (.3, .5, .6, .8, .9), PREFERRED):
        gates = {**questions.GATES, 'lasting': lasting, 'own_words': own, 'student': student, 'scope': scope}
        counts = capture_counts(found, mode, gates, policy, 'fit')
        forbidden = counts['false_saves'] + counts['student_kept']
        wanted = counts['lasting_kept'] - counts['wordname_refused']
        rank = (-forbidden, wanted, -counts['scope_wider'], counts['scope_right'], (lasting, own, -student, scope))
        if best is None or rank > best[0]:
            best = (rank, gates, policy)
    _, gates, policy = best
    return {'gates': {name: gates[name] for name in ('lasting', 'own_words', 'student', 'scope')}, 'student_policy': policy,
            'fit': capture_counts(found, mode, gates, policy, 'fit'), 'test': capture_counts(found, mode, gates, policy, 'test'),
            'starting_gates_all': capture_counts(found, mode, questions.GATES, 'strong'),
            'by_policy_test': {name: capture_counts(found, mode, gates, name, 'test') for name in questions.STUDENT_POLICIES}}


def pair_verdict(p, gates):
    """(verdict, whether it is about the statement the case is about) from the stored answers. Other candidates' `narrows` is
    not kept, so a high `replaces_other` always counts as a wrong replacement: the cautious reading."""
    same, same_other = p.get('same', 0), p['same_other']
    if max(same, same_other) >= gates['same']:
        return 'duplicate', same >= same_other
    if p.get('replaces', 0) >= gates['replaces'] and p.get('narrows', 0) < gates['narrows']:
        return 'supersede', True
    if p['replaces_other'] >= gates['replaces']:
        return 'supersede', False
    return 'save', True


def pair_counts(found, mode, gates, split=None):
    counts = {'duplicates_right': 0, 'restatements': 0, 'replaced_right': 0, 'updates': 0, 'wrong_replacements': 0,
              'wrong_duplicates': 0, 'exceptions_kept': 0, 'exceptions': 0, 'new_kept': 0, 'new': 0, 'unanswered': 0}
    for row in found:
        meta = row['meta']
        if split and meta['split'] != split:
            continue
        outcome, p = _side(row, mode)
        if outcome.get('status') != 'complete':
            counts['unanswered'] += 1
            continue
        verdict, on_true = pair_verdict(p, gates)
        relation = meta['relation']
        counts['restatements'] += relation == 'same'
        counts['updates'] += relation == 'replaces'
        counts['exceptions'] += relation == 'narrows'
        counts['new'] += relation == ''
        counts['duplicates_right'] += relation == 'same' and verdict == 'duplicate' and on_true
        counts['replaced_right'] += relation == 'replaces' and verdict == 'supersede' and on_true
        counts['exceptions_kept'] += relation == 'narrows' and verdict == 'save'
        counts['new_kept'] += relation == '' and verdict == 'save'
        counts['wrong_replacements'] += verdict == 'supersede' and not (relation == 'replaces' and on_true)
        counts['wrong_duplicates'] += verdict == 'duplicate' and not (relation == 'same' and on_true)
    return counts


def pairs(found, mode):
    best = None
    for same, replaces, narrows in itertools.product(LEVELS, LEVELS, (.3, .5, .7)):
        gates = {**questions.GATES, 'same': same, 'replaces': replaces, 'narrows': narrows}
        counts = pair_counts(found, mode, gates, 'fit')
        rank = (-(counts['wrong_replacements'] + counts['wrong_duplicates']), counts['duplicates_right'] + counts['replaced_right'],
                (same, replaces, -narrows))
        if best is None or rank > best[0]:
            best = (rank, gates)
    gates = best[1]
    return {'gates': {name: gates[name] for name in ('same', 'replaces', 'narrows')},
            'fit': pair_counts(found, mode, gates, 'fit'), 'test': pair_counts(found, mode, gates, 'test'),
            'starting_gates_all': pair_counts(found, mode, questions.GATES)}


def relevance_counts(found, mode, gate, split=None):
    counts = {'used_given': 0, 'used': 0, 'others_given': 0, 'others': 0, 'unanswered': 0}
    for row in found:
        if split and row['meta']['split'] != split:
            continue
        outcome, p = _side(row, mode)
        if outcome.get('status') != 'complete':
            counts['unanswered'] += 1
            continue
        for ident in row['meta']['candidates']:
            given = p['use:' + ident] >= gate
            if ident in row['meta']['used']:
                counts['used'] += 1
                counts['used_given'] += given
            else:
                counts['others'] += 1
                counts['others_given'] += given
    return counts


def _f1(counts):
    hit, miss, extra = counts['used_given'], counts['used'] - counts['used_given'], counts['others_given']
    return 2 * hit / (2 * hit + miss + extra) if hit else 0.0


def relevance(found, mode):
    """The gate with the best balance of memories used and others let in (F1 on the fit families); the higher gate on a tie."""
    gate = max(LEVELS + (.3, .4), key=lambda g: (_f1(relevance_counts(found, mode, g, 'fit')), g))
    return {'gates': {'use': gate}, 'fit': relevance_counts(found, mode, gate, 'fit'), 'test': relevance_counts(found, mode, gate, 'test'),
            'by_gate_all': {f'{g:.2f}': relevance_counts(found, mode, g) for g in sorted(LEVELS + (.3, .4))}}


PARTS = {'capture': 'memory_capture', 'capture_v2': 'memory_capture_v2', 'pairs_gold': 'memory_pair_gold', 'pairs_raw': 'memory_pair_raw',
         'pairs_gold_single': 'memory_pair_gold_single', 'pairs_raw_single': 'memory_pair_raw_single',
         'relevance': 'memory_relevance', 'relevance_single': 'memory_relevance_single'}


def proposed(folder, capture=1, single=()):
    """{mode: {gates for the pipeline, student_policy}} from a finished sweep, for the capture wording and the request form
    (`single`: the modes asked about one kept memory at a time) the pipeline will use; {} before a sweep."""
    path = Path(folder) / 'calibration-memory.json'
    if not path.exists():
        return {}
    found = json.loads(path.read_text())
    chosen = {}
    name = 'capture' if capture == 1 else f'capture_v{capture}'
    for mode in found:
        tail = '_single' if mode in single else ''
        parts = (name, 'pairs_gold' + tail, 'relevance' + tail)
        if any(part not in found[mode] for part in parts):
            raise SystemExit(f'The sweep has no {mode} gates for {parts}: run those component checks first.')
        chosen[mode] = {'gates': {k: v for part in parts for k, v in found[mode][part]['gates'].items()},
                        'student_policy': found[mode][parts[0]]['student_policy']}
    if {'jev', 'decisions'} <= set(found):
        # Each judgment's gates from the model `stories.MIXED` sends it to.
        tail = '_single' if 'decisions' in single else ''
        chosen['mixed'] = {'gates': {**found['jev'][name]['gates'], **found['decisions']['pairs_gold' + tail]['gates'],
                                     **found['jev']['relevance' + ('_single' if 'jev' in single else '')]['gates']},
                           'student_policy': found['jev'][name]['student_policy']}
    return chosen


def write(folder):
    folder = Path(folder)
    result = {}
    kinds = {'capture': capture, 'pairs': pairs, 'relevance': relevance}
    for mode in MODES:
        for part, check in PARTS.items():
            found = rows(folder, check)
            if any(mode in row['results'] for row in found):
                result.setdefault(mode, {})[part] = kinds[part.split('_')[0]](found, mode)
    (folder / 'calibration-memory.json').write_text(json.dumps(result, indent=1))
    lines = ['# Memory gates proposed from the invented cases', '',
             'Fitted on the fit families, reported on the held-out test families. Counts only. "v2" is the capture wording revised '
             'after the first live answers; "one at a time" asks about each kept memory in a request of its own.', '']
    titles = {'capture': 'Capture, first wording', 'capture_v2': 'Capture, v2 wording', 'pairs_gold': 'Consolidate, standalone sentence',
              'pairs_raw': 'Consolidate, sentence as typed', 'pairs_gold_single': 'Consolidate, standalone sentence, one at a time',
              'pairs_raw_single': 'Consolidate, sentence as typed, one at a time', 'relevance': 'Relevance',
              'relevance_single': 'Relevance, one at a time'}
    for mode, found in result.items():
        lines += [f'## {mode}', '']
        for part, q in found.items():
            if part.startswith('capture'):
                held = q['test']
                lines += [f"**{titles[part]}** gates {q['gates']}, student rule `{q['student_policy']}`.", '', '| | fit | held out |', '|---|---|---|']
                for name, of, title in (('lasting_kept', 'lasting', 'lasting statements recognised'), ('false_saves', 'never_kept', 'passing remarks or quoted text kept'),
                                        ('student_kept', 'student', 'student statements kept'), ('student_refused', 'student', 'student statements refused by name'),
                                        ('wordname_refused', 'wordname', 'word-name controls wrongly refused'), ('record_seen', 'record', 'record-like facts recognised as records'),
                                        ('record_kept', 'record', 'record-like facts that would be kept'),
                                        ('scope_right', 'lasting_kept', 'kept under the right owner'), ('scope_narrower', 'lasting_kept', 'kept narrower than meant'),
                                        ('scope_wider', 'lasting_kept', 'kept wider than meant')):
                    lines.append(f"| {title} | {q['fit'][name]} of {q['fit'][of]} | {held[name]} of {held[of]} |")
                lines += ['', 'Held-out student statements kept, by rule: ' + ', '.join(
                    f"{name} {counts['student_kept']} (word-names refused {counts['wordname_refused']})" for name, counts in q['by_policy_test'].items()), '']
            elif part.startswith('pairs'):
                lines += [f"**{titles[part]}** gates {q['gates']}.", '', '| | fit | held out |', '|---|---|---|']
                for name, of, label in (('duplicates_right', 'restatements', 'restatements recognised'), ('replaced_right', 'updates', 'updates that replace the right memory'),
                                        ('exceptions_kept', 'exceptions', 'exceptions kept beside the rule'), ('new_kept', 'new', 'new statements kept'),
                                        ('wrong_replacements', None, 'wrong replacements'), ('wrong_duplicates', None, 'statements wrongly dropped as duplicates')):
                    tail = (lambda counts: f" of {counts[of]}") if of else (lambda counts: '')
                    lines.append(f"| {label} | {q['fit'][name]}{tail(q['fit'])} | {q['test'][name]}{tail(q['test'])} |")
                lines.append('')
            else:
                lines += [f"**{titles[part]}** gate {q['gates']['use']}.", '', '| gate | memories a request should use, given | other memories given |', '|---|---|---|']
                lines += [f"| {gate} | {counts['used_given']} of {counts['used']} | {counts['others_given']} of {counts['others']} |"
                          for gate, counts in q['by_gate_all'].items()]
                lines.append('')
    (folder / 'calibration-memory.md').write_text('\n'.join(lines) + '\n')
    return folder / 'calibration-memory.md'
