"""Faculty's writes to Canvas in this process, and a stop that waits for them (docs/decisions/canvas.md "A STOP WAITS").

Every write to Canvas that may still be running when the web process is asked to stop registers here for as long as it runs,
before anything of it is committed: a message going out to sections (canvas/messages.py), an announcement being posted
(canvas/announcements.py), a push of the term's copy or of one
section's own content (content_service.py, section_content.py), grades sent from the gradebook (gradebook/service.py) and a reply
posted in a Canvas conversation from the Mail page (gmail_mail/service.py). A seating chart's Confirm and its send of updated seat cards register too (seating.py, SEATING "A RESTART WAITS"): they issue, publish and email Response Cards in steps. Once the process is stopping none starts: 503, and
nothing is written anywhere. Before 2026-10-04 only messages waited, and a restart cut a push or a send off in the middle. A Sync
with Canvas registers from before it reads Canvas (requirement, 2026-10-06): asked to stop before it has recorded anything, it ends there with nothing written (503).

`stop_handler` is installed for SIGTERM by app.py. A signal handler runs in the main thread, which is waitress's own loop: waiting
there read no request at all, so the instructor's page hung instead of being told Faculty is restarting (2026-10-04 review). The
handler only stops new writes and returns. A thread waits for the running ones, at most STOP_WAIT seconds (systemd allows
TimeoutStopSec before it kills: deploy/faculty.service.d/stop-wait.conf), then delivers the signal again to the handler that was
there before: rosters._terminate, which closes the catalog's idle connections so that a stopped catalog is complete without its
WAL, then ends the process by the signal (the handler it replaced since 2026-10-01 never ran, so a restart left the WAL behind).
Until 2026-10-06 the wait was 80 s of systemd's 90, shorter than a Sync with Canvas: a restart 80 s into the wait cut one off with
4 of its 20 assignments unsent (instructor: block restarts while a sync is running). It is now long enough for a whole sync.
Once the writes it waited for are done it waits ANSWER_WAIT more seconds: a request that registered a write answers just after it
ends (a sync refused for the stop), and ending the process first left the page an answer it could not read.
Last, `drain` (web_server.py, FRONTEND "A RESTART NOBODY SEES") finishes what else is being answered. It comes after the writes, since
waitress keeps taking requests during their wait, which may be minutes.
"""
import os
import signal
import threading
import time

from werkzeug.exceptions import ServiceUnavailable

STOP_WAIT = 600  # seconds a stopping process waits for its writes to Canvas (TimeoutStopSec is 630: stop-wait.conf)
ANSWER_WAIT = 2  # seconds more, after the writes it waited for, for their requests' answers to go out (600 + 2 < 630)


class Writes:
    """The writes to Canvas this process is running: {key: what it is}. One per app (CanvasService.writes)."""

    def __init__(self):
        self._changed = threading.Condition()
        self._running = {}
        self._closing = False

    @property
    def closing(self):
        with self._changed:
            return self._closing

    def begin(self, key, label, refusal):
        """Registers one write, before anything of it is committed (its record then never shows it interrupted while it runs).
        ServiceUnavailable(`refusal`) once the process is stopping."""
        with self._changed:
            if self._closing:
                raise ServiceUnavailable(refusal)
            self._running[key] = label

    def end(self, key):
        with self._changed:
            self._running.pop(key, None)
            self._changed.notify_all()

    def running(self, key):
        with self._changed:
            return key in self._running

    def busy(self, prefix=""):
        """The keys of the writes running now (those starting with `prefix`)."""
        with self._changed:
            return sorted(key for key in self._running if key.startswith(prefix))

    def labels(self):
        """What the running writes are, once each ("a push to Canvas", ...)."""
        with self._changed:
            return sorted(set(self._running.values()))

    def close(self):
        """No new write starts (the process is stopping); running ones go on."""
        with self._changed:
            self._closing = True

    def wait_idle(self, timeout):
        """True once no write runs, False when `timeout` seconds pass first."""
        with self._changed:
            return self._changed.wait_for(lambda: not self._running, timeout)


def stop_handler(writes, timeout=STOP_WAIT, previous=None, answer_wait=ANSWER_WAIT, drain=None):
    """A SIGTERM handler for the web process (see above). `previous`: the handler it replaces (signal.getsignal), which ends the
    process once the writes are done; the default action when there was none. `drain`: called last, before the process ends."""
    restore = previous if callable(previous) else signal.SIG_DFL

    def finish(signum):
        waiting = writes.busy()
        if waiting:
            print(f"Stopping: waiting for {len(waiting)} write(s) ({', '.join(writes.labels())}) to finish (at most {timeout} s).", flush=True)
            if not writes.wait_idle(timeout):
                print("Stopping although a write has not finished; its record says so.", flush=True)
            time.sleep(answer_wait)
        if drain:
            drain()
        os.kill(os.getpid(), signum)  # now handled by `restore`, in the main thread

    def handler(signum, frame):
        writes.close()
        signal.signal(signum, restore)  # a second SIGTERM, or the one `finish` sends, ends the process as before
        threading.Thread(target=finish, args=(signum,), name="canvas-stop", daemon=True).start()

    return handler
