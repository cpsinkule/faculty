"""A small IPP/1.1 client (RFC 8010/8011) for the office printer: just the operations Send to printer needs.

The printer takes a PDF itself (IPP Everywhere / AirPrint), so ml-box needs no CUPS queue or driver. Only plain `ipp://` on the
home network is spoken; nothing here retries.
"""
import http.client
import itertools
import struct

PRINT_JOB, CREATE_JOB, SEND_DOCUMENT, CANCEL_JOB, GET_PRINTER_ATTRIBUTES = 0x02, 0x05, 0x06, 0x08, 0x0B
OPERATION, JOB, END, PRINTER_GROUP, UNSUPPORTED = 0x01, 0x02, 0x03, 0x04, 0x05
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
RANGE, BEGIN_COLLECTION, END_COLLECTION = 0x33, 0x34, 0x37
TEXT, NAME, KEYWORD, URI, CHARSET, LANGUAGE, MIME, MEMBER = 0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49, 0x4A
PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}
_ids = itertools.count(1)


class IppError(Exception):
    """`sent` is True once any document bytes may have reached the printer."""
    def __init__(self, message, sent=False, status=None):
        super().__init__(message)
        self.sent, self.status = sent, status


def _value(tag, value):
    if tag in (INTEGER, ENUM):
        return struct.pack(">i", value)
    if tag == BOOLEAN:
        return b"\x01" if value else b"\x00"
    return value.encode("utf-8")


def encode(operation, attributes, job_attributes=(), request_id=None):
    """The request header: (name, tag, value) triples, the operation group first (charset and language lead, as required)."""
    out = bytearray(struct.pack(">BBHI", 1, 1, operation, request_id or next(_ids)))
    for group, items in ((OPERATION, [("attributes-charset", CHARSET, "utf-8"), ("attributes-natural-language", LANGUAGE, "en"),
                                      *attributes]), (JOB, job_attributes)):
        if not items:
            continue
        out.append(group)
        for name, tag, value in items:
            raw, key = _value(tag, value), name.encode("ascii")
            out += struct.pack(">BH", tag, len(key)) + key + struct.pack(">H", len(raw)) + raw
    out.append(END)
    return bytes(out)


def decode(data):
    """(status code, {group tag: {name: [values]}}); collections are skipped, unknown value types kept as bytes."""
    if len(data) < 9:
        raise IppError("The printer sent a short IPP answer.")
    _, _, status, _ = struct.unpack(">BBHI", data[:8])
    groups, group, name, depth, at = {}, None, None, 0, 8
    while at < len(data):
        tag = data[at]; at += 1
        if tag == END:
            if depth:
                raise IppError("The printer sent a malformed IPP collection.")
            return status, groups
        if tag < 0x10:
            group = groups.setdefault(tag, {}); name = None
            continue
        if at + 2 > len(data):
            raise IppError("The printer sent a malformed IPP answer.")
        (size,) = struct.unpack(">H", data[at:at + 2]); at += 2
        if at + size + 2 > len(data):
            raise IppError("The printer sent a truncated IPP attribute.")
        key = data[at:at + size].decode("utf-8", "replace"); at += size
        (size,) = struct.unpack(">H", data[at:at + 2]); at += 2
        if at + size > len(data):
            raise IppError("The printer sent a truncated IPP value.")
        raw = data[at:at + size]; at += size
        if tag == BEGIN_COLLECTION:
            depth += 1
        if depth:
            if tag == END_COLLECTION:
                depth -= 1
            continue
        if key:
            name = key
        if group is None or name is None:
            continue
        value = (struct.unpack(">i", raw)[0] if tag in (INTEGER, ENUM) and len(raw) == 4 else bool(raw[:1] == b"\x01") if tag == BOOLEAN
                 else struct.unpack(">ii", raw) if tag == RANGE and len(raw) == 8 else raw.decode("utf-8", "replace") if 0x40 <= tag < 0x60
                 else None if tag < 0x20 else raw)
        group.setdefault(name, []).append(value)
    raise IppError("The printer sent an incomplete IPP answer.")


class Printer:
    def __init__(self, host, port, path, timeout=30):
        self.host, self.port, self.path, self.timeout = host, port, path, timeout
        self.uri = f"ipp://{host}{'' if port == 631 else f':{port}'}{path}"

    def call(self, header, document=b"", timeout=None, document_sent=False):
        """POST one IPP request; `document_sent` says whether a failure here may have left pages printing."""
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        try:
            connection.putrequest("POST", self.path)
            connection.putheader("Content-Type", "application/ipp")
            connection.putheader("Content-Length", str(len(header) + len(document)))
            connection.endheaders()
            connection.send(header)
            for start in range(0, len(document), 1 << 20):
                connection.send(document[start:start + (1 << 20)])
            response = connection.getresponse()
            body = response.read(1 << 20)
        except (OSError, http.client.HTTPException) as error:
            raise IppError(f"The printer did not answer ({error.__class__.__name__}).", sent=document_sent) from None
        finally:
            connection.close()
        if response.status != 200:
            raise IppError(f"The printer refused the request (HTTP {response.status}).", sent=False)
        try:
            status, groups = decode(body)
        except IppError as error:
            error.sent = document_sent
            raise
        if status >= 0x0100:
            raise IppError(f"The printer refused the request (IPP status 0x{status:04x}).", sent=False, status=status)
        return groups

    def attributes(self, names=None):
        names = names or ["printer-state", "printer-state-reasons", "printer-make-and-model", "printer-is-accepting-jobs",
                          "sides-supported", "document-format-supported", "pdf-k-octets-supported", "multiple-document-jobs-supported"]
        attributes = [("printer-uri", URI, self.uri), *(("requested-attributes" if i == 0 else "", KEYWORD, n) for i, n in enumerate(names))]
        return self.call(encode(GET_PRINTER_ATTRIBUTES, attributes), timeout=8).get(PRINTER_GROUP, {})

    def create_job(self, name, sides, user="faculty"):
        groups = self.call(encode(CREATE_JOB, [("printer-uri", URI, self.uri), ("requesting-user-name", NAME, user),
                                               ("job-name", NAME, name[:255])],
                                  [("copies", INTEGER, 1), ("sides", KEYWORD, sides), ("print-color-mode", KEYWORD, "monochrome"),
                                   ("multiple-document-handling", KEYWORD, "separate-documents-collated-copies")]))
        job_id = (groups.get(JOB, {}).get("job-id") or [None])[0]
        if not isinstance(job_id, int):
            raise IppError("The printer created no job.")
        return job_id

    def send_document(self, job_id, content, last, name, user="faculty"):
        header = encode(SEND_DOCUMENT, [("printer-uri", URI, self.uri), ("job-id", INTEGER, job_id), ("requesting-user-name", NAME, user),
                                        ("document-name", NAME, name[:255]), ("document-format", MIME, "application/pdf"),
                                        ("last-document", BOOLEAN, last)])
        # A large PDF is held whole before the printer answers; allow it the time.
        return self.call(header, content, timeout=max(self.timeout, 120), document_sent=True)

    def cancel(self, job_id, user="faculty"):
        self.call(encode(CANCEL_JOB, [("printer-uri", URI, self.uri), ("job-id", INTEGER, job_id), ("requesting-user-name", NAME, user)]), timeout=8)
