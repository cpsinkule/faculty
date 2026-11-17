"""JEV PAGE ONE (requirement, 2026-10-25): Jev looks at a seated paper's whole first page and says which exam version is
printed on it, asked in both orders of the plan's letters with "No version is printed" offered. Both orders agreeing on a letter is
the reading; anything else decides nothing, and the local OCR reading (`exam_versions.FIRST_PAGE`) is asked instead. Measured that
day on the live scans: Exam 2 312 of 312 and Exam 1 338 of 338 first pages (A, B and three makeup C) right, both orders agreeing at
confidence 1.0, about 0.3 s each, none wrong or split.
"""
from jev import client
from jev.service import both

RULE = "jev-page-one-version-v1"
PURPOSE = "paper-version"
NONE = "No version is printed"
WHICH_VERSION = ("The image is page 1 of a student's printed exam booklet. Which version of the exam is it? Read the version printed on "
                 "the exam itself, such as in its title or footer; a sticker label stuck on the page does not count.")


def page_one(jev, owner, picture, letters):
    """{status, letter, receipt}: `letter` is "" unless both orders chose the same version. A Jev receipt owned by `owner`."""
    questions = both("version", WHICH_VERSION, dict.fromkeys([f"Version {letter}" for letter in letters] + [NONE]))
    answer = jev.ask(PURPOSE, owner, "A scanned page of a student's exam.", questions, RULE, images=[client.prepare_image(picture)])
    if answer.get("status") != "complete":
        return {"status": answer.get("status") or "failed", "letter": "", "receipt": answer.get("id")}
    first, second = answer["answers"]["version"]["choice"], answer["answers"]["version__reversed"]["choice"]
    letter = first[len("Version "):] if first == second and first != NONE and first.startswith("Version ") else ""
    return {"status": "complete", "letter": letter if letter in letters else "", "receipt": answer["id"]}
