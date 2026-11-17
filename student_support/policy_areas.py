"""TEACHING-WORKFLOWS / TERM POLICIES: term-owned guidance, edited by area.

The areas and prompts reflect captured student/instructor interactions reviewed on
2026-10-03. They suggest questions for the instructor, never policy defaults.
"""
import re

POLICY_LIMIT = 20000
MODES = [
    {"id": "unset", "label": "No guidance yet"},
    {"id": "guidance", "label": "Follow my guidance"},
    {"id": "ask", "label": "Ask me case by case"},
]
AREAS = [
    {"id": "absences", "group": "Attendance & makeups", "label": "Absences & documentation",
     "prompt": "When should students notify you? What documentation do you accept, and what happens when dates or documentation are missing?",
     "placeholder": "Accepted reasons, notice deadlines, documentation, and when to consult me…"},
    {"id": "attendance", "group": "Attendance & makeups", "label": "Attendance & iClicker",
     "prompt": "How should late arrivals, missed responses, attendance corrections, and iClicker connection problems be handled?",
     "placeholder": "Evidence needed for corrections, reporting deadlines, and excused-absence credit…"},
    {"id": "quiz_makeups", "group": "Attendance & makeups", "label": "Missed in-class quizzes",
     "prompt": "Who may make up a missed quiz? Can students attend another section? What details must be confirmed before proposing a makeup?",
     "placeholder": "Eligibility, documentation, missed quiz/date, deadlines, and permitted arrangements…"},
    {"id": "exam_makeups", "group": "Attendance & makeups", "label": "Missed exams & conflicts",
     "prompt": "How should exam absences and schedule conflicts be handled? When must you approve an alternate sitting or consult a coordinator?",
     "placeholder": "Advance notice, eligibility, documentation, alternate sittings, and approval requirements…"},
    {"id": "online_quizzes", "group": "Assignments & grades", "label": "Online & video quizzes",
     "prompt": "When may a locked quiz reopen? Does an absence need to cover the entire availability period? How should mistaken deadlines be handled?",
     "placeholder": "Reopening rules, extension criteria, availability windows, and exceptions…"},
    {"id": "homework", "group": "Assignments & grades", "label": "Homework & late work",
     "prompt": "What are your rules for WebAssign and other homework extensions, late submissions, and missed deadlines?",
     "placeholder": "Extension eligibility, late penalties, coordinator restrictions, and reporting deadlines…"},
    {"id": "grades", "group": "Assignments & grades", "label": "Grades & regrades",
     "prompt": "Where should regrade requests go? How should rubric questions, missing grades, Canvas/Gradescope differences, and dropped-score questions be answered?",
     "placeholder": "Regrade channel and deadline, grade corrections, review meetings, and what requires my decision…"},
    {"id": "appointments", "group": "Student support", "label": "Office hours & appointments",
     "prompt": "Are regular office hours drop-in? What alternatives may be proposed, and what availability, location, or meeting format must be confirmed?",
     "placeholder": "Drop-in rules, appointment windows, online/in-person preferences, and calendar checks…"},
    {"id": "learning", "group": "Student support", "label": "Catching up & study help",
     "prompt": "How should students get missed notes or materials, review an exam, and find tutoring or study support?",
     "placeholder": "Approved resources, materials to share, attachment guidance, and when to offer a meeting…"},
    {"id": "accommodations", "group": "Student support", "label": "Accommodations & flexibility",
     "prompt": "How should approved extended time, testing-center arrangements, and attendance flexibility be communicated? When should you or a coordinator clarify the arrangement?",
     "placeholder": "Use the student's recorded accommodations; add logistics, contacts, and arrangements to confirm…"},
    {"id": "technology", "group": "Student support", "label": "Technology & access",
     "prompt": "What should students do about Canvas, WebAssign/Cengage, access codes, broken links, or platform outages? Does a technical problem affect a deadline?",
     "placeholder": "Troubleshooting, support contacts, evidence needed, and extension decisions…"},
    {"id": "exam_day", "group": "Student support", "label": "Exam-day logistics",
     "prompt": "How should calculator questions, room/seating questions, and missing exam instructions or response cards be handled?",
     "placeholder": "Calculator rules, permitted materials, arrival instructions, and which current records to check…"},
    {"id": "wellbeing", "group": "Student support", "label": "Wellbeing & personal circumstances",
     "prompt": "How should replies acknowledge injury, illness, bereavement, distress, or other personal circumstances? When do you want to review the next step yourself?",
     "placeholder": "Tone, check-in questions, support resources, and situations to bring to me…"},
    {"id": "reply_style", "group": "Communication", "label": "Reply style & follow-through",
     "prompt": "How should replies greet students and sign off? When is an acknowledgment enough, and when should a follow-up or referral be proposed?",
     "placeholder": "Greeting, signature, tone, level of detail, attachments, and follow-up preferences…"},
    {"id": "additional", "group": "Communication", "label": "Additional guidance",
     "prompt": "Keep guidance that spans several areas here. Any policies saved in the previous single text box are preserved here until you choose to reorganize them.",
     "placeholder": "Other guidance for this term…"},
]


def legacy_areas(content):
    return {"additional": {"mode": "guidance", "guidance": content}} if content else {}


def normalize_areas(value):
    """Validate a complete replacement; omitted/unset areas carry no instruction."""
    labels = {area["id"]: area["label"] for area in AREAS}
    if not isinstance(value, dict) or set(value) - labels.keys():
        raise ValueError("Supply guidance for the listed policy areas only.")
    result = {}
    for key, entry in value.items():
        if not isinstance(entry, dict) or set(entry) != {"mode", "guidance"}:
            raise ValueError(f"{labels[key]} needs a mode and guidance text.")
        mode, guidance = entry["mode"], entry["guidance"]
        if not isinstance(mode, str) or mode not in {"unset", "guidance", "ask"}:
            raise ValueError(f"Choose how to handle {labels[key].lower()}.")
        if not isinstance(guidance, str) or len(guidance) > POLICY_LIMIT or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", guidance):
            raise ValueError(f"{labels[key]} must be text of at most {POLICY_LIMIT} characters.")
        guidance = guidance.strip()
        if mode == "unset":
            if guidance:
                raise ValueError(f"Choose Follow my guidance or Ask me case by case to save notes for {labels[key].lower()}.")
            continue
        if mode == "guidance" and not guidance:
            raise ValueError(f"Write your guidance for {labels[key].lower()}, or choose No guidance yet.")
        result[key] = {"mode": mode, "guidance": guidance}
    return result


def policy_content(areas):
    """One deterministic, bounded prompt projection, shared with legacy readers."""
    if set(areas) == {"additional"} and areas["additional"]["mode"] == "guidance":
        return areas["additional"]["guidance"]
    parts = []
    for area in AREAS:
        entry = areas.get(area["id"])
        if not entry:
            continue
        instruction = ("Ask me case by case before proposing a decision or arrangement in this area; "
                       "ask for my guidance rather than inventing a policy.") if entry["mode"] == "ask" else ""
        parts.append(area["label"] + "\n" + "\n".join(part for part in (instruction, entry["guidance"]) if part))
    content = "\n\n".join(parts)
    if len(content) > POLICY_LIMIT:
        raise ValueError(f"Term policies together must be at most {POLICY_LIMIT} characters, including area headings and handling instructions.")
    return content
