"""Bounded, read-only parsing of instructor roster exports. Never evaluate cells."""

import csv
import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

MAX_UPLOAD = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 64
MAX_CELL = 10000


def text_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def iter_table(content, filename, *, max_columns=MAX_COLUMNS, noun="roster"):
    """Yield every row of a CSV or one-worksheet XLSX upload as text cells, blank rows included, so a row's position is its
    line (a CSV record, or a worksheet row). Rows are read and checked one at a time: a caller that refuses row 2 never
    reads the rest, and its own messages keep their order among the reader's. Cells are never evaluated: a workbook with a
    formula is refused. `noun` names the file in messages ("roster", "score sheet")."""
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError(f"Choose a nonempty {noun} file of 10 MB or less.")
    extension = Path(filename).suffix.lower()
    workbook = None
    try:
        if extension == ".csv":
            try:
                decoded = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                decoded = content.decode("cp1252")
            rows = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        else:
            if extension != ".xlsx":
                raise ValueError(f"Choose a CSV or Excel (.xlsx) {noun} export.")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 50 * 1024 * 1024:
                    raise ValueError(f"This workbook is too large. Export the {noun} as CSV instead.")
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
            if len(workbook.worksheets) != 1:
                raise ValueError(f"The {noun} workbook must contain one worksheet. Export that sheet as CSV instead.")
            sheet = workbook.worksheets[0]
            # Bounds apply even when the file declares incorrect worksheet dimensions.
            sheet.reset_dimensions()
            def cells():
                for row in sheet.iter_rows():
                    values = []
                    for cell in row:
                        if cell.data_type == "f":
                            raise ValueError(f"The {noun} contains a formula. Export values as CSV before importing.")
                        value = text_value(cell.value)
                        # Excel may store UINs as numbers with a leading-zero display format.
                        if isinstance(cell.value, (int, float)) and re.fullmatch(r"0+", cell.number_format or ""):
                            value = value.zfill(len(cell.number_format))
                        values.append(value)
                    yield values
            rows = cells()
        for line, raw in enumerate(rows, 1):
            if line > MAX_ROWS + 20:
                raise ValueError(f"A {noun} may contain at most 10,000 student rows.")
            values = [text_value(value) for value in raw]
            if len(values) > max_columns or any(len(value) > MAX_CELL or "\x00" in value for value in values):
                raise ValueError(f"Row {line} has too many columns or an invalid cell.")
            yield values
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"The {noun} could not be read. Upload a fresh CSV or XLSX export.") from error
    finally:
        if workbook is not None:
            workbook.close()


def read_table(content, filename, *, max_columns=MAX_COLUMNS, noun="roster"):
    """Every row of a CSV or one-worksheet XLSX upload as a list of text cells (see `iter_table`), read to the end."""
    rows = iter_table(content, filename, max_columns=max_columns, noun=noun)
    try:
        return list(rows)
    finally:
        rows.close()


def parse_roster(content, filename):
    """Return normalized headers plus every original header and student field."""
    rows = iter_table(content, filename)
    try:
        return validate_rows(rows)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("The roster could not be read. Upload a fresh CSV or XLSX export.") from error
    finally:
        rows.close()


def validate_rows(rows):
    original_headers = None
    students, seen = [], set()
    # `rows` comes from `iter_table`, which already bounds rows, columns and cells.
    for line, values in enumerate(rows, 1):
        if not any(value.strip() for value in values):
            continue
        if original_headers is None:
            original_headers = values
            headers = [" ".join(value.split()) for value in values]
            folded = [value.casefold() for value in headers]
            if any(not value for value in headers) or len(set(folded)) != len(headers):
                raise ValueError("Each roster column needs a distinct, nonempty heading.")
            if not {"uin", "name"} <= set(folded):
                raise ValueError("The roster needs UIN and Name columns, as in the provided export.")
            uin_index, name_index = folded.index("uin"), folded.index("name")
            continue
        if len(values) > len(headers) or (len(values) < len(headers) and len(values) <= max(uin_index, name_index)):
            raise ValueError(f"Row {line} does not match the roster column headings.")
        values += [""] * (len(headers) - len(values))
        uin = values[uin_index].strip()
        if not re.fullmatch(r"[0-9]{9}", uin):
            raise ValueError(f"Row {line} needs a nine-digit UIN. Preserve any leading zeros.")
        if not values[name_index].strip():
            raise ValueError(f"Row {line} needs a student name.")
        if uin in seen:
            raise ValueError(f"Row {line} repeats a UIN. Each student must appear once in an import.")
        seen.add(uin)
        students.append({"uin": uin, "fields": dict(zip(headers, values))})
        if len(students) > MAX_ROWS:
            raise ValueError("A roster may contain at most 10,000 students.")
    if not students:
        raise ValueError("The roster has no student rows to import.")
    return {"headers": headers, "source_headers": original_headers, "students": students}
