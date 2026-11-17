"""Bounded parsing of mixed-course accommodation exports; cells are only data."""
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from course_codes import validate_course_code
from roster_import import MAX_UPLOAD, text_value

METADATA = {"last name", "first name", "uin", "email address (tamu)", "access coordinator",
            "access coordinator email", "crn", "subject", "course", "section", "campus", "title",
            "request status", "submitted on", "last updated", "notes"}
REQUIRED = {"last name", "first name", "uin", "subject", "course", "section", "last updated"}
# Headings only the other export carries (message-only hints when the wrong file reaches an importer).
DR_SIGNATURE = {"exam type", "start time", "end time", "length"}
DR_HINT = "This looks like a DR exam bookings export. Use Import DR exam bookings."


def date_value(value, line):
    value = value.strip()
    if not value:
        return ""
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).isoformat()
        except ValueError:
            pass
    raise ValueError(f"Row {line}: Last Updated must be an Excel date or an ISO/US date.")


def read_export(content, filename, required, label="accommodation", other=None):
    """`other` is (headings that identify the other export, message): only a message, when required headings are missing."""
    if not content or len(content) > MAX_UPLOAD or Path(filename).suffix.lower() != ".xlsx":
        raise ValueError(f"Choose an Excel (.xlsx) {label} export of 10 MB or less.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 50 * 1024**2:
                raise ValueError("The workbook is too large.")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            if len(workbook.worksheets) != 1:
                raise ValueError(f"Use a {label} export containing one worksheet.")
            sheet = workbook.worksheets[0]; sheet.reset_dimensions()
            headers, rows, cells = None, [], 0
            for line, row in enumerate(sheet.iter_rows(), 1):
                cells += len(row)
                if line > 10001 or len(row) > 256 or cells > 1_000_000:
                    raise ValueError("The export exceeds 10,000 rows, 256 columns, or one million cells.")
                values = []
                for cell in row:
                    if cell.data_type in ("f", "e"):
                        raise ValueError(f"Row {line}: formulas and spreadsheet errors are not supported.")
                    value = text_value(cell.value)
                    if isinstance(cell.value, (int, float)) and re.fullmatch(r"0{1,20}", cell.number_format or ""):
                        value = value.zfill(len(cell.number_format))
                    if len(value) > 10000 or "\x00" in value:
                        raise ValueError(f"Row {line} has an invalid or oversized cell.")
                    values.append(value)
                if not any(v.strip() for v in values):
                    continue
                if headers is None:
                    headers = [" ".join(v.split()) for v in values]
                    folded = [h.casefold() for h in headers]
                    if any(not h for h in headers) or len(set(folded)) != len(headers) or not required <= set(folded):
                        if other and other[0] <= set(folded):
                            raise ValueError(other[1])
                        raise ValueError(f"The {label} export needs distinct headings including {', '.join(sorted(required))}.")
                    continue
                if len(values) > len(headers):
                    raise ValueError(f"Row {line} has more cells than column headings.")
                values += [""] * (len(headers) - len(values))
                rows.append((line, dict(zip(headers, values))))
            if not rows:
                raise ValueError(f"The {label} export has no student rows.")
            return headers, rows
        finally:
            workbook.close()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"The {label} export could not be read. Upload a fresh XLSX export.") from error


def display_name(row):
    """'Last, First' as the rosters show it, for previews only (never part of a preview hash or a saved record)."""
    fields = {k.casefold(): str(v or "").strip() for k, v in (row.get("source_fields") or {}).items()}
    last, first = fields.get("last name", ""), fields.get("first name", "")
    return f"{last}, {first}" if last and first else (last or first or row.get("name", ""))


def student_fields(original, line):
    fields = {k.casefold():v for k,v in original.items()}
    uin = fields["uin"].strip()
    if not re.fullmatch(r"[0-9]{9}", uin):
        raise ValueError(f"Row {line} needs a nine-digit UIN, including any leading zeros.")
    code = validate_course_code({"code_prefix": fields["subject"], "code_number": fields["course"]})["code"]
    section = fields["section"].strip()
    if not re.fullmatch(r"[0-9]{1,15}", section) or int(section) < 1:
        raise ValueError(f"Row {line} needs a positive section number.")
    return {"row": line, "uin": uin, "code": code, "section": int(section),
            "name": (fields["first name"].strip() + " " + fields["last name"].strip()).strip(),
            "source_fields": original}


def parse_accommodations(content, filename):
    headers, source = read_export(content, filename, REQUIRED, other=(DR_SIGNATURE, DR_HINT))
    accommodation_headers = [h for h in headers if h.casefold() not in METADATA]
    if not accommodation_headers:
        raise ValueError("The export has no accommodation columns.")
    rows, seen = [], {}
    for line, original in source:
        row = student_fields(original, line)
        fields = {k.casefold():v for k,v in original.items()}
        key = (row['uin'], row['code'], row['section'])
        if key in seen:
            if seen[key] != original:
                raise ValueError(f"Row {line} conflicts with another row for the same student, course, and section.")
            continue
        seen[key] = original
        row.update(updated=date_value(fields['last updated'], line),
                   metadata={h:original[h] for h in headers if h.casefold() in METADATA},
                   values={h:original[h].strip() for h in accommodation_headers})
        rows.append(row)
    return {"headers":headers,"accommodation_headers":accommodation_headers,"rows":rows}
