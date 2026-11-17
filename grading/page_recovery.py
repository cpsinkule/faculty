"""One opt-in page round for a Cloud direct reading whose answer extends beyond its crop.

The term-owned task retains the requests and the source Inbox owns the derived page images. The original crop and free
reading remain intact. Only additional, visibly connected writing is appended; the ordinary literal guard checks the
arrangement again. No region, template, question or grade is changed here.
"""
import copy
import io
import json

from PIL import Image

from . import direct_read, storage
from .cloud import RetryLater
from .models import plain_transcript
from .providers.base import ProviderError

RULE = "reading-page-v1"
SETTING = "reading_page_recovery"
PROMPT = "reconcile_context"
LIMIT = 25 * 1024**2


def enabled(settings):
    return settings.get("reading_page_rule") == RULE and direct_read.keeps_literal(settings)


def requests(value, count):
    rows = value.get("page_requests", [])
    if not isinstance(rows, list) or len(rows) > count:
        raise ValueError("Invalid page-context request.")
    seen, result = set(), []
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("crop")) is not int or not 0 <= row["crop"] < count
                or row["crop"] in seen or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise ValueError("A page-context request must name one existing answer area and its missing context.")
        seen.add(row["crop"])
        result.append({"crop": row["crop"], "reason": row["reason"][:1000]})
    return result


def recover(service, task, owner, settings, reader, reading, crops, payload, result, value, accept):
    """Return (provider reply, verified reading, candidate reading). A failed round flags only this response."""
    if not enabled(settings):
        return result, value, reading
    record = {"rule": RULE, "status": "unresolved", "requests": [], "added_readings": []}
    try:
        wanted = requests(value, len(crops["crops"]))
        if not wanted:
            return result, value, reading
        record["requests"] = wanted
        source = json.loads(task["source_json"])
        working = copy.deepcopy(crops["crops"])
        candidate = copy.deepcopy(reading)
        direct = next(c for c in candidate["candidates"] if c["provider"] == direct_read.PROVIDER)
        total = 0
        for request in wanted:
            index = request["crop"]
            original, region = crops["crops"][index], source["regions"][index]
            if original["metadata"].get("privacy_masks"):
                raise ValueError("A masked answer area needs your review before its whole page can be used.")
            views = service._page_views({"regions": [region], "crop_meta": [original["metadata"]]})
            if not views:
                raise ValueError("The source page could not be read and verified.")
            picture = views[0]
            total += len(picture)
            if total > LIMIT // 2:
                raise ValueError("The requested pages exceed the reading image limit.")
            reply = service.cloud_call({**owner, "step": f"reconciling:page:{index}"}, "reader", reader,
                {"prompt": "read_context", "payload": {"reason": request["reason"], "crop": index}, "images": [picture]}, settings)
            extra = reply["output"].get("text", "")
            if not isinstance(extra, str) or not extra.strip():
                raise ValueError("The page reader returned no literal reading.")
            extra = "" if extra.strip() == "[no additional work]" else extra.strip()
            record["added_readings"].append({"crop": index, "text": extra})
            if extra:
                added = plain_transcript(extra)
                for line in added["lines"]:
                    line["id"] = f"page-{index}-" + line["id"]
                    for el in line["elements"]:
                        el["crop"] = index
                direct["transcript"]["lines"].extend(added["lines"])
                for key in ("plain_text", "latex"):
                    direct["transcript"][key] += "\n" + extra
            with Image.open(io.BytesIO(picture)) as page:
                page_size = list(page.size)
            working[index] = storage.retain(service.inbox, region["message_id"], task["id"], f"context-{index}",
                {"images": {kind: picture for kind in original["images"]},
                 "metadata": {**original["metadata"], "context_page": True, "context_rule": RULE, "crop_size": page_size,
                              "reason": original["metadata"].get("reason", ""), "original_images": original["images"]}})
        images = [storage.read(service.inbox, crop["images"]["cloud"]) for crop in working]
        enhanced = [storage.read(service.inbox, crop["images"]["contrast"]) for crop in working]
        if sum(map(len, images + enhanced)) > LIMIT:
            raise ValueError("The requested pages exceed the reading image limit.")
        reply = service.cloud_call({**owner, "step": "reconciling:page-arrange"}, "reader", reader,
            {"prompt": PROMPT, "payload": {**payload, "candidates": candidate["candidates"], "page_round": True,
                "context_crops": [r["crop"] for r in wanted]}, "images": images + enhanced}, settings)
        arranged = accept(reply)
        if requests(arranged, len(working)):
            arranged["transcript"]["ambiguities"].append("The whole-page reading still needs more context; inspect the original paper.")
        record.update(status="recovered", crops=working)
        arranged["context_recovery"] = record
        return reply, arranged, candidate
    except RetryLater:
        raise
    except (ProviderError, ValueError, TypeError, KeyError, OSError, StopIteration):
        # The cloud receipt retains provider details. Do not put untrusted writing into an operational error.
        record["error"] = "The additional page context could not be recovered; inspect the original paper."
        value["transcript"]["ambiguities"].append(record["error"])
        value["context_recovery"] = record
        return result, value, reading


def crops_for(crops, verified):
    recovery = verified.get("context_recovery") or {}
    return {**crops, "crops": recovery["crops"]} if recovery.get("status") == "recovered" else crops
