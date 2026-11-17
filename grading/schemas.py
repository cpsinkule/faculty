"""Generation constraints complement, never replace, backend validation."""
import copy

from .models import LAYOUT_KINDS

STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
CONFIDENCE = {"type": "number", "minimum": 0, "maximum": 1}


def array(value):
    return {"type": "array", "items": value}


def object_(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


BOX = {"anyOf": [{"type": "null"}, {"type": "array", "items": CONFIDENCE, "minItems": 4, "maxItems": 4}]}
VISUAL_EVIDENCE = {"line_id": STRING, "student_text": STRING, "bbox": BOX, "crop": {"type": "integer", "minimum": 0}}
READING_ISSUES = array(object_({"line_ids": array(STRING), "kind": {"enum": ["transcription", "layout", "unreadable"]}, "description": STRING}))
FLAG_RULINGS = array(object_({"flag": STRING, "resolved": BOOLEAN, "note": STRING}))
ELEMENT = object_({"type": {"enum": ["text", "math"]}, "text": STRING, "latex": STRING, "bbox": BOX,
    "confidence": {"anyOf": [CONFIDENCE, {"type": "null"}]}, "crop": {"type": "integer", "minimum": 0},
    # Literal reconstruction: crossed-out work, insertions and unreadable marks are kept, never deleted or repaired.
    "state": {"enum": ["written", "crossed_out", "inserted", "unclear"]}})
TRANSCRIPT = object_({"lines": array(object_({"id": STRING, "elements": array(ELEMENT)})),
    "plain_text": STRING, "latex": STRING, "confidence": {"anyOf": [CONFIDENCE, {"type": "null"}]}, "ambiguities": array(STRING),
    "layout_relations": array(object_({"from_line": STRING, "to_line": STRING, "kind": {"enum": list(LAYOUT_KINDS)}, "description": STRING, "uncertain": BOOLEAN})),
    "reading_alternatives": array(object_({"line_id": STRING, "options": {"type": "array", "items": STRING, "minItems": 2, "maxItems": 6}, "description": STRING}))})
RECONCILE = object_({"transcript": TRANSCRIPT, "visual_confidence": CONFIDENCE})
def grade(evidence):
    """grade-v3: a mathematical interpretation independent of the rubric, then one decision per rubric item. Points are never a model's."""
    return object_({"interpretation": object_({"meaning": STRING, "method": STRING, "work_consistent": {"enum": ["yes", "no", "unclear"]},
        "attempts": {"type": "integer", "minimum": 0}, "error_graph": array(object_({
        "id": STRING, "caused_by": array(STRING), "line_ids": array(STRING), "description": STRING})),
        # What the student wrote as the answer, quoted from a transcript line. Evidence for grouping; it awards nothing.
        "final_answers": array(object_({"part": STRING, "text": STRING, "line_id": STRING}))}),
        "applications": array(object_({"rubric_item_id": STRING, "applied": BOOLEAN, "reason": STRING, "evidence": array(object_(evidence)),
            "origin_error_id": {"type": ["string", "null"]}, "confidence": CONFIDENCE, "ambiguity": {"type": ["string", "null"]}})),
        # Eligibility is semantic, not inferred from component points reaching the ceiling.
        "full_credit_eligible": BOOLEAN,
        "confidence": CONFIDENCE, "rubric_ambiguities": array(STRING), "review_reasons": array(STRING)})


GRADE = grade(VISUAL_EVIDENCE)
# The transcript grader has no image: its evidence is a quoted line, with no geometry and no crop.
TRANSCRIPT_GRADE = grade({"line_id": STRING, "student_text": STRING})
SECOND_VISION = grade(VISUAL_EVIDENCE)
for schema in (TRANSCRIPT_GRADE, SECOND_VISION):
    schema["properties"]["reading_issues"] = READING_ISSUES
    schema["required"].append("reading_issues")
# Cloud Standard's primary grader sees the crops too (`standard.PRIMARY_RULE`): the second grader's contract, reading issues included.
PRIMARY_VISION = SECOND_VISION
# The adversarial reviewer returns objections, never decisions or points.
ADVERSARIAL = object_({"objections": array(object_({"rubric_item_id": {"type": ["string", "null"]}, "claim": STRING, "evidence": STRING,
    "line_ids": array(STRING), "severity": {"enum": ["material", "minor"]}})), "no_material_contradiction": BOOLEAN,
    "transcript_disagreements": array(STRING), "confidence": CONFIDENCE})
COMPILE = object_({"mode": {"enum": ["positive", "negative"]}, "floor": {"type": "number"}, "ceiling": {"type": "number"},
    "groups": array(object_({"id": STRING, "description": STRING, "selection": {"enum": ["single", "multiple"]},
        "floor": {"type": "number"}, "ceiling": {"type": "number"}})),
    "items": array(object_({"id": STRING, "description": STRING, "points": {"type": "number"}, "group": {"type": ["string", "null"]},
        "follow_through": BOOLEAN, "repeat_penalty_for_same_originating_error": BOOLEAN, "stackable": BOOLEAN,
        "requires": array(STRING), "excludes": array(STRING), "exceptions": STRING, "notes": STRING, "original_source_text": STRING})),
    "notes": STRING, "warnings": array(STRING)})
PAGE_BOX = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1000}, "minItems": 4, "maxItems": 4}
LOCATE = object_({"questions": array(object_({"number": STRING, "label": STRING, "points_text": STRING,
    "header_bbox": PAGE_BOX, "response_bbox": PAGE_BOX, "continued": BOOLEAN, "confidence": CONFIDENCE,
    "warnings": array(STRING)})), "warnings": array(STRING)})
SCHEMAS = {"reconcile": RECONCILE, "grade": GRADE, "transcript_grade": TRANSCRIPT_GRADE, "second_vision": SECOND_VISION,
           "adjudicate": SECOND_VISION, "third_grade": SECOND_VISION, "adversarial": ADVERSARIAL, "compile": COMPILE, "locate": LOCATE,
           "primary_vision": PRIMARY_VISION}
# Final Arbitrator Mode: a grader's complete assessment plus the ruling. `applications` is [] unless the decision is "revise";
# `adopt` and each `flag` are narrowed per request to the assessments and flags that exist (enums of strings only).
ARBITRATE = grade(VISUAL_EVIDENCE)
ARBITRATE["properties"].update({"decision": {"enum": ["adopt", "revise", "unreliable"]}, "adopt": STRING, "reason": STRING,
    "flag_rulings": FLAG_RULINGS, "reading_issues": READING_ISSUES})
ARBITRATE["required"] = list(ARBITRATE["properties"])
SCHEMAS["arbitrate"] = ARBITRATE
SCHEMAS["reconcile_literal"] = RECONCILE
SCHEMAS["reconcile_context"] = {**RECONCILE,
    "properties": {**RECONCILE["properties"], "page_requests": array(object_({"crop": {"type": "integer", "minimum": 0}, "reason": STRING}))},
    "required": [*RECONCILE["required"], "page_requests"]}
# flag_review-v2: every flag is ruled on BEFORE the decision is written, so the decision follows from the rulings (a host with a
# decoding grammar writes the properties in this order).
SCHEMAS["flag_review"] = object_({"flag_rulings": FLAG_RULINGS, "reason": STRING,
    "decision": {"enum": ["finalize", "manual_review"]}})
# One question per request (requirement, 2026-09-21): the reviewer names items only; the question is the one it was asked about.
CONCERN_DECISION = {'title': STRING, 'decision': STRING, 'group': STRING,
    'choices': {'type': 'array', 'maxItems': 4, 'items': object_({'label': STRING, 'feedback': STRING, 'consequence': STRING})},
    # ONE QUESTION ONCE and AN EDIT ONLY THE INSTRUCTOR CAN MAKE (requirement, 2026-10-29; rubric_quality v13 and its siblings): the
    # general policy a decision asks about, and the points change the instructor already asked for, approved with one click.
    'topic': STRING, 'edit': object_({'lines': array(object_({'version': STRING, 'item_id': STRING, 'points': {'type': 'number'}}))})}
# BOOKKEEPING IS FIXED, NOT ASKED (requirement, 2026-10-24; rubric_quality v12 and its siblings): a finding with ONE right answer says
# how it is fixed, "none" for every decision. "key" lists the forms to add to an answer key part (by version where there are versions).
FIX = object_({'kind': {'enum': ['none', 'guidance', 'key']}, 'note': STRING,
    'keys': array(object_({'version': STRING, 'part': STRING, 'add': array(STRING)}))})
SCHEMAS['rubric_quality'] = object_({'summary': STRING,
    'suspected_errors': array(object_({'item_ids': array(STRING), 'message': STRING, **CONCERN_DECISION, 'fix': FIX})),
    'items': array(object_({'item_id': STRING, 'clarification': STRING, 'excludes': array(STRING), 'reason': STRING}))})
# LINKED VERSIONS (requirement, 2026-10-02): a finding that arises on several versions of one problem is ONE entry naming each of them
# with its own items (versions v4 / resolve v6), and the reviewer returns which lines correspond across the versions.
VERSION_ITEMS = object_({'version': STRING, 'item_ids': array(STRING)})
VERSION_LINE = object_({'version': STRING, 'item_id': STRING})
PROBLEM_ERROR = object_({'versions': array(STRING), 'item_ids': array(VERSION_ITEMS),
    'version_notes': array(object_({'version': STRING, 'note': STRING})), 'message': STRING, **CONCERN_DECISION, 'fix': FIX})
PROBLEM_CONSISTENCY = object_({'versions': array(STRING), 'item_ids': array(VERSION_ITEMS), 'message': STRING, **CONCERN_DECISION})
# Every version of a problem in ONE request (requirement, 2026-09-25: the reviewer sees all versions at once, so everything stays
# consistent across them): each version's items by its own IDs, and the differences between the versions' rubrics.
SCHEMAS['rubric_quality_versions'] = object_({'summary': STRING,
    'versions': array(object_({'version': STRING, 'summary': STRING,
        'items': array(object_({'item_id': STRING, 'clarification': STRING, 'excludes': array(STRING), 'reason': STRING}))})),
    'suspected_errors': array(PROBLEM_ERROR), 'consistency_issues': array(PROBLEM_CONSISTENCY),
    'parallel_lines': array(object_({'kind': {'enum': ['same', 'related']}, 'lines': array(VERSION_LINE), 'note': STRING}))})
# The same answer when some versions are settled (requirement, 2026-10-05): `versions` names only the versions asked.
SCHEMAS['rubric_quality_added'] = SCHEMAS['rubric_quality_versions']
# The listed concerns resolved with the instructor's comment on each (2026-09-25): the problem's items as a check returns them, starting
# from the window's clarifications and exclusions, one outcome per concern by its number, and only NEW findings in the two lists.
SCHEMAS['rubric_quality_resolve'] = object_({'summary': STRING,
    'versions': array(object_({'version': STRING, 'summary': STRING,
        'items': array(object_({'item_id': STRING, 'clarification': STRING, 'excludes': array(STRING), 'reason': STRING}))})),
    'concerns': array(object_({'concern': {'type': 'integer', 'minimum': 0}, 'resolved': BOOLEAN, 'how': STRING, 'not_applicable': array(STRING), **CONCERN_DECISION})),
    'suspected_errors': array(PROBLEM_ERROR), 'consistency_issues': array(PROBLEM_CONSISTENCY)})
# An answer key read from the blank exam where the rubric gives none (2026-09-25): parts as the rubric's full-credit line describes
# them, each with how it was found, or no parts and why.
SCHEMAS['answer_key_read'] = object_({'parts': array(object_({'label': STRING, 'accept': array(STRING), 'basis': STRING})), 'unreadable': STRING})
# A regrade request's reply drafted for the instructor to edit, and one sentence on whether the student's point holds (REGRADES).
SCHEMAS['regrade_reply'] = object_({'reply': STRING, 'assessment': STRING})
SCHEMAS['rubric_quality_group'] = object_({'groups': array(object_({'refs': array(STRING)})), 'notes': array(STRING)})
# Rubrics drafted from a blank assignment (requirement, 2026-09-23): the survey lists the printed problems of the whole template, then each
# problem's rubric is written in its own request. Points are the model's reading or proposal; the instructor decides them.
PRINTED_POINTS = {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]}
SCHEMAS["rubric_survey"] = object_({"problems": array(object_({"number": STRING, "label": STRING, "points": PRINTED_POINTS, "points_text": STRING,
    "pages": array({"type": "integer", "minimum": 1}), "parts": array(object_({"label": STRING, "points": PRINTED_POINTS})),
    "statement": STRING})), "notes": array(STRING)})
# PART CAPS (GRADING, 2026-10-15): `parts` is each lettered part's worth (printed, or the writer's division of the total); a deducting
# rubric's part lines are capped at it (`rubrics.capped_parts`), and nothing else keeps a part's worth.
PART_WORTHS = array(object_({"part": STRING, "points": {"type": "number", "minimum": 0}}))
SCHEMAS["rubric_write"] = object_({"solution": STRING, "final_answers": array(object_({"part": STRING, "answer": STRING})),
    "points": {"type": "number", "minimum": 0}, "parts": PART_WORTHS,
    "items": array(object_({"description": STRING, "points": {"type": "number"}, "clarification": STRING})), "notes": STRING})
# One version's rubrics adapted to another version (requirement, 2026-09-25): the problems of the two versions are paired by what they
# ask, then each reference rubric is rewritten for its paired problem, line for line. Points are never the model's: each adapted line
# keeps its reference line's points.
SCHEMAS["rubric_adapt_match"] = object_({"pairs": array(object_({"target": STRING, "reference": {"anyOf": [STRING, {"type": "null"}]}, "reason": STRING})),
    "notes": array(STRING)})
SCHEMAS["rubric_adapt"] = object_({"solution": STRING, "final_answers": array(object_({"part": STRING, "answer": STRING})),
    "items": array(object_({"id": STRING, "description": STRING})), "notes": STRING})
# Every version drafted at once (requirement, 2026-09-25: one button, so the model writes the versions' rubrics together and they use
# the same kinds of lines): each version is surveyed as before, one request groups each problem's copies across versions, and one
# request per problem writes ONE list of lines whose points are shared and whose wording is given per version.
SCHEMAS["rubric_group_versions"] = object_({"groups": array(object_({"members": array(object_({"version": STRING, "number": STRING}))})),
    "notes": array(STRING)})
SCHEMAS["rubric_write_versions"] = object_({"solutions": array(object_({"version": STRING, "solution": STRING,
    "final_answers": array(object_({"part": STRING, "answer": STRING}))})), "points": {"type": "number", "minimum": 0}, "parts": PART_WORTHS,
    # LINKED VERSIONS (2026-10-02): each shared line says whether it is the same on every version or the same step with another outcome.
    "items": array(object_({"points": {"type": "number"}, "link": {"enum": ["same", "related"]},
        "descriptions": array(object_({"version": STRING, "description": STRING, "clarification": STRING}))})),
    "notes": STRING})
# Final Quality Audit (requirement, 2026-09-27; grading/quality_audit.py). The text pass reads one question's final grades as compact
# rows and reports what looks inconsistent; the scan pass looks at the named responses' crops and says whether a finding holds. Both
# only report: suggestions name rubric line IDs to add or remove per response, and nothing is applied without the instructor.
SUGGESTION = object_({"ref": STRING, "add": array(STRING), "remove": array(STRING), "note": STRING})
SCHEMAS["quality_audit"] = object_({"summary": STRING, "findings": array(object_({
    "kind": {"enum": ["inconsistent", "misapplied", "outlier"]}, "severity": {"enum": ["high", "medium", "low"]},
    "refs": array(STRING), "lines": array(STRING), "explanation": STRING, "suggestions": array(SUGGESTION)}))})
SCHEMAS["quality_audit_confirm"] = object_({"verdict": {"enum": ["confirmed", "partly", "not_confirmed"]}, "explanation": STRING,
                                            "suggestions": array(SUGGESTION)})
SCHEMAS["quality_audit_blind"] = object_({"readings": array(object_({"ref": STRING, "work": STRING, "lines": array(STRING),
                                                                    "reason": STRING, "uncertain": BOOLEAN}))})
SCHEMAS["quality_audit_sample"] = SCHEMAS["quality_audit"]
# Flag resolution (requirement, 2026-09-28; grading/flag_resolution.py): one question and version, up to 25 held responses per request. Each
# ruling answers the response's flags first, then upholds an earlier assessment, re-ticks it, or leaves it with the instructor; since v2
# (requirement, 2026-09-29) it may correct or add the transcript lines the
# crop clearly shows and ask to see the whole page (`needs_page`). `clarifications` propose wording for the instructor to confirm in the
# rubric finalization, never applied by themselves.
_CHANGE = object_({"line_id": STRING, "after": STRING, "crop": {"type": "integer", "minimum": 0}, "saw": STRING,
    "elements": array(object_({"type": {"enum": ["math", "text"]}, "text": STRING, "latex": STRING, "state": {"enum": ["written", "crossed_out", "inserted"]}}))})
_RULING = grade(VISUAL_EVIDENCE)
_RULING["properties"] = {"ref": STRING, "flag_rulings": FLAG_RULINGS,
    "decision": {"enum": ["uphold", "retick", "instructor"]}, "uphold": STRING, "needs_page": BOOLEAN, "transcript_changes": array(_CHANGE),
    # v3: the possible readings of a mark that decides credit, each with the complete set of lines it gives ("instructor" only).
    "readings": array(object_({"reading": STRING, "lines": array(STRING)})),
    "reason": STRING, **_RULING["properties"],
    "reading_issues": READING_ISSUES}
_RULING["required"] = list(_RULING["properties"])
SCHEMAS["flag_resolution"] = object_({"rulings": array(_RULING),
    # v3: `linked` words the same rule for the same line on the problem's other versions, in their own values.
    "clarifications": array(object_({"line_id": STRING, "clarification": STRING, "why": STRING,
                                     "linked": array(object_({"version": STRING, "line_id": STRING, "clarification": STRING}))})), "summary": STRING})
# GRADING FLAG REVIEW DEDUCTIONS: old reviews retain their prompt and schema.
SCHEMAS["flag_resolution_adjustments"] = copy.deepcopy(SCHEMAS["flag_resolution"])
_adjusted = SCHEMAS["flag_resolution_adjustments"]["properties"]["rulings"]["items"]
_adjusted["properties"].update({"expected_score": {"type": ["number", "null"]},
    "deductions": array(object_({"id": STRING, "applied": BOOLEAN, "reason": STRING}))})
_adjusted["required"] = list(_adjusted["properties"])
# GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (requirement, 2026-11-14; grading/flag_look.py): where the marks are that a flag review left
# with the instructor to read. The crop is a range, never an integer enum (`providers.cloud.grading_schema`); `flag_look.spots_of`
# still drops a spot on a crop that does not exist.
SCHEMAS["flag_look"] = object_({"spots": {"type": "array", "minItems": 1, "maxItems": 3, "items": object_({
    "label": STRING, "crop": {"type": "integer", "minimum": 0}, "bbox": {"type": "array", "items": CONFIDENCE, "minItems": 4, "maxItems": 4}})}})
# Quizzes drafted from the instructor's material (requirement, 2026-09-28; grading/quiz_drafts.py): the writer returns the whole quiz as
# structured data, every version of each problem side by side, which Faculty assembles into LaTeX (document_identity/quiz_latex.py) and
# validates (`quiz_latex.clean`); the check reads the printed pages and reports what to fix; each problem's rubric is then drafted for all
# its versions at once, in the all-versions rubric writer's own shape, so `rubric_generation.family_rubric` validates it.
QUIZ_VERSION = object_({"version": STRING, "statement": STRING, "left": STRING, "parts": array(object_({"label": STRING, "text": STRING})),
    "choices": array(STRING), "correct_choice": STRING, "figure": STRING, "figure_side": {"enum": ["right", "below", ""]}, "scaffold": STRING,
    "solution": STRING, "final_answers": array(object_({"part": STRING, "answer": STRING}))})
# An answer gives the whole quiz (base "none"), or starts from the request's previous quiz or the writer's last compile and lists only
# its edits (2026-10-01): each replaces one version's whole content of a problem and/or sets the problem's page, space, tag or rubric
# feedback; null keeps what is there (`quiz_drafts.resolved`).
QUIZ_EDIT = object_({"problem": {"type": "integer", "minimum": 1}, "version": STRING, "content": {"anyOf": [QUIZ_VERSION, {"type": "null"}]},
    "page": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}, "space": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
    "tag": {"anyOf": [STRING, {"type": "null"}]}, "rubric_feedback": {"anyOf": [STRING, {"type": "null"}]}})
SCHEMAS["quiz_write"] = object_({"base": {"enum": ["none", "previous", "compiled"]}, "sections_line": STRING, "directions": STRING,
    "problems": array(object_({
    "kind": {"enum": ["multiple_choice", "free_response"]}, "tag": STRING, "points": {"type": "number", "minimum": 0},
    "page": {"type": "integer", "minimum": 1}, "space": {"type": "number", "minimum": 0},
    "parts": array(object_({"label": STRING, "points": {"type": "number", "minimum": 0}})), "rubric_feedback": STRING,
    "versions": array(QUIZ_VERSION)})), "edits": array(QUIZ_EDIT), "notes": STRING, "changes": STRING})
SCHEMAS["quiz_check"] = object_({"issues": array(object_({"version": STRING, "problem": {"type": "integer", "minimum": 0},
    "kind": {"enum": ["correctness", "figure", "layout", "versions", "wording"]}, "issue": STRING, "fix": STRING})), "notes": STRING})
SCHEMAS["quiz_rubric"] = SCHEMAS["rubric_write_versions"]
# A makeup version (GRADING "MAKEUP VERSIONS") is written in the quiz writer's own shape, one version letter.
SCHEMAS["makeup_write"] = SCHEMAS["quiz_write"]

# A course's question bank (requirement, 2026-10-01; question_bank/runs.py). Tagging names outline entries by their request refs, reuses or
# writes one learning objective, and picks a difficulty and one to three types (keys of `question_bank.content.TYPES`); the backend still
# refuses an unknown ref or type. Reading a PDF transcribes each problem that starts on the pages read, never solving one.
SCHEMAS["bank_tag"] = object_({"questions": array(object_({"ref": STRING, "nodes": array(STRING),
    "objectives": array(object_({"ref": STRING, "parent": STRING, "text": STRING})), "difficulty": {"enum": ["easy", "medium", "hard"]},
    "types": array({"enum": ["computation", "word_problem", "setup", "graph", "interpretation", "concept"]}), "reason": STRING})), "notes": STRING})
# Versions of one assessment's problems linked (requirement, 2026-10-01): groups of
# question refs, at most one per version; the backend refuses a repeated question or two of one version.
SCHEMAS["bank_versions"] = object_({"groups": array(object_({"refs": array(STRING), "reason": STRING})), "notes": STRING})
# bank_extract v2 (requirement, 2026-10-03, from a textbook's exercises): each problem's printed section, a box around each figure it relies
# on (cut as its picture when it is added), what to check in it, and whether the request listed every problem (else its pages are split).
SHARE = {"type": "number", "minimum": 0, "maximum": 1}
SCHEMAS["bank_extract"] = object_({"questions": array(object_({"section": STRING, "number": STRING, "page": {"type": "integer", "minimum": 1},
    "kind": {"enum": ["multiple_choice", "free_response"]}, "statement": STRING, "parts": array(object_({"label": STRING, "text": STRING, "points": PRINTED_POINTS})),
    "choices": array(STRING), "correct_choice": STRING, "points": PRINTED_POINTS, "final_answers": array(object_({"part": STRING, "answer": STRING})),
    "solution": STRING, "figures": array(object_({"page": {"type": "integer", "minimum": 1},
                                                  "box": object_({"left": SHARE, "top": SHARE, "right": SHARE, "bottom": SHARE}), "description": STRING})),
    "check": array(STRING)})), "notes": array(STRING), "complete": BOOLEAN})
