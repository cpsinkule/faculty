"""Writing a kept credential file (a sign-in session) so that it is never half written or readable by anyone else.

Stdlib only: the Canvas client (canvas/web.py) and the Google sign-in (absences/google_signin.py) both keep their session
through `write_private_json`, each inside its own lock.
"""

import json
import os
import tempfile


def write_private_json(path, payload, prefix):
    """Writes `payload` (compact JSON) to `path` atomically, mode 0600: a temporary file `prefix`... beside it, flushed and
    fsynced, then renamed over it; the temporary file is removed on any failure. The caller makes the folder and holds its lock."""
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=prefix)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
