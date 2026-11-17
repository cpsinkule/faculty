#!/usr/bin/env python3
"""SANDBOX (requirement, 2026-10-10): what a long-term memory for the Assistant would keep, replace and
give back, measured on invented cases before anything is built into Faculty.

    python3 manage_memory_sandbox.py cases                      # check the invented cases and count them
    python3 manage_memory_sandbox.py keys                       # which of the sandbox's keys (TypeSafe, OpenAI, SubQ, OpenRouter) it would hold (never values)
    python3 manage_memory_sandbox.py fetch [--download]         # the sandbox's own venv, pinned models and Jev-Mem checkout
    python3 manage_memory_sandbox.py audit      --run NAME      # where Jev-Mem's source reaches outside itself, to read
    python3 manage_memory_sandbox.py probe      --run NAME      # each embedder on this machine: load, latency, memory
    python3 manage_memory_sandbox.py embed      --run NAME      # vectors for every case text, cached
    python3 manage_memory_sandbox.py components --run NAME      # each decision on its own, both models
    python3 manage_memory_sandbox.py sweep      --run NAME      # proposed gates from the components, as counts
    python3 manage_memory_sandbox.py stories    --run NAME      # every arm end to end (--gates proposed after a sweep)
    python3 manage_memory_sandbox.py lessons    --run NAME      # where the right lesson ranks, by search
    python3 manage_memory_sandbox.py jevmem     --run NAME      # Jev-Mem at its pinned commit, on the same cases
    python3 manage_memory_sandbox.py report     --run NAME      # memory-report.md, report.md, memory-summary.json
    python3 manage_memory_sandbox.py whole      --run NAME      # every kept memory given to Snap at once, ranked (2026-10-13)

Scripted answers unless --live: without it no request leaves the machine. A run folder is either scripted or live, never
both. Every run folder is under /mnt/nvme/Teaching/tmp/memory-sandbox (0700). Nothing here imports app.py, opens a catalog,
adds a route, migration or setting, or restarts anything; the cases are invented, so nothing real is sent anywhere.
"""
import argparse
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from memory_sandbox import safety  # noqa: E402
from decisions_sandbox.pair import RequestNotSent  # noqa: E402


KEYS_OF = {'jev': ('TYPESAFE_API_KEY',), 'decisions': ('OPENAI_API_KEY',), 'snap': ('SUBQ_API_KEY',),
           'mixed': ('TYPESAFE_API_KEY', 'OPENAI_API_KEY'), 'jev114': ('TYPESAFE_API_KEY',)}


class BudgetExceeded(RequestNotSent):
    """Not an Exception, so no case swallows it: the run stops, and the request that met the limit is not recorded as asked."""


class Budget:
    def __init__(self, limit):
        self.limit, self.sent, self._lock = limit, 0, threading.Lock()

    def wrap(self, ask, per=0):
        """`per`: how many questions the model takes in a request, when a longer one goes as several (Snap: sixteen)."""
        def counted(state, questions, images):
            requests = -(-len(questions) // per) if per else 1
            with self._lock:
                if self.sent + requests > self.limit:
                    raise BudgetExceeded(f'{self.limit} requests were sent; raise --budget-calls to send more.')
                self.sent += requests
            return ask(state, questions, images)
        return counted


def kind_of(folder, live):
    """A run folder answers from one source for good: a scripted answer must never be read as a model's."""
    marker = folder / 'answers-from'
    wanted = 'live' if live else 'scripted'
    if marker.exists() and marker.read_text().strip() != wanted:
        raise SystemExit(f'This run folder holds {marker.read_text().strip()} answers. Use another --run name.')
    marker.write_text(wanted + '\n')


def decision_store(folder, world, live, budget):
    from decisions_sandbox.pair import Store
    from memory_sandbox import fakes
    if not live:
        return Store(folder, ask_jev=fakes.Oracle(world, 'scripted-jev'), ask_decisions=fakes.Oracle(world, 'scripted-decisions'),
                     ask_snap=fakes.Oracle(world, 'scripted-snap'), available=lambda mode: True), None
    from decisions_sandbox import snap_client
    counter = Budget(budget)
    return Store(folder, ask_jev=counter.wrap(Store._jev), ask_decisions=counter.wrap(Store._decisions),
                 ask_snap=counter.wrap(Store._snap, per=snap_client.MAX_QUESTIONS)), counter


def estimate(command, world, modes, embedders):
    """An upper bound on the requests a command sends, before identical requests are shared."""
    events, queries = len(world.events), len(world.queries)
    if command == 'components':
        from memory_sandbox import components
        return sum(len(build(world)) for build in components.ALL.values()) * 6 * len(modes)
    if command == 'whole':
        # For each store: one ranking request and each picker's relevance request (Snap's as two, and two more with the text
        # quoted); then, on the ordinary store, a yes/no for each of about two hundred memories, sixteen a request.
        from memory_sandbox import whole
        return queries * len(whole.PADS) * (1 + len(modes) + 3 * ('snap' in modes)) + queries * 13
    from memory_sandbox import stories
    arms = len(stories.configs(embedders))
    # One request per kept memory at the worst: five when a statement is consolidated, eleven when a request is read.
    return arms * len(modes) * (events * 6 + queries * 3 * 11) + len(modes) * queries * 250


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('cases', 'keys', 'fetch', 'audit', 'probe', 'embed', 'components', 'sweep', 'stories',
                                            'lessons', 'jevmem', 'report', 'whole'))
    parser.add_argument('--baseline', default='run-20261010', help="whole only: the run whose stories give today's design")
    parser.add_argument('--retry', default='', help='whole only: a name under which each ranking that failed is asked once more')
    parser.add_argument('--run', default='')
    parser.add_argument('--live', action='store_true', help='ask the two decision models; without it every answer is scripted')
    parser.add_argument('--budget-calls', type=int, default=0, help='the most requests a live command may send')
    parser.add_argument('--download', action='store_true', help='fetch only: download what is missing')
    parser.add_argument('--modes', default='jev,decisions')
    parser.add_argument('--embedders', default='', help='comma-separated: eg2-768, eg2-256, minilm, hash, none')
    parser.add_argument('--checks', default='', help='components only: comma-separated builders (default: all)')
    parser.add_argument('--gates', default='starting', choices=('starting', 'proposed'))
    parser.add_argument('--set', default='', help='stories only: gates to override, as name=value,name=value')
    parser.add_argument('--label', default='', help='stories only: the name this set of gates has in the report')
    parser.add_argument('--capture', type=int, default=2, choices=(1, 2), help='stories only: which wording of the capture request')
    parser.add_argument('--single', default='decisions', help='stories only: models asked about one kept memory at a time')
    parser.add_argument('--arms', default='', help='stories only: comma-separated arms (default: all)')
    parser.add_argument('--variants', default='gold,raw', help='stories only: statement forms')
    parser.add_argument('--configs', default='', help='jevmem only: comma-separated configurations (default: all)')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args(argv)

    from memory_sandbox import cases
    world = cases.World()
    counts = cases.validate(world)
    if args.command == 'cases':
        print(json.dumps(counts, indent=1))
        fit = sum(event['split'] == 'fit' for event in world.events.values())
        print(f'{len(world.events)} instructor messages ({fit} fit, {len(world.events) - fit} held out), in {len(world.timeline())} steps')
        return 0
    if args.command == 'fetch':
        from memory_sandbox import runtime
        print(json.dumps(runtime.fetch(download=args.download), indent=1))
        return 0
    if args.live and not args.run:
        raise SystemExit('A live command needs --run NAME.')
    folder = safety.run_folder(args.run or 'run-scratch')
    held = safety.scrub_environment(folder)
    if args.command == 'keys':
        print('keys held by the sandbox:', ', '.join(f"{k} {'yes' if v else 'no'}" for k, v in held.items()))
        return 0
    modes = tuple(m for m in args.modes.split(',') if m)
    names = [n for n in args.embedders.split(',') if n] or (['eg2-768', 'eg2-256', 'minilm', 'none'] if args.live else ['hash', 'none'])

    if args.command == 'audit':
        from memory_sandbox import runtime
        path, lines = runtime.audit(folder)
        print(f'{lines} places to read: {path}')
        return 0
    if args.command == 'probe':
        from memory_sandbox import embedding
        embedding.probe(folder)
        return 0
    if args.command == 'embed':
        from memory_sandbox import embedding
        embedding.embed(folder, world, models=sorted({n.split('-')[0] for n in names if n not in ('none', 'hash')}))
        return 0
    if args.command == 'sweep':
        from memory_sandbox import sweep
        print(sweep.write(folder))
        return 0
    if args.command == 'report':
        from decisions_sandbox import report as component_report
        from memory_sandbox import report
        if (folder / 'results-components.jsonl').exists():
            print(component_report.write(folder))
        print(report.write(folder, world))
        return 0
    if args.command == 'lessons':
        from memory_sandbox import embedding
        found = embedding.lessons_recall(world, embedding.embedders(names))
        (folder / 'lessons-recall.json').write_text(json.dumps(found, indent=1))
        print(json.dumps(found, indent=1))
        return 0
    if args.command == 'jevmem':
        from memory_sandbox import jevmem
        return jevmem.run(folder, world, live=args.live, budget=args.budget_calls,
                          wanted=[c for c in args.configs.split(',') if c], held=held)

    kind_of(folder, args.live)
    if args.command == 'whole':  # Snap ranks; `--modes` names who answers the relevance request after it (jev, snap)
        modes = ('jev', 'snap') if args.modes == parser.get_default('modes') else tuple(m for m in modes if m in ('jev', 'snap'))
    wanted = estimate(args.command, world, modes, names)
    if args.live:
        if args.budget_calls < 1:
            raise SystemExit(f'A live {args.command} sends at most about {wanted} requests. Pass --budget-calls N to allow it.')
        asked = {*modes, 'snap'} if args.command == 'whole' else set(modes)
        missing = sorted({key for mode in asked for key in KEYS_OF[mode] if not held.get(key)})
        if missing:
            raise SystemExit('No key for: ' + ', '.join(missing))
        print(f'live: at most about {wanted} requests before sharing; this command may send {args.budget_calls}')
    if args.command == 'whole':
        from memory_sandbox import whole
        store, counter = decision_store(folder, world, args.live, args.budget_calls)
        baseline = safety.SCRATCH / args.baseline
        try:
            whole.run(world, store, folder / 'whole.jsonl', pickers=modes, gates=whole.baseline_gates(baseline), workers=args.workers,
                      retry=args.retry)
        except BudgetExceeded as stop:
            print(f'Stopped: {stop}')
            return 3
        print(whole.write(folder, world, baseline))
        if counter:
            print(f'requests sent by this command: {counter.sent}')
        return 0
    from memory_sandbox import embedding
    found = embedding.embedders(names)
    store, counter = decision_store(folder, world, args.live, args.budget_calls)
    try:
        if args.command == 'components':
            from decisions_sandbox import report as component_report
            from decisions_sandbox import runner
            from memory_sandbox import components
            picked = [n for n in args.checks.split(',') if n] or list(components.BUILDERS)
            built = [case for name in picked for case in components.ALL[name](world)]
            results = folder / 'results-components.jsonl'
            checks = {case.check for case in built}
            if results.exists():  # a partial rerun replaces only the checks it ran
                kept = [line for line in results.read_text().splitlines() if line and json.loads(line)['check'] not in checks]
                results.write_text(''.join(line + '\n' for line in kept))
            print(f'{len(built)} component cases: ' + ', '.join(sorted(checks)))
            runner.run(store, built, results, modes=modes, workers=args.workers)
            print(component_report.write(folder))
        else:
            from memory_sandbox import report, stories, sweep
            single = tuple(m for m in args.single.split(',') if m)
            chosen = sweep.proposed(folder, args.capture, single) if args.gates == 'proposed' else {}
            if args.gates == 'proposed' and not chosen:
                raise SystemExit('Run sweep first: there are no proposed gates in this run folder.')
            changed = {name: float(value) for name, _, value in (item.partition('=') for item in args.set.split(',') if item)}
            label = args.label or args.gates
            arms = tuple(a for a in args.arms.split(',') if a)
            path = folder / 'stories.jsonl'
            before = [json.loads(line) for line in (path.read_text().splitlines() if path.exists() else []) if line]
            variants = tuple(v for v in args.variants.split(',') if v)
            kept = [line for line in before if line['arm'] == 'jevmem' or line.get('gate_set') != label
                    or (arms and line['arm'] not in arms) or line['mode'] not in (*modes, 'none') or line['embedder'] not in (*names, '')
                    or line['variant'] not in variants]
            path.write_text(''.join(json.dumps(line, ensure_ascii=False, sort_keys=True) + '\n' for line in kept))
            if 'mixed' in modes and store.calls_path.exists() is False and args.live:
                raise SystemExit('Run the components for both models first.')
            policy = {mode: chosen[mode]['student_policy'] for mode in chosen}
            gates = {mode: {**(chosen.get(mode, {}).get('gates') or {}), **changed} for mode in modes}
            stories.run(world, store, path, modes, found, gates=gates, student_policy=policy, gate_set=label, workers=args.workers,
                        capture=args.capture, single=single, arms=arms, variants=variants)
            print(report.write(folder, world))
    except BudgetExceeded as stop:
        print(f'Stopped: {stop}')
        return 3
    if counter:
        print(f'requests sent by this command: {counter.sent}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
