"""Explicit installed-model smoke with a generated blank exam; never offline CI."""
import io
import json
import os
import time

from PIL import Image, ImageDraw, ImageFont

from grading import config, locations
from grading.providers.glm_ocr import GlmOcrProvider
from grading.providers.local_vlm import LocalVlmProvider


def main():
    os.environ.setdefault("FACULTY_GRADING_VERIFIER_URL", "http://127.0.0.1:11435/v1")
    settings = config.validate({"local_verifier_backend": "ollama", "local_verifier_model": "qwen3-vl:8b-instruct", "timeout": 120})
    page = Image.new("RGB", (1224, 1584), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 38)
    draw.text((85, 50), "Synthetic blank exam - show your work", font=title, fill="black")
    draw.text((85, 180), "18. (4 points) Solve 2x + 6 = 14.", font=font, fill="black")
    draw.text((85, 900), "19. (2 points) Find the slope through (1, 2) and (3, 6).", font=font, fill="black")
    output = io.BytesIO(); page.save(output, "PNG")
    started = time.monotonic()
    ocr = GlmOcrProvider().recognize(output.getvalue())
    print(json.dumps({"stage": "ocr", "text": ocr["transcript"]["plain_text"], "seconds": round(time.monotonic()-started, 2)}), flush=True)
    assert not ocr["raw"].get("truncated")
    result = LocalVlmProvider(settings).locate({"ocr_text": ocr["transcript"]["plain_text"]}, [output.getvalue()])
    print(json.dumps({"stage": "layout", "output": result["output"]}), flush=True)
    reading = locations.page_result(result["output"], ocr["transcript"]["plain_text"])
    for question, maximum in (("18", 4), ("19", 2)):
        checked = locations.combine({"issues": [], "printed_number": question, "rubric_maximum": maximum}, [(1, reading)])
        print(json.dumps({"stage": "check", "question": question, "result": checked, "seconds": round(time.monotonic()-started, 2)}), flush=True)
        assert checked["points_match"] and not checked["needs_manual_review"]
        assert checked["confirmation_required"]
    mismatch = locations.combine({"issues": [], "printed_number": "18", "rubric_maximum": 6}, [(1, reading)])
    assert mismatch["needs_manual_review"] and not mismatch["points_match"]
    print("Installed local OCR and layout smoke passed: two printed problem totals matched, wrong rubric total required manual review, every location still requires confirmation.", flush=True)


if __name__ == "__main__":
    main()
