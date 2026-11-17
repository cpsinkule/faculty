"""The end-to-end report: what each arm kept, replaced and gave back, against the cases' written answers.

Built from stories.jsonl (ids, actions, probabilities), calls.jsonl (status, usage, timing) and probe.json. No instructor
sentence, memory text or request is read into it. The per-question tables (AUC, accuracy by threshold, agreement between the
two models) are `decisions_sandbox.report.write`'s, in report.md beside this file.
"""
import json
from pathlib import Path

from decisions_sandbox.report import PRICE, _pct, load

from .cases import KEPT, NEVER_KEPT, STATED, World

DEPTHS = (3, 5, 8, 12)


def write_counts(world, line):
    """How one arm's write path did, as counts against the expected action of every message."""
    counts = {key: 0 for key in ('lasting', 'lasting_kept', 'never_kept', 'false_saves', 'student', 'student_kept', 'student_refused',
                                 'wordname', 'wordname_refused', 'record', 'record_kept', 'restatements', 'duplicates_right',
                                 'updates', 'replaced_right', 'wrong_replacements', 'wrong_duplicates', 'exceptions', 'exceptions_kept',
                                 'scope_right', 'scope_judged', 'unavailable', 'fallbacks', 'calls', 'messages', 'max_calls')}
    for result in line['writes']:
        event = world.events[result['event']]
        category, expect, action = event['cat'], event['expect'], result['action']
        recognised = action in (*KEPT, 'duplicate')
        counts['messages'] += 1
        counts['calls'] += len(result.get('calls', []))
        counts['max_calls'] = max(counts['max_calls'], len(result.get('calls', [])))
        counts['unavailable'] += action == 'unavailable'
        counts['fallbacks'] += bool(result.get('fallback'))
        if category in STATED:
            counts['lasting'] += 1
            counts['lasting_kept'] += recognised
            if action in KEPT:
                counts['scope_judged'] += 1
                counts['scope_right'] += result.get('scope') == expect['scope']
        if category in NEVER_KEPT:
            counts['never_kept'] += 1
            counts['false_saves'] += recognised
        if category == 'student':
            counts['student'] += 1
            counts['student_kept'] += recognised
            counts['student_refused'] += action == 'refuse_student'
        if category == 'wordname':
            counts['wordname'] += 1
            counts['wordname_refused'] += action == 'refuse_student'
        if category == 'record_fact':
            counts['record'] += 1
            counts['record_kept'] += recognised
        if category == 'restate':
            counts['restatements'] += 1
            counts['duplicates_right'] += action == 'duplicate' and result['target'] == expect['duplicate_of']
        if category == 'update':
            counts['updates'] += 1
            counts['replaced_right'] += action == 'supersede' and result['target'] == expect['supersedes']
        if category == 'exception':
            counts['exceptions'] += 1
            counts['exceptions_kept'] += action == 'save'
        counts['wrong_replacements'] += action == 'supersede' and not (category == 'update' and result['target'] == expect['supersedes'])
        counts['wrong_duplicates'] += action == 'duplicate' and not (category == 'restate' and result['target'] == expect['duplicate_of'])
    expected = {ident for ident, row in world.oracle_memories().items() if row['status'] == 'current'}
    current = {ident for ident, status in line['final'].items() if status == 'current'}
    counts.update(store_expected=len(expected), store_missing=len(expected - current), store_extra=len(current - expected))
    return counts


def read_counts(world, reads, inject=5):
    """How a read path did over every request: where the right memories ranked, and what was given."""
    counts = {'requests': 0, 'with_used': 0, 'used': 0, 'given': 0, 'given_right': 0, 'given_wrong': 0, 'given_maybe': 0,
              'forbidden_given': 0, 'used_given': 0, 'used_given_any': 0, 'nothing_requests': 0, 'nothing_given': 0, 'chars': 0,
              'calls': 0, 'visible': 0, 'fallbacks': 0, **{f'ranked_{depth}': 0 for depth in DEPTHS}}
    for result in reads:
        query = world.queries[result['query']]
        used, maybe, forbidden = set(query['used']), set(query['maybe']), set(query['not'])
        given, everything = result['injected'][:inject], set(result.get('passed', result['injected']))
        counts['requests'] += 1
        counts['calls'] += result.get('calls', 0)
        counts['visible'] += result['visible']
        counts['chars'] += result['chars']
        counts['fallbacks'] += bool(result.get('fallback'))
        counts['given'] += len(given)
        counts['given_right'] += len(used & set(given))
        counts['given_maybe'] += len(maybe & set(given))
        counts['given_wrong'] += len(set(given) - used - maybe)
        counts['forbidden_given'] += len(forbidden & everything)
        if used:
            counts['with_used'] += 1
            counts['used'] += len(used)
            counts['used_given'] += len(used & set(given))
            counts['used_given_any'] += len(used & everything)
            for depth in DEPTHS:
                counts[f'ranked_{depth}'] += len(used & set(result['ranked'][:depth]))
        else:
            counts['nothing_requests'] += 1
            counts['nothing_given'] += len(set(given) - maybe)
    return counts


def breadth_counts(world, reads):
    """Where the right memories ranked, split by how many a request needs: one or two (a lookup), or five and more (a draft
    that every standing preference bears on). [found in the nearest eight, needed] for each."""
    found = {'narrow': [0, 0], 'broad': [0, 0]}
    for result in reads:
        used = set(world.queries[result['query']]['used'])
        size = 'narrow' if 0 < len(used) <= 2 else 'broad' if len(used) >= 5 else ''
        if size:
            found[size][0] += len(used & set(result['ranked'][:8]))
            found[size][1] += len(used)
    return found


def wrong_kinds(world, reads, inject=5):
    """What the wrongly given memories were: a past-work summary on the same topic is the arguable kind of wrong."""
    kinds = {'past-work summary': 0, 'neutral memory': 0, 'other statement': 0}
    for result in reads:
        query = world.queries[result['query']]
        for ident in result['injected'][:inject]:
            if ident not in query['used'] and ident not in query['maybe']:
                kinds['past-work summary' if ident[0] == 'E' else 'neutral memory' if ident[0] in 'DZ' else 'other statement'] += 1
    return kinds


def step_seconds(line, calls):
    """Seconds of decision requests per instructor message and per later request (the step's requests one after another), as
    (p50, p95), for the messages and requests that asked anything. Embedding time is in the probe, not here."""
    def spent(keys):
        return sum(calls[key].get('seconds') or 0 for key in keys if key in calls)
    writes = sorted(spent(result['calls']) for result in line['writes'] if result.get('calls'))
    reads = sorted(spent(result['keys']) for result in (line['reads'].get('oracle-0') or line['reads'].get('own') or []) if result.get('keys'))
    return {'write': (_pct(writes, .5), _pct(writes, .95)), 'read': (_pct(reads, .5), _pct(reads, .95))}


def _share(part, whole):
    return f'{part}/{whole}' if whole else '–'


def usage(folder):
    """Per model and purpose: requests sent, tokens, dollars at the published prices, seconds p50 and p95."""
    found = {}
    for entry in load(Path(folder) / 'calls.jsonl'):
        row = found.setdefault((entry['mode'], entry['purpose']), {'sent': 0, 'tokens': 0, 'seconds': [], 'not_complete': 0})
        row['sent'] += 1
        row['not_complete'] += entry['status'] != 'complete'
        row['tokens'] += int((entry.get('usage') or {}).get('input_tokens') or 0)
        if entry.get('seconds') is not None and entry['status'] == 'complete':
            row['seconds'].append(entry['seconds'])
    return found


def _name(line):
    gates = '' if line.get('gate_set') in (None, 'starting', 'shipped', 'proposed') else line['gate_set']

    embedder = 'keywords only' if line['embedder'] == 'none' else line['embedder']
    arm = 'Jev-Mem ' + line['config'].split('-')[0] if line['arm'] == 'jevmem' else line['arm']
    return ' · '.join(part for part in (arm, line['mode'], embedder, line['variant'], gates) if part and part != 'none')


def write(folder, world=None):
    folder = Path(folder)
    world = world or World()
    lines = load(folder / 'stories.jsonl')
    out = ['# Assistant memory sandbox', '',
           f'Invented cases only: {len(world.events)} instructor messages, {len(world.queries)} later requests, '
           f'{len(world.distractors)} neutral memories, {sum(1 for e in world.episodes.values() if e["worth"])} past-work summaries. '
           'Ids and numbers only; no sentence is in this report.', '']
    summary = {'writes': {}, 'reads': {}, 'usage': {}}
    probe = folder / 'probe.json'
    if probe.exists():
        found = json.loads(probe.read_text())
        out += ['## Embedders on this machine (CPU)', '', '| embedder | loads in | first vector | short query p50 / p95 | 32 documents | memory | on disk |',
                '|---|---|---|---|---|---|---|']
        for name, row in found.get('models', {}).items():
            if row.get('error'):
                out.append(f"| {name} | did not run: {row['error']} | | | | | |")
                continue
            out.append(f"| {name} | {row['load_seconds']:.1f} s | {row['first_ms']:.0f} ms | {row['query_p50_ms']:.0f} / {row['query_p95_ms']:.0f} ms | "
                       f"{row['batch32_ms']:.0f} ms | {row['rss_mb']:.0f} MB | {row['disk_mb']:.0f} MB |")
        if found.get('search'):
            out += ['', 'Comparing one query with every kept vector in numpy: ' + ', '.join(
                f"{rows} rows {ms:.2f} ms" for rows, ms in found['search'].items()) + '.']
        out.append('')
        summary['probe'] = found
    used = usage(folder)
    if used:
        out += ['## Requests to the decision models', '', '| model | purpose | sent | not complete | tokens | cost | seconds p50 / p95 |', '|---|---|---|---|---|---|---|']
        for (mode, purpose), row in sorted(used.items()):
            seconds = sorted(row['seconds'])
            cost = row['tokens'] * PRICE.get(mode, 0) / 1e6
            out.append(f"| {mode} | {purpose} | {row['sent']} | {row['not_complete']} | {row['tokens']} | ${cost:.3f} | "
                       f"{_pct(seconds, .5) or 0:.2f} / {_pct(seconds, .95) or 0:.2f} |")
            summary['usage'][f'{mode}/{purpose}'] = {'sent': row['sent'], 'tokens': row['tokens'], 'cost': round(cost, 4),
                                                     'p50': _pct(seconds, .5), 'p95': _pct(seconds, .95)}
        out.append('')
    if lines:
        out += ['## What each arm kept', '',
                'Columns: lasting statements recognised; passing remarks, quoted or pasted text kept (must be 0); student statements '
                'kept (must be 0); word-name controls wrongly refused; restatements recognised; updates that replaced the right memory; '
                'wrong replacements; statements wrongly dropped as duplicates; exceptions kept beside the rule; scope right; '
                'final store against the expected one (missing, extra); requests per message (mean, most).', '',
                '| arm | lasting | false saves | student kept | word-names refused | duplicates | replaced | wrong repl. | wrong dup. | exceptions | scope | store −/+ | calls |',
                '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
        for line in sorted(lines, key=_name):
            c = write_counts(world, line)
            summary['writes'][_name(line)] = c
            # Jev-Mem is told each message's owner, and its requests are counted from its own log, per observation it was fed.
            scope = 'given' if line.get('scope_given') else _share(c['scope_right'], c['scope_judged'])
            calls = (f"{line['write_requests'] / max(1, line['observations']):.2f}" if 'write_requests' in line
                     else f"{c['calls'] / max(1, c['messages']):.2f}, {c['max_calls']}")
            out.append(f"| {_name(line)} | {_share(c['lasting_kept'], c['lasting'])} | {_share(c['false_saves'], c['never_kept'])} | "
                       f"{_share(c['student_kept'], c['student'])} | {_share(c['wordname_refused'], c['wordname'])} | "
                       f"{_share(c['duplicates_right'], c['restatements'])} | {_share(c['replaced_right'], c['updates'])} | {c['wrong_replacements']} | "
                       f"{c['wrong_duplicates']} | {_share(c['exceptions_kept'], c['exceptions'])} | {scope} | "
                       f"{c['store_missing']}/{c['store_extra']} | {calls} |")
        out += ['', '## What each arm gave back', '',
                'Columns: of the memories a request should use, how many were among the nearest 3, 5, 8 and 12; of those given to the '
                'model (five at most), how many were right, arguable, wrong; memories that must not be used that were given; '
                'memories given for requests that need none; characters added per request; requests per request.', '',
                '| arm | store | in top 3 / 5 / 8 / 12 | given right / arguable / wrong | forbidden | for nothing | chars | calls |',
                '|---|---|---|---|---|---|---|---|']
        for line in sorted(lines, key=_name):
            for store, reads in sorted(line['reads'].items()):
                c = read_counts(world, reads)
                summary['reads'][f'{_name(line)} · {store}'] = c
                ranked = ' / '.join(_share(c[f'ranked_{depth}'], c['used']) for depth in DEPTHS)
                out.append(f"| {_name(line)} | {store} | {ranked} | {c['given_right']} / {c['given_maybe']} / {c['given_wrong']} | "
                           f"{c['forbidden_given']} | {c['nothing_given']} | {c['chars'] / max(1, c['requests']):.0f} | "
                           f"{c['calls'] / max(1, c['requests']):.2f} |")
        calls = {entry['key']: entry for entry in load(folder / 'calls.jsonl')}
        out += ['', '## Narrow and broad requests, wrong inclusions, and time', '',
                'On the expected store. Narrow: a request that needs one or two memories. Broad: one that needs five or more (a draft '
                'every standing preference bears on). Seconds are the decision requests of one step, one after another.', '',
                '| arm | narrow: in the nearest 8 | broad: in the nearest 8 | wrongly given: past-work / neutral / other | seconds per message p50 / p95 | per request p50 / p95 |',
                '|---|---|---|---|---|---|']
        for line in sorted(lines, key=_name):
            reads = line['reads'].get('oracle-0') or line['reads'].get('own')
            if not reads or line['arm'] in ('today', 'flat-decision-down'):
                continue
            b, k, t = breadth_counts(world, reads), wrong_kinds(world, reads), step_seconds(line, calls)
            summary.setdefault('detail', {})[_name(line)] = {'breadth': b, 'wrong': k, 'seconds': t}
            time = lambda pair: '–' if pair[0] is None else f'{pair[0]:.2f} / {pair[1]:.2f}'  # noqa: E731
            out.append(f"| {_name(line)} | {_share(*b['narrow'])} | {_share(*b['broad'])} | {k['past-work summary']} / {k['neutral memory']} / "
                       f"{k['other statement']} | {time(t['write'])} | {time(t['read'])} |")
        out.append('')
    lessons = folder / 'lessons-recall.json'

    if lessons.exists():
        found = json.loads(lessons.read_text())
        out += ['## Finding the right lesson for a student request', '',
                'Of the requests that have a matching lesson, how many had it among the first 1, 4 and 8 candidates.', '',
                '| search | first | top 4 | top 8 |', '|---|---|---|---|']
        out += [f"| {name} | {row['1']}/{row['of']} | {row['4']}/{row['of']} | {row['8']}/{row['of']} |" for name, row in found.items()]
        out.append('')
        summary['lessons'] = found
    (folder / 'memory-report.md').write_text('\n'.join(out) + '\n')
    (folder / 'memory-summary.json').write_text(json.dumps(summary, indent=1, default=str))
    return folder / 'memory-report.md'
