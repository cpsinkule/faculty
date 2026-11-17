"""The whole path, in order: every instructor message of the timeline through one arm's write path, then every later request
through its read path. One JSON line per (arm, model, embedder, statement form): ids, actions and probabilities, never text.

The read path is measured twice: on the arm's own store (its write mistakes included), and on the store a perfect write path
would have left (`oracle`), at its ordinary size and padded with a thousand neutral rows, so retrieval is judged on its own.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from decisions_sandbox.pair import PairService

from . import questions
from .cases import PADS
from .embedders import Down
from .fakes import Unavailable
from .pipeline import Flat, Store, Today, add_episodes, oracle_store, seeded


def configs(embedders, variants=('gold', 'raw')):
    """The arms: today's behaviour once; the flat pipeline per embedder and statement form; its two outages."""
    found = [{'arm': 'today', 'embedder': '', 'variant': 'raw'}]
    for name in embedders:
        found += [{'arm': 'flat', 'embedder': name, 'variant': variant} for variant in variants]
    first = next((name for name in embedders if name != 'none'), 'none')
    found.append({'arm': 'flat-embedder-down', 'embedder': first, 'variant': 'gold'})
    found.append({'arm': 'flat-decision-down', 'embedder': first, 'variant': 'gold'})
    if 'none' in embedders:  # this arm reads no embedder: a run that names embedders without none leaves it out
        found.append({'arm': 'flat-everything', 'embedder': 'none', 'variant': 'gold'})
    return found


# 'mixed': each judgment from the model that made it best on the components (2026-10-10): what to keep and what a request
# needs from Jev in one request each; whether a statement repeats or replaces a kept one from Decisions, one at a time.
MIXED = {questions.CAPTURE_PURPOSE: 'jev', questions.CONSOLIDATE_PURPOSE: 'decisions', questions.RELEVANCE_PURPOSE: 'jev'}


class Routed:
    """One service for a pipeline whose purposes go to different models; `used` lists every request key, as PairService's."""
    def __init__(self, pair_store, routes):
        self.services = {mode: PairService(pair_store, mode) for mode in set(routes.values())}
        self.routes = routes

    @property
    def used(self):
        return [key for service in self.services.values() for key in service.used]

    def ask(self, purpose, *args, **kwargs):
        return self.services[self.routes[purpose]].ask(purpose, *args, **kwargs)


def _asked(pair_store, mode, down):
    if down:
        return Unavailable()
    return Routed(pair_store, MIXED) if mode == 'mixed' else PairService(pair_store, mode)


def one(world, pair_store, mode, config, embedders, gates=None, student_policy='strong', gate_set='starting', capture=1, single=False):
    arm = config['arm']
    started = time.monotonic()
    line = {**config, 'mode': mode, 'gates': gates or {}, 'student_policy': student_policy, 'gate_set': gate_set,
            'capture': capture, 'single': single}
    if arm == 'today':
        pipeline, store = Today(world), Store()
    else:
        embedder = Down() if arm == 'flat-embedder-down' else embedders[config['embedder']]
        pipeline = Flat(world, embedder, gates=gates, variant=config['variant'], student_policy=student_policy,
                        everything=arm == 'flat-everything', capture=capture, single=single)
        store = seeded(world)
    down = arm == 'flat-decision-down'
    writes = []
    for event in world.timeline():
        service = _asked(pair_store, mode, down)
        result = pipeline.write(service, event, store)
        result['calls'] = list(service.used)
        writes.append(result)
    if arm != 'today':
        add_episodes(world, store)
    line['writes'] = writes
    line['final'] = {ident: row['status'] for ident, row in store.snapshot().items() if ident in world.events}
    # Asking about every kept memory is measured once, on the expected store: its cost is the point, not its write path.
    reads = {} if arm == 'flat-everything' else {'own': _reads(world, pair_store, mode, down, pipeline, store)}
    if arm in ('flat', 'flat-everything'):
        for pad in PADS if arm == 'flat' else PADS[:1]:
            reads[f'oracle-{pad}'] = _reads(world, pair_store, mode, down, pipeline, oracle_store(world, config['variant'], pad))
    line['reads'] = reads
    line['seconds'] = round(time.monotonic() - started, 2)
    return line


def _reads(world, pair_store, mode, down, pipeline, store):
    found = []
    for query in world.queries.values():
        service = _asked(pair_store, mode, down)
        result = pipeline.read(service, query, store)
        result['keys'] = list(service.used)
        found.append(result)
    return found


def run(world, pair_store, path, modes, embedders, gates=None, student_policy=None, gate_set='starting', variants=('gold', 'raw'),
        workers=4, say=print, capture=1, single=(), arms=()):
    """Every arm for every model. An arm is sequential (each message sees the store the last one left); arms run side by side,
    and a request two arms share is sent once."""
    lock = threading.Lock()
    jobs = []
    for config in configs(embedders, variants):
        if arms and config['arm'] not in arms:
            continue
        for mode in (modes[:1] if config['arm'] in ('today', 'flat-decision-down') else modes):
            jobs.append((config, 'none' if config['arm'] == 'today' else mode))
    done = [0]

    def write(line):
        with lock, path.open('a') as out:
            out.write(json.dumps(line, ensure_ascii=False, sort_keys=True, default=str) + '\n')
            done[0] += 1
            say(f"  {done[0]}/{len(jobs)} {line['arm']} {line['mode']} {line['embedder']} {line['variant']}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        def form(mode):
            if mode == 'mixed':
                return sorted(purpose for purpose, model in MIXED.items() if model in single and purpose != questions.CAPTURE_PURPOSE)
            return mode in single
        futures = [pool.submit(one, world, pair_store, mode, config, embedders, (gates or {}).get(mode),
                               (student_policy or {}).get(mode, 'strong'), gate_set, capture, form(mode)) for config, mode in jobs]
        for future in as_completed(futures):
            write(future.result())
    return len(jobs)
