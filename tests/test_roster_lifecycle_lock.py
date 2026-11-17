"""Nested compound actions retain cross-thread and cross-instance exclusion."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from rosters import RosterService
import test_rosters as roster_tests


class RosterLifecycleLockTests(unittest.TestCase):
    managed = False
    setUp = roster_tests.RosterTests.setUp

    def assert_competitors_wait_for_outer_lock(self):
        service = self.service
        peer = RosterService(service.path, storage=self.storage)
        started = [threading.Event(), threading.Event()]
        entered = [threading.Event(), threading.Event()]

        def acquire(other, index):
            started[index].set()
            with other.lock():
                entered[index].set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            with service.lock():
                # An inner exception must not release the outer descriptor or
                # clear its recursion state. More nested service work can follow.
                with self.assertRaisesRegex(ValueError, 'inner'):
                    with service.lock():
                        raise ValueError('inner')
                with service.lock():
                    futures = [pool.submit(acquire, other, i) for i, other in enumerate((service, peer))]
                    for event in started:
                        self.assertTrue(event.wait(1))
                for event in entered:
                    self.assertFalse(event.wait(0.1))
            for future in futures:
                future.result(timeout=3)
        self.assertTrue(all(event.is_set() for event in entered))

    def test_nested_scopes_keep_other_threads_and_instances_excluded(self):
        self.assert_competitors_wait_for_outer_lock()

    def test_outer_exception_releases_lock_and_resets_recursion(self):
        with self.assertRaisesRegex(ValueError, 'outer'):
            with self.service.lock():
                with self.service.lock():
                    raise ValueError('outer')
        # Repeating exclusion after the exception detects leaked descriptors and
        # a stale thread-local flag that would otherwise silently skip locking.
        self.assert_competitors_wait_for_outer_lock()


class ManagedRosterLifecycleLockTests(RosterLifecycleLockTests):
    managed = True

