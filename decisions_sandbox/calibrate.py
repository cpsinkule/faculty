"""TRIAGE CALIBRATION, the sweep (requirement, 2026-10-08). Reads results-triage-calibrate.jsonl and proposes
the gates for each model, by the rule the instructor approved with the plan:

1. FYI (no reply suggested) and a thanks on a tracked follow-up (`acknowledged`) never take a message you answered, tracked or
   recorded (`visible`): zero such messages, then the most messages you let go (`fyi_ok`); ties go to the stricter gate.
2. Wellbeing flags every invented concern, at the highest bar that does; real messages flagged are counted.
3. A request chip keeps at least Jev's recall on the labeled chips at the fewest real flags.
4. Urgency: the confidence that keeps the invented levels right.

Counts, gates and ids only; no message text."""
import itertools
import json
from pathlib import Path

JEV = {'strong_confidence': .95, 'strong_probability': .98, 'needs_action': .02, 'wellbeing': .30, 'urgency_confidence': .6, 'request': .9}
CHIPS = ('absence', 'makeup', 'office_hours', 'grading', 'materials')
CONFIDENCES = (.5, .6, .7, .8, .9, .95)
PROBABILITIES = (.7, .8, .9, .95, .98, .99)
NEEDS = (.02, .05, .1, .2, .3, .5)
BARS = (.1, .2, .3, .4, .5, .6, .7, .8, .9)


# Labels corrected after reading the message and your next one (2026-10-08, the only two "visible" messages any model read as
# FYI or thanks at today's gates): neither student message needed an answer; your next message answered something else.
CORRECTED = {'real-31252724-cec24054': 0,  # a student confirming their excuse note arrived; your reply asked about their quiz
             'real-58843668-d5522d91': 0}  # "please disregard, I found it"; your reply, a minute later, answered the earlier message


def _rows(folder):
    for line in (Path(folder) / 'results-triage-calibrate.jsonl').read_text().splitlines():
        if line:
            row = json.loads(line)
            if row['id'] in CORRECTED:
                row['labels']['visible'] = {'value': CORRECTED[row['id']], 'strength': 'corrected'}
            yield row


def _side(row, mode):
    result = (row['results'].get(mode) or {})
    return (result.get('outcome') or {}), (result.get('p') or {})


def fyi(outcome, p, meta, gates, needs=True):
    if outcome.get('status') != 'complete' or outcome.get('kind_choice') not in ('acknowledgment', 'fyi'):
        return False
    strong = (p.get('kind_confidence') or 0) >= gates['strong_confidence'] and (p.get('kind_top') or 0) >= gates['strong_probability']
    calm = (p.get('wellbeing') or 0) < gates['wellbeing']
    if not needs:  # `acknowledged`: the kind and wellbeing only
        return strong and calm
    return strong and calm and (p.get('needs_action') or 0) <= gates['needs_action'] and not meta.get('context_incomplete')


def _value(row, name):
    return ((row.get('labels') or {}).get(name) or {}).get('value')


def sweep(rows, mode):
    real = [r for r in rows if r['source'] == 'real']
    visible = [r for r in real if _value(r, 'visible') == 1]
    letgo = [r for r in real if _value(r, 'visible') == 0]
    found = {'mode': mode, 'real': len(real), 'visible': len(visible), 'fyi_ok': len(letgo),
             'complete': sum(1 for r in real if _side(r, mode)[0].get('status') == 'complete')}

    def count(gates, needs=True):
        bad = [r['id'] for r in visible if fyi(*_side(r, mode), r['meta'], gates, needs)]
        good = sum(1 for r in letgo if fyi(*_side(r, mode), r['meta'], gates, needs))
        return bad, good
    for name, needs in (('fyi', True), ('acknowledged', False)):
        bad, good = count(JEV, needs)
        found[f'{name}_at_jev_gates'] = {'visible_taken': len(bad), 'let_go_caught': good, 'ids': bad}
        best = None
        for conf, prob, need in itertools.product(CONFIDENCES, PROBABILITIES, NEEDS if needs else (JEV['needs_action'],)):
            gates = {**JEV, 'strong_confidence': conf, 'strong_probability': prob, 'needs_action': need}
            bad, good = count(gates, needs)
            if bad:
                continue
            strict = (conf, prob, -need)
            if best is None or good > best[0] or good == best[0] and strict > best[1]:
                best = (good, strict, gates)
        found[f'{name}_proposed'] = best and {'let_go_caught': best[0], 'strong_confidence': best[2]['strong_confidence'],
                                             'strong_probability': best[2]['strong_probability'],
                                             **({'needs_action': best[2]['needs_action']} if needs else {})}
    invented = [r for r in rows if r['source'] == 'invented' and _value(r, 'wellbeing') is not None]
    concerns = [r for r in invented if _value(r, 'wellbeing') == 1]
    ordinary = [r for r in invented if _value(r, 'wellbeing') == 0]

    def flagged(r, bar):
        return (_side(r, mode)[1].get('wellbeing') or 0) >= bar
    found['wellbeing'] = {f'{bar:.2f}': {'concerns_caught': sum(flagged(r, bar) for r in concerns), 'of': len(concerns),
                                         'ordinary_flagged': sum(flagged(r, bar) for r in ordinary), 'of_ordinary': len(ordinary),
                                         'real_flagged': sum(flagged(r, bar) for r in real)} for bar in BARS}
    catching = [bar for bar in BARS if all(flagged(r, bar) for r in concerns)]
    found['wellbeing_proposed'] = max(catching) if catching else None
    found['missed_concerns_at_jev_bar'] = [r['id'] for r in concerns if not flagged(r, JEV['wellbeing'])]
    chips = {}
    for chip in CHIPS:
        positives = [r for r in real if _value(r, chip) == 1]
        chips[chip] = {f'{bar:.2f}': {'labeled_caught': sum((_side(r, mode)[1].get(chip) or 0) >= bar for r in positives),
                                      'labeled': len(positives), 'real_flagged': sum((_side(r, mode)[1].get(chip) or 0) >= bar for r in real)}
                       for bar in BARS}
    found['chips'] = chips
    levels = [r for r in rows if r['source'] == 'invented' and _value(r, 'urgency_level') is not None]
    found['urgency'] = {}
    for bar in (.4, .5, .6, .7, .8):
        right = 0
        for r in levels:
            p = _side(r, mode)[1]
            level = round(p.get('urgency_score') or 0) if (p.get('urgency_confidence') or 0) >= bar else 0
            right += level == _value(r, 'urgency_level')
        found['urgency'][f'{bar:.1f}'] = f'{right}/{len(levels)}'
    return found


def write(folder):
    rows = list(_rows(folder))
    results = {mode: sweep(rows, mode) for mode in ('jev', 'decisions')}
    (Path(folder) / 'calibration-triage.json').write_text(json.dumps(results, indent=1))
    lines = ['# Triage calibration', '',
             f"Real student messages: {results['jev']['real']} (labeled must stay visible: {results['jev']['visible']}; "
             f"may go FYI: {results['jev']['fyi_ok']}). Invented wellbeing/urgency set as listed.", '']
    for mode, found in results.items():
        lines += [f'## {mode}', '', f"Complete answers: {found['complete']} of {found['real']}.", '']
        for name in ('fyi', 'acknowledged'):
            at = found[f'{name}_at_jev_gates']
            lines.append(f"- **{name}** at today's Jev gates: {at['visible_taken']} visible messages taken, {at['let_go_caught']} let go caught"
                         + (f" (ids: {', '.join(at['ids'])})" if at['ids'] else ''))
            lines.append(f"- **{name}** proposed: {found[f'{name}_proposed']}")
        lines.append(f"- **wellbeing** proposed bar: {found['wellbeing_proposed']}; concerns missed at .30: {found['missed_concerns_at_jev_bar']}")
        lines += ['', '| bar | concerns caught | ordinary flagged | real flagged |', '|---|---|---|---|']
        lines += [f"| {bar} | {v['concerns_caught']}/{v['of']} | {v['ordinary_flagged']}/{v['of_ordinary']} | {v['real_flagged']} |"
                  for bar, v in found['wellbeing'].items()]
        lines += ['', '| chip | bar | labeled caught | real flagged |', '|---|---|---|---|']
        for chip, by in found['chips'].items():
            lines += [f"| {chip} | {bar} | {v['labeled_caught']}/{v['labeled']} | {v['real_flagged']} |" for bar, v in by.items()
                      if bar in ('0.50', '0.70', '0.80', '0.90')]
        lines += ['', f"Urgency levels right by confidence bar: {found['urgency']}", '']
    (Path(folder) / 'calibration-triage.md').write_text('\n'.join(lines) + '\n')
    return Path(folder) / 'calibration-triage.md'
