"""Runs cases: each through the site's own code once per model (and again for the stability subset), one JSON line per case."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import threading
import time

from student_support.service import StudentSupportService

from .catalog_copy import open_copy
from .pair import PairService


class Context:
    """What real-case builders read: the copy (read-only), its tracking cutoff, the moment it was taken."""
    def __init__(self, folder, copy):
        self.folder, self.copy = folder, copy
        self.db = open_copy(copy)
        self.tracking_since = StudentSupportService._baseline(self.db)['tracking_since']
        self.now = datetime.fromtimestamp(copy.stat().st_mtime, timezone.utc).isoformat()

    def fake_support(self):
        support = object.__new__(StudentSupportService)
        support.path, support.triage = self.copy, None
        return support


def _side(store, case, mode, attempt=''):
    service = PairService(store, mode, attempt=attempt)
    started = time.monotonic()
    try:
        result = case.run(service)
    except Exception as why:  # noqa: BLE001 - one case's failure is recorded, the run goes on
        result = {'outcome': {}, 'p': {}, 'error': f'{type(why).__name__}: {str(why)[:160]}'}
    result['calls'] = list(service.used)
    result['seconds'] = round(time.monotonic() - started, 3)
    return result


def one(store, case, modes):
    results = {}
    for mode in [m for m in case.modes if m in modes]:
        results[mode] = _side(store, case, mode)
        if case.repeat:
            results[mode]['repeat'] = _side(store, case, mode, attempt='repeat')
    return {'check': case.check, 'id': case.id, 'source': case.source, 'labels': case.labels, 'meta': case.meta, 'results': results}


def run(store, cases, path, modes=('jev', 'decisions'), workers=6, say=print):
    """Parallel cases first (their own threads), then the ones that patch module globals, one at a time. Snap is asked only when
    `modes` names it, so a caller that says nothing (the memory sandbox, an older command) sends it nothing."""
    lock = threading.Lock()
    done = [0]
    total = len(cases)

    def write(line):
        with lock, path.open('a') as out:
            out.write(json.dumps(line, ensure_ascii=False, sort_keys=True, default=str) + '\n')
            done[0] += 1
            if done[0] % 10 == 0 or done[0] == total:
                say(f'  {done[0]}/{total} cases')

    free = [c for c in cases if not c.serial]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, store, c, modes) for c in free]):
            write(future.result())
    for case in (c for c in cases if c.serial):
        write(one(store, case, modes))
