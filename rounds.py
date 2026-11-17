"""The background round loop shared by the Communications, Google Calendar, iClicker and Canvas morning-sync workers."""

from werkzeug.exceptions import Conflict

from course_storage import StorageError


def repeat(stop, step, interval, log, waiting, failed):
    """One background round every `interval` seconds until `stop`: a Conflict means another process holds the round (skip);
    storage being away is warned once per outage; any other failure is logged and the loop goes on."""
    warned = False
    while not stop.is_set():
        try:
            step()
            warned = False
        except Conflict:
            pass
        except StorageError:
            if not warned:
                log.warning(waiting)
            warned = True
        except Exception:
            log.exception(failed)
        stop.wait(interval)
