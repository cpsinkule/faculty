"""The comparison report: built only from results.jsonl (outcomes, probabilities, ids) and calls.jsonl (status, usage, timing).
No message text, sentence, title or summary is read into it, so none can be written out. It has one column for each of
`MODES` the run folder holds answers of: Jev, Decisions and, since 2026-10-13, Snap, then Microsoft-Decision-1, Jev 1.14, Jev 1.13 with
its opt-in features, Liquid d1-ultra and Gemini Decision 1."""
import csv
import json
import math
from collections import defaultdict

GRID = (.05, .1, .2, .3, .5, .7, .8, .9, .95)
PRICE = {'jev': .042, 'decisions': .10, 'snap': .05, 'microsoft': .042, 'jev114': .042, 'jev113x': .042, 'liquid': .042, 'gemini': .10, 'google': .10, 'googleultra': .10}  # $ per million input tokens; output is free on all
# The thresholds Faculty's own code uses for a probability, by check and label (record-only checks: .5).
SITE = {'wellbeing': (.30,), 'absence': (.9,), 'makeup': (.9,), 'office_hours': (.9,), 'grading': (.9,), 'materials': (.9,),
        'dates': (.8,), 'reason': (.8,), 'agreed': (.8,), 'owed': (.05, .9), 'work_owed': (.05, .9), 'first_date': (.9,), 'last_date': (.9,), 'details': (.9,),
        'conflict': (.5,), 'asks': (.8,), 'answered': (.3, .7), 'attached': (.7,)}
MODES = ('jev', 'decisions', 'snap', 'microsoft', 'jev114', 'jev113x', 'liquid', 'gemini', 'google', 'googleultra')
NAMES = {'jev': 'Jev', 'decisions': 'Decisions', 'snap': 'Snap', 'microsoft': 'Microsoft', 'jev114': 'Jev 1.14',
         'jev113x': 'Jev 1.13 opt-in', 'liquid': 'Liquid d1-ultra', 'gemini': 'Gemini Decision 1',
         'google': 'Gemini (Google)', 'googleultra': 'Gemini (Google, ultra)'}


def load(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def auc(pairs):
    positives = [p for p, y in pairs if y == 1]
    negatives = [p for p, y in pairs if y == 0]
    if not positives or not negatives:
        return None
    wins = sum(1.0 if a > b else .5 if a == b else 0.0 for a in positives for b in negatives)
    return wins / (len(positives) * len(negatives))


def brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def accuracy(pairs, threshold):
    return sum((p >= threshold) == bool(y) for p, y in pairs) / len(pairs) if pairs else None


def _fmt(value, digits=2):
    if value is None:
        return '–'
    if isinstance(value, float):
        return f'{value:.{digits}f}'
    return str(value)


def _binary(value):
    return type(value) is int and value in (0, 1)


def _scope(strength):
    return 'invented' if strength == 'truth' else 'real'


def summarize(lines, calls):
    checks = defaultdict(lambda: {'cases': defaultdict(int), 'status': {m: defaultdict(int) for m in MODES}, 'errors': {m: 0 for m in MODES},
                                  'agree': defaultdict(lambda: defaultdict(lambda: [0, 0])),
                                  'prob': defaultdict(lambda: {m: [] for m in MODES}),
                                  'cat': defaultdict(lambda: {m: [0, 0] for m in MODES}), 'stable': {m: [0, 0] for m in MODES},
                                  'stable_dp': {m: [] for m in MODES}, 'orders': {m: [0, 0] for m in MODES}, 'disagree': [],
                                  'strata': defaultdict(lambda: defaultdict(list)),
                                  # The scoreboard: [right, answered, asked] of every labeled answer, a missing one not right,
                                  # by (invented or real, outcome or yes/no).
                                  'board': defaultdict(lambda: {m: [0, 0, 0] for m in MODES})})
    for line in lines:
        c = checks[line['check']]
        c['cases'][line['source']] += 1
        results = line['results']
        stratum = (line.get('meta') or {}).get('stratum')
        if stratum:
            for mode, result in results.items():
                for name, p in result['p'].items():
                    if p is not None:
                        c['strata'][(stratum, mode)][name].append(p)
        for mode, result in results.items():
            c['status'][mode][result['outcome'].get('status', 'error' if result.get('error') else 'n/a')] += 1
            c['errors'][mode] += bool(result.get('error'))
            for field, value in result['outcome'].items():
                if field.endswith('orders_agree') and value is not None:
                    c['orders'][mode][0] += bool(value)
                    c['orders'][mode][1] += 1
            repeat = result.get('repeat')
            if repeat and not result.get('error') and not repeat.get('error'):
                c['stable'][mode][0] += repeat['outcome'] == result['outcome']
                c['stable'][mode][1] += 1
                for name, p in result['p'].items():
                    q = repeat['p'].get(name)
                    if p is not None and q is not None:
                        c['stable_dp'][mode].append(abs(p - q))
        compared = [m for m in MODES if m in results]
        differs = set()
        for position, a in enumerate(compared):
            for b in compared[position + 1:]:
                first, second = results[a]['outcome'], results[b]['outcome']
                for field in sorted(set(first) | set(second)):
                    if field == 'status' or field.endswith('_status'):
                        continue
                    x, y = first.get(field), second.get(field)
                    if x is None and y is None:
                        continue
                    c['agree'][(a, b)][field][0] += x == y
                    c['agree'][(a, b)][field][1] += 1
                    if x != y:
                        differs.add(field)
        if differs:
            fields = sorted(differs)
            c['disagree'].append({'check': line['check'], 'case': line['id'], 'source': line['source'], 'fields': fields,
                                  **{m: {f: results[m]['outcome'].get(f) for f in fields} for m in compared},
                                  'labels': {k: v['value'] for k, v in line['labels'].items()}, 'meta': line['meta']})
        for name, wanted in line['labels'].items():
            scope = _scope(wanted['strength'])
            for mode, result in results.items():
                if _binary(wanted['value']) and name in result['p']:
                    p = result['p'][name]
                    if p is not None:
                        c['prob'][(name, scope)][mode].append((p, wanted['value'], wanted['strength']))
                elif name in result['outcome']:
                    got = result['outcome'][name]
                    c['cat'][(name, scope)][mode][0] += got == wanted['value']
                    c['cat'][(name, scope)][mode][1] += 1
                if _binary(wanted['value']):
                    p = result['p'].get(name)
                    tally, right = c['board'][(scope, 'yes/no')][mode], p is not None and (p >= .5) == bool(wanted['value'])
                    answered = p is not None
                else:
                    tally, answered = c['board'][(scope, 'outcome')][mode], name in result['outcome']
                    right = answered and result['outcome'][name] == wanted['value']
                tally[0] += right
                tally[1] += answered
                tally[2] += 1
    usage = defaultdict(lambda: {m: {'sent': 0, 'replayed': 0, 'tokens': 0, 'seconds': [], 'status': defaultdict(int), 'gaps': defaultdict(int)}
                                 for m in MODES})
    by_key = {}
    for entry in calls:
        by_key[entry['key']] = entry
    owners = {}
    for line in lines:
        for result in line['results'].values():
            for key in result.get('calls', []) + (result.get('repeat') or {}).get('calls', []):
                owners.setdefault(key, line['check'])
    for key, entry in by_key.items():
        check = owners.get(key, entry.get('purpose', '?'))
        u = usage[check][entry['mode']]
        u['status'][entry['status']] += 1
        if entry.get('replayed'):
            u['replayed'] += 1
            continue
        if entry['status'] in ('complete', 'failed', 'uncertain'):
            u['sent'] += 1
        u['tokens'] += int((entry.get('usage') or {}).get('input_tokens') or 0)
        if entry.get('seconds') is not None and entry['status'] == 'complete':
            u['seconds'].append(entry['seconds'])
        for gap in (entry.get('gaps') or {}):
            u['gaps'][gap] += 1
    return checks, usage


def _pct(sorted_values, q):
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(math.ceil(q * len(sorted_values))) - 1)]


def _row(head, cells):
    return f'| {head} | ' + ' | '.join(str(cell) for cell in cells) + ' |'


def _tally(right, answered, asked):
    if not asked:
        return '–'
    return f'{right}/{asked}' + (f' ({asked - answered} without an answer)' if answered < asked else '')


def write(folder):
    lines = [line for path in sorted(folder.glob('results*.jsonl')) for line in load(path)]
    calls = load(folder / 'calls.jsonl')
    checks, usage = summarize(lines, calls)
    shown = [m for m in MODES if any(m in line['results'] for line in lines) or any(e.get('mode') == m for e in calls)] or list(MODES[:2])
    heads = [NAMES[m] for m in shown]
    rule = '|---|' + '---|' * len(shown)
    out = ['# Decision models side by side: ' + ', '.join(heads), '',
           f'Cases: {len(lines)}. Requests recorded: {len(calls)}. Built only from outcomes, probabilities, ids and call receipts; '
           'no message text is in this report.', '',
           'Strength of a real label: strong = your own change; medium = an approved card or the Assistant\'s change; '
           'weak = an indirect sign. Invented cases carry their written answer ("truth").', '']
    out += ['## Scoreboard', '', 'Labeled answers each model got right, of the labeled cases it ran; one without an answer (the model '
            'failed, or Faculty\'s code settled it without asking) is not right. '
            '"Outcome" is what Faculty\'s own code decided from the answer; "yes/no" reads a probability of .5 or more as yes.', '',
            _row('check', ['labels', *heads]), '|---|---|' + rule[5:]]
    totals = defaultdict(lambda: {m: [0, 0, 0] for m in shown})
    for name in sorted(checks):
        for key in sorted(checks[name]['board']):
            board = checks[name]['board'][key]
            if not any(board[m][2] for m in shown):
                continue
            out.append(_row(name, [f'{key[1]}, {key[0]}', *(_tally(*board[m]) for m in shown)]))
            for m in shown:
                totals[key][m] = [a + b for a, b in zip(totals[key][m], board[m])]
    for key in sorted(totals):
        out.append(_row('**all checks**', [f'{key[1]}, {key[0]}', *(_tally(*totals[key][m]) for m in shown)]))
    out.append('')
    summary = {}
    for name in sorted(checks):
        c = checks[name]
        out += [f'## {name}', '', 'Cases: ' + ', '.join(f'{k} {v}' for k, v in sorted(c['cases'].items())), '']
        out += [_row('', heads), rule]
        for mode_status in sorted({status for m in shown for status in c['status'][m]}):
            out.append(_row(f'status {mode_status}', [c['status'][m].get(mode_status, 0) for m in shown]))
        out.append(_row('case errors', [c['errors'][m] for m in shown]))
        u = usage.get(name)
        if u:
            for mode in MODES:
                seconds = sorted(u[mode]['seconds'])
                u[mode]['p50'], u[mode]['p95'] = _pct(seconds, .5), _pct(seconds, .95)
            out.append(_row('requests sent (replayed)', [f"{u[m]['sent']}" + (f" ({u[m]['replayed']})" if u[m]['replayed'] or m == 'jev' else '')
                                                         for m in shown]))
            out.append(_row('seconds p50 / p95', [f"{_fmt(u[m]['p50'])} / {_fmt(u[m]['p95'])}" for m in shown]))
            out.append(_row('input tokens (cost)', [f"{u[m]['tokens']} (${u[m]['tokens'] * PRICE[m] / 1e6:.4f})" for m in shown]))
            if any(u[m]['gaps'] for m in shown):
                out.append(_row('answer gaps', [', '.join(f'{k} {v}' for k, v in sorted(u[m]['gaps'].items())) for m in shown]))
        if any(c['orders'][m][1] for m in shown):
            out.append(_row('both option orders agree', [f"{c['orders'][m][0]}/{c['orders'][m][1]}" for m in shown]))
        if any(c['stable'][m][1] for m in shown):
            out.append(_row('repeat: same outcome', [f"{c['stable'][m][0]}/{c['stable'][m][1]}" for m in shown]))
            out.append(_row('repeat: mean |Δp|', [_fmt(sum(c['stable_dp'][m]) / len(c['stable_dp'][m]) if c['stable_dp'][m] else None, 3)
                                                  for m in shown]))
        out.append('')
        pairs_shown = [(a, b) for (a, b) in sorted(c['agree'], key=lambda pair: (MODES.index(pair[0]), MODES.index(pair[1])))
                       if a in shown and b in shown]
        if pairs_shown:
            fields = sorted({field for pair in pairs_shown for field in c['agree'][pair]})
            out += ['Agreement of the site\'s outcome between models (same / compared):', '',
                    _row('outcome', [f'{NAMES[a]} and {NAMES[b]}' for a, b in pairs_shown]), '|---|' + '---|' * len(pairs_shown)]
            out += [_row(field, ['/'.join(map(str, c['agree'][pair][field])) if field in c['agree'][pair] else '–' for pair in pairs_shown])
                    for field in fields]
            out.append('')
        if c['cat']:
            out += ['Outcome against labels:', '', _row('label', ['cases', *(f'{head} correct' for head in heads)]), '|---|---|' + rule[5:]]
            for (label, scope), counts in sorted(c['cat'].items()):
                out.append(_row(f'{label} ({scope})', [max(counts[m][1] for m in shown), *(f'{counts[m][0]}/{counts[m][1]}' for m in shown)]))
            out.append('')
        if c['prob']:
            out += ['Probabilities against labels (accuracy = share right when "yes" means p ≥ threshold; * = Faculty\'s own threshold):', '',
                    '| label | n (pos) | model | AUC | Brier | ' + ' | '.join(
                        f'{t:g}' for t in GRID) + ' |', '|---|---|---|---|---|' + '---|' * len(GRID)]
            for (label, scope), per in sorted(c['prob'].items()):
                site = SITE.get(label.split('__')[0], (.5,))
                for mode in shown:
                    pairs = [(p, y) for p, y, _ in per[mode]]
                    if not pairs:
                        continue
                    cells = []
                    for t in GRID:
                        cell = _fmt(accuracy(pairs, t))
                        cells.append(cell + ('*' if any(abs(t - s) < 1e-9 for s in site) else ''))
                    extra = [f'{s:g}: {_fmt(accuracy(pairs, s))}*' for s in site if all(abs(s - t) > 1e-9 for t in GRID)]
                    out.append(f"| {label} ({scope}) | {len(pairs)} ({sum(y for _, y in pairs)}) | {mode} | {_fmt(auc(pairs))} | "
                               f"{_fmt(brier(pairs), 3)} | " + ' | '.join(cells) + ' |' + (' ' + ', '.join(extra) if extra else ''))
            out.append('')
        if c['strata']:
            names = sorted({n for per in c['strata'].values() for n in per if not n.startswith('crop_')})
            out += ['Mean probability by stratum (what Faculty\'s own reading managed):', '',
                    '| stratum | model | n | ' + ' | '.join(names) + ' |', '|---|---|---|' + '---|' * len(names)]
            for (stratum, mode), per in sorted(c['strata'].items()):
                count = max((len(v) for v in per.values()), default=0)
                out.append(f'| {stratum} | {mode} | {count} | ' + ' | '.join(
                    _fmt(sum(per[n]) / len(per[n]) if per.get(n) else None) for n in names) + ' |')
            out.append('')
        summary[name] = {'cases': dict(c['cases']), 'agree': {f'{a}~{b}': dict(c['agree'][(a, b)]) for a, b in pairs_shown},
                         'board': {f'{key[1]}, {key[0]}': {m: c['board'][key][m] for m in shown} for key in sorted(c['board'])},
                         'disagreements': len(c['disagree'])}
    disagreements = [d for c in checks.values() for d in c['disagree']]
    out += ['## Disagreements', '', f'{len(disagreements)} cases where the site\'s outcome differs between models '
            '(full list with ids in disagreements.csv).', '']
    with (folder / 'disagreements.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['check', 'case', 'source', 'fields', *shown, 'labels', 'meta'])
        for d in disagreements:
            writer.writerow([d['check'], d['case'], d['source'], ' '.join(d['fields']),
                             *(json.dumps(d[m], default=str) if m in d else '' for m in shown),
                             json.dumps(d['labels'], default=str), json.dumps(d['meta'], default=str)])
    (folder / 'report.md').write_text('\n'.join(out) + '\n')
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2, default=str))
    return folder / 'report.md'
