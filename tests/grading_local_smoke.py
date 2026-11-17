"""Explicit local-only synthetic provider smoke; never part of offline CI."""
import argparse
import io
import json
import os
import time

from PIL import Image, ImageDraw, ImageFont

from grading import config, consensus, rubrics
from grading.models import plain_transcript, transcript
from grading.providers.local_vlm import LocalVlmProvider
from grading.providers.base import ProviderError
from grading.recognition import validate_grade


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:11435/v1")
    parser.add_argument("--model", default="qwen3-vl:8b-instruct")
    parser.add_argument("--with-glm", action="store_true", help="Read the synthetic page with the installed isolated GLM runtime first.")
    parser.add_argument("--with-compiler", action="store_true", help="Also interpret a synthetic follow-through rubric without approving it.")
    args = parser.parse_args()
    os.environ["FACULTY_GRADING_VERIFIER_URL"] = args.url
    os.environ["FACULTY_GRADING_GRADER_URL"] = args.url
    config.endpoint("local_verifier")  # Refuse remote hosts even for this smoke.
    settings = config.validate({"local_verifier_backend":"ollama", "local_grader_backend":"ollama",
        "local_verifier_model":args.model, "local_grader_model":args.model, "timeout":600})
    image = Image.new("RGB", (600,200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    for y, text in ((40,"3x - 8 = 10"), (100,"x = 8")):
        draw.text((30,y), text, font=font, fill="black")
    stream = io.BytesIO(); image.save(stream,"PNG")
    pixels = stream.getvalue()
    expected = plain_transcript("3x - 8 = 10\nx = 8")
    started = time.monotonic()
    candidate = {"provider":"synthetic", "transcript":expected}
    if args.with_glm:
        from grading.providers.glm_ocr import GlmOcrProvider
        candidate = GlmOcrProvider().recognize(pixels)
        print(json.dumps({"phase":"ocr", "reading":candidate["transcript"]["plain_text"], "seconds":round(time.monotonic()-started)}), flush=True)
    answer = LocalVlmProvider(settings).verify({"response_id":"synthetic",
        "candidates":[candidate], "disagreements":[], "image_order":"Original crop, followed by its enhanced copy; both show the same work."}, [pixels,pixels])
    try:
        reading = transcript(answer["output"]["transcript"])
    except ValueError:
        print(json.dumps({"invalid_synthetic_reading":answer["output"]}), flush=True)
        raise
    if consensus.tokens(reading["plain_text"]) != consensus.tokens(expected["plain_text"]):
        raise ValueError("Synthetic reading changed visible mathematics.")
    if any(element["crop"] != 0 for line in reading["lines"] for element in line["elements"]):
        raise ValueError("Synthetic reading referenced an unavailable original crop.")
    print(json.dumps({"phase":"verified", "reading":reading["plain_text"], "lines":len(reading["lines"]), "usage":answer["metadata"].get("usage"), "seconds":round(time.monotonic()-started)}),flush=True)
    rules = rubrics.parse("-1 Incorrect final value", 10)
    result = LocalVlmProvider(settings,"local_grader").grade({"response_id":"synthetic", "transcript":reading,
        "rubric":rules, "problem":"Solve 3x - 8 = 10", "solution":{"expected_answer":"x = 6"}, "calibration_examples":[]}, [pixels])
    try:
        graded = validate_grade(result["output"], reading, rules, 10, 1)
    except ValueError:
        print(json.dumps({"invalid_synthetic_grade":result["output"]}), flush=True)
        raise
    if not graded["score"] or graded["score"]["score"] != 9:
        raise ValueError("Synthetic grading did not apply the sole incorrect-answer deduction.")
    print(json.dumps({"phase":"graded", "score":graded["score"], "review_reasons":graded["review_reasons"],
        "model":args.model, "seconds":round(time.monotonic()-started)}),flush=True)
    if args.with_compiler:
        source = "-1 Arithmetic error. Do not penalize subsequent work that is correct based on that error."
        result = LocalVlmProvider(settings,"local_grader").compile(source, 10)
        rules = rubrics.validate(result["output"], 10)
        if len(rules["items"]) != 1 or rules["items"][0]["points"] != -1 or not rules["items"][0]["follow_through"] or rules["items"][0]["repeat_penalty_for_same_originating_error"]:
            raise ValueError("Synthetic rubric compilation did not preserve the follow-through rule.")
        print(json.dumps({"phase":"compiled", "items":len(rules["items"]), "follow_through":True, "seconds":round(time.monotonic()-started)}),flush=True)


if __name__ == "__main__":
    try:
        main()
    except ProviderError as error:
        if error.raw:
            print(json.dumps({"invalid_synthetic_provider_output":error.raw.get("message", {}).get("content"), "done_reason":error.raw.get("done_reason")}), flush=True)
        raise
