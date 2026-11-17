"""Read-only course homes organized by course code and academic term.

An offering retains its own identifier and storage lifecycle. A group is a
projection for navigation, so grouping never combines or relocates course data.
"""

import json
import uuid
from copy import deepcopy

from course_codes import split_course_code


def _identity(course):
    parts = split_course_code(course.get("code"))
    code = " ".join(parts) if parts else (course.get("code") or "").strip()
    term = (course.get("term") or "").strip()
    other = (course.get("term_other") or "").strip() if term == "Other" else ""
    year = course.get("year")
    if not code or not term or year is None or (term == "Other" and not other):
        return course["id"], code
    key = json.dumps([code, term, other, year], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "faculty:course-term:" + key)), code


def navigation_id(course):
    """The course page's group ID for one code and term/year (what `group_courses` names it); a projection, never an owner."""
    return _identity(course)[0]


def _offering_order(course):
    sections = course.get("sections") or []
    return (tuple(sections), course.get("created_at") or "", course["id"])


def group_courses(courses):
    """Return stable, deterministic term groups without changing input records."""
    grouped = {}
    for course in courses:
        if course.get("deleted_at"):
            continue
        identifier, code = _identity(course)
        group = grouped.setdefault(identifier, {"id": identifier, "code": code, "offerings": []})
        group["offerings"].append(deepcopy(course))

    result = []
    for group in grouped.values():
        offerings = sorted(group["offerings"], key=_offering_order)
        latest = max(offerings, key=lambda item: (item.get("updated_at") or "", item["id"]))
        sections = {section for item in offerings for section in item.get("sections", [])}
        current_sections = {
            section for item in offerings if item.get("is_current")
            for section in item.get("sections", [])
        }
        result.append({
            "id": group["id"],
            "definition_id": latest.get("definition_id"),
            "code": group["code"],
            "name": latest["name"],
            "term": latest.get("term") or "",
            "term_other": latest.get("term_other") or "",
            "year": latest.get("year"),
            "is_current": any(item.get("is_current") for item in offerings),
            "all_current": all(item.get("is_current") for item in offerings),
            "sections": sorted(sections),
            "current_sections": sorted(current_sections),
            "offerings": offerings,
            "created_at": min(item.get("created_at") or "" for item in offerings),
            "updated_at": max(item.get("updated_at") or "" for item in offerings),
        })
    return sorted(result, key=lambda group: (
        -(group["year"] or 0), group["term"], group["term_other"], group["code"], group["id"],
    ))
