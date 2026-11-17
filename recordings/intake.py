"""The Plaud intake as Faculty reads it (docs/decisions/recordings.md, RECORDINGS "Plaud").

FACULTY ASKS PLAUD NOTHING. The Plaud intake (`~/code/plaud-intake`) is the one program that talks to Plaud: it archives every
recording (Plaud's answer, transcript, outline, marks, notes and audio, each under its sha256), holds and renews the Plaud sign-in,
and is asked through one command that answers one JSON object. This module runs that command and reads the files it names, and
does nothing else: it imports no network module, and it holds no token, no link and no address of Plaud's.

THE COMMAND is the one `FACULTY_PLAUD_INTAKE` names (unset: Plaud is not set up for this Faculty instance), run as an argument
list and never through a shell, with a time limit, its answer read as one JSON object, and with Faculty's environment but no
`FACULTY_*` variable. Only five commands are ever run: `list`, `show <id>`, `audio <id>`, `status` (these four ask Plaud nothing)
and `check-now` (one of the intake's own rounds against Plaud, run only on a round the instructor asked for). Never `round`,
`copy` or `renew`: the intake's timers run the first two and its own sign-in the third. Exit status: 0 done, 1 a problem, 2 "Sign
in to Plaud again", 64 no such command.

EVERY FILE IT READS for the intake must lie inside the archive folder `status` names, is opened without following a symlink at
its last component, is no larger than Faculty keeps, and is read whole and checked against the sha256 the intake gave. A path
outside the archive or a checksum that differs is refused (`Refused`), and nothing of that file is kept. No message here carries a
file's contents.
"""
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone

COMMANDS = ("list", "show", "audio", "status", "check-now")   # the only commands Faculty ever runs
ASK = 60                           # seconds an answer of list, show, audio or status is waited for
ROUND = 20 * 60                    # ... and one round of the intake's own against Plaud (check-now)
PAGE_ASK = 15                      # ... and the page of one recording, which only asks where its audio is
OUT_LIMIT = 32 * 1024 * 1024       # the largest answer read from the command
JSON_LIMIT = 32 * 1024 * 1024      # the largest transcript, outline, marks or notes file read (a three-hour transcript is well under 1 MB)
AUDIO_LIMIT = 2 * 1024 ** 3        # the largest audio served (Ogg, about 15 MB an hour)
CHUNK = 1024 * 1024
SIGN_IN = "Sign in to Plaud again"
UNKNOWN = "No recording with that id"   # how the intake begins its answer for an id its archive does not hold
NOT_SET_UP = "Plaud is not set up for this Faculty instance."


class IntakeError(Exception):
    """The Plaud intake could not be asked or read now (missing, failing, slow, an answer Faculty cannot read). The next round
    tries again."""


class NotSetUp(IntakeError):
    """This Faculty instance does not name the intake's command."""


class SignInAgain(IntakeError):
    """The intake says Plaud wants the instructor to sign in again."""


class Gone(IntakeError):
    """The intake's archive holds no recording with that ID."""


class Refused(IntakeError):
    """A file the intake named is outside its archive, is not an ordinary file, is too large, or does not match its checksum.
    Nothing of it is kept."""


# -- What the intake's answers say -------------------------------------------------------------------------------------------------
def instant(value):
    """The UTC instant a time of the intake's means, or None (it writes "2026-11-13T19:48:40Z"; a zone mark is honoured, a bare time
    is UTC)."""
    text = str(value or "").strip() if not isinstance(value, bool) else ""
    if not text:
        return None
    try:
        found = datetime.fromisoformat(text.replace(" ", "T", 1).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (found if found.tzinfo else found.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def listed(item):
    """What Faculty holds of one row of the intake's list (or of its answer for one recording): {plaud_id, title, started_at (a UTC
    datetime), start_known, duration_ms, at_plaud}, or None for a row without an ID or a start."""
    if not isinstance(item, dict):
        return None
    identifier = item.get("id")
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200 or identifier != identifier.strip():
        return None
    began = instant(item.get("started_at"))
    if not began:
        return None
    duration = item.get("duration_ms")
    duration = int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0 else 0
    return {"plaud_id": identifier, "title": " ".join(str(item.get("title") or "").split())[:500], "started_at": began,
            "start_known": bool(item.get("start_known")), "duration_ms": duration, "at_plaud": bool(item.get("at_plaud", True))}


def said(answer, *names):
    """What the intake says of why it could not do something: its own words, short (never a file's contents)."""
    for name in names:
        value = answer.get(name) if isinstance(answer, dict) else None
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:300]
    return ""


# -- The command ---------------------------------------------------------------------------------------------------------------------
class IntakeClient:
    def __init__(self, command=None):
        self.command = (os.environ.get("FACULTY_PLAUD_INTAKE", "") if command is None else command).strip()

    def setup(self):
        """'' when capture can run, else what is missing, from configuration alone (nothing is run)."""
        return "" if self.command else NOT_SET_UP

    @staticmethod
    def environment():
        """Faculty's environment for the command, without any `FACULTY_*` variable."""
        return {name: value for name, value in os.environ.items() if not name.startswith("FACULTY_")}

    def run(self, *arguments, wait=None, accept=(0,)):
        """(exit status, the one JSON object the command answered). A status not in `accept`, a command that is missing, fails,
        is slow or answers something else is an IntakeError that says which; a 2 is `SignInAgain`."""
        if not self.command:
            raise NotSetUp(NOT_SET_UP)
        wait = wait or ASK
        if not arguments or arguments[0] not in COMMANDS:
            raise ValueError("Faculty runs only the Plaud intake's list, show, audio, status and check-now.")
        try:
            done = subprocess.run([self.command, *arguments], capture_output=True, timeout=wait, shell=False, check=False,
                                  stdin=subprocess.DEVNULL, env=self.environment())
        except subprocess.TimeoutExpired:
            raise IntakeError(f"The Plaud intake did not answer {arguments[0]} in time.") from None
        except (OSError, ValueError) as error:
            raise IntakeError(f"The Plaud intake could not be run ({getattr(error, 'strerror', '') or type(error).__name__}).") from None
        if len(done.stdout) > OUT_LIMIT:
            raise IntakeError("The Plaud intake's answer was larger than Faculty reads.")
        try:
            answer = json.loads(done.stdout.decode("utf-8"))
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise IntakeError(f"The Plaud intake did not answer {arguments[0]} in a shape Faculty reads (it exited {done.returncode}).")
        if done.returncode in accept:
            return done.returncode, answer
        why = said(answer, "error", "problem", "why")
        if done.returncode == 2:
            raise SignInAgain(why or SIGN_IN)
        if done.returncode == 64:
            raise IntakeError("The Plaud intake does not have the command Faculty asked it.")
        if why.startswith(UNKNOWN):
            raise Gone("The archive has no recording with that ID.")
        raise IntakeError(f"The Plaud intake could not do {arguments[0]}{': ' + why if why else ''}.")

    # -- The five commands --
    def status(self):
        """The intake's `status`: {archive, ready, problem, sign_in: {state, says, why}, listing: {at}, ...}. It answers even when
        the archive is not ready (then `ready` is false and `problem` says why)."""
        _, answer = self.run("status", accept=(0, 1))
        if not isinstance(answer.get("ready"), bool) or not isinstance(answer.get("sign_in"), dict):
            raise IntakeError("The Plaud intake's status was not in a shape Faculty reads.")
        return answer

    def check_now(self):
        """One round of the intake's own against Plaud, now (it waits up to ROUND). Returns its answer; a round that did not run
        or did not finish is an IntakeError that says why."""
        code, answer = self.run("check-now", wait=ROUND, accept=(0, 1, 2))
        if code == 2:
            raise SignInAgain(said(answer, "problem", "error") or SIGN_IN)
        if code == 1 or answer.get("ran") is False:
            raise IntakeError(said(answer, "why", "problem", "error") or "The Plaud intake's round did not finish.")
        return answer

    def recordings(self, start, end):
        """What the archive lists that began between two instants (UTC datetimes): [`listed` rows], each recording once,
        including one Plaud no longer has (`at_plaud` false)."""
        _, answer = self.run("list")
        items = answer.get("recordings")
        if not isinstance(items, list):
            raise IntakeError("The Plaud intake's list was not in the shape Faculty reads.")
        found, seen = [], set()
        for item in items:
            entry = listed(item)
            if entry and entry["plaud_id"] not in seen and start <= entry["started_at"] <= end:
                seen.add(entry["plaud_id"])
                found.append(entry)
        return found

    def show(self, plaud_id):
        """One recording as the archive holds it: its row and `files` ({role: {path, sha256, size, content_type, ...}}). Only for a
        recording filed as Faculty's. An ID the archive does not know is `Gone`."""
        _, answer = self.run("show", str(plaud_id))
        if not isinstance(answer.get("files"), dict):
            raise IntakeError("The Plaud intake's answer for a recording was not in the shape Faculty reads.")
        return answer

    def audio(self, plaud_id, wait=None):
        """{path, sha256, size, content_type} of the archived audio, or an IntakeError (none yet, or none such)."""
        _, answer = self.run("audio", str(plaud_id), wait=wait)
        if not all(isinstance(answer.get(name), str) for name in ("path", "sha256")) or not isinstance(answer.get("size"), int):
            raise IntakeError("The Plaud intake's answer for the audio was not in the shape Faculty reads.")
        kind = str(answer.get("content_type") or "").split(";")[0].strip().lower()
        return {"path": answer["path"], "sha256": answer["sha256"], "size": answer["size"],
                "content_type": kind if kind.startswith("audio/") and len(kind) <= 100 else "application/octet-stream"}

    # -- A file the intake names --
    @staticmethod
    def read(entry, archive, limit):
        """The bytes of one file of the archive, whole, checked: its path (absolute) lies inside `archive` (the folder `status`
        named), it is opened without following a symlink at its last component, it is an ordinary file no larger than `limit`,
        and it hashes to the sha256 the intake gave. Anything else is `Refused`."""
        try:
            path, wanted = str(entry["path"]), str(entry["sha256"]).lower()
            root = os.path.realpath(str(archive))
        except (KeyError, TypeError):
            raise Refused("The Plaud intake named a file Faculty cannot read. Nothing of it was kept.") from None
        name = os.path.basename(path)
        if (not os.path.isabs(path) or not str(archive) or not os.path.isabs(str(archive)) or not name or name in (".", "..")
                or len(wanted) != 64 or any(c not in "0123456789abcdef" for c in wanted)):
            raise Refused("The Plaud intake named a file Faculty cannot read. Nothing of it was kept.")
        folder = os.path.realpath(os.path.dirname(path))
        if folder != root and not folder.startswith(root.rstrip(os.sep) + os.sep):
            raise Refused("The Plaud intake named a file outside its archive. It was not read and nothing of it was kept.")
        try:
            descriptor = os.open(os.path.join(folder, name), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise Refused("A file the Plaud intake named could not be opened. Nothing of it was kept.") from None
        try:
            found = os.fstat(descriptor)
            if not stat.S_ISREG(found.st_mode) or found.st_size > limit:
                raise Refused("A file the Plaud intake named is not one Faculty keeps (not a file, or larger than it keeps). Nothing of it was kept.")
            digest, parts, size = hashlib.sha256(), [], 0
            while True:
                chunk = os.read(descriptor, CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise Refused("A file the Plaud intake named is larger than Faculty keeps. Nothing of it was kept.")
                digest.update(chunk)
                parts.append(chunk)
        except OSError:
            raise Refused("A file the Plaud intake named could not be read. Nothing of it was kept.") from None
        finally:
            os.close(descriptor)
        if digest.hexdigest() != wanted:
            raise Refused("A file in the Plaud archive did not match its checksum. It was not used and nothing of it was kept.")
        return b"".join(parts)

