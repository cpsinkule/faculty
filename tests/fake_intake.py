"""A stand-in of the Plaud intake for the tests (everything here is synthetic; no real recording, person or token is in it).

It builds a REAL archive folder inside the test's own temporary directory (`recordings/<id>/<role>/<sha256>.<ext>`, as the intake
lays it out) and an executable stand-in COMMAND beside it that answers `list`, `show <id>`, `audio <id>`, `status` and
`check-now` in the real shapes and with the real exit statuses (0 done, 1 a problem, 2 "Sign in to Plaud again", 64 no such
command; an id it does not know answers {"error": "No recording with that id is in the archive."} with status 1). It answers
from `state.json`, which this module rewrites whenever a test changes what the intake holds, and it records every invocation
(its arguments, and whether any FACULTY_* variable reached it) in `calls.log`, so a test can assert exactly which commands
Faculty ran. It never reaches Plaud or any other host, and it holds no sign-in.

`add(...)` takes what the old loopback stand-in of Plaud took (a recording that began at `start`; `transcript` its segments or None while the archive
holds none; `audio` its bytes or None while the archive holds none), so the recordings tests change little. What a test can make
it stand for:

  * `sign_in_again(why)`: the sign-in needs making again (`status`'s sign_in.state is "again"; `check-now` exits 2);
  * `not_ready(problem)`: the archive is not ready (`status` answers ready false and exits 1; every other command exits 1);
  * `listed_at(moment)`: the intake's last listing of Plaud was then (by default it is as new as Faculty's clock, so a round is
    never stale by itself);
  * a recording with no audio or no transcript yet (`audio=None`, `transcript=None`), a transcript the intake kept a newer
    version of (set `recordings[id]["transcript"]` again: the earlier file stays beside it), a recording gone at Plaud
    (`remove(id)`: it stays in the archive with at_plaud false), one the archive never held (`forget(id)`);
  * a checksum that does not match (`corrupt(id, role)`), a path outside the archive (`outside(id, role)`), a symlink in place
    of a file (`symlink(id, role)`);
  * the command missing (a path that holds no file), hanging (`behave(hang=True)`), or answering garbage (`behave(garbage=True)`);
  * `pending=True` on `add`: the intake has not archived it yet; it appears when `check-now` is run (a round of the intake's own).

Run through `start()`, every command Faculty runs first brings the stand-in up to date (the listing's time follows the clock
the test gives it) and, after a `check-now` that succeeded, archives what was pending. The command itself is really run.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

OGG = b"OggS\x00\x02" + bytes(range(256)) * 4   # what an Ogg file begins with, then filler: never real audio

SCRIPT = r'''#!/usr/bin/python3 -S
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
state = json.load(open(os.path.join(HERE, "state.json")))
with open(os.path.join(HERE, "calls.log"), "a") as log:
    log.write(json.dumps({"argv": argv, "faculty_env": sorted(k for k in os.environ if k.startswith("FACULTY_"))}) + "\n")
how = state.get("behave", {})
if how.get("hang"):
    time.sleep(3600)
if how.get("garbage"):
    sys.stdout.write("this is not one JSON object\n")
    sys.exit(0)


def say(answer, code=0):
    json.dump(answer, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.exit(code)


if not argv or argv[0] not in ("list", "show", "audio", "status", "check-now", "round", "copy", "renew"):
    say({"error": "The commands are list, show <id>, audio <id>, status, check-now, round, copy and renew."}, 64)
command = argv[0]
if command in ("round", "copy", "renew"):
    say({"error": "Faculty never runs this command."}, 1)
if command == "status":
    say(state["status"], 0 if state["status"]["ready"] else 1)
if not state["status"]["ready"]:
    say({"error": state["status"]["problem"]}, 1)
if command == "check-now":
    say(state["check_now"]["answer"], state["check_now"]["code"])
if command == "list":
    say({"count": len(state["list"]), "recordings": state["list"]})
unknown = {"error": "No recording with that id is in the archive."}
found = argv[1] if len(argv) > 1 else ""
if command == "show":
    say(state["show"][found], 0) if found in state["show"] else say(unknown, 1)
if command == "audio":
    if found not in state["show"]:
        say(unknown, 1)
    say(state["audio"][found], 0) if found in state["audio"] else say(
        {"id": found, "error": "The archive has no audio for that recording yet."}, 1)
'''


def stamp(moment):
    """A time as the intake writes it: UTC, with a Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(content):
    return hashlib.sha256(content).hexdigest()


class Entry(dict):
    """One recording the intake holds, as a test changes it: setting a key brings the archive up to date."""

    def __init__(self, fake, *args):
        super().__init__(*args)
        self.fake = fake

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.fake.sync()


class FakeIntake:
    def __init__(self, base):
        self.base = Path(base) / "intake"
        self.archive = self.base / "archive"
        self.command = self.base / "plaud-intake"
        self.archive.mkdir(parents=True)
        self.command.write_text(SCRIPT)
        os.chmod(self.command, 0o755)
        self.recordings = {}          # id -> Entry (what the intake holds of it, and what is yet to be archived)
        self.versions = {}            # (id, role) -> [{path, sha256, size, content_type}] in the order they were kept
        self.paths = {}               # (id, role) -> a path the answer names instead (outside the archive)
        self.sign_in = {"state": "ok", "says": "", "why": ""}
        self.ready, self.problem = True, ""
        self.pinned = None            # a time the last listing is said to be at; None: as new as the clock
        self.clock = lambda: datetime.now(timezone.utc)
        self.check_now = {"code": 0, "answer": {"ran": True, "ok": True, "problem": "", "sign_in": "ok"}}
        self.how = {}
        self.lock = threading.RLock()
        self.patcher = None

    # --- what it holds ----------------------------------------------------------------------------------------------------
    def add(self, plaud_id, start, minutes=30, title="", transcript=None, audio=OGG, outline=None, marks=None, notes=None,
            start_at=True, pending=False):
        """A recording that began at `start` (an aware datetime). `transcript` is its segments ([{start_time, end_time, speaker,
        content}]) or None while the archive holds none; `audio` its bytes or None while it holds none; `notes` Plaud's notes
        ([{type, tab, content}]); `pending` that the intake has not archived it yet (a `check-now` does)."""
        self.recordings[plaud_id] = Entry(self, {"id": plaud_id, "name": title, "start": start, "minutes": minutes, "transcript": transcript,
                                                 "audio": audio, "outline": outline, "marks": marks, "notes": notes or [],
                                                 "start_at": start_at, "gone": False, "pending": pending})
        self.sync()
        return plaud_id

    def remove(self, plaud_id):
        """Plaud no longer has this recording (the instructor deleted it there): the archive keeps it, marked gone."""
        self.recordings[plaud_id]["gone"] = True

    def forget(self, plaud_id):
        """The archive never held (or no longer lists) this recording."""
        with self.lock:
            self.recordings.pop(plaud_id)
        self.sync()

    def sign_in_again(self, why="Plaud refused the sign-in."):
        self.sign_in = {"state": "again", "says": "Sign in to Plaud again", "why": why}
        self.check_now = {"code": 2, "answer": {"ran": True, "ok": False, "problem": why, "sign_in": "again"}}
        self.sync()

    def signed_in(self):
        self.sign_in = {"state": "ok", "says": "", "why": ""}
        self.check_now = {"code": 0, "answer": {"ran": True, "ok": True, "problem": "", "sign_in": "ok"}}
        self.sync()

    def not_ready(self, problem="The archive folder is not there."):
        self.ready, self.problem = False, problem
        self.sync()

    def is_ready(self):
        self.ready, self.problem = True, ""
        self.sync()

    def listed_at(self, moment):
        """The intake last listed Plaud at `moment` (None: as new as the clock, which is how it starts)."""
        self.pinned = moment
        self.sync()

    def fails_check_now(self, code=1, problem="The archive was busy."):
        self.check_now = {"code": code, "answer": {"ran": False, "why": problem} if code == 1 else {"ran": True, "ok": False, "problem": problem}}
        self.sync()

    def behave(self, **how):
        """`hang=True`: the command never answers; `garbage=True`: it answers something that is not one JSON object."""
        self.how = how
        self.sync()

    def corrupt(self, plaud_id, role):
        """The file on disk is not the one the intake gave the checksum of."""
        entry = self.versions[plaud_id, role][-1]
        Path(entry["path"]).write_bytes(Path(entry["path"]).read_bytes() + b" ")

    def outside(self, plaud_id, role):
        """The intake names this file at a path outside its archive (the bytes are right: only the place is not)."""
        entry = self.versions[plaud_id, role][-1]
        elsewhere = self.base / "outside" / f"{plaud_id}-{role}"
        elsewhere.parent.mkdir(exist_ok=True)
        shutil.copyfile(entry["path"], elsewhere)
        self.paths[plaud_id, role] = str(elsewhere)
        self.sync()

    def symlink(self, plaud_id, role):
        """The file is a symlink to the right bytes kept elsewhere: a path inside the archive that follows a link out of it."""
        entry = self.versions[plaud_id, role][-1]
        target = self.base / "outside" / f"{plaud_id}-{role}-link"
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(entry["path"], target)
        os.unlink(entry["path"])
        os.symlink(target, entry["path"])

    # --- the archive and the answers ----------------------------------------------------------------------------------------
    def _keep(self, plaud_id, role, content, kind):
        """The file for these bytes, kept under its checksum; an earlier version of the role stays beside it."""
        digest = sha(content)
        have = self.versions.setdefault((plaud_id, role), [])
        if have and have[-1]["sha256"] == digest:
            return
        folder = self.archive / "recordings" / plaud_id / role
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{digest}{'.ogg' if kind == 'audio/ogg' else '.json'}"
        if not path.exists() or path.is_symlink():
            path.unlink(missing_ok=True)
            path.write_bytes(content)
        have.append({"path": str(path), "sha256": digest, "size": len(content), "content_type": kind})

    def _files(self, found):
        """What the archive holds of one recording now, as bytes by role."""
        plaud_id, made = found["id"], {}
        made["answer"] = (json.dumps({"id": plaud_id, "name": found["name"]}).encode(), "application/json")
        for role in ("transcript", "outline", "marks"):
            if found[role] is not None:
                made[role] = (json.dumps(found[role]).encode(), "application/json")
        if found["notes"]:
            made["notes"] = (json.dumps([{"content": note["content"], "tab": note.get("tab", "Summary"), "title": note.get("tab", "Summary"),
                                          "type": note.get("type", "auto_sum_note")} for note in found["notes"]]).encode(), "application/json")
        if found["audio"] is not None:
            made["audio"] = (found["audio"], "audio/ogg")
        return made

    def _row(self, found, roles):
        began = found["start"]
        kept = "audio" in roles and "transcript" in roles
        return {"id": found["id"], "title": found["name"], "started_at": stamp(began), "start_known": bool(found["start_at"]),
                "duration_ms": found["minutes"] * 60000, "first_seen_at": stamp(began), "at_plaud": not found["gone"],
                "gone_at": "2026-11-12T00:00:00Z" if found["gone"] else "", "status": "kept" if kept else "waiting",
                "detail": "" if kept else "Plaud has not finished it yet.", "has": sorted(roles)}

    def sync(self):
        """Bring the archive and the stand-in's answers up to date with what a test has made the intake hold."""
        with self.lock:
            rows, shows, audios = [], {}, {}
            for plaud_id, found in sorted(self.recordings.items(), key=lambda item: item[1]["start"], reverse=True):
                if found["pending"]:
                    continue
                for role, (content, kind) in self._files(found).items():
                    self._keep(plaud_id, role, content, kind)
                roles = set(self._files(found))
                row = self._row(found, roles)
                rows.append(row)

                def shown(entry, plaud_id=plaud_id, role=None):
                    return {**{k: v for k, v in entry.items()}, "path": self.paths.get((plaud_id, role), entry["path"]),
                            "kept_at": "2026-11-12T00:00:00Z"}
                files = {role: {**shown(self.versions[plaud_id, role][-1], plaud_id, role),
                                "earlier": [shown(entry, plaud_id, role) for entry in self.versions[plaud_id, role][:-1]]}
                         for role in sorted(roles)}
                shows[plaud_id] = {**row, "checked_at": "2026-11-12T00:00:00Z", "next_check_at": "", "skipped": [],
                                   "listed": {"id": plaud_id, "name": found["name"]}, "files": files}
                if "audio" in files:
                    audios[plaud_id] = {"id": plaud_id, **{k: v for k, v in files["audio"].items() if k != "earlier"}}
            at = self.pinned or self.clock()
            status = {"mode": "own", "archive": str(self.archive), "ready": self.ready, "problem": self.problem,
                      "sign_in": {"mode": "own", "renews": True, "file": "tokens.json", **self.sign_in, "expires_at": ""},
                      "round": {}, "listing": {"at": stamp(at), "complete": True, "full": False, "listed": len(rows), "unread": 0},
                      "match": {}, "recordings": {"count": len(rows)}, "copy": {}}
            state = {"status": status, "list": rows, "show": shows, "audio": audios, "check_now": self.check_now, "behave": self.how}
            temporary = self.base / "state.json.part"
            temporary.write_text(json.dumps(state))
            os.replace(temporary, self.base / "state.json")

    # --- running ----------------------------------------------------------------------------------------------------------
    def start(self, clock=None):
        """Faculty's `subprocess` is the real one but for this: each command first brings the stand-in up to date (the listing's
        time follows `clock`), and a `check-now` that succeeded archives what was pending. Returns self."""
        if clock:
            self.clock = clock
        self.sync()
        real = subprocess

        def run(arguments, *args, **kwargs):
            if Path(arguments[0]) == self.command:
                self.sync()
            done = real.run(arguments, *args, **kwargs)
            if Path(arguments[0]) == self.command and arguments[1:2] == ["check-now"] and done.returncode == 0:
                with self.lock:
                    for found in self.recordings.values():
                        found["pending"] = False
                    self.pinned = None
                self.sync()
            return done

        self.patcher = mock.patch("recordings.intake.subprocess", types.SimpleNamespace(
            run=run, TimeoutExpired=real.TimeoutExpired, DEVNULL=real.DEVNULL))
        self.patcher.start()
        return self

    def stop(self):
        if self.patcher:
            self.patcher.stop()
            self.patcher = None

    # --- what a test asks of the record -------------------------------------------------------------------------------------
    def calls(self):
        """[[argument, ...]] of every command run, in order."""
        log = self.base / "calls.log"
        return [json.loads(line)["argv"] for line in log.read_text().splitlines()] if log.exists() else []

    def commands(self):
        """The names of the commands run, in order (`list`, `show`, ...)."""
        return [call[0] if call else "" for call in self.calls()]

    def asked(self, command=None):
        """[(command, id or None)] of every command run, or of one command."""
        return [(call[0], call[1] if len(call) > 1 else None) for call in self.calls() if command in (None, call[0])]

    def leaked(self):
        """Every FACULTY_* variable any run of the command could see (there should be none)."""
        log = self.base / "calls.log"
        return sorted({name for line in log.read_text().splitlines() for name in json.loads(line)["faculty_env"]}) if log.exists() else []

    def clear(self):
        (self.base / "calls.log").write_text("")


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
