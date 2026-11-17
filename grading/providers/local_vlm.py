import base64
import copy
import json
import os
from contextlib import nullcontext
from pathlib import Path

from .. import config
from ..models import digest
from ..schemas import SCHEMAS
from .base import ProviderError, request_json


# Every prompt is a versioned file; results retain its name and hash. A changed contract gets a new version.
VERSIONS = {"recording_memo": 1,  # RECORDINGS "Memos (step 6)": what the instructor asked for, and the rules they laid down, in a memo they dictated alone
            "recording_class": 1,  # RECORDINGS "Classes (step 5)": the notes sections a class worked through and the days the instructor told it
            "recording_student_meeting": 1,  # RECORDINGS "Student meetings (step 4)": what was promised in a student's meeting, and what they said
            "recording_reading": 3,  # RECORDINGS "The reading": v3 (2026-11-13) has a ruling name its assessment by id or by the name it was called by; v2 asks for fewer, merged items after the first live reading gave 105
            "locate": 3, "compile": 2, "grade": 8, "reconcile": 9, "transcript_grade": 7, "second_vision": 5, "adversarial": 3, "adjudicate": 3, "third_grade": 3, "rubric_quality": 13, "arbitrate": 3,
            # reconcile v8 (2026-09-29): a free reading (`read`) is the reader's own and is only arranged, its numbers copied exactly.
            # read v3 / reconcile v9 (2026-09-29): a marked printed choice is written as its label, [marked: (b)] -> (b), never a bare circle.
            "read": 3, "flag_review": 3, "primary_vision": 2,
            # grade v8 / transcript_grade v7 / second_vision v5 / primary_vision v2 (requirement, 2026-10-26): the
            # earlier version word for word and three paragraphs: the approved deductions a grader decides (`grader_deductions`),
            # presentation the rubric does not name (letter case, $ and unit signs, spacing) and an empty answer's unquotable line.
            "reconcile_literal": 1, "reconcile_context": 1, "read_context": 1, "quality_audit_blind": 1, "quality_audit_sample": 1,
            "quality_audit": 1, "quality_audit_confirm": 1, "memory_edit": 1, "memory_rewrite": 1, "probe": 1,
            # Rubric drafts, v2 (requirement, 2026-09-23): any assignment (exam, quiz, homework, handout), not only exams.
            "rubric_survey": 2, "rubric_write": 10,
            # One version's rubrics adapted to another (requirement, 2026-09-25): pair the versions' problems, then adapt line for line.
            "rubric_adapt_match": 1, "rubric_adapt": 1,
            # Every version drafted at once (requirement, 2026-09-25): group the versions' problems, then one request per problem.
            "rubric_group_versions": 1, "rubric_write_versions": 9,
            # The rubric check sees every version of a problem at once (requirement, 2026-09-25), grouped first where needed.
            "rubric_quality_versions": 9, "rubric_quality_group": 1,
            # A check asks only what does not stand (requirement, 2026-10-05: Quiz 4's makeup Version E sent every version again): a
            # problem's confirmed versions come along settled, as the reference, and only the others are answered for.
            "rubric_quality_added": 6,
            # The concerns a check listed, resolved with the instructor's comments (requirement, 2026-09-25); v2: a comment per concern;
            # v3: the concerns without a comment are settled by the reviewer, consistently with the problem and the comments.
            # v4 / v6 (requirement, 2026-10-02, LINKED VERSIONS): one finding per problem naming its versions, the parallel lines
            # returned as same/related, and one answer carried to each version's own lines.
            "rubric_quality_resolve": 11,
            # rubric_write_versions v4 / quiz_rubric v4 (2026-10-02, LINKED VERSIONS): each shared line marked "same" or "related".
            # GRADING guided review: concise decisions and supported alternatives only; [] means free-text feedback.
            # The questions graders stopped at, settled before grading (2026-09-28, from Exam 1, Quiz 2 and Quiz 3): accepted forms
            # by meaning, omissions, methods, whose labels, where the answer is, one mistake one line, the no-credit line; writers v5 /
            # versions v3 also never write a partial line worth its part; reviewers v7 / versions v2 and resolver v4 also read the
            # instructor's comments on other problems and keep a comment's criterion general, with examples only as examples.
            # Held grades of one question ruled on together, 25 at a time, with their crops (requirement, 2026-09-28); v2 (2026-09-29): its
            # own prompt, not the arbitrator's: definitive rulings, transcript lines corrected from the crop, the whole page when work is cut off.
            # v3 (requirement, 2026-10-03): a response left with the instructor names its possible readings, each a complete set of lines.
            "flag_resolution": 3,
            "flag_resolution_adjustments": 1,
            # flag_look v1 (requirement, 2026-11-14): where on the crop the marks are that
            # a flag review left with the instructor to read; one small request per such response (`grading/flag_look.py`).
            "flag_look": 1,
            # An answer key read from the printed problem where the rubric gives none (requirement, 2026-09-25).
            # v2 (requirement, 2026-10-11): v1 word for word, and one paragraph: the request may carry the worked
            # solution, the drafting model's final answers and its correct choice, to be used instead of solving the problem again
            # (a drafted quiz's keys are read once while it is drafted; the plan's own reading sends a stored worked solution).
            "answer_key_read": 2,
            # regrade_reply v1 (2026-10-07, REGRADES "DRAFT"): the reply to a regrade request, drafted for the instructor to edit.
            "regrade_reply": 1,
            # A course's question bank (requirement, 2026-10-01): its questions tagged, and questions read out of a PDF.
            # bank_tag v2 (requirement, 2026-10-03, JEV BANK-TAGS): a question Jev already placed keeps that placement; only its objective.
            # bank_extract v2 (requirement, 2026-10-03, a textbook's exercises): the printed section, figure boxes, a check list per problem,
            # `complete`; a shared instruction is copied without its range. bank_tag v3 (the same day): a printed section places the question.
            "bank_tag": 3, "bank_extract": 2,
            # Versions of one assessment's problems linked (requirement, 2026-10-01).
            "bank_versions": 1,
            # A drafted quiz's rubrics also follow what the instructor wrote for the model when asking for it (requirement, 2026-10-01);
            # v3: they are sent the notes sections the quiz covers and build their lines on the methods the notes teach (the same day).
            # quiz_rubric v5 / rubric_write v6 / rubric_write_versions v5 (requirement, 2026-10-03, from Quiz 4's held grades): the doubt
            # list also settles undefined or problem-text letters, a relation other than "=", non-variables in a list, a wholly blank part
            # and marks that are not answers.
            "quiz_rubric": 10,
            # A quiz request carries its format, prefilled as one multiple-choice and three free-response problems (requirement, 2026-10-01);
            # quiz_write v3: an answer may give only its edits to the previous quiz or to the writer's last compile (the same day);
            # v4: the page prints no version anywhere and two small corner stamps (requirement, 2026-10-01).
            # v5: `course_notes`, the lecture-notes sections the instructor chose for the draft, and `bank_questions` filed under
            # them, models to change and never copy (requirement, 2026-10-28).
            # quiz_check v3 (requirement, 2026-10-11, among the small repeats they chose to remove: the quiz check skips unchanged problems): a later
            # revision's check is told which problems are `unchanged` since a check read them exactly as printed now, and reads only the rest.
            # quiz_check v4 (requirement, 2026-10-11): the check is told that no drafted page names its version on
            # purpose (QUIZZES "NO VERSION ON THE PAGE"); shown a past quiz that prints "Version A", it had remarked on the missing letter.
            # quiz_write v6, quiz_check v5, makeup_write v2 (requirement, 2026-11-04): the check and the
            # fix rounds are told what the instructor `asked` of the draft and keep it; the check had the answer lines just asked for taken off.
            "quiz_write": 6, "quiz_check": 5,
            # makeup_write v1 (2026-10-03, GRADING "MAKEUP VERSIONS"): one parallel version of a given assignment, quiz_write's layout rules.
            "makeup_write": 2,
            # memory_rule v2 (2026-10-10, ASSISTANT-MEMORY "A FOLLOW-UP'S REMEMBER THIS"): the instructor's own wording is kept
            # where it reads alone, and "you" is the Assistant, never "the teaching assistant" (v1 wrote that once of sixteen).
            "memory_rule": 2,
            # RUBRIC MEMORIES (2026-10-14, GRADING): rubric_memory v1 writes general rules and watches from one certified
            # assignment's rubric questions, the instructor's answers and the graders' doubts; the writers (rubric_write v7,
            # rubric_write_versions v6, quiz_rubric v6) and the reviewers (rubric_quality v9, rubric_quality_versions v5,
            # rubric_quality_added v2, rubric_quality_resolve v7) are their predecessors with one section on `standing_rules`:
            # a rule decides its cases, a watch only names them, and neither ever changes a line's required wording.
            "rubric_memory": 1,
            # PART CAPS (requirement, 2026-10-15, GRADING): the writers (rubric_write v8, rubric_write_versions v7, quiz_rubric v7)
            # return each lettered part's worth in `parts` and no longer note a part whose deductions exceed it, since Faculty caps
            # each part there; the reviewers (rubric_quality v10, rubric_quality_versions v6, rubric_quality_added v3,
            # rubric_quality_resolve v8) read the caps in `rubric.groups` and never ask about a part's overflow again.
            # THIS ASSIGNMENT COMES FIRST (requirement, 2026-10-15): the reviewers (rubric_quality v11,
            # rubric_quality_versions v7, rubric_quality_added v4, rubric_quality_resolve v9) and the quiz writer (quiz_rubric v8)
            # follow the instructor's comment, answer or feedback on THIS assignment over a standing rule where the two differ.
            # FEWER RUBRIC QUESTIONS (requirement, 2026-10-24): the reviewers (rubric_quality v12, rubric_quality_versions v8, rubric_quality_added v5,
            # rubric_quality_resolve v10) are their predecessors with two sections, the instructor's word on this assignment
            # (`assignment_choices`, `earlier_answers`) settling its cases, and a `fix` on every suspected error ("guidance" or "key"
            # only where it has one right answer); the resolver also settles a concern Faculty fixed. The writers (rubric_write v9,
            # rubric_write_versions v8, quiz_rubric v9) are their predecessors with one section on `assignment_choices`.
            # SETTLED BY THE INSTRUCTOR'S OWN SOURCES (requirement, 2026-10-29): the reviewers (rubric_quality v13,
            # rubric_quality_versions v9, rubric_quality_added v6, rubric_quality_resolve v11) are their predecessors with three
            # sections: four more cases fixed as guidance (a form the printed problem prescribes, a comment over its own line's
            # wording, one version against all the others, a reading the instructor's sources favor), `topic` on every decision (one
            # question once across problems) and `edit` (a points change the instructor asked for, approved with one click). The
            # writers (rubric_write v10, rubric_write_versions v9, quiz_rubric v10) write "with supporting work" only when a line
            # charges missing work, and settle the same case the same way in every part and version.
            }


def prompt(name):
    path = Path(__file__).parent.parent / "prompts" / f"{name}-v{VERSIONS.get(name, 1)}.txt"
    content = path.read_text()
    return content, {"prompt": path.name, "prompt_sha256": digest(content.encode())}


class LocalVlmProvider:
    def __init__(self, settings, kind="local_verifier"):
        self.settings, self.kind = settings, kind
        self.url = config.endpoint(kind)
        self.model = settings["nebius_model" if kind == "nebius" else kind + "_model"]
        self.backend = "compatible" if kind == "nebius" else settings.get(kind + "_backend", "compatible")

    def _local_model(self):
        info = request_json(self.url.removesuffix("/v1") + "/api/show", body={"model": self.model}, timeout=5)
        if info.get("remote_host") or info.get("remote_model") or not info.get("details", {}).get("quantization_level"):
            raise ProviderError("Fully local grading requires installed local model weights; remote Ollama models are refused.")
        if "vision" not in info.get("capabilities", []):
            raise ProviderError("Choose a local model with image input support.")
        return info

    def _headers(self):
        name = "NEBIUS_API_KEY" if self.kind == "nebius" else "FACULTY_GRADING_LOCAL_KEY"
        key = os.environ.get(name)
        return {"Authorization": "Bearer " + key} if key else {}

    def health(self):
        if not self.url or (self.kind == "nebius" and not self._headers()):
            return {"status": "not_configured", "model": self.model}
        try:
            if self.backend == "ollama":
                self._local_model()
                return {"status": "available", "model": self.model, "backend": self.backend}
            result = request_json(self.url + "/models", headers=self._headers(), timeout=5)
            models = [v.get("id") for v in result.get("data", []) if isinstance(v, dict)]
            return {"status": "available" if self.model in models else "model_unavailable", "model": self.model}
        except ProviderError as error:
            return {"status": "authentication_failed" if "Authentication" in str(error) else "unavailable", "model": self.model}

    def _card(self):
        """The whole local GPU for one request (EMBEDDING "LENDS THE GPU"; requirement, 2026-10-11).
        The local model and the embedding worker do not fit on the card together: the worker moves off it first and takes it
        back after the answer, and its loan outlasts the request so a turn that stops midway returns the card anyway. A cloud
        host borrows nothing."""
        if self.kind == "nebius":
            return nullcontext()
        from faculty_embedding.client import gpu_lent
        from faculty_embedding.config import MAX_LEND
        return gpu_lent(min(self.settings["timeout"] + 30, MAX_LEND))

    def invoke(self, name, payload, images):
        if not self.url or (self.kind == "nebius" and not self._headers()):
            raise ProviderError("Inference service is not configured. Configure the server endpoint and model.")
        instruction, metadata = prompt(name)
        crop_count = (len(images) + 1) // 2 if name == "reconcile" else len(images)
        if crop_count and name in ("reconcile", "grade"):
            instruction += f"\nThere are {crop_count} original response crops. The ONLY valid crop IDs are {list(range(crop_count))}. Enhanced image copies refer to the same original crop IDs. For one original crop, every crop field is 0."
        content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, allow_nan=False)}]
        content.extend({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}} for image in images)
        if self.backend == "ollama":
            info = self._local_model()
            schema = copy.deepcopy(SCHEMAS[name])
            if crop_count and name == "reconcile":
                schema["properties"]["transcript"]["properties"]["lines"]["items"]["properties"]["elements"]["items"]["properties"]["crop"] = {"enum": list(range(crop_count))}
            if name == "grade":
                item_ids = [item["id"] for item in payload["rubric"]["items"]]
                applications = schema["properties"]["applications"]
                applications.update(minItems=len(item_ids), maxItems=len(item_ids))
                applications["items"]["properties"]["rubric_item_id"] = {"enum": item_ids}
                if crop_count:
                    applications["items"]["properties"]["evidence"]["items"]["properties"]["crop"] = {"enum": list(range(crop_count))}
            if name == "compile" and "imported_rubric" in payload:
                imported = payload["imported_rubric"]
                item_ids = [item["id"] for item in imported["items"]]
                entries = schema["properties"]["items"]
                entries.update(minItems=len(item_ids), maxItems=len(item_ids))
                entries["items"]["properties"]["id"] = {"enum": item_ids}
                for field in ("mode", "floor", "ceiling"):
                    schema["properties"][field] = {"enum": [imported[field]]}
            # A decoding grammar alone does not explain the field meanings to
            # the model. Ground the generation in the same explicit contract.
            instruction += "\nOutput JSON schema (return a compact instance, not the schema):\n" + json.dumps(schema, separators=(",", ":"))
            with self._card():
                raw = request_json(self.url.removesuffix("/v1") + "/api/chat", headers=self._headers(), timeout=self.settings["timeout"], body={
                    "model": self.model, "messages": [{"role":"system", "content":instruction},
                        {"role":"user", "content":json.dumps(payload, ensure_ascii=False, allow_nan=False),
                         "images":[base64.b64encode(image).decode() for image in images]}],
                    "format":schema, "stream":False, "think":False, "keep_alive":0,
                    "options":{"temperature":0, "num_predict":8192, "num_ctx":16384}})
            metadata.update(backend="ollama", model_details=info["details"], model_manifest_sha256=digest(info), output_schema_sha256=digest(schema), instruction_sha256=digest(instruction.encode()))
            raw["usage"] = {"prompt_tokens": raw.get("prompt_eval_count", 0), "completion_tokens": raw.get("eval_count", 0),
                "total_tokens": raw.get("prompt_eval_count", 0) + raw.get("eval_count", 0)}
        else:
            with self._card():
                raw = request_json(self.url + "/chat/completions", headers=self._headers(), timeout=self.settings["timeout"], body={
                    "model": self.model, "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": content}],
                    "temperature": 0, "max_tokens": 8192, "stream": False, "store": False, "response_format": {"type": "json_object"}})
        metadata = {**metadata, "provider": self.kind, "requested_model": self.model,
            "instruction_sha256": digest(instruction.encode()),
            "reported_model": raw.get("model"), "system_fingerprint": raw.get("system_fingerprint"),
            "endpoint_sha256": digest(self.url.encode()), "usage": raw.get("usage", {}), "temperature": 0}
        try:
            choice = {"message": raw.get("message"), "finish_reason": raw.get("done_reason")} if self.backend == "ollama" else raw["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError()
            result = json.loads(choice["message"]["content"], parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            if not isinstance(result, dict):
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("Model output was malformed or incomplete. No grade was accepted.", raw=raw, metadata=metadata) from None
        return {"output": result, "raw": raw, "metadata": metadata}

    def verify(self, payload, images):
        return self.invoke("reconcile", payload, images)

    def grade(self, payload, images):
        return self.invoke("grade", payload, images)

    def locate(self, payload, images):
        return self.invoke("locate", payload, images)

    def compile(self, source, max_points, *, rubric=None):
        material = {"imported_rubric": rubric} if rubric is not None else {"original_source": source}
        return self.invoke("compile", {**material, "max_points": max_points}, [])
