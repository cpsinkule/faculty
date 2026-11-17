"""Reading evidence for graders, derived from the existing response's retained steps.

No new owner or model decision: this accompanies the fixed visual transcript and
never includes another grader's selections, interpretation, score or comments.
"""
from . import consensus
from .review_rules import reading_resolution


def for_graders(reading, verified, crops, settings):
    from . import direct_read
    # A grader is shown readings made INDEPENDENTLY of the transcript in front of it. With direct reading the only candidate
    # is the reader's own first pass at the same image, so there is no second opinion to show and none is implied.
    candidates = direct_read.independent(reading["candidates"])
    visual = {"provider": "visual_reader", "transcript": verified["transcript"]}
    # `independent_readings` says outright how many readings stand beside the transcript. With direct reading it is 0, and every list
    # below is empty because nothing was compared, not because a comparison came out clean (the prompts say so).
    return {**({"page_context": "Some answer crops are whole-page views with the original area outlined in red. Use only that answer and its visibly connected continuation; other questions, printed text and names are not evidence for this response."}
               if verified.get("context_recovery", {}).get("status") == "recovered" else {}),
            "independent_readings": len(candidates),
            "ocr_readings": [{"provider": c["provider"], "plain_text": c["transcript"]["plain_text"],
                              "latex": c["transcript"]["latex"]} for c in candidates],
            "ocr_disagreements": reading["consensus"]["disagreements"],
            "visual_vs_ocr": consensus.align([visual, *candidates])["disagreements"],
            "resolution": reading_resolution(reading, verified, crops, settings),
            "visual_confidence": verified.get("visual_confidence"),
            "instructor_corrected": bool(verified.get("human_corrected"))}
