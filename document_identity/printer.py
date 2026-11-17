"""Send to printer (DOCUMENTS "SEND TO PRINTER"): the instructor's explicit sends of existing print files to the office printer.

The printer (a Brother
MFC-L5915DW on the home network) takes PDFs over IPP itself, so Faculty speaks IPP to it (`ipp.py`); ml-box needs no CUPS queue.
A send prints exactly the verified files the Print dialogs offer: one job's PDF, a stack's interleaved PDF, or a seating chart's
whole-room PDF. Only the instructor's Send to printer approves it, bound to those jobs' verified outputs, the sides and the
printer. The worker records the printer's receipt before completion and never resends an interrupted or unconfirmed send:
printing twice duplicates the printed page identities.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import base64
import ipaddress
import math
import os
import uuid
from urllib.parse import urlsplit

from werkzeug.exceptions import Conflict, ServiceUnavailable

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import encoded, sha
from . import process
from .ipp import Printer, IppError, PRINTER_STATES
from .media import DEFAULT_MEDIA
from .service import EMAILED_COPY

SIDES = ("one-sided", "two-sided-long-edge")
# The printer holds a whole PDF before printing it and takes at most 62.5 MB (its `pdf-k-octets-supported`). A larger file goes as
# several documents of one printer job, cut between copies; ~40 MB each leaves the printer room.
DOCUMENT_BYTES, PRINTER_BYTES = 40_000_000, 60_000_000
UNCONFIRMED = "The printer did not confirm the whole job. Some pages may have printed: check the printer before sending again."


def validate_batch_ids(batch_ids):
    if (not isinstance(batch_ids, list) or not 1 <= len(batch_ids) <= 26
            or any(not isinstance(item, str) for item in batch_ids) or len(set(batch_ids)) != len(batch_ids)):
        raise ValueError("Choose the print jobs to send.")


@dataclass
class PrinterConfig:
    uri: str = ""

    @classmethod
    def environment(cls):
        return cls(os.environ.get("FACULTY_PRINTER_URI", "").strip())

    def printer(self):
        """The configured printer, or None. Only plain `ipp://` to a host on the home network (a private address or a
        `.local` name) is accepted: print files never leave the network."""
        try:
            parsed = urlsplit(self.uri)
            if (parsed.scheme != "ipp" or not parsed.hostname or parsed.username or parsed.password or parsed.query
                    or parsed.fragment or not parsed.path.startswith("/")):
                return None
            host = parsed.hostname
            try:
                if not ipaddress.ip_address(host).is_private:
                    return None
            except ValueError:
                if not host.endswith(".local"):
                    return None
            return Printer(host, parsed.port or 631, parsed.path)
        except ValueError:
            return None


class PrinterSends:
    def __init__(self, documents, config=None):
        self.documents = documents
        self.rosters, self.path = documents.rosters, documents.path
        self.config = config or PrinterConfig.environment()

    # ---- what a send prints ---------------------------------------------------------------------------------------------

    def _batches(self, kind, batch_ids, chart_id=None):
        """The ready, current jobs a send prints, in the order its file holds them; the same checks the files themselves make."""
        if kind not in ("job", "stack", "room"):
            raise ValueError("Choose a print job, a stack or a room file to send.")
        validate_batch_ids(batch_ids)
        if kind == "job" and len(batch_ids) != 1:
            raise ValueError("A single print job is sent on its own.")
        if (kind == "room") != bool(chart_id):
            raise ValueError("Only a room file names its seating chart.")
        batches = [self.documents.get_batch(batch_id) for batch_id in batch_ids]
        waiting = [b for b in batches if b["status"] != "ready" or b["superseded_at"]]
        if waiting:
            raise Conflict("Only ready, current print jobs can be sent to the printer.")
        with connect(self.path) as db:
            if any(self.documents.emailed_copy(db, b["id"]) for b in batches):
                raise Conflict(EMAILED_COPY)
        if kind == "stack":
            if len(batches) > 26 or len({self.documents.owner(b) for b in batches}) != 1 or len({b["assignment_id"] for b in batches}) != 1 \
                    or len({b["assignment_version_id"] for b in batches}) != len(batches):
                raise ValueError("A stack needs one print job per version of one assignment, all printed for the same section or term.")
            batches.sort(key=lambda b: (b["version_label"], b["id"]))
        if kind == "room":
            seating = self.documents.seating
            if seating is None:
                raise Conflict("Printing is unavailable.")
            known = {b["id"] for b in seating.get(chart_id)["print_batches"]}
            if len(batches) > 20 or any(b["id"] not in known for b in batches) or len({b["assignment_version_id"] for b in batches}) != 1:
                raise ValueError("Room output needs print jobs of one seating chart and one exam version.")
            batches.sort(key=lambda b: (b["section_number"], b["id"]))
        return batches

    @staticmethod
    def _copies(kind, batches):
        """Each printed copy's page count, in file order (a stack alternates its versions copy by copy, `section_stack`)."""
        if kind == "stack":
            return [b["page_count"] for k in range(1, max(b["quantity"] for b in batches) + 1) for b in batches if k <= b["quantity"]]
        return [b["page_count"] for b in batches for _ in range(b["quantity"])]

    def _facts(self, kind, batches):
        copies = self._copies(kind, batches)
        with connect(self.path) as db:
            pages = sum(len(self.documents.manifest(db, b["id"])) for b in batches)
        known = sum(copies) == pages  # copy boundaries are only trusted when they add up to the file
        return {"pages": pages, "copies": len(copies) if known else None,
                "two_sided": known and all(count % 2 == 0 for count in copies), "boundaries": copies if known else None}

    def content(self, kind, batches, chart_id=None):
        ids = [b["id"] for b in batches]
        if kind == "job":
            return self.documents.download(ids[0])
        if kind == "stack":
            return self.documents.section_stack(ids)
        return self.documents.seating.room_export(chart_id, ids, "print", DEFAULT_MEDIA)

    @staticmethod
    def command_hash(kind, chart_id, outputs, sides, printer_uri):
        return sha(encoded({"kind": kind, "seating_chart_id": chart_id, "outputs": outputs, "sides": sides, "printer": printer_uri}).encode())

    # ---- routes ---------------------------------------------------------------------------------------------------------

    def options(self, kind, batch_ids, chart_id=None):
        batches = self._batches(kind, batch_ids, chart_id)
        facts = self._facts(kind, batches)
        printer = self.config.printer()
        status = None
        if printer:
            try:
                found = printer.attributes()
                reasons = [r for r in found.get("printer-state-reasons", []) if r and r != "none"]
                status = {"reachable": True, "name": (found.get("printer-make-and-model") or [""])[0],
                          "state": PRINTER_STATES.get((found.get("printer-state") or [None])[0], "unknown"),
                          "reasons": reasons, "accepting": bool((found.get("printer-is-accepting-jobs") or [True])[0])}
            except (IppError, OSError):
                status = {"reachable": False}
        with connect(self.path) as db:
            marks = ",".join("?" * len(batches))
            history = [dict(r) for r in db.execute(f"""SELECT DISTINCT s.id,s.kind,s.sides,s.page_count,s.status,s.error,s.created_at,s.updated_at
                FROM print_printer_sends s JOIN print_printer_send_batches l ON l.send_id=s.id WHERE l.batch_id IN ({marks})
                ORDER BY s.created_at DESC,s.id DESC LIMIT 30""", [b["id"] for b in batches])]
        return {"configured": printer is not None, "printer_uri": printer.uri if printer else "", "printer": status,
                "pages": facts["pages"], "copies": facts["copies"], "two_sided_allowed": facts["two_sided"],
                "outputs": [{"id": b["id"], "output_sha256": b["output_sha256"]} for b in batches], "sends": history}

    def submit(self, kind, batch_ids, outputs, sides, request_id, chart_id=None, actor="workspace"):
        try:
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Sending to the printer requires a unique request ID.") from None
        if sides not in SIDES:
            raise ValueError("Choose one-sided or two-sided printing.")
        validate_batch_ids(batch_ids)
        printer = self.config.printer()
        if (not isinstance(outputs, list) or len(outputs) > 26 or not all(isinstance(o, dict)
                and isinstance(o.get("id"), str) and isinstance(o.get("output_sha256"), str) for o in outputs)):
            raise ValueError("Reopen Send to printer before sending.")
        posted = {o.get("id"): o.get("output_sha256") for o in outputs}
        if len(posted) != len(outputs):
            raise ValueError("Choose each print output once.")
        digest_of = lambda uri: self.command_hash(kind, chart_id, [[b, posted.get(b)] for b in batch_ids], sides, uri)
        with connect(self.path) as db:
            existing = db.execute("SELECT * FROM print_printer_sends WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["command_sha256"] != digest_of(existing["printer_uri"]):
                    raise Conflict("This request ID already approves a different printer send.")
                return self.public(existing)
        if printer is None:
            raise ServiceUnavailable("No printer is set up. You can still open the print PDF and print it yourself.")
        batches = self._batches(kind, batch_ids, chart_id)
        if posted != {b["id"]: b["output_sha256"] for b in batches}:
            raise Conflict("The print files changed. Reopen Send to printer before sending.")
        facts = self._facts(kind, batches)
        if sides != "one-sided" and not facts["two_sided"]:
            raise ValueError("Two-sided printing needs an even number of pages in every copy, or copies would share a sheet.")
        digest = digest_of(printer.uri)
        with ExitStack() as locks:
            for batch in batches:
                locks.enter_context(self.documents.batch_lock(batch["id"]))
            with connect(self.path) as db:
                existing = db.execute("SELECT * FROM print_printer_sends WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    if existing["command_sha256"] != digest:
                        raise Conflict("This request ID already approves a different printer send.")
                    return self.public(existing)
                for batch in batches:
                    row = db.execute("SELECT status,superseded_at,output_sha256 FROM print_batches WHERE id=?", (batch["id"],)).fetchone()
                    if row["status"] != "ready" or row["superseded_at"] or row["output_sha256"] != batch["output_sha256"]:
                        raise Conflict("Only ready, current print jobs can be sent to the printer.")
                now, send_id = timestamp(), str(uuid.uuid4())
                db.execute("""INSERT INTO print_printer_sends(id,kind,seating_chart_id,printer_uri,sides,page_count,command_sha256,request_id,
                    approved_by,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                           (send_id, kind, chart_id, printer.uri, sides, facts["pages"], digest, request_id, actor, now, now))
                for position, batch in enumerate(batches):
                    db.execute("INSERT INTO print_printer_send_batches(send_id,batch_id,output_sha256,position) VALUES(?,?,?,?)",
                               (send_id, batch["id"], batch["output_sha256"], position))
                    self.documents.event(db, "printer_send_approved", actor, {"send_id": send_id, "printer": printer.uri, "sides": sides,
                                                                              "command_sha256": digest}, batch_id=batch["id"])
                return self.public(db.execute("SELECT * FROM print_printer_sends WHERE id=?", (send_id,)).fetchone())

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "kind", "sides", "page_count", "status", "error", "created_at", "updated_at")}

    # ---- worker ---------------------------------------------------------------------------------------------------------

    def process_one(self):
        with self.documents.worker_lock():
            with self.rosters.lock(), ExitStack() as locks:
                with connect(self.path) as db:
                    owners = [r[0] for r in db.execute("""SELECT DISTINCT COALESCE(b.section_id,b.term_id) owner FROM print_printer_sends s
                        JOIN print_printer_send_batches l ON l.send_id=s.id JOIN print_batches b ON b.id=l.batch_id
                        WHERE s.status IN ('pending','sending') ORDER BY owner""")]
                for owner in owners:
                    locks.enter_context(self.documents.files.owner_lock(owner))
                with connect(self.path) as db:
                    interrupted = db.execute("SELECT * FROM print_printer_sends WHERE status='sending'").fetchall()
                    for row in interrupted:
                        status = "accepted" if row["printer_receipt_json"] else "uncertain"
                        self._mark(db, dict(row), status, "" if status == "accepted" else
                                   "Sending was interrupted. Some pages may have printed: check the printer before sending again.")
                    row = db.execute("SELECT * FROM print_printer_sends WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
                    if row is None:
                        return bool(interrupted)
                    job = dict(row)
                    job["outputs"] = [dict(r) for r in db.execute(
                        "SELECT batch_id,output_sha256 FROM print_printer_send_batches WHERE send_id=? ORDER BY position", (job["id"],))]
                    db.execute("UPDATE print_printer_sends SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?", (timestamp(), job["id"]))
            try:
                printer = self.config.printer()
                if printer is None or printer.uri != job["printer_uri"]:
                    raise ValueError("The printer this send was approved for is no longer set up. Nothing was printed.")
                batches = self._batches(job["kind"], [o["batch_id"] for o in job["outputs"]], job["seating_chart_id"])
                outputs = [[b["id"], b["output_sha256"]] for b in batches]
                if outputs != [[o["batch_id"], o["output_sha256"]] for o in job["outputs"]] or self.command_hash(
                        job["kind"], job["seating_chart_id"], outputs, job["sides"], job["printer_uri"]) != job["command_sha256"]:
                    raise ValueError("The approved printer send failed verification. Nothing was printed.")
                facts = self._facts(job["kind"], batches)
                content = self.content(job["kind"], batches, job["seating_chart_id"])
                documents = self._documents(content, facts["boundaries"])
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) and "Nothing was printed" in str(error) else \
                    "The print files are unavailable, superseded or failed verification. Nothing was printed."
                self.finish(job, "failed", message)
                return True
            try:
                receipt = self.send(printer, job, batches, content, documents)
            except IppError as error:
                self.finish(job, "uncertain" if error.sent else "failed", UNCONFIRMED if error.sent else
                            f"{error} Nothing was printed. Check that the printer is on and connected, then send again.")
            except Exception:
                self.finish(job, "uncertain", UNCONFIRMED)
            else:
                # The printer's acknowledgement commits before completion; recovery uses it without sending again.
                with self.documents.batch_lock(job["outputs"][0]["batch_id"]), connect(self.path) as db:
                    db.execute("UPDATE print_printer_sends SET printer_receipt_json=?,updated_at=? WHERE id=?",
                               (encoded(receipt), timestamp(), job["id"]))
                self.finish(job, "accepted", "")
            return True

    def _documents(self, content, boundaries):
        """The file as the printer's documents: whole when small enough, else cut between copies into parts of ~40 MB."""
        if len(content) <= DOCUMENT_BYTES or not boundaries and len(content) <= PRINTER_BYTES:
            return [content]
        if not boundaries:
            raise ValueError("This file is too large for the printer in one piece and its copies cannot be told apart.")
        pages = sum(boundaries)
        parts = math.ceil(len(content) / DOCUMENT_BYTES)
        ends, at = [], 0
        for count in boundaries:
            at += count
            ends.append(at)
        cuts = sorted({min(ends, key=lambda end: abs(end - pages * i / parts)) for i in range(1, parts)} - {pages})
        ranges = list(zip([0, *cuts], [*cuts, pages]))
        source = [base64.b64encode(content).decode()]
        out = [process.run("reorder", b"", {"sources": source, "pages": [[0, p] for p in range(a, b)]}, timeout=300)[0] for a, b in ranges]
        if any(len(part) > PRINTER_BYTES for part in out):
            raise ValueError("A part of this file is too large for the printer.")
        return out

    def send(self, printer, job, batches, content, documents):
        first = batches[0]
        where = "All sections" if not first.get("section_id") or job["kind"] == "room" and len(batches) > 1 else f"Section {first['section_number']}"
        name = " · ".join(part for part in (first.get("code"), first.get("title"), {"stack": "every version", "room": "room file"}.get(job["kind"], ""), where) if part)
        job_id = printer.create_job(name, job["sides"])
        try:
            for index, part in enumerate(documents):
                try:
                    printer.send_document(job_id, part, index == len(documents) - 1, f"{name} · part {index + 1} of {len(documents)}")
                except IppError as error:
                    error.sent = error.sent or index > 0
                    raise
        except Exception:
            try:
                printer.cancel(job_id)
            except Exception:
                pass
            raise
        return {"printer_uri": printer.uri, "job_id": job_id, "documents": len(documents), "output_sha256": sha(content),
                "pages": job["page_count"], "sent_at": timestamp()}

    def _mark(self, db, job, status, error):
        db.execute("UPDATE print_printer_sends SET status=?,error=?,updated_at=? WHERE id=?", (status, error, timestamp(), job["id"]))
        batch_ids = [r[0] for r in db.execute("SELECT batch_id FROM print_printer_send_batches WHERE send_id=? ORDER BY position", (job["id"],))]
        for batch_id in batch_ids:
            self.documents.event(db, "printer_send_" + status, "worker", {"send_id": job["id"]}, batch_id=batch_id)

    def finish(self, job, status, error):
        with self.documents.batch_lock(job["outputs"][0]["batch_id"]), connect(self.path) as db:
            self._mark(db, job, status, error)
