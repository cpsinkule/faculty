"""Bounded Response Card authoring schema and explicit, text-only variables.

Templates belong to course definitions; frozen recipient snapshots are produced
by ``personalize`` before they cross the public-service boundary. This module
does not render HTML, execute expressions, or traverse arbitrary object paths.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import secrets
from collections.abc import Mapping


VARIABLES = (
    "student.name", "student.full_name", "student.first_name", "student.last_name",
    "student.email", "student.section", "course.code", "course.name",
    "course.section", "course.term", "course.year", "assignment.name",
    "exam.name", "exam.room", "seat.number", "seat.row", "seat.column",
    "seating_assignment.seat", "dr_exam.date", "dr_exam.time", "dr_exam.location",
    "exam.date", "exam.time", "exam.date_long",
    "exam.event_name", "exam.sitting", "grade.score", "grade.maximum", "grade.feedback",
    "request.description", "request.decision", "request.status",
    "student.uin | last4", "student.uin | uin_digest",
    # A quiz makeup booking card (ABSENCES "MAKEUP BOOKING", 2026-10-04): the day the quiz was given and the day the makeup is due.
    "makeup.given", "makeup.due",
)
FIELD_TYPES = (
    "static", "acknowledgment", "short_text", "long_text", "single_select",
    "multi_select", "confirmation", "boolean", "integer", "decimal", "date",
    "time", "radio", "attestation",
)
_TYPE_ALIASES = {
    "text": "short_text", "static_text": "static", "information": "static",
    "checkbox": "acknowledgment", "textarea": "long_text", "select": "single_select",
    "multiselect": "multi_select", "signature": "attestation",
}
_MODES = {"exact", "case-insensitive", "normalized", "numeric", "one-of", "compact", "uin-digest"}
# A salted SHA-256 of a nine-digit UIN: verification never stores the UIN itself.
_DIGEST = re.compile(r"[0-9a-f]{16}:[0-9a-f]{64}\Z")
_MODE_ALIASES = {
    "case_insensitive": "case-insensitive", "normalized-text": "normalized",
    "normalized_text": "normalized", "one_of": "one-of",
}
_MISMATCH = {"BLOCK_SUBMISSION", "ALLOW_AND_FLAG", "ALLOW"}
_CHOICES = {"single_select", "multi_select", "radio"}
_BOOLEANS = {"acknowledgment", "boolean"}
_FIELD_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_TOKEN = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)(?:\s*\|\s*(last4|uin_digest))?\s*\}\}")
_MAX_SCHEMA_BYTES = 128 * 1024
_MAX_SAFE_INTEGER = 9007199254740991
DEFAULT_CONFIRMATION = "Thank you. Your response has been recorded."
# RESPONSE-CARDS "DR LOCATION IS NOT CHECKED": answers asked for but never compared, even on cards issued with a check.
UNCHECKED_FIELDS = frozenset({"confirm_dr_location"})


def unchecked_dropped(submission):
    """A stored submission as Faculty reads it: the verification results of UNCHECKED_FIELDS left out (the record is unchanged)."""
    checks = (submission or {}).get("verification")
    if not isinstance(checks, dict) or not UNCHECKED_FIELDS & checks.keys():
        return submission
    return {**submission, "verification": {k: v for k, v in checks.items() if k not in UNCHECKED_FIELDS}}


def settled_status(status, submission):
    """A synchronized card status with UNCHECKED_FIELDS ignored: "needs_review" only while another flagged check differs,
    otherwise what the Worker would have said without them ("verified" when every remaining check matched)."""
    checks = (submission or {}).get("verification") if isinstance(submission, dict) else None
    if status != "needs_review" or not isinstance(checks, dict) or not UNCHECKED_FIELDS & checks.keys():
        return status
    rest = [v if isinstance(v, dict) else {"matched": bool(v)} for k, v in checks.items() if k not in UNCHECKED_FIELDS]
    if any(v.get("matched") is not True and v.get("on_mismatch", "ALLOW_AND_FLAG") == "ALLOW_AND_FLAG" for v in rest):
        return status
    return "verified" if rest and all(v.get("matched") is True for v in rest) else "submitted"


class SchemaError(ValueError):
    """A template or recipient snapshot cannot be safely issued."""


def _keys(value, permitted, location):
    if not isinstance(value, dict):
        raise SchemaError(f"{location} must be an object")
    if any(key not in permitted for key in value):
        raise SchemaError(f"{location} contains unsupported properties")


def _tokens(value):
    """Validate the complete delimiter syntax, yielding only allowlisted tokens."""
    end = 0
    for match in _TOKEN.finditer(value):
        literal = value[end:match.start()]
        if ("{{" in literal or "}}" in literal
                or (match.start() and value[match.start() - 1] == "{")
                or (match.end() < len(value) and value[match.end()] == "}")):
            raise SchemaError("Invalid response-card variable syntax")
        name = match.group(1)
        if match.group(2):
            name += " | " + match.group(2)
        if name not in VARIABLES:
            raise SchemaError(f"Unsupported response-card variable: {name}")
        yield match, name
        end = match.end()
    if "{{" in value[end:] or "}}" in value[end:]:
        raise SchemaError("Invalid response-card variable syntax")


def _text(value, maximum, location, *, empty=True):
    if not isinstance(value, str):
        raise SchemaError(f"{location} must be text")
    if len(value) > maximum or (not empty and not value.strip()):
        raise SchemaError(f"{location} must contain {'0' if empty else '1'}–{maximum} characters")
    if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise SchemaError(f"{location} contains invalid characters")
    list(_tokens(value))
    return value


def _number(value, location, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{location} must be a number")
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise SchemaError(f"{location} is outside the supported integer range")
    elif not math.isfinite(value):
        raise SchemaError(f"{location} must be finite")
    if integer and (not isinstance(value, int)):
        raise SchemaError(f"{location} must be an integer")
    return value


def _literal(value, location):
    """Bounded scalars/lists only: never recursively walk untrusted objects."""
    if isinstance(value, str):
        return _text(value, 10000, location)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _number(value, location)
    if isinstance(value, list):
        if not 1 <= len(value) <= 100:
            raise SchemaError(f"{location} must contain 1–100 values")
        result = []
        for item in value:
            if isinstance(item, (list, dict)) or item is None:
                raise SchemaError(f"{location} must contain scalar values")
            result.append(_literal(item, location))
        return result
    raise SchemaError(f"{location} must be text, a boolean, a number, or a list of those values")


def _condition_value(value, source):
    kind = source["type"]
    location = "Condition value"
    if kind == "static":
        raise SchemaError("Conditions cannot depend on static text")
    if kind in _BOOLEANS:
        if not isinstance(value, bool):
            raise SchemaError("A boolean condition requires true or false")
        return value
    if kind in {"integer", "decimal"}:
        return _number(value, location, integer=kind == "integer")
    if kind == "multi_select":
        if not isinstance(value, list) or len(value) > 100:
            raise SchemaError("A multi-select condition requires a list of options")
        result = [_text(item, 200, location) for item in value]
        if len(set(result)) != len(result) or any(item not in source["options"] for item in result):
            raise SchemaError("A multi-select condition must contain distinct defined options")
        return result
    result = _text(value, 10000 if kind == "long_text" else 2000, location)
    if kind in _CHOICES and result not in source["options"]:
        raise SchemaError("A choice condition must equal a defined option")
    if kind == "date" and not list(_tokens(result)):
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result):
                raise ValueError
            dt.date.fromisoformat(result)
        except ValueError:
            raise SchemaError("A date condition must be a valid YYYY-MM-DD date") from None
    if kind == "time" and not list(_tokens(result)):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", result):
            raise SchemaError("A time condition must use HH:MM or HH:MM:SS")
    return result


def _verification(value, field):
    _keys(value, {"expected", "mode", "on_mismatch"}, "Verification")
    if field["type"] == "static":
        raise SchemaError("Static text cannot have verification")
    if "expected" not in value:
        raise SchemaError("Verification requires an expected value")
    mode = value.get("mode", "exact")
    if not isinstance(mode, str):
        raise SchemaError("Verification mode must be text")
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in _MODES:
        raise SchemaError("Unsupported verification mode")
    mismatch = value.get("on_mismatch", "ALLOW_AND_FLAG")
    if not isinstance(mismatch, str) or mismatch not in _MISMATCH:
        raise SchemaError("Unsupported verification mismatch policy")
    expected = _literal(value["expected"], "Expected value")
    if mode in {"case-insensitive", "normalized", "compact"} and not isinstance(expected, str):
        raise SchemaError(f"{mode} verification requires a text expected value")
    if mode == "uin-digest" and (not isinstance(expected, str) or not (_DIGEST.fullmatch(expected) or "uin_digest" in expected)):
        raise SchemaError("UIN verification requires the student.uin | uin_digest variable as its expected value")
    if mode == "numeric":
        if isinstance(expected, str):
            if not list(_tokens(expected)):
                try:
                    if not expected.strip() or not math.isfinite(float(expected)):
                        raise ValueError
                except ValueError:
                    raise SchemaError("Numeric verification requires a finite expected value") from None
        elif isinstance(expected, bool) or not isinstance(expected, (int, float)):
            raise SchemaError("Numeric verification requires a numeric expected value")
    if mode == "one-of" and not isinstance(expected, list):
        raise SchemaError("One-of verification requires a list of expected values")
    return {"expected": expected, "mode": mode, "on_mismatch": mismatch}


def validate_schema(schema):
    """Return a fresh canonical schema, or raise ``SchemaError``/``ValueError``.

    No objects from the authoring input are retained. The field order is the
    dependency order for conditions, excluding cycles and forward references.
    """
    _keys(schema, {"title", "description", "confirmation_message", "fields"}, "Response card")
    result = {
        "title": _text(schema.get("title"), 200, "Title", empty=False),
        "description": _text(schema.get("description", ""), 10000, "Description"),
        "confirmation_message": _text(schema.get("confirmation_message", DEFAULT_CONFIRMATION), 2000, "Confirmation message"),
        "fields": [],
    }
    fields = schema.get("fields")
    if not isinstance(fields, list) or len(fields) > 50:
        raise SchemaError("Response cards require a fields list of at most 50 fields")
    previous = {}
    for original in fields:
        _keys(original, {"id", "type", "label", "required", "options", "show_if", "verification"}, "Field")
        identity = original.get("id")
        if (not isinstance(identity, str) or not _FIELD_ID.fullmatch(identity)
                or identity in {"constructor", "prototype", "__proto__"}):
            raise SchemaError("Field IDs must start with a letter and contain only letters, digits, underscores, or hyphens (maximum 64 characters)")
        if identity in previous:
            raise SchemaError(f"Duplicate field ID: {identity}")
        kind = original.get("type")
        if not isinstance(kind, str):
            raise SchemaError("Field type must be text")
        kind = _TYPE_ALIASES.get(kind, kind)
        if kind not in FIELD_TYPES:
            raise SchemaError(f"Unsupported field type: {kind}")
        required = original.get("required", False)
        if not isinstance(required, bool):
            raise SchemaError("Required must be true or false")
        if kind == "static" and required:
            raise SchemaError("Static text cannot be required")
        field = {"id": identity, "type": kind,
                 "label": _text(original.get("label"), 10000 if kind == "static" else 2000, "Field label", empty=False),
                 "required": required}
        if kind in _CHOICES:
            options = original.get("options")
            if not isinstance(options, list) or not 1 <= len(options) <= 100:
                raise SchemaError("Choice fields require 1–100 options")
            field["options"] = [_text(option, 200, "Option", empty=False) for option in options]
            if len(set(field["options"])) != len(field["options"]):
                raise SchemaError("Choice options must be unique")
        elif "options" in original:
            raise SchemaError("Only choice fields can have options")
        if "show_if" in original:
            condition = original["show_if"]
            _keys(condition, {"field", "equals"}, "Condition")
            source_id = condition.get("field")
            if not isinstance(source_id, str) or source_id not in previous:
                raise SchemaError("Conditions must reference an earlier field")
            if "equals" not in condition:
                raise SchemaError("Conditions require an equals value")
            field["show_if"] = {"field": source_id, "equals": _condition_value(condition["equals"], previous[source_id])}
        if "verification" in original:
            field["verification"] = _verification(original["verification"], field)
        result["fields"].append(field)
        previous[identity] = field
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_SCHEMA_BYTES:
        raise SchemaError("Response-card schema exceeds 128 KiB")
    return result


def uin_digest(uin, salt=None):
    """Salted SHA-256 over the nine UIN digits; the public service compares digits only."""
    if not isinstance(uin, str) or not re.fullmatch(r"[0-9]{9}", uin):
        raise SchemaError("A nine-digit UIN string is required for the uin_digest variable")
    salt = salt or secrets.token_hex(8)
    return f"{salt}:{hashlib.sha256((salt + uin).encode()).hexdigest()}"


def _variable_value(name, variables):
    if name == "student.uin | uin_digest":
        return uin_digest(variables.get("student.uin"))
    if name == "student.uin | last4":
        if name in variables:
            value = variables[name]
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}", value):
                raise SchemaError("The UIN last-four value must contain exactly four digits")
            return value
        value = variables.get("student.uin")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{9}", value):
            raise SchemaError("A nine-digit UIN string is required for the last4 variable")
        return value[-4:]
    # The name has already passed the fixed allowlist. Only this flat context key
    # is read; attribute access, nested paths, filters, and callables are absent.
    value = variables.get(name)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SchemaError(f"Missing or invalid context for variable: {name}")
    if isinstance(value, (int, float)):
        _number(value, f"Variable {name}")
    value = str(value)
    if not value.strip():
        raise SchemaError(f"Missing context for variable: {name}")
    if "{{" in value or "}}" in value:
        raise SchemaError(f"Variable {name} cannot contain template expressions")
    return _text(value, 10000, f"Variable {name}", empty=False)


def render_text(text, variables):
    """Interpolate approved variables once; never evaluate or produce markup."""
    text = _text(text, 10000, "Template text")
    if not isinstance(variables, Mapping):
        raise SchemaError("Variable context must be a mapping")
    matches = list(_tokens(text))
    parts = []
    end = 0
    size = 0
    for match, name in matches:
        prefix = text[end:match.start()]
        replacement = _variable_value(name, variables)
        size += len(prefix) + len(replacement)
        if size > 10000:
            raise SchemaError("Personalized text exceeds 10000 characters")
        parts.extend((prefix, replacement))
        end = match.end()
    parts.append(text[end:])
    rendered = "".join(parts)
    if len(rendered) > 10000:
        raise SchemaError("Personalized text exceeds 10000 characters")
    return rendered


def with_choices(schema, choices):
    """The schema with an issue's own option lists for named choice fields (ABSENCES "MAKEUP BOOKING", 2026-10-04: each student's
    booking card offers their own times). Only the options of a choice field change; every condition on that field must still name
    one of its options. Returns a fresh canonical schema, or raises ``SchemaError``."""
    result = validate_schema(schema)
    if not isinstance(choices, Mapping):
        raise SchemaError("Choices must map field IDs to option lists")
    for identity, options in choices.items():
        field = next((item for item in result["fields"] if item["id"] == identity), None)
        if field is None or field["type"] not in _CHOICES:
            raise SchemaError(f"Choices name a field that is not a choice field: {identity}")
        field["options"] = options
        for other in result["fields"]:
            condition = other.get("show_if")
            wanted = condition["equals"] if condition and isinstance(condition["equals"], list) else [condition["equals"]] if condition else []
            if condition and condition["field"] == identity and any(value not in options for value in wanted):
                raise SchemaError(f"A condition on {identity} names an option the choices leave out")
    return validate_schema(result)


def personalize(schema, variables):
    """Produce a complete recipient schema, failing on missing context.

    Only explicit text-bearing properties are interpolated. Identifiers, field
    types, policy names, and condition references remain schema structure.
    """
    result = validate_schema(schema)
    if not isinstance(variables, Mapping):
        raise SchemaError("Variable context must be a mapping")

    def literal(value):
        if isinstance(value, str):
            return render_text(value, variables)
        if isinstance(value, list):
            return [render_text(item, variables) if isinstance(item, str) else item for item in value]
        return value

    for key in ("title", "description", "confirmation_message"):
        result[key] = render_text(result[key], variables)
    for field in result["fields"]:
        field["label"] = render_text(field["label"], variables)
        if "options" in field:
            field["options"] = literal(field["options"])
        if "show_if" in field:
            field["show_if"]["equals"] = literal(field["show_if"]["equals"])
        if "verification" in field:
            field["verification"]["expected"] = literal(field["verification"]["expected"])
    return validate_schema(result)
