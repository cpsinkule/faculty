"""The question bank's model work (AGENTS.md "Question bank"): TAGGING questions and READING questions out of a PDF.

A run is claimed by the grading cloud dispatcher in the same turn as rubric and quiz-draft work (`grading/cloud.py` calls `claim_runs`,
`run`, `recover_runs`, `fail_run`), and every request is an ordinary cloud call owned by the run (`frq_cloud_calls.bank_run_id`): its
receipt is kept, an interrupted request is settled from its durable call ID, an answer already bought is replayed rather than bought
again, and an uncertain outcome is never resent past the workspace's limits. The model is the Rubric drafting and review model, as for
drafted quizzes. An answer the backend refuses is asked once more with the reason; a second refusal fails the run, which waits for the
instructor's retry.

TAGGING (`bank_tag`, rule `bank-tag-v1`): up to 15 questions per request with the course's outline (each entry by a short reference),
the difficulties and the question types. For each question the model names the outline entries it belongs under (most specific first),
reuses a learning objective or writes a new one under a section or topic, and picks one difficulty and one to three types. The
instructor chose that tags apply AT ONCE; a tag the instructor gave after the run was asked for is never replaced by it.

READING A PDF (`bank_extract`, rule `bank-extract-v2`): the PDF's pages are read in the document sandbox (text and a picture of each,
prepared once and kept beside the upload), then six pages per request, with the next page's text so a problem that runs over is kept
whole; the model transcribes each problem that STARTS on those pages, never solving one or inventing an answer. v2 (2026-10-03, a
textbook's exercises): pages that print more problems than one answer lists are read again in halves, never cut short; each problem
keeps the section printed above it (carried over from earlier pages), a box and a description for each figure it relies on (cut as its
picture when it is added) and what to check in it. Its LaTeX is tidied as the
page shows it (`content.tidy_latex`: "$x = $" spans closed, thousands separators in mathematics written {,}), and a "select all that
apply" problem keeps its key as its final answer (a bank question names one correct choice). What it finds waits for the instructor,
who adds the questions they want.
"""
from __future__ import annotations

import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from werkzeug.exceptions import NotFound

from course_storage import timestamp
from rosters import connect

from . import content as contents, jev_tags
from .pictures import MAX_CAPTION, REGION_MIN

TAG_RULE = "bank-tag-v1"
VERSIONS_RULE = "bank-versions-v1"
EXTRACT_RULE = "bank-extract-v2"
PAGES_PER_REQUEST = 6
PAGE_TEXT = 6000
NEXT_TEXT = 1500
PICTURE_WIDTH = 1500
PARALLEL = 4
MAX_NEW_OBJECTIVES = 1
MAX_FOUND = 200       # problems one answer may list; pages that print more are read in smaller requests (never cut short)
CUT_OFF = ("length", "max_tokens", "MAX_TOKENS")  # an answer that outgrew its output budget: its pages are split too


class QuestionBankRuns:
    # -- The dispatcher's side (grading/cloud.py) -----------------------------------------------------------------------------
    @staticmethod
    def claim_runs(db, now, limit):
        """Claim up to `limit` waiting runs inside the dispatcher's own claiming transaction."""
        units = []
        if limit <= 0:
            return units
        for row in db.execute("SELECT * FROM question_bank_runs WHERE status='pending' AND not_before<=? ORDER BY updated_at, rowid LIMIT ?", (now, limit)).fetchall():
            db.execute("UPDATE question_bank_runs SET status='running', updated_at=? WHERE id=?", (now, row["id"]))
            units.append(("bank_run", dict(row), None))
        return units

    @staticmethod
    def recover_runs(db):
        """The dispatcher restarted: a run left 'running' lost its process and waits again; its calls' receipts decide what is resent."""
        db.execute("UPDATE question_bank_runs SET status='pending', updated_at=? WHERE status='running'", (timestamp(),))

    @staticmethod
    def fail_run(db, run_id, error):
        db.execute("UPDATE question_bank_runs SET status='failed', error=?, updated_at=? WHERE id=? AND status='running'", (error, timestamp(), run_id))

    def run(self, task):
        """Carry out one claimed run and record its outcome (tags are applied in the same transaction that completes a tagging run)."""
        from grading.cloud import RetryLater, later
        from grading.providers.base import ProviderError
        profile = json.loads(task["profile_json"])
        endpoint, settings = profile["endpoint"], profile["settings"]
        status, result, error, after = "complete", None, "", ""
        versions = task["kind"] == "tag" and task.get("purpose") == "versions"
        try:
            result = (self._versions(task, endpoint, settings) if versions else self._tag(task, endpoint, settings) if task["kind"] == "tag"
                      else self._extract(task, endpoint, settings))
        except RetryLater as waiting:
            status, after = "pending", later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound, OSError) as failure:
            status = "failed"
            error = (f"The model could not link these questions' versions: {failure}" if versions
                     else f"The model could not tag these questions: {failure}" if task["kind"] == "tag"
                     else f"The model could not read the questions out of this PDF: {failure}")[:2000]
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT status FROM question_bank_runs WHERE id=?", (task["id"],)).fetchone()
            if not current or current["status"] != "running":
                return  # stopped by the instructor while the model worked: nothing it found is applied
            if status == "complete" and versions:
                result["applied"] = self._apply_versions(db, task, result)
            elif status == "complete" and task["kind"] == "extract":
                result["banked"] = self._bank_bubbles(db, task, result)
            elif status == "complete" and task["kind"] == "tag":
                result["applied"] = self._apply_tags(db, task, result)
            db.execute("UPDATE question_bank_runs SET status=?, result_json=?, error=?, not_before=?, updated_at=? WHERE id=? AND status='running'",
                       (status, json.dumps(result, ensure_ascii=False) if status == "complete" else None, error, after, timestamp(), task["id"]))
            db.commit()

    def _ask(self, task, endpoint, settings, step, prompt, payload, images, check, tools=None):
        """One request, asked once more when its answer is refused, the second time with the reason. `tools` (a subscription connection
        only): what the model may call while it answers (`grading/providers/tools.py`)."""
        refused = None
        for attempt in (0, 1):
            body = payload if not attempt else {**payload, "refused": f"Your previous answer was refused: {refused}"}
            reply = self.grading.cloud_call({"bank_run_id": task["id"], "step": step, "task_attempt": attempt}, "rubric_reviewer", endpoint,
                                            {"prompt": prompt, "payload": body, "images": images, **({"reask": True} if attempt else {}),
                                             **({"tools": tools} if tools else {})}, settings)
            try:
                return check(reply["output"])
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    # -- Tagging ------------------------------------------------------------------------------------------------------------
    def _tag_inputs(self, task):
        with connect(self.path) as db:
            code = db.execute("SELECT code FROM course_definitions WHERE id=?", (task["definition_id"],)).fetchone()["code"]
            outline = self._standing_outline(db, task["definition_id"])
            members = [dict(row) for row in db.execute("""SELECT m.question_id, m.content_revision, r.content_json, q.source_json, q.frq_question_id, q.upload_id
                FROM question_bank_run_questions m JOIN question_bank_revisions r ON r.question_id=m.question_id AND r.revision=m.content_revision
                JOIN question_bank_questions q ON q.id=m.question_id WHERE m.run_id=? ORDER BY q.family, q.created_at, q.rowid""", (task["id"],))]
            links = {row["question_id"]: dict(row) for row in db.execute("""SELECT l.* FROM question_bank_assessment_links l JOIN question_bank_run_questions m
                ON m.question_id=l.question_id WHERE m.run_id=? AND l.revision=(SELECT MAX(revision) FROM question_bank_assessment_links WHERE question_id=l.question_id)""",
                (task["id"],))}
            questions = [{"id": m["question_id"], "frq_question_id": m["frq_question_id"], "upload_id": m["upload_id"], "revision": m["content_revision"],
                          "content": json.loads(m["content_json"]), "source": json.loads(m["source_json"]), "family": "", "created_at": "",
                          "printed_link": links.get(m["question_id"])} for m in members]
            self._sources(db, questions)
        return code, outline, questions

    def _tag(self, task, endpoint, settings):
        code, outline, questions = self._tag_inputs(task)
        if not questions:
            return {"rule": TAG_RULE, "questions": [], "notes": ""}
        refs = {f"N{i}": node for i, node in enumerate(outline, 1)}
        by_id = {node["id"]: ref for ref, node in refs.items()}
        asked = {f"Q{i}": q for i, q in enumerate(questions, 1)}
        # A section a PDF printed the question under (a textbook's own heading) is where it belongs: Jev and the model place it there.
        printed = {ref: entry for ref, q in asked.items() if (entry := contents.printed_entry((q.get("source") or {}).get("section"), outline))}
        # JEV BANK-TAGS: Jev places what it is sure of first; the model then writes only those questions' objectives.
        sure = jev_tags.place(getattr(self, "jev", None), task["definition_id"], questions, outline,
                              printed={asked[ref]["id"]: entry for ref, entry in printed.items()})
        placed = {ref: sure[q["id"]] for ref, q in asked.items() if q["id"] in sure}
        every = {node["id"]: node for node in outline}

        def under(node_id, top):
            while node_id:
                if node_id == top:
                    return True
                node_id = (every.get(node_id) or {}).get("parent_id")
            return False
        payload = {
            "course": code,
            "outline": [{"ref": ref, "level": node["level"], "number": node["number"], "title": node["title"],
                         "parent": by_id.get(node["parent_id"])} for ref, node in refs.items()],
            "difficulties": list(contents.DIFFICULTIES),
            "types": [{"key": key, "label": label, "description": text} for key, (label, text) in contents.TYPES.items()],
            "questions": [{"ref": ref, "kind": contents.KIND_LABELS[q["content"]["kind"]], "points": q["content"]["points"], "source": q["source_label"],
                           **({"label": q["source"]["tag"]} if q["source"].get("tag") else {}),
                           **({"printed_in": by_id[printed[ref]]} if ref in printed else {}),
                           **{k: v for k, v in contents.tagger_reads(q["content"]).items() if k not in ("kind", "points")},
                           **({"placed": {"nodes": [by_id[n] for n in placed[ref]["nodes"]], "difficulty": placed[ref]["difficulty"],
                                          "types": placed[ref]["types"]}} if ref in placed else {})} for ref, q in asked.items()],
        }

        def check(value):
            if not isinstance(value, dict) or not isinstance(value.get("questions"), list):
                raise ValueError("The answer has no list of questions.")
            answered = {}
            for item in value["questions"]:
                ref = item.get("ref") if isinstance(item, dict) else None
                if ref not in asked or ref in answered:
                    raise ValueError("Answer each question once, by its ref.")
                nodes = item.get("nodes")
                if not isinstance(nodes, list) or any(n not in refs for n in nodes) or len(nodes) > 8:
                    raise ValueError(f"{ref}: name at most 8 outline entries, by their refs.")
                if refs and not nodes and not item.get("objectives"):
                    raise ValueError(f"{ref}: place the question under at least one outline entry.")
                objectives = []
                for objective in item.get("objectives") or []:
                    if not isinstance(objective, dict):
                        raise ValueError(f"{ref}: each objective is an object.")
                    existing = objective.get("ref") or ""
                    if existing:
                        if existing not in refs or refs[existing]["level"] != "objective":
                            raise ValueError(f"{ref}: {existing} is not a learning objective of the outline.")
                        objectives.append({"ref": existing})
                        continue
                    parent, text = objective.get("parent") or "", " ".join(str(objective.get("text") or "").split())
                    if parent not in refs or refs[parent]["level"] not in ("section", "topic"):
                        raise ValueError(f"{ref}: a new learning objective sits under a section or a topic, by its ref.")
                    if not 5 <= len(text) <= 200:
                        raise ValueError(f"{ref}: a learning objective is 5-200 characters.")
                    objectives.append({"parent": parent, "text": text})
                if sum("text" in o for o in objectives) > MAX_NEW_OBJECTIVES:
                    raise ValueError(f"{ref}: write at most {MAX_NEW_OBJECTIVES} new learning objective per question; reuse existing ones.")
                # QUESTION-BANK TAGGING (requirement, 2026-11-06): at most one learning objective, however it is named: an objective among
                # the outline entries, a reused one or a new one.
                # (The same objective named twice is one.)
                if len({n for n in nodes if refs[n]["level"] == "objective"} | {o["ref"] for o in objectives if "ref" in o}) + sum("text" in o for o in objectives) > 1:
                    raise ValueError(f"{ref}: give at most one learning objective per question.")
                if ref in printed and ref not in placed:
                    home = printed[ref]
                    named = [refs[n]["id"] for n in nodes] + [refs[o["ref"]]["id"] if "ref" in o else refs[o["parent"]]["id"] for o in objectives]
                    if not any(under(node_id, home) for node_id in named):
                        raise ValueError(f"{ref}: it was printed in {every[home]['number'] or every[home]['title']}; place it under that entry.")
                if item.get("difficulty") not in contents.DIFFICULTIES:
                    raise ValueError(f"{ref}: difficulty is easy, medium or hard.")
                types = item.get("types")
                if not isinstance(types, list) or not 1 <= len(types) <= 3 or any(t not in contents.TYPES for t in types):
                    raise ValueError(f"{ref}: choose one to three question types from the list.")
                answered[ref] = {"nodes": list(dict.fromkeys(nodes)), "objectives": objectives, "difficulty": item["difficulty"],
                                 "types": [t for t in contents.TYPES if t in types], "reason": " ".join(str(item.get("reason") or "").split())[:1000]}
                if ref in placed:
                    # JEV BANK-TAGS: the placement stands; the objective must sit under it (the section Jev chose).
                    home = placed[ref]["nodes"][0]
                    section = home if every[home]["level"] == "section" else every[home]["parent_id"]
                    for objective in objectives:
                        parent = refs[objective["ref"]]["id"] if "ref" in objective else refs[objective["parent"]]["id"]
                        if not under(parent, section):
                            raise ValueError(f"{ref}: its learning objective must sit under {every[section]['number'] or every[section]['title']}, "
                                             "where the question is placed.")
                    answered[ref].update(nodes=[by_id[n] for n in placed[ref]["nodes"]], difficulty=placed[ref]["difficulty"],
                                         types=placed[ref]["types"])
            if set(answered) != set(asked):
                raise ValueError("Answer every question.")
            return answered

        answered = self._ask(task, {**endpoint, "max_tokens": max(endpoint["max_tokens"], 8192)}, settings, "bank:tag", "bank_tag", payload, [], check)
        return {"rule": TAG_RULE, "questions": [{"question_id": asked[ref]["id"], "content_revision": asked[ref]["revision"],
                                                 "nodes": [refs[n]["id"] for n in a["nodes"]],
                                                 "objectives": [{"id": refs[o["ref"]]["id"]} if "ref" in o else {"parent": refs[o["parent"]]["id"], "text": o["text"]}
                                                                for o in a["objectives"]],
                                                 "difficulty": a["difficulty"], "types": a["types"],
                                                 "reason": (a["reason"] + " " if a["reason"] else "") + "Section, topic, difficulty and types placed by Jev."
                                                 if ref in placed else a["reason"],
                                                 **({"jev": {"calls": placed[ref]["calls"], "confidence": placed[ref]["confidence"]}} if ref in placed else {})}
                                                for ref, a in answered.items()]}

    def _apply_tags(self, db, task, result):
        """The model's tags, applied in the run's completing transaction. New learning objectives join the outline (one with the same
        wording under the same entry is reused). A question the instructor tagged after the run was asked for keeps their tags."""
        applied = 0
        objectives = {}
        for row in db.execute("""SELECT n.id, n.parent_id, r.title FROM question_bank_nodes n JOIN question_bank_node_revisions r ON r.node_id=n.id
                WHERE n.definition_id=? AND n.level='objective' AND r.retired=0
                AND r.revision=(SELECT MAX(revision) FROM question_bank_node_revisions WHERE node_id=n.id)""", (task["definition_id"],)):
            objectives[(row["parent_id"], contents.norm(row["title"]))] = row["id"]
        standing = {node["id"] for node in self._standing_outline(db, task["definition_id"])}
        for item in result["questions"]:
            latest = db.execute("SELECT revision, origin, created_at FROM question_bank_classifications WHERE question_id=? ORDER BY revision DESC LIMIT 1",
                                (item["question_id"],)).fetchone()
            if latest and latest["origin"] == "instructor" and latest["created_at"] > task["created_at"]:
                continue
            nodes = [n for n in item["nodes"] if n in standing]
            for objective in item["objectives"]:
                if "id" in objective:
                    if objective["id"] in standing:
                        nodes.append(objective["id"])
                    continue
                if objective["parent"] not in standing:
                    continue
                key = (objective["parent"], contents.norm(objective["text"]))
                if key not in objectives:
                    objectives[key] = self._new_node(db, task["definition_id"], objective["parent"], "objective", "model", None, "", objective["text"],
                                                     self._next_position(db, task["definition_id"], objective["parent"]), "automatic")
                    standing.add(objectives[key])
                nodes.append(objectives[key])
            self._classify(db, item["question_id"], (latest["revision"] if latest else 0) + 1, item["content_revision"], list(dict.fromkeys(nodes))[:12],
                           item["difficulty"], item["types"], "model", "automatic", run_id=task["id"], reason=item["reason"])
            applied += 1
        return applied

    # -- Linking versions -----------------------------------------------------------------------------------------------------
    def _versions(self, task, endpoint, settings):
        """One request for one assessment: every question of it, each with its version and printed number; the model names the groups
        that are versions of one problem (at most one question per version)."""
        _, _, questions = self._tag_inputs(task)
        if len(questions) < 2:
            return {"rule": VERSIONS_RULE, "groups": [], "notes": ""}
        asked = {f"Q{i}": q for i, q in enumerate(questions, 1)}
        version = {ref: q["version"] for ref, q in asked.items()}
        payload = {"assessment": (questions[0].get("printed") or {}).get("title") or questions[0]["source_label"],
                   "versions": sorted({v for v in version.values() if v}),
                   "questions": [{"ref": ref, "version": q["version"], "number": (q.get("printed") or {}).get("number") or q["content"]["number"],
                                  **{k: v for k, v in contents.tagger_reads(q["content"]).items() if k not in ("solution", "figures")}} for ref, q in asked.items()]}

        def check(value):
            if not isinstance(value, dict) or not isinstance(value.get("groups"), list):
                raise ValueError("The answer has no list of groups.")
            seen, groups = set(), []
            for group in value["groups"]:
                refs = group.get("refs") if isinstance(group, dict) else None
                if not isinstance(refs, list) or len(refs) < 2 or any(r not in asked for r in refs):
                    raise ValueError("Each group names two or more questions by their refs.")
                if seen & set(refs) or len(set(refs)) != len(refs):
                    raise ValueError("A question belongs to one group at most.")
                letters = [version[r] for r in refs]
                if len(set(letters)) != len(letters):
                    raise ValueError("A group holds at most one question of each version.")
                seen |= set(refs)
                groups.append({"refs": refs, "reason": " ".join(str(group.get("reason") or "").split())[:500]})
            return groups

        groups = self._ask(task, {**endpoint, "max_tokens": max(endpoint["max_tokens"], 8192)}, settings, "bank:versions", "bank_versions", payload, [], check)
        return {"rule": VERSIONS_RULE, "groups": [{"questions": [asked[r]["id"] for r in g["refs"]], "reason": g["reason"]} for g in groups]}

    def _apply_versions(self, db, task, result):
        """The model's groups, applied in the run's completing transaction, to questions never linked before: a group joins the one group
        some of its members are in already, or becomes a new group. A link the instructor made or removed, and a quiz's own, stand."""
        applied = 0
        for group in result["groups"]:
            current = {}
            for question_id in group["questions"]:
                row = db.execute("SELECT group_id FROM question_bank_version_links WHERE question_id=? ORDER BY revision DESC LIMIT 1", (question_id,)).fetchone()
                current[question_id] = row
            free = [q for q, row in current.items() if row is None]
            existing = {row["group_id"] for row in current.values() if row is not None and row["group_id"]}
            if not free or len(existing) > 1 or (not existing and len(free) < 2):
                continue
            target = next(iter(existing)) if existing else str(uuid.uuid4())
            for question_id in free:
                self._version_link(db, question_id, target, "model", "automatic", run_id=task["id"], reason=group["reason"])
                applied += 1
        return applied

    # -- Reading a PDF ------------------------------------------------------------------------------------------------------
    def _prepared_pages(self, upload):
        """The PDF's pages, read once in the document sandbox (text and a picture of each) and kept beside the upload."""
        from PIL import Image
        from document_identity.storage import publish, read_verified
        from document_identity.template_conversion import convert_template
        base = f"question-bank/uploads/{upload['id']}/pages"
        with self.rosters.lock(), self.documents.files.files("definition", upload["definition_id"]) as root:
            manifest = root / base / "pages.json"
            if manifest.is_file():
                listing = json.loads(manifest.read_text())
                return [{"number": p["number"], "text": p["text"], "picture": read_verified(root, p["path"], p["sha256"])} for p in listing["pages"]]
            original = read_verified(root, upload["path"], upload["source_sha256"])
        try:
            converted = convert_template({"questions.pdf": original})
        except ValueError as error:
            raise ValueError(f"{upload['source_name']} could not be read: {error}") from None
        pages, files = [], {}
        for page in converted["pages"]:
            with Image.open(io.BytesIO(page["png"])) as image:
                image = image.convert("RGB")
                if image.width > PICTURE_WIDTH:
                    image = image.resize((PICTURE_WIDTH, round(image.height * PICTURE_WIDTH / image.width)), Image.LANCZOS)
                out = io.BytesIO()
                image.save(out, "JPEG", quality=85, optimize=True)
            picture = out.getvalue()
            path = f"{base}/page-{page['number']}.jpg"
            files[path] = picture
            pages.append({"number": page["number"], "text": page["text"][:PAGE_TEXT], "path": path, "picture": picture})
        import hashlib
        with self.rosters.lock(), self.documents.files.files("definition", upload["definition_id"], write=True) as root:
            for path, data in files.items():
                publish(root / path, data)
            publish(root / base / "pages.json", json.dumps({"pages": [{"number": p["number"], "text": p["text"], "path": p["path"],
                                                                        "sha256": hashlib.sha256(p["picture"]).hexdigest()} for p in pages]}).encode())
        return pages

    @staticmethod
    def _page_tools(pages, endpoint):
        """On a Claude or ChatGPT subscription connection the reader may open ANY page of the PDF as it needs it (requirement, 2026-10-01),
        not only its own six: a problem that runs on, a figure, table or instructions printed elsewhere, or a region enlarged to read small
        print. It still transcribes only the problems that START on its pages."""
        from grading.drafting_tools import REGION, closer, region_of
        from grading.providers import tools
        if not tools.offered(endpoint):
            return None
        numbered = {p["number"]: p for p in pages}

        def pdf_page(arguments):
            page = numbered.get(arguments.get("page")) if type(arguments.get("page")) is int else None
            if page is None:
                raise tools.ToolError(f"Choose a page from 1 to {len(pages)}.")
            region = region_of(arguments.get("region"))
            return (f"Page {page['number']} of {len(pages)}" + (" (the region asked for)" if region else "") + ". Its text as the PDF holds it:\n"
                    + (page["text"].strip() or "(none: a scanned or drawn page)"), [closer(page["picture"], region)])
        return tools.ToolSet("bank-reader-v1", [tools.Tool(
            "pdf_page", "One page of this PDF, any page, with its text: a page before or after yours where a problem continues or shares a "
            "figure, table or instructions, or a region of a page enlarged to read small print.",
            {"type": "object", "properties": {"page": {"type": "integer", "minimum": 1}, "region": REGION}, "required": ["page"]}, pdf_page)],
            budget=8, guide=("Open any other page of the PDF with pdf_page when a problem that starts on your pages continues, refers to a "
                             "figure, table or instructions printed elsewhere, or when print is too small to read for certain. Transcribe "
                             "only the problems that START on your pages, as before."))

    def _extract(self, task, endpoint, settings):
        from grading.cloud import RetryLater
        from grading.providers.base import ProviderError
        with connect(self.path) as db:
            upload = dict(db.execute("SELECT * FROM question_bank_uploads WHERE id=?", (task["upload_id"],)).fetchone())
            code = db.execute("SELECT code FROM course_definitions WHERE id=?", (task["definition_id"],)).fetchone()["code"]
        pages = self._prepared_pages(upload)
        if not pages:
            raise ValueError("The PDF has no pages.")
        chunks = [pages[start:start + PAGES_PER_REQUEST] for start in range(0, len(pages), PAGES_PER_REQUEST)]
        reader = {**endpoint, "max_tokens": max(endpoint["max_tokens"], 16384)}
        page_tools = self._page_tools(pages, endpoint)
        numbered = {p["number"] for p in pages}

        def ask(chunk, step):
            numbers = [p["number"] for p in chunk]
            following = pages[pages.index(chunk[-1]) + 1] if pages.index(chunk[-1]) + 1 < len(pages) else None
            payload = {"course": code, "file": upload["source_name"], "pages": [{"page": p["number"], "picture": i, "text": p["text"]} for i, p in enumerate(chunk)],
                       "first_page": numbers[0], "last_page": numbers[-1], "total_pages": len(pages),
                       "next_page": {"page": following["number"], "text": following["text"][:NEXT_TEXT]} if following else None}

            def check(value):
                if not isinstance(value, dict) or not isinstance(value.get("questions"), list):
                    raise ValueError("The answer has no list of questions.")
                if len(chunk) > 1 and (len(value["questions"]) > MAX_FOUND or value.get("complete") is False):
                    # Never fewer problems than the pages print (2026-10-03: a refusal at 80 had the reader stop at 80 of 101): these pages are
                    # read again in smaller requests instead.
                    raise Overflow(len(value["questions"]))
                if len(value["questions"]) > MAX_FOUND:
                    raise ValueError(f"The answer lists more than {MAX_FOUND} questions for one page.")
                found = []
                notes = [" ".join(str(n).split())[:500] for n in value.get("notes") or [] if str(n).strip()][:20]
                if value.get("complete") is False:
                    notes.append(f"Page {numbers[0]} holds more problems than the reader could list: look at it in the PDF.")
                for item in value["questions"]:
                    if not isinstance(item, dict) or item.get("page") not in numbers:
                        raise ValueError(f"Each question names the page it starts on, one of pages {numbers[0]}-{numbers[-1]}.")
                    raw = {k: item.get(k) for k in ("kind", "number", "statement", "parts", "choices", "correct_choice", "points", "final_answers", "solution")}
                    if raw["kind"] == "multiple_choice" and len(raw.get("choices") or []) < 2:
                        raw["kind"] = "free_response"
                    checks = [" ".join(str(c).split())[:500] for c in item.get("check") or [] if str(c).strip()][:10]
                    figures, said = [], []
                    # bank_extract v1 described one figure in `figure`; v2 gives each figure's page, box and description.
                    described = [{"description": item.get("figure")}] if isinstance(item.get("figure"), str) and item.get("figure").strip() else []
                    for figure in (item.get("figures") if isinstance(item.get("figures"), list) else described):
                        if not isinstance(figure, dict):
                            continue
                        description = " ".join(str(figure.get("description") or "").split())[:MAX_CAPTION]
                        if description:
                            said.append(f"Figure: {description}")
                        box = figure.get("box")
                        if "box" not in figure:
                            continue
                        region = figure_region(box)
                        if figure.get("page") in numbered and region:
                            figures.append({"page": figure["page"], "region": region, "description": description})
                        else:
                            checks.append("A figure it relies on could not be cut from the page: cut it from the PDF yourself.")
                    several = contents.several_keyed(raw["correct_choice"], len(raw.get("choices") or [])) if raw["kind"] == "multiple_choice" else []
                    if several:
                        # "Select all that apply" keys several choices; a bank question names one correct choice, so the key is its final answer.
                        keyed = ", ".join(letter.upper() for letter in several)
                        raw["correct_choice"] = ""
                        raw["final_answers"] = [*(raw["final_answers"] if isinstance(raw.get("final_answers"), list) else []), {"part": "", "answer": keyed}]
                        said.append(f"Select all that apply: the key marks {keyed}.")
                    raw["notes"] = "\n".join(said)[:contents.LIMITS["notes"]]
                    try:
                        content = contents.clean(contents.tidied(contents.clean(raw)))
                    except ValueError as problem:
                        notes.append(f"A question on page {item['page']} was left out: {problem}")
                        continue
                    found.append({"page": item["page"], "section": " ".join(str(item.get("section") or "").split())[:80], "content": content,
                                  **({"figures": figures} if figures else {}), **({"check": checks} if checks else {})})
                return found, notes

            return self._ask(task, reader, settings, step, "bank_extract", payload, [p["picture"] for p in chunk], check, page_tools)

        def read(chunk, step):
            """One request for these pages; when they print more than one answer can hold, each half of them is read by itself."""
            try:
                return ask(chunk, step)
            except Overflow:
                pass
            except ProviderError as failure:
                cut = failure.confirmation.get("output_limit") or failure.confirmation.get("finish_reason") in CUT_OFF
                if not cut or len(chunk) == 1:
                    raise
            middle = (len(chunk) + 1) // 2
            first, second = read(chunk[:middle], f"{step}:{chunk[0]['number']}-{chunk[middle - 1]['number']}"), \
                read(chunk[middle:], f"{step}:{chunk[middle]['number']}-{chunk[-1]['number']}")
            return first[0] + second[0], first[1] + second[1]

        results, waits = {}, []
        with ThreadPoolExecutor(max_workers=min(PARALLEL, len(chunks)), thread_name_prefix="bank-read") as pool:
            futures = {pool.submit(read, chunk, f"bank:extract:{index + 1}"): index for index, chunk in enumerate(chunks)}
            for future, index in futures.items():
                try:
                    results[index] = future.result()
                except RetryLater as waiting:
                    waits.append(waiting.seconds)
        if waits:
            raise RetryLater(min(waits), "A request to read the PDF is waiting for its host.")
        questions, notes = [], []
        for index in range(len(chunks)):
            found, said = results[index]
            questions += found
            for note in said:
                if note not in notes:
                    notes.append(note)
        # A problem printed under a section that began on an earlier page (read in an earlier request) belongs to that section.
        section = ""
        for question in questions:
            section = question["section"] = question["section"] or section
        return {"rule": EXTRACT_RULE, "pages": len(pages), "notes": notes,
                "questions": [{"index": i, "page": q["page"], **({"section": q["section"]} if q["section"] else {}), "content": q["content"],
                               **{k: q[k] for k in ("figures", "check") if k in q}} for i, q in enumerate(questions)]}


class Overflow(Exception):
    """A reading's pages printed more problems than one answer listed (or may list): they are read again in smaller requests. Not a
    ValueError, so it is never asked again of the same pages with a refusal."""


def figure_region(box):
    """A figure's box from the reader (shares of the page), as a picture's region, or None when it is not one."""
    if not isinstance(box, dict) or any(isinstance(box.get(k), bool) or not isinstance(box.get(k), (int, float)) for k in ("left", "top", "right", "bottom")):
        return None
    region = {k: min(1.0, max(0.0, float(box[k]))) for k in ("left", "top", "right", "bottom")}
    if region["right"] - region["left"] < REGION_MIN or region["bottom"] - region["top"] < REGION_MIN:
        return None
    return {k: round(v, 5) for k, v in region.items()}
