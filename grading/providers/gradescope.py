"""Documented file/entry exports (CSV, Entry View). Sending to Gradescope itself is `gradescope_sync` (AGENTS.md "Sending grades to Gradescope")."""
import csv
import io
from decimal import Decimal


def safe(value):
    value = str(value if value is not None else "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


class GradescopeAdapter:
    def totals(self, snapshot):
        students = {}
        for row in snapshot["records"]:
            key = (row["section_id"], row["enrollment_id"])
            student = students.setdefault(key, {"name": row["name"], "section_id": key[0], "enrollment_id": key[1],
                "questions": {}, "expected_questions": row["expected_questions"]})
            student["questions"][row["question_key"]] = row["final"]["score"]
        for student in students.values():
            complete = set(student["questions"]) == set(student["expected_questions"])
            subtotal = float(sum((Decimal(str(v["score"])) for v in student["questions"].values()), Decimal(0)))
            maximum = float(sum((Decimal(str(v["max_score"])) for v in student["questions"].values()), Decimal(0)))
            student.update(complete=complete, total=subtotal if complete else None,
                max_score=maximum if complete else None, approved_subtotal=subtotal)
        return list(students.values())

    def totals_csv(self, snapshot):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["Name", "Section ID", "Enrollment ID", "Complete", "Assignment total", "Max score", "Approved subtotal", "Approved questions", "Expected questions"])
        for r in self.totals(snapshot):
            writer.writerow([safe(v) for v in (r["name"], r["section_id"], r["enrollment_id"], r["complete"], r["total"], r["max_score"],
                r["approved_subtotal"], "; ".join(r["questions"]), "; ".join(r["expected_questions"]))])
        return stream.getvalue()

    def entry(self, snapshot):
        return {"title": snapshot["title"], "publication": snapshot["publication"], "entries": [{
            "response_id": r["response_id"], "name": r["name"], "section_id": r["section_id"], "enrollment_id": r["enrollment_id"],
            "question": r["question_key"], "score": r["final"]["score"]["score"], "max_score": r["final"]["score"]["max_score"],
            "rubric_items": [i for i in r["rubric"]["rules"]["items"] if i["id"] in r["final"]["score"]["selected"]],
            "adjustments": r["final"]["score"]["adjustments"], "comment": r["final"]["comment"]} for r in snapshot["records"]]}

    def csv(self, snapshot):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["Name", "Section ID", "Enrollment ID", "Question", "Score", "Max score", "Rubric items", "Adjustments", "Comment", "Review ID", "Decided by"])
        for r in snapshot["records"]:
            final = r["final"]
            writer.writerow([safe(v) for v in (r["name"], r["section_id"], r["enrollment_id"], r["question_key"],
                final["score"]["score"], final["score"]["max_score"], "; ".join(final["score"]["selected"]),
                "; ".join(f"{v['points']}: {v['reason']}" for v in final["score"]["adjustments"]), final["comment"], final["id"],
                # Never conflated: a score the instructor confirmed, or one accepted automatically under a retained benchmark report.
                "accepted automatically" if final["status"] == "automatic" else "instructor")])
        return stream.getvalue()
