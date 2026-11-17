"""The Faculty files the compose window can attach (requirement, 2026-10-01): a current student's reviewed papers, the current
courses' lecture notes and the files the assistant saved.

A read-only projection owned by the workspace's Mail page: each list is read through its owner's own route in process (the scans'
student papers, the course page's notes, the assistant's saved files), so it shows what those pages show, and an item is only a route
and a name. Attaching one is the ordinary route attachment (attachments.py), read again at the preview and at the send. A student is
found in the current rosters the Mail page already reads (by name or roster address); no identifier is shown.
"""
from urllib.parse import urlencode

from course_groups import group_courses
from course_storage import live_offerings
from rosters import connect
from student_photos.service import name_key

MAX_STUDENTS = 6
QUERY_MAX = 200


class FacultyFiles:
    def __init__(self, app, mail):
        self.app, self.mail = app, mail

    def _get(self, path):
        """A JSON read of the app's own route, in process; None when it does not answer 200 with JSON."""
        response = self.app.test_client().get(path)
        try:
            return response.get_json(silent=True) if response.status_code == 200 and response.is_json else None
        finally:
            response.close()

    def listing(self, kind, query=""):
        """{kind, groups: [{label, items: [{route, name, size?, detail}], note?}], note}. An item without a route cannot be attached
        (a paper not reviewed yet) and says why."""
        if kind == "papers":
            return {"kind": kind, **self.papers(query)}
        if kind == "notes":
            groups = self.notes()
            return {"kind": kind, "groups": groups, "note": "" if groups else "No current course has lecture notes."}
        if kind == "saved":
            groups = self.saved()
            return {"kind": kind, "groups": groups, "note": "" if groups else "The assistant has saved no files."}
        raise ValueError("Choose student papers, lecture notes or saved files.")

    # -- A student's reviewed papers -----------------------------------------------------------------------------------------
    def papers(self, query):
        query = " ".join(str(query or "").split())
        if len(query) > QUERY_MAX:
            raise ValueError("That search is too long.")
        if len(query) < 2:
            return {"groups": [], "note": "Type a student's name or email."}
        index, people = self.mail._students()
        if "@" in query:
            from .service import variants
            found = index.get(variants(query)[0], [])
        else:
            words = name_key(query).split()
            found = [person["entry"] for person in people if words and all(any(word.startswith(part) for word in person["words"]) for part in words)]
        students, seen = [], set()
        for entry in found:
            if entry.get("enrollment_id") and entry["enrollment_id"] not in seen:
                seen.add(entry["enrollment_id"])
                students.append(entry)
        more = len(students) > MAX_STUDENTS
        groups = [self._student(entry) for entry in students[:MAX_STUDENTS]]
        note = "No current student matches." if not groups else (f"The first {MAX_STUDENTS} matches; type more of the name." if more else "")
        return {"groups": groups, "note": note}

    def _student(self, entry):
        with connect(self.mail.path) as db:
            assignments = db.execute("""SELECT a.id, a.title, MIN(b.created_at) AS printed FROM printed_copies c
                JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                JOIN assignments a ON a.id=v.assignment_id WHERE c.enrollment_id=? GROUP BY a.id ORDER BY printed, a.id""",
                                     (entry["enrollment_id"],)).fetchall()
        items = []
        for assignment in assignments:
            answer = self._get("/api/documents/papers?" + urlencode({"enrollment_id": entry["enrollment_id"], "assignment_id": assignment["id"]}))
            for paper in (answer or {}).get("papers") or []:
                reviewed = paper.get("reviewed") or {}
                detail = [str(paper.get("version") or ""),  # the version's own label ("Version B", "Stamps only · 1 page")
                          f"reviewed {str(reviewed.get('at') or '')[:10]}" if reviewed else "not reviewed yet",
                          "partial" if reviewed.get("partial") else "", "not the paper chosen to count" if paper.get("chosen") is False else ""]
                items.append({"route": reviewed.get("pdf") or None, "name": f"{assignment['title']} - {entry['name']}.pdf",
                              "title": assignment["title"], "detail": " · ".join(part for part in detail if part)})
        return {"label": f"{entry['name']} · {entry['code']} {entry['section']}" + (" · dropped" if entry.get("dropped") else ""), "items": items,
                "note": "" if items else "No paper of this student was printed and scanned."}

    # -- Lecture notes of the current courses --------------------------------------------------------------------------------
    def notes(self):
        with connect(self.mail.path) as db:
            courses = live_offerings(db)
        groups, seen = [], set()
        for group in sorted(group_courses(courses), key=lambda item: str(item.get("code") or "")):
            definition = group.get("definition_id")
            if not group.get("is_current") or not definition or definition in seen:
                continue
            seen.add(definition)
            current = (self._get(f"/api/course-notes/definitions/{definition}") or {}).get("current")
            if not current:
                continue
            items = [{"route": current["original_url"], "name": current["source_name"], "size": current["size"],
                      "title": current["title"] or current["source_name"], "detail": f"the whole notes · {current['pages']} pages"}]
            for part in current.get("parts") or []:
                label = f"{part['number']} {part['title']}".strip()
                items.append({"route": part["url"], "name": f"{label}.pdf", "size": part["size"], "title": label,
                              "detail": f"pages {part['first_page']}–{part['last_page']}"})
            groups.append({"label": f"{group.get('code')} · {current['title'] or current['source_name']}", "items": items})
        return groups

    # -- Files the assistant saved ----------------------------------------------------------------------------------------------
    def saved(self):
        artifacts = (self._get("/api/assistant/artifacts") or {}).get("artifacts") or []
        items = [{"route": item["url"], "name": item["filename"], "size": item["size"], "title": item["title"],
                  "detail": f"saved {str(item['created_at'])[:10]}"} for item in artifacts]
        return [{"label": "Saved by the assistant (newest first)", "items": items}] if items else []
