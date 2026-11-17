"""Only code registered here can run. Future processors must be retry-safe."""
from course_groups import group_courses
from course_storage import live_offerings
from roster_import import parse_roster
from rosters import connect


def archive_documents(service, job, files):
    # The immutable originals already are the document artifacts. Reference them;
    # do not duplicate course-wide files into term or section stores.
    return {"message": f"Archived {len(files)} document(s).", "artifact_ids": [item["id"] for item in files]}


def sync_roster(service, job, files):
    command = job["parameters"]
    with connect(service.path) as db:
        section = db.execute("SELECT * FROM course_sections WHERE id=?", (command["target"]["id"],)).fetchone()
        term = db.execute("SELECT * FROM course_terms WHERE id=?", (section["term_id"],)).fetchone()
        courses = live_offerings(db)
        group = next((group for group in group_courses(courses) if
                      (group["definition_id"], group["term"], group["term_other"], group["year"]) ==
                      (term["definition_id"], term["term"], term["term_other"], term["year"])), None)
    if not group:
        raise ValueError("The roster destination is unavailable; review its course and term.")
    file = files[0]
    parsed = parse_roster(file["content"], file["filename"])
    result = service.rosters.import_roster(group["id"], section["number"], parsed, file["filename"], request_id=job["id"])
    imported = result["last_import"]
    return {"message": f"Roster imported: {imported['added']} added, {imported['updated']} updated.",
            "added": imported["added"], "updated": imported["updated"], "students": result["count"],
            "term_id": result["term_id"], "section_id": result["section_id"],
            "course_url": f"#course/{group['id']}/section/{section['number']}/roster"}


def sync_accommodations(service, job, files):
    if len(files) != 1:
        raise ValueError("Import one accommodation workbook at a time.")
    return service.accommodations.import_job(job, files[0])


def sync_dr_exams(service, job, files):
    if len(files) != 1:
        raise ValueError("Import one DR exam workbook at a time.")
    return service.dr_exams.import_job(job, files[0])


def keep_aim_form(service, job, files):
    # AGENTS.md "ACCOMMODATIONS" AIM FORM CAPTURE: the kept JSON artifact is the capture; nothing else changes.
    return {"message": "AIM form structure kept."}


HANDLERS = {"document.archive": archive_documents, "roster.sync": sync_roster,
            "accommodations.sync": sync_accommodations, "accommodations.exams.sync": sync_dr_exams,
            "aim.form_capture": keep_aim_form}
