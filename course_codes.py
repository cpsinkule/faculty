"""Canonical course codes shared by course definitions and their offerings."""

import re


def split_course_code(value):
    """Recognize legacy codes conservatively; never invent missing components."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([A-Za-z]{1,20})[ \t-]*([0-9]{1,19})", value.strip())
    if not match:
        return None
    return match[1].upper(), match[2]


def validate_course_code(data):
    if "code_prefix" in data or "code_number" in data:
        prefix, number = data.get("code_prefix"), data.get("code_number")
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z]{1,20}", prefix.strip()):
            raise ValueError("Course prefix is required and must contain 1–20 letters.")
        prefix = prefix.strip().upper()
        if type(number) is int:
            number = str(number)
        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,19}", number.strip()):
            raise ValueError("Course number is required and must contain 1–19 digits, without decimals or signs.")
        number = number.strip()
        # Reject conflicting combined and split inputs instead of guessing which was intended.
        if "code" in data and split_course_code(data["code"]) != (prefix, number):
            raise ValueError("The course code does not match its prefix and number.")
    else:
        # Keep older browser sessions and API clients working with valid combined codes.
        parts = split_course_code(data.get("code"))
        if parts is None:
            raise ValueError("Course code requires a letter prefix and numeric suffix, such as MATH 101.")
        prefix, number = parts
    return {"code": f"{prefix} {number}", "code_prefix": prefix, "code_number": number}
